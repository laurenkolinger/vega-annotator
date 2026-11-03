<p><img src="packaging/vicar-logo-long.png" alt="VICAR" height="56"></p>

# VEGA Annotator by VICAR

VEGA Annotator runs VEGA's annotator on a MacBook or a Windows laptop, so labeling can go on when the VEGA machine is busy. It looks like VEGA because it is VEGA's code: the annotator's `app.py`, `index.html`, and SAM3 engine run unchanged, and a thin layer starts them with laptop settings. Projects keep VEGA's folder layout and records.

![VEGA Annotator's home screen, with a project card ready to bring in from the server folder](docs/vega-annotator-home.png)

VEGA makes the projects. This app labels them.

## Install on a Mac

1. Download `VEGAAnnotator-<version>.pkg` from [Releases](https://github.com/laurenkolinger/vega-annotator/releases/latest).
2. Double-click it. macOS blocks an installer from an unidentified developer the first time: open **System Settings > Privacy & Security**, click **Open Anyway**, and run it again.
3. Click through the installer. It needs no administrator password.
4. VEGA Annotator opens and asks to download Python and PyTorch into its own folder, up to about 1 GB. Click **Download** and stay connected to the internet.
5. On the home screen, click **CHOOSE FOLDER** and pick the `VEGA_Annotator` folder on your server.
6. Click **INSTALL SAM3**, which copies 3.4 GB from that folder, then **TEST SAM3**.

It needs an Apple chip, because PyTorch 2.11 does not run on Intel Macs. Tested on an Apple silicon Mac running macOS 27.

## Install on Windows

1. Download `VEGAAnnotator-<version>-Windows.zip` from [Releases](https://github.com/laurenkolinger/vega-annotator/releases/latest), and unzip it.
2. Double-click `Install VEGA Annotator.cmd`. If Windows warns you, click **More info**, then **Run anyway**.
3. Let it download Python and PyTorch, up to about 4 GB on a computer with an NVIDIA card.
4. Follow steps 5 and 6 above.

If setup stops, open the app again to retry. The reason is at the end of the install log, in `~/Library/Logs/VEGA Annotator install.log` on a Mac and in `%LOCALAPPDATA%\VEGA Annotator\logs\` on Windows.

The SAM3 weights are not in the release. Each computer copies them from the server folder, with Meta's license beside them, and needs no Hugging Face account.

## How a project moves

1. VEGA makes the project on the mainframe, as it does now.
2. `tools/publish_project.py` copies it into a `VEGA_Annotator/projects/` folder on the server.
3. You connect the server to your computer and click **BRING IN**. The project copies to the laptop and is marked as in use there. You can then work with the server disconnected.
4. You label and export batches as in VEGA.
5. You click **PUT BACK**. Your work goes to the server folder in VEGA's own formats, with mainframe paths. If another computer took the project over or the server copy moved on, the app overwrites nothing: it saves your changed files under `conflicts/` beside the project and says so.
6. VEGA takes it back. That step is not built yet.

## Disk space

The home screen shows each project's size and this computer's free space. **BRING IN** says what it will copy, and refuses a project that would not fit with room for labeling to grow.

| On the laptop | Size | Measured on |
|---|---|---|
| The app, with Python and PyTorch | about 0.7 GB on a Mac, about 4 GB on Windows with an NVIDIA card | the installed copy |
| SAM3 | 3.4 GB, once | the weights |
| A project when brought in | about 0.66 MB per frame, so 800 frames take about 0.53 GB | a real 1,356-frame project, 0.90 GB |
| The same project fully labeled | about 1.4 MB per frame, so 800 frames take about 1.1 GB | that project's previews and overlays |

A 1 TB drive holds about 900 fully labeled 800-frame projects. A project stores each frame once, and its `all_images/` folder holds links, not copies. A frame that sits in two projects is stored twice: on 2 Nov 2026, 159 of VEGA's 2,713 frames sat in more than one project, which added about 0.1 GB. **Remove** takes a project off the laptop once its work is back on the server.

## What a project carries from TCRMP

A project carries everything the annotator reads, so a laptop needs nothing else from TCRMP.

| TCRMP data | On the laptop? | Why |
|---|---|---|
| Clip frames for the project | Yes, in the project | `routed_input/raw/` |
| Survey points for those frames | Yes, in the project | `reference_points` in `segmentations.json`, read from the point counts when VEGA routed the project |
| Label names | Yes, in the project | `master_codes_recoded.csv` |
| What other projects screened | Yes, in the project | `prior_screening.json`, refreshed when the project is published |
| `all_points.csv` (85 MB) | No | VEGA uses it to choose frames, on the mainframe |
| `TCRMP_clip` (128 GB), `cpc_all` (67 GB), `TCRMP_CVR` (572 MB) | No | VEGA uses them to route frames, on the mainframe |

## SAM3 on each computer

SAM3 runs on a Mac through the Hugging Face Transformers version VEGA already uses, which needs no CUDA. Eight real masks, reproduced on an M3 Max on 2 Nov 2026, overlapped the mainframe's masks by 99 percent or more.

| Computer | SAM3 runs on | Preparing a frame | A click |
|---|---|---|---|
| M3 Max Mac, 96 GB | Graphics chip | 1.4 s | 0.02 s |
| M3 Max Mac, processor only, 4 threads | Processor | 3.6 s | 0.02 s |
| Mac under 24 GB, such as a MacBook Air | Processor | not measured; run **TEST SAM3** | |
| Windows with an NVIDIA card | The card | not measured; run **TEST SAM3** | |
| Windows with no NVIDIA card | Processor | not measured; run **TEST SAM3** | |

The app chooses by memory. The Mac graphics chip used about 9 GB and ran out under a simulated 8 GB limit, while the processor used 2.2 GB.

## What the app changes around VEGA's code

- It listens on 127.0.0.1 only and refuses other hosts and other web pages. VEGA's own launch listens on every network interface.
- It serves VEGA's fonts from local files, so the look holds with no internet.
- It adds a frames-left readout beside the frame counter. A pixel comparison with VEGA's page at 946 and 1280 px found changes only in that readout's strip of the top bar.
- It leaves out the guided tutorial button, because the tutorial walks through mainframe windows.
- On Windows only, it supplies what VEGA's Linux code expects: file locks, image links, LF line endings, and UTF-8.

## Publish to the server folder

From a Mac with `ssh vicar-mainframe` set up, with `VEGA_PUBLISH_DEST` naming the folder on the mainframe:

```bash
packaging/mac/build_pkg.sh
```

```bash
python3 packaging/windows/build_zip.py
```

```bash
tools/publish_to_server.sh
```

```bash
ssh vicar-mainframe python3 - --project run_YOURPROJECT --by LO --dest "$VEGA_PUBLISH_DEST/projects" < tools/publish_project.py
```

Raise `VERSION` in `vega_annotator/__init__.py` for each new release, because the Mac launcher restarts a running home screen only when the version changes.

`publish_project.py` reads the project and writes only to the server folder. It refuses a project with a running annotator. It also refuses to replace a server copy that a computer holds, or one holding laptop work VEGA has not taken back, unless `--replace-laptop-work` is passed.

## Update from VEGA

```bash
python3 tools/sync_from_vega.py
```

This copies the annotator files again and records the commit. `tests/test_vendor_parity.py` fails if a vendored file differs from that record, or if VEGA's page no longer has what the app relies on.

## Tests

```bash
.venv/bin/python -m pytest tests -q
```

```bash
VEGA_TEST_HF_HOME=/path/to/huggingface .venv/bin/python -m pytest tests -q
```

The second form also runs the real SAM3 weights on the CPU and the Mac graphics chip, and the whole click path in a real Chrome. `tests/test_pixels.py` compares the annotator page with VEGA's own page whenever Chrome and node are on the computer. `.github/workflows/windows.yml` runs the real installer and the tests on Windows at every push.

## Uninstall

On a Mac, stop the service and delete the app's folders:

```bash
launchctl bootout gui/$(id -u)/org.vicar.vega-annotator.home
```

```bash
rm -rf ~/Applications/"VEGA Annotator.app" ~/Library/Application\ Support/"VEGA Annotator" ~/Library/LaunchAgents/org.vicar.vega-annotator.home.plist
```

On Windows, delete `%LOCALAPPDATA%\VEGA Annotator` and the desktop shortcut.
