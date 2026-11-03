"""The real click path in a real Chrome with the real SAM3 weights.

Skipped unless VEGA_TEST_HF_HOME names a Hugging Face folder holding facebook/sam3 and
Chrome and node are on this computer.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

import helpers
from annotator_driver import free_port
from vega_annotator import pathmap

ROOT = Path(__file__).resolve().parent.parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HF_HOME = os.environ.get("VEGA_TEST_HF_HOME", "")
DRIVER = ROOT / "tests" / "e2e" / "chrome_flow.mjs"

pytestmark = [pytest.mark.real_sam,
              pytest.mark.skipif(not HF_HOME or not os.path.exists(CHROME) or not shutil.which("node"),
                                 reason="needs VEGA_TEST_HF_HOME, Chrome and node")]


def wait_url(url, seconds=60):
    """Wait until a URL answers, and return the JSON it sent."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return json.loads(response.read().decode("utf-8"))
        except OSError:
            time.sleep(0.2)
    raise AssertionError(f"{url} did not answer")


def test_the_whole_click_path(tmp_path, server_folder, app_home):
    source = helpers.make_project(tmp_path / "inprocess", frames=12, exported=2)
    helpers.publish_to(server_folder, source)
    run = source.name
    hub = app_home / "sam3" / "hf" / "hub"
    hub.mkdir(parents=True)
    os.symlink(Path(HF_HOME) / "hub" / "models--facebook--sam3", hub / "models--facebook--sam3")
    (app_home / "settings.json").write_text(json.dumps({"server_folder": str(server_folder), "device": "cpu"}), encoding="utf-8")
    out = tmp_path / "shots"
    out.mkdir()
    port = free_port()
    home_log = open(tmp_path / "home.log", "w", encoding="utf-8")
    home = subprocess.Popen([sys.executable, "-m", "vega_annotator.home", "--port", str(port)], cwd=str(ROOT),
                            env={**os.environ, "PYTHONPATH": str(ROOT)}, stdout=home_log, stderr=subprocess.STDOUT)
    debug_port = free_port()
    chrome = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", f"--remote-debugging-port={debug_port}",
                               f"--user-data-dir={tmp_path / 'chrome'}", "--no-first-run", "--window-size=1280,900",
                               "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_url(f"http://127.0.0.1:{port}/api/health")
        browser = wait_url(f"http://127.0.0.1:{debug_port}/json/version")["webSocketDebuggerUrl"]
        result = subprocess.run(["node", "--experimental-websocket", str(DRIVER), browser,
                                 f"http://127.0.0.1:{port}/", run, str(out)],
                                capture_output=True, text=True, timeout=400, check=False)
        lines = result.stdout.strip().splitlines()
        report = json.loads(lines[-1]) if lines else {"ok": False, "error": result.stderr[-800:]}
        assert report["ok"] is True, report
    finally:
        chrome.terminate()
        home.terminate()
        home.wait(30)
        home_log.close()
    facts = report["facts"]
    assert facts["purposeSuggestion"] == f"running reef_point_seg on run {run}"
    assert facts["initialsSeeded"] == "AT" and facts["railStep1"] == "completed"
    assert "10 frames left" in facts["readout"].replace("\xa0", " ") and facts["left"] == "10 left"
    assert facts["tutorialButton"] == 0 and facts["googleLinks"] == 0 and facts["fontsLoaded"] is True
    assert " sent. " in facts["putBack"] and facts["annotatorWindowClosed"] is True
    assert facts["descriptionAfterEscape"] == "add a description..."
    assert facts["putBackDisabled"] is True and "latest work" in facts["stateNote"]
    state = json.loads((server_folder / "projects" / run / "vega_annotator.json").read_text(encoding="utf-8"))
    assert state["rev"] == 2 and state["out"] is None
    store = json.loads((server_folder / "projects" / run / pathmap.SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    accepted = [mask for frame in store.values() for mask in frame["masks"] if mask["status"] == "accepted" and mask.get("source_type") == "manual_click" and mask.get("score", 0) != 0.9]
    assert accepted and accepted[0]["species"] == "PA" and accepted[0]["reviewer"] == "AT"
    record = json.loads((server_folder / "projects" / run / "project.json").read_text(encoding="utf-8"))
    actions = [entry["action"] for entry in record["access_log"]]
    assert " sent. " in facts["putBack"] and facts["annotatorWindowClosed"] is True
    assert facts["descriptionAfterEscape"] == "add a description..."
    assert facts["putBackDisabled"] is True and "latest work" in facts["stateNote"]
    assert record["description"] == "round two"
