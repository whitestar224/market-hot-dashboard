const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const js = fs.readFileSync(path.join(root, "app.js"), "utf8");
const css = fs.readFileSync(path.join(root, "styles.css"), "utf8");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const server = fs.readFileSync(path.join(root, "server.py"), "utf8");

test("Ave card combines total and five chain views with a ten-row cap", () => {
  assert.match(js, /const AVE_CHAIN_OPTIONS = new Set\(\["all", "robinhood", "solana", "eth", "bsc", "base"\]\)/);
  assert.match(js, /function aveRowsForSource/);
  assert.match(js, /\.slice\(0, 10\)/);
  assert.match(js, /data-role="ave-chain"/);
  assert.match(js, /saveLocalPreference\(AVE_CHAIN_KEY/);
});

test("Ave supports only 1h 4h and 24h and defaults to 4h", () => {
  assert.match(js, /const AVE_PERIODS = new Set\(\["1h", "4h", "24h"\]\)/);
  assert.match(js, /function normalizeAvePeriod/);
  assert.match(js, /return AVE_PERIODS\.has\(period\) \? period : "4h"/);
  assert.match(js, /data-role="ave-period"/);
  assert.match(js, /periodMetrics/);
  assert.doesNotMatch(js, /data-role="ave-period"[\s\S]{0,500}5m/);
});

test("Ave card precedes THS and Futu US after the requested position swap", () => {
  const ave = server.indexOf('("ave", fetch_ave_hot)');
  const ths = server.indexOf('("ths", fetch_ths_hot)');
  const futu = server.indexOf('("futu-us", lambda: fetch_futu_hot("us"))');
  assert.ok(ave >= 0 && ths > ave && futu > ths);
});

test("Ave controls have compact card-header styling and the static bundle is bumped", () => {
  assert.match(css, /\.board-head-actions\.is-ave-hot/);
  assert.match(html, /app\.js\?v=28/);
  assert.match(js, /market-hot:payload:v12/);
});
