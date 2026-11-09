"""The SAM3 weights on this computer, installing them from the server folder, and checking a copy.

A SAM3 copy, in the server folder or unpacked from the lab's GitHub release, holds the
checkpoint as plain files in Hugging Face's cache layout (refs/main and snapshots/<commit>/),
so no symbolic links are needed and Windows reads it as Linux does, plus Meta's license and
a checksum list. The annotator then loads from the local copy with no network and no
Hugging Face account.
"""

import re
import shutil
import uuid
from pathlib import Path

from . import paths
from .atomic import read_json_object, sha256_file
from .transfer import TransferError, _check_space, copy_verified, safe_rel

CHECKSUMS_NAME = "checksums.json"
LICENSE_NAME = "SAM_LICENSE.txt"
WEIGHTS_NAME = "model.safetensors"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
CHECK_MESSAGE = "Checking SAM3"


def installed_snapshot(model_dir=None):
    """Return the installed checkpoint's snapshot folder, or None.

    Parameters:
        model_dir: the models--facebook--sam3 folder; defaults to this computer's.
    Returns:
        Path: the snapshot folder when refs/main names one holding the weights;
        None when anything is missing.
    """
    model_dir = Path(model_dir) if model_dir else paths.sam3_model_dir()
    try:
        commit = (model_dir / "refs" / "main").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not commit or "/" in commit or "\\" in commit or ".." in commit:
        return None
    snapshot = model_dir / "snapshots" / commit
    return snapshot if (snapshot / WEIGHTS_NAME).is_file() else None


def status():
    """Report whether SAM3 is ready on this computer.

    Returns:
        dict: installed (bool) and size_gb (the weights' size, 0 when absent).
    """
    snapshot = installed_snapshot()
    if snapshot is None:
        return {"installed": False, "size_gb": 0}
    return {"installed": True, "size_gb": round((snapshot / WEIGHTS_NAME).stat().st_size / 1e9, 1)}


def server_copy(server_folder):
    """Return the server folder's sam3/ folder when it holds a usable copy, else None.

    Parameters:
        server_folder: the VEGA_Annotator folder, or "".
    Returns:
        Path or None.
    """
    if not server_folder:
        return None
    folder = Path(server_folder) / paths.SAM3_SUBFOLDER
    return folder if (folder / CHECKSUMS_NAME).is_file() else None


def read_listing(folder):
    """Read a SAM3 copy's checksums.json and check that every entry is usable.

    Parameters:
        folder: the copy's folder, which holds checksums.json.
    Returns:
        dict: each file's path relative to folder, mapped to its size and sha256.
    Raises:
        TransferError: when the list is missing, unreadable or empty, or an entry is not
            a plain relative path with a size and a SHA-256.
    """
    try:
        listing = read_json_object(Path(folder) / CHECKSUMS_NAME)
    except ValueError as exc:
        raise TransferError(f"The SAM3 file list cannot be read: {exc}") from exc
    if not listing:
        raise TransferError("The SAM3 file list is empty, so the copy cannot be checked.")
    for rel, entry in listing.items():
        if (not safe_rel(rel) or not isinstance(entry, dict) or type(entry.get("size")) is not int
                or entry["size"] < 0 or not SHA256_PATTERN.fullmatch(str(entry.get("sha256")))):
            raise TransferError(f"The SAM3 file list has a bad entry: {rel!r}")
    return listing


def verify_copy(folder, progress=None):
    """Check every file in a SAM3 copy against the size and SHA-256 in its checksums.json.

    Parameters:
        folder: the copy's folder, holding checksums.json and the files it lists.
        progress: optional callable (bytes checked, bytes total, message).
    Returns:
        None.
    Raises:
        TransferError: naming the first file that is missing or differs, or when the
            list itself is unusable.
    """
    folder = Path(folder)
    listing = read_listing(folder)
    total = sum(entry["size"] for entry in listing.values())
    done = [0]

    def on_chunk(count):
        """Report progress for one checked chunk."""
        done[0] += count
        if progress:
            progress(done[0], total, CHECK_MESSAGE)

    for rel, entry in sorted(listing.items()):
        path = folder / rel
        if not path.is_file():
            raise TransferError(f"SAM3's file {rel} is missing.")
        size = path.stat().st_size
        if size != entry["size"]:
            raise TransferError(f"SAM3's file {rel} is {size:,} bytes, and its list says {entry['size']:,}.")
        try:
            actual = sha256_file(path, on_chunk)
        except OSError as exc:
            raise TransferError(f"SAM3's file {rel} cannot be read: {exc}") from exc
        if actual != entry["sha256"]:
            raise TransferError(f"SAM3's file {rel} does not match its checksum.")


def install_from_server(server_folder, progress=None):
    """Copy SAM3 from the server folder to this computer, checking every file.

    The copy goes to a temporary folder and moves into place only when complete,
    so a dropped connection leaves nothing half installed.

    Parameters:
        server_folder: the VEGA_Annotator folder.
        progress: optional callable (bytes done, bytes total, message).
    Returns:
        dict: files and bytes copied.
    Raises:
        TransferError: with a sentence saying why it cannot go ahead.
    """
    folder = server_copy(server_folder)
    if folder is None:
        raise TransferError("The server folder holds no copy of SAM3 yet.")
    listing = read_listing(folder)
    total = sum(entry["size"] for entry in listing.values())
    home = paths.sam3_home()
    home.mkdir(parents=True, exist_ok=True)
    _check_space(home, total)
    working = home / f".incoming_{uuid.uuid4().hex[:8]}"
    done = [0]

    def on_chunk(count):
        """Report progress for one copied chunk."""
        done[0] += count
        if progress:
            progress(done[0], total, "Copying SAM3")

    try:
        for rel, entry in sorted(listing.items()):
            copy_verified(folder / rel, working / rel, entry["sha256"], on_chunk)
        hf_hub = paths.sam3_model_dir().parent
        hf_hub.mkdir(parents=True, exist_ok=True)
        target = paths.sam3_model_dir()
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(working / paths.SAM3_MODEL_FOLDER), str(target))
        license_copy = working / LICENSE_NAME
        if license_copy.is_file():
            shutil.move(str(license_copy), str(home / LICENSE_NAME))
    finally:
        shutil.rmtree(working, ignore_errors=True)
    return {"files": len(listing), "bytes": total}
