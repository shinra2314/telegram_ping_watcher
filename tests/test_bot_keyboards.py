from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import unittest

from pulse_desk.bot.keyboards import back_home, section_nav


class SectionNavTests(unittest.TestCase):
    def test_one_row_home_then_refresh(self):
        rows = section_nav(b"menu_stats")
        self.assertEqual(len(rows), 1)
        labels = [b.text for b in rows[0]]
        self.assertEqual(labels, ["⬅️ Домой", "🔄 Обновить"])

    def test_home_button_targets_menu_main(self):
        rows = section_nav(b"menu_stats")
        self.assertEqual(rows[0][0].data, b"menu_main")

    def test_refresh_button_carries_given_callback(self):
        rows = section_nav(b"menu_market")
        self.assertEqual(rows[0][1].data, b"menu_market")


class BackHomeTests(unittest.TestCase):
    def test_single_home_button(self):
        rows = back_home()
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 1)
        self.assertEqual(rows[0][0].data, b"menu_main")
        self.assertEqual(rows[0][0].text, "⬅️ Домой")


from pulse_desk.bot.keyboards import MON_FILTERS, feed_keyboard, ping_card_keyboard


class FeedKeyboardTests(unittest.TestCase):
    ITEMS = [(842, "🔥 14:02 @chan"), (840, "• 13:40 @chan2")]

    def test_each_item_is_a_row_opening_that_ping(self):
        rows = feed_keyboard(self.ITEMS, "all")
        self.assertEqual(rows[0][0].data, b"mon:open:842")
        self.assertEqual(rows[1][0].data, b"mon:open:840")

    def test_filter_row_has_all_filters(self):
        rows = feed_keyboard(self.ITEMS, "all")
        filt = rows[len(self.ITEMS)]
        datas = [b.data for b in filt]
        self.assertIn(b"mon:feed:all", datas)
        self.assertIn(b"mon:feed:check", datas)
        self.assertIn(b"mon:feed:win", datas)
        self.assertIn(b"mon:feed:important", datas)

    def test_active_filter_is_marked(self):
        rows = feed_keyboard(self.ITEMS, "check")
        filt = rows[len(self.ITEMS)]
        active = [b.text for b in filt if b.data == b"mon:feed:check"][0]
        inactive = [b.text for b in filt if b.data == b"mon:feed:all"][0]
        self.assertNotEqual(active, "Чеки")
        self.assertEqual(inactive, "Все")

    def test_footer_home_and_refresh_keep_filter(self):
        rows = feed_keyboard(self.ITEMS, "win")
        footer = rows[-1]
        self.assertEqual(footer[0].data, b"menu_main")
        self.assertEqual(footer[1].data, b"mon:feed:win")

    def test_empty_feed_still_has_filter_and_footer(self):
        rows = feed_keyboard([], "all")
        self.assertEqual(len(rows), 2)  # filter row + footer


class PingCardKeyboardTests(unittest.TestCase):
    def test_admin_gets_fav_and_read(self):
        rows = ping_card_keyboard(842, is_admin=True)
        datas = [b.data for row in rows for b in row]
        self.assertIn(b"ping:fav:842", datas)
        self.assertIn(b"ping:read:842", datas)

    def test_viewer_has_no_mutating_actions(self):
        rows = ping_card_keyboard(842, is_admin=False)
        datas = [b.data for row in rows for b in row]
        self.assertNotIn(b"ping:fav:842", datas)
        self.assertNotIn(b"ping:read:842", datas)

    def test_back_returns_to_feed_and_refresh_reopens(self):
        rows = ping_card_keyboard(842, is_admin=False)
        footer = rows[-1]
        self.assertEqual(footer[0].data, b"mon:feed:all")
        self.assertEqual(footer[1].data, b"mon:open:842")


from pulse_desk.bot.keyboards import giveaway_card_keyboard, giveaway_feed_keyboard


class GiveawayKeyboardTests(unittest.TestCase):
    def test_items_open_giveaway(self):
        rows = giveaway_feed_keyboard([(50, "⏰ 06-27 @gw")])
        self.assertEqual(rows[0][0].data, b"gw:open:50")

    def test_feed_footer_home_and_refresh(self):
        rows = giveaway_feed_keyboard([])
        footer = rows[-1]
        self.assertEqual(footer[0].data, b"menu_main")
        self.assertEqual(footer[1].data, b"menu_giveaways")

    def test_card_back_to_section_and_refresh(self):
        rows = giveaway_card_keyboard(50)
        footer = rows[-1]
        self.assertEqual(footer[0].data, b"menu_giveaways")
        self.assertEqual(footer[1].data, b"gw:open:50")

    def test_card_has_no_action_buttons(self):
        datas = [b.data for row in giveaway_card_keyboard(50) for b in row]
        self.assertNotIn(b"ping:fav:50", datas)
        self.assertEqual(len(datas), 2)  # read-only: only back + refresh


if __name__ == "__main__":
    unittest.main()
