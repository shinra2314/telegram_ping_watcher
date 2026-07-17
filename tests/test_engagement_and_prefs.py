"""Member engagement (claim rate), min_score filter, premium broadcast split."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.bot_prefs import (  # noqa: E402
    DEFAULT_MEMBER_PREFS,
    filter_broadcast_members,
    member_allows,
    parse_member_prefs,
    toggle_member_pref,
)

try:
    import database
except ModuleNotFoundError as exc:  # pure tests run without project deps installed
    if exc.name != "aiosqlite":
        raise
    database = None

if database is not None:
    from pulse_desk import bot_notify
    from pulse_desk.app_ctx import state


def _member(tg_id: int, role: str = "viewer", prefs: dict | None = None, blocked: bool = False) -> dict:
    return {
        "tg_id": tg_id,
        "role": role,
        "blocked": blocked,
        "notification_prefs": json.dumps(prefs) if prefs is not None else None,
    }


class MinScorePrefsTests(unittest.TestCase):
    def test_parse_min_score_tolerant(self):
        self.assertEqual(parse_member_prefs(None)["min_score"], 0)
        self.assertEqual(parse_member_prefs(json.dumps({"min_score": 7}))["min_score"], 7)
        self.assertEqual(parse_member_prefs(json.dumps({"min_score": 999}))["min_score"], 100)
        self.assertEqual(parse_member_prefs(json.dumps({"min_score": "мусор"}))["min_score"], 0)
        # Bool flags stay bools.
        self.assertIs(parse_member_prefs(json.dumps({"mentions": 0}))["mentions"], False)

    def test_toggle_never_flips_min_score(self):
        prefs = dict(DEFAULT_MEMBER_PREFS, min_score=5)
        self.assertEqual(toggle_member_pref(prefs, "min_score")["min_score"], 5)

    def test_member_allows_min_score_gate(self):
        prefs = dict(DEFAULT_MEMBER_PREFS, min_score=5)
        self.assertFalse(member_allows(prefs, "giveaway", score=3))
        self.assertTrue(member_allows(prefs, "giveaway", score=5))
        self.assertTrue(member_allows(prefs, "giveaway", score=None), "unknown score always passes")
        self.assertTrue(member_allows(prefs, "win", score=3), "gate applies to giveaways only")
        zero = dict(DEFAULT_MEMBER_PREFS, min_score=0)
        self.assertTrue(member_allows(zero, "giveaway", score=1))

    def test_filter_by_score(self):
        members = [
            _member(1, prefs={"min_score": 5}),
            _member(2, prefs={"min_score": 0}),
        ]
        got = filter_broadcast_members(members, "giveaway", set(), score=3)
        self.assertEqual([m["tg_id"] for m in got], [2])


class PremiumSplitTests(unittest.TestCase):
    def test_premium_only_split(self):
        members = [
            _member(1, role="viewer"),
            _member(2, role="premium"),
            _member(3, role="premium", blocked=True),
        ]
        premium = filter_broadcast_members(members, "giveaway", set(), premium_only=True)
        rest = filter_broadcast_members(members, "giveaway", set(), premium_only=False)
        everyone = filter_broadcast_members(members, "giveaway", set(), premium_only=None)
        self.assertEqual([m["tg_id"] for m in premium], [2])
        self.assertEqual([m["tg_id"] for m in rest], [1])
        self.assertEqual([m["tg_id"] for m in everyone], [1, 2])


@unittest.skipIf(database is None, "aiosqlite is not installed")
class EngagementDbTests(unittest.TestCase):
    def _run(self, coro_factory):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                original_path = database.DB_PATH
                database.DB_PATH = Path(tmp) / "test.db"
                try:
                    await database.init_db()
                    return await coro_factory()
                finally:
                    database.DB_PATH = original_path

        return asyncio.run(scenario())

    async def _ping(self) -> int:
        return await database.save_ping({
            "date": "2026-07-08T10:00:00",
            "chat": "c",
            "chat_id": -1,
            "sender": "s",
            "message_id": 1,
            "mentions": ["u"],
            "link": "",
            "text": "t",
            "chat_type": "channel",
        })

    def test_upsert_and_stats(self):
        async def body():
            ping_id = await self._ping()
            await database.set_member_engagement(5, ping_id, "joined")
            self.assertEqual(await database.get_member_engagement(5, ping_id), "joined")
            # Change of mind overwrites, not duplicates.
            await database.set_member_engagement(5, ping_id, "skipped")
            self.assertEqual(await database.get_member_engagement(5, ping_id), "skipped")
            stats = await database.member_engagement_stats(5)
            self.assertEqual(stats, {"joined": 0, "skipped": 1})
            summary = await database.engagement_summary()
            self.assertEqual(summary["skipped"], 1)
            self.assertEqual(len(summary["members"]), 1)

        self._run(body)

    def test_unknown_action_rejected(self):
        async def body():
            ping_id = await self._ping()
            with self.assertRaises(ValueError):
                await database.set_member_engagement(5, ping_id, "hacked")

        self._run(body)


@unittest.skipIf(database is None, "aiosqlite is not installed")
class ExecutePendingPremiumTests(unittest.TestCase):
    def _run(self, coro_factory):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                original_path = database.DB_PATH
                original_client = state.bot_client
                original_broadcast = bot_notify.broadcast_member_notification
                database.DB_PATH = Path(tmp) / "test.db"
                try:
                    await database.init_db()
                    return await coro_factory()
                finally:
                    database.DB_PATH = original_path
                    state.bot_client = original_client
                    bot_notify.broadcast_member_notification = original_broadcast

        return asyncio.run(scenario())

    def test_deferred_send_excludes_premium_and_reuses_token(self):
        async def body():
            expires = (datetime.now() + timedelta(minutes=5)).replace(microsecond=0).isoformat()
            pb_id = await database.create_pending_broadcast(
                None, "giveaway", "msg", link="https://t.me/x/1", expires_at=expires, bc_token="tokpremium"
            )
            row = await database.get_pending_broadcast(pb_id)
            calls: list[dict] = []

            async def fake_broadcast(message, buttons=None, file=None, notif_type="mention", score=None, premium_only=None):
                calls.append({"premium_only": premium_only})
                return [(9, 90)]

            bot_notify.broadcast_member_notification = fake_broadcast
            count, token = await bot_notify.execute_pending_broadcast(row)
            self.assertEqual(count, 1)
            self.assertEqual(token, "tokpremium", "premium enqueue token reused for hide button")
            self.assertEqual(calls, [{"premium_only": False}], "premium members excluded from deferred send")
            saved = await database.get_broadcast_messages("tokpremium")
            self.assertEqual([(r["tg_id"], r["message_id"]) for r in saved], [(9, 90)])

        self._run(body)

    def test_deferred_send_no_members_keeps_premium_token(self):
        async def body():
            expires = (datetime.now() + timedelta(minutes=5)).replace(microsecond=0).isoformat()
            pb_id = await database.create_pending_broadcast(
                None, "mention", "msg", expires_at=expires, bc_token="tokonly"
            )
            row = await database.get_pending_broadcast(pb_id)

            async def fake_broadcast(message, buttons=None, file=None, notif_type="mention", score=None, premium_only=None):
                return []

            bot_notify.broadcast_member_notification = fake_broadcast
            count, token = await bot_notify.execute_pending_broadcast(row)
            self.assertEqual(count, 0)
            self.assertEqual(token, "tokonly", "hide button must still cover premium copies")

        self._run(body)


if __name__ == "__main__":
    unittest.main()
