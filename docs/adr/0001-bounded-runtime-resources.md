# ADR-0001: Bound runtime concurrency and ephemeral storage

- Status: Accepted
- Date: 2026-09-07

## Context

The dashboard is a continuously running local monitor. Its market, X, structure, prior-high, News Trade, chain research, QQ/WeChat, and desktop-alert functions all share one Python service. The original implementation created a new timeframe executor inside every concurrently scanned symbol. At full load, eight structure workers and four new-token workers could each create several more threads, causing a large startup peak and retained Python memory. Several timestamped dictionaries kept expired values indefinitely. Local verification browser profiles, downloaded installers, build outputs, and expired WeChat QR images also accumulated beside durable monitoring data.

The user-visible pages, API contracts, ranking logic, monitor cadence, alert behavior, and durable SQLite/JSON state must remain unchanged.

## Decision

1. Keep symbol-level monitoring parallel, but route timeframe, quote, market-source, X-source, and X-mirror work through reusable bounded executors.
2. Preserve the existing source fallback order and wait for the same requested results; only the maximum simultaneous upstream pressure changes.
3. Apply TTL and entry-count limits to transient in-memory caches. Durable snapshots remain on disk and are not subject to these limits.
4. Build the expensive user-independent News Trade discovery snapshot once, persist it, and refresh it in the background. Per-user AI settings and exchange-wallet readiness remain request-time overlays.
5. Retain at most eight recent WeChat authorization QR images for 30 minutes and remove abandoned atomic-write temporary files.
6. Treat packaged applications, PyInstaller output, downloaded installers, browser verification profiles, candle audit fixtures, screenshots, and Python bytecode as reproducible workspace artifacts. Keep authentication databases, monitoring history, user settings, the local translation environment, runtime integrations, and installed development dependencies.

## Alternatives considered

- Split every monitor into a separate service. This offers isolation but would duplicate Python runtimes, increase memory, complicate local startup, and raise migration risk.
- Reduce the number of monitored symbols or disable data sources. This would save resources but change product behavior and coverage.
- Delete all runtime data or development dependencies. This gives a large one-time disk reduction but loses state or makes existing workflows unavailable until reinstallation.
- Keep per-symbol nested executors and rely on garbage collection. Executor shutdown does not prevent peak thread stacks and Python retains memory arenas after large concurrent allocations.

## Consequences

- Positive: thread count and peak allocations are bounded independently of monitor-pool size; upstream APIs and the local proxy receive less burst traffic; expired candles and discovery results cannot grow without limit.
- Positive: workspace cleanup is repeatable and protects durable runtime state through an explicit allowlist of generated targets.
- Neutral: when many symbols become due at exactly the same time, work queues briefly instead of opening dozens of simultaneous network requests. Monitor membership, scheduling priority, and requested timeframes are unchanged.
- Negative: shared executors are process-wide dependencies and must be shut down during service exit.

## Verification

- Workspace size: 5,046,918,788 bytes before, about 1.48 GB after cleanup (70.6% smaller).
- Warm service process: RSS reduced from 601,640,960 bytes to about 516,816,896 bytes while the live pages and monitors were active. The separately spawned, short-lived local Codex AI process is reported independently.
- News Trade cached request: reduced from 52–78 seconds to 0.5–1.0 seconds after the shared snapshot is available.
- Complete Python suite: 421 tests passed. Complete Node suite: 282 tests passed.
- Health, market-hot, gainers, price-watch, structure, X, and News Trade APIs were checked after restart.
- Authentication databases, monitor history/state, translation environment, chat integrations, installed dependencies, and deliverables were preserved by cleanup.
