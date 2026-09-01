const test = require("node:test");
const assert = require("node:assert/strict");

const Engine = require("../dragon-wave-engine.js");

test("long convergence keeps independently timed anchors inside the post-impulse structure window", () => {
  const candles = [];
  for (let index = 0; index < 110; index += 1) {
    if (index < 20) {
      const close = 88 + index * 0.82;
      candles.push({
        time: index + 1,
        open: close - 0.18,
        high: close + 0.3,
        low: close - 0.38,
        close,
        volume: 112,
      });
      continue;
    }
    const upper = 120 - 0.2 * (index - 20);
    const lower = 90 - 0.035 * (index - 8);
    const phase = Math.sin(2 * Math.PI * (index - 2) / 12);
    const center = (upper + lower) / 2;
    const amplitude = (upper - lower) / 2 - 0.15;
    const close = center + phase * amplitude;
    candles.push({
      time: index + 1,
      open: close - 0.05,
      high: Math.min(upper, close + 0.35),
      low: Math.max(lower, close - 0.35),
      close,
      volume: 100,
    });
  }
  const upperCandidate = {
    trendline: {
      startIndex: 20,
      startPrice: 120,
      anchorIndex: 68,
      anchorPrice: 110.4,
      touches: 5,
      anchorMode: "wick",
    },
  };
  const wedge = Engine.detectLongConvergence(candles, 101, 2, upperCandidate);
  assert.ok(wedge);
  assert.equal(wedge.structureShape, "falling-wedge");
  assert.equal(wedge.triangleLines.upper.startIndex, 20);
  assert.ok(wedge.triangleLines.lower.startIndex > wedge.triangleLines.upper.startIndex);
  assert.ok(wedge.triangleLines.lower.displayStartIndex < wedge.triangleLines.lower.startIndex);
  assert.ok(wedge.triangleLines.lower.displayStartIndex >= wedge.triangleLines.structureStartIndex);
  assert.equal(
    wedge.triangleLines.lower.displayStartPrice,
    wedge.triangleLines.lower.startPrice
      + ((wedge.triangleLines.lower.anchorPrice - wedge.triangleLines.lower.startPrice)
        / (wedge.triangleLines.lower.anchorIndex - wedge.triangleLines.lower.startIndex))
      * (wedge.triangleLines.lower.displayStartIndex - wedge.triangleLines.lower.startIndex),
  );
  assert.equal(wedge.triangleLines.structureStartIndex, 20);
  assert.equal(wedge.triangleLines.anchorScope, "post-impulse-consolidation");
  assert.equal(wedge.triangleLines.lower.anchorScope, "post-impulse-consolidation");
  assert.equal(wedge.triangleLines.lower.boundaryModel, "outer-envelope");
  const upperSlope = (wedge.triangleLines.upper.endPrice - wedge.triangleLines.upper.startPrice)
    / (wedge.triangleLines.upper.endIndex - wedge.triangleLines.upper.startIndex);
  const lowerSlope = (wedge.triangleLines.lower.endPrice - wedge.triangleLines.lower.startPrice)
    / (wedge.triangleLines.lower.endIndex - wedge.triangleLines.lower.startIndex);
  assert.ok(upperSlope < lowerSlope, "upper boundary must fall faster so the wedge converges");
});

test("high-level wide disagreement is vetoed before trendlines are used", () => {
  const candles = [];
  for (let index = 0; index < 70; index += 1) {
    if (index < 30) {
      candles.push({ time: index + 1, open: 100, high: 101, low: 99, close: 100, volume: 100 });
      continue;
    }
    const shock = index % 5 === 0;
    candles.push({
      time: index + 1,
      open: 126,
      high: shock ? 140 : 132,
      low: shock ? 116 : 123,
      close: shock ? 120 : 128,
      volume: shock ? 500 : 140,
    });
  }
  const indicators = { atr: candles.map(() => 2), ema90: candles.map(() => 100) };
  const structure = { consolidationBars: 40, trendline: { startIndex: 30 } };
  const assessment = Engine.assessHighLevelDistribution(candles, 69, indicators, structure, 2);
  assert.equal(assessment.risky, true);
  assert.ok(assessment.score >= 5);
  assert.ok(assessment.disagreementBars >= 3);
});

test("aesthetic envelope contains nearly all structure candles and rejects lines weaving through bodies", () => {
  const clean = Array.from({ length: 48 }, (_, index) => ({
    time: index + 1,
    open: 99.7,
    high: 104.2,
    low: 95.8,
    close: 100.3,
    volume: 100,
  }));
  const upperAt = () => 105;
  const lowerAt = () => 95;
  const cleanEnvelope = Engine.assessEnvelopeCoverage(clean, 0, clean.length, upperAt, lowerAt, 2);
  assert.equal(cleanEnvelope.acceptable, true);
  assert.ok(cleanEnvelope.bodyCoverage >= 0.95);
  assert.equal(cleanEnvelope.crossingBars, 0);

  const woven = clean.map((row, index) => {
    if (index % 4 !== 0) return row;
    return index % 8 === 0
      ? { ...row, open: 103.8, close: 106.2, high: 106.6 }
      : { ...row, open: 96.2, close: 93.8, low: 93.4 };
  });
  const wovenEnvelope = Engine.assessEnvelopeCoverage(woven, 0, woven.length, upperAt, lowerAt, 2);
  assert.equal(wovenEnvelope.acceptable, false);
  assert.ok(wovenEnvelope.crossingRatio > 0.08);
});
