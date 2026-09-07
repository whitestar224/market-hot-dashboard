const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const js = fs.readFileSync(path.join(root, "app.js"), "utf8");
const ai = fs.readFileSync(path.join(root, "ai_insights.js"), "utf8");
const auth = fs.readFileSync(path.join(root, "auth.js"), "utf8");
const css = fs.readFileSync(path.join(root, "styles.css"), "utf8");
const server = fs.readFileSync(path.join(root, "server.py"), "utf8");

test("narrative strength stays inside the original independent-board layout", () => {
  assert.match(html, /<option value="rank">榜单原序<\/option>/);
  assert.match(html, /<option value="priority">叙事强弱<\/option>/);
  assert.match(html, /id="priorityPeriodSelect"/);
  assert.match(html, /<option value="1h">近 1 小时<\/option>/);
  assert.match(html, /<option value="6h">近 6 小时<\/option>/);
  assert.match(html, /<option value="24h">近 24 小时<\/option>/);
  assert.doesNotMatch(html, /priority-control-dock/);
  assert.match(html, /styles\.css\?v=86/);
  assert.match(html, /market_total_board\.js\?v=2/);
  assert.match(html, /app\.js\?v=20/);
  assert.match(js, /market-hot:payload:v8/);
  assert.match(js, /function activePriorityScores/);
  assert.match(js, /state\.sort === "priority"/);
  assert.doesNotMatch(js, /function renderPriorityBoard/);
});

test("total board is an additive deduplicated view and keeps the existing all view", () => {
  assert.match(html, /data-filter="total">总榜<\/button>[\s\S]*data-filter="all" class="active">全部<\/button>/);
  assert.match(js, /state\.filter === "total"/);
  assert.match(js, /function buildTotalBoardSource/);
  assert.match(js, /state\.sources\.filter\(\(source\) => deduper\?\.isTotalBoardSource\(source\)\)/);
  assert.match(js, /const TOTAL_BOARD_PAGE_SIZE = 10/);
  assert.match(js, /paginateTotalBoardEntries\(rankedRows, state\.totalPage, TOTAL_BOARD_PAGE_SIZE\)/);
  assert.match(js, /function renderTotalPagination/);
  assert.match(js, /data-role="total-page"/);
  assert.match(js, /id: "total-board"/);
  assert.match(css, /\.board-card\.is-total-board[\s\S]*grid-column: 1 \/ -1/);
  assert.match(css, /\.total-pagination/);
});

test("server keeps heat diagnostics but reorders each source card only by narrative", () => {
  assert.match(server, /MARKET_PRIORITY_WINDOWS = \{"1h":/);
  assert.match(server, /"mode": "narrative-strength"/);
  assert.match(server, /"formula": \{"narrative": 35\}/);
  assert.match(server, /"sort": "narrativeScore-desc"/);
  assert.match(server, /def market_priority_current_assets/);
  assert.match(server, /def market_priority_external_boosts/);
  assert.match(server, /"marketPriorityHistory": market_priority_history/);
  assert.match(js, /function priorityIdentityKeys/);
  assert.match(js, /rows: sortRows\(\(source\.rows \|\| \[\]\)\.filter\(matchesQuery\), priorityScores\)/);
  assert.match(js, /aProfile\?\.narrativeScore/);
  assert.match(js, /return bScore - aScore/);
  assert.match(css, /\.leaderboard-grid/);
});

test("AI insights visibly distinguish Codex, API and rule results", () => {
  assert.match(html, /ai_insights\.js\?v=6/);
  assert.match(ai, /if \(value === "codex-cli"\) return "Codex"/);
  assert.match(ai, /if \(value === "taxonomy" \|\| value === "rules"\) return "规则"/);
  assert.match(js, /providerLabel\?\.\(insight\.provider\)/);
  assert.match(css, /\.row-insight-text b/);
  assert.match(auth, /自动切换 Codex CLI/);
});

test("AI insight requests cover every board in progressive 24-row batches", () => {
  assert.match(ai, /const REQUEST_BATCH_ROWS = 24/);
  assert.match(ai, /function sourceBatches/);
  assert.match(ai, /for \(const batch of sourceBatches\(compact\)\)/);
  assert.match(ai, /state\.pendingKeys\.add/);
  assert.match(ai, /function isPending/);
  assert.match(fs.readFileSync(path.join(root, "insights.js"), "utf8"), /正在分析题材/);
});
