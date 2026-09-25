from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
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
)

SOURCE_PRIORITY = {
    "blockbeats": 100,
    "bwenews": 80,
    "wublock": 70,
}

# These publishers already have a direct source above. User-added copies must
# not be fetched as a second source.
DIRECT_SOURCE_FAMILIES = {"blockbeats", "bwenews", "wublock"}


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
    pool = ThreadPoolExecutor(max_workers=len(routes), thread_name_prefix="newsflash-route")
    futures = [pool.submit(_get_via_route, url, request_headers, timeout, proxy) for proxy in routes]
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
        pool.shutdown(wait=False, cancel_futures=True)


def _fetch_feed(source: dict[str, Any], headers: dict[str, str], timeout: float) -> list[dict[str, Any]]:
    response = http_get_race(str(source["url"]), headers=headers, timeout=timeout)
    if len(response.content) > 2_000_000:
        raise ValueError("feed response is too large")
    return parse_feed_xml(response.text, source)


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


def stories_match(left: dict[str, Any], right: dict[str, Any], window_seconds: int = 18 * 3600) -> bool:
    left_family = source_family(left.get("sourceId") or left.get("source"))
    right_family = source_family(right.get("sourceId") or right.get("source"))
    left_id, right_id = str(left.get("id") or "").strip(), str(right.get("id") or "").strip()
    if left_id and right_id and left_family == right_family and left_id == right_id:
        return True
    left_url, right_url = _canonical_url(left.get("url")), _canonical_url(right.get("url"))
    if left_url and left_url == right_url:
        return True
    left_text, right_text = _normalized_story_text(left), _normalized_story_text(right)
    if not left_text or not right_text:
        return False
    if left_text == right_text:
        return True
    left_time = _timestamp_seconds(left.get("add_time"))
    right_time = _timestamp_seconds(right.get("add_time"))
    if abs(left_time - right_time) > window_seconds:
        return False
    left_actions, right_actions = _event_actions(left), _event_actions(right)
    if _actions_conflict(left_actions, right_actions):
        return False
    left_numbers, right_numbers = _number_anchors(left_text), _number_anchors(right_text)
    if left_numbers and right_numbers and not (left_numbers & right_numbers):
        return False
    shorter, longer = sorted((left_text, right_text), key=len)
    if len(shorter) >= 18 and shorter in longer and len(shorter) / len(longer) >= 0.45:
        return True
    ratio = SequenceMatcher(None, left_text[:700], right_text[:700]).ratio()
    left_pairs, right_pairs = _bigrams(left_text[:500]), _bigrams(right_text[:500])
    union = left_pairs | right_pairs
    jaccard = len(left_pairs & right_pairs) / len(union) if union else 0.0
    shared_actions = left_actions & right_actions
    shared_entities = _semantic_entities(left) & _semantic_entities(right)
    core_facts_match = bool(
        shared_actions
        and shared_entities
        and (not left_numbers or not right_numbers or bool(left_numbers & right_numbers))
        and (len(shared_entities) >= 2 or jaccard >= 0.2 or ratio >= 0.48)
    )
    return core_facts_match or ratio >= 0.78 or (ratio >= 0.62 and jaccard >= 0.5)


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
    for item in prepared:
        duplicate = next((kept for kept in unique if stories_match(kept, item)), None)
        if duplicate is None:
            unique.append(item)
            continue
        known_ids = {source["id"] for source in duplicate["sources"]}
        for source in item["sources"]:
            if source["id"] not in known_ids:
                duplicate["sources"].append(source)
        duplicate["duplicateCount"] += 1 + int(item.get("duplicateCount") or 0)
        if len(item["content"]) > len(duplicate["content"]):
            duplicate["content"] = item["content"]
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
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="newsflash-source") as pool:
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

    unique = deduplicate_news_items(all_items)[:120]
    removed = max(0, len(all_items) - len(unique))
    return {
        "updatedAt": int(time.time() * 1000),
        "items": unique,
        "sources": sorted(source_status, key=lambda row: (row["status"] != "ok", row["name"])),
        "sourceCount": sum(1 for row in source_status if row["status"] == "ok"),
        "rawCount": len(all_items),
        "deduplicatedCount": removed,
    }
