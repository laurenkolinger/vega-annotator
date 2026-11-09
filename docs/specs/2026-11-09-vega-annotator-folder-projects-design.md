# VEGA Annotator 1.1 for Mac: any folder, any labels, SAM3 in setup, 9 Nov 2026

**Status.** Approved by Lauren on 9 Nov 2026 and built as VEGA Annotator 1.1.0. One change after approval, 9 Nov 2026: HEIC, HEIF and WebP photos become JPEG at quality 95 instead of PNG, so an iPhone photo stays about its own size instead of growing several times as a PNG kept as the project's original; TIFF, BMP and GIF still become PNG.

## Why

Lauren wants to share VEGA Annotator with a student whose project sits entirely outside VICARIUS: his own photographs, and labels that are not taxa, such as healthy, bleached or diseased, for health and condition diagnostics. He labels on his MacBook, and his finished images and masks must train in VEGA's Train Model window. Today the app opens only projects that VEGA exported, offers only the labels those projects carry, and installs SAM3 only from Alexandria's server folder.

## Decisions

Lauren's, 9 Nov 2026:

- SAM3 downloads from the lab's GitHub release, in the same setup run as Python and PyTorch.
- The release is for Macs. Windows is not built or tested this time.
- The server connection stays, for Alexandria.

Mine, from the code (each can be overruled):

1. **A folder project is an ordinary VEGA project folder** (`run_<id>`). The app's cards, OPEN, and VEGA's annotator then work as they do now.
2. **Images come in through VEGA's own import code**, the `custom_imports` module behind VEGA's Annotate Images. Every frame is a VEGA custom-import frame: the original is kept, a working JPEG is made, and an image wider than 1920 px on its long edge is reduced to 1920 px for labeling, as VEGA does.
3. **The student's labels become the project's label list.** VEGA's annotator already treats a label as a plain word: it labels, exports, logs and counts any string (checked in the vendored code on 9 Nov 2026).
4. **The hand-over is a folder shaped like one of VEGA's Annotate Images sessions.** VEGA's decision records 0001 and 0006 already make such a session a Train Model source that never enters the Coverage Matrix, STATS or the mask registry, so a word like "bleached" can never be counted as a species. The folder goes into `inprocess/_standalone/` on the mainframe and appears in Train Model's list.
5. **VEGA gains one small piece, built on the mainframe.** Train Model splits into the open project's own `dataset/` and reuses that project's saved split (`pinned_split.py` 512-533; `app.py` 6760-6764). Training a student folder inside an existing project would therefore overwrite that project's split, and leave the student's hold-out empty or be refused. VEGA needs a TRAIN ON THIS action that makes a separate training project for one Annotate Images session, as it already does for composites and box datasets. That piece goes to the mainframe as a handoff on its Desktop, linked in a VEGA to-do; this build does not change VEGA.

## What the student does

1. Downloads `VEGAAnnotator-1.1.0.pkg` from the GitHub release, opens it, and accepts Meta's SAM License.
2. Opens VEGA Annotator. It downloads Python, PyTorch and SAM3 in one run, about 4.5 GB, and says each step.
3. Clicks **NEW PROJECT**, picks his folder of images, names the project, types his labels, and gives his initials. The app shows how many images it found and the space they need, then copies them in.
4. Clicks **OPEN** and labels in VEGA's annotator: a click or a box makes a SAM3 mask, keys 1 to 9 or the label buttons pick a label, and he accepts and exports batches, as in VEGA.
5. Uses **ADD IMAGES** and **LABELS** on the card whenever he needs more.
6. Clicks **EXPORT FOR VEGA**, picks a destination (a USB drive, a shared drive, any folder), and sends Lauren the one folder it writes.

Lauren copies that folder into `inprocess/_standalone/` on vicar-mainframe. Once VEGA's TRAIN ON THIS piece is built (decision 5), she trains it in its own training project.

## Part 1: SAM3 installs with Python

- **The release.** A release tagged `sam3-weights-1` on laurenkolinger/vega-annotator holds `sam3-hf.tar.part1`, `sam3-hf.tar.part2` (each under GitHub's 2 GB file limit) and `SAM_LICENSE.txt`. The tar holds exactly the server folder's `sam3/` copy: `models--facebook--sam3/` in Hugging Face's cache layout, `SAM_LICENSE.txt`, and `checksums.json`. The snapshot is commit `3c879f39826c281e95690f02c7821c4de09afae7`, and its `model.safetensors` is 3,439,938,512 bytes.
- **Pinned.** The app's source pins both parts' sizes and SHA-256 in `packaging/mac/sam3_release.json`, so a changed, partial or swapped download is refused.
- **Setup Step 4 of 4, "downloading SAM3 (3.4 GB)".** `packaging/mac/setup.sh`:
  - downloads both parts into the app's folder, resuming an interrupted download (`curl -C -`);
  - checks each part;
  - unpacks into a temporary folder beside the SAM3 folder;
  - checks every file against the copy's `checksums.json`, as INSTALL SAM3 does today;
  - moves the copy into place and deletes the parts.

  A free-space check before the step asks for 7.5 GB: the parts, the unpacked copy, and room to spare.
- **One downloader.** Step 4 runs the app's own Python module (`vega_annotator.sam3_download`) with the Python that Steps 2 and 3 just installed. The home screen's DOWNLOAD SAM3 button runs the same module, so a retry resumes where the setup stopped.
- **First open.** The dialog reads "Python, PyTorch and SAM3, about 4.5 GB". A Mac set up by 1.0, with Python ready and no SAM3, runs Step 4 only.
- **When the SAM3 download fails.** Python and PyTorch stay installed, the app opens anyway, and the home screen says SAM3 is missing, with DOWNLOAD SAM3 and INSTALL FROM SERVER FOLDER. The app never starts a 3.4 GB download on its own after that; the person clicks.
- **The license.** The installer package shows Meta's SAM License, and the person must accept it, because the license binds anyone who uses or passes on SAM3. The README credits SAM 3 and says that papers using it must acknowledge it, as the license requires.
- **The fallback.** INSTALL SAM3 from a server folder stays for a Mac that cannot download.

## Part 2: projects from any folder

- **The dialog.** NEW PROJECT sits beside CHOOSE FOLDER on the home screen. It asks for the image folder (the macOS folder picker), a project name, the labels, initials and a purpose.
- **Which files count.**
  - `.jpg`, `.jpeg` and `.png` come in as they are.
  - `.heic`, `.heif`, `.tif`, `.tiff`, `.bmp`, `.webp` and `.gif` are converted first with macOS `sips`, because VEGA's import takes only JPEG and PNG: HEIC, HEIF and WebP become JPEG at quality 95, and TIFF, BMP and GIF become PNG.
  - Subfolders are included.
  - Hidden files, files that do not open, and symbolic links are skipped and listed.
- **What the app makes.** A project `run_<YY-MM-DD>_<INI>_<name slug>` in its projects folder, where the slug keeps only lowercase ASCII letters, digits and underscores from the name, so the id also fits VEGA's session-name rule `^[A-Za-z0-9._-]{1,64}$` (`app.py` 1422). The project holds:
  - `project.json`: `id`, `name`, `description`, `initials`, `created_at`, an `access_log` with a `create` row, steps 1 to 3 recorded as `skipped` with step 3's `target_species` set to the labels, `label_set: {kind: "custom", labels: [...]}`, and `origin: {kind: "folder", folder, computer, made_with}`;
  - `step4test_combinedAnnotate/routed_input/ids/sam_click_prompts.json` holding `{}`, and an empty `routed_input/raw/`, so VEGA's annotator opens with no survey points;
  - `step4test_combinedAnnotate/master_codes_recoded.csv`, listing each label with the category `Label`, so no label is stored as `Coral`;
  - `class_map.json` seeded in the label order, so YOLO class ids follow the list;
  - `vega_annotator_local.json` marked `made_here`, so the card works with no server.
- **Bringing the images in.** VEGA's `custom_imports.ingest_files` copies the images, `seg_dict_for` makes their frames, and `record_in_project` logs them, exactly as VEGA's Import Images route does (`app.py` 1761-1814). The store is written atomically. Images are added only while the project is closed in the annotator.
- **Disk.** Each image is kept as its original plus a working JPEG. The dialog shows the total and refuses when the disk lacks that plus 1 GB.

## Part 3: any labels

- Labels are defined at creation, and LABELS on the card adds more later. LABELS and ADD IMAGES work only while the project is closed in the annotator, because the annotator reads its labels once at start and rewrites `class_map.json` on every save (`app.py` 139, 356, 1066-1067).
- A new label always gets the next class id. A label can be renamed or removed only until the project's first export: VEGA numbers classes as `len(class_map)` and writes `nc` as the count of names (`export_yolo.py` 41, 198), so removing a label after an export would leave a gap that training rejects.
- **Rules**, each refused with a plain sentence that says what to change:
  - 1 to 40 characters, starting with a letter or digit and containing a letter;
  - only letters, digits, spaces and `- _ . + ( )`;
  - no two labels that differ only in case;
  - none of the words VEGA reserves: review, unk, and the export manifest's columns (basename, n_masks, label_file, image_file, outcome, preview_file, batch_name, image_path);
  - the label must read back unchanged from YAML, because VEGA writes class names into `data.yaml` unquoted, and YAML turns words like yes, no, null and 0x1F into other values.
- Labels keep their exact spelling and case. Keys 1 to 9 pick the first nine.
- **The annotator page.** On a folder project, the app's own page add-ons (`annotator_extras.js` and `.css`, which VEGA's files never see) make two changes. They hide VEGA's reef shortcuts: the Sponge, Macroalgae, Gorgonian and Turf chips, and the keys s, m, g and t. They also show labels upright instead of in the italics VEGA uses for species names.

## Part 4: the hand-over to VEGA

- **What EXPORT FOR VEGA writes.** On a folder project's card, it asks for a destination folder and writes `<destination>/<project id>_<YYYYMMDD-HHMM>/`. Every hand-over is therefore a new training source with a name VEGA accepts, and two hand-overs never merge or overwrite each other. The folder is shaped like a VEGA Annotate Images session:
  - `data.yaml`, `all_labels/`, `class_map.json`, `export_manifest.csv`, `label_provenance.csv`, `export_log.txt`, `mask_status_log.jsonl`;
  - `all_images/` as real image files, never links, so the folder survives a zip file, a USB drive or a cloud drive;
  - `segmentations/` (the store and its overlays), `previews/`, and `custom_imports/` with its manifest and working JPEGs. Originals stay out unless the student ticks "Include originals", which shows their size;
  - `session.json`: name, created, initials, labels, frames exported, masks, the app version, and the computer;
  - `checksums.json` for every file;
  - `README.txt` for Lauren: "Copy this folder, unchanged, into inprocess/_standalone/ on vicar-mainframe. Train it in its own training project with TRAIN ON THIS. Never pick it as the source inside an existing project, which would overwrite that project's training split."
- It refuses, and says why, when no frame has been exported yet.
- **On the mainframe.** Lauren copies the folder into `inprocess/_standalone/`. VEGA's `_list_standalone_exports` (`app.py` 2106-2150) then lists it with its labels.

## VEGA side: one piece for the mainframe

These go to vicar-mainframe as a handoff on its Desktop, linked at the top of a new VEGA to-do. None of them is built here.

1. **TRAIN ON THIS** (needed). For one Annotate Images session, make a training project, as VEGA already does for composites and box datasets. Steps 1 to 4 are skipped, Step 6 is open, the project is locked to that session, and it gets its own fresh split, so no existing project's split changes.
2. **Small classes can drop out** (a note). Train Model counts a class only on images where it is the most common class, and drops a class seen that way on fewer than 10 images (`min_samples`, `pinned_split.py` 561-590). A condition label that usually shares an image with "healthy" can drop out. Count by presence, or lower `min_samples`, for these projects.
3. **Hold-out split** (a note). Names that are not TCRMP frame names get a random hold-out split, marked `transect-degraded` (`pinned_split.py` 922-944).
4. **Reviewing in VEGA** (a note). Reopening a package in VEGA's Annotate Images to review its masks needs its image paths rewritten. Training does not read them.

## The server connection stays

Alexandria's route is unchanged: CHOOSE FOLDER, Bring In, Put Back. A folder project shows no Bring In or Put Back.

## Mac only

Release 1.1.0 ships the Mac package only. The Windows workflow runs on demand only. Release 1.0.0 stays on the release page for Windows.

## Tests

- **Unit and adversarial:**
  - each label rule, plus Unicode, HTML, quotes and YAML words;
  - the folder scan: empty folder, no images, mixed formats, nested folders, hidden files, symbolic-link loops, an unreadable file, duplicate names, zero-byte files, thousands of files;
  - project creation: name clashes, an existing folder, a full disk;
  - the image import through VEGA's code: store written, frames added, project record logged;
  - ADD IMAGES while closed, refused while open;
  - the package: real files, checksums, the refusal with nothing exported, include originals;
  - the SAM3 step against a fake local release server: good parts, a corrupt part, a short part, resuming after a cut, no space, and a 1.0 Mac with Python ready.
- **Integration on this Mac, with real SAM3.** A folder of 6 images in 4 formats and 3 custom labels, masks made by clicks through VEGA's own routes, two batches exported, then EXPORT FOR VEGA. Check that the package's data.yaml names, class ids, label lines and `all_images` agree.
- **The VEGA-side check, on vicar-mainframe.** Those two functions import code that only the mainframe has (`app.py` 27-88; `pinned_split.py` 76-78), so this part runs there, in a scratch folder under `/mnt/rip/chain_build/` that is removed afterward. Copy the package in, list it with VEGA's own `_list_standalone_exports` pointed at the scratch folder, and make a fresh split of it with `pinned_split.py`. Nothing touches `inprocess/` or a live project.
- **Regression:** the full suite on this Mac.
- **Pixels:** screenshots at 946 and 1280 px of the home screen with a folder project, the NEW PROJECT dialog, the LABELS panel, the EXPORT FOR VEGA dialog, and the annotator page with custom labels, read against the seven questions.
- **Review:** one independent reviewer, then one re-review.

## Release

Version 1.1.0: the README rewritten for Macs and the new route, tag `v1.1.0`, the Mac package on the GitHub release, and the SAM3 release `sam3-weights-1`. The same package goes into Alexandria's server folder through `tools/publish_to_server.sh`, with `START_HERE.txt` rewritten to say that setup now downloads SAM3; the server folder's SAM3 copy stays as the fallback.
