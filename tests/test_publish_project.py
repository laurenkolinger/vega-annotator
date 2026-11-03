"""Tests for tools/publish_project.py, the tool that runs on the mainframe."""

import json

import pytest

import helpers

tool = helpers.load_tool("publish_project")
PUBLISH = dict(repo="/nonexistent", proc_root="/nonexistent")


def test_publish_copies_the_right_files_and_leaves_the_source_alone(tmp_path, source_project):
    before = {path: path.stat().st_mtime_ns for path in source_project.rglob("*") if path.is_file()}
    result = tool.publish(str(source_project), str(tmp_path / "dest"), "LO", **PUBLISH)
    assert result["rev"] == 1
    target = tmp_path / "dest" / source_project.name
    assert (target / "project.json").is_file()
    assert (target / "step2_recodeSpecies" / "master_codes_recoded.csv").is_file()
    assert (target / "step4test_combinedAnnotate" / "routed_input" / "raw").is_dir()
    assert not (target / "step2_recodeSpecies" / "all_points_recoded.csv").exists()
    assert not (target / "step4test_combinedAnnotate" / "all_images").exists()
    assert not (target / "step4test_combinedAnnotate" / "previews").exists()
    assert not (target / "step4test_combinedAnnotate" / "_superseded").exists()
    assert before == {path: path.stat().st_mtime_ns for path in source_project.rglob("*") if path.is_file()}


def test_the_records_describe_the_copy(tmp_path, source_project):
    tool.publish(str(source_project), str(tmp_path / "dest"), "LO", **PUBLISH)
    target = tmp_path / "dest" / source_project.name
    state = json.loads((target / tool.SERVER_STATE).read_text(encoding="utf-8"))
    assert state["format"] == 1 and state["out"] is None and state["rev"] == 1
    assert state["source"]["project_dir"] == str(source_project)
    assert state["source"]["published_by"] == "LO"
    assert len(state["frame_hashes"]) == 6
    assert "step4test_combinedAnnotate/previews/" in state["left_on_mainframe"]
    listing = json.loads((target / tool.CHECKSUMS).read_text(encoding="utf-8"))
    assert "project.json" in listing and tool.SERVER_STATE not in listing
    log = (tmp_path / "dest" / "publish_log.csv").read_text(encoding="utf-8")
    assert source_project.name in log and "LO" in log


def test_frame_hash_ignores_the_machine_path_only():
    frame = {"image_path_abs": "/a", "masks": [], "exported": False}
    assert tool.frame_hash(frame) == tool.frame_hash({**frame, "image_path_abs": "/b"})
    assert tool.frame_hash(frame) != tool.frame_hash({**frame, "exported": True})


def test_dry_run_writes_nothing(tmp_path, source_project):
    result = tool.publish(str(source_project), str(tmp_path / "dest"), "LO", dry_run=True, **PUBLISH)
    assert result["dry_run"] is True and result["files"] > 0
    assert not (tmp_path / "dest").exists()


def test_publishing_twice_needs_update(tmp_path, source_project):
    dest = str(tmp_path / "dest")
    tool.publish(str(source_project), dest, "LO", **PUBLISH)
    with pytest.raises(tool.PublishError, match="already in the server folder"):
        tool.publish(str(source_project), dest, "LO", **PUBLISH)
    result = tool.publish(str(source_project), dest, "LO", update=True, **PUBLISH)
    assert result["rev"] == 2
    assert list((tmp_path / "dest" / "_superseded").glob(f"*/{source_project.name}"))


def test_update_refuses_a_project_in_use(tmp_path, source_project):
    dest = tmp_path / "dest"
    tool.publish(str(source_project), str(dest), "LO", **PUBLISH)
    path = dest / source_project.name / tool.SERVER_STATE
    state = json.loads(path.read_text(encoding="utf-8"))
    state["out"] = {"computer": "Legion", "initials": "AT", "at": "now"}
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(tool.PublishError, match="in use on Legion"):
        tool.publish(str(source_project), str(dest), "LO", update=True, **PUBLISH)


def test_update_refuses_to_replace_laptop_work_vega_has_not_taken_back(tmp_path, source_project):
    dest = tmp_path / "dest"
    tool.publish(str(source_project), str(dest), "LO", **PUBLISH)
    path = dest / source_project.name / tool.SERVER_STATE
    state = json.loads(path.read_text(encoding="utf-8"))
    state["history"].append({"at": "now", "action": "put_back", "computer": "Legion", "initials": "AT"})
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(tool.PublishError, match="has not taken back"):
        tool.publish(str(source_project), str(dest), "LO", update=True, **PUBLISH)
    result = tool.publish(str(source_project), str(dest), "LO", update=True, replace_laptop_work=True, **PUBLISH)
    assert result["rev"] == 2
    assert list((dest / "_superseded").glob(f"*/{source_project.name}/{tool.SERVER_STATE}"))


def test_records_are_readable_by_the_servers_sync(tmp_path, source_project):
    tool.publish(str(source_project), str(tmp_path / "dest"), "LO", **PUBLISH)
    target = tmp_path / "dest" / source_project.name
    for name in (tool.SERVER_STATE, tool.CHECKSUMS):
        assert (target / name).stat().st_mode & 0o044 == 0o044, name


def test_a_running_annotator_stops_the_publish_unless_forced(tmp_path, source_project):
    proc = tmp_path / "proc" / "4242"
    proc.mkdir(parents=True)
    export = (source_project / "step4test_combinedAnnotate").resolve()
    (proc / "environ").write_bytes(b"A=1\0TCRMP_EXPORT_DIR=" + str(export).encode() + b"\0")
    (proc / "cmdline").write_bytes(b"python\0src/app.py\0--port\0005080\0")
    (tmp_path / "proc" / "not_a_pid").mkdir()
    dest = str(tmp_path / "dest")
    with pytest.raises(tool.PublishError, match="process 4242"):
        tool.publish(str(source_project), dest, "LO", repo="/nonexistent", proc_root=str(tmp_path / "proc"))
    assert tool.publish(str(source_project), dest, "LO", force=True, repo="/nonexistent", proc_root=str(tmp_path / "proc"))["rev"] == 1


@pytest.mark.parametrize("breakage,message", [
    (lambda project: (project / "project.json").write_text("{broken", encoding="utf-8"), "cannot be read"),
    (lambda project: (project / "project.json").unlink(), "cannot be read"),
    (lambda project: (project / "project.json").write_text("[1]", encoding="utf-8"), "does not hold"),
    (lambda project: (project / "step4test_combinedAnnotate/segmentations/segmentations.json").write_text("{broken", encoding="utf-8"), "cannot be read"),
    (lambda project: (project / "step4test_combinedAnnotate/segmentations/segmentations.json").write_text("[]", encoding="utf-8"), "does not hold frames"),
])
def test_broken_projects_are_refused(tmp_path, source_project, breakage, message):
    breakage(source_project)
    with pytest.raises(tool.PublishError, match=message):
        tool.publish(str(source_project), str(tmp_path / "dest"), "LO", **PUBLISH)
    assert not (tmp_path / "dest" / source_project.name).exists()


def test_no_targets_or_no_frames_are_refused(tmp_path):
    no_target = helpers.make_project(tmp_path / "a", target="")
    with pytest.raises(tool.PublishError, match="no target labels"):
        tool.publish(str(no_target), str(tmp_path / "dest"), "LO", **PUBLISH)
    no_frames = helpers.make_project(tmp_path / "b", frames=0, exported=0)
    with pytest.raises(tool.PublishError, match="no routed frames"):
        tool.publish(str(no_frames), str(tmp_path / "dest"), "LO", **PUBLISH)


def test_not_a_run_folder_is_refused(tmp_path):
    with pytest.raises(tool.PublishError, match="not a run_"):
        tool.publish(str(tmp_path), str(tmp_path / "dest"), "LO", **PUBLISH)


def test_a_failed_copy_leaves_no_half_project(tmp_path, source_project, monkeypatch):
    real = tool.copy_with_hash
    calls = []

    def fail_late(src, dest):
        calls.append(src)
        if len(calls) == 3:
            raise OSError("Disk went away")
        return real(src, dest)

    monkeypatch.setattr(tool, "copy_with_hash", fail_late)
    with pytest.raises(OSError):
        tool.publish(str(source_project), str(tmp_path / "dest"), "LO", **PUBLISH)
    assert [path.name for path in (tmp_path / "dest").iterdir()] == []


def test_symlinks_pointing_outside_are_left_out(tmp_path, source_project):
    outside = tmp_path / "secret.txt"
    outside.write_text("x", encoding="utf-8")
    (source_project / "step3_chooseImages" / "link.txt").symlink_to(outside)
    tool.publish(str(source_project), str(tmp_path / "dest"), "LO", **PUBLISH)
    assert not (tmp_path / "dest" / source_project.name / "step3_chooseImages" / "link.txt").exists()


def test_refresh_failure_keeps_the_existing_file_and_says_so(tmp_path, source_project):
    note = tool.refresh_prior_screening(str(source_project), str(tmp_path), str(tmp_path), repo="/nonexistent")
    assert "was not found" in note


def test_main_reports_failure_on_stderr(tmp_path, capsys):
    code = tool.main(["--project", str(tmp_path / "run_missing"), "--dest", str(tmp_path / "d"), "--by", "LO"])
    assert code == 1
    assert "cannot be read" in capsys.readouterr().err
