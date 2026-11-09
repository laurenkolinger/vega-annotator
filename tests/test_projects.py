"""Tests for projects.py: VEGA's project records, counts, access log, and launch settings."""

import json

import pytest

import helpers
from vega_annotator import projects
from vega_annotator.device import DevicePlan

PLAN = DevicePlan("cpu:0", "cpu:0", "float32", 4, False, "label")
MODELS = {"hf_home": "/h", "cache_dir": "/c"}


def test_stamp_access_matches_the_launchers_shape(source_project):
    entry = projects.stamp_access(source_project, "AT", "open", "labeling corals")
    assert set(entry) == {"ts", "initials", "action", "purpose"}
    record = json.loads((source_project / "project.json").read_text(encoding="utf-8"))
    assert record["access_log"][-1] == entry
    assert record["updated_at"] == entry["ts"]
    assert entry["ts"].endswith("-04:00")
    assert projects.reviewer_for(record) == "AT"


def test_stamp_access_keeps_earlier_entries_and_survives_a_bad_log(source_project):
    path = source_project / "project.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    before = len(record["access_log"])
    projects.stamp_access(source_project, "AT", "open", "x")
    assert len(json.loads(path.read_text(encoding="utf-8"))["access_log"]) == before + 1
    record = json.loads(path.read_text(encoding="utf-8"))
    record["access_log"] = "not a list"
    path.write_text(json.dumps(record), encoding="utf-8")
    projects.stamp_access(source_project, "AT", "open", "x")
    assert len(json.loads(path.read_text(encoding="utf-8"))["access_log"]) == 1


def test_stamp_access_on_a_broken_record_raises_and_changes_nothing(source_project):
    path = source_project / "project.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        projects.stamp_access(source_project, "AT", "open", "x")
    assert path.read_text(encoding="utf-8") == "{broken"


def test_count_work_counts_frames_and_accepted_masks(source_project):
    work = projects.count_work(source_project)
    assert work["frames_total"] == 6 and work["frames_exported"] == 2 and work["frames_left"] == 4
    assert work["n_masks"] == 2 and work["n_images"] == 2 and work["species"] == {"PA": 2}
    assert work["reviewers"] == ["AT"]


def test_count_work_handles_scrapped_rejected_and_junk(tmp_path):
    project = helpers.make_project(tmp_path, frames=4, exported=0)
    path = project / "step4test_combinedAnnotate" / "segmentations" / "segmentations.json"
    store = json.loads(path.read_text(encoding="utf-8"))
    names = sorted(store)
    store[names[0]]["scrapped"] = True
    store[names[1]]["masks"] = [{"status": "rejected", "species": "PA"}, "junk", {"status": "accepted", "species": ""}]
    store["weird"] = "not a frame"
    path.write_text(json.dumps(store), encoding="utf-8")
    work = projects.count_work(project)
    assert work["frames_scrapped"] == 1 and work["frames_left"] == 3
    assert work["n_masks"] == 1 and work["species"] == {}


def test_a_project_never_opened_counts_its_routed_frames(tmp_path):
    project = helpers.make_project(tmp_path, frames=3, exported=0)
    (project / "step4test_combinedAnnotate" / "segmentations" / "segmentations.json").unlink()
    assert projects.count_work(project)["frames_left"] == 3


def test_target_codes_reads_each_stored_form():
    assert projects.target_codes({"steps": {"3": {"config": {"target_species": "OFRA, PA ,SS"}}}}) == ["OFRA", "PA", "SS"]
    assert projects.target_codes({"steps": {"3": {"config": {"target_species": ["PA"]}}}}) == ["PA"]
    assert projects.target_codes({}) == []


def test_describe_reports_a_card(source_project):
    card = projects.describe(source_project)
    assert card["project_name"] == "AT_PA_6img_26-11-02"
    assert card["target_labels"] == [{"code": "PA", "name": "Porites astreoides"}]
    assert card["completed_count"] == 3 and card["total_steps"] == 6
    assert card["steps"]["1"] == "completed" and card["steps"]["8"] == "locked"


def test_describe_reports_a_broken_project_by_name(tmp_path):
    folder = tmp_path / "run_bad"
    folder.mkdir()
    (folder / "project.json").write_text("{broken", encoding="utf-8")
    card = projects.describe(folder)
    assert card["run"] == "run_bad" and "not valid JSON" in card["error"]


def test_description_round_trip(source_project):
    assert projects.set_description(source_project, "  PA round two  ") == "PA round two"
    assert projects.read_project(source_project)["description"] == "PA round two"


def test_environment_follows_vegas_launch(source_project):
    env = projects.annotator_environment(source_project, PLAN, "http://127.0.0.1:5050", MODELS)
    assert env["TCRMP_TARGET_SPECIES"] == "PA"
    assert env["TCRMP_PROVENANCE_SOURCE"] == "step4test" and env["TCRMP_MANUAL_ANNOTATE"] == "1"
    assert env["TCRMP_REVIEW_BATCH_SIZE"] == "10"
    assert env["TCRMP_CONFIDENCE_THRESHOLD"] == "0.5" and env["TCRMP_MIN_MASK_AREA_PX"] == "500"
    assert env["TCRMP_SAM3_DEVICE_TRACKER"] == "cpu:0" and env["TCRMP_EXEMPLAR_WARMUP"] == "0"
    assert env["HF_HUB_OFFLINE"] == "1" and env["TCRMP_REVIEW_GIT_PUSH"] == "0"
    assert env["TCRMP_REVIEWER"] == "AT" and env["TCRMP_ORCHESTRATOR_URL"] == "http://127.0.0.1:5050"
    assert env["TCRMP_EXPORT_DIR"].endswith("/step4test_combinedAnnotate")
    assert all(isinstance(value, str) for value in env.values())


def test_environment_takes_the_panels_choices(source_project):
    env = projects.annotator_environment(source_project, PLAN, "u", MODELS, "all", target_only=False, reference=False)
    assert env["TCRMP_REVIEW_BATCH_SIZE"] == "all"
    assert env["TCRMP_TARGET_SPECIES_ONLY"] == "0" and env["TCRMP_REFERENCE_DEFAULT"] == "0"


def test_environment_refuses_a_project_with_no_targets(tmp_path):
    project = helpers.make_project(tmp_path, target="")
    with pytest.raises(ValueError):
        projects.annotator_environment(project, PLAN, "u", MODELS)


def folder_project(tmp_path, names=("healthy", "Partly bleached", "SCTLD (lesion)")):
    """Make a project from a folder of two images, in this test's projects folder."""
    from vega_annotator import folder_projects
    photos = tmp_path / "photos"
    helpers.write_image(photos / "a.jpg")
    helpers.write_image(photos / "b.png")
    root = tmp_path / "projects"
    run = folder_projects.create(photos, "Reef health", list(names), "LO", "x", root=root)["run"]
    return root / run


def test_environment_for_folder_project(tmp_path, source_project):
    project = folder_project(tmp_path)
    env = projects.annotator_environment(project, PLAN, "http://127.0.0.1:5050", MODELS)
    assert env["TCRMP_TARGET_SPECIES"] == "healthy,Partly bleached,SCTLD (lesion)"
    assert env["TCRMP_TARGET_SPECIES_ONLY"] == "0"
    assert env["TCRMP_MASTER_CODES"] == str(project / "step4test_combinedAnnotate" / "master_codes_recoded.csv")
    assert env[projects.LABEL_SET_ENV] == "custom" and projects.LABEL_SET_ENV == "VEGA_ANNOTATOR_LABEL_SET"
    assert env["TCRMP_PROJECT_ID"] == project.name[4:] and env["TCRMP_PROJECT_NAME"] == "Reef health"
    assert env["TCRMP_REVIEWER"] == "LO" and all(isinstance(value, str) for value in env.values())
    vega_env = projects.annotator_environment(source_project, PLAN, "u", MODELS)
    assert projects.LABEL_SET_ENV not in vega_env and vega_env["TCRMP_TARGET_SPECIES_ONLY"] == "1"
    assert vega_env["TCRMP_MASTER_CODES"].endswith("step2_recodeSpecies/master_codes_recoded.csv")


def test_open_in_annotator_reads_the_annotators_page(tmp_path, source_project):
    from vega_annotator import home
    assert projects.ANNOTATOR_PORTS == range(home.FIRST_ANNOTATOR_PORT, home.LAST_ANNOTATOR_PORT + 1)
    template = (helpers.ROOT / "vendor" / "vega" / "scripts" / "TCRMPclip_combinedAnnotate" / "src" /
                "templates" / "index.html").read_text(encoding="utf-8")
    assert "const RESUME_EXPORT_DIR = {{ default_export|tojson }};" in template
    project = folder_project(tmp_path)
    assert projects.open_in_annotator(project) is False
    with helpers.AnnotatorPage(project, projects.ANNOTATOR_PORTS):
        assert projects.open_in_annotator(project) is True
        assert projects.open_in_annotator(source_project) is False
    assert projects.open_in_annotator(project) is False


def test_count_work_counts_imports_the_store_lacks(tmp_path):
    project = folder_project(tmp_path)
    store_path = project / "step4test_combinedAnnotate" / "segmentations" / "segmentations.json"
    store = json.loads(store_path.read_text(encoding="utf-8"))
    del store["custom_b.jpg"]
    store_path.write_text(json.dumps(store, indent=1), encoding="utf-8")
    work = projects.count_work(project)
    assert work["frames_total"] == 2 and work["frames_left"] == 2
    (project / "step4test_combinedAnnotate" / "custom_imports" / "raw" / "custom_b.jpg").unlink()
    assert projects.count_work(project)["frames_total"] == 1
    manifest = project / "step4test_combinedAnnotate" / "custom_imports" / "imports_manifest.json"
    manifest.write_text("{broken", encoding="utf-8")
    assert projects.count_work(project)["frames_total"] == 1


def test_update_project_edits_the_record_under_the_lock(source_project):
    record = projects.update_project(source_project, lambda data: data.update(description="edited"))
    assert record["description"] == "edited" == projects.read_project(source_project)["description"]
    assert projects.has_custom_labels(record) is False
    assert projects.has_custom_labels({"label_set": {"kind": "custom", "labels": ["a"]}}) is True
    assert projects.has_custom_labels(None) is False
