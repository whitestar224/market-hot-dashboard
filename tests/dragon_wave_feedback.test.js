const test = require("node:test");
const assert = require("node:assert/strict");

const Feedback = require("../dragon-wave-feedback.js");
const Vision = require("../dragon-wave-vision.js");

function signal(overrides = {}) {
  return {
    id: "15m-1000-base",
    time: 1000,
    interval: "15m",
    pattern: "横盘起飞 + 突破前高",
    patternKey: "base",
    foundationTypes: ["base"],
    auxiliaryTypes: ["previousHigh"],
    confluence: ["base", "previousHigh"],
    status: "buy",
    price: 10.2,
    score: 80,
    certaintyScore: 82,
    evidence: ["母平台外沿"],
    reasons: [],
    index: 0,
    ...overrides,
  };
}

function record(decision, item, updatedAt = 10) {
  return recordFor("TUTUSDT", decision, item, updatedAt);
}

function recordFor(pair, decision, item, updatedAt = 10) {
  const key = Feedback.signalKey(pair, item);
  return {
    version: 1,
    updatedAt,
    records: {
      [key]: {
        key,
        decision,
        createdAt: 1,
        updatedAt,
        pair,
        interval: item.interval,
        venue: "Binance",
        signal: Feedback.snapshotSignal(item),
      },
    },
  };
}

function result({ signals = [], pending = [], rejected = [], candleTimes = [1000, 2000] } = {}) {
  return {
    interval: "15m",
    candles: candleTimes.map((time) => ({ time, open: 10, high: 11, low: 9, close: 10.5 })),
    indicators: { ema90: candleTimes.map(() => 9), atr: candleTimes.map(() => 1) },
    signals,
    pending,
    rejected,
    stats: {},
  };
}

function visualSignature(seed = "a") {
  const bit = seed === "a" ? "1" : "0";
  const inverse = bit === "1" ? "0" : "1";
  return {
    version: Vision.VERSION,
    model: "causal-kline-raster-v1",
    interval: "15m",
    selectedCandleTime: 1000,
    featureCutoffTime: 999,
    causality: "completed-candles-before-selected-index-only",
    windows: [40, 80, 160].map((span) => ({
      span,
      bars: span,
      width: 24,
      height: 16,
      wick: (bit + inverse).repeat(192),
      body: (bit.repeat(3) + inverse.repeat(3)).repeat(64),
      ema: (seed === "a" ? "6789" : "0123").repeat(6),
      volume: (seed === "a" ? "4567" : "fedc").repeat(6),
      closePath: Array.from({ length: 24 }, (_, index) => seed === "a" ? 450 + index * 5 : 900 - index * 20),
      rangePath: Array.from({ length: 24 }, (_, index) => seed === "a" ? 180 - index * 3 : 600 + index * 10),
      triggerRow: seed === "a" ? 4 : 14,
      stats: seed === "a"
        ? { drift: 110, rangeCompression: 120, envelopeCompression: 150, directionChangeRatio: 350 }
        : { drift: -700, rangeCompression: 900, envelopeCompression: 850, directionChangeRatio: 950 },
    })),
  };
}

function reviewedVisualRecord(pair, decision, grade, time, signature, updatedAt, structureTags = ["horizontalLaunch", "consolidationBreakout"]) {
  const item = signal({
    id: `${pair}-${time}`,
    time,
    visualSignature: signature,
    manualCertaintyGrade: grade,
    manualStructureTags: structureTags,
  });
  const key = Feedback.signalKey(pair, item);
  return {
    version: 1,
    updatedAt,
    records: {
      [key]: {
        key,
        decision,
        createdAt: updatedAt,
        updatedAt,
        pair,
        interval: "15m",
        certaintyGrade: grade,
        structureTags,
        signal: Feedback.snapshotSignal(item),
      },
    },
  };
}

test("latest updated record wins when browser, local SQLite and account documents merge", () => {
  const item = signal();
  const merged = Feedback.mergeDocuments(record("confirmed", item, 10), record("denied", item, 20));
  assert.equal(merged.records[Feedback.signalKey("TUTUSDT", item)].decision, "denied");
});

test("feedback snapshots retain the compact causal visual signature", () => {
  const visualSignature = {
    version: 1,
    model: "causal-kline-raster-v1",
    selectedCandleTime: 1000,
    featureCutoffTime: 999,
    causality: "completed-candles-before-selected-index-only",
    windows: [{ span: 40, wick: "01", body: "10", ema: "12", volume: "34", closePath: [1, 2], rangePath: [3, 4] }],
  };
  const snapshot = Feedback.snapshotSignal(signal({ visualSignature }));
  assert.deepEqual(snapshot.visualSignature, visualSignature);
});

test("cross-leader A+ visual prototypes restore only a safe V pre-confirmation, never an automatic B", () => {
  const positive = Feedback.mergeDocuments(
    reviewedVisualRecord("SPKUSDT", "confirmed", "A+", 10_000, visualSignature("a"), 10),
    reviewedVisualRecord("COWUSDT", "confirmed", "A+", 20_000, visualSignature("a"), 20),
    reviewedVisualRecord("TUTUSDT", "denied", "", 30_000, visualSignature("b"), 30),
  );
  const candidate = signal({
    status: "filtered",
    visualSignature: visualSignature("a"),
    reasons: ["母结构尚未成熟：盘整、压缩或贴线蓄力不足"],
  });
  const applied = Feedback.applyToResult(result({ rejected: [candidate] }), "HUSDT", positive);
  assert.equal(applied.signals.length, 0);
  assert.equal(applied.pending.length, 1, JSON.stringify(applied.rejected[0]?.visualLearning));
  assert.equal(applied.pending[0].visualPreconfirmed, true);
  assert.equal(applied.pending[0].status, "pending");
  assert.equal(applied.pending[0].visualLearning.positivePairCount, 2);
  assert.ok(applied.pending[0].visualLearning.positiveSimilarity >= 96);
  assert.ok(applied.pending[0].visualLearning.negativeSimilarity <= 55);
});

test("nearest confirmed visual examples pre-suggest their manually reviewed structure tags", () => {
  const document = Feedback.mergeDocuments(
    reviewedVisualRecord("HUSDT", "confirmed", "A+", 10_000, visualSignature("a"), 10, ["fallingWedge", "trendlineBreakout"]),
    reviewedVisualRecord("AKEUSDT", "confirmed", "A+", 20_000, visualSignature("a"), 20, ["fallingWedge", "trendlineBreakout"]),
    reviewedVisualRecord("TUTUSDT", "denied", "", 30_000, visualSignature("b"), 30, ["box"]),
  );
  const assessment = Feedback.visualLearningAssessment(signal({
    visualSignature: visualSignature("a"),
  }), "NEWUSDT", document);
  assert.ok(assessment.suggestedStructureTags.includes("fallingWedge"), JSON.stringify(assessment));
  assert.ok(assessment.suggestedStructureTags.includes("trendlineBreakout"), JSON.stringify(assessment));
  assert.ok(assessment.structureTagConfidence >= 80, JSON.stringify(assessment));
});

test("pending reviews never train the visual model and exact denial cannot be visually rescued", () => {
  const pendingOnly = reviewedVisualRecord("SPKUSDT", "pending", "A+", 10_000, visualSignature("a"), 10);
  const candidate = signal({ status: "filtered", visualSignature: visualSignature("a"), reasons: [] });
  const noLearning = Feedback.visualLearningAssessment(candidate, "HUSDT", pendingOnly);
  assert.equal(noLearning.positiveSampleCount, 0);
  assert.equal(noLearning.eligible, false);

  const exactDenied = Feedback.mergeDocuments(
    reviewedVisualRecord("SPKUSDT", "confirmed", "A+", 10_000, visualSignature("a"), 10),
    reviewedVisualRecord("COWUSDT", "confirmed", "A+", 20_000, visualSignature("a"), 20),
    reviewedVisualRecord("HUSDT", "denied", "", 1000, visualSignature("a"), 30),
  );
  const denied = Feedback.applyToResult(result({ rejected: [candidate] }), "HUSDT", exactDenied);
  assert.equal(denied.pending.length, 0);
  assert.equal(denied.rejected[0].manualDecision, "denied");
});

test("confirmed signal is restored even after a later strategy version omits it", () => {
  const item = signal({ time: 2000, index: 1 });
  const applied = Feedback.applyToResult(result(), "TUTUSDT", record("confirmed", item));
  assert.equal(applied.signals.length, 1);
  assert.equal(applied.signals[0].manualDecision, "confirmed");
  assert.equal(applied.signals[0].manualRestored, true);
  assert.equal(applied.signals[0].index, 1);
  assert.equal(applied.signals[0].strategyRegressionConflict, true);
  assert.equal(applied.signals[0].strategyCompatibilityStatus, "missing");
  assert.equal(applied.confirmedCompatibility.conflictCount, 1);
  assert.equal(applied.stats.confirmedRegressionConflictCount, 1);
});

test("a new strategy revision passes only when it natively re-hits every confirmed candle", () => {
  const item = signal();
  const document = record("confirmed", item);
  const raw = result({ signals: [item] });
  const audit = Feedback.assertConfirmedCompatibility(raw, "TUTUSDT", document);
  assert.equal(audit.passed, true);
  assert.equal(audit.confirmedInWindow, 1);
  assert.equal(audit.nativeBuyCount, 1);
  const applied = Feedback.applyToResult(raw, "TUTUSDT", document);
  assert.equal(applied.signals[0].strategyRegressionConflict, false);
  assert.equal(applied.signals[0].strategyCompatibilityStatus, "native-buy");
  assert.equal(applied.signals[0].manualRestored, undefined);
  assert.equal(applied.stats.confirmedRegressionConflictCount, 0);
});

test("the optimization envelope keeps confirmed buys as a subset and denied candles outside all native candidates", () => {
  const confirmed = signal({ time: 1000, id: "confirmed-1000" });
  const denied = signal({ time: 2000, id: "denied-2000" });
  const document = Feedback.mergeDocuments(
    record("confirmed", confirmed, 10),
    record("denied", denied, 20),
  );
  const raw = result({
    signals: [confirmed],
    rejected: [{ ...denied, status: "filtered", reasons: ["母结构内部无序波动"] }],
  });
  const envelope = Feedback.assertReviewCompatibility(raw, "TUTUSDT", document);
  assert.equal(envelope.policy, "confirmed-subset-and-denied-disjoint");
  assert.equal(envelope.confirmed.nativeBuyCount, 1);
  assert.equal(envelope.denied.excludedCount, 1);
});

test("a broadened strategy fails the release envelope when a permanently denied candle revives", () => {
  const denied = signal({ time: 1000, id: "denied-1000" });
  const document = record("denied", denied);
  const raw = result({ signals: [{ ...denied, pattern: "放宽后的盘整突破" }] });
  const audit = Feedback.auditDeniedCompatibility(raw, "TUTUSDT", document);
  assert.equal(audit.policy, "denied-buy-zero-revival-conflicts");
  assert.equal(audit.passed, false);
  assert.equal(audit.conflictCount, 1);
  assert.throws(
    () => Feedback.assertReviewCompatibility(raw, "TUTUSDT", document),
    /否定点复活为 native-buy/,
  );
  const applied = Feedback.applyToResult(raw, "TUTUSDT", document);
  assert.equal(applied.signals.length, 0, "永久否定仍必须从最终买点中移除");
  assert.equal(applied.stats.deniedRegressionConflictCount, 1, "不能用最终黑名单遮住原生逻辑变宽");
  assert.equal(applied.deniedCompatibility.conflicts[0].strategyStatus, "native-buy");
});

test("confirmed compatibility exposes the exact new filter conflict instead of hiding it with manual restore", () => {
  const item = signal();
  const filtered = signal({ status: "filtered", reasons: ["画线前缺少可辨认的上推段"] });
  const document = record("confirmed", item);
  const raw = result({ rejected: [filtered] });
  const audit = Feedback.auditConfirmedCompatibility(raw, "TUTUSDT", document);
  assert.equal(audit.passed, false);
  assert.equal(audit.filteredCount, 1);
  assert.deepEqual(audit.conflicts[0].reasons, ["画线前缺少可辨认的上推段"]);
  assert.throws(
    () => Feedback.assertConfirmedCompatibility(raw, "TUTUSDT", document),
    /新策略遗漏 1\/1 个历史确认买点.*画线前缺少可辨认的上推段/,
  );
  const applied = Feedback.applyToResult(raw, "TUTUSDT", document);
  assert.equal(applied.signals.length, 1, "永久确认标记仍应保留");
  assert.equal(applied.signals[0].strategyRegressionConflict, true);
  assert.equal(applied.signals[0].strategyCompatibilityStatus, "filtered");
  assert.ok(applied.signals[0].evidence.some((text) => text.includes("必须继续调整逻辑")));
});

test("confirmed points outside the loaded candle window do not create false regression conflicts", () => {
  const item = signal({ time: 1000 });
  const audit = Feedback.auditConfirmedCompatibility(
    result({ candleTimes: [2000, 3000] }),
    "TUTUSDT",
    record("confirmed", item),
  );
  assert.equal(audit.confirmedInWindow, 0);
  assert.equal(audit.conflictCount, 0);
  assert.equal(audit.passed, true);
});

test("the confirmed TUT 2026-08-09 15:00 one-minute candle is restored as a buy", () => {
  const time = 1_786_258_800_000;
  const item = signal({
    id: `manual-1m-${time}`,
    time,
    interval: "1m",
    index: 0,
    status: "pending",
    patternKey: "manualMissed",
    foundationTypes: ["manualReview"],
    auxiliaryTypes: ["previousHigh"],
    manualCandleSelection: true,
    manualSource: "chart-candle-picker",
    price: 0.19717661685855262,
    selectedPrice: 0.19717661685855262,
    low: 0.19187,
  });
  const oneMinuteResult = result({ candleTimes: [time] });
  oneMinuteResult.interval = "1m";
  oneMinuteResult.candles[0] = { time, open: 0.19224, high: 0.21193, low: 0.19187, close: 0.20595 };
  const applied = Feedback.applyToResult(oneMinuteResult, "TUTUSDT", record("confirmed", item));
  assert.equal(applied.signals.length, 1);
  assert.equal(applied.signals[0].time, time);
  assert.equal(applied.signals[0].status, "buy");
  assert.equal(applied.signals[0].manualDecision, "confirmed");
  assert.equal(applied.signals[0].manualRestored, true);
});

test("denied exact signal is moved to filtered records", () => {
  const item = signal();
  const revisedLogicSignal = signal({ pattern: "收敛三角 + 突破前高", patternKey: "triangle" });
  const applied = Feedback.applyToResult(result({ signals: [revisedLogicSignal] }), "TUTUSDT", record("denied", item));
  assert.equal(applied.signals.length, 0);
  assert.equal(applied.rejected.length, 1);
  assert.equal(applied.rejected[0].manualDecision, "denied");
  assert.match(applied.rejected[0].reasons[0], /用户已否定/);
});

test("pending review is persisted but is neither a positive nor a negative weight", () => {
  const item = signal();
  const pendingDocument = record("pending", item);
  const normalized = Feedback.normalizeDocument(pendingDocument);
  const key = Feedback.signalKey("TUTUSDT", item);
  assert.equal(normalized.records[key].optimizationLabel, 0);
  assert.equal(normalized.records[key].optimizationRole, "unlabeled");
  assert.deepEqual(Feedback.buildWeights(pendingDocument), {});
  const applied = Feedback.applyToResult(result({ signals: [item] }), "TUTUSDT", pendingDocument);
  assert.equal(applied.signals[0].manualDecision, "pending");
  assert.equal(applied.signals[0].status, "buy");
});

test("optimization dataset recognizes confirmed, pending and permanently denied reviews without future outcomes", () => {
  const confirmed = signal({ time: 1000, structureShape: "falling-wedge", rhythmScore: 91, futureReturn: 99 });
  const pending = signal({ time: 2000, patternKey: "triangle" });
  const denied = signal({ time: 3000, patternKey: "noise" });
  const document = Feedback.mergeDocuments(
    record("confirmed", confirmed, 10),
    record("pending", pending, 20),
    record("denied", denied, 30),
  );
  const dataset = Feedback.buildOptimizationDataset(document);
  assert.deepEqual(dataset.rows.map((row) => row.label), [1, 0, -1]);
  assert.deepEqual(dataset.summary, {
    total: 3,
    positiveCount: 1,
    negativeCount: 1,
    pendingCount: 1,
    labeledCount: 2,
    featureCount: 18,
    aPlusPrototypeCount: 0,
    aPlusSampleCount: 0,
    deniedPrototypeCount: 1,
    deniedPrototypeSampleCount: 1,
    visualLabeledCount: 0,
    visualAPlusCount: 0,
    visualDeniedCount: 0,
  });
  assert.equal(dataset.causality, "decision-time-features-only");
  assert.equal(Object.hasOwn(dataset.rows[0].metrics, "futureReturn"), false);
  assert.ok(dataset.rows[0].featureTokens.includes("foundation:base"));
});

test("a cancelled manual candle mark leaves a sync tombstone but no training row or restored buy", () => {
  const manual = signal({
    time: 2000,
    id: "manual-15m-2000",
    pattern: "人工补标 · 遗漏起爆点",
    patternKey: "manualMissed",
    foundationTypes: ["base"],
    auxiliaryTypes: ["previousHigh"],
    contextTokens: ["manual-missed", "prior-consolidation", "prior-high-cross"],
    manualCandleSelection: true,
    manualSource: "chart-candle-picker",
    featureCutoff: "selected-candle-intrabar-no-future-bars",
    priorRangePercent: 4.2,
    selectedPrice: 10.4,
    manualCertaintyGrade: "A+",
    manualStructureTags: ["horizontalLaunch", "ema90Pullback"],
  });
  const confirmed = record("confirmed", manual, 10);
  const restored = Feedback.applyToResult(result(), "TUTUSDT", confirmed);
  assert.equal(restored.signals[0].manualRestored, true);
  const cancelled = Feedback.mergeDocuments(confirmed, record("cleared", manual, 20));
  const key = Feedback.signalKey("TUTUSDT", manual);
  assert.equal(cancelled.records[key].decision, "cleared");
  assert.equal(cancelled.records[key].optimizationRole, "deleted");
  assert.equal(cancelled.records[key].certaintyGrade, "");
  assert.deepEqual(cancelled.records[key].structureTags, []);
  assert.equal(Object.hasOwn(cancelled.records[key].signal, "manualCertaintyGrade"), false);
  assert.equal(Object.hasOwn(cancelled.records[key].signal, "manualStructureTags"), false);
  assert.equal(Feedback.applyToResult(result(), "TUTUSDT", cancelled).signals.length, 0);
  assert.equal(Feedback.buildOptimizationDataset(cancelled).summary.total, 0);
  assert.deepEqual(Feedback.buildWeights(cancelled), {});
});

test("manual candle confirmation exports decision-time context for the next optimization", () => {
  const manual = signal({
    manualCandleSelection: true,
    manualSource: "chart-candle-picker",
    featureCutoff: "selected-candle-intrabar-no-future-bars",
    contextTokens: ["manual-missed", "above-ema90"],
    priorRangePercent: 5.6,
    priorVolumeRatio: 1.4,
    selectedPrice: 10.25,
  });
  const dataset = Feedback.buildOptimizationDataset(record("confirmed", manual));
  assert.equal(dataset.datasetVersion, 12);
  assert.equal(dataset.rows[0].structure.manualSource, "chart-candle-picker");
  assert.equal(dataset.rows[0].structure.featureCutoff, "selected-candle-intrabar-no-future-bars");
  assert.ok(dataset.rows[0].featureTokens.includes("context:manual-missed"));
  assert.equal(dataset.rows[0].metrics.priorRangePercent, 5.6);
});

test("manual structure confirmation persists multiple tags including EMA90 pullback", () => {
  const reviewed = signal({
    manualStructureTags: ["horizontalLaunch", "previousHighBreakout", "ema90Pullback", "volumeBreakout", "nearPreviousHighConsolidation", "newCoinNotFalling", "mainWaveActive", "mainWaveExpected", "invalidTag"],
  });
  const normalized = Feedback.normalizeDocument(record("confirmed", reviewed));
  const key = Feedback.signalKey("TUTUSDT", reviewed);
  assert.deepEqual(normalized.records[key].structureTags, [
    "horizontalLaunch",
    "previousHighBreakout",
    "ema90Pullback",
    "volumeBreakout",
    "nearPreviousHighConsolidation",
    "newCoinNotFalling",
    "mainWaveActive",
    "mainWaveExpected",
  ]);
  assert.deepEqual(normalized.records[key].signal.manualStructureTags, normalized.records[key].structureTags);
  const dataset = Feedback.buildOptimizationDataset(normalized);
  assert.deepEqual(dataset.rows[0].structureTags, normalized.records[key].structureTags);
  assert.deepEqual(dataset.rows[0].structure.manualStructureTags, normalized.records[key].structureTags);
  assert.ok(dataset.rows[0].featureTokens.includes("manual-structure:ema90Pullback"));
  assert.ok(dataset.rows[0].featureTokens.includes("manual-structure:volumeBreakout"));
  assert.ok(dataset.rows[0].featureTokens.includes("manual-structure:horizontalLaunch"));
  assert.ok(dataset.rows[0].featureTokens.includes("manual-structure:nearPreviousHighConsolidation"));
  assert.ok(dataset.rows[0].featureTokens.includes("manual-structure:newCoinNotFalling"));
  assert.ok(dataset.rows[0].featureTokens.includes("manual-structure:mainWaveActive"));
  assert.ok(dataset.rows[0].featureTokens.includes("manual-structure:mainWaveExpected"));
});

test("manual certainty grade is persisted, exported and scales only confirmed positive learning", () => {
  const aPlus = signal({ time: 1000, manualCertaintyGrade: "A+" });
  const a = signal({ time: 2000, manualCertaintyGrade: "A" });
  const b = signal({ time: 3000, manualCertaintyGrade: "B" });
  const document = Feedback.mergeDocuments(
    record("confirmed", aPlus, 10),
    record("confirmed", a, 20),
    record("confirmed", b, 30),
  );
  const keys = [aPlus, a, b].map((item) => Feedback.signalKey("TUTUSDT", item));
  assert.deepEqual(keys.map((key) => document.records[key].certaintyGrade), ["A+", "A", "B"]);
  const dataset = Feedback.buildOptimizationDataset(document);
  assert.deepEqual(dataset.rows.map((row) => row.certaintyGrade), ["A+", "A", "B"]);
  assert.deepEqual(dataset.rows.map((row) => row.metrics.manualCertaintyLevel), [3, 2, 1]);
  const weights = Feedback.buildWeights(document);
  assert.equal(weights["foundation:base"], 2);
});

test("A+ structure confirmations become reusable causal prototypes instead of label-only samples", () => {
  const prototype = signal({
    manualCertaintyGrade: "A+",
    manualStructureTags: ["horizontalLaunch", "box", "consolidationBreakout"],
    consolidationBars: 52,
    outerEdgeConfirmed: true,
    outerEdgeScore: 88,
    ceilingTouches: 3,
    platformTouchGroups: 2,
    aestheticScore: 81,
    rhythmScore: 84,
    orderFlowScore: 76,
    launchDistancePercent: 1.4,
    priorRangePercent: 3.8,
    priorDriftPercent: 1.2,
  });
  const profile = Feedback.buildAPlusPrototypeProfile(record("confirmed", prototype));
  assert.equal(profile.totalAPlusSamples, 1);
  assert.equal(profile.prototypes.length, 1);
  assert.deepEqual(profile.prototypes[0].structureTags, ["box", "consolidationBreakout", "horizontalLaunch"]);
  assert.equal(profile.prototypes[0].metrics.outerEdgeScore.median, 88);
  assert.ok(profile.prototypes[0].sharedReasons.includes("quality:outer-edge:strong"));
  assert.ok(profile.prototypes[0].sharedReasons.includes("quality:launch-distance:attached"));
  const dataset = Feedback.buildOptimizationDataset(record("confirmed", prototype));
  assert.equal(dataset.summary.aPlusPrototypeCount, 1);
  assert.equal(dataset.summary.aPlusSampleCount, 1);
});

test("permanently denied points become causal negative prototypes", () => {
  const negative = signal({
    manualStructureTags: ["box", "previousHighBreakout"],
    consolidationBars: 14,
    outerEdgeConfirmed: false,
    outerEdgeScore: 48,
    ceilingTouches: 1,
    aestheticScore: 52,
    rhythmScore: 43,
    launchDistancePercent: 8.6,
  });
  const profile = Feedback.buildDeniedPrototypeProfile(record("denied", negative));
  assert.equal(profile.totalDeniedSamples, 1);
  assert.equal(profile.prototypes.length, 1);
  assert.deepEqual(profile.prototypes[0].structureTags, ["box", "previousHighBreakout"]);
  assert.ok(profile.prototypes[0].sharedReasons.includes("quality:outer-edge:weak"));
  assert.ok(profile.prototypes[0].sharedReasons.includes("quality:launch-distance:far"));
  const supervised = Feedback.buildSupervisedPrototypeProfile(record("denied", negative));
  assert.equal(supervised.positiveAPlus.totalAPlusSamples, 0);
  assert.equal(supervised.negativeDenied.totalDeniedSamples, 1);
});

test("strategy pre-confirms structures and persists the user's comparison review", () => {
  const candidate = signal({
    foundationTypes: ["base"],
    auxiliaryTypes: ["previousHigh"],
    outerEdgeConfirmed: true,
    outerEdgeScore: 86,
    launchDistancePercent: 1.2,
    relativeVolume: 1.5,
  });
  const predicted = Feedback.inferStructureTags(candidate);
  assert.deepEqual(predicted, [
    "horizontalLaunch", "box", "consolidationBreakout", "previousHighBreakout",
    "nearPreviousHighConsolidation", "volumeBreakout",
  ]);
  const review = Feedback.compareStructureTags(predicted, ["horizontalLaunch", "box", "fallingWedge"]);
  assert.deepEqual(review.addedByUser, ["fallingWedge"]);
  assert.ok(review.removedByUser.includes("previousHighBreakout"));
  const reviewed = {
    ...candidate,
    manualStructureTags: review.reviewed,
    predictedStructureTags: predicted,
    structureReview: review,
  };
  const normalized = Feedback.normalizeDocument(record("confirmed", reviewed));
  const saved = normalized.records[Feedback.signalKey("TUTUSDT", reviewed)];
  assert.deepEqual(saved.predictedStructureTags, predicted);
  assert.deepEqual(saved.structureReview.addedByUser, ["fallingWedge"]);
  const dataset = Feedback.buildOptimizationDataset(normalized);
  assert.ok(dataset.rows[0].featureTokens.includes("strategy-structure:box"));
  assert.ok(dataset.rows[0].featureTokens.includes("review-added:fallingWedge"));
  assert.ok(dataset.rows[0].featureTokens.includes("review-removed:previousHighBreakout"));
});

test("strategy pre-confirmation exposes inferred and declared main-wave context for human review", () => {
  assert.ok(Feedback.inferStructureTags(signal({ mainWaveStage: "active" })).includes("mainWaveActive"));
  assert.ok(Feedback.inferStructureTags(signal({ mainWaveStage: "expected" })).includes("mainWaveExpected"));
  assert.ok(Feedback.inferStructureTags(signal({ newCoinNotFallingMainWavePermit: true })).includes("newCoinNotFalling"));
  assert.equal(Feedback.inferStructureTags(signal({ mainWaveStage: "neutral" })).includes("mainWaveActive"), false);
});

test("learned pattern weights are bounded and never promote an unrelated pending signal", () => {
  const documents = [];
  for (let index = 0; index < 20; index += 1) {
    const item = signal({ time: 10_000 + index, id: `s-${index}` });
    documents.push(record("confirmed", item, 100 + index));
  }
  const merged = Feedback.mergeDocuments(...documents);
  const pending = signal({ time: 1000, status: "pending", score: 79 });
  const applied = Feedback.applyToResult(result({ pending: [pending] }), "TUTUSDT", merged);
  assert.equal(applied.pending.length, 1);
  assert.equal(applied.pending[0].status, "pending");
  assert.equal(applied.pending[0].feedbackAdjustment, 4);
  assert.equal(applied.pending[0].score, 83);
});

test("one densely reviewed leader cannot globally veto the same setup on another leader", () => {
  const denied = Array.from({ length: 6 }, (_, index) => recordFor(
    "TUTUSDT",
    "denied",
    signal({ time: 10_000 + index, interval: "5m", foundationTypes: ["base", "relaunch"], auxiliaryTypes: ["previousHigh"], structureShape: "" }),
    100 + index,
  ));
  const document = Feedback.mergeDocuments(...denied);
  const candidate = signal({ time: 30_000, interval: "5m", foundationTypes: ["base", "relaunch"], auxiliaryTypes: ["previousHigh"], structureShape: "" });
  const applied = Feedback.applyToResult(result({ signals: [candidate], candleTimes: [30_000] }), "COWUSDT", document);
  assert.equal(applied.signals.length, 1);
  assert.equal(applied.rejected.length, 0);
});

test("four zero-positive reviews veto an unreviewed matching setup only on that leader", () => {
  const denied = Array.from({ length: 4 }, (_, index) => recordFor(
    "TUTUSDT",
    "denied",
    signal({ time: 10_000 + index, interval: "5m", foundationTypes: ["base", "relaunch"], auxiliaryTypes: ["previousHigh"], structureShape: "" }),
    100 + index,
  ));
  const document = Feedback.mergeDocuments(...denied);
  const candidate = signal({ time: 30_000, interval: "5m", foundationTypes: ["base", "relaunch"], auxiliaryTypes: ["previousHigh"], structureShape: "" });
  const applied = Feedback.applyToResult(result({ signals: [candidate], candleTimes: [30_000] }), "TUTUSDT", document);
  assert.equal(applied.signals.length, 0);
  assert.equal(applied.rejected.length, 1);
  assert.equal(applied.rejected[0].feedbackReviewVeto, true);
  assert.equal(applied.rejected[0].feedbackReviewScope, "local");
  assert.match(applied.rejected[0].reasons[0], /4 个反例且无确认/);
});

test("consistent zero-positive reviews from two leaders can generalize to a third leader", () => {
  const documents = [];
  for (const pair of ["TUTUSDT", "SPKUSDT"]) {
    for (let index = 0; index < 3; index += 1) {
      documents.push(recordFor(
        pair,
        "denied",
        signal({ time: (pair === "TUTUSDT" ? 10_000 : 20_000) + index, interval: "5m", foundationTypes: ["base"], auxiliaryTypes: [], structureShape: "" }),
        100 + documents.length,
      ));
    }
  }
  const document = Feedback.mergeDocuments(...documents);
  const candidate = signal({ time: 30_000, interval: "5m", foundationTypes: ["base"], auxiliaryTypes: [], structureShape: "" });
  const applied = Feedback.applyToResult(result({ signals: [candidate], candleTimes: [30_000] }), "COWUSDT", document);
  assert.equal(applied.signals.length, 0);
  assert.equal(applied.rejected[0].feedbackReviewScope, "global");
  assert.match(applied.rejected[0].reasons[0], /2 个龙头累计 6 个反例/);
});

test("one confirmed example releases the zero-positive setup veto", () => {
  const examples = Array.from({ length: 4 }, (_, index) => recordFor(
    "TUTUSDT",
    "denied",
    signal({ time: 10_000 + index, interval: "5m", foundationTypes: ["base", "relaunch"], auxiliaryTypes: ["previousHigh"], structureShape: "" }),
    100 + index,
  ));
  examples.push(recordFor(
    "TUTUSDT",
    "confirmed",
    signal({ time: 20_000, interval: "5m", foundationTypes: ["base", "relaunch"], auxiliaryTypes: ["previousHigh"], structureShape: "" }),
    200,
  ));
  const candidate = signal({ time: 30_000, interval: "5m", foundationTypes: ["base", "relaunch"], auxiliaryTypes: ["previousHigh"], structureShape: "" });
  const applied = Feedback.applyToResult(result({ signals: [candidate], candleTimes: [30_000] }), "TUTUSDT", Feedback.mergeDocuments(...examples));
  assert.equal(applied.signals.length, 1);
  assert.equal(applied.signals[0].feedbackReviewVeto, undefined);
});

test("venue changes do not change permanent signal identity", () => {
  const item = signal();
  assert.equal(Feedback.signalKey("TUTUSDT", { ...item, venue: "Binance" }), Feedback.signalKey("TUTUSDT", { ...item, venue: "OKX" }));
});
