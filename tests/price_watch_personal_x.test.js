const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");


test("price watch exposes the existing personal X realtime monitor", () => {
  const html = fs.readFileSync("price-watch.html", "utf8");
  const js = fs.readFileSync("price-watch.js", "utf8");
  const css = fs.readFileSync("styles.css", "utf8");

  assert.match(html, /data-watch-mode="personalx"/);
  assert.match(js, /\/api\/personal-x-monitor/);
  assert.match(js, /\/api\/personal-x-stream/);
  assert.match(js, /new EventSource/);
  assert.match(js, /renderPersonalXMonitor/);
  assert.match(js, /personal-x-post/);
  assert.match(js, /临盘应变信号/);
  assert.match(js, /personal-x-tactical-signal/);
  assert.match(js, /重点看/);
  assert.match(js, /有主升浪预期/);
  assert.match(js, /personal-x-tactical-type/);
  assert.match(js, /暂无生效/);
  assert.match(js, /已恢复并保留/);
  assert.match(js, /等待新动态/);
  assert.doesNotMatch(js, /个人 X 或监控微信群明确提到/);
  assert.match(css, /\.price-watch-grid\.is-personal-x/);
  assert.match(css, /\.personal-x-console/);
  assert.match(css, /\.personal-x-content-grid/);
  assert.match(css, /\.personal-x-tactical-panel/);
  assert.match(css, /border-radius: 999px/);
});
