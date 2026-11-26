// 验收「空投榜」融资额排序按钮：三态循环（原始顺序 → 金额↓ → 金额↑ → 原始顺序）。
//
//   node tools/probe_airdrop_sort.mjs [url] [width] [height] [port]
//
// 判据：
// 1. 按钮在表头第 4 格，初始为 none（箭头 ↕、无 is-active）；
// 2. 点一次 → desc：金额非递增，「未披露」全在尾部；
// 3. 点两次 → asc：金额非递减，「未披露」仍在尾部；
// 4. 点三次 → 回到 none，且首行与初始一致（顺序必须原样还回来）；
// 5. 每次切换后 localStorage 里的偏好跟着变。
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const CHROME = 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe';
const target = process.argv[2] || 'http://127.0.0.1:8765/index.html';
const width = Number(process.argv[3] || 1440);
const height = Number(process.argv[4] || 1200);
const port = Number(process.argv[5] || 9413);

const profile = mkdtempSync(join(tmpdir(), 'cdp-sort-'));
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
      /* still booting */
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error('no debuggable page target');
}

// 取快照：按钮状态 + 前 14 行（名称 / 融资额 / 估值）
const SNAP = `(() => {
  const cards = Array.from(document.querySelectorAll('#leaderboards .board-card'));
  const card = cards.find((c) => (c.querySelector('h3')?.textContent || '').includes('ICODrops'));
  if (!card) return { error: 'airdrop card not rendered' };
  const btn = card.querySelector('.airdrop-sort');
  const scroll = card.querySelector('.airdrop-scroll');
  const rows = scroll ? Array.from(scroll.querySelectorAll('.airdrop-rank-row')) : [];
  return {
    buttonText: (btn?.textContent || '').trim(),
    buttonTitle: btn?.getAttribute('title') || '',
    sort: btn?.dataset.airdropSort || '',
    active: Boolean(btn?.classList.contains('is-active')),
    stored: (() => { try { return localStorage.getItem('xingyunshe:market-hot:airdrop-sort:v1') || ''; } catch { return ''; } })(),
    totalRows: rows.length,
    rows: rows.slice(0, 14).map((row, i) => ({
      rank: row.querySelector('.rank-badge')?.textContent?.trim() || '',
      name: row.querySelector('strong')?.textContent?.trim() || '',
      money: row.querySelector('.airdrop-money-cell b')?.textContent?.trim() || '',
      value: row.querySelector('.airdrop-money-cell span')?.textContent?.trim() || '',
      muted: Boolean(row.querySelector('.airdrop-money-cell b.airdrop-money-empty')),
      rowIndex: i,
    })),
  };
})()`;

const CLICK = `(() => {
  const cards = Array.from(document.querySelectorAll('#leaderboards .board-card'));
  const card = cards.find((c) => (c.querySelector('h3')?.textContent || '').includes('ICODrops'));
  const btn = card?.querySelector('.airdrop-sort');
  if (!btn) return 'no-button';
  btn.click();
  return 'clicked';
})()`;

/** 与 app.js 的 airdropAmountValue 同口径：`$61.8 M` → 61800000；空 → null。 */
const TO_NUM = `(text) => {
  const m = String(text || '').match(/([\\d][\\d.,]*)\\s*([KMB])?/i);
  if (!m) return null;
  const n = Number.parseFloat(m[1].replace(/,/g, ''));
  if (!Number.isFinite(n)) return null;
  const u = String(m[2] || '').toUpperCase();
  return n * (u === 'B' ? 1e9 : u === 'M' ? 1e6 : u === 'K' ? 1e3 : 1);
}`;

function run(wsUrl) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(wsUrl);
    let id = 0;
    const pending = new Map();
    const timer = setTimeout(() => { socket.close(); reject(new Error('probe timeout')); }, 120000);
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
      const evaluate = async (expression, attempts = 4) => {
        // 页面偶尔在 hydration 后自己再导航一次，会把执行上下文打断
        // （"Execution context was destroyed"）。这是瞬时的，重试即可。
        for (let i = 0; i < attempts; i += 1) {
          try {
            const out = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
            if (out.exceptionDetails) throw new Error(out.exceptionDetails.text || 'eval failed');
            return out.result?.value;
          } catch (error) {
            const transient = String(error.message || '').includes('Execution context was destroyed');
            if (!transient || i === attempts - 1) throw error;
            await new Promise((r) => setTimeout(r, 800));
          }
        }
        return undefined;
      };
      try {
        await send('Page.enable');
        await evaluate(`(async () => {
          for (let i = 0; i < 160; i += 1) {
            const cards = Array.from(document.querySelectorAll('#leaderboards .board-card'));
            const hasAirdrop = cards.some((c) => (c.querySelector('h3')?.textContent || '').includes('ICODrops'));
            if (hasAirdrop && cards.length > 3 && document.querySelector('.airdrop-sort')) return true;
            await new Promise((r) => setTimeout(r, 250));
          }
          return false;
        })()`);
        // 等首屏数据落地（统计卡不再是「…」）
        await evaluate(`(async () => {
          for (let i = 0; i < 40; i += 1) {
            const value = document.querySelector('[data-airdrop-card] strong')?.textContent || '';
            if (value && value !== '…') return true;
            await new Promise((r) => setTimeout(r, 250));
          }
          return false;
        })()`);
        await new Promise((r) => setTimeout(r, 400));

        const states = [];
        const click = async (label) => {
          await evaluate(CLICK);
          await new Promise((r) => setTimeout(r, 500));
          const snap = await evaluate(SNAP);
          states.push({ label, ...snap });
        };

        states.push({ label: 'initial', ...(await evaluate(SNAP)) });
        await click('afterClick1');
        await click('afterClick2');
        await click('afterClick3');

        // 单调性判定（只看钱多的行；未披露必须全部在尾部）
        const checks = states.map((state) => {
          const toNum = eval(TO_NUM);
          const vals = state.rows.map((r) => ({ name: r.name, n: toNum(r.money), muted: r.muted }));
          const numbers = vals.filter((v) => v.n !== null);
          const tailMuted = (() => {
            const firstMuted = vals.findIndex((v) => v.n === null);
            if (firstMuted < 0) return true;
            return vals.slice(firstMuted).every((v) => v.n === null);
          })();
          const ascending = numbers.every((v, i) => i === 0 || numbers[i - 1].n <= v.n);
          const descending = numbers.every((v, i) => i === 0 || numbers[i - 1].n >= v.n);
          return {
            sort: state.sort,
            active: state.active,
            arrow: state.buttonText,
            stored: state.stored,
            firstRow: state.rows[0]?.name || '',
            firstMoney: state.rows[0]?.money || '',
            lastShown: state.rows[state.rows.length - 1]?.name || '',
            numbersInView: numbers.length,
            ascending,
            descending,
            mutedAtTail: tailMuted,
          };
        });

        clearTimeout(timer);
        socket.close();
        resolve({ states: checks, sample: states[1]?.rows || [] });
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
  console.log(JSON.stringify(result, null, 2));
} catch (error) {
  console.error('PROBE FAILED: ' + error.message);
  process.exitCode = 1;
} finally {
  chrome.kill();
}
