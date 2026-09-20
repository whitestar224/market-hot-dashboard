const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const js = fs.readFileSync(path.join(__dirname, "../dragon-wave.js"), "utf8");
const params = { historicalDocument: true, pair: "PIUSDT", caseStart: "2025-02-21", caseEnd: "2025-02-27", market: "futures", mainWaveStage: "active", window: {} };
const identity = { requestedVersion: "v91", pair: params.pair, start: params.caseStart, end: params.caseEnd, market: params.market, stage: "active" };
const data = { normalizePair: x => x, isCandleCoverageAcceptable: () => true };
const section = (from, to) => js.slice(js.indexOf(from), js.indexOf(to, js.indexOf(from)));
function harness(fetch) {
  const source = section("  async function resolveLocalStrategyRelease(params)", "  function scheduleStrategyReleaseCheck")
    + section("  async function readLocalPrecomputed(params)", "  function localPrecomputePendingError()");
  return new Function("fetch", "Data", "STRATEGY_CACHE_VERSION", "normalizeMainWaveStage", `${source}; return { resolve: resolveLocalStrategyRelease, read: readLocalPrecomputed };`)(fetch, data, "v91", x => x);
}

test("one resolved case version pins all five GETs even if v91 becomes ready mid-load", async () => {
  let selected = "v90";
  const versions = [];
  let resolves = 0;
  const api = harness(async url => {
    if (url.startsWith("/api/dragon-wave-release?")) {
      resolves++;
      return { ok: true, json: async () => ({ ...identity, selectedVersion: selected, pending: selected !== "v91" }) };
    }
    const query = new URL(url, "http://localhost").searchParams;
    versions.push(query.get("version"));
    selected = "v91";
    return { ok: true, status: 200, json: async () => ({ version: query.get("version"),
      ...Object.fromEntries(["pair", "start", "end", "market", "interval"].map(key => [key, query.get(key)])),
      mainWaveStage: "active", contextComplete: true, result: { candles: [{ time: 1 }], signals: [{ status: "buy" }] } }) };
  });
  const release = await api.resolve(params);
  for (const interval of ["5m", "15m", "1h", "4h", "1d"]) {
    assert.equal((await api.read({ ...params, interval, precomputedVersion: release.selectedVersion })).version, "v90");
  }
  assert.equal(resolves, 1);
  assert.deepEqual(versions, ["v90", "v90", "v90", "v90", "v90"]);
  assert.equal((await api.resolve(params)).selectedVersion, "v91");
});

test("release identity must match symbol, dates, market, stage and known versions", async () => {
  for (const invalid of [{ pair: "NOTUSDT" }, { start: "2025-01-01" }, { end: "2025-03-01" },
    { market: "spot" }, { stage: "auto" }, { requestedVersion: "v99" }, { selectedVersion: "v89" }]) {
    const api = harness(async () => ({ ok: true, json: async () => ({ ...identity, selectedVersion: "v91", ...invalid }) }));
    assert.equal(await api.resolve(params), null);
  }
});

test("live symbols skip historical release resolution", async () => {
  const api = harness(() => assert.fail("live mode must not fetch release"));
  assert.equal(await api.resolve({ ...params, historicalDocument: false }), null);
});

test("pending release check is lightweight and never clears or recomputes the visible chart", async () => {
  let callback;
  let calls = 0;
  const hints = {};
  const state = { generation: 2, loadingWorkspace: false };
  const source = section("  function scheduleStrategyReleaseCheck(params, generation)", "  async function loadAnalyzedInterval(params)");
  const schedule = new Function("state", "window", "clearTimeout", "resolveLocalStrategyRelease", "$", "STRATEGY_CACHE_VERSION",
    `${source}; return scheduleStrategyReleaseCheck;`)(state, { setTimeout: (fn, ms) => { callback = fn; assert.equal(ms, 30000); return 1; } }, () => {},
    async () => { calls++; return { selectedVersion: "v91" }; }, () => hints, "v91");
  schedule(params, 2);
  await callback();
  assert.equal(calls, 1);
  assert.match(hints.textContent, /新版 v91 已就绪/);
  assert.match(hints.textContent, /人工选择保持不变/);
  assert.doesNotMatch(source, /loadWorkspace\(|setLoading\(|analyzeTimeframe|\.results\.clear/);
});

test("page resolves once before loading and preserves manual feedback overlay", () => {
  const source = section("  async function loadWorkspace()", "  function updateSummary(");
  assert.ok(source.indexOf("caseRelease = await resolveLocalStrategyRelease") < source.indexOf("const activeItem = await loadOne"));
  assert.match(source, /precomputedVersion: caseRelease\?\.selectedVersion \|\| STRATEGY_CACHE_VERSION/);
  assert.match(source, /applyFeedbackPolicy\(loaded.result, pair\)/);
  assert.match(source, /当前显示/);
});
