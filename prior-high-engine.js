(function attachPriorHighEngine(root, factory) {
  const engine = factory();
  if (typeof module === "object" && module.exports) module.exports = engine;
  root.PriorHighEngine = engine;
})(typeof globalThis !== "undefined" ? globalThis : this, function createPriorHighEngine() {
  "use strict";

  const VERSION = "v2-mother-range-outer-edge";
  const LEVEL_CLASS = Object.freeze({
    MICRO: "MICRO",
    INTERNAL: "INTERNAL",
    SWING: "SWING",
    MAJOR: "MAJOR",
    HISTORICAL: "HISTORICAL",
  });
  const CLASS_RANK = Object.freeze({ MICRO: 1, INTERNAL: 2, SWING: 3, MAJOR: 4, HISTORICAL: 5 });
  const DEFAULT_CONFIG = Object.freeze({
    atrPeriod: 14,
    pivotLeft: 4,
    pivotRight: 3,
    minProminenceAtr: 0.70,
    identityAtr: 0.35,
    identityPct: 0.0015,
    maxIdentityZoneAtr: 0.80,
    spatialResetAtr: 1.00,
    spatialResetPct: 0.010,
    temporalResetBars: 8,
    temporalBelowRatio: 0.60,
    rearmAtr: 1.00,
    rearmPct: 0.0050,
    rearmBelowCloses: 4,
    rearmMinBars: 4,
    triggerMode: "high",
    triggerBufferAtr: 0.03,
    triggerBufferPct: 0.0002,
    internalProminenceAtr: 1.00,
    swingProminenceAtr: 1.80,
    majorProminenceAtr: 3.00,
    historicalLookback: 300,
    historicalTolerancePct: 0.001,
    // 母盘整外沿审查：同一近期价格区间里仍存在更高的已确认前高时，
    // 较低枢轴只算箱体内部波动，不武装 B 或接近提醒。
    motherRangeOuterEdgeOnly: true,
    // 0 表示按周期换算约 7 天；测试或调用方可显式覆盖。
    motherRangeLookbackBars: 0,
    motherRangeMaxGapAtr: 10.0,
    motherRangeMaxGapPct: 0.12,
    motherRangeDominanceAtr: 0.20,
    motherRangeDominancePct: 0.001,
    alertMicro: false,
    // 提醒点只是“已武装压力位开始接近”，不参与前高是否成立的判断。
    nearThresholdAtr: 0.75,
    nearThresholdPct: 0.03,
  });

  function finite(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
  }

  function median(values) {
    if (!values.length) return 0;
    const sorted = [...values].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
  }

  function priceTolerance(price, atr, atrMultiple, percent) {
    return Math.max(
      Number.isFinite(atr) ? atrMultiple * atr : 0,
      percent * Math.abs(price),
      1e-12,
    );
  }

  function defaultMotherRangeLookback(nativeTf) {
    const normalized = String(nativeTf || "").toLowerCase();
    if (normalized === "1m") return 10_080;
    if (normalized === "5m") return 2_016;
    if (normalized === "15m") return 672;
    if (normalized === "30m") return 336;
    if (normalized === "1h" || normalized === "60m") return 168;
    if (normalized === "4h" || normalized === "240m") return 42;
    if (normalized === "1d" || normalized === "1day") return 30;
    return 168;
  }

  function normalizeCandles(rows) {
    return (Array.isArray(rows) ? rows : [])
      .map((row, index) => {
        if (Array.isArray(row)) {
          // 兼容实时前高旧数据：(time, high, close, open?, low?, volume?)。
          const close = finite(row[2]);
          return {
            time: finite(row[0]) || index,
            closeTime: finite(row.closeTime ?? row[0]) || index,
            open: finite(row[3]) || close,
            high: finite(row[1]),
            low: finite(row[4]) || Math.min(close, finite(row[3]) || close),
            close,
            volume: finite(row[5]),
          };
        }
        return {
          time: finite(row?.time ?? row?.openTime) || index,
          closeTime: finite(row?.closeTime ?? row?.time ?? row?.openTime) || index,
          open: finite(row?.open),
          high: finite(row?.high),
          low: finite(row?.low),
          close: finite(row?.close),
          volume: finite(row?.volume),
        };
      })
      .filter((row) => row.open > 0 && row.high >= row.low && row.low > 0 && row.close > 0)
      .sort((a, b) => a.time - b.time);
  }

  function calculateAtr(candles, period) {
    const output = Array(candles.length).fill(0);
    const trueRanges = [];
    let previousAtr = 0;
    const alpha = 1 / Math.max(1, period);
    candles.forEach((candle, index) => {
      const previousClose = index ? candles[index - 1].close : 0;
      const trueRange = index
        ? Math.max(candle.high - candle.low, Math.abs(candle.high - previousClose), Math.abs(candle.low - previousClose))
        : candle.high - candle.low;
      trueRanges.push(trueRange);
      previousAtr = index ? alpha * trueRange + (1 - alpha) * previousAtr : trueRange;
      output[index] = index + 1 < period ? median(trueRanges) : previousAtr;
    });
    return output;
  }

  function separationType(level) {
    if (level.spatialSeparated && level.temporalSeparated) return "SPATIAL+TEMPORAL";
    if (level.spatialSeparated) return "SPATIAL";
    if (level.temporalSeparated) return "TEMPORAL";
    return "NONE";
  }

  function analyze(rows, nativeTf = "5m", options = {}) {
    const config = { ...DEFAULT_CONFIG, ...(options || {}) };
    if (!(Number(config.motherRangeLookbackBars) > 0)) {
      config.motherRangeLookbackBars = defaultMotherRangeLookback(nativeTf);
    }
    const candles = normalizeCandles(rows);
    const atr = calculateAtr(candles, config.atrPeriod);
    const levels = [];
    const breakouts = [];
    const reminders = [];
    let nextLevelId = 1;

    const isPivotHigh = (pivot, confirmedAt) => {
      if (pivot - config.pivotLeft < 0
        || pivot + config.pivotRight > confirmedAt
        || pivot + config.pivotRight >= candles.length) return false;
      const high = candles[pivot].high;
      for (let index = pivot - config.pivotLeft; index < pivot; index += 1) {
        if (high < candles[index].high) return false;
      }
      for (let index = pivot + 1; index <= pivot + config.pivotRight; index += 1) {
        if (high <= candles[index].high) return false;
      }
      return true;
    };

    const prominenceAtr = (pivot, confirmedAt) => {
      let leftLow = Infinity;
      let rightLow = Infinity;
      for (let index = Math.max(0, pivot - config.pivotLeft); index <= pivot; index += 1) {
        leftLow = Math.min(leftLow, candles[index].low);
      }
      for (let index = pivot; index <= Math.min(confirmedAt, pivot + config.pivotRight); index += 1) {
        rightLow = Math.min(rightLow, candles[index].low);
      }
      return Math.max(0, Math.min(candles[pivot].high - leftLow, candles[pivot].high - rightLow))
        / Math.max(atr[confirmedAt], 1e-12);
    };

    const classifyLevel = (pivot, prominence) => {
      const price = candles[pivot].high;
      const start = Math.max(0, pivot - config.historicalLookback);
      if (pivot > start) {
        let previousMaximum = -Infinity;
        for (let index = start; index < pivot; index += 1) previousMaximum = Math.max(previousMaximum, candles[index].high);
        if (previousMaximum > 0 && price >= previousMaximum * (1 - config.historicalTolerancePct)) {
          return LEVEL_CLASS.HISTORICAL;
        }
      }
      if (prominence >= config.majorProminenceAtr) return LEVEL_CLASS.MAJOR;
      if (prominence >= config.swingProminenceAtr) return LEVEL_CLASS.SWING;
      if (prominence >= config.internalProminenceAtr) return LEVEL_CLASS.INTERNAL;
      return LEVEL_CLASS.MICRO;
    };

    const addOrMergePivot = (pivot, confirmedAt) => {
      const prominence = prominenceAtr(pivot, confirmedAt);
      if (prominence < config.minProminenceAtr) return;
      const price = candles[pivot].high;
      const currentAtr = atr[confirmedAt];
      const tolerance = priceTolerance(price, currentAtr, config.identityAtr, config.identityPct);
      const mergeTarget = levels
        .filter((level) => level.active && Math.abs(price - level.anchor) <= tolerance)
        .sort((left, right) => Math.abs(price - left.anchor) - Math.abs(price - right.anchor))[0];
      const levelClass = classifyLevel(pivot, prominence);
      if (mergeTarget) {
        mergeTarget.touchCount += 1;
        mergeTarget.sourcePivots.push(pivot);
        mergeTarget.prominenceAtr = Math.max(mergeTarget.prominenceAtr, prominence);
        if (CLASS_RANK[levelClass] > CLASS_RANK[mergeTarget.levelClass]) mergeTarget.levelClass = levelClass;
        const candidateLow = Math.min(mergeTarget.zoneLow, price);
        const candidateHigh = Math.max(mergeTarget.zoneHigh, price);
        if (candidateHigh - candidateLow <= config.maxIdentityZoneAtr * Math.max(currentAtr, 1e-12)) {
          mergeTarget.zoneLow = candidateLow;
          mergeTarget.zoneHigh = candidateHigh;
        }
        return;
      }
      levels.push({
        levelId: nextLevelId,
        nativeTf,
        levelClass,
        anchor: price,
        zoneLow: price,
        zoneHigh: price,
        createdBar: pivot,
        confirmedBar: confirmedAt,
        createdTime: candles[pivot].time,
        confirmedTime: candles[confirmedAt].time,
        atrAtConfirm: currentAtr,
        prominenceAtr: prominence,
        touchCount: 1,
        breakoutCount: 0,
        spatialSeparated: false,
        temporalSeparated: false,
        separationComplete: false,
        armed: false,
        triggered: false,
        nearActive: false,
        remindedSinceArm: false,
        minLowSinceConfirm: Infinity,
        barsSinceConfirm: 0,
        belowCloseCount: 0,
        belowCloseStreak: 0,
        active: true,
        sourcePivots: [pivot],
        lastAlertBar: -1,
        lastReminderBar: -1,
      });
      nextLevelId += 1;
    };

    const processLevel = (level, index) => {
      if (!level.active || index <= level.confirmedBar) return;
      const candle = candles[index];
      const reference = Math.max(level.anchor, level.zoneHigh);
      level.barsSinceConfirm += 1;
      level.minLowSinceConfirm = Math.min(level.minLowSinceConfirm, candle.low);
      if (candle.close < reference) level.belowCloseCount += 1;
      const spatialGap = priceTolerance(reference, atr[index], config.spatialResetAtr, config.spatialResetPct);
      if (level.minLowSinceConfirm <= reference - spatialGap) level.spatialSeparated = true;
      if (level.barsSinceConfirm >= config.temporalResetBars
        && level.belowCloseCount / Math.max(1, level.barsSinceConfirm) >= config.temporalBelowRatio) {
        level.temporalSeparated = true;
      }
      level.separationComplete = level.spatialSeparated || level.temporalSeparated;
      if (level.separationComplete && !level.triggered) level.armed = true;

      if (level.triggered) {
        level.belowCloseStreak = candle.close < reference ? level.belowCloseStreak + 1 : 0;
        const rearmGap = priceTolerance(reference, atr[index], config.rearmAtr, config.rearmPct);
        const resetMature = index - level.lastAlertBar >= config.rearmMinBars;
        if (resetMature
          && (candle.low <= reference - rearmGap || level.belowCloseStreak >= config.rearmBelowCloses)) {
          level.triggered = false;
          level.armed = true;
          level.nearActive = false;
          level.remindedSinceArm = false;
        }
      }

      if (!level.armed || !level.separationComplete || index <= 0) return;

      if (config.motherRangeOuterEdgeOnly) {
        const start = Math.max(0, index - config.motherRangeLookbackBars);
        const maxGap = priceTolerance(
          reference,
          atr[index],
          config.motherRangeMaxGapAtr,
          config.motherRangeMaxGapPct,
        );
        const dominance = priceTolerance(
          reference,
          atr[index],
          config.motherRangeDominanceAtr,
          config.motherRangeDominancePct,
        );
        const higherMotherEdge = levels.some((other) => other !== level
          && other.active
          && other.confirmedBar < index
          && other.createdBar >= start
          && Math.max(other.anchor, other.zoneHigh) > reference + dominance
          && Math.max(other.anchor, other.zoneHigh) - reference <= maxGap);
        if (higherMotherEdge) {
          level.nearActive = false;
          return;
        }
      }

      const triggerBuffer = priceTolerance(reference, atr[index], config.triggerBufferAtr, config.triggerBufferPct);
      const threshold = reference + triggerBuffer;
      const previousClose = candles[index - 1].close;
      const triggerPrice = config.triggerMode === "close" ? candle.close : candle.high;
      const crossed = previousClose <= threshold && triggerPrice > threshold;
      if (crossed && level.lastAlertBar !== index) {
        level.breakoutCount += 1;
        level.armed = false;
        level.triggered = true;
        level.nearActive = false;
        level.belowCloseStreak = 0;
        level.lastAlertBar = index;
        if (level.levelClass !== LEVEL_CLASS.MICRO || config.alertMicro) {
          breakouts.push({
            barIndex: index,
            time: candle.time,
            nativeTf,
            levelId: level.levelId,
            levelClass: level.levelClass,
            anchor: reference,
            zoneLow: level.zoneLow,
            zoneHigh: level.zoneHigh,
            triggerPrice,
            breakoutCount: level.breakoutCount,
            separationType: separationType(level),
            spatialSeparated: level.spatialSeparated,
            temporalSeparated: level.temporalSeparated,
            prominenceAtr: level.prominenceAtr,
            touchCount: level.touchCount,
          });
        }
        return;
      }

      const nearGap = priceTolerance(level.anchor, atr[index], config.nearThresholdAtr, config.nearThresholdPct);
      const nearFloor = threshold - nearGap;
      if (candle.close < nearFloor) level.nearActive = false;
      const enteredNear = !level.nearActive
        && !level.remindedSinceArm
        && previousClose < nearFloor
        && candle.high >= nearFloor
        && triggerPrice <= threshold;
      if (enteredNear && level.lastReminderBar !== index
        && (level.levelClass !== LEVEL_CLASS.MICRO || config.alertMicro)) {
        level.nearActive = true;
        level.remindedSinceArm = true;
        level.lastReminderBar = index;
        reminders.push({
          barIndex: index,
          time: candle.time,
          nativeTf,
          levelId: level.levelId,
          levelClass: level.levelClass,
          anchor: reference,
          triggerPrice: threshold,
          currentPrice: candle.close,
          distancePct: Math.max(0, (reference - candle.close) / reference * 100),
          separationType: separationType(level),
          touchCount: level.touchCount,
        });
      }
    };

    candles.forEach((_candle, index) => {
      const pivot = index - config.pivotRight;
      if (pivot >= 0 && isPivotHigh(pivot, index)) addOrMergePivot(pivot, index);
      [...levels].sort((left, right) => left.levelId - right.levelId)
        .forEach((level) => processLevel(level, index));
    });

    const keepHighestOuterEdgePerBar = (events) => {
      const selected = new Map();
      events.forEach((event) => {
        const current = selected.get(event.barIndex);
        if (!current || event.anchor > current.anchor) selected.set(event.barIndex, event);
      });
      return [...selected.values()].sort((left, right) => left.barIndex - right.barIndex);
    };

    return {
      version: VERSION,
      nativeTf,
      config,
      candles,
      atr,
      levels,
      breakouts: keepHighestOuterEdgePerBar(breakouts),
      reminders: keepHighestOuterEdgePerBar(reminders),
    };
  }

  function toChartResult(analysis, sourceResult = {}) {
    const candles = analysis.candles;
    const classScore = { MICRO: 62, INTERNAL: 74, SWING: 84, MAJOR: 92, HISTORICAL: 96 };
    const signals = analysis.breakouts.map((event) => ({
      id: `prior-high-${event.nativeTf}-${event.barIndex}-${event.levelId}-${event.breakoutCount}`,
      index: event.barIndex,
      time: event.time,
      interval: event.nativeTf,
      pattern: `前高突破 · ${event.levelClass}`,
      patternKey: "previousHigh",
      status: "buy",
      price: candles[event.barIndex]?.close || event.triggerPrice,
      triggerPrice: event.triggerPrice,
      level: event.anchor,
      score: classScore[event.levelClass] || 70,
      certaintyScore: classScore[event.levelClass] || 70,
      confluence: ["previousHigh"],
      foundationTypes: ["previousHigh"],
      auxiliaryTypes: [],
      previousHighLevelId: event.levelId,
      previousHighClass: event.levelClass,
      breakoutCount: event.breakoutCount,
      reasons: [],
      evidence: [
        `${event.levelClass} 级结构前高 ${event.anchor}`,
        `${event.separationType} 分离后从下方重新穿越`,
        `同一压力区 ${event.touchCount} 次触碰 · 第 ${event.breakoutCount} 次有效突破`,
      ],
    }));
    const pending = analysis.reminders.map((event) => ({
      id: `prior-high-reminder-${event.nativeTf}-${event.barIndex}-${event.levelId}`,
      index: event.barIndex,
      time: event.time,
      interval: event.nativeTf,
      pattern: `接近前高 · ${event.levelClass}`,
      patternKey: "previousHigh",
      status: "pending",
      price: event.currentPrice,
      triggerPrice: event.triggerPrice,
      level: event.anchor,
      score: classScore[event.levelClass] || 70,
      certaintyScore: classScore[event.levelClass] || 70,
      confluence: ["previousHigh"],
      foundationTypes: ["previousHigh"],
      auxiliaryTypes: [],
      previousHighReminder: true,
      reasons: [],
      evidence: [
        `已完成 ${event.separationType} 分离并进入前高提醒区`,
        `距离压力位 ${event.distancePct.toFixed(2)}% · 等待首次上穿`,
      ],
    }));
    const sourceIndicators = sourceResult.indicators || {};
    const indicators = {
      ...sourceIndicators,
      ema90: Array.isArray(sourceIndicators.ema90) ? sourceIndicators.ema90 : candles.map(() => null),
      atr: analysis.atr,
    };
    return {
      ...sourceResult,
      interval: analysis.nativeTf,
      candles,
      indicators,
      signals,
      pending,
      secondaryBreakoutHints: [],
      retainedCandidates: [],
      rejected: [],
      structures: [],
      priorHighLevels: analysis.levels,
      priorHighAnalysisVersion: analysis.version,
      stats: {
        ...(sourceResult.stats || {}),
        lastPrice: candles.at(-1)?.close || 0,
        signalCount: signals.length,
        pendingCount: pending.length,
        secondaryBreakoutHintCount: 0,
        retainedCandidateCount: 0,
        rejectedCount: 0,
      },
    };
  }

  function analyzeForChart(sourceResult, nativeTf, options = {}) {
    return toChartResult(analyze(sourceResult?.candles || [], nativeTf, options), sourceResult || {});
  }

  return Object.freeze({
    VERSION,
    LEVEL_CLASS,
    DEFAULT_CONFIG,
    normalizeCandles,
    calculateAtr,
    analyze,
    toChartResult,
    analyzeForChart,
  });
});
