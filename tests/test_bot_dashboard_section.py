"""Кнопки пульта и подпись под картинкой.

Пульт — единственный экран, где кнопки строятся из данных: список «что
разобрать» приходит из `build_dashboard_summary`, и каждая строка обязана
открывать то место, где с этим что-то делают. Два риска и проверяются: гость
не должен получить владельческую кнопку, и один и тот же адрес не должен
оказаться на клавиатуре дважды.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
for path in (str(ROOT), str(SRC_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from pulse_desk.bot.sections import dashboard, market  # noqa: E402
from pulse_desk.bot.views import FeedFilter  # noqa: E402


def summary(*kinds: str) -> dict:
    return {
        "headline": "Есть что разобрать",
        "attention": [{"kind": k, "title": k, "text": "", "value": 1, "tone": "warn"}
                      for k in kinds],
        "counts": {},
        "scan_progress": {},
    }


def datas(rows) -> list[bytes]:
    return [b.data for row in rows for b in row]


class FocusButtonTests(unittest.TestCase):
    def test_new_and_important_open_the_feed_already_filtered(self):
        rows = dashboard.keyboard(summary("new", "important"), is_admin=True)
        self.assertIn(FeedFilter(status="n").cb(), datas(rows))
        self.assertIn(FeedFilter(type="i").cb(), datas(rows))

    def test_a_giveaway_signal_opens_the_board_not_the_feed(self):
        # Решение по розыгрышу принимается на доске — туда и ведём.
        rows = dashboard.keyboard(summary("giveaway-action"), is_admin=True)
        self.assertIn(b"menu_giveaways", datas(rows))

    def test_two_signals_pointing_at_one_place_give_one_button(self):
        rows = dashboard.keyboard(summary("giveaway-action", "manual"), is_admin=True)
        self.assertEqual(datas(rows).count(b"menu_giveaways"), 1)

    def test_owner_only_focus_is_hidden_from_a_guest(self):
        guest = datas(dashboard.keyboard(summary("events", "scan"), is_admin=False))
        owner = datas(dashboard.keyboard(summary("events", "scan"), is_admin=True))
        self.assertNotIn(b"menu_logs", guest)
        self.assertNotIn(b"menu_scan", guest)
        self.assertIn(b"menu_logs", owner)

    def test_unknown_signal_kind_is_simply_skipped(self):
        rows = dashboard.keyboard(summary("something-new"), is_admin=True)
        self.assertNotIn(b"", datas(rows))
        self.assertIn(b"menu_main", datas(rows))

    def test_calm_dashboard_still_offers_navigation(self):
        rows = dashboard.keyboard(summary("calm"), is_admin=False)
        self.assertIn(FeedFilter().cb(), datas(rows))
        self.assertIn(b"menu_giveaways", datas(rows))
        self.assertIn(b"menu_summary", datas(rows))

    def test_every_button_fits_the_callback_limit(self):
        rows = dashboard.keyboard(summary(*dashboard.FOCUS_TARGETS), is_admin=True)
        for data in datas(rows):
            self.assertLessEqual(len(data), 64, data)


class CaptionTests(unittest.TestCase):
    def test_headline_and_signals_are_listed(self):
        text = dashboard.caption(summary("new", "important"))
        self.assertIn("Есть что разобрать", text)
        self.assertIn("new", text)

    def test_caption_stays_within_the_media_caption_limit(self):
        many = summary(*(["new"] * 20))
        self.assertLessEqual(len(dashboard.caption(many)), 1024)


class MarketPeriodTests(unittest.TestCase):
    def test_periods_are_offered_and_the_current_one_is_marked(self):
        rows = market.keyboard("w")
        labels = [b.text for b in rows[0]]
        self.assertTrue(any(text.startswith("▸") and "недел" in text for text in labels))
        self.assertEqual(len(rows[0]), len(market.PERIODS))

    def test_refresh_keeps_the_period(self):
        self.assertIn(b"mk:p:w", datas(market.keyboard("w")))

    def test_converter_is_one_tap_away(self):
        self.assertIn(b"cv", datas(market.keyboard("d")))


if __name__ == "__main__":
    unittest.main()
