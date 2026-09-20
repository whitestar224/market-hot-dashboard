const test = require("node:test");
const assert = require("node:assert/strict");
const flow = require("../event-flow.js");

test("live stream removes expired or withdrawn windows without a new revision", () => {
  const model = flow.createModel();
  const news = { active: true, opportunity: true, window: { eligible: true, expiresAt: 100, priority: 80 } };
  model.merge([{ id: 1, revision: 1, news }, { id: 2, revision: 2, popup: { kind: "系统通知" } }]);
  assert.equal(model.rows("all", 99).length, 1);
  assert.equal(model.rows("all", 100).length, 0);
  model.retain([]);
  assert.equal(model.rows().length, 0);
  assert.equal(flow.currentOpportunity({ news: { ...news, active: false } }, 99), false);
});

test("current priority wins over the timestamp of a repeated notification", () => {
  const model = flow.createModel();
  model.merge([{ id: 1, revision: 1, activityAt: 1, news: { window: { priority: 90 } } },
    { id: 2, revision: 2, activityAt: 500, news: { window: { priority: 60 } } }]);
  assert.deepEqual(model.rows().map(row => row.id), [1, 2]);
});

test("a fresh price breakout enters independently of News Trade AI and desktop delivery", () => {
  const item = { id: 1, signal: { type: "breakout", active: true, expiresAt: 100,
    symbol: "ALPHA", triggeredAt: 1, thesis: "触发价高于前高" }, popup: { stage: "failed" } };
  assert.equal(flow.currentOpportunity(item, 99), true);
  assert.equal(flow.currentOpportunity(item, 100), false);
  assert.equal(flow.matches(item, "signal"), true);
  assert.equal(flow.matches(item, "news"), false);
  assert.equal(flow.decision(item), "已突破前高");
  assert.match(flow.card(item), />ALPHA<\/h3>/);
  assert.match(flow.card(item), /显示失败/);
});

test("incremental updates preserve unrelated events and ignore stale history responses", () => {
  const model = flow.createModel();
  model.merge([{ id: 1, revision: 2, activityAt: 20, news: { active: true, opportunity: true } }, { id: 2, revision: 3, activityAt: 30 }]);
  model.merge([{ id: 1, revision: 4, activityAt: 40, news: { active: true, opportunity: false, verdict: "reject" } }]);
  model.merge([{ id: 1, revision: 2, activityAt: 20, news: { active: true, opportunity: true } }]);
  assert.equal(model.rows().length, 2);
  assert.equal(model.rows("opportunity").length, 0);
  assert.equal(flow.decision(model.rows()[0]), "不值得看");
});

test("an authoritative merge replaces duplicate source cards, preserving one event", () => {
  const model = flow.createModel();
  model.merge([{ id: 1, revision: 1, identities: ["a"], activityAt: 1 }, { id: 2, revision: 2, identities: ["b"], activityAt: 2 }]);
  model.merge([{ id: 1, revision: 3, identities: ["a", "b"], activityAt: 3 }]);
  assert.equal(model.rows().length, 1);
  model.merge([{ id: 2, revision: 2, identities: ["b"], activityAt: 2 }]);
  assert.equal(model.rows().length, 1);
});

test("raw signals and pending analyses are never presented as confirmed opportunities", () => {
  assert.equal(flow.decision({ popup: { title: "上涨" } }), "监控提醒");
  assert.equal(flow.decision({ news: { active: true, analysisStatus: "pending" } }), "待确认");
  assert.equal(flow.decision({ news: { active: true, opportunity: true } }), "值得看");
  assert.equal(flow.decision({ news: { active: false, opportunity: true } }), "已过时");
});

test("a high-potential event without a token is visible but never gets a buy button", () => {
  const item = { id: 7, activityAt: 1, news: {
    active: true,
    opportunity: false,
    analysisStatus: "ready",
    title: "一个正在出圈的新人物事件",
    thesis: "人物反差鲜明，适合二创扩散",
    preTokenMeme: { eligible: true, score: 88, priority: 88, catalystAt: 1,
      expiresAt: 100, tokenStatusLabel: "未发币 / 尚无可交易标的" },
  }, history: [] };
  let buyCalls = 0;
  const markup = flow.card(item, () => { buyCalls++; return "<button>买入</button>"; });
  assert.equal(flow.currentOpportunity(item, 99), true);
  assert.equal(flow.currentOpportunity(item, 100), false);
  assert.equal(flow.matches(item, "opportunity"), true);
  assert.equal(flow.decision(item), "大 MEME 潜力");
  assert.equal(buyCalls, 0);
  assert.match(markup, /未发币事件/);
  assert.match(markup, /未发币 \/ 尚无可交易标的/);
  assert.doesNotMatch(markup, />买入</);
});

test("cards put targets and one-line verdict first, with risk and progress collapsed", () => {
  const markup = flow.card({ id: 1, activityAt: Date.now(), news: { active: true, opportunity: true, symbol: "ALPHA", thesis: "明确的新催化", risk: "流动性不足" }, popup: { stage: "dispatched" }, history: [] });
  assert.match(markup, /<h3>ALPHA<\/h3>/);
  assert.match(markup, />值得看</);
  assert.match(markup, /<details class="news-focus-details" data-flow-detail="1">/);
  assert.ok(markup.indexOf("流动性不足") > markup.indexOf("<details"));
  assert.match(markup, /弹窗已调起/);
  assert.doesNotMatch(markup, /交易成功|已买入/);
});

test("untrusted messages are escaped and active script links are excluded", () => {
  const markup = flow.card({ id: 1, news: { active: true, verdict: "watch", symbol: '<img onerror="alert(1)">', thesis: '<script>alert(1)</script>', url: 'javascript:alert(1)' } });
  assert.doesNotMatch(markup, /<script>|<img|javascript:/);
  assert.match(markup, /&lt;script&gt;/);
  assert.equal(flow.safeUrl("https://example.com/news/one"), "https://example.com/news/one");
});
