// Capture what the Arkham web app actually calls (endpoint + auth headers).
//
//   node tools/arkham_probe.mjs [tokenUrl] [maxWaitMs] [port]
//
// 为什么要这么干：arkm.com 是 Next.js 的**客户端渲染**壳子，curl 拿到的 HTML 里没有
// 代币数据；而 api.arkm.com 直连会回
// `{"message":"invalid timestamp format, please sign up for an api key :)"}`。
// 唯一能确定「网页端用哪个端点、带什么头」的办法就是**看它自己发什么请求**。
//
// 2026-10-21 修订：旧版用 mkdtemp 开新 profile + 固定等 12s，被 Cloudflare 托管挑战
// （"Just a moment..."）拦住，只抓到 cdn-cgi/challenge-platform。新版三处改动：
//   1. **持久 profile**（`.runtime-cache/arkham-chrome-profile`）→ cf_clearance 跨次复用，
//      第一次过完，后面几次几乎秒开；
//   2. **轮询挑战是否解除**（读 document.title / body 文本），不再盲等固定时长；
//   3. 不加 `--enable-automation`（那是 Playwright/Puppeteer 干的事，会让
//      navigator.webdriver=true，CF 一眼识别）。headful 下与正常浏览器无异。
//
// ARKHAM_HEADFUL=0 可强制 headless（仅用于对比，headless 大概率过不了）。
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const CHROME = 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe';
const target = process.argv[2] || 'https://arkm.com/explorer/token/janction';
const maxWaitMs = Number(process.argv[3] || 60000);
const port = Number(process.argv[4] || 9377);

const profileDir = join(process.cwd(), '.runtime-cache', 'arkham-chrome-profile');
mkdirSync(profileDir, { recursive: true });

const headful = process.env.ARKHAM_HEADFUL !== '0';
const chrome = spawn(CHROME, [
  ...(headful ? [] : ['--headless=new']),
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

const seen = [];
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
    return;
  }
  if (msg.method === 'Network.requestWillBeSent') {
    const { request } = msg.params;
    if (/arkm/i.test(request.url)) {
      const headers = request.headers || {};
      const interesting = Object.fromEntries(
        Object.entries(headers).filter(([k]) =>
          /api-key|authorization|timestamp|cookie|^x-|referer|origin/i.test(k)),
      );
      seen.push({
        id: msg.params.requestId,
        method: request.method,
        url: request.url,
        type: msg.params.type,
        headers: interesting,
        postData: request.postData ? String(request.postData).slice(0, 500) : '',
      });
    }
  }
  if (msg.method === 'Network.responseReceived') {
    const item = seen.find((row) => row.id === msg.params.requestId);
    if (item) item.status = msg.params.response.status;
  }
  if (msg.method === 'Network.loadingFinished') {
    const item = seen.find((row) => row.id === msg.params.requestId);
    if (!item || item.body) return;
    send(ws, 'Network.getResponseBody', { requestId: msg.params.requestId }).then((res) => {
      const body = res.body || '';
      item.body = body.slice(0, 1500);
    });
  }
};

await send(ws, 'Network.enable');
await send(ws, 'Page.enable');
await send(ws, 'Page.navigate', { url: target });

// 轮询：挑战页 title 是 "Just a moment..."（中文浏览器下是「请稍候…」）。
// 不要拿 title 当唯一判据 —— 只要还没看到 api.arkm.com 的请求，就说明壳子还没起来。
const CHALLENGE_TITLE = /just a moment|请稍候|正在验证|attention required|^$/i;
const CHALLENGE_BODY = /challenge-platform|cf-chl|Verify you are human|验证您是真人/i;
const started = Date.now();
let title = '';
let cleared = false;
let probe = {};
while (Date.now() - started < maxWaitMs) {
  await sleep(2500);
  const res = await send(ws, 'Runtime.evaluate', {
    expression: 'JSON.stringify({t:document.title,b:(document.body&&document.body.innerText||"").slice(0,200),n:(document.body?document.body.innerText.length:0)})',
    returnByValue: true,
  });
  try {
    probe = JSON.parse((res.result && res.result.value) || '{}');
  } catch {
    probe = {};
  }
  title = probe.t || '';
  const hasChallenge = CHALLENGE_TITLE.test(title) || CHALLENGE_BODY.test(probe.b || '');
  const hasApi = seen.some((row) => /api\.arkm\.com/i.test(row.url));
  if (!hasChallenge && hasApi) {
    cleared = true;
    break;
  }
}
// 页面内容起来后再等一会儿，让 holders / entity 那几个懒加载面板发完请求。
await sleep(8000);

const api = seen.filter((row) => /api\.arkm\.com|arkm\.com\/api/i.test(row.url));
writeFileSync(
  join(process.cwd(), '.runtime-cache', '_arkham_probe.json'),
  JSON.stringify({ target, title, cleared, elapsedMs: Date.now() - started, requests: seen }, null, 2),
);

console.log(JSON.stringify({
  target,
  title,
  cleared,
  bodyHead: probe.b,
  elapsedMs: Date.now() - started,
  totalArkmRequests: seen.length,
  apiCalls: api.map((row) => ({
    method: row.method,
    status: row.status || 0,
    type: row.type,
    url: row.url,
    headers: row.headers,
    postData: row.postData,
    bodyHead: (row.body || '').replace(/\s+/g, ' ').slice(0, 300),
  })),
}, null, 2));
ws.close();
chrome.kill();
process.exit(0);
