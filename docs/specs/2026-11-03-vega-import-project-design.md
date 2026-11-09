# VEGA Import project: design and build spec, 3 Nov 2026

> **Start here.** Build **Import project** on VEGA's project cards in VICARIUS. When a student labels a VEGA project on a laptop with VEGA Annotator and puts it back, Import project brings that work into the live VEGA project. Train Model, the Label Coverage Matrix, STATS and Choose Images' Screened rule then see it as if VEGA's own annotator had done the work on the mainframe.
>
> - **Inputs:** this file; the Export project spec and plan it builds on; the code it cites on `vicar-mainframe`; the laptop app at [github.com/laurenkolinger/vega-annotator](https://github.com/laurenkolinger/vega-annotator). Section 2 lists what to read first.
> - **Deliverable:** the import tool, the VICARIUS routes and page changes, the changes to existing VEGA, VICARIUS and VEGA Annotator code, and the tests in sections 7 to 11, plus the records in section 12.
> - **Rules:** Open every cited file and line before relying on it, because line numbers drift. Where this spec and the code disagree, stop and tell Lauren. Before writing code, put each of the eight calls in section 13 to Lauren, one question at a time. Follow `/mnt/rip/vicarius_drive/CLAUDE.md` and the plan-build-test-iterate skill: a plan file first, tests written with the code, one independent reviewer. On screen, the words are **Export project** and **Import project**, never "Send to laptop". Commit only when Lauren asks.
> - **This file:** `/mnt/rip/vicarius_drive/docs/superpowers/specs/2026-11-03-vega-import-project-design.md`. Lauren's Mac holds a copy at `/Users/laurenkay/VEGAAnnotator/docs/specs/2026-11-03-vega-import-project-design.md`. The mainframe to-do is `T20261103-182920`.

**Status.** Written 3 Nov 2026 and revised the same day after an independent builder check and a citation check. Nothing is built. The code was read on 3 Nov 2026 in the module repo at commit `288e9ca` plus its uncommitted working tree, and in VICARIUS's working tree. Export project is live in both and is not committed. The module working tree also holds other unfinished edits (`git status` lists about 20 modified files), so a builder stages files by explicit path. No export location is configured yet (`UI/config/project_transfer.json` does not exist), so no copy exists anywhere and Export project has not been used.

This spec replaces the Import section of [2026-11-02-vega-project-export-import-design.md](2026-11-02-vega-project-export-import-design.md), which stays the record for Export project.

## 0. Path shorthand

| Short | Full path on `vicar-mainframe` |
|---|---|
| `G` | `/mnt/rip/vicarius_drive/vicarius/modules/reef_point_seg/github_repo` |
| `IN` | `/mnt/rip/vicarius_drive/vicarius/modules/reef_point_seg/inprocess` |
| `<run>` | one project folder, `IN/run_<id>` when live |
| `X` | `<run>/step4test_combinedAnnotate`, the folder VEGA's code calls `export_dir` |
| `T` | `X/_transfer`, the import's small records (section 5) |
| `W` | `X/_superseded/<stamp>`, one import's working folder (section 5) |
| `A` | `G/scripts/TCRMPclip_combinedAnnotate/src/app.py` (the combined annotator) |
| `EY` | `G/scripts/TCRMPclip_combinedAnnotate/src/export_yolo.py` |
| `CI` | `G/scripts/TCRMPclip_combinedAnnotate/src/custom_imports.py` |
| `PV` | `G/scripts/TCRMPclip_combinedAnnotate/src/provenance.py` |
| `LI` | `G/scripts/_reefreview/ledger_io.py` |
| `MR` | `G/scripts/_reefreview/mask_registry.py` |
| `UID` | `G/scripts/_reefreview/uid.py` |
| `SCR` | `G/scripts/_reefreview/screening.py` |
| `SUP` | `G/scripts/_superseded.py` (backup folder name and time stamps) |
| `PM` | `G/scripts/pipeline_orchestrator/project_manager.py` |
| `APP` | `G/scripts/pipeline_orchestrator/app.py` (the orchestrator, port 5050) |
| `MB` | `G/scripts/_matrix/builder.py` |
| `CPI` | `G/scripts/check_project_integrity.py` |
| `EXP` | `G/scripts/export_project.py` (Export project's tool) |
| `RFR` | `G/scripts/return_frames_to_review.py` (VEGA's precedent for writing into a live project) |
| `UI` | `/mnt/rip/vicarius_drive/vicarius_ui_os` (VICARIUS, port 5090) |
| `MV` | `UI/module_views.py` |
| `PT` | `UI/project_transfer.py` |
| `ML` | `UI/templates/module_launcher.html` |
| `LAP` | the laptop app, [github.com/laurenkolinger/vega-annotator](https://github.com/laurenkolinger/vega-annotator), commit `0c4dadc`, release `v1.0.0` |

## 1. What Import project is for

A student labels a VEGA project on a laptop with VEGA Annotator, which runs VEGA's own annotator code. The project travels as a folder at an **export location**, which an administrator sets in the **Admin control panel** on VEGA's page. Export project puts a copy there. The laptop brings it in, labels, exports batches, and puts it back. **Import project** takes that returned work into the live project.

### Goals

1. One click on the card opens a check dialog that says exactly what will change, and one more click imports.
2. No work is lost from either side. A frame changed on both sides keeps VEGA's version and sets the laptop's aside in VEGA's own conflict file.
3. Every live file the import replaces is copied to `W/before_import/` first. No image is ever deleted.
4. A stop at any point leaves the project either untouched or finishable from a journal.
5. After an import, VEGA's integrity checker shows no new problem item, except items the laptop's own copy already carried, or the import undoes itself.
6. The import keeps its own merge base, so the next import after more laptop work merges correctly with or without a fresh export.
7. The import only reads the export location. It never writes there.

### Non-goals

- Resolving conflicts automatically. VEGA has no conflict resolution code or page today (4.3); a person decides.
- Folding an expert review made in VEGA into the laptop's version of the same frame. Such a frame goes to the conflict file (call 1 in section 13).
- Refreshing the copy at the export location. Export project does that when Lauren chooses (call 6).
- Training, Refine, or Edit Masks on the laptop.
- Moving TCRMP images. A project carries its own frame copies.

## 2. Read these first

| What | Where | Why it matters |
|---|---|---|
| Export project's spec and plan | `/mnt/rip/vicarius_drive/docs/superpowers/specs/2026-11-02-vega-project-export-import-design.md`; `/mnt/rip/vicarius_drive/docs/superpowers/plans/2026-11-02-vega-project-export.md` | The card, the admin panel, the export location and the copy record already exist |
| Export project's server code | `PT`: states 45-49, `location_status` 255-282, `load_tool` 284-308, `copy_state` 310-335, `card_fields` 356-379, `apply_copy_states` 381-407, `begin_export`, `end_export`, `is_exporting` 409-434, `run_export` 436 on (no `--update`, 448-449). `MV`: `project-location` and `export-run` 4592-4685 (only the `none` state may export, 4670-4671); `/runs` 3654-3738 | Import extends these |
| Export project's page code | `ML`: `HAS_TRANSFER` 219; the admin panel 140-154 and 1036-1073; `transferChip` 584-592; `exportBlock` 609-617 (Export is off in every state but `none`, 615); `exportButton` 620-634; `promptPurpose` 873-973; `exportRun` 1005-1032 | Import adds a button, chips and a dialog beside these |
| Export project's tool | `EXP`: exclusions 53-58 and 81-83; the no-store case 170-178; `annotator_on_project` 182-206; `frame_hash` 209-218; `frame_hashes` 221-233; `holds_laptop_work` 368-383; `publish` 385-490, with the revision at 417-433 and 459 and the copy record at 457-467; command line 496-504 | The merge base, the copy record and the exclusions come from here |
| The laptop's moves | `LAP` `vega_annotator/transfer.py`: `EXCLUDED_DIRS` 52; conflict folders 836-866; `_send_work` 869-918; the Put Back marker 977. `LAP` `vega_annotator/projects.py`: `stamp_access` 70-90; `carry_local_notes` 96-120; `set_description` 122-136. `LAP` `tools/sync_from_vega.py` 44-60 (the files the laptop copies from VEGA) | What the returned copy holds, and the order the laptop writes it in |
| VEGA's precedent for writing into a live project | `RFR` 29-58, 104, 125-144, 380-388, 643, 778-841; `SUP` 17-55, 101-122 | Backups, journal line, lock order and stamps VEGA already uses |
| The annotator's store, save and merge | `A` 441-443, 495-560, 661-691, 713-783, 786-834, 837-931, 1127, 1629-1649, 2225-2226, 3442-3512 | Frames it adds on its own, the merge and the conflict rules |
| Export outputs | `A` 925-1040, 4412-4480, 4482-4636; `EY` 21-85, 88-155, 189-207, 210-343 | Manifest, labels, class map, `data.yaml`, registry rows |
| Ledger | `LI` 39, 47, 63-110, 120-209; `A` 4205-4324 | `label_provenance.csv`, its writer and its lock |
| Registry | `MR` 33-38, 61-93, 96-133, 136-239, 241-270, 272-317; `UID` 54-109 | Rows keyed by mask uid; `upsert` and `prune_frame` keep no backup |
| Project record | `PM` 15-31, 103-115, 329-378, 435-473, 670-740, 882-1041, 1189-1190; `MV` 2859-2924 | Locks, atomic writes, who opened it last |
| Orchestrator state | `APP` 570, 1822-1831, 4900-4922, 5063-5097, 5146-5167, 5498, 5540, 5929, 6114-6137, 6249-6250 | It holds the open project in memory, overwrites the file on save, and starts the annotators |
| Who reads below a project folder | `MB` 188-241, 226-230, 298-539, 308-311; `SCR` 74-76, 123-170, 243-385; `G/scripts/_matrix/stats.py` 115-170 | The readers that would count anything the import leaves in the wrong place |
| Integrity checker | `CPI` 118-124, 137, 236-240, 518-532, 595-1439 (message builders at 960, 995-1024, 1039, 1106, 1143-1170, 1181-1261), 1592-1660, 1723-1759 | The undo test |
| House rules | `/mnt/rip/vicarius_drive/CLAUDE.md` 9-28 and 155-182; `G/CLAUDE.md` 11-27 | Locks, images, backups, changelog, descriptors, tests |

## 3. The returned copy at the export location

Export project (`EXP`) and the laptop app (`LAP`) write everything below. Import reads it, never writes it, and never trusts it without checking.

### 3.1 Layout

```
<location>/projects/
  publish_log.csv                    # one line per export: at, by, run, rev, files, bytes, vega_commit
  <run>/
    vega_annotator.json              # the copy record (3.2)
    checksums.json                   # {rel path: {size, sha256}} for every file in the copy
    project.json                     # the project record, mainframe paths, laptop rows added
    step2_recodeSpecies/...          # as exported; all_points_recoded.csv is left out
    step3_chooseImages/...           # as exported
    step4test_combinedAnnotate/
      segmentations/segmentations.json
      segmentations/segmentations.conflict-<stamp>.json   # only if the laptop's annotator made any
      segmentations/overlays_reviewed/<stem>_seg.jpg      # made on the laptop
      previews/<stem>_seg.jpg                             # made on the laptop
      all_labels/<stem>.txt
      export_manifest.csv  label_provenance.csv  class_map.json  data.yaml
      .export_batch_seq  export_log.txt  mask_status_log.jsonl  eml.xml
      prior_screening.json                                # written fresh by Export (EXP 449-455)
      custom_imports/{raw,originals}/...  custom_imports/imports_manifest.json
      vega_annotator_registry/manifest.csv                # the laptop's registry rows (3.5)
      routed_input/...                                    # frames and prompts, unchanged
      config_log.json                                     # the laptop's launch settings
    conflicts/<stamp>_<computer>_<6 hex>/                 # laptop work set aside (3.4)
    _superseded/<stamp>/before_put_back/<rel>             # copy files each Put Back replaced
  _superseded/<stamp>/<run>/                              # whole copies an Export --update replaced
```

`all_images/` never travels. The laptop rebuilds its own links (`LAP` `transfer.rebuild_image_links`), and VEGA keeps its own. Export leaves out `previews/` and `overlays_reviewed/` (`EXP` 53). Put Back carries them, because the laptop made them.

Put Back sends everything in the working copy except `_superseded/`, `all_images/`, `conflicts/`, `__pycache__/`, `.git/`, the laptop's own records, and `.tmp`, `.lock` and `.vegatmp` files (`LAP` `transfer.EXCLUDED_DIRS`, `EXCLUDED_NAMES`, `EXCLUDED_SUFFIXES`).

**The order of a Put Back.** `LAP` `put_back` first marks the record with `put_back: {computer, initials, at, token}` (`LAP` transfer.py 977). `_send_work` (869-918) then copies each changed file, after saving the copy's old version under `_superseded/<stamp>/before_put_back/<rel>`. It writes `checksums.json` next. Last, it writes the record with a new `put_back` history row, `rev` raised by 1, and `put_back: null`.

**Two-way sync.** Synology Drive syncs Alexandria's folder (session 38) and VEGA's own drive (session 21) both ways, with `conflict_policy=compare_mtime` and `rename_conflict=1` (`~/.SynologyDrive/data/db/sys.sqlite`, `session_table`). Files can reach the mainframe in any order, so a record can arrive before the files it describes; 7.1 step 3 makes the import prove the whole Put Back has arrived. When two machines change one file before it syncs, Synology keeps the newer and renames the older aside with `_Conflict` in its name; that is why the import never writes at the location (7.9). The client also stalled silently for a day on 1 Nov 2026.

**Keep holding.** A Put Back can leave the project held by the same computer (`keep_holding`, `LAP` transfer.py 905-910). The copy then reads `out` set, with work waiting. Import accepts this case (7.1 step 1).

### 3.2 The copy record, `vega_annotator.json`

`EXP.publish` writes it (`EXP` 457-467), and the laptop updates it.

| Key | Meaning |
|---|---|
| `format` | `1` (`EXP` `FORMAT`). Refuse any other value. |
| `project_id` | `project.json` `id`, or else the folder name without `run_` |
| `rev` | `previous_rev + 1` at each export (`EXP` 459), where `previous_rev` is 0 when the location holds no copy (`EXP` 417-433); raised by 1 at each finished Put Back. **A revision is not unique**: it starts again at 1 after an admin changes the location or someone removes a copy. Nothing in the import keys on it. |
| `source` | `{project_dir, repo_dir, published_at, published_by, vega_commit}` from the latest export, and from this batch `export_id` (8.1). Every absolute path in the copy names `source.project_dir`. |
| `left_on_mainframe` | What Export left out (`EXP` 56-58) |
| `prior_screening_note` | Export's note on the fresh `prior_screening.json` |
| `frame_hashes` | `{frame filename: sha256}` of each frame at the latest export, computed from the staged copy over sort-keys JSON with `image_path_abs` removed (`frame_hash` 209-218, `frame_hashes` 221-233, called at `EXP` 466). Empty when the project had no store (`EXP` 170-178). |
| `out` | `null`, or `{computer, initials, at}` while a computer holds the copy |
| `put_back` | `null`, or `{computer, initials, at, token}` while a Put Back is partway done |
| `history` | Rows in order (below). Today an export starts a fresh history (`EXP` 467); 8.1 changes that. |

| `action` | Written by | Fields |
|---|---|---|
| `publish` | `EXP` | `at, by, purpose, files` |
| `bring_in`, `take_over` | `LAP` `transfer._claim` | `at, computer, initials` |
| `bring_in_failed`, `take_over_failed` | `LAP` `transfer._undo_claim` | `at, computer, initials` |
| `put_back` | `LAP` `transfer._send_work` | `at, computer, initials, files, frames_left, masks, token`, and from VEGA Annotator 1.0.1, `checksums_sha256` (8.7). The `token` is unique to one Put Back. |
| `vega_took_back` | Export's refresh (8.1), one per import | `at, by, purpose, import_id, put_back_token` |

**Work waits** when the newest `put_back` row is newer than the newest `publish` or `vega_took_back` row, and `T/imports.jsonl` has no `imported` line carrying that row's `token`.

### 3.3 Paths in the copy

Put Back rewrites every absolute path from the laptop's form back to `source.project_dir` (`LAP` `pathmap.PathMap.to_server`). The rewritten files are `project.json`, `segmentations.json` (`image_path_abs`), `export_manifest.csv` (the `image_path` column), `data.yaml` (the `path:` line), and `config_log.json`. A frame's `image_path` is relative (`raw_rel`, `A` 1638), so it needs no rewrite. When the live project has not moved, the copy's paths already name it.

If the live folder differs from `source.project_dir`, because the project was renamed or restored from the archive, the import rewrites the same fields from `source.project_dir` to the live folder before comparing anything. Reuse the laptop's rules by copying `LAP` `vega_annotator/pathmap.py` (`PathMap`, `rewrite_file`) into VEGA as `G/scripts/_reefreview/transfer_paths.py`, with its tests.

### 3.4 Laptop conflict folders, `conflicts/`

When another computer took the project over, or the copy moved on, the laptop's Put Back writes nothing over the copy. It saves that computer's changed files under `conflicts/<YYYYMMDD-HHMMSS>_<computer>_<6 hex>/<rel>`, then writes `conflict.json` last: `{run, computer, initials, at, reason, base_rev, files}`, where `files` is the sorted list of relative paths (`LAP` transfer.py 846-860). These folders hold real labeling work that never reached the copy, and nothing in them is listed in `checksums.json`.

Import never merges them. It copies a folder whole to `W/laptop_conflicts/<folder name>/` only when it is complete: `conflict.json` exists and every file it lists exists, and, from VEGA Annotator 1.0.1, every file matches the size and SHA-256 that `conflict.json` records (8.7). A folder that is not complete is listed as "still arriving" and waits for the next import. `T/laptop_conflicts.json` records each copied folder's fingerprint: the sorted relative path, size and SHA-256 of every file. A folder whose fingerprint matches a recorded one is skipped, and one whose fingerprint changed is copied again.

### 3.5 The laptop's registry rows

The laptop launches the annotator with `TCRMP_MASK_REGISTRY_DIR=<copy>/step4test_combinedAnnotate/vega_annotator_registry` (`LAP` `projects.annotator_environment`). Its exports write rows there instead of in VEGA's shared registry (`MR` 61-93), with the same columns (`MR` 33-38). Import ignores these rows and writes the registry with VEGA's own rules from the merged frames (7.7).

### 3.6 Settings the laptop's annotator ran with

`LAP` `projects.annotator_environment` sets `TCRMP_PROJECT_ID` to `project.json` `id`, as the orchestrator does (`APP` 6249-6250), so ledger and registry `project_id` values match VEGA's form. It also sets `TCRMP_SYMLINK_IMAGES=1`, `TCRMP_PROVENANCE_SOURCE=step4test`, and `TCRMP_REVIEWER` from the last access row.

The laptop writes access rows `{ts, initials, action, purpose}` with the actions `open`, `bring_in`, `put_back` and `take_over` (`LAP` `projects.stamp_access` 70-90). A student can also edit the project's `description` on the laptop (`LAP` `projects.set_description` 122-136).

## 4. VEGA facts the import must respect

### 4.1 The store, `segmentations.json`

- One object keyed by image filename with its extension. Every value is a frame. There are no other keys (`A` 502-507, 1177).
- Frame keys: `image_path`, `image_path_abs`, `image_width`, `image_height`, `masks`, `reference_points`, `processed_at`, `reviewed`, `exported`, `scrapped`, `label_outcomes`, `ledger_pending`, `image_missing_at`, `returned_to_review`, `custom_import`, `import_scale`, `import_original`. Sources: `A` 1639-1645, 2051-2060, 2610-2734, 3908-3929, 4253-4315, 4535-4539; `PV` 178-184; `CI` 233-244; `RFR` 190-193.
- Mask keys: `id, label, species, name, category, source_x, source_y, polygon_px, polygon_norm, rle, bbox, area, score, status, refinement_clicks, source_type, reviewer, draw_edits, merged_from, source_box`. The expert keys are `review, review_uid, reviews, expert_id` (`G/scripts/TCRMPclip_combinedAnnotate/src/mask_ops.py` 989-1006; `A` 2005-4190).
- `label_outcomes` is `{label code: {outcome, reviewed, source, reviewer, at}}`, the same dicts the ledger stores (4.4).
- Writes go through a temp file, fsync, chmod 0664 and `os.replace`, as `json.dump(indent=1)` (`A` 664-691, 919). The import matches this exactly.
- The annotator takes no file lock on the store. The expert importer and `RFR` take `X/segmentations/.segmentations.json.lock` (`RFR` 138-144). Import takes it too.

**Frames the annotator adds on its own.** When the combined annotator opens a project, it adds an empty frame for every routed image that has none: `image_path` (relative), `image_path_abs`, `image_width`, `image_height`, `masks: []`, `reference_points` (the routed prompts), `processed_at` (the time), `reviewed: false` and `exported: false` (`A` 1629-1649). It does not mark the session dirty, so flush and quit write nothing while nobody edits (`A` 1127, 2225-2226). Any save writes the whole store (`A` 919), these frames included. A project exported straight after routing may therefore have no store, or a store without these frames, and either side can hold empty frames the other lacks. 7.3 treats such a frame as absent.

### 4.2 How the annotator merges with disk

`_merge_store_with_disk` (`A` 713-783) works frame by frame. A frame this process edited keeps its in-memory version; every other frame comes from disk; a frame on one side only is kept. On a frame edited on both sides it calls `_merge_imported_review_fields(current_masks, disk_masks)` (`A` 3450-3512), which carries the expert fields from disk. Import does not fold expert fields (non-goals); a frame VEGA changed in any way while the laptop also changed it is a conflict.

### 4.3 VEGA's conflict file

- Name: `X/segmentations/segmentations.conflict-<YYYYMMDD-HHMMSS>.json`, stamped in AST (`_conflict_episode_path`, `A` 441-443, 786-797).
- Shape: `{"<frame filename>": [{"at": "<AST ISO>", "content": <whole frame>}, ...]}`, with no wrapper keys. A repeat of a frame's last content is skipped (`_append_conflict_entries`, `A` 802-834).
- The only reader is `CPI` R13, which reports each file as a WARN listing its frames (`CPI` 137, 1411-1416). No code or page resolves a conflict.

Import writes the frames it sets aside into a new file of exactly this shape. R13 then reports it, which is the honest signal, and 7.6 expects that one item.

### 4.4 Export outputs and their invariants

`CPI` R05 to R09 test these. After an import every one still holds.

- **How VEGA exports.** The annotator's export route (`A` 4482-4636) calls `EY.export_batch(segmentations, export_dir, class_map, symlink=True, reviewer='')` (`EY` 210-343). For each frame passed in, `export_batch` stamps the session reviewer on accepted masks that lack one, writes the frame's registry rows (`_upsert_registry_for_frame`, before any skip), and skips scrapped frames, frames whose image is missing, and review-only frames. For the rest it writes the image link and the label file from accepted, labeled, non-review masks that have at least three points. At the end it saves `class_map.json` and `data.yaml` and appends one `export_log.txt` line. The route around it writes the manifest, ledger, mask status log, previews, overlays, `eml.xml` and `.export_batch_seq`.
- `all_labels/<stem>.txt`: one line per accepted, labeled, non-review mask, `mask_to_yolo_line(join_polygons(polygon_norm), class_id)`, each coordinate as `f'{v:.6f}'` (`EY` 46-85). A frame with no accepted masks and no review mask gets an empty file (`EY` 285-294, 329-331).
- `all_images/<filename>`: a symlink to `os.path.abspath(image_path_abs)`, made only when no entry exists (`EY` 299-305). `reconcile_export` asserts one entry per exported manifest row (`A` 1017-1040). VEGA's links are absolute, with one exception: on 3 Nov 2026 all 939 links in `run_26-10-04_AT`'s `all_images` were relative (`../routed_input/raw/<name>`), all made at 10:49 on 15 Oct 2026 by a tool nobody has named. They resolve, so the import keeps them and adds absolute links, as `EY` does.
- `class_map.json` `{species: id}`: ids are given in order of first export, `new_id = len(class_map)`, and never change while the file survives (`EY` 21-43). Rewriting it renumbers later exports, and old label files are never rewritten.
- `data.yaml`: `path: <abs export_dir>`, `train: all_images`, `val: all_images`, `nc`, `names` (`EY` 189-207). Train Model reads `names` (`APP` 6713-6757; `G/scripts/TCRMPtrain_oceankindCV/src/pinned_split.py` 190-201).
- `export_manifest.csv`: the columns `basename, n_masks, label_file, image_file, outcome, preview_file, batch_name, image_path`, then one sorted column per species code holding accepted-mask counts, with 0 as backfill. It is cumulative and keyed by stem (`EXPORT_MANIFEST_FIELDS` and its reader and writer, `A` 943-1014). `outcome` is one of `exported_with_masks, exported_empty, review_only, scrapped, image_missing` (`A` 4558-4567).
- `label_provenance.csv`: the header is exactly `basename,label,outcome,source,reviewer,at,project_id` (`LI` 39); `basename` is the stem. `LI.write_frames(export_dir, frames, project_id, source=None, protect_sources=frozenset())` (`LI` 160-209) replaces whole frames in one read-modify-write under `label_provenance.csv.lock` (`LI` 63-110). `frames` is `{basename: {label: outcome dict}}`, the dicts the store keeps in `label_outcomes`. An owner-group writer (`OWNER_GROUP_SOURCES = {'step4test', 'step4loop', 'step5', 'edit'}`, `LI` 47) replaces every row of the frame, and an empty dict clears the frame's rows. Rows whose source is in `protect_sources` are never dropped; the self-repair passes `'expertids'`. `CPI` R08 requires the rows to equal the frames' stored `label_outcomes`.
- `.export_batch_seq` holds a plain integer. `export_log.txt` takes appended lines. `mask_status_log.jsonl` holds lines of `{at, frame, mask_id, old_status, new_status, species, reviewer}` (`A` 2480-2527). `eml.xml` is rewritten at every export (`EY` 158-186).
- Overlays and previews: `X/segmentations/overlays_reviewed/<stem>_seg.jpg`, copied to `X/previews/<stem>_seg.jpg` (`A` 974-986).
- The expert review bundle (`A` 4412-4480) is switched off by a constant, so a laptop export makes none. If it is switched back on, this spec needs a section for `review_dir`.

### 4.5 The mask registry

- One file for all projects under one `inprocess` root, `<root>/_mask_registry/manifest.csv`, with the columns `uid,source_image,basename,species,category,source_type,review,expert_mode,status,project_id,project_name,site,year,transect,frame,created_at,updated_at,reviewer` (`MR` 33-38).
- Rows are keyed by `uid` alone. `mask_uid` combines site, date, transect, frame, the click position, and a hash of the mask's RLE (`UID` 54-109). It leaves out the project id and the mask id.
- `MaskRegistry(root=None)` (`MR` 137) opens `root` when one is passed. Without one it opens `TCRMP_MASK_REGISTRY_DIR` when that is set, else the module's own `inprocess/_mask_registry`, wherever the project lives (`MR` 61-93). **A tool that relies on the default writes the live registry even for a project under a scratch root.**
- `MaskRegistry.upsert(record)` takes `flock` on `.manifest.lock`, rewrites the whole file atomically, keeps `created_at`, always sets `updated_at`, never lets a blank reviewer erase recorded initials, and keeps no backup (`MR` 136-159, 199-239).
- `prune_frame(project_id, source_image, keep_uids)` deletes this project's rows for an image whose uid is not kept, with no backup (`MR` 272-317). Only `purge_test_rows` writes a backup (`MR` 241-270, 319).
- `EY._upsert_registry_for_frame(filename, seg, export_dir, reviewer='')` (`EY` 101-155) is VEGA's per-frame writer. It upserts every mask of the frame, accepted or not, then prunes the frame's other rows, but only when every mask was recorded and the project is a run project (`TCRMP_PROJECT_ID` set, or a `run_*` folder in `export_dir`). It takes its project id from `TCRMP_PROJECT_ID`, else from the `run_*` folder name. It never raises: a failure is only logged. It rewrites the whole registry once per mask.
- Readers: the Matrix (`MB` 417-489), STATS (`G/scripts/_matrix/stats.py` 115-170), and `CPI` R09. R09 expects exactly one row for every mask of every exported frame, with matching `species, status, source_type, review, expert_mode` (`CPI` 1181-1261). The nightly report of 3 Nov 2026 counts "20 registry rows belong to test-fixture projects" in the live registry, so test writes have reached it before.

### 4.6 The project record, `project.json`

- VICARIUS `_mint_run` creates it (`MV` 4321-4343). The orchestrator's `load_project` fills in steps 1 to 8 and saves (`PM` 882-956).
- The write contract is a thread lock plus `flock` on `<run>/.project.json.lock`, then a unique temp file, fsync and `os.replace`, keeping the old file mode (`PM` 329-378, 435-473). `_stamp_project_access` follows it (`MV` 2878-2912). Import does too.
- `access_log` rows are `{ts, initials, action, purpose}`. `CPI` R01 requires `ts, initials, action` on each (`CPI` 620-630).
- **Who opened it last.** `reviewer_for_state` (`PM` 683-699) returns the initials on the last row that has any, whatever its action. They become `TCRMP_REVIEWER` for the next annotator (`APP` 1822-1831). The Matrix's `opened_by` uses the same rule (`MB` 161-185). That is why Export writes no row (`MV` 4638-4640), and why Import marks the laptop's rows (7.8).
- `refresh_auto_name` rebuilds the display name on every orchestrator save (`PM` 724-740).

### 4.7 The orchestrator holds the open project in memory

`current_project` is a dict in the orchestrator process (`APP` 570), carrying `project_dir` (`PM` 899). `save_project` writes that dict without re-reading the disk (`PM` 959-996). It saves on open, on a config change, on a step run, on status polls, and on quit (`APP` 304, 2435, 5107, 5242-5299, 7388-7600, 8763-8773). **A row anyone else adds to `project.json` while the orchestrator has that project loaded is lost at its next save.** This already erases VICARIUS's own access rows; 8.4 fixes it for everyone.

The orchestrator keeps a project loaded until someone clicks Save and close or opens another project. Closing the browser tab leaves it loaded (`APP` 5063-5096, 5146-5167). `GET http://127.0.0.1:5050/api/project/state` returns `{"loaded": false}`, or `{"loaded": true, "state": _state_payload(current_project)}` (`APP` 5092-5096). While the module is locked it answers 423 unless the request carries the backend key (`APP` 4900-4922, `lock_status.is_backend_request`; key file `~/.config/vicarius/vega_backend_token`). Import never calls the orchestrator's quit or open.

### 4.8 Detecting an annotator on the project

Use `EXP.annotator_on_project(project, proc_root="/proc")` (`EXP` 182-206). It finds a process with `app.py` in its command line and `TCRMP_EXPORT_DIR=<realpath(project)>/step4test_combinedAnnotate` in its environment, which covers the combined annotator, Refine and Edit Masks. Never decide by port. Ports 5080 to 5089 are shared with other modules, and on 3 Nov 2026 port 5080 was vicarlab's. `RFR` refuses on any listener there (`RFR` 380-387), which would block an import for no reason. The orchestrator starts annotators at `APP` 5498, 5540, 5929 and 6249, each with `TCRMP_EXPORT_DIR`; 8.4 makes those starts refuse while an import is partway.

### 4.9 Who reads files below a project folder

- The Matrix gathers every `**/label_provenance.csv` and every `**/segmentations/segmentations.json` and `**/segmentations/*/segmentations.json` under a project, skipping only `_superseded` (`MB` 226-230, 308-311). It reads frames with `reviewed` or `exported` true, and the registry (`MB` 188-241, 298-489).
- About ten VEGA tools skip `_superseded` the same way, with `SUP.is_superseded_path(path, root)` (`SUP` 37-55): `CPI`, `RFR`, the Matrix and `_matrix/cellinfo.py`, Choose Images' `select_images.py`, `clip_index.py`, `make_lores_variants.py`, the expert importer `_expertids/importer.py`, `EXP`, and `APP`.
- So anything the import keeps that looks like project files (staged copies, laptop conflict folders) lives under `X/_superseded/`, never in `T`. Otherwise the Matrix, STATS and the Screened rule would count set-aside work.
- The Screened rule reads only `build_matrix` (`SCR` 74-76, 123-170). `prior_screening.json` is an output that the orchestrator rewrites before every annotator launch (`APP` 6114-6137). **Import never copies the laptop's `prior_screening.json`.**
- Train Model reads `all_images/`, `all_labels/` and the `data.yaml` names, and pins its split in `<step6>/dataset/split_manifest.json` (`G/scripts/TCRMPtrain_oceankindCV/src/pinned_split.py`: `load_original_names` 190, `build_image_index` 239, `replay_manifest` 446, `copy_label_filtered` 619).

### 4.10 Backups and stamps

`SUP` defines `SUPERSEDED_DIR_NAME = "_superseded"`, `STAMP_FORMAT = "%Y%m%d-%H%M%S"` and `stamp_now()` (`SUP` 17-35), and gives unique names for clashing paths (`SUP` 122 on). `RFR` backs up to `X/_superseded/<stamp>/before_return_to_review/` and logs to `X/review_returns.jsonl` (`RFR` 29-34, 104, 643, 778-841). `EXP` and the laptop's Put Back skip any folder named `_superseded` at any depth (`EXP` 53, 81; `LAP` transfer.py 52). `PM`'s clean-up removes `_superseded` trees that hold only model artifacts and keeps any that hold annotation work (`PM` 1189-1190, `SUP.holds_annotation_work` 101-120).

### 4.11 The integrity checker

- `run_checks(cfg)` (`CPI` 1592 on) lists every `run_*` folder under `--root` and runs all 15 rules. It writes nothing; the command line writes `<stamp>.txt` and `.json` to `--report-dir`. `--root` is an `inprocess` tree, and the registry is read from `<root>/_mask_registry/manifest.csv` (`CPI` 1186, 1327).
- Flags (`CPI` 1723-1735): `--root`, `--archive-dir` (default `<root>/../archive`), `--report-dir`, `--vocab`, `--pipeline-config`, `--snapshot-dir`, `--manual-source-file`, `--exceptions`, `--now`, `--json`, `--verbose`. No flag limits a run to one project; 8.5 adds one.
- Exit 0 means no FAIL, 1 means a FAIL, and 2 means a usage or read error. The JSON is `{root, started, projects, summary: {PASS, INFO, WARN, FAIL}, findings: [{rule, level, project, path, message}]}`.
- Per-project rules: R01 project record; R04 store; R05 `all_images`; R06 labels, class map and the `data.yaml` path; R07 manifest; R08 ledger; R09 registry rows for exported masks; R13 conflict files, temp files and `prior_screening.json`. R10, R11, R12, R14 and R15 look across projects.
- **How findings are worded.** All of a rule's findings for one project share one path: the manifest for R07 (`CPI` 960), the ledger for R08 (1039), the shared registry for R09 (1186). Each message holds a count and up to five example names (`CPI` 1024, 1106, 1143, 1170, 1260). The nightly report of 3 Nov 2026 (`/mnt/tear/vega_run_snapshots/integrity/20261103-031501.json`) already holds FAILs of this shape for other projects. A message therefore changes whenever one item is added or fixed, so comparing messages cannot tell an import that broke something from one that fixed something. 8.5 adds item lists.

## 5. The transfer folder `T` and the working folder `W`

**`T` = `X/_transfer/`** holds only small records whose names match no VEGA glob:

| Path | Written by | What |
|---|---|---|
| `T/merge_base.json` | Export (8.1) and Import (7.5) | `{format: 1, from: "export" or "import", at, by, lineage, put_back_token, import_id, store_sha256, record_sha256}` |
| `T/merge_base_store.json` | Export and Import | The store as the copy last matched VEGA, in live paths |
| `T/merge_base_record.json` | Export and Import | The project record at the same moment, for its `description` and access rows |
| `T/imports.jsonl` | Import | One line per finished or undone import, keyed by the Put Back token (7.11) |
| `T/laptop_conflicts.json` | Import | Fingerprints of laptop conflict folders already copied (3.4) |
| `T/import_journal.json` | Import | Present only while an import is partway (7.10) |
| `T/.import.lock` | Import | The `flock` target for one import at a time |
| `T/checks/<import_id>/copy/`, `before/`, `after/` | Import | The integrity checker's reports (7.6) |

`lineage` is the record's `source.export_id`, or `source.published_at` for a copy exported before this batch. It ties a merge base to one export of the copy.

**`W` = `X/_superseded/<stamp>/`** is one import's working folder, with `<stamp>` from `SUP.stamp_now()` and SUP's unique naming on a clash:

| Path | What | Kept |
|---|---|---|
| `W/incoming/run_<id>/` | The staged snapshot of the copy: every file in `checksums.json` except `routed_input/` | Removed at the end |
| `W/result/` | The built files, mirroring `X` | Removed at the end |
| `W/before_import/` | A copy of every live file the import replaces (`project.json` as `_run/project.json`, the merge base files as `_transfer/...`) | Kept |
| `W/laptop_conflicts/<folder>/` | Laptop conflict folders copied whole (3.4); the laptop annotator's own conflict files under `W/laptop_conflicts/annotator/` | Kept |
| `W/rolled_back/` | Files an undo moved out | Kept |
| `W/stale_all_images/` | Image links whose frame is no longer exported | Kept |

Staged copies and laptop conflict folders sit in `_superseded` because every VEGA reader already skips it (4.9) and `PM`'s clean-up keeps any tree with annotation work (4.10). An `import_id` is `<stamp>_<6 hex>`. Every file is written atomically with mode 0664, as the store is (`A` 664-691).

`T` and `W` never travel. Export adds `_transfer` to `EXCLUDED_DIR_NAMES` (`EXP` 53) and already skips `_superseded`; VEGA Annotator adds `_transfer` to `transfer.EXCLUDED_DIRS` (8.7). A test fills `T` and `W` and shows that the Matrix, the integrity checker and Choose Images see nothing new (11).

**Why the import keeps a merge base.** The record's `frame_hashes` describe the store at the latest export. After an import without a fresh export, the laptop keeps working from the copy it put back, so the next import must compare against that copy. With only the export's fingerprints, a frame the laptop changed in the first round and again in the second would look changed on both sides, and the student's second round would land in the conflict file instead of the project.

## 6. What the person sees

### 6.1 Card states

`PT.copy_state` gains three states and reads `T` as well as the copy record. It decides in this order:

1. The record cannot be read: `unreadable`.
2. `put_back` is set: `out`, and the chip reads "Putting back from Legion".
3. An import of this project is running in VICARIUS: `importing`.
4. `T/import_journal.json` exists: `import_stopped`.
5. Work waits (3.2): `work_waiting`. When `out` is also set (keep holding), the card shows both chips.
6. `out` is set: `out`.
7. The newest `put_back` row is newer than the newest `publish` row, and `T/imports.jsonl` has an `imported` line with its token: `imported`.
8. A copy exists: `exported`. No copy: `none`.

| State | Chip | Export project | Import project |
|---|---|---|---|
| `none` | none | as today | hidden |
| `exported` | Exported | as today | hidden |
| `out` | Out on Legion, or Putting back from Legion | off, as today | off: "Out on Legion since 3 Nov 14:20, with nothing new to import." or "Legion is putting this project back. Try again when it finishes." |
| `work_waiting` | Work to import (and Out on Legion when held) | off, as today | **on** |
| `importing` (new) | Importing | off | off, reading IMPORTING |
| `import_stopped` (new) | Import stopped partway | off | on, reading FINISH IMPORT |
| `imported` (new) | Imported | on, as a refresh (8.2): "Refresh the copy at the export location with VEGA's merged project. This uploads about 0.6 GB." | hidden |
| `unreadable` | Copy cannot be read | as today | off |
| location not reachable | as today | as today | off |

Import project is also off, with the reason as its tooltip, while:

- the module is locked;
- VEGA's annotator, Refine or Edit Masks runs on the project (4.8): "VEGA's annotator has this project open. Close it, then import.";
- the orchestrator has this project loaded (4.7): "VEGA's pipeline window has this project loaded. Open it there and click Save and close, then import."

`/runs` scans `/proc` and asks the orchestrator once per call, never once per card. A state a person cannot act on shows no pointer cursor, and every control carries a tooltip that says why, per section 7 of `/mnt/rip/vicarius_drive/docs/superpowers/specs/2026-09-02-workflow-page-design.md`.

### 6.2 The check dialog

Clicking **IMPORT PROJECT** opens a modal built with the page's `VIC.buildModal`, as `promptPurpose` uses it (`ML` 873-973). It reads "Checking the copy" while `import-check` runs, which can take a minute on a large project. Then it shows plain lines, each only when it applies:

- "From Legion, put back 3 Nov 14:20 by AT: 62 frames labeled there, 58 exported, 4 scrapped, 211 masks on them, 120 frames left."
- "3 frames were also changed in VEGA since the copy last matched it. VEGA keeps its version, and the laptop's goes to segmentations.conflict-20261103-142000.json." The frames are listed below.
- "2 of the frames are images imported on the laptop."
- "1 folder of laptop work was set aside on the laptop: conflicts/20261103-101500_Legion_ab12cd. It is copied into the project for you to review." Or: "1 folder of laptop work is still arriving and waits for the next import."
- "The laptop's copy already has 2 integrity findings on frames it brings: R07 manifest counts differ for 1 frame (<name>). They come across with its work."
- "The copy's description changed on the laptop and in VEGA. VEGA's is kept: <text>."
- "4 files in the copy are not imported: <names>." These are files that differ from the live project's and that no rule in 7.4 covers.
- "This copy predates Import project, so VEGA rebuilt what it exported from the copy's own backups." Only for a rebuilt base (7.2).
- "Compared with the frame fingerprints from the export. A frame touched on both sides goes to the conflict file." Only for a hashes-only base.
- "The import replaces 140 files, about 48 MB, and keeps a copy of each first."

When the newest Put Back row lacks `checksums_sha256` (VEGA Annotator 1.0.0), the dialog adds: "This Put Back came from VEGA Annotator 1.0.0, which cannot prove the whole copy has reached the mainframe. Check that Synology Drive on the mainframe shows this folder up to date." A checkbox reading "Synology Drive shows it up to date" must be ticked before IMPORT turns on, and the import record notes who ticked it.

Below these sit the initials and purpose fields that `promptPurpose` already builds; give it a `bodyPrefixHtml` option. The suggested purpose is "importing <run> from <computer>". The primary button reads IMPORT, and Cancel changes nothing. A refusal shows its sentence and offers only Close.

### 6.3 After the import

- Success: the toast "project imported, logged as AT", and the card reloads to "Imported".
- Undone: a toast naming the finding, for example "import undone: R07 manifest counts differ for 2 frames". The card keeps "Work to import". Everything is back as it was, and the person can retry after fixing the cause.
- Stopped partway: the card reads "Import stopped partway", and FINISH IMPORT runs `import-finish` (9.3).

### 6.4 OPEN and Archive while a project is out

- **OPEN** (`ML.openRun`): when the card reads "Out on X" or "Work to import", OPEN first shows a modal: "This project is out on X since <time>. A frame changed both here and there keeps VEGA's version, and the laptop's is set aside." It offers OPEN ANYWAY and Cancel.
- **Archive** (`MV.api_module_archive_run`, `MV` 4529-4571): refuses with 409 while the state is `out`, `work_waiting`, `importing` or `import_stopped`. The message names the state.

## 7. The import, step by step

One versioned tool, `G/scripts/import_project.py`, does all the writing. VICARIUS runs it as its own process with VEGA's env python (`G/env/bin/python`), as it runs `EXP`. It needs Python 3.8 or later. It imports `EY` (whose imports are stdlib only, `EY` 10-16), `LI`, `MR`, `SUP`, `transfer_paths` (3.3), `store_io` (7.0), and `EXP`'s `frame_hash` and `annotator_on_project`. It never imports `A`, because importing `A` builds the Flask app from `TCRMP_*` settings.

**Which root and which registry.** The tool treats the project folder's parent as the `inprocess` root. It opens the registry as `MaskRegistry(root=<parent>/_mask_registry)`, passed explicitly, and runs the integrity checker with `--root <parent>`. It never relies on `TCRMP_MASK_REGISTRY_DIR` or on the default registry folder (4.5), so a project under a scratch root can never write the live registry.

### 7.0 First, extract VEGA's helpers without changing behavior

So that the import and the annotator run one implementation, move these helpers out of `A` into `G/scripts/_reefreview/store_io.py`, and have `A` import them back under the same names. The annotator's suites must stay green with no expected output changed.

| Helper | Now at | Import uses it for |
|---|---|---|
| `_frame_hash` (sort-keys JSON) | `A` 495-499 | Comparing frames |
| `_conflict_episode_path`, `_append_conflict_entries` | `A` 786-834 | Writing set-aside frames |
| `EXPORT_MANIFEST_FIELDS`, `_read_export_manifest`, `write_export_manifest`, `reconcile_export` | `A` 943-1040 | The manifest merge and the final check |
| The atomic store write | `A` 664-691 | Writing the store |
| `_manifest_path`, `save_manifest` (the `processed_manifest.json` writer) | `A` 425-426, 934-940 | Recomputing it |

In `EY`, split the text of `update_data_yaml` (189-207) into `data_yaml_text(export_dir, class_map)`, so the import can write `data.yaml` for the live folder from a staging folder. 8.6 lists the registry changes.

Edit Masks keeps its own copies of some of these (`G/scripts/TCRMPclip_editMasks/src/app.py` 375-389, 724-772). Leave them alone in this batch and list them in the batch note.

### 7.1 Stage 1: check

`import_project.py --check --project <run> --location <location> --json`. The check writes nothing in the project or at the location; the integrity checker's reports go to a temporary folder that the check removes.

1. **Copy record.** Read `<location>/projects/<run>/vega_annotator.json`. Refuse when it is missing or unreadable, when `format != 1`, when `project_id` differs from the live `project.json` `id`, or when `put_back` is set. A set `out` is allowed: a Put Back with keep holding leaves it set, and the copy does not change while the laptop holds it.
2. **Work to import.** Refuse with "Nothing new to import" unless work waits (3.2). The test keys on the newest Put Back row's `token`, never on `rev`.
3. **Proof the whole Put Back arrived.** When the newest `put_back` row has `checksums_sha256`, refuse unless the SHA-256 of the `checksums.json` bytes equals it: "The copy is still arriving at the mainframe. Try again when Synology Drive finishes." When the row lacks it, require the confirmation in 6.2.
4. **Checksums.** Check every entry in `checksums.json` by size, then SHA-256. A mismatch refuses: "<file> differs from the copy's own list. A sync may still be running; try again later." Refuse any listed path that is absolute, holds `..` or a backslash, or escapes the copy, with `LAP` `transfer.safe_rel` as the rule.
5. **Locks and processes.** Refuse when:
   - the module is locked;
   - `EXP.annotator_on_project` finds a process;
   - the orchestrator answers `loaded: true` with this run as the realpath of `state["project_dir"]`. Ask it with the backend key. A refused connection counts as not loaded, and any other failure refuses: "VEGA's pipeline window did not answer.";
   - another import or export of this project runs (`PT.begin_transfer`, 7.12);
   - `T/.import.lock` is held.
6. **Paths.** Rewrite the copy's path fields in memory when the live folder differs from `source.project_dir` (3.3).
7. **Base.** Choose the merge base (7.2).
8. **Classify** every frame (7.3), and plan every other file and every laptop conflict folder (7.4, 3.4).
9. **The copy's own findings.** Run the integrity checker on the copy where it lies: `--root <location>/projects --project <run> --json --report-dir <temporary folder>`. Keep its items on frames the import takes, leaving out R09, since a copy has no registry. The dialog lists them (6.2).
10. **Measure.** List every live file the import would replace or add, with sizes. Refuse when the project's disk has less than twice that plus 1 GB free.
11. **Report.** Return the JSON in 9.1 with a `check_token`: the SHA-256 over the `checksums.json` bytes, the newest Put Back token, and the live store, manifest, ledger and class map bytes. The run step refuses when the token no longer matches.

### 7.2 Choosing the merge base

1. **Exact.** `T/merge_base.json` reads, its two hashes match `T/merge_base_store.json` and `T/merge_base_record.json`, its `lineage` equals the record's (5), and either:
   - `from == "export"`; or
   - `from == "import"`, and its `put_back_token` belongs to a `put_back` row in this record's history that is older than the newest one.
2. **Rebuilt.** This applies to a copy exported before this batch, when `T` has no base for its lineage. The store as exported is the oldest copy of `step4test_combinedAnnotate/segmentations/segmentations.json` under the copy's own `_superseded/*/before_put_back/`, or the copy's current store when no Put Back replaced it. Use it only when its frame names equal the keys of `frame_hashes` and every frame matches its hash. The record as exported comes the same way from `project.json`. Mark the check `baseline: "rebuilt"`.
3. **Hashes only.** Otherwise the base is `frame_hashes` alone, and there is no base record. Mark the check `baseline: "hashes only"`.

The base files are in live paths, and every comparison happens after the copy's paths are in live form (7.1 step 6).

### 7.3 Classifying frames

**Normalize** a frame by removing the keys that change without anyone labeling: `image_path_abs`, `ledger_pending`, `image_missing_at`.

**Untouched frames count as absent.** A frame is untouched when:

- its `masks` list is empty;
- `reviewed`, `exported` and `scrapped` are false or missing;
- `label_outcomes` is empty or missing;
- it has no keys beyond the ones the annotator adds on its own (4.1) and the three bookkeeping keys.

An untouched frame counts as absent, so two untouched frames match each other and match a missing frame. A frame with `custom_import` is never untouched.

**"Same"** means equal normalized frames, compared with the extracted `_frame_hash`. With a hashes-only base, "same as base" means `EXP.frame_hash(frame) == frame_hashes[name]`, and an untouched frame on either side counts as same as base.

With L the copy's frame, V VEGA's live frame, and B the base, per frame name in the union of the three, where "absent" includes untouched:

| Base | Laptop (L) | VEGA (V) | Class | Result |
|---|---|---|---|---|
| present | same as B | same as B | `unchanged` | VEGA's |
| present | changed | same as B | `take` | the laptop's frame |
| present | same as B | changed | `keep` | VEGA's |
| present | changed | changed, same as L | `unchanged` | VEGA's |
| present | changed | changed otherwise | `conflict` | VEGA's; the laptop's goes to the conflict file |
| present | absent | present | `keep` | VEGA's; reported, since the laptop never removes frames |
| present | same as B | absent | `keep` | VEGA's; reported |
| present | changed | absent | `conflict` | VEGA's; the laptop's goes to the conflict file |
| absent | present | absent | `take` | the laptop's frame, over any untouched frame VEGA holds |
| absent | absent | present | `keep` | VEGA's |
| absent | present | present, same | `unchanged` | VEGA's |
| absent | present | present, different | `conflict` | VEGA's; the laptop's goes to the conflict file |

An expert review that reached VEGA's copy of a frame counts as a change, so a frame the laptop also changed is a `conflict`.

A `take` frame whose picture exists neither in the live project nor in the copy's `custom_imports/` refuses the check: "<frame>'s picture is missing in both places." The dialog counts as custom images the `take` frames whose `custom_import` is true.

### 7.4 Stage 2: build the result beside the project

`import_project.py --run --check-token <t> ...` takes `T/.import.lock`, which it holds to the end. It repeats every Stage 1 check and refuses on a token mismatch. Then:

1. **Before-check.** Run the integrity checker on the live project (7.6), before anything is staged.
2. **Stage the copy.** Copy every file in `checksums.json` except `routed_input/` into `W/incoming/run_<id>/`, then check each staged file against `checksums.json`. The staged set is one consistent snapshot even if a Put Back starts during the copy; Stage 3 rereads the record and catches that case. Run the integrity checker on the snapshot (`--root W/incoming --project run_<id>`). Its items, leaving out R09, are the copy's own findings for 7.6.
3. **Build** the result in `W/result/`, which mirrors `X`. Nothing live changes yet.

Every file that differs from the live project's falls under exactly one of the rules below, or under "Never copied", or under "Not imported". The last is listed in the dialog, so nothing is dropped unseen.

**Store.** Start from VEGA's store. Put in the `take` frames, with `image_path_abs` in live form and `ledger_pending` removed, because Stage 3 writes their ledger rows. Write it with the extracted atomic writer.

**Class map and `data.yaml`.** Let V be VEGA's `class_map.json` and L the copy's. Keep every id in V. Append each species in L that V lacks, in L's id order, with `id = len(merged)`. Write `data.yaml` with `data_yaml_text(X, merged)`, so its `path:` names the live folder.

**Labels.** For each `take` frame, copy `all_labels/<stem>.txt`, mapping each line's class id to its species through L, then to the merged id. A line whose id L lacks refuses the import: "<stem>.txt names class <n>, which the laptop's class map lacks." An exported frame with no label file in the copy refuses: "<stem>.txt is missing from the copy."

**Manifest.** Read both manifests with the extracted reader. The result keeps VEGA's rows for every frame not taken, and the copy's rows for `take` frames, with `image_path` in live form. The species columns are the sorted union, with 0 as backfill. Write it with the extracted writer.

**Ledger.** The ledger is computed, never copied. For each `take` frame, Stage 3 calls `LI.write_frames(X, {stem: frame["label_outcomes"] or {}}, live_id, source="step4test", protect_sources={"expertids"})`, all frames in one call. That is an owner-group write: it replaces the frame's rows, keeps expert rows, and an empty dict clears the old rows of a frame the laptop scrapped. When the copy's own `label_provenance.csv` rows for a taken frame disagree with its `label_outcomes`, the copy's own findings show it as an R08 item.

**Overlays and previews.** Copy `segmentations/overlays_reviewed/<stem>_seg.jpg` and `previews/<stem>_seg.jpg` for each `take` frame the copy holds them for.

**Custom imports.** Copy every file under the copy's `custom_imports/raw/` and `custom_imports/originals/` that the live project lacks. Merge `custom_imports/imports_manifest.json` entries by `filename`, keeping VEGA's on a clash.

**Small records.**
- `.export_batch_seq`: the larger integer.
- `export_log.txt` and `mask_status_log.jsonl`: VEGA's lines, then the copy's lines VEGA lacks, in file order.
- `segmentations/processed_manifest.json`: recomputed from the merged store with the extracted writer.
- `eml.xml`: VEGA's stays. VEGA's next export rewrites it (`EY` 158-186).

**Set aside.**
- `conflict` frames go to a new `segmentations/segmentations.conflict-<stamp>.json` in `W/result/`, through `_append_conflict_entries`.
- The copy's own `segmentations.conflict-*.json` files go to `W/laptop_conflicts/annotator/` under their own names, so they never mix with VEGA's.
- Complete `conflicts/` folders go to `W/laptop_conflicts/<folder>/` (3.4).

**Never copied:** `prior_screening.json`, `config_log.json`, `vega_annotator_registry/`, `routed_input/`, `label_provenance.csv` (computed instead), the copy's `_superseded/`, `step2_recodeSpecies/`, `step3_chooseImages/`, `vega_annotator.json`, `checksums.json`, and the copy's `project.json`, whose rows 7.8 handles.

### 7.5 Stage 3: move into place, with a journal

Keep `T/.import.lock`, and take the other locks in this order, releasing them in reverse:

1. `<run>/.project.json.lock` (`PM` 329-378)
2. `X/segmentations/.segmentations.json.lock`
3. `X/label_provenance.csv.lock` (`LI` 63-110)

The registry's `.manifest.lock` is taken inside `MR` (7.7).

Under the locks:

1. **Re-check.** Repeat the process and orchestrator checks (4.7, 4.8). Reread the record and refuse when `put_back` is set or the newest Put Back token changed. Recompute the token. Any change aborts with "The project changed during the check. Run Import project again." Nothing has moved yet.
2. **Journal.** Write `T/import_journal.json`: `{import_id, working_folder, stage: "moving", moves: [{rel, staged, live, sha256_after, existed_before}], registry_before: null}`.
3. **Back up.** Copy every live file the import will replace to `W/before_import/<path relative to X>`, checking each copy's SHA-256. `project.json` goes to `W/before_import/_run/project.json`, and the `T` merge base files go to `W/before_import/_transfer/`.
4. **Move.** Put each built file in place with `os.replace`, making folders as needed. Write the ledger with the one `LI.write_frames` call (7.4).
5. **Project record.** Apply 7.8.
6. **Image links.** Make `all_images/` hold one link per manifest row whose outcome is `exported_with_masks` or `exported_empty`, as `reconcile_export` counts them. Make missing links only, each an absolute symlink to `os.path.abspath(image_path_abs)` (`EY` 299-305). A link whose frame is no longer exported moves to `W/stale_all_images/`, never deleted.
7. **Reconcile.** Run the extracted `reconcile_export`. A failure starts the undo (7.10).
8. **Registry.** Apply 7.7, which sets the journal to `stage: "registry"` before its first write.
9. **After-check.** Run 7.6. A caused item starts the undo.
10. **New merge base.** Set the journal to `stage: "finishing"`. Write `T/merge_base_store.json` from the staged copy store, in live paths, and `T/merge_base_record.json` from the staged `project.json`. Then write `T/merge_base.json` with `from: "import"`, the record's `lineage` and the imported Put Back token.
11. **Records.** Append the `imports.jsonl` line (7.11), and add the copied folders' fingerprints to `T/laptop_conflicts.json`.
12. **Clean up.** Remove `W/incoming/` and `W/result/`, then the journal.

Release the locks, then `T/.import.lock`.

### 7.6 The integrity check

8.5 gives the checker a `--project` flag, and gives every per-project finding a `kind` and an `items` list. Items are names relative to the project folder (frame filenames, stems, uids, or relative paths), so runs on different roots compare. The import runs the checker as

`G/env/bin/python G/scripts/check_project_integrity.py --root <root> --project run_<id> --report-dir T/checks/<import_id>/<run> --json`

and reads the JSON from stdout, in three runs:

- **copy**: on the copy at the location (Stage 1, temporary reports), then on the staged snapshot (Stage 2 step 2), which is the one that counts;
- **before**: on the live project, at Stage 2 step 1;
- **after**: on the live project, at Stage 3 step 9.

Compare items by `(rule, level, kind, item)`:

- **New**: in `after` and not in `before`.
- **Inherited**: new, and also in `copy`. The laptop's work carries it; VEGA's own annotator would have made it here too. The dialog listed it, and the import record keeps it. It never undoes the import. R09 items are never inherited, because the import writes the registry itself.
- **Caused**: new and not inherited. Any caused item undoes the import, except the one R13 item naming the conflict file this import wrote.
- **Fixed**: in `before` and not in `after`. The import record keeps the list.

Exit 2 from a run refuses the import when nothing has moved, and undoes it otherwise.

### 7.7 The registry

8.6 adds `MaskRegistry.apply_frames` and `restore_rows`, which do for many frames, under one lock and one write, exactly what `_upsert_registry_for_frame` does for one.

1. Open `MaskRegistry(root=<parent>/_mask_registry)`.
2. Collect `frames = [(filename, merged_frame)]` for the `take` frames that R09 counts as exported (read `CPI` 1181-1261 for its rule).
3. Call `apply_frames(frames, project_id=<live id>, project_name=<live id>, dry_run=True)`. It returns `before`, the current row of every uid the real call will change or remove (`None` for a uid it will add). Write that to the journal as `registry_before`, with `stage: "registry"`.
4. Call `apply_frames(frames, ...)` for real.
5. Check that every mask's uid (`MR.build_registry_record(...)["uid"]`) now has a row whose `project_id` is the live `id`. A missing row starts the undo.

New rows get `created_at` at the import time, as VEGA's export gives them. The laptop's labeling time stays in the ledger's `at` and in the frame's `label_outcomes`. Because uids carry no project id (4.5), a row can already belong to another project with the same click and footprint. The write then hands it to this project, exactly as VEGA's own export would, and the import record counts these rows as `taken_over`. An undo gives them back (7.10).

### 7.8 The project record

Under `.project.json.lock`, read the live `project.json` fresh, never from staging:

- **Laptop access rows.** Compare rows by `ts`, `initials`, `action` and `purpose`, ignoring `source`. The laptop's rows are the copy's rows that the base record lacks; with no base record, they are the rows whose `ts` is later than `source.published_at`. Add each one VEGA lacks with `"source": "laptop"`, then sort all rows by `ts` with a stable sort. 8.3 makes `reviewer_for_state` and the Matrix's `project_identity` skip `source: laptop` rows, so a laptop row never becomes VEGA's reviewer or "opened by". `CPI` R01 still passes, because these rows keep `ts`, `initials` and `action`.
- **No import row.** The import writes no access row. The processing log and `imports.jsonl` record it.
- **Description.** With a base record, take the copy's `description` when VEGA's equals the base's and the copy's differs. When both changed, keep VEGA's and report it. With no base record, take the copy's only when VEGA's is empty.
- **Custom imports.** Merge `custom_imports[]` by `filename`, keeping VEGA's on a clash.
- Leave every other key as it is.

Write the record with `PM`'s atomic contract (`PM` 435-473), keeping the file's mode.

### 7.9 The export location is read, never written

The import writes nothing at the export location: no history row, no marker, no file. Both sides of that folder sync two ways (3.1). A write from the mainframe while a laptop that kept holding the project puts back again could win the sync. Synology would then rename the laptop's Put Back record aside, the import would see nothing new, and the laptop's next Put Back would go to `conflicts/`. `T/imports.jsonl` is VEGA's record of what was imported. Export's refresh writes the `vega_took_back` rows into the new record it builds (8.1), when no laptop holds the copy.

### 7.10 The journal, finishing, and undoing

`T/import_journal.json` passes through `moving`, `registry` and `finishing`. A run of the tool, or `/runs`, that finds a journal with no live import treats the import as stopped partway. `--finish` resolves it:

- **`moving`**: undo. Put back every replaced file from `W/before_import/`. Move every file the import created to `W/rolled_back/`. Restore `project.json` and the merge base files from their backups.
- **`registry`**: undo the files as for `moving`, then call `restore_rows(registry_before)`. That puts back the exact row of every uid the import changed and removes every uid it added, under the registry lock, whatever else has written since.
- **`finishing`**: complete Stage 3 steps 10 to 12, which are safe to repeat.

An undo after a failed check does the same. It never touches the export location, and it appends an `imports.jsonl` line with `outcome: "rolled_back"`.

### 7.11 What the import records

**`T/imports.jsonl`**, one line per finished or undone import:

```
{"import_id","at","by","purpose","location","copy_rev",
 "put_back":{"token","at","computer","initials","checksums_sha256"},
 "put_back_proof":"checksums" | "confirmed by <INI>",
 "baseline":"exact" | "rebuilt" | "hashes only",
 "frames":{"take":n,"keep":n,"unchanged":n,"conflict":n},"custom_images":n,
 "conflict_file":"segmentations.conflict-<stamp>.json" | null,
 "laptop_conflicts":{"copied":[folder names],"still_arriving":[folder names]},
 "classes_added":[codes],
 "registry":{"written":n,"pruned":n,"taken_over":n},
 "integrity":{"inherited":[items],"fixed":[items],"caused":[items]},
 "outcome":"imported" | "rolled_back",
 "working_folder":"_superseded/<stamp>"}
```

The line's `put_back.token` is the key every other part of VEGA uses to ask "was this Put Back imported?"

**Processing log.** The `import-run` route writes one row through `MV._append_processing_log_row`, as `export-run` does. The status is `import_project`, `import_project_failed` or `import_project_rolled_back`; the purpose is `<purpose> [<INI>]`; the notes hold a one-line summary. The CSV's columns are `timestamp,module,origin,purpose,status,duration_s,source_uri,destination_uri,notes` (`_LOG_CSV_COLUMNS`, `MV` 2792-2802; the writer is at `MV` 2828).

### 7.12 Concurrency

- `PT.begin_export` and `end_export` become `begin_transfer(key, kind)` and `end_transfer(key)`, so an export and an import of one project exclude each other in VICARIUS.
- The tool takes `T/.import.lock` with `flock(LOCK_EX | LOCK_NB)` for Stages 2 and 3, which also stops two command-line imports.
- Export refuses while `T/import_journal.json` exists or the import lock is held (8.1).
- The orchestrator refuses to start an annotator on the project while an import is partway (8.4). VICARIUS cannot stop the orchestrator's `/api/project/open`, but Stage 3 rechecks under the locks, and 8.4 makes `save_project` keep what the import wrote, so a late open loses nothing.

## 8. Changes to existing code

### 8.1 Export project's tool, `EXP`

1. Add `_transfer` to `EXCLUDED_DIR_NAMES` (`EXP` 53).
2. Add `export_id`, a random 32-hex id, to the record's `source`.
3. **Write the merge base.** After the copy is in place at the location, write `T/merge_base_store.json` and `T/merge_base_record.json` from the staged store and the staged `project.json`, each through a temp file. Then write `T/merge_base.json` with `from: "export"` and the new `lineage`. The base comes from the staged copy for the same reason `frame_hashes` do (`EXP` 464-466). A failure to write it is a `log_note`, never a failed export, and the next import then rebuilds the base or uses hashes only.
4. **Keep the history.** With `--update`, the new record keeps the old record's `history`, adds a `vega_took_back` row for each `imported` line in `T/imports.jsonl` whose token the history lacks a row for, then appends the new `publish` row.
5. **Read the import log.** `holds_laptop_work(state, imported_tokens=())` also returns False when the newest `put_back` row's token is in `imported_tokens`, which `publish` reads from `T/imports.jsonl`.
6. Refuse while `T/import_journal.json` exists or `T/.import.lock` is held.
7. Write `checksums_sha256` (the SHA-256 of the `checksums.json` bytes) into the record, so a later VEGA Annotator can prove a copy fully arrived before Bring In (14).

### 8.2 VICARIUS: `PT`, `MV`, `ML`

- `PT`: the states and order in 6.1; `begin_transfer` and `end_transfer` (7.12); one `/proc` scan and one orchestrator call per `/runs`; and `run_import(script, run_dir, location, by, purpose, check_token, confirmed)`, which mirrors `run_export`.
- **The refresh path.** `export-run` refuses every state but `none` today (`MV` 4670-4671), the page turns Export off in every other state (`ML` 615), and `run_export` never passes `--update` (`PT` 448-449). Let `export-run` accept the `imported` state and run the tool with `--update`, turn Export on in that state, and pass `--update` whenever a copy exists.
- `MV`: the routes in section 9, the Archive refusal (6.4), and the state fields in `/runs`.
- `ML`: the IMPORT PROJECT and FINISH IMPORT buttons beside `exportButton`, the chips, the check dialog (6.2) and the OPEN warning (6.4).
- Tidy in the same files, from the Export review:
  - remove `PT._within_timeout` (196-218), which has no caller;
  - prune `PT._card_cache` entries for runs `/runs` no longer lists;
  - correct `MV`'s "14-column" comment on the processing log (`MV` 2791), which has nine columns;
  - make `MV._stamp_project_access` keep the old file mode, as `PM` does, instead of leaving new files at 0600.

### 8.3 Who opened it last

In `PM.reviewer_for_state` (`PM` 683-699) and `MB.project_identity` (`MB` 161-185), skip rows whose `source` is `laptop`. Fix the stale docstring at `PM` 15-31, which describes `_stamp_project_access` wrongly and places it at "line 1674"; it now sits at `MV` 2859.

### 8.4 The orchestrator

- **Keep what others wrote.** In `PM.save_project` (`PM` 959-996), under the lock it already takes, reread the file on disk before writing. Union its `access_log` rows into the in-memory list (compared by `ts`, `initials`, `action`, `purpose`), and its `custom_imports[]` by `filename`. Keep the disk's `description` when the in-memory one still equals the value it loaded; record that value at load. This fixes the loss in 4.7 for VICARIUS's own access rows as well as the import's.
- **No annotator during an import.** The four places that start an annotator (`APP` 5498, 5540, 5929, 6249) refuse while `T/import_journal.json` exists or `T/.import.lock` is held: "An import is running on this project. Try again in a minute."
- Test both. Restart the orchestrator with `G/scripts/restart_reef_point_seg.sh`, never while an annotator is live (check `ss` for ports 5080 to 5089 first).

### 8.5 The integrity checker, `CPI`

- **`--project <run folder name>`.** With it, `run_checks` keeps only that folder (`CPI` 1599-1601). The rules that look across projects (R10, R11, R12, R14, R15) are skipped and reported as one INFO line, "skipped with --project". R09 compares only registry rows whose `project_id` is that project's id. Without the flag, nothing changes.
- **Items.** Give every per-project finding (R01, R04 to R09, R13) a `kind`, a stable name for its message template, and an `items` list holding every frame, stem, uid or relative path behind its count, sorted. Items are relative to the project folder. They appear in the JSON only; the text report is unchanged. The messages are built at `CPI` 960-1024, 1039-1106, 1143-1170 and 1181-1261, among others.
- Add tests to the checker's suite for both, and a test that fills `T` and `W` and finds nothing new.

### 8.6 Shared helpers

- `G/scripts/_reefreview/store_io.py` (7.0), `G/scripts/_reefreview/transfer_paths.py` (3.3), and `EY.data_yaml_text` (7.0), each with a suite.
- **`MaskRegistry.apply_frames(frames, project_id, project_name, reviewer='', prune=True, dry_run=False)`**: for each `(filename, seg)`, build a record per mask (the mask's reviewer, else `reviewer`), upsert each with `upsert`'s merge rule, and prune the frame's other rows for `project_id` when every mask was recorded and `prune` is true. All of it happens under one lock with one load and one write. It returns `{"before": {uid: row or None}, "written": n, "pruned": n, "taken_over": n}`. With `dry_run`, it computes the same and writes nothing. Factor `upsert`'s merge rule (`MR` 215-239) into one function that both use.
- **`MaskRegistry.restore_rows(before)`**: under the lock, put each row back, or remove the uid when its row is `None`, in one write.
- `EY._upsert_registry_for_frame` becomes a thin wrapper over `apply_frames` for one frame. It keeps its run-project rule (passed as `prune`) and its never-raise wrapper, so its behavior and its suites do not change.

### 8.7 VEGA Annotator 1.0.1

- `_send_work` reads back the `checksums.json` it just wrote and puts its SHA-256 in the `put_back` row as `checksums_sha256` (`LAP` transfer.py 900-913).
- A conflict folder's `conflict.json` gains `file_hashes: {rel: {size, sha256}}` (`LAP` transfer.py 846-860).
- Add `_transfer` to `EXCLUDED_DIRS` (`LAP` transfer.py 52).
- **Re-copy VEGA's code after 7.0 and 8.6 land.** The annotator will then import `scripts/_reefreview/store_io.py`, which `tools/sync_from_vega.py` (44-60) does not list. Add it, re-copy, and run the laptop's parity and round-trip suites; otherwise the laptop's annotator cannot start.
- Raise `VERSION` in `vega_annotator/__init__.py`. Build, test on the Windows runner (`.github/workflows/windows.yml`), and release 1.0.1 on GitHub with the Mac package and the Windows zip, as 1.0.0 was. Publish the same installers to the server folder with `tools/publish_to_server.sh`, following the README's "Publish to the server folder" section.

## 9. Server routes and tool interface

### 9.1 `GET /api/modules/<name>/import-check?run_path=<abs>`

Refusals come in the order `export-run` uses (`MV` 4631-4685): 423 when locked, 404 when no `project_transfer` is declared, 400 for a bad `run_path`, the `_resolve_run_dir` errors, then 409 for each Stage 1 refusal with `{error: "<sentence>"}`. Success returns 200:

```
{"ok": true, "check_token": "...",
 "copy": {"rev", "computer", "initials", "put_back_at", "put_back_token", "held_by": null | "<computer>",
          "proof": "checksums" | "needs confirmation"},
 "baseline": "exact" | "rebuilt" | "hashes only",
 "frames": {"take": [...], "keep": n, "unchanged": n, "conflict": [...]},
 "custom_images": [...], "masks": n, "frames_left": n, "classes_added": [...],
 "laptop_conflicts": {"to_copy": [...], "still_arriving": [...]},
 "copy_findings": [...], "description": null | {"kept": "vega" | "laptop", "text": "..."},
 "not_imported": [...], "replace": {"files": n, "bytes": n}, "free_bytes": n}
```

The route runs the tool with `--check --json` under a 300 s timeout.

### 9.2 `POST /api/modules/<name>/import-run`

The body is `{run_path, initials, purpose, check_token, confirmed_arrival}`. Validation matches `export-run`: initials must match `^[A-Za-z]{1,4}$` (`MV` 4194), and a purpose is required. `confirmed_arrival` is required only when the check reported `"needs confirmation"`. The route runs the tool with `--run`. 200 means imported. 409 carries `{error, rolled_back: true | false, report}`.

Follow `export-run`'s pattern for the request. If `export-run` holds the request open for the whole export, do the same with a 3600 s timeout. If the server's worker timeout is shorter than an hour, run the tool in a background thread instead, and let the card read `importing` from `/runs`.

### 9.3 `POST /api/modules/<name>/import-finish`

The body is `{run_path, initials}`. It runs the tool with `--finish` and returns the tool's report.

### 9.4 The tool

```
import_project.py --project <run dir> --location <location> --by <INI> --purpose <text>
                  (--check | --run --check-token <t> [--confirmed-arrival] | --finish)
                  [--json] [--proc-root /proc] [--orchestrator http://127.0.0.1:5050]
```

- Exit 0: checked, imported, or finished. Exit 1: refused, with one sentence on stderr. Exit 2: undone, with the finding on stderr.
- `--proc-root` and `--orchestrator` exist so tests never touch a live process.

## 10. Failure modes

| What happens | When | Result | What the person sees |
|---|---|---|---|
| The record arrives before the rest of a Put Back | Stage 1 step 3 | Refused | "The copy is still arriving at the mainframe..." |
| A sync is still writing files | Stage 1 step 4, Stage 2 step 2 | Refused | "<file> differs from the copy's own list..." |
| A laptop conflict folder is half synced | Stage 1 step 8 | That folder waits; the rest imports | "1 folder of laptop work is still arriving..." |
| A Put Back starts during the import | Stage 3 step 1 | Refused, nothing moved | "The project changed during the check..." |
| Someone opens the project in VEGA's pipeline window mid-import | Stage 3 step 1, or later | Refused before moving; after moving, its saves keep the import's rows (8.4), and it cannot start an annotator (8.4) | "VEGA's pipeline window has this project loaded..." |
| The admin moved the location, so revisions restart at 1 | Stage 1 step 2 | Imported normally, because imports key on the Put Back token | Nothing unusual |
| The disk fills during staging | Stage 2 | Staging removed, live untouched | "This needs N GB free, and the disk has M GB." |
| Power is lost while files move | Stage 3 | Journal at `moving` | "Import stopped partway"; FINISH IMPORT restores the backups |
| Power is lost during the registry writes | Stage 3 step 8 | Journal at `registry` | FINISH IMPORT restores the files and the exact registry rows |
| `reconcile_export` fails | Stage 3 step 7 | Undone | "import undone: all_images and the manifest disagree" |
| A registry row is missing after the writes | Stage 3 step 8 | Undone | "import undone: the registry lacks N masks of <frame>" |
| A caused integrity item | Stage 3 step 9 | Undone | "import undone: <rule> <item>" |
| The laptop's copy already had an integrity item | Stage 1 step 9 | Imported; the item comes across | Listed in the check dialog |
| A label line's class is missing from the laptop's class map | Stage 2 | Refused | Names the stem and the class |
| A picture is missing in both places | Stage 1 | Refused | Names the frame |
| The integrity checker cannot run | Any of the three runs | Refused before moving, undone after | "The integrity checker could not run: <stderr line>" |

## 11. Tests

VEGA's house style: no pytest. Each suite is a script with a Module, Purpose, Inputs, Outputs docstring that prints `PASS <name>` per test, runs on `G/env/bin/python`, and is listed in `SUITES` in `G/scripts/run_all_tests.sh`. A Python suite on disk that `SUITES` lacks fails the board. Every suite installs `G/scripts/tests/_isolation.py`. That guard covers the test process only, not the tools it starts, which is why the import tool passes its registry root explicitly (7). Pass fakes through `--proc-root` and `--orchestrator`; no test may start or reach a real annotator (`G/scripts/pipeline_orchestrator/tests/test_reviewer_env.py` 56-88 shows the stubs). VICARIUS tests use pytest in `UI/.venv`, following `UI/tests/test_project_transfer.py`.

Build fixtures the way the laptop app's tests do (`LAP` `tests/helpers.py`, `make_project` and `publish_to`): a small real-format project under a scratch `inprocess` root with its own `_mask_registry`, an export location, and a copy put back by the laptop's real `transfer.put_back`. Vendor `transfer.py`, `pathmap.py` and `projects.py` from `LAP` 1.0.1 into the fixture folder, pinned by commit, so the tests exercise the real Put Back.

**`G/scripts/tests/test_import_project.py`**
- Each row of the table in 7.3, alone and mixed, with exact, rebuilt and hashes-only bases.
- Untouched frames: a project exported with no store, labeled on the laptop while VEGA's annotator saved its own empty frames, takes every laptop frame and sets nothing aside.
- The merge base: a second import after more laptop work, with no export between, takes the second round's frames. An Export refresh resets the base.
- A frame with an expert review that VEGA gained after the export, also changed on the laptop, goes to the conflict file.
- A class new on the laptop gets the next free id, and its label lines are rewritten. A class new in VEGA keeps its id.
- The manifest union, the species column order, and the `image_path` rewrite.
- The ledger equals what `write_frames` makes from the taken frames' `label_outcomes`. Expert rows stay. A frame scrapped on the laptop loses its old rows.
- A custom image imported on the laptop arrives with its file, its manifest entry, its store frame, its `custom_imports[]` row, and an `all_images` link.
- Complete `conflicts/` folders arrive in `W/laptop_conflicts/`; a half-synced one waits; a second import skips a copied one; a changed one is copied again.
- The registry rows equal what `_upsert_registry_for_frame` writes for the merged frames. Prune removes only this project's stale rows. A uid shared with another project counts as `taken_over`, and an undo gives it back.
- Laptop access rows get `source: laptop`, and `reviewer_for_state` and `project_identity` skip them. A second import adds no duplicate rows.
- The description rules, with and without a base record.
- A Put Back with keep holding imports while `out` stays set.
- A copy whose revision restarted at 1 after a location change imports, because its token is new.
- **The live registry never changes.** Run a whole import on a scratch copy of `run_26-10-04_AT`, which has the same id as a live project, and show that `IN/_mask_registry/manifest.csv` keeps its SHA-256.
- **Nothing reads `T` or `W`.** With both full, the Matrix builder, the integrity checker and Choose Images give the same results as with both empty.

**Adversarial**
- A stop after each move in Stage 3, then `--finish` undoes or completes; a stop between two registry writes, then `--finish` restores the exact rows.
- The live store, manifest, ledger or class map changes between the check and the run.
- A record newer than its `checksums.json`; a `checksums.json` newer than its files; a 1.0.0 Put Back without and with the confirmation.
- `put_back` set mid-import; `format: 2`; a different `project_id`.
- Hostile paths in `checksums.json`: absolute, `..`, backslashes, a symlink in the copy.
- A full disk at staging.
- Two imports at once; an import during an export; an export during an import; an annotator start during an import.
- The orchestrator reports the project loaded, refuses the connection, or times out.
- `annotator_on_project` finds a fake process.
- A caused item in the after-check undoes; an inherited one does not; the excused R13 item does not.

**Integration**, on a scratch copy under a throwaway root, never a live project:
1. Export a copy of `run_26-10-04_AT` from a scratch `inprocess` to a scratch location.
2. Bring it in and label 10 frames with VEGA Annotator's real flow, as its `tests/test_round_trip.py` does, then put it back.
3. Import.

Then check that:

- `check_project_integrity.py --project` reports no caused item;
- the label files match a VEGA-native export of the same masks;
- the scratch registry holds one row per mask;
- exported plus scrapped plus left equals routed;
- `pinned_split.py` builds its split;
- the Matrix shows the frames as found or not_found by the laptop's reviewer.

Repeat steps 2 and 3 once more to prove the second-round merge.

**`CPI`**: `--project` limits the run as 8.5 says, items are complete and relative, and the text report is unchanged.

**`PM` and `APP`**: `save_project` keeps a row and a description written on disk by another writer; an annotator start refuses while an import journal exists.

**`MR`**: `apply_frames` gives the same rows as repeated `_upsert_registry_for_frame` calls; `dry_run` writes nothing; `restore_rows` puts back exactly the rows it was given.

**VICARIUS** (`UI/tests/test_project_transfer.py`): `import-check`, `import-run` and `import-finish` with every refusal code; the card states and chips in 6.1; the refresh in the `imported` state; the Archive refusal and the OPEN warning; the admin gate unchanged. Take screenshots at 946 and 1280 px in each new state, and answer the seven questions of section 8a of the workflow page design for each.

**VEGA Annotator 1.0.1**: a Put Back row carries `checksums_sha256` equal to the SHA-256 of the written `checksums.json`; `conflict.json` carries `file_hashes`; `_transfer/` never travels; the re-copied annotator starts. Run the full suite on Mac and on the Windows runner.

**Regression**: VEGA's full board, the annotator's suites after the 7.0 extraction, and the VICARIUS suites named in the Export plan.

## 12. Deliverables that close the batch

Per the "Every change batch" section of `/mnt/rip/vicarius_drive/CLAUDE.md`:

1. **Changelog.** The VICARIUS changelog line, and the module changelog entry in `G`, newest first, with a `Rationale:` line.
2. **Data dictionary.** Descriptors with `status: draft` and `schema_version: "1.0.0"`: `reef_point_seg_transfer_record` (`vega_annotator.json` and `checksums.json`), `reef_point_seg_merge_base` (the three `T/merge_base*` files), `reef_point_seg_import_log` (`T/imports.jsonl`), and `reef_point_seg_label_provenance` (`label_provenance.csv`, which has none today). Add `outputs:` entries in `G/module.yaml` with `eml: false`.
3. **Schema discipline.** Access rows gain the optional `source` field. Bump `schema_version` in `reef_point_seg_training_project.yaml`, the only descriptor that names `access_log`, as a MINOR change. The integrity checker's JSON gains `kind` and `items`; note it in the checker's own docstring and in the nightly report's reader, if any.
4. **ADRs.** Add records under `G/docs/adr/` for the frame rule in 7.3, the merge base in 5, the token keys, and the `source: laptop` rows, each with a `Source:` line naming this spec and the to-do uid.
5. **Glossary.** Add `CONTEXT.md` entries for Export project, Import project, export location, copy record, merge base, Put Back token, and work to import.
6. **Student manual.** VEGA's student manual is a file under `vicarius/_DOCS/`, not a symlink. Add a short "Labeling on a laptop" section.
7. **To-do and note.** Close this to-do, update `T20261102-164635`, and write a plain-language note in `vicarius/_METADATA/notes/LO/`.
8. **Restarts.** Restart the UI with `vicarius fresh --ui-only`. Restart the orchestrator for 8.3 and 8.4 with `G/scripts/restart_reef_point_seg.sh`, never while an annotator is live.
9. **Laptop release.** VEGA Annotator 1.0.1 on GitHub and in the server folder (8.7).

## 13. Calls for Lauren

These are the defaults this spec builds. Put each to Lauren, one question at a time, before writing code, because each one changes what gets built.

1. **A frame changed on both sides** keeps VEGA's version, and the laptop's goes to VEGA's conflict file, which only the integrity checker reads today. An expert review that reached VEGA's copy counts as a change.
2. **Laptop access rows** carry `source: laptop`, and VEGA's reviewer and the Matrix's "opened by" skip them.
3. **Import refuses** while VEGA's pipeline window has the project loaded (until someone clicks Save and close there), while an annotator has it open, and while a Put Back is partway. It accepts a project the laptop still holds after a keep-holding Put Back. The other choice is for Import to close the pipeline window's project itself, which would also close it for whoever has that window open.
4. **The integrity check** undoes an import for any new problem item it caused. Items the laptop's own copy already had come across with the work, after the check dialog lists them.
5. **Laptop conflict folders** are copied into the project for a person to review, never merged.
6. **The import never writes at the export location.** Export project refreshes the copy when Lauren chooses, which uploads the whole project again (about 0.6 GB for 800 frames).
7. **Anyone who can use the page** and gives initials can import, as with Export. Making it admin-only is one line (`admin_session.require_admin`).
8. **A Put Back from VEGA Annotator 1.0.0** imports only after someone ticks that Synology Drive on the mainframe shows the folder up to date. From 1.0.1 the laptop proves it.

## 14. Open items outside this batch

- **Bring In's proof of arrival.** The laptop's Bring In has the same out-of-order risk in the other direction. 8.1 item 7 puts `checksums_sha256` in the record so a later VEGA Annotator can check it before Bring In.
- **The laptop's view of an import.** VEGA Annotator could show "VEGA imported this on <date>", but only after a refresh brings a `vega_took_back` row, since the import writes nothing at the location.
- **Expert reviews on frames that are out.** If the expert review bundle (`A` 4412-4480) is switched back on, a later batch could fold expert-only changes into the laptop's frame instead of setting it aside.
- **Registry `created_at`.** Rows written by an import carry the import time. If STATS ever charts by `created_at`, a later batch can carry the laptop's labeling time through.
- **Edit Masks' merge code** stays separate from `store_io` (7.0).
- **Relative image links** in `run_26-10-04_AT` (4.4): nobody has named the tool that made them on 15 Oct 2026.
- **`EXP.annotator_on_project`** has no positive test; add one when the import's tests add the fake `/proc`.
