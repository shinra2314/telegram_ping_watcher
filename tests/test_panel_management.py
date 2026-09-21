"""Owner management in the panel: keys, access windows, settings, feed, system.

Each of these screens writes through the same helpers as the bot's buttons,
so the tests pin what the panel adds on top: validation before the write,
the secret staying out of every list, and the gate.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

from fastapi.testclient import TestClient  # noqa: E402

import database  # noqa: E402
from pulse_desk.bot_permissions import full_permissions, parse_permissions  # noqa: E402
from pulse_desk.miniapp_auth import sign  # noqa: E402
from pulse_desk.miniapp_server import build_miniapp  # noqa: E402
from routers.miniapp import common  # noqa: E402

TOKEN = "123456:AAHtesttokenvaluethatisnotreal"
OWNER = 1
GUEST = 2
MEMBER = 777


def headers(tg_id: int) -> dict[str, str]:
    payload = {"auth_date": str(int(time.time())),
               "user": json.dumps({"id": tg_id, "first_name": "T"}, separators=(",", ":"))}
    return {"X-Telegram-Init-Data": urlencode({**payload, "hash": sign(urlencode(payload), TOKEN)})}


async def fake_access(tg_id: int):
    if tg_id == OWNER:
        return "admin", full_permissions()
    if tg_id == GUEST:
        return "viewer", full_permissions()
    return None, {}


class PanelBase(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(build_miniapp())
        for target, value in (
            ("routers.miniapp.common.BOT_TOKEN", TOKEN),
            ("routers.miniapp.common.resolve_member_access", fake_access),
            ("pulse_desk.miniapp_server.record_app_event", AsyncMock()),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        common.LIMITER.reset()


class WithDatabase(PanelBase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(database, "DB_PATH", Path(self.tmp.name) / "panel.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        accounts = patch("routers.miniapp.keys.grantable_accounts", return_value=["muver", "shinra"])
        accounts.start()
        self.addCleanup(accounts.stop)
        with self.client as client:
            client.portal.call(database.init_db)


class KeyTests(WithDatabase):
    def test_a_new_key_edited_from_the_panel(self):
        with self.client as client:
            created = client.post("/api/app/keys", headers=headers(OWNER), json={"label": "Вова"}).json()
            key_id = created["id"]
            res = client.post(f"/api/app/keys/{key_id}", headers=headers(OWNER),
                              json={"features": ["giveaways", "stats"], "accounts": ["muver"], "delay_minutes": 15,
                                    "role": "premium", "expires_days": 7, "once": True})
            self.assertEqual(res.status_code, 200)
            card = res.json()
            stored = client.portal.call(database.get_bot_key, key_id)
        self.assertEqual((card["role"], card["max_uses"], card["grants"]["delay_minutes"]), ("premium", 1, 15))
        self.assertEqual(card["grants"]["accounts"], ["muver"])
        grants = parse_permissions(stored["permissions"])
        self.assertEqual(sorted(grants["features"]), ["giveaways", "stats"])
        self.assertIsNotNone(stored["expires_at"])

    def test_picking_every_account_means_all_of_them(self):
        with self.client as client:
            key_id = client.post("/api/app/keys", headers=headers(OWNER), json={}).json()["id"]
            card = client.post(f"/api/app/keys/{key_id}", headers=headers(OWNER),
                               json={"accounts": ["muver", "shinra"]}).json()
        # Stored as "all", so an account tracked later is covered too.
        self.assertEqual(card["grants"]["accounts"], [])

    def test_unknown_codes_are_refused_before_writing(self):
        with self.client as client:
            key_id = client.post("/api/app/keys", headers=headers(OWNER), json={"label": "old"}).json()["id"]
            bad_feature = client.post(f"/api/app/keys/{key_id}", headers=headers(OWNER),
                                      json={"label": "new", "features": ["admin"]})
            bad_account = client.post(f"/api/app/keys/{key_id}", headers=headers(OWNER), json={"accounts": ["nobody"]})
            stored = client.portal.call(database.get_bot_key, key_id)
        self.assertEqual((bad_feature.status_code, bad_account.status_code), (422, 422))
        # The valid field in the refused request was not written either.
        self.assertEqual(stored["label"], "old")

    def test_the_secret_is_only_in_the_link_answer(self):
        with self.client as client:
            key_id = client.post("/api/app/keys", headers=headers(OWNER), json={}).json()["id"]
            secret = client.portal.call(database.get_bot_key, key_id)["secret"]
            listing = client.get("/api/app/keys", headers=headers(OWNER)).text
            card = client.get(f"/api/app/keys/{key_id}", headers=headers(OWNER)).text
            link = client.post(f"/api/app/keys/{key_id}/link", headers=headers(OWNER), json={}).json()
        self.assertNotIn(secret, listing)
        self.assertNotIn(secret, card)
        self.assertEqual(link["secret"], secret)


class WindowTests(WithDatabase):
    def setUp(self):
        super().setUp()
        with self.client as client:
            client.portal.call(database.upsert_bot_member, MEMBER, "friend", "Друг", None)

    def test_working_hours_are_added_and_removed(self):
        with self.client as client:
            res = client.post(f"/api/app/members/{MEMBER}/windows", headers=headers(OWNER),
                              json={"kind": "work", "start": "09:00", "end": "18:00", "days": [1, 2, 3, 4, 5]})
            self.assertEqual(res.status_code, 200)
            windows = res.json()["windows"]
            self.assertEqual(len(windows), 1)
            self.assertTrue(windows[0]["allow"])
            self.assertEqual(res.json()["policy"], "deny")
            gone = client.post(f"/api/app/members/{MEMBER}/windows/{windows[0]['id']}/delete",
                               headers=headers(OWNER), json={})
        self.assertEqual(gone.json()["windows"], [])

    def test_bad_times_and_days_are_refused(self):
        with self.client as client:
            bad_time = client.post(f"/api/app/members/{MEMBER}/windows", headers=headers(OWNER),
                                   json={"kind": "work", "start": "9", "end": "25:00"})
            bad_day = client.post(f"/api/app/members/{MEMBER}/windows", headers=headers(OWNER),
                                  json={"kind": "mute", "start": "23:00", "end": "08:00", "days": [8]})
        self.assertEqual((bad_time.status_code, bad_day.status_code), (422, 422))

    def test_closing_for_two_hours_leaves_a_manual_window(self):
        with self.client as client:
            res = client.post(f"/api/app/members/{MEMBER}/access", headers=headers(OWNER),
                              json={"action": "close", "hours": 2})
            self.assertFalse(res.json()["open_now"])
            reopened = client.post(f"/api/app/members/{MEMBER}/access", headers=headers(OWNER), json={"action": "open"})
        self.assertTrue(reopened.json()["open_now"])


class SettingsTests(PanelBase):
    def test_out_of_range_value_is_refused_with_the_schema_wording(self):
        with patch("pulse_desk.bot.sections.settings.save_runtime", AsyncMock()) as save:
            res = self.client.post("/api/app/settings/field", headers=headers(OWNER), json={"index": 0, "value": 5})
        self.assertEqual(res.status_code, 422)
        self.assertIn("Минимум", res.json()["detail"])
        save.assert_not_awaited()

    def test_bad_time_and_autoclean_are_refused(self):
        bad_time = self.client.post("/api/app/settings/notifications", headers=headers(OWNER), json={"quiet_from": "7pm"})
        bad_clean = self.client.post("/api/app/settings/notifications", headers=headers(OWNER), json={"autoclean_hours": 5})
        self.assertEqual((bad_time.status_code, bad_clean.status_code), (422, 422))


class FeedOwnerTests(PanelBase):
    def test_read_marks_each_selected_row(self):
        with patch("routers.miniapp.feed.apply_ping_meta", AsyncMock()) as apply:
            res = self.client.post("/api/app/feed/read", headers=headers(OWNER), json={"ids": [3, 4, 3]})
        self.assertEqual(res.json()["count"], 2)
        self.assertEqual([c.kwargs["status"] for c in apply.await_args_list], ["read", "read"])

    def test_unknown_status_is_refused(self):
        with patch("routers.miniapp.feed.database.get_ping_by_id", AsyncMock(return_value={"id": 3})):
            res = self.client.post("/api/app/feed/3/meta", headers=headers(OWNER), json={"status": "done"})
        self.assertEqual(res.status_code, 422)


class GateTests(PanelBase):
    def test_guest_never_reaches_management(self):
        h = headers(GUEST)
        for method, path in (
            ("get", "/api/app/system"), ("post", "/api/app/system/scan"), ("post", "/api/app/system/backup"),
            ("get", "/api/app/keys"), ("post", "/api/app/keys"), ("get", "/api/app/keys/1"),
            ("post", "/api/app/keys/1/link"), ("get", "/api/app/members/5"),
            ("post", "/api/app/members/5/access"), ("get", "/api/app/settings"),
            ("post", "/api/app/settings/notifications"), ("post", "/api/app/feed/read"),
            ("post", "/api/app/feed/3/meta"), ("post", "/api/app/feed/3/ignore"),
        ):
            with self.subTest(path=path):
                call = getattr(self.client, method)
                res = call(path, headers=h, json={}) if method == "post" else call(path, headers=h)
                self.assertEqual(res.status_code, 403)


if __name__ == "__main__":
    unittest.main()
