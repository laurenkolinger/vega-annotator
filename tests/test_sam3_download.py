"""Tests for downloading SAM3 from the lab's GitHub release, and for checking a SAM3 copy.

A release server on this computer stands in for GitHub. It serves two parts cut from a
small tar in the release's layout, answers Range requests, and can drop a connection
partway through a file, so no test downloads the real 3.4 GB.
"""

import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import threading
from collections import namedtuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vega_annotator import paths, sam3_download, sam3_files
from vega_annotator.transfer import TransferError

COMMIT = "0123456789abcdef0123456789abcdef01234567"
OLD_COMMIT = "fedcba9876543210fedcba9876543210fedcba98"
LICENSE = "SAM License\nA stand-in for Meta’s license, with a curly apostrophe.\n".encode("utf-8")
WEIGHTS = bytes(range(256)) * 400
PART_NAMES = ("sam3-hf.tar.part1", "sam3-hf.tar.part2")
SPLIT_OFFSET = 123  # Parts split off a 512-byte boundary, as the real ones may.
POLL_INTERVAL_S = 0.02  # How often the fake server checks for a stop, so each test ends quickly.
DiskUsage = namedtuple("DiskUsage", "total used free")


def sha256(data):
    """Return the hex SHA-256 of some bytes."""
    return hashlib.sha256(data).hexdigest()


def build_tar(extra=None):
    """Return a small SAM3 tar in the release's layout: the license, checksums.json, and
    models--facebook--sam3/ with refs/main and one snapshot.

    Parameters:
        extra: optional {member name: bytes} appended after the real members.
    Returns:
        bytes: the tar.
    """
    model = sam3_files.paths.SAM3_MODEL_FOLDER
    files = {
        "SAM_LICENSE.txt": LICENSE,
        f"{model}/refs/main": COMMIT.encode("ascii"),
        f"{model}/snapshots/{COMMIT}/config.json": b"{}",
        f"{model}/snapshots/{COMMIT}/model.safetensors": WEIGHTS,
    }
    listing = {rel: {"size": len(data), "sha256": sha256(data)} for rel, data in files.items()}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:

        def add_file(name, data):
            """Add one regular file to the tar."""
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o664
            archive.addfile(info, io.BytesIO(data))

        def add_folder(name):
            """Add one folder entry to the tar, as GNU tar writes them."""
            info = tarfile.TarInfo(name)
            info.type, info.mode = tarfile.DIRTYPE, 0o775
            archive.addfile(info)

        add_file("SAM_LICENSE.txt", LICENSE)
        add_file("checksums.json", json.dumps(listing, indent=2).encode("utf-8"))
        for folder in (model, f"{model}/snapshots", f"{model}/snapshots/{COMMIT}", f"{model}/refs"):
            add_folder(folder)
        for rel, data in files.items():
            if rel != "SAM_LICENSE.txt":
                add_file(rel, data)
        for name, data in (extra or {}).items():
            add_file(name, data)
    return buffer.getvalue()


class FakeRelease:
    """A release server on this computer that stands in for GitHub.

    It serves files by name, answers "Range: bytes=N-" with 206, records every request,
    counts the bytes it sends, and can drop a connection once partway through a file, as a
    sleeping Mac or a lost Wi-Fi connection would.
    """

    def __init__(self):
        """Start serving on a free port."""
        self.files = {}
        self.requests = []
        self.sent = {}
        self.cut_after = {}
        self.ignore_range = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, args=(POLL_INTERVAL_S,), daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/releases/download/sam3-weights-test"

    def _handler(self):
        """Return the request handler class, bound to this release."""
        release = self

        class Handler(BaseHTTPRequestHandler):
            """Serve one release file, whole or from a byte offset."""

            def do_GET(self):
                """Answer one download request."""
                name = self.path.rsplit("/", 1)[-1]
                wanted = self.headers.get("Range")
                release.requests.append((name, wanted))
                data = release.files.get(name)
                if data is None:
                    self.send_error(404)
                    return
                start = 0
                if wanted and not release.ignore_range:
                    start = int(re.fullmatch(r"bytes=(\d+)-", wanted).group(1))
                    if start >= len(data):
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{len(data)}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                else:
                    self.send_response(200)
                body = data[start:]
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                cut = release.cut_after.pop(name, None)
                if cut is not None:
                    body = body[:cut]
                    self.close_connection = True
                self.wfile.write(body)
                release.sent[name] = release.sent.get(name, 0) + len(body)

            def log_message(self, *args):
                """Keep the test output quiet."""

        return Handler

    def publish(self, tar_bytes):
        """Serve a tar as two parts and return a release record that pins them.

        Parameters:
            tar_bytes: the whole tar.
        Returns:
            dict: a record in sam3_release.json's form, pointing at this server.
        """
        cut = len(tar_bytes) // 2 + SPLIT_OFFSET
        parts = dict(zip(PART_NAMES, (tar_bytes[:cut], tar_bytes[cut:])))
        self.files.update(parts)
        return {
            "tag": "sam3-weights-test",
            "base_url": self.base_url,
            "snapshot_commit": COMMIT,
            "parts": [{"name": name, "size": len(data), "sha256": sha256(data)} for name, data in parts.items()],
            "license": {"name": "SAM_LICENSE.txt", "size": len(LICENSE), "sha256": sha256(LICENSE)},
        }

    def stop(self):
        """Stop serving. A second call does nothing."""
        if self.thread.is_alive():
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()


@pytest.fixture
def fake_release():
    """Return a running FakeRelease, stopped after the test."""
    release = FakeRelease()
    yield release
    release.stop()


@pytest.fixture
def work(tmp_path):
    """Return the folder the parts download into."""
    return tmp_path / "work"


def flip_one_byte(data):
    """Return data with its middle byte changed."""
    middle = len(data) // 2
    return data[:middle] + bytes([data[middle] ^ 0xFF]) + data[middle + 1:]


def leftovers():
    """Return the unpack and set-aside folders left beside the SAM3 folder."""
    return sorted(path.name for path in paths.app_data_dir().glob(".sam3-*"))


def test_download_installs_and_removes_parts(fake_release, work):
    release = fake_release.publish(build_tar())
    seen = []
    result = sam3_download.download(progress=lambda done, total, message: seen.append((done, total, message)),
                                    release=release, work_dir=work)
    assert result == {"installed": True, "size_gb": 0.0}
    snapshot = sam3_files.installed_snapshot()
    assert snapshot is not None and snapshot.name == COMMIT
    assert (snapshot / "model.safetensors").read_bytes() == WEIGHTS
    assert (paths.sam3_home() / "SAM_LICENSE.txt").read_bytes() == LICENSE
    assert not list(work.glob("*.part*"))
    assert leftovers() == []
    total = sum(part["size"] for part in release["parts"])
    assert (total, total, "Downloading SAM3") in seen


def test_download_refuses_a_corrupt_part(fake_release, work):
    release = fake_release.publish(build_tar())
    fake_release.files[PART_NAMES[1]] = flip_one_byte(fake_release.files[PART_NAMES[1]])
    with pytest.raises(sam3_download.DownloadError, match=re.escape(PART_NAMES[1])):
        sam3_download.download(release=release, work_dir=work)
    assert sam3_files.installed_snapshot() is None
    assert not (work / PART_NAMES[1]).exists()
    assert leftovers() == []


def test_download_refuses_a_short_part(fake_release, work):
    release = fake_release.publish(build_tar())
    fake_release.files[PART_NAMES[0]] = fake_release.files[PART_NAMES[0]][:-100]
    with pytest.raises(sam3_download.DownloadError, match=re.escape(PART_NAMES[0])):
        sam3_download.download(release=release, work_dir=work)
    assert sam3_files.installed_snapshot() is None
    assert not (work / PART_NAMES[0]).exists()


def test_download_resumes_a_cut_part(fake_release, work):
    release = fake_release.publish(build_tar())
    part = release["parts"][0]
    cut = part["size"] // 3
    fake_release.cut_after[part["name"]] = cut
    with pytest.raises(sam3_download.DownloadError, match="stopped at .* continue from there"):
        sam3_download.download(release=release, work_dir=work)
    assert (work / part["name"]).stat().st_size == cut
    assert sam3_files.installed_snapshot() is None
    fake_release.requests.clear()
    fake_release.sent.clear()
    sam3_download.download(release=release, work_dir=work)
    assert (part["name"], f"bytes={cut}-") in fake_release.requests
    assert fake_release.sent[part["name"]] == part["size"] - cut < part["size"]
    assert sam3_files.installed_snapshot() is not None


def test_download_starts_a_part_again_when_the_server_ignores_the_range(fake_release, work):
    release = fake_release.publish(build_tar())
    part = release["parts"][0]
    work.mkdir()
    (work / part["name"]).write_bytes(fake_release.files[part["name"]][:500])
    fake_release.ignore_range = True
    sam3_download.download(release=release, work_dir=work)
    assert (part["name"], "bytes=500-") in fake_release.requests
    assert fake_release.sent[part["name"]] == part["size"]
    assert sam3_files.installed_snapshot() is not None


def test_download_refuses_without_space(fake_release, work, monkeypatch):
    release = fake_release.publish(build_tar())
    monkeypatch.setattr(sam3_download.shutil, "disk_usage", lambda path: DiskUsage(10**12, 10**12, 7_000_000_000))
    opened = []
    with pytest.raises(sam3_download.DownloadError, match=r"needs 7\.5 GB.*has 7\.0 GB"):
        sam3_download.download(release=release, work_dir=work, opener=lambda url, start: opened.append(url))
    assert opened == []
    assert fake_release.requests == []


def test_download_refuses_a_tar_that_escapes(fake_release, work):
    release = fake_release.publish(build_tar(extra={"../x": b"outside"}))
    with pytest.raises(sam3_download.DownloadError, match="unpacked"):
        sam3_download.download(release=release, work_dir=work)
    assert not (paths.app_data_dir() / "x").exists()
    assert not (paths.app_data_dir().parent / "x").exists()
    assert sam3_files.installed_snapshot() is None
    assert leftovers() == []
    assert not list(work.glob("*.part*"))


def test_download_replaces_an_older_copy(fake_release, work):
    old_model = paths.sam3_model_dir()
    (old_model / "refs").mkdir(parents=True)
    (old_model / "refs" / "main").write_text(OLD_COMMIT, encoding="utf-8")
    (old_model / "snapshots" / OLD_COMMIT).mkdir(parents=True)
    (old_model / "snapshots" / OLD_COMMIT / "model.safetensors").write_bytes(b"old weights")
    (paths.sam3_cache_dir()).mkdir(parents=True)
    release = fake_release.publish(build_tar())
    sam3_download.download(release=release, work_dir=work)
    assert sam3_files.installed_snapshot().name == COMMIT
    assert not (old_model / "snapshots" / OLD_COMMIT).exists()
    assert not paths.sam3_cache_dir().exists()
    assert leftovers() == []


def test_download_puts_the_older_copy_back_when_the_last_move_fails(fake_release, work, monkeypatch):
    old_model = paths.sam3_model_dir()
    (old_model / "refs").mkdir(parents=True)
    (old_model / "refs" / "main").write_text(OLD_COMMIT, encoding="utf-8")
    (old_model / "snapshots" / OLD_COMMIT).mkdir(parents=True)
    (old_model / "snapshots" / OLD_COMMIT / "model.safetensors").write_bytes(b"old weights")
    release = fake_release.publish(build_tar())
    real_replace = os.replace

    def replace(src, dst):
        """Fail the one rename that puts the new copy in place, as a full disk would."""
        if str(dst) == str(paths.sam3_home()) and ".sam3-unpack-" in str(src):
            raise OSError(28, "No space left on device")
        real_replace(src, dst)

    monkeypatch.setattr(sam3_download.os, "replace", replace)
    with pytest.raises(sam3_download.DownloadError, match="No space left"):
        sam3_download.download(release=release, work_dir=work)
    monkeypatch.setattr(sam3_download.os, "replace", real_replace)
    assert sam3_files.installed_snapshot().name == OLD_COMMIT
    assert leftovers() == []
    assert sorted(path.name for path in work.iterdir()) == list(PART_NAMES)


def test_download_does_nothing_when_sam3_is_installed(fake_release, work):
    release = fake_release.publish(build_tar())
    sam3_download.download(release=release, work_dir=work)
    fake_release.requests.clear()
    assert sam3_download.download(release=release, work_dir=work) == {"installed": True, "size_gb": 0.0}
    assert fake_release.requests == []


def test_download_refuses_while_another_download_runs(fake_release, work):
    release = fake_release.publish(build_tar())
    with sam3_download._download_lock():
        assert sam3_download.state() == "downloading"
        with pytest.raises(sam3_download.DownloadError, match="already downloading"):
            sam3_download.download(release=release, work_dir=work)
    assert fake_release.requests == []


def test_download_says_when_the_release_is_missing(fake_release, work):
    release = fake_release.publish(build_tar())
    fake_release.files.clear()
    with pytest.raises(sam3_download.DownloadError, match="INSTALL FROM SERVER FOLDER"):
        sam3_download.download(release=release, work_dir=work)


def test_download_says_when_github_cannot_be_reached(fake_release, work):
    release = fake_release.publish(build_tar())
    fake_release.stop()
    with pytest.raises(sam3_download.DownloadError, match="internet connection"):
        sam3_download.download(release=release, work_dir=work)


def test_download_refuses_a_bad_release_record(work):
    with pytest.raises(sam3_download.DownloadError, match="release record"):
        sam3_download.download(release={"tag": "x", "parts": []}, work_dir=work)
    with pytest.raises(TypeError):
        sam3_download.download(progress="not a function", work_dir=work)


def test_main_setup_writes_marker_on_failure(fake_release, monkeypatch, capsys):
    release = fake_release.publish(build_tar())
    good = dict(fake_release.files)
    fake_release.files[PART_NAMES[1]] = flip_one_byte(good[PART_NAMES[1]])
    monkeypatch.setattr(sam3_download, "load_release", lambda path=None: release)
    assert sam3_download.main(["--setup"]) == 1
    marker = json.loads(sam3_download.failed_marker().read_text(encoding="utf-8"))
    assert PART_NAMES[1] in marker["reason"] and marker["at"]
    assert sam3_download.failed_marker() == paths.app_data_dir() / "sam3_download_failed.json"
    assert sam3_download.state() == "failed"
    out = capsys.readouterr().out
    assert "Downloading SAM3: 100%" in out and "SAM3 did not download" in out
    fake_release.files.update(good)
    assert sam3_download.main(["--setup"]) == 0
    assert not sam3_download.failed_marker().exists()
    assert sam3_download.state() == "installed"
    assert "SAM3 is installed" in capsys.readouterr().out


def test_main_status_names_the_state(capsys):
    assert sam3_download.main(["--status"]) == 0
    assert capsys.readouterr().out.strip() == "missing"
    with pytest.raises(SystemExit):
        sam3_download.main([])


def test_release_pins_match_the_published_release():
    base = "https://github.com/laurenkolinger/vega-annotator/releases/download/sam3-weights-1"
    assert sam3_download.load_release() == {
        "tag": "sam3-weights-1",
        "base_url": base,
        "snapshot_commit": "3c879f39826c281e95690f02c7821c4de09afae7",
        "parts": [
            {"name": "sam3-hf.tar.part1", "size": 1887436800,
             "sha256": "5b8694330567d3b8553eb0ee263ecf8d2fa4c9829e164e15f78976124bdf220c"},
            {"name": "sam3-hf.tar.part2", "size": 1557596160,
             "sha256": "3763349eef5f27919f5ec0c48a1af117939c5c5349ca3257e4c3a97d9b16a794"},
        ],
        "license": {"name": "SAM_LICENSE.txt", "size": 7353,
                    "sha256": "4dea99bfaa016e21bc860d73f344236bd1e5c4977d1a9a8fd32f822b500ae1be"},
    }


def test_load_release_refuses_a_damaged_record(tmp_path):
    record = sam3_download.load_release()
    record["parts"][0]["name"] = "../part1"
    path = tmp_path / "release.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(sam3_download.DownloadError, match="release record"):
        sam3_download.load_release(path)


def unpack_copy(folder):
    """Unpack the test tar into folder, as a SAM3 copy in the server folder's layout."""
    with tarfile.open(fileobj=io.BytesIO(build_tar())) as archive:
        archive.extractall(folder, filter="data")
    return folder


def test_verify_copy_passes_a_whole_copy_and_names_a_changed_or_missing_file(tmp_path):
    copy = unpack_copy(tmp_path / "copy")
    seen = []
    assert sam3_files.verify_copy(copy, lambda done, total, message: seen.append((done, total))) is None
    assert seen[-1][0] == seen[-1][1]
    weights = copy / paths.SAM3_MODEL_FOLDER / "snapshots" / COMMIT / "model.safetensors"
    weights.write_bytes(flip_one_byte(WEIGHTS))
    with pytest.raises(TransferError, match="model.safetensors"):
        sam3_files.verify_copy(copy)
    weights.unlink()
    with pytest.raises(TransferError, match="model.safetensors is missing"):
        sam3_files.verify_copy(copy)


def test_verify_copy_refuses_a_bad_or_empty_file_list(tmp_path):
    copy = unpack_copy(tmp_path / "copy")
    listing_path = copy / "checksums.json"
    listing = json.loads(listing_path.read_text(encoding="utf-8"))
    listing["../../evil"] = {"size": 1, "sha256": "0" * 64}
    listing_path.write_text(json.dumps(listing), encoding="utf-8")
    with pytest.raises(TransferError, match="bad entry"):
        sam3_files.verify_copy(copy)
    listing_path.write_text("{}", encoding="utf-8")
    with pytest.raises(TransferError, match="empty"):
        sam3_files.verify_copy(copy)
    shutil.rmtree(copy)
    with pytest.raises(TransferError, match="cannot be read"):
        sam3_files.verify_copy(copy)
