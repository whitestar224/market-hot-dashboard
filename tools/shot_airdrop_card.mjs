// 把「ICODrops 空投榜」卡片滚进视野后截图，用于人工验收 UI 是否与其它榜单一致。
//
//   node tools/shot_airdrop_card.mjs [url] [width] [height] [port] [out.png] [sortClicks]
//
// ``sortClicks``：拍照前点几下表头的「融资 / 估值」排序按钮（1=金额↓，2=金额↑，3=回原始顺序）。
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const CHROME = 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe';
const target = process.argv[2] || 'http://127.0.0.1:8765/index.html';
const width = Number(process.argv[3] || 1440);
const height = Number(process.argv[4] || 1100);
const port = Number(process.argv[5] || 9421);
const out = process.argv[6] || '.runtime-cache/shot_airdrop_card.png';
const sortClicks = Math.max(0, Number(process.argv[7] || 0));

const profile = mkdtempSync(join(tmpdir(), 'cdp-card-'));
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
      /* booting */
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
      // 页面偶尔在 hydration 后自己再导航一次，会把执行上下文打断
      // （"Execution context was destroyed"）。这种是瞬时的，重试即可。
      const safeEval = async (expression, attempts = 4) => {
        for (let i = 0; i < attempts; i += 1) {
          try {
            return await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
          } catch (error) {
            if (!String(error.message || '').includes('Execution context was destroyed') || i === attempts - 1) {
              throw error;
            }
            await new Promise((r) => setTimeout(r, 800));
          }
        }
        return null;
      };
      try {
        await send('Page.enable');
        // 等空投卡出现且主榜单也到位
        await safeEval(`(async () => {
            for (let i = 0; i < 160; i += 1) {
              const cards = Array.from(document.querySelectorAll('#leaderboards .board-card'));
              const has = cards.some((c) => (c.querySelector('h3')?.textContent || '').includes('ICODrops'));
              if (has && cards.length > 3) return true;
              await new Promise((r) => setTimeout(r, 250));
            }
            return false;
          })()`);
        // 拍照前先按需点几次排序按钮，用于验收按钮激活态与排好序的列表
        for (let i = 0; i < sortClicks; i += 1) {
          await safeEval(`(() => {
              const cards = Array.from(document.querySelectorAll('#leaderboards .board-card'));
              const card = cards.find((c) => (c.querySelector('h3')?.textContent || '').includes('ICODrops'));
              card?.querySelector('.airdrop-sort')?.click();
              return true;
            })()`);
          await new Promise((r) => setTimeout(r, 450));
        }
        // 把卡片滚到视野中上部，让它和左右两张卡一起入镜，方便对比风格
        await safeEval(`(() => {
            const cards = Array.from(document.querySelectorAll('#leaderboards .board-card'));
            const card = cards.find((c) => (c.querySelector('h3')?.textContent || '').includes('ICODrops'));
            if (!card) return false;
            card.scrollIntoView({ block: 'center' });
            return true;
          })()`);
        await new Promise((r) => setTimeout(r, 1200));
        const shot = await send('Page.captureScreenshot', { format: 'png' });
        writeFileSync(out, Buffer.from(shot.data, 'base64'));
        clearTimeout(timer);
        socket.close();
        resolve({ out, width, height });
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
