# Global Hotspot Meme and Ave Chain Boards Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add hourly AI-powered whole-web hotspot discovery to News Trade and add five ten-row Ave.ai chain boards whose members feed scan/onchain research.

**Architecture:** Persist a rate-limited background hotspot snapshot that the existing News Trade core consumes without blocking HTTP requests. Extend the existing Ave source with cached chain-board payloads and embedded 1h/4h/24h metrics, flatten those rows only for scanning/research, and switch the visible ten-row chain/period view client-side with 4h as the default.

**Tech Stack:** Python 3, existing Codex CLI fallback, HTTP/RSS/DexScreener helpers, SQLite-backed onchain research, vanilla JavaScript, unittest, Node test runner.

---

### Task 1: Hourly hotspot budget and normalization

**Files:**
- Modify: `server.py`
- Test: `tests/test_global_hotspot_monitor.py`

**Steps:**
1. Add failing tests for one-hour minimum spacing, 24-batch daily cap, failure-counted attempts, source-link validation and stable event identities.
2. Run `python -m unittest tests.test_global_hotspot_monitor` and verify the new tests fail.
3. Add persistent state, locks and pure normalization/claim helpers. Defaults must be 3600 seconds and 24 batches, with no environment setting allowed to make them less strict.
4. Run the focused tests and verify they pass.

### Task 2: Background Codex web discovery and News Trade intake

**Files:**
- Modify: `server.py`
- Test: `tests/test_global_hotspot_monitor.py`
- Test: `tests/test_event_monitor.py`

**Steps:**
1. Add failing tests for a Jacob-style resignation event, cached non-blocking reads, stable event dedupe and one batched low-effort Codex web-search call.
2. Implement the prompt and worker using `codex_cli_chat(..., web_search=True)` with the configured light model, evidence URLs and a bounded result schema.
3. Resolve event search terms through the existing DexScreener helper and persist verified candidate rows with chain and CA.
4. Merge cached hotspot source/candidate rows into `build_event_monitor_core_payload`; add the hourly background loop and wake the event cache after completion.
5. Run both focused suites and verify the hotspot appears in News Trade without blocking API reads.

### Task 3: Promote matching scan/research candidates safely

**Files:**
- Modify: `onchain_fast_research.py`
- Modify: `server.py`
- Test: `tests/test_onchain_fast_research.py`
- Test: `tests/test_global_hotspot_monitor.py`

**Steps:**
1. Add failing tests that exact entity/name matches attach hotspot evidence, adequate liquidity/activity may become `shortlisted`, and missing CA/low liquidity/hard-filtered rows do not.
2. Add an optional post-quantitative candidate enricher to `FastResearch` and inject the hotspot matcher from `server.py`.
3. Preserve the original AI recommendation gate: `shortlisted` only queues review; only a research-worthy AI result enters persisted recommendations/精选.
4. Run the focused research suites.

### Task 4: Ave five-chain payload and research intake

**Files:**
- Modify: `server.py`
- Test: `tests/test_ave_hot_monitor.py`

**Steps:**
1. Add failing tests for exact Ave chain parameters `robinhood`, `solana`, `eth`, `bsc`, `base`, ten-row caps and partial-chain failure fallback.
2. Refactor row parsing into one helper, retain 1h/4h/24h metrics on every row, and fetch the five chain boards concurrently after validating the existing Ave token.
3. Return `chainBoards` on the existing `ave` source while retaining total-board rows in `rows`.
4. Flatten unique chain-board rows into `sync_price_watch_ave_candidates` and `ONCHAIN_FAST_RESEARCH.ingest`, retaining the total-list-only popup baseline.
5. Run the Ave and onchain focused suites.

### Task 5: Ave chain selector and card swap

**Files:**
- Modify: `app.js`
- Modify: `styles.css`
- Modify: `index.html`
- Modify: `server.py`
- Test: `tests/market_priority_view.test.js`
- Test: `tests/ave_chain_boards.test.js`

**Steps:**
1. Add failing Node assertions for six Ave view options, local preference persistence, selected-board row rendering and exact ten-row cap.
2. Add Ave chain and period selectors to the card header; allow only 1h/4h/24h, default to 4h, and choose cached rows/metrics without a new network request.
3. Keep the aggregate total board based on Ave's combined top ten so the five chain lists do not inflate the cross-source total board.
4. Swap the `ave` and `futu-us` positions in `market_payload` and bump static asset versions.
5. Run the Node tests and visually inspect the loaded page after deployment.

### Task 6: Regression, deployment and live verification

**Files:**
- Modify: `docs/plans/2026-09-08-active-user-requests.md`

**Steps:**
1. Run Python compilation and all focused Python/Node suites from Tasks 1–5.
2. Run related News Trade, market source, onchain research and live-DOM regressions.
3. Check for unexpired buy/signing orders before any restart; never broadcast or sign a transaction during verification.
4. Restart only the owned dashboard process, then verify `/api/service-liveness`, `/api/market` and `/api/event-monitor`.
5. Confirm live Ave has ten rows for each requested chain, the source order is swapped, and the hotspot task reports its next allowed batch and daily usage.
