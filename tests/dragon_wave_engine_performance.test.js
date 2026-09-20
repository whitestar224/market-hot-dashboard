const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const enginePath = path.join(__dirname, "..", "dragon-wave-engine.js");
const engineSource = fs.readFileSync(enginePath, "utf8");

// Instrument the isolated test module, without adding production test switches or
// running the expensive market detector when only cache lifetime is under test.
function loadEngine(hooks = {}, vision = {}) {
  let source = engineSource;
  const intercept = (signature, body) => {
    assert.ok(source.includes(signature), signature);
    source = source.replace(signature, `${signature}\n${body}`);
  };
  intercept("function ema(values, period) {", "__hooks.ema?.(values, period);");
  intercept("function findCandidates(candles, index, indicators, options = {}) {",
    "if (__hooks.findCandidates) return __hooks.findCandidates(candles, index, indicators, options);");
  intercept("function analyzeTimeframe(rows, options = {}) {",
    "if (__hooks.analyze) return __hooks.analyze(rows, options);");
  intercept("function promoteLowerFrameIgnitionToParent(byInterval, result, signal, preselectedLeader, mainWaveStage) {",
    "if (__hooks.promote) return __hooks.promote(byInterval, result, signal, preselectedLeader, mainWaveStage);");
  intercept("function assessExecutionHierarchy(signal) {",
    "if (__hooks.hierarchy) return __hooks.hierarchy(signal);");
  source = source.replace("return Object.freeze({", `
    if (__hooks.causalAnalysis) {
      const originalCausalAnalysis = analyzeCausalParent;
      analyzeCausalParent = (...args) => {
        const result = originalCausalAnalysis(...args);
        __hooks.causalAnalysis(result);
        return result;
      };
    }
    return Object.freeze({`);
  const sandbox = {
    module: { exports: {} },
    require: (name) => {
      assert.equal(name, "./dragon-wave-vision.js");
      return vision;
    },
    __hooks: hooks,
  };
  vm.runInNewContext(source, sandbox, { filename: enginePath });
  return sandbox.module.exports;
}

function rows(count = 80, ms = 300_000) {
  const start = 1_700_000_000_000;
  return Array.from({ length: count }, (_, index) => {
    const center = 100 + index * 0.12 + Math.sin(index * 0.8);
    return {
      time: start + index * ms,
      closeTime: start + (index + 1) * ms - 1,
      open: center, high: center + 0.8, low: center - 0.6,
      close: center + 0.2, volume: 100, quoteVolume: 100 * center,
    };
  });
}

test("pre-structure checks reuse the analysis EMA and recompute after public input mutation", () => {
  let ema90Calls = 0;
  let captured;
  let engine;
  const observations = [];
  const hooks = {
    ema: (_values, period) => { if (period === 90) ema90Calls += 1; },
    findCandidates: (candles, index, _indicators, options) => {
      captured = candles;
      observations.push(engine.assessPreStructureContext(candles, index, index - 8, 1, options));
      engine.assessPreStructureContext(candles, index, index - 8, 1, options);
      return [];
    },
  };
  engine = loadEngine(hooks);
  engine.analyzeTimeframe(rows(), { interval: "5m", now: 1_800_000_000_000 });
  assert.equal(ema90Calls, 1, "one full EMA, regardless of repeated structure checks");
  const before = engine.assessPreStructureContext(captured, 79, 71, 1, { interval: "5m" });
  assert.deepEqual(before, observations.at(-1));
  captured[0].close = 10_000;
  const after = engine.assessPreStructureContext(captured, 79, 71, 1, { interval: "5m" });
  const fresh = engine.assessPreStructureContext(captured.map((row) => ({ ...row })), 79, 71, 1, { interval: "5m" });
  assert.deepEqual(after, fresh);
  assert.notDeepEqual(after, before);
  assert.equal(ema90Calls, 4, "public calls do not retain the completed analysis cache");
});

test("an analysis exception releases its EMA scope", () => {
  let captured;
  let ema90Calls = 0;
  const engine = loadEngine({
    ema: (_values, period) => { if (period === 90) ema90Calls += 1; },
    findCandidates: (candles) => { captured = candles; throw new Error("detector failure"); },
  });
  assert.throws(() => engine.analyzeTimeframe(rows(), { now: 1_800_000_000_000 }), /detector failure/);
  engine.assessPreStructureContext(captured, 70, 60, 1);
  assert.equal(ema90Calls, 2);
});

test("nested analyses restore the enclosing EMA scope", () => {
  let ema90Calls = 0;
  let entered = false;
  let engine;
  engine = loadEngine({
    ema: (_values, period) => { if (period === 90) ema90Calls += 1; },
    findCandidates: (candles, index) => {
      if (!entered) {
        entered = true;
        engine.analyzeTimeframe(rows(36), { now: 1_800_000_000_000 });
      }
      engine.assessPreStructureContext(candles, index, index - 8, 1);
      return [];
    },
  });
  engine.analyzeTimeframe(rows(40), { now: 1_800_000_000_000 });
  assert.equal(ema90Calls, 2, "one EMA for each invocation, including after the inner scope ends");
});

test("one analysis reuses its visual builder and preserves the standalone evaluation API", () => {
  let builders = 0;
  let signatures = 0;
  let legacyCalls = 0;
  let captured;
  const vision = {
    createVisualSignatureBuilder: (candles, ema90) => {
      builders += 1;
      captured = { candles, ema90 };
      return (index, options) => {
        signatures += 1;
        assert.equal(options.interval, "5m");
        assert.ok(index >= 30);
        return { index, triggerPrice: options.triggerPrice };
      };
    },
    buildVisualSignature: (_candles, index, options) => {
      legacyCalls += 1;
      return { index, triggerPrice: options.triggerPrice };
    },
  };
  const engine = loadEngine({}, vision);
  const result = engine.analyzeTimeframe(rows(48), { interval: "5m", now: 1_800_000_000_000 });
  assert.equal(builders, 1);
  assert.ok(signatures > 0, "the fixture must exercise real candidate evaluation");
  assert.equal(legacyCalls, 0);
  const index = result.candles.length - 1;
  const candidates = engine.findCandidates(result.candles, index, result.indicators, { interval: "5m", rightEdge: true });
  assert.ok(candidates.length > 0);
  engine.evaluateCandidate(result.candles, index, candidates[0], result.indicators, "5m");
  assert.equal(legacyCalls, 1, "standalone evaluation uses its public fallback after scope cleanup");
  assert.equal(captured.candles, result.candles);
  assert.equal(captured.ema90, result.indicators.ema90);
  const legacyVisionEngine = loadEngine({}, { buildVisualSignature: vision.buildVisualSignature });
  const reference = legacyVisionEngine.analyzeTimeframe(rows(48), { interval: "5m", now: 1_800_000_000_000 });
  assert.deepEqual(JSON.parse(JSON.stringify(result)), JSON.parse(JSON.stringify(reference)));
});

function rebuildHarness(observeCausalAnalysis) {
  const parentCandles = rows(2, 3_600_000);
  const childCandles = rows(3);
  const base = {
    parent: { candles: parentCandles },
    lower: { candles: childCandles },
    parentSignal: { time: parentCandles[0].time },
    childSignal: { index: 1, triggerPrice: 100 },
    interval: "1h",
    options: { mainWaveStage: "active" },
  };
  const calls = [];
  const returned = [];
  let engine;
  const hooks = {
    causalAnalysis: observeCausalAnalysis,
    hierarchy: () => ({ permit: true }),
    analyze: (candles, options) => {
      calls.push({ candles, options });
      const time = candles.at(-1).time;
      const signal = (triggerPrice, status = "buy") => ({
        time, triggerPrice, status, crossedLevel: status === "buy",
        foundationTypes: ["base"], consolidationBars: 40, structureQuality: 0.8,
      });
      return { signals: [signal(100), signal(101)], pending: [signal(99.5, "pending")] };
    },
    promote: (_byInterval, _result, signal) => {
      returned.push(rebuild(signal.spec));
      return signal;
    },
  };
  engine = loadEngine(hooks);
  const rebuild = (spec) => engine.rebuildCausalParentAtChild(
    spec.parent, spec.lower, spec.parentSignal, spec.childSignal, spec.interval, spec.options,
  );
  const gate = (specs) => engine.applyContextGates([{
    interval: "1d", signals: specs.map((spec) => ({ spec })),
  }]);
  return { base, calls, returned, engine, hooks, rebuild, gate };
}

test("identical causal analyses are shared but child price and preconfirmation selection remain independent", () => {
  const h = rebuildHarness();
  const withPrice = (price, allowPreconfirmedParent = false) => ({
    ...h.base,
    childSignal: { ...h.base.childSignal, triggerPrice: price },
    options: { ...h.base.options, allowPreconfirmedParent },
  });
  h.gate([withPrice(100), withPrice(101), withPrice(99.5), withPrice(99.5, true)]);
  assert.equal(h.calls.length, 1);
  assert.deepEqual(h.returned.map((item) => item.signal.triggerPrice), [100, 101, 100, 99.5]);
  h.base.lower.candles[1].close = 123;
  h.gate([h.base]);
  assert.equal(h.calls.length, 2, "the next context call starts a new cache");
  assert.equal(h.calls.at(-1).candles.at(-1).close, 123);
  h.rebuild(h.base);
  h.rebuild(h.base);
  assert.equal(h.calls.length, 4, "direct public rebuild calls never share mutable input caches");
});

test("causal analysis keys distinguish arrays, interval, start, cutoff and main-wave stage", () => {
  const h = rebuildHarness();
  const variants = [
    h.base,
    { ...h.base, parent: { candles: h.base.parent.candles.slice() } },
    { ...h.base, lower: { candles: h.base.lower.candles.slice() } },
    { ...h.base, interval: "15m" },
    { ...h.base, parentSignal: { time: h.base.parentSignal.time - 1_000 } },
    { ...h.base, childSignal: { ...h.base.childSignal, index: 2 } },
    { ...h.base, options: { mainWaveStage: "expected" } },
  ];
  h.gate(variants.flatMap((spec) => [spec, spec]));
  assert.equal(h.calls.length, variants.length);
});

test("context exceptions release the causal analysis cache", () => {
  const h = rebuildHarness();
  const promote = h.hooks.promote;
  h.hooks.promote = (...args) => {
    promote(...args);
    throw new Error("context failure");
  };
  assert.throws(() => h.gate([h.base]), /context failure/);
  h.rebuild(h.base);
  h.rebuild(h.base);
  assert.equal(h.calls.length, 3);
});

test("nested context calls restore the enclosing causal analysis cache", () => {
  const h = rebuildHarness();
  const promote = h.hooks.promote;
  let nested = false;
  h.hooks.promote = (...args) => {
    const result = promote(...args);
    if (!nested) {
      nested = true;
      h.gate([h.base, h.base]);
    }
    return result;
  };
  h.gate([h.base, h.base]);
  assert.equal(h.calls.length, 2, "one analysis in each of the two isolated context scopes");
});

test("causal caches retain only current-parent candidates in their original order", () => {
  const analyses = [];
  const h = rebuildHarness((analysis) => analyses.push(analysis));
  const analyze = h.hooks.analyze;
  let fullResult;
  h.hooks.analyze = (...args) => {
    fullResult = analyze(...args);
    fullResult.signals.unshift({ time: h.base.parentSignal.time - 3_600_000, triggerPrice: 90 });
    fullResult.pending.unshift({ time: h.base.parentSignal.time - 3_600_000, triggerPrice: 91 });
    fullResult.candles = args[0];
    fullResult.indicators = { ema90: args[0].map((row) => row.close) };
    fullResult.rejected = [{ time: h.base.parentSignal.time, triggerPrice: 101.5 }];
    return fullResult;
  };
  h.gate([h.base, h.base]);
  assert.equal(analyses.length, 1);
  assert.deepEqual(Object.keys(analyses[0].causalResult).sort(), ["pending", "signals"]);
  assert.deepEqual(Array.from(analyses[0].causalResult.signals, (item) => item.triggerPrice), [100, 101]);
  assert.deepEqual(Array.from(analyses[0].causalResult.pending, (item) => item.triggerPrice), [99.5]);
  assert.equal(fullResult.signals.length, 3, "projection must not mutate the full analysis");
  assert.equal(fullResult.pending.length, 2);
});

test("a 32-entry FIFO cache recomputes evicted inputs without changing their results", () => {
  const h = rebuildHarness();
  h.base.lower.candles = rows(40, 60_000);
  const specs = Array.from({ length: 33 }, (_, index) => ({
    ...h.base, childSignal: { index, triggerPrice: 100 },
  }));
  // Reading entry 1 must not move it in FIFO order. Rebuilding evicted entry 0
  // then evicts entry 1; the latest initial entry remains available throughout.
  h.gate([...specs, specs[1], specs[0], specs[1], specs[32]]);
  assert.equal(h.calls.length, 35);
  assert.deepEqual(h.returned[34], h.returned[0]);
  assert.deepEqual(h.returned[35], h.returned[1]);
  assert.deepEqual(h.returned[36], h.returned[32]);
  assert.notEqual(h.returned[34].signal, h.returned[0].signal);
  assert.equal(h.returned[36].signal, h.returned[32].signal);
});
