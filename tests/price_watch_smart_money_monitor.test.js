const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");

test("monitor page exposes the five-chain smart-money buy console", () => {
  const html = fs.readFileSync("price-watch.html", "utf8");
  const js = fs.readFileSync("price-watch.js", "utf8");
  const css = fs.readFileSync("styles.css", "utf8");

  assert.match(html, /data-watch-mode="smartmoney"/);
  assert.match(js, /\/api\/smart-money-monitor/);
  assert.match(js, /renderSmartMoneyMonitor/);
  assert.match(js, /data-smart-money-form/);
  assert.match(js, /data-smart-money-toggle/);
  assert.match(js, /data-smart-money-remove/);
  assert.match(js, /Ethereum.*BSC.*Base.*Solana.*Robinhood/s);
  assert.match(js, /单笔净买入达到.*10,000U/);
  assert.match(js, /小额仅记录/);
  assert.match(js, /免费只读方案/);
  assert.match(js, /monitorBuyButton\(buyRow\)/);
  assert.match(css, /\.price-watch-grid\.is-smart-money/);
  assert.match(css, /\.smart-money-add-form/);
  assert.match(css, /\.smart-money-health-grid/);
  assert.match(css, /\.smart-money-wallet-list/);
  assert.match(css, /\.smart-money-event-list/);
});

test("desktop package includes the smart-money monitor module", () => {
  const pkg = JSON.parse(fs.readFileSync("package.json", "utf8"));
  const resource = pkg.build.extraResources.find((item) => item.to === "dashboard");
  assert.ok(resource.filter.includes("smart_money_monitor.py"));
});
