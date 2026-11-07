"""RootData 取数：项目热度榜 + 一级市场融资。

**关键发现（2026-11-07）**：``www.rootdata.com`` 挂在腾讯云 WAF 后面（直连只会拿到
滑块验证码页），但 ``cn.rootdata.com`` 没有那层壳 —— 服务端拿到的是完整的服务端渲染
HTML（项目页 615KB、融资页 604KB，表格 30 行）。所以整条链路不需要浏览器，只需要
一个**能出网的代理**：

- 直连（trust_env=False）→ 403；系统注册表代理（127.0.0.1:62577）→ ProxyError；
- 显式 Clash ``127.0.0.1:7890`` → 200 + 干净表格。

因此这里自己挑代理（``ROOTDATA_PROXY`` 可覆盖），不使用 request 的 trust_env。

两个数据集各落一份快照，榜单/板块只读快照，热路径不碰网络：
- ``.runtime-cache/rootdata_projects.json``    项目热度榜（按 RD 热度指数倒排）
- ``.runtime-cache/rootdata_fundraising.json`` 融资事件 + 已提醒过的 key
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import requests
from bs4 import BeautifulSoup

PERSIST_CACHE_DIR = Path(
    os.getenv("XINGYUN_RUNTIME_DIR") or Path(__file__).resolve().parent / ".runtime-cache"
)

PROJECTS_URL = "https://cn.rootdata.com/projects?evalSort=2"
FUNDRAISING_URL = "https://cn.rootdata.com/fundraising"
BASE_URL = "https://cn.rootdata.com"

PROJECTS_SNAPSHOT = PERSIST_CACHE_DIR / "rootdata_projects.json"
FUNDRAISING_SNAPSHOT = PERSIST_CACHE_DIR / "rootdata_fundraising.json"

PROJECTS_TTL_SECONDS = max(300, int(float(os.getenv("ROOTDATA_PROJECTS_TTL_SECONDS", "900") or 900)))
FUNDRAISING_TTL_SECONDS = max(300, int(float(os.getenv("ROOTDATA_FUNDRAISING_TTL_SECONDS", "900") or 900)))
#: 融资提醒保留多少条 key 作为「已经报过」的账本，够挡住重复弹窗又不至于无限长。
FUNDRAISING_SEEN_LIMIT = 3000
#: 被 WAF 拦住后冷却多久再试（默认 30 分钟）。封锁是按出口 IP 的，撞得越勤封得越久。
BLOCK_COOLDOWN_SECONDS = max(300, int(float(os.getenv("ROOTDATA_BLOCK_COOLDOWN_SECONDS", "1800") or 1800)))
#: 榜单卡片展示几条（用户 2026-11-07：只展示前 10 个）。
PROJECT_DISPLAY_LIMIT = max(1, int(float(os.getenv("ROOTDATA_PROJECT_DISPLAY_LIMIT", "10") or 10)))
#: 弹窗监控几条（同一句话：「但是弹窗监控监控20个，有新进的就做弹窗提示」）。
PROJECT_WATCH_LIMIT = max(
    PROJECT_DISPLAY_LIMIT, int(float(os.getenv("ROOTDATA_PROJECT_WATCH_LIMIT", "20") or 20))
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://cn.rootdata.com/",
}

#: 显式候选代理。注册表里那个（62577）实测是坏的，而 trust_env 会优先用它，
#: 所以这里自己排优先级；``ROOTDATA_PROXY`` 可强制指定。
_PROXY_CANDIDATES = tuple(
    item for item in (
        os.getenv("ROOTDATA_PROXY", "").strip(),
        "http://127.0.0.1:7890",
        "http://127.0.0.1:7897",
        "http://127.0.0.1:10809",
        "http://127.0.0.1:1080",
    ) if item
)

_PROXY_LOCK = threading.Lock()
_PROXY_STATE: dict[str, Any] = {"proxy": "", "checked_at": 0.0}

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
_FUNDRAISING_ALERT_CALLBACK: Callable[[dict[str, Any]], Any] | None = None
_PROJECT_ALERT_CALLBACK: Callable[[dict[str, Any]], Any] | None = None


# --------------------------------------------------------------------------- 网络

class RootdataBlocked(RuntimeError):
    """被腾讯云 WAF 的滑块验证码挡住（**按出口 IP 封，不是按代理端口**）。

    2026-11-07 实锤：请求密一点之后 `cn.rootdata.com` 全站（含 `/`、`/projects`、
    `api.rootdata.com`）统一返回 1447 字节的 `CaptchaScript` 页。这时候换代理、换 UA
    都没用（同一个出口 IP），继续撞只会把封禁续期——所以 `refresh_async` 认这个异常，
    直接进冷却，别每 10 分钟去敲一次门。
    """


def _proxies_for(proxy: str) -> dict[str, str]:
    return {"http": proxy, "https": proxy}


def _try_fetch(url: str, proxy: str) -> str:
    response = requests.get(
        url, headers=_HEADERS, proxies=_proxies_for(proxy), timeout=(6, 25)
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    text = response.text
    if "CaptchaScript" in text:
        raise RootdataBlocked("被腾讯云 WAF 拦住（滑块验证码）")
    if "<table" not in text:
        raise RuntimeError("页面结构变了（找不到表格）")
    return text


def _working_proxy() -> str:
    """挑一个真能出网的代理（缓存 5 分钟），全部失败才报错。"""
    with _PROXY_LOCK:
        cached = str(_PROXY_STATE.get("proxy") or "")
        if cached and time.time() - float(_PROXY_STATE.get("checked_at") or 0) < 300:
            return cached
    errors: list[str] = []
    blocked = 0
    for candidate in _PROXY_CANDIDATES:
        try:
            _try_fetch(PROJECTS_URL, candidate)
        except RootdataBlocked as exc:
            # 换代理救不了 IP 级封禁，别把剩下的候选挨个撞一遍。
            blocked += 1
            errors.append(f"{candidate}: {exc}")
            continue
        except Exception as exc:
            errors.append(f"{candidate}: {type(exc).__name__} {str(exc)[:60]}")
            continue
        with _PROXY_LOCK:
            _PROXY_STATE["proxy"] = candidate
            _PROXY_STATE["checked_at"] = time.time()
        return candidate
    with _PROXY_LOCK:
        _PROXY_STATE["proxy"] = ""
        _PROXY_STATE["checked_at"] = 0.0
    if blocked:
        raise RootdataBlocked("RootData 全站被 WAF 拦住：" + "；".join(errors))
    raise RuntimeError("RootData 没有可用代理：" + "；".join(errors))


def fetch_html(url: str) -> str:
    proxy = _working_proxy()
    try:
        return _try_fetch(url, proxy)
    except RootdataBlocked:
        # 封禁与代理无关，重试只会加深封禁。
        raise
    except Exception:
        # 缓存的代理可能刚失效：清掉重挑一次。
        with _PROXY_LOCK:
            _PROXY_STATE["proxy"] = ""
            _PROXY_STATE["checked_at"] = 0.0
        return _try_fetch(url, _working_proxy())


# --------------------------------------------------------------------------- 解析

def _text(value: Any, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _int(value: Any) -> int:
    match = re.search(r"-?[\d,]+", _text(value, 40))
    return int(match.group(0).replace(",", "")) if match else 0


def _date_ms(text: str) -> int:
    """``11-05`` → 毫秒时间戳。跨年时（页面只给月-日）往前推一年。"""
    match = re.fullmatch(r"(\d{1,2})-(\d{1,2})", _text(text, 10))
    if not match:
        return 0
    month, day = int(match.group(1)), int(match.group(2))
    now = datetime.now()
    year = now.year
    candidate = datetime(year, month, day)
    if candidate - now > timedelta(days=30):
        year -= 1
    return int(datetime(year, month, day, 12, 0).timestamp() * 1000)


_IMAGE_EXT = re.compile(r"\.(?:png|jpe?g|webp|svg|gif|avif)(?:[?#]|$)", re.I)


def _image_url(value: Any) -> str:
    """把一个可能是图片地址的字段洗成可直接放进 ``<img src>`` 的 URL，不像图片就返回空。"""
    url = str(value or "").strip().replace("\\/", "/")
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("http://", "https://", "data:image/")):
        return ""
    if not _IMAGE_EXT.search(url) and "/image" not in url.lower() and "/img" not in url.lower():
        return ""
    return url[:400]


def _cell_icon(cell) -> str:
    """项目单元格里若有真图片就直接用它。

    列表页目前是「无图 → 渲染 shadcn 的 avatar 兜底字」，所以这里通常返回空；
    留着是为了页面将来把 logo 服务端渲染出来时能自动吃到。
    """
    for img in cell.find_all("img"):
        for attr in ("src", "data-src", "data-original", "data-lazy-src"):
            url = _image_url(img.get(attr))
            if url:
                return url
        srcset = str(img.get("srcset") or img.get("data-srcset") or "")
        if srcset:
            first = srcset.split(",")[0].strip().split(" ")[0]
            url = _image_url(first)
            if url:
                return url
    return ""


#: 列表页是 Next.js/RSC 渲染的，整行数据会以 JSON 片段塞在 HTML 里（`self.__next_f`）。
#: 实测字段就是 **`logoImg`**（``https://public.rootdata.com/images/<hash>/<ts>.png``），
#: 而 ``name`` 是个多语言对象：``"name":{"tw_value":"Elysium","en_value":...}``。
#: 只认「图片字段 + 附近出现本项目 name」的配对，别把别人家的 logo 安上来。
_PAYLOAD_LOGO = re.compile(
    r'"(?:logoImg|logo|logoUrl|logo_url|logoImage|icon|iconUrl|icon_url)"\s*:\s*"([^"]{8,400})"',
    re.I,
)
_PAYLOAD_NAME = re.compile(
    r'"name"\s*:\s*(?:\{\s*"(?:tw|en|cn|ja|vn)_value"\s*:\s*)?"([^"]{1,80})"'
)
_PAYLOAD_ICON_WINDOW = 900


def icons_from_payload(html: str, names: list[str]) -> dict[str, str]:
    """从页面内嵌的 JSON 里按「离得最近的项目名」配 logo，返回 ``{name.casefold(): url}``。

    配不上的宁可不要：给某个项目安一个别人家的 logo 比没有 logo 更糟。
    """
    wanted = {_text(name, 80).casefold(): name for name in names if _text(name, 80)}
    if not wanted:
        return {}
    # RSC 把内联数据塞在 JS 字符串里，引号是 ``\"``；不先还原一遍正则一条也匹配不到。
    text = html.replace('\\"', '"').replace("\\/", "/")
    name_hits = list(_PAYLOAD_NAME.finditer(text))
    found: dict[str, str] = {}
    for logo in _PAYLOAD_LOGO.finditer(text):
        url = _image_url(logo.group(1))
        if not url:
            continue
        best_key = ""
        best_distance = _PAYLOAD_ICON_WINDOW + 1
        # 同一条记录里 name 排在 logoImg 之后（实测 Elysium 那条就是 logoImg → name）。
        # 先只在「后面」找，找到就不再回头看前面，避免把上一条记录的项目名配给这张图。
        for hit in name_hits:
            if hit.start() <= logo.start():
                continue
            key = hit.group(1).strip().casefold()
            if key not in wanted:
                continue
            distance = hit.start() - logo.start()
            if distance < best_distance:
                best_key, best_distance = key, distance
        if not best_key:
            for hit in name_hits:
                if hit.start() >= logo.start():
                    continue
                key = hit.group(1).strip().casefold()
                if key not in wanted:
                    continue
                distance = logo.start() - hit.start()
                if distance < best_distance:
                    best_key, best_distance = key, distance
        if best_key and best_key not in found:
            found[best_key] = url
    return found


def _project_link(cell) -> tuple[str, str, str, str]:
    """从项目单元格里取出（项目名、代码、详情链接、图标）。

    **坑（2026-11-07）**：单元格最左边那个圆形头像是 ``span.text-muted-foreground``，
    里面的文字是**项目名首字母**（Elysium→"E"、Lombard→"L"）。早期版本把它当 ticker，
    结果榜单第二行变成「E · 高性能 DeFi L2」、融资标题变成「Lombard 种子轮 $L」——
    凭空造了一个不存在的 $L。所以取代码时必须跳过 avatar 子树，宁可为空。
    """
    name = ""
    ticker = ""
    href = ""
    link = cell.select_one("a.animate-underline") or cell.select_one('a[href*="/projects/detail/"]')
    if link is not None:
        name = _text(link.get_text(" ", strip=True), 80)
        href = str(link.get("href") or "")
    if not name:
        name = _text(cell.get_text(" ", strip=True), 80)
    for node in cell.select("span.text-muted-foreground"):
        if node.find_parent(attrs={"data-slot": "avatar"}) is not None:
            continue
        candidate = _text(node.get_text(" ", strip=True), 40)
        # 单个字母 / 与项目名首字母相同 → 还是头像兜底字，不是代码。
        if len(candidate) < 2 or candidate.casefold() == name[:1].casefold():
            continue
        ticker = candidate
        break
    if href.startswith("/"):
        href = BASE_URL + href
    return name, ticker, href, _cell_icon(cell)


def parse_projects(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        return []
    rows: list[dict[str, Any]] = []
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all("td")
        if len(cells) < 6:
            continue
        name, ticker, href, icon = _project_link(cells[0])
        if not name:
            continue
        tags = [
            _text(node.get_text("", strip=True), 40)
            for node in cells[1].select("a")
        ]
        rows.append({
            "name": name,
            "symbol": ticker,
            "tags": [tag for tag in tags if tag],
            "intro": _text(cells[3].get_text(" ", strip=True), 160),
            "growth": _int(cells[4].get_text(" ", strip=True)),
            "popularity": _int(cells[5].get_text(" ", strip=True)),
            "icon": icon,
            "url": href,
        })
    # 单元格里没图（列表页现状）→ 去页面内嵌 JSON 里按项目名配一次 logo。
    if rows and not any(row["icon"] for row in rows):
        payload_icons = icons_from_payload(html, [row["name"] for row in rows])
        for row in rows:
            row["icon"] = payload_icons.get(_text(row["name"], 80).casefold(), "")
    return rows


def _clean_investor_name(text: str) -> str:
    """投资方名字要清两样东西：

    1. 头像字母会被判进文本（``"E Ethereal Ventures *"``）→ 去掉开头的单字母；
    2. 末尾的 ``*`` 是「领投」标记，留在卡片上只会像个错字 —— 去掉，领投信息由
       ``investorLead`` 单独带（真要用再渲染，不塞进名字里）。
    """
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"^[A-Za-z0-9]\s+", "", text).strip()
    text = re.sub(r"[\s*·]+$", "", text).strip()
    return text


def _investors(cell) -> list[str]:
    names: list[str] = []
    for link in cell.select('a[href*="/investors/detail/"]'):
        text = _clean_investor_name(link.get_text(" ", strip=True))
        if text:
            names.append(text)
    if not names:
        fallback = _clean_investor_name(_text(cell.get_text(" ", strip=True), 120))
        if fallback and fallback != "--":
            names.append(fallback)
    return names


def parse_fundraising(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        return []
    rows: list[dict[str, Any]] = []
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all("td")
        if len(cells) < 5:
            continue
        name, ticker, href, _icon = _project_link(cells[0])
        if not name:
            continue
        amount = _text(cells[2].get_text(" ", strip=True), 40)
        valuation = _text(cells[3].get_text(" ", strip=True), 40)
        date_ms = _date_ms(cells[4].get_text(" ", strip=True))
        source_link = cells[5].select_one("a[href]") if len(cells) > 5 else None
        investors = _investors(cells[6]) if len(cells) > 6 else []
        extra = 0
        if len(cells) > 6:
            match = re.search(r"\+\s*(\d+)", cells[6].get_text(" ", strip=True))
            extra = int(match.group(1)) if match else 0
        rows.append({
            "name": name,
            "symbol": ticker,
            "round": _text(cells[1].get_text(" ", strip=True), 40) or "融资",
            "amount": "" if amount == "--" else amount,
            "valuation": "" if valuation == "--" else valuation,
            "date": date_ms,
            "investors": investors,
            "investorExtra": extra,
            "url": href,
            "newsUrl": str((source_link.get("href") if source_link else "") or ""),
        })
    return rows


# --------------------------------------------------------------------------- 快照

def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    PERSIST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return payload


def _age(payload: dict[str, Any]) -> float:
    fetched_at = float(payload.get("fetchedAtMs") or 0) / 1000
    return float("inf") if not fetched_at else max(0.0, time.time() - fetched_at)


def read_snapshot() -> dict[str, Any]:
    return _read(PROJECTS_SNAPSHOT)


def snapshot_age_seconds(payload: dict[str, Any] | None = None) -> float:
    return _age(payload if payload is not None else read_snapshot())


def write_snapshot(
    projects: list[dict[str, Any]], *, watched: list[str] | None = None
) -> dict[str, Any]:
    """落项目快照。``watched`` = 本轮前 20 名的 key，落盘后就是下一轮的「基线名单」。

    判断「新进」必须拿两轮对比，所以 ``refresh`` 的顺序是「先读旧基线 → 算新进 →
    再写本轮基线」，绝不能先写后比。
    """
    payload: dict[str, Any] = {
        "fetchedAtMs": int(time.time() * 1000),
        "projects": projects,
    }
    if watched is not None:
        payload["watched"] = list(dict.fromkeys(str(key) for key in watched))[-PROJECT_WATCH_LIMIT:]
    return _write(PROJECTS_SNAPSHOT, payload)


def read_fundraising_snapshot() -> dict[str, Any]:
    return _read(FUNDRAISING_SNAPSHOT)


def fundraising_age_seconds(payload: dict[str, Any] | None = None) -> float:
    return _age(payload if payload is not None else read_fundraising_snapshot())


# --------------------------------------------------------------------------- 过滤 / 排行

def _tags_of(project: dict[str, Any]) -> list[str]:
    raw = project.get("tags")
    if isinstance(raw, str):
        raw = [part for part in re.split(r"[,/|]", raw)]
    tags: list[str] = []
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
    return any(tag.casefold() in ROOTDATA_EXCLUDED_TAGS for tag in _tags_of(project))


def ranked_projects(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 RD 热度指数倒排，滤掉老大哥，重编名次。"""
    kept = [row for row in projects if row and not rootdata_project_excluded(row)]
    kept.sort(key=lambda row: int(row.get("popularity") or 0), reverse=True)
    for index, row in enumerate(kept, start=1):
        row["rank"] = index
    return kept


def project_rows(limit: int = PROJECT_DISPLAY_LIMIT,
                 payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """把快照里的项目转成热门榜行（app.js::renderRow 认的那套字段）。

    卡片只展示**前 10 个**（用户 2026-11-07：「只展示前10个，但是弹窗监控监控20个」），
    监控范围是 ``PROJECT_WATCH_LIMIT``，见 ``project_watch_alerts``。
    """
    payload = payload if payload is not None else read_snapshot()
    projects = payload.get("projects") if isinstance(payload.get("projects"), list) else []
    rows = ranked_projects([dict(item) for item in projects if isinstance(item, dict)])[:limit]
    # 热度条按「榜上最高热度」归一。取前 20（监控范围）里的最大值当分母，只显示 10 行时
    # 分母仍是同一个数，切换展示条数不会让热度条整体变形。
    watch = ranked_projects([
        dict(item) for item in projects if isinstance(item, dict)
    ])[:PROJECT_WATCH_LIMIT]
    top = max((int(row.get("popularity") or 0) for row in watch), default=0) or 1
    out = []
    for row in rows:
        popularity = int(row.get("popularity") or 0)
        growth = int(row.get("growth") or 0)
        tags = _tags_of(row)
        intro = _text(row.get("intro"), 80)
        # 排版（2026-11-07 实测）：``symbol`` 渲染成主标题（项目名），``name`` 是副标题
        # （简介），``note`` 是最底下一行。三者不许复用同一段文字 —— 早期版本把简介
        # 同时塞进 name 和 note，卡片上同一句话出现两遍。
        icon = _text(row.get("icon"), 400)
        thumb = thumb_url(icon)
        out.append({
            "rank": row["rank"],
            "symbol": _text(row.get("name"), 60),
            "name": intro or _text(row.get("symbol"), 40),
            # 项目 logo：优先 128×128 缩略图（~4KB），CDN 不认后缀时前端退回原图。
            # 抓不到图就**明确告诉前端「没有图标」**，让它别再画两个首字母
            # （用户 2026-11-07：「不要这两只显示两个字母」）。
            "icon": thumb or icon,
            "iconCandidates": [icon] if thumb and icon else [],
            "iconFallback": "none" if not icon else "",
            "turnover": f"热度 {popularity}" if popularity else "",
            # RD 的「成长值」是百分比口径，用户要求带 %（2026-11-07）。
            "change": f"+{growth}%" if growth else "",
            "heat": round(popularity / top * 100) if popularity else 0,
            "tags": tags[:6],
            "note": " · ".join(tags[:4]) or intro,
            "url": _text(row.get("url"), 400),
        })
    return out


def fundraising_rows(limit: int = 18) -> list[dict[str, Any]]:
    """融资事件 → 「上新 IPO」板块的行（listing_section 那套字段）。"""
    payload = read_fundraising_snapshot()
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    rows = []
    for item in events[:limit]:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"), 60)
        ticker = _text(item.get("symbol"), 30)
        round_name = _text(item.get("round"), 30) or "融资"
        amount = _text(item.get("amount"), 30)
        valuation = _text(item.get("valuation"), 30)
        investors = [str(x) for x in (item.get("investors") or [])][:3]
        extra = int(item.get("investorExtra") or 0)
        if extra:
            investors.append(f"+{extra}")
        bits = []
        if amount:
            bits.append(f"金额 {amount}")
        if valuation:
            bits.append(f"估值 {valuation}")
        if investors:
            bits.append("投资方 " + " / ".join(investors))
        rows.append({
            "id": "rootdata-funding-" + re.sub(r"\W+", "-", _fundraising_key(item))[:80],
            "group": "ipo",
            # 专属页签用（listings.html 的 data-filter="rootdata"）。group 保持 ipo，
            # 这样「上新 IPO」页签里照样能看到它 —— 用户 2026-11-07 先要求「加进 IPO」，
            # 后要求「专门开一个板块」，两次都要满足。
            "rootdata": True,
            "source": "RootData",
            "sourceLabel": "RD",
            "title": f"{name} {round_name}" + (f" ${ticker}" if ticker else ""),
            "symbol": ticker or name,
            "status": round_name,
            "date": int(item.get("date") or 0),
            "metric": amount or valuation or "金额未披露",
            "note": " · ".join(bits),
            "tags": [tag for tag in (round_name, "一级融资", f"估值 {valuation}" if valuation else "") if tag],
            "url": _text(item.get("url"), 400) or FUNDRAISING_URL,
        })
    return rows


# --------------------------------------------------------------------------- 刷新

FUNDRAISING_LOCK = FUNDRAISING_SNAPSHOT.with_suffix(".lock")


def _acquire_fundraising_lock(ttl_seconds: float = 180.0) -> bool:
    """融资刷新跨进程互斥。

    ``/api/listing-events``（HTTP 进程）和 ``--worker-market``（市场 worker）都会
    触发刷新；两边各自算「新事件」再各自弹窗，同一笔融资会弹两次。用锁文件挡住
    第二次——拿不到就直接跳过这轮，让对方去抓、去弹、去记账本。
    崩溃残留的锁超过 ``ttl_seconds`` 自动接管。
    """
    PERSIST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(FUNDRAISING_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(handle)
        return True
    except FileExistsError:
        pass
    except OSError:
        return True  # 锁机制本身不可用（只读盘等）时宁可放行，别把刷新永久卡死
    try:
        if time.time() - FUNDRAISING_LOCK.stat().st_mtime < ttl_seconds:
            return False
    except OSError:
        return False
    try:
        FUNDRAISING_LOCK.unlink()
        handle = os.open(FUNDRAISING_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(handle)
        return True
    except OSError:
        return False


def _release_fundraising_lock() -> None:
    try:
        FUNDRAISING_LOCK.unlink()
    except OSError:
        pass


def detect_newcomers(
    ranked: list[dict[str, Any]], previous: dict[str, Any]
) -> tuple[list[dict[str, Any]], bool, list[str]]:
    """对比「上一轮基线」与本轮前 20，挑出新进的项目。

    返回 ``(新进项目, 是否首轮建基线, 本轮基线名单)``。首轮（或旧快照里没有 ``watched``
    字段）一律当「建基线」：不这么做的话第一次运行就会把 20 个项目全当新进弹一遍。
    """
    watched_now = [_project_key(row) for row in ranked[:PROJECT_WATCH_LIMIT]]
    baseline = [str(key) for key in ((previous or {}).get("watched") or [])]
    first_baseline = not previous or not baseline
    if first_baseline:
        return [], True, watched_now
    known = set(baseline)
    new_keys = {key for key in watched_now if key and key not in known}
    newcomers = [row for row in ranked[:PROJECT_WATCH_LIMIT] if _project_key(row) in new_keys]
    return newcomers, False, watched_now


def refresh(datasets: tuple[str, ...] = ("projects", "fundraising")) -> dict[str, Any]:
    """抓一次并落快照；监控范围内「新进」的项目、新的融资事件都发给弹窗回调。"""
    result: dict[str, Any] = {}
    if "projects" in datasets:
        projects = parse_projects(fetch_html(PROJECTS_URL))
        if not projects:
            raise RuntimeError("项目页解析不出任何行")
        ranked = ranked_projects(projects)
        previous = read_snapshot()
        newcomers, first_baseline, watched_now = detect_newcomers(ranked, previous)
        baseline = [str(key) for key in (previous.get("watched") or [])]
        # 没人听（手动 --pull / 未注册回调）时不要把新名单当基线写下去，否则这批
        # 「新进」就在账本里被静默吞掉 —— 与融资那边同一条纪律。
        delivered = first_baseline or _emit_project_alerts(newcomers)
        write_snapshot(ranked, watched=watched_now if delivered else baseline)
        result["projects"] = len(projects)
        result["ranked"] = len(ranked)
        result["newProjects"] = len(newcomers)
    if "fundraising" in datasets:
        if not _acquire_fundraising_lock():
            result["fundraising"] = "skipped: another process is refreshing"
            return result
        try:
            events = parse_fundraising(fetch_html(FUNDRAISING_URL))
            if not events:
                raise RuntimeError("融资页解析不出任何事件")
            previous = read_fundraising_snapshot()
            seen = [str(key) for key in (previous.get("seenKeys") or [])]
            seen_set = set(seen)
            first_run = not previous
            fresh = [
                {**event, "key": _fundraising_key(event)}
                for event in events
                if _fundraising_key(event) not in seen_set
            ]
            # 首跑只建账本（一次弹 30 条是灾难）；之后每轮把「新出现的」交给弹窗。
            # **没注册回调 = 没人听**（例如 tools/rootdata_login.py --pull 手动拉一次、
            # 或跑在没有 main() 的 worker 进程里）：这种情况下绝不能把这些 key 记成
            # 「已经报过」，否则这批融资事件会在账本里被静默吞掉，换到有回调的进程也
            # 再不会弹。宁可让它们留在未读集合里，等真正能弹的那一轮补上。
            delivered = first_run or _emit_fundraising_alerts(fresh)
            write_fundraising_snapshot(
                events, seen, skip_keys=[] if delivered else [event["key"] for event in fresh]
            )
            result["fundraising"] = len(events)
            result["new"] = 0 if first_run else len(fresh)
            result["delivered"] = len(fresh) if delivered else 0
        finally:
            _release_fundraising_lock()
    return result


_THUMB_SIZED = re.compile(r"\.(?:png|jpe?g|webp|gif)$", re.I)


def thumb_url(url: str) -> str:
    """把 ``public.rootdata.com`` 的原图换成 128×128 缩略图。

    实测（2026-11-07）：原图单张 ~235KB，加 ``_128x128.webp`` 后缀后只要 ~4KB（58 倍），
    一张卡 10 个 logo 就从 2.3MB 降到 40KB。CDN 认这个后缀（png/jpg 源都认）；
    不认的情况由前端 ``iconCandidates`` 退回原图，不会变成空白。
    """
    text = _text(url, 400)
    match = _THUMB_SIZED.search(text)
    if not match:
        return ""
    return text[: match.start()] + "_128x128.webp"


def _project_key(project: dict[str, Any]) -> str:
    """项目的「身份」= 项目名（详情链接带一次性 k= 参数，不能当键）。"""
    return _text(project.get("name"), 80).casefold()


def _emit_project_alerts(projects: list[dict[str, Any]]) -> bool:
    """把「新进监控前 20」的项目交给弹窗回调。返回 True = 确实有人收到。"""
    callback = _PROJECT_ALERT_CALLBACK
    if not callback:
        return False
    for project in projects:
        try:
            callback(project)
        except Exception:
            # 弹窗失败绝不能让刷新循环挂掉
            pass
    return True


def set_project_alert_callback(callback: Callable[[dict[str, Any]], Any] | None) -> None:
    global _PROJECT_ALERT_CALLBACK
    _PROJECT_ALERT_CALLBACK = callback


def _fundraising_key(event: dict[str, Any]) -> str:
    return "|".join((
        _text(event.get("name"), 60).casefold(),
        _text(event.get("round"), 30).casefold(),
        str(int(event.get("date") or 0)),
    ))


def fundraising_event_key(event: dict[str, Any]) -> str:
    """公开版去重键（弹窗那边要用它拼 key，别在 server.py 里另造一套）。"""
    return _fundraising_key(event if isinstance(event, dict) else {})


def write_fundraising_snapshot(
    events: list[dict[str, Any]], seen: list[str], *, skip_keys: list[str] | None = None
) -> dict[str, Any]:
    """落快照。``skip_keys`` 里的 key **不进已读账本**（留给下一轮补弹）。"""
    skipped = {str(key) for key in (skip_keys or [])}
    keys = list(dict.fromkeys([
        *seen,
        *(_fundraising_key(event) for event in events if _fundraising_key(event) not in skipped),
    ]))
    return _write(FUNDRAISING_SNAPSHOT, {
        "fetchedAtMs": int(time.time() * 1000),
        "events": events,
        "seenKeys": keys[-FUNDRAISING_SEEN_LIMIT:],
    })


def _emit_fundraising_alerts(events: list[dict[str, Any]]) -> bool:
    """把新事件交给弹窗回调。返回 True 表示「确实有人收到」（含空列表的平凡情况）。"""
    callback = _FUNDRAISING_ALERT_CALLBACK
    if not callback:
        return False
    for event in events:
        try:
            callback(event)
        except Exception:
            # 弹窗失败绝不能让刷新循环挂掉
            pass
    return True


def set_fundraising_alert_callback(callback: Callable[[dict[str, Any]], Any] | None) -> None:
    global _FUNDRAISING_ALERT_CALLBACK
    _FUNDRAISING_ALERT_CALLBACK = callback


def refresh_async(*, min_interval_seconds: float = 600.0,
                  datasets: tuple[str, ...] = ("projects", "fundraising")) -> bool:
    """后台拉一次快照。已有任务在跑、或刚失败过 → 直接返回 False（不排队）。"""
    with _REFRESH_LOCK:
        if _REFRESH_STATE["running"]:
            return False
        if time.time() < float(_REFRESH_STATE.get("locked_until") or 0):
            return False
        _REFRESH_STATE["running"] = True

    def worker() -> None:
        try:
            result = refresh(datasets)
            _REFRESH_STATE["message"] = "ok " + json.dumps(result, ensure_ascii=False)
            _REFRESH_STATE["locked_until"] = 0.0
        except RootdataBlocked as exc:
            # IP 级封禁：越撞封得越久，直接冷到 block 冷却结束（默认 30 分钟）。
            # 只对 WAF 这一种失败拉长冷却；代理挂了、页面改版仍按 min_interval 快速重试。
            _REFRESH_STATE["message"] = "blocked " + str(exc)[:180]
            _REFRESH_STATE["locked_until"] = time.time() + BLOCK_COOLDOWN_SECONDS
        except Exception as exc:
            _REFRESH_STATE["message"] = str(exc)[:200]
            _REFRESH_STATE["locked_until"] = time.time() + min_interval_seconds
        finally:
            _REFRESH_STATE["running"] = False

    threading.Thread(target=worker, daemon=True, name="rootdata-refresh").start()
    return True


def refresh_state() -> dict[str, Any]:
    return dict(_REFRESH_STATE)
