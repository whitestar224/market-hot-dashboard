const test = require("node:test");
const assert = require("node:assert/strict");
const Engine = require("../dragon-wave-engine.js");
const Data = require("../dragon-wave-data.js");
const raw = require("./fixtures/turbousdt_okx_15m_2024-05-21_2024-05-25.json");

const rows = Data.parseRows("okx", raw, 15 * 60_000);
const options = {
  interval: "15m",
  preselectedLeader: true,
  mainWaveStage: "active",
  mainWaveContextSource: "leader-default-main-wave",
};
let complete;
function analyze(input) {
  return Engine.analyzeTimeframe(input, {
    ...options,
    now: input.at(-1).closeTime + 1,
  });
}
function fullResult() {
  if (!complete) complete = analyze(rows);
  return complete;
}

const localBreaks = [
  { label: "2024-05-23 13:15", time: Date.parse("2024-05-23T05:15:00Z"), motherHigh: 0.0019653 },
  { label: "2024-05-24 09:45", time: Date.parse("2024-05-24T01:45:00Z"), motherHigh: 0.0029646 },
];

for (const example of localBreaks) {
  test(`TURBO ${example.label} 15m does not give a compact internal-high break mother-boundary authority`, () => {
    const result = fullResult();
    const current = rows.find((row) => row.time === example.time);
    assert.ok(current.high < example.motherHigh,
      "the current candle has not crossed the already-known complete platform peak");
    assert.equal(result.signals.some((signal) => signal.time === example.time), false);
    const rejected = result.rejected.find((signal) => (
      signal.time === example.time
      && signal.consolidationBars < 28
      && signal.trianglePreviousHighIsOuter === false
    ));
    assert.ok(rejected, "the incomplete static-boundary evidence must remain auditable");
    assert.ok(Math.abs(rejected.triangleMotherHigh - example.motherHigh) < 1e-12);
    assert.ok(rejected.previousHighLevel < rejected.triangleMotherHigh);
    assert.equal(rejected.executionHierarchy.permit, false);
    assert.ok(rejected.reasons.length > 0);
  });

  test(`TURBO ${example.label} compact-boundary verdict does not depend on later candles`, () => {
    const index = rows.findIndex((row) => row.time === example.time);
    const causal = analyze(rows.slice(0, index + 1));
    const full = fullResult();
    assert.equal(causal.signals.some((signal) => signal.time === example.time), false);
    const choose = (result) => result.rejected.find((signal) => (
      signal.time === example.time
      && signal.consolidationBars < 28
      && signal.trianglePreviousHighIsOuter === false
    ));
    const before = choose(causal);
    const after = choose(full);
    assert.ok(before && after);
    assert.equal(before.triangleMotherHigh, after.triangleMotherHigh);
    assert.equal(before.previousHighLevel, after.previousHighLevel);
    assert.equal(before.triggerPrice, after.triggerPrice);
    assert.equal(before.executionHierarchy.permit, after.executionHierarchy.permit);
    assert.deepEqual(before.reasons, after.reasons);
  });
}

test("TURBO 2024-05-23 23:15 genuine full-platform breakout remains a native B", () => {
  const time = Date.parse("2024-05-23T15:15:00Z");
  const fullSignal = fullResult().signals.find((signal) => signal.time === time);
  assert.ok(fullSignal);
  assert.equal(fullSignal.outerEdgeConfirmed, true);
  assert.ok(fullSignal.foundationTypes.includes("base"));
  assert.equal(fullSignal.primaryPatternKey, "consolidationBreakout");
  const index = rows.findIndex((row) => row.time === time);
  const causalSignal = analyze(rows.slice(0, index + 1)).signals.find((signal) => signal.time === time);
  assert.ok(causalSignal);
  assert.equal(causalSignal.triggerPrice, fullSignal.triggerPrice);
  assert.equal(causalSignal.pattern, fullSignal.pattern);
});

test("TURBO 2024-05-24 04:45 confirmed short platform keeps its independent outer-edge permission", () => {
  const time = Date.parse("2024-05-23T20:45:00Z");
  const signal = fullResult().signals.find((candidate) => candidate.time === time);
  assert.ok(signal, "the confirmed 22-candle platform is not subject to a blanket short-structure filter");
  assert.equal(signal.consolidationBars, 22);
  assert.ok(signal.foundationTypes.includes("triangle"));
  assert.ok(signal.foundationTypes.includes("base"));
  assert.equal(signal.outerEdgeConfirmed, true);
  assert.equal(signal.executionHierarchy.permit, true);
  const index = rows.findIndex((row) => row.time === time);
  const causal = analyze(rows.slice(0, index + 1)).signals.find((candidate) => candidate.time === time);
  assert.ok(causal);
  assert.equal(causal.triggerPrice, signal.triggerPrice);
});

test("a short triangle may still qualify when its prior high is the complete structure outer edge", () => {
  const signal = {
    interval: "15m",
    foundationTypes: ["triangle"],
    auxiliaryTypes: ["previousHigh"],
    hasPivot: true,
    consolidationBars: 21,
    structureQuality: 0.81,
    channelInteriorOccupancy: 0.86,
    channelSideTransitions: 2,
    triangleHasPriorAdvance: true,
    crossedLevel: true,
    openedBeyondTrigger: false,
    previousHighLevel: 100,
    triangleMotherHigh: 100,
    trianglePreviousHighIsOuter: true,
    outerEdgeConfirmed: false,
    directStructuralBoundary: false,
    matureTriangleOuterEdge: false,
    relativeVolume: 0.44,
    orderFlowScore: 30,
  };
  const fullBoundary = Engine.assessExecutionHierarchy(signal);
  assert.equal(fullBoundary.permit, true,
    "neither a fixed 28-candle minimum nor pre-breakout low volume should erase a true short outer-edge break");
  assert.equal(fullBoundary.primaryFoundation, "mature-triangle-outer-edge");
  const internalBoundary = Engine.assessExecutionHierarchy({
    ...signal,
    triangleMotherHigh: 105,
    trianglePreviousHighIsOuter: false,
  });
  assert.equal(internalBoundary.permit, false);
});
