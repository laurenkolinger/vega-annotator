"""The real SAM3 weights through the real annotator, on this computer's CPU and GPU.

Skipped unless VEGA_TEST_HF_HOME names a Hugging Face folder holding facebook/sam3.
"""

import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

import helpers
from annotator_driver import CPU_PLAN, Annotator
from vega_annotator import pathmap, paths, transfer
from vega_annotator.device import DevicePlan

HF_HOME = os.environ.get("VEGA_TEST_HF_HOME", "")
ROOT = Path(__file__).resolve().parent.parent
BOOT = ["-m", "vega_annotator.annotator_boot"]
MPS_PLAN = DevicePlan("mps:0", "cpu:0", "bfloat16", 4, False, "test")

pytestmark = [pytest.mark.real_sam,
              pytest.mark.skipif(not HF_HOME, reason="VEGA_TEST_HF_HOME is not set")]


def make_coral_frame(path):
    """Overwrite a frame with one bright disk, so a click has a clear object."""
    import numpy as np
    from PIL import Image

    width, height = helpers.FRAME_SIZE
    rng = np.random.default_rng(1)
    frame = rng.integers(20, 70, size=(height, width, 3), dtype=np.uint8)
    ys, xs = np.ogrid[:height, :width]
    disk = (xs - 320) ** 2 + (ys - 180) ** 2 <= 80 ** 2
    frame[disk] = (235, 200, 120)
    Image.fromarray(frame).save(path, quality=92)
    return disk


@pytest.mark.parametrize("plan", [CPU_PLAN, MPS_PLAN], ids=["cpu_float32", "mac_gpu_bfloat16"])
def test_real_click_gives_the_right_outline_and_exports(tmp_path, server_folder, plan):
    import torch
    if plan.tracker.startswith("mps") and not torch.backends.mps.is_available():
        pytest.skip("no Mac graphics chip here")
    source = helpers.make_project(tmp_path / "inprocess", frames=10, exported=0)
    helpers.publish_to(server_folder, source)
    transfer.bring_in(str(server_folder), source.name, "AT", "Lab MacBook")
    project = paths.projects_dir() / source.name
    raw = project / "step4test_combinedAnnotate" / "routed_input" / "raw"
    name = helpers.frame_name(0)
    disk = make_coral_frame(raw / name)
    annotator = Annotator(project, BOOT, plan=plan, hf_home=HF_HOME, cache_dir=str(tmp_path / "cache"))
    try:
        annotator.boot()
        mask = annotator.label_frame(name, x=320, y=180)
        assert mask["species"] == "PA"
        assert abs(mask["area"] - int(disk.sum())) / disk.sum() < 0.35, annotator.log_tail()
        stats = annotator.export_batch()
        assert stats["exported_masks"] == 1
        log = annotator.log_tail()
        assert "video_backfill" not in log or "SAM3 model loaded" in log
    finally:
        annotator.stop()
    stem = name.rsplit(".", 1)[0]
    label = (project / "step4test_combinedAnnotate" / "all_labels" / f"{stem}.txt").read_text(encoding="utf-8")
    assert label.startswith("0 ")


def make_two_disks(path):
    """Overwrite a frame with two matching bright disks, so Find similar has something to find."""
    import numpy as np
    from PIL import Image

    width, height = helpers.FRAME_SIZE
    rng = np.random.default_rng(2)
    frame = rng.integers(20, 70, size=(height, width, 3), dtype=np.uint8)
    ys, xs = np.ogrid[:height, :width]
    for center_x in (160, 480):
        frame[(xs - center_x) ** 2 + (ys - 180) ** 2 <= 70 ** 2] = (235, 200, 120)
    Image.fromarray(frame).save(path, quality=92)


@pytest.mark.parametrize("plan", [CPU_PLAN, MPS_PLAN], ids=["cpu_float32", "mac_gpu_bfloat16"])
def test_real_find_similar_runs_through_the_annotator(tmp_path, server_folder, plan):
    import torch
    if plan.tracker.startswith("mps") and not torch.backends.mps.is_available():
        pytest.skip("no Mac graphics chip here")
    source = helpers.make_project(tmp_path / "inprocess", frames=10, exported=0)
    helpers.publish_to(server_folder, source)
    transfer.bring_in(str(server_folder), source.name, "AT", "Lab MacBook")
    project = paths.projects_dir() / source.name
    name = helpers.frame_name(0)
    make_two_disks(project / "step4test_combinedAnnotate" / "routed_input" / "raw" / name)
    annotator = Annotator(project, BOOT, plan=plan, hf_home=HF_HOME, cache_dir=str(tmp_path / "cache"))
    try:
        annotator.boot()
        mask = annotator.label_frame(name, x=160, y=180)
        found = annotator.post(f"/api/image/{urllib.parse.quote(name)}/exemplar",
                               {"mode": "this", "mask_id": mask["id"], "threshold": 0.3})
        assert found["ok"] is True and found["count"] == len(found["new_masks"]), annotator.log_tail()
        assert "Traceback" not in annotator.log_tail()
        centers = [(candidate["bbox"][0] + candidate["bbox"][2]) / 2 for candidate in found["new_masks"]]
        assert any(abs(center - 480) < 60 for center in centers), (found["count"], centers)
    finally:
        annotator.stop()


def test_self_test_passes_on_this_computer(tmp_path):
    env = {**os.environ, "HF_HOME": HF_HOME, "HF_HUB_OFFLINE": "1", "TCRMP_SAM3_CACHE_DIR": str(tmp_path),
           "TCRMP_EXEMPLAR_WARMUP": "0", "PYTHONPATH": str(ROOT)}
    result = subprocess.run([sys.executable, "-m", "vega_annotator.self_test", "cpu:0", "cpu:0", "float32", "4"],
                            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=600, check=False)
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["ok"] is True, report
    assert report["iou"] >= 0.7 and report["encode_s"] < 60 and report["dtype"] == "float32"


def disk_photo(path, center, form):
    """Write a 640 by 360 photo holding one bright disk, in the format form names, and return the disk's area."""
    import numpy as np
    from PIL import Image

    width, height = helpers.FRAME_SIZE
    rng = np.random.default_rng(len(path.name))
    frame = rng.integers(20, 70, size=(height, width, 3), dtype=np.uint8)
    ys, xs = np.ogrid[:height, :width]
    disk = (xs - center[0]) ** 2 + (ys - center[1]) ** 2 <= 70 ** 2
    frame[disk] = (235, 200, 120)
    Image.fromarray(frame).save(path, form)
    return int(disk.sum())


def test_real_new_project_with_custom_labels_exports_for_vega(tmp_path, monkeypatch):
    """NEW PROJECT from a folder of JPEG and PNG photos with two typed labels, one real SAM3 click
    per label through VEGA's annotator routes, a batch export, then EXPORT FOR VEGA."""
    import hashlib
    import time

    import yaml

    from vega_annotator import home, package

    photos = tmp_path / "Student photos"
    photos.mkdir()
    areas = {"custom_IMG_1.jpg": disk_photo(photos / "IMG_1.jpg", (320, 180), "JPEG"),
             "custom_IMG_2.jpg": disk_photo(photos / "IMG_2.png", (300, 170), "PNG")}
    typed = ["healthy", "Partly bleached"]
    monkeypatch.setattr(home, "jobs", home.JobBoard())
    client = home.app.test_client()
    answer = client.post("/api/projects/new", base_url="http://127.0.0.1:5050", content_type="application/json",
                         data=json.dumps({"folder": str(photos), "name": "Real SAM3 check", "labels": ", ".join(typed),
                                          "initials": "jm", "purpose": "the real SAM3 check"})).get_json()
    for _attempt in range(600):
        job = client.get(f"/api/jobs/{answer['job']}", base_url="http://127.0.0.1:5050").get_json()["job"]
        if job["status"] != "running":
            break
        time.sleep(0.1)
    assert job["status"] == "done" and job["result"]["added"] == 2 and job["result"]["skipped_count"] == 0, job
    project = paths.projects_dir() / answer["run"]
    annotator = Annotator(project, BOOT, plan=CPU_PLAN, hf_home=HF_HOME, cache_dir=str(tmp_path / "cache"))
    try:
        annotator.boot()
        frames = sorted(annotator.batch_frames())
        assert frames == ["custom_IMG_1.jpg", "custom_IMG_2.jpg"]
        chosen = dict(zip(frames, typed))
        for name, label in chosen.items():
            mask = annotator.label_frame(name, x=310, y=175, species=label, category="Label")
            assert mask["species"] == label, annotator.log_tail()
            assert abs(mask["area"] - areas[name]) / areas[name] < 0.35, (name, mask["area"], areas[name])
        stats = annotator.export_batch()
        assert stats["exported_masks"] == 2, stats
    finally:
        annotator.stop()
    usb = tmp_path / "USB stick"
    usb.mkdir()
    written = package.export_for_vega(project, usb)
    folder = Path(written["folder"])
    assert written["frames"] == 2 and written["masks"] == 2
    data = yaml.safe_load((folder / "data.yaml").read_text(encoding="utf-8"))
    assert data["names"] == {0: "healthy", 1: "Partly bleached"} and data["nc"] == 2
    for name, label in chosen.items():
        lines = [line for line in (folder / "all_labels" / f"{Path(name).stem}.txt").read_text(encoding="utf-8").splitlines() if line.strip()]
        assert lines and {line.split()[0] for line in lines} == {str(typed.index(label))}, (name, lines)
        assert (folder / "all_images" / name).is_file() and not (folder / "all_images" / name).is_symlink()
    sums = json.loads((folder / "checksums.json").read_text(encoding="utf-8"))
    assert set(sums) == {path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()} - {"checksums.json"}
    for rel, entry in sums.items():
        content = (folder / rel).read_bytes()
        assert len(content) == entry["size"] and hashlib.sha256(content).hexdigest() == entry["sha256"], rel
