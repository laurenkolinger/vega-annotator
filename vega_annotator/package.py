"""EXPORT FOR VEGA: write a project made from a folder as a folder VEGA can train on.

The folder is shaped like one of VEGA's Annotate Images sessions. VEGA's decision records
0001 and 0006 make such a session a Train Model source that never enters the Coverage
Matrix, STATS or the mask registry, so a label such as "bleached" is never counted as a
species. Lauren copies it into inprocess/_standalone/ on vicar-mainframe, where VEGA's
_list_standalone_exports lists it with its labels. It holds:

- data.yaml in VEGA's own form, naming every class in class id order, with its path set to
  where the folder goes on the mainframe;
- all_images/ as real image files, never links, one per exported frame, so the folder
  survives a zip file, a USB drive or a cloud drive; and all_labels/, class_map.json,
  export_manifest.csv, label_provenance.csv, export_log.txt and mask_status_log.jsonl;
- segmentations/ (the frame store and its overlays), previews/, and custom_imports/ with
  its manifest and working JPEGs; originals/ only when asked for, and never the app's own
  ledger of source files;
- session.json, checksums.json for every file, and README.txt for Lauren.

Each package is a new folder, <project id without run_>_<YYYYMMDD-HHMM>, so two hand-overs
never merge or overwrite each other. It is written under a hidden .partial name and renamed
only when complete, so a drive that fills or comes out leaves no package under its final name.
"""

import csv
import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from . import VERSION, folder_projects, labels, projects, settings, transfer
from .atomic import write_bytes_atomic, write_json_atomic
from .pathmap import MANIFEST_REL, STEP4_DIR

NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")  # VEGA's session-name rule
MAX_NAME_CHARS = 64
STAMP_FORMAT = "%Y%m%d-%H%M"
CREATED_FORMAT = "%Y-%m-%d %H:%M AST"  # session.json's created, as VEGA's standalone_start writes it
CLASH_SUFFIXES = range(2, 100)
LONGEST_CLASH_SUFFIX = "_99"
DEFAULT_BASE = "project"
PARTIAL_SUFFIX = ".partial"
MAINFRAME_STANDALONE_ROOT = "/mnt/rip/vicarius_drive/vicarius/modules/reef_point_seg/inprocess/_standalone"
REQUIRED_RECORDS = ("class_map.json", "export_manifest.csv")
OPTIONAL_RECORDS = ("label_provenance.csv", "export_log.txt", "mask_status_log.jsonl")
COPIED_FOLDERS = ("segmentations", "previews")
IMPORTS_FOLDER = "custom_imports"
ORIGINALS_FOLDER = "originals"
SET_ASIDE_FOLDER = "_superseded"  # unreadable uploads VEGA moved aside; never training data
CHECKSUMS = "checksums.json"
SPACE_MARGIN_BYTES = 64 * 1024 * 1024
DEFAULT_BLOCK_BYTES = 4096
README_STEPS = ("Copy this folder, unchanged, into inprocess/_standalone/ on vicar-mainframe. "
                "Train it in its own training project with TRAIN ON THIS. "
                "Never pick it as the source inside an existing project, which would overwrite "
                "that project's training split.")


class PackageError(transfer.TransferError):
    """A package that cannot be written, with a sentence the person can act on.

    It is a TransferError, so the home screen's job board shows the sentence alone.
    """


def _name_for(base, stamp, suffix=""):
    """Join a project id and a time stamp into a name VEGA accepts, at most 64 characters.

    The id is cut, never the stamp, so two exports a minute apart keep two names.

    Raises:
        PackageError: when the result would break VEGA's session-name rule.
    """
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_-")
    room = MAX_NAME_CHARS - len(stamp) - len(suffix) - 1
    name = f"{clean[:room].rstrip('_-') or DEFAULT_BASE}_{stamp}{suffix}"
    if not NAME_RE.match(name):
        raise PackageError(f"{name} is not a name VEGA accepts, so no package was written.")
    return name


def _base(project_dir):
    """Return a project's id without run_, from its folder name."""
    run = Path(project_dir).name
    return run[len(transfer.RUN_PREFIX):] if run.startswith(transfer.RUN_PREFIX) else run


def package_name(project_dir, now=None) -> str:
    """Name a package: <project id without run_>_<YYYYMMDD-HHMM>, at most 64 characters.

    Parameters:
        project_dir: the run_* folder.
        now: the time to stamp; defaults to now in AST.
    Returns:
        str: the name, which matches VEGA's session-name rule ^[A-Za-z0-9._-]{1,64}$.
    Raises:
        PackageError: when project_dir names no folder.
    """
    if not Path(project_dir).name:
        raise PackageError("Choose a project to export.")
    return _name_for(_base(project_dir), (now or datetime.now(projects.AST)).strftime(STAMP_FORMAT))


def _size_text(count):
    """Return a byte count the way a person reads it."""
    return f"{count / 1e9:.1f} GB" if count >= 1e9 else f"{max(count / 1e6, 0.1):.1f} MB"


def _inside(path, folder):
    """Return True when path is folder or lies inside it."""
    path, folder = Path(path).resolve(), Path(folder).resolve()
    return path == folder or folder in path.parents


def _exported_rows(step4):
    """Return the export manifest's rows for exported frames.

    Raises:
        PackageError: when the manifest cannot be read, or names a file outside its folder.
    """
    path = Path(step4).parent / MANIFEST_REL
    if not path.is_file():
        return []
    try:
        with open(path, newline="", encoding="utf-8") as handle:
            rows = [row for row in csv.DictReader(handle) if row.get("outcome") in transfer.EXPORTED_OUTCOMES]
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise PackageError(f"export_manifest.csv cannot be read ({exc}), so no package was written.") from exc
    for row in rows:
        for column in ("image_file", "label_file"):
            name = row.get(column) or ""
            if not transfer.safe_rel(name) or "/" in name:
                raise PackageError(f"export_manifest.csv lists {name!r} as a {column}, which is not a plain "
                                   f"file name, so no package was written.")
    return rows


def _class_map(step4):
    """Read class_map.json and check its class ids run 0, 1, 2 with no gap.

    Returns:
        dict: {label: class id}.
    Raises:
        PackageError: when the file is missing, unreadable, or has a gap.
    """
    path = Path(step4) / "class_map.json"
    try:
        class_map = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise PackageError(f"class_map.json cannot be read ({exc}), so no package was written.") from exc
    whole = isinstance(class_map, dict) and all(
        isinstance(value, int) and not isinstance(value, bool) for value in class_map.values())
    if not whole or sorted(class_map.values()) != list(range(len(class_map))):
        raise PackageError("class_map.json does not number its labels 0, 1, 2 and so on, so training "
                           "would refuse the package. No package was written.")
    return class_map


def _image_file(step4, name):
    """Return the image file an exported frame's all_images/ entry stands for.

    VEGA's export makes all_images/ hold links to the working JPEGs; a link is followed to
    its file, and a missing link falls back to the working JPEG itself.

    Raises:
        PackageError: when neither holds the image.
    """
    for candidate in (Path(step4) / "all_images" / name, Path(step4) / IMPORTS_FOLDER / "raw" / name):
        if candidate.is_file():
            return Path(os.path.realpath(candidate))
    raise PackageError(f"{name} is exported, and its image is missing from {IMPORTS_FOLDER}/raw, so the "
                       f"package would not train. Put the image back, then export again.")


def _count_label_lines(path, class_ids):
    """Count a label file's lines, checking each starts with a class id the class map holds.

    Raises:
        PackageError: when the file cannot be read or a line names an unknown class.
    """
    try:
        lines = [line.split() for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeDecodeError) as exc:
        raise PackageError(f"{Path(path).name} cannot be read ({exc}), so no package was written.") from exc
    for fields in lines:
        if not fields[0].isdigit() or int(fields[0]) not in class_ids:
            raise PackageError(f"{Path(path).name} names class {fields[0]}, which class_map.json does not hold, "
                               f"so training would refuse the package. No package was written.")
    return len(lines)


def _tree(folder, prefix, leave_out=()):
    """List a folder's files for the package, without links, hidden files or temporary files.

    Parameters:
        folder: the folder to copy.
        prefix: its path inside the package.
        leave_out: names of subfolders and files to leave out.
    Returns:
        list: (source Path, package path) pairs, in a stable order.
    """
    found = []
    for current, dirs, files in os.walk(folder):
        dirs[:] = sorted(name for name in dirs if not name.startswith(".") and name not in leave_out
                         and not os.path.islink(os.path.join(current, name)))
        for name in sorted(files):
            full = Path(current) / name
            if name.startswith(".") or name in leave_out or name.endswith(transfer.EXCLUDED_SUFFIXES) \
                    or full.is_symlink():
                continue
            found.append((full, f"{prefix}/{full.relative_to(folder).as_posix()}"))
    return found


def _plan(step4, rows, class_map, include_originals):
    """List every file to copy, and count the label lines, checking the export is whole.

    Parameters:
        step4: the project's Place + Segment folder.
        rows: the export manifest's exported rows.
        class_map: {label: class id}.
        include_originals: whether custom_imports/originals/ goes too.
    Returns:
        tuple: ([(source Path, package path)], the number of label lines).
    Raises:
        PackageError: when an exported frame's image or label file is missing or wrong.
    """
    step4 = Path(step4)
    class_ids = set(class_map.values())
    files, masks = [], 0
    for row in rows:
        files.append((_image_file(step4, row["image_file"]), f"all_images/{row['image_file']}"))
        label = step4 / "all_labels" / row["label_file"]
        if not label.is_file():
            raise PackageError(f"{row['image_file']} is exported, and its label file {row['label_file']} is "
                               f"missing, so the package would not train. Export that batch again.")
        masks += _count_label_lines(label, class_ids)
        files.append((label, f"all_labels/{row['label_file']}"))
    for name in REQUIRED_RECORDS + OPTIONAL_RECORDS:
        if (step4 / name).is_file():
            files.append((step4 / name, name))
        elif name in REQUIRED_RECORDS:
            raise PackageError(f"The project has no {name}, so no package was written.")
    for folder in COPIED_FOLDERS:
        files.extend(_tree(step4 / folder, folder))
    leave_out = (SET_ASIDE_FOLDER, folder_projects.SOURCES_LEDGER_NAME)
    if not include_originals:
        leave_out += (ORIGINALS_FOLDER,)
    files.extend(_tree(step4 / IMPORTS_FOLDER, IMPORTS_FOLDER, leave_out))
    return files, masks


def _data_yaml(class_map, name):
    """Write data.yaml as VEGA's update_data_yaml does, with the path of the folder on the mainframe."""
    ordered = sorted(class_map.items(), key=lambda item: item[1])
    lines = [f"path: {MAINFRAME_STANDALONE_ROOT}/{name}", "train: all_images", "val: all_images",
             f"nc: {len(ordered)}", "names:"] + [f"  {class_id}: {label}" for label, class_id in ordered]
    return "\n".join(lines) + "\n"


def _block_size(folder):
    """Return the destination disk's block size, which every file is rounded up to."""
    try:
        return os.statvfs(folder).f_frsize or DEFAULT_BLOCK_BYTES
    except (OSError, AttributeError):
        return DEFAULT_BLOCK_BYTES


def _check_space(destination, sizes):
    """Refuse a package the destination has no room for, before anything is written.

    Parameters:
        destination: the folder the package goes into.
        sizes: the byte count of every file.
    Raises:
        PackageError: when the drive lacks room for the files, each rounded up to its block
            size, plus a margin.
    """
    block = _block_size(destination)
    needed = sum(-(-size // block) * block for size in sizes) + SPACE_MARGIN_BYTES
    free = shutil.disk_usage(destination).free
    if free < needed:
        raise PackageError(f"The package needs {_size_text(needed)} free in {destination}, which has "
                           f"{_size_text(free)}, so nothing was written. Free some space or choose another "
                           f"folder.")


def _final_name(destination, project_dir, now):
    """Return the package's name, with _2 to _99 when an earlier export took it this minute."""
    base, stamp = _base(project_dir), now.strftime(STAMP_FORMAT)
    for suffix in [""] + [f"_{number}" for number in CLASH_SUFFIXES]:
        name = _name_for(base, stamp, suffix)
        if not os.path.lexists(Path(destination) / name):
            return name
    raise PackageError(f"{destination} already holds packages named {_name_for(base, stamp)} to "
                       f"{_name_for(base, stamp, LONGEST_CLASH_SUFFIX)}. Choose another folder.")


def export_for_vega(project_dir, destination, include_originals=False, progress=None, now=None) -> dict:
    """Write a project made from a folder as a package VEGA can train on.

    Parameters:
        project_dir: the run_* folder.
        destination: the folder to write the package into, such as a USB drive.
        include_originals: also copy custom_imports/originals/, the images as they came in.
        progress: optional callable(done bytes, total bytes, message).
        now: the time to name the package by; defaults to now in AST.
    Returns:
        dict: folder (the package's path, as text), frames (exported frames), masks (label
        lines, one per accepted mask), and bytes (the package's size).
    Raises:
        PackageError: with a sentence saying why, and nothing written under the package's
            name, when the project did not come from a folder, the annotator has it open, no
            frame is exported, the export is not whole, the destination is not a writable
            folder outside the project, the drive lacks room, or a copy fails.
    """
    project_dir = Path(project_dir)
    try:
        project = projects.read_project(project_dir)
    except ValueError as exc:
        raise PackageError(f"{project_dir.name} cannot be read ({exc}), so no package was written.") from exc
    if not projects.has_custom_labels(project):
        raise PackageError(f"{project_dir.name} came from VEGA. EXPORT FOR VEGA works on projects made from a "
                           f"folder of images.")
    if not isinstance(destination, (str, os.PathLike)) or not Path(destination).is_dir():
        raise PackageError(f"{destination} is not a folder. Choose the folder to write the package into.")
    destination = Path(destination)
    if _inside(destination, project_dir):
        raise PackageError("Choose a folder outside the project to write the package into.")
    if projects.open_in_annotator(project_dir):
        raise PackageError("Close the annotator on this project first, so the package holds its saved work.")
    step4 = project_dir / STEP4_DIR
    rows = _exported_rows(step4)
    if not rows:
        raise PackageError("No frame is exported yet. Label images in the annotator and export a batch, "
                           "then export for VEGA.")
    class_map = _class_map(step4)
    files, masks = _plan(step4, rows, class_map, include_originals)
    now = now or datetime.now(projects.AST)
    name = _final_name(destination, project_dir, now)
    try:
        label_list = labels.current(project_dir)
    except labels.LabelError as exc:
        raise PackageError(str(exc)) from exc
    initials = str(project.get("initials") or projects.reviewer_for(project))
    computer = settings.computer_name()
    session = {"session": name, "name": project.get("name") or project_dir.name,
               "created": now.strftime(CREATED_FORMAT), "initials": initials, "reviewer": initials,
               "labels": label_list, "frames_exported": len(rows), "masks": masks,
               "app_version": VERSION, "computer": computer}
    readme = (f"VEGA Annotator package {name}\n"
              f"Project: {session['name']}, run {project_dir.name}, started by {initials}.\n"
              f"Written on {computer} with VEGA Annotator {VERSION}, {session['created']}.\n"
              f"Labels: {', '.join(label_list)}.\n"
              f"Frames exported: {len(rows)}. Masks: {masks}.\n\n"
              f"{README_STEPS}\n")
    generated = {"data.yaml": _data_yaml(class_map, name).encode("utf-8"),
                 "session.json": (json.dumps(session, indent=2) + "\n").encode("utf-8"),
                 "README.txt": readme.encode("utf-8")}
    try:
        sizes = [source.stat().st_size for source, _rel in files] + [len(data) for data in generated.values()]
    except OSError as exc:
        raise PackageError(f"{Path(exc.filename or '').name or 'A project file'} went missing, so no package "
                           f"was written. Export for VEGA again.") from exc
    _check_space(destination, sizes)
    partial = destination / f".{name}{PARTIAL_SUFFIX}"
    total = sum(sizes)
    copied = {"bytes": 0}

    def advance(count):
        """Report the bytes written so far."""
        copied["bytes"] += count
        if progress:
            progress(copied["bytes"], total, "Writing the package")

    try:
        if partial.exists():
            shutil.rmtree(partial)
        partial.mkdir()
        checksums = {}
        for source, rel in files:
            digest = transfer.copy_verified(source, partial / rel, progress=advance)
            checksums[rel] = {"size": (partial / rel).stat().st_size, "sha256": digest}
        for rel, data in generated.items():
            write_bytes_atomic(partial / rel, data)
            checksums[rel] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            advance(len(data))
        write_json_atomic(partial / CHECKSUMS, dict(sorted(checksums.items())))
        os.rename(partial, destination / name)
    except (OSError, transfer.TransferError) as exc:
        shutil.rmtree(partial, ignore_errors=True)
        leftover = (f"An unfinished copy named {partial.name} is left there; delete it."
                    if partial.exists() else "The unfinished copy was removed.")
        detail = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
        raise PackageError(f"Writing the package into {destination} stopped: {detail}. {leftover} Check that "
                           f"the drive has room and stays connected, then export again.") from exc
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    size = sum(entry["size"] for entry in checksums.values()) + (destination / name / CHECKSUMS).stat().st_size
    return {"folder": str(destination / name), "frames": len(rows), "masks": masks, "bytes": size}
