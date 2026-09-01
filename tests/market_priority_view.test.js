const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const js = fs.readFileSync(path.join(root, "app.js"), "utf8");
const css = fs.readFileSync(path.join(root, "styles.css"), "utf8");
const server = fs.readFileSync(path.join(root, "server.py"), "utf8");

test("narrative strength stays inside the original independent-board layout", () => {
  assert.match(html, /<option value="rank">榜单原序<\/option>/);
  assert.match(html, /<option value="priority">叙事强弱<\/option>/);
  assert.match(html, /id="priorityPeriodSelect"/);
  assert.match(html, /<option value="1h">近 1 小时<\/option>/);
  assert.match(html, /<option value="6h">近 6 小时<\/option>/);
  assert.match(html, /<option value="24h">近 24 小时<\/option>/);
  assert.doesNotMatch(html, /priority-control-dock/);
  assert.match(html, /styles\.css\?v=80/);
  assert.match(html, /app\.js\?v=17/);
  assert.match(js, /market-hot:payload:v8/);
  assert.match(js, /function activePriorityScores/);
  assert.match(js, /state\.sort === "priority"/);
  assert.doesNotMatch(js, /function renderPriorityBoard/);
});

test("server keeps heat diagnostics but reorders each source card only by narrative", () => {
  assert.match(server, /MARKET_PRIORITY_WINDOWS = \{"1h":/);
  assert.match(server, /"mode": "narrative-strength"/);
  assert.match(server, /"formula": \{"narrative": 35\}/);
  assert.match(server, /"sort": "narrativeScore-desc"/);
  assert.match(server, /def market_priority_current_assets/);
  assert.match(server, /def market_priority_external_boosts/);
  assert.match(server, /"marketPriorityHistory": market_priority_history/);
  assert.match(js, /function priorityIdentityKeys/);
  assert.match(js, /rows: sortRows\(\(source\.rows \|\| \[\]\)\.filter\(matchesQuery\), priorityScores\)/);
  assert.match(js, /aProfile\?\.narrativeScore/);
  assert.match(js, /return bScore - aScore/);
  assert.match(css, /\.leaderboard-grid/);
});
