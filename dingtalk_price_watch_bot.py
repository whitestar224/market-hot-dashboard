"""Independent DingTalk delivery worker for the existing price-watch signals."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests


ROOT = Path(__file__).resolve().parent
DEFAULT_API_URL = "http://127.0.0.1:8765/api/price-watch"
DEFAULT_STATE_PATH = ROOT / ".runtime-cache" / "dingtalk-price-watch-state.json"
USER_AGENT = "XingyunSociety/DingTalkPriceWatch"
CHINA_TIMEZONE = timezone(timedelta(hours=8))


class DingTalkDeliveryError(RuntimeError):
    """Raised when DingTalk accepts HTTP but rejects the robot message."""


@dataclass(frozen=True)
class Config:
    api_url: str
    dashboard_url: str
    webhook_url: str
    secret: str
    poll_seconds: int
    timeout_seconds: int
    state_path: Path
    send_existing_on_first_run: bool = False


def load_env_file(path: Path) -> None:
    """Load a simple .env file without overriding explicitly set variables."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def default_dashboard_url(api_url: str) -> str:
    parsed = urlparse(api_url)
    path = parsed.path or "/"
    if path.rstrip("/").endswith("/api/price-watch"):
        path = path.rstrip("/")[: -len("/api/price-watch")] + "/price-watch.html"
    else:
        path = "/price-watch.html"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def build_signed_webhook(webhook_url: str, secret: str, timestamp_ms: int | None = None) -> str:
    """Add DingTalk's timestamp and HMAC-SHA256 signature to a robot webhook."""
    if not secret:
        return webhook_url
    timestamp = int(timestamp_ms if timestamp_ms is not None else time.time() * 1000)
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    signature = base64.b64encode(
        hmac.new(secret.encode("utf-8"), string_to_sign, digestmod=hashlib.sha256).digest()
    ).decode("utf-8")
    parsed = urlparse(webhook_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({"timestamp": str(timestamp), "sign": signature})
    return urlunparse(parsed._replace(query=urlencode(query)))


def format_price(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    if number <= 0:
        return "--"
    if number >= 1:
        return f"{number:,.2f}"
    if number >= 0.01:
        return f"{number:,.4f}".rstrip("0").rstrip(".")
    return f"{number:,.8f}".rstrip("0").rstrip(".")


def build_markdown_payload(event: dict[str, Any], dashboard_url: str = "") -> dict[str, Any]:
    symbol = str(event.get("symbol") or "--").strip().upper()
    try:
        distance = float(event.get("distancePct") or 0)
    except (TypeError, ValueError):
        distance = 0.0
    setup_label = "回撤后再接近" if event.get("setupType") == "retest" else "盘整后再接近"
    signal_label = "首次有效突破待确认" if event.get("isFirstCandidate") else "非首次突破信号"
    checked_at = int(event.get("latestAlertAt") or event.get("checkedAt") or time.time() * 1000)
    checked_text = datetime.fromtimestamp(checked_at / 1000, CHINA_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    title = f"币种监控｜{symbol} 接近最近 7 日前高"
    lines = [
        f"### 🚨 {title}",
        "",
        f"- **信号**：{signal_label}",
        f"- **结构**：{setup_label}",
        f"- **当前价**：{format_price(event.get('currentPrice'))} USDT",
        f"- **最近 7 日前高**：{format_price(event.get('weekHigh'))} USDT",
        f"- **距离前高**：**{distance:.2f}%**",
        f"- **行情来源**：{str(event.get('provider') or '市场行情')[:80]}",
        f"- **触发时间**：{checked_text}",
    ]
    if dashboard_url:
        lines.extend(["", f"[打开价格监控面板]({dashboard_url})"])
    return {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": "\n".join(lines)},
        "at": {"atMobiles": [], "isAtAll": False},
    }


def build_test_payload() -> dict[str, Any]:
    return {
        "msgtype": "markdown",
        "markdown": {
            "title": "币种监控｜钉钉机器人连接测试",
            "text": (
                "### ✅ 币种监控机器人连接成功\n\n"
                f"- **测试时间**：{datetime.now(CHINA_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}\n"
                "- **状态**：Webhook 与安全签名配置可用"
            ),
        },
        "at": {"atMobiles": [], "isAtAll": False},
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "episodes": {}, "alertTimes": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("state root must be an object")
    raw_episodes = payload.get("episodes")
    raw_times = payload.get("alertTimes")
    if raw_episodes is not None and not isinstance(raw_episodes, dict):
        raise ValueError("state episodes must be an object")
    if raw_times is not None and not isinstance(raw_times, dict):
        raise ValueError("state alertTimes must be an object")
    episodes = {
        str(symbol).upper(): max(0, int(episode or 0))
        for symbol, episode in (raw_episodes or {}).items()
        if str(symbol).strip()
    }
    alert_times = {
        str(symbol).upper(): max(0, int(alert_at or 0))
        for symbol, alert_at in (raw_times or {}).items()
        if str(symbol).strip()
    }
    return {"version": 1, "episodes": episodes, "alertTimes": alert_times}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "episodes": state.get("episodes") or {},
        "alertTimes": state.get("alertTimes") or {},
        "updatedAt": int(time.time() * 1000),
    }
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def fetch_watch_items(config: Config, session: Any = requests) -> list[dict[str, Any]]:
    response = session.get(
        config.api_url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("ok") is False:
        raise RuntimeError("价格监控接口返回失败")
    items = payload.get("items")
    if not isinstance(items, list):
        raise RuntimeError("价格监控接口没有返回币种列表")
    return [item for item in items if isinstance(item, dict)]


def send_dingtalk_payload(
    config: Config,
    payload: dict[str, Any],
    *,
    session: Any = requests,
    timestamp_ms: int | None = None,
) -> dict[str, Any]:
    webhook = build_signed_webhook(config.webhook_url, config.secret, timestamp_ms)
    response = session.post(
        webhook,
        json=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict) or int(result.get("errcode", -1)) != 0:
        message = str(result.get("errmsg") if isinstance(result, dict) else "invalid response")
        code = result.get("errcode") if isinstance(result, dict) else "unknown"
        raise DingTalkDeliveryError(f"DingTalk rejected message ({code}): {message[:160]}")
    return result


def event_sequence(event: dict[str, Any]) -> tuple[int, int]:
    try:
        episode = max(0, int(event.get("latestAlertEpisode") or 0))
    except (TypeError, ValueError):
        episode = 0
    try:
        alert_at = max(0, int(event.get("latestAlertAt") or 0))
    except (TypeError, ValueError):
        alert_at = 0
    return episode, alert_at


def is_new_event(event: dict[str, Any], state: dict[str, Any]) -> bool:
    symbol = str(event.get("symbol") or "").strip().upper()
    if not symbol:
        return False
    episode, alert_at = event_sequence(event)
    if episode <= 0:
        return False
    previous_episode = int((state.get("episodes") or {}).get(symbol) or 0)
    previous_alert_at = int((state.get("alertTimes") or {}).get(symbol) or 0)
    return episode > previous_episode or (alert_at > previous_alert_at and episode != previous_episode)


def checkpoint_event(state: dict[str, Any], event: dict[str, Any]) -> None:
    symbol = str(event.get("symbol") or "").strip().upper()
    if not symbol:
        return
    episode, alert_at = event_sequence(event)
    state.setdefault("episodes", {})[symbol] = episode
    state.setdefault("alertTimes", {})[symbol] = alert_at


def safe_error_text(error: Exception, config: Config) -> str:
    text = str(error)
    for secret in (config.secret, config.webhook_url):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    parsed = urlparse(config.webhook_url)
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in {"access_token", "sign"} and value:
            text = text.replace(value, "[REDACTED]")
    return text[:240]


def run_cycle(config: Config, session: Any = requests) -> dict[str, Any]:
    items = fetch_watch_items(config, session=session)
    state_exists = config.state_path.exists()
    try:
        state = load_state(config.state_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        # A broken or partial state must never cause a burst of historical alerts.
        state_exists = False
        state = {"version": 1, "episodes": {}, "alertTimes": {}}

    valid_items = [item for item in items if str(item.get("symbol") or "").strip()]
    if not state_exists and not config.send_existing_on_first_run:
        for item in valid_items:
            checkpoint_event(state, item)
        save_state(config.state_path, state)
        return {"ok": True, "checked": len(valid_items), "baselined": len(valid_items), "pending": 0, "sent": 0, "failed": 0, "errors": []}

    if not state_exists and config.send_existing_on_first_run:
        # Baseline inactive historical signals; only current near-high signals are eligible.
        for item in valid_items:
            if item.get("status") != "near":
                checkpoint_event(state, item)

    pending = [item for item in valid_items if is_new_event(item, state)]
    pending.sort(key=lambda item: (event_sequence(item)[1], str(item.get("symbol") or "")))
    sent = 0
    failed = 0
    errors: list[str] = []
    for event in pending:
        try:
            send_dingtalk_payload(
                config,
                build_markdown_payload(event, config.dashboard_url),
                session=session,
            )
            checkpoint_event(state, event)
            save_state(config.state_path, state)
            sent += 1
        except Exception as exc:
            failed += 1
            errors.append(f"{str(event.get('symbol') or '--')}: {safe_error_text(exc, config)}")

    if not config.state_path.exists():
        save_state(config.state_path, state)
    return {
        "ok": failed == 0,
        "checked": len(valid_items),
        "baselined": 0,
        "pending": len(pending),
        "sent": sent,
        "failed": failed,
        "errors": errors,
    }


def config_from_args(args: argparse.Namespace) -> Config:
    api_url = args.api_url or os.getenv("DINGTALK_PRICE_WATCH_API_URL", DEFAULT_API_URL)
    dashboard_url = os.getenv("DINGTALK_PRICE_WATCH_DASHBOARD_URL") or default_dashboard_url(api_url)
    raw_state_path = args.state_path or os.getenv("DINGTALK_PRICE_WATCH_STATE_PATH", str(DEFAULT_STATE_PATH))
    state_path = Path(raw_state_path)
    if not state_path.is_absolute():
        state_path = ROOT / state_path
    return Config(
        api_url=api_url.strip(),
        dashboard_url=dashboard_url.strip(),
        webhook_url=os.getenv("DINGTALK_PRICE_WATCH_WEBHOOK_URL", "").strip(),
        secret=os.getenv("DINGTALK_PRICE_WATCH_SECRET", "").strip(),
        poll_seconds=args.poll_seconds or env_int("DINGTALK_PRICE_WATCH_POLL_SECONDS", 60, 10, 3600),
        timeout_seconds=args.timeout_seconds or env_int("DINGTALK_PRICE_WATCH_TIMEOUT_SECONDS", 10, 3, 60),
        state_path=state_path,
        send_existing_on_first_run=args.send_existing or env_bool("DINGTALK_PRICE_WATCH_SEND_EXISTING_ON_FIRST_RUN"),
    )


def validate_config(config: Config) -> None:
    if not config.webhook_url:
        raise ValueError("缺少 DINGTALK_PRICE_WATCH_WEBHOOK_URL")
    parsed = urlparse(config.webhook_url)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError("钉钉 Webhook 必须是完整的 HTTPS 地址")
    api = urlparse(config.api_url)
    if api.scheme.lower() not in {"http", "https"} or not api.netloc:
        raise ValueError("价格监控 API 地址无效")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="独立钉钉热门币前高监控推送机器人")
    parser.add_argument("--once", action="store_true", help="只检查并发送一轮后退出")
    parser.add_argument("--test-message", action="store_true", help="发送一条钉钉连接测试消息后退出")
    parser.add_argument("--api-url", help="价格监控接口地址")
    parser.add_argument("--poll-seconds", type=int, help="轮询间隔秒数")
    parser.add_argument("--timeout-seconds", type=int, help="HTTP 超时秒数")
    parser.add_argument("--state-path", help="钉钉独立发送状态文件")
    parser.add_argument("--send-existing", action="store_true", help="首次运行时发送当前仍接近前高的信号")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_env_file(ROOT / ".env")
    args = build_parser().parse_args(argv)
    config = config_from_args(args)
    try:
        validate_config(config)
    except ValueError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    if args.test_message:
        try:
            send_dingtalk_payload(config, build_test_payload())
            print("钉钉测试消息发送成功。")
            return 0
        except Exception as exc:
            print(f"钉钉测试消息发送失败：{safe_error_text(exc, config)}", file=sys.stderr)
            return 1

    while True:
        try:
            result = run_cycle(config)
            print(
                f"[{datetime.now(CHINA_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}] "
                f"检查 {result['checked']} 个币种，发送 {result['sent']} 条，失败 {result['failed']} 条。"
            )
            for error in result.get("errors") or []:
                print(f"  {error}", file=sys.stderr)
            if args.once:
                return 0 if result.get("ok") else 1
        except KeyboardInterrupt:
            print("钉钉价格监控机器人已停止。")
            return 0
        except Exception as exc:
            print(f"监控轮询失败：{safe_error_text(exc, config)}", file=sys.stderr)
            if args.once:
                return 1
        time.sleep(config.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
