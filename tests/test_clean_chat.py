"""Clean chat: prompts and answers are deleted, wrong input is retried, stale state swept."""
from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk import autoclean  # noqa: E402
from pulse_desk.app_ctx import state  # noqa: E402
from pulse_desk.bot import pending  # noqa: E402


class FakeClient:
    def __init__(self):
        self.deleted: list[tuple[int, list[int]]] = []

    async def delete_messages(self, chat, ids):
        self.deleted.append((chat, list(ids)))


class FakeEvent:
    """Just enough of a Telethon event: respond() hands out increasing ids."""

    _next = 100

    def __init__(self, sender=7, chat=7, data=None, message_id=None, text=""):
        self.sender_id = sender
        self.chat_id = chat
        self.data = data
        self.message_id = message_id
        self.message = SimpleNamespace(text=text, id=self._take())
        self.responses: list[str] = []

    @classmethod
    def _take(cls):
        cls._next += 1
        return cls._next

    async def respond(self, text, **_kw):
        self.responses.append(text)
        return SimpleNamespace(id=self._take())

    async def answer(self, *_a, **_kw):
        return None


class PendingTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self._old_client = state.bot_client
        state.bot_client = self.client
        state.bot_pending_inputs.clear()
        self.seen: list[str] = []

        async def ok(event, entry, raw):
            self.seen.append(raw)
            await event.respond("✅ done")

        async def picky(event, entry, raw):
            if raw != "10:30":
                raise pending.InputRejected("❌ Формат: `09:00`.")
            await event.respond("✅ saved")

        pending.register_prompt("t_ok", "Type", ok)
        pending.register_prompt("t_picky", "Time", picky)
        pending.register_prompt("t_keep", "Note", ok, keep_screen=True)

    def tearDown(self):
        state.bot_client = self._old_client
        state.bot_pending_inputs.clear()

    def _arm(self, kind):
        click = FakeEvent(data=b"x", message_id=55)
        asyncio.run(pending.prompt_pending(click, kind))
        return state.bot_pending_inputs[7]

    def _all_deleted(self):
        return sorted(mid for _chat, ids in self.client.deleted for mid in ids)

    def test_success_deletes_prompt_answer_and_old_screen(self):
        entry = self._arm("t_ok")
        prompt_id = entry["cleanup"][0]
        answer = FakeEvent(text="hello")
        asyncio.run(pending.consume(answer, "admin", pending.take_pending(7)))
        self.assertEqual(self.seen, ["hello"])
        self.assertEqual(self._all_deleted(), sorted([prompt_id, answer.message.id, 55]))

    def test_keep_screen_kind_leaves_the_card(self):
        entry = self._arm("t_keep")
        answer = FakeEvent(text="note")
        asyncio.run(pending.consume(answer, "admin", pending.take_pending(7)))
        self.assertNotIn(55, self._all_deleted())
        self.assertIn(entry["cleanup"][0], self._all_deleted())

    def test_wrong_input_keeps_the_prompt_armed(self):
        entry = self._arm("t_picky")
        prompt_id = entry["cleanup"][0]
        wrong = FakeEvent(text="10.30")
        asyncio.run(pending.consume(wrong, "admin", pending.take_pending(7)))
        rearmed = state.bot_pending_inputs[7]
        self.assertEqual(rearmed["attempts"], 1)
        self.assertEqual(rearmed["cleanup"][0], prompt_id)
        self.assertIn("попытка 2 из 3", wrong.responses[-1])
        # The wrong answer is gone, the prompt is not.
        self.assertIn(wrong.message.id, self._all_deleted())
        self.assertNotIn(prompt_id, self._all_deleted())
        right = FakeEvent(text="10:30")
        asyncio.run(pending.consume(right, "admin", pending.take_pending(7)))
        self.assertEqual(right.responses, ["✅ saved"])
        self.assertIn(prompt_id, self._all_deleted())
        self.assertNotIn(7, state.bot_pending_inputs)

    def test_three_wrong_answers_give_up(self):
        self._arm("t_picky")
        for _ in range(3):
            entry = pending.take_pending(7)
            self.assertIsNotNone(entry)
            event = FakeEvent(text="nope")
            asyncio.run(pending.consume(event, "admin", entry))
        self.assertNotIn(7, state.bot_pending_inputs)
        self.assertIn("Ввод отменён", event.responses[-1])

    def test_cancel_removes_prompt_but_not_screen(self):
        entry = self._arm("t_ok")
        cancel = FakeEvent(data=b"st_x", message_id=entry["cleanup"][0])
        asyncio.run(pending.cancel_pending(cancel))
        self.assertNotIn(7, state.bot_pending_inputs)
        self.assertEqual(self._all_deleted(), [entry["cleanup"][0]])

    def test_new_prompt_replaces_previous_one(self):
        first = self._arm("t_ok")
        self._arm("t_picky")
        self.assertIn(first["cleanup"][0], self._all_deleted())

    def test_sweep_returns_expired_entries(self):
        entry = self._arm("t_ok")
        entry["armed_at"] = datetime.now() - timedelta(minutes=10)
        dropped = pending.sweep_pending()
        self.assertEqual(len(dropped), 1)
        self.assertNotIn(7, state.bot_pending_inputs)

    def test_admin_only_kind_is_silent_for_members(self):
        self._arm("t_ok")
        event = FakeEvent(text="x")
        asyncio.run(pending.consume(event, "viewer", pending.take_pending(7)))
        self.assertEqual(self.seen, [])


class AutocleanRulesTests(unittest.TestCase):
    def test_cycle(self):
        self.assertEqual([autoclean.next_choice(h) for h in (0, 6, 24, 47)], [6, 24, 47, 0])
        self.assertEqual(autoclean.next_choice("junk"), 6)

    def test_never_past_telegram_window(self):
        now = datetime(2026, 9, 13, 12, 0)
        self.assertEqual(autoclean.delete_at(now, 47) - now, timedelta(hours=47))
        self.assertEqual(autoclean.clean_hours(72), 0)

    def test_only_minor_kinds(self):
        self.assertTrue(autoclean.should_schedule("mention", 6))
        self.assertTrue(autoclean.should_schedule("market", 24))
        self.assertFalse(autoclean.should_schedule("win", 24))
        self.assertFalse(autoclean.should_schedule("giveaway", 24))
        self.assertFalse(autoclean.should_schedule("mention", 0))

    def test_member_prefs_round_trip(self):
        from pulse_desk.bot_prefs import parse_member_prefs, toggle_member_pref

        self.assertEqual(parse_member_prefs('{"autoclean_hours": 24}')["autoclean_hours"], 24)
        self.assertEqual(parse_member_prefs('{"autoclean_hours": 5}')["autoclean_hours"], 0)
        prefs = parse_member_prefs('{"autoclean_hours": 24}')
        self.assertEqual(toggle_member_pref(prefs, "autoclean_hours")["autoclean_hours"], 24)


class MemberStatsCardTests(unittest.TestCase):
    def test_shares_and_accounts(self):
        from pulse_desk.bot.cards import member_stats_card

        text = member_stats_card({"joined": 3, "skipped": 1}, {"joined": 1, "skipped": 0},
                                 {"wins": 4, "claimed": 3}, ["alice"])
        self.assertIn("75%", text)
        self.assertIn("@alice", text)
        self.assertIn("3 · 75%", text)

    def test_empty_member_gets_a_hint(self):
        from pulse_desk.bot.cards import member_stats_card

        text = member_stats_card({"joined": 0, "skipped": 0}, {"joined": 0, "skipped": 0})
        self.assertIn("Участвую", text)
        self.assertNotIn("Победы ваших аккаунтов", text)


if __name__ == "__main__":
    unittest.main()


class JanitorTests(unittest.TestCase):
    """One janitor pass against a scratch database."""

    def setUp(self):
        import tempfile

        import database

        self._tmp = tempfile.TemporaryDirectory(prefix="pulse_janitor_")
        self._old_path = database.DB_PATH
        database.DB_PATH = Path(self._tmp.name) / "janitor.db"
        asyncio.run(database.init_db())
        self.client = FakeClient()
        self._old_client = state.bot_client
        state.bot_client = self.client

    def tearDown(self):
        import database

        state.bot_client = self._old_client
        state.bot_feed_queries.clear()
        state.bot_debt_marks.clear()
        state.bot_debt_marks_touched.clear()
        database.DB_PATH = self._old_path
        self._tmp.cleanup()

    def test_due_messages_deleted_and_stale_state_forgotten(self):
        import database
        from pulse_desk.loops import run_janitor_once

        now = datetime.now()
        asyncio.run(database.schedule_message_deletion([(1, 10), (1, 11)], now - timedelta(minutes=1), "mention"))
        asyncio.run(database.schedule_message_deletion([(2, 20)], now + timedelta(hours=5), "mention"))
        state.bot_feed_queries[5] = ("ton", now - timedelta(hours=2))
        state.bot_feed_queries[6] = ("btc", now)
        state.bot_debt_marks[5] = {1, 2}
        state.bot_debt_marks_touched[5] = now - timedelta(hours=2)

        stats = asyncio.run(run_janitor_once(now))

        self.assertEqual(self.client.deleted, [(1, [10, 11])])
        self.assertEqual(stats["deleted"], 2)
        self.assertEqual(len(asyncio.run(database.get_due_ephemeral_messages(now + timedelta(hours=6)))), 1)
        self.assertNotIn(5, state.bot_feed_queries)
        self.assertIn(6, state.bot_feed_queries)
        self.assertNotIn(5, state.bot_debt_marks)


class LogViewTests(unittest.TestCase):
    def test_problem_lines_keep_tracebacks(self):
        from pulse_desk.bot.sections.system import problem_lines

        lines = [
            "2026-09-13 10:00:00,000 [INFO] fine",
            "2026-09-13 10:00:01,000 [ERROR] boom",
            "Traceback (most recent call last):",
            "RuntimeError: boom",
            "2026-09-13 10:00:02,000 [INFO] fine again",
            "2026-09-13 10:00:03,000 [WARNING] careful",
        ]
        self.assertEqual(problem_lines(lines), lines[1:4] + lines[5:])

    def test_tail_bytes_starts_on_a_line(self):
        import tempfile

        from pulse_desk.bot.sections.system import tail_bytes

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.log"
            path.write_bytes(b"first line\nsecond line\nthird\n")
            self.assertEqual(tail_bytes(path, 10), b"third\n")
            self.assertEqual(tail_bytes(path, 1000), b"first line\nsecond line\nthird\n")


class UndoTests(unittest.TestCase):
    """«↩️ Отменить» restores the snapshot and refuses once 30 s passed."""

    def setUp(self):
        import tempfile

        import database

        self._tmp = tempfile.TemporaryDirectory(prefix="pulse_undo_")
        self._old_path = database.DB_PATH
        database.DB_PATH = Path(self._tmp.name) / "undo.db"
        asyncio.run(database.init_db())
        state.bot_undo.clear()

    def tearDown(self):
        import database

        state.bot_undo.clear()
        database.DB_PATH = self._old_path
        self._tmp.cleanup()

    def _ping(self):
        import database

        async def _insert():
            async with database._connect() as db:
                cur = await db.execute(
                    "INSERT INTO pings (chat, text, detected_at, is_win, giveaway_status, action_status) "
                    "VALUES ('c', 't', ?, 1, '', 'claim_prize')", (datetime.now().isoformat(),))
                await db.commit()
                return cur.lastrowid
        return asyncio.run(_insert())

    def test_claim_then_undo(self):
        import database
        from pulse_desk.bot import undo as undo_mod
        from pulse_desk.ping_actions import apply_ping_meta

        ping_id = self._ping()
        before = asyncio.run(undo_mod.snapshot([ping_id]))
        token = undo_mod.remember(1, before, "забрал", "db:s:a:1")
        asyncio.run(apply_ping_meta(ping_id, giveaway_status="claimed", action_status="claimed"))
        restored, back = asyncio.run(undo_mod.undo(1, token))
        self.assertEqual((restored, back), (1, "db:s:a:1"))
        row = asyncio.run(database.get_ping_by_id(ping_id))
        self.assertEqual(row["action_status"], "claim_prize")
        self.assertEqual(row["giveaway_status"], "")
        # Used once.
        self.assertIsNone(asyncio.run(undo_mod.undo(1, token)))

    def test_expired_and_wrong_token(self):
        from pulse_desk.bot import undo as undo_mod

        token = undo_mod.remember(1, {5: {"status": "new"}}, "x", now=datetime.now() - timedelta(seconds=31))
        self.assertIsNone(undo_mod.pending_undo(1, token))
        token = undo_mod.remember(1, {5: {"status": "new"}}, "x")
        self.assertIsNone(undo_mod.pending_undo(1, "nope"))
        self.assertIsNotNone(undo_mod.pending_undo(1, token))
        self.assertEqual(undo_mod.undo_row(None), [])
        self.assertTrue(undo_mod.undo_row(token, "забрал")[0][0].data.startswith(b"ud:"))
