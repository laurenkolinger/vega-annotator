"""The whole trip through VEGA's real annotator code: publish, bring in, label, export,
put back, and bring in on a second computer. Only SAM3 is a stand-in here;
test_annotator_real.py runs the real weights."""

import csv
import json
from pathlib import Path

import pytest

import helpers
from annotator_driver import Annotator
from vega_annotator import pathmap, paths, projects, transfer

FAKE = [str(Path(__file__).parent / "fake_annotator.py")]
COMPUTER = "Lab MacBook"
OTHER = "Legion"


@pytest.fixture
def setup(server_folder, tmp_path):
    source = helpers.make_project(tmp_path / "inprocess", frames=24, exported=2)
    helpers.publish_to(server_folder, source)
    transfer.bring_in(str(server_folder), source.name, "AT", COMPUTER)
    return server_folder, source.name, paths.projects_dir() / source.name


def server_csv(server_folder, run, rel):
    with open(server_folder / "projects" / run / rel, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def run_session(project, frames_to_label=1):
    """Open the annotator, label some frames, export one batch, and stop."""
    annotator = Annotator(project, FAKE)
    try:
        annotator.boot()
        batch = annotator.batch_frames()
        assert len(batch) == 10
        for name in batch[:frames_to_label]:
            annotator.label_frame(name)
        stats = annotator.export_batch()
        assert stats["ok"] is True and stats["exported_images"] == 10 and stats["exported_masks"] == frames_to_label
        left = annotator.get("/api/status")["review_total"]
        return batch, left, stats
    finally:
        annotator.stop()


def test_the_whole_trip_writes_vegas_formats(setup):
    server_folder, run, project = setup
    batch, left, _stats = run_session(project, frames_to_label=2)
    assert left == 12

    # On this computer: VEGA's code wrote its usual files, with the work credited to AT.
    step4 = project / "step4test_combinedAnnotate"
    assert len(list((step4 / "all_images").iterdir())) == 12
    assert (step4 / "vega_annotator_registry" / "manifest.csv").is_file()
    state = transfer.read_local_state(project)
    assert transfer.changed_files(project, state)

    result = transfer.put_back(str(server_folder), run, "AT", COMPUTER)
    assert result["conflict"] is False and result["changed"] > 10

    # On the server: the project reads as a VEGA project, with mainframe paths throughout.
    server_project = server_folder / "projects" / run
    store = json.loads((server_project / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    assert sum(1 for frame in store.values() if frame["exported"]) == 12
    assert all(frame["image_path_abs"].startswith("/mnt/rip/fake/") for frame in store.values())
    frame = store[batch[0]]
    mask = frame["masks"][0]
    assert mask["status"] == "accepted" and mask["species"] == "PA" and mask["reviewer"] == "AT"
    assert mask["source_type"] == "manual_click" and mask["polygon_norm"]

    stem = batch[0].rsplit(".", 1)[0]
    label = (server_project / "step4test_combinedAnnotate" / "all_labels" / f"{stem}.txt").read_bytes()
    assert b"\r" not in label
    fields = label.decode("ascii").split()
    assert fields[0] == "0" and len(fields) >= 7
    assert all(0.0 <= float(value) <= 1.0 for value in fields[1:])

    empty_stem = batch[5].rsplit(".", 1)[0]
    assert (server_project / "step4test_combinedAnnotate" / "all_labels" / f"{empty_stem}.txt").read_bytes() == b""

    manifest = server_csv(server_folder, run, pathmap.MANIFEST_REL)
    assert len(manifest) == 12
    row = next(item for item in manifest if item["basename"] == stem)
    assert row["outcome"] == "exported_with_masks" and row["n_masks"] == "1" and row["PA"] == "1"
    assert row["image_path"].startswith("/mnt/rip/fake/")
    assert next(item for item in manifest if item["basename"] == empty_stem)["outcome"] == "exported_empty"

    ledger = server_csv(server_folder, run, "step4test_combinedAnnotate/label_provenance.csv")
    outcome = {(item["basename"], item["label"]): item for item in ledger}
    assert outcome[(stem, "PA")]["outcome"] == "found_manual" and outcome[(stem, "PA")]["reviewer"] == "AT"
    assert outcome[(stem, "PA")]["project_id"] == run[4:] and outcome[(stem, "PA")]["source"] == "step4test"
    assert outcome[(empty_stem, "PA")]["outcome"] == "not_found"

    registry = server_csv(server_folder, run, "step4test_combinedAnnotate/vega_annotator_registry/manifest.csv")
    assert {item["basename"] for item in registry} == {batch[0].rsplit(".", 1)[0], batch[1].rsplit(".", 1)[0]}
    assert all(item["project_id"] == run[4:] and item["reviewer"] == "AT" for item in registry)

    data_yaml = (server_project / pathmap.DATA_YAML_REL).read_text(encoding="utf-8")
    assert data_yaml.startswith(f"path: /mnt/rip/fake/inprocess/{run}/") and "0: PA" in data_yaml
    class_map = json.loads((server_project / "step4test_combinedAnnotate" / "class_map.json").read_text(encoding="utf-8"))
    assert class_map == {"PA": 0}

    record = json.loads((server_project / "project.json").read_text(encoding="utf-8"))
    assert record["project_dir"] == f"/mnt/rip/fake/inprocess/{run}"
    assert record["access_log"][-1]["action"] == "put_back"
    assert record["steps"]["1"]["status"] == "completed" and record["steps"]["6"]["status"] == "pending"
    for rel in ("segmentations", "previews", "all_labels"):
        assert (server_project / "step4test_combinedAnnotate" / rel).is_dir()
    assert not (server_project / "step4test_combinedAnnotate" / "all_images").exists()


def test_second_computer_continues_where_the_first_stopped(setup, tmp_path):
    server_folder, run, project = setup
    first_batch, _left, _stats = run_session(project)
    transfer.put_back(str(server_folder), run, "AT", COMPUTER)

    # The Legion brings it in fresh. Its all_images links must be rebuilt, or VEGA's export
    # check stops the next batch, which is the failure found on 2 Nov 2026.
    legion = tmp_path / "legion" / "projects"
    transfer.bring_in(str(server_folder), run, "AT", OTHER, local_root=legion)
    second_batch, left, _stats = run_session(legion / run)
    assert left == 2
    assert not set(first_batch) & set(second_batch)
    transfer.put_back(str(server_folder), run, "AT", OTHER, local_root=legion)

    # And the first computer can pick the work back up.
    assert transfer.bring_in(str(server_folder), run, "AT", COMPUTER)["copied"] > 0
    assert projects.count_work(project)["frames_exported"] == 22
    final_batch, left, _stats = run_session_small(project)
    assert left == 0
    assert not set(final_batch) & (set(first_batch) | set(second_batch))


def run_session_small(project):
    """Export the last short batch, ending the queue, which runs VEGA's all-done check."""
    annotator = Annotator(project, FAKE)
    try:
        annotator.boot()
        batch = annotator.batch_frames()
        assert len(batch) == 2
        stats = annotator.export_batch()
        assert stats["ok"] is True
        return batch, annotator.get("/api/status")["review_total"], stats
    finally:
        annotator.stop()


def test_reopening_a_project_with_no_changes_marks_nothing_to_send(setup):
    _server_folder, _run, project = setup
    annotator = Annotator(project, FAKE)
    try:
        annotator.boot()
        annotator.get("/api/status")
    finally:
        annotator.stop()
    assert transfer.changed_files(project, transfer.read_local_state(project)) == []


def test_edits_survive_stopping_without_an_export(setup):
    _server_folder, _run, project = setup
    annotator = Annotator(project, FAKE)
    try:
        annotator.boot()
        name = annotator.batch_frames()[0]
        annotator.label_frame(name)
    finally:
        annotator.stop()
    store = json.loads((project / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    assert store[name]["masks"][0]["status"] == "accepted"
    assert store[name]["image_path_abs"].startswith(str(project))
    assert transfer.changed_files(project, transfer.read_local_state(project))


def test_unlabeled_accept_is_refused_as_in_vega(setup):
    _server_folder, _run, project = setup
    annotator = Annotator(project, FAKE)
    try:
        annotator.boot()
        name = annotator.batch_frames()[0]
        annotator.get(f"/api/image/{name}")
        mask = annotator.post(f"/api/image/{name}/add", {"click_x": 100, "click_y": 100, "species": ""})["mask"]
        answer = annotator.put(f"/api/image/{name}/masks", {"updates": {str(mask["id"]): "accepted"}})
        assert answer["needs_species"] == [mask["id"]]
    finally:
        annotator.stop()


def test_annotator_page_is_vegas_page_with_the_local_layer(setup):
    _server_folder, _run, project = setup
    annotator = Annotator(project, FAKE)
    try:
        import urllib.request
        with urllib.request.urlopen(annotator.base + "/", timeout=30) as response:
            page = response.read().decode("utf-8")
        assert "Annotate Images" in page and 'id="img-counter"' in page
        assert "fonts.googleapis.com" not in page and "/portable/annotator_extras.js" in page
        assert "Porites astreoides" in page
    finally:
        annotator.stop()


def test_real_annotator_sends_no_open_cross_origin_header(setup):
    _server_folder, _run, project = setup
    annotator = Annotator(project, FAKE)
    try:
        import urllib.request
        with urllib.request.urlopen(annotator.base + "/api/status", timeout=30) as response:
            assert "Access-Control-Allow-Origin" not in response.headers
    finally:
        annotator.stop()


def test_label_lines_match_vegas_own_writer(setup):
    """The label file holds what VEGA's own export function writes for the stored mask."""
    import sys
    server_folder, run, project = setup
    batch, _left, _stats = run_session(project)
    transfer.put_back(str(server_folder), run, "AT", COMPUTER)
    sys.path.insert(0, str(paths.vendor_scripts()))
    sys.path.insert(0, str(paths.annotator_src()))
    import export_yolo
    store = json.loads((server_folder / "projects" / run / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    mask = store[batch[0]]["masks"][0]
    expected = export_yolo.mask_to_yolo_line(export_yolo.join_polygons(mask["polygon_norm"]), 0) + "\n"
    stem = batch[0].rsplit(".", 1)[0]
    label = server_folder / "projects" / run / "step4test_combinedAnnotate" / "all_labels" / f"{stem}.txt"
    assert label.read_text(encoding="utf-8") == expected


def test_export_works_after_the_conflict_way_out(setup, tmp_path):
    """VEGA's own export check passes on this computer after it gives up its copy for the server's."""
    server_folder, run, project = setup
    run_session(project, frames_to_label=2)
    legion = tmp_path / "legion"
    transfer.bring_in(str(server_folder), run, "LO", OTHER, take_over=True, local_root=legion)
    run_session(legion / run, frames_to_label=1)
    transfer.put_back(str(server_folder), run, "LO", OTHER, local_root=legion)
    assert transfer.put_back(str(server_folder), run, "AT", COMPUTER)["conflict"] is True
    transfer.bring_in(str(server_folder), run, "AT", COMPUTER)
    images = project / "step4test_combinedAnnotate" / "all_images"
    rows = [row for row in csv.DictReader(open(project / pathmap.MANIFEST_REL, newline="", encoding="utf-8"))
            if row["outcome"].startswith("exported")]
    assert sorted(entry.name for entry in images.iterdir()) == sorted(row["image_file"] for row in rows)
    run_session(project, frames_to_label=1)
    assert transfer.put_back(str(server_folder), run, "AT", COMPUTER)["conflict"] is False
