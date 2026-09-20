const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const Vision = require("../dragon-wave-vision.js");

// Keep the pre-optimization preparation as a self-contained reference so these
// tests also work in checkouts without the ignored local performance baseline.
function legacyBuildVisualSignature(candles, selectedIndex, options = {}) {
  const rows = (Array.isArray(candles) ? candles : []).map(normalizeCandle);
  const index = clamp(Math.trunc(finite(selectedIndex)), 0, rows.length);
  const prior = rows.slice(0, index);
  if (prior.length < 24 || !rows[index]) return null;
  const providedEma = Array.isArray(options.ema90) ? options.ema90.slice(0, index).map(finite) : null;
  const completeEma = providedEma?.length === prior.length
    ? providedEma
    : ema(prior.map((row) => row.close), 90);
  const requestedWindows = [...new Set((options.windows || DEFAULT_WINDOWS).map((value) => Math.max(24, Math.trunc(finite(value)))))];
  const triggerPrice = finite(options.triggerPrice, prior.at(-1).close);
  const windows = requestedWindows.map((span) => {
    const length = Math.min(span, prior.length);
    return encodeWindow(prior.slice(-length), completeEma.slice(-length), span, triggerPrice, "context");
  }).filter(Boolean);
  const requestedStart = Math.trunc(finite(options.structureStartIndex, -1));
  const structureStartIndex = requestedStart >= 0 && requestedStart <= index - 12
    ? requestedStart
    : -1;
  let structure = null;
  if (structureStartIndex >= 0) {
    const focusRows = prior.slice(structureStartIndex);
    const focusEma = ema(focusRows.map((row) => row.close), 90);
    const focusWindow = encodeWindow(focusRows, focusEma, "focus", triggerPrice, "focus");
    if (focusWindow) windows.unshift(focusWindow);
    structure = {
      source: String(options.structureSource || "strategy"),
      startIndex: structureStartIndex,
      startTime: prior[structureStartIndex].time,
      bars: index - structureStartIndex,
    };
  }
  return {
    version: VERSION,
    model: "causal-kline-structure-raster-v2",
    interval: String(options.interval || ""),
    selectedCandleTime: rows[index].time,
    featureCutoffTime: prior.at(-1).closeTime,
    causality: "completed-candles-before-selected-index-only",
    structure,
    windows,
  };
}

const sourcePath = path.join(__dirname, "..", "dragon-wave-vision.js");
const baselinePath = path.join(__dirname, "..", ".runtime-cache", "performance-baseline-20260908", "dragon-wave-vision.js");
const referenceSource = fs.existsSync(baselinePath)
  ? fs.readFileSync(baselinePath, "utf8")
  : fs.readFileSync(sourcePath, "utf8").replace(
    "    buildVisualSignature,",
    `    buildVisualSignature: ${legacyBuildVisualSignature.toString()},`,
  );
const referenceContext = { module: { exports: {} } };
vm.runInNewContext(referenceSource, referenceContext, { filename: "dragon-wave-vision-legacy.js" });
const reference = (rows, index, options) => structuredClone(
  referenceContext.module.exports.buildVisualSignature(rows, index, options),
);

const fixtures = [
  ["husdt_1h_2025-06-29_2025-07-01.json", 60 * 60_000],
  ["piusdt_okx_5m_2025-02-22_1530.json", 5 * 60_000],
  ["notusdt_1h_2024-05-20_2024-05-30.json", 60 * 60_000],
  ["turbousdt_okx_15m_2024-05-21_2024-05-25.json", 15 * 60_000],
].map(([filename, intervalMs]) => ({
  filename,
  rows: require(`./fixtures/${filename}`).map((row) => ({
    time: Number(row[0]), closeTime: Number(row[0]) + intervalMs - 1,
    open: Number(row[1]), high: Number(row[2]), low: Number(row[3]),
    close: Number(row[4]), volume: Number(row[5]),
  })),
}));

function calculateEma(rows) {
  const alpha = 2 / 91;
  const values = [rows[0].close];
  for (let index = 1; index < rows.length; index += 1) {
    values.push(rows[index].close * alpha + values[index - 1] * (1 - alpha));
  }
  return values;
}

function randomIndices(length) {
  let state = 0x5eeda11;
  const indices = [0, 23, 24, 40, 80, length - 1, length];
  for (let count = 0; count < 5; count += 1) {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    indices.push(24 + state % (length - 24));
  }
  return [...new Set(indices)];
}

test("prepared visual signatures exactly match the legacy signatures on fixture histories", () => {
  for (const { filename, rows } of fixtures) {
    for (const ema90 of [undefined, calculateEma(rows), calculateEma(rows).slice(0, 40)]) {
      const build = Vision.createVisualSignatureBuilder(rows, ema90);
      for (const index of randomIndices(rows.length)) {
        const optionCases = [
          {},
          { interval: "1h", triggerPrice: rows[Math.min(index, rows.length - 1)].high },
          { windows: [24, 39.9, "80", 80, NaN, -1, Infinity, 9999], structureStartIndex: index - 37, structureSource: "manual" },
          { windows: [], structureStartIndex: index - 12, structureSource: "strategy" },
          { windows: null, triggerPrice: NaN, structureStartIndex: index - 11 },
        ];
        for (const options of optionCases) {
          const expected = reference(rows, index, { ...options, ema90 });
          assert.deepEqual(build(index, options), expected, `${filename}, index ${index}`);
          assert.deepEqual(Vision.buildVisualSignature(rows, index, { ...options, ema90 }), expected);
        }
      }
    }
  }
});

test("prepared signatures preserve unusual numeric input normalization and option defaults", () => {
  const rows = fixtures[1].rows.slice(0, 96).map((row) => ({ ...row }));
  rows[1] = null;
  rows[3] = {};
  rows[11] = { time: "bad", closeTime: Infinity, open: NaN, high: "1.2", low: -Infinity, close: "0.5", volume: -4 };
  rows[23] = { ...rows[23], open: "", close: null, volume: NaN };
  const ema90 = calculateEma(fixtures[1].rows).slice(0, rows.length);
  [NaN, Infinity, undefined, null, "bad", "1.3", false, -Infinity].forEach((value, offset) => { ema90[20 + offset] = value; });
  for (const suppliedEma of [undefined, ema90, ema90.slice(0, 24), [], null, "invalid"]) {
    const build = Vision.createVisualSignatureBuilder(rows, suppliedEma);
    for (const index of [undefined, null, NaN, Infinity, -2, 23.99, "24", 55.9, 95, 96, 9999]) {
      for (const options of [{}, { interval: 5, triggerPrice: null, windows: [NaN, null, "bad", 25.9], structureStartIndex: 0 }]) {
        assert.deepEqual(build(index, options), reference(rows, index, { ...options, ema90: suppliedEma }));
      }
    }
  }
  for (const invalidRows of [undefined, null, {}, [], rows.slice(0, 23)]) {
    assert.equal(Vision.createVisualSignatureBuilder(invalidRows)(100), null);
    assert.equal(Vision.buildVisualSignature(invalidRows, 100), null);
  }
});

test("a builder owns its input snapshot while public calls observe later candle and EMA mutations", () => {
  const rows = fixtures[0].rows.map((row) => ({ ...row }));
  const ema90 = calculateEma(rows);
  const index = rows.length - 1;
  const options = { interval: "1h", structureStartIndex: index - 40 };
  const build = Vision.createVisualSignatureBuilder(rows, ema90);
  const before = reference(rows, index, { ...options, ema90 });
  assert.deepEqual(build(index, options), before);

  rows[index - 2].close *= 9;
  rows[index - 2].volume *= 17;
  rows[index - 1].closeTime += 100;
  rows[index].time += 200;
  ema90[index - 3] *= 100;
  rows.push({ ...rows.at(-1), time: rows.at(-1).time + 60_000 });
  ema90.push(9999);
  const after = reference(rows, index, { ...options, ema90 });
  assert.notDeepEqual(after, before);
  assert.deepEqual(build(index, options), before);
  assert.deepEqual(Vision.buildVisualSignature(rows, index, { ...options, ema90 }), after);
  assert.equal(build(rows.length - 1, options), null);

  const returned = build(index, options);
  returned.windows[0].closePath[0] = -9999;
  returned.structure.source = "changed";
  assert.deepEqual(build(index, options), before);
  rows.splice(0, rows.length);
  ema90.splice(0, ema90.length);
  assert.deepEqual(build(index, options), before);
  assert.equal(Vision.buildVisualSignature(rows, index, { ...options, ema90 }), null);
});

test("prepared signatures retain historical cutoff and independently restarted focus EMA", () => {
  const rows = fixtures[2].rows.map((row) => ({ ...row }));
  const index = 180;
  const startIndex = 96;
  const options = { interval: "1h", structureStartIndex: startIndex };
  for (const ema90 of [undefined, calculateEma(rows)]) {
    const first = Vision.createVisualSignatureBuilder(rows, ema90)(index, options);
    const altered = rows.map((row, rowIndex) => rowIndex >= index
      ? { ...row, open: 999, close: 9999, high: 99999, low: 0, volume: 1e12 }
      : { ...row });
    const alteredEma = ema90?.map((value, rowIndex) => rowIndex >= index ? 99999 : value);
    const second = Vision.createVisualSignatureBuilder(altered, alteredEma)(index, options);
    assert.deepEqual(second, first);
    assert.equal(second.featureCutoffTime, rows[index - 1].closeTime);
    assert.equal(second.selectedCandleTime, rows[index].time);

    const changedPrefix = rows.map((row, rowIndex) => rowIndex < startIndex
      ? { ...row, open: row.open * 10, high: row.high * 10, low: row.low * 10, close: row.close * 10 }
      : { ...row });
    const prefixSignature = Vision.createVisualSignatureBuilder(changedPrefix, ema90)(index, options);
    assert.deepEqual(prefixSignature.windows[0], first.windows[0]);
  }
});

test("fallback EMA is derived from the captured candles even when first used after caller mutation", () => {
  for (const suppliedEma of [undefined, [1, 2, 3]]) {
    const rows = fixtures[1].rows.map((row) => ({ ...row }));
    const index = 120;
    const options = { structureStartIndex: 80 };
    const expected = reference(rows, index, { ...options, ema90: suppliedEma });
    const build = Vision.createVisualSignatureBuilder(rows, suppliedEma);
    rows.forEach((row) => { row.close *= 100; row.high *= 100; });
    assert.deepEqual(build(index, options), expected);
    assert.deepEqual(build(80), reference(fixtures[1].rows, 80, { ema90: suppliedEma }));
    assert.notDeepEqual(Vision.buildVisualSignature(rows, index, { ...options, ema90: suppliedEma }), expected);
  }
});

test("builder per-call EMA overrides use current override data and preserve the bound snapshot", () => {
  const rows = fixtures[1].rows;
  const ema90 = calculateEma(rows);
  const build = Vision.createVisualSignatureBuilder(rows, ema90);
  const index = 80;
  const bound = reference(rows, index, { ema90 });
  const override = ema90.map((value) => value * 4);
  for (const suppliedEma of [override, override.slice(0, 40), [], null, "bad"]) {
    assert.deepEqual(build(index, { ema90: suppliedEma }), reference(rows, index, { ema90: suppliedEma }));
  }
  override[78] *= 20;
  assert.deepEqual(build(index, { ema90: override }), reference(rows, index, { ema90: override }));
  assert.deepEqual(build(index), bound);
});

test("repeated builder calls do not reread caller candle fields or bound EMA entries", () => {
  let candleReads = 0;
  let emaReads = 0;
  const rows = fixtures[0].rows.map((row) => ({ ...row }));
  rows.forEach((row) => {
    const close = row.close;
    Object.defineProperty(row, "close", { enumerable: true, get() { candleReads += 1; return close; } });
  });
  const ema90 = calculateEma(fixtures[0].rows);
  ema90.forEach((value, index) => {
    Object.defineProperty(ema90, index, { get() { emaReads += 1; return value; } });
  });
  const build = Vision.createVisualSignatureBuilder(rows, ema90);
  const preparationReads = { candleReads, emaReads };
  assert.ok(candleReads >= rows.length);
  assert.equal(emaReads, ema90.length);
  for (const index of [24, 30, 40, rows.length - 1]) {
    assert.ok(build(index, { structureStartIndex: index - 12 }));
  }
  assert.deepEqual({ candleReads, emaReads }, preparationReads);
  Vision.buildVisualSignature(rows, rows.length - 1, { ema90 });
  assert.ok(candleReads > preparationReads.candleReads);
  assert.ok(emaReads > preparationReads.emaReads);
});
