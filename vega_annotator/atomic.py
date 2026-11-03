"""Small file helpers shared across the app: atomic writes, hashes, and JSON reads."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

HASH_CHUNK_BYTES = 4 * 1024 * 1024
SAVED_FILE_MODE = 0o664  # mkstemp's owner-only default would hide a record from the server's sync.


def sha256_file(path, progress=None):
    """Return the hex SHA-256 of a file.

    Parameters:
        path: the file to read.
        progress: optional callable given the byte count of each chunk read.
    Returns:
        str: 64 hex characters.
    Raises:
        OSError: when the file cannot be read.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(HASH_CHUNK_BYTES)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)
            if progress:
                progress(len(chunk))


def write_bytes_atomic(path, data):
    """Write bytes so a reader only ever sees the old file or the whole new one.

    Parameters:
        path: the destination file; its folder is created when missing.
        data: the bytes to write.
    Returns:
        None.
    Raises:
        OSError: when the write fails. The destination then stays as it was and
            the temporary file is removed.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp, SAVED_FILE_MODE)
        except OSError:
            pass  # A network share can refuse this and still save the file under its own rules.
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path, payload, indent=2):
    """Write a JSON document through write_bytes_atomic.

    Parameters:
        path: the destination file.
        payload: any JSON-serializable value.
        indent: the JSON indent.
    Returns:
        None.
    """
    write_bytes_atomic(path, (json.dumps(payload, indent=indent) + "\n").encode("utf-8"))


def read_json_object(path):
    """Read a JSON file that must hold one object.

    Parameters:
        path: the file to read.
    Returns:
        dict: the parsed object.
    Raises:
        ValueError: when the file is missing, unreadable, malformed, or holds
            anything other than an object. The message names the file and why.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"{path} is missing") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path} cannot be read: {exc}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} should hold a JSON object and holds a {type(data).__name__}")
    return data
