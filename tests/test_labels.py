"""Tests for labels.py: the words a person defines as a folder project's labels."""

import csv
import json

import pytest
import yaml

import helpers
from vega_annotator import labels, projects
from vega_annotator.pathmap import SEGMENTATIONS_REL, STEP4_DIR

CONDITION_WORDS = ["healthy", "Partly bleached", "SCTLD (lesion)", "stage 2", "blanqueado", "grade 1.5+"]


@pytest.fixture
def project(tmp_path):
    """Return a bare folder project with three labels written."""
    folder = helpers.make_bare_project(tmp_path / "run_26-11-09_LO_reef")
    labels.write(folder, ["healthy", "bleached", "diseased"])
    return folder


def class_map(folder):
    return json.loads((folder / STEP4_DIR / "class_map.json").read_text(encoding="utf-8"))


def test_check_accepts_condition_words():
    for word in CONDITION_WORDS:
        assert labels.check(word, []) == word
    # VEGA writes class names unquoted under names: in data.yaml, and each one reads back unchanged.
    data_yaml = "names:\n" + "".join(f"  {index}: {word}\n" for index, word in enumerate(CONDITION_WORDS))
    assert yaml.safe_load(data_yaml)["names"] == dict(enumerate(CONDITION_WORDS))


@pytest.mark.parametrize("text,named", [
    ("", ["empty"]),
    ("x" * 41, ["41 characters", "40 or fewer"]),
    ("123", ["no letter"]),
    ("-x", ["starts with a hyphen", "letter or a digit"]),
    ("healthy, bleached", ["contains a comma.", "Use only"]),
    ("Acropora's", ["contains an apostrophe.", "Use only"]),
    ("<b>", ["contains a less-than sign and a greater-than sign.", "Use only"]),
    ("ratio:2", ["contains a colon.", "Use only"]),
    ("live/dead", ["contains a slash.", "Use only"]),
    ('say "hi"', ["'say \"hi\"' contains a double quote.", "Use only"]),
    ("a;b?c", ["contains a semicolon and a question mark.", "Use only"]),
    ("tab\u200bhidden", ["contains the character U+200B.", "Use only"]),
    ("review", ["keeps for itself"]),
    ("REVIEW", ["keeps for itself"]),
    ("unk", ["keeps for itself"]),
    ("outcome", ["keeps for itself"]),
    ("image_path", ["keeps for itself"]),
    ("yes", ["data.yaml", "true"]),
    ("No", ["data.yaml", "false"]),
    ("null", ["data.yaml", "empty value"]),
    ("0x1F", ["data.yaml", "number 31"]),
    ("1.5e+3", ["data.yaml", "number 1500"]),
])
def test_check_refuses_each_rule(text, named):
    with pytest.raises(labels.LabelError) as refusal:
        labels.check(text, [])
    message = str(refusal.value)
    for words in named:
        assert words in message
    assert "\u2014" not in message


def test_labels_tidy_spaces_and_refuse_case_clash():
    assert labels.tidy("  Partly   bleached ") == "Partly bleached"
    assert labels.tidy("Partly\u00a0\tbleached\n") == "Partly bleached"
    assert labels.check("  Partly   bleached ", []) == "Partly bleached"
    with pytest.raises(labels.LabelError) as refusal:
        labels.check("BLEACHED", ["healthy", "bleached"])
    assert "\"BLEACHED\"" in str(refusal.value) and "\"bleached\"" in str(refusal.value)
    assert "only in case" in str(refusal.value)
    with pytest.raises(labels.LabelError, match="only in case"):
        labels.parse("bleached\nBLEACHED")
    with pytest.raises(labels.LabelError, match="already in the label list"):
        labels.parse(["healthy", " healthy "])


def test_refusals_name_characters_in_words_and_never_nest_quotes():
    """A refusal names each character in words, and quotes a label holding a double quote in single quotes."""
    with pytest.raises(labels.LabelError) as refusal:
        labels.check('say "hi"', [])
    message = str(refusal.value)
    assert message == "'say \"hi\"' contains a double quote. Use only letters, digits, spaces and - _ . + ( )."
    assert '"""' not in message and '""' not in message
    with pytest.raises(labels.LabelError) as refusal:
        labels.check("a\u2014b", [])
    assert "contains the character em dash." in str(refusal.value)


def test_a_full_width_label_clashes_with_its_plain_twin():
    """Labels are compared after compatibility normalizing and case folding, as reserved words are."""
    with pytest.raises(labels.LabelError) as refusal:
        labels.check("\uff28\uff45\uff41\uff4c\uff54\uff48\uff59", ["Healthy"])
    assert "read as the same label" in str(refusal.value) and "\"Healthy\"" in str(refusal.value)
    with pytest.raises(labels.LabelError, match="read as the same label"):
        labels.parse("stage 2\nstage \u00b2")  # a superscript two folds to 2
    with pytest.raises(labels.LabelError, match="keeps for itself"):
        labels.check("\uff52\uff45\uff56\uff49\uff45\uff57", [])  # a full-width "review"
    assert labels.check("\uff28\uff45\uff41\uff4c\uff54\uff48\uff59", ["bleached"]) == "\uff28\uff45\uff41\uff4c\uff54\uff48\uff59"


def test_check_keeps_a_decomposed_accent():
    decomposed = "blanqueado parcial e\u0301"
    assert labels.check(decomposed, []) == "blanqueado parcial \u00e9"


def test_parse_splits_lines_and_commas():
    assert labels.parse("healthy, bleached\ndiseased\r\n\n , ") == ["healthy", "bleached", "diseased"]
    assert labels.parse(["healthy", " Partly  bleached ", ""]) == ["healthy", "Partly bleached"]
    for empty in ("", " , \n", [], ["  "]):
        with pytest.raises(labels.LabelError, match="at least one label"):
            labels.parse(empty)
    for wrong in (None, 3, {"a": 1}, ["healthy", 3]):
        with pytest.raises(labels.LabelError, match="text"):
            labels.parse(wrong)
    with pytest.raises(labels.LabelError, match="review"):
        labels.parse("healthy, review")


def test_write_seeds_class_map_in_order_and_appends_later(project):
    assert class_map(project) == {"healthy": 0, "bleached": 1, "diseased": 2}
    record = projects.read_project(project)
    assert record["label_set"] == {"kind": "custom", "labels": ["healthy", "bleached", "diseased"]}
    assert projects.target_codes(record) == ["healthy", "bleached", "diseased"]
    assert labels.current(project) == ["healthy", "bleached", "diseased"]
    # A rewrite in another order changes no class id, and a new label takes the next one.
    labels.write(project, ["dead", "diseased", "healthy", "bleached"])
    assert class_map(project) == {"healthy": 0, "bleached": 1, "diseased": 2, "dead": 3}
    assert labels.add(project, ["Partly bleached"]) == ["dead", "diseased", "healthy", "bleached", "Partly bleached"]
    assert class_map(project)["Partly bleached"] == 4
    raw = (project / STEP4_DIR / "class_map.json").read_text(encoding="utf-8")
    assert raw == json.dumps(class_map(project), indent=2)


def test_add_refuses_a_clash_and_changes_nothing(project):
    before = (project / "project.json").read_bytes()
    with pytest.raises(labels.LabelError, match="only in case"):
        labels.add(project, "dead, HEALTHY")
    assert (project / "project.json").read_bytes() == before
    assert class_map(project) == {"healthy": 0, "bleached": 1, "diseased": 2}


def test_write_makes_master_codes_with_category_Label(project):
    labels.add(project, ["blanqueado (\u00e9)"])
    with open(project / STEP4_DIR / "master_codes_recoded.csv", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [{"species_code": name, "species_name": name, "category": "Label"}
                    for name in ("healthy", "bleached", "diseased", "blanqueado (\u00e9)")]


def test_rename_and_remove_before_the_first_export(project):
    assert labels.rename(project, "bleached", "Partly bleached") == ["healthy", "Partly bleached", "diseased"]
    assert class_map(project) == {"healthy": 0, "Partly bleached": 1, "diseased": 2}
    assert labels.remove(project, "healthy") == ["Partly bleached", "diseased"]
    assert class_map(project) == {"Partly bleached": 0, "diseased": 1}
    with pytest.raises(labels.LabelError, match="not one of this project's labels"):
        labels.remove(project, "healthy")
    labels.remove(project, "diseased")
    with pytest.raises(labels.LabelError, match="at least one label"):
        labels.remove(project, "Partly bleached")


def test_rename_and_remove_refused_after_first_export(project):
    assert labels.exported(project) is False
    manifest = project / STEP4_DIR / "export_manifest.csv"
    manifest.write_text("basename,n_masks,label_file,image_file,outcome,preview_file,batch_name,image_path\n",
                        encoding="utf-8")
    assert labels.exported(project) is False
    with open(manifest, "a", encoding="utf-8") as handle:
        handle.write("custom_a,0,custom_a.txt,custom_a.jpg,exported_empty,,batch_001,/x/custom_a.jpg\n")
    assert labels.exported(project) is True
    with pytest.raises(labels.LabelError, match="exported"):
        labels.rename(project, "bleached", "Partly bleached")
    with pytest.raises(labels.LabelError, match="exported"):
        labels.remove(project, "diseased")
    assert labels.add(project, ["dead"])[-1] == "dead"
    assert class_map(project)["dead"] == 3


def test_remove_refused_while_a_mask_uses_it(project):
    store_path = project / SEGMENTATIONS_REL
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps({"custom_a.jpg": {"masks": [
        {"id": 0, "species": "bleached", "status": "rejected"}]}}, indent=1), encoding="utf-8")
    with pytest.raises(labels.LabelError, match="1 mask"):
        labels.remove(project, "bleached")
    with pytest.raises(labels.LabelError, match="1 mask"):
        labels.rename(project, "bleached", "Bleached")
    assert labels.remove(project, "diseased") == ["healthy", "bleached"]


def test_label_changes_refused_while_the_annotator_has_the_project(project, monkeypatch):
    monkeypatch.setattr(projects, "open_in_annotator", lambda folder: True)
    for change in (lambda: labels.add(project, ["dead"]), lambda: labels.rename(project, "healthy", "fine"),
                   lambda: labels.remove(project, "diseased")):
        with pytest.raises(labels.LabelError, match="Close the annotator"):
            change()
    assert labels.current(project) == ["healthy", "bleached", "diseased"]


def test_labels_refused_on_a_vega_project(source_project):
    with pytest.raises(labels.LabelError, match="made from a folder"):
        labels.add(source_project, ["healthy"])
