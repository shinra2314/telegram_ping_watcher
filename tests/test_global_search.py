"""Global-search pass: mentions proven by Telegram's index, not by local text.

A mini-app result card reaches Telethon as messageMediaUnsupported — empty
text, no entities — so the only evidence that it names a tracked account is
that the server's search returned it. These tests pin that contract: trust the
search verdict when there is nothing to parse, never when there is.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    from pulse_desk import global_search
    from pulse_desk.global_search import (
        has_readable_text,
        is_notifiable,
        mention_query,
        message_ref,
        textless_card_text,
        win_reference_set,
    )

    DEPS_OK = True
except Exception as _exc:  # missing deps or unloadable settings (e.g. CI w/o .env)
    DEPS_OK = False
    _IMPORT_ERROR = _exc


class FakeMessage:
    def __init__(self, chat_id=None, message_id=None, raw_text="", date=None):
        self.chat_id = chat_id
        self.id = message_id
        self.raw_text = raw_text
        self.date = date


class FakeClient:
    """Records the search queries it was asked for and replays canned hits."""

    def __init__(self, results: dict[str, list[FakeMessage]]):
        self.results = results
        self.queries: list[str] = []

    def iter_messages(self, entity, search=None, limit=None):
        self.queries.append(search)
        hits = self.results.get(search, [])

        async def generate():
            for message in hits:
                yield message

        return generate()


@unittest.skipUnless(DEPS_OK, "pulse_desk.global_search not importable")
class QueryHelperTests(unittest.TestCase):
    def test_mention_query_normalises_the_at_sign(self):
        self.assertEqual(mention_query("MCshinra"), "@MCshinra")
        self.assertEqual(mention_query("@MCshinra"), "@MCshinra")
        self.assertEqual(mention_query("  MCshinra "), "@MCshinra")

    def test_message_ref_needs_both_halves(self):
        self.assertEqual(message_ref(FakeMessage(chat_id=-100, message_id=7)), (-100, 7))
        self.assertIsNone(message_ref(FakeMessage(chat_id=None, message_id=7)))
        self.assertIsNone(message_ref(FakeMessage(chat_id=-100, message_id=None)))


@unittest.skipUnless(DEPS_OK, "pulse_desk.global_search not importable")
class ReadableTextTests(unittest.TestCase):
    """Only a message with nothing to parse may lean on the search verdict."""

    def test_undecodable_card_has_no_readable_text(self):
        self.assertFalse(has_readable_text(FakeMessage(raw_text="")))
        self.assertFalse(has_readable_text(FakeMessage(raw_text="   \n ")))

    def test_ordinary_post_has_readable_text(self):
        self.assertTrue(has_readable_text(FakeMessage(raw_text="Победитель @MCshinra")))


@unittest.skipUnless(DEPS_OK, "pulse_desk.global_search not importable")
class PlaceholderTextTests(unittest.TestCase):
    def test_placeholder_names_the_mention_and_link(self):
        text = textless_card_text(["MCshinra"], link="https://t.me/c/1/2")
        self.assertIn("@MCshinra", text)
        self.assertIn("https://t.me/c/1/2", text)

    def test_placeholder_carries_no_win_keyword(self):
        # The classifier runs over this text: a win verdict must come from the
        # search oracle, never from wording we invented ourselves.
        text = textless_card_text(["MCshinra"]).lower()
        for keyword in ("победител", "выигра", "поздравляем", "winner", "congratulation"):
            self.assertNotIn(keyword, text)


@unittest.skipUnless(DEPS_OK, "pulse_desk.global_search not importable")
class NotifyAgeTests(unittest.TestCase):
    """Search answers with its whole backlog, so only fresh hits may ping.

    Without this, the first pass — and every change to the tracked usernames —
    would fire one notification per historical mention.
    """

    NOW = datetime(2026, 9, 4, 19, 0, tzinfo=timezone.utc)

    def test_fresh_hit_notifies(self):
        message = FakeMessage(date=self.NOW - timedelta(hours=2))
        self.assertTrue(is_notifiable(message, now=self.NOW))

    def test_backlog_hit_stays_quiet(self):
        message = FakeMessage(date=self.NOW - timedelta(days=9))
        self.assertFalse(is_notifiable(message, now=self.NOW))

    def test_naive_date_is_read_as_utc(self):
        message = FakeMessage(date=(self.NOW - timedelta(hours=1)).replace(tzinfo=None))
        self.assertTrue(is_notifiable(message, now=self.NOW))

    def test_unknown_age_stays_quiet(self):
        self.assertFalse(is_notifiable(FakeMessage(date=None), now=self.NOW))


@unittest.skipUnless(DEPS_OK, "pulse_desk.global_search not importable")
class WinOracleTests(unittest.TestCase):
    """Win detection for an unreadable card is an intersection of two searches."""

    def test_collects_refs_across_keywords(self):
        client = FakeClient({
            "победитель": [FakeMessage(-100, 5), FakeMessage(-200, 9)],
            "выиграл": [FakeMessage(-100, 5)],
        })
        refs = asyncio.run(win_reference_set(client, ["победитель", "выиграл"]))
        self.assertEqual(refs, {(-100, 5), (-200, 9)})
        self.assertEqual(client.queries, ["победитель", "выиграл"])

    def test_blank_keywords_are_not_queried(self):
        client = FakeClient({"победитель": []})
        asyncio.run(win_reference_set(client, ["победитель", "  ", ""]))
        self.assertEqual(client.queries, ["победитель"])

    def test_query_count_is_capped(self):
        keywords = [f"kw{index}" for index in range(global_search.WIN_ORACLE_MAX_QUERIES + 5)]
        client = FakeClient({})
        asyncio.run(win_reference_set(client, keywords))
        self.assertEqual(len(client.queries), global_search.WIN_ORACLE_MAX_QUERIES)


if __name__ == "__main__":
    unittest.main()
