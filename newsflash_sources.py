from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup


PUBLIC_FEEDS = (
    {
        "id": "bwenews",
        "name": "方程式新闻",
        "label": "BWE",
        "url": "https://rss-public.bwe-ws.com",
        "priority": 80,
    },
    {
        "id": "wublock",
        "name": "吴说区块链",
        "label": "吴说",
        "url": "https://www.wublock123.com/feed",
        "priority": 70,
    },
    {
        # 用户 2026-11-07：「https://foresightnews.pro/airdropnews 把这个集成一下到聚合快讯里」。
        # 它没有 RSS（/rss 一律 302 回首页），但列表页是 Nuxt SSR，`div.news-item` 里
        # 标题/正文/时间全在 HTML 上，所以 kind 走 HTML 解析而不是 parse_feed_xml。
        "id": "foresight",
        "name": "Foresight 空投线报",
        "label": "FN",
        "url": "https://foresightnews.pro/airdropnews",
        "priority": 60,
        "kind": "html-foresight",
    },
    {
        # 用户 2026-11-07 追加：「再把这个页面也集成一下」——空投标签页（搜索「空投」的结果，
        # 混着快讯/文章/专栏，结构和空投线报页完全不同，见 parse_foresight_tag_html）。
        "id": "foresight-tag-airdrop",
        "name": "Foresight 空投标签",
        "label": "FN·标签",
        "url": "https://foresightnews.pro/tag?tagId=55&tagName=%E7%A9%BA%E6%8A%95",
        "priority": 55,
        "kind": "html-foresight-tag",
    },
)

SOURCE_PRIORITY = {
    "blockbeats": 100,
    "bwenews": 80,
    "wublock": 70,
    "foresight": 60,
    "foresight-tag-airdrop": 55,
}

# These publishers already have a direct source above. User-added copies must
# not be fetched as a second source.
DIRECT_SOURCE_FAMILIES = {"blockbeats", "bwenews", "wublock", "foresight"}


def _clean_text(value: Any, limit: int = 5000) -> str:
    raw = html.unescape(str(value or ""))
    text = BeautifulSoup(raw, "lxml").get_text("\n", strip=True)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit]


def source_family(value: Any) -> str:
    normalized = _normalized_source_name(value)
    if "blockbeats" in normalized or "律动" in normalized:
        return "blockbeats"
    if "wublock" in normalized or "吴说" in normalized:
        return "wublock"
    if "bwenews" in normalized or "方程式新闻" in normalized:
        return "bwenews"
    if "foresight" in normalized or "远见" in normalized:
        return "foresight"
    return normalized


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node: ET.Element, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for child in node:
        if _local_name(child.tag) in wanted:
            return "".join(child.itertext()).strip()
    return ""


def _entry_link(node: ET.Element) -> str:
    for child in node:
        if _local_name(child.tag) != "link":
            continue
        href = str(child.attrib.get("href") or "").strip()
        if href and child.attrib.get("rel", "alternate") in {"alternate", ""}:
            return href
        if (child.text or "").strip():
            return str(child.text).strip()
    return ""


def _timestamp_seconds(value: Any) -> int:
    if isinstance(value, (int, float)):
        number = int(value)
        return number // 1000 if number > 10_000_000_000 else number
    text = str(value or "").strip()
    if not text:
        return int(time.time())
    try:
        number = int(float(text))
        return number // 1000 if number > 10_000_000_000 else number
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return int(time.time())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _stable_id(source_id: str, url: str, title: str) -> str:
    digest = hashlib.sha1(f"{source_id}\n{url}\n{title}".encode("utf-8")).hexdigest()[:20]
    return f"{source_id}:{digest}"


def _bwe_title_and_content(raw_title: str, raw_content: str) -> tuple[str, str]:
    normalized = re.sub(r"<br\s*/?>", "\n", html.unescape(raw_title), flags=re.I)
    lines = [_clean_text(line, 1000) for line in normalized.splitlines()]
    lines = [line for line in lines if line and not re.fullmatch(r"[-—_]{5,}", line)]
    useful = [line for line in lines if not re.match(r"^(?:source:|\d{4}-\d{2}-\d{2}\s)", line, flags=re.I)]
    chinese = [line for line in useful[:4] if re.search(r"[\u3400-\u9fff]", line)]
    title = chinese[0] if chinese else (useful[0] if useful else _clean_text(raw_title, 500))
    content_lines = [line for line in useful if line != title]
    content = _clean_text(raw_content, 4000) or "\n".join(content_lines[:5])
    return title[:500], content[:4000]


def parse_feed_xml(xml_text: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    entries = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
    items: list[dict[str, Any]] = []
    for entry in entries[:100]:
        raw_title = _child_text(entry, "title")
        raw_content = _child_text(entry, "content", "description", "summary")
        title = _clean_text(raw_title, 500)
        content = _clean_text(raw_content, 4000)
        if source.get("id") == "bwenews":
            title, content = _bwe_title_and_content(raw_title, raw_content)
        if not title:
            continue
        url = _entry_link(entry) or _child_text(entry, "guid", "id")
        published = _child_text(entry, "pubDate", "published", "updated", "date")
        source_id = str(source.get("id") or "feed")
        items.append(
            {
                "id": _stable_id(source_id, url, title),
                "title": title,
                "content": content,
                "url": url,
                "image": "",
                "links": [url] if url else [],
                "add_time": _timestamp_seconds(published),
                "sourceId": source_id,
                "source": str(source.get("name") or source_id),
                "sourceLabel": str(source.get("label") or source.get("name") or source_id)[:8],
                "sourcePriority": int(source.get("priority") or 50),
            }
        )
    return items


FORESIGHT_ORIGIN = "https://foresightnews.pro"
FORESIGHT_DETAIL_ID = re.compile(r"/(?:news|article)/detail/(\d+)")
FORESIGHT_STAMP = re.compile(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?")
_CST = timezone(timedelta(hours=8))


def _foresight_add_time(node: Any, selectors: tuple[str, ...] = (".publish-time", ".time-detail")) -> int:
    """`2026.11.05 21:38` / `2026-11-06 15:37`（北京时间）→ epoch 秒；只有日期时按当天中午排。"""
    for selector in selectors:
        stamp = node.select_one(selector)
        text = _clean_text(stamp.get_text(" ", strip=True), 40) if stamp else ""
        match = FORESIGHT_STAMP.search(text)
        if not match:
            continue
        year, month, day = (int(match.group(index)) for index in (1, 2, 3))
        hour = int(match.group(4) or 12)
        minute = int(match.group(5) or 0)
        try:
            return int(datetime(year, month, day, hour, minute, tzinfo=_CST).timestamp())
        except ValueError:
            continue
    return int(time.time())


def parse_foresight_html(html_text: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    """Foresight News 列表页（Nuxt SSR）→ 聚合快讯 items。

    页面结构与 RSS 无关：每条是 ``div.news-item``，里面
    ``a[href=/news/detail/<id>]`` + ``h3`` 标题 + ``.content-text`` 正文 +
    ``.publish-time`` 时间。**正文容器里包含 h3 和时间**，所以正文只取叶子 ``<p>``
    （外层 ``<p><p>…</p></p>`` 会被跳过），否则每条摘要都会以标题重复开头。
    深度文章走 ``/article/detail/<id>``，同样收。
    """
    soup = BeautifulSoup(html_text, "lxml")
    source_id = str(source.get("id") or "foresight")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in soup.select("div.news-item"):
        link = node.select_one("a[href*='/news/detail/'], a[href*='/article/detail/']")
        href = str(link.get("href") or "") if link else ""
        heading = node.select_one(".content-text h3") or node.select_one("h3")
        title = _clean_text(heading.get_text(" ", strip=True), 240) if heading else ""
        if not title:
            continue
        detail = FORESIGHT_DETAIL_ID.search(href)
        item_id = f"{source_id}:{detail.group(1)}" if detail else _stable_id(source_id, href, title)
        if item_id in seen:
            continue
        seen.add(item_id)
        container = node.select_one(".content-text") or node
        paragraphs: list[str] = []
        for paragraph in container.find_all("p"):
            if paragraph.find("p"):  # 外层 <p> 只是容器，取叶子才不会重复
                continue
            text = _clean_text(paragraph.get_text(" ", strip=True), 1000)
            if text and text != title:
                paragraphs.append(text)
        url = FORESIGHT_ORIGIN + href if href.startswith("/") else href
        items.append(
            {
                "id": item_id,
                "title": title,
                "content": "\n".join(paragraphs)[:4000],
                "url": url,
                "image": "",
                "links": [url] if url else [],
                "add_time": _foresight_add_time(node),
                "sourceId": source_id,
                "source": str(source.get("name") or "Foresight News"),
                "sourceLabel": str(source.get("label") or "FN")[:8],
                "sourcePriority": int(source.get("priority") or 60),
            }
        )
    items.sort(key=lambda item: item.get("add_time") or 0, reverse=True)
    return items[:60]


def parse_foresight_tag_html(html_text: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    """Foresight「标签 / 搜索结果」页（``/tag?tagId=…``）→ items。

    用户 2026-11-07 追加：「再把这个页面也集成一下」。
    结构与空投线报页完全不同，**不要复用 `parse_foresight_html`**：
      - 每条是 ``div.item_news``（空投线报页是 ``div.news-item``）
      - 标题在 ``a.news_body_title``（已在 a 标签文本里）
      - 正文在 ``.news_body_content``（一段，可能套 ``<span style=…>``）
      - 时间在 ``.foot_tag_time``，形如 ``2026-11-06 15:37``（横线，不是点号）
      - 同一页混着「快讯 / 文章 / 专栏」，标题级别都当快讯处理
    """
    soup = BeautifulSoup(html_text, "lxml")
    source_id = str(source.get("id") or "foresight-tag")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in soup.select("div.item_news"):
        link = node.select_one("a.news_body_title[href*='/news/detail/'], a.news_body_title[href*='/article/detail/']")
        if link is None:
            link = node.select_one("a[href*='/news/detail/'], a[href*='/article/detail/']")
        href = str(link.get("href") or "") if link else ""
        title = _clean_text(link.get_text(" ", strip=True), 240) if link else ""
        if not title:
            heading = node.select_one(".news_body_title")
            title = _clean_text(heading.get_text(" ", strip=True), 240) if heading else ""
        if not title:
            continue
        detail = FORESIGHT_DETAIL_ID.search(href)
        item_id = f"{source_id}:{detail.group(1)}" if detail else _stable_id(source_id, href, title)
        if item_id in seen:
            continue
        seen.add(item_id)
        body = node.select_one(".news_body_content")
        content = _clean_text(body.get_text(" ", strip=True), 4000) if body else ""
        url = FORESIGHT_ORIGIN + href if href.startswith("/") else href
        items.append(
            {
                "id": item_id,
                "title": title,
                "content": content,
                "url": url,
                "image": "",
                "links": [url] if url else [],
                "add_time": _foresight_add_time(node, (".foot_tag_time", ".publish-time")),
                "sourceId": source_id,
                "source": str(source.get("name") or "Foresight 空投标签"),
                "sourceLabel": str(source.get("label") or "FN")[:8],
                "sourcePriority": int(source.get("priority") or 55),
            }
        )
    items.sort(key=lambda item: item.get("add_time") or 0, reverse=True)
    return items[:60]


def _load_json_env(name: str) -> Any:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def configured_feed_sources() -> list[dict[str, Any]]:
    sources = [dict(source) for source in PUBLIC_FEEDS]
    extra = _load_json_env("NEWSFLASH_EXTRA_FEEDS_JSON")
    if isinstance(extra, dict):
        extra = [{"name": name, "url": url} for name, url in extra.items()]
    for index, row in enumerate(extra if isinstance(extra, list) else []):
        if not isinstance(row, dict) or not str(row.get("url") or "").startswith(("https://", "http://")):
            continue
        name = str(row.get("name") or f"扩展新闻源 {index + 1}").strip()
        source_id = re.sub(r"[^a-z0-9_-]+", "-", str(row.get("id") or name).lower()).strip("-")
        if source_family(source_id) in DIRECT_SOURCE_FAMILIES or source_family(name) in DIRECT_SOURCE_FAMILIES:
            continue
        sources.append(
            {
                "id": source_id or f"extra-{index + 1}",
                "name": name,
                "label": str(row.get("label") or name)[:8],
                "url": str(row["url"]),
                "priority": int(row.get("priority") or 50),
            }
        )
    return sources


# Local VPN/proxy clients (Clash, v2rayN, ...) expose HTTP proxies on these
# well-known loopback ports. The dashboard host rotates proxy software over
# time, so every fetch races direct + all plausible local proxy routes and the
# first healthy response wins. Dead ports fail fast on loopback connect, so
# probing them inside the race is effectively free.
PROXY_CANDIDATE_PORTS = (7890, 7897, 7899, 10809, 2080, 53000)
NEWSFLASH_ROUTE_WORKERS = max(
    2,
    min(8, int(os.getenv("NEWSFLASH_ROUTE_WORKERS", "4") or "4")),
)
NEWSFLASH_ROUTE_POOL = ThreadPoolExecutor(
    max_workers=NEWSFLASH_ROUTE_WORKERS,
    thread_name_prefix="newsflash-route",
)


def _env_proxy_url() -> str:
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        value = str(os.getenv(name) or "").strip()
        if value:
            return value.rstrip("/")
    return ""


def _candidate_routes() -> list[str]:
    """Routes to race: direct first, then env proxy, then known local ports."""
    routes = [""]
    for proxy in [_env_proxy_url()] + [f"http://127.0.0.1:{port}" for port in PROXY_CANDIDATE_PORTS]:
        if proxy and proxy not in routes:
            routes.append(proxy)
    return routes


def _get_via_route(url: str, headers: dict[str, str], timeout: float, proxy: str) -> requests.Response:
    # One retry per route: transient TLS resets (VPN tunnel handoff, CDN edge)
    # must not disqualify an otherwise healthy route.
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            if proxy:
                return requests.get(
                    url,
                    headers=headers,
                    timeout=timeout,
                    proxies={"http": proxy, "https": proxy},
                    allow_redirects=True,
                )
            # Direct route: ignore HTTP(S)_PROXY env so a stale desktop proxy
            # cannot wedge the fetch; the OS default route (e.g. a system VPN
            # tunnel) applies.
            session = requests.Session()
            session.trust_env = False
            try:
                return session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            finally:
                session.close()
        except requests.RequestException as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.4)
    assert last_error is not None
    raise last_error


def http_get_race(url: str, *, headers: dict[str, str] | None = None, timeout: float = 10.0) -> requests.Response:
    """GET via direct + local proxy routes in parallel; first healthy response wins.

    A half-dead local proxy can hold a news source until timeout while the same
    URL is fine direct (or via another proxy port). Racing the routes keeps the
    newsflash aggregation working whichever route is broken today.
    """
    request_headers = dict(headers or {"User-Agent": "XingyunSocietyNewsflash/1.0"})
    routes = _candidate_routes()
    if len(routes) == 1:
        return _get_via_route(url, request_headers, timeout, "")
    # All feed sources share one bounded route pool.  Previously every source
    # created up to seven threads of its own, so a six-source refresh could
    # briefly retain 40+ stacks whenever proxy routes timed out.
    futures = [
        NEWSFLASH_ROUTE_POOL.submit(_get_via_route, url, request_headers, timeout, proxy)
        for proxy in routes
    ]
    first_error: Exception | None = None
    try:
        for future in as_completed(futures):
            try:
                response = future.result()
                response.raise_for_status()
            except Exception as exc:  # route failure: try the next completed route
                first_error = first_error or exc
                continue
            return response
        raise first_error or RuntimeError(f"all routes failed for {url}")
    finally:
        for future in futures:
            future.cancel()


FORESIGHT_IMPERSONATE = os.getenv("NEWSFLASH_FORESIGHT_IMPERSONATE", "chrome").strip() or "chrome"
# 标签页 SSR 只渲染首屏 10 条，往下要靠站内接口翻页（2026-11-07 用户：「怎么只有20几条」）。
FORESIGHT_API_ORIGIN = "https://api.foresightnews.pro"
FORESIGHT_TAG_FEED_PATH = "/v2/feed/tag"
FORESIGHT_TAG_PAGES = max(1, min(int(os.getenv("NEWSFLASH_FORESIGHT_TAG_PAGES", "4") or 4), 12))
FORESIGHT_TAG_PAGE_SIZE = max(10, min(int(os.getenv("NEWSFLASH_FORESIGHT_TAG_PAGE_SIZE", "20") or 20), 50))
# 空投线报页（/airdropnews）就是这两条时间线的事件流，页面上也是靠接口往下翻。
FORESIGHT_AIRDROP_EVENT_IDS = os.getenv("NEWSFLASH_FORESIGHT_AIRDROP_EVENTS", "235,53").strip() or "235,53"
FORESIGHT_AIRDROP_PAGES = max(1, min(int(os.getenv("NEWSFLASH_FORESIGHT_AIRDROP_PAGES", "3") or 3), 10))
FORESIGHT_AIRDROP_PAGE_SIZE = max(10, min(int(os.getenv("NEWSFLASH_FORESIGHT_AIRDROP_PAGE_SIZE", "20") or 20), 50))
FORESIGHT_TAG_ID = re.compile(r"[?&]tagId=(\d+)", re.I)
FORESIGHT_ROUTE_TTL = 300.0
_FORESIGHT_ROUTE_LOCK = threading.Lock()
_FORESIGHT_ROUTE: dict[str, Any] = {"proxy": None, "at": 0.0}


def _remember_foresight_route(proxy: str) -> None:
    with _FORESIGHT_ROUTE_LOCK:
        _FORESIGHT_ROUTE["proxy"] = proxy
        _FORESIGHT_ROUTE["at"] = time.time()


def _preferred_foresight_route() -> str | None:
    with _FORESIGHT_ROUTE_LOCK:
        proxy = _FORESIGHT_ROUTE.get("proxy")
        at = float(_FORESIGHT_ROUTE.get("at") or 0.0)
    if proxy is None or time.time() - at > FORESIGHT_ROUTE_TTL:
        return None
    return str(proxy)



def _foresight_get_via(url: str, timeout: float, proxy: str, headers: dict[str, str] | None = None):
    """Foresight 专用：requests/urllib3 一律被回 567，必须伪装 Chrome 的 TLS 指纹。"""
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:  # 缺库只影响这一个源，别带崩整轮聚合
        raise RuntimeError("缺少 curl_cffi，无法抓取 Foresight（pip install curl_cffi）") from exc
    kwargs: dict[str, Any] = {"impersonate": FORESIGHT_IMPERSONATE, "timeout": timeout}
    if headers:
        kwargs["headers"] = headers
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    else:
        # 直连路线：显式 Ban 掉 env 代理，否则半死的 57568 会把直连也代理走。
        kwargs["proxies"] = {"http": "", "https": ""}
    response = curl_requests.get(url, **kwargs)
    if int(response.status_code) != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    return response


def fetch_foresight_html(url: str, timeout: float = 12.0) -> str:
    """抓 Foresight 列表页 HTML（多路由并发，谁先拿到 200 用谁）。

    567 不是限流而是 WAF 按 TLS 指纹判定：同一条路 `requests` 回 567、`curl_cffi`
    伪装 chrome 回 200。所以这里不走 `http_get_race`（它绑死 requests）。

    整轮 race 失败时再补一轮：WAF 偶发抽风、或某个代理端口正好在重连，单轮 5 条路由
    全挂过一次（2026-11-07 重启后实测 foresight 计数掉到 0），重试一次就恢复了。
    """
    wait = max(6.0, min(float(timeout or 12.0), 25.0))
    last_error: Exception | None = None
    for attempt in range(2):
        futures = [
            NEWSFLASH_ROUTE_POOL.submit(_foresight_get_via, url, wait, proxy)
            for proxy in _candidate_routes()
        ]
        try:
            for future in as_completed(futures):
                try:
                    return future.result().text
                except Exception as exc:  # 该路失败，等下一个
                    last_error = last_error or exc
                    continue
        finally:
            for future in futures:
                future.cancel()
        if attempt == 0:
            time.sleep(0.6)
    raise last_error or RuntimeError(f"all routes failed for {url}")


def _foresight_unpack(payload: Any) -> tuple[list[dict[str, Any]], bool]:
    """解出条目列表 + ``has_more``。

    站内接口两种形状都有：``data`` 直接是 ``{items|list, has_more}``，或者整包被压成
    ``base64(zlib(json))``（解出来仍是同样的 dict）。别假设只有一种。
    """
    data = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data, str) and data.strip():
        try:
            data = json.loads(zlib.decompress(base64.b64decode(data)))
        except Exception as exc:  # 解不开就是接口换了压缩方式，别静默当空榜
            raise RuntimeError(f"Foresight 接口返回无法解压：{exc}") from exc
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)], False
    if not isinstance(data, dict):
        return [], False
    rows = data.get("items")
    if not isinstance(rows, list):
        rows = data.get("list")
    if isinstance(rows, str) and rows.strip():
        try:
            rows = json.loads(zlib.decompress(base64.b64decode(rows)))
        except Exception as exc:
            raise RuntimeError(f"Foresight 接口 list 无法解压：{exc}") from exc
    if not isinstance(rows, list):
        rows = []
    return [row for row in rows if isinstance(row, dict)], data.get("has_more") is not False


def fetch_foresight_json(url: str, timeout: float = 12.0) -> Any:
    """接口请求：多路由并发取第一个成功，并把赢的那条路由记下来（省掉后续超时）。

    翻页要连打 4 次，若每次都从头 race，直连那条死路由会白等一个超时。
    """
    wait = max(6.0, min(float(timeout or 12.0), 25.0))
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": FORESIGHT_ORIGIN + "/",
        "X-Requested-With": "XMLHttpRequest",
    }
    preferred = _preferred_foresight_route()
    if preferred is not None:
        try:
            payload = _foresight_get_via(url, wait, preferred, headers).json()
            _remember_foresight_route(preferred)
            return payload
        except Exception:
            pass
    futures = {
        NEWSFLASH_ROUTE_POOL.submit(_foresight_get_via, url, wait, proxy, headers): proxy
        for proxy in _candidate_routes()
        if proxy != preferred
    }
    last_error: Exception | None = None
    try:
        for future in as_completed(futures):
            try:
                payload = future.result().json()
            except Exception as exc:  # 该路失败，等下一个
                last_error = last_error or exc
                continue
            _remember_foresight_route(futures[future])
            return payload
    finally:
        for future in futures:
            future.cancel()
    raise last_error or RuntimeError(f"all routes failed for {url}")


def parse_foresight_feed_json(batches: list[list[dict[str, Any]]], source: dict[str, Any]) -> list[dict[str, Any]]:
    """``/v2/feed/tag`` 与 ``/v1/eventItems`` 的条目 → items。

    两条接口的条目形状一样：``{source_type, source_id, published_at, news|article|…}``，
    同一个 feed 里文章/专题/快讯混排；标签页与空投线报页都只看「快讯」，
    所以只收带 ``news`` 字段的条目。
    """
    source_id = str(source.get("id") or "foresight")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rows in batches:
        for entry in rows:
            news = entry.get("news")
            if not isinstance(news, dict):
                continue  # 文章 / 专题 / 事件，快讯流里不要
            news_id = str(news.get("id") or entry.get("source_id") or "").strip()
            title = _clean_text(news.get("title"), 240)
            if not news_id or not title:
                continue
            item_id = f"{source_id}:{news_id}"
            if item_id in seen:
                continue
            seen.add(item_id)
            content = _clean_text(news.get("brief"), 4000) or _clean_text(news.get("content"), 4000)
            url = f"{FORESIGHT_ORIGIN}/news/detail/{news_id}"
            published = news.get("published_at") or entry.get("published_at") or 0
            try:
                add_time = int(published) or int(time.time())
            except (TypeError, ValueError):
                add_time = int(time.time())
            items.append(
                {
                    "id": item_id,
                    "title": title,
                    "content": content,
                    "url": url,
                    "image": str(news.get("img") or entry.get("img") or ""),
                    "links": [url],
                    "add_time": add_time,
                    "sourceId": source_id,
                    "source": str(source.get("name") or "Foresight News"),
                    "sourceLabel": str(source.get("label") or "FN")[:8],
                    "sourcePriority": int(source.get("priority") or 55),
                }
            )
    items.sort(key=lambda item: item.get("add_time") or 0, reverse=True)
    return items[:120]


def _fetch_foresight_pages(base_url: str, pages: int, source: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
    """按页拉接口，某一页失败就用手上已有的（不要整源归零）。"""
    batches: list[list[dict[str, Any]]] = []
    for page in range(1, max(1, pages) + 1):
        try:
            payload = fetch_foresight_json(f"{base_url}&page={page}", timeout)
        except Exception:
            if batches:
                break
            raise
        if not isinstance(payload, dict) or int(payload.get("code") or 0) != 1:
            if batches:
                break
            message = payload.get("msg") if isinstance(payload, dict) else "unexpected payload"
            raise RuntimeError(f"Foresight 接口失败：{message}")
        rows, has_more = _foresight_unpack(payload)
        batches.append(rows)
        if not has_more or not rows:
            break
    return parse_foresight_feed_json(batches, source)


def fetch_foresight_tag_news(tag_id: str, source: dict[str, Any], timeout: float = 12.0) -> list[dict[str, Any]]:
    """标签页：SSR 只给首屏 10 条，往下是前端无限滚动。

    接口一次 20 条、约一半是文章，所以默认翻 4 页（≈两周、40~50 条快讯）。
    """
    base = (
        f"{FORESIGHT_API_ORIGIN}{FORESIGHT_TAG_FEED_PATH}"
        f"?tag_ids={tag_id}&size={FORESIGHT_TAG_PAGE_SIZE}&search_type=news"
    )
    return _fetch_foresight_pages(base, FORESIGHT_TAG_PAGES, source, timeout)


def fetch_foresight_airdrop_news(source: dict[str, Any], timeout: float = 12.0) -> list[dict[str, Any]]:
    """空投线报页（``/airdropnews``）：页面本身只渲染 20 条，同样靠接口翻页。

    `airdrop_timeline_ids` = 235（Web3 改命指南）+ 53（空投早知道），页面就是这两条
    时间线的事件流；id 变了可用 NEWSFLASH_FORESIGHT_AIRDROP_EVENTS 覆盖。
    """
    base = (
        f"{FORESIGHT_API_ORIGIN}/v1/eventItems"
        f"?event_ids={FORESIGHT_AIRDROP_EVENT_IDS}&size={FORESIGHT_AIRDROP_PAGE_SIZE}&sort_by=desc"
    )
    return _fetch_foresight_pages(base, FORESIGHT_AIRDROP_PAGES, source, timeout)


def _fetch_feed(source: dict[str, Any], headers: dict[str, str], timeout: float) -> list[dict[str, Any]]:
    # 站点没有 RSS 的源（Foresight）按 kind 走抓取 + 解析，其余仍当订阅源。
    kind = str(source.get("kind") or "")
    if kind.startswith("html-foresight"):
        # 两个页面都只有首屏十几二十条，往下是前端无限滚动；先走站内接口翻页，
        # 接口挂了再退回 SSR HTML（条数少但不断流）。
        if kind == "html-foresight-tag":
            tag_id = FORESIGHT_TAG_ID.search(str(source.get("url") or ""))
            if tag_id:
                try:
                    items = fetch_foresight_tag_news(tag_id.group(1), source, timeout)
                    if items:
                        return items
                except Exception:
                    pass
            return parse_foresight_tag_html(fetch_foresight_html(str(source["url"]), timeout), source)
        try:
            items = fetch_foresight_airdrop_news(source, timeout)
            if items:
                return items
        except Exception:
            pass
        return parse_foresight_html(fetch_foresight_html(str(source["url"]), timeout), source)
    response = http_get_race(str(source["url"]), headers=headers, timeout=timeout)
    if len(response.content) > 2_000_000:
        raise ValueError("feed response is too large")
    return parse_feed_xml(response.text, source)


def fetch_configured_feed_items(
    feed_id: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """Fetch one configured public feed by id, exactly like the aggregator would.

    The desktop-alert pipeline mirrors 方程式新闻 (``bwenews``) through this
    helper so its popups use the same parser and route-racing as the aggregated
    page feed, while staying an independent fetch: a broken 律动 route can no
    longer silence 方程式, and vice versa.
    """
    wanted = str(feed_id or "").strip()
    source = next((row for row in configured_feed_sources() if str(row.get("id")) == wanted), None)
    if source is None:
        return []
    request_headers = dict(headers or {"User-Agent": "XingyunSocietyNewsflash/1.0"})
    wait = max(3.0, min(float(timeout or 8.0), 20.0))
    return _fetch_feed(source, request_headers, wait)


def _normalized_source_name(value: Any) -> str:
    """Normalize decorative publisher names without changing visible labels."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in text if character.isalnum() and character not in {"丨"})


def _canonical_url(value: Any) -> str:
    try:
        parts = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if not parts.netloc:
        return ""
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def _normalized_story_text(item: dict[str, Any]) -> str:
    value = f"{item.get('title') or ''} {item.get('content') or ''}"[:1200].lower()
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"(?:快讯|消息|据悉|报道称|最新|独家|breaking|source)[:：\s-]*", " ", value, flags=re.I)
    value = re.sub(r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}(?:日)?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?", " ", value)
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", value)


def _number_anchors(value: str) -> set[str]:
    return {token.lstrip("0") or "0" for token in re.findall(r"\d+(?:\.\d+)?", value)}


def _bigrams(value: str) -> set[str]:
    return {value[index : index + 2] for index in range(max(0, len(value) - 1))}


EVENT_ACTION_PATTERNS = {
    "listing": re.compile(r"上线|上币|上市|新增.*交易|开放.*交易|list(?:ing|ed)?", re.I),
    "delisting": re.compile(r"下线|下架|退市|停止.*交易|delist", re.I),
    "approval": re.compile(r"批准|通过|获批|核准|approve", re.I),
    "rejection": re.compile(r"拒绝|否决|驳回|不予批准|reject|deny", re.I),
    "rise": re.compile(r"上涨|涨超|突破|创.*高|走高|rise|surge|jump", re.I),
    "fall": re.compile(r"下跌|跌超|跌破|创.*低|走低|fall|drop|plunge", re.I),
    "buy": re.compile(r"买入|增持|购入|收购|buy|acquir", re.I),
    "sell": re.compile(r"卖出|减持|抛售|出售|sell|dump", re.I),
    "launch": re.compile(r"发布|推出|上线.*产品|启动|launch|release", re.I),
    "halt": re.compile(r"暂停|终止|停止|关闭|halt|suspend|terminate", re.I),
}
EVENT_ACTION_CONFLICTS = {
    frozenset(("listing", "delisting")),
    frozenset(("approval", "rejection")),
    frozenset(("rise", "fall")),
    frozenset(("buy", "sell")),
    frozenset(("launch", "halt")),
}
SEMANTIC_ENTITY_TERMS = (
    "币安", "Binance", "Coinbase", "OKX", "Bybit", "Gate", "Upbit", "Bithumb",
    "美联储", "SEC", "CFTC", "ETF", "比特币", "以太坊", "Solana", "BNB",
    "特朗普", "马斯克", "美债", "美元", "稳定币", "Robinhood",
)


def _event_actions(item: dict[str, Any]) -> set[str]:
    text = f"{item.get('title') or ''} {item.get('content') or ''}"
    return {name for name, pattern in EVENT_ACTION_PATTERNS.items() if pattern.search(text)}


def _semantic_entities(item: dict[str, Any]) -> set[str]:
    text = f"{item.get('title') or ''} {item.get('content') or ''}"
    entities = {
        token.casefold()
        for token in re.findall(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9._-]{1,14}(?![A-Za-z0-9])", text)
        if token.casefold() not in {"the", "and", "usd", "usdt"}
    }
    for term in SEMANTIC_ENTITY_TERMS:
        if term.casefold() in text.casefold():
            entities.add(term.casefold())
    for quoted in re.findall(r"[《「『\"']([^》」』\"']{2,18})[》」』\"']", text):
        entities.add(_normalized_story_text({"title": quoted}))
    return {entity for entity in entities if entity}


def _actions_conflict(left: set[str], right: set[str]) -> bool:
    return any(
        any(first in left and second in right for first in pair for second in pair if first != second)
        for pair in EVENT_ACTION_CONFLICTS
    )


@dataclass(frozen=True, slots=True)
class _StoryFingerprint:
    family: str
    story_id: str
    url: str
    text: str
    timestamp: int
    actions: frozenset[str]
    numbers: frozenset[str]
    bigrams: frozenset[str]
    entities: frozenset[str]


def _story_fingerprint(item: dict[str, Any]) -> _StoryFingerprint:
    text = _normalized_story_text(item)
    return _StoryFingerprint(
        family=source_family(item.get("sourceId") or item.get("source")),
        story_id=str(item.get("id") or "").strip(),
        url=_canonical_url(item.get("url")),
        text=text,
        timestamp=_timestamp_seconds(item.get("add_time")),
        actions=frozenset(_event_actions(item)),
        numbers=frozenset(_number_anchors(text)),
        bigrams=frozenset(_bigrams(text[:500])),
        entities=frozenset(_semantic_entities(item)),
    )


def _fingerprints_match(
    left: _StoryFingerprint,
    right: _StoryFingerprint,
    window_seconds: int = 18 * 3600,
) -> bool:
    if left.story_id and right.story_id and left.family == right.family and left.story_id == right.story_id:
        return True
    if left.url and left.url == right.url:
        return True
    left_text, right_text = left.text, right.text
    if not left_text or not right_text:
        return False
    if left_text == right_text:
        return True
    if abs(left.timestamp - right.timestamp) > window_seconds:
        return False
    left_actions, right_actions = left.actions, right.actions
    if _actions_conflict(left_actions, right_actions):
        return False
    left_numbers, right_numbers = left.numbers, right.numbers
    if left_numbers and right_numbers and not (left_numbers & right_numbers):
        return False
    shorter, longer = sorted((left_text, right_text), key=len)
    if len(shorter) >= 18 and shorter in longer and len(shorter) / len(longer) >= 0.45:
        return True
    ratio = SequenceMatcher(None, left_text[:700], right_text[:700]).ratio()
    left_pairs, right_pairs = left.bigrams, right.bigrams
    union = left_pairs | right_pairs
    jaccard = len(left_pairs & right_pairs) / len(union) if union else 0.0
    shared_actions = left_actions & right_actions
    shared_entities = left.entities & right.entities
    core_facts_match = bool(
        shared_actions
        and shared_entities
        and (not left_numbers or not right_numbers or bool(left_numbers & right_numbers))
        and (len(shared_entities) >= 2 or jaccard >= 0.2 or ratio >= 0.48)
    )
    return core_facts_match or ratio >= 0.78 or (ratio >= 0.62 and jaccard >= 0.5)


def stories_match(left: dict[str, Any], right: dict[str, Any], window_seconds: int = 18 * 3600) -> bool:
    return _fingerprints_match(_story_fingerprint(left), _story_fingerprint(right), window_seconds)


class StoryIndex:
    """Bounded, thread-safe index of story fingerprints.

    Keeps one story from producing two desktop popups when the same wire item
    arrives from two publishers (律动 and 方程式新闻 both carry the same crypto
    news).  Fingerprints are built once per retained row and once per lookup, so
    a few hundred entries stay cheap on a 12-second poll — unlike calling
    :func:`stories_match` pair-by-pair, which would rebuild every fingerprint on
    every comparison.
    """

    def __init__(self, *, window_seconds: int = 6 * 3600, limit: int = 400) -> None:
        self._lock = threading.Lock()
        self._window_seconds = max(60, int(window_seconds))
        self._limit = max(1, int(limit))
        self._rows: list[tuple[float, _StoryFingerprint]] = []

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()

    def add(self, item: dict[str, Any]) -> None:
        if not isinstance(item, dict):
            return
        fingerprint = _story_fingerprint(item)
        if not fingerprint.text and not fingerprint.url:
            return
        now = time.time()
        cutoff = now - self._window_seconds
        with self._lock:
            self._rows = [row for row in self._rows if row[0] >= cutoff]
            self._rows.append((now, fingerprint))
            if len(self._rows) > self._limit:
                del self._rows[: len(self._rows) - self._limit]

    def contains(self, item: dict[str, Any]) -> bool:
        if not isinstance(item, dict):
            return False
        fingerprint = _story_fingerprint(item)
        cutoff = time.time() - self._window_seconds
        with self._lock:
            rows = [row_fingerprint for stamp, row_fingerprint in self._rows if stamp >= cutoff]
        return any(
            _fingerprints_match(row_fingerprint, fingerprint, self._window_seconds)
            for row_fingerprint in rows
        )


def _source_record(item: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(item.get("sourceId") or "unknown"),
        "name": str(item.get("source") or "未知来源"),
        "label": str(item.get("sourceLabel") or item.get("source") or "来源")[:8],
        "url": str(item.get("url") or ""),
    }


def deduplicate_news_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict) or not _clean_text(raw.get("title"), 500):
            continue
        item = dict(raw)
        item["title"] = _clean_text(item.get("title"), 500)
        item["content"] = _clean_text(item.get("content"), 5000)
        item["add_time"] = _timestamp_seconds(item.get("add_time"))
        item["sourceId"] = str(item.get("sourceId") or "unknown")
        item["source"] = str(item.get("source") or "未知来源")
        item["sourceLabel"] = str(item.get("sourceLabel") or item["source"])[:8]
        item["sourcePriority"] = int(item.get("sourcePriority") or SOURCE_PRIORITY.get(item["sourceId"], 50))
        item["sources"] = [_source_record(item)]
        item["duplicateCount"] = 0
        prepared.append(item)

    prepared.sort(key=lambda row: (row["sourcePriority"], row["add_time"], len(row["content"])), reverse=True)
    unique: list[dict[str, Any]] = []
    unique_fingerprints: list[_StoryFingerprint] = []
    for item in prepared:
        item_fingerprint = _story_fingerprint(item)
        duplicate_index = next(
            (index for index, fingerprint in enumerate(unique_fingerprints) if _fingerprints_match(fingerprint, item_fingerprint)),
            None,
        )
        if duplicate_index is None:
            unique.append(item)
            unique_fingerprints.append(item_fingerprint)
            continue
        duplicate = unique[duplicate_index]
        known_ids = {source["id"] for source in duplicate["sources"]}
        for source in item["sources"]:
            if source["id"] not in known_ids:
                duplicate["sources"].append(source)
        duplicate["duplicateCount"] += 1 + int(item.get("duplicateCount") or 0)
        if len(item["content"]) > len(duplicate["content"]):
            duplicate["content"] = item["content"]
            unique_fingerprints[duplicate_index] = _story_fingerprint(duplicate)
    unique.sort(key=lambda row: row["add_time"], reverse=True)
    return unique


def aggregate_newsflash(blockbeats_payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    request_headers = dict(headers or {"User-Agent": "XingyunSocietyNewsflash/1.0"})
    timeout = max(3.0, min(float(os.getenv("NEWSFLASH_SOURCE_TIMEOUT_SECONDS", "8") or "8"), 20.0))
    all_items: list[dict[str, Any]] = []
    source_status: list[dict[str, Any]] = []
    blockbeats_items = blockbeats_payload.get("items") if isinstance(blockbeats_payload.get("items"), list) else []
    for raw in blockbeats_items:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item.update({"sourceId": "blockbeats", "source": "BlockBeats 律动", "sourceLabel": "BB", "sourcePriority": 100})
        all_items.append(item)
    blockbeats_error = str(blockbeats_payload.get("error") or "").strip()
    blockbeats_status = {"id": "blockbeats", "name": "BlockBeats 律动", "status": "error" if blockbeats_error else "ok", "count": len(blockbeats_items)}
    if blockbeats_error:
        blockbeats_status["error"] = blockbeats_error[:180]
    source_status.append(blockbeats_status)

    jobs: dict[Any, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="newsflash-source") as pool:
        for source in configured_feed_sources():
            jobs[pool.submit(_fetch_feed, source, request_headers, timeout)] = source
        for future in as_completed(jobs):
            source = jobs[future]
            try:
                rows = future.result()
                all_items.extend(rows)
                source_status.append({"id": source["id"], "name": source["name"], "status": "ok", "count": len(rows)})
            except Exception as exc:
                source_status.append({"id": source["id"], "name": source["name"], "status": "error", "count": 0, "error": str(exc)[:180]})

    # 上限放到 240：主快讯页仍只要最新 120 条（服务端按请求再裁），
    # 但空投线报页要的是「Foresight 两个源的全量」，卡在 120 会把它们挤掉。
    unique = deduplicate_news_items(all_items)[:240]
    removed = max(0, len(all_items) - len(unique))
    return {
        "updatedAt": int(time.time() * 1000),
        "items": unique,
        "sources": sorted(source_status, key=lambda row: (row["status"] != "ok", row["name"])),
        "sourceCount": sum(1 for row in source_status if row["status"] == "ok"),
        "rawCount": len(all_items),
        "deduplicatedCount": removed,
    }
