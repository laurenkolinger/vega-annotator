"""Combine this computer's projects and the server folder's into one list of cards.

Each card says where the project is and what can be done with it now. The states:

- on_server: only on the server, and free to bring in.
- here: this computer holds it, with nothing new to put back.
- changed: this computer holds it, with labeling work not yet on the server.
- put_back: its work went back to the server, and bringing it in again is quick.
- server_newer: the server has a newer version than this computer's clean copy.
- held_elsewhere: another computer holds it.
- conflict: this computer has unsent work, and the server copy moved on or another
  computer took it over. Put back then saves the work beside the project.
- saved_beside: this computer's unsent work is safe under conflicts/ beside the project,
  so it can bring in the server's copy or remove its own.
- no_server: the server folder is not connected, and the copy here is not held.
- not_on_server: the server folder is connected and does not hold this project. Work
  here cannot go back until the project is there again.
- made_here: this computer made the project from a folder of images, and only this computer
  holds it. It never goes to or comes from the server folder. The card lists its actions:
  open, add images, labels, export for VEGA, and remove. While its first import is unfinished
  (creating), it lists only add images, which finishes that import, and remove.
- error: a record cannot be read. The card says why and offers nothing that would act on it.
"""

import glob
import os
import threading
from pathlib import Path

from . import folder_projects, paths, projects, transfer

SERVER_CHECK_TIMEOUT_S = 4
MADE_HERE = folder_projects.MADE_HERE
MADE_HERE_ACTIONS = ("open", "add_images", "labels", "export_for_vega", "remove")
CREATING_ACTIONS = ("add_images", "remove")
_listing_sizes = {}
_AUTODETECT_PATTERNS_MAC = ("/Volumes/*/students/*/VEGA_Annotator",
                            "/Volumes/*/*/students/*/VEGA_Annotator",
                            "/Volumes/*/*/VEGA_Annotator", "/Volumes/*/VEGA_Annotator",
                            "~/SynologyDrive/*/VEGA_Annotator", "~/SynologyDrive/VEGA_Annotator")
_WINDOWS_DRIVES = "DEFGHIJKLMNOPQRSTUVWXYZ"
_AUTODETECT_TAILS_WINDOWS = ("students/*/VEGA_Annotator", "*/students/*/VEGA_Annotator",
                             "*/VEGA_Annotator", "VEGA_Annotator")


def with_timeout(function, seconds, default):
    """Run function in a daemon thread and give up waiting after seconds.

    A dead network share can block a file call for minutes, and the home screen
    must still answer.

    Parameters:
        function: a callable with no arguments.
        seconds: how long to wait.
        default: what to return on a timeout.
    Returns:
        The function's result, or default.
    """
    box = {}

    def worker():
        """Run the function and keep its result."""
        try:
            box["value"] = function()
        except OSError:
            box["value"] = default

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(seconds)
    return box.get("value", default)


def _looks_like_server_folder(path):
    """Return True when path holds a projects/ or sam3/ folder."""
    folder = Path(path)
    return (folder / paths.PROJECTS_SUBFOLDER).is_dir() or (folder / paths.SAM3_SUBFOLDER).is_dir()


def resolve_server_folder(chosen):
    """Turn the folder a person picked into the VEGA_Annotator folder.

    Parameters:
        chosen: the picked path. It may be VEGA_Annotator itself or the folder above it.
    Returns:
        str: the VEGA_Annotator folder, or chosen when neither form fits.
    """
    if not chosen:
        return ""
    nested = Path(chosen) / paths.SERVER_FOLDER_NAME
    return str(nested) if nested.is_dir() else str(chosen)


def autodetect_server_folder():
    """Look for a connected server folder in the usual places.

    Returns:
        str: the first match, or "".
    """
    patterns = []
    if os.name == "nt":
        for letter in _WINDOWS_DRIVES:
            patterns.extend(f"{letter}:/{tail}" for tail in _AUTODETECT_TAILS_WINDOWS)
        home = os.path.expanduser("~")
        patterns.extend(f"{home}/SynologyDrive/{tail}" for tail in _AUTODETECT_TAILS_WINDOWS)
    else:
        patterns.extend(os.path.expanduser(pattern) for pattern in _AUTODETECT_PATTERNS_MAC)
    for pattern in patterns:
        for match in glob.glob(pattern):
            if _looks_like_server_folder(match):
                return match
    return ""


def server_status(chosen):
    """Check the server folder without letting a dead connection hang the page.

    Parameters:
        chosen: the folder from settings, or "".
    Returns:
        dict: path (str), connected (bool), and reason (str, when not connected).
    """
    path = chosen or with_timeout(autodetect_server_folder, SERVER_CHECK_TIMEOUT_S, "")
    if not path:
        return {"path": "", "connected": False, "reason": "No server folder is chosen yet."}
    resolved = with_timeout(lambda: resolve_server_folder(path), SERVER_CHECK_TIMEOUT_S, path)
    usable = with_timeout(lambda: _looks_like_server_folder(resolved), SERVER_CHECK_TIMEOUT_S, False)
    if usable:
        return {"path": resolved, "connected": True, "reason": ""}
    exists = with_timeout(lambda: Path(resolved).is_dir(), SERVER_CHECK_TIMEOUT_S, False)
    reason = ("That folder holds no projects or sam3 folder." if exists
              else "The server is not connected to this computer.")
    return {"path": resolved, "connected": False, "reason": reason}


def _server_size(project):
    """Return the bytes a server copy lists, reading its file list again only when it changed.

    Parameters:
        project: the project folder on the server.
    Returns:
        int: the total size, or None when the file list cannot be read.
    """
    listing = Path(project) / transfer.CHECKSUMS
    try:
        stamp = listing.stat().st_mtime_ns
        cached = _listing_sizes.get(listing)
        if cached and cached[0] == stamp:
            return cached[1]
        total = sum(entry["size"] for entry in transfer.read_checksums(project).values())
    except (OSError, transfer.TransferError):
        return None
    _listing_sizes[listing] = (stamp, total)
    return total


def _project_folders(root):
    """Return {run name: folder} for the run_* folders, including one with a missing record."""
    found = {}
    try:
        names = os.listdir(root)
    except OSError:
        return found
    for name in names:
        folder = Path(root) / name
        if transfer.valid_run_name(name) and folder.is_dir():
            found[name] = folder
    return found


def _holder_from(server_state, computer):
    """Return who holds a project, per its server record.

    A Put Back another computer started and never finished counts as that computer
    holding the project, so the card offers Take Over and not a Bring In that is refused.

    Parameters:
        server_state: the project's vega_annotator.json.
        computer: this computer's name.
    Returns:
        dict: computer, initials and at; or None.
    """
    holder = server_state.get("out")
    if isinstance(holder, dict):
        return holder
    marker = server_state.get("put_back")
    if isinstance(marker, dict) and marker.get("computer") != computer:
        return {key: marker.get(key) for key in ("computer", "initials", "at")}
    return None


def _classify(run, local_folder, server_folder, computer, connected=False):
    """Work out one project's state and what can be done with it.

    Parameters:
        run: the run_* name.
        local_folder: the working copy, or None.
        server_folder: the server copy, or None.
        computer: this computer's name.
        connected: whether the server folder could be listed.
    Returns:
        dict: state, pending_files, holder (or None), server_rev, local_rev, holding, size_bytes, error.
        A project made here also carries where ("local", since a server project of the same
        name is another project), creating (its first import is unfinished), and actions.
    """
    info = {"state": "on_server", "pending_files": 0, "unsent_records": False, "holder": None,
            "server_rev": None, "local_rev": None, "holding": False, "error": "",
            "size_bytes": _server_size(server_folder) if server_folder is not None else None}
    server_state = None
    if server_folder is not None:
        try:
            server_state = transfer.read_server_state(server_folder)
            info["server_rev"] = server_state["rev"]
            info["holder"] = _holder_from(server_state, computer)
        except transfer.TransferError as exc:
            info["error"] = str(exc)
            if local_folder is None:
                info["state"] = "error"
                return info
    if local_folder is None:
        if info["holder"] and info["holder"].get("computer") != computer:
            info["state"] = "held_elsewhere"
        return info
    if folder_projects.made_here(local_folder) is not None:
        creating = folder_projects.creating(local_folder)
        info.update(state=MADE_HERE, where="local", holder=None, server_rev=None, error="", creating=creating,
                    size_bytes=transfer.project_size(local_folder),
                    actions=list(CREATING_ACTIONS if creating else MADE_HERE_ACTIONS))
        return info
    try:
        local_state = transfer.read_local_state(local_folder)
    except transfer.TransferError as exc:
        info.update(state="error", error=str(exc))
        return info
    if local_state is None:
        info.update(state="error", error=f"{run} is on this computer with no local record.")
        return info
    # A Bring In that stopped partway finishes here, with or without the server, so nothing
    # can open the project and label over files it has yet to move.
    try:
        transfer.finish_interrupted(local_folder, server_folder)
        local_state = transfer.read_local_state(local_folder)
    except (transfer.TransferError, OSError) as exc:
        info.update(state="error", error=str(exc))
        return info
    info["local_rev"] = local_state["server_rev"]
    info["holding"] = bool(local_state.get("holding"))
    info["size_bytes"] = transfer.project_size(local_folder)
    info["pending_files"] = len(transfer.changed_files(local_folder, local_state))
    info["unsent_records"] = bool(set(transfer.changed_files(local_folder, local_state, include_quiet=True))
                                  - set(transfer.changed_files(local_folder, local_state)))
    if transfer.saved_beside_covers(local_folder, local_state):
        info["state"] = "saved_beside"
        info["conflict_folder"] = local_state["saved_beside"].get("folder", "")
        return info
    if server_state is None:
        if connected and server_folder is None:
            info["state"] = "not_on_server"
        elif info["pending_files"]:
            info["state"] = "changed"
        else:
            info["state"] = "here" if local_state.get("holding") else "no_server"
        return info
    moved_on = server_state["rev"] != local_state["server_rev"]
    held_elsewhere = bool(info["holder"]) and info["holder"].get("computer") != computer
    if info["pending_files"]:
        info["state"] = "conflict" if (moved_on or held_elsewhere) else "changed"
    elif held_elsewhere:
        info["state"] = "held_elsewhere"
    elif moved_on:
        info["state"] = "server_newer"
    elif info["holder"]:
        info["state"] = "here"
    else:
        info["state"] = "put_back"
    return info


def project_card(run, server_path, connected, computer):
    """Return one project's card, or None when neither place holds it.

    Parameters:
        run: the run_* name.
        server_path: the VEGA_Annotator folder, or "".
        connected: whether that folder is reachable.
        computer: this computer's name.
    Returns:
        dict or None.
    """
    for card in list_projects(server_path, connected, computer):
        if card["run"] == run:
            return card
    return None


def list_projects(server_path, connected, computer):
    """Build the home screen's cards, newest first.

    Parameters:
        server_path: the VEGA_Annotator folder, or "".
        connected: whether that folder is reachable.
        computer: this computer's name.
    Returns:
        list: one dict per project, with the projects.describe fields, where
        (local, server or both), and the _classify fields.
    """
    local = _project_folders(paths.projects_dir())
    server = None
    if connected and server_path:
        server = with_timeout(lambda: _project_folders(transfer.server_projects_dir(server_path)),
                              SERVER_CHECK_TIMEOUT_S, None)
    listed = server is not None
    server = server or {}
    cards = []
    for run in sorted(set(local) | set(server)):
        local_folder, server_folder = local.get(run), server.get(run)
        card = projects.describe(local_folder or server_folder)
        unreadable = card.get("error", "")
        card["where"] = "both" if local_folder and server_folder else ("local" if local_folder else "server")
        card.update(_classify(run, local_folder, server_folder, computer, listed))
        if unreadable:
            card.update(state="error", error=unreadable)
        cards.append(card)
    cards.sort(key=lambda card: str(card.get("updated_at") or ""), reverse=True)
    return cards
