"""The SAM3 weights on this computer, and installing them from the server folder.

The server folder holds the checkpoint as plain files in Hugging Face's cache layout
(refs/main and snapshots/<commit>/), so no symbolic links are needed and Windows reads
it as Linux does, plus Meta's license and a checksum list. The annotator then loads
from the local copy with no network and no Hugging Face account.
"""

import shutil
import uuid
from pathlib import Path

from . import paths
from .atomic import read_json_object
from .transfer import TransferError, _check_space, copy_verified, safe_rel

CHECKSUMS_NAME = "checksums.json"
LICENSE_NAME = "SAM_LICENSE.txt"
WEIGHTS_NAME = "model.safetensors"


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
    try:
        listing = read_json_object(folder / CHECKSUMS_NAME)
    except ValueError as exc:
        raise TransferError(f"The SAM3 file list cannot be read: {exc}") from exc
    for rel, entry in listing.items():
        if not safe_rel(rel) or not isinstance(entry, dict) or not isinstance(entry.get("size"), int):
            raise TransferError(f"The SAM3 file list has a bad entry: {rel!r}")
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
            copy_verified(folder / rel, working / rel, entry.get("sha256"), on_chunk)
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
