"""Tests for the home screen's routes, through Flask's test client."""

import json
import shutil
import sys
import threading
import types
import urllib.error
import socket
import subprocess
import time
from pathlib import Path

import pytest

import helpers
import vega_annotator
from vega_annotator import home, paths, settings, transfer
from vega_annotator import folder_projects as real_folder_projects

LOCAL = "http://127.0.0.1:5050"
POST_JSON = dict(content_type="application/json", base_url=LOCAL)
FOLDER_RUN = "run_26-11-09_AT_reef_health"
FOLDER_LABELS = ("healthy", "Partly bleached", "bleached")
ORIGINALS_BYTES = 1500


class FakeAnnotator:
    """Stands in for AnnotatorManager, so the routes start no process."""

    def __init__(self):
        self.run = ""
        self.started = []
        self.stopped = 0
        self.state = "stopped"
        self.refuse_stop = False

    def start(self, run, environment):
        self.run, self.state = run, "ready"
        self.started.append((run, environment))
        return 5080

    def stop(self, force=False):
        self.stopped += 1
        if self.refuse_stop and not force:
            return False
        self.run, self.state = "", "stopped"
        return True

    def status(self):
        return {"state": self.state, "run": self.run, "url": "http://127.0.0.1:5080/" if self.run else ""}


@pytest.fixture
def client(monkeypatch):
    fake = FakeAnnotator()
    monkeypatch.setattr(home, "annotator", fake)
    monkeypatch.setattr(home, "jobs", home.JobBoard())
    monkeypatch.setattr(home.catalog, "autodetect_server_folder", lambda: "")
    test_client = home.app.test_client()
    test_client.fake_annotator = fake
    return test_client


def wait_job(client, job_id):
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}", base_url=LOCAL).get_json()["job"]
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("the job did not finish")


def install_fake_sam3():
    snapshot = paths.sam3_model_dir() / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "model.safetensors").write_bytes(b"x" * 10)
    (paths.sam3_model_dir() / "refs").mkdir()
    (paths.sam3_model_dir() / "refs" / "main").write_text("abc", encoding="utf-8")


def test_pages_render(client, published):
    assert b"Existing projects" in client.get("/", base_url=LOCAL).data
    assert client.get("/project/run_missing", base_url=LOCAL).status_code == 404
    assert client.get("/project/nonsense", base_url=LOCAL).status_code == 400
    assert b"Loading SAM3" in client.get("/loading/run_x", base_url=LOCAL).data


def test_health_and_state(client, published):
    folder, run = published
    settings.save({"server_folder": str(folder), "initials": "AT"})
    assert client.get("/api/health", base_url=LOCAL).get_json()["ok"] is True
    state = client.get("/api/state", base_url=LOCAL).get_json()
    assert state["server"]["connected"] is True and state["settings"]["initials"] == "AT"
    assert state["projects"][0]["run"] == run and state["plan"]["tracker"]
    assert state["sam3"]["installed"] is False and state["annotator"]["state"] == "stopped"


def test_outside_requests_are_refused(client):
    assert client.get("/api/state", base_url="http://evil.example").status_code == 403
    assert client.get("/api/state", base_url=LOCAL, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/settings", base_url=LOCAL, data={"initials": "AT"}).status_code == 415


def test_static_files_are_found_in_each_place(client):
    assert client.get("/static/home.js", base_url=LOCAL).status_code == 200
    assert client.get("/static/style.css", base_url=LOCAL).status_code == 200
    assert client.get("/static/theme.css", base_url=LOCAL).status_code == 200
    assert client.get("/static/fonts/Inter-latin-1.woff2", base_url=LOCAL).status_code == 200
    assert client.get("/static/nope.js", base_url=LOCAL).status_code == 404
    assert client.get("/static/../home.py", base_url=LOCAL).status_code == 404


def test_tutorial_files_answer_empty(client):
    for name in ("vega_tour.css", "vega_tour.js", "vega_tour_content.js"):
        response = client.get(f"/static/{name}", base_url=LOCAL)
        assert response.status_code == 200 and response.data == b""


def test_settings_route_validates(client, server_folder):
    answer = client.post("/api/settings", data=json.dumps({"server_folder": str(server_folder.parent), "initials": "at"}), **POST_JSON)
    assert answer.get_json()["settings"] == {"server_folder": str(server_folder), "initials": "AT", "device": "auto"}
    assert client.post("/api/settings", data=json.dumps({"initials": "12345"}), **POST_JSON).status_code == 400
    assert client.post("/api/settings", data=json.dumps({"device": "turbo"}), **POST_JSON).status_code == 400


def test_bring_in_then_open_then_put_back_through_the_routes(client, published):
    folder, run = published
    settings.save({"server_folder": str(folder)})
    answer = client.post(f"/api/projects/{run}/bring-in", data=json.dumps({"initials": "at"}), **POST_JSON).get_json()
    job = wait_job(client, answer["job"])
    assert job["status"] == "done", job
    assert settings.load()["initials"] == "AT"
    assert client.get(f"/project/{run}", base_url=LOCAL).status_code == 200

    answer = client.post(f"/api/projects/{run}/open", data=json.dumps({"initials": "AT", "purpose": "labeling"}), **POST_JSON).get_json()
    assert answer == {"ok": True, "url": f"/project/{run}"}
    record = json.loads((paths.projects_dir() / run / "project.json").read_text(encoding="utf-8"))
    assert record["access_log"][-1] == {**record["access_log"][-1], "initials": "AT", "action": "open", "purpose": "labeling"}

    answer = client.post(f"/api/projects/{run}/put-back", data=json.dumps({}), **POST_JSON).get_json()
    job = wait_job(client, answer["job"])
    assert job["status"] == "done" and job["result"]["conflict"] is False


def test_open_needs_initials_and_purpose(client, published):
    folder, run = published
    settings.save({"server_folder": str(folder)})
    wait_job(client, client.post(f"/api/projects/{run}/bring-in", data=json.dumps({"initials": "AT"}), **POST_JSON).get_json()["job"])
    assert client.post(f"/api/projects/{run}/open", data=json.dumps({"initials": "AT", "purpose": "  "}), **POST_JSON).status_code == 400
    assert client.post(f"/api/projects/{run}/open", data=json.dumps({"initials": "A1", "purpose": "x"}), **POST_JSON).status_code == 400
    assert client.post("/api/projects/run_gone/open", data=json.dumps({"initials": "AT", "purpose": "x"}), **POST_JSON).status_code == 404


def test_bring_in_with_no_server_or_no_initials(client, published):
    _folder, run = published
    assert client.post(f"/api/projects/{run}/bring-in", data=json.dumps({"initials": "AT"}), **POST_JSON).status_code == 400
    settings.save({"server_folder": str(published[0])})
    assert client.post(f"/api/projects/{run}/bring-in", data=json.dumps({}), **POST_JSON).status_code == 400


def test_failed_job_reports_its_reason(client, published):
    folder, run = published
    settings.save({"server_folder": str(folder), "initials": "AT"})
    (folder / "projects" / run / transfer.CHECKSUMS).write_text("{broken", encoding="utf-8")
    job = wait_job(client, client.post(f"/api/projects/{run}/bring-in", data="{}", **POST_JSON).get_json()["job"])
    assert job["status"] == "failed" and "file list" in job["error"]


def test_second_job_on_the_same_project_is_refused(client, published, monkeypatch):
    folder, run = published
    settings.save({"server_folder": str(folder), "initials": "AT"})
    import threading
    gate = threading.Event()
    monkeypatch.setattr(transfer, "bring_in", lambda *args, **kwargs: gate.wait(5) and {})
    first = client.post(f"/api/projects/{run}/bring-in", data="{}", **POST_JSON)
    second = client.post(f"/api/projects/{run}/bring-in", data="{}", **POST_JSON)
    gate.set()
    assert first.status_code == 200 and second.status_code == 409


def test_start_annotator_needs_sam3_and_a_project(client, published):
    folder, run = published
    settings.save({"server_folder": str(folder), "initials": "AT"})
    assert client.post(f"/api/projects/{run}/start-annotator", data="{}", **POST_JSON).status_code == 404
    wait_job(client, client.post(f"/api/projects/{run}/bring-in", data="{}", **POST_JSON).get_json()["job"])
    assert client.post(f"/api/projects/{run}/start-annotator", data="{}", **POST_JSON).status_code == 409
    install_fake_sam3()
    assert client.post(f"/api/projects/{run}/start-annotator", data=json.dumps({"batch_size": "7"}), **POST_JSON).status_code == 400
    answer = client.post(f"/api/projects/{run}/start-annotator", data=json.dumps({"batch_size": "20", "reference": False}), **POST_JSON)
    assert answer.get_json()["already_running"] is False
    started_run, env = client.fake_annotator.started[0]
    assert started_run == run and env["TCRMP_REVIEW_BATCH_SIZE"] == "20" and env["TCRMP_REFERENCE_DEFAULT"] == "0"
    assert env["VEGA_ANNOTATOR_TRACKER_DTYPE"] in ("float32", "bfloat16") and env["TCRMP_ORCHESTRATOR_URL"] == LOCAL
    assert client.post(f"/api/projects/{run}/start-annotator", data="{}", **POST_JSON).get_json()["already_running"] is True


def test_bring_in_refused_while_the_annotator_is_on_it(client, published):
    folder, run = published
    settings.save({"server_folder": str(folder), "initials": "AT"})
    client.fake_annotator.run = run
    assert client.post(f"/api/projects/{run}/bring-in", data="{}", **POST_JSON).status_code == 409


def test_put_back_stops_the_annotator_on_that_project_first(client, published):
    folder, run = published
    settings.save({"server_folder": str(folder), "initials": "AT"})
    wait_job(client, client.post(f"/api/projects/{run}/bring-in", data="{}", **POST_JSON).get_json()["job"])
    client.fake_annotator.run = run
    job = wait_job(client, client.post(f"/api/projects/{run}/put-back", data="{}", **POST_JSON).get_json()["job"])
    assert job["status"] == "done" and client.fake_annotator.stopped == 1


def test_description_and_remove_routes(client, published):
    folder, run = published
    settings.save({"server_folder": str(folder), "initials": "AT"})
    assert client.post(f"/api/projects/{run}/description", data=json.dumps({"text": "x"}), **POST_JSON).status_code == 404
    wait_job(client, client.post(f"/api/projects/{run}/bring-in", data="{}", **POST_JSON).get_json()["job"])
    assert client.post(f"/api/projects/{run}/description", data=json.dumps({"text": " round two "}), **POST_JSON).get_json()["description"] == "round two"
    assert client.post(f"/api/projects/{run}/remove", data="{}", **POST_JSON).status_code == 409
    wait_job(client, client.post(f"/api/projects/{run}/put-back", data="{}", **POST_JSON).get_json()["job"])
    assert client.post(f"/api/projects/{run}/remove", data="{}", **POST_JSON).get_json()["removed"] is True
    assert not (paths.projects_dir() / run).exists()
    assert client.post(f"/api/projects/{run}/remove", data="{}", **POST_JSON).status_code == 409


def test_self_test_route_needs_weights(client):
    assert client.post("/api/self-test", data="{}", **POST_JSON).status_code == 400


def test_unknown_job(client):
    assert client.get("/api/jobs/nope", base_url=LOCAL).status_code == 404


def test_folder_prompt_per_platform():
    class Done:
        returncode, stdout = 0, "/Volumes/home/x/\n"

    calls = []

    def runner(command, **_kwargs):
        calls.append(command)
        return Done()

    assert home.native_folder_prompt("darwin", runner) == "/Volumes/home/x/"
    assert calls[0][0] == "osascript"
    assert home.native_folder_prompt("win32", runner) == "/Volumes/home/x/"
    assert calls[1][0] == "powershell"
    assert home.native_folder_prompt("linux", runner) == ""

    class Cancelled:
        returncode, stdout = 1, ""

    assert home.native_folder_prompt("darwin", lambda *_a, **_k: Cancelled()) == ""

    def boom(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("x", 1)

    assert home.native_folder_prompt("darwin", boom) == ""


def test_shutdown_saves_the_annotator_then_exits(client, monkeypatch):
    exits = []
    monkeypatch.setattr(home.os, "_exit", exits.append)
    monkeypatch.setattr(home, "SHUTDOWN_DELAY_S", 0)
    assert client.post("/api/shutdown", data="{}", **POST_JSON).get_json() == {"ok": True}
    assert client.fake_annotator.stopped == 1
    for _ in range(100):
        if exits:
            break
        time.sleep(0.01)
    assert exits == [0]


def _hold(client, run):
    """Start a job on a project that stays running until the returned gate is set."""
    import threading
    gate = threading.Event()
    home.jobs.start("put_back", run, lambda _progress: gate.wait(5) and {})
    return gate


def test_nothing_else_runs_on_a_project_during_a_copy(client, published):
    folder, run = published
    settings.save({"server_folder": str(folder), "initials": "AT"})
    wait_job(client, client.post(f"/api/projects/{run}/bring-in", data="{}", **POST_JSON).get_json()["job"])
    install_fake_sam3()
    gate = _hold(client, run)
    try:
        for route, payload in (("open", {"initials": "AT", "purpose": "x"}), ("start-annotator", {}),
                               ("description", {"text": "x"}), ("remove", {})):
            answer = client.post(f"/api/projects/{run}/{route}", data=json.dumps(payload), **POST_JSON)
            assert answer.status_code == 409, route
            assert "still running" in answer.get_json()["error"]
    finally:
        gate.set()
    assert (paths.projects_dir() / run).is_dir()


def test_a_project_not_held_here_cannot_be_opened(client, published, tmp_path):
    folder, run = published
    settings.save({"server_folder": str(folder), "initials": "AT"})
    wait_job(client, client.post(f"/api/projects/{run}/bring-in", data="{}", **POST_JSON).get_json()["job"])
    install_fake_sam3()
    wait_job(client, client.post(f"/api/projects/{run}/put-back", data="{}", **POST_JSON).get_json()["job"])
    for route, payload in (("start-annotator", {}), ("open", {"initials": "AT", "purpose": "x"}), ("description", {"text": "x"})):
        assert client.post(f"/api/projects/{run}/{route}", data=json.dumps(payload), **POST_JSON).status_code == 409, route
    transfer.bring_in(str(folder), run, "AT", "Legion", local_root=tmp_path / "legion")
    answer = client.post(f"/api/projects/{run}/start-annotator", data="{}", **POST_JSON)
    assert answer.status_code == 409 and "Legion" in answer.get_json()["error"]


def test_put_back_stops_when_the_annotator_cannot_save(client, published):
    folder, run = published
    settings.save({"server_folder": str(folder), "initials": "AT"})
    wait_job(client, client.post(f"/api/projects/{run}/bring-in", data="{}", **POST_JSON).get_json()["job"])
    before = json.loads((folder / "projects" / run / transfer.SERVER_STATE).read_text(encoding="utf-8"))["rev"]
    client.fake_annotator.run = run
    client.fake_annotator.refuse_stop = True
    job = wait_job(client, client.post(f"/api/projects/{run}/put-back", data="{}", **POST_JSON).get_json()["job"])
    assert job["status"] == "failed" and "Nothing was sent" in job["error"]
    assert json.loads((folder / "projects" / run / transfer.SERVER_STATE).read_text(encoding="utf-8"))["rev"] == before
    assert client.post("/api/annotator/stop", data="{}", **POST_JSON).status_code == 409


def helpers_free_port():
    """Return a local port nothing is listening on."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _SlowProcess:
    """A stand-in process that never exits by itself."""

    def __init__(self):
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, _seconds):
        return 0


def test_stop_works_while_sam3_loads_and_nothing_is_listening_yet():
    manager = home.AnnotatorManager()
    process = _SlowProcess()
    manager.process = process
    manager.port = helpers_free_port()
    assert manager.stop() is True
    assert process.terminated is True and manager.process is None


def test_manager_does_not_kill_an_annotator_whose_save_failed(monkeypatch):
    def save_hangs(*_args, **_kwargs):
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(home.urllib.request, "urlopen", save_hangs)
    manager = home.AnnotatorManager()
    manager.process = _SlowProcess()
    manager.port = 9
    assert manager.stop() is False
    assert manager.process.terminated is False
    assert manager.stop(force=True) is True
    assert manager.process is None


def test_state_reports_the_disk(client):
    disk = client.get("/api/state", base_url=LOCAL).get_json()["disk"]
    assert disk["free_bytes"] > 0 and disk["total_bytes"] >= disk["free_bytes"]
    assert disk["growth_per_frame_bytes"] == transfer.GROWTH_PER_FRAME_BYTES


# ---------------------------------------------------------------------------
# Folder projects, labels, EXPORT FOR VEGA and DOWNLOAD SAM3. The modules these
# routes call (labels, folder_projects, package, sam3_download) are stood in for
# here, so the routes are tested on their own.
# ---------------------------------------------------------------------------


def _list_from(value):
    """Split label text the way labels.parse does: new lines and commas."""
    items = value if isinstance(value, list) else str(value or "").replace(",", "\n").splitlines()
    return [" ".join(str(item).split()) for item in items if str(item).strip()]


def _stored_labels(project_dir):
    """Read a project's label list from its project.json."""
    record = json.loads((Path(project_dir) / "project.json").read_text(encoding="utf-8"))
    return list(record["label_set"]["labels"])


def _store_labels(project_dir, names):
    """Write a project's label list to its project.json and return it."""
    path = Path(project_dir) / "project.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["label_set"]["labels"] = list(names)
    path.write_text(json.dumps(record), encoding="utf-8")
    return list(names)


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    """Install stand-ins for labels, folder_projects, package and sam3_download."""
    calls = []
    gate = {"create": None}

    labels = types.ModuleType("vega_annotator.labels")

    class LabelError(Exception):
        """The stand-in's refusal."""

    def parse(value):
        """Stand in for labels.parse: split, tidy, and refuse a reserved word or nothing."""
        names = _list_from(value)
        for name in names:
            if name.lower() == "review":
                raise LabelError(f"\"{name}\" is a word VEGA keeps for itself. Choose another label.")
        if not names:
            raise LabelError("Type at least one label.")
        return names

    def add(project_dir, names):
        """Stand in for labels.add: put the new labels at the end."""
        calls.append(("add", list(names)))
        return _store_labels(project_dir, _stored_labels(project_dir) + list(names))

    def rename(project_dir, old, new):
        """Stand in for labels.rename, refused after the first export."""
        calls.append(("rename", old, new))
        if labels.state["exported"]:
            raise LabelError("This project has exported frames, so its labels can no longer be renamed.")
        return _store_labels(project_dir, [new if name == old else name for name in _stored_labels(project_dir)])

    def remove(project_dir, name):
        """Stand in for labels.remove."""
        calls.append(("remove", name))
        return _store_labels(project_dir, [item for item in _stored_labels(project_dir) if item != name])

    labels.LabelError, labels.parse, labels.add, labels.rename, labels.remove = LabelError, parse, add, rename, remove
    labels.state = {"exported": False}
    labels.exported = lambda project_dir: labels.state["exported"]

    folder_projects = types.ModuleType("vega_annotator.folder_projects")

    class FolderError(Exception):
        """The stand-in's refusal."""

    def scan(folder):
        """Stand in for folder_projects.scan: three images, one to convert, one skipped."""
        calls.append(("scan", str(folder)))
        folder = Path(folder)
        return {"images": [folder / "a.jpg", folder / "sub" / "b.png"], "convert": [folder / "c.heic"],
                "skipped": [{"path": folder / ".hidden.jpg", "reason": "hidden file"}], "bytes": 3_000_000}

    def new_project_id(name, initials, root, today=None):
        """Stand in for folder_projects.new_project_id: the date, the initials and the name."""
        return f"run_26-11-09_{initials}_" + name.lower().replace(" ", "_")

    def create(folder, name, label_text, initials, purpose, root=None, progress=None, run=None):
        """Stand in for folder_projects.create, refusing a full disk by name."""
        calls.append(("create", str(folder), name, label_text, initials, purpose))
        if gate["create"] is not None:
            gate["create"].wait(5)
        if name == "Full disk":
            raise FolderError("This computer needs 2.0 GB free for these images and has 0.5 GB.")
        if progress:
            progress(1, 3, "Copying images")
        return {"run": run or new_project_id(name, initials, root), "added": 3,
                "skipped": [{"path": Path(folder) / ".hidden.jpg", "reason": "hidden file"}]}

    def add_images(project_dir, folder, initials, progress=None):
        """Stand in for folder_projects.add_images."""
        calls.append(("add_images", Path(project_dir).name, str(folder), initials))
        return {"added": 2, "skipped": []}

    folder_projects.FolderError, folder_projects.scan, folder_projects.create = FolderError, scan, create
    folder_projects.add_images, folder_projects.new_project_id = add_images, new_project_id
    folder_projects.is_folder_project = lambda project: (project.get("label_set") or {}).get("kind") == "custom"
    # The local record is read for real: what a project made here is, and whether its import ended.
    folder_projects.made_here, folder_projects.creating = real_folder_projects.made_here, real_folder_projects.creating
    folder_projects.MAX_NAME_CHARS = real_folder_projects.MAX_NAME_CHARS

    package = types.ModuleType("vega_annotator.package")

    class PackageError(Exception):
        """The stand-in's refusal."""

    def export_for_vega(project_dir, destination, include_originals=False, progress=None, now=None):
        """Stand in for package.export_for_vega, refusing a full drive by name."""
        calls.append(("export", Path(project_dir).name, str(destination), include_originals))
        if Path(destination).name == "full_usb":
            raise PackageError("The drive needs 1.2 GB free and has 0.1 GB, so nothing was written.")
        return {"folder": Path(destination) / "26-11-09_AT_reef_health_20261109-1530", "frames": 4, "masks": 9,
                "bytes": 52_000_000}

    package.PackageError, package.export_for_vega = PackageError, export_for_vega

    sam3_download = types.ModuleType("vega_annotator.sam3_download")

    class DownloadError(Exception):
        """The stand-in's refusal."""

    def download(progress=None, release=None, work_dir=None, opener=None):
        """Stand in for sam3_download.download, failing when told to."""
        calls.append(("download",))
        if sam3_download.fail:
            raise DownloadError("The second part of SAM3 arrived damaged, so it was deleted. Click DOWNLOAD SAM3 again.")
        if progress:
            progress(5, 10, "Downloading SAM3")
        return {"installed": True, "size_gb": 3.4}

    sam3_download.DownloadError, sam3_download.download, sam3_download.fail = DownloadError, download, False
    sam3_download.failed_marker = lambda: paths.app_data_dir() / "sam3_download_failed.json"

    modules = {"labels": labels, "folder_projects": folder_projects, "package": package, "sam3_download": sam3_download}
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, f"vega_annotator.{name}", module)
        monkeypatch.setattr(vega_annotator, name, module, raising=False)
    return types.SimpleNamespace(calls=calls, gate=gate, **modules)


def make_folder_project(run=FOLDER_RUN, labels=FOLDER_LABELS, mask_labels=("bleached", "bleached", "healthy")):
    """Write a small project in the shape NEW PROJECT makes, on this computer."""
    project = paths.projects_dir() / run
    step4 = project / "step4test_combinedAnnotate"
    for folder in (step4 / "routed_input" / "raw", step4 / "routed_input" / "ids", step4 / "segmentations",
                   step4 / "custom_imports" / "originals" / "sub", step4 / "custom_imports" / "raw"):
        folder.mkdir(parents=True, exist_ok=True)
    (step4 / "routed_input" / "ids" / "sam_click_prompts.json").write_text("{}", encoding="utf-8")
    masks = [{"id": index, "species": label, "status": "accepted" if index else "rejected"}
             for index, label in enumerate(mask_labels)]
    store = {"custom_IMG_1.jpg": {"image_path": "custom_imports/raw/custom_IMG_1.jpg", "masks": masks,
                                  "custom_import": True, "reference_points": [], "exported": False}}
    (step4 / "segmentations" / "segmentations.json").write_text(json.dumps(store), encoding="utf-8")
    (step4 / "custom_imports" / "originals" / "IMG_1.HEIC").write_bytes(b"x" * 1000)
    (step4 / "custom_imports" / "originals" / "sub" / "IMG_2.jpg").write_bytes(b"x" * (ORIGINALS_BYTES - 1000))
    record = {"id": run[4:], "name": "Reef health", "description": "", "initials": "AT",
              "created_at": "2026-11-09T09:00:00-04:00", "updated_at": "2026-11-09T09:00:00-04:00",
              "access_log": [{"ts": "2026-11-09T09:00:00-04:00", "initials": "AT", "action": "create",
                              "purpose": "health labels"}],
              "steps": {"1": {"status": "skipped", "name": "Make All Points"},
                        "2": {"status": "skipped", "name": "Recode Species"},
                        "3": {"status": "skipped", "name": "Choose Images",
                              "config": {"target_species": ",".join(labels)}}},
              "label_set": {"kind": "custom", "labels": list(labels)},
              "origin": {"kind": "folder", "folder": "/Users/student/photos", "computer": "Lab MacBook",
                         "made_with": "VEGA Annotator 1.1.0"}}
    (project / "project.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    (project / transfer.LOCAL_STATE).write_text(json.dumps(
        {"kind": "made_here", "created_at": "2026-11-09T09:00:00-04:00", "computer": "Lab MacBook"}), encoding="utf-8")
    return project


def post(client, route, payload=None):
    """POST JSON to a route and return the response."""
    return client.post(route, data=json.dumps(payload if payload is not None else {}), **POST_JSON)


def test_folder_prompt_carries_its_own_words():
    """Each folder dialog shows its own prompt, quoted safely for macOS and Windows."""
    calls = []

    class Done:
        returncode, stdout = 0, "/Users/student/photos/\n"

    def runner(command, **_kwargs):
        calls.append(command)
        return Done()

    assert home.native_folder_prompt("darwin", runner, prompt=home.IMAGES_PROMPT) == "/Users/student/photos/"
    assert home.IMAGES_PROMPT in calls[0][-1]
    home.native_folder_prompt("win32", runner, prompt="Pick 'this' one")
    assert "Pick ''this'' one" in calls[1][-1]
    home.native_folder_prompt("darwin", runner, prompt='Say "hi" \\ there')
    assert 'Say \\"hi\\" \\\\ there' in calls[2][-1]
    home.native_folder_prompt("darwin", runner)
    assert "VEGA_Annotator in your server folder" in calls[3][-1]


def test_folder_choose_uses_images_prompt(client, fakes, monkeypatch, tmp_path):
    """Choosing images asks with the images prompt and looks through the folder in a job."""
    photos = tmp_path / "photos"
    photos.mkdir()
    prompts = []
    monkeypatch.setattr(home, "native_folder_prompt", lambda **kwargs: prompts.append(kwargs["prompt"]) or str(photos) + "/")
    answer = post(client, "/api/folder/choose", {"for": "images"}).get_json()
    assert prompts == [home.IMAGES_PROMPT] and "images" in home.IMAGES_PROMPT
    assert answer["ok"] is True and answer["cancelled"] is False and answer["folder"] == str(photos)
    job = wait_job(client, answer["job"])
    assert job["status"] == "done", job
    summary = job["result"]
    assert summary["images"] == 3 and summary["convert"] == 1 and summary["skipped_count"] == 1
    assert summary["skipped"] == [{"path": ".hidden.jpg", "reason": "hidden file"}]
    assert summary["bytes"] == 3_000_000 and summary["needed_bytes"] == 6_000_000
    assert summary["free_bytes"] > 0 and summary["fits"] is (summary["free_bytes"] >= 6_000_000 + home.SPARE_BYTES)


def test_folder_choose_for_a_destination_reports_free_space(client, fakes, monkeypatch, tmp_path):
    """Choosing a destination answers at once with the free space there."""
    prompts = []
    monkeypatch.setattr(home, "native_folder_prompt", lambda **kwargs: prompts.append(kwargs["prompt"]) or str(tmp_path))
    answer = post(client, "/api/folder/choose", {"for": "destination"}).get_json()
    assert prompts == [home.DESTINATION_PROMPT]
    assert answer["folder"] == str(tmp_path) and answer["free_bytes"] > 0 and "job" not in answer


def test_folder_choose_cancel_and_bad_requests(client, fakes, monkeypatch, tmp_path):
    """A cancelled dialog changes nothing; a bad request or a vanished folder is refused."""
    monkeypatch.setattr(home, "native_folder_prompt", lambda **_kwargs: "")
    assert post(client, "/api/folder/choose", {"for": "images"}).get_json() == {"ok": True, "cancelled": True}
    for payload in ({}, {"for": "server"}, {"for": ["images"]}):
        answer = post(client, "/api/folder/choose", payload)
        assert answer.status_code == 400 and answer.get_json()["ok"] is False
    monkeypatch.setattr(home, "native_folder_prompt", lambda **_kwargs: str(tmp_path / "gone"))
    answer = post(client, "/api/folder/choose", {"for": "images"})
    assert answer.status_code == 400 and "gone" in answer.get_json()["error"]


def test_new_project_starts_a_job_and_saves_the_initials(client, fakes, tmp_path):
    """NEW PROJECT hands tidy fields to folder_projects.create and keeps the initials."""
    photos = tmp_path / "photos"
    photos.mkdir()
    payload = {"folder": str(photos), "name": "  Reef health ", "labels": "healthy, Partly  bleached\nbleached",
               "initials": "at", "purpose": "health labels"}
    answer = post(client, "/api/projects/new", payload).get_json()
    assert answer["ok"] is True
    job = wait_job(client, answer["job"])
    assert job["status"] == "done", job
    assert job["result"]["run"] == "run_26-11-09_AT_reef_health" and job["result"]["added"] == 3
    assert job["result"]["skipped"][0]["path"].endswith(".hidden.jpg")
    assert fakes.calls[-1] == ("create", str(photos), "Reef health", ["healthy", "Partly bleached", "bleached"],
                               "AT", "health labels")
    assert settings.load()["initials"] == "AT"


def test_new_project_refusals_are_plain_sentences(client, fakes, tmp_path):
    """Every NEW PROJECT refusal is a sentence, before a job or from inside it."""
    photos = tmp_path / "photos"
    photos.mkdir()
    good = {"folder": str(photos), "name": "Reef health", "labels": "healthy", "initials": "AT", "purpose": "x"}
    refused = post(client, "/api/projects/new", {**good, "labels": "healthy, review"})
    assert refused.status_code == 400 and refused.get_json()["error"].startswith('"review" is a word VEGA keeps')
    job = wait_job(client, post(client, "/api/projects/new", {**good, "name": "Full disk"}).get_json()["job"])
    assert job["status"] == "failed" and job["error"].startswith("This computer needs 2.0 GB free")
    for change, words in (({"folder": ""}, "Choose a folder"), ({"folder": str(tmp_path / "gone")}, "cannot find"),
                          ({"folder": "photos"}, "cannot find"), ({"folder": 7}, "Choose a folder"),
                          ({"name": "   "}, "name"), ({"name": "x" * 81}, "80"), ({"labels": ""}, "at least one"),
                          ({"initials": "A1"}, "Initials"), ({"purpose": " "}, "Purpose")):
        answer = post(client, "/api/projects/new", {**good, **change})
        assert answer.status_code == 400 and words in answer.get_json()["error"], change
    assert not [call for call in fakes.calls if call[0] == "create" and call[2] != "Full disk"]


def test_one_new_project_at_a_time(client, fakes, tmp_path):
    """A second NEW PROJECT waits, and SAM3's lane stays free."""
    photos = tmp_path / "photos"
    photos.mkdir()
    fakes.gate["create"] = threading.Event()
    good = {"folder": str(photos), "name": "Reef health", "labels": "healthy", "initials": "AT", "purpose": "x"}
    try:
        first = post(client, "/api/projects/new", good)
        second = post(client, "/api/projects/new", good)
        assert first.status_code == 200 and second.status_code == 409
        assert post(client, "/api/self-test", {}).status_code == 400, "a new project must not block the SAM3 lane"
    finally:
        fakes.gate["create"].set()


def test_add_images_route(client, fakes, tmp_path):
    """ADD IMAGES starts a job on the project, and refuses a bad request."""
    project = make_folder_project()
    photos = tmp_path / "more"
    photos.mkdir()
    answer = post(client, f"/api/projects/{FOLDER_RUN}/add-images", {"folder": str(photos), "initials": "lo"}).get_json()
    job = wait_job(client, answer["job"])
    assert job["status"] == "done" and job["result"] == {"added": 2, "skipped": [], "skipped_count": 0}
    assert fakes.calls[-1] == ("add_images", project.name, str(photos), "LO")
    assert post(client, f"/api/projects/{FOLDER_RUN}/add-images", {"folder": ""}).status_code == 400
    assert post(client, f"/api/projects/{FOLDER_RUN}/add-images", {"folder": str(tmp_path / "gone")}).status_code == 400
    assert post(client, "/api/projects/run_missing/add-images", {"folder": str(photos)}).status_code == 404
    assert post(client, "/api/projects/nonsense/add-images", {"folder": str(photos)}).status_code == 400


def test_add_images_refused_on_a_vega_project_and_while_open(client, fakes, published, tmp_path):
    """ADD IMAGES refuses a project from VEGA, and a project open in the annotator."""
    folder, run = published
    settings.save({"server_folder": str(folder), "initials": "AT"})
    wait_job(client, post(client, f"/api/projects/{run}/bring-in").get_json()["job"])
    photos = tmp_path / "more"
    photos.mkdir()
    answer = post(client, f"/api/projects/{run}/add-images", {"folder": str(photos)})
    assert answer.status_code == 409 and "came from VEGA" in answer.get_json()["error"]
    make_folder_project()
    client.fake_annotator.run, client.fake_annotator.state = FOLDER_RUN, "ready"
    answer = post(client, f"/api/projects/{FOLDER_RUN}/add-images", {"folder": str(photos)})
    assert answer.status_code == 409 and "Stop the annotator" in answer.get_json()["error"]
    assert not [call for call in fakes.calls if call[0] == "add_images"]


def test_labels_route_reads_the_list_and_its_use(client, fakes):
    """The labels route lists the labels, the export state and the masks per label."""
    make_folder_project()
    answer = client.get(f"/api/projects/{FOLDER_RUN}/labels", base_url=LOCAL).get_json()
    assert answer["ok"] is True and answer["labels"] == list(FOLDER_LABELS)
    assert answer["exported"] is False and answer["open"] is False and answer["busy"] is False
    assert answer["in_use"] == {"bleached": 2, "healthy": 1}
    fakes.labels.state["exported"] = True
    assert client.get(f"/api/projects/{FOLDER_RUN}/labels", base_url=LOCAL).get_json()["exported"] is True
    assert client.get("/api/projects/run_missing/labels", base_url=LOCAL).status_code == 404


def test_labels_route_adds_renames_and_removes(client, fakes):
    """The labels route adds, renames and removes, and passes refusals on as sentences."""
    project = make_folder_project()
    route = f"/api/projects/{FOLDER_RUN}/labels"
    answer = post(client, route, {"action": "add", "names": "diseased, paled"}).get_json()
    assert answer == {"ok": True, "labels": [*FOLDER_LABELS, "diseased", "paled"]}
    assert post(client, route, {"action": "rename", "old": "paled", "new": "Pale"}).get_json()["labels"][-1] == "Pale"
    assert post(client, route, {"action": "remove", "name": "Pale"}).get_json()["labels"] == [*FOLDER_LABELS, "diseased"]
    assert _stored_labels(project) == [*FOLDER_LABELS, "diseased"]
    refused = post(client, route, {"action": "add", "names": "review"})
    assert refused.status_code == 400 and "keeps for itself" in refused.get_json()["error"]
    fakes.labels.state["exported"] = True
    refused = post(client, route, {"action": "rename", "old": "healthy", "new": "Healthy"})
    assert refused.status_code == 400 and "exported frames" in refused.get_json()["error"]
    for payload in ({}, {"action": "drop"}, {"action": "rename", "old": "healthy"}, {"action": "remove", "name": 3},
                    {"action": "rename", "old": ["healthy"], "new": "x"}):
        assert post(client, route, payload).status_code == 400, payload


def test_labels_route_refuses_while_open(client, fakes):
    """Labels cannot change while the annotator has the project open."""
    make_folder_project()
    client.fake_annotator.run, client.fake_annotator.state = FOLDER_RUN, "loading"
    answer = post(client, f"/api/projects/{FOLDER_RUN}/labels", {"action": "add", "names": "diseased"})
    assert answer.status_code == 409 and "Stop the annotator" in answer.get_json()["error"]
    assert client.get(f"/api/projects/{FOLDER_RUN}/labels", base_url=LOCAL).get_json()["open"] is True
    assert not [call for call in fakes.calls if call[0] == "add"]


def test_labels_route_refuses_while_a_job_runs_on_the_project(client, fakes):
    """Labels cannot change while a job runs on the project."""
    make_folder_project()
    gate = _hold(client, FOLDER_RUN)
    try:
        answer = post(client, f"/api/projects/{FOLDER_RUN}/labels", {"action": "add", "names": "diseased"})
        assert answer.status_code == 409 and "still running" in answer.get_json()["error"]
        assert client.get(f"/api/projects/{FOLDER_RUN}/labels", base_url=LOCAL).get_json()["busy"] is True
    finally:
        gate.set()


def test_export_for_vega_reports_the_originals_size(client, fakes):
    """The export dialog learns the originals' size and whether anything is exported."""
    make_folder_project()
    answer = client.get(f"/api/projects/{FOLDER_RUN}/export-for-vega", base_url=LOCAL).get_json()
    assert answer == {"ok": True, "originals_bytes": ORIGINALS_BYTES, "exported": False}


def test_export_for_vega_route(client, fakes, tmp_path):
    """EXPORT FOR VEGA refuses with nothing exported, runs as a job, and refuses bad requests."""
    make_folder_project()
    route = f"/api/projects/{FOLDER_RUN}/export-for-vega"
    usb = tmp_path / "usb"
    usb.mkdir()
    refused = post(client, route, {"destination": str(usb)})
    assert refused.status_code == 409 and "Export a batch" in refused.get_json()["error"]
    fakes.labels.state["exported"] = True
    job = wait_job(client, post(client, route, {"destination": str(usb), "include_originals": True}).get_json()["job"])
    assert job["status"] == "done", job
    assert job["result"]["folder"] == str(usb / "26-11-09_AT_reef_health_20261109-1530")
    assert job["result"]["frames"] == 4 and fakes.calls[-1] == ("export", FOLDER_RUN, str(usb), True)
    full = tmp_path / "full_usb"
    full.mkdir()
    job = wait_job(client, post(client, route, {"destination": str(full)}).get_json()["job"])
    assert job["status"] == "failed" and job["error"].startswith("The drive needs 1.2 GB free")
    for payload in ({}, {"destination": "usb"}, {"destination": str(tmp_path / "gone")},
                    {"destination": str(usb), "include_originals": "yes"},
                    {"destination": str(paths.projects_dir())}):
        assert post(client, route, payload).status_code == 400, payload
    client.fake_annotator.run, client.fake_annotator.state = FOLDER_RUN, "ready"
    assert post(client, route, {"destination": str(usb)}).status_code == 409


def test_sam3_download_route(client, fakes, monkeypatch):
    """DOWNLOAD SAM3 runs as a job, reports its failure, and refuses once SAM3 is installed."""
    job = wait_job(client, post(client, "/api/sam3/download").get_json()["job"])
    assert job["status"] == "done" and job["result"] == {"installed": True, "size_gb": 3.4}
    fakes.sam3_download.fail = True
    job = wait_job(client, post(client, "/api/sam3/download").get_json()["job"])
    assert job["status"] == "failed" and job["error"].startswith("The second part of SAM3 arrived damaged")
    install_fake_sam3()
    answer = post(client, "/api/sam3/download")
    assert answer.status_code == 409 and "already installed" in answer.get_json()["error"]


def test_sam3_download_shares_the_lane_with_the_server_copy(client, fakes):
    """A download waits while SAM3 copies from the server folder."""
    gate = threading.Event()
    home.jobs.start("sam3", "", lambda _progress: gate.wait(5) and {})
    try:
        answer = post(client, "/api/sam3/download")
        assert answer.status_code == 409 and "still running" in answer.get_json()["error"]
    finally:
        gate.set()


def test_sam3_download_refused_where_sam3_cannot_run(client, fakes, monkeypatch):
    """A computer that cannot run SAM3 is not offered a 3.4 GB download."""
    monkeypatch.setattr(home.device, "unsupported_reason", lambda _facts: "This Mac has an Intel chip, and SAM3 needs an Apple chip.")
    answer = post(client, "/api/sam3/download")
    assert answer.status_code == 409 and "Intel chip" in answer.get_json()["error"]


def test_state_says_why_the_last_download_stopped(client, fakes):
    """The state carries the reason setup's SAM3 download stopped, and whether there is a Trash."""
    assert client.get("/api/state", base_url=LOCAL).get_json()["sam3"]["download_failed"] == ""
    marker = paths.app_data_dir() / "sam3_download_failed.json"
    marker.write_text(json.dumps({"reason": "The disk has 2.1 GB free, and SAM3 needs 7.5 GB."}), encoding="utf-8")
    state = client.get("/api/state", base_url=LOCAL).get_json()
    assert state["sam3"]["download_failed"] == "The disk has 2.1 GB free, and SAM3 needs 7.5 GB."
    assert state["trash"] is (sys.platform == "darwin")
    marker.write_text("{broken", encoding="utf-8")
    assert client.get("/api/state", base_url=LOCAL).get_json()["sam3"]["download_failed"] == ""


def test_a_folder_project_opens_and_takes_its_description(client, fakes, monkeypatch):
    """A made-here project opens and takes a description like a held project."""
    make_folder_project()
    monkeypatch.setattr(home.catalog, "project_card",
                        lambda run, *_args: {"run": run, "where": "local", "state": "made_here", "holding": False})
    answer = post(client, f"/api/projects/{FOLDER_RUN}/open", {"initials": "AT", "purpose": "labeling"})
    assert answer.get_json() == {"ok": True, "url": f"/project/{FOLDER_RUN}"}
    assert post(client, f"/api/projects/{FOLDER_RUN}/description", {"text": "student set"}).get_json()["ok"] is True


def test_remove_moves_a_folder_project_to_the_trash(client, fakes, monkeypatch, tmp_path):
    """REMOVE moves a made-here project that no annotator has open to the Trash."""
    project = make_folder_project()
    trash = tmp_path / "Trash"
    trash.mkdir()
    moved = []

    def to_trash(path):
        """Move the folder into the test's own Trash, as macOS would."""
        moved.append(Path(path).name)
        shutil.move(str(path), str(trash / Path(path).name))

    monkeypatch.setattr(home, "move_to_trash", to_trash)
    answer = post(client, f"/api/projects/{FOLDER_RUN}/remove").get_json()
    assert answer == {"ok": True, "run": FOLDER_RUN, "removed": True, "trashed": True}
    assert moved == [FOLDER_RUN] and not project.exists() and (trash / FOLDER_RUN / "project.json").is_file()
    assert client.fake_annotator.stopped == 0


@pytest.mark.parametrize("state", ["loading", "ready"])
def test_remove_refused_while_the_apps_annotator_has_the_project(client, fakes, no_finder_no_trash, state):
    """REMOVE never stops the app's own annotator to clear the way: it asks for the annotator to close."""
    project = make_folder_project()
    client.fake_annotator.run, client.fake_annotator.state = FOLDER_RUN, state
    answer = post(client, f"/api/projects/{FOLDER_RUN}/remove")
    assert answer.status_code == 409 and answer.get_json()["error"] == "Close the annotator first, then remove the project."
    assert project.is_dir() and client.fake_annotator.stopped == 0 and client.fake_annotator.run == FOLDER_RUN
    assert list(no_finder_no_trash.iterdir()) == []


def test_remove_refused_while_another_annotator_has_the_project(client, fakes, no_finder_no_trash, published):
    """An annotator the app did not start, left from an earlier session, also keeps REMOVE away."""
    folder_project_dir = make_folder_project()
    folder, run = published
    settings.save({"server_folder": str(folder), "initials": "AT"})
    wait_job(client, post(client, f"/api/projects/{run}/bring-in").get_json()["job"])
    wait_job(client, post(client, f"/api/projects/{run}/put-back").get_json()["job"])
    for project in (folder_project_dir, paths.projects_dir() / run):
        with helpers.AnnotatorPage(project, home.projects.ANNOTATOR_PORTS):
            answer = post(client, f"/api/projects/{project.name}/remove")
            assert answer.status_code == 409, project.name
            assert answer.get_json()["error"] == "Close the annotator first, then remove the project."
        assert project.is_dir()
    assert post(client, f"/api/projects/{FOLDER_RUN}/remove").get_json()["trashed"] is True
    assert post(client, f"/api/projects/{run}/remove").get_json()["removed"] is True


def test_remove_keeps_a_folder_project_when_the_trash_refuses(client, fakes, monkeypatch):
    """A project the Trash refuses stays where it was, with the reason."""
    project = make_folder_project()

    def refuse(path):
        """Refuse, as macOS does when the Trash cannot take the folder."""
        raise transfer.TransferError(f"macOS could not move {Path(path).name} to the Trash, so it stays on this computer.")

    monkeypatch.setattr(home, "move_to_trash", refuse)
    answer = post(client, f"/api/projects/{FOLDER_RUN}/remove")
    assert answer.status_code == 409 and "stays on this computer" in answer.get_json()["error"]
    assert project.is_dir()


def test_move_to_trash_asks_macos_and_checks_the_result(tmp_path):
    """The Trash move goes through macOS and is checked; elsewhere the folder is deleted."""
    folder = tmp_path / "run_x"
    folder.mkdir()
    seen = []

    def finder(command, **_kwargs):
        """Answer as macOS does after moving the folder to the Trash."""
        seen.append(command)
        shutil.rmtree(command[-1])
        return types.SimpleNamespace(returncode=0, stdout="ok\n")

    home.move_to_trash(folder, platform="darwin", runner=finder)
    assert seen[0][:3] == ["osascript", "-l", "JavaScript"] and seen[0][-1] == str(folder)
    assert "trashItemAtURL" in seen[0][-2]
    folder.mkdir()
    with pytest.raises(transfer.TransferError, match="stays on this computer"):
        home.move_to_trash(folder, platform="darwin",
                           runner=lambda *_a, **_k: types.SimpleNamespace(returncode=0, stdout="failed\n"))

    def hangs(*_args, **_kwargs):
        """Time out, as a stuck osascript does."""
        raise subprocess.TimeoutExpired("osascript", 1)

    with pytest.raises(transfer.TransferError, match="stays on this computer"):
        home.move_to_trash(folder, platform="darwin", runner=hangs)
    assert folder.is_dir()
    home.move_to_trash(folder, platform="linux")
    assert not folder.exists()


def test_folder_project_jobs_block_each_other(client, fakes, tmp_path):
    """ADD IMAGES and EXPORT FOR VEGA wait while another job runs on the project."""
    make_folder_project()
    fakes.labels.state["exported"] = True
    photos = tmp_path / "more"
    photos.mkdir()
    gate = _hold(client, FOLDER_RUN)
    try:
        for route, payload in (("add-images", {"folder": str(photos)}), ("export-for-vega", {"destination": str(tmp_path)})):
            answer = post(client, f"/api/projects/{FOLDER_RUN}/{route}", payload)
            assert answer.status_code == 409 and "still running" in answer.get_json()["error"], route
    finally:
        gate.set()


def test_job_results_with_paths_reach_the_page_as_text(client, fakes, tmp_path):
    """A job result holding paths still reaches the page as JSON."""
    make_folder_project()
    fakes.labels.state["exported"] = True
    job_id = post(client, f"/api/projects/{FOLDER_RUN}/export-for-vega", {"destination": str(tmp_path)}).get_json()["job"]
    response = client.get(f"/api/jobs/{job_id}", base_url=LOCAL)
    wait_job(client, job_id)
    assert response.status_code == 200
    assert isinstance(client.get(f"/api/jobs/{job_id}", base_url=LOCAL).get_json()["job"]["result"]["folder"], str)


def test_a_folder_project_through_the_real_modules(client, monkeypatch, tmp_path):
    """NEW PROJECT, LABELS, ADD IMAGES and EXPORT FOR VEGA's refusal, with no stand-ins."""
    from PIL import Image

    photos = tmp_path / "photos"
    (photos / "bleached").mkdir(parents=True)
    Image.new("RGB", (640, 480), (40, 90, 120)).save(photos / "IMG_1.jpg")
    Image.new("RGB", (640, 480), (90, 40, 120)).save(photos / "bleached" / "IMG_1.jpg")
    (photos / ".hidden.jpg").write_bytes(b"x")
    monkeypatch.setattr(home, "native_folder_prompt", lambda **_kwargs: str(photos))
    scan = wait_job(client, post(client, "/api/folder/choose", {"for": "images"}).get_json()["job"])
    assert scan["status"] == "done" and scan["result"]["images"] == 2 and scan["result"]["skipped_count"] == 1, scan
    created = wait_job(client, post(client, "/api/projects/new", {
        "folder": str(photos), "name": "Reef health", "labels": "healthy\nPartly bleached", "initials": "jm",
        "purpose": "health labels"}).get_json()["job"])
    assert created["status"] == "done", created
    run = created["result"]["run"]
    assert created["result"]["added"] == 2 and run.endswith("_JM_reef_health")
    card = [item for item in client.get("/api/state", base_url=LOCAL).get_json()["projects"] if item["run"] == run][0]
    assert card["state"] == "made_here" and card["frames_total"] == 2
    route = f"/api/projects/{run}/labels"
    assert client.get(route, base_url=LOCAL).get_json()["labels"] == ["healthy", "Partly bleached"]
    assert post(client, route, {"action": "add", "names": "bleached"}).get_json()["labels"][-1] == "bleached"
    assert post(client, route, {"action": "rename", "old": "bleached", "new": "Bleached"}).get_json()["labels"][-1] == "Bleached"
    assert post(client, route, {"action": "remove", "name": "Bleached"}).get_json()["labels"] == ["healthy", "Partly bleached"]
    refused = post(client, route, {"action": "add", "names": "HEALTHY"})
    assert refused.status_code == 400 and "healthy" in refused.get_json()["error"]
    more = tmp_path / "more"
    more.mkdir()
    Image.new("RGB", (320, 240), (10, 120, 60)).save(more / "IMG_9.png")
    added = wait_job(client, post(client, f"/api/projects/{run}/add-images", {"folder": str(more)}).get_json()["job"])
    assert added["status"] == "done" and added["result"]["added"] == 1, added
    usb = tmp_path / "usb"
    usb.mkdir()
    refused = post(client, f"/api/projects/{run}/export-for-vega", {"destination": str(usb)})
    assert refused.status_code == 409 and "Export a batch" in refused.get_json()["error"]
    assert client.get(f"/api/projects/{run}/export-for-vega", base_url=LOCAL).get_json()["originals_bytes"] > 0
    assert post(client, f"/api/projects/{run}/open", {"initials": "JM", "purpose": "labeling"}).get_json()["ok"] is True
    assert b'data-made-here="true"' in client.get(f"/project/{run}", base_url=LOCAL).data


# ---------------------------------------------------------------------------
# NEW PROJECT owns its project for the whole import, through the real modules.
# ---------------------------------------------------------------------------

STILL_MAKING_WORDS = "still bringing this project's images in"
STOPPED_WORDS = "import stopped partway"
PROJECT_ROUTES = (("POST", "open", {"initials": "JM", "purpose": "x"}), ("POST", "start-annotator", {}),
                  ("POST", "description", {"text": "x"}), ("POST", "open-folder", {}), ("GET", "labels", None),
                  ("POST", "labels", {"action": "add", "names": "diseased"}), ("GET", "export-for-vega", None),
                  ("POST", "export-for-vega", {"destination": "USB"}), ("POST", "bring-in", {}),
                  ("POST", "put-back", {}))


def call(client, method, route, payload):
    """Call one project route, GET or POST, and return the response."""
    return client.get(route, base_url=LOCAL) if method == "GET" else post(client, route, payload)


@pytest.fixture
def no_finder_no_trash(monkeypatch, tmp_path):
    """Keep the routes away from Finder and the person's real Trash; return the stand-in Trash."""
    trash = tmp_path / "Trash"
    trash.mkdir()
    monkeypatch.setattr(home.shims, "make_host_open_module",
                        lambda: types.SimpleNamespace(open_in_file_manager=lambda _path: (True, "")))
    monkeypatch.setattr(home, "move_to_trash", lambda path: shutil.move(str(path), str(trash / Path(path).name)))
    return trash


def photos_in(folder, count):
    """Write count small, distinct photos into folder and return it."""
    for index in range(count):
        helpers.write_image(folder / f"IMG_{index}.jpg", color=(index * 50 % 256, 90, 120))
    return folder


def test_every_project_route_waits_while_new_project_imports(client, monkeypatch, tmp_path, no_finder_no_trash):
    """While NEW PROJECT brings images in, its card is listed and every route on it refuses."""
    from vega_annotator import folder_projects
    photos = photos_in(tmp_path / "photos", 3)
    more = photos_in(tmp_path / "more", 1)
    install_fake_sam3()
    started, gate = threading.Event(), threading.Event()
    real_import = folder_projects._import

    def held_import(*args, **kwargs):
        """Hold the import until the test has tried every route."""
        started.set()
        gate.wait(10)
        return real_import(*args, **kwargs)

    monkeypatch.setattr(folder_projects, "_import", held_import)
    answer = post(client, "/api/projects/new", {"folder": str(photos), "name": "Reef health",
                                                "labels": "healthy, bleached", "initials": "jm",
                                                "purpose": "testing"}).get_json()
    run = answer["run"]
    try:
        assert started.wait(10)
        state = client.get("/api/state", base_url=LOCAL).get_json()
        card = {item["run"]: item for item in state["projects"]}[run]
        assert card["state"] == "made_here" and card["creating"] is True and card["actions"] == []
        assert [job["run"] for job in state["jobs"] if job["kind"] == "new_project"] == [run]
        for method, route, payload in PROJECT_ROUTES + (("POST", "remove", {}),
                                                        ("POST", "add-images", {"folder": str(more)})):
            payload = {"destination": str(tmp_path)} if route == "export-for-vega" and payload else payload
            response = call(client, method, f"/api/projects/{run}/{route}", payload)
            assert response.status_code == 409, (route, response.get_json())
            assert STILL_MAKING_WORDS in response.get_json()["error"], route
        second = post(client, "/api/projects/new", {"folder": str(more), "name": "Other", "labels": "a1",
                                                    "initials": "JM", "purpose": "x"})
        assert second.status_code == 409
    finally:
        gate.set()
    job = wait_job(client, answer["job"])
    assert job["status"] == "done" and job["result"]["run"] == run, job
    assert folder_projects.creating(paths.projects_dir() / run) is False
    assert post(client, f"/api/projects/{run}/open", {"initials": "JM", "purpose": "labeling"}).get_json()["ok"] is True
    assert sorted(path.name for path in no_finder_no_trash.iterdir()) == []


def stop_after(count, folder_projects, monkeypatch):
    """Make imports stop, as a pulled drive does, once a project holds count imported images."""
    real_stop = folder_projects._stop_reason

    def stop(folder, project_dir, needed=0):
        """Say the folder went away once enough images are in."""
        manifest = Path(project_dir) / "step4test_combinedAnnotate" / "custom_imports" / "imports_manifest.json"
        done = len(json.loads(manifest.read_text(encoding="utf-8"))["imports"]) if manifest.is_file() else 0
        return ("The image folder went away", "Reconnect it") if done >= count else real_stop(folder, project_dir, needed)

    monkeypatch.setattr(folder_projects, "_stop_reason", stop)
    return real_stop


def test_a_new_project_stopped_partway_offers_only_add_images_and_remove(client, monkeypatch, tmp_path,
                                                                         no_finder_no_trash):
    """A stopped import leaves a card with ADD IMAGES and REMOVE only; ADD IMAGES finishes it."""
    from vega_annotator import folder_projects
    photos = photos_in(tmp_path / "photos", 4)
    install_fake_sam3()
    real_stop = stop_after(2, folder_projects, monkeypatch)
    answer = post(client, "/api/projects/new", {"folder": str(photos), "name": "Reef health", "labels": "healthy",
                                                "initials": "jm", "purpose": "testing"}).get_json()
    run = answer["run"]
    job = wait_job(client, answer["job"])
    assert job["status"] == "failed" and "2 of 4" in job["error"], job
    card = {item["run"]: item for item in client.get("/api/state", base_url=LOCAL).get_json()["projects"]}[run]
    assert card["creating"] is True and card["actions"] == ["add_images", "remove"]
    for method, route, payload in PROJECT_ROUTES:
        payload = {"destination": str(tmp_path)} if route == "export-for-vega" and payload else payload
        response = call(client, method, f"/api/projects/{run}/{route}", payload)
        assert response.status_code == 409, (route, response.get_json())
        assert STOPPED_WORDS in response.get_json()["error"], route
    monkeypatch.setattr(folder_projects, "_stop_reason", real_stop)
    added = wait_job(client, post(client, f"/api/projects/{run}/add-images", {"folder": str(photos)}).get_json()["job"])
    assert added["status"] == "done" and added["result"]["added"] == 2, added
    assert added["result"]["skipped_count"] == 2
    assert {item["reason"] for item in added["result"]["skipped"]} == {"already in the project"}
    card = {item["run"]: item for item in client.get("/api/state", base_url=LOCAL).get_json()["projects"]}[run]
    assert card["creating"] is False and card["actions"] == ["open", "add_images", "labels", "export_for_vega", "remove"]
    assert post(client, f"/api/projects/{run}/open", {"initials": "JM", "purpose": "labeling"}).get_json()["ok"] is True


def test_remove_takes_a_project_whose_import_stopped_partway(client, monkeypatch, tmp_path, no_finder_no_trash):
    """REMOVE is the other way out of a stopped import."""
    from vega_annotator import folder_projects
    photos = photos_in(tmp_path / "photos", 3)
    stop_after(1, folder_projects, monkeypatch)
    answer = post(client, "/api/projects/new", {"folder": str(photos), "name": "Reef health", "labels": "healthy",
                                                "initials": "jm", "purpose": "testing"}).get_json()
    assert wait_job(client, answer["job"])["status"] == "failed"
    removed = post(client, f"/api/projects/{answer['run']}/remove").get_json()
    assert removed["ok"] is True and removed["trashed"] is True
    assert [path.name for path in no_finder_no_trash.iterdir()] == [answer["run"]]


def test_the_job_board_keeps_one_job_per_project():
    """A job on a project refuses another on the same project, whatever their lanes."""
    board = home.JobBoard()
    gate = threading.Event()
    try:
        board.start("new_project", "run_26-11-09_AT_reef", lambda _progress: gate.wait(5) and {}, lane="new_project")
        with pytest.raises(transfer.TransferError, match="still running"):
            board.start("add_images", "run_26-11-09_AT_reef", lambda _progress: {})
        assert board.busy_with("run_26-11-09_AT_reef")["kind"] == "new_project"
        board.start("add_images", "run_26-11-09_AT_other", lambda _progress: {})
    finally:
        gate.set()


def test_image_folder_routes_refuse_the_apps_own_folder(client, monkeypatch, tmp_path, app_home):
    """No image folder may hold VEGA Annotator's own folder or sit inside it."""
    from vega_annotator import folder_projects
    project_run = wait_job(client, post(client, "/api/projects/new", {
        "folder": str(photos_in(tmp_path / "photos", 2)), "name": "First", "labels": "healthy", "initials": "jm",
        "purpose": "testing"}).get_json()["job"])["result"]["run"]
    raw = paths.projects_dir() / project_run / "step4test_combinedAnnotate" / "custom_imports" / "raw"
    cases = ((app_home, "inside VEGA Annotator's own folder"), (paths.projects_dir(), "inside VEGA Annotator's own folder"),
             (raw, "inside VEGA Annotator's own folder"), (tmp_path, "holds VEGA Annotator's own folder"))
    for folder, words in cases:
        monkeypatch.setattr(home, "native_folder_prompt", lambda folder=folder, **_kwargs: str(folder))
        for route, payload in (("/api/folder/choose", {"for": "images"}),
                               ("/api/projects/new", {"folder": str(folder), "name": "Again", "labels": "healthy",
                                                      "initials": "JM", "purpose": "x"}),
                               (f"/api/projects/{project_run}/add-images", {"folder": str(folder)})):
            answer = post(client, route, payload)
            assert answer.status_code == 400, (folder, route, answer.get_json())
            assert words in answer.get_json()["error"], (folder, route)
    assert [card["run"] for card in client.get("/api/state", base_url=LOCAL).get_json()["projects"]] == [project_run]
    assert len(json.loads((raw.parent.parent / "segmentations" / "segmentations.json").read_text(encoding="utf-8"))) == 2
    # The destination check is the same test, unchanged: inside the app's folder only.
    assert home.inside_app_folder(raw) and not home.inside_app_folder(tmp_path)


@pytest.mark.parametrize("reason,kept", [
    ("The SAM3 download stopped at 1.2 of 3.4 GB (the connection closed early). Click DOWNLOAD SAM3 to continue from there.",
     "The SAM3 download stopped at 1.2 of 3.4 GB (the connection closed early)."),
    ("Downloading SAM3 needs 7.5 GB of free space, and the disk has 2.1 GB. Free up 5.4 GB, then click DOWNLOAD SAM3.",
     "Downloading SAM3 needs 7.5 GB of free space, and the disk has 2.1 GB. Free up 5.4 GB."),
    ("sam3-hf.tar.part2 does not match the checksum VEGA Annotator expects, so it was deleted. Click DOWNLOAD SAM3 to "
     "download it again. If this happens again, use INSTALL FROM SERVER FOLDER.",
     "sam3-hf.tar.part2 does not match the checksum VEGA Annotator expects, so it was deleted. If this happens again, "
     "use INSTALL FROM SERVER FOLDER."),
    ("GitHub has no sam3-hf.tar.part1 in the SAM3 release. Use INSTALL FROM SERVER FOLDER, or click DOWNLOAD SAM3 again later.",
     "GitHub has no sam3-hf.tar.part1 in the SAM3 release. Use INSTALL FROM SERVER FOLDER."),
    ("The disk has 2.1 GB free, and SAM3 needs 7.5 GB.", "The disk has 2.1 GB free, and SAM3 needs 7.5 GB."),
])
def test_the_saved_download_reason_drops_its_own_retry_advice(client, fakes, reason, kept):
    """The home screen adds one line saying to click DOWNLOAD SAM3, so the saved reason loses its own."""
    (paths.app_data_dir() / "sam3_download_failed.json").write_text(json.dumps({"reason": reason}), encoding="utf-8")
    assert client.get("/api/state", base_url=LOCAL).get_json()["sam3"]["download_failed"] == kept


def test_label_use_reads_the_store_again_only_when_it_changes(monkeypatch):
    """The Labels panel asks every 3 s; the store is parsed again only when its size or time changes."""
    project = make_folder_project()
    store = project / "step4test_combinedAnnotate" / "segmentations" / "segmentations.json"
    parses = []
    real_read = home.projects.read_json_object
    monkeypatch.setattr(home.projects, "read_json_object", lambda path: parses.append(path) or real_read(path))
    assert home.label_use(project) == {"bleached": 2, "healthy": 1}
    assert home.label_use(project) == {"bleached": 2, "healthy": 1}
    assert len(parses) == 1
    data = json.loads(store.read_text(encoding="utf-8"))
    data["custom_IMG_1.jpg"]["masks"].append({"id": 9, "species": "healthy", "status": "accepted"})
    store.write_text(json.dumps(data), encoding="utf-8")
    assert home.label_use(project) == {"bleached": 2, "healthy": 2} and len(parses) == 2
    store.write_text("{broken", encoding="utf-8")
    assert home.label_use(project) == {}


def test_the_scan_names_a_package_picked_as_the_image_folder(client, monkeypatch, tmp_path):
    """Choosing a package itself says what it is, rather than only that it holds no images."""
    package = tmp_path / "26-11-09_AT_reef_20261109-1530"
    helpers.write_image(package / "all_images" / "custom_IMG_1.jpg")
    for name in ("session.json", "data.yaml"):
        (package / name).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(home, "native_folder_prompt", lambda **_kwargs: str(package))
    summary = wait_job(client, post(client, "/api/folder/choose", {"for": "images"}).get_json()["job"])["result"]
    assert summary["images"] == 0 and summary["app_folder"] == "a VEGA Annotator package"
    assert summary["skipped"] == [{"path": str(package), "reason": "a VEGA Annotator package"}]
