"""Every detected mention reaches the owner (ping_notify, schema 24).

A ping card used to be one shot: whatever got in the way of the first send —
the bot offline or never started, quiet hours, a filter, vacation mode — left a
stored ping that no later pass would ever announce. These tests pin each of
those paths to "delivered", late or silent, but delivered.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import database
from telegram_ping_watcher import build_ping_regex

from pulse_desk import bot_notify, ping_notify, ping_pipeline
from pulse_desk.app_ctx import state
from pulse_desk.bot_connection import keep_starting_bot
from pulse_desk.watch_settings import owner_card_muted

ADMIN = 777


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _record(**overrides) -> dict:
    base = {
        "date": _iso(datetime.now()),
        "chat": "test_channel",
        "chat_id": -100123,
        "chat_type": "channel",
        "sender": "sender",
        "sender_id": 1,
        "message_id": 5,
        "mentions": ["@MuverGT"],
        "link": "https://t.me/test_channel/5",
        "text": "hello @MuverGT",
        "detected_at": _iso(datetime.now()),
        "is_win": False,
        "is_giveaway": False,
        "priority_score": 10,
        "priority_label": "normal",
        "action_status": "new",
    }
    base.update(overrides)
    return base


class FakeBot:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.sent: list[dict] = []
        self.edited: list[dict] = []

    def is_connected(self) -> bool:
        return True

    async def connect(self):
        return None

    async def send_message(self, target, message, buttons=None, link_preview=False, file=None, silent=None):
        if self.fail:
            raise RuntimeError("Telegram is having internal issues")
        self.sent.append({"target": target, "message": message, "silent": bool(silent), "file": file})
        return SimpleNamespace(id=100 + len(self.sent))

    async def edit_message(self, target, message_id, text, buttons=None, link_preview=False):
        self.edited.append({"target": target, "message_id": message_id, "text": text})


class DbCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_path = database.DB_PATH
        database.DB_PATH = Path(self._tmp.name) / "test.db"
        await database.init_db()
        self._old_client = state.bot_client
        self._old_admin = bot_notify.ADMIN_ID
        bot_notify.ADMIN_ID = ADMIN
        bot_notify.reset_unreachable_peers()
        ping_notify.reset_for_tests()
        state.notification_seen.clear()
        # No member fan-out in these tests unless one asks for it.
        self._broadcast = patch.object(bot_notify, "broadcast_member_notification", new=AsyncMock(return_value=[]))
        self._broadcast.start()
        self._sleep = patch("pulse_desk.bot_notify.asyncio.sleep", new=AsyncMock())
        self._sleep.start()

    async def asyncTearDown(self):
        self._sleep.stop()
        self._broadcast.stop()
        state.bot_client = self._old_client
        bot_notify.ADMIN_ID = self._old_admin
        bot_notify.reset_unreachable_peers()
        ping_notify.reset_for_tests()
        database.DB_PATH = self._old_path
        self._tmp.cleanup()

    async def owed(self, ping_id: int) -> tuple[bool, bool]:
        return ping_notify.owed_cards(await database.get_ping_by_id(ping_id))


class OutboxDbTests(DbCase):
    async def test_new_ping_owes_a_card_until_marked(self):
        ping_id = await database.save_ping(_record())
        self.assertEqual(await self.owed(ping_id), (True, False))
        self.assertEqual(await database.count_owed_ping_notifications(), 1)
        await database.mark_ping_notified(ping_id, win=False)
        self.assertEqual(await self.owed(ping_id), (False, False))

    async def test_win_edited_into_a_settled_ping_owes_the_trophy_card(self):
        ping_id = await database.save_ping(_record())
        await database.mark_ping_notified(ping_id, win=False)
        await database.save_ping(_record(is_win=True))
        self.assertEqual(await self.owed(ping_id), (False, True))
        await database.mark_ping_notified(ping_id, win=True)
        self.assertEqual(await self.owed(ping_id), (False, False))

    async def test_backlog_insert_is_settled_at_birth(self):
        ping_id = await database.save_ping(_record(is_win=True, notified_at=_iso(datetime.now())))
        self.assertEqual(await self.owed(ping_id), (False, False))

    async def test_list_respects_grace_window_and_copies(self):
        now = datetime.now()
        fresh = await database.save_ping(_record(message_id=1, detected_at=_iso(now)))
        ready = await database.save_ping(_record(message_id=2, detected_at=_iso(now - timedelta(minutes=5))))
        stale = await database.save_ping(_record(message_id=3, detected_at=_iso(now - timedelta(hours=30))))
        copy = await database.save_ping(_record(message_id=4, detected_at=_iso(now - timedelta(minutes=5))))
        await database.mark_duplicates(ready, [copy])
        since, before = ping_notify.retry_bounds(now)
        rows = await database.list_owed_ping_notifications(since=since, before=before)
        self.assertEqual([row["id"] for row in rows], [ready])
        self.assertEqual(await database.expire_owed_ping_notifications(before=since), 1)
        self.assertEqual(await self.owed(stale), (False, False))
        self.assertEqual(await self.owed(fresh), (True, False))

    async def test_init_db_twice_keeps_owed_rows(self):
        ping_id = await database.save_ping(_record())
        await database.init_db()
        self.assertEqual(await self.owed(ping_id), (True, False), "a restart must not settle owed cards")


class DeliveryTests(DbCase):
    async def test_failed_send_is_delivered_late_by_retry(self):
        ping_id = await database.save_ping(_record(detected_at=_iso(datetime.now() - timedelta(minutes=10))))
        state.bot_client = FakeBot(fail=True)
        with patch("pulse_desk.bot_notify.record_app_event", new=AsyncMock()):
            settled = await ping_notify.notify_detected_ping(ping_id, _record())
        self.assertFalse(settled)
        self.assertEqual(await self.owed(ping_id), (True, False), "a failed card stays owed")

        bot = FakeBot()
        state.bot_client = bot
        stats = await ping_notify.retry_owed_notifications()
        self.assertEqual(stats["sent"], 1)
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(bot.sent[0]["target"], ADMIN)
        self.assertIn("С опозданием", bot.sent[0]["message"])
        self.assertEqual(await self.owed(ping_id), (False, False))
        self.assertEqual((await ping_notify.retry_owed_notifications())["sent"], 0, "second pass is a no-op")

    async def test_bot_never_started_leaves_card_owed(self):
        ping_id = await database.save_ping(_record())
        state.bot_client = None
        self.assertFalse(await ping_notify.notify_detected_ping(ping_id, _record()))
        self.assertEqual(await self.owed(ping_id), (True, False))
        self.assertEqual((await ping_notify.retry_owed_notifications())["sent"], 0)

    async def test_quiet_hours_send_silently_instead_of_dropping(self):
        await database.set_setting("notifications", {"quiet_hours": {"enabled": True, "from": "00:00", "to": "23:59"}})
        ping_id = await database.save_ping(_record())
        bot = FakeBot()
        state.bot_client = bot
        self.assertTrue(await ping_notify.notify_detected_ping(ping_id, _record()))
        self.assertEqual(len(bot.sent), 1)
        self.assertTrue(bot.sent[0]["silent"])
        bot_notify.broadcast_member_notification.assert_not_awaited()

    async def test_disabled_notifications_still_reach_the_owner(self):
        await database.set_setting("notifications", {"enabled": False})
        ping_id = await database.save_ping(_record(is_win=True))
        bot = FakeBot()
        state.bot_client = bot
        self.assertTrue(await ping_notify.notify_detected_ping(ping_id, _record(is_win=True)))
        self.assertEqual(len(bot.sent), 1)
        self.assertTrue(bot.sent[0]["silent"])
        self.assertEqual(await self.owed(ping_id), (False, False))

    async def test_vacation_mutes_mentions_but_delivers_them(self):
        until = _iso(datetime.now() + timedelta(days=3))
        await database.set_setting("vacation", {"active": True, "until": until})
        ping_id = await database.save_ping(_record())
        bot = FakeBot()
        state.bot_client = bot
        self.assertTrue(await ping_notify.notify_detected_ping(ping_id, _record()))
        self.assertEqual(len(bot.sent), 1)
        self.assertTrue(bot.sent[0]["silent"])

    async def test_loud_by_default(self):
        ping_id = await database.save_ping(_record())
        bot = FakeBot()
        state.bot_client = bot
        await ping_notify.notify_detected_ping(ping_id, _record())
        self.assertFalse(bot.sent[0]["silent"])

    async def test_racing_passes_send_one_card_and_one_broadcast(self):
        ping_id = await database.save_ping(_record())
        bot = FakeBot()
        state.bot_client = bot
        await asyncio.gather(
            ping_notify.notify_detected_ping(ping_id, _record()),
            ping_notify.notify_detected_ping(ping_id, _record()),
        )
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(bot_notify.broadcast_member_notification.await_count, 1)

    async def test_owner_card_goes_out_before_the_member_broadcast(self):
        ping_id = await database.save_ping(_record())
        bot = FakeBot()
        state.bot_client = bot
        order: list[str] = []

        async def fake_broadcast(*args, **kwargs):
            order.append(f"broadcast after {len(bot.sent)} owner card(s)")
            return [(5, 10)]

        bot_notify.broadcast_member_notification.side_effect = fake_broadcast
        await ping_notify.notify_detected_ping(ping_id, _record())
        self.assertEqual(order, ["broadcast after 1 owner card(s)"])
        self.assertEqual(len(bot.edited), 1, "hide-from-friends button is edited on afterwards")


class BroadcastWithoutBotTests(DbCase):
    async def test_member_copies_are_queued_when_the_bot_never_started(self):
        self._broadcast.stop()
        try:
            state.bot_client = None
            member = {"tg_id": 42, "permissions": ""}
            with patch.object(database, "list_bot_members", new=AsyncMock(return_value=[member])), \
                    patch.object(bot_notify, "filter_broadcast_members", return_value=[member]):
                delivered = await bot_notify.broadcast_member_notification("card", notif_type="mention", token="t1")
            self.assertEqual(delivered, [])
            self.assertEqual(await database.count_pending_sends("t1"), 1)
        finally:
            self._broadcast.start()


def tag_bot_message(prefix: str = "🤑 Игровой чек на сумму 0.5$",
                    emojis=("🧔🏽‍♀️", "🚵‍♂", "🫷🏽", "🌋", "🙌")):
    """A tag bot's ad: a hidden user link on each emoji of the last line."""
    from telethon.helpers import add_surrogate
    from telethon.tl import types

    text = prefix + "\n\n"
    entities = []
    for index, emoji in enumerate(emojis):
        offset = len(add_surrogate(text))
        text += emoji
        entities.append(types.MessageEntityMentionName(offset=offset, length=len(add_surrogate(emoji)), user_id=1000 + index))
        text += " "
    message = FakeMessage(text + "​", mentioned=True)
    message.entities = entities
    return message


class FakeMessage:
    def __init__(self, text: str, *, mentioned: bool = False, sender_id: int = 9, out: bool = False):
        self.raw_text = text
        self.entities = None
        self.mentioned = mentioned
        self.out = out
        self.sender_id = sender_id
        self.chat_id = -100555
        self.id = 77
        self.date = datetime.now()
        self.edit_date = None

    async def get_chat(self):
        return SimpleNamespace(title="Comments", username="comments_chat", id=555)

    async def get_sender(self):
        return SimpleNamespace(first_name="Vasya", username="vasya", id=self.sender_id)


class GroupMentionPipelineTests(DbCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self._old_names = state.ping_usernames
        self._old_regex = state.ping_regex
        self._old_connected = set(state.connected_user_ids)
        state.ping_usernames = ["MuverGT", "ktkerf427"]
        state.ping_regex = build_ping_regex(state.ping_usernames)
        self.notify = AsyncMock(return_value=True)
        self._patches = [
            patch.object(ping_pipeline, "resolve_ping_user_ids", new=AsyncMock()),
            patch.object(ping_pipeline, "notify_detected_ping", new=self.notify),
        ]
        for item in self._patches:
            item.start()

    async def asyncTearDown(self):
        for item in self._patches:
            item.stop()
        state.ping_usernames = self._old_names
        state.ping_regex = self._old_regex
        state.connected_user_ids.clear()
        state.connected_user_ids.update(self._old_connected)
        await super().asyncTearDown()

    async def _process(self, message, chat_type="group", account_username="ktkerf427"):
        with patch.object(ping_pipeline, "get_message_chat_type", new=AsyncMock(return_value=chat_type)):
            return await ping_pipeline.process_ping_message(
                object(), message, account_label="acc", account_username=account_username,
            )

    async def test_reply_without_at_in_a_comment_thread_is_a_mention(self):
        ping_id = await self._process(FakeMessage("ну ты и выдал", mentioned=True))
        self.assertIsNotNone(ping_id)
        row = await database.get_ping_by_id(ping_id)
        self.assertEqual(row["mentions"], ["ktkerf427"])
        self.assertEqual(row["chat_type"], "group")
        self.notify.assert_awaited_once()

    async def test_text_mention_in_a_group_counts(self):
        self.assertIsNotNone(await self._process(FakeMessage("привет @MuverGT")))

    async def test_group_chatter_without_mention_is_ignored(self):
        self.assertIsNone(await self._process(FakeMessage("просто болтовня")))

    async def test_owner_accounts_talking_are_not_news(self):
        state.connected_user_ids.add(9)
        self.assertIsNone(await self._process(FakeMessage("@MuverGT глянь", sender_id=9)))

    async def test_private_chats_stay_out(self):
        self.assertIsNone(await self._process(FakeMessage("@MuverGT", mentioned=True), chat_type="private"))

    async def test_second_account_read_keeps_the_first_accounts_mention(self):
        ping_id = await self._process(FakeMessage("@MuverGT ну ты и выдал", mentioned=True))
        await self._process(FakeMessage("@MuverGT ну ты и выдал"), account_username="MuverGT")
        row = await database.get_ping_by_id(ping_id)
        self.assertEqual(sorted(row["mentions"], key=str.lower), ["ktkerf427", "MuverGT"])

    async def test_tag_bot_ad_is_kept_quietly(self):
        ping_id = await self._process(tag_bot_message())
        self.assertIsNotNone(ping_id, "still in the feed")
        self.notify.assert_not_awaited()
        self.assertEqual(await self.owed(ping_id), (False, False), "and no card is owed for it")

    async def test_written_out_name_beside_tag_emojis_still_pings(self):
        ping_id = await self._process(tag_bot_message(prefix="@MuverGT забери чек"))
        self.assertIsNotNone(ping_id)
        self.notify.assert_awaited_once()

    async def test_backlog_pass_settles_without_a_card(self):
        with patch.object(ping_pipeline, "get_message_chat_type", new=AsyncMock(return_value="group")):
            ping_id = await ping_pipeline.process_ping_message(
                object(), FakeMessage("@MuverGT old"), account_label="acc", notify=False,
            )
        self.notify.assert_not_awaited()
        self.assertEqual(await self.owed(ping_id), (False, False))


class PureRuleTests(unittest.TestCase):
    def test_own_mention_needs_the_flag_and_a_tracked_account(self):
        tracked = ["MuverGT", "Megatronus_praim"]
        msg = SimpleNamespace(mentioned=True, out=False)
        self.assertEqual(ping_pipeline.own_mention(msg, "megatronus_praim", tracked), "@Megatronus_praim")
        self.assertIsNone(ping_pipeline.own_mention(msg, "Swight0", tracked), "old session name is not tracked")
        self.assertIsNone(ping_pipeline.own_mention(SimpleNamespace(mentioned=False, out=False), "MuverGT", tracked))
        self.assertIsNone(ping_pipeline.own_mention(SimpleNamespace(mentioned=True, out=True), "MuverGT", tracked))
        self.assertIsNone(ping_pipeline.own_mention(msg, "", tracked))

    def test_owner_card_muted_covers_every_former_drop(self):
        record = _record()
        self.assertFalse(owner_card_muted(record, {"enabled": True}))
        self.assertTrue(owner_card_muted(record, {"enabled": False}))
        self.assertTrue(owner_card_muted(record, {"chats": ["other_chat"]}))
        self.assertTrue(owner_card_muted(_record(is_win=True), {"include_wins": False}))
        always_quiet = {"quiet_hours": {"enabled": True, "from": "00:00", "to": "23:59"}}
        self.assertTrue(owner_card_muted(record, always_quiet))

    def test_late_footer_only_when_late(self):
        now = datetime(2026, 9, 16, 12, 0)
        self.assertEqual(bot_notify.late_card_footer(_iso(now - timedelta(seconds=30)), now), "")
        self.assertIn("11:00", bot_notify.late_card_footer(_iso(now - timedelta(hours=1)), now))
        self.assertIn("15.09", bot_notify.late_card_footer(_iso(now - timedelta(days=1)), now))
        self.assertEqual(bot_notify.late_card_footer("garbage", now), "")

    def test_live_dedupe_key_is_per_account_only_when_mentioned(self):
        from pulse_desk.telegram_accounts import live_message_key

        plain = SimpleNamespace(chat_id=-1001, id=5, mentioned=False)
        self.assertEqual(live_message_key(plain, "A"), live_message_key(plain, "B"))
        reply = SimpleNamespace(chat_id=-1001, id=5, mentioned=True)
        self.assertNotEqual(live_message_key(reply, "A"), live_message_key(reply, "B"))
        self.assertNotEqual(live_message_key(plain, "A", "2026-09-16"), live_message_key(plain, "A"))

    def test_mass_tag_needs_several_picture_links(self):
        from telegram_ping_watcher import hidden_user_links, is_mass_tag
        from telethon.tl import types

        regex = build_ping_regex(["MuverGT"])
        self.assertEqual(hidden_user_links(tag_bot_message()), 5)
        self.assertTrue(is_mass_tag(tag_bot_message(), regex, ["MuverGT"]))
        self.assertFalse(is_mass_tag(tag_bot_message(emojis=("🌋", "🙌")), regex, ["MuverGT"]), "two links is a chat, not a crowd")
        self.assertFalse(is_mass_tag(tag_bot_message(prefix="Победители: @MuverGT"), regex, ["MuverGT"]))
        named = FakeMessage("Вася Петя Коля")
        named.entities = [types.MessageEntityMentionName(offset=i * 5, length=4, user_id=i) for i in range(3)]
        self.assertEqual(hidden_user_links(named), 0, "links on names are real mentions")

    def test_group_card_title(self):
        msg, _, _ = bot_notify.build_ping_card(_record(chat_type="group"))
        self.assertIn("Упоминание в чате", msg)

    def test_group_mentions_due_skips_unchanged_dialogs(self):
        from pulse_desk import scan_engine

        key = ("s", 1)
        scan_engine._group_mentions_seen.pop(key, None)
        self.assertTrue(scan_engine.group_mentions_due(key, 2, 100))
        scan_engine._group_mentions_seen[key] = (2, 100)
        self.assertFalse(scan_engine.group_mentions_due(key, 2, 100))
        self.assertTrue(scan_engine.group_mentions_due(key, 3, 101))
        self.assertFalse(scan_engine.group_mentions_due(key, 0, 101))
        scan_engine._group_mentions_seen.pop(key, None)


class KeepStartingBotTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_until_started_then_announces(self):
        started = {"ok": False, "calls": 0}
        announced: list[int] = []

        async def start():
            started["calls"] += 1
            started["ok"] = started["calls"] >= 3

        async def on_started(attempts):
            announced.append(attempts)

        attempts = await keep_starting_bot(
            start, is_started=lambda: started["ok"], sleep=AsyncMock(), delay_for=lambda n: 0,
            on_started=on_started,
        )
        self.assertEqual(attempts, 3)
        self.assertEqual(announced, [3])

    async def test_nothing_to_do_when_already_started(self):
        start = AsyncMock()
        on_started = AsyncMock()
        self.assertEqual(await keep_starting_bot(start, is_started=lambda: True, on_started=on_started), 0)
        start.assert_not_awaited()
        on_started.assert_not_awaited()

    async def test_stops_on_shutdown(self):
        start = AsyncMock()
        attempts = await keep_starting_bot(
            start, is_started=lambda: False, should_stop=lambda: True, sleep=AsyncMock(),
        )
        self.assertEqual(attempts, 0)
        start.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()


class IgnoredChatsTests(GroupMentionPipelineTests):
    async def asyncTearDown(self):
        from pulse_desk import ignored_chats

        ignored_chats.apply({})
        await super().asyncTearDown()

    async def test_ignored_chat_is_not_read_at_all(self):
        from pulse_desk import ignored_chats

        await ignored_chats.save(ignored_chats.add(await ignored_chats.load(), -100555, "5-А ЛИГА Казани"))
        self.assertIsNone(await self._process(FakeMessage("привет @MuverGT", mentioned=True)))
        self.notify.assert_not_awaited()
        cfg = await ignored_chats.load()
        self.assertEqual(cfg, {"-100555": "5-А ЛИГА Казани"})
        await ignored_chats.save(ignored_chats.remove(cfg, -100555))
        self.assertIsNotNone(await self._process(FakeMessage("привет @MuverGT")))

    def test_normalize_drops_garbage(self):
        from pulse_desk import ignored_chats

        self.assertEqual(ignored_chats.normalize({"-1001": "A", "x": "B"}), {"-1001": "A"})
        self.assertEqual(ignored_chats.normalize(None), {})

    def test_group_cards_offer_the_ignore_button(self):
        buttons = bot_notify._admin_card_buttons("https://t.me/c/1/2", 42, "group")
        datas = [getattr(b, "data", b"") for row in buttons for b in row]
        self.assertIn(b"igc:add:42", datas)
        channel = bot_notify._admin_card_buttons("https://t.me/c/1/2", 42, "channel")
        self.assertNotIn(b"igc:add:42", [getattr(b, "data", b"") for row in channel for b in row])
