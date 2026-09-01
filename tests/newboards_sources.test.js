const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "newboards.html"), "utf8");
const js = fs.readFileSync(path.join(root, "newboards.js"), "utf8");
const server = fs.readFileSync(path.join(root, "server.py"), "utf8");

test("new coin page includes the four additional market sources", () => {
  for (const source of ["trade-xyz-new", "hyperliquid-new", "aster-new", "binance-alpha-new"]) {
    assert.match(server, new RegExp(`\\(\\"${source}\\"`));
    assert.match(js, new RegExp(`\\"${source}\\"`));
  }
  assert.match(html, /newboards\.js\?v=13/);
});

test("additional new markets reuse the existing ten-row board and alert feed", () => {
  assert.match(js, /slice\(0, 10\)/);
  assert.match(server, /def fetch_trade_xyz_new_coins/);
  assert.match(server, /def fetch_hyperliquid_new_coins/);
  assert.match(server, /def fetch_aster_new_coins/);
  assert.match(server, /def fetch_binance_alpha_new_coins/);
  assert.match(server, /"name": "newboards"[\s\S]*?parse_site_newboard_events/);
  assert.match(server, /speech_subject = "新股上市" if is_stock else "新币上新"/);
});
