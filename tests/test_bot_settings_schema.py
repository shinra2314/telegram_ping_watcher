"""Описание настроек: границы, разбор ввода и подписи.

Тринадцать числовых полей, написанных ветками вручную, расходятся в мелочах —
где-то принимается запятая, где-то нет, где-то отказ без границ. Таблица
описаний существует ровно чтобы этого не было, и эти тесты пиннят её обещания.
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

from pulse_desk import watch_settings as ws  # noqa: E402
from pulse_desk.bot.settings_schema import (  # noqa: E402
    ALL_FIELDS, RUNTIME_FIELDS, apply_value, field_by_code, fields_of, sanitized_runtime,
)


class ParseTests(unittest.TestCase):
    def field(self, key: str):
        found = field_by_code(f"runtime.{key}")
        self.assertIsNotNone(found, key)
        return found

    def test_int_inside_range(self):
        value, err = self.field("scan_interval_seconds").parse("600")
        self.assertEqual(value, 600)
        self.assertIsNone(err)

    def test_int_below_minimum_is_refused_with_the_bound(self):
        value, err = self.field("scan_interval_seconds").parse("10")
        self.assertIsNone(value)
        self.assertIn("60", err)

    def test_int_above_maximum_is_refused(self):
        value, err = self.field("scan_account_concurrency").parse("99")
        self.assertIsNone(value)
        self.assertIn("Максимум", err)

    def test_garbage_reports_the_range(self):
        value, err = self.field("market_retention_days").parse("скоро")
        self.assertIsNone(value)
        self.assertIn("от 1 до 365", err)

    def test_float_accepts_a_comma(self):
        value, err = self.field("market_alert_change_pct").parse("7,5")
        self.assertEqual(value, 7.5)
        self.assertIsNone(err)

    def test_choice_is_case_insensitive_and_bounded(self):
        field = self.field("giveaway_review_mode")
        self.assertEqual(field.parse("STRICT")[0], "strict")
        self.assertIsNone(field.parse("whatever")[0])

    def test_text_field_passes_through_trimmed(self):
        self.assertEqual(self.field("giveaway_action_account").parse("  muver  ")[0], "muver")


class FormatTests(unittest.TestCase):
    def test_unit_is_printed_with_the_number(self):
        field = field_by_code("runtime.scan_interval_seconds")
        self.assertEqual(field.format(900), "900 сек")

    def test_empty_reads_as_a_dash(self):
        self.assertEqual(field_by_code("runtime.giveaway_action_account").format(""), "—")

    def test_prompt_names_the_field_and_its_bounds(self):
        prompt = field_by_code("runtime.scan_interval_seconds").prompt()
        self.assertIn("Интервал скана", prompt)
        self.assertIn("от 60 до 86400", prompt)


class SchemaShapeTests(unittest.TestCase):
    def test_codes_are_unique(self):
        codes = [f.code for f in ALL_FIELDS]
        self.assertEqual(len(codes), len(set(codes)))

    def test_every_runtime_field_exists_in_the_sanitiser(self):
        # The sanitiser stays the source of truth; a field naming a key it does
        # not know would silently never be saved.
        known = set(ws.sanitize_runtime_settings({}))
        missing = [f.key for f in RUNTIME_FIELDS if f.key not in known]
        self.assertEqual(missing, [])

    def test_the_web_runtime_form_is_covered_field_for_field(self):
        known = set(ws.sanitize_runtime_settings({}))
        covered = {f.key for f in RUNTIME_FIELDS}
        self.assertEqual(known - covered, set(), "runtime setting with no bot field")

    def test_fields_of_filters_by_group(self):
        self.assertTrue(all(f.group == "notifications" for f in fields_of("notifications")))

    def test_apply_value_does_not_mutate_the_original(self):
        original = {"scan_interval_seconds": 900}
        updated = apply_value(original, field_by_code("runtime.scan_interval_seconds"), 600)
        self.assertEqual(original["scan_interval_seconds"], 900)
        self.assertEqual(updated["scan_interval_seconds"], 600)

    def test_sanitiser_clamps_what_the_schema_let_through(self):
        cleaned = sanitized_runtime({"scan_interval_seconds": 10})
        self.assertGreaterEqual(cleaned["scan_interval_seconds"], 60)


if __name__ == "__main__":
    unittest.main()
