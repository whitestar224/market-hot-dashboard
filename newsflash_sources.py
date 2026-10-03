from __future__ import annotations

import hashlib
import html
import json
import os
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
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


def _fetch_feed(source: dict[str, Any], headers: dict[str, str], timeout: float) -> list[dict[str, Any]]:
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
