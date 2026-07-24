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
    accounts_allowed,
    allowed_pref_keys,
    dump_permissions,
    full_permissions,
    has_feature,
    normalize_permissions,
    parse_permissions,
    permission_allows_notification,
    render_permissions_summary,
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

    def test_checks_are_grantable_as_a_section_and_a_notification(self):
        self.assertIn("checks", ALL_FEATURES)
        self.assertIn("checks", ALL_NOTIFY)

    def test_check_events_map_onto_the_checks_grant(self):
        perms = normalize_permissions({"notify": ["checks"]})
        self.assertTrue(permission_allows_notification(perms, "check"))
        self.assertFalse(permission_allows_notification(perms, "win"))


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
