"""Account health rules and the once-per-problem alerting."""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk import account_health as ah  # noqa: E402

NOW = datetime(2026, 9, 13, 18, 0)
LONG_AGO = NOW - timedelta(days=1)


class RuleTests(unittest.TestCase):
    def test_hard_problems(self):
        self.assertEqual(ah.account_problem({"status": "unauthorized"}, NOW)[0], "unauthorized")
        kind, text = ah.account_problem({"status": "error", "last_error": "boom"}, NOW)
        self.assertEqual(kind, "error")
        self.assertIn("boom", text)

    def test_stuck_only_after_threshold(self):
        fresh = {"status": "reconnecting", "disconnected_at": (NOW - timedelta(minutes=5)).isoformat()}
        stale = {"status": "reconnecting", "disconnected_at": (NOW - timedelta(minutes=45)).isoformat()}
        self.assertIsNone(ah.account_problem(fresh, NOW))
        kind, text = ah.account_problem(stale, NOW)
        self.assertEqual(kind, "stuck")
        self.assertIn("45 мин", text)

    def test_silent_account(self):
        deaf = {"status": "online", "connected_at": LONG_AGO.isoformat(),
                "last_update_at": (NOW - timedelta(hours=13)).isoformat()}
        alive = dict(deaf, last_update_at=(NOW - timedelta(minutes=3)).isoformat())
        self.assertEqual(ah.account_problem(deaf, NOW, app_started_at=LONG_AGO)[0], "silent")
        self.assertIsNone(ah.account_problem(alive, NOW, app_started_at=LONG_AGO))
        # Right after a start nothing is «silent» yet.
        self.assertIsNone(ah.account_problem(deaf, NOW, app_started_at=NOW - timedelta(hours=1)))

    def test_diff_by_kind(self):
        fresh, recovered = ah.diff_problems({"a": "stuck"}, {"a": ("stuck", "45 мин"), "b": ("banned", "x")})
        self.assertEqual(fresh, {"b": "x"})
        self.assertEqual(recovered, [])
        fresh, recovered = ah.diff_problems({"a": "stuck"}, {})
        self.assertEqual((fresh, recovered), ({}, ["a"]))

    def test_auth_error_classification(self):
        AuthKeyUnregisteredError = type("AuthKeyUnregisteredError", (Exception,), {})
        UserDeactivatedBanError = type("UserDeactivatedBanError", (Exception,), {})
        self.assertEqual(ah.classify_auth_error(AuthKeyUnregisteredError()), "unauthorized")
        self.assertEqual(ah.classify_auth_error(UserDeactivatedBanError()), "banned")
        self.assertIsNone(ah.classify_auth_error(TimeoutError()))

    def test_spambot_verdicts(self):
        self.assertTrue(ah.spam_verdict("Good news, no limits are currently applied to your account.")[0])
        self.assertTrue(ah.spam_verdict("Ваш аккаунт свободен от каких-либо ограничений.")[0])
        self.assertFalse(ah.spam_verdict("Unfortunately, your account is now limited.")[0])
        self.assertIn("не ответил", ah.spam_verdict("")[1])


class AlertingTests(unittest.TestCase):
    def setUp(self):
        import database
        from pulse_desk import loops
        from pulse_desk.app_ctx import state

        self._tmp = tempfile.TemporaryDirectory(prefix="pulse_acc_")
        self._old = database.DB_PATH
        database.DB_PATH = Path(self._tmp.name) / "acc.db"
        asyncio.run(database.init_db())
        self.state = state
        self._accounts = dict(state.accounts_state)
        state.accounts_state.clear()
        loops._account_alerts.clear()
        loops._account_alerts_loaded = False
        self.sent: list[str] = []

        async def fake_send(message, **_kw):
            self.sent.append(message)
            return True

        self._patch = mock.patch.object(loops, "send_admin_bot_message", fake_send)
        self._patch.start()

    def tearDown(self):
        import database

        self._patch.stop()
        self.state.accounts_state.clear()
        self.state.accounts_state.update(self._accounts)
        database.DB_PATH = self._old
        self._tmp.cleanup()

    def test_one_alert_per_problem_and_a_recovery(self):
        from pulse_desk import loops

        self.state.accounts_state["acc1"] = {"session_name": "acc1", "status": "unauthorized"}
        asyncio.run(loops.run_account_health_once(NOW))
        asyncio.run(loops.run_account_health_once(NOW))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("acc1", self.sent[0])

        # A restart loses memory but not the stored record: no second page.
        loops._account_alerts.clear()
        loops._account_alerts_loaded = False
        asyncio.run(loops.run_account_health_once(NOW))
        self.assertEqual(len(self.sent), 1)

        # Still connecting after a restart is not a recovery…
        self.state.accounts_state["acc1"] = {"session_name": "acc1", "status": "connecting",
                                             "connecting_at": NOW.isoformat()}
        asyncio.run(loops.run_account_health_once(NOW))
        self.assertEqual(len(self.sent), 1)
        # …back online is.
        self.state.accounts_state["acc1"] = {"session_name": "acc1", "status": "online",
                                             "connected_at": NOW.isoformat(), "last_update_at": NOW.isoformat()}
        asyncio.run(loops.run_account_health_once(NOW))
        self.assertEqual(len(self.sent), 2)
        self.assertIn("снова в строю", self.sent[1])


if __name__ == "__main__":
    unittest.main()
