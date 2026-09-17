"""Logging into a Telegram account from the panel: number -> code -> 2FA.

A fake Telethon client stands in for Telegram, so every branch a real login
can take — wrong code, cloud password, expired attempt, flood wait — runs
without a network and without burning a real code.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

from pulse_desk import account_login
from pulse_desk.app_ctx import state

PHONE = "+380671234567"


class FakeClient:
    def __init__(self, *, send=None, sign_in=None, me=None):
        self.connected = False
        self._send = send
        self._sign_in = list(sign_in or [])
        self._me = me or SimpleNamespace(id=77, username="fresh")
        self.sign_in_calls: list[dict] = []

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def send_code_request(self, phone, force_sms=False):
        if isinstance(self._send, BaseException):
            raise self._send
        return SimpleNamespace(phone_code_hash="hash-1", type=SimpleNamespace())

    async def sign_in(self, phone=None, code=None, *, password=None, phone_code_hash=None):
        self.sign_in_calls.append({"phone": phone, "code": code, "password": password})
        outcome = self._sign_in.pop(0) if self._sign_in else None
        if isinstance(outcome, BaseException):
            raise outcome
        return self._me

    async def get_me(self):
        return self._me


def run(coro):
    return asyncio.run(coro)


class LoginFlowTests(unittest.TestCase):
    def setUp(self):
        self.saved = (dict(state.pending_auths), dict(state.accounts_state), list(state.session_names))
        state.pending_auths.clear()
        account_login._last_request.clear()
        self.clock = [1000.0]
        for target, value in (
            ("pulse_desk.account_login.API_ID", 1),
            ("pulse_desk.account_login.API_HASH", "h"),
            ("pulse_desk.account_login.disconnect_account", AsyncMock(return_value=False)),
            ("pulse_desk.account_login.record_app_event", AsyncMock()),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.restore)

    def restore(self):
        pending, accounts, names = self.saved
        state.pending_auths.clear()
        state.pending_auths.update(pending)
        state.accounts_state.clear()
        state.accounts_state.update(accounts)
        state.session_names[:] = names

    def request(self, client, phone=PHONE, name="panel_test"):
        return run(account_login.request_code(
            phone, name, client_factory=lambda _name: client, clock=lambda: self.clock[0]))

    # --- step 1 -----------------------------------------------------------
    def test_code_request_keeps_a_pending_login(self):
        client = FakeClient()
        result = self.request(client)
        self.assertEqual(result.status, "code_sent")
        self.assertEqual(result.session_name, "panel_test")
        self.assertIs(state.pending_auths[PHONE]["client"], client)
        self.assertTrue(client.connected)

    def test_bad_phone_is_refused_before_telegram(self):
        client = FakeClient()
        result = self.request(client, phone="12ab")
        self.assertEqual(result.status, "error")
        self.assertFalse(client.connected)

    def test_second_request_within_the_cooldown_is_refused(self):
        self.request(FakeClient())
        self.clock[0] += 10
        second = FakeClient()
        result = self.request(second)
        self.assertEqual(result.status, "error")
        self.assertIn("через", result.message)
        self.assertFalse(second.connected)

    def test_request_after_the_cooldown_replaces_the_old_client(self):
        first = FakeClient()
        self.request(first)
        self.clock[0] += account_login.RESEND_COOLDOWN_SECONDS + 1
        second = FakeClient()
        self.assertEqual(self.request(second).status, "code_sent")
        self.assertFalse(first.connected)
        self.assertIs(state.pending_auths[PHONE]["client"], second)

    def test_flood_wait_reports_the_seconds_and_keeps_nothing(self):
        client = FakeClient(send=FloodWaitError(request=None, capture=321))
        result = self.request(client)
        self.assertEqual(result.status, "error")
        self.assertIn("321", result.message)
        self.assertNotIn(PHONE, state.pending_auths)
        self.assertFalse(client.connected)

    # --- step 2 -----------------------------------------------------------
    def test_wrong_code_keeps_the_login_open(self):
        client = FakeClient(sign_in=[PhoneCodeInvalidError(request=None)])
        self.request(client)
        result = run(account_login.submit_code(PHONE, "11111", launch=lambda n: None))
        self.assertEqual(result.status, "error")
        self.assertIn(PHONE, state.pending_auths)

    def test_empty_code_never_reaches_telegram(self):
        client = FakeClient()
        self.request(client)
        result = run(account_login.submit_code(PHONE, "  -  ", launch=lambda n: None))
        self.assertEqual(result.status, "error")
        self.assertEqual(client.sign_in_calls, [])

    def test_code_with_separators_is_cleaned(self):
        client = FakeClient()
        self.request(client)
        run(account_login.submit_code(PHONE, "12 345", launch=lambda n: None))
        self.assertEqual(client.sign_in_calls[0]["code"], "12345")

    def test_right_code_connects_and_launches_monitoring(self):
        client = FakeClient()
        launched: list[str] = []
        self.request(client)
        result = run(account_login.submit_code(PHONE, "12345", launch=launched.append))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.user, "@fresh")
        self.assertEqual(launched, ["panel_test"])
        self.assertIn("panel_test", state.session_names)
        self.assertNotIn(PHONE, state.pending_auths)
        self.assertFalse(client.connected)

    def test_expired_login_is_closed(self):
        client = FakeClient()
        self.request(client)
        state.pending_auths[PHONE]["created_at"] = datetime.now() - timedelta(days=1)
        result = run(account_login.submit_code(PHONE, "12345", launch=lambda n: None))
        self.assertEqual(result.status, "error")
        self.assertNotIn(PHONE, state.pending_auths)
        self.assertFalse(client.connected)

    # --- step 3 -----------------------------------------------------------
    def test_cloud_password_flow(self):
        client = FakeClient(sign_in=[SessionPasswordNeededError(request=None),
                                     PasswordHashInvalidError(request=None), None])
        launched: list[str] = []
        self.request(client)
        first = run(account_login.submit_code(PHONE, "12345", launch=launched.append))
        self.assertEqual(first.status, "password_needed")
        wrong = run(account_login.submit_password(PHONE, "nope", launch=launched.append))
        self.assertEqual(wrong.status, "error")
        self.assertIn(PHONE, state.pending_auths)
        right = run(account_login.submit_password(PHONE, "secret", launch=launched.append))
        self.assertEqual(right.status, "ok")
        self.assertEqual(launched, ["panel_test"])

    def test_password_before_code_is_refused(self):
        client = FakeClient()
        self.request(client)
        result = run(account_login.submit_password(PHONE, "secret", launch=lambda n: None))
        self.assertEqual(result.status, "error")
        self.assertEqual(client.sign_in_calls, [])

    def test_cancel_closes_the_client(self):
        client = FakeClient()
        self.request(client)
        result = run(account_login.cancel(PHONE))
        self.assertEqual(result.status, "cancelled")
        self.assertNotIn(PHONE, state.pending_auths)
        self.assertFalse(client.connected)


class CleanInputTests(unittest.TestCase):
    def test_phone_forms(self):
        self.assertEqual(account_login.clean_phone("+380 (67) 123-45-67"), PHONE)
        self.assertEqual(account_login.clean_phone("380671234567"), PHONE)
        self.assertEqual(account_login.clean_phone("hello"), "")
        self.assertEqual(account_login.clean_phone(""), "")

    def test_code_keeps_digits_only(self):
        self.assertEqual(account_login.clean_code("12-3 45"), "12345")


if __name__ == "__main__":
    unittest.main()
