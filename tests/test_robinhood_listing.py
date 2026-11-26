"""Robinhood 上币早期信号监控（`robinhood_listing_monitor.py`）的单元测试。

提示词验收标准第 5 条要求：

> 有单元测试覆盖：代币文件解析、OperationScheduled 解码、Safe calldata 解码、
> getAllApprovedTokens 解码、去重和分级。

第 3 条要求：

> 模拟测试：把 PR #9357、#9603 的 head 当作新 PR、回放 8/03 和 8/31 的区块、
> 改动 npm 版本，都能触发对应信号；同一合约同时满足两类时输出 `critical`。

这里的夹具**全部是真实链上 / 真实仓库数据**（`tests/fixtures/`），不是手搓的假 hex ——
手搓 hex 只能证明「我的解码器能解我自己编的数据」，证明不了它能解真网。

夹具来源：

* `operation_scheduled.json` —— 注册合约 `0x7ff4ed…6b93` 的 8 条 `OperationScheduled`
  （块 26953337~26955437，kind = `d50e2949` v3 池子 ×3、`5263548a` v4 池子 ×5）；
* `safe_multisig.json` —— 多签 `0x7d33dd…5ad0` 的 9 条 `SafeMultiSigTransaction`
  （8 条 `to=注册合约`，1 条 `to=金库` 且 calldata 是 `approveTokens`）；
* `get_all_approved_tokens.json` —— 金库 `getAllApprovedTokens()` 的真实返回值（31 个）；
* `pool_tokens.json` —— 其中一个 v4 池子的 `currency0/currency1`；
* `bitgo_hoodethTokens.{ts,js}.snippet` —— BitGo 仓库源文件与 npm 编译产物各一段
  （含 cashcat 与几个 `• Robinhood` 股票代币，用来验「股票代币要能识别出来」）。

**所有测试都不发网络请求**：RPC / HTTP 一律 mock。真网只在 `--backtest` 里用。
"""

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# --- 业务代码在 src/ 下：把它加进 sys.path 才能 `from app.x import y` ---------
import sys as _sys
from pathlib import Path as _Path

_SRC_DIR = _Path(__file__).resolve().parents[1] / "src"
if str(_SRC_DIR) not in _sys.path:
    _sys.path.insert(0, str(_SRC_DIR))
# ---------------------------------------------------------------------------
from app.monitors import robinhood_listing_monitor as rh
FIXTURES = Path(__file__).resolve().parent / "fixtures"

CASHCAT = "0x020bfc650a365f8bb26819deaabf3e21291018b4"
PONS = "0x39dbed3a2bd333467115de45665cc57f813c4571"
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
MAX_UINT = 2 ** 256 - 1


# --------------------------------------------------------------------------- #
# 夹具装载
# --------------------------------------------------------------------------- #
def load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


OPERATION_SCHEDULED = load_json("operation_scheduled.json")
SAFE_MULTISIG = load_json("safe_multisig.json")
APPROVED_RESULT = load_json("get_all_approved_tokens.json")["result"]
POOL_TOKENS = load_json("pool_tokens.json")
BITGO_TS = load_text("bitgo_hoodethTokens.ts.snippet")
BITGO_JS = load_text("bitgo_hoodethTokens.js.snippet")


# --------------------------------------------------------------------------- #
# 1. BitGo 代币文件解析
# --------------------------------------------------------------------------- #
class BitgoTokenParseTest(unittest.TestCase):
    """`parse_bitgo_tokens` —— 源码 TS 与编译 JS 两种写法都要认。"""

    def test_parses_typescript_source(self):
        tokens = rh.parse_bitgo_tokens(BITGO_TS)
        # 夹具给了 4 个完整条目（第 4 个 alab 的后续参数被截断，但四件套已齐全）。
        self.assertEqual(set(tokens), {"skhy", "cashcat", "wdc", "alab"})

    def test_cashcat_fields_are_exact(self):
        meta = rh.parse_bitgo_tokens(BITGO_TS)["cashcat"]
        self.assertEqual(meta["symbol"], "cashcat")
        self.assertEqual(meta["asset"], "hoodeth:cashcat")
        self.assertEqual(meta["name"], "Cash Cat")
        self.assertEqual(meta["contract"], CASHCAT)
        self.assertEqual(meta["decimals"], 18)

    def test_parses_compiled_npm_output(self):
        """编译产物是 ``(0, account_1.erc20Token)('uuid', 'hoodeth:x', …)`` —— 四件套相同。

        这一条很重要：npm 腿上我们拿到的**只有**编译产物，如果正则只认 TS 的
        ``erc20Token(`` 开头，npm 增量检测就永久哑火。
        """
        tokens = rh.parse_bitgo_tokens(BITGO_JS)
        self.assertIn("cashcat", tokens)
        self.assertEqual(tokens["cashcat"]["contract"], CASHCAT)

    def test_source_and_npm_agree_on_shared_tokens(self):
        ts_tokens = rh.parse_bitgo_tokens(BITGO_TS)
        js_tokens = rh.parse_bitgo_tokens(BITGO_JS)
        shared = set(ts_tokens) & set(js_tokens)
        self.assertTrue(shared, "两种写法至少要能解出同一个币")
        for symbol in shared:
            self.assertEqual(ts_tokens[symbol]["contract"], js_tokens[symbol]["contract"])
            self.assertEqual(ts_tokens[symbol]["name"], js_tokens[symbol]["name"])

    def test_symbol_reference_alone_is_not_an_addition(self):
        """`UnderlyingAsset['hoodeth:cashcat']` 这种纯引用不能算「新增了一个币」。"""
        text = "UnderlyingAsset['hoodeth:newcoin'], UnderlyingAsset['hoodeth:other']"
        self.assertEqual(rh.parse_bitgo_tokens(text), {})

    def test_ofc_prefix_is_also_parsed(self):
        text = "erc20Token('u', 'ofchoodeth:test', 'Test Token', 6, '0x" + "ab" * 20 + "')"
        tokens = rh.parse_bitgo_tokens(text)
        self.assertEqual(set(tokens), {"test"})
        self.assertEqual(tokens["test"]["decimals"], 6)

    def test_empty_input_is_empty_result(self):
        self.assertEqual(rh.parse_bitgo_tokens(""), {})
        self.assertEqual(rh.parse_bitgo_tokens("<html>502 Bad Gateway</html>"), {})


class StockTokenTest(unittest.TestCase):
    """股票代币必须能被认出来 —— 它们恒 `low`，且不参与 critical 升级。"""

    def test_robinhood_stock_names_are_flagged(self):
        for name in (
            "SK Hynix Inc ADR • Robinhood",
            "Western Digital Corporation • Robinhood",
            "Astera Labs, Inc. • Robinhood",
        ):
            with self.subTest(name=name):
                self.assertTrue(rh.is_stock_token(name))

    def test_normal_tokens_are_not_flagged(self):
        for name in ("Cash Cat", "Pons", "Virtuals Protocol", ""):
            with self.subTest(name=name):
                self.assertFalse(rh.is_stock_token(name))

    def test_snippets_contain_both_kinds(self):
        """夹具本身要覆盖两种币，否则上面两条断言就是空转。"""
        names = [m["name"] for m in rh.parse_bitgo_tokens(BITGO_TS).values()]
        self.assertTrue(any(rh.is_stock_token(n) for n in names))
        self.assertTrue(any(not rh.is_stock_token(n) for n in names))


# --------------------------------------------------------------------------- #
# 2. OperationScheduled 解码
# --------------------------------------------------------------------------- #
class OperationScheduledDecodeTest(unittest.TestCase):
    """注册合约的 ``OperationScheduled(bytes32 kind, address target, bytes data)``。"""

    def setUp(self):
        self.decoded = [rh.decode_operation_scheduled(e) for e in OPERATION_SCHEDULED]

    def test_every_fixture_entry_decodes(self):
        self.assertEqual(len(OPERATION_SCHEDULED), 8)
        self.assertTrue(all(d is not None for d in self.decoded))

    def test_kinds_match_the_two_known_selectors(self):
        kinds = [d["kind"] for d in self.decoded]
        self.assertEqual(kinds.count(rh.KIND_V3_POOL), 3)
        self.assertEqual(kinds.count(rh.KIND_V4_POOL), 5)

    def test_target_and_tx_are_extracted(self):
        first = self.decoded[0]
        self.assertEqual(first["target"], "0x4b0c312ffbb068f6a0bea128759e35d94b94d0e1")
        self.assertTrue(first["tx"].startswith("0xb1b31bf2af179947"))
        self.assertEqual(first["block"], 26953337)

    def test_v4_payload_carries_the_pool_key(self):
        """v4 的 payload 是完整 PoolKey（5 个字），前后两个是 currency0/1。"""
        v4 = [d for d in self.decoded if d["kind"] == rh.KIND_V4_POOL]
        self.assertTrue(v4)
        for entry in v4:
            self.assertEqual(len(entry["payload"]), 320, "PoolKey = 5 个字 = 160 字节")
            tokens = rh.decode_v4_pool_tokens(entry["payload"])
            self.assertEqual(len(tokens), 2)
            self.assertIn(CASHCAT, tokens)

    def test_v3_payload_is_not_a_pool_key(self):
        """v3 的 payload 只有 20 字节（一个地址），不能拿来当 PoolKey 用。

        这正是为什么要按 ``kind`` 分流：v3 的代币得回头 `eth_call` 池子的
        ``token0()/token1()``，v4 的才在 payload 里。
        """
        v3 = [d for d in self.decoded if d["kind"] == rh.KIND_V3_POOL][0]
        self.assertEqual(len(v3["payload"]), 40)
        self.assertEqual(rh.decode_v4_pool_tokens(v3["payload"]), [])

    def test_short_data_returns_none(self):
        self.assertIsNone(rh.decode_operation_scheduled({"data": "0x"}))
        self.assertIsNone(rh.decode_operation_scheduled({"data": "0x" + "00" * 60}))
        self.assertIsNone(rh.decode_operation_scheduled({}))

    def test_dynamic_bytes_offset_is_word_two(self):
        """``bytes data`` 的偏移在第 3 个字（下标 2）—— 拿下标 1 会解出 ``00000000``。"""
        v3 = [d for d in self.decoded if d["kind"] == rh.KIND_V3_POOL][0]
        self.assertNotIn(v3["payload"], ("", "0" * 64))
        self.assertEqual(len(v3["payload"]), 40)


class V4PoolTokensTest(unittest.TestCase):
    """`decode_v4_pool_tokens` 对应夹具 `pool_tokens.json`。"""

    def test_matches_the_recorded_pool(self):
        # 每个字是 32 字节，地址左补 24 个 0（不是右补 —— 右补会解出别的地址）。
        payload = "0" * 24 + POOL_TOKENS["token0"][2:] + "0" * 24 + POOL_TOKENS["token1"][2:]
        self.assertEqual(
            rh.decode_v4_pool_tokens(payload),
            [POOL_TOKENS["token0"], POOL_TOKENS["token1"]],
        )

    def test_short_payload_returns_empty(self):
        self.assertEqual(rh.decode_v4_pool_tokens(""), [])
        self.assertEqual(rh.decode_v4_pool_tokens("00" * 32), [])


# --------------------------------------------------------------------------- #
# 3. Safe calldata 解码
# --------------------------------------------------------------------------- #
class SafeDecodeTest(unittest.TestCase):
    """多签的 ``SafeMultiSigTransaction``：取 ``to`` 与内层 calldata。"""

    def setUp(self):
        self.decoded = [rh.decode_safe_transaction(e) for e in SAFE_MULTISIG]

    def test_every_fixture_entry_decodes(self):
        self.assertEqual(len(SAFE_MULTISIG), 9)
        self.assertTrue(all(d is not None for d in self.decoded))

    def test_targets_are_registry_or_vault(self):
        targets = [d["to"] for d in self.decoded]
        self.assertEqual(targets.count(rh.WM_REGISTRY), 8)
        self.assertEqual(targets.count(rh.WM_VAULT), 1)

    def test_dynamic_bytes_offset_is_word_two_not_word_one(self):
        """把偏移取成 word1（value 字段）会解出空的 ``00000000``。

        这是实际踩过的坑：Safe 的参数是 ``to, value, data, …``，value 恒 0，所以
        错取 word1 时 ``dynamic_bytes_at`` 得到偏移 0 → 直接返回空串，
        ``approveTokens`` 一个 token 都解不出来，**而且不报错**。
        """
        vault_entry = next(d for d in self.decoded if d["to"] == rh.WM_VAULT)
        self.assertTrue(vault_entry["calldata"].startswith(rh.SEL_APPROVE_TOKENS[2:]))

    def test_approve_tokens_yields_six_entries(self):
        vault_entry = next(d for d in self.decoded if d["to"] == rh.WM_VAULT)
        entries = rh.decode_approve_tokens(vault_entry["calldata"])
        self.assertEqual(len(entries), 6, "3 个 token × 先 approve 0 再 approve max")

    def test_approve_tokens_include_cashcat_at_max(self):
        vault_entry = next(d for d in self.decoded if d["to"] == rh.WM_VAULT)
        entries = rh.decode_approve_tokens(vault_entry["calldata"])
        by_token = {}
        for _spender, token, amount in entries:
            by_token.setdefault(token, []).append(amount)
        self.assertIn(CASHCAT, by_token)
        self.assertEqual(sorted(by_token[CASHCAT]), [0, MAX_UINT])

    def test_spender_is_a_single_address(self):
        vault_entry = next(d for d in self.decoded if d["to"] == rh.WM_VAULT)
        spenders = {s for s, _t, _a in rh.decode_approve_tokens(vault_entry["calldata"])}
        self.assertEqual(len(spenders), 1, "同一个 spender 批准多个 token")

    def test_zero_amounts_are_kept_for_the_caller_to_skip(self):
        """解码层**原样保留** amount=0（重置），过滤是调用方的事 —— 这样职责清晰，
        也方便以后想改成「重置也算动作」时不用动解码。"""
        vault_entry = next(d for d in self.decoded if d["to"] == rh.WM_VAULT)
        entries = rh.decode_approve_tokens(vault_entry["calldata"])
        self.assertEqual(sum(1 for _s, _t, a in entries if a == 0), 3)
        self.assertEqual(sum(1 for _s, _t, a in entries if a == MAX_UINT), 3)

    def test_non_approve_calldata_does_not_match(self):
        """其它目标（注册合约）的 calldata 选择器不是 `approveTokens`。
        `scan_chain` 靠选择器前缀拦，不能靠「解出来几个地址」猜。"""
        others = [d for d in self.decoded if d["to"] == rh.WM_REGISTRY]
        self.assertTrue(others)
        for entry in others:
            self.assertFalse(entry["calldata"].startswith(rh.SEL_APPROVE_TOKENS[2:]))

    def test_short_data_returns_none(self):
        self.assertIsNone(rh.decode_safe_transaction({"data": "0x"}))
        self.assertIsNone(rh.decode_safe_transaction({"data": "0x" + "00" * 60}))


# --------------------------------------------------------------------------- #
# 4. getAllApprovedTokens 解码
# --------------------------------------------------------------------------- #
class ApprovedTokensDecodeTest(unittest.TestCase):
    """金库的 ``getAllApprovedTokens()`` → `address[]`。"""

    def setUp(self):
        self.tokens = rh.decode_address_array(APPROVED_RESULT)

    def test_count_matches_the_abi_length_word(self):
        self.assertEqual(len(self.tokens), 31)

    def test_known_tokens_are_present(self):
        self.assertIn(CASHCAT, self.tokens)
        self.assertIn(PONS, self.tokens)

    def test_first_entries_are_the_base_tokens(self):
        """前两个是 USDG / WETH —— 它们必须在 `BASE_TOKENS` 里，否则每次快照
        都会把这两个基础币当「新批准」弹一遍。"""
        self.assertEqual(self.tokens[0], USDG)
        self.assertEqual(self.tokens[1], WETH)
        base_overlap = [t for t in self.tokens if t in rh.BASE_TOKENS]
        self.assertEqual(set(base_overlap), {USDG, WETH})

    def test_all_addresses_are_lowercase_and_checksum_free(self):
        for token in self.tokens:
            with self.subTest(token=token):
                self.assertRegex(token, r"^0x[0-9a-f]{40}$")

    def test_empty_or_broken_result_returns_empty(self):
        self.assertEqual(rh.decode_address_array(""), [])
        self.assertEqual(rh.decode_address_array("0x"), [])
        self.assertEqual(rh.decode_address_array("0x" + "00" * 32), [])


class StringDecodeTest(unittest.TestCase):
    """`symbol()` / `name()` 的返回值解码（动态 string 与 bytes32 两种历史写法）。"""

    def _encode_dynamic(self, text: str) -> str:
        raw = text.encode()
        padded = raw.hex().ljust(((len(raw) + 31) // 32) * 64, "0")
        return "0x" + f"{32:064x}" + f"{len(raw):064x}" + padded

    def test_dynamic_string(self):
        self.assertEqual(rh.decode_string(self._encode_dynamic("Cash Cat")), "Cash Cat")

    def test_bytes32_style(self):
        word = "Cash Cat".encode().hex().ljust(64, "0")
        self.assertEqual(rh.decode_string("0x" + word), "Cash Cat")

    def test_garbage_returns_empty_string(self):
        self.assertEqual(rh.decode_string(""), "")
        self.assertEqual(rh.decode_string("0x"), "")

    def test_nul_padded_word_is_cleaned(self):
        """`symbol()` 是别人合约上的函数：有的币只返回一串 ``\\x00``。

        不清洗的话，弹窗标题就是 24 个豆腐块，也没法拿去跟榜单做符号匹配。
        """
        self.assertEqual(rh.decode_string("0x" + "00" * 32), "")

    def test_control_characters_are_dropped(self):
        raw = "CA\x00\x07SH\nCAT".encode()
        word = raw.hex().ljust(64, "0")
        self.assertEqual(rh.decode_string("0x" + word), "CASHCAT")

    def test_overlong_symbol_is_truncated(self):
        long_name = "A" * 200
        self.assertEqual(len(rh.decode_string(self._encode_dynamic(long_name))), 48)


class ShortAddressTest(unittest.TestCase):
    def test_shortens_with_ellipsis(self):
        self.assertEqual(
            rh.short_address("0x020bfc650a365f8bb26819deaabf3e21291018b4"),
            "0x020bfc…18b4",
        )

    def test_leaves_short_input_alone(self):
        self.assertEqual(rh.short_address("0x1234"), "0x1234")
        self.assertEqual(rh.short_address(""), "")


# --------------------------------------------------------------------------- #
# 5. 分级（SEVERITY / critical 升级）
# --------------------------------------------------------------------------- #
class SeverityTest(unittest.TestCase):
    """分级规则：BitGo 单独 `high`、Wintermute 单独 `high`、两类都命中 → `critical`。"""

    def setUp(self):
        rh._LEDGER = {}

    def tearDown(self):
        rh._LEDGER = {}

    def _signal(self, type_: str, contract: str = CASHCAT, name: str = "Cash Cat"):
        return rh.make_signal(
            type_=type_, token="CASHCAT", contract=contract, token_name=name,
            source_url="https://example.invalid/x", occurred_ms=1_700_000_000_000,
        )

    def test_base_severity_table(self):
        self.assertEqual(rh.SEVERITY_BY_TYPE["bitgo_pr_new_hoodeth_token"], "high")
        self.assertEqual(rh.SEVERITY_BY_TYPE["wintermute_registry_pool_approval_new_token"], "high")
        self.assertEqual(rh.SEVERITY_BY_TYPE["wintermute_new_withdraw_recipient"], "medium")

    def test_single_family_stays_high(self):
        signal = self._signal("wintermute_registry_pool_approval_new_token")
        rh.memory_signal(
            contract=CASHCAT, family="wintermute", occurred_ms=1_700_000_000_000,
            source_url="u", type_="wintermute_registry_pool_approval_new_token",
        )
        out = rh.apply_severity([signal])
        self.assertEqual(out[0]["severity"], "high")
        self.assertNotIn("crossSignal", out[0]["extra"])

    def test_both_families_become_critical(self):
        rh.memory_signal(
            contract=CASHCAT, family="bitgo", occurred_ms=1_699_000_000_000,
            source_url="https://github.com/BitGo/BitGoJS/pull/9357",
            type_="bitgo_pr_new_hoodeth_token",
        )
        rh.memory_signal(
            contract=CASHCAT, family="wintermute", occurred_ms=1_700_000_000_000,
            source_url="https://robinhoodchain.blockscout.com/tx/0xaa",
            type_="wintermute_vault_token_approval_new_token",
        )
        out = rh.apply_severity([self._signal("wintermute_vault_token_approval_new_token")])
        self.assertEqual(out[0]["severity"], "critical")
        cross = out[0]["extra"]["crossSignal"]
        self.assertEqual(cross["family"], "bitgo")
        self.assertEqual(cross["type"], "bitgo_pr_new_hoodeth_token")
        self.assertIn("9357", cross["source_url"])

    def test_upgrade_works_in_either_order(self):
        """链上先到、GitHub 后到，也要升级成 critical（不能只看「后到的那个」）。"""
        rh.memory_signal(
            contract=CASHCAT, family="wintermute", occurred_ms=1_699_000_000_000,
            source_url="u1", type_="wintermute_registry_pool_approval_new_token",
        )
        rh.memory_signal(
            contract=CASHCAT, family="bitgo", occurred_ms=1_700_000_000_000,
            source_url="u2", type_="bitgo_master_new_hoodeth_token",
        )
        out = rh.apply_severity([self._signal("bitgo_master_new_hoodeth_token")])
        self.assertEqual(out[0]["severity"], "critical")
        self.assertEqual(out[0]["extra"]["crossSignal"]["family"], "wintermute")

    def test_different_contracts_do_not_upgrade_each_other(self):
        rh.memory_signal(
            contract=PONS, family="wintermute", occurred_ms=1_700_000_000_000,
            source_url="u", type_="wintermute_registry_pool_approval_new_token",
        )
        out = rh.apply_severity([self._signal("bitgo_pr_new_hoodeth_token", contract=CASHCAT)])
        self.assertEqual(out[0]["severity"], "high")

    def test_stock_tokens_are_always_low_and_never_upgrade(self):
        stock = "0x84cab63bc87912e71ad199ff14a0ba45de68fef8"
        rh.memory_signal(
            contract=stock, family="bitgo", occurred_ms=1_699_000_000_000,
            source_url="u1", type_="bitgo_pr_new_hoodeth_token",
        )
        rh.memory_signal(
            contract=stock, family="wintermute", occurred_ms=1_700_000_000_000,
            source_url="u2", type_="wintermute_registry_pool_approval_new_token",
        )
        out = rh.apply_severity([
            self._signal("wintermute_registry_pool_approval_new_token",
                         contract=stock, name="SK Hynix Inc ADR • Robinhood")
        ])
        self.assertEqual(out[0]["severity"], "low")
        self.assertNotIn("crossSignal", out[0]["extra"], "股票代币不参与 critical 升级")

    def test_ledger_keeps_the_earliest_occurrence(self):
        rh.memory_signal(contract=CASHCAT, family="bitgo", occurred_ms=2_000,
                         source_url="later", type_="bitgo_pr_new_hoodeth_token")
        rh.memory_signal(contract=CASHCAT, family="bitgo", occurred_ms=1_000,
                         source_url="earlier", type_="bitgo_pr_new_hoodeth_token")
        self.assertEqual(rh._LEDGER[CASHCAT]["bitgo"]["occurredAt"], 1_000)
        self.assertEqual(rh._LEDGER[CASHCAT]["bitgo"]["url"], "earlier")

    def test_empty_contract_is_not_remembered(self):
        rh.memory_signal(contract="", family="bitgo", occurred_ms=1,
                         source_url="u", type_="bitgo_pr_new_hoodeth_token")
        self.assertEqual(rh._LEDGER, {})

    def test_every_emitted_type_has_a_severity(self):
        """任何新加的信号类型都必须先登记严重度，否则会静默落成 `medium`。"""
        source = Path(rh.__file__).read_text(encoding="utf-8")
        used = set(re.findall(r'type_="([a-z][a-z0-9_]+)"', source))
        self.assertTrue(used, "正则没匹配到东西，说明代码写法变了，这条断言要修")
        missing = used - set(rh.SEVERITY_BY_TYPE)
        self.assertEqual(missing, set(), f"这些类型没有登记严重度：{sorted(missing)}")

    def test_bitgo_and_wintermute_type_sets_are_disjoint(self):
        self.assertEqual(rh.BITGO_TYPES & rh.WINTERMUTE_TYPES, set())
        # 剩下的是低置信的 eth 源 —— 它不参与 critical 升级（两侧都不是 Wintermute 链上合约）。
        remainder = set(rh.SEVERITY_BY_TYPE) - rh.BITGO_TYPES - rh.WINTERMUTE_TYPES
        self.assertEqual(remainder, {"eth_wintermute_to_robinhood_new_token"})


# --------------------------------------------------------------------------- #
# 6. 状态文件
# --------------------------------------------------------------------------- #
class StateFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_is_a_fresh_state(self):
        state = rh.load_state(self.path)
        self.assertFalse(state["baseline_done"])
        self.assertEqual(state["chain"]["last_block"], 0)

    def test_roundtrip(self):
        state = rh.load_state(self.path)
        state["baseline_done"] = True
        state["chain"]["last_block"] = 26955437
        rh.save_state(self.path, state)
        again = rh.load_state(self.path)
        self.assertTrue(again["baseline_done"])
        self.assertEqual(again["chain"]["last_block"], 26955437)

    def test_corrupt_file_raises_source_error(self):
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(rh.SourceError):
            rh.load_state(self.path)

    def test_non_object_root_raises_source_error(self):
        self.path.write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertRaises(rh.SourceError):
            rh.load_state(self.path)

    def test_unknown_keys_are_preserved(self):
        """老状态文件里有新版不认识的分区时，不能被整份丢掉。"""
        self.path.write_text(json.dumps({"version": 1, "future_section": {"x": 1}}), encoding="utf-8")
        state = rh.load_state(self.path)
        self.assertEqual(state["future_section"], {"x": 1})

    def test_legacy_state_infers_per_source_baseline(self):
        """只有全局 `baseline_done` 的老文件，要按游标反推哪些源建过基线。

        反推错了的后果很具体：全部当「没建过」→ 下一轮所有存量代币都算新币 → 几百条误报。
        """
        self.path.write_text(json.dumps({
            "baseline_done": True,
            "github": {"master_sha": "a" * 40},
            "chain": {"last_block": 0},
        }), encoding="utf-8")
        state = rh.load_state(self.path)
        self.assertEqual(state["baseline"], {"github": True})
        self.assertNotIn("chain", state["baseline"], "水位是 0 说明没扫过，得重建基线")

    def test_existing_baseline_section_is_not_overwritten(self):
        self.path.write_text(json.dumps({
            "baseline_done": True,
            "baseline": {"chain": True},
            "github": {"master_sha": "a" * 40},
        }), encoding="utf-8")
        self.assertEqual(rh.load_state(self.path)["baseline"], {"chain": True})


# --------------------------------------------------------------------------- #
# 7. 去重（scan_chain / scan_github）
# --------------------------------------------------------------------------- #
class _FakeRpc:
    """一个按方法分发的假 RPC。默认返回「空链」，用例按需补上。"""

    def __init__(self, *, head=27_000_000, block_time=1_700_000_000, calls=None):
        self.head = head
        self.block_time = block_time
        self.calls = calls if calls is not None else []
        self.eth_call: dict[str, str] = {}
        self.logs: list[dict] = []

    def __call__(self, method, params, **_kwargs):
        self.calls.append((method, params))
        if method == "eth_blockNumber":
            return hex(self.head)
        if method == "eth_getBlockByNumber":
            return {"timestamp": hex(self.block_time), "number": params[0]}
        if method == "eth_call":
            key = (params[0].get("data") or "")[:10]
            return self.eth_call.get(key, "0x")
        if method == "eth_getLogs":
            return list(self.logs)
        raise AssertionError(f"未预期的 RPC 方法：{method}")


def _patch_rpc(fake, *, logs=None):
    """把 `rpc` 和 `fetch_logs` 一起顶掉（`fetch_logs` 内部也调 rpc）。"""
    patches = [mock.patch.object(rh, "rpc", fake)]
    if logs is not None:
        patches.append(mock.patch.object(rh, "fetch_logs", lambda *a, **k: list(logs)))
    return patches


class ScanChainDedupTest(unittest.TestCase):
    """`scan_chain` 的去重：同一合约只报一次，重启/重跑不得重复。"""

    def setUp(self):
        rh._LEDGER = {}
        self.cfg = rh.Config(state_path=Path(tempfile.gettempdir()) / "unused.json")
        self.cfg.chain_from_block = 26_950_000
        self.registry_logs = [e for e in OPERATION_SCHEDULED if e["address"] == rh.WM_REGISTRY]
        self.vault_logs = [e for e in SAFE_MULTISIG if e["address"] == rh.WM_SAFE]

    def tearDown(self):
        rh._LEDGER = {}

    def _run(self, fake, state, *, baseline):
        ctx = _patch_rpc(fake, logs=fake.logs)
        for patcher in ctx:
            patcher.start()
        try:
            with mock.patch.object(rh, "log", lambda *a, **k: None):
                return rh.scan_chain(state, self.cfg, baseline)
        finally:
            for patcher in ctx:
                patcher.stop()

    def test_baseline_records_everything_without_signals(self):
        """首跑：建基线只写状态，一条信号都不许出（否则第一次上线就是几百条误报）。"""
        fake = _FakeRpc()
        fake.logs = self.registry_logs + self.vault_logs
        fake.eth_call[rh.SEL_GET_ALL_APPROVED] = APPROVED_RESULT
        fake.eth_call[rh.SEL_TOKEN0] = "0x" + "0" * 24 + CASHCAT[2:]
        fake.eth_call[rh.SEL_TOKEN1] = "0x" + "0" * 24 + USDG[2:]

        signals, state = self._run(fake, rh.empty_state()["chain"], baseline=True)
        self.assertEqual(signals, [])
        self.assertEqual(state["last_block"], fake.head)
        # v4 池子里出现的 cashcat 必须进 known_tokens
        self.assertIn(CASHCAT, state["known_tokens"])
        # 白名单快照也要落下来
        self.assertEqual(len(state["approved_tokens"]), 31)

    def test_second_run_reports_nothing_new(self):
        fake = _FakeRpc()
        fake.logs = self.registry_logs + self.vault_logs
        fake.eth_call[rh.SEL_GET_ALL_APPROVED] = APPROVED_RESULT
        fake.eth_call[rh.SEL_TOKEN0] = "0x" + "0" * 24 + CASHCAT[2:]
        fake.eth_call[rh.SEL_TOKEN1] = "0x" + "0" * 24 + USDG[2:]

        _signals, state = self._run(fake, rh.empty_state()["chain"], baseline=True)
        fake.logs = []          # 链上没有新块
        fake.head += 100
        signals, state2 = self._run(fake, state, baseline=False)
        self.assertEqual(signals, [], "第二轮不该有任何新信号")
        self.assertEqual(state2["known_tokens"].keys(), state["known_tokens"].keys())

    def test_replaying_the_same_logs_does_not_redeliver(self):
        """把旧日志再喂一遍（模拟 last_block 丢了、重扫）也不能重复报。"""
        fake = _FakeRpc()
        fake.logs = self.vault_logs
        fake.eth_call[rh.SEL_GET_ALL_APPROVED] = APPROVED_RESULT
        fake.eth_call[rh.SEL_SYMBOL] = "0x" + f"{3:064x}" + "PONS".encode().hex().ljust(64, "0")

        _s, state = self._run(fake, rh.empty_state()["chain"], baseline=True)
        # 基线之后人为把 last_block 归零 → 下一轮会重扫同一段
        state["last_block"] = 0
        signals, _state2 = self._run(fake, state, baseline=False)
        self.assertEqual(signals, [])

    def test_new_approved_token_fires_exactly_once(self):
        fake = _FakeRpc()
        fake.logs = self.vault_logs
        fake.eth_call[rh.SEL_GET_ALL_APPROVED] = APPROVED_RESULT
        fake.eth_call[rh.SEL_SYMBOL] = "0x" + f"{4:064x}" + "NEWT".encode().hex().ljust(64, "0")

        _s, state = self._run(fake, rh.empty_state()["chain"], baseline=True)

        newcomer = "0x" + "cf" * 20
        extended = rh.decode_address_array(APPROVED_RESULT) + [newcomer]
        fake.eth_call[rh.SEL_GET_ALL_APPROVED] = (
            "0x" + f"{32:064x}" + f"{len(extended):064x}"
            + "".join("0" * 24 + t[2:] for t in extended)
        )
        signals, state2 = self._run(fake, state, baseline=False)
        hits = [s for s in signals if s["contract"] == newcomer]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["type"], "wintermute_getAllApprovedTokens_added")

        # 再跑一次：已经进 approved_tokens 了，不能再报
        signals3, _state3 = self._run(fake, state2, baseline=False)
        self.assertEqual([s for s in signals3 if s["contract"] == newcomer], [])

    def test_base_tokens_never_fire(self):
        """USDG / WETH 在快照里恒存在，绝不能当新批准。"""
        fake = _FakeRpc()
        fake.logs = []
        fake.eth_call[rh.SEL_GET_ALL_APPROVED] = APPROVED_RESULT
        _s, state = self._run(fake, rh.empty_state()["chain"], baseline=True)
        fake.eth_call[rh.SEL_GET_ALL_APPROVED] = "0x"
        signals, _state2 = self._run(fake, state, baseline=False)
        self.assertEqual([s for s in signals if s["contract"] in rh.BASE_TOKENS], [])

    def test_approved_tokens_failure_does_not_discard_event_signals(self):
        """`getAllApprovedTokens` 读失败只能跳过这条腿，事件里已经拿到的不能白扔。

        这是实际踩过的坑：原来直接 `raise`，整个 chain 源算失败、状态不推进，
        本轮扫到的事件全丢（下一轮重扫又丢一遍，永远卡在这条腿）。
        """
        fake = _FakeRpc()
        fake.logs = self.vault_logs

        def flaky(method, params, **kwargs):
            if method == "eth_call" and (params[0].get("data") or "").startswith(rh.SEL_GET_ALL_APPROVED):
                raise rh.SourceError("boom")
            return _FakeRpc.__call__(fake, method, params, **kwargs)

        fake.eth_call[rh.SEL_SYMBOL] = "0x" + f"{3:064x}" + "PONS".encode().hex().ljust(64, "0")
        ctx = [mock.patch.object(rh, "rpc", flaky), mock.patch.object(rh, "fetch_logs", lambda *a, **k: fake.logs)]
        for patcher in ctx:
            patcher.start()
        try:
            with mock.patch.object(rh, "log", lambda *a, **k: None):
                signals, state = rh.scan_chain(rh.empty_state()["chain"], self.cfg, baseline=False)
        finally:
            for patcher in ctx:
                patcher.stop()

        self.assertEqual(state["approved_tokens"], [], "失败的腿不能推进状态")
        # 金库 approveTokens 里 3 个 token 都是新批准的（amount = max 那三条），
        # 其中 cashcat 必须在信号里 —— 这是「事件腿没被白名单腿拖死」的证据。
        contracts = {s["contract"] for s in signals}
        self.assertIn(CASHCAT, contracts, "事件腿解出来的 cashcat 必须仍然产出信号")
        self.assertEqual(state["last_block"], fake.head, "事件腿成功了，水位要推进")


class ScanGithubDedupTest(unittest.TestCase):
    """`scan_github`：新 PR head / master 新增 / npm 版本变化各自触发，重复不报。"""

    def setUp(self):
        rh._LEDGER = {}
        self.cfg = rh.Config(state_path=Path(tempfile.gettempdir()) / "unused2.json")
        self.master_tokens = {"cashcat": {"symbol": "cashcat", "asset": "hoodeth:cashcat",
                                          "name": "Cash Cat", "contract": CASHCAT, "decimals": 18}}
        self.pr_tokens = dict(self.master_tokens)
        self.pr_tokens["pons"] = {"symbol": "pons", "asset": "hoodeth:pons",
                                  "name": "Pons", "contract": PONS, "decimals": 18}
        self.npm_tokens = dict(self.master_tokens)

    def tearDown(self):
        rh._LEDGER = {}

    def _run(self, state, *, baseline, refs=None, npm_version="59.31.0"):
        refs = refs if refs is not None else {
            "refs/heads/master": "a" * 40,
            "refs/pull/9603/head": "b" * 40,
            "refs/tags/@bitgo/statics@59.31.0": "c" * 40,
        }

        def fetch_tokens(sha, **_k):
            return self.master_tokens if sha == "a" * 40 else self.pr_tokens

        response = mock.Mock()
        response.json.return_value = {"version": npm_version}
        response.text = BITGO_TS

        with mock.patch.object(rh, "log", lambda *a, **k: None), \
                mock.patch.object(rh, "ls_remote", lambda *a, **k: refs), \
                mock.patch.object(rh, "fetch_bitgo_tokens_at", fetch_tokens), \
                mock.patch.object(rh, "fetch_npm_tokens", lambda *a, **k: self.npm_tokens), \
                mock.patch.object(rh, "http_get", lambda *a, **k: response):
            return rh.scan_github(state, self.cfg, baseline)

    def test_baseline_learns_without_signals(self):
        signals, state = self._run(rh.empty_state()["github"], baseline=True)
        self.assertEqual(signals, [])
        self.assertEqual(state["master_sha"], "a" * 40)
        self.assertIn("cashcat", state["known_tokens"])

    def test_new_pr_head_fires_for_the_token_it_adds(self):
        """把 PR #9603 的 head 当成一个从没见过的 PR —— 它新增的 pons 要报出来。"""
        _s, state = self._run(rh.empty_state()["github"], baseline=True)
        state["pr_heads"] = {}     # 假装这个 PR 从没被记录过
        signals, state2 = self._run(state, baseline=False)
        pr_hits = [s for s in signals if s["type"] == "bitgo_pr_new_hoodeth_token"]
        self.assertEqual(len(pr_hits), 1)
        self.assertEqual(pr_hits[0]["contract"], PONS)
        self.assertEqual(pr_hits[0]["extra"]["pr"], 9603)
        self.assertEqual(state2["pr_heads"]["9603"], "b" * 40)

    def test_same_pr_head_twice_does_not_refire(self):
        _s, state = self._run(rh.empty_state()["github"], baseline=True)
        state["pr_heads"] = {}
        _signals, state2 = self._run(state, baseline=False)
        signals3, _state3 = self._run(state2, baseline=False)
        self.assertEqual([s for s in signals3 if s["type"] == "bitgo_pr_new_hoodeth_token"], [])

    def test_pr_that_only_touches_master_tokens_is_silent(self):
        """PR 里改的是 master 已有的币（比如只改精度），不能当成新币报。"""
        self.pr_tokens = dict(self.master_tokens)
        _s, state = self._run(rh.empty_state()["github"], baseline=True)
        state["pr_heads"] = {}
        signals, _state2 = self._run(state, baseline=False)
        self.assertEqual([s for s in signals if s["type"] == "bitgo_pr_new_hoodeth_token"], [])

    def test_npm_version_bump_fires_only_for_new_tokens(self):
        _s, state = self._run(rh.empty_state()["github"], baseline=True)
        self.npm_tokens = dict(self.pr_tokens)      # 新版本里多了 pons
        signals, state2 = self._run(state, baseline=False, npm_version="59.32.0")
        npm_hits = [s for s in signals if s["type"] == "npm_statics_publish_new_hoodeth_token"]
        self.assertEqual([s["contract"] for s in npm_hits], [PONS])
        self.assertEqual(state2["npm_latest"], "59.32.0")

        # 版本没变 → 不再报
        signals3, _state3 = self._run(state2, baseline=False, npm_version="59.32.0")
        self.assertEqual([s for s in signals3 if s["type"].startswith("npm_")], [])

    def test_stock_tokens_are_not_reported_from_npm(self):
        """npm 编译产物里股票代币有一大堆，报出来就是纯噪音。"""
        stock = {"skhy": {"symbol": "skhy", "asset": "hoodeth:skhy",
                          "name": "SK Hynix Inc ADR • Robinhood",
                          "contract": "0x84cab63bc87912e71ad199ff14a0ba45de68fef8", "decimals": 18}}
        _s, state = self._run(rh.empty_state()["github"], baseline=True)
        self.npm_tokens = dict(self.master_tokens, **stock)
        signals, _state2 = self._run(state, baseline=False, npm_version="59.32.0")
        self.assertEqual([s for s in signals if s["type"].startswith("npm_")], [])

    def test_unprocessed_prs_do_not_get_their_head_recorded(self):
        """基线一轮只处理 `max_pr_changes` 个 PR。

        没处理过的 PR **不能**记 head —— 记了就等于「看过了」，以后它永远不再被检查，
        而它可能正是带着新币的那个。这是实际踩过的坑。
        """
        self.cfg.max_pr_changes = 1
        refs = {
            "refs/heads/master": "a" * 40,
            "refs/pull/9602/head": "d" * 40,
            "refs/pull/9603/head": "b" * 40,
        }
        _signals, state = self._run(rh.empty_state()["github"], baseline=True, refs=refs)
        recorded = set(state["pr_heads"])
        self.assertEqual(len(recorded), 1, "只处理了一个 PR，就只能记一个 head")
        # 窗口是 300，两个 PR 都在窗口内
        self.assertEqual(state["pr_max"], 9603)

    def test_source_failure_leaves_state_untouched(self):
        """`ls_remote` 失败 → SourceError 冒到 `run()`，由它决定不推进状态。"""
        state = rh.empty_state()["github"]
        with mock.patch.object(rh, "log", lambda *a, **k: None), \
                mock.patch.object(rh, "ls_remote", side_effect=rh.SourceError("boom")):
            with self.assertRaises(rh.SourceError):
                rh.scan_github(state, self.cfg, False)


class RunOrchestrationTest(unittest.TestCase):
    """`run()`：单源失败只影响它自己，其余源照常输出，失败源状态不推进。"""

    def setUp(self):
        rh._LEDGER = {}
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = rh.Config(state_path=Path(self.tmp.name) / "state.json")
        self.cfg.enable_eth = False

    def tearDown(self):
        rh._LEDGER = {}
        self.tmp.cleanup()

    def test_partial_failure_keeps_the_other_source(self):
        def github_fails(*_a, **_k):
            raise rh.SourceError("github 挂了")

        def chain_ok(section, _cfg, _baseline):
            return [rh.make_signal(type_="wintermute_new_withdraw_recipient",
                                   token="0xabc", contract="")], dict(section, last_block=123)

        with mock.patch.object(rh, "log", lambda *a, **k: None), \
                mock.patch.object(rh, "scan_github", github_fails), \
                mock.patch.object(rh, "scan_chain", chain_ok):
            signals, outcome = rh.run(self.cfg)

        self.assertEqual(len(signals), 1)
        self.assertTrue(outcome["github"].startswith("failed"))
        self.assertTrue(outcome["chain"].startswith("ok"))
        state = rh.load_state(self.cfg.state_path)
        self.assertEqual(state["chain"]["last_block"], 123, "成功的源要推进")
        self.assertEqual(state["github"]["master_sha"], "", "失败的源不许推进")
        # 基线是按源记的：github 挂了 → 它下次还要重建基线，否则它的 known_tokens
        # 是空的、下一轮会把全部存量代币当新币报一遍。
        self.assertTrue(state["baseline"]["chain"])
        self.assertFalse(state["baseline"].get("github"), "失败的源不能算建过基线")
        self.assertFalse(state["baseline_done"], "还有源没建成基线，汇总标记不能是真")

    def test_failed_source_retries_its_baseline_next_time(self):
        """上一轮 github 失败，这一轮它必须仍走基线模式（chain 则正常出信号）。"""
        state = rh.empty_state()
        state["baseline"] = {"chain": True}
        state["chain"]["last_block"] = 500
        rh.save_state(self.cfg.state_path, state)

        seen: dict[str, bool] = {}

        def spy(name, signals=()):
            def inner(section, _cfg, baseline):
                seen[name] = baseline
                return list(signals), dict(section, last_block=999)
            return inner

        with mock.patch.object(rh, "log", lambda *a, **k: None), \
                mock.patch.object(rh, "scan_github", spy("github")), \
                mock.patch.object(rh, "scan_chain", spy("chain")):
            rh.run(self.cfg)

        self.assertTrue(seen["github"], "github 上一轮失败了，这轮还得建基线")
        self.assertFalse(seen["chain"], "chain 已经建好了，这轮正常找新信号")

    def test_baseline_flag_is_set_only_when_a_source_survived(self):
        def chain_ok(section, _cfg, _baseline):
            return [], dict(section, last_block=1)

        with mock.patch.object(rh, "log", lambda *a, **k: None), \
                mock.patch.object(rh, "scan_github", chain_ok), \
                mock.patch.object(rh, "scan_chain", chain_ok):
            _signals, _outcome = rh.run(self.cfg)
        self.assertTrue(rh.load_state(self.cfg.state_path)["baseline_done"])

    def test_dry_run_does_not_touch_the_state_file(self):
        def chain_ok(section, _cfg, _baseline):
            return [], dict(section, last_block=9)

        self.cfg.dry_run = True
        with mock.patch.object(rh, "log", lambda *a, **k: None), \
                mock.patch.object(rh, "scan_github", chain_ok), \
                mock.patch.object(rh, "scan_chain", chain_ok):
            rh.run(self.cfg)
        self.assertFalse(self.cfg.state_path.exists())

    def test_only_failure_reports_nonzero(self):
        """所有源都失败 → 退出码 2（供外部告警识别），但 stdout 仍是合法 JSON。"""
        with mock.patch.object(rh, "log", lambda *a, **k: None), \
                mock.patch.object(rh, "scan_github", side_effect=rh.SourceError("x")), \
                mock.patch.object(rh, "scan_chain", side_effect=rh.SourceError("y")):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = rh.main(["--state", str(self.cfg.state_path), "--no-eth"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(buffer.getvalue()), [], "stdout 必须始终是合法 JSON 数组")


# --------------------------------------------------------------------------- #
# 8. getLogs 分段 / 劈半重试
# --------------------------------------------------------------------------- #
class RpcUrlTest(unittest.TestCase):
    """`ROBINHOOD_RPC_URLS` 是**覆盖**而不是追加。"""

    def setUp(self):
        rh._RPC_URLS = []

    def tearDown(self):
        rh._RPC_URLS = []

    def test_env_replaces_defaults(self):
        with mock.patch.dict(os.environ, {"ROBINHOOD_RPC_URLS": "http://a, http://b"}):
            self.assertEqual(rh.rpc_urls(), ["http://a", "http://b"])

    def test_defaults_when_env_empty(self):
        with mock.patch.dict(os.environ, {"ROBINHOOD_RPC_URLS": ""}):
            self.assertEqual(rh.rpc_urls(), list(rh.ROBINHOOD_RPCS))

    def test_list_is_cached(self):
        with mock.patch.dict(os.environ, {"ROBINHOOD_RPC_URLS": "http://a"}):
            self.assertEqual(rh.rpc_urls(), ["http://a"])
        self.assertEqual(rh.rpc_urls(), ["http://a"], "第二次不该重读环境变量")


class FetchLogsTest(unittest.TestCase):
    """节点的硬限制是「一次最多 10 万块」，超了报 `-32602 query spans …`。

    这里验的是「失败那段能自己劈半重试」，而不是整轮放弃 —— 公开端点高峰期限制更紧，
    一旦整轮放弃，这一段的信号就永久漏掉。
    """

    def test_halves_on_span_limit_error(self):
        calls: list[tuple[int, int]] = []

        def fake_rpc(method, params, **_k):
            lo = int(params[0]["fromBlock"], 16)
            hi = int(params[0]["toBlock"], 16)
            calls.append((lo, hi))
            if hi - lo + 1 > 60_000:
                raise rh.SourceError("-32602 query spans over only 100000 are allowed")
            return [{"blockNumber": hex(lo), "topics": [], "data": "0x", "address": "0xaa",
                     "transactionHash": "0x" + "11" * 32}]

        with mock.patch.object(rh, "rpc", fake_rpc):
            logs = rh.fetch_logs(["0xaa"], from_block=0, to_block=199_999, chunk=100_000,
                                 workers=1, chunk_min=1_000)

        # 两段各 10 万块 → 各自失败 → 各劈成两段 5 万块 → 4 条。
        self.assertEqual(len(logs), 4)
        covered = sorted(int(e["blockNumber"], 16) for e in logs)
        self.assertEqual(covered, [0, 50_000, 100_000, 150_000], "劈半后不能有缺口也不能重叠")
        self.assertTrue(any(hi - lo + 1 > 60_000 for lo, hi in calls), "第一段应该是大区间")
        self.assertTrue(all(hi - lo + 1 <= 60_000 for lo, hi in calls if (hi - lo + 1) <= 60_000))
        self.assertTrue(any(hi - lo + 1 == 50_000 for lo, hi in calls), "失败后必须缩小区间")

    def test_empty_range_or_addresses_returns_empty(self):
        self.assertEqual(rh.fetch_logs([], from_block=0, to_block=10), [])
        self.assertEqual(rh.fetch_logs(["0xaa"], from_block=10, to_block=0), [])


# --------------------------------------------------------------------------- #
# 9. 端到端：链上 + BitGo 同一合约 → critical（验收标准第 3 条）
# --------------------------------------------------------------------------- #
class CrossSignalEndToEndTest(unittest.TestCase):
    """把两条腿串起来跑一轮 `run()`：cashcat 同时命中 BitGo 与 Wintermute → critical。"""

    def setUp(self):
        rh._LEDGER = {}
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tmp.name) / "state.json"

    def tearDown(self):
        rh._LEDGER = {}
        self.tmp.cleanup()

    def test_cashcat_hits_both_families_and_escalates(self):
        state = rh.empty_state()
        state["baseline_done"] = True
        state["baseline"] = {"github": True, "chain": True}
        state["github"]["master_sha"] = "a" * 40
        state["github"]["npm_latest"] = "59.31.0"
        # known_tokens 留空 → master 腿会把 cashcat 当「master 新增」报出来（bitgo 侧）
        state["github"]["known_tokens"] = {}
        state["github"]["pr_heads"] = {}
        state["chain"]["known_tokens"] = {}
        state["chain"]["approved_tokens"] = []
        rh.save_state(self.state_path, state)

        def fetch_tokens(sha, **_k):
            return {"cashcat": {"symbol": "cashcat", "asset": "hoodeth:cashcat",
                                "name": "Cash Cat", "contract": CASHCAT, "decimals": 18}}

        response = mock.Mock()
        response.json.return_value = {"version": "59.31.0"}

        def fake_rpc(method, params, **_k):
            if method == "eth_blockNumber":
                return hex(27_000_000)
            if method == "eth_getBlockByNumber":
                return {"timestamp": "0x6553f100"}
            if method == "eth_call":
                data = (params[0].get("data") or "")[:10]
                if data == rh.SEL_GET_ALL_APPROVED:
                    return "0x" + f"{32:064x}" + f"{1:064x}" + "0" * 24 + CASHCAT[2:]
                if data == rh.SEL_SYMBOL:
                    return "0x" + f"{8:064x}" + "CASHCAT".encode().hex().ljust(64, "0")
                if data == rh.SEL_NAME:
                    return "0x" + f"{8:064x}" + "Cash Cat".encode().hex().ljust(64, "0")
                return "0x"
            raise AssertionError(method)

        cfg = rh.Config(state_path=self.state_path, enable_eth=False)
        with mock.patch.object(rh, "log", lambda *a, **k: None), \
                mock.patch.object(rh, "ls_remote", lambda *a, **k: {
                    "refs/heads/master": "a" * 40, "refs/pull/9357/head": "b" * 40}), \
                mock.patch.object(rh, "fetch_bitgo_tokens_at", fetch_tokens), \
                mock.patch.object(rh, "fetch_npm_tokens", lambda *a, **k: {}), \
                mock.patch.object(rh, "http_get", lambda *a, **k: response), \
                mock.patch.object(rh, "rpc", fake_rpc), \
                mock.patch.object(rh, "fetch_logs", lambda *a, **k: []):
            signals, outcome = rh.run(cfg)

        self.assertTrue(outcome["github"].startswith("ok"), outcome["github"])
        self.assertTrue(outcome["chain"].startswith("ok"), outcome["chain"])

        cashcat = [s for s in signals if s["contract"] == CASHCAT]
        self.assertEqual(len(cashcat), 2, "两条腿各出一条（GitHub / 链上），但都是 cashcat")
        for signal in cashcat:
            with self.subTest(type=signal["type"]):
                self.assertEqual(signal["severity"], "critical", "两类都命中必须升 critical")
                cross = signal["extra"]["crossSignal"]
                self.assertNotEqual(cross["family"], "wintermute" if "wintermute" in signal["type"] else "bitgo")
        master = next(s for s in cashcat if s["type"].startswith("bitgo_"))
        self.assertEqual(master["extra"]["crossSignal"]["family"], "wintermute")
        self.assertTrue(master["extra"]["crossSignal"]["source_url"])
        chain_side = next(s for s in cashcat if s["type"].startswith("wintermute_"))
        self.assertEqual(chain_side["extra"]["crossSignal"]["family"], "bitgo")
        self.assertIn("BitGoJS", chain_side["extra"]["crossSignal"]["source_url"])

        # ledger 落盘 → 下一轮重启也知道这个合约两类都命中过
        saved = rh.load_state(self.state_path)
        self.assertIn(CASHCAT, saved["ledger"])
        self.assertEqual(set(saved["ledger"][CASHCAT]), {"bitgo", "wintermute"})


if __name__ == "__main__":
    unittest.main()
