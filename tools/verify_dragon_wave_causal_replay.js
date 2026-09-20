#!/usr/bin/env node
"use strict";

// Local-only parity/performance probe. It invokes the real causal rebuild in a
// real context-cache scope; instrumentation never replaces strategy decisions.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const zlib = require("node:zlib");
const assert = require("node:assert/strict");
const Module = require("node:module");
const { performance } = require("node:perf_hooks");
const ROOT = path.resolve(__dirname, "..");
const hash = value => crypto.createHash("sha256").update(value).digest("hex");
const fileHash = file => hash(fs.readFileSync(file));
const resultHash = result => hash(JSON.stringify(result, (_key, value) => {
  if (value === undefined) return { __parityType: "undefined" };
  if (typeof value === "number" && !Number.isFinite(value)) return { __parityNumber: String(value) };
  return value;
}));

function raw(interval) {
  const key = ["TRBUSDT", "2023-08-25", "2024-01-01", interval, "futures"].join("|");
  const file = path.join(ROOT, ".runtime-cache/dragon-wave-candles", `${hash(key)}.json.gz`);
  const payload = JSON.parse(zlib.gunzipSync(fs.readFileSync(file)));
  assert.equal(payload.interval, interval);
  assert.equal(payload.pair, "TRBUSDT");
  assert.ok(payload.candles.length > 1000);
  return { file, fileSha256: fileHash(file), candles: payload.candles };
}

function load(filename) {
  const loaded = new Module(filename, module);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  loaded.work = 0;
  loaded.analyses = new Map();
  loaded.rebuilt = [];
  let source = fs.readFileSync(filename, "utf8");
  const inject = (declaration, body) => {
    assert.equal(source.split(declaration).length, 2, `Unique instrumentation: ${declaration}`);
    source = source.replace(declaration, `${declaration}\n${body}`);
  };
  inject("function findCandidates(candles, index, indicators, options = {}) {", "module.work++;");
  inject("function promoteLowerFrameIgnitionToParent(byInterval, result, signal, preselectedLeader, mainWaveStage) {", `
    if (signal.__parityRequest) {
      const request = signal.__parityRequest;
      module.currentRequest = request.label;
      const start = performance.now();
      const value = rebuildCausalParentAtChild(
        request.parent, request.lower, { time: request.parentStart },
        { index: request.childIndex, triggerPrice: request.triggerPrice },
        request.interval, request.options);
      module.rebuilt.push({ label: request.label, value, ms: performance.now() - start });
      return signal;
    }
  `);
  inject("return Object.freeze({", ""); // assert the single export site before wrapping
  source = source.replace("return Object.freeze({", `
    const originalAnalyzeForParity = analyzeTimeframe;
    analyzeTimeframe = function observedAnalysis(rows, options) {
      const value = originalAnalyzeForParity(rows, options);
      if (value) module.analyses.set(module.currentRequest, module.hashResult(value));
      return value;
    };
    return Object.freeze({`);
  loaded.hashResult = resultHash;
  // performance is available globally in Node; no market/network shims.
  loaded._compile(source, filename);
  return loaded;
}

function requestsFor(interval, parent, lower, parentTime, triggerPrice, label) {
  const parentMs = interval === "1h" ? 3600000 : 900000;
  const first = lower.candles.findIndex(row => row.time === parentTime);
  assert.ok(first >= 0, "Exact parent opening must be present in 5m data");
  const count = parentMs / 300000;
  return [0, count - 1, count, count + 1, count - 1].map((offset, index) => ({
    label: `${label}-${index}`, parent, lower, interval, triggerPrice,
    parentStart: parentTime + Math.floor(offset / count) * parentMs,
    childIndex: first + offset,
    options: { mainWaveStage: "active", allowPreconfirmedParent: index !== 4 },
  }));
}

function run(loaded, requests) {
  const inputs = [...new Set(requests.flatMap(request => [request.parent, request.lower]))];
  const rawBefore = resultHash(inputs);
  const start = performance.now(), cpuStart = process.cpuUsage();
  loaded.exports.applyContextGates([{
    interval: "1d", signals: requests.map(request => ({ __parityRequest: request })),
  }], [], { preselectedLeader: true, mainWaveStage: "active" });
  const cpu = process.cpuUsage(cpuStart), elapsed = performance.now() - start;
  assert.equal(resultHash(inputs), rawBefore, "Raw parent/child inputs must remain unchanged");
  return { ms: elapsed, cpuMs: (cpu.user + cpu.system) / 1000,
    candidateScans: loaded.work, rawInputsUnchanged: true };
}

function main() {
  const baselineArg = process.argv.find(arg => arg.startsWith("--baseline="))?.slice(11);
  const outputArg = process.argv.find(arg => arg.startsWith("--output="))?.slice(9);
  assert.ok(baselineArg && outputArg, "Pass --baseline=<preserved directory> --output=<report path>");
  const baseline = path.resolve(baselineArg), oldFile = path.join(baseline, "dragon-wave-engine.js");
  const newFile = path.join(ROOT, "dragon-wave-engine.js");
  const initialHashes = { oldEngineSha256: fileHash(oldFile), newEngineSha256: fileHash(newFile),
    oldVisionSha256: fileHash(path.join(baseline, "dragon-wave-vision.js")),
    newVisionSha256: fileHash(path.join(ROOT, "dragon-wave-vision.js")) };
  const rawFrames = { "5m": raw("5m"), "15m": raw("15m"), "1h": raw("1h") };
  const lower = { candles: rawFrames["5m"].candles };
  const frames = Object.fromEntries(["15m", "1h"].map(interval => [interval, { candles: rawFrames[interval].candles }]));
  const requests = [
    ...requestsFor("1h", frames["1h"], lower, 1693227600000, 13.900020022129013, "TRB-hour-positive"),
    ...requestsFor("15m", frames["15m"], lower, 1693035900000, 10.833958300922998, "TRB-15m-positive"),
  ];
  if (!process.argv.includes("--quick")) {
    requests.push(...requestsFor("1h", frames["1h"], lower, 1703941200000, 249.44369293816868, "TRB-hour-long-history"));
    requests.push(...requestsFor("15m", frames["15m"], lower, 1703977200000, 258.92, "TRB-15m-long-history"));
  }
  const oldModule = load(oldFile), newModule = load(newFile);
  console.log(JSON.stringify({ phase: "baseline", requests: requests.length }));
  const before = run(oldModule, requests);
  console.log(JSON.stringify({ phase: "baseline-complete", ...before }));
  const after = run(newModule, requests);
  assert.equal(oldModule.rebuilt.length, requests.length);
  assert.equal(newModule.rebuilt.length, requests.length);
  const comparisons = requests.map((request, index) => {
    const oldResult = oldModule.rebuilt[index], newResult = newModule.rebuilt[index];
    assert.deepStrictEqual(newResult.value, oldResult.value, request.label);
    const oldFull = oldModule.analyses.get(request.label), newFull = newModule.analyses.get(request.label);
    // An impossible-tail prefilter may omit a full analysis only for a null result.
    if (oldFull && newFull) assert.equal(newFull, oldFull, `Full analysis hash: ${request.label}`);
    if (oldFull && !newFull) assert.equal(newResult.value, null);
    return { label: request.label, equal: true, nonNull: !!newResult.value,
      parentStart: request.parentStart, cutoff: lower.candles[request.childIndex].closeTime,
      parentHistoryCandles: request.parent.candles.filter(row => row.time < request.parentStart).length,
      beforeMs: oldResult.ms, afterMs: newResult.ms,
      fullAnalysisCompared: !!oldFull && !!newFull,
      fullAnalysisSha256: newFull || null };
  });
  assert.ok(comparisons.filter(item => item.nonNull).length >= 2, "Must exercise genuine parent buy/pending results");
  assert.ok(after.candidateScans < before.candidateScans, "Repeated full candidate scans must decrease");
  assert.equal(fileHash(oldFile), initialHashes.oldEngineSha256, "Baseline changed during verification");
  assert.equal(fileHash(newFile), initialHashes.newEngineSha256, "Engine changed during verification");
  assert.equal(fileHash(path.join(ROOT, "dragon-wave-vision.js")), initialHashes.newVisionSha256, "Vision changed during verification");
  const report = { passed: true, generatedAt: new Date().toISOString(), quick: process.argv.includes("--quick"),
    ...initialHashes,
    fixtures: Object.entries(rawFrames).map(([interval, frame]) => ({ interval, file: frame.file,
      sha256: frame.fileSha256, candles: frame.candles.length })),
    before, after, comparisons,
    note: "Real TRB candles, unchanged full historical prefixes and causal cutoffs. Context entry instrumentation dispatches only these explicit rebuild requests; this is not a whole-batch forecast." };
  const output = path.resolve(outputArg);
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify({ passed: true, before, after, comparisons: comparisons.length }));
}

if (require.main === module) main();
