from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
GROUP_COUNT_RE = re.compile(r"\s*[（(]\d+[）)]\s*$")
ROOT = Path(__file__).resolve().parent


class OneBotError(RuntimeError):
    pass


class OneBotRecoveryError(RuntimeError):
    pass


def _stable_text(value: Any) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()


def _group_key(value: Any) -> str:
    normalized = GROUP_COUNT_RE.sub("", _stable_text(value))
    return re.sub(r"[^0-9A-Za-z\u3400-\u9fff]", "", normalized).lower()


def _sender_key(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z\u3400-\u9fff]", "", _stable_text(value)).lower()


def sender_matches(expected: Any, observed: Any) -> bool:
    wanted = _sender_key(expected)
    actual = _sender_key(observed)
    if not wanted or not actual:
        return False
    return wanted == actual or (len(wanted) >= 3 and wanted in actual)


def _assert_loopback(url: str) -> None:
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https", "ws", "wss"} or parsed.hostname not in LOOPBACK_HOSTS:
        raise ValueError(f"OneBot endpoint must be loopback-only: {url}")


def flatten_onebot_message(message: Any) -> str:
    """Turn OneBot message segments into inert, bounded text."""
    if isinstance(message, str):
        return _stable_text(message)[:4000]
    if isinstance(message, dict):
        message = [message]
    if not isinstance(message, list):
        return _stable_text(message)[:4000]
    parts: list[str] = []
    labels = {
        "reply": "[回复]",
        "image": "[图片]",
        "record": "[语音]",
        "video": "[视频]",
        "json": "[卡片]",
        "xml": "[卡片]",
        "face": "[表情]",
        "forward": "[合并转发]",
    }
    for segment in message[:120]:
        if not isinstance(segment, dict):
            continue
        segment_type = str(segment.get("type") or "").strip().lower()
        data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
        if segment_type == "text":
            value = _stable_text(data.get("text"))
        elif segment_type == "at":
            name = _stable_text(data.get("name") or data.get("qq"))
            value = f"@{name}" if name else "[提及]"
        elif segment_type == "file":
            name = _stable_text(data.get("name"))
            value = f"[文件:{name}]" if name else "[文件]"
        else:
            value = labels.get(segment_type, f"[{segment_type}]" if segment_type else "")
        if value:
            parts.append(value)
    return _stable_text(" ".join(parts))[:4000]


def normalize_group_event(payload: Any, group_name: str = "") -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    post_type = str(payload.get("post_type") or "message")
    message_type = str(payload.get("message_type") or ("group" if payload.get("group_id") else ""))
    if post_type not in {"message", "message_sent"} or message_type != "group":
        return None
    content = flatten_onebot_message(payload.get("message") if "message" in payload else payload.get("raw_message"))
    if not content:
        return None
    sender_data = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    sender = _stable_text(
        sender_data.get("card")
        or sender_data.get("nickname")
        or payload.get("sender_name")
        or payload.get("user_id")
    )
    group_id = _stable_text(payload.get("group_id"))
    user_id = _stable_text(payload.get("user_id") or sender_data.get("user_id"))
    message_id = _stable_text(payload.get("message_id") or payload.get("message_seq"))
    captured_at = int(float(payload.get("time") or time.time()))
    marker = f"qq:{group_id}:{message_id}" if message_id else "qq"
    if message_id:
        raw_hash = f"qq\n{group_id}\n{message_id}"
    else:
        raw_hash = "\n".join((_group_key(group_name), _sender_key(sender), content, str(captured_at)))
    return {
        "sender": sender or "群成员",
        "content": content,
        "capturedAt": captured_at,
        "marker": marker,
        "platform": "qq",
        "hash": hashlib.sha256(raw_hash.encode("utf-8", errors="ignore")).hexdigest(),
        "groupId": group_id,
        "userId": user_id,
        "messageId": message_id,
    }


class QQOneBotClient:
    def __init__(
        self,
        http_url: str,
        ws_url: str,
        token: str,
        *,
        session: Any | None = None,
        timeout: float = 5.0,
    ) -> None:
        _assert_loopback(http_url)
        _assert_loopback(ws_url)
        self.http_url = str(http_url).rstrip("/")
        self.ws_url = str(ws_url)
        self.token = str(token or "").strip()
        if not self.token:
            raise ValueError("QQ_ONEBOT_TOKEN must not be empty")
        self.session = session or requests.Session()
        self.timeout = max(1.0, min(float(timeout), 30.0))

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def call(self, action: str, params: dict[str, Any] | None = None) -> Any:
        response = self.session.post(
            f"{self.http_url}/{str(action).lstrip('/')}",
            json=params or {},
            headers=self.headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise OneBotError("OneBot returned a non-object response")
        if str(payload.get("status") or "") != "ok" or int(payload.get("retcode") or 0) != 0:
            message = payload.get("message") or payload.get("wording") or payload.get("retcode")
            raise OneBotError(f"OneBot action {action} failed: {message}")
        return payload.get("data")

    def get_login_info(self) -> dict[str, Any]:
        return dict(self.call("get_login_info") or {})

    def get_group_list(self) -> list[dict[str, Any]]:
        data = self.call("get_group_list") or []
        return [dict(item) for item in data if isinstance(item, dict)]

    def get_group_member_list(self, group_id: str) -> list[dict[str, Any]]:
        data = self.call("get_group_member_list", {"group_id": str(group_id), "no_cache": True}) or []
        return [dict(item) for item in data if isinstance(item, dict)]

    def get_group_msg_history(self, group_id: str, count: int = 100) -> list[dict[str, Any]]:
        data = self.call("get_group_msg_history", {"group_id": str(group_id), "count": max(1, min(count, 200))}) or {}
        rows = data.get("messages") if isinstance(data, dict) else data
        return [dict(item) for item in (rows or []) if isinstance(item, dict)]

    def send_group_msg(self, group_id: str, message: str) -> dict[str, Any]:
        content = _stable_text(message)
        if not content:
            raise ValueError("QQ message must not be empty")
        return dict(self.call("send_group_msg", {"group_id": str(group_id), "message": content}) or {})


class QQOneBotBridge:
    def __init__(self, client: QQOneBotClient, *, history_interval: float = 15.0) -> None:
        self.client = client
        self.history_interval = max(0.0, float(history_interval))
        self._events: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=500))
        self._seen_ids: set[str] = set()
        self._group_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._history_at: dict[str, float] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.connected = False
        self.last_error = ""
        self.last_event_at = 0

    def _resolve_group(self, group_name: str) -> dict[str, Any]:
        key = _group_key(group_name)
        cached = self._group_cache.get(key)
        if cached and time.time() - cached[0] < 300:
            return cached[1]
        groups = self.client.get_group_list()
        matches = [item for item in groups if _group_key(item.get("group_name")) == key]
        if len(matches) != 1:
            raise OneBotError(f"群聊“{group_name}”匹配到 {len(matches)} 个结果")
        self._group_cache[key] = (time.time(), matches[0])
        return matches[0]

    def _append_event(self, payload: dict[str, Any]) -> None:
        normalized = normalize_group_event(payload)
        if not normalized:
            return
        unique_id = normalized.get("messageId") or normalized.get("hash")
        with self._lock:
            if unique_id in self._seen_ids:
                return
            self._seen_ids.add(str(unique_id))
            if len(self._seen_ids) > 5000:
                self._seen_ids = {item.get("messageId") or item["hash"] for rows in self._events.values() for item in rows}
            self._events[str(normalized.get("groupId") or "")].append(normalized)
            self.last_event_at = int(time.time())

    def _history(self, group_id: str, group_name: str) -> list[dict[str, Any]]:
        now = time.time()
        if self.history_interval and now - self._history_at.get(group_id, 0.0) < self.history_interval:
            return []
        rows = self.client.get_group_msg_history(group_id, 120)
        self._history_at[group_id] = now
        normalized: list[dict[str, Any]] = []
        for row in rows:
            row.setdefault("post_type", "message")
            row.setdefault("message_type", "group")
            row.setdefault("group_id", group_id)
            item = normalize_group_event(row, group_name=group_name)
            if item:
                normalized.append(item)
        return normalized

    def collect(self, group_name: str, sender_filter: str = "") -> dict[str, Any]:
        try:
            group = self._resolve_group(group_name)
            group_id = _stable_text(group.get("group_id"))
            history = self._history(group_id, group_name)
            with self._lock:
                live = list(self._events.get(group_id, ()))
            merged: dict[str, dict[str, Any]] = {}
            for item in history + live:
                if sender_filter and not sender_matches(sender_filter, item.get("sender")):
                    continue
                merged[str(item.get("messageId") or item.get("hash"))] = item
            messages = sorted(merged.values(), key=lambda item: int(item.get("capturedAt") or 0))[-120:]
            return {
                "ok": True,
                "status": "connected",
                "messages": messages,
                "error": "",
                "collectorMode": "onebot",
                "platform": "qq",
                "senderFilter": _stable_text(sender_filter),
                "groupId": group_id,
            }
        except Exception as exc:
            self.last_error = str(exc)
            return {
                "ok": False,
                "status": "onebot_unavailable",
                "messages": [],
                "error": str(exc)[:320],
                "collectorMode": "onebot",
                "platform": "qq",
            }

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_ws, name="qq-onebot-events", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run_ws(self) -> None:
        try:
            import websocket
        except Exception as exc:
            self.last_error = str(exc)
            return
        backoff = 1.0
        while not self._stop_event.is_set():
            def on_open(_ws: Any) -> None:
                self.connected = True
                self.last_error = ""

            def on_message(_ws: Any, raw: str) -> None:
                try:
                    payload = json.loads(raw)
                    if isinstance(payload, dict):
                        self._append_event(payload)
                except Exception as exc:
                    self.last_error = str(exc)

            def on_error(_ws: Any, error: Any) -> None:
                self.last_error = str(error)

            def on_close(_ws: Any, _code: Any, _reason: Any) -> None:
                self.connected = False

            app = websocket.WebSocketApp(
                self.client.ws_url,
                header=[f"Authorization: Bearer {self.client.token}"],
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            try:
                app.run_forever(ping_interval=20, ping_timeout=8)
            except Exception as exc:
                self.last_error = str(exc)
            self.connected = False
            if self._stop_event.wait(backoff):
                break
            backoff = min(30.0, backoff * 2.0)

    def health(self) -> dict[str, Any]:
        try:
            login = self.client.get_login_info()
            return {
                "ok": True,
                "status": "connected",
                "connected": self.connected,
                "userId": str(login.get("user_id") or ""),
                "nickname": str(login.get("nickname") or ""),
                "lastEventAt": self.last_event_at,
                "error": self.last_error,
            }
        except Exception as exc:
            return {"ok": False, "status": "onebot_unavailable", "connected": False, "error": str(exc)[:320]}


def recover_local_napcat() -> dict[str, Any]:
    """Recover only the configured local QQ/NapCat instance after a sustained API outage."""
    if os.name != "nt":
        return {"ok": False, "skipped": True, "reason": "NapCat recovery is Windows-only"}
    script = Path(
        os.getenv("QQ_NAPCAT_RECOVERY_SCRIPT")
        or ROOT / "tools" / "recover_napcat_bridge.ps1"
    ).resolve()
    runtime_dir = Path(
        os.getenv("QQ_NAPCAT_RUNTIME_DIR")
        or ROOT / ".runtime-tools" / "napcat-v4.18.19" / "shell"
    ).resolve()
    qq_path_value = str(os.getenv("QQ_CLIENT_PATH") or "").strip()
    account = str(os.getenv("QQ_ONEBOT_ACCOUNT") or "").strip()
    if not script.is_file():
        raise OneBotRecoveryError(f"NapCat recovery script not found: {script}")
    if not runtime_dir.is_dir():
        raise OneBotRecoveryError(f"NapCat runtime not found: {runtime_dir}")
    if not qq_path_value:
        raise OneBotRecoveryError("QQ_CLIENT_PATH is required for local recovery")
    qq_path = Path(qq_path_value).resolve()
    if not qq_path.is_file():
        raise OneBotRecoveryError(f"QQ client not found: {qq_path}")
    if not account.isdigit():
        raise OneBotRecoveryError("QQ_ONEBOT_ACCOUNT must contain digits only")

    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-RuntimeDir",
        str(runtime_dir),
        "-QqPath",
        str(qq_path),
        "-Account",
        account,
    ]
    completed = subprocess.run(
        command,
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=100,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    output = str(completed.stdout or "").strip()
    if completed.returncode != 0:
        raise OneBotRecoveryError(output[-500:] or f"NapCat recovery exited {completed.returncode}")
    for line in reversed(output.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {"ok": True, "status": "recovery_started", "output": output[-300:]}


class QQOneBotSupervisor:
    def __init__(
        self,
        bridge: QQOneBotBridge,
        *,
        interval: float = 10.0,
        failure_threshold: int = 3,
        recovery_cooldown: float = 180.0,
        recover: Any = recover_local_napcat,
    ) -> None:
        self.bridge = bridge
        self.interval = max(2.0, float(interval))
        self.failure_threshold = max(2, int(failure_threshold))
        self.recovery_cooldown = max(30.0, float(recovery_cooldown))
        self.recover = recover
        self.failures = 0
        self.last_recovery_at = 0.0
        self.last_recovery_error = ""
        self.last_recovery_result: dict[str, Any] = {}
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self, *, now: float | None = None) -> dict[str, Any]:
        checked_at = float(now if now is not None else time.time())
        health = self.bridge.health()
        if health.get("ok"):
            self.failures = 0
            return {"ok": True, "health": health, "recovered": False}

        self.failures += 1
        result: dict[str, Any] = {
            "ok": False,
            "health": health,
            "failures": self.failures,
            "recovered": False,
        }
        if self.failures < self.failure_threshold:
            return result
        if checked_at - self.last_recovery_at < self.recovery_cooldown:
            return {**result, "cooldown": True}

        self.last_recovery_at = checked_at
        try:
            recovery = self.recover()
            self.last_recovery_result = dict(recovery or {})
            self.last_recovery_error = ""
            self.failures = 0
            return {**result, "recovered": True, "recovery": self.last_recovery_result}
        except Exception as exc:
            self.last_recovery_error = str(exc)[:500]
            return {**result, "recoveryError": self.last_recovery_error}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="qq-onebot-supervisor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval):
            self.run_once()


_BRIDGE: QQOneBotBridge | None = None
_BRIDGE_LOCK = threading.Lock()
_SUPERVISOR: QQOneBotSupervisor | None = None
_SUPERVISOR_LOCK = threading.Lock()


def onebot_enabled() -> bool:
    return str(os.getenv("QQ_ONEBOT_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}


def onebot_recovery_enabled() -> bool:
    # Recovery launches a managed NapCat QQ session. Keep it opt-in so a temporary
    # OneBot outage can never take over or terminate the user's interactive QQ.
    return os.name == "nt" and str(os.getenv("QQ_ONEBOT_RECOVERY_ENABLED", "0")).strip().lower() in {
        "1", "true", "yes", "on"
    }


def get_qq_onebot_bridge() -> QQOneBotBridge | None:
    global _BRIDGE
    if not onebot_enabled():
        return None
    with _BRIDGE_LOCK:
        if _BRIDGE is None:
            client = QQOneBotClient(
                os.getenv("QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:3000"),
                os.getenv("QQ_ONEBOT_WS_URL", "ws://127.0.0.1:3001"),
                os.getenv("QQ_ONEBOT_TOKEN", ""),
            )
            _BRIDGE = QQOneBotBridge(client)
        return _BRIDGE


def collect_qq_onebot_messages(group_name: str, sender_filter: str = "") -> dict[str, Any]:
    try:
        bridge = get_qq_onebot_bridge()
        if bridge is None:
            return {
                "ok": False,
                "status": "onebot_disabled",
                "messages": [],
                "error": "QQ OneBot 后台通道未启用",
                "collectorMode": "onebot",
                "platform": "qq",
            }
        bridge.start()
        return bridge.collect(group_name, sender_filter)
    except Exception as exc:
        return {
            "ok": False,
            "status": "onebot_unavailable",
            "messages": [],
            "error": str(exc)[:320],
            "collectorMode": "onebot",
            "platform": "qq",
        }


def start_qq_onebot_bridge() -> None:
    global _SUPERVISOR
    bridge = get_qq_onebot_bridge()
    if bridge:
        bridge.start()
        if onebot_recovery_enabled():
            with _SUPERVISOR_LOCK:
                if _SUPERVISOR is None:
                    _SUPERVISOR = QQOneBotSupervisor(
                        bridge,
                        interval=float(os.getenv("QQ_ONEBOT_RECOVERY_INTERVAL_SECONDS", "10") or "10"),
                        failure_threshold=int(os.getenv("QQ_ONEBOT_RECOVERY_FAILURES", "3") or "3"),
                        recovery_cooldown=float(os.getenv("QQ_ONEBOT_RECOVERY_COOLDOWN_SECONDS", "180") or "180"),
                    )
                _SUPERVISOR.start()


def stop_qq_onebot_bridge() -> None:
    global _SUPERVISOR
    with _SUPERVISOR_LOCK:
        if _SUPERVISOR:
            _SUPERVISOR.stop()
            _SUPERVISOR = None
    bridge = get_qq_onebot_bridge()
    if bridge:
        bridge.stop()
