# Causal Visual Feedback Learning Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an offline visual-structure recognition layer that learns continuously from confirmed, pending, denied and cancelled chart reviews without reading any future candle.

**Architecture:** A small dependency-free browser/Node module converts the completed candles before a candidate into normalized multi-window K-line rasters. The feedback layer compares each candidate with user-labeled positive and negative visual prototypes. In the first safe-learning release, strong visual matches can only restore a filtered candidate as a `V` pre-confirmation; they cannot create an executable `B` by themselves.

**Tech Stack:** Vanilla JavaScript UMD modules, Canvas UI, localStorage, SQLite feedback API, Node test runner, Python unittest.

---

### Task 1: Causal visual signature module

**Files:**
- Create: `dragon-wave-vision.js`
- Create: `tests/dragon_wave_vision.test.js`

**Step 1: Write failing causality and similarity tests**

- Build a 24x16 silhouette from only `candles.slice(index - window, index)`.
- Assert that changing the selected breakout candle or appending later candles leaves the signature unchanged.
- Assert that two price-scaled copies of the same consolidation have high similarity.
- Assert that a clean box and a noisy oscillating structure have materially lower similarity.

**Step 2: Run the focused test and verify failure**

Run: `node --test tests/dragon_wave_vision.test.js`

Expected: FAIL because `dragon-wave-vision.js` does not exist.

**Step 3: Implement the minimal visual encoder**

- Encode 40, 80 and 160 completed-candle windows when available.
- Normalize each window independently so absolute token price does not affect shape.
- Rasterize wick, body and EMA90 as separate compact strings.
- Include trigger-level position and a short numeric geometry vector.
- Set `featureCutoffTime` to the previous candle close time.

**Step 4: Implement deterministic visual similarity**

- Compare occupied wick/body pixels, EMA90 distance and geometry vectors.
- Return an integer score from 0 to 100 plus per-window evidence.
- Never infer an outcome label inside the vision module.

**Step 5: Run focused tests**

Run: `node --test tests/dragon_wave_vision.test.js`

Expected: PASS.

### Task 2: Attach signatures to every causal candidate and manual candle mark

**Files:**
- Modify: `dragon-wave-engine.js`
- Modify: `dragon-wave.js`
- Modify: `dragon-wave-feedback.js`
- Test: `tests/dragon_wave_engine.test.js`
- Test: `tests/dragon_wave_feedback.test.js`

**Step 1: Write failing integration tests**

- Assert generated buy, pending and filtered candidates carry a visual signature.
- Assert a manual missed-buy selection stores a signature whose cutoff is before the selected candle.
- Assert `snapshotSignal` retains the compact signature.

**Step 2: Run tests and verify failure**

Run: `node --test tests/dragon_wave_engine.test.js tests/dragon_wave_feedback.test.js`

Expected: FAIL on missing `visualSignature`.

**Step 3: Attach the signature**

- Load `DragonWaveVision` before the engine and feedback modules.
- In `evaluateCandidate`, build a signature from prior completed candles and the pre-armed trigger level.
- In manual candle selection, build the same signature from the selected index.
- Add visual fields to the feedback snapshot allowlist and optimization export.

**Step 4: Run integration tests**

Expected: PASS with no change to original signal times or prices.

### Task 3: Supervised visual prototype learning

**Files:**
- Modify: `dragon-wave-feedback.js`
- Modify: `quiet_http_server.py`
- Test: `tests/dragon_wave_feedback.test.js`
- Test: `tests/test_dragon_wave_feedback_api.py`

**Step 1: Write failing learning-policy tests**

- A+ confirmations are strong positive prototypes; A and B use lower weights.
- Denied records are negative prototypes.
- Pending records are excluded from both sides.
- Cleared records make no contribution.
- A visual match cannot override an exact permanent denial.

**Step 2: Implement prototype ranking**

- Match only the same interval in v1.
- Prefer shared manual structure tags but do not require exact tag equality.
- Keep the nearest positive and negative examples, pair counts and similarity scores.
- Require a strong positive margin over negative matches.

**Step 3: Implement safe-learning promotion**

- A filtered causal candidate becomes `pending` with `visualPreconfirmed: true` only when visual evidence is strong and diverse enough.
- It is labelled `V`, never `B`.
- Existing hard causal vetoes such as future-data, gap chasing, 1-minute whitelist and exact manual denial remain final.

**Step 4: Run focused JS and Python tests**

Expected: PASS.

### Task 4: Backfill existing manual reviews when their chart data is loaded

**Files:**
- Modify: `dragon-wave.js`
- Test: `tests/dragon_wave_page.test.js`

**Step 1: Write failing page-source test**

- Assert loaded feedback records missing a signature are reconstructed only from candles before their reviewed timestamp.
- Assert hydration is saved once and does not alter the manual decision, grade or tags.

**Step 2: Implement lazy hydration**

- On each loaded pair/interval, locate matching reviewed candle times.
- Build missing signatures from the current result.
- Merge and persist the enriched record without changing `createdAt` or the user label.
- Leave records untouched when the required history is unavailable.

**Step 3: Run page tests**

Expected: PASS.

### Task 5: Display visual pre-confirmations and audit evidence

**Files:**
- Modify: `dragon-wave.html`
- Modify: `dragon-wave.js`
- Modify: `dragon-wave.css`
- Test: `tests/dragon_wave_page.test.js`

**Step 1: Write failing UI tests**

- Assert the legend explains `V · 视觉预确认`.
- Assert a visual pre-confirmation uses `V`, not `B`.
- Assert the feedback popup and ledger show positive similarity, negative similarity and sample counts.

**Step 2: Implement UI**

- Render a cyan/blue `V` marker distinct from green `B` and amber pre-armed orders.
- Show the predicted structure tags and nearest labeled examples.
- Keep all existing manual confirmation controls unchanged.

**Step 3: Run full verification**

Run:

`node --check dragon-wave-vision.js`

`node --check dragon-wave-engine.js`

`node --check dragon-wave-feedback.js`

`node --check dragon-wave.js`

`node --test tests/dragon_wave*.test.js`

`python -m unittest discover -s tests -p 'test_*.py'`

Expected: all tests pass. Restart the quiet server, verify the v38 page and verify local SQLite feedback remains readable.
