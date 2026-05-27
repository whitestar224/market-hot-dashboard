(() => {
  const SEEN_KEY = "xingyunshe-alert-seen-v2";
  const MUTED_KEY = "xingyunshe-alert-muted";
  const MAX_SEEN = 6000;
  const MAX_VISIBLE = 4;
  const FIRST_SYNC_DELAY = 1800;
  const FEED_STAGGER_MS = 650;
  const LEADER_KEY = "xingyunshe-alert-leader-v1";
  const LEADER_ID = `${Date.now()}:${Math.random().toString(36).slice(2)}`;
  const LEADER_TTL = 15_000;
  const LEADER_CHECK_MS = 5_000;
  const SERVER_MONITOR_STATUS_URL = "/api/site-alert-monitor-status";
  const DEFAULT_MAX_ALERT_AGE_MS = 24 * 60 * 60 * 1000;
  const FUTURE_ALERT_TOLERANCE_MS = 10 * 60 * 1000;

  const DEFAULT_FEEDS = [
    {
      name: "listings",
      url: "/api/listing-events",
      interval: 60_000,
      maxAgeMs: DEFAULT_MAX_ALERT_AGE_MS,
      parse: parseListingEvents
    },
    {
      name: "newboards",
      url: "/api/new-coin-rankings",
      interval: 60_000,
      maxAgeMs: 48 * 60 * 60 * 1000,
      parse: parseNewboardEvents
    },
    {
      name: "newsflash",
      url: "/api/newsflash",
      interval: 45_000,
      maxAgeMs: 6 * 60 * 60 * 1000,
      parse: parseNewsflashEvents
    },
    {
      name: "market",
      url: "/api/market-hot",
      interval: 90_000,
      maxAgeMs: 12 * 60 * 1000,
      parse: parseMarketSignals
    },
    {
      name: "briefs",
      url: "/api/automation-briefs",
      interval: 120_000,
      maxAgeMs: DEFAULT_MAX_ALERT_AGE_MS,
      parse: parseBriefEvents
    }
  ];

  const MARKET_KIND_HINTS = [
    "上新",
    "上市",
    "ipo",
    "新股",
    "榜单",
    "行情",
    "市场",
    "交易"
  ];

  const MARKET_KEYWORDS = [
    "上市",
    "上新",
    "ipo",
    "新股",
    "挂牌",
    "申购",
    "招股",
    "上市聆讯",
    "交易",
    "交易所",
    "币安",
    "binance",
    "okx",
    "欧易",
    "bitget",
    "aicoin",
    "合约",
    "永续",
    "现货",
    "加密",
    "币圈",
    "代币",
    "token",
    "airdrop",
    "空投",
    "主网",
    "defi",
    "web3",
    "bitcoin",
    "btc",
    "ethereum",
    "eth",
    "solana",
    "sol",
    "usdt",
    "usdc",
    "etf",
    "港股",
    "美股",
    "a股",
    "股票",
    "股价",
    "财报",
    "融资",
    "并购",
    "回购",
    "评级",
    "利率",
    "降息",
    "加息",
    "美联储",
    "cpi",
    "pce",
    "非农",
    "sec",
    "监管",
    "行情",
    "价格",
    "涨幅",
    "跌幅",
    "爆仓",
    "持仓",
    "成交",
    "换手",
    "资金",
    "净流入",
    "市值"
  ];

  const NON_MARKET_NOISE = [
    "演唱会",
    "影视",
    "综艺",
    "体育",
    "天气",
    "旅游",
    "美食",
    "招聘",
    "抽奖",
    "八卦"
  ];

  const state = {
    seen: loadSeen(),
    pendingDesktop: new Set(),
    feedReady: new Set(),
    feeds: new Map(),
    audioContext: null,
    muted: localStorage.getItem(MUTED_KEY) === "1",
    stack: null,
    started: false,
    serverMonitorActive: false,
    isLeader: false,
    leaderTimer: null
  };

  function loadSeen() {
    try {
      const values = JSON.parse(localStorage.getItem(SEEN_KEY) || "[]");
      return new Set(Array.isArray(values) ? values : []);
    } catch {
      return new Set();
    }
  }

  function saveSeen() {
    const values = [...state.seen].slice(-MAX_SEEN);
    state.seen = new Set(values);
    localStorage.setItem(SEEN_KEY, JSON.stringify(values));
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    })[char]);
  }

  function stripText(value, max = 138) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    if (text.length <= max) return text;
    return `${text.slice(0, max - 3)}...`;
  }

  function eventTime(value) {
    if (value instanceof Date) return value.getTime();
    const numeric = Number(value);
    if (Number.isFinite(numeric) && numeric > 0) {
      return numeric < 10_000_000_000 ? numeric * 1000 : numeric;
    }
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : Date.now();
  }

  function timeLabel(value) {
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false
    }).format(new Date(eventTime(value)));
  }

  function stablePart(value) {
    return String(value || "")
      .trim()
      .toLowerCase()
      .replace(/\s+/g, " ")
      .slice(0, 180);
  }

  function stableKey(prefix, ...parts) {
    return `${prefix}:${parts.map(stablePart).filter(Boolean).join("|")}`;
  }

  function dedupeTitle(value) {
    return stablePart(value).replace(/^高热提醒[：:]\s*/i, "");
  }

  function alertDedupeKeys(item) {
    const title = dedupeTitle(item?.title);
    const source = item?.source || item?.sourceLabel || "";
    const body = stripText(item?.body || "", 100);
    return [
      item?.key || "",
      stableKey("alert-title-url", source, title, item?.url),
      stableKey("alert-title", source, title),
      stableKey("alert-body", source, title, body)
    ].filter((key) => key && !key.endsWith(":"));
  }

  function hasSeen(item) {
    return alertDedupeKeys(item).some((key) => state.seen.has(key));
  }

  function markSeen(item) {
    alertDedupeKeys(item).forEach((key) => state.seen.add(key));
  }

  function normalizeSearchText(value) {
    return String(value || "").replace(/\s+/g, " ").toLowerCase();
  }

  function hasAny(text, words) {
    const haystack = normalizeSearchText(text);
    return words.some((word) => haystack.includes(String(word).toLowerCase()));
  }

  function isMarketRelated(item) {
    if (item.market === true) return true;
    const text = [
      item.kind,
      item.source,
      item.sourceLabel,
      item.title,
      item.body,
      ...(Array.isArray(item.tags) ? item.tags : [])
    ].filter(Boolean).join(" ");

    if (hasAny(item.kind || "", MARKET_KIND_HINTS)) return true;
    if (!hasAny(text, MARKET_KEYWORDS)) return false;

    const noisy = NON_MARKET_NOISE.some((word) => normalizeSearchText(text).includes(word.toLowerCase()));
    if (!noisy) return true;
    return hasAny(text, ["上市", "ipo", "交易", "股票", "加密", "代币", "行情", "融资"]);
  }

  function pickFirst(source, keys) {
    for (const key of keys) {
      const value = source?.[key];
      if (value !== undefined && value !== null && String(value).trim() !== "") return value;
    }
    return "";
  }

  function normalizeAlertItem(item, feed = {}) {
    const source = pickFirst(item, ["source", "sourceName", "author"]) || feed.source || feed.title || feed.name || "RSS";
    const sourceLabel = pickFirst(item, ["sourceLabel", "label"]) || feed.sourceLabel || source.slice(0, 2).toUpperCase();
    const title = pickFirst(item, ["title", "headline", "name"]) || "市场信息";
    const body = pickFirst(item, ["body", "content", "summary", "description", "text"]) || "";
    const rawTime = pickFirst(item, ["time", "date", "pubDate", "publishedAt", "add_time", "createdAt"]);
    const time = rawTime !== "" ? rawTime : Date.now();
    const url = pickFirst(item, ["url", "link", "href"]) || feed.url || "";
    const key = item.key || stableKey(feed.name || "rss", item.id, item.guid, url, title, time);

    return {
      key,
      kind: item.kind || feed.kind || "市场信息",
      source,
      sourceLabel,
      title,
      body,
      url,
      time,
      priority: item.priority || feed.priority || "实时",
      tags: item.tags || feed.tags || [],
      market: item.market,
      hasExplicitTime: Boolean(rawTime),
      maxAgeMs: Number(item.maxAgeMs ?? feed.maxAgeMs ?? DEFAULT_MAX_ALERT_AGE_MS),
      allowStale: item.allowStale === true || feed.allowStale === true
    };
  }

  function isFreshForAlert(item) {
    if (item.allowStale) return true;
    const timestamp = eventTime(item.time);
    const now = Date.now();
    if (!Number.isFinite(timestamp)) return false;
    if (timestamp - now > FUTURE_ALERT_TOLERANCE_MS) return false;
    const maxAgeMs = Number.isFinite(Number(item.maxAgeMs)) ? Number(item.maxAgeMs) : DEFAULT_MAX_ALERT_AGE_MS;
    return now - timestamp <= maxAgeMs;
  }

  function parseGenericEvents(payload, feed = {}) {
    const items = Array.isArray(payload)
      ? payload
      : Array.isArray(payload?.items)
        ? payload.items
        : Array.isArray(payload?.entries)
          ? payload.entries
          : Array.isArray(payload?.data)
            ? payload.data
            : [];
    return items.map((item) => normalizeAlertItem(item, feed));
  }

  function textFrom(node, selectors) {
    for (const selector of selectors) {
      const found = node.querySelector(selector);
      const value = found?.textContent?.trim() || found?.getAttribute?.("href") || "";
      if (value) return value;
    }
    return "";
  }

  function parseRssText(xmlText, feed = {}) {
    const doc = new DOMParser().parseFromString(xmlText, "text/xml");
    if (doc.querySelector("parsererror")) return { items: [] };
    const feedTitle = textFrom(doc, ["channel > title", "feed > title"]) || feed.source || "RSS";
    const nodes = [...doc.querySelectorAll("item, entry")];
    return {
      items: nodes.map((node) => ({
        guid: textFrom(node, ["guid", "id"]) || textFrom(node, ["link"]),
        title: textFrom(node, ["title"]),
        body: textFrom(node, ["description", "summary", "content", "content\\:encoded"]),
        url: textFrom(node, ["link"]) || node.querySelector("link[href]")?.getAttribute("href") || feed.url,
        time: textFrom(node, ["pubDate", "published", "updated", "dc\\:date"]),
        source: feed.source || feedTitle,
        sourceLabel: feed.sourceLabel || "RSS",
        kind: feed.kind || "市场信息",
        tags: feed.tags || []
      }))
    };
  }

  function parseListingEvents(payload) {
    const sections = Array.isArray(payload?.sections) ? payload.sections : [];
    return sections.flatMap((section) =>
      (section.rows || []).map((row, rowIndex) => {
        const heat = newboardHeatInfo(row, Number(row.rank || rowIndex + 1));
        const baseKind = row.group === "ipo" ? "IPO / 上市" : row.group === "hk" || row.group === "us" ? "港美股上市" : "交易所上新";
        const baseTitle = row.title || row.symbol || "新的上新事件";
        return {
          key: stableKey("listing", row.id, row.source, row.title, row.symbol, row.date, row.url),
          kind: heat.high ? `${baseKind}高热` : baseKind,
          source: row.source || section.sourceName || "Listing",
          sourceLabel: row.sourceLabel || "NEW",
          title: heat.high ? `高热提醒：${baseTitle}` : baseTitle,
          body: [
            row.symbol,
            row.metric,
            row.price,
            row.note,
            heat.high ? `综合热度 ${heat.score}` : ""
          ].filter(Boolean).join(" / "),
          url: row.url,
          time: row.date || 0,
          priority: heat.high ? "高热重点" : row.group === "crypto" ? "交易所上新" : "上市信息",
          tags: [...(row.tags || []), heat.high ? "高热" : ""].filter(Boolean),
          market: true
        };
      })
    );
  }

  function parseNewsflashEvents(payload) {
    const items = Array.isArray(payload?.items) ? payload.items : [];
    return items.map((item) => ({
      key: stableKey("flash", item.id, item.title, item.add_time),
      kind: "律动快讯",
      source: "BlockBeats",
      sourceLabel: "BB",
      title: item.title || "市场快讯",
      body: item.content || "",
      url: item.url || "https://www.theblockbeats.info/newsflash",
      time: item.add_time,
      priority: "市场信息"
    }));
  }

  function parseSignedNumber(value) {
    return Number.parseFloat(String(value || "0").replace("%", "").replace("+", "")) || 0;
  }

  function parseMarketSignals(payload) {
    const sources = Array.isArray(payload?.sources) ? payload.sources : [];
    return sources
      .filter((source) => source?.status !== "unavailable")
      .map((source) => {
        const rows = Array.isArray(source.rows) ? source.rows : [];
        const leader = rows[0];
        if (!leader) return null;
        const change = parseSignedNumber(leader.change);
        const symbol = leader.symbol || leader.name || "--";
        return {
          key: stableKey("market-leader", source.id, symbol, leader.rank),
          kind: "榜首换手",
          source: source.title || source.sourceName || "Market",
          sourceLabel: source.sourceLabel || "MR",
          title: `${source.sourceLabel || "榜单"} 榜首变为 ${symbol}`,
          body: [leader.name, leader.price, leader.change, leader.turnover || leader.note].filter(Boolean).join(" / "),
          url: "./index.html",
          time: source.updatedAt || payload.updatedAt,
          priority: Math.abs(change) >= 8 ? "资金切换" : "榜首换手",
          market: true
        };
      })
      .filter(Boolean);
  }

  function amountFromText(value) {
    const text = String(value || "").replace(/,/g, "").trim();
    const match = text.match(/[-+]?\d+(?:\.\d+)?/);
    if (!match) return 0;
    let amount = Number.parseFloat(match[0]) || 0;
    if (/[亿B]/i.test(text)) amount *= 100_000_000;
    else if (/[万]/.test(text)) amount *= 10_000;
    else if (/M/i.test(text)) amount *= 1_000_000;
    else if (/K/i.test(text)) amount *= 1_000;
    return amount;
  }

  function newboardHeatInfo(row, rank = 99) {
    const amount = Number(row.amount) || amountFromText(row.turnover || row.metric || row.note);
    const heat = Number(row.heat) || 0;
    const change = Math.abs(parseSignedNumber(row.change));
    const isIpo = row.group === "ipo" || /ipo|nasdaq|nyse|上市|招股|申购|upcoming|priced/i.test([
      row.group,
      row.status,
      row.source,
      row.sourceLabel,
      row.metric,
      ...(Array.isArray(row.tags) ? row.tags : [])
    ].join(" "));
    const isContractListing = row.group === "crypto" || /perpetual|usdt|will list/i.test([
      row.group,
      row.status,
      row.source,
      row.sourceLabel,
      row.metric,
      row.title,
      row.symbol,
      ...(Array.isArray(row.tags) ? row.tags : [])
    ].join(" "));
    const text = normalizeSearchText([
      row.title,
      row.symbol,
      row.status,
      row.metric,
      row.note,
      ...(Array.isArray(row.tags) ? row.tags : [])
    ].join(" "));
    let score = 0;
    if (rank <= 3) score += 8;
    else if (rank <= 5) score += 4;
    if (amount >= 1_000_000_000) score += 46;
    else if (amount >= 300_000_000) score += 34;
    else if (amount >= 100_000_000) score += 24;
    else if (amount >= 30_000_000) score += 14;
    else if (amount >= 5_000_000) score += 6;
    if (heat >= 90) score += 30;
    else if (heat >= 80) score += 20;
    else if (heat >= 70) score += 10;
    if (change >= 40) score += 18;
    else if (change >= 25) score += 10;
    else if (change >= 15) score += 5;
    if (/高热|热门|热度|讨论|关注|超购|融资|首日|ipo|新币/.test(text)) score += 12;
    if (isContractListing) {
      if (/binance|okx|bitget/.test(text)) score += 18;
      if (/perpetual|will list|usdt/.test(text)) score += 18;
      if (/ai|meme|memecoin|defi|rwa|gamefi|launchpool/.test(text)) score += 18;
    }
    if (isIpo) {
      if (amount >= 1_000_000_000) score += 40;
      else if (amount >= 300_000_000) score += 32;
      else if (amount >= 100_000_000) score += 22;
      else if (amount >= 50_000_000) score += 14;
      else if (amount >= 20_000_000) score += 8;
      if (/ai|人工智能|智能|芯片|半导体|robot|机器人|digital|infrastructure|crypto|bitcoin|区块链|web3|cerebras|blackstone/.test(text)) score += 28;
      if (/global|select|nasdaq|nyse|首发|申购|招股|upcoming|priced/.test(text)) score += 10;
    }
    const threshold = isIpo ? 112 : isContractListing ? 60 : 78;
    return {
      high: score >= threshold,
      score: Math.min(100, Math.round(score)),
      amount
    };
  }

  function parseNewboardEvents(payload) {
    const sections = Array.isArray(payload?.sections) ? payload.sections : [];
    return sections.flatMap((section) =>
      (section.rows || []).map((row, rowIndex) => {
        const rank = Number(row.rank || rowIndex + 1);
        const heat = newboardHeatInfo(row, rank);
        const symbol = row.symbol || row.asset || row.title || row.name || "新标的";
        const key = stableKey("newboard", section.id, row.id, symbol, row.date, row.url);
        const title = heat.high
          ? `高热提醒：${section.title || "新币新股"} ${symbol}`
          : `${section.title || "新币新股"} ${symbol}`;
        return {
          key,
          kind: heat.high ? "新币新股高热" : "新币新股",
          source: row.source || section.sourceName || section.title || "新币新股榜",
          sourceLabel: row.sourceLabel || section.sourceLabel || "NEW",
          title,
          body: [
            row.price ? `价格 ${row.price}` : "",
            row.change ? `涨跌 ${row.change}` : "",
            row.turnover || row.metric || "",
            heat.high ? `综合热度 ${heat.score}` : ""
          ].filter(Boolean).join(" / "),
          url: row.url || "./newboards.html",
          time: row.date || payload.updatedAt,
          priority: heat.high ? "高热重点" : "新币新股",
          tags: [...(row.tags || []), heat.high ? "高热" : ""].filter(Boolean),
          market: true
        };
      })
    );
  }

  function parseBriefEvents(payload) {
    const briefs = Array.isArray(payload?.briefs) ? payload.briefs : [];
    return briefs
      .filter((brief) => brief?.completedAt)
      .map((brief) => ({
        key: stableKey("brief", brief.id, brief.completedAt, brief.name),
        kind: "自动简报",
        source: brief.name || "自动化简报",
        sourceLabel: "AI",
        title: brief.name || "新的自动化简报",
        body: stripText(brief.content || "", 220),
        url: "./briefs.html",
        time: brief.completedAt,
        priority: "简报"
      }));
  }

  function ensureStack() {
    if (state.stack) return state.stack;
    const stack = document.createElement("section");
    stack.className = "market-alert-stack";
    stack.setAttribute("aria-live", "polite");
    stack.setAttribute("aria-label", "星云社实时弹窗");
    document.body.appendChild(stack);
    state.stack = stack;
    return stack;
  }

  function ensureAudioContext() {
    if (state.muted) return null;
    const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextCtor) return null;
    if (!state.audioContext) {
      state.audioContext = new AudioContextCtor();
    }
    if (state.audioContext.state === "suspended") {
      state.audioContext.resume().catch(() => {});
    }
    return state.audioContext;
  }

  function playAlertSound() {
    const context = ensureAudioContext();
    if (!context || state.muted || context.state === "suspended") return;

    const now = context.currentTime;
    const master = context.createGain();
    master.gain.setValueAtTime(0.0001, now);
    master.gain.exponentialRampToValueAtTime(0.08, now + 0.025);
    master.gain.exponentialRampToValueAtTime(0.0001, now + 0.42);
    master.connect(context.destination);

    [784, 1175].forEach((frequency, index) => {
      const start = now + index * 0.13;
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = "sine";
      oscillator.frequency.setValueAtTime(frequency, start);
      oscillator.frequency.exponentialRampToValueAtTime(frequency * 1.08, start + 0.16);
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(0.7, start + 0.018);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.23);
      oscillator.connect(gain);
      gain.connect(master);
      oscillator.start(start);
      oscillator.stop(start + 0.25);
    });
  }

  function bindAudioUnlock() {
    const unlock = () => ensureAudioContext();
    window.addEventListener("pointerdown", unlock, { once: true, passive: true });
    window.addEventListener("keydown", unlock, { once: true });
  }

  function syncSoundButtons() {
    document.querySelectorAll("[data-alert-sound]").forEach((node) => {
      node.textContent = state.muted ? "静音" : "响铃";
      node.setAttribute("aria-label", state.muted ? "打开弹窗音效" : "关闭弹窗音效");
    });
  }

  function toggleMute(button) {
    state.muted = !state.muted;
    localStorage.setItem(MUTED_KEY, state.muted ? "1" : "0");
    syncSoundButtons();
    if (!state.muted) {
      ensureAudioContext();
      playAlertSound();
    }
    button?.blur();
  }

  function dismissToast(toast) {
    if (!toast || toast.dataset.closing === "1") return;
    toast.dataset.closing = "1";
    toast.classList.add("is-leaving");
    window.setTimeout(() => toast.remove(), 260);
  }

  function showAlert(rawItem) {
    const item = normalizeAlertItem(rawItem);
    if (!isMarketRelated(item)) return false;
    if (!isFreshForAlert(item)) return false;
    const keys = alertDedupeKeys(item);
    if (keys.some((key) => state.pendingDesktop.has(key))) return false;
    keys.forEach((key) => state.pendingDesktop.add(key));
    sendDesktopAlert(item).finally(() => {
      window.setTimeout(() => keys.forEach((key) => state.pendingDesktop.delete(key)), 30_000);
    });
    return true;
  }

  async function sendDesktopAlert(item) {
    try {
      const alertUrl = item.url ? new URL(item.url, window.location.href).href : "";
      const response = await fetch("/api/desktop-alert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          key: item.key,
          kind: item.kind,
          source: item.source,
          sourceLabel: item.sourceLabel,
          title: item.title,
          body: item.body,
          url: alertUrl,
          time: item.time,
          priority: item.priority,
          clientMode: window.XingyunDesktop?.platform || "web",
          sound: !state.muted
        })
      });
      return response.ok;
    } catch (error) {
      console.warn("Desktop alert failed", error);
      return false;
    }
  }

  async function fetchPayload(feed) {
    const response = await fetch(feed.url, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const contentType = response.headers.get("content-type") || "";
    const looksLikeRss = feed.type === "rss" || /xml|rss|atom/i.test(contentType);
    if (looksLikeRss) {
      return parseRssText(await response.text(), feed);
    }
    return response.json();
  }

  function prepareFeed(feed) {
    if (!feed?.name || !feed?.url) {
      throw new Error("Alert feed requires name and url.");
    }
    return {
      interval: 60_000,
      parse: parseGenericEvents,
      ...feed
    };
  }

  async function syncFeed(feed) {
    try {
      const payload = await fetchPayload(feed);
      const items = feed.parse(payload, feed)
        .map((item) => normalizeAlertItem(item, feed))
        .filter((item) => item?.key && item?.title && isMarketRelated(item))
        .sort((a, b) => eventTime(a.time) - eventTime(b.time));

      const isFirstRun = !state.feedReady.has(feed.name);
      const hasKnownHistory = items.some((item) => hasSeen(item));
      const fresh = items.filter((item) => !hasSeen(item) && isFreshForAlert(item));
      items.forEach(markSeen);
      saveSeen();

      if (isFirstRun) {
        state.feedReady.add(feed.name);
        if (!hasKnownHistory) return;
      }

      fresh.forEach(showAlert);
    } catch (error) {
      console.warn(`Alert feed failed: ${feed.name}`, error);
    }
  }

  function clearFeedTimer(feed) {
    if (feed.firstTimer) {
      window.clearTimeout(feed.firstTimer);
      feed.firstTimer = null;
    }
    if (feed.timer) {
      window.clearInterval(feed.timer);
      feed.timer = null;
    }
  }

  function clearAllFeedTimers() {
    state.feeds.forEach(clearFeedTimer);
  }

  function scheduleFeed(feed, index = 0) {
    clearFeedTimer(feed);
    feed.firstTimer = window.setTimeout(() => {
      syncFeed(feed);
      feed.timer = window.setInterval(() => syncFeed(feed), feed.interval);
    }, FIRST_SYNC_DELAY + index * FEED_STAGGER_MS);
  }

  function scheduleDefaultFeeds() {
    [...state.feeds.values()].forEach((feed, index) => scheduleFeed(feed, index));
  }

  function readLeader() {
    try {
      const value = JSON.parse(localStorage.getItem(LEADER_KEY) || "null");
      return value && typeof value === "object" ? value : null;
    } catch {
      return null;
    }
  }

  function writeLeader() {
    localStorage.setItem(
      LEADER_KEY,
      JSON.stringify({
        id: LEADER_ID,
        expiresAt: Date.now() + LEADER_TTL
      })
    );
  }

  function canClaimLeadership() {
    const leader = readLeader();
    return !leader?.id || leader.id === LEADER_ID || Number(leader.expiresAt || 0) < Date.now();
  }

  function becomeLeader() {
    if (state.serverMonitorActive) return;
    if (!canClaimLeadership()) return;
    writeLeader();
    if (!state.isLeader) {
      state.isLeader = true;
      scheduleDefaultFeeds();
    }
  }

  function resignLeader() {
    if (!state.isLeader && !state.leaderTimer) return;
    state.isLeader = false;
    clearAllFeedTimers();
    const leader = readLeader();
    if (leader?.id === LEADER_ID) {
      localStorage.removeItem(LEADER_KEY);
    }
  }

  function electLeader() {
    if (state.serverMonitorActive) {
      resignLeader();
      return;
    }
    if (canClaimLeadership()) {
      becomeLeader();
    } else if (state.isLeader) {
      state.isLeader = false;
      clearAllFeedTimers();
    }
  }

  function startClientPollingFallback() {
    electLeader();
    if (state.leaderTimer) window.clearInterval(state.leaderTimer);
    state.leaderTimer = window.setInterval(() => {
      if (state.isLeader) writeLeader();
      electLeader();
    }, LEADER_CHECK_MS);
  }

  async function detectServerMonitor() {
    try {
      const response = await fetch(SERVER_MONITOR_STATUS_URL, { cache: "no-store" });
      if (!response.ok) return false;
      const payload = await response.json();
      return payload?.active === true;
    } catch {
      return false;
    }
  }

  function registerFeed(feed) {
    const prepared = prepareFeed(feed);
    const existing = state.feeds.get(prepared.name);
    if (existing) clearFeedTimer(existing);
    state.feeds.set(prepared.name, prepared);
    if (state.started && state.isLeader) scheduleFeed(prepared, state.feeds.size - 1);
    return prepared;
  }

  function notify(rawItem) {
    const item = normalizeAlertItem(rawItem);
    if (!item.key) item.key = stableKey("manual", item.source, item.title, item.time);
    if (hasSeen(item)) return false;
    if (!isMarketRelated(item)) return false;
    if (!isFreshForAlert(item)) return false;
    markSeen(item);
    saveSeen();
    return showAlert(item);
  }

  function refresh(name) {
    if (name) {
      const feed = state.feeds.get(name);
      if (feed) return syncFeed(feed);
      return Promise.resolve(false);
    }
    return Promise.all([...state.feeds.values()].map(syncFeed));
  }

  function startNotifier() {
    state.started = true;
    DEFAULT_FEEDS.forEach(registerFeed);

    window.MarketAlertCenter = {
      registerFeed,
      notify,
      refresh,
      isMarketRelated,
      parseGenericEvents,
      parseRssText
    };

    window.showMarketAlertDemo = () =>
      notify({
        key: `demo:${Date.now()}`,
        kind: "交易所上新",
        source: "星云社",
        sourceLabel: "NX",
        title: "示例弹窗：新币上线与市场快讯会在这里出现",
        body: "弹窗会自动播放提示音，并保留查看详情入口。",
        url: "./listings.html",
        time: Date.now(),
        priority: "测试",
        market: true
      });

    if (new URLSearchParams(window.location.search).has("alertDemo")) {
      window.setTimeout(() => window.showMarketAlertDemo(), 900);
    }

    window.addEventListener("storage", (event) => {
      if (event.key === LEADER_KEY) electLeader();
    });
    window.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") electLeader();
    });
    window.addEventListener("pagehide", resignLeader);

    detectServerMonitor().then((active) => {
      state.serverMonitorActive = active;
      if (active) {
        clearAllFeedTimers();
        return;
      }
      startClientPollingFallback();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startNotifier, { once: true });
  } else {
    startNotifier();
  }
})();
