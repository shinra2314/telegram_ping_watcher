"""Handler safety net: safe(), safe_edit() and the callback error paths.

These are the paths a user meets when something goes wrong mid-click, and they
had no coverage at all — bot/service.py was 2500 lines with zero tests.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
for path in (str(ROOT), str(SRC_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from _fakes import FakeEvent  # noqa: E402

from pulse_desk.bot import service  # noqa: E402


class _Err(Exception):
    pass


class TellTests(unittest.IsolatedAsyncioTestCase):
    """`_tell` must reach the user whichever channel is still open."""

    async def test_unanswered_callback_gets_an_alert(self):
        event = FakeEvent(data=b"menu_main")
        await service._tell(event, "boom")
        self.assertEqual(event.answer_texts, ["boom"])
        self.assertTrue(event.answers[0]["alert"])
        self.assertEqual(event.responses, [])

    async def test_already_answered_callback_falls_back_to_respond(self):
        # Telethon answers the query on the first edit, so a later alert is a
        # silent no-op — the user used to see nothing at all.
        event = FakeEvent(data=b"menu_main")
        await event.edit("card")
        await service._tell(event, "boom")
        self.assertEqual(event.answers, [])
        self.assertEqual(event.response_texts, ["boom"])

    async def test_message_event_gets_a_reply(self):
        event = FakeEvent(data=None, text="/menu")
        await service._tell(event, "boom")
        self.assertEqual(event.response_texts, ["boom"])

    async def test_never_raises_when_every_channel_fails(self):
        event = FakeEvent(data=b"x", raises={"answer": _Err("no"), "respond": _Err("no")})
        await service._tell(event, "boom")  # must not raise


class SafeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = service.state
        self.state.bot_handler_calls = 0
        self.state.bot_handler_errors = 0
        self.state.bot_handler_slow = 0
        self.addCleanup(setattr, self.state, "bot_handler_calls", 0)
        self.addCleanup(setattr, self.state, "bot_handler_errors", 0)
        self.addCleanup(setattr, self.state, "bot_handler_slow", 0)

    async def test_success_counts_a_call_and_marks_the_bot_alive(self):
        async def handler(event):
            await event.edit("ok")

        event = FakeEvent(data=b"menu_main")
        await service.safe(handler)(event)
        self.assertEqual(event.edit_texts, ["ok"])
        self.assertEqual(self.state.bot_handler_calls, 1)
        self.assertEqual(self.state.bot_handler_errors, 0)
        # `bot_connected` only proves the socket is up; this is what proves the
        # bot is still processing updates.
        self.assertIsNotNone(self.state.bot_last_update_at)

    async def test_failure_before_any_reply_alerts_the_user(self):
        async def handler(event):
            raise _Err("nope")

        event = FakeEvent(data=b"menu_main")
        await service.safe(handler)(event)
        self.assertEqual(len(event.answers), 1)
        self.assertIn("не так", event.answer_texts[0])
        self.assertEqual(self.state.bot_handler_errors, 1)

    async def test_failure_after_an_edit_still_reaches_the_user(self):
        async def handler(event):
            await event.edit("half a card")
            raise _Err("nope")

        event = FakeEvent(data=b"menu_main")
        await service.safe(handler)(event)
        # The alert is a no-op here, so the message must arrive as a reply.
        self.assertEqual(event.answers, [])
        self.assertEqual(len(event.responses), 1)

    async def test_flood_wait_says_how_long(self):
        exc = service.FloodWaitError(request=None)
        exc.seconds = 42

        async def handler(event):
            raise exc

        event = FakeEvent(data=b"menu_main")
        await service.safe(handler)(event)
        self.assertIn("42", event.answer_texts[0])
        self.assertEqual(self.state.bot_handler_errors, 1)

    async def test_slow_handler_is_counted(self):
        async def handler(event):
            pass

        event = FakeEvent(data=b"menu_main")
        original = service.SLOW_HANDLER_SECONDS
        service.SLOW_HANDLER_SECONDS = 0.0  # everything counts as slow
        try:
            await service.safe(handler)(event)
        finally:
            service.SLOW_HANDLER_SECONDS = original
        self.assertEqual(self.state.bot_handler_slow, 1)

    async def test_handler_name_is_preserved(self):
        async def my_handler(event):
            pass

        self.assertEqual(service.safe(my_handler).__name__, "my_handler")


class SafeEditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # No emoji pack: safe_edit then passes the text through untouched.
        self.state = service.state
        self._map = self.state.custom_emoji_map
        self.state.custom_emoji_map = {}
        self.addCleanup(setattr, self.state, "custom_emoji_map", self._map)

    async def test_plain_edit(self):
        event = FakeEvent(data=b"x")
        await service.safe_edit(event, "card")
        self.assertEqual(event.edit_texts, ["card"])

    async def test_not_modified_is_swallowed(self):
        event = FakeEvent(data=b"x", raises={"edit": service.MessageNotModifiedError(request=None)})
        await service.safe_edit(event, "card")
        self.assertEqual(event.edits, [])  # nothing rendered, but no crash

    async def test_gone_message_is_resent(self):
        # The card is still worth showing, so a dead message id degrades to a
        # fresh reply instead of losing the click.
        event = FakeEvent(data=b"x", raises={"edit": service.MessageIdInvalidError(request=None)})
        await service.safe_edit(event, "card")
        self.assertEqual(event.response_texts, ["card"])

    async def test_edit_expired_is_resent(self):
        event = FakeEvent(data=b"x", raises={"edit": service.MessageEditTimeExpiredError(request=None)})
        await service.safe_edit(event, "card")
        self.assertEqual(event.response_texts, ["card"])

    async def test_fatal_edit_error_tells_the_user(self):
        event = FakeEvent(data=b"x", raises={"edit": service.MessageTooLongError(request=None)})
        await service.safe_edit(event, "card")
        # The edit already flipped _answered, so the notice arrives as a reply.
        self.assertEqual(len(event.responses), 1)
        self.assertIn("Откройте раздел заново", event.response_texts[0])


class EventLabelTests(unittest.TestCase):
    def test_callback_data(self):
        self.assertEqual(service._event_label(FakeEvent(data=b"gw:f:detected:0:0:1")),
                         "gw:f:detected:0:0:1")

    def test_message_text_is_first_line_only(self):
        event = FakeEvent(data=None, text="/search TON\nsecond line")
        self.assertEqual(service._event_label(event), "/search TON")

    def test_empty_event(self):
        self.assertEqual(service._event_label(FakeEvent(data=None, text="")), "")

    def test_long_data_is_truncated(self):
        self.assertLessEqual(len(service._event_label(FakeEvent(data=b"x" * 200))), 64)


if __name__ == "__main__":
    unittest.main()
