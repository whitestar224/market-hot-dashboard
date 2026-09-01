(function attachDragonWaveVision(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.DragonWaveVision = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createDragonWaveVision() {
  "use strict";

  const VERSION = 2;
  const DEFAULT_WINDOWS = Object.freeze([40, 80, 160]);
  const WIDTH = 24;
  const HEIGHT = 16;

  function finite(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function mean(values) {
    return values.length ? values.reduce((sum, value) => sum + finite(value), 0) / values.length : 0;
  }

  function quantile(values, ratio) {
    if (!values.length) return 0;
    const ordered = values.map(finite).sort((a, b) => a - b);
    const position = clamp(ratio, 0, 1) * (ordered.length - 1);
    const lower = Math.floor(position);
    const upper = Math.ceil(position);
    if (lower === upper) return ordered[lower];
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower);
  }

  function regressionLine(values) {
    if (!values.length) return { slope: 0, lineAt: () => 0 };
    const xMean = (values.length - 1) / 2;
    const yMean = mean(values);
    let numerator = 0;
    let denominator = 0;
    values.forEach((value, index) => {
      numerator += (index - xMean) * (finite(value) - yMean);
      denominator += (index - xMean) ** 2;
    });
    const slope = denominator ? numerator / denominator : 0;
    const intercept = yMean - slope * xMean;
    return { slope, lineAt: (index) => intercept + slope * index };
  }

  function fitEnvelope(values, side) {
    const regression = regressionLine(values);
    const residuals = values.map((value, index) => finite(value) - regression.lineAt(index));
    const shift = quantile(residuals, side === "upper" ? 0.9 : 0.1);
    return {
      slope: regression.slope,
      lineAt: (index) => regression.lineAt(index) + shift,
    };
  }

  function ema(values, length = 90) {
    if (!values.length) return [];
    const alpha = 2 / (length + 1);
    const output = [finite(values[0])];
    for (let index = 1; index < values.length; index += 1) {
      output.push(finite(values[index]) * alpha + output[index - 1] * (1 - alpha));
    }
    return output;
  }

  function normalizeCandle(row) {
    const open = finite(row?.open, finite(row?.close));
    const close = finite(row?.close, open);
    return {
      time: Math.trunc(finite(row?.time)),
      closeTime: Math.trunc(finite(row?.closeTime, finite(row?.time))),
      open,
      high: Math.max(open, close, finite(row?.high, Math.max(open, close))),
      low: Math.min(open, close, finite(row?.low, Math.min(open, close))),
      close,
      volume: Math.max(0, finite(row?.volume)),
    };
  }

  function aggregateWindow(rows, emaRows, width = WIDTH) {
    const bins = [];
    for (let x = 0; x < width; x += 1) {
      const start = Math.floor(x * rows.length / width);
      const end = Math.max(start + 1, Math.floor((x + 1) * rows.length / width));
      const segment = rows.slice(start, Math.min(rows.length, end));
      const emaSegment = emaRows.slice(start, Math.min(emaRows.length, end));
      if (!segment.length) continue;
      bins.push({
        open: segment[0].open,
        high: Math.max(...segment.map((row) => row.high)),
        low: Math.min(...segment.map((row) => row.low)),
        close: segment.at(-1).close,
        volume: segment.reduce((sum, row) => sum + row.volume, 0),
        ema: mean(emaSegment),
      });
    }
    return bins;
  }

  function binaryJaccard(left, right) {
    const length = Math.min(String(left || "").length, String(right || "").length);
    if (!length) return 0;
    let intersection = 0;
    let union = 0;
    for (let index = 0; index < length; index += 1) {
      const a = left[index] === "1";
      const b = right[index] === "1";
      if (a || b) union += 1;
      if (a && b) intersection += 1;
    }
    return union ? intersection / union : 1;
  }

  function pathSimilarity(left, right, scale = 1000) {
    const length = Math.min(left?.length || 0, right?.length || 0);
    if (!length) return 0;
    let distance = 0;
    for (let index = 0; index < length; index += 1) {
      distance += Math.min(1, Math.abs(finite(left[index]) - finite(right[index])) / scale);
    }
    return 1 - distance / length;
  }

  function hexPathSimilarity(left, right, maximum = HEIGHT - 1) {
    const length = Math.min(String(left || "").length, String(right || "").length);
    if (!length) return 0;
    let distance = 0;
    for (let index = 0; index < length; index += 1) {
      distance += Math.abs(parseInt(left[index], 16) - parseInt(right[index], 16)) / Math.max(maximum, 1);
    }
    return 1 - distance / length;
  }

  function structureGeometry(bins, priceRange) {
    const upper = fitEnvelope(bins.map((row) => row.high), "upper");
    const lower = fitEnvelope(bins.map((row) => row.low), "lower");
    const segmentCount = bins.length >= 16 ? 4 : 2;
    const bandCount = 8;
    const envelopeBands = Array.from({ length: segmentCount }, () => new Set());
    const globalBands = Array.from({ length: segmentCount }, () => new Set());
    const globalLow = Math.min(...bins.map((row) => row.low));
    const globalHigh = Math.max(...bins.map((row) => row.high));
    const globalRange = Math.max(globalHigh - globalLow, priceRange * 0.01, 1e-8);
    let middleVisits = 0;
    let hollowRows = 0;
    let longestHollowRun = 0;
    let currentHollowRun = 0;
    let previousHollowSide = null;
    let previousBoundarySide = null;
    let channelSideTransitions = 0;
    let globalMiddleVisits = 0;
    let globalHollowRows = 0;
    let globalLongestHollowRun = 0;
    let globalCurrentHollowRun = 0;
    let previousGlobalHollowSide = null;
    let previousGlobalBoundarySide = null;
    let globalSideTransitions = 0;
    let validRows = 0;
    const upperPath = [];
    const lowerPath = [];
    bins.forEach((row, index) => {
      const upperLine = upper.lineAt(index);
      const lowerLine = lower.lineAt(index);
      const gap = upperLine - lowerLine;
      upperPath.push(upperLine);
      lowerPath.push(lowerLine);
      if (gap <= priceRange * 0.015) return;
      validRows += 1;
      const wickLow = clamp((row.low - lowerLine) / gap, 0, 1);
      const wickHigh = clamp((row.high - lowerLine) / gap, 0, 1);
      const bodyLow = clamp((Math.min(row.open, row.close) - lowerLine) / gap, 0, 1);
      const bodyHigh = clamp((Math.max(row.open, row.close) - lowerLine) / gap, 0, 1);
      const midpoint = (bodyLow + bodyHigh) / 2;
      const segment = Math.min(segmentCount - 1, Math.floor(index / Math.max(bins.length / segmentCount, 1)));
      const envelopeStart = Math.min(bandCount - 1, Math.max(0, Math.floor(wickLow * bandCount)));
      const envelopeEnd = Math.min(bandCount - 1, Math.max(0, Math.floor(wickHigh * bandCount)));
      for (let band = envelopeStart; band <= envelopeEnd; band += 1) envelopeBands[segment].add(band);
      const globalStart = Math.min(bandCount - 1, Math.max(0, Math.floor((row.low - globalLow) / globalRange * bandCount)));
      const globalEnd = Math.min(bandCount - 1, Math.max(0, Math.floor((row.high - globalLow) / globalRange * bandCount)));
      for (let band = globalStart; band <= globalEnd; band += 1) globalBands[segment].add(band);
      const globalWickLow = clamp((row.low - globalLow) / globalRange, 0, 1);
      const globalWickHigh = clamp((row.high - globalLow) / globalRange, 0, 1);
      const globalMidpoint = clamp((row.open + row.close) / 2 - globalLow, 0, globalRange) / globalRange;
      if (globalWickHigh >= 0.34 && globalWickLow <= 0.66) globalMiddleVisits += 1;
      const globalBoundarySide = globalMidpoint <= 0.3 ? "lower" : globalMidpoint >= 0.7 ? "upper" : null;
      if (globalBoundarySide && previousGlobalBoundarySide && globalBoundarySide !== previousGlobalBoundarySide) {
        globalSideTransitions += 1;
      }
      if (globalBoundarySide) previousGlobalBoundarySide = globalBoundarySide;
      const globalHollowSide = globalWickHigh <= 0.42 ? "lower" : globalWickLow >= 0.58 ? "upper" : null;
      if (globalHollowSide) {
        globalHollowRows += 1;
        globalCurrentHollowRun = globalHollowSide === previousGlobalHollowSide ? globalCurrentHollowRun + 1 : 1;
        previousGlobalHollowSide = globalHollowSide;
        globalLongestHollowRun = Math.max(globalLongestHollowRun, globalCurrentHollowRun);
      } else {
        globalCurrentHollowRun = 0;
        previousGlobalHollowSide = null;
      }
      if (wickHigh >= 0.34 && wickLow <= 0.66) middleVisits += 1;
      const boundarySide = midpoint <= 0.32 ? "lower" : midpoint >= 0.68 ? "upper" : null;
      if (boundarySide && previousBoundarySide && boundarySide !== previousBoundarySide) channelSideTransitions += 1;
      if (boundarySide) previousBoundarySide = boundarySide;
      const hollowSide = wickHigh <= 0.44 ? "lower" : wickLow >= 0.56 ? "upper" : null;
      if (hollowSide) {
        hollowRows += 1;
        currentHollowRun = hollowSide === previousHollowSide ? currentHollowRun + 1 : 1;
        previousHollowSide = hollowSide;
        longestHollowRun = Math.max(longestHollowRun, currentHollowRun);
      } else {
        currentHollowRun = 0;
        previousHollowSide = null;
      }
    });
    const envelopeOccupancy = mean(envelopeBands.map((bands) => bands.size / bandCount));
    const globalOccupancy = mean(globalBands.map((bands) => bands.size / bandCount));
    const middleParticipation = middleVisits / Math.max(validRows, 1);
    const globalMiddleParticipation = globalMiddleVisits / Math.max(validRows, 1);
    const effectiveTransitions = Math.max(channelSideTransitions, globalSideTransitions);
    const transitionQuality = clamp(effectiveTransitions / 6, 0, 1);
    const interiorOccupancy = clamp(
      envelopeOccupancy * 0.24
      + globalOccupancy * 0.36
      + globalMiddleParticipation * 0.2
      + transitionQuality * 0.2,
      0,
      1,
    );
    const initialGap = upperPath[0] - lowerPath[0];
    const lateGap = upperPath.at(-1) - lowerPath.at(-1);
    return {
      upperPath,
      lowerPath,
      stats: {
        interiorOccupancy: Math.round(interiorOccupancy * 1000),
        envelopeOccupancy: Math.round(envelopeOccupancy * 1000),
        globalOccupancy: Math.round(globalOccupancy * 1000),
        middleParticipation: Math.round(middleParticipation * 1000),
        globalMiddleParticipation: Math.round(globalMiddleParticipation * 1000),
        hollowRatio: Math.round(Math.max(hollowRows, globalHollowRows) / Math.max(validRows, 1) * 1000),
        longestHollowRun: Math.round(Math.max(longestHollowRun, globalLongestHollowRun) / Math.max(validRows, 1) * 1000),
        channelSideTransitions: effectiveTransitions,
        upperSlope: Math.round(upper.slope / Math.max(priceRange, 1e-8) * Math.max(bins.length - 1, 1) * 1000),
        lowerSlope: Math.round(lower.slope / Math.max(priceRange, 1e-8) * Math.max(bins.length - 1, 1) * 1000),
        convergence: Math.round(clamp((initialGap - lateGap) / Math.max(Math.abs(initialGap), priceRange * 0.02), -1, 1) * 1000),
      },
    };
  }

  function encodeWindow(rows, emaRows, span, triggerPrice, kind = "context") {
    const bins = aggregateWindow(rows, emaRows, WIDTH);
    if (!bins.length) return null;
    const minimum = Math.min(...bins.map((row) => row.low), ...bins.map((row) => row.ema));
    const maximum = Math.max(...bins.map((row) => row.high), ...bins.map((row) => row.ema));
    const priceRange = Math.max(maximum - minimum, Math.max(Math.abs(maximum), 1) * 1e-8);
    const rowAt = (price) => clamp(Math.round((maximum - finite(price)) / priceRange * (HEIGHT - 1)), 0, HEIGHT - 1);
    const wickCells = Array(WIDTH * HEIGHT).fill("0");
    const bodyCells = Array(WIDTH * HEIGHT).fill("0");
    const emaPath = [];
    const closePath = [];
    const rangePath = [];
    const volumeMaximum = Math.max(...bins.map((row) => row.volume), 1e-8);
    const volumePath = [];
    bins.forEach((row, x) => {
      const highRow = rowAt(row.high);
      const lowRow = rowAt(row.low);
      const bodyTop = Math.min(rowAt(row.open), rowAt(row.close));
      const bodyBottom = Math.max(rowAt(row.open), rowAt(row.close));
      for (let y = highRow; y <= lowRow; y += 1) wickCells[y * WIDTH + x] = "1";
      for (let y = bodyTop; y <= bodyBottom; y += 1) bodyCells[y * WIDTH + x] = "1";
      emaPath.push(rowAt(row.ema).toString(16));
      closePath.push(Math.round((row.close - minimum) / priceRange * 1000));
      rangePath.push(Math.round((row.high - row.low) / priceRange * 1000));
      volumePath.push(clamp(Math.round(row.volume / volumeMaximum * 15), 0, 15).toString(16));
    });
    const firstThird = bins.slice(0, Math.max(2, Math.floor(bins.length / 3)));
    const lastThird = bins.slice(-Math.max(2, Math.floor(bins.length / 3)));
    const firstRange = mean(firstThird.map((row) => row.high - row.low));
    const lastRange = mean(lastThird.map((row) => row.high - row.low));
    const firstEnvelope = Math.max(...firstThird.map((row) => row.high)) - Math.min(...firstThird.map((row) => row.low));
    const lastEnvelope = Math.max(...lastThird.map((row) => row.high)) - Math.min(...lastThird.map((row) => row.low));
    const directions = bins.slice(1).map((row, index) => Math.sign(row.close - bins[index].close));
    const directionChanges = directions.slice(1).filter((direction, index) => (
      direction !== 0 && directions[index] !== 0 && direction !== directions[index]
    )).length;
    const geometry = structureGeometry(bins, priceRange);
    return {
      kind,
      span,
      bars: rows.length,
      width: WIDTH,
      height: HEIGHT,
      wick: wickCells.join(""),
      body: bodyCells.join(""),
      ema: emaPath.join(""),
      volume: volumePath.join(""),
      envelopeUpper: geometry.upperPath.map((value) => rowAt(value).toString(16)).join(""),
      envelopeLower: geometry.lowerPath.map((value) => rowAt(value).toString(16)).join(""),
      closePath,
      rangePath,
      triggerRow: rowAt(triggerPrice),
      stats: {
        drift: Math.round((bins.at(-1).close - bins[0].close) / priceRange * 1000),
        rangeCompression: Math.round(clamp(lastRange / Math.max(firstRange, 1e-8), 0, 4) * 250),
        envelopeCompression: Math.round(clamp(lastEnvelope / Math.max(firstEnvelope, 1e-8), 0, 4) * 250),
        directionChangeRatio: Math.round(directionChanges / Math.max(directions.length - 1, 1) * 1000),
        ...geometry.stats,
      },
    };
  }

  function buildVisualSignature(candles, selectedIndex, options = {}) {
    const rows = (Array.isArray(candles) ? candles : []).map(normalizeCandle);
    const index = clamp(Math.trunc(finite(selectedIndex)), 0, rows.length);
    const prior = rows.slice(0, index);
    if (prior.length < 24 || !rows[index]) return null;
    const providedEma = Array.isArray(options.ema90) ? options.ema90.slice(0, index).map(finite) : null;
    const completeEma = providedEma?.length === prior.length
      ? providedEma
      : ema(prior.map((row) => row.close), 90);
    const requestedWindows = [...new Set((options.windows || DEFAULT_WINDOWS).map((value) => Math.max(24, Math.trunc(finite(value)))))];
    const triggerPrice = finite(options.triggerPrice, prior.at(-1).close);
    const windows = requestedWindows.map((span) => {
      const length = Math.min(span, prior.length);
      return encodeWindow(prior.slice(-length), completeEma.slice(-length), span, triggerPrice, "context");
    }).filter(Boolean);
    const requestedStart = Math.trunc(finite(options.structureStartIndex, -1));
    const structureStartIndex = requestedStart >= 0 && requestedStart <= index - 12
      ? requestedStart
      : -1;
    let structure = null;
    if (structureStartIndex >= 0) {
      const focusRows = prior.slice(structureStartIndex);
      const focusEma = ema(focusRows.map((row) => row.close), 90);
      const focusWindow = encodeWindow(focusRows, focusEma, "focus", triggerPrice, "focus");
      if (focusWindow) windows.unshift(focusWindow);
      structure = {
        source: String(options.structureSource || "strategy"),
        startIndex: structureStartIndex,
        startTime: prior[structureStartIndex].time,
        bars: index - structureStartIndex,
      };
    }
    return {
      version: VERSION,
      model: "causal-kline-structure-raster-v2",
      interval: String(options.interval || ""),
      selectedCandleTime: rows[index].time,
      featureCutoffTime: prior.at(-1).closeTime,
      causality: "completed-candles-before-selected-index-only",
      structure,
      windows,
    };
  }

  function compareWindow(left, right) {
    const wick = binaryJaccard(left.wick, right.wick);
    const body = binaryJaccard(left.body, right.body);
    const emaScore = hexPathSimilarity(left.ema, right.ema);
    const volume = hexPathSimilarity(left.volume, right.volume, 15);
    const envelopeUpper = left.envelopeUpper && right.envelopeUpper
      ? hexPathSimilarity(left.envelopeUpper, right.envelopeUpper)
      : 0.5;
    const envelopeLower = left.envelopeLower && right.envelopeLower
      ? hexPathSimilarity(left.envelopeLower, right.envelopeLower)
      : 0.5;
    const close = pathSimilarity(left.closePath, right.closePath);
    const range = pathSimilarity(left.rangePath, right.rangePath);
    const trigger = 1 - Math.min(1, Math.abs(finite(left.triggerRow) - finite(right.triggerRow)) / (HEIGHT - 1));
    const statKeys = [
      "drift", "rangeCompression", "envelopeCompression", "directionChangeRatio",
      "interiorOccupancy", "envelopeOccupancy", "globalOccupancy", "middleParticipation",
      "globalMiddleParticipation",
      "hollowRatio", "longestHollowRun", "upperSlope", "lowerSlope", "convergence",
    ];
    const stats = mean(statKeys.map((key) => (
      1 - Math.min(1, Math.abs(finite(left.stats?.[key]) - finite(right.stats?.[key])) / 1000)
    )));
    const envelope = (envelopeUpper + envelopeLower) / 2;
    const score = wick * 0.11
      + body * 0.16
      + emaScore * 0.06
      + volume * 0.04
      + close * 0.19
      + range * 0.13
      + trigger * 0.06
      + envelope * 0.08
      + stats * 0.17;
    return {
      span: left.span,
      score: Math.round(clamp(score, 0, 1) * 100),
      wick: Math.round(wick * 100),
      body: Math.round(body * 100),
      path: Math.round(close * 100),
      range: Math.round(range * 100),
      geometry: Math.round(stats * 100),
    };
  }

  function compareVisualSignatures(left, right) {
    if (!left || !right || left.version !== VERSION || right.version !== VERSION) {
      return { score: 0, matchedWindows: 0, windows: [] };
    }
    const rightBySpan = new Map((right.windows || []).map((window) => [`${window.kind || "context"}:${window.span}`, window]));
    const windows = (left.windows || []).map((window) => {
      const key = `${window.kind || "context"}:${window.span}`;
      const match = rightBySpan.get(key);
      return match ? { ...compareWindow(window, match), kind: window.kind || "context" } : null;
    }).filter(Boolean);
    const totalWeight = windows.reduce((sum, window) => sum + (window.kind === "focus" ? 1.8 : 1), 0);
    return {
      score: windows.length ? Math.round(windows.reduce((sum, window) => (
        sum + window.score * (window.kind === "focus" ? 1.8 : 1)
      ), 0) / Math.max(totalWeight, 1)) : 0,
      matchedWindows: windows.length,
      windows,
    };
  }

  return Object.freeze({
    VERSION,
    DEFAULT_WINDOWS,
    buildVisualSignature,
    compareVisualSignatures,
  });
});
