# Dual-Layer Consolidation Breakout Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Retain high-recall causal consolidation-breakout candidates without turning them into noisy B markers, while allowing only high-certainty executions to trigger chart markers, popups, and voice alerts.

**Architecture:** The shared Dragon Wave engine will keep its existing high-precision `signals` path and add a separate `retainedCandidates` output for completed-candle breakouts of a real consolidation boundary that fail only soft execution gates. Hard structural vetoes remain excluded. The strategy page will expose these candidates only in a ledger tab; the chart and live monitor continue to consume `signals` and `pending`, so candidates cannot alert or trade. Existing feedback can promote a reviewed candidate or permanently deny the exact candle.

**Tech Stack:** Plain JavaScript strategy engine and canvas UI, Node test runner, Python monitoring service, SQLite-backed feedback.

---

### Task 1: Specify the causal retention boundary

**Files:**
- Modify: `tests/dragon_wave_engine.test.js`
- Modify: `dragon-wave-engine.js`

**Steps:**
1. Add a failing test where a mature base/triangle crosses its true prior high but fails a soft quality gate.
2. Assert that it is absent from `signals`, present once in `retainedCandidates`, and carries its original rejection reasons.
3. Add hard-veto cases for an ascending wedge/channel, mother-box interior, gap above trigger, more-than-7% launch distance, missing prior advance, and broken/hollow platform.
4. Implement a causal classifier using only the selected candle and completed candles before it.
5. Deduplicate candidates by candle and trigger zone without suppressing later re-armed platform attempts.

### Task 2: Preserve the precision execution path

**Files:**
- Modify: `dragon-wave-engine.js`
- Test: `tests/dragon_wave_engine.test.js`

**Steps:**
1. Leave `signals`, `pending`, lifecycle re-arm, and all existing hard vetoes unchanged.
2. Add candidate metadata: `candidateTier`, `executionAllowed`, `candidateReasons`, and `featureCutoff`.
3. Confirm that a retained candidate never becomes a B unless the original execution path or explicit human confirmation promotes it.
4. Re-run all existing engine regressions, including 1-minute whitelist and permanent noise filters.

### Task 3: Add a quiet candidate review surface

**Files:**
- Modify: `dragon-wave.html`
- Modify: `dragon-wave.js`
- Modify: `dragon-wave.css`
- Test: `tests/dragon_wave_page.test.js`

**Steps:**
1. Add a `候选` ledger filter and candidate count.
2. Render retained candidates as review records with a neutral `CANDIDATE` badge and their soft execution reasons.
3. Do not render candidate markers on the K-line chart by default.
4. Allow existing confirm, pending, and permanent-denial controls to review candidate records.
5. Ensure exact permanent denial still blocks later strategy restoration.

### Task 4: Keep realtime monitoring quiet

**Files:**
- Modify: `tests/dragon_wave_monitor_bridge.test.js`
- Verify: `tools/dragon_wave_monitor_bridge.js`

**Steps:**
1. Add a regression proving `retainedCandidates` are not exported as monitor signals.
2. Confirm popup and voice alert code remains driven only by `signals`.
3. Confirm later strategy revisions automatically reach the monitor through the shared engine import.

### Task 5: Version and full verification

**Files:**
- Modify: `dragon-wave.js`
- Modify: `dragon-wave.html`
- Modify: `server.py`
- Modify: `tests/dragon_wave_page.test.js`

**Steps:**
1. Bump the shared strategy/cache version.
2. Run all Node tests.
3. Run all Python service tests.
4. Restart the local monitor backend and verify the health endpoint reports the new strategy version.

