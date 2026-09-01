const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "dragon-wave.html"), "utf8");
const css = fs.readFileSync(path.join(root, "dragon-wave.css"), "utf8");
const js = fs.readFileSync(path.join(root, "dragon-wave.js"), "utf8");
const dataSource = fs.readFileSync(path.join(root, "dragon-wave-data.js"), "utf8");
const feedbackSource = fs.readFileSync(path.join(root, "dragon-wave-feedback.js"), "utf8");
const engineSource = fs.readFileSync(path.join(root, "dragon-wave-engine.js"), "utf8");
const analysisWorkerSource = fs.readFileSync(path.join(root, "dragon-wave-analysis-worker.js"), "utf8");
const quietServerSource = fs.readFileSync(path.join(root, "quiet_http_server.py"), "utf8");
const serverSource = fs.readFileSync(path.join(root, "server.py"), "utf8");
const launcher = fs.readFileSync(path.join(root, "打开龙头起爆策略页面.cmd"), "utf8");
const precomputeSource = fs.readFileSync(path.join(root, "tools", "precompute_dragon_wave_cases.js"), "utf8");
const precomputeLauncher = fs.readFileSync(path.join(root, "tools", "start_dragon_wave_precompute.ps1"), "utf8");
const Cases = require("../dragon-wave-cases.js");
const Data = require("../dragon-wave-data.js");

test("page uses one TradingView-style main chart with five switchable core timeframes", () => {
  const intervals = [...html.matchAll(/data-timeframe="([^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(intervals, ["5m", "15m", "1h", "4h", "1d"]);
  for (const id of ["caseSelect", "symbolInput", "marketSelect", "providerSelect", "focusTime", "mainWaveMode", "analysisNote", "loadButton", "showRejected"]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.equal((html.match(/<canvas\b/g) || []).length, 1);
  assert.match(html, /id="mainChart"/);
  assert.match(html, /id="exportChartImage"/);
  assert.match(js, /function exportActiveChartImage\(\)/);
  assert.match(js, /chart\.canvas\.toBlob/);
});

test("preview remains isolated from the existing application navigation", () => {
  assert.doesNotMatch(html, /class="page-nav"/);
  assert.match(html, /PREVIEW 01/);
  assert.match(html, /只读研究 · 不下单/);
});

test("responsive styles cover a large primary chart, stacked layout and reduced motion", () => {
  assert.match(css, /\.main-timeframe-card \.chart-surface/);
  assert.match(css, /\.chart-grid-single/);
  assert.match(css, /@media \(max-width: 1160px\)/);
  assert.match(css, /@media \(max-width: 760px\)/);
  assert.match(css, /prefers-reduced-motion/);
});

test("catalog UI includes the screenshot symbol and a dynamic live-leader rail", () => {
  assert.match(js, /symbol:\s*"AKE"/);
  assert.match(js, /TUT/);
  assert.match(html, /id="leaderRadar"/);
  assert.match(html, /id="leaderChips"/);
  assert.match(html, /id="liveLeaderCount"/);
});

test("causal buys are visually separated from untriggered pre-armed structures", () => {
  assert.match(html, /预备触发/);
  assert.match(html, /data-ledger-filter="pending"/);
  assert.match(html, /id="signalPolicyNote"/);
  assert.match(html, /不读取本根最终收盘颜色/);
  assert.doesNotMatch(html, /id="showRejected"[^>]*checked/);
  assert.match(css, /\.ledger-item\.is-pending/);
  assert.match(js, /result\.pending/);
  assert.match(html, /试错后重新形成的平台外沿可再次武装/);
  assert.match(html, /审美分已移除/);
  assert.doesNotMatch(js, /盘感|aestheticScore|aestheticGrade/);
  assert.doesNotMatch(engineSource, /aestheticScore|aestheticGrade/);
  assert.match(js, /BTC 仅作背景/);
});

test("retained consolidation candidates stay in the quiet review ledger and never become chart B markers", () => {
  assert.match(html, /data-ledger-filter="candidate"/);
  assert.match(html, /安静候选/);
  assert.match(css, /\.ledger-item\.is-candidate/);
  assert.match(js, /result\.retainedCandidates/);
  assert.match(js, /candidateReasons/);
  const feedbackSignalsStart = js.indexOf("feedbackSignals(includeHiddenRejected");
  const feedbackSignalsEnd = js.indexOf("\n    }", feedbackSignalsStart);
  const feedbackSignalsBody = js.slice(feedbackSignalsStart, feedbackSignalsEnd);
  assert.doesNotMatch(feedbackSignalsBody, /retainedCandidates/);
});

test("secondary breakout hints use a red B lane without becoming formal green buys", () => {
  assert.match(html, /防洗二次突破/);
  assert.match(css, /\.chart-legend \.is-secondary-hint/);
  assert.match(css, /\.ledger-item\.is-secondary-hint/);
  assert.match(js, /result\.secondaryBreakoutHints/);
  assert.match(js, /secondaryBreakoutHint/);
  assert.match(js, /#ff5d6c/);
  assert.match(js, /alertOnly/);
});

test("manual confirmation is permanent locally and syncs to the signed-in account", () => {
  assert.match(html, /data-ledger-filter="confirmed"/);
  assert.match(html, /id="feedbackSync"/);
  assert.match(js, /dragon-wave-feedback-v1/);
  assert.match(js, /api\/dragon-wave-feedback\/local/);
  assert.match(js, /api\/dragon-wave-feedback\/account/);
  assert.match(js, /data-feedback-action="confirmed"/);
  assert.match(js, /data-feedback-action="denied"/);
  assert.match(feedbackSource, /manualRestored/);
  assert.match(feedbackSource, /feedbackAdjustment/);
});

test("feedback confirmation updates optimistically and batches heavy persistence work", () => {
  assert.match(js, /applyOptimisticFeedback\(item, decision\)/);
  assert.match(js, /feedbackDirtyKeys: new Set\(\)/);
  assert.match(js, /function queueLoadedFeedbackRefresh\(delay = 900\)/);
  assert.match(js, /function queueFeedbackPersistence\(delay = 360\)/);
  assert.match(js, /"X-Dragon-Wave-Compact": "1"/);
  assert.match(js, /state\.feedback\.records\[key\] = update\.records\[key\]/);
  const recordStart = js.indexOf("function recordFeedback(");
  const recordEnd = js.indexOf("\n  function renderLedger", recordStart);
  const recordBody = js.slice(recordStart, recordEnd);
  assert.doesNotMatch(recordBody, /await persistFeedback\(/);
  assert.doesNotMatch(recordBody, /refreshLoadedFeedback\(\)/);
  assert.match(recordBody, /queueLoadedFeedbackRefresh\(\)/);
  assert.match(recordBody, /queueFeedbackPersistence\(\)/);
  assert.match(feedbackSource, /function prepareApplicationContext\(document\)/);
  assert.match(js, /Feedback\.prepareApplicationContext\(state\.feedback\)/);
  assert.match(quietServerSource, /X-Dragon-Wave-Compact/);
  assert.match(serverSource, /X-Dragon-Wave-Compact/);
});

test("human analysis context can declare a main-wave expectation without bypassing structure rules", () => {
  assert.match(html, /id="mainWaveMode"/);
  assert.match(html, /我给出主升浪预期/);
  assert.match(html, /我确认已进入主升浪/);
  assert.match(html, /id="analysisNote"/);
  assert.match(html, /不会替代成熟结构、真实外沿和突破触发/);
  assert.match(html, /新币不跌后的主升浪/);
  assert.match(html, /EMA90未形成不视为转弱/);
  assert.match(js, /dragon-wave-analysis-context-v1/);
  assert.match(js, /function saveHumanAnalysisContext/);
  assert.match(js, /leaderDefaulted = savedStage === "auto" && isPreselectedLeaderPair\(pair\)/);
  assert.match(js, /leaderDefaulted \? "active" : savedStage/);
  assert.match(js, /leader-default-main-wave/);
  assert.match(js, /龙头默认主升浪环境/);
  assert.match(js, /mainWaveStage: analysisContext\.mainWaveStage/);
  assert.match(js, /mainWaveStage: requestPair === pair \? analysisContext\.mainWaveStage : "auto"/);
  assert.match(js, /STRATEGY_CACHE_VERSION\}:\$\{normalizeMainWaveStage\(params\.mainWaveStage\)\}/);
  assert.match(engineSource, /人工主升判断只放宽“环境许可”/);
  assert.match(engineSource, /declaredMainWavePermit/);
  assert.match(engineSource, /newCoinNotFallingMainWavePermit/);
  assert.match(css, /\.human-analysis-context/);
});

test("the page, strategy engine and persistent analysis cache publish the same strategy version", () => {
  assert.match(js, /STRATEGY_CACHE_VERSION = "v89"/);
  for (const asset of ["dragon-wave-cases.js", "dragon-wave-data.js", "dragon-wave-engine.js", "dragon-wave.js"]) {
    assert.match(html, new RegExp(`${asset.replace(".", "\\.")}\\?v=89`));
  }
  assert.match(analysisWorkerSource, /dragon-wave-engine\.js\?v=89/);
});

test("buy-point feedback can be confirmed or denied directly on the K-line canvas", () => {
  assert.match(html, /class="chart-feedback-popover"/);
  assert.match(html, /data-chart-feedback-action="confirmed"/);
  assert.match(html, /data-chart-feedback-action="denied"/);
  assert.match(html, /data-chart-feedback-action="pending"/);
  assert.match(html, /彻底否定/);
  assert.match(js, /hitFeedbackSignal\(event\)/);
  assert.match(js, /selectFeedbackAt\(event\)/);
  assert.match(js, /new WaveChart\(card, syncCrosshair, recordFeedback\)/);
  assert.match(css, /\.chart-feedback-popover\.is-visible/);
  assert.match(css, /canvas\.is-feedback-target/);
});

test("primary chart uses the full workspace and the feedback decisions stay visible in a bounded card", () => {
  assert.match(html, /class="wave-workspace is-chart-only"/);
  assert.match(html, /class="intel-column" hidden aria-hidden="true"/);
  assert.match(css, /\.wave-workspace\.is-chart-only\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/s);
  assert.match(css, /\.chart-feedback-popover\s*\{[^}]*max-height:\s*calc\(100% - 16px\)[^}]*overflow-y:\s*auto/s);
  assert.match(css, /\.chart-feedback-decisions\s*\{[^}]*position:\s*sticky[^}]*bottom:\s*31px/s);
  assert.match(css, /\.chart-feedback-popover > footer\s*\{[^}]*position:\s*sticky[^}]*bottom:\s*0/s);
  assert.match(css, /\.chart-structure-confirm > div\s*\{[^}]*max-height:\s*146px[^}]*overflow-y:\s*auto/s);
});

test("manually confirmed buys keep a visible B marker on the candle", () => {
  assert.match(js, /ctx\.fillText\("B", x, y \+ 15\)/);
  assert.match(js, /人工确认只增加永久锁定圆环/);
  assert.doesNotMatch(js, /manualDecision === "confirmed" \? "✓" : "B"/);
  assert.match(js, /const selectedConfirmed = selectedSignal\.manualDecision === "confirmed"/);
  assert.match(js, /if \(!selectedConfirmed\) ctx\.fill\(\)/);
  assert.match(js, /buyMarkerY\(signal\)/);
  assert.match(js, /Number\(row\?\.low \?\? signal\?\.price \?\? signal\?\.level\)/);
  assert.match(js, /if \(decision === "confirmed"\) this\.closeFeedbackPopover\(\)/);
});

test("missed buys can be selected from any candle, permanently learned and safely cancelled", () => {
  assert.match(html, /data-draw-tool="feedback"/);
  assert.match(html, /选择K线补标/);
  assert.match(html, /data-chart-feedback-clear/);
  assert.match(js, /selectCandleFeedbackAt\(event\)/);
  assert.match(js, /this\.feedbackSignals\(false\)\.find\(\(item\) => item\.index === point\.index\)/);
  assert.match(js, /this\.feedbackPress = \{ pointerId:/);
  assert.match(js, /hitCandle\(event\)/);
  assert.match(js, /candleWidth \/ 2 \+ 3/);
  assert.match(js, /else if \(this\.hitCandle\(event\)\) this\.selectCandleFeedbackAt\(event\)/);
  assert.match(js, /classList\.toggle\("is-feedback-target", Boolean\(this\.hitCandle\(event\)\)\)/);
  assert.match(js, /buildManualFeedbackSignal\(index, clickedPrice\)/);
  assert.match(js, /selected-candle-intrabar-no-future-bars/);
  assert.match(js, /manualSource: "chart-candle-picker"/);
  assert.match(js, /\["confirmed", "pending", "denied", "cleared"\]/);
  assert.match(js, /data-feedback-action="cleared"/);
  assert.match(feedbackSource, /record\.decision !== "cleared"/);
  assert.match(css, /is-feedback-picking/);
});

test("existing manual reviews are lazily backfilled with causal visual signatures when chart history is loaded", () => {
  assert.match(js, /function hydrateVisualFeedbackForResult\(result, pair\)/);
  assert.match(js, /Vision\.buildVisualSignature\(result\.candles, index/);
  assert.match(js, /visualSignature\?\.version === Vision\.VERSION/);
  assert.match(js, /visualHydratedAt/);
  assert.match(js, /completed-candles-before-selected-index-only/);
});

test("manual reviews expand visual ranges by exact candle with confirm and cancel", () => {
  assert.doesNotMatch(html, /data-chart-visual-range-start/);
  assert.doesNotMatch(html, /选择结构起点/);
  assert.match(html, /data-chart-visual-range-adjust="expand"/);
  assert.match(html, /data-chart-visual-range-adjust="contract"/);
  assert.match(html, /默认自动/);
  assert.match(html, /data-chart-visual-range-reset/);
  assert.match(html, /data-chart-visual-range-confirm/);
  assert.match(html, /data-chart-visual-range-commit/);
  assert.match(html, /data-chart-visual-range-cancel/);
  assert.match(html, /确认前只预览/);
  assert.match(js, /beginVisualRangeExpansion/);
  assert.match(js, /selectVisualRangeDraft/);
  assert.match(js, /commitVisualRangeExpansion/);
  assert.match(js, /cancelVisualRangeExpansion/);
  assert.match(js, /point\.index < this\.visualRangeDraft\.currentStartIndex/);
  assert.match(js, /await this\.persistVisualRangeItem\(this\.visualRangeItem\(draft\.item, draft\.draftStartIndex, "manual"\)\)/);
  assert.match(js, /this\.restoreDefaultPanTool\(\);\s*this\.closeFeedbackPopover\(\);/);
  assert.match(js, /adjustVisualRangeSelection/);
  assert.match(js, /showVisualRangeHint\("请向左点选结构起始 K 线", 3600\)/);
  assert.match(js, /起始 K 线已选，请在反馈卡片中确认/);
  assert.match(js, /hideVisualRangeHint/);
  assert.match(js, /VISUAL_RANGE_MIN_BARS = 12/);
  assert.match(js, /Math\.max\(8, Math\.round\(currentBars \* 0\.3\)\)/);
  assert.match(js, /visualStructureStartTime/);
  assert.match(js, /structureStartIndex/);
  assert.match(js, /VISUAL RANGE/);
  assert.match(css, /data-chart-visual-range-reset/);
  assert.match(css, /is-visual-range-picking/);
  assert.match(css, /is-visual-range-target/);
  assert.match(css, /\.chart-range-hint\.is-visible/);
});

test("visual learning uses a distinct V pre-confirmation with auditable positive and negative similarities", () => {
  assert.match(html, /V · 视觉预确认/);
  assert.match(js, /signal\.visualPreconfirmed/);
  assert.match(js, /ctx\.fillText\("V"/);
  assert.match(js, /data-chart-visual-learning/);
  assert.match(js, /positiveSimilarity/);
  assert.match(js, /negativeSimilarity/);
  assert.match(js, /const badge = .*VISION/s);
  assert.doesNotMatch(js, /visualPreconfirmed[^\n]{0,120}status:\s*"buy"/);
});

test("feedback produces three-state optimization labels and a permanent exact-candle blacklist", () => {
  assert.match(feedbackSource, /optimizationLabel: decision === "confirmed" \? 1 : decision === "denied" \? -1 : 0/);
  assert.match(feedbackSource, /buildOptimizationDataset/);
  assert.match(feedbackSource, /decision-time-features-only/);
  assert.match(feedbackSource, /record\.decision === "pending"/);
  assert.match(js, /永久黑名单/);
  assert.match(js, /\["confirmed", "pending", "denied", "cleared"\]/);
  assert.match(feedbackSource, /buildReviewProfile/);
  assert.match(feedbackSource, /LOCAL_ZERO_POSITIVE_VETO_MIN = 4/);
  assert.match(feedbackSource, /GLOBAL_VETO_PAIR_MIN = 2/);
  assert.match(feedbackSource, /feedbackReviewVeto/);
});

test("confirmed buys expose native-strategy regression conflicts instead of hiding behind restoration", () => {
  assert.match(feedbackSource, /auditConfirmedCompatibility/);
  assert.match(feedbackSource, /confirmed-buy-zero-regression-conflicts/);
  assert.match(feedbackSource, /strategyRegressionConflict/);
  assert.match(js, /loadedConfirmedRegressionConflicts/);
  assert.match(js, /回归冲突/);
  assert.match(css, /has-regression-conflict/);
});

test("release feedback gate keeps confirmed buys included and permanently denied candles disjoint", () => {
  assert.match(feedbackSource, /auditDeniedCompatibility/);
  assert.match(feedbackSource, /denied-buy-zero-revival-conflicts/);
  assert.match(feedbackSource, /confirmed-subset-and-denied-disjoint/);
  assert.match(feedbackSource, /assertReviewCompatibility/);
  assert.match(js, /loadedDeniedRegressionConflicts/);
  assert.match(js, /确认遗漏/);
  assert.match(js, /否定复活/);
});

test("manual certainty can be graded A+, A or B on the chart and in the ledger", () => {
  for (const grade of ["A+", "A", "B"]) {
    assert.match(html, new RegExp(`data-chart-certainty-grade="${grade.replace("+", "\\+")}"`));
  }
  assert.match(js, /data-certainty-grade="A\+"/);
  assert.match(js, /manualCertaintyGrade/);
  assert.match(feedbackSource, /"A\+": 1\.5, A: 1, B: 0\.5/);
  assert.match(css, /\.chart-certainty/);
  assert.match(css, /\.ledger-certainty/);
});

test("TUT hides all displayed signals before 2026-08-05 13:00 China time", () => {
  assert.match(js, /2026-08-05T13:00:00\+08:00/);
  assert.match(js, /normalizedPair === "TUTUSDT" && Number\(item\?\.time\) < TUT_DISPLAY_CUTOFF/);
  assert.match(js, /resultForDisplay\(loaded\.result, pair\)/);
  assert.match(js, /signalDisplayAllowed\(\{ \.\.\.record\.signal, manualDecision: record\.decision \}, pair\)/);
});

test("SPK hides all displayed records before 2025-07-22 07:00 China time", () => {
  assert.match(js, /2025-07-22T07:00:00\+08:00/);
  assert.match(js, /normalizedPair === "SPKUSDT" && Number\(item\?\.time\) < SPK_DISPLAY_CUTOFF/);
  assert.match(js, /const signals = \(result\.signals \|\| \[\]\)\.filter\(\(item\) => signalDisplayAllowed\(item, pair\)\)/);
  assert.match(js, /const pending = \(result\.pending \|\| \[\]\)\.filter\(\(item\) => signalDisplayAllowed\(item, pair\)\)/);
  assert.match(js, /const rejected = \(result\.rejected \|\| \[\]\)\.filter\(\(item\) => signalDisplayAllowed\(item, pair\)\)/);
});

test("trendline display deduplicates one stable outer-envelope structure and suppresses high-level distribution", () => {
  assert.match(js, /intersection \/ smallerSpan >= 0\.45/);
  assert.match(engineSource, /boundaryModel: "outer-envelope"/);
  assert.match(engineSource, /falling-wedge/);
  assert.match(engineSource, /高位大分歧/);
  assert.match(engineSource, /assessEnvelopeCoverage/);
  assert.match(engineSource, /crossingRatio <= 0\.08/);
  assert.match(js, /ctx\.strokeStyle = "rgba\(242, 247, 246, \.9\)"/);
  assert.match(js, /ctx\.lineWidth = 1\.25/);
  assert.match(html, /结构预确认/);
  assert.match(html, /白色实线段/);
  assert.match(html, /白线只代表策略结构预确认，与是否允许在这一根成交分开判断/);
  assert.match(engineSource, /post-impulse-consolidation/);
  assert.match(engineSource, /拉升前 K 线不参与拟合/);
  assert.match(js, /lowerStart < structureStart/);
});

test("high-confidence wedge structures remain visible as pre-confirmations even when execution is vetoed", () => {
  assert.match(engineSource, /structurePreconfirmed: true/);
  assert.match(engineSource, /structures: stableStructures/);
  assert.match(js, /visiblePreconfirmedStructures/);
  assert.match(js, /strategyStructurePrecheck/);
});

test("explains that historical Big Trade is a prior-bar proxy until live trade data is connected", () => {
  assert.match(html, /大单代理/);
  assert.match(html, /逐笔成交确认真实 Big Trade/);
});

test("treats trendlines and previous highs as auxiliary triggers rather than standalone entries", () => {
  assert.match(html, /趋势线只作成熟箱体、收敛三角或下降楔形的低权重辅助/);
  assert.match(engineSource, /FOUNDATION_PATTERNS/);
  assert.match(engineSource, /趋势线与前高仅作辅助/);
  assert.match(js, /eliteStructuralLines/);
  assert.match(engineSource, /anchorMode/);
});

test("disabled W-continuation logic is absent from the strategy UI and engine", () => {
  const engine = fs.readFileSync(path.join(root, "dragon-wave-engine.js"), "utf8");
  assert.doesNotMatch(html, /W中继/);
  assert.doesNotMatch(engine, /W中继|detectWContinuation/);
});

test("uses a single EMA90 trend line", () => {
  assert.match(html, /EMA90/);
  assert.doesNotMatch(html, /EMA20|EMA55/);
  assert.match(js, /indicators\.ema90/);
});

test("one-minute execution is paused in the interactive page but keeps its strict static-review policy", () => {
  assert.match(html, /1分钟暂不参与默认读取和策略计算/);
  assert.doesNotMatch(html, /data-timeframe="1m"/);
  assert.match(js, /filter\(\(interval\) => interval !== "1m"\)/);
  assert.match(engineSource, /function isOneMinuteHorizontalBase/);
  assert.match(engineSource, /1分钟仅保留高确定性横盘起飞或箱体突破，其他结构全部过滤/);
  assert.match(engineSource, /function enforceIntervalStructurePolicy/);
  assert.match(engineSource, /allowDiagonalStructure = structureOptions\.interval !== "1m"/);
  assert.match(engineSource, /structures: \[\]/);
  assert.match(js, /this\.result\.interval === "1m"\s*\? \[\]/);
  assert.match(js, /applyFeedbackPolicy/);
});

test("chart feedback supports persistent multi-select structure confirmation", () => {
  assert.match(html, /结构确认 · 可多选/);
  const tags = [
    "horizontalLaunch", "trendlineBreakout", "triangle", "box", "fallingWedge",
    "pivot", "previousHighBreakout", "consolidationBreakout", "ema90Pullback", "volumeBreakout",
    "nearPreviousHighConsolidation",
    "newCoinNotFalling",
    "mainWaveActive", "mainWaveExpected",
  ];
  tags.forEach((tag) => assert.match(html, new RegExp(`data-chart-structure-tag="${tag}"`)));
  assert.match(html, /回踩90均线/);
  assert.match(html, /放量突破/);
  assert.match(html, /前高附近做盘整/);
  assert.match(js, /nearPreviousHighConsolidation: "前高附近做盘整"/);
  assert.match(js, /newCoinNotFalling: "新币不跌"/);
  assert.match(js, /mainWaveActive: "主升浪阶段"/);
  assert.match(js, /mainWaveExpected: "主升浪预期"/);
  assert.match(html, /新币不跌/);
  assert.match(html, /主升浪阶段/);
  assert.match(html, /主升浪预期/);
  assert.match(js, /normalizeStructureTags/);
  assert.match(feedbackSource, /manual-structure:/);
  assert.match(css, /\.chart-structure-confirm/);
  assert.match(css, /button:last-child:nth-child\(odd\).*grid-column: 1 \/ -1/);
  assert.match(css, /\.ledger-structure-summary/);
});

test("chart feedback separates strategy pre-confirmation from the user's reviewed structure", () => {
  assert.match(html, /策略预确认/);
  assert.match(html, /data-chart-structure-prediction/);
  assert.match(html, /data-chart-structure-review/);
  assert.match(js, /Feedback\.inferStructureTags/);
  assert.match(js, /Feedback\.compareStructureTags/);
  assert.match(css, /button\.is-suggested:not\(\.is-active\)/);
  assert.match(feedbackSource, /strategy-structure:/);
  assert.match(feedbackSource, /review-added:/);
  assert.match(feedbackSource, /review-removed:/);
});

test("provides persistent time-price drawing tools on the main chart", () => {
  for (const tool of ["pan", "feedback", "trend", "horizontal", "ellipse", "erase"]) {
    assert.match(html, new RegExp(`data-draw-tool="${tool}"`));
  }
  assert.match(html, /id="clearDrawings"/);
  assert.match(js, /annotationSets/);
  assert.match(js, /drawAnnotations/);
  assert.match(js, /point\.time/);
  assert.match(js, /point\.price/);
});

test("manual trend lines snap to candle anchors and become reusable structure pre-confirmations", () => {
  assert.match(js, /DRAWING_STORAGE_KEY = "dragon-wave-structure-drawings-v1"/);
  assert.match(js, /snapDrawingPoint\(point\)/);
  assert.match(js, /anchorType/);
  assert.match(js, /classifyManualTrendPair\(first, second\)/);
  assert.match(js, /manual-drawing-learning/);
  assert.match(js, /ascending-triangle/);
  assert.match(js, /converging-triangle/);
  assert.match(js, /falling-wedge/);
  assert.match(js, /postReclaimAboveRatio|coverage/);
  assert.match(js, /manualDrawingStructureFor\(item\)/);
  assert.match(js, /analysis\.tags\.forEach/);
  assert.match(html, /端点自动吸附K线高点、低点、开盘或收盘/);
});

test("chart interaction preloads dense small-timeframe history and uses grab-style panning", () => {
  assert.match(js, /this\.drag\.offset\s*-\s*dragLeftBars/);
  assert.match(js, /"1m":\s*220/);
  assert.match(js, /"5m":\s*190/);
  assert.match(js, /"15m":\s*170/);
  assert.match(js, /Data\.buildCaseWindow/);
  assert.match(js, /renderActiveChart/);
  assert.match(js, /state\.activeInterval/);
  assert.match(js, /chart\.setData\(resultForDisplay\(loaded\.result, pair\), loaded\.venue, focusTime, pair\)/);
});

test("documented leader pairs keep leader permission even after the symbol field enters custom mode", () => {
  assert.match(js, /function isPreselectedLeaderPair\(pair\)/);
  assert.match(js, /Cases\.some\(\(item\) => item\.valid && Data\.normalizePair\(item\.pair\) === normalizedPair\)/);
  assert.match(js, /const preselectedLeader = isPreselectedLeaderPair\(pair\)/);
  assert.match(js, /preselectedLeader,\s*mainWaveStage/);
});

test("refresh restores the active chart first and reuses a bounded persistent analysis cache", () => {
  assert.match(js, /MARKET_CACHE_DB = "dragon-wave-market-cache-v1"/);
  assert.match(js, /window\.indexedDB\.open/);
  assert.match(js, /STRATEGY_CACHE_VERSION = "v89"/);
  assert.match(js, /MARKET_CACHE_LIMIT = 48/);
  assert.match(js, /historical \? 30 \* 24 \* 60 \* 60_000 : 2 \* 60_000/);
  assert.match(js, /function provisionalChartResult/);
  assert.match(js, /signals: \[\],[\s\S]*pending: \[\],[\s\S]*structures: \[\]/);
  const activeFirst = js.indexOf("const activeItem = await loadOne(activeInterval)");
  const remainingLater = js.indexOf("const remainingPromise = Promise.all");
  assert.ok(activeFirst >= 0 && remainingLater > activeFirst);
  assert.match(js, /await nextPaint\(\)/);
  assert.match(js, /主图已恢复 · 后台验证多周期/);
  assert.match(js, /void initializeFeedback\(\);\s*void loadWorkspace\(\);\s*void loadLiveLeaders\(\);/);
});

test("one-minute replay is excluded and historical analysis runs off the interaction thread", () => {
  assert.match(js, /const deferredIntervals = \[\]/);
  assert.match(js, /await commitSettled\(settled, deferredIntervals\.length === 0\)/);
  assert.match(js, /function analyzeTimeframeOffThread/);
  assert.match(js, /new Worker\(ANALYSIS_WORKER_URL\)/);
  assert.match(analysisWorkerSource, /DragonWaveEngine\.analyzeTimeframe/);
});

test("historical cases load locally precomputed results before browser or exchange work", () => {
  assert.match(js, /async function readLocalPrecomputed\(params\)/);
  assert.match(js, /\/api\/dragon-wave-precomputed\?\$\{query\}/);
  assert.match(js, /if \(!params\.historicalDocument \|\| !params\.caseStart \|\| !params\.caseEnd\) return null/);
  assert.match(js, /const precomputed = await readLocalPrecomputed\(params\)/);
  assert.ok(js.indexOf("const precomputed = await readLocalPrecomputed(params)") < js.indexOf("const persistentKey = analyzedCacheKey(params)"));
  assert.match(js, /localPrecomputedHit: true/);
  assert.match(js, /const allLocallyPrecomputed = usable\.length > 0 && usable\.every\(\(item\) => item\.localPrecomputedHit\)/);
  assert.match(js, /allLocallyPrecomputed\s*\? usable\.map\(\(item\) => item\.rawResult\)/);
  assert.match(js, /本机预计算/);
  assert.match(js, /applyFeedbackPolicy\(baseResult, pair\)/);
  assert.match(quietServerSource, /\/api\/dragon-wave-precomputed/);
  assert.match(quietServerSource, /Content-Encoding", "gzip"/);
  assert.match(quietServerSource, /X-Dragon-Wave-Precomputed/);
});

test("the launcher warms historical leaders in a hidden low-priority process", () => {
  assert.match(launcher, /start_dragon_wave_precompute\.ps1/);
  assert.match(launcher, /-Version v89/);
  assert.match(launcher, /dragon-wave\.html\?v=89/);
  assert.match(precomputeLauncher, /PriorityClass = "Idle"/);
  assert.match(precomputeSource, /dragon-wave-precomputed/);
  assert.match(precomputeSource, /zlib\.gzipSync/);
  assert.match(precomputeSource, /Engine\.applyContextGates/);
  assert.match(precomputeSource, /Feedback\.snapshotSignal/);
  assert.match(precomputeSource, /function compactResultForDashboard/);
  assert.match(precomputeSource, /Data\.isCandleCoverageAcceptable/);
  assert.match(precomputeSource, /setTimeout\(resolve, 300\)/);
});

test("chart motion and feedback persistence are coalesced away from pointer and confirmation frames", () => {
  assert.match(js, /if \(this\.renderFrame != null\) return/);
  assert.match(js, /this\.renderFrame = requestAnimationFrame/);
  assert.match(js, /requestIdleCallback\(commit, \{ timeout: 2600 \}\)/);
  assert.match(js, /queueBrowserFeedbackWrite\(1400\)/);
  assert.doesNotMatch(js, /const attached = \[\.\.\.this\.result\.signals/);
});

test("core-first loading protects every long document case instead of special-casing XRP", () => {
  const auditNow = Date.parse("2026-08-17T12:00:00+08:00");
  const longCases = Cases
    .filter((item) => item.valid)
    .filter((item) => Data.buildCaseWindow(item.start, item.end, "1m", auditNow).limit > 20_000);
  assert.ok(longCases.length >= 40, `expected a broad catalog issue, got ${longCases.length}`);
  for (const symbol of ["XRP", "LINK", "SOL", "BNB", "DOGE", "TRB", "ONDO", "ORDI", "币安人生"]) {
    assert.ok(longCases.some((item) => item.symbol === symbol), `${symbol} should use staged loading`);
  }
  const loadWorkspaceSource = js.slice(js.indexOf("async function loadWorkspace()"), js.indexOf("function updateSummary"));
  assert.doesNotMatch(loadWorkspaceSource, /XRP/);
  assert.match(loadWorkspaceSource, /const preselectedLeader = isPreselectedLeaderPair\(pair\)/);
  assert.match(loadWorkspaceSource, /pair === "BTCUSDT" \|\| preselectedLeader[\s\S]*Promise\.resolve\(\[\]\)/);
});

test("BinanceLife uses the native Binance contract instead of BANANAS31", () => {
  assert.match(serverSource, /\{"symbol": "BIANRENSHENG", "name": "币安人生"\}/);
  assert.match(serverSource, /"BIANRENSHENG": "币安人生USDT"/);
  assert.match(serverSource, /def binance_price_watch_pair/);
  assert.doesNotMatch(serverSource, /\{"symbol": "BANANAS31", "name": "币安人生"\}/);
});

test("historical document candles persist independently of strategy versions while live leaders stay fresh", () => {
  assert.match(js, /MARKET_CANDLE_CACHE_STORE = "market-candles"/);
  assert.match(js, /MARKET_CACHE_SCHEMA = 2/);
  assert.match(js, /MARKET_CANDLE_CACHE_LIMIT = 640/);
  assert.match(js, /params\.historicalDocument/);
  assert.match(js, /cached = params\.historicalDocument[\s\S]*readAnalyzedCache/);
  assert.match(js, /readMarketCandleCache\(key\)/);
  assert.match(js, /writeMarketCandleCache\(key, value\)/);
  assert.match(js, /function hasCompleteCachedCandles\(payload, params\)/);
  assert.match(js, /Data\.isCandleCoverageAcceptable\(payload\.candles, params\.window, params\.interval\)/);
  assert.match(js, /cached\.result\.candles, params\.window, params\.interval/);
  assert.match(js, /historicalDocument: Boolean\(state\.activeCase\?\.valid && !state\.activeCase\.live\)/);
  assert.match(js, /实时龙头和自定义实时观察明确绕过持久行情缓存/);
});

test("local historical loader proxies all primary fallback exchanges", () => {
  assert.match(quietServerSource, /BYBIT_INTERVALS/);
  assert.match(quietServerSource, /api\/dragon-wave-candles\/bybit/);
  assert.match(quietServerSource, /api\.bybit\.com\/v5\/market\/kline/);
  assert.match(quietServerSource, /api\/dragon-wave-candles\/okx/);
  assert.match(quietServerSource, /api\/dragon-wave-candles\/bitget/);
  assert.match(quietServerSource, /api\/dragon-wave-candles\/gate/);
  assert.match(quietServerSource, /api\/dragon-wave-candles\/mexc/);
  assert.match(quietServerSource, /api\.mexc\.com\/api\/v3\/klines/);
  assert.match(dataSource, /api\/dragon-wave-candles\/okx/);
  assert.match(dataSource, /api\/dragon-wave-candles\/bitget/);
  assert.match(dataSource, /api\/dragon-wave-candles\/gate/);
  assert.match(dataSource, /api\/dragon-wave-candles\/mexc/);
});

test("launcher uses a quiet detached server so repeated requests cannot block the page", () => {
  assert.match(launcher, /pythonw\.exe/i);
  assert.match(launcher, /quiet_http_server\.py/i);
  assert.match(launcher, /Start-Sleep -Milliseconds 1200/);
  assert.match(launcher, /dragon-wave\.html\?v=89/);
  assert.ok(fs.existsSync(path.join(root, "quiet_http_server.py")));
});
