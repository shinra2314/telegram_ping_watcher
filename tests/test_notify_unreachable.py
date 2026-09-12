"""A recipient the bot can never reach must not cost the pipeline 6s per ping.

A notify target pointing at an account that never pressed /start burned three
attempts plus 2s+4s of backoff on every single send — inline in
``process_ping_message``, i.e. stalling the sweep and the live update handler
alike (2000+ such attempts in the app log). The cooldown below is what keeps a
dead peer from doing that again.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

BASE_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = BASE_DIR / "src"
for path in (str(BASE_DIR), str(SRC_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from pulse_desk.telegram_errors import is_unreachable_recipient

try:
    from pulse_desk import bot_notify

    DEPS_OK = True
except Exception as _exc:  # missing deps or unloadable settings (e.g. CI w/o .env)
    DEPS_OK = False
    _IMPORT_ERROR = _exc


class UnreachableRecipientDetectionTests(unittest.TestCase):
    def test_matches_unknown_username(self):
        self.assertTrue(is_unreachable_recipient(Exception('No user has "w3v8f0rm" as username')))

    def test_matches_unresolvable_entity(self):
        self.assertTrue(is_unreachable_recipient(ValueError("Cannot find any entity corresponding to \"@ghost\"")))
        self.assertTrue(is_unreachable_recipient(Exception("PEER_ID_INVALID")))

    def test_matches_blocked_and_deactivated(self):
        self.assertTrue(is_unreachable_recipient(Exception("USER_IS_BLOCKED")))
        self.assertTrue(is_unreachable_recipient(Exception("INPUT_USER_DEACTIVATED")))
        self.assertTrue(is_unreachable_recipient(Exception("bot can't initiate conversation with a user")))

    def test_ignores_transient_failures(self):
        self.assertFalse(is_unreachable_recipient(Exception("Telegram is having internal issues")))
        self.assertFalse(is_unreachable_recipient(Exception("The provided reply markup is invalid")))


class FakeBotClient:
    def __init__(self, error: Exception):
        self.error = error
        self.calls = 0

    def is_connected(self):
        return True

    async def connect(self):
        return None

    async def send_message(self, *args, **kwargs):
        self.calls += 1
        raise self.error


class SendBotMessageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        if not DEPS_OK:
            self.skipTest(f"pulse_desk deps unavailable: {_IMPORT_ERROR!r}")
        bot_notify.reset_unreachable_peers()
        self._old_client = bot_notify.state.bot_client

    def tearDown(self):
        if not DEPS_OK:
            return
        bot_notify.state.bot_client = self._old_client
        bot_notify.reset_unreachable_peers()

    async def test_permanent_failure_is_not_retried(self):
        client = FakeBotClient(Exception('No user has "w3v8f0rm" as username'))
        bot_notify.state.bot_client = client
        sleep = AsyncMock()
        with patch("pulse_desk.bot_notify.asyncio.sleep", new=sleep), \
                patch("pulse_desk.bot_notify.record_app_event", new=AsyncMock()):
            result = await bot_notify._send_bot_message("@w3v8f0rm", "hi")
        self.assertIsNone(result)
        self.assertEqual(client.calls, 1)
        sleep.assert_not_awaited()

    async def test_known_bad_peer_skips_the_request(self):
        client = FakeBotClient(Exception('No user has "w3v8f0rm" as username'))
        bot_notify.state.bot_client = client
        with patch("pulse_desk.bot_notify.asyncio.sleep", new=AsyncMock()), \
                patch("pulse_desk.bot_notify.record_app_event", new=AsyncMock()):
            await bot_notify._send_bot_message("@w3v8f0rm", "hi")
            await bot_notify._send_bot_message("@w3v8f0rm", "hi again")
        self.assertEqual(client.calls, 1)

    async def test_transient_failure_still_retries(self):
        client = FakeBotClient(Exception("Telegram is having internal issues"))
        bot_notify.state.bot_client = client
        with patch("pulse_desk.bot_notify.asyncio.sleep", new=AsyncMock()), \
                patch("pulse_desk.bot_notify.record_app_event", new=AsyncMock()):
            result = await bot_notify._send_bot_message(12345, "hi")
        self.assertIsNone(result)
        self.assertEqual(client.calls, 3)

    async def test_cooldown_expires(self):
        bot_notify.mark_peer_unreachable("@ghost", now=0.0)
        self.assertTrue(bot_notify.peer_is_unreachable("@ghost", now=10.0))
        self.assertFalse(
            bot_notify.peer_is_unreachable("@ghost", now=bot_notify.UNREACHABLE_PEER_COOLDOWN_SECONDS + 1)
        )

    async def test_success_clears_cooldown(self):
        sent = SimpleNamespace(id=7)

        class OkClient(FakeBotClient):
            async def send_message(self, *args, **kwargs):
                self.calls += 1
                return sent

        bot_notify.mark_peer_unreachable("@ghost", now=0.0)
        bot_notify.clear_peer_unreachable("@ghost")
        client = OkClient(Exception("unused"))
        bot_notify.state.bot_client = client
        with patch("pulse_desk.bot_notify.record_app_event", new=AsyncMock()):
            result = await bot_notify._send_bot_message("@ghost", "hi")
        self.assertIs(result, sent)
        self.assertFalse(bot_notify.peer_is_unreachable("@ghost"))


if __name__ == "__main__":
    unittest.main()
