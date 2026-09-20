"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const Report = require("../tools/report_dragon_wave_precompute.js");
const Cases = require("../dragon-wave-cases.js");

const catalog = [{ id: "sample", valid: true, symbol: "SAMPLE", pair: "SAMPLEUSDT", start: "2024-01-01", end: "2024-01-03" }];
const task = Report.expectedTasks(catalog, "v90", ["1h"])[0];
const root = path.resolve(".runtime-cache/report-unit-fixture");
const options = { catalog, intervals: ["1h"], version: "v90", engineSha256: "engine-a", cacheRoot: root, exists: () => true, now: Date.parse("2025-01-01T00:00:00Z") };

function manifest(overrides = {}) {
  return { schema: 1, version: "v90", status: { state: "complete", fullBatch: true }, records: {
    [task.key]: { ...task, file: "sample.json.gz", engineSha256: "engine-a", contextComplete: true, contextIntervals: ["1h"] },
  }, ...overrides };
}

function payload() {
  const start = Date.parse("2024-01-01T00:00:00+08:00");
  const candles = Array.from({ length: 72 }, (_, index) => ({ time: start + index * 3600000, closeTime: start + (index + 1) * 3600000 - 1, open: 1, high: 1.03, low: 0.98, close: 1.01, volume: 100 }));
  return { ...task, schema: 1, version: "v90", engineSha256: "engine-a", contextComplete: true, contextIntervals: ["1h"], result: {
    interval: "1h", candles, signals: [{ time: candles[10].time, interval: "1h", index: 10, price: 1.02, pattern: "盘整突破", score: 90, status: "buy" }],
    pending: [], rejected: [], structures: [], retainedCandidates: [], secondaryBreakoutHints: [], indicators: { ema90: candles.map(() => 1), atr: candles.map(() => 0.01) }, stats: {},
  } };
}

test("production task catalog has exactly 85 cases and 425 distinct tasks without 1m", () => {
  const tasks = Report.expectedTasks(Cases, "v90");
  assert.equal(tasks.length, 425);
  assert.equal(new Set(tasks.map(task => task.key)).size, 425);
  assert.equal(tasks.some(task => task.interval === "1m"), false);
});

test("cache path cannot escape the production directory", () => {
  assert.equal(Report.cachePath(root, "../secret.json.gz"), null);
  assert.equal(Report.cachePath(root, ""), null);
  assert.equal(Report.cachePath(root, path.resolve("secret.json.gz")), null);
  assert.equal(Report.cachePath(root, "entry.txt"), null);
  assert.equal(Report.cachePath(root, "entry.json.gz"), path.join(root, "entry.json.gz"));
});

test("manifest running cannot report complete even with all matching files", () => {
  const report = Report.buildReport(manifest({ status: { state: "running", fullBatch: true } }), options);
  assert.equal(report.summary.ready, 1);
  assert.equal(report.summary.writtenCurrentEngine, 1);
  assert.equal(report.summary.writtenCompatibleEngine, 0);
  assert.equal(report.completed, false);
  assert.equal(report.manifestRunning, true);
  assert.equal(report.state, "running");
  assert.equal(report.exitCode, 2);
});

test("complete state cannot hide failed or missing production tasks", () => {
  const missing = Report.buildReport(manifest({ records: {} }), options);
  assert.equal(missing.completed, false);
  assert.equal(missing.tasks[0].state, "missing");
  const failed = Report.buildReport(manifest({ records: {}, failures: { [task.key]: { message: "network incomplete", phase: "fetch" } } }), options);
  assert.equal(failed.tasks[0].state, "failed");
  assert.equal(failed.summary.failed, 1);
  assert.equal(failed.exitCode, 2);
});

test("old engine, missing gzip, and incomplete context are not ready", () => {
  const data = manifest();
  data.records[task.key].engineSha256 = "old";
  assert.equal(Report.buildReport(data, options).tasks[0].state, "stale-engine");
  data.records[task.key].engineSha256 = "engine-a";
  data.records[task.key].contextComplete = false;
  assert.equal(Report.buildReport(data, options).tasks[0].state, "partial-context");
  assert.equal(Report.buildReport(manifest(), { ...options, exists: () => false }).tasks[0].state, "missing-file");
});

test("partial per-period files show written progress and the producer's current phase", () => {
  const data = manifest({ status: { state: "running", fullBatch: true, currentTask: { pair: task.pair, interval: "1h", phase: "analyze" } } });
  data.records[task.key].contextComplete = false;
  const report = Report.buildReport(data, options);
  assert.equal(report.summary.writtenFiles, 1);
  assert.equal(report.summary.writtenCurrentEngine, 1);
  assert.equal(report.summary.readyFullContext, 0);
  assert.equal(report.currentTask.phase, "analyze");
  assert.match(Report.formatMarkdown(report), /已落盘：1\/1/);
  assert.match(Report.formatMarkdown(report), /计算策略/);
});

test("lightweight completed status requires fullBatch finalization", () => {
  const complete = Report.buildReport(manifest(), options);
  assert.equal(complete.completed, true);
  assert.equal(complete.exitCode, 0);
  assert.equal(complete.summary.verifiedTasks, 0);
  const notFull = Report.buildReport(manifest({ status: { state: "complete", fullBatch: false } }), options);
  assert.equal(notFull.completed, false);
});

test("phase timing reports actual context and storage work without implying completion", () => {
  const data = manifest({ status: { state: "running", fullBatch: true, currentTask: {
    pair: task.pair, interval: "5m", phase: "final-storage", phaseState: "running", timings: [
      { phase: "partial-context", interval: "5m", state: "complete", durationMs: 1234 },
      { phase: "full-context", state: "complete", durationMs: 0, reused: true },
      { phase: "final-storage", interval: "5m", state: "running" },
    ],
  } } });
  const report = Report.buildReport(data, options), markdown = Report.formatMarkdown(report);
  assert.equal(report.completed, false);
  assert.match(markdown, /汇总已加载周期：完成，1\.23秒/);
  assert.match(markdown, /复用等价结果/);
  assert.match(markdown, /压缩保存最终结果：进行中，尚未结束/);
});

test("verification checks identity, coverage, and invalid candlestick data", () => {
  const good = payload();
  const verified = Report.buildReport(manifest(), { ...options, verifyResults: true, readResult: () => good });
  assert.equal(verified.completed, true);
  assert.equal(verified.summary.verifiedTasks, 1);
  const badIdentity = { ...good, pair: "OTHERUSDT" };
  assert.equal(Report.verifyStoredResult(badIdentity, task, options).valid, false);
  const truncated = { ...good, result: { ...good.result, candles: good.result.candles.slice(40) } };
  assert.equal(Report.verifyStoredResult(truncated, task, options).valid, false);
  const invalid = payload();
  invalid.result.candles[5].high = 0.1;
  assert.match(Report.verifyStoredResult(invalid, task, options).errors.join(" "), /价格非法/);
});

test("gzip/read errors cannot become a successful verified result", () => {
  const result = Report.buildReport(manifest(), { ...options, verifyResults: true, readResult: () => { throw new Error("incorrect header check"); } });
  assert.equal(result.completed, false);
  assert.equal(result.tasks[0].state, "verification-failed");
  assert.equal(result.exitCode, 2);
});

test("confirmation native hits and local feedback restoration are counted separately", () => {
  const data = payload(), nativeTime = data.result.candles[10].time, restoreTime = data.result.candles[20].time;
  const records = {};
  for (const time of [nativeTime, restoreTime]) {
    const key = `${task.pair}|1h|${time}`;
    records[key] = { key, decision: "confirmed", pair: task.pair, interval: "1h", updatedAt: 1, signal: { time, interval: "1h", price: 1.02, triggerPrice: 1.02, pattern: "盘整突破", score: 90, foundationTypes: ["base"], auxiliaryTypes: ["previousHigh"] } };
  }
  const verified = Report.verifyStoredResult(data, task, { ...options, feedbackDocument: { version: 1, records } });
  assert.equal(verified.valid, true);
  assert.equal(verified.confirmed.filter(record => record.native).length, 1);
  assert.equal(verified.confirmed.filter(record => record.restoredByFeedback).length, 1);
  assert.equal(verified.confirmed.every(record => record.candlePresent && record.displayed), true);
});

test("a confirmed time with a missing candle fails verification rather than counting restoration", () => {
  const data = payload(), time = data.result.candles[20].time;
  data.result.candles.splice(20, 1);
  const key = `${task.pair}|1h|${time}`;
  const verified = Report.verifyStoredResult(data, task, { ...options, feedbackDocument: { version: 1, records: {
    [key]: { key, decision: "confirmed", pair: task.pair, interval: "1h", updatedAt: 1, signal: { time, interval: "1h", price: 1.02 } },
  } } });
  assert.equal(verified.valid, false);
  assert.match(verified.errors.join(" "), /对应K线缺失/);
});

test("confirmed points in unfinished tasks remain explicitly unchecked", () => {
  const time = payload().result.candles[10].time;
  const key = `${task.pair}|1h|${time}`;
  const records = { [key]: { key, decision: "confirmed", pair: task.pair, interval: "1h", signal: { time } },
    excludedMinute: { decision: "confirmed", pair: task.pair, interval: "1m", signal: { time } } };
  const result = Report.buildReport(manifest({ records: {} }), { ...options, verifyResults: true, feedbackDocument: { records } });
  assert.equal(result.summary.confirmedExpectedInCatalog, 1);
  assert.equal(result.summary.confirmedNotYetChecked, 1);
  assert.equal(result.summary.confirmedInVerifiedCoverage, 0);
  assert.equal(result.completed, false);
});
