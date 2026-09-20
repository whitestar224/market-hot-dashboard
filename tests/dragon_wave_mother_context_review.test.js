const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fixture = require("./fixtures/dragon_wave_mother_context_prefixes.json");
const Engine = require("../dragon-wave-engine.js");
const Data = require("../dragon-wave-data.js");

// These are single-candle, causal evaluations. They deliberately never call
// analyzeTimeframe, applyContextGates, a production cache writer or feedback.
function input(name) {
  const selected = fixture.cases.find((item) => item.name === name);
  assert.ok(selected, `fixture ${name} exists`);
  return {
    interval: selected.interval,
    candles: selected.rows.map((row) => Object.fromEntries(
      fixture.fields.map((field, index) => [field, row[index]]),
    )),
  };
}

function evaluateAt(candles, index, interval, options = {}) {
  const indicators = {
    atr: Engine.atr(candles, 14),
    ema90: Engine.ema(candles.map((row) => row.close), 90),
  };
  const candidates = Engine.findCandidates(candles, index, indicators, {
    interval,
    rightEdge: false,
  });
  const evaluations = candidates.map((candidate) => Engine.evaluateCandidate(
    candles, index, candidate, indicators, interval, {
      preselectedLeader: true,
      mainWaveStage: "active",
      mainWaveContextSource: "leader-default-main-wave",
      ...options,
    },
  ));
  return { candidates, indicators, evaluations };
}

function evaluate(name, transform = (row) => row, options = {}) {
  const selected = input(name);
  const candles = selected.candles.map(transform);
  return {
    ...selected,
    candles,
    ...evaluateAt(candles, candles.length - 1, selected.interval, options),
  };
}

function firstBase(result) {
  const candidate = result.candidates.find((item) => item.foundationTypes.includes("base"));
  assert.ok(candidate, "a local base was detected, rather than suppressing the detector");
  return candidate;
}

function motherOf(result) {
  const candidate = firstBase(result);
  return Engine.assessMotherStructureNoise(
    result.candles,
    result.candles.length - 1,
    candidate.level,
    result.indicators.atr.at(-2),
    { interval: result.interval, consolidationBars: candidate.consolidationBars },
  );
}

test("mother context fixtures distinguish a rebuilt higher-frame edge from a deep internal fragment", () => {
  const rebuilt = evaluate("higher-frame-rebuilt-platform");
  const internal = evaluate("unordered-mother-internal-platform");
  const rebuiltMother = motherOf(rebuilt);
  const internalMother = motherOf(internal);

  assert.equal(rebuiltMother.mode, "shock-formed-mother-box");
  assert.equal(rebuiltMother.risky, true, "the old shock is real and must not be erased from diagnostics");
  assert.ok(firstBase(rebuilt).structureStartIndex > rebuiltMother.shockLowIndex);
  assert.ok(rebuiltMother.position > 0.84);
  assert.ok(rebuiltMother.localShare > 0.4);
  assert.ok(firstBase(rebuilt).level < rebuiltMother.motherHigh,
    "the positive fixture does not break every historical high");

  assert.equal(internalMother.mode, "unordered-mother-box");
  assert.equal(internalMother.risky, true);
  assert.ok(internalMother.position < 0.7);
  assert.ok(internalMother.headroomAtr > 10);
  assert.ok(internalMother.localShare < 0.2);
});

test("a mature 1h structure rebuilt after the shock low remains native despite quiet prebreak volume", () => {
  const result = evaluate("higher-frame-rebuilt-platform");
  const accepted = result.evaluations.find((item) => item.status === "buy");
  assert.ok(accepted,
    `the independently rebuilt hourly platform must remain native: ${JSON.stringify(result.evaluations.map((item) => item.reasons))}`);
  assert.equal(accepted.motherStructureNoise, false);
  assert.equal(accepted.insideMotherBase, false);
  assert.equal(accepted.outerEdgeConfirmed, true);
  assert.equal(accepted.oneHourPlatformPivotReady, true);
  assert.ok(accepted.relativeVolume < 0.5, "low volume is intentionally part of this positive example");
  assert.equal(accepted.manualOverride, undefined);
});

test("default leader main-wave context cannot authorize a tight fragment inside an unordered mother box", () => {
  for (const stage of ["active", "expected"]) {
    const result = evaluate("unordered-mother-internal-platform", undefined, { mainWaveStage: stage });
    assert.ok(result.evaluations.length > 0);
    assert.equal(result.evaluations.some((item) => item.status === "buy"), false,
      `${stage} is market context, not proof that the current mother boundary is stale`);
    assert.ok(result.evaluations.some((item) => item.motherStructureNoise === true));
    assert.equal(result.evaluations.some((item) => item.mainWaveOldDeclinePressureException === true), false);
  }
});

test("mother-context decisions are structural, not symbol/date/absolute-price exceptions", () => {
  const shift = 37 * 24 * 60 * 60 * 1000;
  const transform = (row) => ({
    ...row,
    time: row.time + shift,
    closeTime: row.closeTime + shift,
    open: row.open * 3.25,
    high: row.high * 3.25,
    low: row.low * 3.25,
    close: row.close * 3.25,
    quoteVolume: row.quoteVolume * 3.25,
  });
  assert.ok(evaluate("higher-frame-rebuilt-platform", transform).evaluations.some((item) => item.status === "buy"));
  assert.equal(evaluate("unordered-mother-internal-platform", transform).evaluations.some((item) => item.status === "buy"), false);
});

test("single-candle mother-context evaluations do not use later extreme price paths", () => {
  const digest = (value) => crypto.createHash("sha256").update(JSON.stringify(value)).digest("hex");
  for (const name of ["higher-frame-rebuilt-platform", "unordered-mother-internal-platform"]) {
    const { candles, interval } = input(name);
    const index = candles.length - 1;
    const intervalMs = candles[index].time - candles[index - 1].time;
    const last = candles[index];
    const future = Array.from({ length: 24 }, (_, cursor) => ({
      ...last,
      time: last.time + intervalMs * (cursor + 1),
      closeTime: last.closeTime + intervalMs * (cursor + 1),
      open: last.close * 10,
      high: last.close * 20,
      low: last.close * 0.01,
      close: last.close * 0.02,
    }));
    assert.equal(
      digest(evaluateAt(candles, index, interval).evaluations),
      digest(evaluateAt([...candles, ...future], index, interval).evaluations),
      `${name} must not use the future to decide whether the old mother structure was repaired`,
    );
  }
});

function flaggedSignalWithStalePermit() {
  const result = evaluate("unordered-mother-internal-platform");
  const signal = {
    ...result.evaluations[0],
    status: "buy",
    reasons: [],
    score: 96,
    certaintyScore: 99,
    motherStructureNoise: false,
    unorderedRepairStillActive: true,
    executionHierarchy: {
      permit: true,
      tier: "core",
      primaryFoundation: "mother-platform-breakout",
      boosters: [],
      missing: [],
    },
  };
  return { result, signal };
}

test("an active unordered-repair flag overrides an earlier cached execution permit", () => {
  const { signal } = flaggedSignalWithStalePermit();
  assert.equal(Engine.assessExecutionHierarchy(signal).permit, false);
  assert.equal(Engine.isHighCertaintyEntry(signal), false,
    "a saved hierarchy permit cannot override a current explicit hard-risk flag");
});

test("context gating cannot keep or promote a signal carrying the active unordered-repair flag", () => {
  const { result, signal } = flaggedSignalWithStalePermit();
  for (const collection of ["signals", "pending", "rejected"]) {
    const frame = {
      interval: result.interval,
      candles: result.candles,
      indicators: result.indicators,
      signals: [],
      pending: [],
      rejected: [],
      stats: {},
      [collection]: [{ ...signal, status: collection === "pending" ? "pending" : collection === "rejected" ? "filtered" : "buy" }],
    };
    const [gated] = Engine.applyContextGates([frame], [], {
      preselectedLeader: true,
      mainWaveStage: "active",
    });
    assert.equal(gated.signals.length, 0, `${collection} must not become an executable signal`);
    assert.equal(gated.pending.length, 0, `${collection} must not remain positively preconfirmed`);
    assert.equal(gated.rejected[0]?.unorderedRepairStillActive, true,
      "the risk evidence must survive the context result");
  }
});

test("an existing mature triangle can still replace an old unordered listing window", () => {
  const intervalMs = 5 * 60_000;
  const rows = Data.parseRows("okx", require("./fixtures/piusdt_okx_5m_2025-02-22_1530.json"), intervalMs);
  // Same already-covered synthetic older history as dragon_wave_engine.test.js;
  // evaluate only its one established positive candle, not the historical loop.
  const start = rows[0].time - 300 * intervalMs;
  const earlier = Array.from({ length: 300 }, (_, index) => {
    const close = 0.735 + Math.sin(index * 0.17) * 0.075 + Math.sin(index * 0.041) * 0.035;
    const open = close + Math.sin(index * 0.33) * 0.008;
    const volume = 350_000 + 50_000 * Math.abs(Math.sin(index));
    return {
      time: start + index * intervalMs,
      closeTime: start + (index + 1) * intervalMs - 1,
      open,
      high: index === 50 ? 1.5 : Math.max(open, close) + 0.012,
      low: Math.min(open, close) - 0.012 - (index % 83 === 0 ? 0.045 : 0),
      close,
      volume,
      quoteVolume: volume * close,
    };
  });
  const target = Date.UTC(2025, 1, 22, 7, 30);
  const candles = earlier.concat(rows).filter((row) => row.time <= target);
  const { evaluations } = evaluateAt(candles, candles.length - 1, "5m");
  const mature = evaluations.find((item) => item.foundationTypes.includes("triangle") && item.foundationTypes.includes("base"));
  assert.ok(mature);
  assert.equal(mature.motherStructureMode, "unordered-mother-box");
  assert.equal(mature.unorderedRepairStillActive, false,
    "an independently mature triangle must not inherit a weak horizontal submodel's hard flag");
  assert.equal(mature.motherStructureNoise, false);
  assert.equal(mature.status, "buy");
});
