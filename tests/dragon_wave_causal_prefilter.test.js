const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const zlib = require("node:zlib");

const root = path.join(__dirname, "..");
const enginePath = path.join(root, "dragon-wave-engine.js");
const engineSource = fs.readFileSync(enginePath, "utf8");
const Vision = require("../dragon-wave-vision.js");
const INTERVAL_MS = { "1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000 };

// An isolated proposal, deliberately not installed in the production engine.
// A negative result is sufficient to skip replay; a positive result must still
// execute all historical lifecycle decisions and secondary-breakout filtering.
function preflightRebuild(engine, spec, observations = []) {
  const { parent, lower, parentSignal, childSignal, interval, options = {} } = spec;
  const childCandle = lower?.candles?.[childSignal?.index];
  const parentStart = Number(parentSignal?.time);
  const parentMs = INTERVAL_MS[interval];
  if (!childCandle || !Number.isFinite(parentStart) || !parentMs) return null;
  const cutoff = Number(childCandle.closeTime ?? childCandle.time);
  const childRows = lower.candles.filter((row) => row.time >= parentStart
    && row.time < parentStart + parentMs && (row.closeTime ?? row.time) <= cutoff);
  if (!childRows.length) return null;
  const partialParent = {
    time: parentStart, closeTime: cutoff, open: childRows[0].open,
    high: Math.max(...childRows.map((row) => row.high)),
    low: Math.min(...childRows.map((row) => row.low)),
    close: childRows.at(-1).close,
    volume: childRows.reduce((sum, row) => sum + (row.volume || 0), 0),
    quoteVolume: childRows.reduce((sum, row) => sum + (row.quoteVolume || 0), 0),
    takerBuyVolume: childRows.reduce((sum, row) => sum + (row.takerBuyVolume || 0), 0),
    tradeCount: childRows.reduce((sum, row) => sum + (row.tradeCount || 0), 0),
  };
  const candles = engine.normalizeCandles([
    ...(parent.candles || []).filter((row) => row.time < parentStart), partialParent,
  ], cutoff);
  const index = candles.length - 1;
  if (index < 30 || candles[index].time !== parentStart) return null;
  let volumeSum = 0;
  const indicators = {
    ema90: engine.ema(candles.map((row) => row.close), 90),
    atr: engine.atr(candles, 14),
    volumeMean: candles.map((row, cursor) => {
      volumeSum += row.volume;
      if (cursor >= 20) volumeSum -= candles[cursor - 20].volume;
      return volumeSum / Math.min(20, cursor + 1);
    }),
  };
  const candidates = engine.findCandidates(candles, index, indicators, { interval, rightEdge: true });
  const evaluations = candidates.map((candidate) => engine.evaluateCandidate(candles, index, candidate, indicators, interval, {
    interval, now: cutoff, preselectedLeader: true, mainWaveStage: options.mainWaveStage,
    mainWaveContextSource: "adjacent-frame-causal-rebuild", mainWaveContextLabel: "相邻周期因果重建",
  }));
  const possible = evaluations.some((item) => {
    const selectable = item.crossedLevel === true || (options.allowPreconfirmedParent === true
      && item.status === "pending"
      && (item.foundationTypes || []).some((type) => ["base", "triangle"].includes(type))
      && (item.consolidationBars || 0) >= 32 && (item.structureQuality || 0) >= 0.65);
    const gap = Math.abs(item.triggerPrice / Math.max(childSignal.triggerPrice, 1e-8) - 1) * 100;
    // Preserve the legacy > comparison, including its NaN behavior. Lifecycle
    // inheritance changes geometry and scores, but never the trigger or status.
    return ["buy", "pending"].includes(item.status) && selectable && !(gap > 1.8);
  });
  observations.push({ possible, evaluations, historyBars: candles.length });
  if (!possible) return null;
  return rebuild(engine, spec);
}

function rebuild(engine, spec) {
  return engine.rebuildCausalParentAtChild(spec.parent, spec.lower, spec.parentSignal,
    spec.childSignal, spec.interval, spec.options);
}

function loadEngine(hooks = {}, source = engineSource, native = false) {
  const intercept = (signature, body) => {
    assert.ok(source.includes(signature), signature);
    source = source.replace(signature, `${signature}\n${body}`);
  };
  intercept("function analyzeTimeframe(rows, options = {}) {", "__hooks.analyze?.(rows, options);");
  intercept("function findCandidates(candles, index, indicators, options = {}) {",
    "if (__hooks.find) return __hooks.find(candles, index, indicators, options);");
  intercept("function evaluateCandidate(candles, index, candidate, indicators, interval, options = {}) {",
    "if (__hooks.evaluate) return __hooks.evaluate(candles, index, candidate, indicators, interval, options);");
  intercept("function structureLifecycleDecision(signals, evaluation, candles, index, atrValue) {",
    "if (__hooks.lifecycle) return __hooks.lifecycle(signals, evaluation, candles, index, atrValue);");
  intercept("function buildSecondaryBreakoutHints(items, candles, indicators, interval, secondaryItems = items) {",
    "if (__hooks.secondary) return __hooks.secondary(items, candles, indicators, interval, secondaryItems);");
  intercept("function assessExecutionHierarchy(signal) {",
    "if (__hooks.hierarchy) return __hooks.hierarchy(signal);");
  if (hooks.process) intercept("function processAnalysisCandle(state, candles, index, indicators, interval, options, rightEdge) {",
    "__hooks.process(candles, index, interval, rightEdge);");
  if (hooks.preflight) intercept("function hasPotentialCausalParentEntry(evaluations) {",
    "__hooks.preflight(evaluations);");
  if (hooks.promote) intercept("function promoteLowerFrameIgnitionToParent(byInterval, result, signal, preselectedLeader, mainWaveStage) {",
    "return __hooks.promote(signal);");
  if (hooks.analyzed) source = source.replace("return Object.freeze({", `
    const originalAnalyzeForPrefilterTest = analyzeTimeframe;
    analyzeTimeframe = function observeAnalysisForPrefilterTest(rows, options) {
      const result = originalAnalyzeForPrefilterTest(rows, options);
      __hooks.analyzed(result);
      return result;
    };
    return Object.freeze({`);
  const sandbox = { module: { exports: {} }, require: () => Vision, __hooks: hooks };
  if (native) {
    // Use host-realm built-ins for real histories; bind globalThis to the private
    // sandbox so the UMD attachment does not change this test process's globals.
    return vm.compileFunction(`${source}\nreturn module.exports;`, ["module", "require", "__hooks", "globalThis"], {
      filename: enginePath,
    })(sandbox.module, sandbox.require, hooks, sandbox);
  }
  vm.runInNewContext(source, sandbox, { filename: enginePath });
  return sandbox.module.exports;
}

function fakeHarness(evaluation, { lifecycle, secondary } = {}) {
  const start = 1_700_000_000_000;
  const candles = Array.from({ length: 40 }, (_, index) => ({
    time: start + index * 900_000, closeTime: start + (index + 1) * 900_000 - 1,
    open: 99, high: 101, low: 98, close: 100, volume: 100,
  }));
  const state = { analyses: 0, lifecycles: 0, finalizers: 0 };
  const engine = loadEngine({
    analyze: () => { state.analyses += 1; },
    find: (rows, index) => index === rows.length - 1 ? [{ crossedLevel: evaluation.crossedLevel }] : [],
    evaluate: (rows, index) => ({
      id: "test-parent", time: rows[index].time, index, interval: "15m",
      triggerPrice: 100, level: 100, score: 90, certaintyScore: 90,
      foundationTypes: ["base"], auxiliaryTypes: [], confluence: ["base"],
      consolidationBars: 40, structureQuality: 0.8, reasons: [], evidence: [],
      ...evaluation,
    }),
    lifecycle: (...args) => { state.lifecycles += 1; return lifecycle?.(...args) || { reason: "" }; },
    secondary: (...args) => { state.finalizers += 1; return secondary?.(...args) || []; },
    hierarchy: () => ({ permit: true }),
  });
  const parentStart = candles[35].time;
  const spec = {
    parent: { candles }, lower: { candles: [{ ...candles[35], closeTime: parentStart + 300_000 - 1 }] },
    parentSignal: { time: parentStart }, childSignal: { index: 0, triggerPrice: 100 },
    interval: "15m", options: { mainWaveStage: "active" },
  };
  return { engine, state, spec };
}

test("an unexecutable current parent skips historical replay and remains null", () => {
  const h = fakeHarness({ status: "filtered", crossedLevel: true, reasons: ["test rejection"] });
  assert.equal(preflightRebuild(h.engine, h.spec), null);
  assert.equal(h.state.analyses, 0);
  assert.equal(rebuild(h.engine, h.spec), null);
  assert.equal(h.state.analyses, 1);
});

test("a possible buy replays lifecycle inheritance even when its raw geometry is weak", () => {
  const h = fakeHarness({ status: "buy", crossedLevel: true, foundationTypes: ["relaunch"], structureQuality: 0.2 }, {
    lifecycle: () => ({ reason: "", retryMaturity: true, inheritStructureContext: {
      structureQuality: 0.95, foundationTypes: ["relaunch", "triangle"], pattern: "inherited prior geometry",
    } }),
  });
  const actual = preflightRebuild(h.engine, h.spec);
  assert.ok(actual);
  assert.equal(h.state.analyses, 1);
  assert.equal(h.state.lifecycles, 1);
  assert.equal(actual.signal.structureQuality, 0.95);
  assert.equal(actual.signal.pattern, "inherited prior geometry");
  assert.deepEqual(structuredClone(actual), structuredClone(rebuild(h.engine, h.spec)));
});

test("a possible buy still permits secondary-breakout finalization to remove the signal", () => {
  const h = fakeHarness({ status: "buy", crossedLevel: true }, {
    secondary: (items) => [{ index: items[0].index, triggerPrice: items[0].triggerPrice }],
  });
  assert.equal(preflightRebuild(h.engine, h.spec), null);
  assert.equal(h.state.analyses, 1);
  assert.equal(h.state.finalizers, 1);
});

test("preconfirmation and crossed pending candidates follow the original selection rules", () => {
  for (const [evaluation, allowPreconfirmedParent, shouldReplay] of [
    [{ status: "pending", crossedLevel: false }, false, false],
    [{ status: "pending", crossedLevel: false }, true, true],
    [{ status: "pending", crossedLevel: false, consolidationBars: 31 }, true, false],
    [{ status: "pending", crossedLevel: false, structureQuality: 0.64 }, true, false],
    [{ status: "pending", crossedLevel: false, foundationTypes: ["relaunch"] }, true, false],
    [{ status: "pending", crossedLevel: true, foundationTypes: ["relaunch"] }, false, true],
  ]) {
    const h = fakeHarness(evaluation);
    h.spec.options.allowPreconfirmedParent = allowPreconfirmedParent;
    const actual = preflightRebuild(h.engine, h.spec);
    assert.equal(h.state.analyses, Number(shouldReplay));
    assert.deepEqual(structuredClone(actual), structuredClone(rebuild(h.engine, h.spec)));
  }
});

test("trigger-gap rejection preserves boundary, nonfinite and seed-independent semantics", () => {
  for (const [triggerPrice, shouldReplay] of [[101, true], [102, false], [NaN, true]]) {
    const h = fakeHarness({ status: "buy", crossedLevel: true, triggerPrice });
    // The full-history seed only chooses parentStart. Its price does not constrain
    // the candidates rebuilt at the current cutoff.
    h.spec.parentSignal.triggerPrice = 99999;
    const actual = preflightRebuild(h.engine, h.spec);
    assert.equal(h.state.analyses, Number(shouldReplay));
    assert.deepEqual(structuredClone(actual), structuredClone(rebuild(h.engine, h.spec)));
  }
});

const trbFiles = {
  child: path.join(root, ".runtime-cache", "dragon-wave-candles", "392075545b7130c29831dba0453145512adba8e2445012d41d4070eecb0e4caa.json.gz"),
  "1h": path.join(root, ".runtime-cache", "dragon-wave-candles", "cb0ecc5a10f2479433293cef623b314f63c4d9cf13146bee642a9ebeef5cee5f.json.gz"),
  "15m": path.join(root, ".runtime-cache", "dragon-wave-candles", "0a6c1dbe595f07233614d56f96d91e93648606ec8483dd07fa19aa2dd1027c09.json.gz"),
};
const baselinePath = path.join(root, ".runtime-cache", "performance-baseline-20260908", "dragon-wave-engine.js");
test("production context preflight returns null, preserves real TRB parents and reuses each historical step", {
  skip: ![trbFiles.child, trbFiles["1h"]].every((file) => fs.existsSync(file)) && "local TRB source snapshots are unavailable",
}, (t) => {
  const readRows = (file) => JSON.parse(zlib.gunzipSync(fs.readFileSync(file))).candles;
  const parent = { candles: readRows(trbFiles["1h"]) };
  const lower = { candles: readRows(trbFiles.child) };
  const specs = [
    [Date.parse("2023-09-05T08:00:00Z"), 0, 17],
    [1693227600000, 3_300_000, 13.900020022129013],
    [1693227600000, 3_000_000, 13.900020022129013],
    [1693227600000, 2_700_000, 13.900020022129013],
    [1693263600000, 3_300_000, 13.495350000000002],
  ].map(([time, offset, triggerPrice]) => {
    const index = lower.candles.findIndex((row) => row.time === time + offset);
    assert.ok(index >= 0, "every factual child cutoff exists in the uncapped source history");
    return { parent, lower, parentSignal: { time }, childSignal: { index, triggerPrice }, interval: "1h",
      options: { mainWaveStage: "active", allowPreconfirmedParent: true } };
  });
  const steps = [];
  const analyses = [];
  const preflights = [];
  const outputs = [];
  const marks = [];
  let engine;
  engine = loadEngine({
    process: (_candles, index, interval, rightEdge) => steps.push({ index, interval, rightEdge }),
    analyzed: (result) => analyses.push(result),
    preflight: (evaluations) => preflights.push(evaluations.map((item) => item.status)),
    // Only the dispatcher is intercepted. It enters the real applyContextGates
    // cache scope and calls real rebuild/normalization/detectors/evaluation.
    promote: (signal) => {
      outputs.push(rebuild(engine, signal.spec));
      marks.push({ steps: steps.length, analyses: analyses.length, preflights: preflights.length });
      return signal;
    },
  }, engineSource, true);
  engine.applyContextGates([{ interval: "1d", signals: specs.map((spec) => ({ spec })) }]);

  assert.equal(outputs[0], null);
  assert.equal(analyses[0], null, "the production analyzer itself returns null at a negative preflight");
  assert.equal(marks[0].steps, 0, "an early null does not process or advance the stable history");
  assert.ok(preflights[0].every((status) => status !== "buy" && status !== "pending"));
  assert.ok(outputs[1], "the known real 1h parent remains executable");
  assert.ok(outputs.at(-1), "the later real 1h parent remains executable");
  assert.ok(analyses.filter(Boolean).length >= 3, "distinct positive cutoffs must actually use the shared prefix");
  const historical = steps.filter((step) => !step.rightEdge);
  const lastIndex = parent.candles.findIndex((row) => row.time === specs.at(-1).parentSignal.time);
  assert.deepEqual(historical.map((step) => step.index), Array.from({ length: lastIndex - 30 }, (_, index) => index + 30),
    "every prior candle from index 30 is processed exactly once, with no lookback cap or duplicate prefix work");

  const priorSteps = steps.length;
  const priorPreflights = preflights.length;
  const direct = rebuild(engine, specs[1]);
  assert.deepEqual(structuredClone(direct), structuredClone(outputs[1]));
  assert.equal(preflights.length, priorPreflights, "public direct rebuild stays outside the private preflight scope");
  const directIndex = parent.candles.findIndex((row) => row.time === specs[1].parentSignal.time);
  assert.equal(steps.length - priorSteps, directIndex - 30 + 1,
    "public direct rebuild independently processes the complete history and terminal candle");
  t.diagnostic(`production early-null=${analyses.filter((value) => value === null).length}, non-null parents=${outputs.filter(Boolean).length}, stable steps=${historical.length}`);
});

test("TRB real-history cutoffs retain complete legacy results including non-null parents", {
  skip: ![...Object.values(trbFiles), baselinePath].every((file) => fs.existsSync(file)) && "local TRB source snapshots are unavailable",
}, () => {
  const readRows = (file) => JSON.parse(zlib.gunzipSync(fs.readFileSync(file))).candles;
  const lower = { candles: readRows(trbFiles.child) };
  // The real-data comparison does not need synthetic hooks. Native modules
  // avoid the substantial VM built-in overhead during historical replay.
  const baseline = require(baselinePath);
  const engine = require("../dragon-wave-engine.js");
  let nonNull = 0;
  let skipped = 0;
  for (const [interval, time, triggerPrice] of [
    ["1h", 1693227600000, 13.900020022129013],
    ["15m", 1693035900000, 10.833958300922998],
  ]) {
    const parent = { candles: readRows(trbFiles[interval]) };
    for (const offset of [0, INTERVAL_MS[interval] - 300_000]) {
      const index = lower.candles.findIndex((row) => row.time === time + offset);
      assert.ok(index >= 0, "the exact historical child cutoff must exist");
      const spec = {
        parent, lower, parentSignal: { time }, childSignal: { index, triggerPrice }, interval,
        options: { mainWaveStage: "active", allowPreconfirmedParent: true },
      };
      const observations = [];
      const expected = rebuild(baseline, spec);
      const actual = preflightRebuild(engine, spec, observations);
      // The reviewed strategy adds two diagnostic fields. These unaffected TRB
      // parents must retain their neutral values; do not strip them from actual
      // results or suppress any change to the underlying decision/geometry.
      if (expected?.signal) {
        expected.signal.postShockPlatformAboveEmaRatio = null;
        expected.signal.unorderedRepairStillActive = false;
      }
      assert.deepEqual(structuredClone(actual), structuredClone(expected), `${interval}, child offset ${offset}`);
      assert.equal(observations[0].historyBars, parent.candles.filter((row) => row.time < time).length + 1,
        "all earlier history is retained; the test does not impose a lookback cap");
      if (actual) nonNull += 1;
      if (!observations[0].possible) skipped += 1;
    }
  }
  assert.ok(nonNull >= 2, "equivalence must include actual non-null 1h and 15m parents");
  assert.ok(skipped > 0, "at least one real cutoff must exercise early rejection");
});
