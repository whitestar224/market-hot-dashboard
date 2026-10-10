// 币安钱包热门榜的「链」下拉：客户端选行 + 与服务端的接线。
//
// 与战壕榜不同，这里的链视图**不做客户端筛选**：上游 rank 接口的 chainId 是服务端
// 过滤，每链返回自己独立的前 10，所以客户端只是从 chainBoards 里挑一条链的行。
// 下面把这两个函数从 app.js 抽出来真跑，而不是只 grep 源码。

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const js = fs.readFileSync(path.join(root, "app.js"), "utf8");
const css = fs.readFileSync(path.join(root, "styles.css"), "utf8");
const server = fs.readFileSync(path.join(root, "server.py"), "utf8");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");

function extract(name) {
  const start = js.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `app.js 里找不到 ${name}()`);
  const end = js.indexOf("\nfunction ", start + 1);
  assert.ok(end > start, `${name}() 之后没有顶层函数，截取失败`);
  return js.slice(start, end);
}

// 白名单是 app.js 顶层的 const Set，抽函数时要按**真实声明**注入（顺便证明它确实存在）。
const chainOptionsMatch = js.match(/const BINANCE_WALLET_CHAIN_OPTIONS = (new Set\(\[[^\]]*\]\));/);
assert.ok(chainOptionsMatch, "app.js 里找不到 BINANCE_WALLET_CHAIN_OPTIONS 声明");
const walletChainOptions = new Function(`return ${chainOptionsMatch[1]};`)();

function loadChainHelpers(initial = "all") {
  const state = { binanceWalletChain: initial };
  const source = [
    extract("normalizeBinanceWalletChain"),
    extract("binanceWalletRowsForSource")
  ].join("\n");
  const api = new Function(
    "state",
    "BINANCE_WALLET_CHAIN_OPTIONS",
    `return (function () { ${source}\nreturn { normalizeBinanceWalletChain, binanceWalletRowsForSource }; })();`
  )(state, walletChainOptions);
  return { state, ...api };
}

const row = (symbol) => ({ symbol, chain: "56", chainLabel: "BSC" });

const walletSource = {
  id: "binance-wallet-hot",
  rows: [row("GLOBAL1"), row("GLOBAL2")],
  chainOptions: [
    { value: "all", label: "综合" },
    { value: "bsc", label: "BSC" },
    { value: "sol", label: "Solana" }
  ],
  chainBoards: [
    { chain: "bsc", chainId: "56", label: "BSC", rows: [row("BSC1"), row("BSC2")] },
    { chain: "sol", chainId: "CT_501", label: "Solana", rows: [] }
  ]
};

test("normalizeBinanceWalletChain keeps the declared codes and rejects anything else", () => {
  const { normalizeBinanceWalletChain } = loadChainHelpers();
  for (const value of ["eth", "bsc", "base", "robinhood", "sol", "all"]) {
    assert.equal(normalizeBinanceWalletChain(value), value);
  }
  assert.equal(normalizeBinanceWalletChain(" BSC "), "bsc");
  assert.equal(normalizeBinanceWalletChain("SOL"), "sol");
  // 不在白名单里的（含服务端日后新增、旧 bundle 不认识的值）一律回落综合。
  assert.equal(normalizeBinanceWalletChain("arbitrum"), "all");
  assert.equal(normalizeBinanceWalletChain(""), "all");
  assert.equal(normalizeBinanceWalletChain(null), "all");
  assert.equal(normalizeBinanceWalletChain(undefined), "all");
  assert.equal(normalizeBinanceWalletChain("eth/../sol"), "all");
});

test("binanceWalletRowsForSource serves the global board for 综合", () => {
  const helpers = loadChainHelpers("all");
  assert.deepEqual(
    helpers.binanceWalletRowsForSource(walletSource).map((item) => item.symbol),
    ["GLOBAL1", "GLOBAL2"]
  );
});

test("binanceWalletRowsForSource serves that chain's own board, not a filter of the global one", () => {
  const helpers = loadChainHelpers("bsc");
  assert.deepEqual(
    helpers.binanceWalletRowsForSource(walletSource).map((item) => item.symbol),
    ["BSC1", "BSC2"]
  );
  // 综合榜里没有 SOL 行：链视图必须来自 chainBoards，筛综合榜只会得到空。
  helpers.state.binanceWalletChain = "sol";
  assert.deepEqual(helpers.binanceWalletRowsForSource(walletSource), []);
  helpers.state.binanceWalletChain = "eth";
  assert.deepEqual(helpers.binanceWalletRowsForSource(walletSource), []);
});

test("binanceWalletRowsForSource survives a payload without chainBoards", () => {
  const helpers = loadChainHelpers("bsc");
  // 旧缓存 bundle / 服务未重启：没有链板时不要抛，也不要凭空造行。
  assert.deepEqual(helpers.binanceWalletRowsForSource({ id: "binance-wallet-hot", rows: [row("G")] }), []);
  assert.deepEqual(helpers.binanceWalletRowsForSource({}), []);
  assert.deepEqual(helpers.binanceWalletRowsForSource(null), []);
});

test("the wallet card renders a chain dropdown that rowsForSourceView obeys", () => {
  assert.match(js, /const BINANCE_WALLET_CHAIN_KEY = "xingyunshe:binance-wallet-hot:chain:v1"/);
  assert.match(js, /const BINANCE_WALLET_CHAIN_OPTIONS = new Set\(\["all", "eth", "bsc", "base", "robinhood", "sol"\]\)/);
  assert.match(js, /data-role="binance-wallet-chain"/);
  assert.match(js, /aria-label="选择币安钱包热门榜的链"/);
  // 控件数变成两个，必须并入 is-onchain-hot 那套容器查询降级。
  assert.match(js, /class="board-head-actions is-wallet-hot is-onchain-hot"/);
  // 选择要落进 state / localStorage。
  assert.match(js, /state\.binanceWalletChain = normalizeBinanceWalletChain\(walletChainSelect\.value\)/);
  assert.match(js, /saveBinanceWalletChain\(state\.binanceWalletChain\)/);
  assert.match(js, /binanceWalletChain: readBinanceWalletChain\(\)/);
  // 切链是纯客户端动作，不该再打一次接口。
  const handler = js.slice(js.indexOf('select[data-role="binance-wallet-chain"]'));
  assert.doesNotMatch(handler.slice(0, 400), /loadBinanceWalletPeriod|fetch\(/);
  assert.match(handler.slice(0, 400), /queueMicrotask\(renderBoards\)/);
  // rowsForSourceView 必须把钱包榜接到按链选行上。
  assert.match(js, /if \(sourceId === "binance-wallet-hot"\) return binanceWalletRowsForSource\(source\);/);
});

test("the chain dropdown only exists when the server really sent chainBoards", () => {
  const body = js.slice(
    js.indexOf('if (String(source?.id || "") !== "binance-wallet-hot")'),
    js.indexOf("function totalPageTokens(")
  );
  assert.match(body, /const chainBoards = Array\.isArray\(source\.chainBoards\) \? source\.chainBoards : \[\];/);
  assert.match(body, /const chainOptions = chainBoards\.length/);
  assert.match(body, /const chainHtml = chainOptions\.length > 1/);
});

test("the chain view's ✦ narrative button is fetched lazily by the client", () => {
  // 服务端不为链板预取叙事（实测要多花约 40 秒），所以客户端必须放行钱包榜。
  const candidate = js.slice(
    js.indexOf("function exchangeAiCandidate("),
    js.indexOf("async function requestExchangeAiNarratives(")
  );
  assert.match(candidate, /"binance-wallet-hot"/);
  // 仍然要求行里有链 + 合约，纯文字行不该发请求。
  assert.match(candidate, /Boolean\(String\(row\?\.chain \|\| ""\)\.trim\(\) && exchangeAiContract\(row\)\)/);
});

test("the server publishes the chain picker on the wallet source", () => {
  assert.match(server, /def binance_wallet_hot_rows_from_tokens\(/);
  assert.match(server, /def binance_wallet_chain_board_rows\(/);
  assert.match(server, /def attach_binance_wallet_chain_boards\(/);
  assert.match(server, /chainId\"\] = normalized_chain|body\["chainId"\] = normalized_chain/);
  assert.match(server, /"chainOptions"\] = \[/);
  assert.match(server, /"chainBoards"\] = boards/);
  // 两张链表必须同源：下拉文案取 BINANCE_WALLET_HOT_CHAINS，行标签取 META。
  assert.match(server, /BINANCE_WALLET_HOT_CHAINS/);
});

test("the wallet card no longer carries its own duplicated degradation rules", () => {
  // 并入 is-onchain-hot 后，332px / 262px 那两块必须删掉，否则同族选择器互相盖。
  assert.doesNotMatch(css, /max-width: 332px/);
  assert.doesNotMatch(css, /is-wallet-hot > strong/);
  assert.match(css, /is-wallet-hot is-onchain-hot/);
});

test("an empty chain says so instead of claiming the whole board is empty", () => {
  const body = js.slice(js.indexOf("function renderEmpty("), js.indexOf("function renderInsight("));
  assert.match(body, /chain !== "all"/);
  assert.match(body, /当前没有标的/);
  assert.match(body, /切回「综合」可以看全链榜单/);
  // 未选链时才退回榜级空态（服务端下发的 emptyTitle/emptyMessage）。
  assert.match(body, /source\.emptyTitle \|\| "暂无数据"/);
});

test("the static bundle is bumped for the new dropdown", () => {
  assert.match(html, /app\.js\?v=46/);
  assert.match(html, /styles\.css\?v=125/);
});
