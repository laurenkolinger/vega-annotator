"""Run the real annotator process with a stand-in for SAM3, for tests with no weights.

Everything except the model is real: VEGA's app.py, the laptop layer, the file formats.
A click gives back a disk around the click.
"""

import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from vega_annotator import annotator_boot, paths, shims  # noqa: E402

DISK_RADIUS_PX = 60


class FakeEngine:
    """The part of VEGA's SAM3Engine that its app.py calls."""

    def __init__(self, _config):
        self.mask_size = "generous"
        self._prompt_lock = threading.Lock()
        self._current_image = None
        self._current_image_path = None
        self._image_size = None

    def set_image(self, image_path):
        from PIL import Image
        self._current_image = Image.open(image_path).convert("RGB")
        self._current_image_path = image_path
        self._image_size = self._current_image.size
        return self._image_size

    def clear_image(self):
        self._current_image = self._current_image_path = self._image_size = None

    def _ensure_embeddings(self):
        return None

    def set_mask_size(self, size):
        self.mask_size = size

    def warm_exemplar_async(self):
        return None

    def segment_point(self, x, y, label=1):
        width, height = self._image_size
        ys, xs = np.ogrid[:height, :width]
        mask = (xs - x) ** 2 + (ys - y) ** 2 <= DISK_RADIUS_PX ** 2
        return {"mask": mask, "score": 0.97, "bbox": None}

    def refine_mask(self, clicks, labels, box=None):
        return self.segment_point(*clicks[0])

    def segment_box(self, x0, y0, x1, y1):
        return self.segment_point((x0 + x1) / 2, (y0 + y1) / 2)

    def exemplar_scan(self, bbox):
        return []

    def release(self):
        return None


def main():
    """Swap in the fake engine, then start the annotator the way the app does."""
    shims.install()
    sys.path.insert(0, str(paths.vendor_scripts()))
    sys.path.insert(0, str(paths.annotator_src()))
    import sam_engine
    sam_engine.SAM3Engine = FakeEngine
    if os.environ.get("VEGA_TEST_PLAIN_PAGE"):
        annotator_boot.LOCAL_PAGE_TAIL = ""
    return annotator_boot.main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
