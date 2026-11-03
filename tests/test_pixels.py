"""The annotator page here against VEGA's own page, in a real Chrome, at the two review widths.

Skipped without Chrome and node. VEGA's page is the vendored file with only its web fonts
served locally. The one thing this app adds to that page is the frames-left readout in the
top bar, so every other pixel must match.
"""

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import helpers
from annotator_driver import Annotator, free_port
from vega_annotator import transfer

ROOT = Path(__file__).resolve().parent.parent
FAKE = [str(Path(__file__).parent / "fake_annotator.py")]
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DRIVER = ROOT / "tests" / "e2e" / "page_shot.mjs"
WIDTHS = (1280, 946)
PAGE_HEIGHT = 900
TOP_BAR_ROWS = 48
MOST_PIXELS_CHANGED = 0.005
CHANNEL_TOLERANCE = 8
FEWEST_COLORS_ON_A_REAL_PAGE = 200
START_TIMEOUT_S = 30
SHOT_TIMEOUT_S = 120

pytestmark = pytest.mark.skipif(not os.path.exists(CHROME) or not shutil.which("node"), reason="needs Chrome and node")


def browser_address(port):
    """Wait for Chrome's debugging port and return the browser's WebSocket address."""
    deadline = time.time() + START_TIMEOUT_S
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as response:
                return json.loads(response.read().decode("utf-8"))["webSocketDebuggerUrl"]
        except OSError:
            time.sleep(0.2)
    raise AssertionError("Chrome did not start")


def shoot(browser, url, width, out):
    """Save a screenshot of the annotator page, and return it as an array and the readout text."""
    result = subprocess.run(["node", "--experimental-websocket", str(DRIVER), browser, url, str(width),
                             str(PAGE_HEIGHT), str(out)],
                            capture_output=True, text=True, timeout=SHOT_TIMEOUT_S, check=False)
    lines = result.stdout.strip().splitlines()
    report = json.loads(lines[-1]) if lines else {"ok": False, "error": result.stderr[-800:]}
    assert report["ok"] is True, report
    return np.asarray(Image.open(out).convert("RGB")).astype(int), report["left"]


def test_only_the_readout_differs_from_vegas_page(tmp_path, server_folder, monkeypatch):
    source = helpers.make_project(tmp_path / "inprocess", frames=24, exported=2)
    helpers.publish_to(server_folder, source)
    run = source.name
    ours_root, vega_root = tmp_path / "ours", tmp_path / "vega"
    transfer.bring_in(str(server_folder), run, "AT", "Lab MacBook", local_root=ours_root)
    transfer.bring_in(str(server_folder), run, "AT", "Legion", take_over=True, local_root=vega_root)
    ours = Annotator(ours_root / run, FAKE)
    monkeypatch.setenv("VEGA_TEST_PLAIN_PAGE", "1")
    vega = Annotator(vega_root / run, FAKE)
    monkeypatch.delenv("VEGA_TEST_PLAIN_PAGE")
    debug_port = free_port()
    chrome = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                               f"--remote-debugging-port={debug_port}", f"--user-data-dir={tmp_path / 'chrome'}",
                               "--window-size=1280,900", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        browser = browser_address(debug_port)
        for width in WIDTHS:
            mine, left = shoot(browser, ours.base + "/", width, tmp_path / f"ours_{width}.png")
            theirs, no_readout = shoot(browser, vega.base + "/", width, tmp_path / f"vega_{width}.png")
            assert left.endswith(" left") and no_readout == ""
            assert mine.shape == theirs.shape
            assert len(np.unique(mine.reshape(-1, 3), axis=0)) > FEWEST_COLORS_ON_A_REAL_PAGE, "the page did not render"
            changed = (np.abs(mine - theirs) > CHANNEL_TOLERANCE).any(axis=2)
            rows = np.nonzero(changed.any(axis=1))[0]
            assert len(rows), "the frames-left readout did not draw"
            assert rows.max() < TOP_BAR_ROWS, f"pixels changed below the top bar at {width}: row {rows.max()}"
            assert changed.mean() < MOST_PIXELS_CHANGED, f"{changed.mean():.2%} of the page changed at {width}"
    finally:
        chrome.terminate()
        ours.stop()
        vega.stop()
