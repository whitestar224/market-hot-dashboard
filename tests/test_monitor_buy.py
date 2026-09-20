import copy
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import time
import threading
import unittest
from unittest.mock import Mock, patch
from contextlib import closing
import requests
from monitor_buy import (DEFAULT_MAX_ORDER_USDT, MonitorBuyService, SOLANA, SOL_NATIVE, ZERO,
                         address, decode_abi_text, units, digest, funding_kind, funding_tier, validate_quote,
                         validate_solana_instructions, FundingUnavailable)
from session_buy import MODE as SESSION_MODE, ROLE_KEY as SESSION_ROLE_KEY, RpcCallReverted

OWNER = "0x" + "12" * 20
TOKEN = "0x" + "34" * 20
DEPOSIT = "0x" + "56" * 20
ORDER = "0x" + "78" * 32
REQUEST = "0x" + "9a" * 32


def fixture(stable=False, same_chain=False):
    source = {"chainId": 8453, "address": TOKEN if stable else ZERO, "decimals": 6 if stable else 18,
              "symbol": "USDC" if stable else "ETH", "owner": OWNER, "priceUsd": "1" if stable else "2000",
              "balanceUsd": "1000", "balance": "100000000000000000000", "nativeBalance": "1000000000000000000", "chainLabel": "Base"}
    target = {"chainId": 8453 if same_chain else 4663, "address": "0x" + "ab" * 20, "decimals": 6, "symbol": "USDG"}
    amount = "10000000" if stable else "5000000000000000"
    intent = {"source": source, "target": target, "sender": OWNER, "recipient": OWNER, "amount": amount,
              "amountUsd": "10", "amountUsdt": "10", "slippageBps": 500}
    networks = {8453: {"id": 8453, "name": "base", "protocol": {"v2": {"chainId": "base", "depository": DEPOSIT}}},
                4663: {"id": 4663, "name": "robinhood", "protocol": {"v2": {"chainId": "robinhood", "depository": DEPOSIT}}}}
    dest = "base" if same_chain else "robinhood"
    payment_in = {"chainId": "base", "currency": source["address"], "amount": amount}
    calldata = "0x49290c1c" + OWNER[2:].rjust(64, "0") + ORDER[2:]
    if stable:
        calldata = "0xe8017952" + OWNER[2:].rjust(64, "0") + TOKEN[2:].rjust(64, "0") + format(int(amount), "064x") + ORDER[2:]
    tx = {"from": OWNER, "to": DEPOSIT, "chainId": 8453, "data": calldata, "value": "0" if stable else amount}
    q = {"details": {"sender": OWNER, "recipient": OWNER,
        "currencyIn": {"currency": source, "amount": amount, "amountUsd": "10"},
        "currencyOut": {"currency": target, "amount": "10000001", "minimumAmount": "9500000"},
        "slippageTolerance": {"total": "500"}, "swapImpact": {"percent": "-0.1"}, "totalImpact": {"percent": "-0.3"}},
        "fees": {"app": {"amount": "0"}, "gas": {"amount": "100", "amountUsd": "0.01"}},
        "steps": [{"kind": "transaction", "requestId": REQUEST, "items": [{"status": "incomplete", "data": tx,
            "check": {"method": "GET", "endpoint": "/intents/status/v3?requestId=" + REQUEST}}]}],
        "protocol": {"v2": {"orderId": ORDER, "paymentDetails": {**payment_in, "depository": DEPOSIT},
            "orderData": {"inputs": [{"payment": payment_in, "refunds": [{"chainId": "base", "recipient": OWNER}]}],
                "output": {"chainId": dest, "payments": [{"recipient": OWNER, "currency": target["address"], "minimumAmount": "9500000"}], "calls": []}, "fees": []}}}}
    if stable:
        q["steps"].insert(0, {"kind": "transaction", "requestId": REQUEST, "items": [{"data": {
            "from": OWNER, "to": TOKEN, "chainId": 8453, "value": "0", "data": "0x095ea7b3" + DEPOSIT[2:].rjust(64, "0") + "f"*64}}]})
    return q, intent, networks


class MonitorBuyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = MonitorBuyService(Path(self.temp.name)/"orders.sqlite")

    def tearDown(self):
        self.service.warm_pool.shutdown(wait=True, cancel_futures=True)
        self.service.pool.shutdown(wait=True, cancel_futures=True)
        self.service.read_pool.shutdown(wait=True, cancel_futures=True)
        self.temp.cleanup()

    def test_same_chain_stable_is_first(self):
        rows = [(56, ZERO, "BNB"), (56, TOKEN, "USDT"), (56, TOKEN, "WETH"),
                (1, TOKEN, "USDC"), (1, ZERO, "ETH"), (1, TOKEN, "WBTC")]
        self.assertEqual([funding_tier({"address": a, "chainId": c, "symbol": s}, 56)
                          for c, a, s in rows], [1, 0, 2, 3, 4, 5])
        self.assertEqual(str(DEFAULT_MAX_ORDER_USDT), "1000")

    def test_wallet_discovery_only_uses_bridgeable_directory_tokens(self):
        network = {"id": 56, "currency": {"address": ZERO, "symbol": "BNB", "name": "BNB", "decimals": 18},
                   "erc20Currencies": [
                       {"address": TOKEN, "symbol": "USDT", "decimals": 18, "supportsBridging": True},
                       {"address": "0x" + "45" * 20, "symbol": "SOMI", "decimals": 18, "supportsBridging": True},
                       {"address": "0x" + "67" * 20, "symbol": "DROP", "decimals": 18, "supportsBridging": False}]}
        tokens = self.service.tokens(network)
        self.assertEqual([item["symbol"] for item in tokens], ["BNB", "USDT", "SOMI"])
        self.assertEqual([funding_kind(item) for item in tokens], ["native", "stable", "other"])
        self.assertTrue(all(item["directorySource"] == "relay-chains" for item in tokens))

    def test_connection_pool_is_reused_per_worker_without_automatic_retries(self):
        main_session = self.service.http_session()
        worker_session = self.service.pool.submit(self.service.http_session).result()
        try:
            self.assertIs(main_session, self.service.http_session())
            self.assertIsNot(main_session, worker_session)
            self.assertEqual(main_session.get_adapter('https://api.relay.link').max_retries.total, 0)
        finally:
            main_session.close()
            worker_session.close()

    def test_bsc_rpc_prefers_free_trusted_endpoint_and_falls_back_read_only(self):
        failed = Mock()
        failed.raise_for_status.side_effect = requests.HTTPError("temporary")
        working = Mock()
        working.raise_for_status.return_value = None
        working.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {"status": "0x1"}}
        session = Mock()
        session.post.side_effect = [failed, working]
        self.service.http_session = Mock(return_value=session)
        network = {"id": 56, "httpRpcUrl": "https://bsc-rpc.publicnode.com"}
        public_address = [(None, None, None, None, ("8.8.8.8", 443))]
        with patch("monitor_buy.socket.getaddrinfo", return_value=public_address):
            self.assertEqual(self.service.rpc(network, "eth_getTransactionReceipt", [ORDER]), {"status": "0x1"})
        self.assertEqual([call.args[0] for call in session.post.call_args_list],
                         ["https://bsc-dataseed.binance.org", "https://bsc-rpc.publicnode.com"])

    def test_signed_broadcast_uses_one_node_once_and_never_read_fallback(self):
        session = Mock()
        session.post.side_effect = requests.Timeout("ambiguous")
        self.service.http_session = Mock(return_value=session)
        network = {"id": 56, "httpRpcUrl": "https://bsc-rpc.publicnode.com"}
        public_address = [(None, None, None, None, ("8.8.8.8", 443))]
        with patch("monitor_buy.socket.getaddrinfo", return_value=public_address):
            with self.assertRaises(requests.Timeout):
                self.service.session_rpc(network, "eth_sendRawTransaction", ["0x1234"])
            with self.assertRaisesRegex(ValueError, "单节点"):
                self.service.rpc(network, "eth_sendRawTransaction", ["0x1234"])
        self.assertEqual(session.post.call_count, 1)
        self.assertEqual(session.post.call_args.args[0], "https://bsc-dataseed.binance.org")

    def test_rpc_exposes_contract_revert_but_not_transport_failures(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"jsonrpc": "2.0", "id": 1,
            "error": {"code": 3, "message": "execution reverted: unauthorized"}}
        session = Mock()
        session.post.return_value = response
        self.service.http_session = Mock(return_value=session)
        network = {"id": 56, "httpRpcUrl": "https://bsc-rpc.publicnode.com"}
        public_address = [(None, None, None, None, ("8.8.8.8", 443))]
        with patch("monitor_buy.socket.getaddrinfo", return_value=public_address):
            with self.assertRaises(RpcCallReverted):
                self.service.rpc(network, "eth_call", [{"to": TOKEN, "data": "0x"}, "latest"])
        self.assertEqual(session.post.call_count, 1)

    def test_session_role_snapshot_is_server_fetched_from_fixed_official_index(self):
        role = {"key": "0x" + "01" * 32, "members": [], "targets": [], "lastUpdate": 123}
        response = Mock()
        response.raise_for_status.return_value = None
        response.content = b"{}"
        response.json.return_value = {"data": {"role": role}}
        session = Mock()
        session.post.return_value = response
        self.service.http_session = Mock(return_value=session)
        self.assertIs(self.service.session_role_snapshot(8453, DEPOSIT), role)
        call = session.post.call_args
        self.assertEqual(call.args[0], "https://gnosisguild.squids.live/roles:production/api/graphql")
        self.assertEqual(call.kwargs["json"]["variables"]["id"],
                         f"base:{DEPOSIT}:{SESSION_ROLE_KEY}")

    def test_usdt_to_native_and_stable_precision(self):
        self.assertEqual(units("99.9", "2000", 18), 49950000000000000)
        self.assertEqual(units("99.9", "0.999", 6), 100000000)
        for bad in ["NaN", "Infinity", "-Infinity", "no"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError): units(bad, 1, 6)

    def test_contract_identity(self):
        self.assertEqual(address(TOKEN.upper().replace("0X", "0x"), 56), TOKEN)
        valid = "5ExRQUbJiZysXWG7KapGwsQmjYvx4hCeBgwAGvht5qab"
        self.assertEqual(address(valid, SOLANA), valid)
        with self.assertRaises(ValueError): address("1"*33, SOLANA)

    def test_fixed_evm_identity_is_verified_onchain_without_waiting_for_relay_directory(self):
        symbol = "LAPTOP".encode().hex().ljust(64, "0")
        abi_symbol = "0x" + format(32, "064x") + format(6, "064x") + symbol
        responses = {"0x313ce567": "0x" + format(18, "064x"), "0x95d89b41": abi_symbol}
        self.service.rpc = Mock(side_effect=lambda _network, _method, params: responses[params[0]["data"]])
        self.service.http = Mock(side_effect=AssertionError("fixed-chain identity must not wait for Relay"))
        result = self.service.resolve({"chain": "bsc", "contractAddress": TOKEN})
        self.assertEqual((result["chainId"], result["address"], result["symbol"], result["decimals"]),
                         (56, TOKEN, "LAPTOP", 18))
        self.assertEqual(result["identityProvider"], "链上合约只读核验")
        self.service.http.assert_not_called()

    def test_abi_token_symbol_accepts_string_and_legacy_bytes32_but_rejects_invalid_data(self):
        text = "CLOCKERS".encode().hex()
        self.assertEqual(decode_abi_text("0x" + format(32, "064x") + format(8, "064x") + text.ljust(64, "0")), "CLOCKERS")
        self.assertEqual(decode_abi_text("0x" + text.ljust(64, "0")), "CLOCKERS")
        for invalid in ("0x", "0x01", "0x" + "00" * 32):
            with self.assertRaises(ValueError):
                decode_abi_text(invalid)

    def test_valid_quote_floor_rounding(self):
        q,i,n = fixture()
        self.assertIs(validate_quote(q,i,n), q)

    def test_relay_20_percent_bound_and_error_categories(self):
        q,i,n = fixture(); i['slippageBps'] = 2000
        out = 246109134998617996535; minimum = out*8000//10000
        q['details']['currencyOut'].update(amount=str(out), minimumAmount=str(minimum))
        q['protocol']['v2']['orderData']['output']['payments'][0]['minimumAmount']=str(minimum)
        q['details']['slippageTolerance']['total']='2000'
        self.assertIs(validate_quote(q,i,n), q)
        q['details']['currencyOut']['minimumAmount']=str(minimum-1)
        with self.assertRaisesRegex(ValueError, '最低到账量'): validate_quote(q,i,n)
        q['details']['currencyOut']['minimumAmount']='0'
        with self.assertRaisesRegex(ValueError, '数据无效'): validate_quote(q,i,n)
        q['details']['slippageTolerance']['total']='2001'
        with self.assertRaisesRegex(ValueError, '20.01%'): validate_quote(q,i,n)
        q['details']['slippageTolerance'].pop('total')
        with self.assertRaisesRegex(ValueError, '未返回滑点'): validate_quote(q,i,n)

    def test_preparation_is_bounded_and_read_only(self):
        payload=self.mock_quote_service()
        self.service.pool.shutdown(wait=True)
        self.service.pool=Mock()
        self.assertTrue(self.service.prepare(payload)['queued'])
        self.assertFalse(self.service.prepare(payload)['queued'])
        self.service.pool.submit.assert_called_once()
        self.service.pool.submit.call_args.args[0]()
        self.service.security.assert_called_once()
        self.service.binance_adapter.assert_not_called()
        self.service.balances.assert_not_called()
        with closing(self.service.db()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0],0)

    def test_fresh_simulation_reuse_is_bound_to_order_without_extending_age(self):
        payload=self.mock_quote_service()
        adapter=self.service.binance_adapter.return_value
        original=adapter.build.side_effect
        def built(intent, network):
            q=original(intent,network);q['_simulatedAt']=time.time()-1;return q
        adapter.build.side_effect=built
        preview=self.service.quote(payload)
        self.service.authorize({**payload,**preview})
        _,record=self.service.order(preview['orderId']); stamp=record['preflightAt']
        self.service.preflight(preview)
        adapter.verify_router.assert_called_once()
        adapter.simulate.assert_not_called()
        adapter.chain_simulate.assert_called_once()
        _,record=self.service.order(preview['orderId'])
        self.assertEqual(record['preflightAt'],stamp)
        self.assertNotIn('_simulatedAt',record['quote'])
        with closing(self.service.db()) as conn,conn:
            record['preflightAt']=time.time()-11
            conn.execute('UPDATE orders SET data=? WHERE id=?',(json.dumps(record),preview['orderId']))
        self.service.preflight(preview)
        adapter.simulate.assert_called_once()
        self.assertEqual(adapter.chain_simulate.call_count, 2)

    def test_approval_flow_never_reuses_a_previous_simulation(self):
        payload=self.mock_quote_service(stable=True)
        preview=self.service.quote(payload);self.service.authorize({**payload,**preview})
        self.service.preflight(preview);self.service.preflight(preview)
        self.assertEqual(self.service.binance_adapter.return_value.simulate.call_count,2)

    def test_approval_limited_to_input(self):
        q,i,n = fixture(stable=True)
        validate_quote(q,i,n)
        self.assertEqual(int(q["steps"][0]["items"][0]["data"]["data"][-64:],16), int(i["amount"]))

    def test_fail_closed_quote_tampering(self):
        edits = [
            lambda q: q["details"].update(recipient=TOKEN),
            lambda q: q["details"]["currencyIn"].update(amount="1"),
            lambda q: q["details"]["currencyOut"]["currency"].update(address=TOKEN),
            lambda q: q["details"]["slippageTolerance"].update(total="1000"),
            lambda q: q["details"]["currencyOut"].update(minimumAmount="9400000"),
            lambda q: q["details"]["swapImpact"].update(percent="3.1"),
            lambda q: q["details"]["totalImpact"].update(percent="8.1"),
            lambda q: q["fees"]["app"].update(amount="1"),
            lambda q: q["protocol"]["v2"]["paymentDetails"].update(amount="1"),
            lambda q: q["protocol"]["v2"]["orderData"]["output"].update(calls=[{}]),
            lambda q: q["steps"][0]["items"][0]["data"].update(to=TOKEN),
            lambda q: q["steps"][0]["items"][0]["data"].update(value="-1"),
            lambda q: q["steps"][0]["items"][0]["data"].update(data="0xdeadbeef" + ORDER[2:]),
            lambda q: q["steps"][0]["items"][0]["check"].update(endpoint="https://evil.invalid/"),
            lambda q: q["steps"][0].update(kind="signature"),
        ]
        for index, edit in enumerate(edits):
            q,i,n = fixture(); q=copy.deepcopy(q)
            edit(q)
            with self.subTest(index=index), self.assertRaises(ValueError): validate_quote(q,i,n)

    def mock_quote_service(self, stable=False, same_chain=True):
        q,i,n = fixture(stable, same_chain)
        self.service.identities.require = Mock(return_value=i["target"])
        self.service.resolve = Mock(side_effect=AssertionError("CA must be resolved before quote"))
        self.service.networks = Mock(return_value=n)
        self.service.security = Mock(return_value={"verified": True, "hardBlocked": False})
        self.service.balances = Mock(return_value={"items": [i["source"]], "checkedChains": [8453], "errors": []})
        self.service.http = Mock(side_effect=lambda path, payload=None, params=None: {"price": "1"} if path.endswith("/price") else copy.deepcopy(q))
        from tests.test_binance_buy import fixture as binance_fixture
        def build(intent, network):
            result, _ = binance_fixture(intent)
            result["_expires"], result["_intent"] = time.time()+60, intent
            return result
        self.service.binance_adapter = Mock(return_value=Mock(build=Mock(side_effect=build)))
        self.service.ai_review = Mock(side_effect=lambda p: {"decision": "allow", "routeId": p["candidates"][0]["routeId"], "reason": "最低到账明确"})
        return {"target": i["target"], "amountUsdt": "10", "wallets": {"evm": OWNER}, "walletProvider": "binance", "sourceChains": [8453]}

    def test_same_chain_skips_ai(self):
        payload = self.mock_quote_service()
        result = self.service.quote(payload)
        self.assertEqual(result["aiReview"]["status"], "not-needed")
        self.service.ai_review.assert_not_called()
        self.service.resolve.assert_not_called()
        self.assertEqual(result["conversion"]["fundingAmountUnits"], "5000000000000000")
        self.assertEqual(result['provider'], 'binance-web3')
        self.assertEqual(result['executionSummary']['approval']['spender'], result['executionSummary']['swapContract'])
        self.assertEqual(result['executionSummary']['targetAddress'], result['target']['address'])
        self.assertEqual(result['executionSummary']['recipient'], result['recipient'])
        self.assertFalse(any(call.args[0] == '/quote/v2' for call in self.service.http.call_args_list))

    def test_funding_failures_distinguish_unreadable_from_insufficient(self):
        payload = self.mock_quote_service()
        for errors, code in ((["Base 原生币余额暂不可读"], "balance-read-failed"), ([], "insufficient-balance")):
            self.service.balances.return_value = {"items": [], "errors": errors, "checkedChains": [8453]}
            stages = []
            with self.subTest(code=code), self.assertRaises(FundingUnavailable) as result:
                self.service.quote(payload, on_progress=stages.append)
            self.assertEqual(result.exception.code, code)
            self.assertNotIn("余额检查完成", str(stages))
            if errors: self.assertIn(errors[0], str(result.exception))
        self.service.ai_review.assert_not_called()

    def test_route_rejection_preserves_reason_without_creating_order(self):
        payload = self.mock_quote_service()
        self.service.binance_adapter.return_value.build.side_effect = ValueError("币安报价未通过")
        with self.assertRaises(FundingUnavailable) as result: self.service.quote(payload)
        self.assertEqual(result.exception.code, "no-valid-route")
        self.assertIn("没有可用买入路线", str(result.exception))
        self.assertIn('币安 Web3 · Base ETH', str(result.exception))
        self.assertFalse(any(call.args[0] == '/quote/v2' for call in self.service.http.call_args_list))
        with closing(self.service.db()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)

    def test_small_route_failure_explains_retry_without_changing_the_amount(self):
        payload = self.mock_quote_service()
        payload["amountUsdt"] = "1"
        self.service.binance_adapter.return_value.build.side_effect = ValueError("连接币安 Web3 接口超时或失败（只读报价已自动重试 1 次）")
        with self.assertRaises(FundingUnavailable) as result:
            self.service.quote(payload)
        self.assertIn("本次金额较小", str(result.exception))
        self.assertIn("自动重试 1 次", str(result.exception))

    def test_same_chain_stable_fast_path_skips_native_and_remote_chains(self):
        payload = self.mock_quote_service(stable=True)
        payload["sourceChains"] = [1, 56, 8453, 4663]
        stages = []
        result = self.service.quote(payload, on_progress=stages.append)
        self.service.balances.assert_called_once_with({**payload, "sourceChains": [8453]}, token_kind="stable", parallel=False)
        self.assertEqual(result["source"]["symbol"], "USDC")
        self.assertEqual(result["coverage"]["checkedChains"], [8453])
        self.assertEqual([s["percent"] for s in stages], [12, 22, 32, 42, 49, 96])

    def test_sufficient_same_chain_stable_failure_falls_back_to_disclosed_native(self):
        payload = self.mock_quote_service(stable=True)
        _q, native_intent, _networks = fixture(same_chain=True)
        stable_source = self.service.balances.return_value["items"][0]
        adapter = self.service.binance_adapter.return_value
        original_build = adapter.build.side_effect
        self.service.balances.side_effect = lambda _payload, token_kind, **_kwargs: {
            "items": [stable_source] if token_kind == "stable" else [native_intent["source"]],
            "checkedChains": [8453], "errors": []}
        adapter.build.side_effect = lambda intent, network: (
            (_ for _ in ()).throw(ValueError("稳定币路线链上模拟失败"))
            if intent["source"]["symbol"] == "USDC" else original_build(intent, network))
        result = self.service.quote(payload)
        self.assertEqual(result["source"]["symbol"], "ETH")
        self.assertTrue(result["fundingFallback"]["used"])
        self.assertIn("稳定币兑换路线未通过", result["fundingFallback"]["reason"])
        self.assertEqual(result["fundingFallback"]["toSymbol"], "ETH")
        self.assertEqual(self.service.balances.call_count, 2)

    def test_stable_and_native_shortfall_uses_same_chain_other_token(self):
        payload = self.mock_quote_service(stable=True)
        other = {**self.service.balances.return_value["items"][0], "symbol": "WETH",
                 "fundingKind": "other", "balanceUsd": "1000"}
        self.service.balances.side_effect = lambda p, token_kind, **kw: {
            "items": [other] if token_kind == "other" else [],
            "checkedChains": p["sourceChains"], "errors": []}
        result = self.service.quote(payload)
        self.assertEqual(result["source"]["symbol"], "WETH")
        self.assertEqual([call.kwargs["token_kind"] for call in self.service.balances.call_args_list],
                         ["stable", "native", "other"])
        self.assertEqual(result["fundingFallback"]["toKind"], "other")
        self.assertFalse(result["fundingFallback"]["crossChain"])
        self.assertIn("原生币余额不足", result["fundingFallback"]["reason"])

    def test_remote_other_token_is_last_cross_chain_fallback(self):
        payload = self.mock_quote_service(stable=True, same_chain=False)
        other = {**self.service.balances.return_value["items"][0], "symbol": "WETH",
                 "fundingKind": "other", "balanceUsd": "1000"}
        self.service.balances.side_effect = lambda p, token_kind, **kw: {
            "items": [other] if token_kind == "other" else [],
            "checkedChains": p["sourceChains"], "errors": []}
        result = self.service.quote(payload)
        self.assertEqual(result["provider"], "relay")
        self.assertEqual(result["source"]["symbol"], "WETH")
        self.assertEqual([call.kwargs["token_kind"] for call in self.service.balances.call_args_list],
                         ["stable", "native", "other"])
        self.assertTrue(result["source"]["chainId"] != result["target"]["chainId"])

    def session_quote(self):
        payload = self.mock_quote_service(stable=True)
        chain = {"chainId": 8453, "safeAddress": OWNER, "rolesAddress": DEPOSIT,
            "stableAddress": TOKEN, "stableSymbol": "USDC", "stableDecimals": 6,
            "sessionAddress": "0x" + "77" * 20, "roleKey": "0x" + "01" * 32,
            "allowanceKey": "0x" + "02" * 32}
        config = {"sessionId": "session-1", "chain": chain}
        self.service.session.require_active = Mock(return_value=config)
        self.service.session.require_quote_policy = Mock(return_value=config)
        self.service.session.status = Mock(return_value={"active": True, "remainingUsdt": "10000"})
        self.service.current_session_role_snapshot = Mock(return_value={"verified": True})
        self.service.rpc = Mock(return_value=hex(10**18))
        payload.update(executionMode=SESSION_MODE, wallets={"evm": TOKEN}, walletProvider="binance",
                       sourceChains=[1, 56, 8453])
        return payload, config, self.service.quote(payload)

    def test_session_quote_uses_safe_fixed_stable_and_never_crosses_chains(self):
        payload, config, result = self.session_quote()
        self.assertEqual(result["executionMode"], SESSION_MODE)
        self.assertEqual(result["recipient"], OWNER)
        self.assertEqual(result["source"]["address"], TOKEN)
        self.assertEqual(result["sessionBuy"]["remainingUsdt"], "10000")
        self.service.session.require_active.assert_called_with(8453, Decimal("10"))
        self.service.session.require_quote_policy.assert_called_once()
        request = self.service.balances.call_args.args[0]
        self.assertEqual(request["wallets"], {"evm": OWNER})
        self.assertEqual(request["sourceChains"], [8453])
        _, record = self.service.order(result["orderId"])
        self.assertTrue(record["intent"]["forceExactApproval"])
        with self.assertRaisesRegex(ValueError, "受限会话"):
            self.service.authorize({**payload, **result})

    def test_session_execute_claims_each_step_once_and_verifies_safe_receipt(self):
        _payload, config, preview = self.session_quote()
        self.service.session.reserve = Mock(return_value={"ok": True})
        self.service.session.mark_reservation = Mock()
        hashes = iter(["0x" + "91" * 32, "0x" + "92" * 32])
        self.service.session.send_call = Mock(side_effect=lambda *args: next(hashes))
        transfer = {"address": preview["target"]["address"], "removed": False,
            "topics": ["0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                       "0x" + "00" * 12 + ("55" * 20), "0x" + "00" * 12 + OWNER[2:]],
            "data": hex(int(preview["details"]["currencyOut"]["minimumAmount"]))}
        receipts = iter([{"transactionHash": "0x" + "91" * 32, "status": "0x1", "logs": []},
                         {"transactionHash": "0x" + "92" * 32, "status": "0x1", "logs": [transfer]}])
        self.service.session.wait_receipt = Mock(side_effect=lambda *args: next(receipts))
        result = self.service.session_execute({"orderId": preview["orderId"], "quoteHash": preview["quoteHash"]})
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["txHashes"]), 2)
        self.assertEqual(self.service.session.send_call.call_count, 2)
        self.assertEqual(len(self.service.order(preview["orderId"])[1]["steps"]), 2)
        self.assertIn("success", [call.args[1] for call in self.service.session.mark_reservation.call_args_list])

    def test_same_chain_native_fallback_does_not_scan_remote_chains(self):
        payload = self.mock_quote_service()
        payload["sourceChains"] = [8453, 1, 56]
        q, intent, _ = fixture(same_chain=True)
        self.service.balances.side_effect = lambda p, token_kind, **kw: {"items": [intent["source"]] if token_kind == "native" else [], "checkedChains": p["sourceChains"], "errors": []}
        result = self.service.quote(payload)
        self.assertEqual(result["source"]["symbol"], "ETH")
        self.assertEqual([call.kwargs["token_kind"] for call in self.service.balances.call_args_list], ["stable", "native"])
        self.assertTrue(all(call.args[0]["sourceChains"] == [8453] for call in self.service.balances.call_args_list))

    def test_native_balance_mode_does_not_read_stable_contracts(self):
        _, intent, networks = fixture(same_chain=True)
        self.service.networks = Mock(return_value=networks)
        self.service.tokens = Mock(return_value=[intent["source"], {**intent["source"], "address": TOKEN, "symbol": "USDC"}])
        self.service.token_balance = Mock(return_value=10**18)
        self.service.http = Mock(return_value={"price": "2000"})
        result = self.service.balances({"sourceChains": [8453], "wallets": {"evm": OWNER}}, token_kind="native")
        self.service.token_balance.assert_called_once()
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["address"], ZERO)

    def test_disconnected_progress_stream_releases_quote_slot(self):
        payload = self.mock_quote_service()
        with self.assertRaises(BrokenPipeError):
            self.service.quote(payload, on_progress=Mock(side_effect=BrokenPipeError()))
        self.assertTrue(self.service.quote(payload)["ok"])

    def test_cross_chain_rules_work_with_no_model_and_bind_authorization(self):
        payload = self.mock_quote_service(same_chain=False)
        self.service.ai_review = Mock(side_effect=AssertionError("must not call AI"))
        result = self.service.quote(payload)
        self.service.ai_review.assert_not_called()
        self.assertEqual([call.kwargs["token_kind"] for call in self.service.balances.call_args_list[:2]], ["stable", "native"])
        self.assertEqual(result["routeReview"]["status"], "rule-checked")
        self.assertEqual(result["routeReview"]["quoteHash"], result["quoteHash"])
        self.assertTrue(self.service.authorize({**payload, **result})['ok'])
        other = self.service.quote(payload)
        _, record = self.service.order(other['orderId'])
        record['routeReview']['quoteHash'] = 'wrong'
        with closing(self.service.db()) as conn, conn:
            conn.execute('UPDATE orders SET data=? WHERE id=?', (json.dumps(record), other['orderId']))
        with self.assertRaisesRegex(ValueError, '规则校验记录'):
            self.service.authorize({**payload, **other})

    def test_cross_chain_stable_is_used_before_remote_native_balance_reads(self):
        payload = self.mock_quote_service(stable=True, same_chain=False)
        result = self.service.quote(payload)
        self.assertEqual(result["provider"], "relay")
        self.assertEqual(result["source"]["symbol"], "USDC")
        self.assertNotEqual(result["source"]["chainId"], result["target"]["chainId"])
        self.service.balances.assert_called_once_with(
            {**payload, "sourceChains": [8453]}, token_kind="stable")

    def test_ai_failure_does_not_block_or_change_quote(self):
        payload = self.mock_quote_service(same_chain=False)
        self.service.ai_review = Mock(side_effect=TimeoutError())
        self.assertTrue(self.service.quote(payload)['ok'])
        self.service.ai_review.assert_not_called()

    def test_rule_selection_checks_expiry_integrity_fees_and_duration(self):
        q, intent, networks = fixture()
        self.service.networks = Mock(return_value=networks)
        q.update(_intent=intent, _expires=time.time()+60)
        expensive = copy.deepcopy(q); expensive['fees']['gas']['amountUsd'] = '2'
        fast = copy.deepcopy(q); fast['details']['timeEstimate'] = 3
        q['details']['timeEstimate'] = 20
        self.assertIs(self.service.choose_cross_chain([expensive, q, fast])[0], fast)
        fast['_expires'] = time.time()+1
        self.assertIs(self.service.choose_cross_chain([q, fast])[0], q)
        with self.assertRaisesRegex(ValueError, '过期'): self.service.choose_cross_chain([fast])
        bad = copy.deepcopy(q); bad['details']['recipient'] = TOKEN
        with self.assertRaises(ValueError): self.service.choose_cross_chain([bad])
        bad = copy.deepcopy(q); bad['_intent']['amountUsd'] = '11'
        with self.assertRaisesRegex(ValueError, '同一买入意图'): self.service.choose_cross_chain([q,bad])

    def test_independent_reads_start_together_and_blocked_risk_never_builds(self):
        payload = self.mock_quote_service(stable=True)
        barrier = threading.Barrier(3)
        funds = self.service.balances.return_value
        def read(value):
            barrier.wait(timeout=3)
            return value
        self.service.usdt_price = Mock(side_effect=lambda: read(1))
        self.service.security = Mock(side_effect=lambda target: read({'verified': True, 'hardBlocked': False}))
        self.service.balances = Mock(side_effect=lambda *a, **k: read(funds))
        self.assertTrue(self.service.quote(payload)['ok'])
        self.service.balances.assert_called_once()
        self.assertFalse(self.service.balances.call_args.kwargs['parallel'])
        self.service.binance_adapter.reset_mock()
        self.service.usdt_price = Mock(return_value=1)
        self.service.balances = Mock(return_value=funds)
        self.service.security = Mock(return_value={'hardBlocked': True})
        with self.assertRaisesRegex(ValueError, '高风险'): self.service.quote(payload)
        self.service.binance_adapter.assert_not_called()

    def test_persistent_no_double_authorize_or_send(self):
        payload = self.mock_quote_service()
        preview = self.service.quote(payload)
        auth = {**payload, "orderId": preview["orderId"], "quoteHash": preview["quoteHash"]}
        result = self.service.authorize(auth)
        with self.assertRaises(ValueError): self.service.authorize(auth)
        fingerprint = digest(result["quote"]["steps"][0]["items"][0]["data"])
        step = {"orderId": preview["orderId"], "fingerprint": fingerprint}
        self.service.preflight({"orderId":preview["orderId"]})
        self.service.claim_step(step)
        restarted = MonitorBuyService(self.service.path)
        try:
            with self.assertRaises(ValueError): restarted.claim_step(step)
        finally: restarted.pool.shutdown()

    def test_wallet_switch_rejected(self):
        payload = self.mock_quote_service()
        preview = self.service.quote(payload)
        with self.assertRaises(ValueError):
            self.service.authorize({**payload, "wallets": {"evm": TOKEN}, "orderId": preview["orderId"], "quoteHash": preview["quoteHash"]})

    def test_expired_quote_rejected(self):
        payload = self.mock_quote_service()
        preview = self.service.quote(payload)
        with closing(self.service.db()) as conn, conn: conn.execute("UPDATE orders SET expires=0")
        with self.assertRaises(ValueError): self.service.authorize({**payload, **preview})

    def test_binance_preflight_required_fresh_and_released_after_error(self):
        payload = self.mock_quote_service()
        preview = self.service.quote(payload)
        execution = self.service.authorize({**payload, **preview})
        request = {'orderId':preview['orderId'], 'fingerprint':digest(execution['quote']['steps'][-1]['items'][0]['data'])}
        with self.assertRaisesRegex(ValueError, '模拟尚未通过'): self.service.claim_step(request)
        adapter = self.service.binance_adapter.return_value
        adapter.simulate.side_effect = ValueError('模拟失败')
        for _ in range(3):
            with self.assertRaisesRegex(ValueError, '模拟失败'): self.service.preflight(request)
        adapter.simulate.side_effect = None
        self.service.preflight(request)
        with closing(self.service.db()) as conn, conn:
            row = conn.execute('SELECT data FROM orders WHERE id=?', (preview['orderId'],)).fetchone()
            record = json.loads(row[0]); record['preflightAt'] = time.time()-16
            conn.execute('UPDATE orders SET data=? WHERE id=?', (json.dumps(record),preview['orderId']))
        with self.assertRaisesRegex(ValueError, '已过期'): self.service.claim_step(request)
        self.service.preflight(request)
        self.assertTrue(self.service.claim_step(request)['ok'])

    def test_binance_steps_cannot_be_claimed_out_of_order(self):
        payload = self.mock_quote_service(stable=True)
        preview = self.service.quote(payload)
        execution = self.service.authorize({**payload, **preview})
        self.service.preflight(preview)
        request = {'orderId':preview['orderId'], 'fingerprint':digest(execution['quote']['steps'][-1]['items'][0]['data'])}
        with self.assertRaisesRegex(ValueError, '顺序'): self.service.claim_step(request)

    def test_high_risk_token_never_quotes(self):
        payload = self.mock_quote_service()
        self.service.security.return_value = {"hardBlocked": True}
        with self.assertRaises(ValueError): self.service.quote(payload)
        self.service.binance_adapter.assert_not_called()
        self.assertTrue(all(call.args[0].endswith('/price') for call in self.service.http.call_args_list))

    def test_volatile_reference_stops_conversion(self):
        payload = self.mock_quote_service()
        self.service.http = Mock(return_value={"price": "0.7"})
        with self.assertRaises(ValueError): self.service.quote(payload)

    def test_solana_exact_native_deposit(self):
        sender = "5ExRQUbJiZysXWG7KapGwsQmjYvx4hCeBgwAGvht5qab"
        vault = "7uTT8Xi5RWXzy7h9XL244GRgEycDYDhLjr3ZyNdXi8pZ"
        program = "99vQwtBwYtrqqD9YSXbdum3KBdxPAVxYTaQ3cfnJSrN2"
        config = "Dodg2HifwU8rmaVVyMyUZDGTRbqAJTyVYxXPwcbNpBKc"
        i = {"source": {"chainId": SOLANA, "address": SOL_NATIVE}, "amount": "100000000", "sender": sender}
        n = {"protocol": {"v2": {"depository": program, "depositoryVault": vault}}}
        tx = {"instructions": [{"programId": program, "data": "0d9e0ddf5fd51c06"+int(i["amount"]).to_bytes(8,"little").hex()+ORDER[2:],
            "keys": [{"pubkey": key, "isSigner": idx==1, "isWritable": idx in (1,3)} for idx,key in enumerate([config,sender,sender,vault,SOL_NATIVE])]}]}
        validate_solana_instructions(tx,i,n,{"orderId":ORDER})
        for mutate in [lambda x:x["instructions"][0].update(programId=sender),
            lambda x:x["instructions"][0]["keys"][3].update(pubkey=sender),
            lambda x:x["instructions"][0]["keys"][0].update(isSigner=True),
            lambda x:x["instructions"][0].update(data="00"*48),
            lambda x:x["instructions"].append(copy.deepcopy(x["instructions"][0]))]:
            changed=copy.deepcopy(tx); mutate(changed)
            with self.assertRaises(ValueError): validate_solana_instructions(changed,i,n,{"orderId":ORDER})

    def test_usdt_reference_and_payment_coin_are_both_converted(self):
        payload = self.mock_quote_service()
        q,i,n = fixture(same_chain=True)
        expected = units("9.99", "2000", 18)
        q["details"]["currencyIn"].update(amount=str(expected),amountUsd="9.99")
        q["steps"][0]["items"][0]["data"]["value"]=str(expected)
        q["protocol"]["v2"]["paymentDetails"]["amount"]=str(expected)
        q["protocol"]["v2"]["orderData"]["inputs"][0]["payment"]["amount"]=str(expected)
        self.service.http = Mock(side_effect=lambda path,payload=None,params=None:{"price":"0.999"} if path.endswith("/price") else copy.deepcopy(q))
        result = self.service.quote(payload)
        self.assertEqual(result["conversion"]["amountUsd"],"9.990")
        self.assertEqual(result["conversion"]["fundingAmountUnits"],str(expected))

    def test_metadata_excludes_other_svm_networks(self):
        self.service.http = Mock(return_value={"chains":[{"id":9286185,"vmType":"svm"},{"id":SOLANA,"vmType":"svm"},{"id":56,"vmType":"evm"}]})
        self.assertEqual(set(self.service.networks()), {SOLANA,56})


if __name__ == "__main__": unittest.main()
