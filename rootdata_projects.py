"""RootData 项目热门榜取数。

为什么不是普通 requests：``www.rootdata.com/projects`` 挂在腾讯云 WAF 后面，
服务器直连一律回 1463 字节的滑块验证码页（``curl_cffi`` 伪装 Chrome 指纹也一样，
``r.jina.ai`` 代抓同样被拦）。``api.rootdata.com/open/*`` 虽然能通，但只提供需要
apikey 的详情接口，没有 RD 排行榜。

所以这里走「真浏览器」：一个**独立配置目录**的 Chrome（``chrome-rootdata``）带
``--remote-debugging-port`` 常驻，页面由浏览器自己渲染，抓完写一份快照到
``.runtime-cache/rootdata_projects.json``；榜单读快照，热路径完全不碰网络和验证码。

首次（或验证码过期）需要人工拖一次滑块：
双击 ``刷新RootData登录态.cmd`` → 弹窗里拖一下 → 脚本自动抓完。之后验证码的
票据存在那个独立配置里，服务可以自己定时刷新，不用再管。
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PERSIST_CACHE_DIR = Path(
    os.getenv("XINGYUN_RUNTIME_DIR") or Path(__file__).resolve().parent / ".runtime-cache"
)

ROOTDATA_BOARD_URL = "https://www.rootdata.com/projects?evalSort=2"
SNAPSHOT_PATH = PERSIST_CACHE_DIR / "rootdata_projects.json"
BODIES_DIR = PERSIST_CACHE_DIR / "rootdata_bodies"
PROFILE_DIR = PERSIST_CACHE_DIR / "chrome-rootdata"
CDP_PORT = int(os.getenv("ROOTDATA_CDP_PORT", "9222"))
SNAPSHOT_TTL_SECONDS = max(300, int(float(os.getenv("ROOTDATA_SNAPSHOT_TTL_SECONDS", "900") or 900)))
PAGE_WAIT_SECONDS = max(30, int(float(os.getenv("ROOTDATA_PAGE_WAIT_SECONDS", "180") or 180)))

#: 「老大哥」名单：交易所 / 稳定币 / 公链基础设施本身不是「项目新叙事」，占着
#: RD 热度榜前排只会把真正在动的项目挤下去。CEX/CeFi 标签已经能滤掉一大票，
#: 这里再按名字兜一层，防止标签缺失时漏网。
ROOTDATA_MAJOR_EXCLUDES = {
    "binance", "bnb", "okx", "okb", "coinbase", "coinbase global", "kraken", "bybit",
    "bitget", "bgb", "gate.io", "gate", "gt", "kucoin", "kcs", "huobi", "htx", "ht",
    "mexc", "crypto.com", "cro", "bitfinex", "upbit", "bitstamp", "gemini",
    "tether", "usdt", "usdc", "dai", "fdusd", "tusd", "busd", "pyusd",
    "ethereum", "solana", "bitcoin", "wbtc", "steth", "weth",
}
ROOTDATA_EXCLUDED_TAGS = {"#cex", "#cefi", "cex", "cefi"}

_REFRESH_LOCK = threading.Lock()
_REFRESH_STATE: dict[str, Any] = {"running": False, "locked_until": 0.0, "message": ""}


# --------------------------------------------------------------------------- 快照

def read_snapshot() -> dict[str, Any]:
    try:
        payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def snapshot_age_seconds(payload: dict[str, Any] | None = None) -> float:
    data = payload if payload is not None else read_snapshot()
    fetched_at = float(data.get("fetchedAtMs") or 0) / 1000
    if not fetched_at:
        return float("inf")
    return max(0.0, time.time() - fetched_at)


def write_snapshot(projects: list[dict[str, Any]], *, source: str, note: str = "") -> dict[str, Any]:
    PERSIST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetchedAtMs": int(time.time() * 1000),
        "source": source,
        "note": note,
        "projects": projects,
    }
    tmp = SNAPSHOT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(SNAPSHOT_PATH)
    return payload


# --------------------------------------------------------------------------- 过滤 / 排行

def _text(value: Any, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _tags_of(project: dict[str, Any]) -> list[str]:
    raw = project.get("tags")
    if isinstance(raw, str):
        raw = [part for part in re.split(r"[,/|]", raw)]
    tags = []
    for item in raw or []:
        text = _text(item, 40)
        if text and text not in tags:
            tags.append(text)
    return tags


def rootdata_project_excluded(project: dict[str, Any]) -> bool:
    """CEX / CeFi / 稳定币 / 公链本身不进榜。"""
    name = _text(project.get("name"), 80).casefold()
    symbol = _text(project.get("symbol"), 40).casefold()
    if name in ROOTDATA_MAJOR_EXCLUDES or symbol in ROOTDATA_MAJOR_EXCLUDES:
        return True
    for tag in _tags_of(project):
        if tag.casefold() in ROOTDATA_EXCLUDED_TAGS:
            return True
    return False


def ranked_projects(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 RD 热度指数倒排，滤掉老大哥，重编名次。"""
    kept = [row for row in projects if row and not rootdata_project_excluded(row)]
    kept.sort(key=lambda row: float(row.get("popularity") or 0), reverse=True)
    for index, row in enumerate(kept, start=1):
        row["rank"] = index
    return kept


def project_rows(limit: int = 20, payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """把快照里的项目转成热门榜行（app.js::renderRow 认的那套字段）。"""
    payload = payload if payload is not None else read_snapshot()
    projects = payload.get("projects") if isinstance(payload.get("projects"), list) else []
    rows = ranked_projects([dict(item) for item in projects if isinstance(item, dict)])[:limit]
    top = max((float(row.get("popularity") or 0) for row in rows), default=0) or 1
    out = []
    for row in rows:
        popularity = float(row.get("popularity") or 0)
        growth = row.get("growth")
        tags = _tags_of(row)
        chain = _text(row.get("ecosystem"), 40)
        note_bits = [bit for bit in (chain, _text(row.get("intro"), 90)) if bit]
        out.append({
            "rank": row["rank"],
            "symbol": _text(row.get("name"), 60) or _text(row.get("symbol"), 40),
            "name": " · ".join(bit for bit in (_text(row.get("symbol"), 40), chain) if bit),
            "icon": _text(row.get("icon"), 400),
            "icons": [url for url in [_text(row.get("icon"), 400)] if url],
            "turnover": f"热度 {int(popularity)}" if popularity else "",
            "change": f"+{int(growth)}" if isinstance(growth, (int, float)) and growth else "",
            "heat": round(popularity / top * 100) if popularity else 0,
            "tags": tags[:6],
            "note": " · ".join(note_bits) or " · ".join(tags[:3]),
            "url": _text(row.get("url"), 400),
        })
    return out


# --------------------------------------------------------------------------- CDP

def _loopback_open(port: int) -> bool:
    sock = socket.socket()
    sock.settimeout(1)
    try:
        sock.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        sock.close()


def _cdp_http(path: str, method: str = "GET") -> Any:
    # loopback 必须绕开环境代理（http_proxy 会把 127.0.0.1:9222 也代理走 → 502）
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(f"http://127.0.0.1:{CDP_PORT}{path}", method=method)
    with opener.open(request, timeout=20) as response:
        body = response.read().decode("utf-8", "replace")
    try:
        return json.loads(body)
    except Exception:
        return {}


def chrome_executable() -> str:
    for candidate in (
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ):
        if candidate and os.path.isfile(candidate):
            return candidate
    return ""


def ensure_browser(*, headless: bool = False, timeout: float = 25.0) -> bool:
    """确保带调试口的 Chrome 在跑。独立配置目录 → 不碰用户自己的 Chrome。"""
    if _loopback_open(CDP_PORT):
        return True
    chrome = chrome_executable()
    if not chrome:
        raise RuntimeError("找不到 chrome.exe")
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    args = [
        chrome,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1440,1000",
    ]
    if headless:
        args.append("--headless=new")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(args, close_fds=True, creationflags=flags,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _loopback_open(CDP_PORT):
            return True
        time.sleep(0.5)
    return False


class _Tab:
    def __init__(self, url: str = "about:blank") -> None:
        from websocket import create_connection

        target = _cdp_http(f"/json/new?{urllib.parse.quote(url, safe='')}", "PUT")
        if not target.get("webSocketDebuggerUrl"):
            raise RuntimeError("CDP 没能新建标签页")
        self.target_id = target.get("id")
        # 同上：loopback 的 ws 也不能被环境代理接管。
        self.ws = create_connection(
            target["webSocketDebuggerUrl"], timeout=30,
            http_no_proxy=["127.0.0.1", "localhost"],
        )
        self.seq = 0
        self.network: list[dict[str, Any]] = []
        self._bodies: dict[str, dict[str, Any]] = {}

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.seq += 1
        self.ws.send(json.dumps({"id": self.seq, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") == self.seq:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result") or {}
            self._harvest(message)

    def _harvest(self, message: dict[str, Any]) -> None:
        if message.get("method") != "Network.responseReceived":
            return
        params = message.get("params") or {}
        response = params.get("response") or {}
        url = str(response.get("url") or "")
        mime = str(response.get("mimeType") or "")
        if "json" not in mime or not url.startswith("http"):
            return
        request_id = params.get("requestId")
        if not request_id:
            return
        self.network.append({"url": url, "requestId": request_id, "mime": mime})

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        self.ws.settimeout(0.5)
        while time.time() < end:
            try:
                self._harvest(json.loads(self.ws.recv()))
            except Exception:
                pass
        self.ws.settimeout(30)

    def body(self, request_id: str) -> Any:
        if request_id in self._bodies:
            return self._bodies[request_id]
        try:
            result = self.send("Network.getResponseBody", {"requestId": request_id})
        except Exception:
            return None
        text = result.get("body") or ""
        if result.get("base64Encoded"):
            import base64

            text = base64.b64decode(text).decode("utf-8", "replace")
        try:
            payload = json.loads(text)
        except Exception:
            payload = None
        self._bodies[request_id] = payload
        return payload

    def eval(self, expression: str) -> Any:
        result = self.send("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        return (result.get("result") or {}).get("value")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass
        try:
            _cdp_http(f"/json/close/{self.target_id}")
        except Exception:
            pass


_PAGE_STATE_JS = """(() => {
  const text = document.body ? document.body.innerText : "";
  return {
    title: document.title || "",
    url: location.href,
    captcha: text.indexOf("Captcha") >= 0 || !!document.querySelector("#CaptchaScript"),
    rows: document.querySelectorAll("table tbody tr").length,
    links: document.querySelectorAll('a[href*="/projects/"]').length,
    textLen: text.length
  };
})()"""

_EXTRACT_JS = """(() => {
  const out = [];
  const rows = document.querySelectorAll("table tbody tr");
  rows.forEach((tr) => {
    const cells = Array.from(tr.querySelectorAll("td")).map((td) => td.innerText.trim());
    const link = tr.querySelector('a[href*="/projects/"]');
    const img = tr.querySelector("img");
    out.push({
      cells: cells,
      href: link ? link.href : "",
      icon: img ? (img.src || img.getAttribute("data-src") || "") : ""
    });
  });
  return {rows: out, count: rows.length};
})()"""


def _project_from_cells(cells: list[str], href: str = "", icon: str = "") -> dict[str, Any] | None:
    """把一行表格单元格折成项目字典（列序见 RootData 项目列表）。"""
    values = [cell for cell in (cells or []) if cell]
    if len(values) < 2:
        return None
    name = values[0]
    symbol = ""
    # 「Pearl PRL」「DAPPOS DOS」这种：名字后面挂着代码
    match = re.match(r"^(?P<name>.+?)\s+(?P<symbol>[A-Z0-9]{2,12})$", name)
    if match:
        name, symbol = match.group("name").strip(), match.group("symbol")
    tags = [tag for tag in re.split(r"\s*#", " ".join(values[1:3])) if tag][:8]
    numbers = [int(cell.replace(",", "")) for cell in values if re.fullmatch(r"[\d,]{1,9}", cell.strip())]
    return {
        "name": name.strip(),
        "symbol": symbol,
        "tags": ["#" + tag.lstrip("#").strip() for tag in tags if tag.strip()],
        "ecosystem": "",
        "intro": values[3] if len(values) > 3 and not values[3].isdigit() else "",
        "growth": numbers[-2] if len(numbers) >= 2 else None,
        "popularity": numbers[-1] if numbers else 0,
        "icon": icon,
        "url": href,
    }


def _walk_json(node: Any, found: list[dict[str, Any]], depth: int = 0) -> None:
    """在任意 JSON 里找出「看起来像项目列表」的那些 dict。"""
    if depth > 6:
        return
    if isinstance(node, list):
        for item in node[:200]:
            _walk_json(item, found, depth + 1)
        return
    if not isinstance(node, dict):
        return
    keys = {str(key).casefold(): key for key in node.keys()}
    name_key = next((keys[k] for k in keys if k in {
        "name", "project_name", "projectname", "title", "project", "token_name"}), None)
    if name_key and isinstance(node.get(name_key), str):
        lower = {k: v for k, v in node.items() if isinstance(k, str)}
        def pick(*names: str) -> Any:
            for name in names:
                for key, value in lower.items():
                    if key.casefold() == name and value not in (None, "", []):
                        return value
            return None

        popularity = pick("eval", "eval_score", "evalscore", "popularity", "heat", "hot",
                          "rd_index", "rdindex", "score", "rank_score")
        growth = pick("eval_growth", "evalgrowth", "growth", "growth_index", "increase")
        if popularity is None and growth is None:
            for item in node.values():
                _walk_json(item, found, depth + 1)
            return
        tags = pick("tags", "tag", "labels", "sectors")
        tag_names = []
        if isinstance(tags, list):
            for tag in tags[:8]:
                if isinstance(tag, dict):
                    tag_names.append(str(tag.get("name") or tag.get("tag") or "").strip())
                else:
                    tag_names.append(str(tag).strip())
        ecosystem = pick("ecosystem", "ecosystem_name", "chain", "chain_name", "networks")
        if isinstance(ecosystem, list):
            ecosystem = ", ".join(str(x.get("name") if isinstance(x, dict) else x) for x in ecosystem[:3])
        found.append({
            "name": _text(node.get(name_key), 80),
            "symbol": _text(pick("symbol", "token_symbol", "ticker", "coin_symbol"), 40),
            "tags": [tag for tag in (_text(t, 40) for t in tag_names) if tag],
            "ecosystem": _text(ecosystem, 60),
            "intro": _text(pick("intro", "introduction", "description", "desc", "brief"), 160),
            "growth": _number(growth),
            "popularity": _number(popularity),
            "icon": _text(pick("logo", "icon", "logo_url", "image"), 400),
            "url": _text(pick("url", "link", "website"), 400),
        })
        return
    for value in node.values():
        if isinstance(value, (list, dict)):
            _walk_json(value, found, depth + 1)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _text(value, 40).replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def pull_from_browser(*, headless: bool = False, dump_bodies: bool = True,
                      wait_seconds: int | None = None) -> dict[str, Any]:
    """打开 RootData 项目页，抓一次榜单，返回 ``{"projects": [...], "source": "..."}``。

    验证码没过去时抛 ``RuntimeError``，并保留窗口给人工拖滑块。
    """
    if not ensure_browser(headless=headless):
        raise RuntimeError("Chrome 调试口没起来（9222）")
    tab = _Tab("about:blank")
    try:
        tab.send("Network.enable")
        tab.send("Page.enable")
        tab.send("Page.navigate", {"url": ROOTDATA_BOARD_URL})
        wait = wait_seconds or PAGE_WAIT_SECONDS
        deadline = time.time() + wait
        state: dict[str, Any] = {}
        while time.time() < deadline:
            tab.pump(3)
            state = tab.eval(_PAGE_STATE_JS) or {}
            if state.get("captcha"):
                if headless:
                    raise RuntimeError("RootData 弹了滑块验证码，需要人工过：双击「刷新RootData登录态.cmd」")
                continue
            if (state.get("rows") or 0) >= 3:
                break
        if state.get("captcha") and not (state.get("rows") or 0):
            raise RuntimeError("等待验证码超时，请在 rootdata 窗口里拖一下滑块后重跑")

        extracted = tab.eval(_EXTRACT_JS) or {}
        dom_rows = [
            _project_from_cells(item.get("cells") or [], item.get("href") or "", item.get("icon") or "")
            for item in extracted.get("rows") or []
        ]
        projects = [row for row in dom_rows if row]
        source = "dom"

        # DOM 结构变了、或者表格里根本没有分数列 → 回头从页面自己的 JSON 接口里捞。
        if len(projects) < 3 or not any(row.get("popularity") for row in projects):
            found: list[dict[str, Any]] = []
            if dump_bodies:
                BODIES_DIR.mkdir(parents=True, exist_ok=True)
            for entry in tab.network:
                payload = tab.body(entry["requestId"])
                if payload is None:
                    continue
                if dump_bodies:
                    safe = re.sub(r"[^A-Za-z0-9]+", "_", entry["url"])[:80]
                    try:
                        (BODIES_DIR / f"{safe}.json").write_text(
                            json.dumps(payload, ensure_ascii=False)[:400000], encoding="utf-8")
                    except Exception:
                        pass
                _walk_json(payload, found)
            if len(found) > len(projects):
                projects, source = found, "ajax"
        if len(projects) < 3:
            raise RuntimeError(
                "页面解析不出项目（dom="
                f"{len(projects)}，textLen={state.get('textLen')}，network={len(tab.network)}）"
            )
        return {"projects": projects, "source": source, "pageState": state,
                "network": [entry["url"] for entry in tab.network][:40]}
    finally:
        tab.close()


# --------------------------------------------------------------------------- 后台刷新

def refresh_async(*, min_interval_seconds: float = 600.0, headless: bool = True) -> bool:
    """后台拉一次快照。已有任务在跑、或刚失败过 → 直接返回 False（不排队）。"""
    with _REFRESH_LOCK:
        if _REFRESH_STATE["running"]:
            return False
        if time.time() < float(_REFRESH_STATE.get("locked_until") or 0):
            return False
        _REFRESH_STATE["running"] = True

    def worker() -> None:
        try:
            result = pull_from_browser(headless=headless)
            projects = ranked_projects([dict(row) for row in result["projects"]])
            write_snapshot(projects, source=str(result.get("source") or "dom"),
                           note=str(result.get("pageState") or ""))
            _REFRESH_STATE["message"] = f"ok {len(projects)}"
            _REFRESH_STATE["locked_until"] = 0.0
        except Exception as exc:
            _REFRESH_STATE["message"] = str(exc)[:200]
            _REFRESH_STATE["locked_until"] = time.time() + min_interval_seconds
        finally:
            _REFRESH_STATE["running"] = False

    threading.Thread(target=worker, daemon=True, name="rootdata-refresh").start()
    return True


def refresh_state() -> dict[str, Any]:
    return dict(_REFRESH_STATE)
