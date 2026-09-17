"""Manual wins (/win) and the value of unclaimed prizes."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk import manual_win, prize_value  # noqa: E402

SNAPSHOT = {
    "tether": {"usd": 1.0, "uah": 41.0},
    "the-open-network": {"usd": 3.0, "uah": 123.0},
    "_fiat": {"UAH": 41.0, "USD": 1.0},
}


class LinkTests(unittest.TestCase):
    def test_public_link(self):
        ref = manual_win.parse_message_link("https://t.me/PromoFrag/1234")
        self.assertEqual((ref.peer, ref.message_id), ("PromoFrag", 1234))

    def test_topic_link_takes_the_message(self):
        ref = manual_win.parse_message_link("t.me/somechat/77/1234")
        self.assertEqual((ref.peer, ref.message_id), ("somechat", 1234))

    def test_private_link(self):
        ref = manual_win.parse_message_link("https://t.me/c/1987654321/55")
        self.assertEqual((ref.peer, ref.message_id), (-1001987654321, 55))
        self.assertEqual(ref.link, "https://t.me/c/1987654321/55")

    def test_not_a_link(self):
        self.assertIsNone(manual_win.parse_message_link("hello"))

    def test_accounts_split_known_and_unknown(self):
        found, unknown = manual_win.parse_accounts(["@MuverGT", "stranger", "@muvergt"], ["MuverGT", "Swight0"])
        self.assertEqual(found, ["MuverGT"])
        self.assertEqual(unknown, ["stranger"])

    def test_record_without_readable_message(self):
        ref = manual_win.parse_message_link("https://t.me/c/1987654321/55")
        record = manual_win.build_manual_record(ref, None, ["MuverGT"])
        self.assertTrue(record["is_win"])
        self.assertEqual(record["mentions"], ["MuverGT"])
        self.assertEqual(record["chat_id"], -1001987654321)
        self.assertEqual(record["chat_type"], "channel")
        self.assertIn(manual_win.MANUAL_NOTE, record["note"])

    def test_record_merges_post_mentions(self):
        ref = manual_win.parse_message_link("https://t.me/chan/9")
        base = {"mentions": ["Swight0"], "chat": "Chan", "chat_id": -100, "message_id": 9,
                "text": "Победители: @Swight0", "chat_type": "private"}
        record = manual_win.build_manual_record(ref, base, ["MuverGT"])
        self.assertEqual(record["mentions"], ["Swight0", "MuverGT"])
        self.assertEqual(record["chat_type"], "channel")
        self.assertEqual(record["text"], "Победители: @Swight0")


class NotTextualWinMarkersTests(unittest.TestCase):
    """reconcile_win_flags recognises these wins by text markers; keep them in sync."""

    def test_markers_match_what_the_writers_produce(self):
        from database.giveaways import MANUAL_WIN_MARK, TEXTLESS_CARD_PREFIX
        from pulse_desk.global_search import textless_card_text

        self.assertTrue(textless_card_text(["Alpha"]).startswith(TEXTLESS_CARD_PREFIX))
        self.assertIn(MANUAL_WIN_MARK, manual_win.MANUAL_NOTE)


class PrizeParsingTests(unittest.TestCase):
    def test_real_post_shapes(self):
        self.assertEqual(prize_value.parse_prizes("4.00 USDT 💵 x 4 на 💲 Crypto"), [(4.0, "USDT")])
        self.assertEqual(prize_value.parse_prizes("1💵 300cек (Двоим)"), [(1.0, "USD")])
        self.assertEqual(prize_value.parse_prizes("Конкурс на 5$"), [(5.0, "USD")])
        self.assertEqual(prize_value.parse_prizes("$5 приз"), [(5.0, "USD")])
        self.assertEqual(prize_value.parse_prizes("100 грн"), [(100.0, "UAH")])

    def test_minutes_after_a_price_are_not_dollars(self):
        # «4$ 15мин» used to read the `$` twice, as 4 and as $15.
        self.assertEqual(prize_value.parse_prizes("4$ 15мин (двоим)"), [(4.0, "USD")])

    def test_no_currency_no_prize(self):
        self.assertEqual(prize_value.parse_prizes("5 победителей, итоги 07.09.20:00, 2 часа"), [])
        self.assertEqual(prize_value.parse_prizes("AWP | Капилляры 🔥 2 победителя"), [])
        self.assertEqual(prize_value.parse_prizes("not 5 not"), [])

    def test_usd_uses_the_snapshot(self):
        self.assertAlmostEqual(prize_value.prize_usd("3 TON", SNAPSHOT), 9.0)
        self.assertIsNone(prize_value.prize_usd("3 TON", None))
        self.assertIsNone(prize_value.prize_usd("скин AWP", SNAPSHOT))

    def test_value_by_account(self):
        rows = [
            {"mentions": ["MuverGT"], "text": "5 USDT"},
            {"mentions": ["MuverGT", "Swight0"], "text": "2$"},
            {"mentions": ["Swight0"], "text": "AWP | Азимов"},
            {"mentions": ["stranger"], "text": "100$"},
        ]
        values = prize_value.value_by_account(rows, ["MuverGT", "Swight0"], SNAPSHOT)
        self.assertAlmostEqual(values["MuverGT"]["usd"], 7.0)
        self.assertEqual(values["Swight0"]["priced"], 1)
        self.assertEqual(values["Swight0"]["unpriced"], 1)
        self.assertNotIn("stranger", values)
        total = prize_value.totals(values)
        self.assertAlmostEqual(total["usd"], 9.0)

    def test_money_lines(self):
        from pulse_desk.bot.sections.debts import money_lines

        values = {"MuverGT": {"usd": 7.0, "priced": 2, "unpriced": 0},
                  "Swight0": {"usd": 2.0, "priced": 1, "unpriced": 1}}
        text = "\n".join(money_lines(values, SNAPSHOT))
        self.assertIn("$9.00", text)
        self.assertIn("₴369", text)
        self.assertIn("@MuverGT", text)
        self.assertIn("без оценки", text)
        self.assertEqual(money_lines({}, SNAPSHOT), [])


if __name__ == "__main__":
    unittest.main()
