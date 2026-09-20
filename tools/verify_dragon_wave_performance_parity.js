#!/usr/bin/env node
"use strict";

// Explicit local baseline comparison. Never modifies production caches or feedback.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const assert = require("node:assert/strict");
const Module = require("node:module");
const { performance } = require("node:perf_hooks");
const ROOT = path.resolve(__dirname, "..");
const Data = require("../dragon-wave-data.js");
const fixtures = [
  ["H", "1h", "husdt_1h_2025-06-29_2025-07-01.json"],
  ["H", "5m", "husdt_5m_2025-07-02_1045.json"],
  ["PI", "5m", "piusdt_okx_5m_2025-02-22_1530.json"],
  ["PI", "5m", "piusdt_okx_5m_2025-02-22_1905.json"],
  ["MMT", "15m", "mmtusdt_15m_2026-07-28_2026-07-30.json"],
  ["NOT", "1h", "notusdt_1h_2024-05-20_2024-05-30.json"],
  ["BASED", "5m", "basedusdt_binance_5m_2026-08-16_1540.json"],
  ["TURBO", "15m", "turbousdt_okx_15m_2024-05-23_2315.json"],
  ["TURBO", "15m", "turbousdt_okx_15m_2024-05-21_2024-05-25.json"],
  ["PI", "5m", "piusdt_okx_5m_2025-02-23_1115.json"],
];
const { PRODUCTION_OPTIONS } = require("./dragon_wave_cache_compatibility.js");
const options = { now: 1_800_000_000_000, ...PRODUCTION_OPTIONS.context };
const analysisOptions = { now: options.now, ...PRODUCTION_OPTIONS.analysis };
const digest = filename => crypto.createHash("sha256").update(fs.readFileSync(filename)).digest("hex");

function loadCountedEngine(filename) {
  const loaded = new Module(filename, module);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  loaded.parityRebuildCalls = 0;
  const source = fs.readFileSync(filename, "utf8"), declaration = "function rebuildCausalParentAtChild(parentResult, lowerFrame, parentSignal, childSignal, parentInterval, options = {}) {";
  assert.equal(source.split(declaration).length, 2, "Causal rebuild instrumentation must match exactly once");
  // In-memory diagnostic only: count calls without replacing their implementation.
  loaded._compile(source.replace(declaration, `${declaration}\n    module.parityRebuildCalls++;`), filename);
  return loaded;
}

function aggregate(candles, intervalMs, childMs) {
  const buckets = new Map();
  for (const row of candles) {
    const time = Math.floor(row.time / intervalMs) * intervalMs;
    if (!buckets.has(time)) buckets.set(time, []);
    buckets.get(time).push(row);
  }
  return [...buckets].filter(([time, rows]) => rows.length === intervalMs / childMs
    && rows.every((row, index) => row.time === time + index * childMs
      && row.closeTime === row.time + childMs - 1)).map(([time, rows]) => ({
    time, closeTime: time + intervalMs - 1, open: rows[0].open, close: rows.at(-1).close,
    high: Math.max(...rows.map(row => row.high)), low: Math.min(...rows.map(row => row.low)),
    ...Object.fromEntries(["volume", "quoteVolume", "takerBuyVolume", "tradeCount"]
      .map(field => [field, rows.reduce((sum, row) => sum + (row[field] || 0), 0)])),
  }));
}

function measure(label, oldCall, newCall, reverse = false) {
  let before, after, beforeMs, afterMs;
  const runOld = () => { const start = performance.now(); before = oldCall(); beforeMs = performance.now() - start; };
  const runNew = () => { const start = performance.now(); after = newCall(); afterMs = performance.now() - start; };
  if (reverse) { runNew(); runOld(); } else { runOld(); runNew(); }
  assert.deepStrictEqual(after, before, `Full result differs: ${label}`);
  const record = { label, equal: true, beforeMs, afterMs, ratio: beforeMs / Math.max(afterMs, 0.001) };
  console.log(JSON.stringify(record));
  return { record, before, after };
}

function main() {
  const baselineArg = process.argv.find(arg => arg.startsWith("--baseline="))?.slice(11);
  if (!baselineArg) throw new Error("Pass --baseline=<directory containing preserved engine and vision>");
  const baseline = path.resolve(baselineArg), oldFile = path.join(baseline, "dragon-wave-engine.js");
  const oldModule = loadCountedEngine(oldFile), newModule = loadCountedEngine(path.join(ROOT, "dragon-wave-engine.js"));
  const oldEngine = oldModule.exports, newEngine = newModule.exports;
  const report = { generatedAt: new Date().toISOString(), oldEngineSha256: digest(oldFile),
    newEngineSha256: digest(path.join(ROOT, "dragon-wave-engine.js")), oldVisionSha256: digest(path.join(baseline, "dragon-wave-vision.js")),
    newVisionSha256: digest(path.join(ROOT, "dragon-wave-vision.js")), comparisons: [],
    productionOptions: PRODUCTION_OPTIONS, now: options.now,
    note: "Full deep equality; timings are local fixture measurements, not full-batch forecasts." };
  let piFrames, highFrameSeed;
  for (const [index, [symbol, interval, file]] of fixtures.entries()) {
    const source = JSON.parse(fs.readFileSync(path.join(ROOT, "tests/fixtures", file)));
    const ms = { "5m": 300000, "15m": 900000, "1h": 3600000 }[interval];
    const rows = !Array.isArray(source[0]) ? source : file.includes("okx")
      ? Data.parseRows("okx", source, ms)
      : source.map(row => ({ time: Number(row[0]), closeTime: Number(row[0]) + ms - 1,
        open: Number(row[1]), high: Number(row[2]), low: Number(row[3]), close: Number(row[4]),
        volume: Number(row[5]), quoteVolume: Number(row[6] ?? Number(row[5]) * Number(row[4])),
        takerBuyVolume: Number(row[7] || 0), tradeCount: Number(row[8] || 0) }));
    const result = measure(file,
      () => oldEngine.analyzeTimeframe(rows, { ...analysisOptions, interval }),
      () => newEngine.analyzeTimeframe(rows, { ...analysisOptions, interval }), index % 2 === 1);
    assert.equal(result.after.candles.length, rows.length, `Fixture must retain its actual candles: ${file}`);
    assert.ok(result.after.candles.length >= 40, `Fixture must exercise strategy analysis: ${file}`);
    report.comparisons.push({ ...result.record, candleCount: rows.length,
      fixtureSha256: digest(path.join(ROOT, "tests/fixtures", file)),
      signals: result.after.signals.length, rejected: result.after.rejected.length });
    if (symbol === "PI" && file.includes("1115")) piFrames = { old: [result.before], current: [result.after] };
    if (symbol === "NOT") highFrameSeed = rows;
  }
  for (const [interval, ms] of [["15m", 900000], ["1h", 3600000], ["4h", 14400000], ["1d", 86400000]]) {
    const rows = aggregate(piFrames.old[0].candles, ms, 300000);
    const result = measure(`PI derived ${interval}`,
      () => oldEngine.analyzeTimeframe(rows, { ...analysisOptions, interval }),
      () => newEngine.analyzeTimeframe(rows, { ...analysisOptions, interval }));
    assert.equal(result.after.candles.length, rows.length);
    report.comparisons.push({ ...result.record, candleCount: rows.length, fullBucketsOnly: true,
      structureLoopExercised: rows.length > 30 });
    piFrames.old.push(result.before); piFrames.current.push(result.after);
  }
  // Long synthetic interval-policy coverage, explicitly not real 4h/day market data.
  for (const [interval, ms] of [["4h", 14400000], ["1d", 86400000]]) {
    const start = Date.UTC(2024, 0, 1), rows = highFrameSeed.map((row, index) => ({
      ...row, time: start + index * ms, closeTime: start + (index + 1) * ms - 1,
    }));
    const result = measure(`Synthetic ${interval} policy using NOT price path`,
      () => oldEngine.analyzeTimeframe(rows, { ...analysisOptions, interval }),
      () => newEngine.analyzeTimeframe(rows, { ...analysisOptions, interval }));
    assert.equal(result.after.candles.length, rows.length);
    report.comparisons.push({ ...result.record, candleCount: rows.length, synthetic: true });
  }
  for (const [label, gateOptions] of [["leader-active", options], ["no-leader", { ...options, preselectedLeader: false }]]) {
    const oldInput = structuredClone(piFrames.old), newInput = structuredClone(piFrames.current);
    const oldRebuilds = oldModule.parityRebuildCalls, newRebuilds = newModule.parityRebuildCalls;
    const result = measure(`PI full causal context ${label}`,
      () => oldEngine.applyContextGates(oldInput, [], gateOptions),
      () => newEngine.applyContextGates(newInput, [], gateOptions));
    assert.deepStrictEqual(oldInput, piFrames.old, "Legacy gate must leave raw inputs unchanged");
    assert.deepStrictEqual(newInput, piFrames.current, "Optimized gate must leave raw inputs unchanged");
    const beforeRebuildCalls = oldModule.parityRebuildCalls - oldRebuilds;
    const afterRebuildCalls = newModule.parityRebuildCalls - newRebuilds;
    if (label === "leader-active") assert.ok(beforeRebuildCalls > 0 && afterRebuildCalls > 0,
      "Active context fixture must execute real causal parent rebuilds");
    report.comparisons.push({ ...result.record, beforeRebuildCalls, afterRebuildCalls, rawInputsUnchanged: true });
  }
  assert.equal(digest(oldFile), report.oldEngineSha256, "Baseline engine changed during verification");
  assert.equal(digest(path.join(ROOT, "dragon-wave-engine.js")), report.newEngineSha256, "Engine changed during verification");
  assert.equal(digest(path.join(ROOT, "dragon-wave-vision.js")), report.newVisionSha256, "Vision changed during verification");
  report.passed = true;
  const outputArg = process.argv.find(arg => arg.startsWith("--output="))?.slice(9);
  if (outputArg) {
    const output = path.resolve(outputArg); fs.mkdirSync(path.dirname(output), { recursive: true });
    fs.writeFileSync(output, JSON.stringify(report, null, 2) + "\n");
  }
  console.log(JSON.stringify({ passed: true, comparisons: report.comparisons.length }));
}

if (require.main === module) main();
