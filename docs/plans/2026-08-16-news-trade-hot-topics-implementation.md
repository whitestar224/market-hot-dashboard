# News Trade Hot Topics Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace listing-heavy News Trade results with clustered recent hot topics ranked by their strongest on-chain Meme candidates, including Top1+2 display and ten-topic pagination.

**Architecture:** Keep the existing event source collectors, but add a deterministic topic/entity layer and a normalized on-chain candidate ranking layer. Build stable topic records from classified events, gate listings through strict on-chain heat thresholds, and paginate the ranked topic records in the existing client.

**Tech Stack:** Python 3 standard library, requests, existing local JSON caches, vanilla JavaScript, HTML/CSS, unittest, Node test runner.

---

### Task 1: Candidate normalization and ranking

**Files:**
- Modify: `server.py`
- Test: `tests/test_event_monitor.py`

**Steps:**
1. Add failing tests for extracting hot-event entities and ranking a larger, hotter pool above smaller same-name contracts.
2. Run `python -m unittest tests.test_event_monitor -v` and confirm the new tests fail.
3. Add normalized on-chain candidate parsing for contract, chain, market cap, liquidity, volume, transactions and heat.
4. Add weighted relevance and market-quality scoring, unique-contract deduplication and Top3 selection.
5. Re-run the event monitor tests and confirm they pass.

### Task 2: Strict listing gate and topic clustering

**Files:**
- Modify: `server.py`
- Test: `tests/test_event_monitor.py`

**Steps:**
1. Add failing tests proving ordinary listings and TradFi perpetuals never enter News Trade.
2. Add a passing fixture for a highly active new crypto contract with strong on-chain liquidity and volume.
3. Implement the listing heat gate and reduce listing-template weight.
4. Add stable topic keys and merge multiple rows for the same event/candidate into one topic with source count and related news.
5. Verify repeated news updates do not change the topic identity.

### Task 3: Payload, alerts and execution compatibility

**Files:**
- Modify: `server.py`
- Test: `tests/test_event_monitor.py`

**Steps:**
1. Make `newsTrades` contain ranked topic records with `memeCandidates`, while retaining `memeOpportunity` as the Top1 compatibility field.
2. Sort topics by weighted score, candidate quality and recency.
3. Change alert dedupe keys to stable topic IDs and include the Top1 asset in alert copy.
4. Keep buy preparation bound to the Top1 verified contract.
5. Verify price-structure event contexts still receive the Top1 symbol.

### Task 4: Topic card and pagination UI

**Files:**
- Modify: `price-watch.js`
- Modify: `styles.css`
- Modify: `price-watch.html`
- Test: `tests/price_watch_chain_ecosystem.test.js`

**Steps:**
1. Add failing UI tests for Top1+2 rendering, weighted score labels and page controls.
2. Add client state with page size 10, page clamping and previous/next/numeric navigation.
3. Render one primary candidate and up to two compact backup candidates inside each topic card.
4. Preserve current page on refresh and reset only when switching away from News Trade.
5. Bump static resource versions and verify responsive spacing keeps actions and candidate metrics aligned.

### Task 5: Active discovery search

**Files:**
- Modify: `server.py`
- Modify: `price-watch.js`
- Modify: `styles.css`
- Test: `tests/test_event_monitor.py`
- Test: `tests/price_watch_chain_ecosystem.test.js`

**Steps:**
1. Add tests for searching local cached news before the external fallback and rejecting empty/stale results.
2. Add a guarded news-search endpoint that accepts a short query or news URL, normalizes recent results and runs the same topic/candidate pipeline.
3. Persist confirmed search topics in a compact runtime JSON cache and merge them into automatic topics by stable topic key.
4. Add a News Trade search panel with loading, preview, duplicate and “加入主题监控” states.
5. Verify a search-generated topic can resolve Top1+2 and survives page refresh without duplicating automatic monitoring.

### Task 6: Regression and live verification

**Files:**
- Test: `tests/test_event_monitor.py`
- Test: `tests/price_watch_chain_ecosystem.test.js`
- Test: `tests/test_market_alert_speech.py`

**Steps:**
1. Run focused Python and Node tests.
2. Run the complete Python suite and relevant Node suites; report unrelated branch failures without modifying them.
3. Restart only the verified dashboard server process on port 8765.
4. Verify the live API no longer has ordinary listing announcements dominating News Trade.
5. Verify the page serves the new resource versions, topic counts, Top1+2 candidates and pagination metadata.
