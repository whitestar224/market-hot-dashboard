const test = require("node:test");
const assert = require("node:assert/strict");
const Engine = require("../dragon-wave-engine.js");

const invalidLevels = [
  ["null", null],
  ["undefined", undefined],
  ["zero", 0],
  ["negative", -1],
  ["empty string", ""],
  ["blank string", "  "],
  ["false", false],
  ["true", true],
  ["NaN", NaN],
  ["Infinity", Infinity],
];

function compactTriangle(overrides = {}) {
  return {
    interval: "15m",
    foundationTypes: ["triangle"],
    auxiliaryTypes: [],
    hasPivot: true,
    consolidationBars: 21,
    structureQuality: 0.76,
    channelInteriorOccupancy: 0.9,
    channelSideTransitions: 4,
    triangleHasPriorAdvance: true,
    trianglePostSelloffRecovery: false,
    crossedLevel: true,
    openedBeyondTrigger: false,
    outerEdgeConfirmed: false,
    directStructuralBoundary: false,
    motherStructureNoise: false,
    riskStructureShape: null,
    ...overrides,
  };
}

for (const [label, previousHighLevel] of invalidLevels) {
  test(`compact triangle cannot manufacture prior-high evidence from ${label}`, () => {
    const hierarchy = Engine.assessExecutionHierarchy(compactTriangle({ previousHighLevel }));
    assert.equal(hierarchy.boosters.includes("previous-high"), false);
    assert.equal(hierarchy.permit, false);
    assert.ok(hierarchy.missing.includes("missing-mother-boundary"));
    assert.equal(hierarchy.missing.includes("hard-structure-veto"), false,
      "the missing true boundary, not an unrelated risk flag, must explain the failure");
  });
}

test("a compact triangle still accepts real positive prior-high evidence", () => {
  for (const previousHighLevel of [100, "100"]) {
    const hierarchy = Engine.assessExecutionHierarchy(compactTriangle({ previousHighLevel }));
    assert.equal(hierarchy.permit, true);
    assert.ok(hierarchy.boosters.includes("previous-high"));
    assert.equal(hierarchy.primaryFoundation, "mature-triangle-outer-edge");
  }
});

test("explicit previous-high and real outer-platform evidence do not require duplicate numeric fields", () => {
  const explicitAuxiliary = Engine.assessExecutionHierarchy(compactTriangle({
    auxiliaryTypes: ["previousHigh"],
    previousHighLevel: null,
  }));
  assert.equal(explicitAuxiliary.permit, true);
  assert.ok(explicitAuxiliary.boosters.includes("previous-high"));

  const platform = Engine.assessExecutionHierarchy({
    interval: "15m",
    foundationTypes: ["base"],
    auxiliaryTypes: [],
    previousHighLevel: null,
    outerEdgeConfirmed: true,
    outerEdgeScore: 90,
    consolidationBars: 48,
    ceilingAge: 10,
    platformTouchGroups: 4,
    horizontalLaunchHasPriorAdvance: true,
    launchDistancePercent: 1,
  });
  assert.equal(platform.permit, true);
  assert.equal(platform.primaryFoundation, "mother-platform-breakout");
  assert.ok(platform.boosters.includes("previous-high"));
});

function nestedTriangle(previousHighLevel, overrides = {}) {
  return {
    interval: "5m",
    mainWaveStage: "active",
    insideMotherBase: true,
    foundationTypes: ["triangle"],
    auxiliaryTypes: ["trendline"],
    previousHighLevel,
    consolidationBars: 80,
    structureQuality: 0.86,
    directStructuralBoundary: true,
    channelInteriorOccupancy: 0.7,
    channelSideTransitions: 3,
    triangleHasPriorAdvance: true,
    trianglePriorAdvanceAtr: 12,
    relativeVolume: 1.1,
    orderFlowScore: 60,
    crossedLevel: true,
    openedBeyondTrigger: false,
    launchDistancePercent: 1,
    motherStructureNoise: false,
    horizontalBrokenOuterPlatform: false,
    trianglePostSelloffRecovery: false,
    riskStructureShape: null,
    highLevelDistribution: false,
    ...overrides,
  };
}

for (const [label, previousHighLevel] of invalidLevels) {
  test(`nested triangle cannot bypass the mother-box veto using ${label} as a prior high`, () => {
    const hierarchy = Engine.assessExecutionHierarchy(nestedTriangle(previousHighLevel));
    // A valid dynamic upper rail exists in both cases. Only the nested-structure
    // exception additionally needs a real static prior high before it may trade
    // inside the parent box. Exercise that private helper via its public caller.
    assert.equal(hierarchy.primaryFoundation, "mature-triangle-outer-edge");
    assert.equal(hierarchy.permit, false);
    assert.ok(hierarchy.missing.includes("hard-structure-veto"));
    const outsideBox = Engine.assessExecutionHierarchy(nestedTriangle(previousHighLevel, {
      insideMotherBase: false,
    }));
    assert.equal(outsideBox.permit, true, "the same valid dynamic structure outside the box remains eligible");
  });
}

test("nested triangle retains its narrow exception when a genuine prior high is present", () => {
  for (const previousHighLevel of [100, "100"]) {
    assert.equal(Engine.assessExecutionHierarchy(nestedTriangle(previousHighLevel)).permit, true);
  }
  assert.equal(Engine.assessExecutionHierarchy(nestedTriangle(null, {
    auxiliaryTypes: ["trendline", "previousHigh"],
  })).permit, true);
});

test("one-hour and four-hour recognized dynamic boundaries remain executable without a static prior high", () => {
  for (const interval of ["1h", "4h"]) {
    const signal = {
      interval,
      foundationTypes: ["triangle"],
      auxiliaryTypes: ["trendline"],
      previousHighLevel: null,
      structureShape: "falling-wedge",
      triangleLines: {
        upper: { startIndex: 30, endIndex: 103, startPrice: 0.82, endPrice: 0.63 },
        lower: { startIndex: 31, endIndex: 103, startPrice: 0.66, endPrice: 0.58 },
      },
      consolidationBars: 74,
      structureQuality: 0.7758,
      channelInteriorOccupancy: 0.7118,
      channelMiddleParticipationRatio: 0.6769,
      channelSideTransitions: 2,
      triangleHasPriorAdvance: true,
      trianglePriorAdvanceAtr: 20.74,
      crossedLevel: true,
      openedBeyondTrigger: false,
      directStructuralBoundary: true,
      breakoutOpen: 0.62569,
      breakoutClose: 0.65743,
      aboveEma90: false,
      ema90SlopeAtDecision: -0.0074,
      motherStructureNoise: false,
      oneMinuteMotherBoxNoise: false,
      horizontalBrokenOuterPlatform: false,
      trianglePostSelloffRecovery: false,
      riskStructureShape: null,
      highLevelDistribution: false,
      launchDistancePercent: 2.7,
      score: 57,
      certaintyScore: 83,
      rhythmScore: 56,
      sentimentScore: 24,
    };
    const hierarchy = Engine.assessExecutionHierarchy(signal);
    assert.equal(hierarchy.boosters.includes("previous-high"), false);
    assert.equal(hierarchy.permit, true);
    assert.equal(Engine.isReviewedHigherTimeframeStructureBreak(signal), true);
    assert.equal(Engine.isRecognizedHigherTimeframeStructureBreak(signal), true);
    assert.equal(Engine.isHighCertaintyEntry(signal), true);
  }
});
