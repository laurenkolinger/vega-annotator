"""The home screen: project cards, the project window, and the annotator's lifecycle.

This process plays the part of the VICARIUS launcher and VEGA's main window. It stays
light, because PyTorch loads only in the annotator process it starts.
"""

import argparse
import atexit
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request, send_from_directory

from . import VERSION, catalog, device, paths, projects, sam3_files, settings, shims, transfer
from .annotator_boot import THREADS_ENV, TRACKER_DTYPE_ENV, is_local_request
from .atomic import read_json_object
from .pathmap import SEGMENTATIONS_REL, STEP4_DIR

UNSAVED_MESSAGE = "The annotator could not save its work, so it is still running. Try again in a moment."
CLOSE_ANNOTATOR_FIRST = "Stop the annotator on this project first, so it saves its work and lets go of the project."
CLOSE_BEFORE_REMOVE = "Close the annotator first, then remove the project."
NOT_A_FOLDER_PROJECT = ("{run} came from VEGA, so its labels and images stay as VEGA made them. "
                        "Only a project made here with NEW PROJECT takes this action.")
STILL_MAKING = "VEGA Annotator is still bringing this project's images in. Wait until the import ends."
INSIDE_APP_IMAGES = ("That folder is inside VEGA Annotator's own folder, which holds its projects. "
                     "Choose a folder of images from elsewhere.")
HOLDS_APP_IMAGES = ("That folder holds VEGA Annotator's own folder, which holds its projects. "
                    "Choose the folder that holds just your images.")
STOPPED_PARTWAY = ("This project's import stopped partway. Use ADD IMAGES with the same folder to bring in "
                   "the rest, or REMOVE the project.")
NEW_PROJECT_KIND = "new_project"
OPENABLE_STATES = ("here", "changed", "made_here")
HOLDING_ONLY_STATES = ("not_on_server",)
DEFAULT_PORT = 5050
FIRST_ANNOTATOR_PORT = 5080
LAST_ANNOTATOR_PORT = 5089
FLUSH_TIMEOUT_S = 15
FLUSH_ATTEMPTS = 3
STOP_TIMEOUT_S = 10
READY_POLL_TIMEOUT_S = 2
LOG_TAIL_LINES = 30
SELF_TEST_TIMEOUT_S = 900
CHOOSER_TIMEOUT_S = 600
BATCH_CHOICES = ("5", "10", "20", "all")
JSON_POST_ONLY = ("POST",)
TUTORIAL_FILE_PREFIX = "vega_tour"
SHUTDOWN_DELAY_S = 0.3
SERVER_PROMPT = "Choose VEGA_Annotator in your server folder"
IMAGES_PROMPT = "Choose a folder of images"
DESTINATION_PROMPT = "Choose where to write the folder for VEGA"
FOLDER_PROMPTS = {"images": IMAGES_PROMPT, "destination": DESTINATION_PROMPT}
SPARE_BYTES = 1_000_000_000
SKIPPED_SHOWN = 100
# A saved SAM3 download reason's own advice to click DOWNLOAD SAM3: a whole sentence, or a
# clause such as ", then click DOWNLOAD SAM3".
RETRY_SENTENCE_RE = re.compile(r"(?:^|(?<=[.!?]) )Click DOWNLOAD SAM3[^.]*\.")
RETRY_CLAUSE_RE = re.compile(r",? (?:then|or) click DOWNLOAD SAM3[^.]*(?=\.)")
TRASH_TIMEOUT_S = 60
TRASH_SCRIPT = ('function run(argv) { ObjC.import("Foundation"); '
                'var url = $.NSURL.fileURLWithPath(argv[0]); '
                'return $.NSFileManager.defaultManager.trashItemAtURLResultingItemURLError(url, null, null) '
                '? "ok" : "failed"; }')
# How a running job is named when another asks for its lane before it reports progress.
JOB_WORDS = {"bring_in": "Bring In", "put_back": "Put Back", "sam3": "Copying SAM3", "self_test": "The SAM3 test",
             "sam3_download": "Downloading SAM3", "scan": "Looking through that folder",
             "new_project": "Making a new project", "add_images": "Adding images",
             "export_for_vega": "EXPORT FOR VEGA"}

PACKAGE_DIR = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(PACKAGE_DIR / "templates"), static_folder=None)


class JobBoard:
    """Long copies run in threads, and the page polls their progress here."""

    def __init__(self):
        """Start with no jobs."""
        self._jobs = {}
        self._lock = threading.Lock()

    def start(self, kind, run, work, lane=None):
        """Start a job unless another job is already running in the same lane or on the same project.

        Parameters:
            kind: a short word such as "bring_in".
            run: the run_* name, or "" for a job with no project.
            work: a callable taking a progress callback and returning a dict.
            lane: what the job may not share with another running job; defaults to its
                project, and "" holds SAM3's install, download and test. A job on a
                project also never runs beside another job on that project, whatever
                their lanes.
        Returns:
            dict: the job record.
        Raises:
            transfer.TransferError: when another job is running in the same lane or on
                the same project.
        """
        lane = run if lane is None else lane
        with self._lock:
            for job in self._jobs.values():
                if job["status"] == "running" and (job["lane"] == lane or (run and job["run"] == run)):
                    raise transfer.TransferError(f"{JOB_WORDS.get(job['kind']) or job['message'] or job['kind']} "
                                                 f"is still running.")
            job = {"id": uuid.uuid4().hex[:12], "kind": kind, "run": run, "lane": lane, "status": "running",
                   "done": 0, "total": 0, "message": "", "result": None, "error": "",
                   "started": time.time()}
            self._jobs[job["id"]] = job

        def progress(done, total, message):
            """Record how far the copy has got."""
            job.update(done=done, total=total, message=message)

        def runner():
            """Run the work and record how it ended."""
            try:
                job["result"] = work(progress)
                job["status"] = "done"
            except transfer.TransferError as exc:
                job.update(status="failed", error=str(exc))
            except Exception as exc:  # A job thread must report every failure to the page.
                job.update(status="failed", error=f"{type(exc).__name__}: {exc}")

        threading.Thread(target=runner, daemon=True).start()
        return job

    def get(self, job_id):
        """Return one job record, or None."""
        return self._jobs.get(job_id)

    def running(self):
        """Return the jobs still running."""
        return [job for job in self._jobs.values() if job["status"] == "running"]

    def busy_with(self, run):
        """Return the running job on one project, or None."""
        for job in self.running():
            if job["run"] == run:
                return job
        return None


class AnnotatorManager:
    """Start and stop the one annotator process, as VEGA's main window does."""

    def __init__(self):
        """Start with nothing running."""
        self.process = None
        self.run = ""
        self.port = 0
        self._log_handle = None
        self._lock = threading.RLock()

    def _log_path(self):
        """Return the annotator's log file."""
        return paths.logs_dir() / "annotator.log"

    def _free_port(self):
        """Return the first free port in VEGA's annotator range."""
        for port in range(FIRST_ANNOTATOR_PORT, LAST_ANNOTATOR_PORT + 1):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                if probe.connect_ex(("127.0.0.1", port)) != 0:
                    return port
        raise transfer.TransferError(f"Ports {FIRST_ANNOTATOR_PORT} to {LAST_ANNOTATOR_PORT} are all in use.")

    def start(self, run, environment):
        """Stop any running annotator and start one on this project.

        Parameters:
            run: the run_* name.
            environment: the settings from projects.annotator_environment.
        Returns:
            int: the port.
        Raises:
            transfer.TransferError: when the running annotator could not save its work.
        """
        with self._lock:
            if not self.stop():
                raise transfer.TransferError(UNSAVED_MESSAGE)
            port = self._free_port()
            paths.logs_dir().mkdir(parents=True, exist_ok=True)
            self._log_handle = open(self._log_path(), "w", encoding="utf-8", errors="replace")
            self.process = subprocess.Popen(
                [sys.executable, "-m", "vega_annotator.annotator_boot", "--port", str(port)],
                cwd=str(paths.app_root()), env={**os.environ, **environment},
                stdout=self._log_handle, stderr=subprocess.STDOUT)
            self.run, self.port = run, port
            return port

    def _get_json(self, route):
        """GET one annotator route, returning its JSON or None."""
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{route}",
                                        timeout=READY_POLL_TIMEOUT_S) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError):
            return None

    def status(self):
        """Report the annotator's state.

        Returns:
            dict: state ("stopped", "loading", "ready" or "failed"), run, url, and
            log (the last log lines, when it failed).
        """
        with self._lock:
            if self.process is None:
                return {"state": "stopped", "run": "", "url": ""}
            url = f"http://127.0.0.1:{self.port}/"
            if self.process.poll() is not None:
                return {"state": "failed", "run": self.run, "url": "", "log": self.log_tail()}
            answer = self._get_json("/api/status")
            return {"state": "ready" if answer else "loading", "run": self.run, "url": url}

    def log_tail(self):
        """Return the last lines of the annotator's log."""
        try:
            text = self._log_path().read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(text.splitlines()[-LOG_TAIL_LINES:])

    def flush(self):
        """Ask the annotator to save its work now, allowing for a save already under way.

        Returns:
            bool: True when the save was confirmed, nothing was running, or the annotator
            is not listening yet, as while SAM3 loads, so it holds no work to save.
        """
        if self.process is None or self.process.poll() is not None:
            return True
        for _attempt in range(FLUSH_ATTEMPTS):
            post = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/flush", data=b"{}", method="POST",
                                          headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(post, timeout=FLUSH_TIMEOUT_S) as response:
                    if response.status == 200:
                        return True
            except (OSError, ValueError) as exc:
                refused = isinstance(getattr(exc, "reason", exc), ConnectionRefusedError)
                if refused or self.process.poll() is not None:
                    return True
        return False

    def stop(self, force=False):
        """Save and stop the annotator. On Windows terminate() skips VEGA's quit handler,
        so the confirmed save here is the one that counts.

        Parameters:
            force: stop even when the save could not be confirmed, for this app's own exit.
        Returns:
            bool: True when the annotator stopped; False when it stays running because
            its save could not be confirmed.
        """
        with self._lock:
            if self.process is None:
                return True
            if not self.flush() and not force:
                return False
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(STOP_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(STOP_TIMEOUT_S)
            if self._log_handle:
                self._log_handle.close()
                self._log_handle = None
            self.process, self.run, self.port = None, "", 0
            return True


jobs = JobBoard()
annotator = AnnotatorManager()
_facts_cache = {}


def machine_facts():
    """Read the machine's facts once; they do not change while the app runs."""
    if "facts" not in _facts_cache:
        _facts_cache["facts"] = device.read_machine_facts()
    return _facts_cache["facts"]


def current_plan():
    """Return the device plan for this computer and the person's choice."""
    return device.plan_for(machine_facts(), settings.load()["device"])


def current_server():
    """Return the server folder's status from the saved choice or a search."""
    return catalog.server_status(settings.load()["server_folder"])


def require_run(run):
    """Answer 400 unless run is a plain run_* folder name.

    Parameters:
        run: the URL part.
    Returns:
        str: the same name.
    """
    if not transfer.valid_run_name(run):
        abort(400, description="That is not a project name.")
    return run


def json_error(message, status=400):
    """Return a JSON error answer the page shows as one line."""
    response = jsonify({"ok": False, "error": message})
    response.status_code = status
    return response


def body():
    """Return the request's JSON object, or {}."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def idle_or_error(run):
    """Answer with an error while a job is running on this project, or None when it is idle."""
    job = jobs.busy_with(run)
    if job is None:
        return None
    if job["kind"] == NEW_PROJECT_KIND:
        return json_error(STILL_MAKING, 409)
    return json_error(f"{JOB_WORDS.get(job['kind']) or job['message'] or 'A copy'} is still running on this project.", 409)


def making_or_error(run, partway_ok=False):
    """Refuse any action on a project NEW PROJECT is still making, or whose import stopped partway.

    Parameters:
        run: the run_* name, already checked.
        partway_ok: let a project whose import stopped partway through, for ADD IMAGES and
            REMOVE, the two actions that end that state.
    Returns:
        An error answer, or None when the project is whole.
    """
    from . import folder_projects

    job = jobs.busy_with(run)
    if job is not None and job["kind"] == NEW_PROJECT_KIND:
        return json_error(STILL_MAKING, 409)
    if not partway_ok and folder_projects.creating(paths.projects_dir() / run):
        return json_error(STOPPED_PARTWAY, 409)
    return None


def refusal_for(run, partway_ok=False, idle=True):
    """Return the first reason a project route must wait: the project is being made, its import
    stopped partway, or another job runs on it.

    Parameters:
        run: the run_* name, already checked.
        partway_ok: let a project whose import stopped partway through.
        idle: also refuse while any other job runs on the project.
    Returns:
        An error answer, or None when the route may go on.
    """
    refusal = making_or_error(run, partway_ok)
    if refusal is None and idle:
        refusal = idle_or_error(run)
    return refusal


def annotator_on(run):
    """Return True while the annotator is starting or running on this project."""
    return annotator.run == run and annotator.status()["state"] in ("loading", "ready")


def json_safe(value):
    """Return value with its paths as text, so a job's result reaches the page as JSON.

    Parameters:
        value: a job result: dicts, lists, tuples, paths and plain values.
    Returns:
        The same shape with every Path a str and every tuple a list.
    """
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return value


def existing_folder(value, missing="Choose a folder of images first."):
    """Check a folder path a page sent.

    Parameters:
        value: the path, as the page sent it.
        missing: the sentence for an empty value.
    Returns:
        tuple: (the folder without a trailing slash, "") or ("", the reason it is refused).
    """
    if not isinstance(value, str) or not value.strip():
        return "", missing
    text = value.strip()
    folder = text.rstrip("/\\") or text
    if not os.path.isabs(folder) or not os.path.isdir(folder):
        return "", f"VEGA Annotator cannot find the folder {text}. Choose it again."
    return folder, ""


def inside_app_folder(folder):
    """Return True when folder is VEGA Annotator's own folder or inside it."""
    app_folder = paths.app_data_dir().resolve()
    chosen = Path(folder).resolve()
    return chosen == app_folder or app_folder in chosen.parents


def image_folder_refusal(folder):
    """Say why a folder cannot be a folder of images: it sits inside VEGA Annotator's own
    folder, or holds it, so its pictures would be the app's own copies.

    The inside half is the same test as the destination's, inside_app_folder.

    Parameters:
        folder: the folder the person chose.
    Returns:
        str: the sentence, or "" for a folder the app may take images from.
    """
    if inside_app_folder(folder):
        return INSIDE_APP_IMAGES
    if Path(folder).resolve() in paths.app_data_dir().resolve().parents:
        return HOLDS_APP_IMAGES
    return ""


def folder_project(run, for_change=True, partway_ok=False):
    """Find a project made on this computer from a folder of images.

    Parameters:
        run: the run_* name, already checked.
        for_change: also refuse while a job or the annotator is working on the project.
        partway_ok: take a project whose import stopped partway, as ADD IMAGES does.
            Every caller refuses a project NEW PROJECT is still making.
    Returns:
        tuple: (the project folder, None), or (None, an error answer).
    """
    from . import folder_projects

    folder = paths.projects_dir() / run
    making = making_or_error(run, partway_ok)
    if making is not None:
        return None, making
    if not (folder / "project.json").is_file():
        return None, json_error(f"{run} is not on this computer.", 404)
    try:
        record = projects.read_project(folder)
    except ValueError as exc:
        return None, json_error(str(exc), 409)
    if not folder_projects.is_folder_project(record):
        return None, json_error(NOT_A_FOLDER_PROJECT.format(run=run), 409)
    if for_change:
        busy = idle_or_error(run)
        if busy:
            return None, busy
        if annotator_on(run):
            return None, json_error(CLOSE_ANNOTATOR_FIRST, 409)
    return folder, None


def made_here(folder):
    """Return True when this computer made the project, so it holds the only copy."""
    from . import folder_projects

    return folder_projects.made_here(folder) is not None


def label_use(folder):
    """Count the masks that carry each label, whatever their status.

    The Labels panel asks every few seconds, so the store is read through projects' cache,
    which parses it again only when its path, size or modification time changes.

    Parameters:
        folder: the project folder.
    Returns:
        dict: {label: masks}; {} when the store is absent or unreadable.
    """
    try:
        store = projects._read_store(folder)
    except ValueError:
        return {}
    counts = {}
    for frame in store.values():
        for mask in (frame.get("masks") or []) if isinstance(frame, dict) else []:
            label = str(mask.get("species") or "").strip() if isinstance(mask, dict) else ""
            if label:
                counts[label] = counts.get(label, 0) + 1
    return counts


def originals_size(folder):
    """Return the bytes of the original images a folder project keeps beside its working copies."""
    total = 0
    for root, _dirs, files in os.walk(Path(folder) / STEP4_DIR / "custom_imports" / "originals"):
        for name in files:
            try:
                info = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            if not os.path.islink(os.path.join(root, name)):
                total += info.st_size
    return total


def shown_path(path, root):
    """Return a path relative to the folder the person chose, when it lies inside it.

    The chosen folder itself, which a scan skips when it is a VEGA project or package,
    shows as its whole path.
    """
    try:
        relative = Path(path).relative_to(root).as_posix()
    except (TypeError, ValueError):
        return str(path)
    return relative if relative != "." else str(path)


def import_report(result, folder):
    """Shape what NEW PROJECT or ADD IMAGES did for the page, with the files it skipped listed.

    Parameters:
        result: create's or add_images's answer: added, skipped ([{"path", "reason"}]) and,
            for NEW PROJECT, run.
        folder: the image folder the person chose.
    Returns:
        dict: the answer as JSON, with skipped cut to the first SKIPPED_SHOWN files, each
        path shown inside the chosen folder, and skipped_count counting them all.
    """
    skipped = [item for item in result.get("skipped") or [] if isinstance(item, dict)]
    report = json_safe({key: value for key, value in result.items() if key != "skipped"})
    report["skipped"] = [{"path": shown_path(item.get("path"), folder), "reason": str(item.get("reason") or "")}
                         for item in skipped[:SKIPPED_SHOWN]]
    report["skipped_count"] = len(skipped)
    return report


def move_to_trash(folder, platform=None, runner=subprocess.run):
    """Move a project this computer made to the Trash, where the person can still get it back.

    Parameters:
        folder: the project folder.
        platform: a sys.platform value; defaults to the running system.
        runner: subprocess.run, passed in so tests can fake it.
    Returns:
        None.
    Raises:
        transfer.TransferError: when macOS did not move it, so it stays where it was.
    """
    platform = platform or sys.platform
    folder = Path(folder)
    if platform != "darwin":
        shutil.rmtree(folder)
        return
    refusal = f"macOS could not move {folder.name} to the Trash, so it stays on this computer."
    try:
        result = runner(["osascript", "-l", "JavaScript", "-e", TRASH_SCRIPT, str(folder)],
                        capture_output=True, text=True, timeout=TRASH_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise transfer.TransferError(refusal) from exc
    if result.returncode != 0 or result.stdout.strip() != "ok" or folder.exists():
        raise transfer.TransferError(refusal)


def download_failure():
    """Return why the last SAM3 download stopped, from its marker, or "" when none stopped.

    The saved reason ends with its own advice to click DOWNLOAD SAM3. The home screen
    follows the reason with one such line of its own, so that advice is taken out here,
    leaving the reason and any other advice once.
    """
    try:
        from . import sam3_download
        marker = sam3_download.failed_marker()
    except ImportError:
        return ""
    try:
        record = read_json_object(marker)
    except ValueError:
        return ""
    for key in ("reason", "error", "message"):
        if isinstance(record.get(key), str) and record[key].strip():
            reason = RETRY_CLAUSE_RE.sub("", record[key].strip())
            return " ".join(RETRY_SENTENCE_RE.sub(" ", reason).split())
    return ""


def refusal_to_open(run):
    """Say why this computer may not work on a project now, or return "".

    Parameters:
        run: the run_* name.
    Returns:
        str: the reason, in a sentence. Only a computer that holds a project may work on it.
    """
    server = current_server()
    card = catalog.project_card(run, server["path"], server["connected"], settings.computer_name())
    if card is None or card["where"] == "server":
        return f"{run} is not on this computer. Bring it in first."
    if card["state"] in OPENABLE_STATES or (card["state"] in HOLDING_ONLY_STATES and card["holding"]):
        return ""
    if card["state"] == "held_elsewhere" and card.get("holder"):
        return f"{run} is in use on {card['holder'].get('computer')}."
    if card["state"] == "error":
        return card.get("error") or f"{run} cannot be read."
    return f"{run} is not held by this computer. Bring it in first."


def initials_from(data):
    """Validate the initials in a request, falling back to the saved ones.

    Parameters:
        data: the request's JSON object.
    Returns:
        str: the initials, upper case.
    Raises:
        ValueError: when neither form holds valid initials.
    """
    return settings.normalize_initials(data.get("initials") or settings.load()["initials"])


@app.before_request
def refuse_outside_requests():
    """Refuse other hosts and other web pages, and any POST that is not JSON."""
    if not is_local_request(request.host, request.headers.get("Origin")):
        abort(403)
    if request.method in JSON_POST_ONLY and request.mimetype != "application/json":
        abort(415)


@app.errorhandler(400)
def bad_request(error):
    """Answer a bad request in one JSON line."""
    return json_error(str(error.description), 400)


@app.route("/static/<path:name>")
def static_file(name):
    """Serve the app's files, then VEGA's main window files, then VICARIUS's styles.

    VEGA's annotator page asks its launcher for the guided tutorial's files. The
    tutorial walks through mainframe windows a laptop does not have, so those
    requests get an empty answer and the page shows no tutorial button.
    """
    if name.startswith(TUTORIAL_FILE_PREFIX):
        return Response("", mimetype="text/css" if name.endswith(".css") else "text/javascript")
    for folder in (PACKAGE_DIR / "static", paths.vega_static(),
                   paths.app_root() / "vendor" / "vega" / "harness" / "vicarius_ui_os" / "static"):
        if (folder / name).is_file():
            return send_from_directory(folder, name)
    abort(404)


@app.route("/")
def home_page():
    """Show the project cards."""
    return render_template("home.html", version=VERSION)


@app.route("/project/<run>")
def project_page(run):
    """Show one project in VEGA's main window layout."""
    require_run(run)
    folder = paths.projects_dir() / run
    if not (folder / "project.json").is_file():
        abort(404)
    return render_template("project.html", run=run, version=VERSION, made_here=made_here(folder))


@app.route("/loading/<run>")
def loading_page(run):
    """Hold the annotator's window until SAM3 has loaded, then go to the annotator."""
    return render_template("loading.html", run=require_run(run))


@app.route("/api/health")
def api_health():
    """Answer the launcher's check that the home screen is running."""
    return jsonify({"ok": True, "version": VERSION})


def disk_status():
    """Report the disk that holds this computer's projects.

    Returns:
        dict: free_bytes and total_bytes, and growth_per_frame_bytes for the Bring In prompt.
    """
    paths.projects_dir().mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(paths.projects_dir())
    return {"free_bytes": usage.free, "total_bytes": usage.total,
            "growth_per_frame_bytes": transfer.GROWTH_PER_FRAME_BYTES}


def project_cards(server, computer):
    """List the project cards, with no action on a project NEW PROJECT is still making.

    Parameters:
        server: current_server()'s answer.
        computer: this computer's name.
    Returns:
        list: catalog.list_projects's cards.
    """
    cards = catalog.list_projects(server["path"], server["connected"], computer)
    for card in cards:
        job = jobs.busy_with(card["run"])
        if job is not None and job["kind"] == NEW_PROJECT_KIND:
            card["actions"] = []
    return cards


@app.route("/api/state")
def api_state():
    """Return everything the home screen and the project window show."""
    server = current_server()
    computer = settings.computer_name()
    facts = machine_facts()
    return jsonify({
        "ok": True, "version": VERSION, "computer": computer, "settings": settings.load(),
        "plan": current_plan().as_dict(), "ram_gb": round(facts.ram_gb),
        "unsupported": device.unsupported_reason(facts),
        "sam3": {**sam3_files.status(),
                 "on_server": bool(server["connected"] and sam3_files.server_copy(server["path"])),
                 "download_failed": download_failure()},
        "server": server, "projects": project_cards(server, computer),
        "annotator": annotator.status(), "jobs": jobs.running(), "disk": disk_status(),
        "trash": sys.platform == "darwin"})


@app.route("/api/settings", methods=["POST"])
def api_settings():
    """Store changed settings."""
    data = body()
    if "server_folder" in data and isinstance(data["server_folder"], str):
        data["server_folder"] = catalog.resolve_server_folder(data["server_folder"].strip())
    try:
        return jsonify({"ok": True, "settings": settings.save(data)})
    except ValueError as exc:
        return json_error(str(exc))


def native_folder_prompt(platform=None, runner=subprocess.run, prompt=SERVER_PROMPT):
    """Ask the operating system's own folder dialog for a folder.

    Parameters:
        platform: a sys.platform value; defaults to the running system.
        runner: subprocess.run, passed in so tests can fake it.
        prompt: the line the dialog shows above the folders.
    Returns:
        str: the chosen path, or "" when the person cancelled or no dialog exists.
    """
    platform = platform or sys.platform
    if platform == "darwin":
        quoted = prompt.replace("\\", "\\\\").replace('"', '\\"')
        command = ["osascript", "-e", f'POSIX path of (choose folder with prompt "{quoted}")']
    elif platform == "win32":
        quoted = prompt.replace("'", "''")
        script = ("Add-Type -AssemblyName System.Windows.Forms;"
                  "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
                  f"$d.Description='{quoted}';"
                  "if($d.ShowDialog() -eq 'OK'){$d.SelectedPath}")
        command = ["powershell", "-NoProfile", "-STA", "-Command", script]
    else:
        return ""
    try:
        result = runner(command, capture_output=True, text=True, timeout=CHOOSER_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


@app.route("/api/choose-server-folder", methods=["POST"])
def api_choose_server_folder():
    """Open the folder dialog and store the choice."""
    chosen = native_folder_prompt()
    if not chosen:
        return jsonify({"ok": True, "cancelled": True})
    resolved = catalog.resolve_server_folder(chosen.rstrip("/\\"))
    settings.save({"server_folder": resolved})
    return jsonify({"ok": True, "cancelled": False, "server_folder": resolved})


@app.route("/api/sam3/install", methods=["POST"])
def api_sam3_install():
    """Copy SAM3 from the server folder."""
    server = current_server()
    if not server["connected"]:
        return json_error(server["reason"])
    try:
        job = jobs.start("sam3", "", lambda progress: sam3_files.install_from_server(server["path"], progress))
    except transfer.TransferError as exc:
        return json_error(str(exc), 409)
    return jsonify({"ok": True, "job": job["id"]})


@app.route("/api/sam3/download", methods=["POST"])
def api_sam3_download():
    """Download SAM3 from the lab's GitHub release, resuming a download that stopped."""
    reason = device.unsupported_reason(machine_facts())
    if reason:
        return json_error(reason, 409)
    if sam3_files.status()["installed"]:
        return json_error("SAM3 is already installed.", 409)
    from . import sam3_download

    def work(progress):
        """Run the download, turning its refusal into the sentence the page shows."""
        try:
            return json_safe(sam3_download.download(progress=progress))
        except sam3_download.DownloadError as exc:
            raise transfer.TransferError(str(exc)) from exc

    try:
        job = jobs.start("sam3_download", "", work)
    except transfer.TransferError as exc:
        return json_error(str(exc), 409)
    return jsonify({"ok": True, "job": job["id"]})


def scan_summary(folder):
    """Look through a folder of images and say what NEW PROJECT or ADD IMAGES would take.

    Parameters:
        folder: the folder the person chose.
    Returns:
        dict: images (all that come in), convert (those converted first: HEIC, HEIF and WebP
        become JPEG at quality 95, and TIFF, BMP and GIF become PNG), skipped (the first
        SKIPPED_SHOWN, each with path and reason), skipped_count, app_folder (what the
        chosen folder is when it is a VEGA Annotator package or a VEGA project, else ""),
        bytes, needed_bytes (each image kept as its original and a working JPEG),
        free_bytes, and fits (room for that plus SPARE_BYTES).
    Raises:
        transfer.TransferError: with the scan's own sentence when it cannot look.
    """
    from . import folder_projects

    root = Path(folder)
    try:
        found = folder_projects.scan(root)
    except folder_projects.FolderError as exc:
        raise transfer.TransferError(str(exc)) from exc
    convert = {str(path) for path in found.get("convert") or []}
    images = {str(path) for path in found.get("images") or []} | convert
    skipped = list(found.get("skipped") or [])
    size = int(found.get("bytes") or 0)
    free = disk_status()["free_bytes"]
    whole = [str(item.get("reason") or "") for item in skipped if isinstance(item, dict) and Path(item.get("path")) == root]
    return {"folder": str(root), "images": len(images), "convert": len(convert), "app_folder": whole[0] if whole else "",
            "skipped": [{"path": shown_path(item.get("path"), root), "reason": str(item.get("reason") or "")}
                        for item in skipped[:SKIPPED_SHOWN] if isinstance(item, dict)],
            "skipped_count": len(skipped), "bytes": size, "needed_bytes": size * 2, "free_bytes": free,
            "fits": free >= size * 2 + SPARE_BYTES}


@app.route("/api/folder/choose", methods=["POST"])
def api_folder_choose():
    """Open the folder dialog for a folder of images, which a job then looks through, or
    for the destination EXPORT FOR VEGA writes into."""
    choice = body().get("for")
    if not isinstance(choice, str) or choice not in FOLDER_PROMPTS:
        return json_error("Say which folder to choose: images or destination.")
    chosen = native_folder_prompt(prompt=FOLDER_PROMPTS[choice])
    if not chosen:
        return jsonify({"ok": True, "cancelled": True})
    folder, error = existing_folder(chosen)
    if error:
        return json_error(error)
    if choice == "destination":
        if inside_app_folder(folder):
            return json_error("Choose a folder outside VEGA Annotator's own folder, such as a USB drive or Documents.")
        return jsonify({"ok": True, "cancelled": False, "folder": folder, "free_bytes": shutil.disk_usage(folder).free})
    refusal = image_folder_refusal(folder)
    if refusal:
        return json_error(refusal)
    try:
        job = jobs.start("scan", "", lambda _progress: scan_summary(folder), lane=f"scan:{folder}")
    except transfer.TransferError as exc:
        return json_error(str(exc), 409)
    return jsonify({"ok": True, "cancelled": False, "folder": folder, "job": job["id"]})


@app.route("/api/projects/new", methods=["POST"])
def api_new_project():
    """Make a project on this computer from a folder of images, with the person's labels.

    The project's run name is chosen before the job starts, and the job carries it, so every
    route on that project waits while its images come in.
    """
    from . import folder_projects, labels

    data = body()
    folder, error = existing_folder(data.get("folder"))
    if error or image_folder_refusal(folder):
        return json_error(error or image_folder_refusal(folder))
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return json_error("Give the project a name.")
    name = " ".join(name.split())
    if len(name) > folder_projects.MAX_NAME_CHARS:
        return json_error(f"Keep the project name to {folder_projects.MAX_NAME_CHARS} characters or fewer.")
    try:
        names = labels.parse(data.get("labels"))
        initials = initials_from(data)
    except (labels.LabelError, ValueError) as exc:
        return json_error(str(exc))
    purpose = str(data.get("purpose") or "").strip()
    if not purpose:
        return json_error("Purpose is required.")
    try:
        run = folder_projects.new_project_id(name, initials, paths.projects_dir())
    except folder_projects.FolderError as exc:
        return json_error(str(exc))
    settings.save({"initials": initials})

    def work(progress):
        """Make the project, turning a refusal into the sentence the page shows."""
        try:
            return import_report(folder_projects.create(folder, name, names, initials, purpose, run=run,
                                                        progress=progress), folder)
        except (folder_projects.FolderError, labels.LabelError) as exc:
            raise transfer.TransferError(str(exc)) from exc

    try:
        job = jobs.start(NEW_PROJECT_KIND, run, work, lane=NEW_PROJECT_KIND)
    except transfer.TransferError as exc:
        return json_error(str(exc), 409)
    return jsonify({"ok": True, "job": job["id"], "run": run})


def run_self_test(_progress):
    """Run self_test.py as its own process and return its JSON line.

    Returns:
        dict: the test's report, or ok False with the reason.
    """
    plan = current_plan()
    env = {**os.environ, "HF_HOME": str(paths.hf_home()), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
           "TCRMP_SAM3_CACHE_DIR": str(paths.sam3_cache_dir()), "TCRMP_EXEMPLAR_WARMUP": "0",
           "PYTHONUTF8": "1"}
    result = subprocess.run(
        [sys.executable, "-m", "vega_annotator.self_test", plan.tracker, plan.exemplar,
         plan.tracker_dtype, str(plan.threads)],
        cwd=str(paths.app_root()), env=env, capture_output=True, text=True,
        timeout=SELF_TEST_TIMEOUT_S, check=False)
    lines = (result.stdout or "").strip().splitlines()
    try:
        return {**json.loads(lines[-1]), "label": plan.label}
    except (IndexError, ValueError):
        return {"ok": False, "error": (result.stderr or "The test printed no result.")[-600:]}


@app.route("/api/self-test", methods=["POST"])
def api_self_test():
    """Start the SAM3 speed test."""
    if not sam3_files.status()["installed"]:
        return json_error("SAM3 is not installed yet.")
    try:
        job = jobs.start("self_test", "", run_self_test)
    except transfer.TransferError as exc:
        return json_error(str(exc), 409)
    return jsonify({"ok": True, "job": job["id"]})


@app.route("/api/jobs/<job_id>")
def api_job(job_id):
    """Report one job's progress."""
    job = jobs.get(job_id)
    if job is None:
        return json_error("That job is not known.", 404)
    return jsonify({"ok": True, "job": job})


@app.route("/api/projects/<run>/bring-in", methods=["POST"])
def api_bring_in(run):
    """Start copying a project from the server folder to this computer."""
    require_run(run)
    refusal = refusal_for(run)
    if refusal is not None:
        return refusal
    data = body()
    server = current_server()
    if not server["connected"]:
        return json_error(server["reason"])
    if annotator.run == run:
        return json_error("Close the annotator on this project first.", 409)
    try:
        initials = initials_from(data)
    except ValueError as exc:
        return json_error(str(exc))
    settings.save({"initials": initials})
    computer = settings.computer_name()
    take_over = data.get("take_over") is True

    def work(progress):
        """Run the copy."""
        return transfer.bring_in(server["path"], run, initials, computer, take_over=take_over, progress=progress)

    try:
        job = jobs.start("bring_in", run, work)
    except transfer.TransferError as exc:
        return json_error(str(exc), 409)
    return jsonify({"ok": True, "job": job["id"]})


@app.route("/api/projects/<run>/put-back", methods=["POST"])
def api_put_back(run):
    """Save and close the annotator on this project, then send the work to the server folder."""
    require_run(run)
    refusal = refusal_for(run)
    if refusal is not None:
        return refusal
    data = body()
    server = current_server()
    if not server["connected"]:
        return json_error(server["reason"])
    try:
        initials = initials_from(data)
    except ValueError as exc:
        return json_error(str(exc))
    computer = settings.computer_name()
    keep_holding = data.get("keep_holding") is True

    def work(progress):
        """Close the annotator so nothing writes during the copy, then run the copy."""
        if annotator.run == run and not annotator.stop():
            raise transfer.TransferError(UNSAVED_MESSAGE + " Nothing was sent.")
        return transfer.put_back(server["path"], run, initials, computer,
                                 keep_holding=keep_holding, progress=progress)

    try:
        job = jobs.start("put_back", run, work)
    except transfer.TransferError as exc:
        return json_error(str(exc), 409)
    return jsonify({"ok": True, "job": job["id"]})


@app.route("/api/projects/<run>/open", methods=["POST"])
def api_open(run):
    """Log who opened the project and why, as the VICARIUS launcher does."""
    require_run(run)
    data = body()
    folder = paths.projects_dir() / run
    refusal = refusal_for(run)
    if refusal is not None:
        return refusal
    if not (folder / "project.json").is_file():
        return json_error(f"{run} is not on this computer. Bring it in first.", 404)
    refusal = refusal_to_open(run)
    if refusal:
        return json_error(refusal, 409)
    try:
        initials = initials_from(data)
        purpose = str(data.get("purpose") or "").strip()
        if not purpose:
            raise ValueError("Purpose is required.")
        settings.save({"initials": initials})
        projects.stamp_access(folder, initials, "open", purpose)
    except ValueError as exc:
        return json_error(str(exc))
    return jsonify({"ok": True, "url": f"/project/{run}"})


@app.route("/api/projects/<run>/start-annotator", methods=["POST"])
def api_start_annotator(run):
    """Start the annotator on this project, with this computer's SAM3 settings."""
    require_run(run)
    data = body()
    folder = paths.projects_dir() / run
    refusal = refusal_for(run)
    if refusal is not None:
        return refusal
    if not (folder / "project.json").is_file():
        return json_error(f"{run} is not on this computer. Bring it in first.", 404)
    reason = device.unsupported_reason(machine_facts())
    if reason:
        return json_error(reason, 409)
    if not sam3_files.status()["installed"]:
        return json_error("SAM3 is not installed yet. Install it from the home screen.", 409)
    refusal = refusal_to_open(run)
    if refusal:
        return json_error(refusal, 409)
    batch = data.get("batch_size")
    if batch is not None and str(batch) not in BATCH_CHOICES:
        return json_error("Review batch size must be 5, 10, 20 or all.")
    plan = current_plan()
    home_url = request.host_url.rstrip("/")
    try:
        environment = projects.annotator_environment(
            folder, plan, home_url,
            {"hf_home": paths.hf_home(), "cache_dir": paths.sam3_cache_dir()}, batch,
            target_only=data.get("target_only") is not False, reference=data.get("reference") is not False)
    except ValueError as exc:
        return json_error(str(exc))
    environment[TRACKER_DTYPE_ENV] = plan.tracker_dtype
    environment[THREADS_ENV] = str(plan.threads)
    if annotator.run == run and annotator.status()["state"] in ("loading", "ready"):
        return jsonify({"ok": True, "already_running": True})
    try:
        annotator.start(run, environment)
    except (transfer.TransferError, OSError) as exc:
        return json_error(str(exc), 409)
    return jsonify({"ok": True, "already_running": False})


@app.route("/api/annotator")
def api_annotator():
    """Report the annotator's state for the loading window and the project window."""
    return jsonify({"ok": True, **annotator.status()})


@app.route("/api/annotator/stop", methods=["POST"])
def api_annotator_stop():
    """Save and close the annotator."""
    return jsonify({"ok": True}) if annotator.stop() else json_error(UNSAVED_MESSAGE, 409)


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """Save and close the annotator, then stop this server. An upgrade calls this first."""
    annotator.stop(force=True)
    threading.Timer(SHUTDOWN_DELAY_S, os._exit, args=(0,)).start()
    return jsonify({"ok": True})


@app.route("/api/projects/<run>/description", methods=["POST"])
def api_description(run):
    """Store a project's description, on this computer's copy."""
    require_run(run)
    folder = paths.projects_dir() / run
    refusal = refusal_for(run)
    if refusal is not None:
        return refusal
    if not (folder / "project.json").is_file():
        return json_error("Bring the project in to edit its description.", 404)
    if refusal_to_open(run):
        return json_error("Bring the project in to edit its description.", 409)
    return jsonify({"ok": True, "description": projects.set_description(folder, body().get("text"))})


@app.route("/api/projects/<run>/remove", methods=["POST"])
def api_remove(run):
    """Delete this computer's copy, which the person asked for, once its work is on the server.
    A project this computer made has no other copy, so it goes to the Trash instead.

    REMOVE waits while any annotator has the project open: the one this app started, and one
    projects.open_in_annotator finds, such as one left from an earlier session. It never stops
    an annotator to clear the way. A project whose import stopped partway can be removed.
    """
    require_run(run)
    busy = idle_or_error(run)
    if busy:
        return busy
    folder = paths.projects_dir() / run
    if annotator_on(run) or projects.open_in_annotator(folder):
        return json_error(CLOSE_BEFORE_REMOVE, 409)
    if annotator.run == run:
        annotator.stop()  # The app's own annotator on this project has stopped by itself; let go of it.
    if folder.is_dir() and made_here(folder):
        try:
            move_to_trash(folder)
        except transfer.TransferError as exc:
            return json_error(str(exc), 409)
        return jsonify({"ok": True, "run": run, "removed": True, "trashed": True})
    server = current_server()
    try:
        return jsonify({"ok": True, **transfer.remove_local(
            run, settings.computer_name(), server["path"] if server["connected"] else None)})
    except transfer.TransferError as exc:
        return json_error(str(exc), 409)


@app.route("/api/projects/<run>/open-folder", methods=["POST"])
def api_open_folder(run):
    """Show this project's Place + Segment folder in Finder or File Explorer."""
    require_run(run)
    refusal = refusal_for(run, idle=False)
    if refusal is not None:
        return refusal
    folder = paths.projects_dir() / run / "step4test_combinedAnnotate"
    opened, error = shims.make_host_open_module().open_in_file_manager(str(folder))
    return jsonify({"ok": True}) if opened else json_error(error)


@app.route("/api/projects/<run>/add-images", methods=["POST"])
def api_add_images(run):
    """Bring more images into a project this computer made, through VEGA's own import code.

    It is also how a new project whose import stopped partway is finished: images already in
    the project are skipped, and the project's creating mark comes off when the import ends.
    """
    require_run(run)
    data = body()
    project, refusal = folder_project(run, partway_ok=True)
    if refusal is not None:
        return refusal
    folder, error = existing_folder(data.get("folder"))
    if error or image_folder_refusal(folder):
        return json_error(error or image_folder_refusal(folder))
    try:
        initials = initials_from(data)
    except ValueError as exc:
        return json_error(str(exc))
    settings.save({"initials": initials})
    from . import folder_projects

    def work(progress):
        """Bring the images in, turning a refusal into the sentence the page shows."""
        try:
            return import_report(folder_projects.add_images(project, folder, initials, progress=progress), folder)
        except folder_projects.FolderError as exc:
            raise transfer.TransferError(str(exc)) from exc

    try:
        job = jobs.start("add_images", run, work)
    except transfer.TransferError as exc:
        return json_error(str(exc), 409)
    return jsonify({"ok": True, "job": job["id"]})


def label_text(data, key):
    """Return one label name from a request, or None when it is missing or not text."""
    value = data.get(key)
    return value if isinstance(value, str) and value.strip() else None


@app.route("/api/projects/<run>/labels", methods=["GET", "POST"])
def api_labels(run):
    """Show a folder project's labels, or add, rename or remove one.

    A label is renamed or removed only before the project's first export, because VEGA
    numbers its classes in order (labels.rename and labels.remove refuse after it).
    """
    require_run(run)
    from . import labels

    if request.method == "GET":
        project, refusal = folder_project(run, for_change=False)
        if refusal is not None:
            return refusal
        record = projects.read_project(project)
        names = (record.get("label_set") or {}).get("labels") or []
        return jsonify({"ok": True, "labels": [str(name) for name in names], "exported": bool(labels.exported(project)),
                        "open": annotator_on(run), "busy": jobs.busy_with(run) is not None,
                        "in_use": label_use(project)})
    data = body()
    project, refusal = folder_project(run)
    if refusal is not None:
        return refusal
    action = data.get("action")
    try:
        if action == "add":
            names = labels.add(project, labels.parse(data.get("names")))
        elif action == "rename":
            old, new = label_text(data, "old"), label_text(data, "new")
            if old is None or new is None:
                return json_error("Say which label to rename and its new name.")
            names = labels.rename(project, old, new)
        elif action == "remove":
            name = label_text(data, "name")
            if name is None:
                return json_error("Say which label to remove.")
            names = labels.remove(project, name)
        else:
            return json_error("Say what to do with the labels: add, rename or remove.")
    except labels.LabelError as exc:
        return json_error(str(exc))
    return jsonify({"ok": True, "labels": list(names)})


@app.route("/api/projects/<run>/export-for-vega", methods=["GET", "POST"])
def api_export_for_vega(run):
    """Say what EXPORT FOR VEGA would carry, or write the folder VEGA trains from."""
    require_run(run)
    from . import labels

    if request.method == "GET":
        project, refusal = folder_project(run, for_change=False)
        if refusal is not None:
            return refusal
        return jsonify({"ok": True, "originals_bytes": originals_size(project),
                        "exported": bool(labels.exported(project))})
    data = body()
    project, refusal = folder_project(run)
    if refusal is not None:
        return refusal
    destination, error = existing_folder(data.get("destination"), "Choose where to write the folder for VEGA first.")
    if error:
        return json_error(error)
    if inside_app_folder(destination):
        return json_error("Choose a folder outside VEGA Annotator's own folder, such as a USB drive or Documents.")
    include = data.get("include_originals", False)
    if not isinstance(include, bool):
        return json_error("Include originals must be ticked or not.")
    if not labels.exported(project):
        return json_error("No frame is exported yet. Export a batch in the annotator first, then EXPORT FOR VEGA.", 409)
    from . import package

    def work(progress):
        """Write the package, turning a refusal into the sentence the page shows."""
        try:
            return json_safe(package.export_for_vega(project, destination, include_originals=include,
                                                     progress=progress))
        except package.PackageError as exc:
            raise transfer.TransferError(str(exc)) from exc

    try:
        job = jobs.start("export_for_vega", run, work)
    except transfer.TransferError as exc:
        return json_error(str(exc), 409)
    return jsonify({"ok": True, "job": job["id"]})


def main(argv=None):
    """Run the home screen.

    Parameters:
        argv: the command line, without the program name.
    Returns:
        int: 0 on a normal stop.
    """
    parser = argparse.ArgumentParser(description="Run the VEGA Annotator home screen.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    shims.install()
    paths.projects_dir().mkdir(parents=True, exist_ok=True)
    atexit.register(annotator.stop, force=True)
    # A stop signal must run the exit handler above, or the annotator outlives this server.
    signal.signal(signal.SIGTERM, lambda _signum, _frame: sys.exit(0))
    print(f"VEGA Annotator {VERSION} on http://127.0.0.1:{args.port}/", flush=True)
    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
