// 地址集群子板块的前端接线（price-watch.js / price-watch.html / styles.css）。
//
// 重点同价格异动那份：**模板读了哪些字段**才是真正的脆点。服务端 payload 改名 /
// 少给一个键，Python 侧单测照样全绿，页面上却是一片「--」或者永远「正在读取」。
//
// 另外一条硬约束：**阈值不许在前端重算**。跨标的 / 5% / 6 小时冷却全是服务端的判定，
// 前端要是自己拿 5 去比较，两边口径一改立刻分叉。
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

const renderBlock = slice("function renderArkhamClusters()", "async function getArkhamClusterPayload");
const itemBlock = slice("function arkhamClusterItemTemplate", "function renderArkhamClusters()");
const tokenBlock = slice("function arkhamClusterTokenRow", "function renderArkhamClusters()");
const chipBlock = slice("function arkhamClusterTokenChip", "function arkhamClusterItemTemplate");
const loadBlock = slice("async function loadArkhamClusters", "// Dev 发币");

test("监控页有地址集群的 tab，模式名进了白名单", () => {
  assert.match(html, /data-watch-mode="arkhamclusters"[^>]*>地址集群</);
  assert.match(js, /"pricemove", "arkhamclusters"\]/);
});

test("?mode=arkhamclusters 直达时四处都必须分流", () => {
  // load()：不分流就会去拉价格监控列表。
  assert.match(js, /if \(currentMode === "arkhamclusters"\) \{\s*await loadArkhamClusters\(\{ refresh, quiet \}\);\s*return;\s*\}/);
  // render()：空监控池会把子板块顶掉。
  assert.match(js, /if \(currentMode === "arkhamclusters"\) \{\s*renderArkhamClusters\(\);\s*return;\s*\}/);
  // 轮询。
  assert.match(js, /if \(currentMode === "arkhamclusters"\) \{[^}]*loadArkhamClusters\(\{ quiet: true \}\);/);
  // 搜索表单：地址集群不按监控池过滤，必须藏起来。
  assert.match(js, /const arkhamclusters = currentMode === "arkhamclusters";/);
  assert.match(js, /form\.hidden = [^;]*\|\| arkhamclusters;/);
});

test("标题段有独立文案，且说明口径与提醒条件", () => {
  assert.match(renderBlock + js.slice(0, js.indexOf("function renderArkhamClusters"))[0] || "", /ARKHAM/);
  const heading = slice("if (arkhamclusters) {", "headingLabel.textContent = oversold");
  assert.match(heading, /地址集群/);
  assert.match(heading, /实体归因/);
  assert.match(heading, /≥2 个标的/);
});

test("集群卡片读的是服务端给的字段", () => {
  for (const key of ["cluster.name", "cluster.kind", "cluster.clusterType", "cluster.tokenCount", "cluster.tokens", "cluster.peakPct", "cluster.firstSeenAt", "cluster.lastSeenAt"]) {
    assert.ok(itemBlock.includes(key), `卡片模板没读 ${key}（服务端改名就会渲染成空）`);
  }
  // 匿名地址簇要把地址显示出来 —— 不然「谁」完全看不出来。
  assert.match(itemBlock, /cluster\.kind === "address" && cluster\.address/);
});

test("标的 chip 读 token.pct / token.url，不自己算占比", () => {
  assert.ok(chipBlock.includes("token.pct"));
  assert.ok(chipBlock.includes("token.url"));
  assert.match(chipBlock, /safeExternalUrl\(token\.url\)/);
  // 没有合法链接就渲染成不可点的 span，不能输出 href="" 的假链接。
  assert.match(chipBlock, /<span class="arkham-token-chip">/);
});

test("已查询标的行读的是服务端快照字段", () => {
  for (const key of ["row.last_error", "row.top_pct", "row.cluster_count", "row.sources", "row.chainLabel"]) {
    assert.ok(tokenBlock.includes(key), `标的行没读 ${key}`);
  }
  // 取不到的标的不许隐藏 —— 隐藏会让人以为它没被监控过。
  assert.match(tokenBlock, /is-error/);
});

test("链名只认服务端给的 chainLabel，不回落裸链号", () => {
  // 踩过：候选链值有三套写法（solana / CT_501 / 56），后端标签缺一个就会让
  // `row.chainLabel || row.chain` 把内部链号原样印在卡片上。
  assert.doesNotMatch(tokenBlock, /chainLabel\s*\|\|\s*row\.chain/);
  assert.match(tokenBlock, /escapeHtml\(row\.chainLabel \|\| ""\)/);
});

test("静态资源版本号已 bump（不吃浏览器旧缓存）", () => {
  assert.match(html, /styles\.css\?v=136/);
  assert.match(html, /price-watch\.js\?v=130/);
});

test("阈值只从 payload 取，前端不做 5% 的比较", () => {
  assert.match(renderBlock, /summary\.significantPct/);
  assert.match(renderBlock, /summary\.crossMinTokens/);
  assert.doesNotMatch(renderBlock, /[<>]=?\s*5\b|\b5\s*[<>]/);
  assert.doesNotMatch(loadBlock, /[<>]=?\s*5\b/);
});

test("Arkham 签名被拒时要明说不可用，而不是显示没有集群", () => {
  assert.match(renderBlock, /state\.authBroken/);
  assert.match(renderBlock, /arkham-cluster-broken/);
  assert.match(renderBlock, /不会用空集群冒充/);
});

test("开关与刷新按钮走 POST，不直接改本地状态", () => {
  assert.match(renderBlock, /data-ac-alerts="\$\{state\.alertsEnabled \? "1" : "0"\}"/);
  assert.match(renderBlock, /data-ac-refresh/);
  assert.match(js, /postArkhamClusterAction\("settings", \{ alertsEnabled: enabled \}\)/);
  assert.match(js, /postArkhamClusterAction\("refresh"\)/);
  // 事件监听必须限定在 arkhamclusters 模式下，否则别的 tab 里同名属性会串。
  assert.match(js, /acAlerts && currentMode === "arkhamclusters"/);
  assert.match(js, /acRefresh && currentMode === "arkhamclusters"/);
});

test("请求的是 /api/arkham-clusters", () => {
  // 读和写都必须打这个路由；refresh=1 才带查询串。
  assert.ok(js.includes("/api/arkham-clusters${refresh"), "GET 没带 refresh 开关");
  assert.ok(js.includes('fetch("/api/arkham-clusters"'), "POST 没打这个路由");
  assert.match(loadBlock, /getArkhamClusterPayload\(refresh\)/);
});

test("样式覆盖摘要五格、卡片、chip 与错误态", () => {
  assert.match(css, /\.price-watch-grid\.is-arkham-clusters/);
  assert.match(css, /\.arkham-cluster-console \.smart-money-summary \{\s*grid-template-columns: repeat\(5/);
  assert.match(css, /\.arkham-cluster-item\.is-entity/);
  assert.match(css, /\.arkham-cluster-item\.is-address/);
  assert.match(css, /\.arkham-token-chip/);
  assert.match(css, /\.arkham-token-item\.is-error/);
  assert.match(css, /@media \(max-width: 720px\)/);
});
