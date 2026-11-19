"""独立桥接服务（不想动你现有后端时用这个）。

    python server.py --port 8765

提供的路由（和嵌进主项目时完全一致）：
    GET /api/gigax/health
    GET /api/gigax/narrative?chain=bsc&address=0x...
    GET /api/gigax/refresh
    OPTIONS /api/gigax/*      （浏览器预检）

注意：**不要对 /api/gigax/* 启用 gzip**。油猴的 GM_xmlhttpRequest 不会自动解压，
返回压缩字节会让脚本 JSON.parse 失败，日志里表现为「桥接返回非 JSON」。
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import gigax  # noqa: E402  （同目录模块）

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "*",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "gigax-bridge/1.0"

    def log_message(self, fmt, *args):  # 安静点，只打业务日志
        pass

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        for k, v in CORS.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        if urlparse(self.path).path.startswith("/api/gigax/"):
            self.send_response(204)
            for k, v in CORS.items():
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_error(404)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not parsed.path.startswith("/api/gigax/"):
            self.send_error(404)
            return

        if parsed.path == "/api/gigax/health":
            self._send(gigax.health())
            return

        if parsed.path == "/api/gigax/refresh":
            ok, msg = gigax.refresh_token()
            self._send({"ok": ok, "msg": msg})
            return

        if parsed.path == "/api/gigax/narrative":
            address = (query.get("address") or [""])[0].strip()
            chain = (query.get("chain") or ["bsc"])[0].strip() or "bsc"
            if not address:
                self._send({"ok": False, "error": "缺少 address"}, status=400)
                return
            result = gigax.fetch_narrative(chain, address)
            print(f"[narrative] {chain}:{address} -> ok={result.get('ok')}", flush=True)
            self._send(result)
            return

        if parsed.path == "/api/gigax/narratives":
            # 批量：?items=sol:CA1,bsc:0x...&workers=8 —— 一屏 20 个币走一条请求
            raw = (query.get("items") or [""])[0]
            items: list[tuple[str, str]] = []
            for part in raw.split(","):
                part = part.strip()
                if not part or ":" not in part:
                    continue
                chain, _, addr = part.partition(":")
                if addr.strip():
                    items.append((chain.strip() or "bsc", addr.strip()))
            if not items:
                self._send({"ok": False, "error": "缺少 items"}, status=400)
                return
            if len(items) > 40:
                items = items[:40]
            try:
                workers = int((query.get("workers") or ["6"])[0])
            except ValueError:
                workers = 6
            result = gigax.fetch_narratives(items, max_workers=workers)
            print(f"[narratives] {len(items)} 个 -> ok={result.get('ok')} "
                  f"cached={result.get('cached')}", flush=True)
            self._send(result)
            return

        self._send({"ok": False, "error": "unknown gigax route"}, status=404)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-keepalive", action="store_true", help="不启动 30 分钟 token 保活")
    args = ap.parse_args()

    if not args.no_keepalive:
        gigax.start_keepalive()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    print(f"GIGA X 桥接已启动: http://{args.host}:{args.port}/api/gigax/health", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye", flush=True)
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
