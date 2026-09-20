"""Bounded Binance Web3 Agent integrations used by the dashboard.

The launch feed is public and read-only.  The wallet bridge deliberately
exposes status and quote operations only: it cannot sign, broadcast, send,
approve, place an order, or make an x402 payment.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Iterable, Mapping

import requests


MEME_RUSH_PATH = "/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/rank/list/ai"
MEME_RUSH_HOSTS = ("https://web3.binance.com", "https://www.binance.com")
MEME_RUSH_CHAIN_IDS = {"bsc": "56", "solana": "CT_501", "base": "8453"}
MEME_RUSH_ROUTE_CHAINS = {"bsc": "bsc", "solana": "sol", "base": "base"}
MEME_RUSH_STAGES = {10: "new", 20: "finalizing", 30: "migrated"}
MEME_RUSH_PROTOCOLS = {
    1001: "pump-fun", 1002: "moonit", 1003: "pump-amm", 1004: "launch-lab",
    1005: "raydium-v4", 1006: "raydium-cpmm", 1007: "raydium-clmm",
    1008: "bonk", 1009: "dynamic-bc", 1010: "moonshot", 1011: "jup-studio",
    1012: "bags", 1013: "believer", 1014: "meteora-damm-v2",
    1015: "meteora-pools", 1016: "orca", 2001: "four-meme", 2002: "flap",
}
PUBLIC_HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "identity",
    "Content-Type": "application/json",
    "User-Agent": "binance-web3/2.0 (Xingyun Market Dashboard)",
}


def _network_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "56": "bsc", "bnb": "bsc", "bnb-chain": "bsc",
        "ct_501": "solana", "sol": "solana",
        "8453": "base",
    }
    return aliases.get(text, text)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else None


def _integer(value: Any) -> int:
    parsed = _number(value)
    return int(parsed) if parsed is not None else 0


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def configured_meme_rush_stages() -> tuple[int, ...]:
    """Return the bounded lifecycle stages; migrated launches are the default."""
    raw = str(os.environ.get("BINANCE_MEME_RUSH_STAGES") or "30").strip()
    stages: list[int] = []
    for value in re.split(r"[,;\s]+", raw):
        try:
            stage = int(value)
        except (TypeError, ValueError):
            continue
        if stage in MEME_RUSH_STAGES and stage not in stages:
            stages.append(stage)
    return tuple(stages or [30])


def fetch_binance_meme_rush(
    network: str,
    *,
    rank_types: Iterable[int] | None = None,
    limit: int = 80,
    session: Any = None,
) -> dict[str, Any]:
    """Fetch public Binance launchpad rows without credentials or paid APIs."""
    network_key = _network_key(network)
    chain_id = MEME_RUSH_CHAIN_IDS.get(network_key)
    if not chain_id:
        return {"data": [], "network": network_key, "unsupported": True}
    stages_list: list[int] = []
    for raw_stage in rank_types or configured_meme_rush_stages():
        try:
            stage = int(raw_stage)
        except (TypeError, ValueError):
            continue
        if stage in MEME_RUSH_STAGES and stage not in stages_list:
            stages_list.append(stage)
    stages = tuple(stages_list or [30])
    client = session or requests
    combined: list[dict[str, Any]] = []
    errors: list[str] = []
    for stage in stages:
        body = {"chainId": chain_id, "rankType": stage, "limit": max(1, min(200, int(limit)))}
        response_payload: Mapping[str, Any] | None = None
        for host in MEME_RUSH_HOSTS:
            try:
                response = client.post(
                    host + MEME_RUSH_PATH,
                    headers=dict(PUBLIC_HEADERS),
                    json=body,
                    timeout=(2.5, 5.5),
                    allow_redirects=False,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, Mapping) or str(payload.get("code") or "000000") != "000000":
                    raise ValueError(f"business code {payload.get('code') if isinstance(payload, Mapping) else 'invalid'}")
                response_payload = payload
                break
            except (requests.RequestException, TimeoutError, ValueError) as exc:
                errors.append(f"{host}:{stage}:{str(exc)[:120]}")
        if response_payload is None:
            continue
        rows = response_payload.get("data")
        if isinstance(rows, Mapping):
            rows = rows.get("list") or rows.get("rows") or []
        for raw in rows if isinstance(rows, list) else []:
            if isinstance(raw, Mapping):
                combined.append({**dict(raw), "_rankType": stage})
    if not combined and errors:
        raise RuntimeError("; ".join(errors[-2:]))
    return {"data": combined, "network": network_key, "errors": errors}


def normalize_binance_meme_rush(
    payload: Any,
    network: str,
    *,
    observed_at: int,
) -> list[dict[str, Any]]:
    """Map Binance launch rows into the existing deterministic research schema."""
    network_key = _network_key(network)
    if network_key not in MEME_RUSH_CHAIN_IDS:
        return []
    raw_rows = payload.get("data") if isinstance(payload, Mapping) else payload
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_rows if isinstance(raw_rows, list) else []:
        if not isinstance(raw, Mapping):
            continue
        contract = str(raw.get("contractAddress") or "").strip()
        identity = contract.lower() if contract.lower().startswith("0x") else contract
        if not identity or identity in seen:
            continue
        seen.add(identity)
        stage_code = _integer(raw.get("_rankType"))
        protocol_code = _integer(raw.get("protocol"))
        socials = raw.get("socials") if isinstance(raw.get("socials"), Mapping) else {}
        narrative = raw.get("narrativeText") if isinstance(raw.get("narrativeText"), Mapping) else {}
        route_chain = MEME_RUSH_ROUTE_CHAINS[network_key]
        created_at = _integer(raw.get("createTime"))
        migrated_at = _integer(raw.get("migrateTime"))
        rows.append({
            "network": network_key,
            "provider": "binance-meme-rush",
            "providers": ["binance-meme-rush"],
            "contractAddress": contract,
            "poolAddress": "",
            "dexId": "",
            "launchpad": MEME_RUSH_PROTOCOLS.get(protocol_code, f"protocol-{protocol_code}" if protocol_code else ""),
            "launchStage": MEME_RUSH_STAGES.get(stage_code, "new"),
            "symbol": str(raw.get("symbol") or "").strip().upper()[:40],
            "name": str(raw.get("name") or raw.get("symbol") or contract).strip()[:160],
            "firstSeenAt": int(observed_at),
            "observedAt": int(observed_at),
            "poolCreatedAt": created_at or migrated_at,
            "tradeUrl": f"https://web3.binance.com/zh-CN/token/{route_chain}/{contract}",
            "narrativeContext": {
                "description": str(narrative.get("cn") or narrative.get("en") or raw.get("description") or "")[:1200],
                "websites": [str(socials.get("website"))[:800]] if socials.get("website") else [],
                "socials": [str(value)[:800] for value in (socials.get("twitter"), socials.get("telegram")) if value],
            },
            "launchFacts": {
                "stage": MEME_RUSH_STAGES.get(stage_code, "new"),
                "progressPercent": _number(raw.get("progress")),
                "holders": _integer(raw.get("holders")),
                "devSellPercent": _number(raw.get("devSellPercent")),
                "top10Percent": _number(raw.get("holdersTop10Percent")),
                "sniperPercent": _number(raw.get("holdersSniperPercent")),
                "insiderPercent": _number(raw.get("holdersInsiderPercent")),
                "smartMoneyHolders": _integer(raw.get("smartMoneyHolders")),
                "smartMoneyHoldingPercent": _number(raw.get("smartMoneyHoldingPercent")),
                "proHolders": _integer(raw.get("proHolders")),
                "proHoldingPercent": _number(raw.get("proHoldingPercent")),
                "kolHolders": _integer(raw.get("kolHolders")),
                "kolHoldingPercent": _number(raw.get("kolHoldingPercent")),
                "newWalletHoldingPercent": _number(raw.get("newWalletHoldingPercent")),
                "bundlerHolders": _integer(raw.get("bundlerHolders")),
                "bundlerHoldingPercent": _number(raw.get("bundlerHoldingPercent")),
                "binanceHolders": _integer(raw.get("bnHolders")),
                "binanceHoldingPercent": _number(raw.get("bnHoldingPercent")),
                "washTrading": _truthy(raw.get("tagDevWashTrading")) or _truthy(raw.get("tagInsiderWashTrading")),
                "taxRateBuy": _number(raw.get("taxRateBuy") or raw.get("taxRate")),
                "taxRateSell": _number(raw.get("taxRateSell") or raw.get("taxRate")),
            },
            "metrics": {
                "priceUsd": _number(raw.get("price")),
                "liquidityUsd": _number(raw.get("liquidity")),
                "fdvUsd": _number(raw.get("marketCap")),
                "marketCapUsd": _number(raw.get("marketCap")),
                "volumeH24Usd": _number(raw.get("volume")),
                "priceChangeH24": _number(raw.get("priceChange")),
                "transactionsH24": _integer(raw.get("count")),
                "buysH24": _integer(raw.get("countBuy")),
                "sellsH24": _integer(raw.get("countSell")),
                "holders": _integer(raw.get("holders")),
                "top10HolderPercent": _number(raw.get("holdersTop10Percent")),
                "smartMoneyHolders": _integer(raw.get("smartMoneyHolders")),
                "smartMoneyHoldingPercent": _number(raw.get("smartMoneyHoldingPercent")),
                "proHolders": _integer(raw.get("proHolders")),
                "proHoldingPercent": _number(raw.get("proHoldingPercent")),
                "kolHolders": _integer(raw.get("kolHolders")),
                "kolHoldingPercent": _number(raw.get("kolHoldingPercent")),
                "newWalletHoldingPercent": _number(raw.get("newWalletHoldingPercent")),
                "bundlerHolders": _integer(raw.get("bundlerHolders")),
                "bundlerHoldingPercent": _number(raw.get("bundlerHoldingPercent")),
                "washTrading": _truthy(raw.get("tagDevWashTrading")) or _truthy(raw.get("tagInsiderWashTrading")),
            },
        })
    return rows


class AgenticWalletReadBridge:
    """Read/quote-only wrapper around the official Agentic Wallet CLI."""

    def __init__(self, *, runner: Any = None):
        self.runner = runner or subprocess.run

    @staticmethod
    def command_prefix() -> list[str]:
        node = shutil.which("node")
        appdata = os.environ.get("APPDATA")
        installed = (
            Path(appdata) / "npm" / "node_modules" / "@binance" / "agentic-wallet" / "dist" / "index.js"
            if appdata else None
        )
        if node and installed and installed.is_file():
            return [node, str(installed)]
        baw = shutil.which("baw")
        if baw and not baw.lower().endswith((".cmd", ".bat", ".ps1")):
            return [baw]
        return []

    def _run_json(self, arguments: list[str], *, timeout: float = 6.0) -> dict[str, Any]:
        prefix = self.command_prefix()
        if not prefix:
            raise RuntimeError("Binance Agentic Wallet CLI 未安装")
        completed = self.runner(
            [*prefix, *arguments, "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
        stdout = str(getattr(completed, "stdout", "") or "").strip()
        if int(getattr(completed, "returncode", 1) or 0) != 0:
            raise RuntimeError("Binance Agentic Wallet 暂不可用")
        try:
            payload = json.loads(stdout)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Binance Agentic Wallet 返回格式无效") from exc
        if not isinstance(payload, dict) or payload.get("success") is False:
            raise RuntimeError("Binance Agentic Wallet 请求失败")
        return payload

    def status(self) -> dict[str, Any]:
        if not self.command_prefix():
            return {"installed": False, "connected": False, "status": "NOT_INSTALLED"}
        try:
            payload = self._run_json(["wallet", "status"], timeout=4.0)
            data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
            status = str(data.get("status") or "UNKNOWN").upper()
            return {"installed": True, "connected": status == "CONNECTED", "status": status}
        except Exception as exc:
            return {"installed": True, "connected": False, "status": "UNAVAILABLE", "reason": str(exc)[:160]}

    def market_quote(
        self,
        *,
        chain_id: Any,
        from_token: Any,
        to_token: Any,
        amount: Any,
        slippage: Any = "auto",
    ) -> dict[str, Any]:
        chain = str(chain_id or "").strip()
        if chain not in {"1", "56", "8453", "CT_501"}:
            raise ValueError("该网络暂未开放 Agentic Wallet 报价")
        tokens = [str(from_token or "").strip(), str(to_token or "").strip()]
        if any(not token or len(token) > 128 or re.search(r"\s|[\x00-\x1f]", token) for token in tokens):
            raise ValueError("代币地址无效")
        try:
            quantity = Decimal(str(amount))
        except (InvalidOperation, ValueError):
            raise ValueError("买入数量无效") from None
        if not quantity.is_finite() or quantity <= 0:
            raise ValueError("买入数量必须大于 0")
        slip = str(slippage or "auto").strip().lower()
        if slip != "auto":
            try:
                slip_value = Decimal(slip)
            except InvalidOperation:
                raise ValueError("滑点参数无效") from None
            if not slip_value.is_finite() or slip_value < 0 or slip_value > 100:
                raise ValueError("滑点参数无效")
        return self._run_json([
            "market-order", "quote",
            "--fromTokenQty", format(quantity, "f"),
            "--fromToken", tokens[0],
            "--toToken", tokens[1],
            "--binanceChainId", chain,
            "--slippage", slip,
        ])


_WALLET_STATUS_LOCK = threading.Lock()
_WALLET_STATUS_CACHE: tuple[float, dict[str, Any]] = (0.0, {})


def agentic_wallet_capabilities(*, max_age_seconds: float = 15.0) -> dict[str, Any]:
    global _WALLET_STATUS_CACHE
    with _WALLET_STATUS_LOCK:
        if _WALLET_STATUS_CACHE[1] and time.monotonic() - _WALLET_STATUS_CACHE[0] < max_age_seconds:
            status = dict(_WALLET_STATUS_CACHE[1])
        else:
            status = AgenticWalletReadBridge().status()
            _WALLET_STATUS_CACHE = (time.monotonic(), dict(status))
    return {
        **status,
        "readOnlyEnabled": bool(status.get("installed")),
        "quoteEnabled": bool(status.get("connected")),
        "executionEnabled": False,
        "executionRequiresUserConfirmation": True,
        "paidX402Enabled": False,
        "supportsCrossChain": False,
        "sameChainIds": [1, 56, 8453, "CT_501"],
        "note": "当前仅接入状态与报价；真实签名、下单、转账和付费请求不会自动执行",
    }
