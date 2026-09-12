from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import unittest

from pulse_desk.bot.views import (
    DIV,
    fmt_dt,
    help_text,
    main_menu_buttons,
    menu_caption,
    paginate,
)


class PaginateTests(unittest.TestCase):
    ROWS = list(range(1, 21))  # 20 rows

    def test_first_page_and_more_flag(self):
        window, has_more = paginate(self.ROWS, page=1, size=8)
        self.assertEqual(window, list(range(1, 9)))
        self.assertTrue(has_more)

    def test_middle_page_continues_where_previous_stopped(self):
        window, has_more = paginate(self.ROWS, page=2, size=8)
        self.assertEqual(window, list(range(9, 17)))
        self.assertTrue(has_more)

    def test_last_page_reports_no_more(self):
        window, has_more = paginate(self.ROWS, page=3, size=8)
        self.assertEqual(window, [17, 18, 19, 20])
        self.assertFalse(has_more)

    def test_page_past_the_end_is_empty(self):
        window, has_more = paginate(self.ROWS, page=9, size=8)
        self.assertEqual(window, [])
        self.assertFalse(has_more)

    def test_page_below_one_is_clamped(self):
        self.assertEqual(paginate(self.ROWS, page=0, size=8)[0], list(range(1, 9)))

    def test_exact_fit_has_no_next_page(self):
        window, has_more = paginate(list(range(8)), page=1, size=8)
        self.assertEqual(len(window), 8)
        self.assertFalse(has_more)


class FmtDtTests(unittest.TestCase):
    def test_none_and_empty_render_dash(self):
        self.assertEqual(fmt_dt(None), "—")
        self.assertEqual(fmt_dt(""), "—")

    def test_iso_trimmed_to_month_day_hour_minute(self):
        self.assertEqual(fmt_dt("2026-06-26T14:02:31"), "06-26 14:02")

    def test_short_value_passed_through(self):
        self.assertEqual(fmt_dt("2026"), "2026")


class MainMenuButtonsTests(unittest.TestCase):
    def _labels(self, role):
        return [b.text for row in main_menu_buttons(role) for b in row]

    def test_viewer_has_no_admin_controls(self):
        labels = self._labels("viewer")
        self.assertIn("📊 Сводка", labels)
        self.assertIn("🎁 Розыгрыши", labels)
        self.assertIn("🔔 Мои уведомления", labels)
        self.assertNotIn("⚙️ Настройки", labels)
        self.assertNotIn("🔑 Ключи", labels)

    def test_summary_replaces_split_views(self):
        for role in ("viewer", "admin"):
            labels = self._labels(role)
            self.assertIn("📊 Сводка", labels)
            self.assertNotIn("🛰 Статус", labels)
            self.assertNotIn("💹 Курсы", labels)

    def test_feed_buttons_target_monitoring(self):
        rows = main_menu_buttons("viewer")
        data = {b.text: b.data for row in rows for b in row}
        self.assertEqual(data["🕐 Последние"], b"mon:feed:all")
        self.assertEqual(data["🎁 Розыгрыши"], b"menu_giveaways")

    def test_analytics_section_is_on_the_home_screen(self):
        data = {b.text: b.data for row in main_menu_buttons("admin") for b in row}
        self.assertEqual(data["📈 Аналитика"], b"an:sum")

    def test_analytics_hidden_from_a_key_without_that_grant(self):
        from pulse_desk.bot_permissions import full_permissions

        perms = full_permissions()
        perms["features"] = [c for c in perms["features"] if c != "analytics"]
        labels = [b.text for row in main_menu_buttons("viewer", perms) for b in row]
        self.assertNotIn("📈 Аналитика", labels)
        self.assertIn("📊 Сводка", labels)

    def test_admin_has_owner_controls(self):
        labels = self._labels("admin")
        self.assertIn("⚙️ Управление", labels)
        self.assertNotIn("🔔 Мои уведомления", labels)
        self.assertNotIn("🔑 Ключи", labels)  # moved into the hub

    def test_admin_management_button_targets_hub(self):
        data = {b.text: b.data for row in main_menu_buttons("admin") for b in row}
        self.assertEqual(data["⚙️ Управление"], b"adm:home")

    def test_admin_has_scan_quick_button(self):
        flat = [b for row in main_menu_buttons("admin") for b in row]
        self.assertTrue(any(getattr(b, "data", b"") == b"menu_scan" for b in flat))

    def test_viewer_has_no_scan_button(self):
        flat = [b for row in main_menu_buttons("viewer") for b in row]
        self.assertFalse(any(getattr(b, "data", b"") == b"menu_scan" for b in flat))


class HelpTextTests(unittest.TestCase):
    def test_admin_help_lists_owner_commands(self):
        text = help_text("admin")
        self.assertIn("/scan", text)
        self.assertIn("/newkey", text)
        self.assertIn("Владелец", text)

    def test_viewer_help_hides_owner_commands(self):
        text = help_text("viewer")
        self.assertNotIn("/scan", text)
        self.assertIn("только просмотр", text)


class MenuCaptionTests(unittest.TestCase):
    def test_caption_reflects_role(self):
        self.assertIn("владелец", menu_caption("admin"))
        self.assertIn("просмотр", menu_caption("viewer"))


from datetime import datetime, timedelta

from pulse_desk.bot.views import (
    MAX_EXPIRY_DAYS, expiry_from_days, format_expiry, key_expired, parse_expiry_days,
)


class KeyExpiryTests(unittest.TestCase):
    def test_zero_days_means_forever(self):
        self.assertIsNone(expiry_from_days(0))
        self.assertEqual(format_expiry(None), "бессрочно")

    def test_days_land_in_the_future(self):
        stamp = expiry_from_days(7)
        self.assertGreater(datetime.fromisoformat(stamp), datetime.now() + timedelta(days=6))

    def test_days_are_clamped_to_the_maximum(self):
        stamp = datetime.fromisoformat(expiry_from_days(10_000))
        self.assertLess(stamp, datetime.now() + timedelta(days=MAX_EXPIRY_DAYS + 1))

    def test_parse_accepts_bare_and_suffixed_numbers(self):
        self.assertEqual(parse_expiry_days("7"), 7)
        self.assertEqual(parse_expiry_days(" 30 дней "), 30)
        self.assertEqual(parse_expiry_days("0"), 0)

    def test_parse_rejects_junk_and_out_of_range(self):
        for raw in ("", "скоро", "-3", "500", "7.5"):
            self.assertIsNone(parse_expiry_days(raw), raw)

    def test_expired_flag_and_wording(self):
        past = "2020-01-01T00:00:00"
        self.assertTrue(key_expired(past))
        self.assertIn("истёк", format_expiry(past))
        self.assertFalse(key_expired(None))

    def test_future_expiry_shows_time_left(self):
        self.assertIn("через", format_expiry(expiry_from_days(5)))


from pulse_desk.bot.views import (
    ALL_ACCOUNTS, GiveawayFilter, account_label, giveaway_filter_cb, parse_giveaway_filter,
)


class GiveawayFilterTests(unittest.TestCase):
    def test_default_state_round_trips(self):
        cb = giveaway_filter_cb(GiveawayFilter())
        self.assertEqual(cb, b"gw:f:d:0:-1:1")
        self.assertEqual(parse_giveaway_filter(cb.decode().split(":")[2:]), GiveawayFilter())

    def test_full_state_round_trips(self):
        state = GiveawayFilter(sort="p", wins=True, account=3, page=2)
        parsed = parse_giveaway_filter(giveaway_filter_cb(state).decode().split(":")[2:])
        self.assertEqual(parsed, state)

    def test_db_sort_maps_to_board_argument(self):
        self.assertEqual(GiveawayFilter().db_sort, "detected")
        self.assertEqual(GiveawayFilter(sort="p").db_sort, "posted")

    def test_junk_falls_back_to_defaults(self):
        for seg in ([], ["x"], ["d", "nope", "nope", "nope"], ["", "", "", ""]):
            self.assertEqual(parse_giveaway_filter(seg), GiveawayFilter(), seg)

    def test_negative_account_normalises_to_all(self):
        self.assertEqual(parse_giveaway_filter(["d", "0", "-7", "1"]).account, ALL_ACCOUNTS)

    def test_page_never_below_one(self):
        self.assertEqual(parse_giveaway_filter(["d", "0", "-1", "0"]).page, 1)

    def test_changing_a_filter_restarts_paging(self):
        state = GiveawayFilter(sort="d", wins=False, account=1, page=4)
        self.assertEqual(state.with_(wins=True).page, 1)
        self.assertEqual(state.with_(page=5).page, 5)

    def test_account_label_handles_missing_and_stale_index(self):
        self.assertEqual(account_label([], ALL_ACCOUNTS), "Все")
        self.assertEqual(account_label(["muver"], 7), "Все")
        self.assertEqual(account_label(["muver"], 0), "@muver")


class DivTests(unittest.TestCase):
    def test_divider_is_box_drawing_run(self):
        self.assertTrue(set(DIV) == {"━"})
        self.assertGreaterEqual(len(DIV), 10)


if __name__ == "__main__":
    unittest.main()
