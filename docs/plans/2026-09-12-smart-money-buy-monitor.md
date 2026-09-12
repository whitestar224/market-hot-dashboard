# Smart Money Buy Monitor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a read-only five-chain smart-money wallet buy monitor with manual/text-discovered addresses and 10,000 USDT popup threshold.

**Architecture:** A new SQLite-backed Python service owns wallet registration, source evidence, chain cursors, buy classification, valuation, deduplication, polling, and payload generation. `server.py` wires it into existing text inputs, HTTP APIs, startup workers, desktop alerts, and monitor-buy identity; `price-watch.js` renders one new monitor tab.

**Tech Stack:** Python 3, SQLite, JSON-RPC over `requests`, existing DexScreener helpers, vanilla JavaScript, HTML/CSS, `unittest`, Node test runner.

---

### Task 1: Registry, validation, and source extraction

**Files:**
- Create: `smart_money_monitor.py`
- Create: `tests/test_smart_money_monitor.py`

**Steps:**
1. Write failing tests for EVM/Solana validation, chain normalization, duplicate upsert, and explicit smart-money mention extraction.
2. Run `python -m unittest tests.test_smart_money_monitor -v`; expect missing-module failure.
3. Implement `SmartMoneyStore`, `normalize_chain`, `normalize_wallet_address`, and `extract_smart_money_mentions`.
4. Ensure CA-labelled addresses, bare addresses, and ordinary whale position text do not auto-register.
5. Re-run the focused test; expect PASS.

### Task 2: Buy classification and chain adapters

**Files:**
- Modify: `smart_money_monitor.py`
- Modify: `tests/test_smart_money_monitor.py`

**Steps:**
1. Add failing fixtures for EVM receipt flows and Solana pre/post balances.
2. Implement deterministic net-flow classification that requires a positive target balance and negative stable/native payment.
3. Exclude approvals, transfers, airdrops, bridge receipts, liquidity operations, sells, failed transactions, and uncertain valuations.
4. Add EVM block polling with receipt-on-match and Solana signature polling with first-run baselines and bounded catch-up.
5. Verify 9,999U records without alert eligibility and 10,000U qualifies once.

### Task 3: API, startup, ingestion, and desktop alert integration

**Files:**
- Modify: `server.py`
- Modify: `tests/test_smart_money_monitor.py`

**Steps:**
1. Add failing tests for GET payload, local/admin mutations, startup idempotence, mention ingestion, and dedicated alert exemption.
2. Create `/api/smart-money-monitor` GET and POST actions `add`, `save`, `remove`, `refresh`.
3. Hook accepted raw inputs from personal X, group/chat intake, and News Trade into explicit mention extraction.
4. Start one daemon monitor worker after HTTP bind and persist health per chain.
5. Route only eligible events through the existing alert queue with source `smart-money-buy`.
6. Run Python monitor, notification, and HTTP regression tests.

### Task 4: Monitor-page UI

**Files:**
- Modify: `price-watch.html`
- Modify: `price-watch.js`
- Modify: `styles.css`
- Create: `tests/smart_money_monitor.test.js`

**Steps:**
1. Write failing DOM/source tests for the tab, form, status, wallet controls, event cards, and refresh loop.
2. Add the `smartmoney` mode between `personalx` and `chains`.
3. Build the address form, compact health summary, wallet list, event history, explorer links, and existing buy button binding.
4. Keep tab spacing responsive at the screenshot width and preserve current visual hierarchy.
5. Run the focused Node test and existing monitor-page tests.

### Task 5: Seed data, verification, and activation

**Files:**
- Modify: `.env.example`
- Modify: `smart_money_monitor.py`
- Modify: `tests/test_smart_money_monitor.py`

**Steps:**
1. Seed the five supplied EVM addresses under source `Inq5️⃣连杆`, idempotently across Ethereum/BSC/Base/Robinhood.
2. Verify no private keys or signing calls are present and no paid provider is configured.
3. Run `python -m unittest tests.test_smart_money_monitor tests.test_notification_preferences -v`.
4. Run `node --test tests/smart_money_monitor.test.js tests/price_watch_strategy_monitor.test.js`.
5. Restart `service_guard.py`, verify liveness, API payload, seeded targets, and one-chain baseline without historical alerts.
6. Open `price-watch.html?mode=smartmoney` for visual verification.

