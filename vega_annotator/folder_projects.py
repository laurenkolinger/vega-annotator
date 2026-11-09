"""Start a VEGA project from any folder of images, and add images to it later.

A folder project is an ordinary VEGA project folder (run_*), so the home screen's cards,
OPEN, and VEGA's annotator work on it as they do on a project VEGA made. It holds:

- project.json, with steps 1 to 3 skipped, Step 3's target labels set to the project's own
  labels, label_set {kind: "custom"}, and origin {kind: "folder"};
- routed_input/ids/sam_click_prompts.json holding {} and an empty routed_input/raw/, so
  VEGA's annotator opens with no survey points;
- the label table and class map that labels.write makes;
- vega_annotator_local.json marked made_here, so its card works with no server folder.

Images come in through VEGA's own import code, custom_imports, exactly as VEGA's Import
Images route does (/api/import_images in the annotator's app.py): ingest_files keeps each
original and makes a working JPEG, reduced to 1920 px on its long edge when larger and
turned upright by its EXIF orientation; seg_dict_for makes its frame; and
record_in_project logs the import in project.json.

JPEG and PNG files come in as they are. VEGA's import takes only those two, so macOS sips
converts the rest first: HEIC, HEIF and WebP photos, which are lossy already, to JPEG at
quality 95, and TIFF, BMP and GIF images to PNG, which loses nothing. The converted copy
is the original VEGA keeps. A 16-bit grayscale PNG, including sips's PNG of a 16-bit TIFF,
comes in as an 8-bit copy scaled by its own maximum, because VEGA's import would clip it
nearly white. A photo whose name is long comes in under a short one, so no frame, preview
or package file passes macOS's 255-byte name limit.

A folder that is a VEGA Annotator package or a VEGA project is never a folder of photos:
the scan leaves it out, so exporting into the photo folder and adding that folder again
brings in nothing of the app's own.

The app keeps its own ledger, custom_imports/vega_annotator_sources.json, of each imported
source file's SHA-256 and the frames it made. A file already in the project, under any name
and from any folder, is skipped, so adding a folder again after an import stopped brings in
only the rest, and two identical files make one frame.

A new project's local record carries "creating": true from before its folder appears in the
projects folder until its first import ends. An import that stops partway leaves the mark, and
the next ADD IMAGES that ends clears it, so the home screen offers such a project only ADD
IMAGES and REMOVE.
"""

import importlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

from PIL import Image

from . import APP_NAME, VERSION, labels, paths, projects, settings, transfer
from .atomic import read_json_object, sha256_file, write_bytes_atomic, write_json_atomic
from .pathmap import SEGMENTATIONS_INDENT, SEGMENTATIONS_REL, STEP4_DIR

DIRECT_SUFFIXES = (".jpg", ".jpeg", ".png")
LOSSY_SUFFIXES = (".heic", ".heif", ".webp")  # converted to JPEG
LOSSLESS_SUFFIXES = (".tif", ".tiff", ".bmp", ".gif")  # converted to PNG
CONVERT_SUFFIXES = LOSSY_SUFFIXES + LOSSLESS_SUFFIXES
TYPES_TEXT = "JPEG, PNG, HEIC, HEIF, TIFF, BMP, WebP and GIF"
SIPS = "/usr/bin/sips"
SIPS_TIMEOUT_S = 300
JPEG_QUALITY = 95
SOURCES_LEDGER_NAME = "vega_annotator_sources.json"
SOURCES_LEDGER_REL = f"{STEP4_DIR}/custom_imports/{SOURCES_LEDGER_NAME}"
ALREADY_IN = "already in the project"
SPACE_FACTOR = 2  # each image is kept as its original plus a working JPEG
SPACE_MARGIN_BYTES = 1_000_000_000
LOW_SPACE_BYTES = 500_000_000  # an import stops before the disk has less than this free
ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")  # VEGA's session-name rule (_SESSION_NAME_RE)
MAX_ID_CHARS = 64
CLASH_SUFFIXES = range(2, 100)
LONGEST_CLASH_SUFFIX = "_99"
DEFAULT_SLUG = "project"
MAX_NAME_CHARS = 80
MADE_HERE = "made_here"
CREATING_KEY = "creating"  # in the local record while a new project's first import is unfinished
# A folder holding these is the app's own work, never a folder of photos: an EXPORT FOR VEGA
# package (or one of VEGA's Annotate Images sessions) and a VEGA project.
PACKAGE_MARKERS = ("session.json", "data.yaml")
PROJECT_MARKER = "project.json"
PACKAGE_REASON = "a VEGA Annotator package"
PROJECT_REASON = "a VEGA project"
SKIPPED_STEPS = (("1", "Make All Points"), ("2", "Recode Species"), ("3", "Choose Images"))
STAGING_PREFIX = ".creating-"
CONVERT_PREFIX = "vega-annotator-convert-"
# A photo whose stem is longer than LONG_STEM_BYTES comes in under its first SHORT_STEM_BYTES
# and STEM_HASH_CHARS of its SHA-256, so no frame, preview or package file passes 255 bytes.
LONG_STEM_BYTES = 100
SHORT_STEM_BYTES = 90
STEM_HASH_CHARS = 8
# Pillow's modes for 16-bit grayscale samples; such an image comes in as an 8-bit copy.
SIXTEEN_BIT_MODES = ("I;16", "I;16B", "I;16L", "I")
EIGHT_BIT_TOP = 255
# The plain reasons a file is skipped during an import.
UNREADABLE = "does not open as an image"
NOT_CONVERTED = "could not be converted"
NOT_COPIED = "could not be copied"
NOT_BROUGHT_IN = "could not be brought in"
# VEGA's ingest_files reasons, by how each starts, and the plain words the page shows instead.
INGEST_REASONS = (("not a readable image", UNREADABLE), ("could not set the unreadable upload aside", UNREADABLE),
                  ("copy failed", NOT_COPIED), ("no such file", "went missing before it could be copied"),
                  ("unsupported extension", "not an image type the app takes"))
# An import feeds VEGA's ingest_files in chunks, so it can report progress and stop between
# them: about PROGRESS_STEPS chunks, of at most MAX_CHUNK_FILES images each.
PROGRESS_STEPS = 100
MAX_CHUNK_FILES = 50

_create_lock = threading.Lock()
_store_lock = threading.Lock()


class FolderError(transfer.TransferError):
    """A folder project that cannot be made or added to, with a sentence the person can act on.

    It is a TransferError, so the home screen's job board shows the sentence alone.
    """


def vega_custom_imports():
    """Return VEGA's custom_imports module from the vendored annotator.

    The two folders annotator_boot puts on sys.path for the annotator go on the path here
    too, after the app's own, and custom_imports then finds _superseded by itself.

    Returns:
        module: VEGA's custom_imports.
    """
    for folder in (paths.vendor_scripts(), paths.annotator_src()):
        if str(folder) not in sys.path:
            sys.path.append(str(folder))
    return importlib.import_module("custom_imports")


def is_folder_project(project) -> bool:
    """Say whether a project record belongs to a project made from a folder.

    Parameters:
        project: the project record.
    Returns:
        bool: True when label_set.kind is "custom".
    """
    return projects.has_custom_labels(project)


def made_here(project_dir):
    """Return the local record of a project made on this computer, or None.

    Parameters:
        project_dir: the run_* folder.
    Returns:
        dict: {"kind": "made_here", "created_at", "computer"}; None when the folder's
        local record is missing, unreadable, or belongs to a project from the server.
    """
    try:
        record = read_json_object(Path(project_dir) / transfer.LOCAL_STATE)
    except ValueError:
        return None
    return record if record.get("kind") == MADE_HERE else None


def creating(project_dir) -> bool:
    """Say whether a project made here still waits for its first import to end.

    Parameters:
        project_dir: the run_* folder.
    Returns:
        bool: True while NEW PROJECT brings its images in, and after that import stopped
        partway, until an ADD IMAGES ends.
    """
    record = made_here(project_dir)
    return bool(record) and record.get(CREATING_KEY) is True


def _finish_creating(project_dir):
    """Take the creating mark off a project whose import has ended.

    Parameters:
        project_dir: the run_* folder.
    Raises:
        FolderError: when the local record cannot be rewritten; the images stay in.
    """
    record = made_here(project_dir)
    if not record or CREATING_KEY not in record:
        return
    record = {key: value for key, value in record.items() if key != CREATING_KEY}
    try:
        write_json_atomic(Path(project_dir) / transfer.LOCAL_STATE, record)
    except OSError as exc:
        raise FolderError(f"The images came in, and the project's record could not be updated "
                          f"({exc.strerror or 'it cannot be written'}). Use ADD IMAGES with the same "
                          f"folder to finish the project.") from exc


def _skip(path, reason):
    """Return one entry of a skipped list."""
    return {"path": Path(path), "reason": reason}


def _hidden(name, info):
    """Return True for a dot name, or a file or folder macOS marks hidden."""
    return name.startswith(".") or bool(getattr(info, "st_flags", 0) & getattr(stat, "UF_HIDDEN", 0))


def _existing_folder(folder):
    """Return folder as a Path, or refuse when it is not a folder."""
    if not isinstance(folder, (str, os.PathLike)) or not str(folder).strip():
        raise FolderError("Choose the folder that holds the images.")
    path = Path(folder).expanduser()
    if not path.is_dir():
        raise FolderError(f"{path} is not a folder. Choose the folder that holds the images.")
    return path


def _cannot_open(exc, what="cannot be opened"):
    """Return the plain reason a file or folder did not open, in the system's own words.

    Parameters:
        exc: the OSError.
        what: the start of the reason.
    Returns:
        str: such as "cannot be opened: Permission denied", never a path or Python's text.
    """
    return f"{what}: {exc.strerror}" if getattr(exc, "strerror", None) else what


def _readable(path):
    """Return "" when a file opens for reading, or the reason it does not."""
    try:
        with open(path, "rb") as handle:
            handle.read(1)
    except OSError as exc:
        return _cannot_open(exc)
    return ""


def _app_folder(folder):
    """Say what a folder is when it holds VEGA's or this app's own work, so no photo is taken from it.

    Parameters:
        folder: a folder.
    Returns:
        str: PACKAGE_REASON for a folder holding session.json and data.yaml, PROJECT_REASON
        for one holding project.json beside VEGA's annotation folder, and "" for any other
        folder. A project.json alone may belong to another labeling tool, so it hides no photo.
    """
    if all(os.path.isfile(os.path.join(folder, name)) for name in PACKAGE_MARKERS):
        return PACKAGE_REASON
    if os.path.isfile(os.path.join(folder, PROJECT_MARKER)) and os.path.isdir(os.path.join(folder, STEP4_DIR)):
        return PROJECT_REASON
    return ""


def scan(folder: Path) -> dict:
    """List the images in a folder and its subfolders.

    Links are never followed, and hidden files and folders are left out, as is any folder
    that is a VEGA Annotator package or a VEGA project: its images are the app's own copies
    and previews. Every file or folder left out is listed once, with the reason.

    Parameters:
        folder: the folder the person picked.
    Returns:
        dict: images ([Path] of JPEG and PNG files), convert ([Path] of HEIC, HEIF, TIFF,
        BMP, WebP and GIF files), skipped ([{"path": Path, "reason": str}]), and bytes
        (the size of images and convert together). The lists follow the folder's order:
        a folder's own files by name, then its subfolders by name. A picked folder that is
        itself a package or a project gives no images and one skipped entry, for itself.
    Raises:
        FolderError: when folder is not a folder.
    """
    root = _existing_folder(folder)
    images, convert, skipped = [], [], []
    total = 0
    kind = _app_folder(root)
    if kind:
        return {"images": images, "convert": convert, "skipped": [_skip(root, kind)], "bytes": total}

    def unreadable_folder(error):
        """List a subfolder that cannot be opened."""
        skipped.append(_skip(error.filename, _cannot_open(error, "folder cannot be opened")))

    for current, dirs, files in os.walk(root, onerror=unreadable_folder):
        base = Path(current)
        keep = []
        for name in sorted(dirs):
            full = base / name
            try:
                info = os.lstat(full)
            except OSError as exc:
                skipped.append(_skip(full, _cannot_open(exc, "folder cannot be opened")))
                continue
            kind = ""
            if stat.S_ISLNK(info.st_mode):
                kind = "link, not followed"
            elif _hidden(name, info):
                kind = "hidden folder"
            else:
                kind = _app_folder(full)
            if kind:
                skipped.append(_skip(full, kind))
            else:
                keep.append(name)
        dirs[:] = keep
        for name in sorted(files):
            full = base / name
            try:
                info = os.lstat(full)
            except OSError as exc:
                skipped.append(_skip(full, _cannot_open(exc)))
                continue
            suffix = full.suffix.lower()
            if _hidden(name, info):
                reason = "hidden file"
            elif stat.S_ISLNK(info.st_mode):
                reason = "link, not followed"
            elif not stat.S_ISREG(info.st_mode):
                reason = "not a regular file"
            elif suffix not in DIRECT_SUFFIXES + CONVERT_SUFFIXES:
                reason = "not an image type the app takes"
            elif info.st_size == 0:
                reason = "empty file"
            else:
                reason = _readable(full)
            if reason:
                skipped.append(_skip(full, reason))
                continue
            (images if suffix in DIRECT_SUFFIXES else convert).append(full)
            total += info.st_size
    return {"images": images, "convert": convert, "skipped": skipped, "bytes": total}


def _project_name(name):
    """Return the project name with its spacing tidied, or refuse it."""
    text = " ".join(name.split()) if isinstance(name, str) else ""
    if not text:
        raise FolderError("Type a name for the project.")
    if len(text) > MAX_NAME_CHARS:
        raise FolderError(f"The project name is {len(text)} characters long. "
                          f"Keep it to {MAX_NAME_CHARS} characters or fewer.")
    return text


def _initials(initials):
    """Return initials in VEGA's stored form, or refuse them with the settings' sentence."""
    try:
        return settings.normalize_initials(initials)
    except ValueError as exc:
        raise FolderError(str(exc)) from exc


def _purpose(purpose):
    """Return the purpose with its spacing tidied, or refuse an empty one."""
    text = " ".join(purpose.split()) if isinstance(purpose, str) else ""
    if not text:
        raise FolderError("Type a purpose, such as labeling coral health in field photos.")
    return text


def _slug(name):
    """Turn a project name into lower-case ASCII letters, digits and underscores."""
    ascii_text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")


def new_project_id(name, initials, root, today=None) -> str:
    """Make the run folder name for a new project: run_<YY-MM-DD>_<INI>_<slug>.

    The slug keeps only lower-case ASCII letters, digits and underscores from the name, so
    the id also fits VEGA's session-name rule. A name already taken in root gets _2 to _99.

    Parameters:
        name: the project's name.
        initials: who makes it.
        root: the folder that holds this computer's projects.
        today: the date to stamp; defaults to today in AST.
    Returns:
        str: the id, at most 64 characters.
    Raises:
        FolderError: when the name or initials are refused, or every suffix is taken.
    """
    slug = _slug(_project_name(name))
    stamp = (today or datetime.now(projects.AST).date()).strftime("%y-%m-%d")
    stem = f"{transfer.RUN_PREFIX}{stamp}_{_initials(initials)}_"
    room = MAX_ID_CHARS - len(stem) - len(LONGEST_CLASH_SUFFIX)
    base = stem + (slug[:room].strip("_") or DEFAULT_SLUG)
    for candidate in [base] + [f"{base}_{number}" for number in CLASH_SUFFIXES]:
        if not os.path.lexists(Path(root) / candidate):
            if not ID_RE.match(candidate):
                raise FolderError(f"{candidate} is not a name VEGA accepts. Choose another name.")
            return candidate
    raise FolderError(f"Projects named {base} and {base}_2 to {base}{LONGEST_CLASH_SUFFIX} "
                      f"already exist. Choose another name.")


def _size_text(count):
    """Return a byte count the way a person reads it."""
    return f"{count / 1e9:.1f} GB"


def _require_images(folder, found):
    """Refuse a folder whose scan found no image the app takes, saying when it is a package or project."""
    if found["images"] or found["convert"]:
        return
    whole = [item["reason"] for item in found["skipped"] if Path(item["path"]) == Path(folder)]
    if whole:
        raise FolderError(f"{folder} is {whole[0]}, not a folder of photos. Choose the folder that holds "
                          f"the photos.")
    raise FolderError(f"{folder} holds no images the app can take. "
                      f"Choose a folder with {TYPES_TEXT} files.")


def _refuse_inside(folder, outer, words):
    """Refuse an image folder inside a folder the app writes to.

    Parameters:
        folder: the image folder.
        outer: the app's folder.
        words: what the app's folder is, for the sentence.
    """
    inner, outer = Path(folder).resolve(), Path(outer).resolve()
    if inner == outer or outer in inner.parents:
        raise FolderError(f"That folder is inside {words}. Choose a folder of images from elsewhere.")


def _check_space(folder, image_bytes):
    """Refuse an import the disk holding folder has no room for.

    Parameters:
        folder: a folder on that disk.
        image_bytes: the size of the images to bring in.
    Raises:
        FolderError: when the disk lacks room for each image twice, plus 1 GB.
    """
    needed = image_bytes * SPACE_FACTOR + SPACE_MARGIN_BYTES
    free = shutil.disk_usage(folder).free
    if free < needed:
        raise FolderError(f"These images need {_size_text(needed)} free on this computer, which has "
                          f"{_size_text(free)}. Free some space, or choose a folder with fewer images.")


def _make_skeleton(root, run, name, names, initials, purpose, folder):
    """Write a new project's folders and records in a staging folder, then move it into place.

    A project that cannot be finished leaves nothing behind, so no half-made card appears.
    Its local record is marked creating before the folder moves into place, so the card
    the home screen lists from then on waits for the import.

    Parameters:
        root: the projects folder.
        run: the new run_* name.
        name: the project's name.
        names: its labels.
        initials: who makes it.
        purpose: why, for the access log.
        folder: the image folder it is made from.
    Returns:
        Path: the project folder.
    Raises:
        FolderError: when a file cannot be written.
    """
    staging = Path(root) / f"{STAGING_PREFIX}{run}-{uuid.uuid4().hex[:8]}"
    created = projects.now_ast()
    computer = settings.computer_name()
    record = {
        "id": run[len(transfer.RUN_PREFIX):],
        "name": name,
        "description": "",
        "initials": initials,
        "created_at": created,
        "updated_at": created,
        "access_log": [{"ts": created, "initials": initials, "action": "create", "purpose": purpose}],
        "steps": {key: {"status": "skipped", "name": title} for key, title in SKIPPED_STEPS},
        "origin": {"kind": "folder", "folder": str(Path(folder).resolve()), "computer": computer,
                   "made_with": f"{APP_NAME} {VERSION}"},
    }
    try:
        routed = staging / STEP4_DIR / "routed_input"
        (routed / "raw").mkdir(parents=True)
        write_json_atomic(routed / "ids" / "sam_click_prompts.json", {})
        write_json_atomic(staging / "project.json", record)
        labels.write(staging, names)
        write_json_atomic(staging / transfer.LOCAL_STATE,
                          {"kind": MADE_HERE, "created_at": created, "computer": computer, CREATING_KEY: True})
        os.rename(staging, Path(root) / run)
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise FolderError(f"The project could not be made in {root}: {exc.strerror or exc}. "
                          f"Check that this computer has room and that the folder can be written to.") from exc
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return Path(root) / run


def _short_stem(stem, digest):
    """Return the stem a photo comes in under: its own, or a short one when its own is long.

    VEGA names a frame custom_<stem>.jpg and its preview custom_<stem>_seg.jpg, and copies
    add a temporary name around those, so a stem near macOS's 255-byte limit stops an import
    or an EXPORT FOR VEGA. A stem longer than LONG_STEM_BYTES (100 characters of a plain
    name; fewer letters of an alphabet that takes several bytes each) is cut to its first
    SHORT_STEM_BYTES and given 8 characters of the file's SHA-256, so two long names that
    start alike stay apart.

    Parameters:
        stem: the file's name without its extension.
        digest: the file's SHA-256, in hex.
    Returns:
        str: the stem to use.
    """
    data = stem.encode("utf-8")
    if len(data) <= LONG_STEM_BYTES:
        return stem
    return f"{data[:SHORT_STEM_BYTES].decode('utf-8', 'ignore')}_{digest[:STEM_HASH_CHARS]}"


def _converted_name(stem, suffix):
    """Name the copy VEGA gets of a file it cannot read: IMG_2.heic becomes IMG_2_heic.jpg.

    The old type stays in the name, and a lossy photo becomes a JPEG, a lossless image a PNG.

    Parameters:
        stem: the stem the photo comes in under, from _short_stem.
        suffix: the file's extension, in lower case.
    Returns:
        str: the copy's file name.
    """
    return f"{stem}_{suffix.lstrip('.')}{'.jpg' if suffix in LOSSY_SUFFIXES else '.png'}"


def _new_spot(scratch, name):
    """Return a path named name in a fresh folder of its own inside scratch."""
    return Path(tempfile.mkdtemp(dir=scratch)) / name


def _convert(source, name, scratch):
    """Convert one image with sips: to JPEG at quality 95 for a .jpg name, else to PNG.

    sips keeps the photo's orientation tag, which VEGA's import then applies.

    Parameters:
        source: the person's file.
        name: the converted copy's file name, from _converted_name.
        scratch: a folder for the converted copy.
    Returns:
        tuple: (the copy's path, "") on success, or (None, NOT_CONVERTED) on failure.
    """
    out = _new_spot(scratch, name)
    form = ["-s", "format", "jpeg", "-s", "formatOptions", str(JPEG_QUALITY)] if out.suffix == ".jpg" \
        else ["-s", "format", "png"]
    try:
        result = subprocess.run([SIPS, *form, str(source), "--out", str(out)],
                                capture_output=True, text=True, timeout=SIPS_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return None, NOT_CONVERTED
    if result.returncode != 0 or not out.is_file():
        return None, NOT_CONVERTED
    return out, ""


def _eight_bit_copy(path, name, scratch):
    """Write an 8-bit copy of a 16-bit PNG, scaled by the image's own maximum, so its range shows.

    VEGA's import turns every image into 8-bit RGB by clipping, which makes a 16-bit
    grayscale image (from a microscope or a scanner, or sips's PNG of a 16-bit TIFF) nearly
    white. Each sample becomes sample * 255 / the image's maximum, so a 12-bit image stored
    in 16 bits fills the range too. An 8-bit image is left as it is. The 8-bit copy is the
    original VEGA keeps, as a converted copy is.

    Parameters:
        path: the PNG to look at.
        name: the copy's file name.
        scratch: a folder for the copy.
    Returns:
        Path: the 8-bit copy, or None when the image is not 16-bit or does not open; VEGA's
        import then takes the file as it is, and names what is wrong with it.
    """
    try:
        with Image.open(path) as image:
            if image.mode not in SIXTEEN_BIT_MODES:
                return None
            wide = image.convert("I")
            exif = image.getexif()
    except Exception:  # Pillow raises many kinds for a damaged file; VEGA's import reports it.
        return None
    _lowest, highest = wide.getextrema()
    scale = EIGHT_BIT_TOP / highest if highest > 0 else 0
    out = _new_spot(scratch, name)
    wide.point(lambda sample: sample * scale).convert("L").save(out, "PNG", **({"exif": exif} if len(exif) else {}))
    return out


def _stage(source, digest, folder, scratch):
    """Ready one photo for VEGA's import: under a short name when its own is long, converted
    when VEGA cannot read its type, and as an 8-bit copy when it is a 16-bit PNG.

    Parameters:
        source: the person's file.
        digest: its SHA-256.
        folder: the picked folder, which source lies in.
        scratch: a folder for staged copies.
    Returns:
        tuple: ((name, path), "") on success, where name is the photo's place inside the
        picked folder under the name VEGA gets, and path is the file VEGA reads; or
        (None, the plain reason the photo is skipped).
    """
    relative = source.relative_to(folder)
    stem = _short_stem(source.stem, digest)
    suffix = source.suffix.lower()
    if suffix in DIRECT_SUFFIXES:
        name, path = stem + source.suffix, source
    else:
        name = _converted_name(stem, suffix)
        path, failure = _convert(source, name, scratch)
        if path is None:
            return None, failure
    try:
        if Path(name).suffix.lower() == ".png":
            path = _eight_bit_copy(path, name, scratch) or path
        if path == source and name != source.name:
            staged = _new_spot(scratch, name)
            shutil.copyfile(source, staged)
            path = staged
    except OSError:
        return None, NOT_COPIED
    return ((relative.parent / name).as_posix(), path), ""


def _plain_reason(why):
    """Turn VEGA's reason for refusing an image into a plain one, without Python's text or paths.

    Parameters:
        why: the reason ingest_files gave.
    Returns:
        str: such as "does not open as an image".
    """
    for start, plain in INGEST_REASONS:
        if str(why).startswith(start):
            return plain
    return NOT_BROUGHT_IN


def _read_store(project_dir):
    """Read a project's frame store, or refuse when it is damaged.

    Returns:
        dict: the store; {} before the first frame.
    Raises:
        FolderError: when segmentations.json exists and cannot be read.
    """
    path = Path(project_dir) / SEGMENTATIONS_REL
    if not path.exists():
        return {}
    try:
        return read_json_object(path)
    except ValueError as exc:
        raise FolderError(f"The project's frames cannot be read ({exc}), so no image was added.") from exc


def _save_frames(project_dir, ci, entries):
    """Add frames for newly imported images to segmentations.json, in VEGA's store format.

    VEGA writes the store with json.dump(indent=1) into a temporary file, makes it group
    readable and moves it over the old one (app.py _atomic_write_json); so does this.

    Parameters:
        project_dir: the run_* folder.
        ci: VEGA's custom_imports module.
        entries: the import manifest entries ingest_files returned.
    Raises:
        FolderError: when the store cannot be read or written.
    """
    export_dir = str(Path(project_dir) / STEP4_DIR)
    with _store_lock:
        store = _read_store(project_dir)
        for entry in entries:
            store.setdefault(entry["filename"], ci.seg_dict_for(export_dir, entry))
        try:
            write_bytes_atomic(Path(project_dir) / SEGMENTATIONS_REL,
                               json.dumps(store, indent=SEGMENTATIONS_INDENT).encode("utf-8"))
        except OSError as exc:
            raise FolderError(f"The images came in, and the project's frame list could not be saved: "
                              f"{exc.strerror or exc}. VEGA's annotator lists them when it opens the "
                              f"project.") from exc


def _read_sources(project_dir):
    """Read the ledger of imported source files: {SHA-256: [frame names]}.

    Returns:
        dict: the ledger; {} before the first import.
    Raises:
        FolderError: when the ledger exists and cannot be read.
    """
    path = Path(project_dir) / SOURCES_LEDGER_REL
    if not path.exists():
        return {}
    try:
        ledger = read_json_object(path)
    except ValueError as exc:
        ledger = exc
    if not isinstance(ledger, dict) or not all(
            isinstance(frames, list) and all(isinstance(frame, str) for frame in frames) for frames in ledger.values()):
        raise FolderError(f"The project's list of imported images, {path}, cannot be read, so no image was "
                          f"added. Move that file aside to add images anyway; an image already in the project "
                          f"would then come in twice.")
    return ledger


def _stop_reason(folder, project_dir, needed=0):
    """Say why an import must stop now, with what to do; ("", "") to go on.

    Parameters:
        folder: the picked folder, which must still be there.
        project_dir: the run_* folder, whose disk must keep LOW_SPACE_BYTES free.
        needed: bytes the next step writes on that disk, beyond that floor.
    Returns:
        tuple: (the reason, what to do), or ("", "").
    """
    if not Path(folder).is_dir():
        return f"The image folder {folder} went away", "Reconnect it"
    if shutil.disk_usage(project_dir).free < LOW_SPACE_BYTES + needed:
        return "This computer's disk is almost full", "Free some space"
    return "", ""


def _size_on_disk(path):
    """Return a file's size, or 0 when it is gone; VEGA's import then lists it as missing."""
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def _import(project_dir, folder, found, initials, progress):
    """Bring scanned images into a project through VEGA's custom_imports, one chunk at a time.

    Each source file's SHA-256 is checked against the project's ledger first: a file already
    in the project, or a copy of one earlier in this import, is skipped. A copy of a file in
    the same chunk waits for the next chunk, which knows whether its twin came in.

    Each image then goes to ingest_files under its path inside the picked folder, so two
    photos with one name in two subfolders become two frames; a converted image goes under
    its copy's name, such as IMG_2_heic.jpg. A long name comes in shortened, and a 16-bit
    PNG as an 8-bit copy (_stage). Before each chunk goes in, the free space is checked
    against the chunk's real size, staged copies included. The ledger is saved as each
    chunk lands. A file VEGA refuses is listed with a plain reason.

    Parameters:
        project_dir: the run_* folder.
        folder: the picked folder.
        found: what scan(folder) returned.
        initials: who is adding the images.
        progress: optional callable(done, total, message).
    Returns:
        dict: added (int) and skipped ([{"path": Path, "reason": str}]).
    Raises:
        FolderError: when the folder goes away, the disk fills, or a write fails partway.
            The frames already imported are saved and recorded first.
    """
    ci = vega_custom_imports()
    export_dir = str(Path(project_dir) / STEP4_DIR)
    sources = list(found["images"]) + list(found["convert"])
    total = len(sources)
    chunk = max(1, min(MAX_CHUNK_FILES, math.ceil(total / PROGRESS_STEPS)))
    taken = set(_read_store(project_dir))
    ledger = _read_sources(project_dir)
    entries, skipped = [], []
    reason = advice = ""
    position = 0
    with tempfile.TemporaryDirectory(prefix=CONVERT_PREFIX) as scratch:
        while position < total and not reason:
            reason, advice = _stop_reason(folder, project_dir)
            batch = {}
            while not reason and position < total and len(batch) < chunk:
                source = sources[position]
                try:
                    digest = sha256_file(source)
                except OSError as exc:
                    reason, advice = _stop_reason(folder, project_dir)
                    if not reason:
                        skipped.append(_skip(source, _cannot_open(exc)))
                        position += 1
                    continue
                if digest in batch:
                    break
                if digest in ledger:
                    skipped.append(_skip(source, ALREADY_IN))
                else:
                    batch[digest] = source
                position += 1
            items, origins, digests = [], {}, {}
            for digest, source in batch.items() if not reason else ():
                staged, failure = _stage(source, digest, folder, scratch)
                if staged is None:
                    skipped.append(_skip(source, failure))
                    continue
                name, path = staged
                items.append((name, str(path)))
                origins[name], digests[name] = source, digest
            if items:
                reason, advice = _stop_reason(folder, project_dir,
                                              SPACE_FACTOR * sum(_size_on_disk(path) for _name, path in items))
            if items and not reason:
                try:
                    new, refused = ci.ingest_files(export_dir, items, taken_names=taken, reviewer=initials)
                    entries.extend(new)
                    for name, why in refused:
                        entry = _skip(origins.get(name, Path(folder) / name), _plain_reason(why))
                        if entry not in skipped:
                            skipped.append(entry)
                    for entry in new:
                        ledger.setdefault(digests[entry["original_name"]], []).append(entry["filename"])
                    if new:
                        write_json_atomic(Path(project_dir) / SOURCES_LEDGER_REL, ledger)
                except OSError as exc:
                    reason = f"Copying the images stopped ({exc.strerror or 'a file could not be written'})"
                    advice = "Check that this computer has room"
            for leftover in Path(scratch).iterdir():
                shutil.rmtree(leftover, ignore_errors=True)
            if progress and not reason:
                progress(position, total, f"Adding images: {position} of {total}")
    if entries:
        _save_frames(project_dir, ci, entries)
        projects.with_record_lock(ci.record_in_project, export_dir, entries, initials=initials)
    if reason:
        raise FolderError(f"{reason} after {len(entries)} of {total} images came in. The project keeps "
                          f"the images that came in and opens as before. {advice}, then use ADD IMAGES "
                          f"with the same folder: images already in the project are skipped.")
    return {"added": len(entries), "skipped": skipped}


def _check_run(run, root):
    """Refuse a run name a caller chose that is not a fresh project name in root.

    Parameters:
        run: the run_* name the caller chose, as new_project_id makes it.
        root: the projects folder.
    Raises:
        FolderError: when run is not a project name VEGA accepts, or is taken.
    """
    if not transfer.valid_run_name(run) or not ID_RE.match(run):
        raise FolderError(f"{run!s} is not a project name VEGA accepts. Click NEW PROJECT again.")
    if os.path.lexists(Path(root) / run):
        raise FolderError(f"A project named {run} already exists on this computer. Click NEW PROJECT again.")


def create(folder, name, label_text, initials, purpose, root=None, progress=None, run=None) -> dict:
    """Make a project from a folder of images, with the labels the person typed.

    The project's local record says creating from before its folder appears in root until
    the import ends; an import that stops partway leaves the mark for ADD IMAGES to clear.

    Parameters:
        folder: the image folder.
        name: the project's name.
        label_text: the labels, as a list or as text with one per line or split by commas.
        initials: who makes it.
        purpose: why, for the access log.
        root: the folder that holds this computer's projects; defaults to paths.projects_dir().
        progress: optional callable(done, total, message), called as the images come in.
        run: the run_* name to make, chosen beforehand with new_project_id so a job can name
            the project before it exists; None chooses one here.
    Returns:
        dict: run (the new run_* name), added (images brought in), and skipped
        ([{"path": Path, "reason": str}], from the scan and the import; a second copy of
        one file is skipped as "already in the project").
    Raises:
        LabelError: when a label breaks a rule. Nothing is made.
        FolderError: when the name, initials, purpose, folder or chosen run is refused, the
            disk lacks room, or the project cannot be written, with nothing made; or when
            the folder goes away or the disk fills during the import, with the project kept
            and still marked creating.
    """
    name = _project_name(name)
    names = labels.parse(label_text)
    initials = _initials(initials)
    purpose = _purpose(purpose)
    folder = _existing_folder(folder)
    root = Path(root) if root else paths.projects_dir()
    _refuse_inside(folder, root, "this app's projects")
    found = scan(folder)
    _require_images(folder, found)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FolderError(f"The projects folder {root} cannot be made: {exc.strerror or exc}.") from exc
    _check_space(root, found["bytes"])
    with _create_lock:
        if run is None:
            run = new_project_id(name, initials, root)
        else:
            _check_run(run, root)
        project_dir = _make_skeleton(root, run, name, names, initials, purpose, folder)
    result = _import(project_dir, folder, found, initials, progress)
    _finish_creating(project_dir)
    return {"run": run, "added": result["added"], "skipped": found["skipped"] + result["skipped"]}


def add_images(project_dir, folder, initials, progress=None) -> dict:
    """Bring more images into a project made from a folder.

    Parameters:
        project_dir: the run_* folder.
        folder: the image folder.
        initials: who adds them.
        progress: optional callable(done, total, message), called as the images come in.
    Returns:
        dict: added (images brought in) and skipped ([{"path": Path, "reason": str}]). A
        file already in the project, under any name and from any folder, is skipped with
        the reason "already in the project", so adding a folder again brings in only what
        is new. An ADD IMAGES that ends finishes a new project whose import stopped
        partway: its creating mark comes off.
    Raises:
        FolderError: when the project did not come from a folder, the annotator has it
            open, the folder holds no images, or the disk lacks room, with nothing added;
            or when the folder goes away or the disk fills partway, with the images that
            came in kept.
    """
    project_dir = Path(project_dir)
    try:
        project = projects.read_project(project_dir)
    except ValueError as exc:
        raise FolderError(f"{project_dir.name} cannot be read ({exc}), so no image was added.") from exc
    if not is_folder_project(project):
        raise FolderError(f"{project_dir.name} came from VEGA. ADD IMAGES works on projects made from "
                          f"a folder of images.")
    initials = _initials(initials)
    folder = _existing_folder(folder)
    _refuse_inside(folder, project_dir, "this project")
    if projects.open_in_annotator(project_dir):
        raise FolderError("Close the annotator on this project first, then add the images.")
    found = scan(folder)
    _require_images(folder, found)
    _check_space(project_dir, found["bytes"])
    result = _import(project_dir, folder, found, initials, progress)
    _finish_creating(project_dir)
    return {"added": result["added"], "skipped": found["skipped"] + result["skipped"]}
