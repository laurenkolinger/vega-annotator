"""Tests for bringing a project in and putting it back, on a small real-format project."""

import json
import os
from pathlib import Path

import pytest

from vega_annotator import pathmap, paths, projects, transfer

COMPUTER = "Lab MacBook"
OTHER = "Legion"


def server_project(published):
    folder, run = published
    return folder / "projects" / run


def server_state(published):
    return json.loads((server_project(published) / transfer.SERVER_STATE).read_text(encoding="utf-8"))


def local_project(run):
    return paths.projects_dir() / run


def bring(published, computer=COMPUTER, **kwargs):
    folder, run = published
    return transfer.bring_in(str(folder), run, "AT", computer, **kwargs)


def label_a_frame(run):
    """Change what an annotator export changes: the store, a label, and the manifest."""
    project = local_project(run)
    store_path = project / pathmap.SEGMENTATIONS_REL
    store = json.loads(store_path.read_text(encoding="utf-8"))
    name = sorted(key for key, frame in store.items() if not frame["exported"])[0]
    store[name]["exported"] = True
    store[name]["masks"] = [{"id": 0, "status": "accepted", "species": "PA", "reviewer": "AT"}]
    store_path.write_text(json.dumps(store, indent=1), encoding="utf-8")
    stem = name.rsplit(".", 1)[0]
    (project / "step4test_combinedAnnotate" / "all_labels" / f"{stem}.txt").write_text("0 0.1 0.1 0.2 0.1 0.2 0.2\n", encoding="utf-8")
    with open(project / pathmap.MANIFEST_REL, "a", encoding="utf-8", newline="") as handle:
        raw = project / "step4test_combinedAnnotate" / "routed_input" / "raw" / name
        handle.write(f"{stem},1,{stem}.txt,{name},exported_with_masks,,batch_002,{raw},1\n")
    return name, stem


def test_bring_in_copies_rewrites_and_claims(published):
    _folder, run = published
    result = bring(published)
    assert result["copied"] > 0
    project = local_project(run)
    record = projects.read_project(project)
    assert record["project_dir"] == str(project)
    store = json.loads((project / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    for frame in store.values():
        assert Path(frame["image_path_abs"]).is_file()
    assert (project / pathmap.DATA_YAML_REL).read_text(encoding="utf-8").startswith(f"path: {project}{os.sep}")
    assert record["access_log"][-1]["action"] == "bring_in"
    state = server_state(published)
    assert state["out"]["computer"] == COMPUTER and state["out"]["initials"] == "AT"
    assert state["history"][-1]["action"] == "bring_in"


def test_server_copy_keeps_mainframe_paths_after_bring_in(published):
    bring(published)
    store = json.loads((server_project(published) / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    assert all(frame["image_path_abs"].startswith("/mnt/rip/fake/") for frame in store.values())


def test_bring_in_rebuilds_image_links_for_exported_frames(published):
    _folder, run = published
    bring(published)
    images = local_project(run) / "step4test_combinedAnnotate" / "all_images"
    assert len(os.listdir(images)) == 2
    for name in os.listdir(images):
        assert (images / name).read_bytes().startswith(b"\xff\xd8")


def test_big_tables_and_backups_stay_on_the_mainframe(published):
    project = server_project(published)
    assert not (project / "step2_recodeSpecies" / "all_points_recoded.csv").exists()
    assert not (project / "step4test_combinedAnnotate" / "previews").exists()
    assert not (project / "step4test_combinedAnnotate" / "_superseded").exists()
    assert not (project / "step4test_combinedAnnotate" / "all_images").exists()
    assert not (project / "step1_makeAllPoints").exists()


def test_a_fresh_copy_has_nothing_to_send(published):
    _folder, run = published
    bring(published)
    state = transfer.read_local_state(local_project(run))
    assert transfer.changed_files(local_project(run), state) == []


def test_opening_alone_does_not_count_as_a_change(published):
    _folder, run = published
    bring(published)
    projects.stamp_access(local_project(run), "AT", "open", "x")
    (local_project(run) / "step4test_combinedAnnotate" / "config_log.json").write_text("{}", encoding="utf-8")
    assert transfer.changed_files(local_project(run), transfer.read_local_state(local_project(run))) == []


def test_put_back_sends_only_the_changes_with_mainframe_paths(published):
    folder, run = published
    bring(published)
    _name, stem = label_a_frame(run)
    state = transfer.read_local_state(local_project(run))
    assert len(transfer.changed_files(local_project(run), state)) == 3
    result = transfer.put_back(str(folder), run, "AT", COMPUTER)
    assert result["conflict"] is False and result["rev"] == 2
    assert result["changed"] == 4  # the three changed files and project.json's access log
    project = server_project(published)
    assert (project / "step4test_combinedAnnotate" / "all_labels" / f"{stem}.txt").is_file()
    store = json.loads((project / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    assert all(frame["image_path_abs"].startswith("/mnt/rip/fake/") for frame in store.values())
    assert sum(1 for frame in store.values() if frame["exported"]) == 3
    assert "/mnt/rip/fake/" in (project / pathmap.MANIFEST_REL).read_text(encoding="utf-8")
    assert str(paths.projects_dir()) not in (project / pathmap.MANIFEST_REL).read_text(encoding="utf-8")
    record = json.loads((project / "project.json").read_text(encoding="utf-8"))
    assert record["project_dir"] == f"/mnt/rip/fake/inprocess/{run}"
    assert [entry["action"] for entry in record["access_log"]][-2:] == ["bring_in", "put_back"]
    state = server_state(published)
    assert state["out"] is None and state["rev"] == 2
    assert state["history"][-1]["frames_left"] == 3 and state["history"][-1]["masks"] == 3


def test_put_back_keeps_a_copy_of_each_replaced_file(published):
    folder, run = published
    bring(published)
    label_a_frame(run)
    before = (server_project(published) / pathmap.SEGMENTATIONS_REL).read_bytes()
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    backups = list((server_project(published) / "_superseded").glob(f"*/before_put_back/{pathmap.SEGMENTATIONS_REL}"))
    assert len(backups) == 1 and backups[0].read_bytes() == before


def test_checksums_stay_true_after_put_back(published):
    folder, run = published
    bring(published)
    label_a_frame(run)
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    listing = transfer.read_checksums(server_project(published))
    from vega_annotator.atomic import sha256_file
    for rel, entry in listing.items():
        path = server_project(published) / rel
        assert path.stat().st_size == entry["size"], rel
        assert sha256_file(path) == entry["sha256"], rel


def test_second_put_back_sends_nothing_new(published):
    folder, run = published
    bring(published)
    label_a_frame(run)
    transfer.put_back(str(folder), run, "AT", COMPUTER, keep_holding=True)
    state = transfer.read_local_state(local_project(run))
    assert transfer.changed_files(local_project(run), state) == []
    assert server_state(published)["out"]["computer"] == COMPUTER


def test_bringing_in_again_after_put_back_copies_nothing(published):
    folder, run = published
    bring(published)
    label_a_frame(run)
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    assert bring(published)["copied"] == 0
    assert server_state(published)["out"]["computer"] == COMPUTER


def test_other_computer_gets_the_latest_work(published, tmp_path):
    folder, run = published
    bring(published)
    name, _stem = label_a_frame(run)
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    other_root = tmp_path / "legion" / "projects"
    transfer.bring_in(str(folder), run, "AT", OTHER, local_root=other_root)
    store = json.loads((other_root / run / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    assert store[name]["exported"] is True
    assert store[name]["image_path_abs"].startswith(str(other_root))
    assert Path(store[name]["image_path_abs"]).is_file()


def test_first_computer_updates_after_the_other_put_back(published, tmp_path):
    folder, run = published
    bring(published)
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    other_root = tmp_path / "legion" / "projects"
    transfer.bring_in(str(folder), run, "AT", OTHER, local_root=other_root)
    store_path = other_root / run / pathmap.SEGMENTATIONS_REL
    store = json.loads(store_path.read_text(encoding="utf-8"))
    first = sorted(store)[3]
    store[first]["exported"] = True
    store_path.write_text(json.dumps(store, indent=1), encoding="utf-8")
    transfer.put_back(str(folder), run, "AT", OTHER, local_root=other_root)
    result = bring(published)
    assert 0 < result["copied"] < 10
    mine = json.loads((local_project(run) / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    assert mine[first]["exported"] is True
    assert mine[first]["image_path_abs"].startswith(str(paths.projects_dir()))


def test_rename_after_bring_in_keeps_the_copy_clean(published):
    _folder, run = published
    bring(published)
    state = transfer.read_local_state(local_project(run))
    assert state["holding"] is True and state["server_rev"] == 1


def test_remove_local_after_put_back_and_release(published):
    folder, run = published
    bring(published)
    transfer.put_back(str(folder), run, "AT", COMPUTER)
    result = transfer.remove_local(run, COMPUTER, str(folder))
    assert result["removed"] is True
    assert not local_project(run).exists()
    assert server_state(published)["out"] is None
