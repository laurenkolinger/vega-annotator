"""Tests for the home screen's routes, through Flask's test client."""

import json
import urllib.error
import socket
import subprocess
import time

import pytest

import helpers
from vega_annotator import home, paths, settings, transfer

LOCAL = "http://127.0.0.1:5050"
POST_JSON = dict(content_type="application/json", base_url=LOCAL)


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
