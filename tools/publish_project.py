#!/usr/bin/env python3
"""Copy a VEGA project into a server folder, read only on the project.

Module: VEGA Annotator / publish_project
Purpose: VEGA makes a project on the mainframe. This tool puts a copy of it where the
    laptop app can bring it in: <dest>/<run>/, the same folder layout VEGA uses, plus the
    two records the app needs (checksums.json and vega_annotator.json). It leaves out what
    the annotator never reads and what can be rebuilt: the point count table, the image
    links, the previews and overlays, and backups. The record lists each one.
Inputs: --project  a run_* folder name under --root, or a full path to one
        --root     VEGA's inprocess/ folder (the default is the mainframe's)
        --dest     the server folder's projects/ folder
        --by       initials of the person publishing
        --update   replace the server copy's files with VEGA's, when no computer holds it
                   and it holds no laptop work VEGA has not taken back
        --replace-laptop-work  also replace laptop work VEGA has not taken back. The old
                   copy moves to _superseded, and the person running this answers for it
        --force    publish even though an annotator is running on the project
        --dry-run  list what would happen and write nothing
Outputs: <dest>/<run>/ and one line appended to <dest>/publish_log.csv.
Run: it needs Python 3.8 and nothing else, so it can run from anywhere:
    ssh vicar-mainframe python3 - --project run_26-10-04_AT --by LO < tools/publish_project.py
"""

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone

FORMAT = 1
DEFAULT_ROOT = "/mnt/rip/vicarius_drive/vicarius/modules/reef_point_seg/inprocess"
VEGA_REPO = "/mnt/rip/vicarius_drive/vicarius/modules/reef_point_seg/github_repo"
STEP4_DIR = "step4test_combinedAnnotate"
SEGMENTATIONS_REL = STEP4_DIR + "/segmentations/segmentations.json"
SERVER_STATE = "vega_annotator.json"
CHECKSUMS = "checksums.json"
AST = timezone(timedelta(hours=-4))
COPY_CHUNK_BYTES = 4 * 1024 * 1024
FREE_SPACE_MARGIN_BYTES = 2 * 1024 ** 3
SAVED_FILE_MODE = 0o664  # mkstemp's owner-only default would hide a record from the server's sync.

# What travels. Everything else stays on the mainframe and is listed in the record.
INCLUDED_TOP_FILES = ("project.json",)
INCLUDED_DIRS = ("step2_recodeSpecies", "step3_chooseImages", STEP4_DIR)
EXCLUDED_DIR_NAMES = {"_superseded", "all_images", "previews", "overlays_reviewed", "__pycache__", "conflicts"}
EXCLUDED_FILE_NAMES = {"all_points_recoded.csv", SERVER_STATE, CHECKSUMS, ".DS_Store"}
EXCLUDED_SUFFIXES = (".tmp", ".lock")
LEFT_ON_MAINFRAME = ("step1_makeAllPoints/", "step2_recodeSpecies/all_points_recoded.csv",
                     STEP4_DIR + "/all_images/", STEP4_DIR + "/previews/",
                     STEP4_DIR + "/segmentations/overlays_reviewed/", STEP4_DIR + "/_superseded/",
                     "step6_trainModel/", "step7_evaluateModel/", "step8_inference/")


class PublishError(Exception):
    """A reason to stop, in a sentence the person running the tool can act on."""


def now_ast():
    """Return the current time as VEGA stamps it: ISO 8601 seconds, in AST."""
    return datetime.now(AST).isoformat(timespec="seconds")


def is_travelling(rel):
    """Return True when a project file goes to the server folder.

    Parameters:
        rel: a path relative to the project, with forward slashes.
    Returns:
        bool: whether the file is copied.
    """
    parts = rel.split("/")
    name = parts[-1]
    if any(part in EXCLUDED_DIR_NAMES for part in parts[:-1]):
        return False
    if name in EXCLUDED_FILE_NAMES or name.endswith(EXCLUDED_SUFFIXES) or name.startswith("._"):
        return False
    return rel in INCLUDED_TOP_FILES or parts[0] in INCLUDED_DIRS


def list_files(project):
    """List the project's travelling files.

    Parameters:
        project: the run_* folder.
    Returns:
        list: (relative path, full path) pairs. A symbolic link to a file is followed
        when it stays inside the project, and left out otherwise, since the app never
        needs what lies outside.
    """
    found = []
    project_real = os.path.realpath(project)
    for folder, dirs, files in os.walk(project):
        dirs[:] = [name for name in dirs if name not in EXCLUDED_DIR_NAMES]
        for name in files:
            full = os.path.join(folder, name)
            rel = os.path.relpath(full, project).replace(os.sep, "/")
            if not is_travelling(rel):
                continue
            if os.path.islink(full) and not os.path.realpath(full).startswith(project_real + os.sep):
                continue
            found.append((rel, full))
    return sorted(found)


def validate_project(project):
    """Check the project before copying anything.

    Parameters:
        project: the run_* folder.
    Returns:
        dict: the parsed project.json.
    Raises:
        PublishError: when the project cannot be used, saying why.
    """
    path = os.path.join(project, "project.json")
    try:
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError) as exc:
        raise PublishError("%s cannot be read: %s" % (path, exc))
    if not isinstance(record, dict):
        raise PublishError("%s does not hold a project record." % path)
    target = ((record.get("steps") or {}).get("3") or {}).get("config") or {}
    if not target.get("target_species"):
        raise PublishError("%s has no target labels. Run Choose Images in VEGA first." % os.path.basename(project))
    routed = os.path.join(project, STEP4_DIR, "routed_input", "raw")
    if not os.path.isdir(routed) or not os.listdir(routed):
        raise PublishError("%s has no routed frames. Open Place + Segment in VEGA once first, so VEGA "
                           "routes the frames." % os.path.basename(project))
    store = os.path.join(project, SEGMENTATIONS_REL)
    if os.path.exists(store):
        try:
            with open(store, encoding="utf-8") as handle:
                parsed = json.load(handle)
        except (OSError, ValueError) as exc:
            raise PublishError("%s cannot be read: %s" % (store, exc))
        if not isinstance(parsed, dict):
            raise PublishError("%s does not hold frames." % store)
    return record


def annotator_on_project(project, proc_root="/proc"):
    """Find a running VEGA annotator working on this project, by its environment.

    Parameters:
        project: the run_* folder.
        proc_root: the /proc folder, passed in so tests can fake it.
    Returns:
        str: the process id, or "" when none is found or /proc does not exist.
    """
    export_dir = os.path.join(os.path.realpath(project), STEP4_DIR)
    try:
        pids = [name for name in os.listdir(proc_root) if name.isdigit()]
    except OSError:
        return ""
    for pid in pids:
        try:
            with open(os.path.join(proc_root, pid, "environ"), "rb") as handle:
                environ = handle.read()
            with open(os.path.join(proc_root, pid, "cmdline"), "rb") as handle:
                command = handle.read()
        except OSError:
            continue
        if b"app.py" in command and (b"TCRMP_EXPORT_DIR=" + export_dir.encode()) in environ:
            return pid
    return ""


def frame_hash(frame):
    """Return the hash of one frame's record without its machine-specific path.

    Parameters:
        frame: one entry of segmentations.json.
    Returns:
        str: a hex SHA-256. VEGA's later merge compares frames with this.
    """
    copy = {key: value for key, value in frame.items() if key != "image_path_abs"}
    return hashlib.sha256(json.dumps(copy, sort_keys=True).encode("utf-8")).hexdigest()


def frame_hashes(project):
    """Hash every frame in the project's store.

    Parameters:
        project: the run_* folder.
    Returns:
        dict: {frame file name: hash}; {} when the store does not exist yet.
    """
    path = os.path.join(project, SEGMENTATIONS_REL)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        return {name: frame_hash(frame) for name, frame in json.load(handle).items() if isinstance(frame, dict)}


def vega_commit(repo):
    """Return the VEGA commit the project was published from, or "" when git cannot say."""
    try:
        result = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, universal_newlines=True, check=False)
        return result.stdout.strip()
    except OSError:
        return ""


PRIOR_SCREENING_CODE = """
import csv, os, shutil, sys, tempfile
scripts, project, root, out = sys.argv[1:5]
sys.path.insert(0, scripts)
from _reefreview.screening import write_prior_screening
selected = os.path.join(project, "step3_chooseImages", "selected_frames.csv")
frames = []
if os.path.isfile(selected):
    with open(selected, newline="", encoding="utf-8") as handle:
        frames = [row["basename"] for row in csv.DictReader(handle) if row.get("basename")]
codes = os.path.join(project, "step2_recodeSpecies", "master_codes_recoded.csv")
with tempfile.TemporaryDirectory() as folder:
    shutil.copy2(os.path.join(project, "project.json"), folder)
    written = write_prior_screening(folder, frames, root, master_codes_csv=codes if os.path.isfile(codes) else None)
    shutil.copy2(written, out)
"""


def refresh_prior_screening(project, working, root, repo=VEGA_REPO):
    """Regenerate prior_screening.json with VEGA's own code, as VEGA does at each launch.

    This file shows the annotator what other projects already screened on these
    frames. VEGA's code writes it inside the folder it is handed, so it runs on a
    temporary folder, and the project stays untouched.

    Parameters:
        project: the run_* folder.
        working: the folder being published.
        root: VEGA's inprocess/ folder.
        repo: the VEGA checkout, holding the Python environment and the code.
    Returns:
        str: "" on success, or why the project's existing file was kept.
    """
    python = os.path.join(repo, "env", "bin", "python")
    if not os.path.isfile(python):
        return "VEGA's Python environment was not found, so the existing file was kept."
    out = os.path.join(working, STEP4_DIR, "prior_screening.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    result = subprocess.run([python, "-c", PRIOR_SCREENING_CODE, os.path.join(repo, "scripts"), project, root, out],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, check=False)
    if result.returncode != 0:
        return "VEGA could not refresh it, so the existing file was kept: " + result.stdout.strip()[-300:]
    return ""


def copy_with_hash(src, dest):
    """Copy one file through a temporary name, returning its size and hash.

    Parameters:
        src: the file to read.
        dest: the destination path.
    Returns:
        tuple: (size in bytes, hex SHA-256).
    """
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = "%s.%s.vegatmp" % (dest, uuid.uuid4().hex[:8])
    digest = hashlib.sha256()
    size = 0
    try:
        with open(src, "rb") as reader, open(tmp, "wb") as writer:
            while True:
                chunk = reader.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return size, digest.hexdigest()


def write_json(path, payload):
    """Write a JSON file through a temporary name."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".publish.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, SAVED_FILE_MODE)
    os.replace(tmp, path)


def append_log(dest, row):
    """Add one row to publish_log.csv, writing the header on first use.

    Parameters:
        dest: the projects/ folder.
        row: a dict with the fields below.
    Returns:
        None.
    """
    path = os.path.join(dest, "publish_log.csv")
    fields = ["at", "by", "run", "rev", "files", "bytes", "vega_commit"]
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        if new:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})


def holds_laptop_work(state):
    """Return True when the server copy has a Put Back newer than its last publish.

    Parameters:
        state: the server copy's vega_annotator.json.
    Returns:
        bool: True when replacing the copy would drop labels VEGA has not taken back.
    """
    newest_first = reversed(state.get("history") or [])
    for entry in newest_first:
        if isinstance(entry, dict) and entry.get("action") == "publish":
            return False
        if isinstance(entry, dict) and entry.get("action") == "put_back":
            return True
    return False


def publish(project, dest, by, update=False, force=False, dry_run=False, repo=VEGA_REPO, proc_root="/proc",
            root=DEFAULT_ROOT, replace_laptop_work=False):
    """Copy one project to the server folder.

    Parameters:
        project: the run_* folder.
        dest: the server folder's projects/ folder.
        by: initials of the person publishing.
        update: replace an existing server copy that no computer holds.
        force: go ahead even though an annotator runs on the project.
        dry_run: report and write nothing.
        repo: the VEGA checkout, for the commit record.
        proc_root: the /proc folder, passed in so tests can fake it.
        root: VEGA's inprocess/ folder, for the prior screening refresh.
        replace_laptop_work: replace a copy holding laptop work VEGA has not taken back.
    Returns:
        dict: run, files, bytes, rev, dest, and screening_note.
    Raises:
        PublishError: when it cannot go ahead, saying why.
    """
    project = os.path.abspath(project)
    run = os.path.basename(project.rstrip("/"))
    if not run.startswith("run_"):
        raise PublishError("%s is not a run_* project folder." % project)
    record = validate_project(project)
    pid = annotator_on_project(project, proc_root)
    if pid and not force:
        raise PublishError("An annotator (process %s) is running on %s. Close it first, so its "
                           "last edits are saved, or pass --force." % (pid, run))
    target = os.path.join(dest, run)
    previous_rev = 0
    if os.path.exists(target):
        if not update:
            raise PublishError("%s is already in the server folder. Pass --update to replace it." % run)
        try:
            with open(os.path.join(target, SERVER_STATE), encoding="utf-8") as handle:
                existing = json.load(handle)
        except (OSError, ValueError) as exc:
            raise PublishError("%s's server record cannot be read: %s" % (run, exc))
        if existing.get("out"):
            raise PublishError("%s is in use on %s. Have that computer put it back first."
                               % (run, existing["out"].get("computer")))
        if holds_laptop_work(existing) and not replace_laptop_work:
            raise PublishError("%s's server copy holds laptop work that VEGA has not taken back. Publishing "
                               "would replace it with VEGA's older copy. Pass --replace-laptop-work only if "
                               "that work should be set aside." % run)
        previous_rev = int(existing.get("rev") or 0)
    files = list_files(project)
    total = sum(os.path.getsize(full) for _rel, full in files)
    if dry_run:
        return {"run": run, "files": len(files), "bytes": total, "rev": previous_rev + 1, "dest": target,
                "dry_run": True}
    os.makedirs(dest, exist_ok=True)
    if shutil.disk_usage(dest).free < total + FREE_SPACE_MARGIN_BYTES:
        raise PublishError("%s has too little free space for %.1f GB." % (dest, total / 1e9))
    working = os.path.join(dest, ".publishing_%s_%s" % (run, uuid.uuid4().hex[:8]))
    os.makedirs(working)
    try:
        listing = {}
        for rel, full in files:
            size, sha = copy_with_hash(full, os.path.join(working, *rel.split("/")))
            listing[rel] = {"size": size, "sha256": sha}
        screening_note = refresh_prior_screening(project, working, root, repo)
        screening_rel = STEP4_DIR + "/prior_screening.json"
        screening_path = os.path.join(working, *screening_rel.split("/"))
        if os.path.isfile(screening_path):
            with open(screening_path, "rb") as handle:
                data = handle.read()
            listing[screening_rel] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        write_json(os.path.join(working, CHECKSUMS), listing)
        write_json(os.path.join(working, SERVER_STATE), {
            "format": FORMAT, "project_id": record.get("id") or run[4:], "rev": previous_rev + 1,
            "source": {"project_dir": project, "repo_dir": record.get("repo_dir", ""),
                       "published_at": now_ast(), "published_by": by, "vega_commit": vega_commit(repo)},
            "left_on_mainframe": list(LEFT_ON_MAINFRAME),
            "prior_screening_note": screening_note,
            "frame_hashes": frame_hashes(project), "out": None,
            "history": [{"at": now_ast(), "action": "publish", "by": by, "files": len(files)}]})
        if os.path.exists(target):
            stamp = datetime.now(AST).strftime("%Y%m%d-%H%M%S")
            superseded = os.path.join(dest, "_superseded", stamp)
            os.makedirs(superseded, exist_ok=True)
            os.replace(target, os.path.join(superseded, run))
        os.replace(working, target)
    except BaseException:
        shutil.rmtree(working, ignore_errors=True)
        raise
    append_log(dest, {"at": now_ast(), "by": by, "run": run, "rev": previous_rev + 1, "files": len(files),
                      "bytes": total, "vega_commit": vega_commit(repo)})
    return {"run": run, "files": len(files), "bytes": total, "rev": previous_rev + 1, "dest": target,
            "screening_note": screening_note}


def main(argv=None):
    """Publish one project and print one summary line.

    Parameters:
        argv: the command line, without the program name.
    Returns:
        int: 0 on success, 1 when publishing stopped, with the reason on stderr.
    """
    parser = argparse.ArgumentParser(description="Copy a VEGA project into the server folder.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--dest", required=True)
    parser.add_argument("--by", required=True)
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--replace-laptop-work", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    project = args.project if os.path.isabs(args.project) else os.path.join(args.root, args.project)
    started = time.time()
    try:
        result = publish(project, args.dest, args.by, update=args.update, force=args.force,
                         dry_run=args.dry_run, root=args.root, replace_laptop_work=args.replace_laptop_work)
    except (PublishError, OSError) as exc:
        print("publish_project: %s" % exc, file=sys.stderr)
        return 1
    verb = "Would publish" if args.dry_run else "Published"
    print("%s %s: %d files, %.2f GB, revision %d, to %s (%.0f s)" % (
        verb, result["run"], result["files"], result["bytes"] / 1e9, result["rev"], result["dest"],
        time.time() - started))
    if result.get("screening_note"):
        print("Note: " + result["screening_note"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
