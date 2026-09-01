#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const Engine = require(path.join(__dirname, "..", "dragon-wave-engine.js"));

const INTERVAL_MS = Object.freeze({
  "1m": 60_000,
  "5m": 300_000,
  "15m": 900_000,
  "1h": 3_600_000,
  "4h": 14_400_000,
  "1d": 86_400_000,
});

const REPLAY_WARMUP = Object.freeze({
  "1m": 1_800,
  "5m": 900,
  "15m": 720,
  "1h": 600,
  "4h": 420,
  "1d": 300,
});

function argumentsOf(argv) {
  const options = { targets: [] };
  argv.forEach((argument) => {
    const separator = argument.indexOf("=");
    const key = separator >= 0 ? argument.slice(0, separator) : argument;
    const value = separator >= 0 ? argument.slice(separator + 1) : "";
    if (key === "--cache") options.cache = value;
    if (key === "--target") options.targets.push(value);
    if (key === "--brief") options.brief = true;
  });
  return options;
}

function parseTarget(value) {
  const comma = value.indexOf(",");
  if (comma < 1) throw new Error(`Invalid --target: ${value}`);
  const interval = value.slice(0, comma);
  const timeText = value.slice(comma + 1);
  const time = /^\d{13}$/.test(timeText) ? Number(timeText) : Date.parse(timeText);
  if (!INTERVAL_MS[interval] || !Number.isFinite(time)) throw new Error(`Invalid --target: ${value}`);
  return { interval, time };
}

function compact(item) {
  if (!item) return null;
  return {
    time: item.time,
    localTime: new Date(item.time).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }),
    status: item.status || "buy",
    pattern: item.pattern,
    patternKey: item.patternKey,
    structureShape: item.structureShape,
    score: item.score,
    certaintyScore: item.certaintyScore,
    triggerPrice: item.triggerPrice,
    level: item.level,
    consolidationBars: item.consolidationBars,
    structureQuality: item.structureQuality,
    foundationTypes: item.foundationTypes,
    auxiliaryTypes: item.auxiliaryTypes,
    confluence: item.confluence,
    displayConfluence: item.displayConfluence,
    outerEdgeConfirmed: item.outerEdgeConfirmed,
    outerEdgeScore: item.outerEdgeScore,
    platformTouchGroups: item.platformTouchGroups,
    channelInteriorOccupancy: item.channelInteriorOccupancy,
    channelMiddleParticipationRatio: item.channelMiddleParticipationRatio,
    channelHollowRatio: item.channelHollowRatio,
    channelLongestHollowRun: item.channelLongestHollowRun,
    channelSideTransitions: item.channelSideTransitions,
    triangleHasPriorAdvance: item.triangleHasPriorAdvance,
    trianglePriorAdvanceAtr: item.trianglePriorAdvanceAtr,
    triangleSelloffAtr: item.triangleSelloffAtr,
    motherStructureMode: item.motherStructureMode,
    motherStructurePosition: item.motherStructurePosition,
    motherStructureNoise: item.motherStructureNoise,
    matureTriangleOuterEdge: item.matureTriangleOuterEdge,
    hasPivot: item.hasPivot,
    aboveEma90: item.aboveEma90,
    ema90SlopeAtDecision: item.ema90SlopeAtDecision,
    rhythmScore: item.rhythmScore,
    sentimentScore: item.sentimentScore,
    horizontalLaunchQualified: item.horizontalLaunchQualified,
    horizontalLaunchHasPriorAdvance: item.horizontalLaunchHasPriorAdvance,
    horizontalLaunchPriorAdvanceAtr: item.horizontalLaunchPriorAdvanceAtr,
    horizontalLaunchUrgent: item.horizontalLaunchUrgent,
    horizontalStructureStartIndex: item.horizontalStructureStartIndex,
    riskStructureShape: item.riskStructureShape,
    riskStructureStartIndex: item.riskStructureStartIndex,
    directStructuralBoundary: item.directStructuralBoundary,
    openedBeyondTrigger: item.openedBeyondTrigger,
    relativeVolume: item.relativeVolume,
    orderFlowScore: item.orderFlowScore,
    klineVelocity: item.klineVelocity,
    ema90ReclaimQualified: item.ema90ReclaimQualified,
    matureOneHourOuterPlatformReset: item.matureOneHourOuterPlatformReset,
    oneHourRelaunchPivotIgnition: item.oneHourRelaunchPivotIgnition,
    oneHourCompactAscendingTriangleIgnition: item.oneHourCompactAscendingTriangleIgnition,
    matureFifteenMinuteRetryPlatformIgnition: item.matureFifteenMinuteRetryPlatformIgnition,
    shockMotherBoxOuterEdge: item.shockMotherBoxOuterEdge,
    reasons: item.reasons || [],
    evidence: item.evidence || [],
  };
}

function exactItems(result, time) {
  const groups = [
    ["buy", result?.signals],
    ["secondary-hint", result?.secondaryBreakoutHints],
    ["pending", result?.pending],
    ["filtered", result?.rejected],
  ];
  return groups.flatMap(([bucket, rows]) => (rows || [])
    .filter((item) => item.time === time)
    .map((item) => ({ bucket, ...compact(item) })));
}

function nearestItems(result, time, interval) {
  const radius = INTERVAL_MS[interval] * 3;
  const groups = [
    ["buy", result?.signals],
    ["secondary-hint", result?.secondaryBreakoutHints],
    ["pending", result?.pending],
    ["filtered", result?.rejected],
  ];
  return groups.flatMap(([bucket, rows]) => (rows || [])
    .filter((item) => Math.abs(item.time - time) <= radius)
    .map((item) => ({ bucket, ...compact(item) })))
    .sort((a, b) => a.time - b.time);
}

function main() {
  const options = argumentsOf(process.argv.slice(2));
  if (!options.cache || !options.targets.length) {
    throw new Error("Usage: replay_strategy_targets.js --cache=FILE --target=5m,2024-02-27T10:40:00+08:00");
  }
  const targets = options.targets.map(parseTarget);
  const payload = JSON.parse(fs.readFileSync(options.cache, "utf8"));
  const strategyOptions = {
    preselectedLeader: true,
    mainWaveStage: "active",
    mainWaveContextSource: "leader-default-main-wave",
    mainWaveContextLabel: "龙头默认主升浪环境",
  };
  const report = targets.map((target) => {
    const allCandles = payload.intervals?.[target.interval]?.candles || [];
    const intervalMs = INTERVAL_MS[target.interval];
    const start = target.time - REPLAY_WARMUP[target.interval] * intervalMs;
    const end = target.time + 3 * intervalMs;
    const candles = allCandles.filter((candle) => candle.time >= start && candle.time <= end);
    const rawResult = candles.length ? Engine.analyzeTimeframe(candles, {
      ...strategyOptions,
      interval: target.interval,
      now: end + intervalMs,
    }) : null;
    const result = rawResult
      ? Engine.enforceIntervalStructurePolicy(Engine.applyContextGates([rawResult], [], strategyOptions)[0])
      : null;
    const normalized = candles.length ? Engine.normalizeCandles(candles, end + intervalMs) : [];
    const targetIndex = normalized.findIndex((candle) => candle.time === target.time);
    const indicators = normalized.length ? {
      ema90: Engine.ema(normalized.map((candle) => candle.close), 90),
      atr: Engine.atr(normalized, 14),
    } : null;
    const candidates = targetIndex > 0
      ? Engine.findCandidates(normalized, targetIndex, indicators, {
        ...strategyOptions,
        interval: target.interval,
      })
      : [];
    return {
      target: {
        ...target,
        localTime: new Date(target.time).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }),
      },
      candlePresent: Boolean(result?.candles?.some((candle) => candle.time === target.time)),
      rawExact: exactItems(rawResult, target.time),
      exact: exactItems(result, target.time),
      nearest: nearestItems(result, target.time, target.interval),
      candidates: candidates.map((candidate) => ({
        type: candidate.type,
        level: candidate.level,
        triggerPrice: candidate.triggerPrice,
        crossedLevel: candidate.crossedLevel,
        openedBeyondTrigger: candidate.openedBeyondTrigger,
        confluence: candidate.confluence,
        foundationTypes: candidate.foundationTypes,
        auxiliaryTypes: candidate.auxiliaryTypes,
        consolidationBars: candidate.consolidationBars,
        structureShape: candidate.structureShape,
        quality: candidate.quality,
        directStructuralBoundary: candidate.directStructuralBoundary,
        channelInteriorOccupancy: candidate.channelInteriorOccupancy,
        channelMiddleParticipationRatio: candidate.channelMiddleParticipationRatio,
        channelHollowRatio: candidate.channelHollowRatio,
        channelLongestHollowRun: candidate.channelLongestHollowRun,
        channelSideTransitions: candidate.channelSideTransitions,
        upper: candidate.triangleLines?.upper,
      })),
    };
  });
  const output = options.brief ? report.map((row) => ({
    target: row.target,
    candlePresent: row.candlePresent,
    exact: row.exact.map((item) => ({
      bucket: item.bucket,
      localTime: item.localTime,
      pattern: item.pattern,
      structureShape: item.structureShape,
      score: item.score,
      certaintyScore: item.certaintyScore,
      triggerPrice: item.triggerPrice,
      consolidationBars: item.consolidationBars,
      foundationTypes: item.foundationTypes,
      auxiliaryTypes: item.auxiliaryTypes,
      outerEdgeConfirmed: item.outerEdgeConfirmed,
      outerEdgeScore: item.outerEdgeScore,
      matureTriangleOuterEdge: item.matureTriangleOuterEdge,
      directStructuralBoundary: item.directStructuralBoundary,
      structureQuality: item.structureQuality,
      channelInteriorOccupancy: item.channelInteriorOccupancy,
      channelMiddleParticipationRatio: item.channelMiddleParticipationRatio,
      channelHollowRatio: item.channelHollowRatio,
      channelLongestHollowRun: item.channelLongestHollowRun,
      channelSideTransitions: item.channelSideTransitions,
      triangleHasPriorAdvance: item.triangleHasPriorAdvance,
      trianglePriorAdvanceAtr: item.trianglePriorAdvanceAtr,
      horizontalLaunchHasPriorAdvance: item.horizontalLaunchHasPriorAdvance,
      horizontalLaunchPriorAdvanceAtr: item.horizontalLaunchPriorAdvanceAtr,
      rhythmScore: item.rhythmScore,
      sentimentScore: item.sentimentScore,
      orderFlowScore: item.orderFlowScore,
      relativeVolume: item.relativeVolume,
      klineVelocity: item.klineVelocity,
      aboveEma90: item.aboveEma90,
      ema90SlopeAtDecision: item.ema90SlopeAtDecision,
      reasons: item.reasons,
    })),
    nearest: row.nearest.map((item) => ({
      bucket: item.bucket,
      localTime: item.localTime,
      pattern: item.pattern,
      structureShape: item.structureShape,
      score: item.score,
      certaintyScore: item.certaintyScore,
      triggerPrice: item.triggerPrice,
      consolidationBars: item.consolidationBars,
      reasons: item.reasons,
    })),
    candidates: row.candidates.map((candidate) => ({
      type: candidate.type,
      level: candidate.level,
      triggerPrice: candidate.triggerPrice,
      crossedLevel: candidate.crossedLevel,
      openedBeyondTrigger: candidate.openedBeyondTrigger,
      foundationTypes: candidate.foundationTypes,
      auxiliaryTypes: candidate.auxiliaryTypes,
      consolidationBars: candidate.consolidationBars,
      structureShape: candidate.structureShape,
      quality: candidate.quality,
      directStructuralBoundary: candidate.directStructuralBoundary,
    })),
  })) : report;
  process.stdout.write(`${JSON.stringify(output, null, 2)}\n`);
}

try {
  main();
} catch (error) {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
}
