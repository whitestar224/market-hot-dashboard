const test = require("node:test");
const assert = require("node:assert/strict");

const Engine = require("../dragon-wave-engine.js");
const Cases = require("../dragon-wave-cases.js");
const Data = require("../dragon-wave-data.js");
const hOneHourWedgeRows = require("./fixtures/husdt_1h_2025-06-29_2025-07-01.json");
const hFiveMinuteLaunchRows = require("./fixtures/husdt_5m_2025-07-02_1045.json");
const piFiveMinuteTriangleRows = require("./fixtures/piusdt_okx_5m_2025-02-22_1530.json");
const piFiveMinuteBoxRows = require("./fixtures/piusdt_okx_5m_2025-02-22_1905.json");
const piFiveMinuteEma90ReclaimRows = require("./fixtures/piusdt_okx_5m_2025-02-23_1115.json");
const mmtFifteenMinuteRows = require("./fixtures/mmtusdt_15m_2026-07-28_2026-07-30.json");
const notOneHourTriangleRows = require("./fixtures/notusdt_1h_2024-05-20_2024-05-30.json");
const basedFiveMinuteRisingWedgeRows = require("./fixtures/basedusdt_binance_5m_2026-08-16_1540.json");
const turboFifteenMinuteConsolidationRows = require("./fixtures/turbousdt_okx_15m_2024-05-23_2315.json");
const turboTwoStageConsolidationRows = require("./fixtures/turbousdt_okx_15m_2024-05-21_2024-05-25.json");

test("a bearish rejection just above a mature boundary remains a soft test", () => {
  const boundary = 1.120109090909091;
  const atrValue = 0.02445253772312437;
  assert.equal(Engine.isAttachedSoftBoundaryTest({
    open: 1.1267,
    high: 1.1271,
    low: 1.0961,
    close: 1.1037,
  }, boundary, atrValue), true);
  assert.equal(Engine.isAttachedSoftBoundaryTest({
    open: 1.115,
    high: 1.1567,
    low: 1.1085,
    close: 1.1432,
  }, 1.1176109090909092, atrValue), false);
});

function candle(index, overrides = {}) {
  const base = 100 + index * 0.03;
  const open = overrides.open ?? base;
  const close = overrides.close ?? base + 0.15;
  return {
    time: overrides.time ?? 1_700_000_000_000 + index * 60_000,
    closeTime: overrides.closeTime ?? 1_700_000_059_999 + index * 60_000,
    open,
    high: overrides.high ?? Math.max(open, close) + 0.45,
    low: overrides.low ?? Math.min(open, close) - 0.45,
    close,
    volume: overrides.volume ?? 100,
    quoteVolume: overrides.quoteVolume ?? (overrides.volume ?? 100) * close,
  };
}

function earlyNewCoinHigherFrames(decisionTime) {
  return [
    { interval: "1h", ms: 60 * 60_000, count: 51 },
    { interval: "4h", ms: 4 * 60 * 60_000, count: 13 },
    { interval: "1d", ms: 24 * 60 * 60_000, count: 3 },
  ].map(({ interval, ms, count }) => {
    const start = decisionTime - count * ms;
    const candles = Array.from({ length: count }, (_, index) => ({
      time: start + index * ms,
      closeTime: start + (index + 1) * ms - 1,
      open: 0.7 + index * 0.002,
      high: 0.72 + index * 0.002,
      low: 0.69 + index * 0.002,
      close: 0.71 + index * 0.002,
      volume: 100,
      quoteVolume: 71,
    }));
    return {
      interval,
      candles,
      indicators: { ema90: Array(count).fill(null), atr: Array(count).fill(0.02) },
      signals: [],
      pending: [],
      rejected: [],
      structures: [],
      stats: { signalCount: 0, pendingCount: 0, rejectedCount: 0 },
    };
  });
}

// 复现看板完整区间：目标前还存在一段上市早期宽幅交易，较短夹具本身看不到。
// 其中的单根长上影只用于让母区间采用稳健边界；所有数据都严格位于目标 K 线之前。
function withStaleListingMotherWindow(rows) {
  const count = 300;
  const intervalMs = 5 * 60_000;
  const start = rows[0].time - count * intervalMs;
  const earlier = Array.from({ length: count }, (_, index) => {
    const close = 0.735
      + Math.sin(index * 0.17) * 0.075
      + Math.sin(index * 0.041) * 0.035;
    const open = close + Math.sin(index * 0.33) * 0.008;
    const volume = 350_000 + 50_000 * Math.abs(Math.sin(index));
    return {
      time: start + index * intervalMs,
      closeTime: start + (index + 1) * intervalMs - 1,
      open,
      high: index === 50 ? 1.5 : Math.max(open, close) + 0.012,
      low: Math.min(open, close) - 0.012 - (index % 83 === 0 ? 0.045 : 0),
      close,
      volume,
      quoteVolume: volume * close,
    };
  });
  return earlier.concat(rows);
}

function horizontalBreakoutSeries() {
  const rows = Array.from({ length: 12 }, (_, index) => candle(index, {
    open: 94 + index * 0.46,
    close: 94.2 + index * 0.46,
    high: 94.5 + index * 0.46,
    low: 93.7 + index * 0.46,
    volume: 108,
  }));
  for (let index = 12; index < 55; index += 1) {
    rows.push(candle(index, {
      open: 99.8 + (index % 4) * 0.08,
      close: 100 + ((index + 1) % 5) * 0.06,
      high: 100.75 - (index % 3) * 0.04,
      low: 99.3 + (index % 2) * 0.08,
      volume: 92 + (index % 5) * 4,
    }));
  }
  rows.push(candle(55, { open: 100.4, close: 102.15, high: 102.45, low: 100.25, volume: 238 }));
  return rows;
}

function ascendingChannelBreakoutSeries({ wedge = false } = {}) {
  const rows = Array.from({ length: 36 }, (_, index) => candle(index, {
    open: 90 + index * 0.25,
    close: 90.15 + index * 0.25,
    high: 90.45 + index * 0.25,
    low: 89.7 + index * 0.25,
    volume: 105,
  }));
  const channelBars = 51;
  for (let step = 0; step < channelBars; step += 1) {
    const upper = 105 + step * (wedge ? 0.12 : 0.17);
    const lower = 99 + step * (wedge ? 0.18 : 0.17);
    const spread = upper - lower;
    const position = 0.5 + Math.cos(Math.PI * 2 * step / 10) * 0.43;
    const close = lower + spread * position;
    rows.push(candle(rows.length, {
      open: close + (step % 2 ? 0.07 : -0.07),
      close,
      high: Math.min(upper, close + 0.32),
      low: Math.max(lower, close - 0.32),
      volume: 92 + (step % 4),
    }));
  }
  const upperAtBreak = 105 + channelBars * (wedge ? 0.12 : 0.17);
  rows.push(candle(rows.length, {
    open: upperAtBreak - 0.45,
    close: upperAtBreak + 1.1,
    high: upperAtBreak + 1.45,
    low: upperAtBreak - 0.58,
    volume: 245,
  }));
  return rows;
}

function postSelloffPlatformBreakoutSeries() {
  const rows = Array.from({ length: 28 }, (_, index) => candle(index, {
    open: 108 + index * 0.12,
    close: 108.08 + index * 0.12,
    high: 108.35 + index * 0.12,
    low: 107.72 + index * 0.12,
    volume: 105,
  }));
  for (let step = 0; step < 7; step += 1) {
    const close = 110.8 - (step + 1) * 2.35;
    rows.push(candle(rows.length, {
      open: close + 1.7,
      close,
      high: close + 1.95,
      low: close - 0.55,
      volume: 250 - step * 8,
    }));
  }
  for (let step = 0; step < 20; step += 1) {
    const close = 94.8 + step * 0.25;
    rows.push(candle(rows.length, {
      open: close - 0.12,
      close,
      high: close + 0.38,
      low: close - 0.42,
      volume: 118,
    }));
  }
  for (let step = 0; step < 42; step += 1) {
    const center = 99.55 + Math.sin(step * 1.6) * 0.22;
    rows.push(candle(rows.length, {
      open: center - 0.08,
      close: center + 0.05,
      high: step % 11 === 2 ? 100.45 : center + 0.38,
      low: center - 0.42,
      volume: 86 + (step % 4),
    }));
  }
  rows.push(candle(rows.length, {
    open: 100.18,
    close: 102.1,
    high: 102.4,
    low: 100.08,
    volume: 238,
  }));
  return rows;
}

function broadLeaderPlatformBreakoutSeries() {
  const rows = Array.from({ length: 42 }, (_, index) => candle(index, {
    open: 92 + index * 0.27,
    close: 92.15 + index * 0.27,
    high: 92.5 + index * 0.27,
    low: 91.7 + index * 0.27,
    volume: 108,
  }));
  for (let step = 0; step < 34; step += 1) {
    const center = 103.9 + step * 0.045 + Math.sin(step * 1.7) * 0.42;
    rows.push(candle(rows.length, {
      open: center + (step % 2 ? 0.12 : -0.14),
      close: center + (step % 3 ? -0.08 : 0.1),
      high: [3, 17].includes(step) ? 106.2 : Math.min(106.05, center + 0.55),
      low: center - 0.58,
      volume: 82 + (step % 5),
    }));
  }
  rows.push(candle(rows.length, {
    open: 105.55,
    close: 108.4,
    high: 108.8,
    low: 105.42,
    volume: 285,
  }));
  return rows;
}

function ordiStyleThreeAttemptSeries() {
  const rows = horizontalBreakoutSeries().slice(0, -1);
  rows.push(candle(rows.length, { open: 100.4, close: 101.4, high: 101.65, low: 100.25, volume: 238 }));
  rows.push(candle(rows.length, { open: 101.2, close: 99.55, high: 101.3, low: 99.05, volume: 210 }));
  for (let step = 0; step < 60; step += 1) {
    rows.push(candle(rows.length, {
      open: 99.7 + (step % 4) * 0.12,
      close: 99.85 + (step % 5) * 0.11,
      high: 101.5 - (step % 3) * 0.03,
      low: 99.2 + (step % 2) * 0.05,
      volume: 90,
    }));
  }
  rows.push(candle(rows.length, { open: 101.45, close: 102.05, high: 102.3, low: 101.3, volume: 230 }));
  rows.push(candle(rows.length, { open: 101.9, close: 99.6, high: 102, low: 99, volume: 220 }));
  for (let step = 0; step < 70; step += 1) {
    rows.push(candle(rows.length, {
      open: 99.8 + (step % 4) * 0.13,
      close: 99.9 + (step % 5) * 0.12,
      high: 102.15 - (step % 3) * 0.04,
      low: 99.25 + (step % 2) * 0.05,
      volume: 88,
    }));
  }
  rows.push(candle(rows.length, { open: 102, close: 104.1, high: 104.4, low: 101.9, volume: 280 }));
  return rows;
}

function previousHighBreakSeries() {
  const rows = Array.from({ length: 60 }, (_, index) => candle(index, {
    open: 88 + index * 0.18,
    close: 88.12 + index * 0.18,
    high: 88.5 + index * 0.18,
    low: 87.7 + index * 0.18,
    volume: 100 + (index % 4) * 3,
  }));
  rows.push(candle(60, { open: 98.7, close: 99.55, high: 99.72, low: 98.55, volume: 245 }));
  return rows;
}

function wContinuationSeries() {
  const closes = [
    100, 101, 102, 103, 102, 101, 99, 97, 96, 97, 99, 101, 103,
    104, 103, 101, 99, 97.2, 96.4, 97.5, 99.5, 101.5, 103.4, 104.5,
  ];
  const rows = [];
  for (let index = 0; index < 40; index += 1) {
    rows.push(candle(index, {
      open: 90 + index * 0.25,
      close: 90.12 + index * 0.25,
      high: 90.5 + index * 0.25,
      low: 89.7 + index * 0.25,
      volume: 100,
    }));
  }
  closes.forEach((close, offset) => {
    const index = rows.length;
    const isBreakout = offset === closes.length - 1;
    rows.push(candle(index, {
      open: isBreakout ? close - 1.3 : close - 0.18,
      close,
      high: close + 0.3,
      low: isBreakout ? close - 1.5 : close - 0.5,
      volume: isBreakout ? 245 : 105,
    }));
  });
  return rows;
}

function triangleBreakoutSeries() {
  const rows = Array.from({ length: 35 }, (_, index) => candle(index, {
    open: 92 + index * 0.2,
    close: 92.12 + index * 0.2,
    high: 92.5 + index * 0.2,
    low: 91.7 + index * 0.2,
    volume: 100,
  }));
  for (let step = 0; step < 32; step += 1) {
    const upper = 104 - step * 0.065;
    const lower = 98 + step * 0.06;
    const position = 0.5 + Math.cos(Math.PI * 2 * step / 6) * 0.43;
    const close = lower + (upper - lower) * position;
    const index = rows.length;
    rows.push(candle(index, {
      open: close + (step % 2 ? 0.08 : -0.08),
      close,
      high: Math.min(upper, close + 0.28),
      low: Math.max(lower, close - 0.28),
      volume: 96,
    }));
  }
  rows.push(candle(rows.length, { open: 101.45, close: 103.8, high: 104.1, low: 101.35, volume: 230 }));
  return rows;
}

function piOneHourMultiBoundaryBreakoutSeries() {
  const startTime = Date.parse("2025-02-22T18:00:00Z");
  const rows = [];
  for (let index = 0; index < 35; index += 1) {
    const close = 86 + index * 0.46;
    rows.push(candle(index, {
      time: startTime + index * 3_600_000,
      closeTime: startTime + (index + 1) * 3_600_000 - 1,
      open: close - 0.16,
      close,
      high: close + 0.34,
      low: close - 0.42,
      volume: 108,
    }));
  }
  for (let step = 0; step < 45; step += 1) {
    const upper = 108.2 - step * 0.085;
    const lower = 98.7 + step * 0.105;
    const position = 0.5 + Math.cos(Math.PI * 2 * step / 8) * 0.43;
    const close = lower + (upper - lower) * position;
    const index = rows.length;
    rows.push(candle(index, {
      time: startTime + index * 3_600_000,
      closeTime: startTime + (index + 1) * 3_600_000 - 1,
      open: close + (step % 2 ? 0.08 : -0.08),
      close,
      high: Math.min(upper, close + 0.3),
      low: Math.max(lower, close - 0.3),
      volume: 91,
    }));
  }
  const index = rows.length;
  rows.push(candle(index, {
    time: Date.parse("2025-02-26T02:00:00Z"),
    closeTime: Date.parse("2025-02-26T02:59:59.999Z"),
    open: 103.75,
    close: 108.7,
    high: 109.05,
    low: 103.55,
    volume: 260,
  }));
  return rows;
}

function piOneMinutePostImpulseHorizontalLaunchSeries() {
  const targetTime = Date.parse("2025-02-22T12:17:00Z");
  const startTime = targetTime - 97 * 60_000;
  const rows = [];
  for (let step = 0; step < 35; step += 1) {
    const close = 0.856 + step * 0.00055;
    rows.push(candle(rows.length, {
      time: startTime + rows.length * 60_000,
      closeTime: startTime + (rows.length + 1) * 60_000 - 1,
      open: close - 0.0004,
      close,
      high: close + 0.0012,
      low: close - 0.0014,
      volume: 105,
    }));
  }
  for (let step = 0; step < 12; step += 1) {
    const close = 0.876 + step * 0.0081;
    rows.push(candle(rows.length, {
      time: startTime + rows.length * 60_000,
      closeTime: startTime + (rows.length + 1) * 60_000 - 1,
      open: close - 0.0046,
      close,
      high: close + 0.0026,
      low: close - 0.0052,
      volume: 155 + step * 12,
    }));
  }
  for (let step = 0; step < 50; step += 1) {
    const center = 0.976 + Math.sin(step * 1.55) * 0.0065 + step * 0.00012;
    rows.push(candle(rows.length, {
      time: startTime + rows.length * 60_000,
      closeTime: startTime + (rows.length + 1) * 60_000 - 1,
      open: center + (step % 2 ? 0.0012 : -0.001),
      close: center,
      high: step % 13 === 3 ? 0.9928 : Math.min(0.9924, center + 0.0032),
      low: center - 0.0036,
      volume: 82 + (step % 5) * 3,
    }));
  }
  rows.push(candle(rows.length, {
    time: targetTime,
    closeTime: targetTime + 59_999,
    open: 0.9898,
    close: 1.0038,
    high: 1.008,
    low: 0.9892,
    volume: 245,
  }));
  return rows;
}

function piOneMinuteMotherBoxNoiseSeries() {
  const targetTime = Date.parse("2025-02-22T21:34:00Z");
  const startTime = targetTime - 620 * 60_000;
  const rows = [];
  const push = (values) => rows.push(candle(rows.length, {
    time: startTime + rows.length * 60_000,
    closeTime: startTime + (rows.length + 1) * 60_000 - 1,
    ...values,
  }));
  for (let step = 0; step < 25; step += 1) {
    const close = 0.855 + step * 0.00055;
    push({ open: close - 0.0004, close, high: close + 0.0014, low: close - 0.0015, volume: 102 });
  }
  for (let step = 0; step < 15; step += 1) {
    const close = 0.87 + step * 0.0081;
    push({
      open: close - 0.0042,
      close,
      high: step === 14 ? 1.014 : close + 0.0032,
      low: close - 0.005,
      volume: 165 + step * 9,
    });
  }
  for (let step = 0; step < 520; step += 1) {
    const close = 0.938 + Math.sin(step * 0.19) * 0.043 + Math.sin(step * 0.051) * 0.008;
    push({
      open: close + (step % 2 ? 0.0015 : -0.0013),
      close,
      high: close + 0.0032,
      low: close - 0.0035,
      volume: 88 + (step % 7) * 3,
    });
  }
  for (let step = 0; step < 60; step += 1) {
    const close = 0.957 + Math.sin(step * 1.55) * 0.0035 + step * 0.000025;
    push({
      open: close + (step % 2 ? 0.0008 : -0.0007),
      close,
      high: step % 13 === 4 ? 0.9662 : Math.min(0.9658, close + 0.0021),
      low: close - 0.0024,
      volume: 80 + (step % 5) * 2,
    });
  }
  push({ open: 0.9648, close: 0.971, high: 0.973, low: 0.9642, volume: 218 });
  return rows;
}

function unorderedMotherBoxSeries(length = 360) {
  return Array.from({ length }, (_, index) => {
    const close = 110 + Math.sin(index * 0.21) * 8.6 + Math.sin(index * 0.057) * 1.2;
    return candle(index, {
      open: close + (index % 2 ? 0.18 : -0.16),
      close,
      high: close + 0.62,
      low: close - 0.65,
      volume: 95 + (index % 6),
    });
  });
}

function postImpulseHighLevelRotationSeries() {
  const rows = [];
  for (let step = 0; step < 60; step += 1) {
    const close = 100 + step * 0.78;
    rows.push(candle(rows.length, {
      open: close - 0.3,
      close,
      high: close + 0.7,
      low: close - 0.8,
      volume: 110 + step,
    }));
  }
  rows.push(candle(rows.length, {
    open: 148,
    close: 156.5,
    high: 160,
    low: 147,
    volume: 280,
  }));
  for (let step = 0; step < 140; step += 1) {
    const close = 143 + Math.sin(step * 0.31) * 10 + Math.sin(step * 0.087) * 2;
    rows.push(candle(rows.length, {
      open: close + (step % 2 ? 0.35 : -0.32),
      close,
      high: close + 0.9,
      low: close - 1,
      volume: 98 + (step % 8),
    }));
  }
  return rows;
}

function ascendingTriangleBreakoutSeries({ precedingDecline = false } = {}) {
  const rows = Array.from({ length: 35 }, (_, index) => {
    const close = precedingDecline ? 125 - index * 0.72 : 86 + index * 0.45;
    return candle(index, {
      open: close + (precedingDecline ? 0.12 : -0.15),
      close,
      high: close + 0.35,
      low: close - 0.45,
      volume: 110,
    });
  });
  for (let step = 0; step < 30; step += 1) {
    const upper = 102.2;
    const lower = 96.5 + step * 0.13;
    const spread = upper - lower;
    const position = 0.5 + Math.cos(Math.PI * 2 * step / 8) * 0.43;
    const close = lower + spread * position;
    rows.push(candle(rows.length, {
      open: close + (step % 2 ? 0.06 : -0.06),
      close,
      high: Math.min(upper, close + 0.26),
      low: Math.max(lower, close - 0.26),
      volume: 92,
    }));
  }
  rows.push(candle(rows.length, {
    open: 101.45,
    close: 103.3,
    high: 103.6,
    low: 101.3,
    volume: 230,
  }));
  return rows;
}

function triangleInsideDowntrendRepairSeries() {
  const rows = [];
  for (let index = 0; index < 72; index += 1) {
    const close = 142 - index * 0.52 + Math.sin(index * 0.65) * 0.35;
    rows.push(candle(index, {
      open: close + 0.14,
      close,
      high: close + 0.42,
      low: close - 0.48,
      volume: 112,
    }));
  }
  for (let step = 0; step < 10; step += 1) {
    const close = 104.6 + step * 0.48;
    rows.push(candle(rows.length, {
      open: close - 0.15,
      close,
      high: close + 0.34,
      low: close - 0.4,
      volume: 118,
    }));
  }
  for (let step = 0; step < 30; step += 1) {
    const upper = 112.35;
    const lower = 107.2 + step * 0.105;
    const spread = upper - lower;
    const position = 0.5 + Math.cos(Math.PI * 2 * step / 8) * 0.43;
    const close = lower + spread * position;
    rows.push(candle(rows.length, {
      open: close + (step % 2 ? 0.06 : -0.06),
      close,
      high: Math.min(upper, close + 0.25),
      low: Math.max(lower, close - 0.25),
      volume: 92,
    }));
  }
  rows.push(candle(rows.length, {
    open: 111.7,
    close: 113.5,
    high: 113.85,
    low: 111.55,
    volume: 238,
  }));
  return rows;
}

function modestImpulseContextRows() {
  const rows = [];
  for (let index = 0; index < 50; index += 1) {
    const close = 100 + Math.sin(index * 0.9) * 0.08;
    rows.push(candle(index, {
      open: close - 0.03,
      close,
      high: close + 0.18,
      low: close - 0.18,
      volume: 86,
    }));
  }
  for (let step = 0; step < 8; step += 1) {
    const close = 100.05 + step * 0.17;
    rows.push(candle(rows.length, {
      open: close - 0.08,
      close,
      high: close + 0.17,
      low: close - 0.16,
      volume: 104,
    }));
  }
  for (let step = 0; step < 24; step += 1) {
    const close = 101.2 + Math.sin(step * 1.1) * 0.11;
    rows.push(candle(rows.length, {
      open: close - 0.04,
      close,
      high: close + 0.19,
      low: close - 0.19,
      volume: 82,
    }));
  }
  return rows;
}

function triangleInsideBroadRangeSeries() {
  const rows = [];
  for (let index = 0; index < 72; index += 1) {
    const close = 100 + Math.sin(index * 0.55) * 2.35;
    rows.push(candle(index, {
      open: close + Math.cos(index * 0.7) * 0.12,
      close,
      high: close + 0.42,
      low: close - 0.42,
      volume: 94,
    }));
  }
  for (let step = 0; step < 10; step += 1) {
    const close = 97.6 + step * 0.22;
    rows.push(candle(rows.length, {
      open: close - 0.08,
      close,
      high: close + 0.24,
      low: close - 0.24,
      volume: 98,
    }));
  }
  for (let step = 0; step < 30; step += 1) {
    const upper = 101.2;
    const lower = 98.3 + step * 0.07;
    const spread = upper - lower;
    const position = 0.5 + Math.cos(Math.PI * 2 * step / 8) * 0.43;
    const close = lower + spread * position;
    rows.push(candle(rows.length, {
      open: close + (step % 2 ? 0.05 : -0.05),
      close,
      high: Math.min(upper, close + 0.2),
      low: Math.max(lower, close - 0.2),
      volume: 84,
    }));
  }
  rows.push(candle(rows.length, {
    open: 100.75,
    close: 102.15,
    high: 102.42,
    low: 100.62,
    volume: 224,
  }));
  return rows;
}

function higherTimeframeBottomBaseRows() {
  const rows = [];
  for (let index = 0; index < 30; index += 1) {
    const close = 120 - index;
    rows.push(candle(index, {
      open: close + 0.2,
      close,
      high: close + 0.5,
      low: close - 0.5,
      volume: 100,
    }));
  }
  for (let index = 30; index < 80; index += 1) {
    const close = 91 + Math.sin(index * 0.7) * 0.35;
    rows.push(candle(index, {
      open: close - 0.08,
      close,
      high: close + 0.45,
      low: close - 0.45,
      volume: 80,
    }));
  }
  return rows;
}

function longConvergenceBreakoutSeries({ risingLower = true } = {}) {
  const rows = Array.from({ length: 60 }, (_, index) => candle(index, {
    open: 88 + index * 0.19,
    close: 88.12 + index * 0.19,
    high: 88.42 + index * 0.19,
    low: 87.72 + index * 0.19,
    volume: 108,
  }));
  for (let step = 0; step <= 96; step += 1) {
    const upper = 112 - step * 0.075;
    const lower = risingLower ? 96 + step * 0.075 : 96 - step * 0.012;
    const spread = upper - lower;
    const wave = (1 + Math.cos(Math.PI * 2 * step / 12)) / 2;
    const close = lower + spread * (0.08 + wave * 0.84);
    rows.push(candle(rows.length, {
      open: close + (step % 2 ? 0.08 : -0.08),
      close,
      // 末端几根仍在上轨下方蓄力；真正的首次越线只留给最后一根突破 K。
      high: step >= 93 ? Math.min(upper - 0.24, close + 0.24) : Math.min(upper, close + 0.24),
      low: Math.max(lower, close - 0.24),
      volume: 102 - Math.min(28, step * 0.24),
    }));
  }
  const upperAtBreak = 112 - 97 * 0.075;
  rows.push(candle(rows.length, {
    open: upperAtBreak - 0.28,
    close: upperAtBreak + 2.1,
    high: upperAtBreak + 2.5,
    low: upperAtBreak - 0.42,
    volume: 255,
  }));
  return rows;
}

function envelopeOccupancySeries({ hollow = false } = {}) {
  const rows = [];
  const upperAt = (index) => 112 - index * 0.04;
  const lowerAt = (index) => 96 - index * 0.01;
  for (let index = 0; index < 64; index += 1) {
    const upper = upperAt(index);
    const lower = lowerAt(index);
    const spread = upper - lower;
    let position;
    if (hollow) {
      // 先从上沿快速滑到下沿，随后长期贴着下沿走。两条线之间虽然没有
      // K 线越界，却留下大片无人交易的空腔，不能算同一段有效收敛。
      position = index < 9
        ? 0.9 - index * 0.09
        : 0.09 + Math.sin(index * 0.7) * 0.025;
    } else {
      // 有效结构会在上下轨之间多次往返，盘整真正填充两条边界共同定义的空间。
      position = 0.5 + Math.cos(Math.PI * 2 * index / 12) * 0.4;
    }
    const close = lower + spread * position;
    rows.push(candle(index, {
      open: close + (index % 2 ? 0.08 : -0.08),
      close,
      high: Math.min(upper, close + 0.22),
      low: Math.max(lower, close - 0.22),
      volume: 90,
    }));
  }
  return { rows, upperAt, lowerAt };
}

function pivotReclaimSeries() {
  const rows = Array.from({ length: 48 }, (_, index) => candle(index, {
    open: 92 + index * 0.2,
    close: 92.14 + index * 0.2,
    high: 92.45 + index * 0.2,
    low: 91.7 + index * 0.2,
    volume: 100,
  }));
  const pullback = [101.1, 100.5, 99.9, 99.5, 99.8, 100.2, 100.8];
  pullback.forEach((close, offset) => {
    rows.push(candle(rows.length, {
      open: close - 0.12,
      close,
      high: close + 0.35,
      low: close - 0.38,
      volume: 88 + offset,
    }));
  });
  rows.push(candle(rows.length, { open: 100.7, close: 102.3, high: 102.55, low: 100.55, volume: 205 }));
  return rows;
}

function pullbackRelaunchSeries() {
  const rows = Array.from({ length: 70 }, (_, index) => candle(index, {
    open: 92 + index * 0.24,
    close: 92.16 + index * 0.24,
    high: 92.42 + index * 0.24,
    low: 91.82 + index * 0.24,
    volume: 100 + (index % 5) * 3,
  }));
  const flag = [108.4, 108.1, 107.7, 107.9, 108.05, 107.86, 108.12, 108.2, 108.08, 108.28, 108.22, 108.34];
  flag.forEach((close, offset) => rows.push(candle(rows.length, {
    open: close - (offset % 2 ? -0.06 : 0.08),
    close,
    high: close + 0.24,
    low: close - 0.3,
    volume: 82 + offset,
  })));
  rows.push(candle(rows.length, { open: 108.3, close: 111.2, high: 111.45, low: 108.18, volume: 265 }));
  return rows;
}

function withSmoothIgnition(rows, bars = 8) {
  const trigger = rows.at(-1);
  let close = trigger.close;
  for (let offset = 1; offset <= bars; offset += 1) {
    const open = close - 0.08;
    close += 0.72 + offset * 0.04;
    rows.push(candle(rows.length, {
      open,
      close,
      high: close + 0.16,
      low: open - 0.18,
      volume: Math.max(145, 205 - offset * 6),
    }));
  }
  return rows;
}

function withChoppyFailure(rows) {
  const trigger = rows.at(-1);
  const closes = [trigger.close - 0.9, trigger.close + 0.35, trigger.close - 1.15, trigger.close + 0.2,
    trigger.close - 0.75, trigger.close + 0.45, trigger.close - 0.6, trigger.close - 0.25];
  closes.forEach((close, offset) => {
    rows.push(candle(rows.length, {
      open: close + (offset % 2 ? -0.2 : 0.25),
      close,
      high: close + 0.55,
      low: close - 0.6,
      volume: 125,
    }));
  });
  return rows;
}

function withNextOpen(rows, overrides = {}) {
  const confirmation = rows.at(-1);
  const open = overrides.open ?? confirmation.close + 0.05;
  rows.push(candle(rows.length, {
    open,
    close: overrides.close ?? open + 0.18,
    high: overrides.high ?? open + 0.35,
    low: overrides.low ?? open - 0.22,
    volume: overrides.volume ?? 110,
  }));
  return rows;
}

function twoStageLeaderSeries() {
  const rows = Array.from({ length: 45 }, (_, index) => candle(index, {
    open: 99.7 + (index % 4) * 0.08,
    close: 99.9 + (index % 5) * 0.07,
    high: 100.65 - (index % 3) * 0.03,
    low: 99.2 + (index % 2) * 0.06,
    volume: 95,
  }));
  rows.push(candle(rows.length, { open: 100.35, close: 102.2, high: 102.5, low: 100.25, volume: 220 }));
  for (let step = 0; step < 8; step += 1) {
    const close = 102.5 + step * 0.65;
    rows.push(candle(rows.length, { open: close - 0.25, close, high: close + 0.3, low: close - 0.45, volume: 140 }));
  }
  for (let step = 0; step < 24; step += 1) {
    const close = 107.2 + (step % 5) * 0.12;
    rows.push(candle(rows.length, { open: close - 0.08, close, high: 108.05 - (step % 3) * 0.03, low: 106.75 + (step % 2) * 0.08, volume: 82 }));
  }
  rows.push(candle(rows.length, { open: 107.75, close: 110.4, high: 110.8, low: 107.6, volume: 260 }));
  return rows;
}

test("catalog preserves the 85 active document ranges, removes COW and corrects the BAKE date", () => {
  assert.equal(Cases.length, 85);
  assert.equal(Cases[0].symbol, "TUT");
  assert.ok(Cases.some((item) => item.symbol === "BLESS" && item.pair === "BLESSUSDT"));
  assert.ok(!Cases.some((item) => item.symbol === "BELSS"));
  assert.ok(Cases.some((item) => item.symbol === "TRUMP" && item.start === "2025-01-18"));
  assert.equal(Cases.find((item) => item.symbol === "币安人生")?.pair, "币安人生USDT");
  assert.ok(!Cases.some((item) => item.symbol === "COW"));
  const bake = Cases.find((item) => item.symbol === "BAKE");
  assert.equal(bake.sourceEnd, "2025-09-30");
  assert.equal(bake.valid, true);
});

test("detects horizontal-base plus previous-high confluence without future bars", () => {
  const rows = horizontalBreakoutSeries();
  const result = Engine.analyzeTimeframe(rows, { interval: "5m", now: 1_800_000_000_000 });
  const signal = result.signals.find((item) => item.index === rows.length - 1);
  assert.ok(signal, JSON.stringify({ signals: result.signals, rejected: result.rejected.slice(-4) }));
  assert.match(signal.pattern, /横盘起飞/);
  assert.match(signal.pattern, /突破前高/);
  assert.equal(signal.status, "buy");
  assert.equal(signal.fillModel, "prearmed-stop-cross-from-below");
  assert.ok(Number.isFinite(signal.structuralEvidenceScore));
  assert.ok(signal.consolidationBars >= 40);
  assert.equal("followThrough" in signal, false);
  assert.equal(signal.visualSignature.causality, "completed-candles-before-selected-index-only");
  assert.equal(signal.visualSignature.selectedCandleTime, rows.at(-1).time);
  assert.equal(signal.visualSignature.featureCutoffTime, rows.at(-2).closeTime);
});

test("recognizes a rising channel envelope and vetoes its terminal prior-high breakout", () => {
  const rows = ascendingChannelBreakoutSeries();
  const atrValues = Engine.atr(rows);
  const trap = Engine.detectAscendingChannelTrap(rows, rows.length - 1, atrValues.at(-2));
  assert.ok(trap, "the occupied parallel rising envelope should be recognized");
  assert.equal(trap.shape, "rising-channel");
  assert.ok(trap.upperTouchGroups >= 2 && trap.lowerTouchGroups >= 2, JSON.stringify(trap));
  assert.ok(trap.sideTransitions >= 2, JSON.stringify(trap));
  const result = Engine.analyzeTimeframe(rows, { interval: "15m", now: 1_800_000_000_000 });
  assert.equal(result.signals.some((item) => item.index === rows.length - 1), false, JSON.stringify(result.signals));
  assert.ok(result.rejected.some((item) => (
    item.index === rows.length - 1
    && item.reasons.includes("前高来自上升通道末端，不作为起爆突破")
  )), JSON.stringify(result.rejected.slice(-8)));
});

test("recognizes a rising wedge envelope and vetoes its terminal breakout", () => {
  const rows = ascendingChannelBreakoutSeries({ wedge: true });
  const atrValues = Engine.atr(rows);
  const trap = Engine.detectAscendingChannelTrap(rows, rows.length - 1, atrValues.at(-2));
  assert.ok(trap, "the occupied converging rising envelope should be recognized");
  assert.equal(trap.shape, "rising-wedge");
  const result = Engine.analyzeTimeframe(rows, { interval: "15m", now: 1_800_000_000_000 });
  assert.equal(result.signals.some((item) => item.index === rows.length - 1), false, JSON.stringify(result.signals));
  assert.ok(result.rejected.some((item) => (
    item.index === rows.length - 1
    && item.reasons.includes("前高来自上升楔形末端，不作为起爆突破")
  )), JSON.stringify(result.rejected.slice(-8)));
});

test("a true post-impulse horizontal launch is not confused with an ascending channel", () => {
  const rows = horizontalBreakoutSeries();
  const atrValues = Engine.atr(rows);
  assert.equal(Engine.detectAscendingChannelTrap(rows, rows.length - 1, atrValues.at(-2)), null);
  const result = Engine.analyzeTimeframe(rows, { interval: "5m", now: 1_800_000_000_000 });
  assert.ok(result.signals.some((item) => item.index === rows.length - 1 && item.pattern.includes("横盘起飞")));
});

test("BASED 2026-08-16 5m structure observation recognizes the occupied rising wedge before an upper-rail test", () => {
  const targetIndex = basedFiveMinuteRisingWedgeRows.length - 1;
  const atrValues = Engine.atr(basedFiveMinuteRisingWedgeRows, 14);
  const strictTriggerTrap = Engine.detectAscendingChannelTrap(
    basedFiveMinuteRisingWedgeRows,
    targetIndex,
    atrValues[targetIndex],
  );
  const observationTrap = Engine.detectAscendingChannelTrap(
    basedFiveMinuteRisingWedgeRows,
    targetIndex,
    atrValues[targetIndex],
    24,
    120,
    { requireUpperTest: false },
  );
  assert.equal(strictTriggerTrap, null, "the last candle is not testing the upper rail yet");
  assert.ok(observationTrap, "the completed envelope should still be recognized during structure observation");
  assert.equal(observationTrap.shape, "rising-wedge");
  assert.ok(observationTrap.lowSlope > observationTrap.highSlope, JSON.stringify(observationTrap));
  assert.ok(observationTrap.interiorOccupancy >= 0.7, JSON.stringify(observationTrap));

  const result = Engine.analyzeTimeframe(basedFiveMinuteRisingWedgeRows, {
    interval: "5m",
    now: basedFiveMinuteRisingWedgeRows.at(-1).closeTime + 1,
    preselectedLeader: true,
  });
  assert.equal(result.structures.some((item) => (
    item.endIndex >= targetIndex - 2
    && /横盘起飞|三角|突破前高/.test(item.pattern)
  )), false, JSON.stringify(result.structures.slice(-4), null, 2));
});

test("does not call a large-selloff repair platform a horizontal launch", () => {
  const rows = postSelloffPlatformBreakoutSeries();
  const result = Engine.analyzeTimeframe(rows, { interval: "5m", now: 1_800_000_000_000 });
  assert.equal(result.signals.some((item) => item.index === rows.length - 1), false, JSON.stringify(result.signals));
  const rejected = result.rejected.find((item) => (
    item.index === rows.length - 1
    && item.reasons.includes("平台直接承接快速大下杀且尚未收复，不属于先拉升后盘整的横盘起飞")
  ));
  assert.ok(rejected, JSON.stringify(result.rejected.slice(-10)));
  assert.equal(rejected.horizontalLaunchPostSelloffRecovery, true);
});

test("ascending-channel and launch-context vetoes are unchanged by future candles", () => {
  const prefixRows = ascendingChannelBreakoutSeries();
  const decisionTime = prefixRows.at(-1).time;
  const fullRows = [...prefixRows];
  for (let step = 0; step < 8; step += 1) {
    fullRows.push(candle(fullRows.length, {
      open: 116 - step * 0.6,
      close: 115.5 - step * 0.65,
      high: 116.4 - step * 0.58,
      low: 115.1 - step * 0.7,
      volume: 150,
    }));
  }
  const prefix = Engine.analyzeTimeframe(prefixRows, { interval: "15m", now: 1_800_000_000_000 });
  const full = Engine.analyzeTimeframe(fullRows, { interval: "15m", now: 1_800_000_000_000 });
  const left = prefix.rejected.find((item) => item.time === decisionTime && item.riskStructureShape === "rising-channel");
  const right = full.rejected.find((item) => item.time === decisionTime && item.riskStructureShape === "rising-channel");
  assert.ok(left && right);
  assert.deepEqual(
    { level: left.level, shape: left.riskStructureShape, start: left.riskStructureStartIndex, bars: left.riskStructureBars },
    { level: right.level, shape: right.riskStructureShape, start: right.riskStructureStartIndex, bars: right.riskStructureBars },
  );
});

test("keeps a broad SPK-style 15-minute mother-platform outer-edge breakout", () => {
  const rows = broadLeaderPlatformBreakoutSeries();
  const result = Engine.analyzeTimeframe(rows, { interval: "15m", now: 1_800_000_000_000 });
  const signal = result.signals.find((item) => item.index === rows.length - 1 && item.outerEdgeConfirmed);
  assert.ok(signal, JSON.stringify({ signals: result.signals.slice(-6), rejected: result.rejected.slice(-6) }));
  assert.match(signal.pattern, /横盘起飞/);
  assert.equal(signal.level, 106.2);
  assert.ok(signal.consolidationBars >= 30);
  assert.ok(signal.outerEdgeScore >= 62);
  assert.ok(signal.launchDistancePercent <= 7);
});

test("a causal mother-platform buy is unchanged when later bars are appended", () => {
  const prefixRows = broadLeaderPlatformBreakoutSeries();
  const fullRows = broadLeaderPlatformBreakoutSeries();
  for (let step = 0; step < 12; step += 1) {
    fullRows.push(candle(fullRows.length, {
      open: 108.3 + Math.sin(step) * 0.8,
      close: 108.1 + Math.cos(step) * 0.9,
      high: 109.4,
      low: 106.9,
      volume: 120,
    }));
  }
  const prefix = Engine.analyzeTimeframe(prefixRows, { interval: "15m", now: 1_800_000_000_000 });
  const full = Engine.analyzeTimeframe(fullRows, { interval: "15m", now: 1_800_000_000_000 });
  const decisionTime = prefixRows.at(-1).time;
  const left = prefix.signals.find((item) => item.time === decisionTime && item.outerEdgeConfirmed);
  const right = full.signals.find((item) => item.time === decisionTime && item.outerEdgeConfirmed);
  assert.ok(left && right);
  assert.deepEqual(
    { level: left.level, triggerPrice: left.triggerPrice, outerEdgeScore: left.outerEdgeScore, consolidationBars: left.consolidationBars },
    { level: right.level, triggerPrice: right.triggerPrice, outerEdgeScore: right.outerEdgeScore, consolidationBars: right.consolidationBars },
  );
});

test("H 2025-07-02 10:45 Beijing 5m horizontal launch survives leader-only context", () => {
  const rows = hFiveMinuteLaunchRows.map(([time, open, high, low, close, volume, quoteVolume, takerBuyVolume, tradeCount]) => ({
    time,
    closeTime: time + 5 * 60_000 - 1,
    open,
    high,
    low,
    close,
    volume,
    quoteVolume,
    takerBuyVolume,
    tradeCount,
  }));
  const decisionTime = Date.parse("2025-07-02T02:45:00Z");
  const prefixRows = rows.filter((row) => row.time <= decisionTime);
  const prefix = Engine.analyzeTimeframe(prefixRows, { interval: "5m", now: decisionTime + 5 * 60_000 });
  const full = Engine.analyzeTimeframe(rows, { interval: "5m", now: rows.at(-1).closeTime + 1 });
  const left = prefix.signals.find((item) => item.time === decisionTime && item.outerEdgeConfirmed);
  const right = full.signals.find((item) => item.time === decisionTime && item.outerEdgeConfirmed);
  assert.ok(left, JSON.stringify(prefix.rejected.slice(-8)));
  assert.ok(right, JSON.stringify(full.rejected.slice(-8)));
  assert.match(left.pattern, /横盘起飞/);
  assert.match(left.pattern, /突破前高/);
  assert.ok(left.foundationTypes.includes("base"));
  assert.ok(left.auxiliaryTypes.includes("previousHigh"));
  assert.ok(left.consolidationBars >= 38);
  assert.ok(left.outerEdgeScore >= 80);
  assert.ok(prefixRows.at(-1).open < left.triggerPrice);
  assert.ok(prefixRows.at(-1).high >= left.triggerPrice);
  assert.deepEqual(
    {
      level: left.level,
      triggerPrice: left.triggerPrice,
      consolidationBars: left.consolidationBars,
      outerEdgeScore: left.outerEdgeScore,
    },
    {
      level: right.level,
      triggerPrice: right.triggerPrice,
      consolidationBars: right.consolidationBars,
      outerEdgeScore: right.outerEdgeScore,
    },
    "future candles must not alter the 10:45 decision",
  );

  const frame = (interval, bullish) => {
    const intervalMs = { "15m": 15 * 60_000, "1h": 60 * 60_000, "4h": 4 * 60 * 60_000, "1d": 24 * 60 * 60_000 }[interval];
    const candles = Array.from({ length: 20 }, (_, index) => {
      const time = decisionTime - (20 - index) * intervalMs;
      const close = bullish ? 118 + index * 0.1 : 82 - index * 0.1;
      return { time, closeTime: time + intervalMs - 1, open: close - 0.1, high: close + 0.4, low: close - 0.5, close };
    });
    return {
      interval,
      candles,
      indicators: {
        ema90: candles.map((_, index) => bullish ? 100 + index * 0.05 : 100 - index * 0.01),
        atr: candles.map(() => 1),
      },
      signals: [], pending: [], rejected: [], structures: [],
      regime: { bullish, strong: bullish, label: bullish ? "主升环境" : "禁止追多" },
      stats: { signalCount: 0, pendingCount: 0, rejectedCount: 0, lastPrice: candles.at(-1).close },
    };
  };
  const [gated] = Engine.applyContextGates(
    [prefix, frame("15m", true), frame("1h", true), frame("4h", true), frame("1d", true)],
    [frame("1h", false), frame("4h", false)],
  );
  const displayed = gated.signals.find((item) => item.time === decisionTime && item.outerEdgeConfirmed);
  assert.ok(displayed, JSON.stringify(gated.rejected.filter((item) => item.time === decisionTime)));
  assert.equal(displayed.marketEmotion, "BTC 背景逆风 0/2 · 龙头独立判断");
});

test("does not emit the disabled W-continuation pattern", () => {
  const result = Engine.analyzeTimeframe(wContinuationSeries(), { interval: "15m", now: 1_800_000_000_000 });
  assert.ok(result.signals.every((signal) => !signal.pattern.includes("W中继")), JSON.stringify(result.signals));
});

test("detects a previous-high breakout after a real pause", () => {
  const rows = previousHighBreakSeries();
  const lastHigh = rows[49].high;
  for (let index = 50; index < 60; index += 1) {
    rows[index] = candle(index, {
      open: lastHigh - 0.55 + (index % 2) * 0.08,
      close: lastHigh - 0.45 + (index % 3) * 0.05,
      high: lastHigh - 0.12 + (index % 2) * 0.04,
      low: lastHigh - 0.95,
      volume: 84,
    });
  }
  rows[60] = candle(60, { open: lastHigh - 0.2, close: lastHigh + 1.2, high: lastHigh + 1.5, low: lastHigh - 0.3, volume: 245 });
  const result = Engine.analyzeTimeframe(rows, { interval: "1h", now: 1_800_000_000_000 });
  assert.ok(result.signals.some((signal) => signal.pattern.includes("突破前高")), JSON.stringify(result.signals));
  assert.ok(result.signals.every((signal) => signal.foundationTypes.length > 0 || signal.hasPivot));
});

test("a previous-high cross without a mother structure remains filtered", () => {
  const result = Engine.analyzeTimeframe(previousHighBreakSeries(), { interval: "1h", now: 1_800_000_000_000 });
  assert.equal(result.signals.length, 0);
  assert.ok(result.rejected.some((item) => (
    item.pattern.includes("突破前高")
    && item.reasons.some((reason) => reason.includes("仅作辅助"))
  )));
});

test("does not buy a prior high crossed by one candle launched from too far below", () => {
  const rows = horizontalBreakoutSeries();
  rows[rows.length - 1] = candle(rows.length - 1, {
    open: 92,
    close: 102.2,
    high: 102.6,
    low: 91.8,
    volume: 280,
  });
  const raw = Engine.analyzeTimeframe(rows, { interval: "5m", now: 1_800_000_000_000 });
  const context = (interval) => ({
    interval,
    candles: [{ time: rows.at(-1).time - 1, closeTime: rows.at(-1).time - 1, open: 110, high: 121, low: 109, close: 120 }],
    indicators: { ema90: [100], atr: [1] },
    signals: [], pending: [], rejected: [],
    regime: { bullish: true, strong: true, label: "主升环境" },
    stats: { signalCount: 0, pendingCount: 0, rejectedCount: 0, lastPrice: 120 },
  });
  const [result] = Engine.applyContextGates([raw, context("1h"), context("4h")]);
  assert.ok(result.signals.every((item) => item.index !== rows.length - 1), JSON.stringify(result.signals));
  assert.ok(result.rejected.some((item) => (
    item.index === rows.length - 1
    && item.reasons.includes("突破K首次触发前低点到前高的涨幅超过 7%，不做这次突破前高")
  )), JSON.stringify(result.rejected.slice(-6)));
});

test("uses only the breakout low formed before the first cross for the seven-percent veto", () => {
  const rows = horizontalBreakoutSeries();
  const raw = Engine.analyzeTimeframe(rows, { interval: "5m", now: 1_800_000_000_000 });
  const time = rows.at(-1).time;
  const childCandles = [
    candle(0, { time, closeTime: time + 59_999, open: 100.3, high: 100.5, low: 90, close: 99.8 }),
    candle(1, { time: time + 60_000, closeTime: time + 119_999, open: 99.8, high: 101.2, low: 99.7, close: 101 }),
    candle(2, { time: time + 120_000, closeTime: time + 179_999, open: 101, high: 101.3, low: 50, close: 100.9 }),
  ];
  const emptyFrame = (interval, candles) => ({
    interval,
    candles,
    indicators: { ema90: candles.map(() => 100), atr: candles.map(() => 1) },
    signals: [], pending: [], rejected: [],
    regime: { bullish: true, strong: true, label: "主升环境" },
    stats: { signalCount: 0, pendingCount: 0, rejectedCount: 0, lastPrice: candles.at(-1).close },
  });
  const contextCandle = [{ time: time - 1, closeTime: time - 1, open: 110, high: 121, low: 109, close: 120 }];
  const [gated] = Engine.applyContextGates([
    raw,
    emptyFrame("1m", childCandles),
    emptyFrame("1h", contextCandle),
    emptyFrame("4h", contextCandle),
  ]);
  const rejected = gated.rejected.find((item) => item.index === rows.length - 1 && item.pattern.includes("突破前高"));
  assert.ok(rejected, JSON.stringify(gated));
  assert.equal(rejected.breakoutLowBeforeTrigger, 90);
  assert.equal(rejected.breakoutLowSource, "1m");
  assert.ok(rejected.riseFromBreakoutLowPercent > 7);
  assert.ok(rejected.reasons.includes("突破K首次触发前低点到前高的涨幅超过 7%，不做这次突破前高"));
  assert.notEqual(rejected.breakoutLowBeforeTrigger, 50, "首次上穿后的未来低点不得参与过滤");
});

test("detects a converging triangle breakout", () => {
  const result = Engine.analyzeTimeframe(triangleBreakoutSeries(), { interval: "5m", now: 1_800_000_000_000 });
  assert.ok(result.signals.some((signal) => signal.pattern.includes("三角突破")), JSON.stringify({ signals: result.signals, pending: result.pending, rejected: result.rejected }));
});

test("PI one-hour long consolidation buys the same-bar triangle, pivot and prior-high breakout", () => {
  const rows = piOneHourMultiBoundaryBreakoutSeries();
  const targetTime = Date.parse("2025-02-26T02:00:00Z");
  const raw = Engine.analyzeTimeframe(rows, {
    interval: "1h",
    now: Date.parse("2025-02-26T04:00:00Z"),
  });
  const bullishContext = (interval) => ({
    interval,
    candles: [{ time: targetTime - 3_600_000, closeTime: targetTime - 1, close: 110, low: 109 }],
    indicators: { ema90: [100], atr: [1] },
    signals: [], pending: [], rejected: [],
    regime: { bullish: true, strong: true, label: "主升环境" },
    stats: { signalCount: 0, pendingCount: 0, rejectedCount: 0, lastPrice: 110 },
  });
  const gated = Engine.applyContextGates([
    raw,
    bullishContext("4h"),
    bullishContext("1d"),
  ]).find((item) => item.interval === "1h");
  const buy = gated.signals.find((item) => item.time === targetTime);
  assert.ok(buy, JSON.stringify({ rawSignals: raw.signals, signals: gated.signals, rejected: gated.rejected.filter((item) => item.time === targetTime) }, null, 2));
  assert.ok(buy.foundationTypes.includes("triangle"), JSON.stringify(buy));
  assert.ok(buy.auxiliaryTypes.includes("previousHigh"), JSON.stringify(buy));
  assert.equal(buy.hasPivot, true, JSON.stringify(buy));
  assert.ok(buy.consolidationBars >= 36, JSON.stringify(buy));
});

test("PI-style triangle cannot use the outer-edge A+ path when the true prior high is over seven percent away", () => {
  const rows = piOneHourMultiBoundaryBreakoutSeries();
  rows[rows.length - 1] = {
    ...rows.at(-1),
    open: 98.5,
    low: 98.3,
  };
  const result = Engine.analyzeTimeframe(rows, {
    interval: "1h",
    now: Date.parse("2025-02-26T04:00:00Z"),
  });
  const targetTime = Date.parse("2025-02-26T02:00:00Z");
  const targetItems = [...result.signals, ...result.rejected]
    .filter((item) => item.time === targetTime);
  assert.equal(targetItems.some((item) => item.matureTriangleOuterEdge), false, JSON.stringify(targetItems));
});

test("PI one-hour A+ decision is unchanged when later candles are appended", () => {
  const rows = piOneHourMultiBoundaryBreakoutSeries();
  const targetTime = Date.parse("2025-02-26T02:00:00Z");
  const left = Engine.analyzeTimeframe(rows, {
    interval: "1h",
    now: Date.parse("2025-02-26T04:00:00Z"),
  }).signals.find((item) => item.time === targetTime && item.matureTriangleOuterEdge);
  const extended = rows.concat([
    candle(rows.length, { time: targetTime + 3_600_000, closeTime: targetTime + 7_199_999, open: 108.7, high: 110, low: 107.9, close: 109.4 }),
    candle(rows.length + 1, { time: targetTime + 7_200_000, closeTime: targetTime + 10_799_999, open: 109.4, high: 109.8, low: 106.2, close: 106.8 }),
  ]);
  const right = Engine.analyzeTimeframe(extended, {
    interval: "1h",
    now: Date.parse("2025-02-26T08:00:00Z"),
  }).signals.find((item) => item.time === targetTime && item.matureTriangleOuterEdge);
  const decision = (item) => ({
    time: item.time,
    triggerPrice: item.triggerPrice,
    confluence: item.confluence,
    structuralEvidenceScore: item.structuralEvidenceScore,
    score: item.score,
    certaintyScore: item.certaintyScore,
  });
  assert.deepEqual(decision(left), decision(right));
});

test("PI one-hour mature platform triggers at the pre-armed boundary without a hidden ATR delay", () => {
  const rows = piOneHourMultiBoundaryBreakoutSeries();
  const targetTime = Date.parse("2025-02-26T02:00:00Z");
  const exactBoundary = Math.max(...rows.slice(0, -1).map((row) => row.high));
  rows[rows.length - 1] = {
    ...rows.at(-1),
    high: exactBoundary,
    close: exactBoundary - 0.04,
  };
  const result = Engine.analyzeTimeframe(rows, {
    interval: "1h",
    now: Date.parse("2025-02-26T04:00:00Z"),
  });
  const buy = result.signals.find((item) => item.time === targetTime);
  assert.ok(buy, JSON.stringify({ signals: result.signals, rejected: result.rejected.filter((item) => item.time === targetTime) }, null, 2));
  assert.equal(buy.directStructuralBoundary, true);
  assert.ok(Math.abs(buy.triggerPrice - buy.level) < 1e-10, JSON.stringify(buy));
  assert.ok(Number.isFinite(buy.structuralEvidenceScore));
});

test("PI 2025-02-22 20:17 one-minute post-impulse box is a canonical horizontal-launch buy", () => {
  const rows = piOneMinutePostImpulseHorizontalLaunchSeries();
  const targetTime = Date.parse("2025-02-22T12:17:00Z");
  const raw = Engine.analyzeTimeframe(rows, {
    interval: "1m",
    now: targetTime + 2 * 60_000,
  });
  const [gated] = Engine.applyContextGates([raw], [], { preselectedLeader: true });
  const displayed = Engine.enforceIntervalStructurePolicy(gated);
  const buy = displayed.signals.find((item) => item.time === targetTime);
  assert.ok(buy, JSON.stringify({ rawSignals: raw.signals, rawRejected: raw.rejected.slice(-8), gatedRejected: gated.rejected.slice(-8) }, null, 2));
  assert.ok(buy.certaintyScore >= 88);
  assert.equal(buy.pattern, "横盘起飞 + 突破前高");
  assert.deepEqual(buy.foundationTypes, ["base"]);
  assert.equal(buy.hasPivot, false);
  assert.equal(buy.structureShape, null);
});

test("a preselected leader keeps its strict one-minute horizontal launch even before higher frames align", () => {
  const rows = piOneMinutePostImpulseHorizontalLaunchSeries();
  const targetTime = Date.parse("2025-02-22T12:17:00Z");
  const raw = Engine.analyzeTimeframe(rows, {
    interval: "1m",
    now: targetTime + 2 * 60_000,
  });
  const bearishContext = (interval) => ({
    interval,
    candles: [{ time: targetTime - 60_000, closeTime: targetTime - 1, close: 90, low: 89 }],
    indicators: { ema90: [100], atr: [1] },
    signals: [], pending: [], rejected: [],
    regime: { bullish: false, strong: false, label: "尚未转强" },
    stats: { signalCount: 0, pendingCount: 0, rejectedCount: 0, lastPrice: 90 },
  });
  const gated = Engine.applyContextGates([
    raw,
    bearishContext("1h"),
    bearishContext("4h"),
  ], [], { preselectedLeader: true }).find((item) => item.interval === "1m");
  const displayed = Engine.enforceIntervalStructurePolicy(gated);
  const buy = displayed.signals.find((item) => item.time === targetTime);
  assert.ok(buy, JSON.stringify(gated.rejected.filter((item) => item.time === targetTime), null, 2));
  assert.equal(buy.pattern, "横盘起飞 + 突破前高");
});

test("PI one-minute horizontal launch stays causal when later candles are appended", () => {
  const rows = piOneMinutePostImpulseHorizontalLaunchSeries();
  const targetTime = Date.parse("2025-02-22T12:17:00Z");
  const analyze = (input, now) => {
    const raw = Engine.analyzeTimeframe(input, { interval: "1m", now });
    const [gated] = Engine.applyContextGates([raw], [], { preselectedLeader: true });
    return Engine.enforceIntervalStructurePolicy(gated).signals.find((item) => item.time === targetTime);
  };
  const left = analyze(rows, targetTime + 2 * 60_000);
  const extended = rows.concat([
    candle(rows.length, { time: targetTime + 60_000, closeTime: targetTime + 119_999, open: 1.0038, high: 1.02, low: 1.001, close: 1.016 }),
    candle(rows.length + 1, { time: targetTime + 120_000, closeTime: targetTime + 179_999, open: 1.016, high: 1.018, low: 0.998, close: 1.002 }),
  ]);
  const right = analyze(extended, targetTime + 4 * 60_000);
  const decision = (item) => ({
    time: item?.time,
    triggerPrice: item?.triggerPrice,
    pattern: item?.pattern,
    structuralEvidenceScore: item?.structuralEvidenceScore,
  });
  assert.ok(left && right, JSON.stringify({ left, right }));
  assert.deepEqual(decision(left), decision(right));
});

test("PI 2025-02-23 05:34 one-minute local fluctuation is rejected inside the larger disorderly mother box", () => {
  const rows = piOneMinuteMotherBoxNoiseSeries();
  const targetTime = Date.parse("2025-02-22T21:34:00Z");
  const result = Engine.analyzeTimeframe(rows, {
    interval: "1m",
    now: targetTime + 2 * 60_000,
  });
  const targetItems = result.rejected.filter((item) => item.time === targetTime);
  assert.ok(targetItems.length, JSON.stringify({ signals: result.signals.slice(-5), rejected: result.rejected.slice(-8) }, null, 2));
  assert.ok(targetItems.some((item) => item.reasons.some((reason) => (
    reason.includes("母箱体内部无序波动") || reason.includes("母压力区间内部")
  ))), JSON.stringify(targetItems, null, 2));
  assert.equal(result.signals.some((item) => item.time === targetTime), false);
});

test("mother-structure perspective rejects local fragments on every timeframe but keeps a true outer edge", () => {
  const rows = unorderedMotherBoxSeries();
  // 单根异常上下影线不能把母区间撑到失真，边界必须由重复交易区域决定。
  rows[2] = { ...rows[2], high: 180, low: 60 };
  const index = rows.length;
  ["1m", "5m", "15m", "1h", "4h", "1d"].forEach((interval) => {
    const local = Engine.assessMotherStructureNoise(rows, index, 109, 1, {
      interval,
      consolidationBars: 32,
    });
    const outer = Engine.assessMotherStructureNoise(rows, index, 119.4, 1, {
      interval,
      consolidationBars: 32,
    });
    assert.equal(local.risky, true, `${interval}: ${JSON.stringify(local)}`);
    assert.equal(outer.risky, false, `${interval}: ${JSON.stringify(outer)}`);
  });
});

test("mother-structure judgement is unchanged when future candles are appended", () => {
  const rows = unorderedMotherBoxSeries();
  const index = rows.length;
  const left = Engine.assessMotherStructureNoise(rows, index, 109, 1, {
    interval: "15m",
    consolidationBars: 32,
  });
  const extended = rows.concat(unorderedMotherBoxSeries(30).map((row, offset) => ({
    ...row,
    time: rows.at(-1).time + (offset + 1) * 60_000,
    closeTime: rows.at(-1).closeTime + (offset + 1) * 60_000,
    open: 135,
    high: 138,
    low: 132,
    close: 136,
  })));
  const right = Engine.assessMotherStructureNoise(extended, index, 109, 1, {
    interval: "15m",
    consolidationBars: 32,
  });
  assert.deepEqual(right, left);
});

test("PI-style 15-minute B points after a parabolic peak stay blocked until the true mother high breaks", () => {
  const rows = postImpulseHighLevelRotationSeries();
  const inside = Engine.assessMotherStructureNoise(rows, rows.length, 148, 2, {
    interval: "15m",
    consolidationBars: 28,
  });
  const outer = Engine.assessMotherStructureNoise(rows, rows.length, 159, 2, {
    interval: "15m",
    consolidationBars: 28,
  });
  assert.equal(inside.risky, true, JSON.stringify(inside));
  assert.equal(inside.mode, "post-impulse-high-level-rotation", JSON.stringify(inside));
  assert.equal(outer.risky, false, JSON.stringify(outer));
});

test("PI 2025-02-27 15m shock-defined mother box blocks every internal rebound breakout regardless of local span", () => {
  const rows = [];
  for (let step = 0; step < 120; step += 1) {
    const close = 1.2 + step * 0.0142;
    rows.push(candle(rows.length, {
      open: close - 0.006,
      close,
      high: close + 0.018,
      low: close - 0.02,
      volume: 120 + step,
    }));
  }
  const shockIndex = rows.length;
  // 用户标注的母箱体：00:30 H 3.0182，00:45 L 2.2415。
  rows.push(candle(rows.length, {
    open: 2.9432,
    high: 3.0182,
    low: 2.3794,
    close: 2.5372,
    volume: 427_820,
  }));
  rows.push(candle(rows.length, {
    open: 2.5395,
    high: 2.5553,
    low: 2.2415,
    close: 2.4497,
    volume: 402_360,
  }));
  for (let step = 0; step < 28; step += 1) {
    const close = 2.46 + step * 0.013 + Math.sin(step * 0.82) * 0.055;
    rows.push(candle(rows.length, {
      open: close + (step % 2 ? -0.01 : 0.012),
      close,
      high: close + 0.035,
      low: close - 0.04,
      volume: 150 - step,
    }));
  }
  const internal = Engine.assessMotherStructureNoise(rows, rows.length, 2.82, 0.16, {
    interval: "15m",
    // 故意报成很长的局部盘整，验证不能借根数绕过母箱体大局观。
    consolidationBars: 92,
  });
  const outer = Engine.assessMotherStructureNoise(rows, rows.length, 3.0182, 0.16, {
    interval: "15m",
    consolidationBars: 92,
  });
  assert.equal(internal.risky, true, JSON.stringify(internal));
  assert.equal(internal.mode, "shock-formed-mother-box", JSON.stringify(internal));
  assert.ok(internal.shockDropPercent >= 7, JSON.stringify(internal));
  assert.ok(internal.shockSelloffBars >= 2, JSON.stringify(internal));
  assert.ok(internal.shockBearishDominance >= 0.72, JSON.stringify(internal));
  assert.equal(outer.risky, false, JSON.stringify(outer));

  const result = Engine.analyzeTimeframe(rows, {
    interval: "15m",
    now: rows.at(-1).closeTime + 1,
  });
  assert.equal(result.signals.some((item) => item.index > shockIndex && item.triggerPrice < 3.0182), false,
    JSON.stringify(result.signals.filter((item) => item.index > shockIndex), null, 2));
  assert.ok(result.rejected.some((item) => (
    item.index > shockIndex
    && item.motherStructureMode === "shock-formed-mother-box"
    && item.reasons.some((reason) => reason.includes("急杀形成的母箱体内部"))
  )), JSON.stringify(result.rejected.slice(-12), null, 2));

  for (const mainWaveStage of ["active", "expected"]) {
    const declared = Engine.analyzeTimeframe(rows, {
      interval: "15m",
      now: rows.at(-1).closeTime + 1,
      mainWaveStage,
    });
    assert.equal(declared.signals.some((item) => item.index > shockIndex && item.triggerPrice < 3.0182), false,
      `${mainWaveStage} must not disable a post-wave shock mother box`);
    assert.ok(declared.rejected.some((item) => (
      item.index > shockIndex
      && item.motherStructureMode === "shock-formed-mother-box"
      && item.reasons.some((reason) => reason.includes("急杀形成的母箱体内部"))
    )), JSON.stringify(declared.rejected.slice(-12), null, 2));
  }
});

test("TRB-style shock box allows only an independent A+ 15m horizontal launch after a clean rebound", () => {
  const rows = [];
  for (let step = 0; step < 120; step += 1) {
    const close = 24 + step * 0.175;
    rows.push(candle(rows.length, {
      open: close - 0.08,
      close,
      high: close + 0.16,
      low: close - 0.18,
      volume: 130 + step,
    }));
  }
  const shockIndex = rows.length;
  rows.push(candle(rows.length, {
    open: 44.9,
    high: 45.197,
    low: 34.2,
    close: 35,
    volume: 1_800,
  }));
  rows.push(candle(rows.length, {
    open: 35,
    high: 35.4,
    low: 24.05,
    close: 25.2,
    volume: 2_200,
  }));
  for (let step = 0; step < 48; step += 1) {
    const close = 25.3 + step * 0.19 + Math.sin(step * 0.8) * 0.12;
    rows.push(candle(rows.length, {
      open: close - 0.04,
      close,
      high: close + 0.13,
      low: close - 0.14,
      volume: 155 - step,
    }));
  }
  for (let step = 0; step < 42; step += 1) {
    const close = 34.42 + Math.sin(step * 0.93) * 0.12 + step * 0.0015;
    rows.push(candle(rows.length, {
      open: close + (step % 2 ? -0.025 : 0.02),
      close,
      high: step % 9 === 0 ? 34.78 : close + 0.11,
      low: close - 0.13,
      volume: 112 - Math.floor(step / 5),
    }));
  }
  rows.push(candle(rows.length, {
    open: 34.58,
    high: 35.35,
    low: 34.5,
    close: 35.18,
    volume: 310,
  }));

  const result = Engine.analyzeTimeframe(rows, {
    interval: "15m",
    now: rows.at(-1).closeTime + 1,
  });
  const signal = result.signals.find((item) => item.index === rows.length - 1);
  assert.ok(signal, JSON.stringify(result.rejected.slice(-8), null, 2));
  assert.equal(signal.shockBoxHorizontalLaunchException, true, JSON.stringify(signal, null, 2));
  assert.ok(signal.certaintyScore >= 88);
  assert.ok(signal.certaintyScore >= 88);
  assert.ok(signal.foundationTypes.includes("base"));
  assert.ok(signal.foundationTypes.every((type) => ["base", "relaunch"].includes(type)));
  assert.ok(signal.auxiliaryTypes.every((type) => type === "previousHigh"));
  assert.equal(signal.structureShape, null);
  assert.ok(signal.motherShockLowIndex >= shockIndex);
  assert.ok(signal.triggerPrice < 45.197, "the exception is intentionally still inside the true mother box");
  assert.ok(signal.evidence.some((item) => item.includes("A+急杀母箱体内横盘起飞")), JSON.stringify(signal.evidence));

  const withFuture = rows.concat(Array.from({ length: 10 }, (_, step) => candle(rows.length + step, {
    open: 35.2 + step * 0.45,
    close: 35.55 + step * 0.45,
    high: 35.75 + step * 0.45,
    low: 35.05 + step * 0.45,
    volume: 280,
  })));
  const replay = Engine.analyzeTimeframe(withFuture, {
    interval: "15m",
    now: withFuture.at(-1).closeTime + 1,
  });
  const replaySignal = replay.signals.find((item) => item.index === rows.length - 1);
  assert.equal(replaySignal?.shockBoxHorizontalLaunchException, true);
  assert.ok((replaySignal?.certaintyScore || 0) >= 88);
  assert.equal(replaySignal?.triggerPrice, signal.triggerPrice);
});

test("PI 2025-02-22 15:30 Beijing 5m keeps the causal main-wave ascending-triangle ignition as A+", () => {
  const targetTime = Date.UTC(2025, 1, 22, 7, 30);
  const rows = Data.parseRows("okx", piFiveMinuteTriangleRows, 5 * 60_000);
  const result = Engine.analyzeTimeframe(rows, {
    interval: "5m",
    now: rows.at(-1).closeTime + 1,
    leaderSelected: true,
  });
  const signal = result.signals.find((item) => item.time === targetTime);

  assert.ok(signal, JSON.stringify(result.rejected.filter((item) => item.time === targetTime), null, 2));
  assert.equal(signal.status, "buy");
  assert.ok(signal.certaintyScore >= 88);
  assert.ok(signal.score >= 88);
  assert.ok(signal.certaintyScore >= 88);
  assert.equal(signal.structureShape, "ascending-triangle");
  assert.equal(signal.shockBoxAscendingTriangleException, true);
  assert.equal(signal.riskStructureShape, null);
  assert.equal(signal.mainWaveStage, "active");
  assert.deepEqual(signal.foundationTypes, ["base", "triangle", "relaunch"]);
  assert.deepEqual(signal.auxiliaryTypes, ["previousHigh"]);
  assert.equal(signal.hasPivot, true);
  assert.ok(Math.abs(signal.triggerPrice - 0.7782) < 1e-10);
  assert.match(signal.pattern, /横盘起飞/);
  assert.match(signal.pattern, /三角突破/);
  assert.match(signal.pattern, /突破前高/);
  assert.deepEqual(signal.reasons, []);
  assert.ok(signal.evidence.some((item) => item.includes("A+主升情绪启动")), JSON.stringify(signal.evidence));

  const strictContext = Engine.applyContextGates([result], [], { preselectedLeader: true })[0];
  const startupSignal = strictContext.signals.find((item) => item.time === targetTime);
  assert.equal(startupSignal?.preHigherFrameMainWaveIgnitionPermit, true);
  assert.equal(startupSignal?.mainWaveContextSource, "leader-main-wave-ignition");
  assert.ok(startupSignal?.evidence.some((item) => item.includes("主升启动不反向要求大周期")));
  const humanExpectedContext = Engine.applyContextGates([result], [], {
    preselectedLeader: true,
    mainWaveStage: "expected",
  })[0];
  const manuallyPermitted = humanExpectedContext.signals.find((item) => item.time === targetTime);
  assert.equal(manuallyPermitted?.mainWaveStage, "expected");
  assert.equal(manuallyPermitted?.mainWaveContextSource, "manual-analysis");
  assert.ok(manuallyPermitted?.evidence.some((item) => item.includes("人工给出主升浪预期")));
  const livePersonalXContext = Engine.applyContextGates([result], [], {
    preselectedLeader: true,
    mainWaveStage: "active",
    mainWaveContextSource: "live-personal-x",
    mainWaveContextLabel: "临盘加速段",
  })[0];
  const livePersonalXSignal = livePersonalXContext.signals.find((item) => item.time === targetTime);
  assert.equal(livePersonalXSignal?.mainWaveStage, "active");
  assert.equal(livePersonalXSignal?.mainWaveContextSource, "live-personal-x");
  assert.ok(livePersonalXSignal?.evidence.some((item) => item.includes("临盘加速段")));
  const automaticNewCoinContext = Engine.applyContextGates(
    [result, earlyNewCoinHigherFrames(targetTime)[0]],
    [],
    { preselectedLeader: true },
  ).find((item) => item.interval === "5m");
  const automaticNewCoinSignal = automaticNewCoinContext.signals.find((item) => item.time === targetTime);
  assert.equal(automaticNewCoinSignal?.newCoinNotFallingMainWavePermit, true);
  assert.equal(automaticNewCoinSignal?.mainWaveContextSource, "new-coin-not-falling");
  assert.ok(automaticNewCoinSignal?.evidence.some((item) => item.includes("新币不跌后的主升浪")));
  assert.ok(automaticNewCoinSignal?.evidence.some((item) => item.includes("4小时和日线不足不否决")));

  const withFuture = rows.concat(Array.from({ length: 8 }, (_, step) => candle(rows.length + step, {
    time: targetTime + (step + 1) * 5 * 60_000,
    closeTime: targetTime + (step + 2) * 5 * 60_000 - 1,
    open: 0.79 + step * 0.01,
    close: 0.795 + step * 0.01,
    high: 0.802 + step * 0.01,
    low: 0.785 + step * 0.01,
    volume: 2_000_000,
  })));
  const replay = Engine.analyzeTimeframe(withFuture, {
    interval: "5m",
    now: withFuture.at(-1).closeTime + 1,
    leaderSelected: true,
  });
  const replaySignal = replay.signals.find((item) => item.time === targetTime);
  assert.equal(replaySignal?.triggerPrice, signal.triggerPrice);
  assert.equal(replaySignal?.mainWaveStage, signal.mainWaveStage);
  assert.equal(replaySignal?.shockBoxAscendingTriangleException, true);
});

test("dual-layer retention keeps soft-gated consolidation breakouts without reviving structural noise", () => {
  const rows = Data.parseRows("okx", piFiveMinuteTriangleRows, 5 * 60_000);
  const result = Engine.analyzeTimeframe(rows, {
    interval: "5m",
    now: rows.at(-1).closeTime + 1,
    preselectedLeader: true,
  });
  const softTrendTime = Date.parse("2025-02-21T23:00:00Z");
  const duplicateCrossTime = Date.parse("2025-02-21T23:05:00Z");
  const rearmedCrossTime = Date.parse("2025-02-21T23:30:00Z");
  const missingAdvanceTime = Date.parse("2025-02-21T23:40:00Z");
  const rushedLiftTime = Date.parse("2025-02-22T00:35:00Z");
  const motherBoxTime = Date.parse("2025-02-22T04:25:00Z");
  const shockBoxTime = Date.parse("2025-02-22T06:50:00Z");
  const retained = result.retainedCandidates || [];
  const candidate = retained.find((item) => item.time === softTrendTime);

  assert.ok(candidate, "a mature true-boundary cross that only fails the weak-trend gate must be retained for review");
  assert.equal(candidate.status, "candidate");
  assert.equal(candidate.candidateTier, "retained");
  assert.equal(candidate.executionAllowed, false);
  assert.deepEqual(candidate.candidateReasons, ["突破前趋势仍弱"]);
  assert.ok(candidate.foundationTypes.includes("triangle"));
  assert.ok(candidate.auxiliaryTypes.includes("previousHigh"));
  assert.ok(candidate.featureCutoff < candidate.time);
  assert.equal(result.signals.some((item) => item.time === softTrendTime), false);
  assert.equal(retained.some((item) => item.time === duplicateCrossTime), false, "same-platform recross is noise before a real reset");
  assert.equal(retained.some((item) => item.time === rearmedCrossTime), true, "a stopped attempt may re-arm after price resets and rebuilds the edge");

  assert.equal(retained.some((item) => item.time === missingAdvanceTime), false, "missing prior advance is a hard veto");
  assert.equal(retained.some((item) => item.time === rushedLiftTime), false, "rushed stair-step lifting is a hard veto");
  assert.equal(retained.some((item) => item.time === motherBoxTime), false, "unordered mother-box interior noise is a hard veto");
  assert.equal(retained.some((item) => item.time === shockBoxTime), false, "shock-box interior noise is a hard veto");
  assert.equal(result.stats.retainedCandidateCount, retained.length);
});

test("execution hierarchy puts the mother-platform boundary before score and child labels", () => {
  const core = Engine.assessExecutionHierarchy({
    interval: "5m",
    foundationTypes: ["base"],
    auxiliaryTypes: ["previousHigh", "trendline"],
    consolidationBreakout: true,
    crossedLevel: true,
    openedBeyondTrigger: false,
    outerEdgeConfirmed: true,
    outerEdgeScore: 91,
    consolidationBars: 52,
    ceilingAge: 12,
    platformTouchGroups: 4,
    launchDistancePercent: 1.8,
    horizontalLaunchHasPriorAdvance: true,
    horizontalLaunchPostSelloffRecovery: false,
    horizontalLaunchUrgent: false,
    horizontalLaunchInsufficientEdgeDwell: false,
    directStructuralBoundary: true,
    previousHighLevel: 100,
    triggerPrice: 100,
    motherStructureNoise: false,
    highLevelDistribution: false,
    riskStructureShape: null,
    score: 99,
  });
  assert.equal(core.permit, true);
  assert.equal(core.tier, "core");
  assert.equal(core.primaryFoundation, "mother-platform-breakout");
  assert.deepEqual(core.childStructures, ["horizontal-launch"]);
  assert.deepEqual(core.boosters, ["previous-high", "trendline"]);

  const scoreOnly = Engine.assessExecutionHierarchy({
    interval: "15m",
    foundationTypes: ["relaunch"],
    auxiliaryTypes: ["trendline"],
    hasPivot: true,
    consolidationBars: 60,
    crossedLevel: true,
    openedBeyondTrigger: false,
    score: 99,
    certaintyScore: 99,
    aestheticScore: 99,
    rhythmScore: 99,
    sentimentScore: 99,
  });
  assert.equal(scoreOnly.permit, false, "an auxiliary confluence cannot manufacture execution permission");
  assert.equal(scoreOnly.tier, "none");
  assert.ok(scoreOnly.missing.includes("missing-mother-boundary"));
});

test("a mature triangle outer edge remains a causal core while cross-frame score alone does not", () => {
  const triangle = Engine.assessExecutionHierarchy({
    interval: "1h",
    foundationTypes: ["triangle"],
    auxiliaryTypes: ["trendline", "previousHigh"],
    consolidationBreakout: true,
    matureTriangleOuterEdge: true,
    directStructuralBoundary: true,
    crossedLevel: true,
    openedBeyondTrigger: false,
    consolidationBars: 48,
    structureQuality: 0.78,
    channelInteriorOccupancy: 0.82,
    channelSideTransitions: 5,
    triangleHasPriorAdvance: true,
    trianglePostSelloffRecovery: false,
    launchDistancePercent: 2.4,
    motherStructureNoise: false,
    highLevelDistribution: false,
    riskStructureShape: null,
  });
  assert.equal(triangle.permit, true);
  assert.equal(triangle.primaryFoundation, "mature-triangle-outer-edge");

  const fabricated = Engine.assessExecutionHierarchy({
    ...triangle,
    matureTriangleOuterEdge: false,
    directStructuralBoundary: false,
    auxiliaryTypes: ["trendline"],
    multiTimeframeConfluence: true,
    score: 99,
  });
  assert.equal(fabricated.permit, false, "multi-timeframe resonance can boost a valid structure but cannot create one");
});

test("five-minute execution requires a seasoned platform or triangle instead of a few local candles", () => {
  const base = {
    interval: "5m",
    foundationTypes: ["base"],
    auxiliaryTypes: ["previousHigh"],
    consolidationBreakout: true,
    crossedLevel: true,
    openedBeyondTrigger: false,
    outerEdgeConfirmed: true,
    outerEdgeScore: 91,
    consolidationBars: 20,
    ceilingAge: 15,
    ceilingTouches: 3,
    platformTouchGroups: 2,
    launchDistancePercent: 1.2,
    horizontalLaunchHasPriorAdvance: true,
    horizontalLaunchPostSelloffRecovery: false,
    horizontalLaunchUrgent: false,
    horizontalLaunchInsufficientEdgeDwell: false,
    motherStructureNoise: false,
    rhythmScore: 72,
  };
  assert.equal(Engine.assessExecutionHierarchy(base).permit, false, "a 20-bar local shelf is not a five-minute mother platform");

  const compactButRepeated = {
    ...base,
    consolidationBars: 25,
    outerEdgeScore: 86,
    ceilingTouches: 4,
    platformTouchGroups: 3,
    rhythmScore: 61,
  };
  assert.equal(Engine.assessExecutionHierarchy(compactButRepeated).permit, true, "a compact platform may qualify after repeated independent edge tests");

  const shortTriangle = {
    ...base,
    foundationTypes: ["triangle"],
    auxiliaryTypes: ["previousHigh", "trendline"],
    outerEdgeConfirmed: false,
    hasPivot: true,
    consolidationBars: 24,
    structureQuality: 0.76,
    channelInteriorOccupancy: 0.9,
    channelSideTransitions: 4,
    triangleHasPriorAdvance: true,
  };
  assert.equal(Engine.assessExecutionHierarchy(shortTriangle).permit, false, "five-minute compact triangles need more than 24 bars");
  assert.equal(Engine.assessExecutionHierarchy({ ...shortTriangle, interval: "15m", consolidationBars: 21 }).permit, true, "the same compact structure may be meaningful on fifteen minutes");
});

test("a quiet five-minute recross needs older edge memory or more repeated ceiling tests", () => {
  const quietRecross = Engine.assessExecutionHierarchy({
    interval: "5m",
    foundationTypes: ["base", "triangle"],
    auxiliaryTypes: ["previousHigh", "trendline"],
    consolidationBreakout: true,
    crossedLevel: true,
    openedBeyondTrigger: false,
    outerEdgeConfirmed: true,
    outerEdgeScore: 96,
    consolidationBars: 48,
    ceilingAge: 13,
    ceilingTouches: 5,
    platformTouchGroups: 3,
    launchDistancePercent: 1.1,
    horizontalLaunchHasPriorAdvance: true,
    triangleHasPriorAdvance: true,
    horizontalLaunchPostSelloffRecovery: false,
    trianglePostSelloffRecovery: false,
    horizontalLaunchUrgent: false,
    horizontalLaunchInsufficientEdgeDwell: false,
    directStructuralBoundary: true,
    structureQuality: 0.74,
    matureTriangleOuterEdge: true,
    relativeVolume: 0.99,
    orderFlowScore: 35,
    motherStructureNoise: false,
  });
  assert.equal(quietRecross.permit, false);
  assert.ok(quietRecross.missing.includes("five-minute-edge-not-seasoned"));
});

test("a mature one-hour post-shock recovery can replace an obsolete mother-pressure veto", () => {
  const recovered = Engine.assessExecutionHierarchy({
    interval: "1h",
    foundationTypes: ["base", "relaunch"],
    auxiliaryTypes: ["previousHigh"],
    consolidationBreakout: true,
    crossedLevel: true,
    openedBeyondTrigger: false,
    outerEdgeConfirmed: true,
    outerEdgeScore: 98,
    consolidationBars: 37,
    ceilingAge: 0,
    ceilingTouches: 14,
    platformTouchGroups: 4,
    launchDistancePercent: 1.18,
    horizontalLaunchHasPriorAdvance: true,
    horizontalLaunchPostSelloffRecovery: false,
    horizontalLaunchUrgent: false,
    horizontalLaunchInsufficientEdgeDwell: false,
    motherStructureNoise: true,
    motherStructureMode: "shock-formed-mother-box",
    matureHigherTimeframePostShockRecovery: true,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.02,
  });
  assert.equal(recovered.permit, true);
  assert.equal(recovered.primaryFoundation, "mother-platform-breakout");
});

test("low-volume first test can produce one red alert-only B on a stronger second boundary cross", () => {
  const candles = Array.from({ length: 9 }, (_, index) => candle(index, {
    open: 99.4,
    high: 100.25,
    low: 99.1,
    close: index === 3 || index === 4 ? 99.72 : 99.85,
    volume: 90 + index * 4,
  }));
  candles[2] = candle(2, { open: 99.7, high: 100.35, low: 99.55, close: 100.12, volume: 92 });
  candles[3] = candle(3, { open: 100.05, high: 100.1, low: 99.5, close: 99.72, volume: 86 });
  candles[6] = candle(6, { open: 99.78, high: 100.82, low: 99.66, close: 100.64, volume: 168 });
  const baseAttempt = {
    interval: "5m",
    foundationTypes: ["base"],
    auxiliaryTypes: ["previousHigh"],
    consolidationBreakout: true,
    crossedLevel: true,
    openedBeyondTrigger: false,
    outerEdgeConfirmed: true,
    outerEdgeScore: 88,
    consolidationBars: 46,
    ceilingAge: 10,
    platformTouchGroups: 3,
    launchDistancePercent: 1.4,
    horizontalLaunchHasPriorAdvance: true,
    horizontalLaunchPostSelloffRecovery: false,
    horizontalLaunchUrgent: false,
    horizontalLaunchInsufficientEdgeDwell: false,
    directStructuralBoundary: true,
    previousHighLevel: 100,
    triggerPrice: 100,
    level: 100,
    stop: 98.9,
    motherStructureNoise: false,
    highLevelDistribution: false,
    riskStructureShape: null,
    relativeVolume: 0.94,
    orderFlowScore: 43,
    klineVelocity: 0.34,
    aestheticScore: 76,
    certaintyScore: 84,
    rhythmScore: 73,
    sentimentScore: 65,
    score: 82,
    pattern: "盘整突破 + 横盘起飞 + 突破前高",
    patternKey: "base",
    confluence: ["base", "previousHigh"],
    reasons: [],
    evidence: [],
  };
  const attempts = [
    { ...baseAttempt, id: "first-test", index: 2, time: candles[2].time, decisionTime: candles[2].time },
    {
      ...baseAttempt,
      id: "second-cross",
      index: 6,
      time: candles[6].time,
      decisionTime: candles[6].time,
      relativeVolume: 1.28,
      orderFlowScore: 68,
      klineVelocity: 0.88,
      score: 90,
    },
  ];
  const indicators = { atr: Array(candles.length).fill(0.8) };
  const hints = Engine.buildSecondaryBreakoutHints(attempts, candles, indicators, "5m");

  assert.equal(hints.length, 1);
  assert.equal(hints[0].status, "secondary-hint");
  assert.equal(hints[0].secondaryBreakoutHint, true);
  assert.equal(hints[0].alertOnly, true);
  assert.equal(hints[0].executionAllowed, false);
  assert.equal(hints[0].primaryAttemptId, "first-test");
  assert.match(hints[0].pattern, /二次突破提示/);
  assert.ok(hints[0].evidence.some((item) => item.includes("第一次只完成缩量试盘")));
});

test("a washed first test can use direct candle expansion for the red second-breakout hint", () => {
  const candles = Array.from({ length: 8 }, (_, index) => candle(index, {
    open: 99.5,
    high: 100.1,
    low: 99.3,
    close: 99.8,
    volume: 100,
  }));
  candles[2] = candle(2, { open: 99.7, high: 100.3, low: 99.45, close: 100.05, volume: 100 });
  candles[3] = candle(3, { open: 100.02, high: 100.08, low: 99.2, close: 99.55, volume: 88 });
  candles[5] = candle(5, { open: 99.5, high: 102.2, low: 99.4, close: 102.0, volume: 180 });
  const attempt = {
    interval: "1h",
    foundationTypes: ["base"],
    auxiliaryTypes: ["previousHigh"],
    consolidationBreakout: true,
    crossedLevel: true,
    openedBeyondTrigger: false,
    outerEdgeConfirmed: true,
    outerEdgeScore: 86,
    consolidationBars: 25,
    ceilingAge: 8,
    platformTouchGroups: 3,
    launchDistancePercent: 1.2,
    horizontalLaunchHasPriorAdvance: true,
    horizontalLaunchPostSelloffRecovery: false,
    horizontalLaunchUrgent: false,
    horizontalLaunchInsufficientEdgeDwell: false,
    directStructuralBoundary: true,
    previousHighLevel: 100,
    triggerPrice: 100,
    level: 100,
    stop: 98.6,
    motherStructureNoise: false,
    highLevelDistribution: false,
    riskStructureShape: null,
    relativeVolume: 1.05,
    orderFlowScore: 62,
    klineVelocity: 1.35,
    certaintyScore: 84,
    rhythmScore: 74,
    sentimentScore: 70,
    score: 84,
    pattern: "盘整突破 + 横盘起飞 + 突破前高",
    patternKey: "base",
    confluence: ["base", "previousHigh"],
    reasons: [],
    evidence: [],
  };
  const attempts = [
    { ...attempt, id: "quiet-first", index: 2, time: candles[2].time, decisionTime: candles[2].time },
    {
      ...attempt,
      id: "expanded-second",
      index: 5,
      time: candles[5].time,
      decisionTime: candles[5].time,
      triggerPrice: 100.1,
      level: 100.1,
      relativeVolume: 1.04,
      orderFlowScore: 48,
      klineVelocity: 0.7,
      score: 88,
    },
  ];
  const hints = Engine.buildSecondaryBreakoutHints(
    attempts,
    candles,
    { atr: Array(candles.length).fill(0.8) },
    "1h",
  );
  assert.equal(hints.length, 1);
  assert.equal(hints[0].markerColor, "red");
  assert.equal(hints[0].executionAllowed, false);
  assert.ok(hints[0].evidence.some((item) => item.includes("价格运动确认强于首次试盘")));
});

test("a stopped valid platform may use a hierarchy-filtered recross only as a red alert", () => {
  const candles = Array.from({ length: 8 }, (_, index) => candle(index, {
    open: 99.6,
    high: 100.05,
    low: 99.35,
    close: 99.75,
    volume: 100,
  }));
  candles[2] = candle(2, { open: 99.7, high: 100.3, low: 99.4, close: 100.05, volume: 100 });
  candles[3] = candle(3, { open: 100.02, high: 100.08, low: 99.15, close: 99.5, volume: 90 });
  candles[5] = candle(5, { open: 99.8, high: 102.1, low: 99.7, close: 101.7, volume: 220 });
  const first = {
    id: "valid-quiet-first",
    interval: "15m",
    index: 2,
    time: candles[2].time,
    decisionTime: candles[2].time,
    status: "buy",
    foundationTypes: ["base"],
    auxiliaryTypes: ["previousHigh"],
    confluence: ["base", "previousHigh"],
    consolidationBreakout: true,
    crossedLevel: true,
    openedBeyondTrigger: false,
    outerEdgeConfirmed: true,
    outerEdgeScore: 84,
    consolidationBars: 48,
    ceilingAge: 8,
    platformTouchGroups: 3,
    horizontalLaunchHasPriorAdvance: true,
    horizontalLaunchPostSelloffRecovery: false,
    horizontalLaunchUrgent: false,
    horizontalLaunchInsufficientEdgeDwell: false,
    triggerPrice: 100,
    level: 100,
    stop: 98.6,
    motherStructureNoise: false,
    highLevelDistribution: false,
    riskStructureShape: null,
    relativeVolume: 0.78,
    orderFlowScore: 42,
    klineVelocity: 0.48,
    certaintyScore: 84,
    rhythmScore: 72,
    sentimentScore: 66,
    score: 84,
    pattern: "盘整突破 + 突破前高",
    patternKey: "base",
    reasons: [],
    evidence: [],
  };
  const filteredRecross = {
    ...first,
    id: "filtered-recross",
    index: 5,
    time: candles[5].time,
    decisionTime: candles[5].time,
    status: "filtered",
    foundationTypes: ["relaunch"],
    auxiliaryTypes: [],
    confluence: ["relaunch", "pivot"],
    hasPivot: true,
    outerEdgeConfirmed: false,
    triggerPrice: 100.1,
    level: 100.1,
    relativeVolume: 1.3,
    orderFlowScore: 78,
    klineVelocity: 1.1,
    score: 64,
    reasons: ["因果层级未通过：缺少成熟母平台或三角真实外沿"],
  };
  const hints = Engine.buildSecondaryBreakoutHints(
    [first],
    candles,
    { atr: Array(candles.length).fill(0.8) },
    "15m",
    [first, filteredRecross],
  );
  assert.equal(hints.length, 1);
  assert.equal(hints[0].status, "secondary-hint");
  assert.equal(hints[0].markerColor, "red");
  assert.equal(hints[0].executionAllowed, false);
  assert.equal(hints[0].primaryAttemptId, first.id);
  assert.match(hints[0].pattern, /二次突破提示/);
});

test("a fully traded 1h drawn structure is not vetoed again by a duplicate hierarchy gate", () => {
  const structure = {
    interval: "1h",
    foundationTypes: ["triangle", "relaunch"],
    auxiliaryTypes: ["trendline", "previousHigh"],
    hasPivot: true,
    structureShape: "ascending-triangle",
    triangleLines: {
      upper: { startIndex: 0, endIndex: 38, startPrice: 1.12, endPrice: 1.11 },
      lower: { startIndex: 0, endIndex: 38, startPrice: 0.92, endPrice: 1.06 },
    },
    consolidationBars: 39,
    structureQuality: 0.63,
    channelInteriorOccupancy: 0.71,
    channelMiddleParticipationRatio: 0.69,
    channelSideTransitions: 2,
    triangleHasPriorAdvance: true,
    trianglePriorAdvanceAtr: 12.1,
    directStructuralBoundary: true,
    crossedLevel: true,
    openedBeyondTrigger: false,
    breakoutOpen: 1.095,
    breakoutClose: 1.115,
    aboveEma90: true,
    ema90SlopeAtDecision: 2.7,
    motherStructureNoise: false,
    oneMinuteMotherBoxNoise: false,
    horizontalLaunchPostSelloffRecovery: false,
    trianglePostSelloffRecovery: false,
    riskStructureShape: null,
    highLevelDistribution: false,
    launchDistancePercent: 1.8,
    score: 99,
    certaintyScore: 77,
    rhythmScore: 84,
    sentimentScore: 76,
  };
  assert.equal(Engine.isReviewedHigherTimeframeStructureBreak(structure), true);
  assert.equal(Engine.assessExecutionHierarchy(structure).permit, true);
  assert.equal(Engine.isHighCertaintyEntry(structure), true);
  assert.equal(Engine.isReviewedHigherTimeframeStructureBreak({
    ...structure,
    channelSideTransitions: 0,
  }), false, "a one-way or hollow fitted line must remain filtered");
  assert.equal(Engine.isReviewedHigherTimeframeStructureBreak({
    ...structure,
    riskStructureShape: "rising-wedge",
  }), false, "an ascending risk structure must remain filtered");
});

test("PI complete-history 15:30 5m ignition is not buried by the stale listing mother range", () => {
  const targetTime = Date.UTC(2025, 1, 22, 7, 30);
  const fixture = Data.parseRows("okx", piFiveMinuteTriangleRows, 5 * 60_000);
  const rows = withStaleListingMotherWindow(fixture);
  const raw = Engine.analyzeTimeframe(rows, {
    interval: "5m",
    now: rows.at(-1).closeTime + 1,
  });
  const rejected = raw.rejected.find((item) => (
    item.time === targetTime
    && item.foundationTypes.includes("base")
    && item.foundationTypes.includes("triangle")
  ));
  assert.equal(rejected?.motherStructureMode, "unordered-mother-box", JSON.stringify(rejected, null, 2));
  assert.equal(rejected?.mainWaveOldDeclinePressureException, true, JSON.stringify(rejected, null, 2));
  assert.equal(rejected?.motherStructureNoise, false);
  assert.equal(rejected?.reasons.some((reason) => reason.includes("母箱体内部无序波动")), false);
  assert.ok(rejected?.evidence.some((item) => item.includes("大周期下跌旧高低只作背景")));

  const [gated] = Engine.applyContextGates([raw], [], { preselectedLeader: true });
  const buy = gated.signals.find((item) => item.time === targetTime);
  assert.ok(buy, JSON.stringify(gated.rejected.filter((item) => item.time === targetTime), null, 2));
  assert.equal(buy.status, "buy");
  assert.ok(buy.certaintyScore >= 88);
  assert.equal(buy.preHigherFrameMainWaveIgnitionPermit, true);
  assert.equal(buy.motherStructureNoise, false);
  assert.equal(buy.mainWaveContextSource, "leader-main-wave-ignition");
  assert.ok(buy.score >= 88);
  assert.deepEqual(buy.reasons, []);

  const [unselected] = Engine.applyContextGates([raw], [], { preselectedLeader: false });
  assert.equal(unselected.signals.some((item) => item.time === targetTime), false);
});

test("main-wave active or expected ignores pre-wave bearish 4h/daily context but keeps current structure gates", () => {
  const lower = Engine.analyzeTimeframe(horizontalBreakoutSeries(), { interval: "5m", now: 1_800_000_000_000 });
  assert.ok(lower.signals.length >= 1);
  const candidate = {
    ...lower.signals.at(-1),
    mainWaveStage: "active",
    foundationTypes: ["base"],
    auxiliaryTypes: ["previousHigh"],
    confluence: ["base", "previousHigh"],
    horizontalLaunchHasPriorAdvance: true,
    outerEdgeConfirmed: true,
    outerEdgeScore: 88,
    ceilingAge: 8,
    platformTouchGroups: 3,
    consolidationBars: 42,
    launchDistancePercent: 2,
    score: 90,
    aestheticScore: 82,
    certaintyScore: 92,
    rhythmScore: 80,
    sentimentScore: 72,
    orderFlowScore: 52,
    relativeVolume: 1.2,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.1,
    motherStructureNoise: false,
    oneMinuteMotherBoxNoise: false,
    riskStructureShape: null,
  };
  const mainWaveLower = { ...lower, signals: [candidate], pending: [], rejected: [] };
  const bearishContext = (interval) => ({
    interval,
    candles: [{ time: 1_600_000_000_000, close: 90 }],
    indicators: { ema90: [100], atr: [1] },
    signals: [], pending: [], rejected: [],
    regime: { bullish: false, strong: false, label: "旧下跌背景" },
    stats: { signalCount: 0, pendingCount: 0, rejectedCount: 0, lastPrice: 90 },
  });
  const contexts = [mainWaveLower, bearishContext("1h"), bearishContext("4h"), bearishContext("1d")];
  const automatic = Engine.applyContextGates(contexts)[0];
  assert.equal(automatic.signals.length, 1, JSON.stringify(automatic.rejected, null, 2));
  assert.equal(automatic.signals[0].mainWaveHigherFramePermit, true);
  assert.equal(automatic.signals[0].mainWaveContextSource, "strategy-main-wave");
  assert.ok(automatic.signals[0].evidence.some((item) => item.includes("旧下跌边界仅作背景")));

  const expected = Engine.applyContextGates(contexts, [], { mainWaveStage: "expected" })[0];
  assert.equal(expected.signals.length, 1, JSON.stringify(expected.rejected, null, 2));
  assert.equal(expected.signals[0].mainWaveContextSource, "manual-analysis");
  assert.ok(expected.signals[0].evidence.some((item) => item.includes("人工给出主升浪预期")));

  const risky = Engine.applyContextGates([
    { ...mainWaveLower, signals: [{ ...candidate, riskStructureShape: "rising-wedge" }] },
    ...contexts.slice(1),
  ])[0];
  assert.equal(risky.signals.length, 0);
  assert.ok(risky.rejected.some((item) => item.reasons.includes("大周期未共振")));
});

test("a preselected leader keeps a mature BANK-style 15m platform when higher-frame data refreshes away", () => {
  const raw = Engine.analyzeTimeframe(horizontalBreakoutSeries(), {
    interval: "15m",
    now: 1_800_000_000_000,
  });
  assert.ok(raw.signals.length >= 1);
  const candidate = {
    ...raw.signals.at(-1),
    foundationTypes: ["base"],
    auxiliaryTypes: ["previousHigh", "trendline"],
    confluence: ["base", "previousHigh", "trendline", "pivot"],
    crossedLevel: true,
    outerEdgeConfirmed: true,
    outerEdgeScore: 90,
    ceilingAge: 8,
    platformTouchGroups: 3,
    consolidationBars: 42,
    horizontalLaunchHasPriorAdvance: true,
    horizontalLaunchUrgent: false,
    horizontalLaunchInsufficientEdgeDwell: false,
    horizontalLaunchPostSelloffRecovery: false,
    motherStructureNoise: false,
    oneMinuteMotherBoxNoise: false,
    riskStructureShape: null,
    launchDistancePercent: 2,
    aestheticScore: 76,
    certaintyScore: 86,
    rhythmScore: 58,
    sentimentScore: 43,
    score: 64,
  };
  const bankLike = { ...raw, signals: [candidate], pending: [], rejected: [] };
  const [leader] = Engine.applyContextGates([bankLike], [], {
    preselectedLeader: true,
    mainWaveStage: "active",
    mainWaveContextSource: "leader-default-main-wave",
    mainWaveContextLabel: "龙头默认主升浪环境",
  });
  assert.equal(leader.signals.length, 1, JSON.stringify(leader.rejected, null, 2));
  assert.equal(leader.signals[0].mainWaveContextSource, "leader-default-main-wave");
  assert.ok(leader.signals[0].evidence.some((item) => item.includes("不撤销本周期")));

  const [ordinary] = Engine.applyContextGates([bankLike], [], {
    preselectedLeader: false,
    mainWaveStage: "active",
  });
  assert.equal(ordinary.signals.length, 0);

  const [risky] = Engine.applyContextGates([{
    ...bankLike,
    signals: [{ ...candidate, riskStructureShape: "rising-wedge" }],
  }], [], { preselectedLeader: true, mainWaveStage: "active" });
  assert.equal(risky.signals.length, 0);
});

test("a mature triangle outer edge is not vetoed by the parallel horizontal urgency submodel", () => {
  const matureTriangle = {
    interval: "15m",
    foundationTypes: ["base", "triangle"],
    auxiliaryTypes: ["previousHigh", "trendline"],
    consolidationBars: 56,
    outerEdgeConfirmed: true,
    outerEdgeScore: 89,
    ceilingAge: 12,
    platformTouchGroups: 4,
    matureTriangleOuterEdge: true,
    directStructuralBoundary: true,
    structureQuality: 0.71,
    channelInteriorOccupancy: 0.7,
    channelSideTransitions: 3,
    triangleHasPriorAdvance: true,
    trianglePriorAdvanceAtr: 10,
    horizontalLaunchHasPriorAdvance: true,
    horizontalLaunchUrgent: true,
    horizontalLaunchInsufficientEdgeDwell: false,
    launchDistancePercent: 2,
    hasPivot: true,
    aestheticScore: 78,
    certaintyScore: 90,
    rhythmScore: 74,
    sentimentScore: 68,
    triggerPrice: 100,
  };
  const permitted = Engine.assessExecutionHierarchy(matureTriangle);
  const ordinaryUrgentBase = Engine.assessExecutionHierarchy({
    ...matureTriangle,
    foundationTypes: ["base"],
    matureTriangleOuterEdge: false,
    directStructuralBoundary: false,
  });

  assert.equal(permitted.permit, true);
  assert.equal(permitted.primaryFoundation, "mother-platform-breakout");
  assert.equal(ordinaryUrgentBase.permit, false);
  assert.ok(ordinaryUrgentBase.missing.includes("hard-structure-veto"));
});

test("a mature 5m mother platform is not vetoed by an incidental low-transition triangle", () => {
  const hierarchy = Engine.assessExecutionHierarchy({
    interval: "5m",
    mainWaveStage: "active",
    foundationTypes: ["base", "triangle", "relaunch"],
    auxiliaryTypes: ["previousHigh", "trendline"],
    consolidationBars: 66,
    outerEdgeConfirmed: true,
    outerEdgeScore: 99,
    ceilingAge: 16,
    ceilingTouches: 3,
    platformTouchGroups: 2,
    horizontalLaunchHasPriorAdvance: true,
    horizontalLaunchPriorAdvanceAtr: 12.4,
    horizontalLaunchUrgent: false,
    horizontalLaunchInsufficientEdgeDwell: false,
    horizontalLaunchPostSelloffRecovery: false,
    triangleHasPriorAdvance: true,
    trianglePriorAdvanceAtr: 11.7,
    directStructuralBoundary: true,
    structureQuality: 0.79,
    channelInteriorOccupancy: 0.74,
    channelSideTransitions: 1,
    crossedLevel: true,
    openedBeyondTrigger: false,
    launchDistancePercent: 0.94,
    riskStructureShape: null,
    relativeVolume: 1.12,
    orderFlowScore: 64,
    certaintyScore: 99,
    rhythmScore: 68,
    sentimentScore: 51,
  });

  assert.equal(hierarchy.permit, true);
  assert.equal(hierarchy.primaryFoundation, "mother-platform-breakout");
  assert.equal(hierarchy.missing.includes("hard-structure-veto"), false);
});

test("an independently mature 5m sub-structure may break inside an older main-wave mother box", () => {
  const nestedPlatform = {
    interval: "5m",
    mainWaveStage: "active",
    insideMotherBase: true,
    foundationTypes: ["base", "relaunch"],
    auxiliaryTypes: ["previousHigh", "trendline"],
    consolidationBars: 40,
    outerEdgeConfirmed: true,
    outerEdgeScore: 81,
    ceilingAge: 7,
    ceilingTouches: 1,
    platformTouchGroups: 1,
    horizontalLaunchHasPriorAdvance: true,
    horizontalLaunchPriorAdvanceAtr: 8.12,
    horizontalLaunchUrgent: false,
    horizontalLaunchInsufficientEdgeDwell: false,
    horizontalLaunchPostSelloffRecovery: false,
    crossedLevel: true,
    openedBeyondTrigger: false,
    launchDistancePercent: 0.96,
    motherStructureNoise: false,
    riskStructureShape: null,
    highLevelDistribution: null,
    certaintyScore: 99,
    rhythmScore: 85,
    sentimentScore: 79,
  };
  const permitted = Engine.assessExecutionHierarchy(nestedPlatform);
  const weakInteriorFluctuation = Engine.assessExecutionHierarchy({
    ...nestedPlatform,
    consolidationBars: 18,
    outerEdgeScore: 62,
    horizontalLaunchPriorAdvanceAtr: 3,
  });

  assert.equal(permitted.permit, true);
  assert.equal(permitted.missing.includes("hard-structure-veto"), false);
  assert.equal(weakInteriorFluctuation.permit, false);
  assert.ok(weakInteriorFluctuation.missing.includes("hard-structure-veto"));
});

test("a mature nested compound structure may inherit the platform's earlier impulse", () => {
  const hierarchy = Engine.assessExecutionHierarchy({
    interval: "5m",
    mainWaveStage: "active",
    insideMotherBase: true,
    foundationTypes: ["base", "triangle", "relaunch"],
    auxiliaryTypes: ["previousHigh", "trendline"],
    consolidationBars: 81,
    outerEdgeConfirmed: true,
    outerEdgeScore: 66,
    horizontalLaunchHasPriorAdvance: true,
    horizontalLaunchPriorAdvanceAtr: 26.2,
    horizontalLaunchUrgent: false,
    horizontalLaunchInsufficientEdgeDwell: false,
    horizontalLaunchPostSelloffRecovery: false,
    triangleHasPriorAdvance: false,
    trianglePriorAdvanceAtr: 26.2,
    trianglePostSelloffRecovery: false,
    directStructuralBoundary: true,
    structureQuality: 0.86,
    channelInteriorOccupancy: 0.65,
    channelSideTransitions: 3,
    crossedLevel: true,
    openedBeyondTrigger: false,
    launchDistancePercent: 0.92,
    motherStructureNoise: false,
    riskStructureShape: null,
    relativeVolume: 0.93,
    orderFlowScore: 63,
    certaintyScore: 99,
    rhythmScore: 84,
    sentimentScore: 71,
  });

  assert.equal(hierarchy.permit, true);
  assert.equal(hierarchy.missing.includes("missing-prior-advance"), false);
  assert.equal(hierarchy.missing.includes("hard-structure-veto"), false);
});

test("a preselected leader can reset stale long-history pressure only with an independent high-quality current structure", () => {
  const time = Date.parse("2024-12-14T01:00:00Z");
  const candidate = {
    id: "xrp-independent-reset",
    interval: "15m",
    index: 0,
    time,
    decisionTime: time,
    status: "filtered",
    pattern: "盘整突破 + 横盘起飞 + 三角突破 + 拐点收复 + 突破前高",
    patternKey: "base",
    primaryPatternKey: "consolidationBreakout",
    foundationTypes: ["base", "triangle"],
    auxiliaryTypes: ["previousHigh", "trendline"],
    confluence: ["base", "triangle", "previousHigh", "pivot"],
    hasPivot: true,
    consolidationBreakout: true,
    consolidationBars: 40,
    outerEdgeConfirmed: true,
    outerEdgeScore: 94,
    ceilingTouches: 8,
    platformTouchGroups: 3,
    matureTriangleOuterEdge: true,
    directStructuralBoundary: true,
    structureQuality: 0.77,
    channelInteriorOccupancy: 0.88,
    channelSideTransitions: 3,
    horizontalLaunchHasPriorAdvance: true,
    horizontalLaunchPriorAdvanceAtr: 9.2,
    triangleHasPriorAdvance: true,
    trianglePriorAdvanceAtr: 9.1,
    horizontalLaunchPostSelloffRecovery: false,
    trianglePostSelloffRecovery: false,
    horizontalLaunchUrgent: false,
    horizontalBrokenOuterPlatform: false,
    motherStructureMode: "post-impulse-high-level-rotation",
    motherStructureNoise: true,
    motherStructurePosition: 0.81,
    oldMotherBoundaryPrecedesIndependentAdvance: true,
    riskStructureShape: null,
    openedBeyondTrigger: false,
    crossedLevel: true,
    triggerPrice: 101,
    level: 101,
    previousHighLevel: 101,
    breakoutLow: 99.8,
    launchDistancePercent: 1.35,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.1,
    relativeVolume: 1.26,
    orderFlowScore: 69,
    score: 86,
    aestheticScore: 76,
    certaintyScore: 98,
    rhythmScore: 70,
    sentimentScore: 51,
    evidence: [],
    reasons: [
      "仍在冲高后的母压力区间内部：15m 回看 240 根，尚未突破真正峰值 104.00000000",
      "因果层级未通过：存在母箱体内部、急促推进或风险结构硬否决",
    ],
  };
  const baseResult = {
    interval: "15m",
    candles: [{ time, closeTime: time + 15 * 60_000 - 1, open: 100, high: 102, low: 99.8, close: 101.5, volume: 200 }],
    indicators: { atr: [1], ema90: [99] },
    signals: [],
    pending: [],
    rejected: [candidate],
    structures: [],
    regime: { bullish: true, strong: true, label: "主升" },
    stats: { signalCount: 0, pendingCount: 0, rejectedCount: 1, lastPrice: 101.5 },
  };
  const [leader] = Engine.applyContextGates([baseResult], [], { preselectedLeader: true });
  const [ordinary] = Engine.applyContextGates([baseResult], [], { preselectedLeader: false });
  const [notReset] = Engine.applyContextGates([{
    ...baseResult,
    rejected: [{ ...candidate, oldMotherBoundaryPrecedesIndependentAdvance: false }],
  }], [], { preselectedLeader: true });

  assert.equal(leader.signals.length, 1, JSON.stringify(leader.rejected, null, 2));
  assert.equal(leader.signals[0].leaderIndependentStructureReset, true);
  assert.equal(leader.signals[0].motherStructureNoise, false);
  assert.deepEqual(leader.signals[0].reasons, []);
  assert.equal(ordinary.signals.length, 0);
  assert.equal(notReset.signals.length, 0);
});

test("an elite rebuilt platform and mature triangle can re-enter immediately after a failed test", () => {
  const time = Date.parse("2024-12-15T17:30:00Z");
  const candidate = {
    id: "xrp-elite-retry",
    interval: "15m",
    index: 0,
    time,
    decisionTime: time,
    status: "filtered",
    pattern: "盘整突破 + 横盘起飞 + 三角突破 + 拐点收复 + 突破前高",
    patternKey: "base",
    primaryPatternKey: "consolidationBreakout",
    foundationTypes: ["base", "triangle"],
    auxiliaryTypes: ["previousHigh", "trendline"],
    confluence: ["base", "triangle", "previousHigh", "pivot"],
    hasPivot: true,
    consolidationBreakout: true,
    consolidationBars: 52,
    outerEdgeConfirmed: true,
    outerEdgeScore: 90,
    ceilingTouches: 13,
    ceilingAge: 20,
    platformTouchGroups: 4,
    matureTriangleOuterEdge: true,
    directStructuralBoundary: true,
    structureQuality: 0.7,
    channelInteriorOccupancy: 0.87,
    channelSideTransitions: 3,
    horizontalLaunchHasPriorAdvance: true,
    horizontalLaunchPriorAdvanceAtr: 7.2,
    triangleHasPriorAdvance: true,
    trianglePriorAdvanceAtr: 7.2,
    horizontalLaunchPostSelloffRecovery: false,
    trianglePostSelloffRecovery: false,
    horizontalLaunchUrgent: false,
    motherStructureNoise: false,
    riskStructureShape: null,
    openedBeyondTrigger: false,
    crossedLevel: true,
    triggerPrice: 101,
    level: 101,
    previousHighLevel: 101,
    breakoutLow: 100,
    launchDistancePercent: 0.3,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.1,
    mainWaveStage: "active",
    relativeVolume: 0.7,
    orderFlowScore: 33,
    score: 99,
    aestheticScore: 83,
    certaintyScore: 99,
    rhythmScore: 92,
    sentimentScore: 87,
    evidence: [],
    reasons: ["前次试错止损后尚未形成新的高质量母平台真实外沿或成熟三角外沿"],
  };
  const baseResult = {
    interval: "15m",
    candles: [{ time, closeTime: time + 15 * 60_000 - 1, open: 100.2, high: 102, low: 100, close: 101.5, volume: 120 }],
    indicators: { atr: [1], ema90: [99] },
    signals: [],
    pending: [],
    rejected: [candidate],
    structures: [],
    regime: { bullish: true, strong: true, label: "主升" },
    stats: { signalCount: 0, pendingCount: 0, rejectedCount: 1, lastPrice: 101.5 },
  };
  const [leader] = Engine.applyContextGates([baseResult], [], { preselectedLeader: true });
  const [ordinary] = Engine.applyContextGates([baseResult], [], { preselectedLeader: false });

  assert.equal(leader.signals.length, 1, JSON.stringify(leader.rejected, null, 2));
  assert.equal(leader.signals[0].leaderEliteStructureRetry, true);
  assert.deepEqual(leader.signals[0].reasons, []);
  assert.equal(ordinary.signals.length, 0);
});

test("PI 2025-02-23 11:15 Beijing 5m buys the mature structure after an EMA90 reclaim", () => {
  const targetTime = Date.UTC(2025, 1, 23, 3, 15);
  const rows = Data.parseRows("okx", piFiveMinuteEma90ReclaimRows, 5 * 60_000);
  const targetIndex = rows.findIndex((row) => row.time === targetTime);
  assert.ok(targetIndex > 0);

  const prefixRows = rows.slice(0, targetIndex + 1);
  const prefix = Engine.analyzeTimeframe(prefixRows, {
    interval: "5m",
    now: prefixRows.at(-1).closeTime + 1,
    leaderSelected: true,
  });
  const full = Engine.analyzeTimeframe(rows, {
    interval: "5m",
    now: rows.at(-1).closeTime + 1,
    leaderSelected: true,
  });
  const signal = prefix.signals.find((item) => item.time === targetTime);
  const replay = full.signals.find((item) => item.time === targetTime);

  assert.ok(signal, JSON.stringify(prefix.rejected.filter((item) => item.time === targetTime), null, 2));
  assert.ok(replay, JSON.stringify(full.rejected.filter((item) => item.time === targetTime), null, 2));
  assert.equal(signal.status, "buy");
  assert.ok(signal.certaintyScore >= 88);
  assert.ok(signal.score >= 88);
  assert.ok(signal.certaintyScore >= 88);
  assert.equal(signal.ema90ReclaimContinuation, true);
  assert.equal(signal.ema90ReclaimOverrodeNestedAscendingTrap, true);
  assert.equal(signal.riskStructureShape, null);
  assert.equal(signal.outerEdgeConfirmed, true);
  assert.ok(signal.foundationTypes.includes("base"));
  assert.ok(signal.contextTokens.includes("ema90-reclaim"));
  assert.ok(signal.ema90BreachStartIndex < signal.ema90ReclaimIndex);
  assert.ok(signal.ema90ReclaimIndex < signal.index);
  assert.ok(signal.ema90ReclaimRecoveryBars >= 8);
  assert.ok(signal.evidence.some((item) => item.includes("A+ EMA90修复再启动")), JSON.stringify(signal.evidence));
  assert.deepEqual(
    {
      triggerPrice: replay.triggerPrice,
      breach: replay.ema90BreachStartIndex,
      reclaim: replay.ema90ReclaimIndex,
      recoveryBars: replay.ema90ReclaimRecoveryBars,
      pattern: replay.pattern,
    },
    {
      triggerPrice: signal.triggerPrice,
      breach: signal.ema90BreachStartIndex,
      reclaim: signal.ema90ReclaimIndex,
      recoveryBars: signal.ema90ReclaimRecoveryBars,
      pattern: signal.pattern,
    },
    "future candles must not alter the EMA90 reclaim decision",
  );
});

test("EMA90 reclaim continuation rejects a deep breakdown or an overlong stay below the average", () => {
  const index = 110;
  const ema90 = Array.from({ length: index + 1 }, (_, cursor) => 100 + cursor * 0.01);
  const atrValues = Array(index + 1).fill(1);
  const buildRows = (breachStart, breachEnd, deviation) => Array.from({ length: index + 1 }, (_, cursor) => {
    const close = cursor >= breachStart && cursor <= breachEnd
      ? ema90[cursor] + deviation
      : ema90[cursor] + 0.55;
    return candle(cursor, {
      open: close - 0.08,
      close,
      high: close + 0.2,
      low: close - 0.2,
    });
  });
  const context = {
    structureStartIndex: 60,
    consolidationBars: 50,
    structureQuality: 0.82,
    structureLabel: "箱体",
    matureStructure: true,
    hasPriorAdvance: true,
    priorAdvanceAtr: 4.2,
    postSelloffRecovery: false,
    strictMotherRisk: false,
    ascendingStructureTrap: null,
  };

  const deep = Engine.assessEma90ReclaimContinuation(
    buildRows(82, 89, -3.2),
    index,
    { ema90, atr: atrValues },
    "5m",
    1,
    context,
  );
  assert.equal(deep.qualified, false);
  assert.ok(deep.deepestBreachAtr < -1.8);

  const overlong = Engine.assessEma90ReclaimContinuation(
    buildRows(68, 95, -0.55),
    index,
    { ema90, atr: atrValues },
    "5m",
    1,
    context,
  );
  assert.equal(overlong.qualified, false);
  assert.ok(overlong.breachSpanBars > 24 || overlong.belowEmaBars > 24);
});

test("PI 2025-02-22 19:05 Beijing 5m buys the post-impulse box at its true 16:30 ceiling", () => {
  const targetTime = Date.UTC(2025, 1, 22, 11, 5);
  const structureStartTime = Date.UTC(2025, 1, 22, 7, 55);
  const ceilingTime = Date.UTC(2025, 1, 22, 8, 30);
  const floorTime = Date.UTC(2025, 1, 22, 9, 45);
  const rows = Data.parseRows("okx", piFiveMinuteBoxRows, 5 * 60_000);
  const start = rows.find((row) => row.time === structureStartTime);
  const ceiling = rows.find((row) => row.time === ceilingTime);
  const floor = rows.find((row) => row.time === floorTime);
  assert.equal(start?.close > start?.open, true);
  assert.equal(ceiling?.high, 0.892);
  assert.equal(floor?.low, 0.82);

  const result = Engine.analyzeTimeframe(rows, {
    interval: "5m",
    now: rows.at(-1).closeTime + 1,
    leaderSelected: true,
  });
  const signal = result.signals.find((item) => item.time === targetTime);
  assert.ok(signal, JSON.stringify(result.rejected.filter((item) => item.time === targetTime), null, 2));
  assert.equal(signal.status, "buy");
  assert.ok(signal.certaintyScore >= 88);
  assert.equal(signal.outerEdgeConfirmed, true);
  assert.ok(signal.foundationTypes.includes("base"));
  assert.ok(signal.auxiliaryTypes.includes("previousHigh"));
  assert.match(signal.pattern, /横盘起飞/);
  assert.match(signal.pattern, /突破前高/);
  assert.ok(Math.abs(signal.level - ceiling.high) < 1e-10);
  assert.ok(Math.abs(signal.triggerPrice - ceiling.high) < 1e-10);
  assert.equal(signal.mainWaveStage, "active");
  assert.equal(signal.mainWaveContextSource, "strategy-inference");
  assert.deepEqual(signal.reasons, []);

  const permitted = Engine.applyContextGates(
    [result, earlyNewCoinHigherFrames(targetTime)[0]],
    [],
    { preselectedLeader: true },
  ).find((item) => item.interval === "5m");
  const marked = permitted.signals.find((item) => item.time === targetTime);
  assert.equal(marked?.triggerPrice, ceiling.high);
  assert.equal(marked?.newCoinNotFallingMainWavePermit, true);
  assert.equal(marked?.mainWaveContextSource, "new-coin-not-falling");
  assert.ok(marked?.evidence.some((item) => item.includes("新币不跌后的主升浪")));

  const coldResult = {
    ...result,
    signals: result.signals.map((item) => item.time === targetTime ? {
      ...item,
      sentimentScore: 48,
      orderFlowScore: 25,
      relativeVolume: 1,
    } : item),
  };
  const coldContext = Engine.applyContextGates(
    [coldResult, earlyNewCoinHigherFrames(targetTime)[0]],
    [],
    { preselectedLeader: true },
  ).find((item) => item.interval === "5m");
  assert.equal(coldContext.signals.some((item) => item.time === targetTime), false);
  assert.ok(coldContext.rejected.some((item) => item.time === targetTime
    && item.reasons.some((reason) => ["大周期证据不足", "大周期未共振"].includes(reason))));
});

test("ascending and symmetrical triangles are equal-level foundations under the same quality gates", () => {
  const ascendingRows = ascendingTriangleBreakoutSeries();
  const symmetricalRows = triangleBreakoutSeries();
  const ascendingIndex = ascendingRows.length - 1;
  const symmetricalIndex = symmetricalRows.length - 1;
  const ascending = Engine.detectTriangle(
    ascendingRows,
    ascendingIndex,
    Engine.atr(ascendingRows, 14)[ascendingIndex - 1],
    { interval: "15m" },
  );
  const symmetrical = Engine.detectTriangle(
    symmetricalRows,
    symmetricalIndex,
    Engine.atr(symmetricalRows, 14)[symmetricalIndex - 1],
    { interval: "15m" },
  );
  assert.equal(ascending.structureShape, "ascending-triangle");
  assert.equal(symmetrical.structureShape, "converging-triangle");
  [ascending, symmetrical].forEach((structure) => {
    assert.equal(structure.type, "triangle");
    assert.ok(structure.quality >= 0.68, JSON.stringify(structure));
    assert.ok(structure.triangleLines.upper.touchGroups >= 2);
    assert.ok(structure.triangleLines.lower.touchGroups >= 2);
    assert.ok(structure.channelSideTransitions >= 2);
    assert.equal(structure.preStructureContext.mode, "prior-advance");
  });
});

test("does not draw a triangle after an unrecovered decline unless new-coin-not-falling is explicit", () => {
  const rows = ascendingTriangleBreakoutSeries({ precedingDecline: true });
  const index = rows.length - 1;
  const atrValue = Engine.atr(rows, 14)[index - 1];
  assert.equal(Engine.detectTriangle(rows, index, atrValue, { interval: "15m" }), null);
  const explicitNewCoin = Engine.detectTriangle(rows, index, atrValue, {
    interval: "15m",
    newCoinNotFalling: true,
  });
  assert.equal(explicitNewCoin.structureShape, "ascending-triangle");
  assert.equal(explicitNewCoin.preStructureContext.mode, "new-coin-not-falling");
});

test("does not treat a coherent local rebound inside a broad downtrend as a prior impulse", () => {
  const rows = triangleInsideDowntrendRepairSeries();
  const index = rows.length - 1;
  const atrValue = Engine.atr(rows, 14)[index - 1];
  const invalid = Engine.detectTriangle(rows, index, atrValue, { interval: "15m" });
  assert.equal(invalid, null);

  // The geometry itself is deliberately a valid ascending triangle. The explicit
  // new-coin exception proves that rejection comes from market-stage context,
  // not from weakening the triangle definition until the fixture disappears.
  const explicitException = Engine.detectTriangle(rows, index, atrValue, {
    interval: "15m",
    newCoinNotFalling: true,
  });
  assert.equal(explicitException.structureShape, "ascending-triangle");

  const context = Engine.assessPreStructureContext(rows, index, 82, atrValue, { interval: "15m" });
  assert.equal(context.qualified, false, JSON.stringify(context));
  assert.equal(context.downtrendRepairBounce || context.unrecoveredPriorDecline, true, JSON.stringify(context));
  assert.equal(context.freshRangeExpansion, false, JSON.stringify(context));
});

test("does not promote a small triangle nested inside a broad consolidation range", () => {
  const rows = triangleInsideBroadRangeSeries();
  const index = rows.length - 1;
  const atrValue = Engine.atr(rows, 14)[index - 1];
  assert.equal(Engine.detectTriangle(rows, index, atrValue, { interval: "15m" }), null);
  const explicitException = Engine.detectTriangle(rows, index, atrValue, {
    interval: "15m",
    newCoinNotFalling: true,
  });
  assert.equal(explicitException.structureShape, "ascending-triangle");

  const context = Engine.assessPreStructureContext(rows, index, 82, atrValue, { interval: "15m" });
  assert.equal(context.qualified, false, JSON.stringify(context));
  assert.equal(context.insideBroadConsolidation || context.wideChopWithoutExpansion, true, JSON.stringify(context));
  assert.equal(context.freshRangeExpansion, false, JSON.stringify(context));
});

test("keeps a small but coherent and immediate upward push before consolidation", () => {
  const rows = modestImpulseContextRows();
  const context = Engine.assessPreStructureContext(rows, rows.length, 58, 1, { interval: "15m" });
  assert.equal(context.qualified, true, JSON.stringify(context));
  assert.equal(context.mode, "prior-advance");
  assert.ok(context.bestAdvance.advanceAtr >= 0.75 && context.bestAdvance.advanceAtr < 2, JSON.stringify(context));
  assert.equal(context.downtrendRepairBounce, false);
  assert.equal(context.insideBroadConsolidation, false);
});

test("only 4h and daily structures may replace the prior impulse with a long low-level bottom base", () => {
  const rows = higherTimeframeBottomBaseRows();
  const oneHour = Engine.assessPreStructureContext(rows, rows.length, 30, 1, { interval: "1h" });
  const fourHour = Engine.assessPreStructureContext(rows, rows.length, 30, 1, { interval: "4h" });
  const daily = Engine.assessPreStructureContext(rows, rows.length, 30, 1, { interval: "1d" });
  assert.equal(oneHour.qualified, false);
  assert.equal(oneHour.unrecoveredPriorDecline, true);
  [fourHour, daily].forEach((context) => {
    assert.equal(context.qualified, true);
    assert.equal(context.mode, "higher-timeframe-bottom-base");
    assert.equal(context.higherTimeframeBottomBase, true);
    assert.ok(context.bottomBaseDetail.bars >= 24);
  });
});

test("rejects trendline pairs that leave a large untraded hollow channel", () => {
  const { rows, upperAt, lowerAt } = envelopeOccupancySeries({ hollow: true });
  const result = Engine.assessEnvelopeCoverage(rows, 0, rows.length, upperAt, lowerAt, 1);
  assert.equal(result.upper.acceptable, true);
  assert.equal(result.lower.acceptable, true);
  assert.equal(result.hollowChannel, true, JSON.stringify(result));
  assert.equal(result.acceptable, false, JSON.stringify(result));
  assert.ok(result.longestHollowRun >= 32, JSON.stringify(result));
  assert.ok(result.middleParticipationRatio < 0.3, JSON.stringify(result));
});

test("keeps an occupied convergence whose candles repeatedly rotate between both boundaries", () => {
  const { rows, upperAt, lowerAt } = envelopeOccupancySeries();
  const result = Engine.assessEnvelopeCoverage(rows, 0, rows.length, upperAt, lowerAt, 1);
  assert.equal(result.hollowChannel, false, JSON.stringify(result));
  assert.equal(result.acceptable, true, JSON.stringify(result));
  assert.ok(result.channelSideTransitions >= 6, JSON.stringify(result));
  assert.ok(result.interiorOccupancy >= 0.55, JSON.stringify(result));
});

test("detects an AKE-style long convergence from many confirmed swings", () => {
  const rows = longConvergenceBreakoutSeries();
  const result = Engine.analyzeTimeframe(rows, { interval: "4h", now: 1_800_000_000_000 });
  const signal = result.signals.find((item) => item.index === rows.length - 1 && item.pattern.includes("三角突破"));
  assert.ok(signal, JSON.stringify({ signals: result.signals.slice(-4), rejected: result.rejected.slice(-6) }));
  assert.match(signal.pattern, /趋势线突破/);
  assert.ok(signal.consolidationBars >= 72, JSON.stringify(signal));
  assert.ok(signal.triangleLines?.upper && signal.triangleLines?.lower);
  assert.ok(signal.evidence.some((item) => item.includes("已确认摆动触点")));
});

test("a descending upper and more slowly descending lower boundary form a falling wedge", () => {
  const rows = longConvergenceBreakoutSeries({ risingLower: false });
  const result = Engine.analyzeTimeframe(rows, { interval: "4h", now: 1_800_000_000_000 });
  const wedge = [...result.signals, ...result.rejected].find((item) => (
    item.index === rows.length - 1 && item.pattern.includes("下降楔形突破")
  ));
  assert.ok(wedge, JSON.stringify({ signals: result.signals.slice(-4), rejected: result.rejected.slice(-6) }));
  assert.equal(wedge.structureShape, "falling-wedge");
  assert.ok(wedge.triangleLines.upper && wedge.triangleLines.lower);
  assert.ok(result.structures.some((item) => item.structureShape === "falling-wedge" && item.structurePreconfirmed));
});

test("recognized one-hour and four-hour envelopes are B points without score or EMA re-veto", () => {
  const gmtOneHourWedge = {
    interval: "1h",
    foundationTypes: ["triangle", "base"],
    auxiliaryTypes: ["trendline"],
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
  assert.equal(Engine.isReviewedHigherTimeframeStructureBreak(gmtOneHourWedge), true);
  assert.equal(Engine.isRecognizedHigherTimeframeStructureBreak(gmtOneHourWedge), true);
  assert.equal(Engine.isReviewedHigherTimeframeStructureBreak({
    ...gmtOneHourWedge,
    interval: "4h",
    structureShape: "converging-triangle",
  }), true);
  assert.equal(Engine.isReviewedHigherTimeframeStructureBreak({
    ...gmtOneHourWedge,
    interval: "5m",
  }), false, "short frames retain their stricter execution filters");
  assert.equal(Engine.isReviewedHigherTimeframeStructureBreak({
    ...gmtOneHourWedge,
    breakoutClose: gmtOneHourWedge.breakoutOpen - 0.01,
  }), false, "bearish candles are never promoted");
  assert.equal(Engine.isReviewedHigherTimeframeStructureBreak({
    ...gmtOneHourWedge,
    riskStructureShape: "rising-wedge",
  }), false, "rising wedges remain hard vetoes");
  assert.equal(Engine.isReviewedHigherTimeframeStructureBreak({
    ...gmtOneHourWedge,
    crossedLevel: false,
  }), false, "an identified but unbroken structure is not an entry");
});

test("keeps a very long falling wedge only after a full-window two-boundary audit", () => {
  const rows = Array.from({ length: 30 }, (_, index) => candle(index, {
    open: 88 + index * 0.38,
    close: 88.2 + index * 0.38,
    high: 88.55 + index * 0.38,
    low: 87.7 + index * 0.38,
    volume: 112,
  }));
  for (let step = 0; step < 220; step += 1) {
    const upper = 120 - step * 0.08;
    const lower = 100 - step * 0.025;
    const spread = upper - lower;
    const wave = step >= 216
      ? 0.88
      : 0.08 + ((1 + Math.cos(Math.PI * 2 * step / 16)) / 2) * 0.84;
    const close = lower + spread * wave;
    rows.push(candle(rows.length, {
      open: close + (step % 2 ? 0.06 : -0.06),
      close,
      high: Math.min(upper - 0.1, close + 0.22),
      low: Math.max(lower, close - 0.22),
      volume: 96,
    }));
  }
  const index = rows.length;
  const upperAtBreak = 120 - 220 * 0.08;
  rows.push(candle(index, {
    open: upperAtBreak - 0.3,
    close: upperAtBreak + 1.1,
    high: upperAtBreak + 1.35,
    low: upperAtBreak - 0.42,
    volume: 245,
  }));
  const upperCandidate = {
    trendline: {
      startIndex: 30,
      startPrice: 120,
      anchorIndex: 130,
      anchorPrice: 112,
      anchorMode: "wick",
      touches: 3,
      structureStartIndex: 30,
      postImpulseStartIndex: 31,
      activeProximity: 0.4,
      provisionalLongBoundary: true,
    },
    preStructureContext: {
      qualified: true,
      evidence: ["前置拉升已确认"],
    },
  };
  const wedge = Engine.detectLongConvergence(rows, index, 1, upperCandidate, { interval: "5m" });
  assert.ok(wedge, "expected a strict full-window falling wedge");
  assert.equal(wedge.structureShape, "falling-wedge");
  assert.ok(["outer-envelope", "quantile-outer-envelope"].includes(
    wedge.triangleLines.lower.boundaryModel,
  ));
  assert.ok(wedge.consolidationBars >= 200);
  assert.ok(wedge.channelInteriorOccupancy >= 0.58);
  assert.ok(wedge.evidence.some((item) => /完整盘整|母结构外包络|临时长上轨本身不可执行/.test(item)));
});

test("H one-hour post-impulse wedge keeps both the 21:00 upper-line B and 22:00 prior-high B", () => {
  const rows = hOneHourWedgeRows.map(([time, open, high, low, close, volume]) => ({
    time,
    closeTime: time + 60 * 60_000 - 1,
    open,
    high,
    low,
    close,
    volume,
    quoteVolume: volume * close,
  }));
  const firstTime = Date.parse("2025-07-01T13:00:00Z");
  const secondTime = Date.parse("2025-07-01T14:00:00Z");
  const options = { interval: "1h", now: Date.parse("2025-07-02T00:00:00Z") };
  const firstOnly = Engine.analyzeTimeframe(rows.slice(0, -1), options);
  const raw = Engine.analyzeTimeframe(rows, options);
  const firstBeforeSecondExists = firstOnly.signals.find((item) => item.time === firstTime);
  const buys = raw.signals.filter((item) => [firstTime, secondTime].includes(item.time));
  assert.ok(firstBeforeSecondExists, JSON.stringify(firstOnly.rejected.slice(-5)));
  assert.deepEqual(buys.map((item) => item.time), [firstTime, secondTime]);
  buys.forEach((item) => {
    assert.equal(item.interval, "1h");
    assert.equal(item.structureShape, "falling-wedge");
    assert.equal(item.triangleLines.upper.startIndex, 12);
    assert.equal(rows[item.triangleLines.upper.startIndex].time, Date.parse("2025-06-29T20:00:00Z"));
    assert.equal(Engine.isOneHourPostImpulseWedgeIgnition(item), true);
    assert.equal(Engine.isHighCertaintyEntry(item), true);
  });
  assert.deepEqual(
    {
      level: buys[0].level,
      triggerPrice: buys[0].triggerPrice,
      structureStart: buys[0].triangleLines.upper.startIndex,
    },
    {
      level: firstBeforeSecondExists.level,
      triggerPrice: firstBeforeSecondExists.triggerPrice,
      structureStart: firstBeforeSecondExists.triangleLines.upper.startIndex,
    },
    "the later 22:00 candle must not alter the causal 21:00 decision",
  );
  assert.ok(buys[1].evidence.some((item) => item.includes("两级触发价独立预设")));

  const contextFrame = (interval, bullish) => ({
    interval,
    candles: [{
      time: firstTime - 8 * 60 * 60_000,
      closeTime: firstTime - 1,
      open: bullish ? 115 : 95,
      high: bullish ? 122 : 98,
      low: bullish ? 112 : 88,
      close: bullish ? 120 : 90,
    }],
    indicators: { ema90: [100], atr: [1] },
    signals: [], pending: [], rejected: [], structures: [],
    regime: { bullish, strong: bullish, label: bullish ? "主升环境" : "禁止追多" },
    stats: { signalCount: 0, pendingCount: 0, rejectedCount: 0, lastPrice: bullish ? 120 : 90 },
  });
  const [gated] = Engine.applyContextGates(
    [raw, contextFrame("4h", true), contextFrame("1d", true)],
    [contextFrame("1h", false), contextFrame("4h", false)],
  );
  const displayed = gated.signals.filter((item) => [firstTime, secondTime].includes(item.time));
  assert.deepEqual(displayed.map((item) => item.time), [firstTime, secondTime]);
  assert.ok(displayed.every((item) => item.marketEmotion === "BTC 背景逆风 0/2 · 龙头独立判断"));
  assert.equal(
    raw.rejected.some((item) => (
      item.time === secondTime
      && item.structureShape === "falling-wedge"
      && item.reasons.some((reason) => reason.includes("同一盘整已有有效买点"))
    )),
    false,
    "the staged 21:00/22:00 buys must not leave a duplicate lifecycle-veto noise record",
  );
});

test("one-hour falling wedge uses its own upper rail when no duplicate trendline auxiliary exists", () => {
  const signal = {
    interval: "1h",
    foundationTypes: ["triangle"],
    auxiliaryTypes: ["previousHigh"],
    structureShape: "falling-wedge",
    structureQuality: 0.68,
    triangleLines: {
      upper: {
        startIndex: 20, endIndex: 65, startPrice: 12, endPrice: 10,
        envelopeCoverage: 0.98, bodyCoverage: 0.99, crossingRatio: 0.01,
        touchGroups: 2, activeProximity: 0.86, originRangeAtr: 6,
      },
      lower: {
        startIndex: 20, endIndex: 65, startPrice: 9, endPrice: 8.4,
        envelopeCoverage: 0.98, bodyCoverage: 0.99, crossingRatio: 0.01,
        touchGroups: 2,
      },
    },
    triangleHasPriorAdvance: true,
    trianglePriorAdvanceAtr: 12,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.02,
    rhythmScore: 90,
    sentimentScore: 74,
    certaintyScore: 82,
    score: 86,
    outerEdgeConfirmed: false,
  };
  assert.equal(Engine.isOneHourPostImpulseWedgeIgnition(signal), true);
});

test("mature one-hour long triangle can reset an obsolete post-impulse pressure peak", () => {
  assert.equal(Engine.isMatureOneHourLongTriangleReset({
    interval: "1h",
    foundationTypes: ["triangle"],
    structureShape: "converging-triangle",
    directStructuralBoundary: true,
    consolidationBars: 72,
    structureQuality: 0.84,
    channelInteriorOccupancy: 0.7,
    channelMiddleParticipationRatio: 0.67,
    channelHollowRatio: 0.69,
    channelLongestHollowRun: 18,
    channelSideTransitions: 3,
    triangleHasPriorAdvance: true,
    trianglePriorAdvanceAtr: 12.6,
    hasPivot: true,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.01,
    motherStructureMode: "post-impulse-high-level-rotation",
    motherStructurePosition: 0.82,
    crossedLevel: true,
    openedBeyondTrigger: false,
  }), true);
});

test("a fully traded one-hour mother convergence may break its dynamic edge before the static peak", () => {
  const xrpMotherConvergence = {
    interval: "1h",
    foundationTypes: ["triangle"],
    structureShape: "converging-triangle",
    directStructuralBoundary: true,
    consolidationBars: 122,
    structureQuality: 0.96,
    channelInteriorOccupancy: 0.824,
    channelMiddleParticipationRatio: 0.744,
    channelHollowRatio: 0.522,
    channelLongestHollowRun: 10,
    channelSideTransitions: 4,
    triangleHasPriorAdvance: true,
    trianglePriorAdvanceAtr: 18.46,
    hasPivot: false,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.006,
    motherStructureMode: "shock-formed-mother-box",
    motherStructurePosition: 0.507,
    crossedLevel: true,
    openedBeyondTrigger: false,
    trianglePostSelloffRecovery: false,
    riskStructureShape: null,
    rhythmScore: 85,
    sentimentScore: 62,
  };
  assert.equal(Engine.isMatureOneHourLongTriangleReset(xrpMotherConvergence), true);
  assert.equal(Engine.isMatureOneHourLongTriangleReset({
    ...xrpMotherConvergence,
    channelMiddleParticipationRatio: 0.3,
    channelLongestHollowRun: 34,
    channelSideTransitions: 1,
  }), false);
});

test("long base with a real prior-high and pivot can ignite before a mechanical outer-edge score cutoff", () => {
  assert.equal(Engine.isLongBasePreviousHighIgnition({
    interval: "5m",
    foundationTypes: ["base"],
    auxiliaryTypes: ["previousHigh"],
    hasPivot: true,
    consolidationBars: 60,
    outerEdgeScore: 56,
    platformTouchGroups: 2,
    ceilingTouches: 3,
    horizontalLaunchHasPriorAdvance: true,
    horizontalLaunchPriorAdvanceAtr: 9.79,
    horizontalLaunchQualified: true,
    horizontalLaunchUrgent: false,
    horizontalLaunchInsufficientEdgeDwell: false,
    horizontalLaunchPostSelloffRecovery: false,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.01,
    motherStructureNoise: false,
    crossedLevel: true,
    openedBeyondTrigger: false,
    launchDistancePercent: 5.2,
    orderFlowScore: 78,
  }), true);
});

test("soft boundary tests stay inside a mature triangle until the decisive body breakout", () => {
  assert.equal(Engine.isSoftTestExtendedTriangleBreakout({
    interval: "5m",
    foundationTypes: ["triangle"],
    softTestExtendedTriangle: true,
    directStructuralBoundary: true,
    consolidationBars: 48,
    structureQuality: 0.75,
    channelInteriorOccupancy: 0.76,
    channelMiddleParticipationRatio: 0.69,
    channelHollowRatio: 0.59,
    channelLongestHollowRun: 4,
    channelSideTransitions: 3,
    triangleHasPriorAdvance: true,
    trianglePriorAdvanceAtr: 14.7,
    crossedLevel: true,
    openedBeyondTrigger: false,
  }), true);
});

test("keeps an isolated inflection reclaim as auxiliary evidence instead of a standalone buy", () => {
  const rows = pivotReclaimSeries();
  const fiveMinute = Engine.analyzeTimeframe(rows, { interval: "5m", now: 1_800_000_000_000 });
  const oneMinute = Engine.analyzeTimeframe(rows, { interval: "1m", now: 1_800_000_000_000 });
  assert.equal(fiveMinute.signals.some((signal) => signal.pattern.includes("拐点收复")), false);
  assert.ok(fiveMinute.rejected.some((signal) => (
    signal.pattern.includes("拐点收复")
    && signal.reasons.some((reason) => reason.includes("因果层级未通过"))
  )), JSON.stringify(fiveMinute.rejected));
  assert.equal(oneMinute.signals.some((signal) => signal.pattern.includes("拐点收复")), false);
  assert.ok(oneMinute.rejected.some((signal) => (
    signal.pattern.includes("拐点收复")
    && signal.reasons.some((reason) => reason.includes("1分钟仅保留高确定性横盘起飞或箱体突破"))
  )));
});

test("keeps a shallow pullback relaunch as auxiliary evidence until a mother structure breaks", () => {
  const result = Engine.analyzeTimeframe(pullbackRelaunchSeries(), { interval: "5m", now: 1_800_000_000_000 });
  assert.equal(result.signals.some((signal) => signal.pattern.includes("回踩再点火")), false);
  assert.ok(result.rejected.some((signal) => (
    signal.pattern.includes("回踩再点火")
    && signal.reasons.some((reason) => reason.includes("因果层级未通过"))
  )), JSON.stringify(result.rejected));
});

test("rejects a gap beyond the pre-armed trigger instead of chasing it", () => {
  const rows = horizontalBreakoutSeries();
  rows[rows.length - 1] = candle(rows.length - 1, {
    open: 109,
    close: 113,
    high: 113.4,
    low: 108.7,
    volume: 300,
  });
  const result = Engine.analyzeTimeframe(rows, { interval: "1m", now: 1_800_000_000_000 });
  assert.equal(result.signals.length, 0);
  assert.ok(result.rejected.some((item) => item.reasons.includes("开盘已越过触发线，非从下向上首次突破")));
});

test("marks the base level when a large ignition candle launches directly from consolidation", () => {
  const rows = horizontalBreakoutSeries();
  rows[rows.length - 1] = candle(rows.length - 1, {
    open: 100.45,
    close: 108.2,
    high: 108.7,
    low: 100.3,
    volume: 360,
  });
  const result = Engine.analyzeTimeframe(rows, { interval: "5m", now: 1_800_000_000_000 });
  const signal = result.signals.find((item) => item.pattern.includes("横盘起飞"));
  assert.ok(signal, JSON.stringify({ signals: result.signals, rejected: result.rejected.slice(-6) }));
  assert.ok(signal.price < rows.at(-1).close);
  assert.equal("confirmationClose" in signal, false);
  assert.ok(signal.originDistanceAtr <= 1.5);
});

test("a pre-armed stop remains a buy even if the trigger bar later closes with a long upper wick", () => {
  const rows = horizontalBreakoutSeries();
  rows[rows.length - 1] = candle(rows.length - 1, {
    open: 100.4,
    close: 101.15,
    high: 106.8,
    low: 100.2,
    volume: 260,
  });
  const result = Engine.analyzeTimeframe(rows, { interval: "5m", now: 1_800_000_000_000 });
  assert.ok(result.signals.some((item) => item.index === rows.length - 1));
});

test("ignores the still-open candle to avoid repainting", () => {
  const rows = horizontalBreakoutSeries();
  rows.at(-1).closeTime = 1_900_000_000_000;
  const result = Engine.analyzeTimeframe(rows, { interval: "5m", now: 1_800_000_000_000 });
  assert.equal(result.candles.length, rows.length - 1);
  assert.equal(result.signals.length, 0);
});

test("keeps a valid unbroken structure as a right-edge pre-armed candidate", () => {
  const rows = horizontalBreakoutSeries();
  rows[rows.length - 1] = candle(rows.length - 1, {
    open: 100.4,
    close: 100.52,
    high: 100.76,
    low: 100.2,
    volume: 90,
  });
  const result = Engine.analyzeTimeframe(rows, { interval: "5m", now: 1_800_000_000_000 });
  assert.equal(result.pending.length, 1);
  assert.equal(result.pending[0].status, "pending");
  assert.ok(result.pending[0].triggerPrice > rows.at(-1).high);
});

test("appending a choppy future cannot erase or alter an earlier buy", () => {
  const prefixRows = horizontalBreakoutSeries();
  const prefix = Engine.analyzeTimeframe(prefixRows, { interval: "5m", now: 1_800_000_000_000 });
  const full = Engine.analyzeTimeframe(withChoppyFailure(horizontalBreakoutSeries()), { interval: "5m", now: 1_800_000_000_000 });
  const time = prefixRows.at(-1).time;
  const left = prefix.signals.find((item) => item.time === time);
  const right = full.signals.find((item) => item.time === time);
  assert.ok(left && right);
  assert.deepEqual(
    { status: left.status, level: left.level, triggerPrice: left.triggerPrice, price: left.price, score: left.score, confluence: left.confluence },
    { status: right.status, level: right.level, triggerPrice: right.triggerPrice, price: right.price, score: right.score, confluence: right.confluence },
  );
});

test("does not turn every new high in one continuous impulse into a fresh setup", () => {
  const result = Engine.analyzeTimeframe(withSmoothIgnition(horizontalBreakoutSeries(), 14), { interval: "5m", now: 1_800_000_000_000 });
  assert.equal(result.signals.length, 1);
});

test("one long consolidation zone throttles cyclical internal noise without imposing a hard retry cap", () => {
  const rows = Array.from({ length: 1_200 }, (_, index) => {
    const base = 100 + Math.sin(index / 13) * 0.18 + index * 0.00002;
    return candle(index, {
      open: base - 0.02,
      close: base + 0.02,
      high: base + 0.09,
      low: base - 0.09,
      volume: 100 + (index % 7),
    });
  });
  const result = Engine.analyzeTimeframe(rows, { interval: "5m", now: 1_800_000_000_000 });
  assert.ok(result.signals.length <= 6, JSON.stringify(result.signals.map((item) => ({ time: item.time, level: item.level }))));
  assert.ok(result.rejected.some((item) => item.reasons.some((reason) => (
    reason.includes("同一盘整")
    || reason.includes("假突破")
    || reason.includes("仅作辅助")
    || reason.includes("母结构尚未成熟")
  ))));
});

test("ORDI-style third breakout remains buyable after two stopped and fully rebuilt attempts", () => {
  const rows = ordiStyleThreeAttemptSeries();
  const result = Engine.analyzeTimeframe(rows, { interval: "15m", now: 1_800_000_000_000 });
  assert.ok(result.signals.length >= 3, JSON.stringify({ signals: result.signals, rejected: result.rejected.slice(-10) }));
  const finalBreakout = result.signals.find((item) => item.index === rows.length - 1);
  assert.ok(finalBreakout, JSON.stringify(result.signals));
  assert.match(finalBreakout.pattern, /横盘起飞/);
  assert.match(finalBreakout.pattern, /突破前高/);
});

test("re-arms a mature mother base even when a recent internal trial stopped only a few bars ago", () => {
  const rows = Array.from({ length: 15 }, (_, index) => candle(index, {
    open: 99.6 + index * 0.08,
    close: 99.72 + index * 0.08,
    high: 100.05 + index * 0.08,
    low: 99.25 + index * 0.08,
  }));
  rows[9].low = 98.5;
  const priorSignal = {
    index: 8,
    level: 100,
    triggerPrice: 100,
    stop: 99,
    atrAtDecision: 1,
    consolidationBars: 8,
    confluence: ["relaunch", "previousHigh"],
    aestheticScore: 70,
    patternKey: "relaunch",
  };
  const evaluation = {
    level: 101.4,
    triggerPrice: 101.4,
    stop: 99.1,
    consolidationBars: 34,
    confluence: ["base", "relaunch", "pivot"],
    aestheticScore: 72,
    score: 96,
    patternKey: "base",
  };
  const decision = Engine.structureLifecycleDecision([priorSignal], evaluation, rows, 14, 1);
  assert.equal(decision.reason, "");
  assert.equal(decision.retryMaturity, true);
  assert.match(decision.retryEvidence, /34 根母结构仍有效/);

  const immature = Engine.structureLifecycleDecision(
    [priorSignal],
    { ...evaluation, consolidationBars: 20 },
    rows,
    14,
    1,
  );
  assert.match(immature.reason, /尚未形成新的高质量母平台真实外沿或成熟三角外沿/);
});

test("re-arms the successful outer-edge cross one bar after a stopped first attempt", () => {
  const rows = Array.from({ length: 10 }, (_, index) => candle(index, {
    open: 99.7,
    close: 99.9,
    high: 100.1,
    low: 99.4,
  }));
  rows[8].low = 98.7;
  const priorSignal = {
    index: 7,
    level: 100,
    triggerPrice: 100.04,
    stop: 99,
    atrAtDecision: 1,
    consolidationBars: 20,
    outerEdgeConfirmed: true,
    outerEdgeScore: 66,
    platformTouchGroups: 2,
    confluence: ["base", "previousHigh"],
  };
  const evaluation = {
    level: 100.1,
    triggerPrice: 100.1,
    stop: 99.1,
    consolidationBars: 22,
    outerEdgeConfirmed: true,
    outerEdgeScore: 68,
    platformTouchGroups: 2,
    confluence: ["base", "previousHigh"],
    foundationTypes: ["base"],
    aestheticScore: 62,
    certaintyScore: 68,
    score: 72,
  };
  const decision = Engine.structureLifecycleDecision([priorSignal], evaluation, rows, 9, 1);
  assert.equal(decision.reason, "");
  assert.equal(decision.retryMaturity, true);
  assert.match(decision.retryEvidence, /再次从线下触发/);
});

test("treats a break below the first breakout candle low as a stopped trial for lifecycle re-entry", () => {
  const rows = Array.from({ length: 10 }, (_, index) => candle(index, {
    open: 99.7,
    close: 99.9,
    high: 100.1,
    low: 99.45,
  }));
  rows[7].low = 99.4;
  rows[8].low = 99.2;
  const priorSignal = {
    index: 7,
    level: 100,
    triggerPrice: 100,
    // 仓位止损尚未触发，但突破K低点 99.4 已被打穿，应释放旧结构占位。
    stop: 98.8,
    atrAtDecision: 1,
    consolidationBars: 20,
    outerEdgeConfirmed: true,
    outerEdgeScore: 66,
    platformTouchGroups: 2,
    confluence: ["base", "previousHigh"],
  };
  const evaluation = {
    interval: "1h",
    level: 100.1,
    triggerPrice: 100.1,
    stop: 99,
    consolidationBars: 22,
    outerEdgeConfirmed: true,
    outerEdgeScore: 68,
    platformTouchGroups: 2,
    confluence: ["base", "previousHigh"],
    foundationTypes: ["base"],
    certaintyScore: 72,
    score: 76,
  };
  const decision = Engine.structureLifecycleDecision([priorSignal], evaluation, rows, 9, 1);
  assert.equal(decision.reason, "");
  assert.equal(decision.retryMaturity, true);
  assert.match(decision.retryEvidence, /前次外沿试错已止损/);
});

test("a directional recovery pivot does not inherit fake base triangle or prior-high labels", () => {
  const overLabelled = {
    interval: "15m",
    pattern: "盘整突破 + 三角突破 + 拐点收复 + 趋势线突破 + 突破前高",
    patternKey: "triangle",
    hasPivot: true,
    horizontalLaunchQualified: false,
    horizontalLaunchUrgent: true,
    clusteredCeilingBand: false,
    ceilingAge: 0,
    ceilingTouches: 3,
    horizontalLaunchRetainedAboveHalf: false,
    triangleSelloffAtr: 7.2,
    trianglePriorAdvanceAtr: 5.1,
    originDistanceAtr: 0.37,
    evidence: [],
  };
  assert.equal(Engine.isDirectionalRecoveryPivotOnly(overLabelled), true);
  const cleaned = Engine.normalizeDisplayedStructureLabels(overLabelled);
  assert.equal(cleaned.pattern, "拐点收复");
  assert.equal(cleaned.patternKey, "pivot");
  assert.deepEqual(cleaned.displayConfluence, ["pivot"]);
  assert.ok(cleaned.evidence.some((item) => item.includes("不作为独立三角/横盘标签")));
});

test("a long swing mother box is displayed as a consolidation breakout instead of a fitted triangle", () => {
  const overLabelled = {
    interval: "15m",
    pattern: "盘整突破 + 三角突破 + 趋势线突破 + 突破前高",
    patternKey: "triangle",
    longSwingMotherBox: true,
    evidence: [],
  };
  const cleaned = Engine.normalizeDisplayedStructureLabels(overLabelled);
  assert.equal(cleaned.pattern, "盘整突破");
  assert.equal(cleaned.patternKey, "base");
  assert.deepEqual(cleaned.displayConfluence, ["base"]);
  assert.deepEqual(cleaned.displayFoundationTypes, ["base"]);
  assert.deepEqual(cleaned.displayAuxiliaryTypes, []);
  assert.ok(cleaned.evidence.some((item) => item.includes("完整长箱体前高")));
});

test("replaces a provisional mother edge with the next completed higher outer edge", () => {
  const rows = Array.from({ length: 10 }, (_, index) => candle(index, {
    open: 99.7,
    close: 99.9,
    high: 100.15,
    low: 99.4,
  }));
  const priorSignal = {
    id: "provisional-edge",
    index: 7,
    level: 100,
    triggerPrice: 100,
    stop: 98.8,
    atrAtDecision: 1,
    consolidationBars: 40,
    outerEdgeConfirmed: true,
    outerEdgeScore: 70,
    foundationTypes: ["base"],
  };
  const evaluation = {
    interval: "5m",
    level: 100.35,
    triggerPrice: 100.35,
    stop: 99.1,
    consolidationBars: 53,
    outerEdgeConfirmed: true,
    outerEdgeScore: 77,
    foundationTypes: ["base"],
    orderFlowScore: 86,
    klineVelocity: 1.6,
    openedBeyondTrigger: false,
    riskStructureShape: null,
  };
  const decision = Engine.structureLifecycleDecision([priorSignal], evaluation, rows, 8, 1);
  assert.equal(decision.reason, "");
  assert.equal(decision.replacePriorId, "provisional-edge");
  assert.match(decision.retryEvidence, /撤销提前信号并保留最终起爆K/);
});

test("re-arms an independent 83-bar nested triangle while the prior trade remains active", () => {
  const rows = Array.from({ length: 45 }, (_, index) => candle(index, {
    open: 99.7,
    close: 99.9,
    high: 100.12,
    low: 99.35,
  }));
  const priorSignal = {
    id: "earlier-wave-entry",
    index: 5,
    level: 100,
    triggerPrice: 100,
    stop: 90,
    atrAtDecision: 1,
    consolidationBars: 20,
    foundationTypes: ["base"],
  };
  const evaluation = {
    interval: "5m",
    level: 100.4,
    triggerPrice: 100.4,
    stop: 98.8,
    independentNestedMainWaveStructure: true,
    foundationTypes: ["triangle"],
    directStructuralBoundary: true,
    consolidationBars: 83,
    structureQuality: 0.85,
    channelInteriorOccupancy: 0.67,
    relativeVolume: 1.07,
    score: 88,
    certaintyScore: 88,
  };
  const decision = Engine.structureLifecycleDecision([priorSignal], evaluation, rows, 40, 1);
  assert.equal(decision.reason, "");
  assert.equal(decision.retryMaturity, true);
  assert.match(decision.retryEvidence, /独立形成 83 根成熟三角/);
});

test("a new A+ triangle edge re-enters immediately after a stop without a fixed candle wait", () => {
  const rows = Array.from({ length: 10 }, (_, index) => candle(index, {
    open: 99.7,
    close: 99.9,
    high: 100.1,
    low: 99.4,
  }));
  rows[8].low = 98.7;
  const priorSignal = {
    index: 7,
    level: 100,
    triggerPrice: 100,
    stop: 99,
    atrAtDecision: 1,
    consolidationBars: 20,
    outerEdgeConfirmed: false,
    confluence: ["relaunch", "previousHigh"],
  };
  const evaluation = {
    interval: "15m",
    level: 100.6,
    triggerPrice: 100.6,
    stop: 99.2,
    crossedLevel: true,
    openedBeyondTrigger: false,
    consolidationBars: 31,
    confluence: ["triangle", "previousHigh"],
    foundationTypes: ["triangle"],
    auxiliaryTypes: ["previousHigh"],
    structureShape: "ascending-triangle",
    matureTriangleOuterEdge: true,
    directStructuralBoundary: true,
    triangleHasPriorAdvance: true,
    trianglePostSelloffRecovery: false,
    motherStructureNoise: false,
    riskStructureShape: null,
    aestheticScore: 80,
    rhythmScore: 76,
    sentimentScore: 72,
    certaintyScore: 93,
    score: 94,
    executionHierarchy: {
      permit: true,
      primaryFoundation: "mature-triangle-outer-edge",
    },
  };
  const decision = Engine.structureLifecycleDecision([priorSignal], evaluation, rows, 9, 1);
  assert.equal(decision.reason, "");
  assert.equal(decision.retryMaturity, true);
  assert.match(decision.retryEvidence, /按新机会立即重入/);
});

test("an elite active-wave outer platform can offset a marginal rhythm score without waiting", () => {
  const rows = Array.from({ length: 10 }, (_, index) => candle(index, {
    open: 99.7,
    close: 99.9,
    high: 100.1,
    low: 99.4,
  }));
  rows[8].low = 98.7;
  const priorSignal = {
    index: 7,
    level: 100,
    triggerPrice: 101,
    stop: 99,
    atrAtDecision: 1,
    consolidationBars: 18,
    confluence: ["base", "previousHigh"],
  };
  const evaluation = {
    interval: "15m",
    level: 100.6,
    triggerPrice: 100.6,
    stop: 99.2,
    crossedLevel: true,
    openedBeyondTrigger: false,
    consolidationBars: 29,
    outerEdgeConfirmed: true,
    outerEdgeScore: 88,
    platformTouchGroups: 3,
    confluence: ["base", "triangle", "previousHigh"],
    foundationTypes: ["base", "triangle"],
    horizontalLaunchHasPriorAdvance: true,
    horizontalLaunchPostSelloffRecovery: false,
    trianglePostSelloffRecovery: false,
    motherStructureNoise: false,
    riskStructureShape: null,
    mainWaveStage: "active",
    aestheticScore: 76,
    rhythmScore: 67,
    sentimentScore: 70,
    orderFlowScore: 60,
    klineVelocity: 1.1,
    certaintyScore: 96,
    score: 87,
    executionHierarchy: {
      permit: true,
      primaryFoundation: "mother-platform-breakout",
    },
  };
  const decision = Engine.structureLifecycleDecision([priorSignal], evaluation, rows, 9, 1);
  assert.equal(decision.reason, "");
  assert.equal(decision.retryMaturity, true);
  assert.match(decision.retryEvidence, /独立母平台结构重置/);
});

test("a long high-certainty base plus auxiliary trendline can re-arm near a robust rebuild boundary", () => {
  const rows = Array.from({ length: 52 }, (_, index) => candle(index, {
    open: 99.5,
    close: 99.8,
    high: 100.1,
    low: 99.25,
  }));
  rows[32].low = 98.6;
  const priorSignal = {
    index: 30,
    level: 100,
    triggerPrice: 100,
    stop: 99,
    atrAtDecision: 1,
    consolidationBars: 31,
    confluence: ["base", "previousHigh"],
    aestheticScore: 70,
  };
  const evaluation = {
    level: 100.2,
    triggerPrice: 100.2,
    stop: 99.1,
    consolidationBars: 61,
    confluence: ["base", "trendline", "previousHigh"],
    foundationTypes: ["base"],
    aestheticScore: 64,
    certaintyScore: 74,
    score: 90,
  };
  const decision = Engine.structureLifecycleDecision([priorSignal], evaluation, rows, 51, 1);
  assert.equal(decision.reason, "");
  assert.equal(decision.retryMaturity, true);
  assert.match(decision.retryEvidence, /高确定性母结构/);
});

test("keeps separate structural breakouts inside one leader main wave", () => {
  const result = Engine.analyzeTimeframe(twoStageLeaderSeries(), { interval: "5m", now: 1_800_000_000_000 });
  assert.ok(result.signals.length >= 2, JSON.stringify(result.signals));
  assert.ok(result.signals.at(-1).pattern.includes("横盘起飞"));
  assert.ok(result.signals.at(-1).confluence.length >= 2);
});

test("downgrades a live low-timeframe candidate when higher timeframes do not align", () => {
  const lower = Engine.analyzeTimeframe(horizontalBreakoutSeries(), { interval: "5m", now: 1_800_000_000_000 });
  assert.ok(lower.signals.length >= 1);
  const bearishContext = (interval) => ({
    interval,
    candles: [{ time: 1_600_000_000_000, close: 90 }],
    indicators: { ema90: [100] },
    signals: [],
    rejected: [],
    regime: { bullish: false, strong: false, label: "禁止追多" },
    stats: { signalCount: 0, rejectedCount: 0, lastPrice: 90 },
  });
  const gated = Engine.applyContextGates([
    lower,
    bearishContext("1h"),
    bearishContext("4h"),
    bearishContext("1d"),
  ]);
  const gatedLower = gated.find((item) => item.interval === "5m");
  assert.equal(gatedLower.signals.length, 0);
  assert.equal(gatedLower.pending.length, 0);
  assert.ok(gatedLower.rejected.some((item) => item.reasons.includes("大周期未共振")));
});

test("rejects a small-timeframe trigger that has no nearby higher-timeframe setup", () => {
  const lower = Engine.analyzeTimeframe(horizontalBreakoutSeries(), { interval: "5m", now: 1_800_000_000_000 });
  const bullishContext = (interval) => ({
    interval,
    candles: [{ time: 1_600_000_000_000, close: 110 }],
    indicators: { ema90: [100] },
    signals: [],
    pending: [],
    rejected: [],
    regime: { bullish: true, strong: true, label: "主升环境" },
    stats: { signalCount: 0, pendingCount: 0, rejectedCount: 0, lastPrice: 110 },
  });
  const gated = Engine.applyContextGates([
    lower,
    bullishContext("1h"),
    bullishContext("4h"),
    bullishContext("1d"),
  ]);
  const gatedLower = gated.find((item) => item.interval === "5m");
  assert.equal(gatedLower.signals.length, 0);
  assert.equal(gatedLower.pending.length, 0);
  assert.ok(gatedLower.rejected.some((item) => item.reasons.includes("小周期缺少上级结构锚点")));
});

test("fabricated five-minute metadata cannot be promoted by a real one-minute A+ child", () => {
  const targetTime = Date.parse("2025-02-22T12:17:00Z");
  const parentTime = Date.parse("2025-02-22T12:15:00Z");
  const oneMinute = Engine.analyzeTimeframe(piOneMinutePostImpulseHorizontalLaunchSeries(), {
    interval: "1m",
    now: targetTime + 2 * 60_000,
  });
  const childBuy = oneMinute.signals.find((item) => item.time === targetTime);
  assert.ok(childBuy, JSON.stringify(oneMinute.rejected.slice(-5), null, 2));

  const parentCandles = Array.from({ length: 32 }, (_, index) => {
    const time = parentTime - (31 - index) * 5 * 60_000;
    const close = 0.952 + index * 0.0011;
    return candle(index, {
      time,
      closeTime: time + 5 * 60_000 - 1,
      open: close - 0.0004,
      high: close + 0.002,
      low: close - 0.0022,
      close,
      volume: 100,
    });
  });
  parentCandles[31] = candle(31, {
    time: parentTime,
    closeTime: parentTime + 5 * 60_000 - 1,
    open: 0.9898,
    high: 1.008,
    low: 0.9892,
    close: 1.0038,
    volume: 245,
  });
  const parentSignal = {
    id: "5m-pi-2015-relaunch",
    interval: "5m",
    time: parentTime,
    decisionTime: parentTime,
    index: 31,
    status: "buy",
    pattern: "回踩再点火 + 突破前高",
    patternKey: "relaunch",
    foundationTypes: ["relaunch"],
    auxiliaryTypes: ["previousHigh"],
    confluence: ["relaunch", "previousHigh"],
    hasPivot: true,
    triggerPrice: 0.9928,
    previousHighLevel: 0.9928,
    level: 0.9928,
    stop: 0.972,
    consolidationBars: 16,
    horizontalLaunchHasPriorAdvance: true,
    horizontalLaunchPostSelloffRecovery: false,
    motherStructureNoise: false,
    oneMinuteMotherBoxNoise: false,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.001,
    riskStructureShape: null,
    launchDistancePercent: 0.31,
    structuralRiskPercent: 2.1,
    score: 68,
    aestheticScore: 68,
    aestheticGrade: "A",
    certaintyScore: 70,
    rhythmScore: 70,
    sentimentScore: 64,
    orderFlowScore: 52,
    relativeVolume: 1.15,
    reasons: [],
    evidence: ["5分钟自身已有回踩平台与前高首次上穿"],
  };
  const fiveMinute = {
    interval: "5m",
    candles: parentCandles,
    indicators: { ema90: Array(32).fill(0.94), atr: Array(32).fill(0.01) },
    signals: [parentSignal],
    pending: [],
    rejected: [],
    regime: { bullish: true, strong: true, label: "主升环境" },
    stats: { signalCount: 1, pendingCount: 0, rejectedCount: 0, lastPrice: 1.0038 },
  };

  const gated = Engine.applyContextGates([oneMinute, fiveMinute], [], {
    preselectedLeader: true,
    mainWaveStage: "active",
  });
  const gatedParent = gated.find((item) => item.interval === "5m");
  assert.equal(gatedParent.signals.length, 0);
  assert.ok(gatedParent.rejected.some((item) => item.reasons.includes("大周期证据不足")));

  const withoutChild = Engine.applyContextGates([
    { ...oneMinute, signals: [] },
    fiveMinute,
  ], [], { preselectedLeader: true, mainWaveStage: "active" });
  assert.equal(withoutChild.find((item) => item.interval === "5m").signals.length, 0);
});

test("fabricated fifteen-minute metadata cannot be promoted by a five-minute child", () => {
  const parentTime = Date.parse("2025-02-22T12:15:00Z");
  const childTime = parentTime + 5 * 60_000;
  const childCandles = Array.from({ length: 8 }, (_, index) => candle(index, {
    time: childTime - (7 - index) * 5 * 60_000,
    closeTime: childTime - (7 - index) * 5 * 60_000 + 5 * 60_000 - 1,
    open: 99.4 + index * 0.08,
    high: 99.8 + index * 0.08,
    low: 99.1 + index * 0.08,
    close: 99.6 + index * 0.08,
    volume: 100,
  }));
  childCandles[7] = candle(7, {
    time: childTime,
    closeTime: childTime + 5 * 60_000 - 1,
    open: 100.2,
    high: 102.2,
    low: 100,
    close: 101.8,
    volume: 220,
  });
  const childSignal = {
    id: "5m-a-plus-ignition",
    interval: "5m",
    time: childTime,
    decisionTime: childTime,
    index: 7,
    status: "buy",
    pattern: "横盘起飞 + 突破前高",
    patternKey: "base",
    foundationTypes: ["base"],
    auxiliaryTypes: ["previousHigh"],
    confluence: ["base", "previousHigh"],
    triggerPrice: 101.2,
    level: 101.2,
    stop: 98.8,
    outerEdgeConfirmed: true,
    outerEdgeScore: 90,
    consolidationBars: 40,
    ceilingAge: 8,
    platformTouchGroups: 3,
    ceilingTouches: 3,
    launchDistancePercent: 0.8,
    structuralRiskPercent: 2.4,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.12,
    motherStructureNoise: false,
    oneMinuteMotherBoxNoise: false,
    riskStructureShape: null,
    score: 90,
    aestheticScore: 86,
    aestheticGrade: "A+",
    certaintyScore: 92,
    rhythmScore: 84,
    sentimentScore: 78,
    evidence: ["5分钟A+母平台真实突破"],
    reasons: [],
  };
  const lower = {
    interval: "5m",
    candles: childCandles,
    indicators: { ema90: Array(8).fill(96), atr: Array(8).fill(1) },
    signals: [childSignal], pending: [], rejected: [],
    regime: { bullish: true, strong: true, label: "主升环境" },
    stats: { signalCount: 1, pendingCount: 0, rejectedCount: 0, lastPrice: 101.8 },
  };
  const parentCandles = Array.from({ length: 40 }, (_, index) => candle(index, {
    time: parentTime - (39 - index) * 15 * 60_000,
    closeTime: parentTime - (39 - index) * 15 * 60_000 + 15 * 60_000 - 1,
    open: 96 + index * 0.09,
    high: 96.5 + index * 0.09,
    low: 95.7 + index * 0.09,
    close: 96.2 + index * 0.09,
    volume: 100,
  }));
  parentCandles[39] = candle(39, {
    time: parentTime,
    closeTime: parentTime + 15 * 60_000 - 1,
    open: 100,
    high: 103,
    low: 99.7,
    close: 102.4,
    volume: 300,
  });
  const parentSignal = {
    id: "15m-parent-breakout",
    interval: "15m",
    time: parentTime,
    decisionTime: parentTime,
    index: 39,
    status: "buy",
    pattern: "横盘起飞 + 突破前高",
    patternKey: "base",
    foundationTypes: ["base"],
    auxiliaryTypes: ["previousHigh"],
    confluence: ["base", "previousHigh"],
    triggerPrice: 101,
    previousHighLevel: 101,
    level: 101,
    stop: 98.5,
    consolidationBars: 24,
    horizontalLaunchHasPriorAdvance: true,
    horizontalLaunchPostSelloffRecovery: false,
    motherStructureNoise: false,
    oneMinuteMotherBoxNoise: false,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.08,
    riskStructureShape: null,
    launchDistancePercent: 1,
    structuralRiskPercent: 2.5,
    score: 72,
    aestheticScore: 72,
    aestheticGrade: "A",
    certaintyScore: 74,
    rhythmScore: 72,
    sentimentScore: 66,
    reasons: [],
    evidence: ["15分钟自身已有前置上推和成熟平台"],
  };
  const parent = {
    interval: "15m",
    candles: parentCandles,
    indicators: { ema90: Array(40).fill(94), atr: Array(40).fill(1) },
    signals: [parentSignal], pending: [], rejected: [],
    regime: { bullish: true, strong: true, label: "主升环境" },
    stats: { signalCount: 1, pendingCount: 0, rejectedCount: 0, lastPrice: 102.4 },
  };

  const gatedParent = Engine.applyContextGates([lower, parent], [], {
    preselectedLeader: true,
    mainWaveStage: "active",
  }).find((item) => item.interval === "15m");
  assert.equal(gatedParent.signals.length, 0);
  assert.ok(gatedParent.rejected.some((item) => item.reasons.includes("大周期证据不足")));

  const withoutChild = Engine.applyContextGates([
    { ...lower, signals: [] },
    parent,
  ], [], { preselectedLeader: true, mainWaveStage: "active" });
  assert.equal(withoutChild.find((item) => item.interval === "15m").signals.length, 0);
});

test("adjacent-timeframe promotion requires causal parent geometry instead of fabricated metadata", () => {
  const pairs = [
    ["15m", "1h", 15 * 60_000, 60 * 60_000],
    ["1h", "4h", 60 * 60_000, 4 * 60 * 60_000],
    ["4h", "1d", 4 * 60 * 60_000, 24 * 60 * 60_000],
  ];
  const start = Date.parse("2025-02-20T00:00:00Z");
  const makeSignal = (interval, time, id, parent = false) => ({
    id,
    interval,
    time,
    decisionTime: time,
    index: 0,
    status: "buy",
    pattern: "横盘起飞 + 突破前高",
    patternKey: "base",
    foundationTypes: ["base"],
    auxiliaryTypes: ["previousHigh"],
    confluence: ["base", "previousHigh"],
    triggerPrice: parent ? 101 : 101.2,
    previousHighLevel: parent ? 101 : 101.2,
    level: parent ? 101 : 101.2,
    stop: 98,
    outerEdgeConfirmed: true,
    outerEdgeScore: 92,
    consolidationBars: 42,
    ceilingAge: 8,
    platformTouchGroups: 3,
    ceilingTouches: 3,
    horizontalLaunchHasPriorAdvance: true,
    horizontalLaunchPostSelloffRecovery: false,
    launchDistancePercent: 1,
    structuralRiskPercent: 3,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.1,
    motherStructureNoise: false,
    oneMinuteMotherBoxNoise: false,
    riskStructureShape: null,
    score: 92,
    aestheticScore: 88,
    aestheticGrade: "A+",
    certaintyScore: 94,
    rhythmScore: 86,
    sentimentScore: 80,
    evidence: [`${interval}成熟平台`],
    reasons: [],
  });
  const makeResult = (interval, intervalMs, signal, parent = false) => ({
    interval,
    candles: [candle(0, {
      time: signal.time,
      closeTime: signal.time + intervalMs - 1,
      open: 100,
      high: 103,
      low: 99,
      close: 102,
      volume: 300,
    })],
    indicators: { ema90: [95], atr: [1] },
    signals: [signal], pending: [], rejected: [],
    regime: { bullish: true, strong: true, label: parent ? "父周期主升" : "子周期起爆" },
    stats: { signalCount: 1, pendingCount: 0, rejectedCount: 0, lastPrice: 102 },
  });

  pairs.forEach(([childInterval, parentInterval, childMs, parentMs], pairIndex) => {
    const parentTime = start + pairIndex * 2 * 24 * 60 * 60_000;
    const childTime = parentTime + childMs;
    const childSignal = makeSignal(childInterval, childTime, `${childInterval}-a-plus`);
    const parentSignal = makeSignal(parentInterval, parentTime, `${parentInterval}-parent`, true);
    const lower = makeResult(childInterval, childMs, childSignal);
    const parent = makeResult(parentInterval, parentMs, parentSignal, true);
    const gatedParent = Engine.applyContextGates([lower, parent], [], {
      preselectedLeader: true,
      mainWaveStage: "active",
    }).find((item) => item.interval === parentInterval);
    const promoted = gatedParent.signals[0];
    assert.ok(promoted, `${childInterval} -> ${parentInterval}`);
    assert.equal(promoted.decisionTime, parentTime);
    assert.equal(promoted.crossFrameDirection, undefined);
    assert.equal(promoted.lowerTimeframeTriggerId, undefined);
  });
});

test("does not use retrospective performance to override a context veto", () => {
  const lower = Engine.analyzeTimeframe(horizontalBreakoutSeries(), { interval: "5m", now: 1_800_000_000_000 });
  const bearishContext = (interval) => ({
    interval,
    candles: [{ time: 1_600_000_000_000, close: 90 }],
    indicators: { ema90: [100], atr: [1] },
    signals: [], pending: [], rejected: [],
    regime: { bullish: false, strong: false, label: "禁止追多" },
    stats: { signalCount: 0, pendingCount: 0, rejectedCount: 0, lastPrice: 90 },
  });
  const gated = Engine.applyContextGates([lower, bearishContext("1h"), bearishContext("4h"), bearishContext("1d")]);
  const gatedLower = gated.find((item) => item.interval === "5m");
  assert.equal(gatedLower.signals.length, 0);
  assert.ok(gatedLower.rejected.some((item) => item.reasons.includes("大周期未共振")));
});

test("one-minute and four-hour entries must be causal A+ setups", () => {
  const time = 2_000;
  const result = (interval, signal = null) => ({
    interval,
    candles: Array.from({ length: 20 }, (_, index) => ({ time: index * 50, closeTime: index * 50 + 40, close: 110 + index, low: 109 + index })),
    indicators: { ema90: Array(20).fill(100), atr: Array(20).fill(1) },
    signals: signal ? [{ id: `${interval}-signal`, interval, time, index: 19, status: "buy", pattern: "突破前高", patternKey: "previousHigh", level: 110, price: 110.1, stop: 108, score: 80, rhythmScore: 55, sentimentScore: 55, aestheticScore: 58, aestheticGrade: "B", confluence: ["previousHigh"], reasons: [], evidence: [] }] : [],
    pending: [], rejected: [],
    regime: { bullish: true, strong: true, label: "主升环境" },
    stats: { signalCount: signal ? 1 : 0, pendingCount: 0, rejectedCount: 0, lastPrice: 129 },
  });
  const oneMinute = result("1m", true);
  const fiveMinuteAnchor = result("5m", true);
  fiveMinuteAnchor.signals[0].time = time;
  const fourHour = result("4h", true);
  const gated = Engine.applyContextGates([
    oneMinute, fiveMinuteAnchor, result("15m"), result("1h"), fourHour, result("1d"),
  ]);
  const gated1m = gated.find((item) => item.interval === "1m");
  const gated4h = gated.find((item) => item.interval === "4h");
  assert.equal(gated1m.signals.length, 0);
  assert.ok(gated1m.rejected.some((item) => item.reasons.includes("1分钟仅保留高确定性横盘起飞或箱体突破，其他结构全部过滤")));
  assert.equal(gated4h.signals.length, 0);
  assert.ok(gated4h.rejected.some((item) => item.reasons.includes("4小时仅执行 A+ 大结构")));
});

test("one-minute structure whitelist accepts mature horizontal launches and A+ box prototypes", () => {
  const valid = {
    foundationTypes: ["base"],
    auxiliaryTypes: ["previousHigh"],
    patternKey: "base",
    hasPivot: false,
    structureShape: null,
    consolidationBars: 44,
    outerEdgeConfirmed: true,
    outerEdgeScore: 75,
    ceilingTouches: 3,
    aestheticScore: 82,
  };
  assert.equal(Engine.isOneMinuteHorizontalBase(valid), true);
  assert.equal(Engine.isOneMinuteHorizontalBase({ ...valid, foundationTypes: ["triangle"] }), false);
  assert.equal(Engine.isOneMinuteHorizontalBase({ ...valid, foundationTypes: ["base", "triangle"] }), false);
  assert.equal(Engine.isOneMinuteHorizontalBase({ ...valid, auxiliaryTypes: ["trendline"] }), false);
  assert.equal(Engine.isOneMinuteHorizontalBase({ ...valid, hasPivot: true }), true);
  assert.equal(Engine.isOneMinuteHorizontalBase({ ...valid, consolidationBars: 24 }), false);
  assert.equal(Engine.isOneMinuteHorizontalBase({ ...valid, outerEdgeConfirmed: false }), false);
  assert.equal(Engine.isOneMinuteHorizontalBase({ manualCandleSelection: true }), true);
  assert.equal(Engine.isOneMinuteHorizontalBase({
    manualDecision: "confirmed",
    manualStructureTags: ["horizontalLaunch"],
  }), true);
  const validBox = {
    ...valid,
    foundationTypes: ["base", "relaunch"],
    consolidationBars: 29,
    outerEdgeScore: 88,
    ceilingTouches: 2,
    ceilingAge: 5,
    aestheticScore: 66,
    certaintyScore: 85,
    rhythmScore: 69,
    score: 81,
    launchDistancePercent: 2,
  };
  assert.equal(Engine.isOneMinuteHorizontalBase(validBox), true);
  assert.equal(Engine.isOneMinuteHorizontalBase({ ...validBox, outerEdgeScore: 78 }), false);
  assert.equal(Engine.isOneMinuteHorizontalBase({
    manualDecision: "confirmed",
    manualStructureTags: ["box"],
  }), true);
});

test("one-minute final whitelist also filters restored automatic feedback noise", () => {
  const valid = {
    id: "valid-base",
    interval: "1m",
    time: 1000,
    index: 0,
    status: "buy",
    foundationTypes: ["base"],
    auxiliaryTypes: ["previousHigh"],
    patternKey: "base",
    hasPivot: false,
    consolidationBars: 46,
    outerEdgeConfirmed: true,
    outerEdgeScore: 74,
    ceilingTouches: 3,
    aestheticScore: 84,
  };
  const automaticNoise = {
    ...valid,
    id: "old-confirmed-triangle",
    time: 2000,
    index: 1,
    foundationTypes: ["triangle"],
    patternKey: "triangle",
    manualDecision: "confirmed",
  };
  const validBox = {
    ...valid,
    id: "valid-box",
    time: 1500,
    index: 2,
    foundationTypes: ["base", "relaunch"],
    consolidationBars: 31,
    outerEdgeScore: 87,
    ceilingTouches: 2,
    ceilingAge: 6,
    aestheticScore: 68,
    certaintyScore: 84,
    rhythmScore: 70,
    score: 82,
    launchDistancePercent: 1.8,
  };
  const result = Engine.enforceIntervalStructurePolicy({
    interval: "1m",
    signals: [valid, validBox, automaticNoise],
    pending: [],
    rejected: [],
    structures: [{ id: "legacy-one-minute-wedge", structureShape: "falling-wedge" }],
    stats: {},
  });
  assert.deepEqual(result.signals.map((item) => item.id), ["valid-base", "valid-box"]);
  assert.deepEqual(result.structures, []);
  assert.equal(result.rejected[0].id, "old-confirmed-triangle");
  assert.ok(result.rejected[0].reasons.includes("1分钟最终白名单：非高确定性横盘起飞或箱体突破买点不显示"));
});

test("one-minute analysis never emits automatic triangle, wedge or trendline structures", () => {
  const rows = longConvergenceBreakoutSeries({ risingLower: false });
  const oneMinute = Engine.analyzeTimeframe(rows, { interval: "1m", now: 1_800_000_000_000 });
  const fiveMinute = Engine.analyzeTimeframe(rows, { interval: "5m", now: 1_800_000_000_000 });
  assert.deepEqual(oneMinute.structures, []);
  assert.equal(oneMinute.signals.some((item) => item.trendline || item.triangleLines), false);
  assert.ok(fiveMinute.structures.some((item) => item.structureShape === "falling-wedge"), JSON.stringify(fiveMinute.structures));
});

test("primary execution frames require mature causal structure evidence instead of trading consolidation noise", () => {
  const time = 2_000;
  const context = (interval, signals = []) => ({
    interval,
    candles: Array.from({ length: 20 }, (_, index) => ({
      time: index * 50,
      closeTime: index * 50 + 40,
      close: 110 + index,
      low: 109 + index,
    })),
    indicators: { ema90: Array(20).fill(100), atr: Array(20).fill(1) },
    signals,
    pending: [],
    rejected: [],
    regime: { bullish: true, strong: true, label: "主升环境" },
    stats: { signalCount: signals.length, pendingCount: 0, rejectedCount: 0, lastPrice: 129 },
  });
  const noisySignal = {
    id: "15m-noisy",
    interval: "15m",
    time,
    decisionTime: time,
    status: "buy",
    pattern: "横盘起飞 + 突破前高",
    level: 110,
    price: 110.1,
    stop: 108,
    score: 78,
    rhythmScore: 58,
    sentimentScore: 57,
    aestheticScore: 61,
    aestheticGrade: "B",
    confluence: ["base", "previousHigh"],
    reasons: [],
    evidence: [],
  };
  const gated = Engine.applyContextGates([
    context("15m", [noisySignal]),
    context("1h"),
    context("4h"),
    context("1d"),
  ]);
  const primary = gated.find((item) => item.interval === "15m");
  assert.equal(primary.signals.length, 0);
  assert.ok(primary.rejected.some((item) => item.reasons.some((reason) => reason.includes("仅保留高确定性起爆"))));
});

test("high-certainty gate keeps true outer-box and mature trendline-assisted ignitions only", () => {
  const common = {
    aestheticScore: 80,
    certaintyScore: 88,
    rhythmScore: 82,
    sentimentScore: 76,
    consolidationBars: 42,
    hasPivot: false,
  };
  assert.equal(Engine.isHighCertaintyEntry({
    ...common,
    foundationTypes: [],
    auxiliaryTypes: ["trendline", "previousHigh"],
  }), false);
  assert.equal(Engine.isHighCertaintyEntry({
    ...common,
    aestheticScore: 66,
    certaintyScore: 81,
    rhythmScore: 68,
    sentimentScore: 64,
    consolidationBars: 34,
    foundationTypes: ["base", "relaunch"],
    auxiliaryTypes: [],
    hasPivot: true,
  }), false);
  assert.equal(Engine.isHighCertaintyEntry({
    ...common,
    aestheticScore: 64,
    certaintyScore: 74,
    rhythmScore: 73,
    sentimentScore: 61,
    consolidationBars: 61,
    foundationTypes: ["base"],
    auxiliaryTypes: ["trendline", "previousHigh"],
    outerEdgeConfirmed: true,
    outerEdgeScore: 78,
    ceilingAge: 8,
    platformTouchGroups: 3,
    horizontalLaunchHasPriorAdvance: true,
    launchDistancePercent: 2,
  }), true);
  assert.equal(Engine.isHighCertaintyEntry({
    interval: "15m",
    foundationTypes: ["base"],
    auxiliaryTypes: [],
    consolidationBars: 24,
    outerEdgeConfirmed: true,
    outerEdgeScore: 68,
    ceilingAge: 6,
    platformTouchGroups: 1,
    launchDistancePercent: 2.4,
    aestheticScore: 58,
    certaintyScore: 62,
    rhythmScore: 57,
    sentimentScore: 48,
  }), true, "confirmed platform edge must not depend on a trendline or nearby green candle");
});

test("trendline geometry can select candle bodies while remaining structural-only", () => {
  const rows = longConvergenceBreakoutSeries();
  const result = Engine.analyzeTimeframe(rows, { interval: "4h", now: 1_800_000_000_000 });
  const structural = [...result.signals, ...result.rejected]
    .find((item) => item.triangleLines && item.trendline?.anchorMode);
  assert.ok(structural, JSON.stringify(result.signals.slice(-4)));
  assert.match(structural.trendline.anchorMode, /^(body|wick)$/);
  assert.ok(structural.foundationTypes.includes("triangle"));
});

test("AKE-style four-hour convergence permits the precise 17:00 one-hour trigger", () => {
  const decisionTime = Date.parse("2026-08-13T09:00:00Z");
  const anchorTime = decisionTime - 60 * 60_000;
  const makeResult = (interval, candles, signals = [], rejected = []) => ({
    interval,
    candles,
    indicators: { ema90: candles.map(() => 90), atr: candles.map(() => 1) },
    signals,
    pending: [],
    rejected,
    regime: { bullish: true, strong: true, label: "主升环境" },
    stats: { signalCount: signals.length, pendingCount: 0, rejectedCount: rejected.length, lastPrice: candles.at(-1).close },
  });
  const anchor = {
    id: "ake-4h-convergence",
    time: anchorTime,
    decisionTime: anchorTime,
    index: 1,
    interval: "4h",
    status: "buy",
    pattern: "三角突破 + 趋势线突破",
    foundationTypes: ["triangle"],
    auxiliaryTypes: ["trendline"],
    confluence: ["triangle", "trendline"],
    hasPivot: false,
    triggerPrice: 100,
    level: 99.9,
    price: 100.1,
    stop: 95,
    consolidationBars: 97,
    aestheticScore: 81,
    aestheticGrade: "A+",
    certaintyScore: 87,
    rhythmScore: 95,
    sentimentScore: 76,
    orderFlowScore: 10,
    score: 90,
    reasons: [],
    evidence: [],
  };
  const local = {
    id: "ake-1h-17",
    time: decisionTime,
    decisionTime,
    index: 1,
    interval: "1h",
    status: "filtered",
    pattern: "三角突破 + 趋势线突破",
    foundationTypes: ["triangle"],
    auxiliaryTypes: ["trendline"],
    confluence: ["triangle", "trendline"],
    hasPivot: false,
    triggerPrice: 105,
    level: 104.9,
    price: 105.08,
    stop: 101,
    consolidationBars: 162,
    aestheticScore: 48,
    aestheticGrade: "C",
    certaintyScore: 52,
    rhythmScore: 50,
    sentimentScore: 17,
    orderFlowScore: 75,
    score: 45,
    reasons: ["母结构尚未成熟：盘整、压缩或贴线蓄力不足"],
    evidence: [],
  };
  const oneHourCandles = [
    { time: anchorTime, closeTime: decisionTime - 1, open: 100, high: 104.5, low: 99.8, close: 104 },
    { time: decisionTime, closeTime: decisionTime + 60 * 60_000 - 1, open: 104, high: 106, low: 103.8, close: 105.8 },
  ];
  const fourHourCandles = [
    { time: anchorTime - 4 * 60 * 60_000, closeTime: anchorTime - 1, open: 95, high: 101, low: 94, close: 100 },
    { time: anchorTime, closeTime: anchorTime + 4 * 60 * 60_000 - 1, open: 99, high: 108, low: 98, close: 107 },
  ];
  const dailyCandles = [{ time: anchorTime - 24 * 60 * 60_000, closeTime: anchorTime - 1, open: 95, high: 101, low: 94, close: 100 }];
  const gated = Engine.applyContextGates([
    makeResult("1h", oneHourCandles, [], [local]),
    makeResult("4h", fourHourCandles, [anchor]),
    makeResult("1d", dailyCandles),
  ]);
  const oneHour = gated.find((item) => item.interval === "1h");
  const precise = oneHour.signals.find((item) => item.time === decisionTime);
  assert.ok(precise, JSON.stringify(oneHour));
  assert.equal(precise.crossFramePrecision, true);
  assert.equal(precise.higherTimeframeAnchor, "4h");
  assert.equal(precise.status, "buy");
  const fourHour = gated.find((item) => item.interval === "4h");
  assert.equal(fourHour.signals.length, 1, "a recognized four-hour convergence breakout is itself a B point");
  assert.equal(fourHour.rejected.length, 0);

  const bearishMarketCandle = [{
    time: anchorTime - 60 * 60_000,
    closeTime: decisionTime - 1,
    open: 82,
    high: 83,
    low: 79,
    close: 80,
  }];
  const withWeakBtc = Engine.applyContextGates([
    makeResult("1h", oneHourCandles, [], [local]),
    makeResult("4h", fourHourCandles, [anchor]),
    makeResult("1d", dailyCandles),
  ], [
    makeResult("1h", bearishMarketCandle),
    makeResult("4h", bearishMarketCandle),
  ]);
  const independentLeader = withWeakBtc.find((item) => item.interval === "1h").signals
    .find((item) => item.time === decisionTime);
  assert.ok(independentLeader, JSON.stringify(withWeakBtc));
  assert.equal(independentLeader.marketEmotion, "BTC 背景逆风 0/2 · 龙头独立判断");

  const futureAnchor = { ...anchor, id: "future-anchor", time: decisionTime + 1, decisionTime: decisionTime + 1 };
  const [withoutFuturePermit] = Engine.applyContextGates([
    makeResult("1h", oneHourCandles, [], [local]),
    makeResult("4h", fourHourCandles, [futureAnchor]),
    makeResult("1d", dailyCandles),
  ]);
  assert.equal(withoutFuturePermit.signals.length, 0, "决策时刻之后才出现的4小时锚点不得反向升级买点");
});

test("two closed bearish BTC frames remain background-only for a preselected leader", () => {
  const decisionTime = 2_000;
  const frame = (interval, bullish, signals = []) => ({
    interval,
    candles: Array.from({ length: 20 }, (_, index) => ({
      time: index * 50,
      closeTime: index * 50 + 40,
      close: bullish ? 120 + index : 80 - index * 0.1,
      low: bullish ? 119 + index : 79 - index * 0.1,
    })),
    indicators: { ema90: Array(20).fill(100), atr: Array(20).fill(1) },
    signals,
    pending: [],
    rejected: [],
    regime: { bullish, strong: bullish, label: bullish ? "主升环境" : "禁止追多" },
    stats: { signalCount: signals.length, pendingCount: 0, rejectedCount: 0, lastPrice: bullish ? 139 : 78 },
  });
  const signal = {
    id: "15m-valid",
    interval: "15m",
    time: decisionTime,
    decisionTime,
    status: "buy",
    pattern: "横盘起飞 + 突破前高",
    level: 110,
    price: 110.1,
    stop: 108,
    score: 90,
    rhythmScore: 78,
    sentimentScore: 75,
    aestheticScore: 82,
    aestheticGrade: "A+",
    confluence: ["base", "previousHigh"],
    foundationTypes: ["base"],
    auxiliaryTypes: ["previousHigh"],
    consolidationBars: 40,
    outerEdgeConfirmed: true,
    outerEdgeScore: 82,
    launchDistancePercent: 1.2,
    ceilingAge: 8,
    reasons: [],
    evidence: [],
  };
  const [primary] = Engine.applyContextGates(
    [frame("15m", true, [signal]), frame("1h", true), frame("4h", true), frame("1d", true)],
    [frame("1h", false), frame("4h", false)],
  );
  assert.equal(primary.signals.length, 1);
  assert.equal(primary.signals[0].marketEmotion, "BTC 背景逆风 0/2 · 龙头独立判断");
  assert.ok(!primary.rejected.some((item) => item.reasons.includes("市场情绪未共振")));
});

test("the approximate 20-buy expectation never deletes valid causal signals", () => {
  const makeResult = (interval, count, score) => ({
    interval,
    candles: [{ time: 1, close: 1 }],
    indicators: { ema90: [1] },
    signals: Array.from({ length: count }, (_, index) => ({
      id: `${interval}-${index}`,
      interval,
      time: index + 1,
      score: score - index * 0.01,
    })),
    pending: [],
    rejected: [],
    regime: { bullish: true, strong: true, label: "主升环境" },
    stats: { signalCount: count, pendingCount: 0, rejectedCount: 0, lastPrice: 1 },
  });
  const pruned = Engine.pruneSignalBudget([
    makeResult("1m", 10, 99),
    makeResult("1d", 20, 90),
  ]);
  assert.equal(pruned.reduce((sum, result) => sum + result.signals.length, 0), 30);
  assert.equal(pruned.find((result) => result.interval === "1m").signals.length, 10);
});

test("treats 20 as a soft target and keeps additional exceptional independent ignitions", () => {
  const result = {
    interval: "1d",
    candles: [{ time: 1, close: 1 }],
    indicators: { ema90: [1] },
    signals: Array.from({ length: 24 }, (_, index) => ({
      id: `1d-exceptional-${index}`,
      interval: "1d",
      time: index * 86_400_000,
      score: 96,
    })),
    pending: [],
    rejected: [],
    regime: { bullish: true, strong: true, label: "主升环境" },
    stats: { signalCount: 24, pendingCount: 0, rejectedCount: 0, lastPrice: 1 },
  };
  const [pruned] = Engine.pruneSignalBudget([result]);
  assert.equal(pruned.signals.length, 24);
  const [displayed] = Engine.applyContextGates([result]);
  assert.equal(displayed.signals.length, 24);
});

test("trigger-bar close and volume do not leak into an intrabar pre-armed entry", () => {
  const baselineRows = horizontalBreakoutSeries();
  const alteredRows = horizontalBreakoutSeries();
  alteredRows.at(-1).close = 99.5;
  alteredRows.at(-1).volume = 1;
  alteredRows.at(-1).quoteVolume = 1_000_000_000;
  alteredRows.at(-1).takerBuyVolume = 1_000_000_000;
  alteredRows.at(-1).tradeCount = 1_000_000;
  const baseline = Engine.analyzeTimeframe(baselineRows, { interval: "5m", now: 1_800_000_000_000 });
  const altered = Engine.analyzeTimeframe(alteredRows, { interval: "5m", now: 1_800_000_000_000 });
  const time = baselineRows.at(-1).time;
  const left = baseline.signals.find((item) => item.time === time);
  const right = altered.signals.find((item) => item.time === time);
  assert.ok(left && right);
  assert.deepEqual(
    { level: left.level, triggerPrice: left.triggerPrice, price: left.price, score: left.score, orderFlowScore: left.orderFlowScore },
    { level: right.level, triggerPrice: right.triggerPrice, price: right.price, score: right.score, orderFlowScore: right.orderFlowScore },
  );
});

test("higher-timeframe context uses only candles closed before the decision", () => {
  const result = {
    interval: "1h",
    candles: [
      { time: 1_000, closeTime: 2_000, close: 90, low: 89 },
      { time: 2_000, closeTime: 3_000, close: 120, low: 118 },
    ],
    indicators: { ema90: [100, 100], atr: [1, 1] },
  };
  assert.equal(Engine.regimeAt(result, 2_500).bullish, false);
  assert.equal(Engine.regimeAt(result, 3_000).bullish, true);
});

test("distinguishes BLESS rushed stair-step lift from the later settled 15m platform", () => {
  const tuples = [
    [1785680100000, 0.015997, 0.0166, 0.01587, 0.016391],
    [1785681000000, 0.016392, 0.016724, 0.016326, 0.016441],
    [1785681900000, 0.01644, 0.017265, 0.01625, 0.016927],
    [1785682800000, 0.016936, 0.016959, 0.016555, 0.016792],
    [1785683700000, 0.016795, 0.016882, 0.016296, 0.016612],
    [1785684600000, 0.016606, 0.01724, 0.016554, 0.016945],
    [1785685500000, 0.016945, 0.016986, 0.016636, 0.016871],
    [1785686400000, 0.016873, 0.01788, 0.016451, 0.017148],
    [1785687300000, 0.017144, 0.018214, 0.017102, 0.017833],
    [1785688200000, 0.017834, 0.018095, 0.017641, 0.017707],
    [1785689100000, 0.017705, 0.018444, 0.017423, 0.018018],
    [1785690000000, 0.018021, 0.01818, 0.01777, 0.018065],
    [1785690900000, 0.018069, 0.018573, 0.018005, 0.018178],
    [1785691800000, 0.018179, 0.018667, 0.017578, 0.017786],
    [1785692700000, 0.017787, 0.01818, 0.0173, 0.017944],
    [1785693600000, 0.017941, 0.018444, 0.017921, 0.018213],
    [1785694500000, 0.018216, 0.018385, 0.017688, 0.017762],
    [1785695400000, 0.017759, 0.018349, 0.017736, 0.018064],
    [1785696300000, 0.018068, 0.018504, 0.018042, 0.018374],
    [1785697200000, 0.018374, 0.01878, 0.018248, 0.018324],
    [1785698100000, 0.018331, 0.01839, 0.01806, 0.018164],
    [1785699000000, 0.018168, 0.018482, 0.017764, 0.018194],
    [1785699900000, 0.018188, 0.01839, 0.01791, 0.018121],
    [1785700800000, 0.018119, 0.018269, 0.016671, 0.017432],
    [1785701700000, 0.017435, 0.01821, 0.017256, 0.018009],
    [1785702600000, 0.018005, 0.018164, 0.017701, 0.017926],
    [1785703500000, 0.017925, 0.018029, 0.016979, 0.017343],
    [1785704400000, 0.01735, 0.01765, 0.017213, 0.017494],
    [1785705300000, 0.017494, 0.017915, 0.017348, 0.017629],
  ];
  const rows = tuples.map(([time, open, high, low, close]) => ({ time, open, high, low, close }));
  const rushed = Engine.assessHorizontalBaseUrgency(rows.slice(1, 19), 0.0006887441191164687);
  const settled = Engine.assessHorizontalBaseUrgency(rows.slice(-18), 0.00070788588508725);
  assert.equal(rushed.urgent, true);
  assert.ok(rushed.netAdvanceAtr >= 2);
  assert.ok(rushed.lowSlopeAtrPerBar >= 0.1);
  assert.equal(settled.urgent, false);
  assert.ok(settled.maxPullbackAtr > 1.05 || settled.netAdvanceAtr < 2);
});

test("permits only the settled short digestion after a rapid main-wave lift", () => {
  const settled = {
    interval: "15m",
    foundationTypes: ["relaunch"],
    auxiliaryTypes: ["previousHigh"],
    consolidationBars: 18,
    structureQuality: 0.77,
    triggerPrice: 0.01569,
    breakoutLow: 0.01516,
    ema90SlopeAtDecision: 0.00049,
    crossedLevel: true,
    openedBeyondTrigger: false,
    aboveEma90: true,
    motherStructureNoise: false,
    oneMinuteMotherBoxNoise: false,
    horizontalLaunchUrgent: false,
    horizontalLaunchInsufficientEdgeDwell: false,
    horizontalLaunchPostSelloffRecovery: false,
    trianglePostSelloffRecovery: false,
    riskStructureShape: null,
    score: 82,
    aestheticScore: 71,
    certaintyScore: 73,
    rhythmScore: 75,
    sentimentScore: 76,
  };
  const hierarchy = Engine.assessExecutionHierarchy(settled);
  assert.equal(hierarchy.permit, true);
  assert.equal(hierarchy.primaryFoundation, "explicit-structural-exception");
  assert.equal(Engine.assessExecutionHierarchy({ ...settled, horizontalLaunchUrgent: true }).permit, false);
  assert.equal(Engine.assessExecutionHierarchy({ ...settled, consolidationBars: 8 }).permit, false);
  assert.equal(Engine.assessExecutionHierarchy({ ...settled, auxiliaryTypes: [] }).permit, false);
});

test("a mature 5m mother platform can confirm its own short 15m consolidation but not fabricate one", () => {
  const start = 1_800_000_000_000;
  const child = {
    id: "5m-mother",
    interval: "5m",
    index: 0,
    time: start + 5 * 60_000,
    decisionTime: start + 5 * 60_000,
    triggerPrice: 100.4,
    score: 94,
    aestheticScore: 78,
    certaintyScore: 88,
    consolidationBars: 41,
    outerEdgeConfirmed: true,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.1,
    executionHierarchy: {
      permit: true,
      primaryFoundation: "mother-platform-breakout",
    },
    reasons: [],
    evidence: [],
  };
  const parent = {
    id: "15m-short-parent",
    interval: "15m",
    index: 0,
    time: start,
    decisionTime: start,
    pattern: "回踩再点火 + 拐点收复 + 突破前高",
    foundationTypes: ["relaunch"],
    auxiliaryTypes: ["previousHigh"],
    confluence: ["relaunch", "pivot", "previousHigh"],
    hasPivot: true,
    consolidationBars: 8,
    structureQuality: 0.78,
    score: 88,
    aestheticScore: 76,
    certaintyScore: 82,
    rhythmScore: 75,
    sentimentScore: 80,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.1,
    crossedLevel: true,
    openedBeyondTrigger: false,
    triggerPrice: 100,
    previousHighLevel: 100,
    level: 100,
    breakoutLow: 99,
    status: "filtered",
    reasons: ["大周期未共振"],
    evidence: [],
  };
  const makeFrame = (interval, candles, signals, rejected) => ({
    interval,
    candles,
    indicators: { ema90: candles.map(() => 98), atr: candles.map(() => 1) },
    signals,
    pending: [],
    rejected,
    structures: [],
    stats: { signalCount: signals.length, pendingCount: 0, rejectedCount: rejected.length },
  });
  const five = makeFrame("5m", [{
    time: child.time,
    closeTime: child.time + 5 * 60_000 - 1,
    open: 99,
    high: 101,
    low: 98.8,
    close: 100.7,
  }], [child], []);
  const fifteen = makeFrame("15m", [{
    time: start,
    closeTime: start + 15 * 60_000 - 1,
    open: 99,
    high: 101,
    low: 98.7,
    close: 100.6,
  }], [], [parent]);
  const gated = Engine.applyContextGates([five, fifteen], [], { preselectedLeader: true });
  const promoted = gated.find((item) => item.interval === "15m").signals[0];
  assert.equal(promoted.adjacentMotherChildConfluence, true);
  assert.equal(promoted.executionHierarchy.primaryFoundation, "adjacent-lower-frame-mother-platform");
  assert.deepEqual(promoted.multiTimeframeConfluenceFrames, ["5m", "15m"]);

  const weakParent = {
    ...parent,
    id: "15m-no-own-structure",
    hasPivot: false,
    structureQuality: 0.4,
  };
  const weak = Engine.applyContextGates([
    five,
    makeFrame("15m", fifteen.candles, [], [weakParent]),
  ], [], { preselectedLeader: true });
  assert.equal(weak.find((item) => item.interval === "15m").signals.length, 0);
});

test("a closed 15m mother platform can anchor one strong 5m volume rebreak", () => {
  const parentStart = 1_800_100_000_000;
  const childTime = parentStart + 15 * 60_000;
  const parent = {
    id: "15m-closed-mother",
    interval: "15m",
    index: 0,
    time: parentStart,
    decisionTime: parentStart,
    triggerPrice: 100,
    consolidationBreakout: true,
    outerEdgeConfirmed: true,
    consolidationBars: 30,
    score: 93,
    aestheticScore: 76,
    certaintyScore: 84,
    executionHierarchy: { permit: true, primaryFoundation: "mother-platform-breakout" },
    reasons: [],
    evidence: [],
  };
  const child = {
    id: "5m-volume-rebreak",
    interval: "5m",
    index: 0,
    time: childTime,
    decisionTime: childTime,
    pattern: "回踩再点火 + 拐点收复",
    foundationTypes: ["relaunch"],
    auxiliaryTypes: [],
    confluence: ["relaunch", "pivot"],
    hasPivot: true,
    consolidationBars: 15,
    structureQuality: 0.38,
    relativeVolume: 1.73,
    orderFlowScore: 83,
    klineVelocity: 1.28,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.1,
    crossedLevel: true,
    openedBeyondTrigger: false,
    triggerPrice: 101,
    level: 101,
    breakoutLow: 98,
    score: 74,
    aestheticScore: 68,
    certaintyScore: 72,
    rhythmScore: 66,
    sentimentScore: 60,
    status: "filtered",
    reasons: ["母结构尚未成熟"],
    evidence: [],
  };
  const makeFrame = (interval, candles, signals, rejected) => ({
    interval,
    candles,
    indicators: { ema90: candles.map(() => 96), atr: candles.map(() => 1) },
    signals,
    pending: [],
    rejected,
    structures: [],
    stats: { signalCount: signals.length, pendingCount: 0, rejectedCount: rejected.length },
  });
  const fifteen = makeFrame("15m", [{
    time: parentStart,
    closeTime: parentStart + 15 * 60_000 - 1,
    open: 98,
    high: 100.5,
    low: 97.5,
    close: 100.2,
  }], [parent], []);
  const five = makeFrame("5m", [{
    time: childTime,
    closeTime: childTime + 5 * 60_000 - 1,
    open: 99,
    high: 102,
    low: 98,
    close: 101.5,
  }], [], [child]);
  const gated = Engine.applyContextGates([five, fifteen], [], { preselectedLeader: true });
  const promoted = gated.find((item) => item.interval === "5m").signals[0];
  assert.equal(promoted.adjacentMotherChildConfluence, true);
  assert.equal(promoted.pattern, "多周期盘整突破 + 放量再突破前高");
  assert.equal(promoted.executionHierarchy.primaryFoundation, "adjacent-parent-mother-platform");
});

test("does not borrow a long rising history when only a few terminal candles touched the outer edge", () => {
  const sparse = Engine.assessHorizontalBaseDwell({
    platformModel: "outer",
    touchGroups: 1,
    ceilingTouches: 2,
    ceilingAge: 3,
    consolidationBars: 37,
  });
  const mature = Engine.assessHorizontalBaseDwell({
    platformModel: "outer",
    touchGroups: 2,
    ceilingTouches: 3,
    ceilingAge: 3,
    consolidationBars: 37,
  });
  assert.equal(sparse.insufficient, true);
  assert.match(sparse.evidence.join(" "), /不能用更早的上涨路径/);
  assert.equal(mature.insufficient, false);
});

test("outer platform starts at the first real edge touch instead of an arbitrary rolling-window candle", () => {
  const intervalMs = 15 * 60_000;
  const rows = [];
  const push = (open, high, low, close, volume = 1_000) => {
    const time = rows.length * intervalMs;
    rows.push({ time, closeTime: time + intervalMs - 1, open, high, low, close, volume });
  };

  // 这段只是旧下跌背景，任何一根都不能成为后面母平台的起始 K 线。
  for (let cursor = 0; cursor < 140; cursor += 1) {
    const close = 100 - cursor * 0.025;
    push(close + 0.04, close + 0.16, close - 0.16, close);
  }
  // 独立上推把价格送到新的平台外沿。
  for (let cursor = 0; cursor < 12; cursor += 1) {
    const open = 96.5 + cursor * 0.43;
    const close = open + 0.34;
    push(open, close + 0.08, open - 0.08, close, 1_500);
  }
  const expectedStartIndex = rows.length;
  // 外沿第一次形成后，才开始计算横盘母平台的实际长度。
  for (let cursor = 0; cursor < 44; cursor += 1) {
    const close = 101.35 + Math.sin(cursor * 1.7) * 0.18;
    const high = [0, 10, 24, 36].includes(cursor) ? 102 : Math.min(101.88, close + 0.22);
    push(close - Math.sin(cursor) * 0.05, high, Math.max(100.92, close - 0.28), close, 850);
  }
  const breakoutIndex = rows.length;
  push(101.72, 102.24, 101.65, 102.1, 2_200);

  const platform = Engine.detectOuterPlatform(rows, breakoutIndex, 0.5, 18, 192);
  assert.ok(platform, "the mature local platform should still be detected");
  assert.ok(platform.structureStartIndex >= expectedStartIndex - 1);
  assert.ok(platform.structureStartIndex <= expectedStartIndex + 1);
  assert.ok(platform.consolidationBars >= 43 && platform.consolidationBars <= 45);
  assert.notEqual(platform.structureStartIndex, breakoutIndex - 192);
  assert.ok(rows[platform.structureStartIndex].high >= 101.75, "the start candle must establish the outer edge");
});

test("a few late edge candles cannot borrow an earlier decline to become a mature mother platform", () => {
  const intervalMs = 15 * 60_000;
  const rows = Array.from({ length: 186 }, (_, cursor) => {
    const close = 100 - cursor * 0.015;
    return {
      time: cursor * intervalMs,
      closeTime: (cursor + 1) * intervalMs - 1,
      open: close + 0.03,
      high: close + 0.12,
      low: close - 0.12,
      close,
      volume: 1_000,
    };
  });
  for (let cursor = 0; cursor < 6; cursor += 1) {
    const time = rows.length * intervalMs;
    rows.push({
      time,
      closeTime: time + intervalMs - 1,
      open: 101.55,
      high: cursor % 2 ? 101.98 : 102,
      low: 101.32,
      close: 101.62,
      volume: 1_000,
    });
  }
  const breakoutIndex = rows.length;
  const time = rows.length * intervalMs;
  rows.push({ time, closeTime: time + intervalMs - 1, open: 101.7, high: 102.25, low: 101.65, close: 102.1, volume: 2_000 });

  assert.equal(Engine.detectOuterPlatform(rows, breakoutIndex, 0.5, 18, 192), null);
});

test("MMT 15m does not bridge two distant highs with a nearly horizontal trendline across a full market phase", () => {
  const mmt = Engine.assessDescendingTrendlineGeometry(
    132,
    (0.1923 - 0.1935) / 132,
    0.0016264131197738175,
  );
  const realDescendingPressure = Engine.assessDescendingTrendlineGeometry(94, -0.00011, 0.0016);
  assert.equal(mmt.longShallowBridge, true);
  assert.match(mmt.reason, /跨阶段旧高/);
  assert.equal(realDescendingPressure.acceptable, true);
});

test("MMT 2026-07-28 23:15 to 2026-07-30 08:15 removes the false line and both dependent B signals", () => {
  const targetTime = Date.parse("2026-07-30T00:15:00Z");
  const firstFalseBuyTime = Date.parse("2026-07-28T20:45:00Z");
  const targetIndex = mmtFifteenMinuteRows.findIndex((row) => row.time === targetTime);
  const atrValues = Engine.atr(mmtFifteenMinuteRows, 14);
  const trendline = Engine.detectDescendingTrendline(
    mmtFifteenMinuteRows,
    targetIndex,
    atrValues[targetIndex - 1],
    { interval: "15m" },
  );
  const result = Engine.analyzeTimeframe(mmtFifteenMinuteRows, {
    interval: "15m",
    now: targetTime + 15 * 60_000,
  });
  const falseBuys = result.signals.filter((item) => (
    item.time === firstFalseBuyTime || item.time === targetTime
  ));
  assert.equal(trendline, null);
  assert.deepEqual(falseBuys, []);
  assert.ok(result.rejected.some((item) => (
    item.time === firstFalseBuyTime
    && item.reasons.some((reason) => reason.includes("缺少成熟横盘、三角或有效回踩母结构"))
  )));
  assert.ok(result.rejected.some((item) => (
    item.time === targetTime
    && item.reasons.some((reason) => reason.includes("不能用更早上涨历史充当横盘长度"))
  )));

  const platformWindow = mmtFifteenMinuteRows.slice(targetIndex - 192, targetIndex);
  const continuity = Engine.assessOuterPlatformContinuity(
    platformWindow,
    0.1943,
    [58, 59, 188],
    2,
    atrValues[targetIndex - 1],
  );
  assert.equal(continuity.phaseBroken, true);
  assert.ok(continuity.longestDeepDepartureRun >= 18);
});

test("NOT 2024-05-30 07:00 Beijing 1h keeps the horizontal launch, triangle and true prior-high breakout", () => {
  const targetTime = Date.parse("2024-05-29T23:00:00Z");
  const rows = notOneHourTriangleRows.map((row) => ({
    time: row[0],
    closeTime: row[0] + 60 * 60_000 - 1,
    open: row[1],
    high: row[2],
    low: row[3],
    close: row[4],
    volume: row[5],
    quoteVolume: row[5] * row[4],
  }));
  const result = Engine.analyzeTimeframe(rows, {
    interval: "1h",
    now: targetTime + 60 * 60_000 + 1,
  });
  const signal = result.signals.find((item) => item.time === targetTime);

  assert.ok(signal, "the exact 07:00 breakout candle must display B");
  assert.deepEqual(signal.reasons, []);
  assert.ok(signal.foundationTypes.includes("base"));
  assert.ok(signal.foundationTypes.includes("triangle"));
  assert.ok(signal.auxiliaryTypes.includes("previousHigh"));
  assert.equal(signal.outerEdgeConfirmed, true);
  assert.equal(signal.horizontalLaunchHasPriorAdvance, true);
  assert.equal(signal.horizontalLaunchPriorAdvanceSource, "triangle-aligned-prior-advance");
  assert.equal(signal.triangleHasPriorAdvance, true);
  assert.ok(signal.certaintyScore >= 90);
  assert.ok(signal.evidence.some((item) => item.includes("同一成熟三角前已有")));

  const withFuture = rows.concat(Array.from({ length: 8 }, (_, offset) => {
    const time = targetTime + (offset + 1) * 60 * 60_000;
    const close = 0.0102 - offset * 0.00008;
    return {
      time,
      closeTime: time + 60 * 60_000 - 1,
      open: close + 0.00004,
      high: close + 0.00012,
      low: close - 0.00012,
      close,
      volume: 1_000_000_000,
      quoteVolume: 1_000_000_000 * close,
    };
  }));
  const later = Engine.analyzeTimeframe(withFuture, {
    interval: "1h",
    now: withFuture.at(-1).closeTime + 1,
  }).signals.find((item) => item.time === targetTime);
  assert.ok(later, "later candles must not erase the causal 07:00 decision");
  assert.equal(later.triggerPrice, signal.triggerPrice);
  assert.equal(later.horizontalLaunchPriorAdvanceSource, signal.horizontalLaunchPriorAdvanceSource);
});

test("TURBO 2024-05-23 23:15 Beijing 15m prioritizes the mature consolidation and true prior-high breakout", () => {
  const intervalMs = 15 * 60_000;
  const targetTime = Date.parse("2024-05-23T15:15:00Z");
  const rows = Data.parseRows("okx", turboFifteenMinuteConsolidationRows, intervalMs);
  const result = Engine.analyzeTimeframe(rows, {
    interval: "15m",
    now: rows.at(-1).closeTime + 1,
    preselectedLeader: true,
  });
  const signal = result.signals.find((item) => item.time === targetTime);

  assert.ok(signal, JSON.stringify(result.rejected.filter((item) => item.time === targetTime), null, 2));
  assert.equal(signal.status, "buy");
  assert.equal(signal.primaryPatternKey, "consolidationBreakout");
  assert.equal(signal.consolidationBreakout, true);
  assert.match(signal.pattern, /^盘整突破/);
  assert.match(signal.pattern, /横盘起飞/);
  assert.match(signal.pattern, /突破前高/);
  assert.ok(signal.foundationTypes.includes("base"));
  assert.ok(signal.auxiliaryTypes.includes("previousHigh"));
  assert.equal(signal.outerEdgeConfirmed, true);
  assert.equal(signal.clusteredCeilingBand, true);
  assert.ok(signal.consolidationBars >= 28);
  assert.ok(signal.ceilingTouches >= 5);
  assert.ok(signal.platformTouchGroups >= 3);
  assert.ok(signal.certaintyScore >= 86);
  assert.ok(signal.certaintyScore >= 86);
  assert.deepEqual(signal.reasons, []);
  assert.ok(signal.evidence.some((item) => item.includes("压力带")));
  assert.ok(signal.evidence.some((item) => item.includes("盘整突破是主类")));

  const targetIndex = rows.findIndex((item) => item.time === targetTime);
  const causalRows = rows.slice(0, targetIndex + 1);
  const causal = Engine.analyzeTimeframe(causalRows, {
    interval: "15m",
    now: causalRows.at(-1).closeTime + 1,
    preselectedLeader: true,
  }).signals.find((item) => item.time === targetTime);
  assert.ok(causal, "the breakout decision must exist before any later candle is available");
  assert.equal(causal.triggerPrice, signal.triggerPrice);
  assert.equal(causal.pattern, signal.pattern);
  assert.equal(causal.outerEdgeScore, signal.outerEdgeScore);
});

test("TURBO 2024-05-23 23:00 Beijing 1h uses the 23:15 15m A+ trigger without waiting for the hourly close", () => {
  const fifteenMinuteMs = 15 * 60_000;
  const oneHourMs = 60 * 60_000;
  const parentTime = Date.parse("2024-05-23T15:00:00Z");
  const childTime = Date.parse("2024-05-23T15:15:00Z");
  const fifteenMinuteRows = Data.parseRows("okx", turboFifteenMinuteConsolidationRows, fifteenMinuteMs);
  const aggregate = (sourceRows, partialTail = false) => {
    const grouped = [];
    sourceRows.forEach((row) => {
      const time = Math.floor(row.time / oneHourMs) * oneHourMs;
      let bucket = grouped.at(-1);
      if (!bucket || bucket.time !== time) {
        bucket = {
          time,
          closeTime: time + oneHourMs - 1,
          open: row.open,
          high: row.high,
          low: row.low,
          close: row.close,
          volume: row.volume || 0,
          quoteVolume: row.quoteVolume || 0,
        };
        grouped.push(bucket);
      } else {
        bucket.high = Math.max(bucket.high, row.high);
        bucket.low = Math.min(bucket.low, row.low);
        bucket.close = row.close;
        bucket.volume += row.volume || 0;
        bucket.quoteVolume += row.quoteVolume || 0;
      }
    });
    if (partialTail && grouped.length) grouped.at(-1).closeTime = sourceRows.at(-1).closeTime;
    return grouped;
  };
  const fullHourRows = aggregate(fifteenMinuteRows);
  const fifteenResult = Engine.analyzeTimeframe(fifteenMinuteRows, {
    interval: "15m",
    now: fifteenMinuteRows.at(-1).closeTime + 1,
    preselectedLeader: true,
  });
  const oneHourResult = Engine.analyzeTimeframe(fullHourRows, {
    interval: "1h",
    now: fullHourRows.at(-1).closeTime + 1,
    preselectedLeader: true,
  });
  const gated = Engine.applyContextGates(
    [oneHourResult, fifteenResult],
    [],
    { preselectedLeader: true },
  ).find((item) => item.interval === "1h");
  const signal = gated.signals.find((item) => item.time === parentTime);

  assert.ok(signal, JSON.stringify(gated.rejected.filter((item) => item.time === parentTime), null, 2));
  assert.equal(signal.primaryPatternKey, "consolidationBreakout");
  assert.match(signal.pattern, /^盘整突破/);
  assert.equal(signal.crossFramePrecision, true);
  assert.equal(signal.lowerTimeframeTrigger, "15m");
  assert.equal(signal.decisionTime, childTime);
  assert.equal(signal.causalObservationTime, childTime + fifteenMinuteMs - 1);
  assert.equal(signal.executionHierarchy.primaryFoundation, "mother-platform-breakout");
  assert.ok(signal.consolidationBars >= 12 && signal.consolidationBars < 16);
  assert.deepEqual(signal.reasons, []);
  assert.ok(signal.evidence.some((item) => item.includes("不读取本小时后续数据")));

  // 只保留到23:15这根15分钟K，父周期用“半小时可见数据”重算后结论不变。
  const causalFifteenRows = fifteenMinuteRows.filter((row) => row.time <= childTime);
  const causalHourRows = aggregate(causalFifteenRows, true);
  const causalFifteen = Engine.analyzeTimeframe(causalFifteenRows, {
    interval: "15m",
    now: causalFifteenRows.at(-1).closeTime + 1,
    preselectedLeader: true,
  });
  const causalHour = Engine.analyzeTimeframe(causalHourRows, {
    interval: "1h",
    now: causalHourRows.at(-1).closeTime + 1,
    preselectedLeader: true,
  });
  const causalSignal = Engine.applyContextGates(
    [causalHour, causalFifteen],
    [],
    { preselectedLeader: true },
  ).find((item) => item.interval === "1h")
    .signals.find((item) => item.time === parentTime);
  assert.ok(causalSignal, "the 1h/15m resonance must already exist before later 15m candles are available");
  assert.equal(causalSignal.triggerPrice, signal.triggerPrice);
  assert.equal(causalSignal.decisionTime, signal.decisionTime);
});

test("TURBO 2024-05-23 23:00 Beijing 1h re-arms the real mother-platform edge after a brief failed local attempt", () => {
  const fifteenMinuteMs = 15 * 60_000;
  const oneHourMs = 60 * 60_000;
  const targetTime = Date.parse("2024-05-23T15:00:00Z");
  const sourceRows = Data.parseRows("okx", turboTwoStageConsolidationRows, fifteenMinuteMs);
  // 补足目标之前已经存在、但与本结构无关的上市历史，使EMA和生命周期与看板
  // 完整行情一致；这些K全部早于目标，不能提供任何未来信息。
  const historyCount = 100;
  const historyStart = sourceRows[0].time - historyCount * fifteenMinuteMs;
  const historyPrice = sourceRows[0].open;
  const stabilizingHistory = Array.from({ length: historyCount }, (_, index) => {
    const price = historyPrice * (0.96 + 0.04 * index / (historyCount - 1));
    return {
      time: historyStart + index * fifteenMinuteMs,
      closeTime: historyStart + (index + 1) * fifteenMinuteMs - 1,
      open: price,
      high: price * 1.003,
      low: price * 0.997,
      close: price * 1.0002,
      volume: 100_000,
      quoteVolume: 100_000 * price,
    };
  });
  const grouped = [];
  stabilizingHistory.concat(sourceRows).forEach((row) => {
    const time = Math.floor(row.time / oneHourMs) * oneHourMs;
    let bucket = grouped.at(-1);
    if (!bucket || bucket.time !== time) {
      bucket = {
        time,
        closeTime: time + oneHourMs - 1,
        open: row.open,
        high: row.high,
        low: row.low,
        close: row.close,
        volume: row.volume || 0,
        quoteVolume: row.quoteVolume || 0,
      };
      grouped.push(bucket);
    } else {
      bucket.high = Math.max(bucket.high, row.high);
      bucket.low = Math.min(bucket.low, row.low);
      bucket.close = row.close;
      bucket.volume += row.volume || 0;
      bucket.quoteVolume += row.quoteVolume || 0;
    }
  });
  const targetIndex = grouped.findIndex((item) => item.time === targetTime);
  const causalRows = grouped.slice(0, targetIndex + 1);
  const result = Engine.analyzeTimeframe(causalRows, {
    interval: "1h",
    now: causalRows.at(-1).closeTime + 1,
    preselectedLeader: true,
  });
  const signal = result.signals.find((item) => item.time === targetTime);

  assert.ok(signal, JSON.stringify(result.rejected.filter((item) => item.time === targetTime), null, 2));
  assert.equal(signal.primaryPatternKey, "consolidationBreakout");
  assert.equal(signal.outerEdgeConfirmed, true);
  assert.ok(signal.consolidationBars >= 14);
  assert.ok(signal.evidence.some((item) => item.includes("盘整") || item.includes("外沿")));
  assert.deepEqual(signal.reasons, []);
});

test("TURBO 2024-05-24 23:00 Beijing rebuilds the second 1h platform and resonates with the 23:45 15m A+ buy", () => {
  const fifteenMinuteMs = 15 * 60_000;
  const oneHourMs = 60 * 60_000;
  const parentTime = Date.parse("2024-05-24T15:00:00Z");
  const childTime = Date.parse("2024-05-24T15:45:00Z");
  const fifteenMinuteRows = Data.parseRows("okx", turboTwoStageConsolidationRows, fifteenMinuteMs);
  const aggregate = (sourceRows, partialTail = false) => {
    const grouped = [];
    sourceRows.forEach((row) => {
      const time = Math.floor(row.time / oneHourMs) * oneHourMs;
      let bucket = grouped.at(-1);
      if (!bucket || bucket.time !== time) {
        bucket = {
          time,
          closeTime: time + oneHourMs - 1,
          open: row.open,
          high: row.high,
          low: row.low,
          close: row.close,
          volume: row.volume || 0,
          quoteVolume: row.quoteVolume || 0,
        };
        grouped.push(bucket);
      } else {
        bucket.high = Math.max(bucket.high, row.high);
        bucket.low = Math.min(bucket.low, row.low);
        bucket.close = row.close;
        bucket.volume += row.volume || 0;
        bucket.quoteVolume += row.quoteVolume || 0;
      }
    });
    if (partialTail && grouped.length) grouped.at(-1).closeTime = sourceRows.at(-1).closeTime;
    return grouped;
  };
  const fullHourRows = aggregate(fifteenMinuteRows);
  const fifteenResult = Engine.analyzeTimeframe(fifteenMinuteRows, {
    interval: "15m",
    now: fifteenMinuteRows.at(-1).closeTime + 1,
    preselectedLeader: true,
  });
  const oneHourResult = Engine.analyzeTimeframe(fullHourRows, {
    interval: "1h",
    now: fullHourRows.at(-1).closeTime + 1,
    preselectedLeader: true,
  });
  const fifteenBuy = fifteenResult.signals.find((item) => item.time === childTime);
  assert.ok(fifteenBuy, "the 15m mature platform must independently produce its A+ buy");
  assert.equal(fifteenBuy.primaryPatternKey, "consolidationBreakout");

  const rawParent = oneHourResult.signals.find((item) => item.time === parentTime);
  assert.ok(rawParent, JSON.stringify(oneHourResult.rejected.filter((item) => item.time === parentTime), null, 2));
  assert.equal(rawParent.executionHierarchy.primaryFoundation, "mother-platform-breakout");
  assert.ok(rawParent.foundationTypes.includes("base"));
  assert.ok(rawParent.consolidationBars >= 17, "the native 1h detector must merge the full same-height post-impulse platform");
  assert.ok(rawParent.evidence.some((item) => item.includes("同高度压力带向左合并")));

  const gatedParent = Engine.applyContextGates(
    [oneHourResult, fifteenResult],
    [],
    { preselectedLeader: true },
  ).find((item) => item.interval === "1h")
    .signals.find((item) => item.time === parentTime);
  assert.ok(gatedParent, "the second rebuilt platform must not be swallowed by the first platform lifecycle");
  assert.equal(gatedParent.primaryPatternKey, "consolidationBreakout");
  assert.match(gatedParent.pattern, /^盘整突破/);
  assert.equal(gatedParent.crossFramePrecision, true);
  assert.equal(gatedParent.lowerTimeframeTrigger, "15m");
  assert.equal(gatedParent.decisionTime, childTime);
  assert.equal(gatedParent.causalObservationTime, childTime + fifteenMinuteMs - 1);
  assert.ok(gatedParent.highResolutionParentBars >= 17);
  assert.ok(gatedParent.consolidationBars >= 17);
  assert.deepEqual(gatedParent.reasons, []);
  assert.ok(gatedParent.evidence.some((item) => item.includes("高分辨率结构映射")));

  const causalFifteenRows = fifteenMinuteRows.filter((row) => row.time <= childTime);
  const causalFifteen = Engine.analyzeTimeframe(causalFifteenRows, {
    interval: "15m",
    now: causalFifteenRows.at(-1).closeTime + 1,
    preselectedLeader: true,
  });
  const causalHourRows = aggregate(causalFifteenRows, true);
  const causalHour = Engine.analyzeTimeframe(causalHourRows, {
    interval: "1h",
    now: causalHourRows.at(-1).closeTime + 1,
    preselectedLeader: true,
  });
  const causalParent = Engine.applyContextGates(
    [causalHour, causalFifteen],
    [],
    { preselectedLeader: true },
  ).find((item) => item.interval === "1h")
    .signals.find((item) => item.time === parentTime);
  assert.ok(causalParent, "later candles must not be required to recover the second 1h platform");
  assert.equal(causalParent.triggerPrice, gatedParent.triggerPrice);
  assert.equal(causalParent.decisionTime, gatedParent.decisionTime);
});

test("PEOPLE reviewed structure exceptions stay narrow and independently auditable", () => {
  assert.equal(Engine.isMatureOneHourOuterPlatformReset({
    interval: "1h",
    foundationTypes: ["base"],
    auxiliaryTypes: ["trendline", "previousHigh"],
    outerEdgeConfirmed: true,
    outerEdgeScore: 96,
    consolidationBars: 72,
    structureQuality: 0.91,
    platformTouchGroups: 6,
    ceilingTouches: 18,
    motherStructureMode: "post-impulse-high-level-rotation",
    motherStructurePosition: 0.69,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.0001,
    crossedLevel: true,
    openedBeyondTrigger: false,
    horizontalLaunchUrgent: false,
    horizontalLaunchInsufficientEdgeDwell: false,
    horizontalLaunchPostSelloffRecovery: false,
    riskStructureShape: null,
    launchDistancePercent: 2.8,
    relativeVolume: 1.4,
    orderFlowScore: 70,
  }), true);
  assert.equal(Engine.isMatureOneHourOuterPlatformReset({
    interval: "1h",
    foundationTypes: ["base"],
    auxiliaryTypes: ["previousHigh"],
    outerEdgeConfirmed: true,
    outerEdgeScore: 96,
    consolidationBars: 24,
    structureQuality: 0.91,
    platformTouchGroups: 2,
    ceilingTouches: 3,
    motherStructureMode: "post-impulse-high-level-rotation",
    motherStructurePosition: 0.69,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.0001,
    crossedLevel: true,
    openedBeyondTrigger: false,
    relativeVolume: 1.4,
  }), false);

  assert.equal(Engine.isOneHourRelaunchPivotIgnition({
    interval: "1h",
    foundationTypes: ["relaunch"],
    auxiliaryTypes: ["previousHigh"],
    consolidationBars: 18,
    structureQuality: 0.72,
    rhythmScore: 66,
    sentimentScore: 64,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.001,
    crossedLevel: true,
    openedBeyondTrigger: false,
    motherStructureNoise: false,
    riskStructureShape: null,
  }), true);

  assert.equal(Engine.isOneHourCompactAscendingTriangleIgnition({
    interval: "1h",
    foundationTypes: ["triangle", "relaunch"],
    auxiliaryTypes: [],
    hasPivot: true,
    structureShape: "ascending-triangle",
    consolidationBars: 39,
    structureQuality: 0.678,
    channelInteriorOccupancy: 0.716,
    channelMiddleParticipationRatio: 0.919,
    channelHollowRatio: 0.297,
    channelLongestHollowRun: 5,
    channelSideTransitions: 2,
    triangleHasPriorAdvance: true,
    trianglePriorAdvanceAtr: 3.5,
    trianglePostSelloffRecovery: false,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.001,
    crossedLevel: true,
    openedBeyondTrigger: false,
    motherStructureNoise: false,
    riskStructureShape: null,
    relativeVolume: 1.27,
    orderFlowScore: 77,
    klineVelocity: 1.37,
  }), true);

  assert.equal(Engine.isMatureFifteenMinuteRetryPlatformIgnition({
    interval: "15m",
    foundationTypes: ["base", "relaunch"],
    auxiliaryTypes: ["previousHigh"],
    hasPivot: true,
    consolidationBars: 42,
    outerEdgeScore: 56,
    platformTouchGroups: 2,
    ceilingTouches: 3,
    horizontalLaunchHasPriorAdvance: true,
    horizontalLaunchPriorAdvanceAtr: 6.26,
    horizontalLaunchQualified: true,
    horizontalLaunchUrgent: false,
    horizontalLaunchInsufficientEdgeDwell: false,
    horizontalLaunchPostSelloffRecovery: false,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.001,
    motherStructureNoise: false,
    riskStructureShape: "rising-channel",
    horizontalStructureStartIndex: 100,
    riskStructureStartIndex: 114,
    crossedLevel: true,
    openedBeyondTrigger: false,
    launchDistancePercent: 1.2,
    rhythmScore: 70,
    sentimentScore: 64,
    orderFlowScore: 82,
    klineVelocity: 1.25,
  }), true);

  const matureFifteenMinuteTriangle = {
    interval: "15m",
    foundationTypes: ["triangle", "relaunch"],
    auxiliaryTypes: ["previousHigh"],
    hasPivot: true,
    structureShape: "converging-triangle",
    directStructuralBoundary: true,
    consolidationBars: 39,
    structureQuality: 0.789,
    channelInteriorOccupancy: 0.67,
    channelMiddleParticipationRatio: 0.654,
    channelHollowRatio: 0.577,
    channelLongestHollowRun: 11,
    triangleHasPriorAdvance: true,
    trianglePriorAdvanceAtr: 10.9,
    trianglePostSelloffRecovery: false,
    horizontalLaunchPostSelloffRecovery: false,
    aboveEma90: true,
    ema90SlopeAtDecision: 0.001,
    crossedLevel: true,
    openedBeyondTrigger: false,
    motherStructureNoise: false,
    riskStructureShape: null,
    highLevelDistribution: false,
    launchDistancePercent: 2.2,
    rhythmScore: 92,
    sentimentScore: 80,
  };
  assert.equal(
    Engine.isMatureFifteenMinutePriorHighTriangleIgnition(matureFifteenMinuteTriangle),
    true,
  );
  assert.equal(Engine.isMatureFifteenMinutePriorHighTriangleIgnition({
    ...matureFifteenMinuteTriangle,
    auxiliaryTypes: [],
    consolidationBars: 16,
    trianglePostSelloffRecovery: true,
    channelHollowRatio: 0.72,
  }), false);
});

test("shock mother-box outer edge is generated before the breakout candle completes", () => {
  const hour = 60 * 60_000;
  const rows = [];
  let priorClose = 0.06;
  for (let index = 0; index < 120; index += 1) {
    let close;
    if (index < 56) close = 0.06 + index * 0.00068;
    else if (index === 56) close = 0.098;
    else if (index === 57) close = 0.088;
    else if (index === 58) close = 0.078;
    else if (index === 59) close = 0.074;
    else close = 0.084 + (index % 10) * 0.00095;
    const open = priorClose;
    const high = index === 56 ? 0.1 : Math.max(open, close) + 0.0007;
    const low = Math.min(open, close) - 0.0007;
    rows.push({
      time: index * hour,
      closeTime: (index + 1) * hour - 1,
      open,
      high,
      low,
      close,
      volume: 1_000_000 + (index % 5) * 100_000,
      quoteVolume: (1_000_000 + (index % 5) * 100_000) * close,
    });
    priorClose = close;
  }
  rows.push({
    time: 120 * hour,
    closeTime: 121 * hour - 1,
    open: 0.094,
    high: 0.103,
    low: 0.0935,
    close: 0.102,
    volume: 2_800_000,
    quoteVolume: 2_800_000 * 0.102,
  });
  const indicators = {
    ema90: Engine.ema(rows.map((row) => row.close), 90),
    atr: Engine.atr(rows, 14),
  };
  const candidates = Engine.findCandidates(rows, 120, indicators, { interval: "1h" });
  const motherEdge = candidates.find((candidate) => (
    Math.abs(candidate.level - 0.1) < 0.00001
    && candidate.components?.some((component) => component.shockMotherBoxOuterEdge === true)
  ));
  assert.ok(motherEdge, JSON.stringify(candidates.map((item) => ({ level: item.level, confluence: item.confluence })), null, 2));
  assert.equal(motherEdge.triggered, true);
  assert.equal(motherEdge.openedBeyondTrigger, false);
  assert.ok(motherEdge.evidence.some((item) => item.includes("当前K开始前")));
});

test("engine source contains no future-bar confirmation path", () => {
  const source = require("node:fs").readFileSync(require.resolve("../dragon-wave-engine.js"), "utf8");
  assert.doesNotMatch(source, /followThroughProfile|candles\.slice\(index \+ 1/);
  assert.doesNotMatch(source, /const requiredRebuild|尚未完成级别扩大/);
  assert.match(source, /prearmed-stop/);
});
