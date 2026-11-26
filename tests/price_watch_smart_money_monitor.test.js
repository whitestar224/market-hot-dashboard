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
  // 弹窗线是「按地址」的（CryptoCharming 为 100U），文案不能再写死 10,000U。
  assert.match(js, /单笔净买入达到该地址弹窗线才弹窗/);
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

test("elysium 的聪明钱事件「查看」落 GMGN 交易页（不再去 429 的浏览器 / 站外站）", () => {
  const js = fs.readFileSync("price-watch.js", "utf8");
  // 用户 2026-11-09：「点击查看要跳转到 GMGN 的交易页」「不要跳转到 dexscreener」。
  assert.match(js, /function smartMoneyGmgnUrl/);
  assert.ok(js.includes("https://gmgn.ai/elysium/token/"));
  // 落 GMGN 时按钮文案必须改成「GMGN 交易」，别再冒充链上交易详情。
  assert.match(js, /explorerUrl\.includes\("gmgn\.ai\/"\) \? "GMGN 交易" : "查看链上交易"/);
  assert.doesNotMatch(js, /dexscreener\.com\/elysium/);
});
