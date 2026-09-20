const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const enginePath = path.join(__dirname, "..", "dragon-wave-engine.js");
const engineSource = fs.readFileSync(enginePath, "utf8");
const START = 1_700_000_000_000;
const CHILD_MS = 300_000;
const PARENT_MS = CHILD_MS * 3;

function makeRows(parentCount = 80, flat = false) {
  const child = Array.from({ length: parentCount * 3 }, (_, index) => {
    const close = flat ? 100 : 100 + Math.sin(index * 0.31) * 0.7;
    return {
      time: START + index * CHILD_MS,
      closeTime: START + (index + 1) * CHILD_MS - 1,
      open: close - (flat ? 0 : 0.1), high: close + 0.2, low: close - 0.2,
      close, volume: 100, quoteVolume: close * 100, takerBuyVolume: 60, tradeCount: 1,
    };
  });
  const parent = Array.from({ length: parentCount }, (_, index) => {
    const inside = child.slice(index * 3, index * 3 + 3);
    return {
      time: inside[0].time, closeTime: inside.at(-1).closeTime, open: inside[0].open,
      high: Math.max(...inside.map((row) => row.high)), low: Math.min(...inside.map((row) => row.low)),
      close: inside.at(-1).close,
      volume: inside.reduce((sum, row) => sum + row.volume, 0),
      quoteVolume: inside.reduce((sum, row) => sum + row.quoteVolume, 0),
      takerBuyVolume: inside.reduce((sum, row) => sum + row.takerBuyVolume, 0),
      tradeCount: inside.reduce((sum, row) => sum + row.tradeCount, 0),
    };
  });
  return { parent, child };
}

function loadEngine({ replay = true, prefilter = false, noCandidates = false, failAt = null } = {}) {
  const steps = [];
  const analyses = [];
  const rebuilt = [];
  let engine;
  let source = engineSource;
  const intercept = (signature, body) => {
    assert.ok(source.includes(signature), signature);
    source = source.replace(signature, `${signature}\n${body}`);
  };
  if (!replay) {
    source = source.replace(
      "function causalReplayFor(parentCandles, parentInterval, mainWaveStage) {",
      "function causalReplayFor(parentCandles, parentInterval, mainWaveStage) { return null;",
    );
  }
  if (!prefilter) {
    // Keep full private analysis output observable in the replay-equivalence
    // tests. Separate tests below exercise the production early-null path.
    source = source.replace(
      "function hasPotentialCausalParentEntry(evaluations) {",
      "function hasPotentialCausalParentEntry(evaluations) { return true;",
    );
  }
  intercept("function findCandidates(candles, index, indicators, options = {}) {",
    "return __hooks.findCandidates(candles, index, indicators, options);");
  intercept("function evaluateCandidate(candles, index, candidate, indicators, interval, options = {}) {",
    "return __hooks.evaluate(candles, index, candidate, indicators, interval, options);");
  intercept("function structureLifecycleDecision(signals, evaluation, candles, index, atrValue) {",
    "return __hooks.lifecycle(signals, evaluation);");
  intercept("function buildSecondaryBreakoutHints(items, candles, indicators, interval, secondaryItems = items) {",
    "return __hooks.secondary(items, candles);");
  intercept("function assessExecutionHierarchy(signal) {", "return { permit: true };");
  intercept("function promoteLowerFrameIgnitionToParent(byInterval, result, signal, preselectedLeader, mainWaveStage) {",
    "return __hooks.promote(signal);");
  const processSignature = "function processAnalysisCandle(state, candles, index, indicators, interval, options, rightEdge) {";
  if (source.includes(processSignature)) {
    intercept(processSignature, "__hooks.observeStep?.(state, candles, index, rightEdge);");
    intercept("function finalizeAnalysis(state, candles, closes, indicators, interval) {",
      "__hooks.beforeFinalize?.(state);");
  }
  source = source.replace("return Object.freeze({", `
    const originalAnalyzeForTest = analyzeTimeframe;
    analyzeTimeframe = function observedAnalyze(rows, options) {
      const result = originalAnalyzeForTest(rows, options);
      __hooks.analyzed(result);
      return result;
    };
    return Object.freeze({`);
  const hooks = {
    findCandidates(candles, index, _indicators, options) {
      steps.push({ index, rightEdge: options.rightEdge, length: candles.length });
      if (failAt === index) throw new Error("injected replay failure");
      return noCandidates ? [] : [{
        crossedLevel: !options.rightEdge || index % 3 === 0,
        rightEdge: options.rightEdge,
      }];
    },
    evaluate(candles, index, candidate, indicators, interval, options) {
      return {
        id: `${interval}-${candles[index].time}`,
        interval, index, time: candles[index].time, decisionTime: candles[index].closeTime,
        status: candidate.crossedLevel ? "buy" : "pending", crossedLevel: candidate.crossedLevel,
        level: 100, triggerPrice: 100, price: 100,
        score: 85, certaintyScore: 85, structureQuality: 0.8,
        consolidationBars: 40, foundationTypes: ["base"], auxiliaryTypes: [], confluence: ["base"],
        reasons: [], evidence: [`rightEdge=${candidate.rightEdge}`, String(options.mainWaveStage)],
        atrAtDecision: indicators.atr[index - 1],
      };
    },
    lifecycle(signals, evaluation) {
      return {
        reason: "", retryMaturity: false,
        replacePriorId: evaluation.index % 4 === 0 ? signals.at(-1)?.id : undefined,
      };
    },
    secondary(items, candles) {
      // The real finalizer writes these three fields onto the historical primary
      // signal. A shorter cutoff must never inherit a previous branch's writes.
      if (items.length && candles.at(-1).tradeCount === 2) {
        items[0].secondaryDirectCandleExpansion = true;
        items[0].secondaryReferenceHigh = 123;
        items[0].secondaryWashBars = 2;
      }
      return [];
    },
    promote(signal) {
      const spec = signal.spec;
      rebuilt.push(engine.rebuildCausalParentAtChild(
        { candles: spec.parent }, { candles: spec.child }, { time: spec.parentStart },
        { index: spec.childIndex, triggerPrice: 100 }, spec.interval || "15m",
        { mainWaveStage: spec.mainWaveStage || "active", allowPreconfirmedParent: true },
      ));
      return signal;
    },
    analyzed(result) { analyses.push(result); },
  };
  const sandbox = { module: { exports: {} }, require: () => ({}), __hooks: hooks };
  vm.runInNewContext(source, sandbox, { filename: enginePath });
  engine = sandbox.module.exports;
  const gate = (specs) => engine.applyContextGates([{
    interval: "1d", signals: specs.map((spec) => ({ spec })),
  }]);
  return { engine, steps, analyses, rebuilt, gate, hooks };
}

function spec(data, parentIndex, childOffset = 0, extra = {}) {
  return {
    ...data, parentStart: data.parent[parentIndex].time,
    childIndex: parentIndex * 3 + childOffset, ...extra,
  };
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

test("growing causal prefixes evaluate each stable candle once and reproduce all full-analysis fields", () => {
  const data = makeRows();
  const specs = [[35, 0], [35, 1], [35, 2], [36, 0], [38, 0], [38, 1], [40, 0]]
    .map(([index, offset]) => spec(data, index, offset));
  const optimized = loadEngine();
  const reference = loadEngine({ replay: false });
  optimized.gate(specs);
  reference.gate(specs);
  assert.deepEqual(plain(optimized.analyses), plain(reference.analyses));
  assert.deepEqual(plain(optimized.rebuilt), plain(reference.rebuilt));
  assert.deepEqual(optimized.steps.filter((step) => !step.rightEdge).map((step) => step.index),
    Array.from({ length: 10 }, (_, index) => index + 30));
  assert.equal(optimized.steps.filter((step) => step.rightEdge).length, specs.length);
  assert.equal(optimized.steps.length, 17);
  assert.equal(reference.steps.length, 54);
});

test("backward cutoffs and parent times do not inherit terminal pending, replacements or finalizer writes", () => {
  const data = makeRows();
  const specs = [[35, 1], [35, 0], [36, 1], [34, 2], [37, 0], [35, 2]]
    .map(([index, offset]) => spec(data, index, offset));
  const optimized = loadEngine();
  const reference = loadEngine({ replay: false });
  optimized.gate(specs);
  reference.gate(specs);
  assert.deepEqual(plain(optimized.analyses), plain(reference.analyses));
  assert.deepEqual(plain(optimized.rebuilt), plain(reference.rebuilt));
  assert.equal(optimized.analyses[0].signals[0].secondaryReferenceHigh, 123);
  assert.equal(optimized.analyses[1].signals[0].secondaryReferenceHigh, undefined);
  assert.ok(optimized.analyses[0].pending.length > 0);
});

test("replay states are separated by source identity and semantic options", () => {
  const data = makeRows();
  const copied = { parent: data.parent.map((row) => ({ ...row })), child: data.child };
  copied.parent[0].close = 10;
  const specs = [
    spec(data, 35), spec(data, 36, 0, { mainWaveStage: "expected" }),
    spec(copied, 37), spec(data, 38),
  ];
  const optimized = loadEngine();
  const reference = loadEngine({ replay: false });
  optimized.gate(specs);
  reference.gate(specs);
  assert.deepEqual(plain(optimized.analyses), plain(reference.analyses));
});

test("input revisions restart stable replay instead of retaining stale indicators or signals", () => {
  const data = makeRows();
  const run = (h) => {
    const promote = h.hooks.promote;
    let calls = 0;
    h.hooks.promote = (signal) => {
      if (calls++ === 1) data.parent[0].close = 17;
      return promote(signal);
    };
    data.parent[0].close = 100;
    h.gate([spec(data, 35), spec(data, 36)]);
  };
  const optimized = loadEngine();
  const reference = loadEngine({ replay: false });
  run(optimized);
  run(reference);
  assert.deepEqual(plain(optimized.analyses), plain(reference.analyses));
});

test("short histories and terminal long-consolidation audits retain complete full-replay output", () => {
  const short = makeRows(35, true);
  const long = makeRows(725, true);
  const specs = [spec(short, 28), spec(short, 29), spec(short, 30),
    spec(long, 718), spec(long, 719), spec(long, 720), spec(long, 722)];
  const optimized = loadEngine({ noCandidates: true });
  const reference = loadEngine({ replay: false, noCandidates: true });
  optimized.gate(specs);
  reference.gate(specs);
  assert.deepEqual(plain(optimized.analyses), plain(reference.analyses));
  assert.ok(optimized.analyses.at(-1).rejected.some((item) => item.auditOnly));
});

test("standalone public analyses and subsequent context calls always start fresh", () => {
  const data = makeRows();
  const h = loadEngine();
  h.gate([spec(data, 35), spec(data, 36)]);
  h.steps.length = 0;
  h.engine.analyzeTimeframe(data.parent.slice(0, 40), { interval: "15m", now: 1_800_000_000_000 });
  assert.deepEqual(h.steps.map((step) => step.index), Array.from({ length: 10 }, (_, index) => 30 + index));
  h.steps.length = 0;
  h.gate([spec(data, 35)]);
  assert.deepEqual(h.steps.map((step) => step.index).sort((a, b) => a - b), [30, 31, 32, 33, 34, 35]);
});

test("an exception clears the private replay request before the next public analysis", () => {
  const data = makeRows();
  const h = loadEngine({ failAt: 33 });
  assert.throws(() => h.gate([spec(data, 35)]), /injected replay failure/);
  h.hooks.findCandidates = () => [];
  const actual = h.engine.analyzeTimeframe(data.parent.slice(0, 40), { interval: "15m", now: 1_800_000_000_000 });
  const reference = loadEngine({ noCandidates: true }).engine.analyzeTimeframe(
    data.parent.slice(0, 40), { interval: "15m", now: 1_800_000_000_000 },
  );
  assert.deepEqual(plain(actual), plain(reference));
});

test("terminal branches isolate signal annotations while retaining aliases inside the branch", () => {
  const data = makeRows();
  const h = loadEngine();
  let stableSignal;
  const branches = [];
  h.hooks.observeStep = (state, _candles, index, rightEdge) => {
    if (!rightEdge && index === 31) stableSignal = state.signals[0];
  };
  h.hooks.beforeFinalize = (state) => {
    const first = state.signals[0];
    assert.notEqual(first, stableSignal);
    assert.equal(first, state.crossedEvaluations.find((item) => item.id === first.id));
    branches.push(first);
  };
  h.gate([spec(data, 35, 1), spec(data, 35, 0)]);
  assert.equal(branches.length, 2);
  assert.notEqual(branches[0], branches[1]);
  assert.equal(branches[0].secondaryReferenceHigh, 123);
  assert.equal(branches[1].secondaryReferenceHigh, undefined);
  assert.equal(stableSignal.secondaryReferenceHigh, undefined);
});

test("only four forward replay states are retained and an evicted source safely replays again", () => {
  const sources = Array.from({ length: 5 }, () => makeRows());
  const specs = [...sources.map((data) => spec(data, 35)), spec(sources[0], 36)];
  const h = loadEngine();
  const reference = loadEngine({ replay: false });
  h.gate(specs);
  reference.gate(specs);
  assert.deepEqual(plain(h.analyses), plain(reference.analyses));
  assert.deepEqual(h.steps.slice(-7).map((step) => step.index).sort((a, b) => a - b), [30, 31, 32, 33, 34, 35, 36]);
  assert.equal(h.steps.length, 37);
});

test("invalid partial candles use the full analyzer's earlier right edge before replay resumes", () => {
  const data = makeRows();
  data.child[35 * 3].open = 0;
  const specs = [spec(data, 34, 2), spec(data, 35), spec(data, 36)];
  const h = loadEngine();
  const reference = loadEngine({ replay: false });
  h.gate(specs);
  reference.gate(specs);
  assert.deepEqual(plain(h.analyses), plain(reference.analyses));
  assert.deepEqual(plain(h.rebuilt), plain(reference.rebuilt));
  assert.equal(h.analyses[1].candles.at(-1).time, data.parent[34].time);
});

test("changing unseen parent and child candles cannot change earlier causal replay results", () => {
  const original = makeRows();
  const changed = { parent: original.parent.map((row) => ({ ...row })), child: original.child.map((row) => ({ ...row })) };
  for (const row of changed.parent.slice(36)) {
    row.high = 10_000;
    row.low = 1;
    row.close = 9_000;
  }
  for (const row of changed.child.slice(36 * 3 + 1)) {
    row.high = 10_000;
    row.low = 1;
    row.close = 9_000;
    row.volume = 1e9;
  }
  const first = loadEngine();
  const second = loadEngine();
  first.gate([spec(original, 35, 1), spec(original, 36)]);
  second.gate([spec(changed, 35, 1), spec(changed, 36)]);
  assert.deepEqual(plain(first.analyses), plain(second.analyses));
});

test("a filtered current parent returns null without advancing the stable prefix", () => {
  const data = makeRows();
  const h = loadEngine({ prefilter: true });
  const reference = loadEngine({ replay: false });
  for (const engine of [h, reference]) {
    const evaluate = engine.hooks.evaluate;
    engine.hooks.evaluate = (...args) => {
      const result = evaluate(...args);
      if (args[1] === 38 && args[2].rightEdge) result.status = "filtered";
      return result;
    };
  }
  const specs = [spec(data, 35), spec(data, 38), spec(data, 36)];
  h.gate(specs);
  reference.gate(specs);
  assert.deepEqual(plain(h.rebuilt), plain(reference.rebuilt));
  assert.equal(h.analyses[1], null);
  assert.deepEqual(h.steps.filter((step) => !step.rightEdge).map((step) => step.index), [30, 31, 32, 33, 34, 35]);
  assert.equal(h.steps.filter((step) => step.rightEdge).length, specs.length);
  assert.deepEqual(plain(h.analyses[2]), plain(reference.analyses[2]));
});

test("any pending candidate still receives historical lifecycle and finalization before selection", () => {
  const data = makeRows();
  const h = loadEngine({ prefilter: true });
  const evaluate = h.hooks.evaluate;
  h.hooks.evaluate = (...args) => {
    const result = evaluate(...args);
    if (args[2].rightEdge) {
      result.status = "pending";
      result.crossedLevel = false;
      result.foundationTypes = [];
      result.structureQuality = 0;
    }
    return result;
  };
  h.gate([spec(data, 35)]);
  assert.equal(h.rebuilt[0], null);
  assert.ok(h.analyses[0], "even an unselectable pending item does not trigger the conservative early-null gate");
  assert.deepEqual(h.steps.filter((step) => !step.rightEdge).map((step) => step.index), [30, 31, 32, 33, 34]);
});
