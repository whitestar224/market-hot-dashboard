const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const js = fs.readFileSync(path.join(root, "app.js"), "utf8");
const css = fs.readFileSync(path.join(root, "styles.css"), "utf8");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const server = fs.readFileSync(path.join(root, "server.py"), "utf8");

test("GMGN Hot Search has the native five periods and chain switch", () => {
  assert.match(js, /const GMGN_PERIODS = new Set\(\["1m", "5m", "1h", "6h", "24h"\]\)/);
  assert.match(js, /const GMGN_CHAIN_OPTIONS = new Set\(\["all", "sol", "bsc", "base", "eth", "robinhood", "arc", "stable"\]\)/);
  assert.match(js, /function gmgnRowsForSource/);
  assert.match(js, /periodBoards/);
  assert.match(js, /data-role="gmgn-period"/);
  assert.match(js, /data-role="gmgn-chain"/);
  assert.match(js, /saveLocalPreference\(GMGN_PERIOD_KEY/);
  assert.match(js, /saveLocalPreference\(GMGN_CHAIN_KEY/);
  assert.match(js, /function replaceGmgnHotSource/);
  assert.match(js, /async function loadGmgnHotSource/);
  assert.match(js, /fetch\(`\/api\/gmgn-hot-search/);
  assert.match(server, /parsed\.path == "\/api\/gmgn-hot-search"/);
});

test("wallet, GMGN trenches, and AIcoin occupy the first row in that order", () => {
  const wallet = server.indexOf('(\"binance-wallet-hot\", lambda: binance_wallet_hot_source(\"24h\"))');
  const trenches = server.indexOf('(\"gmgn-trenches\", fetch_gmgn_trenches_hot_board)');
  const aicoin = server.indexOf('(\"aicoin\", fetch_aicoin)');
  const binance = server.indexOf('(\"binance\", fetch_binance)');
  assert.ok(wallet >= 0 && trenches > wallet && aicoin > trenches);
  assert.ok(binance > aicoin);
  assert.match(server, /RANK_MONITOR_SKIP_UPDATE_SOURCES = \{[\s\S]{0,300}"gmgn-hot-search"/);
});

test("GMGN trench history is scrollable and uses the cached Binance AI narrative route", () => {
  assert.match(css, /\.board-head-actions\.is-onchain-hot/);
  assert.match(css, /\.gmgn-trench-hot-scroll/);
  assert.match(css, /\.gmgn-trench-popover/);
  assert.match(css, /\.gmgn-trench-risk-note/);
  assert.match(css, /\.gmgn-trench-research-badge/);
  assert.match(css, /\.gmgn-trench-person-badge/);
  assert.match(css, /\.gmgn-trench-hot-row\.is-research-pick/);
  assert.match(js, /function renderGmgnTrenchBoard/);
  assert.match(js, /严格按开盘时间倒序，首屏最新 10 个/);
  assert.match(js, /gmgn-trench-risk-note/);
  assert.match(js, /gmgn-trench-research-badge/);
  assert.match(js, /gmgn-trench-person-badge/);
  assert.match(js, /kind: "person"/);
  assert.match(js, /V4\.7 好标的/);
  assert.match(js, /is-research-pick/);
  assert.match(js, /币安 AI 叙事/);
  assert.match(js, /sourceId === "gmgn-trenches"/);
  assert.match(js, /sourceRows\.slice\(0, Math\.min\(10/);
  assert.match(js, /const gmgnTrenchBatchLimit = 4/);
  assert.match(js, /class="gmgn-trench-token-link"/);
  assert.match(js, /kind: "wallet"/);
  assert.match(js, /noopener noreferrer/);
  assert.doesNotMatch(js, /gmgn-trenches\/ai/);
  assert.match(server, /"gmgn-trenches",\s*\n?\}/);
  assert.match(server, /worker_limit = 2 if contains_trenches else 6/);
  assert.match(server, /GMGN_TRENCH_BOARD_REFRESH_SECONDS/);
  assert.match(server, /def binance_wallet_contract_url/);
  assert.match(server, /"binanceWalletUrl": binance_wallet_contract_url/);
  assert.match(server, /gmgn_trenches_received_history\.json/);
  assert.match(server, /"excludeFromTotal": True/);
  assert.match(html, /styles\.css\?v=112/);
  assert.match(html, /market_total_board\.js\?v=3/);
  assert.match(html, /app\.js\?v=37/);
  assert.match(js, /market-hot:payload:v15/);
});

test("GMGN X hover lazily loads the corresponding original post and media", () => {
  assert.match(js, /function renderGmgnTrenchXTooltip/);
  assert.match(js, /\/api\/gmgn-trench-x-post\?statusId=/);
  assert.match(js, /boardsEl\.addEventListener\("pointerover"/);
  assert.match(js, /gmgnTrenchXPostQueue/);
  assert.match(js, /不会预抓全部历史帖子/);
  assert.match(css, /\.gmgn-trench-x-head/);
  assert.match(css, /\.gmgn-trench-x-media/);
  assert.match(server, /def gmgn_trench_x_post_payload/);
  assert.match(server, /parsed\.path == "\/api\/gmgn-trench-x-post"/);
  assert.match(server, /GMGN_TRENCH_X_POST_CACHE_TTL_SECONDS/);
});

test("GMGN trench contracts can be copied with immediate inline feedback", () => {
  assert.match(js, /class="gmgn-trench-ca-copy"/);
  assert.match(js, /data-contract="\$\{escapeHtml\(contract\)\}"/);
  assert.match(js, /navigator\.clipboard\?\.writeText/);
  assert.match(js, /document\.execCommand\("copy"\)/);
  assert.match(js, /copyGmgnTrenchContract\(contractButton\)/);
  assert.match(js, /CA 已复制/);
  assert.match(css, /\.gmgn-trench-ca-copy\.is-copied/);
  assert.match(css, /cursor: copy/);
});

test("the first-row boards share a compact, aligned visual rhythm", () => {
  assert.match(css, /@media \(min-width: 721px\) \{[\s\S]{0,180}\.leaderboard-grid \.board-head \{[\s\S]{0,80}height: 90px;/);
  assert.match(css, /\.gmgn-trench-history-strip \{[\s\S]{0,180}min-height: 34px;/);
  assert.match(css, /\.gmgn-trench-hot-row \{[\s\S]{0,260}min-height: 56px;[\s\S]{0,80}padding: 5px 12px;/);
  assert.match(css, /\.gmgn-trench-hot-scroll \{[\s\S]{0,120}max-height: 560px;/);
  assert.match(css, /\.gmgn-trench-hot-row \{[\s\S]{0,260}height: 56px;/);
  assert.match(css, /\.gmgn-trench-avatar \.asset-icon \{[\s\S]{0,100}width: 38px;[\s\S]{0,60}height: 38px;/);
  assert.match(css, /\.gmgn-trench-subline \{[\s\S]{0,180}height: 12px;[\s\S]{0,220}white-space: nowrap;/);
});
