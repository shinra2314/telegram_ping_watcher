"""Bot converter: query parsing, cross rates, fiat fallback, rendering."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pulse_desk.converter import (  # noqa: E402
    Query,
    code_of,
    convert,
    fiat_block,
    fiat_per_usd,
    fmt_amount,
    fmt_money,
    parse_query,
    render_conversion,
    render_converter_home,
    usd_value,
)

NOW = "2026-09-05T10:00:00"

# 1 USD = 41.5 UAH = 0.9 EUR = 90 RUB; BTC $100 000, TON $3, USDT $1.
SNAPSHOT = {
    "fetched_at_iso": NOW,
    "bitcoin": {"usd": 100_000.0, "uah": 4_150_000.0},
    "the-open-network": {"usd": 3.0, "uah": 124.5},
    "tether": {"usd": 1.0, "uah": 41.5},
    "_fiat": {"USD": 1.0, "UAH": 41.5, "EUR": 0.9, "RUB": 90.0},
}

# What the market loop stored before the converter existed: no `_fiat` key.
LEGACY = {k: v for k, v in SNAPSHOT.items() if k != "_fiat"}


class TestCodeOf(unittest.TestCase):
    def test_tickers_are_case_insensitive(self):
        self.assertEqual(code_of("btc"), "BTC")
        self.assertEqual(code_of("UAH"), "UAH")

    def test_symbols(self):
        self.assertEqual(code_of("$"), "USD")
        self.assertEqual(code_of("₴"), "UAH")

    def test_russian_case_forms_resolve_by_stem(self):
        for word in ("гривна", "гривны", "гривен", "грн"):
            self.assertEqual(code_of(word), "UAH", word)
        for word in ("рубль", "рублей", "руб"):
            self.assertEqual(code_of(word), "RUB", word)

    def test_longest_stem_wins(self):
        self.assertEqual(code_of("тонкоин"), "TON")
        self.assertEqual(code_of("биткоин"), "BTC")

    def test_unknown_word(self):
        self.assertIsNone(code_of("завтра"))
        self.assertIsNone(code_of(""))


class TestParseQuery(unittest.TestCase):
    def test_plain_pair(self):
        self.assertEqual(parse_query("100 usd uah"), Query(100.0, "USD", "UAH"))

    def test_filler_words_and_arrows(self):
        self.assertEqual(parse_query("100 usd в грн"), Query(100.0, "USD", "UAH"))
        self.assertEqual(parse_query("1 btc -> uah"), Query(1.0, "BTC", "UAH"))
        self.assertEqual(parse_query("5 ton to usd"), Query(5.0, "TON", "USD"))

    def test_attached_symbol(self):
        self.assertEqual(parse_query("100$ в грн"), Query(100.0, "USD", "UAH"))

    def test_decimal_comma(self):
        self.assertEqual(parse_query("2,5 ton eur"), Query(2.5, "TON", "EUR"))

    def test_amount_defaults_to_one(self):
        self.assertEqual(parse_query("btc uah"), Query(1.0, "BTC", "UAH"))

    def test_single_currency_defaults_the_target(self):
        self.assertEqual(parse_query("5 ton"), Query(5.0, "TON", "USD"))
        # USD would convert to itself — show the hryvnia instead.
        self.assertEqual(parse_query("100 usd"), Query(100.0, "USD", "UAH"))

    def test_unparseable(self):
        self.assertIsNone(parse_query("сколько стоит"))
        self.assertIsNone(parse_query(""))


class TestRates(unittest.TestCase):
    def test_crypto_to_fiat(self):
        self.assertAlmostEqual(convert(SNAPSHOT, 1, "BTC", "UAH"), 4_150_000.0, places=2)

    def test_fiat_to_crypto_is_the_inverse(self):
        self.assertAlmostEqual(convert(SNAPSHOT, 4_150_000.0, "UAH", "BTC"), 1.0, places=6)

    def test_fiat_cross_rate(self):
        self.assertAlmostEqual(convert(SNAPSHOT, 100, "USD", "RUB"), 9_000.0, places=4)
        self.assertAlmostEqual(convert(SNAPSHOT, 90, "RUB", "EUR"), 0.9, places=6)

    def test_crypto_to_crypto_through_usd(self):
        self.assertAlmostEqual(convert(SNAPSHOT, 1, "BTC", "TON"), 100_000 / 3, places=4)

    def test_same_currency_is_identity(self):
        self.assertEqual(convert(SNAPSHOT, 7.5, "TON", "TON"), 7.5)

    def test_missing_asset_yields_none(self):
        self.assertIsNone(convert(SNAPSHOT, 1, "DOGS", "USD"))
        self.assertIsNone(convert(SNAPSHOT, 1, "USD", "CZK"))
        self.assertIsNone(usd_value(SNAPSHOT, "WAT"))

    def test_legacy_snapshot_rebuilds_uah_from_coin_quotes(self):
        self.assertAlmostEqual(fiat_per_usd(LEGACY, "UAH"), 41.5, places=6)
        self.assertAlmostEqual(convert(LEGACY, 1, "USD", "UAH"), 41.5, places=6)
        # Currencies that only ever lived in `_fiat` are simply unavailable.
        self.assertIsNone(fiat_per_usd(LEGACY, "EUR"))

    def test_zero_and_garbage_quotes_are_ignored(self):
        broken = {"bitcoin": {"usd": 0, "uah": "n/a"}, "_fiat": {"UAH": "oops"}}
        self.assertIsNone(usd_value(broken, "BTC"))
        self.assertIsNone(fiat_per_usd(broken, "UAH"))


class TestFiatBlock(unittest.TestCase):
    def test_median_of_the_bridge_coins(self):
        prices = {
            "tether": {"usd": 1.0, "uah": 41.4, "eur": 0.9},
            "bitcoin": {"usd": 100_000.0, "uah": 4_160_000.0},
        }
        block = fiat_block(prices)
        # UAH: median of 41.4 and 41.6; EUR quoted by one coin only.
        self.assertAlmostEqual(block["UAH"], 41.5, places=6)
        self.assertAlmostEqual(block["EUR"], 0.9, places=6)
        self.assertNotIn("RUB", block)

    def test_empty_payload(self):
        self.assertEqual(fiat_block({}), {})


class TestFormatting(unittest.TestCase):
    def test_fiat_keeps_cents_and_groups_thousands(self):
        self.assertEqual(fmt_amount(4150000.0, "UAH"), "4 150 000.00")

    def test_small_crypto_amounts_keep_precision(self):
        self.assertEqual(fmt_amount(0.00002411, "BTC"), "0.00002411")

    def test_crypto_drops_trailing_zeros(self):
        self.assertEqual(fmt_amount(1.0, "BTC"), "1")
        self.assertEqual(fmt_amount(2.5, "TON"), "2.5")

    def test_money_prefixes_the_sign_or_ticker(self):
        self.assertEqual(fmt_money(100.0, "USD"), "$100.00")
        self.assertEqual(fmt_money(1.5, "BTC"), "1.5 BTC")


class TestRendering(unittest.TestCase):
    def test_conversion_card_carries_both_directions(self):
        text = render_conversion(SNAPSHOT, Query(100, "USD", "UAH"))
        self.assertIn("4 150.00 UAH", text)
        self.assertIn("1 USD = `41.50` UAH", text)
        self.assertIn("05.09 10:00", text)

    def test_cross_pair_is_anchored_to_dollars(self):
        text = render_conversion(SNAPSHOT, Query(1, "BTC", "UAH"))
        self.assertIn("$100 000.00", text)

    def test_usd_pair_skips_the_dollar_anchor(self):
        self.assertNotIn("≈", render_conversion(SNAPSHOT, Query(1, "BTC", "USD")))

    def test_unknown_pair_lists_supported_codes(self):
        text = render_conversion(SNAPSHOT, Query(1, "DOGS", "USD"))
        self.assertIn("Нет курса", text)
        self.assertIn("BTC", text)

    def test_no_snapshot_degrades_to_a_notice(self):
        self.assertIn("не загружены", render_conversion(None, Query(1, "BTC", "USD")))
        self.assertIn("не загружены", render_converter_home(None))

    def test_home_card_shows_live_reference_rates(self):
        text = render_converter_home(SNAPSHOT)
        self.assertIn("1 USD = `41.50` UAH", text)
        self.assertIn("1 BTC = `100 000.00` USD", text)


if __name__ == "__main__":
    unittest.main()
