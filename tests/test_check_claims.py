"""Which messages are wallet-bot checks, whose they are, what the bot answered."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk import check_claims as cc  # noqa: E402


def button(text: str, url: str | None = None, data: bytes | None = None):
    return SimpleNamespace(text=text, url=url, data=data)


def message(text: str = "", buttons=None, entities=None, **extra):
    markup = SimpleNamespace(rows=[SimpleNamespace(buttons=row) for row in buttons]) if buttons else None
    base = dict(raw_text=text, reply_markup=markup, entities=entities or [], out=False,
                sender_id=777, date=datetime.now(timezone.utc), chat_id=-100555, id=10)
    base.update(extra)
    return SimpleNamespace(**base)


XROCKET_POST = message(
    "‍🚀 Чек на 0.1\xa0USDT (0.1$) для @MCshinra",
    buttons=[[button("Получить 0.1 USDT", "https://t.me/xrocket?start=mc_AbC123xyz")]],
    entities=[SimpleNamespace(url="https://image.api.xrocket.exchange/image/cheque?coinCode=USDT")],
)


class FindCheckTests(unittest.TestCase):
    def test_xrocket_button_is_a_check(self):
        info = cc.find_check(XROCKET_POST)
        self.assertIsNotNone(info)
        self.assertEqual([(link.bot, link.code) for link in info.links], [("xrocket", "mc_AbC123xyz")])
        self.assertEqual(info.amount, "0.1 USDT")
        self.assertEqual(info.addressee, "mcshinra")

    def test_general_check_has_no_addressee(self):
        post = message("🚀 Чек на 5 USDT (5.0$)",
                       buttons=[[button("Получить 5 USDT", "https://t.me/xrocket?start=mc_Zz9")]])
        info = cc.find_check(post)
        self.assertEqual(info.addressee, "")
        self.assertEqual(info.amount, "5 USDT")

    def test_cryptobot_hidden_link(self):
        post = message("🦋 Чек на 1.5 TON", entities=[SimpleNamespace(url="https://t.me/send?start=CQabcDEF12")])
        info = cc.find_check(post)
        self.assertEqual([(link.bot, link.code) for link in info.links], [("send", "CQabcDEF12")])
        self.assertEqual(info.amount, "1.5 TON")

    def test_cryptobot_alias_and_plain_text_link(self):
        post = message("держите чек t.me/CryptoBot?start=CQqwerty1 успевайте")
        info = cc.find_check(post)
        self.assertEqual([(link.bot, link.code) for link in info.links], [("send", "CQqwerty1")])

    def test_xrocket_transfer_wording_is_a_check(self):
        post = message("Перевод 0.5 USDT для @MCshinra",
                       buttons=[[button("Открыть", "https://t.me/xrocket?start=t_AbCdEfGh1234567")]])
        info = cc.find_check(post)
        self.assertEqual(info.addressee, "mcshinra")
        self.assertTrue(cc.post_is_dead("Перевод 0.5 USDT\nПеревод уже активирован"))

    def test_invoices_are_not_checks(self):
        self.assertIsNone(cc.find_check(message("Чек на оплату t.me/send?start=IVabcdef")))
        self.assertIsNone(cc.find_check(message("Чек t.me/xrocket?start=inv_abcdef")))

    def test_referral_link_in_chatter_is_ignored(self):
        self.assertIsNone(cc.find_check(message("лучший кошелёк https://t.me/xrocket?start=i_2Hx9")))

    def test_claim_label_alone_is_enough(self):
        post = message("💸 Раздача", buttons=[[button("Receive 2 USDT", "https://t.me/send?start=CQ12345")]])
        self.assertIsNotNone(cc.find_check(post))

    def test_other_bots_are_ignored(self):
        self.assertIsNone(cc.find_check(message("🔥 Чек на Казино 1$ : https://t.me/somecasinobot?start=CivUJDeEhFjZ")))

    def test_redcube_multicheck(self):
        post = message("🧾 Мультичек на сумму 3$\n\nСумма одного чека: 0.3$\nКол-во активаций: 10",
                       buttons=[[button("Получить 0.3$", "https://t.me/redcubebetbot?start=CivUJDeEhFjZ")]],
                       via_bot_id=777000)
        info = cc.find_check(post)
        self.assertEqual([(link.bot, link.code) for link in info.links], [("redcube", "CivUJDeEhFjZ")])
        self.assertEqual(info.amount, "0.3 $")
        self.assertFalse(info.dead)
        # A player's profile link is not a check.
        self.assertIsNone(cc.find_check(message("Чек игрока t.me/redcubebetbot?start=U1741523718")))
        self.assertEqual(cc.amount_totals(["0.3 $", "0.3 $"]), {"$": "0.6"})

    def test_same_code_twice_is_one_link(self):
        post = message("Чек https://t.me/xrocket?start=mc_Same1",
                       buttons=[[button("Получить", "https://t.me/xrocket?start=mc_Same1")]])
        self.assertEqual(len(cc.find_check(post).links), 1)

    def test_message_without_links(self):
        self.assertIsNone(cc.find_check(message("просто чек из магазина")))

    def test_password_from_post(self):
        post = message("Мультичек на 3 USDT, пароль: kotik42",
                       buttons=[[button("Получить", "https://t.me/xrocket?start=mc_Pw1")]])
        self.assertEqual(cc.find_check(post).password, "kotik42")
        self.assertEqual(cc.post_password("пароль от чека будет позже"), "")
        self.assertEqual(cc.post_password("🔑 1234"), "1234")
        self.assertEqual(cc.follow_up_password("Пароль: kotik42"), "kotik42")
        self.assertEqual(cc.follow_up_password("  kotik42 "), "kotik42")
        self.assertEqual(cc.follow_up_password("ну и где пароль то"), "")


class OwnerRuleTests(unittest.TestCase):
    def test_own_sender(self):
        own_users, own_chats = {1, 2}, {-1001}
        self.assertTrue(cc.own_sender(1, False, own_users, own_chats))
        self.assertTrue(cc.own_sender(-1001, False, own_users, own_chats))
        self.assertTrue(cc.own_sender(999, True, own_users, own_chats))
        self.assertFalse(cc.own_sender(999, False, own_users, own_chats))
        self.assertFalse(cc.own_sender(-1002, False, own_users, own_chats))
        self.assertFalse(cc.own_sender(None, False, own_users, own_chats))


class ClassifyTests(unittest.TestCase):
    CASES = {
        "✅ Вы получили 0.1 USDT": "claimed",
        "Вы активировали чек и получили 0.1 USDT": "claimed",
        "Получено: 0.1 USDT": "claimed",
        "You received 5 USDT ($5.00).": "claimed",
        "You've received 5 USDT": "claimed",
        "Вы уже активировали этот чек": "gone",
        "Вы не можете получить этот чек": "unknown",
        "Чек успешно активирован": "claimed",
        "❌ Этот чек уже активирован": "gone",
        "Этот перевод уже активирован.": "gone",  # xRocket's real wording, 23.09
        "Нельзя активировать свой перевод": "own",
        "Чек не найден или уже использован": "gone",
        "This check has already been activated.": "gone",
        "Активаций больше нет": "gone",
        "Этот чек предназначен для другого пользователя": "not_for_you",
        "Нельзя активировать свой собственный чек": "own",
        "Чек доступен только пользователям Telegram Premium": "premium",
        "Для получения чека решите капчу": "captcha",
        "Введите пароль от чека": "password",
        "Чтобы получить чек, подпишитесь на каналы:": "subscribe",
        "Выберите язык / Choose language": "unknown",
        "": "unknown",
        "🧾 Счёт на 5 USDT": "invoice",
        "Счёт на оплату от @shop": "invoice",
        "Invoice #123 for 5 USDT": "invoice",
        "✅ Вы получили 5 USDT, счёт пополнен": "claimed",
        # RedCube's real wording, 23.09: a check locked behind a betting turnover.
        "⚠️ Для активации чека, нужен оборот 1 000$ за 1 день. Вам осталось набрать 1 000$": "turnover",
        "Чтобы получить чек, отыграйте 500$": "turnover",
        "Wager 100$ to activate this check": "turnover",
    }

    def test_money_out_labels(self):
        for label in ("Оплатить 5 USDT", "💸 Перевести", "Pay", "Send", "Вывести", "Пополнить", "Сделать ставку"):
            with self.subTest(label=label):
                self.assertTrue(cc.money_out(label))
        for label in ("🍌", "✅ Проверить подписку", "🇷🇺 Русский", "Получить 5 USDT", "Paypal? no — Payload"):
            with self.subTest(label=label):
                self.assertFalse(cc.money_out(label))

    def test_table(self):
        for text, expected in self.CASES.items():
            with self.subTest(text=text):
                self.assertEqual(cc.classify_reply(text), expected)


class JoinTargetsTests(unittest.TestCase):
    def test_targets(self):
        urls = [
            "https://t.me/xrocket",            # the wallet bot itself
            "https://t.me/some_news_bot",      # a bot, cannot be joined
            "https://t.me/digrentg",
            "https://t.me/digrentg",           # duplicate
            "https://t.me/+AbCdEfGh123",
            "https://t.me/joinchat/QwErTy12345",
            "https://t.me/fourth_channel",
            "https://example.com/x",
        ]
        self.assertEqual(cc.join_targets(urls), [
            ("public", "digrentg"), ("invite", "AbCdEfGh123"), ("invite", "QwErTy12345"),
        ])

    def test_recheck_button(self):
        rows = [[button("📢 Канал", url="https://t.me/x")], [button("✅ Проверить подписку", data=b"chk")]]
        self.assertEqual(cc.recheck_button(rows), (1, 0))
        self.assertIsNone(cc.recheck_button([[button("Канал", url="https://t.me/x")]]))
        # «Получить» matches the recheck words, but a payment button is never pressed.
        self.assertIsNone(cc.recheck_button([[button("Оплатить и получить", data=b"pay")]]))


class ConfigAndMiscTests(unittest.TestCase):
    def test_config_defaults_to_claim(self):
        self.assertEqual(cc.normalize_config(None), {"mode": "claim", "disabled": []})
        self.assertEqual(cc.normalize_config({"mode": "watch", "disabled": ["b", "a", "a", ""]}),
                         {"mode": "watch", "disabled": ["a", "b"]})
        self.assertEqual(cc.normalize_config({"mode": "bogus"})["mode"], "claim")

    def test_freshness(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(cc.is_fresh(now - timedelta(minutes=4), now))
        self.assertFalse(cc.is_fresh(now - timedelta(minutes=10), now))
        self.assertTrue(cc.is_fresh(now - timedelta(hours=9), now, personal=True))
        self.assertFalse(cc.is_fresh(now - timedelta(days=2), now, personal=True))
        self.assertTrue(cc.is_fresh(None, now))

    def test_dead_posts(self):
        for text in ("🚀 Чек на 5 USDT\n\n✅ Чек активирован", "Мультичек на 1 TON\nАктивировано: 10/10",
                     "Активаций больше нет", "This cheque has been activated"):
            with self.subTest(text=text):
                self.assertTrue(cc.post_is_dead(text))
        for text in ("🚀 Чек на 5 USDT (5.0$)", "Мультичек на 1 TON\nАктивировано: 3/10",
                     "🚀 Чек на 0.1 USDT (0.1$) для @MCshinra"):
            with self.subTest(text=text):
                self.assertFalse(cc.post_is_dead(text))
        self.assertTrue(cc.post_is_dead("", ["✅ Чек активирован"]))
        self.assertFalse(cc.post_is_dead("", ["Получить 5 USDT"]))

    def test_dead_marker_counts_only_in_a_bots_text(self):
        # A person's comment next to a fresh link must not kill it.
        human = message("прошлый чек закончился, вот новый t.me/xrocket?start=mc_New1")
        self.assertFalse(cc.find_check(human).dead)
        edited = message("🚀 Чек на 5 USDT\n✅ Чек активирован",
                         buttons=[[button("Получить 5 USDT", "https://t.me/xrocket?start=mc_Old1")]],
                         via_bot_id=5014831088)
        self.assertTrue(cc.find_check(edited).dead)

    def test_totals(self):
        self.assertEqual(cc.amount_totals(["0.1 USDT", "0.2 USDT", "1 GRAM", "", "junk"]),
                         {"USDT": "0.3", "GRAM": "1"})


if __name__ == "__main__":
    unittest.main()
