"""Read-only smart-money wallet buy monitoring for the local dashboard."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Callable

import requests


DEFAULT_ALERT_THRESHOLD_USD = 10_000.0
DEFAULT_SMART_MONEY_SEEDS = (
    {
        "address": "0x6078ee8a93697c6d67863fcbff77141d9ab358b2",
        "nickname": "Inq",
        "sourceName": "Inq5️⃣连杆提供",
        "sourceUrl": "https://web3.okx.com/portfolio/0x6078ee8a93697c6d67863fcbff77141d9ab358b2/analysis",
        "sourceEvidence": "由 Inq5️⃣连杆提供；OKX 公开钱包标签为 Inq（@inquixit）",
    },
    {
        "address": "0xa0ed47a5dc1017db87d3807ecd95750b3ea0ff85",
        "nickname": "身份待核验 · a0ed…ff85",
        "sourceName": "Inq5️⃣连杆提供",
        "sourceUrl": "",
        "sourceEvidence": "由 Inq5️⃣连杆提供；公开钱包标签未识别",
    },
    {
        "address": "0x24e91130ba7fb21f853d709a1ffb8dee99ba7ba3",
        "nickname": "身份待核验 · 24e9…7ba3",
        "sourceName": "Inq5️⃣连杆提供",
        "sourceUrl": "",
        "sourceEvidence": "由 Inq5️⃣连杆提供；公开钱包标签未识别",
    },
    {
        "address": "0x00fe92e51f487407a49ce7f7fab9eaecada119fb",
        "nickname": "身份待核验 · 00fe…19fb",
        "sourceName": "Inq5️⃣连杆提供",
        "sourceUrl": "",
        "sourceEvidence": "由 Inq5️⃣连杆提供；公开钱包标签未识别",
    },
    {
        "address": "0xa23103777a932eb2a99826e01d48f62f954504f6",
        "nickname": "身份待核验 · a231…04f6",
        "sourceName": "Inq5️⃣连杆提供",
        "sourceUrl": "",
        "sourceEvidence": "由 Inq5️⃣连杆提供；公开钱包标签未识别",
    },
    {
        "address": "0x0a6ebed0155edb4b21d92ad02897a626cd90119e",
        "nickname": "Bonk Guy (Unipcs)",
        "sourceName": "Bonk Guy 公开钱包",
        "sourceUrl": "https://gxnonqlmujmtgczvhvzp.supabase.co/functions/v1/api/v1/traders/unipcs/wallets",
        "sourceEvidence": "Unipcs 公开交易者目录中的当前 EVM 钱包；Robinhood 两种独立代币持仓交叉确认",
    },
    {
        "address": "2heJbC32Tpfcb3nbUb5ER61K11FGZVfVGtVnDm6LDogF",
        "nickname": "Bonk Guy (Unipcs)",
        "sourceName": "Bonk Guy 公开钱包",
        "sourceUrl": "https://gxnonqlmujmtgczvhvzp.supabase.co/functions/v1/api/v1/traders/unipcs/wallets",
        "sourceEvidence": "Unipcs 公开交易者目录中的当前 Solana 钱包，并有历史交易记录",
        "chains": ("solana",),
    },
    {
        "address": "5M8ACGKEXG1ojKDTMH3sMqhTihTgHYMSsZc6W8i7QW3Y",
        "nickname": "Bonk Guy (Unipcs)",
        "sourceName": "Bonk Guy 公开钱包",
        "sourceUrl": "https://www.reddit.com/r/CryptoCurrency/comments/1mh9ct2/ama_i_turned_16k_to_20000000_on_a_single_memecoin/",
        "sourceEvidence": "Bonk Guy 本人公开过的 USELESS 持仓钱包；与当前 Solana 交易钱包分别保留",
        "chains": ("solana",),
    },
)
DEFAULT_SMART_MONEY_SEED_ADDRESSES = tuple(row["address"] for row in DEFAULT_SMART_MONEY_SEEDS)
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
EVM_CHAINS = ("ethereum", "bsc", "base", "robinhood")
SUPPORTED_CHAINS = (*EVM_CHAINS, "solana")
CHAIN_META = {
    "ethereum": {
        "label": "Ethereum", "vm": "evm", "native": "ETH", "dex": "ethereum",
        "explorer": "https://etherscan.io/tx/{}",
        "rpcEnv": "SMART_MONEY_ETHEREUM_RPC_URLS",
        "rpcs": (
            "https://eth.drpc.org", "https://rpc.flashbots.net",
            "https://ethereum-rpc.publicnode.com", "https://rpc.mevblocker.io",
        ),
    },
    "bsc": {
        "label": "BSC", "vm": "evm", "native": "BNB", "dex": "bsc",
        "explorer": "https://bscscan.com/tx/{}",
        "rpcEnv": "SMART_MONEY_BSC_RPC_URLS",
        "rpcs": (
            "https://bsc-rpc.publicnode.com", "https://bsc-dataseed.bnbchain.org",
            "https://bsc-dataseed-public.bnbchain.org",
        ),
    },
    "base": {
        "label": "Base", "vm": "evm", "native": "ETH", "dex": "base",
        "explorer": "https://basescan.org/tx/{}",
        "rpcEnv": "SMART_MONEY_BASE_RPC_URLS",
        "rpcs": ("https://mainnet.base.org", "https://base-rpc.publicnode.com"),
    },
    "robinhood": {
        "label": "Robinhood", "vm": "evm", "native": "ETH", "dex": "robinhood",
        "explorer": "https://robinhoodchain.blockscout.com/tx/{}",
        "rpcEnv": "SMART_MONEY_ROBINHOOD_RPC_URLS",
        "rpcs": ("https://rpc.mainnet.chain.robinhood.com/",),
    },
    "solana": {
        "label": "Solana", "vm": "solana", "native": "SOL", "dex": "solana",
        "explorer": "https://solscan.io/tx/{}",
        "rpcEnv": "SMART_MONEY_SOLANA_RPC_URLS",
        "rpcs": ("https://api.mainnet-beta.solana.com",),
    },
}
CHAIN_ALIASES = {
    "eth": "ethereum", "ethereum": "ethereum", "以太坊": "ethereum",
    "1": "ethereum",
    "bsc": "bsc", "bnb": "bsc", "bnb chain": "bsc", "bnb smart chain": "bsc",
    "币安智能链": "bsc", "56": "bsc",
    "base": "base", "base chain": "base", "8453": "base",
    "sol": "solana", "solana": "solana", "索拉纳": "solana",
    "robinhood": "robinhood", "robinhood chain": "robinhood", "hood chain": "robinhood",
    "4663": "robinhood",
}
EVM_ADDRESS_RE = re.compile(r"(?<![0-9a-fA-F])0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")
SOLANA_ADDRESS_RE = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![1-9A-HJ-NP-Za-km-z])")
SMART_MONEY_RE = re.compile(
    r"(?:聪明钱|聪明钱包|聪明地址|smart[\s_-]*money|smart[\s_-]*wallet|"
    r"smartmoney|高胜率钱包|高胜率地址|盈利钱包|内幕钱包|insider[\s_-]*wallet)",
    re.I,
)
CONTRACT_LABEL_RE = re.compile(
    r"(?:\bca\b|合约(?:地址)?|代币地址|token[\s_-]*address|contract(?:[\s_-]*address)?)\s*[：:#=-]*\s*$",
    re.I,
)
CHAIN_TEXT_PATTERNS = {
    "ethereum": re.compile(r"(?:ethereum|以太坊|\beth\b)", re.I),
    "bsc": re.compile(r"(?:bnb\s*(?:smart\s*)?chain|币安智能链|\bbsc\b|\bbnb\b)", re.I),
    "base": re.compile(r"(?:base\s*chain|\bbase\b)", re.I),
    "robinhood": re.compile(r"(?:robinhood\s*chain|hood\s*chain|robinhood)", re.I),
    "solana": re.compile(r"(?:solana|索拉纳|\bsol\b)", re.I),
}
STABLE_SYMBOLS = {"USDT", "USDC", "USDBC", "DAI", "FDUSD", "USDE", "USD1", "USDO"}
WRAPPED_NATIVE_SYMBOLS = {"WETH", "WBNB", "WSOL"}
SOLANA_STABLE_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
}
EXCLUDED_OPERATION_WORDS = {"approve", "approval", "bridge", "liquidity", "add_liquidity", "remove_liquidity"}


def _now_ms() -> int:
    return int(time.time() * 1000)


def normalize_chain(value: Any) -> str:
    raw = str(value or "").strip().casefold().replace("_", " ").replace("-", " ")
    raw = re.sub(r"\s+", " ", raw)
    return CHAIN_ALIASES.get(raw, "")


def _decode_base58(value: str) -> bytes:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = 0
    for character in value:
        position = alphabet.find(character)
        if position < 0:
            raise ValueError("invalid base58")
        number = number * 58 + position
    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\x00" * (len(value) - len(value.lstrip("1"))) + decoded


def normalize_wallet_address(chain: Any, address: Any) -> str:
    normalized_chain = normalize_chain(chain)
    if not normalized_chain:
        raise ValueError("不支持的钱包链")
    raw = str(address or "").strip()
    if normalized_chain in EVM_CHAINS:
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", raw, flags=re.I):
            raise ValueError("EVM 钱包地址格式无效")
        return "0x" + raw[2:].lower()
    try:
        decoded = _decode_base58(raw)
    except ValueError as exc:
        raise ValueError("Solana 钱包地址格式无效") from exc
    if not 32 <= len(raw) <= 44 or len(decoded) != 32:
        raise ValueError("Solana 钱包地址格式无效")
    return raw


def _mentioned_chains(window: str) -> list[str]:
    return [chain for chain, pattern in CHAIN_TEXT_PATTERNS.items() if pattern.search(window)]


def extract_smart_money_mentions(
    text: Any,
    *,
    source_name: str = "",
    source_kind: str = "text",
    source_url: str = "",
    observed_at: int | None = None,
) -> list[dict[str, Any]]:
    value = str(text or "")
    if not value or not SMART_MONEY_RE.search(value):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def append_match(match: re.Match[str], address_kind: str) -> None:
        address = match.group(0)
        prefix = value[max(0, match.start() - 48):match.start()]
        window = value[max(0, match.start() - 180):min(len(value), match.end() + 180)]
        if not SMART_MONEY_RE.search(window) or CONTRACT_LABEL_RE.search(prefix):
            return
        mentioned = _mentioned_chains(window)
        if address_kind == "evm":
            chains = [chain for chain in mentioned if chain in EVM_CHAINS] or list(EVM_CHAINS)
        else:
            if mentioned and "solana" not in mentioned:
                return
            chains = ["solana"]
        for chain in chains:
            try:
                normalized = normalize_wallet_address(chain, address)
            except ValueError:
                continue
            key = (chain, normalized)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "chain": chain,
                "address": normalized,
                "nickname": "",
                "sourceName": str(source_name or "自动识别")[:120],
                "sourceKind": str(source_kind or "text")[:40],
                "sourceUrl": str(source_url or "")[:800],
                "sourceEvidence": re.sub(r"\s+", " ", window).strip()[:600],
                "observedAt": int(observed_at or _now_ms()),
            })

    for match in EVM_ADDRESS_RE.finditer(value):
        append_match(match, "evm")
    for match in SOLANA_ADDRESS_RE.finditer(value):
        append_match(match, "solana")
    return rows


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    data = dict(row)
    mapping = {
        "source_name": "sourceName", "source_kind": "sourceKind", "source_url": "sourceUrl",
        "source_evidence": "sourceEvidence", "alert_threshold_usd": "alertThresholdUsd",
        "created_at": "createdAt", "updated_at": "updatedAt", "transaction_hash": "transactionHash",
        "wallet_address": "walletAddress", "wallet_nickname": "walletNickname",
        "token_address": "tokenAddress", "token_amount": "tokenAmount", "payment_asset": "paymentAsset",
        "payment_amount": "paymentAmount", "payment_usd": "paymentUsd", "price_usd": "priceUsd",
        "popup_eligible": "popupEligible", "alerted_at": "alertedAt", "observed_at": "observedAt",
        "block_ref": "blockRef", "event_key": "eventKey",
    }
    for source, target in mapping.items():
        if source in data:
            data[target] = data.pop(source)
    if "enabled" in data:
        data["enabled"] = bool(data["enabled"])
    if "popupEligible" in data:
        data["popupEligible"] = bool(data["popupEligible"])
    if "details_json" in data:
        raw = data.pop("details_json")
        try:
            data["details"] = json.loads(raw or "{}")
        except ValueError:
            data["details"] = {}
    if data.get("chain") in CHAIN_META:
        data["chainLabel"] = CHAIN_META[data["chain"]]["label"]
        if data.get("transactionHash"):
            data["transactionUrl"] = CHAIN_META[data["chain"]]["explorer"].format(data["transactionHash"])
    return data


class SmartMoneyStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS wallets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chain TEXT NOT NULL,
                    address TEXT NOT NULL,
                    nickname TEXT NOT NULL DEFAULT '',
                    source_name TEXT NOT NULL DEFAULT '',
                    source_kind TEXT NOT NULL DEFAULT 'manual',
                    source_url TEXT NOT NULL DEFAULT '',
                    source_evidence TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    alert_threshold_usd REAL NOT NULL DEFAULT 10000,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(chain, address)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    chain TEXT NOT NULL,
                    wallet_address TEXT NOT NULL,
                    wallet_nickname TEXT NOT NULL DEFAULT '',
                    transaction_hash TEXT NOT NULL,
                    token_address TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    token_amount REAL NOT NULL DEFAULT 0,
                    payment_asset TEXT NOT NULL DEFAULT '',
                    payment_amount REAL NOT NULL DEFAULT 0,
                    payment_usd REAL NOT NULL DEFAULT 0,
                    price_usd REAL NOT NULL DEFAULT 0,
                    popup_eligible INTEGER NOT NULL DEFAULT 0,
                    alerted_at INTEGER NOT NULL DEFAULT 0,
                    observed_at INTEGER NOT NULL,
                    block_ref TEXT NOT NULL DEFAULT '',
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_smart_money_events_observed
                    ON events(observed_at DESC);
                CREATE TABLE IF NOT EXISTS cursors (
                    cursor_key TEXT PRIMARY KEY,
                    cursor_value TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS health (
                    chain TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'waiting',
                    message TEXT NOT NULL DEFAULT '',
                    cursor_value TEXT NOT NULL DEFAULT '',
                    last_checked_at INTEGER NOT NULL DEFAULT 0,
                    last_ok_at INTEGER NOT NULL DEFAULT 0
                );
            """)
            self.conn.commit()

    def close(self) -> None:
        with self.lock:
            if self.conn is not None:
                self.conn.close()
                self.conn = None

    def upsert_wallet(
        self,
        chain: Any,
        address: Any,
        *,
        nickname: str = "",
        source_name: str = "",
        source_kind: str = "manual",
        source_url: str = "",
        source_evidence: str = "",
        enabled: bool = True,
        alert_threshold_usd: float = DEFAULT_ALERT_THRESHOLD_USD,
        observed_at: int | None = None,
        preserve_controls: bool = False,
    ) -> dict[str, Any]:
        normalized_chain = normalize_chain(chain)
        normalized_address = normalize_wallet_address(normalized_chain, address)
        now = int(observed_at or _now_ms())
        threshold = max(0.0, float(alert_threshold_usd or DEFAULT_ALERT_THRESHOLD_USD))
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO wallets (
                    chain, address, nickname, source_name, source_kind, source_url,
                    source_evidence, enabled, alert_threshold_usd, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chain, address) DO UPDATE SET
                    nickname = CASE WHEN excluded.nickname != '' THEN excluded.nickname ELSE wallets.nickname END,
                    source_name = CASE WHEN excluded.source_name != '' THEN excluded.source_name ELSE wallets.source_name END,
                    source_kind = CASE WHEN excluded.source_name != '' THEN excluded.source_kind ELSE wallets.source_kind END,
                    source_url = CASE WHEN excluded.source_url != '' THEN excluded.source_url ELSE wallets.source_url END,
                    source_evidence = CASE WHEN excluded.source_evidence != '' THEN excluded.source_evidence ELSE wallets.source_evidence END,
                    enabled = CASE WHEN ? = 1 THEN wallets.enabled ELSE excluded.enabled END,
                    alert_threshold_usd = CASE WHEN ? = 1 THEN wallets.alert_threshold_usd ELSE excluded.alert_threshold_usd END,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_chain, normalized_address, str(nickname or "")[:120], str(source_name or "")[:120],
                    str(source_kind or "manual")[:40], str(source_url or "")[:800],
                    re.sub(r"\s+", " ", str(source_evidence or "")).strip()[:1000],
                    1 if enabled else 0, threshold, now, now,
                    1 if preserve_controls else 0, 1 if preserve_controls else 0,
                ),
            )
            self.conn.commit()
            return _row_dict(self.conn.execute(
                "SELECT * FROM wallets WHERE chain = ? AND address = ?",
                (normalized_chain, normalized_address),
            ).fetchone())

    def list_wallets(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE enabled = 1" if enabled_only else ""
        with self.lock:
            rows = self.conn.execute(
                f"SELECT * FROM wallets {where} ORDER BY enabled DESC, updated_at DESC, id DESC"
            ).fetchall()
        return [_row_dict(row) for row in rows]

    def wallet(self, chain: Any, address: Any) -> dict[str, Any]:
        normalized_chain = normalize_chain(chain)
        normalized_address = normalize_wallet_address(normalized_chain, address)
        with self.lock:
            return _row_dict(self.conn.execute(
                "SELECT * FROM wallets WHERE chain = ? AND address = ?",
                (normalized_chain, normalized_address),
            ).fetchone())

    def save_wallet(self, identity: int, *, enabled: bool | None = None, nickname: str | None = None,
                    alert_threshold_usd: float | None = None) -> dict[str, Any]:
        updates: list[str] = []
        params: list[Any] = []
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(1 if enabled else 0)
        if nickname is not None:
            updates.append("nickname = ?")
            params.append(str(nickname or "")[:120])
        if alert_threshold_usd is not None:
            updates.append("alert_threshold_usd = ?")
            params.append(max(0.0, float(alert_threshold_usd)))
        updates.append("updated_at = ?")
        params.append(_now_ms())
        params.append(int(identity))
        with self.lock:
            self.conn.execute(f"UPDATE wallets SET {', '.join(updates)} WHERE id = ?", params)
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM wallets WHERE id = ?", (int(identity),)).fetchone()
        if row is None:
            raise ValueError("聪明钱地址不存在")
        return _row_dict(row)

    def remove_wallet(self, identity: int) -> bool:
        with self.lock:
            cursor = self.conn.execute("DELETE FROM wallets WHERE id = ?", (int(identity),))
            self.conn.commit()
            return bool(cursor.rowcount)

    def seed_wallets(self, rows: list[dict[str, Any]]) -> int:
        """Insert first-party defaults once without overwriting later user choices."""
        now = _now_ms()
        inserted = 0
        with self.lock:
            for row in rows:
                chain = normalize_chain(row.get("chain"))
                address = normalize_wallet_address(chain, row.get("address"))
                cursor = self.conn.execute(
                    """
                    INSERT OR IGNORE INTO wallets (
                        chain, address, nickname, source_name, source_kind, source_url,
                        source_evidence, enabled, alert_threshold_usd, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        chain, address, str(row.get("nickname") or "")[:120],
                        str(row.get("sourceName") or "预置")[:120],
                        str(row.get("sourceKind") or "seed")[:40],
                        str(row.get("sourceUrl") or "")[:800],
                        str(row.get("sourceEvidence") or "")[:1000],
                        max(0.0, float(row.get("alertThresholdUsd") or DEFAULT_ALERT_THRESHOLD_USD)),
                        now, now,
                    ),
                )
                inserted += int(bool(cursor.rowcount))
            self.conn.commit()
        return inserted

    def repair_legacy_seed_labels(self, rows: tuple[dict[str, Any], ...]) -> int:
        """Apply verified per-wallet labels without touching user-edited records."""
        updated = 0
        now = _now_ms()
        with self.lock:
            for row in rows:
                cursor = self.conn.execute(
                    """
                    UPDATE wallets
                    SET nickname = ?, source_name = ?, source_url = ?, source_evidence = ?, updated_at = ?
                    WHERE source_kind = 'seed'
                      AND lower(address) = ?
                      AND (
                          (nickname = ? AND source_name = ?)
                          OR (nickname = ? AND source_name = ?)
                      )
                    """,
                    (
                        row["nickname"], row["sourceName"], row.get("sourceUrl", ""),
                        row["sourceEvidence"], now, row["address"].casefold(),
                        "Inq5️⃣连杆", "Inq5️⃣连杆", "其他聪明钱", "用户预置",
                    ),
                )
                updated += max(0, int(cursor.rowcount or 0))
            self.conn.commit()
        return updated

    def cursor(self, key: str) -> str:
        with self.lock:
            row = self.conn.execute("SELECT cursor_value FROM cursors WHERE cursor_key = ?", (key,)).fetchone()
        return str(row[0]) if row else ""

    def set_cursor(self, key: str, value: Any) -> None:
        now = _now_ms()
        with self.lock:
            self.conn.execute(
                "INSERT INTO cursors(cursor_key, cursor_value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(cursor_key) DO UPDATE SET cursor_value=excluded.cursor_value, updated_at=excluded.updated_at",
                (str(key), str(value), now),
            )
            self.conn.commit()

    def update_health(self, chain: str, status: str, message: str = "", cursor: str = "") -> None:
        now = _now_ms()
        ok_at = now if status in {"ok", "baseline"} else 0
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO health(chain,status,message,cursor_value,last_checked_at,last_ok_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(chain) DO UPDATE SET
                    status=excluded.status, message=excluded.message, cursor_value=excluded.cursor_value,
                    last_checked_at=excluded.last_checked_at,
                    last_ok_at=CASE WHEN excluded.last_ok_at > 0 THEN excluded.last_ok_at ELSE health.last_ok_at END
                """,
                (chain, status, str(message or "")[:300], str(cursor or ""), now, ok_at),
            )
            self.conn.commit()

    def health(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM health ORDER BY chain").fetchall()
        return [_row_dict(row) for row in rows]

    def record_event(self, event: dict[str, Any]) -> dict[str, Any]:
        chain = normalize_chain(event.get("chain"))
        wallet = normalize_wallet_address(chain, event.get("walletAddress"))
        tx_hash = str(event.get("transactionHash") or "").strip()
        token = str(event.get("tokenAddress") or "").strip()
        if not tx_hash or not token:
            raise ValueError("聪明钱买入事件缺少交易哈希或代币地址")
        event_key = str(event.get("eventKey") or hashlib.sha256(
            f"{chain}|{tx_hash.casefold()}|{wallet}|{token.casefold()}".encode("utf-8")
        ).hexdigest())
        observed_at = int(event.get("observedAt") or _now_ms())
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        with self.lock:
            cursor = self.conn.execute(
                """
                INSERT OR IGNORE INTO events (
                    event_key,chain,wallet_address,wallet_nickname,transaction_hash,token_address,symbol,
                    token_amount,payment_asset,payment_amount,payment_usd,price_usd,popup_eligible,
                    alerted_at,observed_at,block_ref,details_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_key, chain, wallet, str(event.get("walletNickname") or "")[:120], tx_hash,
                    token, str(event.get("symbol") or "")[:40], float(event.get("tokenAmount") or 0),
                    str(event.get("paymentAsset") or "")[:40], float(event.get("paymentAmount") or 0),
                    float(event.get("paymentUsd") or 0), float(event.get("priceUsd") or 0),
                    1 if event.get("popupEligible") else 0, 0, observed_at,
                    str(event.get("blockRef") or "")[:100], json.dumps(details, ensure_ascii=False),
                ),
            )
            created = bool(cursor.rowcount)
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM events WHERE event_key = ?", (event_key,)).fetchone()
        return {"created": created, "event": _row_dict(row)}

    def mark_alerted(self, event_key: str, alerted_at: int | None = None) -> None:
        with self.lock:
            self.conn.execute(
                "UPDATE events SET alerted_at = ? WHERE event_key = ? AND alerted_at = 0",
                (int(alerted_at or _now_ms()), event_key),
            )
            self.conn.commit()

    def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM events ORDER BY observed_at DESC, id DESC LIMIT ?",
                (max(1, min(500, int(limit))),),
            ).fetchall()
        return [_row_dict(row) for row in rows]


def _hex_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    raw = str(value or "0")
    try:
        return int(raw, 16) if raw.lower().startswith("0x") else int(raw)
    except (TypeError, ValueError):
        return 0


def _topic_wallet(topic: Any) -> str:
    value = str(topic or "").lower().removeprefix("0x")
    return "0x" + value[-40:] if len(value) >= 40 else ""


def classify_evm_buy(
    chain: Any,
    wallet_address: Any,
    transaction: dict[str, Any],
    receipt: dict[str, Any],
    token_metadata: Callable[[str], dict[str, Any]],
    *,
    native_price_usd: float = 0.0,
    threshold_usd: float = DEFAULT_ALERT_THRESHOLD_USD,
) -> dict[str, Any] | None:
    normalized_chain = normalize_chain(chain)
    wallet = normalize_wallet_address(normalized_chain, wallet_address)
    if _hex_int(receipt.get("status")) != 1:
        return None
    operation = str(transaction.get("operation") or receipt.get("operation") or "").casefold()
    if any(word in operation for word in EXCLUDED_OPERATION_WORDS):
        return None
    net: dict[str, int] = {}
    for log in receipt.get("logs") or []:
        if not isinstance(log, dict):
            continue
        topics = log.get("topics") if isinstance(log.get("topics"), list) else []
        if len(topics) < 3 or str(topics[0]).casefold() != TRANSFER_TOPIC:
            continue
        token = str(log.get("address") or "").lower()
        sender = _topic_wallet(topics[1])
        recipient = _topic_wallet(topics[2])
        amount = _hex_int(log.get("data"))
        if sender == wallet:
            net[token] = net.get(token, 0) - amount
        if recipient == wallet:
            net[token] = net.get(token, 0) + amount
    if not net:
        return None
    metadata: dict[str, dict[str, Any]] = {}
    normalized: dict[str, float] = {}
    for token, raw_amount in net.items():
        meta = token_metadata(token) or {}
        metadata[token] = meta
        decimals = max(0, min(36, int(meta.get("decimals", 18) or 18)))
        normalized[token] = raw_amount / (10 ** decimals)

    stable_spends: list[tuple[str, float, float]] = []
    wrapped_spends: list[tuple[str, float, float]] = []
    targets: list[tuple[str, float, dict[str, Any]]] = []
    for token, amount in normalized.items():
        meta = metadata[token]
        symbol = str(meta.get("symbol") or "").upper()
        price = float(meta.get("priceUsd") or 0)
        stable = bool(meta.get("stable")) or (symbol in STABLE_SYMBOLS and (price == 0 or 0.8 <= price <= 1.2))
        wrapped = symbol in WRAPPED_NATIVE_SYMBOLS
        if amount < 0 and stable:
            stable_spends.append((symbol or "USD", abs(amount), abs(amount) * (price or 1.0)))
        elif amount < 0 and wrapped and native_price_usd > 0:
            wrapped_spends.append((symbol, abs(amount), abs(amount) * native_price_usd))
        elif amount > 0 and not stable and not wrapped:
            targets.append((token, amount, meta))
    native_amount = _hex_int(transaction.get("value")) / 10**18
    payment_options = [*stable_spends, *wrapped_spends]
    if native_amount > 0 and native_price_usd > 0:
        payment_options.append((CHAIN_META[normalized_chain]["native"], native_amount, native_amount * native_price_usd))
    if not targets or not payment_options:
        return None
    payment_asset, payment_amount, payment_usd = max(payment_options, key=lambda row: row[2])
    target_token, target_amount, target_meta = max(
        targets,
        key=lambda row: row[1] * float(row[2].get("priceUsd") or 0) or row[1],
    )
    tx_hash = str(transaction.get("hash") or receipt.get("transactionHash") or "")
    price_usd = payment_usd / target_amount if target_amount > 0 else 0
    return {
        "chain": normalized_chain,
        "walletAddress": wallet,
        "transactionHash": tx_hash,
        "tokenAddress": target_token,
        "symbol": str(target_meta.get("symbol") or "UNKNOWN")[:40],
        "tokenAmount": target_amount,
        "paymentAsset": payment_asset,
        "paymentAmount": payment_amount,
        "paymentUsd": payment_usd,
        "priceUsd": price_usd,
        "popupEligible": payment_usd >= float(threshold_usd),
        "observedAt": _now_ms(),
        "blockRef": str(receipt.get("blockNumber") or transaction.get("blockNumber") or ""),
        "details": {"classification": "net-token-flow", "readOnly": True},
    }


def _solana_token_balances(rows: Any, wallet: str) -> dict[str, float]:
    balances: dict[str, float] = {}
    for row in rows or []:
        if not isinstance(row, dict) or str(row.get("owner") or "") != wallet:
            continue
        amount = row.get("uiTokenAmount") if isinstance(row.get("uiTokenAmount"), dict) else {}
        raw = amount.get("uiAmountString")
        try:
            value = float(raw if raw is not None else amount.get("uiAmount") or 0)
        except (TypeError, ValueError):
            value = 0.0
        mint = str(row.get("mint") or "")
        if mint:
            balances[mint] = balances.get(mint, 0.0) + value
    return balances


def classify_solana_buy(
    wallet_address: Any,
    payload: dict[str, Any],
    token_metadata: Callable[[str], dict[str, Any]],
    *,
    native_price_usd: float = 0.0,
    threshold_usd: float = DEFAULT_ALERT_THRESHOLD_USD,
) -> dict[str, Any] | None:
    wallet = normalize_wallet_address("solana", wallet_address)
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    if not meta or meta.get("err") is not None:
        return None
    operation = str(payload.get("operation") or "").casefold()
    if any(word in operation for word in EXCLUDED_OPERATION_WORDS):
        return None
    before = _solana_token_balances(meta.get("preTokenBalances"), wallet)
    after = _solana_token_balances(meta.get("postTokenBalances"), wallet)
    mints = set(before) | set(after)
    changes = {mint: after.get(mint, 0) - before.get(mint, 0) for mint in mints}
    payment_options: list[tuple[str, float, float]] = []
    targets: list[tuple[str, float, dict[str, Any]]] = []
    for mint, amount in changes.items():
        token_meta = token_metadata(mint) or {}
        symbol = str(token_meta.get("symbol") or SOLANA_STABLE_MINTS.get(mint) or "").upper()
        price = float(token_meta.get("priceUsd") or 0)
        stable = mint in SOLANA_STABLE_MINTS or bool(token_meta.get("stable")) or (
            symbol in STABLE_SYMBOLS and (price == 0 or 0.8 <= price <= 1.2)
        )
        if amount < 0 and stable:
            payment_options.append((symbol or "USD", abs(amount), abs(amount) * (price or 1.0)))
        elif amount > 0 and not stable and symbol not in WRAPPED_NATIVE_SYMBOLS:
            targets.append((mint, amount, token_meta))
    message = (payload.get("transaction") or {}).get("message") or {}
    account_keys = message.get("accountKeys") if isinstance(message, dict) else []
    wallet_index = -1
    for index, key in enumerate(account_keys or []):
        public_key = key.get("pubkey") if isinstance(key, dict) else key
        if str(public_key or "") == wallet:
            wallet_index = index
            break
    pre_balances = meta.get("preBalances") or []
    post_balances = meta.get("postBalances") or []
    native_spend = 0.0
    if 0 <= wallet_index < len(pre_balances) and wallet_index < len(post_balances):
        fee = int(meta.get("fee") or 0) if wallet_index == 0 else 0
        native_spend = max(0, int(pre_balances[wallet_index] or 0) - int(post_balances[wallet_index] or 0) - fee) / 10**9
    if native_spend > 0 and native_price_usd > 0:
        payment_options.append(("SOL", native_spend, native_spend * native_price_usd))
    if not targets or not payment_options:
        return None
    payment_asset, payment_amount, payment_usd = max(payment_options, key=lambda row: row[2])
    target_mint, target_amount, target_meta = max(
        targets,
        key=lambda row: row[1] * float(row[2].get("priceUsd") or 0) or row[1],
    )
    signatures = (payload.get("transaction") or {}).get("signatures") or []
    tx_hash = str(signatures[0] if signatures else payload.get("signature") or "")
    return {
        "chain": "solana", "walletAddress": wallet, "transactionHash": tx_hash,
        "tokenAddress": target_mint, "symbol": str(target_meta.get("symbol") or "UNKNOWN")[:40],
        "tokenAmount": target_amount, "paymentAsset": payment_asset, "paymentAmount": payment_amount,
        "paymentUsd": payment_usd, "priceUsd": payment_usd / target_amount if target_amount else 0,
        "popupEligible": payment_usd >= float(threshold_usd), "observedAt": _now_ms(),
        "blockRef": str(payload.get("slot") or ""),
        "details": {"classification": "solana-balance-delta", "readOnly": True},
    }


class SmartMoneyMonitor:
    def __init__(
        self,
        path: str | Path,
        *,
        request_session: requests.Session | None = None,
        poll_interval_seconds: float | None = None,
        seed_defaults: bool = False,
    ):
        self.store = SmartMoneyStore(path)
        self.session = request_session or requests.Session()
        self.poll_interval_seconds = max(3.0, float(
            poll_interval_seconds or os.getenv("SMART_MONEY_POLL_INTERVAL_SECONDS", "6") or 6
        ))
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.poll_lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.running = False
        self.alert_callback: Callable[[dict[str, Any]], Any] | None = None
        self._metadata_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._native_price_cache: dict[str, tuple[float, float]] = {}
        if seed_defaults:
            self.seed_defaults()

    def seed_defaults(self) -> int:
        self.store.repair_legacy_seed_labels(DEFAULT_SMART_MONEY_SEEDS)
        rows = [
            {
                "chain": chain,
                "address": seed["address"],
                "nickname": seed["nickname"],
                "sourceName": seed["sourceName"],
                "sourceKind": "seed",
                "sourceUrl": seed.get("sourceUrl", ""),
                "sourceEvidence": seed["sourceEvidence"],
                "alertThresholdUsd": DEFAULT_ALERT_THRESHOLD_USD,
            }
            for seed in DEFAULT_SMART_MONEY_SEEDS
            for chain in seed.get("chains", EVM_CHAINS)
        ]
        return self.store.seed_wallets(rows)

    def set_alert_callback(self, callback: Callable[[dict[str, Any]], Any] | None) -> None:
        self.alert_callback = callback

    def add_wallet(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.store.upsert_wallet(
            payload.get("chain"), payload.get("address"), nickname=str(payload.get("nickname") or ""),
            source_name=str(payload.get("sourceName") or "手动添加"), source_kind=str(payload.get("sourceKind") or "manual"),
            source_url=str(payload.get("sourceUrl") or ""), source_evidence=str(payload.get("sourceEvidence") or ""),
            enabled=payload.get("enabled") is not False,
            alert_threshold_usd=float(payload.get("alertThresholdUsd") or DEFAULT_ALERT_THRESHOLD_USD),
        )

    def ingest_text(self, text: Any, **context: Any) -> list[dict[str, Any]]:
        added: list[dict[str, Any]] = []
        for mention in extract_smart_money_mentions(text, **context):
            added.append(self.store.upsert_wallet(
                mention["chain"], mention["address"], nickname=mention.get("nickname") or "",
                source_name=mention.get("sourceName") or "自动识别", source_kind=mention.get("sourceKind") or "text",
                source_url=mention.get("sourceUrl") or "", source_evidence=mention.get("sourceEvidence") or "",
                observed_at=int(mention.get("observedAt") or _now_ms()),
                preserve_controls=True,
            ))
        return added

    def record_buy(self, event: dict[str, Any]) -> dict[str, Any]:
        wallet = self.store.wallet(event.get("chain"), event.get("walletAddress"))
        threshold = float(wallet.get("alertThresholdUsd") or DEFAULT_ALERT_THRESHOLD_USD)
        normalized = {
            **event,
            "walletNickname": event.get("walletNickname") or wallet.get("nickname") or wallet.get("sourceName") or "聪明钱",
            "popupEligible": float(event.get("paymentUsd") or 0) >= threshold,
        }
        result = self.store.record_event(normalized)
        stored = result["event"]
        if result["created"] and stored.get("popupEligible") and self.alert_callback:
            self.alert_callback(stored)
            self.store.mark_alerted(stored["eventKey"])
            stored["alertedAt"] = _now_ms()
        return {"created": result["created"], "event": stored}

    def start(self, *, start_worker: bool = True) -> None:
        with self.lock:
            if self.running:
                return
            self.running = True
            self.stop_event.clear()
            if start_worker:
                self.thread = threading.Thread(target=self._worker, daemon=True, name="smart-money-buy-monitor")
                self.thread.start()

    def stop(self) -> None:
        with self.lock:
            if not self.running:
                return
            self.running = False
            self.stop_event.set()
            thread = self.thread
            self.thread = None
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)

    def close(self) -> None:
        self.stop()
        self.store.close()

    def _worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                pass
            self.stop_event.wait(self.poll_interval_seconds)

    def rpc_urls(self, chain: str) -> list[str]:
        meta = CHAIN_META[chain]
        configured = [value.strip() for value in str(os.getenv(meta["rpcEnv"], "") or "").split(",") if value.strip()]
        return list(dict.fromkeys([*configured, *meta["rpcs"]]))

    def rpc(self, chain: str, method: str, params: list[Any]) -> Any:
        errors: list[str] = []
        for url in self.rpc_urls(chain):
            try:
                response = self.session.post(
                    url,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    timeout=(4, 12),
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("error"):
                    raise RuntimeError(str(payload["error"]))
                return payload.get("result")
            except Exception as exc:
                errors.append(f"{url}: {str(exc)[:120]}")
        raise RuntimeError("；".join(errors) or f"{chain} RPC 不可用")

    def _token_price(self, chain: str, token: str) -> float:
        try:
            response = self.session.get(
                f"https://api.dexscreener.com/tokens/v1/{CHAIN_META[chain]['dex']}/{token}",
                headers={"Accept": "application/json"}, timeout=(4, 8),
            )
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list):
                return 0.0
            best = max((row for row in rows if isinstance(row, dict)), key=lambda row: float((row.get("liquidity") or {}).get("usd") or 0), default={})
            return float(best.get("priceUsd") or 0)
        except Exception:
            return 0.0

    @staticmethod
    def _decode_abi_string(value: Any) -> str:
        raw = str(value or "").removeprefix("0x")
        try:
            data = bytes.fromhex(raw)
        except ValueError:
            return ""
        if len(data) >= 64:
            offset = int.from_bytes(data[:32], "big")
            if offset + 32 <= len(data):
                length = int.from_bytes(data[offset:offset + 32], "big")
                return data[offset + 32:offset + 32 + length].decode("utf-8", errors="ignore").strip("\x00")
        return data[:32].decode("utf-8", errors="ignore").strip("\x00")

    def evm_token_metadata(self, chain: str, token: str) -> dict[str, Any]:
        key = (chain, token.lower())
        cached = self._metadata_cache.get(key)
        if cached and time.time() - cached[0] < 3600:
            return dict(cached[1])
        symbol = ""
        decimals = 18
        try:
            decimals = _hex_int(self.rpc(chain, "eth_call", [{"to": token, "data": "0x313ce567"}, "latest"]))
            symbol = self._decode_abi_string(self.rpc(chain, "eth_call", [{"to": token, "data": "0x95d89b41"}, "latest"]))
        except Exception:
            pass
        price = self._token_price(chain, token)
        result = {
            "symbol": symbol[:40] or "UNKNOWN", "decimals": decimals if 0 <= decimals <= 36 else 18,
            "priceUsd": price, "stable": symbol.upper() in STABLE_SYMBOLS and (price == 0 or 0.8 <= price <= 1.2),
        }
        self._metadata_cache[key] = (time.time(), result)
        return dict(result)

    def solana_token_metadata(self, mint: str) -> dict[str, Any]:
        key = ("solana", mint)
        cached = self._metadata_cache.get(key)
        if cached and time.time() - cached[0] < 3600:
            return dict(cached[1])
        price = self._token_price("solana", mint)
        result = {
            "symbol": SOLANA_STABLE_MINTS.get(mint, "UNKNOWN"), "priceUsd": price,
            "stable": mint in SOLANA_STABLE_MINTS,
        }
        self._metadata_cache[key] = (time.time(), result)
        return dict(result)

    def native_price_usd(self, chain: str) -> float:
        symbol = CHAIN_META[chain]["native"]
        cached = self._native_price_cache.get(symbol)
        if cached and time.time() - cached[0] < 30:
            return cached[1]
        try:
            response = self.session.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": f"{symbol}USDT"}, timeout=(4, 8),
            )
            response.raise_for_status()
            price = float(response.json().get("price") or 0)
        except Exception:
            price = 0.0
        self._native_price_cache[symbol] = (time.time(), price)
        return price

    def _poll_evm(self, chain: str, wallets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        latest = _hex_int(self.rpc(chain, "eth_blockNumber", []))
        key = f"evm:{chain}:block"
        previous = _hex_int(self.store.cursor(key))
        if not previous:
            self.store.set_cursor(key, latest)
            self.store.update_health(chain, "baseline", "已建立最新区块基线，不追溯旧交易", str(latest))
            return []
        if latest <= previous:
            self.store.update_health(chain, "ok", "已同步到最新区块", str(latest))
            return []
        watched = {row["address"]: row for row in wallets}
        events: list[dict[str, Any]] = []
        max_blocks = max(5, min(500, int(os.getenv("SMART_MONEY_EVM_MAX_BLOCKS_PER_POLL", "120") or 120)))
        start = max(previous + 1, latest - max_blocks + 1)
        skipped = max(0, start - previous - 1)
        candidates: dict[str, set[str]] = {}
        candidate_transactions: dict[str, dict[str, Any]] = {}
        recipient_topics = ["0x" + wallet.removeprefix("0x").rjust(64, "0") for wallet in watched]
        log_error: Exception | None = RuntimeError("BSC 公共日志接口受限") if chain == "bsc" else None
        if log_error is None:
            try:
                logs = self.rpc(chain, "eth_getLogs", [{
                    "fromBlock": hex(start),
                    "toBlock": hex(latest),
                    "topics": [TRANSFER_TOPIC, None, recipient_topics],
                }]) or []
                for log in logs:
                    tx_hash = str(log.get("transactionHash") or "")
                    topics = log.get("topics") or []
                    recipient = "0x" + str(topics[2])[-40:].lower() if len(topics) > 2 else ""
                    if tx_hash and recipient in watched:
                        candidates.setdefault(tx_hash, set()).add(recipient)
                    elif tx_hash and len(watched) == 1:
                        candidates.setdefault(tx_hash, set()).add(next(iter(watched)))
            except Exception as exc:
                log_error = exc

        if log_error is not None:
            fallback_max = max(5, min(100, int(os.getenv("SMART_MONEY_EVM_FALLBACK_BLOCKS", "24") or 24)))
            fallback_start = max(previous + 1, latest - fallback_max + 1)
            skipped = max(skipped, max(0, fallback_start - previous - 1))
            block_numbers = list(range(fallback_start, latest + 1))
            blocks: dict[int, dict[str, Any]] = {}
            with ThreadPoolExecutor(max_workers=min(8, len(block_numbers))) as executor:
                futures = {
                    executor.submit(self.rpc, chain, "eth_getBlockByNumber", [hex(block_number), True]): block_number
                    for block_number in block_numbers
                }
                for future in as_completed(futures):
                    blocks[futures[future]] = future.result() or {}
            for block_number in block_numbers:
                block = blocks.get(block_number) or {}
                for transaction in block.get("transactions") or []:
                    sender = str(transaction.get("from") or "").lower()
                    tx_hash = str(transaction.get("hash") or "")
                    if sender in watched and tx_hash:
                        candidates.setdefault(tx_hash, set()).add(sender)
                        candidate_transactions[tx_hash] = transaction
        for tx_hash, candidate_wallets in candidates.items():
            transaction = candidate_transactions.get(tx_hash) or self.rpc(chain, "eth_getTransactionByHash", [tx_hash]) or {}
            sender = str(transaction.get("from") or "").lower()
            if sender not in candidate_wallets:
                continue
            receipt = self.rpc(chain, "eth_getTransactionReceipt", [tx_hash]) or {}
            event = classify_evm_buy(
                chain, sender, transaction, receipt,
                lambda token: self.evm_token_metadata(chain, token),
                native_price_usd=self.native_price_usd(chain),
                threshold_usd=float(watched[sender].get("alertThresholdUsd") or DEFAULT_ALERT_THRESHOLD_USD),
            )
            if event:
                events.append(self.record_buy(event)["event"])
        self.store.set_cursor(key, latest)
        message = "只读新区块监控正常"
        if log_error is not None:
            message = "快速日志接口受限，已自动切换新区块扫描"
        if skipped:
            scanned = min(max_blocks, latest - start + 1) if log_error is None else min(
                int(os.getenv("SMART_MONEY_EVM_FALLBACK_BLOCKS", "24") or 24), latest - previous
            )
            message = f"{message}；已扫描最近 {scanned} 个区块，跳过 {skipped} 个旧区块"
        self.store.update_health(chain, "ok", message, str(latest))
        return events

    def _poll_solana(self, wallets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for wallet in wallets:
            address = wallet["address"]
            key = f"solana:{address}:signature"
            previous = self.store.cursor(key)
            params: dict[str, Any] = {"limit": 20, "commitment": "confirmed"}
            if previous:
                params["until"] = previous
            signatures = self.rpc("solana", "getSignaturesForAddress", [address, params]) or []
            if not previous:
                if signatures:
                    self.store.set_cursor(key, signatures[0].get("signature") or "")
                continue
            for row in reversed(signatures):
                signature = str(row.get("signature") or "")
                if not signature or row.get("err") is not None:
                    continue
                payload = self.rpc("solana", "getTransaction", [signature, {
                    "encoding": "jsonParsed", "commitment": "confirmed", "maxSupportedTransactionVersion": 0,
                }]) or {}
                payload["signature"] = signature
                event = classify_solana_buy(
                    address, payload, self.solana_token_metadata,
                    native_price_usd=self.native_price_usd("solana"),
                    threshold_usd=float(wallet.get("alertThresholdUsd") or DEFAULT_ALERT_THRESHOLD_USD),
                )
                if event:
                    events.append(self.record_buy(event)["event"])
            if signatures:
                self.store.set_cursor(key, signatures[0].get("signature") or previous)
        self.store.update_health("solana", "ok", "只读签名监控正常", str(len(wallets)))
        return events

    def poll_once(self) -> list[dict[str, Any]]:
        if not self.poll_lock.acquire(blocking=False):
            return []
        try:
            wallets = self.store.list_wallets(enabled_only=True)
            by_chain = {chain: [row for row in wallets if row["chain"] == chain] for chain in SUPPORTED_CHAINS}
            events: list[dict[str, Any]] = []
            for chain, rows in by_chain.items():
                if not rows:
                    continue
                try:
                    if chain == "solana":
                        events.extend(self._poll_solana(rows))
                    else:
                        events.extend(self._poll_evm(chain, rows))
                except Exception as exc:
                    self.store.update_health(chain, "error", str(exc)[:300], self.store.cursor(
                        f"solana:{rows[0]['address']}:signature" if chain == "solana" else f"evm:{chain}:block"
                    ))
            return events
        finally:
            self.poll_lock.release()

    def payload(self) -> dict[str, Any]:
        wallets = self.store.list_wallets()
        events = self.store.list_events()
        health = self.store.health()
        today = time.strftime("%Y-%m-%d", time.localtime())
        today_start = int(time.mktime(time.strptime(today, "%Y-%m-%d")) * 1000)
        return {
            "ok": True,
            "supportedChains": [
                {"id": chain, "label": CHAIN_META[chain]["label"], "vmType": CHAIN_META[chain]["vm"]}
                for chain in SUPPORTED_CHAINS
            ],
            "wallets": wallets,
            "events": events,
            "health": health,
            "summary": {
                "running": self.running,
                "wallets": len(wallets),
                "enabledWallets": sum(1 for row in wallets if row.get("enabled")),
                "events": len(events),
                "todayEligibleBuys": sum(
                    1 for row in events if row.get("popupEligible") and int(row.get("observedAt") or 0) >= today_start
                ),
                "alertThresholdUsd": DEFAULT_ALERT_THRESHOLD_USD,
                "readOnly": True,
            },
            "updatedAt": _now_ms(),
        }
