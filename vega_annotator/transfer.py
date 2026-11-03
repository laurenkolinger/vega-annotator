"""Bring a project in from the server folder, and put it back.

The server folder holds each project as a VEGA project folder, with mainframe paths,
plus two records:

- checksums.json: size and SHA-256 of every project file, so each copy is verified.
- vega_annotator.json: the project's revision, which computer holds it, and its history.

Rules that keep work safe:

- A file arrives under a temporary name and moves into place only after its checksum
  matches, so a dropped connection leaves the old file whole.
- Bring In gathers everything in a staging folder first. Only a complete set moves into
  the working copy, and a journal finishes the move if the computer stops partway.
- Put Back marks the project as being put back, sends its files, and raises the revision
  last. A journal on this computer records what the new state will be, so a stop at any
  point either finishes or runs again, and never reads as a conflict.
- One computer holds a project at a time. When another computer took it over, or the
  server copy moved on, Put Back overwrites nothing: it saves this computer's changed
  files under conflicts/ beside the project and says so. This computer can then bring in
  the server's copy, or remove its own, because its work is safe.
- A server file about to be replaced is first copied to _superseded/<stamp>/.
- A file that changes while it is being read stops the send, so a save can never be marked
  as sent when it was not.
"""

import csv
import glob
import hashlib
import os
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import paths, projects
from .atomic import read_json_object, sha256_file, write_json_atomic
from .pathmap import MANIFEST_REL, PATH_BEARING_RELS, PROJECT_JSON_REL, STEP4_DIR, PathMap, rewrite_file

SERVER_STATE = "vega_annotator.json"
LOCAL_STATE = "vega_annotator_local.json"
BRING_IN_JOURNAL = "vega_annotator_bring_in.json"
PUT_BACK_JOURNAL = "vega_annotator_put_back.json"
CHECKSUMS = "checksums.json"
FORMAT = 1
COPY_CHUNK_BYTES = 4 * 1024 * 1024
FREE_SPACE_MARGIN_BYTES = 500 * 1024 * 1024
TEMP_SUFFIX = ".vegatmp"
EXCLUDED_DIRS = {"_superseded", "all_images", "conflicts", "__pycache__", ".git"}
EXCLUDED_NAMES = {SERVER_STATE, LOCAL_STATE, BRING_IN_JOURNAL, PUT_BACK_JOURNAL, CHECKSUMS,
                  ".DS_Store", "Thumbs.db", "desktop.ini"}
EXCLUDED_SUFFIXES = (".tmp", ".lock", TEMP_SUFFIX)
RUN_PREFIX = "run_"
EXPORTED_OUTCOMES = ("exported_with_masks", "exported_empty")
# Files the annotator rewrites just by opening, so they never mark a project as changed.
QUIET_RELS = {PROJECT_JSON_REL, f"{STEP4_DIR}/config_log.json"}
# Labeling adds previews and overlays. They came to 0.75 MB per exported frame on a real project.
GROWTH_PER_FRAME_BYTES = 1_000_000
FRAME_FOLDER_MARK = "/routed_input/raw/"
FOLDER_RENAME_ATTEMPTS = 60
FOLDER_RENAME_WAIT_S = 0.5

# The home screen looks for stopped moves every few seconds. This lock keeps that look away
# from a journal a running move has just written, which is not a stopped move.
_journal_lock = threading.RLock()


class TransferError(Exception):
    """A move that cannot go ahead, with a sentence the person can act on."""


def valid_run_name(run):
    """Return True when run is a plain run_* folder name, never a path.

    Parameters:
        run: the name to check.
    Returns:
        bool: whether the name is safe to join onto a folder.
    """
    return (isinstance(run, str) and run.startswith(RUN_PREFIX) and len(run) > len(RUN_PREFIX)
            and "/" not in run and "\\" not in run and ".." not in run and "\0" not in run)


def _when(stamp):
    """Return a record's time stamp the way a person reads it: 2026-11-02 18:27."""
    return str(stamp or "")[:16].replace("T", " ")


def _require_run(run):
    """Raise TransferError unless run is a plain run_* folder name."""
    if not valid_run_name(run):
        raise TransferError(f"{run!r} is not a project folder name.")


def is_excluded(rel):
    """Return True for files that never travel between computers.

    Parameters:
        rel: a path relative to the project, with forward slashes.
    Returns:
        bool: True for image links, backups, records, locks, and system files.
    """
    parts = rel.split("/")
    name = parts[-1]
    return (any(part in EXCLUDED_DIRS for part in parts[:-1]) or name in EXCLUDED_NAMES
            or name.startswith("._") or name.endswith(EXCLUDED_SUFFIXES))


def safe_rel(rel):
    """Return True when rel stays inside the project folder.

    Parameters:
        rel: a path from checksums.json.
    Returns:
        bool: False for absolute paths, parent references, backslashes, and blanks.
    """
    if not isinstance(rel, str) or not rel or "\\" in rel or "\0" in rel or rel.startswith("/"):
        return False
    return all(part not in ("", ".", "..") and ":" not in part for part in rel.split("/"))


def server_projects_dir(server_folder):
    """Return the projects/ folder inside the server folder."""
    return Path(server_folder) / paths.PROJECTS_SUBFOLDER


def read_server_state(project):
    """Read and check a server project's vega_annotator.json.

    Parameters:
        project: the project folder on the server.
    Returns:
        dict: the record.
    Raises:
        TransferError: when the record is missing, malformed or from a newer format.
    """
    try:
        state = read_json_object(Path(project) / SERVER_STATE)
    except ValueError as exc:
        raise TransferError(f"{Path(project).name} has no usable project record: {exc}") from exc
    if state.get("format") != FORMAT:
        raise TransferError(f"{Path(project).name} uses record format {state.get('format')!r}, "
                            f"and this app reads format {FORMAT}. Update the app.")
    source = state.get("source")
    if not isinstance(state.get("rev"), int) or not isinstance(source, dict) \
            or not isinstance(source.get("project_dir"), str) or not source["project_dir"]:
        raise TransferError(f"{Path(project).name} has an incomplete project record.")
    return state


def read_checksums(project):
    """Read and check a server project's checksums.json.

    Parameters:
        project: the project folder on the server.
    Returns:
        dict: {relative path: {"size": int, "sha256": str}}.
    Raises:
        TransferError: when the list is missing or malformed, or names a path
            outside the project.
    """
    try:
        listing = read_json_object(Path(project) / CHECKSUMS)
    except ValueError as exc:
        raise TransferError(f"{Path(project).name} has no usable file list: {exc}") from exc
    for rel, entry in listing.items():
        if not safe_rel(rel):
            raise TransferError(f"{Path(project).name}'s file list names an unsafe path: {rel!r}")
        if not isinstance(entry, dict) or not isinstance(entry.get("size"), int) \
                or not isinstance(entry.get("sha256"), str):
            raise TransferError(f"{Path(project).name}'s file list has a bad entry for {rel}.")
    return listing


def read_local_state(project):
    """Read a working copy's record.

    Parameters:
        project: the working copy's folder.
    Returns:
        dict: the record, or None when the folder has no record.
    Raises:
        TransferError: when the record exists and is malformed.
    """
    path = Path(project) / LOCAL_STATE
    if not path.exists():
        return None
    try:
        state = read_json_object(path)
    except ValueError as exc:
        raise TransferError(f"{Path(project).name}'s local record is damaged: {exc}") from exc
    if not isinstance(state.get("base"), dict) or not isinstance(state.get("server_rev"), int):
        raise TransferError(f"{Path(project).name}'s local record is incomplete.")
    return state


def _check_writable(folder):
    """Raise TransferError unless a file can be created and removed in folder."""
    probe = Path(folder) / f".vega_write_probe_{uuid.uuid4().hex}{TEMP_SUFFIX}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        raise TransferError(f"The server folder cannot be written to: {exc}") from exc


def _check_space(folder, needed):
    """Raise TransferError unless folder's disk has room for needed bytes plus a margin."""
    target = Path(folder)
    while not target.exists() and target != target.parent:
        target = target.parent
    free = shutil.disk_usage(target).free
    if free < needed + FREE_SPACE_MARGIN_BYTES:
        raise TransferError(f"This needs {(needed + FREE_SPACE_MARGIN_BYTES) / 1e9:.1f} GB free "
                            f"and the disk has {free / 1e9:.1f} GB.")


def copy_verified(src, dest, expected_sha=None, progress=None, read_back=False):
    """Copy one file through a temporary name, checking its checksum.

    Parameters:
        src: the file to read.
        dest: the destination path; its folder is created when missing.
        expected_sha: the SHA-256 the content must have; None skips the comparison.
        progress: optional callable given each chunk's byte count.
        read_back: also re-read the written file and compare, for slow or
            unreliable destinations such as a network share.
    Returns:
        str: the SHA-256 of the copied content.
    Raises:
        TransferError: on a checksum mismatch, or when the source or destination
            fails. The destination then stays as it was.
    """
    src, dest = Path(src), Path(dest)
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:8]}{TEMP_SUFFIX}")
    digest = hashlib.sha256()
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(src, "rb") as reader, open(tmp, "wb") as writer:
            while True:
                chunk = reader.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
                writer.write(chunk)
                if progress:
                    progress(len(chunk))
            writer.flush()
            os.fsync(writer.fileno())
        actual = digest.hexdigest()
        if expected_sha and actual != expected_sha:
            raise TransferError(f"{src.name} did not match its checksum, so it was not copied. "
                                f"The file may be damaged or still syncing.")
        if read_back and sha256_file(tmp) != actual:
            raise TransferError(f"{dest.name} did not read back correctly from the server.")
        os.replace(tmp, dest)
        return actual
    except OSError as exc:
        raise TransferError(f"Copying {src.name} failed: {exc}") from exc
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _walk_project(root):
    """Yield (relative path, full path) for each travelling file under root."""
    root = Path(root)
    for folder, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in EXCLUDED_DIRS]
        base = Path(folder)
        for name in files:
            full = base / name
            rel = full.relative_to(root).as_posix()
            if not is_excluded(rel) and not full.is_symlink():
                yield rel, full


def _stat_pair(path):
    """Return a file's (size, mtime_ns)."""
    stat = Path(path).stat()
    return stat.st_size, stat.st_mtime_ns


def _base_entry(sha256, pair):
    """Return a base entry recording a file's checksum and its size and time."""
    return {"sha256": sha256, "size": pair[0], "mtime_ns": pair[1]}


def _unchanged(path, base_entry):
    """Return True when a local file still has the size and time in its base entry."""
    if not base_entry:
        return False
    try:
        return _stat_pair(path) == (base_entry.get("size"), base_entry.get("mtime_ns"))
    except OSError:
        return False


def project_size(project):
    """Return the bytes of a working copy's travelling files.

    Parameters:
        project: the working copy's folder.
    Returns:
        int: the total size. A file that vanishes mid-count counts as nothing.
    """
    total = 0
    for _rel, full in _walk_project(project):
        try:
            total += full.stat().st_size
        except OSError:
            pass
    return total


def changed_files(project, local_state, include_quiet=False):
    """List a working copy's files that differ from its last synced state.

    Parameters:
        project: the working copy's folder.
        local_state: its record.
        include_quiet: also list QUIET_RELS, the files that opening a project changes
            with no labeling done. They still go back with the work.
    Returns:
        list: relative paths of changed or new files.
    """
    base = local_state.get("base") or {}
    return [rel for rel, full in _walk_project(project)
            if (include_quiet or rel not in QUIET_RELS) and not _unchanged(full, base.get(rel))]


def saved_beside_covers(project, local_state):
    """Return True when every unsent file is exactly what a conflict copy already saved.

    Parameters:
        project: the working copy's folder.
        local_state: its record.
    Returns:
        bool: False when no conflict copy exists, or when work changed after it.
    """
    saved = local_state.get("saved_beside")
    if not isinstance(saved, dict):
        return False
    files = saved.get("files") or {}
    for rel in changed_files(project, local_state, include_quiet=True):
        recorded = files.get(rel)
        try:
            if not recorded or list(_stat_pair(Path(project) / rel)) != list(recorded):
                return False
        except OSError:
            return False
    return True


def _claim(server_project, checked, computer, initials, action):
    """Record on the server that this computer holds the project.

    Reads the record again first, because another computer can write it at any moment.

    Parameters:
        server_project: the project folder on the server.
        checked: the record the caller made its checks against.
        computer: this computer's name.
        initials: who is claiming it.
        action: "bring_in" or "take_over", for the history.
    Returns:
        dict: the claim as written.
    Raises:
        TransferError: when the record moved on since the checks, or cannot be written.
    """
    latest = read_server_state(server_project)
    if latest["rev"] != checked["rev"] or latest.get("out") != checked.get("out"):
        raise TransferError(f"{Path(server_project).name} changed on the server just now. Try again.")
    claim = {"computer": computer, "initials": initials, "at": projects.now_ast()}
    latest["out"] = claim
    latest["put_back"] = None
    latest.setdefault("history", []).append({"at": claim["at"], "action": action, "computer": computer,
                                             "initials": initials})
    try:
        write_json_atomic(Path(server_project) / SERVER_STATE, latest)
    except OSError as exc:
        raise TransferError(f"The server folder cannot be written to: {exc}") from exc
    return claim


def _undo_claim(server_project, claim, checked, action):
    """Give back a claim after a failed Bring In, keeping whatever another computer wrote since.

    With the connection gone the claim stays, and this computer can simply try again.

    Parameters:
        server_project: the project folder on the server.
        claim: the claim _claim wrote.
        checked: the record from before the claim.
        action: "bring_in" or "take_over", for the history.
    Returns:
        None.
    """
    try:
        latest = read_server_state(server_project)
        if latest.get("out") == claim:
            unchanged = latest["rev"] == checked["rev"]
            latest["out"] = checked.get("out") if unchanged else None
            latest["put_back"] = checked.get("put_back") if unchanged else None
        latest.setdefault("history", []).append({"at": projects.now_ast(), "action": f"{action}_failed",
                                                 "computer": claim["computer"], "initials": claim["initials"]})
        write_json_atomic(Path(server_project) / SERVER_STATE, latest)
    except (TransferError, OSError):
        pass


def _release(server_project, computer):
    """Clear the holder on the server when this computer is the holder."""
    latest = read_server_state(server_project)
    out = latest.get("out")
    if isinstance(out, dict) and out.get("computer") == computer:
        latest["out"] = None
        write_json_atomic(Path(server_project) / SERVER_STATE, latest)


def _relative_to_root(recorded, paths_root):
    """Return a recorded absolute path as a project-relative path, or "" when outside.

    Parameters:
        recorded: an absolute path from the export manifest.
        paths_root: the project folder that path was written against.
    Returns:
        str: a safe relative path with forward slashes, or "".
    """
    normalized = str(recorded or "").replace("\\", "/")
    root = str(paths_root).replace("\\", "/").rstrip("/")
    if not normalized.startswith(root + "/"):
        return ""
    rel = normalized[len(root) + 1:]
    return rel if safe_rel(rel) else ""


def _clear_stale_image(entry, project):
    """Take one all_images/ entry out of the way, because the export manifest does not list it.

    A link goes. A real file, which a computer with no links can hold, moves to _superseded/,
    because images are deleted only when a person asks.

    Parameters:
        entry: the path inside all_images/.
        project: the project's folder.
    Returns:
        None.
    """
    if entry.is_symlink():
        entry.unlink()
        return
    stamp = datetime.now(projects.AST).strftime("%Y%m%d-%H%M%S")
    aside = Path(project) / "_superseded" / stamp / "stale_all_images" / entry.name
    aside.parent.mkdir(parents=True, exist_ok=True)
    os.replace(entry, aside)


def rebuild_image_links(project, paths_root=None):
    """Make all_images/ hold exactly the frames the export manifest lists as exported.

    The links stay on each computer, because they do not travel over a network
    share. VEGA's export checks that all_images/ holds exactly one image per
    exported frame, so a working copy needs this before its next export.

    Parameters:
        project: the folder to work in.
        paths_root: the folder the manifest's image_path values name, when project
            is a staging folder that has not moved into place yet.
    Returns:
        int: how many links were made.
    Raises:
        TransferError: when an exported frame's picture is missing, since the next
            export would stop on it.
    """
    project = Path(project)
    manifest = project / MANIFEST_REL
    images = project / STEP4_DIR / "all_images"
    raw = project / STEP4_DIR / "routed_input" / "raw"
    rows = []
    if manifest.is_file():
        with open(manifest, newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    wanted = {}
    for row in rows:
        name = row.get("image_file") or ""
        if row.get("outcome") in EXPORTED_OUTCOMES and name and safe_rel(name) and "/" not in name:
            wanted[name] = row
    if images.is_dir():
        for entry in list(images.iterdir()):
            if entry.name not in wanted:
                _clear_stale_image(entry, project)
    if not wanted:
        return 0
    images.mkdir(parents=True, exist_ok=True)
    made = 0
    for name, row in wanted.items():
        link = images / name
        if link.exists():
            continue
        if link.is_symlink():
            link.unlink()
        target = raw / name
        if not target.is_file():
            # An imported image sits in custom_imports/, which the manifest's path names.
            rel = _relative_to_root(row.get("image_path"), paths_root or project)
            target = project / rel if rel else target
        if not target.is_file():
            raise TransferError(f"{name} is exported in this project and its picture is missing.")
        try:
            os.symlink(os.path.relpath(target, images), link)
        except (OSError, NotImplementedError):
            try:
                os.link(target, link)
            except OSError:
                shutil.copy2(target, link)
        made += 1
    return made


def _drop_bring_in_journal(project, staging):
    """Remove a finished Bring In's staging folder, then its journal."""
    shutil.rmtree(staging, ignore_errors=True)
    (Path(project) / BRING_IN_JOURNAL).unlink(missing_ok=True)


def _apply_bring_in(project, journal):
    """Move a staged update into the working copy and record it. Safe to run twice.

    Parameters:
        project: the working copy's folder.
        journal: the bring-in journal.
    Returns:
        None.
    """
    project = Path(project)
    staging = Path(journal["staging"])
    local_state = read_local_state(project) or {}
    base = dict(local_state.get("base") or {})
    for rel, sha in journal["files"].items():
        staged = staging / rel
        final = project / rel
        if staged.is_file():
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, final)
        if final.is_file():
            base[rel] = _base_entry(sha, _stat_pair(final))
    local_state.update({"base": base, "server_rev": journal["server_rev"], "holding": True,
                        "brought_in_at": projects.now_ast(), "computer": journal["computer"]})
    local_state.pop("saved_beside", None)
    write_json_atomic(project / LOCAL_STATE, local_state)
    # The journal goes last, so a stop anywhere before that runs all of this again.
    try:
        rebuild_image_links(project)
    except TransferError:
        # The files are in place and only a picture is missing. The next Bring In tries again.
        _drop_bring_in_journal(project, staging)
        raise
    _drop_bring_in_journal(project, staging)


def _apply_put_back(project, journal):
    """Record a finished Put Back in the working copy's record. Safe to run twice.

    Parameters:
        project: the working copy's folder.
        journal: the put-back journal.
    Returns:
        None.
    """
    project = Path(project)
    local_state = read_local_state(project)
    local_state.update({"base": journal["base"], "server_rev": journal["rev"], "holding": journal["holding"],
                        "put_back_at": journal["at"]})
    local_state.pop("saved_beside", None)
    write_json_atomic(project / LOCAL_STATE, local_state)
    (project / PUT_BACK_JOURNAL).unlink(missing_ok=True)


def _put_back_finished(state, token):
    """Return True when the server's history records the Put Back with this token."""
    return any(isinstance(row, dict) and row.get("token") == token for row in state.get("history") or [])


def finish_interrupted(project, server_project=None):
    """Finish a Bring In or Put Back that stopped partway on this computer.

    A Bring In journal always finishes, with or without the server, because its files are
    already on this computer. A Put Back journal finishes when the server's history shows
    that Put Back's token. When it does not, the Put Back never finished and its journal goes
    away, so Put Back simply runs again. With no server to ask, it waits.

    Parameters:
        project: the working copy's folder.
        server_project: the project folder on the server, or None when not connected.
    Returns:
        None.
    Raises:
        TransferError: when a journal is damaged.
    """
    project = Path(project)
    with _journal_lock:
        for name in (BRING_IN_JOURNAL, PUT_BACK_JOURNAL):
            path = project / name
            if not path.exists():
                continue
            try:
                journal = read_json_object(path)
            except ValueError as exc:
                raise TransferError(f"{project.name}'s {name} is damaged: {exc}") from exc
            if name == BRING_IN_JOURNAL:
                _apply_bring_in(project, journal)
            elif server_project is not None:
                if _put_back_finished(read_server_state(server_project), journal.get("token")):
                    _apply_put_back(project, journal)
                else:
                    path.unlink()


def _rename_folder(working, final):
    """Move a finished staging folder into place, waiting out a file scanner on Windows.

    Parameters:
        working: the staging folder.
        final: the project's folder.
    Returns:
        None.
    Raises:
        TransferError: when the folder stays held by another program.
    """
    for attempt in range(FOLDER_RENAME_ATTEMPTS):
        try:
            os.replace(working, final)
            return
        except PermissionError as exc:
            if attempt == FOLDER_RENAME_ATTEMPTS - 1:
                raise TransferError(f"Another program is holding {Path(final).name} open: {exc}. "
                                    f"Try again in a minute.") from exc
            time.sleep(FOLDER_RENAME_WAIT_S)


def _files_word(count):
    """Return "file" or "files" for a count."""
    return "file" if count == 1 else "files"


def _set_aside_local_only(project, pending, listing):
    """Move unsent files the server lacks out of the way, once a conflict copy holds them.

    Parameters:
        project: the working copy's folder.
        pending: the unsent relative paths.
        listing: the server's file list.
    Returns:
        None.
    """
    stamp = datetime.now(projects.AST).strftime("%Y%m%d-%H%M%S")
    for rel in pending:
        if rel in listing:
            continue
        aside = Path(project) / "_superseded" / stamp / "after_conflict" / rel
        aside.parent.mkdir(parents=True, exist_ok=True)
        os.replace(Path(project) / rel, aside)


def bring_in(server_folder, run, initials, computer, take_over=False, progress=None, local_root=None):
    """Copy a project from the server folder to this computer and claim it.

    Copies only what differs from what this computer already holds, so bringing
    a project in again after a Put Back is quick. Unsent work here is never overwritten,
    unless a conflict copy already holds exactly that work.

    Parameters:
        server_folder: the VEGA_Annotator folder on the server.
        run: the run_* folder name.
        initials: who is bringing it in.
        computer: this computer's name.
        take_over: claim the project even though another computer holds it.
        progress: optional callable (bytes done, bytes total, message).
        local_root: where working copies live; defaults to paths.projects_dir().
    Returns:
        dict: run, copied (files), and bytes (copied).
    Raises:
        TransferError: with a sentence saying why it cannot go ahead.
    """
    _require_run(run)
    local_root = Path(local_root) if local_root else paths.projects_dir()
    source = server_projects_dir(server_folder) / run
    if not source.is_dir():
        raise TransferError(f"{run} is not in the server folder.")
    final = local_root / run
    if final.exists():
        finish_interrupted(final, source)
    # A journal that needed a staging folder is finished by now, so any still here is left over.
    for leftover in glob.glob(str(local_root / f".incoming_{glob.escape(run)}_*")):
        shutil.rmtree(leftover, ignore_errors=True)
    state = read_server_state(source)
    listing = read_checksums(source)
    if PROJECT_JSON_REL not in listing:
        raise TransferError(f"{run}'s file list does not include project.json.")
    try:
        projects.read_project(source)
    except ValueError as exc:
        raise TransferError(f"{run} cannot be brought in: {exc}") from exc
    mid_put_back = state.get("put_back")
    if isinstance(mid_put_back, dict) and mid_put_back.get("computer") != computer and not take_over:
        raise TransferError(f"{run} is being put back from {mid_put_back.get('computer')} "
                            f"since {_when(mid_put_back.get('at'))}.")
    out = state.get("out")
    held_elsewhere = isinstance(out, dict) and out.get("computer") != computer
    if held_elsewhere and not take_over:
        raise TransferError(f"{run} is in use on {out.get('computer')} by {out.get('initials')} "
                            f"since {_when(out.get('at'))}.")
    local_root.mkdir(parents=True, exist_ok=True)
    local_state = None
    keep_local = set()
    replace_saved = []
    pending = []
    if final.exists():
        local_state = read_local_state(final)
        if local_state is None:
            raise TransferError(f"{final} already exists and the app did not make it.")
        if local_state.get("project_id") != state.get("project_id"):
            raise TransferError(f"{final} holds a different project with the same folder name.")
        pending = changed_files(final, local_state, include_quiet=True)
        if pending and saved_beside_covers(final, local_state):
            replace_saved = pending
        elif pending:
            real_work = [rel for rel in pending if rel not in QUIET_RELS]
            if real_work and local_state["server_rev"] != state["rev"]:
                raise TransferError(f"{run} has {len(real_work)} changed {_files_word(len(real_work))} on this "
                                    f"computer, and the server copy has also moved on. Use Put Back, which "
                                    f"keeps both.")
            # Work here is never overwritten. It goes back through Put Back.
            keep_local = set(pending) if local_state["server_rev"] == state["rev"] else set(real_work)
    _check_writable(source)
    base = dict(local_state["base"]) if local_state else {}
    to_copy = {rel: entry for rel, entry in listing.items() if not is_excluded(rel) and rel not in keep_local
               and (rel in replace_saved or base.get(rel, {}).get("sha256") != entry["sha256"]
                    or not (final / rel).is_file())}
    total = sum(entry["size"] for entry in to_copy.values())
    growth = 0 if final.exists() else sum(FRAME_FOLDER_MARK in rel for rel in listing) * GROWTH_PER_FRAME_BYTES
    _check_space(local_root, total + growth)
    action = "take_over" if held_elsewhere else "bring_in"
    claim = _claim(source, state, computer, initials, action)
    fresh = not final.exists()
    staging = local_root / f".incoming_{run}_{uuid.uuid4().hex[:8]}"
    done = [0]

    def on_chunk(count):
        """Report progress for one copied chunk."""
        done[0] += count
        if progress:
            progress(done[0], total, f"Bringing in {run}")

    mapper = PathMap(state["source"]["project_dir"], final)
    try:
        staging.mkdir(parents=True)
        for rel, entry in sorted(to_copy.items()):
            copy_verified(source / rel, staging / rel, entry["sha256"], on_chunk)
        for rel in PATH_BEARING_RELS:
            if rel in to_copy:
                rewrite_file(staging / rel, rel, mapper.to_local)
        if PROJECT_JSON_REL in to_copy and PROJECT_JSON_REL in pending:
            projects.carry_local_notes(final, staging)
        if fresh:
            rebuild_image_links(staging, paths_root=final)
            new_base = {rel: _base_entry(entry["sha256"], _stat_pair(staging / rel)) for rel, entry in to_copy.items()}
            write_json_atomic(staging / LOCAL_STATE, {
                "format": FORMAT, "project_id": state.get("project_id"),
                "source_project_dir": state["source"]["project_dir"], "server_rev": state["rev"],
                "brought_in_at": projects.now_ast(), "computer": computer, "holding": True, "base": new_base})
            _rename_folder(staging, final)
        else:
            if replace_saved:
                _set_aside_local_only(final, replace_saved, listing)
            journal = {"staging": str(staging), "server_rev": state["rev"], "computer": computer,
                       "files": {rel: entry["sha256"] for rel, entry in to_copy.items()}}
            with _journal_lock:
                write_json_atomic(final / BRING_IN_JOURNAL, journal)
                _apply_bring_in(final, journal)
    except (TransferError, ValueError, OSError) as exc:
        _undo_claim(source, claim, state, action)
        if not (final / BRING_IN_JOURNAL).exists():
            shutil.rmtree(staging, ignore_errors=True)
        message = str(exc)
        if isinstance(mid_put_back, dict) and mid_put_back.get("computer") != computer:
            message += (f" {mid_put_back.get('computer')} stopped partway through putting this project back, "
                        f"so run Put Back on that computer first.")
        raise TransferError(message) from exc
    projects.stamp_access(final, initials, action, f"bringing {run} in to {computer}")
    return {"run": run, "copied": len(to_copy), "bytes": total}


def _sendable(rel, full, mapper, staging):
    """Prepare one file to send, and record it only if it held still while being read.

    Parameters:
        rel: the file's path relative to the project.
        full: the file on this computer.
        mapper: the PathMap for this project.
        staging: a temporary folder for files whose paths need rewriting.
    Returns:
        tuple: (path to send, its SHA-256, its new base entry).
    Raises:
        TransferError: when the file changed during the read, or cannot be rewritten.
    """
    before = _stat_pair(full)
    send_path = Path(full)
    if rel in PATH_BEARING_RELS:
        send_path = Path(staging) / rel
        send_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            rewrite_file(full, rel, mapper.to_server, dest=send_path)
        except ValueError as exc:
            raise TransferError(str(exc)) from exc
    sha = sha256_file(send_path)
    if _stat_pair(full) != before:
        raise TransferError(f"{Path(full).name} changed while it was being read. Close the annotator and "
                            f"try Put Back again.")
    return send_path, sha, _base_entry(sha, before)


def _save_conflict_copy(server_folder, run, project, local_state, mapper, computer, initials, reason):
    """Save this computer's changed files beside the server project, overwriting nothing.

    A second attempt with no new work reuses the earlier copy. Afterward this computer
    can bring in the server's copy or remove its own, because its work is safe.

    Returns:
        dict: the Put Back result, with conflict True and the folder holding the work.
    """
    if saved_beside_covers(project, local_state):
        saved = local_state["saved_beside"]
        return {"run": run, "conflict": True, "conflict_folder": saved["folder"], "reason": reason,
                "changed": len(saved.get("files") or {})}
    source = server_projects_dir(server_folder) / run
    stamp = datetime.now(projects.AST).strftime("%Y%m%d-%H%M%S")
    safe_computer = "".join(ch if ch.isalnum() else "_" for ch in computer)
    dest = source / "conflicts" / f"{stamp}_{safe_computer}_{uuid.uuid4().hex[:6]}"
    base = local_state.get("base") or {}
    saved_files = {}
    with tempfile.TemporaryDirectory(prefix="vega_conflict_") as staging:
        for rel, full in _walk_project(project):
            if _unchanged(full, base.get(rel)):
                continue
            send_path, sha, entry = _sendable(rel, full, mapper, staging)
            copy_verified(send_path, dest / rel, sha, read_back=True)
            saved_files[rel] = [entry["size"], entry["mtime_ns"]]
    write_json_atomic(dest / "conflict.json", {
        "run": run, "computer": computer, "initials": initials, "at": projects.now_ast(),
        "reason": reason, "base_rev": local_state["server_rev"], "files": sorted(saved_files)})
    local_state.update({"holding": False,
                        "saved_beside": {"folder": str(dest), "at": projects.now_ast(), "files": saved_files}})
    write_json_atomic(Path(project) / LOCAL_STATE, local_state)
    return {"run": run, "conflict": True, "conflict_folder": str(dest), "reason": reason,
            "changed": len(saved_files)}


def _send_work(source, project, run, listing, local_state, mapper, initials, computer, keep_holding, token, progress):
    """Send the changed files, then raise the server's revision. See put_back for the parameters."""
    projects.stamp_access(project, initials, "put_back", f"putting {run} back from {computer}")
    base = dict(local_state["base"])
    outgoing = []
    with tempfile.TemporaryDirectory(prefix="vega_putback_") as staging:
        for rel, full in _walk_project(project):
            if _unchanged(full, base.get(rel)):
                continue
            send_path, sha, entry = _sendable(rel, full, mapper, staging)
            base[rel] = entry
            if listing.get(rel, {}).get("sha256") != sha:
                outgoing.append((rel, send_path, sha, send_path.stat().st_size))
        total = sum(item[3] for item in outgoing)
        _check_space(source, total)
        stamp = datetime.now(projects.AST).strftime("%Y%m%d-%H%M%S")
        done = [0]

        def on_chunk(count):
            """Report progress for one sent chunk."""
            done[0] += count
            if progress:
                progress(done[0], total, f"Putting back {run}")

        for rel, send_path, sha, _size in outgoing:
            existing = source / rel
            if existing.is_file():
                copy_verified(existing, source / "_superseded" / stamp / "before_put_back" / rel)
            copy_verified(send_path, existing, sha, on_chunk, read_back=True)
            listing[rel] = {"size": existing.stat().st_size, "sha256": sha}
    work = projects.count_work(project)
    write_json_atomic(source / CHECKSUMS, listing)
    # A take over that arrived while the files moved keeps its claim. That computer's copy
    # now lags the server, so its own Put Back saves beside the project and nothing is lost.
    latest = read_server_state(source)
    holder = latest.get("out")
    taken_over = isinstance(holder, dict) and holder.get("computer") != computer
    holding = keep_holding and not taken_over
    latest["rev"] += 1
    latest["put_back"] = None
    if not taken_over:
        latest["out"] = {"computer": computer, "initials": initials, "at": projects.now_ast()} if keep_holding else None
    latest.setdefault("history", []).append(
        {"at": projects.now_ast(), "action": "put_back", "computer": computer, "initials": initials,
         "files": len(outgoing), "frames_left": work["frames_left"], "masks": work["n_masks"], "token": token})
    journal = {"token": token, "rev": latest["rev"], "base": base, "holding": holding, "at": projects.now_ast()}
    with _journal_lock:
        write_json_atomic(project / PUT_BACK_JOURNAL, journal)
        write_json_atomic(source / SERVER_STATE, latest)
        _apply_put_back(project, journal)
    return {"run": run, "changed": len(outgoing), "bytes": total, "rev": latest["rev"], "conflict": False}


def put_back(server_folder, run, initials, computer, keep_holding=False, progress=None, local_root=None):
    """Send this computer's work to the server folder.

    Parameters:
        server_folder: the VEGA_Annotator folder on the server.
        run: the run_* folder name.
        initials: who is putting it back.
        computer: this computer's name.
        keep_holding: keep the project marked as held by this computer.
        progress: optional callable (bytes done, bytes total, message).
        local_root: where working copies live; defaults to paths.projects_dir().
    Returns:
        dict: run, changed (files sent), bytes, rev, and conflict (bool), with
        conflict_folder and reason when the work was saved beside the project.
    Raises:
        TransferError: with a sentence saying why it cannot go ahead.
    """
    _require_run(run)
    local_root = Path(local_root) if local_root else paths.projects_dir()
    project = local_root / run
    if not project.is_dir():
        raise TransferError(f"{run} is not on this computer.")
    if read_local_state(project) is None:
        raise TransferError(f"{project} has no local record, so the app cannot tell what changed.")
    source = server_projects_dir(server_folder) / run
    if not source.is_dir():
        raise TransferError(f"This server folder does not hold {run}, so its work stays on this computer. "
                            f"Check the chosen folder, or publish the project again from the mainframe.")
    finish_interrupted(project, source)
    local_state = read_local_state(project)
    state = read_server_state(source)
    listing = read_checksums(source)
    _check_writable(source)
    if not changed_files(project, local_state, include_quiet=True):
        # Nothing here differs from the last sync, so there is nothing to save, whatever the server did.
        if not keep_holding:
            _release(source, computer)
            local_state["holding"] = False
            write_json_atomic(project / LOCAL_STATE, local_state)
        return {"run": run, "changed": 0, "bytes": 0, "rev": state["rev"], "conflict": False}
    mapper = PathMap(state["source"]["project_dir"], project)
    out = state.get("out")
    marker = state.get("put_back")
    if isinstance(out, dict) and out.get("computer") != computer:
        return _save_conflict_copy(server_folder, run, project, local_state, mapper, computer, initials,
                                   f"{out.get('computer')} took this project over on {_when(out.get('at'))}.")
    if isinstance(marker, dict) and marker.get("computer") != computer:
        return _save_conflict_copy(server_folder, run, project, local_state, mapper, computer, initials,
                                   f"{marker.get('computer')} started putting this project back on "
                                   f"{_when(marker.get('at'))}.")
    if state["rev"] != local_state["server_rev"]:
        return _save_conflict_copy(server_folder, run, project, local_state, mapper, computer, initials,
                                   "The server copy changed after this computer brought it in.")
    token = uuid.uuid4().hex
    marked = read_server_state(source)
    marked["put_back"] = {"computer": computer, "initials": initials, "at": projects.now_ast(), "token": token}
    write_json_atomic(source / SERVER_STATE, marked)
    try:
        return _send_work(source, project, run, listing, local_state, mapper, initials, computer,
                          keep_holding, token, progress)
    except BaseException:
        try:
            latest = read_server_state(source)
            if (latest.get("put_back") or {}).get("token") == token:
                latest["put_back"] = None
                write_json_atomic(source / SERVER_STATE, latest)
        except (TransferError, OSError):
            pass  # The connection is gone. The same computer can still run Put Back again.
        raise


def remove_local(run, computer, server_folder=None, local_root=None):
    """Delete this computer's working copy after its work is safe on the server.

    Parameters:
        run: the run_* folder name.
        computer: this computer's name.
        server_folder: the server folder when connected, to release the claim.
        local_root: where working copies live; defaults to paths.projects_dir().
    Returns:
        dict: run and removed True.
    Raises:
        TransferError: when the working copy holds work not yet on the server, or holds
            a claim that cannot be released with the server disconnected.
    """
    _require_run(run)
    local_root = Path(local_root) if local_root else paths.projects_dir()
    project = local_root / run
    if not project.is_dir():
        raise TransferError(f"{run} is not on this computer.")
    if read_local_state(project) is None:
        raise TransferError(f"{project} has no local record, so the app leaves it alone.")
    source = server_projects_dir(server_folder) / run if server_folder else None
    connected = source is not None and source.is_dir()
    finish_interrupted(project, source if connected else None)
    local_state = read_local_state(project)
    pending = changed_files(project, local_state, include_quiet=True)
    if pending and not saved_beside_covers(project, local_state):
        raise TransferError(f"{run} has {len(pending)} changed {_files_word(len(pending))} that the server "
                            f"does not have. Use Put Back first.")
    if connected:
        _release(source, computer)
    elif local_state.get("holding"):
        raise TransferError(f"{run} is marked as held by this computer, and the server folder cannot release "
                            f"it. Connect the server, or publish the project again from the mainframe.")
    shutil.rmtree(project)
    return {"run": run, "removed": True}
