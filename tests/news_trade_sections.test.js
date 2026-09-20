const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const sections = require("../news-trade-sections.js");
const source = fs.readFileSync(path.join(__dirname, "../price-watch.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "../price-watch.html"), "utf8");

function rows(template, count, extra = {}) {
  return Array.from({ length: count }, (_, index) => ({
    id: `${template}-${index}`, template, timestamp: 1788870000000 - index * 60000, ...extra,
  }));
}

test("every deduplicated opportunity belongs to exactly one section without losing metadata", () => {
  const items = [
    { id: "a", template: "meme-catalyst", mergedKind: "news-trade", mergedDuplicateCount: 5 },
    { id: "b", template: "listing-latency", mergedKind: "news-trade" },
    { id: "c", template: "listing-latency", mergedKind: "event" },
    { id: "d", template: "anchor-policy" },
    { id: "e", template: "future-new-type" },
    { id: "f", template: "project-x-meme" },
    { id: "g", template: "counter-consensus-culture" },
  ].map(Object.freeze);
  const grouped = sections.group(Object.freeze(items));
  assert.deepEqual(grouped.meme.map(row => row.id), ["a", "f", "g"]);
  assert.deepEqual(grouped.exchange.map(row => row.id), ["b", "c"]);
  assert.deepEqual(grouped.catalyst.map(row => row.id), ["d", "e"]);
  assert.deepEqual(Object.values(grouped).flat().map(row => row.id).sort(), items.map(row => row.id).sort());
  assert.equal(grouped.meme[0], items[0]);
  assert.equal(grouped.meme[0].mergedDuplicateCount, 5);
});

test("all sections sort by latest published message before pagination, never score or AI refresh", () => {
  for (const { key } of sections.sections) {
    const template = { meme: "meme-catalyst", exchange: "listing-latency", catalyst: "anchor-policy" }[key];
    const old = Object.freeze({ id: "old", template, timestamp: 1788850000000,
      topicScore: 99, updatedAt: 1788880000000, aiAnalysisUpdatedAt: 1788880000000 });
    const recent = Object.freeze({ id: "recent", template, timestamp: 1788860000000, topicScore: 50 });
    const latest = Object.freeze({ id: "latest", template, timestamp: 1788870000000, topicScore: 10, sourceActive: false });
    const input = Object.freeze([old, latest, recent]);
    const view = sections.createView({ section: key, pageSize: 2, storage: null });
    assert.deepEqual(view.snapshot(input).visibleItems, [latest, recent]);
    view.setPage(2, input);
    assert.deepEqual(view.snapshot(input).visibleItems, [old]);
    assert.deepEqual(input, [old, latest, recent]);
  }
});

test("a genuinely newer catalyst advances the topic, observation and analysis timestamps do not", () => {
  const base = 1788850000000;
  const topic = { id: "topic", timestamp: base, firstSeenAt: base + 1, enteredAt: base + 2,
    updatedAt: base + 9000, lastSeenAt: base + 9000, aiAnalysisUpdatedAt: base + 9000,
    informationSources: [{ capturedAt: base + 9000, publishedAt: base }] };
  assert.equal(sections.eventTime(topic), base);
  const newer = { id: "newer", timestamp: base + 1000 };
  assert.deepEqual(sections.newestFirst([topic, newer]).map(row => row.id), ["newer", "topic"]);
  for (const update of [
    { latestCatalyst: { timestamp: base + 2000 } },
    { relatedNews: [{ timestamp: base + 2000 }] },
    { informationSources: [{ publishedAt: base + 2000, capturedAt: base + 9000 }] },
  ]) {
    assert.equal(sections.eventTime({ ...topic, ...update }), base + 2000);
    assert.deepEqual(sections.newestFirst([{ ...topic, ...update }, newer]).map(row => row.id), ["topic", "newer"]);
  }
});

test("timestamps accept seconds, milliseconds and ISO; missing times stay last and ties are deterministic", () => {
  const time = Date.parse("2026-09-08T12:00:00Z");
  assert.equal(sections.eventTime({ timestamp: time / 1000 }), time);
  assert.equal(sections.eventTime({ timestamp: String(time) }), time);
  assert.equal(sections.eventTime({ publishedAt: "2026-09-08T12:00:00Z" }), time);
  assert.equal(sections.eventTime({ timestamp: "invalid", firstSeenAt: time }), time);
  assert.equal(sections.eventTime({ firstSeenAt: null, enteredAt: time }), time);
  for (const timestamp of [null, undefined, "", "invalid", -1, 0, NaN, Infinity, true, {}, []]) {
    assert.equal(sections.eventTime({ timestamp, updatedAt: time, aiAnalysisUpdatedAt: time }), 0);
  }
  const items = [{ id: "b", timestamp: time }, { id: "a", timestamp: time }, { id: "missing" }];
  assert.deepEqual(sections.newestFirst(items).map(row => row.id), ["a", "b", "missing"]);
  assert.deepEqual(sections.newestFirst([...items].reverse()).map(row => row.id), ["a", "b", "missing"]);
});

test("exchange names in titles do not classify memes as announcements; official listings take precedence", () => {
  assert.equal(sections.sectionFor({ template: "meme-catalyst", title: "Binance 吉祥物新 MEME", source: "Binance" }), "meme");
  assert.equal(sections.sectionFor({ template: "meme-catalyst", sourceType: "listing" }), "exchange");
  assert.equal(sections.sectionFor({ sourceType: "listing", title: "交易安排调整" }), "exchange");
  assert.equal(sections.sectionFor({ eventType: "交易所公告延迟套利" }), "exchange");
  assert.equal(sections.sectionFor({ templateName: "热点叙事 MEME 映射" }), "meme");
  assert.equal(sections.sectionFor({ title: "交易所公告", template: "instant-repricing" }), "catalyst");
  assert.equal(sections.sectionFor({ mergedKind: "news-trade", template: "kol-latency" }), "catalyst");
});

test("each section paginates its own full feed, including non-News-Trade announcements", () => {
  const items = [...rows("meme-catalyst", 15), ...rows("listing-latency", 23, { mergedKind: "event" })];
  const view = sections.createView({ storage: null });
  view.setPage(2, items);
  assert.equal(view.snapshot(items).visibleItems.length, 5);
  assert.equal(view.select("exchange"), true);
  view.setPage(3, items);
  let snapshot = view.snapshot(items);
  assert.deepEqual(snapshot.counts, { meme: 15, exchange: 23, catalyst: 0 });
  assert.equal(snapshot.pageCount, 3);
  assert.equal(snapshot.visibleItems[0].id, "listing-latency-20");
  assert.equal(snapshot.visibleItems.length, 3);
  view.select("meme");
  assert.equal(view.snapshot(items).page, 2);
  view.select("exchange");
  assert.equal(view.snapshot(items).page, 3);
});

test("background refresh preserves section and page, only clamping pages when data shrinks", () => {
  const view = sections.createView({ section: "exchange", storage: null });
  view.setPage(3, rows("listing-latency", 23));
  let snapshot = view.snapshot(rows("listing-latency", 24));
  assert.equal(snapshot.section, "exchange");
  assert.equal(snapshot.page, 3);
  assert.equal(view.snapshot(rows("listing-latency", 12)).page, 2);
  snapshot = view.snapshot([]);
  assert.equal(snapshot.section, "exchange");
  assert.equal(snapshot.page, 1);
  assert.equal(snapshot.total, 0);
  assert.deepEqual(snapshot.visibleItems, []);
  assert.equal(view.select("not-a-section"), false);
  assert.equal(view.snapshot([]).section, "exchange");
});

test("selected subsection survives reload, explicit URL wins, and denied storage is harmless", () => {
  const values = new Map();
  const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) };
  const view = sections.createView({ storage });
  assert.equal(view.snapshot([]).section, "meme");
  view.select("exchange");
  assert.equal(sections.createView({ storage }).snapshot([]).section, "exchange");
  assert.equal(sections.createView({ section: "catalyst", storage }).snapshot([]).section, "catalyst");
  assert.equal(sections.createView({ section: "invalid", storage }).snapshot([]).section, "exchange");
  const blocked = { getItem() { throw Error("denied"); }, setItem() { throw Error("denied"); } };
  const privateView = sections.createView({ storage: blocked });
  assert.equal(privateView.select("catalyst"), true);
  assert.equal(privateView.snapshot([]).section, "catalyst");
});

function declaration(name) {
  const start = source.indexOf(`  function ${name}(`);
  assert.ok(start >= 0, `${name} must exist`);
  const next = source.slice(start + 1).search(/\n  (?:async )?function /);
  assert.ok(next >= 0);
  return source.slice(start, start + 1 + next);
}

function renderer(items, loaded = true, section = "meme") {
  const calls = [], analyses = [];
  const context = {
    newsTradeSections: sections,
    newsTradeView: sections.createView({ section, storage: null }),
    mergedEventItems: items,
    eventLoaded: loaded,
    escapeHtml: value => String(value),
    grid: { classList: { add() {}, remove() {} } },
    renderGrid: markup => { context.markup = markup; },
    newsTradeSearchTemplate: () => '<section data-search>搜索</section>',
    newsTradeExecutionNoticeTemplate: () => '<section data-notice>买入状态</section>',
    eventMonitorCardTemplate: (item, newsMode) => { calls.push({ id: item.id, newsMode }); return `<article data-card="${item.id}"></article>`; },
    requestVisibleNewsTradeAi: items => analyses.push(items.map(item => item.id)),
  };
  vm.createContext(context);
  for (const name of ["newsTradeSubnavTemplate", "newsTradePaginationTemplate", "renderEventMonitor"]) {
    vm.runInContext(declaration(name), context);
  }
  context.renderEventMonitor();
  return { context, calls, analyses };
}

test("real renderer shows separate categories and only requests AI for the current visible enriched cards", async () => {
  const items = [...rows("meme-catalyst", 12, { mergedKind: "news-trade" }), ...rows("listing-latency", 23, { mergedKind: "event" })];
  const { context, calls, analyses } = renderer(items);
  await Promise.resolve();
  assert.equal(calls.length, 10);
  assert.ok(calls.every(row => row.id.startsWith("meme-catalyst") && row.newsMode));
  assert.equal(analyses[0].length, 10);
  assert.match(context.markup, /chain-research-subnav news-trade-subnav/);
  assert.match(context.markup, /data-news-section="meme" aria-pressed="true"/);
  assert.match(context.markup, /<span>事件 MEME<\/span><em>12<\/em>/);
  assert.match(context.markup, /<span>交易所公告<\/span><em>23<\/em>/);
  assert.doesNotMatch(context.markup, /data-card="listing-latency/);
  context.newsTradeView.select("exchange");
  context.newsTradeView.setPage(3, items);
  calls.length = 0;
  context.renderEventMonitor();
  await Promise.resolve();
  assert.equal(calls.length, 3);
  assert.ok(calls.every(row => row.id.startsWith("listing-latency") && !row.newsMode));
  assert.equal(analyses.at(-1).length, 0);
  assert.match(context.markup, /交易所公告 · 第 3 \/ 3 页 · 共 23 条去重机会/);
  assert.match(context.markup, /data-search/);
  assert.match(context.markup, /data-notice/);
});

test("real renderer shows newest first even when the server supplies score or refresh order", () => {
  const items = rows("meme-catalyst", 12, { mergedKind: "news-trade" }).reverse();
  const { calls } = renderer(items);
  assert.deepEqual(calls.map(row => row.id), Array.from({ length: 10 }, (_, index) => `meme-catalyst-${index}`));
});

test("loading and empty subsections keep navigation and shared actions available", () => {
  const loading = renderer([], false, "exchange");
  assert.match(loading.context.markup, /正在核对/);
  assert.match(loading.context.markup, /data-news-section="exchange" aria-pressed="true"/);
  assert.match(loading.context.markup, /<em>—<\/em>/);
  const empty = renderer(rows("meme-catalyst", 2), true, "exchange");
  assert.match(empty.context.markup, /当前没有可展示的交易所公告机会/);
  assert.match(empty.context.markup, /data-news-section="meme"/);
  assert.match(empty.context.markup, /data-search/);
  assert.equal(empty.calls.length, 0);
});

test("page wires section state before use, preserves URL context, and paginates the merged feed", () => {
  assert.ok(html.indexOf("news-trade-sections.js") < html.indexOf("price-watch.js"));
  assert.match(source, /url.searchParams.set\("newsView", section\)/);
  assert.match(source, /newsTradeView.setPage\(newsTradePageButton.dataset.newsTradePage, mergedEventItems\)/);
  assert.doesNotMatch(source, /Math.ceil\(newsTradeItems.length \/ NEWS_TRADE_PAGE_SIZE\)/);
  assert.doesNotMatch(source, /okxWalletToolbarTemplate|data-wallet-panel-toggle/);
});

test("actual News Trade card keeps analysis and risk behind a closed detail section", () => {
  const context = {
    newsTradeSections: sections,
    escapeHtml: value => String(value), safeExternalUrl: value => /^https?:/.test(value || "") ? value : "",
    monitorBuyButton: row => `<button data-buy="${row.symbol}">买入</button>`,
    newsTradePhaseMeta: () => ({ code: "understanding", label: "理解阶段" }),
    relativeTime: value => `time:${value}`, compactUsd: () => "$10",
    newsTradeScoreTagsTemplate: () => "评分标签",
    newsTradeIntelligenceTemplate: () => "底层指标",
    newsTradeTargetsTemplate: () => "其他映射",
  };
  vm.createContext(context);
  vm.runInContext(declaration("eventMonitorCardTemplate"), context);
  const item = { id: "topic", title: "长篇英文原文不应抢占主界面", assets: ["ALPHA"], sourceActive: true,
    enteredAt: 1788850000000, timestamp: 1788850000000, latestCatalyst: { timestamp: 1788870000000 },
    aiAnalysisStatus: "ready", aiAnalysis: { verdict: "trade-candidate", confidence: 80, primarySymbol: "ALPHA",
      thesis: "有明确的新催化", catalyst: "事件依据", risk: "低流动性", actionHint: "行动规则" } };
  const markup = context.eventMonitorCardTemplate(item, true);
  const summary = markup.slice(0, markup.indexOf("<details"));
  assert.match(summary, />值得看</);
  assert.match(summary, /<h3>ALPHA<\/h3>/);
  assert.match(summary, /有明确的新催化/);
  assert.match(summary, /消息 time:1788870000000/);
  assert.match(markup, /入池 time:1788850000000/);
  assert.doesNotMatch(summary, /评分标签|底层指标|低流动性|行动规则|长篇英文/);
  assert.match(markup, /<details class="news-focus-details" data-research-detail="news:topic">/);
  assert.match(markup, /低流动性/);
  assert.match(context.eventMonitorCardTemplate({ ...item, aiAnalysisStatus: "pending" }, true), />待确认</);
  assert.match(context.eventMonitorCardTemplate({ ...item, aiAnalysis: { ...item.aiAnalysis, verdict: "reject" } }, true), />不值得看</);
});
