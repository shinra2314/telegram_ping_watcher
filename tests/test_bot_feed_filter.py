"""Состояние ленты, уложенное в кнопку.

Telegram даёт 64 байта на callback-данные — это и есть вся память экрана между
нажатиями. Тесты держат три обещания: выборка туда влезает, разбор кнопки
возвращает ровно то, что закодировали, и любой мусор (кнопка от прошлой версии,
подобранная руками строка) деградирует в дефолтную ленту, а не в исключение.
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

from pulse_desk.bot.views import (  # noqa: E402
    FEED_SORTS, FEED_STATUSES, FEED_TYPES, FeedFilter, describe_feed_filter,
    feed_filter_from_legacy, parse_feed_filter,
)

CALLBACK_LIMIT = 64


def roundtrip(state: FeedFilter) -> FeedFilter:
    return parse_feed_filter(state.cb().decode().split(":")[2:])


class EncodingTests(unittest.TestCase):
    def test_default_encodes_and_fits(self):
        data = FeedFilter().cb()
        self.assertEqual(data, b"mon:f:a:a:0:d:0:0:1")
        self.assertLessEqual(len(data), CALLBACK_LIMIT)

    def test_every_combination_fits_the_callback_limit(self):
        for type_code, _db, _label in FEED_TYPES:
            for status_code, _sdb, _slabel in FEED_STATUSES:
                for sort_code, _odb, _olabel in FEED_SORTS:
                    state = FeedFilter(type=type_code, status=status_code, sort=sort_code,
                                       favorite=True, ascending=True, query=True, page=9999)
                    self.assertLessEqual(len(state.cb()), CALLBACK_LIMIT, state)

    def test_roundtrip_preserves_every_field(self):
        state = FeedFilter(type="w", status="n", favorite=True, sort="p",
                           ascending=True, query=True, page=4)
        self.assertEqual(roundtrip(state), state)

    def test_page_override_does_not_touch_the_rest(self):
        state = FeedFilter(type="g", favorite=True, page=1)
        self.assertEqual(parse_feed_filter(state.cb(3).decode().split(":")[2:]).page, 3)
        self.assertTrue(parse_feed_filter(state.cb(3).decode().split(":")[2:]).favorite)


class DecodingTests(unittest.TestCase):
    def test_garbage_falls_back_to_the_default_feed(self):
        self.assertEqual(parse_feed_filter(["zz", "??", "x", "nope", "-", "-", "abc"]),
                         FeedFilter())

    def test_short_callback_is_not_fatal(self):
        self.assertEqual(parse_feed_filter([]), FeedFilter())
        self.assertEqual(parse_feed_filter(["w"]), FeedFilter(type="w"))

    def test_page_is_never_below_one(self):
        self.assertEqual(parse_feed_filter(["a", "a", "0", "d", "0", "0", "-5"]).page, 1)

    def test_legacy_button_maps_onto_the_new_filter(self):
        self.assertEqual(feed_filter_from_legacy("win", 2), FeedFilter(type="w", page=2))
        self.assertEqual(feed_filter_from_legacy("all"), FeedFilter())
        # an unknown legacy code must not blow up an old chat button
        self.assertEqual(feed_filter_from_legacy("unknown"), FeedFilter())


class TransitionTests(unittest.TestCase):
    def test_any_change_but_paging_returns_to_page_one(self):
        deep = FeedFilter(page=7)
        self.assertEqual(deep.with_(type="w").page, 1)
        self.assertEqual(deep.with_(page=8).page, 8)

    def test_sort_cycles_through_every_option_and_returns(self):
        state = FeedFilter()
        seen = [state.sort]
        for _ in range(len(FEED_SORTS) - 1):
            state = state.next_sort()
            seen.append(state.sort)
        self.assertEqual(len(set(seen)), len(FEED_SORTS))
        self.assertEqual(state.next_sort().sort, FeedFilter().sort)

    def test_status_cycles(self):
        state = FeedFilter()
        for _ in range(len(FEED_STATUSES)):
            state = state.next_status()
        self.assertEqual(state.status, FeedFilter().status)

    def test_is_default_ignores_the_page(self):
        self.assertTrue(FeedFilter(page=5).is_default)
        self.assertFalse(FeedFilter(favorite=True).is_default)


class TranslationTests(unittest.TestCase):
    def test_db_values_are_what_get_pings_expects(self):
        state = FeedFilter(type="w", status="n", sort="p", ascending=True)
        self.assertEqual(state.db_type, "win")
        self.assertEqual(state.db_status, "new")
        self.assertEqual(state.db_sort, "priority_score")
        self.assertEqual(state.db_order, "ASC")

    def test_any_status_means_no_filter(self):
        self.assertIsNone(FeedFilter().db_status)

    def test_description_lists_only_what_is_set(self):
        self.assertEqual(describe_feed_filter(FeedFilter()), "обнаружено ↓")
        text = describe_feed_filter(FeedFilter(type="w", favorite=True, ascending=True))
        self.assertIn("победы", text)
        self.assertIn("избранное", text)
        self.assertIn("↑", text)

    def test_description_shows_the_search_term_only_when_applied(self):
        self.assertNotIn("«", describe_feed_filter(FeedFilter(), "ton"))
        self.assertIn("«ton»", describe_feed_filter(FeedFilter(query=True), "ton"))


if __name__ == "__main__":
    unittest.main()
