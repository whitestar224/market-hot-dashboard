// Verify a monitoring sub-board actually renders in a real browser.
//
//   node tools/cdp_subboard_probe.mjs <url> <w> <h> <port> <waitSelector> [countSelector|click:selector...]
//
// Prints one JSON object: page title/url, whether it fell back to login, the
// grid's class list, how many nodes each countSelector matched, horizontal
// overflow, and every uncaught exception / console.error the page produced.
//
// `click:<selector>` 会**先点它再数数**（按给出顺序依次点，每次等 500ms）。
// 自绘控件（下拉、折叠）光看 DOM 结构说明不了交互是否真的生效。
//
// Rationale: 子板块（被夹 / Dev 发币 / 做市商…）改的是「一段模板字符串 + 一堆事件
// 委托」，语法检查查不到「渲染出来是空白」。这里直接对**运行中的服务**取数，
// 类名、数量、报错三者同时为真才算过。
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const CHROME = 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe';
const target = process.argv[2] || 'http://127.0.0.1:8765/price-watch.html?mode=marketmaker';
const width = Number(process.argv[3] || 1400);
const height = Number(process.argv[4] || 1200);
const port = Number(process.argv[5] || 9347);
const waitSelector = process.argv[6] || '.mm-console';
// 以 `click:` 开头的参数是「先点它，再数数」——用来验收下拉展开 / 选项切换这类交互。
const rawSelectors = process.argv.slice(7);
const clickSelectors = rawSelectors.filter((s) => s.startsWith('click:')).map((s) => s.slice(6));
const countSelectors = rawSelectors.filter((s) => !s.startsWith('click:'));

const profile = mkdtempSync(join(tmpdir(), 'cdp-sub-'));
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

const PROBE = (selector, counts, clicks) => `(() => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  return (async () => {
    for (let i = 0; i < 80; i += 1) {
      if (document.querySelector(${JSON.stringify(selector)})) break;
      await wait(250);
    }
    await wait(1200);
    // 交互验收：自绘控件必须真的点得动，光看 DOM 结构说明不了什么。
    const clicked = [];
    for (const sel of ${JSON.stringify(clicks)}) {
      const node = document.querySelector(sel);
      if (!node) { clicked.push({ sel, ok: false }); continue; }
      node.click();
      clicked.push({ sel, ok: true });
      await wait(500);
    }
    const grid = document.querySelector('.price-watch-grid') || document.querySelector('main');
    const out = {
      url: location.href,
      title: document.title,
      bodyClass: document.body.className,
      login: /login/i.test(location.href),
      gridClasses: grid ? String(grid.className) : '',
      found: Boolean(document.querySelector(${JSON.stringify(selector)})),
      counts: {},
      overflow: grid ? grid.scrollWidth - grid.clientWidth : -1,
      docOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      status: (document.querySelector('#watchStatus') || {}).textContent || '',
      heading: (document.querySelector('#watchHeadingTitle') || {}).textContent || '',
      empty: Boolean(document.querySelector('.price-watch-empty')),
      clicked,
      pickerLabel: ((document.querySelector('.mm-firm-picker-toggle b') || {}).textContent || '').trim(),
    };
    for (const sel of ${JSON.stringify(counts)}) out.counts[sel] = document.querySelectorAll(sel).length;
    // 读「当前激活的 tab」，不要写死某一个子板块 —— 写死的话每新加一个子板块，
    // 这里都会报上一个子板块的名字（看着像分流失效，其实只是探针在撒谎）。
    const activeTab = [...document.querySelectorAll('[data-watch-mode]')].find((el) => el.classList.contains('active'));
    out.tab = activeTab ? { mode: activeTab.dataset.watchMode, text: activeTab.textContent.trim(), active: true } : null;
    return JSON.stringify(out);
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

function probe(wsUrl) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(wsUrl);
    const errors = [];
    const timer = setTimeout(() => { socket.close(); reject(new Error('probe timeout')); }, 70000);
    socket.addEventListener('error', (event) => { clearTimeout(timer); reject(new Error(`ws error: ${event.message || 'unknown'}`)); });
    socket.addEventListener('open', () => {
      socket.send(JSON.stringify({ id: 90, method: 'Runtime.enable' }));
      socket.send(JSON.stringify({ id: 91, method: 'Log.enable' }));
      socket.send(JSON.stringify({
        id: 1,
        method: 'Runtime.evaluate',
        params: { expression: PROBE(waitSelector, countSelectors, clickSelectors), awaitPromise: true, returnByValue: true, timeout: 60000 },
      }));
    });
    socket.addEventListener('message', (event) => {
      const msg = JSON.parse(event.data);
      if (msg.method === 'Runtime.exceptionThrown') {
        const detail = msg.params?.exceptionDetails || {};
        errors.push(`exception: ${detail.exception?.description || detail.text || 'unknown'}`);
        return;
      }
      if (msg.method === 'Runtime.consoleAPICalled' && msg.params?.type === 'error') {
        errors.push(`console.error: ${(msg.params.args || []).map((a) => a.value ?? a.description ?? '').join(' ').slice(0, 300)}`);
        return;
      }
      if (msg.method === 'Log.entryAdded' && msg.params?.entry?.level === 'error') {
        errors.push(`log: ${String(msg.params.entry.text || '').slice(0, 300)}`);
        return;
      }
      if (msg.id !== 1) return;
      clearTimeout(timer);
      const value = msg.result?.result?.value;
      setTimeout(() => { socket.close(); }, 200);
      socket.onclose = null;
      const finish = () => {
        if (msg.result?.exceptionDetails) {
          reject(new Error(JSON.stringify(msg.result.exceptionDetails).slice(0, 800)));
          return;
        }
        if (value === undefined) {
          reject(new Error('no value: ' + JSON.stringify(msg).slice(0, 1200)));
          return;
        }
        resolve({ data: JSON.parse(value), errors });
      };
      setTimeout(finish, 300);
    });
  });
}

try {
  const page = await findTarget();
  await new Promise((resolve) => setTimeout(resolve, 1800));
  let result;
  let last = '';
  for (let i = 0; i < 6; i += 1) {
    try {
      result = await probe(page.webSocketDebuggerUrl);
      break;
    } catch (error) {
      last = error.message;
      if (!/destroyed|timeout|no value/i.test(last)) throw error;
      await new Promise((resolve) => setTimeout(resolve, 900));
    }
  }
  if (!result) throw new Error(last || 'probe failed');
  console.log(JSON.stringify({ ...result.data, errors: result.errors }, null, 2));
  if (!result.data.found) process.exitCode = 2;
  if (result.data.errors?.length) process.exitCode = 3;
} catch (error) {
  console.error('PROBE FAILED: ' + error.message);
  process.exitCode = 1;
} finally {
  chrome.kill();
}
