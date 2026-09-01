# Public Chain Ecosystem Monitor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a full-lifecycle public-chain ecosystem monitor to the existing price-watch page, with evidence-backed discovery, L0-L3 markets, potential-issuance projects, per-market Top 5 rankings, and deduplicated high-value alerts.

**Architecture:** Keep domain logic, SQLite persistence, provider adapters, scoring, and alert state in a new `chain_ecosystem_monitor.py` module. Integrate it into `server.py` through a small GET/POST API surface and render it as one more mode in the existing `price-watch.html` / `price-watch.js` shell, reusing the current design tokens and desktop-alert queue.

**Tech Stack:** Python 3, SQLite, `requests`, `unittest`, vanilla JavaScript, Node built-in test runner, existing HTML/CSS design system.

---

### Task 1: Domain vocabulary and stage-aware scoring

**Files:**
- Create: `chain_ecosystem_monitor.py`
- Create: `tests/test_chain_ecosystem_monitor.py`

**Step 1: Write the failing scoring tests**

Add tests that require:

```python
from chain_ecosystem_monitor import (
    DEFAULT_MARKETS,
    rank_market_projects,
    score_potential_project,
    score_traded_project,
)


def test_taxonomy_contains_all_confirmed_l0_to_l3_markets():
    keys = {row["key"] for row in DEFAULT_MARKETS}
    assert {"chain_token", "dex", "lending", "meme", "nft", "gamefi", "stablecoin", "derivatives", "points", "identity", "validators"} <= keys


def test_potential_project_uses_stage_specific_weights():
    result = score_potential_project({
        "officialProgress": 100,
        "ecosystemRole": 80,
        "development": 70,
        "fundingPartners": 60,
        "community": 50,
    })
    assert result["score"] == 76.5
    assert result["confidence"] == 100


def test_missing_metric_rebalances_available_weights_and_reduces_confidence():
    result = score_traded_project({"liquidity": 80, "activity": 60})
    assert result["score"] == 70
    assert 0 < result["confidence"] < 100


def test_market_rank_is_deterministic_and_limited_to_five():
    rows = [{"projectId": f"p{index}", "score": 100 - index} for index in range(8)]
    assert [row["projectId"] for row in rank_market_projects(rows)] == ["p0", "p1", "p2", "p3", "p4"]
```

**Step 2: Run the focused tests and verify failure**

Run:

```powershell
python -m unittest tests.test_chain_ecosystem_monitor -v
```

Expected: FAIL because `chain_ecosystem_monitor` does not exist.

**Step 3: Implement the minimal pure domain layer**

In `chain_ecosystem_monitor.py` add:

- `CHAIN_STAGES`, `PROJECT_TOKEN_STAGES`, and the confirmed `DEFAULT_MARKETS` taxonomy.
- `weighted_score(values, weights)` that ignores absent values, rescales remaining weights, and reports metric coverage as confidence.
- `score_potential_project`, `score_traded_project`, and deterministic `rank_market_projects(limit=5)`.
- Input clamping to 0-100 and stable tie-breaking by evidence confidence, score, then project id.

Use these exact weights:

```python
POTENTIAL_WEIGHTS = {
    "officialProgress": 0.30,
    "ecosystemRole": 0.20,
    "development": 0.20,
    "fundingPartners": 0.15,
    "community": 0.15,
}
TRADED_WEIGHTS = {
    "liquidity": 0.25,
    "activity": 0.25,
    "adoption": 0.20,
    "priceStrength": 0.15,
    "ecosystemCentrality": 0.10,
    "evidenceConfidence": 0.05,
}
```

**Step 4: Run tests and compile**

Run:

```powershell
python -m unittest tests.test_chain_ecosystem_monitor -v
python -m py_compile chain_ecosystem_monitor.py
```

Expected: all Task 1 tests PASS; compilation exits 0.

**Step 5: Commit the isolated domain layer**

```powershell
git add chain_ecosystem_monitor.py tests/test_chain_ecosystem_monitor.py
git commit -m "feat: add chain ecosystem scoring model"
```

Do not stage any `dragon-wave*`, WeChat, or unrelated dirty files.

### Task 2: Evidence-graph SQLite storage

**Files:**
- Modify: `chain_ecosystem_monitor.py`
- Modify: `tests/test_chain_ecosystem_monitor.py`

**Step 1: Add failing persistence tests**

Use `tempfile.TemporaryDirectory()` and require:

```python
def test_store_seeds_taxonomy_once_and_preserves_evidence_history():
    store = ChainEcosystemStore(temp_db_path)
    store.initialize()
    chain = store.upsert_chain({"slug": "robinhood", "name": "Robinhood Chain", "stage": "tradable_ecosystem"})
    first = store.add_evidence(chain["id"], "chain", {"source": "official", "url": "https://docs.robinhood.com/chain/", "confidence": 100})
    second = store.add_evidence(chain["id"], "chain", {"source": "official", "url": "https://docs.robinhood.com/chain/", "confidence": 100})
    assert first["id"] == second["id"]
    assert len(store.list_markets(chain["id"])) == len(DEFAULT_MARKETS)


def test_failed_refresh_does_not_replace_last_complete_snapshot():
    store.save_ranking_snapshot(...)
    with self.assertRaises(RuntimeError):
        with store.refresh_transaction(...):
            store.save_ranking_snapshot(...)
            raise RuntimeError("provider down")
    assert store.latest_complete_snapshot(...) == original_snapshot
```

Also test project-market many-to-many relations, unique chain slugs, unique `(chain_id, contract_address)`, source-health updates, and audit preservation for manual corrections.

**Step 2: Run the focused tests**

Run `python -m unittest tests.test_chain_ecosystem_monitor -v`.

Expected: new persistence tests FAIL because the store is missing.

**Step 3: Implement `ChainEcosystemStore`**

Create tables for:

- `chains`
- `stage_transitions`
- `markets`
- `projects`
- `project_markets`
- `assets`
- `evidence`
- `ranking_snapshots`
- `alert_events`
- `source_health`
- `manual_audit`

Requirements:

- Set `PRAGMA foreign_keys = ON`, WAL mode, row factory, and a per-store lock.
- Use transactions for a complete refresh.
- Use stable unique keys for chain slug, evidence fingerprint, contracts, alert dedupe keys, and snapshot rank rows.
- Seed all default markets for every new chain.
- Store provider payload fragments as bounded JSON; do not store unbounded raw HTML.

**Step 4: Run the focused suite twice**

Run:

```powershell
python -m unittest tests.test_chain_ecosystem_monitor -v
python -m unittest tests.test_chain_ecosystem_monitor -v
```

Expected: both runs PASS, proving initialization is idempotent.

**Step 5: Commit storage**

```powershell
git add chain_ecosystem_monitor.py tests/test_chain_ecosystem_monitor.py
git commit -m "feat: persist chain ecosystem evidence graph"
```

### Task 3: Provider adapters and fixture-backed normalization

**Files:**
- Modify: `chain_ecosystem_monitor.py`
- Modify: `tests/test_chain_ecosystem_monitor.py`
- Create: `tests/fixtures/chain_ecosystem_geckoterminal.json`
- Create: `tests/fixtures/chain_ecosystem_dexscreener.json`
- Create: `tests/fixtures/chain_ecosystem_defillama.json`
- Create: `tests/fixtures/chain_ecosystem_github.json`
- Create: `tests/fixtures/chain_ecosystem_blockscout.json`

**Step 1: Save minimal sanitized provider fixtures**

Each fixture should contain only fields the normalizer consumes. Include duplicate names, one invalid pool, one missing metric, and one project visible from two providers.

**Step 2: Add failing adapter tests**

Require adapters to return a shared evidence shape:

```python
{
    "provider": "geckoterminal",
    "subjectType": "asset",
    "externalId": "...",
    "observedAt": 0,
    "confidence": 0,
    "metrics": {},
    "evidence": [],
}
```

Test that:

- GeckoTerminal and DEX Screener pools merge by normalized chain plus contract/pool address.
- DefiLlama protocol categories map to the confirmed market taxonomy.
- GitHub activity never creates a token or trading claim.
- Blockscout contract evidence alone produces `contract_confirmed`, not `trading`.
- Timeouts update source health and leave previous metrics untouched.

**Step 3: Run tests and verify failure**

Run `python -m unittest tests.test_chain_ecosystem_monitor -v`.

Expected: adapter tests FAIL because provider functions are absent.

**Step 4: Implement injectable provider functions**

Add request functions with injectable `session`, explicit timeouts, provider-specific User-Agent/Accept headers, bounded retries, and no import-time network calls:

- `fetch_geckoterminal_network`
- `fetch_dexscreener_assets`
- `fetch_defillama_protocols`
- `fetch_github_repository`
- `fetch_blockscout_chain`
- `normalize_provider_rows`
- `merge_provider_entities`

Do not require API keys for the baseline. Read an optional `GITHUB_TOKEN` only when calling GitHub.

**Step 5: Run tests**

Run:

```powershell
python -m unittest tests.test_chain_ecosystem_monitor -v
python -m py_compile chain_ecosystem_monitor.py
```

Expected: PASS and exit 0.

**Step 6: Commit adapters and fixtures**

```powershell
git add chain_ecosystem_monitor.py tests/test_chain_ecosystem_monitor.py tests/fixtures/chain_ecosystem_*.json
git commit -m "feat: add chain ecosystem data adapters"
```

### Task 4: Discovery, lifecycle migration, ranking snapshots, and alerts

**Files:**
- Modify: `chain_ecosystem_monitor.py`
- Modify: `tests/test_chain_ecosystem_monitor.py`

**Step 1: Add failing lifecycle and alert tests**

Cover these exact behaviors:

- An official mainnet announcement advances `early_watch` to `mainnet_focus`.
- Confirmed public mainnet plus a valid live pool advances to `tradable_ecosystem`.
- A potential project stays out of the trade ranking until both contract and valid-pool evidence exist.
- A new dynamic market produces one `new_market` alert.
- A challenger must lead by at least 5 points for two complete snapshots before `leader_change`.
- Token launch/listing updates lifecycle and ranking state without creating an alert.
- Volume surge needs at least 2.5x trailing median and USD 100,000; liquidity surge needs at least 1.5x prior liquidity and USD 25,000 absolute growth.
- Replaying the same evidence never duplicates an alert.
- A partial/failed scan never advances lifecycle or changes the leader.

**Step 2: Run the focused tests**

Expected: FAIL because orchestration functions are absent.

**Step 3: Implement the orchestration layer**

Add:

- `discover_chain_ecosystem(chain, store, providers)`
- `resolve_chain_stage(evidence)`
- `resolve_project_token_stage(project, evidence)`
- `classify_project_markets(project, evidence)`
- `build_ranking_snapshot(chain_id, observed_at)`
- `detect_high_value_alerts(previous, current)`
- `refresh_chain_ecosystem(chain_id, force=False)`
- `chain_ecosystem_payload(chain_id=None)`

Implement dynamic market creation only when there are two independent signals or one official/manual signal. Keep low-confidence suggestions in a pending-review collection.

**Step 4: Seed Robinhood Chain with current official facts only**

Seed:

- name: `Robinhood Chain`
- slug: `robinhood-chain`
- stage: `tradable_ecosystem`
- chain id: `4663`
- gas symbol: `ETH`
- official docs: `https://docs.robinhood.com/chain/`
- official mainnet announcement as evidence
- RPC and explorer values from official docs

Do not seed Top 5 rankings or infer ecosystem projects from memory. Let providers create those rows.

**Step 5: Run focused tests twice**

Expected: all lifecycle, ranking, and alert cases PASS on both runs.

**Step 6: Commit orchestration**

```powershell
git add chain_ecosystem_monitor.py tests/test_chain_ecosystem_monitor.py
git commit -m "feat: rank chain ecosystems and detect lifecycle alerts"
```

### Task 5: Server integration and mutation safety

**Files:**
- Modify: `server.py:190-270`
- Modify: `server.py:20050-20110`
- Modify: `server.py:20480-20535`
- Modify: `server.py:20670-20705`
- Modify: `tests/test_chain_ecosystem_monitor.py`

**Step 1: Add failing API-boundary tests**

Test pure request handlers or extracted payload functions rather than opening a real port. Require:

- GET payload returns `ok`, `chains`, `selectedChain`, `markets`, `potentialProjects`, `alerts`, `sourceHealth`, `warnings`, `updatedAt`, and `stale`.
- Refresh returns the last snapshot immediately and schedules one background refresh per chain.
- POST validates `action`, chain/project names, URL schemes, chain id, contract address length, and market key.
- Manual mutations require a current user.
- Errors do not expose raw provider secrets or local paths.

**Step 2: Run tests and verify failure**

Run `python -m unittest tests.test_chain_ecosystem_monitor -v`.

**Step 3: Wire the module into `server.py`**

- Set the module database path to `PERSIST_CACHE_DIR / "chain_ecosystem.db"`.
- Initialize the store in `main()` after `init_auth_db()`.
- Add `GET /api/chain-ecosystem` with optional `chain`, `refresh`, and `includePending` query values.
- Add authenticated `POST /api/chain-ecosystem` actions: `add_chain`, `add_project`, `add_evidence`, `confirm_market`, `correct_relation`, `ack_alert`, and `refresh`.
- Return 400 for validation errors, 401 for unauthenticated mutation, and 502 only when no trustworthy payload can be returned.
- Never hold an HTTP request open for provider fan-out; force refresh schedules the worker and returns cache state.

**Step 4: Run focused tests and compile**

```powershell
python -m unittest tests.test_chain_ecosystem_monitor -v
python -m py_compile chain_ecosystem_monitor.py server.py
```

Expected: PASS and exit 0.

**Step 5: Commit API integration**

```powershell
git add server.py chain_ecosystem_monitor.py tests/test_chain_ecosystem_monitor.py
git commit -m "feat: expose chain ecosystem monitor API"
```

### Task 6: Existing-style monitor page rendering

**Files:**
- Modify: `price-watch.html:72-83`
- Modify: `price-watch.html:100-106`
- Modify: `price-watch.js:1-40`
- Modify: `price-watch.js:591-690`
- Modify: `price-watch.js:700-920`
- Modify: `price-watch.js:1040-1110`
- Create: `tests/price_watch_chain_ecosystem.test.js`

**Step 1: Write a failing static UI contract test**

Use Node's built-in test runner:

```javascript
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");

test("price watch exposes chain ecosystem mode and API", () => {
  const html = fs.readFileSync("price-watch.html", "utf8");
  const js = fs.readFileSync("price-watch.js", "utf8");
  assert.match(html, /data-watch-mode="chains"/);
  assert.match(js, /\/api\/chain-ecosystem/);
  assert.match(js, /renderChainEcosystem/);
  assert.match(js, /potentialProjects/);
});
```

**Step 2: Run the test and verify failure**

Run `node --test tests/price_watch_chain_ecosystem.test.js`.

Expected: FAIL because the mode and renderer do not exist.

**Step 3: Add the mode and client state**

- Add one `公链生态` mode button.
- Add `chains` to `supportedModes`.
- Add payload/loading/cache state for chains, selected chain, markets, pending projects, alerts, health, warnings, and timestamps.
- Hide the generic symbol-add form in chain mode, as is already done for WeChat mode.
- Preserve `?mode=chains&chain=<slug>` in browser history.

**Step 4: Implement escaped templates and interactions**

Add pure templates for:

- chain sidebar rows grouped by lifecycle
- chain header and source freshness
- L0-L3 market sections
- Top 5 rows with Top 1 emphasis and score breakdown
- potential issuance cards
- alert timeline
- pending-review state
- manual add/evidence dialog or inline panel

All remote strings and URLs must pass through existing escaping/URL validation. Do not inject provider HTML.

**Step 5: Implement loading and refresh behavior**

- Add `getChainEcosystemPayload`, `postChainEcosystemAction`, `loadChainEcosystem`, and `renderChainEcosystem`.
- Keep cached content visible during refresh.
- Poll only while `currentMode === "chains"`; use a 60-second UI cadence and let the server control provider TTLs.
- Display “数据延迟” from `stale`/`sourceHealth` without replacing content with an error panel.

**Step 6: Run the UI contract test**

Run `node --test tests/price_watch_chain_ecosystem.test.js`.

Expected: PASS.

**Step 7: Commit page behavior**

```powershell
git add price-watch.html price-watch.js tests/price_watch_chain_ecosystem.test.js
git commit -m "feat: render chain ecosystem monitor"
```

### Task 7: Visual consistency and responsive layout

**Files:**
- Modify: `styles.css:10224-10350`
- Modify: `styles.css:11860-12045`
- Modify: `tests/price_watch_chain_ecosystem.test.js`

**Step 1: Extend the UI contract test**

Assert that `styles.css` contains `.price-watch-grid.is-chains`, `.chain-ecosystem-console`, `.chain-ecosystem-sidebar`, `.chain-market-card`, `.chain-ranking-row`, `.chain-potential-pool`, and responsive rules.

**Step 2: Run the test and verify failure**

Run `node --test tests/price_watch_chain_ecosystem.test.js`.

Expected: FAIL on missing CSS hooks.

**Step 3: Style by reusing the current design system**

- Change the mode grid from eight to nine columns on desktop; retain existing horizontal overflow/wrapping behavior at narrow widths.
- Use existing variables and 7px card radii.
- Keep the left sidebar near 300px and let the right panel consume remaining width.
- Use amber only for active selection, Top 1, and primary evidence; keep secondary rows neutral.
- Align Top 5 rank, token/project name, market indicators, score, and change columns across cards.
- Add focus-visible states and minimum 36px interactive height.
- At <=980px collapse the layout to one column; at <=640px stack score details and avoid clipped labels.

Do not alter shared color variables or existing monitor components to make the new mode fit.

**Step 4: Run the UI test and inspect at three widths**

Run:

```powershell
node --test tests/price_watch_chain_ecosystem.test.js
```

Then inspect `price-watch.html?mode=chains` at approximately 1440px, 980px, and 390px widths. Expected:

- no horizontal page overflow
- balanced sidebar/main widths
- consistent card edges and spacing
- readable Top 5 columns
- existing tabs and modes unchanged

**Step 5: Commit styles**

```powershell
git add styles.css tests/price_watch_chain_ecosystem.test.js
git commit -m "style: match chain monitor to price watch design"
```

### Task 8: Background refresh and high-confidence desktop alerts

**Files:**
- Modify: `chain_ecosystem_monitor.py`
- Modify: `server.py:17770-18960`
- Modify: `server.py:20680-20705`
- Modify: `tests/test_chain_ecosystem_monitor.py`
- Modify: `tests/test_desktop_alert_priority.py`

**Step 1: Add failing scheduler and alert tests**

Require:

- only one background chain refresh loop starts
- each chain respects its refresh interval and provider backoff
- first run establishes a baseline and does not replay historical alerts
- only the five approved event types can reach desktop delivery
- duplicate dedupe keys never launch twice
- chain alerts use priority below active price signals but above ordinary news
- source failure does not emit a leader-change or surge alert

**Step 2: Run focused tests and verify failure**

```powershell
python -m unittest tests.test_chain_ecosystem_monitor tests.test_desktop_alert_priority -v
```

**Step 3: Implement the refresh loop and alert adapter**

- Add `start_chain_ecosystem_monitor()` and a daemon loop with per-chain cadence and exception isolation.
- Start it from `main()` after the current price/WeChat monitors.
- Convert unsent, high-confidence `AlertEvent` rows to the existing `launch_desktop_alert` shape with stable `eventId`, concise Chinese title, body, URL, and speech.
- Mark an event delivered only after `launch_desktop_alert` accepts it.
- Keep page timeline alerts even if desktop delivery is disabled.

**Step 4: Run focused tests**

Expected: PASS.

**Step 5: Commit scheduler and alert delivery**

```powershell
git add chain_ecosystem_monitor.py server.py tests/test_chain_ecosystem_monitor.py tests/test_desktop_alert_priority.py
git commit -m "feat: monitor chain ecosystems in background"
```

### Task 9: Regression, live smoke test, and documentation

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `.env.production.example`
- Test: `tests/test_chain_ecosystem_monitor.py`
- Test: `tests/price_watch_chain_ecosystem.test.js`
- Test: existing non-dragon Python tests

**Step 1: Document configuration and operation**

Document optional settings for refresh cadence, GitHub token, liquidity/volume thresholds, database location, and disabling the chain monitor. State that no paid key is required for baseline operation.

**Step 2: Run focused verification**

```powershell
python -m unittest tests.test_chain_ecosystem_monitor tests.test_desktop_alert_priority -v
node --test tests/price_watch_chain_ecosystem.test.js
python -m py_compile chain_ecosystem_monitor.py server.py
```

Expected: all PASS and exit 0.

**Step 3: Run relevant regression tests**

```powershell
python -m unittest tests.test_market_alert_speech tests.test_event_monitor tests.test_wechat_group_monitor tests.test_wechat_opportunity_lifecycle -v
```

Expected: all existing monitor and WeChat tests PASS.

**Step 4: Run a local smoke test**

Start the existing server normally, then verify:

- `GET /api/health` remains healthy.
- `GET /api/chain-ecosystem` returns the seeded chain and explicit source state.
- `price-watch.html?mode=chains` renders without console errors.
- Refresh keeps old content visible while work runs.
- Robinhood Chain facts link to official evidence.
- A provider timeout produces a stale/degraded label instead of an empty dashboard.
- Manual add/confirm controls require login and succeed when logged in.

**Step 5: Review the diff for scope and visual regressions**

Confirm that:

- no secret, runtime database, provider response dump, or local browser profile is staged
- no `dragon-wave*` or `industry_chain_dragon_skill/` file changed
- WeChat monitor behavior and five-second page polling remain unchanged
- new styling uses existing variables and component proportions

**Step 6: Commit documentation and verification changes**

```powershell
git add README.md .env.example .env.production.example
git commit -m "docs: explain chain ecosystem monitor"
```

### Task 10: Final acceptance checkpoint

**Files:**
- Review only: all files changed by Tasks 1-9

**Step 1: Produce an acceptance checklist**

Record the result of every item in the design document's “验收” section, including screenshots at desktop and mobile widths.

**Step 2: Record known data limitations**

List unsupported networks, provider coverage gaps, delayed metrics, and any market classification awaiting manual confirmation. Do not silently fill gaps with AI output.

**Step 3: Ask for user visual approval**

Show the user the live “公链生态” page before any later integration with “龙头起爆策略” or other notification channels.
