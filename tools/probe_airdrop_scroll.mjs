// 验收「空投榜」榜单卡：位置（在 RootData 项目热门榜之后）、默认露出 10 行、可滚动。
//
//   node tools/probe_airdrop_scroll.mjs [url] [width] [height] [port]
//
// 输出 JSON：卡片序号 / 是否紧跟 RootData 榜 / 表头列 / 行数 / clientHeight /
// scrollHeight / 是否可滚动 / 行高 / 第 10 行 / 滚到底的末行 / 顶部统计卡文案。
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const CHROME = 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe';
const target = process.argv[2] || 'http://127.0.0.1:8765/index.html';
const width = Number(process.argv[3] || 1440);
const height = Number(process.argv[4] || 1200);
const port = Number(process.argv[5] || 9411);

const profile = mkdtempSync(join(tmpdir(), 'cdp-probe-'));
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

const PROBE = `(() => {
  const cards = Array.from(document.querySelectorAll('#leaderboards .board-card'));
  const titles = cards.map((c) => c.querySelector('h3')?.textContent?.trim() || '');
  const idx = titles.findIndex((t) => t.includes('ICODrops'));
  const rootIdx = titles.findIndex((t) => t.includes('RootData 项目热门榜'));
  const card = idx >= 0 ? cards[idx] : null;
  if (!card) return { error: 'airdrop card not rendered', titles };
  const scroll = card.querySelector('.airdrop-scroll');
  const rows = scroll ? Array.from(scroll.querySelectorAll('.airdrop-rank-row')) : [];
  const style = scroll ? getComputedStyle(scroll) : null;
  return {
    cardIndex: idx,
    rootdataIndex: rootIdx,
    rightAfterRootdata: rootIdx >= 0 && idx === rootIdx + 1,
    titles,
    cardGroup: card.querySelector('.board-head-copy p')?.textContent?.trim() || '',
    cardTitle: card.querySelector('h3')?.textContent?.trim() || '',
    cardSubtitle: card.querySelector('.board-head-copy span')?.textContent?.trim() || '',
    headCols: Array.from(card.querySelectorAll('.airdrop-table-head span')).map((s) => s.textContent.trim()),
    rows: rows.length,
    clientHeight: scroll?.clientHeight ?? 0,
    scrollHeight: scroll?.scrollHeight ?? 0,
    overflowY: style?.overflowY || '',
    maxHeight: style?.maxHeight || '',
    canScroll: scroll ? scroll.scrollHeight > scroll.clientHeight + 2 : false,
    rowHeight: rows.length ? Math.round(rows[0].getBoundingClientRect().height) : 0,
    firstRow: rows[0]?.querySelector('strong')?.textContent || '',
    tenthRow: rows[9]?.querySelector('strong')?.textContent || '',
    lastRow: rows[rows.length - 1]?.querySelector('strong')?.textContent || '',
    // 图标：统计真实加载成功的 <img>，避免「看起来有图标其实全是字母占位」。
    iconsTotal: rows.filter((r) => r.querySelector('.asset-icon img')).length,
    // 首屏这 10 行是用户真正会看的，它们的图标必须加载成功（其余是懒加载，滚动才触发）。
    iconsLoadedTop10: rows.slice(0, 10).filter((r) => {
      const img = r.querySelector('.asset-icon img');
      return img && img.complete && img.naturalWidth > 0;
    }).length,
    iconsLoaded: rows.filter((r) => {
      const img = r.querySelector('.asset-icon img');
      return img && img.complete && img.naturalWidth > 0;
    }).length,
    iconSamples: rows.slice(0, 3).map((r) => {
      const img = r.querySelector('.asset-icon img');
      return img ? { src: (img.currentSrc || img.src || '').slice(0, 60), ok: img.complete && img.naturalWidth > 0 } : null;
    }),
    cardValue: document.querySelector('[data-airdrop-card] strong')?.textContent || '',
    cardMeta: document.querySelector('[data-airdrop-card] span')?.textContent || '',
  };
})()`;

const SCROLL_END = `(() => {
  const cards = Array.from(document.querySelectorAll('#leaderboards .board-card'));
  const card = cards.find((c) => (c.querySelector('h3')?.textContent || '').includes('ICODrops'));
  const scroll = card?.querySelector('.airdrop-scroll');
  if (!scroll) return null;
  scroll.scrollTop = scroll.scrollHeight;
  const box = scroll.getBoundingClientRect();
  const rows = Array.from(scroll.querySelectorAll('.airdrop-rank-row'));
  const visible = rows.filter((row) => {
    const r = row.getBoundingClientRect();
    return r.top >= box.top - 1 && r.bottom <= box.bottom + 1;
  });
  return {
    scrollTop: Math.round(scroll.scrollTop),
    visibleCount: visible.length,
    lastVisible: visible.length ? visible[visible.length - 1].querySelector('strong')?.textContent : '',
  };
})()`;

function run(wsUrl) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(wsUrl);
    let id = 0;
    const pending = new Map();
    const timer = setTimeout(() => { socket.close(); reject(new Error('probe timeout')); }, 90000);
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
        await send('Runtime.evaluate', {
          expression: `(async () => {
            for (let i = 0; i < 160; i += 1) {
              const cards = Array.from(document.querySelectorAll('#leaderboards .board-card'));
              const hasAirdrop = cards.some((c) => (c.querySelector('h3')?.textContent || '').includes('ICODrops'));
              // 等到主榜单也回来了再判位置，否则 market-hot 还在加载时只有空投榜一张卡。
              if (hasAirdrop && cards.length > 3) return true;
              await new Promise((r) => setTimeout(r, 250));
            }
            return false;
          })()`,
          awaitPromise: true,
          returnByValue: true,
        });
        await send('Runtime.evaluate', {
          expression: `(async () => {
            for (let i = 0; i < 40; i += 1) {
              const value = document.querySelector('[data-airdrop-card] strong')?.textContent || '';
              if (value && value !== '…') return true;
              await new Promise((r) => setTimeout(r, 250));
            }
            return false;
          })()`,
          awaitPromise: true,
          returnByValue: true,
        });
        await new Promise((r) => setTimeout(r, 400));
        const top = await send('Runtime.evaluate', { expression: PROBE, returnByValue: true });
        const scrolled = await send('Runtime.evaluate', { expression: SCROLL_END, returnByValue: true });
        clearTimeout(timer);
        socket.close();
        resolve({ initial: top.result?.value, afterScroll: scrolled.result?.value });
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
