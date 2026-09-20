const test = require("node:test");
const assert = require("node:assert/strict");
const Engine = require("../dragon-wave-engine.js");

test("urgent horizontal sublabel is removed without deleting an independent triangle B", () => {
  const signal = {
    id: "independent-triangle", status: "buy", interval: "15m", time: 1735866000000,
    pattern: "盘整突破 + 横盘起飞 + 三角突破 + 趋势线突破",
    foundationTypes: ["base", "triangle"], confluence: ["base", "triangle", "trendline"],
    horizontalLaunchUrgent: true, horizontalLaunchQualified: true,
    structureShape: "converging-triangle", consolidationBars: 85,
    triggerPrice: 2.4, executionHierarchy: { permit: true, childStructures: ["horizontal-launch"] },
    evidence: ["成熟三角独立成立"],
  };
  const before = structuredClone(signal);
  const displayed = Engine.normalizeDisplayedStructureLabels(signal);
  assert.equal(displayed.status, "buy");
  assert.equal(displayed.id, signal.id);
  assert.equal(displayed.triggerPrice, signal.triggerPrice);
  assert.equal(displayed.executionHierarchy.permit, true);
  assert.equal(displayed.pattern, "盘整突破 + 三角突破 + 趋势线突破");
  assert.deepEqual(displayed.displayFoundationTypes, ["triangle"]);
  assert.deepEqual(displayed.executionChildStructures, []);
  assert.equal(displayed.horizontalLaunchDisplayQualified, false);
  assert.deepEqual(signal, before, "normalization must not mutate cached evidence");
  assert.deepEqual(Engine.normalizeDisplayedStructureLabels(displayed), displayed);
});

test("a genuinely settled horizontal base keeps its label and B", () => {
  const signal = { status: "buy", pattern: "盘整突破 + 横盘起飞 + 突破前高",
    horizontalLaunchUrgent: false, horizontalLaunchInsufficientEdgeDwell: false };
  assert.equal(Engine.normalizeDisplayedStructureLabels(signal), signal);
});
