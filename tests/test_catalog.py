"""Tests for catalog.py: each project card's state and the server folder check."""

import json
import os
from pathlib import Path

import pytest

import helpers
from vega_annotator import catalog, pathmap, paths, transfer

COMPUTER = "Lab MacBook"
OTHER = "Legion"


def cards(server_folder, computer=COMPUTER, connected=True):
    return catalog.list_projects(str(server_folder), connected, computer)


def edit(run):
    path = paths.projects_dir() / run / pathmap.SEGMENTATIONS_REL
    store = json.loads(path.read_text(encoding="utf-8"))
    store[sorted(store)[-1]]["exported"] = True
    path.write_text(json.dumps(store, indent=1), encoding="utf-8")


def test_only_on_the_server(published):
    folder, run = published
    card = cards(folder)[0]
    assert (card["run"], card["where"], card["state"]) == (run, "server", "on_server")
    assert card["frames_left"] == 4 and card["project_name"]


def test_brought_in(published):
    folder, run = published
    transfer.bring_in(str(folder), run, "AT", COMPUTER)
    card = cards(folder)[0]
    assert card["where"] == "both" and card["state"] == "here" and card["pending_files"] == 0


def test_changed_then_put_back(published):
    folder, run = published
    transfer.bring_in(str(folder), run, "AT", COMPUTER)
    edit(run)
    card = cards(folder)[0]
    assert card["state"] == "changed" and card["pending_files"] == 1
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    assert cards(folder)[0]["state"] == "put_back"


def test_held_elsewhere_with_and_without_a_copy_here(published, tmp_path):
    folder, run = published
    transfer.bring_in(str(folder), run, "AT", OTHER, local_root=tmp_path / "legion")
    card = cards(folder)[0]
    assert card["state"] == "held_elsewhere" and card["holder"]["computer"] == OTHER


def test_server_newer(published, tmp_path):
    folder, run = published
    transfer.bring_in(str(folder), run, "AT", COMPUTER)
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    legion = tmp_path / "legion"
    transfer.bring_in(str(folder), run, "AT", OTHER, local_root=legion)
    path = legion / run / pathmap.SEGMENTATIONS_REL
    store = json.loads(path.read_text(encoding="utf-8"))
    store[sorted(store)[-1]]["exported"] = True
    path.write_text(json.dumps(store, indent=1), encoding="utf-8")
    transfer.put_back(str(folder), run, "AT", OTHER, local_root=legion)
    assert cards(folder)[0]["state"] == "server_newer"


def test_conflict_when_both_sides_changed(published, tmp_path):
    folder, run = published
    transfer.bring_in(str(folder), run, "AT", COMPUTER)
    edit(run)
    state_path = folder / "projects" / run / transfer.SERVER_STATE
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["rev"] += 1
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert cards(folder)[0]["state"] == "conflict"


def test_offline_keeps_held_work_openable(published):
    folder, run = published
    transfer.bring_in(str(folder), run, "AT", COMPUTER)
    assert cards("", connected=False)[0]["state"] == "here"
    edit(run)
    assert cards("", connected=False)[0]["state"] == "changed"


def test_offline_after_put_back_says_connect(published):
    folder, run = published
    transfer.bring_in(str(folder), run, "AT", COMPUTER)
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    assert cards("", connected=False)[0]["state"] == "no_server"


@pytest.mark.parametrize("rel,content,reason", [
    ("project.json", "{broken", "not valid JSON"),
    (pathmap.SEGMENTATIONS_REL, "{broken", "not valid JSON"),
])
def test_a_corrupt_record_shows_its_reason_and_no_actions(published, rel, content, reason):
    folder, run = published
    (folder / "projects" / run / rel).write_text(content, encoding="utf-8")
    card = cards(folder)[0]
    assert card["state"] == "error" and reason in card["error"]


def test_a_project_with_a_missing_record_is_still_listed(published):
    folder, run = published
    (folder / "projects" / run / "project.json").unlink()
    card = cards(folder)[0]
    assert card["run"] == run and card["state"] == "error" and "missing" in card["error"]


def test_a_publishing_folder_is_not_listed(server_folder):
    (server_folder / "projects" / ".publishing_run_x_ab12").mkdir()
    assert cards(server_folder) == []


def test_saved_beside_state_after_a_conflict(published, tmp_path):
    folder, run = published
    transfer.bring_in(str(folder), run, "AT", COMPUTER)
    edit(run)
    transfer.bring_in(str(folder), run, "AT", OTHER, take_over=True, local_root=tmp_path / "legion")
    assert cards(folder)[0]["state"] == "conflict"
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    card = cards(folder)[0]
    assert card["state"] == "saved_beside" and "conflicts" in card["conflict_folder"]
    edit_more = paths.projects_dir() / run / pathmap.SEGMENTATIONS_REL
    store = json.loads(edit_more.read_text(encoding="utf-8"))
    store[sorted(store)[-1]]["reviewed"] = True
    edit_more.write_text(json.dumps(store, indent=1), encoding="utf-8")
    assert cards(folder)[0]["state"] == "conflict"


def test_a_description_is_unsent_without_marking_labeling_work(published):
    from vega_annotator import projects
    folder, run = published
    transfer.bring_in(str(folder), run, "AT", COMPUTER)
    projects.set_description(paths.projects_dir() / run, "notes")
    card = cards(folder)[0]
    assert card["state"] == "here" and card["pending_files"] == 0 and card["unsent_records"] is True


def test_newest_first(server_folder, tmp_path):
    older = helpers.make_project(tmp_path / "a", run="run_a")
    newer = helpers.make_project(tmp_path / "b", run="run_b")
    record = json.loads((newer / "project.json").read_text(encoding="utf-8"))
    record["updated_at"] = "2030-01-01T00:00:00-04:00"
    (newer / "project.json").write_text(json.dumps(record), encoding="utf-8")
    helpers.publish_to(server_folder, older)
    helpers.publish_to(server_folder, newer)
    assert [card["run"] for card in cards(server_folder)] == ["run_b", "run_a"]


def test_server_status_states(server_folder, tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "autodetect_server_folder", lambda: "")
    assert catalog.server_status("")["connected"] is False
    assert catalog.server_status(str(server_folder))["connected"] is True
    assert catalog.server_status(str(server_folder.parent))["path"] == str(server_folder)
    gone = catalog.server_status(str(tmp_path / "gone"))
    assert gone["connected"] is False and "not connected" in gone["reason"]
    empty = tmp_path / "empty"
    empty.mkdir()
    assert "holds no projects" in catalog.server_status(str(empty))["reason"]


def test_a_hanging_share_does_not_hang_the_page():
    import threading
    release = threading.Event()
    assert catalog.with_timeout(lambda: release.wait(5), 0.05, "gave up") == "gave up"
    release.set()


def test_with_timeout_treats_a_disconnect_as_the_default():
    def boom():
        raise OSError("Host is down")

    assert catalog.with_timeout(boom, 1, "default") == "default"


def test_a_card_shows_the_project_size_in_both_places(published):
    folder, run = published
    listing = json.loads((folder / "projects" / run / transfer.CHECKSUMS).read_text(encoding="utf-8"))
    assert cards(folder)[0]["size_bytes"] == sum(entry["size"] for entry in listing.values())
    transfer.bring_in(str(folder), run, "AT", COMPUTER)
    card = cards(folder)[0]
    assert card["size_bytes"] == transfer.project_size(paths.projects_dir() / run) > 0


def test_a_broken_file_list_gives_no_size_and_no_crash(published):
    folder, run = published
    (folder / "projects" / run / transfer.CHECKSUMS).write_text("{broken", encoding="utf-8")
    assert cards(folder)[0]["size_bytes"] is None
