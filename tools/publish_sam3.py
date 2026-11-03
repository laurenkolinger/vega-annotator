#!/usr/bin/env python3
"""Copy the SAM3 weights from the mainframe's Hugging Face cache into the server folder.

Module: VEGA Annotator / publish_sam3
Purpose: the laptop app installs SAM3 from the server folder, with no Hugging Face
    account. Hugging Face's cache stores files behind symbolic links, which Windows and
    network shares handle badly, so this tool writes plain files in the same layout:
    models--facebook--sam3/refs/main and models--facebook--sam3/snapshots/<commit>/.
    Meta's license must go with any copy, so the tool refuses to finish without it.
Inputs: --cache    the models--facebook--sam3 folder in the Hugging Face cache
        --dest     the server folder's sam3/ folder, already holding SAM_LICENSE.txt
        --dry-run  list what would happen and write nothing
Outputs: the checkpoint and checksums.json under --dest.
Run: ssh vicar-mainframe python3 - < tools/publish_sam3.py
"""

import argparse
import hashlib
import json
import os
import shutil
import sys

DEFAULT_CACHE = os.path.expanduser("~/.cache/huggingface/hub/models--facebook--sam3")
MODEL_FOLDER = "models--facebook--sam3"
LICENSE_NAME = "SAM_LICENSE.txt"
CHECKSUMS = "checksums.json"
CHUNK_BYTES = 4 * 1024 * 1024


def file_digest(path):
    """Return (size, hex SHA-256) of a file."""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(CHUNK_BYTES)
            if not chunk:
                return size, digest.hexdigest()
            digest.update(chunk)
            size += len(chunk)


def find_snapshot(cache):
    """Return (commit, snapshot folder) of the cached checkpoint.

    Parameters:
        cache: the models--facebook--sam3 folder.
    Returns:
        tuple: the commit name and its folder.
    Raises:
        ValueError: when the cache holds no usable snapshot.
    """
    refs = os.path.join(cache, "refs", "main")
    snapshots = os.path.join(cache, "snapshots")
    if os.path.isfile(refs):
        with open(refs, encoding="utf-8") as handle:
            commit = handle.read().strip()
    else:
        names = sorted(os.listdir(snapshots)) if os.path.isdir(snapshots) else []
        if len(names) != 1:
            raise ValueError("%s should hold one snapshot and holds %d." % (snapshots, len(names)))
        commit = names[0]
    folder = os.path.join(snapshots, commit)
    if not os.path.isfile(os.path.join(folder, "model.safetensors")):
        raise ValueError("%s holds no model.safetensors." % folder)
    return commit, folder


def publish(cache, dest, dry_run=False):
    """Copy the checkpoint as plain files and write the checksum list.

    Parameters:
        cache: the models--facebook--sam3 folder.
        dest: the server folder's sam3/ folder.
        dry_run: report and write nothing.
    Returns:
        dict: commit, files, and bytes.
    Raises:
        ValueError: when the cache or the license is missing.
    """
    commit, snapshot = find_snapshot(cache)
    license_path = os.path.join(dest, LICENSE_NAME)
    if not os.path.isfile(license_path):
        raise ValueError("%s is missing. Meta's license must go with any copy of the weights." % license_path)
    names = sorted(name for name in os.listdir(snapshot) if os.path.isfile(os.path.join(snapshot, name)))
    if dry_run:
        total = sum(os.path.getsize(os.path.join(snapshot, name)) for name in names)
        return {"commit": commit, "files": len(names), "bytes": total}
    listing = {}
    size, sha = file_digest(license_path)
    listing[LICENSE_NAME] = {"size": size, "sha256": sha}
    target = os.path.join(dest, MODEL_FOLDER, "snapshots", commit)
    os.makedirs(target, exist_ok=True)
    os.makedirs(os.path.join(dest, MODEL_FOLDER, "refs"), exist_ok=True)
    refs_rel = "%s/refs/main" % MODEL_FOLDER
    with open(os.path.join(dest, *refs_rel.split("/")), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(commit)
    size, sha = file_digest(os.path.join(dest, *refs_rel.split("/")))
    listing[refs_rel] = {"size": size, "sha256": sha}
    for name in names:
        source = os.path.join(snapshot, name)
        destination = os.path.join(target, name)
        if not os.path.exists(destination) or os.path.getsize(destination) != os.path.getsize(source):
            partial = destination + ".vegatmp"
            shutil.copyfile(source, partial)
            os.replace(partial, destination)
        size, sha = file_digest(destination)
        if (size, sha) != file_digest(source):
            raise ValueError("%s does not match its source after copying." % destination)
        listing["%s/snapshots/%s/%s" % (MODEL_FOLDER, commit, name)] = {"size": size, "sha256": sha}
    with open(os.path.join(dest, CHECKSUMS), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(listing, indent=2) + "\n")
    return {"commit": commit, "files": len(listing), "bytes": sum(item["size"] for item in listing.values())}


def main(argv=None):
    """Publish the weights and print one summary line.

    Parameters:
        argv: the command line, without the program name.
    Returns:
        int: 0 on success, 1 on failure with the reason on stderr.
    """
    parser = argparse.ArgumentParser(description="Copy SAM3 into the server folder.")
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--dest", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = publish(args.cache, args.dest, args.dry_run)
    except (ValueError, OSError) as exc:
        print("publish_sam3: %s" % exc, file=sys.stderr)
        return 1
    print("%s SAM3 commit %s: %d files, %.2f GB, to %s" % (
        "Would publish" if args.dry_run else "Published", result["commit"][:10], result["files"],
        result["bytes"] / 1e9, args.dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
