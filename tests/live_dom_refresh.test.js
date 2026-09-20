const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const read = (name) => fs.readFileSync(path.join(root, name), "utf8");

test("background refresh reconciles stable elements instead of replacing the page", () => {
  const adapter = read("desktop_adapter.js");
  assert.match(adapter, /window\.XingyunLiveDom/);
  assert.match(adapter, /function syncChildren/);
  assert.match(adapter, /data-live-key/);

  for (const name of ["app.js", "rankings.js", "newboards.js"]) {
    const source = read(name);
    assert.match(source, /XingyunLiveDom\?\.render/);
    assert.match(source, /data-live-key/);
  }
  assert.match(read("price-watch.js"), /function renderGrid/);
});

test("News Trade remains one visible tab while consuming the deduplicated combined feed", () => {
  const html = read("price-watch.html");
  const source = read("price-watch.js");
  const newsTabs = html.match(/data-watch-mode="news"/g) || [];

  assert.equal(newsTabs.length, 1);
  assert.doesNotMatch(html, /data-watch-mode="events"/);
  assert.match(html, /data-watch-mode="news">News Trade<\/button>/);
  assert.match(source, /mergedEventItems = Array\.isArray\(payload\.mergedItems\)/);
  assert.match(source, /headingTitle\.textContent = "News Trade"/);
  assert.doesNotMatch(source, /headingTitle\.textContent = "News Trade 与事件驱动"/);
});

test("group opportunities expose explicit AI narrative and Meme analysis", () => {
  const source = read("price-watch.js");
  const server = read("server.py");

  assert.match(source, /wechat-opportunity-ai-status/);
  assert.match(source, /叙事强度/);
  assert.match(source, /Meme 潜力/);
  assert.match(server, /"narrativeStrength":/);
  assert.match(server, /"memePotential":/);
  assert.match(server, /重点识别土狗、链上新币、项目进展/);
});
