"""Tests for pathmap.py: paths move between mainframe form and local form, and back."""

import json

import pytest

from vega_annotator import pathmap
from vega_annotator.pathmap import PathMap, map_strings, rewrite_file, rewrite_text

SERVER = "/mnt/rip/inprocess/run_X"


def test_to_local_and_back_posix():
    mapper = PathMap(SERVER, "/Users/at/projects/run_X")
    local = mapper.to_local(f"{SERVER}/step4/raw/a.jpeg")
    assert local == "/Users/at/projects/run_X/step4/raw/a.jpeg"
    assert mapper.to_server(local) == f"{SERVER}/step4/raw/a.jpeg"


def test_roots_map_to_each_other():
    mapper = PathMap(SERVER, "/Users/at/run_X")
    assert mapper.to_local(SERVER) == "/Users/at/run_X"
    assert mapper.to_server("/Users/at/run_X") == SERVER


def test_paths_outside_the_project_stay():
    mapper = PathMap(SERVER, "/Users/at/run_X")
    for other in ("/mnt/rip/other/TCRMP_clip/a.jpg", f"{SERVER}_longer/a.jpg", "PA", ""):
        assert mapper.to_local(other) == other
    assert mapper.to_server("/Users/at/run_X_other/a") == "/Users/at/run_X_other/a"


def test_windows_form_goes_back_to_forward_slashes():
    mapper = PathMap(SERVER, r"C:\Users\at\run_X")
    assert mapper.to_server(r"C:\Users\at\run_X\step4\raw\a.jpeg") == f"{SERVER}/step4/raw/a.jpeg"
    assert mapper.to_server("C:/Users/at/run_X/step4/raw/a.jpeg") == f"{SERVER}/step4/raw/a.jpeg"


def test_map_strings_walks_lists_and_dicts_and_leaves_keys():
    result = map_strings({"/k": ["/a", {"n": 1, "s": "/a"}], "none": None}, lambda s: s.upper())
    assert result == {"/k": ["/A", {"n": 1, "s": "/A"}], "none": None}


def test_segmentations_keep_vegas_layout():
    store = {"a.jpeg": {"image_path": "raw/a.jpeg", "image_path_abs": f"{SERVER}/raw/a.jpeg", "masks": []}}
    text = json.dumps(store, indent=1)
    mapper = PathMap(SERVER, "/L")
    local = rewrite_text(pathmap.SEGMENTATIONS_REL, text, mapper.to_local)
    assert json.loads(local)["a.jpeg"]["image_path_abs"] == "/L/raw/a.jpeg"
    assert rewrite_text(pathmap.SEGMENTATIONS_REL, local, mapper.to_server) == text


def test_manifest_rewrites_only_the_path_column_and_keeps_crlf():
    text = f"basename,image_path,PA\r\na,{SERVER}/raw/a.jpeg,1\r\nb,,\r\n"
    mapper = PathMap(SERVER, "/L")
    local = rewrite_text(pathmap.MANIFEST_REL, text, mapper.to_local)
    assert local == "basename,image_path,PA\r\na,/L/raw/a.jpeg,1\r\nb,,\r\n"
    assert rewrite_text(pathmap.MANIFEST_REL, local, mapper.to_server) == text


def test_manifest_keeps_lf_and_a_quoted_comma():
    text = f'basename,image_path\n"a,b",{SERVER}/x.jpeg\n'
    local = rewrite_text(pathmap.MANIFEST_REL, text, PathMap(SERVER, "/L").to_local)
    assert local == 'basename,image_path\n"a,b",/L/x.jpeg\n'


def test_manifest_without_the_column_is_unchanged():
    text = "basename,other\na,b\n"
    assert rewrite_text(pathmap.MANIFEST_REL, text, str.upper) == text


def test_data_yaml_rewrites_only_the_path_line():
    text = f"path: {SERVER}/step4\ntrain: all_images\nnames:\n  0: PA\n"
    mapper = PathMap(SERVER, "/L")
    assert rewrite_text(pathmap.DATA_YAML_REL, text, mapper.to_local) == "path: /L/step4\ntrain: all_images\nnames:\n  0: PA\n"


def test_project_json_rewrites_every_path_inside():
    text = json.dumps({"project_dir": SERVER, "steps": {"3": {"dir": f"{SERVER}/s3", "clip": "/other"}}}, indent=2)
    local = json.loads(rewrite_text(pathmap.PROJECT_JSON_REL, text, PathMap(SERVER, "/L").to_local))
    assert local == {"project_dir": "/L", "steps": {"3": {"dir": "/L/s3", "clip": "/other"}}}


def test_rewrite_text_refuses_other_files():
    with pytest.raises(ValueError):
        rewrite_text("step4/notes.txt", "x", str)


def test_rewrite_file_leaves_a_malformed_file_alone(tmp_path):
    path = tmp_path / "segmentations.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        rewrite_file(path, pathmap.SEGMENTATIONS_REL, str)
    assert path.read_text(encoding="utf-8") == "{broken"


def test_rewrite_file_writes_to_another_destination(tmp_path):
    path = tmp_path / "data.yaml"
    path.write_text(f"path: {SERVER}/a\n", encoding="utf-8")
    dest = tmp_path / "out" / "data.yaml"
    rewrite_file(path, pathmap.DATA_YAML_REL, PathMap(SERVER, "/L").to_local, dest=dest)
    assert dest.read_text(encoding="utf-8") == "path: /L/a\n"
    assert path.read_text(encoding="utf-8") == f"path: {SERVER}/a\n"
