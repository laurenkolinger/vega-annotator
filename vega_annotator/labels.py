"""Labels a person defines for a project made from a folder, kept in VEGA's own files.

A folder project's labels are plain words such as healthy, bleached or diseased, kept with
their exact spelling and case. VEGA's annotator reads them from three places, and this
module keeps the three in step:

- project.json: label_set {kind: "custom", labels: [...]} and Step 3's target_species, which
  the app hands the annotator as TCRMP_TARGET_SPECIES.
- step4test_combinedAnnotate/master_codes_recoded.csv: each label with the category Label,
  so no label is stored as Coral.
- step4test_combinedAnnotate/class_map.json: YOLO class ids in label order. A new label
  takes the next id and no id ever changes once the project has exported, because VEGA
  numbers classes as len(class_map) and writes nc as the count of names, so a gap would
  stop training.

Adding, renaming and removing a label refuse, with a sentence saying what to do, while the
annotator has the project open: the annotator reads its labels once at start and rewrites
class_map.json on every save.
"""

import csv
import io
import json
import unicodedata
from pathlib import Path

import yaml

from . import projects, transfer
from .atomic import write_bytes_atomic
from .pathmap import MANIFEST_REL, STEP4_DIR

MAX_LABEL_CHARS = 40
ALLOWED_MARKS = frozenset(" -_.+()")
ALLOWED_MARKS_TEXT = "letters, digits, spaces and - _ . + ( )"
# VEGA's review tag and default species, and the fixed columns of its export manifest,
# where each label also becomes a column of its own.
RESERVED_WORDS = frozenset({"review", "unk", "basename", "n_masks", "label_file", "image_file",
                            "outcome", "preview_file", "batch_name", "image_path"})
LABEL_CATEGORY = "Label"
CODES_FIELDS = ("species_code", "species_name", "category")
CLASS_MAP_REL = f"{STEP4_DIR}/class_map.json"
CLASS_MAP_INDENT = 2  # VEGA's save_class_map writes json.dump(class_map, f, indent=2)
TARGET_SEPARATOR = ", "  # VEGA stores Step 3's target labels as "OFRA, PA, SS"
# How a refusal names a character, in words, so no message shows a bare quote mark.
CHARACTER_WORDS = {
    '"': "a double quote", "'": "an apostrophe", ",": "a comma", ":": "a colon", ";": "a semicolon",
    "/": "a slash", "\\": "a backslash", "<": "a less-than sign", ">": "a greater-than sign", "&": "an ampersand",
    "#": "a number sign", "%": "a percent sign", "*": "an asterisk", "?": "a question mark",
    "!": "an exclamation mark", "@": "an at sign", "$": "a dollar sign", "=": "an equals sign",
    "[": "an opening bracket", "]": "a closing bracket", "{": "an opening brace", "}": "a closing brace",
    "|": "a vertical bar", "~": "a tilde", "`": "a backtick", "^": "a caret", "-": "a hyphen",
    "_": "an underscore", ".": "a period", "+": "a plus sign", "(": "an opening parenthesis",
    ")": "a closing parenthesis", "\u2018": "an opening curly quote", "\u2019": "a curly apostrophe",
    "\u201c": "an opening curly double quote", "\u201d": "a closing curly double quote",
}


class LabelError(transfer.TransferError):
    """A label or a label change that cannot go ahead, with a sentence the person can act on.

    It is a TransferError, so the home screen's job board shows the sentence alone.
    """


def tidy(text: str) -> str:
    """Trim a label and collapse each run of spaces, tabs or other blanks into one space.

    The text is also put in composed Unicode form, so an accent typed as two characters
    becomes the one letter it shows.

    Parameters:
        text: the label as typed.
    Returns:
        str: the tidied label.
    Raises:
        LabelError: when text is not text.
    """
    if not isinstance(text, str):
        raise LabelError("Each label must be text, such as healthy.")
    return " ".join(unicodedata.normalize("NFC", text).split())


def _shown(character):
    """Return a character as a refusal names it: in words, such as "a double quote".

    A character without a name of its own here takes its Unicode name, such as "the
    character em dash", or its code when it does not print, such as "the character U+200B".
    """
    if character in CHARACTER_WORDS:
        return CHARACTER_WORDS[character]
    name = unicodedata.name(character, "")
    if name and character.isprintable():
        return f"the character {name.lower()}"
    return f"the character U+{ord(character):04X}"


def _listed(words):
    """Join words as a sentence lists them: "a", "a and b", "a, b and c"."""
    words = list(words)
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def _quoted(label):
    """Return a label in quotes for a message: double quotes, or single ones when the label holds a double quote."""
    return f"'{label}'" if '"' in label and "'" not in label else f'"{label}"'


def _folded(label):
    """Return the form two labels are compared in: compatibility-normalized and case-folded.

    "Healthy", "HEALTHY" and full-width "\uff28\uff45\uff41\uff4c\uff54\uff48\uff59" all fold to "healthy".
    """
    return unicodedata.normalize("NFKC", label).casefold()


def _yaml_meaning(value):
    """Describe what YAML made of a label that did not read back as text."""
    if isinstance(value, bool):
        return f"the value {str(value).lower()}"
    if value is None:
        return "an empty value"
    if isinstance(value, (int, float)):
        return f"the number {value:g}"
    return f"the value {value!r}"


def check(text: str, existing: list[str]) -> str:
    """Tidy one label and check it against VEGA's rules and the labels already chosen.

    Parameters:
        text: the label as typed.
        existing: the project's other labels.
    Returns:
        str: the tidied label.
    Raises:
        LabelError: naming the rule the label breaks and what to change.
    """
    label = tidy(text)
    if not label:
        raise LabelError("A label cannot be empty. Type a word such as healthy.")
    if len(label) > MAX_LABEL_CHARS:
        raise LabelError(f"{_quoted(label)} has {len(label)} characters. Shorten it to {MAX_LABEL_CHARS} or fewer.")
    outside = []
    for character in label:
        if not (character.isalpha() or character.isdigit() or character in ALLOWED_MARKS) \
                and character not in outside:
            outside.append(character)
    if outside:
        raise LabelError(f"{_quoted(label)} contains {_listed(_shown(character) for character in outside)}. "
                         f"Use only {ALLOWED_MARKS_TEXT}.")
    if not (label[0].isalpha() or label[0].isdigit()):
        raise LabelError(f"{_quoted(label)} starts with {_shown(label[0])}. Start the label with a letter or a digit.")
    if not any(character.isalpha() for character in label):
        raise LabelError(f'{_quoted(label)} has no letter. Add at least one, as in "stage 2".')
    if _folded(label) in RESERVED_WORDS:
        raise LabelError(f"{_quoted(label)} is a word VEGA keeps for itself. Choose another label.")
    try:
        read_back = yaml.safe_load("- " + label)
    except yaml.YAMLError:
        read_back = None
    if read_back != [label]:
        meaning = _yaml_meaning(read_back[0]) if isinstance(read_back, list) and read_back else "something else"
        raise LabelError(f"{_quoted(label)} would turn into {meaning} in VEGA's data.yaml. "
                         f"Add a word to it or choose another label.")
    for other in existing or []:
        other = str(other)
        if other == label:
            raise LabelError(f"{_quoted(label)} is already in the label list.")
        if other.casefold() == label.casefold():
            raise LabelError(f"{_quoted(label)} and {_quoted(other)} differ only in case. Keep one of them.")
        if _folded(other) == _folded(label):
            raise LabelError(f"{_quoted(label)} and {_quoted(other)} read as the same label. Keep one of them.")
    return label


def parse(value) -> list[str]:
    """Read a label list from the person's typing.

    Parameters:
        value: a list of labels, or text with one label per line or labels split by commas.
            Blank entries are dropped.
    Returns:
        list: the tidied labels, in the order given.
    Raises:
        LabelError: when value is neither, when any label breaks a rule, or when no label
            is left.
    """
    if isinstance(value, str):
        pieces = value.replace(",", "\n").splitlines()
    elif isinstance(value, (list, tuple)):
        pieces = list(value)
    else:
        raise LabelError("Labels must be text, one per line or split by commas.")
    names = []
    for piece in pieces:
        if not isinstance(piece, str):
            raise LabelError("Labels must be text, one per line or split by commas.")
        if tidy(piece):
            names.append(check(piece, names))
    if not names:
        raise LabelError("Type at least one label, such as healthy.")
    return names


def _read_class_map(project_dir):
    """Read the project's class_map.json and check its ids run 0, 1, 2 with no gap.

    Returns:
        dict: {label: class id}; {} when the file does not exist yet.
    Raises:
        LabelError: when the file cannot be read or its ids are not 0 to n - 1.
    """
    path = Path(project_dir) / CLASS_MAP_REL
    if not path.exists():
        return {}
    try:
        class_map = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise LabelError(f"{path} cannot be read ({exc}), so the labels were not changed.") from exc
    whole = isinstance(class_map, dict) and all(
        isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool)
        for key, value in class_map.items())
    if not whole or sorted(class_map.values()) != list(range(len(class_map))):
        raise LabelError(f"{path} does not number its labels 0, 1, 2 and so on, "
                         f"so the labels were not changed. Restore the file from a backup.")
    return class_map


def _save(project_dir, names, class_map):
    """Write the three label files: the class map, the label table, then project.json.

    project.json goes last, since the annotator takes its label list from there.
    """
    project_dir = Path(project_dir)
    write_bytes_atomic(project_dir / CLASS_MAP_REL,
                       json.dumps(class_map, indent=CLASS_MAP_INDENT).encode("utf-8"))
    table = io.StringIO()
    writer = csv.writer(table, lineterminator="\n")
    writer.writerow(CODES_FIELDS)
    writer.writerows((name, name, LABEL_CATEGORY) for name in names)
    write_bytes_atomic(project_dir / projects.CUSTOM_CODES_REL, table.getvalue().encode("utf-8"))

    def record_labels(data):
        """Store the label set and Step 3's target labels."""
        data["label_set"] = {"kind": projects.CUSTOM_LABEL_SET, "labels": list(names)}
        steps = data.get("steps") if isinstance(data.get("steps"), dict) else {}
        step3 = steps.get("3") if isinstance(steps.get("3"), dict) else {}
        config = step3.get("config") if isinstance(step3.get("config"), dict) else {}
        config["target_species"] = TARGET_SEPARATOR.join(names)
        step3["config"] = config
        steps["3"] = step3
        data["steps"] = steps
        data["updated_at"] = projects.now_ast()

    projects.update_project(project_dir, record_labels)


def write(project_dir: Path, names: list[str]) -> None:
    """Write a project's labels into project.json, the label table and the class map.

    A label new to the class map takes the next class id; no existing id changes.

    Parameters:
        project_dir: the run_* folder.
        names: the labels, in the order the annotator offers them (keys 1 to 9 pick
            the first nine).
    Returns:
        None.
    Raises:
        LabelError: when a label breaks a rule, or the class map cannot be read.
        ValueError: when project.json is missing or malformed.
        OSError: when a file cannot be written.
    """
    names = parse(names)
    _save(project_dir, names, _with_new_labels(_read_class_map(project_dir), names))


def _with_new_labels(class_map, names):
    """Return the class map with each label it lacks added at the end, as VEGA numbers them.

    Parameters:
        class_map: {label: class id}, numbered 0 to n - 1.
        names: the project's labels.
    Returns:
        dict: the class map, extended.
    """
    class_map = dict(class_map)
    for name in names:
        if name not in class_map:
            class_map[name] = len(class_map)
    return class_map


def exported(project_dir: Path) -> bool:
    """Say whether the project has exported a batch, after which class ids are fixed.

    Parameters:
        project_dir: the run_* folder.
    Returns:
        bool: True once export_manifest.csv holds a row, and also when the manifest
        exists and cannot be read, since then no export can be ruled out.
    """
    path = Path(project_dir) / MANIFEST_REL
    if not path.exists():
        return False
    try:
        with open(path, newline="", encoding="utf-8") as handle:
            rows = csv.reader(handle)
            next(rows, None)
            return any(any(cell.strip() for cell in row) for row in rows)
    except (OSError, UnicodeDecodeError, csv.Error):
        return True


def current(project_dir: Path) -> list[str]:
    """Return the labels of a project made from a folder.

    Parameters:
        project_dir: the run_* folder.
    Returns:
        list: the labels, in order.
    Raises:
        LabelError: when the project's labels belong to VEGA rather than to a person.
        ValueError: when project.json is missing or malformed.
    """
    project = projects.read_project(project_dir)
    if not projects.has_custom_labels(project):
        raise LabelError(f"{Path(project_dir).name} takes its labels from VEGA. "
                         f"Labels can be changed here only on a project made from a folder of images.")
    return [str(name) for name in project["label_set"].get("labels") or []]


def _changeable(project_dir):
    """Return the current labels, or refuse while the annotator has the project open."""
    names = current(project_dir)
    if projects.open_in_annotator(project_dir):
        raise LabelError("Close the annotator on this project first. "
                         "The annotator reads its labels only when it opens.")
    return names


def _refuse_if_fixed(project_dir, name, change):
    """Refuse renaming or removing a label once its class id is fixed or a mask uses it.

    Parameters:
        project_dir: the run_* folder.
        name: the label.
        change: "renamed" or "removed", for the sentence.
    Raises:
        LabelError: after the first export, or while a mask in the store carries the label.
    """
    if exported(project_dir):
        raise LabelError(f"This project has exported frames, so {_quoted(name)} can no longer be {change}. "
                         f"Add a new label instead.")
    try:
        store = projects._read_store(project_dir)
    except ValueError as exc:
        raise LabelError(f"The project's frames cannot be read ({exc}), so no label was {change}.") from exc
    uses = sum(1 for frame in store.values() if isinstance(frame, dict)
               for mask in frame.get("masks") or [] if isinstance(mask, dict) and mask.get("species") == name)
    if uses:
        word = "mask carries" if uses == 1 else "masks carry"
        raise LabelError(f"{uses} {word} {_quoted(name)}, so it cannot be {change}. "
                         f"Relabel or delete those masks in the annotator first.")


def add(project_dir, names) -> list[str]:
    """Add labels at the end of the list, each with the next class id.

    Parameters:
        project_dir: the run_* folder.
        names: a list of labels, or text with one per line or split by commas.
    Returns:
        list: all the project's labels.
    Raises:
        LabelError: when a label breaks a rule or clashes, or the annotator has the project open.
    """
    labels_now = _changeable(project_dir)
    new = []
    for name in parse(names):
        new.append(check(name, labels_now + new))
    write(project_dir, labels_now + new)
    return labels_now + new


def rename(project_dir, old, new) -> list[str]:
    """Rename one label, keeping its place and class id, until the project's first export.

    Parameters:
        project_dir: the run_* folder.
        old: the label as it is now.
        new: the new spelling.
    Returns:
        list: all the project's labels.
    Raises:
        LabelError: when old is not a label, new breaks a rule or clashes, the project has
            exported, a mask carries old, or the annotator has the project open.
    """
    labels_now = _changeable(project_dir)
    if old not in labels_now:
        raise LabelError(f"{_quoted(old)} is not one of this project's labels.")
    others = [name for name in labels_now if name != old]
    new = check(new, others)
    if new == old:
        return labels_now
    _refuse_if_fixed(project_dir, old, "renamed")
    names = [new if name == old else name for name in labels_now]
    class_map = {(new if key == old else key): value for key, value in _read_class_map(project_dir).items()}
    _save(project_dir, names, _with_new_labels(class_map, names))
    return names


def remove(project_dir, name) -> list[str]:
    """Remove one label until the project's first export, numbering the rest 0, 1, 2 again.

    Parameters:
        project_dir: the run_* folder.
        name: the label to remove.
    Returns:
        list: the labels left.
    Raises:
        LabelError: when name is not a label or is the last one, the project has exported,
            a mask carries it, or the annotator has the project open.
    """
    labels_now = _changeable(project_dir)
    if name not in labels_now:
        raise LabelError(f"{_quoted(name)} is not one of this project's labels.")
    if len(labels_now) == 1:
        raise LabelError(f"{_quoted(name)} is the only label, and a project needs at least one label. "
                         f"Add another label first.")
    _refuse_if_fixed(project_dir, name, "removed")
    names = [item for item in labels_now if item != name]
    kept = sorted((value, key) for key, value in _read_class_map(project_dir).items() if key != name)
    class_map = {key: index for index, (_value, key) in enumerate(kept)}
    _save(project_dir, names, _with_new_labels(class_map, names))
    return names
