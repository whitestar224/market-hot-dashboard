const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const js = fs.readFileSync(path.join(root, "app.js"), "utf8");
const ai = fs.readFileSync(path.join(root, "ai_insights.js"), "utf8");
const auth = fs.readFileSync(path.join(root, "auth.js"), "utf8");
const css = fs.readFileSync(path.join(root, "styles.css"), "utf8");
const server = fs.readFileSync(path.join(root, "server.py"), "utf8");
const rankingPages = ["gainers.html", "turnover.html"].map((file) => fs.readFileSync(path.join(root, file), "utf8"));

test("narrative strength stays inside the original independent-board layout", () => {
  assert.match(html, /<option value="rank">榜单原序<\/option>/);
  assert.match(html, /<option value="priority">叙事强弱<\/option>/);
  assert.match(html, /id="priorityPeriodSelect"/);
  assert.match(html, /<option value="1h">近 1 小时<\/option>/);
  assert.match(html, /<option value="6h">近 6 小时<\/option>/);
  assert.match(html, /<option value="24h">近 24 小时<\/option>/);
  assert.doesNotMatch(html, /priority-control-dock/);
  assert.match(html, /styles\.css\?v=\d+/);
  assert.match(html, /market_total_board\.js\?v=\d+/);
  assert.match(html, /app\.js\?v=\d+/);
  assert.match(js, /market-hot:payload:v12/);
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
  assert.match(js, /const rows = rowsForSourceView\(source\)[\s\S]*sortRows\(rows, priorityScores\)/);
  assert.match(js, /aProfile\?\.narrativeScore/);
  assert.match(js, /return bScore - aScore/);
  assert.match(css, /\.leaderboard-grid/);
});

test("AI insights visibly distinguish Codex, API and rule results", () => {
  assert.match(html, /ai_insights\.js\?v=8/);
  assert.match(html, /id="rankAiToggle"[^>]*role="switch"/);
  assert.match(html, /id="rankAiToggleStatus">关闭</);
  assert.match(ai, /const ENABLED_KEY = "xingyun:rank-ai-enabled:v1"/);
  assert.match(ai, /enabledByPreference = localStorage\.getItem\(ENABLED_KEY\) === "1"/);
  assert.match(ai, /if \(!state\.enabled\) return;/);
  assert.match(ai, /if \(value === "codex-cli"\) return "Codex"/);
  assert.match(ai, /if \(value === "taxonomy" \|\| value === "rules"\) return "规则"/);
  assert.match(js, /providerLabel\?\.\(insight\.provider\)/);
  assert.match(css, /\.row-insight-text b/);
  assert.match(auth, /自动切换 Codex CLI/);
  rankingPages.forEach((page) => {
    assert.match(page, /id="rankAiToggle"[^>]*role="switch"/);
    assert.match(page, /ai_insights\.js\?v=8/);
  });
});

test("AI insight requests cover every board in progressive 24-row batches", () => {
  assert.match(ai, /const REQUEST_BATCH_ROWS = 24/);
  assert.match(ai, /function sourceBatches/);
  assert.match(ai, /for \(const batch of sourceBatches\(compact\)\)/);
  assert.match(ai, /state\.pendingKeys\.add/);
  assert.match(ai, /function isPending/);
  const insights = fs.readFileSync(path.join(root, "insights.js"), "utf8");
  assert.match(insights, /function buildFallbackInsight/);
  assert.match(insights, /return \{ \.\.\.fallback, pending: Boolean\(pending\) \}/);
  assert.match(insights, /binance\[-\\s\]\?wallet\|wallet\|币安钱包/);
  assert.match(html, /insights\.js\?v=7/);
});

test("Binance Wallet rows show an immediate onchain fallback while AI is pending", () => {
  const context = {
    window: {
      XingyunAiInsights: {
        getRowInsight: () => null,
        isPending: () => true,
        shouldDeferFallback: () => true
      }
    }
  };
  vm.runInNewContext(fs.readFileSync(path.join(root, "insights.js"), "utf8"), context);
  const result = context.window.XingyunInsights.buildRowInsight(
    { symbol: "PONS", name: "PONS", rank: 9, change: "-3.99%" },
    { source: { id: "binance-wallet-hot", title: "币安钱包热门榜", group: "crypto" }, rank: 9, mode: "hot" }
  );

  assert.equal(result.provider, "taxonomy");
  assert.equal(result.pending, true);
  assert.match(result.detail, /链上资金.*Meme轮动/);
  assert.notEqual(result.detail, "正在分析题材");
});

test("all eligible crypto boards render exchange-native AI narratives with provider attribution", () => {
  assert.match(server, /aiNarrativeFlag/);
  assert.match(server, /ai-widget\/analysis-narrative/);
  assert.match(server, /"Lang": "zh-CN"/);
  assert.match(server, /def exchange_ai_narratives_payload/);
  assert.match(server, /fetch_bitget_exchange_ai_narrative/);
  assert.match(server, /route == "\/api\/exchange-ai-narratives"/);
  assert.match(js, /function renderExchangeAiNarrative/);
  assert.match(js, /row\?\.exchangeAiNarrative \|\| row\?\.binanceAiNarrative/);
  assert.match(js, /\/api\/exchange-ai-narratives/);
  assert.match(js, /"binance", "binance-gainers"/);
  assert.match(js, /"aicoin"/);
  assert.doesNotMatch(js, /sourceId\.includes\("binance-wallet"\).*narrative/);
  assert.match(js, /免责声明：本内容由.*生成或整理/);
  assert.match(css, /\.binance-ai-narrative:hover \.binance-ai-narrative-tooltip/);
  assert.match(css, /\.binance-ai-narrative:focus-visible \.binance-ai-narrative-tooltip/);
});
