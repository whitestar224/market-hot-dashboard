// ==UserScript==
// @name         GMGN AI 叙事 → GIGA X 叙事
// @namespace    https://github.com/whitestar224/market-hot-dashboard
// @version      1.8.0
// @description  把 GMGN 网页端代币 AI 叙事悬浮窗内容替换为 GIGA X 对应代币的叙事分析
// @author       market-hot-dashboard
// @match        *://gmgn.ai/*
// @match        *://www.gmgn.ai/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_registerMenuCommand
// @grant        unsafeWindow
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// @noframes
// ==/UserScript==

(function () {
  'use strict';

  const W = (typeof unsafeWindow !== 'undefined') ? unsafeWindow : window;

  // ===== 配置 =====
  // 桥接已集成进 market-hot-dashboard 主服务（server.py 端口 8765），随主服务一起启动。
  // 桥接根地址：**只填到端口**，路由前缀 /api/gigax 由脚本补。
  const BRIDGE_RAW = GM_getValue('bridge', 'http://127.0.0.1:8765');
  const BRIDGE = BRIDGE_RAW.replace(/\/+$/, '').replace(/\/api\/gigax$/, '');
  const API = BRIDGE + '/api/gigax';
  // 版本号写死一份：诊断面板会显示它，用来确认「你装的到底是不是这一版」
  //（反复修同一个问题、结果用户还在跑旧脚本，已经踩过一次）。
  const SCRIPT_VERSION = '1.8.0';

  const POLL_MS = 1500;            // 轮询保底（快路径是 DOM 观察 + hover 预热；放宽以减轻主线程压力）
  const CACHE_TTL = 30 * 60 * 1000;// 同一 CA 叙事缓存 30 分钟（和服务端一致）
  const NO_DATA_TTL = 2 * 60 * 1000;// 「GIGA X 没有这个币」也缓存 2 分钟，别反复打桥接
  const NET_AI_TTL = 30000;        // 叙事类请求的 CA 有效期（30s：再长就会跨币串味——旧请求的币顶掉当前币）
  const NET_AI_FRESH = 3000;       // 「浮层就是按这次请求渲染的」窗口（3 秒）——主判据，别放大
  // 曾经还有一个 NET_ANY_TTL=8s 的「最近任意请求」兜底，已删除：列表/滚动会不断发带 CA 的
  // 普通请求，那个兜底等于「随便抓一个币」，是代币与叙事对不上的主要来源之一（2026-11-18）。

  // 悬浮窗正文的特征锚点（GMGN 固定的免责声明文案）
  const ANCHORS = [
    '人工智能生成内容',
    '免责声明',
    '投资需自行研究',
    'AI 生成',
    'AI生成',
    'AI 分析',
    'AI 总结',
    'AI 叙事',
  ];
  const CONTAINER_HINTS = ['tooltip', 'popover', 'popper', 'tippy', 'floating', 'ai-narrative', 'ai-analysis', 'bubble', 'card-pop'];

  let enabled = GM_getValue('enabled', true);
  // 机会标记：off=关 | 其余任何值（on / 老的 strict、loose）=开
  // 2026-11-19 用户定案：「新和第一要的，弱词单个也行」—— 强词弱词一律**命中一个就算**，
  // 于是原来的「严格/宽松」两档彻底等价，菜单里那项已经删掉。
  // **默认开启**（2026-11-19 定案）。中间那一版（1.7.6）曾按「干脆别做标记了」默认关，
  // 现在按「新和第一要的」改回开；菜单里仍可一键关掉。
  let hotMode = GM_getValue('hotMark', 'on');
  // 1.7.6 曾经按「干脆别做标记了」强制写过 off；用户随后改口「新和第一要的」→
  // 这里再一次性改回 on（只覆盖那一版强制写入的值，之后菜单开关照常生效）。
  if (GM_getValue('hotMarkOnMigrated', '') !== 'v1') {
    hotMode = 'on';
    GM_setValue('hotMark', 'on');
    GM_setValue('hotMarkOnMigrated', 'v1');
  }
  // 是否替换 GMGN 浮层里的 AI 叙事正文。**标色完全不依赖它**：
  // 标色只用到「CA → 叙事 → 关键词」这条链路，跟浮层是谁无关。
  // 用户 2026-11-19：「为什么要做浮层呢，卡片的 CSS 本身就可以改色」——
  // 所以给它一个一键关掉的开关；关掉后本脚本绝不改动页面上任何正文。
  let replacePopup = GM_getValue('replacePopup', true);
  const cache = new Map(); // chain:ca -> {text, ts}

  function log(...a) { console.log('[GMGN→GIGAX]', ...a); }
  function warn(...a) { console.warn('[GMGN→GIGAX]', ...a); }

  // ===== 菜单 =====
  GM_registerMenuCommand(enabled ? '关闭替换' : '开启替换', () => {
    enabled = !enabled;
    GM_setValue('enabled', enabled);
    log('替换已' + (enabled ? '开启' : '关闭'));
    if (!enabled) restoreAll();
  });
  GM_registerMenuCommand('设置桥接地址', () => {
    const v = prompt('GIGA X 桥接根地址（默认 http://127.0.0.1:8765，只填到端口即可）:', BRIDGE);
    if (v) { GM_setValue('bridge', v); location.reload(); }
  });
  GM_registerMenuCommand('打印悬浮窗 DOM（调试）', dumpFloating);
  GM_registerMenuCommand(
    replacePopup ? '关闭「浮层正文替换」（只保留卡片标色）' : '开启「浮层正文替换」',
    () => {
      replacePopup = !replacePopup;
      GM_setValue('replacePopup', replacePopup);
      if (!replacePopup) restoreAll();      // 立刻把改过的正文还原
      log('浮层正文替换：' + (replacePopup ? '开' : '关（只标色）'));
    });

  GM_registerMenuCommand(hotMode === 'off' ? '开启机会标记（蓝底卡片）' : '关闭机会标记（蓝底卡片）', () => {
    hotMode = hotMode === 'off' ? 'on' : 'off';
    GM_setValue('hotMark', hotMode);
    log('机会标记已' + (hotMode === 'off' ? '关闭' : '开启'));
    paintHotCards(true);
  });

  // ===== 地址解析 =====
  const EVM_RE = /0x[a-fA-F0-9]{40}/;
  const EVM_RE_G = /0x[a-fA-F0-9]{40}/g;
  const CHAINS = ['sol', 'eth', 'bsc', 'base', 'robinhood', 'blast', 'tron', 'arbitrum', 'polygon'];

  const isEvm = ca => /^0x[a-fA-F0-9]{40}$/.test(ca || '');
  // base58 **大小写敏感**：Solana 地址一旦 lower() 就变成另一个地址，
  // 桥接会查到一个不存在的币，静默返回空 —— 这是「Solana 的币全都没有叙事」的元凶。
  const isSol = ca => /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(ca || '');
  const normCa = ca => (isEvm(ca) ? ca.toLowerCase() : ca);

  // ===== 最硬的取址来源：GMGN 自己写在 DOM 上的 data 属性 =====
  // 2026-11-19 用 chrome-devtools-mcp 打开真实战壕页实测（未登录，30 张卡）：
  //   <div data-trenches-token-address="0x44b9…7777"
  //        data-trenches-token-chain="bsc"
  //        data-trenches-token-key="0x44b9…7777-bsc">     ← 一卡一个，30 个 holder / 30 个不同 CA
  //   <div data-row-key="bsc:0xb825…7777">               ← 左列表一行一个
  //   <div data-recycler-key="bsc:0xb825…7777">          ← 虚拟列表的回收键
  //
  // **为什么这条是决定性的**：战壕卡片里 `a[href*="/token/"]` 的个数实测是 0/1/2 个，
  // 而且币名（"NVIDIASI"）是**纯文本节点**、不是链接。老版本靠代币链接找币，
  // 在战壕区要么一无所获（退到网络嗅探 → 全变成同一个币），要么爬到列容器里
  // 取到**下面那张卡**的链接（IDRENT 取到慈善CZ）。data 属性没有这些问题：
  // 它就是那张卡自己的、唯一的、机器可读的地址。
  //
  // 另一条实测证据：同一屏两张卡都叫 "PENGB Penguin Solutions"、都叫 "NVIDIASI"，
  // CA 却分别是 0x0c58…ffff / 0xd962…ffff 与 0x44b9…7777 / 0x407a…ffff。
  // 所以「靠 symbol 认币」从根上不可靠 —— 能拿到地址就绝不靠名字。
  const CA_DATA_ATTRS = [
    'data-trenches-token-address',   // 战壕卡（最权威）
    'data-token-address',
    'data-row-key',                  // 左列表行："chain:CA"
    'data-recycler-key',             // 回收容器："chain:CA"
  ];
  const CA_DATA_SEL = CA_DATA_ATTRS.map(a => `[${a}]`).join(',');
  const CA_DATA_MAX_H = 400;         // 超过这个高度的是容器，不是单张卡片

  // 从一段 URL 里解析出 {chain, ca}
  // GMGN 的地址页形状是 /<chain>/token/<CA>（链名在前）。老版本只认 /token/<chain>/<CA>，
  // 于是 Solana 页面上的地址一个都解析不出来 —— 「有些币没替换」的另一个主因。
  function parseUrlToken(href) {
    if (!href) return null;
    const s = String(href);
    const withChain = (chain, raw) => {
      const ca = normCa(raw);
      if (!isEvm(ca) && !isSol(ca)) return null;
      const c = CHAINS.includes(String(chain || '').toLowerCase()) ? String(chain).toLowerCase() : null;
      return { chain: c, ca };
    };
    let m = s.match(/\/([a-z]{2,10})\/token\/([0-9a-zA-Z]{32,64})/i);
    if (m) { const r = withChain(m[1], m[2]); if (r) return r; }
    m = s.match(/\/token\/([a-z]{2,10})\/([0-9a-zA-Z]{32,64})/i);
    if (m) { const r = withChain(m[1], m[2]); if (r) return r; }
    m = s.match(/[?&](?:address|token|tokenAddress|ca|contract)=([0-9a-zA-Z]{32,64})/i);
    if (m) { const r = withChain(null, m[1]); if (r) return r; }
    // 只有 CA 没有链名：EVM 直接认，base58 归 sol
    m = s.match(/\/(?:token|address)\/([1-9A-HJ-NP-Za-km-z]{32,44})(?:[?#/]|$)/);
    if (m && isSol(m[1])) return { chain: 'sol', ca: m[1] };
    m = s.match(EVM_RE);
    if (m) return { chain: null, ca: m[0].toLowerCase() };
    return null;
  }

  // ===== 网络嗅探：GMGN 自己请求哪个币的叙事，就用哪个 CA =====
  // 这是最可靠的来源 —— 悬浮窗是按那次请求渲染出来的，DOM 里反而常常没有地址，
  // 老版本在这里兜底用了 location.href，于是「页面主体的币」会顶掉「卡片上的币」。
  const netEvents = [];
  const AI_PATH_RE = /narrative|analysis|ai_|ai-|social|story|insight/;
  // 每来一次「叙事类」请求就 +1。悬浮窗换币时 GMGN 一定会重新请求叙事，
  // 这比「比对 CA」便宜得多，用来作废「已知无数据」的标记（否则换到新币会一直保持原文）。
  let netVersion = 0;
  function noteUrl(url) {
    try {
      const parsed = parseUrlToken(url);
      if (!parsed) return;
      const path = String(url).toLowerCase();
      const isAi = AI_PATH_RE.test(path);
      netEvents.push({ ...parsed, path, isAi, ts: Date.now() });
      if (isAi) {
        netVersion++;
        // 事件驱动：GMGN 发叙事类请求 ≈ 悬浮窗马上要出现。此刻才值得扫一次，
        // 平时完全不扫 —— 这是把「定时轮询压主线程」降到最低的关键。
        scheduleScan(40);
      }
      if (netEvents.length > 60) netEvents.shift();
    } catch (e) { /* ignore */ }
  }
  function installNetSniffer() {
    try {
      const f = W.fetch;
      if (typeof f === 'function' && !f.__gigaxPatched) {
        const patched = function (input, init) {
          try {
            noteUrl(typeof input === 'string' ? input : (input && input.url));
            // 地址也可能在 POST body 里（GraphQL / JSON）
            const b = init && init.body;
            if (typeof b === 'string') noteUrl(b);
          } catch (e) {}
          return f.apply(this, arguments);
        };
        patched.__gigaxPatched = true;
        W.fetch = patched;
        log('fetch 嗅探已挂');
      }
    } catch (e) { warn('fetch 嗅探失败', e); }
    try {
      const X = W.XMLHttpRequest;
      if (X && X.prototype && !X.prototype.__gigaxPatched) {
        const open = X.prototype.open;
        X.prototype.open = function (method, url) {
          try { noteUrl(url); this.__gigaxUrl = String(url); } catch (e) {}
          return open.apply(this, arguments);
        };
        X.prototype.__gigaxPatched = true;
        const send = X.prototype.send;
        X.prototype.send = function (data) {
          try {
            const low = String(this.__gigaxUrl || '');
            if (typeof data === 'string' && /narrative|analysis|ai_|ai-|social|story|insight/.test(low)) {
              noteUrl(data);
            }
          } catch (e) {}
          return send.apply(this, arguments);
        };
        log('XHR 嗅探已挂');
      }
    } catch (e) { warn('XHR 嗅探失败', e); }
  }
  installNetSniffer();

  // ===== DOM 侧兜底 =====
  const lastPointer = { x: 0, y: 0 };
  document.addEventListener('mousemove', e => {
    lastPointer.x = e.clientX;
    lastPointer.y = e.clientY;
  }, { passive: true, capture: true });

  function collectUrlsIn(scope) {
    const found = [];
    const push = (v) => {
      const p = parseUrlToken(v);
      if (p) found.push(p);
    };
    try {
      scope.querySelectorAll('a[href]').forEach(a => push(a.getAttribute('href')));
      // GMGN 的 data 属性（`data-trenches-token-address` / `data-row-key="bsc:0x…"`）
      // 是带链名的权威地址，比正文里捞 0x 精确 —— 先过它们，且用 parseCaKey 保住链名。
      for (const el of [scope, ...scope.querySelectorAll(CA_DATA_SEL)]) {
        if (!el || !el.attributes) continue;
        for (const attr of CA_DATA_ATTRS) {
          const raw = el.getAttribute && el.getAttribute(attr);
          if (!raw) continue;
          const p = parseCaKey(raw);
          if (p) found.push({ chain: p.chain, ca: normCa(p.ca) });
        }
      }
      for (const el of [scope, ...scope.querySelectorAll('[data-address],[data-ca],[title]')]) {
        if (!el || !el.attributes) continue;
        for (const at of el.attributes) push(at.value || '');
      }
      const text = scope.innerText || '';
      (text.match(EVM_RE_G) || []).forEach(ca => found.push({ chain: null, ca: ca.toLowerCase() }));
    } catch (e) { /* ignore */ }
    return found;
  }

  function uniqueCas(list) {
    const seen = new Set();
    const out = [];
    for (const it of list) {
      if (seen.has(it.ca)) continue;
      seen.add(it.ca);
      out.push(it);
    }
    return out;
  }

  // 只收集「指向代币详情页」的链接（零 layout：不读 innerText / 不算样式）。
  // mouseover 是高频事件，collectUrlsIn 里那句 scope.innerText 会强制 reflow，
  // 拿它做 hover 反查会把交易页卡死。
  function tokenLinksIn(el) {
    const out = [];
    try {
      el.querySelectorAll('a[href*="/token/"]').forEach(a => {
        const q = parseUrlToken(a.getAttribute('href'));
        if (q && q.ca) out.push({ ...q, el: a });
      });
    } catch (e) { /* ignore */ }
    return out;
  }

  // 一张卡片里**允许**出现的地址个数。
  // 战壕面板的卡片常常同时链到两个币（配对预设 P1/P2/P3、相关币、同车标的），
  // 老规则「出现第二个币就停」在卡片第一层就触发，于是整张卡片被当成「列表容器」放弃标记
  // —— 这就是「左边（列表）标得对、右边（战壕）一个都不标」的原因（用户 2026-11-18）。
  // 真正的容器（整列/整个网格）里的地址是十几个，而且高度必然超过 CARD_MAX_H，
  // 所以放宽到 2 仍然安全。
  const CARD_MAX_CAS = 2;

  function nearestToPointer(cas) {
    if (!cas || !cas.length) return null;
    if (cas.length === 1) return cas[0];
    let best = cas[0];
    let bestD = Infinity;
    for (const c of cas) {
      let d = Infinity;
      try {
        const r = c.el.getBoundingClientRect();   // 只有 2 个，代价可以忽略
        const dx = (r.left + r.width / 2) - lastPointer.x;
        const dy = (r.top + r.height / 2) - lastPointer.y;
        d = dx * dx + dy * dy;
      } catch (e) { /* ignore */ }
      if (d < bestD) { bestD = d; best = c; }
    }
    return best;
  }

  // 从某个元素向上找它所属的「单张代币卡片」。
  // **绝不越界**：地址个数超过 CARD_MAX_CAS 就说明爬到容器了，只认上一层。
  // 并且继续往上爬到「地址集合不再变化」的最外层为止 —— 那才是用户眼里的那张卡片
  // （内层常常只是名字行/图标行，标上去看着像只亮了一小块）。
  // 指针到底落在哪张卡片里。**只认同「正好一个」**：命中 0 个或多个就返回 null（不猜）。
  // 之前这里是「离指针中心最近」，结果把下面那张卡片当成了当前卡片
  //（慈善CZ 被认成 PK —— 用户 2026-11-19 用 GIGA X app 里的地址对出来的）。
  function pickByPointer(cas) {
    const inside = [];
    for (const c of cas) {
      let r = null;
      try {
        const card = cardFromAnchor(c.el, c.ca) || c.el;
        r = card.getBoundingClientRect();
      } catch (e) { continue; }
      if (!r || !r.width || !r.height) continue;
      if (lastPointer.x >= r.left && lastPointer.x <= r.right &&
          lastPointer.y >= r.top && lastPointer.y <= r.bottom) inside.push(c);
    }
    return inside.length === 1 ? inside[0] : null;
  }

  // 一张卡片里可能有多个代币链接（主币 + 配对币/相关币的小挂件）。
  // **主币 = 面积最大的那个链接**：图标/名字那种大块是主币，配对/相关币是小挂件。
  // 这一条是用户 2026-11-19 那张截图逼出来的：徽标显示 "PK1056(4/2)12h $19.79万…"，
  // 说明鼠标落在 IDRENT 卡片里那个「配对 PK1056」小挂件上，被当成了主币。
  const MAX_LINKS_IN_CARD = 5;
  function primaryCaInCard(cardEl) {
    let best = null;
    let bestArea = -1;
    const seen = new Set();
    for (const c of tokenLinksIn(cardEl)) {
      if (seen.has(c.ca)) continue;
      seen.add(c.ca);
      let area = 0;
      try {
        const r = c.el.getBoundingClientRect();
        area = (r.width || 0) * (r.height || 0);
      } catch (e) { /* ignore */ }
      if (area > bestArea) { bestArea = area; best = c; }
    }
    return best;
  }

  // ===== 最硬的取址来源：GMGN 自己写在 DOM 上的 data 属性 =====
  // 2026-11-19 用 chrome-devtools-mcp 打开真实战壕页实测（未登录，30 张卡）：
  //   <div data-trenches-token-address="0x44b9…7777"
  //        data-trenches-token-chain="bsc"
  //        data-trenches-token-key="0x44b9…7777-bsc">     ← 一卡一个，30 个 holder / 30 个不同 CA
  //   <div data-row-key="bsc:0xb825…7777">               ← 左列表一行一个
  //   <div data-recycler-key="bsc:0xb825…7777">          ← 虚拟列表的回收键
  //
  // **为什么这条是决定性的**：战壕卡片里 `a[href*="/token/"]` 的个数实测是 0/1/2 个，
  // 而且币名（"NVIDIASI"）是**纯文本节点**、不是链接。老版本靠代币链接找币，
  // 在战壕区要么一无所获（退到网络嗅探 → 全变成同一个币），要么爬到列容器里
  // 取到**下面那张卡**的链接（IDRENT 取到慈善CZ）。data 属性没有这些问题：
  // 它就是那张卡自己的、唯一的、机器可读的地址。
  //
  // 另一条实测证据：同一屏两张卡都叫 "PENGB Penguin Solutions"、都叫 "NVIDIASI"，
  // CA 却分别是 0x0c58…ffff / 0xd962…ffff 与 0x44b9…7777 / 0x407a…ffff。
  // 所以「靠 symbol 认币」从根上不可靠 —— 能拿到地址就绝不靠名字。
  // （常量 CA_DATA_ATTRS / CA_DATA_SEL 声明在文件靠上的「地址解析」段，collectUrlsIn 要用。）

  function parseCaKey(raw) {
    const s = String(raw || '').trim();
    if (!s) return null;
    const i = s.indexOf(':');
    let chain = null;
    let ca = s;
    if (i > 0) {
      const head = s.slice(0, i).toLowerCase();
      // 只有认得出的链名才当 chain，否则可能是 base58 里的冒号误判
      if (CHAINS.indexOf(head) >= 0 || head === 'solana' || head === 'ethereum') {
        chain = head === 'solana' ? 'sol' : (head === 'ethereum' ? 'eth' : head);
        ca = s.slice(i + 1);
      }
    }
    if (!ca || !(isEvm(ca) || isSol(ca))) return null;
    return { chain, ca };
  }

  // 从任意元素往上找「它所属的那张卡」自带的地址。返回 el = 属性所在节点 = 那张卡片本身。
  function caFromDataAttrs(el) {
    if (!el || !el.closest) return null;
    let node = null;
    try { node = el.closest(CA_DATA_SEL); } catch (e) { return null; }
    if (!node) return null;
    let h = 0;
    try { h = node.getBoundingClientRect().height || 0; } catch (e) { h = 0; }
    if (h > CA_DATA_MAX_H) return null;    // 爬到了整块列表/面板，放弃
    for (const attr of CA_DATA_ATTRS) {
      const raw = node.getAttribute(attr);
      if (!raw) continue;
      const p = parseCaKey(raw);
      if (!p) continue;
      let chain = p.chain;
      if (!chain) {
        try { chain = String(node.getAttribute('data-trenches-token-chain') || '').toLowerCase() || null; } catch (e) {}
      }
      return { chain: chain || null, ca: normCa(p.ca), el: node, src: attr, via: 'data' };
    }
    return null;
  }

  function caFromElement(el) {
    // 0) 权威来源：GMGN 写在 DOM 上的 data 属性。
    //    先它、且**只认它**的话就不会串币 —— 战壕卡/列表行在真实 DOM 里都有。
    const dv = caFromDataAttrs(el);
    if (dv) return dv;

    // 1) 指针所在元素本身就在一个「够大」的代币链接里（整张卡片包在 <a> 里，或图标/名字链接）
    try {
      const a = el.closest ? el.closest('a[href*="/token/"]') : null;
      if (a) {
        const q = parseUrlToken(a.getAttribute('href'));
        const r = a.getBoundingClientRect();
        // 太小的（配对币小挂件）不算主币，继续走卡片层逻辑
        if (q && q.ca && (r.width || 0) >= 80 && (r.height || 0) >= 28) {
          return { chain: q.chain, ca: q.ca, el: a };
        }
      }
    } catch (e) { /* ignore */ }

    // 2) 找卡片层：**从里往外遇到的第一个「卡片大小」且带代币链接的祖先**。
    //    必须从里往外停，不能一路爬到最外层 —— 战壕一列里相邻两张卡会被同一个
    //    列容器包住，爬到那层就等于把两张卡当成一张（IDRENT 取到下面慈善CZ，用户 2026-11-19）。
    let cur = el;
    let card = null;
    let cardCas = null;
    for (let k = 0; k < 14 && cur && cur !== document.body; k++) {
      const cas = uniqueCas(tokenLinksIn(cur));
      if (cas.length) {
        let r = null;
        try { r = cur.getBoundingClientRect(); } catch (e) { /* ignore */ }
        const cardSized = r && r.height >= 40 && r.width >= 100;
        if (cardSized) { card = cur; cardCas = cas; break; }
        if (cas.length > MAX_LINKS_IN_CARD) break;    // 已经是大容器，别再往上
      }
      cur = cur.parentElement;
    }
    if (!card || !cardCas) return null;
    if (cardCas.length === 1) return { chain: cardCas[0].chain, ca: cardCas[0].ca, el: card };

    // 3) 多个链接：按面积取主币；面积完全并列（认不出主次）就放弃，不猜
    const primary = primaryCaInCard(card);
    if (!primary) return null;
    const areas = [];
    const seen2 = new Set();
    for (const c of tokenLinksIn(card)) {
      if (seen2.has(c.ca)) continue;
      seen2.add(c.ca);
      let a2 = 0;
      try { const r = c.el.getBoundingClientRect(); a2 = (r.width || 0) * (r.height || 0); } catch (e) {}
      areas.push(a2);
    }
    areas.sort((x, y) => y - x);
    if (areas.length > 1 && areas[0] === areas[1]) return null;   // 并列 → 不猜
    return { chain: primary.chain, ca: primary.ca, el: card };
  }

  // 鼠标下面那张卡片对应的币（悬浮窗浮在上面，用 elementsFromPoint 穿透）。
  // 跳过**所有**浮层（不只当前 root）：页面上可能同时存在两三个 tooltip，只跳过 root
  // 的话，第一个命中的会是另一个浮层里的内容。
  function caFromPointer(root) {
    try {
      const stack = document.elementsFromPoint(lastPointer.x, lastPointer.y) || [];
      // 指针**就在浮层上**时，穿透看到的那个币毫无意义 —— 它只是浮层背后碰巧挡着的
      // 那张卡片/列表行，不是这个浮层讲的币。
      // 这一条是「浮窗先是大字、过一会儿变成很小字体」的真凶（2026-11-19）：
      // 浮层盖住指针 → 底下穿透到另一张卡片（诊断日志里 ptr=m4ckeq8s8j、hov=4oduvmdiWG）
      // → staleTarget 的 C 判据 1 秒判一次「换币」→ resetRoot 把原生小字还原 →
      // 下一轮又从缓存重绘 → 页面就这么 1 秒抖一次。
      if (stack.length && root && (root === stack[0] || root.contains(stack[0]))) return null;
      // 自己的「叙事关键词」面板也是 fixed 浮层：指针停在它上面时，穿透到底下的卡片同样没意义
      //（拖面板时尤其容易撞上，别让它把浮窗身份改掉）。
      if (stack.length && stack[0] && stack[0].closest && stack[0].closest('#gigax-kw-panel')) return null;
      for (const r of knownRoots) {
        if (stack.length && (r === stack[0] || r.contains(stack[0]))) return null;
      }
      for (const el of stack) {
        if (root && (root === el || root.contains(el))) continue;
        if (isFloatingLayer(el)) continue;
        const hit = caFromElement(el);
        if (hit) return hit;
      }
    } catch (e) { /* ignore */ }
    return null;
  }

  // ===== hover 锚点：浮窗的「因」=====
  // 悬浮窗是「hover 某张卡片」触发出来的，那张卡片才是浮窗对应的币。但浮窗浮起来后
  // 往往盖住鼠标，事后再用 elementsFromPoint 已经摸不到卡片了 —— 锚点一丢，就只能退到
  // 「详情页 URL / 网络请求」，于是卡片是 Pussy、叙事却是 hotdog（用户 2026-11-18）。
  // 所以在 hover 的**当时**就把 CA + 卡片矩形 + 卡片上的 symbol 记下来，
  // 事后用三条随时可验证的判据来认：「指针还在卡片里」「浮窗文字里有这个 symbol」
  // 「浮窗紧贴这张卡片」。
  // hover 锚点的有效期。原来是 120 秒 —— 太长了：用户可能早就把鼠标移到别的币上了，
  // 旧锚点还在，就会被当成「当前这个浮层讲的币」（IDRENT 显示雪球）。20 秒足够
  // 覆盖「hover 卡片 → 浮层出现 → 读几行」的真实间隔。
  const HOVER_TTL = 20000;
  const HOVER_ADJ_TTL = 20000;
  const HOVER_ADJ_GAP = 320;
  let hoverRec = null;              // {chain, ca, el, rect, sym, ts}
  // 最后一次「指针落在非卡片区域」的时间。**比 hoverRec.ts 新就说明人已经离开卡片**，
  // 锚点必须作废 —— 否则从列表划到图表上，旧卡片的币会一直顶替页面自己的币。
  let hoverMissAt = 0;
  let hoverRaf = 0;

  function symbolOfCard(cardEl, ca) {
    let sym = '';
    try {
      cardEl.querySelectorAll('a[href*="/token/"]').forEach(a => {
        const q = parseUrlToken(a.getAttribute('href'));
        if (!q || q.ca !== ca || sym) return;
        // textContent 不触发布局（innerText 会）
        const t = (a.textContent || '').replace(/\s+/g, ' ').trim();
        // 这个链接常常包着**整行**文字（"DCTY28829(18/11)10h $7.49万-4.45%"），
        // 直接当前缀塞进徽标会很难看。只取开头的币名：遇到空格 / 括号 / 分隔符就截断。
        const cut = t.split(/[\s(（\[|·•、,，]/)[0] || '';
        const clean = cut.replace(/[^A-Za-z0-9_$\-]/g, '');
        if (clean && /[A-Za-z]/.test(clean) && clean.length <= 16) sym = clean;
        else if (t && t.length <= 32) sym = t;
      });
    } catch (e) { /* ignore */ }
    return sym;
  }

  function noteHover(ev) {
    if (hoverRaf) return;
    const target = ev.target;
    if (!target || target.nodeType !== 1) return;
    // 便宜的先判：还在同一张卡片里挪来挪去（改价格、动图标都会重发 mouseover），
    // 锚点没变就不用再算一遍 —— 这一步把高频事件的成本压到接近 0。
    if (hoverRec && hoverRec.el && hoverRec.el.isConnected &&
        hoverRec.el.contains(target) && Date.now() - hoverRec.ts < 4000) return;
    hoverRaf = requestAnimationFrame(() => {
      hoverRaf = 0;
      try {
        // 指针在浮层里（正在看我方/原生浮窗）时不算新的锚点
        let node = target;
        for (let i = 0; i < 12 && node && node !== document.body; i++) {
          if (isFloatingLayer(node)) return;
          node = node.parentElement;
        }
        const hit = caFromElement(target);
        if (!hit) {
          // 指针移到了**不是卡片**的地方（详情页头部、K 线 iframe、空白区）。
          // 老写法直接 return，于是上一个划过的卡片的锚点一直留着 —— 用户从左侧列表
          // 划到图表上，hotdog 页面就弹出了列表里那个币（COST）的叙事（2026-11-19）。
          // 这里只**记账**，不动 hoverRec：指针移进浮层时上面的 early-return 已经拦掉了，
          // 所以能走到这里的「不是卡片」确实意味着人已经离开卡片了。
          if (!(hoverRec && hoverRec.el && hoverRec.el.isConnected && hoverRec.el.contains(target))) {
            hoverMissAt = Date.now();
          }
          return;
        }
        const cardEl = hit.el;
        const rect = cardEl.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        hoverRec = {
          chain: hit.chain, ca: hit.ca, el: cardEl, rect,
          sym: symbolOfCard(cardEl, hit.ca), ts: Date.now(),
          ambiguous: !!hit.ambiguous,
        };
      } catch (e) { /* ignore */ }
    });
  }
  document.addEventListener('mouseover', noteHover, { passive: true, capture: true });

  function rectNear(a, b, gap) {
    if (!a || !b) return false;
    const dx = Math.max(a.left - b.right, b.left - a.right, 0);
    const dy = Math.max(a.top - b.bottom, b.top - a.bottom, 0);
    return dx <= gap && dy <= gap;
  }

  // 宽松版 hover 锚点：只要「最近 120 秒内 hover 过某张卡片」就认，不要求指针还在那张卡片里。
  // 用在**浮层自身没有任何身份文字**的场景 —— GMGN 有些叙事浮层里只有正文 + 免责声明，
  // 名字在外面那张卡片上。这种浮层就是「hover 卡片弹出来的」，它讲的一定是那张卡片；
  // 指针后来移进浮层里（几何判据失效）不代表浮层换了币。
  // 更关键的是：这类浮层换币时正文节点**不会重写**，不认 hover 就只能永远停在上一段
  //（「红薯小猫弹出 PIKA」，用户 2026-11-19）。
  function hoverLoose() {
    const r = hoverRec;
    if (!r) return null;
    if (Date.now() - r.ts > HOVER_TTL) return null;
    if (!r.chain && !r.ca) return null;
    return { chain: r.chain, ca: r.ca };
  }

  function hoverTarget(root) {
    const r = hoverRec;
    if (!r) return null;
    const age = Date.now() - r.ts;
    if (age > HOVER_TTL) return null;
    // 指针后来移到了非卡片区域（详情页头部 / K 线 iframe / 空白）→ 这个锚点已经是「上一个币」了。
    if (hoverMissAt > r.ts) return null;

    const base = { chain: r.chain, ca: r.ca };
    if (r.ambiguous) base.ambiguous = true;
    // 卡片被 React 重渲染（价格每跳一次就可能换节点）时，el 会断开 —— 几何判据失效，
    // 但「浮窗文字里有这个 symbol」依然成立，所以不能在这里整条否掉。
    const live = !!(r.el && r.el.isConnected);

    if (live) {
      // 判据 1：指针还在那张卡片里（浮窗盖住不影响卡片的几何矩形，这条最可靠）
      const b = r.el.getBoundingClientRect();
      const pad = 14;
      if (lastPointer.x >= b.left - pad && lastPointer.x <= b.right + pad &&
          lastPointer.y >= b.top - pad && lastPointer.y <= b.bottom + pad) {
        return { ...base, why: 'hover' };
      }
    }

    // 判据 2：浮窗正文里出现了那张卡片的 symbol（浮窗讲的就是它）。
    // 注意：我们自己塞进去的文本里带着**上一个币**的名字（「PIKA（pika.meme）…」），
    // 拿它做匹配会自我印证，所以页面上有我们的内容时这条判据直接不用。
    if (r.sym && r.sym.length >= 2) {
      const txt = root.textContent || '';
      if (!txt.includes('GIGA X')) {
        if (txt.toLowerCase().includes(r.sym.toLowerCase())) return { ...base, why: 'hover-sym' };
      }
    }

    // 判据 3（浮窗紧贴卡片）已删除：320px 的邻近距离会把「上一次 hover 的币」当成当前币，
    // 是「我看的是 IDRENT、却显示雪球」的直接来源（用户 2026-11-19）。
    // 浮层贴不贴卡片不该作为认币依据 —— 认币靠身份、指针穿透、GMGN 的请求。
    return null;
  }

  // ===== 浮层身份：从浮层**自己的文字**认币 =====
  // 我们一旦把正文替换掉，浮层里关于「这是哪个币」的直接证据（那一段原文）就没了，
  // 只能靠 hover/指针去猜 —— 于是出现「红薯小猫弹出 PIKA」（用户 2026-11-19）。
  // 但浮层的**头部**（名字、@handle、税、标签）是 GMGN 自己渲染的，一直还在。
  // 把头部文字和页面上各张卡片的 symbol 对一下，就能反过来认出这个浮层是哪个币 —— 这是
  // 最硬的证据，比 hover 和网络嗅探都可靠。
  function popupOwnText(root, node) {
    let txt = '';
    try {
      const walk = (el) => {
        for (const c of el.childNodes) {
          if (c === node) continue;
          if (c.nodeType === 3) { txt += ' ' + (c.nodeValue || ''); continue; }
          if (c.nodeType !== 1) continue;
          // 我们自己写进去的整块（含徽标）一律跳过 —— 不能靠 node 身份判断，
          // React 可能换掉那个节点，留我们旧内容在别的子树上。
          if ((c.textContent || '').includes('GIGA X')) continue;
          walk(c);
        }
      };
      walk(root);
    } catch (e) { /* ignore */ }
    return txt;
  }

  // 指纹：只留字母，剥掉所有数字 —— 市值/价格/百分比每跳一次都会变，名字和标签不会。
  function popupFingerprint(root, node) {
    return popupOwnText(root, node)
      .replace(/\d+/g, '')
      .replace(/[^\p{L}]+/gu, '')
      .toLowerCase()
      .slice(0, 160);
  }

  const identityCache = new Map();   // 头部文字 -> {chain, ca} | null
  const lettersOnly = (s) => String(s || '').replace(/\d+/g, '').replace(/[^\p{L}]+/gu, '').toLowerCase();

  // 两个串的公共前缀长度。浮层头部的开头就是币名，卡片文字的开头也是币名，
  // 所以「公共前缀最长的那个卡片」基本就是这个浮层讲的币。
  function commonPrefixLen(a, b) {
    const n = Math.min(a.length, b.length);
    let i = 0;
    while (i < n && a[i] === b[i]) i++;
    return i;
  }

  function caFromPopupIdentity(root) {
    const head = lettersOnly(popupOwnText(root, nodeOf(root))).slice(0, 160);
    if (head.length < 3) return null;
    // 用前 40 个字母做缓存键：头部尾部可能有「分钟前 / 持有」这类会变的字，
    // 全串做键会让缓存一直打不中，于是每轮都去扫一遍全页（很贵）。
    const key = head.slice(0, 40);
    if (identityCache.has(key)) return identityCache.get(key);

    let best = null;
    let bestLen = 0;
    let tie = false;
    const seen = new Set();
    try {
      for (const a of document.querySelectorAll('a[href*="/token/"]')) {
        const p = parseUrlToken(a.getAttribute('href'));
        if (!p || !p.ca || seen.has(p.ca)) continue;
        seen.add(p.ca);
        const card = cardFromAnchor(a, p.ca);
        if (!card) continue;
        const cardText = lettersOnly(card.textContent).slice(0, 200);
        if (!cardText) continue;
        const n = commonPrefixLen(head, cardText);
        if (n < 3) continue;          // 至少对上 3 个字，否则当噪声
        if (n > bestLen) { best = { chain: p.chain, ca: p.ca }; bestLen = n; tie = false; }
        else if (n === bestLen) { tie = true; }
      }
    } catch (e) { /* ignore */ }

    const result = (best && !tie) ? best : null;
    if (identityCache.size > 80) identityCache.clear();
    identityCache.set(key, result);
    if (result) log('浮层身份认出:', result.chain + ':' + result.ca,
                    '| 前缀', bestLen, '|', head.slice(0, 30));
    return result;
  }

  // 某个 CA 在页面上对应的卡片元素（用于几何校验，带缓存）
  const cardCaCache = new Map();
  function cardForCa(ca) {
    if (!ca) return null;
    if (cardCaCache.has(ca)) {
      const c = cardCaCache.get(ca);
      if (!c || c.isConnected) return c;
    }
    let el = null;
    try {
      for (const a of document.querySelectorAll('a[href*="/token/"]')) {
        const p = parseUrlToken(a.getAttribute('href'));
        if (!p || p.ca !== ca) continue;
        el = cardFromAnchor(a, ca);
        if (el) break;
      }
    } catch (e) { /* ignore */ }
    if (cardCaCache.size > 300) cardCaCache.clear();
    cardCaCache.set(ca, el);
    return el;
  }

  // GMGN 的叙事请求能不能用来认这个浮层的币：
  // ① 这个币在页面上确实有卡片；② 那张卡片离浮层不远（400px 内）。
  // 这条挡住了「GMGN 预取隔壁卡片的叙事 → 我们拿来当当前币」——
  // 用户 2026-11-19「第一次对、第二次就变成 PIKA」就是邻居预取干的。
  function freshPlausible(root, ev) {
    if (!ev || !ev.ca) return false;
    if (!caPresentOnPage(ev.ca)) return false;
    const card = cardForCa(ev.ca);
    if (!card) return true;
    try {
      return rectNear(card.getBoundingClientRect(), root.getBoundingClientRect(), 400);
    } catch (e) { return true; }
  }

  // 最终判定（越靠前越可信）：
  //   0 浮窗自己 DOM 里的唯一地址 —— 浮窗自带地址，就是铁证
  //   1 浮层头部身份（名字对上页面唯一卡片）
  //   2 **GMGN 自己刚发的叙事请求（≤3 秒）** —— 浮窗就是按这次请求渲染的，这是最早能用、
  //      也一直最管用的判据（用户 2026-11-19：「之前为什么拿得到，按之前的来」）
  //   3 hover 锚点（卡片矩形 + symbol）
  //   4 指针穿透到的卡片
  //   5 宽松 hover（浮层里没有身份文字、指针又落在浮层里时）
  //   6 浮窗多个地址时，取能和上面几个候选对上的那个
  //   7 详情页 URL 的 CA
  //   8 叙事请求（≤30 秒，且页面上必须找得到这个币的链接）
  //
  // 五次串味的成因（用户 2026-11-18~19）：
  //   a) 列表页 hover Builder Inu → 显示页面主体 IDRENT 的叙事：老代码拿「页面 URL 的币」兜底；
  //   b) hotdog 详情页浮窗 → 显示 COST：叙事请求窗口 **120 秒**太长，混进了 COST 的预取；
  //   c) 战壕卡片 Pussy → 叙事 hotdog：详情页 URL 又排到了 hover 之前；
  //   d) 反查卡片爬到列表容器，取到「第一张卡片的币」；
  //   e) 卡片里挂了 3 个以上代币链接时 hover 锚点永远认不出 → 所有浮窗停在同一段叙事。
  // 结论：**网络请求本身是对的，错的是窗口太长**。所以把它提回前列，窗口收到 3 秒 ——
  // 浮窗出现的这一刻，最近 3 秒内 GMGN 只为它发过请求。
  // 指针此刻是不是**真的落在**一张带 GMGN data 属性的卡片上（战壕卡 / 列表行）。
  // 详情页只认这一种「别的币」的证据，理由见 resolveTarget 的 0a：
  // 页面头部的「配对币」小挂件（本例 hotdog 的配对币是 COST）、以及从浮层往上爬到的容器，
  // 都可能带一个 `/token/` 链接，把页面的币顶掉。
  function pointerOnDataCard(root) {
    try {
      const stack = document.elementsFromPoint(lastPointer.x, lastPointer.y) || [];
      for (const el of stack) {
        // 浮层挡在指针最上面：这说明人手在浮层上、不是在底下的卡片上。
        // 详情页一律按「页面这个币」处理（列表页不受影响，那条链路根本不进这个函数）。
        if (root && (root === el || root.contains(el))) return false;
        if (!el.closest) continue;
        const node = el.closest(CA_DATA_SEL);
        if (!node) continue;
        if ((node.getBoundingClientRect().height || 0) > CA_DATA_MAX_H) continue;
        return true;
      }
    } catch (e) { /* ignore */ }
    return false;
  }

  function resolveTarget(root) {
    // 0) 浮窗自己带唯一 CA
    const inRoot = uniqueCas(collectUrlsIn(root));
    if (inRoot.length === 1) return { ...inRoot[0], why: 'popup' };

    // 指针此刻**真正**落在哪张卡片上（穿透所有浮层；拿不到 = 不在任何卡片上）。
    // 只算一次，后面 0a / 3 / popup-multi 共用 —— 每次都 elementsFromPoint 太贵。
    const underPtr = caFromPointer(root);

    // 0a) 详情页（`/<chain>/token/<CA>`）的浮层，讲的就是**这个页面的币**。
    //     **只有指针真的落在带 data 属性的卡片（左侧列表行）上时**，才允许是别的币。
    //
    //     为什么必须这么硬（2026-11-19 用户实测：「第一次 hover 对，第二次就变成 COST」，
    //     而 hotdog 这个币正是 **COST 池子里的**）：
    //       · 页面自己的币在 DOM 里没有任何 data 属性、也没有指向自己的 token 链接；
    //       · K 线是 TradingView 的 **iframe**，指针在图表上时 elementsFromPoint 只拿到 IFRAME;
    //       · 于是剩下的判据（hover 锚点 / 浮层祖先里那个「配对币」链接 / GMGN 为配对币发的
    //         叙事预取 / 上一轮留下的 bind 绑定）**全都指向 COST**，而它们没有一个能证明
    //         「用户现在看的是 COST 的卡片」。
    //     手指没落在卡片上 = 用户在读页面这个币 —— 这条没有例外。
    const pgNow = pageToken();
    if (pgNow && !pointerOnDataCard(root)) {
      return { ...pgNow, why: 'page-url' };
    }

    // 0b) 浮层**首次出现时**绑定的身份（那一刻鼠标就在对应卡片上，且是从卡片的
    //     `a[href*="/token/"]` 直接取的 CA）。只要 GMGN 还没为新币发叙事请求，就一直认它。
    const bind = bindValid(root);
    if (bind) {
      const i = bind.indexOf(':');
      const bchain = bind.slice(0, i) || null;
      const bca = bind.slice(i + 1);
      if (bca) return { chain: bchain, ca: bca, why: 'bind' };
    }

    // 1) 浮层头部文字（名字 / @handle）对得上页面上唯一的某张卡片 —— 最硬的证据，
    //    因为它读的是「GMGN 自己写的这个浮层是谁」，跟鼠标此刻在哪无关。
    const id = caFromPopupIdentity(root);
    if (id) return { ...id, why: 'identity' };

    const hover = hoverTarget(root);
    const fresh = latestAiEvent(NET_AI_FRESH);

    // 2) 严格 hover：指针确实还停在那张卡片里，或浮层文字里出现了那张卡片的 symbol
    if (hover && !hover.ambiguous) return hover;

    // 3) 指针穿透：浮层浮在鼠标上方时，elementsFromPoint 能拿到**浮层底下**那张卡片
    if (underPtr && !underPtr.ambiguous) return { ...underPtr, why: 'pointer' };

    // 4) GMGN 自己刚（≤3s）为这个浮层发的叙事请求。
    //    hover 含糊（卡片那一层挂了多个代币链接，只能靠指针猜）时**优先信它**：
    //    慈善CZ 就是被含糊的 hover 认成了隔壁的 PK，而 GMGN 的请求 `0x70c35410…` 是对的
    //    （用户 2026-11-19 拿 GIGA X app 里的地址对出来的）。
    if (fresh && freshPlausible(root, fresh)) return { ...fresh, why: 'network-ai' };

    // 5) 只剩含糊结果时退而用之（仍比 URL 兜底强）
    if (hover) return hover;
    if (underPtr) return { ...underPtr, why: 'pointer' };

    // 3) 浮层自身没有任何身份文字（GMGN 有些叙事浮层里只有正文 + 免责声明，名字在外面卡片上），
    //    指针又落在浮层里 —— 只能认「最后一个 hover 过的卡片」，浮层就是 hover 它弹出来的。
    if (!id) {
      const loose = hoverLoose();
      if (loose) return { ...loose, why: 'hover-loose' };
    }

    // 4) 详情页 URL（只认 /token/ 形式的地址页）
    const onPage = pgNow;

    // 4) 浮窗里有多个地址：优先取能和上面几个候选对上的那个，别盲取第一个
    if (inRoot.length > 1) {
      const cands = [underPtr && underPtr.ca, onPage && onPage.ca].filter(Boolean);
      const pick = cands.length ? inRoot.find(it => cands.includes(it.ca)) : null;
      if (pick) return { ...pick, why: 'popup-multi' };
    }

    // 5) 页面地址
    if (onPage) return { ...onPage, why: 'page-url' };

    if (inRoot.length > 1) return { ...inRoot[0], why: 'popup-multi' };

    // 6) 网络嗅探：最后兜底，且必须在页面上找得到这个币的链接。
    //    找不到就说明这是别的币（GMGN 预取 / 上一张卡片）的请求 → 宁可返回 null 不替换。
    const ai = latestAiEvent();
    if (ai && caPresentOnPage(ai.ca)) return { ...ai, why: 'network-ai' };

    return null;
  }

  // 最近一次「叙事类」请求（netEvents 按时间顺序追加，从后往前找）
  // maxAge 不传就按 NET_AI_TTL；传 NET_AI_FRESH 就是「刚刚才发的」那一种。
  function latestAiEvent(maxAge) {
    const now = Date.now();
    const ttl = Number(maxAge) || NET_AI_TTL;
    for (let i = netEvents.length - 1; i >= 0; i--) {
      const e = netEvents[i];
      if (now - e.ts > ttl) break;
      if (e.isAi) return e;
    }
    return null;
  }

  // 当前页面是不是「某个币的详情页」；是就返回它，否则 null。
  // 只认 `/<chain>/token/<CA>` 形态 —— GMGN 的榜单页、喊单页、监控页都不是这种路径。
  function pageToken() {
    try {
      if (!/\/token\//i.test(location.pathname)) return null;
      const p = parseUrlToken(location.href);
      if (!p || !p.ca) return null;
      return { chain: p.chain, ca: p.ca };
    } catch (e) { return null; }
  }

  // 这个 CA 在页面上是否真的有一张卡片/链接（零 layout）
  function caPresentOnPage(ca) {
    if (!ca) return false;
    try {
      let hit = false;
      document.querySelectorAll('a[href*="/token/"]').forEach(a => {
        if (hit) return;
        const q = parseUrlToken(a.getAttribute('href'));
        if (q && q.ca === ca) hit = true;
      });
      return hit;
    } catch (e) { return false; }
  }

  function extractChain(root, hint) {
    if (hint && hint.chain) return hint.chain;
    if (hint && hint.ca && !EVM_RE.test(hint.ca)) return 'sol';
    const src = (root?.outerHTML || '') + ' ' + location.pathname;
    if (/\/sol(\/|$|\b)/i.test(src) || /solana/i.test(src)) return 'sol';
    if (/\/base(\/|$|\b)/i.test(src)) return 'base';
    if (/\/eth(\/|$|\b)/i.test(src)) return 'eth';
    if (/\/robin(\/|$|\b)/i.test(src)) return 'robinhood';
    return 'bsc';
  }

  // ===== 定位悬浮窗根容器 =====
  // 读 innerText / getBoundingClientRect 都会触发 layout，交易页上动辄几千个 div，
  // 全量扫会把主线程卡住 —— 用户 2026-11-18 反馈「装了插件后原生数据（K线图）经常
  // 卡顿/加载失败」，根因就是这里的全量 innerText 深扫 + 800ms 轮询一起压主线程。
  // 所以：深扫间隔放宽到 3 秒，且只扫带浮层 class 提示的元素，不再遍历所有 div。
  let knownRoots = [];
  let lastDeepScan = 0;
  const DEEP_SCAN_MS = 15000;   // 深扫保底降到 15s（真正触发靠叙事请求/DOM 变化），不抢主线程

  // 「确实是浮层」的硬判据：fixed/absolute 定位，或明确的 tooltip/popover class。
  // 只看「文本里带 AI 词」会把页面主体（GMGN 那些 AI 情报弹幕、AI 分析区）误判成
  // 叙事浮窗，然后 maskNative 清空它的内容 → React 崩溃 → 主体黑屏（用户 2026-11-18）。
  function isFloatingLayer(el) {
    try {
      const s = getComputedStyle(el);
      if (s.position === 'fixed' || s.position === 'absolute') return true;
      const cls = (typeof el.className === 'string' ? el.className : '').toLowerCase();
      return CONTAINER_HINTS.some(h => cls.includes(h));
    } catch (e) { return false; }
  }

  // 深扫时「多少字的叶子才值得看一眼」。
  // **这个下限原来是 30，是「有时候不显示 GIGA X 叙事」的直接原因**（2026-11-19）：
  // GMGN 的叙事浮层里唯一带锚点词的那一行是页脚
  // 「免责声明：人工智能生成内容，投资前请自行研究。」——**只有 25 个字**，
  // 而它上面那段正文（AI 生成的币种介绍）不含任何锚点词。
  // 于是 hint-class 那条路（组件没挂 tooltip/popover 类名）走不通、深扫又被 30 字挡回去，
  // 这个浮层从头到尾没被识别成「浮层」—— 脚本一次都没碰过它，页面就一直显示原生文案。
  // 放到 10 是对的：下面还有 isFloatingLayer / 尺寸上限两道闸，页面正文不会被误伤。
  const DEEP_MIN_TEXT = 10;
  const DEEP_MAX_TEXT = 600;

  function isPopupSized(el) {
    try {
      const r = el.getBoundingClientRect();
      const vw = window.innerWidth || 1280;
      const vh = window.innerHeight || 800;
      return r.width > 0 && r.height > 0 && r.width <= vw * 0.95 && r.height <= vh * 0.90;
    } catch (e) { return false; }
  }

  function findFloatingRoots() {
    const roots = new Set();
    // 初筛：必须是「浮层」，再要求带 AI 叙事特征。两个条件都满足才认。
    for (const hint of CONTAINER_HINTS) {
      document.querySelectorAll(`[class*="${hint}" i]`).forEach(el => {
        if (isVisible(el) && looksLikeAIFloat(el)) roots.add(el);
      });
    }
    // 深扫兜底：只认「确实是浮层」的节点，绝不动页面主体的普通文本。
    if (Date.now() - lastDeepScan > DEEP_SCAN_MS) {
      lastDeepScan = Date.now();
      document.querySelectorAll('div,span,p').forEach(el => {
        if (el.childElementCount > 1) return;
        const txt = el.textContent || '';
        if (txt.length < DEEP_MIN_TEXT || txt.length > DEEP_MAX_TEXT) return;
        if (!ANCHORS.some(a => txt.includes(a))) return;
        const fr = climbToFloatRoot(el);
        if (!fr) return;
        // 尺寸上限：页面级容器（整个 main / app shell）也可能是 fixed，
        // 认它当浮层就会把正文一起改写 —— 这条闸必须留着。
        if (!isPopupSized(fr)) return;
        roots.add(fr);
      });
    }
    for (const el of knownRoots) {
      if (el.isConnected && isVisible(el)) roots.add(el);
    }
    // 自己的面板（fixed）绝不能被当成叙事浮层 —— 认错的后果是它会把面板正文改写成「加载中」
    const out = [...roots].filter(el => el && el.isConnected && isFloatingLayer(el)
      && !(el.closest && el.closest('#gigax-kw-panel')));
    knownRoots = out;
    return out;
  }
  function isVisible(el) {
    const s = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 120 && r.height > 40;
  }
  function looksLikeAIFloat(el) {
    const txt = el.innerText || '';
    return ANCHORS.some(a => txt.includes(a)) && txt.length > 30;
  }  function climbToFloatRoot(el) {
    let cur = el;
    // 先看自己是不是浮层；不是再往上找。找不到就返回 null（别把页面主体当浮层）。
    if (isFloatingLayer(cur)) return cur;
    for (let i = 0; i < 6 && cur.parentElement; i++) {
      const p = cur.parentElement;
      const s = getComputedStyle(p);
      const cls = (typeof p.className === 'string' ? p.className : '').toLowerCase();
      const isFloat = ['fixed', 'absolute'].includes(s.position) ||
        CONTAINER_HINTS.some(h => cls.includes(h));
      if (isFloat) return p;
      cur = p;
    }
    return null;
  }

  // ===== 找正文节点（要替换的那段）=====
  function findTextNode(root) {
    let anchorEl = null;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    while (walker.nextNode()) {
      const el = walker.currentNode;
      const t = (el.textContent || '');
      if (ANCHORS.some(a => t.includes(a)) && el.children.length === 0) { anchorEl = el; break; }
    }
    if (!anchorEl) {
      let best = null, bestLen = 0;
      root.querySelectorAll('div,p,span').forEach(el => {
        if (el.children.length <= 1 && (el.innerText || '').length > bestLen) {
          best = el; bestLen = (el.innerText || '').length;
        }
      });
      return best;
    }
    let target = anchorEl.previousElementSibling;
    if (target && (target.innerText || '').length > 15) return target;
    const parent = anchorEl.parentElement;
    if (parent) {
      for (const child of parent.children) {
        if (child !== anchorEl && !ANCHORS.some(a => (child.textContent || '').includes(a)) &&
            (child.innerText || '').length > 15) return child;
      }
    }
    return parent || anchorEl;
  }

  // ===== 调桥接服务 =====
  // cb(text, err, info)：info.reason = 'no-narrative' | 'auth' | 'error'
  const inflight = new Map();  // 同一个 CA 只打一枪：轮询 / DOM 观察 / hover 预热会同时来问

  function peekCache(chain, address) {
    const c = cache.get(chain + ':' + address);
    if (!c) return null;
    const ttl = c.ttl || (c.text ? CACHE_TTL : NO_DATA_TTL);
    return (Date.now() - c.ts < ttl) ? c : null;
  }

  function fetchNarrative(chain, address, cb, meta) {
    const key = chain + ':' + address;
    const hit = peekCache(chain, address);
    if (hit) return cb(hit.text || null, hit.error || null, hit);
    const waiters = inflight.get(key);
    if (waiters) { waiters.push(cb); return; }
    const list = [cb];
    inflight.set(key, list);
    const done = (text, err, info) => {
      inflight.delete(key);
      for (const fn of list) { try { fn(text, err, info); } catch (e) { warn(e); } }
    };
    // 把「脚本版本 / 取址判据 / hover 到的币」带进 URL：服务端访问日志里就能直接看到
    // 用户跑的是哪一版、这次用的是哪条判据 —— 不用再猜，也不用让用户翻控制台。
    const extra = `&v=${encodeURIComponent(SCRIPT_VERSION)}` +
      (meta && meta.why ? `&src=${encodeURIComponent(meta.why)}` : '') +
      (meta && meta.hover ? `&h=${encodeURIComponent(String(meta.hover).slice(0, 12))}` : '');
    const url = `${API}/narrative?chain=${encodeURIComponent(chain)}&address=${encodeURIComponent(address)}${extra}`;
    GM_xmlhttpRequest({
      method: 'GET', url, timeout: 75000,
      onload(res) {
        const body = res.responseText || '';
        let d = null;
        try { d = JSON.parse(body); } catch (e) { /* 下面统一报错 */ }
        if (d && d.ok && d.text) {
          cache.set(key, { text: d.text, ts: Date.now() });
          schedulePaint(50);          // 叙事刚到手：顺手把卡片染上（不用等下一轮扫）
          done(d.text, null, d);
          return;
        }
        if (d) {
          const info = {
            reason: d.reason || 'error',
            status: d.status || '',
            retryAfter: Number(d.retryAfter || 0),
            error: d.error || '桥接没有返回正文',
          };
          // status=generating 会带 retry_after_seconds（实测 5s）：GIGA X 正在写，很快就有了，
          // 别跟着 2 分钟冷却一起躺平
          const ttl = info.reason === 'no-narrative'
            ? Math.min(NO_DATA_TTL, Math.max(10000, info.retryAfter * 1000 + 2000))
            : NO_DATA_TTL;
          cache.set(key, { text: '', ts: Date.now(), ttl, ...info });
          if (info.reason !== 'no-narrative') warn('桥接返回失败:', info.error);
          done(null, info.error, info);
          return;
        }
        warn('桥接返回非 JSON', 'HTTP', res.status, body.slice(0, 120));
        done(null, `桥接返回非 JSON（HTTP ${res.status}）${body.slice(0, 60).replace(/\s+/g, ' ')}`,
             { reason: 'error' });
      },
      onerror() {
        done(null, '连不上桥接服务 ' + API + '（确认 market-hot-dashboard 主服务 8765 在跑）', { reason: 'error' });
      },
      ontimeout() {
        done(null, '桥接服务超时（GIGA X 侧可能正在续期 token）', { reason: 'error' });
      },
    });
  }

  // ===== 机会标记：从叙事里提关键词，把对应卡片刷成浅蓝底 =====
  // 用户 2026-11-18：把「新机制 / 发射台 / 平台币 / 第一个 / agentic…」这类叙事挑出来，
  // 对应卡片底色变蓝，一眼看出哪个值得先看。
  // **底色必须浅**（原话：「不能太深，不然看不到数据和文字」）——只用 12% 透明蓝 +
  // 一圈 1px 内描边，绝不改文字颜色，也不动布局。
  const HOT_STRONG = [
    '新模式', '新机制', '新玩法', '新叙事', '新范式', '新物种',
    '第一个', '首个', '首家', '首例', '首款', '首创', '首发', '首次',
    '地推', '平台币', '平台原生', '发射台', '发射平台', 'launchpad', 'launch pad',
    'bonding curve', 'agentic',
    // AI 身份 / 新机制（2026-11-19：AGENT 那条叙事里「币安 Agent ID」一点都没命中）
    'agent id', 'ai agent', 'ai 身份', '智能体', '灵魂绑定', 'soulbound',
    // 用户 2026-11-19：「关键词再加一个『代理』『agent』」
    // 注意 `agent` 是子串匹配，会同时吃掉 `agentic` / `agent id` / 币名里的 AGENT ——
    // 也就是说「叙事里只要提到 agent」就标，这是用户明确要的召回优先。
    '代理', 'agent', 'ai',
  ];
  const HOT_WEAK = ['新', '第一'];

  // **AI 相关**的一组词：命中它们时卡片走白色标记（用户 2026-11-19）。
  // 判定按小写比，所以这里写小写即可。
  const HOT_AI = new Set([
    'agent', 'agent id', 'ai agent', 'ai', 'ai 身份', '智能体', '代理', 'agentic',
  ]);

  // 标色（用户 2026-11-18 三次校准后的定案）：
  //   1) 「太浅了，深一点」「太浅了，要用亮蓝做浅」→ 不能只靠淡淡一层底色；
  //   2) 「用类似 deepseek logo 的那种蓝」→ 定色 #4D6BFE（rgb 77,107,254），
  //      比 sky-400 深、比宝蓝亮，深色战壕卡片上 30% 就足够醒目。
  // 手法：底色填充 + **2px 同色内描边 + 内发光**，四档只调填充浓度，描边恒亮，
  // 这样任何卡片底色下都看得出来（战壕卡片内层是透明 div，之前就是被它坑了）。
  const HOT_RGB = '77,107,254';     // #4D6BFE —— DeepSeek logo 那个蓝
  const HOT_RING = '#4d6bfe';
  const HOT_ALPHA_STEPS = [0.18, 0.30, 0.42, 0.55];
  let hotAlpha = Number(GM_getValue('hotAlpha', 0.30));
  if (!HOT_ALPHA_STEPS.includes(hotAlpha)) hotAlpha = 0.30;

  function hotCss() {
    const a = hotAlpha;
    const hover = Math.min(0.75, a + 0.10);
    // AI 系用**白色**（用户 2026-11-19）：
    // 不能像蓝色那样给 30% 填充 —— 白底会把浅色文字洗掉，所以白色只做
    // 「10~16% 极淡填充 + 2px 纯白描边 + 白色内发光」，看起来是一圈亮白框。
    const wFill = Math.min(0.20, Math.max(0.08, a * 0.4));
    const wHover = Math.min(0.30, wFill + 0.06);
    return [
      '.gigax-hot-card{',
      `  background-color:rgba(${HOT_RGB},${a})!important;`,
      `  box-shadow:inset 0 0 0 2px ${HOT_RING}, inset 0 0 38px rgba(${HOT_RGB},.40)!important;`,
      '}',
      `.gigax-hot-card:hover{background-color:rgba(${HOT_RGB},${hover})!important;}`,
      '.gigax-ai-card{',
      `  background-color:rgba(255,255,255,${wFill.toFixed(2)})!important;`,
      '  box-shadow:inset 0 0 0 2px #ffffff, inset 0 0 34px rgba(255,255,255,.38)!important;',
      '}',
      `.gigax-ai-card:hover{background-color:rgba(255,255,255,${wHover.toFixed(2)})!important;}`,
    ].join('\n');
  }

  function applyHotCss() {
    let st = document.getElementById('gigax-hot-style');
    if (!st) {
      st = document.createElement('style');
      st.id = 'gigax-hot-style';
      (document.head || document.documentElement).appendChild(st);
    }
    st.textContent = hotCss();
  }
  applyHotCss();
  // 注意：这两条菜单只能注册在声明之后（let 有 TDZ，放文件顶部会 ReferenceError）
  GM_registerMenuCommand(`标记深浅：${Math.round(hotAlpha * 100)}% → 点一下换下一档`, () => {
    const i = HOT_ALPHA_STEPS.indexOf(hotAlpha);
    hotAlpha = HOT_ALPHA_STEPS[(i + 1) % HOT_ALPHA_STEPS.length];
    GM_setValue('hotAlpha', hotAlpha);
    applyHotCss();
    log('标记底色透明度：' + Math.round(hotAlpha * 100) + '%');
  });

  // 命中判定：**任意一个词命中就算**（强词、弱词同权）。
  // 用户 2026-11-19：「新和第一要的，弱词单个也行」—— 之前弱词要同时命中 2 个
  // （`新` + `第一`）才生效，太严。
  // 代价要清楚：GIGA X 的文案里「新」几乎无处不在，单个「新」就标 → 一屏会蓝掉一大片。
  // 纯 ASCII 且只有 1~2 个字母的词（`AI`）必须按「整词」匹配：
  // `chairman` 里含 `ai`、`main`/`claim`/`email` 也都含 —— 用 includes 会全算命中，
  // 实测那条 AGENT 叙事里就有 `CHAIRMAN Z`。所以这类短词走字母边界正则。
  const SHORT_ASCII_RX = new Map();
  [...HOT_STRONG, ...HOT_WEAK].forEach(w => {
    const low = String(w).toLowerCase();
    if (/^[a-z0-9]{1,2}$/.test(low)) {
      SHORT_ASCII_RX.set(low, new RegExp(`(^|[^a-z0-9])${low}($|[^a-z0-9])`));
    }
  });

  // ===== 自定义关键词（2026-11-19 用户要求）=====
  // 悬浮面板里自己填「要捕获的叙事词」：填了就存进 GM 存储，删了就立刻失效。
  // 命中后**与内置词同权**参与标色（用户：「被捕获的关键词也是用蓝色标记」）——
  // 也就是走 `.gigax-hot-card` 那条蓝底；只有词本身属于 AI 组时才变白。
  const CUSTOM_MAX = 40;        // 最多存几个（面板 chip 太多会滚成一团）
  const CUSTOM_WORD_MAX = 24;   // 单个词最长字符数
  const CUSTOM_SEP = /[,，、;；\s]+/;   // 一次输入多个词的分隔符
  let customKw = GM_getValue('customKw', []);
  if (!Array.isArray(customKw)) customKw = [];
  customKw = customKw
    .filter(w => typeof w === 'string' && w.trim())
    .map(w => w.trim().slice(0, CUSTOM_WORD_MAX));
  let customKwOn = GM_getValue('customKwOn', 'on') !== 'off';

  // 自定义词是运行时才知道的，短 ASCII 词（`id`/`xy`）的整词边界正则只能按需建；
  // 内置词那几个已经在上面预编译过，这里直接复用同一个 Map。
  function shortRxFor(p) {
    if (!/^[a-z0-9]{1,2}$/.test(p)) return null;
    let rx = SHORT_ASCII_RX.get(p);
    if (!rx) {
      rx = new RegExp(`(^|[^a-z0-9])${p}($|[^a-z0-9])`);
      SHORT_ASCII_RX.set(p, rx);
    }
    return rx;
  }

  // **英文一律不区分大小写**：GMGN/GIGA X 的文案里 `Launchpad`、`Agent ID`、`Agentic`
  // 首字母大写的写法很常见，以前 `text.includes('launchpad')` 直接漏掉（2026-11-19）。
  function matchHot(text) {
    if (!text) return [];
    const low = String(text).toLowerCase();
    const has = (w) => {
      const p = String(w).toLowerCase();
      const rx = shortRxFor(p);
      return rx ? rx.test(low) : low.includes(p);
    };
    const strong = HOT_STRONG.filter(has);
    // '新模式' 已经算强词，别再把它里面的 '新' 重复算一个弱词
    const weak = HOT_WEAK.filter(w => has(w) && !strong.some(s => s.includes(w)));
    // 自定义词：关掉开关就当它不存在（内置词不受影响）
    const custom = customKwOn ? customKw.filter(has) : [];
    if (!strong.length && !weak.length && !custom.length) return [];
    return [...new Set(strong.concat(weak).concat(custom))];
  }

  let markedCards = [];
  let paintedAt = 0;
  let lastPaintSig = '';     // 上一轮的标记签名（只用于「标记变了才上报」）
  let lastPaintAt = 0;
  let paintTimer = null;
  const hotLogged = new Set();

  // 排一次重刷。**绝不能把请求丢掉**：老写法是「800ms 内再来一次就 return」，
  // 而 React 每次价格跳动都会把卡片节点整个换新、class 全没；如果那一轮重刷正好被防抖吃掉，
  // 这次请求就永久丢了 —— 表现就是「刚刚还标着，一会又没了」（用户 2026-11-19）。
  // 现在改成：合并请求 + 400ms 地板间隔 + 到点**强制执行**一次真实重刷。
  const PAINT_MIN_GAP = 400;
  function schedulePaint(delay) {
    if (paintTimer) return;
    const wait = Math.max(Number(delay) || 0, PAINT_MIN_GAP - (Date.now() - paintedAt), 0);
    paintTimer = setTimeout(() => {
      paintTimer = null;
      paintHotCards(true);
    }, wait);
  }

  // 卡片 = 从代币链接往上爬，爬到「里面只有这一个币」的最大那层。
  // 关键：**优先取最外层「有背景色/背景图」的祖先** —— 战壕榜（trench）那种卡片
  // 里面套了好几层透明 div，只刷最里面那层根本看不见（用户 2026-11-18 反馈）。
  // 但「往上爬」必须有硬边界：GMGN 列表页一行一个币、几十行叠在一个有背景的
  // 滚动容器里，一旦爬到那个容器就会把**整屏列表**染蓝（2026-11-18 截图事故）。
  // 所以两条铁律一起上：
  //   a) 祖先里出现**第二个币的 CA**（任何形式：链接/data-address/title/正文里的
  //      0x 地址）就停 —— 那是列表容器，不是单张卡片；
  //   b) 祖先尺寸超过「单张卡片」上限（过高/过宽）也停 —— 兜底挡住全屏列表。
  function isPainted(el) {
    try {
      const cs = getComputedStyle(el);
      const bg = String(cs.backgroundColor || '');
      const hasBg = bg && bg !== 'transparent' && !/rgba\(0,\s*0,\s*0,\s*0\)/.test(bg);
      const hasImg = String(cs.backgroundImage || 'none') !== 'none';
      if (hasBg || hasImg) return true;
      // 战壕面板的卡片是「透明底 + 1px 边框」的画法，只认背景色会一路退到内层的名字行，
      // 看起来就像只亮了一小块 —— 有可见边框也算「这是用户眼里的一个盒子」。
      const bw = (parseFloat(cs.borderTopWidth) || 0) + (parseFloat(cs.borderBottomWidth) || 0);
      const bcol = String(cs.borderTopColor || '');
      const noCol = !bcol || /rgba\(0,\s*0,\s*0,\s*0\)/.test(bcol);
      return bw >= 1 && cs.borderTopStyle !== 'none' && !noCol;
    } catch (e) { return false; }
  }

  // 一个「单张代币卡片」的合理上限：GMGN 列表行再高也就 100px 上下，宽不会吃满视口。
  // 超过这个尺度基本就是「整块列表 / 整个面板」，绝不该整块染色。
  const CARD_MAX_H = 240;   // px
  const CARD_MAX_W_RATIO = 0.92; // 相对视口宽度

  // 「第二个币」的判定**只看指向代币详情页的链接**（`/<chain>/token/<CA>` 这类），
  // 不能看 innerText 里的所有 0x 地址 —— GMGN 列表行里合约地址、钱包地址、交易哈希
  // 都是 0x…40，把钱包地址也当成「另一个币」会误判，导致正常该标的行标不上
  // （IDRENT「之前有、现在没了」就是上一版误杀，2026-11-18）。
  function distinctTokenCasIn(el, known) {
    const seen = new Set(known || []);
    try {
      // 只认 `/<chain>/token/<CA>` 这种详情页链接：卡片里链到区块浏览器 /address/0x…
      // 的地址也会被 parseUrlToken 认成代币，那会凭空多出一个「第二个币」把标染拦掉。
      el.querySelectorAll('a[href*="/token/"]').forEach(a => {
        const q = parseUrlToken(a.getAttribute('href'));
        if (q && q.ca) seen.add(q.ca);
      });
    } catch (e) { /* ignore */ }
    return seen;
  }

  function cardFromAnchor(a, ca) {
    // 短缓存：同一个 CA 的卡片定位结果复用（React 重渲染前 anchor 还是同一批）。
    // 命中时校验节点仍连着 DOM，失效（React 换了节点）就重算。这砍掉了
    // 「每次 paint 都对同一批卡片逐祖先读 getComputedStyle」的重复开销。
    // 缓存里那条如果落在**持仓面板**，别用它：同一个币在行情榜里还有一张正经卡片，
    // 拿持仓行顶替会让榜上那张永远标不上（2026-11-19 加持仓豁免时的连带问题）。
    const hit = cardCache.get(ca);
    if (hit && hit.isConnected && !inHoldingsPanel(hit)) return hit;

    let painted = null;   // 最外层「有背景」的祖先 = 用户眼里的那张卡片
    let sized = null;     // 没有背景时的兜底（表格行常见）
    const vw = (document.documentElement && document.documentElement.clientWidth) || window.innerWidth || 1280;
    const maxW = vw * CARD_MAX_W_RATIO;
    let cur = a;
    for (let i = 0; i < 10 && cur; i++) {
      const p = cur.parentElement;
      if (!p || p === document.body) break;

      // a) 这一层里出现太多「代币链接」就停：那是列表/网格容器，不是单张卡片。
      //    门槛用 CARD_MAX_CAS（=2）而不是 1：战壕卡片自身常常就带两个币（配对/相关币）。
      const cas = distinctTokenCasIn(p, [ca]);
      if (cas.size > CARD_MAX_CAS) break;

      const r = p.getBoundingClientRect();
      // b) 尺寸超出一张卡片该有的尺度，停：整块列表 / 面板不染。
      if (r.height > CARD_MAX_H || r.width > maxW) break;

      if (r.width >= 140 && r.height >= 24) {
        if (!sized) sized = p;
        if (isPainted(p)) painted = p;
      }
      cur = p;
    }
    const result = painted || sized;
    if (result && !inHoldingsPanel(result)) cardCache.set(ca, result);
    return result;
  }
  // CA → 已定位的卡片节点（WeakRef 不行就普通 Map，配合 isConnected 校验防失效）。
  const cardCache = new Map();

  function clearMarks() {
    for (const el of markedCards) {
      try {
        el.classList.remove('gigax-hot-card');
        el.classList.remove('gigax-ai-card');
        const t = el.dataset.gigaxTitle;
        if (t != null) {
          if (t) el.setAttribute('title', t); else el.removeAttribute('title');
          delete el.dataset.gigaxTitle;
        }
        delete el.dataset.gigaxCa;
      } catch (e) { /* 节点可能已被 React 删掉 */ }
    }
    markedCards = [];
  }

  // 标一张卡片。`data-gigax-ca` 是给虚拟列表准备的：GMGN 的 recycler 会把**同一个 DOM
  // 节点**复用给另一个币，只在节点上挂 class 的话，节点换了币蓝底还留着。
  // 持仓/仓位面板**不标**（2026-11-19 用户：「这个持仓位置的不用标记」）。
  // 那是「你自己买了什么」，不是「市场有什么机会」；而且那几行里全是盈亏数字，
  // 蓝底一压更难读。判据只看**结构**，不看币种：祖先里有持仓表专有的列名就算。
  // 「当前买入 / 当前卖出 / 未实现」这三组词在行情榜/战壕榜里不会同时出现。
  const HOLDING_MARKERS = ['当前买入', '当前卖出', '未实现', '我的BSC链持仓', '我的SOL链持仓'];
  function inHoldingsPanel(el) {
    try {
      // ① 持仓面板是**表格**（币种/当前买入/当前卖出/余额/未实现 是列头）：
      //    只看「自己所在的这张表」，绝不往上层容器爬 —— 爬上去会把行情榜和持仓一起圈进来。
      const tbl = el.closest && el.closest('table');
      if (tbl) {
        // 表格里就不用再往上爬了：一张表就是一个整体，答案只有「是/不是」
        const t = tbl.textContent || '';
        return t.length <= 60000 && HOLDING_MARKERS.some(m => t.includes(m));
      }
      // ② 万一它是 div 列表：只在「小」的祖先里找（高度 ≤320、宽度 ≤90% 视口），
      //    一旦爬到面板/整页尺度就放弃，免得把别的榜也豁免掉。
      let cur = el;
      const maxW = (window.innerWidth || 1280) * 0.9;
      for (let i = 0; i < 4 && cur && cur !== document.body; i++) {
        const r = cur.getBoundingClientRect ? cur.getBoundingClientRect() : null;
        if (r && (r.height > 320 || r.width > maxW)) break;
        const t = cur.textContent || '';
        if (t.length <= 1200 && HOLDING_MARKERS.some(m => t.includes(m))) return true;
        cur = cur.parentElement;
      }
    } catch (e) { /* ignore */ }
    return false;
  }

  function paintOne(card, ca, hits) {
    if (inHoldingsPanel(card)) return;   // 持仓行即使命中也不染
    // AI 类关键词单独一档皮肤（白色描边）；同时命中两类时以 AI 为准（更具体）。
    const ai = hits.some(w => HOT_AI.has(String(w).toLowerCase()));
    card.classList.add(ai ? 'gigax-ai-card' : 'gigax-hot-card');
    try { card.dataset.gigaxCa = ca; } catch (e) { /* ignore */ }
    // 悬停卡片就能看到为什么标蓝（不覆盖 GMGN 自己的 title）
    if (card.dataset.gigaxTitle == null) card.dataset.gigaxTitle = card.getAttribute('title') || '';
    card.setAttribute('title', `GIGA X 叙事命中${ai ? '（AI）' : ''}：${hits.join('、')}`);
    markedCards.push(card);
  }

  // 记住「标过的币 + 命中词」，重开页面/翻页时不用等网络就先把卡片标上
  // （只存标记结果，不存正文，体积可控）。TTL 给 24 小时：用户「之前标过、隔几小时
  // 回来没了」就是因为 2 小时太短，标记结果本身是缓存的「机会结论」，跨天仍有参考价值。
  const HOT_INDEX_TTL = 24 * 3600 * 1000;
  let hotIndex = GM_getValue('hotIndex', {});
  if (!hotIndex || typeof hotIndex !== 'object') hotIndex = {};
  let hotSaveTimer = null;

  function rememberHot(ca, hits) {
    if (!ca || !hits || !hits.length) return;
    const word = hits.join('、');
    const prev = hotIndex[ca];
    if (prev && prev.h === word) return;
    hotIndex[ca] = { h: word, ts: Date.now() };
    scheduleHotSave();
  }

  // 当前正文明确不命中 → 立刻删掉这个币的历史结论，别等 24h 过期
  //（`0x70c3…7777` 慈善CZ 就是靠这条修掉的，2026-11-19）
  function dropHot(ca) {
    if (!ca || !hotIndex[ca]) return;
    delete hotIndex[ca];
    scheduleHotSave();
  }

  // 这一轮标记里，哪些币是「上次标过、这次没拿到正文」补位出来的（只用于诊断上报）
  const hotFromIndex = new Set();

  function scheduleHotSave() {
    if (hotSaveTimer) return;
    hotSaveTimer = setTimeout(() => {
      hotSaveTimer = null;
      const now = Date.now();
      const out = {};
      let n = 0;
      for (const k of Object.keys(hotIndex)) {
        const v = hotIndex[k];
        if (!v || now - v.ts > HOT_INDEX_TTL) continue;
        out[k] = v;
        if (++n >= 500) break;
      }
      hotIndex = out;
      try { GM_setValue('hotIndex', out); } catch (e) { /* 配额满了就算了 */ }
    }, 2000);
  }

  function paintHotCards(force) {
    if (!force && Date.now() - paintedAt < 800) return;   // 防抖：React 会反复冲掉 class，但 800ms 内重刷足够且不压主线程
    paintedAt = Date.now();
    clearMarks();
    hotFromIndex.clear();
    if (hotMode === 'off' || !enabled) return;

    // 先按缓存里的正文判定，再用「上次标过的」补位（顺序覆盖：新结果优先）
    // **铁律：当前正文已经取到的币，以当前正文为准 —— 历史结论不许翻案**（2026-11-19）。
    // 老写法里「不命中」走的是 continue，币因此没进 marks，紧接着被 hotIndex 补位标回去：
    // 用户看到的「这段叙事根本没有关键词，怎么就被标了」
    // （实锤：`0x70c3…7777` 慈善CZ，桥接正文 110 字，强词弱词各 0 命中，卡片却是蓝的）——
    // 因为 hotIndex 记的是**上一版正文**的命中，而 GIGA X 的叙事会重写，TTL 还有 24 小时。
    const marks = new Map();
    const known = new Set();     // 这一轮正文已经拿到手的币（含「拿到了但不命中」）
    for (const [key, entry] of cache) {
      const ca = key.slice(key.indexOf(':') + 1);
      if (!entry || !entry.text) {
        // 桥接**明确**说「没有这个币的叙事」时，这本身就是答案：没有正文 → 不可能命中，
        // 不许再拿历史结论补位（`0xe0bcf5d8…` 就是这种币：bsc/base/eth/robinhood/elysium
        // 全链都返回 no-narrative，浮层显示的是 GMGN 原生文案，卡片却被旧结论染蓝）。
        // 只有「不知道」（断网 / 超时 / 还没请求）才继续交给 hotIndex 兜。
        if (entry && entry.reason === 'no-narrative') known.add(ca);
        continue;
      }
      known.add(ca);
      const hits = matchHot(entry.text);
      if (!hits.length) { dropHot(ca); continue; }   // 顺手把过期的历史结论清掉
      marks.set(ca, hits);
      rememberHot(ca, hits);
    }
    const now = Date.now();
    for (const ca of Object.keys(hotIndex)) {
      if (marks.has(ca) || known.has(ca)) continue;
      const v = hotIndex[ca];
      if (!v || !v.h || now - v.ts > HOT_INDEX_TTL) continue;
      marks.set(ca, v.h.split('、'));
      hotFromIndex.add(ca);
    }

    // 一次性全页收集 token 链接 → 建 ca→anchor[] 索引，避免每个币各扫一次全页
    // （之前 N 个币各 querySelectorAll 一次 = O(N×M)，现在一次 = O(M)）。
    const anchorIndex = new Map();
    try {
      document.querySelectorAll('a[href*="/token/"]').forEach(a => {
        const p = parseUrlToken(a.getAttribute('href'));
        if (!p || !p.ca) return;
        if (!anchorIndex.has(p.ca)) anchorIndex.set(p.ca, []);
        anchorIndex.get(p.ca).push(a);
      });
    } catch (e) { /* ignore */ }

    // 数据属性索引：**战壕卡只能靠这条**（实测卡里 0~2 个 token 链接，币名是纯文本）。
    // 属性所在节点就是那张卡，不用再爬祖先，也不会有「取到隔壁卡」的可能。
    const dataIndex = new Map();
    try {
      document.querySelectorAll(CA_DATA_SEL).forEach(node => {
        const hit = caFromDataAttrs(node);
        if (!hit) return;
        if (!dataIndex.has(hit.ca)) dataIndex.set(hit.ca, []);
        const arr = dataIndex.get(hit.ca);
        if (arr.indexOf(hit.el) < 0) arr.push(hit.el);
      });
    } catch (e) { /* ignore */ }

    const seen = new Set();
    const painted = [];
    for (const [ca, hits] of marks) {
      const cards = [];
      const dataEls = dataIndex.get(ca);
      if (dataEls) for (const el of dataEls) cards.push(el);
      const anchors = anchorIndex.get(ca);
      if (anchors) {
        for (const a of anchors) {
          const card = cardFromAnchor(a, ca);
          if (card) cards.push(card);
        }
      }
      for (const card of cards) {
        if (!card || seen.has(card)) continue;
        seen.add(card);
        paintOne(card, ca, hits);
        painted.push(ca.slice(0, 8) + '=' + hits.join('/') + (hotFromIndex.has(ca) ? '[旧]' : ''));
      }
      if (!hotLogged.has(ca)) {
        hotLogged.add(ca);
        log('机会标记', ca, '|', hits.join('、'), hotFromIndex.has(ca) ? '（上次标过）' : '');
      }
    }
    // 上报这一屏到底标了谁、凭哪个词、是当前正文还是历史结论 —— 用户问「为什么这个被标了」
    // 时不用再猜（2026-11-19）。只在这一轮的结果变了才发，最多 10 秒一次。
    const sig = painted.slice(0, 12).join(',');
    if (sig !== lastPaintSig && Date.now() - lastPaintAt > 10000) {
      lastPaintSig = sig;
      lastPaintAt = Date.now();
      try { beacon('paint', { n: String(painted.length), hit: sig.slice(0, 300) }); }
      catch (e) { /* ignore */ }
    }
  }

  // ===== 自定义关键词面板（悬浮 / 可拖拽 / 可折叠）=====
  // 用户 2026-11-19：「能不能再做一个自定义关键词的填写框，然后用一个悬浮可拖拽的开关去控制，
  // 可以填写要捕获的叙事关键词，然后在输入框里填了之后会被保存，也可以通过删除键去删除」，
  // 补一句「被捕获的关键词也是用蓝色标记」。
  // 设计取舍：
  //  · 面板只管「填词 / 删词 / 自定义词的开关」，**内置词表一个字都不动**（深浅与总开关仍在菜单）；
  //  · 拖拽只挂标题栏，pointerdown 立刻 stopPropagation —— GMGN 自己也在监听指针事件，
  //    不拦住的话拖面板会顺带把卡片 hover 起来；
  //  · 面板直接挂 body，不参与 findFloatingRoots / 深扫，所以不会被当成叙事浮层；
  //  · 词存在 GM 存储（`customKw`），页面刷新、换标签页都还在。
  const KW_PANEL_ID = 'gigax-kw-panel';
  const KW_POS_KEY = 'customKwPos';
  let kwHidden = GM_getValue('customKwHidden', false) === true;
  let kwCollapsed = GM_getValue('customKwCollapsed', false) === true;
  let kwPanel = null;
  let kwFlashTimer = null;

  function ensureKwCss() {
    if (document.getElementById('gigax-kw-style')) return;
    const st = document.createElement('style');
    st.id = 'gigax-kw-style';
    st.textContent = `
#gigax-kw-panel{position:fixed;right:18px;bottom:92px;width:252px;z-index:2147483002;
  background:rgba(18,22,32,.97)!important;border:1px solid rgba(122,150,220,.30);border-radius:12px;
  box-shadow:0 12px 32px rgba(0,0,0,.52);color:#e6ebf5!important;overflow:hidden;text-align:left;
  font:12px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;}
#gigax-kw-panel *{box-sizing:border-box;font-family:inherit;}
#gigax-kw-panel.kw-collapsed .kw-body{display:none;}
#gigax-kw-panel .kw-head{display:flex;align-items:center;gap:7px;padding:8px 9px;cursor:grab;
  background:linear-gradient(180deg,rgba(77,107,254,.26),rgba(77,107,254,.06));
  border-bottom:1px solid rgba(122,150,220,.16);}
#gigax-kw-panel.kw-dragging .kw-head{cursor:grabbing;}
#gigax-kw-panel .kw-dot{width:7px;height:7px;border-radius:50%;background:#4d6bfe;flex:0 0 auto;
  box-shadow:0 0 8px rgba(77,107,254,.9);}
#gigax-kw-panel .kw-title{font-size:12.5px;font-weight:600;color:#eef2fb;white-space:nowrap;}
#gigax-kw-panel .kw-count{margin-left:auto;font-size:10.5px;color:#9fb0d0;min-width:13px;text-align:right;}
#gigax-kw-panel .kw-switch{position:relative;width:34px;height:18px;border-radius:9px;border:0;padding:0;
  cursor:pointer;flex:0 0 auto;background:#4d6bfe;transition:background .15s;}
#gigax-kw-panel .kw-switch[aria-checked="false"]{background:rgba(255,255,255,.20);}
#gigax-kw-panel .kw-switch>span{position:absolute;top:2px;left:18px;width:14px;height:14px;
  border-radius:50%;background:#fff;transition:left .15s;}
#gigax-kw-panel .kw-switch[aria-checked="false"]>span{left:2px;}
#gigax-kw-panel .kw-min{border:0;background:rgba(255,255,255,.10);color:#cfd8ea;width:18px;height:18px;
  border-radius:5px;line-height:1;cursor:pointer;font-size:12px;padding:0;flex:0 0 auto;}
#gigax-kw-panel .kw-min:hover{background:rgba(255,255,255,.20);}
#gigax-kw-panel .kw-body{padding:9px 9px 6px;}
#gigax-kw-panel .kw-input-row{display:flex;gap:6px;}
#gigax-kw-panel .kw-input{flex:1 1 auto;min-width:0;height:28px;background:rgba(255,255,255,.07)!important;
  border:1px solid rgba(255,255,255,.16);color:#eef2fb!important;border-radius:8px;padding:0 8px;
  font-size:12px;outline:none;}
#gigax-kw-panel .kw-input::placeholder{color:#8494b0;}
#gigax-kw-panel .kw-input:focus{border-color:#4d6bfe;box-shadow:0 0 0 2px rgba(77,107,254,.30);}
#gigax-kw-panel .kw-add{border:0;background:#4d6bfe;color:#fff;border-radius:8px;padding:0 10px;
  height:28px;cursor:pointer;font-size:12px;flex:0 0 auto;}
#gigax-kw-panel .kw-add:hover{background:#5d79ff;}
#gigax-kw-panel .kw-chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:7px;max-height:126px;
  overflow:auto;}
#gigax-kw-panel .kw-chip{display:inline-flex;align-items:center;gap:3px;max-width:100%;
  background:rgba(77,107,254,.24);border:1px solid rgba(77,107,254,.55);color:#dfe6ff;
  border-radius:999px;padding:1px 3px 1px 8px;font-size:11.5px;line-height:1.6;outline:none;}
#gigax-kw-panel .kw-chip:focus{box-shadow:0 0 0 2px rgba(77,107,254,.55);}
#gigax-kw-panel .kw-chip.off{opacity:.45;}
#gigax-kw-panel .kw-chip>b{font-weight:500;max-width:152px;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap;}
#gigax-kw-panel .kw-x{border:0;background:transparent;color:#c8d4ff;cursor:pointer;font-size:13px;
  line-height:1;padding:0 3px;border-radius:50%;}
#gigax-kw-panel .kw-x:hover{background:rgba(255,255,255,.16);color:#fff;}
#gigax-kw-panel .kw-empty{color:#7d8ca8;font-size:11px;padding:2px;}
#gigax-kw-panel .kw-hint{margin-top:6px;color:#8291ad;font-size:10.5px;line-height:1.45;}
#gigax-kw-panel .kw-flash{margin-top:2px;color:#8ad9a4;font-size:10.5px;height:14px;opacity:0;
  transition:opacity .2s;}
`;
    (document.head || document.documentElement).appendChild(st);
  }

  function kwFlash(msg) {
    if (!kwPanel) return;
    const el = kwPanel.querySelector('.kw-flash');
    if (!el) return;
    el.textContent = msg || '';
    el.style.opacity = msg ? '1' : '0';
    if (kwFlashTimer) clearTimeout(kwFlashTimer);
    if (msg) kwFlashTimer = setTimeout(() => { el.style.opacity = '0'; }, 1800);
  }

  function renderKwChips() {
    if (!kwPanel) return;
    const box = kwPanel.querySelector('.kw-chips');
    const cnt = kwPanel.querySelector('.kw-count');
    if (!box) return;
    box.textContent = '';
    if (!customKw.length) {
      const e = document.createElement('span');
      e.className = 'kw-empty';
      e.textContent = '还没有自定义词';
      box.appendChild(e);
    }
    customKw.forEach((w, i) => {
      const chip = document.createElement('span');
      chip.className = 'kw-chip' + (customKwOn ? '' : ' off');
      chip.tabIndex = 0;
      chip.title = `命中「${w}」的叙事会标蓝 · 点 × 或按 Delete 删除`;
      const b = document.createElement('b');
      b.textContent = w;
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'kw-x';
      x.textContent = '×';
      x.title = '删除这个关键词';
      x.addEventListener('click', ev => { ev.stopPropagation(); removeCustomKw(i); });
      chip.appendChild(b);
      chip.appendChild(x);
      // 「删除键」也支持：聚焦到某个词上按 Delete / Backspace 就删它
      chip.addEventListener('keydown', ev => {
        if (ev.key === 'Delete' || ev.key === 'Backspace') {
          ev.preventDefault();
          removeCustomKw(i);
        }
      });
      box.appendChild(chip);
    });
    if (cnt) cnt.textContent = String(customKw.length);
  }

  // 词表变了：落盘 + 重绘 + 立刻按新词重判一次（正文都在缓存里，不用重新请求桥接）
  function kwSync() {
    try { GM_setValue('customKw', customKw); } catch (e) { /* 配额满了就算了 */ }
    renderKwChips();
    try { paintHotCards(true); } catch (e) { /* 面板不该因为标色出错而挂掉 */ }
  }

  function addCustomWords(raw) {
    const parts = String(raw || '').split(CUSTOM_SEP).map(s => s.trim()).filter(Boolean);
    if (!parts.length) return 0;
    let added = 0, dup = 0, full = 0;
    for (const p of parts) {
      const w = p.slice(0, CUSTOM_WORD_MAX);
      if (customKw.some(x => x.toLowerCase() === w.toLowerCase())) { dup++; continue; }
      if (customKw.length >= CUSTOM_MAX) { full++; break; }
      customKw.push(w);
      added++;
    }
    if (added) kwSync();
    if (full) kwFlash(`最多 ${CUSTOM_MAX} 个词`);
    else if (!added && dup) kwFlash('这些词已经在列表里了');
    else if (added) kwFlash(`已添加 ${added} 个`);
    return added;
  }

  function removeCustomKw(i) {
    if (i < 0 || i >= customKw.length) return;
    const w = customKw.splice(i, 1)[0];
    kwSync();
    kwFlash(`已删除「${w}」`);
  }

  function buildKwPanel() {
    ensureKwCss();
    const p = document.createElement('div');
    p.id = KW_PANEL_ID;
    if (kwCollapsed) p.classList.add('kw-collapsed');
    const pos = GM_getValue(KW_POS_KEY, null);
    if (pos && typeof pos.x === 'number' && typeof pos.y === 'number' && isFinite(pos.x) && isFinite(pos.y)) {
      p.style.left = Math.max(0, pos.x) + 'px';
      p.style.top = Math.max(0, pos.y) + 'px';
      p.style.right = 'auto';
      p.style.bottom = 'auto';
    }

    const head = document.createElement('div');
    head.className = 'kw-head';
    const dot = document.createElement('span');
    dot.className = 'kw-dot';
    const title = document.createElement('span');
    title.className = 'kw-title';
    title.textContent = '叙事关键词';
    const cnt = document.createElement('span');
    cnt.className = 'kw-count';
    cnt.textContent = String(customKw.length);

    const sw = document.createElement('button');
    sw.type = 'button';
    sw.className = 'kw-switch';
    sw.setAttribute('role', 'switch');
    sw.setAttribute('aria-checked', customKwOn ? 'true' : 'false');
    sw.title = '自定义关键词开关（关掉后只有内置词生效）';
    sw.appendChild(document.createElement('span'));
    sw.addEventListener('click', ev => {
      ev.stopPropagation();
      customKwOn = !customKwOn;
      sw.setAttribute('aria-checked', customKwOn ? 'true' : 'false');
      try { GM_setValue('customKwOn', customKwOn ? 'on' : 'off'); } catch (e) { /* ignore */ }
      renderKwChips();
      try { paintHotCards(true); } catch (e) { /* ignore */ }
      kwFlash(customKwOn ? '自定义词已开启' : '自定义词已关闭（内置词照常）');
    });

    const min = document.createElement('button');
    min.type = 'button';
    min.className = 'kw-min';
    min.textContent = kwCollapsed ? '+' : '–';
    min.title = '折叠 / 展开';
    min.addEventListener('click', ev => {
      ev.stopPropagation();
      kwCollapsed = !kwCollapsed;
      p.classList.toggle('kw-collapsed', kwCollapsed);
      min.textContent = kwCollapsed ? '+' : '–';
      try { GM_setValue('customKwCollapsed', kwCollapsed); } catch (e) { /* ignore */ }
    });

    head.appendChild(dot);
    head.appendChild(title);
    head.appendChild(cnt);
    head.appendChild(sw);
    head.appendChild(min);

    const body = document.createElement('div');
    body.className = 'kw-body';
    const row = document.createElement('div');
    row.className = 'kw-input-row';
    const input = document.createElement('input');
    input.className = 'kw-input';
    input.type = 'text';
    input.spellcheck = false;
    input.maxLength = 60;
    input.placeholder = '输入关键词，回车添加';
    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'kw-add';
    add.textContent = '加';
    add.addEventListener('click', () => {
      if (addCustomWords(input.value)) input.value = '';
      input.focus();
    });
    row.appendChild(input);
    row.appendChild(add);

    // 键盘：Enter 添加；输入框空着时 Backspace/Delete 删最后一个（用户要的「删除键」）
    input.addEventListener('keydown', ev => {
      if (ev.isComposing || ev.keyCode === 229) return;   // 中文输入法选词时的回车不能算提交
      if (ev.key === 'Enter') {
        ev.preventDefault();
        if (!input.value.trim()) return;
        if (addCustomWords(input.value)) input.value = '';
        return;
      }
      if ((ev.key === 'Backspace' || ev.key === 'Delete') && !input.value) {
        if (!customKw.length) return;
        ev.preventDefault();
        removeCustomKw(customKw.length - 1);
        return;
      }
      if (ev.key === 'Escape') input.blur();
    });

    const chips = document.createElement('div');
    chips.className = 'kw-chips';
    const hint = document.createElement('div');
    hint.className = 'kw-hint';
    hint.textContent = '回车添加（可逗号分隔多个）· 输入框空着按退格删最后一个 · 点 × 也能删';
    const flash = document.createElement('div');
    flash.className = 'kw-flash';
    body.appendChild(row);
    body.appendChild(chips);
    body.appendChild(hint);
    body.appendChild(flash);
    p.appendChild(head);
    p.appendChild(body);

    // 拖拽：只认标题栏；pointer capture 保证拖出面板范围也跟手
    let drag = null;
    head.addEventListener('pointerdown', ev => {
      if (ev.button !== 0) return;
      if (sw.contains(ev.target) || min.contains(ev.target)) return;
      ev.preventDefault();
      ev.stopPropagation();
      const r = p.getBoundingClientRect();
      drag = { id: ev.pointerId, dx: ev.clientX - r.left, dy: ev.clientY - r.top };
      try { head.setPointerCapture(ev.pointerId); } catch (e) { /* ignore */ }
      p.classList.add('kw-dragging');
    });
    head.addEventListener('pointermove', ev => {
      if (!drag || ev.pointerId !== drag.id) return;
      ev.stopPropagation();
      const vw = window.innerWidth || 1280;
      const vh = window.innerHeight || 800;
      const x = Math.max(0, Math.min(vw - 70, ev.clientX - drag.dx));
      const y = Math.max(0, Math.min(vh - 30, ev.clientY - drag.dy));
      p.style.left = x + 'px';
      p.style.top = y + 'px';
      p.style.right = 'auto';
      p.style.bottom = 'auto';
    });
    const endDrag = () => {
      if (!drag) return;
      drag = null;
      p.classList.remove('kw-dragging');
      try {
        const r = p.getBoundingClientRect();
        GM_setValue(KW_POS_KEY, { x: Math.round(r.left), y: Math.round(r.top) });
      } catch (e) { /* ignore */ }
    };
    head.addEventListener('pointerup', endDrag);
    head.addEventListener('pointercancel', endDrag);

    return p;
  }

  function ensureKwPanel() {
    if (kwHidden) {
      if (kwPanel) kwPanel.style.display = 'none';
      return;
    }
    if (!document.body) return;
    if (!kwPanel || !kwPanel.isConnected) {
      kwPanel = buildKwPanel();
      document.body.appendChild(kwPanel);
      renderKwChips();
    }
    kwPanel.style.display = '';
  }

  function setKwHidden(hide) {
    kwHidden = !!hide;
    try { GM_setValue('customKwHidden', kwHidden); } catch (e) { /* ignore */ }
    if (!kwHidden) ensureKwPanel();
    else if (kwPanel) kwPanel.style.display = 'none';
  }

  GM_registerMenuCommand(kwHidden ? '显示「叙事关键词」面板' : '隐藏「叙事关键词」面板', () => {
    setKwHidden(!kwHidden);
  });
  GM_registerMenuCommand('清空自定义关键词', () => {
    if (!customKw.length) { kwFlash('列表本来就是空的'); return; }
    if (!confirm(`确定清空这 ${customKw.length} 个自定义关键词？`)) return;
    customKw = [];
    kwSync();
    kwFlash('已清空');
  });

  // ===== 批量取叙事（一屏一个请求）=====
  // 单个币一个请求、串着发，一屏 20 个要十几秒；改成一次打包，由桥接并发去问 GIGA X。
  // items = [{chain, ca}]；回调参数是「拿到正文的个数」「其中是不是真的出错」
  //（`no-narrative` 不算出错：GIGA X 只是还没生成，别按「撞墙」冷 2 分钟）
  function fetchBatch(items, cb) {
    if (!items || !items.length) { cb && cb(0, false); return; }
    const q = items.map(it => `${it.chain}:${it.ca}`).join(',');
    const url = `${API}/narratives?items=${encodeURIComponent(q)}&workers=8&v=${encodeURIComponent(SCRIPT_VERSION)}`;
    GM_xmlhttpRequest({
      method: 'GET', url, timeout: 90000,
      onload(res) {
        let d = null;
        try { d = JSON.parse(res.responseText || ''); } catch (e) { /* 下面统一报错 */ }
        if (!d || !d.results) {
          warn('批量返回异常', 'HTTP', res.status, (res.responseText || '').slice(0, 120));
          cb && cb(0, true);
          return;
        }
        let withText = 0;
        let hardFail = 0;
        const lat = [];
        for (const key of Object.keys(d.results)) {
          const p = d.results[key];
          if (!p) continue;
          if (p.ok && p.text) {
            cache.set(key, { text: p.text, ts: Date.now() });
            withText++;
            const t0 = pfSeenAt.get(key);
            if (t0) lat.push(Date.now() - t0);
          } else {
            const reason = p.reason || 'error';
            if (reason !== 'no-narrative') hardFail++;
            let ttl = Math.min(NO_DATA_TTL,
              Math.max(10000, (Number(p.retryAfter) || 0) * 1000 + 2000));
            // 新币：刚出现 10 分钟内的币，GIGA X 多数还没生成叙事 ——
            // 按 2 分钟等太钝（用户「半小时后才被标记」），窗口内 45 秒探一次。
            if (reason === 'no-narrative') {
              const t0 = pfSeenAt.get(key);
              if (t0 && Date.now() - t0 < NEW_COIN_FAST_MS) ttl = Math.min(ttl, NEW_COIN_FAST_TTL);
            }
            cache.set(key, {
              text: '', ts: Date.now(), ttl, reason,
              status: p.status || '',
              retryAfter: Number(p.retryAfter) || 0,
              error: p.error || '',
            });
          }
        }
        log('批量结果：', withText, '/', Object.keys(d.results).length, '有正文',
            '| 服务端命中缓存', d.cached || 0);
        // 「从卡片出现到拿到正文」的真实延迟：用户说慢时看这条，别再靠猜
        if (lat.length) {
          lat.sort((a, b) => a - b);
          const p50 = lat[Math.floor(lat.length / 2)];
          log('正文延迟 p50', p50, 'ms / 最慢', lat[lat.length - 1], 'ms');
          try { beacon('narr', { n: String(withText), p50: String(p50), max: String(lat[lat.length - 1]) }); }
          catch (e) { /* ignore */ }
        }
        schedulePaint(0);
        cb && cb(withText, hardFail > 0);
      },
      onerror() { warn('批量请求连不上桥接'); cb && cb(0, true); },
      ontimeout() { warn('批量请求超时'); cb && cb(0, true); },
    });
  }

  // ===== 自动预取当前屏：不用 hover、不用弹过浮窗，进页面就自己标出来 =====
  // 用户 2026-11-18：「我要的是我没有 hover 它也要自己显示」+「一个币一个币太慢，能不能一批」。
  // 所以这里**按批**发：一次 12 个币打包成一条请求，服务端 8 路并发去问 GIGA X；
  // 允许两条批同时在飞（第一波 12 个先亮，第二波跟上），滚动到新一屏会自动继续。
  const PF_BATCH = 12;           // 一条请求带几个币
  const PF_ROUND_MS = 700;       // 两轮之间至少间隔
  const PF_MAX_FLIGHT = 2;       // 同时最多几条批在飞
  const PF_FAIL_TTL = 2 * 60 * 1000;

  let autoPrefetchOn = GM_getValue('autoPrefetch', true);
  let pfInFlight = 0;
  let pfLastRound = 0;
  const pfPending = new Set();   // 已发出去还没回来的 key（避免下一轮重复发）
  const pfFailed = new Map();    // key -> ts（撞墙的先冷一会儿）
  // key -> 这个币**第一次进入预取视野**的时间。两个用途：
  //  ① 算「从看到卡片到拿到正文」的真实延迟（_diag?ev=narr 上报，用户说「太慢」时不用猜）；
  //  ② 认「新币」：刚出现 10 分钟内的币，没拿到正文时缩短重探间隔。
  const pfSeenAt = new Map();

  // 新币加速：GIGA X 对刚上线的币常常还没生成叙事，原来一等就是 2 分钟
  //（用户 2026-11-19：新币延迟太高，都半小时了才被标）。窗口内改成 45 秒探一次。
  const NEW_COIN_FAST_MS = 10 * 60 * 1000;
  const NEW_COIN_FAST_TTL = 45 * 1000;

  function viewportTargets() {
    // 结果短缓存：同一批锚点的 getBoundingClientRect 结果在 500ms 内复用，
    // 避免 autoPrefetch 每 700ms 就对全页 token 链接重排一次 layout。
    const now = Date.now();
    if (vpCache.at && now - vpCache.at < 500) return vpCache.list;

    const out = [];
    const seen = new Set();
    const de = document.documentElement || document.body || {};
    const vh = de.clientHeight || (typeof innerHeight === 'number' ? innerHeight : 800);
    const vw = de.clientWidth || (typeof innerWidth === 'number' ? innerWidth : 1200);
    const push = (chain, ca, el, top) => {
      if (out.length >= 40 || !ca || seen.has(ca)) return;
      seen.add(ca);
      out.push({ chain, ca, el, top });
      const key = (chain || '') + ':' + ca;
      if (!pfSeenAt.has(key)) {
        pfSeenAt.set(key, now);
        if (pfSeenAt.size > 600) {          // 长期挂着的页面别让它无限长
          const first = pfSeenAt.keys().next();
          if (!first.done) pfSeenAt.delete(first.value);
        }
      }
    };
    for (const a of document.querySelectorAll('a[href*="/token/"]')) {
      if (out.length >= 40) break;
      const p = parseUrlToken(a.getAttribute('href'));
      if (!p || !p.ca) continue;
      const r = a.getBoundingClientRect();
      if (!r.width || r.bottom < -40 || r.top > vh + 40 || r.right < 0 || r.left > vw) continue;
      push(p.chain, p.ca, a, r.top);
    }
    // ② data 属性卡：**战壕格 / 新币格能不能被预取，全靠这一条**。
    // 实测这些卡里 `a[href*="/token/"]` 只有 0~2 个、币名是纯文本 —— 只按链接枚举，
    // 整片格子就永远等不到第一次请求，只能等用户自己去 hover（于是「半小时后才标」）。
    // 属性所在节点就是卡片本身，用它自己的矩形判是否在视野内，不会串到隔壁卡。
    if (out.length < 40) {
      for (const node of document.querySelectorAll(CA_DATA_SEL)) {
        if (out.length >= 40) break;
        const hit = caFromDataAttrs(node);
        if (!hit || !hit.ca) continue;
        const r = node.getBoundingClientRect();
        if (!r.width || !r.height || r.height > CARD_MAX_H) continue;
        if (r.bottom < -40 || r.top > vh + 40 || r.right < 0 || r.left > vw) continue;
        push(hit.chain, hit.ca, node, r.top);
      }
    }
    out.sort((x, y) => x.top - y.top);      // 从上往下取：视野里先看到的先亮
    vpCache = { at: now, list: out };
    return out;
  }
  let vpCache = { at: 0, list: [] };

  function autoPrefetch() {
    if (!autoPrefetchOn || document.hidden) return;
    if (pfInFlight >= PF_MAX_FLIGHT) return;
    const now = Date.now();
    if (now - pfLastRound < PF_ROUND_MS) return;
    pfLastRound = now;

    const items = [];
    const keys = [];
    for (const t of viewportTargets()) {
      if (items.length >= PF_BATCH) break;
      const chain = extractChain(t.el, t);
      const key = chain + ':' + t.ca;
      if (pfPending.has(key)) continue;
      const failedAt = pfFailed.get(key);
      if (failedAt && now - failedAt < PF_FAIL_TTL) continue;
      if (peekCache(chain, t.ca)) continue;
      items.push({ chain, ca: t.ca });
      keys.push(key);
    }
    if (!items.length) return;

    pfInFlight++;
    items.forEach(k => pfPending.add(k));
    log('批量预取', items.length, '个（在飞', pfInFlight, '批）');
    fetchBatch(items, (n, hard) => {
      pfInFlight = Math.max(0, pfInFlight - 1);
      keys.forEach(k => pfPending.delete(k));
      // 只有**真出错**才冷 2 分钟；`no-narrative`（GIGA X 还没生成）交给 cache 的 TTL，
      // 新币窗口内是 45 秒 —— 原来这里一律冷 2 分钟，等于把新币又拖慢一倍。
      if (!n && hard) keys.forEach(k => pfFailed.set(k, Date.now()));
      if (pfFailed.size > 400) {
        const cut = Date.now() - PF_FAIL_TTL;
        for (const [k, ts] of pfFailed) if (ts < cut) pfFailed.delete(k);
      }
      log('本批完成：', n, '/', items.length, '有正文', hard ? '（含错误）' : '');
    });
  }

  // ===== 渲染 / 还原 =====
  // 两条铁律（用户 2026-11-18 反馈）：
  //   1 不能先露出 GMGN 原生的 AI 叙事再跳成 GIGA X 的 —— 所以**先遮住再发请求**；
  //   2 GIGA X 说「没有这个币」时一个字都不要动（不写红字、不清空），保留原文。
  const LOADING_HTML =
    '<span style="color:#8b8b9a;font-style:italic;">GIGA X 叙事加载中…</span>';
  const MAX_ATTEMPTS = 3;      // 真错误（断网/token）的重试次数
  const RETRY_MS = 8000;       // 重试间隔
  const HARD_FAIL_MS = 60000;  // 连着失败后，先放弃一会儿，别再每 700ms 抖一次

  // 记住每个浮层用的是哪个正文节点：React 重渲染后 findTextNode 可能挑到别的节点，
  // 那会把还原的内容塞进错误的地方（出现重复文案）。
  const nodeMap = new WeakMap();
  function nodeOf(root, refresh) {
    if (!refresh) {
      const n = nodeMap.get(root);
      if (n && n.isConnected) return n;
    }
    const n = findTextNode(root);
    if (n) nodeMap.set(root, n);
    return n;
  }

  // 原文快照：只在「这次读到的是 GMGN 的东西」时更新，且取更长的那个
  // （第一次看到时文案可能还没渲染完，别把半截存成原文）
  function saveOrig(root, node) {
    const cur = node.innerHTML || '';
    if (cur.includes('GIGA X')) return;
    const prev = root.dataset.gigaxOrig;
    if (prev == null || cur.length > prev.length) root.dataset.gigaxOrig = cur;
  }

  function maskNative(root) {
    const node = nodeOf(root);
    if (!node) return false;
    if ((node.innerText || '').includes('GIGA X')) return true;   // 已经是我们的内容
    saveOrig(root, node);
    // 记下「这个浮层是谁」的指纹（剥数字的名字/标签），换币时唯一可靠的判据
    if (!root.dataset.gigaxFp) root.dataset.gigaxFp = popupFingerprint(root, node);
    node.innerHTML = LOADING_HTML;
    return true;
  }

  // ===== 判据上报（自查用）=====
  // 把「这个浮层显示的是谁 / 按哪条判据认的 / 各判据此刻分别指向谁」当成一次普通 GET 发出去。
  // 服务端访问日志会原样记下 URL（路由不存在也没关系，404 也记），于是我能直接读日志定位，
  // 不用用户开控制台或截图。
  let lastBeacon = '';
  function beacon(ev, data) {
    try {
      const parts = [['ev', ev], ['v', SCRIPT_VERSION]];
      for (const k of Object.keys(data || {})) parts.push([k, data[k]]);
      const q = parts.map(([k, v]) => `${k}=${encodeURIComponent(v == null ? '' : String(v))}`).join('&');
      if (q === lastBeacon) return;
      lastBeacon = q;
      GM_xmlhttpRequest({ method: 'GET', url: `${API}/_diag?${q}`, timeout: 5000 });
    } catch (e) { /* ignore */ }
  }

  const shortCa = (v) => (v && v.ca ? String(v.ca).slice(0, 10) : '-');
  function judgeSnapshot(root) {
    const out = {};
    try { out.id = shortCa(caFromPopupIdentity(root)); } catch (e) { out.id = 'ERR'; }
    try { const h = hoverTarget(root); out.hov = h ? shortCa(h) + '/' + h.why : '-'; } catch (e) { out.hov = 'ERR'; }
    try { out.ptr = shortCa(caFromPointer(root)); } catch (e) { out.ptr = 'ERR'; }
    try { const f = latestAiEvent(NET_AI_FRESH); out.frsh = f ? shortCa(f) + '/' + (f.path || '').slice(0, 24) : '-'; } catch (e) { out.frsh = 'ERR'; }
    try { const l = hoverLoose(); out.loose = l ? shortCa(l) : '-'; } catch (e) { out.loose = 'ERR'; }
    out.net = String(netVersion);
    return out;
  }

  // 这个 CA 在页面对应哪张卡片、卡片上写的 symbol —— 只用来显示在徽标里，
  // 让人一眼看出「这段叙事是给哪个币的」（很多币的叙事开头都在讲同一个发射台）。
  const symCache = new Map();
  function symbolForCa(ca) {
    if (!ca) return '';
    if (symCache.has(ca)) return symCache.get(ca);
    let sym = '';
    try {
      for (const a of document.querySelectorAll('a[href*="/token/"]')) {
        const p = parseUrlToken(a.getAttribute('href'));
        if (!p || p.ca !== ca) continue;
        const card = cardFromAnchor(a, ca);
        if (card) sym = symbolOfCard(card, ca) || '';
        if (!sym) {
          const t = (a.textContent || '').replace(/\s+/g, ' ').trim();
          // 只取开头一段像名字的东西：遇到空格/括号/数字就截断，避免把
          // 「PK1056(4/2)12h $19.79万 -0.65%)」这种统计块当成币名（用户 2026-11-19 截图）。
          const cut = t.split(/[\s(（,，|]/)[0] || '';
          if (cut && cut.length <= 14) sym = cut;
        }
        if (sym) break;
      }
    } catch (e) { /* ignore */ }
    if (symCache.size > 200) symCache.clear();
    symCache.set(ca, sym);
    return sym;
  }

  // 我们写进去的正文单独套一层容器 + 显式排版。
  // 为什么必须套一层：浮层本身是给「短摘要」设计的，字号小（实测 ~11px）、行高紧，
  // 直接往 node 里塞文本节点会一路继承下去 —— 用户 2026-11-19：「字做得太小了」。
  // 也不能直接给 node 设 font-size：还原原文时那段行内样式会留在 node 上，把小字的
  // 原生文案也顶成大字。套一层之后，`restoreContent` 把 innerHTML 换回来就干净了。
  const NARR_FONT_SIZES = [13, 15, 17];
  let narrFont = Number(GM_getValue('narrFont', 15));
  if (!NARR_FONT_SIZES.includes(narrFont)) narrFont = 15;

  function styleNarrBody(el) {
    el.style.fontSize = `${narrFont}px`;
    el.style.lineHeight = '1.62';
    el.style.letterSpacing = '0.01em';
    el.style.wordBreak = 'break-word';
  }

  // 菜单放这儿而不是文件顶部：标签里要读 narrFont，写在顶部会撞暂时性死区。
  GM_registerMenuCommand('调整浮层字号（13 → 15 → 17 循环）', () => {
    const i = NARR_FONT_SIZES.indexOf(narrFont);
    narrFont = NARR_FONT_SIZES[(i + 1) % NARR_FONT_SIZES.length];
    GM_setValue('narrFont', narrFont);
    document.querySelectorAll('.gigax-narrative-body').forEach(el => {
      styleNarrBody(el);
      const b = el.querySelector('span');
      if (b) b.style.fontSize = `${Math.max(11, narrFont - 1)}px`;
    });
    log('浮层字号：' + narrFont + 'px');
  });

  function renderNarrative(root, text, ca, chain, opts) {
    const node = nodeOf(root);
    if (!node) { warn('找不到正文节点'); return false; }
    saveOrig(root, node);
    if (!root.dataset.gigaxFp) root.dataset.gigaxFp = popupFingerprint(root, node);
    // 已经是同一个币、同一段正文、容器也还在 → 一个字都不动。
    // 重写 innerHTML 会让整块重新布局（用户看到的「闪一下」），没必要。
    const old = node.querySelector('.gigax-narrative-body');
    if (old && root.dataset.gigaxCa === ca &&
        (old.textContent || '').includes(text.slice(0, 40))) {
      styleNarrBody(old);
      root.dataset.gigaxDone = '1';
      return true;
    }
    node.innerHTML = '';
    const body = document.createElement('div');
    body.className = 'gigax-narrative-body';
    styleNarrBody(body);
    const badge = document.createElement('span');
    const sym = symbolForCa(ca);
    badge.textContent = sym ? `【GIGA X 叙事 · ${sym}】 ` : '【GIGA X 叙事】 ';
    badge.style.cssText = `color:#7c5cff;font-weight:700;font-size:${Math.max(11, narrFont - 1)}px;white-space:nowrap;`;
    // 悬停能看到用的是哪个地址、以及这个地址是从哪认出来的，对不上时一眼可查
    const why = root.dataset.gigaxWhy || '';
    badge.title = `GIGA X 按 ${chain}:${ca} 取叙事${why ? `（地址来源：${why}）` : ''} · v${SCRIPT_VERSION}`;
    body.appendChild(badge);
    body.appendChild(document.createTextNode(text));
    node.appendChild(body);
    root.dataset.gigaxDone = '1';
    if (ca) root.dataset.gigaxCa = ca;
    if (chain) root.dataset.gigaxChain = chain;
    root.dataset.gigaxNetAt = String(netVersion);
    delete root.dataset.gigaxPending;
    delete root.dataset.gigaxAttempts;
    delete root.dataset.gigaxRetryAt;
    nodeMap.set(root, node);
    log('已替换:', chain + ':' + ca, '|', text.slice(0, 40) + '…');
    if (!(opts && opts.silent)) {
      try {
        beacon('render', {
          shown: ca, sym: sym || '', why: root.dataset.gigaxWhy || '',
          fs: getComputedStyle(body).fontSize + '/' + narrFont,
          host: (node.tagName || '?') + '.' + String(node.className || '').slice(0, 24),
          ...judgeSnapshot(root),
        });
      } catch (e) { /* ignore */ }
    }
    return true;
  }

  // 只把正文还回去，保留标记（done/retryAt 由调用方决定）
  function restoreContent(root) {
    const node = nodeOf(root);
    if (!node) return;
    const orig = root.dataset.gigaxOrig;
    if (orig) { node.innerHTML = orig; return; }
    // 原文本来就是空的：只撤掉我们自己的占位，
    // 千万别把 React 在这期间写进来的原生文案一起抹掉（那会留下一个永久空白框）
    if ((node.innerHTML || '').includes('GIGA X')) node.innerHTML = '';
  }

  function resetRoot(root) {
    restoreContent(root);
    nodeMap.delete(root);
    delete root.dataset.gigaxOrig;
    delete root.dataset.gigaxDone;
    delete root.dataset.gigaxCa;
    delete root.dataset.gigaxChain;
    delete root.dataset.gigaxWhy;
    delete root.dataset.gigaxFp;
    delete root.dataset.gigaxNetAt;
    delete root.dataset.gigaxAttempts;
    delete root.dataset.gigaxRetryAt;
    delete root.dataset.gigaxPending;
  }

  function restoreAll() {
    document.querySelectorAll('[data-gigax-done],[data-gigax-pending]').forEach(resetRoot);
    clearMarks();
  }

  // 结果落地：有正文就换上；「没有」就悄悄还原；真错误才重试
  function settle(root, text, err, info, ca, chain) {
    delete root.dataset.gigaxPending;
    if (text) {
      // 响应回来时浮窗可能已经换成另一个币了（GMGN 换 hover 目标很快，GIGA X 生成要几秒）。
      // 这段正文属于**发请求时**那个币，写进新币的浮窗就是「代币与叙事对不上」。
      // 宁可丢掉让下一轮按新币重取（命中的是缓存，不会再打一次网络）。
      const now = resolveTarget(root);
      if (!now || now.ca !== ca) {
        log('响应已过期（浮窗已换成别的币），丢弃：', chain + ':' + ca,
            '→', now ? now.chain + ':' + now.ca : 'null');
        // 完全认不出币时把原文还回去，别留一个转圈的占位框
        if (!now) restoreContent(root);
        return;
      }
      renderNarrative(root, text, ca, chain);
      return;
    }

    if (info && info.reason === 'no-narrative') {
      restoreContent(root);
      root.dataset.gigaxDone = '2';           // 2 = 已知 GIGA X 没这个币
      root.dataset.gigaxCa = ca || '';
      root.dataset.gigaxNetAt = String(netVersion);
      // status=generating 时 5 秒后再看一眼（GIGA X 正在写），别一躺就是两分钟
      root.dataset.gigaxRetryAt =
        String(Date.now() + Math.max(5000, (info.retryAfter || 0) * 1000 + 1500));
      log('GIGA X 暂无该币叙事，保留原文：', chain + ':' + ca, info.status || '');
      return;
    }

    const attempts = Number(root.dataset.gigaxAttempts || '0') + 1;
    root.dataset.gigaxAttempts = String(attempts);
    restoreContent(root);
    if (attempts < MAX_ATTEMPTS) {
      root.dataset.gigaxRetryAt = String(Date.now() + RETRY_MS);
      warn(`第 ${attempts} 次失败，${RETRY_MS / 1000}s 后重试：`, err);
      return;
    }
    // 彻底失败：还原文，别在页面上留一个红框
    root.dataset.gigaxDone = '3';             // 3 = 放弃
    root.dataset.gigaxRetryAt = String(Date.now() + HARD_FAIL_MS);
    warn('放弃替换，保留 GMGN 原文：', chain + ':' + ca, err);
  }

  // ===== 调试：打印悬浮窗 DOM =====
  function dumpFloating() {
    const roots = findFloatingRoots();
    log('调试：找到悬浮窗根', roots.length, '个');
    roots.forEach((r, i) => {
      log(`--- root ${i} ---`, r.tagName, r.className);
      log(r.outerHTML.slice(0, 2500));
      const node = findTextNode(r);
      log('正文节点:', node ? node.tagName + '.' + node.className : 'null',
          node ? (node.innerText || '').slice(0, 120) : '');
      const t = resolveTarget(r);
      log('解析结果:', t ? `${t.chain || '?'}:${t.ca}（来源 ${t.why}）` : 'null',
          '| 浮层内地址数:', uniqueCas(collectUrlsIn(r)).length,
          '| 最近网络事件:', netEvents.slice(-4).map(e => `${e.chain || '?'}:${e.ca.slice(0, 8)}…(${e.path.slice(0, 40)})`));
    });
    if (!roots.length) warn('没找到悬浮窗。请在 GMGN 上 hover/点开一个代币的 AI 叙事悬浮窗后再按 Alt+G');
  }
  document.addEventListener('keydown', e => {
    if (e.altKey && !e.shiftKey && e.key.toLowerCase() === 'g') { e.preventDefault(); dumpFloating(); }
  });

  // ===== 诊断：Alt+Shift+G 直接在页面上列出「每个可见的币为什么没标上」 =====
  // 控制台用户不方便看，画在页面上截个图就能定位：是没取到叙事、没命中关键词，
  // 还是卡片定位失败（战壕网格的老问题）。
  function dumpCards() {
    const lines = [];
    const seen = new Set();
    let n = 0;
    for (const a of document.querySelectorAll('a[href*="/token/"]')) {
      const p = parseUrlToken(a.getAttribute('href'));
      if (!p || seen.has(p.ca)) continue;
      seen.add(p.ca);
      if (++n > 40) break;
      const chain = extractChain(a, p);
      const c = peekCache(chain, p.ca);
      const hits = c && c.text ? (matchHot(c.text).join('/') || '无关键词')
        : (c ? `空(${c.reason || '?'})` : '未取到');
      const card = cardFromAnchor(a, p.ca);
      let tail;
      if (card) {
        const r = card.getBoundingClientRect();
        tail = `卡片 ${Math.round(r.width)}x${Math.round(r.height)} 背景${isPainted(card) ? '有' : '无'}` +
               (card.classList.contains('gigax-ai-card') ? ' 【已标·AI白】'
                 : card.classList.contains('gigax-hot-card') ? ' 【已标】' : '');
      } else {
        tail = `卡片未找到（该层地址数 ${uniqueCas(tokenLinksIn(a.parentElement || a)).length}）`;
      }
      lines.push(`${p.ca.slice(0, 10)}… ${hits} | ${tail}`);
    }
    const old = document.getElementById('gigax-diag');
    if (old) old.remove();
    const box = document.createElement('div');
    box.id = 'gigax-diag';
    box.style.cssText = [
      'position:fixed', 'right:12px', 'bottom:64px', 'z-index:2147483000',
      'max-width:560px', 'max-height:62vh', 'overflow:auto',
      'background:#0b1020', 'color:#d7e0ff', 'border:1px solid #4d6bfe',
      'border-radius:8px', 'padding:10px 12px', 'font:11px/1.6 ui-monospace,Consolas,monospace',
      'white-space:pre', 'box-shadow:0 8px 32px rgba(0,0,0,.6)', 'cursor:pointer',
    ].join(';');
    box.textContent = `GIGA X 标记诊断 v${SCRIPT_VERSION}（${lines.length} 个币，点一下关闭）\n` + lines.join('\n');
    box.addEventListener('click', () => box.remove());
    document.body.appendChild(box);
    log('诊断输出', lines.length, '条');
  }
  document.addEventListener('keydown', e => {
    if (e.altKey && e.shiftKey && e.key.toLowerCase() === 'g') { e.preventDefault(); dumpCards(); }
  });

  // 已经渲染过的浮层：判断它**现在**讲的还是不是当初那个币。
  // GMGN 的浮层容器是复用的 —— 换币时它只改标题/数字，那段正文节点并不重写，
  // 于是我们塞进去的上一段叙事会一直留着：IDRENT、红薯小猫弹出来都是 PIKA 的叙事
  // （用户 2026-11-18）。所以「里面还是 GIGA X 的内容」绝不能当作「已经处理好了」。
  // 换币检测。顺序很重要：**几何判据优先**，网络请求只在前两者都拿不到时才用。
  // 之前把「GMGN 刚为别的币发了叙事请求」放在最前，而 GMGN 会不停预取相邻卡片的叙事，
  // 于是同一个浮层每 2~3 秒就被判定为「换币」→ 重渲染 → 页面一直抖（用户 2026-11-19 的抓包）。
  function staleTarget(root) {
    const shown = root.dataset.gigaxCa || '';

    // 详情页：浮层显示的就是**页面这个币** → 永远不算过期。
    // 否则「最近 3 秒的叙事请求 / 最近 hover」会把正确的页面币判成换币，
    // 于是不停重取、甚至换成列表里别的币（hotdog 页显示 COST，2026-11-19）。
    const pg = pageToken();
    if (pg) {
      if (shown === pg.ca) return false;
      // 显示的不是页面币，而指针又没落在任何 data 卡片上 → 一定是错的，立刻整块重来
      if (!pointerOnDataCard(root)) return true;
    }

    // 绑定的身份仍然有效（GMGN 没为新币发请求）→ 以它为准，别被鼠标位置带着跑
    const bind = bindValid(root);
    if (bind) {
      const bca = bind.slice(bind.indexOf(':') + 1);
      if (bca && bca !== shown) return true;
      if (bca === shown) return false;
    }

    // A 浮层头部指纹（名字/标签变了 = 换币）
    const fp = root.dataset.gigaxFp || '';
    if (fp) {
      const nowFp = popupFingerprint(root, nodeOf(root));
      if (nowFp && nowFp !== fp) return true;
    }
    // B 指针确实停在另一张卡片里 / 或浮层文字里出现了另一张卡片的 symbol
    const h = hoverTarget(root);
    if (h && h.ca !== shown) return true;
    // 指针**几何上还落在那张卡片里**（浮层盖住卡片不影响卡片矩形，why='hover' 是纯几何判据）
    // 并且讲的就是它 → 铁定没换币，不必再看穿透。放在 C 之前是必须的：浮层盖住指针时，
    // 穿透看到的是背后的另一张卡片，会让同一个浮层每秒被判一次换币（2026-11-19）。
    if (h && h.ca === shown && h.why === 'hover') return false;
    // C 浮层底下穿透到的是另一张卡片
    const p = caFromPointer(root);
    if (p && p.ca !== shown) return true;

    // 几何已经能确认「还是同一个币」时，不再理会网络请求（它在预取邻居）
    if ((h && h.ca === shown) || (p && p.ca === shown)) return false;

    // D 实在没有落点：才用「最近 3 秒的叙事请求 / 最近 hover」兜底
    const ai = latestAiEvent(NET_AI_FRESH);
    if (ai && ai.ca !== shown) return true;
    const hl = hoverLoose();
    if (hl && hl.ca !== shown) return true;
    return Number(root.dataset.gigaxNetAt || '-1') !== netVersion;
  }

  // 记住哪些浮层节点是「新出现」的。GMGN 有时每个币都新建一个浮层节点，有时复用同一个 ——
  // 新节点的第一次解析一定是最准的（此刻鼠标就在那张卡片上），所以把它**冻结**成这个节点的
  // 身份，之后只要 GMGN 没为新币发叙事请求（netVersion 未变）就一直用它。
  // 这就是「卡片加载/浮层出现时就把 CA 绑定好」的落地（用户 2026-11-19 的思路）。
  const seenRoots = new WeakSet();

  // 绑定的身份是否仍然可用：GMGN 没为新币发叙事请求，且浮层头部指纹没变
  // （复用同一个节点换币时，头部一定会变 —— 那是判断「这个节点现在讲的是谁」的硬信号）。
  function bindValid(root) {
    const bind = root.dataset.gigaxBind;
    if (!bind) return null;
    if (Number(root.dataset.gigaxBindNet || '-1') !== netVersion) return null;

    // **绑定只用于「浮层刚出现的那一下」**，不是永久身份。
    // 浮层被复用（同一个节点换币）时，头部可能完全不变（有些浮层里只有正文+免责声明），
    // 指纹判据失效 —— 唯一还活着的信号是「鼠标已经移到别的卡片上了」。
    // 用户 2026-11-19：「第一次 hover 是对的，第二次就变成 PIKA」就是这个原因。
    if (Date.now() - Number(root.dataset.gigaxBindAt || '0') > 4000) return null;
    const r = hoverRec;
    if (r && r.ca && bind.slice(bind.indexOf(':') + 1) !== r.ca) return null;

    const fp = root.dataset.gigaxBindFp || '';
    if (fp) {
      const nowFp = popupFingerprint(root, nodeOf(root));
      if (nowFp && nowFp !== fp) return null;
    }
    return bind;
  }

  // 页面上明明有「AI 叙事特征文字」，但一个浮层都没认出来 —— 把它的层级链上报一次。
  // 这就是「有时候不显示」的黑匣子：没有这条，我只能猜是没识别到还是没取到地址。
  let lastNoDetectAt = 0;
  function reportUndetected() {
    if (Date.now() - lastNoDetectAt < 10000) return;
    lastNoDetectAt = Date.now();
    try {
      for (const el of document.querySelectorAll('div,span,p')) {
        if (el.childElementCount > 1) continue;
        const t = el.textContent || '';
        if (t.length < 6 || t.length > DEEP_MAX_TEXT) continue;
        if (!ANCHORS.some(a => t.includes(a))) continue;
        const chain = [];
        let cur = el;
        for (let k = 0; k < 6 && cur && cur !== document.body; k++) {
          const cs = getComputedStyle(cur);
          const r = cur.getBoundingClientRect();
          chain.push(`${cur.tagName}.${String(cur.className || '').slice(0, 26)}|${cs.position}|${Math.round(r.width)}x${Math.round(r.height)}`);
          cur = cur.parentElement;
        }
        beacon('nodetect', { len: String(t.length), text: t.slice(0, 30), chain: chain.join(' < ') });
        return;
      }
    } catch (e) { /* ignore */ }
  }

  // ===== 主循环 =====
  function scan() {
    if (!enabled) return;
    // 关掉替换后：完全不碰浮层 DOM —— 标色由 autoPrefetch + paintHotCards 独立完成
    if (!replacePopup) return;
    const roots = findFloatingRoots();
    if (!roots.length) reportUndetected();
    for (const root of roots) {
      if (!seenRoots.has(root)) {
        seenRoots.add(root);
        try {
          const t0 = resolveTarget(root);
          if (t0 && t0.ca) {
            root.dataset.gigaxBind = `${t0.chain || ''}:${t0.ca}`;
            root.dataset.gigaxBindNet = String(netVersion);
            root.dataset.gigaxBindFp = popupFingerprint(root, nodeOf(root));
            root.dataset.gigaxBindAt = String(Date.now());
            log('浮层首次出现，绑定身份:', root.dataset.gigaxBind, '来源', t0.why);
          }
        } catch (e) { /* ignore */ }
      }
      const state = root.dataset.gigaxDone;

      if (state === '1') {
        // 我们还塞着内容，且**仍然是同一个币**才跳过；换币了就整块重来。
        const node = nodeOf(root);
        const ours = node && (node.textContent || '').includes('GIGA X 叙事');
        if (ours && !staleTarget(root)) {
          // 正文还在、但**容器丢了**（React 重排冲掉外层 div，或者这段是旧版直接塞的纯文本）。
          // 这时字会继承浮层原生的小字号 —— 用户说的「显示一段时间就变成很小的字体」。
          // 命中缓存就地重排一次，不重新发请求、不重置身份；缓存也没了才会掉到下面的重置路径。
          if (node.querySelector('.gigax-narrative-body')) continue;
          const c = peekCache(root.dataset.gigaxChain || '', root.dataset.gigaxCa || '');
          if (c && c.text) {
            renderNarrative(root, c.text, root.dataset.gigaxCa, root.dataset.gigaxChain || '', { silent: true });
            continue;
          }
        }
        try { beacon('stale', { shown: root.dataset.gigaxCa || '', ...judgeSnapshot(root) }); }
        catch (e) { /* ignore */ }
        resetRoot(root);
      } else if (state === '2') {
        // 已知 GIGA X 没这个币：换币（鼠标移到别的卡片 / GMGN 又发了叙事请求）或冷却到点才重判。
        // 不重判的话，同一个浮层换成下一个币时会一直保持原判 —— 那正是「有些币没有」的另一半原因。
        const until = Number(root.dataset.gigaxRetryAt || '0');
        if (until > Date.now() && !staleTarget(root)) continue;
        resetRoot(root);
      } else if (state === '3') {
        if (Number(root.dataset.gigaxRetryAt || '0') > Date.now()) continue;
        resetRoot(root);
      }

      if (root.dataset.gigaxPending === '1') {
        // 请求还在路上：React 可能把原生文案又写回来了，继续按住
        const node = nodeOf(root);
        if (node && !(node.innerText || '').includes('GIGA X')) maskNative(root);
        continue;
      }
      if (Number(root.dataset.gigaxRetryAt || '0') > Date.now()) continue;

      // 取址放在所有早退之后：resolveTarget 会扫 DOM，别白跑
      const target = resolveTarget(root);
      const ca = target ? target.ca : null;
      if (!ca) {
        // 取不到就退避 3 秒：否则每 200ms 刷一次 console，除了吵没别的用
        root.dataset.gigaxRetryAt = String(Date.now() + 3000);
        warn('悬浮窗未提取到 CA，3s 后重试', root);
        continue;
      }
      const chain = extractChain(root, target);
      const key = chain + ':' + ca;

      const cached = peekCache(chain, ca);
      if (cached) {
        // 命中缓存：有正文立刻换上；没正文就一个字都不动（避免「加载中 → 原文」抽搐）
        if (cached.text) renderNarrative(root, cached.text, ca, chain);
        else if (cached.reason === 'no-narrative') {
          root.dataset.gigaxDone = '2';
          root.dataset.gigaxCa = ca;
          root.dataset.gigaxNetAt = String(netVersion);
          root.dataset.gigaxRetryAt =
            String(Date.now() + Math.max(5000, (cached.retryAfter || 0) * 1000 + 1500));
        } else if (cached.reason) {
          root.dataset.gigaxDone = '3';
          root.dataset.gigaxRetryAt = String(Date.now() + HARD_FAIL_MS);
        }
        continue;
      }

      log('检测到悬浮窗', key, '来源=', target.why);
      maskNative(root);                       // 先遮住原生正文，再问桥接
      root.dataset.gigaxPending = '1';
      root.dataset.gigaxCa = ca;
      root.dataset.gigaxWhy = target.why || '';
      fetchNarrative(chain, ca,
        (text, err, info) => settle(root, text, err, info, ca, chain),
        { why: target.why, hover: hoverRec && hoverRec.ca });
    }
    // React 重渲染会把卡片上的 class 冲掉，每轮顺手重刷一次。
    // 注意条件里要带上 hotIndex：缓存里没有正文、但「上次标过的」还有记录时也要重刷，
    // 否则过一会儿（或刷新后）这些币就再也不标了。
    if (cache.size || Object.keys(hotIndex).length) schedulePaint(0);
  }

  // 「提前显示颜色标记」不依赖 scan 的重节奏：autoPrefetch 自己跑一个 700ms 的
  // 独立定时器（轻量：只发请求，不扫 DOM），进页面就立刻开始预取 + 标色。
  // 而 scan（浮窗识别，重）走 5s 保底 + 事件驱动 —— 两者解耦，互不拖累。
  const idleCb = (typeof requestIdleCallback === 'function')
    ? requestIdleCallback.bind(window)
    : (fn) => setTimeout(fn, 50);
  function autoPrefetchLoop() {
    if (enabled && !document.hidden) {
      idleCb(() => autoPrefetch(), { timeout: 200 });
    }
  }
  setInterval(autoPrefetchLoop, 700);

  // 标记保底重刷：卡片的 class 会被 React 反复冲掉（价格每跳一次就可能换节点），
  // 事件驱动之外每 3 秒补一次 —— 保证任何情况下标记最多丢 3 秒。
  // 很轻：卡片定位结果按 CA 缓存，命中时只剩 classList 操作。
  setInterval(() => {
    if (!enabled || hotMode === 'off' || document.hidden) return;
    if (!cache.size && !Object.keys(hotIndex).length) return;
    schedulePaint(0);
  }, 3000);

  // 扫描节流：轮询 + DOM 变化 + hover 三条路都会来调，合并成最快 250ms 一次。
  // 用 requestIdleCallback 跑，让 scan 只在浏览器空闲时执行，绝不和 GMGN 的渲染抢主线程。
  let lastScanAt = 0;
  function scheduleScan(delay) {
    if (scheduleScan._t) return;
    const wait = Math.max(delay || 80, 250 - (Date.now() - lastScanAt));
    scheduleScan._t = setTimeout(() => {
      scheduleScan._t = null;
      lastScanAt = Date.now();
      idleCb(() => scan(), { timeout: 300 });
    }, wait);
  }

  // 保底轮询大幅降频到 5 秒，且走 idle：真正触发扫描的是「叙事类网络请求 / DOM 变化 /
  // hover」，这个 setInterval 只是兜底（防漏网），不再是每 1.5s 压一次主线程。
  setInterval(() => scheduleScan(0), 5000);

  // DOM 一变就扫：悬浮窗是 React 现插的，等 800ms 轮询才反应就是「先看到原生文案」的根源。
  // 全量扫有 1 秒节流，所以这里只对「看着像叙事浮层」的变更强制放开节流。
  function looksLikeNarrative(node) {
    if (!node || node.nodeType !== 1) return false;
    const cls = typeof node.className === 'string' ? node.className.toLowerCase() : '';
    if (CONTAINER_HINTS.some(h => cls.includes(h))) return true;
    const t = node.textContent || '';
    return ANCHORS.some(a => t.includes(a));
  }
  try {
    // GMGN 是 React 重渲染极其频繁的页面（K线每秒 tick、行情条实时刷），
    // MutationObserver 挂 subtree 会每个 tick 都触发。这里加一层粗粒度早退：
    // 只有 mutation 里真的出现「叙事特征」节点才 scheduleScan，其余直接忽略，
    // 绝不让插件在每一个行情 tick 上都去读 textContent 触发 reflow。
    const mo = new MutationObserver(muts => {
      // 大量小 mutation（价格数字刷新）时快速跳过：先扫一眼有没有类名/文本特征。
      for (const m of muts) {
        if (m.type === 'characterData') {
          if (looksLikeNarrative(m.target.parentElement)) { lastDeepScan = 0; scheduleScan(40); return; }
          continue;
        }
        for (const n of m.addedNodes) {
          if (looksLikeNarrative(n)) { lastDeepScan = 0; scheduleScan(40); return; }
        }
      }
    });
    mo.observe(document.body, { childList: true, subtree: true, characterData: true });
    log('DOM 观察已挂');
  } catch (e) { warn('MutationObserver 挂载失败', e); }

  // ===== 回收键观察：虚拟列表换币时必须立刻重刷标色 =====
  // 实测：GMGN 的列表容器带 `data-recycler-key="bsc:0x…"`，滚动时**同一个 DOM 节点**
  // 被复用给另一个币（key 一变内容就整块换掉，节点本身不换）。只在节点上挂 class 的后果：
  //   ① 标过的卡片变成别的币，蓝底还在；② React 重渲染把 class 冲掉，于是「刚刚还标着，一会没了」。
  // attributeFilter 只盯这三个键，一次滚动最多几十条 mutation，比 subtree+characterData 便宜得多。
  try {
    const moKey = new MutationObserver(() => schedulePaint(30));
    moKey.observe(document.body, {
      attributes: true,
      subtree: true,
      attributeFilter: ['data-recycler-key', 'data-row-key', 'data-trenches-token-address'],
    });
    log('回收键观察已挂（换币即重刷标色）');
  } catch (e) { warn('回收键观察挂载失败', e); }

  // hover 预热：鼠标停在某个币的卡片/AI 按钮上时就先去取一次。
  // GMGN 的悬浮窗要 hover 之后才渲染，等它出现时缓存已经命中 —— 用户看不到原生文案。
  let hoverTimer = null;
  let lastHoverAt = 0;
  let lastHoverCa = null;
  let prefetchDepth = 0;
  document.addEventListener('pointerover', e => {
    if (!enabled) return;
    if (Date.now() - lastHoverAt < 120) return;
    lastHoverAt = Date.now();
    const hit = caFromElement(e.target);
    if (!hit) { lastHoverCa = null; return; }
    if (hit.ca !== lastHoverCa) {
      lastHoverCa = hit.ca;
      netVersion++;    // 换了个币 ≈ 悬浮窗内容要换，作废「已知无数据」标记
    }
    const el = e.target;
    if (hoverTimer) clearTimeout(hoverTimer);
    hoverTimer = setTimeout(() => {
      const chain = extractChain(el, hit);
      if (peekCache(chain, hit.ca)) return;
      if (prefetchDepth >= 2) return;   // 鼠标扫过一长串卡片时别把桥接打爆
      prefetchDepth++;
      log('预热叙事：', chain + ':' + hit.ca);
      fetchNarrative(chain, hit.ca, () => { prefetchDepth--; });
    }, 180);
  }, { passive: true, capture: true });

  // 自定义关键词面板：进页面就挂上 —— 它跟「浮层替换」无关（关掉替换也要能填词）。
  try { ensureKwPanel(); } catch (e) { warn('关键词面板挂载失败', e); }
  setInterval(() => {
    if (kwHidden) return;
    try { ensureKwPanel(); } catch (e) { /* ignore */ }   // React 换 body 的极端情况兜一下
  }, 5000);

  log('已加载 v' + SCRIPT_VERSION + '。桥接=', API, ' 开关=', enabled ? '开' : '关', ' 机会标记=', hotMode);
  log('机会标记：叙事里出现「新机制/发射台/平台币/第一个/agentic…」时，对应卡片刷蓝底（菜单可关/可放宽/可调深浅）');
  log('自动预取：', autoPrefetchOn ? '开（按批，一屏一个请求，不用 hover）' : '关');
  log('取址优先级：data 属性(战壕卡/列表行) > 浮窗内唯一地址 > 详情页(指针不在卡片上时>用页面币) > 绑定 > 浮层头部身份 > hover > 指针穿透 > 叙事请求');
  log('命中缓存的悬浮窗直接替换；未命中先遮住原生正文，GIGA X 说没有就原样还回去');
  log('快捷键 Alt+G 打印悬浮窗 DOM（含取址来源）；菜单里可开关/改桥接地址');
})();
