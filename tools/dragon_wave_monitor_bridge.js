"use strict";

const fs = require("node:fs");
const Engine = require("../dragon-wave-engine.js");

const INTERVAL_MS = Object.freeze({
  "1m": 60_000,
  "5m": 5 * 60_000,
  "15m": 15 * 60_000,
  "1h": 60 * 60_000,
  "4h": 4 * 60 * 60_000,
  "1d": 24 * 60 * 60_000,
});

const INTERVAL_LABELS = Object.freeze({
  "1m": "1分钟",
  "5m": "5分钟",
  "15m": "15分钟",
  "1h": "1小时",
  "4h": "4小时",
  "1d": "日线",
});

const DISPLAY_INTERVALS = Object.freeze(["1m", "5m", "15m", "1h", "4h", "1d"]);

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function normalizeRows(rows, interval) {
  const intervalMs = INTERVAL_MS[interval];
  if (!intervalMs || !Array.isArray(rows)) return [];
  return rows.map((row) => {
    if (Array.isArray(row)) {
      const time = finite(row[0]);
      return {
        time,
        closeTime: time + intervalMs - 1,
        open: finite(row[1]),
        high: finite(row[2]),
        low: finite(row[3]),
        close: finite(row[4]),
        volume: finite(row[5]),
      };
    }
    const time = finite(row?.time ?? row?.openTime);
    return {
      time,
      closeTime: finite(row?.closeTime) || time + intervalMs - 1,
      open: finite(row?.open),
      high: finite(row?.high),
      low: finite(row?.low),
      close: finite(row?.close),
      volume: finite(row?.volume),
    };
  }).filter((row) => (
    row.time > 0
    && row.open > 0
    && row.high > 0
    && row.low > 0
    && row.close > 0
  ));
}

function latest(items) {
  return (Array.isArray(items) ? items : [])
    .slice()
    .sort((left, right) => (
      finite(right.decisionTime ?? right.time) - finite(left.decisionTime ?? left.time)
      || finite(right.score) - finite(left.score)
    ))[0] || null;
}

function displayPattern(item) {
  if (!item) return "无明确结构";
  return String(item.pattern || item.structureLabel || item.structureShape || "无明确结构")
    .replaceAll("三角突破", "三角")
    .replaceAll("回踩再点火", "拐点再启动");
}

function compactSignal(signal, interval, lastIndex) {
  const time = finite(signal.decisionTime ?? signal.time);
  const certainty = Math.round(finite(signal.certaintyScore));
  return {
    id: String(signal.id || `${interval}-${time}`),
    interval,
    label: INTERVAL_LABELS[interval],
    time: finite(signal.time),
    decisionTime: time,
    index: finite(signal.index),
    pattern: displayPattern(signal),
    score: Math.round(finite(signal.score)),
    certainty,
    grade: String(signal.manualCertaintyGrade || (certainty >= 90 ? "A+" : certainty >= 80 ? "A" : "B")),
    price: finite(signal.price ?? signal.triggerPrice),
    triggerPrice: finite(signal.triggerPrice),
    breakoutOpen: finite(signal.breakoutOpen),
    breakoutLow: finite(signal.breakoutLow),
    stop: finite(signal.stop),
    multiTimeframeConfluence: signal.multiTimeframeConfluence === true,
    crossFrameDirection: String(signal.crossFrameDirection || ""),
    lowerTimeframeTrigger: String(signal.lowerTimeframeTrigger || ""),
    lowerTimeframeTriggerId: String(signal.lowerTimeframeTriggerId || ""),
    mainWaveStage: String(signal.mainWaveStage || "neutral"),
    mainWaveContextSource: String(signal.mainWaveContextSource || ""),
    adaptiveMode: String(signal.adaptiveMode || ""),
    adaptiveLabel: String(signal.adaptiveLabel || ""),
    secondaryBreakoutHint: signal.secondaryBreakoutHint === true,
    alertOnly: signal.alertOnly === true,
    barsAgo: Math.max(0, lastIndex - finite(signal.index)),
  };
}

function monitorFrame(result) {
  const interval = result.interval;
  const lastIndex = Math.max(0, (result.candles || []).length - 1);
  const fresh = (item) => finite(item?.index) >= lastIndex - 1;
  const buy = latest((result.signals || []).filter(fresh));
  const secondaryHint = latest((result.secondaryBreakoutHints || []).filter(fresh));
  const pending = latest((result.pending || []).filter(fresh));
  const structure = latest((result.structures || []).filter((item) => {
    const endIndex = finite(item.endIndex ?? item.index);
    return endIndex >= lastIndex - 2;
  }));
  const selected = buy || secondaryHint || pending || structure;
  const stage = buy
    ? (buy.multiTimeframeConfluence ? "多周期A+起爆" : "买点触发")
    : secondaryHint
      ? "二次突破提示"
    : pending
      ? "预备起爆"
      : structure
        ? "结构观察"
        : "观察";
  const confidence = Math.round(finite(
    selected?.certaintyScore
      ?? (finite(selected?.quality) * 100),
  ));
  const support = finite(selected?.stop ?? selected?.lowerLevel ?? selected?.support);
  const resistance = finite(selected?.triggerPrice ?? selected?.level ?? selected?.upperLevel ?? selected?.resistance);
  return {
    key: interval,
    label: INTERVAL_LABELS[interval],
    pattern: displayPattern(selected),
    stage,
    confidence,
    support: support || null,
    resistance: resistance || null,
    summary: buy
      ? `${INTERVAL_LABELS[interval]} ${displayPattern(buy)}，${buy.multiTimeframeConfluence ? "相邻周期共振确认" : "策略买点已触发"}`
      : secondaryHint
        ? `${INTERVAL_LABELS[interval]} ${displayPattern(secondaryHint)}，红色B只作防洗踏空提醒`
      : pending
        ? `${INTERVAL_LABELS[interval]} ${displayPattern(pending)}，等待真实突破触发`
        : structure
          ? `${INTERVAL_LABELS[interval]} ${displayPattern(structure)}，当前只作结构观察`
          : "当前尚无符合龙头起爆策略的高确定性结构",
    signal: buy ? compactSignal(buy, interval, lastIndex) : null,
    alertHint: !buy && secondaryHint ? compactSignal(secondaryHint, interval, lastIndex) : null,
    pending: pending ? compactSignal(pending, interval, lastIndex) : null,
  };
}

function analyzeMonitorPayload(input) {
  const now = finite(input?.now) || Date.now();
  const timeframes = input?.timeframes && typeof input.timeframes === "object"
    ? input.timeframes
    : {};
  const options = { preselectedLeader: input?.preselectedLeader !== false };
  if (["active", "expected"].includes(input?.mainWaveStage)) {
    options.mainWaveStage = input.mainWaveStage;
    options.mainWaveContextSource = String(input?.mainWaveContextSource || "live-analysis");
    options.mainWaveContextLabel = String(input?.mainWaveContextLabel || "临盘应变判断");
  } else if (options.preselectedLeader) {
    options.mainWaveStage = "active";
    options.mainWaveContextSource = "leader-default-main-wave";
    options.mainWaveContextLabel = "龙头默认主升浪环境";
  }
  const rawResults = DISPLAY_INTERVALS.map((interval) => {
    const candles = normalizeRows(timeframes[interval], interval);
    if (!candles.length) return null;
    return Engine.analyzeTimeframe(candles, { ...options, interval, now });
  }).filter(Boolean);
  const adaptiveContext = input?.adaptiveContext && typeof input.adaptiveContext === "object"
    ? input.adaptiveContext
    : null;
  const decorate = (item) => adaptiveContext ? {
    ...item,
    adaptiveMode: String(adaptiveContext.mode || ""),
    adaptiveLabel: String(adaptiveContext.label || ""),
    adaptiveContextSource: String(adaptiveContext.sourceKind || ""),
  } : item;
  const gated = Engine.applyContextGates(rawResults, [], options)
    .map((result) => Engine.enforceIntervalStructurePolicy({
      ...result,
      signals: (result.signals || []).map(decorate),
      secondaryBreakoutHints: (result.secondaryBreakoutHints || []).map(decorate),
      pending: (result.pending || []).map(decorate),
      rejected: (result.rejected || []).map(decorate),
    }));
  const resultByInterval = new Map(gated.map((result) => [result.interval, result]));
  const frames = DISPLAY_INTERVALS.map((interval) => {
    const result = resultByInterval.get(interval);
    if (result) return monitorFrame(result);
    return {
      key: interval,
      label: INTERVAL_LABELS[interval],
      pattern: "数据不足",
      stage: "等待",
      confidence: 0,
      support: null,
      resistance: null,
      summary: "有效K线不足",
      signal: null,
      alertHint: null,
      pending: null,
    };
  });
  const signals = frames.map((frame) => frame.signal).filter(Boolean);
  const alertHints = frames.map((frame) => frame.alertHint).filter(Boolean);
  return {
    ok: true,
    strategy: "dragon-wave-engine",
    strategyVersion: String(input?.strategyVersion || "shared-live"),
    frames,
    signals,
    alertHints,
    signalCount: signals.length,
    alertHintCount: alertHints.length,
    adaptiveContext,
  };
}

if (require.main === module) {
  try {
    const input = JSON.parse(fs.readFileSync(0, "utf8") || "{}");
    process.stdout.write(JSON.stringify(analyzeMonitorPayload(input)));
  } catch (error) {
    process.stderr.write(String(error?.stack || error));
    process.exitCode = 1;
  }
}

module.exports = { analyzeMonitorPayload, normalizeRows, monitorFrame };
