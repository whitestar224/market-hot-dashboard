const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const code = fs.readFileSync(path.join(__dirname, "..", "ai_insights.js"), "utf8");

test("degraded results preserve cache, stop batches, enable rules and retry after cooldown", async () => {
  let now = 100000;
  let notice;
  let calls = 0;
  let degraded = false;
  const source = { id: "crypto", rows: [{ symbol: "BTC", name: "Bitcoin" }] };
  const context = {
    window: {}, console, Date: { now: () => now },
    localStorage: { getItem: (key) => key === "xingyun:rank-ai-enabled:v1" ? "1" : null, setItem() {}, removeItem() {} },
    document: {
      getElementById: () => notice,
      createElement: () => ({ setAttribute() {}, remove() { notice = undefined; } }),
      body: { prepend(value) { notice = value; } }
    },
    fetch: async (url, options) => {
      calls++;
      const key = JSON.parse(options.body).sources[0].rows[0].key;
      return { ok: true, json: async () => ({ ok: true, enabled: true, degraded,
        retryAfterSeconds: 60, insights: degraded ? {} : { [key]: { detail: "生态升级", provider: "deepseek" } } }) };
    }
  };
  vm.runInNewContext(code, context);
  const ai = context.window.XingyunAiInsights;
  await ai.requestForSources([source]);
  degraded = true;
  source.rows[0].price = "2";
  await ai.requestForSources([source]);
  assert.equal(ai.getRowInsight(source.rows[0], { source }).detail, "生态升级");
  assert.equal(ai.shouldDeferFallback(source.rows[0], { source }), false);
  assert.equal(notice.textContent, "AI暂不可用，使用本地规则结果");
  assert.equal(ai.isPending(source.rows[0], { source }), false);
  await ai.requestForSources([source]);
  assert.equal(calls, 2);
  now += 61000;
  degraded = false;
  await ai.requestForSources([source]);
  assert.equal(calls, 3);
  assert.equal(notice, undefined);
});

test("an open board retries AI only after a restarted service reports a ready startup probe", async () => {
  let now = 100000;
  let servicePid = 11;
  let rankCalls = 0;
  const timers = [];
  const source = { id: "crypto", rows: [{ symbol: "BTC", name: "Bitcoin" }] };
  const context = {
    window: { setTimeout(callback) { timers.push(callback); return timers.length; } },
    console,
    Date: { now: () => now },
    localStorage: { getItem: (key) => key === "xingyun:rank-ai-enabled:v1" ? "1" : null, setItem() {}, removeItem() {} },
    document: {
      getElementById: () => null,
      createElement: () => ({ setAttribute() {}, remove() {} }),
      body: { prepend() {} }
    },
    fetch: async (url, options) => {
      if (url === "/api/service-liveness") {
        return { ok: true, json: async () => ({ ok: true, pid: servicePid, aiStartup: { status: "ready" } }) };
      }
      rankCalls++;
      const key = JSON.parse(options.body).sources[0].rows[0].key;
      return { ok: true, json: async () => rankCalls === 1
        ? ({ ok: true, enabled: true, degraded: true, retryAfterSeconds: 60, insights: {},
             servicePid: 11, aiStartup: { status: "ready" } })
        : ({ ok: true, enabled: true, degraded: false,
             insights: { [key]: { detail: "AI恢复", provider: "codex-cli" } },
             servicePid, aiStartup: { status: "ready" } }) };
    }
  };

  vm.runInNewContext(code, context);
  const ai = context.window.XingyunAiInsights;
  await ai.requestForSources([source]);
  assert.equal(rankCalls, 1);
  await timers.shift()();
  assert.equal(rankCalls, 1);
  servicePid = 22;
  await timers.shift()();
  assert.equal(rankCalls, 2);
  assert.equal(ai.getRowInsight(source.rows[0], { source }).detail, "AI恢复");
});

test("rank Codex analysis is off by default and makes no request until enabled", async () => {
  let calls = 0;
  const preferences = new Map();
  const source = { id: "crypto", rows: [{ symbol: "BTC", name: "Bitcoin" }] };
  const context = {
    window: {},
    console,
    localStorage: {
      getItem: (key) => preferences.get(key) ?? null,
      setItem: (key, value) => preferences.set(key, value),
      removeItem: (key) => preferences.delete(key)
    },
    document: {
      getElementById: () => null,
      createElement: () => ({ setAttribute() {}, remove() {} }),
      body: { prepend() {} }
    },
    fetch: async (url, options) => {
      calls++;
      const key = JSON.parse(options.body).sources[0].rows[0].key;
      return {
        ok: true,
        json: async () => ({ ok: true, enabled: true, insights: { [key]: { detail: "减半叙事", provider: "codex-cli" } } })
      };
    }
  };

  vm.runInNewContext(code, context);
  const ai = context.window.XingyunAiInsights;
  assert.equal(ai.isEnabled(), false);
  assert.equal(ai.shouldDeferFallback(source.rows[0], { source }), false);
  await ai.requestForSources([source]);
  assert.equal(calls, 0);
  assert.equal(ai.getRowInsight(source.rows[0], { source }), null);

  ai.setEnabled(true);
  await ai.requestForSources([source]);
  assert.equal(calls, 1);
  assert.equal(ai.getRowInsight(source.rows[0], { source }).detail, "减半叙事");
  assert.equal(preferences.get("xingyun:rank-ai-enabled:v1"), "1");

  ai.setEnabled(false);
  assert.equal(ai.getRowInsight(source.rows[0], { source }), null);
});
