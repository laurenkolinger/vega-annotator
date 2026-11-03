"""Tests for installing SAM3 from the server folder, and tools/publish_sam3.py."""

import json

import pytest

import helpers
from vega_annotator import paths, sam3_files
from vega_annotator.transfer import TransferError

tool = helpers.load_tool("publish_sam3")
COMMIT = "abc123"


def make_cache(root):
    """Write a small Hugging Face style cache, with its weights behind a symbolic link."""
    cache = root / "models--facebook--sam3"
    blobs = cache / "blobs"
    snapshot = cache / "snapshots" / COMMIT
    blobs.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    (cache / "refs").mkdir()
    (cache / "refs" / "main").write_text(COMMIT, encoding="utf-8")
    (blobs / "aaa").write_bytes(b"weights" * 1000)
    (snapshot / "model.safetensors").symlink_to("../../blobs/aaa")
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    return cache


@pytest.fixture
def server_with_sam3(server_folder, tmp_path):
    dest = server_folder / "sam3"
    dest.mkdir()
    (dest / tool.LICENSE_NAME).write_text("SAM License", encoding="utf-8")
    tool.publish(str(make_cache(tmp_path)), str(dest))
    return server_folder


def test_publish_writes_plain_files_in_the_cache_layout(server_with_sam3):
    model = server_with_sam3 / "sam3" / tool.MODEL_FOLDER
    weights = model / "snapshots" / COMMIT / "model.safetensors"
    assert weights.is_file() and not weights.is_symlink()
    assert (model / "refs" / "main").read_text(encoding="utf-8") == COMMIT
    listing = json.loads((server_with_sam3 / "sam3" / "checksums.json").read_text(encoding="utf-8"))
    assert tool.LICENSE_NAME in listing and f"{tool.MODEL_FOLDER}/refs/main" in listing


def test_publish_refuses_without_the_license(tmp_path):
    (tmp_path / "dest").mkdir()
    with pytest.raises(ValueError, match="license"):
        tool.publish(str(make_cache(tmp_path)), str(tmp_path / "dest"))


def test_publish_refuses_an_empty_cache(tmp_path):
    with pytest.raises(ValueError):
        tool.publish(str(tmp_path / "nothing"), str(tmp_path))


def test_status_before_and_after_install(server_with_sam3):
    assert sam3_files.status() == {"installed": False, "size_gb": 0}
    assert sam3_files.server_copy(str(server_with_sam3)) is not None
    result = sam3_files.install_from_server(str(server_with_sam3))
    assert result["files"] == 4
    assert sam3_files.status()["installed"] is True
    assert (paths.sam3_home() / sam3_files.LICENSE_NAME).read_text(encoding="utf-8") == "SAM License"
    assert (paths.sam3_model_dir() / "snapshots" / COMMIT / "model.safetensors").stat().st_size == 7000


def test_install_reports_progress(server_with_sam3):
    seen = []
    sam3_files.install_from_server(str(server_with_sam3), lambda done, total, message: seen.append((done, total)))
    assert seen[-1][0] == seen[-1][1]


def test_install_with_no_copy_on_the_server(server_folder):
    assert sam3_files.server_copy("") is None
    with pytest.raises(TransferError, match="no copy of SAM3"):
        sam3_files.install_from_server(str(server_folder))


def test_damaged_weights_install_nothing(server_with_sam3):
    weights = server_with_sam3 / "sam3" / tool.MODEL_FOLDER / "snapshots" / COMMIT / "model.safetensors"
    weights.write_bytes(b"short")
    with pytest.raises(TransferError, match="checksum"):
        sam3_files.install_from_server(str(server_with_sam3))
    assert sam3_files.status()["installed"] is False
    assert not list(paths.sam3_home().glob(".incoming_*"))


def test_hostile_file_list_is_refused(server_with_sam3):
    path = server_with_sam3 / "sam3" / "checksums.json"
    listing = json.loads(path.read_text(encoding="utf-8"))
    listing["../../evil"] = {"size": 1, "sha256": "0"}
    path.write_text(json.dumps(listing), encoding="utf-8")
    with pytest.raises(TransferError, match="bad entry"):
        sam3_files.install_from_server(str(server_with_sam3))


def test_installed_snapshot_refuses_a_bad_ref(tmp_path):
    model = tmp_path / "m"
    (model / "refs").mkdir(parents=True)
    (model / "refs" / "main").write_text("../../etc", encoding="utf-8")
    assert sam3_files.installed_snapshot(model) is None
