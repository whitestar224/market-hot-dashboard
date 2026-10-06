const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const js = fs.readFileSync(path.join(root, "app.js"), "utf8");
const css = fs.readFileSync(path.join(root, "styles.css"), "utf8");
const server = fs.readFileSync(path.join(root, "server.py"), "utf8");
const state = fs.readFileSync(path.join(root, "src", "app", "core", "state.py"), "utf8");

test("cross-source backfill is a tunable, provider-scoped server feature", () => {
  assert.match(state, /GMGN_TRENCH_BACKFILL_MAX_ROWS/);
  assert.match(state, /GMGN_TRENCH_BACKFILL_PROVIDERS: tuple\[str, \.\.\.\] = \(/);
  assert.match(state, /GMGN_TRENCH_BACKFILL_ORIGIN_LABELS: dict\[str, str\] = \{/);
  assert.match(server, /GMGN_TRENCH_BACKFILL_MAX_ROWS,\r?\n/);
  assert.match(server, /def gmgn_trench_external_backfill_rows\(/);
  assert.match(server, /def gmgn_trench_merge_backfill_rows\(/);
  // 补录必须排除 GMGN 自己已经采到的币
  assert.match(server, /if "gmgn-trenches" in providers:/);
  assert.match(server, /"backfilledCount": backfilled_count/);
});

test("backfill runs after the research ingest so the gmgn channel keeps its membership", () => {
  const ingest = server.indexOf("board_research_rows = rows[:GMGN_TRENCH_RESPONSE_MAX_ROWS]");
  const backfill = server.indexOf("backfill_rows = gmgn_trench_external_backfill_rows(");
  const merge = server.indexOf("rows = gmgn_trench_merge_backfill_rows(");
  assert.ok(ingest >= 0);
  assert.ok(backfill > ingest);
  assert.ok(merge > backfill);
});

test("backfilled board rows are visibly labelled with their origin", () => {
  assert.match(js, /gmgn-trench-backfill-badge/);
  assert.match(js, /row\?\.backfilled/);
  assert.match(js, /row\?\.originLabels/);
  assert.match(js, /gmgn-trench-backfill-summary/);
  assert.match(js, /source\?\.backfilledCount/);
  assert.match(css, /\.gmgn-trench-backfill-badge \{/);
  assert.match(css, /\.gmgn-trench-backfill-summary/);
});
