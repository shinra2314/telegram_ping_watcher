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
)


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
        self.assertEqual(data["💸 Чеки"], b"mon:feed:check")

    def test_admin_has_owner_controls(self):
        labels = self._labels("admin")
        self.assertIn("🔑 Ключи", labels)
        self.assertIn("⚙️ Настройки", labels)
        self.assertIn("♻️ Рестарт", labels)
        self.assertNotIn("🔔 Мои уведомления", labels)


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


class DivTests(unittest.TestCase):
    def test_divider_is_box_drawing_run(self):
        self.assertTrue(set(DIV) == {"━"})
        self.assertGreaterEqual(len(DIV), 10)


if __name__ == "__main__":
    unittest.main()
