(function attachDragonWaveEngine(root, factory) {
  const vision = typeof module === "object" && module.exports
    ? require("./dragon-wave-vision.js")
    : root.DragonWaveVision;
  const engine = factory(vision);
  if (typeof module === "object" && module.exports) module.exports = engine;
  root.DragonWaveEngine = engine;
})(typeof globalThis !== "undefined" ? globalThis : this, function createEngine(Vision) {
  "use strict";

  const PATTERN_LABELS = Object.freeze({
    base: "横盘起飞",
    previousHigh: "突破前高",
    triangle: "三角突破",
    trendline: "趋势线突破",
    pivot: "拐点收复",
    relaunch: "回踩再点火",
  });

  // 横盘 / 三角 / 有效回踩决定“有没有可交易结构”；趋势线和前高只负责最后触发，
  // 不能因为多画出一条线就把普通波动升级成买点。
  const PATTERN_PRIORITY = Object.freeze({ base: 5, triangle: 4.9, relaunch: 4.5, pivot: 2.5, trendline: 1.4, previousHigh: 1.2 });
  const FOUNDATION_PATTERNS = new Set(["base", "triangle", "relaunch"]);
  const AUXILIARY_PATTERNS = new Set(["trendline", "previousHigh"]);
  const DEFAULT_SLIPPAGE_BPS = 8;
  const INTERVAL_MS = Object.freeze({
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
  });

  function finite(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function mean(values) {
    if (!values.length) return 0;
    return values.reduce((sum, value) => sum + finite(value), 0) / values.length;
  }

  function maxBy(values, getter) {
    let best = null;
    let bestValue = -Infinity;
    values.forEach((value, index) => {
      const current = getter(value, index);
      if (current > bestValue) {
        bestValue = current;
        best = value;
      }
    });
    return best;
  }

  function normalizeCandles(rows, now = Date.now()) {
    return (Array.isArray(rows) ? rows : [])
      .map((row) => ({
        time: finite(row.time ?? row.openTime),
        closeTime: finite(row.closeTime ?? row.time ?? row.openTime),
        open: finite(row.open),
        high: finite(row.high),
        low: finite(row.low),
        close: finite(row.close),
        volume: finite(row.volume),
        quoteVolume: finite(row.quoteVolume),
        takerBuyVolume: finite(row.takerBuyVolume),
        tradeCount: finite(row.tradeCount),
      }))
      .filter((row) => row.time > 0 && row.closeTime <= now && row.open > 0 && row.high >= row.low && row.close > 0)
      .sort((a, b) => a.time - b.time);
  }

  function ema(values, period) {
    const output = Array(values.length).fill(null);
    if (!values.length || period <= 0) return output;
    const multiplier = 2 / (period + 1);
    let current = finite(values[0]);
    output[0] = current;
    for (let index = 1; index < values.length; index += 1) {
      current = finite(values[index]) * multiplier + current * (1 - multiplier);
      output[index] = current;
    }
    return output;
  }

  function atr(candles, period = 14) {
    const trueRanges = candles.map((candle, index) => {
      if (index === 0) return candle.high - candle.low;
      const previousClose = candles[index - 1].close;
      return Math.max(
        candle.high - candle.low,
        Math.abs(candle.high - previousClose),
        Math.abs(candle.low - previousClose),
      );
    });
    return ema(trueRanges, period);
  }

  function rollingMean(values, period) {
    const output = Array(values.length).fill(null);
    let sum = 0;
    for (let index = 0; index < values.length; index += 1) {
      sum += finite(values[index]);
      if (index >= period) sum -= finite(values[index - period]);
      output[index] = sum / Math.min(period, index + 1);
    }
    return output;
  }

  function regressionSlope(values) {
    if (values.length < 2) return 0;
    const xMean = (values.length - 1) / 2;
    const yMean = mean(values);
    let numerator = 0;
    let denominator = 0;
    values.forEach((value, index) => {
      numerator += (index - xMean) * (value - yMean);
      denominator += (index - xMean) ** 2;
    });
    return denominator ? numerator / denominator : 0;
  }

  function regressionSlopeAtPositions(points) {
    if (points.length < 2) return 0;
    const xMean = mean(points.map((point) => point.x));
    const yMean = mean(points.map((point) => point.y));
    let numerator = 0;
    let denominator = 0;
    points.forEach((point) => {
      numerator += (point.x - xMean) * (point.y - yMean);
      denominator += (point.x - xMean) ** 2;
    });
    return denominator ? numerator / denominator : 0;
  }

  function quantile(values, ratio) {
    if (!values.length) return 0;
    const ordered = values.map(finite).sort((a, b) => a - b);
    const position = clamp(ratio, 0, 1) * (ordered.length - 1);
    const lower = Math.floor(position);
    const upper = Math.ceil(position);
    if (lower === upper) return ordered[lower];
    const weight = position - lower;
    return ordered[lower] * (1 - weight) + ordered[upper] * weight;
  }

  function fitQuantileEnvelope(values, slope, side) {
    const xMean = (values.length - 1) / 2;
    const residuals = values.map((value, index) => finite(value) - slope * (index - xMean));
    const intercept = quantile(residuals, side === "upper" ? 0.975 : 0.025);
    return {
      slope,
      lineAt: (cursor) => intercept + slope * (cursor - xMean),
    };
  }

  function estimateBoundarySlope(values, side) {
    if (values.length < 12) return regressionSlope(values);
    const segmentCount = clamp(Math.floor(values.length / 8), 4, 8);
    const points = [];
    for (let segment = 0; segment < segmentCount; segment += 1) {
      const start = Math.floor(segment * values.length / segmentCount);
      const end = Math.floor((segment + 1) * values.length / segmentCount);
      const bucket = values.slice(start, end);
      if (!bucket.length) continue;
      points.push({
        x: (start + end - 1) / 2,
        y: quantile(bucket, side === "upper" ? 0.88 : 0.12),
      });
    }
    return regressionSlopeAtPositions(points);
  }

  function assessBoundaryContainment(candles, start, end, lineAt, side, atrValue, anchorMode = "wick") {
    const rows = candles.slice(start, end);
    if (!rows.length || typeof lineAt !== "function") {
      return { acceptable: false, structuralCoverage: 0, bodyCoverage: 0, wickCoverage: 0, crossingRatio: 1, crossingBars: 0, outsideTransitions: 0 };
    }
    const bodyTolerance = Math.max(atrValue * 0.12, 1e-8);
    const wickTolerance = Math.max(atrValue * 0.38, bodyTolerance);
    let structuralInside = 0;
    let bodyInside = 0;
    let wickInside = 0;
    let crossingBars = 0;
    let outsideTransitions = 0;
    let previousSide = null;
    rows.forEach((row, offset) => {
      const cursor = start + offset;
      const line = lineAt(cursor);
      const bodyHigh = Math.max(row.open, row.close);
      const bodyLow = Math.min(row.open, row.close);
      const structuralValue = side === "upper"
        ? (anchorMode === "body" ? bodyHigh : row.high)
        : (anchorMode === "body" ? bodyLow : row.low);
      const structuralContained = side === "upper"
        ? structuralValue <= line + bodyTolerance
        : structuralValue >= line - bodyTolerance;
      const bodyContained = side === "upper"
        ? bodyHigh <= line + bodyTolerance
        : bodyLow >= line - bodyTolerance;
      const wickContained = side === "upper"
        ? row.high <= line + wickTolerance
        : row.low >= line - wickTolerance;
      structuralInside += Number(structuralContained);
      bodyInside += Number(bodyContained);
      wickInside += Number(wickContained);
      if (bodyLow < line - bodyTolerance && bodyHigh > line + bodyTolerance) crossingBars += 1;
      const currentSide = bodyLow > line + bodyTolerance ? "above" : bodyHigh < line - bodyTolerance ? "below" : null;
      if (currentSide && previousSide && currentSide !== previousSide) outsideTransitions += 1;
      if (currentSide) previousSide = currentSide;
    });
    const count = rows.length;
    const structuralCoverage = structuralInside / count;
    const bodyCoverage = bodyInside / count;
    const wickCoverage = wickInside / count;
    const crossingRatio = crossingBars / count;
    const envelopeScore = clamp(
      structuralCoverage * 0.42
      + bodyCoverage * 0.33
      + wickCoverage * 0.15
      + (1 - crossingRatio) * 0.1,
      0,
      1,
    );
    return {
      acceptable: structuralCoverage >= 0.93
        && bodyCoverage >= 0.94
        && wickCoverage >= 0.86
        && crossingRatio <= 0.08
        && outsideTransitions <= 2,
      structuralCoverage,
      bodyCoverage,
      wickCoverage,
      crossingRatio,
      crossingBars,
      outsideTransitions,
      envelopeScore,
    };
  }

  function assessEnvelopeCoverage(candles, start, end, upperAt, lowerAt, atrValue, upperMode = "wick", lowerMode = "wick") {
    const upper = assessBoundaryContainment(candles, start, end, upperAt, "upper", atrValue, upperMode);
    const lower = assessBoundaryContainment(candles, start, end, lowerAt, "lower", atrValue, lowerMode);
    const rows = candles.slice(start, end);
    const bodyTolerance = Math.max(atrValue * 0.12, 1e-8);
    const wickTolerance = Math.max(atrValue * 0.38, bodyTolerance);
    let bodyInside = 0;
    let wickInside = 0;
    let crossingBars = 0;
    let validChannelRows = 0;
    let wideChannelRows = 0;
    let middleParticipationRows = 0;
    let hollowRows = 0;
    let currentHollowRun = 0;
    let longestHollowRun = 0;
    let previousBoundarySide = null;
    let previousHollowSide = null;
    let channelSideTransitions = 0;
    const segmentCount = rows.length >= 48 ? 4 : rows.length >= 24 ? 3 : 1;
    const verticalBandCount = 8;
    const segmentBands = Array.from({ length: segmentCount }, () => new Set());
    rows.forEach((row, offset) => {
      const cursor = start + offset;
      const upperLine = upperAt(cursor);
      const lowerLine = lowerAt(cursor);
      const bodyHigh = Math.max(row.open, row.close);
      const bodyLow = Math.min(row.open, row.close);
      bodyInside += Number(bodyHigh <= upperLine + bodyTolerance && bodyLow >= lowerLine - bodyTolerance);
      wickInside += Number(row.high <= upperLine + wickTolerance && row.low >= lowerLine - wickTolerance);
      crossingBars += Number(
        (bodyLow < upperLine - bodyTolerance && bodyHigh > upperLine + bodyTolerance)
        || (bodyLow < lowerLine - bodyTolerance && bodyHigh > lowerLine + bodyTolerance),
      );

      // “包住 K 线”只说明边界没有画穿，并不代表两条线共同定义了一段真实盘整。
      // 这里继续检查通道内部是否真的有成交路径：有效结构会在上下轨之间多次
      // 往返；若价格长期贴住单边、另一侧留下大片空白，则这对趋势线作废。
      const channelHeight = upperLine - lowerLine;
      if (!Number.isFinite(channelHeight) || channelHeight <= bodyTolerance) {
        currentHollowRun = 0;
        return;
      }
      validChannelRows += 1;
      const normalizedWickLow = clamp((row.low - lowerLine) / channelHeight, 0, 1);
      const normalizedWickHigh = clamp((row.high - lowerLine) / channelHeight, 0, 1);
      const normalizedBodyLow = clamp((bodyLow - lowerLine) / channelHeight, 0, 1);
      const normalizedBodyHigh = clamp((bodyHigh - lowerLine) / channelHeight, 0, 1);
      const normalizedMidpoint = (normalizedBodyLow + normalizedBodyHigh) / 2;
      const segmentIndex = Math.min(
        segmentCount - 1,
        Math.floor(offset / Math.max(rows.length / segmentCount, 1)),
      );
      const bandStart = Math.min(verticalBandCount - 1, Math.max(0, Math.floor(normalizedWickLow * verticalBandCount)));
      const bandEnd = Math.min(verticalBandCount - 1, Math.floor(normalizedWickHigh * verticalBandCount));
      for (let band = bandStart; band <= bandEnd; band += 1) segmentBands[segmentIndex].add(band);

      const wideEnoughToJudge = channelHeight >= Math.max(atrValue * 2.2, bodyTolerance * 8);
      if (!wideEnoughToJudge) {
        currentHollowRun = 0;
        return;
      }
      wideChannelRows += 1;
      const visitsMiddle = normalizedWickHigh >= 0.34 && normalizedWickLow <= 0.66;
      middleParticipationRows += Number(visitsMiddle);
      const boundarySide = normalizedMidpoint <= 0.32
        ? "lower"
        : normalizedMidpoint >= 0.68
          ? "upper"
          : null;
      if (boundarySide && previousBoundarySide && boundarySide !== previousBoundarySide) {
        channelSideTransitions += 1;
      }
      if (boundarySide) previousBoundarySide = boundarySide;

      const hollowSide = normalizedWickHigh <= 0.44
        ? "lower"
        : normalizedWickLow >= 0.56
          ? "upper"
          : null;
      if (hollowSide) {
        hollowRows += 1;
        currentHollowRun = hollowSide === previousHollowSide
          ? currentHollowRun + 1
          : 1;
        previousHollowSide = hollowSide;
        longestHollowRun = Math.max(longestHollowRun, currentHollowRun);
      } else {
        currentHollowRun = 0;
        previousHollowSide = null;
      }
    });
    const count = Math.max(rows.length, 1);
    const bodyCoverage = bodyInside / count;
    const wickCoverage = wickInside / count;
    const crossingRatio = crossingBars / count;
    const middleParticipationRatio = middleParticipationRows / Math.max(wideChannelRows, 1);
    const hollowRatio = hollowRows / Math.max(wideChannelRows, 1);
    const segmentOccupancy = mean(segmentBands.map((bands) => bands.size / verticalBandCount));
    const transitionQuality = clamp(channelSideTransitions / 6, 0, 1);
    const interiorOccupancy = clamp(
      segmentOccupancy * 0.58
      + middleParticipationRatio * 0.24
      + transitionQuality * 0.18,
      0,
      1,
    );
    const minimumWideRows = Math.max(16, Math.round(rows.length * 0.38));
    const minimumHollowRun = Math.max(12, Math.round(wideChannelRows * 0.26));
    const hollowChannel = rows.length >= 24
      && wideChannelRows >= minimumWideRows
      && hollowRatio >= 0.42
      && longestHollowRun >= minimumHollowRun
      && middleParticipationRatio < 0.3
      && channelSideTransitions <= 2
      && interiorOccupancy < 0.5;
    const boundaryScore = clamp(
      (upper.envelopeScore + lower.envelopeScore) * 0.3
      + bodyCoverage * 0.25
      + wickCoverage * 0.1
      + (1 - crossingRatio) * 0.05,
      0,
      1,
    );
    const envelopeScore = clamp(boundaryScore * 0.84 + interiorOccupancy * 0.16, 0, 1);
    return {
      acceptable: upper.acceptable
        && lower.acceptable
        && bodyCoverage >= 0.92
        && wickCoverage >= 0.84
        && crossingRatio <= 0.08
        && !hollowChannel,
      upper,
      lower,
      bodyCoverage,
      wickCoverage,
      crossingRatio,
      crossingBars,
      validChannelRows,
      wideChannelRows,
      interiorOccupancy,
      segmentOccupancy,
      middleParticipationRatio,
      hollowRatio,
      longestHollowRun,
      channelSideTransitions,
      hollowChannel,
      envelopeScore,
    };
  }

  function consolidationProfile(candles, index, atrValue, minimumBars = 12, maximumBars = 96) {
    const profiles = [];
    const available = Math.min(maximumBars, index);
    for (let length = minimumBars; length <= available; length += length < 48 ? 1 : length < 96 ? 4 : 8) {
      const window = candles.slice(index - length, index);
      const ceiling = Math.max(...window.map((row) => row.high));
      const floor = Math.min(...window.map((row) => row.low));
      const midpoint = mean(window.map((row) => row.close));
      const width = ceiling - floor;
      const drift = Math.abs(window.at(-1).close - window[0].close);
      const widthLimit = Math.max(
        midpoint * 0.015,
        Math.min(midpoint * 0.12, atrValue * 9.5),
      );
      const driftLimit = Math.max(
        midpoint * 0.006,
        Math.min(midpoint * 0.04, atrValue * 4.2),
      );
      const closeSlope = Math.abs(regressionSlope(window.map((row) => row.close)));
      // 横盘必须是真正停止推进的母结构。持续斜向爬升即使落在一个宽窗口内，
      // 也不能被滚动识别成新箱体，否则主升途中会在每个附近阳线反复开仓。
      if (width > widthLimit || drift > driftLimit || closeSlope > atrValue * 0.09) continue;
      const split = Math.floor(window.length / 2);
      const early = window.slice(0, split);
      const late = window.slice(split);
      const earlyWidth = Math.max(...early.map((row) => row.high)) - Math.min(...early.map((row) => row.low));
      const lateWidth = Math.max(...late.map((row) => row.high)) - Math.min(...late.map((row) => row.low));
      const pressure = window.slice(-8).filter((row) => row.close >= ceiling - atrValue * 1.25).length;
      let lastCeilingIndex = -1;
      let ceilingTouches = 0;
      window.forEach((row, cursor) => {
        if (row.high >= ceiling - atrValue * 0.18) {
          lastCeilingIndex = cursor;
          ceilingTouches += 1;
        }
      });
      const ceilingAge = window.length - 1 - lastCeilingIndex;
      const duration = clamp(length / 40, 0, 1);
      const compression = clamp(1 - lateWidth / Math.max(earlyWidth * 1.15, 1e-8), 0, 1);
      const tightness = clamp(1 - width / Math.max(widthLimit, 1e-8), 0, 1);
      const quality = duration * 0.28 + compression * 0.28 + tightness * 0.24 + clamp(pressure / 5, 0, 1) * 0.2;
      profiles.push({ bars: length, ceiling, floor, midpoint, width, earlyWidth, lateWidth, pressure, quality, ceilingAge, ceilingTouches });
    }
    return profiles.sort((a, b) => (
      Number(b.ceilingAge >= 3 || b.ceilingTouches >= 3) - Number(a.ceilingAge >= 3 || a.ceilingTouches >= 3)
      || b.quality - a.quality
      || b.bars - a.bars
    ))[0]
      || { bars: 0, ceiling: 0, floor: 0, midpoint: 0, width: 0, earlyWidth: 0, lateWidth: 0, pressure: 0, quality: 0, ceilingAge: 0, ceilingTouches: 0 };
  }

  // “横盘”必须先真正停止推进。仅仅在一个较大的滚动窗口里出现几根阴线，
  // 不能把仍在快速抬高低点的阶梯式上涨解释成换手平台。这里专门观察触发前
  // 最后 12~18 根已经收盘的 K 线；既不读取突破 K，也不使用突破后的走势。
  function assessHorizontalBaseUrgency(rows, atrValue) {
    const source = Array.isArray(rows) ? rows.filter(Boolean) : [];
    const tail = source.slice(-Math.min(18, source.length));
    const safeAtr = Math.max(Number(atrValue) || 0, 1e-8);
    if (tail.length < 12) {
      return {
        urgent: false,
        bars: tail.length,
        efficiency: 0,
        netAdvanceAtr: 0,
        closeSlopeAtrPerBar: 0,
        lowSlopeAtrPerBar: 0,
        nonFallingLowGroupRatio: 0,
        risingLowGroupRatio: 0,
        maxPullbackAtr: 0,
        evidence: [],
      };
    }
    const closes = tail.map((row) => row.close);
    const lows = tail.map((row) => row.low);
    const travelled = closes.slice(1).reduce((sum, close, cursor) => (
      sum + Math.abs(close - closes[cursor])
    ), 0);
    const efficiency = travelled
      ? Math.abs(closes.at(-1) - closes[0]) / travelled
      : 0;
    const netAdvanceAtr = (closes.at(-1) - closes[0]) / safeAtr;
    const closeSlopeAtrPerBar = regressionSlope(closes) / safeAtr;
    const lowSlopeAtrPerBar = regressionSlope(lows) / safeAtr;
    const groupedLows = [];
    for (let cursor = 0; cursor + 2 < tail.length; cursor += 3) {
      groupedLows.push(Math.min(...tail.slice(cursor, cursor + 3).map((row) => row.low)));
    }
    const groupedAdvances = groupedLows.slice(1).map((low, cursor) => (
      (low - groupedLows[cursor]) / safeAtr
    ));
    const nonFallingLowGroupRatio = groupedAdvances.length
      ? groupedAdvances.filter((advance) => advance >= -0.1).length / groupedAdvances.length
      : 0;
    const risingLowGroupRatio = groupedAdvances.length
      ? groupedAdvances.filter((advance) => advance >= 0.05).length / groupedAdvances.length
      : 0;
    let runningPeak = closes[0];
    let maxPullback = 0;
    closes.forEach((close) => {
      runningPeak = Math.max(runningPeak, close);
      maxPullback = Math.max(maxPullback, runningPeak - close);
    });
    const maxPullbackAtr = maxPullback / safeAtr;
    const urgent = efficiency >= 0.34
      && netAdvanceAtr >= 2
      && closeSlopeAtrPerBar >= 0.1
      && lowSlopeAtrPerBar >= 0.1
      && nonFallingLowGroupRatio >= 0.75
      && risingLowGroupRatio >= 0.6
      && maxPullbackAtr <= 1.05;
    return {
      urgent,
      bars: tail.length,
      efficiency,
      netAdvanceAtr,
      closeSlopeAtrPerBar,
      lowSlopeAtrPerBar,
      nonFallingLowGroupRatio,
      risingLowGroupRatio,
      maxPullbackAtr,
      evidence: urgent ? [
        `末段 ${tail.length} 根净推进 ${netAdvanceAtr.toFixed(2)} ATR，收盘/低点斜率 ${closeSlopeAtrPerBar.toFixed(2)}/${lowSlopeAtrPerBar.toFixed(2)} ATR/根`,
        `分组低点 ${(nonFallingLowGroupRatio * 100).toFixed(0)}% 持续抬高，最大回撤仅 ${maxPullbackAtr.toFixed(2)} ATR，仍属急促斜拉而非充分换手`,
      ] : [],
    };
  }

  function assessHorizontalBaseDwell(baseComponent) {
    const outerPlatform = baseComponent?.platformModel === "outer";
    const touchGroups = Number(baseComponent?.touchGroups) || 0;
    const ceilingTouches = Number(baseComponent?.ceilingTouches) || 0;
    const ceilingAge = Number(baseComponent?.ceilingAge) || 0;
    const insufficient = outerPlatform
      && touchGroups < 2
      && ceilingTouches < 3
      && ceilingAge < 6;
    return {
      insufficient,
      touchGroups,
      ceilingTouches,
      ceilingAge,
      evidence: insufficient ? [
        `母平台末端仅 ${touchGroups} 组 / ${ceilingTouches} 次外沿触碰，距突破只有 ${ceilingAge} 根，尚未形成真正横向停留`,
        "不能用更早的上涨路径扩充平台长度；突破前最后一段必须独立完成横向换手",
      ] : [],
    };
  }

  function assessOuterPlatformContinuity(window, ceiling, touchIndexes, touchGroups, atrValue) {
    const rows = Array.isArray(window) ? window : [];
    const touches = Array.isArray(touchIndexes) ? touchIndexes : [];
    if (rows.length < 2 || touches.length < 2) {
      return {
        acceptable: true,
        phaseBroken: false,
        betweenTouchBars: 0,
        longestDeepDepartureRun: 0,
        maximumDepartureAtr: 0,
        maximumDeparturePercent: 0,
        evidence: [],
      };
    }
    const firstTouch = touches[0];
    const lastTouch = touches.at(-1);
    const between = rows.slice(firstTouch, lastTouch + 1);
    const safeCeiling = Math.max(Number(ceiling) || 0, 1e-8);
    const safeAtr = Math.max(Number(atrValue) || 0, 1e-8);
    const deepDeparture = Math.max(safeAtr * 4, safeCeiling * 0.05);
    let currentDeepRun = 0;
    let longestDeepDepartureRun = 0;
    let maximumDeparture = 0;
    between.forEach((row) => {
      const departure = Math.max(0, safeCeiling - row.close);
      maximumDeparture = Math.max(maximumDeparture, departure);
      if (departure > deepDeparture) {
        currentDeepRun += 1;
        longestDeepDepartureRun = Math.max(longestDeepDepartureRun, currentDeepRun);
      } else {
        currentDeepRun = 0;
      }
    });
    const maximumDepartureAtr = maximumDeparture / safeAtr;
    const maximumDeparturePercent = maximumDeparture / safeCeiling * 100;
    const deepRunLimit = Math.max(18, Math.ceil(between.length * 0.18));
    const phaseBroken = Number(touchGroups) <= 2
      && between.length >= 48
      && maximumDepartureAtr >= 8
      && maximumDeparturePercent >= 8
      && longestDeepDepartureRun >= deepRunLimit;
    return {
      acceptable: !phaseBroken,
      phaseBroken,
      betweenTouchBars: between.length,
      longestDeepDepartureRun,
      maximumDepartureAtr,
      maximumDeparturePercent,
      evidence: phaseBroken ? [
        `两组外沿触碰之间相隔 ${between.length} 根，中途最大深跌 ${maximumDepartureAtr.toFixed(2)} ATR / ${maximumDeparturePercent.toFixed(1)}%`,
        `连续 ${longestDeepDepartureRun} 根远离外沿，已经经历独立深跌与修复，不能视为同一横盘母平台`,
      ] : [],
    };
  }

  function detectHorizontalBase(candles, index, atrValue, minimumBars = 18) {
    const profile = consolidationProfile(candles, index, atrValue, minimumBars, 96);
    if (profile.bars < minimumBars || (profile.ceilingAge < 3 && profile.ceilingTouches < 3)) return null;
    const horizontalUrgency = assessHorizontalBaseUrgency(
      candles.slice(index - profile.bars, index),
      atrValue,
    );
    return {
      type: "base",
      structureStartIndex: index - profile.bars,
      structureEndIndex: index - 1,
      level: profile.ceiling,
      stop: profile.floor,
      consolidationBars: profile.bars,
      pressureBars: profile.pressure,
      rangeCompression: profile.lateWidth / Math.max(profile.earlyWidth, 1e-8),
      quality: profile.quality,
      outerEdgeScore: Math.round(clamp(profile.quality * 100 + Math.min(profile.bars, 48) * 0.35, 0, 99)),
      ceilingAge: profile.ceilingAge,
      ceilingTouches: profile.ceilingTouches,
      touchGroups: profile.ceilingTouches >= 3 ? 2 : 1,
      launchDistancePercent: Math.max(0, profile.ceiling / Math.max(candles[index].open, 1e-8) - 1) * 100,
      platformModel: "tight",
      horizontalUrgency,
      evidence: [
        `突破前已完成 ${profile.bars} 根 K 线箱体`,
        `箱体宽度 ${(profile.width / Math.max(profile.midpoint, 1e-8) * 100).toFixed(2)}%`,
        profile.lateWidth <= profile.earlyWidth ? "末端振幅继续收紧" : "末端振幅保持稳定",
      ],
    };
  }

  // “盘整前高”与附近阳线高点是两种完全不同的东西。严格横盘模型会漏掉
  // 主升途中略宽、略抬高的平台，因此单独扫描已经形成的外沿；只要当前 K 线
  // 从线下首次穿越，触发价就固定在整个平台的最高点，而不是最后几根局部高点。
  // 本函数只读取 index 以前已经收盘的 K 线；当前 K 线仅用于判断预设 stop 是否成交。
  function detectOuterPlatform(candles, index, atrValue, minimumBars = 18, maximumBars = 192, options = {}) {
    if (index < minimumBars) return null;
    const current = candles[index];
    const profiles = [];
    const available = Math.min(maximumBars, index);
    const countTouchGroups = (indexes) => {
      if (!indexes.length) return 0;
      let groups = 1;
      for (let cursor = 1; cursor < indexes.length; cursor += 1) {
        if (indexes[cursor] - indexes[cursor - 1] >= 3) groups += 1;
      }
      return groups;
    };
    for (let scannedLength = minimumBars; scannedLength <= available; scannedLength += scannedLength < 48 ? 1 : scannedLength < 96 ? 4 : 8) {
      const scannedWindow = candles.slice(index - scannedLength, index);
      const scannedCeiling = Math.max(...scannedWindow.map((row) => row.high));
      const exactCeilingTolerance = Math.max(atrValue * 0.28, scannedCeiling * 0.0025);
      const exactTouchIndexes = [];
      scannedWindow.forEach((row, cursor) => {
        if (row.high >= scannedCeiling - exactCeilingTolerance) exactTouchIndexes.push(cursor);
      });
      // 成熟平台的压力通常是一条反复交易的“带”，不一定每次上影线都精确
      // 触到唯一最高点。允许长平台用较宽的 ATR 带证明成熟度，但真实触发价
      // 仍固定在历史最高点；这样不会把附近阳线降格成伪前高，也不会漏掉
      // TURBO 2024-05-23 23:15 这类被单根略高上影线扭曲的盘整突破。
      const repeatedBandTolerance = Math.max(atrValue * 1.05, scannedCeiling * 0.0025);
      const repeatedBandTouchIndexes = [];
      scannedWindow.forEach((row, cursor) => {
        if (row.high >= scannedCeiling - repeatedBandTolerance) repeatedBandTouchIndexes.push(cursor);
      });
      const repeatedBandGroups = countTouchGroups(repeatedBandTouchIndexes);
      const repeatedBandSpan = repeatedBandTouchIndexes.length
        ? repeatedBandTouchIndexes.at(-1) - repeatedBandTouchIndexes[0]
        : 0;
      const matureClusteredCeilingBand = scannedLength >= 40
        && exactTouchIndexes.length < 3
        && repeatedBandTouchIndexes.length >= 5
        && repeatedBandGroups >= 3
        && repeatedBandSpan >= Math.max(12, Math.ceil(scannedLength * 0.28));
      // 1小时平台的 K 线天然更稀疏。像 TURBO 2024-05-24 这类拉升后
      // 17 根左右都在同一高度带换手的结构，不能因为单根上影线略高，
      // 就只从末端最近的精确触点重新计数。允许“紧凑父平台”使用相同的
      // 压力带向左合并，但仍要求至少三组触碰、足够跨度和真实外沿突破。
      const compactParentCeilingBand = options.compactOneHour === true
        && scannedLength >= 12
        && repeatedBandTouchIndexes.length >= 7
        && repeatedBandGroups >= 2
        && repeatedBandSpan >= Math.max(7, Math.ceil(scannedLength * 0.42));
      const clusteredCeilingBand = matureClusteredCeilingBand || compactParentCeilingBand;
      const ceilingTolerance = clusteredCeilingBand ? repeatedBandTolerance : exactCeilingTolerance;
      let scannedTouchIndexes = clusteredCeilingBand ? repeatedBandTouchIndexes : exactTouchIndexes;
      if (!scannedTouchIndexes.length) continue;

      if (compactParentCeilingBand) {
        const settledTouches = scannedTouchIndexes.filter((cursor) => {
          const row = scannedWindow[cursor];
          return row.high - row.low <= atrValue * 2.2;
        });
        // 排除拉升段最后一根大阳线，把平台起点放在拉升后第一次真正进入
        // 同高度区间并开始换手的 K 线；若没有可辨认的稳定触点则不放宽。
        if (!settledTouches.length) continue;
        scannedTouchIndexes = scannedTouchIndexes.filter((cursor) => cursor >= settledTouches[0]);
      }

      // 滚动扫描窗口不是母平台本身。真正的平台只能从其外沿第一次形成时开始：
      // 此前的下跌、修复或更早上涨路径仅用于前置环境判断，不能被借来凑平台长度。
      // 这样 HYPE 这类 192 根长回看不会把下跌中途的一根普通 K 线当成起点。
      const phaseStartOffset = scannedTouchIndexes[0];
      const window = scannedWindow.slice(phaseStartOffset);
      const length = window.length;
      if (length < minimumBars) continue;
      const ceiling = Math.max(...window.map((row) => row.high));
      const floor = Math.min(...window.map((row) => row.low));
      const trigger = ceiling + atrValue * 0.04;
      if (current.high < trigger || current.open >= trigger) continue;

      const touchIndexes = [];
      window.forEach((row, cursor) => {
        if (row.high >= ceiling - ceilingTolerance) touchIndexes.push(cursor);
      });
      if (!touchIndexes.length) continue;
      const touchGroups = countTouchGroups(touchIndexes);
      const ceilingAge = length - 1 - touchIndexes.at(-1);
      const firstCeilingAge = length - 1 - touchIndexes[0];
      const establishedOuterEdge = ceilingAge >= 3 || (touchGroups >= 2 && firstCeilingAge >= 6);
      if (!establishedOuterEdge) continue;
      const platformContinuity = assessOuterPlatformContinuity(
        window,
        ceiling,
        touchIndexes,
        touchGroups,
        atrValue,
      );
      if (!platformContinuity.acceptable) continue;

      const launchDistancePercent = Math.max(0, ceiling / Math.max(current.open, 1e-8) - 1) * 100;
      if (launchDistancePercent > 7) continue;
      const tail = window.slice(-Math.min(18, length));
      const fullCloses = window.map((row) => row.close);
      const fullTravelled = fullCloses.slice(1).reduce((sum, close, cursor) => (
        sum + Math.abs(close - fullCloses[cursor])
      ), 0);
      const fullEfficiency = fullTravelled
        ? Math.abs(fullCloses.at(-1) - fullCloses[0]) / fullTravelled
        : 0;
      const platformRangePercent = (ceiling - floor) / Math.max(ceiling, 1e-8) * 100;
      const tailCloses = tail.map((row) => row.close);
      const travelled = tailCloses.slice(1).reduce((sum, close, cursor) => (
        sum + Math.abs(close - tailCloses[cursor])
      ), 0);
      const efficiency = travelled
        ? Math.abs(tailCloses.at(-1) - tailCloses[0]) / travelled
        : 0;
      const directionChanges = tailCloses.slice(2).reduce((count, close, cursor) => {
        const priorMove = tailCloses[cursor + 1] - tailCloses[cursor];
        const currentMove = close - tailCloses[cursor + 1];
        return count + Number(priorMove * currentMove < 0);
      }, 0);
      const counterBars = tail.filter((row) => row.close <= row.open).length;
      const pressureDistance = Math.max(atrValue * 2.2, ceiling * 0.012);
      const pressureWindow = window.slice(-Math.min(10, length));
      const pressureBars = pressureWindow.filter((row) => row.close >= ceiling - pressureDistance).length;
      const platformPressureBars = window.filter((row) => row.close >= ceiling - pressureDistance).length;
      const requiredPlatformPressure = Math.max(
        5,
        Math.ceil(length * (touchGroups >= 2 ? 0.22 : 0.35)),
      );
      const belowCeilingRatio = window.filter((row) => row.close <= ceiling + ceilingTolerance * 0.35).length / length;
      const tailHigh = Math.max(...tail.map((row) => row.high));
      const tailLow = Math.min(...tail.map((row) => row.low));
      const tailRangePercent = (tailHigh - tailLow) / Math.max(ceiling, 1e-8) * 100;
      const hasPauseRhythm = directionChanges >= 3
        || counterBars >= 3
        || efficiency <= 0.58;
      if (!hasPauseRhythm
        || pressureBars < 3
        || platformPressureBars < requiredPlatformPressure
        || belowCeilingRatio < 0.82
        || tailRangePercent > 18) continue;
      if (options.strictHorizontal === true
        && (fullEfficiency > 0.3
          || platformRangePercent > 6
          || platformPressureBars / length < 0.34)) continue;

      const durationQuality = clamp(length / 40, 0, 1);
      const touchQuality = clamp((touchGroups - 1) / 2 + Math.min(ceilingAge, 8) / 16, 0, 1);
      const pressureQuality = clamp(pressureBars / Math.min(7, pressureWindow.length), 0, 1);
      const pauseQuality = clamp(1 - efficiency / 0.78, 0, 1);
      const distanceQuality = clamp(1 - launchDistancePercent / 7, 0, 1);
      const quality = durationQuality * 0.25
        + touchQuality * 0.22
        + pressureQuality * 0.22
        + pauseQuality * 0.18
        + distanceQuality * 0.13;
      // 28～39 根已经足以形成一段可独立辨认的平台。过去只给
      // 40 根以上时间加分，会把 H 5m 这类 30 根、多次触及外沿的
      // 真平台压到 80 分以下。奖励从 28 根开始，不改变 18 根成熟底线。
      const outerEdgeScore = Math.round(clamp(
        quality * 100
        + Number(length >= 28) * 4
        + Number(compactParentCeilingBand) * 4,
        0,
        99,
      ));
      if (outerEdgeScore < 62) continue;
      if (options.strictHorizontal === true && outerEdgeScore < 84) continue;
      const horizontalUrgency = assessHorizontalBaseUrgency(window, atrValue);
      profiles.push({
        type: "base",
        structureStartIndex: index - length,
        structureEndIndex: index - 1,
        level: ceiling,
        stop: Math.max(floor, Math.min(...tail.map((row) => row.low))),
        consolidationBars: length,
        pressureBars,
        rangeCompression: 1,
        quality,
        outerEdgeConfirmed: true,
        outerEdgeScore,
        ceilingAge,
        ceilingTouches: touchIndexes.length,
        touchGroups,
        platformEfficiency: efficiency,
        fullPlatformEfficiency: fullEfficiency,
        platformRangePercent,
        platformPressureRatio: platformPressureBars / length,
        launchDistancePercent,
        platformModel: "outer",
        clusteredCeilingBand,
        ceilingBandToleranceAtr: ceilingTolerance / Math.max(atrValue, 1e-8),
        scannedBars: scannedLength,
        discardedLeadInBars: phaseStartOffset,
        horizontalUrgency,
        platformContinuity,
        evidence: [
          `盘整母平台外沿已提前形成 ${length} 根，前高 ${ceiling.toFixed(8)}`,
          ...(clusteredCeilingBand ? [
            `单根上影线略高于反复交易压力带；用 ${touchIndexes.length} 次 / ${touchGroups} 组压力带触碰判断成熟度，执行价仍采用真实最高点`,
          ] : []),
          ...(compactParentCeilingBand ? [
            "1小时紧凑父平台按同高度压力带向左合并，并剔除平台前最后一根拉升K线",
          ] : []),
          ...(phaseStartOffset > 0 ? [
            `已剔除外沿形成前 ${phaseStartOffset} 根下跌/推进路径；母平台从首次有效触碰前高的 K 线开始`,
          ] : []),
          `外沿触碰 ${touchIndexes.length} 次 / ${touchGroups} 组，最后一次距触发 ${ceilingAge} 根`,
          horizontalUrgency.urgent
            ? `末段推进效率 ${(efficiency * 100).toFixed(0)}%，低点仍在急促连续抬高`
            : `末段推进效率 ${(efficiency * 100).toFixed(0)}%，确认是盘整而非连续斜拉`,
          ...horizontalUrgency.evidence,
          ...platformContinuity.evidence,
          `突破K开盘到盘整前高 ${launchDistancePercent.toFixed(2)}%，未超过 7%`,
        ],
      });
    }
    return profiles.sort((a, b) => (
      b.level - a.level
      || b.outerEdgeScore - a.outerEdgeScore
      || b.consolidationBars - a.consolidationBars
    ))[0] || null;
  }

  // 滚动平台经常会从“真正区间高点之后的第一根稳定 K 线”开始，进而把
  // 母平台最重要的首个峰值切掉。若平台起点前不远处已经有一个更高、清晰的
  // 结构峰值，那么它仍是这段盘整必须突破的真实上沿。当前 K 线没有越过它时，
  // 局部箱体、附近阳线和内切三角只能算母结构内部波动；越过后则把执行价提升
  // 到该峰值。所有判断只读取 index 以前的 K 线与当前触发 K，不含未来数据。
  function resolveTruePriorHighBoundary(candles, index, baseComponent, atrValue) {
    if (!baseComponent || !Number.isFinite(baseComponent.structureStartIndex)) {
      return { component: baseComponent, context: null };
    }
    const structureStartIndex = clamp(
      Math.trunc(baseComponent.structureStartIndex),
      1,
      Math.max(1, index - 1),
    );
    const consolidationBars = Math.max(1, index - structureStartIndex);
    const maximumLeadBars = Math.max(12, Math.min(36, Math.round(consolidationBars * 0.55)));
    const leadStartIndex = Math.max(0, structureStartIndex - maximumLeadBars);
    if (structureStartIndex - leadStartIndex < 4) return { component: baseComponent, context: null };

    let peakIndex = leadStartIndex;
    for (let cursor = leadStartIndex + 1; cursor < structureStartIndex; cursor += 1) {
      if (candles[cursor].high > candles[peakIndex].high) peakIndex = cursor;
    }
    const peak = candles[peakIndex].high;
    const prominence = peak - baseComponent.level;
    const barsBeforePlatform = structureStartIndex - peakIndex;
    const maximumCausalGap = Math.max(6, Math.min(14, Math.round(consolidationBars * 0.28)));
    const prominent = prominence >= Math.max(atrValue * 0.18, baseComponent.level * 0.0012);
    if (!prominent || barsBeforePlatform > maximumCausalGap) {
      return { component: baseComponent, context: null };
    }

    // 一根略高的旧上影线不能机械覆盖随后被反复交易、已经成熟的真实压力带。
    // 当当前平台在同一高度拥有足够多的触碰与分组，而且旧峰只比压力带高出
    // 约 1.5 ATR 以内时，优先使用市场实际反复成交的外沿；否则 H 一小时这类
    // 长平台会被孤立插针抬高触发线，漏掉平台末端真正的突破 K。
    const repeatedPressureBand = baseComponent.clusteredCeilingBand === true
      && (baseComponent.ceilingTouches || 0) >= 8
      && (baseComponent.touchGroups || 0) >= 4
      && prominence <= atrValue * 1.5;
    if (repeatedPressureBand) {
      return {
        component: baseComponent,
        context: {
          level: baseComponent.level,
          ignoredPeak: peak,
          peakIndex,
          barsBeforePlatform,
          prominenceAtr: prominence / Math.max(atrValue, 1e-8),
          ignoredIsolatedPeak: true,
          blocked: false,
          evidence: [
            `局部旧峰仅比成熟压力带高 ${Math.max(0, prominence / Math.max(atrValue, 1e-8)).toFixed(2)} ATR`,
            `当前外沿已有 ${baseComponent.ceilingTouches || 0} 次 / ${baseComponent.touchGroups || 0} 组反复交易，保留真实压力带而不被孤立上影线抬高`,
          ],
        },
      };
    }

    const triggerPrice = peak + atrValue * 0.04;
    const current = candles[index];
    const crossed = current.high >= triggerPrice && current.open < triggerPrice;
    const context = {
      level: peak,
      triggerPrice,
      peakIndex,
      barsBeforePlatform,
      prominenceAtr: prominence / Math.max(atrValue, 1e-8),
      blocked: !crossed,
      evidence: [
        `局部平台起点前 ${barsBeforePlatform} 根已有更高结构峰值 ${peak.toFixed(8)}`,
        crossed
          ? "当前 K 线同时越过该母结构真实前高，执行边界提升到完整盘整上沿"
          : "当前只突破局部上沿，尚未越过完整盘整的真实前高",
      ],
    };
    if (!crossed) return { component: null, context };
    const floor = Math.min(
      baseComponent.stop,
      ...candles.slice(peakIndex + 1, index).map((row) => row.low),
    );
    return {
      context,
      component: {
        ...baseComponent,
        structureStartIndex: peakIndex,
        level: peak,
        stop: floor,
        consolidationBars: index - peakIndex,
        launchDistancePercent: Math.max(0, peak / Math.max(current.open, 1e-8) - 1) * 100,
        truePriorHighPromoted: true,
        truePriorHighIndex: peakIndex,
        truePriorHighProminenceAtr: context.prominenceAtr,
        evidence: [...(baseComponent.evidence || []), ...context.evidence],
      },
    };
  }

  // 这里不返回可交易平台，只记录一种反证：两组距离很远的旧高点
  // 虽然价格接近，但中间经历了整段深跌与修复，绝大多数 K 线并未
  // 沿外沿交易。它不能被当成母平台，也不能为末端小回踩提供假共振。
  function detectBrokenOuterPlatformContext(candles, index, atrValue, minimumBars = 18, maximumBars = 192) {
    if (index < minimumBars) return null;
    const current = candles[index];
    const available = Math.min(maximumBars, index);
    const matches = [];
    for (let length = minimumBars; length <= available; length += length < 48 ? 1 : length < 96 ? 4 : 8) {
      const window = candles.slice(index - length, index);
      const ceiling = Math.max(...window.map((row) => row.high));
      const tolerance = Math.max(atrValue * 0.28, ceiling * 0.0025);
      const trigger = ceiling + atrValue * 0.04;
      if (current.high < trigger || current.open >= trigger) continue;
      const touchIndexes = [];
      window.forEach((row, cursor) => {
        if (row.high >= ceiling - tolerance) touchIndexes.push(cursor);
      });
      if (touchIndexes.length < 2) continue;
      let touchGroups = 1;
      for (let cursor = 1; cursor < touchIndexes.length; cursor += 1) {
        if (touchIndexes[cursor] - touchIndexes[cursor - 1] >= 3) touchGroups += 1;
      }
      if (touchGroups < 2) continue;
      const continuity = assessOuterPlatformContinuity(
        window,
        ceiling,
        touchIndexes,
        touchGroups,
        atrValue,
      );
      if (!continuity.phaseBroken) continue;
      matches.push({
        level: ceiling,
        scannedBars: length,
        structureStartIndex: index - length + touchIndexes[0],
        touchGroups,
        ceilingTouches: touchIndexes.length,
        ...continuity,
      });
    }
    return matches.sort((a, b) => (
      b.level - a.level
      || b.longestDeepDepartureRun - a.longestDeepDepartureRun
      || b.scannedBars - a.scannedBars
    ))[0] || null;
  }

  function countBoundaryTouchGroups(rows, lineAt, side, tolerance, minimumGap = 3) {
    const indexes = [];
    rows.forEach((row, cursor) => {
      const price = side === "upper" ? row.high : row.low;
      if (Math.abs(price - lineAt(cursor)) <= tolerance) indexes.push(cursor);
    });
    let groups = indexes.length ? 1 : 0;
    for (let cursor = 1; cursor < indexes.length; cursor += 1) {
      if (indexes[cursor] - indexes[cursor - 1] >= minimumGap) groups += 1;
    }
    return { indexes, touches: indexes.length, groups };
  }

  // 上升通道与上升楔形的末端新高，视觉上很容易被滚动箱体误认为“前高突破”。
  // 这里拟合完整上下包络，并要求 K 线在两条边界之间真实来回轮动；普通单边上涨、
  // 空心通道或随手画出的两条斜线不会命中。函数只读取 index 以前已收盘的 K 线。
  function detectAscendingChannelTrap(
    candles,
    index,
    atrValue,
    minimumBars = 24,
    maximumBars = 120,
    options = {},
  ) {
    if (index < minimumBars) return null;
    const current = candles[index];
    const available = Math.min(maximumBars, index);
    const matches = [];
    for (let length = minimumBars; length <= available; length += length < 48 ? 2 : 4) {
      const startIndex = index - length;
      const window = candles.slice(startIndex, index);
      const highs = window.map((row) => row.high);
      const lows = window.map((row) => row.low);
      const highSlope = estimateBoundarySlope(highs, "upper");
      const lowSlope = estimateBoundarySlope(lows, "lower");
      const slopeFloor = atrValue * 0.035;
      if (highSlope <= slopeFloor || lowSlope <= slopeFloor) continue;

      const upperFit = fitQuantileEnvelope(highs, highSlope, "upper");
      const lowerFit = fitQuantileEnvelope(lows, lowSlope, "lower");
      const upperStart = upperFit.lineAt(0);
      const lowerStart = lowerFit.lineAt(0);
      const upperEnd = upperFit.lineAt(length - 1);
      const lowerEnd = lowerFit.lineAt(length - 1);
      const upperNow = upperFit.lineAt(length);
      const lowerNow = lowerFit.lineAt(length);
      const startWidth = upperStart - lowerStart;
      const endWidth = upperEnd - lowerEnd;
      const centerAdvance = ((upperEnd + lowerEnd) - (upperStart + lowerStart)) / 2;
      if (startWidth <= atrValue * 0.9 || endWidth <= atrValue * 0.7 || centerAdvance < atrValue * 2.5) continue;

      const widthRatio = endWidth / Math.max(startWidth, 1e-8);
      const slopeRatio = highSlope / Math.max(lowSlope, 1e-8);
      const parallelChannel = slopeRatio >= 0.6
        && slopeRatio <= 1.65
        && widthRatio >= 0.7
        && widthRatio <= 1.34;
      const risingWedge = lowSlope >= highSlope + atrValue * 0.012
        && widthRatio >= 0.32
        && widthRatio <= 0.83;
      if (!parallelChannel && !risingWedge) continue;

      const envelope = assessEnvelopeCoverage(
        window,
        0,
        window.length,
        upperFit.lineAt,
        lowerFit.lineAt,
        atrValue,
        "wick",
        "wick",
      );
      if (!envelope.acceptable
        || envelope.bodyCoverage < 0.92
        || envelope.wickCoverage < 0.84
        || envelope.interiorOccupancy < 0.48
        || envelope.middleParticipationRatio < 0.22
        || envelope.channelSideTransitions < 2) continue;

      const touchTolerance = Math.max(
        atrValue * 0.42,
        Math.min(atrValue * 0.78, mean([startWidth, endWidth]) * 0.11),
      );
      const upperTouches = countBoundaryTouchGroups(window, upperFit.lineAt, "upper", touchTolerance);
      const lowerTouches = countBoundaryTouchGroups(window, lowerFit.lineAt, "lower", touchTolerance);
      if (upperTouches.groups < 2 || lowerTouches.groups < 2 || upperTouches.touches + lowerTouches.touches < 5) continue;

      // 真正执行突破时要求当前 K 线触碰预先存在的上轨；右侧“结构观察”只负责判断
      // 这段盘整究竟是横盘，还是整体斜向上推进的通道/上楔，不能因为尚未碰上轨
      // 就把一个清晰上楔错误预确认为横盘起飞。
      if (options.requireUpperTest !== false
        && (current.open > upperNow + atrValue * 0.35 || current.high < upperNow - atrValue * 0.42)) continue;
      const shape = risingWedge ? "rising-wedge" : "rising-channel";
      const quality = clamp(
        envelope.envelopeScore * 0.38
        + envelope.interiorOccupancy * 0.2
        + clamp((upperTouches.groups + lowerTouches.groups) / 6, 0, 1) * 0.17
        + clamp(centerAdvance / Math.max(atrValue * 6, 1e-8), 0, 1) * 0.13
        + clamp(length / 64, 0, 1) * 0.12,
        0,
        1,
      );
      matches.push({
        shape,
        startIndex,
        endIndex: index - 1,
        bars: length,
        upperStart,
        upperEnd,
        upperAtTrigger: upperNow,
        lowerStart,
        lowerEnd,
        lowerAtTrigger: lowerNow,
        highSlope,
        lowSlope,
        startWidth,
        endWidth,
        widthRatio,
        centerAdvanceAtr: centerAdvance / atrValue,
        upperTouches: upperTouches.touches,
        upperTouchGroups: upperTouches.groups,
        lowerTouches: lowerTouches.touches,
        lowerTouchGroups: lowerTouches.groups,
        interiorOccupancy: envelope.interiorOccupancy,
        middleParticipationRatio: envelope.middleParticipationRatio,
        sideTransitions: envelope.channelSideTransitions,
        quality,
        evidence: [
          `${shape === "rising-wedge" ? "上升楔形" : "上升通道"}已由触发前 ${length} 根 K 线形成`,
          `上下轨触碰 ${upperTouches.touches}/${lowerTouches.touches} 次，内部换边 ${envelope.channelSideTransitions} 次`,
          `结构斜向推进 ${(centerAdvance / atrValue).toFixed(2)} ATR，末端不按横盘前高执行`,
        ],
      });
    }
    return matches.sort((a, b) => b.quality - a.quality || b.bars - a.bars)[0] || null;
  }

  // 横盘起飞必须是“先向上推一段，再停下来盘整”。若所谓平台直接包含一段
  // 快速大下杀，且直到突破前仍未收复下杀起点，它只是跌后修复，不是主升接力。
  function assessHorizontalLaunchContext(candles, index, baseComponent, atrValue) {
    if (!baseComponent) return null;
    const fallbackStart = index - Math.max(0, baseComponent.consolidationBars || 0);
    const structureStartIndex = clamp(
      Math.trunc(baseComponent.structureStartIndex ?? fallbackStart),
      0,
      Math.max(0, index - 1),
    );
    const priorStart = Math.max(0, structureStartIndex - 48);
    const priorRows = candles.slice(priorStart, structureStartIndex);
    const structureRows = candles.slice(structureStartIndex, index);
    const enoughPriorHistory = priorRows.length >= 8;
    const priorEarly = priorRows.slice(0, Math.min(6, priorRows.length));
    const priorLate = priorRows.slice(-Math.min(5, priorRows.length));
    const priorNetAdvance = mean(priorLate.map((row) => row.close)) - mean(priorEarly.map((row) => row.close));
    const priorSlope = regressionSlope(priorRows.map((row) => row.close));
    const priorLow = priorRows.length ? Math.min(...priorRows.map((row) => row.low)) : baseComponent.stop;
    const launchReferenceRows = structureRows.slice(0, Math.min(5, structureRows.length));
    const launchReference = mean(launchReferenceRows.map((row) => row.close));
    const riseIntoBase = launchReference - priorLow;
    const hasPriorAdvance = !enoughPriorHistory
      || ((priorNetAdvance >= atrValue * 0.45 || riseIntoBase >= atrValue * 0.75)
        && priorSlope >= -atrValue * 0.008);

    // “盘整突破”是母类，“横盘起飞”是其中更严格的子类。横盘起飞除了
    // 有前置拉升，还要求调整没有跌破该拉升段约一半；深回撤后的重新修复
    // 可以继续作为盘整突破或拐点，但不再错误贴上横盘起飞标签。
    const impulseStartIndex = Math.max(0, structureStartIndex - 48);
    const impulseEndIndex = Math.min(index, structureStartIndex + 4);
    const impulseRows = candles.slice(impulseStartIndex, impulseEndIndex);
    let impulsePeakOffset = 0;
    impulseRows.forEach((row, cursor) => {
      if (row.high > impulseRows[impulsePeakOffset].high) impulsePeakOffset = cursor;
    });
    const impulsePeakIndex = impulseStartIndex + impulsePeakOffset;
    const impulseOriginRows = candles.slice(impulseStartIndex, impulsePeakIndex + 1);
    const impulsePeak = candles[impulsePeakIndex]?.high || launchReference;
    const impulseOrigin = impulseOriginRows.length
      ? Math.min(...impulseOriginRows.map((row) => row.low))
      : priorLow;
    const pullbackRows = candles.slice(impulsePeakIndex + 1, index);
    const pullbackLow = pullbackRows.length
      ? Math.min(...pullbackRows.map((row) => row.low))
      : impulsePeak;
    const impulseAdvance = Math.max(0, impulsePeak - impulseOrigin);
    const retracementRatio = impulseAdvance > atrValue * 0.35
      ? Math.max(0, impulsePeak - pullbackLow) / impulseAdvance
      : 1;
    const retainedAboveHalf = retracementRatio <= 0.5;
    // 单根冲高偶尔会把“拉升顶点”抬得过高，机械按极值计算会把已经在高位
    // 重新完成较长换手的平台误判为深回撤。只有平台本身成熟、重新贴近真前高
    // 且末端不急促时，才允许用“高位重建”替代严格的 50% 极值规则。
    const rebuiltNearHighPlatform = (baseComponent.consolidationBars || 0) >= 28
      && (baseComponent.outerEdgeScore || 0) >= 80
      && baseComponent.horizontalUrgency?.urgent !== true
      // “高位重建”只是极端插针后的窄例外，不能把普通 60%~78% 深回撤
      && retracementRatio <= 0.78
      && Math.max(priorNetAdvance, riseIntoBase) >= atrValue * (
        (baseComponent.consolidationBars || 0) >= 56 ? 1 : 2.5
      );

    // 真实平台起点不再包含前置下杀，但语境审计仍需要向前看完整
    // 一段。只看起点前 8 根会看不到“急杀—修复—未收复”的因果链，
    // 进而把跌后修复平台错叫成主升横盘起飞。
    const riskRows = candles.slice(Math.max(0, structureStartIndex - 48), index);
    let peak = -Infinity;
    let peakIndex = -1;
    let worstDrop = 0;
    let worstDropPercent = 0;
    let dropBars = 0;
    let dropPeak = 0;
    riskRows.forEach((row, cursor) => {
      if (row.high > peak) {
        peak = row.high;
        peakIndex = cursor;
      }
      const drop = peak - row.low;
      if (drop > worstDrop && cursor > peakIndex) {
        worstDrop = drop;
        worstDropPercent = drop / Math.max(peak, 1e-8) * 100;
        dropBars = cursor - peakIndex;
        dropPeak = peak;
      }
    });
    const fastSelloff = dropBars >= 1
      && dropBars <= 14
      && worstDrop >= atrValue * 4.5
      && worstDropPercent >= 4;
    const unrecoveredDrop = dropPeak > 0
      && baseComponent.level <= dropPeak - Math.max(atrValue * 1.6, dropPeak * 0.018);
    const postSelloffRecovery = fastSelloff && unrecoveredDrop;
    return {
      structureStartIndex,
      enoughPriorHistory,
      hasPriorAdvance,
      horizontalLaunchQualified: hasPriorAdvance
        && (retainedAboveHalf || rebuiltNearHighPlatform)
        && !postSelloffRecovery,
      retainedAboveHalf,
      rebuiltNearHighPlatform,
      retracementRatio,
      impulsePeakIndex,
      impulseAdvanceAtr: impulseAdvance / atrValue,
      priorNetAdvanceAtr: priorNetAdvance / atrValue,
      riseIntoBaseAtr: riseIntoBase / atrValue,
      priorSlopeAtr: priorSlope / atrValue,
      postSelloffRecovery,
      selloffAtr: worstDrop / atrValue,
      selloffPercent: worstDropPercent,
      selloffBars: dropBars,
      unrecoveredDistanceAtr: dropPeak > 0 ? (dropPeak - baseComponent.level) / atrValue : 0,
      evidence: postSelloffRecovery
        ? [`平台前段出现 ${worstDropPercent.toFixed(2)}% / ${(worstDrop / atrValue).toFixed(2)} ATR 快速下杀，尚未收复下杀起点`]
        : hasPriorAdvance
          ? [
            `盘整前已有 ${(Math.max(priorNetAdvance, riseIntoBase) / atrValue).toFixed(2)} ATR 前置上推`,
            retainedAboveHalf
              ? `调整回撤前置拉升的 ${(retracementRatio * 100).toFixed(0)}%，保留横盘起飞子标签`
              : rebuiltNearHighPlatform
                ? `极值回撤达到 ${(retracementRatio * 100).toFixed(0)}%，但已在真前高附近重新完成成熟换手，按高位重建平台保留横盘起飞子标签`
                : `调整回撤前置拉升的 ${(retracementRatio * 100).toFixed(0)}%，只保留盘整突破/拐点，不标横盘起飞`,
          ]
          : ["盘整前缺少向上推动，不能定义为横盘起飞"],
    };
  }

  // 自动画线之前先回答一个更高层的问题：这是不是“拉升后的中继结构”。
  // 小周期和 1 小时若没有前置上推，哪怕局部几何能拟合成三角或楔形也不画；
  // 4 小时 / 日线另允许下跌后在低位长时间止跌压缩，新币不跌则必须由外部
  // 元数据或人工标签明确指定，绝不从一小段普通 K 线中猜测。
  function assessPreStructureContext(candles, index, structureStartIndex, atrValue, options = {}) {
    const startIndex = clamp(Math.trunc(structureStartIndex), 0, Math.max(0, index - 1));
    const interval = options.interval || "5m";
    if (options.newCoinNotFalling === true) {
      return {
        qualified: true,
        mode: "new-coin-not-falling",
        hasPriorAdvance: false,
        higherTimeframeBottomBase: false,
        evidence: ["新币不跌标签已明确确认，使用独立结构豁免，不强制要求上市前拉升段"],
      };
    }

    // 结构前置语境要看得比局部画线窗口更宽。只看 40～50 根很容易把
    // 大级别下跌中的一次小反弹误认成“拉升后的中继”；90 根用于判断
    // 当前结构究竟是独立上推后的整理，还是大下跌 / 大盘整内部的子结构。
    const priorStartIndex = Math.max(0, startIndex - 90);
    const priorRows = candles.slice(priorStartIndex, startIndex);
    const structureRows = candles.slice(startIndex, index);
    const enoughHistory = priorRows.length >= 8;
    const endpointRows = [
      ...priorRows.slice(-3),
      ...(candles[startIndex] ? [candles[startIndex]] : []),
    ];
    const endpoint = Math.max(
      mean(endpointRows.map((row) => row.close)),
      candles[startIndex] ? Math.max(candles[startIndex].open, candles[startIndex].close) : 0,
    );
    const lookbacks = [8, 12, 18, 24, 32, 40, 48, 56, 72, 90]
      .filter((length) => length <= priorRows.length);
    let bestAdvance = null;
    const advanceCandidates = [];
    lookbacks.forEach((length) => {
      const segment = priorRows.slice(-length).concat(candles[startIndex] ? [candles[startIndex]] : []);
      let pivotIndex = 0;
      for (let cursor = 1; cursor < segment.length - 3; cursor += 1) {
        if (segment[cursor].low < segment[pivotIndex].low) pivotIndex = cursor;
      }
      if (segment.length - pivotIndex < 5) return;
      const leg = segment.slice(pivotIndex);
      const closes = leg.map((row) => row.close);
      const origin = mean(closes.slice(0, Math.min(2, closes.length)));
      const advance = endpoint - Math.min(...leg.map((row) => row.low));
      const netAdvance = endpoint - origin;
      const travelled = closes.slice(1).reduce((sum, close, cursor) => (
        sum + Math.abs(close - closes[cursor])
      ), 0);
      const efficiency = netAdvance / Math.max(travelled, atrValue * 0.2);
      const positiveSteps = closes.slice(1).filter((close, cursor) => close > closes[cursor]).length
        / Math.max(closes.length - 1, 1);
      const slope = regressionSlope(closes);
      // 前置上推看实体是否把价格推到结构起点；单根上影线不应否定一段
      // 已经完成的真实拉升，否则 H 这类冲高后收敛会被误判为“未到峰值”。
      const peak = Math.max(...leg.map((row) => Math.max(row.open, row.close)));
      const nearPeak = endpoint >= peak - atrValue * 0.9;
      const qualified = advance >= atrValue * 0.75
        && netAdvance >= atrValue * 0.3
        && slope >= atrValue * 0.003
        && efficiency >= 0.08
        && positiveSteps >= 0.42
        && nearPeak;
      const score = clamp(advance / Math.max(atrValue * 5, 1e-8), 0, 1) * 0.34
        + clamp(netAdvance / Math.max(atrValue * 3, 1e-8), 0, 1) * 0.24
        + clamp(efficiency / 0.45, 0, 1) * 0.18
        + clamp(positiveSteps / 0.68, 0, 1) * 0.12
        + Number(nearPeak) * 0.12;
      const candidate = {
        qualified,
        pivotIndex: startIndex - (segment.length - 1) + pivotIndex,
        bars: leg.length,
        advanceAtr: advance / atrValue,
        netAdvanceAtr: netAdvance / atrValue,
        slopeAtr: slope / atrValue,
        efficiency,
        positiveSteps,
        nearPeak,
        score,
      };
      advanceCandidates.push(candidate);
      if (!bestAdvance || candidate.score > bestAdvance.score) bestAdvance = candidate;
    });
    // 执行许可仍由质量最高的上推段决定；视觉和因果说明则尽量追溯到同一
    // 推动波的更早起点。较长候选必须自身也通过上推审计、幅度不少于最佳段
    // 的 65%，且质量不能相差过远，才允许向左延伸。这样能覆盖 XRP 由低位
    // 连续推到首个结构高点的完整拉升，又不会把更早的大跌或无序母箱体算进来。
    const visualAdvanceCandidates = advanceCandidates.filter((candidate) => (
      candidate.qualified === true
      && candidate.score >= (bestAdvance?.score || 0) - 0.16
      && candidate.advanceAtr >= Math.max(0.75, (bestAdvance?.advanceAtr || 0) * 0.65)
      && candidate.netAdvanceAtr >= 0.3
      && candidate.efficiency >= 0.08
      && candidate.nearPeak === true
    ));
    const impulseContextStartIndex = visualAdvanceCandidates.length
      ? Math.min(...visualAdvanceCandidates.map((candidate) => candidate.pivotIndex))
      : bestAdvance?.pivotIndex ?? startIndex;

    const broadRows = priorRows.slice(-Math.min(72, priorRows.length));
    const broadHigh = broadRows.length ? Math.max(...broadRows.map((row) => row.high)) : endpoint;
    const broadBodyHigh = broadRows.length
      ? Math.max(...broadRows.map((row) => Math.max(row.open, row.close)))
      : endpoint;
    const broadLow = broadRows.length ? Math.min(...broadRows.map((row) => row.low)) : endpoint;
    const broadRange = broadHigh - broadLow;
    const broadCloses = broadRows.map((row) => row.close);
    const broadTravel = broadCloses.slice(1).reduce((sum, close, cursor) => (
      sum + Math.abs(close - broadCloses[cursor])
    ), 0);
    const broadEfficiency = broadCloses.length >= 2
      ? Math.abs(broadCloses.at(-1) - broadCloses[0]) / Math.max(broadTravel, atrValue * 0.2)
      : 0;
    const broadSlope = regressionSlope(broadCloses);

    // EMA90 只使用结构起点及以前的数据。它不负责产生买点，只用来识别
    // “局部反弹仍从属于下降趋势”的市场阶段。历史太短时不做这项否定。
    const closesThroughStart = candles.slice(0, startIndex + 1).map((row) => row.close);
    const ema90Series = ema(closesThroughStart, 90);
    const ema90AtStart = ema90Series[startIndex] ?? endpoint;
    const emaShortAnchor = Math.max(0, startIndex - 8);
    const emaMediumAnchor = Math.max(0, startIndex - 21);
    const ema90ShortSlopeAtr = startIndex >= 12
      ? (ema90AtStart - ema90Series[emaShortAnchor]) / Math.max(startIndex - emaShortAnchor, 1) / atrValue
      : 0;
    const ema90MediumSlopeAtr = startIndex >= 28
      ? (ema90AtStart - ema90Series[emaMediumAnchor]) / Math.max(startIndex - emaMediumAnchor, 1) / atrValue
      : 0;
    const ema90Falling = startIndex >= 28
      && ema90MediumSlopeAtr <= -0.004
      && ema90ShortSlopeAtr <= 0.001;

    const pivotIndex = bestAdvance?.pivotIndex ?? startIndex;
    const preImpulseRows = candles.slice(Math.max(0, pivotIndex - 48), pivotIndex);
    const preImpulseBodyHigh = preImpulseRows.length
      ? Math.max(...preImpulseRows.map((row) => Math.max(row.open, row.close)))
      : endpoint;
    const freshRangeExpansion = preImpulseRows.length < 6
      || endpoint >= preImpulseBodyHigh - atrValue * 0.65;
    const impulseDominance = (bestAdvance?.advanceAtr || 0) * atrValue
      / Math.max(broadRange, atrValue);
    const broadSlopeAtr = broadSlope / atrValue;
    const broadDowntrend = broadRows.length >= 20
      && (broadSlopeAtr <= -0.008 || ema90Falling)
      && endpoint < broadBodyHigh - atrValue * 0.55;
    const impulseRepairsTrend = Boolean(bestAdvance?.qualified)
      && freshRangeExpansion
      && endpoint >= ema90AtStart + atrValue * 0.25
      && impulseDominance >= 0.48
      && bestAdvance.efficiency >= 0.2;
    const downtrendRepairBounce = broadDowntrend && !impulseRepairsTrend;

    // 宽幅盘整中经常能截取出一个漂亮的小三角，但它没有独立行情阶段，
    // 只是母区间里的随机子波动。必须看到局部上推占据母区间的足够比例，
    // 或者已经把价格推回母区间上沿，才允许进入几何识别。
    const insideBroadConsolidation = broadRows.length >= 30
      && broadRange >= atrValue * 4.2
      && broadEfficiency < 0.18
      && impulseDominance < 0.82
      && !freshRangeExpansion
      && endpoint < broadBodyHigh - atrValue * 0.65;
    const unrecoveredPriorDecline = broadRows.length >= 12
      && broadRange >= atrValue * 4
      && broadSlope <= -atrValue * 0.012
      && endpoint <= broadHigh - Math.max(atrValue * 2, broadHigh * 0.025);
    const wideChopWithoutExpansion = broadRange >= atrValue * 4.8
      && broadEfficiency < 0.16
      && impulseDominance < 0.48
      && !freshRangeExpansion
      && endpoint < broadHigh - atrValue * 0.75;

    let runningPeak = -Infinity;
    let runningPeakIndex = -1;
    let selloff = 0;
    let selloffBars = 0;
    let selloffPeak = 0;
    priorRows.forEach((row, cursor) => {
      if (row.high > runningPeak) {
        runningPeak = row.high;
        runningPeakIndex = cursor;
      }
      const drop = runningPeak - row.low;
      if (cursor > runningPeakIndex && drop > selloff) {
        selloff = drop;
        selloffBars = cursor - runningPeakIndex;
        selloffPeak = runningPeak;
      }
    });
    const unrecoveredFastSelloff = selloffBars >= 1
      && selloffBars <= 14
      && selloff >= atrValue * 4.5
      && selloff / Math.max(selloffPeak, 1e-8) >= 0.04
      && endpoint <= selloffPeak - Math.max(atrValue * 1.6, selloffPeak * 0.018);
    const hasPriorAdvance = Boolean(bestAdvance?.qualified)
      && !unrecoveredFastSelloff
      && !unrecoveredPriorDecline
      && !wideChopWithoutExpansion
      && !insideBroadConsolidation
      && !downtrendRepairBounce;

    const higherTimeframe = interval === "4h" || interval === "1d";
    const minimumBottomBars = interval === "1d" ? 24 : 42;
    let higherTimeframeBottomBase = false;
    let bottomBaseDetail = null;
    if (higherTimeframe && structureRows.length >= minimumBottomBars && priorRows.length >= 12) {
      const third = Math.max(6, Math.floor(structureRows.length / 3));
      const early = structureRows.slice(0, third);
      const late = structureRows.slice(-third);
      const structureLow = Math.min(...structureRows.map((row) => row.low));
      const lateLow = Math.min(...late.map((row) => row.low));
      const earlyRange = mean(early.map((row) => row.high - row.low));
      const lateRange = mean(late.map((row) => row.high - row.low));
      const priorPeak = Math.max(...priorRows.map((row) => row.high));
      const decline = priorPeak - structureLow;
      const combinedLow = Math.min(broadLow, structureLow);
      const combinedHigh = Math.max(broadHigh, priorPeak);
      const lowZonePosition = (mean(late.map((row) => row.close)) - combinedLow)
        / Math.max(combinedHigh - combinedLow, atrValue);
      const lowIndex = structureRows.findIndex((row) => row.low === structureLow);
      const lowEstablishedBeforeTail = lowIndex
        <= structureRows.length - Math.max(8, Math.floor(structureRows.length * 0.18));
      const rangeContracted = lateRange <= earlyRange * 0.92 || lateRange <= atrValue * 1.05;
      const stoppedMakingLows = lateLow >= structureLow - atrValue * 0.15 && lowEstablishedBeforeTail;
      const meaningfulDecline = decline >= atrValue * 4
        && decline / Math.max(priorPeak, 1e-8) >= 0.05;
      higherTimeframeBottomBase = meaningfulDecline
        && lowZonePosition <= 0.48
        && stoppedMakingLows
        && rangeContracted;
      bottomBaseDetail = {
        bars: structureRows.length,
        declineAtr: decline / atrValue,
        declinePercent: decline / Math.max(priorPeak, 1e-8) * 100,
        lowZonePosition,
        rangeCompression: lateRange / Math.max(earlyRange, 1e-8),
      };
    }

    const qualified = hasPriorAdvance || higherTimeframeBottomBase;
    let reason = "画线前缺少可辨认的上推段，当前几何更像普通震荡或失真结构";
    if (unrecoveredFastSelloff || unrecoveredPriorDecline) reason = "画线前是尚未收复的大跌，不把跌后反弹拟合成中继结构";
    else if (downtrendRepairBounce) reason = "局部反弹仍从属于下降趋势，尚未收复母区间与 EMA90，不生成中继结构线";
    else if (insideBroadConsolidation || wideChopWithoutExpansion) reason = "当前几何位于更大盘整区间内部，没有独立上推与区间扩张，不生成结构线";
    else if (!enoughHistory) reason = "画线前历史不足，尚不能确认独立上推段";
    return {
      qualified,
      mode: hasPriorAdvance ? "prior-advance" : higherTimeframeBottomBase ? "higher-timeframe-bottom-base" : "invalid",
      hasPriorAdvance,
      higherTimeframeBottomBase,
      enoughHistory,
      bestAdvance,
      impulseContextStartIndex,
      broadSlopeAtr,
      ema90AtStart,
      ema90ShortSlopeAtr,
      ema90MediumSlopeAtr,
      ema90Falling,
      freshRangeExpansion,
      impulseDominance,
      impulseRepairsTrend,
      downtrendRepairBounce,
      insideBroadConsolidation,
      unrecoveredFastSelloff,
      unrecoveredPriorDecline,
      wideChopWithoutExpansion,
      bottomBaseDetail,
      reason,
      evidence: qualified
        ? hasPriorAdvance
          ? [
            `画线前已有 ${bestAdvance.advanceAtr.toFixed(2)} ATR、持续 ${bestAdvance.bars} 根的独立上推段`,
            `完整推动语境从结构前第 ${startIndex - impulseContextStartIndex} 根开始，画线仍只使用拉升后的盘整高低点`,
          ]
          : [`${interval} 下跌后低位止跌盘整 ${bottomBaseDetail.bars} 根，使用大级别筑底结构豁免`]
        : [reason],
    };
  }

  function detectTriangle(candles, index, atrValue, options = {}) {
    const matches = [];
    const available = Math.min(48, index);
    for (let length = 18; length <= available; length += length < 30 ? 1 : 3) {
      const startIndex = index - length;
      const window = candles.slice(startIndex, index);
      const highs = window.map((row) => row.high);
      const lows = window.map((row) => row.low);
      const estimatedHighSlope = estimateBoundarySlope(highs, "upper");
      const lowSlope = estimateBoundarySlope(lows, "lower");
      const slopeFloor = atrValue * 0.022;
      const flatTopTolerance = atrValue * 0.04;
      const ascendingTriangle = Math.abs(estimatedHighSlope) <= flatTopTolerance
        && lowSlope > slopeFloor;
      const symmetricalTriangle = estimatedHighSlope < -slopeFloor
        && lowSlope > slopeFloor;
      if (!ascendingTriangle && !symmetricalTriangle) continue;

      // 上升三角的压力线必须真正近似水平，不能让若干附近阳线的高点带出斜率。
      const upperSlope = ascendingTriangle ? 0 : estimatedHighSlope;
      const upperFit = fitQuantileEnvelope(highs, upperSlope, "upper");
      const lowerFit = fitQuantileEnvelope(lows, lowSlope, "lower");
      const upperStart = upperFit.lineAt(0);
      const lowerStart = lowerFit.lineAt(0);
      const upperNow = upperFit.lineAt(window.length);
      const lowerNow = lowerFit.lineAt(window.length);
      const earlySpread = upperStart - lowerStart;
      const lateSpread = upperNow - lowerNow;
      if (earlySpread <= atrValue * 1.5
        || lateSpread <= atrValue * 0.45
        || lateSpread >= earlySpread * 0.8
        || lowerNow >= upperNow) continue;

      const envelope = assessEnvelopeCoverage(
        window,
        0,
        window.length,
        upperFit.lineAt,
        lowerFit.lineAt,
        atrValue,
        "wick",
        "wick",
      );
      if (!envelope.acceptable
        || envelope.interiorOccupancy < 0.45
        || envelope.channelSideTransitions < 2) continue;

      const touchTolerance = Math.max(atrValue * 0.38, Math.min(atrValue * 0.62, earlySpread * 0.1));
      const upperTouches = countBoundaryTouchGroups(window, upperFit.lineAt, "upper", touchTolerance);
      const lowerTouches = countBoundaryTouchGroups(window, lowerFit.lineAt, "lower", touchTolerance);
      if (upperTouches.groups < 2 || lowerTouches.groups < 2) continue;

      const alternatingTouches = [];
      window.forEach((row, cursor) => {
        const upperDistance = Math.abs(row.high - upperFit.lineAt(cursor));
        const lowerDistance = Math.abs(row.low - lowerFit.lineAt(cursor));
        const type = Math.min(upperDistance, lowerDistance) <= touchTolerance
          ? (upperDistance <= lowerDistance ? "upper" : "lower")
          : null;
        if (!type) return;
        if (alternatingTouches.at(-1)?.type === type) {
          alternatingTouches[alternatingTouches.length - 1] = { type, cursor };
        } else alternatingTouches.push({ type, cursor });
      });
      const recentTouches = alternatingTouches.slice(-7);
      const touchLegs = recentTouches.slice(1).map((touch, cursor) => (
        touch.cursor - recentTouches[cursor].cursor
      )).filter((bars) => bars > 0);
      const legMean = mean(touchLegs);
      const legDeviation = touchLegs.length >= 3
        ? mean(touchLegs.map((bars) => Math.abs(bars - legMean))) / Math.max(legMean, 1e-8)
        : 1;
      const timeSymmetryQuality = touchLegs.length >= 3 ? clamp(1 - legDeviation, 0, 1) : 0.35;
      const compressionQuality = clamp(1 - lateSpread / earlySpread, 0, 1);
      const structureShape = ascendingTriangle ? "ascending-triangle" : "converging-triangle";
      const preStructureContext = assessPreStructureContext(candles, index, startIndex, atrValue, options);
      if (!preStructureContext.qualified) continue;
      matches.push({
        type: "triangle",
        structureShape,
        level: upperNow,
        stop: lowerNow,
        consolidationBars: length,
        quality: compressionQuality * 0.42
          + timeSymmetryQuality * 0.16
          + envelope.envelopeScore * 0.24
          + clamp((upperTouches.groups + lowerTouches.groups) / 6, 0, 1) * 0.18,
        rhythmQuality: timeSymmetryQuality,
        channelInteriorOccupancy: envelope.interiorOccupancy,
        channelMiddleParticipationRatio: envelope.middleParticipationRatio,
        channelHollowRatio: envelope.hollowRatio,
        channelLongestHollowRun: envelope.longestHollowRun,
        channelSideTransitions: envelope.channelSideTransitions,
        triangleLines: {
          structureStartIndex: startIndex,
          interiorOccupancy: envelope.interiorOccupancy,
          middleParticipationRatio: envelope.middleParticipationRatio,
          hollowRatio: envelope.hollowRatio,
          longestHollowRun: envelope.longestHollowRun,
          sideTransitions: envelope.channelSideTransitions,
          hollowChannel: false,
          upper: {
            startIndex,
            startPrice: upperStart,
            endIndex: index,
            endPrice: upperNow,
            anchorMode: "wick",
            boundaryModel: ascendingTriangle ? "horizontal-pressure-envelope" : "quantile-envelope",
            envelopeCoverage: envelope.upper.structuralCoverage,
            bodyCoverage: envelope.upper.bodyCoverage,
            crossingRatio: envelope.upper.crossingRatio,
            touchGroups: upperTouches.groups,
          },
          lower: {
            startIndex,
            startPrice: lowerStart,
            endIndex: index,
            endPrice: lowerNow,
            anchorMode: "wick",
            boundaryModel: "rising-support-envelope",
            envelopeCoverage: envelope.lower.structuralCoverage,
            bodyCoverage: envelope.lower.bodyCoverage,
            crossingRatio: envelope.lower.crossingRatio,
            touchGroups: lowerTouches.groups,
          },
        },
        evidence: [
          ...preStructureContext.evidence,
          ascendingTriangle ? "上方压力近水平、下方支撑逐级抬高" : "高点下移、低点抬高",
          `压力/支撑有效触碰 ${upperTouches.touches}/${lowerTouches.touches} 次，分为 ${upperTouches.groups}/${lowerTouches.groups} 组`,
          `结构宽度压缩 ${((1 - lateSpread / earlySpread) * 100).toFixed(0)}%`,
          `调整段时间对称度 ${(timeSymmetryQuality * 100).toFixed(0)}%（仅使用已完成摆动）`,
          `上下沿结构包络约 ${(envelope.bodyCoverage * 100).toFixed(0)}%，实体穿线 ${(envelope.crossingRatio * 100).toFixed(0)}%`,
          `轨道内部占用 ${(envelope.interiorOccupancy * 100).toFixed(0)}%，上下沿有效换边 ${envelope.channelSideTransitions} 次，无大片连续空腔`,
          ascendingTriangle ? "水平压力线已经形成，可预设真正前高突破" : "收敛上沿已经形成，可预设突破触发",
        ],
        preStructureContext,
      });
    }
    return matches.sort((a, b) => b.quality - a.quality || b.consolidationBars - a.consolidationBars)[0] || null;
  }

  function assessDescendingTrendlineGeometry(span, slope, atrValue) {
    const normalizedSlopeAtr = Math.abs(Number(slope) || 0) / Math.max(Number(atrValue) || 0, 1e-8);
    const longShallowBridge = Number(span) >= 120 && normalizedSlopeAtr < 0.04;
    return {
      acceptable: !longShallowBridge,
      normalizedSlopeAtr,
      longShallowBridge,
      reason: longShallowBridge
        ? "长跨度近水平连线只是跨阶段旧高，不属于同一盘整的下降趋势线"
        : "",
    };
  }

  function detectDescendingTrendline(candles, index, atrValue, options = {}) {
    // 5分钟长降楔常在主升后的首个冲高处起线，RAVE 2026-04-16 的有效
    // 上沿起点距离突破约 236 根；180 根截窗会在候选生成前直接丢失结构。
    // 扩到 300 根后仍由完整包络、连续空腔、前置拉升和斜率审计约束，
    // 因此不是把任意久远旧高连回来。
    const start = Math.max(2, index - 300);
    const end = index - 3;
    if (end - start < 24) return null;
    const prior = candles[index - 1];
    let best = null;
    const geometries = [
      { mode: "wick", label: "上影线高点", value: (row) => row.high },
      { mode: "body", label: "实体高点", value: (row) => Math.max(row.open, row.close) },
    ];
    geometries.forEach((geometry) => {
      const pivots = [];
      for (let cursor = start; cursor <= end; cursor += 1) {
        const high = geometry.value(candles[cursor]);
        const neighbors = [
          geometry.value(candles[cursor - 2]),
          geometry.value(candles[cursor - 1]),
          geometry.value(candles[cursor + 1]),
          geometry.value(candles[cursor + 2]),
        ];
        if (neighbors.every((value) => high >= value) && neighbors.some((value) => high > value)) {
          pivots.push({ index: cursor, high });
        }
      }
      const recentPivots = pivots.slice(-48);
      for (let firstCursor = 0; firstCursor < recentPivots.length - 1; firstCursor += 1) {
        const first = recentPivots[firstCursor];
        const span = index - first.index;
        if (span < 24) continue;
        // 上沿必须是外包络线，但不能机械选择跨度最长、斜率最平的历史压力线。
        // 同一个首高后的全部有效锚点都参与结构质量评估，优先保留价格持续贴近、
        // 起点来自最近一段明显拉升、并在末端真正收敛的盘整边界。
        const anchors = recentPivots.slice(firstCursor + 1).map((pivot) => ({
          ...pivot,
          separation: pivot.index - first.index,
          slope: (pivot.high - first.high) / Math.max(pivot.index - first.index, 1),
        })).filter((pivot) => (
          pivot.separation >= 8
          && pivot.high < first.high - atrValue * 0.3
          && pivot.slope < -atrValue * 0.003
          && pivot.slope > -atrValue * 0.35
        ));
        if (!anchors.length) continue;
        for (const second of anchors.sort((a, b) => b.slope - a.slope)) {
          const slope = second.slope;
          const trendlineGeometry = assessDescendingTrendlineGeometry(span, slope, atrValue);
          // 跨度很长却几乎水平的两点连线，本质上只是两个相隔很远的旧高，
          // 不是下降趋势线。若它真是水平压力，应交给箱体/上升三角的多触点
          // 结构去确认；不能以一条白色斜线横跨完整深跌与修复周期。
          if (!trendlineGeometry.acceptable) continue;
          const level = first.high + slope * (index - first.index);
          if (level <= 0) continue;
          const distanceAtr = (level - prior.close) / atrValue;
          if (distanceAtr < -0.35 || distanceAtr > 2.2) continue;
          const lineAt = (cursor) => first.high + slope * (cursor - first.index);
          const inspected = candles.slice(first.index + 1, index);
          const containment = assessBoundaryContainment(
            candles,
            first.index + 1,
            index,
            lineAt,
            "upper",
            atrValue,
            geometry.mode,
          );
          if (!containment.acceptable) continue;
          const activeProximity = inspected.filter((row, offset) => {
            const cursor = first.index + 1 + offset;
            const gapAtr = (lineAt(cursor) - geometry.value(row)) / atrValue;
            return gapAtr >= -0.35 && gapAtr <= 3.4;
          }).length / Math.max(inspected.length, 1);
          const gapAtrs = inspected.map((row, offset) => {
            const cursor = first.index + 1 + offset;
            return (lineAt(cursor) - geometry.value(row)) / atrValue;
          });
          let currentSevereHollowRun = 0;
          let longestSevereHollowRun = 0;
          gapAtrs.forEach((gapAtr) => {
            if (gapAtr > 5.5) {
              currentSevereHollowRun += 1;
              longestSevereHollowRun = Math.max(longestSevereHollowRun, currentSevereHollowRun);
            } else {
              currentSevereHollowRun = 0;
            }
          });
          const middleStart = Math.floor(gapAtrs.length / 3);
          const middleEnd = Math.max(middleStart + 1, Math.ceil(gapAtrs.length * 2 / 3));
          const middleGaps = gapAtrs.slice(middleStart, middleEnd);
          const middleParticipation = middleGaps.filter((gapAtr) => (
            gapAtr >= -0.35 && gapAtr <= 4.5
          )).length / Math.max(middleGaps.length, 1);
          const maximumGapAtr = Math.max(...gapAtrs, 0);
          // 一条压力线若跨越很久，但大部分 K 线长期远离边界，只是把两个行情
          // 阶段强行连在一起；它不属于当前盘整。长结构至少半数 K 线应处于
          // 上沿的有效活动带内，防止上市初期高点压住后续全新的主升结构。
          // 长降楔的上沿有时由首个冲高与末端递减高点定义，中段价格会更多
          // 沿下轨运行。此时单看上沿会显得“离线”，但上下轨合起来仍可能是
          // 一个交易路径连续、没有空白阶段的完整降楔。先保留一条严格受限的
          // provisional 上沿交给 detectLongConvergence 做双轨审计；它本身绝不
          // 作为趋势线买点，也不会单独画到盘面。
          const provisionalLongBoundary = span >= 180
            && containment.structuralCoverage >= 0.96
            && containment.bodyCoverage >= 0.97
            && containment.crossingRatio <= 0.04
            && activeProximity >= 0.3
            && distanceAtr <= 1.55;
          if (span >= 72 && activeProximity < 0.34 && !provisionalLongBoundary) continue;
          // MMT 15m 这类“旧高—新高”连线虽然包络没有被 K 线向上穿越，
          // 但中间经历了完整深跌和重新修复，价格曾连续很久远离压力线，视觉上
          // 是一个巨大的空心碗而非同一盘整。必须限制连续深空腔，并要求结构
          // 中段也真实参与这条边界，不能只靠两端孤立高点成立。
          const severeHollowLimit = Math.max(12, Math.ceil(gapAtrs.length * 0.25));
          if (span >= 48 && longestSevereHollowRun >= severeHollowLimit && !provisionalLongBoundary) continue;
          if (span >= 72 && maximumGapAtr > 8 && middleParticipation < 0.18 && !provisionalLongBoundary) continue;
          const touches = recentPivots.filter((pivot) => (
            pivot.index >= first.index
            && pivot.index < index
            && Math.abs(pivot.high - lineAt(pivot.index)) <= atrValue * 0.5
          )).length;
          if (touches < 2) continue;
          const adherence = containment.envelopeScore;
          const originFloor = Math.min(...candles.slice(Math.max(0, first.index - 12), first.index + 1).map((row) => row.low));
          const originExpansionAtr = Math.max(0, first.high - originFloor) / atrValue;
          const originRangeAtr = (candles[first.index].high - candles[first.index].low) / atrValue;
          const structuralOriginQuality = clamp(originExpansionAtr / 5, 0, 1) * 0.58
            + clamp(span / 110, 0, 1) * 0.42;
          const preStructureContext = assessPreStructureContext(candles, index, first.index, atrValue, options);
          if (!preStructureContext.qualified) continue;
          const quality = clamp(
            clamp(span / 100, 0, 1) * 0.25
            + clamp(touches / 4, 0, 1) * 0.25
            + adherence * 0.25
            + clamp(1 - Math.max(distanceAtr, 0) / 2.2, 0, 1) * 0.15
            + structuralOriginQuality * 0.1,
            0,
            1,
          );
          const candidate = {
            type: "trendline",
            level,
            stop: Math.min(...candles.slice(Math.max(0, index - 4), index).map((row) => row.low)),
            consolidationBars: span,
            quality,
            trendline: {
              startIndex: first.index,
              startPrice: first.high,
              // 首段冲高形成母结构的时间边界。后续下轨只能使用这个边界之后
              // 的盘整 / 回调低点，不能向前借用拉升前的低点来美化斜率。
              structureStartIndex: first.index,
              postImpulseStartIndex: first.index + 1,
              anchorScope: "post-impulse-consolidation",
              anchorIndex: second.index,
              anchorPrice: second.high,
              endIndex: index,
              endPrice: level,
              touches,
              adherence,
              envelopeCoverage: containment.structuralCoverage,
              bodyCoverage: containment.bodyCoverage,
              wickCoverage: containment.wickCoverage,
              crossingRatio: containment.crossingRatio,
              crossingBars: containment.crossingBars,
              outsideTransitions: containment.outsideTransitions,
              anchorMode: geometry.mode,
              boundaryModel: "outer-envelope",
              originExpansionAtr,
              originRangeAtr,
              activeProximity,
              middleParticipation,
              longestSevereHollowRun,
              maximumGapAtr,
              normalizedSlopeAtr: trendlineGeometry.normalizedSlopeAtr,
              provisionalLongBoundary,
            },
            evidence: [
              ...preStructureContext.evidence,
              `下降压力线从首段冲高的${geometry.label}出发，连接后续最外沿递减高点`,
              `趋势线已延续 ${span} 根，触碰 ${touches} 次，结构包络 ${(containment.structuralCoverage * 100).toFixed(0)}%`,
              `实体穿线 ${(containment.crossingRatio * 100).toFixed(0)}%，不允许在线两侧反复穿插`,
              `趋势线中段参与 ${(middleParticipation * 100).toFixed(0)}%，连续深空腔最长 ${longestSevereHollowRun} 根`,
              "采用覆盖主体 K 线的外包络；实体线与影线线分别拟合后择优",
              "当前外推只使用突破前已经确认的摆动高点",
            ],
            preStructureContext,
            provisionalLongBoundary,
          };
          const recencyQuality = clamp(1 - span / 180, 0, 1);
          const structureRank = quality
            + structuralOriginQuality * 0.12
            + activeProximity * 0.18
            + recencyQuality * 0.06
            + clamp(originRangeAtr / 6, 0, 1) * 0.08;
          if (!best || structureRank > best.structureRank) best = { ...candidate, structureRank };
        }
      }
    });
    return best;
  }

  function detectLongConvergence(candles, index, atrValue, upperCandidate, options = {}) {
    const upper = upperCandidate?.trendline;
    if (!upper || index - upper.startIndex < 36) return null;
    const upperSlope = (upper.anchorPrice - upper.startPrice)
      / Math.max(upper.anchorIndex - upper.startIndex, 1);
    if (upperSlope >= 0) return null;
    const upperAt = (cursor) => upper.startPrice + upperSlope * (cursor - upper.startIndex);
    // 上沿首个冲高点同时定义这段盘整 / 回调的结构窗口。上下沿仍可使用
    // 不同时间的有效摆动点，但下沿不得越过该窗口去连接拉升前的低点。
    const structureStartIndex = Math.max(2, upper.structureStartIndex ?? upper.startIndex);
    const preStructureContext = upperCandidate.preStructureContext
      || assessPreStructureContext(candles, index, structureStartIndex, atrValue, options);
    if (!preStructureContext.qualified) return null;
    const postImpulseStartIndex = Math.max(
      structureStartIndex + 1,
      upper.postImpulseStartIndex ?? structureStartIndex + 1,
    );
    const start = Math.max(postImpulseStartIndex, index - 300);
    const end = index - 3;
    let best = null;
    const lowerGeometries = [
      { mode: "wick", label: "下影线低点", value: (row) => row.low },
      { mode: "body", label: "实体低点", value: (row) => Math.min(row.open, row.close) },
    ];
    lowerGeometries.forEach((geometry) => {
      const lows = [];
      for (let cursor = start; cursor <= end; cursor += 1) {
        const low = geometry.value(candles[cursor]);
        const neighbors = [
          geometry.value(candles[cursor - 2]),
          geometry.value(candles[cursor - 1]),
          geometry.value(candles[cursor + 1]),
          geometry.value(candles[cursor + 2]),
        ];
        if (neighbors.every((value) => low <= value) && neighbors.some((value) => low < value)) {
          lows.push({ index: cursor, low });
        }
      }
      const recentLows = lows.slice(-48);
      for (let firstCursor = 0; firstCursor < recentLows.length - 1; firstCursor += 1) {
        const first = recentLows[firstCursor];
        if (index - first.index < 30 || first.index > upper.startIndex + 28) continue;
        const lowerAnchors = recentLows.slice(firstCursor + 1).map((pivot) => ({
          ...pivot,
          separation: pivot.index - first.index,
          slope: (pivot.low - first.low) / Math.max(pivot.index - first.index, 1),
        })).filter((pivot) => (
          pivot.separation >= 8
          && pivot.slope > upperSlope + atrValue * 0.003
          && pivot.slope > -atrValue * 0.18
          && pivot.slope < atrValue * 0.16
        ));
        if (!lowerAnchors.length) continue;
        // 支撑线也使用外包络：取仍能与上沿收敛的最小斜率，确保主要低点
        // 位于线上方。斜率为负时即为下降楔形，为正时是普通收敛三角。
        const second = lowerAnchors.sort((a, b) => a.slope - b.slope)[0];
        const separation = second.index - first.index;
        const span = index - first.index;
        if (separation < 8 || span < 30) continue;
        const lowerSlope = (second.low - first.low) / separation;
        if (lowerSlope <= upperSlope + atrValue * 0.003 || lowerSlope >= atrValue * 0.16) continue;
        const fallingWedge = lowerSlope < 0;
        const lowerAt = (cursor) => first.low + lowerSlope * (cursor - first.index);
        const lowerNow = lowerAt(index);
        const upperNow = upperAt(index);
        const initialIndex = Math.max(first.index, upper.startIndex);
        const initialGap = upperAt(initialIndex) - lowerAt(initialIndex);
        const lateGap = upperNow - lowerNow;
        const maximumGapRatio = (upper.activeProximity || 0) >= 0.9
          && (upper.originRangeAtr || 0) >= 2.4
          ? 0.84
          : 0.72;
        if (initialGap <= atrValue * 1.2
          || lateGap <= atrValue * 0.12
          || lateGap >= initialGap * maximumGapRatio) continue;
        const priorDistance = (upperNow - candles[index - 1].close) / atrValue;
        if (priorDistance < -0.35 || priorDistance > 1.55) continue;

        // 上下轨必须解释整个盘整/回调区间，而不是只用末端 120 根把早期
        // 的大片空白藏掉。扩展回看后尤其要用完整区间做包络审计。
        const inspectedStart = initialIndex;
        let lowerTouches = 0;
        const envelope = assessEnvelopeCoverage(
          candles,
          inspectedStart,
          index,
          upperAt,
          lowerAt,
          atrValue,
          upper.anchorMode || "wick",
          geometry.mode,
        );
        lowerTouches = recentLows.filter((pivot) => (
          pivot.index >= inspectedStart
          && Math.abs(pivot.low - lowerAt(pivot.index)) <= atrValue * 0.55
        )).length;
        const upperTouches = Number.isFinite(upper.touches) ? upper.touches : 2;
        if (!envelope.acceptable
          || lowerTouches < 2
          || upperTouches < 2) continue;
        // 计算锚点仍由两个已确认低点决定，但绘制时允许把支撑线向左外推到
        // 拉升后的盘整起点。只有至少 95% 实体仍在线上方才延长，绝不借用
        // 拉升前低点，也不改变斜率、触发价或买点时间。
        let lowerDisplayStartIndex = first.index;
        const displayTolerance = atrValue * 0.12;
        for (let cursor = postImpulseStartIndex; cursor < first.index; cursor += 1) {
          const displayRows = candles.slice(cursor, first.index + 1);
          const contained = displayRows.filter((row, offset) => (
            Math.min(row.open, row.close) >= lowerAt(cursor + offset) - displayTolerance
          )).length / Math.max(displayRows.length, 1);
          if (contained >= 0.95) {
            lowerDisplayStartIndex = cursor;
            break;
          }
        }
        const lowerDisplayStartPrice = lowerAt(lowerDisplayStartIndex);
        const compressionQuality = clamp(1 - lateGap / initialGap, 0, 1);
        const adherence = envelope.envelopeScore;
        const durationQuality = clamp(span / 90, 0, 1);
        const quality = clamp(
          compressionQuality * 0.34
          + adherence * 0.25
          + durationQuality * 0.21
          + clamp((upperTouches + lowerTouches) / 7, 0, 1) * 0.2,
          0,
          1,
        );
        const candidate = {
          type: "triangle",
          level: upperNow,
          stop: lowerNow,
          consolidationBars: Math.max(index - upper.startIndex, span),
          quality,
          rhythmQuality: compressionQuality,
          structureShape: fallingWedge ? "falling-wedge" : "converging-triangle",
          trendline: upper,
          channelInteriorOccupancy: envelope.interiorOccupancy,
          channelMiddleParticipationRatio: envelope.middleParticipationRatio,
          channelHollowRatio: envelope.hollowRatio,
          channelLongestHollowRun: envelope.longestHollowRun,
          channelSideTransitions: envelope.channelSideTransitions,
          triangleLines: {
            structureStartIndex,
            anchorScope: "post-impulse-consolidation",
            interiorOccupancy: envelope.interiorOccupancy,
            middleParticipationRatio: envelope.middleParticipationRatio,
            hollowRatio: envelope.hollowRatio,
            longestHollowRun: envelope.longestHollowRun,
            sideTransitions: envelope.channelSideTransitions,
            hollowChannel: false,
            upper: {
              startIndex: upper.startIndex,
              startPrice: upper.startPrice,
              endIndex: index,
              endPrice: upperNow,
              anchorMode: upper.anchorMode || "wick",
              boundaryModel: "outer-envelope",
              envelopeCoverage: envelope.upper.structuralCoverage,
              bodyCoverage: envelope.upper.bodyCoverage,
              crossingRatio: envelope.upper.crossingRatio,
              touchGroups: upperTouches,
              structureStartIndex,
              anchorScope: "post-impulse-consolidation",
            },
            lower: {
              startIndex: first.index,
              startPrice: first.low,
              displayStartIndex: lowerDisplayStartIndex,
              displayStartPrice: lowerDisplayStartPrice,
              endIndex: index,
              endPrice: lowerNow,
              anchorMode: geometry.mode,
              anchorIndex: second.index,
              anchorPrice: second.low,
              boundaryModel: "outer-envelope",
              envelopeCoverage: envelope.lower.structuralCoverage,
              bodyCoverage: envelope.lower.bodyCoverage,
              crossingRatio: envelope.lower.crossingRatio,
              touchGroups: lowerTouches,
              structureStartIndex,
              anchorScope: "post-impulse-consolidation",
            },
          },
          evidence: [
            ...preStructureContext.evidence,
            `长周期收敛已持续 ${Math.max(index - upper.startIndex, span)} 根 K 线`,
            fallingWedge
              ? `下降楔形：上下沿同步下压，但上沿下降更快，楔口收窄 ${(compressionQuality * 100).toFixed(0)}%`
              : `下降上沿与抬高下沿使结构宽度收窄 ${(compressionQuality * 100).toFixed(0)}%`,
            `上下沿共有 ${upperTouches + lowerTouches} 个已确认摆动触点（上 ${upperTouches} / 下 ${lowerTouches}）`,
            `下沿从冲高后盘整区的首个回撤${geometry.label}起连，上沿从首段冲高${upper.anchorMode === "body" ? "实体高点" : "上影线高点"}起连`,
            "上下轨锚点限定在同一段拉升后盘整 / 回调窗口，拉升前 K 线不参与拟合",
            `下轨锚点不变，白线向左延长 ${first.index - lowerDisplayStartIndex} 根以覆盖完整盘整（实体包络不低于 95%）`,
            "上下沿均采用母结构外包络，不用末端局部高低点重画内切线",
            `长结构主体包络 ${(envelope.bodyCoverage * 100).toFixed(0)}%，实体穿线 ${(envelope.crossingRatio * 100).toFixed(0)}%`,
            `轨道内部占用 ${(envelope.interiorOccupancy * 100).toFixed(0)}%，上下沿有效换边 ${envelope.channelSideTransitions} 次，无大片连续空腔`,
            "趋势线只作为收敛母结构的上沿触发，不作为独立买点",
          ],
          preStructureContext,
        };
        const originAlignment = clamp(1 - Math.abs(first.index - upper.startIndex) / 56, 0, 1);
        const structureRank = quality + clamp(span / 120, 0, 1) * 0.08 + originAlignment * 0.05;
        if (!best || structureRank > best.structureRank) best = { ...candidate, structureRank };
      }
    });
    // 超长降楔的下轨不一定恰好穿过两枚局部最低点。RAVE 2026-04-16
    // 一类结构会在近 300 根 K 线中缓慢下移，局部低点直连反而把后续大量
    // K 线切在线下。此时只允许对“已经通过 96% 以上完整上沿包络审计”的
    // 临时长上轨，补做整个盘整窗口的低位分段斜率 + 2.5% 分位外包络。
    // 临时上轨自身不可执行；只有上下轨共同通过完整轨道审计才升级为结构。
    if (!best && upper.provisionalLongBoundary === true) {
      const envelopeStart = Math.max(postImpulseStartIndex, upper.startIndex);
      const envelopeRows = candles.slice(envelopeStart, index);
      if (envelopeRows.length >= 180) {
        lowerGeometries.forEach((geometry) => {
          const values = envelopeRows.map(geometry.value);
          const lowerSlope = estimateBoundarySlope(values, "lower");
          if (lowerSlope <= upperSlope + atrValue * 0.003
            || lowerSlope >= 0
            || lowerSlope <= -atrValue * 0.18) return;
          const fitted = fitQuantileEnvelope(values, lowerSlope, "lower");
          const lowerAt = (cursor) => fitted.lineAt(cursor - envelopeStart);
          const upperNow = upperAt(index);
          const lowerNow = lowerAt(index);
          const initialGap = upperAt(envelopeStart) - lowerAt(envelopeStart);
          const lateGap = upperNow - lowerNow;
          if (initialGap <= atrValue * 1.2
            || lateGap <= atrValue * 0.12
            || lateGap >= initialGap * 0.72) return;
          const priorDistance = (upperNow - candles[index - 1].close) / atrValue;
          if (priorDistance < -0.35 || priorDistance > 1.55) return;
          const envelope = assessEnvelopeCoverage(
            candles,
            envelopeStart,
            index,
            upperAt,
            lowerAt,
            atrValue,
            upper.anchorMode || "wick",
            geometry.mode,
          );
          const lowerTouches = countBoundaryTouchGroups(
            envelopeRows,
            (cursor) => lowerAt(envelopeStart + cursor),
            "lower",
            atrValue * 0.55,
          );
          const upperTouches = Number.isFinite(upper.touches) ? upper.touches : 2;
          if (!envelope.acceptable
            || envelope.lower.structuralCoverage < 0.95
            || envelope.interiorOccupancy < 0.58
            || envelope.middleParticipationRatio < 0.3
            || envelope.channelSideTransitions < 3
            || lowerTouches.groups < 2
            || upperTouches < 2) return;
          const compressionQuality = clamp(1 - lateGap / initialGap, 0, 1);
          const durationQuality = clamp(envelopeRows.length / 240, 0, 1);
          const quality = clamp(
            compressionQuality * 0.32
            + envelope.envelopeScore * 0.3
            + durationQuality * 0.2
            + clamp((upperTouches + lowerTouches.groups) / 7, 0, 1) * 0.18,
            0,
            1,
          );
          const candidate = {
            type: "triangle",
            level: upperNow,
            stop: lowerNow,
            consolidationBars: envelopeRows.length,
            quality,
            rhythmQuality: compressionQuality,
            structureShape: "falling-wedge",
            trendline: upper,
            channelInteriorOccupancy: envelope.interiorOccupancy,
            channelMiddleParticipationRatio: envelope.middleParticipationRatio,
            channelHollowRatio: envelope.hollowRatio,
            channelLongestHollowRun: envelope.longestHollowRun,
            channelSideTransitions: envelope.channelSideTransitions,
            triangleLines: {
              structureStartIndex,
              anchorScope: "post-impulse-consolidation",
              interiorOccupancy: envelope.interiorOccupancy,
              middleParticipationRatio: envelope.middleParticipationRatio,
              hollowRatio: envelope.hollowRatio,
              longestHollowRun: envelope.longestHollowRun,
              sideTransitions: envelope.channelSideTransitions,
              hollowChannel: false,
              upper: {
                startIndex: upper.startIndex,
                startPrice: upper.startPrice,
                endIndex: index,
                endPrice: upperNow,
                anchorMode: upper.anchorMode || "wick",
                boundaryModel: "outer-envelope",
                envelopeCoverage: envelope.upper.structuralCoverage,
                bodyCoverage: envelope.upper.bodyCoverage,
                crossingRatio: envelope.upper.crossingRatio,
                touchGroups: upperTouches,
                structureStartIndex,
                anchorScope: "post-impulse-consolidation",
              },
              lower: {
                startIndex: envelopeStart,
                startPrice: lowerAt(envelopeStart),
                displayStartIndex: envelopeStart,
                displayStartPrice: lowerAt(envelopeStart),
                endIndex: index,
                endPrice: lowerNow,
                anchorMode: geometry.mode,
                anchorIndex: null,
                anchorPrice: null,
                boundaryModel: "quantile-outer-envelope",
                envelopeCoverage: envelope.lower.structuralCoverage,
                bodyCoverage: envelope.lower.bodyCoverage,
                crossingRatio: envelope.lower.crossingRatio,
                touchGroups: lowerTouches.groups,
                structureStartIndex,
                anchorScope: "post-impulse-consolidation",
              },
            },
            evidence: [
              ...preStructureContext.evidence,
              `长周期降楔持续 ${envelopeRows.length} 根 K 线，上下沿宽度收窄 ${(compressionQuality * 100).toFixed(0)}%`,
              `完整下轨采用分段低位斜率与 2.5% 外包络，主体覆盖 ${(envelope.lower.structuralCoverage * 100).toFixed(0)}%`,
              `轨道内部占用 ${(envelope.interiorOccupancy * 100).toFixed(0)}%，中部参与 ${(envelope.middleParticipationRatio * 100).toFixed(0)}%，有效换边 ${envelope.channelSideTransitions} 次`,
              `上下沿共有 ${upperTouches + lowerTouches.groups} 组已确认触点，且无大片连续空腔`,
              "上下轨都从拉升后的同一盘整窗口起算，不借用拉升前低点",
              "临时长上轨本身不可执行；只有严格双轨外包络完成后才升级为降楔结构",
            ],
            preStructureContext,
          };
          const structureRank = quality + durationQuality * 0.08;
          if (!best || structureRank > best.structureRank) best = { ...candidate, structureRank };
        });
      }
    }
    return best;
  }

  function isAttachedSoftBoundaryTest(row, boundary, atrValue) {
    if (!row || !Number.isFinite(boundary) || !Number.isFinite(atrValue) || atrValue <= 0) return false;
    const highAttached = row.high <= boundary + atrValue * 1.05;
    const bodyAttached = Math.max(row.open, row.close) <= boundary + atrValue * 0.12;
    // 成熟长结构的上沿偶尔会先被一根阴线开盘/上影短暂探出，随后收回线下。
    // 这种 K 线是对压力的试探与拒绝，不是结构已经突破。若机械要求开盘和
    // 实体最高点都留在线下，会把真正的下一根放量突破当成普通箱体突破，
    // 从而丢失原有三角/楔形语义（XRP 2024-11-22 01:00 1h）。
    const rejectedBackInside = row.close <= boundary + atrValue * 0.12
      && row.open <= boundary + atrValue * 0.38
      && row.close <= row.open;
    return highAttached && (bodyAttached || rejectedBackInside);
  }

  function recoverLongTriangleAfterSoftBoundaryTests(candles, index, indicators, options = {}) {
    // 成熟三角第一次越线后若只是在上沿附近收回，结构并没有结束。允许回看
    // 最近三根的既有因果结构，把一至两根“贴线上方但没有实体脱离”的试盘
    // 包回原三角；只有当前阳线以明显实体远离上轨时才恢复为可执行候选。
    // 这不是使用未来数据：试盘K、当前K和所有锚点在当前时刻都已经存在。
    for (let lag = 1; lag <= 3; lag += 1) {
      const probeIndex = index - lag;
      if (probeIndex < 40) continue;
      const probeAtr = Math.max(
        indicators.atr[probeIndex - 1] || candles[probeIndex - 1].high - candles[probeIndex - 1].low,
        1e-8,
      );
      const probeTrendline = detectDescendingTrendline(candles, probeIndex, probeAtr, options);
      if (!probeTrendline) continue;
      const probeTriangle = detectLongConvergence(
        candles,
        probeIndex,
        probeAtr,
        probeTrendline,
        options,
      );
      if (!probeTriangle
        || !["converging-triangle", "falling-wedge"].includes(probeTriangle.structureShape)
        || (probeTriangle.consolidationBars || 0) < 36
        || (probeTriangle.quality || 0) < 0.68
        || (probeTriangle.channelInteriorOccupancy || 0) < 0.68
        || (probeTriangle.channelMiddleParticipationRatio || 0) < 0.5
        || (probeTriangle.channelHollowRatio || 1) > 0.66
        || (probeTriangle.channelLongestHollowRun || 99) > 12
        || (probeTriangle.channelSideTransitions || 0) < 3) continue;
      const upper = probeTriangle.triangleLines?.upper;
      const lower = probeTriangle.triangleLines?.lower;
      if (!upper || !lower) continue;
      const lineAt = (line, cursor) => {
        const slope = (line.endPrice - line.startPrice)
          / Math.max(line.endIndex - line.startIndex, 1);
        return line.startPrice + slope * (cursor - line.startIndex);
      };
      const softTests = candles.slice(probeIndex, index);
      const remainedAttached = softTests.length >= 1 && softTests.every((row, offset) => {
        const cursor = probeIndex + offset;
        const boundary = lineAt(upper, cursor);
        return isAttachedSoftBoundaryTest(row, boundary, probeAtr);
      });
      if (!remainedAttached) continue;
      const upperNow = lineAt(upper, index);
      const lowerNow = lineAt(lower, index);
      const current = candles[index];
      const decisiveSeparation = current.open <= upperNow + probeAtr * 0.08
        && current.high >= upperNow
        && current.close >= upperNow + probeAtr * 0.28
        && current.close - current.open >= probeAtr * 0.85;
      if (!decisiveSeparation || lowerNow >= upperNow) continue;
      return {
        ...probeTriangle,
        level: upperNow,
        stop: lowerNow,
        consolidationBars: (probeTriangle.consolidationBars || 0) + lag,
        softBoundaryTests: lag,
        softTestExtendedTriangle: true,
        triangleLines: {
          ...probeTriangle.triangleLines,
          upper: { ...upper, endIndex: index, endPrice: upperNow },
          lower: { ...lower, endIndex: index, endPrice: lowerNow },
        },
        evidence: [
          ...(probeTriangle.evidence || []),
          `前 ${lag} 根仅在上轨附近试盘并收回，仍归入原盘整；当前实体首次明确脱离上轨`,
        ],
      };
    }
    return null;
  }

  function detectPivotReclaim(candles, index, atrValue, ema90Value) {
    if (index < 18) return null;
    const candidates = [];
    for (let length = 10; length <= Math.min(36, index); length += 1) {
      const startIndex = index - length;
      const window = candles.slice(startIndex, index);
      const peakSearchEnd = Math.max(4, Math.floor(window.length * 0.64));
      let peakOffset = 0;
      for (let cursor = 1; cursor < peakSearchEnd; cursor += 1) {
        if (window[cursor].high > window[peakOffset].high) peakOffset = cursor;
      }
      if (peakOffset > window.length - 6) continue;
      let lowOffset = peakOffset + 1;
      for (let cursor = peakOffset + 2; cursor < window.length - 2; cursor += 1) {
        if (window[cursor].low < window[lowOffset].low) lowOffset = cursor;
      }
      const baseRows = window.slice(lowOffset + 1);
      if (baseRows.length < 3) continue;
      const originRows = candles.slice(Math.max(0, startIndex - 24), startIndex + peakOffset + 1);
      const impulseOrigin = Math.min(...originRows.map((row) => row.low));
      const impulsePeak = window[peakOffset].high;
      const advance = impulsePeak - impulseOrigin;
      if (advance < atrValue * 1.2) continue;
      const pullbackDepth = impulsePeak - window[lowOffset].low;
      const retracementRatio = pullbackDepth / Math.max(advance, 1e-8);
      if (retracementRatio < 0.1 || retracementRatio > 0.68) continue;
      const reclaimLevel = Math.max(...baseRows.map((row) => row.high));
      const baseLowSlope = regressionSlope(baseRows.map((row) => row.low));
      const baseCloseSlope = regressionSlope(baseRows.map((row) => row.close));
      const stabilized = baseLowSlope >= -atrValue * 0.035
        && baseCloseSlope >= -atrValue * 0.025;
      if (!stabilized) continue;
      if (candles[index - 1].close < ema90Value - atrValue * 1.25) continue;
      const distanceAtr = Math.max(0, reclaimLevel - candles[index - 1].close) / atrValue;
      if (distanceAtr > 1.8) continue;
      const quality = clamp(
        clamp(advance / Math.max(atrValue * 4, 1e-8), 0, 1) * 0.28
        + clamp(1 - Math.abs(retracementRatio - 0.38) / 0.38, 0, 1) * 0.24
        + clamp(baseRows.length / 10, 0, 1) * 0.2
        + clamp(1 - distanceAtr / 1.8, 0, 1) * 0.16
        + clamp((candles[index - 1].close - ema90Value + atrValue) / Math.max(atrValue * 2.5, 1e-8), 0, 1) * 0.12,
        0,
        1,
      );
      candidates.push({
        type: "pivot",
        level: reclaimLevel,
        stop: window[lowOffset].low,
        quality,
        structuredPivot: true,
        structureStartIndex: startIndex + peakOffset,
        pivotLowIndex: startIndex + lowOffset,
        consolidationBars: window.length - peakOffset,
        retracementRatio,
        priorAdvanceAtr: advance / atrValue,
        evidence: [
          `前置拉升 ${Number(advance / atrValue).toFixed(2)} ATR 后回撤 ${(retracementRatio * 100).toFixed(0)}%，未破坏 0.618 附近的主升结构`,
          `低点后稳定 ${baseRows.length} 根，不再持续创新低`,
          `从线下突破回踩平台局部高点 ${reclaimLevel.toFixed(8)}，确认拐点重新向上`,
        ],
      });
    }
    return candidates.sort((a, b) => b.quality - a.quality || b.consolidationBars - a.consolidationBars)[0] || null;
  }

  function detectPreviousHigh(candles, index, atrValue) {
    const lookbacks = [8, 12, 18, 24, 36, 60].filter((length) => length <= index);
    const priorClose = candles[index - 1].close;
    const candidates = lookbacks.map((length) => {
      const pauseBars = Math.min(4, Math.max(3, length - 4));
      const resistanceWindow = candles.slice(index - length, index - pauseBars);
      const pause = candles.slice(index - pauseBars, index);
      if (resistanceWindow.length < 4 || pause.length < 3) return null;
      const level = Math.max(...resistanceWindow.map((row) => row.high));
      const floor = Math.min(...candles.slice(index - Math.min(14, length), index).map((row) => row.low));
      const pressure = candles.slice(Math.max(0, index - 8), index).filter((row) => row.close >= level - atrValue * 1.25).length;
      const distance = Math.max(0, level - priorClose) / atrValue;
      const pauseRange = Math.max(...pause.map((row) => row.high)) - Math.min(...pause.map((row) => row.low));
      const pauseDrift = Math.abs(pause.at(-1).close - pause[0].close);
      if (pauseRange > atrValue * 4.8 || pauseDrift > atrValue * 1.55) return null;
      const quality = clamp(pressure / 5, 0, 1) * 0.5
        + clamp(length / 36, 0, 1) * 0.25
        + clamp(1 - distance / 2.2, 0, 1) * 0.25;
      return {
        type: "previousHigh",
        level,
        stop: floor,
        lookback: length,
        quality,
        evidence: [
          `${length} 根 K 线局部前高已经形成`,
          `前高下方承接 ${pressure}/8 根`,
          "首次上穿时触发，不等待突破后的结果",
        ],
      };
    }).filter(Boolean).filter((item) => item.level - priorClose <= atrValue * 2.5);
    return candidates.sort((a, b) => b.quality - a.quality || b.lookback - a.lookback)[0] || null;
  }

  function detectPullbackRelaunch(candles, index, atrValue, indicators) {
    if (index < 45) return null;
    const priorIndex = index - 1;
    const ema90Value = indicators.ema90[priorIndex];
    const priorEma90 = indicators.ema90[Math.max(0, priorIndex - 18)];
    if (!Number.isFinite(ema90Value) || ema90Value <= priorEma90 || candles[priorIndex].close <= ema90Value - atrValue) return null;
    let best = null;
    for (let length = 6; length <= Math.min(28, index - 24); length += 1) {
      const flag = candles.slice(index - length, index);
      const before = candles.slice(Math.max(0, index - length - 24), index - length);
      if (before.length < 16) continue;
      const terminal = flag.slice(Math.max(2, Math.floor(length * 0.45)));
      const level = Math.max(...terminal.map((row) => row.high));
      const floor = Math.min(...flag.map((row) => row.low));
      const terminalFloor = Math.min(...terminal.map((row) => row.low));
      const width = level - terminalFloor;
      const impulseOrigin = Math.min(...before.map((row) => row.low));
      const impulsePeak = Math.max(...before.slice(-4).concat(flag.slice(0, 3)).map((row) => row.high));
      const priorAdvance = impulsePeak - impulseOrigin;
      const halfRetracement = impulseOrigin + priorAdvance * 0.5;
      const flagDrift = Math.abs(flag.at(-1).close - flag[0].close);
      const pullbackSteps = flag.slice(1).filter((row, cursor) => row.close < flag[cursor].close).length;
      const split = Math.floor(flag.length / 2);
      const earlyLow = Math.min(...flag.slice(0, split).map((row) => row.low));
      const lateLow = Math.min(...flag.slice(split).map((row) => row.low));
      const risingLows = lateLow >= earlyLow - atrValue * 0.18
        && regressionSlope(flag.map((row) => row.low)) >= -atrValue * 0.02;
      const holdsHalf = floor >= halfRetracement - atrValue * 0.3;
      const shallowFloor = floor >= ema90Value - atrValue * 1.35;
      const compact = width <= atrValue * 4.8;
      const hadImpulse = priorAdvance >= atrValue * 2.2;
      const pausedInsteadOfTrending = flagDrift <= atrValue * 1.65 && pullbackSteps >= 2;
      const nearLevel = level - flag.at(-1).close <= atrValue * 1.25;
      if (!shallowFloor || !holdsHalf || !risingLows || !compact || !hadImpulse || !pausedInsteadOfTrending || !nearLevel) continue;
      const quality = clamp(
        (1 - width / Math.max(atrValue * 5, 1e-8)) * 0.3
        + clamp(priorAdvance / Math.max(atrValue * 6, 1e-8), 0, 1) * 0.24
        + clamp((floor - halfRetracement + atrValue * 0.3) / Math.max(atrValue * 1.8, 1e-8), 0, 1) * 0.24
        + clamp((lateLow - earlyLow + atrValue * 0.18) / Math.max(atrValue, 1e-8), 0, 1) * 0.22,
        0,
        1,
      );
      const candidate = {
        type: "relaunch",
        level,
        stop: terminalFloor,
        consolidationBars: length,
        quality,
        evidence: [
          `明显拉升后浅回踩 ${length} 根 K 线，守住前段涨幅 50% 位`,
          `末端低点抬高，局部结构宽度 ${(width / atrValue).toFixed(2)} ATR`,
          "只突破末端小结构即可触发，不必等待更远的历史前高",
        ],
      };
      if (!best || candidate.quality > best.quality) best = candidate;
    }
    return best;
  }

  // 主升中的 EMA90 修复不是某一种固定图形，而是一层更高的结构语境：先有独立
  // 拉升，整理中短暂失守 EMA90，随后重新站回并在上行均线上方稳定蓄力，最后由
  // 成熟横盘、箱体、三角、降楔或前高附近盘整的真实外沿触发。这里仅使用触发 K
  // 线以前已经收盘的数据；局部上升楔形只有在它只是更大修复结构末端的内嵌拟合时
  // 才能被覆盖，真正覆盖完整母结构的上升通道/上升楔形仍继续否决。
  function assessEma90ReclaimContinuation(candles, index, indicators, interval, atrValue, context = {}) {
    const empty = {
      qualified: false,
      mode: null,
      structureStartIndex: null,
      breachStartIndex: null,
      lastBelowIndex: null,
      reclaimIndex: null,
      breachBars: 0,
      belowEmaBars: 0,
      breachSpanBars: 0,
      recoveryBars: 0,
      deepestBreachAtr: 0,
      deepestBreachPercent: 0,
      postReclaimAboveRatio: 0,
      nestedAscendingTrap: false,
      evidence: [],
    };
    if (!["5m", "15m"].includes(interval) || index < 48) return empty;
    const consolidationBars = Number(context.consolidationBars) || 0;
    const structureStartIndex = clamp(
      Math.trunc(Number(context.structureStartIndex)),
      0,
      Math.max(0, index - 1),
    );
    const matureStructure = context.matureStructure === true
      && consolidationBars >= 28
      && Number(context.structureQuality || 0) >= 0.52;
    const priorAdvanceAtr = Number(context.priorAdvanceAtr) || 0;
    if (!matureStructure
      || context.hasPriorAdvance !== true
      || priorAdvanceAtr < 2
      || context.postSelloffRecovery === true
      || context.strictMotherRisk === true) return { ...empty, structureStartIndex };

    const priorIndex = index - 1;
    const emaNow = indicators.ema90[priorIndex];
    const emaReferenceIndex = Math.max(structureStartIndex, priorIndex - 24);
    const emaReference = indicators.ema90[emaReferenceIndex];
    if (!Number.isFinite(emaNow)
      || !Number.isFinite(emaReference)
      || emaNow <= emaReference + atrValue * 0.04) return { ...empty, structureStartIndex };

    const scanStartIndex = Math.max(
      structureStartIndex,
      index - Math.min(120, Math.max(48, consolidationBars + 18)),
    );
    const deviations = [];
    for (let cursor = scanStartIndex; cursor < index; cursor += 1) {
      const emaValue = indicators.ema90[cursor];
      const localAtr = Math.max(indicators.atr[cursor] || atrValue, atrValue * 0.45, 1e-8);
      if (!Number.isFinite(emaValue)) continue;
      deviations.push({
        index: cursor,
        deviationAtr: (candles[cursor].close - emaValue) / localAtr,
      });
    }
    const meaningfulBreaches = deviations.filter((item) => item.deviationAtr <= -0.2);
    if (meaningfulBreaches.length < 2) return { ...empty, structureStartIndex };
    const deepestBreach = meaningfulBreaches.reduce((worst, item) => (
      item.deviationAtr < worst.deviationAtr ? item : worst
    ));
    const deepestBreachAtr = deepestBreach.deviationAtr;
    const deepestEma = indicators.ema90[deepestBreach.index];
    const deepestBreachPercent = Number.isFinite(deepestEma) && deepestEma > 0
      ? (candles[deepestBreach.index].close / deepestEma - 1) * 100
      : 0;
    const breachStartIndex = meaningfulBreaches[0].index;
    const lastBelow = deviations.filter((item) => item.deviationAtr < -0.04).at(-1);
    if (!lastBelow) return { ...empty, structureStartIndex };
    const reclaimIndex = lastBelow.index + 1;
    const belowEmaBars = deviations.filter((item) => (
      item.index >= breachStartIndex
      && item.index < reclaimIndex
      && item.deviationAtr < -0.04
    )).length;
    const breachSpanBars = reclaimIndex - breachStartIndex;
    const recoveryBars = index - reclaimIndex;
    const maximumBreachSpan = interval === "5m" ? 24 : 18;
    const shallowAndQuickRepair = deepestBreachAtr >= -1.8
      && deepestBreachPercent >= -5
      && belowEmaBars <= maximumBreachSpan
      && breachSpanBars <= maximumBreachSpan;
    if (!shallowAndQuickRepair || recoveryBars < 8 || recoveryBars > 48) {
      return {
        ...empty,
        structureStartIndex,
        breachStartIndex,
        lastBelowIndex: lastBelow.index,
        reclaimIndex,
        breachBars: meaningfulBreaches.length,
        belowEmaBars,
        breachSpanBars,
        recoveryBars,
        deepestBreachAtr,
        deepestBreachPercent,
      };
    }

    const postReclaim = deviations.filter((item) => item.index >= reclaimIndex);
    const postReclaimAbove = postReclaim.filter((item) => item.deviationAtr >= -0.04).length;
    const postReclaimAboveRatio = postReclaimAbove / Math.max(postReclaim.length, 1);
    const terminalStable = postReclaim.slice(-Math.min(6, postReclaim.length))
      .every((item) => item.deviationAtr >= 0);
    const reclaimEma = indicators.ema90[reclaimIndex];
    const emaAdvancedAfterReclaim = Number.isFinite(reclaimEma)
      && emaNow >= reclaimEma + atrValue * 0.12;
    if (postReclaimAboveRatio < 0.82 || !terminalStable || !emaAdvancedAfterReclaim) {
      return {
        ...empty,
        structureStartIndex,
        breachStartIndex,
        lastBelowIndex: lastBelow.index,
        reclaimIndex,
        breachBars: meaningfulBreaches.length,
        belowEmaBars,
        breachSpanBars,
        recoveryBars,
        deepestBreachAtr,
        deepestBreachPercent,
        postReclaimAboveRatio,
      };
    }

    const trap = context.ascendingStructureTrap || null;
    // 局部上升斜线只要明确晚于 EMA90 修复母结构，就不应覆盖整个
    // 箱体。PI 5m 的真实母结构比末端内切线早 6 根；固定 8 根会把
    // 这种已经人工确认的 EMA90 收复再启动误杀。
    const minimumLead = Math.max(5, Math.round(consolidationBars * 0.12));
    const nestedAscendingTrap = Boolean(trap)
      && structureStartIndex <= Number(trap.startIndex) - minimumLead
      && Number(trap.bars || 0) <= Math.max(42, consolidationBars * 0.68);
    if (trap && !nestedAscendingTrap) {
      return {
        ...empty,
        structureStartIndex,
        breachStartIndex,
        lastBelowIndex: lastBelow.index,
        reclaimIndex,
        breachBars: meaningfulBreaches.length,
        belowEmaBars,
        breachSpanBars,
        recoveryBars,
        deepestBreachAtr,
        deepestBreachPercent,
        postReclaimAboveRatio,
      };
    }

    return {
      qualified: true,
      mode: "ema90-reclaim-continuation",
      structureStartIndex,
      breachStartIndex,
      lastBelowIndex: lastBelow.index,
      reclaimIndex,
      breachBars: meaningfulBreaches.length,
      belowEmaBars,
      breachSpanBars,
      recoveryBars,
      deepestBreachAtr,
      deepestBreachPercent,
      postReclaimAboveRatio,
      nestedAscendingTrap,
      evidence: [
        `EMA90快速修复：均线下方停留 ${belowEmaBars} 根、历时 ${breachSpanBars} 根，最深收盘偏离 ${Math.abs(deepestBreachAtr).toFixed(2)} ATR / ${Math.abs(deepestBreachPercent).toFixed(2)}%`,
        `重新站回后已稳定 ${recoveryBars} 根，${Math.round(postReclaimAboveRatio * 100)}% 收盘守在EMA90上方且均线继续上行`,
        `前置独立拉升 ${priorAdvanceAtr.toFixed(2)} ATR，最终由成熟${context.structureLabel || "盘整结构"}外沿触发`,
        ...(nestedAscendingTrap ? ["末端局部上升斜线只是大结构内部拟合，不覆盖完整蓄力区间，取消其一票否决"] : []),
      ],
    };
  }

  function assessHighLevelDistribution(candles, index, indicators, structure, atrValue) {
    if (!structure || index < 45) return { risky: false, score: 0 };
    const lineStart = structure.triangleLines?.upper?.startIndex
      ?? structure.trendline?.startIndex
      ?? Math.max(0, index - (structure.consolidationBars || 40));
    const start = Math.max(1, lineStart);
    const beforeStart = Math.max(0, start - 32);
    const window = candles.slice(start, index);
    const baseline = candles.slice(beforeStart, start);
    if (window.length < 24 || baseline.length < 8) return { risky: false, score: 0 };

    let maxExtensionAtr = 0;
    let shockBars = 0;
    let disagreementBars = 0;
    window.forEach((row, offset) => {
      const absoluteIndex = start + offset;
      const localAtr = Math.max(indicators.atr[absoluteIndex] || atrValue, 1e-8);
      const localEma = indicators.ema90[absoluteIndex];
      if (Number.isFinite(localEma)) maxExtensionAtr = Math.max(maxExtensionAtr, (row.high - localEma) / localAtr);
      const rangeAtr = (row.high - row.low) / localAtr;
      const bodyAtr = Math.abs(row.close - row.open) / localAtr;
      const upperWickAtr = (row.high - Math.max(row.open, row.close)) / localAtr;
      if (rangeAtr >= 2.35) shockBars += 1;
      if ((row.close < row.open && bodyAtr >= 1.05) || upperWickAtr >= 1.15) disagreementBars += 1;
    });
    const peak = Math.max(...window.map((row) => row.high));
    const origin = Math.max(mean(baseline.slice(-8).map((row) => row.close)), 1e-8);
    const runupPercent = Math.max(0, peak / origin - 1) * 100;
    const tail = window.slice(-Math.min(32, window.length));
    const tailRangeAtr = (Math.max(...tail.map((row) => row.high)) - Math.min(...tail.map((row) => row.low))) / atrValue;
    const baselineVolume = mean(baseline.map((row) => row.volume));
    const divergenceVolumeRatio = mean(window.filter((row) => (
      (row.high - row.low) / atrValue >= 1.6
    )).map((row) => row.volume)) / Math.max(baselineVolume, 1e-8);
    const flags = [
      runupPercent >= 18,
      maxExtensionAtr >= 6,
      shockBars >= 3,
      disagreementBars >= 3,
      tailRangeAtr >= 6.5,
      divergenceVolumeRatio >= 1.35,
    ];
    const score = flags.filter(Boolean).length;
    const risky = flags[0] && flags[1] && score >= 5;
    return {
      risky,
      score,
      runupPercent,
      maxExtensionAtr,
      shockBars,
      disagreementBars,
      tailRangeAtr,
      divergenceVolumeRatio,
    };
  }

  function findCandidates(candles, index, indicators, options = {}) {
    const priorIndex = index - 1;
    const atrValue = Math.max(indicators.atr[priorIndex] || candles[priorIndex].high - candles[priorIndex].low, 1e-8);
    const current = candles[index];
    const rightEdge = Boolean(options.rightEdge);
    const recentHigh = Math.max(...candles.slice(Math.max(0, index - 4), index).map((row) => row.high));
    const baseHigh = Math.max(...candles.slice(Math.max(0, index - 18), index).map((row) => row.high));
    const localBreakPotential = current.high >= recentHigh + atrValue * 0.015 || current.open >= recentHigh;
    const baseBreakPotential = current.high >= baseHigh + atrValue * 0.02 || current.open >= baseHigh;
    const shortBase = baseBreakPotential || rightEdge
      ? detectHorizontalBase(candles, index, atrValue, 18)
      : null;
    const motherBaseCandidate = baseBreakPotential || rightEdge
      ? detectHorizontalBase(candles, index, atrValue, 28)
      : null;
    const compactOneHourPlatform = options.interval === "1h";
    const rawOuterPlatformCandidate = baseBreakPotential || rightEdge
      ? detectOuterPlatform(candles, index, atrValue, compactOneHourPlatform ? 12 : 18, 192, {
        compactOneHour: compactOneHourPlatform,
      })
      : null;
    const truePriorHighResolution = resolveTruePriorHighBoundary(
      candles,
      index,
      rawOuterPlatformCandidate,
      atrValue,
    );
    const outerPlatformCandidate = truePriorHighResolution.component;
    const truePriorHighContext = truePriorHighResolution.context;
    const brokenOuterPlatformContext = baseBreakPotential || rightEdge
      ? detectBrokenOuterPlatformContext(candles, index, atrValue, 18, 192)
      : null;
    // 急拉后急杀形成的母箱体，真正的执行边界是急杀前的母压力，而不是
    // 箱体末端六七根K线里的局部小前高。这里在当前K开始前只使用历史K线
    // 解析母箱体；当前K仅负责判断是否从线下穿越母压力，因此不会使用
    // 突破后的结果倒推结构。PEOPLE 2024-06-01 16:00 属于这一类。
    const motherBoundaryAssessment = baseBreakPotential || rightEdge
      ? assessMotherStructureNoise(
        candles,
        index,
        candles[priorIndex].close,
        atrValue,
        { interval: options.interval || "5m", consolidationBars: 0 },
      )
      : { risky: false };
    const motherBoundaryLaunchDistance = motherBoundaryAssessment.motherHigh > 0
      ? Math.max(0, motherBoundaryAssessment.motherHigh / Math.max(current.open, 1e-8) - 1) * 100
      : 99;
    const shockMotherBoxOuterEdge = motherBoundaryAssessment.risky === true
      && motherBoundaryAssessment.mode === "shock-formed-mother-box"
      && (motherBoundaryAssessment.postPeakBars || 0) >= 24
      && (motherBoundaryAssessment.position || 0) >= 0.76
      && motherBoundaryAssessment.stillInsideMotherEdge === true
      && current.open < motherBoundaryAssessment.motherHigh
      && current.high >= motherBoundaryAssessment.motherHigh
      && motherBoundaryLaunchDistance <= 7
      ? {
        type: "base",
        level: motherBoundaryAssessment.motherHigh,
        stop: Math.min(...candles.slice(Math.max(0, index - 12), index).map((row) => row.low)),
        quality: clamp(
          0.72
            + Math.min(0.12, (motherBoundaryAssessment.postPeakBars || 0) / 500)
            + Math.min(0.1, (motherBoundaryAssessment.upperVisits || 0) * 0.015),
          0,
          0.94,
        ),
        consolidationBars: motherBoundaryAssessment.postPeakBars,
        pressureBars: candles.slice(Math.max(0, index - 8), index)
          .filter((row) => row.high <= motherBoundaryAssessment.motherHigh).length,
        rangeCompression: compressionRatio(candles, index),
        edgeReady: true,
        outerEdgeConfirmed: true,
        outerEdgeScore: Math.min(99, 86 + Math.min(10, motherBoundaryAssessment.upperVisits || 0)),
        clusteredCeilingBand: true,
        ceilingTouches: Math.max(2, motherBoundaryAssessment.upperVisits || 0),
        touchGroups: Math.max(2, Math.min(6, motherBoundaryAssessment.upperVisits || 0)),
        ceilingAge: motherBoundaryAssessment.postPeakBars,
        structureStartIndex: motherBoundaryAssessment.shockPeakIndex,
        platformModel: "shock-mother-box-outer-edge",
        launchDistancePercent: motherBoundaryLaunchDistance,
        shockMotherBoxOuterEdge: true,
        evidence: [
          `急杀母箱体真正上沿已在当前K开始前确定为 ${motherBoundaryAssessment.motherHigh.toFixed(8)}`,
          `急杀后已经在母压力下方轮动 ${motherBoundaryAssessment.postPeakBars} 根；不再用末端局部小前高替代母边界`,
          `突破K开盘到母压力 ${motherBoundaryLaunchDistance.toFixed(2)}%，未超过7%`,
        ],
      }
      : null;
    // 能被当前 K 线穿越的多个平台中，优先使用最高的母平台外沿。这样局部阳线
    // 即使与趋势线共振，也不能抢走更上方盘整前高的执行权。
    const baseCandidate = [shockMotherBoxOuterEdge, outerPlatformCandidate, motherBaseCandidate, shortBase]
      .filter(Boolean)
      .sort((a, b) => (
        b.level - a.level
        || Number(b.platformModel === "outer") - Number(a.platformModel === "outer")
        || (b.outerEdgeScore || 0) - (a.outerEdgeScore || 0)
        || b.consolidationBars - a.consolidationBars
      ))[0] || null;
    const strictBaseEdgeReady = Boolean(baseCandidate)
      && baseCandidate.pressureBars >= 2
      && baseCandidate.level - candles[priorIndex].close <= atrValue * 1.3
      && baseCandidate.rangeCompression <= 1.6;
    const outerPlatformEdgeReady = (Boolean(baseCandidate?.platformModel === "outer")
        && baseCandidate.outerEdgeScore >= 62
        && baseCandidate.launchDistancePercent <= 7)
      || baseCandidate?.shockMotherBoxOuterEdge === true;
    const baseEdgeReady = strictBaseEdgeReady || outerPlatformEdgeReady;
    const executableBase = baseCandidate ? {
      ...baseCandidate,
      edgeReady: baseEdgeReady,
      outerEdgeConfirmed: baseEdgeReady,
    } : null;
    // 只有价格已经在成熟箱体外沿蓄力时，箱体上沿才取得触发价控制权。
    // 若只是较宽的背景盘整，仍可提供结构背景，但不能压过更贴近价格的有效触发结构。
    const motherBase = motherBaseCandidate && baseEdgeReady ? executableBase : null;
    const structureOptions = {
      interval: options.interval || "5m",
      newCoinNotFalling: options.newCoinNotFalling === true,
    };
    // 1分钟只承担精确执行，并且用户已明确限定为横盘起飞 / 箱体突破。
    // 三角、楔形与趋势线即使最后不会成为买点，过去仍可能进入 structures
    // 作为白色预确认线显示，造成“盘整区间里瞎画”的视觉杂音。这里从识别
    // 源头关闭 1分钟斜线结构；5分钟及以上仍完整保留结构识别。
    const allowDiagonalStructure = structureOptions.interval !== "1m";
    const trendlineCandidate = allowDiagonalStructure && (localBreakPotential || rightEdge)
      ? detectDescendingTrendline(candles, index, atrValue, structureOptions)
      : null;
    let executableTrendline = trendlineCandidate
      && trendlineCandidate.provisionalLongBoundary !== true
      && trendlineCandidate.quality >= 0.72
      && trendlineCandidate.consolidationBars >= 36
      && (trendlineCandidate.trendline?.touches || 0) >= 3
      && (trendlineCandidate.trendline?.adherence || 0) >= 0.88
      ? trendlineCandidate
      : null;
    const shortTriangle = allowDiagonalStructure
      ? detectTriangle(candles, index, atrValue, structureOptions)
      : null;
    const longConvergence = allowDiagonalStructure
      ? detectLongConvergence(candles, index, atrValue, trendlineCandidate, structureOptions)
      : null;
    let triangleCandidate = [shortTriangle, longConvergence]
      .filter(Boolean)
      .sort((a, b) => b.quality - a.quality)[0] || null;
    if (!triangleCandidate && allowDiagonalStructure && localBreakPotential) {
      triangleCandidate = recoverLongTriangleAfterSoftBoundaryTests(
        candles,
        index,
        indicators,
        structureOptions,
      );
    }
    const triangleUpperForLongBox = triangleCandidate?.triangleLines?.upper;
    const triangleLowerForLongBox = triangleCandidate?.triangleLines?.lower;
    const longSwingBoxBoundary = Number(triangleUpperForLongBox?.startPrice);
    // 长箱体有时只有两个相隔较远、但同属一段盘整的主要峰值；普通压力带
    // 聚类会因为触碰次数少而漏掉它。若整个区间上下沿都有真实触点、K线
    // 长时间在两条边界之间来回、没有大片空白，最终又由实体扩张阳线越过
    // 最早峰值，则把该峰值恢复为母平台外沿。后段斜线仅帮助界定区间，
    // 不取得“三角/趋势线”显示权。WLD 2024-02-18 09:00 即属此类。
    const longSwingMotherBox = !executableBase?.edgeReady
      && ["5m", "15m", "1h"].includes(structureOptions.interval)
      && triangleCandidate?.structureShape === "converging-triangle"
      && (triangleCandidate.consolidationBars || 0) >= 40
      && (triangleCandidate.quality || 0) >= 0.58
      && (triangleCandidate.channelInteriorOccupancy || 0) >= 0.68
      && (triangleCandidate.channelMiddleParticipationRatio || 0) >= 0.5
      && (triangleCandidate.channelHollowRatio || 0) <= 0.58
      && (triangleCandidate.channelLongestHollowRun || 99) <= 12
      && (triangleCandidate.channelSideTransitions || 0) >= 2
      && (triangleUpperForLongBox?.touchGroups || 0) >= 2
      && (triangleLowerForLongBox?.touchGroups || 0) >= 2
      && Number.isFinite(longSwingBoxBoundary)
      && triangleUpperForLongBox.endPrice <= longSwingBoxBoundary + atrValue * 0.12
      && current.open < longSwingBoxBoundary
      && current.high >= longSwingBoxBoundary
      && current.close >= longSwingBoxBoundary + atrValue * 0.2
      && current.close - current.open >= atrValue * 1.15
      && (longSwingBoxBoundary / Math.max(current.open, 1e-8) - 1) * 100 <= 7
      ? {
        type: "base",
        level: longSwingBoxBoundary,
        stop: Math.min(
          ...candles.slice(triangleUpperForLongBox.startIndex, index).map((row) => row.low),
        ),
        quality: Math.max(0.62, triangleCandidate.quality),
        consolidationBars: triangleCandidate.consolidationBars,
        pressureBars: 2,
        rangeCompression: 1,
        edgeReady: true,
        outerEdgeConfirmed: true,
        // 这不是给普通双峰加分；上面的完整区间占用、空腔、触点、换边和
        // 实体突破已经共同完成严格审计，因此外沿可直接按成熟母平台评级。
        outerEdgeScore: Math.max(86, Math.round((triangleCandidate.quality || 0) * 100 + 14)),
        clusteredCeilingBand: false,
        ceilingTouches: 2,
        touchGroups: 2,
        ceilingAge: Math.max(3, Math.round((triangleCandidate.consolidationBars || 40) * 0.2)),
        structureStartIndex: triangleUpperForLongBox.startIndex,
        platformModel: "long-swing-outer-edge",
        launchDistancePercent: Math.max(0, longSwingBoxBoundary / Math.max(current.open, 1e-8) - 1) * 100,
        longSwingMotherBox: true,
        evidence: [
          `长盘整真实外沿取首个结构峰值 ${longSwingBoxBoundary.toFixed(8)}，不取末端动态斜线`,
          `区间持续 ${triangleCandidate.consolidationBars} 根，上下沿各至少2组触点，内部占用 ${Math.round((triangleCandidate.channelInteriorOccupancy || 0) * 100)}%`,
          "最终阳线以放大实体从线下越过真实箱体前高，归类为盘整突破",
        ],
      }
      : null;
    const distributionAssessment = assessHighLevelDistribution(
      candles,
      index,
      indicators,
      triangleCandidate || executableTrendline,
      atrValue,
    );
    // 高位大分歧即使局部能拟合斜线，也不是可交易的蓄势结构。趋势线只是辅助，
    // 这里直接撤掉三角/楔形和趋势线组件，避免派发区出现成组杂线与错误共振。
    if (distributionAssessment.risky) {
      executableTrendline = null;
      triangleCandidate = null;
    }
    const ascendingStructureTrap = baseBreakPotential || rightEdge
      ? detectAscendingChannelTrap(
        candles,
        index,
        atrValue,
        24,
        120,
        { requireUpperTest: !rightEdge },
      )
      : null;
    // 完整小周期可能有数万根 K 线。箱体、回踩和拐点只可能在局部高点被穿越时成交，
    // 因而先做这个严格必要条件筛选；下降三角的上沿会移动，仍逐根检测，避免漏点。
    const raw = [
      localBreakPotential || rightEdge ? detectPullbackRelaunch(candles, index, atrValue, indicators) : null,
      triangleCandidate,
      executableTrendline,
      executableBase,
      // 同类型组件按后者覆盖；严格长箱体证据成立时，应覆盖普通短平台的
      // 未成熟结果，而不是反过来被短平台抹掉。
      longSwingMotherBox,
      localBreakPotential || rightEdge ? detectPivotReclaim(candles, index, atrValue, indicators.ema90[priorIndex]) : null,
      localBreakPotential || rightEdge ? detectPreviousHigh(candles, index, atrValue) : null,
    ].filter(Boolean).map((item) => ({ ...item, triggerPrice: item.level + atrValue * 0.04 }));
    if (!raw.length) return [];

    const priorClose = candles[priorIndex].close;
    const tolerance = Math.max(atrValue * 0.9, priorClose * 0.0025);
    const clusters = raw.map((anchor) => {
      const components = raw.filter((item) => Math.abs(item.level - anchor.level) <= tolerance);
      const unique = [...new Map(components.map((item) => [item.type, item])).values()];
      const foundationComponents = unique.filter((item) => FOUNDATION_PATTERNS.has(item.type));
      const auxiliaryComponents = unique.filter((item) => AUXILIARY_PATTERNS.has(item.type));
      const pivotComponent = unique.find((item) => item.type === "pivot") || null;
      const componentLevels = unique.map((item) => item.level).sort((a, b) => a - b);
      const clusteredBase = unique.find((item) => item.type === "base") || null;
      const clusteredPreviousHigh = unique.find((item) => item.type === "previousHigh") || null;
      const triangleStructure = unique.find((item) => item.type === "triangle") || null;
      // 识别到横盘母结构后，执行价必须是箱体上沿；趋势线、局部前高和附近阳线只作辅助。
      // 没有箱体时，其他结构共振仍使用中位线，避免被最远的弱压力位拖到追高位置。
      let level = clusteredBase?.edgeReady
        ? clusteredBase.level
        : componentLevels[Math.floor(componentLevels.length / 2)];
      const triangleUpper = triangleStructure?.triangleLines?.upper;
      const triangleLower = triangleStructure?.triangleLines?.lower;
      const matureTriangleDirectBoundary = Boolean(triangleStructure)
        && (triangleStructure.consolidationBars || 0) >= 28
        && (triangleStructure.quality || 0) >= 0.68
        && (triangleUpper?.touchGroups || 0) >= 2
        && (triangleLower?.touchGroups || 0) >= 2
        && (triangleStructure.channelInteriorOccupancy || 0) >= 0.5;
      // 三角动态上轨与盘整真前高属于同一触发簇时，执行价必须取更高的真前高。
      // 这样第一次只碰动态线的试盘不会提前出现绿色 B；等后续阳线真正越过
      // 盘整前高时才执行。没有独立前高的三角仍保持“突破上轨即买”。
      const emaAtBoundary = indicators.ema90[priorIndex];
      const priorEmaAtBoundary = indicators.ema90[Math.max(0, priorIndex - 1)];
      const strongTrendAtBoundary = Number.isFinite(emaAtBoundary)
        && candles[priorIndex].close > emaAtBoundary
        && (!Number.isFinite(priorEmaAtBoundary) || emaAtBoundary >= priorEmaAtBoundary);
      if (!clusteredBase?.edgeReady
        && matureTriangleDirectBoundary
        && clusteredPreviousHigh
        && strongTrendAtBoundary) {
        level = Math.max(level, clusteredPreviousHigh.level);
      }
      // PI 2025-02-26 10:00 一类长盘整在真正边界处已经完成全部结构确认。
      // 这时再把执行价抬高 0.04 ATR，会把“突破即买”机械延迟到下一根 K 线。
      // 精确边界只开放给成熟三角，或 1 小时长平台的拐点+真前高共振；
      // 普通趋势线、附近阳线和短周期局部高点仍保留缓冲，避免全局增加杂音。
      const oneHourPlatformPivotBoundary = options.interval === "1h"
        && Boolean(clusteredBase)
        && Boolean(pivotComponent)
        && Boolean(clusteredPreviousHigh)
        && (clusteredBase.consolidationBars || 0) >= 36
        && (clusteredBase.quality || 0) >= 0.46
        && (clusteredBase.launchDistancePercent ?? 99) <= 7;
      const directStructuralBoundary = matureTriangleDirectBoundary
        || oneHourPlatformPivotBoundary;
      const triggerPrice = directStructuralBoundary ? level : level + atrValue * 0.04;
      const confluence = unique
        .sort((a, b) => PATTERN_PRIORITY[b.type] - PATTERN_PRIORITY[a.type])
        .map((item) => item.type);
      const primaryPool = foundationComponents.length
        ? foundationComponents
        : pivotComponent ? [pivotComponent] : unique;
      const primary = primaryPool.sort((a, b) => (
        PATTERN_PRIORITY[b.type] + b.quality * 2 - PATTERN_PRIORITY[a.type] - a.quality * 2
      ))[0];
      const validStops = unique.map((item) => item.stop).filter((value) => value > 0 && value < level);
      const stop = validStops.length ? Math.max(...validStops) : level - atrValue * 2;
      const foundationQuality = mean(foundationComponents.map((item) => item.quality));
      const supportingQuality = mean(unique
        .filter((item) => !FOUNDATION_PATTERNS.has(item.type))
        .map((item) => item.quality));
      const quality = foundationComponents.length
        ? foundationQuality * 0.85 + supportingQuality * 0.15
        : mean(unique.map((item) => item.quality));
      const trendline = unique.find((item) => item.type === "trendline")?.trendline || null;
      const triangleLines = triangleStructure?.triangleLines || null;
      const crossedLevel = candles[index].high >= triggerPrice;
      const triggered = candles[index].open < triggerPrice && crossedLevel;
      const openedBeyondTrigger = candles[index].open >= triggerPrice;
      const distance = Math.max(0, level - priorClose) / atrValue;
      const triangleStartIndex = triangleLines?.structureStartIndex
        ?? triangleLines?.upper?.structureStartIndex
        ?? triangleLines?.upper?.startIndex
        ?? index;
      const motherStartIndex = motherBase?.structureStartIndex
        ?? index - (motherBase?.consolidationBars || 0);
      const triangleTouchesReady = (triangleLines?.upper?.touchGroups || 0) >= 2
        && (triangleLines?.lower?.touchGroups || 0) >= 2;
      const triangleCoversMotherStructure = Boolean(triangleStructure && motherBase)
        && triangleStartIndex <= motherStartIndex + Math.max(3, Math.round((motherBase.consolidationBars || 0) * 0.15))
        && triangleStructure.consolidationBars >= Math.max(18, (motherBase.consolidationBars || 0) * 0.68);
      // 上升三角与对称三角都以自身成熟外沿为执行边界。若两条边界覆盖的正是
      // 母盘整主体，而非末端临时画出的局部内切线，就允许先突破动态上沿成交；
      // 普通局部阳线和短趋势线仍必须等待真正的盘整前高。
      const matureTriangleBoundary = triangleCoversMotherStructure
        && triangleStructure.quality >= 0.68
        && triangleTouchesReady
        && (triangleStructure.channelInteriorOccupancy || 0) >= 0.45
        && (triangleStructure.channelSideTransitions || 0) >= 2;
      const blockedTruePriorHigh = Boolean(truePriorHighContext?.blocked)
        && level < truePriorHighContext.level - tolerance * 0.2;
      const insideMotherBase = (Boolean(motherBase)
          && !clusteredBase
          && !matureTriangleBoundary
          && level < motherBase.level - tolerance * 0.45)
        || blockedTruePriorHigh;
      const terminalAscendingTrap = ascendingStructureTrap && (
        // 执行阶段：候选价必须正处在上轨附近，才属于上升通道/上楔末端追高。
        (level >= ascendingStructureTrap.upperAtTrigger - Math.max(atrValue * 0.9, tolerance)
          && level <= ascendingStructureTrap.upperAtTrigger + Math.max(atrValue * 1.25, tolerance * 1.2))
        // 观察阶段：只要所谓“横盘/三角”价位仍落在完整斜向包络内部，就不能
        // 先给出正向结构预确认；否则 BASED 这类尚未触碰上轨的上楔仍会漏网。
        || (rightEdge
          && !crossedLevel
          && level >= ascendingStructureTrap.lowerAtTrigger - Math.max(atrValue * 0.6, tolerance * 0.6)
          && level <= ascendingStructureTrap.upperAtTrigger + Math.max(atrValue * 0.6, tolerance * 0.6))
      ) ? ascendingStructureTrap : null;
      const relevantBrokenOuterPlatform = brokenOuterPlatformContext
        && Math.abs(level - brokenOuterPlatformContext.level) <= tolerance
        ? brokenOuterPlatformContext
        : null;
      const clusterScore = foundationComponents.length * 22
        + Number(Boolean(pivotComponent)) * 5
        + auxiliaryComponents.length * 2
        + quality * 20
        + PATTERN_PRIORITY[primary.type] * 3
        - distance * 4;
      return {
        ...primary,
        // 同一买点同时有短横盘和更完整的三角/回踩时，节奏成熟度
        // 应由最完整的因果结构控制，不能因为存在一个 21 根小箱体
        // 就把已经走了 36 根的上升三角缩短为 21 根。
        consolidationBars: Math.max(0, ...foundationComponents.map((item) => item.consolidationBars || 0))
          || primary.consolidationBars,
        level,
        triggerPrice,
        stop,
        quality,
        triggered,
        crossedLevel,
        openedBeyondTrigger,
        confluence,
        components: unique,
        componentLevels,
        foundationTypes: foundationComponents.map((item) => item.type),
        auxiliaryTypes: auxiliaryComponents.map((item) => item.type),
        hasPivot: Boolean(pivotComponent),
        structuredPivot: pivotComponent?.structuredPivot === true,
        pivotStructureStartIndex: pivotComponent?.structureStartIndex ?? null,
        pivotLowIndex: pivotComponent?.pivotLowIndex ?? null,
        pivotRetracementRatio: pivotComponent?.retracementRatio ?? null,
        pivotPriorAdvanceAtr: pivotComponent?.priorAdvanceAtr ?? null,
        insideMotherBase,
        matureTriangleBoundary,
        directStructuralBoundary,
        oneHourPlatformPivotBoundary,
        motherBaseLevel: blockedTruePriorHigh
          ? truePriorHighContext.level
          : motherBase?.level || null,
        truePriorHighContext,
        brokenOuterPlatformContext: relevantBrokenOuterPlatform,
        ascendingStructureTrap: terminalAscendingTrap,
        trendline,
        triangleLines,
        structureShape: triangleStructure?.structureShape || null,
        channelInteriorOccupancy: triangleStructure?.channelInteriorOccupancy ?? null,
        channelMiddleParticipationRatio: triangleStructure?.channelMiddleParticipationRatio ?? null,
        channelHollowRatio: triangleStructure?.channelHollowRatio ?? null,
        channelLongestHollowRun: triangleStructure?.channelLongestHollowRun ?? null,
        channelSideTransitions: triangleStructure?.channelSideTransitions ?? null,
        clusterScore,
        evidence: [
          ...primary.evidence,
          confluence.length > 1
            ? `多逻辑共振：${confluence.map((type) => PATTERN_LABELS[type]).join(" + ")}`
            : `单结构预备：${PATTERN_LABELS[primary.type]}`,
        ],
      };
    });
    // 普通聚类只合并约 0.9 ATR 内的价位，这能压住附近阳线噪声，却会把
    // “三角动态上轨”和“更高的盘整真实前高”拆成两笔。PI 2025-02-26
    // 10:00 正是同一根1小时K线连续穿过这两级边界：只要成熟三角已经在
    // 真正母平台内部完成、两级边界都在突破K开盘上方且总距离不超过7%，
    // 就以更高的母平台前高作为唯一执行价，不能让较低上轨先占用买点名额。
    const triangleForOuterEdge = triangleCandidate;
    const baseForOuterEdge = executableBase;
    if (triangleForOuterEdge
      && baseForOuterEdge
      && triangleForOuterEdge.quality >= 0.68
      && triangleForOuterEdge.consolidationBars >= 28
      && (triangleForOuterEdge.channelInteriorOccupancy || 0) >= 0.55
      && (triangleForOuterEdge.channelSideTransitions || 0) >= 3
      && (triangleForOuterEdge.triangleLines?.upper?.touchGroups || 0) >= 2
      && (triangleForOuterEdge.triangleLines?.lower?.touchGroups || 0) >= 2
      && baseForOuterEdge.level > triangleForOuterEdge.level + atrValue * 0.15
      && current.open < triangleForOuterEdge.level + atrValue * 0.04
      && current.open < baseForOuterEdge.level + atrValue * 0.04
      && current.high >= baseForOuterEdge.level + atrValue * 0.04
      && (baseForOuterEdge.level / Math.max(current.open, 1e-8) - 1) * 100 <= 7) {
      const derivedTrendline = executableTrendline || {
        type: "trendline",
        level: triangleForOuterEdge.level,
        stop: triangleForOuterEdge.stop,
        quality: triangleForOuterEdge.quality,
        trendline: triangleForOuterEdge.triangleLines.upper,
        evidence: ["成熟三角上轨作为动态趋势线辅助，真正盘整前高控制最终执行价"],
      };
      const truePreviousHigh = {
        type: "previousHigh",
        level: baseForOuterEdge.level,
        stop: baseForOuterEdge.stop,
        quality: Math.max(baseForOuterEdge.quality || 0, 0.68),
        evidence: ["突破三角后继续穿越同一母盘整的真正前高"],
      };
      const pivotForOuterEdge = raw.find((item) => item.type === "pivot") || {
        type: "pivot",
        level: triangleForOuterEdge.level,
        stop: triangleForOuterEdge.stop,
        quality: triangleForOuterEdge.quality,
        evidence: ["成熟三角末端由收敛转为向上越过上轨，确认结构拐点"],
      };
      const components = [
        triangleForOuterEdge,
        baseForOuterEdge,
        derivedTrendline,
        truePreviousHigh,
        pivotForOuterEdge,
      ].filter(Boolean);
      const unique = [...new Map(components.map((item) => [item.type, item])).values()];
      const confluence = unique
        .sort((a, b) => PATTERN_PRIORITY[b.type] - PATTERN_PRIORITY[a.type])
        .map((item) => item.type);
      const level = baseForOuterEdge.level;
      const triggerPrice = level;
      const validStops = unique.map((item) => item.stop).filter((value) => value > 0 && value < level);
      clusters.push({
        ...triangleForOuterEdge,
        type: "triangle",
        level,
        triggerPrice,
        stop: validStops.length ? Math.max(...validStops) : level - atrValue * 2,
        quality: triangleForOuterEdge.quality * 0.88 + Math.max(baseForOuterEdge.quality || 0, 0.5) * 0.12,
        triggered: current.open < triggerPrice && current.high >= triggerPrice,
        crossedLevel: current.high >= triggerPrice,
        openedBeyondTrigger: current.open >= triggerPrice,
        confluence,
        components: unique,
        componentLevels: unique.map((item) => item.level).sort((a, b) => a - b),
        foundationTypes: unique.filter((item) => FOUNDATION_PATTERNS.has(item.type)).map((item) => item.type),
        auxiliaryTypes: unique.filter((item) => AUXILIARY_PATTERNS.has(item.type)).map((item) => item.type),
        hasPivot: true,
        insideMotherBase: false,
        matureTriangleBoundary: true,
        matureTriangleOuterEdge: true,
        directStructuralBoundary: true,
        oneHourPlatformPivotBoundary: false,
        motherBaseLevel: level,
        brokenOuterPlatformContext: brokenOuterPlatformContext
          && Math.abs(level - brokenOuterPlatformContext.level) <= tolerance
          ? brokenOuterPlatformContext
          : null,
        ascendingStructureTrap: null,
        trendline: derivedTrendline.trendline,
        triangleLines: triangleForOuterEdge.triangleLines,
        structureShape: triangleForOuterEdge.structureShape,
        channelInteriorOccupancy: triangleForOuterEdge.channelInteriorOccupancy,
        channelMiddleParticipationRatio: triangleForOuterEdge.channelMiddleParticipationRatio,
        channelHollowRatio: triangleForOuterEdge.channelHollowRatio,
        channelLongestHollowRun: triangleForOuterEdge.channelLongestHollowRun,
        channelSideTransitions: triangleForOuterEdge.channelSideTransitions,
        consolidationBars: Math.max(
          triangleForOuterEdge.consolidationBars || 0,
          baseForOuterEdge.consolidationBars || 0,
        ),
        clusterScore: 140 + confluence.length * 4,
        evidence: [
          ...triangleForOuterEdge.evidence,
          `动态三角上轨 ${triangleForOuterEdge.level.toFixed(8)} 与母盘整前高 ${level.toFixed(8)} 同K依次突破`,
          `突破K开盘到真正前高 ${((level / Math.max(current.open, 1e-8) - 1) * 100).toFixed(2)}%，未超过7%`,
          `多逻辑共振：${confluence.map((type) => PATTERN_LABELS[type]).join(" + ")}`,
        ],
      });
    }
    const uniqueClusters = [...new Map(clusters.map((item) => [
      `${Math.round(item.level / tolerance)}-${item.confluence.join("+")}`,
      item,
    ])).values()];
    // 预备单优先级必须在当前 K 线开始前固定；current.high 只判断是否成交，不能反过来挑结构。
    return uniqueClusters.sort((a, b) => (
      b.clusterScore - a.clusterScore
      || b.confluence.length - a.confluence.length
    ));
  }

  function efficiencyRatio(candles, index, period = 14) {
    const start = Math.max(0, index - period);
    const net = Math.abs(candles[index].close - candles[start].close);
    let travelled = 0;
    for (let cursor = start + 1; cursor <= index; cursor += 1) {
      travelled += Math.abs(candles[cursor].close - candles[cursor - 1].close);
    }
    return travelled ? net / travelled : 0;
  }

  function candleRange(row) {
    return Math.max(row.high - row.low, 1e-8);
  }

  function compressionRatio(candles, index) {
    const recent = candles.slice(Math.max(0, index - 8), index).map(candleRange);
    const prior = candles.slice(Math.max(0, index - 20), Math.max(0, index - 8)).map(candleRange);
    if (recent.length < 6 || prior.length < 6) return 1;
    return mean(recent) / Math.max(mean(prior), 1e-8);
  }

  function motherStructureSpans(interval) {
    return ({
      "1m": [720, 480, 300, 180],
      "5m": [480, 300, 180, 120],
      "15m": [320, 240, 160, 100],
      "1h": [240, 160, 120, 80],
      "4h": [180, 120, 80, 60],
      "1d": [120, 80, 60],
    })[interval] || [240, 160, 120, 80];
  }

  function assessMotherStructureNoise(candles, index, level, atrValue, options = {}) {
    const interval = options.interval || "15m";
    const spans = motherStructureSpans(interval);
    const minimumSpan = Math.min(...spans);
    if (index < minimumSpan || !Number.isFinite(level) || level <= 0) return { risky: false };
    const localBars = Math.max(0, options.consolidationBars || 0);
    const assessments = spans
      .filter((span) => index >= span)
      .map((span) => {
        const rows = candles.slice(index - span, index);
        const rawMotherHigh = Math.max(...rows.map((row) => row.high));
        const rawMotherLow = Math.min(...rows.map((row) => row.low));
        // 大局观不能被左侧一根异常插针撑坏。母区间使用被多次交易过的稳健边界，
        // 原始极值仅保留作审计；这样 PI 15m 长平台中的小高点仍然属于母箱体内部，
        // 真正接近反复成交上沿的突破则不会被误杀。
        const robustHigh = quantile(rows.map((row) => Math.max(row.open, row.close, row.high)), 0.955);
        const robustLow = quantile(rows.map((row) => Math.min(row.open, row.close, row.low)), 0.045);
        const robustRange = Math.max(robustHigh - robustLow, 1e-8);
        const rawPeakIndex = rows.findIndex((row) => row.high === rawMotherHigh);
        const rawPeak = rows[rawPeakIndex];
        const rawPeakBodyTop = rawPeak ? Math.max(rawPeak.open, rawPeak.close) : robustHigh;
        const meaningfulRawPeak = rawMotherHigh - robustHigh <= Math.max(atrValue * 3, robustRange * 0.18)
          && rawMotherHigh - rawPeakBodyTop <= Math.max(atrValue * 2.5, robustRange * 0.12);
        const rawFloorIndex = rows.findIndex((row) => row.low === rawMotherLow);
        const rawFloor = rows[rawFloorIndex];
        const rawFloorBodyBottom = rawFloor ? Math.min(rawFloor.open, rawFloor.close) : robustLow;
        const meaningfulRawFloor = robustLow - rawMotherLow <= Math.max(atrValue * 3, robustRange * 0.18)
          && rawFloorBodyBottom - rawMotherLow <= Math.max(atrValue * 2.5, robustRange * 0.12);
        const motherHigh = meaningfulRawPeak ? rawMotherHigh : robustHigh;
        const motherLow = meaningfulRawFloor ? rawMotherLow : robustLow;
        const range = Math.max(motherHigh - motherLow, 1e-8);
        const closes = rows.map((row) => row.close);
        const travelled = closes.slice(1).reduce((sum, close, cursor) => (
          sum + Math.abs(close - closes[cursor])
        ), 0);
        const efficiency = Math.abs(closes.at(-1) - closes[0]) / Math.max(travelled, 1e-8);
        const middleLow = motherLow + range * 0.18;
        const middleHigh = motherHigh - range * 0.18;
        const middleOccupancy = closes.filter((close) => close >= middleLow && close <= middleHigh).length / rows.length;
        const upperVisits = rows.filter((row) => row.high >= motherHigh - range * 0.15).length;
        const lowerVisits = rows.filter((row) => row.low <= motherLow + range * 0.2).length;
        const headroom = motherHigh - level;
        const position = (level - motherLow) / range;
        const wideMotherRange = range >= Math.max(atrValue * 8, level * 0.045);
        const materiallyInside = headroom >= Math.max(atrValue * 2.2, level * 0.015)
          && position <= 0.84;
        // 局部形态只占母区间的一小段时，不能反过来把自己解释成母结构外沿。
        // 若当前盘整已经覆盖母区间的大半，它本身就是待突破的主结构，不在这里否决。
        const localFragment = localBars <= 0 || localBars <= span * 0.46;
        const unordered = efficiency <= 0.16
          && middleOccupancy >= 0.42
          && upperVisits >= 2
          && lowerVisits >= 2;
        const postPeakRows = meaningfulRawPeak && rawPeakIndex >= 0
          ? rows.slice(rawPeakIndex + 1)
          : [];
        const postPeakCloses = postPeakRows.map((row) => row.close);
        const postPeakTravelled = postPeakCloses.slice(1).reduce((sum, close, cursor) => (
          sum + Math.abs(close - postPeakCloses[cursor])
        ), 0);
        const postPeakEfficiency = postPeakCloses.length >= 2
          ? Math.abs(postPeakCloses.at(-1) - postPeakCloses[0]) / Math.max(postPeakTravelled, 1e-8)
          : 1;
        const postPeakRange = postPeakRows.length
          ? quantile(postPeakRows.map((row) => row.high), 0.95)
            - quantile(postPeakRows.map((row) => row.low), 0.05)
          : 0;
        const shockWindow = postPeakRows.slice(0, Math.min(12, postPeakRows.length));
        const shockLow = shockWindow.length ? Math.min(...shockWindow.map((row) => row.low)) : rawMotherHigh;
        const shockLowOffset = shockWindow.findIndex((row) => row.low === shockLow);
        const motherWindowStartIndex = index - span;
        const shockPeakIndex = motherWindowStartIndex + rawPeakIndex;
        const shockLowIndex = shockLowOffset >= 0 ? shockPeakIndex + 1 + shockLowOffset : -1;
        const shockDrop = Math.max(0, rawMotherHigh - shockLow);
        const shockDropPercent = shockDrop / Math.max(rawMotherHigh, 1e-8) * 100;
        const shockLegRows = rawPeak && shockLowOffset >= 0
          ? [rawPeak, ...postPeakRows.slice(0, shockLowOffset + 1)]
          : [];
        const shockSelloffBars = shockLegRows.filter((row) => (
          row.close < row.open
          && row.open - row.close >= Math.max((row.high - row.low) * 0.28, rawMotherHigh * 0.006)
        )).length;
        const shockBearishBody = shockLegRows.reduce((sum, row) => sum + Math.max(0, row.open - row.close), 0);
        const shockTotalBody = shockLegRows.reduce((sum, row) => sum + Math.abs(row.close - row.open), 0);
        const shockBearishDominance = shockBearishBody / Math.max(shockTotalBody, 1e-8);
        const motherEdgeTolerance = Math.max(level * 0.004, range * 0.05);
        const stillInsideMotherEdge = headroom > motherEdgeTolerance && position < 0.96;
        const recoveredOffShockLow = level >= shockLow + shockDrop * 0.2;
        // PI 2025-02-27 这类母箱体由“峰值 K + 紧随其后的急杀 K”直接定出上下沿。
        // 随后的修复反弹即使在局部形成小平台、小前高或小三角，只要仍明显低于
        // 急杀前高，就仍是母箱体内部噪声。这里使用价格比例与母区间占比，不让
        // 急杀抬高 ATR 后反过来放过内部突破，也不受局部结构报出多少根 K 线影响。
        const shockFormedMotherBox = meaningfulRawPeak
          && rawPeakIndex >= Math.max(6, Math.round(span * 0.06))
          && postPeakRows.length >= 16
          && shockLowOffset >= 0
          && shockLowOffset <= 6
          && shockSelloffBars >= 2
          && shockBearishDominance >= 0.72
          && shockDropPercent >= 7
          && shockDrop >= range * 0.45
          && recoveredOffShockLow
          && stillInsideMotherEdge;
        // 急拉见高后若已经轮动多轮，却仍未越过真实峰值，那么内部逐级抬高的
        // 小前高都只是高位母压力区间中的波动，不应连续出现 B。
        const postImpulseHighLevelRotation = meaningfulRawPeak
          && rawPeakIndex >= Math.max(8, Math.round(span * 0.08))
          && postPeakRows.length >= 12
          && postPeakRange >= Math.max(atrValue * 4, level * 0.03)
          && postPeakEfficiency <= 0.3
          && position >= 0.45;
        const mode = shockFormedMotherBox
          ? "shock-formed-mother-box"
          : postImpulseHighLevelRotation
            ? "post-impulse-high-level-rotation"
          : unordered ? "unordered-mother-box" : "";
        return {
          risky: shockFormedMotherBox
            || (wideMotherRange
              && materiallyInside
              && (unordered || postImpulseHighLevelRotation)
              && localFragment),
          mode,
          span,
          motherHigh,
          motherLow,
          rawMotherHigh,
          rawMotherLow,
          headroomAtr: headroom / Math.max(atrValue, 1e-8),
          position,
          efficiency,
          middleOccupancy,
          upperVisits,
          lowerVisits,
          meaningfulRawPeak,
          postPeakBars: postPeakRows.length,
          postPeakEfficiency,
          postPeakRange,
          shockLow,
          shockPeakIndex,
          shockLowIndex,
          shockDropPercent,
          shockLowOffset,
          shockSelloffBars,
          shockBearishDominance,
          stillInsideMotherEdge,
          localBars,
          localShare: localBars / span,
        };
      });
    return assessments.find((item) => item.risky) || { risky: false };
  }

  // 急杀母箱体内部的唯一自动交易例外：5m / 15m 必须先从急杀低点走出一段
  // 独立上推，再形成可单独辨认的紧凑横盘，最后从已经反复确认的外沿起飞。
  // 三角、趋势线、拐点和附近小前高都不能使用这条豁免。
  function isExceptionalShockBoxHorizontalLaunch(options = {}) {
    const {
      interval,
      motherStructure,
      baseComponent,
      launchContext,
      foundationTypes = [],
      auxiliaryTypes = [],
      rhythmScore = 0,
      orderFlowScore = 0,
      ascendingStructureTrap = null,
      shockReboundAdvanceAtr = 0,
      shockReboundAdvancePercent = 0,
    } = options;
    if (!["5m", "15m"].includes(interval)
      || motherStructure?.mode !== "shock-formed-mother-box"
      || !baseComponent?.outerEdgeConfirmed
      || launchContext.postSelloffRecovery === true
      || ascendingStructureTrap) return false;
    const priorAdvanceAtr = Math.max(
      Number(launchContext.priorNetAdvanceAtr) || 0,
      Number(launchContext.riseIntoBaseAtr) || 0,
    );
    const structureStartsAfterShock = Number.isFinite(Number(motherStructure.shockLowIndex))
      && baseComponent.structureStartIndex >= motherStructure.shockLowIndex + 12;
    const pureHorizontalFoundation = foundationTypes.includes("base")
      && foundationTypes.every((type) => ["base", "relaunch"].includes(type));
    const cleanAuxiliaries = auxiliaryTypes.every((type) => type === "previousHigh");
    const independentRebound = launchContext.hasPriorAdvance
      || (shockReboundAdvanceAtr >= 4 && shockReboundAdvancePercent >= 5);
    return structureStartsAfterShock
      && independentRebound
      && pureHorizontalFoundation
      && cleanAuxiliaries
      && baseComponent.platformModel === "outer"
      && (baseComponent.consolidationBars || 0) >= 28
      && (baseComponent.consolidationBars || 0) <= 72
      && (baseComponent.outerEdgeScore || 0) >= 84
      && (baseComponent.ceilingTouches || 0) >= 3
      && (baseComponent.touchGroups || 0) >= 2
      && (baseComponent.fullPlatformEfficiency ?? 1) <= 0.3
      && (baseComponent.platformRangePercent ?? 99) <= 6
      && (baseComponent.platformPressureRatio ?? 0) >= 0.34
      && (baseComponent.launchDistancePercent ?? 99) <= 2.5
      && priorAdvanceAtr >= 3.5
      && rhythmScore >= 72
      && orderFlowScore >= 32;
  }

  // 新币或龙头在早期急杀后，旧高点不能无限期压制后续已经独立启动的主升结构。
  // 这里只开放一种极窄的 5 分钟例外：先走出新的上推和区间扩张，再形成完整的
  // 上升三角，同时突破水平压力与盘整真前高。普通箱体内反弹、局部趋势线和
  // 上升通道/楔形末端都不满足这些几何与情绪条件。
  function isExceptionalShockBoxAscendingTriangleIgnition(options = {}) {
    const {
      interval,
      motherStructure,
      triangleComponent,
      triangleLaunchContext,
      baseComponent,
      foundationTypes = [],
      auxiliaryTypes = [],
      hasPivot = false,
      level = 0,
      breakoutLow = 0,
      pressureBars = 0,
      rhythmScore = 0,
      orderFlowScore = 0,
      trendUp = false,
      aboveEma90 = false,
      ema90Slope = 0,
    } = options;
    const upper = triangleComponent?.triangleLines?.upper;
    const lower = triangleComponent?.triangleLines?.lower;
    const preContext = triangleComponent?.preStructureContext;
    const triangleStart = triangleComponent?.triangleLines?.structureStartIndex
      ?? upper?.startIndex;
    const priorAdvanceAtr = Math.max(
      Number(triangleLaunchContext?.priorNetAdvanceAtr) || 0,
      Number(triangleLaunchContext?.riseIntoBaseAtr) || 0,
      Number(preContext?.bestAdvance?.advanceAtr) || 0,
    );
    const motherGapPercent = (Number(motherStructure?.motherHigh) - Number(level))
      / Math.max(Number(motherStructure?.motherHigh) || 0, 1e-8) * 100;
    const breakoutDistancePercent = (Number(level) - Number(breakoutLow))
      / Math.max(Number(breakoutLow) || 0, 1e-8) * 100;
    const matureCausalTriangle = (triangleComponent?.quality || 0) >= 0.72
      && (triangleComponent?.channelInteriorOccupancy || 0) >= 0.9
      && (triangleComponent?.channelSideTransitions || 0) >= 4
      && (upper?.touchGroups || 0) >= 3
      && (lower?.touchGroups || 0) >= 3
      && (upper?.envelopeCoverage || 0) >= 0.98
      && (lower?.envelopeCoverage || 0) >= 0.98
      && (upper?.crossingRatio ?? 1) <= 0.02
      && (lower?.crossingRatio ?? 1) <= 0.02;
    return interval === "5m"
      && motherStructure?.mode === "shock-formed-mother-box"
      && Number.isFinite(Number(motherStructure.shockLowIndex))
      && Number.isFinite(Number(triangleStart))
      && triangleStart >= motherStructure.shockLowIndex + 24
      && triangleComponent?.structureShape === "ascending-triangle"
      && (triangleComponent.consolidationBars || 0) >= 28
      && (triangleComponent.consolidationBars || 0) <= 60
      && (triangleComponent.quality || 0) >= 0.67
      && (triangleComponent.channelInteriorOccupancy || 0) >= 0.85
      && (triangleComponent.channelSideTransitions || 0) >= 3
      && upper?.boundaryModel === "horizontal-pressure-envelope"
      && lower?.boundaryModel === "rising-support-envelope"
      && (upper?.touchGroups || 0) >= 3
      && (lower?.touchGroups || 0) >= 3
      && (upper?.envelopeCoverage || 0) >= 0.95
      && (lower?.envelopeCoverage || 0) >= 0.95
      && (upper?.crossingRatio ?? 1) <= 0.05
      && (lower?.crossingRatio ?? 1) <= 0.05
      && preContext?.qualified === true
      // 更长回看窗口有时会把最近一次推进并入更早的大推进。此时不再用一个
      // 综合分补偿，而是让已经成熟、占用充分、几乎不穿线的三角几何直接作证。
      && (preContext?.freshRangeExpansion === true || matureCausalTriangle)
      && priorAdvanceAtr >= 4
      && triangleLaunchContext?.postSelloffRecovery !== true
      && Boolean(baseComponent?.outerEdgeConfirmed)
      && foundationTypes.includes("base")
      && foundationTypes.includes("triangle")
      && foundationTypes.every((type) => ["base", "triangle", "relaunch"].includes(type))
      && auxiliaryTypes.every((type) => type === "previousHigh")
      && hasPivot
      && motherGapPercent >= 0
      && motherGapPercent <= 4.5
      && breakoutDistancePercent >= 0
      && breakoutDistancePercent <= 7
      && pressureBars >= 3
      && rhythmScore >= 76
      && orderFlowScore >= 45
      && trendUp
      && aboveEma90
      && ema90Slope >= 0;
  }

  function isOneMinutePostImpulseHorizontalLaunch(signal) {
    const foundations = signal?.foundationTypes || [];
    const auxiliaries = signal?.auxiliaryTypes || [];
    const touches = Math.max(signal?.ceilingTouches || 0, signal?.platformTouchGroups || 0);
    return signal?.interval === "1m"
      && foundations.includes("base")
      && foundations.every((type) => ["base", "relaunch"].includes(type))
      && auxiliaries.every((type) => type === "previousHigh")
      && !signal?.structureShape
      && signal?.outerEdgeConfirmed === true
      && (signal?.consolidationBars || 0) >= 28
      && (signal?.outerEdgeScore || 0) >= 66
      && touches >= 2
      && (signal?.launchDistancePercent ?? 99) <= 7
      && signal?.horizontalLaunchHasPriorAdvance === true
      && signal?.horizontalLaunchPostSelloffRecovery !== true
      && signal?.motherStructureNoise !== true
      && signal?.aboveEma90 === true
      && (signal?.ema90SlopeAtDecision || 0) >= 0
      && (signal?.certaintyScore || 0) >= 70
      && (signal?.rhythmScore || 0) >= 60
      && (signal?.score || 0) >= 72;
  }

  function isOneMinuteHorizontalBase(signal) {
    if (signal?.manualCandleSelection) return true;
    if (signal?.manualDecision === "confirmed"
      && (signal?.manualStructureTags || []).some((tag) => ["horizontalLaunch", "box"].includes(tag))) return true;
    if (signal?.motherStructureNoise === true || signal?.oneMinuteMotherBoxNoise === true) return false;
    const foundations = signal?.foundationTypes || [];
    const auxiliaries = signal?.auxiliaryTypes || [];
    const hasBase = foundations.includes("base");
    const baseFamilyOnly = hasBase && foundations.every((type) => ["base", "relaunch"].includes(type));
    const cleanBoxTrigger = baseFamilyOnly
      && !signal?.structureShape
      && auxiliaries.every((type) => type === "previousHigh");
    if (!cleanBoxTrigger || signal?.outerEdgeConfirmed !== true || (signal?.launchDistancePercent ?? 0) > 7) return false;
    const bars = signal?.consolidationBars || 0;
    const outerEdge = signal?.outerEdgeScore || 0;
    const touches = signal?.ceilingTouches || 0;
    // 纯横盘起飞用可以复核的外沿、触点和平台年龄确认，不再依赖综合审美分。
    const matureHorizontalLaunch = foundations.length === 1
      && bars >= 32
      && outerEdge >= 72
      && touches >= 2
      && ((signal?.ceilingAge || 0) >= 4 || (signal?.platformTouchGroups || 0) >= 2 || touches >= 3);
    const aPlusBoxBreakout = bars >= 28
      && outerEdge >= 84
      && touches >= 2
      && ((signal?.ceilingAge || 0) >= 4 || (signal?.platformTouchGroups || 0) >= 2 || touches >= 3)
      && (signal?.certaintyScore || 0) >= 78
      && (signal?.rhythmScore || 0) >= 66
      && (signal?.score || 0) >= 76;
    return matureHorizontalLaunch
      || aPlusBoxBreakout
      || isOneMinutePostImpulseHorizontalLaunch(signal);
  }

  function evaluateCandidate(candles, index, candidate, indicators, interval, options = {}) {
    const current = candles[index];
    const priorIndex = index - 1;
    const prior = candles[priorIndex];
    const atrValue = Math.max(indicators.atr[priorIndex], 1e-8);
    const ema90Value = indicators.ema90[priorIndex];
    const priorEma90 = indicators.ema90[Math.max(0, priorIndex - 8)];
    const pressureDistanceAtr = Math.max(0, candidate.level - prior.close) / atrValue;
    const pressureBars = candles.slice(Math.max(0, index - 8), index)
      .filter((row) => row.close >= candidate.level - atrValue * 1.25).length;
    const compression = compressionRatio(candles, index);
    const consolidation = candidate.consolidationBars
      ? { bars: candidate.consolidationBars }
      : consolidationProfile(candles, index, atrValue, 12, 96);
    const recentVolume = mean(candles.slice(Math.max(0, index - 6), index).map((row) => row.volume));
    const baselineVolume = mean(candles.slice(Math.max(0, index - 20), Math.max(0, index - 6)).map((row) => row.volume));
    const relativeVolume = recentVolume / Math.max(baselineVolume, 1e-8);
    const flowRecent = candles.slice(Math.max(1, index - 4), index);
    const flowBaseline = candles.slice(Math.max(0, index - 24), Math.max(0, index - 4));
    const quoteAt = (row) => row.quoteVolume || row.volume * row.close;
    const baselineQuote = mean(flowBaseline.map(quoteAt));
    const bigTradeRatio = Math.max(...flowRecent.map(quoteAt), 0) / Math.max(baselineQuote, 1e-8);
    const klineVelocity = Math.max(...flowRecent.map((row, cursor) => {
      const absoluteIndex = index - flowRecent.length + cursor;
      const previousClose = candles[Math.max(0, absoluteIndex - 1)].close;
      return Math.max(0, row.close - previousClose) / atrValue;
    }), 0);
    const closeLocation = mean(flowRecent.map((row) => (
      (row.close - row.low) / Math.max(row.high - row.low, 1e-8)
    )));
    const takerVolume = flowRecent.reduce((sum, row) => sum + row.takerBuyVolume, 0);
    const totalFlowVolume = flowRecent.reduce((sum, row) => sum + row.volume, 0);
    const hasTakerFlow = takerVolume > 0 && totalFlowVolume > 0;
    const takerBuyRatio = hasTakerFlow ? takerVolume / totalFlowVolume : null;
    const orderFlowScore = Math.round(clamp((
      clamp((bigTradeRatio - 0.9) / 1.6, 0, 1) * 0.3
      + clamp(klineVelocity / 0.9, 0, 1) * 0.28
      + clamp((closeLocation - 0.38) / 0.42, 0, 1) * 0.24
      + (hasTakerFlow ? clamp((takerBuyRatio - 0.42) / 0.22, 0, 1) : 0.5) * 0.18
    ) * 100, 0, 100));
    const preAdvanceAtr = (prior.close - candles[Math.max(0, index - 18)].close) / atrValue;
    const trendUp = Number.isFinite(ema90Value) && Number.isFinite(priorEma90) && ema90Value >= priorEma90;
    const durationQuality = clamp(consolidation.bars / 40, 0, 1);
    const compressionQuality = clamp((1.45 - compression) / 0.85, 0, 1);
    const pressureQuality = clamp(pressureBars / 5, 0, 1);
    const dryUpQuality = clamp((1.25 - relativeVolume) / 0.55, 0, 1);
    const foundationTypes = candidate.foundationTypes || candidate.confluence.filter((type) => FOUNDATION_PATTERNS.has(type));
    const auxiliaryTypes = candidate.auxiliaryTypes || candidate.confluence.filter((type) => AUXILIARY_PATTERNS.has(type));
    const hasPivot = candidate.hasPivot || candidate.confluence.includes("pivot");
    const foundationComponents = candidate.components.filter((item) => FOUNDATION_PATTERNS.has(item.type));
    const baseComponent = foundationComponents.find((item) => item.type === "base") || null;
    const triangleComponent = foundationComponents.find((item) => item.type === "triangle") || null;
    const relaunchComponent = foundationComponents.find((item) => item.type === "relaunch") || null;
    const previousHighComponent = candidate.components.find((item) => item.type === "previousHigh") || null;
    const horizontalUrgency = baseComponent?.horizontalUrgency || null;
    const horizontalDwell = assessHorizontalBaseDwell(baseComponent);
    const independentMatureTriangle = Boolean(triangleComponent)
      && (triangleComponent.quality || 0) >= 0.62
      && (triangleComponent.consolidationBars || 0) >= 24
      && (candidate.channelInteriorOccupancy || 0) >= 0.55
      && (candidate.channelSideTransitions || 0) >= 3
      && (triangleComponent.triangleLines?.upper?.touchGroups || 0) >= 2
      && (triangleComponent.triangleLines?.lower?.touchGroups || 0) >= 2;
    const motherStructure = assessMotherStructureNoise(candles, index, candidate.level, atrValue, {
      interval,
      consolidationBars: Math.max(
        candidate.consolidationBars || 0,
        ...foundationComponents.map((item) => item.consolidationBars || 0),
      ),
    });
    const strictShockBaseComponent = motherStructure.mode === "shock-formed-mother-box"
      && ["5m", "15m"].includes(interval)
      ? detectOuterPlatform(candles, index, atrValue, 28, 72, { strictHorizontal: true })
      : null;
    const strictShockLaunchContext = strictShockBaseComponent
      ? assessHorizontalLaunchContext(candles, index, strictShockBaseComponent, atrValue)
      : null;
    const shockReboundReferenceRows = strictShockBaseComponent
      ? candles.slice(
        strictShockBaseComponent.structureStartIndex,
        Math.min(index, strictShockBaseComponent.structureStartIndex + 4),
      )
      : [];
    const shockReboundReference = mean(shockReboundReferenceRows.map((row) => row.close));
    const shockReboundAdvance = strictShockBaseComponent
      ? Math.max(0, shockReboundReference - Number(motherStructure.shockLow || 0))
      : 0;
    const shockReboundAdvanceAtr = shockReboundAdvance / Math.max(atrValue, 1e-8);
    const shockReboundAdvancePercent = shockReboundAdvance
      / Math.max(Number(motherStructure.shockLow) || 0, 1e-8) * 100;
    const horizontalLaunchContext = baseComponent
      ? assessHorizontalLaunchContext(candles, index, baseComponent, atrValue)
      : null;
    const horizontalPreStructureContext = baseComponent
      ? assessPreStructureContext(
        candles,
        index,
        baseComponent.structureStartIndex ?? index - (baseComponent.consolidationBars || 0),
        atrValue,
        {
          interval,
          newCoinNotFalling: options.newCoinNotFalling === true,
        },
      )
      : null;
    const horizontalStructureException = horizontalPreStructureContext
      && ["higher-timeframe-bottom-base", "new-coin-not-falling"].includes(horizontalPreStructureContext.mode);
    const triangleLaunchContext = triangleComponent
      ? assessHorizontalLaunchContext(candles, index, {
        structureStartIndex: triangleComponent.triangleLines?.structureStartIndex
          ?? triangleComponent.triangleLines?.upper?.startIndex,
        consolidationBars: triangleComponent.consolidationBars,
        level: triangleComponent.level,
        stop: triangleComponent.stop,
      }, atrValue)
      : null;
    const triangleUpper = triangleComponent?.triangleLines?.upper;
    const triangleLower = triangleComponent?.triangleLines?.lower;
    // 同一根突破可能同时落在一个很长的母平台和一个更贴近当前节奏的三角里。
    // 长平台的起点若取得过早，会看不到三角开始前已经完成的独立上推，从而把
    // 合格的“拉升—三角盘整—突破”误判为普通横盘。这里只允许成熟三角把自己
    // 的因果前置上推传给同一候选中的横盘结构；纯横盘和低质量小三角不受影响。
    const triangleAnchoredHorizontalPriorAdvance = Boolean(baseComponent)
      && Boolean(triangleComponent)
      // 若当前仍被识别为更大的无序母箱体，必须继续走原有的龙头主升
      // 旧边界例外审计，不能用局部三角直接绕过母结构许可。
      && motherStructure.risky !== true
      && (triangleComponent.quality || 0) >= 0.65
      && (triangleComponent.consolidationBars || 0) >= 36
      && (triangleComponent.channelInteriorOccupancy || 0) >= 0.65
      && (triangleComponent.channelSideTransitions || 0) >= 2
      && (triangleUpper?.touchGroups || 0) >= 3
      && (triangleLower?.touchGroups || 0) >= 2
      && triangleComponent.preStructureContext?.bestAdvance?.qualified === true
      && (triangleComponent.preStructureContext.bestAdvance.advanceAtr || 0) >= 4
      && triangleLaunchContext?.postSelloffRecovery !== true;
    const effectiveHorizontalPriorAdvance = horizontalLaunchContext?.hasPriorAdvance === true
      || triangleAnchoredHorizontalPriorAdvance;
    const effectiveHorizontalPriorAdvanceAtr = Math.max(
      Number(horizontalLaunchContext?.priorNetAdvanceAtr) || 0,
      Number(horizontalLaunchContext?.riseIntoBaseAtr) || 0,
      Number(triangleLaunchContext?.priorNetAdvanceAtr) || 0,
      Number(triangleLaunchContext?.riseIntoBaseAtr) || 0,
      Number(triangleComponent?.preStructureContext?.bestAdvance?.advanceAtr) || 0,
    );
    const horizontalLaunchDisplayRetracementReady = horizontalLaunchContext?.retainedAboveHalf === true
      || (horizontalLaunchContext?.retracementRatio || 0) <= 0.6
      || effectiveHorizontalPriorAdvanceAtr >= 10
      || (baseComponent?.consolidationBars || 0) >= 56;
    const horizontalLaunchQualified = Boolean(baseComponent)
      && effectiveHorizontalPriorAdvance
      && horizontalLaunchContext?.postSelloffRecovery !== true
      // 高位重建可以帮助母平台继续取得“盘整突破”执行权，但若回撤明显
      // 超过半分位、前置推动又不够强，就不额外贴“横盘起飞”标签。
      && horizontalLaunchDisplayRetracementReady
      && (horizontalLaunchContext?.horizontalLaunchQualified === true
        || (triangleAnchoredHorizontalPriorAdvance
          && triangleLaunchContext?.horizontalLaunchQualified === true));
    const triangleDominatesAscendingTrap = Boolean(candidate.ascendingStructureTrap)
      && triangleComponent?.structureShape === "ascending-triangle"
      && (triangleComponent.quality || 0) >= 0.67
      && (triangleComponent.consolidationBars || 0) >= 28
      && (triangleComponent.channelInteriorOccupancy || 0) >= 0.85
      && (triangleComponent.channelSideTransitions || 0) >= 3
      && triangleUpper?.boundaryModel === "horizontal-pressure-envelope"
      && triangleLower?.boundaryModel === "rising-support-envelope"
      && (triangleUpper?.touchGroups || 0) >= 3
      && (triangleLower?.touchGroups || 0) >= 3
      && Math.abs(
        Number(triangleComponent.triangleLines?.structureStartIndex ?? triangleUpper?.startIndex)
        - Number(candidate.ascendingStructureTrap.startIndex),
      ) <= Math.max(8, Math.round((triangleComponent.consolidationBars || 0) * 0.25));
    // 上升通道有时只是母箱体中后段的内部行进路径。若更早开始的母平台已经
    // 围绕同一真实前高反复交易，且最终触发价同时越过通道上轨与母箱体最高点，
    // 应由母箱体取得结构优先级。没有成熟外沿、触点分组或明显更早起点的普通
    // 上升通道仍继续硬否决，避免把“通道末端不追”整体放宽。
    const ascendingRiskPredatesCurrentMotherPlatform = Boolean(candidate.ascendingStructureTrap)
      && Boolean(baseComponent?.outerEdgeConfirmed)
      && Number.isFinite(baseComponent?.structureStartIndex)
      && Number.isFinite(candidate.ascendingStructureTrap.startIndex)
      && baseComponent.structureStartIndex - candidate.ascendingStructureTrap.startIndex
        >= Math.max(8, Math.round((baseComponent.consolidationBars || 0) * 0.2))
      && candidate.level >= baseComponent.level - atrValue * 0.15;
    const motherPlatformDominatesTerminalAscendingRisk = Boolean(candidate.ascendingStructureTrap)
      && Boolean(baseComponent?.outerEdgeConfirmed)
      && (baseComponent.outerEdgeScore || 0) >= 82
      && (baseComponent.consolidationBars || 0) >= 40
      && (baseComponent.quality || 0) >= 0.62
      && baseComponent.clusteredCeilingBand === true
      && (baseComponent.ceilingTouches || 0) >= 4
      && (baseComponent.touchGroups || 0) >= 3
      && Number.isFinite(baseComponent.structureStartIndex)
      && Number.isFinite(candidate.ascendingStructureTrap.startIndex)
      && candidate.ascendingStructureTrap.startIndex - baseComponent.structureStartIndex
        >= Math.max(8, Math.round((baseComponent.consolidationBars || 0) * 0.2))
      && candidate.level >= (candidate.ascendingStructureTrap.upperAtTrigger || candidate.level)
        + atrValue * 0.08;
    let effectiveAscendingStructureTrap = (
      triangleDominatesAscendingTrap
      || motherPlatformDominatesTerminalAscendingRisk
      || ascendingRiskPredatesCurrentMotherPlatform
    )
      ? null
      : candidate.ascendingStructureTrap;
    const compactOneHourOuterPlatformBreak = interval === "1h"
      && baseComponent?.platformModel === "outer"
      && baseComponent?.clusteredCeilingBand === true
      && (baseComponent.consolidationBars || 0) >= 12
      && (baseComponent.quality || 0) >= 0.6
      && (baseComponent.touchGroups || 0) >= 2
      && (baseComponent.ceilingTouches || 0) >= 7;
    const confirmedPlatformBreak = Boolean(baseComponent?.outerEdgeConfirmed)
      && (baseComponent.outerEdgeScore || 0) >= 62
      && ((baseComponent.consolidationBars || 0) >= 18 || compactOneHourOuterPlatformBreak)
      && (baseComponent.launchDistancePercent || 0) <= 7
      && !candidate.openedBeyondTrigger;
    const shockMotherBoxOuterEdgeBreakout = baseComponent?.shockMotherBoxOuterEdge === true
      && confirmedPlatformBreak;
    if (shockMotherBoxOuterEdgeBreakout) effectiveAscendingStructureTrap = null;
    const oneHourPlatformPivotReady = interval === "1h"
      && candidate.oneHourPlatformPivotBoundary === true
      && Boolean(baseComponent)
      && Boolean(previousHighComponent)
      && hasPivot
      && (baseComponent.consolidationBars || 0) >= 36
      && (baseComponent.quality || 0) >= 0.46
      && (baseComponent.launchDistancePercent ?? 99) <= 7
      && horizontalLaunchContext?.hasPriorAdvance === true
      && horizontalLaunchContext?.postSelloffRecovery !== true
      && trendUp
      && current.open < candidate.level
      && current.high >= candidate.level;
    const reclaimStructureStartIndices = [
      baseComponent?.structureStartIndex,
      triangleComponent?.triangleLines?.structureStartIndex,
      triangleComponent?.triangleLines?.upper?.startIndex,
      relaunchComponent?.structureStartIndex,
    ].map(Number).filter(Number.isFinite);
    const reclaimStructureStartIndex = reclaimStructureStartIndices.length
      // EMA90 收复描述的是母结构末端的修复段。更早的母平台起点只负责
      // 结构背景，不能把末端跌破—收回窗口无限向左拉长后判成“修复太久”。
      ? Math.max(...reclaimStructureStartIndices)
      : index - Math.max(28, candidate.consolidationBars || 0);
    const reclaimPriorAdvanceAtr = Math.max(
      Number(horizontalLaunchContext?.priorNetAdvanceAtr) || 0,
      Number(horizontalLaunchContext?.riseIntoBaseAtr) || 0,
      Number(triangleLaunchContext?.priorNetAdvanceAtr) || 0,
      Number(triangleLaunchContext?.riseIntoBaseAtr) || 0,
      Number(triangleComponent?.preStructureContext?.bestAdvance?.advanceAtr) || 0,
      Number(horizontalPreStructureContext?.bestAdvance?.advanceAtr) || 0,
    );
    const ema90ReclaimContinuation = assessEma90ReclaimContinuation(
      candles,
      index,
      indicators,
      interval,
      atrValue,
      {
        structureStartIndex: reclaimStructureStartIndex,
        consolidationBars: Math.max(
          candidate.consolidationBars || 0,
          baseComponent?.consolidationBars || 0,
          triangleComponent?.consolidationBars || 0,
          relaunchComponent?.consolidationBars || 0,
        ),
        structureQuality: Math.max(
          candidate.quality || 0,
          baseComponent?.quality || 0,
          triangleComponent?.quality || 0,
          relaunchComponent?.quality || 0,
        ),
        structureLabel: triangleComponent
          ? triangleComponent.structureShape === "falling-wedge" ? "降楔" : "三角/收敛"
          : baseComponent ? "横盘/箱体" : "回踩再启动",
        matureStructure: confirmedPlatformBreak
          || candidate.matureTriangleBoundary === true
          || candidate.matureTriangleOuterEdge === true
          || (foundationTypes.length >= 2
            && (candidate.consolidationBars || 0) >= 28
            && auxiliaryTypes.includes("previousHigh")
            && pressureBars >= 2),
        hasPriorAdvance: effectiveHorizontalPriorAdvance
          || horizontalPreStructureContext?.bestAdvance?.qualified === true
          || triangleLaunchContext?.hasPriorAdvance === true
          || triangleComponent?.preStructureContext?.bestAdvance?.qualified === true,
        priorAdvanceAtr: reclaimPriorAdvanceAtr,
        postSelloffRecovery: horizontalLaunchContext?.postSelloffRecovery === true
          || (triangleComponent && triangleLaunchContext?.postSelloffRecovery === true),
        strictMotherRisk: ["shock-formed-mother-box", "post-impulse-high-level-rotation"]
          .includes(motherStructure.mode),
        ascendingStructureTrap: effectiveAscendingStructureTrap,
      },
    );
    if (ema90ReclaimContinuation.qualified && ema90ReclaimContinuation.nestedAscendingTrap) {
      effectiveAscendingStructureTrap = null;
    }
    // 长时间的一小时盘整可能大部分时间压在 EMA90 附近，直到结构末端才
    // 同时收复均线与上沿。若强制要求“突破前一根已经在 EMA90 上方且均线
    // 已转升”，会把 XRP 这种最关键的结构拐点延迟甚至删除。这里不是放宽
    // 普通反弹：必须有独立前置拉升、32 根以上成熟母结构、真实外沿从线下
    // 触发，且触发线只比当时 EMA90 高很小一段，才能把结构突破本身视为
    // EMA90 的终端收复确认。
    const terminalEma90BoundaryReclaim = interval === "1h"
      && Number.isFinite(ema90Value)
      && prior.close <= ema90Value + atrValue * 0.12
      && candidate.triggerPrice >= ema90Value + atrValue * 0.04
      && candidate.triggerPrice <= ema90Value + atrValue * 0.72
      && (candidate.consolidationBars || 0) >= 32
      && (candidate.quality || 0) >= 0.62
      && (effectiveHorizontalPriorAdvance
        || triangleComponent?.preStructureContext?.hasPriorAdvance === true)
      && (effectiveHorizontalPriorAdvanceAtr >= 2
        || (triangleComponent?.preStructureContext?.bestAdvance?.advanceAtr || 0) >= 2)
      && candidate.crossedLevel === true
      && candidate.openedBeyondTrigger !== true
      && !effectiveAscendingStructureTrap
      && motherStructure.risky !== true;
    const trianglePostSelloffRisk = Boolean(triangleLaunchContext?.postSelloffRecovery)
      && (!trendUp || prior.close < ema90Value - atrValue * 0.25);
    const structuralWeight = foundationTypes.length
      + Number(hasPivot) * 0.4
      + auxiliaryTypes.length * 0.1;
    const confluenceQuality = clamp(structuralWeight / 2, 0, 1);
    const rhythmScore = Math.round(clamp((
      durationQuality * 0.25
      + compressionQuality * 0.22
      + pressureQuality * 0.21
      + candidate.quality * 0.14
      + (trendUp ? 1 : 0) * 0.1
      + confluenceQuality * 0.08
    ) * 100, 0, 100));
    const sentimentScore = Math.round(clamp((
      (trendUp ? 1 : 0) * 0.28
      + clamp(preAdvanceAtr / 5, 0, 1) * 0.2
      + pressureQuality * 0.2
      + compressionQuality * 0.14
      + dryUpQuality * 0.08
      + confluenceQuality * 0.1
    ) * 100, 0, 100));
    const sentimentPhase = sentimentScore >= 72 ? "主升接力" : sentimentScore >= 55 ? "蓄势临界" : "情绪未聚焦";
    // 只保留可解释的结构证据汇总；它用于页面解释与排序，不作为任何单项硬门槛。
    // 买点许可继续由母平台、真实外沿、前置拉升、包络/穿插、EMA90 与突破事实决定。
    const structuralEvidenceScore = Math.round(clamp(
      candidate.quality * 100 * 0.28
      + durationQuality * 100 * 0.16
      + compressionQuality * 100 * 0.14
      + pressureQuality * 100 * 0.14
      + dryUpQuality * 100 * 0.06
      + confluenceQuality * 100 * 0.08
      + Number(confirmedPlatformBreak || candidate.matureTriangleOuterEdge === true) * 14,
      0,
      100,
    ));
    const slippageBps = Number.isFinite(options.slippageBps) ? options.slippageBps : DEFAULT_SLIPPAGE_BPS;
    const price = candidate.triggerPrice * (1 + slippageBps / 10_000);
    const originDistanceAtr = Math.max(
      Math.abs(prior.close - candidate.level),
      Math.abs(current.open - candidate.level),
    ) / atrValue;
    const distanceAtr = Math.max(0, price - candidate.level) / atrValue;
    const structuralRiskPercent = Math.max(0, candidate.triggerPrice - candidate.stop) / Math.max(candidate.triggerPrice, 1e-8) * 100;
    const reasons = [];
    const oneMinutePrototypeCertainty = Math.round(clamp(
      structuralEvidenceScore
      + Number(confirmedPlatformBreak) * 10
      + Number(foundationTypes.length >= 2) * 5
      + Math.min(auxiliaryTypes.length, 2) * 2
      + Number(orderFlowScore >= 65) * 2,
      0,
      99,
    ));
    const oneMinutePrototypeScore = Math.round(clamp(
      32
      + rhythmScore * 0.36
      + sentimentScore * 0.18
      + candidate.quality * 10
      + Math.min(Math.max(foundationTypes.length - 1, 0), 2) * 9
      + Number(hasPivot) * 4
      + auxiliaryTypes.length * 2,
      0,
      99,
    ));

    if (interval === "1m" && !isOneMinuteHorizontalBase({
      foundationTypes,
      auxiliaryTypes,
      hasPivot,
      patternKey: candidate.type,
      consolidationBars: baseComponent?.consolidationBars || consolidation.bars,
      outerEdgeConfirmed: confirmedPlatformBreak,
      outerEdgeScore: baseComponent?.outerEdgeScore || 0,
      platformModel: baseComponent?.platformModel || null,
      shockMotherBoxOuterEdge: baseComponent?.shockMotherBoxOuterEdge === true,
      longSwingMotherBox: baseComponent?.longSwingMotherBox === true,
      ceilingTouches: baseComponent?.ceilingTouches || 0,
      ceilingAge: baseComponent?.ceilingAge || 0,
      platformTouchGroups: baseComponent?.touchGroups || 0,
      structureQuality: candidate.quality || 0,
      certaintyScore: oneMinutePrototypeCertainty,
      rhythmScore,
      score: oneMinutePrototypeScore,
      launchDistancePercent: baseComponent?.launchDistancePercent ?? 0,
      structureShape: candidate.structureShape || null,
      motherStructureNoise: motherStructure.risky,
      oneMinuteMotherBoxNoise: interval === "1m" && motherStructure.risky,
    })) {
      reasons.push("1分钟仅保留高确定性横盘起飞或箱体突破，其他结构全部过滤");
    }
    if (auxiliaryTypes.includes("previousHigh")
      && Number.isFinite(Number(baseComponent?.launchDistancePercent))
      && Number(baseComponent.launchDistancePercent) > 7) {
      reasons.push("突破K首次触发前低点到前高的涨幅超过 7%，不做这次突破前高");
    }
    const shockBoxHorizontalLaunchException = isExceptionalShockBoxHorizontalLaunch({
      interval,
      motherStructure,
      baseComponent: strictShockBaseComponent,
      launchContext: strictShockLaunchContext,
      foundationTypes,
      auxiliaryTypes,
      rhythmScore,
      orderFlowScore,
      ascendingStructureTrap: candidate.ascendingStructureTrap,
      shockReboundAdvanceAtr,
      shockReboundAdvancePercent,
    });
    const shockBoxAscendingTriangleException = isExceptionalShockBoxAscendingTriangleIgnition({
      interval,
      motherStructure,
      triangleComponent,
      triangleLaunchContext,
      baseComponent,
      foundationTypes,
      auxiliaryTypes,
      hasPivot,
      level: candidate.level,
      breakoutLow: current.low,
      pressureBars,
      rhythmScore,
      orderFlowScore,
      trendUp,
      aboveEma90: Number.isFinite(ema90Value) && prior.close > ema90Value,
      ema90Slope: Number.isFinite(ema90Value) && Number.isFinite(priorEma90)
        ? ema90Value - priorEma90
        : 0,
    });
    const strictMotherBox = ["shock-formed-mother-box", "post-impulse-high-level-rotation"]
      .includes(motherStructure.mode)
      && ["1m", "5m", "15m", "1h"].includes(interval);
    const declaredMainWaveStage = ["active", "expected"].includes(options.mainWaveStage)
      ? options.mainWaveStage
      : null;
    const declaredMainWaveContextSource = String(options.mainWaveContextSource || "manual-analysis");
    const declaredMainWaveContextLabel = String(options.mainWaveContextLabel || (
      declaredMainWaveStage === "active" ? "人工确认主升浪阶段" : "人工给出主升浪预期"
    ));
    const triangleAdvanceAtr = Math.max(
      Number(triangleLaunchContext?.priorNetAdvanceAtr) || 0,
      Number(triangleLaunchContext?.riseIntoBaseAtr) || 0,
      Number(triangleComponent?.preStructureContext?.bestAdvance?.advanceAtr) || 0,
    );
    const inferredMainWaveStage = shockBoxAscendingTriangleException
      || (trendUp
        && Number.isFinite(ema90Value)
        && prior.close > ema90Value
        && (!Number.isFinite(priorEma90) || ema90Value >= priorEma90)
        && sentimentScore >= 60
        && Math.max(preAdvanceAtr, triangleAdvanceAtr) >= 3
        && (triangleComponent?.preStructureContext?.freshRangeExpansion === true
          || horizontalLaunchContext?.hasPriorAdvance === true))
      ? "active"
      : trendUp
        && sentimentScore >= 52
        && Math.max(preAdvanceAtr, triangleAdvanceAtr) >= 2
        ? "expected"
        : "neutral";
    const mainWaveStage = declaredMainWaveStage || inferredMainWaveStage;
    // 人工主升判断只放宽“环境许可”，不能把普通局部波动变成买点。只有已经
    // 独立完成成熟母结构、真实外沿和顺势节奏的候选，才允许绕过过宽母箱体的误伤。
    const declaredMainWaveStructurePermit = Boolean(declaredMainWaveStage)
      && ["5m", "15m", "1h"].includes(interval)
      && foundationTypes.length >= 1
      && (confirmedPlatformBreak || candidate.matureTriangleOuterEdge === true || oneHourPlatformPivotReady)
      && !effectiveAscendingStructureTrap
      && !trianglePostSelloffRisk
      && horizontalLaunchContext?.postSelloffRecovery !== true
      && pressureBars >= 2
      && rhythmScore >= (declaredMainWaveStage === "active" ? 68 : 74)
      && orderFlowScore >= 45
      && trendUp
      && Number.isFinite(ema90Value)
      && prior.close > ema90Value
      && (!Number.isFinite(priorEma90) || ema90Value >= priorEma90);
    const currentStructureStartIndices = [
      baseComponent?.structureStartIndex,
      triangleComponent?.triangleLines?.structureStartIndex,
      triangleComponent?.triangleLines?.upper?.startIndex,
      relaunchComponent?.structureStartIndex,
    ].map(Number).filter(Number.isFinite);
    const currentStructureStartIndex = currentStructureStartIndices.length
      ? Math.max(...currentStructureStartIndices)
      : index - Math.max(12, candidate.consolidationBars || 0);
    const independentAdvancePivotIndices = [
      horizontalPreStructureContext?.bestAdvance?.pivotIndex,
      triangleComponent?.preStructureContext?.bestAdvance?.pivotIndex,
    ].map(Number).filter(Number.isFinite);
    const independentAdvancePivotIndex = independentAdvancePivotIndices.length
      ? Math.max(...independentAdvancePivotIndices)
      : currentStructureStartIndex;
    const motherPeakIndex = Number(motherStructure.shockPeakIndex);
    const motherSpan = Number(motherStructure.span) || 0;
    const oldBoundarySeparation = Math.max(12, Math.round(motherSpan * 0.12));
    const oldBoundaryPrecedesIndependentAdvance = Number.isFinite(motherPeakIndex)
      && motherPeakIndex >= 0
      && independentAdvancePivotIndex - motherPeakIndex >= oldBoundarySeparation;
    const currentStructureHasPriorAdvance = horizontalLaunchContext?.hasPriorAdvance === true
      || triangleLaunchContext?.hasPriorAdvance === true
      || triangleComponent?.preStructureContext?.bestAdvance?.qualified === true;
    const currentStructureMature = foundationTypes.length >= 1
      && (confirmedPlatformBreak || candidate.matureTriangleOuterEdge === true || oneHourPlatformPivotReady);
    const mainWaveStructureQualityPermit = ["active", "expected"].includes(mainWaveStage)
      && ["5m", "15m", "1h"].includes(interval)
      && currentStructureMature
      && currentStructureHasPriorAdvance
      && !effectiveAscendingStructureTrap
      && !trianglePostSelloffRisk
      && horizontalLaunchContext?.postSelloffRecovery !== true
      && pressureBars >= 2
      && rhythmScore >= (mainWaveStage === "active" ? 66 : 72)
      && orderFlowScore >= (mainWaveStage === "active" ? 35 : 45)
      && trendUp
      && Number.isFinite(ema90Value)
      && prior.close > ema90Value
      && (!Number.isFinite(priorEma90) || ema90Value >= priorEma90)
      && (baseComponent?.launchDistancePercent == null || baseComponent.launchDistancePercent <= 7);
    // 急杀只否定尚未修复的局部反弹，不能永久封死后续重新长成的大级别结构。
    // 1h/4h 若在急杀低点之后独立完成高占用、反复试顶的成熟母平台/三角，
    // 并重新站上上行 EMA90、从真实外沿线下触发，则旧母压力降级为历史背景。
    const postShockStructureStartsAfterLow = !Number.isFinite(Number(motherStructure.shockLowIndex))
      || currentStructureStartIndex >= Number(motherStructure.shockLowIndex) + 4;
    const maturePostShockBase = Boolean(baseComponent)
      && confirmedPlatformBreak
      && (baseComponent.consolidationBars || 0) >= 32
      && (baseComponent.outerEdgeScore || 0) >= 90
      && (baseComponent.quality || 0) >= 0.74
      && baseComponent.clusteredCeilingBand === true
      && (baseComponent.ceilingTouches || 0) >= 8
      && (baseComponent.touchGroups || 0) >= 3;
    const maturePostShockTriangle = Boolean(triangleComponent)
      && (candidate.matureTriangleOuterEdge === true || candidate.directStructuralBoundary === true)
      && (triangleComponent.consolidationBars || 0) >= 32
      && (triangleComponent.quality || 0) >= 0.72
      && (triangleComponent.channelInteriorOccupancy || 0) >= 0.78
      && (triangleComponent.channelSideTransitions || 0) >= 3;
    const matureHigherTimeframePostShockRecovery = ["1h", "4h"].includes(interval)
      && motherStructure.risky === true
      && motherStructure.mode === "shock-formed-mother-box"
      && motherStructure.position >= 0.72
      && postShockStructureStartsAfterLow
      && (maturePostShockBase || maturePostShockTriangle)
      && currentStructureHasPriorAdvance
      && effectiveHorizontalPriorAdvanceAtr >= 4
      && horizontalLaunchContext?.postSelloffRecovery !== true
      && triangleLaunchContext?.postSelloffRecovery !== true
      && horizontalUrgency?.urgent !== true
      && horizontalDwell.insufficient !== true
      && !effectiveAscendingStructureTrap
      && pressureBars >= 2
      && rhythmScore >= 78
      && trendUp
      && Number.isFinite(ema90Value)
      && prior.close > ema90Value
      && (!Number.isFinite(priorEma90) || ema90Value >= priorEma90)
      && current.open < candidate.level
      && current.high >= candidate.level;
    // 主升已经成立或已有明确预期时，主升启动前的大周期下跌旧高低只作背景。
    // 仅允许越过“早于当前独立上推”的无序旧边界；主升后急杀形成的母箱体、
    // 冲高大分歧和当前结构内部的压力仍然保留，防止把环境放宽误用成追涨许可。
    const mainWaveOldDeclinePressureException = motherStructure.risky === true
      && motherStructure.mode === "unordered-mother-box"
      && !strictMotherBox
      && oldBoundaryPrecedesIndependentAdvance
      && mainWaveStructureQualityPermit;
    const structuralExceptionProbe = {
      interval,
      foundationTypes,
      auxiliaryTypes,
      hasPivot,
      consolidationBars: candidate.consolidationBars || consolidation.bars,
      structureShape: candidate.structureShape || null,
      structureQuality: triangleComponent?.quality ?? baseComponent?.quality ?? candidate.quality,
      outerEdgeConfirmed: confirmedPlatformBreak,
      directStructuralBoundary: candidate.directStructuralBoundary === true,
      softTestExtendedTriangle: candidate.softTestExtendedTriangle === true
        || triangleComponent?.softTestExtendedTriangle === true,
      channelInteriorOccupancy: candidate.channelInteriorOccupancy,
      channelMiddleParticipationRatio: candidate.channelMiddleParticipationRatio,
      channelHollowRatio: candidate.channelHollowRatio,
      channelLongestHollowRun: candidate.channelLongestHollowRun,
      channelSideTransitions: candidate.channelSideTransitions,
      triangleHasPriorAdvance: triangleLaunchContext?.hasPriorAdvance === true,
      trianglePriorAdvanceAtr: triangleAdvanceAtr,
      trianglePostSelloffRecovery: trianglePostSelloffRisk,
      outerEdgeScore: baseComponent?.outerEdgeScore || 0,
      platformTouchGroups: baseComponent?.touchGroups || 0,
      ceilingTouches: baseComponent?.ceilingTouches || 0,
      launchDistancePercent: baseComponent?.launchDistancePercent ?? null,
      horizontalLaunchHasPriorAdvance: effectiveHorizontalPriorAdvance,
      horizontalLaunchPriorAdvanceAtr: effectiveHorizontalPriorAdvanceAtr,
      horizontalLaunchQualified,
      horizontalLaunchUrgent: horizontalUrgency?.urgent ?? false,
      horizontalLaunchInsufficientEdgeDwell: horizontalDwell.insufficient,
      horizontalLaunchPostSelloffRecovery: horizontalLaunchContext?.postSelloffRecovery ?? false,
      motherStructureNoise: motherStructure.risky === true,
      motherStructureMode: motherStructure.mode || null,
      motherStructurePosition: motherStructure.position ?? null,
      aboveEma90: Number.isFinite(ema90Value) && prior.close > ema90Value,
      ema90SlopeAtDecision: Number.isFinite(ema90Value) && Number.isFinite(priorEma90)
        ? ema90Value - priorEma90
        : 0,
      crossedLevel: candidate.crossedLevel === true,
      openedBeyondTrigger: candidate.openedBeyondTrigger === true,
      riskStructureShape: effectiveAscendingStructureTrap?.shape || null,
      riskStructureStartIndex: effectiveAscendingStructureTrap?.startIndex ?? null,
      riskStructureBars: effectiveAscendingStructureTrap?.bars ?? null,
      horizontalStructureStartIndex: baseComponent?.structureStartIndex ?? null,
      orderFlowScore,
      relativeVolume,
      klineVelocity,
      rhythmScore,
      sentimentScore,
    };
    const matureOneHourLongTriangleReset = isMatureOneHourLongTriangleReset(structuralExceptionProbe);
    const longBasePreviousHighIgnition = isLongBasePreviousHighIgnition(structuralExceptionProbe);
    const softTestExtendedTriangleBreakout = isSoftTestExtendedTriangleBreakout(structuralExceptionProbe);
    const matureOneHourOuterPlatformReset = isMatureOneHourOuterPlatformReset(structuralExceptionProbe);
    const oneHourRelaunchPivotIgnition = isOneHourRelaunchPivotIgnition(structuralExceptionProbe);
    const oneHourCompactAscendingTriangleIgnition = isOneHourCompactAscendingTriangleIgnition(structuralExceptionProbe);
    const matureFifteenMinuteRetryPlatformIgnition = isMatureFifteenMinuteRetryPlatformIgnition(structuralExceptionProbe);
    const matureFifteenMinutePriorHighTriangleIgnition = isMatureFifteenMinutePriorHighTriangleIgnition(
      structuralExceptionProbe,
    );
    if (matureFifteenMinuteRetryPlatformIgnition) effectiveAscendingStructureTrap = null;
    const motherStructureException = options.newCoinNotFalling === true
      || shockBoxHorizontalLaunchException
      || shockBoxAscendingTriangleException
      || ema90ReclaimContinuation.qualified
      || matureHigherTimeframePostShockRecovery
      || matureOneHourLongTriangleReset
      || matureOneHourOuterPlatformReset
      || softTestExtendedTriangleBreakout
      || matureFifteenMinutePriorHighTriangleIgnition
      || mainWaveOldDeclinePressureException
      || (!strictMotherBox && (candidate.matureTriangleOuterEdge === true || horizontalStructureException));
    if (motherStructure.risky && !motherStructureException) {
      reasons.push(motherStructure.mode === "shock-formed-mother-box"
        ? `仍在急杀形成的母箱体内部：${interval} 母压力 ${motherStructure.motherHigh.toFixed(8)} 尚未突破，局部反弹小结构不作为起爆点`
        : motherStructure.mode === "post-impulse-high-level-rotation"
          ? `仍在冲高后的母压力区间内部：${interval} 回看 ${motherStructure.span} 根，尚未突破真正峰值 ${motherStructure.motherHigh.toFixed(8)}`
          : `仍在更大母箱体内部无序波动：${interval} 回看 ${motherStructure.span} 根，局部触发位仅处于母箱体 ${(motherStructure.position * 100).toFixed(0)}% 位置，未突破真正上沿`);
    }
    // 策略内部已能证明“旧下跌边界失效”，但在尚未收到龙头/人工主升
    // 语境时先保留为待升级候选。applyContextGates 得到龙头语境后才清除该原因。
    if (mainWaveOldDeclinePressureException && !declaredMainWaveStage) {
      reasons.push("上市旧下跌边界已失效，等待龙头或人工主升语境确认");
    }

    const horizontalEdgeReady = Boolean(baseComponent?.edgeReady)
      && baseComponent.consolidationBars >= (foundationTypes.length >= 2 ? 18 : 28)
      && pressureBars >= 2
      && compression <= 1.6;
    const trendlineBackedBaseReady = Boolean(baseComponent)
      && auxiliaryTypes.includes("trendline")
      && baseComponent.consolidationBars >= 40
      && baseComponent.quality >= 0.38
      && pressureBars >= 1
      && compression <= 1.95;
    const baseReady = horizontalEdgeReady
      || trendlineBackedBaseReady
      || confirmedPlatformBreak
      || longBasePreviousHighIgnition
      || matureFifteenMinuteRetryPlatformIgnition
      || matureFifteenMinutePriorHighTriangleIgnition
      || oneHourPlatformPivotReady;
    const triangleReady = Boolean(triangleComponent)
      && triangleComponent.quality >= 0.48
      // 没有静态母平台外沿兜底时，长收敛必须至少完成一次上下区域换边。
      // 只有包络、没有轮动的空心三角不授予独立买点；若真正盘整前高同时
      // 被突破，三角仍可保留为辅助标签，执行权由母平台取得。
      && (confirmedPlatformBreak || (candidate.channelSideTransitions || 0) >= 1)
      && pressureBars >= 2;
    const relaunchReady = Boolean(relaunchComponent)
      && relaunchComponent.quality >= 0.5
      && (relaunchComponent.consolidationBars >= 8 || foundationTypes.length >= 2)
      && pressureBars >= 2;
    // 拐点和前高只能辅助成熟母结构，不能因为通用节奏分较高就在没有横盘、
    // 三角或有效回踩结构时独立开仓。否则一条错误趋势线即使被移除，原位置
    // 仍会换成“拐点 + 前高”的标签继续生成同一个假 B。
    const pivotReadyWithoutFoundation = false;
    const triangleOuterEdgeReady = candidate.matureTriangleOuterEdge === true
      && Boolean(triangleComponent)
      && Boolean(baseComponent)
      && auxiliaryTypes.includes("trendline")
      && auxiliaryTypes.includes("previousHigh")
      && (candidate.consolidationBars || 0) >= 28
      && (candidate.channelInteriorOccupancy || 0) >= 0.55
      && (candidate.channelSideTransitions || 0) >= 3;
    const foundationReady = baseReady || triangleReady || relaunchReady || triangleOuterEdgeReady;
    const matureTrendlineContext = trendlineBackedBaseReady && baseComponent.consolidationBars >= 40;
    const strictLongFallingWedgeRecovery = candidate.structureShape === "falling-wedge"
      && triangleComponent?.triangleLines?.lower?.boundaryModel === "quantile-outer-envelope"
      && (candidate.consolidationBars || 0) >= 180
      && (candidate.channelInteriorOccupancy || 0) >= 0.58
      && (candidate.channelMiddleParticipationRatio || 0) >= 0.3
      && (candidate.channelSideTransitions || 0) >= 3
      && candidate.openedBeyondTrigger !== true;

    if (!confirmedPlatformBreak
      && !strictLongFallingWedgeRecovery
      && !trendUp
      && prior.close < ema90Value - atrValue * 1.1) reasons.push("突破前趋势仍弱");
    if (effectiveAscendingStructureTrap) {
      reasons.push(effectiveAscendingStructureTrap.shape === "rising-wedge"
        ? "前高来自上升楔形末端，不作为起爆突破"
        : "前高来自上升通道末端，不作为起爆突破");
    }
    if (baseComponent && horizontalUrgency?.urgent && !independentMatureTriangle) {
      reasons.push("横盘末段仍在急促斜向推进：低点连续快速抬高、回撤不足，尚未完成横向换手");
    }
    if (baseComponent && horizontalDwell.insufficient && !independentMatureTriangle) {
      reasons.push("所谓母平台只在末端停留数根：不能用更早上涨历史充当横盘长度");
    }
    if (candidate.brokenOuterPlatformContext) {
      reasons.push("所谓母平台中间整段深跌与修复几乎没有沿外沿交易：不能用更早上涨历史充当横盘长度");
    }
    if (baseComponent
      && horizontalLaunchContext?.postSelloffRecovery
      && !horizontalStructureException
      && !shockBoxHorizontalLaunchException
      && !shockBoxAscendingTriangleException
      && !shockMotherBoxOuterEdgeBreakout) {
      reasons.push("平台直接承接快速大下杀且尚未收复，不属于先拉升后盘整的横盘起飞");
    } else if (baseComponent
      && horizontalPreStructureContext?.enoughHistory
      && !horizontalPreStructureContext.qualified
      && !effectiveHorizontalPriorAdvance
      && !ema90ReclaimContinuation.qualified
      && !shockBoxAscendingTriangleException
      && !shockMotherBoxOuterEdgeBreakout) {
      reasons.push("横盘起飞前缺少向上推动，不能把普通横盘或跌后修复当作主升接力");
    }
    if (triangleComponent && trianglePostSelloffRisk) {
      reasons.push("三角结构直接承接未收复的大下杀，属于跌后反弹收敛，不作为主升起爆");
    } else if (triangleComponent
      && triangleLaunchContext?.enoughPriorHistory
      && !triangleLaunchContext.hasPriorAdvance) {
      reasons.push("三角前缺少向上推动，不属于主升浪中继结构");
    }
    if (candidate.insideMotherBase
      && !matureOneHourLongTriangleReset
      && !matureOneHourOuterPlatformReset
      && !softTestExtendedTriangleBreakout) {
      reasons.push(`仍在母箱体内部，只突破附近阳线；必须突破盘整前高 ${candidate.motherBaseLevel.toFixed(8)}`);
    }
    if (!foundationTypes.length && !pivotReadyWithoutFoundation) {
      reasons.push("趋势线与前高仅作辅助，缺少成熟横盘、三角或有效回踩母结构");
    } else if (foundationTypes.length && !foundationReady) {
      reasons.push("母结构尚未成熟：盘整、压缩或贴线蓄力不足");
    }
    if (!confirmedPlatformBreak
      && !triangleOuterEdgeReady
      && !oneHourPlatformPivotReady
      && (pressureDistanceAtr > 2.2 || pressureBars < 1)) reasons.push("突破前未贴近关键位蓄力");
    if (!confirmedPlatformBreak && compression > 1.7 && candidate.confluence.length < 2) reasons.push("结构松散，波动没有收敛");
    if (candidate.type === "previousHigh" && candidate.confluence.length < 2 && efficiencyRatio(candles, priorIndex) < 0.08) {
      reasons.push("单一前高结构往返噪声过高");
    }
    if (candidate.crossedLevel === true
      && current.close <= candidate.triggerPrice
      && ["4h", "1d"].includes(interval)
      && foundationTypes.includes("triangle")
      && !foundationTypes.includes("base")) {
      reasons.push("三角上沿仅被上影线试探，尚未完成结构外沿确认");
    }
    // 买点采用突破前预埋的 stop-cross 模型：触发K一旦从线下穿越即成交，
    // 不能在事后用该根收盘价反向删除已经发生的买点。收盘未站稳由生命周期
    // 记录为第一次试盘，并只在后续重新突破第一次K高点时给红色二次提示。
    if (candidate.openedBeyondTrigger) reasons.push("开盘已越过触发线，非从下向上首次突破");

    const score = Math.round(clamp(
      32
      + rhythmScore * 0.36
      + sentimentScore * 0.18
      + candidate.quality * 10
      + Math.min(Math.max(foundationTypes.length - 1, 0), 2) * 9
      + Number(hasPivot) * 4
      + auxiliaryTypes.length * 2
      - reasons.length * 18,
      0,
      99,
    ));
    const minimumScore = confirmedPlatformBreak ? 52 : foundationTypes.length >= 2 ? 58 : 64;
    const certaintyScore = Math.round(clamp(
      structuralEvidenceScore
      + Number(horizontalEdgeReady) * 8
      + Number(confirmedPlatformBreak) * 10
      + Number(foundationTypes.length >= 2) * 5
      + Number(triangleReady) * 4
      + Number(matureTrendlineContext) * 6
      + Math.min(auxiliaryTypes.length, 2) * 2
      + Number(orderFlowScore >= 65) * 2,
      0,
      99,
    ));
    const evidence = [
      ...candidate.evidence,
      ...(effectiveAscendingStructureTrap?.evidence || []),
      ...(horizontalLaunchContext?.evidence || []),
      ...(triangleAnchoredHorizontalPriorAdvance ? [
        `长母平台起点虽早，但同一成熟三角前已有 ${effectiveHorizontalPriorAdvanceAtr.toFixed(2)} ATR 独立上推；采用更贴近当前结构的因果上推证据`,
      ] : []),
      ...(horizontalStructureException ? horizontalPreStructureContext.evidence : []),
      ...(triangleLaunchContext
        ? trianglePostSelloffRisk
          ? triangleLaunchContext.evidence
          : triangleLaunchContext.hasPriorAdvance
            ? [`三角前已有 ${Math.max(triangleLaunchContext.priorNetAdvanceAtr, triangleLaunchContext.riseIntoBaseAtr).toFixed(2)} ATR 前置上推`]
            : triangleLaunchContext.evidence
        : []),
      ...(declaredMainWaveStructurePermit ? [
        `${declaredMainWaveContextLabel}：只放宽环境门槛，成熟母结构、真实外沿与突破节奏仍须独立通过`,
      ] : []),
      ...(mainWaveOldDeclinePressureException ? [
        `主升浪${mainWaveStage === "active" ? "已经成立" : "预期已建立"}：主升启动前的大周期下跌旧高低只作背景，不否决当前成熟结构的真实突破`,
        "主升后的急杀母箱体、冲高大分歧与当前结构内部压力仍继续过滤",
      ] : []),
      ...(matureHigherTimeframePostShockRecovery ? [
        "大周期急杀后已重新形成独立成熟结构：旧母压力不再一票否决当前真实外沿突破",
        "结构位于急杀低点之后，反复试顶并重新站上上行EMA90；普通局部反弹仍继续过滤",
      ] : []),
      ...(ema90ReclaimContinuation.qualified ? ema90ReclaimContinuation.evidence : []),
      ...(terminalEma90BoundaryReclaim ? [
        `一小时长结构末端收复EMA90：预设结构上沿高于均线 ${((candidate.triggerPrice - ema90Value) / atrValue).toFixed(2)} ATR，突破结构即同步完成均线收复`,
        "此前盘整可位于EMA90附近或短暂下方；只要没有深跌、前置拉升和成熟外沿仍完整，就不要求整个平台始终站在均线上",
      ] : []),
      `所有结构指标截止于触发前一根已收盘 K 线`,
      `关键位下方承接 ${pressureBars}/8 根`,
      `当前周期盘整约 ${consolidation.bars} 根`,
      `末端量能比 ${relativeVolume.toFixed(2)}×，结构节奏 ${rhythmScore} 分`,
      `情绪节奏：${sentimentPhase} ${sentimentScore} 分`,
      `突破前订单流 ${orderFlowScore} 分：大单代理 ${bigTradeRatio.toFixed(2)}×，K 线推进速度 ${klineVelocity.toFixed(2)} ATR/根${hasTakerFlow ? `，主动买量 ${(takerBuyRatio * 100).toFixed(0)}%` : ""}`,
      `结构证据：质量 ${Math.round((candidate.quality || 0) * 100)}%，外沿 ${baseComponent?.outerEdgeScore || 0}，触点 ${Math.max(baseComponent?.ceilingTouches || 0, baseComponent?.touchGroups || 0)}`,
      candidate.openedBeyondTrigger
        ? `本根开盘已越过 ${candidate.triggerPrice.toFixed(8)}，不追入`
        : `本根开盘位于触发线下方，盘中首次向上穿越 ${candidate.triggerPrice.toFixed(8)} 即成交`,
      `结构止损风险 ${structuralRiskPercent.toFixed(2)}%，模拟滑点 ${slippageBps} bps`,
    ];
    const geometryStructureStartIndex = clamp(Math.trunc(
      candidate.triangleLines?.structureStartIndex
      ?? candidate.triangleLines?.upper?.startIndex
      ?? candidate.trendline?.structureStartIndex
      ?? candidate.trendline?.startIndex
      ?? index - Math.min(Math.max(consolidation.bars || 40, 12), 240)
    ), 0, Math.max(0, index - 12));
    // 结构线与视觉因果窗口是两个不同概念：白色结构线只能从拉升后的盘整
    // 高低点开始，但视觉判断必须把“先拉升、再盘整、最后突破”的前置推动
    // 一起看进去。否则 XRP 1h 这类长平台会只剩末端几十根，被误读成没有
    // 拉升起点的普通横盘。这里优先采用已经通过因果审计的上推低点；不合格
    // 的局部反弹、母箱体内部波动和下跌修复不会取得这个向左扩展资格。
    const causalAdvanceContexts = [
      horizontalPreStructureContext,
      triangleComponent?.preStructureContext,
    ].filter((context) => (
      context?.hasPriorAdvance === true
      && context?.bestAdvance?.qualified === true
      && Number.isFinite(Number(context.bestAdvance.pivotIndex))
    ));
    const impulseStartIndex = causalAdvanceContexts.length
      ? Math.min(...causalAdvanceContexts.map((context) => Number(
        context.impulseContextStartIndex ?? context.bestAdvance.pivotIndex
      )))
      : null;
    const causalContextStartIndex = clamp(Math.trunc(
      Number.isFinite(impulseStartIndex)
        ? Math.min(geometryStructureStartIndex, impulseStartIndex)
        : geometryStructureStartIndex
    ), 0, Math.max(0, index - 12));
    const visualStructureStartIndex = causalContextStartIndex;
    if (Number.isFinite(impulseStartIndex) && impulseStartIndex < geometryStructureStartIndex) {
      evidence.push(
        `视觉因果窗口向左覆盖前置拉升 ${geometryStructureStartIndex - impulseStartIndex} 根；结构线仍只从拉升后的盘整起点开始`,
      );
    }
    const visualSignature = Vision?.buildVisualSignature?.(candles, index, {
      interval,
      triggerPrice: candidate.triggerPrice,
      ema90: indicators.ema90,
      structureStartIndex: visualStructureStartIndex,
      structureSource: "strategy",
    }) || null;
    const consolidationBreakout = interval !== "1m"
      && ((foundationTypes.includes("base") && confirmedPlatformBreak)
        || (auxiliaryTypes.includes("previousHigh")
          && (foundationTypes.includes("base") || foundationTypes.includes("triangle"))
          && (confirmedPlatformBreak || triangleReady || triangleOuterEdgeReady))
        || oneHourCompactAscendingTriangleIgnition
        || matureFifteenMinuteRetryPlatformIgnition
        || matureFifteenMinutePriorHighTriangleIgnition);
    const derivedPivotLabel = matureOneHourOuterPlatformReset || oneHourRelaunchPivotIgnition;
    const componentPatternLabels = [...new Set(candidate.confluence.flatMap((type) => {
      if (type === "base" && !horizontalLaunchQualified) return [];
      return [type === "triangle" && candidate.structureShape === "falling-wedge"
        ? "下降楔形突破"
        : PATTERN_LABELS[type]];
    }).concat(
      derivedPivotLabel && !hasPivot ? ["拐点收复"] : [],
      shockMotherBoxOuterEdgeBreakout && !auxiliaryTypes.includes("previousHigh") ? ["突破前高"] : [],
    ))];
    const displayPattern = consolidationBreakout
      ? ["盘整突破", ...componentPatternLabels].join(" + ")
      : componentPatternLabels.join(" + ");
    const base = {
      id: `${interval}-${current.time}-${candidate.confluence.join("+")}`,
      time: current.time,
      decisionTime: current.time,
      index,
      interval,
      pattern: displayPattern,
      patternKey: candidate.type,
      primaryPatternKey: consolidationBreakout ? "consolidationBreakout" : candidate.type,
      consolidationBreakout,
      crossedLevel: candidate.crossedLevel === true,
      openedBeyondTrigger: candidate.openedBeyondTrigger === true,
      insideMotherBase: candidate.insideMotherBase === true,
      featureCutoff: current.time - 1,
      confluence: candidate.confluence,
      foundationTypes,
      auxiliaryTypes,
      hasPivot: hasPivot || derivedPivotLabel,
      price,
      breakoutOpen: current.open,
      breakoutClose: current.close,
      breakoutLow: current.low,
      triggerPrice: candidate.triggerPrice,
      level: candidate.level,
      previousHighLevel: previousHighComponent?.level
        || (shockMotherBoxOuterEdgeBreakout ? candidate.level : null),
      stop: candidate.stop,
      score,
      relativeVolume,
      distanceAtr,
      originDistanceAtr,
      consolidationBars: consolidation.bars,
      outerEdgeConfirmed: confirmedPlatformBreak,
      outerEdgeScore: baseComponent?.outerEdgeScore || 0,
      platformModel: baseComponent?.platformModel || null,
      shockMotherBoxOuterEdge: baseComponent?.shockMotherBoxOuterEdge === true,
      longSwingMotherBox: baseComponent?.longSwingMotherBox === true,
      clusteredCeilingBand: baseComponent?.clusteredCeilingBand === true,
      ceilingBandToleranceAtr: baseComponent?.ceilingBandToleranceAtr ?? null,
      ceilingAge: baseComponent?.ceilingAge || 0,
      ceilingTouches: baseComponent?.ceilingTouches || 0,
      platformTouchGroups: baseComponent?.touchGroups || 0,
      platformEfficiency: baseComponent?.platformEfficiency ?? null,
      launchDistancePercent: baseComponent?.launchDistancePercent ?? null,
      horizontalLaunchUrgent: horizontalUrgency?.urgent ?? false,
      horizontalLaunchTailBars: horizontalUrgency?.bars ?? null,
      horizontalLaunchTailEfficiency: horizontalUrgency?.efficiency ?? null,
      horizontalLaunchTailNetAdvanceAtr: horizontalUrgency?.netAdvanceAtr ?? null,
      horizontalLaunchCloseSlopeAtr: horizontalUrgency?.closeSlopeAtrPerBar ?? null,
      horizontalLaunchLowSlopeAtr: horizontalUrgency?.lowSlopeAtrPerBar ?? null,
      horizontalLaunchNonFallingLowRatio: horizontalUrgency?.nonFallingLowGroupRatio ?? null,
      horizontalLaunchRisingLowRatio: horizontalUrgency?.risingLowGroupRatio ?? null,
      horizontalLaunchMaxPullbackAtr: horizontalUrgency?.maxPullbackAtr ?? null,
      horizontalLaunchInsufficientEdgeDwell: horizontalDwell.insufficient,
      horizontalStructureStartIndex: baseComponent?.structureStartIndex ?? null,
      horizontalStructureStartTime: Number.isFinite(baseComponent?.structureStartIndex)
        ? candles[baseComponent.structureStartIndex]?.time || null
        : null,
      horizontalOuterScanBars: baseComponent?.scannedBars ?? null,
      horizontalDiscardedLeadInBars: baseComponent?.discardedLeadInBars ?? null,
      horizontalBrokenOuterPlatform: Boolean(candidate.brokenOuterPlatformContext),
      horizontalBrokenOuterPlatformBars: candidate.brokenOuterPlatformContext?.scannedBars ?? null,
      horizontalBrokenOuterPlatformDeepRun: candidate.brokenOuterPlatformContext?.longestDeepDepartureRun ?? null,
      rhythmScore,
      sentimentScore,
      sentimentPhase,
      mainWaveStage,
      mainWaveContextSource: declaredMainWaveStage ? declaredMainWaveContextSource : "strategy-inference",
      declaredMainWaveStructurePermit,
      mainWaveOldDeclinePressureException,
      matureHigherTimeframePostShockRecovery,
      matureOneHourLongTriangleReset,
      longBasePreviousHighIgnition,
      softTestExtendedTriangleBreakout,
      matureOneHourOuterPlatformReset,
      oneHourRelaunchPivotIgnition,
      oneHourCompactAscendingTriangleIgnition,
      matureFifteenMinuteRetryPlatformIgnition,
      matureFifteenMinutePriorHighTriangleIgnition,
      oldMotherBoundaryPrecedesIndependentAdvance: oldBoundaryPrecedesIndependentAdvance,
      ema90ReclaimContinuation: ema90ReclaimContinuation.qualified,
      ema90ReclaimStructureStartIndex: ema90ReclaimContinuation.structureStartIndex,
      ema90BreachStartIndex: ema90ReclaimContinuation.breachStartIndex,
      ema90ReclaimIndex: ema90ReclaimContinuation.reclaimIndex,
      ema90ReclaimBelowBars: ema90ReclaimContinuation.belowEmaBars,
      ema90ReclaimBreachSpanBars: ema90ReclaimContinuation.breachSpanBars,
      ema90ReclaimRecoveryBars: ema90ReclaimContinuation.recoveryBars,
      ema90ReclaimDeepestBreachAtr: ema90ReclaimContinuation.deepestBreachAtr,
      ema90ReclaimDeepestBreachPercent: ema90ReclaimContinuation.deepestBreachPercent,
      ema90PostReclaimAboveRatio: ema90ReclaimContinuation.postReclaimAboveRatio,
      ema90ReclaimOverrodeNestedAscendingTrap: ema90ReclaimContinuation.qualified
        && ema90ReclaimContinuation.nestedAscendingTrap,
      terminalEma90BoundaryReclaim,
      contextTokens: [
        ...(ema90ReclaimContinuation.qualified ? ["ema90-reclaim"] : []),
        ...(terminalEma90BoundaryReclaim ? ["terminal-ema90-boundary-reclaim"] : []),
      ],
      orderFlowScore,
      bigTradeRatio,
      klineVelocity,
      takerBuyRatio,
      structuralEvidenceScore,
      certaintyScore,
      aboveEma90: Number.isFinite(ema90Value) && prior.close > ema90Value,
      ema90AtDecision: ema90Value,
      ema90SlopeAtDecision: Number.isFinite(ema90Value) && Number.isFinite(priorEma90)
        ? ema90Value - priorEma90
        : 0,
      atrAtDecision: atrValue,
      structuralRiskPercent,
      positionScale: structuralRiskPercent >= 3 ? 0.5 : 1,
      trendline: candidate.trendline || null,
      triangleLines: candidate.triangleLines || null,
      structureStartIndex: geometryStructureStartIndex,
      impulseStartIndex,
      impulseStartTime: Number.isFinite(impulseStartIndex)
        ? candles[impulseStartIndex]?.time || null
        : null,
      causalContextStartIndex,
      causalContextStartTime: candles[causalContextStartIndex]?.time || null,
      structureShape: candidate.structureShape || null,
      matureTriangleOuterEdge: candidate.matureTriangleOuterEdge === true,
      directStructuralBoundary: candidate.directStructuralBoundary === true,
      softTestExtendedTriangle: candidate.softTestExtendedTriangle === true
        || triangleComponent?.softTestExtendedTriangle === true,
      oneHourPlatformPivotBoundary: candidate.oneHourPlatformPivotBoundary === true,
      oneHourPlatformPivotReady,
      motherStructureNoise: motherStructure.risky && !motherStructureException,
      motherStructureBars: motherStructure.span || null,
      motherStructureHigh: motherStructure.motherHigh || null,
      motherStructureLow: motherStructure.motherLow || null,
      motherStructurePosition: motherStructure.position ?? null,
      motherStructureEfficiency: motherStructure.efficiency ?? null,
      motherStructureMode: motherStructure.mode || null,
      motherShockPeakIndex: motherStructure.shockPeakIndex ?? null,
      motherShockLowIndex: motherStructure.shockLowIndex ?? null,
      shockBoxHorizontalLaunchException,
      shockBoxAscendingTriangleException,
      shockBoxStructureStartIndex: strictShockBaseComponent?.structureStartIndex ?? null,
      shockBoxConsolidationBars: strictShockBaseComponent?.consolidationBars ?? null,
      shockBoxOuterEdgeScore: strictShockBaseComponent?.outerEdgeScore ?? null,
      shockBoxCeilingTouches: strictShockBaseComponent?.ceilingTouches ?? null,
      shockBoxTouchGroups: strictShockBaseComponent?.touchGroups ?? null,
      shockBoxFullPlatformEfficiency: strictShockBaseComponent?.fullPlatformEfficiency ?? null,
      shockBoxPlatformRangePercent: strictShockBaseComponent?.platformRangePercent ?? null,
      shockBoxPlatformPressureRatio: strictShockBaseComponent?.platformPressureRatio ?? null,
      shockBoxLaunchDistancePercent: strictShockBaseComponent?.launchDistancePercent ?? null,
      shockBoxPostSelloffRecovery: strictShockLaunchContext?.postSelloffRecovery ?? null,
      shockBoxPriorAdvanceAtr: strictShockLaunchContext
        ? Math.max(strictShockLaunchContext.priorNetAdvanceAtr, strictShockLaunchContext.riseIntoBaseAtr)
        : null,
      shockBoxReboundAdvanceAtr: shockReboundAdvanceAtr || null,
      shockBoxReboundAdvancePercent: shockReboundAdvancePercent || null,
      oneMinuteMotherBoxNoise: interval === "1m" && motherStructure.risky && !motherStructureException,
      oneMinuteMotherBoxBars: interval === "1m" ? motherStructure.span || null : null,
      oneMinuteMotherBoxHigh: interval === "1m" ? motherStructure.motherHigh || null : null,
      oneMinuteMotherBoxPosition: interval === "1m" ? motherStructure.position ?? null : null,
      visualStructureStartIndex,
      visualStructureStartTime: candles[visualStructureStartIndex]?.time || null,
      visualStructureBars: index - visualStructureStartIndex,
      visualStructureSource: "strategy",
      channelInteriorOccupancy: candidate.channelInteriorOccupancy ?? null,
      channelMiddleParticipationRatio: candidate.channelMiddleParticipationRatio ?? null,
      channelHollowRatio: candidate.channelHollowRatio ?? null,
      channelLongestHollowRun: candidate.channelLongestHollowRun ?? null,
      channelSideTransitions: candidate.channelSideTransitions ?? null,
      riskStructureShape: effectiveAscendingStructureTrap?.shape || null,
      motherPlatformDominatesTerminalAscendingRisk,
      riskStructureStartIndex: effectiveAscendingStructureTrap?.startIndex ?? null,
      riskStructureBars: effectiveAscendingStructureTrap?.bars ?? null,
      riskStructureUpperAtTrigger: effectiveAscendingStructureTrap?.upperAtTrigger ?? null,
      horizontalLaunchHasPriorAdvance: baseComponent ? effectiveHorizontalPriorAdvance : null,
      horizontalLaunchQualified: baseComponent ? horizontalLaunchQualified : null,
      horizontalLaunchRetracementRatio: horizontalLaunchContext?.retracementRatio ?? null,
      horizontalLaunchRetainedAboveHalf: horizontalLaunchContext?.retainedAboveHalf ?? null,
      horizontalLaunchRebuiltNearHighPlatform: horizontalLaunchContext?.rebuiltNearHighPlatform ?? null,
      horizontalLaunchPriorAdvanceSource: triangleAnchoredHorizontalPriorAdvance
        ? "triangle-aligned-prior-advance"
        : horizontalLaunchContext?.hasPriorAdvance === true ? "base-window" : null,
      horizontalLaunchPostSelloffRecovery: horizontalLaunchContext?.postSelloffRecovery ?? null,
      horizontalLaunchPriorAdvanceAtr: baseComponent ? effectiveHorizontalPriorAdvanceAtr : null,
      horizontalLaunchSelloffAtr: horizontalLaunchContext?.selloffAtr ?? null,
      horizontalLaunchSelloffPercent: horizontalLaunchContext?.selloffPercent ?? null,
      horizontalStructureContextMode: horizontalPreStructureContext?.mode ?? null,
      triangleHasPriorAdvance: triangleLaunchContext?.hasPriorAdvance ?? null,
      trianglePostSelloffRecovery: triangleComponent ? trianglePostSelloffRisk : null,
      triangleFastSelloffDetected: triangleLaunchContext?.postSelloffRecovery ?? null,
      trianglePriorAdvanceAtr: triangleLaunchContext
        ? Math.max(triangleLaunchContext.priorNetAdvanceAtr, triangleLaunchContext.riseIntoBaseAtr)
        : null,
      triangleSelloffAtr: triangleLaunchContext?.selloffAtr ?? null,
      triangleSelloffPercent: triangleLaunchContext?.selloffPercent ?? null,
      structuredPivot: candidate.structuredPivot === true,
      pivotStructureStartIndex: candidate.pivotStructureStartIndex ?? null,
      pivotLowIndex: candidate.pivotLowIndex ?? null,
      pivotRetracementRatio: candidate.pivotRetracementRatio ?? null,
      pivotPriorAdvanceAtr: candidate.pivotPriorAdvanceAtr ?? null,
      // 结构质量只描述突破前母结构本身，不受本根是否允许追入、最终分数或
      // 开盘是否已经越线影响。用于保留高质量三角/降楔的结构预确认。
      structureQuality: triangleComponent?.quality ?? candidate.quality,
      visualSignature,
      fillModel: "prearmed-stop-cross-from-below",
      slippageBps,
      evidence,
      reasons,
    };
    const wedgeStructureEvidence = oneHourPostImpulseWedgeStructureScore(base);
    const evaluated = wedgeStructureEvidence > 0
      ? {
        ...base,
        wedgeStructureEvidence,
        certaintyScore: Math.max(base.certaintyScore, Math.min(99, wedgeStructureEvidence + 4)),
        evidence: [
          ...base.evidence,
          `降楔结构证据 ${wedgeStructureEvidence}：覆盖、穿线、拉升起点、触点与EMA90均已分别核验`,
        ],
      }
      : base;
    const matureTriangleOuterEdge = isMatureTriangleOuterEdgeIgnition(evaluated);
    const oneHourPlatformPivotIgnition = isOneHourPlatformPivotIgnition(evaluated);
    const oneMinutePostImpulseLaunch = isOneMinutePostImpulseHorizontalLaunch(evaluated);
    const independentNestedMainWaveStructure = isIndependentNestedMainWaveStructure(evaluated);
    const matureConsolidationBreakout = evaluated.consolidationBreakout === true
      && evaluated.outerEdgeConfirmed === true
      && (evaluated.outerEdgeScore || 0) >= 84
      && (evaluated.consolidationBars || 0) >= 28
      && evaluated.horizontalLaunchHasPriorAdvance === true
      && evaluated.horizontalLaunchPostSelloffRecovery !== true
      && evaluated.horizontalLaunchUrgent !== true
      && (evaluated.launchDistancePercent ?? 99) <= 7;
    const reviewedHigherTimeframeStructureBreak = isReviewedHigherTimeframeStructureBreak(evaluated);
    const gradedEvaluation = matureConsolidationBreakout
      || matureTriangleOuterEdge
      || oneHourPlatformPivotIgnition
      || oneMinutePostImpulseLaunch
      || independentNestedMainWaveStructure
      || matureOneHourLongTriangleReset
      || longBasePreviousHighIgnition
      || softTestExtendedTriangleBreakout
      || matureOneHourOuterPlatformReset
      || oneHourRelaunchPivotIgnition
      || oneHourCompactAscendingTriangleIgnition
      || matureFifteenMinuteRetryPlatformIgnition
      || matureFifteenMinutePriorHighTriangleIgnition
      || reviewedHigherTimeframeStructureBreak
      || shockMotherBoxOuterEdgeBreakout
      || shockBoxHorizontalLaunchException
      || shockBoxAscendingTriangleException
      || ema90ReclaimContinuation.qualified
      ? {
        ...evaluated,
        score: Math.max(evaluated.score || 0, shockBoxHorizontalLaunchException || shockBoxAscendingTriangleException || shockMotherBoxOuterEdgeBreakout || ema90ReclaimContinuation.qualified || independentNestedMainWaveStructure ? 88 : matureConsolidationBreakout || longBasePreviousHighIgnition || softTestExtendedTriangleBreakout || matureOneHourOuterPlatformReset || oneHourCompactAscendingTriangleIgnition || matureFifteenMinuteRetryPlatformIgnition || matureFifteenMinutePriorHighTriangleIgnition || oneMinutePostImpulseLaunch ? 86 : oneHourPlatformPivotIgnition || oneHourRelaunchPivotIgnition ? 82 : 84),
        certaintyScore: Math.max(evaluated.certaintyScore || 0, shockBoxHorizontalLaunchException || shockBoxAscendingTriangleException || shockMotherBoxOuterEdgeBreakout || ema90ReclaimContinuation.qualified || independentNestedMainWaveStructure ? 88 : matureConsolidationBreakout || longBasePreviousHighIgnition || softTestExtendedTriangleBreakout || matureOneHourOuterPlatformReset || oneHourCompactAscendingTriangleIgnition || matureFifteenMinuteRetryPlatformIgnition || matureFifteenMinutePriorHighTriangleIgnition ? 86 : 84),
        independentNestedMainWaveStructure,
        reviewedHigherTimeframeStructureBreak,
        evidence: [
          ...evaluated.evidence,
          ...(matureConsolidationBreakout ? [
            "A+盘整突破：前置拉升后完成28根以上成熟换手，反复确认的母平台外沿与真正前高同K触发",
            "盘整突破是主类；横盘起飞、箱体或三角是其子结构，前高与拐点只作为触发共振",
          ] : []),
          ...(longBasePreviousHighIgnition ? [
            "长平台前高起爆：48根以上盘整已有多组外沿触碰，前置拉升、拐点与真正前高同K触发",
            "外沿分数略低时由长时间换手、上行EMA90和订单流确认共同补足；附近小阳线不能取得这条例外",
          ] : []),
          ...(softTestExtendedTriangleBreakout ? [
            "成熟三角延迟真突破：前一至两根只在上轨附近试盘并收回，当前实体才首次明确脱离结构",
            "试盘K继续计入原盘整，不在第一次轻微越线处提前结束结构或制造假买点",
          ] : []),
          ...(matureOneHourLongTriangleReset ? [
            "一小时长三角重置旧母压力：高占用结构完成多次换边，并从线下突破真实动态上沿",
            "完整母级收敛可先突破动态上沿、再突破静态前高；没有独立前置拉升、EMA90修复或完整轨道占用时不适用",
          ] : []),
          ...(matureOneHourOuterPlatformReset ? [
            "一小时长平台重置旧峰值：60根以上围绕同一外沿充分换手，至少五组触顶并从线下突破真正平台前高",
            "更早冲高峰值只作历史背景；没有高占用外沿、上行EMA90和量流确认时仍继续过滤",
          ] : []),
          ...(oneHourRelaunchPivotIgnition ? [
            "一小时回踩拐点先手：16根以上回踩结构完成后重新越过真前高，不强迫再等待一个重复大箱体",
            "只在EMA90继续上行、结构质量与主升节奏同时成立时执行",
          ] : []),
          ...(oneHourCompactAscendingTriangleIgnition ? [
            "一小时紧凑上升三角：三角与回踩共同成立，内部充分换边且量能、订单流和推进速度同时确认",
            "动态上沿就是当前盘整边界；空心收敛、几根K上楔和无前置拉升不会取得执行权",
          ] : []),
          ...(matureFifteenMinuteRetryPlatformIgnition ? [
            "十五分钟二次起爆：完整平台明显早于末端上升通道，真前高、回踩拐点与强订单流同K触发",
            "末端通道只描述箱体内部行进路径，不覆盖更早、更完整的盘整母结构",
          ] : []),
          ...(matureFifteenMinutePriorHighTriangleIgnition ? [
            "十五分钟静默收敛起爆：36根以上完整包络承接强前置拉升，动态上沿、真正前高与拐点同K从线下突破",
            "安静蓄势不强制提前放量；空心通道、跌后修复、急促抬低点和附近小阳线仍继续过滤",
          ] : []),
          ...(reviewedHigherTimeframeStructureBreak ? [
            "大周期结构事实：1小时/4小时已完成独立前置拉升、完整轨道换边并从线下突破结构上沿",
            "该路径不再被综合分或重复层级门槛二次否决；母箱体、高位分歧、上升楔形与开盘越线仍是硬否决",
          ] : []),
          ...(shockMotherBoxOuterEdgeBreakout ? [
            "急杀母箱体真上沿突破：执行价直接采用当前K开始前已经确定的急杀前母压力",
            "末端局部小前高不再提前触发；只有从线下越过母箱体真正上沿才标记买点",
          ] : []),
          ...(independentNestedMainWaveStructure ? [
            "主升母箱体内的独立子结构：自身已完成前置拉升、成熟盘整与真实外沿突破，不再被更早母箱体机械否决",
            "大母箱体仍作为上方压力背景；本例外不适用于附近小阳线、空心趋势线、急促上楔或超过7%的追高",
          ] : ema90ReclaimContinuation.qualified ? [
            "A+ EMA90修复再启动：主升中短暂失守后重新站回上行均线，并在均线上方完成成熟结构蓄力",
            "形态可以是横盘、箱体、三角、降楔或前高附近盘整；只在真实外沿从线下突破时执行",
          ] : shockBoxAscendingTriangleException ? [
            "A+主升情绪启动：独立上推与区间扩张已经发生，随后形成高占用的成熟上升三角",
            "水平压力、抬高下轨、盘整真前高与拐点在同一根5分钟K线从线下触发；更早的新币急杀高点不再机械否决",
          ] : shockBoxHorizontalLaunchException ? [
            "A+急杀母箱体内横盘起飞：急杀低点后先完成独立反弹，再形成28根以上紧凑平台",
            "只执行反复确认的平台外沿；母箱体内部的三角、趋势线、拐点和附近小前高仍全部过滤",
          ] : matureTriangleOuterEdge ? [
            "A+长盘整外沿共振：成熟三角、趋势线上轨与真正前高在同一根K线内依次触发",
            "安静蓄势期不强制要求提前放量；执行仍须从线下突破且前高距离不超过7%",
          ] : oneHourPlatformPivotIgnition ? [
            "A+一小时平台拐点：前置拉升后长时间盘整，拐点与真正前高在预设边界同K触发",
            "成熟结构在真实边界突破即买，不再额外抬高0.04 ATR；普通局部高点仍保留确认缓冲",
          ] : [
            "A+一分钟横盘起飞：明确拉升后形成成熟箱体，EMA90上行，真正外沿从线下触发",
            "拐点或回踩只视为箱体内部细节，盘面只保留横盘起飞主结构",
          ]),
        ],
      }
      : evaluated;
    const executionHierarchy = assessExecutionHierarchy(gradedEvaluation);
    const hierarchyEvidence = executionHierarchy.permit
      ? [
        `因果层级 ${executionHierarchy.tier}：${executionHierarchy.primaryFoundationLabel}`,
        ...(executionHierarchy.boosters.length
          ? [`辅助共振：${executionHierarchy.boosters.join(" / ")}（只加分，不单独授予买点）`]
          : []),
      ]
      : [];
    const hierarchicalEvaluation = {
      ...gradedEvaluation,
      executionHierarchy,
      executionHierarchyTier: executionHierarchy.tier,
      executionPrimaryFoundation: executionHierarchy.primaryFoundation,
      executionChildStructures: executionHierarchy.childStructures,
      executionBoosters: executionHierarchy.boosters,
      scoreAuthority: "rank-only-after-causal-permit",
      evidence: [...gradedEvaluation.evidence, ...hierarchyEvidence],
    };
    const hierarchyReasons = executionHierarchy.permit
      ? []
      : [`因果层级未通过：${executionHierarchy.missingLabels.join("、") || "缺少成熟母结构真实外沿"}`];
    const effectiveReasons = reviewedHigherTimeframeStructureBreak
      // 上面的结构事实函数已经逐项检查真实外沿、首次突破、阳线、前置因果、
      // 母箱体噪声、风险形态与7%距离。这里清掉的是早期通用模型留下的重复
      // 评分原因，不能让“母结构尚未成熟”与已经画出的完整高周期轨道相冲突。
      ? []
      : independentNestedMainWaveStructure
        ? gradedEvaluation.reasons.filter((reason) => (
        !String(reason).startsWith("仍在母箱体内部")
        && String(reason) !== "母结构尚未成熟：盘整、压缩或贴线蓄力不足"
        && String(reason) !== "三角前缺少向上推动，不属于主升浪中继结构"
        ))
        : gradedEvaluation.reasons;
    const finalReasons = [...effectiveReasons, ...hierarchyReasons];
    const finalEvaluation = { ...hierarchicalEvaluation, reasons: finalReasons };
    if (!candidate.triggered) return { ...finalEvaluation, status: finalReasons.length || gradedEvaluation.score < minimumScore ? "filtered" : "pending" };
    if (finalReasons.length || gradedEvaluation.score < minimumScore) return { ...finalEvaluation, status: "filtered" };
    return { ...finalEvaluation, status: "buy" };
  }

  // 候选层只接住“真实盘整边界已经被突破，但执行环境还差一口气”的点。
  // 这里刻意使用软原因白名单：任何未显式列出的当前或未来否决原因，
  // 都继续视为硬否决，避免候选层把母箱体噪声、急促抬升或无前置拉升复活。
  const RETAINED_BREAKOUT_SOFT_REASONS = Object.freeze([
    "突破前趋势仍弱",
    "开盘已越过触发线，非从下向上首次突破",
    "上市旧下跌边界已失效，等待龙头或人工主升语境确认",
    "母结构尚未成熟：盘整、压缩或贴线蓄力不足",
    "突破前未贴近关键位蓄力",
    "结构松散，波动没有收敛",
  ]);

  function isRetainableConsolidationCandidate(evaluation) {
    if (!evaluation || evaluation.status !== "filtered" || evaluation.crossedLevel !== true) return false;
    const foundations = new Set((evaluation.foundationTypes || []).map(String));
    const auxiliaries = new Set((evaluation.auxiliaryTypes || []).map(String));
    const hasBase = foundations.has("base");
    const hasTriangle = foundations.has("triangle");
    const consolidationBars = Number(evaluation.consolidationBars) || 0;
    const structureQuality = Number(evaluation.structureQuality) || 0;
    const trueBoundaryCross = (hasBase
        && evaluation.outerEdgeConfirmed === true
        && (evaluation.outerEdgeScore || 0) >= 62)
      || (auxiliaries.has("previousHigh")
        && consolidationBars >= 18
        && ((hasBase && structureQuality >= 0.38) || (hasTriangle && structureQuality >= 0.42)));
    if (!trueBoundaryCross) return false;
    if (evaluation.interval === "1m" && !(hasBase
        && evaluation.outerEdgeConfirmed === true
        && (evaluation.outerEdgeScore || 0) >= 84
        && consolidationBars >= 28)) return false;
    // 成熟三角偶尔会以很小的跳空越过动态上沿。它不能作为绿色正式 B，
    // 但若跳空不超过 1 ATR、同时越过真实前高，可进入人工复核候选层；
    // 这样既不追高，也不会让一次已经完成回落重置的结构从审计账本消失。
    const causalGapOpenReview = evaluation.openedBeyondTrigger === true
      && evaluation.directStructuralBoundary === true
      && hasTriangle
      && auxiliaries.has("previousHigh")
      && consolidationBars >= 28
      && Number.isFinite(evaluation.breakoutOpen)
      && evaluation.breakoutOpen - evaluation.triggerPrice <= Math.max(evaluation.atrAtDecision || 0, 1e-8)
      && (evaluation.launchDistancePercent == null || evaluation.launchDistancePercent <= 7);
    if ((evaluation.openedBeyondTrigger === true && !causalGapOpenReview)
      || evaluation.insideMotherBase === true
      || evaluation.motherStructureNoise === true
      || evaluation.horizontalBrokenOuterPlatform === true
      || evaluation.horizontalLaunchUrgent === true
      || evaluation.horizontalLaunchInsufficientEdgeDwell === true
      || evaluation.horizontalLaunchPostSelloffRecovery === true
      || (hasBase && evaluation.horizontalLaunchHasPriorAdvance === false)
      || (hasTriangle && (evaluation.triangleHasPriorAdvance === false
        || evaluation.trianglePostSelloffRecovery === true))
      || Boolean(evaluation.riskStructureShape)
      || Boolean(evaluation.highLevelDistribution)
      || (evaluation.launchDistancePercent != null && evaluation.launchDistancePercent > 7)) return false;
    const reasons = Array.isArray(evaluation.reasons) ? evaluation.reasons : [];
    return reasons.every((reason) => RETAINED_BREAKOUT_SOFT_REASONS.some((allowed) => (
      String(reason) === allowed || String(reason).startsWith(allowed)
    )) || (causalGapOpenReview && String(reason).startsWith("因果层级未通过")));
  }

  function retainedCandidateFrom(evaluation) {
    const candidateReasons = evaluation.reasons?.length
      ? [...evaluation.reasons]
      : ["执行评分未达到正式B点门槛"];
    return {
      ...evaluation,
      id: `${evaluation.id}-retained-candidate`,
      status: "candidate",
      candidateTier: "retained",
      executionAllowed: false,
      consolidationBreakoutCandidate: true,
      candidateReasons,
      featureCutoff: Number.isFinite(evaluation.featureCutoff)
        ? evaluation.featureCutoff
        : Number(evaluation.time) - 1,
      evidence: [...new Set([
        ...(evaluation.evidence || []),
        "高召回候选层已保留该真实盘整边界突破；未通过执行层，因此不显示B、不弹窗、不语音播报",
        "候选判断只使用突破当时及以前数据；突破后的涨幅仅可用于离线复盘标签",
      ])],
    };
  }

  function retainRearmedCandidates(items, candles, indicators) {
    const retained = [];
    [...items].sort((a, b) => a.index - b.index || (b.score || 0) - (a.score || 0)).forEach((item) => {
      const itemAtr = Math.max(indicators.atr[item.index - 1] || item.atrAtDecision || 0, 1e-8);
      const prior = [...retained].reverse().find((existing) => (
        Math.abs(existing.triggerPrice - item.triggerPrice) <= Math.max(
          itemAtr,
          existing.atrAtDecision || 0,
          1e-8,
        ) * 1.5
      ));
      if (!prior) {
        retained.push(item);
        return;
      }
      if (prior.index === item.index) return;
      const between = candles.slice(prior.index + 1, item.index);
      const stopIndexOffset = between.findIndex((row) => row.low <= prior.stop);
      const stopped = stopIndexOffset >= 0;
      const returnedBelow = between.some((row) => (
        row.close < prior.triggerPrice - itemAtr * 0.05
      ));
      const resetIndex = stopped ? prior.index + 1 + stopIndexOffset : -1;
      const rebuildBars = resetIndex >= 0 ? item.index - resetIndex : 0;
      const rebuiltOuterEdge = ((item.outerEdgeConfirmed === true
          && (item.outerEdgeScore || 0) >= 62)
        || ((item.auxiliaryTypes || []).includes("previousHigh")
          && (item.foundationTypes || []).some((type) => ["base", "triangle"].includes(type))
          && (item.structureQuality || 0) >= 0.42))
        && (item.consolidationBars || 0) >= 18
        && (() => {
          const sameMatureDynamicBoundary = item.directStructuralBoundary === true
            && prior.directStructuralBoundary === true
            && item.structureShape === prior.structureShape
            && Number.isFinite(item.triangleLines?.upper?.startIndex)
            && Number.isFinite(prior.triangleLines?.upper?.startIndex)
            && Math.abs(item.triangleLines.upper.startIndex - prior.triangleLines.upper.startIndex) <= 4;
          return item.triggerPrice >= prior.triggerPrice - itemAtr * (sameMatureDynamicBoundary ? 0.25 : 0.08);
        })();
      if (stopped && returnedBelow && rebuildBars >= 1 && rebuiltOuterEdge) retained.push(item);
    });
    return retained;
  }

  function buildSecondaryBreakoutHints(items, candles, indicators, interval, secondaryItems = items) {
    const rows = Array.isArray(candles) ? candles : [];
    const atrValues = indicators?.atr || [];
    const maximumBarsByInterval = {
      "1m": 45,
      "5m": 36,
      "15m": 32,
      "1h": 30,
      "4h": 18,
      "1d": 12,
    };
    const attempts = (Array.isArray(items) ? items : [])
      // 二次突破只能继承一笔真正通过执行层级的正式尝试。被质量门槛过滤、
      // 错误趋势线派生或母箱体内部的候选不能充当“第一次突破”，否则会凭空
      // 制造红色 B。
      .filter((item) => item?.status !== "filtered"
        && item?.status !== "candidate"
        && item?.crossedLevel === true
        && item?.openedBeyondTrigger !== true)
      .filter((item) => assessExecutionHierarchy(item).permit)
      .sort((left, right) => left.index - right.index || (right.score || 0) - (left.score || 0))
      .reduce((selected, item) => {
        const atrValue = Math.max(atrValues[item.index - 1] || item.atrAtDecision || 0, 1e-8);
        const duplicate = selected.some((existing) => (
          existing.index === item.index
          && Math.abs((existing.triggerPrice || existing.level) - (item.triggerPrice || item.level)) <= atrValue * 0.35
        ));
        if (!duplicate) selected.push(item);
        return selected;
      }, []);
    const isLifecycleFilteredCandidate = (item) => item?.status === "filtered"
      && (item.reasons || []).length > 0
      && (item.reasons || []).every((reason) => (
        reason.includes("前次试错止损后尚未形成")
        || reason.includes("同一盘整已有有效买点")
      ));
    // 上升楔形 / 上升通道仍不能成为绿色正式买点。但如果前面已经有一笔
    // 真实、缩量的正式试盘，随后只是短暂洗回母结构内部，那么“重新突破
    // 第一次K高点”可以作为红色防踏空提示。这里不复活风险结构本身，且
    // 后面的短生命周期、母结构守住和量速改善条件仍必须全部通过。
    const isRiskySecondaryOnlyCandidate = (item) => item?.status === "filtered"
      && ["rising-wedge", "ascending-channel"].includes(item?.riskStructureShape)
      && item?.outerEdgeConfirmed === true
      && (item?.foundationTypes || []).includes("base")
      && (item?.consolidationBars || 0) >= 28
      && item?.motherStructureNoise !== true
      && item?.oneMinuteMotherBoxNoise !== true
      && item?.openedBeyondTrigger !== true;
    // 首次正式试盘被止损以后，第二次重新突破“第一次突破K/洗盘前高点”时，
    // 当前局部候选不必重新伪造一套完整母结构。它继承的是第一次已经通过执行层
    // 的母平台，只负责证明：价格确实洗回边界、结构下沿未坏，并以更强量速重新
    // 越过第一次K高点。该路径只能生成红色提示，绝不会升级成绿色自动买点。
    const isStrictStoppedAttemptRecrossCandidate = (item) => item?.status === "filtered"
      && item?.crossedLevel === true
      && item?.openedBeyondTrigger !== true
      && item?.highLevelDistribution !== true
      && item?.oneMinuteMotherBoxNoise !== true
      && item?.riskStructureShape == null
      && ((item?.foundationTypes || []).some((type) => ["base", "relaunch"].includes(type))
        || item?.hasPivot === true
        || (item?.auxiliaryTypes || []).includes("previousHigh"))
      && (item?.score || 0) >= 40
      && ((item?.orderFlowScore || 0) >= 65
        || (item?.relativeVolume || 0) >= 1.15
        || (item?.klineVelocity || 0) >= 0.85);
    const secondCandidates = (Array.isArray(secondaryItems) ? secondaryItems : [])
      .filter((item) => item?.crossedLevel === true && item?.openedBeyondTrigger !== true)
      .filter((item) => item?.status !== "candidate")
      .filter((item) => item?.status !== "filtered"
        || isLifecycleFilteredCandidate(item)
        || isRiskySecondaryOnlyCandidate(item)
        || isStrictStoppedAttemptRecrossCandidate(item))
      .filter((item) => assessExecutionHierarchy(item).permit
        || isRiskySecondaryOnlyCandidate(item)
        || isStrictStoppedAttemptRecrossCandidate(item))
      .sort((left, right) => left.index - right.index || (right.score || 0) - (left.score || 0))
      .reduce((selected, item) => {
        const atrValue = Math.max(atrValues[item.index - 1] || item.atrAtDecision || 0, 1e-8);
        const duplicate = selected.some((existing) => (
          existing.index === item.index
          && Math.abs((existing.triggerPrice || existing.level) - (item.triggerPrice || item.level)) <= atrValue * 0.35
        ));
        if (!duplicate) selected.push(item);
        return selected;
      }, []);
    const hints = [];
    const usedPrimaryIds = new Set();
    secondCandidates.forEach((second) => {
      if (hints.some((hint) => hint.index === second.index)) return;
      const secondAtr = Math.max(atrValues[second.index - 1] || second.atrAtDecision || 0, 1e-8);
      const first = attempts.filter((candidate) => candidate.index < second.index).reverse().find((candidate) => {
        if (usedPrimaryIds.has(candidate.id)) return false;
        const barsApart = second.index - candidate.index;
        const maximumBars = Math.min(
          maximumBarsByInterval[interval] || 32,
          Math.max(8, Math.round((candidate.consolidationBars || 24) * 0.9)),
        );
        if (barsApart < 2 || barsApart > maximumBars) return false;
        const trigger = Number(candidate.triggerPrice || candidate.level);
        const firstWasQuietTest = (candidate.relativeVolume || 0) < 1.15
          && (candidate.orderFlowScore || 0) < 66;
        if (!firstWasQuietTest) return false;
        const between = rows.slice(candidate.index + 1, second.index);
        if (!between.length) return false;
        const washIndex = between.findIndex((row) => (
          row.close <= trigger - secondAtr * 0.03
          || row.low <= trigger - secondAtr * 0.08
        ));
        const returnedToBoundary = washIndex >= 0;
        const lowerLine = candidate.triangleLines?.lower;
        const lowerSlope = Number.isFinite(lowerLine?.startIndex)
          && Number.isFinite(lowerLine?.endIndex)
          && lowerLine.endIndex !== lowerLine.startIndex
          ? (lowerLine.endPrice - lowerLine.startPrice) / (lowerLine.endIndex - lowerLine.startIndex)
          : null;
        // 第一次试盘的执行止损可以被打掉，但这并不等于母结构已经破坏。二次提示
        // 应按当时已经画出的结构下沿判断：价格短暂洗到突破K低点之下，只要仍守住
        // 三角/箱体支撑，随后几根重新突破第一次K或洗盘前新高，就保留红色 B。
        const structureHeld = between.every((row, offset) => {
          const rowIndex = candidate.index + 1 + offset;
          const structuralFloor = Number.isFinite(lowerSlope)
            // 突破以后不能无限外推原三角下轨，否则几根之后支撑线会被机械
            // 抬到价格上方。结构有效性只检查突破时已经存在的最后支撑位置。
            ? lowerLine.startPrice + lowerSlope * (
              Math.min(rowIndex, lowerLine.endIndex) - lowerLine.startIndex
            )
            : Number(candidate.stop || trigger - secondAtr * 2) - secondAtr * 1.15;
          return row.low > structuralFloor - secondAtr * 0.35;
        });
        if (!returnedToBoundary || !structureHeld) return false;
        // 参照线不是第二个候选自己算出的附近阳线，而是第一次突破 K 的高点；
        // 若洗盘前又创了新高，则使用当时已经形成的新高。只有价格回到线下后
        // 再由当前阳线从下向上越过它，才是“防洗踏空”的二次突破。
        const referenceRows = rows.slice(candidate.index, candidate.index + 1 + washIndex);
        const referenceHigh = Math.max(...referenceRows.map((row) => row.high));
        const priorRow = rows[second.index - 1];
        const secondRow = rows[second.index];
        const crossedReference = priorRow.close <= referenceHigh + secondAtr * 0.03
          && secondRow.open < referenceHigh + secondAtr * 0.04
          && secondRow.high >= referenceHigh + secondAtr * 0.04
          && secondRow.close > referenceHigh
          && secondRow.close > secondRow.open;
        if (!crossedReference) return false;
        // 已经独立通过长平台真前高审计，或明确属于“软试盘后实体首次脱离”
        // 的成熟三角，本根本身就是新的绿色正式买点，不降级成红色防踏空提示。
        if (second.longBasePreviousHighIgnition === true
          || second.softTestExtendedTriangleBreakout === true
          || second.matureOneHourOuterPlatformReset === true
          || second.oneHourRelaunchPivotIgnition === true
          || second.oneHourCompactAscendingTriangleIgnition === true
          || second.matureFifteenMinuteRetryPlatformIgnition === true
          || second.shockMotherBoxOuterEdge === true) return false;
        // 第二次候选若已经形成明显更高、独立成熟的母平台外沿，它是新的
        // 绿色正式买点，不应被降级成“重破第一次K高点”的红色防踏空提示。
        const independentPlatformReset = second.outerEdgeConfirmed === true
          && (second.outerEdgeScore || 0) >= 62
          && (second.consolidationBars || 0) >= 28
          && (second.ceilingTouches || 0) >= 5
          && (second.platformTouchGroups || 0) >= 3
          && Number.isFinite(second.structureStartIndex)
          && Number.isFinite(candidate.structureStartIndex)
          && second.structureStartIndex >= candidate.structureStartIndex
            + Math.max(8, Math.round((candidate.consolidationBars || 24) * 0.2));
        if ((second.status !== "filtered" && independentPlatformReset) || (second.outerEdgeConfirmed === true
          && (second.outerEdgeScore || 0) >= 62
          && Number(second.triggerPrice || second.level) >= referenceHigh + secondAtr * 0.6)) {
          return false;
        }
        const sameStructure = (
          Number.isFinite(candidate.triangleLines?.upper?.startIndex)
          && Number.isFinite(second.triangleLines?.upper?.startIndex)
          && candidate.structureShape === second.structureShape
          && Math.abs(
            candidate.triangleLines.upper.startIndex - second.triangleLines.upper.startIndex,
          ) <= 6
        ) || Math.abs(Number(second.level) - trigger) <= Math.max(secondAtr * 1.3, trigger * 0.0035)
          // 二次提示真正需要重破的是第一次突破K/洗盘前高点。局部拐点模型
          // 可能给当前K附上一个略高的新触发价，不能因此把已经完成的参考高点
          // 重破误判成“另一套结构”；母结构继承资格仍由前面的止损、守底、
          // 短生命周期和量速改善共同约束。
          || isStrictStoppedAttemptRecrossCandidate(second);
        if (!sameStructure) return false;
        const flowImproved = (second.orderFlowScore || 0) >= (candidate.orderFlowScore || 0) + 8;
        const volumeImproved = (second.relativeVolume || 0) >= Math.max(1.12, (candidate.relativeVolume || 0) * 1.15);
        const velocityImproved = (second.klineVelocity || 0) >= (candidate.klineVelocity || 0) + 0.12;
        const firstRow = rows[candidate.index];
        // 在历史高波动阶段，滚动量能和ATR会把一根肉眼很强的二次突破K
        // 归一化得偏低。只要它在洗盘后以明显阳线实体重新越过第一次K高点，
        // 且原始成交量也显著高于第一次试盘，就承认“价格运动本身的改善”。
        // 这条只生成红色提示，不会把它升级成绿色自动执行买点。
        const directCandleExpansion = secondRow.close - secondRow.open >= secondAtr * 1.2
          && secondRow.close >= referenceHigh + secondAtr * 0.55
          && Number(firstRow?.volume) > 0
          && secondRow.volume >= firstRow.volume * 1.25;
        if (!(flowImproved || volumeImproved || velocityImproved || directCandleExpansion)) return false;
        candidate.secondaryDirectCandleExpansion = directCandleExpansion;
        candidate.secondaryReferenceHigh = referenceHigh;
        candidate.secondaryWashBars = washIndex + 1;
        return true;
      });
      if (!first) return;
      usedPrimaryIds.add(first.id);
      const hierarchy = assessExecutionHierarchy(second);
      hints.push({
        ...second,
        id: `${second.id}-secondary-breakout-hint`,
        status: "secondary-hint",
        secondaryBreakoutHint: true,
        alertOnly: true,
        executionAllowed: false,
        reasons: [],
        markerColor: "red",
        primaryAttemptId: first.id,
        primaryAttemptTime: first.time,
        primaryAttemptIndex: first.index,
        secondaryReferenceHigh: first.secondaryReferenceHigh,
        secondaryWashBars: first.secondaryWashBars,
        executionHierarchy: hierarchy,
        executionHierarchyTier: hierarchy.tier,
        pattern: `二次突破提示 · ${String(second.pattern || "盘整突破").replace(/^二次突破提示\s*·\s*/, "")}`,
        featureCutoff: Number(second.time) - 1,
        evidence: [...new Set([
          ...(second.evidence || []),
          `第一次只完成缩量试盘：量能比 ${(first.relativeVolume || 0).toFixed(2)}×，订单流 ${Math.round(first.orderFlowScore || 0)} 分`,
          `价格回到母平台边界附近但未破坏结构，${first.secondaryWashBars} 根后重新突破第一次突破K/洗盘前新高 ${Number(first.secondaryReferenceHigh).toFixed(8)}`,
          "同一结构的二次提示使用短生命周期；相隔过远或依赖已过滤候选的信号一律不生成",
          `二次突破前订单流 ${Math.round(second.orderFlowScore || 0)} 分、量能比 ${(second.relativeVolume || 0).toFixed(2)}×、推进速度 ${(second.klineVelocity || 0).toFixed(2)} ATR/根`,
          ...(first.secondaryDirectCandleExpansion
            ? ["二次K以放大实体和原始成交量重新突破第一次K高点，价格运动确认强于首次试盘"]
            : []),
          "红色B属于提示层：可弹窗和语音提醒，但不是绿色正式买点，也不进入自动执行",
        ])],
      });
    });
    return hints;
  }

  function structureLifecycleDecision(signals, evaluation, candles, index, atrValue) {
    const memoryBars = Math.max(240, Math.min(2_000, Math.round((evaluation.consolidationBars || 32) * 20)));
    const evaluationStructureStart = evaluation.triangleLines?.upper?.startIndex
      ?? evaluation.triangleLines?.structureStartIndex;
    const sameZone = signals.filter((priorSignal) => (
      index - priorSignal.index <= memoryBars
      && (
        Math.abs(evaluation.level - priorSignal.level) <= Math.max(
          atrValue,
          priorSignal.atrAtDecision || 0,
          1e-8,
        ) * 1.5
        // 同一条三角/楔形上沿不能因为动态线价格逐步变化，就被误认成多个
        // 新结构反复开仓。只有原结构真正失效后按二次突破状态机重入，或
        // 后续结构已经换了新的起点与边界，才重新武装。
        || (
          Number.isFinite(evaluationStructureStart)
          && Number.isFinite(priorSignal.triangleLines?.upper?.startIndex)
          && evaluation.structureShape === priorSignal.structureShape
          && Math.abs(evaluationStructureStart - priorSignal.triangleLines.upper.startIndex) <= 4
        )
        // 完整1小时长收敛先突破动态上沿，下一根再越过静态母平台前高时，
        // 第二根本身是 base 候选、没有 triangleLines。仍需把它识别为同一
        // 结构的二级确认，否则会丢失多周期盘面上的结构语义与第二个B。
        || (
          index - priorSignal.index <= 2
          && isMatureOneHourLongTriangleReset(priorSignal)
          && (evaluation.foundationTypes || []).includes("base")
          && evaluation.outerEdgeConfirmed === true
          && evaluation.triggerPrice > priorSignal.triggerPrice
        )
      )
    ));
    if (!sameZone.length) return { reason: "", retryMaturity: false };
    const priorSignal = sameZone.at(-1);
    const priorStructureStart = priorSignal.triangleLines?.upper?.startIndex;
    const currentStructureStart = evaluation.triangleLines?.upper?.startIndex;
    const stagedWedgeSameGeometry = index - priorSignal.index <= 2
      && priorSignal.structureShape === "falling-wedge"
      && evaluation.structureShape === "falling-wedge"
      && (priorSignal.foundationTypes || []).includes("triangle")
      && !(priorSignal.outerEdgeConfirmed)
      && (evaluation.foundationTypes || []).includes("base")
      && evaluation.outerEdgeConfirmed === true
      && Number.isFinite(priorStructureStart)
      && Number.isFinite(currentStructureStart)
      && Math.abs(currentStructureStart - priorStructureStart) <= 4
      && evaluation.triggerPrice >= priorSignal.triggerPrice + Math.max(
        atrValue * 0.25,
        priorSignal.triggerPrice * 0.002,
      );
    const stagedWedgeOuterPlatform = index - priorSignal.index <= 2
      && evaluation.interval === "1h"
      && ["falling-wedge", "converging-triangle"].includes(priorSignal.structureShape)
      && (priorSignal.foundationTypes || []).includes("triangle")
      && !(priorSignal.outerEdgeConfirmed)
      && (evaluation.foundationTypes || []).includes("base")
      && evaluation.outerEdgeConfirmed === true
      && evaluation.triggerPrice >= priorSignal.triggerPrice + Math.max(
        atrValue * 0.25,
        priorSignal.triggerPrice * 0.002,
      )
      && (isOneHourPostImpulseWedgeIgnition(priorSignal)
        || isMatureOneHourLongTriangleReset(priorSignal));
    const stagedWedgeConfirmation = stagedWedgeSameGeometry || stagedWedgeOuterPlatform;
    if (stagedWedgeConfirmation) {
      return {
        reason: "",
        retryMaturity: true,
        retryEvidence: "同一1小时降楔先突破动态上轨，随后突破静态盘整前高；两级触发价独立预设，保留第二个确认买点",
        inheritStructureContext: stagedWedgeOuterPlatform ? {
          structureShape: priorSignal.structureShape,
          triangleLines: priorSignal.triangleLines,
          trendline: priorSignal.trendline,
          structureQuality: Math.max(evaluation.structureQuality || 0, priorSignal.structureQuality || 0),
          score: Math.max(evaluation.score || 0, priorSignal.score || 0),
          certaintyScore: Math.max(evaluation.certaintyScore || 0, priorSignal.certaintyScore || 0),
          rhythmScore: Math.max(evaluation.rhythmScore || 0, priorSignal.rhythmScore || 0),
          sentimentScore: Math.max(evaluation.sentimentScore || 0, priorSignal.sentimentScore || 0),
          channelInteriorOccupancy: priorSignal.channelInteriorOccupancy,
          channelMiddleParticipationRatio: priorSignal.channelMiddleParticipationRatio,
          channelHollowRatio: priorSignal.channelHollowRatio,
          channelLongestHollowRun: priorSignal.channelLongestHollowRun,
          channelSideTransitions: priorSignal.channelSideTransitions,
          triangleHasPriorAdvance: priorSignal.triangleHasPriorAdvance,
          trianglePriorAdvanceAtr: priorSignal.trianglePriorAdvanceAtr,
          foundationTypes: [...new Set([...(evaluation.foundationTypes || []), "triangle"])],
          auxiliaryTypes: [...new Set([...(evaluation.auxiliaryTypes || []), "trendline", "previousHigh"])],
          confluence: [...new Set([...(evaluation.confluence || []), "triangle", "trendline", "previousHigh"])],
          pattern: priorSignal.structureShape === "falling-wedge"
            ? "盘整突破 + 横盘起飞 + 下降楔形突破 + 趋势线突破 + 突破前高"
            : "盘整突破 + 横盘起飞 + 三角突破 + 趋势线突破 + 突破前高",
        } : null,
      };
    }
    // PI 2025-02-26 10:00 一类结构会在同一根1小时K线中先越过动态
    // 三角上轨、再越过真正盘整前高。较低的动态上轨候选不能先占用
    // “同一盘整已买”名额，进而删掉更高、更完整的A+外沿确认。
    const stagedTriangleOuterEdgeConfirmation = index - priorSignal.index <= 2
      && ["converging-triangle", "ascending-triangle", "falling-wedge"].includes(evaluation.structureShape)
      && evaluation.structureShape === priorSignal.structureShape
      && (priorSignal.foundationTypes || []).includes("triangle")
      && (evaluation.foundationTypes || []).includes("triangle")
      && (evaluation.auxiliaryTypes || []).includes("previousHigh")
      && !(priorSignal.auxiliaryTypes || []).includes("previousHigh")
      && Number.isFinite(priorStructureStart)
      && Number.isFinite(currentStructureStart)
      && Math.abs(currentStructureStart - priorStructureStart) <= 4
      && evaluation.triggerPrice >= priorSignal.triggerPrice + Math.max(
        Math.max(atrValue, priorSignal.atrAtDecision || 0, 1e-8) * 0.18,
        priorSignal.triggerPrice * 0.002,
      )
      && isMatureTriangleOuterEdgeIgnition(evaluation);
    if (stagedTriangleOuterEdgeConfirmation) {
      return {
        reason: "",
        retryMaturity: true,
        replacePriorId: index === priorSignal.index ? priorSignal.id : null,
        retryEvidence: "同一成熟三角先越过动态上轨、再突破真正盘整前高；较低候选不阻断A+外沿确认",
      };
    }
    // 同一根或下一根 K 线先越过较低的临时外沿、随后才突破更高且更完整的
    // 母平台外沿时，只保留后者。前者不是额外买点，而是结构尚未完成时的
    // 提前试探；这覆盖 RAVE 2026-04-13 12:10 -> 12:15 的外沿升级。
    const stagedMotherPlatformOuterEdgeUpgrade = index - priorSignal.index <= 2
      && priorSignal.outerEdgeConfirmed === true
      && evaluation.outerEdgeConfirmed === true
      && (evaluation.foundationTypes || []).includes("base")
      && (evaluation.consolidationBars || 0) >= (priorSignal.consolidationBars || 0) + 8
      && evaluation.triggerPrice >= priorSignal.triggerPrice + Math.max(
        Math.max(atrValue, priorSignal.atrAtDecision || 0, 1e-8) * 0.25,
        priorSignal.triggerPrice * 0.002,
      )
      && (evaluation.outerEdgeScore || 0) >= 72
      && (evaluation.orderFlowScore || 0) >= 65
      && (evaluation.klineVelocity || 0) >= 1
      && evaluation.openedBeyondTrigger !== true
      && !evaluation.riskStructureShape;
    if (stagedMotherPlatformOuterEdgeUpgrade) {
      return {
        reason: "",
        retryMaturity: true,
        replacePriorId: priorSignal.id,
        retryEvidence: "同一段母平台先试探较低临时外沿，下一根才突破更高且更完整的真实外沿；撤销提前信号并保留最终起爆K",
      };
    }
    // 独立形成的长三角已经是一个新的母结构。它是否重新武装不应取决于
    // 旧突破K低点有没有在期间被回踩：否则把生命周期止损收紧到突破K低点
    // 后，会反而压掉已经成熟的 83 根新结构。先判定结构独立性，再进入旧
    // 交易的止损/占位分支。
    const independentNestedStructureReset = evaluation.independentNestedMainWaveStructure === true
      && evaluation.interval === "5m"
      && index - priorSignal.index >= 24
      && (evaluation.foundationTypes || []).includes("triangle")
      && evaluation.directStructuralBoundary === true
      && (evaluation.consolidationBars || 0) >= 72
      && (evaluation.structureQuality || 0) >= 0.84
      && (evaluation.channelInteriorOccupancy || 0) >= 0.65
      && (evaluation.relativeVolume || 0) >= 1.05
      && (evaluation.score || 0) >= 88
      && (evaluation.certaintyScore || 0) >= 88;
    if (independentNestedStructureReset) {
      return {
        reason: "",
        retryMaturity: true,
        retryEvidence: `前一买点之后已经独立形成 ${evaluation.consolidationBars} 根成熟三角；新的真实外沿按新机会重新武装`,
      };
    }
    // 1h / 4h 的同一大结构可能先小幅试破，随后在结构继续扩展、真正的
    // 起爆阳线到来时再次突破。后者不能因为前面已有一个候选就被固定冷却
    // 压掉；同时也不能把沿着同一条线的普通反复穿越全部放出来。因此要求
    // 已审阅的大周期完整结构显著扩展，并由实体或量能确认这次是新的推进。
    const priorBars = Math.max(1, priorSignal.consolidationBars || 0);
    const breakoutBody = Number(evaluation.breakoutClose) - Number(evaluation.breakoutOpen);
    const decisiveHigherTimeframeExpansion = evaluation.reviewedHigherTimeframeStructureBreak === true
      && ["1h", "4h"].includes(evaluation.interval)
      && (evaluation.consolidationBars || 0) >= Math.max(36, priorBars * 1.35)
      // 后续形成的结构可以比第一次更长、更完整，但结构分不一定更高；
      // GMT 的 74 根下降楔形就是典型例子。只要求它自身通过成熟线，不能
      // 再拿前一个不同结构的分数做相对比较。
      && (evaluation.structureQuality || 0) >= 0.68
      && (evaluation.directStructuralBoundary === true
        || evaluation.matureTriangleOuterEdge === true)
      && (breakoutBody >= Math.max(atrValue, 1e-8) * 0.6
        || (evaluation.relativeVolume || 0) >= 1.15
        || (evaluation.orderFlowScore || 0) >= 65);
    if (decisiveHigherTimeframeExpansion) {
      return {
        reason: "",
        retryMaturity: true,
        retryEvidence: "1小时/4小时完整结构继续扩展后出现新的决定性突破；按结构事实重新武装，不用固定冷却根数压掉新机会",
      };
    }
    const reviewedImmediateStructureReset = evaluation.matureOneHourLongTriangleReset === true
      || evaluation.matureOneHourOuterPlatformReset === true
      || evaluation.oneHourRelaunchPivotIgnition === true
      || evaluation.oneHourCompactAscendingTriangleIgnition === true
      || evaluation.matureFifteenMinuteRetryPlatformIgnition === true
      || evaluation.shockMotherBoxOuterEdge === true;
    if (reviewedImmediateStructureReset) {
      return {
        reason: "",
        retryMaturity: true,
        retryEvidence: "新的独立高质量结构已经完成并突破；按结构事实立即重新武装，不机械等待固定根数",
      };
    }
    const afterEntry = candles.slice(priorSignal.index + 1, index);
    const breakoutBarLow = Number(priorSignal.breakoutLow)
      || Number(candles[priorSignal.index]?.low);
    const configuredStop = Number(priorSignal.stop);
    // “防洗踏空”的生命周期应比仓位风控更灵敏：第一次突破K低点一旦被
    // 后续K线打穿，就说明这次外沿试探已经失败，允许状态机寻找二次突破。
    // 页面上的实际止损仍沿用策略 stop；这里只用两者中更高、更严格的一条
    // 判断旧买点是否仍占用同一盘整，避免 WLD 2024-02-16 04:00 一类已经
    // 被洗掉的试盘长期压住后面的红色提示。
    const lifecycleStop = Number.isFinite(breakoutBarLow) && Number.isFinite(configuredStop)
      ? Math.max(breakoutBarLow, configuredStop)
      : (Number.isFinite(breakoutBarLow) ? breakoutBarLow : configuredStop);
    const stopOffset = afterEntry.findIndex((row) => (
      Number.isFinite(lifecycleStop) && row.low <= lifecycleStop
    ));
    if (stopOffset < 0) {
      const triggerUpgrade = evaluation.triggerPrice >= priorSignal.triggerPrice + Math.max(
        atrValue,
        priorSignal.atrAtDecision || 0,
        1e-8,
      ) * 0.18;
      const structureUpgrade = (evaluation.confluence?.length || 1) > (priorSignal.confluence?.length || 1)
        || (evaluation.patternKey === "relaunch" && priorSignal.patternKey === "pivot")
        || (evaluation.certaintyScore || 0) >= (priorSignal.certaintyScore || 0) + 6
        || (evaluation.outerEdgeScore || 0) >= (priorSignal.outerEdgeScore || 0) + 8;
      const returnedBelowTrigger = afterEntry.some((row) => row.close < priorSignal.triggerPrice - Math.max(
        atrValue,
        priorSignal.atrAtDecision || 0,
        1e-8,
      ) * 0.08);
      const matureBaseContinues = evaluation.consolidationBars >= 40;
      const sameOrHigherTrigger = evaluation.triggerPrice >= priorSignal.triggerPrice - Math.max(
        atrValue,
        priorSignal.atrAtDecision || 0,
        1e-8,
      ) * 0.15;
      const confirmedOuterPlatform = Boolean(evaluation.outerEdgeConfirmed)
        && (evaluation.outerEdgeScore || 0) >= 62
        && (evaluation.consolidationBars || 0) >= 18
        && ((evaluation.ceilingAge || 0) >= 3 || (evaluation.platformTouchGroups || 0) >= 2);
      const materiallyHigherOuterEdge = evaluation.triggerPrice >= priorSignal.triggerPrice + Math.max(
        Math.max(atrValue, priorSignal.atrAtDecision || 0, 1e-8) * 0.22,
        priorSignal.triggerPrice * 0.0035,
      );
      const priorWasStaticOuterEdge = priorSignal.outerEdgeConfirmed === true;
      if (confirmedOuterPlatform
        && index - priorSignal.index >= (priorWasStaticOuterEdge ? 8 : 2)
        && (materiallyHigherOuterEdge || (returnedBelowTrigger && evaluation.consolidationBars >= 18))) {
        return {
          reason: "",
          retryMaturity: true,
          retryEvidence: `已形成新的盘整外沿（${evaluation.consolidationBars} 根），更新后的盘整前高重新武装`,
        };
      }
      if (index - priorSignal.index >= 2 && returnedBelowTrigger && matureBaseContinues && sameOrHigherTrigger) {
        return { reason: "", retryMaturity: true };
      }
      if (index - priorSignal.index >= 4 && triggerUpgrade && returnedBelowTrigger && (matureBaseContinues || structureUpgrade)) {
        return { reason: "", retryMaturity: true };
      }
      if (index - priorSignal.index >= 4 && triggerUpgrade && structureUpgrade) {
        return { reason: "", retryMaturity: true };
      }
      return { reason: "同一盘整已有有效买点，尚未回落重置并突破更新后的前高", retryMaturity: false };
    }
    const stopIndex = priorSignal.index + 1 + stopOffset;
    const rebuildBars = index - stopIndex;
    const priorStructureBars = Math.max(
      priorSignal.consolidationBars || 0,
      16,
    );
    const triggerAdvanced = evaluation.triggerPrice >= priorSignal.triggerPrice + Math.max(
      atrValue,
      priorSignal.atrAtDecision || 0,
      1e-8,
    ) * 0.08;
    const lifecycleAtr = Math.max(atrValue, priorSignal.atrAtDecision || 0, 1e-8);
    const pressureUpgradeAtr = (evaluation.triggerPrice - priorSignal.triggerPrice) / lifecycleAtr;
    const motherStructureBars = evaluation.consolidationBars || 0;
    const motherStructureMature = motherStructureBars >= Math.max(
      28,
      Math.round(priorStructureBars * 2),
    );
    const motherStructureBreakout = motherStructureMature
      && pressureUpgradeAtr >= 0.55
      && (evaluation.confluence?.length || 1) >= 3
      && evaluation.score >= 80;
    const immediateOuterEdgeRetry = Boolean(priorSignal.outerEdgeConfirmed)
      && Boolean(evaluation.outerEdgeConfirmed)
      && rebuildBars >= 1
      && rebuildBars <= 4
      && motherStructureBars >= 20
      && (evaluation.outerEdgeScore || 0) >= 65
      && (evaluation.platformTouchGroups || 0) >= 2
      && evaluation.triggerPrice >= priorSignal.triggerPrice + lifecycleAtr * 0.05;
    if (immediateOuterEdgeRetry) {
      return {
        reason: "",
        retryMaturity: true,
        retryEvidence: `前次外沿试错已止损；更新后的盘整前高在 ${rebuildBars} 根内再次从线下触发`,
      };
    }
    // 新机会由结构事实决定，而不是机械等待固定根数。长平台真正前高与拐点
    // 同K触发，或成熟三角经历一至两根软试盘后由实体首次脱离，已经分别通过
    // 独立的严格结构审计；即使此前有小级别试错，也应在本根立即重新武装。
    if (evaluation.longBasePreviousHighIgnition === true) {
      return {
        reason: "",
        retryMaturity: true,
        retryEvidence: `前次试错后，长平台已完成多组外沿确认；真正前高与拐点从线下同K突破，按新的独立机会立即重入`,
      };
    }
    if (evaluation.softTestExtendedTriangleBreakout === true) {
      return {
        reason: "",
        retryMaturity: true,
        retryEvidence: `前次轻微越线只作试盘；当前实体首次明确脱离延续中的成熟三角上轨，立即重新武装`,
      };
    }
    if (evaluation.matureFifteenMinutePriorHighTriangleIgnition === true
      || isMatureFifteenMinutePriorHighTriangleIgnition(evaluation)) {
      return {
        reason: "",
        retryMaturity: true,
        retryEvidence: `前次试错后，十五分钟静默收敛已经独立完成强前置拉升、完整包络及真前高重置；当前同K突破按新机会立即重新武装`,
      };
    }
    // 前次试错止损后，若当前已经在主升母箱体内形成一个完全独立的长三角，
    // 新机会的成立依据是“新的结构质量”，不是等待固定根数或必须高于旧触发线。
    // 这里沿用独立子结构的全部严格门槛，并额外要求已经完成止损后的重建、
    // 量速不弱和真实动态上沿突破；普通原位反抽不会被重新武装。
    const independentNestedTriangleRearm = evaluation.independentNestedMainWaveStructure === true
      && evaluation.interval === "5m"
      && (evaluation.foundationTypes || []).includes("triangle")
      && evaluation.directStructuralBoundary === true
      && (evaluation.consolidationBars || 0) >= 72
      && (evaluation.structureQuality || 0) >= 0.84
      && rebuildBars >= 4
      && (evaluation.relativeVolume || 0) >= 1.05
      && (evaluation.orderFlowScore || 0) >= 55
      && (evaluation.score || 0) >= 88
      && (evaluation.certaintyScore || 0) >= 88;
    if (independentNestedTriangleRearm) {
      return {
        reason: "",
        retryMaturity: true,
        retryEvidence: `前次试错止损后已重建 ${rebuildBars} 根；新的长三角独立完成强前置拉升、轨道压缩与真实外沿突破，按新机会重新武装`,
      };
    }
    const exploratoryPriorImproved = Number.isFinite(priorSignal.certaintyScore)
      && !isHighCertaintyEntry(priorSignal)
      && rebuildBars >= 4
      && (evaluation.certaintyScore || 0) >= (priorSignal.certaintyScore || 0) + 6
      && ((evaluation.outerEdgeScore || 0) >= (priorSignal.outerEdgeScore || 0) + 6
        || (evaluation.structureQuality || 0) >= (priorSignal.structureQuality || 0) + 0.08);
    if (exploratoryPriorImproved) {
      return {
        reason: "",
        retryMaturity: true,
        retryEvidence: `前次只是低确定性试错；重建 ${rebuildBars} 根后结构评分明显升级，不阻断新的高质量触发`,
      };
    }
    // 止损之后是否重入，由“有没有形成新的高质量机会”决定，而不是由已经
    // 等待了多少根K决定。新的母平台真实外沿或成熟三角外沿一旦从线下突破，
    // 且压力位、结构质量与主升节奏同步升级，就立即重新武装；同一位置的普通
    // 反抽因为缺少独立外沿与质量升级仍会被拦截。
    const executionHierarchy = evaluation.executionHierarchy || assessExecutionHierarchy(evaluation);
    const independentMotherPlatform = executionHierarchy.primaryFoundation === "mother-platform-breakout"
      && evaluation.outerEdgeConfirmed === true
      && (evaluation.outerEdgeScore || 0) >= 66
      && ((evaluation.platformTouchGroups || 0) >= 2 || (evaluation.ceilingAge || 0) >= 3);
    const independentTriangleEdge = executionHierarchy.primaryFoundation === "mature-triangle-outer-edge"
      && (evaluation.matureTriangleOuterEdge === true || evaluation.directStructuralBoundary === true);
    const independentCompactParent = executionHierarchy.primaryFoundation === "compact-one-hour-parent-platform"
      && evaluation.interval === "1h"
      && (evaluation.consolidationBars || 0) >= 12;
    // 节奏分只能用于衡量机会质量，不能重新变成一条僵硬的等待门槛。若母平台
    // 本身已经足够成熟、外沿清晰且处在主升浪，允许极强的结构质量补偿 1～2 分
    // 的节奏偏差；普通结构仍须满足原来的节奏线，避免把连续扫单放回来。
    const eliteOuterPlatformQuality = independentMotherPlatform
      && evaluation.mainWaveStage === "active"
      && motherStructureBars >= 24
      && (evaluation.outerEdgeScore || 0) >= 85
      && (evaluation.platformTouchGroups || 0) >= 3
      && (evaluation.score || 0) >= 87
      && (evaluation.certaintyScore || 0) >= 94;
    const highQualityRhythm = (evaluation.rhythmScore || 0) >= 68
      || (eliteOuterPlatformQuality && (evaluation.rhythmScore || 0) >= 66);
    const independentLevelReset = eliteOuterPlatformQuality
      && Number.isFinite(priorSignal.stop)
      && evaluation.triggerPrice >= priorSignal.stop + lifecycleAtr * 0.5
      // 新外沿低于旧突破线时，要求突破前订单流与推进速度共同证明它确实是
      // 一次新的起爆，而不是旧区间里的普通反弹复穿。
      && (evaluation.orderFlowScore || 0) >= 55
      && (evaluation.klineVelocity || 0) >= 0.85;
    const triggerOrStructureReset = (triggerAdvanced && pressureUpgradeAtr >= 0.18)
      || independentLevelReset;
    const highQualityOpportunityRearm = triggerOrStructureReset
      && (independentMotherPlatform || independentTriangleEdge || independentCompactParent)
      && (evaluation.confluence?.length || 0) >= 2
      && (evaluation.horizontalLaunchHasPriorAdvance === true
        || evaluation.triangleHasPriorAdvance === true
        || evaluation.shockBoxHorizontalLaunchException === true
        || evaluation.shockBoxAscendingTriangleException === true)
      && evaluation.horizontalLaunchPostSelloffRecovery !== true
      && evaluation.trianglePostSelloffRecovery !== true
      && evaluation.motherStructureNoise !== true
      && evaluation.riskStructureShape == null
      && evaluation.openedBeyondTrigger !== true
      && highQualityRhythm
      && (evaluation.sentimentScore || 0) >= 60
      && (evaluation.score || 0) >= 84
      && (evaluation.certaintyScore || 0) >= 84;
    if (highQualityOpportunityRearm) {
      return {
        reason: "",
        retryMaturity: true,
        retryEvidence: independentLevelReset && pressureUpgradeAtr < 0.18
          ? `前次试错止损后已完成独立母平台结构重置；新真实外沿从线下突破，按新机会立即重入，不要求高于旧突破线或等待固定根数`
          : `前次试错止损后已形成新的高质量真实外沿；当前从线下突破并完成压力升级，按新机会立即重入，不以固定重建根数延迟`,
      };
    }
    const rebuiltOuterPlatform = Boolean(evaluation.outerEdgeConfirmed)
      && (evaluation.outerEdgeScore || 0) >= 62
      && motherStructureBars >= 18
      && rebuildBars >= 6
      && ((evaluation.ceilingAge || 0) >= 3 || (evaluation.platformTouchGroups || 0) >= 2)
      && evaluation.triggerPrice >= priorSignal.triggerPrice + lifecycleAtr * 0.12;
    if (rebuiltOuterPlatform) {
      return {
        reason: "",
        retryMaturity: true,
        retryEvidence: `前次试错后已重建 ${rebuildBars} 根，并突破新形成的盘整外沿`,
      };
    }
    const ema90RepairRearm = evaluation.ema90ReclaimContinuation === true
      && Number(evaluation.ema90BreachStartIndex) > priorSignal.index
      && Number(evaluation.ema90ReclaimIndex) > Number(evaluation.ema90BreachStartIndex)
      && rebuildBars >= 12
      && motherStructureBars >= 36
      && evaluation.outerEdgeConfirmed === true
      && (evaluation.outerEdgeScore || 0) >= 78
      && (evaluation.score || 0) >= 88
      && (evaluation.certaintyScore || 0) >= 88;
    if (ema90RepairRearm) {
      return {
        reason: "",
        retryMaturity: true,
        retryEvidence: `前次交易后已经历EMA90失守、收复与 ${evaluation.ema90ReclaimRecoveryBars} 根重新蓄力；成熟外沿独立重置并重新武装`,
      };
    }
    // 最近一次小级别试错止损，不应抹掉更早已经存在的母箱体。只要突破前就能确认：
    // 母结构已持续足够久、压力位明显抬升且至少三类结构共振，就立即重新武装。
    // 这使 SHIB 2024-03-04 23:00 一类“箱体中间试错、最终突破外沿”的 K 线可成交，
    // 同时不会把止损后的原位连续扫单放回来。
    if (motherStructureBreakout) {
      return {
        reason: "",
        retryMaturity: true,
        retryEvidence: `近期小级别试错虽止损，但 ${motherStructureBars} 根母结构仍有效；新压力抬升 ${pressureUpgradeAtr.toFixed(2)} ATR 后重新武装`,
      };
    }
    const matureContextRearm = triggerAdvanced
      && rebuildBars >= 12
      && motherStructureBars >= 40
      && (evaluation.confluence?.length || 1) >= 3
      && (evaluation.foundationTypes?.length || 0) >= 1
      && (evaluation.certaintyScore || 0) >= 72;
    if (matureContextRearm) {
      return {
        reason: "",
        retryMaturity: true,
        retryEvidence: `已重建 ${rebuildBars} 根且形成 ${motherStructureBars} 根高确定性母结构；更新压力位后重新武装`,
      };
    }
    return {
      reason: "前次试错止损后尚未形成新的高质量母平台真实外沿或成熟三角外沿",
      retryMaturity: false,
    };
  }

  function isWeaklyRotatingShortFrameConvergence(signal) {
    const bars = Number(signal?.consolidationBars) || 0;
    const sideTransitions = Number(signal?.channelSideTransitions) || 0;
    const genuineOuterPlatform = signal?.outerEdgeConfirmed === true
      && signal?.clusteredCeilingBand === true
      && (signal?.ceilingTouches || 0) >= 5
      && (signal?.platformTouchGroups || 0) >= 3;
    // 一条斜线可以与一个真实横盘压力带共存，但不能凭一次单边迁移就独立取得
    // “三角/趋势线突破”执行权。保留真实母平台，是为了不误删 TURBO 这类横盘；
    // 没有母平台兜底时，RAVE 的长空心/单边连线既不画线，也不生成买点。
    return ["5m", "15m"].includes(signal?.interval)
      && (signal?.foundationTypes || []).includes("triangle")
      && bars >= 36
      && sideTransitions < 2
      && !genuineOuterPlatform;
  }

  // 更大的母箱体只是背景压力，不应机械抹掉其中已经独立完成的主升接力结构。
  // 例外只服务于 5 分钟主升阶段，并且要求子结构自己具备完整因果链：
  // 前置拉升 -> 足够长的盘整/收敛 -> 真实外沿从线下突破。局部小阳线、
  // 空心趋势线、急促上楔和超过 7% 才碰到前高的追高都不会通过。
  function isIndependentNestedMainWaveStructure(signal) {
    if (signal?.interval !== "5m"
      || signal?.insideMotherBase !== true
      || !["active", "expected"].includes(signal?.mainWaveStage)
      || signal?.crossedLevel !== true
      || signal?.openedBeyondTrigger === true
      || Boolean(signal?.riskStructureShape)
      || Boolean(signal?.highLevelDistribution)
      || signal?.motherStructureNoise === true
      || signal?.horizontalBrokenOuterPlatform === true
      || signal?.horizontalLaunchPostSelloffRecovery === true
      || signal?.trianglePostSelloffRecovery === true
      || (signal?.launchDistancePercent != null && signal.launchDistancePercent > 7)) return false;

    const foundations = new Set((signal?.foundationTypes || []).map(String));
    const auxiliaries = new Set((signal?.auxiliaryTypes || []).map(String));
    const bars = Number(signal?.consolidationBars) || 0;
    const horizontalAdvanceAtr = Number(signal?.horizontalLaunchPriorAdvanceAtr) || 0;
    const triangleAdvanceAtr = Number(signal?.trianglePriorAdvanceAtr) || 0;
    const hasPreviousHigh = auxiliaries.has("previousHigh")
      || Number.isFinite(Number(signal?.previousHighLevel));

    const independentPlatform = foundations.has("base")
      && signal?.outerEdgeConfirmed === true
      && (signal?.outerEdgeScore || 0) >= 80
      && bars >= 40
      && signal?.horizontalLaunchHasPriorAdvance === true
      && horizontalAdvanceAtr >= 7.5
      && signal?.horizontalLaunchUrgent !== true
      && signal?.horizontalLaunchInsufficientEdgeDwell !== true;

    // 长三角即使在统计上只完成一次完整换边，只要它已经持续 72 根以上、
    // 轨道内交易充分、前置上推很强，并在同一根 K 线突破动态上沿和真实前高，
    // 也属于独立结构；这是 RAVE 2026-04-10 14:55 一类慢压缩，而非空心连线。
    const longDirectTriangle = foundations.has("triangle")
      && signal?.directStructuralBoundary === true
      && hasPreviousHigh
      && bars >= 72
      && (signal?.structureQuality || 0) >= 0.84
      && (signal?.channelInteriorOccupancy || 0) >= 0.65
      && triangleAdvanceAtr >= 10
      && ((signal?.relativeVolume || 0) >= 1.05 || (signal?.orderFlowScore || 0) >= 60);

    // 复合结构中，三角自己的观察窗可能从母平台中段开始，因而看不到更早的
    // 拉升；若同一候选的横盘母结构已经保存了强前置上推，允许它为三角提供
    // 因果起点，但仍要求长盘整、多次换边和同 K 的真实边界突破。
    const compoundPlatformTriangle = foundations.has("base")
      && foundations.has("triangle")
      && signal?.outerEdgeConfirmed === true
      && signal?.directStructuralBoundary === true
      && hasPreviousHigh
      && bars >= 60
      && (signal?.structureQuality || 0) >= 0.84
      && (signal?.channelInteriorOccupancy || 0) >= 0.64
      && (signal?.channelSideTransitions || 0) >= 3
      && signal?.horizontalLaunchHasPriorAdvance === true
      && horizontalAdvanceAtr >= 10;

    return independentPlatform || longDirectTriangle || compoundPlatformTriangle;
  }

  function isDirectionalRecoveryPivotOnly(signal) {
    // 深回撤后的单边修复，有时能被数学包络拟合成一个“收敛三角”，但盘面上
    // 并没有独立母平台：末端低点急促抬高、压力带刚在本根形成，且结构前的
    // 下杀大于所谓前置拉升。此时 B 可以作为主升拐点保留，不能连带显示
    // 横盘起飞、三角、趋势线或前高突破。WLD 2024-02-19 10:45 即属此类。
    return ["5m", "15m", "1h"].includes(signal?.interval)
      && signal?.hasPivot === true
      && signal?.horizontalLaunchQualified !== true
      && signal?.horizontalLaunchUrgent === true
      && signal?.clusteredCeilingBand !== true
      && (signal?.ceilingAge || 0) <= 1
      && (signal?.ceilingTouches || 0) <= 3
      && signal?.horizontalLaunchRetainedAboveHalf !== true
      && (signal?.triangleSelloffAtr || 0) >= Math.max(
        4,
        (signal?.trianglePriorAdvanceAtr || 0) * 1.2,
      )
      && (signal?.originDistanceAtr || 0) <= 0.55;
  }

  function normalizeDisplayedStructureLabels(signal) {
    if (signal?.longSwingMotherBox === true) {
      return {
        ...signal,
        pattern: "盘整突破",
        patternKey: "base",
        displayConfluence: ["base"],
        displayFoundationTypes: ["base"],
        displayAuxiliaryTypes: [],
        evidence: [...new Set([
          ...(signal.evidence || []),
          "母结构以完整长箱体前高为执行边界；箱体后段拟合斜线不显示为三角或趋势线标签",
        ])],
      };
    }
    if (!isDirectionalRecoveryPivotOnly(signal)) return signal;
    return {
      ...signal,
      pattern: "拐点收复",
      patternKey: "pivot",
      displayConfluence: ["pivot"],
      displayFoundationTypes: [],
      displayAuxiliaryTypes: ["pivot"],
      directionalRecoveryPivotOnly: true,
      evidence: [...new Set([
        ...(signal.evidence || []),
        "本段属于深回撤后的方向性修复：数学包络不作为独立三角/横盘标签，仅保留拐点收复",
      ])],
    };
  }

  function analyzeTimeframe(rows, options = {}) {
    const now = Number.isFinite(options.now) ? options.now : Date.now();
    const interval = options.interval || "5m";
    const candles = normalizeCandles(rows, now);
    const closes = candles.map((row) => row.close);
    const volumes = candles.map((row) => row.volume);
    const indicators = {
      ema90: ema(closes, 90),
      atr: atr(candles, 14),
      volumeMean: rollingMean(volumes, 20),
    };
    const signals = [];
    const pending = [];
    const rejected = [];
    const structures = [];
    const crossedEvaluations = [];

    for (let index = 30; index < candles.length; index += 1) {
      const rightEdge = index === candles.length - 1;
      const candidates = findCandidates(candles, index, indicators, {
        rightEdge,
        interval,
        newCoinNotFalling: options.newCoinNotFalling === true,
      });
      candidates.forEach((candidate) => {
        if (!candidate.crossedLevel && !rightEdge) return;
        const evaluation = evaluateCandidate(candles, index, candidate, indicators, interval, options);
        if (candidate.crossedLevel) crossedEvaluations.push(evaluation);
        if (evaluation.triangleLines
          && ["falling-wedge", "converging-triangle", "ascending-triangle"].includes(evaluation.structureShape)
          && !evaluation.highLevelDistribution
          && !evaluation.riskStructureShape
          && !isWeaklyRotatingShortFrameConvergence(evaluation)
          && (evaluation.consolidationBars || 0) >= 36
          && (evaluation.structureQuality || 0) >= 0.48
          && ((evaluation.channelSideTransitions || 0) >= 1
            || (evaluation.outerEdgeConfirmed === true
              && (evaluation.channelInteriorOccupancy || 0) >= 0.62))) {
          structures.push({
            ...evaluation,
            status: "structure",
            structurePreconfirmed: true,
            executionAllowed: evaluation.status === "buy",
          });
        }
        if (evaluation.status === "buy") {
          const lifecycle = structureLifecycleDecision(
            signals,
            evaluation,
            candles,
            index,
            Math.max(indicators.atr[index - 1], 1e-8),
          );
          // 同一盘整只允许一笔活跃交易。假突破止损后必须扩大调整级别并重建结构；
          // 重建成熟后不限制再次突破的次数，因此不会漏掉多次试错后的真正起爆。
          if (!lifecycle.reason) {
            if (lifecycle.replacePriorId) {
              const replaceIndex = signals.findIndex((signal) => signal.id === lifecycle.replacePriorId);
              if (replaceIndex >= 0) signals.splice(replaceIndex, 1);
            }
            const accepted = lifecycle.retryMaturity
              ? {
                ...evaluation,
                ...(lifecycle.inheritStructureContext || {}),
                evidence: [
                  ...evaluation.evidence,
                  lifecycle.retryEvidence || "前次试错已完成回落重置，允许再次突破，但不会因此提高确定性评级",
                ],
              }
              : evaluation;
            signals.push(accepted);
          }
          else if (candidate.crossedLevel) {
            const coveredByBuy = signals.some((signal) => (
              signal.index === index
              && Math.abs(signal.triggerPrice - evaluation.triggerPrice) <= Math.max(indicators.atr[index - 1], 1e-8) * 0.25
            ));
            if (!coveredByBuy) rejected.push({
              ...evaluation,
              id: `${evaluation.id}-lifecycle-veto`,
              status: "filtered",
              reasons: [...evaluation.reasons, lifecycle.reason],
              evidence: [...evaluation.evidence, "结构生命周期只读取当前时点以前的止损与重建过程"],
            });
          }
        } else if (evaluation.status === "pending") {
          if (index === candles.length - 1) pending.push(evaluation);
        } else if (candidate.crossedLevel) {
          const coveredByBuy = signals.some((signal) => (
            signal.index === index
            && Math.abs(signal.triggerPrice - evaluation.triggerPrice) <= Math.max(indicators.atr[index - 1], 1e-8) * 0.25
          ));
          if (!coveredByBuy) rejected.push(evaluation);
        }
      });
    }

    // 超长、低效率的单一横向区间即使没有生成可成交候选，也要留下一个明确的
    // 过滤审计原因。否则“没有 B”看起来像漏识别，实际却是价格数百根都在同一
    // 母盘整内部反复越过附近小高点。该记录只进入过滤账本，不画 B、不提醒。
    if (candles.length >= 720
      && !signals.length
      && !pending.length
      && !rejected.length
      && !crossedEvaluations.length) {
      const auditSpan = Math.min(480, candles.length);
      const auditRows = candles.slice(-auditSpan);
      const auditAtr = Math.max(indicators.atr.at(-2) || indicators.atr.at(-1) || 0, 1e-8);
      const auditRangeAtr = (Math.max(...auditRows.map((row) => row.high))
        - Math.min(...auditRows.map((row) => row.low))) / auditAtr;
      const auditNetAtr = Math.abs(auditRows.at(-1).close - auditRows[0].close) / auditAtr;
      if (auditRangeAtr <= 4 && auditNetAtr <= 0.6) {
        const auditIndex = candles.length - 1;
        rejected.push({
          id: `${interval}-${candles[auditIndex].time}-long-consolidation-noise-audit`,
          time: candles[auditIndex].time,
          decisionTime: candles[auditIndex].time,
          index: auditIndex,
          interval,
          status: "filtered",
          pattern: "长期盘整内部噪声",
          patternKey: "motherConsolidationNoise",
          crossedLevel: false,
          executionAllowed: false,
          auditOnly: true,
          level: Math.max(...auditRows.map((row) => row.high)),
          price: candles[auditIndex].close,
          reasons: ["同一盘整内部的周期性小高点反复穿越，不构成真实外沿突破"],
          evidence: [
            `最近 ${auditSpan} 根总区间仅 ${auditRangeAtr.toFixed(2)} ATR，净位移 ${auditNetAtr.toFixed(2)} ATR`,
            "过滤审计只解释为什么没有买点，不生成盘面标记、弹窗或语音",
          ],
        });
      }
    }

    // 同一高确定性三角/降楔可能同时生成动态上轨、静态前高等多个候选。
    // 已经由相邻 B 覆盖的“同一盘整已有买点”生命周期否定只是内部去重信息，
    // 不再放进人工复盘账本；真正因质量、情绪或位置被过滤的记录仍完整保留。
    const cleanedRejected = rejected.filter((item) => {
      const lifecycleDuplicate = (item.reasons || []).some((reason) => (
        reason.includes("同一盘整已有有效买点")
      ));
      if (!lifecycleDuplicate || !["falling-wedge", "converging-triangle", "ascending-triangle"].includes(item.structureShape)) {
        return true;
      }
      const itemStart = item.triangleLines?.upper?.startIndex;
      const coveredByAdjacentBuy = signals.some((signal) => {
        if (signal.structureShape !== item.structureShape) return false;
        if (signal.index > item.index || item.index - signal.index > 2) return false;
        const signalStart = signal.triangleLines?.upper?.startIndex;
        return Number.isFinite(itemStart)
          && Number.isFinite(signalStart)
          && Math.abs(itemStart - signalStart) <= 4;
      });
      return !coveredByAdjacentBuy;
    });

    const secondaryBreakoutHints = buildSecondaryBreakoutHints(
      signals,
      candles,
      indicators,
      interval,
      [...signals, ...cleanedRejected],
    );
    const executableSignals = signals.filter((signal) => !secondaryBreakoutHints.some((hint) => (
      hint.index === signal.index
      && Math.abs((hint.triggerPrice || hint.level) - (signal.triggerPrice || signal.level))
        <= Math.max(indicators.atr[signal.index - 1] || signal.atrAtDecision || 0, 1e-8) * 0.35
    )));

    const retainableCandidates = cleanedRejected
      .filter(isRetainableConsolidationCandidate)
      .filter((item) => !executableSignals.some((signal) => (
        signal.index === item.index
        && Math.abs(signal.triggerPrice - item.triggerPrice) <= Math.max(indicators.atr[item.index - 1] || 0, 1e-8) * 0.25
      )))
      .sort((a, b) => a.index - b.index
        || (b.outerEdgeScore || 0) - (a.outerEdgeScore || 0)
        || (b.foundationTypes?.length || 0) - (a.foundationTypes?.length || 0)
        || (b.score || 0) - (a.score || 0))
      .reduce((selected, item) => {
        const duplicate = selected.some((existing) => (
          existing.index === item.index
          && Math.abs(existing.triggerPrice - item.triggerPrice)
            <= Math.max(indicators.atr[item.index - 1] || 0, 1e-8) * 0.25
        ));
        if (!duplicate) selected.push(item);
        return selected;
      }, []);
    const retainedCandidates = retainRearmedCandidates(retainableCandidates, candles, indicators)
      .map(retainedCandidateFrom)
      .sort((a, b) => a.index - b.index);

    const last = candles.length - 1;
    const bullish = last >= 0
      && closes[last] > indicators.ema90[last]
      && indicators.ema90[last] >= indicators.ema90[Math.max(0, last - 4)];
    const strong = bullish && closes[last] >= indicators.ema90[last] + Math.max(indicators.atr[last] || 0, 0) * 0.6;
    const pivotOnlyGeometry = executableSignals
      .filter((signal) => isDirectionalRecoveryPivotOnly(signal) || signal.longSwingMotherBox === true)
      .map((signal) => ({
        shape: signal.structureShape,
        startIndex: signal.triangleLines?.upper?.startIndex,
      }));
    const stableStructures = structures
      .filter((structure) => !pivotOnlyGeometry.some((geometry) => (
        geometry.shape === structure.structureShape
        && Number.isFinite(geometry.startIndex)
        && Number.isFinite(structure.triangleLines?.upper?.startIndex)
        && Math.abs(geometry.startIndex - structure.triangleLines.upper.startIndex) <= 4
      )))
      .sort((a, b) => (b.structureQuality || 0) - (a.structureQuality || 0)
        || (b.certaintyScore || 0) - (a.certaintyScore || 0)
        || (b.consolidationBars || 0) - (a.consolidationBars || 0))
      .reduce((selected, structure) => {
        const startIndex = structure.triangleLines?.upper?.startIndex ?? structure.index;
        const duplicate = selected.some((existing) => {
          const existingStart = existing.triangleLines?.upper?.startIndex ?? existing.index;
          const intersection = Math.max(0, Math.min(structure.index, existing.index) - Math.max(startIndex, existingStart));
          const smallerSpan = Math.max(1, Math.min(structure.index - startIndex, existing.index - existingStart));
          return structure.structureShape === existing.structureShape && intersection / smallerSpan >= 0.7;
        });
        if (!duplicate) selected.push(structure);
        return selected;
      }, [])
      .sort((a, b) => a.index - b.index);
    return {
      interval,
      candles,
      indicators,
      signals: executableSignals,
      pending,
      retainedCandidates,
      secondaryBreakoutHints,
      rejected: cleanedRejected,
      structures: stableStructures,
      regime: {
        bullish,
        strong,
        label: strong ? "主升环境" : bullish ? "多头观察" : "禁止追多",
      },
      stats: {
        signalCount: executableSignals.length,
        pendingCount: pending.length,
        retainedCandidateCount: retainedCandidates.length,
        secondaryBreakoutHintCount: secondaryBreakoutHints.length,
        rejectedCount: cleanedRejected.length,
        lastPrice: last >= 0 ? closes[last] : 0,
      },
    };
  }

  function latestItem(items) {
    return items.reduce((latest, item) => (!latest || item.time > latest.time ? item : latest), null);
  }

  function regimeAt(result, time) {
    if (!result?.candles?.length) return null;
    let low = 0;
    let high = result.candles.length - 1;
    if ((result.candles[0].closeTime ?? result.candles[0].time) > time) return null;
    while (low < high) {
      const middle = Math.ceil((low + high) / 2);
      if ((result.candles[middle].closeTime ?? result.candles[middle].time) <= time) low = middle;
      else high = middle - 1;
    }
    const close = result.candles[low].close;
    const ema90Value = result.indicators?.ema90?.[low];
    const priorEma90 = result.indicators?.ema90?.[Math.max(0, low - 4)];
    if (!Number.isFinite(close) || !Number.isFinite(ema90Value)) return null;
    return {
      bullish: close > ema90Value && (!Number.isFinite(priorEma90) || ema90Value >= priorEma90),
      index: low,
    };
  }

  function hasNearbyAnchor(result, time, windowMs) {
    if (!result) return false;
    return [...(result.signals || []), ...(result.pending || [])]
      .some((item) => Math.abs((item.decisionTime ?? item.time) - time) <= windowMs);
  }

  function hasTrendAnchor(result, time) {
    const snapshot = regimeAt(result, time);
    if (!snapshot || !snapshot.bullish || snapshot.index < 12) return false;
    const index = snapshot.index;
    const ema90Value = result.indicators?.ema90?.[index];
    const priorEma90 = result.indicators?.ema90?.[Math.max(0, index - 12)];
    const atrValue = Math.max(result.indicators?.atr?.[index] || 0, 1e-8);
    const recent = result.candles.slice(Math.max(0, index - 8), index + 1);
    const recentFloor = Math.min(...recent.map((row) => row.low));
    return Number.isFinite(ema90Value)
      && Number.isFinite(priorEma90)
      && ema90Value > priorEma90
      && recentFloor >= ema90Value - atrValue * 1.8;
  }

  function pruneSignalBudget(results) {
    // “约 20 个”只用于人工复盘预期，不能成为删除因果信号的硬配额。
    return results;
  }

  function oneHourPostImpulseWedgeStructureScore(signal) {
    if (!signal || signal.interval !== "1h" || signal.structureShape !== "falling-wedge") return 0;
    const foundations = signal.foundationTypes || [];
    const upper = signal.triangleLines?.upper;
    const lower = signal.triangleLines?.lower;
    const trendline = signal.trendline || upper;
    // 降楔自己的上轨就是结构趋势线。若同一价位没有另外生成一个 trendline
    // 辅助组件，不能因此否定已经完整存在的上下轨几何；否则会漏掉“降楔上轨
    // + 真前高”直接共振的突破（如 PEPE 2024-03-01 15:00 1h）。
    if (!foundations.includes("triangle") || !upper || !lower) return 0;
    const structureBars = Math.max(
      0,
      (upper.endIndex ?? signal.index ?? 0) - (upper.startIndex ?? signal.index ?? 0),
    );
    const average = (...values) => mean(values.map((value) => finite(value)));
    const envelopeCoverage = average(upper.envelopeCoverage, lower.envelopeCoverage);
    const bodyCoverage = average(upper.bodyCoverage, lower.bodyCoverage);
    const crossingRatio = Math.max(upper.crossingRatio || 0, lower.crossingRatio || 0);
    // 仅用于解释降楔几何，不参与通用策略许可或一票否决。
    const structureBalance = (
      clamp((envelopeCoverage - 0.82) / 0.18, 0, 1) * 0.18
      + clamp((bodyCoverage - 0.88) / 0.12, 0, 1) * 0.1
      + clamp(1 - crossingRatio / 0.1, 0, 1) * 0.14
      + clamp(((trendline.activeProximity || 0) - 0.42) / 0.5, 0, 1) * 0.14
      + clamp((trendline.originRangeAtr || 0) / 5, 0, 1) * 0.1
      + clamp(((signal.structureQuality || 0) - 0.42) / 0.33, 0, 1) * 0.14
      + clamp((structureBars - 24) / 32, 0, 1) * 0.1
      + Number(signal.aboveEma90 === true && (signal.ema90SlopeAtDecision || 0) > 0) * 0.1
    ) * 100;
    return Math.round(clamp(structureBalance, 0, 100));
  }

  function isOneHourPostImpulseWedgeIgnition(signal) {
    const structureEvidence = oneHourPostImpulseWedgeStructureScore(signal);
    if (!structureEvidence) return false;
    const foundations = signal.foundationTypes || [];
    const upper = signal.triangleLines?.upper;
    const lower = signal.triangleLines?.lower;
    const structureBars = Math.max(0, (upper?.endIndex || 0) - (upper?.startIndex || 0));
    const crossingRatio = Math.max(upper?.crossingRatio || 0, lower?.crossingRatio || 0);
    const terminalEmaReclaim = signal?.terminalEma90BoundaryReclaim === true
      && signal?.structureShape === "falling-wedge"
      && foundations.includes("triangle")
      && (signal?.auxiliaryTypes || []).includes("trendline")
      && ((signal?.auxiliaryTypes || []).includes("previousHigh") || signal?.hasPivot === true)
      && signal?.triangleHasPriorAdvance === true
      && (signal?.trianglePriorAdvanceAtr || 0) >= 2
      && (signal?.structureQuality || 0) >= 0.7
      && (upper?.touchGroups || 0) >= 3
      && (lower?.touchGroups || 0) >= 3
      && (upper?.envelopeCoverage || 0) >= 0.9
      && (lower?.envelopeCoverage || 0) >= 0.9
      && crossingRatio <= 0.06
      && signal?.directStructuralBoundary === true;
    const causalGeometry = structureBars >= 32
      && crossingRatio <= 0.1
      && (upper?.envelopeCoverage || 0) >= 0.86
      && (lower?.envelopeCoverage || 0) >= 0.86
      && (upper?.touchGroups || 0) >= 2
      && (lower?.touchGroups || 0) >= 2
      && (signal?.structureQuality || 0) >= 0.52
      && signal?.triangleHasPriorAdvance === true
      && ((signal.aboveEma90 === true && (signal.ema90SlopeAtDecision || 0) > 0)
        || terminalEmaReclaim)
      && (signal.score || 0) >= 74;
    if (!causalGeometry) return false;
    // 第一级是下降楔形动态上轨的先手突破；第二级是紧接着突破同一盘整的
    // 静态前高。两者触发价不同，均从线下首次上穿，因此在1小时盘面分别标 B。
    const stageMomentum = (signal.rhythmScore || 0) * 0.38
      + (signal.sentimentScore || 0) * 0.25
      + (signal.certaintyScore || 0) * 0.27
      + (signal.score || 0) * 0.1;
    const wedgeUpperBreak = !signal.outerEdgeConfirmed
      && stageMomentum >= 68;
    const outerEdgeBalance = (signal.outerEdgeScore || 0) * 0.65
      + clamp((signal.platformTouchGroups || 0) / 2, 0, 1) * 25
      + clamp((7 - (signal.launchDistancePercent ?? 99)) / 7, 0, 1) * 10;
    const outerEdgeConfirmation = foundations.includes("base")
      && signal.outerEdgeConfirmed === true
      && (signal.launchDistancePercent ?? 99) <= 7
      && outerEdgeBalance >= 68;
    return terminalEmaReclaim || wedgeUpperBreak || outerEdgeConfirmation;
  }

  function isMatureOneHourLongTriangleReset(signal) {
    const foundations = signal?.foundationTypes || [];
    const bars = signal?.consolidationBars || 0;
    const parentRotationNearEdge = signal?.motherStructureMode === "post-impulse-high-level-rotation"
      && (signal?.motherStructurePosition || 0) >= 0.78
      && signal?.hasPivot === true;
    // 一条已经沿上下轨交易一百根左右、覆盖完整中段并多次换边的1小时结构，
    // 本身就是母级别盘整，而不再是“急杀箱体内的局部反弹”。允许它先突破
    // 动态上沿，再由下一根突破静态前高确认；不能要求第一阶段就同时越过旧峰。
    // 门槛刻意使用完整轨道占用、连续空腔和四次换边，避免普通箱体里的几根
    // 反弹小三角借此绕过母箱体过滤。
    const fullyTradedMotherConvergence = bars >= 96
      && (signal?.structureQuality || 0) >= 0.85
      && (signal?.channelInteriorOccupancy || 0) >= 0.75
      && (signal?.channelMiddleParticipationRatio || 0) >= 0.65
      && (signal?.channelHollowRatio ?? 1) <= 0.6
      && (signal?.channelLongestHollowRun ?? 99) <= 12
      && (signal?.channelSideTransitions || 0) >= 4
      && (signal?.trianglePriorAdvanceAtr || 0) >= 8
      && (signal?.rhythmScore || 0) >= 75
      && (signal?.sentimentScore || 0) >= 55;
    return signal?.interval === "1h"
      && foundations.includes("triangle")
      && signal?.structureShape === "converging-triangle"
      && signal?.directStructuralBoundary === true
      && bars >= 60
      && (signal?.structureQuality || 0) >= 0.8
      && (signal?.channelInteriorOccupancy || 0) >= 0.68
      && (signal?.channelMiddleParticipationRatio || 0) >= 0.6
      && (signal?.channelHollowRatio ?? 1) <= 0.72
      && (signal?.channelLongestHollowRun ?? 99) <= 20
      && (signal?.channelSideTransitions || 0) >= 3
      && signal?.triangleHasPriorAdvance === true
      && (signal?.trianglePriorAdvanceAtr || 0) >= 8
      && signal?.aboveEma90 === true
      && (signal?.ema90SlopeAtDecision || 0) >= 0
      && (parentRotationNearEdge || fullyTradedMotherConvergence)
      && signal?.crossedLevel === true
      && signal?.openedBeyondTrigger !== true
      && signal?.trianglePostSelloffRecovery !== true
      && signal?.riskStructureShape == null
      && (signal?.launchDistancePercent == null || signal.launchDistancePercent <= 7);
  }

  function isLongBasePreviousHighIgnition(signal) {
    const foundations = signal?.foundationTypes || [];
    const auxiliaries = signal?.auxiliaryTypes || [];
    return ["5m", "15m"].includes(signal?.interval)
      && foundations.includes("base")
      && auxiliaries.includes("previousHigh")
      && signal?.hasPivot === true
      && (signal?.consolidationBars || 0) >= 48
      && (signal?.outerEdgeScore || 0) >= 52
      && (signal?.platformTouchGroups || 0) >= 2
      && (signal?.ceilingTouches || 0) >= 3
      && signal?.horizontalLaunchHasPriorAdvance === true
      && (signal?.horizontalLaunchPriorAdvanceAtr || 0) >= 6
      && signal?.horizontalLaunchQualified === true
      && signal?.horizontalLaunchUrgent !== true
      && signal?.horizontalLaunchInsufficientEdgeDwell !== true
      && signal?.horizontalLaunchPostSelloffRecovery !== true
      && signal?.aboveEma90 === true
      && (signal?.ema90SlopeAtDecision || 0) > 0
      && signal?.motherStructureNoise !== true
      && signal?.riskStructureShape == null
      && signal?.crossedLevel === true
      && signal?.openedBeyondTrigger !== true
      && (signal?.launchDistancePercent == null || signal.launchDistancePercent <= 7)
      && ((signal?.orderFlowScore || 0) >= 70
        || (signal?.relativeVolume || 0) >= 1.1
        || (signal?.klineVelocity || 0) >= 1.15);
  }

  function isSoftTestExtendedTriangleBreakout(signal) {
    const foundations = signal?.foundationTypes || [];
    return ["5m", "15m"].includes(signal?.interval)
      && foundations.includes("triangle")
      && signal?.softTestExtendedTriangle === true
      && signal?.directStructuralBoundary === true
      && (signal?.consolidationBars || 0) >= 40
      && (signal?.structureQuality || 0) >= 0.68
      && (signal?.channelInteriorOccupancy || 0) >= 0.68
      && (signal?.channelMiddleParticipationRatio || 0) >= 0.5
      && (signal?.channelHollowRatio ?? 1) <= 0.66
      && (signal?.channelLongestHollowRun ?? 99) <= 12
      && (signal?.channelSideTransitions || 0) >= 3
      && signal?.triangleHasPriorAdvance === true
      && (signal?.trianglePriorAdvanceAtr || 0) >= 8
      && signal?.crossedLevel === true
      && signal?.openedBeyondTrigger !== true
      && signal?.trianglePostSelloffRecovery !== true
      && signal?.riskStructureShape == null
      && (signal?.launchDistancePercent == null || signal.launchDistancePercent <= 7);
  }

  // 一小时冲高后的长平台已经用大量K线反复交易同一外沿时，平台本身可以
  // 重置更早的孤立峰值。只接受高质量、六十根以上、至少五组触顶且有量流
  // 确认的真外沿；普通高位震荡和附近阳线不会取得这条例外。
  function isMatureOneHourOuterPlatformReset(signal) {
    const foundations = signal?.foundationTypes || [];
    const auxiliaries = signal?.auxiliaryTypes || [];
    return signal?.interval === "1h"
      && foundations.includes("base")
      && auxiliaries.includes("previousHigh")
      && signal?.outerEdgeConfirmed === true
      && (signal?.outerEdgeScore || 0) >= 92
      && (signal?.consolidationBars || 0) >= 60
      && (signal?.structureQuality || 0) >= 0.86
      && (signal?.platformTouchGroups || 0) >= 5
      && (signal?.ceilingTouches || 0) >= 12
      && signal?.motherStructureMode === "post-impulse-high-level-rotation"
      && (signal?.motherStructurePosition || 0) >= 0.64
      && signal?.aboveEma90 === true
      && (signal?.ema90SlopeAtDecision || 0) > 0
      && signal?.crossedLevel === true
      && signal?.openedBeyondTrigger !== true
      && signal?.horizontalLaunchUrgent !== true
      && signal?.horizontalLaunchInsufficientEdgeDwell !== true
      && signal?.horizontalLaunchPostSelloffRecovery !== true
      && signal?.riskStructureShape == null
      && (signal?.launchDistancePercent == null || signal.launchDistancePercent <= 7)
      && ((signal?.relativeVolume || 0) >= 1.25 || (signal?.orderFlowScore || 0) >= 68);
  }

  // 一小时回踩后重新越过真前高，是“拐点先手”而不是要求再伪造一个大箱体。
  // 这条路径只给 16~28 根、质量和主升节奏都达标、EMA90继续上行的回踩结构。
  function isOneHourRelaunchPivotIgnition(signal) {
    const foundations = signal?.foundationTypes || [];
    const auxiliaries = signal?.auxiliaryTypes || [];
    return signal?.interval === "1h"
      && foundations.includes("relaunch")
      && auxiliaries.includes("previousHigh")
      && (signal?.consolidationBars || 0) >= 16
      && (signal?.consolidationBars || 0) <= 28
      && (signal?.structureQuality || 0) >= 0.7
      && (signal?.rhythmScore || 0) >= 65
      && (signal?.sentimentScore || 0) >= 60
      && signal?.aboveEma90 === true
      && (signal?.ema90SlopeAtDecision || 0) > 0
      && signal?.crossedLevel === true
      && signal?.openedBeyondTrigger !== true
      && signal?.motherStructureNoise !== true
      && signal?.riskStructureShape == null
      && (signal?.launchDistancePercent == null || signal.launchDistancePercent <= 7);
  }

  // 一小时紧凑上升三角允许动态上沿直接取得执行权，但必须是三角与回踩
  // 两个独立母结构共同成立，内部占用充分、空白短、至少两次换边，并由量速
  // 同时确认。几根K的上楔或空心收敛不会进入这条路径。
  function isOneHourCompactAscendingTriangleIgnition(signal) {
    const foundations = signal?.foundationTypes || [];
    return signal?.interval === "1h"
      && foundations.includes("triangle")
      && foundations.includes("relaunch")
      && signal?.hasPivot === true
      && signal?.structureShape === "ascending-triangle"
      && (signal?.consolidationBars || 0) >= 36
      && (signal?.structureQuality || 0) >= 0.675
      && (signal?.channelInteriorOccupancy || 0) >= 0.7
      && (signal?.channelMiddleParticipationRatio || 0) >= 0.75
      && (signal?.channelHollowRatio ?? 1) <= 0.4
      && (signal?.channelLongestHollowRun ?? 99) <= 8
      && (signal?.channelSideTransitions || 0) >= 2
      && signal?.triangleHasPriorAdvance === true
      && (signal?.trianglePriorAdvanceAtr || 0) >= 3
      && signal?.trianglePostSelloffRecovery !== true
      && signal?.aboveEma90 === true
      && (signal?.ema90SlopeAtDecision || 0) > 0
      && signal?.crossedLevel === true
      && signal?.openedBeyondTrigger !== true
      && signal?.motherStructureNoise !== true
      && signal?.riskStructureShape == null
      && (signal?.relativeVolume || 0) >= 1.15
      && (signal?.orderFlowScore || 0) >= 70
      && (signal?.klineVelocity || 0) >= 1.15;
  }

  // 15分钟完整平台可能在末端形成一小段上升通道。若平台明显更早开始、
  // 已经盘整40根以上且真正前高、回踩拐点与强订单流同K触发，应由完整
  // 平台取得优先级；这不是允许交易上升通道末端本身。
  function isMatureFifteenMinuteRetryPlatformIgnition(signal) {
    const foundations = signal?.foundationTypes || [];
    const auxiliaries = signal?.auxiliaryTypes || [];
    const fullPlatformPredatesTerminalChannel = Number.isFinite(signal?.horizontalStructureStartIndex)
      && Number.isFinite(signal?.riskStructureStartIndex)
      && signal.riskStructureStartIndex - signal.horizontalStructureStartIndex >= 8;
    return signal?.interval === "15m"
      && foundations.includes("base")
      && foundations.includes("relaunch")
      && auxiliaries.includes("previousHigh")
      && signal?.hasPivot === true
      && (signal?.consolidationBars || 0) >= 40
      && (signal?.outerEdgeScore || 0) >= 52
      && (signal?.platformTouchGroups || 0) >= 2
      && (signal?.ceilingTouches || 0) >= 3
      && signal?.horizontalLaunchHasPriorAdvance === true
      && (signal?.horizontalLaunchPriorAdvanceAtr || 0) >= 6
      && signal?.horizontalLaunchQualified === true
      && signal?.horizontalLaunchUrgent !== true
      && signal?.horizontalLaunchInsufficientEdgeDwell !== true
      && signal?.horizontalLaunchPostSelloffRecovery !== true
      && signal?.aboveEma90 === true
      && (signal?.ema90SlopeAtDecision || 0) > 0
      && signal?.motherStructureNoise !== true
      && signal?.riskStructureShape === "rising-channel"
      && fullPlatformPredatesTerminalChannel
      && signal?.crossedLevel === true
      && signal?.openedBeyondTrigger !== true
      && (signal?.launchDistancePercent == null || signal.launchDistancePercent <= 7)
      && (signal?.rhythmScore || 0) >= 68
      && (signal?.sentimentScore || 0) >= 60
      && (signal?.orderFlowScore || 0) >= 78
      && (signal?.klineVelocity || 0) >= 1.1;
  }

  // 15分钟安静收敛并不一定会在突破前放量，也不一定机械完成三次换边。
  // 当结构已经持续36根以上、上下包络完整、前面有强独立拉升，并在同一根K
  // 从线下突破动态上沿与真正前高时，真前高和拐点本身足以确认这是完整母结构
  // 的最终起爆，而不是附近小阳线或单条趋势线。空心通道、急促抬低点、跌后
  // 修复和没有真前高的候选均不进入这条窄路径。
  function isMatureFifteenMinutePriorHighTriangleIgnition(signal) {
    const foundations = signal?.foundationTypes || [];
    const auxiliaries = signal?.auxiliaryTypes || [];
    return signal?.interval === "15m"
      && foundations.includes("triangle")
      && foundations.includes("relaunch")
      && auxiliaries.includes("previousHigh")
      && signal?.hasPivot === true
      && ["converging-triangle", "ascending-triangle"].includes(signal?.structureShape)
      && signal?.directStructuralBoundary === true
      && (signal?.consolidationBars || 0) >= 36
      && (signal?.structureQuality || 0) >= 0.76
      && (signal?.channelInteriorOccupancy || 0) >= 0.64
      && (signal?.channelMiddleParticipationRatio || 0) >= 0.62
      && (signal?.channelHollowRatio ?? 1) <= 0.6
      && (signal?.channelLongestHollowRun ?? 99) <= 12
      && signal?.triangleHasPriorAdvance === true
      && (signal?.trianglePriorAdvanceAtr || 0) >= 8
      && signal?.trianglePostSelloffRecovery !== true
      && signal?.horizontalLaunchPostSelloffRecovery !== true
      && signal?.aboveEma90 === true
      && (signal?.ema90SlopeAtDecision || 0) > 0
      && signal?.crossedLevel === true
      && signal?.openedBeyondTrigger !== true
      && signal?.motherStructureNoise !== true
      && signal?.riskStructureShape == null
      && signal?.highLevelDistribution !== true
      && (signal?.launchDistancePercent == null || signal.launchDistancePercent <= 7)
      && (signal?.rhythmScore || 0) >= 88
      && (signal?.sentimentScore || 0) >= 75;
  }

  function isReviewedHigherTimeframeStructureBreak(signal) {
    const foundations = signal?.foundationTypes || [];
    const upper = signal?.triangleLines?.upper;
    const lower = signal?.triangleLines?.lower;
    const recognizedShape = ["falling-wedge", "converging-triangle", "ascending-triangle"]
      .includes(signal?.structureShape);
    const recognizedEnvelope = Boolean(upper && lower)
      && (signal?.consolidationBars || 0) >= 36
      && (signal?.structureQuality || 0) >= 0.48
      && ((signal?.channelSideTransitions || 0) >= 1
        || (signal?.outerEdgeConfirmed === true
          && (signal?.channelInteriorOccupancy || 0) >= 0.62));
    const causalAdvance = signal?.triangleHasPriorAdvance === true
      || signal?.horizontalStructureContextMode === "higher-timeframe-bottom-base"
      || signal?.matureHigherTimeframePostShockRecovery === true;
    const bullishBreakout = !Number.isFinite(Number(signal?.breakoutOpen))
      || !Number.isFinite(Number(signal?.breakoutClose))
      || Number(signal.breakoutClose) > Number(signal.breakoutOpen);
    const trueBoundary = signal?.directStructuralBoundary === true
      || signal?.matureTriangleOuterEdge === true;
    // “结构已识别”和“结构能否执行”必须使用同一套几何事实。只要1小时/4小时
    // 已经画出了完整三角或下降楔形，轨道内有真实来回交易，并由阳线从线下
    // 突破真实上沿，就直接成为 B 点。前高、拐点、EMA90位置、综合分、节奏分
    // 和情绪分都只能排序，不能再把已经成立的结构突破二次过滤。
    return ["1h", "4h"].includes(signal?.interval)
      && foundations.includes("triangle")
      && recognizedShape
      && recognizedEnvelope
      && causalAdvance
      && trueBoundary
      && signal?.crossedLevel === true
      && signal?.openedBeyondTrigger !== true
      && bullishBreakout
      && signal?.motherStructureNoise !== true
      && signal?.oneMinuteMotherBoxNoise !== true
      && signal?.horizontalBrokenOuterPlatform !== true
      && (signal?.trianglePostSelloffRecovery !== true
        || signal?.matureHigherTimeframePostShockRecovery === true)
      && signal?.riskStructureShape == null
      && signal?.highLevelDistribution !== true
      && (signal?.launchDistancePercent == null || signal.launchDistancePercent <= 7);
  }

  function isFourHourCausalStructureIgnition(signal) {
    const hierarchy = signal?.executionHierarchy || assessExecutionHierarchy(signal);
    const foundations = signal?.foundationTypes || [];
    const causalAdvance = signal?.horizontalLaunchHasPriorAdvance === true
      || signal?.triangleHasPriorAdvance === true
      || signal?.horizontalStructureContextMode === "higher-timeframe-bottom-base";
    const trueBoundary = signal?.outerEdgeConfirmed === true
      || signal?.matureTriangleOuterEdge === true
      || signal?.directStructuralBoundary === true;
    // 四小时本来就只挑少数大结构，不能再让某一个综合分数覆盖已经成立的
    // 因果事实。成熟母平台/三角必须先有独立拉升（或大级别筑底豁免），
    // 完整盘整后从线下突破真实外沿；满足这些条件时直接按结构事实判断。
    return signal?.interval === "4h"
      && hierarchy.permit === true
      && ["mother-platform-breakout", "mature-triangle-outer-edge"].includes(
        hierarchy.primaryFoundation,
      )
      && foundations.some((type) => ["base", "triangle"].includes(type))
      && causalAdvance
      && trueBoundary
      && signal?.crossedLevel === true
      && signal?.openedBeyondTrigger !== true
      && (signal?.consolidationBars || 0) >= 28
      && (signal?.structureQuality || 0) >= 0.62
      && (signal?.score || 0) >= 84
      && (signal?.certaintyScore || 0) >= 70
      && (signal?.rhythmScore || 0) >= 55
      && signal?.aboveEma90 === true
      && (signal?.ema90SlopeAtDecision || 0) >= 0
      && signal?.motherStructureNoise !== true
      && signal?.riskStructureShape == null
      && (signal?.launchDistancePercent == null || signal.launchDistancePercent <= 7);
  }

  function isRecognizedHigherTimeframeStructureBreak(signal) {
    const hierarchy = signal?.executionHierarchy || assessExecutionHierarchy(signal);
    const foundations = signal?.foundationTypes || [];
    const causalAdvance = signal?.horizontalLaunchHasPriorAdvance === true
      || signal?.triangleHasPriorAdvance === true
      || signal?.horizontalStructureContextMode === "higher-timeframe-bottom-base"
      || signal?.matureHigherTimeframePostShockRecovery === true;
    const reviewedStructureBreak = signal?.reviewedHigherTimeframeStructureBreak === true
      || isReviewedHigherTimeframeStructureBreak(signal);
    const trueBoundary = signal?.outerEdgeConfirmed === true
      || signal?.matureTriangleOuterEdge === true
      || signal?.directStructuralBoundary === true
      || reviewedStructureBreak;
    // 龙头主升中的 1h / 4h / 日线结构一旦已经由因果层确认，就不再用
    // 综合分、情绪分或固定根数做第二次否决。这里没有放宽结构识别本身：
    // 仍须有前置拉升（或大级别筑底/急杀后成熟修复）、真实外沿、从线下
    // 首次突破，并通过母箱体与风险形态硬否决。
    return ["1h", "4h", "1d"].includes(signal?.interval)
      && hierarchy.permit === true
      && Boolean(hierarchy.primaryFoundation)
      && foundations.some((type) => ["base", "triangle", "relaunch"].includes(type))
      && causalAdvance
      && trueBoundary
      && signal?.crossedLevel === true
      && signal?.openedBeyondTrigger !== true
      && signal?.motherStructureNoise !== true
      && signal?.oneMinuteMotherBoxNoise !== true
      && signal?.riskStructureShape == null
      && signal?.highLevelDistribution !== true
      && (signal?.launchDistancePercent == null || signal.launchDistancePercent <= 7);
  }

  function isMatureTriangleOuterEdgeIgnition(signal) {
    const foundations = signal?.foundationTypes || [];
    const auxiliaries = signal?.auxiliaryTypes || [];
    const upper = signal?.triangleLines?.upper;
    const lower = signal?.triangleLines?.lower;
    const structureBars = Math.max(
      signal?.consolidationBars || 0,
      Number.isFinite(upper?.endIndex) && Number.isFinite(upper?.startIndex)
        ? upper.endIndex - upper.startIndex
        : 0,
    );
    // 这是“真正盘整外沿确认”，不是放宽普通三角：必须同时具备成熟三角、
    // 趋势线上轨、真正前高、前置拉升、完整轨道占用与上行EMA90。订单流在
    // 安静蓄势末端可以很低，因此不把突破前放量设成硬门槛。
    const structuralCore = ["5m", "15m", "1h"].includes(signal?.interval)
      && foundations.includes("triangle")
      && auxiliaries.includes("trendline")
      && auxiliaries.includes("previousHigh")
      && Number.isFinite(signal?.previousHighLevel)
      && signal.previousHighLevel > 0
      && structureBars >= 28
      && (signal?.structureQuality || 0) >= 0.68
      && (signal?.channelInteriorOccupancy || 0) >= 0.55
      && (signal?.channelSideTransitions || 0) >= 3
      && (upper?.touchGroups || 0) >= 2
      && (lower?.touchGroups || 0) >= 2
      && signal?.triangleHasPriorAdvance === true
      && (signal?.trianglePriorAdvanceAtr || 0) >= 0.75
      && signal?.aboveEma90 === true
      && (signal?.ema90SlopeAtDecision || 0) > 0;
    if (!structuralCore) return false;
    if (signal?.matureTriangleOuterEdge === true) {
      // 真正前高高于动态上轨时，“贴近最高压力位”会机械压低通用节奏分；
      // 此处用已经确认的完整包络、触点、换边和7%距离替代这项重复惩罚。
      return (signal?.score || 0) >= 84
        && (signal?.certaintyScore || 0) >= 64
        && (signal?.rhythmScore || 0) >= 64
        && (signal?.sentimentScore || 0) >= 52;
    }
    return (signal?.score || 0) >= 80
      && (signal?.certaintyScore || 0) >= 76
      && (signal?.rhythmScore || 0) >= 72
      && (signal?.sentimentScore || 0) >= 60;
  }

  function isOneHourPlatformPivotIgnition(signal) {
    const foundations = signal?.foundationTypes || [];
    const auxiliaries = signal?.auxiliaryTypes || [];
    // 这是 PI 这类“长平台最后一根拐点突破”的独立 A+ 路径，不是放宽所有
    // 前高：必须先有拉升，再有至少 36 根的一小时盘整，并同时具备横盘母结构、
    // 拐点和真正前高。执行价仍需在突破 K 开盘 7% 以内，且 EMA90 已经上行。
    return signal?.interval === "1h"
      && signal?.oneHourPlatformPivotReady === true
      && signal?.directStructuralBoundary === true
      && foundations.includes("base")
      && auxiliaries.includes("previousHigh")
      && signal?.hasPivot === true
      && (signal?.consolidationBars || 0) >= 36
      && (signal?.structureQuality || 0) >= 0.46
      && (signal?.launchDistancePercent ?? 99) <= 7
      && signal?.horizontalLaunchHasPriorAdvance === true
      && signal?.horizontalLaunchPostSelloffRecovery !== true
      && signal?.aboveEma90 === true
      && (signal?.ema90SlopeAtDecision || 0) >= 0
      && !signal?.riskStructureShape
      && (signal?.score || 0) >= 72
      && (signal?.certaintyScore || 0) >= 64
      && (signal?.rhythmScore || 0) >= 62
      && (signal?.sentimentScore || 0) >= 50;
  }

  function isRapidMainWaveShortDigestionBreakout(signal) {
    const foundations = signal?.foundationTypes || [];
    const auxiliaries = signal?.auxiliaryTypes || [];
    const trigger = Number(signal?.triggerPrice) || 0;
    const breakoutLow = Number(signal?.breakoutLow) || 0;
    const triggerDistancePercent = trigger > 0 && breakoutLow > 0
      ? Math.max(0, trigger / breakoutLow - 1) * 100
      : 99;
    const normalizedEmaSlopePercent = trigger > 0
      ? Math.max(0, Number(signal?.ema90SlopeAtDecision) || 0) / trigger * 100
      : 0;
    // 急拉行情不能强行套用40根横盘。BLESS 这类路径必须先有非常陡峭的
    // 主升斜率，再完成十余根但并不急促抬低点的短消化，最后从线下突破真实
    // 前高。突破K的最终成交量在预埋止损单触发时尚未收完，因此这里只使用
    // 触发前已知的主升斜率、结构质量和节奏；实时监控可再用盘中量速增强提示。
    return ["5m", "15m"].includes(signal?.interval)
      && foundations.length === 1
      && foundations.includes("relaunch")
      && auxiliaries.includes("previousHigh")
      && (signal?.consolidationBars || 0) >= 12
      && (signal?.consolidationBars || 0) <= 22
      && (signal?.structureQuality || 0) >= 0.74
      && normalizedEmaSlopePercent >= 0.8
      && triggerDistancePercent <= 4.5
      && signal?.crossedLevel === true
      && signal?.openedBeyondTrigger !== true
      && signal?.aboveEma90 === true
      && (signal?.ema90SlopeAtDecision || 0) > 0
      && signal?.motherStructureNoise !== true
      && signal?.oneMinuteMotherBoxNoise !== true
      && signal?.horizontalLaunchUrgent !== true
      && signal?.horizontalLaunchInsufficientEdgeDwell !== true
      && signal?.horizontalLaunchPostSelloffRecovery !== true
      && signal?.trianglePostSelloffRecovery !== true
      && signal?.riskStructureShape == null
      && (signal?.score || 0) >= 80
      && (signal?.certaintyScore || 0) >= 70
      && (signal?.rhythmScore || 0) >= 74
      && (signal?.sentimentScore || 0) >= 72;
  }

  function assessExecutionHierarchy(signal) {
    const foundations = new Set((signal?.foundationTypes || []).map(String));
    const auxiliaries = new Set((signal?.auxiliaryTypes || []).map(String));
    const hasBase = foundations.has("base");
    const hasTriangle = foundations.has("triangle");
    const hasPreviousHigh = auxiliaries.has("previousHigh")
      || Number.isFinite(Number(signal?.previousHighLevel))
      || signal?.outerEdgeConfirmed === true;
    const hasTrendline = auxiliaries.has("trendline") || Boolean(signal?.trendline);
    const bars = Number(signal?.consolidationBars) || 0;
    const launchDistance = signal?.launchDistancePercent == null
      ? 0
      : Number(signal.launchDistancePercent);
    const matureHigherTimeframePostShockRecovery = signal?.matureHigherTimeframePostShockRecovery === true;
    const matureOneHourLongTriangleReset = signal?.matureOneHourLongTriangleReset === true
      || isMatureOneHourLongTriangleReset(signal);
    const longBasePreviousHighIgnition = signal?.longBasePreviousHighIgnition === true
      || isLongBasePreviousHighIgnition(signal);
    const softTestExtendedTriangleBreakout = signal?.softTestExtendedTriangleBreakout === true
      || isSoftTestExtendedTriangleBreakout(signal);
    const matureOneHourOuterPlatformReset = signal?.matureOneHourOuterPlatformReset === true
      || isMatureOneHourOuterPlatformReset(signal);
    const oneHourRelaunchPivotIgnition = signal?.oneHourRelaunchPivotIgnition === true
      || isOneHourRelaunchPivotIgnition(signal);
    const oneHourCompactAscendingTriangleIgnition = signal?.oneHourCompactAscendingTriangleIgnition === true
      || isOneHourCompactAscendingTriangleIgnition(signal);
    const matureFifteenMinuteRetryPlatformIgnition = signal?.matureFifteenMinuteRetryPlatformIgnition === true
      || isMatureFifteenMinuteRetryPlatformIgnition(signal);
    const matureFifteenMinutePriorHighTriangleIgnition = signal?.matureFifteenMinutePriorHighTriangleIgnition === true
      || isMatureFifteenMinutePriorHighTriangleIgnition(signal);
    const shockMotherBoxOuterEdgeBreakout = signal?.shockMotherBoxOuterEdge === true;
    const reviewedHigherTimeframeStructureBreak = signal?.reviewedHigherTimeframeStructureBreak === true
      || isReviewedHigherTimeframeStructureBreak(signal);
    const eliteOccupiedFiveMinuteTriangle = signal?.interval === "5m"
      && hasBase
      && hasTriangle
      && signal?.directStructuralBoundary === true
      && bars >= 36
      && (signal?.structureQuality || 0) >= 0.78
      && (signal?.channelInteriorOccupancy || 0) >= 0.78
      && (signal?.channelSideTransitions || 0) >= 2
      && signal?.triangleHasPriorAdvance === true
      && (signal?.trianglePriorAdvanceAtr || 0) >= 4
      && !signal?.riskStructureShape;
    const fiveMinuteQuietEdgeNotSeasoned = signal?.interval === "5m"
      && hasBase
      && hasTriangle
      && !eliteOccupiedFiveMinuteTriangle
      && (signal?.relativeVolume || 0) < 1.05
      && (signal?.orderFlowScore || 0) < 45
      && (signal?.ceilingAge || 0) < 18
      && (signal?.ceilingTouches || 0) < 6;
    // 横盘子模型会把“末端低点快速抬高”标记为急促，这对普通横盘是必要的
    // 否决；但当同一候选已经独立完成成熟三角真实外沿时，横盘只是并列的
    // 子标签，不能反过来抹掉三角本身。这里仍要求足够长的结构、三次换边、
    // 高占用率和明确的涨前推动，因此不会把几根K的上升楔形放回来。
    const matureTriangleOverridesHorizontalSubmodel = hasTriangle
      && signal?.matureTriangleOuterEdge === true
      && signal?.directStructuralBoundary === true
      && bars >= 40
      && (signal?.structureQuality || 0) >= 0.68
      && (signal?.channelInteriorOccupancy || 0) >= 0.68
      && (signal?.channelSideTransitions || 0) >= 3
      && signal?.triangleHasPriorAdvance === true
      && (signal?.trianglePriorAdvanceAtr || 0) >= 4
      && (signal?.certaintyScore || 0) >= 84
      && (signal?.rhythmScore || 0) >= 66
      && (signal?.sentimentScore || 0) >= 60
      && !signal?.riskStructureShape;
    const independentNestedMainWaveStructure = isIndependentNestedMainWaveStructure(signal);
    // 附带三角的换边次数不足，只能否定“三角”这条子逻辑，不能反向否定一个
    // 已经独立成熟的母平台。平台必须有 40 根以上、极清晰外沿和强前置拉升；
    // 因此 RAVE 04-09 21:00 的真实横盘会保留，而普通单边斜移仍继续过滤。
    const maturePlatformOverridesWeakTriangle = hasBase
      && signal?.outerEdgeConfirmed === true
      && (signal?.outerEdgeScore || 0) >= 84
      && bars >= 40
      && signal?.horizontalLaunchHasPriorAdvance === true
      && (signal?.horizontalLaunchPriorAdvanceAtr || 0) >= 6
      && signal?.horizontalLaunchUrgent !== true
      && signal?.horizontalLaunchInsufficientEdgeDwell !== true
      && signal?.horizontalLaunchPostSelloffRecovery !== true
      && (signal?.launchDistancePercent == null || signal.launchDistancePercent <= 7)
      && !signal?.riskStructureShape;
    const hardRisk = signal?.openedBeyondTrigger === true
      || (signal?.insideMotherBase === true
        && !independentNestedMainWaveStructure
        && !matureOneHourLongTriangleReset
        && !matureOneHourOuterPlatformReset
        && !softTestExtendedTriangleBreakout
        && !reviewedHigherTimeframeStructureBreak)
      || (signal?.motherStructureNoise === true
        && !matureHigherTimeframePostShockRecovery
        && !matureOneHourLongTriangleReset
        && !matureOneHourOuterPlatformReset
        && !softTestExtendedTriangleBreakout
        && !reviewedHigherTimeframeStructureBreak)
      || signal?.oneMinuteMotherBoxNoise === true
      || signal?.horizontalBrokenOuterPlatform === true
      || (signal?.horizontalLaunchUrgent === true
        && !matureTriangleOverridesHorizontalSubmodel
        && !reviewedHigherTimeframeStructureBreak)
      || (signal?.horizontalLaunchInsufficientEdgeDwell === true
        && !matureTriangleOverridesHorizontalSubmodel
        && !reviewedHigherTimeframeStructureBreak)
      || (signal?.horizontalLaunchPostSelloffRecovery === true
        && !reviewedHigherTimeframeStructureBreak)
      || (signal?.trianglePostSelloffRecovery === true
        && !reviewedHigherTimeframeStructureBreak)
      || (Boolean(signal?.riskStructureShape) && !matureFifteenMinuteRetryPlatformIgnition)
      || Boolean(signal?.highLevelDistribution)
      || (isWeaklyRotatingShortFrameConvergence(signal)
        && !maturePlatformOverridesWeakTriangle
        && !independentNestedMainWaveStructure
        && !matureFifteenMinutePriorHighTriangleIgnition
        && !reviewedHigherTimeframeStructureBreak)
      || fiveMinuteQuietEdgeNotSeasoned
      || launchDistance > 7;
    const baseHasCausalAdvance = signal?.horizontalLaunchHasPriorAdvance !== false
      || signal?.shockBoxHorizontalLaunchException === true
      || shockMotherBoxOuterEdgeBreakout
      || signal?.ema90ReclaimContinuation === true
      || signal?.terminalEma90BoundaryReclaim === true
      || signal?.horizontalStructureContextMode === "new-coin-not-falling";
    const triangleHasCausalAdvance = signal?.triangleHasPriorAdvance !== false
      || signal?.shockBoxAscendingTriangleException === true
      || signal?.ema90ReclaimContinuation === true
      || signal?.terminalEma90BoundaryReclaim === true
      || (independentNestedMainWaveStructure
        && signal?.horizontalLaunchHasPriorAdvance === true
        && (signal?.horizontalLaunchPriorAdvanceAtr || 0) >= 10);
    const compactOneHourMotherPlatform = signal?.interval === "1h"
      && signal?.clusteredCeilingBand === true
      && bars >= 12
      && (signal?.structureQuality || 0) >= 0.6
      && (signal?.platformTouchGroups || 0) >= 2
      && (signal?.ceilingTouches || 0) >= 7;
    const compactFiveMinuteMotherPlatform = signal?.interval === "5m"
      && bars >= 24
      && (signal?.outerEdgeScore || 0) >= 84
      && (signal?.platformTouchGroups || 0) >= 3
      && (signal?.ceilingTouches || 0) >= 4
      && (signal?.rhythmScore || 0) >= 60;
    const motherPlatformMinimumBars = signal?.interval === "5m" ? 28 : 18;
    const matureMotherPlatform = hasBase
      && signal?.outerEdgeConfirmed === true
      && (signal?.outerEdgeScore || 0) >= 62
      && (bars >= motherPlatformMinimumBars
        || compactOneHourMotherPlatform
        || compactFiveMinuteMotherPlatform)
      && launchDistance <= 7
      && ((signal?.ceilingAge || 0) >= 3 || (signal?.platformTouchGroups || 0) >= 2)
      && baseHasCausalAdvance;
    const matureTriangleBoundary = hasTriangle
      && triangleHasCausalAdvance
      && bars >= 28
      && (signal?.structureQuality || 0) >= 0.62
      && (signal?.matureTriangleOuterEdge === true
        || (signal?.directStructuralBoundary === true && (hasPreviousHigh || hasTrendline)));
    // 少于 28 根的三角不能仅凭“看起来收敛”取得执行权。只有已经完成多次
    // 上下沿换边、轨道内部占用充分，并同时突破真前高的紧凑三角，才视为
    // 成熟母结构。它覆盖的是清晰的 20 余根三角，不会放开普通几根K的小波动。
    const compactOccupiedTriangleBoundary = hasTriangle
      && triangleHasCausalAdvance
      && hasPreviousHigh
      && signal?.hasPivot === true
      && bars >= (signal?.interval === "5m" ? 28 : 18)
      && (signal?.structureQuality || 0) >= 0.68
      && (signal?.channelInteriorOccupancy || 0) >= 0.78
      && ((signal?.channelSideTransitions || 0) >= 3
        || ((signal?.structureQuality || 0) >= 0.78
          && (signal?.channelInteriorOccupancy || 0) >= 0.8
          && (signal?.channelSideTransitions || 0) >= 2));
    // 末端 40 根以上已经在两条边界间充分换边，突破时又有明显量速确认时，
    // 5分钟动态上轨本身就是可执行的一级结构；不强迫它同时伪造一条附近前高。
    // 这条路径仍要求前置拉升和完整轨道占用，错误的一次换边/空心三角无法进入。
    const upperBoundary = signal?.triangleLines?.upper;
    const triangleLineCrossedDirectly = Number.isFinite(upperBoundary?.endPrice)
      && Number.isFinite(signal?.triggerPrice)
      && Math.abs(signal.triggerPrice - upperBoundary.endPrice)
        <= Math.max((signal?.atrAtDecision || 0) * 0.35, signal.triggerPrice * 0.0015)
      && signal?.crossedLevel === true
      && signal?.openedBeyondTrigger !== true;
    const rotatedFiveMinuteTriangleBoundary = signal?.interval === "5m"
      && hasTriangle
      && triangleHasCausalAdvance
      && (signal?.directStructuralBoundary === true || triangleLineCrossedDirectly)
      && bars >= 40
      && (signal?.structureQuality || 0) >= 0.58
      && (signal?.channelInteriorOccupancy || 0) >= 0.8
      && (signal?.channelSideTransitions || 0) >= 3
      && ((signal?.relativeVolume || 0) >= 1.1 || (signal?.orderFlowScore || 0) >= 60)
      && signal?.crossedLevel === true;
    // 1小时母平台常会被四根15分钟K合并成十几根，看起来比15分钟结构短。
    // TURBO 这类候选仍必须在1小时上独立具备回踩母结构、真前高、上行EMA90、
    // 高结构质量和从线下首次突破；15分钟只负责精确触发，不能凭空制造结构。
    const compactParentJudgementScore = (signal?.certaintyScore || 0)
      + (signal?.rhythmScore || 0)
      + (signal?.sentimentScore || 0);
    const compactOneHourParentPlatform = signal?.interval === "1h"
      && foundations.has("relaunch")
      && hasPreviousHigh
      && bars >= 10
      && (signal?.structureQuality || 0) >= 0.8
      // 根数略少时，用结构确定性、节奏与情绪的组合判断补足；三项都不能太差。
      && (signal?.certaintyScore || 0) >= 78
      && (signal?.rhythmScore || 0) >= 66
      && (signal?.sentimentScore || 0) >= 60
      && compactParentJudgementScore >= 206
      && signal?.aboveEma90 === true
      && (signal?.ema90SlopeAtDecision || 0) >= 0
      && Number.isFinite(Number(signal?.breakoutLow))
      && Math.max(0, signal.triggerPrice / Math.max(signal.breakoutLow, 1e-8) - 1) * 100 <= 7
      && signal?.crossedLevel === true
      && signal?.openedBeyondTrigger !== true;
    const explicitException = signal?.ema90ReclaimContinuation === true
      || signal?.terminalEma90BoundaryReclaim === true
      || signal?.shockBoxHorizontalLaunchException === true
      || signal?.shockBoxAscendingTriangleException === true
      || signal?.oneHourPlatformPivotReady === true
      || matureHigherTimeframePostShockRecovery
      || independentNestedMainWaveStructure
      || matureOneHourLongTriangleReset
      || longBasePreviousHighIgnition
      || softTestExtendedTriangleBreakout
      || matureOneHourOuterPlatformReset
      || oneHourRelaunchPivotIgnition
      || oneHourCompactAscendingTriangleIgnition
      || matureFifteenMinuteRetryPlatformIgnition
      || matureFifteenMinutePriorHighTriangleIgnition
      || reviewedHigherTimeframeStructureBreak
      || shockMotherBoxOuterEdgeBreakout
      || isRapidMainWaveShortDigestionBreakout(signal)
      || isOneHourPostImpulseWedgeIgnition(signal)
      || (signal?.interval === "1m" && isOneMinutePostImpulseHorizontalLaunch(signal));
    const primaryFoundation = matureMotherPlatform
      ? "mother-platform-breakout"
      : matureTriangleBoundary || compactOccupiedTriangleBoundary || rotatedFiveMinuteTriangleBoundary
        ? "mature-triangle-outer-edge"
        : compactOneHourParentPlatform
          ? "compact-one-hour-parent-platform"
      : explicitException
          ? "explicit-structural-exception"
          : "";
    const childStructures = [];
    if (hasBase && signal?.horizontalLaunchQualified !== false) {
      childStructures.push("horizontal-launch");
    }
    const boosters = [];
    if (hasPreviousHigh) boosters.push("previous-high");
    if (hasTrendline) boosters.push("trendline");
    if (signal?.hasPivot === true) boosters.push("pivot");
    if ((signal?.relativeVolume || 0) >= 1.15 || (signal?.orderFlowScore || 0) >= 65) boosters.push("volume-flow");
    if (signal?.ema90ReclaimContinuation === true || signal?.terminalEma90BoundaryReclaim === true) {
      boosters.push("ema90-reclaim");
    }
    if (signal?.multiTimeframeConfluence === true) boosters.push("multi-timeframe");
    const missing = [];
    if (!primaryFoundation) missing.push("missing-mother-boundary");
    if ((hasBase || hasTriangle) && !baseHasCausalAdvance && !triangleHasCausalAdvance) missing.push("missing-prior-advance");
    if (launchDistance > 7) missing.push("launch-distance-over-7-percent");
    if (fiveMinuteQuietEdgeNotSeasoned) missing.push("five-minute-edge-not-seasoned");
    if (hardRisk) missing.push("hard-structure-veto");
    const missingLabelMap = {
      "missing-mother-boundary": "缺少成熟母平台或三角真实外沿",
      "missing-prior-advance": "结构前缺少独立拉升",
      "launch-distance-over-7-percent": "起涨位置到前高超过7%",
      "five-minute-edge-not-seasoned": "5分钟安静突破缺少足够久的外沿记忆或反复试顶",
      "hard-structure-veto": "存在母箱体内部、急促推进或风险结构硬否决",
    };
    const permit = Boolean(primaryFoundation) && !hardRisk;
    const tier = permit
      ? primaryFoundation === "explicit-structural-exception" ? "exception" : "core"
      : "none";
    const primaryFoundationLabel = primaryFoundation === "mother-platform-breakout"
      ? "母平台真实外沿 / 盘整前高突破"
      : primaryFoundation === "mature-triangle-outer-edge"
        ? "成熟三角真实外沿突破"
        : primaryFoundation === "compact-one-hour-parent-platform"
          ? "紧凑一小时母平台 / 真前高突破"
        : primaryFoundation === "explicit-structural-exception"
          ? "已验证的结构例外"
          : "未形成一级执行结构";
    return {
      permit,
      tier,
      primaryFoundation,
      primaryFoundationLabel,
      childStructures,
      boosters,
      missing: [...new Set(missing)],
      missingLabels: [...new Set(missing)].map((key) => missingLabelMap[key] || key),
      maturity: primaryFoundation === "compact-one-hour-parent-platform"
        ? "compact-one-hour-qualified"
        : bars >= 40 ? "mature-center" : bars >= 28 ? "compact-mature" : bars >= 18 ? "short-qualified" : "immature",
      scoreAuthority: "rank-only",
    };
  }

  function isHighCertaintyEntry(signal) {
    const hierarchy = signal?.executionHierarchy || assessExecutionHierarchy(signal);
    if (!hierarchy.permit) return false;
    const foundations = signal.foundationTypes || [];
    const auxiliaries = signal.auxiliaryTypes || [];
    const hasBase = foundations.includes("base");
    const hasTriangle = foundations.includes("triangle");
    const hasRelaunch = foundations.includes("relaunch");
    const hasPivot = Boolean(signal.hasPivot);
    const hasTrendline = auxiliaries.includes("trendline");
    const hasPreviousHigh = auxiliaries.includes("previousHigh");
    const bars = signal.consolidationBars || 0;
    const certainty = signal.certaintyScore ?? 0;
    const rhythm = signal.rhythmScore ?? 0;
    const sentiment = signal.sentimentScore ?? 0;
    // 已确认的平台外沿是独立执行逻辑：它不需要趋势线、附近阳线或拐点来“凑共振”。
    // 真正的门槛在平台自身——外沿必须事先形成、价格在其下反复盘整，并从线下首次穿越。
    const confirmedPlatformBreak = hasBase
      && Boolean(signal.outerEdgeConfirmed)
      && (signal.outerEdgeScore || 0) >= 62
      && bars >= 18
      && (signal.launchDistancePercent ?? 99) <= 7
      && ((signal.ceilingAge || 0) >= 3 || (signal.platformTouchGroups || 0) >= 2);

    // 外沿箱体起爆：SHIB 一类盘整前高突破，必须同时有回踩/三角与拐点确认。
    const outerBoxIgnition = hasBase
      && (hasRelaunch || hasTriangle)
      && hasPivot
      && bars >= 28
      && certainty >= 80
      && rhythm >= 68
      && sentiment >= 60;
    // 长时间母结构里的趋势线只作扣扳机辅助，必须再有前高确认；不能单独开仓。
    const matureTrendlineAssist = hasBase
      && hasTrendline
      && hasPreviousHigh
      && bars >= 40
      && certainty >= 72
      && rhythm >= 72
      && sentiment >= 58;
    // 没有拐点的纯箱体突破，只保留节奏本身已经达到 A+ 的极少数。
    const eliteBoxBreak = hasBase
      && hasPreviousHigh
      && bars >= 35
      && certainty >= 86
      && rhythm >= 78
      && sentiment >= 70;
    const eliteRelaunch = hasRelaunch
      && hasPivot
      && bars >= 8
      && certainty >= 82
      && rhythm >= 76
      && sentiment >= 70;
    const eliteTriangle = hasTriangle
      && bars >= 18
      && certainty >= 82
      && rhythm >= 76
      && sentiment >= 65;
    return confirmedPlatformBreak
      || outerBoxIgnition
      || matureTrendlineAssist
      || eliteBoxBreak
      || eliteRelaunch
      || eliteTriangle
      || isMatureTriangleOuterEdgeIgnition(signal)
      || isOneHourPlatformPivotIgnition(signal)
      || isRapidMainWaveShortDigestionBreakout(signal)
      || isOneHourPostImpulseWedgeIgnition(signal)
      || isMatureOneHourLongTriangleReset(signal)
      || isLongBasePreviousHighIgnition(signal)
      || isSoftTestExtendedTriangleBreakout(signal)
      || signal?.matureOneHourOuterPlatformReset === true
      || isMatureOneHourOuterPlatformReset(signal)
      || signal?.oneHourRelaunchPivotIgnition === true
      || isOneHourRelaunchPivotIgnition(signal)
      || signal?.oneHourCompactAscendingTriangleIgnition === true
      || isOneHourCompactAscendingTriangleIgnition(signal)
      || signal?.matureFifteenMinuteRetryPlatformIgnition === true
      || isMatureFifteenMinuteRetryPlatformIgnition(signal)
      || signal?.reviewedHigherTimeframeStructureBreak === true
      || isReviewedHigherTimeframeStructureBreak(signal)
      || signal?.shockMotherBoxOuterEdge === true;
  }

  // 龙头的第一段主升启动不能反向要求 4h / 日线已经进入主升，否则最重要的
  // 起爆点永远会被确认得太晚。这里只识别一种极窄的先行许可：5m~1h 已经
  // 完成独立上推、成熟上升三角、母平台真实外沿与前高同 K 突破。
  // 若左侧宽区间的高点出现在回看窗口最前端，且随后没有“先拉升再急杀”的
  // 成箱过程，那只是上市早期直接下跌留下的旧边界，不能定义后续母箱体。
  function isPreHigherFrameLeaderMainWaveIgnition(signal) {
    const foundations = signal?.foundationTypes || [];
    const auxiliaries = signal?.auxiliaryTypes || [];
    const motherSpan = Number(signal?.motherStructureBars) || 0;
    const motherStart = (Number(signal?.index) || 0) - motherSpan;
    const motherPeakOffset = (Number(signal?.motherShockPeakIndex) || 0) - motherStart;
    const staleUnqualifiedMotherBoundary = signal?.motherStructureMode === "unordered-mother-box"
      && motherSpan >= 120
      && motherPeakOffset >= 0
      // “最前端”按约前五分之一处理；PI 完整历史中的上市旧高位于 19.6%，
      // 后面还有约 386 根已完成 K 线足以证明它不是当前盘整的有效外沿。
      && motherPeakOffset <= motherSpan * 0.22;
    const motherContextAllowed = signal?.motherStructureNoise !== true
      || signal?.shockBoxAscendingTriangleException === true
      || staleUnqualifiedMotherBoundary;
    return ["5m", "15m", "1h"].includes(signal?.interval)
      && foundations.includes("base")
      && foundations.includes("triangle")
      && auxiliaries.includes("previousHigh")
      && signal?.hasPivot === true
      && signal?.structureShape === "ascending-triangle"
      && signal?.outerEdgeConfirmed === true
      && signal?.directStructuralBoundary === true
      // 完整上升三角为 36 根时，内部小箱体可以只有 20 余根。
      // 此处审核的是“三角 + 真外沿”整体，不再用过去被虚假长回看
      // 撑高的 40 根 / 90 分门槛。其余触点、占用率、涨前推动与节奏条件仍严格保留。
      && (signal?.consolidationBars || 0) >= 36
      && (signal?.outerEdgeScore || 0) >= 82
      && (signal?.ceilingTouches || 0) >= 3
      && (signal?.platformTouchGroups || 0) >= 2
      && (signal?.structureQuality || 0) >= 0.65
      && (signal?.channelInteriorOccupancy || 0) >= 0.85
      && (signal?.channelSideTransitions || 0) >= 3
      && signal?.triangleHasPriorAdvance === true
      && (signal?.trianglePriorAdvanceAtr || 0) >= 4
      && signal?.trianglePostSelloffRecovery !== true
      && signal?.horizontalLaunchPostSelloffRecovery !== true
      && signal?.mainWaveStage === "active"
      && signal?.aboveEma90 === true
      && (signal?.ema90SlopeAtDecision || 0) >= 0
      && !signal?.riskStructureShape
      && (signal?.launchDistancePercent ?? 99) <= 7
      && (signal?.structuralRiskPercent ?? 99) <= 4
      && (signal?.rhythmScore || 0) >= 74
      && (signal?.sentimentScore || 0) >= 60
      && (signal?.orderFlowScore || 0) >= 45
      && (signal?.certaintyScore || 0) >= 88
      && motherContextAllowed;
  }

  function promotePreHigherFrameLeaderMainWaveIgnition(result, signal, preselectedLeader) {
    if (!preselectedLeader
      || !isPreHigherFrameLeaderMainWaveIgnition(signal)
      || signal?.motherStructureMode !== "unordered-mother-box") return null;
    const staleMotherReason = (reason) => reason.includes("仍在更大母箱体内部无序波动");
    const oversizedBaseStartReason = (reason) => (
      reason === "横盘起飞前缺少向上推动，不能把普通横盘或跌后修复当作主升接力"
    );
    const awaitingMainWaveContextReason = (reason) => (
      reason === "上市旧下跌边界已失效，等待龙头或人工主升语境确认"
    );
    const reasons = signal.reasons || [];
    const oldMotherBoundaryAlreadyCleared = signal.mainWaveOldDeclinePressureException === true;
    if ((!oldMotherBoundaryAlreadyCleared && !reasons.some(staleMotherReason))
      || reasons.some((reason) => (
        !staleMotherReason(reason)
        && !oversizedBaseStartReason(reason)
        && !awaitingMainWaveContextReason(reason)
      ))) return null;
    const current = result?.candles?.[signal.index];
    if (!current || current.open >= signal.triggerPrice || current.high < signal.triggerPrice) return null;
    return {
      ...signal,
      id: `${signal.id}-leader-main-wave-ignition`,
      promotedFromId: signal.id,
      status: "buy",
      reasons: [],
      score: Math.max(signal.score || 0, 88),
      certaintyScore: Math.max(signal.certaintyScore || 0, 92),
      motherStructureNoise: false,
      staleUnqualifiedMotherBoundaryIgnored: true,
      preHigherFrameMainWaveIgnitionPermit: true,
      evidence: [
        ...(signal.evidence || []),
        "左侧旧高低来自无前置拉升的上市早期直接下跌，不具备母箱体边界资格",
        `后续已重新完成 ${signal.trianglePriorAdvanceAtr.toFixed(2)} ATR 独立上推，并形成成熟上升三角与真实平台外沿`,
        "龙头主升启动不反向要求大周期主升已经形成；只执行本周期从线下首次突破的 A+ 起爆点",
      ],
    };
  }

  function isLeaderIndependentCurrentStructure(signal) {
    const foundations = signal?.foundationTypes || [];
    const bars = Number(signal?.consolidationBars) || 0;
    const priorAdvanceAtr = Math.max(
      Number(signal?.horizontalLaunchPriorAdvanceAtr) || 0,
      Number(signal?.trianglePriorAdvanceAtr) || 0,
    );
    const maturePlatform = foundations.includes("base")
      && signal?.outerEdgeConfirmed === true
      && (signal?.outerEdgeScore || 0) >= 90
      && bars >= 32
      && (signal?.ceilingTouches || 0) >= 6
      && (signal?.platformTouchGroups || 0) >= 3;
    const matureTriangle = foundations.includes("triangle")
      && signal?.matureTriangleOuterEdge === true
      && signal?.directStructuralBoundary === true
      && bars >= 36
      && (signal?.structureQuality || 0) >= 0.68
      && (signal?.channelInteriorOccupancy || 0) >= 0.78
      && (signal?.channelSideTransitions || 0) >= 3;
    const rhythmReady = (signal?.rhythmScore || 0) >= 66
      || ((signal?.rhythmScore || 0) >= 60
        && (signal?.orderFlowScore || 0) >= 75
        && (signal?.relativeVolume || 0) >= 1.5);
    return ["5m", "15m", "1h"].includes(signal?.interval)
      && (maturePlatform || matureTriangle)
      && signal?.hasPivot === true
      && priorAdvanceAtr >= 7
      && signal?.horizontalLaunchPostSelloffRecovery !== true
      && signal?.trianglePostSelloffRecovery !== true
      && signal?.horizontalBrokenOuterPlatform !== true
      && signal?.riskStructureShape == null
      && signal?.openedBeyondTrigger !== true
      && (signal?.launchDistancePercent == null || signal.launchDistancePercent <= 7)
      && signal?.aboveEma90 === true
      && (signal?.ema90SlopeAtDecision || 0) >= 0
      && (signal?.score || 0) >= 84
      && (signal?.certaintyScore || 0) >= 84
      && (signal?.sentimentScore || 0) >= 50
      && rhythmReady
      && ((signal?.orderFlowScore || 0) >= 55 || (signal?.relativeVolume || 0) >= 1.25);
  }

  // 龙头长历史会让 240/320 根回看把已经失效的旧高误当成当前母压力。
  // 只有当前结构自己已经完成真实外沿、涨前推动、拐点和订单流确认时，才把
  // 旧边界降为背景；急杀箱体、当前上升楔形和没有独立推动的反弹仍不放行。
  function promoteLeaderIndependentCurrentStructure(result, signal, preselectedLeader) {
    if (!preselectedLeader || !isLeaderIndependentCurrentStructure(signal)) return null;
    const reasons = signal?.reasons || [];
    const staleMotherReason = (reason) => reason.includes("仍在更大母箱体内部无序波动")
      || reason.includes("仍在冲高后的母压力区间内部");
    const hierarchyReason = (reason) => reason.startsWith("因果层级未通过：");
    if (!reasons.some(staleMotherReason)
      || reasons.some((reason) => !staleMotherReason(reason) && !hierarchyReason(reason))) return null;
    const unorderedOldBoundary = signal?.motherStructureMode === "unordered-mother-box"
      && signal?.oldMotherBoundaryPrecedesIndependentAdvance === true;
    const resetAfterOldImpulsePeak = signal?.motherStructureMode === "post-impulse-high-level-rotation"
      && signal?.oldMotherBoundaryPrecedesIndependentAdvance === true
      && (signal?.motherStructurePosition || 0) >= 0.78;
    if (!unorderedOldBoundary && !resetAfterOldImpulsePeak) return null;
    const current = result?.candles?.[signal.index];
    if (!current || signal?.crossedLevel !== true || current.open >= signal.triggerPrice || current.high < signal.triggerPrice) {
      return null;
    }
    const promoted = {
      ...signal,
      id: `${signal.id}-leader-independent-structure-reset`,
      promotedFromId: signal.id,
      status: "buy",
      reasons: [],
      motherStructureNoise: false,
      insideMotherBase: false,
      leaderIndependentStructureReset: true,
      staleUnqualifiedMotherBoundaryIgnored: true,
      evidence: [
        ...(signal.evidence || []),
        "龙头长历史中的旧高已早于当前独立上推；当前结构重新形成真实外沿，旧压力降为背景",
        "只放行本周期已有涨前推动、成熟平台/三角、拐点和量价确认的首次线下突破",
      ],
    };
    promoted.executionHierarchy = assessExecutionHierarchy(promoted);
    return promoted.executionHierarchy.permit ? promoted : null;
  }

  function promoteLeaderEliteStructureRetry(result, signal, preselectedLeader) {
    if (!preselectedLeader) return null;
    const reasons = signal?.reasons || [];
    if (reasons.length !== 1 || !reasons[0].includes("前次试错止损后尚未形成新的高质量母平台")) {
      return null;
    }
    const foundations = signal?.foundationTypes || [];
    const eliteIndependentReset = ["5m", "15m", "1h"].includes(signal?.interval)
      && foundations.includes("base")
      && foundations.includes("triangle")
      && signal?.outerEdgeConfirmed === true
      && signal?.matureTriangleOuterEdge === true
      && signal?.directStructuralBoundary === true
      && (signal?.consolidationBars || 0) >= 40
      && (signal?.outerEdgeScore || 0) >= 85
      && (signal?.platformTouchGroups || 0) >= 3
      && (signal?.structureQuality || 0) >= 0.68
      && (signal?.channelInteriorOccupancy || 0) >= 0.82
      && (signal?.channelSideTransitions || 0) >= 3
      && signal?.triangleHasPriorAdvance === true
      && (signal?.trianglePriorAdvanceAtr || 0) >= 5
      && signal?.mainWaveStage === "active"
      && signal?.motherStructureNoise !== true
      && signal?.horizontalLaunchUrgent !== true
      && signal?.horizontalLaunchPostSelloffRecovery !== true
      && signal?.trianglePostSelloffRecovery !== true
      && signal?.riskStructureShape == null
      && signal?.openedBeyondTrigger !== true
      && (signal?.score || 0) >= 92
      && (signal?.certaintyScore || 0) >= 95
      && (signal?.rhythmScore || 0) >= 80
      && (signal?.sentimentScore || 0) >= 75;
    if (!eliteIndependentReset) return null;
    const current = result?.candles?.[signal.index];
    if (!current || signal?.crossedLevel !== true || current.open >= signal.triggerPrice || current.high < signal.triggerPrice) {
      return null;
    }
    return {
      ...signal,
      id: `${signal.id}-leader-elite-retry`,
      promotedFromId: signal.id,
      status: "buy",
      reasons: [],
      leaderEliteStructureRetry: true,
      evidence: [
        ...(signal.evidence || []),
        "前次试错后当前平台与成熟三角已独立重合，真实外沿重新从线下突破，按新机会立即重入",
        "重入依据是当前结构质量而非固定等待根数；普通原位复穿仍继续过滤",
      ],
    };
  }

  function causalBreakoutLow(byInterval, result, signal) {
    const parent = result.candles?.[signal.index];
    if (!parent) return null;
    const parentOpen = finite(parent.open) || finite(signal.price) || finite(parent.close);
    const childPreferences = {
      "4h": ["1m", "5m", "15m", "1h"],
      "1h": ["1m", "5m", "15m"],
      "15m": ["1m", "5m"],
      "5m": ["1m"],
    };
    const duration = INTERVAL_MS[result.interval] || 0;
    const endTime = signal.time + duration;
    for (const childInterval of childPreferences[result.interval] || []) {
      const child = byInterval.get(childInterval);
      if (!child?.candles?.length) continue;
      const inside = child.candles.filter((row) => row.time >= signal.time && row.time < endTime);
      const crossIndex = inside.findIndex((row) => row.high >= signal.triggerPrice);
      if (crossIndex < 0) continue;
      // 首次上穿所在的子 K 线内部仍没有 OHLC 先后顺序，因此只使用它的开盘价；
      // 更早子 K 线的低点已经完成，可安全计入，不使用上穿后的任何低点。
      const completedLows = inside.slice(0, crossIndex).map((row) => finite(row.low)).filter((value) => value > 0);
      const crossingOpen = finite(inside[crossIndex].open) || parentOpen;
      const low = Math.min(parentOpen, crossingOpen, ...completedLows);
      return { low, sourceInterval: childInterval, crossTime: inside[crossIndex].time };
    }
    return { low: parentOpen, sourceInterval: "open-fallback", crossTime: signal.time };
  }

  function findHigherStructureAnchor(byInterval, result, signal) {
    if (result.interval !== "1h") return null;
    const higher = byInterval.get("4h");
    if (!higher) return null;
    const current = result.candles?.[signal.index];
    const prior = result.candles?.[signal.index - 1];
    if (!current || !prior) return null;
    const decisionTime = signal.decisionTime ?? signal.time;
    const anchors = [
      ...(higher.signals || []),
      ...(higher.pending || []),
      ...(higher.rejected || []),
    ].filter((anchor) => {
      const foundations = anchor.foundationTypes || [];
      const auxiliaries = anchor.auxiliaryTypes || [];
      const anchorTime = anchor.decisionTime ?? anchor.time;
      const age = decisionTime - anchorTime;
      const higherTriggerKnown = Math.max(prior.close, current.open) >= anchor.triggerPrice;
      const triggerSeparation = signal.triggerPrice / Math.max(anchor.triggerPrice, 1e-8) - 1;
      return age >= 0
        && age < INTERVAL_MS["4h"]
        && foundations.includes("triangle")
        && auxiliaries.includes("trendline")
        && (anchor.consolidationBars || 0) >= 48
        && (anchor.certaintyScore || 0) >= 82
        && (anchor.score || 0) >= 84
        && higherTriggerKnown
        && triggerSeparation >= -0.01
        && triggerSeparation <= 0.07;
    });
    return anchors.sort((a, b) => (
      (b.certaintyScore || 0) - (a.certaintyScore || 0)
      || (b.decisionTime ?? b.time) - (a.decisionTime ?? a.time)
    ))[0] || null;
  }

  function promoteCrossFramePrecision(byInterval, result, signal) {
    const current = result.candles?.[signal.index];
    if (!current || result.interval !== "1h") return null;
    const foundations = signal.foundationTypes || [];
    const auxiliaries = signal.auxiliaryTypes || [];
    const removableReason = "母结构尚未成熟：盘整、压缩或贴线蓄力不足";
    const remainingReasons = (signal.reasons || []).filter((reason) => reason !== removableReason);
    if (!foundations.includes("triangle")
      || !auxiliaries.includes("trendline")
      || (signal.consolidationBars || 0) < 40
      || (signal.orderFlowScore || 0) < 65
      || remainingReasons.length
      || current.open >= signal.triggerPrice
      || current.high < signal.triggerPrice) return null;
    const anchor = findHigherStructureAnchor(byInterval, result, signal);
    if (!anchor) return null;
    const triggerDistancePercent = Math.max(0, signal.triggerPrice / Math.max(current.open, 1e-8) - 1) * 100;
    const precisionQuality = clamp(1 - triggerDistancePercent / 7, 0, 1) * 100;
    const crossFrameScore = Math.round(clamp(
      (anchor.certaintyScore || 0) * 0.55
      + (signal.orderFlowScore || 0) * 0.3
      + precisionQuality * 0.15,
      0,
      99,
    ));
    return {
      ...signal,
      id: `${signal.id}-4h-to-1h-precision`,
      promotedFromId: signal.id,
      status: "buy",
      reasons: [],
      score: Math.max(signal.score || 0, crossFrameScore),
      localSentimentScore: signal.sentimentScore,
      certaintyScore: Math.max(signal.certaintyScore || 0, crossFrameScore + 4),
      rhythmScore: Math.max(signal.rhythmScore || 0, 72),
      sentimentScore: Math.max(signal.sentimentScore || 0, anchor.sentimentScore || 0),
      sentimentPhase: "4小时蓄势 / 1小时点火",
      crossFramePrecision: true,
      higherTimeframeAnchor: "4h",
      higherTimeframeAnchorId: anchor.id,
      evidence: [
        ...signal.evidence,
        `4小时 ${anchor.consolidationBars} 根长收敛只作母结构许可`,
        `1小时突破前订单流 ${signal.orderFlowScore} 分，执行价由1小时上沿独立确定`,
        "4小时锚点与1小时触发均只使用各自决策时刻已经可见的数据",
      ],
    };
  }

  const LOWER_TRIGGER_BY_PARENT_INTERVAL = Object.freeze({
    "5m": "1m",
    "15m": "5m",
    "1h": "15m",
    "4h": "1h",
    "1d": "4h",
  });

  const CROSS_FRAME_PARENT_POLICY = Object.freeze({
    "5m": { bars: 12, score: 62, rhythm: 64, sentiment: 58 },
    "15m": { bars: 14, score: 66, rhythm: 66, sentiment: 58 },
    "1h": { bars: 10, score: 70, rhythm: 68, sentiment: 60 },
    "4h": { bars: 24, score: 80, rhythm: 72, sentiment: 64 },
    "1d": { bars: 30, score: 86, rhythm: 76, sentiment: 68 },
  });

  function isQualifiedLowerFrameIgnition(signal, interval) {
    if (!signal || signal.interval !== interval) return false;
    if (interval === "1m") return isOneMinutePostImpulseHorizontalLaunch(signal);
    const hierarchy = signal.executionHierarchy || assessExecutionHierarchy(signal);
    const strictRhythm = (signal.rhythmScore || 0) >= 72
      && (signal.sentimentScore || 0) >= 60;
    const causalPlatformAPlus = hierarchy.primaryFoundation === "mother-platform-breakout"
      && signal.outerEdgeConfirmed === true
      && (signal.outerEdgeScore || 0) >= 84
      && (signal.score || 0) >= 84
      && (signal.certaintyScore || 0) >= 84;
    return isHighCertaintyEntry(signal)
      && (signal.score || 0) >= 80
      && (signal.certaintyScore || 0) >= 78
      && (strictRhythm || causalPlatformAPlus)
      && signal.aboveEma90 === true
      && (signal.ema90SlopeAtDecision || 0) >= 0
      && signal.motherStructureNoise !== true
      && signal.oneMinuteMotherBoxNoise !== true
      && signal.riskStructureShape == null
      && (signal.launchDistancePercent ?? 99) <= 7;
  }

  function rebuildCausalParentAtChild(parentResult, lowerFrame, parentSignal, childSignal, parentInterval, options = {}) {
    const childCandle = lowerFrame?.candles?.[childSignal?.index];
    const parentStart = Number(parentSignal?.time);
    const parentMs = INTERVAL_MS[parentInterval];
    if (!childCandle || !Number.isFinite(parentStart) || !parentMs) return null;
    const cutoff = Number(childCandle.closeTime ?? childCandle.time);
    const childRows = (lowerFrame.candles || []).filter((row) => (
      row.time >= parentStart
      && row.time < parentStart + parentMs
      && (row.closeTime ?? row.time) <= cutoff
    ));
    if (!childRows.length) return null;
    const partialParent = {
      time: parentStart,
      // 将父K的可见截止时间截在子K收盘，不借用本小时后续15分钟数据。
      closeTime: cutoff,
      open: childRows[0].open,
      high: Math.max(...childRows.map((row) => row.high)),
      low: Math.min(...childRows.map((row) => row.low)),
      close: childRows.at(-1).close,
      volume: childRows.reduce((sum, row) => sum + (row.volume || 0), 0),
      quoteVolume: childRows.reduce((sum, row) => sum + (row.quoteVolume || 0), 0),
      takerBuyVolume: childRows.reduce((sum, row) => sum + (row.takerBuyVolume || 0), 0),
      tradeCount: childRows.reduce((sum, row) => sum + (row.tradeCount || 0), 0),
    };
    const causalRows = [
      ...(parentResult.candles || []).filter((row) => row.time < parentStart),
      partialParent,
    ];
    const causalResult = analyzeTimeframe(causalRows, {
      interval: parentInterval,
      now: cutoff,
      preselectedLeader: true,
      mainWaveStage: options.mainWaveStage,
      mainWaveContextSource: "adjacent-frame-causal-rebuild",
      mainWaveContextLabel: "相邻周期因果重建",
    });
    const candidates = [...(causalResult.signals || []), ...(causalResult.pending || [])]
      .filter((item) => item.time === parentStart && (
        item.crossedLevel === true
        || (options.allowPreconfirmedParent === true
          && item.status === "pending"
          && (item.foundationTypes || []).some((type) => ["base", "triangle"].includes(type))
          && (item.consolidationBars || 0) >= 32
          && (item.structureQuality || 0) >= 0.65)
      ))
      .filter((item) => assessExecutionHierarchy(item).permit)
      .sort((left, right) => (
        Math.abs(left.triggerPrice - childSignal.triggerPrice)
          - Math.abs(right.triggerPrice - childSignal.triggerPrice)
      ));
    const causalParent = candidates[0] || null;
    if (!causalParent) return null;
    const triggerGapPercent = Math.abs(
      causalParent.triggerPrice / Math.max(childSignal.triggerPrice, 1e-8) - 1,
    ) * 100;
    // 子周期的精确拐点可以略早于父平台最终外沿。BIO 的5分钟拐点与
    // 同小时1小时母结构相差约1.55%，仍属于同一收敛楔口；上限只放宽到
    // 1.8%，且父结构已经经过截断重算和因果层级审计，不能用未来父K兜底。
    if (triggerGapPercent > 1.8) return null;
    return {
      signal: causalParent,
      partialParent,
      cutoff,
    };
  }

  // 相邻小周期负责在父周期母K内部给出精确扣扳机时间，但绝不把任意小周期B
  // 机械投影到大周期。父周期必须先独立形成平台/三角/回踩结构、真正外沿的首次
  // 上穿、上行EMA90与主升前置推动；子周期还必须是A+，并在同一根父K内从线下
  // 穿过几乎相同的压力位。decisionTime采用子K触发时刻，只使用当时已经可见的
  // 子K和父K开盘，不等待父K收盘后再倒推买点。
  function promoteLowerFrameIgnitionToParent(byInterval, result, signal, preselectedLeader, mainWaveStage) {
    const parentInterval = result?.interval;
    const childInterval = LOWER_TRIGGER_BY_PARENT_INTERVAL[parentInterval];
    const policy = CROSS_FRAME_PARENT_POLICY[parentInterval];
    if (!preselectedLeader || !childInterval || !policy) return null;
    const current = result.candles?.[signal.index];
    const lowerFrame = byInterval.get(childInterval);
    if (!current || !lowerFrame?.candles?.length) return null;
    const foundations = signal.foundationTypes || [];
    const auxiliaries = signal.auxiliaryTypes || [];
    const hasBaseFamily = foundations.some((type) => ["base", "relaunch"].includes(type));
    const hasTriangle = foundations.includes("triangle");
    const directBoundary = auxiliaries.includes("previousHigh")
      || (hasTriangle
        && auxiliaries.includes("trendline")
        && (signal.matureTriangleOuterEdge === true
          || signal.directStructuralBoundary === true
          || signal.outerEdgeConfirmed === true));
    const parentHierarchy = signal.executionHierarchy || assessExecutionHierarchy(signal);
    const hasPriorAdvance = signal.horizontalLaunchHasPriorAdvance === true
      || signal.triangleHasPriorAdvance === true
      || signal.shockBoxHorizontalLaunchException === true
      || signal.shockBoxAscendingTriangleException === true
      || signal.mainWaveOldDeclinePressureException === true
      || parentHierarchy.primaryFoundation === "compact-one-hour-parent-platform"
      || ["active", "expected"].includes(mainWaveStage);
    const compactParentQualityPermit = parentInterval === "1h"
      && parentHierarchy.primaryFoundation === "compact-one-hour-parent-platform"
      && (signal.certaintyScore || 0) >= 78
      && (signal.rhythmScore || 0) >= 66
      && (signal.sentimentScore || 0) >= 60
      && (signal.certaintyScore || 0) + (signal.rhythmScore || 0) + (signal.sentimentScore || 0) >= 206;
    const eliteLargeParent = !["4h", "1d"].includes(parentInterval)
      || isHighCertaintyEntry(signal);
    const parentStructure = foundations.length >= 1
      && foundations.every((type) => ["base", "triangle", "relaunch"].includes(type))
      && (hasBaseFamily || hasTriangle)
      && directBoundary
      && auxiliaries.every((type) => ["previousHigh", "trendline"].includes(type))
      && (signal.consolidationBars || 0) >= policy.bars
      && hasPriorAdvance
      && signal.horizontalLaunchPostSelloffRecovery !== true
      && signal.motherStructureNoise !== true
      && signal.oneMinuteMotherBoxNoise !== true
      && signal.aboveEma90 === true
      && (signal.ema90SlopeAtDecision || 0) >= 0
      && signal.riskStructureShape == null
      && (Number.isFinite(Number(signal.launchDistancePercent))
        ? Number(signal.launchDistancePercent)
        : Math.max(0, signal.triggerPrice / Math.max(current.open, 1e-8) - 1) * 100) <= 7
      && ((signal.structuralRiskPercent ?? 99) <= (parentInterval === "1h" ? 6 : 4)
        || (parentInterval === "1h"
          && ["compact-one-hour-parent-platform", "mother-platform-breakout"].includes(parentHierarchy.primaryFoundation)
          && (parentHierarchy.primaryFoundation === "compact-one-hour-parent-platform"
            || (signal.clusteredCeilingBand === true
              && (signal.platformTouchGroups || 0) >= 2
              && (signal.ceilingTouches || 0) >= 7))
          && (signal.structuralRiskPercent ?? 99) <= 12))
      && (signal.score || 0) >= policy.score
      && (compactParentQualityPermit || (
        (signal.rhythmScore || 0) >= policy.rhythm
        && (signal.sentimentScore || 0) >= policy.sentiment
      ))
      && eliteLargeParent
      && current.open < signal.triggerPrice;
    if (!parentStructure) return null;

    const parentEnd = signal.time + INTERVAL_MS[parentInterval];
    const child = (lowerFrame.signals || [])
      .filter((item) => {
        const decisionTime = item.decisionTime ?? item.time;
        const childCandle = lowerFrame.candles?.[item.index];
        const triggerGapPercent = Math.abs(item.triggerPrice / Math.max(signal.triggerPrice, 1e-8) - 1) * 100;
        return decisionTime >= signal.time
          && decisionTime < parentEnd
          && isQualifiedLowerFrameIgnition(item, childInterval)
          && childCandle?.open < signal.triggerPrice
          && childCandle?.high >= signal.triggerPrice
          && triggerGapPercent <= 1.5;
      })
      .sort((a, b) => (a.decisionTime ?? a.time) - (b.decisionTime ?? b.time))[0];
    if (!child) return null;

    const causalParentRebuild = rebuildCausalParentAtChild(
      result,
      lowerFrame,
      signal,
      child,
      parentInterval,
      { mainWaveStage },
    );
    if (!causalParentRebuild) return null;
    const sourceParentSignal = causalParentRebuild.signal;
    const decisionTime = child.decisionTime ?? child.time;
    const childStructureStartTime = Number(child.horizontalStructureStartTime);
    const highResolutionParentBars = Number.isFinite(childStructureStartTime)
      ? Math.max(0, Math.ceil((child.time - childStructureStartTime) / INTERVAL_MS[parentInterval]))
      : 0;
    const effectiveParentBars = Math.max(
      sourceParentSignal.consolidationBars || 0,
      highResolutionParentBars,
    );
    const compactParentConsolidation = parentInterval === "1h"
      && (sourceParentSignal.foundationTypes || []).includes("relaunch")
      && (sourceParentSignal.auxiliaryTypes || []).includes("previousHigh")
      && effectiveParentBars >= 12;
    const crossFrameScore = Math.round(clamp(
      (child.certaintyScore || 0) * 0.52
      + (sourceParentSignal.certaintyScore || 0) * 0.25
      + (sourceParentSignal.rhythmScore || 0) * 0.15
      + (sourceParentSignal.sentimentScore || 0) * 0.08,
      0,
      99,
    ));
    return {
      ...sourceParentSignal,
      id: `${sourceParentSignal.id}-${childInterval}-to-${parentInterval}-precision`,
      decisionTime,
      causalObservationTime: causalParentRebuild.cutoff,
      nativeParentConsolidationBars: sourceParentSignal.consolidationBars || 0,
      highResolutionParentBars,
      consolidationBars: effectiveParentBars,
      status: "buy",
      reasons: [],
      pattern: compactParentConsolidation
        ? `盘整突破 + ${String(sourceParentSignal.pattern || "回踩再点火").replace(/^盘整突破\s*\+\s*/, "")}`
        : sourceParentSignal.pattern,
      primaryPatternKey: compactParentConsolidation ? "consolidationBreakout" : sourceParentSignal.primaryPatternKey,
      consolidationBreakout: compactParentConsolidation || sourceParentSignal.consolidationBreakout === true,
      score: Math.max(sourceParentSignal.score || 0, 86, crossFrameScore),
      certaintyScore: Math.max(sourceParentSignal.certaintyScore || 0, 92, crossFrameScore + 4),
      rhythmScore: Math.max(sourceParentSignal.rhythmScore || 0, 74),
      sentimentScore: Math.max(sourceParentSignal.sentimentScore || 0, 64),
      crossFramePrecision: true,
      multiTimeframeConfluence: true,
      multiTimeframeConfluenceFrames: [childInterval, parentInterval],
      crossFrameDirection: `${childInterval}-to-${parentInterval}`,
      lowerTimeframeTrigger: childInterval,
      lowerTimeframeTriggerId: child.id,
      evidence: [
        ...(sourceParentSignal.evidence || []),
        `同一根${parentInterval}K内，${childInterval} A+起爆于 ${new Date(decisionTime).toISOString()} 先行触发`,
        `${childInterval}精确起爆与${parentInterval}成熟结构共振，确定性按多周期A+路径提升`,
        ...(compactParentConsolidation ? [
          `${parentInterval}原生识别 ${sourceParentSignal.consolidationBars} 根，${childInterval}高分辨率结构映射为 ${highResolutionParentBars} 根父K；真前高与母平台边界一致，不再被固定根数门槛误杀`,
        ] : []),
        `${parentInterval}自身已有前置上推、成熟盘整结构与真实外沿首次上穿；${childInterval}只负责精确触发，不单独替代父周期结构`,
        `跨周期共振把${parentInterval}当前K截断到${childInterval}触发K收盘重新计算，不读取本小时后续数据`,
      ],
    };
  }

  const PARENT_INTERVAL_BY_CHILD = Object.freeze({
    "1m": "5m",
    "5m": "15m",
    "15m": "1h",
    "1h": "4h",
  });

  function promoteAdjacentMotherChildConfluence(byInterval, result, signal, preselectedLeader) {
    if (!preselectedLeader || !signal || signal.crossedLevel !== true) return null;
    if (signal.openedBeyondTrigger === true
      || signal.oneMinuteMotherBoxNoise === true
      || signal.horizontalLaunchUrgent === true
      || signal.horizontalLaunchInsufficientEdgeDwell === true
      || signal.horizontalLaunchPostSelloffRecovery === true
      || signal.trianglePostSelloffRecovery === true
      || signal.riskStructureShape != null) return null;

    // 向上共振只允许 15 分钟自身已经形成“短盘整 + 拐点 + 真前高”时使用。
    // 小周期成熟平台负责证明母结构，15分钟仍需独立具备自己的短盘整节奏；
    // 因此不会把任意小周期买点机械投影成1小时或4小时买点。
    const lowerInterval = LOWER_TRIGGER_BY_PARENT_INTERVAL[result?.interval];
    const lowerFrame = lowerInterval ? byInterval.get(lowerInterval) : null;
    const parentFoundations = signal.foundationTypes || [];
    const parentAuxiliaries = signal.auxiliaryTypes || [];
    const shortParentQualityStack = (signal.certaintyScore || 0)
      + (signal.rhythmScore || 0)
      + (signal.sentimentScore || 0);
    const shortParentOwnStructure = result?.interval === "15m"
      && parentFoundations.includes("relaunch")
      && parentAuxiliaries.includes("previousHigh")
      && signal.hasPivot === true
      && (signal.consolidationBars || 0) >= 8
      && (signal.structureQuality || 0) >= 0.72
      && (signal.score || 0) >= 86
      && (signal.certaintyScore || 0) >= 64
      && (signal.rhythmScore || 0) >= 70
      && (signal.sentimentScore || 0) >= 70
      && signal.motherStructureNoise !== true
      && shortParentQualityStack >= 225
      && signal.aboveEma90 === true
      && (signal.ema90SlopeAtDecision || 0) > 0;
    if (shortParentOwnStructure && lowerFrame) {
      const parentEnd = signal.time + INTERVAL_MS[result.interval];
      const child = (lowerFrame.signals || [])
        .filter((item) => {
          const hierarchy = item.executionHierarchy || assessExecutionHierarchy(item);
          const triggerGapPercent = Math.abs(
            item.triggerPrice / Math.max(signal.triggerPrice, 1e-8) - 1,
          ) * 100;
          return item.time >= signal.time
            && item.time < parentEnd
            && hierarchy.permit === true
            && hierarchy.primaryFoundation === "mother-platform-breakout"
            && item.outerEdgeConfirmed === true
            && (item.consolidationBars || 0) >= 28
            && (item.score || 0) >= 90
            && (item.certaintyScore || 0) >= 84
            && item.aboveEma90 === true
            && (item.ema90SlopeAtDecision || 0) > 0
            && triggerGapPercent <= 1.5;
        })
        .sort((left, right) => left.time - right.time)[0];
      if (child) {
        return {
          ...signal,
          id: `${signal.id}-${lowerInterval}-mother-confluence`,
          promotedFromId: signal.id,
          status: "buy",
          reasons: [],
          pattern: `盘整突破 + ${String(signal.pattern || "短盘整突破").replace(/^盘整突破\s*\+\s*/, "")}`,
          primaryPatternKey: "consolidationBreakout",
          consolidationBreakout: true,
          score: Math.max(signal.score || 0, 90),
          certaintyScore: Math.max(signal.certaintyScore || 0, 90),
          crossFramePrecision: true,
          multiTimeframeConfluence: true,
          adjacentMotherChildConfluence: true,
          multiTimeframeConfluenceFrames: [lowerInterval, result.interval],
          crossFrameDirection: `${lowerInterval}-to-${result.interval}`,
          lowerTimeframeTrigger: lowerInterval,
          lowerTimeframeTriggerId: child.id,
          executionHierarchy: {
            permit: true,
            tier: "exception",
            primaryFoundation: "adjacent-lower-frame-mother-platform",
            primaryFoundationLabel: `${lowerInterval}成熟母平台 + ${result.interval}短盘整真前高`,
            childStructures: ["short-parent-consolidation"],
            boosters: ["previous-high", "pivot", "multi-timeframe"],
            missing: [],
            missingLabels: [],
            maturity: "cross-frame-qualified",
            scoreAuthority: "rank-only",
          },
          evidence: [
            ...(signal.evidence || []),
            `${lowerInterval}已独立形成${child.consolidationBars || 0}根成熟母平台，${result.interval}自身另有短盘整、拐点和真实前高`,
            `两个周期在同一根${result.interval}K内从线下突破近似压力位；小周期补足结构分辨率，但不替代大周期自身判断`,
          ],
        };
      }
    }

    // 向下共振用于“父周期母平台已经确认，子周期随后放量再突破”的精确执行。
    // 子周期仍须有回踩/拐点、强订单流和K线速度；普通几根K的小反弹不会因
    // 附近存在父周期结构而获得买点。
    const parentInterval = PARENT_INTERVAL_BY_CHILD[result?.interval];
    const parentFrame = parentInterval ? byInterval.get(parentInterval) : null;
    if (result?.interval !== "5m" || !parentFrame) return null;
    const childBreakDistance = Number(signal.breakoutLow) > 0
      ? Math.max(0, signal.triggerPrice / signal.breakoutLow - 1) * 100
      : 99;
    // BIO 2025-08-24 16:00 一类拐点发生在本小时第一根5分钟K：父周期
    // 结构已经成立，但1小时K尚未收盘。不能等整小时结束才确认，也不能直接
    // 使用完整父K（会偷看未来）。这里先找到包含当前子K的父候选，再把父K
    // 截断到当前5分钟收盘，因果重算父结构；两边都成立才提升精确拐点。
    const sameParentCausalPivotReady = parentFoundations.includes("relaunch")
      && signal.hasPivot === true
      && signal.crossedLevel === true
      && signal.openedBeyondTrigger !== true
      && (signal.consolidationBars || 0) >= 18
      && (signal.structureQuality || 0) >= 0.48
      && (signal.certaintyScore || 0) >= 72
      && (signal.rhythmScore || 0) >= 78
      && (signal.sentimentScore || 0) >= 70
      && childBreakDistance <= 3
      && signal.aboveEma90 === true
      && (signal.ema90SlopeAtDecision || 0) > 0;
    if (sameParentCausalPivotReady) {
      // 先看相邻15分钟；若15分钟合并后仍太短，再看同一小时已经预确认的
      // 1小时母结构。两者都按当前5分钟收盘截断，绝不直接读取完整父K。
      const causalAnchor = [...new Set([parentInterval, "1h"])]
        .map((anchorInterval) => {
          const anchorFrame = byInterval.get(anchorInterval);
          const anchorMs = INTERVAL_MS[anchorInterval];
          if (!anchorFrame || !anchorMs) return null;
          const parentSeed = [
            ...(anchorFrame.signals || []),
            ...(anchorFrame.pending || []),
            ...(anchorFrame.rejected || []),
          ].filter((item) => (
            item.time <= signal.time
            && signal.time < item.time + anchorMs
            && item.crossedLevel === true
          )).sort((left, right) => (
            Math.abs(left.triggerPrice - signal.triggerPrice)
              - Math.abs(right.triggerPrice - signal.triggerPrice)
          ))[0];
          const causalParent = parentSeed
            ? rebuildCausalParentAtChild(
              anchorFrame,
              result,
              parentSeed,
              signal,
              anchorInterval,
              {
                mainWaveStage: signal.mainWaveStage,
                allowPreconfirmedParent: true,
              },
            )
            : null;
          return causalParent ? { anchorInterval, causalParent } : null;
        })
        .filter(Boolean)
        .sort((left, right) => (
          Math.abs(left.causalParent.signal.triggerPrice - signal.triggerPrice)
            - Math.abs(right.causalParent.signal.triggerPrice - signal.triggerPrice)
        ))[0];
      if (causalAnchor) {
        const { anchorInterval, causalParent } = causalAnchor;
        const parentHierarchy = causalParent.signal.executionHierarchy
          || assessExecutionHierarchy(causalParent.signal);
        if (parentHierarchy.permit === true
          && (causalParent.signal.foundationTypes || []).some((type) => (
            ["base", "triangle", "relaunch"].includes(type)
          ))) {
          const auxiliaryTypes = [...new Set([...(signal.auxiliaryTypes || []), "previousHigh"])];
          const confluence = [...new Set([...(signal.confluence || []), "previousHigh"])];
          return {
            ...signal,
            id: `${signal.id}-${anchorInterval}-same-candle-causal-pivot`,
            promotedFromId: signal.id,
            status: "buy",
            reasons: [],
            pattern: "多周期盘整突破 + 拐点收复 + 突破前高",
            primaryPatternKey: "pivot",
            consolidationBreakout: true,
            auxiliaryTypes,
            confluence,
            previousHighLevel: signal.level,
            score: Math.max(signal.score || 0, 88),
            certaintyScore: Math.max(signal.certaintyScore || 0, 90),
            crossFramePrecision: true,
            multiTimeframeConfluence: true,
            adjacentMotherChildConfluence: true,
            multiTimeframeConfluenceFrames: [result.interval, anchorInterval],
            crossFrameDirection: `${anchorInterval}-to-${result.interval}`,
            higherTimeframeAnchor: anchorInterval,
            higherTimeframeAnchorId: causalParent.signal.id,
            executionHierarchy: {
              permit: true,
              tier: "exception",
              primaryFoundation: "causal-open-parent-structure",
              primaryFoundationLabel: `${anchorInterval}未收盘K的因果结构 + ${result.interval}精确拐点`,
              childStructures: ["precision-pivot"],
              boosters: ["previous-high", "pivot", "multi-timeframe"],
              missing: [],
              missingLabels: [],
              maturity: "cross-frame-qualified",
              scoreAuthority: "rank-only",
            },
            evidence: [
              ...(signal.evidence || []),
              `${anchorInterval}当前K截断到本根${result.interval}收盘后，仍独立形成可执行母结构`,
              `${result.interval}完成 ${signal.consolidationBars || 0} 根止跌盘整、拐点与真实边界首次上穿，负责精确执行`,
              `父K只使用截至 ${new Date(causalParent.cutoff).toISOString()} 的已收盘子K，未读取本小时后续数据`,
            ],
          };
        }
      }
    }
    const preciseChildRebreak = parentFoundations.includes("relaunch")
      && signal.hasPivot === true
      && (signal.consolidationBars || 0) >= 8
      && (signal.structureQuality || 0) >= 0.35
      && (signal.relativeVolume || 0) >= 1.45
      && (signal.orderFlowScore || 0) >= 75
      && (signal.klineVelocity || 0) >= 1
      && childBreakDistance <= 5
      && signal.motherStructureNoise !== true
      && signal.aboveEma90 === true
      && (signal.ema90SlopeAtDecision || 0) > 0;
    if (!preciseChildRebreak) return null;
    const parentMs = INTERVAL_MS[parentInterval];
    const parent = (parentFrame.signals || [])
      .filter((item) => {
        const hierarchy = item.executionHierarchy || assessExecutionHierarchy(item);
        const parentClose = item.time + parentMs;
        const age = signal.time - parentClose;
        const triggerGapPercent = Math.abs(
          signal.triggerPrice / Math.max(item.triggerPrice, 1e-8) - 1,
        ) * 100;
        return parentClose <= signal.time
          && age <= parentMs
          && hierarchy.permit === true
          && item.consolidationBreakout === true
          && item.outerEdgeConfirmed === true
          && (item.consolidationBars || 0) >= 28
          && (item.score || 0) >= 86
          && (item.certaintyScore || 0) >= 82
          && triggerGapPercent <= 4;
      })
      .sort((left, right) => right.time - left.time)[0];
    if (!parent) return null;
    const auxiliaryTypes = [...new Set([...(signal.auxiliaryTypes || []), "previousHigh"])];
    const confluence = [...new Set([...(signal.confluence || []), "previousHigh"])];
    return {
      ...signal,
      id: `${signal.id}-${parentInterval}-mother-rebreak`,
      promotedFromId: signal.id,
      status: "buy",
      reasons: [],
      pattern: "多周期盘整突破 + 放量再突破前高",
      primaryPatternKey: "consolidationBreakout",
      consolidationBreakout: true,
      auxiliaryTypes,
      confluence,
      previousHighLevel: signal.level || parent.triggerPrice,
      score: Math.max(signal.score || 0, 88),
      certaintyScore: Math.max(signal.certaintyScore || 0, 88),
      rhythmScore: Math.max(signal.rhythmScore || 0, 72),
      sentimentScore: Math.max(signal.sentimentScore || 0, 60),
      crossFramePrecision: true,
      multiTimeframeConfluence: true,
      adjacentMotherChildConfluence: true,
      multiTimeframeConfluenceFrames: [result.interval, parentInterval],
      crossFrameDirection: `${parentInterval}-to-${result.interval}`,
      higherTimeframeAnchor: parentInterval,
      higherTimeframeAnchorId: parent.id,
      executionHierarchy: {
        permit: true,
        tier: "exception",
        primaryFoundation: "adjacent-parent-mother-platform",
        primaryFoundationLabel: `${parentInterval}成熟母平台 + ${result.interval}放量再突破`,
        childStructures: ["precision-rebreak"],
        boosters: ["previous-high", "pivot", "volume-flow", "multi-timeframe"],
        missing: [],
        missingLabels: [],
        maturity: "cross-frame-qualified",
        scoreAuthority: "rank-only",
      },
      evidence: [
        ...(signal.evidence || []),
        `${parentInterval}母平台已在上一根父K完成真实外沿突破；${result.interval}随后以量能 ${(signal.relativeVolume || 0).toFixed(2)}×、订单流 ${signal.orderFlowScore || 0} 分再突破`,
        "父周期提供盘整区间，子周期只负责防洗出后的精确再入；两项证据均在当前K触发前已经可见",
      ],
    };
  }

  function assessEarlyNewCoinHistory(byInterval, decisionTime) {
    const limits = { "1h": 90, "4h": 24, "1d": 12 };
    const stats = Object.keys(limits).map((interval) => {
      const result = byInterval.get(interval);
      const candles = (result?.candles || []).filter((row) => (
        (row.closeTime ?? row.time) <= decisionTime
      ));
      if (!candles.length) return null;
      return {
        interval,
        count: candles.length,
        limit: limits[interval],
        firstTime: candles[0].time,
        lastCloseTime: candles.at(-1).closeTime ?? candles.at(-1).time,
      };
    }).filter(Boolean);
    const fresh = stats.filter((item) => (
      decisionTime - item.lastCloseTime <= INTERVAL_MS[item.interval] * 2
    ));
    if (!fresh.length) return { eligible: false, stats: fresh };
    const firstTimes = fresh.map((item) => item.firstTime);
    const listingStartTime = Math.min(...firstTimes);
    const hasShortListingHistory = fresh.some((item) => item.count < item.limit);
    const listingAgeHours = (decisionTime - listingStartTime) / (60 * 60_000);
    return {
      // 4小时和日线在新币早期可能只有几根甚至完全没有；不再要求它们
      // 同时存在。任一现有高周期能证明上市历史短即可，最终仍由止跌、
      // 主升情绪、成熟结构和真实突破等严格条件决定是否出现 B 点。
      eligible: hasShortListingHistory
        && listingAgeHours >= 12
        && listingAgeHours <= 14 * 24,
      stats: fresh,
      listingStartTime,
      listingAgeHours,
    };
  }

  function applyContextGates(results, marketResults = [], options = {}) {
    const list = (Array.isArray(results) ? results : []).filter(Boolean);
    const preselectedLeader = options.preselectedLeader === true;
    const declaredMainWaveStage = ["active", "expected"].includes(options.mainWaveStage)
      ? options.mainWaveStage
      : null;
    const declaredMainWaveContextSource = String(options.mainWaveContextSource || "manual-analysis");
    const declaredMainWaveContextLabel = String(options.mainWaveContextLabel || (
      declaredMainWaveStage === "active" ? "人工确认主升浪阶段" : "人工给出主升浪预期"
    ));
    const byInterval = new Map(list.map((result) => [result.interval, result]));
    const marketByInterval = new Map((Array.isArray(marketResults) ? marketResults : [])
      .filter(Boolean)
      .map((result) => [result.interval, result]));
    const requirements = {
      "1m": { frames: ["1h", "4h", "1d"], available: 2, bullish: 1 },
      "5m": { frames: ["1h", "4h", "1d"], available: 2, bullish: 1 },
      "15m": { frames: ["1h", "4h", "1d"], available: 2, bullish: 1 },
      "1h": { frames: ["4h", "1d"], available: 2, bullish: 1 },
      "4h": { frames: ["1d"], available: 1, bullish: 1 },
    };
    const executionAnchors = {
      "1m": { frames: ["5m", "15m"], windowMs: 30 * 60_000 },
      "5m": { frames: ["15m", "1h"], windowMs: 2 * 60 * 60_000 },
    };

    const gated = list.map((result) => {
      const adjacentFramePromotions = (result.signals || []).map((signal) => (
        promoteLowerFrameIgnitionToParent(
          byInterval,
          result,
          signal,
          preselectedLeader,
          declaredMainWaveStage || signal.mainWaveStage,
        ) || signal
      ));
      const rule = requirements[result.interval];
      if (!rule) {
        return {
          ...result,
          signals: adjacentFramePromotions,
          stats: {
            ...(result.stats || {}),
            signalCount: adjacentFramePromotions.length,
          },
        };
      }
      const keptSignals = [];
      const keptPending = [];
      const downgraded = [];
      const contextualPromotions = (result.rejected || [])
        .map((signal) => (
          promoteAdjacentMotherChildConfluence(byInterval, result, signal, preselectedLeader)
          || promoteCrossFramePrecision(byInterval, result, signal)
          || promoteLeaderIndependentCurrentStructure(result, signal, preselectedLeader)
          || promoteLeaderEliteStructureRetry(result, signal, preselectedLeader)
          || promotePreHigherFrameLeaderMainWaveIgnition(result, signal, preselectedLeader)
        ))
        .filter(Boolean);
      const promotedFromIds = new Set(contextualPromotions.map((signal) => signal.promotedFromId));
      const sourceSignals = [
        ...adjacentFramePromotions,
        ...contextualPromotions,
      ];
      const sourceRejected = (result.rejected || []).filter((signal) => !promotedFromIds.has(signal.id));
      const gateItem = (signal, target) => {
        const decisionTime = signal.decisionTime ?? signal.time;
        const breakoutLow = causalBreakoutLow(byInterval, result, signal);
        const previousHighLevel = signal.previousHighLevel || signal.level;
        const riseFromBreakoutLowPercent = breakoutLow
          ? Math.max(0, previousHighLevel / Math.max(breakoutLow.low, 1e-8) - 1) * 100
          : 0;
        const contextualSignal = {
          ...signal,
          breakoutLowBeforeTrigger: breakoutLow?.low || null,
          breakoutLowSource: breakoutLow?.sourceInterval || null,
          riseFromBreakoutLowPercent,
          evidence: [
            ...signal.evidence,
            breakoutLow
              ? `首次触发前低点 ${breakoutLow.low.toFixed(8)}（${breakoutLow.sourceInterval === "open-fallback" ? "无子周期时使用突破K开盘" : `${breakoutLow.sourceInterval} 子K因果重建`}），到前高涨幅 ${riseFromBreakoutLowPercent.toFixed(2)}%`
              : "首次触发前低点证据不足",
          ],
        };
        const snapshots = rule.frames
          .map((interval) => regimeAt(byInterval.get(interval), decisionTime))
          .filter(Boolean);
        const bullish = snapshots.filter((snapshot) => snapshot.bullish).length;
        const marketSnapshots = ["1h", "4h"]
          .map((interval) => regimeAt(marketByInterval.get(interval), decisionTime))
          .filter(Boolean);
        const marketBullish = marketSnapshots.filter((snapshot) => snapshot.bullish).length;
        const earlyNewCoinHistory = assessEarlyNewCoinHistory(byInterval, decisionTime);
        const oneMinuteLeaderLaunch = preselectedLeader
          && isOneMinutePostImpulseHorizontalLaunch(signal);
        const oneHourLeaderPlatform = preselectedLeader
          && isOneHourPlatformPivotIgnition(signal);
        const partialHigherFramePermit = snapshots.length < rule.available
          && ((oneHourLeaderPlatform && bullish >= 1)
            || (oneMinuteLeaderLaunch && (
              bullish >= 1
              || (snapshots.length === 0
                && signal.horizontalLaunchHasPriorAdvance === true
                && (signal.outerEdgeScore || 0) >= 78)
            )));
        const childFramePrecision = signal.crossFramePrecision === true
          && signal.lowerTimeframeTrigger === LOWER_TRIGGER_BY_PARENT_INTERVAL[result.interval]
          && signal.crossFrameDirection === `${signal.lowerTimeframeTrigger}-to-${result.interval}`;
        const independentLeaderStrength = childFramePrecision
          || signal.adjacentMotherChildConfluence === true
          || signal.leaderIndependentStructureReset === true
          || signal.leaderEliteStructureRetry === true
          || oneMinuteLeaderLaunch
          || signal.matureHigherTimeframePostShockRecovery === true
          || (Boolean(signal.crossFramePrecision)
          && signal.higherTimeframeAnchor === "4h"
          && (signal.orderFlowScore || 0) >= 72
          && (signal.score || 0) >= 78)
          || isOneHourPostImpulseWedgeIgnition(signal);
        const effectiveMainWaveStage = declaredMainWaveStage
          || (["active", "expected"].includes(signal.mainWaveStage) ? signal.mainWaveStage : null);
        const hasIndependentMainWaveAdvance = signal.horizontalLaunchHasPriorAdvance === true
          || signal.triangleHasPriorAdvance === true
          || signal.shockBoxHorizontalLaunchException === true
          || signal.shockBoxAscendingTriangleException === true
          || signal.mainWaveOldDeclinePressureException === true;
        const currentMainWaveHeat = Boolean(declaredMainWaveStage)
          || ((signal.sentimentScore || 0) >= (effectiveMainWaveStage === "active" ? 58 : 64)
            && ((signal.orderFlowScore || 0) >= 35 || (signal.relativeVolume || 0) >= 1.1));
        const mainWaveHigherFramePermit = Boolean(effectiveMainWaveStage)
          && (signal.foundationTypes || []).length >= 1
          && isHighCertaintyEntry(signal)
          && hasIndependentMainWaveAdvance
          && currentMainWaveHeat
          && (signal.score || 0) >= (effectiveMainWaveStage === "active" ? 72 : 84)
          && (signal.rhythmScore || 0) >= (effectiveMainWaveStage === "active" ? 66 : 74)
          && signal.aboveEma90 === true
          && (signal.ema90SlopeAtDecision || 0) >= 0
          && signal.riskStructureShape == null
          && signal.motherStructureNoise !== true
          && signal.oneMinuteMotherBoxNoise !== true
          && (signal.launchDistancePercent == null || signal.launchDistancePercent <= 7);
        const declaredMainWavePermit = Boolean(declaredMainWaveStage)
          && mainWaveHigherFramePermit;
        const executionHierarchy = signal.executionHierarchy || assessExecutionHierarchy(signal);
        const recognizedHigherTimeframeBreak = preselectedLeader
          && ["active", "expected"].includes(declaredMainWaveStage || signal.mainWaveStage)
          && isRecognizedHigherTimeframeStructureBreak({ ...signal, executionHierarchy });
        const leaderReviewedStructuralException = signal.matureOneHourLongTriangleReset === true
          || signal.longBasePreviousHighIgnition === true
          || signal.softTestExtendedTriangleBreakout === true
          || signal.matureOneHourOuterPlatformReset === true
          || signal.oneHourRelaunchPivotIgnition === true
          || signal.oneHourCompactAscendingTriangleIgnition === true
          || signal.matureFifteenMinuteRetryPlatformIgnition === true
          || signal.shockMotherBoxOuterEdge === true;
        // 文档样本与实时龙头池已经先完成“龙头”选择。默认主升浪环境只能
        // 替代高周期环境许可，不能替代当前周期的成熟结构与真实突破。
        const leaderDefaultMainWaveEnvironmentPermit = preselectedLeader
          && declaredMainWaveStage === "active"
          && executionHierarchy.permit === true
          && (["mother-platform-breakout", "mature-triangle-outer-edge"].includes(
            executionHierarchy.primaryFoundation,
          ) || leaderReviewedStructuralException)
          && signal.crossedLevel === true
          && (signal.outerEdgeConfirmed === true
            || signal.matureTriangleOuterEdge === true
            || leaderReviewedStructuralException)
          && ((signal.consolidationBars || 0) >= 24 || leaderReviewedStructuralException)
          // 当高周期数据源暂时缺失时，默认主升浪许可必须依赖一个在突破前
          // 已经存在了一段时间的真实边界；刚在当前 K 线附近临时生成的局部
          // 高点不能借环境放宽复活。BANK 的成熟平台边界已存在 27 根，SPK
          // 被人工否定的局部假突破 ceilingAge=0，会在这里继续被拦截。
          && ((signal.ceilingAge || 0) >= 3 || leaderReviewedStructuralException)
          && (signal.certaintyScore || 0) >= 84
          && isHighCertaintyEntry(signal)
          && signal.horizontalLaunchUrgent !== true
          && signal.horizontalLaunchInsufficientEdgeDwell !== true
          && (signal.horizontalLaunchPostSelloffRecovery !== true || signal.shockMotherBoxOuterEdge === true)
          && signal.trianglePostSelloffRecovery !== true
          && signal.motherStructureNoise !== true
          && signal.oneMinuteMotherBoxNoise !== true
          && signal.riskStructureShape == null
          && (signal.launchDistancePercent == null || signal.launchDistancePercent <= 7);
        const inferredMainWavePermit = !declaredMainWaveStage
          && mainWaveHigherFramePermit;
        const preHigherFrameMainWaveIgnitionPermit = preselectedLeader
          && isPreHigherFrameLeaderMainWaveIgnition(signal);
        const newCoinMarketHeat = (signal.sentimentScore || 0) >= 72
          || ((signal.sentimentScore || 0) >= 60
            && ((signal.orderFlowScore || 0) >= 45 || (signal.relativeVolume || 0) >= 1.15));
        const newCoinNotFallingMainWavePermit = preselectedLeader
          && earlyNewCoinHistory.eligible === true
          && ["active", "expected"].includes(signal.mainWaveStage)
          && (signal.foundationTypes || []).some((type) => ["base", "triangle", "relaunch"].includes(type))
          && (signal.outerEdgeConfirmed === true || signal.matureTriangleOuterEdge === true)
          && isHighCertaintyEntry(signal)
          && (signal.score || 0) >= 84
          && (signal.rhythmScore || 0) >= 72
          && newCoinMarketHeat
          && signal.aboveEma90 === true
          && (signal.ema90SlopeAtDecision || 0) >= 0
          && signal.motherStructureNoise !== true
          && signal.oneMinuteMotherBoxNoise !== true
          && signal.riskStructureShape == null
          && (signal.launchDistancePercent == null || signal.launchDistancePercent <= 7);
        const contextualMainWavePermit = declaredMainWavePermit
          || leaderDefaultMainWaveEnvironmentPermit
          || recognizedHigherTimeframeBreak
          || newCoinNotFallingMainWavePermit
          || preHigherFrameMainWaveIgnitionPermit
          || inferredMainWavePermit;
        let reason = "";
        if (result.interval === "1m" && !isOneMinuteHorizontalBase(signal)) {
          reason = "1分钟仅保留高确定性横盘起飞或箱体突破，其他结构全部过滤";
        } else if ((signal.auxiliaryTypes || []).includes("previousHigh") && riseFromBreakoutLowPercent > 7) {
          reason = "突破K首次触发前低点到前高的涨幅超过 7%，不做这次突破前高";
        } else if (snapshots.length < rule.available && !independentLeaderStrength && !partialHigherFramePermit && !contextualMainWavePermit) reason = "大周期证据不足";
        else if (bullish < rule.bullish && !independentLeaderStrength && !partialHigherFramePermit && !contextualMainWavePermit) reason = "大周期未共振";
        const anchorRule = executionAnchors[result.interval];
        if (!reason && anchorRule) {
          const anchored = anchorRule.frames.some((interval) => (
            hasNearbyAnchor(byInterval.get(interval), decisionTime, anchorRule.windowMs)
            || hasTrendAnchor(byInterval.get(interval), decisionTime)
          ));
          if (!anchored && !oneMinuteLeaderLaunch && !childFramePrecision && !contextualMainWavePermit) reason = "小周期缺少上级结构锚点";
        }
        if (!reason && result.interval === "1m") {
          const aPlusBoxPrototype = Boolean(signal.outerEdgeConfirmed)
            && (signal.outerEdgeScore || 0) >= 84
            && (signal.consolidationBars || 0) >= 28
            && (signal.ceilingTouches || 0) >= 2
            && (signal.certaintyScore || 0) >= 78
            && (signal.rhythmScore || 0) >= 66;
          const elite = oneMinuteLeaderLaunch || (
            ((signal.confluence?.length || 1) >= 2 || aPlusBoxPrototype)
              && signal.score >= (aPlusBoxPrototype ? 76 : 86)
              && signal.rhythmScore >= (aPlusBoxPrototype ? 66 : 62)
              && signal.sentimentScore >= 58
              && isHighCertaintyEntry(signal)
          );
          if (!elite) reason = "1分钟仅执行 A+ 精确买点";
        }
        if (!reason && ["5m", "15m", "1h"].includes(result.interval) && !signal.crossFramePrecision) {
          if (!isHighCertaintyEntry(signal) && !recognizedHigherTimeframeBreak) reason = "仅保留高确定性起爆：成熟母结构、真正外沿与主升节奏尚未同时成立";
        }
        if (!reason && result.interval === "4h") {
          const foundations = signal.foundationTypes || [];
          // 进入 signals 的4小时三角/降楔/箱体已经在原生识别层完成真实突破
          // 审计，环境层不再因为“只突破动态上沿”而把它降成纯许可锚点。
          const nativeRecognizedStructure = signal.status === "buy"
            && foundations.some((type) => ["base", "triangle", "relaunch"].includes(type))
            && signal.openedBeyondTrigger !== true
            && signal.motherStructureNoise !== true
            && signal.riskStructureShape == null
            && signal.highLevelDistribution !== true;
          const elite = recognizedHigherTimeframeBreak
            || nativeRecognizedStructure
            || isFourHourCausalStructureIgnition(signal)
            || ((signal.confluence?.length || 1) >= 2
              && signal.score >= 84
              && signal.rhythmScore >= 60
              && signal.sentimentScore >= 58
              && isHighCertaintyEntry(signal));
          if (!elite) reason = "4小时仅执行 A+ 大结构";
        }
        // 进入本页面的标的已经由文档样本或实时龙头池完成“龙头”筛选。
        // BTC 只作为背景备注，不能否决龙头自身已经成立的因果起爆结构。
        const marketEmotion = marketSnapshots.length < 2
          ? "BTC 仅作背景 · 不参与龙头买点否决"
          : marketBullish >= 1
            ? `BTC 背景 ${marketBullish}/2 · 不参与龙头买点否决`
            : "BTC 背景逆风 0/2 · 龙头独立判断";
        if (!reason) target.push(normalizeDisplayedStructureLabels({
          ...contextualSignal,
          mainWaveStage: declaredMainWaveStage || contextualSignal.mainWaveStage || "neutral",
          mainWaveContextSource: declaredMainWavePermit
            ? declaredMainWaveContextSource
            : leaderDefaultMainWaveEnvironmentPermit
              ? declaredMainWaveContextSource
            : newCoinNotFallingMainWavePermit
              ? "new-coin-not-falling"
              : preHigherFrameMainWaveIgnitionPermit
                ? "leader-main-wave-ignition"
                : inferredMainWavePermit
                  ? "strategy-main-wave"
                  : contextualSignal.mainWaveContextSource,
          mainWaveHigherFramePermit,
          preHigherFrameMainWaveIgnitionPermit,
          newCoinNotFallingMainWavePermit,
          newCoinListingAgeHours: newCoinNotFallingMainWavePermit
            ? Number(earlyNewCoinHistory.listingAgeHours.toFixed(1))
            : null,
          newCoinHigherFrameCounts: newCoinNotFallingMainWavePermit
            ? Object.fromEntries(earlyNewCoinHistory.stats.map((item) => [item.interval, item.count]))
            : null,
          newCoinMarketHeat,
          marketEmotion,
          evidence: [
            ...contextualSignal.evidence,
            ...(declaredMainWavePermit ? [
              `${declaredMainWaveContextLabel}：主升前4小时/日线下跌形成的旧压力、高点和低点不参与否决，当前结构质量与真实突破条件保持不变`,
            ] : []),
            ...(leaderDefaultMainWaveEnvironmentPermit ? [
              `${declaredMainWaveContextLabel}：高周期数据源缺失、切换或暂未共振时，不撤销本周期已经成熟的母平台/三角真实外沿突破`,
              "默认主升浪只替代环境许可；盘整成熟度、结构外沿、突破K、风险形态和追高距离仍按原规则执行",
            ] : []),
            ...(newCoinNotFallingMainWavePermit ? [
              `新币不跌后的主升浪：上市约 ${earlyNewCoinHistory.listingAgeHours.toFixed(0)} 小时，现有高周期样本仍短（${earlyNewCoinHistory.stats.map((item) => `${item.interval} ${item.count}根`).join(" / ")}）；4小时和日线不足不否决，也不把 EMA90 未形成当作转弱`,
              `新币止跌并完成独立上推，情绪 ${signal.sentimentScore || 0} 分、节奏 ${signal.rhythmScore || 0} 分；只在成熟外沿真实突破时执行`,
            ] : []),
            ...(preHigherFrameMainWaveIgnitionPermit ? [
              "龙头主升启动不反向要求大周期主升已经形成：本周期独立上推、成熟上升三角、平台真外沿与前高突破已经共振",
              ...(signal.staleUnqualifiedMotherBoundaryIgnored ? [
                "无前置拉升的上市早期下跌高低点已排除，不作为后续母箱体边界",
              ] : []),
            ] : []),
            ...(inferredMainWavePermit ? [
              `策略已判定${effectiveMainWaveStage === "active" ? "主升浪成立" : "具备主升浪预期"}：主升前4小时/日线旧下跌边界仅作背景，不再否决本周期成熟起爆结构`,
              "主升后新形成的急杀母箱体、高位大分歧和当前结构风险仍保留过滤",
            ] : []),
            marketEmotion,
          ],
        }));
        else downgraded.push({
          ...contextualSignal,
          id: `${signal.id}-context-veto`,
          status: "filtered",
          score: Math.max(0, signal.score - 22),
          reasons: [...signal.reasons, reason],
          evidence: [...contextualSignal.evidence, `大周期许可 ${bullish}/${snapshots.length}`, marketEmotion],
        });
      };
      sourceSignals.forEach((signal) => gateItem(signal, keptSignals));
      (result.pending || []).forEach((signal) => gateItem(signal, keptPending));
      const rejected = [...sourceRejected, ...downgraded].sort((a, b) => a.time - b.time);
      return {
        ...result,
        signals: keptSignals,
        pending: keptPending,
        rejected,
        stats: {
          ...result.stats,
          signalCount: keptSignals.length,
          pendingCount: keptPending.length,
          rejectedCount: rejected.length,
        },
      };
    });
    return gated;
  }

  function enforceIntervalStructurePolicy(result) {
    if (!result || result.interval !== "1m") return result;
    const reason = "1分钟最终白名单：非高确定性横盘起飞或箱体突破买点不显示";
    const keptSignals = [];
    const keptPending = [];
    const keptSecondaryHints = [];
    const demoted = [];
    const canonicalHorizontalSignal = (signal) => {
      const hasPreviousHigh = (signal.auxiliaryTypes || []).includes("previousHigh")
        || String(signal.pattern || "").includes("突破前高");
      return {
        ...signal,
        pattern: hasPreviousHigh ? "横盘起飞 + 突破前高" : "横盘起飞",
        patternKey: "base",
        confluence: hasPreviousHigh ? ["base", "previousHigh"] : ["base"],
        foundationTypes: ["base"],
        auxiliaryTypes: hasPreviousHigh ? ["previousHigh"] : [],
        hasPivot: false,
        trendline: null,
        triangleLines: null,
        structureShape: null,
        oneMinuteCanonicalHorizontal: true,
        evidence: [...new Set([
          ...(signal.evidence || []),
          "1分钟只保留横盘起飞主结构；箱体内部附带的拐点、回踩或再启动标签不参与显示和开仓分类",
        ])],
      };
    };
    const route = (signal, target) => {
      if (isOneMinuteHorizontalBase(signal)) {
        target.push(canonicalHorizontalSignal(signal));
        return;
      }
      demoted.push({
        ...signal,
        status: "filtered",
        reasons: [...new Set([...(signal.reasons || []), reason])],
      });
    };
    (result.signals || []).forEach((signal) => route(signal, keptSignals));
    (result.pending || []).forEach((signal) => route(signal, keptPending));
    (result.secondaryBreakoutHints || []).forEach((signal) => route(signal, keptSecondaryHints));
    const rejected = [...(result.rejected || []), ...demoted]
      .sort((a, b) => a.time - b.time || a.index - b.index);
    return {
      ...result,
      signals: keptSignals,
      pending: keptPending,
      secondaryBreakoutHints: keptSecondaryHints,
      rejected,
      // 防止旧缓存、人工反馈快照或未来代码改动再次把非白名单斜线带回
      // 1分钟盘面。手动画线数据独立保存，不受这里影响。
      structures: [],
      stats: {
        ...(result.stats || {}),
        signalCount: keptSignals.length,
        pendingCount: keptPending.length,
        secondaryBreakoutHintCount: keptSecondaryHints.length,
        rejectedCount: rejected.length,
      },
    };
  }

  function summarizeTimeframes(results, focusTime) {
    const valid = (Array.isArray(results) ? results : []).filter((item) => item && item.candles?.length);
    const regimeSnapshots = Number.isFinite(focusTime)
      ? valid.map((item) => regimeAt(item, focusTime)).filter(Boolean)
      : valid.map((item) => item.regime);
    const bullishCount = regimeSnapshots.filter((item) => item.bullish).length;
    const signalCount = valid.reduce((sum, item) => sum + item.signals.length, 0);
    const pendingCount = valid.reduce((sum, item) => sum + (item.pending?.length || 0), 0);
    const rejectedCount = valid.reduce((sum, item) => sum + item.rejected.length, 0);
    const bestSignal = latestItem(valid.flatMap((item) => item.signals)) || null;
    const bestPending = latestItem(valid.flatMap((item) => item.pending || [])) || null;
    const higherFrames = valid.filter((item) => ["1h", "4h", "1d"].includes(item.interval));
    const higherAligned = Number.isFinite(focusTime)
      ? higherFrames.map((item) => regimeAt(item, focusTime)).filter((item) => item?.bullish).length
      : higherFrames.filter((item) => item.regime.bullish).length;
    const status = higherAligned >= 2 && bestSignal
      ? "等待低周期触发"
      : higherAligned >= 2
        ? "主升环境成立"
        : "环境未通过";
    return { bullishCount, signalCount, pendingCount, rejectedCount, bestSignal, bestPending, higherAligned, status };
  }

  return Object.freeze({
    PATTERN_LABELS,
    normalizeCandles,
    ema,
    atr,
    assessBoundaryContainment,
    assessEnvelopeCoverage,
    detectAscendingChannelTrap,
    assessDescendingTrendlineGeometry,
    assessEma90ReclaimContinuation,
    assessHorizontalLaunchContext,
    assessHorizontalBaseUrgency,
    assessHorizontalBaseDwell,
    assessOuterPlatformContinuity,
    detectOuterPlatform,
    resolveTruePriorHighBoundary,
    detectBrokenOuterPlatformContext,
    assessPreStructureContext,
    assessMotherStructureNoise,
    isExceptionalShockBoxHorizontalLaunch,
    isExceptionalShockBoxAscendingTriangleIgnition,
    assessExecutionHierarchy,
    buildSecondaryBreakoutHints,
    detectTriangle,
    detectDescendingTrendline,
    detectLongConvergence,
    recoverLongTriangleAfterSoftBoundaryTests,
    isAttachedSoftBoundaryTest,
    findCandidates,
    evaluateCandidate,
    assessHighLevelDistribution,
    analyzeTimeframe,
    isRetainableConsolidationCandidate,
    isDirectionalRecoveryPivotOnly,
    normalizeDisplayedStructureLabels,
    structureLifecycleDecision,
    isHighCertaintyEntry,
    oneHourPostImpulseWedgeStructureScore,
    isOneHourPostImpulseWedgeIgnition,
    isMatureOneHourLongTriangleReset,
    isLongBasePreviousHighIgnition,
    isSoftTestExtendedTriangleBreakout,
    isMatureOneHourOuterPlatformReset,
    isOneHourRelaunchPivotIgnition,
    isOneHourCompactAscendingTriangleIgnition,
    isMatureFifteenMinuteRetryPlatformIgnition,
    isMatureFifteenMinutePriorHighTriangleIgnition,
    isReviewedHigherTimeframeStructureBreak,
    isFourHourCausalStructureIgnition,
    isRecognizedHigherTimeframeStructureBreak,
    isOneMinuteHorizontalBase,
    promoteLowerFrameIgnitionToParent,
    promoteAdjacentMotherChildConfluence,
    rebuildCausalParentAtChild,
    applyContextGates,
    assessEarlyNewCoinHistory,
    enforceIntervalStructurePolicy,
    pruneSignalBudget,
    regimeAt,
    summarizeTimeframes,
  });
});
