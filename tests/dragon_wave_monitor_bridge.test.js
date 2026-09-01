const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const Bridge = require("../tools/dragon_wave_monitor_bridge.js");
const basedFiveMinuteRisingWedgeRows = require("./fixtures/basedusdt_binance_5m_2026-08-16_1540.json");

test("monitor bridge normalizes exchange rows with causal candle close times", () => {
  const rows = Bridge.normalizeRows([
    [1_700_000_000_000, 10, 11, 9, 10.5, 1234],
  ], "5m");
  assert.equal(rows.length, 1);
  assert.equal(rows[0].time, 1_700_000_000_000);
  assert.equal(rows[0].closeTime, 1_700_000_299_999);
  assert.equal(rows[0].volume, 1234);
});

test("monitor bridge always returns the unchanged six-row display contract", () => {
  const payload = Bridge.analyzeMonitorPayload({ now: 1_800_000_000_000, timeframes: {} });
  assert.equal(payload.strategy, "dragon-wave-engine");
  assert.deepEqual(payload.frames.map((frame) => frame.key), ["1m", "5m", "15m", "1h", "4h", "1d"]);
  assert.ok(payload.frames.every((frame) => frame.pattern === "数据不足"));
  assert.deepEqual(payload.signals, []);
});

test("monitor bridge carries the short-lived live context into the shared engine", () => {
  const adaptiveContext = {
    symbol: "PI",
    mode: "acceleration",
    label: "主升加速段",
    mainWaveStage: "active",
    sourceKind: "personal-x",
  };
  const payload = Bridge.analyzeMonitorPayload({
    now: 1_800_000_000_000,
    mainWaveStage: "active",
    mainWaveContextSource: "live-personal-x",
    mainWaveContextLabel: "临盘主升加速段",
    adaptiveContext,
    timeframes: {},
  });
  assert.deepEqual(payload.adaptiveContext, adaptiveContext);
  const source = fs.readFileSync(path.join(__dirname, "../tools/dragon_wave_monitor_bridge.js"), "utf8");
  assert.match(source, /Engine\.analyzeTimeframe\(candles, \{ \.\.\.options, interval, now \}\)/);
  assert.match(source, /options\.mainWaveContextSource/);
});

test("preselected hot leaders default to the active main-wave environment", () => {
  const source = fs.readFileSync(path.join(__dirname, "../tools/dragon_wave_monitor_bridge.js"), "utf8");
  assert.match(source, /else if \(options\.preselectedLeader\)/);
  assert.match(source, /options\.mainWaveStage = "active"/);
  assert.match(source, /options\.mainWaveContextSource = "leader-default-main-wave"/);
  assert.match(source, /龙头默认主升浪环境/);
});

test("BASED rising wedge is not published to the live board as a horizontal launch", () => {
  const payload = Bridge.analyzeMonitorPayload({
    now: basedFiveMinuteRisingWedgeRows.at(-1).closeTime + 1,
    preselectedLeader: true,
    timeframes: {
      "5m": basedFiveMinuteRisingWedgeRows.map((row) => [
        row.time,
        row.open,
        row.high,
        row.low,
        row.close,
        row.volume,
      ]),
    },
  });
  const frame = payload.frames.find((item) => item.key === "5m");
  assert.equal(frame.pattern, "无明确结构");
  assert.equal(frame.stage, "观察");
  assert.equal(frame.signal, null);
  assert.equal(frame.pending, null);
});

test("retained review candidates never enter popup or voice signal output", () => {
  const frame = Bridge.monitorFrame({
    interval: "5m",
    candles: [{ time: 1, closeTime: 2, open: 1, high: 2, low: 1, close: 2, volume: 1 }],
    signals: [],
    pending: [],
    structures: [],
    retainedCandidates: [{
      index: 0,
      time: 1,
      status: "candidate",
      pattern: "盘整突破",
      certaintyScore: 88,
    }],
  });
  assert.equal(frame.pattern, "无明确结构");
  assert.equal(frame.stage, "观察");
  assert.equal(frame.signal, null);
  assert.equal(frame.pending, null);
});

test("secondary breakout hint is exported as an alert-only red-B notification", () => {
  const frame = Bridge.monitorFrame({
    interval: "5m",
    candles: [
      { time: 1, closeTime: 2, open: 1, high: 2, low: 1, close: 2, volume: 1 },
      { time: 3, closeTime: 4, open: 1, high: 2, low: 1, close: 2, volume: 1 },
    ],
    signals: [],
    pending: [],
    structures: [],
    secondaryBreakoutHints: [{
      id: "second-hint",
      index: 1,
      time: 3,
      decisionTime: 3,
      status: "secondary-hint",
      secondaryBreakoutHint: true,
      alertOnly: true,
      pattern: "二次突破提示 · 盘整突破",
      certaintyScore: 86,
      triggerPrice: 1.8,
    }],
  });
  assert.equal(frame.stage, "二次突破提示");
  assert.equal(frame.signal, null);
  assert.equal(frame.alertHint.alertOnly, true);
  assert.equal(frame.alertHint.secondaryBreakoutHint, true);
});

test("strategy monitor exports the breakout candle low for unified stop-loss sizing", () => {
  const frame = Bridge.monitorFrame({
    interval: "1h",
    candles: [
      { time: 1, closeTime: 2, open: 98, high: 101, low: 94, close: 100, volume: 10 },
    ],
    signals: [{
      id: "breakout-stop",
      index: 0,
      time: 1,
      decisionTime: 1,
      pattern: "盘整突破",
      certaintyScore: 95,
      triggerPrice: 99,
      breakoutOpen: 98,
      breakoutLow: 94,
    }],
    pending: [],
    structures: [],
    secondaryBreakoutHints: [],
  });

  assert.equal(frame.signal.breakoutOpen, 98);
  assert.equal(frame.signal.breakoutLow, 94);
});

test("hot-coin monitor imports the shared engine instead of copying strategy rules", () => {
  const source = fs.readFileSync(path.join(__dirname, "../tools/dragon_wave_monitor_bridge.js"), "utf8");
  assert.match(source, /require\("\.\.\/dragon-wave-engine\.js"\)/);
  assert.match(source, /Engine\.analyzeTimeframe/);
  assert.match(source, /Engine\.applyContextGates/);
  assert.match(source, /Engine\.enforceIntervalStructurePolicy/);
  assert.doesNotMatch(source, /function\s+(detectTriangle|recognizePriceStructure|detectWBottom)/);
});
