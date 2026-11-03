/**
 * The small part of the Chrome DevTools Protocol these tests use.
 */
const POLL_MS = 100;
const WAIT_MS = 180000;

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export async function until(what, check, limit = WAIT_MS) {
  const end = Date.now() + limit;
  while (Date.now() < end) {
    const value = await check();
    if (value) return value;
    await sleep(POLL_MS);
  }
  throw new Error('timed out waiting for ' + what);
}

/** Connect to a browser's debugging address. Returns its send, attach and evaluate calls. */
export async function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  const pending = new Map();
  let nextId = 1;

  ws.onmessage = (message) => {
    const data = JSON.parse(message.data);
    if (data.id && pending.has(data.id)) {
      const { resolve, reject, method } = pending.get(data.id);
      pending.delete(data.id);
      if (data.error) reject(new Error(method + ': ' + data.error.message));
      else resolve(data.result);
    }
  };
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });

  function send(method, params = {}, sessionId) {
    const id = nextId++;
    ws.send(JSON.stringify({ id, method, params, sessionId }));
    return new Promise((resolve, reject) => pending.set(id, { resolve, reject, method }));
  }

  async function attach(targetId) {
    const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
    await send('Runtime.enable', {}, sessionId);
    await send('Page.enable', {}, sessionId);
    return sessionId;
  }

  async function evaluate(sessionId, expression) {
    const { result, exceptionDetails } = await send('Runtime.evaluate', {
      expression, returnByValue: true, awaitPromise: true, userGesture: true }, sessionId);
    if (exceptionDetails) throw new Error('page error: ' + JSON.stringify(exceptionDetails).slice(0, 300));
    return result.value;
  }

  return { send, attach, evaluate };
}
