from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8-sig")


class NoDiscordSelfbotTests(unittest.TestCase):
    def test_discord_selfbot_monitoring_is_absent(self) -> None:
        server = read("server.py")
        sources = read("newsflash_sources.py")
        ui = read("price-watch.js")

        forbidden = (
            "NEWSFLASH_DISCORD_USER_TOKEN",
            "NEWSFLASH_DISCORD_TOKEN",
            "DISCORD_OPPORTUNITY_",
            "discord_channel_monitors",
            "discord.com/api/v9",
            "poll_discord_opportunity_monitor_once",
            "start_discord_opportunity_monitor",
        )
        combined = "\n".join((server, sources, ui, read(".env.example")))
        for marker in forbidden:
            self.assertNotIn(marker, combined)

    def test_ca_discord_push_is_removed_but_price_bot_push_remains(self) -> None:
        server = read("server.py")
        self.assertNotIn("CA_MONITOR_DISCORD_", server)
        self.assertNotIn("send_ca_monitor_discord_alert", server)
        self.assertNotIn("enqueue_ca_monitor_discord_alert", server)
        self.assertIn("def send_price_watch_discord_alerts", server)
        self.assertIn("PRICE_WATCH_DISCORD_BOT_TOKEN", server)

    def test_desktop_config_never_copies_generic_discord_secrets(self) -> None:
        script = read("desktop/prepare-desktop-config.ps1")
        self.assertNotIn('"DISCORD_"', script)
        self.assertIn('"PRICE_WATCH_"', script)

    def test_formula_news_uses_public_rss_not_discord(self) -> None:
        sources = read("newsflash_sources.py")
        self.assertIn('"url": "https://rss-public.bwe-ws.com"', sources)
        self.assertNotIn('"url": "https://discord.com/', sources)

    def test_inbound_discord_is_rejected_while_outbound_push_is_retained(self) -> None:
        research = read("onchain_fast_research.py")
        server = read("server.py")
        self.assertIn("discord-monitor-disabled", research)
        self.assertNotIn('item.get("platform") == "discord" and "方程式新闻"', research)
        self.assertIn("def send_price_watch_discord_alerts", server)
        self.assertIn("def start_discord_newsflash_bridge", server)
