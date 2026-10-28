// 监控页「Dev 发币」子板块的前端接线回归。
//
// 后端判据在 tests/test_dev_launch_monitor.py 里钉；这里钉的是「页面真的把它接上了」：
// 标签页、supportedModes 注册、?mode=devlaunch 直达分流、四档级别徽章、添加地址时的
// 前端拦截（非法地址 / 索引器不可用的链）、以及轮询只在看这个子板块时才跑。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");

test("monitor page exposes the dev-launch sub-board", () => {
  const html = fs.readFileSync("price-watch.html", "utf8");
  const js = fs.readFileSync("price-watch.js", "utf8");
  const css = fs.readFileSync("styles.css", "utf8");

  assert.match(html, /data-watch-mode="devlaunch"/);
  // 必须注册进 supportedModes，否则 URL ?mode=devlaunch 会被顶回 prior。
  assert.match(js, /const supportedModes = \[[^\]]*"devlaunch"/);
  assert.match(js, /\/api\/dev-launch/);
  assert.match(js, /renderDevLaunch/);
  assert.match(js, /loadDevLaunch/);
  assert.match(js, /getDevLaunchPayload/);
  assert.match(js, /postDevLaunchAction/);
  assert.match(js, /data-dev-launch-wallet-form/);
  assert.match(js, /data-dev-launch-toggle/);
  assert.match(js, /data-dev-launch-remove/);
  assert.match(js, /data-dev-launch-rescan/);
  assert.match(js, /data-dev-launch-alerts/);
  assert.match(js, /data-dev-launch-refresh/);
  assert.match(css, /\.price-watch-grid\.is-dev-launch/);
});

test("the four dev tiers match the agreed thresholds", () => {
  const js = fs.readFileSync("price-watch.js", "utf8");
  for (const tier of ["legendary", "premium", "quality", "flow"]) {
    assert.match(js, new RegExp(`\\b${tier}: \\{ label:`), tier);
  }
  // 四档的名字必须与用户拍板的一致，且口径（历史峰值市值）要写在页面上。
  for (const label of ["顶级", "精品", "优质", "普通 / 流水"]) {
    assert.ok(js.includes(label), `缺少级别标签 ${label}`);
  }
  assert.match(js, /历史发过的币里最高的一次市值/);
  assert.match(js, /1 亿美元/);
});

test("the tier legend spells out the thresholds in the same wording the user agreed", () => {
  const js = fs.readFileSync("price-watch.js", "utf8");
  // 中文刻度（1 亿 / 1000 万 / 100 万）而不是 compactUsd 的 $100.00M —— 后者是给市值用的，
  // 当阈值标签读起来很别扭。这四句同时也是后端 DEV_TIER_STEPS 阈值的前端镜像。
  for (const range of ["≥ $1 亿", "$1000 万 ~ $1 亿", "$100 万 ~ $1000 万", "低于 $100 万"]) {
    assert.ok(js.includes(range), `级别图例缺少刻度文案 ${range}`);
  }
});

test("each tier badge has its own colour so levels are readable at a glance", () => {
  const css = fs.readFileSync("styles.css", "utf8");
  for (const tier of ["legendary", "premium", "quality", "flow"]) {
    assert.match(css, new RegExp(`\\.dev-launch-console \\.dev-tier\\.is-${tier} \\{`), tier);
  }
  // 徽章必须挂在 .dev-launch-console 下面：否则会被 .sandwich-result > header > i
  // （被夹结论标签，font-size 8px / 圆角 999px）按选择器权重压过去。
  assert.ok(
    /\.dev-launch-console \.dev-tier \{/.test(css),
    "基础徽章样式也必须在 .dev-launch-console 作用域内",
  );
});

test("the poller only refreshes the sub-board while it is on screen", () => {
  const js = fs.readFileSync("price-watch.js", "utf8");
  // 轮询分支里允许夹注释（\s* 不跨行吞注释），所以显式放行注释行。
  assert.match(
    js,
    /if \(currentMode === "devlaunch"\) \{\s*(?:\/\/[^\n]*\n\s*)*loadDevLaunch\(\{ quiet: true \}\);\s*return;\s*\}/,
  );
  assert.match(js, /grid\.classList\.add\("is-dev-launch"\)/);
});

test("?mode=devlaunch direct open does not fall through to the price list", () => {
  const js = fs.readFileSync("price-watch.js", "utf8");
  // load() 里必须显式分流：直接带 ?mode=devlaunch 打开（或在子板块里刷新页面）时
  // 不会走模式按钮的点击分支，漏了这处就会去拉价格监控列表。
  assert.match(js, /if \(currentMode === "devlaunch"\) \{\s*\/\/[^\n]*\n\s*await loadDevLaunch\(\{ refresh, quiet \}\);\s*return;\s*\}/);
  // render() 里也要分流，否则空监控池会把子板块顶掉。
  assert.match(js, /if \(currentMode === "devlaunch"\) \{\s*renderDevLaunch\(\);\s*return;\s*\}/);
  // 模式切换时的标题与文案。
  assert.match(js, /headingTitle\.textContent = "Dev 发币监控"/);
  // 「加地址」表单在这个子板块里没有意义，必须藏掉。
  // 注意：后面每加一个不出表单的子板块（做市商监控即 marketmaker）都要追加到这里，
  // 断言写成「至少隐藏到 devlaunch」而不是整行等值，免得下次又被一条无关改动打红。
  assert.match(js, /form\.hidden = wechat \|\| personalx \|\| smartmoney \|\| chains \|\| sandwich \|\| devlaunch(?:\s*\|\|[^;]+)?;/);
});

test("adding a dev address is validated before it reaches the server", () => {
  const js = fs.readFileSync("price-watch.js", "utf8");
  assert.match(js, /Dev 发币监控只支持 EVM 地址/);
  // 该链索引器不可用时当场拦住：加进去了也扫不出东西，用户会一直白等提醒。
  assert.match(js, /chainMeta\.available === false/);
  assert.match(js, /暂时查不了/);
});

test("dev launch records link out to gmgn with the same session as the hot board", () => {
  const js = fs.readFileSync("price-watch.js", "utf8");
  // 「查看」口径与 GMGN 热搜榜弹窗一致：跳 GMGN 站内交易页（浏览器已登录就带登录态）。
  assert.match(js, /GMGN 交易/);
  assert.match(js, /GMGN 交易<\/a>/);
  assert.match(js, /发币交易/);
});

test("dev launch module is shipped with the desktop package", () => {
  const pkg = JSON.parse(fs.readFileSync("package.json", "utf8"));
  const resource = pkg.build.extraResources.find((item) => item.to === "dashboard");
  assert.ok(resource.filter.includes("dev_launch_monitor.py"));
});
