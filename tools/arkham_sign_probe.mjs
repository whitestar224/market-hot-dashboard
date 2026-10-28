// 在 arkm.com 页面上下文里翻前端 JS，找 `X-Payload` 的签名算法。
//
//   node tools/arkham_sign_probe.mjs [tokenUrl] [port]
//
// 背景：探针确认网页端调 api.arkm.com **不需要 API key**，只带
//   X-Timestamp: <unix 秒>
//   X-Payload:   <64 位 hex>
// 同 timestamp 下不同 path 的 payload 不同，同 path 不同 query 的 payload 相同
// → 签名对象是「path + timestamp」并混了某个前端口令。暴力枚举常见拼法已失败，
// 只能把口令从 bundle 里挖出来。
//
// 做法：等 Cloudflare 挑战过去后，在页面里跑一段 fetch 遍历所有 .js 资源，
// 搜 "X-Payload" / "x-timestamp" 并把命中处的上下文打出来。
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const CHROME = 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe';
const target = process.argv[2] || 'https://arkm.com/explorer/token/janction';
const port = Number(process.argv[3] || 9378);

const profileDir = join(process.cwd(), '.runtime-cache', 'arkham-chrome-profile');
mkdirSync(profileDir, { recursive: true });

const chrome = spawn(CHROME, [
  '--disable-gpu',
  '--no-sandbox',
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-extensions',
  '--window-size=1440,1000',
  `--user-data-dir=${profileDir}`,
  `--remote-debugging-port=${port}`,
  'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function findTarget() {
  for (let i = 0; i < 80; i += 1) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/list`);
      const list = await res.json();
      const page = list.find((item) => item.type === 'page');
      if (page) return page;
    } catch {}
    await sleep(200);
  }
  throw new Error('找不到 CDP 页面');
}

function connect(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    ws.onopen = () => resolve(ws);
    ws.onerror = (err) => reject(err);
  });
}

let nextId = 0;
const pending = new Map();
function send(ws, method, params = {}) {
  const id = (nextId += 1);
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve) => pending.set(id, resolve));
}

const page = await findTarget();
const ws = await connect(page.webSocketDebuggerUrl);
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && pending.has(msg.id)) {
    pending.get(msg.id)(msg.result || {});
    pending.delete(msg.id);
  }
};

await send(ws, 'Page.enable');
await send(ws, 'Runtime.enable');
await send(ws, 'Page.navigate', { url: target });

// 等挑战解除（title 不再是「请稍候…」）
const CHALLENGE = /just a moment|请稍候|正在验证|attention required|^$/i;
let title = '';
for (let i = 0; i < 40; i += 1) {
  await sleep(2500);
  const r = await send(ws, 'Runtime.evaluate', { expression: 'document.title', returnByValue: true });
  title = (r.result && r.result.value) || '';
  if (title && !CHALLENGE.test(title)) break;
}
await sleep(5000);

// needle 必须在 Node 侧定值再注入 —— 浏览器里没有 process.env。
const NEEDLE = JSON.stringify(process.env.ARKHAM_NEEDLE || 'X-Payload');

const scanner = `(async () => {
  const urls = Array.from(new Set(
    performance.getEntriesByType('resource').map(e => e.name).filter(u => u.endsWith('.js'))
  ));
  const hits = [];
  const NEEDLE = ${NEEDLE};
  for (const u of urls) {
    let t = '';
    try { t = await (await fetch(u)).text(); } catch (e) { continue; }
    for (const needle of [NEEDLE, NEEDLE.toLowerCase()]) {
      let i = t.indexOf(needle);
      while (i >= 0) {
        hits.push({ url: u, needle, snippet: t.slice(Math.max(0, i - 2000), i + 1200) });
        i = t.indexOf(needle, i + 1);
        if (hits.length > 40) break;
      }
      if (hits.length > 40) break;
    }
    if (hits.length > 40) break;
  }
  return JSON.stringify({ scanned: urls.length, hits });
})()`;

const res = await send(ws, 'Runtime.evaluate', {
  expression: scanner,
  awaitPromise: true,
  returnByValue: true,
});

let parsed = {};
try {
  parsed = JSON.parse((res.result && res.result.value) || '{}');
} catch {
  parsed = { raw: (res.result && res.result.value) || '', exception: res.exceptionDetails };
}

writeFileSync(
  join(process.cwd(), '.runtime-cache', '_arkham_sign.json'),
  JSON.stringify({ title, ...parsed }, null, 2),
);

console.log('title:', title);
console.log('scanned:', parsed.scanned, 'hits:', (parsed.hits || []).length);
for (const h of (parsed.hits || []).slice(0, 3)) {
  console.log('\n=== ' + h.url);
  console.log(h.snippet);
}
ws.close();
chrome.kill();
process.exit(0);
