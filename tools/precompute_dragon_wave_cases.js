#!/usr/bin/env node
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const zlib = require("node:zlib");
const Data = require("../dragon-wave-data");
const Engine = require("../dragon-wave-engine");
const Feedback = require("../dragon-wave-feedback");
const cases = require("../dragon-wave-cases");

const ROOT = path.resolve(__dirname, "..");
const LOCAL_ORIGIN = process.env.DRAGON_WAVE_ORIGIN || "http://127.0.0.1:8791";
const STRATEGY_VERSION = process.argv.find((arg) => arg.startsWith("--version="))?.split("=")[1] || "v89";
const INTERVALS = (process.argv.find((arg) => arg.startsWith("--intervals="))?.split("=")[1]
  || "5m,15m,1h,4h,1d").split(",").map((value) => value.trim()).filter((value) => Data.INTERVALS[value]);
const SYMBOL_FILTER = new Set((process.argv.find((arg) => arg.startsWith("--symbols="))?.split("=")[1] || "")
  .split(",").map((value) => value.trim()).filter(Boolean));
const FORCE = process.argv.includes("--force");
const COMPACT_EXISTING = process.argv.includes("--compact-existing");
const MAX_CASES = Math.max(0, Number(process.argv.find((arg) => arg.startsWith("--max-cases="))?.split("=")[1] || 0));
const OUTPUT_ROOT = path.join(ROOT, ".runtime-cache", "dragon-wave-precomputed", STRATEGY_VERSION);
const MANIFEST_PATH = path.join(OUTPUT_ROOT, "manifest.json");
const LOCK_PATH = path.join(ROOT, ".runtime-cache", "dragon-wave-precomputed", `${STRATEGY_VERSION}.lock`);
const REQUEST_ROOT = path.join(ROOT, ".runtime-cache", "dragon-wave-precomputed", `${STRATEGY_VERSION}.requests`);
const LOCAL_ARCHIVE_CACHE = new Map();
const LOCAL_ARCHIVE_DIRS = [
  path.join(ROOT, ".runtime-cache", "confirmed-native-audit-candles"),
  path.join(ROOT, ".runtime-cache", "strategy-issue-candles"),
];

global.location = { hostname: "127.0.0.1" };
const nativeFetch = global.fetch;
global.fetch = (url, options) => nativeFetch(String(url).startsWith("/") ? `${LOCAL_ORIGIN}${url}` : url, options);

function cacheKey(item, interval, mainWaveStage = "active") {
  return [STRATEGY_VERSION, Data.normalizePair(item.pair), item.start, item.end, interval, "futures", mainWaveStage].join("|");
}

function fileNameFor(key) {
  return `${crypto.createHash("sha256").update(key).digest("hex")}.json.gz`;
}

function readManifest() {
  try {
    const value = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
    if (value?.version === STRATEGY_VERSION && value.records && typeof value.records === "object") return value;
  } catch (_error) {
    // Missing or interrupted manifests are rebuilt from the current run.
  }
  return { schema: 1, version: STRATEGY_VERSION, generatedAt: 0, records: {} };
}

function atomicWrite(filePath, bytes) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, bytes);
  fs.renameSync(temporary, filePath);
}

function writeManifest(manifest) {
  manifest.generatedAt = Date.now();
  atomicWrite(MANIFEST_PATH, `${JSON.stringify(manifest, null, 2)}\n`);
}

function recordUsable(manifest, item, interval) {
  const key = cacheKey(item, interval);
  const record = manifest.records[key];
  return !FORCE && record?.file && fs.existsSync(path.join(OUTPUT_ROOT, record.file));
}

function acquireLock() {
  fs.mkdirSync(path.dirname(LOCK_PATH), { recursive: true });
  try {
    const descriptor = fs.openSync(LOCK_PATH, "wx");
    fs.writeFileSync(descriptor, JSON.stringify({ pid: process.pid, startedAt: Date.now() }));
    fs.closeSync(descriptor);
    return true;
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
    try {
      const lock = JSON.parse(fs.readFileSync(LOCK_PATH, "utf8"));
      let processAlive = false;
      try {
        process.kill(Number(lock.pid), 0);
        processAlive = true;
      } catch (_processError) {
        processAlive = false;
      }
      if (!processAlive || Date.now() - Number(lock.startedAt || 0) > 24 * 60 * 60 * 1000) {
        fs.unlinkSync(LOCK_PATH);
        return acquireLock();
      }
    } catch (_readError) {
      fs.rmSync(LOCK_PATH, { force: true });
      return acquireLock();
    }
    return false;
  }
}

function releaseLock() {
  try { fs.rmSync(LOCK_PATH, { force: true }); } catch (_error) { /* best effort */ }
}

function priorityRequests() {
  let names = [];
  try {
    names = fs.readdirSync(REQUEST_ROOT).filter((name) => /^[0-9a-f]{64}\.json$/.test(name));
  } catch (_error) {
    return [];
  }
  const requests = [];
  names.forEach((name) => {
    const filePath = path.join(REQUEST_ROOT, name);
    try {
      const request = JSON.parse(fs.readFileSync(filePath, "utf8"));
      const item = cases.find((candidate) => candidate.valid
        && Data.normalizePair(candidate.pair) === Data.normalizePair(request.pair)
        && candidate.start === request.start
        && candidate.end === request.end);
      if (item) requests.push({
        filePath,
        item,
        interval: INTERVALS.includes(request.interval) ? request.interval : "",
        priority: Math.max(0, Number(request.priority || 0)),
        requestedAt: Number(request.requestedAt || 0),
      });
      else fs.rmSync(filePath, { force: true });
    } catch (_error) {
      try { fs.rmSync(filePath, { force: true }); } catch (_removeError) { /* best effort */ }
    }
  });
  return requests.sort((left, right) => right.priority - left.priority || right.requestedAt - left.requestedAt);
}

async function buildCaseSafely(
  item,
  manifest,
  position,
  total,
  preferredIntervals = [],
  onlyPreferred = false,
  afterInterval = null,
) {
  try {
    return await buildCase(
      item,
      manifest,
      position,
      total,
      preferredIntervals,
      onlyPreferred,
      afterInterval,
    );
  } catch (error) {
    process.stderr.write(`[${position}/${total}] ${item.symbol} 预计算失败：${error?.stack || error}\n`);
    return { built: 0, skipped: 0, failed: onlyPreferred ? preferredIntervals.length : INTERVALS.length };
  }
}

async function drainPriorityRequests(manifest, totals) {
  const queued = priorityRequests();
  if (!queued.length) return;
  // Keep each requested interval independently schedulable. Grouping all periods
  // of one symbol made a long 5m history block confirmed 1h/4h points elsewhere.
  for (const entry of queued) {
    const preferredIntervals = entry.interval ? [entry.interval] : [];
    const result = await buildCaseSafely(entry.item, manifest, "优先", queued.length, preferredIntervals, true);
    totals.built += result.built;
    totals.skipped += result.skipped;
    totals.failed += result.failed;
    try {
      fs.unlinkSync(entry.filePath);
    } catch (error) {
      if (error?.code !== "ENOENT") process.stderr.write(`无法清理优先请求 ${entry.filePath}：${error?.message || error}\n`);
    }
  }
}

const SOFT_VISUAL_FILTERS = [
  /母结构尚未成熟/,
  /突破前未贴近关键位蓄力/,
  /结构松散/,
  /单一前高结构往返噪声过高/,
];

function compactRejectedSignal(item) {
  const compact = {
    ...Feedback.snapshotSignal(item),
    index: item.index,
    breakoutOpen: item.breakoutOpen,
    breakoutLow: item.breakoutLow,
  };
  const reasons = Array.isArray(compact.reasons) ? compact.reasons : [];
  const canUseVisualPrecheck = reasons.length > 0
    && reasons.every((reason) => SOFT_VISUAL_FILTERS.some((pattern) => pattern.test(String(reason))));
  if (!canUseVisualPrecheck) delete compact.visualSignature;
  return compact;
}

function compactResultForDashboard(result) {
  return {
    ...result,
    // 过滤记录只保留界面、人工回归和视觉预确认真正使用的字段。完整K线、
    // 正式买点、候选与结构不裁剪，因此盘面和确认逻辑不变；长案例可少解析
    // 数十 MB 重复诊断字段。
    rejected: (result?.rejected || []).map(compactRejectedSignal),
  };
}

function compactExistingRecords(manifest) {
  let compacted = 0;
  Object.values(manifest.records || {}).forEach((record) => {
    if (!record?.file || record.compactSchema === 1) return;
    if (SYMBOL_FILTER.size && !SYMBOL_FILTER.has(record.symbol)) return;
    const filePath = path.join(OUTPUT_ROOT, record.file);
    if (!fs.existsSync(filePath)) return;
    const value = JSON.parse(zlib.gunzipSync(fs.readFileSync(filePath)).toString("utf8"));
    value.result = compactResultForDashboard(value.result);
    const compressed = zlib.gzipSync(Buffer.from(JSON.stringify(value)), { level: 6 });
    atomicWrite(filePath, compressed);
    record.bytes = compressed.length;
    record.compactSchema = 1;
    compacted += 1;
  });
  writeManifest(manifest);
  console.log(`现有本地结果压缩完成：${compacted} 个周期。`);
}

function readArchive(filePath) {
  if (LOCAL_ARCHIVE_CACHE.has(filePath)) return LOCAL_ARCHIVE_CACHE.get(filePath);
  try {
    const value = JSON.parse(fs.readFileSync(filePath, "utf8"));
    LOCAL_ARCHIVE_CACHE.set(filePath, value);
    return value;
  } catch (_error) {
    LOCAL_ARCHIVE_CACHE.set(filePath, null);
    return null;
  }
}

function localArchivePaths(item) {
  const pair = Data.normalizePair(item.pair);
  const paths = [];
  const fullAudit = path.join(ROOT, ".runtime-cache", `${item.symbol.toLowerCase()}-full-audit.json`);
  if (fs.existsSync(fullAudit)) paths.push(fullAudit);
  LOCAL_ARCHIVE_DIRS.forEach((directory) => {
    try {
      fs.readdirSync(directory)
        .filter((name) => name.toUpperCase().startsWith(`${pair}-`) && name.toLowerCase().endsWith(".json"))
        .forEach((name) => paths.push(path.join(directory, name)));
    } catch (_error) {
      // Local audit archives are opportunistic; network fallback remains available.
    }
  });
  return [...new Set(paths)];
}

function readLocalArchivedCandles(item, interval) {
  const window = Data.buildCaseWindow(item.start, item.end, interval);
  const byTime = new Map();
  let provider = "";
  localArchivePaths(item).forEach((filePath) => {
    const archive = readArchive(filePath);
    if (!archive || Data.normalizePair(archive.pair) !== Data.normalizePair(item.pair)) return;
    const intervalValue = archive.intervals?.[interval];
    const candles = Array.isArray(intervalValue) ? intervalValue : intervalValue?.candles;
    if (!Array.isArray(candles)) return;
    provider ||= archive.provider || "local";
    candles.forEach((candle) => {
      const time = Number(candle?.time);
      if (Number.isFinite(time) && time >= window.start && time <= window.end) byTime.set(time, candle);
    });
  });
  const candles = [...byTime.values()].sort((left, right) => left.time - right.time);
  if (!Data.isCandleCoverageAcceptable(candles, window, interval)) return null;
  return {
    candles,
    venue: { id: "local-archive", provider: provider || "local", market: "futures", label: "本机K线库" },
    attempts: [],
    coverage: Data.assessCandleCoverage(candles, window, interval),
  };
}

async function fetchAndAnalyze(item, interval) {
  const window = Data.buildCaseWindow(item.start, item.end, interval);
  const payload = readLocalArchivedCandles(item, interval) || await Data.fetchCandles({
    pair: item.pair,
    interval,
    provider: "auto",
    market: "futures",
    window,
  });
  if (!Data.isCandleCoverageAcceptable(payload.candles, window, interval)) {
    throw new Error(`指定区间不完整 ${(payload.coverage?.spanCoverage * 100 || 0).toFixed(1)}%`);
  }
  const rawResult = Engine.analyzeTimeframe(payload.candles, {
    interval,
    now: Date.now(),
    mainWaveStage: "active",
    mainWaveContextSource: "leader-default-main-wave",
    mainWaveContextLabel: "龙头默认主升浪环境",
  });
  return { interval, rawResult, venue: payload.venue, attempts: payload.attempts || [], coverage: payload.coverage };
}

function storeAnalyzedEntry(item, manifest, entry, result) {
  const key = cacheKey(item, entry.interval);
  const file = fileNameFor(key);
  const value = {
    schema: 1,
    version: STRATEGY_VERSION,
    generatedAt: Date.now(),
    key,
    pair: Data.normalizePair(item.pair),
    symbol: item.symbol,
    start: item.start,
    end: item.end,
    interval: entry.interval,
    market: "futures",
    mainWaveStage: "active",
    venue: entry.venue,
    attempts: entry.attempts,
    coverage: entry.coverage,
    result: compactResultForDashboard(result || entry.rawResult),
  };
  const compressed = zlib.gzipSync(Buffer.from(JSON.stringify(value)), { level: 6 });
  atomicWrite(path.join(OUTPUT_ROOT, file), compressed);
  manifest.records[key] = {
    file,
    pair: value.pair,
    symbol: item.symbol,
    start: item.start,
    end: item.end,
    interval: entry.interval,
    market: value.market,
    mainWaveStage: value.mainWaveStage,
    venue: entry.venue?.label || "",
    candleCount: value.result?.candles?.length || 0,
    bytes: compressed.length,
    generatedAt: value.generatedAt,
    compactSchema: 1,
  };
}

function contextGate(entries) {
  return new Map(Engine.applyContextGates(
    entries.map((entry) => entry.rawResult),
    [],
    {
      preselectedLeader: true,
      mainWaveStage: "active",
      mainWaveContextSource: "leader-default-main-wave",
      mainWaveContextLabel: "龙头默认主升浪环境",
    },
  ).map((result) => [result.interval, result]));
}

async function buildCase(
  item,
  manifest,
  position,
  total,
  preferredIntervals = [],
  onlyPreferred = false,
  afterInterval = null,
) {
  const eligibleIntervals = onlyPreferred
    ? preferredIntervals.filter((interval) => INTERVALS.includes(interval))
    : INTERVALS;
  const missingIntervals = eligibleIntervals.filter((interval) => !recordUsable(manifest, item, interval));
  if (!missingIntervals.length) {
    process.stdout.write(`[${position}/${total}] ${item.symbol} 已缓存\n`);
    return { built: 0, skipped: eligibleIntervals.length, failed: 0 };
  }
  const loaded = [];
  const failures = [];
  const preferred = [...new Set([...preferredIntervals, "1h"])]
    .filter((interval) => missingIntervals.includes(interval));
  const remaining = missingIntervals.filter((interval) => !preferred.includes(interval));
  const loadAndStore = async (interval) => {
    try {
      const entry = await fetchAndAnalyze(item, interval);
      loaded.push(entry);
      // The requested/primary interval is written as soon as it is ready. The final
      // pass below overwrites it with the complete five-timeframe gate.
      const partialGate = contextGate(loaded);
      storeAnalyzedEntry(item, manifest, entry, partialGate.get(interval));
      writeManifest(manifest);
      process.stdout.write(`[${position}/${total}] ${item.symbol} ${interval} 已落盘 ${entry.rawResult.candles.length} 根\n`);
    } catch (error) {
      failures.push({ interval, message: error?.message || String(error) });
    }
  };
  // Full-catalog warming is intentionally sequential. A long historical symbol
  // must not occupy every fetch/worker slot while the user is waiting for another
  // symbol. After every interval is persisted, service the newest dashboard
  // request before continuing the low-priority warm-up.
  for (const interval of [...preferred, ...remaining]) {
    await loadAndStore(interval);
    if (!onlyPreferred && typeof afterInterval === "function") await afterInterval();
  }
  const gated = contextGate(loaded);
  for (const entry of loaded) {
    storeAnalyzedEntry(item, manifest, entry, gated.get(entry.interval));
  }
  writeManifest(manifest);
  const detail = loaded.map((entry) => `${entry.interval}:${entry.rawResult.candles.length}`).join(" ");
  const failed = failures.map((entry) => `${entry.interval}:${entry.message}`).join("；");
  process.stdout.write(`[${position}/${total}] ${item.symbol} 完成 ${detail}${failed ? `；缺失 ${failed}` : ""}\n`);
  return { built: loaded.length, skipped: 0, failed: failures.length };
}

async function main() {
  if (!/^v\d+$/.test(STRATEGY_VERSION)) throw new Error(`无效策略版本 ${STRATEGY_VERSION}`);
  if (!INTERVALS.length) throw new Error("没有可预计算的周期");
  if (!acquireLock()) {
    console.log(`已有 ${STRATEGY_VERSION} 预计算任务运行中，本次退出。`);
    return;
  }
  try {
    fs.mkdirSync(OUTPUT_ROOT, { recursive: true });
    const manifest = readManifest();
    if (COMPACT_EXISTING) {
      compactExistingRecords(manifest);
      return;
    }
    let selected = cases.filter((item) => item.valid && (!SYMBOL_FILTER.size || SYMBOL_FILTER.has(item.symbol)));
    if (MAX_CASES) selected = selected.slice(0, MAX_CASES);
    const totals = { built: 0, skipped: 0, failed: 0 };
    manifest.status = {
      state: "running",
      pid: process.pid,
      startedAt: Date.now(),
      totalCases: selected.length,
      processedCases: 0,
      intervals: INTERVALS,
    };
    writeManifest(manifest);
    await drainPriorityRequests(manifest, totals);
    for (let index = 0; index < selected.length; index += 1) {
      const result = await buildCaseSafely(
        selected[index],
        manifest,
        index + 1,
        selected.length,
        [],
        false,
        () => drainPriorityRequests(manifest, totals),
      );
      totals.built += result.built;
      totals.skipped += result.skipped;
      totals.failed += result.failed;
      manifest.status.processedCases = index + 1;
      manifest.status.updatedAt = Date.now();
      writeManifest(manifest);
      await drainPriorityRequests(manifest, totals);
      // 后台预热让出一点 CPU，避免用户正在看盘或确认买点时被批量分析抢占。
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
    await drainPriorityRequests(manifest, totals);
    manifest.status = {
      ...manifest.status,
      state: "complete",
      finishedAt: Date.now(),
      built: totals.built,
      skipped: totals.skipped,
      failed: totals.failed,
      recordCount: Object.keys(manifest.records || {}).length,
    };
    writeManifest(manifest);
    console.log(`预计算结束：新增 ${totals.built}，复用 ${totals.skipped}，失败 ${totals.failed}。`);
  } finally {
    releaseLock();
  }
}

main().catch((error) => {
  releaseLock();
  console.error(error);
  process.exitCode = 1;
});
