const test = require("node:test");
const assert = require("node:assert/strict");

const Vision = require("../dragon-wave-vision.js");

function makeSeries({ scale = 1, noisy = false } = {}) {
  const start = Date.parse("2025-01-01T00:00:00Z");
  return Array.from({ length: 181 }, (_, index) => {
    const impulse = index < 24 ? index * 0.42 : 10;
    const progress = Math.max(0, index - 24) / 156;
    const center = 100 + impulse - progress * 2.4;
    const cleanWave = Math.sin(index * 0.52) * (2.8 - progress * 1.6);
    const noisyWave = Math.sin(index * 2.3) * 4.8 + (index % 2 ? 2.4 : -2.4);
    const wave = noisy ? noisyWave : cleanWave;
    const open = (center + wave * 0.72) * scale;
    const close = (center + wave) * scale;
    const wick = (noisy ? 2.2 + (index % 5) : 0.65 + (index % 4) * 0.08) * scale;
    return {
      time: start + index * 60 * 60_000,
      closeTime: start + (index + 1) * 60 * 60_000 - 1,
      open,
      high: Math.max(open, close) + wick,
      low: Math.min(open, close) - wick,
      close,
      volume: noisy ? 100 + (index % 7) * 90 : 100 + index * 0.3,
    };
  });
}

test("visual signature is causal and ignores the selected breakout candle and all future candles", () => {
  const rows = makeSeries();
  const index = 180;
  const first = Vision.buildVisualSignature(rows, index, { interval: "1h", triggerPrice: 110 });
  const altered = rows.map((row) => ({ ...row }));
  altered[index] = { ...altered[index], open: 999, high: 1200, low: 1, close: 2, volume: 1e12 };
  altered.push({ ...altered[index], time: altered[index].time + 60 * 60_000, closeTime: altered[index].closeTime + 60 * 60_000 });
  const second = Vision.buildVisualSignature(altered, index, { interval: "1h", triggerPrice: 110 });
  assert.deepEqual(second, first);
  assert.equal(first.featureCutoffTime, rows[index - 1].closeTime);
  assert.equal(first.selectedCandleTime, rows[index].time);
  assert.equal(first.causality, "completed-candles-before-selected-index-only");
});

test("visual similarity is invariant to absolute token price scale", () => {
  const original = makeSeries();
  const scaled = makeSeries({ scale: 0.0075 });
  const left = Vision.buildVisualSignature(original, 180, { interval: "1h", triggerPrice: 110 });
  const right = Vision.buildVisualSignature(scaled, 180, { interval: "1h", triggerPrice: 110 * 0.0075 });
  const comparison = Vision.compareVisualSignatures(left, right);
  assert.ok(comparison.score >= 96, JSON.stringify(comparison));
  assert.equal(comparison.matchedWindows, 3);
});

test("a clean converging silhouette is visually distinct from noisy oscillation", () => {
  const clean = makeSeries();
  const noisy = makeSeries({ noisy: true });
  const cleanSignature = Vision.buildVisualSignature(clean, 180, { interval: "1h", triggerPrice: 110 });
  const noisySignature = Vision.buildVisualSignature(noisy, 180, { interval: "1h", triggerPrice: 110 });
  const same = Vision.compareVisualSignatures(cleanSignature, cleanSignature);
  const different = Vision.compareVisualSignatures(cleanSignature, noisySignature);
  assert.equal(same.score, 100);
  assert.ok(different.score <= 78, JSON.stringify(different));
  assert.ok(same.score - different.score >= 22, JSON.stringify({ same, different }));
});

test("structure-aware signature adds a resampled focus window without reading outside the labeled range", () => {
  const rows = makeSeries();
  const first = Vision.buildVisualSignature(rows, 180, {
    interval: "1h",
    triggerPrice: 110,
    structureStartIndex: 96,
    structureSource: "manual",
  });
  assert.equal(first.version, 2);
  assert.equal(first.model, "causal-kline-structure-raster-v2");
  assert.equal(first.structure.startIndex, 96);
  assert.equal(first.structure.startTime, rows[96].time);
  assert.equal(first.structure.bars, 84);
  assert.equal(first.structure.source, "manual");
  const focus = first.windows.find((window) => window.kind === "focus");
  assert.ok(focus, JSON.stringify(first));
  assert.equal(focus.span, "focus");
  assert.equal(focus.bars, 84);

  const altered = rows.map((row, index) => index < 96
    ? { ...row, open: row.open * 7, high: row.high * 8, low: row.low * 6, close: row.close * 7 }
    : { ...row });
  const second = Vision.buildVisualSignature(altered, 180, {
    interval: "1h",
    triggerPrice: 110,
    structureStartIndex: 96,
    structureSource: "manual",
  });
  assert.deepEqual(second.windows.find((window) => window.kind === "focus"), focus);
});

test("structure geometry recognizes an occupied rotation and separates a hollow one-sided channel", () => {
  const makeChannel = (hollow) => Array.from({ length: 81 }, (_, index) => {
    const upper = 112 - index * 0.04;
    const lower = 96 - index * 0.01;
    const spread = upper - lower;
    const position = hollow
      ? (index < 10 ? 0.9 - index * 0.08 : 0.1 + Math.sin(index) * 0.02)
      : 0.5 + Math.cos(Math.PI * 2 * index / 12) * 0.4;
    const close = lower + spread * position;
    return {
      time: Date.parse("2025-02-01T00:00:00Z") + index * 60 * 60_000,
      closeTime: Date.parse("2025-02-01T00:00:00Z") + (index + 1) * 60 * 60_000 - 1,
      open: close + (index % 2 ? 0.06 : -0.06),
      high: close + 0.18,
      low: close - 0.18,
      close,
      volume: 100,
    };
  });
  const occupiedRows = makeChannel(false);
  const hollowRows = makeChannel(true);
  const occupied = Vision.buildVisualSignature(occupiedRows, 80, {
    interval: "1h", triggerPrice: occupiedRows[80].open, structureStartIndex: 4,
  });
  const hollow = Vision.buildVisualSignature(hollowRows, 80, {
    interval: "1h", triggerPrice: hollowRows[80].open, structureStartIndex: 4,
  });
  const occupiedFocus = occupied.windows.find((window) => window.kind === "focus");
  const hollowFocus = hollow.windows.find((window) => window.kind === "focus");
  assert.ok(occupiedFocus.stats.interiorOccupancy >= 580, JSON.stringify(occupiedFocus.stats));
  assert.ok(occupiedFocus.stats.channelSideTransitions >= 5, JSON.stringify(occupiedFocus.stats));
  assert.ok(hollowFocus.stats.interiorOccupancy <= 470, JSON.stringify(hollowFocus.stats));
  assert.ok(hollowFocus.stats.hollowRatio >= 550, JSON.stringify(hollowFocus.stats));
  assert.ok(Vision.compareVisualSignatures(occupied, hollow).score <= 78);
});
