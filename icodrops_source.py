"""ICODrops 空投榜数据源（热门榜「空投榜」的唯一取数口）。

背景（2026-11-09）：用户要把 CryptoRank 的 Drophunting 搬进热门榜做成「空投榜」。
实测结论 —— CryptoRank 那条路走不通：
- ``/v3/drophunting/list`` 等 5 个端点用免费 Sandbox key 一律
  ``403 ENDPOINT_NOT_AVAILABLE``（文档写明 ``Available from: Advanced``，$149/月）；
- 网页端被 Cloudflare 拦（curl/curl_cffi 各种指纹全 403）；
- 免费替代源项目级重叠度极低（CryptoRank 前 20 ∩ airdrops.io = 1，∩ DropsTab = 1）。

选定 ``icodrops.com``：完全免费、SSR 直出、无 Cloudflare 挑战，
``curl_cffi`` + ``impersonate="chrome110"`` 稳定 200。
``/category/active-ico/``（即页面上的 ACTIVE 标签，截图「ACTIVE 46」）一次给出
全部活动，每行含 ``项目名 / 符号 / 活动类型 / 融资额 / 估值 / 时间 / 详情链接``。

归类口径（``kind``）：
- ``airdrop``：Airdrop / Genesis Airdrop / Possible Retrodrop / Incentivized Activities /
  Early Incentives / Testnet / Point System Activity / Points Farming。
  激励活动、测试网、积分系统本质都是「潜在空投」，与 CryptoRank 的 ``POTENTIAL`` 同义。
- ``sale``：IEO / IDO / IKO / ICM / Public Sale / Presale / Pre-Sale 等代币销售。
- ``unknown``：识别不出的活动类型。

只做「抓一页 → 解析 → 归类 → 结构化返回」这一件事。抓取失败绝不伪装成空榜：
先退落盘缓存并标 ``stale``，连缓存都没有才返回带 ``error`` 的空态。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

PERSIST_CACHE_DIR = Path(
    os.getenv("XINGYUN_RUNTIME_DIR") or Path(__file__).resolve().parent / ".runtime-cache"
)

#: ACTIVE 分类页 —— 对应页面上的「ACTIVE」标签（当前 46 条）。
ICODROPS_ACTIVE_URL = "https://icodrops.com/category/active-ico/"
ICODROPS_SNAPSHOT = PERSIST_CACHE_DIR / "icodrops_active_airdrops.json"
#: 活动列表更新很慢（一天几次），缓存 30 分钟足够，别每次刷新都打上游。
ICODROPS_TTL_SECONDS = max(300, int(float(os.getenv("ICODROPS_TTL_SECONDS", "1800") or 1800)))
#: 上游单页条目上限。46 条是当前 ACTIVE 全量，留点余量防止上游扩量时截断。
ICODROPS_MAX_ITEMS = max(20, int(float(os.getenv("ICODROPS_MAX_ITEMS", "120") or 120)))

# ---- 详情页融资补全 -------------------------------------------------------
#: ACTIVE 列表页有 20/46 行「融资额」是空（上游本身就没收录，不是解析漏抓）。
#: 实测这批行的**详情页**还能再补到一部分「估值」（7/20），所以做一层后台补全：
#: 列表接口照旧秒回，补全在独立线程里串行跑、结果落盘，下次请求自动带上。
#: 融资额（Total Raised）在详情页同样是「—」，补不出来 —— 不许拿估值冒充融资额。
ICODROPS_FUNDING_CACHE = PERSIST_CACHE_DIR / "icodrops_funding_detail.json"
#: 补全结果的保鲜期（7 天）。这类历史融资数据基本不变，没必要反复抓。
ICODROPS_FUNDING_TTL_SECONDS = max(3600, int(float(os.getenv("ICODROPS_FUNDING_TTL_SECONDS", "604800") or 604800)))
#: 抓失败的项目多久后重试（6 小时），别把「这次失败」当成「永远没有」。
ICODROPS_FUNDING_RETRY_SECONDS = max(600, int(float(os.getenv("ICODROPS_FUNDING_RETRY_SECONDS", "21600") or 21600)))
#: 单轮补全最多抓几条 / 每条之间隔多久（秒）。串行 + 限速，别把上游打急。
ICODROPS_FUNDING_BATCH = max(1, int(float(os.getenv("ICODROPS_FUNDING_BATCH", "24") or 24)))
ICODROPS_FUNDING_INTERVAL = max(0.0, float(os.getenv("ICODROPS_FUNDING_INTERVAL", "0.8") or 0.8))

_proxy = os.getenv("ICODROPS_PROXY", "http://127.0.0.1:7890").strip() or None

#: 活动类型里出现这些词 → 归为「空投/潜在空投」。
_AIRDROP_HINTS = (
    "airdrop",
    "retrodrop",
    "incentiv",      # Incentivized Activities / Early Incentives
    "testnet",
    "point",         # Point System Activity / Points Farming
)
#: 出现这些词 → 归为「代币销售」（与空投互斥，先判 airdrop 再判这里）。
_SALE_HINTS = (
    "ieo",
    "ido",
    "iko",
    "icm",
    "sale",
    "presale",
    "pre-sale",
    "private round",
    "strategic round",
    "token launch",
    "launchpad",
)

_INVESTOR_NOISE = re.compile(r"&(?:amp;)?nbsp;?", re.I)


def _clean(text: Any, limit: int = 160) -> str:
    """去掉标签残留 / ``&nbsp`` 噪声 / 多余空白，并截断。"""
    if text is None:
        return ""
    raw = str(text)
    raw = _INVESTOR_NOISE.sub(" ", raw)
    raw = raw.replace("\xa0", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    # icodrops 用「—」（em dash）表示「无数据」，统一成空串方便前端判空。
    if raw in {"—", "-", "–", "N/A", "n/a", "--"}:
        return ""
    return raw[:limit]


def classify_kind(round_text: str) -> str:
    """把活动类型归类为 ``airdrop`` / ``sale`` / ``unknown``。"""
    low = (round_text or "").lower()
    if not low:
        return "unknown"
    for hint in _AIRDROP_HINTS:
        if hint in low:
            return "airdrop"
    for hint in _SALE_HINTS:
        if hint in low:
            return "sale"
    return "unknown"


def _absolute_url(url: str) -> str:
    """补全相对/协议相对 URL，让前端能直接当 <img src>。"""
    text = str(url or "").strip()
    if text.startswith("//"):
        return "https:" + text
    if text.startswith("/"):
        return "https://icodrops.com" + text
    return text


def _icon_url(row: Any) -> str:
    """取项目 logo。

    上游是懒加载：真实地址在 ``data-src``，``src`` 往往是 ``data:`` 占位符，
    所以先读 ``data-src``、跳过 ``data:``，最后才退到 ``data-srcset`` 的第一项。
    """
    img = row.select_one("img.Cll-Avt__img") or row.select_one("img.Avt")
    if not img:
        return ""
    for attr in ("data-src", "src"):
        raw = str(img.get(attr) or "").strip()
        if raw and not raw.startswith("data:"):
            return _absolute_url(raw)
    srcset = str(img.get("data-srcset") or "").strip()
    if srcset:
        first = srcset.split(",")[0].strip().split(" ")[0]
        if first and not first.startswith("data:"):
            return _absolute_url(first)
    return ""


def parse_active_html(html: str) -> list[dict[str, Any]]:
    """解析 ACTIVE 分类页的表格行，返回结构化活动列表。

    行结构（上游 class 名，2026-11 实测）：``li.Tbl-Row`` 内
    ``.Cll-Project__name`` / ``.Cll-Project__ticker`` /
    ``.Tbl-Row__item--round|--raised|--pre-valuation|--date`` 里的 ``.Cll-Value``。

    注意必须用 BeautifulSoup 而不是正则切 ``<li>...</li>``：投资人 logo 本身是
    嵌套 ``<li>``，正则会把行切断，导致部分行的活动类型丢成空串。
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []

    def _text(node: Any, limit: int = 160) -> str:
        if not node:
            return ""
        return _clean(node.get_text(" ", strip=True), limit)

    for row in soup.select("li.Tbl-Row"):
        name = _text(row.select_one(".Cll-Project__name"))
        if not name:
            continue
        round_text = _text(row.select_one(".Tbl-Row__item--round .Cll-Value"))
        link = row.select_one(".Cll-Project__link")
        href = (link.get("href") if link else "") or ""
        if href.startswith("/"):
            href = "https://icodrops.com" + href
        items.append({
            "name": name,
            "ticker": _text(row.select_one(".Cll-Project__ticker")).upper(),
            "round": round_text,
            "kind": classify_kind(round_text),
            "raised": _text(row.select_one(".Tbl-Row__item--raised .Cll-Value")),
            "preval": _text(row.select_one(".Tbl-Row__item--pre-valuation .Cll-Value")),
            "date": _text(row.select_one(".Tbl-Row__item--date .Cll-Value"), 40),
            "icon": _icon_url(row),
            "url": href,
        })
        if len(items) >= ICODROPS_MAX_ITEMS:
            break
    return items


def _fetch_html(url: str = ICODROPS_ACTIVE_URL) -> str:
    try:
        from curl_cffi import requests as curl_requests
    except Exception as exc:  # 缺 curl_cffi 只影响空投榜，别带崩整页
        raise RuntimeError("缺少 curl_cffi，无法抓取 ICODrops 数据") from exc
    kwargs: dict[str, Any] = {"impersonate": "chrome110", "timeout": 20.0}
    if _proxy:
        kwargs["proxies"] = {"http": _proxy, "https": _proxy}
    else:
        kwargs["proxies"] = {"http": "", "https": ""}
    response = curl_requests.get(url, **kwargs)
    if int(response.status_code) != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    if "Just a moment" in response.text:
        raise RuntimeError("Cloudflare 挑战页")
    return response.text


def _payload_from_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    airdrop_count = sum(1 for item in items if item.get("kind") == "airdrop")
    sale_count = sum(1 for item in items if item.get("kind") == "sale")
    return {
        "ok": True,
        "total": len(items),
        "airdropCount": airdrop_count,
        "saleCount": sale_count,
        # 有其他活动的（识别不出的类型）由前端按 total - airdrop - sale 兜底显示。
        "otherCount": len(items) - airdrop_count - sale_count,
        "updatedAt": int(time.time() * 1000),
        "source": "icodrops.com/category/active-ico",
        "items": items,
    }


# ------------------------------------------------------------------ 详情页补全
#: 补全线程状态（同一时刻只允许一个，避免每次轮询都起新线程打上游）。
_ENRICH_LOCK = threading.Lock()
_ENRICH_STATE: dict[str, Any] = {"running": False, "lastRunAt": 0, "lastFilled": 0, "lastError": ""}

#: 详情页两个「价格块」的标题 —— 只有这两个字段值得取，其余不认识的一律丢。
_DETAIL_BLOCK_TITLES = {
    "total raised": "raised",
    "pre-valuation": "preval",
    "pre valuation": "preval",
}


def _slug_from_url(url: str) -> str:
    """``https://icodrops.com/zns-connect/`` → ``zns-connect``。"""
    path = str(url or "").split("?")[0].split("#")[0].rstrip("/")
    return path.rsplit("/", 1)[-1].strip().lower()


def parse_detail_funding(html: str) -> dict[str, str]:
    """从项目详情页抽 ``Total Raised`` / ``Pre-Valuation``。

    真实结构（2026-11 实测）是 **一个** ``section.Overview-Section-Price-Block``
    里面装多个 ``div.Overview-Section-Price-Block__box``，每个 box 一组
    ``__title`` + ``__value``::

        Total Raised  —      In 3 rounds
        Pre-Valuation $16 M  Calculated

    所以必须按 **box** 遍历；按 section 遍历再 ``select_one`` 只会拿到第一个
    （凑巧是「—」），把后面的估值全丢掉 —— 这是踩过的坑。

    ``__value`` 里是 ``<span class="Empty-Data">—</span>`` 表示上游没有这条数据，
    必须当空处理，**绝不能把「—」当成 0，更不能拿估值冒充融资额**。
    """
    if not html:
        return {}
    soup = BeautifulSoup(html, "lxml")
    boxes = soup.select(".Overview-Section-Price-Block__box")
    if not boxes:
        # 上游若把 box 层去掉，退回 section 直接当容器（同样按标题配对）。
        boxes = soup.select(".Overview-Section-Price-Block")
    result: dict[str, str] = {}
    for block in boxes:
        title_node = block.select_one(".Overview-Section-Price-Block__title")
        value_node = block.select_one(".Overview-Section-Price-Block__value")
        if not title_node or not value_node:
            continue
        key = _DETAIL_BLOCK_TITLES.get(_clean(title_node.get_text(" ", strip=True), 40).lower())
        if not key:
            continue
        if value_node.select_one(".Empty-Data"):
            continue
        value = _clean(value_node.get_text(" ", strip=True), 40)
        if value:
            result[key] = value
    return result


def _read_funding_cache() -> dict[str, Any]:
    try:
        payload = json.loads(ICODROPS_FUNDING_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    entries = payload.get("entries")
    return entries if isinstance(entries, dict) else {}


def _write_funding_cache(entries: dict[str, Any]) -> None:
    try:
        ICODROPS_FUNDING_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = ICODROPS_FUNDING_CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(ICODROPS_FUNDING_CACHE)
    except OSError:
        pass


def _funding_entry_fresh(entry: dict[str, Any], now: float) -> bool:
    """成功结果按 7 天保鲜，失败结果按 6 小时重试。"""
    try:
        stamp = float(entry.get("fetchedAt") or 0) / 1000
    except (TypeError, ValueError):
        return False
    ttl = ICODROPS_FUNDING_RETRY_SECONDS if entry.get("error") else ICODROPS_FUNDING_TTL_SECONDS
    return bool(stamp) and now - stamp < ttl


def apply_detail_funding(items: list[dict[str, Any]]) -> int:
    """把补全缓存合并进活动列表，只补空字段，返回本次补到的行数。

    纯本地读文件 + 内存合并，几十条量级，随便调，不会产生网络请求。
    """
    entries = _read_funding_cache()
    if not entries:
        return 0
    filled = 0
    now = time.time()
    for item in items:
        if not isinstance(item, dict):
            continue
        entry = entries.get(_slug_from_url(item.get("url") or ""))
        if not isinstance(entry, dict) or entry.get("error"):
            continue
        if not _funding_entry_fresh(entry, now):
            continue
        changed = False
        for key in ("raised", "preval"):
            value = _clean(entry.get(key), 40)
            if value and not item.get(key):
                item[key] = value
                changed = True
        if changed:
            # 前端据此显示「数据来自详情页」，别让人以为列表页本来就有。
            item["fundingSource"] = "detail"
            filled += 1
    return filled


def _pending_detail_urls(items: list[dict[str, Any]], limit: int) -> list[str]:
    entries = _read_funding_cache()
    now = time.time()
    urls: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        slug = _slug_from_url(url)
        if not url.startswith("https://icodrops.com/") or not slug:
            continue
        if item.get("raised") and item.get("preval"):
            continue  # 列表页已经给全了，不用再跑一趟
        entry = entries.get(slug)
        if isinstance(entry, dict) and _funding_entry_fresh(entry, now):
            continue
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def _enrich_worker(urls: list[str]) -> None:
    entries = _read_funding_cache()
    filled = 0
    last_error = ""
    try:
        for index, url in enumerate(urls):
            slug = _slug_from_url(url)
            if not slug:
                continue
            try:
                data = parse_detail_funding(_fetch_html(url))
            except Exception as exc:  # 单条失败不影响其它条目
                entries[slug] = {"error": _clean(str(exc), 120) or "抓取失败", "fetchedAt": int(time.time() * 1000)}
                last_error = str(entries[slug]["error"])
            else:
                entries[slug] = {
                    "raised": data.get("raised", ""),
                    "preval": data.get("preval", ""),
                    "fetchedAt": int(time.time() * 1000),
                }
                if data.get("raised") or data.get("preval"):
                    filled += 1
            _write_funding_cache(entries)  # 每抓一条就落盘，中途被打断也不白跑
            if index < len(urls) - 1 and ICODROPS_FUNDING_INTERVAL:
                time.sleep(ICODROPS_FUNDING_INTERVAL)
    finally:
        with _ENRICH_LOCK:
            _ENRICH_STATE["running"] = False
            _ENRICH_STATE["lastRunAt"] = int(time.time() * 1000)
            _ENRICH_STATE["lastFilled"] = filled
            _ENRICH_STATE["lastError"] = last_error


def schedule_detail_enrichment(items: list[dict[str, Any]]) -> bool:
    """按需在后台补齐详情页的融资/估值。返回是否真的起了线程。

    **必须由调用方（server 端点）在返回响应之后触发**：抓 20 个详情页要十几秒，
    绝不能放进请求路径里。同一时刻只跑一个线程，跑完自动释放。
    """
    pending = _pending_detail_urls(items or [], ICODROPS_FUNDING_BATCH)
    if not pending:
        return False
    with _ENRICH_LOCK:
        if _ENRICH_STATE["running"]:
            return False
        _ENRICH_STATE["running"] = True
    thread = threading.Thread(
        target=_enrich_worker, args=(pending,), name="icodrops-funding-enrich", daemon=True
    )
    thread.start()
    return True


def funding_enrichment_state() -> dict[str, Any]:
    """给诊断端点/日志看的补全进度。"""
    with _ENRICH_LOCK:
        state = dict(_ENRICH_STATE)
    state["cached"] = len(_read_funding_cache())
    return state


def _read_cache() -> dict[str, Any] | None:
    try:
        payload = json.loads(ICODROPS_SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_cache(payload: dict[str, Any]) -> None:
    try:
        ICODROPS_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        tmp = ICODROPS_SNAPSHOT.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(ICODROPS_SNAPSHOT)
    except OSError:
        pass


def fetch_active_airdrops(*, force: bool = False) -> dict[str, Any]:
    """返回 ACTIVE 空投榜；优先落盘缓存，过期或 ``force`` 才重抓。

    失败语义（铁律：不许把抓取失败伪装成空榜）：
    1. 抓取失败 → 退落盘缓存，标 ``stale=True`` 并附 ``error`` 说明真实原因；
    2. 连缓存都没有 → 返回 ``ok=False`` + ``error`` 的空态，前端显示原因而不是「0 条」。
    """
    if not force:
        cached = _read_cache()
        if cached and time.time() - float(cached.get("updatedAt") or 0) / 1000 < ICODROPS_TTL_SECONDS:
            # 缓存命中时也合并一次详情页补全结果（纯本地读文件），补到就固化回快照，
            # 免得每 30 分钟才能看到新补出来的估值。
            items = cached.get("items")
            if isinstance(items, list) and apply_detail_funding(items):
                _write_cache(cached)
            return cached

    try:
        items = parse_active_html(_fetch_html())
        if not items:
            raise RuntimeError("ICODrops ACTIVE 页面没有解析到任何活动")
        apply_detail_funding(items)
        payload = _payload_from_items(items)
        _write_cache(payload)
        return payload
    except Exception as exc:
        reason = _clean(str(exc), 120) or "抓取失败"
        cached = _read_cache()
        if cached:
            cached = dict(cached)
            if isinstance(cached.get("items"), list):
                apply_detail_funding(cached["items"])
            cached["stale"] = True
            cached["error"] = reason
            return cached
        return {
            "ok": False,
            "total": 0,
            "airdropCount": 0,
            "saleCount": 0,
            "otherCount": 0,
            "updatedAt": 0,
            "stale": True,
            "error": reason,
            "source": "icodrops.com/category/active-ico",
            "items": [],
        }
