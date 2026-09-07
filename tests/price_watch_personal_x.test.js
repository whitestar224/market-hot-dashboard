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

test("News Trade shows an auditable Meme-potential verdict for project X posts", () => {
  const js = fs.readFileSync("price-watch.js", "utf8");
  const css = fs.readFileSync("styles.css", "utf8");

  assert.match(js, /xMemePotential/);
  assert.match(js, /推文 Meme 潜力/);
  assert.match(js, /未发现足够的名称、形象、玩梗或社区参与信号/);
  assert.match(css, /\.news-trade-x-meme/);
  assert.match(css, /\.news-trade-x-meme\.is-high/);
});

test("News Trade renders AI event and Meme analysis without replacing safety gates", () => {
  const js = fs.readFileSync("price-watch.js", "utf8");
  const css = fs.readFileSync("styles.css", "utf8");

  assert.match(js, /事件与 Meme 判断/);
  assert.match(js, /aiAnalysisStatus/);
  assert.match(js, /narrativeStrength/);
  assert.match(js, /memePotential/);
  assert.match(js, /candidate\?\.security/);
  assert.match(js, /\/api\/ai\/news-trade/);
  assert.match(js, /requestVisibleNewsTradeAi/);
  assert.match(css, /\.news-trade-ai-analysis/);
  assert.match(css, /\.news-trade-ai-analysis\.is-pending/);
});
