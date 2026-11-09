# VEGA Annotator 1.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let anyone with a Mac start a VEGA project from any folder of images, label it with words they define, install SAM3 in the same setup run as Python, and hand the result to VEGA as a training source.

**Architecture:** Three new app modules (`labels.py`, `folder_projects.py`, `package.py`) and a SAM3 downloader (`sam3_download.py`) sit beside the existing app. Folder projects are ordinary `run_*` folders whose images come in through VEGA's own vendored `custom_imports` code. The home server gains routes and the home and project pages gain controls. Setup gains a fourth step that runs the downloader with the Python it just installed.

**Tech Stack:** Python 3.12, Flask, Pillow, PyYAML, bash, macOS `sips`, `osascript`, `pkgbuild` and `productbuild`; pytest in the repo's `.venv`.

**Spec:** `docs/specs/2026-11-09-vega-annotator-folder-projects-design.md` (approved by Lauren, 9 Nov 2026).

## Global Constraints

- Mac only (Apple chip). Windows code stays as it is and is not tested.
- Files under `vendor/` never change; `tests/test_vendor_parity.py` must stay green. Page changes go in `vega_annotator/static/annotator_extras.js` and `.css`.
- The SAM3 release is tag `sam3-weights-1` on laurenkolinger/vega-annotator. Parts: `sam3-hf.tar.part1` (1887436800 bytes, sha256 `5b8694330567d3b8553eb0ee263ecf8d2fa4c9829e164e15f78976124bdf220c`) and `sam3-hf.tar.part2` (1557596160 bytes, sha256 `3763349eef5f27919f5ec0c48a1af117939c5c5349ca3257e4c3a97d9b16a794`); `SAM_LICENSE.txt` 7353 bytes, sha256 `4dea99bfaa016e21bc860d73f344236bd1e5c4977d1a9a8fd32f822b500ae1be`. The parts join into one POSIX tar holding `SAM_LICENSE.txt`, `checksums.json` and `models--facebook--sam3/` (snapshot commit `3c879f39826c281e95690f02c7821c4de09afae7`).
- Download URL form: `https://github.com/laurenkolinger/vega-annotator/releases/download/sam3-weights-1/<name>`.
- Free space before the SAM3 step: 7.5 GB (`7_500_000_000` bytes).
- Label rules exactly as the spec's Part 3, including the reserved words and the YAML round trip.
- Folder project id: `run_<YY-MM-DD>_<INI>_<slug>`; slug from the name, lowercase ASCII letters, digits and `_`; the id fits `^[A-Za-z0-9._-]{1,64}$`; a clash takes `_2` to `_99`.
- Package name: `<project id without run_>_<YYYYMMDD-HHMM>`, cut to 64 characters, matching `^[A-Za-z0-9._-]{1,64}$`.
- On-screen words: NEW PROJECT, ADD IMAGES, LABELS, EXPORT FOR VEGA, DOWNLOAD SAM3, INSTALL FROM SERVER FOLDER. Every control has a tooltip that says what it does. No em dashes in any text.
- Nobody commits, tags or pushes except the coordinator at integration.
- Test command: `.venv/bin/python -m pytest tests -q`.

## Review Focus

1. **An image folder on an external or network drive that disappears mid-import.** Expect the import to stop with a plain sentence and leave the project openable, with the frames already imported kept. Test: `test_add_images_folder_vanishes_midway` (Task 3).
2. **The same photo name in two subfolders** (`healthy/IMG_1.jpg`, `bleached/IMG_1.jpg`). Expect two frames, never one overwriting the other. Test: `test_scan_same_name_in_two_subfolders_gives_two_frames` (Task 3).
3. **A label typed with odd spacing or case** ("  Partly   bleached ", "BLEACHED" next to "bleached"). Expect spacing tidied, and a refusal that names the clash. Test: `test_labels_tidy_spaces_and_refuse_case_clash` (Task 2).
4. **The Mac sleeps or Wi-Fi drops during the 3.4 GB download.** Expect the next DOWNLOAD SAM3 to resume, not restart. Test: `test_download_resumes_a_cut_part` (Task 1).
5. **EXPORT FOR VEGA to a USB drive that fills up.** Expect a refusal before writing when space is short, and no half-written package with a final name if it fills anyway. Test: `test_package_never_leaves_a_half_written_folder` (Task 5).

---

### Task 1: SAM3 downloads in setup, and on request

**Files:**
- Create: `vega_annotator/sam3_download.py`
- Create: `vega_annotator/sam3_release.json` (the pins in Global Constraints)
- Create: `packaging/mac/resources/SAM_LICENSE.txt` (Meta's license, the same bytes as the release's)
- Modify: `packaging/mac/setup.sh` (Step 4 of 4)
- Modify: `packaging/mac/launch.sh` (dialog text; Step 4 alone for a 1.0 Mac)
- Modify: `packaging/mac/build_pkg.sh` (license page in `distribution.xml`)
- Modify: `vega_annotator/sam3_files.py` (factor out `verify_copy`)
- Test: `tests/test_sam3_download.py`, `tests/test_packaging.py`

**Interfaces:**
- Produces:
  - `sam3_download.DownloadError(Exception)`, whose `str()` is a plain sentence for the person.
  - `sam3_download.load_release(path=None) -> dict`: `{"tag", "base_url", "parts": [{"name", "size", "sha256"}], "license": {...}, "snapshot_commit"}`.
  - `sam3_download.download(progress=None, release=None, work_dir=None, opener=None) -> dict`: returns `{"installed": True, "size_gb": float}`. `progress(done_bytes, total_bytes, message)`; `opener(url, start_byte)` returns a readable response and exists for tests.
  - `sam3_download.failed_marker() -> Path`: `paths.app_data_dir() / "sam3_download_failed.json"`.
  - `sam3_download.main(argv=None) -> int`: `--setup` prints step lines for setup.sh; exits 0 on success and 1 on failure, after writing the marker.
  - `sam3_files.verify_copy(folder: Path) -> None`: raises `TransferError` when any file in `folder/checksums.json` is missing or differs.
- Consumes: `paths.sam3_home()`, `paths.app_data_dir()`, `sam3_files.installed_snapshot()`.

- [ ] **Step 1: Write the failing tests.** In `tests/test_sam3_download.py`, a local `http.server` thread serves two fake parts cut from a small tar built in the test in the release layout, with pins made for them, and supports `Range` requests. Tests:
  - `test_download_installs_and_removes_parts`: after `download()`, `sam3_files.installed_snapshot()` is not None, and the work folder holds no `.part` files.
  - `test_download_refuses_a_corrupt_part`: one flipped byte, so `DownloadError` names the part; nothing is installed; the bad part is deleted.
  - `test_download_refuses_a_short_part`: a truncated file, so `DownloadError`.
  - `test_download_resumes_a_cut_part`: the server drops the connection after N bytes on the first try; the second `download()` sends `Range: bytes=N-`, and its total transferred bytes are less than the part size.
  - `test_download_refuses_without_space`: `shutil.disk_usage` patched below 7.5 GB, so `DownloadError` says how much is needed, and no request is made.
  - `test_download_refuses_a_tar_that_escapes`: a member named `../x`, so `DownloadError`, and nothing is written outside the unpack folder.
  - `test_main_setup_writes_marker_on_failure`: `main(["--setup"])` returns 1, the marker holds the reason, and a later success removes it.
  - `test_release_pins_match_the_published_release`: `load_release()` equals the Global Constraints values.
- [ ] **Step 2: Run the tests and see them fail.** Run `.venv/bin/python -m pytest tests/test_sam3_download.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement `sam3_download.py`.**
  - Download each part with `Range` resume into `work_dir` (default `paths.app_data_dir()/downloads/sam3`), check its size and SHA-256 against the pins, and delete a part that fails.
  - Stream both parts as one file into `tarfile.open(mode="r|")` with `filter="data"`, unpacking into `paths.sam3_home().parent / f".sam3-unpack-{uuid}"`.
  - Run `sam3_files.verify_copy`, then replace `paths.sam3_home()` with the unpacked folder in one `os.replace` (keep the old copy aside until the replace succeeds).
  - Delete the parts and the marker.
- [ ] **Step 4: Run the tests and see them pass.** Same command. Expected: PASS.
- [ ] **Step 5: Wire setup and the launcher.**
  - `setup.sh` gains `step "Step 4 of 4: downloading SAM3 (3.4 GB)"`, running `"$RUNTIME/bin/python" -m vega_annotator.sam3_download --setup` from `$HERE/app`. A failure is logged and does not fail setup.
  - In `launch.sh`, the first dialog says "Python, PyTorch and SAM3, about 4.5 GB". When the runtime is ready, SAM3 is missing and the marker is absent, it asks once and runs only Step 4 (`setup.sh --sam3-only`).
  - `build_pkg.sh` adds `<license file="SAM_LICENSE.txt"/>` to `distribution.xml`.
  - Tests in `tests/test_packaging.py`:
    - `test_setup_has_step_four_and_tolerates_its_failure`;
    - `test_launch_dialog_names_sam3`;
    - `test_pkg_shows_the_sam_license`: build the package, expand it with `pkgutil --expand`, and find the license in `Distribution`.
- [ ] **Step 6: Run the full suite.** Run `.venv/bin/python -m pytest tests -q`. Expected: all pass.

### Task 2: Labels a person defines

**Files:**
- Create: `vega_annotator/labels.py`
- Test: `tests/test_labels.py`

**Interfaces:**
- Produces:
  - `labels.LabelError(Exception)`, with a plain sentence.
  - `labels.tidy(text: str) -> str`: trim, and collapse runs of spaces.
  - `labels.check(text: str, existing: list[str]) -> str`: returns the tidied label or raises.
  - `labels.parse(value) -> list[str]`: accepts a list, or text split on new lines and commas; checks every label; refuses an empty result.
  - `labels.write(project_dir: Path, names: list[str]) -> None`: writes `project.json` `label_set` and `steps.3.config.target_species`, `step4test_combinedAnnotate/master_codes_recoded.csv` (`species_code,species_name,category` = label, label, `Label`), and `step4test_combinedAnnotate/class_map.json`, adding new labels at the end and never renumbering.
  - `labels.exported(project_dir: Path) -> bool`: True once `export_manifest.csv` has a row.
  - `labels.add(project_dir, names) -> list[str]`
  - `labels.rename(project_dir, old, new) -> list[str]`
  - `labels.remove(project_dir, name) -> list[str]`

  `rename` and `remove` raise after the first export, or when a mask in the store uses the label.
- Consumes: `projects.read_project`, `projects._write_project`.

- [ ] **Step 1: Write the failing tests.**
  - `test_check_accepts_condition_words` ("healthy", "Partly bleached", "SCTLD (lesion)", "stage 2").
  - `test_check_refuses_each_rule`, parametrized, each case checking that the message names the problem:
    - an empty label, and one of 41 characters;
    - "123", and "-x" (the first character is not a letter or digit);
    - characters outside the allowed set: a comma, an apostrophe, "<b>", a colon, a slash;
    - the reserved words "review", "REVIEW", "unk", "outcome", "image_path";
    - words YAML reads as something else: "yes", "No", "null", "0x1F", "1.5e+3".
  - `test_labels_tidy_spaces_and_refuse_case_clash`.
  - `test_parse_splits_lines_and_commas`.
  - `test_write_seeds_class_map_in_order_and_appends_later`.
  - `test_write_makes_master_codes_with_category_Label`.
  - `test_rename_and_remove_refused_after_first_export`.
  - `test_remove_refused_while_a_mask_uses_it`.
- [ ] **Step 2: Run the tests and see them fail.** Run `.venv/bin/python -m pytest tests/test_labels.py -q`.
- [ ] **Step 3: Implement `labels.py`.** The YAML check is `yaml.safe_load("- " + label) == [label]`. The allowed characters are Unicode letters, digits, spaces and `-_.+()`.
- [ ] **Step 4: Run the tests and see them pass.** Same command. Expected: PASS.

### Task 3: Projects from a folder of images

**Files:**
- Create: `vega_annotator/folder_projects.py`
- Modify: `vega_annotator/catalog.py` (the `made_here` state)
- Modify: `vega_annotator/projects.py` (`annotator_environment` and `count_work` for a custom label set)
- Test: `tests/test_folder_projects.py`, `tests/test_catalog.py`, `tests/test_projects.py`

**Interfaces:**
- Consumes: Task 2's `labels.parse`, `labels.write`; VEGA's vendored `custom_imports.ingest_files`, `seg_dict_for`, `record_in_project` (load them the way `annotator_boot.py` puts VEGA's scripts on `sys.path`).
- Produces:
  - `folder_projects.FolderError(Exception)`
  - `folder_projects.scan(folder: Path) -> dict`, giving:
    - `images`: `[Path]`;
    - `convert`: `[Path]` (`.heic .heif .tif .tiff .bmp .webp .gif`);
    - `skipped`: `[{"path", "reason"}]`;
    - `bytes`: `int`.

    It walks subfolders without following links and skips hidden files.
  - `folder_projects.new_project_id(name, initials, root, today=None) -> str`
  - `folder_projects.create(folder, name, label_text, initials, purpose, root=None, progress=None) -> dict`: returns `{"run", "added", "skipped"}`.
  - `folder_projects.add_images(project_dir, folder, initials, progress=None) -> dict`: returns `{"added", "skipped"}`.
  - `folder_projects.is_folder_project(project) -> bool`: true when `label_set.kind == "custom"`.
  - The local state file `vega_annotator_local.json` gets `{"kind": "made_here", "created_at", "computer"}`.
  - The catalog state `made_here` allows OPEN, ADD IMAGES, LABELS, EXPORT FOR VEGA and REMOVE, and never Bring In or Put Back.

- [ ] **Step 1: Write the failing tests.**
  - `test_scan_finds_images_in_subfolders_and_skips_hidden_and_links`
  - `test_scan_same_name_in_two_subfolders_gives_two_frames` (checked after `create`)
  - `test_scan_lists_unreadable_and_zero_byte_files_as_skipped`
  - `test_new_project_id_slug_and_clash_suffix`
  - `test_create_writes_project_skeleton`: `project.json` fields per the spec, `sam_click_prompts.json == {}`, the labels written, and the local state `made_here`.
  - `test_create_imports_through_vega_custom_imports`: `custom_imports/raw/custom_*.jpg` exist; the store has one frame per image with `custom_import` true and empty `reference_points`; `project.json` lists them.
  - `test_create_converts_heic_and_tiff_with_sips`, marked to skip off macOS. Make a TIFF with Pillow and a HEIC with `sips`.
  - `test_create_refuses_without_space`
  - `test_add_images_refused_while_annotator_running`
  - `test_add_images_folder_vanishes_midway`
  - `test_catalog_shows_made_here_card_with_its_buttons`
  - `test_environment_for_folder_project`: `TCRMP_TARGET_SPECIES` is the labels joined by commas, `TCRMP_TARGET_SPECIES_ONLY` is "0", `TCRMP_MASTER_CODES` points at the project's CSV, and `VEGA_ANNOTATOR_LABEL_SET` is "custom".
- [ ] **Step 2: Run the tests and see them fail.**
- [ ] **Step 3: Implement.**
  - The space check needs the scan's bytes times 2, plus 1 GB.
  - Converted copies go to a temporary folder, through `sips -s format png`.
  - New frames are merged into `segmentations.json` with VEGA's store format: `json.dump(indent=1)`, a temporary file, then `os.replace`, mode 0664.
- [ ] **Step 4: Run the tests and see them pass.**

### Task 4: Home screen, project page and annotator page

**Files:**
- Modify: `vega_annotator/home.py`
  - `native_folder_prompt(prompt=...)`;
  - new routes `POST /api/folder/choose`, `POST /api/projects/new`, `POST /api/projects/<run>/add-images`, `GET`/`POST /api/projects/<run>/labels`, `POST /api/projects/<run>/export-for-vega` and `POST /api/sam3/download`;
  - every long action runs as a `jobs.start(...)` job, like `api_sam3_install`.
- Modify: `vega_annotator/static/home.js`, `vega_annotator/templates/home.html`: the NEW PROJECT button and dialog, the made-here card buttons, and the SAM3 status line with DOWNLOAD SAM3 and INSTALL FROM SERVER FOLDER.
- Modify: `vega_annotator/static/project.js`, `vega_annotator/templates/project.html`: the LABELS panel, ADD IMAGES, EXPORT FOR VEGA.
- Modify: `vega_annotator/annotator_boot.py`: set `window.VEGA_ANNOTATOR = {labelSet: ...}` before `annotator_extras.js`.
- Modify: `vega_annotator/static/annotator_extras.js`, `.css`: when `labelSet == "custom"`, hide the Sponge, Macroalgae, Gorgonian and Turf chips, block the keys s, m, g and t in the capture phase, and set label text to `font-style: normal`.
- Test: `tests/test_home.py`, `tests/test_annotator_boot.py`, `tests/test_pixels.py`

**Interfaces:**
- Consumes:
  - Task 1's `sam3_download.download`;
  - Task 2's `labels.*`;
  - Task 3's `folder_projects.*`;
  - Task 5's `package.export_for_vega`.

  Tests stub all of these.
- Produces: the routes above, each returning `{"ok": true, "job": id}` or `{"ok": false, "error": sentence}`.

- [ ] **Step 1: Write the failing tests.**
  - One route test per route: success, refusal, and a bad request.
  - `test_folder_choose_uses_images_prompt`
  - `test_labels_route_refuses_while_open`
  - `test_boot_page_carries_label_set_flag`
  - Pixel tests at 946 and 1280 px: the home screen with a made-here card, the NEW PROJECT dialog, the LABELS panel, the EXPORT FOR VEGA dialog, and the annotator page with custom labels. Read each against the seven questions.
- [ ] **Step 2: Run the tests and see them fail.**
- [ ] **Step 3: Implement.** Follow the existing card and button patterns in `home.js`: `canOpen`, `adoptRunningJobs`, focus return, tooltips.
- [ ] **Step 4: Run the tests and see them pass**, and read the screenshots.

### Task 5: EXPORT FOR VEGA

**Files:**
- Create: `vega_annotator/package.py`
- Test: `tests/test_package.py`

**Interfaces:**
- Produces:
  - `package.PackageError(Exception)`
  - `package.package_name(project_dir, now=None) -> str`
  - `package.export_for_vega(project_dir, destination, include_originals=False, progress=None, now=None) -> dict`: returns `{"folder", "frames", "masks", "bytes"}`.
- Consumes: `transfer.copy_verified` for file copies with a hash.

- [ ] **Step 1: Write the failing tests.**
  - `test_package_has_session_shape`:
    - `data.yaml` names equal the labels in class order;
    - `all_images/` holds regular files, one per exported row, matching `all_labels/`;
    - `session.json` keys per the spec;
    - `checksums.json` covers every file;
    - `README.txt` has the three sentences from the spec.
  - `test_package_refuses_with_nothing_exported`
  - `test_package_excludes_originals_unless_asked`
  - `test_package_name_rule`: the regex, the 64-character cut, and two exports a minute apart giving two folders.
  - `test_package_never_leaves_a_half_written_folder`: an injected `OSError` mid-copy leaves no `<name>` folder, only a removed `.partial`.
  - `test_package_refused_while_annotator_running`
  - `test_package_refuses_without_space`
- [ ] **Step 2: Run the tests and see them fail.**
- [ ] **Step 3: Implement.**
  - Write into `<destination>/.<name>.partial`, then rename.
  - Resolve each `all_images` link to its file and copy the file.
  - Copy `segmentations/`, `previews/` and `custom_imports/` (with `originals/` only on request), plus the export files the spec lists.
- [ ] **Step 4: Run the tests and see them pass.**

### Task 6: TRAIN ON THIS handed to the mainframe (coordinator)

- [ ] Write the handoff to `/home/bizon/Desktop/vega-train-on-this-handoff-2026-11-09.md` on vicar-mainframe: the spec's "VEGA side" items with their code references, the four notes, and the rules from `/mnt/rip/vicarius_drive/CLAUDE.md`.
- [ ] POST a VEGA to-do on the mainframe whose body starts with the Desktop path.
- [ ] Add a PKM pointer note and a Handoffs line, per Lauren's handoff rules. Add no start card for the Mac.

### Task 7: Integration, the VEGA-side check, review and release (coordinator)

- [ ] **Integration on this Mac with real SAM3.**
  - Install SAM3 through `sam3_download.download()` from the live release, which is the real download test.
  - Run the spec's integration scenario through VEGA's own annotator routes.
- [ ] **VEGA-side check on vicar-mainframe**, in `/mnt/rip/chain_build/vega_folder_projects_check/`, removed afterward:
  - VEGA's `_list_standalone_exports` lists the package with its labels;
  - `pinned_split.py` makes a fresh split from it.
- [ ] **Review.** One independent reviewer on the whole branch, then one re-review after fixes.
- [ ] **Release.**
  - Set `VERSION = "1.1.0"`.
  - Rewrite the README and `docs/START_HERE.txt`.
  - Make `.github/workflows/windows.yml` run on demand only.
  - Build the package; commit; tag `v1.1.0`; push; make the GitHub release with the Mac package.
  - Publish to Alexandria's folder with `tools/publish_to_server.sh`.
