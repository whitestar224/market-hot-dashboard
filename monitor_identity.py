"""Read-only asset preflight at monitor intake. Never reads wallets or creates orders."""
from __future__ import annotations

from contextlib import closing
import copy
import json
import math
from pathlib import Path
import queue
import sqlite3
import threading
import time
from urllib.parse import urlparse

from monitor_buy import address, chain_id, SOLANA


def symbol_key(value):
    symbol = str(value or "").strip().upper()
    if len(symbol) > 4 and symbol.endswith("USDT"):
        symbol = symbol[:-4]
    return symbol.removesuffix("/USD").strip(" /-")


def target_identity(row):
    if row.get("kind") == "nft":
        raise ValueError("NFT 不适用代币买入")
    chains = [row.get(key) for key in ("onchainChain", "onchain_chain", "network", "chain", "chainId") if row.get(key)]
    contracts = [row.get(key) for key in ("contractAddress", "onchainContractAddress", "onchain_contract_address", "tokenAddress", "address") if row.get(key)]
    links = [row.get(key) for key in ("tradeUrl", "url", "officialUrl")]
    links += list((row.get("tradeUrls") or {}).values()) if isinstance(row.get("tradeUrls"), dict) else []
    for link in links:
        parsed = urlparse(str(link or ""))
        parts = parsed.path.strip("/").split("/")
        if parsed.scheme == "https" and parsed.hostname in {"web3.binance.com", "web3.okx.com"} and "token" in parts:
            offset = parts.index("token")
            if len(parts) > offset + 2:
                chains.append(parts[offset + 1]); contracts.append(parts[offset + 2])
    if not chains or not contracts:
        raise ValueError("监控来源缺少明确的链或 CA，等待补充来源，不按币名猜测")
    keys = {chain_id(str(value).removeprefix("ct_")) for value in chains}
    if len(keys) != 1 or min(keys) <= 0:
        raise ValueError("来源中的公链信息冲突，需要重新核实")
    key = keys.pop()
    addresses = {address(value, key) for value in contracts}
    if len(addresses) != 1:
        raise ValueError("来源中的 CA 信息冲突，需要重新核实")
    contract = addresses.pop()
    return {"chainId": key, "address": contract, "kind": "token"}


class MonitorIdentityRegistry:
    TTL = 24 * 60 * 60
    REFRESH = 12 * 60 * 60
    RETRY = 60

    def __init__(self, path, resolver, capacity=256):
        self.path = Path(path)
        self.resolver = resolver
        self.lock = threading.RLock()
        self.records = {}
        # Keep foreground identities independent from the large persisted
        # monitor backlog. Values are queue priorities: 0 = visible now,
        # 10 = background intake.
        self.pending = {}
        self.jobs = queue.Queue(maxsize=capacity)
        self.urgent_jobs = queue.Queue(maxsize=max(16, min(64, capacity)))
        self.stopped = threading.Event()
        self.running = False
        self.workers = []

    def start(self):
        with self.lock:
            if self.running:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=3)) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("CREATE TABLE IF NOT EXISTS identities (key TEXT PRIMARY KEY, data TEXT NOT NULL)")
                conn.commit()
                for key, data in conn.execute("SELECT key, data FROM identities"):
                    try:
                        record = json.loads(data)
                        if not isinstance(record, dict) or not all(isinstance(record.get(field, 0), (int, float)) and math.isfinite(record.get(field, 0)) for field in ("verifiedAt", "expiresAt", "retryAt")):
                            continue
                        if record.get("status") == "verified" and (self.key(target_identity(record["target"])) != key or type(record["target"].get("decimals")) is not int or not 0 <= record["target"]["decimals"] <= 36):
                            continue
                        self.records[key] = record
                    except (ValueError, TypeError, KeyError):
                        continue  # A damaged record stays unverified; it cannot break monitoring.
            self.running = True
            for index in range(4):
                worker = threading.Thread(target=self._work, name=f"monitor-ca-{index}", daemon=True)
                self.workers.append(worker); worker.start()

    def stop(self):
        self.stopped.set()
        for worker in self.workers:
            worker.join(timeout=1)

    @staticmethod
    def key(target):
        return f"{target['chainId']}:{target['address']}"

    def snapshot(self, row, enqueue=True, *, priority=10, retry_existing=True):
        try:
            target = target_identity(row)
        except (ValueError, TypeError, AttributeError) as exc:
            return {"status": "unresolved", "reason": str(exc), "target": None}
        key = self.key(target)
        now = time.time()
        with self.lock:
            record = copy.deepcopy(self.records.get(key) or {})
            queued_priority = self.pending.get(key)
            needs_check = not record or (retry_existing and now - record.get("verifiedAt", 0) >= self.REFRESH)
            if (enqueue and self.running and needs_check and now >= record.get("retryAt", 0)
                    and (queued_priority is None or priority < queued_priority)):
                target_queue = self.urgent_jobs if priority == 0 else self.jobs
                try:
                    target_queue.put_nowait((priority, key, target))
                    self.pending[key] = priority
                except queue.Full:
                    pass  # Foreground has reserved capacity; either queue remains bounded.
        if record.get("status") == "verified" and now < record.get("expiresAt", 0):
            verified = record["target"]
            expected = symbol_key(row.get("symbol"))
            if expected and expected != symbol_key(verified.get("symbol")):
                return {"status": "conflict", "reason": "来源币名与该链 CA 的元数据不一致，已禁止买入", "target": None}
            return {"status": "verified", "key": key, "verifiedAt": int(record["verifiedAt"] * 1000),
                    "expiresAt": int(record["expiresAt"] * 1000),
                    "provider": verified.get("identityProvider") or "Relay exact chain/address",
                    "target": {**verified, "identityKey": key}, "reason": "纳入监控时已核验链与唯一 CA"}
        return {"status": record.get("status") if record.get("status") in {"retrying", "conflict"} else "pending",
                "key": key, "target": None, "reason": record.get("reason") or "已纳入后台 CA 核验，完成后开放买入"}

    def require(self, payload):
        result = self.snapshot(payload, enqueue=False)
        if result.get("status") != "verified":
            raise ValueError(result["reason"] + "；请在监控页等待核验完成，不会在买入时临时查找 CA")
        target = result["target"]
        if payload.get("identityKey") != result["key"]:
            raise ValueError("监控身份已变化，请刷新标的后重试")
        if "decimals" in payload and payload["decimals"] != target["decimals"]:
            raise ValueError("目标代币精度与监控核验记录不一致")
        return target

    def _verify(self, key, target):
        now = time.time()
        try:
            resolved = self.resolver(target)
            if target_identity(resolved) != target or type(resolved.get("decimals")) is not int or not 0 <= resolved["decimals"] <= 36 or not resolved.get("symbol"):
                raise ValueError("元数据与监控链或 CA 不一致")
            with self.lock:
                previous = self.records.get(key) or {}
            if previous.get("target") and any(previous["target"].get(field) != resolved.get(field) for field in ("chainId", "address", "decimals")):
                record = {"status": "conflict", "reason": "目标元数据发生变化，已停止开放买入", "retryAt": now + self.REFRESH}
            else:
                record = {"status": "verified", "target": resolved, "verifiedAt": now,
                          "expiresAt": now + self.TTL, "retryAt": 0}
        except Exception:
            with self.lock:
                previous = copy.deepcopy(self.records.get(key) or {})
            record = previous if previous.get("expiresAt", 0) > now else {
                "status": "retrying", "reason": "CA 元数据核验暂未通过，后台稍后重试；暂不开放买入"}
            record["retryAt"] = now + self.RETRY
        # Persist before publishing ready, so a restart cannot lose an admitted identity.
        with closing(sqlite3.connect(self.path, timeout=3)) as conn:
            conn.execute("INSERT OR REPLACE INTO identities VALUES (?, ?)", (key, json.dumps(record, ensure_ascii=False)))
            conn.commit()
        with self.lock:
            self.records[key] = record

    def _work(self):
        while not self.stopped.is_set():
            source = self.urgent_jobs
            try:
                priority, key, target = source.get_nowait()
            except queue.Empty:
                source = self.jobs
                try:
                    priority, key, target = source.get(timeout=0.5)
                except queue.Empty:
                    continue
            try:
                with self.lock:
                    if self.pending.get(key) != priority:
                        continue  # A visible request promoted this background job.
                self._verify(key, target)
            except Exception:
                pass  # Store failures never publish a ready identity or kill the worker.
            finally:
                with self.lock:
                    if self.pending.get(key) == priority:
                        self.pending.pop(key, None)
                source.task_done()

    def enrich(self, payload):
        """Copy public monitor rows; no HTTP and no wallet access in the caller."""
        def visit(value, depth=0):
            if depth > 12:
                return value
            if isinstance(value, list):
                return [visit(item, depth + 1) for item in value]
            if not isinstance(value, dict):
                return value
            result = {key: visit(item, depth + 1) for key, item in value.items() if key != "buyIdentity"}
            if value.get("symbol") or any(value.get(key) for key in ("contractAddress", "onchain_contract_address", "tokenAddress")):
                result["buyIdentity"] = self.snapshot(value, priority=0)
            return result
        return visit(payload)

    def observe(self, payload):
        # Observing does not modify authoritative data or start a worker on import.
        if not self.running:
            return
        pending = [(payload, 0)]
        while pending:
            value, depth = pending.pop()
            if depth > 12:
                continue
            if isinstance(value, dict):
                if value.get("symbol") or any(value.get(key) for key in ("contractAddress", "onchain_contract_address", "tokenAddress")):
                    # Background sweeps admit a new CA once, but do not cycle tens
                    # of thousands of old failures. A currently rendered row can
                    # still retry through the reserved foreground queue.
                    self.snapshot(value, priority=10, retry_existing=False)
                pending.extend((item, depth + 1) for key, item in reversed(list(value.items())) if key != "buyIdentity" and isinstance(item, (dict, list)))
            elif isinstance(value, list):
                pending.extend((item, depth + 1) for item in reversed(value) if isinstance(item, (dict, list)))
