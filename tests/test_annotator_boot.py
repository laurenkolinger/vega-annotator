"""Tests for the laptop layer around VEGA's annotator: the page, the guard, the devices."""

import types
from pathlib import Path

import pytest

from vega_annotator import annotator_boot as boot

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "vendor/vega/scripts/TCRMPclip_combinedAnnotate/src/templates/index.html"


def test_vegas_page_loses_google_fonts_and_gains_local_files():
    page = TEMPLATE.read_text(encoding="utf-8")
    assert "fonts.googleapis.com" in page
    local = boot.localize_page(page)
    assert "fonts.googleapis.com" not in local and "fonts.gstatic.com" not in local
    assert "/portable/fonts.css" in local and "/portable/annotator_extras.js" in local
    assert local.index("/portable/fonts.css") < local.index("<style>")
    assert local.count("</body>") == page.count("</body>")


def test_page_elements_the_readout_needs_still_exist_in_vegas_page():
    page = TEMPLATE.read_text(encoding="utf-8")
    assert 'id="img-counter"' in page and 'id="btn-next-frame"' in page


@pytest.mark.parametrize("host,origin,allowed", [
    ("127.0.0.1:5080", None, True),
    ("localhost:5080", None, True),
    ("[::1]:5080", None, True),
    ("127.0.0.1:5080", "http://127.0.0.1:5050", True),
    ("localhost:5080", "http://localhost:5150", True),
    ("evil.example", None, False),
    ("evil.example:5080", "http://evil.example", False),
    ("127.0.0.1:5080", "https://evil.example", False),
    ("127.0.0.1:5080", "null", False),
    ("", None, False),
    ("127.0.0.1.evil.example", None, False),
])
def test_only_this_computer_gets_in(host, origin, allowed):
    assert boot.is_local_request(host, origin) is allowed


def fake_torch(cuda=False, mps=False):
    return types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: cuda),
                                 backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps)))


def test_missing_gpu_falls_back_to_cpu_at_float32():
    env = {"TCRMP_SAM3_DEVICE_TRACKER": "cuda:0", "TCRMP_SAM3_DEVICE_EXEMPLAR": "cuda:0",
           boot.TRACKER_DTYPE_ENV: "bfloat16"}
    notes = boot.choose_runtime_devices(fake_torch(), env)
    assert env["TCRMP_SAM3_DEVICE_TRACKER"] == "cpu:0" and env["TCRMP_SAM3_DEVICE_EXEMPLAR"] == "cpu:0"
    assert env[boot.TRACKER_DTYPE_ENV] == "float32"
    assert len(notes) == 2


def test_available_devices_stay():
    env = {"TCRMP_SAM3_DEVICE_TRACKER": "mps:0", "TCRMP_SAM3_DEVICE_EXEMPLAR": "cpu:0",
           boot.TRACKER_DTYPE_ENV: "bfloat16"}
    assert boot.choose_runtime_devices(fake_torch(mps=True), env) == []
    assert env["TCRMP_SAM3_DEVICE_TRACKER"] == "mps:0" and env[boot.TRACKER_DTYPE_ENV] == "bfloat16"


def test_wrap_tracker_dtype_replaces_the_request():
    seen = []
    module = types.SimpleNamespace(_load_image_tracker=seen.append)
    boot.wrap_tracker_dtype(module, "float32")
    module._load_image_tracker("bfloat16")
    assert seen == ["float32"]


def test_portable_layer_guards_rewrites_and_serves(tmp_path):
    from flask import Flask

    (tmp_path / "fonts.css").write_text("/* fonts */", encoding="utf-8")
    app = Flask(__name__)

    @app.route("/")
    def page():
        return ('<html><head><link href="https://fonts.googleapis.com/css2?x" rel="stylesheet">\n<style></style></head>'
                "<body></body></html>")

    @app.route("/data")
    def data():
        response = app.response_class('{"a":1}', mimetype="application/json")
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    boot.attach_portable_layer(app, str(tmp_path))
    client = app.test_client()
    html = client.get("/", base_url="http://127.0.0.1:5080").get_data(as_text=True)
    assert "googleapis" not in html and "/portable/fonts.css" in html
    assert client.get("/portable/fonts.css", base_url="http://127.0.0.1:5080").status_code == 200
    assert "Access-Control-Allow-Origin" not in client.get("/data", base_url="http://127.0.0.1:5080").headers
    assert client.get("/data", base_url="http://evil.example").status_code == 403
    assert client.get("/data", base_url="http://127.0.0.1:5080", headers={"Origin": "https://evil.example"}).status_code == 403
