#!/usr/bin/env python3
"""Copy VEGA's annotator files into vendor/vega/ and record exactly what was copied.

Purpose: the app runs VEGA's own annotator code unchanged. This tool refreshes
    the vendored copy from the mainframe's working tree and writes VENDORED.json,
    which holds the source commit and a checksum for every file. The parity test
    (tests/test_vendor_parity.py) fails if a vendored file later differs.
Inputs: --host, the ssh host name of the mainframe (default vicar-mainframe);
    --repo, the VEGA checkout on that host; --dry-run, to list without writing.
Outputs: vendor/vega/<VEGA relative paths> and vendor/vega/VENDORED.json.
Run: python3 tools/sync_from_vega.py
"""

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

DEFAULT_HOST = "vicar-mainframe"
DEFAULT_REPO = "/mnt/rip/vicarius_drive/vicarius/modules/reef_point_seg/github_repo"
ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "vega"
MANIFEST_NAME = "VENDORED.json"

# Every path is relative to the VEGA checkout. Directories are copied without
# their tests and caches.
VENDORED_PATHS = (
    "scripts/TCRMPclip_combinedAnnotate/src/app.py",
    "scripts/TCRMPclip_combinedAnnotate/src/config.py",
    "scripts/TCRMPclip_combinedAnnotate/src/custom_imports.py",
    "scripts/TCRMPclip_combinedAnnotate/src/export_yolo.py",
    "scripts/TCRMPclip_combinedAnnotate/src/mask_ops.py",
    "scripts/TCRMPclip_combinedAnnotate/src/provenance.py",
    "scripts/TCRMPclip_combinedAnnotate/src/render_overlay.py",
    "scripts/TCRMPclip_combinedAnnotate/src/sam_engine.py",
    "scripts/TCRMPclip_combinedAnnotate/src/templates/index.html",
    "scripts/_superseded.py",
    "scripts/_reefreview/__init__.py",
    "scripts/_reefreview/clock.py",
    "scripts/_reefreview/codes.py",
    "scripts/_reefreview/coverage.py",
    "scripts/_reefreview/eml_writer.py",
    "scripts/_reefreview/filename_parse.py",
    "scripts/_reefreview/ledger_io.py",
    "scripts/_reefreview/library.py",
    "scripts/_reefreview/manual_sources.py",
    "scripts/_reefreview/mask_geom.py",
    "scripts/_reefreview/mask_registry.py",
    "scripts/_reefreview/render_review.py",
    "scripts/_reefreview/review_export.py",
    "scripts/_reefreview/review_repo.py",
    "scripts/_reefreview/screening.py",
    "scripts/_reefreview/training_projects.py",
    "scripts/_reefreview/uid.py",
    "scripts/pipeline_orchestrator/static/vicarius_workflow.css",
    "scripts/pipeline_orchestrator/static/style.css",
    "scripts/pipeline_orchestrator/static/vicarius_tooltips.js",
    "requirements.lock",
)
# The home screen uses VEGA's launcher styling, so these files are read.
# Paths relative to the VICARIUS harness root, one level up from modules.
HARNESS_ROOT = "/mnt/rip/vicarius_drive"
HARNESS_PATHS = (
    "vicarius_ui_os/static/theme.css",
    "vicarius_ui_os/templates/module_launcher.html",
)
LAUNCHER_TEMPLATE = "vicarius_ui_os/templates/module_launcher.html"
LAUNCHER_CSS = "vicarius_ui_os/static/launcher_styles.css"


def sha256_bytes(data):
    """Return the hex SHA-256 of data.

    Parameters:
        data: the bytes to hash.
    Returns:
        str: 64 hex characters.
    """
    return hashlib.sha256(data).hexdigest()


def run_ssh(host, command):
    """Run one command on the host and return its stdout bytes.

    Parameters:
        host: ssh host name.
        command: the shell command, as one string.
    Returns:
        bytes: the command's stdout.
    Raises:
        RuntimeError: when ssh or the command exits non-zero, naming the command
            and the stderr text.
    """
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", host, command],
                            capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"ssh {host} failed running {command[:120]!r}: "
            f"{result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def fetch_files(host, base, paths):
    """Read files from the host through one tar stream.

    Parameters:
        host: ssh host name.
        base: the directory the paths are relative to.
        paths: the relative file paths to read.
    Returns:
        dict: {relative path: file bytes}.
    Raises:
        RuntimeError: when a path is missing on the host or the stream is short.
    """
    quoted = " ".join(f"'{p}'" for p in paths)
    raw = run_ssh(host, f"cd '{base}' && tar cf - {quoted}")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for member in archive.getmembers():
            if member.isfile():
                files[member.name] = archive.extractfile(member).read()
    missing = [p for p in paths if p not in files]
    if missing:
        raise RuntimeError(f"files missing from the stream from {host}: {missing}")
    return files


def launcher_styles(template_bytes):
    """Pull the inline style block out of VICARIUS's project launcher page.

    Parameters:
        template_bytes: module_launcher.html as bytes.
    Returns:
        bytes: the CSS between its <style> tags.
    Raises:
        RuntimeError: when the template holds no style block.
    """
    text = template_bytes.decode("utf-8")
    start, end = text.find("<style>"), text.find("</style>")
    if start < 0 or end < start:
        raise RuntimeError("module_launcher.html holds no <style> block")
    return text[start + len("<style>"):end].encode("utf-8")


def write_vendor(files, harness_files, commit, dirty, repo):
    """Replace vendor/vega/ with the fetched files and write VENDORED.json.

    Parameters:
        files: {path relative to the VEGA checkout: bytes}.
        harness_files: {path relative to the harness root: bytes}.
        commit: the VEGA commit hash the working tree sits on.
        dirty: the list of vendored paths with uncommitted edits at copy time.
        repo: the checkout path on the mainframe, recorded for the reader.
    Returns:
        dict: the manifest that was written.
    """
    if VENDOR.exists():
        shutil.rmtree(VENDOR)
    entries = {}
    for rel, data in sorted(files.items()):
        dest = VENDOR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        entries[rel] = sha256_bytes(data)
    for rel, data in sorted(harness_files.items()):
        dest = VENDOR / "harness" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        entries[f"harness/{rel}"] = sha256_bytes(data)
    manifest = {
        "source_repo": repo,
        "commit": commit,
        "uncommitted_at_copy": dirty,
        "copied_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "files": entries,
    }
    (VENDOR / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    """Fetch the vendored files and report the commit and any uncommitted edits.

    Returns:
        int: 0 on success, 1 on an ssh or copy failure.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        commit = run_ssh(args.host, f"git -C '{args.repo}' rev-parse HEAD").decode().strip()
        status = run_ssh(args.host, f"git -C '{args.repo}' status --porcelain -- "
                         + " ".join(f"'{p}'" for p in VENDORED_PATHS)).decode()
        dirty = [line[3:] for line in status.splitlines() if line.strip()]
        if args.dry_run:
            print(f"commit {commit}; {len(VENDORED_PATHS)} files; uncommitted: {dirty}")
            return 0
        files = fetch_files(args.host, args.repo, VENDORED_PATHS)
        harness = fetch_files(args.host, HARNESS_ROOT, HARNESS_PATHS)
        # Only the page's styles are needed, so the page itself stays out of the copy.
        harness[LAUNCHER_CSS] = launcher_styles(harness.pop(LAUNCHER_TEMPLATE))
    except RuntimeError as exc:
        print(f"sync_from_vega: {exc}", file=sys.stderr)
        return 1
    manifest = write_vendor(files, harness, commit, dirty, args.repo)
    print(f"Copied {len(manifest['files'])} files from commit {commit[:10]}.")
    if dirty:
        print(f"Uncommitted at copy time: {', '.join(dirty)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
