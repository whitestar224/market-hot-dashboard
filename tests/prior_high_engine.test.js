const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const PriorHigh = require("../prior-high-engine.js");

function candle(index, open, high, low, close) {
  return {
    time: (index + 1) * 60_000,
    closeTime: (index + 2) * 60_000 - 1,
    open,
    high,
    low,
    close,
    volume: 100,
  };
}

const compactConfig = {
  atrPeriod: 3,
  pivotLeft: 2,
  pivotRight: 2,
  minProminenceAtr: 0.35,
  internalProminenceAtr: 0.5,
  swingProminenceAtr: 1,
  majorProminenceAtr: 2,
  temporalResetBars: 3,
  temporalBelowRatio: 0.66,
  spatialResetAtr: 0.8,
  spatialResetPct: 0.01,
  triggerBufferAtr: 0,
  triggerBufferPct: 0,
  nearThresholdPct: 0.025,
  alertMicro: true,
};

test("a confirmed level alerts only after separation and a fresh crossing", () => {
  const rows = [
    candle(0, 95, 96, 94, 95),
    candle(1, 96, 98, 95, 97),
    candle(2, 98, 100, 97, 99),
    candle(3, 98, 99, 96, 97),
    candle(4, 96, 98, 94, 95),
    candle(5, 95, 97, 93, 94),
    candle(6, 94, 98, 93, 97),
    candle(7, 97, 101, 96, 100.5),
  ];
  const result = PriorHigh.analyze(rows, "5m", compactConfig);
  assert.equal(result.breakouts.length, 1);
  assert.equal(result.breakouts[0].barIndex, 7);
  assert.equal(result.breakouts[0].anchor, 100);
  assert.notEqual(result.breakouts[0].separationType, "NONE");
});

test("the same level does not spam and a newly confirmed higher edge blocks its lower rebreak", () => {
  const rows = [
    candle(0, 95, 96, 94, 95), candle(1, 96, 98, 95, 97),
    candle(2, 98, 100, 97, 99), candle(3, 98, 99, 96, 97),
    candle(4, 96, 98, 94, 95), candle(5, 95, 97, 93, 94),
    candle(6, 94, 98, 93, 97), candle(7, 97, 101, 96, 100.5),
    candle(8, 100.5, 102, 100, 101), candle(9, 101, 102, 99.5, 100.5),
    candle(10, 100, 100.2, 98, 99), candle(11, 99, 99.5, 97.5, 98.5),
    candle(12, 98.5, 101.5, 98, 101),
  ];
  const result = PriorHigh.analyze(rows, "5m", compactConfig);
  const sameLevel = result.breakouts.filter((event) => event.anchor === 100);
  assert.deepEqual(sameLevel.map((event) => event.barIndex), [7]);
  assert.deepEqual(sameLevel.map((event) => event.breakoutCount), [1]);
});

test("nearby pivots share a frozen identity instead of chasing price", () => {
  const rows = [
    candle(0, 95, 96, 94, 95), candle(1, 97, 99, 96, 98),
    candle(2, 99, 100, 98, 99), candle(3, 98, 99, 96, 97),
    candle(4, 97, 98, 95, 96), candle(5, 98, 100.1, 97, 99),
    candle(6, 99, 99.5, 97, 98), candle(7, 98, 99, 95, 96),
    candle(8, 96, 98, 94, 95), candle(9, 95, 101, 94, 100.5),
  ];
  const result = PriorHigh.analyze(rows, "15m", {
    ...compactConfig,
    identityAtr: 1,
    identityPct: 0.01,
    maxIdentityZoneAtr: 2,
  });
  const merged = result.levels.find((level) => level.sourcePivots.includes(2));
  assert.ok(merged);
  assert.equal(merged.anchor, 100);
  assert.ok(merged.sourcePivots.includes(5));
  assert.equal(merged.touchCount, 2);
  assert.ok(merged.zoneHigh >= 100.1);
});

test("near reminders are marked before the breakout B and do not replace it", () => {
  const rows = [
    candle(0, 95, 96, 94, 95), candle(1, 96, 98, 95, 97),
    candle(2, 98, 100, 97, 99), candle(3, 98, 99, 96, 97),
    candle(4, 96, 97, 93, 94), candle(5, 94, 96, 93, 95),
    candle(6, 95, 98.4, 94, 98), candle(7, 98, 101, 97.5, 100.5),
  ];
  const result = PriorHigh.analyze(rows, "1h", compactConfig);
  assert.ok(result.reminders.some((event) => event.barIndex === 6));
  assert.ok(result.breakouts.some((event) => event.barIndex === 7));
  const chart = PriorHigh.toChartResult(result, {
    candles: rows,
    indicators: { ema90: rows.map(() => 95), atr: rows.map(() => 2) },
    stats: { lastPrice: rows.at(-1).close },
  });
  assert.equal(chart.signals.at(-1).status, "buy");
  assert.equal(chart.signals.at(-1).patternKey, "previousHigh");
  assert.equal(chart.pending.at(-1).status, "pending");
});

test("old consolidation and higher-timeframe permission gates are not applied", () => {
  const rows = [
    candle(0, 95, 96, 94, 95), candle(1, 96, 98, 95, 97),
    candle(2, 98, 100, 97, 99), candle(3, 98, 99, 95, 96),
    candle(4, 96, 97, 92, 93), candle(5, 93, 96, 92, 95),
    candle(6, 95, 97, 94, 96), candle(7, 96, 98.5, 95, 98),
    candle(8, 98, 100.5, 97, 100.2),
  ];
  const result = PriorHigh.analyze(rows, "4h", compactConfig);
  assert.equal(result.breakouts.length, 1);
  assert.equal(result.breakouts[0].barIndex, 8);
});

test("mother consolidation hides internal prior-high crosses until the true outer edge breaks", () => {
  const rows = [
    candle(0, 100, 102, 99, 101),
    candle(1, 101, 105, 100, 104),
    candle(2, 104, 110, 103, 108),
    candle(3, 108, 106, 102, 104),
    candle(4, 104, 103, 100, 101),
    candle(5, 101, 102, 99, 100),
    candle(6, 100, 105, 99.5, 104),
    candle(7, 104, 103, 100, 101),
    candle(8, 101, 102, 99, 100),
    candle(9, 100, 106, 99.5, 105),
    candle(10, 105, 104, 101, 102),
    candle(11, 102, 103, 100, 101),
    candle(12, 101, 111, 100.5, 110.5),
  ];
  const result = PriorHigh.analyze(rows, "15m", compactConfig);
  assert.deepEqual(result.breakouts.map((event) => event.barIndex), [12]);
  assert.equal(result.breakouts[0].anchor, 110);
  assert.ok(result.reminders.every((event) => event.anchor === 110));
});

test("merged resistance triggers at its highest zone edge rather than the first lower anchor", () => {
  const rows = [
    candle(0, 95, 96, 94, 95), candle(1, 97, 99, 96, 98),
    candle(2, 99, 100, 98, 99), candle(3, 98, 99, 96, 97),
    candle(4, 97, 98, 95, 96), candle(5, 98, 100.1, 97, 99),
    candle(6, 99, 99.5, 97, 98), candle(7, 98, 99, 95, 96),
    candle(8, 96, 98, 94, 95), candle(9, 95, 100.05, 94, 100),
    candle(10, 100, 100.3, 98, 100.2),
  ];
  const result = PriorHigh.analyze(rows, "15m", {
    ...compactConfig,
    identityAtr: 1,
    identityPct: 0.01,
    maxIdentityZoneAtr: 2,
  });
  assert.ok(!result.breakouts.some((event) => event.barIndex === 9));
  assert.equal(result.breakouts.at(-1).barIndex, 10);
  assert.equal(result.breakouts.at(-1).anchor, 100.1);
});

test("dashboard exposes the prior-high page without replacing the ignition page", () => {
  const root = path.resolve(__dirname, "..");
  const html = fs.readFileSync(path.join(root, "dragon-wave.html"), "utf8");
  const script = fs.readFileSync(path.join(root, "dragon-wave.js"), "utf8");
  assert.match(html, /data-strategy-page="ignition"/);
  assert.match(html, /data-strategy-page="prior-high"/);
  assert.match(html, /prior-high-engine\.js/);
  assert.match(script, /PriorHigh\.analyzeForChart/);
  assert.match(script, /前高突破 B/);
  assert.match(script, /接近前高提醒/);
});
