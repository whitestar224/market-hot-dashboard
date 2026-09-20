"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const Engine = require("../dragon-wave-engine.js");
const fixture = require("./fixtures/dragon-wave-pi-secondary-recovery.json");

// These real prefix/evaluation fixtures exercise the hint-finalization boundary.
// They do not replace the separately budgeted full OHLC/multiframe replay, and
// assert neither profitability nor an intrabar execution time.
function run(secondPatch = {}) {
  const candles = structuredClone(fixture.candles);
  const first = structuredClone(fixture.first);
  const second = { ...structuredClone(fixture.second), ...secondPatch };
  const hints = Engine.buildSecondaryBreakoutHints(
    [first, second], candles, { atr: Engine.atr(candles, 14) }, "1h",
  );
  return { second, hints: hints.filter(hint => hint.time === second.time) };
}

test("PI mature post-shock outer-boundary formal buy is not downgraded to red", () => {
  const { second, hints } = run();
  assert.equal(second.status, "buy");
  assert.equal(second.matureHigherTimeframePostShockRecovery, true);
  assert.equal(second.motherStructureNoise, false);
  assert.equal(second.insideMotherBase, false);
  assert.equal(second.unorderedRepairStillActive, false);
  assert.equal(second.outerEdgeConfirmed, true);
  assert.equal(Engine.assessExecutionHierarchy(second).permit, true);
  assert.equal(hints.length, 0, "An independently valid recovery buy must survive hint finalization");
  assert.ok(fixture.candles.every(candle => candle.closeTime <= fixture.provenance.cutoff));
  assert.equal(fixture.candles.at(-1).time, second.time, "Fixture has no post-trigger candle");
});

test("ordinary quiet-test washout recross still produces red non-executable hint", () => {
  const { hints } = run({ matureHigherTimeframePostShockRecovery: false });
  assert.equal(hints.length, 1);
  assert.equal(hints[0].status, "secondary-hint");
  assert.equal(hints[0].markerColor, "red");
  assert.equal(hints[0].executionAllowed, false);
  assert.equal(hints[0].alertOnly, true);
});

test("a lifecycle-filtered candidate cannot claim formal-buy immunity via recovery flag", () => {
  const { second, hints } = run({
    status: "filtered",
    reasons: ["同一盘整已有有效买点，尚未回落重置并突破更新后的前高"],
  });
  assert.equal(second.matureHigherTimeframePostShockRecovery, true);
  assert.equal(second.status, "filtered");
  assert.equal(hints.length, 1, "Filtered retries remain hints, not independently permitted green buys");
  assert.equal(hints[0].status, "secondary-hint");
  assert.equal(hints[0].executionAllowed, false);
});

test("unrepaired mother-box noise remains vetoed even with a spoofed recovery flag", () => {
  const { second, hints } = run({
    status: "filtered",
    motherStructureNoise: true,
    insideMotherBase: true,
    unorderedRepairStillActive: true,
    reasons: ["仍在母箱体内部无序波动"],
  });
  assert.equal(second.matureHigherTimeframePostShockRecovery, true);
  assert.equal(Engine.assessExecutionHierarchy(second).permit, false);
  assert.equal(hints.length, 0, "A blocked/noisy candidate must not manufacture a retry alert");
  assert.equal(second.status, "filtered");
});

test("strong order flow cannot use stopped-attempt recross to bypass unrepaired mother-box veto", () => {
  const { second, hints } = run({
    status: "filtered",
    motherStructureNoise: true,
    insideMotherBase: true,
    unorderedRepairStillActive: true,
    orderFlowScore: 80,
    reasons: ["仍在母箱体内部无序波动"],
  });
  assert.equal(Engine.assessExecutionHierarchy(second).permit, false);
  assert.equal(hints.length, 0, "Volume/flow improvement cannot revive active unordered repair as a red hint");
});
