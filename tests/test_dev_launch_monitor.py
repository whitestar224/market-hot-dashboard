"""监控页「Dev 发币」子板块（dev_launch_monitor.py）的纯逻辑回归测试。

真正的扫链要打 Blockscout / BscScan / DexScreener / GeckoTerminal 四个外部服务，
单测里一律不真跑；这里钉死的是**判据**，它们决定这个功能可不可信：

* 级别四档的边界（历史峰值市值 1 亿 / 1000 万 / 100 万）；
* 「一次发币弹两条窗」的去噪闸 —— 发射台一次 create2 出代币 + 包装合约两个地址，
  只有 ``/api/v2/tokens/{addr}`` 返 200 + ERC-20 的那个才算币，另一个是 404；
* 索引器拿不准 / 挂掉时**绝不当成「没发过币」**（Else 会静默漏报，最难排查）；
* 首次回扫只登记、不弹窗（否则加一个老 Dev 进来会被历史那几十个币淹没）；
* 峰值市值只增不减（否则盘中回撤会把级别标签降档）；
* 删地址要连坐清掉发币记录（否则重新加回来永远不会再弹窗）。
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dev_launch_monitor as dlm


DEV = "0x" + "ab" * 20
TOKEN = "0x" + "d8d5c24e6d1493b522a12efd695cb344024bee1b"[0:40]
WRAPPER = "0x" + "fb68e84127caa0f73795e3bc982a5ca19e73c202"[0:40]
FACTORY = "0x" + "e3" * 20
TX = "0x" + "5a" * 32


def make_store(tmp: Path) -> dlm.DevLaunchStore:
    return dlm.DevLaunchStore(tmp / "dev_launch_monitor.sqlite")


class TierTests(unittest.TestCase):
    """四档级别的边界必须是闭区间的下沿（>= 阈值即入档）。"""

    def test_steps_are_expected_four_tiers(self):
        self.assertEqual(
            [tier for tier, _label, _floor in dlm.DEV_TIER_STEPS],
            ["legendary", "premium", "quality", "flow"],
        )
        self.assertEqual(
            [label for _tier, label, _floor in dlm.DEV_TIER_STEPS],
            ["顶级", "精品", "优质", "普通 / 流水"],
        )

    def test_boundaries_are_inclusive_floors(self):
        self.assertEqual(dlm.tier_for_market_cap(100_000_000)["tier"], "legendary")
        self.assertEqual(dlm.tier_for_market_cap(1_000_000_000)["tier"], "legendary")
        self.assertEqual(dlm.tier_for_market_cap(99_999_999)["tier"], "premium")
        self.assertEqual(dlm.tier_for_market_cap(10_000_000)["tier"], "premium")
        self.assertEqual(dlm.tier_for_market_cap(9_999_999)["tier"], "quality")
        self.assertEqual(dlm.tier_for_market_cap(1_000_000)["tier"], "quality")
        self.assertEqual(dlm.tier_for_market_cap(999_999)["tier"], "flow")

    def test_flow_is_the_fallback_for_zero_and_garbage(self):
        # 「没发过任何有市值的币」正是用户说得最多的那一档，不能落到空档。
        for value in (0, None, "", "abc", -5):
            with self.subTest(value=value):
                tier = dlm.tier_for_market_cap(value)
                self.assertEqual(tier["tier"], "flow")
                self.assertEqual(tier["label"], "普通 / 流水")
                self.assertEqual(tier["peakMarketCapText"], "$0")

    def test_tier_carries_threshold_and_text(self):
        tier = dlm.tier_for_market_cap(12_345_678)
        self.assertEqual(tier["minUsd"], 10_000_000.0)
        self.assertEqual(tier["peakMarketCapUsd"], 12_345_678.0)
        self.assertEqual(tier["peakMarketCapText"], "$12.35M")


class MoneyTextTests(unittest.TestCase):
    def test_shorthand_units(self):
        self.assertEqual(dlm.money_text(120), "$120")
        self.assertEqual(dlm.money_text(8_500), "$8.50K")
        self.assertEqual(dlm.money_text(1_200_000), "$1.20M")
        self.assertEqual(dlm.money_text(2_500_000_000), "$2.50B")

    def test_empty_when_unknown(self):
        # 空串 = 前端不显示这一位，而不是显示 $0（会把「没取到」误当成「市值归零」）。
        self.assertEqual(dlm.money_text(0), "")
        self.assertEqual(dlm.money_text(None), "")
        self.assertEqual(dlm.money_text(""), "")


class ChainHelperTests(unittest.TestCase):
    def test_aliases_normalise(self):
        self.assertEqual(dlm.normalize_chain("BSC"), "bsc")
        self.assertEqual(dlm.normalize_chain("eth"), "ethereum")
        self.assertEqual(dlm.normalize_chain("Ethereum"), "ethereum")
        self.assertEqual(dlm.normalize_chain("8453"), "base")
        self.assertEqual(dlm.normalize_chain("4663"), "robinhood")
        self.assertEqual(dlm.normalize_chain("sol"), "")

    def test_labels(self):
        self.assertEqual(dlm.chain_label("robinhood"), "Robinhood")
        self.assertEqual(dlm.chain_label("eth"), "Ethereum")
        self.assertEqual(dlm.chain_label("bsc"), "BSC")

    def test_bsc_is_unavailable_without_key(self):
        with patch.dict(os.environ, {"BSCSCAN_API_KEY": ""}):
            available, note = dlm.chain_availability("bsc")
        self.assertFalse(available)
        self.assertIn("BSCSCAN_API_KEY", note)

    def test_bsc_availability_defers_to_the_live_probe(self):
        # 「有 key」与「能查」是两件事：配了 key 也照样可能被免费档挡在门外，
        # 所以这里必须把结论交给真探测（_bsc_capability），不能凭 key 是否存在下判断。
        with patch.dict(os.environ, {"BSCSCAN_API_KEY": "k"}), \
             patch.object(dlm, "_bsc_capability", return_value=(True, "")):
            self.assertEqual(dlm.chain_availability("bsc"), (True, ""))
        blocked = (False, "BscScan 免费档不支持 BSC（Etherscan V2 把 chainid=56 划为付费链）")
        with patch.dict(os.environ, {"BSCSCAN_API_KEY": "k"}), \
             patch.object(dlm, "_bsc_capability", return_value=blocked):
            self.assertEqual(dlm.chain_availability("bsc"), blocked)

    def test_blockscout_chains_need_no_key(self):
        for chain in ("ethereum", "base", "robinhood"):
            with self.subTest(chain=chain):
                self.assertEqual(dlm.chain_availability(chain), (True, ""))

    def test_unknown_chain_is_unavailable(self):
        available, note = dlm.chain_availability("solana")
        self.assertFalse(available)
        self.assertTrue(note)

    def test_chain_options_cover_all_four(self):
        options = dlm.chain_options()
        self.assertEqual([row["id"] for row in options], list(dlm.EVM_CHAINS))
        for row in options:
            self.assertIn("available", row)
            self.assertTrue(row["label"])

    def test_address_shape(self):
        self.assertTrue(dlm.is_evm_address(DEV))
        # 前缀大小写都收：粘贴进来的地址可能是 0X 开头，不该因此被判无效。
        self.assertTrue(dlm.is_evm_address(DEV.upper()))
        self.assertFalse(dlm.is_evm_address("0x123"))
        self.assertFalse(dlm.is_evm_address("not-an-address"))


class IndexerParsingTests(unittest.TestCase):
    def test_address_hash_accepts_string_and_object(self):
        self.assertEqual(dlm._address_hash(DEV.upper()), DEV.lower())
        self.assertEqual(dlm._address_hash({"hash": DEV.upper()}), DEV.lower())
        self.assertEqual(dlm._address_hash(None), "")
        self.assertEqual(dlm._address_hash("0x123"), "")

    def test_iso_timestamp_to_ms_is_utc(self):
        # 2026-10-15T16:22:13Z == 1792081333000（用 calendar.timegm 而不是 mktime，
        # 否则会带上本机时区偏移，弹窗时间就错了）。
        self.assertEqual(dlm._iso_to_ms("2026-10-15T16:22:13.000000Z"), 1792081333000)
        self.assertEqual(dlm._iso_to_ms("2026-10-15 16:22:13"), 1792081333000)
        self.assertEqual(dlm._iso_to_ms(""), 0)
        self.assertEqual(dlm._iso_to_ms("nonsense"), 0)


class BlockscoutIndexerTests(unittest.TestCase):
    """``filter=from`` 拿到的形状必须能直接喂给扫描器。"""

    def test_outgoing_shape(self):
        payload = {
            "items": [
                {
                    "hash": TX,
                    "to": {"hash": FACTORY, "is_contract": True},
                    "timestamp": "2026-10-15T16:22:13.000000Z",
                    "block_number": "89363679",
                    "created_contract": None,
                    "method": "launchAndBuy",
                },
                {
                    "hash": "0x" + "77" * 32,
                    "to": None,
                    "timestamp": "2026-10-15T16:20:00.000000Z",
                    "block_number": "89363600",
                    "created_contract": {"hash": TOKEN},
                    "method": "createToken",
                },
            ],
        }
        with patch.object(dlm, "_blockscout_get", return_value=payload) as getter:
            rows = dlm.outgoing_transactions("robinhood", DEV, pages=1)
        self.assertEqual(getter.call_count, 1)
        self.assertEqual(getter.call_args.args[1], f"/api/v2/addresses/{DEV}/transactions")
        self.assertEqual(getter.call_args.args[2], {"filter": "from"})

        self.assertEqual(rows[0]["hash"], TX)
        self.assertEqual(rows[0]["to"], FACTORY)
        self.assertTrue(rows[0]["toIsContract"])
        self.assertEqual(rows[0]["createdContract"], "")
        self.assertEqual(rows[0]["createdAt"], 1792081333000)
        self.assertEqual(rows[0]["blockNumber"], 89363679)
        # 第二笔自己就在部署合约：createdContract 直接给出新地址。
        self.assertEqual(rows[1]["createdContract"], TOKEN)
        self.assertFalse(rows[1]["toIsContract"])

    def test_pagination_follows_next_page_params(self):
        pages = [
            {"items": [{"hash": TX, "to": None, "timestamp": "", "block_number": 1}],
             "next_page_params": {"block_number": 1, "index": 5}},
            {"items": [{"hash": "0x" + "88" * 32, "to": None, "timestamp": "", "block_number": 2}],
             "next_page_params": None},
        ]
        with patch.object(dlm, "_blockscout_get", side_effect=pages) as getter:
            rows = dlm.outgoing_transactions("robinhood", DEV, pages=6)
        self.assertEqual(getter.call_count, 2)
        # 第二页必须原样带上第一页给的游标（Blockscout 的翻页就是靠它）。
        self.assertEqual(
            getter.call_args.args[2],
            {"filter": "from", "block_number": 1, "index": 5},
        )
        self.assertEqual(len(rows), 2)

    def test_bad_address_short_circuits(self):
        with patch.object(dlm, "_blockscout_get") as getter:
            self.assertEqual(dlm.outgoing_transactions("robinhood", "0x123"), [])
            self.assertEqual(dlm.outgoing_transactions("solana", DEV), [])
        getter.assert_not_called()

    def test_factory_creations_keep_only_create_types(self):
        payload = {
            "items": [
                {"type": "call", "created_contract": None},
                {"type": "create2", "created_contract": {"hash": TOKEN}},
                {"type": "create2", "created_contract": {"hash": WRAPPER}},
                # 同一个地址重复出现只留一份，避免同一次发币登记两遍。
                {"type": "create", "created_contract": TOKEN},
                {"type": "delegatecall", "created_contract": {"hash": FACTORY}},
            ],
        }
        with patch.object(dlm, "_blockscout_get", return_value=payload):
            created = dlm.transaction_creations("robinhood", TX)
        self.assertEqual(created, [TOKEN, WRAPPER])

    def test_creation_lookup_needs_a_real_tx_hash(self):
        with patch.object(dlm, "_blockscout_get") as getter:
            self.assertEqual(dlm.transaction_creations("robinhood", "0xabc"), [])
        getter.assert_not_called()


class TokenMetadataFilterTests(unittest.TestCase):
    """去噪闸：一次 launchAndBuy 会 create2 出代币 + 包装合约，只有一个是币。"""

    def test_erc20_response_is_a_token(self):
        payload = {
            "type": "ERC-20", "symbol": "QUIV", "name": "QUIVER",
            "holders_count": "15", "exchange_rate": "0.001", "circulating_market_cap": "120000",
        }
        with patch.object(dlm, "_blockscout_get", return_value=payload):
            meta = dlm.token_metadata("robinhood", TOKEN)
        self.assertTrue(meta["isToken"])
        self.assertEqual(meta["symbol"], "QUIV")
        self.assertEqual(meta["marketCapUsd"], 120000.0)
        self.assertEqual(meta["source"], "blockscout")

    def test_404_means_not_a_token(self):
        # 404 是「这个地址不是代币」的确定回答，必须和「请求失败」分开。
        error = dlm.IndexerError("robinhood Blockscout 请求失败：404 Client Error: Not Found")
        with patch.object(dlm, "_blockscout_get", side_effect=error):
            meta = dlm.token_metadata("robinhood", WRAPPER)
        self.assertIs(meta["isToken"], False)

    def test_other_failure_falls_through_to_other_sources(self):
        # 网络抖动（非 404）不能判成「非代币」——那会每次抖动都放一个包装合约进库。
        error = dlm.IndexerError("robinhood Blockscout 请求失败：Read timed out")
        with patch.object(dlm, "_blockscout_get", side_effect=error), \
             patch.object(dlm, "_dexscreener_pairs", return_value=[]), \
             patch.object(dlm, "_rpc_symbol", return_value=True):
            meta = dlm.token_metadata("robinhood", TOKEN)
        self.assertIs(meta["isToken"], True)
        self.assertEqual(meta["source"], "rpc")

    def test_unknown_verdict_is_treated_as_token(self):
        # None = 拿不准。漏报一个真发币比多显示一张卡片贵得多，所以按「是」处理。
        error = dlm.IndexerError("boom")
        with patch.object(dlm, "_blockscout_get", side_effect=error), \
             patch.object(dlm, "_dexscreener_pairs", return_value=[]), \
             patch.object(dlm, "_rpc_symbol", return_value=None):
            meta = dlm.token_metadata("robinhood", TOKEN)
        self.assertIsNone(meta["isToken"])
        self.assertNotIn(meta["isToken"], (False,))

    def test_bad_input_is_not_a_token(self):
        self.assertIs(dlm.token_metadata("robinhood", "nope")["isToken"], False)


class BscScanTests(unittest.TestCase):
    def test_missing_key_raises_indexer_error(self):
        with patch.dict(os.environ, {"BSCSCAN_API_KEY": ""}):
            with self.assertRaises(dlm.IndexerError):
                dlm._bscscan_get({"module": "account", "action": "txlist"})

    def test_empty_result_is_not_an_error(self):
        # Etherscan 系把「没有记录」也标成 status=0，这里必须当成正常空集。
        with patch.dict(os.environ, {"BSCSCAN_API_KEY": "k"}), \
             patch.object(dlm, "_session", return_value=_FakeSession({
                 "status": "0", "message": "No transactions found", "result": "No transactions found",
             })):
            self.assertEqual(dlm._bscscan_get({"module": "account", "action": "txlist"}), [])

    def test_real_error_is_raised(self):
        # 免费档查不了 BSC 时报的就是这一条，必须如实抛出去（前端要能展示原因）。
        with patch.dict(os.environ, {"BSCSCAN_API_KEY": "k"}), \
             patch.object(dlm, "_session", return_value=_FakeSession({
                 "status": "0", "message": "NOTOK",
                 "result": "Free API access is not supported for this chain.",
             })):
            with self.assertRaises(dlm.IndexerError) as ctx:
                dlm._bscscan_get({"module": "account", "action": "txlist"})
        self.assertIn("Free API access", str(ctx.exception))

    def test_the_query_targets_bsc_via_chainid(self):
        # BscScan 自家 V1 接口已下线，官方接任者是 Etherscan V2 —— 那个接口靠
        # chainid 认链，漏了这个参数就会去查以太坊主网（结果全是错的、还不报错）。
        session = _FakeSession({"status": "1", "message": "OK", "result": []})
        with patch.dict(os.environ, {"BSCSCAN_API_KEY": "k"}), \
             patch.object(dlm, "_session", return_value=session):
            dlm._bscscan_get({"module": "account", "action": "txlist"})
        call = session.calls[0]
        self.assertIn("etherscan.io/v2/api", call["url"])
        self.assertEqual(call["params"]["chainid"], 56)
        self.assertEqual(call["params"]["apikey"], "k")

    def test_a_retired_endpoint_returning_html_is_explained(self):
        session = _HtmlSession()
        with patch.dict(os.environ, {"BSCSCAN_API_KEY": "k"}), \
             patch.object(dlm, "_session", return_value=session):
            with self.assertRaises(dlm.IndexerError) as ctx:
                dlm._bscscan_get({"module": "account", "action": "txlist"})
        # 不能让用户看到「Expecting value: line 1 column 1」这种没法排查的解析错误。
        self.assertIn("非 JSON", str(ctx.exception))

    def test_txlist_shape(self):
        rows = [{
            "hash": TX, "to": FACTORY, "timeStamp": "1792081333",
            "blockNumber": "89363679", "input": "0xdeadbeef",
            "contractAddress": "",
        }]
        with patch.dict(os.environ, {"BSCSCAN_API_KEY": "k"}), \
             patch.object(dlm, "_bscscan_get", return_value=rows):
            out = dlm.outgoing_transactions("bsc", DEV, pages=1)
        self.assertEqual(out[0]["createdAt"], 1792081333000)
        self.assertTrue(out[0]["toIsContract"])


class BscCapabilityProbeTests(unittest.TestCase):
    """「配了 key」不等于「能查」—— 免费档把 BSC 划成付费链，必须真探一次。

    不探测的后果是静默失败：用户加完 BSC 地址，以为在监控，其实一轮都扫不出来。
    """

    def setUp(self):
        self._saved = dlm._BSC_PROBE
        dlm._BSC_PROBE = None
        self.addCleanup(lambda: setattr(dlm, "_BSC_PROBE", self._saved))

    def test_a_working_key_reports_available(self):
        with patch.dict(os.environ, {"BSCSCAN_API_KEY": "k"}), \
             patch.object(dlm, "_bscscan_get", return_value=[]):
            self.assertEqual(dlm._bsc_capability(), (True, ""))

    def test_the_paid_chain_answer_is_a_definitive_verdict(self):
        error = dlm.IndexerError(
            "BscScan：NOTOK: Free API access is not supported for this chain."
        )
        with patch.dict(os.environ, {"BSCSCAN_API_KEY": "k"}), \
             patch.object(dlm, "_bscscan_get", side_effect=error) as getter:
            available, note = dlm._bsc_capability()
            self.assertFalse(available)
            self.assertIn("免费档", note)
            # 定论必须缓存：每建一次快照就问一次是白烧配额。
            self.assertEqual(dlm._bsc_capability(), (available, note))
        self.assertEqual(getter.call_count, 1)

    def test_transient_failures_are_not_cached_as_a_verdict(self):
        # 网络抖动不等于「这条链查不了」：绝不能把一次超时写成永久结论。
        with patch.dict(os.environ, {"BSCSCAN_API_KEY": "k"}), \
             patch.object(dlm, "_bscscan_get", side_effect=dlm.IndexerError("read timed out")) as getter:
            available, note = dlm._bsc_capability()
            self.assertFalse(available)
            self.assertIn("timed out", note)
            self.assertIsNone(dlm._BSC_PROBE, "非定论结论不该进缓存")
            dlm._bsc_capability()
        self.assertEqual(getter.call_count, 2)

    def test_no_key_short_circuits_before_any_request(self):
        with patch.dict(os.environ, {"BSCSCAN_API_KEY": ""}), \
             patch.object(dlm, "_bscscan_get") as getter:
            available, note = dlm.chain_availability("bsc")
        self.assertFalse(available)
        self.assertIn("BSCSCAN_API_KEY", note)
        getter.assert_not_called()


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _HtmlResponse:
    """已下线的旧接口会把请求落回官网 HTML —— 此时 ``json()`` 抛 ValueError。"""

    status_code = 404

    def raise_for_status(self):
        return None

    def json(self):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get(self, url, params=None, **kwargs):
        self.calls.append({"url": url, "params": dict(params or {})})
        return _FakeResponse(self._payload)


class _HtmlSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, **kwargs):
        self.calls.append({"url": url, "params": dict(params or {})})
        return _HtmlResponse()


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = make_store(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_wallet_validates_input(self):
        with self.assertRaises(ValueError):
            self.store.save_wallet("solana", DEV)
        with self.assertRaises(ValueError):
            self.store.save_wallet("bsc", "0x123")

    def test_save_wallet_is_upsert_and_keeps_untouched_fields(self):
        self.store.save_wallet("bsc", DEV.upper(), "老王", True)
        row = self.store.wallet("bsc", DEV)
        self.assertEqual(row["label"], "老王")
        self.assertTrue(row["enabled"])
        # 只改 enabled，备注不动。
        self.store.save_wallet("bsc", DEV, None, False)
        row = self.store.wallet("bsc", DEV)
        self.assertEqual(row["label"], "老王")
        self.assertFalse(row["enabled"])
        # 传空串 = 用户就是要清空备注，不是「不动」。
        self.store.save_wallet("bsc", DEV, "", None)
        self.assertEqual(self.store.wallet("bsc", DEV)["label"], "")

    def test_remove_wallet_cascades(self):
        self.store.save_wallet("robinhood", DEV)
        self.store.upsert_token({
            "chain": "robinhood", "token": TOKEN, "dev_address": DEV,
            "symbol": "QUIV", "launch_tx": TX,
        })
        self.store.mark_tx_scanned("robinhood", TX, DEV)
        self.assertTrue(self.store.known_token("robinhood", TOKEN))
        self.assertTrue(self.store.tx_scanned("robinhood", TX))

        self.assertTrue(self.store.remove_wallet("robinhood", DEV))
        # 连坐清干净：否则重新加回同一个 Dev，历史那批币会被当成「早就见过」，
        # 结果一条新币提醒都不弹，用户会以为功能坏了。
        self.assertIsNone(self.store.wallet("robinhood", DEV))
        self.assertFalse(self.store.known_token("robinhood", TOKEN))
        self.assertFalse(self.store.tx_scanned("robinhood", TX))

    def test_upsert_token_reports_first_sight_only_once(self):
        row = {"chain": "base", "token": TOKEN, "dev_address": DEV, "symbol": "QUIV"}
        self.assertTrue(self.store.upsert_token(row))
        self.assertFalse(self.store.upsert_token(row))
        # 已知行只补名字，不会把 first_seen_at 往后推。
        self.store.upsert_token({**row, "name": "QUIVER"})
        results = self.store.results()
        self.assertEqual(results[0]["name"], "QUIVER")

    def test_peak_market_cap_only_moves_up(self):
        self.store.save_wallet("base", DEV)
        self.store.upsert_token({"chain": "base", "token": TOKEN, "dev_address": DEV})
        self.store.update_token_market("base", TOKEN, DEV, {
            "priceUsd": 1.0, "marketCapUsd": 5_000_000,
            "peakUsd": 5_000_000, "peakSource": "current", "source": "dexscreener",
        })
        self.assertEqual(self.store.wallet_tier("base", DEV)["tier"], "quality")
        # 回撤：当前市值掉到 100 万，峰值与级别都必须保持不变。
        self.store.update_token_market("base", TOKEN, DEV, {
            "priceUsd": 0.2, "marketCapUsd": 1_000_000,
            "peakUsd": 1_000_000, "peakSource": "geckoterminal", "source": "dexscreener",
        })
        row = self.store.results()[0]
        self.assertEqual(row["peak_market_cap_usd"], 5_000_000)
        self.assertEqual(self.store.wallet_tier("base", DEV)["tier"], "quality")

    def test_wallet_tier_uses_the_best_token(self):
        self.store.save_wallet("base", DEV)
        for token, peak in ((TOKEN, 2_000_000), ("0x" + "11" * 20, 150_000_000)):
            self.store.upsert_token({"chain": "base", "token": token, "dev_address": DEV})
            self.store.update_token_market("base", token, DEV, {"peakUsd": peak})
        tier = self.store.wallet_tier("base", DEV)
        self.assertEqual(tier["tier"], "legendary")
        self.assertEqual(tier["tokenCount"], 2)

    def test_alerts_toggle_defaults_on(self):
        self.assertTrue(self.store.alerts_enabled())
        self.assertFalse(self.store.set_alerts_enabled(False))
        self.assertFalse(self.store.alerts_enabled())
        self.assertTrue(self.store.set_alerts_enabled("1"))

    def test_results_carry_view_links(self):
        self.store.save_wallet("robinhood", DEV, "NΞO")
        self.store.upsert_token({
            "chain": "robinhood", "token": TOKEN, "dev_address": DEV,
            "symbol": "QUIV", "launch_tx": TX, "via": "factory",
        })
        row = self.store.results()[0]
        self.assertEqual(row["chainLabel"], "Robinhood")
        self.assertEqual(row["devLabel"], "NΞO")
        self.assertEqual(row["url"], f"https://gmgn.ai/robinhood/token/{TOKEN}")
        self.assertEqual(row["explorerUrl"], f"https://robinhoodchain.blockscout.com/token/{TOKEN}")
        self.assertTrue(row["launchTxUrl"].endswith(TX))
        self.assertTrue(row["fresh"])

    def test_counts_grounded_peaks(self):
        self.store.save_wallet("base", DEV)
        self.store.upsert_token({"chain": "base", "token": TOKEN, "dev_address": DEV})
        self.store.update_token_market("base", TOKEN, DEV, {"peakUsd": 9, "peakSource": "geckoterminal"})
        counts = self.store.counts()
        self.assertEqual(counts["wallets"], 1)
        self.assertEqual(counts["tokens"], 1)
        self.assertEqual(counts["peaksGrounded"], 1)

    def test_mixed_case_addresses_do_not_split_a_dev_in_two(self):
        """checksum 大小写必须被归一 —— 这是「级别莫名全变流水」的真凶。

        区块浏览器里复制的地址带大小写，而 ``dev_wallets.address`` 恒为小写。如果
        ``dev_tokens.dev_address`` 原样存混合大小写，``wallet_tier`` 用小写去查就会
        查不到 → 该 Dev 的级别、历史峰值、发币数全变 0，界面上看起来「分级坏了」。
        （2026-10-16 无头渲染验收时抓到：NΞO 明明发了 1.5 亿峰值 QUIV，卡片却写 $0。）
        """
        mixed = "0xC0F59aD036F0F9D6a0e0C49a080F826C925146BB"
        self.store.save_wallet("robinhood", mixed, "NΞO")
        self.assertTrue(self.store.upsert_token({
            "chain": "robinhood", "token": TOKEN.upper().replace("0X", "0x"),
            "dev_address": mixed, "symbol": "QUIV",
        }))
        self.store.update_token_market("robinhood", TOKEN, mixed, {
            "peakUsd": 150_000_000, "peakSource": "geckoterminal",
        })

        # 库里的键必须是小写。
        stored = self.store.results()[0]
        self.assertEqual(stored["dev_address"], mixed.lower())
        self.assertEqual(stored["token"], TOKEN.lower())
        # Dev 地址：任一大小写写法都要能查到同一个级别。
        for probe in (mixed, mixed.lower(), mixed.upper(), "0X" + mixed[2:].upper()):
            with self.subTest(dev=probe[:8]):
                self.assertEqual(self.store.wallet_tier("robinhood", probe)["tier"], "legendary")
        # 代币地址同理（否则 known_token 会漏判成新币、对同一个币重复弹窗）。
        for probe in (TOKEN, TOKEN.upper().replace("0X", "0x"), TOKEN.upper()):
            with self.subTest(token=probe[:8]):
                self.assertTrue(self.store.known_token("robinhood", probe))
        # 同一个币 + 同一个 Dev，只换大小写，不该算新币。
        self.assertFalse(self.store.upsert_token({
            "chain": "robinhood", "token": TOKEN.upper().replace("0X", "0x"),
            "dev_address": mixed.upper(), "symbol": "DUP",
        }))
        self.assertEqual(len(self.store.results()), 1)

        # 钱包视图（save_wallet 存的小写）必须能匹配上发币记录，卡片的级别与发币数才对。
        wallet = self.store.wallet("robinhood", mixed)
        self.assertEqual(wallet["tier"]["tier"], "legendary")
        self.assertEqual(wallet["tier"]["tokenCount"], 1)
        # results() 的 devLabel 也靠 (chain, dev_address) 匹配，同样受益。
        self.assertEqual(stored["devLabel"], "NΞO")


class ScanTests(unittest.TestCase):
    """首次回扫 vs 巡航的差别：只登记、不弹窗。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = make_store(self.tmp)
        self.store.save_wallet("robinhood", DEV, "NΞO")
        self.alerts = []
        self.monitor = dlm.DevLaunchMonitor(
            self.store, interval=999, internal_lookups=5,
            alert_callback=self.alerts.append,
        )
        self.txs = [{
            "hash": TX, "to": FACTORY, "createdAt": 1792081333000,
            "blockNumber": 89363679, "toIsContract": True,
            "createdContract": "", "method": "launchAndBuy",
        }]
        self.creations = [TOKEN, WRAPPER]

    def tearDown(self):
        self._tmp.cleanup()

    def _meta(self, chain, address):
        return {"isToken": address == TOKEN, "symbol": "QUIV" if address == TOKEN else ""}

    def test_first_scan_registers_without_announcing(self):
        with patch.object(dlm, "outgoing_transactions", return_value=self.txs), \
             patch.object(dlm, "transaction_creations", return_value=self.creations), \
             patch.object(dlm, "token_metadata", side_effect=self._meta):
            wallet = self.store.wallet("robinhood", DEV)
            discovered = self.monitor.scan_wallet(wallet)
        # 只登记真代币；包装合约被 404 判据挡掉，不会一次发币弹两条窗。
        self.assertEqual(discovered, 1)
        self.assertEqual(self.alerts, [], "首次回扫不能弹窗，否则历史几十个币会淹没用户")
        self.assertTrue(self.store.known_token("robinhood", TOKEN))
        self.assertFalse(self.store.known_token("robinhood", WRAPPER))
        self.assertEqual(len(self.store.results()), 1)
        # 回扫完成会推进 scanned_at，下一次就走巡航的小配额。
        self.assertTrue(self.store.wallet("robinhood", DEV)["scanned_at"])

    def test_second_scan_announces_new_token(self):
        fresh_tx = {**self.txs[0], "hash": "0x" + "99" * 32}
        with patch.object(dlm, "outgoing_transactions", return_value=self.txs), \
             patch.object(dlm, "transaction_creations", return_value=self.creations), \
             patch.object(dlm, "token_metadata", side_effect=self._meta):
            self.monitor.scan_wallet(self.store.wallet("robinhood", DEV))
        # 第二轮：钱包已经扫过了，新币要弹窗。
        second = [TOKEN, "0x" + "22" * 20]
        with patch.object(dlm, "outgoing_transactions", return_value=[fresh_tx]), \
             patch.object(dlm, "transaction_creations", return_value=second), \
             patch.object(dlm, "token_metadata", side_effect=lambda c, a: {"isToken": True}):
            discovered = self.monitor.scan_wallet(self.store.wallet("robinhood", DEV))
        self.assertEqual(discovered, 1)  # TOKEN 已登记，只有新地址算新
        self.assertEqual(len(self.alerts), 1)
        self.assertEqual(self.alerts[0]["token"], "0x" + "22" * 20)
        self.assertEqual(self.alerts[0]["via"], "factory")
        self.assertEqual(self.alerts[0]["label"], "NΞO")

    def test_already_known_token_is_not_announced_again(self):
        known = [TOKEN]
        with patch.object(dlm, "outgoing_transactions", return_value=self.txs), \
             patch.object(dlm, "transaction_creations", return_value=known), \
             patch.object(dlm, "token_metadata", side_effect=lambda c, a: {"isToken": True}):
            self.monitor.scan_wallet(self.store.wallet("robinhood", DEV))
            self.assertEqual(self.alerts, [])
            # 再扫一轮，同一个币仍在库里 → 依旧不弹，也不会重复登记。
            self.monitor.scan_wallet(self.store.wallet("robinhood", DEV))
        self.assertEqual(self.alerts, [])
        self.assertEqual(len(self.store.results()), 1)

    def test_direct_deploy_path(self):
        tx = [{
            "hash": TX, "to": "", "createdAt": 1, "blockNumber": 1,
            "toIsContract": False, "createdContract": TOKEN, "method": "createToken",
        }]
        with patch.object(dlm, "outgoing_transactions", return_value=tx), \
             patch.object(dlm, "transaction_creations") as creations, \
             patch.object(dlm, "token_metadata", return_value={"isToken": True, "symbol": "X"}):
            discovered = self.monitor.scan_wallet(self.store.wallet("robinhood", DEV))
        self.assertEqual(discovered, 1)
        # 自己部署的这笔不必再去查内部交易（那是贵的那一路）。
        creations.assert_not_called()
        self.assertEqual(self.store.results()[0]["via"], "deploy")

    def test_indexer_error_leaves_wallet_unscanned(self):
        # 「查不到」绝不能记成「这个 Dev 没发过币」：scanned_at 不推进，下轮重扫。
        with patch.object(dlm, "outgoing_transactions", side_effect=dlm.IndexerError("down")), \
             patch.object(dlm, "transaction_creations"), \
             patch.object(dlm, "token_metadata"):
            with self.assertRaises(dlm.IndexerError):
                self.monitor.scan_wallet(self.store.wallet("robinhood", DEV))
        self.assertFalse(self.store.wallet("robinhood", DEV)["scanned_at"])

    def test_run_cycle_reports_busy_instead_of_queueing(self):
        self.monitor._cycle_lock.acquire()
        try:
            self.assertEqual(self.monitor.run_cycle(), {"discovered": 0, "enriched": 0, "busy": 1})
        finally:
            self.monitor._cycle_lock.release()

    def test_scan_wallet_now_refuses_unavailable_chain(self):
        self.store.save_wallet("bsc", DEV)
        with patch.dict(os.environ, {"BSCSCAN_API_KEY": ""}):
            result = self.monitor.scan_wallet_now("bsc", DEV)
        self.assertFalse(result["ok"])
        self.assertIn("BSCSCAN_API_KEY", result["error"])

    def test_scan_wallet_now_unknown_address(self):
        self.assertFalse(self.monitor.scan_wallet_now("base", DEV)["ok"])


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = make_store(self.tmp)
        self.monitor = dlm.DevLaunchMonitor(self.store, interval=120)

    def tearDown(self):
        self._tmp.cleanup()

    def test_payload_shape(self):
        self.store.save_wallet("robinhood", DEV, "NΞO")
        self.store.upsert_token({
            "chain": "robinhood", "token": TOKEN, "dev_address": DEV, "symbol": "QUIV",
        })
        payload = self.monitor.payload()
        for key in ("ok", "now", "running", "chains", "tiers", "wallets", "results", "summary"):
            self.assertIn(key, payload)
        self.assertEqual(len(payload["chains"]), 4)
        self.assertEqual(len(payload["tiers"]), 4)
        summary = payload["summary"]
        self.assertEqual(summary["wallets"], 1)
        self.assertEqual(summary["enabledWallets"], 1)
        self.assertEqual(summary["tokens"], 1)
        self.assertTrue(summary["alertsEnabled"])
        self.assertIn("flow", summary["tiers"])
        self.assertFalse(payload["running"])

    def test_status_shape(self):
        status = self.monitor.status()
        for key in ("running", "intervalSeconds", "lastCycleAt", "lastError", "counts", "bscIndexerReady"):
            self.assertIn(key, status)


if __name__ == "__main__":
    unittest.main()
