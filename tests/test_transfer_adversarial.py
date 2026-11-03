"""Tests for what real data, tired people, and bad connections do to bring in and put back."""

import csv
import json
import threading
from pathlib import Path
import os
import shutil

import pytest

from vega_annotator import catalog, pathmap, paths, transfer
from vega_annotator.transfer import TransferError

COMPUTER = "Lab MacBook"
OTHER = "Legion"


def project_on_server(published):
    folder, run = published
    return folder / "projects" / run


def bring(published, computer=COMPUTER, **kwargs):
    folder, run = published
    return transfer.bring_in(str(folder), run, "AT", computer, **kwargs)


def edit_store(root, run):
    path = root / run / pathmap.SEGMENTATIONS_REL
    store = json.loads(path.read_text(encoding="utf-8"))
    name = sorted(store)[-1]
    store[name]["exported"] = True
    path.write_text(json.dumps(store, indent=1), encoding="utf-8")


@pytest.mark.parametrize("run", ["../run_x", "run_/../../x", "run_a/b", "run_a\\b", "notarun", "", "run_", "run_a\0b", None, 5])
def test_hostile_project_names_are_refused(server_folder, run):
    with pytest.raises(TransferError):
        transfer.bring_in(str(server_folder), run, "AT", COMPUTER)
    with pytest.raises(TransferError):
        transfer.put_back(str(server_folder), run, "AT", COMPUTER)
    with pytest.raises(TransferError):
        transfer.remove_local(run, COMPUTER)


@pytest.mark.parametrize("rel", ["../evil", "a/../../evil", "/etc/passwd", "C:/evil", "a\\b", "", "a//b", "./a", "a/./b", "x:y"])
def test_unsafe_paths_in_the_file_list_stop_the_copy(published, rel):
    listing_path = project_on_server(published) / transfer.CHECKSUMS
    listing = json.loads(listing_path.read_text(encoding="utf-8"))
    listing[rel] = {"size": 1, "sha256": "0" * 64}
    listing_path.write_text(json.dumps(listing), encoding="utf-8")
    with pytest.raises(TransferError, match="unsafe path"):
        bring(published)
    assert not (paths.projects_dir() / published[1]).exists()


def test_a_project_not_in_the_server_folder(server_folder):
    with pytest.raises(TransferError, match="not in the server folder"):
        transfer.bring_in(str(server_folder), "run_missing", "AT", COMPUTER)


@pytest.mark.parametrize("name", [transfer.SERVER_STATE, transfer.CHECKSUMS, "project.json"])
def test_missing_or_corrupt_records_stop_cleanly(published, name):
    path = project_on_server(published) / name
    original = path.read_text(encoding="utf-8")
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(TransferError):
        bring(published)
    path.unlink()
    with pytest.raises(TransferError):
        bring(published)
    path.write_text(original, encoding="utf-8")
    assert bring(published)["copied"] > 0


def test_a_newer_record_format_asks_for_an_update(published):
    path = project_on_server(published) / transfer.SERVER_STATE
    state = json.loads(path.read_text(encoding="utf-8"))
    state["format"] = 99
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(TransferError, match="Update the app"):
        bring(published)


def test_a_damaged_frame_stops_the_copy_and_releases_the_claim(published):
    project = project_on_server(published)
    frame = next((project / "step4test_combinedAnnotate" / "routed_input" / "raw").iterdir())
    frame.write_bytes(b"truncated by a bad sync")
    with pytest.raises(TransferError, match="checksum"):
        bring(published)
    assert not (paths.projects_dir() / published[1]).exists()
    assert not list(paths.projects_dir().glob(".incoming_*"))
    state = json.loads((project / transfer.SERVER_STATE).read_text(encoding="utf-8"))
    assert state["out"] is None


def test_a_missing_frame_stops_the_copy(published):
    frame = next((project_on_server(published) / "step4test_combinedAnnotate" / "routed_input" / "raw").iterdir())
    frame.unlink()
    with pytest.raises(TransferError):
        bring(published)
    assert not (paths.projects_dir() / published[1]).exists()


def test_exported_frame_with_no_picture_is_reported(published):
    project = project_on_server(published)
    listing_path = project / transfer.CHECKSUMS
    listing = json.loads(listing_path.read_text(encoding="utf-8"))
    rel = next(key for key in listing if key.endswith("T100.jpeg"))
    del listing[rel]
    listing_path.write_text(json.dumps(listing), encoding="utf-8")
    with pytest.raises(TransferError, match="picture is missing"):
        bring(published)


def test_in_use_elsewhere_is_refused_and_says_who(published):
    bring(published, computer=OTHER)
    with pytest.raises(TransferError, match="in use on Legion"):
        bring(published)


def test_take_over_claims_and_logs_it(published, tmp_path):
    folder, run = published
    bring(published, computer=OTHER, local_root=tmp_path / "legion")
    bring(published, take_over=True)
    state = json.loads((project_on_server(published) / transfer.SERVER_STATE).read_text(encoding="utf-8"))
    assert state["out"]["computer"] == COMPUTER and state["history"][-1]["action"] == "take_over"
    log = json.loads((paths.projects_dir() / run / "project.json").read_text(encoding="utf-8"))["access_log"]
    assert log[-1]["action"] == "take_over"


def test_work_on_a_taken_over_computer_is_saved_beside_the_project(published, tmp_path):
    folder, run = published
    legion = tmp_path / "legion"
    bring(published, computer=OTHER, local_root=legion)
    edit_store(legion, run)
    bring(published, take_over=True)
    before = (project_on_server(published) / pathmap.SEGMENTATIONS_REL).read_bytes()
    result = transfer.put_back(str(folder), run, "AT", OTHER, local_root=legion)
    assert result["conflict"] is True and "took this project over" in result["reason"]
    assert (project_on_server(published) / pathmap.SEGMENTATIONS_REL).read_bytes() == before
    saved = list((project_on_server(published) / "conflicts").glob(f"*/{pathmap.SEGMENTATIONS_REL}"))
    assert len(saved) == 1
    assert json.loads(saved[0].read_text(encoding="utf-8"))
    summary = json.loads(next((project_on_server(published) / "conflicts").glob("*/conflict.json")).read_text(encoding="utf-8"))
    assert summary["computer"] == OTHER and pathmap.SEGMENTATIONS_REL in summary["files"]


def test_server_moving_on_never_overwrites_this_computers_work(published, tmp_path):
    folder, run = published
    bring(published)
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    legion = tmp_path / "legion"
    transfer.bring_in(str(folder), run, "AT", OTHER, local_root=legion)
    edit_store(legion, run)
    transfer.put_back(str(folder), run, "AT", OTHER, local_root=legion)
    edit_store(paths.projects_dir(), run)
    with pytest.raises(TransferError, match="also moved on"):
        bring(published)
    result = transfer.put_back(str(folder), run, "AT", COMPUTER)
    assert result["conflict"] is True
    assert (paths.projects_dir() / run).is_dir()


def test_an_interrupted_put_back_leaves_the_old_file_and_can_run_again(published, monkeypatch):
    folder, run = published
    bring(published)
    edit_store(paths.projects_dir(), run)
    before = (project_on_server(published) / pathmap.SEGMENTATIONS_REL).read_bytes()
    real_replace = os.replace

    def dropped_connection(src, dst, **kwargs):
        if str(dst).endswith("segmentations.json") and str(project_on_server(published)) in str(dst):
            raise OSError("The network share went away")
        real_replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", dropped_connection)
    with pytest.raises(TransferError, match="went away"):
        transfer.put_back(str(folder), run, "AT", COMPUTER)
    monkeypatch.setattr(os, "replace", real_replace)
    assert (project_on_server(published) / pathmap.SEGMENTATIONS_REL).read_bytes() == before
    assert not [p for p in project_on_server(published).rglob("*") if p.name.endswith(transfer.TEMP_SUFFIX)]
    state = json.loads((project_on_server(published) / transfer.SERVER_STATE).read_text(encoding="utf-8"))
    assert state["rev"] == 1
    assert transfer.put_back(str(folder), run, "AT", COMPUTER)["rev"] == 2


def test_a_read_only_server_folder_is_refused_before_anything_changes(published, monkeypatch):
    folder, run = published
    bring(published)
    real_write = transfer.Path.write_bytes

    def refuse(self, data):
        if str(self).endswith(transfer.TEMP_SUFFIX):
            raise PermissionError("Read-only file system")
        return real_write(self, data)

    monkeypatch.setattr(transfer.Path, "write_bytes", refuse)
    with pytest.raises(TransferError, match="cannot be written"):
        transfer.put_back(str(folder), run, "AT", COMPUTER)


def test_a_full_disk_is_refused_with_the_numbers(published, monkeypatch):
    usage = shutil.disk_usage("/")
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: usage._replace(free=1000))
    with pytest.raises(TransferError, match="GB free"):
        bring(published)
    assert not (paths.projects_dir() / published[1]).exists()


def test_a_folder_the_app_did_not_make_is_left_alone(published):
    folder, run = published
    (paths.projects_dir() / run).mkdir(parents=True)
    (paths.projects_dir() / run / "mine.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(TransferError, match="did not make"):
        bring(published)
    assert (paths.projects_dir() / run / "mine.txt").read_text(encoding="utf-8") == "keep"


def test_a_different_project_with_the_same_name_is_refused(published):
    bring(published)
    path = project_on_server(published) / transfer.SERVER_STATE
    state = json.loads(path.read_text(encoding="utf-8"))
    state["project_id"] = "someone-else"
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(TransferError, match="different project"):
        bring(published)


def test_a_damaged_local_record_is_reported(published):
    folder, run = published
    bring(published)
    (paths.projects_dir() / run / transfer.LOCAL_STATE).write_text("{broken", encoding="utf-8")
    with pytest.raises(TransferError, match="damaged"):
        transfer.put_back(str(folder), run, "AT", COMPUTER)


def test_a_corrupt_working_store_is_not_sent(published):
    folder, run = published
    bring(published)
    (paths.projects_dir() / run / pathmap.SEGMENTATIONS_REL).write_text("{broken", encoding="utf-8")
    before = (project_on_server(published) / pathmap.SEGMENTATIONS_REL).read_bytes()
    with pytest.raises(TransferError, match="could not be rewritten"):
        transfer.put_back(str(folder), run, "AT", COMPUTER)
    assert (project_on_server(published) / pathmap.SEGMENTATIONS_REL).read_bytes() == before


def test_remove_refuses_unsent_work(published):
    folder, run = published
    bring(published)
    edit_store(paths.projects_dir(), run)
    with pytest.raises(TransferError, match="Put Back first"):
        transfer.remove_local(run, COMPUTER, str(folder))
    assert (paths.projects_dir() / run).is_dir()


def test_system_clutter_never_travels(published):
    folder, run = published
    bring(published)
    project = paths.projects_dir() / run
    for name in (".DS_Store", "._frame.jpeg", "Thumbs.db", "x.tmp", "y.lock"):
        (project / "step4test_combinedAnnotate" / name).write_text("junk", encoding="utf-8")
    result = transfer.put_back(str(folder), run, "AT", COMPUTER)
    assert result["changed"] == 1  # project.json's access log only
    assert not (project_on_server(published) / "step4test_combinedAnnotate" / ".DS_Store").exists()


def test_non_ascii_names_travel(published):
    folder, run = published
    bring(published)
    note = paths.projects_dir() / run / "step4test_combinedAnnotate" / "nota_coral_Montastraea_ñ.txt"
    note.write_text("é", encoding="utf-8")
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    assert (project_on_server(published) / "step4test_combinedAnnotate" / note.name).read_text(encoding="utf-8") == "é"


# --- The review's sequences of 2 Nov 2026. Each pinned what it found. ---

def _edit(root, run, flag="exported"):
    path = root / run / pathmap.SEGMENTATIONS_REL
    store = json.loads(path.read_text(encoding="utf-8"))
    store[sorted(store)[-1]][flag] = True
    path.write_text(json.dumps(store, indent=1), encoding="utf-8")


def _server_state(published):
    return json.loads((project_on_server(published) / transfer.SERVER_STATE).read_text(encoding="utf-8"))


def test_after_a_conflict_copy_there_is_a_way_out(published, tmp_path):
    folder, run = published
    root = paths.projects_dir()
    bring(published)
    _edit(root, run)
    legion = tmp_path / "legion"
    transfer.bring_in(str(folder), run, "AT", OTHER, take_over=True, local_root=legion)
    _edit(legion, run, "reviewed")
    transfer.put_back(str(folder), run, "AT", OTHER, local_root=legion)

    first = transfer.put_back(str(folder), run, "AT", COMPUTER)
    second = transfer.put_back(str(folder), run, "AT", COMPUTER)
    assert first["conflict"] and second["conflict"] and first["conflict_folder"] == second["conflict_folder"]
    assert len(list((project_on_server(published) / "conflicts").iterdir())) == 1

    # The saved copy holds this computer's edit, so bringing in the server's copy loses nothing.
    saved = json.loads((project_on_server(published) / "conflicts" / os.path.basename(first["conflict_folder"])
                        / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    assert saved[sorted(saved)[-1]]["exported"] is True
    assert bring(published)["copied"] > 0
    state = transfer.read_local_state(root / run)
    assert "saved_beside" not in state and state["server_rev"] == _server_state(published)["rev"]
    assert transfer.changed_files(root / run, state) == []
    mine = json.loads((root / run / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    assert mine[sorted(mine)[-1]]["reviewed"] is True and mine[sorted(mine)[-1]]["exported"] is False
    assert all(frame["image_path_abs"].startswith(str(root)) for frame in mine.values())


def test_a_conflict_copy_lets_this_computer_remove_its_copy(published, tmp_path):
    folder, run = published
    bring(published)
    _edit(paths.projects_dir(), run)
    transfer.bring_in(str(folder), run, "AT", OTHER, take_over=True, local_root=tmp_path / "legion")
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    assert transfer.remove_local(run, COMPUTER, str(folder))["removed"] is True
    assert _server_state(published)["out"]["computer"] == OTHER


def test_new_work_after_a_conflict_copy_is_protected_again(published, tmp_path):
    folder, run = published
    bring(published)
    _edit(paths.projects_dir(), run)
    transfer.bring_in(str(folder), run, "AT", OTHER, take_over=True, local_root=tmp_path / "legion")
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    _edit(paths.projects_dir(), run, "reviewed")
    with pytest.raises(TransferError, match="Put Back first"):
        transfer.remove_local(run, COMPUTER, str(folder))
    result = transfer.put_back(str(folder), run, "AT", COMPUTER)
    assert result["conflict"] is True
    assert len(list((project_on_server(published) / "conflicts").iterdir())) == 2


def test_a_dropped_second_bring_in_changes_nothing_here(published, tmp_path, monkeypatch):
    folder, run = published
    root = paths.projects_dir()
    bring(published)
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    legion = tmp_path / "legion"
    transfer.bring_in(str(folder), run, "AT", OTHER, local_root=legion)
    _edit(legion, run)
    transfer.put_back(str(folder), run, "AT", OTHER, local_root=legion)
    before_store = (root / run / pathmap.SEGMENTATIONS_REL).read_bytes()
    before_record = (root / run / "project.json").read_bytes()
    real, calls = transfer.copy_verified, [0]

    def dropped(src, dest, *args, **kwargs):
        calls[0] += 1
        if calls[0] == 2:
            raise TransferError("Copying failed: The network share went away")
        return real(src, dest, *args, **kwargs)

    monkeypatch.setattr(transfer, "copy_verified", dropped)
    with pytest.raises(TransferError, match="went away"):
        bring(published)
    monkeypatch.setattr(transfer, "copy_verified", real)
    assert (root / run / pathmap.SEGMENTATIONS_REL).read_bytes() == before_store
    assert (root / run / "project.json").read_bytes() == before_record
    state = transfer.read_local_state(root / run)
    assert transfer.changed_files(root / run, state, include_quiet=True) == []
    assert not list(root.glob(".incoming_*"))
    assert _server_state(published)["out"] is None
    assert bring(published)["copied"] > 0
    assert json.loads((root / run / "project.json").read_text(encoding="utf-8"))["project_dir"] == str(root / run)


def test_a_stop_while_moving_an_update_into_place_finishes_on_the_next_look(published, tmp_path, monkeypatch):
    folder, run = published
    root = paths.projects_dir()
    bring(published)
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    legion = tmp_path / "legion"
    transfer.bring_in(str(folder), run, "AT", OTHER, local_root=legion)
    _edit(legion, run)
    transfer.put_back(str(folder), run, "AT", OTHER, local_root=legion)
    real = transfer._apply_bring_in

    def stops_midway(project, journal):
        raise OSError("The computer went to sleep")

    monkeypatch.setattr(transfer, "_apply_bring_in", stops_midway)
    # The handler keeps the staging folder, because the journal still needs it.
    with pytest.raises(TransferError, match="went to sleep"):
        bring(published)
    assert (root / run / transfer.BRING_IN_JOURNAL).exists()
    monkeypatch.setattr(transfer, "_apply_bring_in", real)
    transfer.finish_interrupted(root / run, project_on_server(published))
    assert not (root / run / transfer.BRING_IN_JOURNAL).exists()
    state = transfer.read_local_state(root / run)
    assert state["server_rev"] == _server_state(published)["rev"]
    assert transfer.changed_files(root / run, state) == []
    mine = json.loads((root / run / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    assert mine[sorted(mine)[-1]]["exported"] is True


def test_a_stop_between_the_two_records_reads_as_finished(published, monkeypatch):
    folder, run = published
    root = paths.projects_dir()
    bring(published)
    _edit(root, run)
    real = transfer.write_json_atomic

    def full_disk(path, payload, indent=2):
        if str(path).endswith(transfer.LOCAL_STATE):
            raise OSError(28, "No space left on device")
        return real(path, payload, indent)

    monkeypatch.setattr(transfer, "write_json_atomic", full_disk)
    with pytest.raises(OSError):
        transfer.put_back(str(folder), run, "AT", COMPUTER)
    monkeypatch.setattr(transfer, "write_json_atomic", real)
    assert _server_state(published)["rev"] == 2
    assert (root / run / transfer.PUT_BACK_JOURNAL).exists()
    result = transfer.put_back(str(folder), run, "AT", COMPUTER)
    assert result["conflict"] is False and result["changed"] == 0
    assert not (project_on_server(published) / "conflicts").exists()
    state = transfer.read_local_state(root / run)
    assert state["server_rev"] == 2 and transfer.changed_files(root / run, state, include_quiet=True) == []
    assert transfer.remove_local(run, COMPUTER, str(folder))["removed"] is True


def test_a_put_back_that_never_finished_runs_again(published, monkeypatch):
    folder, run = published
    root = paths.projects_dir()
    bring(published)
    _edit(root, run)
    real = transfer.write_json_atomic

    def share_gone(path, payload, indent=2):
        if str(path).endswith(transfer.SERVER_STATE) and any(row.get("token") for row in payload.get("history", [])):
            raise OSError("The network share went away")
        return real(path, payload, indent)

    monkeypatch.setattr(transfer, "write_json_atomic", share_gone)
    with pytest.raises(OSError):
        transfer.put_back(str(folder), run, "AT", COMPUTER)
    monkeypatch.setattr(transfer, "write_json_atomic", real)
    assert _server_state(published)["rev"] == 1
    _edit(root, run, "reviewed")
    # More labeling, then a Bring In from the card: it keeps the work, and Put Back sends it.
    bring(published)
    mine = json.loads((root / run / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    assert mine[sorted(mine)[-1]]["reviewed"] is True
    assert transfer.put_back(str(folder), run, "AT", COMPUTER)["rev"] == 2
    server = json.loads((project_on_server(published) / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    assert server[sorted(server)[-1]]["reviewed"] is True


def test_a_save_during_the_read_stops_the_send_and_keeps_the_work(published, monkeypatch):
    folder, run = published
    root = paths.projects_dir()
    bring(published)
    _edit(root, run)
    real = transfer.sha256_file

    def save_arrives_during_read(path, *args, **kwargs):
        digest = real(path, *args, **kwargs)
        if str(path).endswith("segmentations.json"):
            _edit(root, run, "reviewed")
        return digest

    monkeypatch.setattr(transfer, "sha256_file", save_arrives_during_read)
    with pytest.raises(TransferError, match="changed while it was being read"):
        transfer.put_back(str(folder), run, "AT", COMPUTER)
    monkeypatch.setattr(transfer, "sha256_file", real)
    state = transfer.read_local_state(root / run)
    assert pathmap.SEGMENTATIONS_REL in transfer.changed_files(root / run, state)
    assert _server_state(published).get("put_back") is None
    assert transfer.put_back(str(folder), run, "AT", COMPUTER)["conflict"] is False
    server = json.loads((project_on_server(published) / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    assert server[sorted(server)[-1]]["reviewed"] is True


def test_a_take_over_during_put_back_keeps_its_claim(published, tmp_path, monkeypatch):
    folder, run = published
    root = paths.projects_dir()
    bring(published)
    _edit(root, run)
    real, done = transfer.copy_verified, [False]

    def take_over_midway(src, dest, *args, **kwargs):
        if not done[0]:
            done[0] = True
            transfer.bring_in(str(folder), run, "AT", OTHER, take_over=True, local_root=tmp_path / "legion")
        return real(src, dest, *args, **kwargs)

    monkeypatch.setattr(transfer, "copy_verified", take_over_midway)
    result = transfer.put_back(str(folder), run, "AT", COMPUTER)
    monkeypatch.setattr(transfer, "copy_verified", real)
    state = _server_state(published)
    assert result["rev"] == 2 and state["out"]["computer"] == OTHER and state["rev"] == 2
    assert transfer.read_local_state(root / run)["holding"] is False


def test_bring_in_elsewhere_waits_while_a_put_back_is_running(published, tmp_path, monkeypatch):
    folder, run = published
    bring(published)
    _edit(paths.projects_dir(), run)
    real, seen = transfer.copy_verified, []

    def try_bring_in_midway(src, dest, *args, **kwargs):
        if not seen:
            seen.append(True)
            state = _server_state(published)
            state["out"] = None
            (project_on_server(published) / transfer.SERVER_STATE).write_text(json.dumps(state), encoding="utf-8")
            with pytest.raises(TransferError, match="being put back from"):
                transfer.bring_in(str(folder), run, "AT", OTHER, local_root=tmp_path / "legion")
        return real(src, dest, *args, **kwargs)

    monkeypatch.setattr(transfer, "copy_verified", try_bring_in_midway)
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    assert seen and _server_state(published).get("put_back") is None


def test_remove_refuses_an_unsent_description_and_log(published):
    from vega_annotator import projects
    folder, run = published
    bring(published)
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    bring(published)
    projects.set_description(paths.projects_dir() / run, "Notes on this batch")
    with pytest.raises(TransferError, match="Put Back first"):
        transfer.remove_local(run, COMPUTER, str(folder))
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    server = json.loads((project_on_server(published) / "project.json").read_text(encoding="utf-8"))
    assert server["description"] == "Notes on this batch"
    assert transfer.remove_local(run, COMPUTER, str(folder))["removed"] is True


def test_remove_refuses_a_held_project_with_the_server_disconnected(published):
    folder, run = published
    bring(published)
    transfer.put_back(str(folder), run, "AT", COMPUTER, keep_holding=True)
    with pytest.raises(TransferError, match="Connect the server"):
        transfer.remove_local(run, COMPUTER)
    assert (paths.projects_dir() / run).is_dir()


def test_exported_imported_images_get_their_links_elsewhere(published, tmp_path):
    folder, run = published
    root = paths.projects_dir()
    bring(published)
    step4 = root / run / "step4test_combinedAnnotate"
    imported = step4 / "custom_imports" / "raw" / "custom_a.jpg"
    imported.parent.mkdir(parents=True)
    imported.write_bytes(b"\xff\xd8 a real picture")
    with open(root / run / pathmap.MANIFEST_REL, "a", encoding="utf-8", newline="") as handle:
        handle.write(f"custom_a,0,custom_a.txt,custom_a.jpg,exported_empty,,batch_002,{imported},\n")
    (step4 / "all_labels" / "custom_a.txt").write_text("", encoding="utf-8")
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    legion = tmp_path / "legion"
    transfer.bring_in(str(folder), run, "AT", OTHER, local_root=legion)
    link = legion / run / "step4test_combinedAnnotate" / "all_images" / "custom_a.jpg"
    assert link.read_bytes() == b"\xff\xd8 a real picture"


def test_a_manifest_path_outside_the_project_is_not_followed(published):
    folder, run = published
    project = project_on_server(published)
    listing_path = project / transfer.CHECKSUMS
    with open(project / pathmap.MANIFEST_REL, "a", encoding="utf-8", newline="") as handle:
        handle.write("evil,0,evil.txt,evil.jpg,exported_empty,,batch_002,/etc/passwd,\n")
    from vega_annotator.atomic import sha256_file
    listing = json.loads(listing_path.read_text(encoding="utf-8"))
    path = project / pathmap.MANIFEST_REL
    listing[pathmap.MANIFEST_REL] = {"size": path.stat().st_size, "sha256": sha256_file(path)}
    listing_path.write_text(json.dumps(listing), encoding="utf-8")
    with pytest.raises(TransferError, match="picture is missing"):
        bring(published)


def test_a_held_folder_rename_gives_a_plain_message_and_releases(published, monkeypatch):
    real = os.replace

    def held(src, dst, **kwargs):
        if ".incoming_" in str(src) and str(dst).endswith(published[1]):
            raise PermissionError("in use by another process")
        return real(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", held)
    monkeypatch.setattr(transfer, "FOLDER_RENAME_ATTEMPTS", 2)
    monkeypatch.setattr(transfer, "FOLDER_RENAME_WAIT_S", 0)
    with pytest.raises(TransferError, match="holding"):
        bring(published)
    monkeypatch.setattr(os, "replace", real)
    assert not list(paths.projects_dir().glob(".incoming_*"))
    assert _server_state(published)["out"] is None


def test_records_written_for_the_server_are_group_readable(published):
    bring(published)
    assert (project_on_server(published) / transfer.SERVER_STATE).stat().st_mode & 0o044 == 0o044


def test_the_home_screens_refresh_cannot_finish_a_running_put_back(published, monkeypatch):
    folder, run = published
    root = paths.projects_dir()
    bring(published)
    _edit(root, run)
    real = transfer.write_json_atomic
    errors, polls = [], []

    def poll():
        try:
            transfer.finish_interrupted(root / run, project_on_server(published))
        except Exception as exc:  # The test reports whatever the refresh would have hit.
            errors.append(exc)

    def poll_during_the_journal(path, payload, indent=2):
        result = real(path, payload, indent)
        if str(path).endswith(transfer.PUT_BACK_JOURNAL):
            polls.append(threading.Thread(target=poll))
            polls[-1].start()
            polls[-1].join(0.2)
        return result

    monkeypatch.setattr(transfer, "write_json_atomic", poll_during_the_journal)
    result = transfer.put_back(str(folder), run, "AT", COMPUTER)
    monkeypatch.setattr(transfer, "write_json_atomic", real)
    polls[0].join(5)
    assert result["rev"] == 2 and result["conflict"] is False and not errors
    assert transfer.read_local_state(root / run)["server_rev"] == 2
    assert not (root / run / transfer.PUT_BACK_JOURNAL).exists()


def test_the_home_screens_refresh_cannot_finish_a_running_bring_in(published, tmp_path, monkeypatch):
    folder, run = published
    root = paths.projects_dir()
    bring(published)
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    legion = tmp_path / "legion"
    transfer.bring_in(str(folder), run, "AT", OTHER, local_root=legion)
    _edit(legion, run)
    transfer.put_back(str(folder), run, "AT", OTHER, local_root=legion)
    real = transfer.write_json_atomic
    errors, polls = [], []

    def poll():
        try:
            transfer.finish_interrupted(root / run, project_on_server(published))
        except Exception as exc:  # The test reports whatever the refresh would have hit.
            errors.append(exc)

    def poll_during_the_journal(path, payload, indent=2):
        result = real(path, payload, indent)
        if str(path).endswith(transfer.BRING_IN_JOURNAL):
            polls.append(threading.Thread(target=poll))
            polls[-1].start()
            polls[-1].join(0.2)
        return result

    monkeypatch.setattr(transfer, "write_json_atomic", poll_during_the_journal)
    assert bring(published)["copied"] > 0
    monkeypatch.setattr(transfer, "write_json_atomic", real)
    polls[0].join(5)
    assert not errors and not (root / run / transfer.BRING_IN_JOURNAL).exists()
    state = transfer.read_local_state(root / run)
    assert state["server_rev"] == _server_state(published)["rev"]
    assert transfer.changed_files(root / run, state) == []


def test_two_computers_claiming_at_once_lose_no_work(published, tmp_path):
    folder, run = published
    roots = {COMPUTER: paths.projects_dir(), OTHER: tmp_path / "legion"}
    barrier = threading.Barrier(2)
    failures = []

    def claim(computer):
        barrier.wait(5)
        try:
            try:
                transfer.bring_in(str(folder), run, "AT", computer, take_over=True, local_root=roots[computer])
            except TransferError as exc:
                assert "Try again" in str(exc)
                transfer.bring_in(str(folder), run, "AT", computer, take_over=True, local_root=roots[computer])
        except Exception as exc:  # The test reports whatever a claim hit.
            failures.append(exc)

    threads = [threading.Thread(target=claim, args=(name,)) for name in roots]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert not failures
    holder = _server_state(published)["out"]["computer"]
    other = OTHER if holder == COMPUTER else COMPUTER
    _edit(roots[holder], run, "exported")
    _edit(roots[other], run, "reviewed")
    sent = transfer.put_back(str(folder), run, "AT", holder, local_root=roots[holder])
    beside = transfer.put_back(str(folder), run, "AT", other, local_root=roots[other])
    assert sent["conflict"] is False and beside["conflict"] is True
    server = json.loads((project_on_server(published) / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    assert server[sorted(server)[-1]]["exported"] is True
    saved = json.loads((Path(beside["conflict_folder"]) / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    assert saved[sorted(saved)[-1]]["reviewed"] is True


def test_a_replaced_record_keeps_this_computers_log_rows_and_description(published, tmp_path):
    from vega_annotator import projects
    folder, run = published
    root = paths.projects_dir()
    bring(published)
    projects.stamp_access(root / run, "AT", "open", "checking the batch on the Air")
    projects.set_description(root / run, "Written on the Air")
    legion = tmp_path / "legion"
    transfer.bring_in(str(folder), run, "LO", OTHER, take_over=True, local_root=legion)
    _edit(legion, run)
    transfer.put_back(str(folder), run, "LO", OTHER, local_root=legion)
    assert transfer.bring_in(str(folder), run, "AT", COMPUTER)["copied"] > 0
    record = json.loads((root / run / "project.json").read_text(encoding="utf-8"))
    log = record["access_log"]
    assert any(row["purpose"] == "checking the batch on the Air" for row in log)
    assert any(row["initials"] == "LO" for row in log)
    assert [row["ts"] for row in log] == sorted(row["ts"] for row in log)
    assert record["description"] == "Written on the Air"
    assert record["project_dir"] == str(root / run)
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    server = json.loads((project_on_server(published) / "project.json").read_text(encoding="utf-8"))
    assert any(row["purpose"] == "checking the batch on the Air" for row in server["access_log"])
    assert server["description"] == "Written on the Air"


def test_a_failed_bring_in_is_recorded_as_failed(published, monkeypatch):
    def dropped(*_args, **_kwargs):
        raise TransferError("Copying failed: The network share went away")

    monkeypatch.setattr(transfer, "copy_verified", dropped)
    with pytest.raises(TransferError):
        bring(published)
    actions = [row["action"] for row in _server_state(published)["history"]]
    assert actions[-2:] == ["bring_in", "bring_in_failed"]


def test_bring_in_restores_a_file_gone_from_this_computer(published):
    folder, run = published
    root = paths.projects_dir()
    bring(published)
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    prompts = root / run / "step4test_combinedAnnotate" / "routed_input" / "ids" / "sam_click_prompts.json"
    before = prompts.read_bytes()
    prompts.unlink()
    assert bring(published)["copied"] == 1
    assert prompts.read_bytes() == before


def test_a_take_over_of_a_stopped_put_back_says_what_to_do(published, tmp_path):
    folder, run = published
    root = paths.projects_dir()
    bring(published)
    state = _server_state(published)
    state["put_back"] = {"computer": COMPUTER, "at": "2026-11-02T12:00:00-04:00", "token": "abc"}
    (project_on_server(published) / transfer.SERVER_STATE).write_text(json.dumps(state), encoding="utf-8")
    store = project_on_server(published) / pathmap.SEGMENTATIONS_REL
    store.write_bytes(store.read_bytes() + b" ")
    with pytest.raises(TransferError, match="run Put Back on that computer first"):
        transfer.bring_in(str(folder), run, "LO", OTHER, take_over=True, local_root=tmp_path / "legion")
    assert not (tmp_path / "legion" / run).exists()
    assert (root / run).is_dir()


def test_a_missing_staged_file_does_not_wedge_the_journal(published, tmp_path):
    folder, run = published
    root = paths.projects_dir()
    bring(published)
    journal = {"staging": str(tmp_path / "gone"), "server_rev": 1, "computer": COMPUTER,
               "files": {"step4test_combinedAnnotate/new_file.txt": "0" * 64}}
    (root / run / transfer.BRING_IN_JOURNAL).write_text(json.dumps(journal), encoding="utf-8")
    transfer.finish_interrupted(root / run, project_on_server(published))
    assert not (root / run / transfer.BRING_IN_JOURNAL).exists()
    assert "step4test_combinedAnnotate/new_file.txt" not in transfer.read_local_state(root / run)["base"]


# --- The review's second pass, 2 Nov 2026. Each pinned what it found. ---

class Crash(BaseException):
    """Stands in for a hard stop: the handlers for ordinary errors do not see it."""


def _newer_on_server(published, tmp_path):
    """Leave this computer with a clean copy while the server moved on. Returns Legion's folder."""
    folder, run = published
    bring(published)
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    legion = tmp_path / "legion"
    transfer.bring_in(str(folder), run, "AT", OTHER, local_root=legion)
    _edit(legion, run)
    transfer.put_back(str(folder), run, "AT", OTHER, local_root=legion)
    return legion


def _exported_names(project):
    with open(project / pathmap.MANIFEST_REL, newline="", encoding="utf-8") as handle:
        return {row["image_file"] for row in csv.DictReader(handle) if row["outcome"].startswith("exported")}


def test_image_links_match_the_manifest_exactly(published):
    folder, run = published
    bring(published)
    project = paths.projects_dir() / run
    images = project / pathmap.STEP4_DIR / "all_images"
    raw = project / pathmap.STEP4_DIR / "routed_input" / "raw"
    sources = sorted(raw.iterdir())
    os.symlink(sources[-1], images / "stale_link.jpeg")
    (images / "stale_copy.jpeg").write_bytes(b"a real file")
    transfer.rebuild_image_links(project)
    assert {entry.name for entry in images.iterdir()} == _exported_names(project)
    assert sources[-1].is_file()
    kept = list((project / "_superseded").glob("*/stale_all_images/stale_copy.jpeg"))
    assert kept and kept[0].read_bytes() == b"a real file"


def test_a_dangling_image_link_is_replaced(published):
    folder, run = published
    bring(published)
    project = paths.projects_dir() / run
    images = project / pathmap.STEP4_DIR / "all_images"
    name = sorted(_exported_names(project))[0]
    (images / name).unlink()
    os.symlink(project / "nothing_here.jpeg", images / name)
    transfer.rebuild_image_links(project)
    assert (images / name).is_file()


def test_no_manifest_on_the_incoming_copy_clears_the_links(published):
    folder, run = published
    bring(published)
    project = paths.projects_dir() / run
    (project / pathmap.MANIFEST_REL).unlink()
    transfer.rebuild_image_links(project)
    assert list((project / pathmap.STEP4_DIR / "all_images").iterdir()) == []


def test_a_failed_take_over_keeps_the_put_back_that_finished_meanwhile(published, tmp_path, monkeypatch):
    folder, run = published
    bring(published)

    def lab_put_back_finishes_then_the_share_drops(*_args, **_kwargs):
        # Lab's Put Back passed its checks before this take over, and its last step finishes now.
        state = _server_state(published)
        state["rev"] += 1
        state["history"].append({"at": "now", "action": "put_back", "computer": COMPUTER, "token": "abc"})
        (project_on_server(published) / transfer.SERVER_STATE).write_text(json.dumps(state), encoding="utf-8")
        raise TransferError("Copying failed: The network share went away")

    monkeypatch.setattr(transfer, "copy_verified", lab_put_back_finishes_then_the_share_drops)
    with pytest.raises(TransferError, match="went away"):
        transfer.bring_in(str(folder), run, "LO", OTHER, take_over=True, local_root=tmp_path / "legion")
    state = _server_state(published)
    assert state["rev"] == 2 and state["out"] is None and state["put_back"] is None
    actions = [row["action"] for row in state["history"]]
    assert "put_back" in actions and actions[-1] == "take_over_failed"


def test_a_failed_bring_in_keeps_a_claim_another_computer_made_meanwhile(published, tmp_path, monkeypatch):
    folder, run = published
    real, done = transfer.copy_verified, [False]

    def legion_takes_over_then_the_share_drops(src, dest, *args, **kwargs):
        if not done[0]:
            done[0] = True
            monkeypatch.setattr(transfer, "copy_verified", real)
            transfer.bring_in(str(folder), run, "LO", OTHER, take_over=True, local_root=tmp_path / "legion")
        raise TransferError("Copying failed: The network share went away")

    monkeypatch.setattr(transfer, "copy_verified", legion_takes_over_then_the_share_drops)
    with pytest.raises(TransferError, match="went away"):
        bring(published)
    assert _server_state(published)["out"]["computer"] == OTHER


def test_a_claim_refuses_a_record_that_moved_after_the_checks(published, tmp_path, monkeypatch):
    folder, run = published
    real = transfer._check_space

    def legion_claims_during_the_checks(*args):
        monkeypatch.setattr(transfer, "_check_space", real)
        transfer.bring_in(str(folder), run, "LO", OTHER, local_root=tmp_path / "legion")
        return real(*args)

    monkeypatch.setattr(transfer, "_check_space", legion_claims_during_the_checks)
    with pytest.raises(TransferError, match="Try again"):
        bring(published)
    assert _server_state(published)["out"]["computer"] == OTHER


def test_a_stop_before_the_links_are_rebuilt_runs_again(published, tmp_path, monkeypatch):
    folder, run = published
    root = paths.projects_dir()
    _newer_on_server(published, tmp_path)
    images = root / run / pathmap.STEP4_DIR / "all_images"
    shutil.rmtree(images)
    real = transfer.rebuild_image_links

    def stops(*_args, **_kwargs):
        raise Crash()

    monkeypatch.setattr(transfer, "rebuild_image_links", stops)
    with pytest.raises(Crash):
        bring(published)
    monkeypatch.setattr(transfer, "rebuild_image_links", real)
    assert (root / run / transfer.BRING_IN_JOURNAL).exists()
    transfer.finish_interrupted(root / run, project_on_server(published))
    assert not (root / run / transfer.BRING_IN_JOURNAL).exists()
    assert {entry.name for entry in images.iterdir()} == _exported_names(root / run)


def _stop_mid_move(published, monkeypatch, after):
    """Run a Bring In that stops after moving some staged files into place."""
    real = os.replace
    moved = [0]

    def stops(src, dst, **kwargs):
        if ".incoming_" in str(src) and ".incoming_" not in str(dst):
            if moved[0] == after:
                raise Crash()
            moved[0] += 1
        return real(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", stops)
    with pytest.raises(Crash):
        bring(published)
    monkeypatch.setattr(os, "replace", real)


@pytest.mark.parametrize("after", [0, 1])
def test_a_real_stop_mid_move_finishes_on_the_next_look(published, tmp_path, monkeypatch, after):
    folder, run = published
    root = paths.projects_dir()
    legion = _newer_on_server(published, tmp_path)
    _stop_mid_move(published, monkeypatch, after)
    assert (root / run / transfer.BRING_IN_JOURNAL).exists()
    transfer.finish_interrupted(root / run, project_on_server(published))
    state = transfer.read_local_state(root / run)
    assert state["server_rev"] == _server_state(published)["rev"]
    assert transfer.changed_files(root / run, state) == []
    mine = json.loads((root / run / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    theirs = json.loads((legion / run / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    assert [frame["exported"] for frame in mine.values()] == [frame["exported"] for frame in theirs.values()]
    assert not list(root.glob(".incoming_*"))


def test_a_stopped_bring_in_finishes_offline_so_later_work_is_never_marked_sent(published, tmp_path, monkeypatch):
    folder, run = published
    root = paths.projects_dir()
    _newer_on_server(published, tmp_path)
    _stop_mid_move(published, monkeypatch, 0)
    # The app starts again with the server disconnected, and the home screen looks at the card.
    cards = catalog.list_projects("", False, COMPUTER)
    assert [card["run"] for card in cards] == [run]
    assert not (root / run / transfer.BRING_IN_JOURNAL).exists()
    _edit(root, run, "reviewed")
    state = transfer.read_local_state(root / run)
    assert pathmap.SEGMENTATIONS_REL in transfer.changed_files(root / run, state)
    # Back on the server, the labeling goes up.
    result = transfer.put_back(str(folder), run, "AT", COMPUTER)
    assert result["conflict"] is False and result["changed"] >= 1
    server = json.loads((project_on_server(published) / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    assert server[sorted(server)[-1]]["reviewed"] is True


def test_a_project_gone_from_the_server_says_so_and_protects_a_held_copy(published):
    folder, run = published
    root = paths.projects_dir()
    bring(published)
    _edit(root, run)
    shutil.rmtree(project_on_server(published))
    card = catalog.list_projects(str(folder), True, COMPUTER)[0]
    assert card["state"] == "not_on_server" and card["holding"] is True and card["pending_files"] == 1
    with pytest.raises(TransferError, match="does not hold"):
        transfer.put_back(str(folder), run, "AT", COMPUTER)
    with pytest.raises(TransferError, match="Put Back first"):
        transfer.remove_local(run, COMPUTER, str(folder))
    assert (root / run).is_dir()


def test_a_held_clean_copy_gone_from_the_server_is_not_removed(published):
    folder, run = published
    root = paths.projects_dir()
    bring(published)
    transfer.put_back(str(folder), run, "AT", COMPUTER, keep_holding=True)
    shutil.rmtree(project_on_server(published))
    assert catalog.list_projects(str(folder), True, COMPUTER)[0]["state"] == "not_on_server"
    with pytest.raises(TransferError, match="publish the project again"):
        transfer.remove_local(run, COMPUTER, str(folder))
    assert (root / run).is_dir()


def test_an_unheld_clean_copy_gone_from_the_server_can_be_removed(published):
    folder, run = published
    bring(published)
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    shutil.rmtree(project_on_server(published))
    card = catalog.list_projects(str(folder), True, COMPUTER)[0]
    assert card["state"] == "not_on_server" and card["holding"] is False
    assert transfer.remove_local(run, COMPUTER, str(folder))["removed"] is True


def test_a_stale_put_back_marker_offers_take_over_and_take_over_clears_it(published, tmp_path):
    folder, run = published
    state = _server_state(published)
    state["put_back"] = {"computer": OTHER, "initials": "LO", "at": "2026-11-02T12:00:00-04:00", "token": "abc"}
    (project_on_server(published) / transfer.SERVER_STATE).write_text(json.dumps(state), encoding="utf-8")
    card = catalog.list_projects(str(folder), True, COMPUTER)[0]
    assert card["state"] == "held_elsewhere" and card["holder"]["computer"] == OTHER
    assert "token" not in card["holder"]
    with pytest.raises(TransferError, match="being put back from"):
        bring(published)
    bring(published, take_over=True)
    assert _server_state(published)["put_back"] is None
    bring(published)
    _edit(paths.projects_dir(), run)
    assert transfer.put_back(str(folder), run, "AT", COMPUTER)["conflict"] is False


def test_put_back_goes_beside_while_another_computer_is_mid_put_back(published):
    folder, run = published
    bring(published)
    _edit(paths.projects_dir(), run)
    state = _server_state(published)
    state["out"] = None
    state["put_back"] = {"computer": OTHER, "initials": "LO", "at": "2026-11-02T12:00:00-04:00", "token": "abc"}
    (project_on_server(published) / transfer.SERVER_STATE).write_text(json.dumps(state), encoding="utf-8")
    result = transfer.put_back(str(folder), run, "AT", COMPUTER)
    assert result["conflict"] is True and OTHER in result["reason"]


def test_put_back_with_no_work_on_a_newer_copy_writes_no_conflict(published, tmp_path):
    folder, run = published
    root = paths.projects_dir()
    _newer_on_server(published, tmp_path)
    state = transfer.read_local_state(root / run)
    assert transfer.changed_files(root / run, state, include_quiet=True) == []
    result = transfer.put_back(str(folder), run, "AT", COMPUTER)
    assert result["conflict"] is False and result["changed"] == 0
    assert not (project_on_server(published) / "conflicts").exists()
    assert catalog.list_projects(str(folder), True, COMPUTER)[0]["state"] == "server_newer"


def test_a_finished_put_back_reads_as_finished_after_the_server_moves_on(published, tmp_path, monkeypatch):
    folder, run = published
    root = paths.projects_dir()
    bring(published)
    _edit(root, run)
    real = transfer.write_json_atomic

    def stops_before_the_local_record(path, payload, indent=2):
        if str(path).endswith(transfer.LOCAL_STATE):
            raise Crash()
        return real(path, payload, indent)

    monkeypatch.setattr(transfer, "write_json_atomic", stops_before_the_local_record)
    with pytest.raises(Crash):
        transfer.put_back(str(folder), run, "AT", COMPUTER)
    monkeypatch.setattr(transfer, "write_json_atomic", real)
    legion = tmp_path / "legion"
    transfer.bring_in(str(folder), run, "LO", OTHER, local_root=legion)
    _edit(legion, run, "reviewed")
    transfer.put_back(str(folder), run, "LO", OTHER, local_root=legion)
    card = catalog.list_projects(str(folder), True, COMPUTER)[0]
    assert card["state"] == "server_newer"
    assert not (project_on_server(published) / "conflicts").exists()


def test_staging_a_hard_stop_left_behind_is_cleared_by_the_next_bring_in(published, tmp_path, monkeypatch):
    folder, run = published
    root = paths.projects_dir()
    _newer_on_server(published, tmp_path)
    real = transfer.copy_verified

    def stops(*_args, **_kwargs):
        raise Crash()

    monkeypatch.setattr(transfer, "copy_verified", stops)
    with pytest.raises(Crash):
        bring(published)
    monkeypatch.setattr(transfer, "copy_verified", real)
    assert list(root.glob(f".incoming_{run}_*"))
    bring(published)
    assert not list(root.glob(f".incoming_{run}_*"))


def test_a_share_that_refuses_chmod_still_saves(published, monkeypatch):
    folder, run = published

    def refused(*_args, **_kwargs):
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(os, "chmod", refused)
    bring(published)
    _edit(paths.projects_dir(), run)
    assert transfer.put_back(str(folder), run, "AT", COMPUTER)["rev"] == 2


def test_messages_show_a_readable_time(published, tmp_path):
    folder, run = published
    transfer.bring_in(str(folder), run, "LO", OTHER, local_root=tmp_path / "legion")
    with pytest.raises(TransferError) as caught:
        bring(published)
    assert "T" not in str(caught.value).split("since ")[1]


def test_a_first_bring_in_leaves_room_for_labeling_to_grow(published, monkeypatch):
    folder, run = published
    listing = json.loads((project_on_server(published) / transfer.CHECKSUMS).read_text(encoding="utf-8"))
    frames = sum(transfer.FRAME_FOLDER_MARK in rel for rel in listing)
    copy_bytes = sum(entry["size"] for rel, entry in listing.items() if not transfer.is_excluded(rel))
    assert frames > 0
    room = copy_bytes + transfer.FREE_SPACE_MARGIN_BYTES + frames * transfer.GROWTH_PER_FRAME_BYTES

    class Usage:
        def __init__(self, free):
            self.free = free

    monkeypatch.setattr(transfer.shutil, "disk_usage", lambda _path: Usage(room - 1))
    with pytest.raises(TransferError, match="GB free"):
        bring(published)
    assert not (paths.projects_dir() / run).exists()
    monkeypatch.setattr(transfer.shutil, "disk_usage", lambda _path: Usage(room))
    assert bring(published)["copied"] > 0


def test_a_project_size_ignores_image_links_and_records(published):
    folder, run = published
    bring(published)
    project = paths.projects_dir() / run
    before = transfer.project_size(project)
    images = project / pathmap.STEP4_DIR / "all_images"
    assert any(images.iterdir())
    (project / "vega_annotator_extra.txt").write_bytes(b"12345")
    assert transfer.project_size(project) == before + 5


def test_a_same_day_repeat_keeps_its_staging_folder(published, tmp_path, monkeypatch):
    folder, run = published
    root = paths.projects_dir()
    root.mkdir(parents=True, exist_ok=True)
    other = root / f".incoming_{run}_2_ab12cd34"
    other.mkdir()
    bring(published)
    assert other.is_dir()
