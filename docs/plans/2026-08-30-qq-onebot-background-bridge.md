# QQ OneBot Background Bridge Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task.

**Goal:** Replace visible-window QQ scraping with an authenticated localhost OneBot channel that receives group events, reads recent group history, and sends QQ messages while QQ remains in the background.

**Architecture:** NapCat v4.18.19 exposes OneBot 11 HTTP and WebSocket endpoints bound only to `127.0.0.1`. A new Python adapter owns group discovery, history reads, event normalization, reconnect/backoff, and outbound calls; the existing monitor pipeline continues to own sender filtering, symbol extraction, deduplication, structure-pool admission, and WeChat forwarding. UI/OCR remains a disabled-by-default emergency fallback rather than the primary QQ path.

**Tech Stack:** Python 3, `requests`, `websocket-client`, OneBot 11, NapCat Shell, SQLite, `unittest`.

---

### Task 1: Define the OneBot contract

**Files:**
- Create: `qq_onebot_bridge.py`
- Create: `tests/test_qq_onebot_bridge.py`

**Step 1: Write failing tests**

- Normalize `message.group.normal` events into the existing message payload shape.
- Preserve `group_id`, `user_id`, display name, timestamp, message ID, and plain text.
- Flatten text, at, reply, face, image and JSON message segments without executing embedded content.
- Reject private events, heartbeat events, wrong groups, and wrong senders.

**Step 2: Run the focused test file**

Run: `python -m unittest tests.test_qq_onebot_bridge -v`

Expected: FAIL because the adapter does not exist.

**Step 3: Implement the minimal parser and bounded in-memory event buffer**

The buffer is keyed by group ID and deduplicated by OneBot `message_id`; no message content is written outside the existing application database.

**Step 4: Re-run the focused test file**

Expected: PASS.

### Task 2: Add authenticated HTTP history and send actions

**Files:**
- Modify: `qq_onebot_bridge.py`
- Modify: `tests/test_qq_onebot_bridge.py`

**Step 1: Add failing HTTP-client tests**

- `get_login_info` is the health probe.
- `get_group_list` resolves the configured group name to an exact group ID.
- `get_group_member_list` resolves sender card/nickname and QQ number.
- `get_group_msg_history` returns recent messages after restart.
- `send_group_msg` supports explicit outbound QQ messages.
- Every request carries `Authorization: Bearer <token>` and rejects non-local endpoint hosts by default.

**Step 2: Implement timeouts, response validation and status mapping**

No automatic retry is allowed for `send_group_msg`; retrying an unknown send result could duplicate a message. Read-only requests use one bounded retry.

**Step 3: Run tests**

Run: `python -m unittest tests.test_qq_onebot_bridge -v`

Expected: PASS.

### Task 3: Connect live WebSocket events to the existing monitor

**Files:**
- Modify: `qq_onebot_bridge.py`
- Modify: `wechat_group_monitor.py`
- Modify: `server.py`
- Modify: `tests/test_wechat_group_monitor.py`
- Modify: `tests/test_wechat_opportunity_lifecycle.py`

**Step 1: Add failing integration tests**

- QQ collection prefers OneBot and works with no visible QQ window.
- History is used once at startup to avoid missing recent messages.
- Existing sender filter `鲸鱼🐳PP` remains exact/normalized.
- Existing dedupe prevents historical replay from forwarding twice.
- Mentioned symbols still enter the structure monitor for 30 days.

**Step 2: Implement the singleton bridge and startup lifecycle**

Start the event listener with the server, reconnect with capped exponential backoff, expose health in the existing monitor payload, and fall back to UI/OCR only when explicitly enabled.

**Step 3: Run integration tests**

Run: `python -m unittest tests.test_qq_onebot_bridge tests.test_wechat_group_monitor tests.test_wechat_opportunity_lifecycle -v`

Expected: PASS.

### Task 4: Add local-only configuration

**Files:**
- Modify: `.env.example`
- Modify: `.env.production.example`
- Modify: `README.md`
- Modify: `.gitignore`

**Step 1: Document variables**

- `QQ_ONEBOT_ENABLED=1`
- `QQ_ONEBOT_HTTP_URL=http://127.0.0.1:3000`
- `QQ_ONEBOT_WS_URL=ws://127.0.0.1:3001`
- `QQ_ONEBOT_TOKEN=<random secret>`
- `QQ_UI_FALLBACK_ENABLED=0`

**Step 2: Protect runtime files**

Ignore the downloaded bridge runtime, generated NapCat config and tokens. Never commit the live token.

### Task 5: Install and configure NapCat Shell

**Files:**
- Runtime only: `.runtime-tools/napcat/`
- Runtime only: NapCat `config/onebot11_<qq>.json`

**Step 1: Download only the official v4.18.19 release asset**

Use `NapCat.Shell.zip` from `NapNeko/NapCatQQ` GitHub Releases and verify its SHA-256 locally before extraction.

**Step 2: Configure loopback-only services**

Create an HTTP server on `127.0.0.1:3000` and WebSocket server on `127.0.0.1:3001`, both using the same randomly generated token. Do not enable CORS or bind to `0.0.0.0`.

**Step 3: Start NapCat with the already logged-in QQ account**

Launch the official Shell entry point in the background. If QQ requires one-time device confirmation, stop and ask the user to complete it manually.

**Step 4: Verify health**

Call `get_login_info`, resolve the exact group `地表最强bsc eth (337)`, and confirm the target member `鲸鱼🐳PP` is present.

### Task 6: End-to-end proof

**Files:**
- No additional production files expected.

**Step 1: Read one historical message**

Fetch one recent message authored by `鲸鱼🐳PP` from the resolved group while the QQ window is minimized or closed to tray.

**Step 2: Forward one labeled test message to WeChat**

Use the existing durable outbox and exact target `文件传输助手`; record delivery status and do not replay the same history item.

**Step 3: Verify QQ background send without sending unsolicited content**

Exercise `send_group_msg` against a dry-run/mocked endpoint in automated tests. A real QQ message is sent only when the user explicitly requests the destination and content.

**Step 4: Run regression suite and restart**

Run: `python -m unittest tests.test_qq_onebot_bridge tests.test_wechat_group_monitor tests.test_wechat_opportunity_lifecycle tests.test_market_alert_speech -v`

Expected: PASS, followed by one healthy local service process on port 8765.
