"""The claimer end to end, on Telethon stand-ins: who presses, what the bot said."""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

import database  # noqa: E402
from pulse_desk import bot_notify, check_claimer  # noqa: E402
from pulse_desk import check_claims as cc  # noqa: E402
from pulse_desk.app_ctx import state  # noqa: E402

BOT_ID = 4242
STATE_FIELDS = {
    "check_claim_cfg": dict, "check_bot_ids": dict, "check_seen": OrderedDict, "check_chat_codes": OrderedDict,
    "own_check_codes": OrderedDict, "dead_check_codes": OrderedDict, "check_awaiting_password": dict, "own_admin_chat_ids": set, "check_relays": dict,
    "connected_user_ids": set, "accounts_state": dict, "clients": list, "session_names": list,
}


class Btn:
    def __init__(self, text: str, data: bytes | None = None, url: str | None = None):
        self.text, self.data, self.url = text, data, url


class BotMessage:
    """A message in the wallet bot's private chat, as one account sees it."""

    def __init__(self, client, msg_id, out, text, buttons=None, photo=None):
        self._client, self.id, self.out, self.raw_text = client, msg_id, out, text
        self.buttons = buttons
        self.reply_markup = SimpleNamespace(rows=[SimpleNamespace(buttons=row) for row in buttons]) if buttons else None
        self.entities = []
        self.photo, self.edit_date = photo, None

    async def click(self, i, j):
        self._client.clicks.append((self.id, i, j))
        self._client.react("click", self.buttons[i][j].text)
        return SimpleNamespace(message="")


class FakeClient:
    """One account: records requests and plays a scripted wallet bot."""

    def __init__(self, session, script=None, *, deferred=False):
        self._session_name_custom = session
        self.script = script or (lambda kind, value: [])
        self.requests, self.clicks, self.texts, self.chat = [], [], [], []
        self._next_id = 100
        self.deferred, self._owed = deferred, []

    @property
    def starts(self):
        return [r.start_param for r in self.requests if type(r).__name__ == "StartBotRequest"]

    def add(self, out, text, buttons=None, photo=None):
        self._next_id += 1
        message = BotMessage(self, self._next_id, out, text, buttons, photo)
        message.date = datetime.now(timezone.utc)
        self.chat.append(message)
        return message

    def react(self, kind, value):
        for spec in self.script(kind, value) or []:
            if self.deferred:
                self._owed.append(spec)
            else:
                self.add(False, spec.get("text", ""), spec.get("buttons"), spec.get("photo"))

    async def get_input_entity(self, username):
        return SimpleNamespace(user_id=BOT_ID, username=username)

    async def __call__(self, request):
        self.requests.append(request)
        if type(request).__name__ == "StartBotRequest":
            mine = self.add(True, f"/start {request.start_param}")
            self.react("start", request.start_param)
            return SimpleNamespace(updates=[SimpleNamespace(message=mine)])
        return SimpleNamespace()

    async def get_messages(self, peer, limit=None, min_id=0, ids=None):
        while self._owed:
            spec = self._owed.pop(0)
            self.add(False, spec.get("text", ""), spec.get("buttons"), spec.get("photo"))
        if ids is not None:
            return next((m for m in self.chat if m.id == ids), None)
        found = sorted((m for m in self.chat if m.id > (min_id or 0)), key=lambda m: m.id, reverse=True)
        return found[:limit] if limit else found

    async def send_message(self, peer, text):
        self.texts.append(text)
        mine = self.add(True, text)
        self.react("text", text)
        return mine

    async def download_media(self, message, file=None):
        return b"picture"


CHAT = SimpleNamespace(title="промодрочь 2015", username="ludka2k33")


def post(text, code="mc_Gen1", *, sender_id=777, out=False, age=timedelta(0), msg_id=10, label=None):
    button = SimpleNamespace(text=label or "Получить", url=f"https://t.me/xrocket?start={code}")
    return SimpleNamespace(
        raw_text=text, reply_markup=SimpleNamespace(rows=[SimpleNamespace(buttons=[button])]), entities=[],
        out=out, sender_id=sender_id, date=datetime.now(timezone.utc) - age, chat_id=-1002198600083,
        id=msg_id, is_private=False, chat=CHAT,
    )


def claimed(kind, value):
    return [{"text": "✅ Вы получили 5 USDT"}] if kind == "start" else []


class ClaimerCase(unittest.IsolatedAsyncioTestCase):
    script = staticmethod(claimed)
    only_a = False  # Swight0 switched off: one account, one bot chat to assert on

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_path = database.DB_PATH
        database.DB_PATH = Path(self._tmp.name) / "checks.db"
        await database.init_db()
        self.notify = AsyncMock(return_value=True)
        self._patches = [
            patch.object(check_claimer, "POLL_FIRST_DELAY", 0),
            patch.object(check_claimer, "SETTLE_SECONDS", 0),
            patch.object(check_claimer, "JOIN_PAUSE_SECONDS", 0),
            patch.object(check_claimer, "REPLY_TIMEOUT_SECONDS", 0.05),
            patch.object(check_claimer, "CLICK_TIMEOUT_SECONDS", 0.05),
            patch.object(check_claimer, "ADMIN_ID", 5),
            patch.object(bot_notify, "send_admin_bot_message", new=self.notify),
        ]
        for item in self._patches:
            item.start()
        self._saved = {name: getattr(state, name) for name in STATE_FIELDS}
        for name, factory in STATE_FIELDS.items():
            setattr(state, name, factory())
        state.check_claim_cfg = cc.normalize_config({"disabled": ["Swight0"]} if self.only_a else None)
        state.connected_user_ids.update({1, 2})
        state.accounts_state.update({
            "w3v8f0rm": {"status": "online", "username": "MCshinra", "display": "@MCshinra"},
            "Swight0": {"status": "online", "username": "Megatronus_praim", "display": "@Megatronus_praim"},
        })
        self.a = FakeClient("w3v8f0rm", self.script)
        self.b = FakeClient("Swight0", self.script)
        state.clients.extend([self.a, self.b])

    async def asyncTearDown(self):
        for item in self._patches:
            item.stop()
        for name, value in self._saved.items():
            setattr(state, name, value)
        database.DB_PATH = self._old_path
        self._tmp.cleanup()

    def see(self, client, message):
        check_claimer.on_message(client, client._session_name_custom,
                                 state.accounts_state[client._session_name_custom], message)

    async def settle(self):
        while True:
            tasks = [task for name, task in list(state.background_tasks.items()) if name.startswith("check-")]
            if not tasks:
                return
            await asyncio.gather(*tasks)

    async def rows(self):
        return await database.get_recent_check_claims(50)


class WhoPressesTests(ClaimerCase):
    async def test_general_check_is_pressed_by_every_account_that_sees_it(self):
        message = post("🚀 Чек на 5 USDT (5.0$)")
        self.see(self.a, message)
        self.see(self.b, message)
        await self.settle()
        self.assertEqual(self.a.starts, ["mc_Gen1"])
        self.assertEqual(self.b.starts, ["mc_Gen1"])
        self.assertEqual(sorted(r["outcome"] for r in await self.rows()), ["claimed", "claimed"])
        self.assertEqual(self.notify.await_count, 2)
        self.assertIn("Чек забран", self.notify.await_args.args[0])

    async def test_channel_post_of_a_stranger_is_pressed_by_every_account(self):
        # t.me/DRUNK_BONUS/12246: posted in the channel's own name, all accounts subscribed.
        message = post("‍🚀 Чек на 5 USDT (5.0$)", "t_Drunk5Usdt123", sender_id=-1002231021453,
                       label="Получить 5 USDT")
        self.see(self.a, message)
        self.see(self.b, message)
        await self.settle()
        self.assertEqual((self.a.starts, self.b.starts), (["t_Drunk5Usdt123"], ["t_Drunk5Usdt123"]))

    async def test_personal_check_goes_to_the_addressee_whoever_received_it(self):
        self.see(self.b, post("🚀 Чек на 0.1 USDT (0.1$) для @MCshinra", "mc_P1"))
        await self.settle()
        self.assertEqual(self.a.starts, ["mc_P1"])
        self.assertEqual(self.b.starts, [])

    async def test_personal_check_for_a_stranger_is_left_alone(self):
        self.see(self.a, post("🚀 Чек на 0.1 USDT для @someone_else", "mc_P2"))
        await self.settle()
        self.assertEqual(self.a.starts + self.b.starts, [])

    async def test_repeated_or_edited_post_is_pressed_once(self):
        message = post("🚀 Чек на 5 USDT")
        self.see(self.a, message)
        self.see(self.a, message)
        await self.settle()
        self.assertEqual(self.a.starts, ["mc_Gen1"])

    async def test_stale_post_is_ignored(self):
        self.see(self.a, post("🚀 Чек на 5 USDT", age=timedelta(minutes=10)))
        await self.settle()
        self.assertEqual(self.a.starts + self.b.starts, [])

    async def test_post_the_bot_marked_as_used_is_not_pressed(self):
        message = post("🚀 Чек на 5 USDT\n\n✅ Чек активирован")
        message.via_bot_id = 5014831088
        self.see(self.a, message)
        await self.settle()
        self.assertEqual(self.a.starts + self.b.starts, [])

    async def test_a_used_up_check_is_not_pressed_again_by_anyone(self):
        self.a.script = self.b.script = lambda kind, value: (
            [{"text": "❌ Этот чек уже активирован"}] if kind == "start" else [])
        state.check_claim_cfg = cc.normalize_config({"disabled": ["Swight0"]})
        self.see(self.a, post("🚀 Чек на 5 USDT"))
        await self.settle()
        state.check_claim_cfg = cc.normalize_config(None)  # Swight0 back on, the post seen again
        self.see(self.b, post("🚀 Чек на 5 USDT"))
        await self.settle()
        self.assertEqual((self.a.starts, self.b.starts), (["mc_Gen1"], []))

    async def test_a_restart_does_not_press_what_was_pressed_before_it(self):
        self.see(self.a, post("🚀 Чек на 5 USDT"))
        await self.settle()
        self.assertEqual(len(self.a.starts), 1)
        state.check_seen.clear()  # the restart: in-memory marks are gone…
        await check_claimer.load()  # …and come back from the journal
        self.see(self.a, post("🚀 Чек на 5 USDT"))  # xRocket edits the post: a fresh update
        await self.settle()
        self.assertEqual((len(self.a.starts), len(self.b.starts)), (1, 1))

    async def test_personal_check_for_us_waits_for_days(self):
        # Sent at night while the PC was off: nobody else can take it.
        self.see(self.b, post("🚀 Чек на 0.1 USDT для @MCshinra", "mc_Night", age=timedelta(hours=9)))
        await self.settle()
        self.assertEqual(self.a.starts, ["mc_Night"])

    async def test_catch_up_presses_a_personal_check_nobody_tried(self):
        check_claimer.on_message(self.a, "w3v8f0rm", state.accounts_state["w3v8f0rm"],
                                 post("🚀 Чек на 0.1 USDT для @MCshinra", "mc_Cu1", age=timedelta(hours=3)),
                                 live=False)
        await self.settle()
        self.assertEqual(self.a.starts, ["mc_Cu1"])

    async def test_catch_up_does_not_repeat_a_code_tried_before_a_restart(self):
        await database.record_check_claim({"bot": "xrocket", "code": "mc_Cu2", "session": "w3v8f0rm",
                                           "outcome": "claimed"})
        check_claimer.on_message(self.a, "w3v8f0rm", state.accounts_state["w3v8f0rm"],
                                 post("🚀 Чек на 0.1 USDT для @MCshinra", "mc_Cu2", age=timedelta(hours=3)),
                                 live=False)
        await self.settle()
        self.assertEqual(self.a.starts, [])

    async def test_disabled_account_does_not_press(self):
        state.check_claim_cfg = cc.normalize_config({"disabled": ["Swight0"]})
        message = post("🚀 Чек на 5 USDT")
        self.see(self.a, message)
        self.see(self.b, message)
        await self.settle()
        self.assertEqual((self.a.starts, self.b.starts), (["mc_Gen1"], []))

    async def test_off_does_nothing(self):
        state.check_claim_cfg = cc.normalize_config({"mode": "off"})
        self.see(self.a, post("🚀 Чек на 5 USDT"))
        await self.settle()
        self.assertEqual(self.a.starts, [])
        self.assertEqual(await self.rows(), [])

    async def test_watch_announces_once_and_presses_nothing(self):
        state.check_claim_cfg = cc.normalize_config({"mode": "watch"})
        message = post("🚀 Чек на 5 USDT")
        self.see(self.a, message)
        self.see(self.b, message)
        await self.settle()
        self.assertEqual(self.a.starts + self.b.starts, [])
        self.notify.assert_awaited_once()
        self.assertIn("Поймал бы", self.notify.await_args.args[0])


class SpeedTests(ClaimerCase):
    async def test_first_account_to_see_a_general_check_presses_for_all(self):
        self.see(self.a, post("🚀 Чек на 5 USDT"))  # B's copy of the update has not come yet
        await self.settle()
        self.assertEqual((self.a.starts, self.b.starts), (["mc_Gen1"], ["mc_Gen1"]))
        rows = await self.rows()
        self.assertTrue(all(r["press_ms"] is not None for r in rows))

    async def test_fan_out_still_skips_switched_off_accounts(self):
        state.check_claim_cfg = cc.normalize_config({"disabled": ["Swight0"]})
        self.see(self.a, post("🚀 Чек на 5 USDT"))
        await self.settle()
        self.assertEqual(self.b.starts, [])

    async def test_back_to_back_checks_are_both_pressed_before_any_answer_and_not_mixed_up(self):
        def script(kind, value):
            if kind != "start":
                return []
            return [{"text": "✅ Вы получили 5 USDT" if value == "mc_First" else "❌ Этот чек уже активирован"}]

        self.a.script, self.a.deferred = script, True
        state.check_claim_cfg = cc.normalize_config({"disabled": ["Swight0"]})
        self.see(self.a, post("🚀 Чек на 5 USDT", "mc_First", msg_id=11))
        self.see(self.a, post("🚀 Чек на 5 USDT", "mc_Second", msg_id=12))
        await self.settle()
        # Both presses went out before the bot answered either of them…
        self.assertEqual([m.out for m in self.a.chat[:2]], [True, True])
        # …and each answer was read as its own check's.
        outcomes = {r["code"]: r["outcome"] for r in await self.rows()}
        self.assertEqual(outcomes, {"mc_First": "claimed", "mc_Second": "gone"})


class OwnChecksTests(ClaimerCase):
    async def test_check_from_one_of_our_accounts_is_never_pressed(self):
        message = post("🚀 Чек на 5 USDT", sender_id=1)
        self.see(self.a, message)
        self.see(self.b, message)
        await self.settle()
        self.assertEqual(self.a.starts + self.b.starts, [])
        rows = await self.rows()
        self.assertEqual([(r["session"], r["outcome"]) for r in rows], [("", "own")])

    async def test_check_from_the_owner_is_never_pressed(self):
        self.see(self.a, post("🚀 Чек на 5 USDT", sender_id=5))
        await self.settle()
        self.assertEqual(self.a.starts, [])

    async def test_our_post_as_a_channel_is_recognised_by_the_others(self):
        # Account A posted it as its channel: A sees `out`, B sees the channel.
        self.see(self.a, post("🚀 Чек на 5 USDT", sender_id=-100777, out=True))
        self.see(self.b, post("🚀 Чек на 5 USDT", sender_id=-100777))
        await self.settle()
        self.assertEqual(self.a.starts + self.b.starts, [])

    async def test_post_in_the_name_of_a_chat_we_administer_is_own(self):
        state.own_admin_chat_ids.add(-100777)
        self.see(self.b, post("🚀 Чек на 5 USDT", sender_id=-100777))
        await self.settle()
        self.assertEqual(self.b.starts, [])

    async def test_check_created_in_the_wallet_bot_chat_is_own(self):
        state.check_bot_ids[BOT_ID] = "xrocket"
        created = SimpleNamespace(raw_text="Чек создан: t.me/xrocket?start=mc_Mine1", reply_markup=None,
                                  entities=[], out=False, sender_id=BOT_ID, is_private=True,
                                  date=datetime.now(timezone.utc), chat_id=BOT_ID, id=5)
        self.see(self.a, created)
        self.see(self.b, post("🚀 Чек на 5 USDT", "mc_Mine1"))
        await self.settle()
        self.assertEqual(self.a.starts + self.b.starts, [])


def subscribe_script(kind, value):
    if kind == "start":
        return [{"text": "Чтобы получить чек, подпишитесь на каналы:",
                 "buttons": [[Btn("📢 Канал", url="https://t.me/digrentg")],
                             [Btn("✅ Проверить подписку", data=b"chk")]]}]
    if kind == "click":
        return [{"text": "✅ Вы получили 5 USDT"}]
    return []


class SubscribeTests(ClaimerCase):
    only_a = True
    script = staticmethod(subscribe_script)

    async def test_subscription_is_met_then_the_bot_checks_again(self):
        self.see(self.a, post("🚀 Чек на 5 USDT"))
        await self.settle()
        joins = [r for r in self.a.requests if type(r).__name__ == "JoinChannelRequest"]
        self.assertEqual([r.channel for r in joins], ["digrentg"])
        self.assertEqual([(i, j) for _mid, i, j in self.a.clicks], [(1, 0)])
        self.assertEqual([r["outcome"] for r in await self.rows()], ["claimed"])


def password_script(kind, value):
    if kind == "start":
        return [{"text": "Введите пароль от чека"}]
    if kind == "text" and value == "kotik42":
        return [{"text": "✅ Вы получили 3 USDT"}]
    return []


class PasswordTests(ClaimerCase):
    only_a = True
    script = staticmethod(password_script)

    async def test_password_written_in_the_post_is_typed_in(self):
        self.see(self.a, post("Мультичек на 3 USDT, пароль: kotik42", "mc_Pw1"))
        await self.settle()
        self.assertEqual(self.a.texts, ["kotik42"])
        self.assertEqual([r["outcome"] for r in await self.rows()], ["claimed"])

    async def test_password_nobody_wrote_goes_to_the_owner(self):
        self.see(self.a, post("Мультичек на 3 USDT", "mc_Pw2"))
        await self.settle()
        self.assertEqual(self.a.texts, [])
        self.assertIn("пароль", self.notify.await_args.args[0])
        self.assertEqual(len(state.check_relays), 1)


def captcha_script(kind, value):
    if kind == "start":
        return [{"text": "Для получения чека решите капчу", "photo": object(),
                 "buttons": [[Btn("🍎", data=b"1"), Btn("🍌", data=b"2")]]}]
    if kind == "click":
        return [{"text": "✅ Вы получили 5 USDT"}] if value == "🍌" else [{"text": "❌ Неверно"}]
    return []


class CaptchaRelayTests(ClaimerCase):
    script = staticmethod(captcha_script)

    async def test_captcha_is_handed_to_the_owner_and_the_press_is_mirrored(self):
        state.check_claim_cfg = cc.normalize_config({"disabled": ["Swight0"]})
        self.see(self.a, post("🚀 Чек на 5 USDT"))
        await self.settle()
        self.notify.assert_awaited_once()
        text = self.notify.await_args.args[0]
        buttons = self.notify.await_args.kwargs["buttons"]
        self.assertIn("капча", text)
        self.assertIsNotNone(self.notify.await_args.kwargs["file"])  # the picture is re-sent
        (token,) = state.check_relays
        self.assertEqual([b.data for b in buttons[0]], [f"ck:b:{token}:0:0".encode(), f"ck:b:{token}:0:1".encode()])

        outcome, _reply, _action = await check_claimer.relay_press(token, 0, 1)
        self.assertEqual(outcome, "claimed")
        self.assertEqual([(i, j) for _mid, i, j in self.a.clicks], [(0, 1)])
        self.assertEqual([r["outcome"] for r in await self.rows()], ["claimed"])

    async def test_second_account_on_the_same_captcha_waits_in_the_queue(self):
        message = post("🚀 Чек на 5 USDT")
        self.see(self.a, message)
        self.see(self.b, message)
        await self.settle()
        self.notify.assert_awaited_once()
        (relay,) = state.check_relays.values()
        self.assertEqual(len(relay["queue"]), 1)

        fresh = await check_claimer.relay_next(relay["token"])
        self.assertEqual(fresh["step"]["session"], {"w3v8f0rm", "Swight0"}.difference({relay["step"]["session"]}).pop())
        self.assertEqual(self.notify.await_count, 2)
        self.assertEqual(list(state.check_relays), [fresh["token"]])

    async def test_expired_relays_are_swept(self):
        state.check_claim_cfg = cc.normalize_config({"disabled": ["Swight0"]})
        self.see(self.a, post("🚀 Чек на 5 USDT"))
        await self.settle()
        self.assertEqual(check_claimer.sweep_relays(datetime.now() + timedelta(hours=1)), 1)
        self.assertEqual(state.check_relays, {})


def pay_script(kind, value):
    if kind == "start":
        return [{"text": "Для получения чека решите капчу",
                 "buttons": [[Btn("🍎", data=b"1"), Btn("💸 Оплатить 5 USDT", data=b"pay")]]}]
    return []


class MoneyOutTests(ClaimerCase):
    only_a = True
    script = staticmethod(pay_script)

    async def test_payment_buttons_never_reach_the_card_or_the_bot(self):
        self.see(self.a, post("🚀 Чек на 5 USDT"))
        await self.settle()
        (token,) = state.check_relays
        labels = [b.text for row in self.notify.await_args.kwargs["buttons"] for b in row]
        self.assertIn("🍎", labels)
        self.assertFalse(any("Оплатить" in label for label in labels))
        # A forged callback aimed at the payment button is refused before any press.
        with self.assertRaises(RuntimeError):
            await check_claimer.relay_press(token, 0, 1)
        self.assertEqual(self.a.clicks, [])


def invoice_script(kind, value):
    return [{"text": "🧾 Счёт на 5 USDT", "buttons": [[Btn("Оплатить", data=b"pay")]]}] if kind == "start" else []


class InvoiceTests(ClaimerCase):
    only_a = True
    script = staticmethod(invoice_script)

    async def test_an_invoice_behind_a_check_word_stops_without_a_card(self):
        self.see(self.a, post("🚀 Чек на 5 USDT"))
        await self.settle()
        self.assertEqual([r["outcome"] for r in await self.rows()], ["invoice"])
        self.notify.assert_not_awaited()
        self.assertEqual(state.check_relays, {})


def later_password_script(kind, value):
    if kind == "start":
        return [{"text": "Введите пароль от чека", "buttons": [[Btn("❌ Отмена", data=b"x")]]}]
    if kind == "text":
        return [{"text": "✅ Вы получили 3 USDT"}] if value == "kotik42" else [{"text": "Неверный пароль"}]
    return []


class LaterPasswordTests(ClaimerCase):
    only_a = True
    script = staticmethod(later_password_script)

    def follow_up(self, text, sender_id=777):
        return SimpleNamespace(raw_text=text, reply_markup=None, entities=[], out=False, sender_id=sender_id,
                               date=datetime.now(timezone.utc), chat_id=-1002198600083, id=99,
                               is_private=False, chat=CHAT)

    async def test_password_in_the_next_post_is_typed_in(self):
        self.see(self.a, post("Мультичек на 3 USDT, пароль в следующем посте", "mc_Later1"))
        await self.settle()
        self.see(self.a, self.follow_up("Пароль: kotik42"))
        self.see(self.b, self.follow_up("Пароль: kotik42"))  # same update on another account
        await self.settle()
        self.assertEqual(self.a.texts, ["kotik42"])
        self.assertEqual([r["outcome"] for r in await self.rows()], ["claimed"])
        self.assertIn("Чек забран", self.notify.await_args.args[0])

    async def test_bare_password_from_the_author_counts(self):
        self.see(self.a, post("Мультичек на 3 USDT", "mc_Later2"))
        await self.settle()
        self.see(self.a, self.follow_up("kotik42"))
        await self.settle()
        self.assertEqual(self.a.texts, ["kotik42"])

    async def test_someone_elses_message_is_not_a_password(self):
        self.see(self.a, post("Мультичек на 3 USDT", "mc_Later3"))
        await self.settle()
        self.see(self.a, self.follow_up("kotik42", sender_id=555))
        self.see(self.a, self.follow_up("ну и где пароль то"))  # the author, but not a password
        await self.settle()
        self.assertEqual(self.a.texts, [])


class SectionTests(ClaimerCase):
    only_a = True
    script = staticmethod(captcha_script)

    def click(self, data: str):
        from pulse_desk.bot.router import Click

        from _fakes import FakeEvent

        event = FakeEvent(sender_id=5, data=data.encode())
        return event, Click(event=event, data=data, role="admin", perms={}, has_feature=lambda perms, code: True)

    async def test_screen_shows_mode_accounts_and_totals(self):
        from pulse_desk.bot.sections import checks

        text = checks.card(
            cc.normalize_config({"disabled": ["Swight0"]}),
            [("Swight0", "@Megatronus_praim"), ("w3v8f0rm", "@MCshinra")],
            [{"outcome": "claimed", "created_at": "2026-09-23T12:53:16", "amount": "0.1 USDT",
              "account": "@MCshinra", "chat": "промодрочь 2015"}],
            {"outcomes": {"claimed": 1}, "claimed": ["0.1 USDT"]},
            {"outcomes": {"claimed": 1}, "claimed": ["0.1 USDT"]},
        )
        self.assertIn("🎯 Ловить", text)
        self.assertIn("Ловят: 1 из 2", text)
        self.assertIn("Всего забрано: 0.1 USDT", text)
        self.assertIn("✅ забрал · 23.09 12:53 · 0.1 USDT · @MCshinra", text)

    async def test_mode_and_account_switches_are_saved_and_applied(self):
        from pulse_desk.bot.sections import checks

        state.session_names = ["Swight0", "w3v8f0rm"]
        event, click = self.click("ck:m:watch")
        await checks.handle(click)
        self.assertEqual(state.check_claim_cfg["mode"], "watch")
        self.assertEqual((await database.get_setting("check_claim"))["mode"], "watch")
        self.assertIn("Режим: 👀 Смотреть", event.edit_texts[-1])

        event, click = self.click("ck:a:0")
        await checks.handle(click)
        self.assertEqual(state.check_claim_cfg["disabled"], [])
        event, click = self.click("ck:a:0")
        await checks.handle(click)
        self.assertEqual(state.check_claim_cfg["disabled"], ["Swight0"])

    async def test_owner_press_on_the_card_claims_the_check(self):
        from pulse_desk.bot.sections import checks

        self.see(self.a, post("🚀 Чек на 5 USDT"))
        await self.settle()
        (token,) = state.check_relays
        event, click = self.click(f"ck:b:{token}:0:1")
        await checks.handle(click)
        self.assertIn("Чек забран", event.edit_texts[-1])
        self.assertEqual(state.check_relays, {})  # settled: the card closed

    async def test_stale_card_says_so(self):
        from pulse_desk.bot.sections import checks

        event, click = self.click("ck:b:deadbeef:0:0")
        await checks.handle(click)
        self.assertTrue(event.answers[-1]["alert"])


class SilenceTests(ClaimerCase):
    only_a = True
    script = staticmethod(lambda kind, value: [])

    async def test_no_answer_is_journaled_as_an_error_without_a_card(self):
        self.see(self.a, post("🚀 Чек на 5 USDT"))
        await self.settle()
        self.assertEqual([r["outcome"] for r in await self.rows()], ["error"])
        self.notify.assert_not_awaited()


class JournalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_path = database.DB_PATH
        database.DB_PATH = Path(self._tmp.name) / "journal.db"
        await database.init_db()

    async def asyncTearDown(self):
        database.DB_PATH = self._old_path
        self._tmp.cleanup()

    async def test_one_row_per_attempt_and_totals(self):
        row = {"bot": "xrocket", "code": "mc_1", "session": "w3v8f0rm", "account": "@MCshinra",
               "chat_id": -1, "chat": "chat", "message_id": 1, "link": "", "amount": "0.1 USDT"}
        first = await database.record_check_claim({**row, "outcome": "captcha"})
        second = await database.record_check_claim({**row, "outcome": "claimed"})
        self.assertEqual(first, second)
        await database.record_check_claim({**row, "session": "Swight0", "amount": "0.2 USDT", "outcome": "claimed"})
        await database.record_check_claim({**row, "code": "mc_2", "outcome": "gone"})
        stats = await database.get_check_claim_stats()
        self.assertEqual(stats["outcomes"], {"claimed": 2, "gone": 1})
        self.assertEqual(cc.amount_totals(stats["claimed"]), {"USDT": "0.3"})
        # A later «уже активирован» for a code this account already won keeps the win.
        await database.record_check_claim({**row, "outcome": "gone", "reply": "уже активирован"})
        self.assertEqual((await database.get_check_claim_stats())["outcomes"], {"claimed": 2, "gone": 1})
        self.assertTrue(await database.has_check_claim("w3v8f0rm", "xrocket", "mc_1"))
        self.assertFalse(await database.has_check_claim("w3v8f0rm", "xrocket", "mc_404"))
        await database.update_check_claim(first, "gone", "late")
        rows = await database.get_recent_check_claims(10)
        self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main()
