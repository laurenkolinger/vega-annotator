/**
 * End-to-end click path in a real headless Chrome, with real popups and real key presses:
 * home screen, Bring In, Open with initials and purpose, the project window, Open Annotator,
 * the loading window, VEGA's annotator page, a click and an accept, Put Back.
 *
 * Usage: node --experimental-websocket chrome_flow.mjs <browser ws url> <home url> <run> <out dir>
 * The last stdout line is one JSON object: {ok, step, error, facts}.
 */
import { writeFileSync } from 'node:fs';
import path from 'node:path';
import { connect, sleep, until } from './cdp.mjs';

const [WS_URL, HOME_URL, RUN, OUT_DIR] = process.argv.slice(2);
const facts = {};
let step = 'connect';
let send;
let attach;
let evaluate;

async function press(sessionId, key, code, vk, text) {
  const base = { key, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk };
  await send('Input.dispatchKeyEvent', { type: 'keyDown', ...base, text }, sessionId);
  await send('Input.dispatchKeyEvent', { type: 'keyUp', ...base }, sessionId);
}

async function clickAt(sessionId, x, y) {
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, sessionId);
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 }, sessionId);
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 }, sessionId);
}

async function clickSelector(sessionId, selector) {
  const box = await until('element ' + selector, () => evaluate(sessionId, `(() => {
    const el = document.querySelector(${JSON.stringify(selector)});
    if (!el || el.disabled) return null;
    // Instant, because the page scrolls smoothly and a click mid-scroll lands somewhere else.
    el.scrollIntoView({ block: 'center', behavior: 'instant' });
    const r = el.getBoundingClientRect();
    return r.width ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : null;
  })()`));
  await clickAt(sessionId, box.x, box.y);
}

async function targetsNamed(part) {
  const { targetInfos } = await send('Target.getTargets');
  return targetInfos.filter((info) => info.type === 'page' && info.url.includes(part));
}

async function shot(sessionId, name) {
  const { data } = await send('Page.captureScreenshot', { format: 'png' }, sessionId);
  writeFileSync(path.join(OUT_DIR, name + '.png'), Buffer.from(data, 'base64'));
}

async function main() {
  ({ send, attach, evaluate } = await connect(WS_URL));
  await send('Target.setDiscoverTargets', { discover: true });
  const { targetId } = await send('Target.createTarget', { url: HOME_URL });
  const home = await attach(targetId);

  step = 'home shows the project';
  const action = (name) => `.run-action[data-action="${name}"][data-run="${RUN}"]`;
  await clickSelector(home, action('bring-in'));

  step = 'bring in asks for initials';
  await until('the initials box', () => evaluate(home, `!!document.querySelector('#va-initials')`));
  await sleep(150);
  await evaluate(home, `document.querySelector('#va-initials').focus()`);
  await send('Input.insertText', { text: 'at' }, home);
  await press(home, 'Enter', 'Enter', 13, '\r');

  step = 'bring in finishes';
  await until('the card holds the project', () => evaluate(home, `!!document.querySelector('${action('open')}')`));

  step = 'the description editor works from the keyboard';
  const description = `.run-card-desc[data-run="${RUN}"]`;
  const typeDescription = async (text, key, code, vk) => {
    await evaluate(home, `document.querySelector('${description}').focus()`);
    await press(home, 'Enter', 'Enter', 13, '\r');
    await until('the editor', () => evaluate(home, `document.activeElement.classList.contains('va-desc-edit')`));
    await send('Input.insertText', { text }, home);
    await press(home, key, code, vk, key === 'Enter' ? '\r' : undefined);
    await until('the editor to close', () => evaluate(home, `!document.querySelector('.va-desc-edit')`));
    await until('focus back on the description', () => evaluate(home, `document.activeElement.matches('${description}')`));
  };
  await typeDescription('thrown away', 'Escape', 'Escape', 27);
  facts.descriptionAfterEscape = await evaluate(home, `document.querySelector('${description}').textContent`);
  await typeDescription('round two', 'Enter', 'Enter', 13);
  await until('the saved description', () => evaluate(home, `document.querySelector('${description}').textContent === 'round two'`));

  step = 'the open button asks for initials and purpose';
  await clickSelector(home, action('open'));

  step = 'the prompt offers the suggested purpose';
  await until('the purpose box', () => evaluate(home, `!!document.querySelector('#va-purpose')`));
  facts.purposeSuggestion = await evaluate(home, `document.querySelector('#va-purpose').value`);
  facts.initialsSeeded = await evaluate(home, `document.querySelector('#va-initials').value`);
  await clickSelector(home, '.modal-actions .btn-accent');

  step = 'the project window opens as a popup';
  const projectTarget = await until('the project window', async () => (await targetsNamed('/project/' + RUN))[0]);
  const project = await attach(projectTarget.targetId);
  await until('the frames readout', () => evaluate(project, `document.getElementById('frames-readout').textContent.includes('frames left')`));
  facts.readout = await evaluate(project, `document.getElementById('frames-readout').textContent`);
  facts.railStep1 = await evaluate(project, `document.getElementById('status-1').textContent`);
  await shot(project, 'project');

  step = 'the annotator opens through the loading window';
  await clickSelector(project, '#s4test-run-btn');
  const annotatorInfo = await until('the annotator page', async () => {
    const { targetInfos } = await send('Target.getTargets');
    return targetInfos.find((info) => info.type === 'page' && /127\.0\.0\.1:508\d\/$/.test(info.url));
  });
  const annotator = await attach(annotatorInfo.targetId);

  step = 'VEGA page shows a frame and the readout';
  await until('the first frame', () => evaluate(annotator, `document.getElementById('image-filename') && document.getElementById('image-filename').textContent.includes('.jpeg')`));
  await until('the frames left readout', () => evaluate(annotator, `(document.getElementById('va-frames-left') || {}).textContent`));
  facts.left = await evaluate(annotator, `document.getElementById('va-frames-left').textContent`);
  facts.title = await evaluate(annotator, `document.title`);
  facts.tutorialButton = await evaluate(annotator, `Array.from(document.querySelectorAll('button')).filter((b) => /tutorial/i.test(b.textContent)).length`);
  facts.fontsLoaded = await evaluate(annotator, `document.fonts.ready.then(() => document.fonts.check('14px Inter') && document.fonts.check('13px "Space Mono"'))`);
  facts.googleLinks = await evaluate(annotator, `document.querySelectorAll('link[href*="googleapis"]').length`);

  step = 'a click makes a mask and Enter accepts it';
  await press(annotator, '1', 'Digit1', 49, '1');
  const canvas = await evaluate(annotator, `(() => { const c = document.querySelector('canvas'); const r = c.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
  await sleep(500);
  await clickAt(annotator, canvas.x, canvas.y);
  await until('a mask row', () => evaluate(annotator, `document.body.innerText.includes('1 pending') || document.body.innerText.includes('0/1 accepted')`));
  await press(annotator, 'Enter', 'Enter', 13, '\r');
  await until('the accepted count', () => evaluate(annotator, `document.body.innerText.includes('1/1 accepted')`));
  await sleep(2500);
  await shot(annotator, 'annotator');

  step = 'put back closes the annotator and sends the work';
  await evaluate(project, `document.querySelector('#step-nav li[data-step="putback"]').click()`);
  await until('changed files to send', () => evaluate(project, `document.getElementById('putback-readout').textContent.includes('changed file')`), 20000);
  await clickSelector(project, '#putback-btn');
  await until('the put back result', () => evaluate(project, `document.getElementById('putback-result').className.includes('good')`));
  facts.putBack = await evaluate(project, `document.getElementById('putback-result').textContent`);
  facts.annotatorWindowClosed = (await targetsNamed(':508')).length === 0;
  await shot(project, 'put_back');
  await until('the project window to follow the state', () => evaluate(project, `document.getElementById('s4test-run-btn').disabled`), 20000);
  facts.putBackDisabled = await evaluate(project, `document.getElementById('putback-btn').disabled`);
  facts.stateNote = await evaluate(project, `document.getElementById('putback-readout').textContent`);

  step = 'the home card reads put back';
  await until('the back on the server chip', () => evaluate(home, `document.querySelector('.run-card[data-run="${RUN}"]').innerText.includes('Back on the server')`), 20000);
  await shot(home, 'home_after');
  console.log(JSON.stringify({ ok: true, step: 'done', facts }));
}

main().then(() => process.exit(0)).catch((error) => {
  console.log(JSON.stringify({ ok: false, step, error: String(error), facts }));
  process.exit(1);
});
