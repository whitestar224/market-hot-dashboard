(() => {
  const API_URL = "/api/ai/rank-insights";
  const CACHE_KEY = "xingyun:deepseek-rank-insights:v6";
  const CACHE_TTL_MS = 12 * 60 * 60 * 1000;
  const CACHE_LIMIT = 700;
  const state = {
    insights: new Map(),
    pending: false,
    lastSignature: "",
    disabledUntil: 0
  };

  function stableText(value, limit = 120) {
    return String(value ?? "").replace(/\s+/g, " ").trim().slice(0, limit);
  }

  const EMPTY_INSIGHT_RE =
    /(高热关注|高热上榜|高热度上榜|高热.{0,4}上榜|热度榜首|热度上榜|热门币种|局部异动|成交活跃|成交放量|成交平稳|成交普通|成交密集|成交密度|成交额大|成交额较大|成交额巨大|成交额放大|成交额靠前|成交额领先|成交占优|成交排名|成交榜|成交量大|成交量较大|资金密集|流动性一般|价格波动|短线波动|温和上涨|小幅波动|强势领涨|领涨|补涨|赛道上扬|走强|走高|拉升|冲高|市场热度|板块轮动|榜单异动|资金关注|交易所热度|合约交易|合约标的|合约博弈|合约热炒|合约热度|合约情绪|合约密集|合约拥挤|合约活跃|合约热|热炒|热点炒作|炒作热度|涨幅惊人|涨幅可观|涨幅客观|涨幅明显|涨幅扩大|涨幅较大|涨幅\s*[xXｘＸ]+%|涨幅.{0,6}[+\-]?\d+(?:\.\d+)?%|24h\s*涨幅|24小时\s*涨幅|\d+(?:\.\d+)?%\s*涨幅)/i;

  function isEmptyInsight(value) {
    return EMPTY_INSIGHT_RE.test(stableText(value, 80).replace(/\s+/g, ""));
  }

  function rowKey(row, source, mode) {
    const sourceId = stableText(source?.id || row?.sourceId || source?.title || source?.sourceName, 80).toLowerCase();
    const symbol = stableText(row?.symbol || row?.asset || row?.code || row?.name, 80).toLowerCase();
    const name = stableText(row?.name || row?.title || row?.symbol, 80).toLowerCase();
    const url = stableText(row?.targetUrl || row?.url || "", 140).toLowerCase();
    return [mode || "hot", sourceId, symbol, name, url].filter(Boolean).join("|");
  }

  function compactSources(sources, mode) {
    return (Array.isArray(sources) ? sources : [])
      .filter(Boolean)
      .map((source) => {
        const rows = (Array.isArray(source.rows) ? source.rows : []).slice(0, 10).map((row, index) => ({
          key: rowKey(row, source, mode),
          rank: row.rank || index + 1,
          symbol: stableText(row.symbol || row.asset || row.code || row.name, 50),
          name: stableText(row.name || row.title || row.symbol, 90),
          price: stableText(row.price || row.metric || row.metricLabel, 50),
          change: stableText(row.change, 40),
          turnover: stableText(row.turnover || row.amount || row.note, 70),
          heat: Number(row.heat || row.heatScore || row.score || 0) || 0,
          note: stableText(row.note, 130),
          tags: Array.isArray(row.tags) ? row.tags.slice(0, 6).map((tag) => stableText(tag, 40)) : []
        }));
        return {
          id: stableText(source.id || source.sourceId || source.title, 80),
          title: stableText(source.title, 90),
          group: stableText(source.group, 30),
          sourceName: stableText(source.sourceName || source.subtitle, 120),
          sourceLabel: stableText(source.sourceLabel, 30),
          rows
        };
      })
      .filter((source) => source.rows.length);
  }

  function signatureFor(payload) {
    return JSON.stringify(payload);
  }

  function normalizeInsight(item) {
    if (!item || !item.detail) return null;
    if (isEmptyInsight(item.reason) || isEmptyInsight(item.theme) || isEmptyInsight(item.detail)) return null;
    return {
      reason: stableText(item.reason, 24),
      theme: stableText(item.theme, 30),
      detail: stableText(item.detail, 34),
      tone: item.tone === "is-hot" ? "is-hot" : "",
      provider: item.provider || "deepseek"
    };
  }

  function loadPersistedInsights() {
    try {
      const raw = localStorage.getItem(CACHE_KEY);
      if (!raw) return;
      const payload = JSON.parse(raw);
      const items = payload?.items && typeof payload.items === "object" ? payload.items : {};
      const now = Date.now();
      Object.entries(items).forEach(([key, item]) => {
        const updatedAt = Number(item?.updatedAt || payload?.updatedAt || 0);
        if (!updatedAt || now - updatedAt > CACHE_TTL_MS) return;
        const insight = normalizeInsight(item?.insight || item);
        if (!insight) return;
        state.insights.set(key, insight);
      });
    } catch (error) {
      localStorage.removeItem(CACHE_KEY);
    }
  }

  function persistInsights() {
    try {
      const entries = Array.from(state.insights.entries()).slice(-CACHE_LIMIT);
      const now = Date.now();
      const items = {};
      entries.forEach(([key, insight]) => {
        const normalized = normalizeInsight(insight);
        if (normalized) items[key] = { updatedAt: now, insight: normalized };
      });
      localStorage.setItem(CACHE_KEY, JSON.stringify({ version: 3, updatedAt: now, items }));
    } catch (error) {
      console.warn("Persist DeepSeek rank insights failed", error);
    }
  }

  function shouldDeferFallback(row, context = {}) {
    const source = context.source || row?.source || {};
    const mode = context.mode || "hot";
    const text = [
      mode,
      source?.id,
      source?.group,
      source?.title,
      source?.subtitle,
      source?.sourceName,
      source?.sourceLabel
    ]
      .filter(Boolean)
      .join(" ");
    return /crypto|aicoin|dex|binance|okx|bitget|futu|ths|10jqka|币圈|合约|链上|港股|美股|A股|同花顺|富途|热门|涨幅|成交额|新币|新股|IPO/i.test(text);
  }

  async function requestForSources(sources, options = {}) {
    if (Date.now() < state.disabledUntil || state.pending) return;
    const mode = options.mode || "hot";
    const compact = compactSources(sources, mode);
    if (!compact.length) return;
    const payload = { mode, sources: compact };
    const signature = signatureFor(payload);
    if (signature === state.lastSignature) return;
    state.pending = true;
    state.lastSignature = signature;
    try {
      const response = await fetch(API_URL, {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false || data.enabled === false) {
        state.disabledUntil = Date.now() + 5 * 60 * 1000;
        return;
      }
      const insights = data.insights && typeof data.insights === "object" ? data.insights : {};
      let changed = false;
      compact.forEach((source) => {
        source.rows.forEach((row) => {
          if (state.insights.delete(row.key)) changed = true;
        });
      });
      Object.entries(insights).forEach(([key, value]) => {
        const insight = normalizeInsight(value);
        if (!insight) return;
        state.insights.set(key, insight);
        changed = true;
      });
      if (changed) persistInsights();
      if (changed && typeof options.onUpdate === "function") options.onUpdate();
    } catch (error) {
      state.disabledUntil = Date.now() + 90_000;
      console.warn("DeepSeek rank insights failed", error);
    } finally {
      state.pending = false;
    }
  }

  function getRowInsight(row, context = {}) {
    const key = rowKey(row, context.source || row?.source || {}, context.mode || "hot");
    const insight = normalizeInsight(state.insights.get(key));
    if (!insight) {
      state.insights.delete(key);
      return null;
    }
    return insight;
  }

  loadPersistedInsights();

  window.XingyunAiInsights = { requestForSources, getRowInsight, rowKey, shouldDeferFallback };
})();
