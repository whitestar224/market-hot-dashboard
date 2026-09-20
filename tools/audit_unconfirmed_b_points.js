"use strict";

// Offline inventory only. Never fetches candles or changes user decisions.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const zlib = require("node:zlib");
const { DatabaseSync } = require("node:sqlite");
const Engine = require("../dragon-wave-engine.js");
const Data = require("../dragon-wave-data.js");
const Feedback = require("../dragon-wave-feedback.js");
const Cases = require("../dragon-wave-cases.js");
const ROOT = path.resolve(__dirname, "..");
const MS = { "5m": 300000, "15m": 900000, "1h": 3600000, "4h": 14400000, "1d": 86400000 };
const OPTIONS = { mainWaveStage: "active", mainWaveContextSource: "leader-default-main-wave", mainWaveContextLabel: "龙头默认主升浪环境", preselectedLeader: true };
const args = Object.fromEntries(process.argv.slice(2).map(arg => { const p = arg.indexOf("="); return p < 0 ? [arg.slice(2), true] : [arg.slice(2, p), arg.slice(p + 1)]; }));
const OUT = path.resolve(ROOT, args.out || "deliverables/2026-09-07-unconfirmed-b-audit");
const LABEL = args.label || "baseline";
const readJson = filename => JSON.parse(fs.readFileSync(filename, "utf8").replace(/^\uFEFF/, ""));
const relative = filename => path.relative(ROOT, filename).replaceAll("\\", "/");
const sha = filename => crypto.createHash("sha256").update(fs.readFileSync(filename)).digest("hex");
const localTime = time => new Date(Number(time) + 8 * 3600000).toISOString().slice(0, 16).replace("T", " ");
const increment = (map, key) => { map[key] = (map[key] || 0) + 1; };
const decisionCounts = rows => rows.reduce((all, row) => { increment(all, row.reviewDecision || row.decision); return all; }, {});

function documentWindows(pair, time) {
  return Cases.filter(item => item.valid !== false && item.pair === pair).filter(item => {
    const start = Date.parse(`${item.start}T00:00:00+08:00`);
    const end = Date.parse(`${item.end}T23:59:59.999+08:00`);
    return time >= start && time <= end;
  }).map(item => ({ id: item.id, start: item.start, end: item.end }));
}

function loadFeedback() {
  const filename = path.join(ROOT, ".runtime-cache/dragon_wave_feedback.db");
  if (!fs.existsSync(filename)) return { document: Feedback.emptyDocument(), sources: 0, rawCount: 0, invalidRecords: [], error: "feedback SQLite missing" };
  const db = new DatabaseSync(filename, { readOnly: true });
  let rows;
  try { rows = db.prepare("SELECT device_id, payload, updated_at FROM feedback_documents ORDER BY updated_at ASC").all(); } finally { db.close(); }
  // Merge by individual record time, not document/device timestamp.
  const merged = {};
  for (const row of rows) {
    const document = JSON.parse(row.payload);
    for (const [key, record] of Object.entries(document.records || {})) {
      if (!merged[key] || Number(record.updatedAt || 0) >= Number(merged[key].updatedAt || 0)) merged[key] = record;
    }
  }
  const valid = {}, invalidRecords = [];
  for (const [key, record] of Object.entries(merged)) {
    const expected = Feedback.signalKey(record.pair, { ...record.signal, interval: record.interval });
    if (String(record.pair).toUpperCase() === "TESTUSDT") { invalidRecords.push({ key, reason: "test symbol excluded" }); continue; }
    if (key !== expected) { invalidRecords.push({ key, expected, reason: "key/pair/interval/time mismatch" }); continue; }
    valid[key] = record;
  }
  return { document: { version: 1, records: valid, updatedAt: Math.max(0, ...Object.values(valid).map(r => Number(r.updatedAt || 0))) }, sources: rows.length, rawCount: Object.keys(merged).length, rawDecisionCounts: decisionCounts(Object.values(merged)), invalidRecords };
}

function normalizeFixture(filename, rows, interval) {
  if (filename.includes("_okx_")) return Data.parseRows("okx", rows, MS[interval]);
  return Engine.normalizeCandles(rows.map(row => {
    if (!Array.isArray(row)) return { ...row, closeTime: row.closeTime ?? Number(row.time) + MS[interval] - 1 };
    const [time, open, high, low, close, volume, quoteVolume, takerBuyVolume, tradeCount] = row;
    return { time: Number(time), closeTime: Number(time) + MS[interval] - 1, open: Number(open), high: Number(high), low: Number(low), close: Number(close), volume: Number(volume), ...(quoteVolume !== undefined ? { quoteVolume: Number(quoteVolume), takerBuyVolume: Number(takerBuyVolume), tradeCount: Number(tradeCount) } : {}) };
  }));
}

function coverage(candles, interval) {
  const gaps = [];
  for (let i = 1; i < candles.length; i++) if (candles[i].time - candles[i - 1].time > MS[interval]) gaps.push({ after: candles[i - 1].time, before: candles[i].time, missingBars: Math.round((candles[i].time - candles[i - 1].time) / MS[interval]) - 1 });
  return { bars: candles.length, start: candles[0]?.time ?? null, end: candles.at(-1)?.time ?? null, startLocal: candles.length ? localTime(candles[0].time) : null, endLocal: candles.length ? localTime(candles.at(-1).time) : null, gaps, missingBars: gaps.reduce((n, gap) => n + gap.missingBars, 0) };
}

function classify(signal) {
  const foundation = new Set(signal.foundationTypes || []), auxiliaries = new Set(signal.auxiliaryTypes || []);
  const flags = [];
  const numeric = value => (typeof value === "number" || (typeof value === "string" && value.trim() !== "")) && Number.isFinite(Number(value));
  const lowVolume = numeric(signal.relativeVolume) && Number(signal.relativeVolume) < 1.05;
  const lowFlow = numeric(signal.orderFlowScore) && Number(signal.orderFlowScore) < 45;
  const youngEdge = numeric(signal.ceilingAge) && Number(signal.ceilingAge) < 18;
  const fewTouches = numeric(signal.ceilingTouches) && Number(signal.ceilingTouches) < 6;
  if (lowVolume) flags.push("relative-volume-below-1.05");
  if (lowFlow) flags.push("order-flow-below-45");
  if (youngEdge) flags.push("edge-age-below-18");
  if (fewTouches) flags.push("ceiling-touches-below-6");
  if (foundation.has("base") && !foundation.has("triangle")) flags.push("pure-base-without-triangle");
  if (lowVolume && lowFlow && youngEdge && fewTouches && foundation.has("base")) flags.push(foundation.has("triangle") ? "quiet-young-base-with-triangle" : "quiet-young-pure-base");
  if (signal.horizontalLaunchUrgent || signal.horizontalLaunchInsufficientEdgeDwell) flags.push("urgent-or-insufficient-dwell");
  if (["1h", "4h"].includes(signal.interval) && Engine.isRecognizedHigherTimeframeStructureBreak(signal)) flags.push("recognized-higher-timeframe-structure");
  const validPriorHigh = numeric(signal.previousHighLevel) && Number(signal.previousHighLevel) > 0;
  const noPriorEvidence = !auxiliaries.has("previousHigh") && !validPriorHigh && signal.outerEdgeConfirmed !== true;
  if (noPriorEvidence && (signal.executionHierarchy?.boosters || []).includes("previous-high")) flags.push("unsupported-previous-high-booster");
  return { flags, lowVolume, lowFlow, youngEdge, fewTouches, noPriorEvidence, validPriorHigh, hierarchyPermit: signal.executionHierarchy?.permit ?? null };
}

function stripHeavy(value) {
  return JSON.parse(JSON.stringify(value, (key, item) => ["visualSignature", "visualTrainingSignature"].includes(key) ? undefined : item));
}

const feedback = loadFeedback();
const feedbackContext = Feedback.prepareApplicationContext(feedback.document);
let report = {
  schema: 1, label: LABEL, startedAt: Date.now(), generatedAt: Date.now(), engineSha256: sha(path.join(ROOT, "dragon-wave-engine.js")), status: "running",
  scope: "本地真实行情可覆盖的原生 B 与反馈后 B；不是全交易所或所有文档币种的完整历史。1 分钟按用户要求跳过。质量标签仅使用信号当时特征，不使用后续涨跌，不自动改变用户确认。",
  metricNotes: { ceilingAge: "常规平台为最后一次触及上沿距离触发的K线数，不是盘整总根数；三角融合候选可能有代理值。只用于复现现有组合条件，不能单独判断盘整短促。", consolidationBars: "整个候选结构使用的盘整K线数。", localScope: "inDocumentWindow 只按用户案例起止日期，不包括预热窗口；无文档案例的实时币仍留在raw观察但不混入文档内统计。" },
  options: OPTIONS, feedback: { sources: feedback.sources, rawCount: feedback.rawCount, rawDecisionCounts: feedback.rawDecisionCounts, validCount: Object.keys(feedback.document.records).length, validDecisionCounts: decisionCounts(Object.values(feedback.document.records)), invalidRecords: feedback.invalidRecords, error: feedback.error },
  sources: [], signals: [], secondaryHints: [], reviews: [], errors: [], missingCoverage: [], summary: {},
};

function recordSource(id, pair, interval, candles, sourceKind, metadata = {}) {
  const item = { id, pair, interval, sourceKind, ...coverage(candles, interval), ...metadata, status: "pending" };
  report.sources.push(item); return item;
}

function capture(result, source) {
  const pair = source.pair;
  const nativeTimes = new Set((result.signals || []).filter(s => !s.manualRestored).map(s => s.time));
  const display = Feedback.applyToResult(result, pair, feedback.document, feedbackContext);
  const displayByTime = new Map((display.signals || []).map(s => [s.time, s]));
  const nativeByTime = new Map((result.signals || []).map(s => [s.time, s]));
  const times = new Set([...nativeByTime.keys(), ...displayByTime.keys()]);
  report.signals = report.signals.filter(item => item.sourceId !== source.id);
  report.secondaryHints = report.secondaryHints.filter(item => item.sourceId !== source.id);
  report.reviews = report.reviews.filter(item => item.sourceId !== source.id);
  for (const time of times) {
    const signal = nativeByTime.get(time) || displayByTime.get(time), shown = displayByTime.get(time);
    const key = Feedback.signalKey(pair, { ...signal, interval: source.interval });
    const review = feedback.document.records[key];
    report.signals.push({ key, pair, interval: source.interval, time, localTime: localTime(time), sourceId: source.id, sourceKind: source.sourceKind, sourceBars: source.bars, sourceStart: source.start, sourceEnd: source.end, reviewDecision: review?.decision || "unreviewed", reviewUpdatedAt: review?.updatedAt || null, native: nativeTimes.has(time), displayed: Boolean(shown), manualRestored: Boolean(shown?.manualRestored) || (Boolean(shown) && !nativeTimes.has(time)), targetUnconfirmedB: Boolean(shown) && nativeTimes.has(time) && !["confirmed", "denied"].includes(review?.decision), analysis: classify({ ...signal, interval: source.interval }), signal: stripHeavy(signal) });
  }
  for (const signal of result.secondaryBreakoutHints || []) report.secondaryHints.push({ key: Feedback.signalKey(pair, signal), sourceId: source.id, pair, interval: source.interval, time: signal.time, localTime: localTime(signal.time), signal: stripHeavy(signal), formalBuy: false });
  for (const [key, review] of Object.entries(feedback.document.records)) {
    const time = Number(review.signal?.time);
    if (review.pair !== pair || review.interval !== source.interval || time < source.start || time > source.end) continue;
    const exactCandle = result.candles.some(c => c.time === time);
    const native = nativeByTime.get(time), rejected = result.rejected?.find(s => s.time === time), pending = result.pending?.find(s => s.time === time);
    report.reviews.push({ key, sourceId: source.id, sourceBars: source.bars, pair, interval: source.interval, time, localTime: localTime(time), reviewDecision: review.decision, exactCandle, native: Boolean(native), nativeStatus: native ? "buy" : rejected ? "filtered" : pending ? "pending" : "missing", displayed: displayByTime.has(time), reasons: rejected?.reasons || pending?.reasons || [], signal: stripHeavy(native || rejected || pending || null) });
  }
  source.status = "complete"; source.nativeSignalCount = nativeTimes.size; source.displayedSignalCount = displayByTime.size;
  source.contextIntervals = source.contextIntervals || [source.interval];
  source.completedAt = Date.now();
}

function persist() {
  const unique = new Map();
  for (const signal of report.signals) {
    const old = unique.get(signal.key);
    if (!old || signal.sourceBars > old.sourceBars) unique.set(signal.key, signal);
  }
  const deduped = [...unique.values()], targets = deduped.filter(row => row.targetUnconfirmedB), flagCounts = {};
  for (const row of report.signals) {
    row.documentWindows = documentWindows(row.pair, row.time);
    row.inDocumentWindow = row.documentWindows.length > 0;
    row.targetUnconfirmedBInDocument = row.targetUnconfirmedB && row.inDocumentWindow;
  }
  const documentTargets = deduped.filter(row => row.targetUnconfirmedBInDocument), documentFlagCounts = {};
  for (const row of documentTargets) for (const flag of row.analysis.flags) increment(documentFlagCounts, flag);
  for (const row of targets) for (const flag of row.analysis.flags) increment(flagCounts, flag);
  report.generatedAt = Date.now();
  report.summary = { sourceCount: report.sources.length, completedSources: report.sources.filter(s => s.status === "complete").length, rawSignalObservations: report.signals.length, uniqueSignals: deduped.length, uniqueNativeSignals: deduped.filter(s => s.native).length, uniqueDisplayedSignals: deduped.filter(s => s.displayed).length, uniqueManualRestored: deduped.filter(s => s.manualRestored).length, targetUnconfirmedB: targets.length, targetUnconfirmedBInDocument: documentTargets.length, targetUnconfirmedBOutsideDocument: targets.length - documentTargets.length, uniqueNativeSignalsInDocument: deduped.filter(s => s.native && s.inDocumentWindow).length, uniqueReviewDecisionCounts: decisionCounts(deduped), flagCounts, documentFlagCounts, nativeByInterval: deduped.filter(s => s.native).reduce((all, row) => { increment(all, row.interval); return all; }, {}), reviewCoveredUnique: new Set(report.reviews.map(r => r.key)).size };
  fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(path.join(OUT, `${LABEL}.json`), JSON.stringify(report, null, 2));
  const suspicious = documentTargets.filter(row => row.analysis.flags.some(flag => ["quiet-young-pure-base", "unsupported-previous-high-booster", "urgent-or-insufficient-dwell"].includes(flag)));
  const md = [`# 未确认 B 点离线审计：${LABEL}`, "", `状态：${report.status}；引擎 SHA256：${report.engineSha256}`, "", report.scope, "", `- 已完成数据源：${report.summary.completedSources}/${report.summary.sourceCount}`, `- 去重原生 B：${report.summary.uniqueNativeSignals}；未确认且展示的原生 B：${targets.length}`, `- 人工恢复 B：${report.summary.uniqueManualRestored}（不计为策略原生命中）`, `- 全设备反馈：${feedback.rawCount}；通过键校验且非测试标的：${report.feedback.validCount}`, `- 原生信号去重规则：同币/周期/时间取覆盖根数最多的来源；逐来源结果完整保留在 JSON。`, "", "## 来源和覆盖范围", "", "| 来源 | 币种 | 周期 | 根数 | 首根（北京时间） | 末根 | 状态 |", "|---|---|---|---:|---|---|---|", ...report.sources.map(s => `| ${s.id} | ${s.pair} | ${s.interval} | ${s.bars} | ${s.startLocal} | ${s.endLocal} | ${s.status} |`), "", "## 待复核特征分布（标签不是自动否定）", "", ...Object.entries(flagCounts).map(([flag, count]) => `- ${flag}：${count}`), "", "## 待复核点位", "", ...suspicious.map(s => `- ${s.pair} ${s.interval} ${s.localTime}（${s.reviewDecision}）：${s.analysis.flags.join("、")}；量比 ${s.signal.relativeVolume}，流量分 ${s.signal.orderFlowScore}，边界年龄 ${s.signal.ceilingAge}，触边 ${s.signal.ceilingTouches}；${s.signal.pattern || ""}`), "", "## 未覆盖和限制", "", ...report.missingCoverage.map(s => `- ${typeof s === "string" ? s : JSON.stringify(s)}`), ...report.errors.map(s => `- ${JSON.stringify(s)}`), "- 夹具是局部历史，不能把在短窗口内未出现的母结构或确认点当作无效；跨周期只有已提供数据可校验。", "- 后续收益未读取、未参与判定。本报告不是收益或胜率证明。", ""];
  md.splice(8, 0, `- 其中文档日期内未确认原生 B：${documentTargets.length}；预热或文档外研究信号：${targets.length - documentTargets.length}（分开统计）。`, "- ceilingAge 是最后一次触边距触发的根数，不是平台总根数；盘整长度看 consolidationBars。以下待复核点位只列文档日期内信号。");
  fs.writeFileSync(path.join(OUT, `${LABEL}.md`), md.join("\n"));
  console.log(JSON.stringify({ phase: report.status, ...report.summary }));
}

function analyzeSource(candles, source) {
  console.log(`ANALYZE ${source.id} ${candles.length} bars`);
  return Engine.analyzeTimeframe(candles, { ...OPTIONS, interval: source.interval, now: (candles.at(-1)?.closeTime || source.end + MS[source.interval] - 1) + 1 });
}

function main() {
  if (args.reclassify) {
    report = readJson(path.join(OUT, `${LABEL}.json`));
    report.metricNotes = { ceilingAge: "常规平台为最后一次触及上沿距离触发的K线数，不是盘整总根数；三角融合候选可能有代理值。", consolidationBars: "整个候选结构使用的盘整K线数。", localScope: "inDocumentWindow 只按用户案例起止日期，不包括预热窗口。" };
    if (args["excluded-intervals"]) {
      report.excludedIntervals = String(args["excluded-intervals"]).split(",");
      report.missingCoverage = [...new Set([...report.missingCoverage, `本报告尚未覆盖 XRP ${report.excludedIntervals.join("/")} 全窗口回放；不能视为这些周期不存在未确认B。`])];
    }
    if (args["checkpoint-status"]) report.status = String(args["checkpoint-status"]);
    if (!fs.existsSync(path.join(ROOT, ".runtime-cache/dragon-wave-precomputed/v89/manifest.json"))) report.missingCoverage = [...new Set([...report.missingCoverage, "v89 precomputed manifest missing; all document assets could not be inventoried."])];
    for (const row of report.signals) {
      row.analysis = classify({ ...row.signal, interval: row.interval });
      row.manualRestored = Boolean(row.manualRestored) || (row.displayed && !row.native);
    }
    report.classificationUpdatedAt = Date.now();
    persist();
    return;
  }
  console.log(`ENGINE_LOADED ${report.engineSha256}`);
  persist();
  if (!args["skip-fixtures"]) for (const name of fs.readdirSync(path.join(ROOT, "tests/fixtures")).filter(name => /usdt.*\.json$/.test(name))) {
    const match = name.match(/^([a-z0-9]+usdt)(?:_(?:okx|binance))?_(5m|15m|1h|4h|1d)_/);
    if (!match) continue;
    const [, pairText, interval] = match, pair = pairText.toUpperCase(), filename = path.join(ROOT, "tests/fixtures", name);
    try {
      const candles = normalizeFixture(name, readJson(filename), interval);
      const source = recordSource(relative(filename), pair, interval, candles, "local-regression-fixture", { fileSha256: sha(filename), completeDocumentHistory: false, contextIntervals: [interval] });
      const result = Engine.applyContextGates([analyzeSource(candles, source)], [], OPTIONS).map(Engine.enforceIntervalStructurePolicy)[0];
      capture(result, source); persist();
    } catch (error) { report.errors.push({ source: name, message: error.message, stack: error.stack }); persist(); }
  }
  if (!args["fixtures-only"]) {
    const filename = path.join(ROOT, ".runtime-cache/xrp-full-audit.json");
    if (!fs.existsSync(filename)) report.missingCoverage.push("XRP full audit candle file missing");
    else {
      const payload = readJson(filename), results = [], sources = new Map();
      const order = args.intervals ? String(args.intervals).split(",").filter(i => MS[i]) : ["1h", "4h", "1d", "15m", "5m"];
      for (const interval of order) {
        const candles = Engine.normalizeCandles(payload.intervals?.[interval]?.candles || []);
        if (!candles.length) { report.missingCoverage.push(`XRP ${interval} candles missing`); continue; }
        const source = recordSource(`${relative(filename)}#${interval}`, payload.pair || "XRPUSDT", interval, candles, "local-historical-candle-cache", { venue: payload.provider, market: payload.market, requestedStart: payload.intervals[interval].start, requestedEnd: payload.intervals[interval].end, completeDocumentHistory: false });
        sources.set(interval, source);
        try {
          results.push(analyzeSource(candles, source));
          // Re-apply causal cross-timeframe gates after each completed frame.
          // Earlier partial checkpoints explicitly list available context frames.
          const contextIntervals = results.map(r => r.interval);
          for (const result of Engine.applyContextGates(results, [], OPTIONS).map(Engine.enforceIntervalStructurePolicy)) {
            sources.get(result.interval).contextIntervals = contextIntervals;
            capture(result, sources.get(result.interval));
          }
          persist();
        } catch (error) { source.status = "error"; report.errors.push({ source: source.id, message: error.message }); persist(); }
      }
    }
  }
  const manifestPath = path.join(ROOT, ".runtime-cache/dragon-wave-precomputed/v89/manifest.json");
  if (!fs.existsSync(manifestPath)) report.missingCoverage.push("v89 precomputed manifest missing; all document assets could not be inventoried.");
  else if (!args["fixtures-only"]) {
    for (const [key, meta] of Object.entries(readJson(manifestPath).records || {})) {
      if (!MS[meta.interval]) continue;
      const filename = path.resolve(path.dirname(manifestPath), meta.file);
      if (!filename.startsWith(path.dirname(manifestPath) + path.sep)) { report.errors.push({ key, reason: "cache path outside manifest directory" }); continue; }
      try {
        const cached = JSON.parse(zlib.gunzipSync(fs.readFileSync(filename)).toString("utf8"));
        const candles = Engine.normalizeCandles(cached.result?.candles || []);
        if (!candles.length) { report.missingCoverage.push({ key, reason: "precomputed cache has no candles" }); continue; }
        const source = recordSource(`${relative(filename)}#${key}`, meta.pair || cached.pair, meta.interval, candles, "local-precomputed-candles-replayed-current-engine", { cacheStrategyVersion: cached.version, documentStart: meta.start, documentEnd: meta.end, contextIntervals: [meta.interval] });
        capture(Engine.applyContextGates([analyzeSource(candles, source)], [], OPTIONS).map(Engine.enforceIntervalStructurePolicy)[0], source);
        persist();
      } catch (error) { report.errors.push({ key, source: relative(filename), message: error.message }); persist(); }
    }
  }
  report.status = args["fixtures-only"] ? "fixtures-only-complete" : "available-sources-complete";
  persist();
}

if (require.main === module) main();
module.exports = { classify, coverage, normalizeFixture };
