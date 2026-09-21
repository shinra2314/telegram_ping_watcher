"""Stage-five pieces: the owner card's panel button and the converter chart."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

from pulse_desk import bot_notify  # noqa: E402
from pulse_desk.app_ctx import state  # noqa: E402
from routers.miniapp.market import history_points  # noqa: E402


def labels(buttons) -> list[str]:
    return [getattr(b, "text", "") for row in buttons for b in row]


class PanelButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_off_by_default(self):
        self.assertIsNone(await bot_notify.panel_card_screen({"is_win": 1}, {"panel_button": False}))

    async def test_a_win_links_to_its_giveaway_card(self):
        self.assertEqual(await bot_notify.panel_card_screen({"is_win": 1}, {"panel_button": True}), "giveaway")
        self.assertEqual(await bot_notify.panel_card_screen({}, {"panel_button": True}), "ping")

    def test_no_tunnel_no_button(self):
        with patch.object(state, "public_url", None):
            buttons = bot_notify._admin_card_buttons("https://t.me/c/1", 5, "channel", "giveaway")
        self.assertNotIn("🛰 В панели", labels(buttons))

    def test_the_button_carries_the_record(self):
        with patch.object(state, "public_url", "https://pd.example.ts.net"):
            buttons = bot_notify._admin_card_buttons("https://t.me/c/1", 5, "channel", "giveaway")
        panel = [b for row in buttons for b in row if getattr(b, "text", "") == "🛰 В панели"]
        self.assertEqual(panel[0].url, "https://pd.example.ts.net/app?s=giveaway&id=5")


class ServiceWorkerTests(unittest.TestCase):
    def test_worker_is_served_from_the_root_as_javascript(self):
        from fastapi.testclient import TestClient

        from pulse_desk.miniapp_server import build_miniapp

        res = TestClient(build_miniapp()).get("/app-sw.js")
        self.assertEqual(res.status_code, 200)
        self.assertIn("javascript", res.headers["content-type"])
        # It must never answer for the data: /api stays network-only.
        self.assertIn("SHELL", res.text)
        self.assertNotIn("/api/", res.text.split("const SHELL")[1].split("\n")[0])


class HistoryPointsTests(unittest.TestCase):
    SNAP = {"bitcoin": {"usd": 60000.0}, "_fiat": {"USD": 1.0, "UAH": 40.0}}

    def snaps(self, prices):
        # get_market_history hands them over newest first.
        return [{"bitcoin": {"usd": p}, "_fiat": {"USD": 1.0}, "fetched_at_iso": f"2026-09-2{i}T10:00:00"}
                for i, p in reversed(list(enumerate(prices)))]

    def test_oldest_first_and_priced(self):
        pts = history_points(self.snaps([100.0, 110.0, 120.0]), "BTC", "USD")
        self.assertEqual([p["v"] for p in pts], [100.0, 110.0, 120.0])

    def test_a_snapshot_that_cannot_price_the_pair_is_skipped(self):
        snaps = self.snaps([100.0, 120.0])
        snaps.insert(1, {"fetched_at_iso": "2026-09-20T12:00:00"})
        self.assertEqual([p["v"] for p in history_points(snaps, "BTC", "USD")], [100.0, 120.0])

    def test_thinned_but_keeps_the_latest(self):
        pts = history_points(self.snaps([float(i) for i in range(1, 11)]), "BTC", "USD", points=4)
        self.assertEqual(len(pts), 4)
        self.assertEqual(pts[-1]["v"], 10.0)


if __name__ == "__main__":
    unittest.main()
