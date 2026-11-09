"""Tests for the installers: what they hold, and how the Mac launcher and setup behave.

The Mac launcher and setup run for real in a temporary home folder, with every command that
would touch the Mac replaced by a stub that records its arguments, as Sponge Lab's tests do.
Setup's SAM3 step downloads from a release server on this computer, never from GitHub.
"""

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from test_sam3_download import PART_NAMES, FakeRelease, build_tar, flip_one_byte
from vega_annotator import VERSION, launcher, paths, sam3_download, sam3_files

ROOT = Path(__file__).resolve().parent.parent
LABEL = "org.vicar.vega-annotator.home"
APP_DATA_PARTS = ("Library", "Application Support", "VEGA Annotator")
FAKE_SAM3_COMMIT = "3c879f39826c281e95690f02c7821c4de09afae7"
STUBS = {
    "launchctl": 'echo "launchctl $*" >> "$STUB_STATE/calls.log"\n'
                 'case "$1" in kickstart) touch "$STUB_STATE/healthy" ;; esac\n',
    "curl": '[ -e "$STUB_STATE/healthy" ] || exit 7\n'
            'echo "{\\"ok\\":true,\\"version\\":\\"$(cat "$STUB_STATE/version" 2>/dev/null || echo 1.0.0)\\"}"\n',
    "lsof": 'cat "$STUB_STATE/pid" 2>/dev/null\n',
    "open": 'echo "open $*" >> "$STUB_STATE/calls.log"\n',
    "osascript": 'echo "osascript $*" >> "$STUB_STATE/calls.log"\n'
                 'exit "$(cat "$STUB_STATE/osascript_exit" 2>/dev/null || echo 0)"\n',
    "uname": 'echo "$(cat "$STUB_STATE/arch" 2>/dev/null || echo arm64)"\n',
}
# Setup's own stubs: curl hands over a stand-in uv, and osascript keeps each notification.
SETUP_STUBS = {
    "curl": 'echo "curl $*" >> "$STUB_STATE/calls.log"\ncat "$STUB_STATE/uv.tar.gz"\n',
    "osascript": 'echo "${@: -1}" >> "$STUB_STATE/notes.log"\n',
    "uname": "echo arm64\n",
}
# A stand-in for uv: "venv" makes a runtime whose Python is this test's own, and "pip"
# installs nothing.
FAKE_UV = """#!/bin/bash
echo "uv $1" >> "$STUB_STATE/calls.log"
if [ "$1" = venv ]; then
  for target in "$@"; do :; done
  mkdir -p "$target/bin"
  cp "$STUB_STATE/runtime_python" "$target/bin/python"
fi
"""
# The runtime's Python in setup's tests: it answers setup's PyTorch check line, and runs
# everything else, the SAM3 downloader included, with this test's Python.
RUNTIME_PYTHON = """#!/bin/bash
if [ "$1" = -c ]; then echo "torch (stand-in)"; exit 0; fi
exec "$REAL_PYTHON" "$@"
"""


def write_stubs(folder, stubs):
    """Write each stub as an executable bash script in folder."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, body in stubs.items():
        stub = folder / name
        stub.write_text(body if body.startswith("#!") else "#!/bin/bash\n" + body, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)


def requirements_stamp():
    """Return the bytes setup writes to runtime/.complete."""
    return (ROOT / "requirements" / "base.txt").read_bytes() + (ROOT / "requirements" / "mac.txt").read_bytes()


def model_dir(app_data):
    """Return the SAM3 model folder inside an app folder."""
    return app_data / paths.sam3_model_dir().relative_to(paths.app_data_dir())


def install_fake_sam3(app_data):
    """Write a SAM3 copy that sam3_files counts as installed, with stand-in weights."""
    model = model_dir(app_data)
    (model / "refs").mkdir(parents=True)
    (model / "refs" / "main").write_text(FAKE_SAM3_COMMIT, encoding="utf-8")
    (model / "snapshots" / FAKE_SAM3_COMMIT).mkdir(parents=True)
    (model / "snapshots" / FAKE_SAM3_COMMIT / "model.safetensors").write_bytes(b"weights")


@pytest.fixture
def rig(tmp_path):
    """An installed copy of the Mac launcher in a temporary home, with a ready runtime and SAM3."""
    home = tmp_path / "home"
    app = home / "Applications" / "VEGA Annotator.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources" / "app").mkdir(parents=True)
    shutil.copy(ROOT / "packaging" / "mac" / "launch.sh", app / "Contents" / "MacOS" / "launch")
    shutil.copytree(ROOT / "requirements", app / "Contents" / "Resources" / "app" / "requirements")
    os.symlink(ROOT / "vega_annotator", app / "Contents" / "Resources" / "app" / "vega_annotator")
    (app / "Contents" / "Info.plist").write_text(
        '<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleShortVersionString</key>'
        f"<string>{VERSION}</string></dict></plist>", encoding="utf-8")
    (app / "Contents" / "Resources" / "setup.sh").write_text(
        'echo "setup $*" >> "$STUB_STATE/calls.log"; exit "${SETUP_EXIT:-0}"\n', encoding="utf-8")
    app_data = home.joinpath(*APP_DATA_PARTS)
    support = app_data / "runtime"
    (support / "bin").mkdir(parents=True)
    os.symlink(sys.executable, support / "bin" / "python")
    (support / ".complete").write_bytes(requirements_stamp())
    install_fake_sam3(app_data)
    bin_dir = tmp_path / "stubs"
    state = tmp_path / "state"
    state.mkdir()
    write_stubs(bin_dir, STUBS)
    env = {"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin", "STUB_STATE": str(state)}

    def run(**extra):
        """Run the launcher with the rig's environment plus extra variables."""
        return subprocess.run(["/bin/bash", str(app / "Contents" / "MacOS" / "launch")], env={**env, **extra},
                              capture_output=True, text=True, timeout=60, check=False)

    return {"run": run, "state": state, "home": home, "support": support, "app_data": app_data}


def calls(rig):
    """Return everything the rig's stubs recorded, as one text."""
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


def remove_fake_sam3(rig):
    """Take SAM3 off the rig, as on a Mac set up by version 1.0."""
    shutil.rmtree(rig["app_data"] / paths.SAM3_SUBFOLDER)


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac launcher")
def test_launch_dialog_names_sam3(rig):
    (rig["support"] / ".complete").unlink()
    result = rig["run"]()
    assert result.returncode == 0, result.stderr
    log = calls(rig)
    assert "Python, PyTorch and SAM3, about 4.5 GB" in log
    assert "setup \n" in log and "--sam3-only" not in log
    assert "open -a Google Chrome" in log


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac launcher")
def test_mac_launch_downloads_only_sam3_on_a_mac_set_up_by_1_0(rig):
    remove_fake_sam3(rig)
    result = rig["run"]()
    assert result.returncode == 0, result.stderr
    log = calls(rig)
    assert "SAM3, 3.4 GB" in log and "Python, PyTorch" not in log
    assert "setup --sam3-only" in log
    assert log.index("setup --sam3-only") < log.index("open -a Google Chrome")


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac launcher")
def test_mac_launch_opens_without_sam3_after_not_now(rig):
    remove_fake_sam3(rig)
    (rig["state"] / "osascript_exit").write_text("1", encoding="utf-8")
    result = rig["run"]()
    assert result.returncode == 0, result.stderr
    log = calls(rig)
    assert "display dialog" in log and "setup" not in log
    assert "open -a Google Chrome" in log


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac launcher")
def test_mac_launch_opens_when_the_sam3_step_fails(rig):
    remove_fake_sam3(rig)
    result = rig["run"](SETUP_EXIT="1")
    assert result.returncode == 0, result.stderr
    log = calls(rig)
    assert "setup --sam3-only" in log and "display alert" not in log
    assert "open -a Google Chrome" in log


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac launcher")
def test_mac_launch_never_asks_again_after_a_failed_download(rig):
    remove_fake_sam3(rig)
    (rig["app_data"] / sam3_download.failed_marker().name).write_text('{"reason": "stopped"}', encoding="utf-8")
    result = rig["run"]()
    assert result.returncode == 0, result.stderr
    log = calls(rig)
    assert "display dialog" not in log and "setup" not in log
    assert "open -a Google Chrome" in log


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac launcher")
def test_mac_launch_does_not_ask_while_sam3_downloads(rig):
    import fcntl
    remove_fake_sam3(rig)
    with open(rig["app_data"] / sam3_download.LOCK_NAME, "a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = rig["run"]()
    assert result.returncode == 0, result.stderr
    log = calls(rig)
    assert "display dialog" not in log and "setup" not in log
    assert "open -a Google Chrome" in log


@pytest.fixture
def fake_release():
    """Return a running FakeRelease, stopped after the test."""
    release = FakeRelease()
    yield release
    release.stop()


@pytest.fixture
def setup_rig(tmp_path, fake_release):
    """An installed copy of setup.sh in a temporary home.

    uv is a stand-in whose runtime Python is this test's own, notifications are kept in a
    file, and the app's SAM3 release record points at a FakeRelease on this computer.
    """
    home = tmp_path / "home"
    resources = home / "Applications" / "VEGA Annotator.app" / "Contents" / "Resources"
    resources.mkdir(parents=True)
    shutil.copy(ROOT / "packaging" / "mac" / "setup.sh", resources / "setup.sh")
    shutil.copytree(ROOT / "requirements", resources / "app" / "requirements")
    shutil.copytree(ROOT / "vega_annotator", resources / "app" / "vega_annotator",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    state = tmp_path / "state"
    state.mkdir()
    write_stubs(tmp_path / "stubs", SETUP_STUBS)
    write_stubs(state, {"runtime_python": RUNTIME_PYTHON})
    uv_bytes = FAKE_UV.encode("utf-8")
    with tarfile.open(state / "uv.tar.gz", "w:gz") as archive:
        info = tarfile.TarInfo("uv-aarch64-apple-darwin/uv")
        info.size, info.mode = len(uv_bytes), 0o755
        archive.addfile(info, io.BytesIO(uv_bytes))
    app_data = home.joinpath(*APP_DATA_PARTS)
    env = {"HOME": str(home), "PATH": f"{tmp_path / 'stubs'}:/usr/bin:/bin:/usr/sbin:/sbin",
           "STUB_STATE": str(state), "REAL_PYTHON": sys.executable}

    def publish(tar_bytes):
        """Serve a SAM3 tar from the fake release and point the app's record at it."""
        release = fake_release.publish(tar_bytes)
        record = resources / "app" / "vega_annotator" / "sam3_release.json"
        record.write_text(json.dumps(release, indent=2), encoding="utf-8")
        return release

    def run(*args):
        """Run setup.sh with the given arguments."""
        return subprocess.run(["/bin/bash", str(resources / "setup.sh"), *args], env=env,
                              capture_output=True, text=True, timeout=120, check=False)

    def read(name):
        """Return the lines of one of the stubs' record files."""
        path = state / name
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    log = home / "Library" / "Logs" / "VEGA Annotator install.log"
    return {"publish": publish, "run": run, "read": read, "state": state, "app_data": app_data,
            "runtime": app_data / "runtime",
            "marker": app_data / sam3_download.failed_marker().name, "release": fake_release,
            "log": lambda: log.read_text(encoding="utf-8") if log.exists() else ""}


def sam3_installed(setup_rig):
    """Return True when the rig's app folder holds an installed SAM3."""
    return sam3_files.installed_snapshot(model_dir(setup_rig["app_data"])) is not None


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac setup")
def test_setup_installs_python_and_sam3_on_a_fresh_mac(setup_rig):
    setup_rig["publish"](build_tar())
    result = setup_rig["run"]()
    assert result.returncode == 0, setup_rig["log"]()
    assert setup_rig["read"]("notes.log") == [
        "Step 1 of 4: downloading the setup tool",
        "Step 2 of 4: downloading Python",
        "Step 3 of 4: downloading PyTorch and the annotator's libraries",
        "Step 4 of 4: downloading SAM3 (3.4 GB)",
    ]
    assert (setup_rig["runtime"] / ".complete").read_bytes() == requirements_stamp()
    assert sam3_installed(setup_rig)
    assert not setup_rig["marker"].exists()
    assert not list(setup_rig["app_data"].rglob("*.part[0-9]"))
    assert "SAM3 is installed" in setup_rig["log"]()


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac setup")
def test_setup_has_step_four_and_tolerates_its_failure(setup_rig):
    setup_rig["publish"](build_tar())
    files = setup_rig["release"].files
    files[PART_NAMES[1]] = flip_one_byte(files[PART_NAMES[1]])
    result = setup_rig["run"]()
    assert result.returncode == 0, setup_rig["log"]()
    notes = setup_rig["read"]("notes.log")
    assert "Step 4 of 4: downloading SAM3 (3.4 GB)" in notes
    assert notes[-1].startswith("SAM3 did not download.") and "DOWNLOAD SAM3" in notes[-1]
    assert (setup_rig["runtime"] / ".complete").read_bytes() == requirements_stamp()
    assert not sam3_installed(setup_rig)
    assert PART_NAMES[1] in json.loads(setup_rig["marker"].read_text(encoding="utf-8"))["reason"]


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac setup")
def test_setup_downloads_only_sam3_on_a_mac_set_up_by_1_0(setup_rig):
    setup_rig["publish"](build_tar())
    (setup_rig["runtime"] / "bin").mkdir(parents=True)
    shutil.copy(setup_rig["state"] / "runtime_python", setup_rig["runtime"] / "bin" / "python")
    (setup_rig["runtime"] / ".complete").write_bytes(b"requirements of 1.0")
    result = setup_rig["run"]("--sam3-only")
    assert result.returncode == 0, setup_rig["log"]()
    assert setup_rig["read"]("notes.log") == ["Downloading SAM3 (3.4 GB)"]
    assert setup_rig["read"]("calls.log") == []
    assert (setup_rig["runtime"] / ".complete").read_bytes() == b"requirements of 1.0"
    assert sam3_installed(setup_rig)
    assert setup_rig["run"]("--everything").returncode != 0


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac setup")
def test_sam3_only_adds_to_the_log_and_a_full_setup_starts_it_again(setup_rig):
    """--sam3-only keeps the first setup's lines in the log; a full setup starts the log again."""
    setup_rig["publish"](build_tar())
    (setup_rig["runtime"] / "bin").mkdir(parents=True)
    shutil.copy(setup_rig["state"] / "runtime_python", setup_rig["runtime"] / "bin" / "python")
    (setup_rig["runtime"] / ".complete").write_bytes(b"requirements of 1.0")
    log = Path(setup_rig["state"]).parent / "home" / "Library" / "Logs" / "VEGA Annotator install.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    first = "SAM3 did not download: the first setup's reason, kept for Lauren.\n"
    log.write_text(first, encoding="utf-8")
    assert setup_rig["run"]("--sam3-only").returncode == 0
    assert setup_rig["log"]().startswith(first) and "SAM3 is installed" in setup_rig["log"]()
    assert setup_rig["run"]().returncode == 0
    assert first not in setup_rig["log"]()


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac setup")
def test_setup_keeps_an_installed_sam3(setup_rig):
    setup_rig["publish"](build_tar())
    assert setup_rig["run"]().returncode == 0
    setup_rig["release"].requests.clear()
    result = setup_rig["run"]()
    assert result.returncode == 0, setup_rig["log"]()
    assert setup_rig["release"].requests == []
    assert sam3_installed(setup_rig)
    assert "SAM3 is already installed" in setup_rig["log"]()


@pytest.mark.skipif(sys.platform != "darwin", reason="builds the Mac package")
def test_pkg_shows_the_sam_license(tmp_path):
    dist = tmp_path / "dist"
    expanded = tmp_path / "expanded"
    env = {**os.environ, "PATH": f"{Path(sys.executable).parent}:{os.environ.get('PATH', '/usr/bin:/bin')}",
           "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        built = subprocess.run(["/bin/bash", str(ROOT / "packaging" / "mac" / "build_pkg.sh"), str(dist)],
                               env=env, capture_output=True, text=True, timeout=600, check=False)
        assert built.returncode == 0, built.stderr
        package = dist / f"VEGAAnnotator-{VERSION}.pkg"
        assert built.stdout.strip() == str(package)
        subprocess.run(["pkgutil", "--expand", str(package), str(expanded)], check=True, capture_output=True)
        distribution = (expanded / "Distribution").read_text(encoding="utf-8")
        assert '<license file="SAM_LICENSE.txt"/>' in distribution
        shown = (expanded / "Resources" / "SAM_LICENSE.txt").read_bytes()
        assert hashlib.sha256(shown).hexdigest() == sam3_download.load_release()["license"]["sha256"]
        assert shown == (ROOT / "vendor" / "SAM_LICENSE.txt").read_bytes()
    finally:
        shutil.rmtree(dist, ignore_errors=True)
        shutil.rmtree(expanded, ignore_errors=True)


def test_windows_zip_holds_everything_setup_copies(tmp_path):
    subprocess.run([sys.executable, str(ROOT / "packaging" / "windows" / "build_zip.py")], check=True, capture_output=True)
    path = ROOT / "dist" / f"VEGAAnnotator-{VERSION}-Windows.zip"
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    finally:
        # The test leaves no zip in dist/, where a publish could pick it up.
        path.unlink(missing_ok=True)
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


def test_start_here_and_the_installer_say_what_setup_takes():
    """START_HERE and the installer's last page say how long setup takes and that the app asks first."""
    start_here = (ROOT / "docs" / "START_HERE.txt").read_text(encoding="utf-8")
    assert "INSTALL (once per computer, 20 minutes to an hour, depending on the connection)" in start_here
    assert "about 20 minutes" not in start_here
    conclusion = (ROOT / "packaging" / "mac" / "resources" / "conclusion.html").read_text(encoding="utf-8")
    assert "<p>It opens now and asks to download what it needs, about 4.5 GB.</p>" in conclusion


def run_publish(tmp_path, with_zip=True, **env_extra):
    """Run tools/publish_to_server.sh with stand-in ssh and rsync, and return (result, the calls they logged)."""
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / f"VEGAAnnotator-{VERSION}.pkg").write_bytes(b"pkg")
    if with_zip:
        (dist / f"VEGAAnnotator-{VERSION}-Windows.zip").write_bytes(b"zip")
    stubs = tmp_path / "bin"
    stubs.mkdir()
    log = tmp_path / "calls.log"
    (stubs / "ssh").write_text(f'#!/bin/bash\necho "ssh $*" >> "{log}"\ncat > /dev/null\n', encoding="utf-8")
    (stubs / "rsync").write_text(f'#!/bin/bash\necho "rsync $*" >> "{log}"\n', encoding="utf-8")
    for stub in stubs.iterdir():
        stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}", "VEGA_PUBLISH_DEST": "/fake/VEGA_Annotator",
           "VEGA_PUBLISH_DIST": str(dist), **env_extra}
    if "VEGA_PUBLISH_WINDOWS" not in env_extra:
        env.pop("VEGA_PUBLISH_WINDOWS", None)
    result = subprocess.run(["/bin/bash", str(ROOT / "tools" / "publish_to_server.sh")], env=env,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    return result, calls


def test_publish_sends_only_the_mac_installer_unless_windows_is_asked_for(tmp_path):
    """A Windows zip a test run left in dist/ never reaches a student's folder; VEGA_PUBLISH_WINDOWS=1 sends it."""
    result, calls = run_publish(tmp_path / "default")
    assert result.returncode == 0, result.stderr
    installers = [line for line in calls.splitlines() if line.startswith("rsync") and "/install/" in line]
    assert len(installers) == 1 and f"VEGAAnnotator-{VERSION}.pkg" in installers[0] and ".zip" not in installers[0]
    result, calls = run_publish(tmp_path / "windows", VEGA_PUBLISH_WINDOWS="1")
    assert result.returncode == 0, result.stderr
    assert f"VEGAAnnotator-{VERSION}-Windows.zip" in calls


def test_publish_refuses_a_missing_windows_zip_when_asked_for_it(tmp_path):
    """Asking for Windows with no zip built stops before anything is sent, with a sentence."""
    result, calls = run_publish(tmp_path, with_zip=False, VEGA_PUBLISH_WINDOWS="1")
    assert result.returncode == 1
    assert "Build the Windows installer first." in result.stderr
    assert "rsync" not in calls
