import os
import unittest
from unittest.mock import patch

from network_proxy import (
    NetworkProxyAdapter,
    RouteProbe,
    apply_proxy_environment,
    collect_proxy_candidates,
    masked_proxy_url,
)


class NetworkProxyHelpersTest(unittest.TestCase):
    def test_direct_route_removes_stale_proxy_and_keeps_local_bypass(self):
        environment = {
            "HTTP_PROXY": "http://127.0.0.1:7890",
            "HTTPS_PROXY": "http://127.0.0.1:7890",
            "NO_PROXY": "example.local",
        }

        apply_proxy_environment(environment, "")

        self.assertNotIn("HTTP_PROXY", environment)
        self.assertNotIn("HTTPS_PROXY", environment)
        self.assertIn("127.0.0.1", environment["NO_PROXY"])
        self.assertIn("example.local", environment["NO_PROXY"])

    def test_candidates_combine_explicit_environment_and_detected_ports(self):
        environment = {
            "XINGYUN_PROXY_CANDIDATES": "127.0.0.1:7897,http://127.0.0.1:7890",
            "HTTPS_PROXY": "http://127.0.0.1:7890",
        }

        result = collect_proxy_candidates(
            environment,
            windows_candidates=["127.0.0.1:10809"],
            local_candidates=["http://127.0.0.1:7897"],
        )

        self.assertEqual(
            result,
            [
                "http://127.0.0.1:7897",
                "http://127.0.0.1:7890",
                "http://127.0.0.1:10809",
            ],
        )

    def test_public_proxy_url_masks_credentials(self):
        self.assertEqual(
            masked_proxy_url("http://name:secret@127.0.0.1:7890"),
            "http://127.0.0.1:7890",
        )


class NetworkProxyAdapterTest(unittest.TestCase):
    def test_auto_prefers_working_direct_tun_over_stale_local_proxy(self):
        environment = {
            "HTTP_PROXY": "http://127.0.0.1:7890",
            "HTTPS_PROXY": "http://127.0.0.1:7890",
            "XINGYUN_NETWORK_PROXY": "auto",
        }

        def fake_probe(route, *, urls):
            reachable = len(tuple(urls)) if not route else 0
            return RouteProbe(route, reachable, len(tuple(urls)))

        adapter = NetworkProxyAdapter(environment=environment, probe=fake_probe)
        with patch("network_proxy.windows_proxy_candidates", return_value=[]), patch(
            "network_proxy.local_proxy_candidates", return_value=[]
        ):
            status = adapter.refresh()

        self.assertEqual(status["route"], "direct")
        self.assertTrue(status["healthy"])
        self.assertNotIn("HTTP_PROXY", environment)
        self.assertNotIn("HTTPS_PROXY", environment)

    def test_auto_uses_alternative_proxy_when_direct_is_blocked(self):
        environment = {
            "XINGYUN_NETWORK_PROXY": "auto",
            "XINGYUN_PROXY_CANDIDATES": "http://127.0.0.1:7897",
        }

        def fake_probe(route, *, urls):
            total = len(tuple(urls))
            reachable = total if route.endswith(":7897") else 0
            return RouteProbe(route, reachable, total)

        adapter = NetworkProxyAdapter(environment=environment, probe=fake_probe)
        with patch("network_proxy.windows_proxy_candidates", return_value=[]), patch(
            "network_proxy.local_proxy_candidates", return_value=[]
        ):
            status = adapter.refresh()

        self.assertEqual(status["route"], "http://127.0.0.1:7897")
        self.assertEqual(environment["HTTPS_PROXY"], "http://127.0.0.1:7897")
        self.assertNotIn("proxyUrl", status)


if __name__ == "__main__":
    unittest.main()
