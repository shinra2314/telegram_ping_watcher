"""Mini App HTTP surface: who gets in, who gets what, what an action costs.

The panel's port is published to the internet, so these tests are about the
gate first: forged or missing initData, a guest reaching owner endpoints, a
stale initData driving an action. Data access is patched out — the routes are
thin, and their collaborators have their own tests.
"""
from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fastapi.testclient import TestClient

from pulse_desk.account_login import LoginResult
from pulse_desk.bot_permissions import full_permissions
from pulse_desk.miniapp_auth import sign
from pulse_desk.miniapp_server import build_miniapp
from routers.miniapp.market import rate_table

TOKEN = "123456:AAHtesttokenvaluethatisnotreal"
OWNER = 1
GUEST = 2

SNAPSHOT = {
    "bitcoin": {"usd": 60000.0, "usd_24h_change": 1.5},
    "tether": {"usd": 1.0},
    "fetched_at_iso": "2026-09-12T10:00:00",
    "_fiat": {"USD": 1.0, "UAH": 41.5, "EUR": 0.9},
}


def init_data(tg_id: int, age_seconds: int = 0) -> str:
    payload = {
        "auth_date": str(int(time.time()) - age_seconds),
        "user": json.dumps({"id": tg_id, "first_name": "T"}, separators=(",", ":")),
    }
    return urlencode({**payload, "hash": sign(urlencode(payload), TOKEN)})


def headers(tg_id: int, age_seconds: int = 0) -> dict[str, str]:
    return {"X-Telegram-Init-Data": init_data(tg_id, age_seconds)}


async def fake_access(tg_id: int):
    if tg_id == OWNER:
        return "admin", full_permissions()
    if tg_id == GUEST:
        return "viewer", {**full_permissions(), "features": ["giveaways"]}
    return None, {}


class MiniAppApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(build_miniapp())

    def setUp(self):
        for target, value in (
            ("routers.miniapp.common.BOT_TOKEN", TOKEN),
            ("routers.miniapp.common.resolve_member_access", fake_access),
            ("routers.miniapp.market.latest_snapshot", AsyncMock(return_value=SNAPSHOT)),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    # --- the gate ---------------------------------------------------------
    def test_missing_init_data_is_401(self):
        self.assertEqual(self.client.get("/api/app/market").status_code, 401)

    def test_forged_init_data_is_401(self):
        forged = init_data(OWNER).replace("%22T%22", "%22X%22")
        res = self.client.get("/api/app/market", headers={"X-Telegram-Init-Data": forged})
        self.assertEqual(res.status_code, 401)

    def test_unknown_user_is_403(self):
        self.assertEqual(self.client.get("/api/app/market", headers=headers(999)).status_code, 403)

    def test_guest_without_the_grant_is_403(self):
        self.assertEqual(self.client.get("/api/app/market", headers=headers(GUEST)).status_code, 403)

    def test_guest_never_reaches_owner_endpoints(self):
        h = headers(GUEST)
        self.assertEqual(self.client.get("/api/app/accounts", headers=h).status_code, 403)
        self.assertEqual(self.client.get("/api/app/debts", headers=h).status_code, 403)
        res = self.client.post("/api/app/login/code", headers=h, json={"phone": "+380671234567"})
        self.assertEqual(res.status_code, 403)
        res = self.client.post("/api/app/debts/claim", headers=h, json={"ids": [1]})
        self.assertEqual(res.status_code, 403)

    def test_stale_init_data_reads_but_cannot_act(self):
        stale = headers(OWNER, age_seconds=2 * 60 * 60)
        with patch("routers.miniapp.accounts.collect", AsyncMock(return_value=[])):
            self.assertEqual(self.client.get("/api/app/accounts", headers=stale).status_code, 200)
        with patch("routers.miniapp.debts.apply_ping_meta", AsyncMock()) as apply:
            res = self.client.post("/api/app/debts/claim", headers=stale, json={"ids": [1]})
        self.assertEqual(res.status_code, 401)
        apply.assert_not_awaited()

    # --- what comes back --------------------------------------------------
    def test_market_carries_the_usd_table(self):
        res = self.client.get("/api/app/market", headers=headers(OWNER))
        self.assertEqual(res.status_code, 200)
        usd = res.json()["usd"]
        self.assertEqual(usd["BTC"], 60000.0)
        self.assertAlmostEqual(usd["UAH"], 1 / 41.5)

    def test_convert_parses_free_text(self):
        res = self.client.get("/api/app/convert", params={"q": "2 usd в грн"}, headers=headers(OWNER))
        self.assertEqual(res.status_code, 200)
        self.assertAlmostEqual(res.json()["value"], 83.0)

    def test_convert_rejects_nonsense(self):
        res = self.client.get("/api/app/convert", params={"q": "привет"}, headers=headers(OWNER))
        self.assertEqual(res.status_code, 422)

    def test_status_derives_the_action_unless_one_is_given(self):
        # ✕ in the list sends only «closed»; «Вернуть» sends the row's old pair back.
        with patch("routers.miniapp.giveaways.database.get_ping_by_id", AsyncMock(return_value={"id": 5})), \
                patch("routers.miniapp.giveaways.apply_ping_meta", AsyncMock()) as apply:
            closed = self.client.post("/api/app/pings/5/status", headers=headers(OWNER), json={"status": "closed"})
            back = self.client.post("/api/app/pings/5/status", headers=headers(OWNER),
                                    json={"status": "", "action": "claim_prize"})
        self.assertEqual((closed.status_code, back.status_code), (200, 200))
        self.assertEqual(apply.await_args_list[0].kwargs["action_status"], "closed")
        self.assertEqual(apply.await_args_list[1].kwargs,
                         {"giveaway_status": "", "action_status": "claim_prize"})

    def test_guest_cannot_remove_from_the_queue(self):
        res = self.client.post("/api/app/pings/5/status", headers=headers(GUEST), json={"status": "closed"})
        self.assertEqual(res.status_code, 403)

    def test_claim_applies_each_id_once(self):
        with patch("routers.miniapp.debts.apply_ping_meta", AsyncMock()) as apply:
            res = self.client.post("/api/app/debts/claim", headers=headers(OWNER),
                                   json={"ids": [5, 7, 5]})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["count"], 2)
        self.assertEqual([c.args[0] for c in apply.await_args_list], [5, 7])
        self.assertEqual(apply.await_args_list[0].kwargs["giveaway_status"], "claimed")

    def test_login_code_goes_through_the_login_service(self):
        result = LoginResult("code_sent", "Код отправлен", "session_380", "app")
        with patch("routers.miniapp.accounts.account_login.request_code",
                   AsyncMock(return_value=result)) as request:
            res = self.client.post("/api/app/login/code", headers=headers(OWNER),
                                   json={"phone": "+380671234567", "session_name": "", "force_sms": True})
        self.assertEqual(res.json()["status"], "code_sent")
        request.assert_awaited_once_with("+380671234567", "", True)

    def test_salary_is_404_when_not_visible(self):
        with patch("routers.miniapp.salary.visible", AsyncMock(return_value=False)):
            res = self.client.get("/api/app/salary", headers=headers(GUEST))
        self.assertEqual(res.status_code, 404)

    # --- headers ----------------------------------------------------------
    def test_api_answers_are_never_cached(self):
        res = self.client.get("/api/app/market", headers=headers(OWNER))
        self.assertEqual(res.headers["cache-control"], "no-store")

    def test_page_carries_a_content_security_policy(self):
        res = self.client.get("/app")
        self.assertEqual(res.status_code, 200)
        self.assertIn("script-src 'self' https://telegram.org", res.headers["content-security-policy"])


class RateTableTests(unittest.TestCase):
    def test_empty_snapshot(self):
        self.assertEqual(rate_table(None)["usd"], {})

    def test_every_listed_coin_has_a_usd_price(self):
        table = rate_table(SNAPSHOT)
        self.assertEqual([c["code"] for c in table["coins"]], ["BTC", "USDT"])
        self.assertEqual(table["updated"], "12.09 10:00")

    def test_fiat_lines_skip_usd_itself(self):
        codes = [f["code"] for f in rate_table(SNAPSHOT)["fiat"]]
        self.assertNotIn("USD", codes)
        self.assertIn("UAH", codes)


if __name__ == "__main__":
    unittest.main()
