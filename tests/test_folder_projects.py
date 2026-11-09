"""Tests for folder_projects.py: projects started from any folder of images."""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from collections import namedtuple
from datetime import date
from pathlib import Path

import pytest
from PIL import Image, ImageOps

import helpers
from vega_annotator import VERSION, folder_projects, labels, projects, transfer
from vega_annotator.device import DevicePlan
from vega_annotator.pathmap import SEGMENTATIONS_REL, STEP4_DIR

LABELS = "healthy, bleached\ndiseased"
TODAY = date(2026, 11, 9)
Usage = namedtuple("Usage", "total used free")
ON_MAC = pytest.mark.skipif(sys.platform != "darwin", reason="sips converts images only on macOS")
PLAN = DevicePlan("cpu:0", "cpu:0", "float32", 4, False, "label")


@pytest.fixture
def root(tmp_path):
    """Return an empty projects folder."""
    folder = tmp_path / "projects"
    folder.mkdir()
    return folder


def store_of(project):
    return json.loads((project / SEGMENTATIONS_REL).read_text(encoding="utf-8"))


def sources_of(project):
    path = project / STEP4_DIR / "custom_imports" / "vega_annotator_sources.json"
    return json.loads(path.read_text(encoding="utf-8"))


def sha_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def imports_of(project):
    path = project / STEP4_DIR / "custom_imports" / "imports_manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))["imports"]


def make(folder, root, name="Reef health", label_text=LABELS, progress=None):
    result = folder_projects.create(folder, name, label_text, "lo", "health labels", root=root, progress=progress)
    return result, root / result["run"]


def test_scan_finds_images_in_subfolders_and_skips_hidden_and_links(tmp_path):
    folder = tmp_path / "photos"
    found = [helpers.write_image(folder / "a.jpg"), helpers.write_image(folder / "sub" / "b.png"),
             helpers.write_image(folder / "sub" / "deeper" / "c.JPEG")]
    tiff = helpers.write_image(folder / "d.tif")
    helpers.write_image(folder / ".hidden.jpg")
    helpers.write_image(folder / ".thumbs" / "e.jpg")
    (folder / "notes.txt").write_text("field notes", encoding="utf-8")
    os.symlink(folder / "a.jpg", folder / "link.jpg")
    os.symlink(folder / "sub", folder / "linkdir")
    os.symlink(folder, folder / "sub" / "loop")
    result = folder_projects.scan(folder)
    assert result["images"] == found and result["convert"] == [tiff]
    assert result["bytes"] == sum(path.stat().st_size for path in found + [tiff])
    reasons = {Path(item["path"]).relative_to(folder).as_posix(): item["reason"] for item in result["skipped"]}
    assert reasons == {".hidden.jpg": "hidden file", ".thumbs": "hidden folder",
                       "link.jpg": "link, not followed", "linkdir": "link, not followed",
                       "sub/loop": "link, not followed", "notes.txt": "not an image type the app takes"}


def test_scan_lists_unreadable_and_zero_byte_files_as_skipped(tmp_path):
    folder = tmp_path / "photos"
    helpers.write_image(folder / "good.jpg")
    (folder / "empty.jpg").write_bytes(b"")
    locked = helpers.write_image(folder / "locked.jpg")
    locked_folder = helpers.write_image(folder / "locked_folder" / "inside.jpg").parent
    os.mkfifo(folder / "pipe.jpg")
    locked.chmod(0)
    locked_folder.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("This account reads files whatever their permissions.")
        result = folder_projects.scan(folder)
    finally:
        locked.chmod(stat.S_IRUSR | stat.S_IWUSR)
        locked_folder.chmod(stat.S_IRWXU)
    assert result["images"] == [folder / "good.jpg"]
    reasons = {Path(item["path"]).name: item["reason"] for item in result["skipped"]}
    assert reasons["empty.jpg"] == "empty file"
    assert reasons["pipe.jpg"] == "not a regular file"
    assert reasons["locked.jpg"].startswith("cannot be opened")
    assert reasons["locked_folder"].startswith("folder cannot be opened")


def test_scan_refuses_what_is_not_a_folder_and_handles_thousands_of_files(tmp_path):
    with pytest.raises(folder_projects.FolderError, match="not a folder"):
        folder_projects.scan(tmp_path / "missing")
    with pytest.raises(folder_projects.FolderError, match="not a folder"):
        folder_projects.scan(helpers.write_image(tmp_path / "one.jpg"))
    empty = tmp_path / "empty"
    empty.mkdir()
    assert folder_projects.scan(empty) == {"images": [], "convert": [], "skipped": [], "bytes": 0}
    many = tmp_path / "many"
    for index in range(3000):
        path = many / f"site_{index % 30:02d}" / f"IMG_{index:04d}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xd8\xff" + bytes(index % 7))
    result = folder_projects.scan(many)
    assert len(result["images"]) == 3000 and result["skipped"] == []
    assert result["images"] == sorted(result["images"])


def test_new_project_id_slug_and_clash_suffix(root):
    make_id = folder_projects.new_project_id
    assert make_id("Coral Health: St. Thomas 2026!", "lo", root, today=TODAY) == \
        "run_26-11-09_LO_coral_health_st_thomas_2026"
    assert make_id("R\u00e9cif  sant\u00e9", "AT", root, today=TODAY) == "run_26-11-09_AT_recif_sante"
    assert make_id("\u5065\u5eb7", "AT", root, today=TODAY) == "run_26-11-09_AT_project"
    long_id = make_id("word " * 15, "ABCD", root, today=TODAY)
    assert len(long_id) == 61 and folder_projects.ID_RE.match(long_id) and not long_id.endswith("_")
    (root / "run_26-11-09_LO_reef").mkdir()
    assert make_id("Reef", "LO", root, today=TODAY) == "run_26-11-09_LO_reef_2"
    for suffix in range(2, 100):
        (root / f"run_26-11-09_LO_reef_{suffix}").mkdir()
    with pytest.raises(folder_projects.FolderError, match="Choose another name"):
        make_id("Reef", "LO", root, today=TODAY)
    with pytest.raises(folder_projects.FolderError, match="Initials"):
        make_id("Reef", "L0", root, today=TODAY)
    for blank in ("", "   ", None):
        with pytest.raises(folder_projects.FolderError, match="name"):
            make_id(blank, "LO", root, today=TODAY)


def test_create_writes_project_skeleton(tmp_path, root):
    folder = tmp_path / "photos"
    helpers.write_image(folder / "a.jpg")
    helpers.write_image(folder / "b.png")
    result, project = make(folder, root)
    assert result["run"].startswith("run_") and result["run"].endswith("_LO_reef_health")
    assert result["added"] == 2 and result["skipped"] == []
    record = projects.read_project(project)
    assert record["id"] == result["run"][4:] and record["name"] == "Reef health"
    assert record["description"] == "" and record["initials"] == "LO"
    assert record["created_at"].endswith("-04:00") and record["updated_at"]
    assert record["access_log"][0] == {"ts": record["created_at"], "initials": "LO", "action": "create",
                                       "purpose": "health labels"}
    assert {key: step["status"] for key, step in record["steps"].items()} == {"1": "skipped", "2": "skipped",
                                                                              "3": "skipped"}
    assert record["steps"]["3"]["config"]["target_species"] == "healthy, bleached, diseased"
    assert record["label_set"] == {"kind": "custom", "labels": ["healthy", "bleached", "diseased"]}
    origin = record["origin"]
    assert origin["kind"] == "folder" and origin["folder"] == str(folder.resolve())
    assert origin["computer"] and origin["made_with"] == f"VEGA Annotator {VERSION}"
    step4 = project / STEP4_DIR
    assert json.loads((step4 / "routed_input" / "ids" / "sam_click_prompts.json").read_text(encoding="utf-8")) == {}
    assert (step4 / "routed_input" / "raw").is_dir() and not any((step4 / "routed_input" / "raw").iterdir())
    assert labels.current(project) == ["healthy", "bleached", "diseased"]
    assert json.loads((step4 / "class_map.json").read_text(encoding="utf-8")) == \
        {"healthy": 0, "bleached": 1, "diseased": 2}
    assert (step4 / "master_codes_recoded.csv").is_file()
    local = json.loads((project / transfer.LOCAL_STATE).read_text(encoding="utf-8"))
    assert local == {"kind": "made_here", "created_at": record["created_at"], "computer": origin["computer"]}
    assert folder_projects.made_here(project) == local
    assert folder_projects.is_folder_project(record) and not folder_projects.is_folder_project({"steps": {}})
    assert sorted(path.name for path in root.iterdir()) == [result["run"]]


def test_create_imports_through_vega_custom_imports(tmp_path, root):
    folder = tmp_path / "photos"
    helpers.write_image(folder / "a.jpg")
    helpers.write_image(folder / "plates" / "b.png")
    helpers.write_image(folder / "big.jpg", size=(2400, 1200))
    _result, project = make(folder, root)
    step4 = project / STEP4_DIR
    store = store_of(project)
    assert sorted(store) == ["custom_a.jpg", "custom_b.jpg", "custom_big_lores.jpg"]
    for name, frame in store.items():
        assert frame["custom_import"] is True and frame["reference_points"] == [] and frame["masks"] == []
        assert frame["image_path"] == f"custom_imports/raw/{name}" and frame["exported"] is False
        assert Path(frame["image_path_abs"]) == step4 / "custom_imports" / "raw" / name
        assert (step4 / "custom_imports" / "raw" / name).is_file()
    with Image.open(step4 / "custom_imports" / "raw" / "custom_big_lores.jpg") as working:
        assert working.size == (1920, 960)
    assert store["custom_big_lores.jpg"]["import_scale"] == pytest.approx(0.8)
    assert sorted(path.name for path in (step4 / "custom_imports" / "originals").iterdir()) == \
        ["a.jpg", "b.png", "big.jpg"]
    assert {entry["original_name"] for entry in imports_of(project)} == {"a.jpg", "plates/b.png", "big.jpg"}
    # The store is written the way VEGA writes it: indent 1, group readable.
    raw = (project / SEGMENTATIONS_REL).read_text(encoding="utf-8")
    assert raw == json.dumps(store, indent=1)
    assert stat.S_IMODE((project / SEGMENTATIONS_REL).stat().st_mode) == 0o664
    record = projects.read_project(project)
    assert sorted(item["filename"] for item in record["custom_imports"]) == sorted(store)
    assert record["access_log"][-1]["action"] == "import_images" and record["access_log"][-1]["initials"] == "LO"
    work = projects.count_work(project)
    assert work["frames_total"] == 3 and work["frames_left"] == 3


def test_scan_same_name_in_two_subfolders_gives_two_frames(tmp_path, root):
    folder = tmp_path / "photos"
    helpers.write_image(folder / "healthy" / "IMG_1.jpg", color=(10, 200, 10))
    helpers.write_image(folder / "bleached" / "IMG_1.jpg", color=(250, 250, 250))
    result, project = make(folder, root)
    assert result["added"] == 2
    store = store_of(project)
    assert sorted(store) == ["custom_IMG_1.jpg", "custom_IMG_1_2.jpg"]
    names = {entry["original_name"]: entry["filename"] for entry in imports_of(project)}
    assert sorted(names) == ["bleached/IMG_1.jpg", "healthy/IMG_1.jpg"]
    raw = project / STEP4_DIR / "custom_imports" / "raw"
    with Image.open(raw / names["healthy/IMG_1.jpg"]) as healthy, Image.open(raw / names["bleached/IMG_1.jpg"]) as bleached:
        assert healthy.getpixel((5, 5)) != bleached.getpixel((5, 5))
    # The same photo name in a later ADD IMAGES takes another frame too, never the first one's.
    later = helpers.write_image(tmp_path / "later" / "IMG_1.jpg", color=(90, 10, 10)).parent
    assert folder_projects.add_images(project, later, "LO")["added"] == 1
    assert sorted(store_of(project)) == ["custom_IMG_1.jpg", "custom_IMG_1_2.jpg", "custom_IMG_1_3.jpg"]


@ON_MAC
def test_create_converts_heic_and_tiff_with_sips(tmp_path, root):
    folder = tmp_path / "photos"
    helpers.write_image(folder / "x.tif", color=(0, 90, 200))
    heic = helpers.write_heic(folder / "phone" / "y.heic")
    helpers.write_image(folder / "z.jpg")
    helpers.write_image(folder / "w.bmp")
    helpers.write_image(folder / "v.webp", color=(90, 10, 200))
    result, project = make(folder, root)
    assert result["added"] == 5 and result["skipped"] == []
    assert sorted(store_of(project)) == ["custom_v_webp.jpg", "custom_w_bmp.jpg", "custom_x_tif.jpg",
                                         "custom_y_heic.jpg", "custom_z.jpg"]
    originals = project / STEP4_DIR / "custom_imports" / "originals"
    assert sorted(path.name for path in originals.iterdir()) == ["v_webp.jpg", "w_bmp.png", "x_tif.png",
                                                                 "y_heic.jpg", "z.jpg"]
    # A lossy photo is kept as a JPEG, so an iPhone picture never becomes a large PNG.
    for name, kind in (("y_heic.jpg", "JPEG"), ("v_webp.jpg", "JPEG"), ("x_tif.png", "PNG"), ("w_bmp.png", "PNG")):
        with Image.open(originals / name) as converted:
            assert converted.format == kind and converted.size == (64, 48)
    assert {entry["original_name"] for entry in imports_of(project)} >= {"phone/y_heic.jpg", "x_tif.png"}
    # The ledger keys each frame by its source file, the HEIC itself, not its converted copy.
    assert sources_of(project)[sha_of(heic)] == ["custom_y_heic.jpg"]


def orientation_six_jpeg(path):
    """Write a 400 by 200 JPEG with a red left edge, tagged to show turned upright (EXIF orientation 6)."""
    image = Image.new("RGB", (400, 200), (40, 120, 160))
    image.paste((255, 0, 0), (0, 0, 20, 200))
    exif = Image.Exif()
    exif[0x0112] = 6
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, quality=95, exif=exif.tobytes())
    return path


@ON_MAC
@pytest.mark.parametrize("kind", ["heic", "jpg"])
def test_a_rotated_photo_stays_upright(tmp_path, root, kind):
    folder = tmp_path / "photos"
    photo = orientation_six_jpeg(folder / "turned.jpg")
    if kind == "heic":
        subprocess.run(["sips", "-s", "format", "heic", str(photo), "--out", str(folder / "turned.heic")],
                       capture_output=True, check=True)
        photo.unlink()
    _result, project = make(folder, root)
    (frame,) = store_of(project)
    with Image.open(project / STEP4_DIR / "custom_imports" / "raw" / frame) as working:
        assert working.size == (200, 400)
        # Orientation 6 shows the stored left edge along the top, so the red edge is on top.
        assert working.getpixel((100, 5))[0] > 200 and working.getpixel((100, 395))[0] < 100
        assert ImageOps.exif_transpose(working).size == (200, 400)


def test_create_lists_a_file_that_does_not_open_and_one_that_cannot_convert(tmp_path, root):
    folder = tmp_path / "photos"
    helpers.write_image(folder / "good.jpg")
    (folder / "broken.jpg").write_bytes(b"not a picture at all")
    (folder / "broken.tif").write_bytes(b"not a tiff either")
    result, project = make(folder, root)
    assert result["added"] == 1 and sorted(store_of(project)) == ["custom_good.jpg"]
    reasons = {Path(item["path"]).name: item["reason"] for item in result["skipped"]}
    # Plain reasons, with no Python text or internal paths.
    assert reasons["broken.jpg"] == "does not open as an image"
    assert reasons["broken.tif"] == "could not be converted"
    assert all(isinstance(item["path"], Path) and item["path"].is_absolute() for item in result["skipped"])


def test_create_refuses_bad_input_and_makes_nothing(tmp_path, root):
    folder = tmp_path / "photos"
    helpers.write_image(folder / "a.jpg")
    with pytest.raises(labels.LabelError, match="keeps for itself"):
        folder_projects.create(folder, "Reef", "healthy, review", "LO", "x", root=root)
    with pytest.raises(folder_projects.FolderError, match="Initials"):
        folder_projects.create(folder, "Reef", LABELS, "", "x", root=root)
    with pytest.raises(folder_projects.FolderError, match="purpose"):
        folder_projects.create(folder, "Reef", LABELS, "LO", "  ", root=root)
    with pytest.raises(folder_projects.FolderError, match="80 characters"):
        folder_projects.create(folder, "x" * 81, LABELS, "LO", "x", root=root)
    nothing = tmp_path / "nothing"
    (nothing / "sub").mkdir(parents=True)
    (nothing / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(folder_projects.FolderError, match="holds no images"):
        folder_projects.create(nothing, "Reef", LABELS, "LO", "x", root=root)
    assert list(root.iterdir()) == []


def test_create_refuses_without_space(tmp_path, root, monkeypatch):
    folder = tmp_path / "photos"
    image = helpers.write_image(folder / "a.jpg")
    needed = image.stat().st_size * folder_projects.SPACE_FACTOR + folder_projects.SPACE_MARGIN_BYTES
    monkeypatch.setattr(folder_projects.shutil, "disk_usage", lambda _path: Usage(10**12, 10**12, needed - 1))
    with pytest.raises(folder_projects.FolderError) as refusal:
        folder_projects.create(folder, "Reef", LABELS, "LO", "x", root=root)
    assert "GB free" in str(refusal.value) and "Free some space" in str(refusal.value)
    assert list(root.iterdir()) == []


def test_create_leaves_nothing_when_the_skeleton_cannot_be_written(tmp_path, root, monkeypatch):
    folder = tmp_path / "photos"
    helpers.write_image(folder / "a.jpg")

    def full_disk(_project_dir, _names):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(folder_projects.labels, "write", full_disk)
    with pytest.raises(folder_projects.FolderError, match="No space left on device"):
        folder_projects.create(folder, "Reef", LABELS, "LO", "x", root=root)
    assert list(root.iterdir()) == []


def test_add_images_refused_while_annotator_running(tmp_path, root, monkeypatch):
    folder = tmp_path / "photos"
    helpers.write_image(folder / "a.jpg")
    _result, project = make(folder, root)
    more = helpers.write_image(tmp_path / "more" / "b.jpg", color=(200, 30, 30)).parent
    with helpers.AnnotatorPage(project, projects.ANNOTATOR_PORTS):
        with pytest.raises(folder_projects.FolderError, match="Close the annotator"):
            folder_projects.add_images(project, more, "LO")
    assert sorted(store_of(project)) == ["custom_a.jpg"]
    assert folder_projects.add_images(project, more, "LO")["added"] == 1


def test_add_images_refused_on_a_vega_project(source_project, tmp_path):
    more = helpers.write_image(tmp_path / "more" / "b.jpg").parent
    with pytest.raises(folder_projects.FolderError, match="made from a folder"):
        folder_projects.add_images(source_project, more, "AT")


def test_add_images_folder_vanishes_midway(tmp_path, root):
    folder = tmp_path / "photos"
    helpers.write_image(folder / "a.jpg")
    _result, project = make(folder, root)
    drive = tmp_path / "Volumes" / "FIELD_USB" / "dive_3"
    for index in range(6):
        helpers.write_image(drive / f"IMG_{index}.jpg", color=(index * 30, 80, 80))
    backup = tmp_path / "backup_of_dive_3"
    shutil.copytree(drive, backup)
    seen = []

    def unplug_after_first(done, total, _message):
        seen.append((done, total))
        if len(seen) == 1:
            shutil.rmtree(drive.parent)

    with pytest.raises(folder_projects.FolderError) as stop:
        folder_projects.add_images(project, drive, "LO", progress=unplug_after_first)
    message = str(stop.value)
    assert "went away" in message and "1 of 6" in message and "ADD IMAGES" in message
    assert sources_of(project)[sha_of(backup / "IMG_0.jpg")] == ["custom_IMG_0.jpg"]
    # The project still opens, with the frame already imported kept and recorded.
    store = store_of(project)
    assert sorted(store) == ["custom_IMG_0.jpg", "custom_a.jpg"]
    assert (project / STEP4_DIR / "custom_imports" / "raw" / "custom_IMG_0.jpg").is_file()
    record = projects.read_project(project)
    assert "custom_IMG_0.jpg" in [item["filename"] for item in record["custom_imports"]]
    assert projects.count_work(project)["frames_total"] == 2
    env = projects.annotator_environment(project, PLAN, "http://127.0.0.1:5050", {"hf_home": "/h", "cache_dir": "/c"})
    assert env["TCRMP_TARGET_SPECIES"] == "healthy,bleached,diseased"


def test_add_images_stops_when_the_disk_fills(tmp_path, root, monkeypatch):
    folder = tmp_path / "photos"
    helpers.write_image(folder / "a.jpg")
    _result, project = make(folder, root)
    more = tmp_path / "more"
    for index in range(4):
        helpers.write_image(more / f"m{index}.jpg", color=(index * 50, 200, 30))
    space = {"free": 10**12}
    monkeypatch.setattr(folder_projects.shutil, "disk_usage", lambda _path: Usage(10**12, 0, space["free"]))

    def fill_after_first(done, _total, _message):
        if done == 1:
            space["free"] = folder_projects.LOW_SPACE_BYTES - 1

    with pytest.raises(folder_projects.FolderError) as stop:
        folder_projects.add_images(project, more, "LO", progress=fill_after_first)
    assert "almost full" in str(stop.value) and "1 of 4" in str(stop.value)
    assert sorted(store_of(project)) == ["custom_a.jpg", "custom_m0.jpg"]


def test_progress_counts_every_image(tmp_path, root):
    folder = tmp_path / "photos"
    for index in range(5):
        helpers.write_image(folder / f"p{index}.png", color=(index * 40, 30, 90))
    seen = []
    result, _project = make(folder, root, progress=lambda done, total, message: seen.append((done, total, message)))
    assert result["added"] == 5
    assert [item[:2] for item in seen] == [(index, 5) for index in range(1, 6)]
    assert all(item[2] for item in seen)


def test_add_images_after_a_cut_import_adds_only_the_rest(tmp_path, root):
    folder = tmp_path / "photos"
    helpers.write_image(folder / "a.jpg")
    _result, project = make(folder, root)
    drive = tmp_path / "Volumes" / "FIELD_USB" / "dive_3"
    for index in range(6):
        helpers.write_image(drive / f"IMG_{index}.jpg", color=(index * 30, 80, 80))
    backup = tmp_path / "backup_of_dive_3"
    shutil.copytree(drive, backup)

    def unplug_after_two(done, _total, _message):
        if done == 2:
            shutil.rmtree(drive.parent)

    with pytest.raises(folder_projects.FolderError, match="2 of 6"):
        folder_projects.add_images(project, drive, "LO", progress=unplug_after_two)
    shutil.copytree(backup, drive)
    result = folder_projects.add_images(project, drive, "LO")
    assert result["added"] == 4
    assert sorted(item["path"].name for item in result["skipped"]) == ["IMG_0.jpg", "IMG_1.jpg"]
    assert {item["reason"] for item in result["skipped"]} == {"already in the project"}
    assert sorted(store_of(project)) == ["custom_IMG_0.jpg", "custom_IMG_1.jpg", "custom_IMG_2.jpg",
                                         "custom_IMG_3.jpg", "custom_IMG_4.jpg", "custom_IMG_5.jpg", "custom_a.jpg"]
    assert len(sources_of(project)) == 7 and len(imports_of(project)) == 7


def test_add_images_twice_adds_nothing_new(tmp_path, root):
    folder = tmp_path / "photos"
    for index in range(3):
        helpers.write_image(folder / f"p{index}.jpg", color=(index * 60, 10, 10))
    _result, project = make(folder, root)
    store_before = (project / SEGMENTATIONS_REL).read_bytes()
    again = folder_projects.add_images(project, folder, "LO")
    assert again["added"] == 0
    assert [(item["path"], item["reason"]) for item in again["skipped"]] == \
        [(folder / f"p{index}.jpg", "already in the project") for index in range(3)]
    assert (project / SEGMENTATIONS_REL).read_bytes() == store_before
    # A copy of one photo under another name, beside a new photo, brings in only the new one.
    other = tmp_path / "other"
    other.mkdir()
    shutil.copyfile(folder / "p1.jpg", other / "renamed_copy.jpg")
    helpers.write_image(other / "new.jpg", color=(1, 2, 3))
    result = folder_projects.add_images(project, other, "LO")
    assert result["added"] == 1 and result["skipped"] == [{"path": other / "renamed_copy.jpg",
                                                           "reason": "already in the project"}]
    assert sorted(store_of(project)) == ["custom_new.jpg", "custom_p0.jpg", "custom_p1.jpg", "custom_p2.jpg"]
    assert sources_of(project) == {sha_of(path): [f"custom_{path.stem}.jpg"]
                                   for path in [folder / "p0.jpg", folder / "p1.jpg", folder / "p2.jpg",
                                                other / "new.jpg"]}


@pytest.mark.parametrize("steps", [folder_projects.PROGRESS_STEPS, 1])
def test_identical_files_in_one_folder_make_one_frame(tmp_path, root, monkeypatch, steps):
    # With 1 step every image shares one chunk; with the usual steps each image is its own chunk.
    monkeypatch.setattr(folder_projects, "PROGRESS_STEPS", steps)
    folder = tmp_path / "photos"
    first = helpers.write_image(folder / "a.jpg", color=(70, 70, 70))
    (folder / "copies").mkdir()
    shutil.copyfile(first, folder / "copies" / "a_again.jpg")
    shutil.copyfile(first, folder / "copies" / "a_third.jpg")
    helpers.write_image(folder / "b.jpg", color=(10, 200, 10))
    (folder / "broken.jpg").write_bytes(b"not a picture")
    (folder / "copies" / "broken_too.jpg").write_bytes(b"not a picture")
    result, project = make(folder, root)
    assert result["added"] == 2 and sorted(store_of(project)) == ["custom_a.jpg", "custom_b.jpg"]
    reasons = {item["path"].relative_to(folder).as_posix(): item["reason"] for item in result["skipped"]}
    assert reasons["copies/a_again.jpg"] == reasons["copies/a_third.jpg"] == "already in the project"
    # A copy of an image that did not come in is tried, and fails for its own reason.
    assert reasons["broken.jpg"] == reasons["copies/broken_too.jpg"] == "does not open as an image"
    assert sources_of(project) == {sha_of(first): ["custom_a.jpg"], sha_of(folder / "b.jpg"): ["custom_b.jpg"]}


@ON_MAC
def test_free_space_is_rechecked_with_the_converted_sizes(tmp_path, root, monkeypatch):
    folder = tmp_path / "photos"
    helpers.write_image(folder / "a.jpg")
    _result, project = make(folder, root)
    phone = tmp_path / "phone"
    heic = helpers.write_heic(phone / "IMG_7.heic")
    monkeypatch.setattr(folder_projects, "SPACE_MARGIN_BYTES", 0)
    monkeypatch.setattr(folder_projects, "LOW_SPACE_BYTES", 0)
    # Room for the HEIC twice over, which the first estimate accepts; its JPEG is larger.
    free = heic.stat().st_size * folder_projects.SPACE_FACTOR
    monkeypatch.setattr(folder_projects.shutil, "disk_usage", lambda _path: Usage(10**12, 10**12, free))
    with pytest.raises(folder_projects.FolderError, match="almost full"):
        folder_projects.add_images(project, phone, "LO")
    assert sorted(store_of(project)) == ["custom_a.jpg"]
    assert not (project / STEP4_DIR / "custom_imports" / "raw" / "custom_IMG_7_heic.jpg").exists()


def write_package(folder):
    """Write a folder shaped like an EXPORT FOR VEGA package, with its working images and previews."""
    helpers.write_image(folder / "all_images" / "custom_IMG_1.jpg")
    helpers.write_image(folder / "previews" / "custom_IMG_1_seg.jpg", color=(200, 0, 0))
    helpers.write_image(folder / "custom_imports" / "raw" / "custom_IMG_1.jpg")
    (folder / "session.json").write_text("{}", encoding="utf-8")
    (folder / "data.yaml").write_text("names:\n  0: healthy\n", encoding="utf-8")
    return folder


def test_scan_skips_vega_packages_and_projects(tmp_path):
    """A package or a project inside the picked folder is skipped once, whole; picked itself, it is refused."""
    folder = tmp_path / "photos"
    photo = helpers.write_image(folder / "IMG_1.jpg")
    write_package(folder / "26-11-09_LO_reef_20261109-1530")
    project = folder / "older" / "run_26-11-02_AT"
    helpers.write_image(project / STEP4_DIR / "custom_imports" / "raw" / "custom_x.jpg")
    (project / "project.json").write_text("{}", encoding="utf-8")
    # A folder with only one of a package's two files is an ordinary folder of photos.
    yolo = helpers.write_image(folder / "yolo" / "a.jpg")
    (folder / "yolo" / "data.yaml").write_text("names: {}\n", encoding="utf-8")
    result = folder_projects.scan(folder)
    assert result["images"] == [photo, yolo] and result["convert"] == []
    assert {Path(item["path"]).relative_to(folder).as_posix(): item["reason"] for item in result["skipped"]} == {
        "26-11-09_LO_reef_20261109-1530": "a VEGA Annotator package", "older/run_26-11-02_AT": "a VEGA project",
        "yolo/data.yaml": "not an image type the app takes"}
    # A folder that is itself a package or a project is skipped whole, once, and refused with a sentence.
    for inner, kind in ((folder / "26-11-09_LO_reef_20261109-1530", "a VEGA Annotator package"),
                        (project, "a VEGA project")):
        found = folder_projects.scan(inner)
        assert found == {"images": [], "convert": [], "skipped": [{"path": inner, "reason": kind}], "bytes": 0}
        with pytest.raises(folder_projects.FolderError, match=f"is {kind}"):
            folder_projects.create(inner, "Reef", LABELS, "LO", "x", root=tmp_path / "projects")


def test_scan_keeps_photos_beside_another_tools_project_json(tmp_path):
    """A project.json from another labeling tool, with no VEGA annotation folder beside it, hides no photos."""
    folder = tmp_path / "photos"
    photos = [helpers.write_image(folder / "cvat_export" / f"img_{index}.jpg") for index in range(4)]
    (folder / "cvat_export" / "project.json").write_text('{"tool": "cvat"}', encoding="utf-8")
    result = folder_projects.scan(folder)
    assert result["images"] == photos
    assert all(item["reason"] != folder_projects.PROJECT_REASON for item in result["skipped"])
    picked = folder_projects.scan(folder / "cvat_export")
    assert picked["images"] == photos and picked["skipped"] == [
        {"path": folder / "cvat_export" / "project.json", "reason": "not an image type the app takes"}]


def test_a_package_exported_into_the_photo_folder_is_not_imported_again(tmp_path, root):
    """EXPORT FOR VEGA into the photo folder, then ADD IMAGES there, brings in none of the package's images."""
    from vega_annotator import package
    folder = tmp_path / "Reef photos"
    for index in range(3):
        helpers.write_image(folder / f"IMG_{index}.png", size=(320, 240), color=(index * 70, 90, 90))
    _result, project = make(folder, root, label_text="healthy, bleached")
    helpers.export_like_vega(project, {name: ["healthy"] for name in sorted(store_of(project))[:2]})
    written = Path(package.export_for_vega(project, folder)["folder"])
    result = folder_projects.add_images(project, folder, "LO")
    assert result["added"] == 0 and len(store_of(project)) == 3
    reasons = {Path(item["path"]).name: item["reason"] for item in result["skipped"]}
    assert reasons[written.name] == "a VEGA Annotator package"
    assert not [item for item in result["skipped"] if written in Path(item["path"]).parents]


def import_and_export(folder, root, tmp_path):
    """Make a project from folder, export every frame as VEGA does, write its package, and return
    (the result, the project, the package's folder)."""
    from vega_annotator import package
    result, project = make(folder, root, label_text="healthy")
    helpers.export_like_vega(project, {name: ["healthy"] for name in store_of(project)})
    usb = tmp_path / "usb"
    usb.mkdir(exist_ok=True)
    return result, project, Path(package.export_for_vega(project, usb)["folder"])


# 251 characters and ".jpg" make 255 bytes, the longest file name macOS holds.
@pytest.mark.parametrize("length", [100, 101, 222, 223, 244, 245, 251])
def test_a_long_photo_name_imports_and_exports(tmp_path, root, length):
    """A photo name up to macOS's longest comes in under a short name and goes out in the package."""
    folder = tmp_path / "photos"
    photo = helpers.write_image(folder / "deep" / ("x" * length + ".jpg"), size=(64, 48))
    result, project, written = import_and_export(folder, root, tmp_path)
    assert result["added"] == 1 and result["skipped"] == []
    (frame,) = store_of(project)
    stem = "x" * length if length <= 100 else f"{'x' * 90}_{sha_of(photo)[:8]}"
    assert frame == f"custom_{stem}.jpg"
    assert imports_of(project)[0]["original_name"] == f"deep/{stem}.jpg"
    assert (project / STEP4_DIR / "custom_imports" / "originals" / f"{stem}.jpg").read_bytes() == photo.read_bytes()
    assert (written / "all_images" / frame).is_file() and (written / "previews" / f"custom_{stem}_seg.jpg").is_file()
    assert not list(tmp_path.joinpath("usb").glob(".*"))


@ON_MAC
def test_a_long_name_is_shortened_by_bytes_so_any_alphabet_fits(tmp_path, root):
    """A long name in an alphabet of several bytes a letter, and a long TIFF name, come in short too."""
    # 80 Thai letters are 240 bytes: a name VEGA's preview would push past macOS's 255-byte limit.
    folder = tmp_path / "photos"
    photo = helpers.write_image(folder / ("ก" * 80 + ".png"))
    heic_like = helpers.write_image(folder / ("y" * 240 + ".tif"), color=(0, 90, 200))
    result, project, written = import_and_export(folder, root, tmp_path)
    assert result["added"] == 2 and result["skipped"] == []
    frames = sorted(store_of(project))
    assert frames == sorted([f"custom_{'ก' * 30}_{sha_of(photo)[:8]}.jpg",
                             f"custom_{'y' * 90}_{sha_of(heic_like)[:8]}_tif.jpg"])
    assert all(len(name.encode("utf-8")) < 120 for name in frames)
    assert sorted(path.name for path in (written / "all_images").iterdir()) == frames


def test_the_staged_name_of_a_300_character_stem():
    """A 300-character stem is staged as its first 90 characters and 8 of its file's SHA-256."""
    # macOS holds no 300-character file name, so the name the import stages is checked directly.
    digest = hashlib.sha256(b"photo").hexdigest()
    assert folder_projects._short_stem("x" * 300, digest) == "x" * 90 + "_" + digest[:8]
    assert folder_projects._short_stem("x" * 100, digest) == "x" * 100


def gradient(top):
    """Return a 256 by 128 left-to-right gradient from 0 to top, as 16-bit samples."""
    import numpy as np
    return np.tile(np.linspace(0, top, 256), (128, 1)).astype(np.uint16)


def edges_of(project, name):
    """Return the mean gray of a working frame's left edge, middle and right edge."""
    import numpy as np
    with Image.open(project / STEP4_DIR / "custom_imports" / "raw" / name) as working:
        gray = np.asarray(working.convert("L")).astype(float)
    return gray[:, :8].mean(), gray[:, 124:132].mean(), gray[:, -8:].mean()


def test_a_sixteen_bit_png_keeps_its_full_range(tmp_path, root):
    """16-bit and 12-bit grayscale PNGs come in scaled by their own maximum; an 8-bit PNG comes in untouched."""
    folder = tmp_path / "photos"
    folder.mkdir()
    Image.fromarray(gradient(65535)).save(folder / "gray16.png")
    Image.fromarray(gradient(4095)).save(folder / "gray12.png")  # a 12-bit camera's samples, stored in 16 bits
    eight = folder / "gray8.png"
    Image.fromarray(gradient(255).astype("uint8")).save(eight)
    result, project = make(folder, root)
    assert result["added"] == 3 and result["skipped"] == []
    for name in ("custom_gray16.jpg", "custom_gray12.jpg", "custom_gray8.jpg"):
        left, middle, right = edges_of(project, name)
        assert left < 10 and 115 < middle < 140 and right > 245, (name, left, middle, right)
    originals = project / STEP4_DIR / "custom_imports" / "originals"
    # An 8-bit image comes in untouched; a 16-bit one as its 8-bit copy, which VEGA keeps as the original.
    assert (originals / "gray8.png").read_bytes() == eight.read_bytes()
    with Image.open(originals / "gray16.png") as kept:
        assert kept.mode == "L" and kept.getextrema() == (0, 255)
    assert sources_of(project)[sha_of(folder / "gray16.png")] == ["custom_gray16.jpg"]


@ON_MAC
def test_a_sixteen_bit_tiff_keeps_its_full_range(tmp_path, root):
    """16-bit and 12-bit grayscale TIFFs, which sips keeps at 16 bits, come in with their full range."""
    folder = tmp_path / "photos"
    folder.mkdir()
    Image.fromarray(gradient(65535)).save(folder / "scan16.tif")
    Image.fromarray(gradient(4095)).save(folder / "scan12.tiff")
    result, project = make(folder, root)
    assert result["added"] == 2 and result["skipped"] == []
    for name in ("custom_scan16_tif.jpg", "custom_scan12_tiff.jpg"):
        left, middle, right = edges_of(project, name)
        assert left < 10 and 115 < middle < 140 and right > 245, (name, left, middle, right)


def test_create_marks_the_project_creating_until_its_import_ends(tmp_path, root):
    """A new project is marked creating from before it appears until its import ends; a chosen id is checked."""
    folder = tmp_path / "photos"
    for index in range(2):
        helpers.write_image(folder / f"IMG_{index}.jpg", color=(index * 90, 40, 40))
    run = folder_projects.new_project_id("Reef health", "LO", root)
    seen = []

    def look(_done, _total, _message):
        """Record what the project's local record says while its images come in."""
        seen.append((folder_projects.creating(root / run), folder_projects.made_here(root / run)["creating"]))

    result = folder_projects.create(folder, "Reef health", LABELS, "lo", "x", root=root, run=run, progress=look)
    assert result["run"] == run and result["added"] == 2
    assert seen == [(True, True), (True, True)]
    assert folder_projects.creating(root / run) is False
    assert "creating" not in json.loads((root / run / transfer.LOCAL_STATE).read_text(encoding="utf-8"))
    # The id the caller chose is taken now, and one that is not a run name is refused; nothing more is made.
    with pytest.raises(folder_projects.FolderError, match="already"):
        folder_projects.create(folder, "Reef health", LABELS, "lo", "x", root=root, run=run)
    for wrong in ("../run_x", "run_", "reef", "run_a/b", 7):
        with pytest.raises(folder_projects.FolderError, match="project name"):
            folder_projects.create(folder, "Reef health", LABELS, "lo", "x", root=root, run=wrong)
    assert sorted(path.name for path in root.iterdir()) == [run]


def test_an_import_stopped_partway_stays_creating_until_add_images_finishes_it(tmp_path, root):
    """A stopped import leaves the mark, and ADD IMAGES with the same folder brings in the rest and clears it."""
    drive = tmp_path / "Volumes" / "FIELD_USB" / "dive_3"
    for index in range(4):
        helpers.write_image(drive / f"IMG_{index}.jpg", color=(index * 50, 80, 80))
    backup = tmp_path / "backup_of_dive_3"
    shutil.copytree(drive, backup)

    def unplug_after_first(done, _total, _message):
        """Pull the drive once the first image is in."""
        if done == 1:
            shutil.rmtree(drive.parent)

    with pytest.raises(folder_projects.FolderError, match="1 of 4"):
        folder_projects.create(drive, "Dive three", LABELS, "lo", "x", root=root, progress=unplug_after_first)
    (project,) = list(root.iterdir())
    assert folder_projects.creating(project) is True
    assert sorted(store_of(project)) == ["custom_IMG_0.jpg"]
    shutil.copytree(backup, drive)
    result = folder_projects.add_images(project, drive, "LO")
    assert result["added"] == 3
    assert [(item["path"].name, item["reason"]) for item in result["skipped"]] == [("IMG_0.jpg", "already in the project")]
    assert folder_projects.creating(project) is False
    assert len(store_of(project)) == 4


@pytest.mark.parametrize("error", [labels.LabelError, folder_projects.FolderError, "package"])
def test_errors_reach_the_job_board_as_their_sentence(error):
    from vega_annotator import home, package
    error = package.PackageError if error == "package" else error
    assert issubclass(error, transfer.TransferError)
    board = home.JobBoard()

    def work(_progress):
        raise error("The drive needs 1.2 GB free and has 0.1 GB, so nothing was written.")

    job = board.start("test", "", work)
    for _attempt in range(200):
        if job["status"] != "running":
            break
        time.sleep(0.01)
    assert job["status"] == "failed"
    assert job["error"] == "The drive needs 1.2 GB free and has 0.1 GB, so nothing was written."

