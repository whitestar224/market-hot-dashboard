const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const js = fs.readFileSync(path.join(root, "price-watch.js"), "utf8");

test("group monitor hides missing timestamps and accepts second-based timestamps", () => {
  assert.match(js, /Number\(item\.lastSeenAt\) > 0 \? relativeTime\(item\.lastSeenAt\) : ""/);
  assert.match(js, /time < 1_000_000_000_000 \? time \* 1000 : time/);
});

test("group monitor does not expose raw OneBot transport wording in its template", () => {
  assert.doesNotMatch(js, /HTTPConnectionPool/);
  assert.doesNotMatch(js, /NewConnectionError/);
});
