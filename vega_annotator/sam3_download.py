"""Download SAM3 from the lab's GitHub release and install it on this computer.

Module: VEGA Annotator / sam3_download
Purpose: SAM3, Meta's 3.4 GB segmentation model, comes from the release tagged
    sam3-weights-1 on laurenkolinger/vega-annotator as two parts of one tar, each under
    GitHub's 2 GB file limit. sam3_release.json beside this file pins each part's size and
    SHA-256, so a changed, partial or swapped download is refused. The tar holds the same
    copy as the server folder's sam3/: SAM_LICENSE.txt, checksums.json, and
    models--facebook--sam3/ in Hugging Face's cache layout.
Steps, the same for setup's Step 4 and the home screen's DOWNLOAD SAM3:
    1. Check for 7.5 GB of free space: the parts, the unpacked copy, and room to spare.
    2. Download each part into the work folder. A part that stopped partway continues
       with an HTTP Range request, so a Mac that slept or lost its Wi-Fi picks up where
       it stopped.
    3. Check each part's size and SHA-256 against the pins, and delete a part that fails.
    4. Unpack both parts, read as one stream, into a temporary folder beside the SAM3
       folder, refusing any member that would land outside it.
    5. Check every file against the copy's checksums.json, as INSTALL SAM3 does.
    6. Put the copy in place of the SAM3 folder in one rename, keeping the old folder
       aside until the rename succeeds, then delete the parts.
    A failed setup download leaves a marker (failed_marker()), so the app never asks to
    start a 3.4 GB download again on its own; the person clicks DOWNLOAD SAM3. A success
    removes it. One download runs at a time on a computer.
Run: python -m vega_annotator.sam3_download --setup    (setup's Step 4)
     python -m vega_annotator.sam3_download --status   (installed, downloading, failed or missing)
"""

import argparse
import contextlib
import http.client
import math
import os
import re
import shutil
import sys
import tarfile
import traceback
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import VERSION, paths, projects, sam3_files
from .atomic import read_json_object, sha256_file, write_json_atomic
from .transfer import TransferError

RELEASE_PATH = Path(__file__).with_name("sam3_release.json")
MARKER_NAME = "sam3_download_failed.json"
LOCK_NAME = "sam3_download.lock"
WORK_SUBFOLDERS = ("downloads", "sam3")
UNPACK_PREFIX = ".sam3-unpack-"
ASIDE_PREFIX = ".sam3-old-"
FOLDER_TAG_LENGTH = 8
FREE_SPACE_BYTES = 7_500_000_000
BYTES_PER_GB = 1e9
CHUNK_BYTES = 1024 * 1024
NETWORK_TIMEOUT_S = 60
HTTP_OK = 200
HTTP_PARTIAL_CONTENT = 206
HTTP_NOT_FOUND = 404
HTTP_RANGE_NOT_SATISFIABLE = 416
SETUP_REPORT_STEPS = 10  # Setup's log gets a line as each step starts and at each tenth of it.
DOWNLOAD_MESSAGE = "Downloading SAM3"
PART_CHECK_MESSAGE = "Checking the download"
UNPACK_MESSAGE = "Unpacking SAM3"
STATE_INSTALLED = "installed"
STATE_DOWNLOADING = "downloading"
STATE_FAILED = "failed"
STATE_MISSING = "missing"
RELEASE_KEYS = {"tag", "base_url", "snapshot_commit", "parts", "license"}
FILE_KEYS = {"name", "size", "sha256"}
URL_SCHEMES = ("https://", "http://")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
CONTENT_RANGE_PATTERN = re.compile(r"bytes (\d+)-(\d+)/(\d+|\*)")
TRY_AGAIN = "Click DOWNLOAD SAM3 to try again."
FALLBACK = "If this happens again, use INSTALL FROM SERVER FOLDER."


class DownloadError(TransferError):
    """A SAM3 download that cannot go ahead or did not finish, with a sentence the person can act on.

    It is a TransferError, so the home screen's job runner shows the sentence as it is.
    """


def load_release(path=None):
    """Read the pinned SAM3 release record.

    Parameters:
        path: the record to read; defaults to sam3_release.json beside this module.
    Returns:
        dict: tag, base_url, snapshot_commit, parts (each with name, size and sha256), and
        license (name, size and sha256).
    Raises:
        DownloadError: when the record is missing or damaged.
    """
    path = Path(path) if path else RELEASE_PATH
    try:
        record = read_json_object(path)
    except ValueError as exc:
        raise DownloadError(f"The SAM3 release record cannot be read: {exc}. Reinstall VEGA Annotator.") from exc
    return _checked_release(record)


def failed_marker():
    """Return the file that records why setup's SAM3 download failed.

    Returns:
        Path: sam3_download_failed.json in the app's folder. It exists only after a failed
        setup download, until a download succeeds.
    """
    return paths.app_data_dir() / MARKER_NAME


def state():
    """Say where SAM3 stands on this computer, for the launcher.

    Returns:
        str: "installed" when SAM3 is ready; "downloading" while a download runs;
        "failed" after a failed setup download, when the app waits for the person to
        click DOWNLOAD SAM3; "missing" otherwise.
    """
    if sam3_files.installed_snapshot() is not None:
        return STATE_INSTALLED
    try:
        with _download_lock():
            pass
    except DownloadError:
        return STATE_DOWNLOADING
    return STATE_FAILED if failed_marker().exists() else STATE_MISSING


def download(progress=None, release=None, work_dir=None, opener=None):
    """Download SAM3 from the release, check it, and install it on this computer.

    Parameters:
        progress: optional callable (bytes done, bytes total, message), called through
            each step: downloading, checking the download, unpacking, and checking SAM3.
        release: the release record; defaults to load_release().
        work_dir: the folder the parts download into; defaults to downloads/sam3 in the
            app's folder. A part left there by a download that stopped is continued.
        opener: callable (url, start byte) returning an HTTP response with status,
            headers, read(size) and close(), as urllib's does; it exists for tests. The
            default asks for the bytes from start onward with a Range header.
    Returns:
        dict: installed (True) and size_gb, the weights' size. When the pinned snapshot
        is already installed, it returns at once and downloads nothing.
    Raises:
        DownloadError: with a sentence saying what failed and what to do. A part cut off
            partway is kept, so the next download continues it.
        TypeError: when progress or opener is not callable.
    """
    if progress is not None and not callable(progress):
        raise TypeError("progress must be a callable taking (done, total, message).")
    if opener is not None and not callable(opener):
        raise TypeError("opener must be a callable taking (url, start byte).")
    release = load_release() if release is None else _checked_release(release)
    report = progress or _ignore_progress
    work_dir = Path(work_dir) if work_dir else paths.app_data_dir().joinpath(*WORK_SUBFOLDERS)
    with _download_lock():
        if _installed_commit() == release["snapshot_commit"]:
            _clear_marker()
            return _installed_result()
        try:
            _remove_leftovers()
            _check_space(release, work_dir)
            work_dir.mkdir(parents=True, exist_ok=True)
            parts = _download_parts(release, work_dir, opener or _open_url, report)
            _check_parts(release, parts, report)
            _install_from_parts(release, parts, report)
            for part in parts:
                part.unlink()
            with contextlib.suppress(OSError):
                work_dir.rmdir()
        except OSError as exc:
            raise DownloadError(f"SAM3 could not be saved on this computer ({_reason(exc)}). "
                                f"Check the disk, then click DOWNLOAD SAM3 to continue.") from exc
    _clear_marker()
    return _installed_result()


def main(argv=None):
    """Run the downloader for setup.sh, or tell the launcher where SAM3 stands.

    Parameters:
        argv: the command line, without the program name; defaults to sys.argv[1:].
            --setup downloads SAM3 as setup's Step 4, printing one line per step and at
            each tenth of it. --status prints installed, downloading, failed or missing.
    Returns:
        int: 0 on success, or after printing the state; 1 when the download failed,
        after writing the reason to failed_marker().
    """
    parser = argparse.ArgumentParser(prog="python -m vega_annotator.sam3_download",
                                     description="Download SAM3 from the lab's GitHub release and install it.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--setup", action="store_true",
                      help="download SAM3 as setup's Step 4, printing one line per step")
    mode.add_argument("--status", action="store_true", help="print installed, downloading, failed or missing")
    args = parser.parse_args(argv)
    if args.status:
        print(state())
        return 0
    try:
        release = load_release()
        already = _installed_commit() == release["snapshot_commit"]
        print(f"SAM3 release {release['tag']}, from {release['base_url']}", flush=True)
        result = download(progress=_setup_printer(), release=release)
    except DownloadError as exc:
        return _setup_failed(str(exc))
    except Exception as exc:  # Setup records every failure, so the app opens and waits for a click.
        traceback.print_exc(file=sys.stdout)
        return _setup_failed(f"An unexpected error stopped the SAM3 download "
                             f"({type(exc).__name__}: {exc}). {TRY_AGAIN}")
    print("SAM3 is already installed." if already else f"SAM3 is installed: {result['size_gb']} GB.", flush=True)
    return 0


def _checked_release(record):
    """Return a release record unchanged when it is complete.

    Parameters:
        record: the parsed sam3_release.json, or a record in its form.
    Returns:
        dict: the record.
    Raises:
        DownloadError: naming what is wrong with it.
    """
    problem = _release_problem(record)
    if problem:
        raise DownloadError(f"The SAM3 release record is damaged: {problem}. Reinstall VEGA Annotator.")
    return record


def _release_problem(record):
    """Return what is wrong with a release record, or "" when nothing is.

    Parameters:
        record: the record to check.
    Returns:
        str: a clause naming the problem, or "".
    """
    if not isinstance(record, dict) or set(record) != RELEASE_KEYS:
        return f"it should hold exactly {', '.join(sorted(RELEASE_KEYS))}"
    if not isinstance(record["tag"], str) or not record["tag"]:
        return "its tag is blank"
    if not isinstance(record["base_url"], str) or not record["base_url"].startswith(URL_SCHEMES):
        return "its download address is not a web address"
    if not isinstance(record["snapshot_commit"], str) or not COMMIT_PATTERN.fullmatch(record["snapshot_commit"]):
        return "its snapshot commit is not 40 hexadecimal characters"
    if not isinstance(record["parts"], list) or not record["parts"]:
        return "it lists no parts"
    for entry in [*record["parts"], record["license"]]:
        problem = _file_problem(entry)
        if problem:
            return problem
    names = [part["name"] for part in record["parts"]]
    if len(set(names)) != len(names):
        return "two of its parts share a name"
    return ""


def _file_problem(entry):
    """Return what is wrong with one file's pin, or "" when nothing is.

    Parameters:
        entry: a part's or the license's {name, size, sha256}.
    Returns:
        str: a clause naming the problem, or "".
    """
    if not isinstance(entry, dict) or set(entry) != FILE_KEYS:
        return "each file in it should hold exactly name, size and sha256"
    name = entry["name"]
    if not isinstance(name, str) or not name or name.startswith(".") or "/" in name or "\\" in name:
        return f"{name!r} is not a plain file name"
    if type(entry["size"]) is not int or entry["size"] <= 0:
        return f"the size of {name} is not a whole number above 0"
    if not isinstance(entry["sha256"], str) or not sam3_files.SHA256_PATTERN.fullmatch(entry["sha256"]):
        return f"the SHA-256 of {name} is not 64 hexadecimal characters"
    return ""


@contextlib.contextmanager
def _download_lock():
    """Hold the lock that lets one SAM3 download run at a time on this computer.

    Setup and the home screen run in different processes, so the lock is a file lock,
    which the system releases when its process ends, however it ends.

    Yields:
        None, while the lock is held.
    Raises:
        DownloadError: when another download holds the lock.
    """
    import fcntl  # Windows has fcntl only after shims.install(), which the home server runs at start.

    path = paths.app_data_dir() / LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DownloadError("SAM3 is already downloading in another window. "
                                "Wait for that download to finish.") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _installed_commit():
    """Return the commit of the SAM3 snapshot installed on this computer, or None."""
    snapshot = sam3_files.installed_snapshot()
    return snapshot.name if snapshot is not None else None


def _installed_result():
    """Return download()'s result for the SAM3 now installed."""
    return {"installed": True, "size_gb": sam3_files.status()["size_gb"]}


def _ignore_progress(_done, _total, _message):
    """Stand in for a progress callable when the caller passes none."""


def _clear_marker():
    """Delete the failed-download marker, if there is one."""
    failed_marker().unlink(missing_ok=True)


def _setup_failed(reason):
    """Record why setup's download failed, and print it for setup's log.

    Parameters:
        reason: the sentence for the person.
    Returns:
        int: 1, setup's code for a failed step.
    """
    try:
        write_json_atomic(failed_marker(), {"reason": reason, "at": projects.now_ast()})
    except OSError as exc:
        print(f"The reason could not be saved in {failed_marker()}: {exc}", flush=True)
    print(f"SAM3 did not download: {reason}", flush=True)
    return 1


def _setup_printer():
    """Return a progress callable that prints a line for setup's log as each step starts and at each tenth.

    Returns:
        callable: (bytes done, bytes total, message).
    """
    shown = {"message": None, "tenth": -1}

    def report(done, total, message):
        """Print a line when a step starts and each time it passes another tenth."""
        tenth = min(done * SETUP_REPORT_STEPS // total, SETUP_REPORT_STEPS) if total > 0 else SETUP_REPORT_STEPS
        if message != shown["message"]:
            shown.update(message=message, tenth=-1)
        if tenth > shown["tenth"]:
            shown["tenth"] = tenth
            print(f"{message}: {tenth * 100 // SETUP_REPORT_STEPS}% of {total / BYTES_PER_GB:.1f} GB", flush=True)

    return report


def _reason(exc):
    """Return the short reason an exception carries, to end a sentence with.

    Parameters:
        exc: the exception, often a URLError wrapping the socket's own.
    Returns:
        str: such as "timed out" or "Connection refused".
    """
    inner = getattr(exc, "reason", None)
    if isinstance(inner, BaseException):
        exc = inner
    elif isinstance(inner, str) and inner:
        return inner
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror
    return str(exc) or type(exc).__name__


def _gb(count):
    """Return a byte count as gigabytes with one decimal, the way the messages show it."""
    return f"{count / BYTES_PER_GB:.1f}"


def _remove_leftovers():
    """Delete unpack and set-aside folders left behind by a download that a crash or power cut stopped."""
    for prefix in (UNPACK_PREFIX, ASIDE_PREFIX):
        for folder in paths.sam3_home().parent.glob(f"{prefix}*"):
            shutil.rmtree(folder, ignore_errors=True)


def _size_of(path):
    """Return a file's size in bytes, or 0 when it does not exist."""
    return path.stat().st_size if path.is_file() else 0


def _check_space(release, work_dir):
    """Refuse to start when the app folder's disk lacks room for the parts and the unpacked copy.

    Parts already downloaded count toward the need, so a download that stopped partway
    can continue on a fuller disk.

    Parameters:
        release: the release record.
        work_dir: the folder the parts download into.
    Returns:
        None.
    Raises:
        DownloadError: saying how much space is needed and how much the disk has.
    """
    have = sum(min(_size_of(work_dir / part["name"]), part["size"]) for part in release["parts"])
    needed = FREE_SPACE_BYTES - have
    free = shutil.disk_usage(paths.app_data_dir()).free
    if free < needed:
        short = math.ceil((needed - free) / BYTES_PER_GB * SETUP_REPORT_STEPS) / SETUP_REPORT_STEPS
        raise DownloadError(f"Downloading SAM3 needs {_gb(needed)} GB of free space, and the disk has "
                            f"{_gb(free)} GB. Free up {short:.1f} GB, then click DOWNLOAD SAM3.")


def _open_url(url, start):
    """Open one release file for download, from a byte onward.

    Parameters:
        url: the file's address.
        start: the first byte wanted; above 0, the request carries a Range header, which
            urllib keeps when GitHub redirects it to the file store.
    Returns:
        http.client.HTTPResponse: the open response.
    Raises:
        urllib.error.HTTPError: for an error answer; OSError when the server cannot be reached.
    """
    headers = {"User-Agent": f"VEGA-Annotator/{VERSION}"}
    if start:
        headers["Range"] = f"bytes={start}-"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=NETWORK_TIMEOUT_S)


def _download_parts(release, work_dir, opener, report):
    """Download every part into work_dir, continuing any part that stopped partway.

    Parameters:
        release: the release record.
        work_dir: the folder the parts download into.
        opener: callable (url, start byte) returning an HTTP response.
        report: the progress callable.
    Returns:
        list of Path: the downloaded parts, in the release's order.
    Raises:
        DownloadError: from _fetch_part.
    """
    total = sum(part["size"] for part in release["parts"])
    before = 0
    files = []
    for part in release["parts"]:
        path = work_dir / part["name"]
        _fetch_part(part, f"{release['base_url'].rstrip('/')}/{part['name']}", path, opener, before, total, report)
        before += part["size"]
        files.append(path)
    return files


def _fetch_part(part, url, path, opener, before, total, report):
    """Download one part into path, continuing from the bytes already there.

    Parameters:
        part: the part's pin: name, size and sha256.
        url: where to download it.
        path: the file to write.
        opener: callable (url, start byte) returning an HTTP response.
        before: bytes of the parts before this one, for progress.
        total: bytes of all parts, for progress.
        report: the progress callable.
    Returns:
        None.
    Raises:
        DownloadError: when the download fails or the part has the wrong size. A part cut
            off partway is kept for the next try; a part of the wrong size is deleted.
    """
    name, size = part["name"], part["size"]
    have = _size_of(path)
    if have > size:
        path.unlink()
        have = 0
    report(before + have, total, DOWNLOAD_MESSAGE)
    if have == size:
        return
    response = _request(opener, url, have, path, name)
    with contextlib.closing(response):
        have = _start_of(response, have, path, name)
        length = response.headers.get("Content-Length")
        promised = have + int(length) if length and length.isdigit() else size
        with open(path, "ab" if have else "wb") as out:
            while have <= size:
                try:
                    chunk = response.read(CHUNK_BYTES)
                except (OSError, http.client.HTTPException) as exc:
                    raise DownloadError(_stopped(before + have, total, _reason(exc))) from exc
                if not chunk:
                    break
                out.write(chunk)
                have += len(chunk)
                report(before + have, total, DOWNLOAD_MESSAGE)
    if have < promised and have < size:
        raise DownloadError(_stopped(before + have, total, "the connection closed early"))
    if have != size:
        path.unlink()
        raise DownloadError(f"{name} from the SAM3 release is {have:,} bytes, and VEGA Annotator expects "
                            f"{size:,}, so it was deleted. {TRY_AGAIN} {FALLBACK}")


def _stopped(done, total, reason):
    """Return the sentence for a download cut off partway, which the next try continues."""
    return (f"The SAM3 download stopped at {_gb(done)} of {_gb(total)} GB ({reason}). "
            f"Click DOWNLOAD SAM3 to continue from there.")


def _request(opener, url, start, path, name):
    """Ask for one part from a byte onward.

    Parameters:
        opener: callable (url, start byte) returning an HTTP response.
        url: the part's address.
        start: the first byte wanted.
        path: the part's file, deleted when the server says it cannot continue it.
        name: the part's name, for messages.
    Returns:
        the open response.
    Raises:
        DownloadError: when GitHub cannot be reached or answers with an error.
    """
    try:
        return opener(url, start)
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code == HTTP_NOT_FOUND:
            raise DownloadError(f"GitHub has no {name} in the SAM3 release. Use INSTALL FROM SERVER FOLDER, "
                                f"or click DOWNLOAD SAM3 again later.") from exc
        if exc.code == HTTP_RANGE_NOT_SATISFIABLE:
            path.unlink(missing_ok=True)
            raise DownloadError(f"The part of {name} downloaded earlier does not fit the release, so it was "
                                f"deleted. Click DOWNLOAD SAM3 to download it again.") from exc
        raise DownloadError(f"GitHub answered {exc.code} when asked for {name}. "
                            f"Click DOWNLOAD SAM3 to try again later.") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise DownloadError(f"VEGA Annotator could not reach GitHub to download SAM3 ({_reason(exc)}). "
                            f"Check the internet connection, then click DOWNLOAD SAM3.") from exc


def _start_of(response, have, path, name):
    """Return the byte the response's body starts at.

    Parameters:
        response: the open response.
        have: the bytes of the part already on disk.
        path: the part's file, deleted when the server sends it from the wrong place.
        name: the part's name, for messages.
    Returns:
        int: have, when the server continues the part (206); 0, when it sends the whole
        file (200), which then replaces the bytes on disk.
    Raises:
        DownloadError: for any other answer, or a continuation from the wrong byte.
    """
    if response.status == HTTP_OK:
        return 0
    if response.status == HTTP_PARTIAL_CONTENT:
        match = CONTENT_RANGE_PATTERN.fullmatch(response.headers.get("Content-Range") or "")
        if match and int(match.group(1)) == have:
            return have
        path.unlink(missing_ok=True)
        raise DownloadError(f"GitHub continued {name} from the wrong place, so the part downloaded earlier "
                            f"was deleted. Click DOWNLOAD SAM3 to download it again.")
    raise DownloadError(f"GitHub answered {response.status} when asked for {name}. "
                        f"Click DOWNLOAD SAM3 to try again later.")


def _check_parts(release, files, report):
    """Check each downloaded part's SHA-256 against its pin, deleting a part that differs.

    Parameters:
        release: the release record.
        files: the downloaded parts, in the release's order.
        report: the progress callable.
    Returns:
        None.
    Raises:
        DownloadError: naming the part that differs.
    """
    total = sum(part["size"] for part in release["parts"])
    done = [0]

    def on_chunk(count):
        """Report progress for one checked chunk."""
        done[0] += count
        report(done[0], total, PART_CHECK_MESSAGE)

    for part, path in zip(release["parts"], files):
        if sha256_file(path, on_chunk) != part["sha256"]:
            path.unlink()
            raise DownloadError(f"{part['name']} does not match the checksum VEGA Annotator expects, so it was "
                                f"deleted. Click DOWNLOAD SAM3 to download it again. {FALLBACK}")


def _install_from_parts(release, files, report):
    """Unpack the parts beside the SAM3 folder, check the copy, and put it in place.

    Parameters:
        release: the release record.
        files: the checked parts, in the release's order.
        report: the progress callable.
    Returns:
        None.
    Raises:
        DownloadError: when the parts cannot be unpacked or the copy fails its check.
        OSError: when the disk refuses a write or a move; the old SAM3 folder stays.
    """
    home = paths.sam3_home()
    unpack = home.parent / f"{UNPACK_PREFIX}{uuid.uuid4().hex[:FOLDER_TAG_LENGTH]}"
    try:
        _unpack(files, unpack, report)
        _check_copy(unpack, release, report)
        _arrange(unpack)
        _swap(unpack, home)
    finally:
        shutil.rmtree(unpack, ignore_errors=True)


def _unpack(files, folder, report):
    """Unpack the parts, read one after another as one tar, into folder.

    Tar's "data" filter refuses any member that would land outside folder, any link
    that points outside it, and any device file.

    Parameters:
        files: the parts, in order.
        folder: the new folder to unpack into.
        report: the progress callable.
    Returns:
        None.
    Raises:
        DownloadError: when the tar is damaged or holds a refused member; the parts are
            then deleted, because unpacking them again would fail the same way.
    """
    total = sum(path.stat().st_size for path in files)
    done = [0]

    def on_read(count):
        """Report progress for one read of the joined parts."""
        done[0] += count
        report(done[0], total, UNPACK_MESSAGE)

    folder.mkdir()
    stream = _JoinedFiles(files, on_read)
    try:
        # Reading and copying in equal large chunks unpacks 3.4 GB about five times faster than
        # tarfile's small default copy buffer, which re-slices the stream buffer on every read.
        with tarfile.open(fileobj=stream, mode="r|", bufsize=CHUNK_BYTES, copybufsize=CHUNK_BYTES) as archive:
            archive.extractall(folder, filter="data")
    except tarfile.TarError as exc:
        stream.close()
        for path in files:
            path.unlink(missing_ok=True)
        raise DownloadError(f"The SAM3 download could not be unpacked ({exc}), so its parts were deleted. "
                            f"Click DOWNLOAD SAM3 to download them again. {FALLBACK}") from exc
    finally:
        stream.close()


def _check_copy(folder, release, report):
    """Check every unpacked file against the copy's checksums.json, and the snapshot against the pin.

    Parameters:
        folder: the unpacked copy.
        release: the release record.
        report: the progress callable.
    Returns:
        None.
    Raises:
        DownloadError: naming what differs. The parts are kept, so the next try unpacks
            them again without downloading.
    """
    try:
        sam3_files.verify_copy(folder, report)
    except TransferError as exc:
        raise DownloadError(f"The unpacked SAM3 did not pass its check: {exc} "
                            f"Click DOWNLOAD SAM3 to unpack it again from the download.") from exc
    snapshot = sam3_files.installed_snapshot(folder / paths.SAM3_MODEL_FOLDER)
    if snapshot is None or snapshot.name != release["snapshot_commit"]:
        raise DownloadError(f"The SAM3 download does not hold snapshot {release['snapshot_commit']}, the one "
                            f"VEGA Annotator expects. Use INSTALL FROM SERVER FOLDER instead.")


def _arrange(folder):
    """Lay out the unpacked copy as INSTALL SAM3 leaves the SAM3 folder.

    The license stays at the top and the model moves into Hugging Face's cache folder.
    checksums.json goes, because its paths name the copy's own layout.

    Parameters:
        folder: the checked copy.
    Returns:
        None.
    """
    target = folder / paths.sam3_model_dir().relative_to(paths.sam3_home())
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(folder / paths.SAM3_MODEL_FOLDER, target)
    (folder / sam3_files.CHECKSUMS_NAME).unlink()


def _swap(folder, home):
    """Put the new copy in place of the SAM3 folder with one rename.

    The old folder moves aside first and comes back if the rename fails, so SAM3 is never
    left half replaced; it is deleted once the new copy is in place.

    Parameters:
        folder: the arranged copy, beside home.
        home: the SAM3 folder.
    Returns:
        None.
    Raises:
        OSError: when the rename fails, after the old folder is back.
    """
    aside = None
    if home.exists():
        aside = home.parent / f"{ASIDE_PREFIX}{uuid.uuid4().hex[:FOLDER_TAG_LENGTH]}"
        os.replace(home, aside)
    try:
        os.replace(folder, home)
    except OSError:
        if aside is not None:
            os.replace(aside, home)
        raise
    if aside is not None:
        shutil.rmtree(aside, ignore_errors=True)


class _JoinedFiles:
    """Read several files one after another as one stream, reporting the size of each read."""

    def __init__(self, files, on_read):
        """Remember the files, opening none yet.

        Parameters:
            files: the paths, in order.
            on_read: callable given the byte count of each read.
        """
        self._pending = list(files)
        self._handle = None
        self._on_read = on_read

    def read(self, size=-1):
        """Return up to size bytes, moving on to the next file at the end of each.

        Parameters:
            size: the most bytes to return; -1 for the rest of the current file.
        Returns:
            bytes: b"" once the last file is read to its end.
        """
        while True:
            if self._handle is None:
                if not self._pending:
                    return b""
                self._handle = open(self._pending.pop(0), "rb")
            data = self._handle.read(size)
            if data:
                self._on_read(len(data))
                return data
            self._handle.close()
            self._handle = None

    def close(self):
        """Close the file being read, if any."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None


if __name__ == "__main__":
    sys.exit(main())
