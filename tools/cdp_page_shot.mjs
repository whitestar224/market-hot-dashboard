// Full-page screenshot of a running page through CDP.
//
//   node tools/cdp_page_shot.mjs <url> <w> <h> <port> <out.png> [waitSelector] [maxHeight] [clickSelector]
//
// `Page.captureScreenshot` with `captureBeyondViewport: true` and **no** clip
// captures the whole document; a clip would need page coordinates and silently
//拍出空白（踩过）。高度封顶避免把 77 条地址簿全拍进一张超长图。
//
// `clickSelector` 在截图前点一下（例如展开自绘下拉）—— 原生 <select> 的弹层是系统
// 画的、根本截不到，所以凡是要验收「展开后的样子」，控件都必须是自绘的。
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const CHROME = 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe';
const target = process.argv[2];
const width = Number(process.argv[3] || 1400);
const height = Number(process.argv[4] || 1200);
const port = Number(process.argv[5] || 9357);
const out = process.argv[6] || 'page.png';
const waitSelector = process.argv[7] || '';
const maxHeight = Number(process.argv[8] || 3200);
const clickSelector = process.argv[9] || '';

if (!target) {
  console.error('usage: node cdp_page_shot.mjs <url> <w> <h> <port> <out.png> [waitSelector] [maxHeight] [clickSelector]');
  process.exit(1);
}

const profile = mkdtempSync(join(tmpdir(), 'cdp-shot-'));
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

function run(wsUrl) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(wsUrl);
    let id = 0;
    const pending = new Map();
    const timer = setTimeout(() => { socket.close(); reject(new Error('shot timeout')); }, 90000);
    const send = (method, params) => new Promise((res, rej) => {
      id += 1;
      pending.set(id, { res, rej });
      socket.send(JSON.stringify({ id, method, params }));
    });
    socket.addEventListener('message', (event) => {
      const msg = JSON.parse(event.data);
      const slot = pending.get(msg.id);
      if (!slot) return;
      pending.delete(msg.id);
      if (msg.error) slot.rej(new Error(JSON.stringify(msg.error)));
      else slot.res(msg.result);
    });
    socket.addEventListener('open', async () => {
      try {
        await send('Page.enable');
        if (waitSelector) {
          const expr = `(async () => {
            for (let i = 0; i < 80; i += 1) {
              if (document.querySelector(${JSON.stringify(waitSelector)})) return true;
              await new Promise((r) => setTimeout(r, 250));
            }
            return false;
          })()`;
          await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
        }
        if (clickSelector) {
          // 刚连上调试口时页面可能还在导航（Runtime.evaluate 会抛
          // "Execution context was destroyed"），所以这里带重试。
          let clicked = null;
          for (let attempt = 0; attempt < 4; attempt += 1) {
            try {
              clicked = await send('Runtime.evaluate', {
                expression: `(() => {
                  const node = document.querySelector(${JSON.stringify(clickSelector)});
                  if (!node) return false;
                  node.click();
                  return true;
                })()`,
                returnByValue: true,
              });
              break;
            } catch (error) {
              if (!/context was destroyed/i.test(error.message) || attempt === 3) throw error;
              await new Promise((r) => setTimeout(r, 700));
            }
          }
          if (!clicked || !clicked.result || clicked.result.value !== true) {
            console.error(`click target not found: ${clickSelector}`);
          }
          // 等过渡动画（caret 旋转 / 面板出现）走完再拍。
          await new Promise((r) => setTimeout(r, 600));
        }
        await new Promise((r) => setTimeout(r, 1500));
        const metrics = await send('Page.getLayoutMetrics');
        const docHeight = Math.min(Math.ceil(metrics.cssContentSize?.height || height), maxHeight);
        await send('Emulation.setDeviceMetricsOverride', {
          width, height: docHeight, deviceScaleFactor: 1, mobile: false,
        });
        await new Promise((r) => setTimeout(r, 700));
        const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
        writeFileSync(out, Buffer.from(shot.data, 'base64'));
        clearTimeout(timer);
        socket.close();
        resolve({ out, docHeight, width });
      } catch (error) {
        clearTimeout(timer);
        socket.close();
        reject(error);
      }
    });
    socket.addEventListener('error', (event) => {
      clearTimeout(timer);
      reject(new Error(`ws error: ${event.message || 'unknown'}`));
    });
  });
}

try {
  const page = await findTarget();
  await new Promise((resolve) => setTimeout(resolve, 1500));
  const result = await run(page.webSocketDebuggerUrl);
  console.log(JSON.stringify(result));
} catch (error) {
  console.error('SHOT FAILED: ' + error.message);
  process.exitCode = 1;
} finally {
  chrome.kill();
}
