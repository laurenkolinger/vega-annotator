"""The annotator page here against VEGA's own page, in a real Chrome, at the two review widths.

Skipped without Chrome and node. VEGA's page is the vendored file with only its web fonts
served locally. The one thing this app adds to that page is the frames-left readout in the
top bar, so every other pixel must match.
"""

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import helpers
from annotator_driver import Annotator, free_port
from vega_annotator import transfer

ROOT = Path(__file__).resolve().parent.parent
FAKE = [str(Path(__file__).parent / "fake_annotator.py")]
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DRIVER = ROOT / "tests" / "e2e" / "page_shot.mjs"
WIDTHS = (1280, 946)
PAGE_HEIGHT = 900
TOP_BAR_ROWS = 48
MOST_PIXELS_CHANGED = 0.005
CHANNEL_TOLERANCE = 8
FEWEST_COLORS_ON_A_REAL_PAGE = 200
START_TIMEOUT_S = 30
SHOT_TIMEOUT_S = 120

pytestmark = pytest.mark.skipif(not os.path.exists(CHROME) or not shutil.which("node"), reason="needs Chrome and node")


def browser_address(port):
    """Wait for Chrome's debugging port and return the browser's WebSocket address."""
    deadline = time.time() + START_TIMEOUT_S
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as response:
                return json.loads(response.read().decode("utf-8"))["webSocketDebuggerUrl"]
        except OSError:
            time.sleep(0.2)
    raise AssertionError("Chrome did not start")


def shoot(browser, url, width, out):
    """Save a screenshot of the annotator page, and return it as an array and the readout text."""
    result = subprocess.run(["node", "--experimental-websocket", str(DRIVER), browser, url, str(width),
                             str(PAGE_HEIGHT), str(out)],
                            capture_output=True, text=True, timeout=SHOT_TIMEOUT_S, check=False)
    lines = result.stdout.strip().splitlines()
    report = json.loads(lines[-1]) if lines else {"ok": False, "error": result.stderr[-800:]}
    assert report["ok"] is True, report
    return np.asarray(Image.open(out).convert("RGB")).astype(int), report["left"]


def test_only_the_readout_differs_from_vegas_page(tmp_path, server_folder, monkeypatch):
    source = helpers.make_project(tmp_path / "inprocess", frames=24, exported=2)
    helpers.publish_to(server_folder, source)
    run = source.name
    ours_root, vega_root = tmp_path / "ours", tmp_path / "vega"
    transfer.bring_in(str(server_folder), run, "AT", "Lab MacBook", local_root=ours_root)
    transfer.bring_in(str(server_folder), run, "AT", "Legion", take_over=True, local_root=vega_root)
    ours = Annotator(ours_root / run, FAKE)
    monkeypatch.setenv("VEGA_TEST_PLAIN_PAGE", "1")
    vega = Annotator(vega_root / run, FAKE)
    monkeypatch.delenv("VEGA_TEST_PLAIN_PAGE")
    debug_port = free_port()
    chrome = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                               f"--remote-debugging-port={debug_port}", f"--user-data-dir={tmp_path / 'chrome'}",
                               "--window-size=1280,900", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        browser = browser_address(debug_port)
        for width in WIDTHS:
            mine, left = shoot(browser, ours.base + "/", width, tmp_path / f"ours_{width}.png")
            theirs, no_readout = shoot(browser, vega.base + "/", width, tmp_path / f"vega_{width}.png")
            assert left.endswith(" left") and no_readout == ""
            assert mine.shape == theirs.shape
            assert len(np.unique(mine.reshape(-1, 3), axis=0)) > FEWEST_COLORS_ON_A_REAL_PAGE, "the page did not render"
            changed = (np.abs(mine - theirs) > CHANNEL_TOLERANCE).any(axis=2)
            rows = np.nonzero(changed.any(axis=1))[0]
            assert len(rows), "the frames-left readout did not draw"
            assert rows.max() < TOP_BAR_ROWS, f"pixels changed below the top bar at {width}: row {rows.max()}"
            assert changed.mean() < MOST_PIXELS_CHANGED, f"{changed.mean():.2%} of the page changed at {width}"
    finally:
        chrome.terminate()
        ours.stop()
        vega.stop()


# ---------------------------------------------------------------------------
# The 1.1 screens: the home screen with a project made here, the NEW PROJECT dialog,
# the LABELS panel, the EXPORT FOR VEGA dialog, and VEGA's annotator page with custom
# labels. The home server runs in this process with stand-ins for the modules other
# tasks write; Chrome is driven over its debugging protocol. Every screenshot is kept
# in SHOT_DIR for reading against the seven questions.
# ---------------------------------------------------------------------------

import threading  # noqa: E402
import types  # noqa: E402

from werkzeug.serving import make_server  # noqa: E402

from test_home import FakeAnnotator, FOLDER_RUN, ORIGINALS_BYTES, fakes, make_folder_project  # noqa: E402,F401
from vega_annotator import home, paths, projects  # noqa: E402

CDP = (ROOT / "tests" / "e2e" / "cdp.mjs").as_uri()
SHOT_DIR = Path(os.environ.get("VEGA_SHOT_DIR") or (ROOT / ".pytest_cache" / "screens"))
SCREEN_HEIGHT = 900
CUSTOM_LABELS = ("healthy", "Partly bleached", "bleached")
SCREEN_LABELS = (*CUSTOM_LABELS, "diseased")
ORIGINALS_SIZE = 1_930_000_000

SCREENS_DRIVER = r"""
import { writeFileSync } from 'node:fs';
import { connect, sleep, until } from '__CDP__';

const [WS_URL, BASE, RUN, OUT, WIDTH, HEIGHT] = process.argv.slice(2);
const facts = {};
let step = 'connect';

async function main() {
  const { send, attach, evaluate } = await connect(WS_URL);
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const page = await attach(targetId);
  const size = async (height) => send('Emulation.setDeviceMetricsOverride', {
    width: Number(WIDTH), height, deviceScaleFactor: 1, mobile: false }, page);
  const shot = async (name) => {
    await evaluate(page, 'document.fonts.ready.then(() => true)');
    await sleep(350);
    const { data } = await send('Page.captureScreenshot', { format: 'png' }, page);
    writeFileSync(`${OUT}/${name}_${WIDTH}.png`, Buffer.from(data, 'base64'));
  };
  const click = (selector) => evaluate(page, `(() => { const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) throw new Error('missing ' + ${JSON.stringify(selector)}); el.scrollIntoView({ block: 'center', behavior: 'instant' }); el.click(); return true; })()`);
  const type = (selector, text) => evaluate(page, `(() => { const el = document.querySelector(${JSON.stringify(selector)});
    el.focus(); el.value = ${JSON.stringify(text)}; el.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`);
  const key = async (name, code, vk) => {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: name, code, windowsVirtualKeyCode: vk }, page);
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: name, code, windowsVirtualKeyCode: vk }, page);
  };
  const card = `.run-card[data-run="${RUN}"]`;

  step = 'home';
  await size(Number(HEIGHT));
  await send('Page.navigate', { url: BASE + '/' }, page);
  await until('the made-here card', () => evaluate(page, `!!document.querySelector('${card} .run-action[data-action="export-for-vega"]')`));
  facts.cardButtons = await evaluate(page, `Array.from(document.querySelectorAll('${card} .run-action')).map((b) => b.textContent.trim())`);
  facts.chip = await evaluate(page, `document.querySelector('${card} .launcher-chip').textContent`);
  facts.labelsLine = await evaluate(page, `document.querySelector('${card} .va-targets').textContent`);
  facts.untitled = await evaluate(page, `Array.from(document.querySelectorAll('button, [role=button], summary, label.va-check')).filter((b) => b.offsetParent && !(b.title || '').trim()).map((b) => b.id || b.className || b.textContent.trim())`);
  facts.samButtons = await evaluate(page, `['download-sam3', 'install-sam3', 'test-sam3'].filter((id) => !document.getElementById(id).hidden)`);
  facts.installLocked = await evaluate(page, `getComputedStyle(document.getElementById('install-sam3')).cursor`);
  await until('the SAM3 line', () => evaluate(page, `document.getElementById('sam3-result').textContent.length > 0`));
  facts.samNote = await evaluate(page, `document.getElementById('sam3-result').textContent`);
  facts.exportLockedCursor = await evaluate(page, `(() => { const b = document.querySelector('${card} .run-action[data-action="export-for-vega"]'); return [b.getAttribute('aria-disabled'), getComputedStyle(b).cursor]; })()`);
  const full = await evaluate(page, 'document.documentElement.scrollHeight');
  await size(Math.max(Number(HEIGHT), full));
  await shot('home');
  await size(Number(HEIGHT));

  step = 'new project dialog';
  await click('#new-project');
  await until('the dialog', () => evaluate(page, `!!document.querySelector('#np-folder-btn')`));
  facts.newProjectFocus = await until('focus on CHOOSE FOLDER', () => evaluate(page, `document.activeElement && document.activeElement.id`));
  await click('#np-folder-btn');
  await until('the scan summary', () => evaluate(page, `document.querySelector('#np-summary').className.includes('good')`));
  facts.summary = await evaluate(page, `document.querySelector('#np-summary').textContent`);
  facts.nameFromFolder = await evaluate(page, `document.querySelector('#np-name').value`);
  await type('#np-name', 'Reef health 2026');
  await type('#np-labels', 'healthy, Partly bleached, bleached');
  await type('#np-purpose', 'Health and condition labels for the student set');
  await type('#va-initials', 'JM');
  await evaluate(page, `document.querySelector('.va-skipped').open = true`);
  facts.dialogUntitled = await evaluate(page, `Array.from(document.querySelectorAll('.modal button, .modal summary, .modal label')).filter((b) => b.offsetParent && !(b.title || '').trim()).map((b) => b.id || b.className || b.textContent.trim())`);
  await shot('new_project');

  step = 'new project made';
  await click('#np-create');
  await until('the result', () => evaluate(page, `document.querySelector('#np-result').textContent.includes('is ready')`));
  facts.newProjectDone = await evaluate(page, `document.querySelector('#np-result').textContent`);
  facts.newProjectButtons = await evaluate(page, `Array.from(document.querySelectorAll('.modal-actions button')).filter((b) => !b.hidden).map((b) => b.textContent)`);
  await evaluate(page, `document.querySelector('#np-result .va-skipped').open = true`);
  await evaluate(page, `(() => { const m = document.querySelector('.modal'); m.scrollTop = m.scrollHeight; return true; })()`);
  facts.scanLineHidden = await evaluate(page, `document.querySelector('#np-summary').hidden`);
  // The tooltip script parks a focused control's title in data-tip while it shows it.
  facts.doneUntitled = await evaluate(page, `Array.from(document.querySelectorAll('.modal button, .modal summary')).filter((b) => b.offsetParent && !(b.title || b.dataset.tip || '').trim()).map((b) => b.id || b.className || b.textContent.trim())`);
  await shot('new_project_done');
  await key('Escape', 'Escape', 27);
  await until('the dialog to close', () => evaluate(page, `!document.querySelector('.modal-backdrop.open')`));
  facts.focusBack = await evaluate(page, `document.activeElement && document.activeElement.id`);

  step = 'labels dialog';
  await click(`${card} .run-action[data-action="labels"]`);
  await until('the labels', () => evaluate(page, `document.querySelectorAll('.modal .va-label-row').length === 4`));
  facts.dialogLabelButtons = await evaluate(page, `Array.from(document.querySelectorAll('.modal .va-label-rename, .modal .va-label-remove')).map((b) => [b.textContent, b.getAttribute('aria-disabled') === 'true', b.classList.contains('btn-accent-outline'), getComputedStyle(b).borderTopColor, getComputedStyle(b).color])`);
  await shot('labels_dialog');
  await key('Escape', 'Escape', 27);
  await until('the dialog to close', () => evaluate(page, `!document.querySelector('.modal-backdrop.open')`));

  step = 'export dialog';
  await click(`${card} .run-action[data-action="export-for-vega"]`);
  await until('the originals size', () => evaluate(page, `document.querySelector('#ex-originals-size').textContent.length > 0`));
  facts.exportLocked = await evaluate(page, `document.querySelector('#ex-go').getAttribute('aria-disabled')`);
  await click('#ex-folder-btn');
  await until('the destination', () => evaluate(page, `document.querySelector('#ex-free').textContent.includes('free there')`));
  facts.exportUnlocked = await evaluate(page, `document.querySelector('#ex-go').getAttribute('aria-disabled')`);
  facts.originals = await evaluate(page, `document.querySelector('label.va-check').textContent.replace(/\\s+/g, ' ').trim()`);
  await shot('export');
  await key('Escape', 'Escape', 27);

  step = 'project page labels panel';
  await send('Page.navigate', { url: `${BASE}/project/${RUN}` }, page);
  await until('the rail statuses', () => evaluate(page, `(document.getElementById('status-labels') || {}).textContent === '4 labels'`));
  facts.putBackOnRail = await evaluate(page, `!!document.querySelector('#step-nav li[data-step="putback"]')`);
  await click('#step-nav li[data-step="labels"]');
  await until('the labels', () => evaluate(page, `document.querySelectorAll('#labels-editor .va-label-row').length === 4`));
  await until('the rail count', () => evaluate(page, `document.getElementById('status-labels').textContent === '4 labels'`));
  facts.renameLocked = await evaluate(page, `Array.from(document.querySelectorAll('#labels-editor .va-label-rename')).map((b) => b.getAttribute('aria-disabled') === 'true')`);
  facts.panelLive = await evaluate(page, `Array.from(document.querySelectorAll('#labels-editor .va-label-rename, #labels-editor .va-label-remove')).map((b) => b.classList.contains('va-live') ? getComputedStyle(b).borderTopColor : '')`);
  facts.pageUntitled = await evaluate(page, `Array.from(document.querySelectorAll('button, li[data-step]')).filter((b) => b.offsetParent && !(b.title || '').trim() && !b.closest('li[data-step]:not([data-step="labels"]):not([data-step="addimages"]):not([data-step="export"])')).map((b) => b.id || b.className)`);
  await evaluate(page, 'window.scrollTo(0, 0)');
  await shot('labels');
  await click('#step-nav li[data-step="export"]');
  await sleep(200);
  await click('#export-folder-btn');
  await until('the destination', () => evaluate(page, `document.querySelector('#export-free').textContent.includes('free there')`));
  await evaluate(page, 'window.scrollTo(0, 0)');
  await shot('export_panel');
  await click('#step-nav li[data-step="addimages"]');
  await sleep(200);
  await click('#add-folder-btn');
  await until('the scan', () => evaluate(page, `document.querySelector('#add-summary').className.includes('good')`));
  await evaluate(page, 'window.scrollTo(0, 0)');
  await shot('add_images_panel');
  console.log(JSON.stringify({ ok: true, facts }));
}

main().then(() => process.exit(0)).catch((error) => {
  console.log(JSON.stringify({ ok: false, step, error: String(error), facts }));
  process.exit(1);
});
"""

EXPORT_DRIVER = r"""
import { writeFileSync } from 'node:fs';
import { connect, sleep, until } from '__CDP__';

const [WS_URL, BASE, RUN, OUT, WIDTH, HEIGHT] = process.argv.slice(2);
const facts = {};
let step = 'connect';

async function main() {
  const { send, attach, evaluate } = await connect(WS_URL);
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const page = await attach(targetId);
  await send('Emulation.setDeviceMetricsOverride', { width: Number(WIDTH), height: Number(HEIGHT), deviceScaleFactor: 1, mobile: false }, page);
  const click = (selector) => evaluate(page, `(() => { const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) throw new Error('missing ' + ${JSON.stringify(selector)}); el.scrollIntoView({ block: 'center', behavior: 'instant' }); el.click(); return true; })()`);
  step = 'export runs';
  await send('Page.navigate', { url: BASE + '/' }, page);
  const button = `.run-action[data-action="export-for-vega"][data-run="${RUN}"]`;
  await until('the card', () => evaluate(page, `!!document.querySelector('${button}')`), 20000);
  await click(button);
  await until('the dialog', () => evaluate(page, `!!document.querySelector('#ex-folder-btn')`), 20000);
  await click('#ex-folder-btn');
  await until('the destination', () => evaluate(page, `document.querySelector('#ex-free').textContent.includes('free there')`), 20000);
  await click('#ex-originals');
  await click('#ex-go');
  await until('the result', () => evaluate(page, `document.querySelector('#ex-result').className.includes('good')`), 20000);
  facts.result = await evaluate(page, `document.querySelector('#ex-result').textContent`);
  facts.buttons = await evaluate(page, `Array.from(document.querySelectorAll('.modal-actions button')).filter((b) => !b.hidden).map((b) => b.textContent)`);
  await evaluate(page, 'document.fonts.ready.then(() => true)');
  await sleep(300);
  const { data } = await send('Page.captureScreenshot', { format: 'png' }, page);
  writeFileSync(`${OUT}/export_done_${WIDTH}.png`, Buffer.from(data, 'base64'));
  console.log(JSON.stringify({ ok: true, facts }));
}

main().then(() => process.exit(0)).catch((error) => {
  console.log(JSON.stringify({ ok: false, step, error: String(error), facts }));
  process.exit(1);
});
"""

ANNOTATOR_DRIVER = r"""
import { writeFileSync } from 'node:fs';
import { connect, sleep, until } from '__CDP__';

const [WS_URL, URL, OUT, WIDTH, HEIGHT] = process.argv.slice(2);
const LIMIT = 30000;
const facts = {};
let step = 'connect';

async function main() {
  const { send, attach, evaluate } = await connect(WS_URL);
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const page = await attach(targetId);
  await send('Emulation.setDeviceMetricsOverride', { width: Number(WIDTH), height: Number(HEIGHT), deviceScaleFactor: 1, mobile: false }, page);
  const press = async (key, code, vk, text) => {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key, code, windowsVirtualKeyCode: vk, text }, page);
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, windowsVirtualKeyCode: vk }, page);
  };
  const clickAt = async (x, y) => {
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, page);
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 }, page);
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 }, page);
  };
  const popupOpen = () => evaluate(page, `document.getElementById('cat-popup').classList.contains('visible')`);

  step = 'page';
  await send('Page.navigate', { url: URL }, page);
  await until('the first frame', () => evaluate(page, `(() => { const n = document.getElementById('image-filename'); return !!n && n.textContent.includes('custom_'); })()`), LIMIT);
  await evaluate(page, 'document.fonts.ready.then(() => true)');
  facts.flag = await evaluate(page, `window.VEGA_ANNOTATOR && window.VEGA_ANNOTATOR.labelSet`);
  facts.marked = await evaluate(page, `document.documentElement.classList.contains('va-custom-labels')`);
  facts.hint = await evaluate(page, `Array.from(document.querySelectorAll('#kbd-help .kh-desc')).map((d) => d.textContent).filter((t) => t.includes('the popup takes'))[0] || ''`);
  await evaluate(page, `window.__seen = []; document.addEventListener('keydown', (e) => window.__seen.push(e.key)); true`);

  step = 'a mask waits for its label';
  await press('a', 'KeyA', 65, 'a');
  await sleep(300);
  // Each width clicks its own part of the image, clear of the mask the other width made.
  const across = WIDTH === '1280' ? 0.3 : 0.7;
  const canvas = await evaluate(page, `(() => { const r = document.querySelector('canvas').getBoundingClientRect(); return { x: r.left + r.width * ${across}, y: r.top + r.height / 2 }; })()`);
  await clickAt(canvas.x, canvas.y);
  await until('a mask', () => evaluate(page, `typeof currentData !== 'undefined' && currentData && currentData.masks && currentData.masks.length > 0`), LIMIT);
  await sleep(300);
  await press('c', 'KeyC', 67, 'c');
  await until('the label popup', popupOpen, LIMIT);
  facts.chips = await evaluate(page, `Array.from(document.querySelectorAll('#cat-popup .cat-btn')).filter((b) => getComputedStyle(b).display !== 'none').map((b) => b.innerText.replace(/\\s+/g, ' ').trim())`);
  facts.heads = await evaluate(page, `Array.from(document.querySelectorAll('#cat-popup .cat-head')).filter((h) => getComputedStyle(h).display !== 'none').map((h) => h.textContent.trim())`);
  facts.repeatedShown = await evaluate(page, `Array.from(document.querySelectorAll('.cat-code, .qa-code, .m-species')).filter((c) => c.offsetParent && c.previousElementSibling && c.previousElementSibling.textContent.trim() === c.textContent.trim()).length`);
  facts.nameStyles = await evaluate(page, `Array.from(document.querySelectorAll('#cat-popup .sci-name, .quickadd-bar .sci-name')).map((n) => n.textContent + ':' + getComputedStyle(n).fontStyle)`);
  await sleep(400);
  const { data } = await send('Page.captureScreenshot', { format: 'png' }, page);
  writeFileSync(`${OUT}/annotator_custom_${WIDTH}.png`, Buffer.from(data, 'base64'));

  step = 'reef keys inside the popup';
  await evaluate(page, `window.__seen = []; true`);
  for (const [k, code, vk] of [['s', 'KeyS', 83], ['m', 'KeyM', 77], ['g', 'KeyG', 71], ['t', 'KeyT', 84], ['S', 'KeyS', 83]]) {
    await press(k, code, vk, k);
  }
  await sleep(300);
  facts.popupAfterReefKeys = await popupOpen();
  facts.speciesAfterReefKeys = await evaluate(page, `currentData.masks[selectedMask].species || ''`);
  facts.seenInPopup = await evaluate(page, `window.__seen.slice()`);
  await press('1', 'Digit1', 49, '1');
  await until('the popup to close', async () => !(await popupOpen()), LIMIT);
  facts.speciesAfterKey1 = await evaluate(page, `currentData.masks[selectedMask >= 0 ? selectedMask : currentData.masks.length - 1].species`);

  step = 'reef keys outside the popup';
  await evaluate(page, `window.__seen = []; true`);
  await press('g', 'KeyG', 71, 'g');
  await press('g', 'KeyG', 71, 'g');
  facts.seenOutside = await evaluate(page, `window.__seen.slice()`);
  facts.listRepeats = await evaluate(page, `Array.from(document.querySelectorAll('#mask-list .m-species')).filter((c) => c.offsetParent).map((c) => c.textContent)`);
  facts.listNames = await evaluate(page, `Array.from(document.querySelectorAll('#mask-list .m-name')).map((c) => c.textContent)`);
  console.log(JSON.stringify({ ok: true, facts }));
}

main().then(() => process.exit(0)).catch((error) => {
  console.log(JSON.stringify({ ok: false, step, error: String(error), facts }));
  process.exit(1);
});
"""


def run_driver(source, tmp_path, name, args, timeout=SHOT_TIMEOUT_S):
    """Write a driver script beside the test's files, run it with node, and return its report."""
    script = tmp_path / f"{name}.mjs"
    script.write_text(source.replace("__CDP__", CDP), encoding="utf-8")
    result = subprocess.run(["node", "--experimental-websocket", str(script), *args],
                            capture_output=True, text=True, timeout=timeout, check=False)
    lines = result.stdout.strip().splitlines()
    report = json.loads(lines[-1]) if lines else {"ok": False, "error": result.stderr[-800:]}
    assert report["ok"] is True, report
    return report["facts"]


def start_chrome(tmp_path):
    """Start a headless Chrome and return (process, its browser address)."""
    debug_port = free_port()
    chrome = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                               f"--remote-debugging-port={debug_port}", f"--user-data-dir={tmp_path / 'chrome'}",
                               "--window-size=1280,900", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return chrome, browser_address(debug_port)


def screens_project():
    """Write a project made here, with 24 frames: 6 exported, 15 masks over three of its four labels."""
    project = make_folder_project(labels=SCREEN_LABELS)
    with open(project / "step4test_combinedAnnotate" / "custom_imports" / "originals" / "IMG_0001.HEIC", "wb") as handle:
        handle.truncate(ORIGINALS_SIZE - ORIGINALS_BYTES)  # A sparse file: its size, with no disk used.
    store = {}
    for index in range(24):
        name = f"custom_IMG_{index:04d}.jpg"
        masks = []
        if index < 6:
            masks = [{"id": n, "species": CUSTOM_LABELS[(index + n) % 3], "status": "accepted", "reviewer": "JM"}
                     for n in range(3 if index % 2 else 2)]
        store[name] = {"image_path": f"custom_imports/raw/{name}", "masks": masks, "custom_import": True,
                       "reference_points": [], "exported": index < 6}
    (project / "step4test_combinedAnnotate" / "segmentations" / "segmentations.json").write_text(json.dumps(store), encoding="utf-8")
    record = json.loads((project / "project.json").read_text(encoding="utf-8"))
    record["name"] = "Reef health 2026"
    record["description"] = "Health and condition labels for the student set"
    (project / "project.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return project


def screens_cards(folder_project, vega_project):
    """Return the cards the catalog gives once Task 3's made_here state exists."""
    mine = projects.describe(folder_project)
    mine.update(where="local", state="made_here", holding=False, pending_files=0, unsent_records=False, holder=None,
                server_rev=None, local_rev=None, size_bytes=transfer.project_size(folder_project), error="")
    theirs = projects.describe(vega_project)
    theirs.update(where="both", state="here", holding=True, pending_files=0, unsent_records=False,
                  holder={"computer": "Lab MacBook", "initials": "AT", "at": "2026-11-02T10:00:00"},
                  server_rev=1, local_rev=1, size_bytes=transfer.project_size(vega_project), error="")
    return [mine, theirs]


@pytest.fixture
def screens_server(fakes, monkeypatch, tmp_path):
    """Run the home server in this process, with a project made here and one from VEGA."""
    folder_project = screens_project()
    vega_project = helpers.make_project(paths.projects_dir(), frames=12, exported=4)
    photos, usb = tmp_path / "Reef photos 2026", tmp_path / "USB"
    photos.mkdir()
    usb.mkdir()
    cards = screens_cards(folder_project, vega_project)
    monkeypatch.setattr(home.catalog, "list_projects", lambda *_args: [dict(card) for card in cards])
    monkeypatch.setattr(home.catalog, "project_card", lambda run, *_args: next((dict(c) for c in cards if c["run"] == run), None))
    monkeypatch.setattr(home.catalog, "autodetect_server_folder", lambda: "")
    monkeypatch.setattr(home, "annotator", FakeAnnotator())
    monkeypatch.setattr(home, "jobs", home.JobBoard())
    monkeypatch.setattr(home, "native_folder_prompt",
                        lambda **kwargs: str(photos) if kwargs.get("prompt") == home.IMAGES_PROMPT else str(usb))

    def scan(folder):
        """Stand in for folder_projects.scan with a folder of 240 photos."""
        folder = Path(folder)
        images = [folder / f"IMG_{n:04d}.JPG" for n in range(222)] + [folder / "bleached" / f"IMG_{n:04d}.HEIC" for n in range(18)]
        return {"images": images[:222], "convert": images[222:], "bytes": 1_930_000_000,
                "skipped": [{"path": folder / ".DS_Store", "reason": "hidden file"},
                            {"path": folder / "bleached" / "IMG_0099.JPG", "reason": "does not open as an image"},
                            {"path": folder / "notes.txt", "reason": "not an image"}]}

    fakes.folder_projects.scan = scan
    fakes.labels.state["exported"] = False
    (paths.app_data_dir() / "sam3_download_failed.json").write_text(json.dumps({"reason": (
        "The SAM3 download stopped at 1.2 of 3.4 GB (the connection closed early). "
        "Click DOWNLOAD SAM3 to continue from there.")}), encoding="utf-8")
    port = free_port()
    server = make_server("127.0.0.1", port, home.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield types.SimpleNamespace(base=f"http://127.0.0.1:{port}", fakes=fakes, cards=cards, photos=photos)
    finally:
        server.shutdown()


def test_new_screens_at_both_widths(screens_server, tmp_path):
    """Shoot the 1.1 screens at both widths and check what a person sees and can use."""
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    chrome, browser = start_chrome(tmp_path)
    try:
        for width in WIDTHS:
            screens_server.fakes.labels.state["exported"] = False
            facts = run_driver(SCREENS_DRIVER, tmp_path, f"screens_{width}",
                               [browser, screens_server.base, FOLDER_RUN, str(SHOT_DIR), str(width), str(SCREEN_HEIGHT)])
            assert facts["cardButtons"] == ["OPEN →", "ADD IMAGES", "LABELS", "EXPORT FOR VEGA", ""], facts
            assert facts["chip"] == "Made on this computer"
            assert facts["labelsLine"] == "Labels: healthy, Partly bleached, bleached, diseased"
            assert facts["samButtons"] == ["download-sam3", "install-sam3"]
            assert facts["installLocked"] == "not-allowed"
            assert facts["exportLockedCursor"] == [None, "crosshair"]
            assert facts["untitled"] == [] and facts["dialogUntitled"] == [], facts
            assert facts["newProjectFocus"] == "np-folder-btn" and facts["focusBack"] == "new-project"
            assert facts["summary"].startswith("240 images found, 18 of them to convert first. 3 files skipped.")
            assert facts["nameFromFolder"] == "Reef photos 2026"
            assert facts["exportLocked"] == "true" and facts["exportUnlocked"] is None
            assert facts["originals"] == "Include originals (1.93 GB)", facts["originals"]
            assert facts["putBackOnRail"] is False and facts["renameLocked"] == [True, True, True, False]
            assert facts["pageUntitled"] == [], facts["pageUntitled"]
            assert facts["samNote"] == ("The SAM3 download stopped at 1.2 of 3.4 GB (the connection closed early). "
                                        "Click DOWNLOAD SAM3 to continue from where it stopped."), facts["samNote"]
            assert facts["newProjectDone"].startswith("run_26-11-09_JM_reef_health_2026 is ready: 3 images added, "
                                                      "1 file skipped.Files skipped"), facts["newProjectDone"]
            assert ".DS_Store" not in facts["newProjectDone"] and ".hidden.jpg hidden file" in facts["newProjectDone"]
            assert facts["newProjectButtons"] == ["Close"] and facts["scanLineHidden"] is True, facts["newProjectButtons"]
            assert facts["doneUntitled"] == [], facts["doneUntitled"]
            # An enabled RENAME or REMOVE wears the pink outline; a locked one stays gray.
            live = [row for row in facts["dialogLabelButtons"] if not row[1]]
            locked = [row for row in facts["dialogLabelButtons"] if row[1]]
            assert [row[0] for row in live] == ["RENAME", "REMOVE"] and all(row[2] for row in live), facts["dialogLabelButtons"]
            assert len(locked) == 6 and not any(row[2] for row in locked)
            assert {row[3] for row in live}.isdisjoint({row[3] for row in locked}) and live[0][4] == live[0][3]
            assert facts["panelLive"][:6] == [""] * 6 and len(facts["panelLive"]) == 8 and all(facts["panelLive"][6:])
            screens_server.fakes.labels.state["exported"] = True
            done = run_driver(EXPORT_DRIVER, tmp_path, f"export_{width}",
                              [browser, screens_server.base, FOLDER_RUN, str(SHOT_DIR), str(width), str(SCREEN_HEIGHT)])
            assert done["result"].startswith("VEGA Annotator wrote ") and "4 frames, 9 masks, 52 MB" in done["result"], done
            assert done["buttons"] == ["Close"], done
            assert screens_server.fakes.calls[-1][-1] is True, "Include originals did not reach the route"
            for name in ("home", "new_project", "new_project_done", "labels_dialog", "export", "labels", "export_panel",
                         "add_images_panel", "export_done"):
                image = np.asarray(Image.open(SHOT_DIR / f"{name}_{width}.png").convert("RGB"))
                assert image.shape[1] == width
                assert len(np.unique(image.reshape(-1, 3), axis=0)) > FEWEST_COLORS_ON_A_REAL_PAGE, name
    finally:
        chrome.terminate()


def custom_label_project(root, monkeypatch):
    """Write a project the way NEW PROJECT makes one: no survey points, the images brought in
    through VEGA's own custom_imports code, and the labels a student typed."""
    project = Path(root) / FOLDER_RUN
    step4 = project / "step4test_combinedAnnotate"
    for folder in (step4 / "routed_input" / "raw", step4 / "routed_input" / "ids", step4 / "segmentations"):
        folder.mkdir(parents=True, exist_ok=True)
    (step4 / "routed_input" / "ids" / "sam_click_prompts.json").write_text("{}", encoding="utf-8")
    record = {"id": FOLDER_RUN[4:], "name": "Reef health 2026", "description": "", "initials": "JM",
              "created_at": "2026-11-09T09:00:00-04:00", "updated_at": "2026-11-09T09:00:00-04:00",
              "access_log": [{"ts": "2026-11-09T09:00:00-04:00", "initials": "JM", "action": "create", "purpose": "x"}],
              "steps": {"1": {"status": "skipped", "name": "Make All Points"},
                        "2": {"status": "skipped", "name": "Recode Species"},
                        "3": {"status": "skipped", "name": "Choose Images",
                              "config": {"target_species": ",".join(CUSTOM_LABELS)}}},
              "label_set": {"kind": "custom", "labels": list(CUSTOM_LABELS)}}
    (project / "project.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    (step4 / "master_codes_recoded.csv").write_text(
        "species_code,species_name,category\n" + "".join(f"{label},{label},Label\n" for label in CUSTOM_LABELS),
        encoding="utf-8")
    (step4 / "class_map.json").write_text(json.dumps({label: index for index, label in enumerate(CUSTOM_LABELS)}),
                                          encoding="utf-8")
    photos = Path(root) / "photos"
    photos.mkdir()
    for index in range(6):
        Image.new("RGB", (1280, 720), (20 + 30 * index, 90, 140 - 10 * index)).save(photos / f"IMG_{index:04d}.jpg", quality=85)
    monkeypatch.syspath_prepend(str(paths.vendor_scripts()))
    monkeypatch.syspath_prepend(str(paths.annotator_src()))
    import custom_imports

    entries, skipped = custom_imports.ingest_files(str(step4), [(path.name, str(path)) for path in sorted(photos.iterdir())])
    assert not skipped and len(entries) == 6
    store = {entry["filename"]: custom_imports.seg_dict_for(str(step4), entry) for entry in entries}
    (step4 / "segmentations" / "segmentations.json").write_text(json.dumps(store, indent=1), encoding="utf-8")
    custom_imports.record_in_project(str(step4), entries, initials="JM")
    return project


def test_annotator_page_with_custom_labels(tmp_path, monkeypatch):
    """VEGA's page with custom labels: no reef shortcuts, upright names, no repeated codes."""
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    project = custom_label_project(tmp_path / "inprocess", monkeypatch)
    monkeypatch.setenv("VEGA_ANNOTATOR_LABEL_SET", "custom")
    annotator = Annotator(project, FAKE)
    chrome, browser = start_chrome(tmp_path)
    try:
        for width in WIDTHS:
            facts = run_driver(ANNOTATOR_DRIVER, tmp_path, f"annotator_{width}",
                               [browser, annotator.base + "/", str(SHOT_DIR), str(width), str(PAGE_HEIGHT)])
            assert facts["flag"] == "custom" and facts["marked"] is True, facts
            assert "s / m / g / t" not in facts["hint"] and "the popup takes 1 to 9 for a label" in facts["hint"]
            assert not [chip for chip in facts["chips"] if chip.endswith(("Sponge", "Macroalgae", "Gorgonian", "Turf"))], facts
            assert any("Unlabel" in chip for chip in facts["chips"])
            assert facts["nameStyles"] and all(style.endswith(":normal") for style in facts["nameStyles"]), facts
            assert facts["popupAfterReefKeys"] is True and facts["speciesAfterReefKeys"] == "", facts
            assert facts["seenInPopup"] == [] and facts["speciesAfterKey1"] == "healthy", facts
            assert facts["seenOutside"] == ["g", "g"], facts
            assert facts["heads"] == ["Target labels"] and facts["repeatedShown"] == 0, facts
            assert "healthy" in facts["listNames"] and facts["listRepeats"] == [], facts
            assert facts["chips"][:3] == ["1 healthy", "2 Partly bleached", "3 bleached"], facts
    finally:
        chrome.terminate()
        annotator.stop()


# ---------------------------------------------------------------------------
# A new project while NEW PROJECT still brings its images in, and one whose import stopped.
# ---------------------------------------------------------------------------

IMPORTING_RUN = "run_26-11-09_JM_dive_three"
PARTWAY_RUN = "run_26-11-09_JM_dive_two"

NEW_PROJECT_STATES_DRIVER = r"""
import { writeFileSync } from 'node:fs';
import { connect, sleep, until } from '__CDP__';

const [WS_URL, BASE, IMPORTING, PARTWAY, OUT, WIDTH, HEIGHT] = process.argv.slice(2);
const facts = {};
let step = 'connect';

async function main() {
  const { send, attach, evaluate } = await connect(WS_URL);
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const page = await attach(targetId);
  const size = async (height) => send('Emulation.setDeviceMetricsOverride', {
    width: Number(WIDTH), height, deviceScaleFactor: 1, mobile: false }, page);
  const shot = async (name) => {
    await evaluate(page, 'document.fonts.ready.then(() => true)');
    await sleep(350);
    const { data } = await send('Page.captureScreenshot', { format: 'png' }, page);
    writeFileSync(`${OUT}/${name}_${WIDTH}.png`, Buffer.from(data, 'base64'));
  };
  const click = (selector) => evaluate(page, `(() => { const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) throw new Error('missing ' + ${JSON.stringify(selector)}); el.scrollIntoView({ block: 'center', behavior: 'instant' }); el.click(); return true; })()`);
  const importing = `.run-card[data-run="${IMPORTING}"]`;
  const partway = `.run-card[data-run="${PARTWAY}"]`;

  step = 'home';
  await size(Number(HEIGHT));
  await send('Page.navigate', { url: BASE + '/' }, page);
  await until('the import progress', () => evaluate(page, `!!document.querySelector('${importing} .va-progress')`));
  await until('the panel progress', () => evaluate(page, `document.getElementById('new-project-status').textContent.includes('Adding images')`));
  facts.importingButtons = await evaluate(page, `Array.from(document.querySelectorAll('${importing} .run-action')).map((b) => b.textContent.trim())`);
  facts.importingChip = await evaluate(page, `document.querySelector('${importing} .launcher-chip').textContent`);
  facts.importingProgress = await evaluate(page, `document.querySelector('${importing} .va-note').textContent`);
  facts.importingOpens = await evaluate(page, `document.querySelector('${importing}').classList.contains('va-openable')`);
  facts.panelStatus = await evaluate(page, `document.getElementById('new-project-status').textContent`);
  facts.partwayButtons = await evaluate(page, `Array.from(document.querySelectorAll('${partway} .run-action')).map((b) => b.textContent.trim())`);
  facts.partwayChip = await evaluate(page, `document.querySelector('${partway} .launcher-chip').textContent`);
  facts.partwayNote = await evaluate(page, `document.querySelector('${partway} .va-note.warn').textContent`);
  facts.partwayOpens = await evaluate(page, `document.querySelector('${partway}').classList.contains('va-openable')`);
  facts.untitled = await evaluate(page, `Array.from(document.querySelectorAll('button, [role=button]')).filter((b) => b.offsetParent && !(b.title || '').trim()).map((b) => b.id || b.className || b.textContent.trim())`);
  const full = await evaluate(page, 'document.documentElement.scrollHeight');
  await size(Math.max(Number(HEIGHT), full));
  await shot('home_new_project_states');
  await size(Number(HEIGHT));

  step = 'add images to finish';
  await click(`${partway} .run-action[data-action="add-images"]`);
  await until('the dialog', () => evaluate(page, `!!document.querySelector('#ai-add')`));
  await until('focus on ADD IMAGES', () => evaluate(page, `document.activeElement && document.activeElement.id === 'ai-add'`));
  facts.finishFolder = await evaluate(page, `document.querySelector('#ai-folder').textContent`);
  facts.finishLocked = await evaluate(page, `document.querySelector('#ai-add').getAttribute('aria-disabled')`);
  facts.finishNote = await evaluate(page, `document.querySelector('.modal .va-note').textContent`);
  facts.dialogUntitled = await evaluate(page, `Array.from(document.querySelectorAll('.modal button, .modal label')).filter((b) => b.offsetParent && !(b.title || b.dataset.tip || '').trim()).map((b) => b.id || b.className || b.textContent.trim())`);
  await shot('add_images_partway');
  console.log(JSON.stringify({ ok: true, facts }));
}

main().then(() => process.exit(0)).catch((error) => {
  console.log(JSON.stringify({ ok: false, step, error: String(error), facts }));
  process.exit(1);
});
"""


def unfinished_card(run, name):
    """Write a project made here whose first import has not ended, and return its card."""
    project = make_folder_project(run=run, labels=CUSTOM_LABELS)
    local = project / transfer.LOCAL_STATE
    local.write_text(json.dumps({**json.loads(local.read_text(encoding="utf-8")), "creating": True}), encoding="utf-8")
    record = json.loads((project / "project.json").read_text(encoding="utf-8"))
    record["name"] = name
    (project / "project.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    card = projects.describe(project)
    card.update(where="local", state="made_here", creating=True, actions=["add_images", "remove"], holding=False,
                pending_files=0, unsent_records=False, holder=None, server_rev=None, local_rev=None,
                size_bytes=transfer.project_size(project), error="")
    return card


def test_a_new_project_while_it_imports_and_after_its_import_stops(screens_server, tmp_path):
    """While its images come in a card shows only progress; once stopped, only ADD IMAGES and REMOVE."""
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    screens_server.cards[:0] = [unfinished_card(IMPORTING_RUN, "Dive three"), unfinished_card(PARTWAY_RUN, "Dive two")]
    gate = threading.Event()

    def held_import(progress):
        """Stand in for NEW PROJECT's import, held partway until the screenshots are taken."""
        progress(45, 120, "Adding images: 45 of 120")
        gate.wait(120)
        return {}

    home.jobs.start("new_project", IMPORTING_RUN, held_import, lane="new_project")
    chrome, browser = start_chrome(tmp_path)
    try:
        for width in WIDTHS:
            facts = run_driver(NEW_PROJECT_STATES_DRIVER, tmp_path, f"states_{width}",
                               [browser, screens_server.base, IMPORTING_RUN, PARTWAY_RUN, str(SHOT_DIR), str(width),
                                str(SCREEN_HEIGHT)])
            assert facts["importingButtons"] == [] and facts["importingOpens"] is False, facts
            assert facts["importingChip"] == "Bringing images in"
            assert facts["importingProgress"] == "Adding images: 45 of 120 38%", facts["importingProgress"]
            assert facts["panelStatus"] == "Adding images: 45 of 120 38%"
            assert facts["partwayButtons"] == ["ADD IMAGES", ""] and facts["partwayOpens"] is False, facts
            assert facts["partwayChip"] == "Import stopped partway"
            assert facts["partwayNote"].startswith("The import stopped partway. Use ADD IMAGES with the same folder")
            assert facts["untitled"] == [] and facts["dialogUntitled"] == [], facts
            assert facts["finishFolder"] == "/Users/student/photos" and facts["finishLocked"] is None, facts
            assert facts["finishNote"].startswith("This project's import stopped partway.")
            for name in ("home_new_project_states", "add_images_partway"):
                image = np.asarray(Image.open(SHOT_DIR / f"{name}_{width}.png").convert("RGB"))
                assert image.shape[1] == width
                assert len(np.unique(image.reshape(-1, 3), axis=0)) > FEWEST_COLORS_ON_A_REAL_PAGE, name
    finally:
        gate.set()
        chrome.terminate()


# ---------------------------------------------------------------------------
# The LABELS dialog and panel after an export, through the real labels route.
# ---------------------------------------------------------------------------

LABELS_EXPORTED_DRIVER = r"""
import { writeFileSync } from 'node:fs';
import { connect, sleep, until } from '__CDP__';

const [WS_URL, BASE, RUN, OUT, WIDTH, HEIGHT] = process.argv.slice(2);
const facts = {};
let step = 'connect';

async function main() {
  const { send, attach, evaluate } = await connect(WS_URL);
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const page = await attach(targetId);
  await send('Emulation.setDeviceMetricsOverride', { width: Number(WIDTH), height: Number(HEIGHT), deviceScaleFactor: 1, mobile: false }, page);
  const shot = async (name) => {
    await evaluate(page, 'document.fonts.ready.then(() => true)');
    await sleep(350);
    const { data } = await send('Page.captureScreenshot', { format: 'png' }, page);
    writeFileSync(`${OUT}/${name}_${WIDTH}.png`, Buffer.from(data, 'base64'));
  };
  const click = (selector) => evaluate(page, `(() => { const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) throw new Error('missing ' + ${JSON.stringify(selector)}); el.scrollIntoView({ block: 'center', behavior: 'instant' }); el.click(); return true; })()`);

  step = 'labels dialog';
  await send('Page.navigate', { url: BASE + '/' }, page);
  await until('the card', () => evaluate(page, `!!document.querySelector('.run-action[data-action="labels"][data-run="${RUN}"]')`));
  await click(`.run-action[data-action="labels"][data-run="${RUN}"]`);
  await until('the labels', () => evaluate(page, `document.querySelectorAll('.modal .va-label-row').length === 3`));
  facts.dialogNames = await evaluate(page, `Array.from(document.querySelectorAll('.modal .va-label-name')).map((n) => n.textContent)`);
  facts.dialogChanges = await evaluate(page, `document.querySelectorAll('.modal .va-label-rename, .modal .va-label-remove, .modal .va-label-save').length`);
  facts.dialogNote = await evaluate(page, `document.querySelector('.modal .va-labels-note').textContent`);
  facts.dialogUse = await evaluate(page, `Array.from(document.querySelectorAll('.modal .va-label-use')).map((n) => n.textContent)`);
  await shot('labels_dialog_exported');

  step = 'labels panel';
  await send('Page.navigate', { url: `${BASE}/project/${RUN}` }, page);
  await until('the rail', () => evaluate(page, `(document.getElementById('status-labels') || {}).textContent === '3 labels'`));
  await click('#step-nav li[data-step="labels"]');
  await until('the labels', () => evaluate(page, `document.querySelectorAll('#labels-editor .va-label-row').length === 3`));
  facts.panelChanges = await evaluate(page, `document.querySelectorAll('#labels-editor .va-label-rename, #labels-editor .va-label-remove').length`);
  facts.panelNote = await evaluate(page, `document.querySelector('#labels-editor .va-labels-note').textContent`);
  await evaluate(page, 'window.scrollTo(0, 0)');
  await shot('labels_exported');
  console.log(JSON.stringify({ ok: true, facts }));
}

main().then(() => process.exit(0)).catch((error) => {
  console.log(JSON.stringify({ ok: false, step, error: String(error), facts }));
  process.exit(1);
});
"""


def test_the_labels_dialog_after_an_export_through_the_real_route(monkeypatch, tmp_path):
    """After the first export, the real labels route leaves no RENAME or REMOVE on any label."""
    from vega_annotator import folder_projects
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    photos = tmp_path / "photos"
    for index in range(3):
        helpers.write_image(photos / f"IMG_{index}.jpg", size=(320, 240), color=(40 * index, 90, 120))
    run = folder_projects.create(photos, "Reef health 2026", "healthy, Partly bleached, bleached", "JM", "labels")["run"]
    project = paths.projects_dir() / run
    store = json.loads((project / "step4test_combinedAnnotate" / "segmentations" / "segmentations.json").read_text(encoding="utf-8"))
    helpers.export_like_vega(project, {name: ["healthy"] for name in sorted(store)[:2]})
    monkeypatch.setattr(home.catalog, "autodetect_server_folder", lambda: "")
    monkeypatch.setattr(home, "annotator", FakeAnnotator())
    monkeypatch.setattr(home, "jobs", home.JobBoard())
    port = free_port()
    server = make_server("127.0.0.1", port, home.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    chrome, browser = start_chrome(tmp_path)
    try:
        for width in WIDTHS:
            facts = run_driver(LABELS_EXPORTED_DRIVER, tmp_path, f"labels_exported_{width}",
                               [browser, f"http://127.0.0.1:{port}", run, str(SHOT_DIR), str(width), str(SCREEN_HEIGHT)])
            assert facts["dialogNames"] == ["healthy", "Partly bleached", "bleached"], facts
            assert facts["dialogChanges"] == 0 and facts["panelChanges"] == 0, facts
            assert facts["dialogNote"].startswith("This project has exported frames.") and facts["panelNote"] == facts["dialogNote"]
            assert facts["dialogUse"] == ["2 masks", "unused", "unused"], facts
    finally:
        chrome.terminate()
        server.shutdown()


STOPPED_CREATE_DRIVER = r"""
import { connect, sleep, until } from '__CDP__';

const [WS_URL, BASE, WIDTH, HEIGHT] = process.argv.slice(2);
const facts = {};
let step = 'connect';

async function main() {
  const { send, attach, evaluate } = await connect(WS_URL);
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const page = await attach(targetId);
  await send('Emulation.setDeviceMetricsOverride', { width: Number(WIDTH), height: Number(HEIGHT), deviceScaleFactor: 1, mobile: false }, page);
  const click = (selector) => evaluate(page, `(() => { const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) throw new Error('missing ' + ${JSON.stringify(selector)}); el.click(); return true; })()`);
  const type = (selector, text) => evaluate(page, `(() => { const el = document.querySelector(${JSON.stringify(selector)});
    el.value = ${JSON.stringify(text)}; return true; })()`);

  step = 'create';
  await send('Page.navigate', { url: BASE + '/' }, page);
  await until('the page', () => evaluate(page, `!!document.querySelector('.run-card')`));
  await click('#new-project');
  await until('the dialog', () => evaluate(page, `!!document.querySelector('#np-folder-btn')`));
  await click('#np-folder-btn');
  await until('the scan', () => evaluate(page, `document.querySelector('#np-summary').className.includes('good')`));
  await type('#np-name', 'Dive two');
  await type('#np-labels', 'healthy');
  await type('#np-purpose', 'testing');
  await type('#va-initials', 'JM');
  await click('#np-create');
  await until('the refusal', () => evaluate(page, `document.querySelector('#np-result').className.includes('bad')`));
  await until('CREATE to go', () => evaluate(page, `document.querySelector('#np-create').hidden`));
  facts.result = await evaluate(page, `document.querySelector('#np-result').textContent`);
  facts.buttons = await evaluate(page, `Array.from(document.querySelectorAll('.modal-actions button')).filter((b) => !b.hidden).map((b) => b.textContent)`);
  console.log(JSON.stringify({ ok: true, facts }));
}

main().then(() => process.exit(0)).catch((error) => {
  console.log(JSON.stringify({ ok: false, step, error: String(error), facts }));
  process.exit(1);
});
"""


def test_a_new_project_whose_import_stopped_is_never_made_twice(screens_server, tmp_path):
    """After NEW PROJECT stops partway, the dialog offers Close, not CREATE, since the card finishes the import."""
    screens_server.cards[:0] = [unfinished_card(PARTWAY_RUN, "Dive two")]
    fakes = screens_server.fakes

    def stopped(folder, name, label_text, initials, purpose, root=None, progress=None, run=None):
        """Stand in for an import whose drive came out partway, leaving the project's card."""
        raise fakes.folder_projects.FolderError("The image folder went away after 2 of 4 images came in. "
                                                "Reconnect it, then use ADD IMAGES with the same folder.")

    fakes.folder_projects.create = stopped
    chrome, browser = start_chrome(tmp_path)
    try:
        facts = run_driver(STOPPED_CREATE_DRIVER, tmp_path, "stopped_create",
                           [browser, screens_server.base, "1280", str(SCREEN_HEIGHT)])
        assert facts["result"].startswith("The image folder went away after 2 of 4 images came in.")
        assert facts["buttons"] == ["Close"], facts
    finally:
        chrome.terminate()


FOCUS_DRIVER = r"""
import { connect, sleep, until } from '__CDP__';

const [WS_URL, BASE, WIDTH, HEIGHT] = process.argv.slice(2);
const facts = {};
let step = 'connect';

async function main() {
  const { send, attach, evaluate } = await connect(WS_URL);
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const page = await attach(targetId);
  await send('Emulation.setDeviceMetricsOverride', { width: Number(WIDTH), height: Number(HEIGHT), deviceScaleFactor: 1, mobile: false }, page);
  const click = (selector) => evaluate(page, `(() => { const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) throw new Error('missing ' + ${JSON.stringify(selector)}); el.click(); return true; })()`);
  const type = (selector, text) => evaluate(page, `(() => { const el = document.querySelector(${JSON.stringify(selector)});
    el.value = ${JSON.stringify(text)}; return true; })()`);

  step = 'create';
  await send('Page.navigate', { url: BASE + '/' }, page);
  await until('the page', () => evaluate(page, `!!document.querySelector('.run-card')`));
  await click('#new-project');
  await until('the dialog', () => evaluate(page, `!!document.querySelector('#np-folder-btn')`));
  await click('#np-folder-btn');
  await until('the scan', () => evaluate(page, `document.querySelector('#np-summary').className.includes('good')`));
  await type('#np-name', 'Focus check');
  await type('#np-labels', 'healthy');
  await type('#np-purpose', 'testing');
  await type('#va-initials', 'JM');
  await click('#np-create');
  step = 'result';
  await until('the result', () => evaluate(page, `document.querySelector('#np-result').className.includes('good')`));
  await until('the new card', () => evaluate(page, `!!document.querySelector('.run-action[data-action="open"][data-run="run_26-11-09_JM_focus_check"]')`));
  await sleep(2500);
  facts.active_in_modal = await evaluate(page, `!!(document.activeElement && document.activeElement.closest('.modal'))`);
  step = 'escape';
  await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 }, page);
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 }, page);
  await sleep(600);
  facts.closed_by_escape = await evaluate(page, `!document.querySelector('.modal-backdrop.open')`);
  console.log(JSON.stringify({ ok: true, facts }));
}

main().then(() => process.exit(0)).catch((error) => {
  console.log(JSON.stringify({ ok: false, step, error: String(error), facts }));
  process.exit(1);
});
"""


def test_the_new_project_dialog_keeps_focus_while_it_lists_skipped_files(screens_server, tmp_path):
    """When NEW PROJECT ends with skipped files, the dialog stays and keeps the keyboard, so Escape closes it."""
    fakes = screens_server.fakes
    made_here = dict(screens_server.cards[0])
    original = fakes.folder_projects.create

    def create(folder, name, label_text, initials, purpose, root=None, progress=None, run=None):
        """Make the project as the stand-in does, then give the home screen its card, as the catalog would."""
        result = original(folder, name, label_text, initials, purpose, root=root, progress=progress, run=run)
        screens_server.cards.insert(0, {**made_here, "run": result["run"], "name": name})
        return result

    fakes.folder_projects.create = create
    chrome, browser = start_chrome(tmp_path)
    try:
        facts = run_driver(FOCUS_DRIVER, tmp_path, "np_focus", [browser, screens_server.base, "1280", str(SCREEN_HEIGHT)])
        assert facts == {"active_in_modal": True, "closed_by_escape": True}, facts
    finally:
        chrome.terminate()
