"""Arkham 网页端签名通路的口径。

这个客户端最容易错的三件事，每条都有对应用例：

1. **签名对象只有 path**，不含 query —— 实测同一 timestamp 下
   ``?groupByEntity=true`` 与 ``=false`` 的 ``X-Payload`` 完全相同。如果把它算成
   含 query 的串，带参数的请求会全部 401。
2. **401/403 必须抛 ArkhamAuthError**，不能退化成「接口不可用但返回空」——
   「抓取失败伪装成没有数据」是这个项目反复踩的坑。
3. **占比单位**：Arkham 的 ``pctOfCap`` 是 0~1 的小数，客户端统一换成百分数，
   免得下游四处乘法、四处漏乘。
"""

import unittest

import arkham_client as ac

#: 2026-10-21 从 arkm.com 真机抓包拿到的 (path, ts, X-Payload) 样本。
CAPTURED = [
    ("/user", "1792562769", "ad84f8448abe6b22fdd813a387ff91238648c8e9741391bd8c9dcfff24feea80"),
    ("/entitlements", "1792562769", "9622e1286c34458ff219fa388911f6755b507af7c68cdbd253ff66934179bc33"),
    ("/token/market/janction", "1792562769", "ffaa58f22f42cb7d5574f4e735f164649b8afc47c6ad727010d31d3ceba809ad"),
    ("/intelligence/token/janction", "1792562769", "3175505a28b4ca010aef5ef2a2703cf588a4d522b183950a80e50342c27bd039"),
    ("/token/holders/janction", "1792562772", "70f6d0b6b24b99f1ed8fe4da77394c0c9a81d20d8280c2a07f5b04af12290ab9"),
    ("/token/price/history/janction", "1792562772", "8cfe149cf2f0bae8b3e577a21fb93e96c9db3ef5112338a5cd26078a08a42cdf"),
    ("/token/addresses/janction", "1792562769", "6bbee628561ce959e00737e0cef6ed185596d426dc9770319d611e0c2ad41846"),
]


class SignatureTest(unittest.TestCase):
    """签名必须与真实抓包逐位吻合 —— 这是整条通路的地基。"""

    def test_matches_captured_samples(self):
        for path, ts, expected in CAPTURED:
            with self.subTest(path=path, ts=ts):
                self.assertEqual(
                    ac.sign_payload(path, ts, ac.DEFAULT_CLIENT_KEY), expected
                )

    def test_headers_carry_timestamp_and_payload_but_no_api_key(self):
        headers = ac.arkham_headers("/token/holders/bsc/0xabc", timestamp="1792562769")
        self.assertEqual(headers["X-Timestamp"], "1792562769")
        self.assertEqual(
            headers["X-Payload"],
            ac.sign_payload("/token/holders/bsc/0xabc", "1792562769", ac.DEFAULT_CLIENT_KEY),
        )
        # 网页端根本不用 API key —— 带上反而会被当成另一条鉴权路径。
        self.assertNotIn("API-Key", headers)
        self.assertNotIn("Authorization", headers)

    def test_query_string_is_not_signed(self):
        """同一 path 换 query，签名必须一样（见模块注释的实测证据）。"""
        a = ac.arkham_headers("/token/holders/janction?groupByEntity=true", timestamp="1792562772")
        b = ac.arkham_headers("/token/holders/janction?groupByEntity=false", timestamp="1792562772")
        self.assertEqual(a["X-Payload"], b["X-Payload"])


class ChainSlugTest(unittest.TestCase):
    def test_maps_internal_codes_and_labels(self):
        cases = {
            "bsc": "bsc", "bnb": "bsc", "币安智能链": "bsc", "56": "bsc",
            "ethereum": "ethereum", "eth": "ethereum", "以太坊": "ethereum",
            "sol": "solana", "solana": "solana", "索拉纳": "solana",
            # 币安钱包的链号：CT_501 是 Solana，4663 是 Robinhood。
            "CT_501": "solana", "501": "solana",
            "base": "base", "8453": "base", "robinhood": "robinhood", "4663": "robinhood",
            # Arkham 的写法是 arbitrum_one，不是 arbitrum。
            "arbitrum": "arbitrum_one", "arbitrum_one": "arbitrum_one", "42161": "arbitrum_one",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(ac.arkham_chain_slug(raw), expected)

    def test_rejects_unknown_and_bare_chain_ids(self):
        for raw in ("", None, "5042", "CT_999", "某条链"):
            with self.subTest(raw=raw):
                self.assertEqual(ac.arkham_chain_slug(raw), "")


class ChainLabelTest(unittest.TestCase):
    """展示名认不出必须留空：回吐裸链号 ``CT_501`` 会印到卡片上（踩过）。"""

    def test_labels_every_known_writing(self):
        cases = {
            "solana": "Solana", "sol": "Solana",
            # 币安钱包链号是最容易漏的那个：通用展示表不认它。
            "CT_501": "Solana", "501": "Solana",
            "56": "BSC", "bsc": "BSC", "bnb": "BSC",
            "4663": "Robinhood", "robinhood": "Robinhood",
            "eth": "Ethereum", "1": "Ethereum",
            "8453": "Base", "base": "Base",
            "42161": "Arbitrum", "arbitrum_one": "Arbitrum",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(ac.chain_label(raw), expected)

    def test_unknown_chain_yields_empty_not_raw(self):
        for raw in ("", None, "5042", "CT_999", "EVM", "某条链"):
            with self.subTest(raw=raw):
                self.assertEqual(ac.chain_label(raw), "")


class TokenUrlTest(unittest.TestCase):
    def test_builds_explorer_url(self):
        self.assertEqual(ac.arkham_token_url("janction"), "https://arkm.com/explorer/token/janction")

    def test_falls_back_to_explorer_home_without_id(self):
        # 猜路径只会得到空白页（Explorer 是 SPA，任何路径都回 200 空壳）。
        self.assertEqual(ac.arkham_token_url(""), "https://arkm.com/explorer")
        self.assertEqual(ac.arkham_token_url(None), "https://arkm.com/explorer")
        self.assertEqual(ac.arkham_token_url("a/b"), "https://arkm.com/explorer")


def _holder(address, pct, *, chain="bsc", entity=None, label=None, usd=1.0):
    return {
        "address": {
            "address": address,
            "chain": chain,
            "arkhamEntity": entity,
            "arkhamLabel": {"name": label} if label else None,
            "contract": False,
        },
        "balance": 10.0,
        "usd": usd,
        "pctOfCap": pct,
    }


class NormalizeHoldersTest(unittest.TestCase):
    def test_pct_is_converted_to_percent(self):
        payload = {"addressTopHolders": {"ethereum": [_holder("0xA", 0.2629, chain="ethereum")]}}
        rows = ac.normalize_holders(payload)
        self.assertAlmostEqual(rows[0]["pct"], 26.29, places=6)

    def test_extracts_entity_and_label(self):
        payload = {
            "addressTopHolders": {
                "bsc": [
                    _holder("0xB", 0.0389, entity={"id": "bitget", "name": "Bitget", "type": "cex"}),
                    _holder("0xC", 0.0071, label="Gnosis Safe Proxy"),
                ]
            }
        }
        rows = ac.normalize_holders(payload)
        self.assertEqual(rows[0]["entityId"], "bitget")
        self.assertEqual(rows[0]["entityName"], "Bitget")
        self.assertEqual(rows[0]["entityType"], "cex")
        self.assertEqual(rows[1]["entityId"], "")
        self.assertEqual(rows[1]["label"], "Gnosis Safe Proxy")

    def test_skips_broken_rows_instead_of_crashing(self):
        payload = {
            "addressTopHolders": {
                "bsc": [
                    {"address": None},
                    {"address": {"address": ""}},
                    _holder("0xD", 0.01),
                ]
            }
        }
        rows = ac.normalize_holders(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["address"], "0xD")

    def test_pct_falls_back_to_balance_over_supply(self):
        """Solana 上很多币不给 pctOfCap —— 必须用 balance/totalSupply 自己算。"""
        payload = {
            "totalSupply": {"solana": 3_404_374.677815482},
            "addressTopHolders": {
                "solana": [
                    {"address": {"address": "Mint1", "chain": "solana"}, "balance": 465_848.77, "usd": 0},
                ]
            },
        }
        rows = ac.normalize_holders(payload)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["pct"], 13.68, places=1)

    def test_explicit_pct_wins_over_fallback(self):
        payload = {
            "totalSupply": {"bsc": 100.0},
            "addressTopHolders": {
                "bsc": [
                    {"address": {"address": "0xA", "chain": "bsc"}, "balance": 50.0, "pctOfCap": 0.0389, "usd": 1},
                ]
            },
        }
        rows = ac.normalize_holders(payload)
        self.assertAlmostEqual(rows[0]["pct"], 3.89, places=6)

    def test_no_supply_and_no_pct_stays_zero(self):
        payload = {"addressTopHolders": {"bsc": [
            {"address": {"address": "0xA", "chain": "bsc"}, "balance": 5.0},
        ]}}
        rows = ac.normalize_holders(payload)
        self.assertEqual(rows[0]["pct"], 0.0)

    def test_missing_sections_returns_empty(self):
        self.assertEqual(ac.normalize_holders({}), [])
        self.assertEqual(ac.normalize_holders({"addressTopHolders": None}), [])
        self.assertEqual(ac.normalize_entity_holders({}), [])


class FakeResponse:
    def __init__(self, status, body="{}"):
        self.status_code = status
        self.text = body
        self._body = body

    def json(self):
        import json

        return json.loads(self._body)


class FakeSession:
    """签名故意写成和 ``requests.Session.get`` 一致（**不吃 **kwargs**）。

    这里曾经写成 ``**kwargs``，于是 ``trust_env=True`` 这种非法参数一路绿灯，
    真机上 40 个候选全部 TypeError 才发现 —— 假对象必须收窄，才能替真对象挡枪。
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.trust_env = True

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(url)
        return self.responses.pop(0)

    def close(self):
        pass


class ArkhamGetTest(unittest.TestCase):
    def setUp(self):
        self._interval = ac.ARKHAM_MIN_INTERVAL
        self._backoff = ac.ARKHAM_BACKOFF_SECONDS
        ac.ARKHAM_MIN_INTERVAL = 0
        ac.ARKHAM_BACKOFF_SECONDS = 0

    def tearDown(self):
        ac.ARKHAM_MIN_INTERVAL = self._interval
        ac.ARKHAM_BACKOFF_SECONDS = self._backoff

    def test_returns_json_on_200(self):
        session = FakeSession([FakeResponse(200, '{"ok":1}')])
        self.assertEqual(ac.arkham_get("/chains", session=session), {"ok": 1})

    def test_401_raises_auth_error_without_retry(self):
        session = FakeSession([FakeResponse(401, '{"message":"nope"}')])
        with self.assertRaises(ac.ArkhamAuthError):
            ac.arkham_get("/chains", session=session, retries=2)
        # 口令被轮换时重试没有意义 —— 必须只打一次就报。
        self.assertEqual(len(session.calls), 1)

    def test_403_also_raises_auth_error(self):
        session = FakeSession([FakeResponse(403, "")])
        with self.assertRaises(ac.ArkhamAuthError):
            ac.arkham_get("/chains", session=session)

    def test_404_is_a_plain_error_not_auth(self):
        session = FakeSession([FakeResponse(404, '{"message":"token not found"}')])
        with self.assertRaises(ac.ArkhamError) as ctx:
            ac.arkham_get("/token/holders/bsc/0xdead", session=session)
        self.assertNotIsInstance(ctx.exception, ac.ArkhamAuthError)
        self.assertEqual(ctx.exception.status, 404)

    def test_429_is_retried_then_raises(self):
        session = FakeSession([FakeResponse(429), FakeResponse(429)])
        with self.assertRaises(ac.ArkhamError):
            ac.arkham_get("/chains", session=session, retries=1)
        self.assertEqual(len(session.calls), 2)

    def test_429_then_success_recovers(self):
        session = FakeSession([FakeResponse(429), FakeResponse(200, '{"ok":2}')])
        self.assertEqual(ac.arkham_get("/chains", session=session, retries=1), {"ok": 2})


class AddressShapeTest(unittest.TestCase):
    """合约地址形态配不上链时要在本地拦下 —— 送去 Arkham 只会换回一个 400。"""

    def test_evm_chains_require_0x_plus_40_hex(self):
        self.assertTrue(ac.address_matches_chain("bsc", "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82"))
        self.assertFalse(ac.address_matches_chain("bsc", "3ZLekZYq2qkZiSpnSvabjit34tUkjSwD1JFuW9as9wBG"))
        self.assertFalse(ac.address_matches_chain("ethereum", "0x123"))

    def test_solana_requires_base58(self):
        self.assertTrue(ac.address_matches_chain("solana", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"))
        self.assertFalse(ac.address_matches_chain("solana", "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82"))

    def test_ct_501_contracts_are_evaluated_as_solana(self):
        """CT_501 是 Solana —— 映射错的话这里会拿 EVM 规则去卡 base58，直接 False。"""
        mint = "3ZLekZYq2qkZiSpnSvabjit34tUkjSwD1JFuW9as9wBG"
        self.assertEqual(ac.arkham_chain_slug("CT_501"), "solana")
        self.assertTrue(ac.address_matches_chain(ac.arkham_chain_slug("CT_501"), mint))


class FetchTokenHoldersTest(unittest.TestCase):
    def test_mismatched_address_is_rejected_without_request(self):
        result = ac.fetch_token_holders("bsc", "3ZLekZYq2qkZiSpnSvabjit34tUkjSwD1JFuW9as9wBG",
                                       session=FakeSession([]))
        self.assertEqual(result["holders"], [])
        self.assertIn("不匹配", result["reason"])

    def test_unsupported_chain_returns_reason_without_request(self):
        result = ac.fetch_token_holders("某条链", "0xabc", session=FakeSession([]))
        self.assertEqual(result["holders"], [])
        self.assertIn("不支持", result["reason"])

    def test_missing_contract_returns_reason(self):
        result = ac.fetch_token_holders("bsc", "", session=FakeSession([]))
        self.assertEqual(result["holders"], [])
        self.assertIn("合约", result["reason"])


if __name__ == "__main__":
    unittest.main()
