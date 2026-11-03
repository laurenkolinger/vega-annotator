"""Translate a project's absolute paths between the mainframe and this computer.

A VEGA project records absolute paths in a handful of files. The server copy always
holds the mainframe's paths, so the folder stays in VEGA's form and VEGA can read it
later. The working copy on a laptop holds local paths, because VEGA's annotator opens
frames through them. Everything else in a project is relative or points outside the
project, and stays as it is.
"""

import csv
import io
import json
import os
from pathlib import Path

STEP4_DIR = "step4test_combinedAnnotate"
SEGMENTATIONS_REL = f"{STEP4_DIR}/segmentations/segmentations.json"
MANIFEST_REL = f"{STEP4_DIR}/export_manifest.csv"
DATA_YAML_REL = f"{STEP4_DIR}/data.yaml"
PROJECT_JSON_REL = "project.json"
CONFIG_LOG_RELS = (f"{STEP4_DIR}/config_log.json", f"{STEP4_DIR}/routed_input/config_log.json")
SEGMENTATIONS_INDENT = 1  # VEGA's save_segmentations writes json.dump(payload, f, indent=1)
OTHER_JSON_INDENT = 2
MANIFEST_PATH_COLUMN = "image_path"
PATH_BEARING_RELS = (PROJECT_JSON_REL, SEGMENTATIONS_REL, MANIFEST_REL, DATA_YAML_REL) + CONFIG_LOG_RELS


class PathMap:
    """Convert paths under one project between server form and local form."""

    def __init__(self, server_root, local_root):
        """Remember both roots.

        Parameters:
            server_root: the project's folder on the mainframe (POSIX form).
            local_root: the project's folder on this computer.
        """
        self.server_root = str(server_root).rstrip("/")
        self.local_root = str(local_root).rstrip("/\\")

    def to_local(self, value):
        """Return value with the server root replaced by the local root.

        Parameters:
            value: any string.
        Returns:
            str: the local path, or value unchanged when it is not under the
            server root.
        """
        if value == self.server_root:
            return self.local_root
        if value.startswith(self.server_root + "/"):
            rest = value[len(self.server_root) + 1:]
            return os.path.join(self.local_root, *rest.split("/"))
        return value

    def to_server(self, value):
        """Return value with the local root replaced by the server root.

        Parameters:
            value: any string.
        Returns:
            str: the POSIX server path, or value unchanged when it is not under
            the local root.
        """
        normalized = value.replace("\\", "/")
        root = self.local_root.replace("\\", "/")
        same_root = os.path.normcase(normalized) == os.path.normcase(root)
        under_root = os.path.normcase(normalized).startswith(os.path.normcase(root + "/"))
        if same_root:
            return self.server_root
        if under_root:
            return f"{self.server_root}/{normalized[len(root) + 1:]}"
        return value


def map_strings(value, convert):
    """Apply convert to every string held anywhere inside a JSON value.

    Parameters:
        value: a JSON value.
        convert: a function from str to str.
    Returns:
        The rewritten value. Keys are left as they are.
    """
    if isinstance(value, str):
        return convert(value)
    if isinstance(value, list):
        return [map_strings(item, convert) for item in value]
    if isinstance(value, dict):
        return {key: map_strings(item, convert) for key, item in value.items()}
    return value


def _rewrite_segmentations(text, convert):
    """Rewrite each frame's image_path_abs, in the layout VEGA itself writes."""
    store = json.loads(text)
    if not isinstance(store, dict):
        raise ValueError("segmentations.json should hold an object of frames")
    for frame in store.values():
        if isinstance(frame, dict) and isinstance(frame.get("image_path_abs"), str):
            frame["image_path_abs"] = convert(frame["image_path_abs"])
    return json.dumps(store, indent=SEGMENTATIONS_INDENT)


def _rewrite_general_json(text, convert):
    """Rewrite every string in a JSON file, keeping a final newline if it had one."""
    rewritten = json.dumps(map_strings(json.loads(text), convert), indent=OTHER_JSON_INDENT)
    return rewritten + "\n" if text.endswith("\n") else rewritten


def _rewrite_manifest(text, convert):
    """Rewrite the image_path column of export_manifest.csv, keeping its line ending."""
    reader = csv.reader(io.StringIO(text, newline=""))
    rows = list(reader)
    if not rows or MANIFEST_PATH_COLUMN not in rows[0]:
        return text
    column = rows[0].index(MANIFEST_PATH_COLUMN)
    terminator = "\r\n" if "\r\n" in text else "\n"
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator=terminator)
    writer.writerow(rows[0])
    for row in rows[1:]:
        if len(row) > column:
            row[column] = convert(row[column])
        writer.writerow(row)
    return out.getvalue()


def _rewrite_data_yaml(text, convert):
    """Rewrite the path: line of data.yaml. VEGA writes this file line by line."""
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if line.startswith("path: "):
            lines[index] = "path: " + convert(line[len("path: "):].rstrip("\r"))
            break
    return "\n".join(lines)


def rewrite_text(rel, text, convert):
    """Rewrite one path-bearing file's text.

    Parameters:
        rel: the file's path relative to the project, one of PATH_BEARING_RELS.
        text: the file's text.
        convert: PathMap.to_local or PathMap.to_server.
    Returns:
        str: the rewritten text.
    Raises:
        ValueError: when rel is not path-bearing, or the JSON inside is malformed.
    """
    if rel == SEGMENTATIONS_REL:
        return _rewrite_segmentations(text, convert)
    if rel == MANIFEST_REL:
        return _rewrite_manifest(text, convert)
    if rel == DATA_YAML_REL:
        return _rewrite_data_yaml(text, convert)
    if rel == PROJECT_JSON_REL or rel in CONFIG_LOG_RELS:
        return _rewrite_general_json(text, convert)
    raise ValueError(f"{rel} is not a file with project paths")


def rewrite_file(path, rel, convert, dest=None):
    """Rewrite one path-bearing file.

    Parameters:
        path: the file to read.
        rel: its path relative to the project.
        convert: PathMap.to_local or PathMap.to_server.
        dest: where to write; defaults to path. The write goes through a
            temporary name, so a failure leaves the destination as it was.
    Returns:
        None.
    Raises:
        ValueError: when the file's content is malformed, naming the file.
        OSError: when it cannot be read or written.
    """
    from .atomic import write_bytes_atomic

    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8")
        rewritten = rewrite_text(rel, text, convert)
    except ValueError as exc:
        raise ValueError(f"{rel} could not be rewritten: {exc}") from exc
    write_bytes_atomic(dest or path, rewritten.encode("utf-8"))
