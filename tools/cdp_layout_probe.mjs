// Measure the real 榜单页 layout through the Chrome DevTools Protocol.
//
//   node cdp_probe.mjs <url> <width> <height> [port]
//
// Prints one JSON line per card header: title line count, where the selects /
// board badge / pill row actually landed, and any horizontal overflow.
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const CHROME = 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe';
const target = process.argv[2] || 'http://127.0.0.1:8765/index.html';
const width = Number(process.argv[3] || 1400);
const height = Number(process.argv[4] || 1300);
const port = Number(process.argv[5] || 9333);

const profile = mkdtempSync(join(tmpdir(), 'cdp-'));
const chrome = spawn(
  CHROME,
  [
    '--headless=new',
    '--disable-gpu',
    '--hide-scrollbars',
    '--no-sandbox',
    '--no-first-run',
    '--disable-extensions',
    '--disable-background-networking',
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    `--window-size=${width},${height}`,
    '--force-device-scale-factor=1',
    target,
  ],
  { stdio: 'ignore', detached: false },
);

const PROBE = `(() => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const round = (value) => Math.round(value);
  const box = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: round(r.x), y: round(r.y), w: round(r.width), h: round(r.height), right: round(r.right), bottom: round(r.bottom) };
  };
  const lines = (el) => {
    if (!el) return 0;
    const lh = parseFloat(getComputedStyle(el).lineHeight) || parseFloat(getComputedStyle(el).fontSize) * 1.2;
    return Math.max(1, Math.round(el.getBoundingClientRect().height / lh));
  };
  return (async () => {
    for (let i = 0; i < 60; i += 1) {
      if (document.querySelectorAll('.board-card').length >= 10) break;
      await wait(250);
    }
    await wait(600);
    const out = [];
    Array.prototype.forEach.call(document.querySelectorAll('.board-card'), (card) => {
      const key = String(card.getAttribute('data-live-key') || '').replace('board:', '');
      const head = card.querySelector('.board-head');
      if (!head) return;
      const copy = head.querySelector('.board-head-copy');
      const title = head.querySelector('h3');
      const badge = head.querySelector('.board-head-actions > strong');
      const acts = head.querySelector('.board-head-actions');
      const foot = head.querySelector('.board-head-foot');
      const controls = Array.prototype.map.call(head.querySelectorAll('.board-period-control'), (c) => box(c));
      const selects = Array.prototype.map.call(head.querySelectorAll('select'), (s) => box(s));
      const pills = box(head.querySelector('.board-switches'));
      const cardBox = box(card);
      const inner = cardBox.right - parseFloat(getComputedStyle(card).paddingRight || 0);
      out.push({
        key,
        cardW: cardBox.w,
        headW: box(head).w,
        titleLines: lines(title),
        titleW: box(title).w,
        copyW: box(copy).w,
        copyBottom: box(copy).bottom,
        badge: badge ? { w: box(badge).w, h: box(badge).h, y: box(badge).y, text: badge.textContent.trim(), fs: getComputedStyle(badge).fontSize } : null,
        badgeWidthAuto: badge ? (() => { const prev = badge.style.width; badge.style.width = 'auto'; const w = round(badge.getBoundingClientRect().width); badge.style.width = prev; return w; })() : null,
        controls,
        selects,
        selectRows: (() => { const ys = selects.map((s) => s.y); return Array.from(new Set(ys)).length; })(),
        pillRow: pills ? { y: pills.y, w: pills.w, h: pills.h } : null,
        footRowW: foot ? box(foot).w : null,
        actsRightGap: acts ? round(inner - box(acts).right) : null,
        footRightGap: pills ? round(inner - pills.right) : null,
        strongRightGap: badge ? round(inner - box(badge).right) : null,
        col2LeftGap: acts ? round(inner - box(acts).right) : null,
        overflow: card.scrollWidth - card.clientWidth,
        headOverflow: head.scrollWidth - head.clientWidth,
      });
    });
    return JSON.stringify({ url: location.href, view: [innerWidth, innerHeight], cards: out });
  })();
})()`;

async function findTarget() {
  for (let i = 0; i < 80; i += 1) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/list`);
      const list = await res.json();
      const page = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
      if (page) return page;
    } catch {
      /* chrome still booting */
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error('no debuggable page target');
}

function evaluate(wsUrl, expression, emulateWidth) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(wsUrl);
    const timer = setTimeout(() => { socket.close(); reject(new Error('probe timeout')); }, 60000);
    socket.addEventListener('error', (event) => { clearTimeout(timer); reject(new Error(`ws error: ${event.message || 'unknown'}`)); });
    socket.addEventListener('open', () => {
      if (emulateWidth) {
        // Headless refuses to open a window narrower than ~500px, so squeeze the
        // viewport through the emulation domain to reach the narrow-panel case.
        socket.send(JSON.stringify({
          id: 100,
          method: 'Emulation.setDeviceMetricsOverride',
          params: { width: emulateWidth, height, deviceScaleFactor: 1, mobile: false },
        }));
      }
      socket.send(JSON.stringify({
        id: 1,
        method: 'Runtime.evaluate',
        params: { expression, awaitPromise: true, returnByValue: true, timeout: 55000 },
      }));
    });
    socket.addEventListener('message', (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id !== 1) return;
      clearTimeout(timer);
      socket.close();
      if (msg.result?.exceptionDetails) {
        reject(new Error(JSON.stringify(msg.result.exceptionDetails).slice(0, 800)));
        return;
      }
      const value = msg.result?.result?.value;
      if (value === undefined) {
        reject(new Error('no value: ' + JSON.stringify(msg).slice(0, 1200)));
        return;
      }
      resolve(value);
    });
  });
}

async function evaluateRetry(wsUrl, expression, attempts, emulateWidth) {
  let last = '';
  for (let i = 0; i < attempts; i += 1) {
    try {
      return await evaluate(wsUrl, expression, emulateWidth);
    } catch (error) {
      last = error.message;
      // "Execution context was destroyed" means the tab is still navigating;
      // just wait for the next document and probe again.
      if (!/destroyed|timeout|no value/i.test(last)) throw error;
      await new Promise((resolve) => setTimeout(resolve, 900));
    }
  }
  throw new Error(last);
}

try {
  const emulate = Number(process.argv[6] || 0) || 0;
  const page = await findTarget();
  await new Promise((resolve) => setTimeout(resolve, 1500));
  const raw = await evaluateRetry(page.webSocketDebuggerUrl, PROBE, 12, emulate);
  const data = JSON.parse(raw);
  console.log(`viewport=${data.view[0]}x${data.view[1]}`);
  data.cards.forEach((card) => {
    const sel = card.selects.map((s) => `${s.w}x${s.h}@y${s.y}`).join(' ');
    console.log([
      card.key.padEnd(18),
      `card=${card.cardW}`,
      `copy=${card.copyW}`,
      `title=${card.titleLines}行(${card.titleW})`,
      card.badge ? `badge=${card.badge.text}:${card.badge.w}x${card.badge.h}@y${card.badge.y}fs${card.badge.fs}(auto${card.badgeWidthAuto})` : 'badge=-',
      `selects=[${sel}]`,
      `ctlW=${card.controls.map((c) => c.w).join('+')}`,
      `selRows=${card.selectRows}`,
      card.pillRow ? `pills=y${card.pillRow.y}(w${card.pillRow.w})` : 'pills=-',
      `gapActs=${card.actsRightGap}`,
      `gapFoot=${card.footRightGap}`,
      `gapBadge=${card.strongRightGap}`,
      `of=${card.overflow}/${card.headOverflow}`,
    ].join(' | '));
  });
  console.log('\nTITLE_LINES_MAX=' + Math.max(...data.cards.map((c) => c.titleLines)));
  console.log('OVERFLOW_TOTAL=' + data.cards.reduce((sum, c) => sum + Math.max(0, c.overflow) + Math.max(0, c.headOverflow), 0));
} catch (error) {
  console.error('PROBE FAILED: ' + error.message);
  process.exitCode = 1;
} finally {
  try { chrome.kill(); } catch { /* ignore */ }
}
