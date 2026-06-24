from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.bot_prefs import (
    DEFAULT_MEMBER_PREFS,
    KEYWORD_SCOPES,
    filter_broadcast_members,
    member_allows,
    notification_type_of,
    parse_hhmm,
    parse_member_prefs,
    parse_quiet_hours_input,
    seconds_until_hhmm,
    toggle_member_pref,
)

try:
    import database
except ModuleNotFoundError as exc:  # Allows pure tests to run without project dependencies installed.
    if exc.name != "aiosqlite":
        raise
    database = None


class ParseMemberPrefsTests(unittest.TestCase):
    def test_empty_inputs_return_defaults(self):
        for raw in (None, "", "   "):
            self.assertEqual(parse_member_prefs(raw), DEFAULT_MEMBER_PREFS)

    def test_garbage_json_returns_defaults(self):
        self.assertEqual(parse_member_prefs("{not json"), DEFAULT_MEMBER_PREFS)

    def test_non_dict_json_returns_defaults(self):
        self.assertEqual(parse_member_prefs("[1, 2]"), DEFAULT_MEMBER_PREFS)

    def test_partial_dict_merges_over_defaults(self):
        prefs = parse_member_prefs('{"muted": true, "wins": false}')
        self.assertTrue(prefs["muted"])
        self.assertFalse(prefs["wins"])
        self.assertTrue(prefs["mentions"])
        self.assertFalse(prefs["deadlines"])

    def test_unknown_keys_ignored(self):
        prefs = parse_member_prefs('{"bogus": true}')
        self.assertNotIn("bogus", prefs)
        self.assertEqual(prefs, DEFAULT_MEMBER_PREFS)


class MemberAllowsTests(unittest.TestCase):
    def test_muted_blocks_every_type(self):
        prefs = dict(DEFAULT_MEMBER_PREFS, muted=True, deadlines=True, digest=True)
        for notif_type in ("mention", "giveaway", "win", "deadline", "digest", "other"):
            self.assertFalse(member_allows(prefs, notif_type))

    def test_each_type_respects_its_toggle(self):
        cases = {
            "mention": "mentions",
            "giveaway": "giveaways",
            "win": "wins",
            "deadline": "deadlines",
            "digest": "digest",
        }
        for notif_type, pref_key in cases.items():
            prefs = dict(DEFAULT_MEMBER_PREFS)
            prefs[pref_key] = True
            self.assertTrue(member_allows(prefs, notif_type), notif_type)
            prefs[pref_key] = False
            self.assertFalse(member_allows(prefs, notif_type), notif_type)

    def test_deadline_and_digest_default_off(self):
        prefs = parse_member_prefs(None)
        self.assertFalse(member_allows(prefs, "deadline"))
        self.assertFalse(member_allows(prefs, "digest"))

    def test_unknown_type_falls_back_to_not_muted(self):
        self.assertTrue(member_allows(parse_member_prefs(None), "unknown"))


class NotificationTypeTests(unittest.TestCase):
    def test_win_takes_precedence(self):
        self.assertEqual(notification_type_of({"is_win": True, "is_giveaway": True}), "win")

    def test_giveaway_over_mention(self):
        self.assertEqual(notification_type_of({"is_giveaway": True}), "giveaway")

    def test_default_is_mention(self):
        self.assertEqual(notification_type_of({}), "mention")


class FilterBroadcastMembersTests(unittest.TestCase):
    def _member(self, tg_id, **kwargs):
        member = {"tg_id": tg_id, "blocked": 0, "notification_prefs": ""}
        member.update(kwargs)
        return member

    def test_excludes_blocked_admin_and_muted(self):
        members = [
            self._member(1),
            self._member(2, blocked=1),
            self._member(3),  # admin
            self._member(4, notification_prefs='{"muted": true}'),
        ]
        result = filter_broadcast_members(members, "mention", {3})
        self.assertEqual([m["tg_id"] for m in result], [1])

    def test_excludes_type_opt_outs(self):
        members = [
            self._member(1, notification_prefs='{"giveaways": false}'),
            self._member(2),
        ]
        result = filter_broadcast_members(members, "giveaway", set())
        self.assertEqual([m["tg_id"] for m in result], [2])

    def test_empty_prefs_get_core_types_but_not_optins(self):
        members = [self._member(1)]
        for notif_type in ("mention", "giveaway", "win"):
            self.assertEqual(len(filter_broadcast_members(members, notif_type, set())), 1, notif_type)
        for notif_type in ("deadline", "digest"):
            self.assertEqual(filter_broadcast_members(members, notif_type, set()), [], notif_type)


class TogglePrefTests(unittest.TestCase):
    def test_flips_value_without_mutating_input(self):
        original = dict(DEFAULT_MEMBER_PREFS)
        updated = toggle_member_pref(original, "muted")
        self.assertTrue(updated["muted"])
        self.assertFalse(original["muted"])

    def test_unknown_key_is_noop(self):
        original = dict(DEFAULT_MEMBER_PREFS)
        updated = toggle_member_pref(original, "bogus")
        self.assertEqual(updated, original)
        self.assertNotIn("bogus", updated)


class TimeParsingTests(unittest.TestCase):
    def test_parse_hhmm_valid(self):
        self.assertEqual(parse_hhmm("09:00"), "09:00")
        self.assertEqual(parse_hhmm("9:5"), "09:05")
        self.assertEqual(parse_hhmm(" 23.59 "), "23:59")

    def test_parse_hhmm_invalid(self):
        for text in ("24:00", "12:60", "ab:cd", "", "12", "1:2:3"):
            self.assertIsNone(parse_hhmm(text), text)

    def test_seconds_until_future_time_today(self):
        now = datetime(2026, 6, 12, 8, 0, 0)
        self.assertEqual(seconds_until_hhmm(now, "09:00"), 3600.0)

    def test_seconds_until_past_time_rolls_to_tomorrow(self):
        now = datetime(2026, 6, 12, 10, 0, 0)
        self.assertEqual(seconds_until_hhmm(now, "09:00"), 82800.0)

    def test_exact_time_rolls_full_day(self):
        now = datetime(2026, 6, 12, 9, 0, 0)
        self.assertEqual(seconds_until_hhmm(now, "09:00"), 86400.0)

    def test_invalid_time_falls_back_without_raising(self):
        now = datetime(2026, 6, 12, 8, 0, 0)
        self.assertEqual(seconds_until_hhmm(now, "garbage"), 3600.0)

    def test_parse_quiet_hours(self):
        self.assertEqual(parse_quiet_hours_input("23:00-08:00"), ("23:00", "08:00"))
        self.assertEqual(parse_quiet_hours_input(" 23:00 - 8:0 "), ("23:00", "08:00"))
        for text in ("23:00", "23:00-25:00", "", "a-b"):
            self.assertIsNone(parse_quiet_hours_input(text), text)


class KeywordScopesTests(unittest.TestCase):
    def test_scopes_cover_all_keyword_lists(self):
        keys = {scope[0] for scope in KEYWORD_SCOPES.values()}
        self.assertEqual(
            keys,
            {"win_keywords", "giveaway_keywords", "check_keywords", "high_priority_keywords", "ignore_keywords"},
        )


@unittest.skipIf(database is None, "aiosqlite is not installed")
class MemberPrefsMigrationTests(unittest.TestCase):
    def test_migration_adds_column_and_prefs_round_trip(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                original_path = database.DB_PATH
                database.DB_PATH = Path(tmp) / "test.db"
                try:
                    await database.init_db()
                    await database.upsert_bot_member(
                        tg_id=42, tg_username="tester", name="Tester", key_id=None, role="viewer"
                    )
                    await database.set_bot_member_prefs(42, {"muted": True, "digest": True})
                    member = await database.get_bot_member(42)
                    prefs = parse_member_prefs(member.get("notification_prefs"))
                    self.assertTrue(prefs["muted"])
                    self.assertTrue(prefs["digest"])
                    self.assertTrue(prefs["mentions"])
                finally:
                    database.DB_PATH = original_path

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
