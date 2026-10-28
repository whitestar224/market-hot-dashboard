"""做市商实体目录：``厂家 → 各链全部地址``（来自 Arkham，带缓存）。

## 为什么需要它

做市商监控原来的地址簿是人工攒的 77 条、集中在以太坊 —— 一只币在 BSC / Solana 上
有没有做市商，根本无从判断（这两条链没有 Blockscout 标签可读）。

Arkham 的 ``/intelligence/entity/<id>/addresses`` 直接把**一个实体的全部地址按链列出来**：

    wintermute  20 条链 8907 个地址     cumberland  18 条链 25351 个地址
    jump-trading 18 条链 1945 个        gsr-markets 871 个
    amber 626 / b2c2 522 / dwf-labs 398 / flow-traders 1473

数量太大，**不能**拿去轮询（把 4 万个地址排进轮询队列等于把监控打死）。正确用法是
反过来当**索引**：拿到某只币的持币人后，用集合查「这些地址属于哪个厂家」，成本是 O(1)
一次哈希，覆盖面却是全链。

## 用法

    import mm_entity_directory as mdir
    mdir.firm_of("bsc", "0x...")        # → "wintermute" / ""
    mdir.addresses_of("wintermute", "eth")   # → set(小写地址)

缓存 7 天（地址变化慢，但会变）。抓取失败时**沿用旧缓存**并给出 stale 标记，
不会把「接口挂了」表现成「这些厂家一个地址都没有」。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import arkham_client as ARKHAM

#: 厂家（`market_maker_monitor.MM_FIRMS` 的键）→ Arkham 实体 id。
#: 这些 id 是实测出来的：`gsr`/`amber`/`jump` 都不等于厂家键，猜错会直接 404。
FIRM_ENTITIES: dict[str, str] = {
    "wintermute": "wintermute",
    "jump": "jump-trading",
    "gsr": "gsr-markets",
    "dwf": "dwf-labs",
    "amber": "amber",
    "b2c2": "b2c2",
    "cumberland": "cumberland",
    "flow": "flow-traders",
}

CACHE_TTL_SECONDS = 7 * 24 * 3600
_LOCK = threading.Lock()
_CACHE: dict[str, Any] | None = None


def runtime_dir() -> Path:
    import os
    return Path(os.getenv("XINGYUN_RUNTIME_DIR") or (Path(__file__).resolve().parent / ".runtime-cache"))


def cache_path() -> Path:
    return runtime_dir() / "mm_entity_directory.json"


def _read_cache() -> dict[str, Any] | None:
    path = cache_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def refresh(*, firms: dict[str, str] | None = None, timeout: float = 30.0) -> dict[str, Any]:
    """拉一遍全部厂家地址并写缓存。返回 ``{"fetchedAt", "firms": {...}, "errors": [...]}``。"""
    firms = firms or FIRM_ENTITIES
    out: dict[str, Any] = {"fetchedAt": int(time.time() * 1000), "firms": {}, "errors": []}
    for firm, entity_id in firms.items():
        try:
            meta = ARKHAM.arkham_get(f"/intelligence/entity/{entity_id}", timeout=timeout)
            addresses = ARKHAM.arkham_get(
                f"/intelligence/entity/{entity_id}/addresses", timeout=timeout
            ).get("addresses") or {}
            cleaned = {
                str(chain): [str(a).strip() for a in values if str(a).strip()]
                for chain, values in addresses.items()
                if isinstance(values, list)
            }
            out["firms"][firm] = {
                "entityId": entity_id,
                "name": str(meta.get("name") or firm),
                "type": str(meta.get("type") or ""),
                "addresses": cleaned,
            }
        except Exception as exc:  # 单个厂家失败不影响其它
            out["errors"].append(f"{firm}: {type(exc).__name__}: {str(exc)[:120]}")
    return out


def ensure(*, force: bool = False) -> dict[str, Any]:
    """拿到可用的目录（缓存优先，过期才拉）。失败时退回旧缓存并标 ``stale``。"""
    global _CACHE
    with _LOCK:
        if _CACHE is not None and not force:
            age = time.time() - (_CACHE.get("fetchedAt") or 0) / 1000
            if age < CACHE_TTL_SECONDS:
                return _CACHE
        cached = _read_cache()
        if cached and not force:
            age = time.time() - (cached.get("fetchedAt") or 0) / 1000
            if age < CACHE_TTL_SECONDS:
                _CACHE = cached
                return cached
        fresh = refresh()
        if fresh["firms"]:
            fresh["stale"] = bool(fresh["errors"])
            cache_path().parent.mkdir(parents=True, exist_ok=True)
            cache_path().write_text(json.dumps(fresh, ensure_ascii=False), encoding="utf-8")
            _CACHE = fresh
            return fresh
        if cached:
            cached = dict(cached)
            cached["stale"] = True
            _CACHE = cached
            return cached
        _CACHE = fresh
        return fresh


def _chain_index(chain: str, data: dict[str, Any] | None = None) -> dict[str, str]:
    """某条链上 ``地址(小写) → 厂家``。链名两种写法（`eth`/`ethereum`）都认。"""
    data = data or ensure()
    slug = ARKHAM.arkham_chain_slug(chain) or str(chain or "").strip().casefold()
    index: dict[str, str] = {}
    for firm, payload in (data.get("firms") or {}).items():
        for chain_name, values in (payload.get("addresses") or {}).items():
            if (ARKHAM.arkham_chain_slug(chain_name) or chain_name).casefold() != slug:
                continue
            for address in values:
                index.setdefault(str(address).strip().casefold(), firm)
    return index


def firm_of(chain: str, address: Any, *, data: dict[str, Any] | None = None) -> str:
    key = str(address or "").strip().casefold()
    if not key:
        return ""
    return _chain_index(chain, data).get(key, "")


def addresses_of(firm: str, chain: str, *, data: dict[str, Any] | None = None) -> set[str]:
    data = data or ensure()
    payload = (data.get("firms") or {}).get(firm) or {}
    slug = ARKHAM.arkham_chain_slug(chain) or str(chain or "").strip().casefold()
    out: set[str] = set()
    for chain_name, values in (payload.get("addresses") or {}).items():
        if (ARKHAM.arkham_chain_slug(chain_name) or chain_name).casefold() != slug:
            continue
        out.update(str(a).strip().casefold() for a in values)
    return out


def coverage(data: dict[str, Any] | None = None) -> dict[str, dict[str, int]]:
    """``{厂家: {链: 地址数}}``，给报告和页面用。"""
    data = data or ensure()
    return {
        firm: {chain: len(values) for chain, values in (payload.get("addresses") or {}).items()}
        for firm, payload in (data.get("firms") or {}).items()
    }
