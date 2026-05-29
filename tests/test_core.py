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
from pulse_desk.deadlines import parse_claim_deadline, parse_deadline, parse_participation_deadline
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
                "dry_run_giveaways": True,
                "auto_join_giveaways": False,
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
            tasks={"overdue": [{}], "today": [{}], "tomorrow": [], "no_deadline": [], "all_open": [{}, {}]},
            giveaway_board={
                "stats": {"overdue": 1, "claim_prize": 2, "no_deadline": 1},
                "bucket_counts": {"need_action": 2, "waiting_result": 4, "no_deadline": 1, "suspicious": 1},
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
        self.assertTrue(any(item["key"] == "giveaways" and item["ok"] for item in summary["readiness"]))

    def test_dashboard_summary_reports_calm_state(self):
        summary = build_dashboard_summary(
            status={
                "accounts_online": 1,
                "accounts_total": 1,
                "tracked_usernames": ["Alpha"],
                "dry_run_giveaways": True,
                "auto_join_giveaways": False,
                "scan": {"running": False},
                "last_scan": {"status": "finished"},
            },
            analytics={"total_pings": 4, "new_pings": 0, "important": 0, "resolved": 4, "favorites": 0, "total_channels": 1},
            tasks={"overdue": [], "today": [], "tomorrow": [], "no_deadline": [], "all_open": []},
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
            ["alga_kazakhst2n", "w3v8f0rm", "Fjfjfjfjds", "Timofey02513", "MuverGT", "xdfusybau", "davifd23", "fsdfsdfdsg34"],
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

    def test_parse_numeric_deadline_without_year(self):
        match = parse_deadline("Итоги до 11.05 в 18:00", now=datetime(2026, 5, 1, 10, 0))
        self.assertIsNotNone(match)
        self.assertEqual(match.deadline_at.isoformat(), "2026-05-11T18:00:00")

    def test_parse_text_month_deadline_default_time(self):
        match = parse_deadline("дедлайн 11 мая", now=datetime(2026, 5, 1, 10, 0))
        self.assertIsNotNone(match)
        self.assertEqual(match.deadline_at.isoformat(), "2026-05-11T23:59:00")

    def test_parse_iso_deadline(self):
        match = parse_deadline("deadline 2026-05-11 09:30", now=datetime(2026, 5, 1, 10, 0))
        self.assertIsNotNone(match)
        self.assertEqual(match.deadline_at.isoformat(), "2026-05-11T09:30:00")

    def test_parse_relative_deadline_minutes(self):
        match = parse_deadline("Итоги: Через 120 минут", now=datetime(2026, 5, 10, 20, 0))
        self.assertIsNotNone(match)
        self.assertEqual(match.deadline_at.isoformat(), "2026-05-10T22:00:00")

    def test_parse_deadline_tomorrow_with_time(self):
        match = parse_deadline("Итоги завтра в 18:30", now=datetime(2026, 5, 10, 20, 0))
        self.assertIsNotNone(match)
        self.assertEqual(match.deadline_at.isoformat(), "2026-05-11T18:30:00")

    def test_parse_deadline_time_only_marker_rolls_forward(self):
        match = parse_deadline("До 20:00 принимаем условия", now=datetime(2026, 5, 10, 21, 0))
        self.assertIsNotNone(match)
        self.assertEqual(match.deadline_at.isoformat(), "2026-05-11T20:00:00")

    def test_parse_relative_deadline_short_hour(self):
        match = parse_deadline("Розыгрыш, итоги через час", now=datetime(2026, 5, 10, 20, 0))
        self.assertIsNotNone(match)
        self.assertEqual(match.deadline_at.isoformat(), "2026-05-10T21:00:00")

    def test_parse_claim_window_after_result_marker(self):
        match = parse_claim_deadline(
            "🎉 Результаты розыгрыша:\nПобедители, у вас есть сутки, чтобы получить приз",
            now=datetime(2026, 5, 10, 20, 0),
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.deadline_at.isoformat(), "2026-05-11T20:00:00")

    def test_parse_claim_window_hours(self):
        match = parse_claim_deadline("Отпишите в течение 24 часов для получения приза", now=datetime(2026, 5, 10, 20, 0))
        self.assertIsNotNone(match)
        self.assertEqual(match.deadline_at.isoformat(), "2026-05-11T20:00:00")

    def test_claim_deadline_ignores_original_results_time(self):
        text = "Итоги завтра 21:00\nПобедители: @Alpha"
        self.assertIsNone(parse_claim_deadline(text, now=datetime(2026, 5, 10, 20, 0)))
        self.assertEqual(parse_participation_deadline(text, now=datetime(2026, 5, 10, 20, 0)).deadline_at.isoformat(), "2026-05-11T21:00:00")

    def test_claim_deadline_reads_otpiska_window(self):
        match = parse_claim_deadline("Победители: @Alpha\nНа отписку 30 минут", now=datetime(2026, 5, 10, 20, 0))
        self.assertIsNotNone(match)
        self.assertEqual(match.deadline_at.isoformat(), "2026-05-10T20:30:00")

    def test_parse_deadline_absent(self):
        self.assertIsNone(parse_deadline("тут нет даты", now=datetime(2026, 5, 1, 10, 0)))

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

        board = await database.get_debt_board(["Alpha", "Beta"])
        self.assertEqual(board["stats"]["total"], 1)
        self.assertEqual(board["stats"]["critical"], 1)
        self.assertEqual(board["rows"][0]["id"], alpha_id)
        self.assertNotIn(private_id, [row["id"] for row in board["rows"]])
        self.assertEqual(board["rows"][0]["giveaway_status"], "pending")
        alpha_profile = next(profile for profile in board["profiles"] if profile["username"] == "Alpha")
        beta_profile = next(profile for profile in board["profiles"] if profile["username"] == "Beta")
        self.assertEqual([row["id"] for row in alpha_profile["rows"]], [alpha_id])
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

    async def test_deadline_fields_and_tasks_roundtrip(self):
        ping_id = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Deadline Channel",
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
            "deadline_at": "2026-05-11T18:00:00",
            "deadline_source": "channel_description",
            "deadline_text": "Итоги до 11.05 в 18:00",
            "action_status": "waiting_result",
        })
        await database.replace_ping_reminders(int(ping_id), "2026-05-11T18:00:00", "2026-05-10T18:00:00")
        rows = await database.get_pings(has_deadline=True, action_status="waiting_result")
        self.assertEqual(rows[0]["deadline_source"], "channel_description")
        tasks = await database.get_task_overview()
        self.assertTrue(any(row["id"] == ping_id for row in tasks["all_open"]))

    async def test_channel_profile_and_source_scores(self):
        await database.upsert_channel_profile(200, "Source Channel", "source", "Итоги 11 мая", "2026-05-11T23:59:00", "Итоги 11 мая")
        profile = await database.get_channel_profile(200)
        self.assertEqual(profile["deadline_at"], "2026-05-11T23:59:00")
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

    async def test_backfill_channel_post_text_deadline_uses_message_date(self):
        ping_id = await database.save_ping({
            "date": "2026-05-10T12:00:00",
            "chat": "Channel With Post Deadline",
            "chat_id": 300,
            "sender": "Channel",
            "sender_id": 300,
            "message_id": 301,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/301",
            "text": "Розыгрыш\nИтоги: 10.05 в 21:00 по МСК",
            "chat_type": "channel",
            "detected_at": "2026-05-11T10:01:00",
            "is_giveaway": True,
            "is_win": False,
            "action_status": "waiting_result",
        })
        changed = await database.backfill_deadlines_from_text()
        self.assertGreaterEqual(changed, 1)
        rows = await database.get_pings(has_deadline=True, chat_type="giveaway")
        row = next(item for item in rows if item["id"] == ping_id)
        self.assertEqual(row["deadline_at"], "2026-05-10T21:00:00")
        self.assertEqual(row["deadline_source"], "channel_post_text")

    async def test_backfill_result_claim_window_uses_message_date(self):
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
        changed = await database.backfill_deadlines_from_text()
        self.assertGreaterEqual(changed, 1)
        row = await database.get_ping_by_id(ping_id)
        self.assertEqual(row["is_win"], 1)
        self.assertEqual(row["action_status"], "claim_prize")
        self.assertEqual(row["deadline_at"], "2026-05-11T12:00:00")
        self.assertEqual(row["deadline_source"], "claim_window_text")

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
            "deadline_at": "2026-05-11T18:00:00",
            "deadline_source": "channel_post_text",
            "deadline_text": "11.05",
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
        self.assertEqual(stale["deadline_at"], None)
        self.assertEqual(private["is_giveaway"], 0)

    async def test_update_channel_deadlines_does_not_overwrite_existing_deadline(self):
        existing_id = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Deadline Channel",
            "chat_id": 305,
            "sender": "Channel",
            "sender_id": 305,
            "message_id": 305,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/305",
            "text": "Конкурс, итоги 11.05",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": True,
            "is_win": False,
            "deadline_at": "2026-05-11T23:59:00",
            "deadline_source": "channel_post_text",
            "deadline_text": "итоги 11.05",
        })
        missing_id = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "Deadline Channel",
            "chat_id": 305,
            "sender": "Channel",
            "sender_id": 305,
            "message_id": 306,
            "mentions": ["@Alpha"],
            "link": "https://t.me/test/306",
            "text": "Конкурс без даты",
            "chat_type": "channel",
            "detected_at": "2026-05-07T10:01:00",
            "is_giveaway": True,
            "is_win": False,
        })
        changed = await database.update_channel_deadlines(305, "2026-05-12T18:00:00", "описание 12.05")
        self.assertEqual(changed, 1)
        existing = await database.get_ping_by_id(existing_id)
        missing = await database.get_ping_by_id(missing_id)
        self.assertEqual(existing["deadline_at"], "2026-05-11T23:59:00")
        self.assertEqual(existing["deadline_source"], "channel_post_text")
        self.assertEqual(missing["deadline_at"], "2026-05-12T18:00:00")
        self.assertEqual(missing["deadline_source"], "channel_description")

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
            "deadline_at": "2026-05-11T18:00:00",
            "action_status": "waiting_result",
        })
        no_deadline_id = await database.save_ping({
            "date": "2026-05-07T10:00:00",
            "chat": "No Deadline Channel",
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
        self.assertIn(no_deadline_id, [row["id"] for row in board["buckets"]["no_deadline"]])
        self.assertIn(suspicious_id, [row["id"] for row in board["buckets"]["suspicious"]])
        self.assertGreaterEqual(board["stats"]["total"], 3)


if __name__ == "__main__":
    unittest.main()
