import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from binance_agentic import (
    AgenticWalletReadBridge,
    agentic_wallet_capabilities,
    normalize_binance_meme_rush,
)
from chain_ecosystem_monitor import evaluate_onchain_candidate, scan_onchain_research


NOW = 1_789_200_000_000
BSC_TOKEN = "0x" + "a" * 40


def meme_payload():
    return {
        "code": "000000",
        "data": [{
            "chainId": "56",
            "contractAddress": BSC_TOKEN,
            "symbol": "FAST",
            "name": "Fast Launch",
            "createTime": NOW - 5_000,
            "protocol": 2001,
            "_rankType": 30,
            "price": "0.00001",
            "liquidity": "2500",
            "marketCap": "12000",
            "volume": "4000",
            "count": 10,
            "countBuy": 8,
            "countSell": 2,
            "holders": 12,
            "smartMoneyHolders": 4,
            "smartMoneyHoldingPercent": "3.2",
            "proHolders": 6,
            "proHoldingPercent": "4.8",
            "kolHolders": 2,
            "kolHoldingPercent": "1.6",
            "newWalletHoldingPercent": "9.5",
            "bundlerHolders": 1,
            "bundlerHoldingPercent": "0.7",
            "bnHolders": 3,
            "bnHoldingPercent": "2.1",
            "tagDevWashTrading": False,
            "tagInsiderWashTrading": False,
            "progress": "18.5",
            "narrativeText": {"cn": "刚创建的新币"},
            "socials": {"website": "https://example.com", "twitter": "https://x.com/example"},
        }],
    }


class RecordingStore:
    def __init__(self):
        self.calls = []

    def save_onchain_research_scan(self, rows, **metadata):
        self.calls.append((list(rows), dict(metadata)))


class BinanceAgenticTests(unittest.TestCase):
    def test_meme_rush_normalizes_without_promoting_24h_counts_to_1h(self):
        row = normalize_binance_meme_rush(meme_payload(), "bsc", observed_at=NOW)[0]

        self.assertEqual(row["provider"], "binance-meme-rush")
        self.assertEqual(row["contractAddress"], BSC_TOKEN)
        self.assertEqual(row["launchStage"], "migrated")
        self.assertEqual(row["launchpad"], "four-meme")
        self.assertEqual(row["metrics"]["transactionsH24"], 10)
        self.assertNotIn("transactionsH1", row["metrics"])
        self.assertEqual(row["launchFacts"]["smartMoneyHolders"], 4)
        self.assertEqual(row["launchFacts"]["proHolders"], 6)
        self.assertEqual(row["launchFacts"]["newWalletHoldingPercent"], 9.5)
        self.assertFalse(row["launchFacts"]["washTrading"])
        self.assertIn(BSC_TOKEN, row["tradeUrl"])
        evaluated = evaluate_onchain_candidate(row, now_ms=NOW)
        self.assertEqual(evaluated["decision"], "warming")

    def test_binance_migrated_screen_survives_gmgn_failure(self):
        store = RecordingStore()
        emitted = []

        result = scan_onchain_research(
            store,
            networks=["bsc"],
            observed_at=NOW,
            binance_launch_fetcher=lambda _network, **_kwargs: meme_payload(),
            gmgn_trenches_fetcher=lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("slow")),
            candidate_sink=lambda rows: emitted.append(list(rows)),
            include_non_trench_sources=True,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["discovered"], 1)
        self.assertEqual(result["sourceStatus"]["bsc"], "ok")
        self.assertTrue(emitted)
        self.assertEqual(emitted[0][0]["provider"], "binance-meme-rush")
        self.assertEqual(store.calls[0][1]["source_status"], {"bsc/binance-meme-rush": "ok"})

    def test_scanner_drops_non_migrated_binance_rows_even_if_provider_returns_them(self):
        payload = meme_payload()
        payload["data"][0]["_rankType"] = 10
        store = RecordingStore()

        result = scan_onchain_research(
            store,
            networks=["bsc"],
            observed_at=NOW,
            binance_launch_fetcher=lambda _network, **_kwargs: payload,
            gmgn_trenches_fetcher=lambda _network: {"code": 0, "data": {"completed": []}},
            dexscreener_fetcher=lambda *_args, **_kwargs: {"pairs": []},
            include_non_trench_sources=True,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["discovered"], 0)

    def test_wallet_bridge_only_runs_read_quote_command(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=json.dumps({"success": True, "data": {"toCoinAmount": "9.8"}}),
            stderr="",
        )
        runner = Mock(return_value=completed)
        bridge = AgenticWalletReadBridge(runner=runner)

        with patch.object(AgenticWalletReadBridge, "command_prefix", return_value=["node", "baw.js"]):
            result = bridge.market_quote(
                chain_id=56,
                from_token="0x" + "1" * 40,
                to_token="0x" + "2" * 40,
                amount="10",
            )

        self.assertEqual(result["data"]["toCoinAmount"], "9.8")
        command = runner.call_args.args[0]
        self.assertEqual(command[2:4], ["market-order", "quote"])
        self.assertNotIn("swap", command)
        self.assertNotIn("x402-payment", command)
        self.assertFalse(runner.call_args.kwargs["shell"])

    def test_capabilities_never_enable_paid_or_write_operations(self):
        with patch.object(AgenticWalletReadBridge, "status", return_value={
            "installed": True, "connected": True, "status": "CONNECTED",
        }), patch("binance_agentic._WALLET_STATUS_CACHE", (0.0, {})):
            result = agentic_wallet_capabilities(max_age_seconds=0)

        self.assertTrue(result["quoteEnabled"])
        self.assertFalse(result["executionEnabled"])
        self.assertFalse(result["paidX402Enabled"])
        self.assertFalse(result["supportsCrossChain"])


if __name__ == "__main__":
    unittest.main()
