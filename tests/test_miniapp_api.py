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
from routers.miniapp import common
from routers.miniapp.common import RateLimiter
from routers.miniapp.market import rate_table

TOKEN = "123456:AAHtesttokenvaluethatisnotreal"
OWNER = 1
GUEST = 2
READER = 3      # recent only: the feed, but not search, not stats
SCOPED = 4      # recent + search + stats, one account

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
    if tg_id == READER:
        return "viewer", {**full_permissions(), "features": ["recent"], "notify": ["mentions", "wins"]}
    if tg_id == SCOPED:
        return "premium", {**full_permissions(), "features": ["recent", "search", "stats"],
                           "accounts": ["muver"]}
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
        # One limiter serves the whole class-level client; a test must not
        # inherit the minute budget the previous ones spent.
        common.LIMITER.reset()
        journal = patch("pulse_desk.miniapp_server.record_app_event", AsyncMock())
        self.journal = journal.start()
        self.addCleanup(journal.stop)

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
        with patch("routers.miniapp.debts.apply_ping_meta", AsyncMock()) as apply,                 patch("routers.miniapp.debts.snapshot", AsyncMock(return_value={5: {}, 7: {}})):
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

    # --- guest screens: feed ----------------------------------------------
    def test_feed_needs_the_recent_grant(self):
        self.assertEqual(self.client.get("/api/app/feed", headers=headers(GUEST)).status_code, 403)

    def test_search_needs_its_own_grant(self):
        with patch("routers.miniapp.feed.fetch", AsyncMock(return_value=([], False))) as fetch:
            plain = self.client.get("/api/app/feed", headers=headers(READER))
            searched = self.client.get("/api/app/feed", params={"q": "гив"}, headers=headers(READER))
        self.assertEqual((plain.status_code, searched.status_code), (200, 403))
        self.assertFalse(plain.json()["can_search"])
        self.assertEqual(fetch.await_count, 1)

    def test_feed_is_cut_to_the_keys_accounts(self):
        row = {"id": 9, "chat": "Chan", "text": "  hello\n  @muver ", "mentions": '["muver"]'}
        with patch("routers.miniapp.feed.fetch", AsyncMock(return_value=([row], True))) as fetch:
            res = self.client.get("/api/app/feed", params={"q": "hello", "type": "w", "page": 2},
                                  headers=headers(SCOPED))
        self.assertEqual(res.status_code, 200)
        filt, perms, query = fetch.await_args.args
        self.assertEqual((filt.type, filt.query, filt.page, query), ("w", True, 2, "hello"))
        self.assertEqual(perms["accounts"], ["muver"])
        self.assertEqual(fetch.await_args.kwargs["page_size"], 20)
        item = res.json()["items"][0]
        self.assertEqual((item["snippet"], item["mentions"]), ("hello @muver", ["muver"]))
        self.assertTrue(res.json()["has_more"])

    def test_feed_rejects_unknown_filter_codes(self):
        res = self.client.get("/api/app/feed", params={"type": "z"}, headers=headers(READER))
        self.assertEqual(res.status_code, 422)

    def test_card_about_another_account_is_403(self):
        row = {"id": 9, "chat": "Chan", "text": "t", "mentions": '["someone_else"]'}
        with patch("routers.miniapp.feed.database.get_ping_by_id", AsyncMock(return_value=row)):
            other = self.client.get("/api/app/feed/9", headers=headers(SCOPED))
            owner = self.client.get("/api/app/feed/9", headers=headers(OWNER))
        self.assertEqual((other.status_code, owner.status_code), (403, 200))
        self.assertIsNone(owner.json()["giveaway_status"])

    # --- guest screens: notifications ---------------------------------------
    def member(self, prefs=None):
        return patch("routers.miniapp.prefs.get_bot_member",
                     AsyncMock(return_value={"tg_id": READER, "notification_prefs": json.dumps(prefs or {})}))

    def test_owner_has_no_personal_prefs(self):
        self.assertEqual(self.client.get("/api/app/prefs", headers=headers(OWNER)).status_code, 404)

    def test_prefs_show_only_granted_types(self):
        with self.member():
            res = self.client.get("/api/app/prefs", headers=headers(READER))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["allowed"], ["mentions", "wins"])
        self.assertEqual(res.json()["hidden"], ["giveaways", "digest"])

    def test_prefs_write_needs_a_fresh_session(self):
        with self.member(), patch("routers.miniapp.prefs.set_bot_member_prefs", AsyncMock()) as save:
            res = self.client.post("/api/app/prefs", headers=headers(READER, age_seconds=2 * 60 * 60),
                                   json={"wins": False})
        self.assertEqual(res.status_code, 401)
        save.assert_not_awaited()

    def test_prefs_refuse_a_type_the_key_never_granted(self):
        with self.member(), patch("routers.miniapp.prefs.set_bot_member_prefs", AsyncMock()) as save:
            res = self.client.post("/api/app/prefs", headers=headers(READER), json={"giveaways": True})
        self.assertEqual(res.status_code, 403)
        save.assert_not_awaited()

    def test_prefs_refuse_an_unknown_autoclean_value(self):
        with self.member(), patch("routers.miniapp.prefs.set_bot_member_prefs", AsyncMock()) as save:
            res = self.client.post("/api/app/prefs", headers=headers(READER), json={"autoclean_hours": 5})
        self.assertEqual(res.status_code, 422)
        save.assert_not_awaited()

    def test_prefs_patch_is_merged_and_saved(self):
        with self.member({"min_score": 40}), \
                patch("routers.miniapp.prefs.set_bot_member_prefs", AsyncMock()) as save:
            res = self.client.post("/api/app/prefs", headers=headers(READER),
                                   json={"wins": False, "muted": True, "autoclean_hours": 24})
        self.assertEqual(res.status_code, 200)
        saved = save.await_args.args[1]
        self.assertEqual((saved["wins"], saved["muted"], saved["autoclean_hours"], saved["min_score"]),
                         (False, True, 24, 40))
        self.assertTrue(saved["mentions"])
        self.assertEqual(res.json()["prefs"], saved)

    # --- guest screens: statistics ------------------------------------------
    def test_statistics_need_stats_or_analytics(self):
        self.assertEqual(self.client.get("/api/app/analytics", headers=headers(READER)).status_code, 403)

    def test_statistics_are_counted_over_the_keys_accounts(self):
        report = {"summary": {"total": 1}, "daily": [], "chats": [{"chat": "x"}], "hours": [0] * 24}
        with patch("routers.miniapp.analytics.build_panel_report", AsyncMock(return_value=report)) as build, \
                patch("routers.miniapp.analytics.visible_accounts", return_value=["muver"]):
            scoped = self.client.get("/api/app/analytics", headers=headers(SCOPED))
            owner = self.client.get("/api/app/analytics", headers=headers(OWNER))
        self.assertEqual(scoped.status_code, 200)
        self.assertEqual(build.await_args_list[0].args, (["muver"], ["muver"]))
        self.assertEqual(build.await_args_list[1].args[0], [])
        # `stats` alone opens the summary, not the breakdowns.
        self.assertNotIn("chats", scoped.json())
        self.assertIn("chats", owner.json())

    # --- guest screens: home -------------------------------------------------
    def test_guest_home_carries_their_own_profile(self):
        member = {"tg_id": SCOPED, "notification_prefs": json.dumps({"muted": True})}
        with patch("routers.miniapp.home.get_bot_member", AsyncMock(return_value=member)), \
                patch("routers.miniapp.home.list_access_windows", AsyncMock(return_value=[])), \
                patch("routers.miniapp.home.member_engagement_since",
                      AsyncMock(return_value={"joined": 2, "skipped": 1})), \
                patch("routers.miniapp.home.account_win_stats",
                      AsyncMock(return_value={"wins": 3, "claimed": 1})) as wins, \
                patch("routers.miniapp.home.visible_accounts", return_value=["muver"]), \
                patch("routers.miniapp.home.salary_visible", AsyncMock(return_value=False)):
            res = self.client.get("/api/app/home", headers=headers(SCOPED))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["sections"]["feed"])
        self.assertEqual((data["sections"]["prefs"], data["sections"]["analytics"]), (True, True))
        self.assertFalse(data["sections"]["giveaways"])
        self.assertEqual((data["me"]["role"], data["me"]["accounts"], data["me"]["muted"]),
                         ("premium", ["muver"], True))
        self.assertEqual(data["me"]["access"], {"scheduled": False, "until": None})
        self.assertEqual(data["mine"]["wins"], {"wins": 3, "claimed": 1})
        self.assertEqual(wins.await_args.args[0], ["muver"])
        self.assertEqual(data["mine"]["recent_wins"], [])

    # --- headers ----------------------------------------------------------
    def test_api_answers_are_never_cached(self):
        res = self.client.get("/api/app/market", headers=headers(OWNER))
        self.assertEqual(res.headers["cache-control"], "no-store")

    def test_page_carries_a_content_security_policy(self):
        res = self.client.get("/app")
        self.assertEqual(res.status_code, 200)
        self.assertIn("script-src 'self' https://telegram.org", res.headers["content-security-policy"])


class GateInventoryTests(unittest.TestCase):
    """Every panel route declares its gate. A new endpoint that forgets one
    fails here instead of shipping open on a port that is on the internet."""

    @staticmethod
    def calls(dependant) -> set:
        found = set()
        for dep in dependant.dependencies:
            found.add(dep.call)
            found |= GateInventoryTests.calls(dep)
        return found

    def test_every_api_route_verifies_the_caller(self):
        routes = [r for r in build_miniapp().routes if getattr(r, "path", "").startswith("/api/")]
        self.assertTrue(routes)
        for route in routes:
            with self.subTest(route=route.path):
                self.assertIn(common.current_caller, self.calls(route.dependant))

    def test_every_action_needs_a_fresh_session(self):
        fresh = {common.fresh_admin, common.fresh_caller}
        for route in build_miniapp().routes:
            if not getattr(route, "path", "").startswith("/api/") or "POST" not in route.methods:
                continue
            with self.subTest(route=route.path):
                self.assertTrue(self.calls(route.dependant) & fresh)


class LimitsAndJournalTests(unittest.TestCase):
    """Budget per person and the action journal, on a client of their own."""

    def setUp(self):
        self.client = TestClient(build_miniapp())
        for target, value in (
            ("routers.miniapp.common.BOT_TOKEN", TOKEN),
            ("routers.miniapp.common.resolve_member_access", fake_access),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        common.LIMITER.reset()
        journal = patch("pulse_desk.miniapp_server.record_app_event", AsyncMock())
        self.journal = journal.start()
        self.addCleanup(journal.stop)

    def test_limiter_forgets_after_the_window(self):
        now = [0.0]
        limiter = RateLimiter(window=60, clock=lambda: now[0])
        self.assertTrue(all(limiter.allow("k", 3) for _ in range(3)))
        self.assertFalse(limiter.allow("k", 3))
        self.assertTrue(limiter.allow("other", 3))
        now[0] = 60.0
        self.assertTrue(limiter.allow("k", 3))

    def test_guest_actions_are_capped_per_minute(self):
        h = headers(GUEST)
        codes = [self.client.post("/api/app/pings/5/status", headers=h, json={"status": "closed"}).status_code
                 for _ in range(common.LIMITS["guest"][1] + 1)]
        self.assertEqual(codes[:-1], [403] * common.LIMITS["guest"][1])
        self.assertEqual(codes[-1], 429)
        # Reading is a separate budget.
        self.assertEqual(self.client.get("/api/app/market", headers=h).status_code, 403)

    def test_journal_names_the_actor_and_never_the_phone(self):
        result = LoginResult("code_sent", "Код отправлен", "session_380", "app")
        with patch("routers.miniapp.accounts.account_login.request_code", AsyncMock(return_value=result)):
            res = self.client.post("/api/app/login/code", headers=headers(OWNER),
                                   json={"phone": "+380671234567", "session_name": ""})
        self.assertEqual(res.status_code, 200)
        self.journal.assert_awaited_once()
        level, source, _message, context = self.journal.await_args.args
        self.assertEqual((level, source, context["tg_id"], context["role"]), ("INFO", "miniapp", OWNER, "admin"))
        self.assertEqual(context["path"], "/api/app/login/code")
        self.assertNotIn("380671234567", json.dumps(context))

    def test_journal_carries_the_ids_from_the_body(self):
        with patch("routers.miniapp.debts.apply_ping_meta", AsyncMock()),                 patch("routers.miniapp.debts.snapshot", AsyncMock(return_value={})):
            self.client.post("/api/app/debts/claim", headers=headers(OWNER), json={"ids": [5, 7]})
        context = self.journal.await_args.args[3]
        self.assertEqual((context["ids"], context["status"]), ([5, 7], "claimed"))

    def test_refused_actions_are_not_journalled(self):
        self.client.post("/api/app/debts/claim", headers=headers(GUEST), json={"ids": [1]})
        self.journal.assert_not_awaited()


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
