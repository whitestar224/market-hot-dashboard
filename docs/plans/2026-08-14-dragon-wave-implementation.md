# Dragon Wave Buy-Point Workbench Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an isolated, reviewable multi-timeframe candlestick page that marks explainable dragon-wave breakout entries from live Binance public data without integrating it into existing navigation.

**Architecture:** Keep the preview additive: a standalone HTML page, a dedicated stylesheet, a pure signal engine, a document-derived case catalog, a multi-venue read-only data adapter, and a UI/chart controller. The browser follows a Binance → OKX → Bitget → Hyperliquid → spot fallback chain; the pure engine and adapter are dependency-free and tested with Node's built-in test runner.

**Tech Stack:** HTML5, CSS, vanilla JavaScript, Canvas 2D, public REST market data from Binance/OKX/Bitget/Gate/KuCoin/Hyperliquid, Node `node:test`.

---

### Task 1: Encode the document-derived research catalog

**Files:**
- Create: `dragon-wave-cases.js`
- Test: `tests/dragon_wave_engine.test.js`

**Step 1:** Add a failing catalog test for required cases, valid date normalization, and the BAKE invalid-date flag.

**Step 2:** Run `node --test tests/dragon_wave_engine.test.js` and verify the catalog assertions fail.

**Step 3:** Encode all 86 source ranges as immutable records with symbol, start, end, display label, and validation status.

**Step 4:** Re-run the test and verify the catalog assertions pass.

### Task 2: Implement the deterministic multi-timeframe signal engine

**Files:**
- Create: `dragon-wave-engine.js`
- Modify: `tests/dragon_wave_engine.test.js`

**Step 1:** Add failing synthetic-candle tests for horizontal base breakout, W continuation, previous-high break, triangle breakout, pivot reclaim, late-chase rejection, wick rejection, and open-candle exclusion.

**Step 2:** Run the focused test file and verify failures identify missing engine behavior.

**Step 3:** Implement EMA, ATR, relative volume, pivot extraction, structure detectors, regime checks, trigger scoring, vetoes, deduplication, and multi-timeframe summary generation.

**Step 4:** Re-run tests and verify every detector and veto passes.

### Task 3: Build the standalone terminal UI

**Files:**
- Create: `dragon-wave.html`
- Create: `dragon-wave.css`
- Create: `dragon-wave.js`

**Step 1:** Create accessible semantic layout for controls, summary cards, six chart panels, strategy evidence, and the sample catalog.

**Step 2:** Implement responsive terminal styling with a distinctive cyan/amber/red signal system and balanced chart geometry.

**Step 3:** Implement the multi-venue futures/spot fallback chain, bounded timeframe windows, schema normalization, error isolation, caching, and stale-request cancellation.

**Step 4:** Implement Canvas candlesticks, volume, EMA overlays, structure levels, buy/veto markers, crosshair, wheel zoom, drag pan, and linked focus time.

**Step 5:** Wire case selection, custom symbol/date input, timeframe expansion, evidence cards, filters, and loading/empty/error states.

### Task 4: Verify data, logic, and visual behavior

**Files:**
- Modify only if defects are found: `dragon-wave-engine.js`, `dragon-wave.js`, `dragon-wave.css`, `dragon-wave.html`

**Step 1:** Run `node --test tests/dragon_wave_engine.test.js`; expected result: all tests pass.

**Step 2:** Start the existing local server and request `dragon-wave.html`; expected result: HTTP 200 with no changes to existing navigation.

**Step 3:** Verify a live Binance symbol returns candles in all six intervals or shows an explicit per-panel error.

**Step 4:** Inspect the page at desktop and narrow widths, checking alignment, spacing, chart labels, overflow, and interactive states.

**Step 5:** Save a preview screenshot under `deliverables/` for user review.

### Task 5: Hold integration for user approval

**Files:**
- No existing application file is modified in this preview phase.

**Step 1:** Deliver the standalone preview link, screenshot, test result, and known limitations.

**Step 2:** After explicit approval, add navigation and any preferred server-side proxy/cache in a separate change.
