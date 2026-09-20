import tempfile
import unittest
from pathlib import Path
import json

from eth_abi import decode, encode
from eth_utils import keccak

from session_buy import (
    ALLOWANCE_KEY, APPROVE_SELECTOR, ASSIGN_ROLES_SELECTOR, BINANCE_ROUTER,
    BINANCE_SWAP_SELECTOR, MAX_ORDER_USDT, MAX_SESSION_USDT, ROLE_KEY,
    ROLES_MASTER_COPY, RpcCallReverted, SessionBuyService, bytes32_label, encode_call,
)

BSC_USDT = "0x55d398326f99059ff775485246999027b3197955"


class FakeProtector:
    def protect(self, value):
        return b"protected:" + value[::-1]

    def unprotect(self, value):
        if not value.startswith(b"protected:"):
            raise ValueError("bad envelope")
        return value[len(b"protected:"):][::-1]


class Account:
    def __init__(self, key):
        self.address = "0x" + key.hex()[-40:]


def output(types, values):
    return "0x" + encode(types, values).hex()


def selector(signature):
    return "0x" + keccak(text=signature)[:4].hex()


def role_snapshot(session_address, roles, token, *, allowance=True, extra_target=False, last_update=123):
    pass_condition = {"paramType": 1, "operator": 0}
    approval_amount = {"paramType": 1, "operator": 28,
                       "compValue": ALLOWANCE_KEY} if allowance else pass_condition
    approval = {"paramType": 5, "operator": 5,
                "children": [pass_condition, approval_amount]}
    restricted = {"paramType": 5, "operator": 5, "children": [pass_condition]}
    def target(address, function_selector, condition):
        return {"address": address, "clearance": "Function", "executionOptions": "None",
                "functions": [{"selector": function_selector, "executionOptions": "None",
                    "wildcarded": False, "condition": {"id": "condition", "json": json.dumps(condition)}}]}
    targets = [target(token, APPROVE_SELECTOR, approval),
               target(BINANCE_ROUTER, BINANCE_SWAP_SELECTOR, restricted),
               target(roles, ASSIGN_ROLES_SELECTOR, restricted)]
    if extra_target:
        targets.append(target("0x" + "99" * 20, APPROVE_SELECTOR, restricted))
    return {"key": ROLE_KEY, "members": [{"member": {"address": session_address}}],
            "targets": targets, "lastUpdate": last_update}


class RpcHarness:
    def __init__(self, service, safe, roles, token, decimals=18):
        self.service, self.safe, self.roles, self.token = service, safe, roles, token
        self.decimals = decimals
        self.sent = []
        self.exec_selector = selector("execTransactionWithRole(address,uint256,bytes,uint8,bytes32,bool)")

    def __call__(self, network, method, params):
        if method == "eth_getCode":
            address = params[0].lower()
            if address == self.roles:
                return "0x363d3d373d3d3d363d73" + ROLES_MASTER_COPY[2:] + "5af43d82803e903d91602b57fd5bf3"
            return "0x60016000"
        if method == "eth_estimateGas":
            return "0x30d40"
        if method == "eth_gasPrice":
            return "0x3b9aca00"
        if method == "eth_getTransactionCount":
            return "0x7"
        if method == "eth_getBalance":
            return "0xde0b6b3a7640000"
        if method == "eth_sendRawTransaction":
            self.sent.append(params[0])
            return "0x" + "ab" * 32
        if method != "eth_call":
            raise AssertionError(method)
        call = params[0]
        to, data = call["to"].lower(), call["data"].lower()
        if to == self.roles and data.startswith(self.exec_selector):
            inner = decode(["address", "uint256", "bytes", "uint8", "bytes32", "bool"], bytes.fromhex(data[10:]))
            inner_to, inner_data = inner[0].lower(), "0x" + inner[2].hex()
            allowed = False
            if inner_to == self.token and inner_data.startswith(selector("approve(address,uint256)")):
                spender, amount = decode(["address", "uint256"], bytes.fromhex(inner_data[10:]))
                allowed = spender.lower() == BINANCE_ROUTER and amount <= 1000 * 10 ** self.decimals
            elif inner_to == self.roles and inner_data.startswith(selector("assignRoles(address,bytes32[],bool[])")):
                member, keys, enabled = decode(["address", "bytes32[]", "bool[]"], bytes.fromhex(inner_data[10:]))
                allowed = member.lower() == self.service.vault.public()["address"] and list(keys) == [bytes.fromhex(ROLE_KEY[2:])] and list(enabled) == [False]
            elif inner_to == BINANCE_ROUTER:
                words = [int(inner_data[10 + i * 64:74 + i * 64], 16) for i in range(10)]
                allowed = words[1] == 0 and words[4] <= 1000 * 10 ** self.decimals
            if not allowed:
                raise RpcCallReverted("execution reverted: unauthorized")
            return output(["bool"], [False])
        if to == self.roles:
            if data.startswith(selector("owner()")) or data.startswith(selector("avatar()")) or data.startswith(selector("target()")):
                return output(["address"], [self.safe])
            if data.startswith(selector("isModuleEnabled(address)")):
                return output(["bool"], [True])
            if data.startswith(selector("allowances(bytes32)")):
                cap = 10000 * 10 ** self.decimals
                return output(["uint128", "uint128", "uint64", "uint128", "uint64"], [0, cap, 0, cap, 123])
        if to == self.safe and data.startswith(selector("isModuleEnabled(address)")):
            return output(["bool"], [True])
        if to == self.token and data.startswith(selector("decimals()")):
            return output(["uint256"], [self.decimals])
        raise AssertionError((to, data[:10]))


class SessionBuyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.now = 2_000_000_000
        self.service = SessionBuyService(root / "state.sqlite", root / "session.bin",
            protector=FakeProtector(), account_factory=Account, clock=lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def configure(self):
        self.service.create()
        return self.service.configure_local({"verified": True, "chainId": 56,
            "safeAddress": "0x" + "11" * 20, "rolesAddress": "0x" + "22" * 20,
            "stableAddress": BSC_USDT, "stableSymbol": "USDT", "stableDecimals": 18})

    def test_keys_are_bytes32_labels_and_policy_constants_are_fixed(self):
        self.assertEqual(ROLE_KEY, bytes32_label("xingyun_buy"))
        self.assertEqual(ALLOWANCE_KEY, bytes32_label("xingyun_10k"))
        self.assertEqual(BINANCE_ROUTER, "0xb44446b0c8e56988c34f7ff73ae904982b5fdda5")
        self.assertEqual(MAX_ORDER_USDT, 1000)
        self.assertEqual(MAX_SESSION_USDT, 10000)

    def test_create_never_returns_or_stores_plaintext_key(self):
        result = self.service.create()
        self.assertTrue(result["created"])
        self.assertFalse(result["privateKeyExposed"])
        self.assertNotIn("privateKey", result)
        raw = self.service.vault.path.read_bytes()
        self.assertTrue(raw.startswith(b"protected:"))
        self.assertNotIn(self.service.vault.private_key(), raw)
        again = self.service.create()
        self.assertFalse(again["created"])
        self.assertEqual(again["sessionAddress"], result["sessionAddress"])

    def test_unverified_or_invalid_configuration_cannot_enable(self):
        self.service.create()
        base = {"chainId": 56, "safeAddress": "0x" + "11" * 20,
            "rolesAddress": "0x" + "22" * 20, "stableAddress": BSC_USDT,
            "stableSymbol": "USDT", "stableDecimals": 18}
        with self.assertRaisesRegex(ValueError, "链上权限"):
            self.service.configure_local(base)
        with self.assertRaisesRegex(ValueError, "白名单"):
            self.service.configure_local({**base, "verified": True, "stableSymbol": "DAI"})
        with self.assertRaisesRegex(ValueError, "当前只支持"):
            self.service.configure_local({**base, "verified": True, "chainId": 42161})
        self.assertFalse(self.service.status()["active"])

    def test_session_is_24_hours_and_never_refills(self):
        status = self.configure()
        self.assertTrue(status["active"])
        self.assertEqual(status["expiresAt"] - status["createdAt"], 86400)
        self.assertEqual(status["remainingUsdt"], "10000")
        self.now += 86400
        status = self.service.status()
        self.assertFalse(status["active"])
        self.assertIn("到期", status["reason"])
        with self.assertRaisesRegex(ValueError, "到期"):
            self.service.require_active(56, "1")

    def test_single_and_total_limits_are_reserved_before_execution(self):
        self.configure()
        with self.assertRaisesRegex(ValueError, "单笔"):
            self.service.reserve("a" * 40, 56, "1000.01")
        for index in range(10):
            result = self.service.reserve((str(index) * 40)[:40], 56, "1000")
        self.assertEqual(result["remainingUsdt"], "0")
        with self.assertRaisesRegex(ValueError, "累计"):
            self.service.reserve("z" * 40, 56, "0.01")

    def test_duplicate_and_failed_orders_keep_their_reservation(self):
        self.configure()
        order = "a" * 40
        self.service.reserve(order, 56, "100")
        with self.assertRaisesRegex(ValueError, "已经占用"):
            self.service.reserve(order, 56, "100")
        self.service.mark_reservation(order, "failed")
        self.assertEqual(self.service.status()["remainingUsdt"], "9900")

    def test_local_disable_fails_closed_and_schedules_revoke(self):
        self.configure()
        status = self.service.disable_local()
        self.assertFalse(status["active"])
        self.assertEqual(status["revokeState"], "pending")
        with self.assertRaisesRegex(ValueError, "尚未启用"):
            self.service.reserve("a" * 40, 56, "1")

    def test_live_policy_verification_checks_safe_role_allowance_and_denials(self):
        created = self.service.create()
        safe, roles, token = "0x" + "11" * 20, "0x" + "22" * 20, BSC_USDT
        rpc = RpcHarness(self.service, safe, roles, token)
        status = self.service.verify_and_configure({"chainId": 56, "safeAddress": safe,
            "rolesAddress": roles, "stableAddress": token, "stableSymbol": "USDT", "stableDecimals": 18,
            "_roleSnapshot": role_snapshot(created["sessionAddress"], roles, token)},
            {"id": 56}, rpc)
        self.assertTrue(status["active"])
        self.assertEqual(status["sessionAddress"], created["sessionAddress"])
        self.assertEqual(status["chains"][0]["routerAddress"], BINANCE_ROUTER)

    def test_quote_policy_requires_safe_fixed_stable_exact_approval_and_denies_mutations(self):
        self.configure()
        config = self.service._load_config()
        chain = config["chains"]["56"]
        rpc = RpcHarness(self.service, chain["safeAddress"], chain["rolesAddress"], chain["stableAddress"])
        amount = 100 * 10 ** 18
        approval = {"to": chain["stableAddress"], "data": encode_call("approve(address,uint256)",
            ["address", "uint256"], [BINANCE_ROUTER, amount])}
        words = [0, 0, 0, int(chain["stableAddress"], 16), amount, int("0x" + "44" * 20, 16), 1, 0, 0, 320]
        swap_data = "0xad43f73d" + "".join(format(value, "064x") for value in words)
        quote = {"provider": "binance-web3", "steps": [
            {"id": "approval", "items": [{"data": approval}]},
            {"id": "swap", "items": [{"data": {"to": BINANCE_ROUTER, "data": swap_data}}]}]}
        intent = {"sender": chain["safeAddress"], "recipient": chain["safeAddress"],
            "amountUsdt": "100", "amount": str(amount),
            "source": {"chainId": 56, "address": chain["stableAddress"], "decimals": 18},
            "target": {"chainId": 56, "address": "0x" + "44" * 20}}
        snapshot = role_snapshot(chain["sessionAddress"], chain["rolesAddress"], chain["stableAddress"])
        self.assertEqual(self.service.require_quote_policy(intent, quote, {"id": 56}, rpc, snapshot)["chain"], chain)
        with self.assertRaisesRegex(ValueError, "精确授权"):
            self.service.require_quote_policy(intent, {**quote, "steps": quote["steps"][1:]}, {"id": 56}, rpc, snapshot)
        with self.assertRaisesRegex(ValueError, "付款和收币"):
            self.service.require_quote_policy({**intent, "recipient": chain["sessionAddress"]}, quote, {"id": 56}, rpc, snapshot)

    def test_role_snapshot_requires_exact_surface_and_onchain_allowance_link(self):
        self.configure()
        chain = self.service._load_config()["chains"]["56"]
        valid = role_snapshot(chain["sessionAddress"], chain["rolesAddress"], chain["stableAddress"])
        self.assertEqual(self.service.verify_role_snapshot(chain, valid), 123)
        with self.assertRaisesRegex(ValueError, "10000"):
            self.service.verify_role_snapshot(chain, role_snapshot(chain["sessionAddress"],
                chain["rolesAddress"], chain["stableAddress"], allowance=False))
        with self.assertRaisesRegex(ValueError, "额外目标"):
            self.service.verify_role_snapshot(chain, role_snapshot(chain["sessionAddress"],
                chain["rolesAddress"], chain["stableAddress"], extra_target=True))

    def test_denied_probe_only_accepts_an_explicit_evm_revert(self):
        self.configure()
        chain = self.service._load_config()["chains"]["56"]
        def unavailable(_network, _method, _params):
            raise ValueError("RPC unavailable")
        with self.assertRaisesRegex(ValueError, "RPC unavailable"):
            self.service._probe_denied(unavailable, {"id": 56}, chain,
                chain["stableAddress"], encode_call("approve(address,uint256)",
                ["address", "uint256"], [chain["sessionAddress"], 1]), "权限过宽")

    def test_submitted_revoke_is_polled_without_rebroadcast(self):
        self.configure()
        tx_hash = "0x" + "cd" * 32
        self.service.mark_revoke("submitted", tx_hash)
        calls = []
        def pending_rpc(_network, method, params):
            calls.append((method, params))
            return None
        status = self.service.revoke_if_due({56: {"id": 56}}, pending_rpc)
        self.assertEqual(status["revokeState"], "submitted")
        self.assertEqual(calls, [("eth_getTransactionReceipt", [tx_hash])])
        def mined_rpc(_network, method, params):
            self.assertEqual(method, "eth_getTransactionReceipt")
            return {"transactionHash": tx_hash, "status": "0x1"}
        status = self.service.revoke_if_due({56: {"id": 56}}, mined_rpc)
        self.assertEqual(status["revokeState"], "revoked")

    def test_send_call_simulates_signs_and_broadcasts_once(self):
        self.configure()
        chain = self.service._load_config()["chains"]["56"]
        rpc = RpcHarness(self.service, chain["safeAddress"], chain["rolesAddress"], chain["stableAddress"])
        data = encode_call("approve(address,uint256)", ["address", "uint256"], [BINANCE_ROUTER, 0])
        tx_hash = self.service.send_call({"id": 56}, rpc, chain, chain["stableAddress"], 0, data)
        self.assertEqual(tx_hash, "0x" + "ab" * 32)
        self.assertEqual(len(rpc.sent), 1)
        self.assertGreater(len(rpc.sent[0]), 100)


if __name__ == "__main__":
    unittest.main()
