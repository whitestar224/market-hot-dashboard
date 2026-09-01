(function (root, factory) {
  const vision = typeof module === "object" && module.exports
    ? require("./dragon-wave-vision.js")
    : root.DragonWaveVision;
  const api = factory(vision);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.DragonWaveFeedback = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (Vision) {
  "use strict";

  const MAX_RECORDS = 1200;
  const DATASET_VERSION = 12;
  const LOCAL_ZERO_POSITIVE_VETO_MIN = 4;
  const GLOBAL_ZERO_POSITIVE_VETO_MIN = 6;
  const GLOBAL_VETO_PAIR_MIN = 2;
  const DECISIONS = new Set(["confirmed", "pending", "denied", "cleared"]);
  const CERTAINTY_GRADES = new Set(["A+", "A", "B"]);
  const STRUCTURE_TAGS = new Set([
    "horizontalLaunch", "trendlineBreakout", "triangle", "box",
    "fallingWedge", "pivot", "previousHighBreakout", "consolidationBreakout", "ema90Pullback",
    "volumeBreakout", "nearPreviousHighConsolidation", "newCoinNotFalling",
    "mainWaveActive", "mainWaveExpected",
  ]);
  const SNAPSHOT_FIELDS = [
    "id", "time", "decisionTime", "interval", "pattern", "patternKey", "confluence",
    "foundationTypes", "auxiliaryTypes", "hasPivot", "price", "triggerPrice", "level",
    "previousHighLevel", "stop", "score", "relativeVolume", "consolidationBars", "rhythmScore",
    "sentimentScore", "sentimentPhase", "marketEmotion", "mainWaveStage", "mainWaveContextSource", "orderFlowScore",
    "certaintyScore", "structuralRiskPercent", "trendline", "triangleLines",
    "evidence", "reasons", "status", "outerEdgeType", "outerEdgeLevel", "crossFramePrecision",
    "higherTimeframeAnchor", "feedbackAdjustment", "newCoinNotFallingMainWavePermit",
    "newCoinListingAgeHours", "newCoinHigherFrameCounts", "newCoinMarketHeat",
    "structureShape", "structureQuality", "wedgeStructureEvidence", "outerEdgeConfirmed", "outerEdgeScore", "clusteredCeilingBand", "ceilingBandToleranceAtr", "ceilingAge", "ceilingTouches",
    "primaryPatternKey", "consolidationBreakout",
    "candidateTier", "executionAllowed", "consolidationBreakoutCandidate", "candidateReasons",
    "platformTouchGroups", "launchDistancePercent", "compressionRatioAtDecision",
    "channelInteriorOccupancy", "channelMiddleParticipationRatio", "channelHollowRatio", "channelLongestHollowRun", "channelSideTransitions",
    "manualCandleSelection", "manualSource", "featureCutoff", "contextTokens",
    "open", "high", "low", "close", "volume", "ema90AtDecision", "atrAtDecision",
    "priorHighAtDecision", "priorLowAtDecision", "priorRangePercent", "priorDriftPercent",
    "priorVolumeRatio", "aboveEma90", "ema90SlopeAtDecision", "breaksPriorHigh", "selectedPrice",
    "manualCertaintyGrade", "manualStructureTags", "predictedStructureTags", "structureReview",
    "visualStructureStartIndex", "visualStructureStartTime", "visualStructureBars", "visualStructureSource",
    "visualSignature", "visualPreconfirmed", "visualLearning",
  ];

  function finite(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function mean(values) {
    return values.length ? values.reduce((sum, value) => sum + finite(value), 0) / values.length : 0;
  }

  function normalizeCertaintyGrade(value) {
    const grade = String(value || "").toUpperCase().trim();
    return CERTAINTY_GRADES.has(grade) ? grade : "";
  }

  function normalizeStructureTags(value) {
    const tags = Array.isArray(value) ? value : [];
    return [...new Set(tags.map((item) => String(item || "").trim()).filter((item) => STRUCTURE_TAGS.has(item)))];
  }

  function inferStructureTags(signal) {
    if (!signal || typeof signal !== "object") return [];
    const foundations = new Set((signal.foundationTypes || []).map(String));
    const auxiliaries = new Set((signal.auxiliaryTypes || []).map(String));
    const pattern = String(signal.pattern || "");
    const shape = String(signal.structureShape || "");
    const tags = [];
    const add = (tag) => {
      if (STRUCTURE_TAGS.has(tag) && !tags.includes(tag)) tags.push(tag);
    };
    if (foundations.has("base")) add("horizontalLaunch");
    if (signal.consolidationBreakout === true
      || signal.outerEdgeConfirmed === true
      || (foundations.has("base") && (signal.outerEdgeScore || 0) >= 62)) {
      add("box");
      add("consolidationBreakout");
    }
    if (foundations.has("triangle") || shape === "converging-triangle") add("triangle");
    if (shape === "falling-wedge") add("fallingWedge");
    if (signal.hasPivot || foundations.has("pivot") || pattern.includes("拐点")) add("pivot");
    if (auxiliaries.has("trendline") && (shape || foundations.size > 0)) add("trendlineBreakout");
    if (auxiliaries.has("previousHigh")) add("previousHighBreakout");
    if (foundations.has("base") && auxiliaries.has("previousHigh") && (signal.launchDistancePercent ?? 99) <= 3) {
      add("nearPreviousHighConsolidation");
    }
    if (pattern.includes("回踩90") || pattern.includes("均线") || (signal.contextTokens || []).includes("ema90-reclaim")) {
      add("ema90Pullback");
    }
    if ((signal.relativeVolume || 0) >= 1.35 || (signal.orderFlowScore || 0) >= 72) add("volumeBreakout");
    if (signal.newCoinNotFallingMainWavePermit === true) add("newCoinNotFalling");
    if (signal.mainWaveStage === "active") add("mainWaveActive");
    else if (signal.mainWaveStage === "expected") add("mainWaveExpected");
    return normalizeStructureTags(tags);
  }

  function compareStructureTags(predicted, reviewed) {
    const prediction = normalizeStructureTags(predicted).sort();
    const manual = normalizeStructureTags(reviewed).sort();
    const predictedSet = new Set(prediction);
    const manualSet = new Set(manual);
    const matched = prediction.filter((tag) => manualSet.has(tag));
    const addedByUser = manual.filter((tag) => !predictedSet.has(tag));
    const removedByUser = prediction.filter((tag) => !manualSet.has(tag));
    const unionSize = new Set([...prediction, ...manual]).size;
    const agreement = unionSize ? matched.length / unionSize : 1;
    return {
      predicted: prediction,
      reviewed: manual,
      matched,
      addedByUser,
      removedByUser,
      agreement: Math.round(agreement * 1000) / 1000,
      exact: addedByUser.length === 0 && removedByUser.length === 0,
    };
  }

  function cleanJson(value, depth = 0) {
    // 视觉签名包含 signal → signature → windows → vector 的紧凑嵌套；
    // 六层仍是固定小对象，但可避免重复归一化时把路径向量清成 null。
    if (depth > 6) return null;
    if (value == null || typeof value === "boolean") return value;
    if (typeof value === "number") return Number.isFinite(value) ? value : 0;
    if (typeof value === "string") return value.slice(0, 1200);
    if (Array.isArray(value)) return value.slice(0, 32).map((item) => cleanJson(item, depth + 1));
    if (typeof value === "object") {
      return Object.fromEntries(Object.entries(value).slice(0, 64).map(([key, item]) => [
        String(key).slice(0, 80),
        cleanJson(item, depth + 1),
      ]));
    }
    return String(value).slice(0, 1200);
  }

  function emptyDocument() {
    return { version: 1, updatedAt: 0, records: {} };
  }

  function normalizeDocument(value) {
    const payload = value && typeof value === "object" ? value : {};
    const source = payload.records && typeof payload.records === "object" ? payload.records : {};
    const ordered = Object.entries(source)
      .sort((a, b) => finite(b[1]?.updatedAt) - finite(a[1]?.updatedAt))
      .slice(0, MAX_RECORDS);
    const records = {};
    ordered.forEach(([rawKey, rawRecord]) => {
      if (!rawRecord || typeof rawRecord !== "object") return;
      const key = String(rawKey || rawRecord.key || "").slice(0, 240);
      const decision = String(rawRecord.decision || "");
      if (!key || !DECISIONS.has(decision)) return;
      const signal = cleanJson(rawRecord.signal && typeof rawRecord.signal === "object" ? rawRecord.signal : {});
      const certaintyGrade = decision === "cleared"
        ? ""
        : normalizeCertaintyGrade(rawRecord.certaintyGrade || signal.manualCertaintyGrade);
      const structureTags = decision === "cleared"
        ? []
        : normalizeStructureTags(rawRecord.structureTags ?? signal.manualStructureTags);
      const predictionWasStored = decision !== "cleared" && (
        Array.isArray(rawRecord.predictedStructureTags)
        || Array.isArray(signal.predictedStructureTags)
      );
      const predictedStructureTags = decision === "cleared"
        ? []
        : normalizeStructureTags(rawRecord.predictedStructureTags ?? signal.predictedStructureTags);
      const structureReview = decision === "cleared" || !predictionWasStored
        ? null
        : compareStructureTags(predictedStructureTags, structureTags);
      if (certaintyGrade) signal.manualCertaintyGrade = certaintyGrade;
      else delete signal.manualCertaintyGrade;
      if (structureTags.length) signal.manualStructureTags = structureTags;
      else delete signal.manualStructureTags;
      if (predictionWasStored) signal.predictedStructureTags = predictedStructureTags;
      else delete signal.predictedStructureTags;
      if (predictionWasStored && structureReview) signal.structureReview = structureReview;
      else delete signal.structureReview;
      records[key] = {
        key,
        decision,
        optimizationLabel: decision === "confirmed" ? 1 : decision === "denied" ? -1 : 0,
        optimizationRole: decision === "confirmed" ? "positive" : decision === "denied" ? "negative" : decision === "pending" ? "unlabeled" : "deleted",
        datasetVersion: DATASET_VERSION,
        createdAt: Math.max(0, Math.trunc(finite(rawRecord.createdAt))),
        updatedAt: Math.max(0, Math.trunc(finite(rawRecord.updatedAt))),
        pair: String(rawRecord.pair || "").toUpperCase().slice(0, 32),
        interval: String(rawRecord.interval || "").slice(0, 8),
        venue: String(rawRecord.venue || "").slice(0, 80),
        certaintyGrade,
        structureTags,
        ...(predictionWasStored ? { predictedStructureTags, structureReview } : {}),
        signal,
      };
    });
    const updatedAt = Object.values(records).reduce((latest, record) => Math.max(latest, record.updatedAt), 0);
    return { version: 1, updatedAt, records };
  }

  function mergeDocuments(...documents) {
    const merged = emptyDocument();
    documents.forEach((document) => {
      const normalized = normalizeDocument(document);
      Object.entries(normalized.records).forEach(([key, record]) => {
        const existing = merged.records[key];
        if (!existing || record.updatedAt >= existing.updatedAt) merged.records[key] = record;
      });
    });
    return normalizeDocument(merged);
  }

  function signalKey(pair, signal) {
    const normalizedPair = String(pair || signal?.pair || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
    const interval = String(signal?.interval || "").trim();
    const time = Math.trunc(finite(signal?.time));
    return normalizedPair && interval && time ? `${normalizedPair}|${interval}|${time}` : "";
  }

  function snapshotSignal(signal) {
    const snapshot = {};
    SNAPSHOT_FIELDS.forEach((field) => {
      if (signal?.[field] !== undefined) snapshot[field] = cleanJson(signal[field]);
    });
    return snapshot;
  }

  function featureTokens(signal) {
    if (!signal || typeof signal !== "object") return [];
    const tokens = [];
    const foundations = [...new Set((signal.foundationTypes || []).map(String))].sort();
    const auxiliaries = [...new Set((signal.auxiliaryTypes || []).map(String))].sort();
    const confluence = [...new Set((signal.confluence || []).map(String))].sort();
    const interval = String(signal.interval || "").trim();
    const structureShape = String(signal.structureShape || "none").trim() || "none";
    foundations.forEach((item) => tokens.push(`foundation:${item}`));
    auxiliaries.forEach((item) => tokens.push(`auxiliary:${item}`));
    if (signal.patternKey) tokens.push(`pattern:${signal.patternKey}`);
    if (confluence.length) tokens.push(`combo:${confluence.join("+")}`);
    if (interval) {
      tokens.push(`interval:${interval}`);
      if (foundations.length && !foundations.includes("manualReview")) {
        tokens.push(`interval-foundation:${interval}|${foundations.join("+")}`);
        tokens.push(`interval-auxiliary:${interval}|${auxiliaries.join("+") || "none"}`);
        tokens.push(`interval-shape:${interval}|${structureShape}`);
        // 只有完整的“周期＋基础结构＋辅助结构＋形态”组合才有资格成为
        // 强反例规则，避免因单一周期或单一结构标签误杀其他起爆点。
        tokens.push(`interval-setup:${interval}|${foundations.join("+")}>${auxiliaries.join("+") || "none"}|${structureShape}`);
      }
    }
    (Array.isArray(signal.contextTokens) ? signal.contextTokens : []).map(String).sort()
      .forEach((item) => tokens.push(`context:${item}`));
    normalizeStructureTags(signal.manualStructureTags).sort()
      .forEach((item) => tokens.push(`manual-structure:${item}`));
    normalizeStructureTags(signal.predictedStructureTags || inferStructureTags(signal)).sort()
      .forEach((item) => tokens.push(`strategy-structure:${item}`));
    const review = signal.structureReview && typeof signal.structureReview === "object"
      ? signal.structureReview
      : null;
    normalizeStructureTags(review?.matched).sort().forEach((item) => tokens.push(`review-match:${item}`));
    normalizeStructureTags(review?.addedByUser).sort().forEach((item) => tokens.push(`review-added:${item}`));
    normalizeStructureTags(review?.removedByUser).sort().forEach((item) => tokens.push(`review-removed:${item}`));
    if (review) tokens.push(`review-agreement:${review.exact ? "exact" : review.agreement >= 0.5 ? "partial" : "low"}`);
    const metricBand = (name, value, boundaries, labels) => {
      const number = Number(value);
      if (!Number.isFinite(number)) return;
      const index = boundaries.findIndex((boundary) => number < boundary);
      tokens.push(`quality:${name}:${labels[index < 0 ? labels.length - 1 : index]}`);
    };
    metricBand("base-bars", signal.consolidationBars, [20, 40, 80], ["short", "forming", "mature", "long"]);
    metricBand("outer-edge", signal.outerEdgeScore, [60, 80], ["weak", "clean", "strong"]);
    metricBand("ceiling-touches", signal.ceilingTouches, [2, 3], ["single", "double", "multiple"]);
    metricBand("rhythm", signal.rhythmScore, [60, 75], ["weak", "flowing", "elite"]);
    metricBand("certainty", signal.certaintyScore, [70, 85], ["low", "high", "elite"]);
    metricBand("order-flow", signal.orderFlowScore, [60, 75], ["quiet", "supportive", "strong"]);
    metricBand("launch-distance", signal.launchDistancePercent, [2, 7], ["attached", "near", "far"]);
    metricBand("prior-range", signal.priorRangePercent, [4, 8], ["tight", "controlled", "wide"]);
    metricBand("prior-drift", signal.priorDriftPercent, [2, 6], ["flat", "controlled", "trending"]);
    metricBand("prior-volume", signal.priorVolumeRatio ?? signal.relativeVolume, [1, 1.35], ["dry", "normal", "expanding"]);
    metricBand("channel-occupancy", signal.channelInteriorOccupancy, [0.5, 0.7], ["hollow", "occupied", "full"]);
    metricBand("channel-hollow", signal.channelHollowRatio, [0.25, 0.42], ["low", "moderate", "high"]);
    metricBand("channel-transitions", signal.channelSideTransitions, [2, 5], ["single-side", "rotating", "active"]);
    if (signal.outerEdgeConfirmed === true) tokens.push("quality:outer-edge-confirmed");
    if (signal.aboveEma90 === true) tokens.push("quality:above-ema90");
    if (signal.breaksPriorHigh === true) tokens.push("quality:breaks-prior-high");
    const grade = normalizeCertaintyGrade(signal.manualCertaintyGrade);
    if (grade) tokens.push(`manual-grade:${grade}`);
    return [...new Set(tokens)];
  }

  function optimizationMetrics(signal) {
    const fields = [
      "score", "certaintyScore", "rhythmScore", "sentimentScore",
      "orderFlowScore", "consolidationBars", "relativeVolume", "structuralRiskPercent", "structureQuality", "wedgeStructureEvidence",
      "ceilingAge", "ceilingTouches", "outerEdgeScore",
      "platformTouchGroups", "launchDistancePercent", "compressionRatioAtDecision",
      "channelInteriorOccupancy", "channelMiddleParticipationRatio", "channelHollowRatio", "channelLongestHollowRun", "channelSideTransitions",
      "ema90AtDecision", "ema90SlopeAtDecision", "atrAtDecision", "priorHighAtDecision", "priorLowAtDecision",
      "priorRangePercent", "priorDriftPercent", "priorVolumeRatio", "selectedPrice",
    ];
    const metrics = {};
    fields.forEach((field) => {
      const value = Number(signal?.[field]);
      if (Number.isFinite(value)) metrics[field] = value;
    });
    const certaintyLevel = { "A+": 3, A: 2, B: 1 }[normalizeCertaintyGrade(signal?.manualCertaintyGrade)];
    if (certaintyLevel) metrics.manualCertaintyLevel = certaintyLevel;
    return metrics;
  }

  function percentile(values, ratio) {
    const ordered = values.map(Number).filter(Number.isFinite).sort((a, b) => a - b);
    if (!ordered.length) return 0;
    const position = (ordered.length - 1) * ratio;
    const lower = Math.floor(position);
    const upper = Math.ceil(position);
    if (lower === upper) return ordered[lower];
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower);
  }

  function buildPrototypeProfile(document, selector, alreadyNormalized = false) {
    const normalized = alreadyNormalized ? document : normalizeDocument(document);
    const groups = new Map();
    Object.values(normalized.records).forEach((record) => {
      if (!selector(record)) return;
      const signal = record.signal || {};
      const tags = normalizeStructureTags(record.structureTags || signal.manualStructureTags).sort();
      const interval = String(record.interval || signal.interval || "");
      const foundations = [...new Set((signal.foundationTypes || []).map(String))].sort();
      const auxiliaries = [...new Set((signal.auxiliaryTypes || []).map(String))].sort();
      const structureShape = String(signal.structureShape || "none");
      const setupSignature = `${foundations.join("+") || "none"}>${auxiliaries.join("+") || "none"}|${structureShape}`;
      const key = tags.length
        ? `${interval}|manual:${tags.join("+")}`
        : `${interval}|setup:${setupSignature}`;
      if (!groups.has(key)) groups.set(key, {
        interval,
        tags,
        setupSignature,
        rows: [],
        pairs: new Set(),
      });
      const group = groups.get(key);
      group.rows.push(signal);
      group.pairs.add(record.pair);
    });
    const metricNames = [
      "consolidationBars", "outerEdgeScore", "ceilingTouches", "platformTouchGroups",
      "rhythmScore", "certaintyScore", "orderFlowScore",
      "launchDistancePercent", "priorRangePercent", "priorDriftPercent", "priorVolumeRatio",
      "channelInteriorOccupancy", "channelMiddleParticipationRatio", "channelHollowRatio", "channelLongestHollowRun", "channelSideTransitions",
    ];
    const prototypes = [...groups.entries()].map(([key, group]) => {
      const metrics = {};
      metricNames.forEach((name) => {
        const values = group.rows.map((row) => row?.[name]).filter((value) => Number.isFinite(Number(value)));
        if (!values.length) return;
        metrics[name] = {
          low: percentile(values, 0.25),
          median: percentile(values, 0.5),
          high: percentile(values, 0.75),
        };
      });
      const tokenCounts = {};
      group.rows.forEach((row) => featureTokens(row).forEach((token) => {
        if (token.startsWith("quality:") || token.startsWith("context:") || token.startsWith("manual-structure:")
          || token.startsWith("strategy-structure:") || token.startsWith("review-")) {
          tokenCounts[token] = finite(tokenCounts[token]) + 1;
        }
      }));
      return {
        key,
        interval: group.interval,
        structureTags: group.tags,
        setupSignature: group.setupSignature,
        sampleCount: group.rows.length,
        pairCount: group.pairs.size,
        metrics,
        sharedReasons: Object.entries(tokenCounts)
          .filter(([, count]) => count / group.rows.length >= 0.6)
          .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
          .map(([token]) => token),
      };
    }).sort((a, b) => b.sampleCount - a.sampleCount || a.key.localeCompare(b.key));
    return { sampleCount: prototypes.reduce((sum, item) => sum + item.sampleCount, 0), prototypes };
  }

  function buildAPlusPrototypeProfile(document, alreadyNormalized = false) {
    const profile = buildPrototypeProfile(document, (record) => (
      record.decision === "confirmed" && record.certaintyGrade === "A+"
    ), alreadyNormalized);
    return { totalAPlusSamples: profile.sampleCount, prototypes: profile.prototypes };
  }

  function buildDeniedPrototypeProfile(document, alreadyNormalized = false) {
    const profile = buildPrototypeProfile(document, (record) => record.decision === "denied", alreadyNormalized);
    return { totalDeniedSamples: profile.sampleCount, prototypes: profile.prototypes };
  }

  function buildSupervisedPrototypeProfile(document, alreadyNormalized = false) {
    const normalized = alreadyNormalized ? document : normalizeDocument(document);
    return {
      positiveAPlus: buildAPlusPrototypeProfile(normalized, true),
      negativeDenied: buildDeniedPrototypeProfile(normalized, true),
      policy: "causal-feature-combination-only",
    };
  }

  function buildVisualPrototypeIndex(document, alreadyNormalized = false) {
    const normalized = alreadyNormalized ? document : normalizeDocument(document);
    return Object.values(normalized.records).flatMap((record) => {
      if (!["confirmed", "denied"].includes(record.decision) || !record.signal?.visualSignature) return [];
      return [{
        key: record.key,
        pair: record.pair,
        interval: String(record.interval || record.signal?.interval || ""),
        time: finite(record.time),
        decision: record.decision,
        certaintyGrade: record.certaintyGrade,
        structureTags: record.structureTags,
        visualSignature: record.signal.visualSignature,
      }];
    });
  }

  function visualLearningAssessment(signal, pair, document, prototypeIndex = null) {
    const empty = {
      eligible: false,
      mode: "safe-preconfirmation-only",
      positiveSimilarity: 0,
      negativeSimilarity: 0,
      positiveSampleCount: 0,
      negativeSampleCount: 0,
      positivePairCount: 0,
      positiveAPlusCount: 0,
      suggestedStructureTags: [],
      structureTagConfidence: 0,
      structureTagVotes: [],
      nearestPositive: [],
      nearestNegative: [],
      reason: "尚无可用视觉样本",
    };
    if (!Vision?.compareVisualSignatures || !signal?.visualSignature) return empty;
    const interval = String(signal.interval || "");
    const prototypes = prototypeIndex || buildVisualPrototypeIndex(document);
    const comparisons = prototypes.flatMap((record) => {
      if (record.interval !== interval) return [];
      const comparison = Vision.compareVisualSignatures(signal.visualSignature, record.visualSignature);
      if (comparison.matchedWindows < 2) return [];
      return [{
        key: record.key,
        pair: record.pair,
        time: finite(record.signal?.time),
        decision: record.decision,
        certaintyGrade: record.certaintyGrade,
        structureTags: record.structureTags,
        similarity: comparison.score,
        matchedWindows: comparison.matchedWindows,
      }];
    });
    const positives = comparisons.filter((item) => item.decision === "confirmed")
      .sort((a, b) => b.similarity - a.similarity || a.key.localeCompare(b.key));
    const negatives = comparisons.filter((item) => item.decision === "denied")
      .sort((a, b) => b.similarity - a.similarity || a.key.localeCompare(b.key));
    const supportedPositives = positives.filter((item) => item.similarity >= 80);
    const gradeWeight = { "A+": 1, A: 0.78, B: 0.55 };
    const weightedTop = positives.slice(0, 3);
    const weightTotal = weightedTop.reduce((sum, item) => sum + (gradeWeight[item.certaintyGrade] || 0.65), 0);
    const positiveSimilarity = weightTotal
      ? Math.round(weightedTop.reduce((sum, item) => (
        sum + item.similarity * (gradeWeight[item.certaintyGrade] || 0.65)
      ), 0) / weightTotal)
      : 0;
    const negativeSimilarity = negatives.length
      ? Math.round(mean(negatives.slice(0, 2).map((item) => item.similarity)))
      : 0;
    const positivePairs = new Set(supportedPositives.map((item) => item.pair).filter(Boolean));
    const positiveAPlusCount = supportedPositives.filter((item) => item.certaintyGrade === "A+").length;
    const supportReady = positivePairs.size >= 2 || supportedPositives.length >= 3;
    const tagVotes = new Map();
    const voteCandidates = supportedPositives.slice(0, 5);
    let tagVoteTotal = 0;
    voteCandidates.forEach((item) => {
      const weight = (gradeWeight[item.certaintyGrade] || 0.65) * item.similarity;
      tagVoteTotal += weight;
      normalizeStructureTags(item.structureTags).forEach((tag) => {
        const vote = tagVotes.get(tag) || { tag, weight: 0, samples: 0, pairs: new Set() };
        vote.weight += weight;
        vote.samples += 1;
        if (item.pair) vote.pairs.add(item.pair);
        tagVotes.set(tag, vote);
      });
    });
    const structureTagVotes = [...tagVotes.values()].map((vote) => ({
      tag: vote.tag,
      confidence: tagVoteTotal ? Math.round(vote.weight / tagVoteTotal * 100) : 0,
      samples: vote.samples,
      pairs: vote.pairs.size,
    })).sort((a, b) => b.confidence - a.confidence || b.samples - a.samples || a.tag.localeCompare(b.tag));
    const suggestedStructureTags = structureTagVotes
      .filter((vote) => vote.confidence >= 55 && (vote.samples >= 2 || vote.pairs >= 2))
      .map((vote) => vote.tag);
    const structureTagConfidence = suggestedStructureTags.length
      ? Math.round(mean(structureTagVotes.filter((vote) => suggestedStructureTags.includes(vote.tag)).map((vote) => vote.confidence)))
      : 0;
    const eligible = positiveSimilarity >= 88
      && supportReady
      && positiveAPlusCount >= 1
      && negativeSimilarity <= positiveSimilarity - 8;
    return {
      eligible,
      mode: "safe-preconfirmation-only",
      positiveSimilarity,
      negativeSimilarity,
      positiveSampleCount: positives.length,
      negativeSampleCount: negatives.length,
      positivePairCount: positivePairs.size,
      positiveAPlusCount,
      suggestedStructureTags,
      structureTagConfidence,
      structureTagVotes,
      nearestPositive: positives.slice(0, 3),
      nearestNegative: negatives.slice(0, 3),
      reason: eligible
        ? `视觉轮廓接近人工正样本 ${positiveSimilarity}%，高于反例 ${positiveSimilarity - negativeSimilarity} 分；仅生成 V 预确认`
        : positives.length
          ? `视觉正样本 ${positiveSimilarity}%，反例 ${negativeSimilarity}%；样本多样性或正负间距尚不足`
          : "当前周期尚无已确认视觉样本",
    };
  }

  function isSoftVisualAestheticFilter(signal) {
    const reasons = Array.isArray(signal?.reasons) ? signal.reasons : [];
    const softPatterns = [
      /母结构尚未成熟/,
      /突破前未贴近关键位蓄力/,
      /结构松散/,
      /单一前高结构往返噪声过高/,
    ];
    return reasons.every((reason) => softPatterns.some((pattern) => pattern.test(String(reason))));
  }

  function buildOptimizationDataset(document) {
    const normalized = normalizeDocument(document);
    const rows = Object.values(normalized.records)
      .filter((record) => record.decision !== "cleared")
      .sort((a, b) => finite(a.updatedAt) - finite(b.updatedAt) || a.key.localeCompare(b.key))
      .map((record) => {
        const signal = record.signal || {};
        return {
          key: record.key,
          label: record.optimizationLabel,
          role: record.optimizationRole,
          decision: record.decision,
          certaintyGrade: record.certaintyGrade,
          structureTags: record.structureTags,
          pair: record.pair,
          interval: record.interval,
          time: Math.max(0, Math.trunc(finite(signal.time))),
          updatedAt: record.updatedAt,
          featureTokens: featureTokens(signal),
          structure: {
            patternKey: String(signal.patternKey || ""),
            pattern: String(signal.pattern || ""),
            foundationTypes: [...new Set((signal.foundationTypes || []).map(String))].sort(),
            auxiliaryTypes: [...new Set((signal.auxiliaryTypes || []).map(String))].sort(),
            confluence: [...new Set((signal.confluence || []).map(String))].sort(),
            structureShape: String(signal.structureShape || ""),
            hasPivot: Boolean(signal.hasPivot),
            sentimentPhase: String(signal.sentimentPhase || ""),
            marketEmotion: String(signal.marketEmotion || ""),
            manualSource: String(signal.manualSource || ""),
            featureCutoff: String(signal.featureCutoff || ""),
            contextTokens: [...new Set((Array.isArray(signal.contextTokens) ? signal.contextTokens : []).map(String))].sort(),
            certaintyGrade: record.certaintyGrade,
            manualStructureTags: record.structureTags,
            predictedStructureTags: record.predictedStructureTags,
            structureReview: record.structureReview,
          },
          metrics: optimizationMetrics(signal),
          visualSignature: signal.visualSignature || null,
        };
      });
    const featureCount = new Set(rows.flatMap((row) => row.featureTokens)).size;
    const supervisedPrototypeProfile = buildSupervisedPrototypeProfile(normalized);
    const aPlusPrototypeProfile = supervisedPrototypeProfile.positiveAPlus;
    const deniedPrototypeProfile = supervisedPrototypeProfile.negativeDenied;
    return {
      datasetVersion: DATASET_VERSION,
      generatedAt: normalized.updatedAt,
      causality: "decision-time-features-only",
      excludedOutcomeFields: ["futureReturn", "maxFavorableExcursion", "maxAdverseExcursion", "futureHigh", "futureLow"],
      summary: {
        total: rows.length,
        positiveCount: rows.filter((row) => row.label === 1).length,
        negativeCount: rows.filter((row) => row.label === -1).length,
        pendingCount: rows.filter((row) => row.label === 0).length,
        labeledCount: rows.filter((row) => row.label !== 0).length,
        featureCount,
        aPlusPrototypeCount: aPlusPrototypeProfile.prototypes.length,
        aPlusSampleCount: aPlusPrototypeProfile.totalAPlusSamples,
        deniedPrototypeCount: deniedPrototypeProfile.prototypes.length,
        deniedPrototypeSampleCount: deniedPrototypeProfile.totalDeniedSamples,
        visualLabeledCount: rows.filter((row) => row.label !== 0 && row.visualSignature).length,
        visualAPlusCount: rows.filter((row) => row.decision === "confirmed" && row.certaintyGrade === "A+" && row.visualSignature).length,
        visualDeniedCount: rows.filter((row) => row.decision === "denied" && row.visualSignature).length,
      },
      supervisedPrototypeProfile,
      rows,
    };
  }

  function buildWeights(document, alreadyNormalized = false) {
    const normalized = alreadyNormalized ? document : normalizeDocument(document);
    const pairDeltas = {};
    Object.values(normalized.records).forEach((record) => {
      if (record.decision === "pending" || record.decision === "cleared") return;
      const pair = String(record.pair || "UNKNOWN");
      if (!pairDeltas[pair]) pairDeltas[pair] = {};
      const confirmedWeight = { "A+": 1.5, A: 1, B: 0.5 }[record.certaintyGrade] || 1;
      const delta = record.decision === "confirmed" ? confirmedWeight : -1.25;
      featureTokens({ ...record.signal, interval: record.interval || record.signal?.interval }).forEach((token) => {
        pairDeltas[pair][token] = finite(pairDeltas[pair][token]) + delta;
      });
    });
    const weights = {};
    Object.values(pairDeltas).forEach((pairWeights) => {
      Object.entries(pairWeights).forEach(([token, delta]) => {
        // 一个被密集校对的龙头对任一特征最多贡献 ±2，防止 TUT 一段
        // 走势的几十个反例直接压过 SPK、COW 等其他龙头的有效结构。
        weights[token] = finite(weights[token]) + clamp(delta, -2, 2);
      });
    });
    return Object.fromEntries(Object.entries(weights).map(([token, value]) => [token, clamp(value, -4, 4)]));
  }

  function buildReviewProfile(document, alreadyNormalized = false) {
    const normalized = alreadyNormalized ? document : normalizeDocument(document);
    const profile = { global: {}, local: {}, summary: { globalVetoRules: 0, localVetoRules: 0 } };
    const update = (bucket, token, record) => {
      if (!bucket[token]) bucket[token] = { positiveCount: 0, negativeCount: 0, positivePairs: new Set(), negativePairs: new Set() };
      const stats = bucket[token];
      if (record.decision === "confirmed") {
        stats.positiveCount += 1;
        stats.positivePairs.add(record.pair);
      } else if (record.decision === "denied") {
        stats.negativeCount += 1;
        stats.negativePairs.add(record.pair);
      }
    };
    Object.values(normalized.records).forEach((record) => {
      if (!["confirmed", "denied"].includes(record.decision)) return;
      const pair = String(record.pair || "");
      if (!pair) return;
      if (!profile.local[pair]) profile.local[pair] = {};
      featureTokens({ ...record.signal, interval: record.interval || record.signal?.interval })
        .filter((token) => token.startsWith("interval-setup:"))
        .forEach((token) => {
          update(profile.global, token, record);
          update(profile.local[pair], token, record);
        });
    });
    const localRules = Object.values(profile.local).flatMap((bucket) => Object.values(bucket));
    profile.summary.localVetoRules = localRules.filter((stats) => (
      stats.positiveCount === 0 && stats.negativeCount >= LOCAL_ZERO_POSITIVE_VETO_MIN
    )).length;
    profile.summary.globalVetoRules = Object.values(profile.global).filter((stats) => (
      stats.positiveCount === 0
      && stats.negativeCount >= GLOBAL_ZERO_POSITIVE_VETO_MIN
      && stats.negativePairs.size >= GLOBAL_VETO_PAIR_MIN
    )).length;
    return profile;
  }

  function reviewDecision(signal, pair, profile) {
    const setupTokens = featureTokens(signal).filter((token) => token.startsWith("interval-setup:"));
    const local = profile?.local?.[String(pair || "").toUpperCase()] || {};
    for (const token of setupTokens) {
      const stats = local[token];
      if (stats && stats.positiveCount === 0 && stats.negativeCount >= LOCAL_ZERO_POSITIVE_VETO_MIN) {
        return {
          veto: true,
          scope: "local",
          token,
          positiveCount: 0,
          negativeCount: stats.negativeCount,
          reason: `人工校对同类结构：当前龙头已有 ${stats.negativeCount} 个反例且无确认，暂不自动开仓`,
        };
      }
    }
    for (const token of setupTokens) {
      const stats = profile?.global?.[token];
      if (stats
        && stats.positiveCount === 0
        && stats.negativeCount >= GLOBAL_ZERO_POSITIVE_VETO_MIN
        && stats.negativePairs.size >= GLOBAL_VETO_PAIR_MIN) {
        return {
          veto: true,
          scope: "global",
          token,
          positiveCount: 0,
          negativeCount: stats.negativeCount,
          pairCount: stats.negativePairs.size,
          reason: `人工校对同类结构：${stats.negativePairs.size} 个龙头累计 ${stats.negativeCount} 个反例且无确认，暂不自动开仓`,
        };
      }
    }
    return { veto: false };
  }

  function feedbackAdjustment(signal, weights) {
    const values = featureTokens(signal).map((token) => finite(weights?.[token])).filter((value) => value !== 0);
    if (!values.length) return 0;
    return clamp(Math.round(values.reduce((sum, value) => sum + value, 0) / values.length * 2), -6, 6);
  }

  function addWeight(signal, weights) {
    const adjustment = feedbackAdjustment(signal, weights);
    return {
      ...signal,
      feedbackAdjustment: adjustment,
      score: clamp(Math.round(finite(signal.score) + adjustment), 0, 100),
      certaintyScore: clamp(Math.round(finite(signal.certaintyScore, finite(signal.score)) + adjustment), 0, 100),
    };
  }

  function applyDecision(signal, record, weights, originalStatus, learnedReview = { veto: false }) {
    const weighted = addWeight(signal, weights);
    if (!record && learnedReview.veto) {
      return {
        ...weighted,
        status: "filtered",
        feedbackReviewVeto: true,
        feedbackReviewScope: learnedReview.scope,
        feedbackReviewToken: learnedReview.token,
        strategyStatusBeforeFeedback: originalStatus,
        reasons: [...new Set([learnedReview.reason, ...(weighted.reasons || [])])],
        evidence: [...new Set([...(weighted.evidence || []), "监督规则仅使用人工标签与买点当时可见的结构特征，不读取未来行情"] )],
      };
    }
    if (!record) return weighted;
    if (record.decision === "cleared") return weighted;
    const graded = {
      ...weighted,
      ...(record.signal?.visualSignature ? { visualSignature: record.signal.visualSignature } : {}),
      ...(Number.isFinite(Number(record.signal?.visualStructureStartIndex)) ? {
        visualStructureStartIndex: Number(record.signal.visualStructureStartIndex),
      } : {}),
      ...(Number.isFinite(Number(record.signal?.visualStructureStartTime)) ? {
        visualStructureStartTime: Number(record.signal.visualStructureStartTime),
      } : {}),
      ...(Number.isFinite(Number(record.signal?.visualStructureBars)) ? {
        visualStructureBars: Number(record.signal.visualStructureBars),
      } : {}),
      ...(record.signal?.visualStructureSource ? { visualStructureSource: String(record.signal.visualStructureSource) } : {}),
      ...(record.certaintyGrade ? { manualCertaintyGrade: record.certaintyGrade } : {}),
      ...(record.structureTags?.length ? { manualStructureTags: record.structureTags } : {}),
    };
    if (record.decision === "pending") {
      return {
        ...graded,
        manualDecision: "pending",
        strategyStatusBeforeFeedback: originalStatus,
        evidence: [...new Set([...(weighted.evidence || []), "用户标记为待定：保留复盘，不进入正负样本学习"] )],
      };
    }
    if (record.decision === "denied") {
      return {
        ...graded,
        status: "filtered",
        manualDecision: "denied",
        strategyStatusBeforeFeedback: originalStatus,
        reasons: [...new Set(["用户已否定此买点（永久反馈）", ...(weighted.reasons || [])])],
      };
    }
    const manualOverride = originalStatus !== "buy";
    return {
      ...graded,
      status: "buy",
      manualDecision: "confirmed",
      manualConfirmed: true,
      manualOverride,
      strategyStatusBeforeFeedback: originalStatus,
      reasons: [],
      evidence: [...new Set([...(weighted.evidence || []), manualOverride
        ? "用户永久确认：作为人工复盘买点恢复，不计入自动策略的因果许可"
        : "用户永久确认：策略原生买点已锁定保留"] )],
    };
  }

  function findCandleIndex(candles, time) {
    const target = Math.trunc(finite(time));
    if (!target) return -1;
    return candles.findIndex((row) => Math.trunc(finite(row?.time)) === target);
  }

  // 历史确认点必须由新策略本身重新命中，人工永久恢复只负责防止标记丢失，
  // 不能被当成“新逻辑已经兼容”的证据。审计只比较同一根 K 线在当前原始
  // 策略结果中的状态，不读取确认后的未来涨跌。
  function auditConfirmedCompatibility(result, pair, document, alreadyNormalized = false) {
    const normalized = alreadyNormalized ? document : normalizeDocument(document);
    const normalizedPair = String(pair || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
    const interval = String(result?.interval || "");
    const candles = Array.isArray(result?.candles) ? result.candles : [];
    const nativeSignals = (result?.signals || []).filter((item) => (
      !item.manualRestored
      && !(item.manualOverride && item.strategyStatusBeforeFeedback !== "buy")
    ));
    const byTime = (items) => new Map((items || []).map((item) => [Math.trunc(finite(item.time)), item]));
    const signalByTime = byTime(nativeSignals);
    const pendingByTime = byTime(result?.pending);
    const rejectedByTime = byTime(result?.rejected);
    const items = [];

    Object.values(normalized.records).forEach((record) => {
      if (record.decision !== "confirmed"
        || record.pair !== normalizedPair
        || record.interval !== interval) return;
      const time = Math.trunc(finite(record.signal?.time));
      const candleIndex = findCandleIndex(candles, time);
      if (candleIndex < 0) return;
      const native = signalByTime.get(time) || null;
      const pending = pendingByTime.get(time) || null;
      const filtered = rejectedByTime.get(time) || null;
      const strategyItem = native || pending || filtered;
      const strategyStatus = native ? "native-buy" : pending ? "pending" : filtered ? "filtered" : "missing";
      items.push({
        key: record.key,
        pair: record.pair,
        interval: record.interval,
        time,
        candleIndex,
        certaintyGrade: record.certaintyGrade,
        structureTags: record.structureTags,
        strategyStatus,
        covered: strategyStatus === "native-buy",
        conflict: strategyStatus !== "native-buy",
        strategyPattern: String(strategyItem?.pattern || ""),
        strategyPatternKey: String(strategyItem?.patternKey || ""),
        reasons: strategyStatus === "native-buy" ? [] : [...new Set((strategyItem?.reasons || []).map(String))],
      });
    });

    const conflicts = items.filter((item) => item.conflict);
    return {
      policy: "confirmed-buy-zero-regression-conflicts",
      causality: "same-candle-strategy-status-only",
      pair: normalizedPair,
      interval,
      confirmedInWindow: items.length,
      nativeBuyCount: items.length - conflicts.length,
      conflictCount: conflicts.length,
      filteredCount: conflicts.filter((item) => item.strategyStatus === "filtered").length,
      pendingCount: conflicts.filter((item) => item.strategyStatus === "pending").length,
      missingCount: conflicts.filter((item) => item.strategyStatus === "missing").length,
      passed: conflicts.length === 0,
      items,
      conflicts,
    };
  }

  function assertConfirmedCompatibility(result, pair, document) {
    const audit = auditConfirmedCompatibility(result, pair, document);
    if (!audit.passed) {
      const detail = audit.conflicts.slice(0, 5).map((item) => (
        `${item.pair} ${item.interval} ${item.time}: ${item.strategyStatus}${item.reasons[0] ? `（${item.reasons[0]}）` : ""}`
      )).join("；");
      throw new Error(`新策略遗漏 ${audit.conflictCount}/${audit.confirmedInWindow} 个历史确认买点：${detail}`);
    }
    return audit;
  }

  // “彻底否定”是确认集包含约束的另一面：新策略不能因为阈值放宽，
  // 又把同一标的、同一周期、同一根 K 线放回买点或预备触发。
  function auditDeniedCompatibility(result, pair, document, alreadyNormalized = false) {
    const normalized = alreadyNormalized ? document : normalizeDocument(document);
    const normalizedPair = String(pair || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
    const interval = String(result?.interval || "");
    const candles = Array.isArray(result?.candles) ? result.candles : [];
    const nativeSignals = (result?.signals || []).filter((item) => (
      !item.manualRestored
      && !(item.manualOverride && item.strategyStatusBeforeFeedback !== "buy")
    ));
    const byTime = (items) => new Map((items || []).map((item) => [Math.trunc(finite(item.time)), item]));
    const signalByTime = byTime(nativeSignals);
    const pendingByTime = byTime(result?.pending);
    const rejectedByTime = byTime(result?.rejected);
    const items = [];

    Object.values(normalized.records).forEach((record) => {
      if (record.decision !== "denied"
        || record.pair !== normalizedPair
        || record.interval !== interval) return;
      const time = Math.trunc(finite(record.signal?.time));
      const candleIndex = findCandleIndex(candles, time);
      if (candleIndex < 0) return;
      const native = signalByTime.get(time) || null;
      const pending = pendingByTime.get(time) || null;
      const filtered = rejectedByTime.get(time) || null;
      const strategyItem = native || pending || filtered;
      const strategyStatus = native ? "native-buy" : pending ? "pending" : filtered ? "filtered" : "absent";
      const conflict = strategyStatus === "native-buy" || strategyStatus === "pending";
      items.push({
        key: record.key,
        pair: record.pair,
        interval: record.interval,
        time,
        candleIndex,
        strategyStatus,
        excluded: !conflict,
        conflict,
        strategyPattern: String(strategyItem?.pattern || ""),
        strategyPatternKey: String(strategyItem?.patternKey || ""),
        reasons: conflict ? [...new Set((strategyItem?.reasons || []).map(String))] : [],
      });
    });

    const conflicts = items.filter((item) => item.conflict);
    return {
      policy: "denied-buy-zero-revival-conflicts",
      causality: "same-candle-strategy-status-only",
      pair: normalizedPair,
      interval,
      deniedInWindow: items.length,
      excludedCount: items.length - conflicts.length,
      conflictCount: conflicts.length,
      nativeBuyCount: conflicts.filter((item) => item.strategyStatus === "native-buy").length,
      pendingCount: conflicts.filter((item) => item.strategyStatus === "pending").length,
      passed: conflicts.length === 0,
      items,
      conflicts,
    };
  }

  function assertReviewCompatibility(result, pair, document) {
    const confirmed = auditConfirmedCompatibility(result, pair, document);
    const denied = auditDeniedCompatibility(result, pair, document);
    if (!confirmed.passed || !denied.passed) {
      const missing = confirmed.conflicts.slice(0, 3).map((item) => (
        `${item.pair} ${item.interval} ${item.time}: 确认点变为 ${item.strategyStatus}`
      ));
      const revived = denied.conflicts.slice(0, 3).map((item) => (
        `${item.pair} ${item.interval} ${item.time}: 否定点复活为 ${item.strategyStatus}`
      ));
      throw new Error(`策略集合回归失败：${[...missing, ...revived].join("；")}`);
    }
    return {
      policy: "confirmed-subset-and-denied-disjoint",
      passed: true,
      confirmed,
      denied,
    };
  }

  function dedupe(items, pair) {
    const byKey = new Map();
    items.forEach((item) => {
      const key = signalKey(pair, item) || String(item.id || `${item.interval}|${item.index}|${item.triggerPrice}`);
      const existing = byKey.get(key);
      if (!existing || (item.manualDecision === "confirmed" && existing.manualDecision !== "confirmed")) byKey.set(key, item);
    });
    return [...byKey.values()].sort((a, b) => finite(a.time) - finite(b.time) || finite(b.score) - finite(a.score));
  }

  function prepareApplicationContext(document) {
    const normalized = normalizeDocument(document);
    return {
      normalized,
      weights: buildWeights(normalized, true),
      reviewProfile: buildReviewProfile(normalized, true),
      visualPrototypeIndex: buildVisualPrototypeIndex(normalized, true),
      feedbackPrototypeProfile: buildSupervisedPrototypeProfile(normalized, true),
    };
  }

  function applyToResult(result, pair, document, preparedContext = null) {
    if (!result || !Array.isArray(result.candles)) return result;
    const prepared = preparedContext?.normalized ? preparedContext : prepareApplicationContext(document);
    const { normalized, weights, reviewProfile, visualPrototypeIndex, feedbackPrototypeProfile } = prepared;
    const confirmedCompatibility = auditConfirmedCompatibility(result, pair, normalized, true);
    const deniedCompatibility = auditDeniedCompatibility(result, pair, normalized, true);
    const compatibilityByKey = new Map(confirmedCompatibility.items.map((item) => [item.key, item]));
    const deniedCompatibilityByKey = new Map(deniedCompatibility.items.map((item) => [item.key, item]));
    const buckets = { signals: [], pending: [], rejected: [] };
    const seen = new Set();
    const process = (item, originalStatus) => {
      const key = signalKey(pair, item);
      if (key) seen.add(key);
      const record = key ? normalized.records[key] : null;
      const learnedReview = reviewDecision(item, pair, reviewProfile);
      const visualLearning = visualLearningAssessment(item, pair, normalized, visualPrototypeIndex);
      let next = applyDecision({ ...item, feedbackKey: key, visualLearning }, record, weights, originalStatus, learnedReview);
      const compatibility = key ? compatibilityByKey.get(key) : null;
      if (compatibility) {
        next = {
          ...next,
          strategyCompatibilityStatus: compatibility.strategyStatus,
          strategyRegressionConflict: compatibility.conflict,
          evidence: compatibility.conflict
            ? [...new Set([
              ...(next.evidence || []),
              `历史确认回归冲突：新策略状态为 ${compatibility.strategyStatus}，必须继续调整逻辑直到原生重新命中`,
            ])]
            : [...new Set([...(next.evidence || []), "历史确认回归通过：新策略在同一根 K 线上原生命中买点"])],
        };
      }
      const deniedCompatibilityItem = key ? deniedCompatibilityByKey.get(key) : null;
      if (deniedCompatibilityItem) {
        next = {
          ...next,
          strategyDeniedRegressionConflict: deniedCompatibilityItem.conflict,
          evidence: deniedCompatibilityItem.conflict
            ? [...new Set([
              ...(next.evidence || []),
              `历史否定回归冲突：新策略状态为 ${deniedCompatibilityItem.strategyStatus}，不得仅靠永久黑名单遮蔽`,
            ])]
            : next.evidence,
        };
      }
      if (!record
        && !next.feedbackReviewVeto
        && next.status === "filtered"
        && visualLearning.eligible
        && isSoftVisualAestheticFilter(next)) {
        next = {
          ...next,
          status: "pending",
          visualPreconfirmed: true,
          strategyStatusBeforeVision: originalStatus,
          visualOriginalReasons: [...(next.reasons || [])],
          reasons: [],
          evidence: [...new Set([
            ...(next.evidence || []),
            visualLearning.reason,
            "视觉层只比较买点当时以前的K线轮廓；安全学习阶段不能单独生成可执行B",
          ])],
        };
      }
      if (next.status === "buy") buckets.signals.push(next);
      else if (next.status === "pending") buckets.pending.push(next);
      else buckets.rejected.push(next);
    };
    (result.signals || []).forEach((item) => process(item, "buy"));
    (result.pending || []).forEach((item) => process(item, "pending"));
    (result.rejected || []).forEach((item) => process(item, "filtered"));

    Object.values(normalized.records).forEach((record) => {
      if (record.decision !== "confirmed" || record.pair !== String(pair || "").toUpperCase() || record.interval !== result.interval || seen.has(record.key)) return;
      const index = findCandleIndex(result.candles, record.signal?.time);
      if (index < 0) return;
      const restored = applyDecision({
        ...record.signal,
        interval: result.interval,
        index,
        feedbackKey: record.key,
      }, record, weights, record.signal?.status || "absent");
      const compatibility = compatibilityByKey.get(record.key);
      buckets.signals.push({
        ...restored,
        manualOverride: true,
        manualRestored: true,
        strategyCompatibilityStatus: compatibility?.strategyStatus || "missing",
        strategyRegressionConflict: compatibility?.conflict ?? true,
        evidence: [...new Set([
          ...(restored.evidence || []),
          `历史确认回归冲突：新策略状态为 ${compatibility?.strategyStatus || "missing"}，必须继续调整逻辑直到原生重新命中`,
        ])],
      });
    });

    const signals = dedupe(buckets.signals, pair);
    const pending = dedupe(buckets.pending, pair);
    const rejected = dedupe(buckets.rejected, pair);
    return {
      ...result,
      signals,
      pending,
      rejected,
      feedbackWeights: weights,
      feedbackCalibration: reviewProfile.summary,
      feedbackPrototypeProfile,
      confirmedCompatibility,
      deniedCompatibility,
      stats: {
        ...(result.stats || {}),
        signalCount: signals.length,
        pendingCount: pending.length,
        rejectedCount: rejected.length,
        manualConfirmedCount: signals.filter((item) => item.manualConfirmed).length,
        manualRestoredCount: signals.filter((item) => item.manualRestored).length,
        confirmedRegressionConflictCount: confirmedCompatibility.conflictCount,
        confirmedNativeCoverageCount: confirmedCompatibility.nativeBuyCount,
        deniedRegressionConflictCount: deniedCompatibility.conflictCount,
        deniedNativeExcludedCount: deniedCompatibility.excludedCount,
        visualPreconfirmedCount: pending.filter((item) => item.visualPreconfirmed).length,
      },
    };
  }

  return Object.freeze({
    MAX_RECORDS,
    DATASET_VERSION,
    normalizeCertaintyGrade,
    normalizeStructureTags,
    inferStructureTags,
    compareStructureTags,
    emptyDocument,
    normalizeDocument,
    mergeDocuments,
    signalKey,
    snapshotSignal,
    featureTokens,
    buildOptimizationDataset,
    buildAPlusPrototypeProfile,
    buildDeniedPrototypeProfile,
    buildSupervisedPrototypeProfile,
    buildVisualPrototypeIndex,
    visualLearningAssessment,
    buildWeights,
    buildReviewProfile,
    prepareApplicationContext,
    reviewDecision,
    feedbackAdjustment,
    auditConfirmedCompatibility,
    assertConfirmedCompatibility,
    auditDeniedCompatibility,
    assertReviewCompatibility,
    applyToResult,
  });
});
