"""Run SAM3 once on this computer and report the speed.

The home screen's Test SAM3 button runs this as its own process. It uses VEGA's own
engine with this computer's device plan, on a generated picture with one bright disk,
so it needs no project. A click on the disk must give back an outline close to the
disk. An outline covering the whole frame is the signature of a model on random
weights (see the note in VEGA's README).

The last line of output is one JSON object.
"""

import json
import os
import sys
import tempfile
import time

from . import paths, shims

DISK_RADIUS_PX = 170
FRAME_SIZE = (1920, 1080)
MIN_IOU = 0.7


def make_test_frame(path):
    """Write the test picture and return the true disk mask.

    Parameters:
        path: where to save the JPEG.
    Returns:
        numpy.ndarray: a boolean array, True on the disk.
    """
    import numpy as np
    from PIL import Image

    width, height = FRAME_SIZE
    rng = np.random.default_rng(0)
    frame = rng.integers(20, 70, size=(height, width, 3), dtype=np.uint8)
    ys, xs = np.ogrid[:height, :width]
    disk = (xs - width // 2) ** 2 + (ys - height // 2) ** 2 <= DISK_RADIUS_PX ** 2
    frame[disk] = (235, 200, 120)
    Image.fromarray(frame).save(path, quality=92)
    return disk


def run(plan_tracker, plan_exemplar, dtype_name, threads):
    """Load the engine, encode the frame, and make one click.

    Parameters:
        plan_tracker: the tracker device string.
        plan_exemplar: the exemplar device string.
        dtype_name: "bfloat16" or "float32".
        threads: CPU threads, or 0 for PyTorch's default.
    Returns:
        dict: ok, load_s, encode_s, click_s, iou, tracker, dtype.
    """
    shims.install()
    import torch

    if threads:
        torch.set_num_threads(threads)
    sys.path.insert(0, str(paths.vendor_scripts()))
    sys.path.insert(0, str(paths.annotator_src()))
    import sam_engine

    from .annotator_boot import wrap_tracker_dtype

    wrap_tracker_dtype(sam_engine, getattr(torch, dtype_name))

    class Settings:
        """The settings VEGA's engine reads from its config module."""

        SAM3_DEVICE_TRACKER = plan_tracker
        SAM3_DEVICE_EXEMPLAR = plan_exemplar
        CONFIDENCE_THRESHOLD = 0.5
        SAM3_MASK_SIZE = "generous"

    def sync():
        """Wait for the Mac's graphics chip so the timings are real."""
        if plan_tracker.startswith("mps"):
            torch.mps.synchronize()

    start = time.perf_counter()
    engine = sam_engine.SAM3Engine(Settings)
    load_s = time.perf_counter() - start
    with tempfile.TemporaryDirectory() as folder:
        frame_path = os.path.join(folder, "test_frame.jpeg")
        disk = make_test_frame(frame_path)
        engine.set_image(frame_path)
        start = time.perf_counter()
        with engine._prompt_lock:
            engine._ensure_embeddings()
        sync()
        encode_s = time.perf_counter() - start
        # The first click on a device compiles its shaders, so the timed click is the third.
        for offset in (0, 20):
            engine.segment_point(FRAME_SIZE[0] // 2 + offset, FRAME_SIZE[1] // 2, label=1)
        start = time.perf_counter()
        result = engine.segment_point(FRAME_SIZE[0] // 2, FRAME_SIZE[1] // 2 + 20, label=1)
        sync()
        click_s = time.perf_counter() - start
    iou = 0.0
    if result is not None:
        mask = result["mask"]
        iou = float((mask & disk).sum()) / max(1, float((mask | disk).sum()))
    return {"ok": iou >= MIN_IOU, "load_s": round(load_s, 1), "encode_s": round(encode_s, 2),
            "click_s": round(click_s, 3), "iou": round(iou, 3), "tracker": plan_tracker,
            "dtype": dtype_name}


def main(argv=None):
    """Print one JSON result line, whether the test passes or fails.

    Parameters:
        argv: tracker device, exemplar device, dtype name, thread count.
    Returns:
        int: 0 when the outline matched, 1 otherwise.
    """
    argv = sys.argv[1:] if argv is None else argv
    try:
        report = run(argv[0], argv[1], argv[2], int(argv[3]))
    except Exception as exc:  # The whole point is to report any failure in one line.
        report = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(report), flush=True)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
