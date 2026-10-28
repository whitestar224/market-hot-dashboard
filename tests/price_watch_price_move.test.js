// 价格异动子板块的前端接线（price-watch.js / price-watch.html / styles.css）。
//
// 重点不是「函数存在」，而是**模板读了哪些字段**：服务端 payload 改名 / 少给一个键，
// Python 侧单测照样全绿，页面上却是一片「--」或者永远「正在读取」。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");

const js = fs.readFileSync("price-watch.js", "utf8");
const html = fs.readFileSync("price-watch.html", "utf8");
const css = fs.readFileSync("styles.css", "utf8");

function slice(startMarker, endMarker) {
  const start = js.indexOf(startMarker);
  assert.ok(start >= 0, `找不到起点：${startMarker}`);
  const end = js.indexOf(endMarker, start + startMarker.length);
  assert.ok(end > start, `找不到终点：${endMarker}`);
  return js.slice(start, end);
}

const renderBlock = slice("function renderPriceMove()", "async function getPriceMovePayload");
const itemBlock = slice("function priceMoveItemTemplate", "function renderPriceMove()");

test("监控页有价格异动的 tab，模式名进了白名单", () => {
  assert.match(html, /data-watch-mode="pricemove"[^>]*>价格异动</);
  assert.match(js, /"marketmaker", "pricemove"/);
});

test("?mode=pricemove 直达时四处都必须分流", () => {
  // load()：不分流就会去拉价格监控列表。
  assert.match(js, /if \(currentMode === "pricemove"\) \{\s*await loadPriceMove\(\{ refresh, quiet \}\);\s*return;\s*\}/);
  // render()：空监控池会把子板块顶掉。
  assert.match(js, /if \(currentMode === "pricemove"\) \{\s*renderPriceMove\(\);\s*return;\s*\}/);
  // 30 秒轮询：采样 5 分钟一轮，页面取快照即可。
  assert.match(js, /if \(currentMode === "pricemove"\) \{\s*\/\/[^\n]*\n\s*loadPriceMove\(\{ quiet: true \}\);\s*return;\s*\}/);
  // 标题与「加币」表单。
  assert.match(js, /headingTitle\.textContent = "价格异动"/);
  assert.match(js, /form\.hidden = [^;]*\|\| pricemove\b[^;]*;/);
});

test("渲染层读的是服务端真实下发的那几个键", () => {
  for (const key of ["thresholdPct", "windowSeconds", "cooldownSeconds", "note"]) {
    assert.ok(renderBlock.includes(`priceMovePayload.${key}`), `渲染没有读 ${key}`);
  }
  for (const key of ["frames", "symbols", "current", "total", "running", "intervalSeconds", "lastCycleAt", "lastError", "alertsEnabled"]) {
    assert.ok(renderBlock.includes(`summary.${key}`), `摘要没有读 summary.${key}`);
  }
  assert.match(renderBlock, /Array\.isArray\(priceMovePayload\.moves\)/);
});

test("窗口没满时要说明已覆盖多久，而不是说要等窗口走满才提醒", () => {
  assert.match(renderBlock, /const warming = Boolean\(priceMovePayload\.note\)/);
  assert.match(renderBlock, /const coverageMinutes = [^;]*coverageSeconds/);
  assert.match(renderBlock, /price-move-warming/);
  assert.match(renderBlock, /窗口未满/);
  assert.match(renderBlock, /已经可以报/);
  assert.match(renderBlock, /还在攒第一帧/);
  // 窗口没满的空态不能复用「当前没有涨幅超过…」那句话。
  assert.match(renderBlock, /warming \? "还在攒第一帧" : `当前没有涨幅超过/);
  // 用户 2026-10-21 二次拍板：触线即报 —— 页面不许再写「凑满 X 小时之前不会产生任何提醒」。
  assert.ok(!/不会产生任何提醒/.test(renderBlock), "旧口径「等窗口走满才报」的文案还留着");
  assert.ok(!/正在预热/.test(renderBlock), "「预热中」的说法要换掉：现在窗口没满也会报");
});

test("卡片读的是后端算好的字段，前端不自己重算涨幅", () => {
  for (const field of ["symbol", "pctText", "pct", "price", "basePrice", "windowText", "chainLabel", "source", "url", "contract", "at"]) {
    assert.ok(itemBlock.includes(`row.${field}`), `卡片没有读 row.${field}`);
  }
  // 链接一律过 safeExternalUrl：GMGN 与币安两种地址都可能为空。
  assert.match(itemBlock, /safeExternalUrl\(row\.url\)/);
  // 没有 CA 的合约币去 GMGN 是死链，按钮文案要跟着换。
  assert.match(itemBlock, /row\.contract \? "GMGN 交易" : "币安合约"/);
});

test("控盘爆红与 Alpha 角标只读后端字段，前端不自己判", () => {
  // 判据在服务端（alpha_mm 名单）：前端只认 alphaHot / alphaPhase / alphaControlPct。
  assert.match(itemBlock, /const hot = Boolean\(row\.alphaHot\)/);
  assert.match(itemBlock, /row\.alphaPhase/);
  assert.match(itemBlock, /row\.alphaControlPct/);
  assert.match(itemBlock, /row\.alphaMarketCapText/);
  assert.match(itemBlock, /is-alpha-hot/);
  assert.match(itemBlock, /price-move-alpha/);
  assert.match(itemBlock, /项目方控盘/);
  // 前端不许自己算「≥90%」——那会让爆红标准出现第二份实现。
  assert.ok(!/[<>]=?\s*90\b|\b90\s*[<>]/.test(itemBlock), "前端不应该拿 90% 做比较");
});

test("口径里写明什么时候会爆红", () => {
  assert.match(renderBlock, /控盘 ≥90%/);
  assert.match(renderBlock, /流通市值 ≤3000 万/);
  assert.match(renderBlock, /爆红/);
});

test("两个按钮各自带 loading 闸与 true 分支防重复点击", () => {
  assert.match(js, /data-pm-alerts="\$\{summary\.alertsEnabled \? "1" : "0"\}"/);
  assert.match(js, /data-pm-refresh \$\{busy \? "disabled" : ""\}/);
  assert.match(js, /if \(pmAlerts && currentMode === "pricemove" && !priceMoveActionLoading\)/);
  assert.match(js, /if \(pmRefresh && currentMode === "pricemove" && !priceMoveActionLoading\)/);
  assert.match(js, /postPriceMoveAction\("settings", \{ alertsEnabled: enabled \}\)/);
  assert.match(js, /postPriceMoveAction\("refresh"\)/);
});

test("取数与提交都打到 /api/price-move，并且不吃浏览器缓存", () => {
  assert.match(js, /fetch\(`\/api\/price-move\$\{refresh \? "\?refresh=1" : ""\}`/);
  assert.match(js, /fetch\("\/api\/price-move", \{/);
  assert.match(js, /method: "POST"/);
  assert.ok(js.split("/api/price-move").length - 1 >= 2);
  // 页面侧 10 秒去重：60 秒轮询叠上 tab 切换也不该连打服务端。
  assert.match(js, /const cacheFresh = priceMoveLoaded && Date\.now\(\) - lastPriceMoveLoadAt < 10_000/);
});

test("空态与失败态都有话说，不会留一片白", () => {
  assert.match(renderBlock, /当前没有涨幅超过 \$\{threshold\}% 的标的/);
  assert.match(js, /价格异动暂不可用/);
  assert.match(js, /grid\.classList\.add\("is-price-move"\)/);
});

test("样式作用域挂在 is-price-move 上，卡片用项目现有的涨色", () => {
  assert.match(css, /\.price-watch-grid\.is-price-move \{/);
  assert.match(css, /\.price-move-item \{[\s\S]*?border-left: 3px solid var\(--up\)/);
  assert.match(css, /\.price-move-item > header i \{[\s\S]*?color: var\(--up\)/);
  assert.match(css, /\.price-move-bar span \{[\s\S]*?background: linear-gradient\(90deg, rgba\(35, 201, 154, 0\.35\), var\(--up\)\)/);
  assert.match(css, /\.price-move-facts \{[\s\S]*?grid-template-columns: repeat\(4, minmax\(0, 1fr\)\)/);
  // 预热提示要显眼（琥珀边），别和普通说明文字混在一起。
  assert.match(css, /\.price-move-console \.price-move-warming \{[\s\S]*?border-left: 3px solid var\(--amber\)/);
  // 窄屏回到两列，四格在手机上会挤成一团。
  assert.match(css, /@media \(max-width: 720px\) \{[\s\S]*?\.price-move-facts \{[\s\S]*?repeat\(2, minmax\(0, 1fr\)\)/);
});

test("控盘爆红用的是红边红底，Alpha 角标在爆红时转实心", () => {
  assert.match(css, /\.price-move-item\.is-alpha-hot \{[\s\S]*?border-left-color: var\(--down\)/);
  assert.match(css, /\.price-move-item\.is-alpha-hot > header i \{[\s\S]*?color: var\(--down\)/);
  assert.match(css, /\.price-move-alpha \{/);
  assert.match(css, /\.price-move-alpha\.is-hot \{[\s\S]*?background: var\(--down\)/);
  assert.match(css, /\.price-move-console \.price-move-hot b \{[\s\S]*?color: var\(--down\)/);
});

test("涨幅条的宽度夹在 4~100 之间（0% 也要看得见，200% 不能撑破）", () => {
  assert.match(itemBlock, /Math\.max\(4, Math\.min\(100, Math\.abs\(Number\(row\.pct \|\| 0\)\)\)\)/);
});

test("页面引的静态资源都带了版本号（改完必须 bump，否则吃旧缓存）", () => {
  assert.match(html, /styles\.css\?v=\d+/);
  assert.match(html, /price-watch\.js\?v=\d+/);
});
