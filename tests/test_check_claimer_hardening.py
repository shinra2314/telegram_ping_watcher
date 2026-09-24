"""The claimer under failure: hand-offs, timeouts, cooldowns, cards that close.

Harness from test_check_claimer (two accounts, a scripted wallet bot). See
docs/superpowers/specs/2026-09-24-reliability.md, stage 2.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pulse_desk import check_claimer
from pulse_desk import check_claims as cc
from pulse_desk.app_ctx import state

from test_check_claimer import ClaimerCase, FakeClient, post


class FloodWait(Exception):
    """Telethon's FloodWaitError carries ``seconds``; that is all the claimer reads."""

    def __init__(self, seconds: int):
        super().__init__(f"A wait of {seconds} seconds is required")
        self.seconds = seconds


class RefusingClient(FakeClient):
    """An account whose startBot fails the way the test says."""

    def __init__(self, session, script=None, *, error: BaseException):
        super().__init__(session, script)
        self.error = error
        self.refused = 0

    async def __call__(self, request, **kwargs):
        if type(request).__name__ == "StartBotRequest":
            self.refused += 1
            raise self.error
        return await super().__call__(request, **kwargs)


class HangingClient(FakeClient):
    """Presses fine, then never gets an answer out of the bot's chat."""

    async def get_messages(self, peer, limit=None, min_id=0, ids=None):
        await asyncio.sleep(3600)


class HardeningCase(ClaimerCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        check_claimer._cooldown_until.clear()
        check_claimer._bot_peers.clear()
        check_claimer._expired_cards.clear()
        check_claimer.stats.update(pressed=0, handoffs=0, timeouts=0, outcomes={}, last_error="", last_error_at=None)

    def replace_a(self, client):
        state.clients[state.clients.index(self.a)] = client
        self.a = client


class HandOffTests(HardeningCase):
    async def test_a_flood_waiting_account_hands_the_check_to_the_next_one(self):
        self.replace_a(RefusingClient("w3v8f0rm", self.script, error=FloodWait(120)))
        self.see(self.a, post("🚀 Чек на 5 USDT (5.0$)"))
        await self.settle()
        self.assertEqual((self.a.refused, self.b.starts), (1, ["mc_Gen1"]))
        rows = await self.rows()
        self.assertEqual([(r["session"], r["outcome"]) for r in rows], [("Swight0", "claimed")])
        self.assertEqual(check_claimer.stats["handoffs"], 1)
        self.assertIn("w3v8f0rm", check_claimer.stats_snapshot()["cooling"])

        # While it waits, the next check A sees goes straight to B: no press is spent on A.
        self.see(self.a, post("🚀 Чек на 1 USDT", "mc_Gen2", msg_id=11))
        await self.settle()
        self.assertEqual((self.a.refused, self.b.starts), (1, ["mc_Gen1", "mc_Gen2"]))

    async def test_a_timed_out_press_is_not_handed_on(self):
        # It may have reached the bot: a second account would be a second press.
        self.replace_a(RefusingClient("w3v8f0rm", self.script, error=asyncio.TimeoutError()))
        self.see(self.a, post("🚀 Чек на 5 USDT (5.0$)"))
        await self.settle()
        self.assertEqual(self.b.starts, [])
        self.assertEqual([(r["session"], r["outcome"]) for r in await self.rows()], [("w3v8f0rm", "error")])

    async def test_a_personal_check_has_nobody_to_hand_to(self):
        self.replace_a(RefusingClient("w3v8f0rm", self.script, error=FloodWait(30)))
        self.see(self.b, post("🚀 Чек на 0.1 USDT (0.1$) для @MCshinra", "mc_P1"))
        await self.settle()
        self.assertEqual((self.a.refused, self.b.starts), (1, []))
        self.assertEqual([r["outcome"] for r in await self.rows()], ["error"])

    async def test_hand_offs_are_capped(self):
        state.accounts_state["third"] = {"status": "online", "username": "third", "display": "@third"}
        third = RefusingClient("third", self.script, error=ConnectionError("dropped"))
        state.clients.append(third)
        self.replace_a(RefusingClient("w3v8f0rm", self.script, error=ConnectionError("dropped")))
        state.clients[state.clients.index(self.b)] = RefusingClient("Swight0", self.script,
                                                                    error=ConnectionError("dropped"))
        self.see(self.a, post("🚀 Чек на 5 USDT (5.0$)"))
        await self.settle()
        pressed_by = [client.refused for client in state.clients]
        self.assertEqual(sum(pressed_by), 1 + check_claimer.MAX_HANDOFFS)
        self.assertEqual([r["outcome"] for r in await self.rows()], ["error"])

    async def test_a_failed_press_drops_the_cached_peer(self):
        self.replace_a(RefusingClient("w3v8f0rm", self.script, error=ValueError("PEER_ID_INVALID")))
        check_claimer._bot_peers[("w3v8f0rm", "xrocket")] = SimpleNamespace(user_id=1)
        with patch.object(check_claimer, "_next_taker", return_value=None):
            self.see(self.a, post("🚀 Чек на 5 USDT (5.0$)"))
            await self.settle()
        self.assertNotIn(("w3v8f0rm", "xrocket"), check_claimer._bot_peers)


class TimeoutTests(HardeningCase):
    only_a = True

    async def test_a_silent_bot_chat_ends_the_claim_and_frees_the_lock(self):
        self.replace_a(HangingClient("w3v8f0rm", self.script))
        with patch.object(check_claimer, "CONVERSATION_TIMEOUT_SECONDS", 0.1), \
                patch.object(check_claimer, "GET_TIMEOUT_SECONDS", 5):
            self.see(self.a, post("🚀 Чек на 5 USDT (5.0$)"))
            await asyncio.wait_for(self.settle(), 3)
            self.assertFalse(check_claimer._lock_for("w3v8f0rm", "xrocket").locked())
        (row,) = await self.rows()
        self.assertEqual(row["outcome"], "error")
        self.assertIn("не ответил вовремя", row["reply"])
        self.assertEqual(check_claimer.stats["timeouts"], 1)

    async def test_one_slow_read_is_a_missed_poll_not_a_failed_claim(self):
        slow = {"left": 1}
        real_get = self.a.get_messages

        async def flaky_get(peer, **kwargs):
            if slow["left"]:
                slow["left"] -= 1
                await asyncio.sleep(1)
            return await real_get(peer, **kwargs)

        self.a.get_messages = flaky_get
        with patch.object(check_claimer, "GET_TIMEOUT_SECONDS", 0.05), \
                patch.object(check_claimer, "REPLY_TIMEOUT_SECONDS", 1.0):
            self.see(self.a, post("🚀 Чек на 5 USDT (5.0$)"))
            await self.settle()
        self.assertEqual([r["outcome"] for r in await self.rows()], ["claimed"])


def captcha_script(kind, value):
    from test_check_claimer import Btn

    if kind == "start":
        return [{"text": "Решите капчу", "buttons": [[Btn("🍎", data=b"1"), Btn("🍌", data=b"2")]]}]
    return []


class RelayCardTests(HardeningCase):
    only_a = True
    script = staticmethod(captcha_script)

    async def test_an_expired_relay_card_loses_its_dead_buttons(self):
        self.notify.return_value = 555  # the card's message id (want_id=True)
        self.see(self.a, post("🚀 Чек на 5 USDT"))
        await self.settle()
        self.assertEqual(self.notify.await_args.kwargs.get("want_id"), True)
        (relay,) = state.check_relays.values()
        self.assertEqual(relay["card_id"], 555)

        bot = SimpleNamespace(edit_message=AsyncMock())
        with patch.object(state, "bot_client", bot):
            self.assertEqual(check_claimer.sweep_relays(datetime.now() + timedelta(hours=1)), 1)
            self.assertEqual(await check_claimer.close_expired_cards(), 1)
        chat, card_id, text = bot.edit_message.await_args.args
        self.assertEqual((chat, card_id), (5, 555))
        self.assertIn("закрыт", text)
        buttons = bot.edit_message.await_args.kwargs["buttons"] or []
        self.assertFalse(any(getattr(b, "data", None) for row in buttons for b in row))

    async def test_a_hanging_relay_press_reports_instead_of_hanging(self):
        self.see(self.a, post("🚀 Чек на 5 USDT"))
        await self.settle()
        (token,) = state.check_relays
        self.replace_a(HangingClient("w3v8f0rm", self.script))
        with patch.object(check_claimer, "RELAY_TIMEOUT_SECONDS", 0.1):
            with self.assertRaisesRegex(RuntimeError, "не ответил вовремя"):
                await asyncio.wait_for(check_claimer.relay_press(token, 0, 0), 3)
        self.assertFalse(check_claimer._lock_for("w3v8f0rm", "xrocket").locked())


class NoiseTests(HardeningCase):
    only_a = True

    async def test_known_non_checks_are_not_logged_as_unreadable(self):
        invoice = post("Пополнение счёта для игрока на сумму 2$", "inv_NZK3mUo0a7VABOn", label="Оплатить")
        referral = post("15.00💵 на CryptoBot / XRocket", "i_l0EMspDVxB", msg_id=12)
        with self.assertNoLogs(check_claimer.logger, logging.INFO):
            self.see(self.a, invoice)
            self.see(self.a, referral)
        self.assertTrue(cc.known_not_check("http://t.me/send?start=r-g13rh"))
        self.assertFalse(cc.known_not_check("https://t.me/send?start=CQ123"))
        # xRocket's i_… is a referral, never pressed even in a post that says «чек».
        self.assertIsNone(cc.find_check(post("Чек! t.me/xrocket?start=i_2Hx9", "i_2Hx9")))
