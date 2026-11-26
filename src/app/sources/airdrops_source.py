"""空投猎手数据源（airdrops.io，替代抓不到的 CryptoRank Drophunting）。

背景（2026-11-08）：用户想把 CryptoRank 的「Drophunting 空投猎手」概览（新活动 /
已确认 / 待检查 / 平均奖励）搬进项目，替换首页 summary 的「成交/交易龙头」卡片。
但 CryptoRank 网页与 API 全被 Cloudflare 拦（curl/curl_cffi 各种指纹 + 浏览器级抓取
一律 403），公开 API v3 还需付费 key —— 数据拿不到。用户拍板「用其它空投源替代」。

选定 ``airdrops.io``：自 2017 年起人工核验空投，首页 SSR 直接给出 30 条最新空投，
每条带状态标签（New / Updated / Confirmed / Claim live）+ 项目名 + 链接，
``curl_cffi`` + ``impersonate="chrome110"`` 能稳定过 Cloudflare（200）。
这里只做「抓首页 → 统计各状态数量 → 结构化返回」这一件事，失败静默降级，绝不让
空投抓取拖垮整页。

字段语义（对应 CryptoRank 的「当前活动概述」四个维度，尽量对齐）：
- ``new``        → 本周新增的空投活动（CryptoRank 的「新活动」）
- ``confirmed``  → 已确认可领取（Confirmed + Claim live，对应「奖励已确认」）
- ``claim_live`` → 领取页面已开放（对应「待检查的奖励 / 可领取」）
- ``updated``    → 有更新、值得回看的活动
- ``total``      → 首页抓到的空投总数（30）
"""
from __future__ import annotations
from app.core.paths import runtime_dir

import json
import os
import time
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

PERSIST_CACHE_DIR = runtime_dir()

AIRDROP_URL = "https://airdrops.io/"
AIRDROP_SNAPSHOT = PERSIST_CACHE_DIR / "airdrops_overview.json"
#: 空投概览缓存多久（秒）。空投列表一天才更新几次，缓存 30 分钟足够，别每页刷新都打上游。
AIRDROP_TTL_SECONDS = max(300, int(float(os.getenv("AIRDROP_TTL_SECONDS", "1800") or 1800)))

#: 状态标签 → 归一化字段名。
_STATUS_MAP = {
    "new": "new",
    "confirmed": "confirmed",
    "claim live": "claim_live",
    "claimable": "claim_live",
    "updated": "updated",
}

_proxy = os.getenv("AIRDROP_PROXY", "http://127.0.0.1:7890").strip() or None


def _status_key(tag: str) -> str:
    return _STATUS_MAP.get(tag.strip().lower(), "other")


def parse_airdrops_html(html: str) -> list[dict[str, Any]]:
    """解析 airdrops.io 首页的 30 条空投，返回 ``[{name, status, url}]``。"""
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    for node in soup.select("li.adio-wire__item"):
        tag_node = node.select_one("span.adio-wire__tag")
        link_node = node.select_one("a.adio-wire__link")
        name = link_node.get_text(" ", strip=True) if link_node else ""
        if not name:
            continue
        items.append({
            "name": name,
            "status": tag_node.get_text(strip=True) if tag_node else "",
            "url": link_node.get("href") if link_node else "",
        })
    return items


def _fetch_html() -> str:
    try:
        from curl_cffi import requests as curl_requests
    except Exception as exc:  # 缺 curl_cffi 只影响空投卡，别带崩整页
        raise RuntimeError("缺少 curl_cffi，无法抓取空投数据") from exc
    kwargs: dict[str, Any] = {"impersonate": "chrome110", "timeout": 15.0}
    if _proxy:
        kwargs["proxies"] = {"http": _proxy, "https": _proxy}
    else:
        kwargs["proxies"] = {"http": "", "https": ""}
    response = curl_requests.get(AIRDROP_URL, **kwargs)
    if int(response.status_code) != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    if "Just a moment" in response.text:
        raise RuntimeError("Cloudflare 挑战页")
    return response.text


def _overview_from_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"new": 0, "confirmed": 0, "claim_live": 0, "updated": 0, "other": 0}
    for item in items:
        key = _status_key(item.get("status") or "")
        counts[key] = counts.get(key, 0) + 1
    return {
        "total": len(items),
        "new": counts["new"],
        # 「已确认」= 明确确认的 + 已开放领取的（两者都是确定性奖励）。
        "confirmed": counts["confirmed"] + counts["claim_live"],
        "claim_live": counts["claim_live"],
        "updated": counts["updated"],
        "updatedAt": int(time.time() * 1000),
    }


def fetch_airdrops_overview(*, force: bool = False) -> dict[str, Any]:
    """返回空投概览；优先落盘缓存，过期或 force 才重抓。失败时退缓存、退空态。"""
    if not force:
        try:
            payload = json.loads(AIRDROP_SNAPSHOT.read_text(encoding="utf-8"))
            if time.time() - float(payload.get("updatedAt") or 0) / 1000 < AIRDROP_TTL_SECONDS:
                return payload
        except (OSError, ValueError):
            pass

    try:
        items = parse_airdrops_html(_fetch_html())
        overview = _overview_from_items(items)
        overview["items"] = items[:30]
        try:
            AIRDROP_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
            tmp = AIRDROP_SNAPSHOT.with_suffix(".tmp")
            tmp.write_text(json.dumps(overview, ensure_ascii=False), encoding="utf-8")
            tmp.replace(AIRDROP_SNAPSHOT)
        except OSError:
            pass
        return overview
    except Exception:
        # 抓取失败：退落盘缓存（标注 stale），再退空态。
        try:
            cached = json.loads(AIRDROP_SNAPSHOT.read_text(encoding="utf-8"))
            cached["stale"] = True
            return cached
        except (OSError, ValueError):
            return {"total": 0, "new": 0, "confirmed": 0, "claim_live": 0, "updated": 0,
                    "updatedAt": 0, "stale": True, "error": "空投数据源暂时不可用"}
