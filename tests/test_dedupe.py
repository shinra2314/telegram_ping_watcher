"""Copies of one winners post become one debt."""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import database  # noqa: E402
from pulse_desk import dedupe  # noqa: E402

TEXT = "🎁 РОЗЫГРЫШ | Нож Боуи | Автотроника  Условие: подписаться. Победители: @w3v8f0rm"


def row(id_, chat_type="private", when="2026-05-10T21:10:32", text=TEXT, mentions=("w3v8f0rm",), chat_id=None):
    # Every row sits in a chat of its own unless the test says otherwise.
    return {"id": id_, "chat_id": id_ if chat_id is None else chat_id, "chat_type": chat_type,
            "detected_at": when, "text": text, "mentions": list(mentions)}


class RuleTests(unittest.TestCase):
    def test_channel_post_is_primary_even_if_later(self):
        rows = [row(1, "private", "2026-05-10T21:00:00"), row(2, "channel", "2026-05-10T21:10:00"),
                row(3, "private", "2026-05-11T09:00:00")]
        self.assertEqual(dedupe.group_duplicates(rows), [(2, [1, 3])])

    def test_whitespace_and_case_do_not_matter(self):
        self.assertTrue(dedupe.same_win(row(1), row(2, text=TEXT.upper().replace(" ", "  "))))

    def test_different_account_or_far_apart_is_not_a_copy(self):
        self.assertFalse(dedupe.same_win(row(1), row(2, mentions=("other",))))
        self.assertFalse(dedupe.same_win(row(1), row(2, when="2026-05-20T21:10:32")))

    def test_short_generic_text_is_never_glued(self):
        self.assertFalse(dedupe.same_win(row(1, text="Победитель: @a"), row(2, text="Победитель: @a", mentions=("a",))))

    def test_find_primary(self):
        self.assertEqual(dedupe.find_primary(row(9), [row(1, "channel"), row(9)])["id"], 1)
        self.assertIsNone(dedupe.find_primary(row(9), [row(9)]))

    def test_same_text_again_in_one_chat_is_a_new_win(self):
        # A channel reuses one template for every fast giveaway (FREE CS2 posts
        # 490 and 547, Univer 8 and 9), and the same account can win twice.
        self.assertFalse(dedupe.same_win(row(1, "channel", chat_id=7), row(2, "channel", chat_id=7)))
        self.assertIsNone(dedupe.find_primary(row(9, "channel", chat_id=7), [row(1, "channel", chat_id=7)]))

    def test_a_copy_elsewhere_does_not_glue_two_posts_of_one_chat(self):
        # The private copy matches both posts; it belongs to the first, and the
        # second post stays a win of its own.
        rows = [row(1, "channel", "2026-05-10T21:00:00", chat_id=7), row(3, "private", "2026-05-10T22:00:00"),
                row(2, "channel", "2026-05-10T23:00:00", chat_id=7)]
        self.assertEqual(dedupe.group_duplicates(rows), [(1, [3])])

    def test_two_pastes_of_one_post_in_one_chat_are_both_copies(self):
        # PIAR CHAT MEXANICK pasted the artexxx winners post twice, 32 min apart.
        rows = [row(1, "channel", "2026-09-18T22:03:01", chat_id=7), row(2, "group", "2026-09-18T22:07:32", chat_id=8),
                row(3, "group", "2026-09-18T22:39:29", chat_id=8)]
        self.assertEqual(dedupe.group_duplicates(rows), [(1, [2, 3])])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pulse_dedupe_")
        self._old = database.DB_PATH
        database.DB_PATH = Path(self._tmp.name) / "dedupe.db"
        asyncio.run(database.init_db())

    def tearDown(self):
        database.DB_PATH = self._old
        self._tmp.cleanup()

    def _save(self, chat_id, message_id, chat_type):
        return asyncio.run(database.save_ping({
            "chat": f"chat{chat_id}", "chat_id": chat_id, "message_id": message_id, "mentions": ["w3v8f0rm"],
            "text": TEXT, "chat_type": chat_type, "is_win": True, "action_status": "claim_prize",
        }))

    def test_new_copy_links_and_board_lists_one(self):
        from pulse_desk.ping_actions import apply_ping_meta
        from pulse_desk.ping_pipeline import link_win_copies

        first = self._save(555, 1, "private")
        self.assertIsNone(asyncio.run(link_win_copies(first)))
        channel = self._save(-100777, 2, "channel")
        # The channel post arrives later but becomes the primary.
        self.assertEqual(asyncio.run(link_win_copies(channel)), channel)
        board = asyncio.run(database.get_debt_board(["w3v8f0rm"]))
        self.assertEqual([r["id"] for r in board["rows"]], [channel])
        self.assertEqual([c["id"] for c in asyncio.run(database.get_duplicates(channel))], [first])

        asyncio.run(apply_ping_meta(channel, giveaway_status="claimed", action_status="claimed"))
        copy = asyncio.run(database.get_ping_by_id(first))
        self.assertEqual(copy["action_status"], "claimed")

    def test_two_posts_of_one_chat_are_two_debts(self):
        from pulse_desk.ping_pipeline import dedupe_existing_wins, link_win_copies

        first = self._save(-100777, 490, "channel")
        second = self._save(-100777, 547, "channel")
        self.assertIsNone(asyncio.run(link_win_copies(second)))
        self.assertEqual(asyncio.run(dedupe_existing_wins()), 0)
        board = asyncio.run(database.get_debt_board(["w3v8f0rm"]))
        self.assertEqual(sorted(r["id"] for r in board["rows"]), [first, second])

    def test_startup_releases_posts_glued_in_their_own_chat(self):
        # The old rule glued Univer posts 8 and 9 and hid a live debt.
        first = self._save(-100777, 8, "channel")
        second = self._save(-100777, 9, "channel")
        copy = self._save(555, 1, "private")
        asyncio.run(database.mark_duplicates(first, [second, copy]))
        self.assertEqual(asyncio.run(database.unglue_same_chat_copies()), 1)
        self.assertIsNone(asyncio.run(database.get_ping_by_id(second))["duplicate_of"])
        self.assertEqual(asyncio.run(database.get_ping_by_id(copy))["duplicate_of"], first)
        board = asyncio.run(database.get_debt_board(["w3v8f0rm"]))
        self.assertEqual(sorted(r["id"] for r in board["rows"]), [first, second])
        # Every start runs it; the second pass finds nothing.
        self.assertEqual(asyncio.run(database.unglue_same_chat_copies()), 0)

    def test_backfill_carries_a_claim_made_on_a_copy(self):
        from pulse_desk.ping_pipeline import dedupe_existing_wins

        copy = self._save(555, 1, "private")
        channel = self._save(-100777, 2, "channel")
        asyncio.run(database.update_ping_meta(copy, giveaway_status="claimed", action_status="claimed"))
        self.assertEqual(asyncio.run(dedupe_existing_wins()), 1)
        primary = asyncio.run(database.get_ping_by_id(channel))
        self.assertEqual(primary["action_status"], "claimed")
        self.assertEqual(asyncio.run(database.get_ping_by_id(copy))["duplicate_of"], channel)


if __name__ == "__main__":
    unittest.main()
