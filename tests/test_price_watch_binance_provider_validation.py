import unittest
from unittest.mock import Mock, patch

import server


class PriceWatchBinanceProviderValidationTests(unittest.TestCase):
    def test_settled_binance_contract_is_rejected_before_kline_request(self):
        with (
            patch.object(server, "binance_futures_contract_status", return_value="SETTLING"),
            patch.object(server.requests, "get") as request,
        ):
            with self.assertRaisesRegex(RuntimeError, "not trading"):
                server.price_watch_candles_from_binance("RAY", True)

        request.assert_not_called()

    def test_zero_volume_futures_candles_are_rejected(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            [index, "0.248", "0.248", "0.248", "0.248", "0"]
            for index in range(24)
        ]
        with (
            patch.object(server, "binance_futures_contract_status", return_value="TRADING"),
            patch.object(server.requests, "get", return_value=response),
        ):
            with self.assertRaisesRegex(RuntimeError, "no recent volume"):
                server.price_watch_candles_from_binance("RAY", True)


if __name__ == "__main__":
    unittest.main()
