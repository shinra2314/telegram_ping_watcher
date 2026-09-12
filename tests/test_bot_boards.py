"""Доски: сегменты долгов, «горячий», суммы и пункты заметки.

Сегменты в вебе считались в браузере, и граница между «критично» и «высокий»
жила в JS. Здесь она одна и проверяется: строка со score 90 — критичная, со
score 89 — высокая, и ни одна не может попасть в оба сегмента сразу.
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

from pulse_desk.bot.sections import debts, obsidian  # noqa: E402


def row(id_: int, score: int = 0, status: str = "read", value=None, when: str = "") -> dict:
    return {"id": id_, "priority_score": score, "status": status,
            "estimated_value": value, "detected_at": when, "chat": f"@c{id_}"}


class SegmentTests(unittest.TestCase):
    ROWS = [row(1, 95, "new"), row(2, 90), row(3, 89), row(4, 70), row(5, 10, "new")]

    def test_critical_starts_at_ninety(self):
        ids = [r["id"] for r in debts.segment_rows(self.ROWS, "c")]
        self.assertEqual(ids, [1, 2])

    def test_high_is_seventy_up_to_but_not_including_critical(self):
        ids = [r["id"] for r in debts.segment_rows(self.ROWS, "h")]
        self.assertEqual(ids, [3, 4])

    def test_segments_do_not_overlap(self):
        critical = {r["id"] for r in debts.segment_rows(self.ROWS, "c")}
        high = {r["id"] for r in debts.segment_rows(self.ROWS, "h")}
        self.assertEqual(critical & high, set())

    def test_fresh_is_the_unread_ones(self):
        self.assertEqual([r["id"] for r in debts.segment_rows(self.ROWS, "n")], [1, 5])

    def test_unknown_segment_shows_everything(self):
        # Кнопка из прошлой версии не должна открывать пустой экран.
        self.assertEqual(len(debts.segment_rows(self.ROWS, "zz")), len(self.ROWS))

    def test_empty_board_is_not_an_error(self):
        self.assertEqual(debts.segment_rows([], "c"), [])


class HottestTests(unittest.TestCase):
    def test_highest_score_wins(self):
        self.assertEqual(debts.hottest([row(1, 10), row(2, 90)])["id"], 2)

    def test_ties_break_on_freshness(self):
        rows = [row(1, 90, when="2026-09-01T10:00"), row(2, 90, when="2026-09-05T10:00")]
        self.assertEqual(debts.hottest(rows)["id"], 2)

    def test_nothing_to_pick_from(self):
        self.assertIsNone(debts.hottest([]))


class ValueTests(unittest.TestCase):
    def test_sums_what_it_can(self):
        self.assertEqual(debts.total_value([row(1, value=100), row(2, value="50.5")]), 150.5)

    def test_missing_and_broken_values_are_skipped_not_fatal(self):
        self.assertEqual(debts.total_value([row(1), row(2, value="—"), row(3, value=5)]), 5)


class MarkTests(unittest.TestCase):
    def test_marks_are_per_sender(self):
        debts.state.bot_debt_marks.clear()
        debts.marks(1).add(10)
        self.assertEqual(debts.marks(2), set())
        self.assertIn(10, debts.marks(1))
        debts.state.bot_debt_marks.clear()

    def test_row_label_shows_the_mark(self):
        self.assertTrue(debts.row_label(row(1, 95), True).startswith("☑️"))
        self.assertTrue(debts.row_label(row(1, 95), False).startswith("🔴"))


class BoardKeyboardTests(unittest.TestCase):
    def test_bulk_button_appears_only_with_marks(self):
        plain = [b.data for r in debts.board_keyboard([row(1)], "a", 1, False, set()) for b in r]
        marked = [b.data for r in debts.board_keyboard([row(1)], "a", 1, False, {1}) for b in r]
        self.assertFalse(any(d.startswith(b"db:go") for d in plain))
        self.assertTrue(any(d.startswith(b"db:go") for d in marked))

    def test_every_callback_fits(self):
        rows = debts.board_keyboard([row(999999)], "c", 99, True, {999999})
        for data in [b.data for r in rows for b in r]:
            self.assertLessEqual(len(data), 64, data)

    def test_card_offers_a_link_only_when_there_is_one(self):
        with_link = debts.card_keyboard({"id": 1, "link": "https://t.me/a/1"}, "a", 1, False)
        without = debts.card_keyboard({"id": 1, "link": "нет ссылки"}, "a", 1, False)
        self.assertTrue(any(getattr(b, "url", None) for r in with_link for b in r))
        self.assertFalse(any(getattr(b, "url", None) for r in without for b in r))


class ObsidianTests(unittest.TestCase):
    SNAPSHOT = {
        "enabled": True,
        "overall": {"done": 3, "total": 10, "pct": 30},
        "groups": [
            {"username": "@a", "done": 1, "total": 2,
             "items": [{"checked": True, "title": "x", "link_norm": "t.me/a/1"},
                       {"checked": False, "title": "y", "link_norm": "t.me/a/2"}]},
            {"username": "@b", "done": 0, "total": 1,
             "items": [{"checked": False, "title": "z", "link_norm": "t.me/b/1"}]},
        ],
    }

    def test_items_are_flattened_in_display_order(self):
        items = obsidian.flat_items(self.SNAPSHOT)
        self.assertEqual([i["title"] for i in items], ["x", "y", "z"])
        self.assertEqual(items[2]["username"], "@b")

    def test_index_in_the_callback_matches_that_order(self):
        rows = obsidian.keyboard(self.SNAPSHOT, 1, True)
        self.assertTrue(rows[0][0].data.startswith(b"ob:t:0:"))
        self.assertTrue(rows[2][0].data.startswith(b"ob:t:2:"))

    def test_progress_is_drawn_and_counted(self):
        text = obsidian.progress_text(self.SNAPSHOT)
        self.assertIn("3/10", text)

    def test_a_disabled_note_explains_itself(self):
        text = obsidian.card({"enabled": False, "reason": "no_path"}, 1)
        self.assertIn("путь", text)

    def test_owner_only_actions_are_hidden_from_a_guest(self):
        guest = [b.data for r in obsidian.keyboard(self.SNAPSHOT, 1, False) for b in r]
        owner = [b.data for r in obsidian.keyboard(self.SNAPSHOT, 1, True) for b in r]
        self.assertFalse(any(d.startswith(b"ob:sync") for d in guest))
        self.assertTrue(any(d.startswith(b"ob:sync") for d in owner))


if __name__ == "__main__":
    unittest.main()
