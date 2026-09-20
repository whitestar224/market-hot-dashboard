const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const zlib = require("node:zlib");

const source = fs.readFileSync(path.join(__dirname, "../tools/precompute_dragon_wave_cases.js"), "utf8");
const order = ["1h", "4h", "1d", "15m", "5m"];
const example = { valid: true, symbol: "TEST", pair: "TESTUSDT", start: "2024-01-01", end: "2024-01-02" };

function harness(t, options = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dragon-wave-full-batch-test-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, "tools"));
  fs.writeFileSync(path.join(root, "dragon-wave-engine.js"), "test-engine-version-one");
  const outputRoot = path.join(root, ".runtime-cache/dragon-wave-precomputed/v90");
  const fetchCalls = [], analysisCalls = [], contextCalls = [], logs = [], progress = [], storageCalls = [];
  const failures = new Set(options.failIntervals || []);
  const analysisFailures = new Set(options.failAnalysis || []);
  const contextFailures = new Set(options.failContextCalls || []);
  const incompleteContextCalls = new Set(options.incompleteContextCalls || []);
  const storageFailures = new Set(options.failStorageCalls || []);
  let clockTime = Date.now();
  const advance = (ms) => { clockTime += ms; };
  class TestDate extends Date { static now() { return clockTime; } }
  const candle = { time: 1000, closeTime: 1999, open: 1, high: 2, low: 1, close: 2, volume: 10 };
  let api;
  const Data = {
    INTERVALS: Object.fromEntries(order.map((interval) => [interval, { ms: 1000 }])),
    normalizePair: (value) => String(value).toUpperCase(),
    buildCaseWindow: () => ({ start: 1000, end: 1999, completeCase: true }),
    isCandleCoverageAcceptable: (candles) => Array.isArray(candles)
      && candles.length > 0 && candles.every((row) => row.time === 1000),
    assessCandleCoverage: () => ({ completeEnough: true, spanCoverage: 1 }),
    fetchCandles: async ({ interval }) => {
      fetchCalls.push(interval);
      advance(3);
      if (failures.has(interval)) throw Object.assign(new Error("missing-" + interval),
        { attempts: [{ venue: "test", message: "missing" }] });
      return { candles: options.invalidPayload ? [] : [{ ...candle }],
        coverage: { completeEnough: !options.invalidPayload, spanCoverage: options.invalidPayload ? 0 : 1 },
        venue: { label: "test exchange" }, attempts: [] };
    },
  };
  const Engine = {
    analyzeTimeframe: (candles, settings) => {
      analysisCalls.push(settings.interval);
      advance(11);
      assert.ok(fs.existsSync(api.rawCandlePath(example, settings.interval)), "save source candles before analysis");
      if (analysisFailures.has(settings.interval)) throw new Error("analysis-" + settings.interval);
      return { interval: settings.interval, candles: options.invalidNormalized ? [] : candles,
        signals: [], rejected: [], rawOnly: true };
    },
    applyContextGates: (entries) => {
      assert.ok(entries.every((entry) => entry.rawOnly === true), "never feed already-gated output into context");
      contextCalls.push(Array.from(entries, (entry) => entry.interval));
      advance(23);
      if (contextFailures.has(contextCalls.length)) throw new Error("context-" + contextCalls.length);
      if (incompleteContextCalls.has(contextCalls.length)) entries = entries.slice(0, -1);
      return entries.map((entry) => ({ ...entry, rawOnly: false,
        signals: entries.length === order.length ? [{ id: "full-context-only" }] : [],
        contextSeen: entries.map((value) => value.interval) }));
    },
  };
  const module = { exports: {} };
  const fakeRequire = (name) => {
    if (name === "../dragon-wave-data") return Data;
    if (name === "../dragon-wave-engine") return Engine;
    if (name === "../dragon-wave-feedback") return { snapshotSignal: (value) => ({ ...value }) };
    if (name === "../dragon-wave-cases") return [example];
    if (name === "./dragon_wave_cache_compatibility") return options.compatibilityModule
      || require("../tools/dragon_wave_cache_compatibility.js");
    if (name === "node:fs") return { ...fs, renameSync: (from, to) => {
      if (path.dirname(to) === outputRoot && to.endsWith(".json.gz")) {
        storageCalls.push(to);
        if (storageFailures.has(storageCalls.length)) throw new Error("storage-" + storageCalls.length);
      }
      fs.renameSync(from, to);
      if (path.basename(to) === "manifest.json") {
        const saved = JSON.parse(fs.readFileSync(to, "utf8"));
        if (saved.status?.currentTask) progress.push(saved.status.currentTask);
      } else advance(2);
    } };
    if (name === "node:zlib") return { ...zlib, gzipSync: (...args) => {
      advance(7);
      return zlib.gzipSync(...args);
    } };
    return require(name);
  };
  const processStub = {
    argv: ["node", "precompute", "--full-batch", "--version=v90", ...(options.args || [])],
    env: {}, pid: process.pid,
    stdout: { write: (value) => logs.push(value) }, stderr: { write: (value) => logs.push(value) },
    kill: options.kill || process.kill.bind(process),
  };
  const sandbox = { module, exports: module.exports, require: fakeRequire, __dirname: path.join(root, "tools"),
    process: processStub, Buffer, console: { log: (value) => logs.push(value), error: (value) => logs.push(value) },
    setTimeout: (callback) => { callback(); return 1; }, fetch: async () => { throw new Error("unexpected real fetch"); } };
  if (options.syntheticClock) sandbox.Date = TestDate;
  sandbox.global = sandbox;
  vm.runInNewContext(source, sandbox, { filename: "precompute_dragon_wave_cases.js" });
  api = module.exports;
  const manifest = { schema: 1, version: "v90", records: {}, status: {} };
  const lockPath = path.join(root, ".runtime-cache/dragon-wave-precomputed/v90.lock");
  const readRecord = (interval) => {
    const record = manifest.records[api.cacheKey(example, interval)];
    return JSON.parse(zlib.gunzipSync(fs.readFileSync(path.join(outputRoot, record.file))).toString("utf8"));
  };
  return { root, api, manifest, outputRoot, lockPath, readRecord, fetchCalls, analysisCalls, contextCalls,
    logs, progress, storageCalls, failures, analysisFailures };
}

test("full batch saves raw candles first and finalizes every interval with all five raw contexts", async (t) => {
  const h = harness(t);
  const result = await h.api.buildCase(example, h.manifest, 1, 1, ["5m"], true,
    () => assert.fail("full batch must not drain interactive priority requests"));
  assert.equal(result.built, 5);
  assert.equal(result.failed, 0);
  assert.deepEqual(h.fetchCalls, order);
  assert.deepEqual(h.analysisCalls, order);
  assert.deepEqual(h.contextCalls, order.map((_, index) => order.slice(0, index + 1)),
    "finalization reuses the last gate over the same complete raw input");
  assert.equal(h.storageCalls.length, 10, "each provisional result is still saved before finalization");
  for (const interval of order) {
    const saved = h.readRecord(interval);
    assert.equal(saved.version, "v90");
    assert.equal(saved.contextComplete, true);
    assert.deepEqual(saved.contextIntervals, order);
    assert.deepEqual(saved.result.contextSeen, order);
    assert.equal(saved.result.signals[0].id, "full-context-only");
  }
  assert.equal(h.manifest.cases[h.api.fullCaseKey(example)].state, "complete");
  assert.equal(h.manifest.status.currentTask, null);
  assert.ok(h.logs.some((line) => line.includes("1h fetch")));
  assert.ok(h.logs.some((line) => line.includes("1h analyze")));
});

test("local-only release never fetches missing candles from an exchange", async (t) => {
  const h = harness(t, { args: ["--local-only"] });
  await assert.rejects(h.api.fetchAndAnalyze(example, "1h"), /本地K线不完整/);
  assert.deepEqual(h.fetchCalls, []);
  assert.deepEqual(h.analysisCalls, []);
});

test("local-only release reuses complete persisted candles", async (t) => {
  const h = harness(t, { args: ["--local-only"] });
  const file = h.api.rawCandlePath(example, "1h");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, zlib.gzipSync(JSON.stringify({ schema: 1, pair: example.pair,
    start: example.start, end: example.end, interval: "1h", market: "futures",
    candles: [{ time: 1000 }], venue: { label: "local" } })));
  const value = await h.api.fetchAndAnalyze(example, "1h");
  assert.equal(value.interval, "1h");
  assert.deepEqual(h.fetchCalls, []);
  assert.deepEqual(h.analysisCalls, ["1h"]);
});

test("phase checkpoints separate native analysis, partial context, and compression/storage timings", async (t) => {
  const h = harness(t, { syntheticClock: true });
  await h.api.buildCase(example, h.manifest, 1, 1);
  const checkpoint = h.manifest.cases[h.api.fullCaseKey(example)];
  const durations = { fetch: 3, "persist-candles": 9, analyze: 11, "partial-context": 23,
    "partial-storage": 9, "final-storage": 9 };
  for (const interval of order) {
    for (const [phase, durationMs] of Object.entries(durations)) {
      const timing = checkpoint.timings.find((value) => value.interval === interval && value.phase === phase);
      assert.equal(timing.state, "complete");
      assert.equal(timing.durationMs, durationMs, interval + " " + phase);
      for (const phaseState of ["running", "complete"]) {
        const saved = h.progress.find((value) => value.interval === interval
          && value.phase === phase && value.phaseState === phaseState);
        assert.ok(saved, "persist " + interval + " " + phase + " " + phaseState);
        assert.equal(saved.startedAt, timing.startedAt);
        assert.ok(saved.timings.some((value) => value.phase === phase && value.interval === interval));
      }
    }
    const analysisDone = h.progress.findIndex((value) => value.interval === interval
      && value.phase === "analyze" && value.phaseState === "complete");
    const contextStart = h.progress.findIndex((value) => value.interval === interval
      && value.phase === "partial-context" && value.phaseState === "running");
    assert.ok(analysisDone < contextStart, "native analysis ends before the partial context stage");
  }
  const finalContext = checkpoint.timings.find((value) => value.phase === "full-context");
  assert.equal(finalContext.reused, true);
  assert.equal(finalContext.durationMs, 0);
  assert.ok(h.progress.some((value) => value.phase === "full-context"
    && value.phaseState === "complete" && value.reused === true));
  assert.ok(h.logs.some((value) => value.includes("5m analyze complete 11ms")));
  assert.ok(h.logs.some((value) => value.includes("5m partial-context complete 23ms")));
  assert.ok(h.logs.some((value) => value.includes("full-context complete 0ms reused")));
});

test("a missing final timeframe reuses the unchanged last successful partial context", async (t) => {
  const h = harness(t, { failIntervals: ["5m"] });
  await h.api.buildCase(example, h.manifest, 1, 1);
  assert.equal(h.contextCalls.length, 4);
  assert.deepEqual(h.readRecord("1h").result.contextSeen, order.slice(0, -1));
  assert.equal(h.readRecord("1h").contextComplete, false);
  assert.equal(h.manifest.cases[h.api.fullCaseKey(example)].state, "partial");
});

test("failed or incomplete gates cannot reuse a stale context from fewer raw intervals", async (t) => {
  for (const failureMode of ["failContextCalls", "incompleteContextCalls"]) {
    const h = harness(t, { [failureMode]: [5] });
    await h.api.buildCase(example, h.manifest, 1, 1);
    assert.equal(h.contextCalls.length, 6, failureMode + " requires final recomputation");
    assert.deepEqual(h.contextCalls[5], order);
    assert.deepEqual(h.readRecord("1h").result.contextSeen, order);
    const checkpoint = h.manifest.cases[h.api.fullCaseKey(example)];
    assert.equal(checkpoint.timings.find((value) => value.phase === "full-context").reused, false);
    if (failureMode === "failContextCalls") {
      assert.equal(checkpoint.state, "partial", "a repaired final gate does not erase the earlier failure");
      assert.equal(h.manifest.failures[h.api.cacheKey(example, "5m")].phase, "partial-context");
    }
  }
});

test("a failed provisional write does not discard a valid context result", async (t) => {
  const h = harness(t, { failStorageCalls: [5] });
  await h.api.buildCase(example, h.manifest, 1, 1);
  assert.equal(h.contextCalls.length, 5);
  assert.deepEqual(h.readRecord("5m").result.contextSeen, order);
  assert.equal(h.readRecord("5m").contextComplete, false);
  assert.equal(h.manifest.failures[h.api.cacheKey(example, "5m")].phase, "partial-storage");
});

test("final context and storage failures retain their actual failed phase", async (t) => {
  for (const options of [{ failContextCalls: [5, 6] }, { failStorageCalls: [6] }]) {
    const h = harness(t, options);
    const result = await h.api.buildCase(example, h.manifest, 1, 1);
    const phase = options.failContextCalls ? "full-context" : "final-storage";
    assert.equal(result.failed, 5);
    assert.equal(h.manifest.cases[h.api.fullCaseKey(example)].state, "partial");
    for (const interval of order) assert.equal(h.manifest.failures[h.api.cacheKey(example, interval)].phase, phase);
    assert.ok(h.progress.some((value) => value.phase === phase && value.phaseState === "failed"));
  }
});

test("empty raw input never invokes context gates or becomes a reusable complete case", async (t) => {
  for (const options of [{ failIntervals: order }, { args: ["--intervals=unknown"] }]) {
    const h = harness(t, options);
    await h.api.buildCase(example, h.manifest, 1, 1);
    assert.equal(h.contextCalls.length, 0);
    assert.equal(h.storageCalls.length, 0);
    const checkpoint = h.manifest.cases[h.api.fullCaseKey(example)];
    assert.equal(checkpoint.state, "partial");
    assert.equal(checkpoint.timings.find((value) => value.phase === "full-context").skipped, true);
    assert.equal(h.api.fullCaseUsable(h.manifest, example, []), false);
  }
});

test("only a complete case is reusable; missing output rebuilds all context from saved candles", async (t) => {
  const h = harness(t);
  await h.api.buildCase(example, h.manifest, 1, 1);
  const skipped = await h.api.buildCase(example, h.manifest, 1, 1);
  assert.equal(skipped.skipped, 5);
  assert.equal(h.analysisCalls.length, 5);
  const missing = h.manifest.records[h.api.cacheKey(example, "15m")];
  fs.unlinkSync(path.join(h.outputRoot, missing.file));
  const repaired = await h.api.buildCase(example, h.manifest, 1, 1);
  assert.equal(repaired.built, 5);
  assert.equal(h.fetchCalls.length, 5, "recovery reuses the source K lines");
  assert.equal(h.analysisCalls.length, 10, "rebuild every native timeframe, not gated snapshots");
  assert.equal(h.readRecord("1h").result.signals.length, 1);
});

test("verified compatible complete cases skip analysis without rewriting source results; force still rebuilds", async (t) => {
  const compatibility = require("../tools/dragon_wave_cache_compatibility.js");
  for (const force of [false, true]) {
    let assessed = 0;
    const h = harness(t, { args: force ? ["--force"] : [], compatibilityModule: { ...compatibility,
      loadCompatibility: () => ({ assessCase: (manifest, key, intervals) => {
        assessed++;
        assert.equal(manifest.cases[key].state, "complete");
        assert.deepEqual(Array.from(intervals), order);
        return { compatible: true, evidenceId: "verified-source-to-target" };
      } }),
    } });
    await h.api.buildCase(example, h.manifest, 1, 1);
    // Simulate a previous producer fingerprint; the real validator's file and
    // evidence checks are covered by dragon_wave_cache_compatibility.test.js.
    h.manifest.cases[h.api.fullCaseKey(example)].engineSha256 = "previous-engine";
    const oldManifest = JSON.stringify(h.manifest);
    const originalFiles = order.map(interval => {
      const record = h.manifest.records[h.api.cacheKey(example, interval)];
      return { filename: path.join(h.outputRoot, record.file), bytes: fs.readFileSync(path.join(h.outputRoot, record.file)) };
    });
    const result = await h.api.buildCase(example, h.manifest, 1, 1);
    assert.equal(h.analysisCalls.length, force ? 10 : 5);
    assert.equal(assessed, force ? 0 : 1);
    if (!force) {
      assert.equal(result.compatibleSkipped, 5);
      assert.equal(JSON.stringify(h.manifest), oldManifest);
      originalFiles.forEach(({ filename, bytes }) => assert.deepEqual(fs.readFileSync(filename), bytes));
      assert.ok(h.logs.some(line => line.includes("已验证兼容，复用原结果")));
    }
  }
});

test("failure is persisted, not saved as a zero-B result; retry restores full context", async (t) => {
  const h = harness(t, { failIntervals: ["15m"] });
  const partial = await h.api.buildCase(example, h.manifest, 1, 1);
  assert.equal(partial.failed, 1);
  assert.equal(h.manifest.cases[h.api.fullCaseKey(example)].state, "partial");
  const key = h.api.cacheKey(example, "15m");
  assert.equal(h.manifest.records[key], undefined);
  assert.equal(h.manifest.failures[key].phase, "fetch");
  assert.equal(h.manifest.failures[key].attempts[0].venue, "test");
  assert.equal(h.readRecord("1h").contextComplete, false);
  h.failures.clear();
  const recovered = await h.api.buildCase(example, h.manifest, 1, 1);
  assert.equal(recovered.failed, 0);
  assert.equal(h.fetchCalls.length, 6, "only fetch the previously missing candle series");
  assert.equal(h.manifest.failures[key], undefined);
  assert.equal(h.manifest.cases[h.api.fullCaseKey(example)].state, "complete");
  assert.deepEqual(h.readRecord("1h").result.contextSeen, order);
});

test("analysis interruption preserves source candles for retry", async (t) => {
  const h = harness(t, { failAnalysis: ["1h"] });
  await h.api.buildCase(example, h.manifest, 1, 1);
  const key = h.api.cacheKey(example, "1h");
  assert.equal(h.manifest.failures[key].phase, "analyze");
  assert.ok(fs.existsSync(h.api.rawCandlePath(example, "1h")));
  assert.equal(h.manifest.records[key], undefined);
  h.analysisFailures.clear();
  await h.api.buildCase(example, h.manifest, 1, 1);
  assert.equal(h.fetchCalls.length, 5);
  assert.equal(h.manifest.cases[h.api.fullCaseKey(example)].state, "complete");
});

for (const mode of ["invalidPayload", "invalidNormalized"]) {
  test(mode + " cannot produce a successful zero-B record", async (t) => {
    const h = harness(t, { [mode]: true });
    const result = await h.api.buildCase(example, h.manifest, 1, 1);
    assert.equal(result.failed, 5);
    assert.equal(Object.keys(h.manifest.records).length, 0);
    assert.equal(h.manifest.cases[h.api.fullCaseKey(example)].state, "partial");
    assert.equal(Object.keys(h.manifest.failures).length, 5);
  });
}

test("main marks failures partial and leaves interactive requests untouched", async (t) => {
  const h = harness(t, { failIntervals: ["5m"] });
  const requests = path.join(h.root, ".runtime-cache/dragon-wave-precomputed/v90.requests");
  fs.mkdirSync(requests, { recursive: true });
  const requestFile = path.join(requests, "a".repeat(64) + ".json");
  fs.writeFileSync(requestFile, JSON.stringify({ ...example, interval: "1h", priority: 1000 }));
  await h.api.main();
  const manifest = JSON.parse(fs.readFileSync(path.join(h.outputRoot, "manifest.json"), "utf8"));
  assert.equal(manifest.status.state, "partial");
  assert.equal(manifest.status.fullBatch, true);
  assert.equal(manifest.status.failed, 1);
  assert.ok(fs.existsSync(requestFile));
  assert.equal(fs.existsSync(h.lockPath), false);
});

test("a live lock older than 24 hours is not stolen, including EPERM probes", (t) => {
  for (const denied of [false, true]) {
    const h = harness(t, { kill: () => {
      if (denied) throw Object.assign(new Error("permission denied"), { code: "EPERM" });
    } });
    fs.mkdirSync(path.dirname(h.lockPath), { recursive: true });
    const original = JSON.stringify({ pid: process.pid + 1, startedAt: Date.now() - 3 * 86400000 });
    fs.writeFileSync(h.lockPath, original);
    assert.equal(h.api.acquireLock(), false);
    h.api.releaseLock();
    assert.equal(fs.readFileSync(h.lockPath, "utf8"), original);
  }
});

test("only a proven dead process lock is reclaimed; release checks current ownership", (t) => {
  const h = harness(t, { kill: () => { throw Object.assign(new Error("gone"), { code: "ESRCH" }); } });
  fs.mkdirSync(path.dirname(h.lockPath), { recursive: true });
  fs.writeFileSync(h.lockPath, JSON.stringify({ pid: process.pid + 1, startedAt: 1 }));
  assert.equal(h.api.acquireLock(), true);
  fs.writeFileSync(h.lockPath, JSON.stringify({ pid: process.pid + 2, startedAt: Date.now() }));
  h.api.releaseLock();
  assert.equal(JSON.parse(fs.readFileSync(h.lockPath, "utf8")).pid, process.pid + 2);
});
