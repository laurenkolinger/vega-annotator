# VEGA Annotator build plan, 2 Nov 2026

## Purpose

A student labels TCRMP frames in VEGA's annotator, and waits whenever the mainframe is busy. VEGA Annotator runs that same annotator on a MacBook or on the Lenovo Legion, with no mainframe. The student connects a server folder to the computer, brings a project in, labels it, and puts it back.

## Inputs and outputs

- Input: a VEGA project folder in a server folder, `VEGA_Annotator/projects/run_<id>/`, written there by `tools/publish_project.py` on the mainframe, and the SAM3 weights in `VEGA_Annotator/sam3/`.
- Output: the same project folder back in the server folder, with the student's masks, YOLO labels, ledger, manifest, registry rows, and access log entries, in VEGA's own formats.

## Decisions

1. **VEGA's code runs unchanged.** The annotator's `app.py`, `index.html`, `sam_engine.py` and helpers sit in `vendor/vega/` byte for byte, with the VEGA commit recorded. A thin layer starts them with laptop settings. A parity test fails if a vendored file drifts. This is what makes the screen, the keys, and the file formats match.
2. **The server folder is the only route.** There is no Dropbox route. The app reads and writes any folder she connects: an SMB share or a Synology Drive folder.
3. **VEGA makes projects, the laptop annotates** (Lauren's answer, 2 Nov 2026). The laptop holds only the project's frames, survey points, and label list. It needs no `all_points.csv`, no clip library, and no point-reading software.
4. **Project management stays VEGA's.** Each project keeps its `run_<id>` folder, `project.json`, step statuses, and access log. Opening a project asks for initials and purpose, as the VICARIUS launcher does, and writes the same `{ts, initials, action, purpose}` entry. Masks are credited to those initials. Bringing in and putting back are logged as access log entries too. The home screen copies the launcher's project cards and the main window copies VEGA's rail.
5. **One computer holds a project at a time.** Bringing a project in records the computer and initials in the server copy, and another computer sees "In use on ...". A take over is a deliberate, confirmed step. Two versions of the same labeling work are never merged or overwritten: when the server copy moved on, Put Back saves the laptop's copy beside it under `conflicts/`, and that computer can then bring in the server's copy or remove its own. Access log rows are the one thing that combines, so a replaced `project.json` keeps every row, and a description carries over only when the incoming record has none.
6. **SAM3 settings by computer, from measurements on 2 Nov 2026 (M3 Max, VEGA's engine, 8 of AT's masks, 99 percent overlap with the mainframe's masks):**
   - Mac with 24 GB or more: tracker on the Mac GPU at bfloat16 (1.4 s per frame, about 9 GB), Find similar on the CPU.
   - Mac under 24 GB: both on the CPU at float32 (3.6 s per frame on 4 threads of an M3 Max, 2.2 GB). The GPU path ran out of memory under a simulated 8 GB limit, and Find similar ran out under a simulated 16 GB limit.
   - Windows with an NVIDIA card of 4 GB or more: tracker on the card at bfloat16, as VEGA does. Find similar joins it at 12 GB or more, and uses the CPU below that.
   - Anything else: CPU at float32. bfloat16 on the CPU took 67 s per frame and is never used there.
   - Intel Macs cannot run PyTorch 2.11, and setup says so.
7. **Weights come from the server folder**, with Meta's license beside them. The person labeling needs no Hugging Face account, and the app never goes online for the model.
8. **Local only.** The servers listen on 127.0.0.1 and refuse requests from any other host or web page. VEGA's own launch listens on every network interface, which is wrong on a laptop.
9. **Frames left.** The thin layer adds a visible "415 left" readout beside the frame counter, VEGA's page file stays unchanged. The black-frame defect comes from slow or missing pictures. Bring in checks every picture and the pictures are on local disk, so the page fix stays on VEGA's list.
10. **Nothing is built in VEGA.** The VEGA side of the round trip is listed in the mainframe's to-do log, entry `T20261102-164635`.

## Bad input

- A project folder with a missing or corrupt `project.json` or `segmentations.json` is listed with its reason and cannot be brought in.
- A copied file whose checksum differs stops the copy, and the laptop's working copy stays as it was.
- A server folder that disconnects mid-copy leaves the server copy intact, because files arrive under a temporary name and move into place only after a check.
- Initials must be 1 to 4 letters, as in VEGA.

## Files

- `vendor/vega/`: VEGA's files, unchanged, with `VENDORED.json`.
- `vega_annotator/`: `paths.py`, `settings.py`, `device.py`, `shims.py`, `annotator_boot.py`, `projects.py`, `pathmap.py`, `transfer.py`, `catalog.py`, `atomic.py`, `sam3_files.py`, `self_test.py`, `home.py`, `launcher.py`, `templates/`, `static/`.
- `requirements/`: pinned to VEGA's `requirements.lock`.
- `packaging/mac/`, `packaging/windows/`: installers, following Sponge Lab's pattern.
- `tools/`: `sync_from_vega.py`, `publish_project.py`, `publish_sam3.py`, `publish_to_server.sh`.
- `tests/`: below.

## Tests

Automated, in `tests/`, run with `pytest`:

- Unit: one module per file in `vega_annotator/` and `tools/`: `test_settings.py`, `test_device.py`, `test_shims.py`, `test_pathmap.py`, `test_projects.py`, `test_transfer.py`, `test_catalog.py`, `test_home.py`, `test_annotator_boot.py`, `test_publish_project.py`, `test_sam3_files.py`, `test_packaging.py`.
- Adversarial: `test_transfer_adversarial.py`: a connection dropped mid-copy, a checksum mismatch, a read-only server, a full disk, hostile file names, paths outside the project, corrupt JSON, duplicate names, two computers claiming at once, a stop at every step of a move, a save arriving during a read, and the home screen's refresh arriving during a move.
- Integration: `test_annotator_real.py` runs the real annotator with the real SAM3 weights through its web routes, on the CPU and the Mac GPU. `test_round_trip.py` publishes a project, brings it in, labels through the web routes with VEGA's real code, exports, puts it back, and brings it in on a second computer, with a stand-in for SAM3. `test_e2e_chrome.py` walks the whole click path in a real Chrome with the real weights.
- Parity: `test_vendor_parity.py` checks every vendored file against `VENDORED.json`. `test_round_trip.py` checks a label file against the output of VEGA's own label writer.
- Pixels: `test_pixels.py` compares the annotator page with VEGA's own page in a real Chrome at 946 and 1280 px, and only the frames-left readout may differ.

Run by hand, with the results in the final report:

- VEGA's own annotator suites against the vendored copy, and its layout suites in Chrome.
- VEGA's `check_project_integrity` on a scratch merge of a put-back project.
- Screenshots of the home screen and the project window at 946 and 1280 px, read as images against the seven questions.

Still to run: the whole non-GPU suite on Windows through `.github/workflows/windows.yml`, once Lauren agrees to a private repository, and a first real run on the Legion with the app's Test SAM3 button. No Windows machine has run this app yet.

## Review

One independent reviewer agent reads the code against this plan, runs the tests, and takes its own screenshots. Defects are fixed, one re-review follows, and notes go into the report as open items.
