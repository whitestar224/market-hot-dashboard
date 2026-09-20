"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const Engine = require("../dragon-wave-engine.js");
const snapshots = require("./fixtures/dragon_wave_structure_lifecycle_review.json");
const bankSnapshot = require("./fixtures/dragon_wave_bank_secondary_review.json");

function fixture(id) {
  const entry = snapshots.cases.find((row) => row.id === id);
  assert.ok(entry, `missing causal snapshot: ${id}`);
  return structuredClone(entry);
}

function decide(entry) {
  return Engine.structureLifecycleDecision(
    [entry.prior], entry.evaluation, entry.candles, entry.index, entry.atr,
  );
}

function replaceBetween(entry, rows) {
  const priorCandle = entry.candles[entry.prior.index];
  const currentCandle = entry.candles[entry.index];
  const step = entry.candles[entry.prior.index + 1].time - priorCandle.time;
  const timedRows = rows.map((row, offset) => ({
    ...row, time: priorCandle.time + (offset + 1) * step,
  }));
  entry.candles = [
    ...entry.candles.slice(0, entry.prior.index + 1),
    ...timedRows,
    { ...currentCandle, time: priorCandle.time + (rows.length + 1) * step },
  ];
  entry.index = entry.candles.length - 1;
  entry.evaluation.index = entry.index;
  entry.evaluation.time = entry.candles.at(-1).time;
  return entry;
}

function lifecycleFilteredHints(entry) {
  const lifecycle = decide(entry);
  assert.ok(lifecycle.reason, "this helper tests secondary routing only after a formal-entry veto");
  const prior = structuredClone(entry.prior);
  // Full-cache prior records may contain annotations created when a later hint
  // was built. They are not evidence available at the original attempt.
  delete prior.secondaryDirectCandleExpansion;
  delete prior.secondaryReferenceHigh;
  delete prior.secondaryWashBars;
  const atrValues = entry.atrValues || Array(entry.candles.length).fill(entry.atr);
  if (!entry.atrValues) atrValues[prior.index - 1] = prior.atrAtDecision;
  const second = {
    ...entry.evaluation,
    status: "filtered",
    reasons: [lifecycle.reason],
  };
  return Engine.buildSecondaryBreakoutHints(
    [prior], entry.candles, { atr: atrValues }, entry.evaluation.interval, [second],
  );
}

test("PI: repaired failed trial may retry the mature outer boundary without an arbitrary ATR upgrade", () => {
  const entry = fixture("pi-stopped-platform-retry");
  const between = entry.candles.slice(entry.prior.index + 1, entry.index);
  const trough = Math.min(...between.map((row) => row.low));
  const troughOffset = between.findIndex((row) => row.low === trough);

  // These are pre-trigger facts, not the breakout candle's eventual close or return.
  assert.ok(between.some((row) => row.low <= entry.prior.breakoutLow));
  assert.ok(troughOffset < between.length - 2, "pullback low occurred before the repair");
  assert.ok(between.at(-1).low > trough && between.at(-1).close > between[troughOffset].close);
  assert.ok(entry.evaluation.outerEdgeConfirmed);
  assert.equal(entry.evaluation.executionHierarchy.primaryFoundation, "mother-platform-breakout");
  assert.ok((entry.evaluation.triggerPrice - entry.prior.triggerPrice) / entry.atr < 0.18);
  const result = decide(entry);
  assert.equal(result.reason, "", "the washed-out trial must not occupy this repaired mature opportunity");
  assert.equal(result.retryMaturity, true);
});

test("TUT: a still-active platform cannot recycle its breakout candle's new high as a second mature boundary", () => {
  const entry = fixture("tut-active-platform-duplicate");
  const between = entry.candles.slice(entry.prior.index + 1, entry.index);
  const lifecycleStop = Math.max(entry.prior.breakoutLow, entry.prior.stop);
  assert.equal(between.length, 1);
  assert.ok(between.every((row) => row.low > lifecycleStop));
  assert.equal(entry.evaluation.level, entry.candles[entry.prior.index].high);
  assert.ok(entry.evaluation.consolidationBars >= 40, "long inherited context is not new boundary maturity");
  const result = decide(entry);
  assert.ok(result.reason, "retain secondary-hint eligibility, but do not issue another formal B here");
  assert.equal(result.retryMaturity, false);
});

test("a boundary created by the prior breakout can mature through genuinely separated new retests", () => {
  const entry = fixture("tut-active-platform-duplicate");
  const level = entry.evaluation.level;
  const atr = entry.atr;
  const touch = { open: level - atr * 0.18, high: level, low: level - atr * 0.24, close: level - atr * 0.05 };
  const retreat = { open: level - atr * 0.4, high: level - atr * 0.3, low: level - atr * 0.65, close: level - atr * 0.5 };
  const firstPause = entry.candles[entry.prior.index + 1];
  replaceBetween(entry, [firstPause, touch, retreat, touch, retreat]);
  assert.ok(entry.candles.slice(entry.prior.index + 1, entry.index)
    .every((row) => row.low > entry.prior.breakoutLow));
  const result = decide(entry);
  assert.equal(result.reason, "", "new retests mature the boundary without an arbitrary extra waiting period");
  assert.equal(result.retryMaturity, true);
});

test("consecutive near-edge candles with one taller wick are one retest, not two independent touch groups", () => {
  const entry = fixture("tut-active-platform-duplicate");
  const level = entry.evaluation.level;
  const atr = entry.atr;
  const near = { open: level - atr * 0.03, high: level, low: level - atr * 0.06, close: level - atr * 0.02 };
  const tallerWick = { ...near, high: level + atr * 0.2 };
  const firstPause = entry.candles[entry.prior.index + 1];
  replaceBetween(entry, [firstPause, near, tallerWick, near]);
  assert.ok(decide(entry).reason,
    "a wick outside the touch tolerance is not a below-boundary retreat that rearms a new touch group");
});

test("BANK: suppressing a duplicate formal candidate retains the confirmed red secondary-breakout hint", () => {
  const entry = structuredClone(bankSnapshot);
  const current = entry.candles[entry.index];
  const priorHigh = entry.candles[entry.prior.index].high;
  assert.equal(entry.provenance.originalStatus, "secondary-hint");
  assert.ok(entry.prior.relativeVolume < 1.15);
  assert.ok(entry.evaluation.relativeVolume > entry.prior.relativeVolume);
  // This is the existing close-confirmed alert layer, not evidence permitting
  // an intrabar formal B. No candle after this close exists in the fixture.
  assert.ok(current.close > priorHigh);
  const hints = lifecycleFilteredHints(entry);
  assert.equal(hints.length, 1);
  assert.equal(hints[0].time, current.time);
  assert.equal(hints[0].status, "secondary-hint");
  assert.equal(hints[0].markerColor, "red");
  assert.equal(hints[0].alertOnly, true);
  assert.equal(hints[0].executionAllowed, false);
  assert.equal(hints[0].secondaryReferenceHigh, priorHigh);
  assert.equal(hints[0].primaryAttemptIndex, entry.prior.index);
});

test("TUT: an unrecaptured first-breakout high must not be promoted into a red hint either", () => {
  const entry = fixture("tut-active-platform-duplicate");
  assert.ok(entry.candles[entry.index].close < entry.candles[entry.prior.index].high);
  assert.equal(lifecycleFilteredHints(entry).length, 0);
});

test("a failed trial followed only by falling lows is not a repaired structure merely because old scores remain high", () => {
  const entry = fixture("pi-stopped-platform-retry");
  const first = entry.prior.index + 1;
  const count = entry.index - first;
  for (let at = first; at < entry.index; at += 1) {
    const close = entry.prior.breakoutLow - entry.atr * (0.2 + (at - first) * 0.18);
    Object.assign(entry.candles[at], {
      open: close + entry.atr * 0.12,
      high: close + entry.atr * 0.15,
      low: close - entry.atr * 0.15,
      close,
    });
  }
  assert.ok(count >= 3);
  assert.equal(
    Math.min(...entry.candles.slice(first, entry.index).map((row) => row.low)),
    entry.candles[entry.index - 1].low,
  );
  // Only the current candle recrosses; the preceding prefix has never repaired.
  const previousClose = entry.candles[entry.index - 1].close;
  entry.candles[entry.index].open = previousClose;
  entry.candles[entry.index].low = previousClose;
  entry.evaluation.breakoutOpen = previousClose;
  entry.evaluation.breakoutLow = previousClose;
  assert.ok(decide(entry).reason, "no post-trial repair means no new formal entry");
});

for (const id of ["pi-stopped-platform-retry", "tut-active-platform-duplicate"]) {
  test(`${id}: appending future candles cannot alter the lifecycle decision`, () => {
    const entry = fixture(id);
    const before = decide(entry);
    const last = entry.candles.at(-1);
    entry.candles.push(
      { ...last, time: last.time + 900_000, open: last.close, high: last.high * 10, low: last.low * 0.01, close: last.close * 5 },
      { ...last, time: last.time + 1_800_000, open: last.close * 5, high: last.high * 10, low: last.low * 0.001, close: last.close * 0.01 },
    );
    assert.deepEqual(decide(entry), before);
  });
}

for (const id of ["turbo-first-hourly-platform", "turbo-next-hourly-platform", "not-hourly-triangle"]) {
  test(`${id}: preserve the previously confirmed independent hourly opportunity`, () => {
    const entry = fixture(id);
    assert.equal(decide(entry).reason, "");
  });
}

test("lifecycle identity checks do not mutate cached candidates or promote a hint into a formal buy", () => {
  const entry = fixture("tut-active-platform-duplicate");
  const before = structuredClone(entry);
  decide(entry);
  assert.deepEqual(entry, before);
});

test("lifecycle decisions are invariant under price scaling and date translation", () => {
  const absolutePrices = new Set([
    "open", "high", "low", "close", "level", "price", "triggerPrice", "stop",
    "atr", "atrAtDecision", "breakoutOpen", "breakoutClose", "breakoutLow", "breakoutLowBeforeTrigger",
    "startPrice", "endPrice", "anchorPrice", "previousHighLevel", "secondaryReferenceHigh",
    "motherStructureHigh", "motherStructureLow", "triangleMotherHigh", "ema90AtDecision",
  ]);
  const shiftMs = 83 * 24 * 60 * 60_000;
  function transform(value, factor, key = "") {
    if (Array.isArray(value)) return value.map((row) => transform(row, factor));
    if (value && typeof value === "object") return Object.fromEntries(
      Object.entries(value).map(([field, item]) => [field, transform(item, factor, field)]),
    );
    if (typeof value !== "number") return value;
    if (absolutePrices.has(key)) return value * factor;
    if (key === "time" || key.endsWith("Time") || key === "featureCutoff") return value + shiftMs;
    return value;
  }
  for (const id of ["pi-stopped-platform-retry", "tut-active-platform-duplicate",
    "turbo-first-hourly-platform", "turbo-next-hourly-platform", "not-hourly-triangle"]) {
    const entry = fixture(id);
    const expected = decide(entry);
    for (const factor of [0.01, 37, 1_000]) {
      assert.deepEqual(decide(transform(entry, factor)), expected, `${id}: price factor ${factor}`);
    }
  }
});
