# Causal Replay Performance Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce repeated parent-history computation without changing strategy outputs, causal cutoffs, confirmation records, or historical coverage.

**Architecture:** Within one context-gating call, retain the unfinished raw analysis state for completed parent candles; clone that state before evaluating each partial current candle and running the unchanged finalization. Optionally reject impossible current-parent candidates before replay. Reuse completed v90 files only through an explicit source-to-target, file-hash-bound compatibility certificate backed by equality tests.

**Tech Stack:** Node.js, CommonJS, node:test, local gzip/JSON caches, PowerShell batch runner.

## Constraints

- No threshold, interval, history-window, visual layout, or confirmation changes.
- No Git commits/pushes and no unrelated process changes.
- Preserve the baseline in `.runtime-cache/performance-baseline-20260908-0845`.
- Keep partial outputs partial; never relabel old bytes as newly computed.
- Existing worktree is shared and dirty; edit only assigned files.
- Required superpowers execution subskill is unavailable; follow the Code verification workflow in this task instead. User already authorized implementation.

### Task 1: Incremental causal analysis

Files: `dragon-wave-engine.js`, `tests/dragon_wave_causal_replay_performance.test.js`, `tests/dragon_wave_causal_prefilter.test.js`.

1. Add tests comparing full replay with repeated, advancing, and out-of-order partial-parent evaluations.
2. Separate one-candle analysis from finalization without changing its order or field values.
3. Retain only completed-parent raw state; independently clone mutable evaluations, preserving alias relationships, for partial-candle branches.
4. Keep cache lifetime to one synchronous context call and use bounded storage.
5. Prove any current-candle prefilter is a necessary condition, not a replacement strategy judgment.
6. Run new tests plus existing engine, causal, lifecycle, and vision tests.

### Task 2: Preserve completed production work

Files: `tools/dragon_wave_cache_compatibility.js`, `tools/precompute_dragon_wave_cases.js`, `tools/report_dragon_wave_precompute.js`, related tests.

1. Test exact source/target fingerprints, production options, completed context, proof hashes, and gzip hashes.
2. Share compatibility checks between producer and reporter.
3. Reject missing/stale/transitive evidence, incomplete records, and mismatched inputs; keep force rebuild behavior.
4. Keep source hash/timestamps unchanged and report compatible reuse separately.

### Task 3: Independent equality and performance evidence

Files: `tools/verify_dragon_wave_performance_parity.js`, a real-TRB causal comparison tool, and `deliverables/2026-09-08-causal-replay-performance/`.

1. Record exact production options, fixture identities/hashes, source/target engine/vision hashes.
2. Run 18 complete-result comparisons, including real causal calls and input immutability checks.
3. Compare real TRB partial-parent results across multiple cutoffs, including non-null results and out-of-order requests.
4. Record work counts and measured timings without extrapolating fixture timings to the entire batch.
5. Run the regression suite and only then issue the compatibility certificate.

### Task 4: Controlled handover

1. Pause the existing completion reminder temporarily.
2. Read fresh progress and back up the current manifest and exact completed files before switching.
3. Verify this task's wrapper/compute identities, stop only those two processes, and launch the existing hidden wrapper with the verified code.
4. Confirm completed cases are reused and the remaining case progresses; retain full history and local candle caches.
5. Update the same reminder to the new process/log identities and resume it; keep it silent on unchanged normal progress.
6. Report measured performance, actual batch progress, and remaining work. Do not claim all 425 tasks complete until full verification passes.
