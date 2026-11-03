# VEGA project Export and Import, 2 Nov 2026

## Why

A student labels frames in VEGA, and VEGA's machine is often busy. VEGA Annotator runs the same annotator on a laptop. VEGA now needs two plain controls on each project card to send a project out and take the work back: **Export project** and **Import project**. An administrator defines where projects go in an admin control panel on the same page.

The words on screen are "Export project" and "Import project", everywhere.

## Status

| Part | Status |
|---|---|
| Admin control panel, export location | This spec, built in the first batch |
| Export project, with size on the card | This spec, built in the first batch |
| Import project | This spec, built after Lauren approves this document |
| Warning when a project that is out gets opened in VEGA | This spec, built with Import project |

## Goals

- A person makes a project in VEGA, clicks **Export project** on its card, and later clicks **Import project** on the same card. No command line.
- Every project card shows the project's size before anything is copied.
- An export never changes the project in VEGA.
- An import never loses work from either side, and every file it replaces stays recoverable.
- After an import, Train Model, the Label Coverage Matrix, STATS, and Screened all see the returned frames as if VEGA's own annotator made them.

## Non-goals

- Making projects on a laptop, training on a laptop, and Refine or Edit Masks on a laptop.
- Merging two people's edits to the same frame. VEGA keeps its version and sets the other aside for a person to decide.
- Moving images out of the TCRMP archive. A project carries its own frame copies.

## Where it lives

Both controls sit on each project card in VEGA's page in VICARIUS, in the card's foot beside **OPEN**. The admin control panel sits above the project list on the same page. It shows only while the desktop is in admin mode, the way the Bounding Boxes button does today.

The controls appear only for a module whose `module.yaml` declares them, so no other module's cards change:

```yaml
ui:
  runs:
    project_transfer:
      script: "scripts/export_project.py"
```

## Admin control panel

The panel holds one field, **Export and import location**: a folder path as the mainframe sees it. Lauren defines it there.

- The location can be anything the mainframe can read and write: a folder a server sync carries, a mounted network share, or an attached external drive.
- A laptop itself counts only if its folder is mounted on the mainframe. The usual route is a server folder that the laptop also connects to.
- VICARIUS adds `projects/` under the location and keeps each project at `<location>/projects/<run>/`.
- Saving requires admin mode, and initials and a purpose. The route answers 403 without admin mode, as the module lock toggle does.
- Saving resolves the path first, settling `..`, a leading `//`, and symbolic links. It then checks that the path is a folder that exists or can be made, and can be written. It refuses a path inside VICARIUS's modules folder or inside an archive root from `nas.yaml`, and a mounted path that names an archive share, such as gvfs's `share=archive6_16tb`.
- The panel shows the location's free space beside the field.
- The location lives in `vicarius_ui_os/config/project_transfer.json`, written atomically, with who set it and when. Each change writes a row to the processing log.
- Projects already exported stay where they went. Changing the location does not move them, and their cards read the new location from then on.

**My call, for Lauren to check:** one location, shared by everyone. A second student with a second folder would need a list here, and the file format leaves room for one.

## What the card shows

**Size.** Each card shows what an export would copy, such as `0.90 GB to export`. VICARIUS computes it with the export tool's own file list. It keeps the number until a counted folder changes or the module rewrites `project.json`, the store, or the manifest. Training projects show no size and no Export project, because they never travel.

**State and controls.**

| State at the location | Chip | Export project | Import project |
|---|---|---|---|
| No location defined | none | Off: "An administrator sets the location" | Hidden |
| Location not reachable, or stops answering within 3 s | "Location not reachable" | Off | Off |
| Project not ready to export | none | Off: says why, such as "Open Place + Segment once first" | Hidden |
| No copy there | none | On | Hidden |
| Copy there, nobody has it | "Exported" | Off | Hidden |
| Copy held by a computer | "Out on Legion" | Off | Off |
| A computer stopped partway through putting it back | "Out on Legion" | Off | Off: says which computer must finish |
| Work put back, not imported yet | "Work to import" | Off | On |
| Imported, and nothing newer | "Imported" | On, which refreshes the copy | Hidden |

A control that cannot be used shows no pointer and carries a tooltip saying why, per the workflow page design.

## Disk space

Measured on 2 Nov 2026 on all 15 of VEGA's project folders, of which 5 hold frames:

| Project | Frames | Export copies | Whole folder in VEGA |
|---|---|---|---|
| AT_PA_800img_26-10-04 | 1,356 | 0.90 GB | 1.69 GB |
| AT_PA_750img_26-09-18 | 693 | 0.48 GB | 1.12 GB |
| 18aprilpm_PA | 461 | 0.30 GB | 2.83 GB, which includes training runs an export leaves out |
| AT_2nd_test_6July2026 | 294 | 0.21 GB | 0.81 GB |
| AT_test_6July2026 | 74 | 0.06 GB | 0.35 GB |

- An export copies about 0.66 MB per frame. An 800-frame project copies about 0.53 GB.
- Labeling adds previews and overlays, 0.75 MB per exported frame on the largest project. A fully labeled 800-frame project takes about 1.1 GB on the laptop.
- A project stores each frame once. `all_images/` holds links, which an export leaves out and each computer rebuilds. On Windows a link falls back to a hard link, which takes no extra space.
- A frame in two projects is stored twice. Across VEGA's projects today, 159 of 2,713 frames sit in more than one project, which costs about 0.1 GB.
- A 1 TB drive holds about 900 fully labeled 800-frame projects, after 3.4 GB for SAM3 and up to 4 GB for the app.

The controls enforce this:

- Export project refuses when the location lacks room for the project plus a 2 GB margin.
- The laptop's **BRING IN** states the size and the free space, and refuses a project that would not fit with room to grow by 1 MB per frame.
- The laptop's **Remove** takes a project off once its work is back.

## Export project

**Click.** VICARIUS prompts for initials and purpose, as **OPEN** does. The suggested purpose names the run, and the prompt shows the size.

**Refusals, each with a sentence saying why:**

- The module is locked (423, as **OPEN** answers).
- No location is defined, or it cannot be written.
- VEGA's annotator is running on this project.
- The project has no target labels, or no routed frames.
- The location lacks room.
- Another export of this project is running.
- A copy exists there with work VEGA has not imported. This tool never replaces that.

**What it does**, through `scripts/export_project.py`, a versioned tool in VEGA's repository:

1. Writes a processing log row with the initials and purpose, and records both in the copy's history. It writes no row into the project's own `access_log`, because VEGA reads that log's last row as the person who opened the project last.
2. Copies through a staging folder, checking each file's SHA-256, and moves the folder into place only when complete.
3. Refreshes `prior_screening.json` with VEGA's own code, as a launch does.
4. Writes `checksums.json`, the size and SHA-256 of every file, and `vega_annotator.json`: `format`, `project_id`, `rev`, `source.project_dir`, `frame_hashes`, `out`, and `history`. It takes `frame_hashes` from the copy itself, so a save in VEGA during the export cannot shift the import's baseline.
5. Removes any staging folder a stopped export of the same project left behind.

**What travels:** `project.json`, `step2_recodeSpecies/` except `all_points_recoded.csv`, `step3_chooseImages/`, and `step4test_combinedAnnotate/`.

**What stays:** `all_images/` links, `previews/`, `segmentations/overlays_reviewed/`, `_superseded/`, `step1_makeAllPoints/`, and the training and inference steps.

An export of 0.90 GB took 4 s to a local disk. The button reads "Exporting" and stays off until the tool finishes, in every window, because the server reports each running export. Closing the page does not stop the tool.

## Import project

Import project takes a project's work back from the location into its folder in VEGA. It is the one operation here that writes into a live project, so it runs in stages and can stop safely at any point.

### Stage 1: check, writing nothing

VICARIUS runs `scripts/import_project.py --check` and shows the result in a dialog before anything changes:

- Frames changed on the laptop, frames exported, frames scrapped, and masks added.
- Frames changed on both sides, which VEGA keeps and sets aside.
- Folders under `conflicts/`, which computers saved beside the project.
- New images imported on the laptop.
- The space the import needs.

It refuses, with a sentence saying why, when:

- A computer still holds the copy (`out` is set), or a Put Back is unfinished (`put_back` is set).
- VEGA's annotator is running on this project, or the module is locked.
- Any file differs from `checksums.json`. A sync still in progress looks like this, so the message says to try again later.
- The record's `project_id` or `source.project_dir` does not match this project, or its `format` is newer than the tool reads.

The dialog takes initials and purpose, and its button reads **IMPORT PROJECT**.

### Stage 2: build the result beside the project

The tool builds the merged result in `<run>/.importing_<stamp>/`, leaving the project untouched.

**`segmentations.json`, frame by frame**, comparing each side's frame hash with `frame_hashes` from the export:

| Laptop | VEGA | Result |
|---|---|---|
| Unchanged | Unchanged | Keep |
| Changed | Unchanged | Take the laptop's |
| Unchanged | Changed | Keep VEGA's |
| Changed | Changed | Keep VEGA's, and set the laptop's aside in the annotator's own conflict file |
| Only on the laptop, such as an imported image | Absent | Add it |
| Absent | Only in VEGA | Keep it |

Expert review fields always stay VEGA's, as `_merge_store_with_disk` in the annotator's `app.py` keeps them. A frame set aside goes to `segmentations.conflict-<AST stamp>.json` beside the store, written through `_append_conflict_entries`, so the annotator's own conflict review reads it and `check_project_integrity.py` counts it.

**Files, for frames taken from the laptop only:**

- `all_labels/<stem>.txt`, with class ids checked against the project's `class_map.json`. A class new on the laptop gets the next free id, and its label lines are rewritten to match.
- `export_manifest.csv` rows, replaced per image, with absolute paths rewritten to this project.
- `label_provenance.csv` rows, replaced per image.
- `previews/` images, for the frames that came back with one.
- `custom_imports/` images new on the laptop, added beside the project's frames.

**Records, merged:**

- `access_log`: every row from both sides, in time order, with duplicates dropped. Each laptop row carries `"source": "laptop"`. `reviewer_for_state` in `project_manager.py` and the Matrix's `opened_by` skip rows marked so, because both read the last row as whoever opened the project in VEGA. Import adds no row of its own; the processing log records it.
- `mask_status_log.jsonl` and `export_log.txt`: lines from both sides, with duplicates dropped.
- `.export_batch_seq`: the larger of the two.
- `class_map.json` and `data.yaml`: rebuilt for this project's root.

A frame set aside as a conflict brings none of its files.

### Stage 3: move into place, with a journal

1. Writes `<run>/import_journal.json`, naming every file to replace.
2. Copies each file it will replace to `_superseded/<stamp>/before_import/`. No image is deleted (hard rule of 2026-10-15).
3. Moves each built file into place.
4. Rebuilds `all_images/` as relative links to `../routed_input/raw/` and `custom_imports/`, so it holds exactly the manifest's exported frames.
5. Updates `updated_at` and writes a processing log row with the initials and purpose.
6. Removes the journal last.

The mask registry waits for Stage 4, because `MaskRegistry.upsert` restamps rows and `prune_frame` keeps no backup.

If the computer stops partway, the next look at the card finishes the journal, the same way the laptop's Bring In does.

### Stage 4: check the result, then the registry

`check_project_integrity.py` audits every project under a root, so the tool runs it on a scratch root holding only this project, once on a copy from before the import and once after. A finding counts as new when its rule, project, and message, with the scratch root's path replaced, do not appear in the before run. On 2 Nov 2026 a hand merge of a real returned project ran this way: 14 PASS, 3 FAIL, 1 WARN, before and after, and no new findings. Its three FAILs came from the scratch root itself.

A new finding rolls the import back from `_superseded/<stamp>/before_import/`, and the card says what the finding was.

With no new finding, the tool copies `inprocess/_mask_registry/manifest.csv` to `_superseded/<stamp>/before_import/`, then replays the rows in `vega_annotator_registry/manifest.csv` with `MaskRegistry.upsert` and `prune_frame`, under the project id in `project.json`. A failure partway restores that copy of the registry.

### Stage 5: tell the location

The tool refreshes the copy at the location from VEGA's merged project, with the export tool's `--update` path. The files, `checksums.json`, and `frame_hashes` then match VEGA, so the next round starts from the merged state. It appends `{action: "vega_took_back", rev, at, by, purpose}` to `history` and raises `rev`. A laptop still holding an old copy reads "Newer on the server" and can bring in the merged state. `holds_laptop_work` in the export tool stops at this row.

The copy stays at the location until the next export replaces it, and each replaced copy moves to `_superseded/`. Removing old copies stays a person's decision, on purpose.

### A second round

After an import, **Export project** turns on again and refreshes the copy with `--update`. The laptop's next **BRING IN** copies only what changed.

## Archiving while a project is out

**Archive** refuses a project whose copy is out on a computer, or holds work to import, and says why.

## While a project is out

Once Import project is built, **OPEN** on a project marked "Out on Legion" warns before opening: "This project is out on Legion since 2026-11-02 18:27. Edits here can conflict with work there." It allows opening after a confirmation, because VEGA's copy stays usable. A frame edited on both sides then goes through the conflict rule above.

## Files

| Repository | File | Change |
|---|---|---|
| `reef_point_seg` | `scripts/export_project.py` | New, from VEGA Annotator's `tools/publish_project.py` |
| `reef_point_seg` | `scripts/import_project.py` | New, with Import project |
| `reef_point_seg` | `module.yaml` | Declares `ui.runs.project_transfer` |
| `reef_point_seg` | `tests/` | House tests for both tools |
| `vicarius-harness` | `vicarius_ui_os/project_transfer.py` | New: location store, size cache, state, tool runner |
| `vicarius-harness` | `vicarius_ui_os/module_views.py` | Routes `project-location`, `export-run`, and later `import-check` and `import-run`; size and state in `/runs` |
| `vicarius-harness` | `vicarius_ui_os/templates/module_launcher.html` | Admin control panel, card controls, size, chips, tooltips |
| `vicarius-harness` | `vicarius_ui_os/tests/test_project_transfer.py` | New |
| `vicarius-harness` | Changelog, TODO `T20261102-164635`, data dictionary, `CONTEXT.md` | Per the change batch rules |

## Tests

**Export and location, first batch:**

- Unit: size matches the tool's file list; the location store round trips; each refusal above; the project's files, `project.json` included, stay byte for byte the same after an export.
- Adversarial: a path outside the runs root; a relative or unwritable location; a location reached through `//`, `..`, or a symbolic link into the modules folder; a mounted archive share; a location that vanishes or stops answering; bad initials; a double click; a save in VEGA during the copy; a log that cannot be written; a broken `project.json`; hostile run names.
- Admin: saving without admin mode answers 403; the panel stays hidden outside admin mode.
- Integration: a real export of a scratch copy of a real project, then a Bring In with the laptop app, with checksums confirmed.
- Pixels: VEGA's page at 946 and 1280 px in each card state, against the seven questions.

**Import, second batch:**

- Unit: each row of the frame merge table; class id remapping; duplicate log rows.
- Adversarial: a stop at each stage followed by the next look; a file changed after the check; a mid-sync copy; a copy from another project; a newer record format; a registry row for a frame set aside.
- Integration, on a scratch copy under a throwaway `--root`, never a live project: export a real project, label on a Mac and on the Legion, put back, and import.
- Acceptance: the integrity checker reports no new finding; returned label files match a VEGA-native export of the same masks; the registry holds one row per mask; exported + scrapped + left = routed; Train Model builds its split from the project; a frame edited on both sides reaches the conflict file with neither version lost; VEGA's full test board stays green.

## Review

One independent reviewer per batch, with one re-review, per the build discipline.

## Calls I made, for Lauren to check

1. One location, shared by everyone.
2. Export and Import write no row into VEGA's `access_log`, which VEGA reads as who opened the project last. The processing log and the copy's history record them.
3. **Export project** stays off once a copy exists, until an import or a person's command refreshes it. This keeps a click from replacing work.
4. A frame edited on both sides keeps VEGA's version, with the laptop's saved to a conflict file.
5. Import project rolls itself back on any new integrity finding.
6. Old copies stay at the location until a person removes them.
7. **OPEN** warns on a project that is out and still allows opening.
