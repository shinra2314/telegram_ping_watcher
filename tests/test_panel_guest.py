"""The guest's side of the panel: answering a giveaway, win history, pulse.

Every figure a guest sees is counted over their key's accounts, and every
action they take is limited to rows about those accounts — the same rule the
feed and the statistics already follow.
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
from pulse_desk.bot_permissions import full_permissions  # noqa: E402
from pulse_desk.miniapp_auth import sign  # noqa: E402
from pulse_desk.miniapp_server import build_miniapp  # noqa: E402
from routers.miniapp import common, pulse  # noqa: E402
from routers.miniapp.wins import outcome  # noqa: E402

TOKEN = "123456:AAHtesttokenvaluethatisnotreal"
OWNER = 1
GUEST = 2       # giveaways, scoped to @muver
READER = 3      # recent only


def headers(tg_id: int, age: int = 0) -> dict[str, str]:
    payload = {"auth_date": str(int(time.time()) - age),
               "user": json.dumps({"id": tg_id, "first_name": "T"}, separators=(",", ":"))}
    return {"X-Telegram-Init-Data": urlencode({**payload, "hash": sign(urlencode(payload), TOKEN)})}


async def fake_access(tg_id: int):
    if tg_id == OWNER:
        return "admin", full_permissions()
    if tg_id == GUEST:
        return "viewer", {**full_permissions(), "features": ["giveaways"], "accounts": ["muver"]}
    if tg_id == READER:
        return "viewer", {**full_permissions(), "features": ["recent"]}
    return None, {}


class GuestApiBase(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(build_miniapp())
        for target, value in (
            ("routers.miniapp.common.BOT_TOKEN", TOKEN),
            ("routers.miniapp.common.resolve_member_access", fake_access),
            ("pulse_desk.miniapp_server.record_app_event", AsyncMock()),
            ("routers.miniapp.giveaways.record_app_event", AsyncMock()),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        common.LIMITER.reset()
        pulse._cache.clear()


class AnswerTests(GuestApiBase):
    def ping(self, mentions='["muver"]'):
        return patch("routers.miniapp.giveaways.database.get_ping_by_id",
                     AsyncMock(return_value={"id": 5, "mentions": mentions}))

    def test_guest_answer_is_recorded(self):
        with self.ping(), patch("routers.miniapp.giveaways.database.set_member_engagement", AsyncMock()) as save:
            res = self.client.post("/api/app/giveaways/5/engagement", headers=headers(GUEST), json={"action": "joined"})
        self.assertEqual((res.status_code, res.json()["answer"]), (200, "joined"))
        save.assert_awaited_once_with(GUEST, 5, "joined")

    def test_a_row_about_another_account_is_refused(self):
        with self.ping('["someone"]'), patch("routers.miniapp.giveaways.database.set_member_engagement", AsyncMock()) as save:
            res = self.client.post("/api/app/giveaways/5/engagement", headers=headers(GUEST), json={"action": "joined"})
        self.assertEqual(res.status_code, 403)
        save.assert_not_awaited()

    def test_the_owner_has_nothing_to_answer(self):
        res = self.client.post("/api/app/giveaways/5/engagement", headers=headers(OWNER), json={"action": "joined"})
        self.assertEqual(res.status_code, 404)

    def test_a_key_without_giveaways_cannot_answer(self):
        res = self.client.post("/api/app/giveaways/5/engagement", headers=headers(READER), json={"action": "joined"})
        self.assertEqual(res.status_code, 403)

    def test_an_old_session_cannot_answer(self):
        res = self.client.post("/api/app/giveaways/5/engagement", headers=headers(GUEST, age=7200),
                               json={"action": "joined"})
        self.assertEqual(res.status_code, 401)

    def test_only_the_two_answers_exist(self):
        res = self.client.post("/api/app/giveaways/5/engagement", headers=headers(GUEST), json={"action": "maybe"})
        self.assertEqual(res.status_code, 422)


class WinsTests(GuestApiBase):
    def test_history_is_scoped_and_drops_copies(self):
        rows = [
            {"id": 9, "chat": "A", "detected_at": "2026-09-20T10:00:00", "mentions": '["muver"]',
             "giveaway_status": "claimed", "text": "5 USDT"},
            {"id": 8, "chat": "A-copy", "detected_at": "2026-09-20T09:00:00", "mentions": '["muver"]',
             "duplicate_of": 9, "text": ""},
        ]
        with patch("routers.miniapp.wins.database.get_pings", AsyncMock(return_value=rows)) as get, \
                patch("routers.miniapp.wins.database.account_win_stats",
                      AsyncMock(return_value={"wins": 1, "claimed": 1})), \
                patch("routers.miniapp.wins.latest_snapshot", AsyncMock(return_value=None)), \
                patch("routers.miniapp.wins.visible_accounts", return_value=["muver"]):
            res = self.client.get("/api/app/wins", params={"days": 90}, headers=headers(GUEST))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(get.await_args.kwargs["mention_any"], ["muver"])
        self.assertEqual(get.await_args.kwargs["chat_type"], "win")
        body = res.json()
        self.assertEqual([i["id"] for i in body["items"]], [9])
        self.assertEqual((body["days"], body["items"][0]["outcome"]), (90, "claimed"))

    def test_history_needs_the_giveaways_grant(self):
        self.assertEqual(self.client.get("/api/app/wins", headers=headers(READER)).status_code, 403)

    def test_outcome_words(self):
        self.assertEqual(outcome({"giveaway_status": "", "action_status": "claim_prize"}), "open")
        self.assertEqual(outcome({"giveaway_status": "missed_reply"}), "missed")
        self.assertEqual(outcome({"action_status": "scam"}), "scam")


class PulseTests(GuestApiBase):
    def test_guest_pulse_is_counted_over_their_accounts(self):
        with patch("routers.miniapp.pulse.get_pings", AsyncMock(return_value=[{"id": 42}])) as get:
            res = self.client.get("/api/app/pulse", headers=headers(GUEST))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"latest_giveaway": 42, "latest_win": 42})
        self.assertTrue(all(c.kwargs["mention_any"] == ["muver"] for c in get.await_args_list))

    def test_pulse_is_cached_per_person(self):
        with patch("routers.miniapp.pulse.get_pings", AsyncMock(return_value=[])) as get:
            self.client.get("/api/app/pulse", headers=headers(READER))
            self.client.get("/api/app/pulse", headers=headers(READER))
        self.assertEqual(get.await_count, 1)


class EngagementBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_answers_for_a_page_of_rows(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(database, "DB_PATH", Path(tmp) / "e.db"):
            await database.init_db()
            ids = [await database.save_ping({"chat": "c", "chat_id": 1, "message_id": i, "sender": "s",
                                             "text": f"t{i}", "mentions": ["muver"], "chat_type": "channel"})
                   for i in range(1, 5)]
            await database.set_member_engagement(7, ids[0], "joined")
            await database.set_member_engagement(7, ids[2], "skipped")
            await database.set_member_engagement(8, ids[1], "joined")
            found = await database.member_engagement_for(7, ids)
        self.assertEqual(found, {ids[0]: "joined", ids[2]: "skipped"})


if __name__ == "__main__":
    unittest.main()
