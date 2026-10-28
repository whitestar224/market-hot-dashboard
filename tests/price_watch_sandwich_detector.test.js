const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");

test("monitor page exposes the sandwich-detection sub-board", () => {
  const html = fs.readFileSync("price-watch.html", "utf8");
  const js = fs.readFileSync("price-watch.js", "utf8");
  const css = fs.readFileSync("styles.css", "utf8");

  assert.match(html, /data-watch-mode="sandwich"/);
  // supportedModes 里必须注册，否则 URL ?mode=sandwich 会被顶回 prior。
  // 不能写成 /"sandwich"\]/：数组末尾随时可能再挂新模式（例如"devlaunch"）。
  assert.match(js, /const supportedModes = \[[^\]]*"sandwich"/);
  assert.match(js, /\/api\/sandwich-detector/);
  assert.match(js, /renderSandwichDetector/);
  assert.match(js, /loadSandwichDetector/);
  assert.match(js, /data-sandwich-wallet-form/);
  assert.match(js, /data-sandwich-toggle/);
  assert.match(js, /data-sandwich-remove/);
  assert.match(js, /data-sandwich-alerts/);
  assert.match(js, /data-sandwich-refresh/);
  assert.match(js, /data-sandwich-filter/);
  assert.match(js, /monitorBuyButton\(tradeRow\)/);
  assert.match(css, /\.price-watch-grid\.is-sandwich/);
  assert.match(css, /\.sandwich-result\.is-confirmed/);
  assert.match(css, /\.sandwich-result\.is-suspected/);
});

test("the four verdicts are the ones the engine actually emits", () => {
  const js = fs.readFileSync("price-watch.js", "utf8");
  for (const verdict of ["confirmed", "suspected", "normal", "unknown"]) {
    assert.match(js, new RegExp(`\\b${verdict}: \\{ label:`), verdict);
  }
  // 判定口径必须写在页面上：用户要能自己判断这个结论可不可信。
  assert.match(js, /同一区块内，受害者前一笔/);
  assert.match(js, /绝不猜/);
});

test("only confirmed/suspected alerts are painted as warnings", () => {
  const css = fs.readFileSync("styles.css", "utf8");
  const red = css.match(/\.sandwich-result\.is-confirmed \{[^}]*--down[^}]*\}/s);
  assert.ok(red, "已确认必须用红色（--down）");
  const amber = css.match(/\.sandwich-result\.is-suspected \{[^}]*--amber[^}]*\}/s);
  assert.ok(amber, "疑似必须用琥珀色（--amber）");
  // 「正常」与「无法取证」共用一个安静的左边框，不能混进告警配色。
  assert.match(css, /\.sandwich-result\.is-normal,\s*\.sandwich-result\.is-unknown \{/);
});

test("the poller refreshes the sub-board only while it is on screen", () => {
  const js = fs.readFileSync("price-watch.js", "utf8");
  assert.match(js, /if \(currentMode === "sandwich"\) \{\s*loadSandwichDetector\(\{ quiet: true \}\);\s*return;\s*\}/);
  assert.match(js, /grid\.classList\.add\("is-sandwich"\)/);
});

test("desktop package includes the sandwich detector module", () => {
  const pkg = JSON.parse(fs.readFileSync("package.json", "utf8"));
  const resource = pkg.build.extraResources.find((item) => item.to === "dashboard");
  assert.ok(resource.filter.includes("sandwich_detector.py"));
});
