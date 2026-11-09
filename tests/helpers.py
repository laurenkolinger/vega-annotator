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


# Imports for the folder project helpers below.
import csv  # noqa: E402
import http.server  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402

from collections import Counter  # noqa: E402

import pytest  # noqa: E402

MANIFEST_FIELDS = ["basename", "n_masks", "label_file", "image_file", "outcome", "preview_file",
                   "batch_name", "image_path"]  # VEGA's EXPORT_MANIFEST_FIELDS


def make_bare_project(project, name="Reef health", initials="LO"):
    """Write the record of a project made from a folder, before any label or image.

    Parameters:
        project: the run_* folder to make.
        name: the project's name.
        initials: who made it.
    Returns:
        Path: the project folder.
    """
    project = Path(project)
    (project / "step4test_combinedAnnotate" / "routed_input" / "ids").mkdir(parents=True)
    (project / "step4test_combinedAnnotate" / "routed_input" / "raw").mkdir()
    (project / "step4test_combinedAnnotate" / "routed_input" / "ids" / "sam_click_prompts.json").write_text(
        "{}", encoding="utf-8")
    stamp = "2026-11-09T09:00:00-04:00"
    record = {"id": project.name[4:], "name": name, "description": "", "initials": initials,
              "created_at": stamp, "updated_at": stamp,
              "access_log": [{"ts": stamp, "initials": initials, "action": "create", "purpose": "testing"}],
              "steps": {"1": {"status": "skipped", "name": "Make All Points"},
                        "2": {"status": "skipped", "name": "Recode Species"},
                        "3": {"status": "skipped", "name": "Choose Images"}}}
    (project / "project.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return project


def write_image(path, size=(64, 48), color=(40, 120, 160)):
    """Write a small real image, in the format its suffix names.

    Parameters:
        path: the file to write; its folder is made when missing.
        size: width and height in pixels.
        color: the RGB fill, so two images can differ.
    Returns:
        Path: the file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def write_heic(path, size=(64, 48), color=(200, 60, 60)):
    """Write a small HEIC image with macOS sips, which Pillow cannot write.

    Parameters:
        path: the .heic file to write.
        size: width and height in pixels.
        color: the RGB fill.
    Returns:
        Path: the file.
    """
    path = Path(path)
    source = write_image(path.with_name(path.stem + "_source.png"), size, color)
    subprocess.run(["sips", "-s", "format", "heic", str(source), "--out", str(path)],
                   capture_output=True, check=True)
    source.unlink()
    return path


class AnnotatorPage:
    """Serve the line of VEGA's annotator page that names its project, on an annotator port.

    Used as a context manager: while it runs, the app reads the project as open in the annotator.
    """

    def __init__(self, project_dir, ports):
        """Start serving on the first free port.

        Parameters:
            project_dir: the run_* folder the page names.
            ports: the ports the home screen starts its annotator on.
        """
        export_dir = (Path(project_dir) / "step4test_combinedAnnotate").as_posix()
        body = f"<html><script>const RESUME_EXPORT_DIR = {json.dumps(export_dir)};</script></html>".encode()

        class Handler(http.server.BaseHTTPRequestHandler):
            """Answer every GET with the page."""

            def do_GET(self):
                """Send the page."""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                """Keep the test output quiet."""

        self.server = None
        for port in ports:
            try:
                self.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
                break
            except OSError:
                continue
        if self.server is None:
            pytest.skip("Every annotator port is in use on this computer.")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def __enter__(self):
        """Return the running page."""
        return self

    def __exit__(self, *_exc):
        """Stop serving."""
        self.server.shutdown()
        self.server.server_close()


def export_like_vega(project, labeled, batch="batch_001_20261109-153000"):
    """Write what VEGA's annotator leaves after exporting one batch of a folder project.

    The files follow VEGA's export: all_images/ links to the working JPEGs, a label file per
    frame with one line per accepted mask, export_manifest.csv with a count column per label,
    data.yaml, export_log.txt, label_provenance.csv, mask_status_log.jsonl, and the previews.

    Parameters:
        project: the run_* folder.
        labeled: {frame name: [the label of each accepted mask]} for the frames the batch
            exports; a frame with no label exports as an empty frame.
        batch: the batch name the manifest records.
    Returns:
        Path: the project's Place + Segment folder.
    """
    step4 = Path(project) / "step4test_combinedAnnotate"
    store_path = step4 / "segmentations" / "segmentations.json"
    store = json.loads(store_path.read_text(encoding="utf-8"))
    class_map = json.loads((step4 / "class_map.json").read_text(encoding="utf-8"))
    for folder in ("all_images", "all_labels", "previews", "segmentations/overlays_reviewed"):
        (step4 / folder).mkdir(parents=True, exist_ok=True)
    manifest = step4 / "export_manifest.csv"
    rows = {}
    if manifest.is_file():
        with open(manifest, newline="", encoding="utf-8") as handle:
            rows = {row["basename"]: row for row in csv.DictReader(handle)}
    provenance, status_log = [], []
    for name, names in labeled.items():
        frame, stem = store[name], name.rsplit(".", 1)[0]
        frame["exported"] = True
        frame["masks"] = [{"id": index, "label": "A", "species": label, "category": "Label", "status": "accepted",
                           "reviewer": "LO", "source_type": "manual_click", "area": 400, "bbox": [6, 4, 26, 20],
                           "polygon_px": [[6, 4, 32, 4, 32, 24]], "polygon_norm": [[0.1, 0.1, 0.5, 0.1, 0.5, 0.5]],
                           "score": 0.97} for index, label in enumerate(names)]
        lines = [f"{class_map[label]} 0.100000 0.100000 0.500000 0.100000 0.500000 0.500000" for label in names]
        (step4 / "all_labels" / f"{stem}.txt").write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
        os.symlink(os.path.abspath(frame["image_path_abs"]), step4 / "all_images" / name)
        overlay = write_image(step4 / "segmentations" / "overlays_reviewed" / f"{stem}_seg.jpg", color=(200, 0, 0))
        shutil.copyfile(overlay, step4 / "previews" / f"{stem}_seg.jpg")
        rows[stem] = {"basename": stem, "n_masks": len(names), "label_file": f"{stem}.txt", "image_file": name,
                      "outcome": "exported_with_masks" if names else "exported_empty",
                      "preview_file": f"previews/{stem}_seg.jpg", "batch_name": batch,
                      "image_path": frame["image_path_abs"], **Counter(names)}
        provenance.extend(f"{stem},{label},found,step4test,LO,2026-11-09T15:30:00-04:00,{Path(project).name[4:]}\n"
                          for label in sorted(set(names)))
        status_log.extend(json.dumps({"filename": name, "mask_id": index, "new": "accepted", "species": label}) + "\n"
                          for index, label in enumerate(names))
    store_path.write_text(json.dumps(store, indent=1), encoding="utf-8")
    counts = sorted({key for row in rows.values() for key in row} - set(MANIFEST_FIELDS))
    with open(manifest, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS + counts)
        writer.writeheader()
        for stem in sorted(rows):
            writer.writerow({**{key: rows[stem].get(key, "") for key in MANIFEST_FIELDS},
                             **{label: rows[stem].get(label, 0) for label in counts}})
    ordered = sorted(class_map.items(), key=lambda item: item[1])
    (step4 / "data.yaml").write_text("\n".join([f"path: {os.path.abspath(step4)}", "train: all_images",
                                                "val: all_images", f"nc: {len(ordered)}", "names:"]
                                               + [f"  {cid}: {label}" for label, cid in ordered]) + "\n",
                                     encoding="utf-8")
    with open(step4 / "export_log.txt", "a", encoding="utf-8") as handle:
        handle.write(f"2026-11-09T15:30:00 exported {len(labeled)} images\n")
    ledger = step4 / "label_provenance.csv"
    if not ledger.is_file():
        ledger.write_text("basename,label,outcome,source,reviewer,at,project_id\n", encoding="utf-8")
    with open(ledger, "a", encoding="utf-8") as handle:
        handle.writelines(provenance)
    with open(step4 / "mask_status_log.jsonl", "a", encoding="utf-8") as handle:
        handle.writelines(status_log)
    return step4

