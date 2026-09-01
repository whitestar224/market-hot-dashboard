"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { DatabaseSync } = require("node:sqlite");
const Engine = require("../dragon-wave-engine.js");
const Feedback = require("../dragon-wave-feedback.js");

const ROOT = path.resolve(__dirname, "..");
const DB_PATH = path.join(ROOT, ".runtime-cache", "dragon_wave_feedback.db");
const CACHE_DIR = path.join(ROOT, ".runtime-cache", "confirmed-native-audit-candles");
const versionArg = process.argv.find((item) => item.startsWith("--version="));
const strategyVersion = versionArg ? versionArg.split("=")[1] : "v75";
const pairsArg = process.argv.find((item) => item.startsWith("--pairs="));
const intervalsArg = process.argv.find((item) => item.startsWith("--intervals="));
const requestedPairs = new Set((pairsArg ? pairsArg.split("=")[1] : "")
  .split(",").map((item) => item.trim().toUpperCase()).filter(Boolean));
const requestedIntervals = new Set((intervalsArg ? intervalsArg.split("=")[1] : "")
  .split(",").map((item) => item.trim()).filter(Boolean));
const REPORT_JSON = path.join(ROOT, "deliverables", `review-native-coverage-${strategyVersion}.json`);
const REPORT_MD = path.join(ROOT, "deliverables", `review-native-coverage-${strategyVersion}.md`);
const fetcher = path.join(__dirname, "fetch_confirmed_audit_candles.ps1");
const refresh = process.argv.includes("--refresh");
const pairWindow = process.argv.includes("--pair-window");
const INTERVAL_AUDIT_META = Object.freeze({
  "1m": { ms: 60_000, warmup: 1800 },
  "5m": { ms: 300_000, warmup: 900 },
  "15m": { ms: 900_000, warmup: 800 },
  "1h": { ms: 3_600_000, warmup: 600 },
  "4h": { ms: 14_400_000, warmup: 400 },
  "1d": { ms: 86_400_000, warmup: 300 },
});
const AUDIT_NEIGHBORS = Object.freeze({
  "1m": ["1m", "5m"],
  "5m": ["1m", "5m", "15m"],
  "15m": ["5m", "15m", "1h"],
  "1h": ["15m", "1h", "4h"],
  "4h": ["1h", "4h", "1d"],
  "1d": ["4h", "1d"],
});

function latestFeedbackDocument() {
  const db = new DatabaseSync(DB_PATH, { readOnly: true });
  try {
    const rows = db.prepare("SELECT device_id, payload, updated_at FROM feedback_documents ORDER BY updated_at DESC").all();
    for (const row of rows) {
      const document = JSON.parse(row.payload);
      if (Object.keys(document.records || {}).length) return { deviceId: row.device_id, updatedAt: row.updated_at, document };
    }
  } finally {
    db.close();
  }
  throw new Error("没有找到非空反馈文档");
}

function venueConfig(record, dominantByPair) {
  const venue = String(record.venue || "");
  if (venue.includes("Binance")) return { provider: "binance", market: venue.includes("现货") ? "spot" : "futures" };
  if (venue.includes("OKX")) return { provider: "okx", market: venue.includes("现货") ? "spot" : "futures" };
  return dominantByPair.get(record.pair) || { provider: "okx", market: "futures" };
}

function shanghaiTime(time) {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(new Date(time));
  const get = (type) => parts.find((item) => item.type === type)?.value || "";
  return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")}`;
}

function configKey(pair, provider, market) {
  return `${pair}|${provider}|${market}`;
}

function loadOrFetch(config) {
  fs.mkdirSync(CACHE_DIR, { recursive: true });
  const file = path.join(CACHE_DIR, `${config.pair}-${config.provider}-${config.market}-${config.minTime}-${config.maxTime}.json`);
  const readJson = () => JSON.parse(fs.readFileSync(file, "utf8").replace(/^\uFEFF/, ""));
  if (!refresh && fs.existsSync(file)) {
    const cached = readJson();
    if (!Object.keys(cached.errors || {}).length) return cached;
  }
  if (!refresh) {
    const prefix = `${config.pair}-${config.provider}-${config.market}-`;
    const enclosing = fs.readdirSync(CACHE_DIR)
      .filter((name) => name.startsWith(prefix) && name.endsWith(".json"))
      .map((name) => {
        const match = name.slice(prefix.length).match(/^(\d+)-(\d+)\.json$/);
        return match ? { name, start: Number(match[1]), end: Number(match[2]) } : null;
      })
      .filter((item) => item && item.start <= config.minTime && item.end >= config.maxTime)
      .sort((a, b) => (a.end - a.start) - (b.end - b.start))[0];
    if (enclosing) {
      const cached = JSON.parse(fs.readFileSync(path.join(CACHE_DIR, enclosing.name), "utf8").replace(/^\uFEFF/, ""));
      if (!Object.keys(cached.errors || {}).length) return cached;
    }
  }
  const args = [
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", fetcher,
    "-Provider", config.provider,
    "-Market", config.market,
    "-Pair", config.pair,
    "-FocusStartMs", String(config.minTime),
    "-FocusEndMs", String(config.maxTime),
    "-OutputPath", file,
  ];
  const run = spawnSync("powershell.exe", args, { cwd: ROOT, encoding: "utf8", maxBuffer: 20 * 1024 * 1024 });
  if (run.status !== 0 || !fs.existsSync(file)) {
    throw new Error((run.stderr || run.stdout || `行情下载失败，退出码 ${run.status}`).trim());
  }
  return readJson();
}

function nativeStatusAt(result, time) {
  const native = (result.signals || []).find((item) => item.time === time && !item.manualRestored
    && !(item.manualOverride && item.strategyStatusBeforeFeedback !== "buy"));
  if (native) return { status: "native-buy", item: native };
  const pending = (result.pending || []).find((item) => item.time === time);
  if (pending) return { status: "pending", item: pending };
  const filtered = (result.rejected || []).find((item) => item.time === time);
  if (filtered) return { status: "filtered", item: filtered };
  return { status: "missing", item: null };
}

function featureSnapshot(item) {
  if (!item) return null;
  const keys = [
    "patternKey", "primaryPatternKey", "foundationTypes", "auxiliaryTypes", "confluence",
    "structureShape", "riskStructureShape", "hasPivot", "consolidationBreakout",
    "consolidationBars", "outerEdgeConfirmed", "outerEdgeScore", "ceilingAge",
    "ceilingTouches", "platformTouchGroups", "clusteredCeilingBand", "platformModel",
    "launchDistancePercent", "horizontalLaunchHasPriorAdvance", "horizontalLaunchPriorAdvanceAtr",
    "horizontalLaunchPostSelloffRecovery", "horizontalLaunchUrgent",
    "horizontalLaunchInsufficientEdgeDwell", "triangleHasPriorAdvance", "trianglePriorAdvanceAtr",
    "trianglePostSelloffRecovery", "structureQuality", "channelInteriorOccupancy",
    "channelSideTransitions", "matureTriangleOuterEdge", "directStructuralBoundary",
    "openedBeyondTrigger", "insideMotherBase", "motherStructureNoise", "motherStructureMode",
    "motherStructureBars", "motherStructureHigh", "motherStructureLow", "motherStructurePosition",
    "oneMinuteMotherBoxNoise", "oneHourPlatformPivotReady", "ema90ReclaimContinuation",
    "aboveEma90", "ema90SlopeAtDecision", "relativeVolume", "orderFlowScore", "klineVelocity",
    "certaintyScore", "rhythmScore", "sentimentScore", "score",
    "crossedLevel", "triggerPrice", "breakoutLow", "previousHighLevel", "stop",
    "mainWaveStage", "mainWaveContextSource", "preHigherFrameMainWaveIgnitionPermit",
    "ascendingStructureTrap", "secondaryBreakoutHint",
  ];
  const snapshot = {};
  keys.forEach((key) => {
    if (item[key] !== undefined) snapshot[key] = item[key];
  });
  if (item.executionHierarchy) snapshot.executionHierarchy = item.executionHierarchy;
  return snapshot;
}

function main() {
  const feedback = latestFeedbackDocument();
  const reviewed = Object.values(feedback.document.records || {})
    .filter((record) => ["confirmed", "denied"].includes(record.decision))
    .filter((record) => !requestedPairs.size || requestedPairs.has(String(record.pair || "").toUpperCase()))
    .filter((record) => !requestedIntervals.size || requestedIntervals.has(String(record.interval || "")));
  const confirmed = reviewed.filter((record) => record.decision === "confirmed");
  const denied = reviewed.filter((record) => record.decision === "denied");
  const dominantByPair = new Map();
  const venueCounts = new Map();
  reviewed.forEach((record) => {
    const config = venueConfig(record, new Map());
    if (String(record.venue || "").includes("人工")) return;
    const key = configKey(record.pair, config.provider, config.market);
    venueCounts.set(key, (venueCounts.get(key) || 0) + 1);
  });
  [...new Set(reviewed.map((record) => record.pair))].forEach((pair) => {
    const best = [...venueCounts.entries()]
      .filter(([key]) => key.startsWith(`${pair}|`))
      .sort((a, b) => b[1] - a[1])[0];
    if (best) {
      const [, provider, market] = best[0].split("|");
      dominantByPair.set(pair, { provider, market });
    }
  });

  const pairVenueRecords = new Map();
  reviewed.forEach((record) => {
    const resolved = venueConfig(record, dominantByPair);
    const key = configKey(record.pair, resolved.provider, resolved.market);
    if (!pairVenueRecords.has(key)) pairVenueRecords.set(key, {
      key, pair: record.pair, ...resolved, records: [],
    });
    pairVenueRecords.get(key).records.push(record);
  });

  // A leader can have reviews separated by months or years. Treating the
  // earliest and latest review as one continuous replay window makes the
  // small-timeframe audit quadratic and can consume gigabytes of memory. Each
  // cluster still receives the fetcher's full interval-specific warm-up, so a
  // mother structure is not clipped merely to make the audit faster.
  const configs = new Map();
  // Replay each reviewed timestamp independently. Records on different
  // intervals but the exact same timestamp can share a window; any later
  // timestamp starts a fresh window. The fetcher already prepends 600-1600
  // candles per interval, which is the intended structural context.
  // 默认逐条做严格因果重放；局部迭代时可把同币同源记录合并为一个窗口，
  // 复用一次分析结果以便快速验证修改，最终全量审计仍保持逐条模式。
  const maxClusterSpanMs = pairWindow ? Number.POSITIVE_INFINITY : 0;
  for (const base of pairVenueRecords.values()) {
    const records = [...base.records].sort((a, b) => Number(a.signal?.time) - Number(b.signal?.time));
    let cluster = null;
    records.forEach((record) => {
      const time = Number(record.signal?.time) || 0;
      if (!cluster || time - cluster.minTime > maxClusterSpanMs) {
        const clusterIndex = cluster ? cluster.clusterIndex + 1 : 0;
        cluster = {
          ...base,
          key: `${base.key}|${clusterIndex}`,
          clusterIndex,
          records: [],
          minTime: time,
          maxTime: time,
        };
        configs.set(cluster.key, cluster);
      }
      cluster.records.push(record);
      cluster.minTime = Math.min(cluster.minTime, time);
      cluster.maxTime = Math.max(cluster.maxTime, time);
    });
  }

  const details = [];
  const dataErrors = [];
  for (const config of configs.values()) {
    process.stdout.write(`重跑 ${config.pair} ${config.provider}-${config.market}（${config.records.length} 个已复核点）...\n`);
    let payload;
    try {
      payload = loadOrFetch(config);
    } catch (error) {
      config.records.forEach((record) => dataErrors.push({
        pair: record.pair, interval: record.interval, time: record.signal.time,
        venue: record.venue, reason: error.message,
      }));
      continue;
    }
    const rawResults = [];
    const relevantIntervals = new Set(config.records.flatMap((record) => (
      AUDIT_NEIGHBORS[record.interval] || [record.interval]
    )));
    for (const interval of ["1m", "5m", "15m", "1h", "4h", "1d"]
      .filter((item) => relevantIntervals.has(item))
      .filter((item) => !requestedIntervals.size || requestedIntervals.has(item))) {
      const allCandles = payload.intervals?.[interval]?.candles || [];
      const meta = INTERVAL_AUDIT_META[interval];
      const sliceStart = config.minTime - meta.warmup * meta.ms;
      const sliceEnd = config.maxTime + 3 * meta.ms;
      const candles = allCandles.filter((candle) => candle.time >= sliceStart && candle.time <= sliceEnd);
      if (!candles.length) continue;
      rawResults.push(Engine.analyzeTimeframe(candles, {
        interval,
        now: Number(payload.intervals[interval].end) + 1,
        mainWaveStage: "active",
        mainWaveContextSource: "leader-default-main-wave",
        mainWaveContextLabel: "龙头默认主升浪环境",
      }));
    }
    const gated = Engine.applyContextGates(rawResults, [], {
      preselectedLeader: true,
      mainWaveStage: "active",
      mainWaveContextSource: "leader-default-main-wave",
      mainWaveContextLabel: "龙头默认主升浪环境",
    }).map((result) => Engine.enforceIntervalStructurePolicy(result));
    const resultByInterval = new Map(gated.map((result) => [result.interval, result]));
    for (const record of config.records) {
      const result = resultByInterval.get(record.interval);
      const time = Number(record.signal?.time);
      if (!result || !result.candles.some((candle) => candle.time === time)) {
        dataErrors.push({
          pair: record.pair, interval: record.interval, time, venue: record.venue,
          reason: payload.errors?.[record.interval] || "下载窗口内没有对应 K 线",
        });
        continue;
      }
      const observed = nativeStatusAt(result, time);
      const conflict = record.decision === "confirmed"
        ? observed.status !== "native-buy"
        : ["native-buy", "pending"].includes(observed.status);
      details.push({
        pair: record.pair,
        interval: record.interval,
        time,
        localTime: shanghaiTime(time),
        venue: record.venue,
        decision: record.decision,
        certaintyGrade: record.certaintyGrade || "未分级",
        reviewedTags: record.structureTags || [],
        status: observed.status,
        covered: record.decision === "confirmed" && observed.status === "native-buy",
        excluded: record.decision === "denied" && !conflict,
        conflict,
        currentPattern: observed.item?.pattern || "",
        currentScore: observed.item?.score ?? null,
        reasons: observed.status === "native-buy" ? [] : [...new Set(observed.item?.reasons || [])],
        features: featureSnapshot(observed.item),
      });
    }
  }

  const confirmedDetails = details.filter((item) => item.decision === "confirmed");
  const deniedDetails = details.filter((item) => item.decision === "denied");
  const nativeBuyCount = confirmedDetails.filter((item) => item.covered).length;
  const testedCount = confirmedDetails.length;
  const deniedExcludedCount = deniedDetails.filter((item) => item.excluded).length;
  const deniedConflictCount = deniedDetails.filter((item) => item.conflict).length;
  const statusCounts = Object.fromEntries(["native-buy", "pending", "filtered", "missing"]
    .map((status) => [status, confirmedDetails.filter((item) => item.status === status).length]));
  const groupMap = new Map();
  confirmedDetails.forEach((item) => {
    const key = `${item.pair}|${item.interval}`;
    if (!groupMap.has(key)) groupMap.set(key, { pair: item.pair, interval: item.interval, tested: 0, native: 0, conflicts: 0 });
    const group = groupMap.get(key);
    group.tested += 1;
    group.native += item.covered ? 1 : 0;
    group.conflicts += item.covered ? 0 : 1;
  });
  const report = {
    strategyVersion,
    generatedAt: Date.now(),
    feedbackDeviceId: feedback.deviceId,
    feedbackUpdatedAt: feedback.updatedAt,
    confirmedTotal: confirmed.length,
    deniedTotal: denied.length,
    testedCount,
    unavailableCount: dataErrors.length,
    nativeBuyCount,
    nativeHitRate: testedCount ? nativeBuyCount / testedCount : 0,
    statusCounts,
    deniedTestedCount: deniedDetails.length,
    deniedExcludedCount,
    deniedConflictCount,
    groups: [...groupMap.values()].sort((a, b) => a.pair.localeCompare(b.pair) || a.interval.localeCompare(b.interval)),
    details: details.sort((a, b) => a.pair.localeCompare(b.pair) || a.interval.localeCompare(b.interval) || a.time - b.time),
    dataErrors,
  };
  fs.mkdirSync(path.dirname(REPORT_JSON), { recursive: true });
  fs.writeFileSync(REPORT_JSON, JSON.stringify(report, null, 2), "utf8");

  const misses = report.details.filter((item) => item.decision === "confirmed" && !item.covered);
  const revived = report.details.filter((item) => item.decision === "denied" && item.conflict);
  const lines = [
    `# ${strategyVersion} 人工复核集双向原生审计`,
    "",
    `- 确认库：${report.confirmedTotal} 个`,
    `- 可重跑：${report.testedCount} 个`,
    `- 原生命中：${report.nativeBuyCount} 个（${(report.nativeHitRate * 100).toFixed(1)}%）`,
    `- 未覆盖行情：${report.unavailableCount} 个`,
    `- 待定 / 被过滤 / 无候选：${report.statusCounts.pending} / ${report.statusCounts.filtered} / ${report.statusCounts.missing}`,
    `- 彻底否定库：${report.deniedTotal} 个；可重跑 ${report.deniedTestedCount} 个`,
    `- 否定点保持排除：${report.deniedExcludedCount} 个；复活冲突 ${report.deniedConflictCount} 个`,
    "",
    "## 分组",
    "",
    "| 币种 | 周期 | 原生命中 | 可重跑 | 冲突 |",
    "|---|---:|---:|---:|---:|",
    ...report.groups.map((group) => `| ${group.pair} | ${group.interval} | ${group.native} | ${group.tested} | ${group.conflicts} |`),
    "",
    "## 未原生命中",
    "",
    ...(misses.length ? misses.map((item) => (
      `- ${item.pair} ${item.interval} ${item.localTime}：${item.status}${item.reasons[0] ? `；${item.reasons[0]}` : ""}`
    )) : ["- 无"]),
    "",
    "## 彻底否定点复活冲突",
    "",
    ...(revived.length ? revived.map((item) => (
      `- ${item.pair} ${item.interval} ${item.localTime}：${item.status}；${item.currentPattern || "无结构名"}`
    )) : ["- 无"]),
    "",
    "## 行情未覆盖",
    "",
    ...(dataErrors.length ? dataErrors.map((item) => (
      `- ${item.pair} ${item.interval} ${shanghaiTime(item.time)}（${item.venue}）：${item.reason}`
    )) : ["- 无"]),
    "",
    `> 口径：只统计当前 ${strategyVersion} 在同一根 K 线上的原生结果；人工永久恢复/黑名单都不算策略自身命中或排除。每根 K 线只使用当时及以前的数据生成特征。`,
  ];
  fs.writeFileSync(REPORT_MD, lines.join("\n"), "utf8");
  console.log(JSON.stringify({
    confirmedTotal: report.confirmedTotal,
    testedCount: report.testedCount,
    unavailableCount: report.unavailableCount,
    nativeBuyCount: report.nativeBuyCount,
    hitRatePercent: Number((report.nativeHitRate * 100).toFixed(1)),
    statusCounts: report.statusCounts,
    deniedTestedCount: report.deniedTestedCount,
    deniedExcludedCount: report.deniedExcludedCount,
    deniedConflictCount: report.deniedConflictCount,
    report: REPORT_MD,
  }, null, 2));
}

main();
