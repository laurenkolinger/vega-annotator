"""The person's choices for this computer, kept in settings.json."""

import os
import re
import socket
import subprocess
import sys

from . import paths
from .atomic import read_json_object, write_json_atomic

INITIALS_RE = re.compile(r"^[A-Za-z]{1,4}$")
DEVICE_CHOICES = ("auto", "cpu", "gpu")
DEFAULTS = {"server_folder": "", "initials": "", "device": "auto"}
COMPUTER_NAME_TIMEOUT_S = 3


def normalize_initials(value):
    """Return initials in VEGA's stored form, or raise.

    Parameters:
        value: the text the person typed.
    Returns:
        str: 1 to 4 letters, upper case.
    Raises:
        ValueError: when the text is not 1 to 4 letters.
    """
    text = str(value or "").strip()
    if not INITIALS_RE.match(text):
        raise ValueError("Initials must be 1 to 4 letters.")
    return text.upper()


def load():
    """Read settings.json, falling back to the defaults field by field.

    A missing file is normal on a first run. A corrupt file is moved aside to
    settings.json.bad so the person's next save does not hide the evidence, and
    the app carries on with the defaults.

    Returns:
        dict: every key of DEFAULTS, each holding a valid value.
    """
    path = paths.settings_path()
    merged = dict(DEFAULTS)
    if not path.exists():
        return merged
    try:
        stored = read_json_object(path)
    except ValueError:
        try:
            os.replace(path, str(path) + ".bad")
        except OSError:
            pass
        return merged
    if isinstance(stored.get("server_folder"), str):
        merged["server_folder"] = stored["server_folder"]
    if isinstance(stored.get("initials"), str) and INITIALS_RE.match(stored["initials"]):
        merged["initials"] = stored["initials"].upper()
    if stored.get("device") in DEVICE_CHOICES:
        merged["device"] = stored["device"]
    return merged


def save(updates):
    """Validate and store changed settings.

    Parameters:
        updates: a dict holding any of server_folder, initials, device.
    Returns:
        dict: the settings after the change.
    Raises:
        ValueError: on an unknown key or an invalid value, naming which.
    """
    current = load()
    for key, value in updates.items():
        if key == "initials":
            current[key] = normalize_initials(value) if value else ""
        elif key == "device":
            if value not in DEVICE_CHOICES:
                raise ValueError(f"Device must be one of {', '.join(DEVICE_CHOICES)}.")
            current[key] = value
        elif key == "server_folder":
            if not isinstance(value, str):
                raise ValueError("The server folder must be a path.")
            current[key] = value.strip()
        else:
            raise ValueError(f"Unknown setting: {key}")
    write_json_atomic(paths.settings_path(), current)
    return current


def computer_name():
    """Return a name people recognize for this computer.

    Returns:
        str: the Mac's ComputerName, the Windows COMPUTERNAME, or the host name.
    """
    if sys.platform == "darwin":
        try:
            result = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True,
                                    text=True, timeout=COMPUTER_NAME_TIMEOUT_S, check=False)
            name = result.stdout.strip()
            if result.returncode == 0 and name:
                return name
        except (OSError, subprocess.SubprocessError):
            pass
    return os.environ.get("COMPUTERNAME") or socket.gethostname() or "this computer"
