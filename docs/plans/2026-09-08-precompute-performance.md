# Precompute Performance Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. That exact sub-skill is unavailable here; use the active Code planning/execution/verification workflow.

**Goal:** Reduce redundant local historical computation without changing strategy decisions, causal cutoffs, manual feedback, or data coverage.

**Architecture:** Preserve the current engine and vision module as a local immutable comparison baseline. Reuse deterministic work inside a single analysis or context call, and avoid the duplicate final full-context pass. Keep production results provisional until all timeframe context is complete; verify output equality before replacing the owned batch process.

**Tech Stack:** Node.js, JavaScript strategy modules, PowerShell detached runner, node:test.

---

## 1. Preserve baseline and scope

- Copy the current engine/vision/precompute files into `.runtime-cache/performance-baseline-20260908` without replacing any pre-existing directory.
- Preserve unrelated dirty changes. No Git push, feedback change, strategy threshold change, or competing-process termination.

## 2. Engine deterministic reuse

- Modify `dragon-wave-engine.js`; add focused tests in a separate engine performance test file.
- Reuse the exactly equivalent EMA prefix while analyzing a single immutable candle set.
- Memoize the full causal parent analysis by exact candle identities, parent start, cutoff and all analysis options within one context call. Keep final candidate selection dependent on child trigger and preconfirmation option.
- Retain only the current parent candle's relevant signal/pending output and cap reusable entries at 32 to avoid accumulating every historical rebuild in memory.
- Maintain all history, stable selection, lifecycle state and non-lookahead rules.

## 3. Visual preparation reuse

- Modify `dragon-wave-vision.js`; add separate vision performance tests.
- Share normalized candle/EMA preparation only within a well-defined immutable analysis session. Standalone public calls must remain correct when caller data changes.
- Preserve the complete visual signature byte-for-byte and its historical cutoff.

## 4. Full-batch scheduling and timing

- Modify `tools/precompute_dragon_wave_cases.js` and its batch tests.
- Reuse the last successful full input context result instead of running it twice. Do not pass previously context-gated results as raw input.
- Add fetch/analyze/context/compact-write elapsed measurements and progress timestamps. Retain failure/resume checks.

## 5. Equality and safe deployment

- Compare old/new full analysis on representative fixed fixtures and boundary cases. Compare full results, not only B counts.
- Run strategy, vision, batch, feedback, and page regressions as relevant. Avoid concurrent expensive full-history replays while production runs.
- Only after equality passes, replace this task's owned old batch with the optimized version and reuse persisted source candles. Preserve previous result files/manifests and update completion monitoring to the new batch identity.
- The runner accepts an optional Normal calculation priority for this batch, without changing its default BelowNormal or other processes. Use Normal for the replacement batch so unrelated CPU-heavy work does not starve this one; never use High/Realtime.
- Do not claim all 425 tasks are complete merely because performance tests pass; report measured timing separately from actual full-batch progress.
