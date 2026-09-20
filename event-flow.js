(function (root) {
  "use strict";
  const labels = { news: "新消息", analysis: "AI 已研判", "meme-potential": "大 MEME 潜力观察", opportunity: "发现机会", near: "接近前高预警", breakout: "突破前高", queued: "弹窗排队中", starting: "等待窗口显示", displayed: "弹窗已显示 · 未确认已读", dispatched: "弹窗已调起", failed: "显示失败 · 已保留并限次重试", skipped: "未弹窗" };
  const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  function safeUrl(value) {
    try {
      const url = new URL(value || "", "http://localhost/");
      return /^(https?:)$/.test(url.protocol) && value ? String(value) : "";
    } catch (_) { return ""; }
  }
  function matches(item, category) {
    if (category === "opportunity") return Boolean(item.news?.active && (item.news?.opportunity || item.news?.preTokenMeme?.eligible));
    if (category === "news") return Boolean(item.news);
    if (category === "popup") return Boolean(item.popup);
    if (category === "signal") return Boolean(item.signal);
    if (category === "system") return String(item.popup?.kind || "").includes("系统");
    return true;
  }
  function currentOpportunity(item, now = Date.now()) {
    return Boolean(item.news?.active && item.news?.opportunity && item.news?.window?.eligible && Number(item.news.window.expiresAt) > now)
      || Boolean(item.news?.active && item.news?.preTokenMeme?.eligible && Number(item.news.preTokenMeme.expiresAt) > now)
      || Boolean(item.signal?.active && ["near", "breakout"].includes(item.signal.type) && Number(item.signal.expiresAt) > now);
  }
  function decision(item) {
    if (item.signal) return item.signal.type === "breakout" ? "已突破前高" : "接近前高";
    const news = item.news;
    if (!news) return "监控提醒";
    if (!news.active) return "已过时";
    if (news.preTokenMeme?.eligible) return "大 MEME 潜力";
    if (news.opportunity) return "值得看";
    if (news.verdict === "reject") return "不值得看";
    if (news.analysisStatus === "ready") return "先观察，暂不参与";
    return "待确认";
  }
  function createModel() {
    const items = new Map(), aliases = new Map();
    return {
      merge(rows) {
        for (const row of rows || []) {
          const keys = row.identities || [];
          const related = [...new Set([row.id, ...keys.map(key => aliases.get(key))])].filter(id => items.has(id));
          if (related.some(id => Number(items.get(id).revision) > Number(row.revision))) continue;
          for (const id of related) if (id !== row.id) items.delete(id);
          items.set(row.id, row);
          for (const key of keys) aliases.set(key, row.id);
        }
      },
      retain(ids) {
        const active = new Set(ids);
        for (const id of items.keys()) if (!active.has(id)) items.delete(id);
        for (const [key, id] of aliases) if (!active.has(id)) aliases.delete(key);
      },
      rows(category = "all", now = null) { return [...items.values()].filter(item => matches(item, category) && (now === null || currentOpportunity(item, now))).sort((a, b) =>
        Number(b.signal?.priority || b.news?.window?.priority || b.news?.preTokenMeme?.priority || 0) - Number(a.signal?.priority || a.news?.window?.priority || a.news?.preTokenMeme?.priority || 0)
        || Number(b.news?.window?.catalystAt || b.news?.preTokenMeme?.catalystAt || b.activityAt) - Number(a.news?.window?.catalystAt || a.news?.preTokenMeme?.catalystAt || a.activityAt) || b.id - a.id); },
    };
  }
  function timeLabel(value) {
    const date = new Date(Number(value));
    return Number.isFinite(date.getTime()) ? date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "时间待确认";
  }
  function card(item, buyButton = () => "") {
    const data = item.signal || item.news || item.popup || {};
    const preTokenMeme = Boolean(item.news?.preTokenMeme?.eligible);
    const symbol = data.symbol || item.popup?.symbol || "";
    const target = (data.targets || []).find(target => String(target.symbol || "").toUpperCase() === String(symbol).toUpperCase()) || { symbol };
    const sourceUrl = safeUrl(data.url || item.popup?.url);
    const conclusion = !item.signal && item.news && !item.news.verdict ? item.news.reason : data.thesis || data.title;
    const stage = item.popup?.stage;
    const history = (item.history || []).slice(0, 12);
    return `<article class="event-monitor-card news-trade-focus-card" data-live-key="flow:${Number(item.id)}">
      <header class="event-monitor-topline"><span class="news-focus-source">${escapeHtml(data.source || "系统监控")} · ${escapeHtml(timeLabel(item.signal?.triggeredAt || item.news?.window?.catalystAt || item.news?.preTokenMeme?.catalystAt || item.activityAt))}</span><span class="news-focus-verdict ${item.signal || matches(item, "opportunity") ? "is-opportunity" : ""}">${decision(item)}</span></header>
      <div class="event-monitor-main">
        <div class="news-focus-target"><div><span>${preTokenMeme ? "未发币事件" : symbol ? "关联标的" : "事件"}</span><h3${preTokenMeme ? ' class="is-event-title"' : ""}>${escapeHtml(preTokenMeme ? (data.title || "高潜力事件") : symbol || item.popup?.kind || data.title || "标的待确认")}</h3>${preTokenMeme ? `<em class="event-flow-token-state">${escapeHtml(item.news.preTokenMeme.tokenStatusLabel || "尚无可交易标的")}</em>` : ""}</div>${symbol && !preTokenMeme ? buyButton(target) : ""}</div>
        <p class="news-focus-conclusion">${escapeHtml(conclusion || "等待进一步确认。")}</p>
        <details class="news-focus-details" data-flow-detail="${Number(item.id)}"><summary>查看事件进展与依据</summary><div class="news-focus-detail-body">
          <h4>${escapeHtml(data.title || "事件原文")}</h4>
          ${data.actionHint ? `<p>应对：${escapeHtml(data.actionHint)}</p>` : ""}
          ${data.risk ? `<p>风险：${escapeHtml(data.risk)}</p>` : ""}
          ${data.reason ? `<p>播报说明：${escapeHtml(data.reason)}</p>` : ""}
          <ol class="event-flow-history">${history.map(step => `<li><time>${escapeHtml(timeLabel(step.happenedAt))}</time><b>${escapeHtml(labels[step.stage] || step.stage)}</b><p>${escapeHtml(step.summary)}</p></li>`).join("")}</ol>
          ${item.popup?.reason ? `<p>${escapeHtml(item.popup.reason)}</p>` : ""}
        </div></details>
      </div>
      <footer class="event-monitor-footer"><span class="event-flow-delivery ${stage === "failed" ? "is-failed" : ""}">${escapeHtml(labels[stage] || (item.news ? "已归档" : "监控记录"))}</span>${sourceUrl ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">查看来源</a>` : ""}</footer>
    </article>`;
  }
  const api = { createModel, matches, decision, card, safeUrl, currentOpportunity };
  if (typeof module === "object" && module.exports) { module.exports = api; return; }
  root.XingyunEventFlow = api;
  const list = document.querySelector("#eventFlowList");
  if (!list) return;
  const status = document.querySelector("#eventFlowStatus"), count = document.querySelector("#eventFlowCount");
  const older = document.querySelector("#eventFlowOlder");
  let model = createModel(), category = "all", cursor = null, before = null, hasOlder = false;
  let busy = false, generation = 0, controller = null, timer = 0, visibleCount = 30, total = 0, failures = 0;
  let clockOffset = 0;

  function render() {
    const opened = new Set([...list.querySelectorAll("details[open]")].map(node => node.dataset.flowDetail));
    const anchor = root.scrollY > 150 ? [...list.children].find(node => node.getBoundingClientRect().bottom > 0) : null;
    const anchorKey = anchor?.dataset.liveKey, anchorTop = anchor?.getBoundingClientRect().top;
    const rows = model.rows(category, Date.now() + clockOffset);
    const markup = rows.length ? rows.slice(0, visibleCount).map(item => card(item, row => root.MonitorBuyCore?.button(row) || "")).join("") : '<div class="price-watch-empty"><b>暂时没有新的有效机会或行情信号</b><span>继续后台监控。高潜力未发币事件、新催化、前高预警或突破会自动进入；不补旧消息，不凑数量。</span></div>';
    if (root.XingyunLiveDom?.render) root.XingyunLiveDom.render(list, markup);
    else if (list.innerHTML !== markup) list.innerHTML = markup;
    for (const detail of list.querySelectorAll("details[data-flow-detail]")) if (opened.has(detail.dataset.flowDetail)) detail.open = true;
    if (anchorKey) {
      const next = [...list.children].find(node => node.dataset.liveKey === anchorKey);
      if (next) root.scrollBy(0, next.getBoundingClientRect().top - anchorTop);
    }
    count.textContent = rows.length ? `当前值得关注 ${total} 条 · 已显示 ${Math.min(rows.length, visibleCount)} 条` : "";
    older.hidden = !hasOlder && rows.length <= visibleCount;
    older.disabled = busy;
  }
  function schedule(delay = 5000) {
    clearTimeout(timer);
    if (!document.hidden) timer = setTimeout(() => load(), delay);
  }
  async function load({ reset = false, history = false } = {}) {
    if (busy && !reset) return;
    if (reset) { generation++; controller?.abort(); model = createModel(); cursor = null; before = null; hasOlder = false; visibleCount = 30; render(); }
    const requestGeneration = generation;
    const requestController = new AbortController(); controller = requestController;
    clearTimeout(timer); busy = true; older.disabled = true;
    const timeout = setTimeout(() => requestController.abort(), 15000);
    const params = new URLSearchParams({ limit: history || cursor === null ? "30" : "100", category });
    if (history && before) { params.set("beforeTime", before[0]); params.set("beforeId", before[1]); }
    else if (cursor !== null) params.set("after", cursor);
    let followUp = 5000;
    try {
      const response = await fetch(`/api/event-flow?${params}`, { cache: "no-store", signal: requestController.signal });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw Error(payload.error || "事件记录暂不可用");
      if (generation !== requestGeneration) return;
      const initial = cursor === null;
      model.merge(payload.items);
      if (Array.isArray(payload.activeIds)) model.retain(payload.activeIds);
      if (Number.isFinite(payload.serverNow)) clockOffset = payload.serverNow - Date.now();
      if (!history) cursor = payload.cursor;
      if (initial || history) { before = payload.nextBefore; hasOlder = payload.hasMore; }
      if (history) visibleCount += 30;
      if (!initial && !history && payload.hasMore) followUp = 200;
      total = payload.total; failures = 0;
      status.textContent = "实时同步中";
      render();
    } catch (error) {
      if (generation !== requestGeneration) return;
      failures++;
      status.textContent = failures < 3 ? "连接暂缓，正在重连…" : "连接暂缓，保留已有记录并自动重连";
      render(); // Expired windows disappear even while the connection is down.
      followUp = Math.min(30000, failures * 5000);
    } finally {
      clearTimeout(timeout);
      if (generation === requestGeneration) { busy = false; older.disabled = false; schedule(followUp); }
    }
  }
  older.addEventListener("click", () => {
    if (model.rows(category, Date.now() + clockOffset).length > visibleCount) { visibleCount += 30; render(); }
    else load({ history: true });
  });
  document.addEventListener("visibilitychange", () => { if (document.hidden) clearTimeout(timer); else { render(); load(); } });
  load();
})(typeof window !== "undefined" ? window : globalThis);
