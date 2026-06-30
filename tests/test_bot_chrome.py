from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import unittest

from pulse_desk.bot.chrome import bar, chip, dot, empty, header, kv


class HeaderTests(unittest.TestCase):
    def test_title_uppercased_and_bolded(self):
        out = header("📡", "Мониторинг")
        self.assertTrue(out.startswith("📡 **МОНИТОРИНГ**"))

    def test_breadcrumb_italic_when_given(self):
        out = header("📡", "Чеки", "Домой › Мониторинг › Чеки")
        self.assertIn("__Домой › Мониторинг › Чеки__", out)

    def test_no_breadcrumb_line_when_absent(self):
        out = header("📊", "Сводка")
        self.assertNotIn("__", out)

    def test_ends_with_divider(self):
        self.assertTrue(header("📊", "Сводка").rstrip().endswith("━"))


class KvTests(unittest.TestCase):
    def test_value_monospaced(self):
        self.assertEqual(kv("🆕", "Новых", 12), "🆕 Новых: `12`")


class DotTests(unittest.TestCase):
    def test_bool_true_is_green(self):
        self.assertEqual(dot(True), "🟢")

    def test_bool_false_is_red(self):
        self.assertEqual(dot(False), "🔴")

    def test_known_statuses(self):
        self.assertEqual(dot("online"), "🟢")
        self.assertEqual(dot("offline"), "🔴")
        self.assertEqual(dot("degraded"), "🟡")

    def test_unknown_status_is_amber(self):
        self.assertEqual(dot("connecting"), "🟡")


class EmptyTests(unittest.TestCase):
    def test_empty_wraps_in_box_and_italics(self):
        self.assertEqual(empty("Чеков пока нет."), "📭 __Чеков пока нет.__")


class BarTests(unittest.TestCase):
    def test_full(self):
        self.assertEqual(bar(5, 5), "▰▰▰▰▰")

    def test_partial_rounds(self):
        self.assertEqual(bar(3, 5), "▰▰▰▱▱")

    def test_zero_total_all_empty(self):
        self.assertEqual(bar(0, 0), "▱▱▱▱▱")

    def test_clamps_over(self):
        self.assertEqual(bar(9, 5), "▰▰▰▰▰")

    def test_custom_width(self):
        self.assertEqual(len(bar(1, 4, width=8)), 8)


class ChipTests(unittest.TestCase):
    def test_wraps(self):
        self.assertEqual(chip("розыгрыш"), "「розыгрыш」")


if __name__ == "__main__":
    unittest.main()
