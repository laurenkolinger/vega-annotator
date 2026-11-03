"""The home screen: project cards, the project window, and the annotator's lifecycle.

This process plays the part of the VICARIUS launcher and VEGA's main window. It stays
light, because PyTorch loads only in the annotator process it starts.
"""

import argparse
import atexit
import json
import os
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

UNSAVED_MESSAGE = "The annotator could not save its work, so it is still running. Try again in a moment."
OPENABLE_STATES = ("here", "changed")
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

PACKAGE_DIR = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(PACKAGE_DIR / "templates"), static_folder=None)


class JobBoard:
    """Long copies run in threads, and the page polls their progress here."""

    def __init__(self):
        """Start with no jobs."""
        self._jobs = {}
        self._lock = threading.Lock()

    def start(self, kind, run, work):
        """Start a job unless the same kind of work is already running on that project.

        Parameters:
            kind: a short word such as "bring_in".
            run: the run_* name, or "" for a job with no project.
            work: a callable taking a progress callback and returning a dict.
        Returns:
            dict: the job record.
        Raises:
            transfer.TransferError: when another job is running on the same project.
        """
        with self._lock:
            for job in self._jobs.values():
                if job["status"] == "running" and job["run"] == run:
                    raise transfer.TransferError(f"{job['message'] or job['kind']} is still running.")
            job = {"id": uuid.uuid4().hex[:12], "kind": kind, "run": run, "status": "running",
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
    """Answer with an error while a copy is running on this project, or None when it is idle."""
    job = jobs.busy_with(run)
    if job is None:
        return None
    return json_error(f"{job['message'] or 'A copy'} is still running on this project.", 409)


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
    return render_template("project.html", run=run, version=VERSION)


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
                 "on_server": bool(server["connected"] and sam3_files.server_copy(server["path"]))},
        "server": server, "projects": catalog.list_projects(server["path"], server["connected"], computer),
        "annotator": annotator.status(), "jobs": jobs.running(), "disk": disk_status()})


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


def native_folder_prompt(platform=None, runner=subprocess.run):
    """Ask the operating system's own folder dialog for a folder.

    Parameters:
        platform: a sys.platform value; defaults to the running system.
        runner: subprocess.run, passed in so tests can fake it.
    Returns:
        str: the chosen path, or "" when the person cancelled or no dialog exists.
    """
    platform = platform or sys.platform
    if platform == "darwin":
        command = ["osascript", "-e", 'POSIX path of (choose folder with prompt '
                   '"Choose VEGA_Annotator in your server folder")']
    elif platform == "win32":
        script = ("Add-Type -AssemblyName System.Windows.Forms;"
                  "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
                  "$d.Description='Choose VEGA_Annotator in your server folder';"
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
    if not (folder / "project.json").is_file():
        return json_error(f"{run} is not on this computer. Bring it in first.", 404)
    busy = idle_or_error(run)
    if busy:
        return busy
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
    if not (folder / "project.json").is_file():
        return json_error(f"{run} is not on this computer. Bring it in first.", 404)
    reason = device.unsupported_reason(machine_facts())
    if reason:
        return json_error(reason, 409)
    if not sam3_files.status()["installed"]:
        return json_error("SAM3 is not installed yet. Install it from the home screen.", 409)
    busy = idle_or_error(run)
    if busy:
        return busy
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
    if not (folder / "project.json").is_file():
        return json_error("Bring the project in to edit its description.", 404)
    busy = idle_or_error(run)
    if busy:
        return busy
    if refusal_to_open(run):
        return json_error("Bring the project in to edit its description.", 409)
    return jsonify({"ok": True, "description": projects.set_description(folder, body().get("text"))})


@app.route("/api/projects/<run>/remove", methods=["POST"])
def api_remove(run):
    """Delete this computer's copy, which the person asked for, once its work is on the server."""
    require_run(run)
    busy = idle_or_error(run)
    if busy:
        return busy
    if annotator.run == run and not annotator.stop():
        return json_error(UNSAVED_MESSAGE, 409)
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
    folder = paths.projects_dir() / run / "step4test_combinedAnnotate"
    opened, error = shims.make_host_open_module().open_in_file_manager(str(folder))
    return jsonify({"ok": True}) if opened else json_error(error)


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
