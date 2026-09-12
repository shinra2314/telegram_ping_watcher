"""Чистые куски ленты: пресеты, выгрузка, разбор тегов, текст карточки.

Пресеты лежат в том же ключе настроек, что писал веб (`saved_filters`), поэтому
преобразование «выборка ↔ сохранённый запрос» обязано ходить в обе стороны без
потерь — иначе сохранённое в вебе откроется в боте другой лентой.
"""
from __future__ import annotations

import csv
import io
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
for path in (str(ROOT), str(SRC_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from pulse_desk.bot.sections import feed  # noqa: E402
from pulse_desk.bot.views import FeedFilter  # noqa: E402


class PresetTests(unittest.TestCase):
    def test_roundtrip_keeps_the_selection(self):
        for state in (
            FeedFilter(),
            FeedFilter(type="w", status="n", favorite=True, sort="p", ascending=True),
            FeedFilter(type="g", status="s", sort="c"),
        ):
            restored = feed._preset_to_filter(feed._preset_query(state))
            self.assertEqual(restored, state.with_(page=1), state)

    def test_saved_query_uses_the_web_vocabulary(self):
        query = feed._preset_query(FeedFilter(type="w", status="n", sort="p", ascending=True))
        self.assertEqual(query["type"], "win")
        self.assertEqual(query["status"], "new")
        self.assertEqual(query["sort_by"], "priority_score")
        self.assertEqual(query["sort_order"], "ASC")

    def test_a_preset_saved_by_the_web_still_opens(self):
        # Exactly the shape routers/lookups.py stored.
        legacy = {"type": "giveaway", "status": "", "favorite": True,
                  "sort_by": "detected_at", "sort_order": "DESC"}
        state = feed._preset_to_filter(legacy)
        self.assertEqual(state.type, "g")
        self.assertEqual(state.status, "a")
        self.assertTrue(state.favorite)
        self.assertFalse(state.ascending)

    def test_garbage_preset_opens_the_default_feed(self):
        self.assertEqual(feed._preset_to_filter({}), FeedFilter())
        self.assertEqual(feed._preset_to_filter({"type": "nope", "sort_by": "nope"}), FeedFilter())


class ExportTests(unittest.TestCase):
    ROWS = [
        {"id": 1, "chat": "@a", "text": "привет, победа", "is_win": 1, "link": "https://t.me/a/1"},
        {"id": 2, "chat": "@b", "text": 'кавычки "внутри", запятая', "is_win": 0},
    ]

    def test_csv_starts_with_a_bom_for_excel(self):
        payload, name = feed._export_bytes(self.ROWS, "c")
        self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(name, "pings.csv")

    def test_csv_is_parseable_and_keeps_every_row(self):
        payload, _name = feed._export_bytes(self.ROWS, "c")
        text = payload.decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual([r["id"] for r in rows], ["1", "2"])
        self.assertIn('кавычки "внутри", запятая', [r["text"] for r in rows])

    def test_json_is_valid_and_cyrillic_stays_readable(self):
        payload, name = feed._export_bytes(self.ROWS, "j")
        self.assertEqual(name, "pings.json")
        data = json.loads(payload.decode("utf-8"))
        self.assertEqual(len(data), 2)
        self.assertIn("победа", payload.decode("utf-8"))

    def test_only_the_declared_columns_are_exported(self):
        payload, _name = feed._export_bytes([{**self.ROWS[0], "secret": "x"}], "j")
        self.assertNotIn("secret", payload.decode("utf-8"))


class TagParsingTests(unittest.TestCase):
    def test_list_column(self):
        self.assertEqual(feed._tags_of({"tags": ["гив", "долг"]}), ["гив", "долг"])

    def test_json_string_column(self):
        self.assertEqual(feed._tags_of({"tags": '["гив"]'}), ["гив"])

    def test_comma_separated_legacy_value(self):
        self.assertEqual(feed._tags_of({"tags": "гив, долг"}), ["гив", "долг"])

    def test_empty_and_broken_values_are_not_fatal(self):
        for value in (None, "", "   ", "{", 5):
            self.assertEqual(feed._tags_of({"tags": value}), [], value)


class CardTextTests(unittest.TestCase):
    def test_status_is_always_shown(self):
        text = feed.card_text({"id": 1, "status": "important", "text": "x"})
        self.assertIn("Важное", text)

    def test_giveaway_outcome_only_for_a_giveaway(self):
        plain = feed.card_text({"id": 1, "status": "new", "text": "x"})
        gw = feed.card_text({"id": 1, "status": "new", "is_giveaway": 1,
                             "giveaway_status": "claimed", "text": "x"})
        self.assertNotIn("Розыгрыш:", plain)
        self.assertIn("забрал", gw)

    def test_note_and_tags_appear_when_set(self):
        text = feed.card_text({"id": 1, "status": "new", "text": "x",
                               "note": "написать вечером", "tags": ["долг"]})
        self.assertIn("написать вечером", text)
        self.assertIn("долг", text)

    def test_a_very_long_note_is_trimmed(self):
        text = feed.card_text({"id": 1, "status": "new", "text": "x", "note": "я" * 5000})
        self.assertLess(len(text), 4096)


if __name__ == "__main__":
    unittest.main()
