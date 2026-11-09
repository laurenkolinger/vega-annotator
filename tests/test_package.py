"""Tests for package.py: EXPORT FOR VEGA writes a folder shaped like a VEGA Annotate Images session."""

import csv
import hashlib
import json
import socket
import sys
from collections import namedtuple
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

import annotator_driver
import helpers
from vega_annotator import VERSION, folder_projects, package, projects, transfer
from vega_annotator.pathmap import SEGMENTATIONS_REL, STEP4_DIR

NOW = datetime(2026, 11, 9, 15, 30, tzinfo=projects.AST)
LABELS = ["healthy", "bleached", "diseased"]
README_SENTENCES = (
    "Copy this folder, unchanged, into inprocess/_standalone/ on vicar-mainframe.",
    "Train it in its own training project with TRAIN ON THIS.",
    "Never pick it as the source inside an existing project, which would overwrite that project's training split.",
)
FAKE = [str(Path(__file__).parent / "fake_annotator.py")]
Usage = namedtuple("Usage", "total used free")
ON_MAC = pytest.mark.skipif(sys.platform != "darwin", reason="sips converts images only on macOS")


def port_free(port):
    """Return True when nothing listens on a local port."""
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


@pytest.fixture
def project(tmp_path):
    """Return a folder project of three images with two of them exported, one with two masks."""
    photos = tmp_path / "photos"
    helpers.write_image(photos / "a.jpg", color=(10, 100, 10))
    helpers.write_image(photos / "sub" / "b.png", color=(20, 100, 10))
    helpers.write_image(photos / "c.jpg", color=(30, 100, 10))
    root = tmp_path / "projects"
    run = folder_projects.create(photos, "Reef health", LABELS, "LO", "health labels", root=root)["run"]
    helpers.export_like_vega(root / run, {"custom_a.jpg": ["bleached", "healthy"], "custom_b.jpg": []})
    return root / run


@pytest.fixture
def destination(tmp_path):
    """Return an empty folder standing in for a USB drive."""
    folder = tmp_path / "USB"
    folder.mkdir()
    return folder


def files_under(folder):
    return {path.relative_to(folder).as_posix(): path for path in folder.rglob("*") if path.is_file()}


def test_package_has_session_shape(project, destination):
    result = package.export_for_vega(project, destination, now=NOW)
    name = f"{project.name[4:]}_20261109-1530"
    folder = Path(result["folder"])
    assert folder == destination / name and sorted(path.name for path in destination.iterdir()) == [name]
    assert (result["frames"], result["masks"]) == (2, 2)
    files = files_under(folder)
    assert result["bytes"] == sum(path.stat().st_size for path in files.values())

    data = yaml.safe_load((folder / "data.yaml").read_text(encoding="utf-8"))
    assert data == {"path": f"{package.MAINFRAME_STANDALONE_ROOT}/{name}", "train": "all_images",
                    "val": "all_images", "nc": 3, "names": dict(enumerate(LABELS))}

    images = sorted(path.name for path in (folder / "all_images").iterdir())
    assert images == ["custom_a.jpg", "custom_b.jpg"]
    for image in images:
        copy = folder / "all_images" / image
        assert copy.is_file() and not copy.is_symlink()
        assert copy.read_bytes() == (project / STEP4_DIR / "custom_imports" / "raw" / image).read_bytes()
    assert sorted(path.stem for path in (folder / "all_labels").iterdir()) == [Path(image).stem for image in images]
    lines = (folder / "all_labels" / "custom_a.txt").read_text(encoding="utf-8").split("\n")
    assert [LABELS[int(line.split()[0])] for line in lines if line] == ["bleached", "healthy"]
    assert (folder / "all_labels" / "custom_b.txt").read_text(encoding="utf-8") == ""

    session = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    assert set(session) == {"session", "name", "created", "initials", "reviewer", "labels", "frames_exported",
                            "masks", "app_version", "computer"}
    assert session["session"] == name and session["name"] == "Reef health" and session["labels"] == LABELS
    assert session["created"] == "2026-11-09 15:30 AST" and session["initials"] == session["reviewer"] == "LO"
    assert (session["frames_exported"], session["masks"], session["app_version"]) == (2, 2, VERSION)
    assert session["computer"]

    checksums = json.loads((folder / "checksums.json").read_text(encoding="utf-8"))
    assert set(checksums) == set(files) - {"checksums.json"}
    for rel, entry in checksums.items():
        assert entry == {"size": files[rel].stat().st_size,
                         "sha256": hashlib.sha256(files[rel].read_bytes()).hexdigest()}
    readme = (folder / "README.txt").read_text(encoding="utf-8")
    assert all(sentence in readme for sentence in README_SENTENCES)
    assert "\u2014" not in readme

    expected = {"class_map.json", "export_manifest.csv", "label_provenance.csv", "export_log.txt",
                "mask_status_log.jsonl", "segmentations/segmentations.json", "previews/custom_a_seg.jpg",
                "segmentations/overlays_reviewed/custom_a_seg.jpg", "custom_imports/imports_manifest.json",
                "custom_imports/raw/custom_a.jpg", "custom_imports/raw/custom_c.jpg"}
    assert expected <= set(files)
    assert not any(rel.startswith(("custom_imports/originals/", "routed_input/", "vega_annotator_registry/"))
                   for rel in files)
    assert (project / STEP4_DIR / "custom_imports" / "vega_annotator_sources.json").is_file()
    assert "custom_imports/vega_annotator_sources.json" not in files
    assert json.loads((folder / "class_map.json").read_text(encoding="utf-8")) == {"healthy": 0, "bleached": 1,
                                                                                    "diseased": 2}
    with open(folder / "export_manifest.csv", newline="", encoding="utf-8") as handle:
        exported = [row["image_file"] for row in csv.DictReader(handle) if row["outcome"] in transfer.EXPORTED_OUTCOMES]
    assert sorted(exported) == images


def test_package_names_labels_added_after_the_last_export(project, destination):
    from vega_annotator import labels
    labels.add(project, ["dead"])
    folder = Path(package.export_for_vega(project, destination, now=NOW)["folder"])
    data = yaml.safe_load((folder / "data.yaml").read_text(encoding="utf-8"))
    assert data["nc"] == 4 and data["names"] == dict(enumerate(LABELS + ["dead"]))


def test_package_refuses_with_nothing_exported(tmp_path, destination):
    photos = tmp_path / "photos"
    helpers.write_image(photos / "a.jpg")
    root = tmp_path / "projects"
    project = root / folder_projects.create(photos, "Reef", LABELS, "LO", "x", root=root)["run"]
    with pytest.raises(package.PackageError, match="No frame is exported yet"):
        package.export_for_vega(project, destination, now=NOW)
    (project / STEP4_DIR / "export_manifest.csv").write_text(
        ",".join(helpers.MANIFEST_FIELDS) + "\ncustom_a,0,,,scrapped,,batch_001,\n", encoding="utf-8")
    with pytest.raises(package.PackageError, match="No frame is exported yet"):
        package.export_for_vega(project, destination, now=NOW)
    assert list(destination.iterdir()) == []


def test_package_excludes_originals_unless_asked(project, destination):
    plain = Path(package.export_for_vega(project, destination, now=NOW)["folder"])
    assert not (plain / "custom_imports" / "originals").exists()
    later = NOW + timedelta(minutes=1)
    full = Path(package.export_for_vega(project, destination, include_originals=True, now=later)["folder"])
    originals = sorted(path.relative_to(full).as_posix() for path in (full / "custom_imports" / "originals").iterdir())
    assert originals == ["custom_imports/originals/a.jpg", "custom_imports/originals/b.png",
                         "custom_imports/originals/c.jpg"]
    assert set(originals) <= set(json.loads((full / "checksums.json").read_text(encoding="utf-8")))


def test_package_name_rule(project, destination, tmp_path):
    name = package.package_name(project, now=NOW)
    assert name == f"{project.name[4:]}_20261109-1530" and package.NAME_RE.match(name)
    long_run = tmp_path / ("run_26-11-09_ABCD_" + "very_long_name_" * 5)
    long_name = package.package_name(long_run, now=NOW)
    assert len(long_name) == 64 and long_name.endswith("_20261109-1530") and package.NAME_RE.match(long_name)
    odd = package.package_name(tmp_path / "run_odd name..here", now=NOW)
    assert package.NAME_RE.match(odd) and ".." not in odd
    first = Path(package.export_for_vega(project, destination, now=NOW)["folder"])
    second = Path(package.export_for_vega(project, destination, now=NOW + timedelta(minutes=1))["folder"])
    third = Path(package.export_for_vega(project, destination, now=NOW + timedelta(minutes=1, seconds=20))["folder"])
    assert first.name.endswith("_20261109-1530") and second.name.endswith("_20261109-1531")
    assert third.name == second.name + "_2" and package.NAME_RE.match(third.name)
    assert len({first, second, third}) == 3 and all(folder.is_dir() for folder in (first, second, third))


def test_package_never_leaves_a_half_written_folder(project, destination, monkeypatch):
    copies = []
    real_copy = transfer.copy_verified

    def fails_on_the_third_file(src, dest, *args, **kwargs):
        copies.append(dest)
        if len(copies) == 3:
            raise OSError(28, "No space left on device")
        return real_copy(src, dest, *args, **kwargs)

    monkeypatch.setattr(package.transfer, "copy_verified", fails_on_the_third_file)
    with pytest.raises(package.PackageError) as stop:
        package.export_for_vega(project, destination, now=NOW)
    message = str(stop.value)
    assert "No space left on device" in message and "removed" in message
    assert len(copies) == 3 and ".partial" in str(copies[0])
    assert list(destination.iterdir()) == []


def test_package_refused_while_annotator_running(project, destination):
    with helpers.AnnotatorPage(project, projects.ANNOTATOR_PORTS):
        with pytest.raises(package.PackageError, match="Close the annotator"):
            package.export_for_vega(project, destination, now=NOW)
    assert list(destination.iterdir()) == []


def test_package_refuses_without_space(project, destination, monkeypatch):
    monkeypatch.setattr(package.shutil, "disk_usage", lambda _path: Usage(10**12, 10**12, 1_000_000))
    with pytest.raises(package.PackageError) as refusal:
        package.export_for_vega(project, destination, now=NOW)
    assert "free" in str(refusal.value) and "nothing was written" in str(refusal.value)
    assert list(destination.iterdir()) == []


def test_package_refuses_a_bad_destination_or_project(project, destination, source_project, tmp_path):
    with pytest.raises(package.PackageError, match="not a folder"):
        package.export_for_vega(project, tmp_path / "missing", now=NOW)
    with pytest.raises(package.PackageError, match="outside the project"):
        package.export_for_vega(project, project / STEP4_DIR, now=NOW)
    with pytest.raises(package.PackageError, match="made from a folder"):
        package.export_for_vega(source_project, destination, now=NOW)
    (project / STEP4_DIR / "custom_imports" / "raw" / "custom_b.jpg").unlink()
    with pytest.raises(package.PackageError, match="custom_b.jpg"):
        package.export_for_vega(project, destination, now=NOW)
    assert list(destination.iterdir()) == []


def test_package_refuses_a_label_line_with_an_unknown_class(project, destination):
    (project / STEP4_DIR / "all_labels" / "custom_a.txt").write_text("7 0.1 0.1 0.5 0.1 0.5 0.5\n", encoding="utf-8")
    with pytest.raises(package.PackageError, match="class 7"):
        package.export_for_vega(project, destination, now=NOW)
    assert list(destination.iterdir()) == []


def test_a_stale_partial_folder_is_replaced(project, destination):
    stale = destination / f".{package.package_name(project, now=NOW)}.partial"
    (stale / "all_images").mkdir(parents=True)
    (stale / "all_images" / "junk.jpg").write_bytes(b"junk")
    folder = Path(package.export_for_vega(project, destination, now=NOW)["folder"])
    assert not stale.exists() and not (folder / "all_images" / "junk.jpg").exists()


@ON_MAC
def test_package_from_a_real_annotator_session(tmp_path, destination, monkeypatch):
    """VEGA's own annotator labels a folder project and exports it; the package then agrees with itself."""
    photos = tmp_path / "photos"
    helpers.write_image(photos / "reef_a.jpg", size=(2400, 1350), color=(30, 90, 120))
    helpers.write_image(photos / "reef_b.png", size=(640, 360), color=(60, 90, 120))
    helpers.write_image(photos / "plates" / "reef_c.tif", size=(640, 360), color=(90, 90, 120))
    helpers.write_heic(photos / "plates" / "reef_d.heic", size=(640, 360), color=(120, 90, 120))
    names = ["healthy", "Partly bleached", "SCTLD (lesion)"]
    root = tmp_path / "projects"
    project = root / folder_projects.create(photos, "Reef health", names, "LO", "health labels", root=root)["run"]
    ports = iter(port for port in projects.ANNOTATOR_PORTS if port_free(port))
    monkeypatch.setattr(annotator_driver, "free_port", lambda: next(ports))
    annotator = annotator_driver.Annotator(project, FAKE)
    try:
        annotator.boot()
        offered = annotator.get("/api/status")["target_labels"]
        assert offered == [{"code": name, "name": name, "category": "Label"} for name in names]
        batch = annotator.batch_frames()
        assert sorted(batch) == ["custom_reef_a_lores.jpg", "custom_reef_b.jpg", "custom_reef_c_tif.jpg",
                                 "custom_reef_d_heic.jpg"]
        annotator.label_frame("custom_reef_a_lores.jpg", x=300, y=300, species="Partly bleached", category="Label")
        annotator.label_frame("custom_reef_b.jpg", x=100, y=100, species="healthy", category="Label")
        annotator.label_frame("custom_reef_b.jpg", x=450, y=250, species="SCTLD (lesion)", category="Label")
        stats = annotator.export_batch()
        assert stats["ok"] is True and stats["exported_images"] == 4 and stats["exported_masks"] == 3
        with pytest.raises(package.PackageError, match="Close the annotator"):
            package.export_for_vega(project, destination, now=NOW)
    finally:
        annotator.stop()
    result = package.export_for_vega(project, destination, now=NOW)
    folder = Path(result["folder"])
    assert (result["frames"], result["masks"]) == (4, 3)
    data = yaml.safe_load((folder / "data.yaml").read_text(encoding="utf-8"))
    assert data["nc"] == 3 and data["names"] == dict(enumerate(names))
    store = json.loads((project / SEGMENTATIONS_REL).read_text(encoding="utf-8"))
    images = sorted(path.name for path in (folder / "all_images").iterdir())
    assert images == sorted(batch)
    for image in images:
        assert not (folder / "all_images" / image).is_symlink()
        lines = [line for line in (folder / "all_labels" / f"{Path(image).stem}.txt").read_text(
            encoding="utf-8").splitlines() if line]
        accepted = [mask["species"] for mask in store[image]["masks"] if mask["status"] == "accepted"]
        assert sorted(data["names"][int(line.split()[0])] for line in lines) == sorted(accepted)
    checksums = json.loads((folder / "checksums.json").read_text(encoding="utf-8"))
    for rel, entry in checksums.items():
        assert hashlib.sha256((folder / rel).read_bytes()).hexdigest() == entry["sha256"]
