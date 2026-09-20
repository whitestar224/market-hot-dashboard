#!/usr/bin/env node
"use strict";

// Offline validation only. Source candles, production caches and feedback are
// read-only. Writes are confined to a separate derived-report directory.
const fs = require("node:fs");
const path = require("node:path");
const zlib = require("node:zlib");
const crypto = require("node:crypto");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { performance } = require("node:perf_hooks");
const { loadFeedbackDatabase } = require("./report_dragon_wave_precompute.js");
const ROOT = path.resolve(__dirname, "..");
const DEFAULT_OUT = path.join(ROOT, "deliverables/2026-09-08-structure-optimization");
const VERIFY = path.join(ROOT, "deliverables/2026-09-07-full-precompute/verification.json");
const CACHE = path.join(ROOT, ".runtime-cache/dragon-wave-precomputed/v90");
const MS = { "5m": 300000, "15m": 900000, "1h": 3600000, "4h": 14400000, "1d": 86400000 };
const ANALYSIS_OPTIONS = { mainWaveStage: "active", mainWaveContextSource: "leader-default-main-wave", mainWaveContextLabel: "龙头默认主升浪环境" };
const CONTEXT_OPTIONS = { ...ANALYSIS_OPTIONS, preselectedLeader: true };
const TARGETS = [
  { pair: "PIUSDT", interval: "5m", local: "2025-02-22 15:30", expectation: "restore-confirmed" },
  { pair: "PIUSDT", interval: "1h", local: "2025-02-26 10:00", expectation: "restore-confirmed" },
  { pair: "TUTUSDT", interval: "15m", local: "2026-08-06 12:30", expectation: "reject-user-denied" },
  { pair: "SPKUSDT", interval: "5m", local: "2025-07-24 22:45", expectation: "reject-user-denied" },
  { pair: "NOTUSDT", interval: "1h", local: "2024-05-30 07:00", expectation: "retain-native-confirmed" },
  { pair: "TURBOUSDT", interval: "1h", local: "2024-05-24 23:00", expectation: "retain-native-confirmed" },
  { pair: "XRPUSDT", interval: "4h", local: "2024-11-22 00:00", expectation: "retain-native-confirmed" },
  { pair: "SPKUSDT", interval: "15m", local: "2025-07-22 19:30", expectation: "retain-native-confirmed" },
  { pair: "BANKUSDT", interval: "15m", local: "2026-07-17 16:45", expectation: "retain-confirmed-secondary-hint" },
].map(row => ({ ...row, time: Date.parse(`${row.local.replace(" ", "T")}:00+08:00`) }));
const read = file => JSON.parse(fs.readFileSync(file, "utf8").replace(/^\uFEFF/, ""));
const hash = data => crypto.createHash("sha256").update(data).digest("hex");
const fileHash = file => hash(fs.readFileSync(file));
const jsonHash = item => hash(JSON.stringify(item));
const localTime = time => new Date(time + 28800000).toISOString().slice(0, 16).replace("T", " ");
const signalKey = (pair, interval, time) => `${pair}|${interval}|${time}`;
const strip = value => JSON.parse(JSON.stringify(value, (key, item) => /Signature$/.test(key) ? undefined : item));
const argsOf = argv => Object.fromEntries(argv.map(arg => { const split = arg.indexOf("="); return split < 0 ? [arg.replace(/^--/, ""), true] : [arg.slice(2, split), arg.slice(split + 1)]; }));
const count = (rows, fn) => rows.reduce((all, row) => { const key = fn(row); all[key] = (all[key] || 0) + 1; return all; }, {});

function outputDir(value) {
  const directory = path.resolve(ROOT, value || DEFAULT_OUT);
  const allowed = path.join(ROOT, "deliverables") + path.sep;
  assert.ok(directory.startsWith(allowed), "Derived reports must stay under workspace deliverables/");
  fs.mkdirSync(directory, { recursive: true });
  return directory;
}
function save(directory, name, data) {
  assert.ok(/^[a-zA-Z0-9._-]+\.json$/.test(name), "Invalid report basename");
  fs.writeFileSync(path.join(directory, name), JSON.stringify(data, null, 2) + "\n");
}
function payloadFor(task) {
  assert.ok(task.file && path.basename(task.file) === task.file, "Cache basename required");
  const buffer = fs.readFileSync(path.join(CACHE, task.file));
  const payload = JSON.parse(zlib.gunzipSync(buffer));
  assert.equal(payload.key, task.key, "Source task key mismatch");
  assert.equal(payload.pair, task.pair);
  assert.equal(payload.interval, task.interval);
  assert.equal(payload.contextComplete, true);
  return { payload, sha256: hash(buffer), bytes: buffer.length };
}
function groupsAt(result, time) {
  return [["buy", result.signals], ["secondary-hint", result.secondaryBreakoutHints], ["filtered", result.rejected], ["pending", result.pending], ["retained-candidate", result.retainedCandidates], ["structure", result.structures]]
    .flatMap(([bucket, rows]) => (rows || []).filter(s => s.time === time).map(signal => ({ bucket, signal: strip(signal) })));
}
function taskFor(ready, target) {
  return ready.find(t => t.pair === target.pair && t.interval === target.interval && target.time >= Date.parse(`${t.start}T00:00:00+08:00`) && target.time <= Date.parse(`${t.end}T23:59:59.999+08:00`));
}
function snapshots(directory) {
  const verification = read(VERIFY), ready = verification.tasks.filter(t => t.state === "ready");
  const feedback = loadFeedbackDatabase(path.join(ROOT, ".runtime-cache/dragon_wave_feedback.db"));
  const denied = Object.entries(feedback.document.records).filter(([, record]) => record.decision === "denied").map(([key, record]) => {
    const sourceTasks = ready.filter(t => t.pair === record.pair && t.interval === record.interval && record.signal.time >= Date.parse(`${t.start}T00:00:00+08:00`) && record.signal.time <= Date.parse(`${t.end}T23:59:59.999+08:00`));
    return { key, pair: record.pair, interval: record.interval, time: record.signal.time, caseIds: [...new Set(sourceTasks.map(t => t.caseId))] };
  }).filter(row => row.caseIds.length);
  const reviewRows = [
    ...verification.confirmedResults.map(row => ({ ...row, group: row.native ? "confirmed-native" : "confirmed-restored" })),
    ...denied.map(row => ({ ...row, group: "denied" })),
  ];
  for (const row of reviewRows) {
    const expected = row.group === "denied" ? "denied" : "confirmed";
    assert.equal(feedback.document.records[row.key]?.decision, expected, `Feedback changed since review: ${row.key}`);
    row.localTime = localTime(row.time);
    row.observations = [];
  }
  const taskSources = [];
  for (const task of ready) {
    const { payload, sha256, bytes } = payloadFor(task);
    const source = { key: task.key, pair: task.pair, interval: task.interval, caseId: task.caseId, start: task.start, end: task.end, file: task.file, sha256, bytes, candleCount: payload.result.candles.length, firstTime: payload.result.candles[0]?.time, lastTime: payload.result.candles.at(-1)?.time, sourceEngineSha256: payload.engineSha256 };
    taskSources.push(source);
    for (const row of reviewRows.filter(row => row.pair === task.pair && row.interval === task.interval && row.caseIds.includes(task.caseId))) {
      row.observations.push({ taskKey: task.key, file: task.file, candlePresent: payload.result.candles.some(c => c.time === row.time), candidates: groupsAt(payload.result, row.time) });
    }
  }
  const data = { schema: 1, generatedAt: new Date().toISOString(), scope: "Frozen stored v90 candidate baseline; not a replay", sourceVerification: { file: VERIFY, sha256: fileHash(VERIFY), generatedAt: verification.generatedAt, summary: verification.summary }, feedbackHash: jsonHash(feedback.document), invalidFeedback: feedback.invalidRecords, productionEngineAtSnapshot: fileHash(path.join(ROOT, "dragon-wave-engine.js")), tasks: taskSources, cases: reviewRows, targets: TARGETS, summary: { readyTasks: taskSources.length, groups: count(reviewRows, row => row.group), withoutStoredCandidate: reviewRows.filter(row => row.observations.every(o => !o.candidates.length)).map(row => row.key) }, limitations: ["No missing market data fetched", "425 total tasks; only 380 previously ready tasks snapshotted", "User feedback never applied as strategy eligibility", "Stored candidates are not new native detections"] };
  save(directory, "structure-review-baseline.json", data);
  console.log(JSON.stringify({ phase: "baseline-saved", ...data.summary }));
  return data;
}
function engineAt(args) {
  const filename = path.resolve(ROOT, args.engine || "dragon-wave-engine.js");
  return { filename, sha256: fileHash(filename), api: require(filename) };
}
function helperAssessment(value, args) {
  const field = args["pass-field"] || "permit";
  const raw = typeof value === "boolean" ? value : value?.[field];
  return typeof raw === "boolean" ? (args.invert ? !raw : raw) : null;
}
function reviewCandidates(args, directory, baseline) {
  const engine = engineAt(args), helper = String(args.helper || "assessExecutionHierarchy");
  assert.equal(typeof engine.api[helper], "function", `Unknown exported helper ${helper}`);
  const rows = baseline.cases.map(row => {
    const observations = row.observations.map(observation => ({ taskKey: observation.taskKey, assessments: observation.candidates.map(candidate => {
      const signal = structuredClone(candidate.signal), before = jsonHash(signal);
      const result = engine.api[helper](signal);
      assert.equal(jsonHash(signal), before, `Helper must not mutate cached input: ${row.key}`);
      return { bucket: candidate.bucket, id: signal.id, priorStatus: signal.status, helperResult: strip(result), permit: helperAssessment(result, args) };
    }) }));
    const decisions = observations.flatMap(o => o.assessments.map(a => a.permit));
    return { key: row.key, group: row.group, localTime: row.localTime, observations, anyPermit: decisions.includes(true), anyReject: decisions.includes(false), unavailable: !decisions.length, unclassified: decisions.filter(p => p === null).length };
  });
  const report = { schema: 1, generatedAt: new Date().toISOString(), validationLevel: "stored-candidate-helper-only", engineSha256: engine.sha256, helper, passField: args["pass-field"] || "permit", invert: Boolean(args.invert), baselineSha256: fileHash(path.join(directory, "structure-review-baseline.json")), rows, summary: Object.fromEntries([...new Set(rows.map(r => r.group))].map(group => { const selected = rows.filter(r => r.group === group); return [group, { total: selected.length, anyPermit: selected.filter(r => r.anyPermit).length, unavailable: selected.filter(r => r.unavailable).length, unclassifiedCandidates: selected.reduce((sum, r) => sum + r.unclassified, 0) }]; })), limitations: ["This does not regenerate candidates from OHLC", "Lifecycle/deduplication and full multi-timeframe gates are NOT rerun", "A newly passing helper does not prove recovery of a native B", "Each candidate remains unchanged; feedback does not grant permit"] };
  assert.equal(fileHash(engine.filename), engine.sha256, "Engine changed during candidate check");
  save(directory, `${args.label || "current"}-candidate-check.json`, report);
  console.log(JSON.stringify({ phase: "candidate-only-complete", helper, summary: report.summary }));
  return report;
}
function closedPrefix(candles, interval, cutoff) {
  const rows = candles.filter(row => (row.closeTime ?? row.time + MS[interval] - 1) <= cutoff);
  assert.ok(rows.every(row => row.time <= cutoff && (row.closeTime ?? row.time + MS[interval] - 1) <= cutoff));
  return rows;
}
function rollingMean(values, period) {
  let sum = 0;
  return values.map((value, index) => {
    sum += Number(value) || 0;
    if (index >= period) sum -= Number(values[index - period]) || 0;
    return sum / Math.min(period, index + 1);
  });
}
function reviewOhlcCandidates(args, directory, baseline) {
  const engine = engineAt(args), started = performance.now(), reports = [];
  const selected = args.target ? new Set(selectedTargets(args).map(t => signalKey(t.pair, t.interval, t.time))) : null;
  const jobs = baseline.cases.filter(row => !selected || selected.has(row.key)).map(row => ({ row, observation: row.observations.find(o => o.candlePresent) })).sort((a, b) => String(a.observation?.file).localeCompare(String(b.observation?.file)) || a.row.time - b.row.time);
  let currentFile, currentPayload;
  const initialEngineHash = engine.sha256;
  const persist = state => {
    const summary = Object.fromEntries([...new Set(jobs.map(job => job.row.group))].map(group => {
      const members = reports.filter(row => row.group === group);
      return [group, { expected: jobs.filter(job => job.row.group === group).length, evaluated: members.length, rawCandidateBuy: members.filter(row => row.rawCandidateBuy).length, afterLifecycleBuy: members.filter(row => row.afterLifecycleBuy).length, missingCandle: members.filter(row => row.state === "missing-candle").length }];
    }));
    const report = { schema: 1, state, generatedAt: new Date().toISOString(), validationLevel: "single-target-OHLC-candidates-with-frozen-prior-lifecycle", engineSha256: initialEngineHash, baselineSha256: fileHash(path.join(directory, "structure-review-baseline.json")), candidateOrder: "Natural findCandidates order, evaluate all crossed candidates; no best-candidate cherry-picking", rightEdge: false, rows: reports, summary, milliseconds: performance.now() - started, limitations: ["Not a full sequential OHLC replay", "Prior native signals are frozen v90 records strictly before the target, identical on both sides; their earlier lifecycle is not recomputed", "Current multi-timeframe gates and adjacent-frame promotion are NOT run in this mode", "Manual feedback does not change candidate or lifecycle eligibility", "Current target OHLC is used only at its historical candle close; does not establish intrabar trigger latency", "A candidate-only improvement is NOT a confirmed native production B recovery"] };
    save(directory, `${args.label || "current"}-ohlc-candidate-check.json`, report);
    return report;
  };
  for (const { row, observation } of jobs) {
    if (performance.now() - started > Number(args["budget-seconds"] || 480) * 1000) { const report = persist("budget-limited"); console.log(JSON.stringify({ phase: "ohlc-candidate-budget-limited", summary: report.summary })); return report; }
    if (!observation) { reports.push({ key: row.key, group: row.group, state: "missing-candle" }); continue; }
    if (currentFile !== observation.file) {
      const task = baseline.tasks.find(t => t.key === observation.taskKey), loaded = payloadFor(task);
      assert.equal(loaded.sha256, task.sha256, `Baseline source changed: ${task.key}`);
      currentPayload = loaded.payload; currentFile = task.file;
    }
    const cutoff = row.time + MS[row.interval] - 1, prefix = closedPrefix(currentPayload.result.candles, row.interval, cutoff);
    const candles = engine.api.normalizeCandles(prefix, cutoff), index = candles.findIndex(c => c.time === row.time);
    assert.ok(index >= 0, `Exact target candle is missing: ${row.key}`);
    assert.deepEqual(candles.map(c => c.time), prefix.map(c => c.time), "Full-prefix candle indices changed");
    const indicators = { ema90: engine.api.ema(candles.map(c => c.close), 90), atr: engine.api.atr(candles, 14), volumeMean: rollingMean(candles.map(c => c.volume), 20) };
    const prior = structuredClone((currentPayload.result.signals || []).filter(s => s.time < row.time && (s.decisionTime ?? s.time) < row.time && !s.manualRestored && !(s.manualOverride && s.strategyStatusBeforeFeedback !== "buy")));
    const frozenPriorHash = jsonHash(prior), priorCount = prior.length;
    const candidates = engine.api.findCandidates(candles, index, indicators, { interval: row.interval, rightEdge: false });
    const evaluations = [];
    for (const [candidateIndex, candidate] of candidates.entries()) {
      if (!candidate.crossedLevel) { evaluations.push({ candidateIndex, crossedLevel: false, type: candidate.type }); continue; }
      const evaluation = engine.api.evaluateCandidate(candles, index, candidate, indicators, row.interval, { ...ANALYSIS_OPTIONS, now: cutoff });
      assert.ok(evaluation.featureCutoff == null || evaluation.featureCutoff < row.time, "Candidate feature cutoff is not pre-trigger");
      let lifecycle = null, accepted = false;
      if (evaluation.status === "buy") {
        lifecycle = engine.api.structureLifecycleDecision(prior, evaluation, candles, index, Math.max(indicators.atr[index - 1], 1e-8));
        if (!lifecycle.reason) {
          if (lifecycle.replacePriorId) { const oldIndex = prior.findIndex(s => s.id === lifecycle.replacePriorId); if (oldIndex >= 0) prior.splice(oldIndex, 1); }
          prior.push(lifecycle.retryMaturity ? { ...evaluation, ...(lifecycle.inheritStructureContext || {}) } : evaluation);
          accepted = true;
        }
      }
      evaluations.push({ candidateIndex, crossedLevel: true, type: candidate.type, evaluation: strip(evaluation), lifecycle: strip(lifecycle), acceptedAfterLifecycle: accepted });
    }
    reports.push({ key: row.key, group: row.group, localTime: row.localTime, taskKey: observation.taskKey, state: "evaluated", cutoff, prefixBars: candles.length, firstTime: candles[0]?.time, lastCloseTime: candles.at(-1)?.closeTime, priorCount, frozenPriorHash, candidates: evaluations, rawCandidateBuy: evaluations.some(e => e.evaluation?.status === "buy"), afterLifecycleBuy: evaluations.some(e => e.acceptedAfterLifecycle) });
    if (reports.length % 20 === 0) { persist("running"); console.log(JSON.stringify({ phase: "ohlc-candidate-progress", completed: reports.length, total: jobs.length, seconds: Math.round((performance.now() - started) / 1000) })); }
  }
  assert.equal(fileHash(engine.filename), initialEngineHash, "Engine changed during OHLC candidate check");
  const report = persist("completed"); console.log(JSON.stringify({ phase: "ohlc-candidate-complete", summary: report.summary })); return report;
}
function compact(items) {
  return items.map(({ bucket, signal }) => ({ bucket, time: signal.time, decisionTime: signal.decisionTime, status: signal.status, pattern: signal.pattern, primaryPatternKey: signal.primaryPatternKey, triggerPrice: signal.triggerPrice, consolidationBars: signal.consolidationBars, structureShape: signal.structureShape, executionHierarchy: signal.executionHierarchy, reasons: signal.reasons || [], featureCutoff: signal.featureCutoff, secondaryBreakoutHint: signal.secondaryBreakoutHint === true, markerColor: signal.markerColor, executionAllowed: signal.executionAllowed, manualConfirmed: signal.manualConfirmed === true, manualRestored: signal.manualRestored === true, feedbackKey: signal.feedbackKey }));
}
function selectedTargets(args) {
  if (!args.target) return TARGETS;
  const ids = String(args.target).split(",").map(Number);
  assert.ok(ids.every(id => Number.isInteger(id) && TARGETS[id]), "--target=0,1 uses zero-based planned target indices");
  return ids.map(id => TARGETS[id]);
}
function planReplay(args, baseline) {
  const seconds = Number(args["per-target-seconds"] || 90), budget = Number(args["budget-seconds"] || 480);
  assert.ok(seconds > 0 && seconds <= 1800 && budget > 0 && budget <= 7200, "Replay time budget out of allowed range");
  return { validationLevel: "real-OHLC-full-prefix-multiframe-replay", perTargetSeconds: seconds, totalBudgetSeconds: budget, targets: selectedTargets(args).map(target => ({ ...target, task: taskFor(baseline.tasks, target)?.key || null })), strategyOptions: { analysis: ANALYSIS_OPTIONS, context: CONTEXT_OPTIONS }, method: "Every frame is recomputed on its complete available source prefix ending at target candle close; no fixed lookback truncation, no later candles, no stored signals passed into new analysis", limitations: ["Target candle final OHLC is known only at its close: this validates historical-bar eligibility, NOT tick-exact intrabar latency", "Only fully closed context bars are visible at target close; unfinished higher bars are not fabricated", "A timeout is not a filtered signal or regression pass", "Passing selected targets does not establish full 380-task or 237-point replay coverage"] };
}
function replayWorker(args, directory, baseline) {
  const target = TARGETS[Number(args.worker)], task = taskFor(baseline.tasks, target);
  assert.ok(task, "Target not in ready baseline coverage");
  const engine = engineAt(args), cutoff = target.time + MS[target.interval] - 1;
  const frames = baseline.tasks.filter(t => t.caseId === task.caseId);
  const missing = Object.keys(MS).filter(interval => !frames.some(t => t.interval === interval));
  assert.equal(missing.length, 0, `Full-context replay needs ready frames: ${missing}`);
  const started = performance.now(), native = [], frameReports = [];
  for (const frame of [...frames].sort((a, b) => MS[b.interval] - MS[a.interval])) {
    const source = payloadFor(frame);
    assert.equal(source.sha256, frame.sha256, `Baseline cache changed: ${frame.key}`);
    const rows = closedPrefix(source.payload.result.candles, frame.interval, cutoff);
    const before = jsonHash(rows), frameStart = performance.now();
    console.log(JSON.stringify({ phase: "frame-start", pair: target.pair, interval: frame.interval, candles: rows.length }));
    const result = engine.api.analyzeTimeframe(rows, { ...ANALYSIS_OPTIONS, interval: frame.interval, now: cutoff });
    assert.equal(jsonHash(rows), before, "Engine altered source OHLC prefix");
    native.push(result);
    frameReports.push({ interval: frame.interval, candles: rows.length, firstTime: rows[0]?.time, lastCloseTime: rows.at(-1)?.closeTime, sourceSha256: source.sha256, prefixSha256: before, milliseconds: performance.now() - frameStart });
  }
  const beforeContext = jsonHash(native), contextStart = performance.now();
  const gated = engine.api.applyContextGates(native, [], CONTEXT_OPTIONS).map(engine.api.enforceIntervalStructurePolicy);
  assert.equal(jsonHash(native), beforeContext, "Context gates altered raw results");
  const result = gated.find(r => r.interval === target.interval), raw = native.find(r => r.interval === target.interval);
  const exact = compact(groupsAt(result, target.time)), rawExact = compact(groupsAt(raw, target.time));
  for (const item of exact) {
    assert.ok(item.decisionTime == null || item.decisionTime <= cutoff, "Decision uses a later candle");
    assert.ok(item.featureCutoff == null || item.featureCutoff <= cutoff, "Features use later information");
  }
  const original = payloadFor(task).payload.result;
  const isBuy = exact.some(r => r.bucket === "buy" && !r.secondaryBreakoutHint), expectedBuy = target.expectation !== "reject-user-denied";
  const nativeSecondaryHint = exact.some(r => r.bucket === "secondary-hint" && r.secondaryBreakoutHint && r.markerColor === "red" && r.executionAllowed === false);
  // Feedback is a separate visibility overlay, never an input to native analysis.
  // BANK was confirmed as a red re-entry hint, not as an additional formal buy.
  let feedbackVisibility = null;
  if (target.expectation === "retain-confirmed-secondary-hint") {
    const Feedback = require(path.join(ROOT, "dragon-wave-feedback.js"));
    const feedback = loadFeedbackDatabase(path.join(ROOT, ".runtime-cache/dragon_wave_feedback.db"));
    const feedbackHash = jsonHash(feedback.document), resultHash = jsonHash(result);
    const key = signalKey(target.pair, target.interval, target.time);
    const storedDecision = feedback.document.records[key]?.decision;
    assert.equal(storedDecision, "confirmed", "Expected an existing confirmed red hint");
    const displayed = Feedback.applyToResult(result, target.pair, feedback.document, Feedback.prepareApplicationContext(feedback.document));
    const shown = compact(groupsAt(displayed, target.time));
    const redHintVisible = shown.some(item => item.bucket === "secondary-hint" && item.secondaryBreakoutHint && item.markerColor === "red" && item.executionAllowed === false);
    const confirmedOverlayVisible = shown.some(item => item.manualConfirmed && item.feedbackKey === key);
    feedbackVisibility = { storedDecision, feedbackHash, key, exact: shown, redHintVisible, confirmedOverlayVisible, confirmedRedVisible: redHintVisible && confirmedOverlayVisible && storedDecision === "confirmed", note: "Existing UI reads confirmation by pair/interval/time feedback key. It retains the red-hint bucket plus a separately confirmed overlay; hint.manualConfirmed is not required. This check does not relabel the native hint as a formal buy." };
    assert.equal(jsonHash(feedback.document), feedbackHash, "Visibility check changed feedback");
    assert.equal(jsonHash(result), resultHash, "Visibility check changed native result");
  }
  const expectationMet = target.expectation === "retain-confirmed-secondary-hint"
    ? nativeSecondaryHint && feedbackVisibility.confirmedRedVisible
    : target.expectation === "reject-user-denied" ? !isBuy && !nativeSecondaryHint : isBuy === expectedBuy;
  const nearbyTimes = [-2, -1, 0].map(offset => target.time + offset * MS[target.interval]);
  const report = { schema: 1, state: "completed", validationLevel: "real-OHLC-full-prefix-multiframe-replay", target, engineSha256: engine.sha256, cutoff, frameReports, contextMilliseconds: performance.now() - contextStart, milliseconds: performance.now() - started, oldStoredExact: compact(groupsAt(original, target.time)), rawExact, exact, nearby: nearbyTimes.flatMap(time => compact(groupsAt(result, time))), rawStats: raw?.stats, finalStats: result?.stats, nativeBuy: isBuy, nativeSecondaryHint, feedbackVisibility, expectationMet, sourcePrefixUnchanged: true, contextInputsUnchanged: true, note: "No manual feedback used in native analysis. Optional feedbackVisibility is a separate read-only display check, not strategy eligibility. Target-bar final OHLC is evaluated only as known at target close; not an intrabar first-touch timing guarantee." };
  assert.equal(fileHash(engine.filename), engine.sha256, "Engine changed during replay");
  save(directory, `${args.label || "current"}-replay-${args.worker}.json`, report);
  console.log(JSON.stringify({ phase: "target-complete", target, nativeBuy: isBuy, expectationMet: report.expectationMet, milliseconds: report.milliseconds }));
}
function boundedChild(args, index, timeout, directory) {
  return new Promise(resolve => {
    const started = performance.now(), command = [__filename, "--mode=worker", `--worker=${index}`, `--out=${directory}`, `--label=${args.label || "current"}`];
    if (args.engine) command.push(`--engine=${args.engine}`);
    const child = spawn(process.execPath, command, { cwd: ROOT, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "", stderr = "", timedOut = false;
    child.stdout.on("data", buffer => { const chunk = String(buffer); stdout = (stdout + chunk).slice(-12000); process.stdout.write(chunk); });
    child.stderr.on("data", buffer => { stderr = (stderr + String(buffer)).slice(-12000); });
    const timer = setTimeout(() => { timedOut = true; child.kill(); }, timeout);
    child.on("error", error => { clearTimeout(timer); resolve({ targetIndex: index, state: "error", error: error.message }); });
    child.on("close", code => { clearTimeout(timer); resolve({ targetIndex: index, state: timedOut ? "timeout" : code === 0 ? "completed" : "error", exitCode: code, milliseconds: performance.now() - started, stdout, stderr }); });
  });
}
async function replay(args, directory, baseline) {
  const plan = planReplay(args, baseline), started = performance.now(), observations = [];
  for (const target of plan.targets) {
    const remaining = plan.totalBudgetSeconds * 1000 - (performance.now() - started);
    const index = TARGETS.findIndex(t => t.pair === target.pair && t.interval === target.interval && t.time === target.time);
    if (remaining <= 0) { observations.push({ targetIndex: index, state: "budget-not-run" }); continue; }
    observations.push(await boundedChild(args, index, Math.min(remaining, plan.perTargetSeconds * 1000), directory));
    save(directory, `${args.label || "current"}-replay-run.json`, { plan, observations, milliseconds: performance.now() - started });
  }
  save(directory, `${args.label || "current"}-replay-run.json`, { plan, observations, milliseconds: performance.now() - started, summary: count(observations, row => row.state) });
  console.log(JSON.stringify({ phase: "bounded-replay-finished", summary: count(observations, row => row.state), notFullBatch: true }));
}
function compareReports(args, directory) {
  const beforeLabel = args.before || "baseline", afterLabel = args.after || "current";
  const before = read(path.join(directory, `${beforeLabel}-ohlc-candidate-check.json`));
  const after = read(path.join(directory, `${afterLabel}-ohlc-candidate-check.json`));
  assert.equal(before.baselineSha256, after.baselineSha256, "Comparisons must use one frozen source baseline");
  const beforeRows = new Map(before.rows.map(row => [row.key, row]));
  const changes = [];
  for (const next of after.rows) {
    const prior = beforeRows.get(next.key);
    assert.ok(prior, `New target not in baseline candidate run: ${next.key}`);
    for (const field of ["taskKey", "cutoff", "prefixBars", "firstTime", "lastCloseTime", "frozenPriorHash"]) assert.equal(next[field], prior[field], `Input mismatch ${field}: ${next.key}`);
    if (prior.rawCandidateBuy !== next.rawCandidateBuy || prior.afterLifecycleBuy !== next.afterLifecycleBuy) {
      changes.push({ key: next.key, group: next.group, localTime: next.localTime, beforeRaw: prior.rawCandidateBuy, afterRaw: next.rawCandidateBuy, beforeLifecycle: prior.afterLifecycleBuy, afterLifecycle: next.afterLifecycleBuy, beforeReasons: prior.candidates.flatMap(c => [...(c.evaluation?.reasons || []), c.lifecycle?.reason].filter(Boolean)), afterReasons: next.candidates.flatMap(c => [...(c.evaluation?.reasons || []), c.lifecycle?.reason].filter(Boolean)) });
    }
  }
  const beforeReplayLabels = String(args["before-replay-labels"] || `${beforeLabel},${beforeLabel}-more`).split(",");
  const afterReplayLabels = String(args["after-replay-labels"] || afterLabel).split(",");
  const replayComparisons = TARGETS.map((target, index) => {
    const find = labels => labels.map(label => path.join(directory, `${label}-replay-${index}.json`)).find(fs.existsSync);
    const beforeFile = find(beforeReplayLabels), afterFile = find(afterReplayLabels);
    if (!beforeFile || !afterFile) return { targetIndex: index, target, state: "not-compared", beforeAvailable: !!beforeFile, afterAvailable: !!afterFile };
    const oldResult = read(beforeFile), newResult = read(afterFile);
    assert.equal(oldResult.cutoff, newResult.cutoff, "Full replay cutoff mismatch");
    assert.deepEqual(oldResult.frameReports.map(f => [f.interval, f.prefixSha256]), newResult.frameReports.map(f => [f.interval, f.prefixSha256]), "Full replay OHLC prefixes differ");
    return { targetIndex: index, target, state: "compared", beforeNativeBuy: oldResult.nativeBuy, afterNativeBuy: newResult.nativeBuy, beforeSecondaryHint: oldResult.nativeSecondaryHint ?? oldResult.exact.some(item => item.bucket === "secondary-hint"), afterSecondaryHint: newResult.nativeSecondaryHint, afterConfirmedRedVisible: newResult.feedbackVisibility?.confirmedRedVisible ?? null, expectationMet: newResult.expectationMet, beforeMilliseconds: oldResult.milliseconds, afterMilliseconds: newResult.milliseconds, beforeEngineSha256: oldResult.engineSha256, afterEngineSha256: newResult.engineSha256 };
  });
  const report = { schema: 1, generatedAt: new Date().toISOString(), candidateOnly: { beforeSummary: before.summary, afterSummary: after.summary, sameInputRowsCompared: after.rows.length, expectedRows: before.rows.length, changes, confirmedNativeLifecycleLosses: changes.filter(c => c.group === "confirmed-native" && c.beforeLifecycle && !c.afterLifecycle), userDeniedLifecycleGains: changes.filter(c => c.group === "denied" && !c.beforeLifecycle && c.afterLifecycle) }, fullReplay: { comparisons: replayComparisons, completed: replayComparisons.filter(c => c.state === "compared").length, notCompared: replayComparisons.filter(c => c.state !== "compared").length }, limits: ["Candidate comparison does not establish native production B recovery", "Only fullReplay.comparisons with state=compared were actually rerun through all causal context gates", "No full 380-task replay or profitability backtest performed", "Timeout/unfinished targets are not counted as a passing test"] };
  save(directory, `${afterLabel}-validation-comparison.json`, report);
  console.log(JSON.stringify({ phase: "comparison-saved", candidateRowsCompared: after.rows.length, candidateChanges: changes.length, confirmedCandidateLosses: report.candidateOnly.confirmedNativeLifecycleLosses.length, deniedCandidateGains: report.candidateOnly.userDeniedLifecycleGains.length, fullReplayCompared: report.fullReplay.completed }));
  return report;
}
async function main() {
  const args = argsOf(process.argv.slice(2)), directory = outputDir(args.out), mode = args.mode || "plan";
  if (mode === "self-test") {
    assert.equal(selectedTargets({ target: "0,4" }).length, 2);
    assert.deepEqual(closedPrefix([{ time: 100, closeTime: 199 }, { time: 200, closeTime: 299 }], "5m", 250).map(r => r.time), [100]);
    assert.equal(helperAssessment({ permit: true }, {}), true);
    assert.equal(helperAssessment({ reject: true }, { "pass-field": "reject", invert: true }), false);
    assert.equal(helperAssessment({}, {}), null);
    assert.equal(groupsAt({ signals: [{ time: 1 }, { time: 2 }], rejected: [{ time: 1 }] }, 1).length, 2);
    console.log("6 validation utility checks passed"); return;
  }
  if (mode === "baseline") { snapshots(directory); return; }
  if (mode === "compare") { compareReports(args, directory); return; }
  const baseline = read(path.join(directory, "structure-review-baseline.json"));
  if (mode === "candidate") { reviewCandidates(args, directory, baseline); return; }
  if (mode === "ohlc-candidate") { reviewOhlcCandidates(args, directory, baseline); return; }
  if (mode === "worker") { replayWorker(args, directory, baseline); return; }
  if (mode === "replay") { await replay(args, directory, baseline); return; }
  assert.equal(mode, "plan", "--mode must be plan, baseline, candidate, ohlc-candidate, replay, compare or self-test");
  const plan = planReplay(args, baseline); save(directory, "structure-review-validation-plan.json", plan); console.log(JSON.stringify(plan, null, 2));
}
if (require.main === module) main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
module.exports = { closedPrefix, groupsAt, selectedTargets, helperAssessment, TARGETS };
