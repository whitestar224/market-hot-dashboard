// 读 TradingView 自选表（需要登录态）。
//
// TradingView 的自选表接口 `GET /api/v1/symbols_list/custom/<id>/` 未登录直接回
// `403 {"detail":"Login required."}` —— 不是签名、不是 UA，就是缺 cookie。
//
// 本机 Chrome 常年开着、占着默认配置，既不能二次启动也不能挂 CDP。而 Chrome 对
// `Default/Network/Cookies` 是**完全独占**的（连只读打开都拒），所以要先让用户退出
// Chrome，再由 `tools/tv_profile_copy.py` 把登录态最小子集复制到独立目录
// （`Local State` 里的 DPAPI 密钥对同一个 Windows 用户可用，副本仍能解密 cookie），
// 最后用这个副本起无头实例。原浏览器不受影响。
//
//   python tools/tv_profile_copy.py .runtime-cache/tv-chrome-profile --wait 240
//   node tools/tv_watchlist_probe.mjs [watchlistId] [out.json]
//
// 输出里 `lists` 是接口原文，`flat` 是「分组 → [ticker]」的扁平结果。
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';

const CHROME = 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe';
const CHROME_FALLBACK = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const listId = process.argv[2] || '350560242';
const outPath = process.argv[3] || '.runtime-cache/_tv_watchlist.json';

const SRC = join(homedir(), 'AppData', 'Local', 'Google', 'Chrome', 'User Data');
const PROFILE = join(process.cwd(), '.runtime-cache', 'tv-chrome-profile');
const PORT = 9361;

/** 登录态副本不在就让 Python 去等 Chrome 退出后复制（Node 这边拿不到被锁的库）。 */
function ensureProfile() {
  const cookieFile = join(PROFILE, 'Default', 'Network', 'Cookies');
  if (existsSync(cookieFile)) return 'ready';
  const python = 'C:\\Python314\\python.exe';
  const result = spawnSync(python, ['tools/tv_profile_copy.py', PROFILE, '--wait', '240'], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const out = (result.stdout || '').trim();
  process.stderr.write(out + '\n');
  if (!existsSync(cookieFile)) return `missing (${result.status}): ${out.slice(0, 300)}`;
  return 'copied';
}

const PROBE = `(() => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  return (async () => {
    await wait(2500);
    const base = '/api/v1/symbols_list';
    const tries = [
      base + '/all/',
      base + '/custom/${listId}/',
      base + '/',
    ];
    const out = { href: location.href, loggedIn: false, calls: {}, errors: [] };
    out.loggedIn = !document.documentElement.className.includes('is-not-authenticated');
    for (const path of tries) {
      try {
        const res = await fetch(path, { credentials: 'include', headers: { Accept: 'application/json' } });
        const text = await res.text();
        out.calls[path] = { status: res.status, body: text.slice(0, 200000) };
      } catch (e) {
        out.errors.push(path + ': ' + String(e));
      }
    }
    return JSON.stringify(out);
  })();
})()`;

async function findTarget() {
  for (let i = 0; i < 80; i += 1) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const list = await res.json();
      const page = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
      if (page) return page;
    } catch {
      /* chrome still booting */
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error('no debuggable page target');
}

function evaluate(wsUrl) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(wsUrl);
    const timer = setTimeout(() => { socket.close(); reject(new Error('probe timeout')); }, 90000);
    socket.addEventListener('error', (e) => { clearTimeout(timer); reject(new Error(`ws error: ${e.message || ''}`)); });
    socket.addEventListener('open', () => {
      socket.send(JSON.stringify({
        id: 1,
        method: 'Runtime.evaluate',
        params: { expression: PROBE, awaitPromise: true, returnByValue: true, timeout: 70000 },
      }));
    });
    socket.addEventListener('message', (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id !== 1) return;
      clearTimeout(timer);
      const value = msg.result?.result?.value;
      socket.close();
      if (value === undefined) {
        reject(new Error(JSON.stringify(msg).slice(0, 800)));
        return;
      }
      resolve(JSON.parse(value));
    });
  });
}

/** 从接口原文里抽出「分组 → ticker 列表」。两种形状都兼容。 */
function flatten(payload) {
  const out = [];
  const push = (name, symbols) => {
    const ticks = (symbols || [])
      .map((s) => (typeof s === 'string' ? s : s?.symbol || ''))
      .filter(Boolean);
    if (ticks.length) out.push({ name, symbols: ticks });
  };
  if (!payload) return out;
  if (Array.isArray(payload)) {
    for (const item of payload) {
      push(item?.name || item?.id || '未命名', item?.symbols || []);
    }
    return out;
  }
  for (const [key, value] of Object.entries(payload)) {
    if (Array.isArray(value)) push(key, value);
  }
  for (const item of payload.lists || payload.custom || []) {
    push(item?.name || String(item?.id || '未命名'), item?.symbols || []);
  }
  return out;
}

const chromePath = existsSync(CHROME) ? CHROME : CHROME_FALLBACK;
if (!existsSync(chromePath)) {
  console.error(`找不到 chrome：${CHROME}`);
  process.exit(1);
}

console.log('profile:', ensureProfile());

const chrome = spawn(chromePath, [
  '--headless=new',
  '--disable-gpu',
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-extensions',
  '--disable-background-networking',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${PROFILE}`,
  '--window-size=1400,1000',
  `https://cn.tradingview.com/watchlists/${listId}/`,
], { stdio: 'ignore', detached: false });

try {
  const page = await findTarget();
  await new Promise((r) => setTimeout(r, 2500));
  const data = await evaluate(page.webSocketDebuggerUrl);
  const result = { listId, loggedIn: data.loggedIn, href: data.href, errors: data.errors, calls: {} };
  let best = null;
  for (const [path, call] of Object.entries(data.calls || {})) {
    let parsed = null;
    try {
      parsed = JSON.parse(call.body);
    } catch {
      /* 非 JSON（多半是登录页 HTML） */
    }
    result.calls[path] = { status: call.status, json: parsed ? 'parsed' : 'not-json', preview: String(call.body).slice(0, 160) };
    if (parsed) {
      const flat = flatten(parsed);
      if (flat.length && (!best || flat.length > best.flat.length)) best = { path, raw: parsed, flat };
    }
  }
  if (best) {
    result.source = best.path;
    result.lists = best.raw;
    result.flat = best.flat;
  }
  mkdirSync(dirname(outPath), { recursive: true });
  writeFileSync(outPath, JSON.stringify(result, null, 2), 'utf8');
  const summary = {
    loggedIn: result.loggedIn,
    source: result.source || null,
    groups: (result.flat || []).length,
    total: (result.flat || []).reduce((n, g) => n + g.symbols.length, 0),
    names: (result.flat || []).map((g) => `${g.name}(${g.symbols.length})`),
    calls: Object.fromEntries(Object.entries(result.calls).map(([k, v]) => [k, v.status])),
    out: outPath,
  };
  console.log(JSON.stringify(summary, null, 2));
  if (!result.loggedIn) process.exitCode = 2;
} catch (error) {
  console.error('PROBE FAILED: ' + error.message);
  process.exitCode = 1;
} finally {
  chrome.kill();
}
