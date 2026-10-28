// 做市商监控子板块的前端接线（price-watch.js / price-watch.html / styles.css）。
//
// 覆盖的是「模板里到底读了哪些字段」——服务端 payload 改了名字而前端没跟上时，
// Python 侧的单测全绿，页面上却是一片「--」。所以这里按渲染函数切一段代码出来看
// 它引用的键名，而不是只断言「函数存在」。
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

const renderBlock = slice("function renderMarketMaker()", "async function getMarketMakerPayload");

test("监控页有做市商监控的 tab，并且模式名进了白名单", () => {
  assert.match(html, /data-watch-mode="marketmaker"[^>]*>做市商监控</);
  // 白名单只断言「做市商这几个按顺序在」——后面还会继续加子板块（价格异动就是），
  // 写死数组结尾的话每加一个 tab 都要来改一次测试。
  assert.match(js, /"sandwich", "devlaunch", "marketmaker"/);
});

test("?mode=marketmaker 直达时三处都必须分流", () => {
  // load()：不带分流就会去拉价格监控列表。
  assert.match(js, /if \(currentMode === "marketmaker"\) \{\s*\/\/[^\n]*\n\s*await loadMarketMaker\(\{ refresh, quiet \}\);\s*return;\s*\}/);
  // render()：空监控池会把子板块顶掉。
  assert.match(js, /if \(currentMode === "marketmaker"\) \{\s*renderMarketMaker\(\);\s*return;\s*\}/);
  // 30 秒轮询：服务端 5 分钟一轮，页面拿快照即可。
  assert.match(js, /if \(currentMode === "marketmaker"\) \{\s*\/\/[^\n]*\n\s*loadMarketMaker\(\{ quiet: true \}\);\s*return;\s*\}/);
  // 标题与「加币」表单。
  assert.match(js, /headingTitle\.textContent = "做市商监控"/);
  assert.match(js, /form\.hidden = wechat \|\| personalx \|\| smartmoney \|\| chains \|\| sandwich \|\| devlaunch \|\| marketmaker/);
});

test("渲染层读的是服务端真实下发的那几个键", () => {
  for (const key of [
    "controlTargets", "controlExcluded", "controlSummary", "crossToken",
    "largeTransfers", "transfers", "positions", "firms", "addresses",
  ]) {
    assert.ok(renderBlock.includes(`marketMakerPayload.${key}`), `渲染没有读 ${key}`);
  }
  // 摘要里的计数与服务端 summary 同名（改一边不改另一边就会显示 --）。
  assert.match(renderBlock, /control\.buyZone/);
  assert.match(renderBlock, /summary\.lastCycleAt/);
  assert.match(renderBlock, /summary\.largeTransferUsd/);
  // 控制台第一格靠 top-level 的 addressesPerCycle（summary 里也补了，做双保险）。
  assert.match(renderBlock, /addressesPerCycle/);
});

test("控盘标的卡展示五条判据对应的字段，而不是只有名字", () => {
  const controlBlock = slice("function mmControlTemplate", "function mmTransferTemplate");
  for (const field of [
    "controlText", "marketCapText", "drawdownPct", "changeText",
    "priceText", "supportText", "supportLabel", "distancePct", "origin",
  ]) {
    assert.ok(controlBlock.includes(field), `控盘卡没有展示 ${field}`);
  }
  // 状态标签走映射表（buy/wait/pending/weak/broken 五档）。
  assert.match(js, /const MM_CONTROL_META = \{[\s\S]*?pending:[\s\S]*?broken:/);
  assert.match(controlBlock, /MM_CONTROL_META\[row\.status\]/);
});

test("「查看」走 GMGN 交易页，没有链接时退回 Alpha 页面", () => {
  const controlBlock = slice("function mmControlTemplate", "function mmTransferTemplate");
  assert.match(controlBlock, /safeExternalUrl\(row\.gmgnUrl\) \|\| safeExternalUrl\(row\.url\)/);
  assert.match(controlBlock, /GMGN 交易/);
});

test("地址簿按厂家折叠，且有暂停 / 删除两个动作", () => {
  const addressBlock = slice("function mmAddressLine", "function renderMarketMaker");
  assert.match(addressBlock, /data-mm-toggle=/);
  assert.match(addressBlock, /data-mm-remove=/);
  assert.match(addressBlock, /evidenceLabel/);
  // 只看一家时自动展开（options.open），全量视角仍默认折叠。
  assert.match(js, /<details class="mm-firm"\$\{options\.open \? " open" : ""\}>/);
});

test("按做市商分开看：下拉框 + 药丸都只改视图，不重新拉数据", () => {
  // 状态落在 ?mmFirm= 与 localStorage 两处，刷新 / 从别的 tab 回来都还在同一家。
  assert.match(js, /new URLSearchParams\(window\.location\.search\)\.get\("mmFirm"\)/);
  assert.match(js, /localStorage\.getItem\("xingyun\.mmFirm"\)/);
  assert.match(js, /function syncMarketMakerFirm\(\)/);
  assert.match(js, /url\.searchParams\.set\("mmFirm", marketMakerFirm\)/);
  assert.match(js, /url\.searchParams\.delete\("mmFirm"\)/);
  assert.match(js, /localStorage\.setItem\("xingyun\.mmFirm", marketMakerFirm\)/);

  // 渲染层：自绘下拉（全部 + 8 家）与药丸都可选，选中打 is-selected。
  // 原生 <select> 的弹层是系统画的、深色下一片纯黑（用户 2026-10-21 嫌太黑），所以换成自绘。
  assert.match(renderBlock, /mmFirmPicker\(firms, selectedFirm\)/);
  assert.match(js, /<div class="mm-firm-picker">/);
  assert.match(js, /data-mm-firm-toggle/);
  assert.doesNotMatch(renderBlock, /<select data-mm-firm>/, "原生 select 已经换掉了");
  assert.match(js, /data-mm-option="\$\{escapeHtml\(id\)\}"/);
  assert.match(js, /回到全量视角/);
  assert.match(renderBlock, /data-mm-pick="\$\{escapeHtml\(firm\.id\)\}"/);
  assert.match(renderBlock, /mm-firm-chip\$\{firm\.id === marketMakerFirm \? " is-selected" : ""\}/);

  // 四类列表全部跟着收窄：持仓 / 共识 / 大额 / 流水。
  assert.match(renderBlock, /const byFirm = \(row\) => !marketMakerFirm \|\| row\.firm === marketMakerFirm;/);
  assert.match(renderBlock, /allPositions\.filter\(byFirm\)/);
  assert.match(renderBlock, /\.filter\(\(row\) => !marketMakerFirm \|\| \(row\.firms \|\| \[\]\)\.includes\(marketMakerFirm\)\)/);
  assert.match(renderBlock, /\.filter\(byFirm\);/, "大额 / 流水也要走 byFirm");
  // 地址簿跟着筛选走，只排所选那一家。
  assert.match(renderBlock, /const firmsForBook = selectedFirm \? \[selectedFirm\] : firms;/);

  // 事件处理：下拉开关、选项切换、药丸再点一次取消，三条都得在。
  assert.match(js, /const mmFirmToggle = event\.target\.closest\("\[data-mm-firm-toggle\]"\)/);
  assert.match(js, /const mmFirmOption = event\.target\.closest\("\[data-mm-option\]"\)/);
  assert.match(js, /const mmFirmPick = event\.target\.closest\("\[data-mm-pick\]"\)/);
  assert.match(js, /marketMakerFirm = picked === marketMakerFirm \? "" : picked;/);
  // 点空白处要收菜单，且不能 return（后面的点击还得照常处理）。
  assert.match(js, /if \(marketMakerFirmMenuOpen && !event\.target\.closest\("\.mm-firm-picker"\)\)/);

  // 控盘那一节按标的不按厂家，必须仍然全量，且有文案说明。
  assert.match(renderBlock, /与所选做市商无关/);
});

test("筛选相关样式齐全（选中态 / 下拉面板 / 焦点卡）", () => {
  assert.match(css, /\.mm-console \.mm-firm-chip\.is-selected \{/);
  assert.match(css, /\.mm-console \.mm-firm-picker-toggle \{/);
  assert.match(css, /\.mm-console \.mm-firm-picker-menu \{/);
  assert.match(css, /\.mm-console \.mm-firm-picker-menu\.is-open \{/);
  assert.match(css, /\.mm-console \.mm-firm-option\.is-selected \{/);
  assert.match(css, /\.mm-console \.mm-firm-focus \{/);
  // 药丸是 <button>，不清掉默认样式会变成灰色系统按钮。
  assert.match(css, /\.mm-console \.mm-firm-chip \{[\s\S]*?text-align: left;[\s\S]*?cursor: pointer;/);
  // 面板底色必须比页面亮一档，且**不能**用纯黑（那正是用户嫌的那个颜色）。
  const menuRule = css.slice(css.indexOf(".mm-console .mm-firm-picker-menu {"));
  assert.doesNotMatch(menuRule.slice(0, 500), /background:\s*#000|background:\s*black/);
});

test("四个动作的请求落在 /api/market-maker 上", () => {
  assert.match(js, /fetch\("\/api\/market-maker", \{[\s\S]*?body: JSON\.stringify\(\{ action, \.\.\.extra \}\)/);
  assert.match(js, /fetch\(`\/api\/market-maker\$\{refresh \? "\?refresh=1" : ""\}`/);
  for (const action of ["settings", "refresh", "toggle", "remove"]) {
    assert.ok(js.includes(`postMarketMakerAction("${action}"`), `缺少 ${action} 动作`);
  }
  // 按钮一律挂 data-mm-*，事件委托按这个约定找。
  for (const hook of ["data-mm-alerts", "data-mm-refresh", "data-mm-toggle", "data-mm-remove"]) {
    assert.ok(js.includes(hook), `缺少 ${hook} 钩子`);
  }
});

test("页面把三方共振的口径讲清楚（大额 × 涨幅榜 × AIcoin，6 小时一次）", () => {
  assert.match(renderBlock, /marketMakerPayload\.resonance/);
  assert.match(renderBlock, /三方共振/);
  assert.match(renderBlock, /resonance\.cooldownSeconds/);
  assert.match(renderBlock, /resonance\.blocked/);
  // 没接线 / 读不到榜单时必须说实话，不能装作「没有共振」（旧后端连这个键都没有）。
  assert.match(renderBlock, /resonance\.armed !== true/);
  assert.match(renderBlock, /resonance\.note/);
  // 两段说明各自独立成行，不能挤在一个 <p> 里。
  assert.equal((renderBlock.match(/class="smart-money-readonly mm-gate"/g) || []).length, 2);
});

test("样式只在本板块作用域内生效", () => {
  assert.match(css, /\.price-watch-grid\.is-market-maker \{/);
  assert.match(css, /\.mm-console \.mm-control\.is-buy > header i \{/);
  // 77 条地址默认全展开会把页面拉得很长 —— 必须折叠。
  assert.match(css, /\.mm-console \.mm-firm > summary \{/);
});
