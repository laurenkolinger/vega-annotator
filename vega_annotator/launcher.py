"""Start the home screen if it is not running, and open it in a browser.

Windows uses this as its shortcut's target. The Mac app does the same work in
packaging/mac/launch.sh, through launchd, because macOS stops any process an app
leaves behind when the app exits.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

from . import VERSION, paths
from .home import DEFAULT_PORT

HEALTH_TIMEOUT_S = 2
START_WAIT_S = 30
STOP_WAIT_S = 5
POLL_S = 0.2
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
HOME_URL = f"http://127.0.0.1:{DEFAULT_PORT}/"
CHROME_PATHS = (r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
                r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
                r"%LocalAppData%\Google\Chrome\Application\chrome.exe")


def running_version(url=HOME_URL):
    """Return the version of the home screen answering at url, or "" when none answers.

    Parameters:
        url: the home screen's address.
    Returns:
        str: the version text.
    """
    try:
        with urllib.request.urlopen(url + "api/health", timeout=HEALTH_TIMEOUT_S) as response:
            return str(json.loads(response.read().decode("utf-8")).get("version") or "")
    except (OSError, ValueError):
        return ""


def start_home():
    """Start the home screen as a background process with no console window.

    Returns:
        subprocess.Popen: the process.
    """
    logs = paths.logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    log = open(logs / "home.log", "a", encoding="utf-8", errors="replace")
    flags = (CREATE_NO_WINDOW | DETACHED_PROCESS) if os.name == "nt" else 0
    return subprocess.Popen([sys.executable, "-m", "vega_annotator.home", "--port", str(DEFAULT_PORT)],
                            cwd=str(paths.app_root()), stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, creationflags=flags)


def stop_old_home(url=HOME_URL):
    """Ask a home screen from another version to stop, and wait until it has.

    Parameters:
        url: the home screen's address.
    Returns:
        bool: True when nothing answers any more.
    """
    request = urllib.request.Request(url + "api/shutdown", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=STOP_WAIT_S).close()
    except (OSError, ValueError):
        pass
    deadline = time.time() + STOP_WAIT_S
    while time.time() < deadline:
        if not running_version(url):
            return True
        time.sleep(POLL_S)
    return False


def open_browser(url=HOME_URL):
    """Open url in Chrome when it is installed, otherwise in the default browser.

    Parameters:
        url: the address to open.
    Returns:
        None.
    """
    if os.name == "nt":
        for template in CHROME_PATHS:
            path = os.path.expandvars(template)
            if os.path.isfile(path):
                subprocess.Popen([path, url])
                return
    webbrowser.open(url)


def show_message_box(message):
    """Show a Windows message box and wait for the person to close it.

    Parameters:
        message: the sentence to show.
    Returns:
        None.
    """
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, message, "VEGA Annotator", 0x10)


def report_failure(message):
    """Tell the person a start failed. pythonw has no console, so Windows gets a message box.

    Parameters:
        message: the sentence to show.
    Returns:
        None.
    """
    if sys.stderr is not None:
        print(message, file=sys.stderr)
    if os.name == "nt":
        show_message_box(message)


def main():
    """Make sure the current version's home screen is up, then open it.

    Returns:
        int: 0 when the page was opened, 1 when the home screen did not start.
    """
    version = running_version()
    if version and version != VERSION:
        stop_old_home()
        version = ""
    if not version:
        start_home()
        deadline = time.time() + START_WAIT_S
        while time.time() < deadline and not running_version():
            time.sleep(POLL_S)
        if not running_version():
            report_failure(f"VEGA Annotator did not start. The reason is in {paths.logs_dir() / 'home.log'}.")
            return 1
    open_browser()
    return 0


if __name__ == "__main__":
    sys.exit(main())
