"""Start an annotator process and call its web routes the way VEGA's page does."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from vega_annotator import projects
from vega_annotator.annotator_boot import THREADS_ENV, TRACKER_DTYPE_ENV
from vega_annotator.device import DevicePlan

ROOT = Path(__file__).resolve().parent.parent
READY_TIMEOUT_S = 300
CPU_PLAN = DevicePlan("cpu:0", "cpu:0", "float32", 4, False, "test")


def free_port():
    """Return a free local port."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Annotator:
    """One running annotator process on one project."""

    def __init__(self, project, entry, plan=CPU_PLAN, hf_home="/nonexistent", cache_dir="/nonexistent",
                 log_path=None, batch=None):
        """Start the process and wait until its routes answer.

        Parameters:
            project: the working copy's folder.
            entry: the command after the Python executable.
            plan: the device plan.
            hf_home: the Hugging Face folder.
            cache_dir: VEGA's tracker cache folder.
            log_path: where the output goes.
            batch: the review batch size.
        """
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        env = projects.annotator_environment(project, plan, "http://127.0.0.1:5050",
                                             {"hf_home": hf_home, "cache_dir": cache_dir}, batch)
        env[TRACKER_DTYPE_ENV] = plan.tracker_dtype
        env[THREADS_ENV] = str(plan.threads)
        self.log_path = log_path or (Path(project).parent / f"annotator_{self.port}.log")
        self.log_handle = open(self.log_path, "w", encoding="utf-8", errors="replace")
        self.process = subprocess.Popen([sys.executable, *entry, "--port", str(self.port)], cwd=str(ROOT),
                                        env={**os.environ, **env, "PYTHONPATH": str(ROOT)},
                                        stdout=self.log_handle, stderr=subprocess.STDOUT)
        deadline = time.time() + READY_TIMEOUT_S
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"annotator exited early: {self.log_tail()}")
            try:
                self.get("/api/status")
                return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError(f"annotator did not start: {self.log_tail()}")

    def log_tail(self):
        """Return the end of the process output."""
        self.log_handle.flush()
        return Path(self.log_path).read_text(encoding="utf-8", errors="replace")[-3000:]

    def _send(self, method, route, payload=None):
        """Send one request and return the parsed JSON answer."""
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.base + route, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))

    def get(self, route):
        """GET a route."""
        return self._send("GET", route)

    def post(self, route, payload=None):
        """POST a route."""
        return self._send("POST", route, payload if payload is not None else {})

    def put(self, route, payload):
        """PUT a route."""
        return self._send("PUT", route, payload)

    def boot(self):
        """Do what the page does when it opens on a project."""
        return self.post("/api/configure", {})

    def batch_frames(self):
        """Return the current batch's frame names."""
        return [image["filename"] for image in self.get("/api/review_batch")["images"]]

    def label_frame(self, name, x=200, y=150, species="PA", category="Coral"):
        """Click a frame and accept the mask with a label, as Enter does.

        Returns:
            dict: the new mask as the route returned it.
        """
        quoted = urllib.parse.quote(name)
        self.get(f"/api/image/{quoted}")
        answer = self.post(f"/api/image/{quoted}/add", {"click_x": x, "click_y": y, "species": species,
                                                        "category": category, "label": "A"})
        mask = answer["mask"]
        self.put(f"/api/image/{quoted}/masks", {"updates": {str(mask["id"]): "accepted"}})
        return mask

    def export_batch(self):
        """Export the current batch, as the end-of-batch dialog does."""
        return self.post("/api/export_batch", {})

    def flush(self):
        """Save now, as Ctrl+S does."""
        return self.post("/api/flush", {})

    def stop(self):
        """Stop the process the way the home screen does: save first, then terminate."""
        try:
            self.flush()
        except OSError:
            pass
        self.process.terminate()
        try:
            self.process.wait(20)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(10)
        self.log_handle.close()
