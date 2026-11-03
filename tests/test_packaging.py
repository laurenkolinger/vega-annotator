"""Tests for the installers: what they hold, and how the Mac launcher behaves.

The Mac launcher runs for real in a temporary home folder, with every command that would
touch the Mac replaced by a stub that records its arguments, as Sponge Lab's tests do.
"""

import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from vega_annotator import VERSION, launcher

ROOT = Path(__file__).resolve().parent.parent
LABEL = "org.vicar.vega-annotator.home"
STUBS = {
    "launchctl": 'echo "launchctl $*" >> "$STUB_STATE/calls.log"\n'
                 'case "$1" in kickstart) touch "$STUB_STATE/healthy" ;; esac\n',
    "curl": '[ -e "$STUB_STATE/healthy" ] || exit 7\n'
            'echo "{\\"ok\\":true,\\"version\\":\\"$(cat "$STUB_STATE/version" 2>/dev/null || echo 1.0.0)\\"}"\n',
    "lsof": 'cat "$STUB_STATE/pid" 2>/dev/null\n',
    "open": 'echo "open $*" >> "$STUB_STATE/calls.log"\n',
    "osascript": 'echo "osascript $*" >> "$STUB_STATE/calls.log"\n',
    "uname": 'echo "$(cat "$STUB_STATE/arch" 2>/dev/null || echo arm64)"\n',
}


@pytest.fixture
def rig(tmp_path):
    """An installed copy of the Mac launcher in a temporary home, with a ready runtime."""
    home = tmp_path / "home"
    app = home / "Applications" / "VEGA Annotator.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources" / "app").mkdir(parents=True)
    shutil.copy(ROOT / "packaging" / "mac" / "launch.sh", app / "Contents" / "MacOS" / "launch")
    shutil.copytree(ROOT / "requirements", app / "Contents" / "Resources" / "app" / "requirements")
    (app / "Contents" / "Info.plist").write_text(
        '<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleShortVersionString</key>'
        f"<string>{VERSION}</string></dict></plist>", encoding="utf-8")
    (app / "Contents" / "Resources" / "setup.sh").write_text('echo setup >> "$STUB_STATE/calls.log"; exit "${SETUP_EXIT:-0}"\n', encoding="utf-8")
    support = home / "Library" / "Application Support" / "VEGA Annotator" / "runtime"
    (support / "bin").mkdir(parents=True)
    os.symlink(sys.executable, support / "bin" / "python")
    requirements = (ROOT / "requirements" / "base.txt").read_bytes() + (ROOT / "requirements" / "mac.txt").read_bytes()
    (support / ".complete").write_bytes(requirements)
    bin_dir = tmp_path / "stubs"
    bin_dir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    for name, body in STUBS.items():
        stub = bin_dir / name
        stub.write_text("#!/bin/bash\n" + body, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin", "STUB_STATE": str(state)}

    def run(**extra):
        return subprocess.run(["/bin/bash", str(app / "Contents" / "MacOS" / "launch")], env={**env, **extra},
                              capture_output=True, text=True, timeout=60, check=False)

    return {"run": run, "state": state, "home": home, "support": support}


def calls(rig):
    path = rig["state"] / "calls.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac launcher")
def test_mac_launch_starts_the_service_and_opens_chrome(rig):
    result = rig["run"]()
    assert result.returncode == 0, result.stderr
    log = calls(rig)
    assert f"launchctl bootstrap gui/{os.getuid()}" in log and "kickstart" in log
    assert "open -a Google Chrome http://127.0.0.1:5050/" in log
    assert "setup" not in log
    plist = rig["home"] / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    import plistlib
    service = plistlib.loads(plist.read_bytes())
    assert service["ProgramArguments"][1:] == ["-m", "vega_annotator.home", "--port", "5050"]
    assert service["Label"] == LABEL


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac launcher")
def test_mac_launch_reuses_a_healthy_server(rig):
    (rig["state"] / "healthy").touch()
    (rig["state"] / "version").write_text(VERSION, encoding="utf-8")
    result = rig["run"]()
    assert result.returncode == 0
    assert "bootstrap" not in calls(rig) and "open -a" in calls(rig)


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac launcher")
def test_mac_launch_runs_setup_when_the_requirements_changed(rig):
    (rig["support"] / ".complete").write_text("older requirements", encoding="utf-8")
    result = rig["run"]()
    assert result.returncode == 0
    log = calls(rig)
    assert "display dialog" in log and "setup" in log


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac launcher")
def test_mac_launch_reports_a_failed_setup(rig):
    (rig["support"] / ".complete").unlink()
    result = rig["run"](SETUP_EXIT="1")
    assert result.returncode == 1
    assert "display alert" in calls(rig)


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac launcher")
def test_mac_launch_refuses_an_intel_mac_with_a_reason(rig):
    (rig["state"] / "arch").write_text("x86_64", encoding="utf-8")
    result = rig["run"]()
    assert result.returncode == 1
    assert "display alert" in calls(rig) and "launchctl" not in calls(rig)


def test_windows_zip_holds_everything_setup_copies(tmp_path):
    subprocess.run([sys.executable, str(ROOT / "packaging" / "windows" / "build_zip.py")], check=True, capture_output=True)
    path = ROOT / "dist" / f"VEGAAnnotator-{VERSION}-Windows.zip"
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    top = "VEGA Annotator Installer/"
    for needed in ("Install VEGA Annotator.cmd", "setup.ps1", "VEGA Annotator.vbs", "icon.ico",
                   "vega_annotator/home.py", "vega_annotator/static/fonts.css",
                   "vega_annotator/templates/home.html", "requirements/windows-cuda.txt",
                   "requirements/windows-cpu.txt", "requirements/base.txt",
                   "vendor/vega/scripts/TCRMPclip_combinedAnnotate/src/app.py",
                   "vendor/vega/scripts/TCRMPclip_combinedAnnotate/src/templates/index.html",
                   "vendor/vega/harness/vicarius_ui_os/static/theme.css", "vendor/SAM_LICENSE.txt"):
        assert top + needed in names, needed
    assert not [name for name in names if "__pycache__" in name or name.endswith(".pyc") or ".DS_Store" in name]
    assert not [name for name in names if name.endswith((".safetensors", ".jpeg"))]


def test_windows_scripts_name_what_the_zip_holds():
    setup = (ROOT / "packaging" / "windows" / "setup.ps1").read_text(encoding="utf-8")
    assert "'windows-cuda.txt'" in setup and "'windows-cpu.txt'" in setup
    assert "api/shutdown" in setup and "Scripts\\python.exe" in setup
    vbs = (ROOT / "packaging" / "windows" / "VEGA Annotator.vbs").read_text(encoding="utf-8")
    assert "pythonw.exe" in vbs and "vega_annotator.launcher" in vbs


def test_cuda_requirements_pin_real_builds():
    text = (ROOT / "requirements" / "windows-cuda.txt").read_text(encoding="utf-8")
    assert "torch==2.11.0+cu128" in text and "torchvision==0.26.0+cu128" in text
    assert "download.pytorch.org/whl/cu128" in text


def test_icon_writes_png_and_ico(tmp_path):
    subprocess.run([sys.executable, str(ROOT / "packaging" / "make_icon.py"), str(tmp_path / "a.png"), "64"], check=True)
    subprocess.run([sys.executable, str(ROOT / "packaging" / "make_icon.py"), str(tmp_path / "a.ico"), "64"], check=True)
    assert (tmp_path / "a.png").read_bytes().startswith(b"\x89PNG")
    assert (tmp_path / "a.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"
    from PIL import Image
    assert Image.open(tmp_path / "a.png").size == (64, 64)
    assert Image.open(tmp_path / "a.ico").size[0] == 64


def test_launcher_running_version_handles_silence():
    assert launcher.running_version("http://127.0.0.1:9/") == ""


def test_launcher_opens_the_page_when_home_is_up(monkeypatch):
    opened = []
    monkeypatch.setattr(launcher, "running_version", lambda url=None: VERSION)
    monkeypatch.setattr(launcher, "open_browser", lambda url=None: opened.append(True))
    assert launcher.main() == 0 and opened == [True]


def test_launcher_replaces_a_home_from_another_version(monkeypatch):
    answers = iter(["0.9.0", VERSION, VERSION])
    started, stopped, opened = [], [], []
    monkeypatch.setattr(launcher, "running_version", lambda url=None: next(answers))
    monkeypatch.setattr(launcher, "stop_old_home", lambda url=None: stopped.append(True) or True)
    monkeypatch.setattr(launcher, "start_home", lambda: started.append(True))
    monkeypatch.setattr(launcher, "open_browser", lambda url=None: opened.append(True))
    assert launcher.main() == 0
    assert stopped == [True] and started == [True] and opened == [True]


def test_launcher_says_where_to_look_when_home_will_not_start(monkeypatch, capsys):
    shown = []
    monkeypatch.setattr(launcher, "show_message_box", shown.append)
    monkeypatch.setattr(launcher, "running_version", lambda url=None: "")
    monkeypatch.setattr(launcher, "start_home", lambda: None)
    monkeypatch.setattr(launcher, "START_WAIT_S", 0)
    assert launcher.main() == 1
    assert "home.log" in capsys.readouterr().err
    assert len(shown) == (1 if launcher.os.name == "nt" else 0)


def test_launcher_failure_survives_having_no_console(monkeypatch):
    shown = []
    monkeypatch.setattr(launcher, "show_message_box", shown.append)
    monkeypatch.setattr(launcher.sys, "stderr", None)
    launcher.report_failure("It did not start.")
    assert shown == (["It did not start."] if launcher.os.name == "nt" else [])
