// 战壕新币榜的「链」下拉：客户端筛选 + 与服务端链选择器的接线。
//
// 战壕榜不按链复制 rows（300 条重行），所以筛选在客户端做——这几个函数直接
// 从 app.js 抽出来真跑，而不是只 grep 源码。

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const js = fs.readFileSync(path.join(root, "app.js"), "utf8");
const css = fs.readFileSync(path.join(root, "styles.css"), "utf8");
const server = fs.readFileSync(path.join(root, "server.py"), "utf8");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");

// 取一个顶层函数的源码：从 `function name(` 到下一个顶层的 `function `。
function extract(name) {
  const start = js.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `app.js 里找不到 ${name}()`);
  const end = js.indexOf("\nfunction ", start + 1);
  assert.ok(end > start, `${name}() 之后没有顶层函数，截取失败`);
  return js.slice(start, end);
}

function loadChainHelpers(initial = "all") {
  const state = { trenchChain: initial };
  const source = [
    extract("normalizeTrenchChain"),
    extract("trenchRowChain"),
    extract("trenchRowsForSource"),
    extract("trenchSelectedCounts")
  ].join("\n");
  const api = new Function(
    "state",
    `${source}\nreturn { normalizeTrenchChain, trenchRowChain, trenchRowsForSource, trenchSelectedCounts };`
  )(state);
  return { state, ...api };
}

const row = (chain) => ({ symbol: chain.toUpperCase(), network: chain, chain: chain });

test("normalizeTrenchChain keeps real slugs and rejects anything else", () => {
  const { normalizeTrenchChain } = loadChainHelpers();
  assert.equal(normalizeTrenchChain("solana"), "solana");
  assert.equal(normalizeTrenchChain("SOLANA"), "solana");
  assert.equal(normalizeTrenchChain(" robinhood "), "robinhood");
  assert.equal(normalizeTrenchChain("all"), "all");
  // 宽松白名单：服务端日后新增链，旧 bundle 也不该把选择弹回「综合」。
  assert.equal(normalizeTrenchChain("newchain"), "newchain");
  assert.equal(normalizeTrenchChain(""), "all");
  assert.equal(normalizeTrenchChain(null), "all");
  assert.equal(normalizeTrenchChain(undefined), "all");
  assert.equal(normalizeTrenchChain("eth/../sol"), "all");
  assert.equal(normalizeTrenchChain("x".repeat(30)), "all");
});

test("trenchRowsForSource filters by network, or by chain when network is absent", () => {
  const source = {
    rows: [
      row("solana"),
      row("bsc"),
      { symbol: "ARC", chain: "arc" },
      row("robinhood")
    ]
  };
  const helpers = loadChainHelpers("all");
  assert.equal(helpers.trenchRowsForSource(source).length, 4);
  helpers.state.trenchChain = "solana";
  assert.deepEqual(helpers.trenchRowsForSource(source).map((item) => item.symbol), ["SOLANA"]);
  helpers.state.trenchChain = "arc";
  assert.deepEqual(helpers.trenchRowsForSource(source).map((item) => item.symbol), ["ARC"]);
  helpers.state.trenchChain = "eth";
  assert.deepEqual(helpers.trenchRowsForSource(source), []);
});

test("trenchRowsForSource survives a source without rows", () => {
  const helpers = loadChainHelpers("solana");
  assert.deepEqual(helpers.trenchRowsForSource({}), []);
  assert.deepEqual(helpers.trenchRowsForSource(null), []);
});

test("trenchSelectedCounts reads the selected chain out of chainCounts", () => {
  const source = {
    historyCount: 300,
    currentCount: 67,
    backfilledCount: 8,
    chainCounts: {
      all: { total: 300, current: 67, backfilled: 8 },
      solana: { total: 284, current: 60, backfilled: 3 },
      eth: { total: 0, current: 0, backfilled: 0 }
    }
  };
  const helpers = loadChainHelpers("all");
  assert.deepEqual(helpers.trenchSelectedCounts(source), { total: 300, current: 67, backfilled: 8 });
  helpers.state.trenchChain = "solana";
  assert.deepEqual(helpers.trenchSelectedCounts(source), { total: 284, current: 60, backfilled: 3 });
  helpers.state.trenchChain = "eth";
  assert.deepEqual(helpers.trenchSelectedCounts(source), { total: 0, current: 0, backfilled: 0 });
});

test("trenchSelectedCounts falls back to the flat totals for a stale payload", () => {
  const helpers = loadChainHelpers("all");
  helpers.state.trenchChain = "all";
  assert.deepEqual(helpers.trenchSelectedCounts({ rows: [{}, {}], historyCount: 300 }), {
    total: 300,
    current: 0,
    backfilled: 0
  });
  // 没有 chainCounts 时，选具体链只能显示 0，不能把扁平总量冒充成该链的量。
  helpers.state.trenchChain = "solana";
  assert.deepEqual(helpers.trenchSelectedCounts({ rows: [{}, {}], historyCount: 300 }), {
    total: 0,
    current: 0,
    backfilled: 0
  });
});

test("the trench card renders a chain dropdown that rowsForSourceView obeys", () => {
  assert.match(js, /const TRENCH_CHAIN_KEY = "xingyunshe:gmgn-trenches:chain:v1"/);
  assert.match(js, /data-role="trench-chain"/);
  assert.match(js, /is-gmgn-trenches-head is-onchain-hot/);
  // 选择要落进 state / localStorage，并把页码拨回第一页。
  assert.match(js, /state\.trenchChain = normalizeTrenchChain\(trenchChainSelect\.value\)/);
  assert.match(js, /saveLocalPreference\(TRENCH_CHAIN_KEY, state\.trenchChain\)/);
  assert.match(js, /trenchChain: normalizeTrenchChain\(readLocalPreference\(TRENCH_CHAIN_KEY, "all"\)\)/);
  const handler = js.slice(js.indexOf('select[data-role="trench-chain"]'));
  assert.match(handler.slice(0, 400), /state\.trenchPage = 1/);
  // rowsForSourceView 必须把战壕榜接到按链筛选上。
  assert.match(js, /if \(sourceId === "gmgn-trenches"\) return trenchRowsForSource\(source\);/);
  // 页码要夹在有效范围内（切链后旧页码越界会渲染空白）。
  assert.match(js, /const page = Math\.min\(Math\.max\(1, Number\(state\.trenchPage\) \|\| 1\), totalPages\);/);
  // 提示条要说明当前只看哪条链。
  assert.match(js, /gmgn-trench-chain-summary/);
  assert.match(css, /\.gmgn-trench-history-strip \.gmgn-trench-chain-summary \{/);
});

test("an empty chain says so instead of claiming the whole board is empty", () => {
  const body = js.slice(js.indexOf("function renderGmgnTrenchBoard("), js.indexOf("function renderGmgnTrenchBoard(") + 2400);
  assert.match(body, /if \(chainLabel\) \{/);
  assert.match(body, /当前没有新币/);
  // 未选链时才退回榜级空态。
  assert.match(body, /return `<div class="rows">\$\{renderEmpty\(source\)\}<\/div>`;/);
});

test("the server publishes the chain picker on the trench source", () => {
  assert.match(server, /def gmgn_trench_chain_picker\(/);
  assert.match(server, /def gmgn_trench_chain_tally\(/);
  assert.match(server, /chain_options, chain_counts = gmgn_trench_chain_picker\(/);
  assert.match(server, /"chainOptions": chain_options,/);
  assert.match(server, /"chainCounts": chain_counts,/);
  // 只发一条扁平 tape：战壕榜这一段绝不能像 GMGN 热搜 / Ave 那样再挂 chainBoards
  // （那会把 300 条重行按链复制好几份）。
  const trenchBlock = server.slice(
    server.indexOf("chain_options, chain_counts = gmgn_trench_chain_picker("),
    server.indexOf('"chainFilters": {', server.indexOf("chain_options, chain_counts = gmgn_trench_chain_picker("))
  );
  assert.doesNotMatch(trenchBlock, /"chainBoards":/);
  assert.match(server, /五链可按链筛选/);
});

test("the static bundle is bumped for the new dropdown", () => {
  assert.match(html, /app\.js\?v=45/);
  assert.match(html, /styles\.css\?v=124/);
});
