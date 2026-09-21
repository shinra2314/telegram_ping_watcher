"""Owner actions the panel gained: undo, triage order, card actions, the gate.

«Вернуть» must put every row back exactly as it was — including the pair of
status columns and the win's copies — because a mis-tap on «Забрал · 12»
otherwise costs twelve manual fixes. It runs here against a real temporary
database, through the HTTP surface, the way the panel calls it.
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
from pulse_desk import giveaway_ops  # noqa: E402
from pulse_desk.bot_permissions import full_permissions  # noqa: E402
from pulse_desk.miniapp_auth import sign  # noqa: E402
from pulse_desk.miniapp_server import build_miniapp  # noqa: E402
from routers.miniapp import common  # noqa: E402
from routers.miniapp.triage import order_wins  # noqa: E402

TOKEN = "123456:AAHtesttokenvaluethatisnotreal"
OWNER = 1
GUEST = 2


def headers(tg_id: int) -> dict[str, str]:
    payload = {"auth_date": str(int(time.time())),
               "user": json.dumps({"id": tg_id, "first_name": "T"}, separators=(",", ":"))}
    return {"X-Telegram-Init-Data": urlencode({**payload, "hash": sign(urlencode(payload), TOKEN)})}


async def fake_access(tg_id: int):
    if tg_id == OWNER:
        return "admin", full_permissions()
    if tg_id == GUEST:
        return "viewer", {**full_permissions(), "features": ["giveaways"]}
    return None, {}


class PanelActionsBase(unittest.TestCase):
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


class UndoRoundTripTests(PanelActionsBase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(database, "DB_PATH", Path(self.tmp.name) / "undo.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        with self.client as client:
            self.ids = client.portal.call(self._seed)

    async def _seed(self) -> list[int]:
        await database.init_db()
        ids = []
        for i, (giveaway_status, action_status) in enumerate((("", "claim_prize"), ("pending", "new"))):
            ids.append(await database.save_ping({
                "chat": f"chan{i}", "chat_id": 500 + i, "message_id": i + 1, "sender": "s",
                "text": f"Победитель @muver {i}", "mentions": ["muver"], "chat_type": "channel",
                "is_win": 1, "giveaway_status": giveaway_status, "action_status": action_status,
            }))
        return ids

    async def _statuses(self) -> list[tuple]:
        rows = [await database.get_ping_by_id(i) for i in self.ids]
        return [(r["status"], r["giveaway_status"], r["action_status"]) for r in rows]

    def test_mass_claim_is_undone_row_for_row(self):
        with self.client as client:
            before = client.portal.call(self._statuses)
            res = client.post("/api/app/debts/claim", headers=headers(OWNER), json={"ids": self.ids})
            self.assertEqual(res.status_code, 200)
            claimed = client.portal.call(self._statuses)
            self.assertTrue(all(row[1] == "claimed" for row in claimed))
            undo = client.post("/api/app/undo", headers=headers(OWNER), json={"token": res.json()["undo"]})
            self.assertEqual((undo.status_code, undo.json()["restored"]), (200, 2))
            after = client.portal.call(self._statuses)
        # NULL columns come back as the values every filter reads them as.
        self.assertEqual([(s or "new", g or "", a or "new") for s, g, a in before], after)

    def test_a_token_works_once(self):
        with self.client as client:
            res = client.post(f"/api/app/pings/{self.ids[0]}/status", headers=headers(OWNER),
                              json={"status": "missed"})
            token = res.json()["undo"]
            first = client.post("/api/app/undo", headers=headers(OWNER), json={"token": token})
            second = client.post("/api/app/undo", headers=headers(OWNER), json={"token": token})
        self.assertEqual((first.status_code, second.status_code), (200, 410))


class GateTests(PanelActionsBase):
    def test_guest_never_reaches_the_new_owner_endpoints(self):
        h = headers(GUEST)
        for method, path, body in (
            ("get", "/api/app/triage", None),
            ("get", "/api/app/cleanup", None),
            ("post", "/api/app/cleanup/leave", {"chat_ids": [1]}),
            ("post", "/api/app/undo", {"token": "abc"}),
            ("post", "/api/app/giveaways/5/analyze", {}),
            ("post", "/api/app/giveaways/5/skip", {}),
            ("post", "/api/app/accounts/x/spam", {}),
        ):
            with self.subTest(path=path):
                call = getattr(self.client, method)
                res = call(path, headers=h, json=body) if body is not None else call(path, headers=h)
                self.assertEqual(res.status_code, 403)

    def test_spam_check_of_an_offline_account_is_409(self):
        with patch("routers.miniapp.accounts._known", return_value=True), \
                patch("routers.miniapp.accounts.check_spam", AsyncMock(side_effect=LookupError("x"))):
            res = self.client.post("/api/app/accounts/x/spam", headers=headers(OWNER), json={})
        self.assertEqual(res.status_code, 409)

    def test_leave_stops_on_flood_wait(self):
        cache = {"items": {1: {"chat_id": 1, "accounts": ["a"]}, 2: {"chat_id": 2, "accounts": ["a"]}}}
        flood = giveaway_ops.GiveawayActionError("wait", "flood_wait", 30)
        with patch("routers.miniapp.giveaways.state.bot_cleanup_cache", cache), \
                patch("routers.miniapp.giveaways.leave_channel", AsyncMock(side_effect=flood)) as leave:
            res = self.client.post("/api/app/cleanup/leave", headers=headers(OWNER), json={"chat_ids": [1, 2]})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(leave.await_count, 1)
        self.assertEqual(res.json()["left"], 0)


class ProfileTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_profile_is_refreshed_for_the_ping_s_channel_not_its_id(self):
        with patch("pulse_desk.giveaway_ops.get_ping_by_id", AsyncMock(return_value={"id": 42, "chat_id": -100777})), \
                patch("pulse_desk.giveaway_ops.refresh_profile", AsyncMock(return_value={})) as refresh:
            await giveaway_ops.refresh_profile_for_ping(42)
        refresh.assert_awaited_once_with(-100777)


class TriageOrderTests(unittest.TestCase):
    def test_hot_first_then_value_then_newest(self):
        rows = [
            {"id": 1, "priority_score": 70, "detected_at": "2026-09-20T10:00:00"},
            {"id": 2, "priority_score": 95, "detected_at": "2026-09-19T10:00:00"},
            {"id": 3, "priority_score": 70, "detected_at": "2026-09-21T10:00:00"},
            {"id": 4, "priority_score": 70, "detected_at": "2026-09-18T10:00:00"},
        ]
        values = {1: 5.0, 2: None, 3: 5.0, 4: 20.0}
        self.assertEqual([r["id"] for r in order_wins(rows, values)], [2, 4, 3, 1])


if __name__ == "__main__":
    unittest.main()
