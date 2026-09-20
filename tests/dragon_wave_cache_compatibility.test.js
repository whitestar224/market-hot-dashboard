"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const zlib = require("node:zlib");
const Compatibility = require("../tools/dragon_wave_cache_compatibility.js");
const Report = require("../tools/report_dragon_wave_precompute.js");
const Data = require("../dragon-wave-data.js");

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dragon-wave-compatibility-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const baselineRoot = path.join(root, "baseline"), fixtureRoot = path.join(root, "tests/fixtures");
  const cacheRoot = path.join(root, ".runtime-cache/dragon-wave-precomputed/v90");
  for (const directory of [baselineRoot, fixtureRoot, cacheRoot]) fs.mkdirSync(directory, { recursive: true });
  for (const [directory, prefix] of [[root, "new"], [baselineRoot, "old"]]) {
    fs.writeFileSync(path.join(directory, "dragon-wave-engine.js"), `${prefix}-engine`);
    fs.writeFileSync(path.join(directory, "dragon-wave-vision.js"), `${prefix}-vision`);
  }
  for (const file of Compatibility.REQUIRED_FIXTURES) fs.writeFileSync(path.join(fixtureRoot, file), "[1,2,3]");
  const source = Compatibility.fingerprint(baselineRoot), target = Compatibility.fingerprint(root);
  const report = { passed: true, productionOptions: structuredClone(Compatibility.PRODUCTION_OPTIONS),
    oldEngineSha256: source.engineSha256, oldVisionSha256: source.visionSha256,
    newEngineSha256: target.engineSha256, newVisionSha256: target.visionSha256,
    comparisons: Compatibility.REQUIRED_COMPARISONS.map(label => ({ label, equal: true, beforeMs: 2, afterMs: 1,
      ...(Compatibility.REQUIRED_FIXTURES.includes(label) ? { candleCount: 40,
        fixtureSha256: Compatibility.sha256(fs.readFileSync(path.join(fixtureRoot, label))) } : {}),
      ...(label.startsWith("PI derived") ? { candleCount: 5, fullBucketsOnly: true } : {}),
      ...(label.startsWith("Synthetic") ? { candleCount: 80, synthetic: true } : {}),
      ...(label.startsWith("PI full causal context") ? { beforeRebuildCalls: 2, afterRebuildCalls: 2, rawInputsUnchanged: true } : {}),
    })) };
  const parityReportPath = path.join(root, "parity.json");
  const writeReport = value => fs.writeFileSync(parityReportPath, JSON.stringify(value));
  writeReport(report);
  const catalog = [{ valid: true, id: "sample", pair: "SAMPLEUSDT", symbol: "SAMPLE", start: "2024-01-01", end: "2024-01-03" }];
  const now = Date.parse("2025-01-01T00:00:00Z"), intervals = [...Compatibility.PRODUCTION_OPTIONS.intervals];
  const tasks = Report.expectedTasks(catalog, "v90", intervals), caseKey = Compatibility.caseKeyForRecord(tasks[0].key);
  const manifest = { version: "v90", status: { state: "complete", fullBatch: true }, failures: {}, records: {}, cases: {
    [caseKey]: { state: "complete", engineSha256: source.engineSha256, intervals,
      completedIntervals: intervals.slice(), failedIntervals: [], completedAt: now },
  } };
  for (const task of tasks) {
    const ms = Data.INTERVALS[task.interval].ms, window = Data.buildCaseWindow(task.start, task.end, task.interval, now);
    const candles = Array.from({ length: Math.ceil((window.end - window.start + 1) / ms) }, (_, index) => ({
      time: window.start + index * ms, closeTime: window.start + (index + 1) * ms - 1,
      open: 1, high: 1.1, low: 0.9, close: 1.03, volume: 100,
    }));
    const payload = { schema: 1, version: "v90", ...task, engineSha256: source.engineSha256,
      generatedAt: now - 1000, contextComplete: true, contextIntervals: intervals,
      result: { interval: task.interval, candles, signals: [], pending: [], rejected: [], retainedCandidates: [],
        secondaryBreakoutHints: [], structures: [], indicators: { ema90: candles.map(() => 1) }, stats: {} } };
    const file = `${Compatibility.sha256(task.key)}.json.gz`;
    fs.writeFileSync(path.join(cacheRoot, file), zlib.gzipSync(JSON.stringify(payload)));
    manifest.records[task.key] = { ...task, file, engineSha256: source.engineSha256, generatedAt: payload.generatedAt,
      contextComplete: true, contextIntervals: intervals, compactSchema: 1, candleCount: candles.length };
  }
  const options = { workspaceRoot: root, cacheRoot, version: "v90", engineSha256: target.engineSha256,
    productionOptions: Compatibility.PRODUCTION_OPTIONS };
  const build = () => Compatibility.buildCertificate({ manifest, cacheRoot, workspaceRoot: root, baselineRoot,
    parityReportPath, expectedSource: source, now });
  const publish = certificate => fs.writeFileSync(path.join(path.dirname(cacheRoot), "v90.compatibility.json"), JSON.stringify(certificate));
  return { root, baselineRoot, fixtureRoot, cacheRoot, parityReportPath, source, target, report, writeReport,
    catalog, now, intervals, tasks, caseKey, manifest, options, build, publish };
}

test("certificate building is read-only and binds the original five files, timestamps, and source", (t) => {
  const h = fixture(t), original = structuredClone(h.manifest);
  const snapshots = Object.fromEntries(h.tasks.map(task => {
    const file = h.manifest.records[task.key].file;
    return [file, fs.readFileSync(path.join(h.cacheRoot, file))];
  }));
  const certificate = h.build();
  assert.deepEqual(h.manifest, original);
  assert.deepEqual(certificate.source, h.source);
  assert.deepEqual(certificate.target, h.target);
  assert.equal(Object.keys(certificate.cases).length, 1);
  assert.equal(Object.keys(certificate.cases[h.caseKey].records).length, 5);
  for (const [file, bytes] of Object.entries(snapshots)) assert.deepEqual(fs.readFileSync(path.join(h.cacheRoot, file)), bytes);
  assert.equal(fs.existsSync(path.join(path.dirname(h.cacheRoot), "v90.compatibility.json")), false);
});

test("a valid direct certificate makes old complete files verifiable without claiming current-engine output", (t) => {
  const h = fixture(t), original = structuredClone(h.manifest);
  h.publish(h.build());
  const policy = Compatibility.loadCompatibility(h.options);
  assert.equal(policy.available, true);
  const assessment = policy.assessCase(h.manifest, h.caseKey);
  assert.equal(assessment.compatible, true);
  assert.equal(assessment.sourceEngineSha256, h.source.engineSha256);
  const report = Report.buildReport(h.manifest, { ...h.options, intervals: h.intervals, catalog: h.catalog,
    compatibility: policy, verifyResults: true, now: h.now });
  assert.equal(report.summary.writtenCurrentEngine, 0);
  assert.equal(report.summary.writtenCompatibleEngine, 5);
  assert.equal(report.summary.readyCompatibleEngine, 5);
  assert.equal(report.summary.verifiedTasks, 5);
  assert.equal(report.completed, true);
  assert.ok(report.tasks.every(task => task.compatibilityEvidenceId === policy.evidenceId));
  assert.match(Report.formatMarkdown(report), /已验证兼容的原结果：5项/);
  assert.deepEqual(h.manifest, original);
});

test("parity evidence requires all named comparisons, real rebuilds, raw invariance, and actual fixture hashes", (t) => {
  const h = fixture(t);
  const mutations = [
    value => { value.passed = false; },
    value => { value.comparisons.pop(); },
    value => { value.comparisons[1].label = value.comparisons[0].label; },
    value => { value.comparisons[0].equal = false; },
    value => { value.comparisons[0].fixtureSha256 = "0".repeat(64); },
    value => { value.comparisons[0].candleCount = 0; },
    value => { value.productionOptions.context.preselectedLeader = false; },
    value => { value.newVisionSha256 = "0".repeat(64); },
    value => { value.comparisons.find(entry => entry.label === "PI full causal context leader-active").afterRebuildCalls = 0; },
    value => { value.comparisons.find(entry => entry.label === "PI full causal context no-leader").rawInputsUnchanged = false; },
    value => { value.comparisons.find(entry => entry.label === "PI derived 1h").fullBucketsOnly = false; },
    value => { value.comparisons.find(entry => entry.label.startsWith("Synthetic 4h")).candleCount = 30; },
  ];
  for (const mutate of mutations) {
    const changed = structuredClone(h.report); mutate(changed); h.writeReport(changed);
    assert.throws(h.build, /兼容凭据/);
  }
});

test("missing evidence is closed by default and altered report, vision, source, or version is rejected", (t) => {
  const h = fixture(t);
  assert.equal(Compatibility.loadCompatibility(h.options).available, false);
  assert.equal(Compatibility.loadCompatibility(h.options).error, null);
  const certificate = h.build();
  for (const alter of [value => { value.version = "v91"; }, value => { value.target.engineSha256 = "0".repeat(64); },
    value => { value.source.visionSha256 = "0".repeat(64); }, value => { value.evidence.parityReportSha256 = "0".repeat(64); }]) {
    const changed = structuredClone(certificate); alter(changed);
    assert.throws(() => Compatibility.createPolicy(changed, h.options), /兼容凭据/);
  }
  h.publish(certificate);
  fs.appendFileSync(h.parityReportPath, " ");
  assert.equal(Compatibility.loadCompatibility(h.options).available, false);
  h.writeReport(h.report);
  fs.writeFileSync(path.join(h.root, "dragon-wave-vision.js"), "different-vision");
  assert.equal(Compatibility.loadCompatibility(h.options).available, false);
});

test("a certificate is detached from its caller and cannot authorize another case or changed file", (t) => {
  const h = fixture(t), certificate = h.build(), policy = Compatibility.createPolicy(certificate, h.options);
  delete certificate.cases[h.caseKey];
  assert.equal(policy.assessCase(h.manifest, h.caseKey).compatible, true);
  assert.equal(policy.assessCase(h.manifest, h.caseKey.replace("SAMPLEUSDT", "OTHERUSDT")).compatible, false);
  assert.equal(policy.assessCase(h.manifest, h.caseKey, ["1h"]).compatible, false);
  const record = h.manifest.records[h.tasks[0].key];
  const file = path.join(h.cacheRoot, record.file), payload = JSON.parse(zlib.gunzipSync(fs.readFileSync(file)));
  payload.result.stats.extra = "changed";
  fs.writeFileSync(file, zlib.gzipSync(JSON.stringify(payload)));
  assert.equal(policy.assessCase(h.manifest, h.caseKey).compatible, false);
});

test("partial cases, stale record identity, missing files, and prior failures remain unusable", (t) => {
  const h = fixture(t), certificate = h.build(), policy = Compatibility.createPolicy(certificate, h.options);
  const original = structuredClone(h.manifest), key = h.tasks[0].key;
  for (const mutate of [
    value => { value.cases[h.caseKey].state = "partial"; },
    value => { value.cases[h.caseKey].completedAt++; },
    value => { value.cases[h.caseKey].completedIntervals.pop(); },
    value => { value.records[key].contextComplete = false; },
    value => { value.records[key].pair = "OTHERUSDT"; },
    value => { value.records[key].generatedAt++; },
    value => { value.records[key].engineSha256 = h.target.engineSha256; },
    value => { value.failures[key] = { message: "still failed" }; },
  ]) {
    const changed = structuredClone(original); mutate(changed);
    assert.equal(policy.assessCase(changed, h.caseKey).compatible, false);
    const report = Report.buildReport(changed, { ...h.options, catalog: h.catalog, compatibility: policy });
    assert.equal(report.summary.readyCompatibleEngine, 0);
  }
  fs.unlinkSync(path.join(h.cacheRoot, original.records[key].file));
  assert.equal(policy.assessCase(h.manifest, h.caseKey).compatible, false);
});

test("only direct source-to-current compatibility is accepted, with no A-to-B-to-C inference", (t) => {
  const h = fixture(t), certificate = h.build();
  fs.writeFileSync(path.join(h.root, "dragon-wave-engine.js"), "third-engine");
  const target = Compatibility.fingerprint(h.root);
  assert.throws(() => Compatibility.createPolicy(certificate, { ...h.options, engineSha256: target.engineSha256 }), /目标/);
  certificate.target = target;
  assert.throws(() => Compatibility.createPolicy(certificate, { ...h.options, engineSha256: target.engineSha256 }), /报告/);
});

test("standalone result verification cannot treat unbound JSON as a compatible cached payload", (t) => {
  const h = fixture(t), policy = Compatibility.createPolicy(h.build(), h.options);
  const assessment = policy.assessCase(h.manifest, h.caseKey), task = h.tasks[0];
  const payload = assessment.payloads[task.key];
  const options = { ...h.options, intervals: h.intervals, now: h.now, compatibilityAssessment: assessment };
  assert.equal(Report.verifyStoredResult(payload, task, options).valid, true);
  assert.equal(Report.verifyStoredResult(structuredClone(payload), task, options).valid, false);
});
