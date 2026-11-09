"""VEGA project records: reading them, counting their work, and logging who opened them.

These functions follow the VICARIUS launcher's rules so a project reads the same here
as on the mainframe. The access log entry has the launcher's shape, the counts follow
its project cards, and the annotator's settings follow VEGA's launch.
"""

import csv
import json
import os
import re
import tempfile
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .atomic import read_json_object
from .pathmap import PROJECT_JSON_REL, SEGMENTATIONS_REL, STEP4_DIR

AST = timezone(timedelta(hours=-4))
# Project stages in VEGA's order, with the names the VEGA rail shows.
RAIL_STAGES = (("1", "Fetch Points"), ("2", "Fetch Labels"), ("3", "Choose Images"),
               ("step4test", "Place + Segment"), ("6", "Train Model"),
               ("7", "Evaluate Model"), ("8", "Model Inference"))
CARD_STAGE_KEYS = ("1", "2", "3", "6", "7", "8")
MASTER_CODES_REL = "step2_recodeSpecies/master_codes_recoded.csv"
PROMPTS_REL = f"{STEP4_DIR}/routed_input/ids/sam_click_prompts.json"
IMPORTS_MANIFEST_REL = f"{STEP4_DIR}/custom_imports/imports_manifest.json"
LOCK_NAME = ".project.json.lock"
DEFAULT_BATCH_SIZE = "10"
# A project made from a folder has its own label set. Its label table sits in the Place +
# Segment folder, the first place VEGA's annotator looks for one.
CUSTOM_LABEL_SET = "custom"
CUSTOM_CODES_REL = f"{STEP4_DIR}/master_codes_recoded.csv"
LABEL_SET_ENV = "VEGA_ANNOTATOR_LABEL_SET"
# The home screen starts its annotator on the first free port of 5080 to 5089
# (home.FIRST_ANNOTATOR_PORT to home.LAST_ANNOTATOR_PORT).
ANNOTATOR_PORTS = range(5080, 5090)
ANNOTATOR_PROBE_TIMEOUT_S = 1.0
ANNOTATOR_PAGE_MAX_BYTES = 4 * 1024 * 1024
# VEGA's annotator page names the folder it saves into in this line (index.html).
EXPORT_DIR_LINE_RE = re.compile(r'const RESUME_EXPORT_DIR = ("(?:[^"\\]|\\.)*");')

_file_lock = threading.Lock()
_json_cache = {}


def now_ast():
    """Return the current time as VEGA stamps it: ISO 8601 seconds, in AST."""
    return datetime.now(AST).isoformat(timespec="seconds")


def read_project(project_dir):
    """Read a project's project.json.

    Parameters:
        project_dir: the run_* folder.
    Returns:
        dict: the project record.
    Raises:
        ValueError: when the file is missing or malformed, naming the file.
    """
    return read_json_object(Path(project_dir) / PROJECT_JSON_REL)


def _write_project(project_dir, data):
    """Write project.json through a unique temporary file, as VEGA's writers do."""
    path = Path(project_dir) / PROJECT_JSON_REL
    fd, tmp = tempfile.mkstemp(prefix=".project.json.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(data, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def update_project(project_dir, change):
    """Read project.json, let change edit it, and write it back, under the app's record lock.

    Parameters:
        project_dir: the run_* folder.
        change: a callable given the record (a dict) to edit in place.
    Returns:
        dict: the record as written.
    Raises:
        ValueError: when project.json is missing or malformed.
        OSError: when it cannot be written.
    """
    with _file_lock:
        data = read_project(project_dir)
        change(data)
        _write_project(project_dir, data)
    return data


def with_record_lock(function, *args, **kwargs):
    """Run a function that rewrites project.json by itself, such as VEGA's record_in_project,
    under the same lock as this module's own writers.

    Parameters:
        function: the callable.
        args, kwargs: its arguments.
    Returns:
        Whatever the function returns.
    """
    with _file_lock:
        return function(*args, **kwargs)


def has_custom_labels(project):
    """Say whether a project uses labels a person defined, as a project made from a folder does.

    Parameters:
        project: the project record.
    Returns:
        bool: True when label_set.kind is "custom".
    """
    label_set = project.get("label_set") if isinstance(project, dict) else None
    return isinstance(label_set, dict) and label_set.get("kind") == CUSTOM_LABEL_SET


def open_in_annotator(project_dir):
    """Say whether a running annotator has this project open.

    The home screen starts VEGA's annotator on one of ANNOTATOR_PORTS, and VEGA's page
    names the folder that annotator saves into. Asking each port finds an annotator this
    app started, and one left running by an earlier session of the app.

    Parameters:
        project_dir: the run_* folder.
    Returns:
        bool: True when an annotator on this computer serves the project.
    """
    wanted = os.path.realpath(Path(project_dir) / STEP4_DIR)
    for port in ANNOTATOR_PORTS:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=ANNOTATOR_PROBE_TIMEOUT_S) as answer:
                page = answer.read(ANNOTATOR_PAGE_MAX_BYTES).decode("utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        match = EXPORT_DIR_LINE_RE.search(page)
        if not match:
            continue
        try:
            served = json.loads(match.group(1))
        except ValueError:
            continue
        if isinstance(served, str) and served and os.path.realpath(served) == wanted:
            return True
    return False


def stamp_access(project_dir, initials, action, purpose):
    """Append one entry to the project's access log, as the VICARIUS launcher does.

    Parameters:
        project_dir: the run_* folder.
        initials: who is acting, already validated.
        action: "open", "bring_in", "put_back" or "take_over".
        purpose: why, in the person's words or a plain default.
    Returns:
        dict: the entry that was written.
    Raises:
        ValueError: when project.json is missing or malformed.
        OSError: when it cannot be written.
    """
    with _file_lock:
        data = read_project(project_dir)
        stamp = now_ast()
        entry = {"ts": stamp, "initials": initials, "action": action, "purpose": purpose}
        log = data.get("access_log")
        data["access_log"] = (log if isinstance(log, list) else []) + [entry]
        data["updated_at"] = stamp
        _write_project(project_dir, data)
    return entry


def carry_local_notes(local_dir, incoming_dir):
    """Keep this computer's unsent log rows and description in a record about to replace it.

    Parameters:
        local_dir: the working copy, whose project.json is about to be replaced.
        incoming_dir: the folder holding the replacement project.json.
    Returns:
        int: how many access log rows were carried over.
    Raises:
        ValueError: when either project.json is malformed.
    """
    with _file_lock:
        mine = read_project(local_dir)
        incoming = read_project(incoming_dir)
        theirs = incoming.get("access_log") if isinstance(incoming.get("access_log"), list) else []
        rows = mine.get("access_log") if isinstance(mine.get("access_log"), list) else []
        extra = [row for row in rows if isinstance(row, dict) and row not in theirs]
        keep_description = mine.get("description") and not incoming.get("description")
        if extra:
            incoming["access_log"] = sorted(theirs + extra, key=lambda row: str(row.get("ts", "")) if isinstance(row, dict) else "")
        if keep_description:
            incoming["description"] = mine["description"]
        if extra or keep_description:
            _write_project(incoming_dir, incoming)
    return len(extra)


def set_description(project_dir, text):
    """Store the project's free-text description, as the launcher's card does.

    Parameters:
        project_dir: the run_* folder.
        text: the description.
    Returns:
        str: the stored text, with surrounding space removed.
    """
    with _file_lock:
        data = read_project(project_dir)
        data["description"] = str(text or "").strip()
        data["updated_at"] = now_ast()
        _write_project(project_dir, data)
    return data["description"]


def target_codes(project):
    """Return the project's target label codes, in Step 3's order.

    Parameters:
        project: the project record.
    Returns:
        list: the codes. VEGA stores them as "PA" or "OFRA, PA, SS", and older
        projects as a list.
    """
    config = ((project.get("steps") or {}).get("3") or {}).get("config") or {}
    raw = config.get("target_species") or ""
    parts = raw if isinstance(raw, list) else str(raw).split(",")
    return [str(part).strip() for part in parts if str(part).strip()]


def read_label_names(project_dir):
    """Read the project's code to full name table.

    Parameters:
        project_dir: the run_* folder.
    Returns:
        dict: {code: full name}; {} when the table is absent or unreadable.
    """
    path = Path(project_dir) / MASTER_CODES_REL
    names = {}
    try:
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                code = (row.get("species_code") or row.get("code") or "").strip()
                name = (row.get("species_name") or row.get("name") or "").strip()
                if code and name:
                    names[code] = name
    except (OSError, UnicodeDecodeError, csv.Error):
        return {}
    return names


def _read_json_cached(path):
    """Read a JSON object file, reusing the last parse while the file is unchanged.

    Parameters:
        path: the file.
    Returns:
        dict: the object; {} when the file does not exist.
    Raises:
        ValueError: when the file exists and is malformed.
    """
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    cached = _json_cache.get(str(path))
    if cached and cached[0] == key:
        return cached[1]
    data = read_json_object(path)
    _json_cache[str(path)] = (key, data)
    return data


def _read_store(project_dir):
    """Read segmentations.json, reusing the last parse while the file is unchanged.

    Returns:
        dict: the frame store; {} when the file does not exist yet.
    Raises:
        ValueError: when the file exists and is malformed.
    """
    return _read_json_cached(Path(project_dir) / SEGMENTATIONS_REL)


def _queued_import_frames(project_dir, store):
    """Count imported images VEGA has not yet added to the store.

    VEGA's annotator adds every entry of custom_imports/imports_manifest.json that the store
    lacks, when its image is on disk, as it opens (_merge_custom_imports). An import that
    stopped before its frames were saved leaves such entries.

    Parameters:
        project_dir: the run_* folder.
        store: the frame store.
    Returns:
        int: how many frames the annotator will add.
    """
    try:
        entries = _read_json_cached(Path(project_dir) / IMPORTS_MANIFEST_REL).get("imports")
    except ValueError:
        return 0
    export_dir = Path(project_dir) / STEP4_DIR
    count = 0
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        name, delivered = entry.get("filename"), entry.get("delivered_file")
        if isinstance(name, str) and name and name not in store and isinstance(delivered, str) \
                and delivered and (export_dir / delivered).is_file():
            count += 1
    return count


def _queued_prompt_frames(project_dir, store, codes):
    """Count routed frames VEGA has not yet added to the store.

    A project the annotator has never opened holds its frames only in
    sam_click_prompts.json. The annotator takes those with a target point.
    """
    path = Path(project_dir) / PROMPTS_REL
    try:
        prompts = read_json_object(path)
    except ValueError:
        return 0
    count = 0
    for name, entry in prompts.items():
        if name in store or not isinstance(entry, dict):
            continue
        for point in entry.get("points") or []:
            if isinstance(point, dict) and (point.get("species") or point.get("species_code")) in codes:
                count += 1
                break
    return count


def count_work(project_dir, project=None):
    """Count a project's frames and masks, as the launcher's cards do.

    Parameters:
        project_dir: the run_* folder.
        project: the project record, when the caller already read it.
    Returns:
        dict: frames_total, frames_exported, frames_scrapped, frames_left,
        n_masks and n_images (accepted masks and the frames holding them),
        species ({code or label: accepted masks}), and reviewers (sorted initials).
        Frames the annotator adds as it opens count as left: routed frames with a
        target point, and imported images the store lacks.
    Raises:
        ValueError: when segmentations.json is malformed.
    """
    project = project if project is not None else read_project(project_dir)
    store = _read_store(project_dir)
    exported = scrapped = left = n_masks = 0
    images = set()
    species = {}
    reviewers = set()
    for name, frame in store.items():
        if not isinstance(frame, dict):
            continue
        if frame.get("scrapped"):
            scrapped += 1
        elif frame.get("exported"):
            exported += 1
        else:
            left += 1
        for mask in frame.get("masks") or []:
            if not isinstance(mask, dict) or mask.get("status") != "accepted":
                continue
            n_masks += 1
            images.add(name)
            code = str(mask.get("species") or "").strip()
            if code:
                species[code] = species.get(code, 0) + 1
            who = str(mask.get("reviewer") or "").strip().upper()
            if who:
                reviewers.add(who)
    queued = (_queued_prompt_frames(project_dir, store, set(target_codes(project)))
              + _queued_import_frames(project_dir, store))
    return {"frames_total": exported + scrapped + left + queued, "frames_exported": exported,
            "frames_scrapped": scrapped, "frames_left": left + queued, "n_masks": n_masks,
            "n_images": len(images), "species": species, "reviewers": sorted(reviewers)}


def stages_for(project):
    """Return the project's stages for the card's dots.

    Parameters:
        project: the project record.
    Returns:
        list: one {key, name, status} per stage in CARD_STAGE_KEYS.
    """
    steps = project.get("steps") or {}
    out = []
    for key in CARD_STAGE_KEYS:
        step = steps.get(key)
        if isinstance(step, dict):
            out.append({"key": key, "name": step.get("name") or f"stage {key}",
                        "status": step.get("status") or "locked"})
    return out


def describe(project_dir):
    """Build the card data for one project folder.

    Parameters:
        project_dir: the run_* folder, on this computer or on the server.
    Returns:
        dict: the fields the home screen shows, with origin_folder naming the folder a
        project made here came from ("" for VEGA's projects). A project that cannot be
        read carries "error" with the reason and only its folder name besides.
    """
    project_dir = Path(project_dir)
    base = {"run": project_dir.name}
    try:
        project = read_project(project_dir)
        work = count_work(project_dir, project)
    except ValueError as exc:
        return {**base, "error": str(exc)}
    stages = stages_for(project)
    names = read_label_names(project_dir)
    codes = target_codes(project)
    log = project.get("access_log") if isinstance(project.get("access_log"), list) else []
    origin = project.get("origin") if isinstance(project.get("origin"), dict) else {}
    return {**base, **work,
            "project_id": project.get("id") or project_dir.name,
            "project_name": project.get("name") or project_dir.name,
            "description": project.get("description") or "",
            "created_at": project.get("created_at") or "",
            "updated_at": project.get("updated_at") or "",
            "stages": stages,
            "completed_count": sum(1 for stage in stages if stage["status"] == "completed"),
            "total_steps": len(stages),
            "target_labels": [{"code": code, "name": names.get(code, "")} for code in codes],
            "label_names": names,
            "last_access": log[-1] if log else None,
            "origin_folder": str(origin.get("folder") or "") if origin.get("kind") == "folder" else "",
            "steps": {key: ((project.get("steps") or {}).get(key) or {}).get("status", "locked")
                      for key, _name in RAIL_STAGES if key != "step4test"}}


def reviewer_for(project):
    """Return who the annotator credits, by VEGA's rule: the last access log initials.

    Parameters:
        project: the project record.
    Returns:
        str: upper-case initials, or "" when none is recorded.
    """
    latest = ""
    for entry in project.get("access_log") or []:
        if isinstance(entry, dict) and str(entry.get("initials") or "").strip():
            latest = str(entry["initials"]).strip()
    return (latest or str(project.get("initials") or "")).upper()


def annotator_environment(project_dir, plan, home_url, model_paths, batch_size=None,
                          target_only=True, reference=True):
    """Build the settings VEGA's launcher hands its annotator.

    This follows _launch_step4test_ui in VEGA's pipeline_orchestrator/app.py: the
    project's identity and paths, its target labels, and its Step 5 SAM3 settings.
    The devices come from the plan for this computer.

    A project made from a folder carries its own labels: they become the target labels,
    the session keeps every frame (its images carry no survey points to select by), the
    label table is the project's own, and VEGA_ANNOTATOR_LABEL_SET tells the page's
    extras that the labels are custom words.

    Parameters:
        project_dir: the working copy's run_* folder.
        plan: a device.DevicePlan.
        home_url: the home screen's address, where the annotator's Done button goes.
        model_paths: a dict with hf_home and cache_dir.
        batch_size: "5", "10", "20" or "all"; None uses the project's setting.
        target_only: limit the session to frames carrying a target label. A project
            made from a folder always keeps every frame.
        reference: show the survey points as a read-only guide.
    Returns:
        dict: environment variables, all strings.
    Raises:
        ValueError: when the project record cannot be read or has no target labels.
    """
    project_dir = Path(project_dir)
    project = read_project(project_dir)
    codes = target_codes(project)
    if not codes:
        raise ValueError(f"{project_dir.name} has no target labels in its Step 3 settings.")
    custom = has_custom_labels(project)
    export_dir = project_dir / STEP4_DIR
    step5 = ((project.get("steps") or {}).get("5") or {}).get("config") or {}
    env = {
        # Forward slashes keep VEGA's page able to read the project's name out
        # of this path on Windows too, and Python accepts them there.
        "TCRMP_INPUT_DIR": (export_dir / "routed_input").as_posix(),
        "TCRMP_EXPORT_DIR": export_dir.as_posix(),
        "TCRMP_PROJECT_ID": str(project.get("id") or project_dir.name),
        "TCRMP_PROJECT_NAME": str(project.get("name") or project_dir.name),
        "TCRMP_TARGET_SPECIES": ",".join(codes),
        "TCRMP_TARGET_SPECIES_ONLY": "1" if target_only and not custom else "0",
        "TCRMP_REFERENCE_DEFAULT": "1" if reference else "0",
        "TCRMP_REVIEW_BATCH_SIZE": str(batch_size or step5.get("review_batch_size") or DEFAULT_BATCH_SIZE),
        "TCRMP_SAM3_DEVICE_TRACKER": plan.tracker,
        "TCRMP_SAM3_DEVICE_EXEMPLAR": plan.exemplar,
        "TCRMP_SYMLINK_IMAGES": "1",
        "TCRMP_MANUAL_ANNOTATE": "1",
        "TCRMP_PROVENANCE_SOURCE": "step4test",
        "TCRMP_REVIEWER": reviewer_for(project),
        "TCRMP_MASK_REGISTRY_DIR": str(export_dir / "vega_annotator_registry"),
        "TCRMP_ORCHESTRATOR_URL": home_url,
        "TCRMP_REVIEW_GIT_PUSH": "0",
        "TCRMP_EXEMPLAR_WARMUP": "1" if plan.warm_exemplar else "0",
        "HF_HOME": str(model_paths["hf_home"]),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TCRMP_SAM3_CACHE_DIR": str(model_paths["cache_dir"]),
        "PYTHONUTF8": "1",
    }
    for key, name in (("confidence_threshold", "TCRMP_CONFIDENCE_THRESHOLD"),
                      ("min_mask_area_px", "TCRMP_MIN_MASK_AREA_PX"),
                      ("merge_distance_px", "TCRMP_MERGE_DISTANCE_PX"),
                      ("overlap_strategy", "TCRMP_OVERLAP_STRATEGY"),
                      ("thin_mask_ratio", "TCRMP_THIN_MASK_RATIO"),
                      ("polygon_simplify_epsilon", "TCRMP_POLYGON_SIMPLIFY_EPSILON")):
        value = step5.get(key)
        if value not in (None, ""):
            env[name] = str(value)
    codes_file = project_dir / (CUSTOM_CODES_REL if custom else MASTER_CODES_REL)
    if codes_file.is_file():
        env["TCRMP_MASTER_CODES"] = str(codes_file)
    if custom:
        env[LABEL_SET_ENV] = CUSTOM_LABEL_SET
    return env
