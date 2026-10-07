// Screenshot the two crowded 榜单 card headers straight off the running server.
//
//   node cdp_shot.mjs <url> <width> <height> <port> <out-prefix> [emulateWidth]
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve as resolvePath } from 'node:path';

const CHROME = 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe';
const target = process.argv[2];
const width = Number(process.argv[3] || 1400);
const height = Number(process.argv[4] || 1200);
const port = Number(process.argv[5] || 9500);
const prefix = process.argv[6] || 'shot';
const emulate = Number(process.argv[7] || 0) || 0;
const shotHeight = Number(process.argv[8] || 330);

const profile = mkdtempSync(join(tmpdir(), 'cdp-shot-'));
const chrome = spawn(CHROME, [
  '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-sandbox',
  '--no-first-run', '--disable-extensions', '--disable-background-networking',
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  `--window-size=${width},${height}`, '--force-device-scale-factor=1', target,
], { stdio: 'ignore' });

const SCROLL_TO = (id) => `(async () => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  for (let i = 0; i < 60; i += 1) {
    if (document.querySelectorAll('.board-card').length >= 10) break;
    await wait(250);
  }
  await wait(700);
  const card = document.querySelector('.board-card[data-live-key="board:${id}"]');
  if (!card) return 'null';
  const head = card.querySelector('.board-head');
  head.scrollIntoView({ block: 'start', inline: 'nearest' });
  await wait(500);
  const r = head.getBoundingClientRect();
  return JSON.stringify({ x: r.x, y: r.y, w: r.width, h: r.height, scrollY: window.scrollY });
})()`;

function cdp(wsUrl, handler) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(wsUrl);
    let seq = 0;
    const waiters = new Map();
    const send = (method, params) => new Promise((res, rej) => {
      seq += 1;
      waiters.set(seq, { res, rej });
      socket.send(JSON.stringify({ id: seq, method, params }));
    });
    const timer = setTimeout(() => { socket.close(); reject(new Error('cdp timeout')); }, 90000);
    socket.addEventListener('message', (event) => {
      const msg = JSON.parse(event.data);
      const waiter = waiters.get(msg.id);
      if (!waiter) return;
      waiters.delete(msg.id);
      if (msg.error) waiter.rej(new Error(JSON.stringify(msg.error)));
      else waiter.res(msg.result);
    });
    socket.addEventListener('error', (event) => { clearTimeout(timer); reject(new Error(String(event.message || 'ws error'))); });
    socket.addEventListener('open', async () => {
      let value;
      try {
        value = await handler(send);
        clearTimeout(timer);
        resolve(value);
      } catch (error) {
        clearTimeout(timer);
        reject(error);
      } finally {
        socket.close();
      }
    });
  });
}

async function findTarget() {
  for (let i = 0; i < 80; i += 1) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      const page = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
      if (page) return page;
    } catch { /* booting */ }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error('no target');
}

async function shootOnce(page) {
  return cdp(page.webSocketDebuggerUrl, async (send) => {
    // Drive both dimensions through the emulation domain: it is the only way to
    // get a short viewport (so the shot is a readable strip) and to go below the
    // ~500px minimum window width.
    await send('Emulation.setDeviceMetricsOverride', {
      width: emulate || width,
      height: shotHeight,
      deviceScaleFactor: 1,
      mobile: false,
    });
    const written = [];
    for (const id of ['ave', 'gmgn-hot-search']) {
      const evaluated = await send('Runtime.evaluate', { expression: SCROLL_TO(id), awaitPromise: true, returnByValue: true });
      if (evaluated.exceptionDetails) throw new Error('eval error: ' + JSON.stringify(evaluated.exceptionDetails).slice(0, 400));
      if (evaluated.result.value === 'null') throw new Error(`card ${id} not found`);
      const shot = await send('Page.captureScreenshot', { format: 'png' });
      const file = resolvePath(`${prefix}_${id}.png`);
      writeFileSync(file, Buffer.from(shot.data, 'base64'));
      written.push(file);
    }
    return written;
  });
}

try {
  const page = await findTarget();
  await new Promise((r) => setTimeout(r, 1500));
  // The tab may still be navigating when we attach, which destroys the execution
  // context mid-probe; just retry against the next document.
  let files = null;
  let last = '';
  for (let attempt = 0; attempt < 8 && !files; attempt += 1) {
    try {
      files = await shootOnce(page);
    } catch (error) {
      last = error.message;
      if (!/destroyed|timeout/i.test(last)) throw error;
      await new Promise((r) => setTimeout(r, 1200));
    }
  }
  if (!files) throw new Error(last);
  console.log(files.join('\n'));
} catch (error) {
  console.error('SHOT FAILED: ' + error.message);
  process.exitCode = 1;
} finally {
  try { chrome.kill(); } catch { /* ignore */ }
}
