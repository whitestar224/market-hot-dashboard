"""一次性探针：确认 BWE WebSocket 的可用线路与帧格式。"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import websocket

from newsflash_sources import PROXY_CANDIDATE_PORTS

URL = "wss://bwenews-api.bwe-ws.com/ws"
READ_SECONDS = 12.0


def candidate_proxies() -> list[tuple[str, str, int] | None]:
    routes: list[tuple[str, str, int] | None] = []
    env_proxy = ""
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        value = str(os.getenv(name) or "").strip()
        if value:
            env_proxy = value
            break
    if env_proxy:
        cleaned = env_proxy.replace("http://", "").replace("https://", "").replace("socks5://", "")
        host, _, port = cleaned.rpartition(":")
        routes.append(("env " + env_proxy, host.strip("/"), int(port or 0)))
    for port in PROXY_CANDIDATE_PORTS:
        routes.append((f"127.0.0.1:{port}", "127.0.0.1", port))
    routes.append(None)  # direct last
    return routes


def probe(label: str, host: str | None, port: int | None) -> bool:
    started = time.time()
    kwargs = {"timeout": 8, "enable_multithread": True}
    if host:
        kwargs.update(
            http_proxy_host=host, http_proxy_port=port, proxy_type="http",
            http_proxy_auth=None,
        )
    try:
        ws = websocket.create_connection(URL, **kwargs)
    except Exception as exc:
        print(f"  [x] {label}: {type(exc).__name__}: {exc}")
        return False
    print(f"  [v] {label}: connected in {time.time() - started:.2f}s")
    try:
        ws.settimeout(READ_SECONDS)
        ws.send("ping")
        frames = 0
        deadline = time.time() + READ_SECONDS
        while time.time() < deadline:
            try:
                frame = ws.recv()
            except Exception as exc:
                print(f"      recv stop: {type(exc).__name__}: {exc}")
                break
            if isinstance(frame, bytes):
                frame = frame.decode("utf-8", "replace")
            frames += 1
            print(f"      frame#{frames}: {frame[:300]}")
            if frames >= 6:
                break
        print(f"      total frames seen: {frames}")
    finally:
        try:
            ws.close()
        except Exception:
            pass
    return True


def main() -> int:
    for route in candidate_proxies():
        label, host, port = route if route else ("direct", None, None)
        if probe(label, host, port):
            return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
