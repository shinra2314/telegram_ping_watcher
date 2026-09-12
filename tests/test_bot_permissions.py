from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.bot_permissions import (
    ALL_FEATURES,
    ALL_NOTIFY,
    DELAY_PRESETS,
    MAX_DELAY_MINUTES,
    accounts_allowed,
    allowed_pref_keys,
    dump_permissions,
    format_delay,
    full_permissions,
    has_feature,
    normalize_permissions,
    parse_delay_input,
    parse_permissions,
    permission_allows_notification,
    permission_delay_minutes,
    render_permissions_summary,
    set_accounts,
    set_delay,
    set_features,
    set_notify,
    toggle_account,
    toggle_feature,
    toggle_notify,
)
from pulse_desk.bot_prefs import filter_broadcast_members, render_member_prefs_text


class ParsePermissionsTests(unittest.TestCase):
    def test_missing_column_means_full_access(self):
        """Keys created before grants existed must keep working unchanged."""
        for raw in (None, "", "   "):
            self.assertEqual(parse_permissions(raw), full_permissions())

    def test_garbage_json_falls_back_to_full_access(self):
        self.assertEqual(parse_permissions("{not json"), full_permissions())

    def test_unknown_codes_are_dropped_and_order_is_stable(self):
        perms = normalize_permissions({"features": ["market", "nope", "stats"], "notify": ["wins"], "accounts": []})
        self.assertEqual(perms["features"], [c for c in ALL_FEATURES if c in ("stats", "market")])
        self.assertEqual(perms["notify"], ["wins"])

    def test_empty_list_grants_nothing_but_omitted_key_grants_all(self):
        self.assertEqual(normalize_permissions({"features": []})["features"], [])
        self.assertEqual(normalize_permissions({"features": []})["notify"], ALL_NOTIFY)

    def test_accounts_are_stripped_and_deduplicated(self):
        perms = normalize_permissions({"accounts": ["@Muver", " muver ", "", "Other"]})
        self.assertEqual(perms["accounts"], ["Muver", "Other"])

    def test_round_trip_through_json_column(self):
        perms = normalize_permissions({"features": ["stats"], "notify": ["wins"], "accounts": ["@a"]})
        self.assertEqual(parse_permissions(dump_permissions(perms)), perms)


class FeatureGateTests(unittest.TestCase):
    def test_has_feature(self):
        perms = normalize_permissions({"features": ["stats", "market"]})
        self.assertTrue(has_feature(perms, "stats"))
        self.assertFalse(has_feature(perms, "search"))


class SendDelayTests(unittest.TestCase):
    def test_legacy_keys_have_no_delay(self):
        self.assertEqual(permission_delay_minutes(full_permissions()), 0)
        self.assertEqual(permission_delay_minutes(parse_permissions("")), 0)
        self.assertEqual(permission_delay_minutes(None), 0)

    def test_delay_is_clamped_and_garbage_tolerant(self):
        self.assertEqual(normalize_permissions({"delay_minutes": -5})["delay_minutes"], 0)
        self.assertEqual(normalize_permissions({"delay_minutes": 10 ** 6})["delay_minutes"], MAX_DELAY_MINUTES)
        self.assertEqual(normalize_permissions({"delay_minutes": "junk"})["delay_minutes"], 0)

    def test_delay_survives_the_json_column(self):
        perms = set_delay(full_permissions(), 15)
        self.assertEqual(permission_delay_minutes(parse_permissions(dump_permissions(perms))), 15)

    def test_reads_a_raw_json_column_directly(self):
        self.assertEqual(permission_delay_minutes('{"delay_minutes": 30}'), 30)

    def test_parse_delay_input_accepts_plain_and_suffixed_minutes(self):
        self.assertEqual(parse_delay_input("0"), 0)
        self.assertEqual(parse_delay_input(" 15 "), 15)
        self.assertEqual(parse_delay_input("30 мин"), 30)
        self.assertEqual(parse_delay_input("45min"), 45)
        self.assertEqual(parse_delay_input(str(MAX_DELAY_MINUTES)), MAX_DELAY_MINUTES)

    def test_parse_delay_input_rejects_junk_and_overflow(self):
        for text in ("", "  ", "-5", "abc", "1441", "5.5", "1e3"):
            self.assertIsNone(parse_delay_input(text), text)

    def test_format_delay(self):
        self.assertEqual(format_delay(0), "мгновенно")
        self.assertEqual(format_delay(5), "5 мин")
        self.assertEqual(format_delay(60), "1 ч")
        self.assertEqual(format_delay(90), "1 ч 30 мин")

    def test_presets_are_valid_inputs(self):
        for minutes in DELAY_PRESETS:
            self.assertEqual(set_delay(full_permissions(), minutes)["delay_minutes"], minutes)

    def test_summary_mentions_the_delay(self):
        self.assertIn("15 мин", render_permissions_summary(set_delay(full_permissions(), 15)))


class PanelMutatorTests(unittest.TestCase):
    def test_toggle_feature_flips_and_keeps_catalog_order(self):
        perms = toggle_feature(full_permissions(), "market")
        self.assertNotIn("market", perms["features"])
        self.assertEqual(toggle_feature(perms, "market")["features"], ALL_FEATURES)

    def test_toggle_ignores_unknown_codes(self):
        perms = full_permissions()
        self.assertEqual(toggle_feature(perms, "bogus")["features"], ALL_FEATURES)
        self.assertEqual(toggle_notify(perms, "bogus")["notify"], ALL_NOTIFY)

    def test_mutators_do_not_touch_the_input(self):
        perms = full_permissions()
        toggle_feature(perms, "market")
        set_delay(perms, 30)
        toggle_account(perms, "muver")
        self.assertEqual(perms, full_permissions())

    def test_toggle_notify_flips(self):
        perms = toggle_notify(full_permissions(), "wins")
        self.assertNotIn("wins", perms["notify"])

    def test_toggle_account_adds_then_removes(self):
        added = toggle_account(full_permissions(), "@Muver")
        self.assertEqual(added["accounts"], ["Muver"])
        self.assertEqual(toggle_account(added, "muver")["accounts"], [])

    def test_toggle_account_ignores_blanks(self):
        self.assertEqual(toggle_account(full_permissions(), "  ")["accounts"], [])

    def test_set_helpers_normalise(self):
        self.assertEqual(set_features(full_permissions(), ["market", "bogus"])["features"], ["market"])
        self.assertEqual(set_notify(full_permissions(), [])["notify"], [])
        self.assertEqual(set_accounts(full_permissions(), ["@a", "a", " "])["accounts"], ["a"])

    def test_mutators_preserve_the_other_fields(self):
        perms = set_delay(set_accounts(full_permissions(), ["muver"]), 20)
        after = toggle_feature(perms, "market")
        self.assertEqual(after["accounts"], ["muver"])
        self.assertEqual(after["delay_minutes"], 20)


class AccountFilterTests(unittest.TestCase):
    def test_empty_whitelist_allows_everything(self):
        perms = normalize_permissions({"accounts": []})
        self.assertTrue(accounts_allowed(perms, ["@anyone"]))
        self.assertTrue(accounts_allowed(perms, []))

    def test_whitelist_matches_case_insensitively_and_ignores_at_sign(self):
        perms = normalize_permissions({"accounts": ["MuverGT"]})
        self.assertTrue(accounts_allowed(perms, ["@muvergt"]))
        self.assertTrue(accounts_allowed(perms, ["muvergt"]))
        self.assertFalse(accounts_allowed(perms, ["@someone_else"]))

    def test_whitelist_accepts_the_raw_json_column(self):
        perms = normalize_permissions({"accounts": ["muver"]})
        self.assertTrue(accounts_allowed(perms, json.dumps(["@muver"])))
        self.assertFalse(accounts_allowed(perms, json.dumps(["@other"])))

    def test_unmentioned_event_is_blocked_for_a_restricted_key(self):
        perms = normalize_permissions({"accounts": ["muver"]})
        self.assertFalse(accounts_allowed(perms, None))


class NotificationGateTests(unittest.TestCase):
    def test_type_must_be_granted(self):
        perms = normalize_permissions({"notify": ["wins"]})
        self.assertTrue(permission_allows_notification(perms, "win"))
        self.assertFalse(permission_allows_notification(perms, "giveaway"))

    def test_account_whitelist_narrows_a_granted_type(self):
        perms = normalize_permissions({"notify": ["wins"], "accounts": ["muver"]})
        self.assertTrue(permission_allows_notification(perms, "win", ["@muver"]))
        self.assertFalse(permission_allows_notification(perms, "win", ["@other"]))

    def test_digest_reaches_an_unrestricted_key(self):
        perms = normalize_permissions({"notify": ["digest"]})
        self.assertTrue(permission_allows_notification(perms, "digest", ["@anyone"]))

    def test_account_scoped_key_never_gets_the_aggregate_digest(self):
        """One digest covers every account — it cannot be narrowed, so it is withheld."""
        perms = normalize_permissions({"notify": ["digest", "wins"], "accounts": ["muver"]})
        self.assertFalse(permission_allows_notification(perms, "digest", ["@muver"]))
        self.assertTrue(permission_allows_notification(perms, "win", ["@muver"]))

    def test_allowed_pref_keys_follows_the_grant(self):
        self.assertEqual(allowed_pref_keys(normalize_permissions({"notify": ["wins", "digest"]})), ["wins", "digest"])

    def test_allowed_pref_keys_hides_digest_for_a_scoped_key(self):
        perms = normalize_permissions({"notify": ["wins", "digest"], "accounts": ["muver"]})
        self.assertEqual(allowed_pref_keys(perms), ["wins"])


class ApertureCatalogTests(unittest.TestCase):
    """The catalogs must cover every section/notification the bot actually has."""

    def test_giveaways_are_grantable_as_a_section_and_a_notification(self):
        self.assertIn("giveaways", ALL_FEATURES)
        self.assertIn("giveaways", ALL_NOTIFY)

    def test_giveaway_events_map_onto_the_giveaways_grant(self):
        perms = normalize_permissions({"notify": ["giveaways"]})
        self.assertTrue(permission_allows_notification(perms, "giveaway"))
        self.assertFalse(permission_allows_notification(perms, "win"))

    def test_removed_grants_are_gone_from_the_catalogs(self):
        for code in ("checks", "deadlines"):
            self.assertNotIn(code, ALL_FEATURES)
            self.assertNotIn(code, ALL_NOTIFY)


class BroadcastFilterTests(unittest.TestCase):
    def _member(self, tg_id: int, permissions: str = "", prefs: str = "") -> dict:
        return {"tg_id": tg_id, "permissions": permissions, "notification_prefs": prefs, "blocked": 0}

    def test_key_grant_blocks_a_type_the_member_enabled(self):
        member = self._member(1, dump_permissions(normalize_permissions({"notify": ["mentions"]})))
        self.assertEqual(filter_broadcast_members([member], "win", set()), [])
        self.assertEqual(filter_broadcast_members([member], "mention", set()), [member])

    def test_account_scoped_key_only_gets_its_own_wins(self):
        member = self._member(1, dump_permissions(normalize_permissions({"accounts": ["muver"]})))
        self.assertEqual(filter_broadcast_members([member], "win", set(), mentions=["@muver"]), [member])
        self.assertEqual(filter_broadcast_members([member], "win", set(), mentions=["@other"]), [])

    def test_personal_mute_still_wins_over_a_granted_type(self):
        member = self._member(1, prefs=json.dumps({"wins": False}))
        self.assertEqual(filter_broadcast_members([member], "win", set()), [])

    def test_blocked_and_admin_members_are_skipped(self):
        blocked = self._member(1)
        blocked["blocked"] = 1
        self.assertEqual(filter_broadcast_members([blocked], "win", set()), [])
        self.assertEqual(filter_broadcast_members([self._member(7)], "win", {7}), [])

    def test_legacy_member_without_grants_still_receives(self):
        self.assertEqual(len(filter_broadcast_members([self._member(1)], "win", set(), mentions=["@any"])), 1)


class RenderTests(unittest.TestCase):
    def test_summary_mentions_restricted_accounts(self):
        text = render_permissions_summary(normalize_permissions({"accounts": ["muver"]}))
        self.assertIn("@muver", text)
        self.assertIn("все разделы", text)

    def test_member_prefs_text_hides_ungranted_types(self):
        perms = normalize_permissions({"notify": ["wins"]})
        text = render_member_prefs_text({"wins": True}, allowed_pref_keys(perms), perms.get("accounts"))
        self.assertIn("Победы", text)
        self.assertIn("Скрыто владельцем", text)
        self.assertNotIn("✅ Упоминания", text)


if __name__ == "__main__":
    unittest.main()
