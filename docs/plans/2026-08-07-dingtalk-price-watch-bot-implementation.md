# DingTalk Price Watch Bot Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an independently configured DingTalk delivery worker that reuses the existing AICoin previous-high monitoring signals.

**Architecture:** Poll the existing `/api/price-watch` endpoint and treat each symbol's `latestAlertEpisode` as an immutable signal sequence. Keep a DingTalk-only local checkpoint so Discord delivery and DingTalk delivery remain independent.

**Tech Stack:** Python 3, `requests`, `unittest`, PowerShell, DingTalk custom robot webhook.

---

### Task 1: Message signing and formatting

**Files:**
- Create: `dingtalk_price_watch_bot.py`
- Create: `tests/test_dingtalk_price_watch_bot.py`

**Steps:**

1. Write tests for deterministic HMAC-SHA256 signing, Markdown payload formatting, and secret redaction.
2. Run the focused test file and verify it fails because the module does not exist.
3. Implement the signing and formatting helpers without network side effects during import.
4. Run the focused tests and verify they pass.

### Task 2: Independent delivery state

**Files:**
- Modify: `dingtalk_price_watch_bot.py`
- Modify: `tests/test_dingtalk_price_watch_bot.py`

**Steps:**

1. Write tests for first-run baselining, new episode selection, successful checkpointing, duplicate filtering, and failed-send retry behavior.
2. Run the tests and verify the new cases fail.
3. Implement atomic JSON state persistence and one-cycle polling with injectable HTTP clients.
4. Run the focused tests and verify they pass.

### Task 3: Runnable worker and configuration

**Files:**
- Modify: `dingtalk_price_watch_bot.py`
- Create: `start-dingtalk-price-watch.ps1`
- Modify: `.env.example`
- Modify: `.env.production.example`
- Modify: `README.md`

**Steps:**

1. Add CLI options for `--once`, API URL, polling interval, timeout, state path, and first-run behavior.
2. Add environment examples for the DingTalk-only settings.
3. Add a PowerShell launcher that uses the project Python environment when available.
4. Document DingTalk robot creation, security configuration, startup, and a single-cycle test.

### Task 4: Verification

**Files:**
- Test: `tests/test_dingtalk_price_watch_bot.py`

**Steps:**

1. Run the full focused unit-test suite.
2. Run Python bytecode compilation for the new module.
3. Run `--help` and an intentional missing-Webhook `--once` check.
4. Inspect the Git diff to ensure no secret values were added and no Discord implementation was changed.
