from __future__ import annotations

import asyncio
import logging
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from telegram_ping_watcher import (
    DEFAULT_USERNAMES,
    build_message_link,
    build_ping_regex,
    chat_type_from_entity,
    extract_mentions,
    local_iso_datetime,
    message_to_record,
    message_looks_like_broadcast_channel,
    normalize_usernames,
)
from pulse_desk.dashboard import build_dashboard_summary
from pulse_desk.giveaways import (
    RequiredChannel,
    extract_estimated_value,
    extract_external_requirements,
    extract_required_channel_usernames,
    giveaway_outcome_resolution,
    is_internal_notification_text,
    is_giveaway_outcome_text,
    is_win_text,
    score_analysis,
)
from pulse_desk.jobs import start_tracked_task
from pulse_desk.runtime import AppState
from pulse_desk.scan import channel_sweep_start_id, normalize_recent_edit_scan_limit, normalize_scan_history_limit
from pulse_desk.simple_qr import qr_matrix, terminal_qr, write_svg_qr
from pulse_desk.telegram_errors import auth_key_duplicated_message, is_auth_key_duplicated
from pulse_desk.telegram_reconnect import reconnect_delay_seconds

try:
    import database
except ModuleNotFoundError as exc:  # Allows parser tests to run without project dependencies installed.
    if exc.name != "aiosqlite":
        raise
    database = None


class CoreParsingTests(unittest.TestCase):
    def test_reconnect_delay_uses_exponential_backoff_with_cap(self):
        delays = [
            reconnect_delay_seconds(
                "alpha",
                attempt,
                base_seconds=20,
                max_seconds=90,
                jitter_seconds=0,
            )
            for attempt in range(1, 6)
        ]
        self.assertEqual(delays, [20, 40, 80, 90, 90])

    def test_reconnect_delay_adds_stable_per_session_jitter(self):
        first = reconnect_delay_seconds("alpha", 2, base_seconds=20, max_seconds=300, jitter_seconds=15)
        second = reconnect_delay_seconds("alpha", 2, base_seconds=20, max_seconds=300, jitter_seconds=15)
        other = reconnect_delay_seconds("beta", 2, base_seconds=20, max_seconds=300, jitter_seconds=15)
        self.assertEqual(first, second)
        self.assertGreaterEqual(first, 40)
        self.assertLessEqual(first, 55)
        self.assertGreaterEqual(other, 40)
        self.assertLessEqual(other, 55)

    def test_dashboard_summary_prioritizes_operational_attention(self):
        summary = build_dashboard_summary(
            status={
                "accounts_online": 1,
                "accounts_total": 2,
                "tracked_usernames": ["Alpha", "Beta"],
                "scan": {
                    "running": True,
                    "processed_usernames": 3,
                    "total_usernames": 6,
                    "processed_accounts": 1,
                    "total_accounts": 2,
                    "total_channels": 9,
                    "found": 4,
                },
                "last_scan": {"status": "running"},
            },
            analytics={
                "total_pings": 12,
                "new_pings": 3,
                "important": 1,
                "resolved": 5,
                "favorites": 2,
                "total_channels": 9,
                "channel_memberships_total": 9,
                "channel_chats_total": 7,
            },
            tasks={"claim_prize": [{}], "waiting_result": [{}], "all_open": [{}, {}]},
            giveaway_board={
                "stats": {"claim_prize": 2},
                "bucket_counts": {"need_action": 2, "waiting_result": 4, "suspicious": 1},
            },
            problem_events=[{"message": "scan warning"}],
        )
        self.assertEqual(summary["health_level"], "bad")
        self.assertEqual(summary["scan_progress"]["percent"], 50)
        self.assertEqual(summary["scan_progress"]["total_channels"], 9)
        self.assertEqual(summary["counts"]["giveaway_need_action"], 2)
        self.assertEqual(summary["counts"]["total_channels"], 9)
        self.assertTrue(any(item["kind"] == "accounts" for item in summary["attention"]))
        self.assertTrue(any(item["key"] == "channels" and item["ok"] for item in summary["readiness"]))

    def test_dashboard_summary_reports_calm_state(self):
        summary = build_dashboard_summary(
            status={
                "accounts_online": 1,
                "accounts_total": 1,
                "tracked_usernames": ["Alpha"],
                "scan": {"running": False},
                "last_scan": {"status": "finished"},
            },
            analytics={"total_pings": 4, "new_pings": 0, "important": 0, "resolved": 4, "favorites": 0, "total_channels": 1},
            tasks={"claim_prize": [], "waiting_result": [], "all_open": []},
            giveaway_board={"stats": {}, "bucket_counts": {}},
        )
        self.assertEqual(summary["health_level"], "good")
        self.assertEqual(summary["attention"][0]["kind"], "calm")

    def test_tracked_task_keeps_strong_reference_until_done(self):
        async def runner():
            state = AppState()
            logger = logging.getLogger("test")
            event = asyncio.Event()

            async def wait_for_event():
                await event.wait()
                return "ok"

            task = start_tracked_task(state, logger, "sample", wait_for_event())
            self.assertIs(state.background_tasks["sample"], task)
            self.assertIn("sample", state.background_task_names)
            event.set()
            self.assertEqual(await task, "ok")
            await asyncio.sleep(0)
            self.assertNotIn("sample", state.background_tasks)
            self.assertNotIn("sample", state.background_task_names)

        asyncio.run(runner())

    def test_channel_sweep_requires_complete_positive_checkpoints(self):
        self.assertIsNone(channel_sweep_start_id({}))
        self.assertIsNone(channel_sweep_start_id({"Alpha": 42, "Beta": 0}))
        self.assertEqual(channel_sweep_start_id({"Alpha": 42, "Beta": 45}), 42)

    def test_normalize_usernames_deduplicates_case_insensitively(self):
        self.assertEqual(normalize_usernames([" @Test ", "test", "Other"]), ["Test", "Other"])

    def test_default_usernames_match_requested_targets(self):
        self.assertEqual(
            list(DEFAULT_USERNAMES),
            ["alga_kazakhst2n", "w3v8f0rm", "Fjfjfjfjds", "Timofey02513", "MuverGT", "xdfusybau", "davifd23", "fsdfsdfdsg34", "sakmangg69"],
        )

    def test_scan_history_limit_zero_means_unlimited(self):
        self.assertEqual(normalize_scan_history_limit(0), 0)
        self.assertEqual(normalize_scan_history_limit(""), 0)
        self.assertEqual(normalize_scan_history_limit(-20), 0)
        self.assertEqual(normalize_scan_history_limit(12000), 12000)

    def test_recent_edit_scan_limit_is_clamped(self):
        self.assertEqual(normalize_recent_edit_scan_limit(0), 0)
        self.assertEqual(normalize_recent_edit_scan_limit(""), 20)
        self.assertEqual(normalize_recent_edit_scan_limit(-5), 0)
        self.assertEqual(normalize_recent_edit_scan_limit(900), 500)

    def test_auth_key_duplicated_error_is_detected_from_telethon_text(self):
        exc = RuntimeError("The authorization key (session file) was used under two different IP addresses simultaneously")
        self.assertTrue(is_auth_key_duplicated(exc))
        self.assertFalse(is_auth_key_duplicated(RuntimeError("ordinary network failure")))
        self.assertIn("alpha", auth_key_duplicated_message("alpha"))

    def test_local_qr_generator_outputs_matrix_terminal_and_svg(self):
        matrix = qr_matrix("tg://login?token=test-token")
        self.assertTrue(matrix)
        self.assertEqual(len(matrix), len(matrix[0]))
        self.assertIn("██", terminal_qr("tg://login?token=test-token"))
        with tempfile.TemporaryDirectory() as tmp:
            path = write_svg_qr("tg://login?token=test-token", Path(tmp) / "qr.svg")
            self.assertIn("<svg", path.read_text(encoding="utf-8"))

    def test_extract_mentions_from_text(self):
        regex = build_ping_regex(["Alpha", "Beta"])
        message = SimpleNamespace(raw_text="hello @alpha and @Beta_test and @Beta", entities=None)
        self.assertEqual(extract_mentions(message, regex, ["Alpha", "Beta"]), ["@Alpha", "@Beta"])

    def test_extract_mentions_matches_text_mention_by_user_id(self):
        from telethon import types as tg_types

        text = "🏆 Победители: ww zavoz 67"
        offset = text.index("ww zavoz 67")
        entity = tg_types.MessageEntityMentionName(offset=offset, length=len("ww zavoz 67"), user_id=777)
        message = SimpleNamespace(raw_text=text, entities=[entity])
        regex = build_ping_regex(["sakmangg69"])

        # No id map: a name-link ping is invisible (the original bug).
        self.assertEqual(extract_mentions(message, regex, ["sakmangg69"]), [])
        # With the resolved id map: the name-link resolves to the tracked username.
        self.assertEqual(
            extract_mentions(message, regex, ["sakmangg69"], {777: "sakmangg69"}),
            ["@sakmangg69"],
        )

    def test_extract_mentions_from_profile_link_in_text(self):
        regex = build_ping_regex(["Sanrayder"])
        text = "🏆 Победитель: http://t.me/Sanrayder — забирай приз"
        message = SimpleNamespace(raw_text=text, entities=None)
        self.assertEqual(extract_mentions(message, regex, ["Sanrayder"]), ["@Sanrayder"])

        for variant in ("t.me/Sanrayder", "https://t.me/@Sanrayder", "https://www.telegram.me/Sanrayder"):
            message = SimpleNamespace(raw_text=f"итоги {variant}", entities=None)
            self.assertEqual(extract_mentions(message, regex, ["Sanrayder"]), ["@Sanrayder"], variant)

        message = SimpleNamespace(raw_text="t.me/Sanrayderbot", entities=None)
        self.assertEqual(extract_mentions(message, regex, ["Sanrayder"]), [])

    def test_extract_mentions_from_hidden_hyperlink(self):
        from telethon import types as tg_types

        text = "Победитель: Саня"
        offset = text.index("Саня")
        message = SimpleNamespace(
            raw_text=text,
            entities=[tg_types.MessageEntityTextUrl(offset=offset, length=4, url="https://t.me/Sanrayder")],
        )
        regex = build_ping_regex(["Sanrayder"])
        self.assertEqual(extract_mentions(message, regex, ["Sanrayder"]), ["@Sanrayder"])

    def test_extract_mentions_from_hidden_user_id_link(self):
        from telethon import types as tg_types

        text = "Победитель: Саня"
        offset = text.index("Саня")
        message = SimpleNamespace(
            raw_text=text,
            entities=[tg_types.MessageEntityTextUrl(offset=offset, length=4, url="tg://user?id=777")],
        )
        regex = build_ping_regex(["Sanrayder"])
        self.assertEqual(extract_mentions(message, regex, ["Sanrayder"]), [])
        self.assertEqual(
            extract_mentions(message, regex, ["Sanrayder"], {777: "Sanrayder"}),
            ["@Sanrayder"],
        )

    def test_build_ping_regex_matches_exact_username(self):
        regex = build_ping_regex(["Alpha"])
        self.assertIsNotNone(regex.search("hello @Alpha"))
        self.assertIsNone(regex.search("hello @Alphabet"))

    def test_build_public_message_link(self):
        chat = SimpleNamespace(username="channel")
        message = SimpleNamespace(id=42, chat_id=-100123)
        self.assertEqual(build_message_link(chat, message), "https://t.me/channel/42")

    def test_build_private_channel_link(self):
        chat = SimpleNamespace(username=None)
        message = SimpleNamespace(id=42, chat_id=-100987654321)
        self.assertEqual(build_message_link(chat, message), "https://t.me/c/987654321/42")

    def test_message_channel_hint_rejects_groups(self):
        self.assertTrue(message_looks_like_broadcast_channel(SimpleNamespace(is_channel=True, is_group=False)))
        self.assertFalse(message_looks_like_broadcast_channel(SimpleNamespace(is_channel=True, is_group=True)))
        self.assertFalse(message_looks_like_broadcast_channel(SimpleNamespace(is_channel=False, is_group=False)))

    def test_channel_record_requires_tracked_mention_by_default(self):
        async def runner():
            chat = SimpleNamespace(title="Hot Steam/News", username="hottgnews")
            sender = SimpleNamespace(first_name="Hot Steam/News", username="hottgnews")

            class Message:
                id = 510
                chat_id = 123456
                sender_id = 123456
                date = datetime(2026, 5, 22, 16, 38, 4, tzinfo=timezone.utc)
                raw_text = "Ребят запускаем розыгрыш\nПобедители: @MuverGT\nИтоги: 22 мая в 22:00"
                entities = None

                async def get_chat(self):
                    return chat

                async def get_sender(self):
                    return sender

            self.assertIsNone(await message_to_record(None, Message(), build_ping_regex(["Alpha"]), ["Alpha"]))
            record = await message_to_record(None, Message(), build_ping_regex(["MuverGT"]), ["MuverGT"])
            self.assertIsNotNone(record)
            self.assertEqual(record["mentions"], ["@MuverGT"])
            self.assertEqual(record["chat"], "Hot Steam/News (@hottgnews)")
            self.assertEqual(record["link"], "https://t.me/hottgnews/510")

        asyncio.run(runner())

    def test_chat_type_unknown_without_telethon_entity(self):
        self.assertEqual(chat_type_from_entity(SimpleNamespace()), "unknown")

    def test_local_iso_datetime(self):
        value = datetime(2026, 5, 7, 12, 30, tzinfo=timezone.utc)
        self.assertIn("2026-05-07T", local_iso_datetime(value))

    def test_ping_tags_default_is_empty_list(self):
        import json
        raw = '[]'
        self.assertEqual(json.loads(raw), [])

    def test_ping_tags_round_trip(self):
        import json
        tags = ['важно', 'проверить']
        stored = json.dumps(tags, ensure_ascii=False)
        self.assertEqual(json.loads(stored), tags)

    def test_giveaway_extracts_required_channels(self):
        text = "Subscribe to @smallskin and https://t.me/another_channel to participate"
        self.assertEqual(extract_required_channel_usernames(text), ["another_channel", "smallskin"])

    def test_giveaway_outcome_counts_as_win_text(self):
        text = "🎉 Результаты розыгрыша:\n🏆 Победители:\n1. User (@w3v8f0rm)"
        self.assertTrue(is_giveaway_outcome_text(text))
        self.assertTrue(is_win_text(text, ["победитель"]))

    def test_generic_winner_count_is_not_win_text(self):
        text = "🎁 Розыгрыш\nОдин победитель получит приз\nИтоги завтра"
        self.assertFalse(is_giveaway_outcome_text(text))
        self.assertFalse(is_win_text(text, ["победитель"]))

    def test_negative_giveaway_outcome_resolution(self):
        self.assertEqual(giveaway_outcome_resolution("Победители: @Alpha (не выполнил условия)"), "missed")

    def test_internal_bot_notification_is_not_win(self):
        text = "Новое упоминание\n\nЧат: test\n\nПобедители: @Alpha"
        self.assertTrue(is_internal_notification_text(text))
        self.assertFalse(is_giveaway_outcome_text(text))
        self.assertFalse(is_win_text(text, ["победитель"]))

    def test_giveaway_external_requirements_are_manual_only(self):
        text = "Solve captcha, subscribe to youtube.com/test and leave a comment in chat"
        requirements = extract_external_requirements(text)
        self.assertIn("youtube.com", requirements)
        self.assertIn("captcha_or_verification", requirements)
        self.assertIn("comment_or_chat", requirements)

    def test_giveaway_score_prefers_small_channels_and_value(self):
        channels = [RequiredChannel(username="small", subscribers=900, giveaway_posts=2, accessible=True)]
        score, reasons, status, blocked = score_analysis(channels, ["Join"], [], extract_estimated_value("skin $75"))
        self.assertGreaterEqual(score, 65)
        self.assertEqual(status, "recommended")
        self.assertEqual(blocked, "")
        self.assertTrue(any("small channel" in reason for reason in reasons))

    def test_giveaway_score_blocks_external_requirements(self):
        score, reasons, status, blocked = score_analysis([], ["Join"], ["twitch.tv"], 20)
        self.assertEqual(status, "manual_required")
        self.assertIn("twitch.tv", blocked)

    def test_giveaway_analysis_gate_runs_once_per_message(self):
        from pulse_desk.giveaways import should_analyze_giveaway

        # First time a giveaway post is seen → run the (network-heavy) analysis.
        self.assertTrue(should_analyze_giveaway(True, is_new=True, source="telegram"))
        # Re-scan re-delivers the same known post → skip; this is the fix that
        # stops the redundant re-analysis churn.
        self.assertFalse(should_analyze_giveaway(True, is_new=False, source="telegram"))
        # An edit of a known post may have changed the content → analyze again.
        self.assertTrue(should_analyze_giveaway(True, is_new=False, source="telegram-edit"))
        # Non-giveaway messages never trigger giveaway analysis.
        self.assertFalse(should_analyze_giveaway(False, is_new=True, source="telegram"))

    def test_settings_history_schema(self):
        from datetime import datetime
        row = {
            "id": 1,
            "key": "usernames",
            "old_value": "alice",
            "new_value": "alice,bob",
            "changed_at": datetime.now().isoformat(),
        }
        self.assertIn("key", row)
        self.assertIn("old_value", row)
        self.assertIn("new_value", row)

    def test_digest_formats_empty(self):
        from pulse_desk.digest import format_digest
        result = format_digest([])
        self.assertIn("Нет", result)

    def test_digest_formats_pings(self):
        from pulse_desk.digest import format_digest
        pings = [
            {"chat": "channel1", "text": "Победитель @alice!", "is_win": True, "link": "https://t.me/c/1/1", "date": "2026-05-29T12:00:00"},
            {"chat": "channel2", "text": "Просто упоминание", "is_win": False, "link": "https://t.me/c/2/2", "date": "2026-05-29T11:00:00"},
        ]
        result = format_digest(pings)
        self.assertIn("channel1", result)
        self.assertIn("🏆", result)
        self.assertIn("Побед: 1", result)
        self.assertIn("Всего: 2", result)


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if database is None:
            self.skipTest("aiosqlite is not installed in this Python environment")
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "pulse_test.db"
        await database.init_db()

    async def asyncTearDown(self):
        database.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    async def test_save_ping_and_filter(self):
        await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Test Chat",
            "chat_id": 1,
            "sender": "Alice",
            "sender_id": 2,
            "message_id": 3,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/3",
            "text": "hello @Alpha",
            "chat_type": "group",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": False,
            "is_win": False,
        })
        rows = await database.get_pings(search="hello", mention="Alpha", chat_type="group")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["chat"], "Test Chat")
        by_ref = await database.get_ping_by_message_ref(1, 3)
        self.assertIsNotNone(by_ref)
        self.assertEqual(by_ref["mentions"], ["Alpha"])

    async def test_delete_ping_soft_deletes_giveaways_and_wins(self):
        base = {
            "date": "2026-05-07T10:00:00",
            "chat": "Test Chat",
            "chat_id": 1,
            "sender": "Alice",
            "sender_id": 2,
            "mentions": ["@Alpha"],
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_win": False,
        }
        await database.save_ping({**base, "message_id": 3, "link": "https://t.me/test/3", "text": "plain @Alpha", "is_giveaway": False})
        await database.save_ping({**base, "message_id": 4, "link": "https://t.me/test/4", "text": "giveaway @Alpha", "is_giveaway": True})
        await database.save_ping({**base, "message_id": 5, "link": "https://t.me/test/5", "text": "win @Alpha", "is_giveaway": False, "is_win": True})

        await database.delete_ping(1, 3)
        await database.delete_ping(1, 4)
        await database.delete_ping(1, 5)

        self.assertIsNone(await database.get_ping_by_message_ref(1, 3))
        giveaway = await database.get_ping_by_message_ref(1, 4)
        self.assertIsNotNone(giveaway)
        self.assertTrue(giveaway["deleted_at"])
        win = await database.get_ping_by_message_ref(1, 5)
        self.assertIsNotNone(win)
        self.assertTrue(win["deleted_at"])

        # repeated delete keeps the original timestamp
        first_deleted_at = giveaway["deleted_at"]
        await database.delete_ping(1, 4)
        again = await database.get_ping_by_message_ref(1, 4)
        self.assertEqual(again["deleted_at"], first_deleted_at)

    async def test_delete_ping_by_message_id_never_touches_channels(self):
        """A chat-less MessageDeleted event can only come from a private chat or
        a basic group, so identically numbered channel posts must survive."""
        base = {
            "date": "2026-05-07T10:00:00",
            "sender": "Alice",
            "sender_id": 2,
            "mentions": ["@Alpha"],
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": False,
            "is_win": False,
        }
        await database.save_ping({
            **base, "chat": "Channel", "chat_id": 1, "message_id": 7,
            "link": "https://t.me/test/7", "text": "channel win @Alpha",
            "chat_type": "channel", "is_win": True,
        })
        await database.save_ping({
            **base, "chat": "Channel", "chat_id": 1, "message_id": 8,
            "link": "https://t.me/test/8", "text": "channel mention @Alpha",
            "chat_type": "channel",
        })
        await database.save_ping({
            **base, "chat": "DM", "chat_id": 9, "message_id": 7,
            "link": "https://t.me/c/9/7", "text": "dm mention @Alpha",
            "chat_type": "private",
        })

        await database.delete_ping_by_message_id(7)
        await database.delete_ping_by_message_id(8)

        channel_win = await database.get_ping_by_message_ref(1, 7)
        self.assertIsNotNone(channel_win)
        self.assertIsNone(channel_win["deleted_at"])          # not flagged as removed
        self.assertIsNotNone(await database.get_ping_by_message_ref(1, 8))  # not dropped
        self.assertIsNone(await database.get_ping_by_message_ref(9, 7))     # the real target went

    async def test_save_ping_updates_duplicate_message_text_and_mentions(self):
        base_record = {
            "date": "2026-05-07T10:00:00",
            "chat": "Giveaway Channel",
            "chat_id": 11,
            "sender": "Channel",
            "sender_id": 22,
            "message_id": 33,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/33",
            "text": "initial @Alpha",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": True,
            "is_win": False,
        }
        first_id = await database.save_ping(base_record)
        updated_id = await database.save_ping({
            **base_record,
            "mentions": ["@Beta"],
            "text": "edited winner @Beta",
            "is_win": True,
            "priority_score": 90,
            "priority_label": "high",
        })
        self.assertEqual(updated_id, first_id)
        by_ref = await database.get_ping_by_message_ref(11, 33)
        self.assertEqual(by_ref["mentions"], ["Beta"])
        self.assertEqual(by_ref["text"], "edited winner @Beta")
        self.assertEqual(by_ref["is_win"], 1)

    async def test_get_pings_zero_limit_returns_all_rows(self):
        for index in range(3):
            await database.save_ping({
                "date": "2026-05-07T10:00:00",
                "chat": f"Test Chat {index}",
                "chat_id": index + 10,
                "sender": "Alice",
                "sender_id": 2,
                "message_id": index + 20,
                "mentions": ["@Alpha"],
                "link": f"https://t.me/test/{index}",
                "text": "hello @Alpha",
                "chat_type": "channel",
                "detected_at": "2026-05-07T10:01:00",
                "is_giveaway": False,
                "is_win": False,
            })
        rows = await database.get_pings(limit=0)
        self.assertEqual(len(rows), 3)

    async def test_search_matches_split_terms_without_exact_phrase(self):
        await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Prize Channel",
            "chat_id": 601,
            "sender": "Channel",
            "sender_id": 601,
            "message_id": 1,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/601",
            "text": "hello @Alpha, claim the skin prize before evening",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": False,
            "is_win": True,
        })
        await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Noise Channel",
            "chat_id": 602,
            "sender": "Channel",
            "sender_id": 602,
            "message_id": 1,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/602",
            "text": "hello @Alpha without the target word",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": False,
            "is_win": False,
        })
        rows = await database.get_pings(search="alpha prize")
        self.assertEqual([row["chat"] for row in rows], ["Prize Channel"])

    async def test_rebuild_search_indexes_repairs_missing_fts(self):
        ping_id = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Repair Channel",
            "chat_id": 603,
            "sender": "Channel",
            "sender_id": 603,
            "message_id": 1,
            "mentions": ["@Repair"],
            "link": "https://t.me/test/603",
            "text": "repairable search row",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": False,
            "is_win": False,
        })
        async with database._connect() as db:
            await db.execute("DELETE FROM pings_fts WHERE rowid = ?", (ping_id,))
            await db.commit()
        self.assertEqual(await database.rebuild_search_indexes(), {"pings": 1, "fts": 1, "mentions": 1})
        rows = await database.get_pings(search="repairable")
        self.assertEqual(rows[0]["id"], ping_id)

    async def test_mark_pings_read_bulk_uses_filters(self):
        await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Channel A",
            "chat_id": 501,
            "sender": "Channel",
            "sender_id": 501,
            "message_id": 1,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/1",
            "text": "hello @Alpha",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": False,
            "is_win": False,
        })
        await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Group A",
            "chat_id": 502,
            "sender": "Group",
            "sender_id": 502,
            "message_id": 1,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/2",
            "text": "hello @Alpha",
            "chat_type": "group",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": False,
            "is_win": False,
        })
        changed = await database.mark_pings_read(chat_type="channel")
        self.assertEqual(changed, 1)
        channel_rows = await database.get_pings(chat_type="channel", status="read")
        group_rows = await database.get_pings(chat_type="group", status="new")
        self.assertEqual(len(channel_rows), 1)
        self.assertEqual(len(group_rows), 1)

    async def test_giveaway_status_roundtrip(self):
        ping_id = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Giveaway Channel",
            "chat_id": 10,
            "sender": "Channel",
            "sender_id": 10,
            "message_id": 11,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/11",
            "text": "hello @Alpha",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": True,
            "is_win": False,
        })
        rows = await database.get_pings(chat_type="giveaway")
        self.assertEqual(rows[0]["id"], ping_id)
        self.assertEqual(rows[0]["giveaway_status"], "pending")
        await database.update_ping_meta(int(ping_id), giveaway_status="claimed")
        rows = await database.get_pings(chat_type="giveaway")
        self.assertEqual(rows[0]["giveaway_status"], "claimed")

    async def test_extended_giveaway_status_roundtrip(self):
        ping_id = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Reply Channel",
            "chat_id": 12,
            "sender": "Channel",
            "sender_id": 12,
            "message_id": 13,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/13",
            "text": "конкурс @Alpha итоги завтра",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": True,
            "is_win": False,
        })
        await database.update_ping_meta(int(ping_id), giveaway_status="missed_reply", action_status="missed")
        board = await database.get_giveaway_board(limit=20)
        self.assertIn(ping_id, [row["id"] for row in board["buckets"]["done"]])
        self.assertEqual(board["stats"]["missed_reply"], 1)

    async def test_won_prize_stays_on_the_board_despite_manual_requirements(self):
        """Captcha/comment rules gate *joining* a giveaway. Once it is won the
        prize is owed, so the analysis must not hide it from every bucket."""
        ping_id = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Captcha Channel",
            "chat_id": 21,
            "sender": "Channel",
            "sender_id": 21,
            "message_id": 210,
            "mentions": ["@Alpha"],
            "link": "https://t.me/prize/210",
            "text": "Победитель @Alpha",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_win": True,
            "action_status": "claim_prize",
        })
        await database.upsert_giveaway_candidate({
            "ping_id": int(ping_id),
            "status": "manual_required",
            "score": 10,
            "reasons": ["captcha"],
            "required_channels": [],
            "join_buttons": [],
            "external_requirements": ["captcha_or_verification"],
            "blocked_reason": "Manual-only requirement: captcha_or_verification",
            "estimated_value": None,
        })

        board = await database.get_giveaway_board(limit=50)
        buckets = board["buckets"]
        self.assertIn(ping_id, [row["id"] for row in buckets["need_action"]])
        row = next(r for r in buckets["need_action"] if r["id"] == ping_id)
        self.assertEqual(row["workflow_stage"], "claim")
        self.assertEqual(row["workflow_hint"], "claim_prize")
        # and it is not double-listed as a suspicious candidate
        self.assertNotIn(ping_id, [r["id"] for r in buckets["suspicious"]])

    async def test_giveaway_board_totals_are_not_capped_by_limit(self):
        """`bucket_counts` follows the page limit; `bucket_totals` must not —
        the bot header would otherwise report 3 open prizes when there are 7."""
        for index in range(7):
            await database.save_ping({
                "date": "2026-05-07T10:00:00",
                "chat": "Prize Channel",
                "chat_id": 20,
                "sender": "Channel",
                "sender_id": 20,
                "message_id": 200 + index,
                "mentions": ["@Alpha"],
                "link": f"https://t.me/prize/{200 + index}",
                "text": f"Победитель @Alpha #{index}",
                "chat_type": "channel",
                "detected_at": "2026-05-07T10:01:00",
                "is_win": True,
                "action_status": "claim_prize",
            })

        board = await database.get_giveaway_board(limit=3)
        self.assertEqual(len(board["buckets"]["need_action"]), 3)
        self.assertEqual(board["bucket_counts"]["need_action"], 3)
        self.assertEqual(board["bucket_totals"]["need_action"], 7)

        full = await database.get_giveaway_board(limit=50)
        self.assertEqual(full["bucket_totals"]["need_action"], 7)
        self.assertEqual(full["bucket_counts"]["need_action"], 7)

    async def test_giveaway_board_sorts_by_message_date_on_request(self):
        """Same bucket rank, opposite orders: the scan found the older post last."""
        old_post = await database.save_ping({
            "date": "2026-05-01T09:00:00",       # published first…
            "chat": "Prize Channel", "chat_id": 30, "sender": "Channel", "sender_id": 30,
            "message_id": 301, "mentions": ["@Alpha"], "link": "https://t.me/prize/301",
            "text": "Победитель @Alpha (старый пост)", "chat_type": "channel",
            "detected_at": "2026-05-09T10:00:00",  # …but noticed last
            "is_win": True, "action_status": "claim_prize",
        })
        new_post = await database.save_ping({
            "date": "2026-05-08T09:00:00",
            "chat": "Prize Channel", "chat_id": 30, "sender": "Channel", "sender_id": 30,
            "message_id": 302, "mentions": ["@Alpha"], "link": "https://t.me/prize/302",
            "text": "Победитель @Alpha (свежий пост)", "chat_type": "channel",
            "detected_at": "2026-05-08T10:00:00",
            "is_win": True, "action_status": "claim_prize",
        })

        by_detected = await database.get_giveaway_board(limit=20)
        self.assertEqual(by_detected["sort"], "detected")
        self.assertEqual([r["id"] for r in by_detected["buckets"]["need_action"]][:2], [old_post, new_post])

        by_posted = await database.get_giveaway_board(limit=20, sort="posted")
        self.assertEqual(by_posted["sort"], "posted")
        self.assertEqual([r["id"] for r in by_posted["buckets"]["need_action"]][:2], [new_post, old_post])

    async def test_unknown_sort_falls_back_to_detected(self):
        board = await database.get_giveaway_board(limit=5, sort="nonsense")
        self.assertEqual(board["sort"], "detected")

    async def test_giveaway_account_counts_split_wins_from_giveaways(self):
        await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Prize Channel", "chat_id": 31, "sender": "Channel", "sender_id": 31,
            "message_id": 311, "mentions": ["@Alpha"], "link": "https://t.me/prize/311",
            "text": "Победитель @Alpha", "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00", "is_win": True, "action_status": "claim_prize",
        })
        await database.save_ping({
            "date": "2026-05-07T11:00:00",
            "chat": "Prize Channel", "chat_id": 31, "sender": "Channel", "sender_id": 31,
            "message_id": 312, "mentions": ["@Alpha", "@Beta"], "link": "https://t.me/prize/312",
            "text": "Конкурс для @Alpha и @Beta", "chat_type": "channel",
            "detected_at": "2026-05-07T11:01:00", "is_giveaway": True,
        })
        closed = await database.save_ping({
            "date": "2026-05-07T12:00:00",
            "chat": "Prize Channel", "chat_id": 31, "sender": "Channel", "sender_id": 31,
            "message_id": 313, "mentions": ["@Beta"], "link": "https://t.me/prize/313",
            "text": "Победитель @Beta", "chat_type": "channel",
            "detected_at": "2026-05-07T12:01:00", "is_win": True,
        })
        await database.update_ping_meta(int(closed), giveaway_status="claimed")

        counts = await database.giveaway_account_counts()
        self.assertEqual(counts["alpha"], {"wins": 1, "giveaways": 1, "total": 2})
        self.assertEqual(counts["beta"], {"wins": 0, "giveaways": 1, "total": 1})  # claimed one dropped

        everything = await database.giveaway_account_counts(open_only=False)
        self.assertEqual(everything["beta"]["wins"], 1)

    async def test_debt_board_groups_pending_prizes_by_tracked_username(self):
        alpha_id = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Prize Channel",
            "chat_id": 13,
            "sender": "Channel",
            "sender_id": 13,
            "message_id": 91,
            "mentions": ["@Alpha"],
            "link": "https://t.me/prize/91",
            "text": "Победитель @Alpha, приз ожидает выдачи",
            "chat_type": "channel",
            "is_win": True,
            "priority_score": 96,
            "priority_label": "critical",
            "action_status": "claim_prize",
        })
        beta_id = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Closed Prize Channel",
            "chat_id": 14,
            "sender": "Channel",
            "sender_id": 14,
            "message_id": 92,
            "mentions": ["@Beta"],
            "link": "https://t.me/prize/92",
            "text": "Победитель @Beta",
            "chat_type": "channel",
            "is_win": True,
            "action_status": "claim_prize",
        })
        await database.update_ping_meta(int(beta_id), giveaway_status="claimed", action_status="claimed")
        private_id = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Private Dialog",
            "chat_id": 15,
            "sender": "Friend",
            "sender_id": 150,
            "message_id": 93,
            "mentions": ["@Alpha"],
            "link": "",
            "text": "Winner @Alpha, but this is not a channel",
            "chat_type": "private",
            "is_win": True,
            "priority_score": 99,
            "priority_label": "critical",
            "action_status": "claim_prize",
        })

        group_id = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Giveaway Chat",
            "chat_id": 16,
            "sender": "Random Bot",
            "sender_id": 160,
            "message_id": 94,
            "mentions": ["@Alpha"],
            "link": "https://t.me/chat/94",
            "text": "Результаты розыгрыша. Победители: @Alpha",
            "chat_type": "group",
            "is_win": True,
            "priority_score": 50,
            "action_status": "claim_prize",
        })

        board = await database.get_debt_board(["Alpha", "Beta"])
        board_ids = [row["id"] for row in board["rows"]]
        self.assertEqual(board["stats"]["total"], 2)
        self.assertEqual(board["stats"]["critical"], 1)
        self.assertEqual(board["rows"][0]["id"], alpha_id)
        self.assertIn(group_id, board_ids)        # group results count as wins
        self.assertNotIn(private_id, board_ids)   # forwarded copies in DMs do not
        self.assertEqual(board["rows"][0]["giveaway_status"], "pending")
        alpha_profile = next(profile for profile in board["profiles"] if profile["username"] == "Alpha")
        beta_profile = next(profile for profile in board["profiles"] if profile["username"] == "Beta")
        self.assertEqual([row["id"] for row in alpha_profile["rows"]], [alpha_id, group_id])
        self.assertEqual(beta_profile["rows"], [])

    async def test_market_history_uses_fetched_at_iso(self):
        await database.save_market_snapshot({"bitcoin": {"usd": 100}, "fetched_at_iso": "2026-05-07T10:00:00"})
        rows = await database.get_market_history(limit=1)
        self.assertEqual(rows[0]["fetched_at_iso"], "2026-05-07T10:00:00")
        self.assertEqual(rows[0]["bitcoin"]["usd"], 100)

    async def test_checkpoint_roundtrip(self):
        self.assertEqual(await database.get_checkpoint("session", "Alpha"), 0)
        await database.save_checkpoint("session", "Alpha", 123)
        self.assertEqual(await database.get_checkpoint("session", "Alpha"), 123)

    async def test_checkpoint_batch_roundtrip(self):
        await database.save_checkpoints("session", {"Alpha": 123, "Beta": 456})
        rows = await database.get_checkpoints("session", ["Alpha", "Beta", "Gamma"])
        self.assertEqual(rows, {"Alpha": 123, "Beta": 456})

    async def test_latest_checkpoints_seed_new_sessions(self):
        await database.save_checkpoints("session-a", {"Alpha": 123})
        await database.save_checkpoints("session-b", {"Alpha": 456})
        rows = await database.get_latest_checkpoints(["Alpha", "Beta"])
        self.assertEqual(rows, {"Alpha": 456})

    async def test_app_events_and_settings_roundtrip(self):
        await database.record_event("warning", "test", "Something happened", {"id": 1})
        events = await database.get_events(limit=10, level="WARNING")
        self.assertEqual(events[0]["source"], "test")
        self.assertEqual(events[0]["context"]["id"], 1)
        await database.set_setting("notifications", {"enabled": False, "keywords": ["ton"]})
        self.assertEqual(await database.get_setting("notifications"), {"enabled": False, "keywords": ["ton"]})

    async def test_scan_run_progress_roundtrip(self):
        scan_id = await database.start_scan_run(total_accounts=2, total_usernames=6)
        await database.update_scan_run(scan_id, processed_accounts=1, processed_usernames=3, found=4, status="running")
        latest = await database.get_latest_scan_run()
        self.assertEqual(latest["id"], scan_id)
        self.assertEqual(latest["processed_usernames"], 3)
        self.assertEqual(latest["found"], 4)

    async def test_giveaway_ping_lands_in_task_overview(self):
        ping_id = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Giveaway Channel",
            "chat_id": 100,
            "sender": "Channel",
            "sender_id": 100,
            "message_id": 101,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/101",
            "text": "hello @Alpha",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": True,
            "is_win": False,
            "action_status": "waiting_result",
        })
        rows = await database.get_pings(chat_type="giveaway", action_status="waiting_result")
        self.assertTrue(any(row["id"] == ping_id for row in rows))
        tasks = await database.get_task_overview()
        self.assertTrue(any(row["id"] == ping_id for row in tasks["all_open"]))
        self.assertTrue(any(row["id"] == ping_id for row in tasks["waiting_result"]))

    async def test_channel_profile_and_source_scores(self):
        await database.upsert_channel_profile(200, "Source Channel", "source", "Итоги 11 мая")
        profile = await database.get_channel_profile(200)
        self.assertEqual(profile["description"], "Итоги 11 мая")
        await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Source Channel",
            "chat_id": 200,
            "sender": "Channel",
            "sender_id": 200,
            "message_id": 201,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/201",
            "text": "winner @Alpha",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": True,
            "is_win": True,
            "priority_score": 90,
            "action_status": "claim_prize",
        })
        await database.recalculate_source_scores()
        sources = await database.get_source_scores()
        self.assertEqual(sources[0]["chat_id"], 200)

    async def test_result_post_becomes_a_claimable_win(self):
        ping_id = await database.save_ping({
            "date": "2026-05-10T12:00:00",
            "chat": "Result Channel",
            "chat_id": 304,
            "sender": "Channel",
            "sender_id": 304,
            "message_id": 304,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/304",
            "text": "🎉 Результаты розыгрыша:\nПобедители, у вас есть сутки, чтобы получить приз",
            "chat_type": "channel",
            "detected_at": "2026-05-11T10:01:00",
            "is_giveaway": True,
            "is_win": False,
            "action_status": "waiting_result",
        })
        await database.reconcile_giveaway_outcomes()
        row = await database.get_ping_by_id(ping_id)
        self.assertEqual(row["is_win"], 1)
        self.assertEqual(row["action_status"], "claim_prize")

    async def test_reconcile_giveaways_requires_channel_and_keyword(self):
        stale_channel = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Plain Channel",
            "chat_id": 301,
            "sender": "Channel",
            "sender_id": 301,
            "message_id": 301,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/301",
            "text": "Обычная новость без нужных слов",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": True,
            "is_win": False,
        })
        valid_channel = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Giveaway Channel",
            "chat_id": 302,
            "sender": "Channel",
            "sender_id": 302,
            "message_id": 302,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/302",
            "text": "Конкурс для подписчиков, итоги завтра",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": False,
            "is_win": False,
        })
        private_keyword = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "DM",
            "chat_id": 303,
            "sender": "Alice",
            "sender_id": 303,
            "message_id": 303,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/303",
            "text": "розыгрыш в личке не должен стать задачей",
            "chat_type": "private",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": True,
            "is_win": False,
        })
        result = await database.reconcile_giveaway_flags(["конкурс", "розыгрыш", "итоги"])
        self.assertEqual(result["enabled"], 1)
        self.assertEqual(result["disabled"], 2)
        rows = await database.get_pings(limit=10, chat_type="giveaway")
        self.assertEqual([row["id"] for row in rows], [valid_channel])
        stale = await database.get_ping_by_id(stale_channel)
        private = await database.get_ping_by_id(private_keyword)
        self.assertEqual(stale["is_giveaway"], 0)
        self.assertEqual(private["is_giveaway"], 0)

    async def test_reconcile_win_flags_requires_mentions(self):
        no_mention = await database.save_ping({
            "date": "2026-07-04T16:37:00",
            "chat": "Foreign Wins Channel",
            "chat_id": 401,
            "sender": "Channel",
            "sender_id": 401,
            "message_id": 401,
            "mentions": [],
            "link": "https://t.me/test/401",
            "text": "Чек на сумму 3 USDT, заберите приз",
            "chat_type": "channel",
            "detected_at": "2026-07-04T16:37:32",
            "is_giveaway": False,
            "is_win": False,
        })
        mentioned = await database.save_ping({
            "date": "2026-07-04T16:38:00",
            "chat": "My Win Channel",
            "chat_id": 402,
            "sender": "Channel",
            "sender_id": 402,
            "message_id": 402,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/402",
            "text": "@Alpha, заберите приз",
            "chat_type": "channel",
            "detected_at": "2026-07-04T16:38:32",
            "is_giveaway": False,
            "is_win": False,
        })
        stale_win = await database.save_ping({
            "date": "2026-07-04T16:39:00",
            "chat": "Foreign Wins Channel",
            "chat_id": 401,
            "sender": "Channel",
            "sender_id": 401,
            "message_id": 403,
            "mentions": [],
            "link": "https://t.me/test/403",
            "text": "Заберите приз, победители!",
            "chat_type": "channel",
            "detected_at": "2026-07-04T22:34:49",
            "is_giveaway": False,
            "is_win": True,
            "priority_score": 90,
            "action_status": "claim_prize",
        })
        result = await database.reconcile_win_flags(["приз"])
        self.assertEqual(result["enabled"], 1)
        self.assertEqual(result["disabled"], 1)
        self.assertEqual((await database.get_ping_by_id(no_mention))["is_win"], 0)
        flagged = await database.get_ping_by_id(mentioned)
        self.assertEqual(flagged["is_win"], 1)
        self.assertEqual(flagged["action_status"], "claim_prize")
        healed = await database.get_ping_by_id(stale_win)
        self.assertEqual(healed["is_win"], 0)
        self.assertEqual(healed["priority_score"], 55)

    async def test_reconcile_giveaway_flags_requires_mentions(self):
        no_mention = await database.save_ping({
            "date": "2026-07-04T16:37:00",
            "chat": "Foreign Giveaway Channel",
            "chat_id": 405,
            "sender": "Channel",
            "sender_id": 405,
            "message_id": 405,
            "mentions": [],
            "link": "https://t.me/test/405",
            "text": "Розыгрыш для подписчиков, итоги завтра",
            "chat_type": "channel",
            "detected_at": "2026-07-04T16:37:32",
            "is_giveaway": False,
            "is_win": False,
        })
        stale_giveaway = await database.save_ping({
            "date": "2026-07-04T16:38:00",
            "chat": "Foreign Giveaway Channel",
            "chat_id": 405,
            "sender": "Channel",
            "sender_id": 405,
            "message_id": 406,
            "mentions": [],
            "link": "https://t.me/test/406",
            "text": "Розыгрыш для подписчиков, итоги завтра",
            "chat_type": "channel",
            "detected_at": "2026-07-04T22:34:49",
            "is_giveaway": True,
            "is_win": False,
            "giveaway_status": "pending",
            "action_status": "waiting_result",
        })
        mentioned = await database.save_ping({
            "date": "2026-07-04T16:39:00",
            "chat": "My Giveaway Channel",
            "chat_id": 407,
            "sender": "Channel",
            "sender_id": 407,
            "message_id": 407,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/407",
            "text": "Розыгрыш для подписчиков, итоги завтра",
            "chat_type": "channel",
            "detected_at": "2026-07-04T16:39:32",
            "is_giveaway": False,
            "is_win": False,
        })
        result = await database.reconcile_giveaway_flags(["розыгрыш"])
        self.assertEqual(result["enabled"], 1)
        self.assertEqual(result["disabled"], 1)
        self.assertEqual((await database.get_ping_by_id(no_mention))["is_giveaway"], 0)
        healed = await database.get_ping_by_id(stale_giveaway)
        self.assertEqual(healed["is_giveaway"], 0)
        self.assertEqual(healed["giveaway_status"], "")
        flagged = await database.get_ping_by_id(mentioned)
        self.assertEqual(flagged["is_giveaway"], 1)
        self.assertEqual(flagged["giveaway_status"], "pending")

    async def test_reconcile_outcomes_require_mentions(self):
        foreign = await database.save_ping({
            "date": "2026-07-04T16:37:00",
            "chat": "Foreign Results Channel",
            "chat_id": 408,
            "sender": "Channel",
            "sender_id": 408,
            "message_id": 408,
            "mentions": [],
            "link": "https://t.me/test/408",
            "text": "🎉 Результаты розыгрыша:\nПобедители, у вас есть сутки, чтобы получить приз",
            "chat_type": "channel",
            "detected_at": "2026-07-04T16:37:32",
            "is_giveaway": True,
            "is_win": False,
            "action_status": "waiting_result",
        })
        result = await database.reconcile_giveaway_outcomes()
        self.assertEqual(result["marked"], 0)
        row = await database.get_ping_by_id(foreign)
        self.assertEqual(row["is_win"], 0)
        self.assertEqual(row["action_status"], "waiting_result")

    async def _win_row(self, message_id: int, **over):
        record = {
            "date": "2026-09-10T12:00:00", "chat": "Results", "chat_id": 900, "sender": "Channel",
            "sender_id": 900, "message_id": message_id, "mentions": ["@Alpha"],
            "link": f"https://t.me/test/{message_id}", "text": "Итоги розыгрыша. Победители: @Alpha",
            "chat_type": "channel", "detected_at": "2026-09-10T12:01:00", "is_giveaway": True,
            "is_win": True, "action_status": "claim_prize", "priority_score": 95,
        }
        record.update(over)
        return await database.save_ping(record)

    async def test_startup_reconciles_are_idempotent(self):
        await self._win_row(910)
        await database.reconcile_win_flags(["итоги"])
        await database.reconcile_giveaway_flags(["розыгрыш"])
        await database.reconcile_giveaway_outcomes()
        # A second start must find nothing to do: the outcome pass used to
        # rewrite and "mark" every result post on every start.
        self.assertEqual(await database.reconcile_win_flags(["итоги"]), {"enabled": 0, "disabled": 0})
        self.assertEqual(await database.reconcile_giveaway_flags(["розыгрыш"]), {"enabled": 0, "disabled": 0})
        self.assertEqual((await database.reconcile_giveaway_outcomes())["marked"], 0)

    async def test_channel_win_keeps_giveaway_flag_and_claimed_status(self):
        ping_id = await self._win_row(911, text="Поздравляем! Победитель: @Alpha")
        await database.update_ping_meta(ping_id, giveaway_status="claimed", action_status="claimed")
        # No giveaway keyword in the text: the old pass cleared is_giveaway and
        # wiped giveaway_status='claimed' on every start.
        result = await database.reconcile_giveaway_flags(["розыгрыш"])
        self.assertEqual(result["disabled"], 0)
        row = await database.get_ping_by_id(ping_id)
        self.assertEqual((row["is_giveaway"], row["giveaway_status"]), (1, "claimed"))

    async def test_outcome_pass_keeps_owner_decisions(self):
        ping_id = await self._win_row(912, priority_score=100)
        await database.update_ping_meta(ping_id, giveaway_status="", action_status="scam")
        await database.reconcile_giveaway_outcomes()
        row = await database.get_ping_by_id(ping_id)
        self.assertEqual((row["giveaway_status"], row["action_status"]), ("", "scam"))

    async def test_rescan_and_startup_agree_on_a_result_post_priority(self):
        from pulse_desk import ping_pipeline

        saved = ping_pipeline.state.high_priority_keywords
        ping_pipeline.state.high_priority_keywords = []
        self.addCleanup(setattr, ping_pipeline.state, "high_priority_keywords", saved)
        # What a sweep stores: a channel win whose text misses the giveaway rule.
        record = {"text": "Итоги розыгрыша. Победители: @Alpha", "chat": "Results", "chat_type": "channel",
                  "mentions": ["@Alpha"], "is_win": True, "is_giveaway": False}
        self.assertEqual(ping_pipeline.apply_priority(dict(record))["priority_score"], 90)
        ping_id = await self._win_row(916, priority_score=ping_pipeline.apply_priority(dict(record))["priority_score"])
        await database.reconcile_giveaway_outcomes()
        # The next sweep re-saves it; the next start must have nothing to fix.
        await self._win_row(916, priority_score=ping_pipeline.apply_priority(dict(record))["priority_score"])
        self.assertEqual((await database.reconcile_giveaway_outcomes())["marked"], 0)
        self.assertEqual((await database.get_ping_by_id(ping_id))["priority_score"], 90)

    async def test_wins_not_judged_by_text_are_not_unwon(self):
        card = await self._win_row(
            913, text="🃏 Карточка mini-app: её содержимое видно только в самом Telegram.\nПоиск Telegram нашёл в ней @Alpha")
        manual = await self._win_row(914, text="✍️ Добавлено вручную: https://t.me/test/914", note="✍️ Добавлено вручную")
        plain = await self._win_row(915, text="Спасибо за участие, @Alpha")
        result = await database.reconcile_win_flags(["победител", "итоги"])
        self.assertEqual(result["disabled"], 1)
        self.assertEqual((await database.get_ping_by_id(card))["is_win"], 1)
        self.assertEqual((await database.get_ping_by_id(manual))["is_win"], 1)
        self.assertEqual((await database.get_ping_by_id(plain))["is_win"], 0)

    async def test_giveaway_candidate_and_action_roundtrip(self):
        ping_id = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Queue Channel",
            "chat_id": 400,
            "sender": "Channel",
            "sender_id": 400,
            "message_id": 401,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/401",
            "text": "giveaway @Alpha",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": True,
            "is_win": False,
        })
        await database.upsert_giveaway_candidate({
            "ping_id": ping_id,
            "status": "recommended",
            "score": 77,
            "reasons": ["small channel"],
            "required_channels": [{"username": "small", "subscribers": 100}],
            "join_buttons": ["Join"],
            "external_requirements": [],
            "blocked_reason": "",
            "estimated_value": 50,
        })
        await database.record_giveaway_action(ping_id, "confirm", "confirmed", "test", context={"button": "Join"})
        candidates = await database.get_giveaway_candidates(status="recommended")
        self.assertEqual(candidates[0]["score"], 77)
        self.assertEqual(candidates[0]["required_channels"][0]["username"], "small")
        actions = await database.get_giveaway_actions(ping_id)
        self.assertEqual(actions[0]["context"]["button"], "Join")

    async def test_recent_giveaway_actions_across_pings_with_chat(self):
        ping_id = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Recent Channel",
            "chat_id": 420,
            "sender": "Channel",
            "sender_id": 420,
            "message_id": 421,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/421",
            "text": "giveaway @Alpha",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": True,
            "is_win": False,
        })
        await database.record_giveaway_action(ping_id, "skip", "skipped", "telegram_bot")
        await database.record_giveaway_action(ping_id, "confirm", "confirmed", "telegram_bot")
        recent = await database.get_recent_giveaway_actions(limit=5)
        self.assertGreaterEqual(len(recent), 2)
        # Newest first, with the joined chat label exposed for display.
        self.assertEqual(recent[0]["action"], "confirm")
        self.assertEqual(recent[0]["chat"], "Recent Channel")

    async def test_giveaway_action_analyze_is_deduplicated(self):
        ping_id = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Dedup Channel",
            "chat_id": 410,
            "sender": "Channel",
            "sender_id": 410,
            "message_id": 411,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/411",
            "text": "конкурс @Alpha итоги завтра",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": True,
            "is_win": False,
        })
        first = await database.record_giveaway_action(ping_id, "analyze", "pending_review", "system", context={"score": 10})
        second = await database.record_giveaway_action(ping_id, "analyze", "pending_review", "system", context={"score": 10})
        third = await database.record_giveaway_action(ping_id, "analyze", "pending_review", "system", context={"score": 20})
        self.assertEqual(first, second)
        self.assertNotEqual(second, third)
        actions = await database.get_giveaway_actions(ping_id, limit=10)
        self.assertEqual(len(actions), 2)

    async def test_cleanup_outbox_keeps_recent_limit(self):
        for index in range(5):
            await database.enqueue_outbox_event("ping", {"index": index})
        await database.cleanup_outbox(days=30, max_events=2)
        rows = await database.get_outbox_after(0, limit=10)
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["payload"]["index"] for row in rows], [3, 4])
        stats = await database.get_outbox_stats()
        self.assertEqual(stats["total"], 2)
        self.assertIn(stats["pressure"], {"ok", "high"})

    async def test_interrupt_stale_scan_runs(self):
        scan_id = await database.start_scan_run(total_accounts=1, total_usernames=1)
        interrupted = await database.interrupt_stale_scan_runs()
        self.assertEqual([row["id"] for row in interrupted], [scan_id])
        latest = await database.get_latest_scan_run()
        self.assertEqual(latest["status"], "interrupted")
        self.assertIn("Application restarted", latest["last_error"])
        health = await database.get_scan_run_health()
        self.assertFalse(health["running"])
        self.assertTrue(health["recent_interrupted"])

    async def test_giveaway_board_groups_core_buckets(self):
        waiting_id = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Waiting Channel",
            "chat_id": 420,
            "sender": "Channel",
            "sender_id": 420,
            "message_id": 421,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/421",
            "text": "конкурс @Alpha итоги завтра",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": True,
            "is_win": False,
            "action_status": "waiting_result",
        })
        need_action_id = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Need Action Channel",
            "chat_id": 421,
            "sender": "Channel",
            "sender_id": 421,
            "message_id": 422,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/422",
            "text": "розыгрыш @Alpha",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": True,
            "is_win": False,
            "action_status": "to_check",
        })
        suspicious_id = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Manual Channel",
            "chat_id": 422,
            "sender": "Channel",
            "sender_id": 422,
            "message_id": 423,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/423",
            "text": "розыгрыш @Alpha captcha",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": True,
            "is_win": False,
        })
        await database.upsert_giveaway_candidate({
            "ping_id": suspicious_id,
            "status": "manual_required",
            "score": 20,
            "reasons": ["external requirement"],
            "required_channels": [],
            "join_buttons": [],
            "external_requirements": ["captcha_or_verification"],
            "blocked_reason": "captcha_or_verification",
            "estimated_value": None,
        })
        board = await database.get_giveaway_board(limit=20)
        self.assertIn(waiting_id, [row["id"] for row in board["buckets"]["waiting_result"]])
        self.assertIn(need_action_id, [row["id"] for row in board["buckets"]["need_action"]])
        self.assertIn(suspicious_id, [row["id"] for row in board["buckets"]["suspicious"]])
        self.assertGreaterEqual(board["stats"]["total"], 3)


class BotAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if database is None:
            self.skipTest("aiosqlite is not installed in this Python environment")
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "pulse_test.db"
        await database.init_db()

    async def asyncTearDown(self):
        database.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    async def test_create_and_lookup_key(self):
        key = await database.create_bot_key("friend", "secret-abc-123456", "viewer", None)
        self.assertEqual(key["label"], "friend")
        found = await database.get_bot_key_by_secret("secret-abc-123456")
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], key["id"])
        self.assertIsNone(await database.get_bot_key_by_secret("nope"))

    async def test_revoked_and_expired_keys_are_rejected(self):
        revoked = await database.create_bot_key("r", "rev-secret-000000", "viewer", None)
        await database.revoke_bot_key(revoked["id"])
        self.assertIsNone(await database.get_bot_key_by_secret("rev-secret-000000"))

        await database.create_bot_key("e", "exp-secret-000000", "viewer", "2000-01-01T00:00:00")
        self.assertIsNone(await database.get_bot_key_by_secret("exp-secret-000000"))

    async def test_redeem_upserts_member_without_duplicates(self):
        key = await database.create_bot_key("team", "team-secret-123456", "viewer", None)
        await database.upsert_bot_member(555, "vasya", "Вася", key["id"], "viewer")
        await database.upsert_bot_member(555, "vasya", "Вася П.", key["id"], "viewer")
        member = await database.get_bot_member(555)
        self.assertEqual(member["role"], "viewer")
        self.assertEqual(member["name"], "Вася П.")
        self.assertFalse(member["blocked"])
        self.assertEqual(len(await database.list_bot_members()), 1)
        keys = await database.list_bot_keys()
        self.assertEqual(keys[0]["member_count"], 1)

    async def test_key_edits_after_creation(self):
        key = await database.create_bot_key("draft", "edit-secret-123456", "viewer", None)
        await database.set_bot_key_label(key["id"], "  друзья  ")
        await database.set_bot_key_role(key["id"], "premium")
        await database.set_bot_key_expiry(key["id"], "2999-01-01T00:00:00")
        updated = await database.get_bot_key(key["id"])
        self.assertEqual(updated["label"], "друзья")
        self.assertEqual(updated["role"], "premium")
        self.assertEqual(updated["expires_at"], "2999-01-01T00:00:00")
        self.assertIsNotNone(await database.get_bot_key_by_secret("edit-secret-123456"))

        # Clearing the expiry makes the key permanent again.
        await database.set_bot_key_expiry(key["id"], None)
        self.assertIsNone((await database.get_bot_key(key["id"]))["expires_at"])

    async def test_revoke_can_be_undone(self):
        key = await database.create_bot_key("back", "undo-secret-123456", "viewer", None)
        await database.set_bot_key_revoked(key["id"], True)
        self.assertIsNone(await database.get_bot_key_by_secret("undo-secret-123456"))
        self.assertEqual(await database.list_bot_keys(), [])
        self.assertEqual(len(await database.list_bot_keys(include_revoked=True)), 1)

        await database.set_bot_key_revoked(key["id"], False)
        self.assertIsNotNone(await database.get_bot_key_by_secret("undo-secret-123456"))

    async def test_list_key_members_only_returns_that_keys_holders(self):
        mine = await database.create_bot_key("mine", "mine-secret-123456", "viewer", None)
        other = await database.create_bot_key("other", "othr-secret-123456", "viewer", None)
        await database.upsert_bot_member(11, "a", "A", mine["id"], "viewer")
        await database.upsert_bot_member(22, "b", "B", other["id"], "viewer")

        holders = await database.list_bot_key_members(mine["id"])
        self.assertEqual([m["tg_id"] for m in holders], [11])

    async def test_key_delay_reaches_its_holders_but_other_grants_do_not(self):
        from pulse_desk.bot.sections.keys import save_permissions
        from pulse_desk.bot_permissions import parse_permissions, set_delay, set_features

        key = await database.create_bot_key("late", "late-secret-123456", "viewer", None,
                                            '{"features": ["stats"], "delay_minutes": 1}')
        other = await database.create_bot_key("keep", "keep-secret-123456", "viewer", None,
                                              '{"delay_minutes": 5}')
        await database.upsert_bot_member(31, "a", "A", key["id"], "viewer", '{"features": ["stats"], "delay_minutes": 1}')
        await database.upsert_bot_member(32, "b", "B", other["id"], "viewer", '{"delay_minutes": 5}')

        grants = set_features(set_delay(parse_permissions(key["permissions"]), 0), ["stats", "market"])
        await save_permissions(key["id"], grants)

        holder = parse_permissions((await database.get_bot_member(31))["permissions"])
        self.assertEqual(holder["delay_minutes"], 0)
        # The snapshot still guards the menu: only the delay is carried over.
        self.assertEqual(holder["features"], ["stats"])
        stranger = parse_permissions((await database.get_bot_member(32))["permissions"])
        self.assertEqual(stranger["delay_minutes"], 5)

    async def test_delete_key_removes_it_but_keeps_the_member(self):
        key = await database.create_bot_key("gone", "del-secret-1234567", "viewer", None, '{"features": ["stats"]}')
        await database.upsert_bot_member(901, "u", "U", key["id"], "viewer", '{"features": ["stats"]}')

        deleted = await database.delete_bot_key(key["id"])
        self.assertEqual(deleted["label"], "gone")
        self.assertEqual(deleted["member_count"], 1)

        self.assertIsNone(await database.get_bot_key_by_secret("del-secret-1234567"))
        self.assertEqual(await database.list_bot_keys(include_revoked=True), [])
        # The member survives with their grants; only the key link is cleared.
        member = await database.get_bot_member(901)
        self.assertIsNotNone(member)
        self.assertFalse(member["blocked"])
        self.assertIsNone(member["key_id"])
        self.assertIn("stats", member["permissions"])

    async def test_delete_missing_key_is_a_no_op(self):
        self.assertIsNone(await database.delete_bot_key(4242))

    async def test_block_member(self):
        key = await database.create_bot_key("", "blk-secret-1234567", "viewer", None)
        await database.upsert_bot_member(777, "x", "X", key["id"], "viewer")
        await database.set_bot_member_blocked(777, True)
        self.assertTrue((await database.get_bot_member(777))["blocked"])
        await database.set_bot_member_blocked(777, False)
        self.assertFalse((await database.get_bot_member(777))["blocked"])

    async def test_broadcast_messages_roundtrip(self):
        await database.save_broadcast_messages("abc123", [(111, 10), (222, 20)])
        rows = await database.get_broadcast_messages("abc123")
        self.assertEqual(len(rows), 2)
        self.assertEqual({(r["tg_id"], r["message_id"]) for r in rows}, {(111, 10), (222, 20)})
        self.assertEqual(await database.get_broadcast_messages("missing"), [])

        await database.delete_broadcast_messages("abc123")
        self.assertEqual(await database.get_broadcast_messages("abc123"), [])

    async def test_broadcast_messages_save_empty_is_noop(self):
        await database.save_broadcast_messages("empty", [])
        self.assertEqual(await database.get_broadcast_messages("empty"), [])

    async def test_prune_broadcast_messages(self):
        await database.save_broadcast_messages("fresh", [(111, 10)])
        import aiosqlite as _aiosqlite

        async with _aiosqlite.connect(database.DB_PATH) as db:
            await db.execute(
                "INSERT INTO bot_broadcast_messages (token, tg_id, message_id, created_at) VALUES (?, ?, ?, ?)",
                ("stale", 333, 30, "2000-01-01T00:00:00"),
            )
            await db.commit()
        pruned = await database.prune_broadcast_messages(days=7)
        self.assertEqual(pruned, 1)
        self.assertEqual(await database.get_broadcast_messages("stale"), [])
        self.assertEqual(len(await database.get_broadcast_messages("fresh")), 1)


class MonitoringRestartTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_disconnects_all_then_starts_discovered_sessions(self):
        import pulse_desk.telegram_accounts as ta
        from pulse_desk.runtime import AppState

        st = AppState()
        st.clients = [SimpleNamespace(_session_name_custom="a"), SimpleNamespace(_session_name_custom="b")]
        disconnected: list[str] = []
        started: list[str] = []

        async def fake_disconnect(name):
            disconnected.append(name)
            st.clients[:] = [c for c in st.clients if getattr(c, "_session_name_custom", "") != name]
            return True

        def fake_bg(name, coro):
            started.append(name)
            coro.close()  # don't actually connect to Telegram

        async def fake_event(level, source, message, context=None):
            return None

        originals = (ta.state, ta.disconnect_account, ta.start_background_task, ta.settings, ta.record_app_event)
        ta.state = st
        ta.disconnect_account = fake_disconnect
        ta.start_background_task = fake_bg
        ta.settings = SimpleNamespace(discover_sessions=lambda: ["a", "b", "c"])
        ta.record_app_event = fake_event
        try:
            result = await ta.restart_monitoring()
        finally:
            (ta.state, ta.disconnect_account, ta.start_background_task, ta.settings, ta.record_app_event) = originals

        # Every connected client is disconnected first...
        self.assertEqual(sorted(disconnected), ["a", "b"])
        self.assertEqual(st.clients, [])
        # ...then every freshly discovered session is (re)started.
        self.assertEqual(started, ["telegram-start:a", "telegram-start:b", "telegram-start:c"])
        self.assertEqual(result["restarted"], 3)


if __name__ == "__main__":
    unittest.main()
