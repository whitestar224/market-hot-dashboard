const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(root, "strategy.html"), "utf8");
const js = fs.readFileSync(path.join(root, "strategy.js"), "utf8");
const server = fs.readFileSync(path.join(root, "server.py"), "utf8");

test("Strategy Board consumes the shared Dragon Wave engine instead of duplicating recognition", () => {
  assert.match(server, /def strategy_dragon_wave_items\(/);
  assert.match(server, /price_structure_latest_snapshot_payload\(\)/);
  assert.match(server, /structure_item\.get\("strategy"\) or "dragon-wave-engine"/);
  assert.match(html, /龙头起爆策略信号/);
  assert.match(js, /item\.strategySignal \|\| item\.strategyPending/);
});

test("Strategy Board displays breakout-candle stop source and the 3 percent fallback", () => {
  assert.match(server, /def strategy_stop_loss_plan\(/);
  assert.match(server, /"breakout-candle-low" if use_candle_low else "default-3pct"/);
  assert.match(html, /K低点 \/ ≥3%/);
  assert.match(js, /preview\.stopLossSourceLabel/);
  assert.match(js, /preview\.stopLossReason/);
});
