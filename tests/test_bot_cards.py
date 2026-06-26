from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import unittest

from pulse_desk.bot.cards import home_card, summary_card


class HomeCardTests(unittest.TestCase):
    def _card(self, **over):
        data = dict(
            role="admin", new_pings=12, urgent=3, accounts_online=4,
            accounts_total=4, fresh_checks=2, last_scan="06-26 14:02 · ok",
        )
        data.update(over)
        return home_card(**data)

    def test_shows_brand_and_owner_badge(self):
        out = self._card(role="admin")
        self.assertIn("**PULSE DESK**", out)
        self.assertIn("владелец", out)

    def test_viewer_badge(self):
        self.assertIn("просмотр", self._card(role="viewer"))

    def test_counters_present_and_monospaced(self):
        out = self._card()
        self.assertIn("Новых пингов: `12`", out)
        self.assertIn("Срочных: `3`", out)
        self.assertIn("Аккаунты: `4/4`", out)
        self.assertIn("Чеки: `2`", out)
        self.assertIn("Скан: `06-26 14:02 · ok`", out)

    def test_has_section_prompt(self):
        self.assertIn("Выберите раздел", self._card())


class SummaryCardTests(unittest.TestCase):
    ANALYTICS = {"total_pings": 1240, "new_pings": 12, "favorites": 8}
    MARKET = {"btc": 98420, "eth": 3510, "ton": 5.23, "sol": 182.4}
    SYSTEM = {
        "version": "1.5.0", "uptime": "12ч 30м", "db_mb": 8.4,
        "accounts_online": 4, "accounts_total": 4, "last_scan": "06-26 14:02 · ok",
    }

    def test_header_and_three_blocks(self):
        out = summary_card(analytics=self.ANALYTICS, market=self.MARKET, system=self.SYSTEM)
        self.assertIn("**СВОДКА**", out)
        self.assertIn("Записей: `1240`", out)
        self.assertIn("BTC", out)
        self.assertIn("Система", out)
        self.assertIn("v1.5.0", out)

    def test_market_values_rendered(self):
        out = summary_card(analytics=self.ANALYTICS, market=self.MARKET, system=self.SYSTEM)
        self.assertIn("98,420", out)
        self.assertIn("5.230", out)

    def test_missing_market_shows_empty_state(self):
        out = summary_card(analytics=self.ANALYTICS, market=None, system=self.SYSTEM)
        self.assertIn("📭", out)
        self.assertNotIn("BTC", out)


if __name__ == "__main__":
    unittest.main()
