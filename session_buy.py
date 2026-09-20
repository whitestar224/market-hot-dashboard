"""Local Safe/Zodiac session account state and signing boundary.

The browser never receives private-key material.  A session is deliberately
one-shot: its 10,000 USDT allowance does not refill, and every reservation is
counted even when the broadcast result is ambiguous.
"""
from __future__ import annotations

import base64
from contextlib import closing
import ctypes
from ctypes import wintypes
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time


MODE = "safe-zodiac-local-v1"
MAX_ORDER_USDT = Decimal("1000")
MAX_SESSION_USDT = Decimal("10000")
SESSION_SECONDS = 24 * 60 * 60
SUPPORTED_CHAINS = frozenset({1, 56, 8453})
BINANCE_ROUTER = "0xb44446b0c8e56988c34f7ff73ae904982b5fdda5"
ROLES_MASTER_COPY = "0xf2964ce6161ce0e75964fe7927ce114cb0b283d5"
APPROVE_SELECTOR = "0x095ea7b3"
BINANCE_SWAP_SELECTOR = "0xad43f73d"
ASSIGN_ROLES_SELECTOR = "0x957ed2b3"
PINNED_STABLECOINS = {
    1: {
        "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
    },
    56: {
        # Binance-Peg BSC-USD, shown as USDT by the supported wallet/quote flow.
        "0x55d398326f99059ff775485246999027b3197955": ("USDT", 18),
    },
    8453: {
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": ("USDC", 6),
    },
}
ROLE_LABEL = "xingyun_buy"
ALLOWANCE_LABEL = "xingyun_10k"
ZERO = "0x" + "0" * 40
SECP256K1_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


class AmbiguousBroadcastError(RuntimeError):
    """The node may have received a signed transaction; never auto-replay it."""


class RpcCallReverted(ValueError):
    """A reachable JSON-RPC node confirmed that an EVM call reverted."""


def decimal_amount(value) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError("自动买入金额格式不正确") from None
    if not result.is_finite() or result <= 0:
        raise ValueError("自动买入金额必须大于 0")
    return result


def evm_address(value, label="地址") -> str:
    result = str(value or "").strip().lower()
    if not re.fullmatch(r"0x[0-9a-f]{40}", result) or result == ZERO:
        raise ValueError(f"{label}不正确")
    return result


def bytes32_label(value: str) -> str:
    raw = str(value).encode("utf-8")
    if not raw or len(raw) > 31:
        raise ValueError("角色标识必须为 1–31 字节")
    return "0x" + raw.hex().ljust(64, "0")


ROLE_KEY = bytes32_label(ROLE_LABEL)
ALLOWANCE_KEY = bytes32_label(ALLOWANCE_LABEL)


def _eth_modules():
    try:
        from eth_abi import decode, encode
        from eth_account import Account
        from eth_utils import keccak
    except ImportError:
        raise RuntimeError("本地签名组件尚未安装，免确认买入保持关闭") from None
    return encode, decode, Account, keccak


def encode_call(signature: str, types: list[str], values: list) -> str:
    encode, _, _, keccak = _eth_modules()
    selector = keccak(text=signature)[:4]
    return "0x" + (selector + encode(types, values)).hex()


def decode_result(types: list[str], value: str):
    _, decode, _, _ = _eth_modules()
    if not isinstance(value, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", value):
        raise ValueError("链上权限读取结果无效")
    return decode(types, bytes.fromhex(value[2:]))


def mutate_word(calldata: str, index: int, value: int) -> str:
    if not isinstance(calldata, str) or not calldata.startswith("0x") or len(calldata) < 10 + (index + 1) * 64:
        raise ValueError("币安买入调用结构不完整")
    start = 10 + index * 64
    return calldata[:start] + format(value, "064x") + calldata[start + 64:]


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


class WindowsDpapi:
    """Current-user DPAPI wrapper; there is intentionally no portable fallback."""

    _entropy = b"xingyunshe-safe-session-v1"
    _forbid_ui = 0x1

    @staticmethod
    def _blob(data: bytes):
        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        return blob, buffer

    def _crypt(self, data: bytes, decrypt: bool) -> bytes:
        if os.name != "nt":
            raise RuntimeError("免确认买入密钥保险箱当前只支持 Windows")
        source, source_buffer = self._blob(data)
        entropy, entropy_buffer = self._blob(self._entropy)
        output = _DataBlob()
        if decrypt:
            ok = ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(source), None, ctypes.byref(entropy), None, None,
                self._forbid_ui, ctypes.byref(output),
            )
        else:
            ok = ctypes.windll.crypt32.CryptProtectData(
                ctypes.byref(source), "Xingyun session key", ctypes.byref(entropy),
                None, None, self._forbid_ui, ctypes.byref(output),
            )
        # Keep the input buffers alive until the native call has returned.
        _ = source_buffer, entropy_buffer
        if not ok:
            raise OSError(ctypes.get_last_error(), "Windows 无法解锁自动买入密钥")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(output.pbData)

    def protect(self, data: bytes) -> bytes:
        return self._crypt(data, False)

    def unprotect(self, data: bytes) -> bytes:
        return self._crypt(data, True)


class SessionKeyVault:
    def __init__(self, path: Path, protector=None, account_factory=None):
        self.path = Path(path)
        self.protector = protector or WindowsDpapi()
        self.account_factory = account_factory
        self.lock = threading.RLock()

    @staticmethod
    def _account(private_key: bytes):
        try:
            from eth_account import Account
        except ImportError:
            raise RuntimeError("本地签名组件尚未安装，免确认买入保持关闭") from None
        return Account.from_key(private_key)

    def exists(self) -> bool:
        return self.path.is_file()

    def _decode(self, encrypted: bytes) -> dict:
        try:
            payload = json.loads(self.protector.unprotect(encrypted).decode("utf-8"))
            private_key = base64.b64decode(payload["privateKey"], validate=True)
            if len(private_key) != 32:
                raise ValueError()
            account = (self.account_factory or self._account)(private_key)
            address = evm_address(getattr(account, "address", account), "会话账户地址")
            if address != evm_address(payload.get("address"), "会话账户地址"):
                raise ValueError()
            return {"privateKey": private_key, "address": address, "createdAt": int(payload["createdAt"])}
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise RuntimeError("自动买入密钥文件已损坏，保持停用") from None

    def create(self, now=None) -> dict:
        with self.lock:
            if self.path.exists():
                current = self._decode(self.path.read_bytes())
                return {"address": current["address"], "createdAt": current["createdAt"], "created": False}
            account_factory = self.account_factory or self._account
            while True:
                private_key = secrets.token_bytes(32)
                value = int.from_bytes(private_key, "big")
                if 0 < value < SECP256K1_ORDER:
                    break
            account = account_factory(private_key)
            address = evm_address(getattr(account, "address", account), "会话账户地址")
            created_at = int(now if now is not None else time.time())
            clear = json.dumps({"version": 1, "address": address, "createdAt": created_at,
                                "privateKey": base64.b64encode(private_key).decode("ascii")},
                               separators=(",", ":")).encode("utf-8")
            encrypted = self.protector.protect(clear)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp-" + secrets.token_hex(6))
            try:
                with open(temporary, "xb") as handle:
                    handle.write(encrypted)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.chmod(temporary, 0o600)
                except OSError:
                    pass
                os.replace(temporary, self.path)
            finally:
                if temporary.exists():
                    temporary.unlink()
            return {"address": address, "createdAt": created_at, "created": True}

    def public(self) -> dict | None:
        with self.lock:
            if not self.path.exists():
                return None
            current = self._decode(self.path.read_bytes())
            return {"address": current["address"], "createdAt": current["createdAt"]}

    def private_key(self) -> bytes:
        """Internal signing use only. Never place the return value in an API payload."""
        with self.lock:
            if not self.path.exists():
                raise ValueError("尚未创建免确认买入会话账户")
            return self._decode(self.path.read_bytes())["privateKey"]


class SessionBuyService:
    def __init__(self, db_path: Path, key_path: Path, protector=None, account_factory=None, clock=None):
        self.db_path = Path(db_path)
        self.vault = SessionKeyVault(key_path, protector=protector, account_factory=account_factory)
        self.clock = clock or time.time
        self.lock = threading.RLock()
        self.ready = False

    def db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=8)
        conn.row_factory = sqlite3.Row
        with self.lock:
            if not self.ready:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("CREATE TABLE IF NOT EXISTS session_config (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
                conn.execute("""CREATE TABLE IF NOT EXISTS session_reservations (
                    order_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, chain_id INTEGER NOT NULL,
                    amount_usdt TEXT NOT NULL, created REAL NOT NULL, state TEXT NOT NULL)""")
                conn.commit()
                self.ready = True
        return conn

    def _load_config(self, conn=None):
        owned = conn is None
        conn = conn or self.db()
        try:
            row = conn.execute("SELECT data FROM session_config WHERE id=1").fetchone()
            return json.loads(row[0]) if row else None
        finally:
            if owned:
                conn.close()

    def _save_config(self, config, conn=None):
        owned = conn is None
        conn = conn or self.db()
        try:
            conn.execute("INSERT INTO session_config(id,data) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                         (json.dumps(config, separators=(",", ":"), sort_keys=True),))
            if owned:
                conn.commit()
        finally:
            if owned:
                conn.close()

    def create(self) -> dict:
        public = self.vault.create(now=self.clock())
        return {"ok": True, "mode": MODE, "sessionAddress": public["address"],
                "created": public["created"], "privateKeyExposed": False,
                "nextStep": "请在 Safe/Zodiac 中为此公开地址安装受限角色；安装前不会启用"}

    def configure_local(self, payload: dict) -> dict:
        """Persist an already on-chain-verified configuration.

        The caller that performs chain verification must pass verified=True.
        This method never treats browser-provided limits or timestamps as authority.
        """
        if payload.get("verified") is not True:
            raise ValueError("链上权限尚未逐项核验，不能启用免确认买入")
        public = self.vault.public()
        if not public:
            raise ValueError("请先创建本机会话账户")
        chain_config = self._chain_payload(payload, public)
        chain = chain_config["chainId"]
        if payload.get("roleLastUpdate") is not None:
            chain_config["roleLastUpdate"] = int(payload["roleLastUpdate"])
        now = int(self.clock())
        session_id = secrets.token_urlsafe(24)
        chain_config["verifiedAt"] = now
        config = {"version": 1, "mode": MODE, "sessionId": session_id,
                  "sessionAddress": public["address"], "enabled": True,
                  "createdAt": now, "expiresAt": now + SESSION_SECONDS,
                  "maxOrderUsdt": str(MAX_ORDER_USDT), "maxSessionUsdt": str(MAX_SESSION_USDT),
                  "revokeState": "scheduled", "chains": {str(chain): chain_config}}
        with self.lock, closing(self.db()) as conn, conn:
            self._save_config(config, conn)
        return self.status()

    def disable_local(self, reason="已由本机停用") -> dict:
        with self.lock, closing(self.db()) as conn, conn:
            config = self._load_config(conn)
            if config:
                config.update(enabled=False, disabledAt=int(self.clock()), disableReason=str(reason)[:160])
                if config.get("revokeState") == "scheduled":
                    config["revokeState"] = "pending"
                self._save_config(config, conn)
        return self.status()

    def _reserved(self, conn, session_id):
        rows = conn.execute("SELECT amount_usdt FROM session_reservations WHERE session_id=?", (session_id,)).fetchall()
        return sum((Decimal(row[0]) for row in rows), Decimal(0))

    def status(self) -> dict:
        public = self.vault.public()
        with closing(self.db()) as conn:
            config = self._load_config(conn)
            reserved = self._reserved(conn, config["sessionId"]) if config else Decimal(0)
        now = int(self.clock())
        configured = bool(config and config.get("chains"))
        expired = bool(config and now >= int(config.get("expiresAt") or 0))
        active = bool(configured and config.get("enabled") and not expired and config.get("revokeState") == "scheduled")
        remaining = max(Decimal(0), MAX_SESSION_USDT - reserved) if config else MAX_SESSION_USDT
        if not public:
            reason = "尚未创建本机会话账户"
        elif not configured:
            reason = "尚未完成 Safe/Zodiac 一次性链上安装"
        elif expired:
            reason = "24 小时授权已到期，等待链上撤销"
        elif config.get("revokeState") != "scheduled" or not config.get("enabled"):
            reason = "免确认买入已停用，等待链上撤销或重新授权"
        elif remaining <= 0:
            reason = "本次 10000 USDT 授权额度已用完"
        else:
            reason = "已启用"
        return {"ok": True, "mode": MODE, "keyCreated": bool(public),
                "sessionAddress": public["address"] if public else None,
                "privateKeyExposed": False, "configured": configured, "active": active,
                "reason": reason, "createdAt": config.get("createdAt") if config else None,
                "expiresAt": config.get("expiresAt") if config else None,
                "maxOrderUsdt": str(MAX_ORDER_USDT), "maxSessionUsdt": str(MAX_SESSION_USDT),
                "reservedUsdt": str(reserved), "remainingUsdt": str(remaining),
                "revokeState": config.get("revokeState") if config else "not-configured",
                "chains": list((config.get("chains") or {}).values()) if config else []}

    def require_active(self, chain_id: int, amount_usdt=None, conn=None) -> dict:
        owned = conn is None
        conn = conn or self.db()
        try:
            config = self._load_config(conn)
            if not config or not config.get("enabled") or config.get("revokeState") != "scheduled":
                raise ValueError("免费免确认买入尚未启用")
            if int(self.clock()) >= int(config.get("expiresAt") or 0):
                raise ValueError("24 小时授权已到期，不能继续买入")
            chain = (config.get("chains") or {}).get(str(int(chain_id)))
            if not chain:
                raise ValueError("目标链尚未完成免确认买入授权")
            if amount_usdt is not None:
                amount = decimal_amount(amount_usdt)
                if amount > MAX_ORDER_USDT:
                    raise ValueError("免确认买入单笔最多 1000 USDT")
                if self._reserved(conn, config["sessionId"]) + amount > MAX_SESSION_USDT:
                    raise ValueError("本次 24 小时授权累计最多 10000 USDT")
            return {**config, "chain": chain}
        finally:
            if owned:
                conn.close()

    def reserve(self, order_id: str, chain_id: int, amount_usdt) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_-]{40,64}", str(order_id or "")):
            raise ValueError("自动买入订单标识无效")
        amount = decimal_amount(amount_usdt)
        with self.lock, closing(self.db()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            config = self.require_active(chain_id, amount, conn)
            try:
                conn.execute("INSERT INTO session_reservations VALUES(?,?,?,?,?,?)",
                             (order_id, config["sessionId"], int(chain_id), str(amount), self.clock(), "reserved"))
            except sqlite3.IntegrityError:
                raise ValueError("本次自动买入已经占用额度，不会重复提交") from None
            conn.commit()
            used = self._reserved(conn, config["sessionId"])
        return {"ok": True, "reservedUsdt": str(amount), "remainingUsdt": str(MAX_SESSION_USDT-used)}

    def mark_reservation(self, order_id: str, state: str):
        if state not in {"broadcasting", "submitted", "success", "failed", "uncertain", "revoked"}:
            raise ValueError("自动买入额度状态无效")
        with closing(self.db()) as conn, conn:
            changed = conn.execute("UPDATE session_reservations SET state=? WHERE order_id=?", (state, order_id)).rowcount
        if changed != 1:
            raise ValueError("自动买入额度记录不存在")

    @staticmethod
    def _chain_payload(payload: dict, public: dict) -> dict:
        chain = int(payload.get("chainId") or 0)
        if chain not in SUPPORTED_CHAINS:
            raise ValueError("免费免确认买入当前只支持 Ethereum、BNB Chain、Base 同链")
        decimals = int(payload.get("stableDecimals"))
        if not 0 <= decimals <= 24:
            raise ValueError("稳定币精度不正确")
        symbol = str(payload.get("stableSymbol") or "").strip().upper()[:16]
        stable_address = evm_address(payload.get("stableAddress"), "稳定币地址")
        expected = PINNED_STABLECOINS.get(chain, {}).get(stable_address)
        if expected != (symbol, decimals):
            raise ValueError("稳定币合约、名称或精度不在本项目固定白名单中")
        return {"chainId": chain,
                "safeAddress": evm_address(payload.get("safeAddress"), "Safe 地址"),
                "rolesAddress": evm_address(payload.get("rolesAddress"), "Roles 地址"),
                "stableAddress": stable_address,
                "stableSymbol": symbol, "stableDecimals": decimals,
                "sessionAddress": public["address"], "roleKey": ROLE_KEY,
                "allowanceKey": ALLOWANCE_KEY, "routerAddress": BINANCE_ROUTER}

    @staticmethod
    def _rpc_address(rpc, network, target, signature, args_types=None, args=None):
        data = encode_call(signature, args_types or [], args or [])
        raw = rpc(network, "eth_call", [{"to": target, "data": data}, "latest"])
        return evm_address(decode_result(["address"], raw)[0], "链上合约地址")

    @staticmethod
    def _rpc_uint(rpc, network, target, signature):
        raw = rpc(network, "eth_call", [{"to": target, "data": encode_call(signature, [], [])}, "latest"])
        return int(decode_result(["uint256"], raw)[0])

    @staticmethod
    def wrap_call(chain: dict, to: str, value: int, data: str, should_revert=True) -> str:
        if not isinstance(data, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", data):
            raise ValueError("待执行交易数据无效")
        return encode_call("execTransactionWithRole(address,uint256,bytes,uint8,bytes32,bool)",
            ["address", "uint256", "bytes", "uint8", "bytes32", "bool"],
            [evm_address(to), int(value), bytes.fromhex(data[2:]), 0,
             bytes.fromhex(chain["roleKey"][2:]), bool(should_revert)])

    @classmethod
    def _probe_allowed(cls, rpc, network, chain, to, data):
        wrapped = cls.wrap_call(chain, to, 0, data, should_revert=False)
        result = rpc(network, "eth_call", [{"from": chain["sessionAddress"],
            "to": chain["rolesAddress"], "data": wrapped}, "latest"])
        decoded = decode_result(["bool"], result)
        if len(decoded) != 1:
            raise ValueError("Roles 权限模拟结果无效")
        return bool(decoded[0])

    @classmethod
    def _probe_denied(cls, rpc, network, chain, to, data, message):
        try:
            cls._probe_allowed(rpc, network, chain, to, data)
        except RpcCallReverted:
            return
        raise ValueError(message)

    @staticmethod
    def _enum_matches(value, number, label):
        return value == number or str(value).strip().lower() == str(label).lower()

    @classmethod
    def _condition_has_allowance(cls, condition, allowance_key, depth=0, budget=None):
        if budget is None:
            budget = [64]
        if depth > 12 or budget[0] <= 0 or not isinstance(condition, dict):
            raise ValueError("Roles 条件树结构异常")
        budget[0] -= 1
        operator = condition.get("operator")
        comp = str(condition.get("compValue") or "").lower()
        if cls._enum_matches(operator, 28, "WithinAllowance") and comp == allowance_key.lower():
            return True
        children = condition.get("children") or []
        if not isinstance(children, list):
            raise ValueError("Roles 条件树结构异常")
        return any(cls._condition_has_allowance(child, allowance_key, depth + 1, budget) for child in children)

    @classmethod
    def verify_role_snapshot(cls, chain, snapshot):
        """Require the official indexer's latest role surface in addition to live call probes."""
        if not isinstance(snapshot, dict):
            raise ValueError("未取得 Roles 最新权限清单，免确认模式保持关闭")
        if str(snapshot.get("key") or "").lower() != chain["roleKey"].lower():
            raise ValueError("Roles 权限清单的角色标识不一致")
        members = []
        for item in snapshot.get("members") or []:
            raw = item.get("member", {}).get("address") if isinstance(item, dict) else item
            members.append(evm_address(raw, "Roles 成员地址"))
        if members != [chain["sessionAddress"]]:
            raise ValueError("指定角色必须只分配给本机会话地址")
        expected_functions = {
            chain["stableAddress"]: APPROVE_SELECTOR,
            chain["routerAddress"]: BINANCE_SWAP_SELECTOR,
            chain["rolesAddress"]: ASSIGN_ROLES_SELECTOR,
        }
        rows = snapshot.get("targets")
        if not isinstance(rows, list) or len(rows) != len(expected_functions):
            raise ValueError("Roles 角色开放了额外目标或缺少固定目标")
        seen = set()
        approval_condition = None
        for target in rows:
            if not isinstance(target, dict):
                raise ValueError("Roles 权限清单结构异常")
            target_address = evm_address(target.get("address"), "Roles 目标地址")
            selector = expected_functions.get(target_address)
            if not selector or target_address in seen:
                raise ValueError("Roles 角色开放了未批准的目标")
            seen.add(target_address)
            if not cls._enum_matches(target.get("clearance"), 2, "Function"):
                raise ValueError("Roles 目标没有限制到固定函数")
            if not cls._enum_matches(target.get("executionOptions"), 0, "None"):
                raise ValueError("Roles 目标允许转原生币或代理调用")
            functions = target.get("functions")
            if not isinstance(functions, list) or len(functions) != 1:
                raise ValueError("Roles 目标函数范围不是唯一固定函数")
            function = functions[0]
            if str(function.get("selector") or "").lower() != selector:
                raise ValueError("Roles 固定函数与项目模板不一致")
            if function.get("wildcarded") is not False or not isinstance(function.get("condition"), dict):
                raise ValueError("Roles 固定函数缺少参数限制")
            if not cls._enum_matches(function.get("executionOptions"), 0, "None"):
                raise ValueError("Roles 函数允许转原生币或代理调用")
            condition = function["condition"].get("json", function["condition"])
            if isinstance(condition, str):
                try:
                    condition = json.loads(condition)
                except json.JSONDecodeError:
                    raise ValueError("Roles 条件清单无法解析") from None
            if not isinstance(condition, dict):
                raise ValueError("Roles 固定函数缺少可核验的参数条件")
            if target_address == chain["stableAddress"]:
                approval_condition = condition
        if seen != set(expected_functions):
            raise ValueError("Roles 固定权限目标不完整")
        children = approval_condition.get("children") if isinstance(approval_condition, dict) else None
        if (not cls._enum_matches(approval_condition.get("operator") if isinstance(approval_condition, dict) else None,
                                  5, "Matches") or not isinstance(children, list) or len(children) != 2
                or not cls._condition_has_allowance(children[1], chain["allowanceKey"])):
            raise ValueError("稳定币授权金额没有绑定一次性 10000 USDT 链上额度")
        try:
            last_update = int(snapshot.get("lastUpdate"))
        except (TypeError, ValueError):
            raise ValueError("Roles 权限清单缺少更新时间") from None
        if last_update <= 0:
            raise ValueError("Roles 权限清单更新时间无效")
        return last_update

    def verify_and_configure(self, payload: dict, network: dict, rpc) -> dict:
        """Verify the live Safe/Role boundary before enabling local signing."""
        public = self.vault.public()
        if not public:
            raise ValueError("请先创建本机会话账户")
        chain = self._chain_payload(payload, public)
        role_last_update = self.verify_role_snapshot(chain, payload.get("_roleSnapshot"))
        if int(network.get("id") or 0) != chain["chainId"]:
            raise ValueError("RPC 网络与免确认授权链不一致")
        for label, target in (("Safe", chain["safeAddress"]), ("Roles", chain["rolesAddress"]),
                              (chain["stableSymbol"], chain["stableAddress"])):
            code = rpc(network, "eth_getCode", [target, "latest"])
            if not isinstance(code, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})+", code) or code == "0x":
                raise ValueError(f"{label} 地址没有可核验合约")
        roles_code = rpc(network, "eth_getCode", [chain["rolesAddress"], "latest"]).lower()
        if chain["rolesAddress"] != ROLES_MASTER_COPY and ROLES_MASTER_COPY[2:] not in roles_code:
            raise ValueError("Roles 实例没有绑定到项目固定的官方主实现")
        for method in ("owner()", "avatar()", "target()"):
            actual = self._rpc_address(rpc, network, chain["rolesAddress"], method)
            if actual != chain["safeAddress"]:
                raise ValueError(f"Roles 的 {method[:-2]} 不是所填 Safe")
        safe_enabled = rpc(network, "eth_call", [{"to": chain["safeAddress"],
            "data": encode_call("isModuleEnabled(address)", ["address"], [chain["rolesAddress"]])}, "latest"])
        if not bool(decode_result(["bool"], safe_enabled)[0]):
            raise ValueError("Safe 尚未启用这个 Roles 模块")
        member_enabled = rpc(network, "eth_call", [{"to": chain["rolesAddress"],
            "data": encode_call("isModuleEnabled(address)", ["address"], [chain["sessionAddress"]])}, "latest"])
        if not bool(decode_result(["bool"], member_enabled)[0]):
            raise ValueError("本机会话地址尚未加入指定 Roles 角色")
        decimals = self._rpc_uint(rpc, network, chain["stableAddress"], "decimals()")
        if decimals != chain["stableDecimals"]:
            raise ValueError("稳定币链上精度与设置不一致")
        cap_units = int(MAX_SESSION_USDT * (10 ** decimals))
        allowance_raw = rpc(network, "eth_call", [{"to": chain["rolesAddress"],
            "data": encode_call("allowances(bytes32)", ["bytes32"], [bytes.fromhex(ALLOWANCE_KEY[2:])])}, "latest"])
        refill, max_refill, period, balance, _timestamp = [int(item) for item in
            decode_result(["uint128", "uint128", "uint64", "uint128", "uint64"], allowance_raw)]
        if refill != 0 or period != 0 or max_refill != cap_units or not 0 < balance <= cap_units:
            raise ValueError("链上额度不是一次性 10000 USDT，不能启用")
        # The harmless approve(known router, 0) must be permitted, while another
        # spender and an amount above the per-order ceiling must be rejected.
        approve_zero = encode_call("approve(address,uint256)", ["address", "uint256"], [BINANCE_ROUTER, 0])
        self._probe_allowed(rpc, network, chain, chain["stableAddress"], approve_zero)
        foreign_spender = encode_call("approve(address,uint256)", ["address", "uint256"], [chain["sessionAddress"], 1])
        self._probe_denied(rpc, network, chain, chain["stableAddress"], foreign_spender,
                           "链上角色可以授权给非币安路由，权限过宽")
        too_large = encode_call("approve(address,uint256)", ["address", "uint256"],
                                [BINANCE_ROUTER, int((MAX_ORDER_USDT + Decimal("0.01")) * 10 ** decimals)])
        self._probe_denied(rpc, network, chain, chain["stableAddress"], too_large,
                           "链上角色没有限制单笔 1000 USDT")
        revoke = self.revoke_inner_call(chain)
        self._probe_allowed(rpc, network, chain, chain["rolesAddress"], revoke)
        assign_other = encode_call("assignRoles(address,bytes32[],bool[])",
            ["address", "bytes32[]", "bool[]"],
            [chain["sessionAddress"], [bytes.fromhex(ROLE_KEY[2:])], [True]])
        self._probe_denied(rpc, network, chain, chain["rolesAddress"], assign_other,
                           "链上角色可以重新增加权限，不能启用")
        return self.configure_local({**payload, "verified": True, "roleLastUpdate": role_last_update})

    def require_quote_policy(self, intent: dict, quote: dict, network: dict, rpc, role_snapshot) -> dict:
        amount_usdt = decimal_amount(intent.get("amountUsdt"))
        config = self.require_active(intent["target"]["chainId"], amount_usdt)
        chain = config["chain"]
        last_update = self.verify_role_snapshot(chain, role_snapshot)
        if last_update < int(chain.get("roleLastUpdate") or 0):
            raise ValueError("Roles 权限索引发生回退，免确认买入已停止")
        if quote.get("provider") != "binance-web3" or intent["source"]["chainId"] != intent["target"]["chainId"]:
            raise ValueError("免确认买入只允许已核验的 Binance Web3 同链路线")
        if any(evm_address(intent[key]) != chain["safeAddress"] for key in ("sender", "recipient")):
            raise ValueError("免确认买入的付款和收币地址必须都是 Safe")
        if (evm_address(intent["source"]["address"]) != chain["stableAddress"]
                or int(intent["source"]["decimals"]) != int(chain["stableDecimals"])):
            raise ValueError("报价没有使用本次授权的固定稳定币")
        approvals = [item["data"] for step in quote.get("steps") or [] if step.get("id") == "approval"
                     for item in step.get("items") or []]
        if not approvals or int(approvals[-1]["data"][-64:], 16) != int(intent["amount"]):
            raise ValueError("免确认买入必须重新设置本次精确授权")
        for item in approvals:
            self._probe_allowed(rpc, network, chain, item["to"], item["data"])
        swap = quote["steps"][-1]["items"][0]["data"]
        self._probe_allowed(rpc, network, chain, swap["to"], swap["data"])
        # The first outer word that controls the receiver must remain zero, so
        # the pinned router sends output to msg.sender (the Safe).
        bad_receiver = mutate_word(swap["data"], 1, int(chain["sessionAddress"], 16))
        self._probe_denied(rpc, network, chain, swap["to"], bad_receiver,
                           "链上路由权限没有把最终收币人锁定为 Safe")
        too_large_units = int((MAX_ORDER_USDT + Decimal("0.01")) * 10 ** chain["stableDecimals"])
        bad_amount = mutate_word(swap["data"], 4, too_large_units)
        self._probe_denied(rpc, network, chain, swap["to"], bad_amount,
                           "链上路由权限没有限制单笔 1000 USDT")
        return config

    @staticmethod
    def revoke_inner_call(chain: dict) -> str:
        return encode_call("assignRoles(address,bytes32[],bool[])",
            ["address", "bytes32[]", "bool[]"],
            [chain["sessionAddress"], [bytes.fromhex(chain["roleKey"][2:])], [False]])

    def _signed_raw_transaction(self, transaction: dict):
        _, _, Account, _ = _eth_modules()
        signed = Account.sign_transaction(transaction, self.vault.private_key())
        raw = getattr(signed, "raw_transaction", getattr(signed, "rawTransaction", None))
        if raw is None:
            raise RuntimeError("本地签名器没有返回交易数据")
        return "0x" + bytes(raw).hex()

    def send_call(self, network: dict, rpc, chain: dict, to: str, value: int, data: str) -> str:
        """Simulate, sign and broadcast exactly once. The caller persists a claim first."""
        wrapped = self.wrap_call(chain, to, value, data, should_revert=True)
        call = {"from": chain["sessionAddress"], "to": chain["rolesAddress"], "value": "0x0", "data": wrapped}
        rpc(network, "eth_call", [call, "latest"])
        estimated = int(rpc(network, "eth_estimateGas", [call]), 16)
        gas_price = int(rpc(network, "eth_gasPrice", []), 16)
        nonce = int(rpc(network, "eth_getTransactionCount", [chain["sessionAddress"], "pending"]), 16)
        balance = int(rpc(network, "eth_getBalance", [chain["sessionAddress"], "latest"]), 16)
        if not 50_000 <= estimated <= 5_000_000 or not 0 < gas_price <= 10 ** 12:
            raise ValueError("免确认交易的 Gas 估计异常，已停止")
        gas = estimated * 12 // 10
        if balance < gas * gas_price:
            raise ValueError("会话账户的原生币不足以支付 Gas")
        transaction = {"chainId": int(chain["chainId"]), "nonce": nonce,
            "to": chain["rolesAddress"], "value": 0, "data": wrapped,
            "gas": gas, "gasPrice": gas_price}
        raw = self._signed_raw_transaction(transaction)
        # Never retry this call: a timeout is an ambiguous broadcast result.
        try:
            tx_hash = rpc(network, "eth_sendRawTransaction", [raw])
        except Exception as exc:
            raise AmbiguousBroadcastError("节点未明确返回提交结果；不会自动重发") from exc
        if not isinstance(tx_hash, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", tx_hash):
            raise RuntimeError("节点没有返回有效交易哈希；不会自动重发")
        return tx_hash.lower()

    @staticmethod
    def wait_receipt(network: dict, rpc, tx_hash: str, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            receipt = rpc(network, "eth_getTransactionReceipt", [tx_hash])
            if receipt:
                if str(receipt.get("transactionHash") or "").lower() != tx_hash.lower():
                    raise ValueError("链上回执与免确认交易哈希不一致")
                if int(receipt.get("status") or "0x0", 16) != 1:
                    raise ValueError("免确认交易在链上执行失败")
                return receipt
            time.sleep(1.5)
        raise TimeoutError("免确认交易已提交但确认超时；不会自动重发")

    def mark_revoke(self, state: str, tx_hash=None):
        if state not in {"pending", "submitted", "revoked", "failed", "uncertain"}:
            raise ValueError("撤销状态无效")
        with self.lock, closing(self.db()) as conn, conn:
            config = self._load_config(conn)
            if not config:
                return
            config["enabled"] = False
            config["revokeState"] = state
            if tx_hash:
                config["revokeTxHash"] = str(tx_hash).lower()
            if state == "revoked":
                config["revokedAt"] = int(self.clock())
            self._save_config(config, conn)

    def revoke_if_due(self, networks: dict, rpc) -> dict:
        status = self.status()
        if not status["configured"]:
            return status
        due = not status["active"] and status["revokeState"] in {"scheduled", "pending", "failed", "submitted"}
        if not due:
            return status
        config = self._load_config()
        chain = next(iter(config["chains"].values()))
        network = networks.get(int(chain["chainId"]))
        if not network:
            self.mark_revoke("failed")
            raise ValueError("撤销所需公链当前不可用")
        if status["revokeState"] == "submitted":
            tx_hash = config.get("revokeTxHash")
            if not isinstance(tx_hash, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", tx_hash):
                self.mark_revoke("uncertain")
                raise ValueError("撤销交易记录不完整，已停止自动重发")
            receipt = rpc(network, "eth_getTransactionReceipt", [tx_hash])
            if not receipt:
                return status
            if str(receipt.get("transactionHash") or "").lower() != tx_hash.lower():
                self.mark_revoke("uncertain", tx_hash)
                raise ValueError("撤销交易回执不一致，已停止自动重发")
            if int(receipt.get("status") or "0x0", 16) == 1:
                self.mark_revoke("revoked", tx_hash)
                return self.status()
            # A mined failure is unambiguous and consumed its nonce; a later
            # maintenance pass may safely build a fresh revoke transaction.
            self.mark_revoke("failed", tx_hash)
            raise ValueError("链上撤销失败，稍后将重新核验并重试")
        self.mark_revoke("pending")
        try:
            data = self.revoke_inner_call(chain)
            tx_hash = self.send_call(network, rpc, chain, chain["rolesAddress"], 0, data)
            self.mark_revoke("submitted", tx_hash)
            self.wait_receipt(network, rpc, tx_hash)
            self.mark_revoke("revoked", tx_hash)
        except AmbiguousBroadcastError:
            self.mark_revoke("uncertain")
            raise
        except Exception:
            # If a hash exists, status stays submitted; otherwise a later local
            # maintenance pass may retry only after the operator checks state.
            latest = self._load_config() or {}
            if latest.get("revokeState") != "submitted":
                self.mark_revoke("failed")
            raise
        return self.status()
