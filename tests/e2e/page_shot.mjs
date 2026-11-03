/**
 * Save a screenshot of VEGA's annotator page once its first frame shows, at one width.
 *
 * Usage: node --experimental-websocket page_shot.mjs <browser ws url> <page url> <width> <height> <out png>
 * The last stdout line is one JSON object: {ok, error, left}.
 */
import { writeFileSync } from 'node:fs';
import { connect, sleep, until } from './cdp.mjs';

const [WS_URL, PAGE_URL, WIDTH, HEIGHT, OUT] = process.argv.slice(2);
const SETTLE_MS = 800;

async function main() {
  const { send, attach, evaluate } = await connect(WS_URL);
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const page = await attach(targetId);
  await send('Emulation.setDeviceMetricsOverride', {
    width: Number(WIDTH), height: Number(HEIGHT), deviceScaleFactor: 1, mobile: false }, page);
  await send('Page.navigate', { url: PAGE_URL }, page);
  await until('the first frame', () => evaluate(page, `(() => {
    const name = document.getElementById('image-filename');
    return !!name && name.textContent.includes('.jpeg');
  })()`));
  await evaluate(page, 'document.fonts.ready.then(() => true)');
  await sleep(SETTLE_MS);
  const left = await evaluate(page, `(document.getElementById('va-frames-left') || {}).textContent || ''`);
  const { data } = await send('Page.captureScreenshot', { format: 'png' }, page);
  writeFileSync(OUT, Buffer.from(data, 'base64'));
  console.log(JSON.stringify({ ok: true, left }));
}

main().then(() => process.exit(0)).catch((error) => {
  console.log(JSON.stringify({ ok: false, error: String(error) }));
  process.exit(1);
});
