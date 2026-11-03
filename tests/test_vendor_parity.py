"""VEGA's files must stay byte for byte what tools/sync_from_vega.py copied."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "vega"


def test_every_vendored_file_matches_its_recorded_checksum():
    manifest = json.loads((VENDOR / "VENDORED.json").read_text(encoding="utf-8"))
    assert manifest["commit"] and manifest["files"]
    for rel, expected in manifest["files"].items():
        actual = hashlib.sha256((VENDOR / rel).read_bytes()).hexdigest()
        assert actual == expected, f"{rel} differs from what was copied from VEGA"


def test_nothing_extra_was_added_to_the_vendored_folder():
    manifest = json.loads((VENDOR / "VENDORED.json").read_text(encoding="utf-8"))
    found = {path.relative_to(VENDOR).as_posix() for path in VENDOR.rglob("*")
             if path.is_file() and path.name != "VENDORED.json" and "__pycache__" not in path.parts}
    assert found == set(manifest["files"])


def test_requirements_match_vegas_lock_for_the_shared_packages():
    lock = {}
    for line in (VENDOR / "requirements.lock").read_text(encoding="utf-8").splitlines():
        if "==" in line:
            name, version = line.split("==", 1)
            lock[name.strip().lower()] = version.strip()
    pins = {}
    for line in (ROOT / "requirements" / "base.txt").read_text(encoding="utf-8").splitlines():
        if "==" in line and not line.startswith("#"):
            name, version = line.split("==", 1)
            pins[name.strip().lower()] = version.strip()
    for package in ("transformers", "huggingface_hub", "numpy", "pillow", "flask", "flask-cors",
                    "opencv-python-headless", "safetensors", "pyyaml"):
        assert pins[package] == lock[package], package
