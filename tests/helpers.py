"""Shared test helpers: small, real-format VEGA projects and a fake SAM3 engine."""

import importlib.util
import json
import os
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MAINFRAME_ROOT = "/mnt/rip/fake/inprocess"
FRAME_SIZE = (640, 360)


def load_tool(name):
    """Import one of the single-file tools from tools/ by name."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def frame_name(index):
    """Return a TCRMP-style frame file name."""
    return f"TCRMP20240101_clip_BIT_T1{index:02d}.jpeg"


def make_project(root, run="run_26-11-02_AT", frames=6, exported=2, target="PA"):
    """Write a small VEGA project in the mainframe's layout.

    Parameters:
        root: the inprocess-like folder to write under.
        run: the run_* folder name.
        frames: how many frames the project holds.
        exported: how many of them are already exported, with one accepted mask each.
        target: the Step 3 target label string.
    Returns:
        Path: the project folder.
    """
    project = Path(root) / run
    step4 = project / "step4test_combinedAnnotate"
    raw = step4 / "routed_input" / "raw"
    ids = step4 / "routed_input" / "ids"
    for folder in (raw, ids, step4 / "segmentations", step4 / "all_labels", step4 / "all_images",
                   step4 / "previews", step4 / "_superseded", project / "step1_makeAllPoints",
                   project / "step2_recodeSpecies", project / "step3_chooseImages"):
        folder.mkdir(parents=True, exist_ok=True)
    (project / "step2_recodeSpecies" / "master_codes_recoded.csv").write_text(
        "code,category,name\nPA,Coral,Porites astreoides\nSS,Coral,Siderastrea siderea\n", encoding="utf-8")
    (project / "step2_recodeSpecies" / "all_points_recoded.csv").write_text("big,table\n", encoding="utf-8")
    (project / "step3_chooseImages" / "selected_frames.csv").write_text("basename\n", encoding="utf-8")
    server_project = f"{MAINFRAME_ROOT}/{run}"
    store, prompts = {}, {}
    manifest_rows = []
    for index in range(frames):
        name = frame_name(index)
        Image.new("RGB", FRAME_SIZE, (30 + index, 90, 120)).save(raw / name, quality=80)
        points = [{"label": "A", "species": "PA", "name": "Porites astreoides", "category": "Coral",
                   "x": 100.0 + index, "y": 80.0, "point_type": 1}]
        frame = {"image_path": f"raw/{name}",
                 "image_path_abs": f"{server_project}/step4test_combinedAnnotate/routed_input/raw/{name}",
                 "image_width": FRAME_SIZE[0], "image_height": FRAME_SIZE[1], "masks": [],
                 "reference_points": points, "processed_at": "2026-11-02T10:00:00", "reviewed": False,
                 "exported": False}
        if index < exported:
            frame["exported"] = True
            frame["masks"] = [{"id": 0, "label": "A", "species": "PA", "status": "accepted", "reviewer": "AT",
                               "source_type": "manual_click", "area": 100, "bbox": [0, 0, 10, 10],
                               "polygon_px": [[0, 0, 10, 0, 10, 10]], "polygon_norm": [[0, 0, 0.1, 0, 0.1, 0.1]],
                               "score": 0.9, "rle": {"size": [360, 640], "counts": "0"}}]
            stem = name.rsplit(".", 1)[0]
            (step4 / "all_labels" / f"{stem}.txt").write_text("0 0 0 0.1 0 0.1 0.1\n", encoding="utf-8")
            manifest_rows.append(f"{stem},1,{stem}.txt,{name},exported_with_masks,,batch_001,"
                                 f"{server_project}/step4test_combinedAnnotate/routed_input/raw/{name},1")
            os.symlink(f"../routed_input/raw/{name}", step4 / "all_images" / name)
        store[name] = frame
        prompts[name] = {"raw_image": f"raw/{name}", "points": points}
    (step4 / "segmentations" / "segmentations.json").write_text(json.dumps(store, indent=1), encoding="utf-8")
    (ids / "sam_click_prompts.json").write_text(json.dumps(prompts), encoding="utf-8")
    (step4 / "routed_input" / "config_log.json").write_text(
        json.dumps({"export_dir": f"{server_project}/step4test_combinedAnnotate/routed_input",
                    "clip_dir": "/mnt/rip/other/TCRMP_clip"}, indent=2) + "\n", encoding="utf-8")
    (step4 / "config_log.json").write_text(
        json.dumps({"input_dir": f"{server_project}/step4test_combinedAnnotate/routed_input",
                    "export_dir": f"{server_project}/step4test_combinedAnnotate"}, indent=2), encoding="utf-8")
    (step4 / "export_manifest.csv").write_text(
        "basename,n_masks,label_file,image_file,outcome,preview_file,batch_name,image_path,PA\n"
        + "".join(row + "\n" for row in manifest_rows), encoding="utf-8")
    (step4 / "data.yaml").write_text(
        f"path: {server_project}/step4test_combinedAnnotate\ntrain: all_images\nval: all_images\nnc: 1\n"
        "names:\n  0: PA\n", encoding="utf-8")
    (step4 / "class_map.json").write_text(json.dumps({"PA": 0}, indent=2), encoding="utf-8")
    (step4 / "label_provenance.csv").write_text("basename,label,outcome,source,reviewer,at,project_id\n", encoding="utf-8")
    (step4 / "previews" / "old_seg.jpg").write_bytes(b"preview")
    (step4 / "_superseded" / "old.json").write_text("{}", encoding="utf-8")
    record = {"id": run[4:], "name": f"AT_{target}_{frames}img_26-11-02", "created_at": "2026-11-02T09:00:00",
              "updated_at": "2026-11-02T09:00:00-04:00", "project_dir": server_project,
              "repo_dir": "/mnt/rip/fake/github_repo", "current_step": 4, "initials": "AT", "name_auto": True,
              "description": "", "access_log": [{"ts": "2026-11-02T09:00:00-04:00", "initials": "AT",
                                                 "action": "create", "purpose": "testing"}],
              "steps": {"1": {"status": "completed", "name": "Make All Points", "dir": f"{server_project}/step1_makeAllPoints"},
                        "2": {"status": "completed", "name": "Recode Species", "dir": f"{server_project}/step2_recodeSpecies"},
                        "3": {"status": "completed", "name": "Choose Images", "dir": f"{server_project}/step3_chooseImages",
                              "config": {"target_species": target, "all_points": f"{server_project}/step2_recodeSpecies/all_points_recoded.csv"}},
                        "4": {"status": "pending", "name": "Place Points", "dir": f"{server_project}/step4_routeChosenImages"},
                        "5": {"status": "locked", "name": "Segment Images (SAM3)", "dir": f"{server_project}/step5_segmentImages",
                              "config": {"confidence_threshold": 0.5, "min_mask_area_px": 500, "review_batch_size": "10"}},
                        "6": {"status": "pending", "name": "Train Model", "dir": f"{server_project}/step6_trainModel"},
                        "7": {"status": "locked", "name": "Evaluate Model", "dir": f"{server_project}/step7_evaluateModel"},
                        "8": {"status": "locked", "name": "Model Inference", "dir": f"{server_project}/step8_inference"}}}
    (project / "project.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return project


def publish_to(server_folder, project, by="LO"):
    """Publish a project with the real tool, recording mainframe paths in its record.

    Parameters:
        server_folder: the VEGA_Annotator folder.
        project: the project folder.
        by: initials of the person publishing.
    Returns:
        dict: the tool's result.
    """
    tool = load_tool("publish_project")
    dest = Path(server_folder) / "projects"
    result = tool.publish(str(project), str(dest), by, repo="/nonexistent", proc_root="/nonexistent")
    record_path = dest / project.name / "vega_annotator.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["source"]["project_dir"] = f"{MAINFRAME_ROOT}/{project.name}"
    record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return result
