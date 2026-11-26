"""SoSoValue 融资项目详情交叉补全（RootData 详情页的兜底源）。

背景（2026-11-08）：RootData 详情页在腾讯云 WAF 后面，抓密了会按出口 IP 封全站；
但融资事件卡要补「详情/标签/生态/成立时间」四个字段。用户提供 SoSoValue 做交叉——
SoSoValue 详情页是 Next.js 服务端预渲染（SSP），``curl_cffi`` 能稳定过 Cloudflare，
没有 RootData 那种「抓密封 IP」的滑块封禁。

关键发现（实测）：
- 融资榜 ``/zh/assets/fundraising`` 用 ``curl_cffi`` + ``impersonate="chrome110"`` 能拿到
  ``__NEXT_DATA__``，内含 ``projectName -> projectId`` 映射（**chrome120 反而 403**）。
- 详情页 ``/zh/project/<slug>-<id>`` 的 ``__NEXT_DATA__.projectInfo`` 有完整四字段：
  ``projectIntroduction``(简介) / ``projectTags``(标签) / ``projectEcosystemList``(生态) /
  ``establishmentDate``(成立时间，毫秒时间戳)。
- 融资榜第 1 页（100 条最新事件）就能覆盖 RootData 融资列表的绝大多数项目。

这里只做「按项目名解析 SoSoValue 详情」这一件事，不碰网络重活：失败静默返回 None，
让调用方（rootdata_projects._enrich_fundraising_details）自己决定要不要回落/留空。
"""
from __future__ import annotations
from app.core.paths import runtime_dir

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from curl_cffi import requests as curl_requests  # 能模拟 Chrome TLS 指纹过 Cloudflare
except Exception:  # pragma: no cover - 环境缺 curl_cffi 时整体降级为不可用
    curl_requests = None

from bs4 import BeautifulSoup

PERSIST_CACHE_DIR = runtime_dir()

#: 融资榜第一页（最新 100 条）足够覆盖 RootData 融资列表的绝大多数项目。
FUNDRAISING_URL = "https://sosovalue.com/zh/assets/fundraising"
DETAIL_URL_TEMPLATE = "https://sosovalue.com/zh/project/{slug}-{project_id}"

#: 代理候选：Clash 7890（系统代理）优先，可由环境变量覆盖。
_DEFAULT_PROXIES = [
    "http://127.0.0.1:7890",
    "http://127.0.0.1:7897",
]
_SOSO_PROXY = os.getenv("SOSOVALUE_PROXY", "").strip()

#: name -> projectId 映射的进程内缓存 + 落盘快照，避免每次刷新都重抓融资榜。
_NAME_ID_SNAPSHOT = PERSIST_CACHE_DIR / "sosovalue_name_id.json"
_NAME_ID_TTL_SECONDS = max(600, int(float(os.getenv("SOSOVALUE_NAME_ID_TTL", "3600") or 3600)))

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_name_id_cache: dict[str, str] | None = None
_name_id_loaded_at = 0.0


def _proxies() -> dict[str, str]:
    proxy = _SOSO_PROXY or _DEFAULT_PROXIES[0]
    return {"http": proxy, "https": proxy}


def _fetch(url: str, timeout: float = 30.0) -> str:
    """用 curl_cffi + chrome110 指纹抓页面；任何失败抛异常（调用方决定怎么降级）。"""
    if curl_requests is None:
        raise RuntimeError("curl_cffi 不可用")
    response = curl_requests.get(
        url, headers=_HEADERS, proxies=_proxies(), impersonate="chrome110", timeout=timeout
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    if "Just a moment" in response.text:  # Cloudflare 挑战页
        raise RuntimeError("Cloudflare 挑战页")
    return response.text


def _extract_next_data(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    node = soup.find("script", id="__NEXT_DATA__")
    if node is None or not node.string:
        raise RuntimeError("页面无 __NEXT_DATA__")
    return json.loads(node.string)


def _load_name_id_map(force: bool = False) -> dict[str, str]:
    """加载 name(casefold) -> projectId 映射。优先内存缓存，其次落盘快照，最后抓榜单。"""
    global _name_id_cache, _name_id_loaded_at
    if _name_id_cache is not None and not force and time.time() - _name_id_loaded_at < _NAME_ID_TTL_SECONDS:
        return _name_id_cache

    # 落盘快照（够新就直接用，别每次刷新都打融资榜）。
    if not force:
        try:
            payload = json.loads(_NAME_ID_SNAPSHOT.read_text(encoding="utf-8"))
            if time.time() - float(payload.get("fetchedAtMs") or 0) / 1000 < _NAME_ID_TTL_SECONDS:
                _name_id_cache = {str(k): str(v) for k, v in (payload.get("map") or {}).items()}
                _name_id_loaded_at = float(payload.get("fetchedAtMs") or 0) / 1000
                return _name_id_cache
        except (OSError, ValueError):
            pass

    html = _fetch(FUNDRAISING_URL)
    data = _extract_next_data(html)
    home = data["props"]["pageProps"]["homeListData"]
    mapping: dict[str, str] = {}
    for item in home.get("list") or []:
        name = (item.get("projectName") or "").strip()
        pid = (item.get("projectId") or "").strip()
        if name and pid:
            mapping.setdefault(name.casefold(), pid)

    _name_id_cache = mapping
    _name_id_loaded_at = time.time()
    try:
        _NAME_ID_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        tmp = _NAME_ID_SNAPSHOT.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"fetchedAtMs": int(time.time() * 1000), "map": mapping}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(_NAME_ID_SNAPSHOT)
    except OSError:
        pass
    return mapping


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return slug


def _establishment_year(ms: Any) -> str:
    """把毫秒时间戳转成「2024」这样的年份；非法/缺失返回空串。"""
    try:
        value = int(float(ms))
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y")
    except (OverflowError, OSError, ValueError):
        return ""


def fetch_project_detail(project_id: str) -> dict[str, Any]:
    """抓单个项目详情页，返回 {intro, tags, ecosystem, founded}。失败抛异常。"""
    # slug 不是强约束（详情页靠 id 定位），用一个占位 slug 也够；但尽量从映射里带真名。
    # 这里接受调用方直接给 id，slug 用通用占位 —— 实测 id 对了就能拿到页面。
    return _fetch_detail_by_id(project_id)


def _fetch_detail_by_id(project_id: str) -> dict[str, Any]:
    url = f"https://sosovalue.com/zh/project/x-{project_id}"
    html = _fetch(url)
    data = _extract_next_data(html)
    info = data["props"]["pageProps"].get("projectInfo") or {}
    return {
        "intro": (info.get("projectIntroduction") or "").strip(),
        "tags": [str(t).strip() for t in (info.get("projectTags") or []) if str(t).strip()],
        "ecosystem": [str(e).strip() for e in (info.get("projectEcosystemList") or []) if str(e).strip()],
        "founded": _establishment_year(info.get("establishmentDate")),
    }


def resolve_detail_by_name(name: str) -> dict[str, Any] | None:
    """按项目名解析 SoSoValue 详情四字段；找不到/抓不到返回 None（绝不抛）。"""
    if not name:
        return None
    try:
        mapping = _load_name_id_map()
    except Exception:
        return None
    project_id = mapping.get(name.strip().casefold())
    if not project_id:
        return None
    try:
        return _fetch_detail_by_id(project_id)
    except Exception:
        return None


def name_id_map_size() -> int:
    """诊断用：当前缓存的 name->id 映射条数。"""
    try:
        return len(_load_name_id_map())
    except Exception:
        return 0
