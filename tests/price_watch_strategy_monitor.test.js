const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "price-watch.html"), "utf8");
const js = fs.readFileSync(path.join(root, "price-watch.js"), "utf8");
const css = fs.readFileSync(path.join(root, "styles.css"), "utf8");
const server = fs.readFileSync(path.join(root, "server.py"), "utf8");
const chainMonitor = fs.readFileSync(path.join(root, "chain_ecosystem_monitor.py"), "utf8");

test("strategy replacement preserves the original monitor card layout", () => {
  assert.match(html, /id="priceWatchGrid" class="price-watch-grid"/);
  assert.match(js, /class="price-structure-card\$\{newLowCard \? " is-new-low" : ""\}"/);
  assert.match(js, /class="price-structure-row is-\$\{tone\}"/);
  assert.match(js, /structureItems\.map\(structureCardTemplate\)/);
});

test("News Trade is the first monitor tab", () => {
  const tablist = html.match(/<div class="price-watch-modes"[\s\S]*?<\/div>/)?.[0] || "";
  const modeOrder = [...tablist.matchAll(/data-watch-mode="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(modeOrder[0], "news");
  assert.equal(modeOrder.filter((mode) => mode === "news").length, 1);
});

test("hot-coin structure tab now describes all six shared strategy frames", () => {
  assert.match(js, /1分钟、5分钟、15分钟、1小时、4小时和日线/);
  assert.match(js, /使用龙头起爆策略识别A\+买点与多周期共振/);
  assert.match(html, /price-watch\.js\?v=51/);
});

test("multi-timeframe page follows the authoritative background pool every three seconds", () => {
  assert.match(js, /const STRUCTURE_SYNC_INTERVAL_MS = 3_000/);
  assert.match(js, /if \(structureLoading\) return/);
  assert.match(js, /loadStructures\(\{ live: true, quiet: true \}\)/);
  assert.match(js, /后台监控池与页面每 3 秒同步/);
  assert.doesNotMatch(js, /每 3 分钟更新/);
});

test("prior-high removal is isolated from oversold and restores only after an AICoin re-entry", () => {
  assert.match(js, /data-exclude-prior-high=/);
  assert.match(js, /postAction\("exclude_prior_high", symbol\)/);
  assert.match(js, /item\.priorHighEnabled !== false/);
  assert.match(server, /prior_high_excluded_at/);
  assert.match(server, /prior_high_absent_at/);
  assert.match(server, /restoreRule": "aicoin_leave_then_reenter"/);
  assert.match(server, /DELETE FROM price_watch_alert_state WHERE symbol = \?/);
  assert.match(server, /DELETE FROM price_watch_first_confirmations WHERE symbol = \?/);
});

test("multi-timeframe cards support an isolated manual exclusion", () => {
  assert.match(js, /data-exclude-structure=/);
  assert.match(js, /postStructureAction\("exclude", symbol\)/);
  assert.match(js, /structureItems = structureItems\.filter/);
  assert.match(server, /CREATE TABLE IF NOT EXISTS price_structure_exclusions/);
  assert.match(server, /def exclude_price_structure_symbol/);
  assert.match(server, /price_structure_symbol_excluded\(symbol\)/);
  assert.match(html, /styles\.css\?v=85/);
});

test("group monitoring supports a targeted QQ speaker, structure admission, and WeChat forwarding", () => {
  assert.match(js, /<option value="qq">Q群<\/option>/);
  assert.match(js, /指定发言 ID（Q群必填）/);
  assert.match(js, /新消息转发到微信文件传输助手/);
  assert.match(js, /item\.platform === "qq" \? "Q群 · " : "微信 · "/);
  assert.match(server, /DEFAULT_QQ_GROUP_NAME = "地表最强bsc eth"/);
  assert.match(server, /DEFAULT_QQ_SENDER_FILTER = "鲸鱼🐳PP"/);
  assert.match(server, /directly_monitored_symbols\.add\(symbol\)/);
  assert.match(server, /enqueue_chat_message_forward/);
  assert.match(server, /process_chat_message_forward_outbox/);
  assert.match(css, /\.wechat-monitor-source-icon\.is-qq/);
});

test("each multi-timeframe card exposes six independent broadcast switches", () => {
  assert.match(js, /data-structure-interval=/);
  assert.match(js, /postStructureAction\("set_interval"/);
  assert.match(js, /STRUCTURE_INTERVALS/);
  assert.match(js, /structure1mEnabled/);
  assert.match(js, /structureIntervalStates/);
  assert.match(css, /\.price-structure-interval-toggle\.is-on/);
  assert.match(css, /\.price-structure-interval-toggle\.is-off/);
  assert.match(js, /enabled \? "开" : "关"/);
});

test("server monitor launches deduplicated popup and voice alerts for shared-engine signals", () => {
  assert.match(server, /run_dragon_wave_monitor_strategy\(strategy_timeframes, adaptive_context=adaptive_context\)/);
  assert.doesNotMatch(server, /def recognize_price_structure\(/);
  assert.match(server, /launch_price_structure_strategy_alerts/);
  assert.match(server, /price-watch:dragon-wave:\{symbol\}:\{interval\}:\{decision_time\}/);
  assert.match(server, /"speech": \(/);
  assert.match(server, /start_price_structure_strategy_monitor\(\)/);
  assert.match(server, /strategy_adaptive_context_for_symbol/);
  assert.match(server, /update_strategy_contexts_from_personal_x_payload/);
  assert.match(server, /source_kind="qq" if platform == "qq" else "wechat"/);
  assert.match(server, /force_refresh else price_watch_payload\(sync_candidates=False\)/);
});

test("recent-year new coins have a dedicated low-position structure monitor", () => {
  assert.match(html, /data-watch-mode="newlow">新币低位结构/);
  assert.match(js, /\/api\/new-coin-low-structures/);
  assert.match(js, /近一年新币低位结构/);
  assert.match(js, /按跨交易所<b>首次上架<\/b>计算近 365 天币龄/);
  assert.match(js, /仅监控 <b>1小时 \/ 4小时<\/b>/);
  assert.match(js, /已剔除不活跃/);
  assert.match(js, /1000 万美元/);
  assert.match(js, /恢复后自动加回/);
  assert.match(js, /\["1h", "4h"\]\.includes\(interval\.key\)/);
  assert.match(js, /K线 · Aster备用/);
  assert.match(server, /def new_coin_low_position_context/);
  assert.match(server, /NEW_COIN_LOW_STRUCTURE_INTERVALS = frozenset\(\{"1h", "4h"\}\)/);
  assert.match(server, /NEW_COIN_LOW_MIN_TURNOVER_24H_USD/);
  assert.match(server, /def new_coin_low_activity_state/);
  assert.match(server, /def price_structure_monitor_next_rows/);
  assert.match(server, /def filter_price_monitor_rows_by_activity/);
  assert.match(js, /仅保留 24H 成交额不低于 <b>1000 万美元<\/b>/);
  assert.match(server, /merge_new_coin_low_listing_candidate/);
  assert.match(server, /start_new_coin_low_structure_monitor\(\)/);
  assert.match(js, /price-structure-card\$\{newLowCard \? " is-new-low" : ""\}/);
  assert.match(js, /newLowCard \? "" : `<span class="price-structure-price">/);
  assert.match(js, /newLowCard \? "" : `<span class="price-watch-origin"/);
  assert.match(css, /\.price-structure-card\.is-new-low > header/);
  assert.match(css, /\.price-structure-card\.is-new-low \.price-watch-asset strong/);
});

test("News Trade exposes event heat and extracted news keywords", () => {
  assert.match(js, />事件热度</);
  assert.match(js, /\["大瓜", Number\(eventPoints\.bigGossip\)/);
  assert.match(js, /<span>事件热度<\/span><span>大瓜<\/span>/);
  assert.match(js, /新闻关键词/);
  assert.match(server, /def event_monitor_news_keywords/);
  assert.match(server, /"bigGossip": 20\.0/);
  assert.match(server, /record_desktop_alert_news_trade_intake/);
  assert.match(js, /newsTradeTargetsTemplate/);
  assert.match(js, /\u6d89\u53ca\u6807\u7684 \/ TARGETS/);
  assert.match(js, /class="news-trade-candidate-buy /);
  assert.match(js, /candidateContract/);
  assert.match(css, /\.news-trade-target-zone/);
  assert.match(css, /\.news-trade-candidate-buy/);
  assert.match(server, /"sort": "enteredAt-firstSeenAt-desc"/);
});

test("News Trade blocks unsafe contracts and previews fees, slippage and minimum received", () => {
  assert.match(server, /def news_trade_fetch_security/);
  assert.match(server, /def news_trade_execution_cost_estimate/);
  assert.match(server, /NEWS_TRADE_MAX_PRICE_IMPACT_PCT/);
  assert.match(server, /minimumReceivedUsdEquivalent/);
  assert.match(js, /news-trade-security is-/);
  assert.match(js, /买入前成本与安全提醒/);
  assert.match(js, /建议滑点上限/);
  assert.match(js, /最低可得金额/);
  assert.match(css, /\.news-trade-execution-notice\.is-blocked/);
});

test("News Trade wallet adapter can switch accounts and use Binance Wallet", () => {
  assert.match(js, /eip6963:requestProvider/);
  assert.match(js, /window\.binancew3w\?\.ethereum/);
  assert.match(js, /window\.BinanceChain\?\.request/);
  assert.match(js, /wallet_requestPermissions/);
  assert.match(js, /data-wallet-provider/);
  assert.match(js, /walletProvider: okxWalletState\.providerKey \|\| "okx"/);
  assert.match(server, /provider in \{"okx", "binance"\}/);
  assert.match(css, /\.news-trade-wallet-actions/);
});

test("News Trade candidate safety is compact and paired topic cards stay aligned", () => {
  assert.match(js, /news-trade-candidate-name/);
  assert.match(js, /news-trade-candidate-foot-actions/);
  assert.match(js, /securityStatus === "safe"/);
  assert.match(css, /grid-template-rows: repeat\(3, minmax\(54px, 1fr\)\)/);
  assert.match(css, /\.price-watch-grid\.is-events > \.event-monitor-card/);
  assert.match(css, /\.price-watch-grid\.is-events \{[^}]*column-gap: 18px;[^}]*row-gap: 22px;/s);
  assert.match(css, /\.news-trade-security > i/);
  assert.doesNotMatch(css, /\.news-trade-security \{[^}]*grid-column: 2 \/ -1/s);
});

test("chain watch admission responds before the background ecosystem scan", () => {
  assert.match(js, /chainActionLoading/);
  assert.match(js, /\u6b63\u5728\u52a0\u5165\u89c2\u5bdf…/);
  assert.match(js, /\u9996\u6b21\u626b\u63cf\u5df2\u5728\u540e\u53f0\u8fd0\u884c/);
  assert.match(chainMonitor, /"refreshScheduled": refresh_scheduled/);
  assert.match(css, /chain-submit-pulse/);
});
