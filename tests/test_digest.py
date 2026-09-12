"""Digest rendering: crypto block, day-over-day deltas, degraded-data paths."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pulse_desk.digest import format_digest, market_block, ping_stats  # noqa: E402


def snapshot(fetched_at: str, **prices) -> dict:
    """`prices` maps a coingecko id to (usd, uah, usd_24h_change)."""
    snap: dict = {"fetched_at_iso": fetched_at}
    for asset, values in prices.items():
        usd, uah, change = values
        snap[asset.replace("_", "-")] = {
            "usd": usd,
            "uah": uah,
            "usd_24h_change": change,
            "uah_24h_change": change,
        }
    return snap


NOW = "2026-09-05T10:00:00"
DAY_AGO = "2026-09-04T10:00:00"


def two_days(latest_btc: float = 110.0, base_btc: float = 100.0) -> list[dict]:
    """Newest-first pair of snapshots, BTC moving, USDT pegged."""
    return [
        snapshot(NOW, bitcoin=(latest_btc, latest_btc * 44, -99.0), tether=(1.0, 44.55, 0.02)),
        snapshot(DAY_AGO, bitcoin=(base_btc, base_btc * 44, 0.0), tether=(1.0, 44.0, 0.0)),
    ]


class TestPingStats(unittest.TestCase):
    def test_splits_wins_and_mentions(self):
        stats = ping_stats([{"is_win": True}, {"is_win": False}, {}])
        self.assertEqual(stats, {"total": 3, "wins": 1, "mentions": 2})


class TestMarketBlock(unittest.TestCase):
    def test_delta_comes_from_our_own_snapshots(self):
        text = "\n".join(market_block(two_days()))
        self.assertIn("▲10.00%", text)  # 100 -> 110, not the -99% field
        self.assertIn("Сравнение со снимком 04.09 10:00", text)

    def test_falls_back_to_coingecko_change_with_one_snapshot(self):
        rows = [snapshot(NOW, bitcoin=(110.0, 4840.0, -1.49), tether=(1.0, 44.55, 0.02))]
        text = "\n".join(market_block(rows))
        self.assertIn("▼1.49%", text)
        self.assertIn("CoinGecko", text)

    def test_drop_renders_a_down_arrow(self):
        text = "\n".join(market_block(two_days(latest_btc=90.0)))
        self.assertIn("▼10.00%", text)

    def test_leader_and_laggard(self):
        rows = [
            snapshot(NOW, bitcoin=(110.0, 1.0, 0.0), solana=(90.0, 1.0, 0.0), tether=(1.0, 44.5, 0.0)),
            snapshot(DAY_AGO, bitcoin=(100.0, 1.0, 0.0), solana=(100.0, 1.0, 0.0), tether=(1.0, 44.5, 0.0)),
        ]
        text = "\n".join(market_block(rows))
        self.assertIn("Лидер: **BTC** ▲10.00%", text)
        self.assertIn("Аутсайдер: **SOL** ▼10.00%", text)

    def test_average_ignores_the_pegged_stablecoin(self):
        rows = [
            snapshot(NOW, bitcoin=(110.0, 1.0, 0.0), tether=(2.0, 89.0, 0.0)),
            snapshot(DAY_AGO, bitcoin=(100.0, 1.0, 0.0), tether=(1.0, 44.5, 0.0)),
        ]
        text = "\n".join(market_block(rows))
        self.assertIn("Средняя динамика рынка: ▲10.00%", text)

    def test_hryvnia_line(self):
        text = "\n".join(market_block(two_days()))
        self.assertIn("🇺🇦 Гривна: USDT `₴44.55`", text)

    def test_empty_history_says_so(self):
        text = "\n".join(market_block([]))
        self.assertIn("недоступны", text)

    def test_unknown_assets_are_skipped(self):
        text = "\n".join(market_block([{"fetched_at_iso": NOW, "dogecoin": {"usd": 1}}]))
        self.assertIn("недоступны", text)

    def test_pegged_coin_gets_no_fake_sparkline(self):
        rows = [
            snapshot(f"2026-09-05T{hour:02d}:00:00", tether=(1.0 + hour * 0.00001, 44.5, 0.0))
            for hour in range(9, -1, -1)
        ]
        line = next(row for row in market_block(rows) if "USDT" in row and "Гривна" not in row)
        self.assertFalse(any(block in line for block in "▁▂▃▄▅▆▇█"))

    def test_sparkline_rendered_when_history_is_long_enough(self):
        rows = [
            snapshot(f"2026-09-05T{hour:02d}:00:00", bitcoin=(100.0 + hour, 1.0, 0.0))
            for hour in range(9, -1, -1)
        ]
        line = next(row for row in market_block(rows) if "BTC" in row)
        self.assertTrue(any(block in line for block in "▁▂▃▄▅▆▇█"))


class TestFormatDigest(unittest.TestCase):
    def test_previous_day_deltas(self):
        pings = [{"is_win": True, "chat": "c1"}, {"is_win": False, "chat": "c2"}]
        prev = [{"is_win": False, "chat": "c3"}]
        text = format_digest(pings, prev_pings=prev)
        self.assertIn("Всего: 2 (вчера 1 · +1)", text)
        self.assertIn("🏆 Побед: 1 (вчера 0 · +1)", text)
        self.assertIn("📌 Упоминаний: 1 (вчера 1 · ±0)", text)

    def test_negative_delta(self):
        text = format_digest([], prev_pings=[{"is_win": True}, {"is_win": True}])
        self.assertIn("Всего: 0 (вчера 2 · −2)", text)

    def test_no_previous_window_keeps_the_flat_counters(self):
        text = format_digest([{"is_win": True, "chat": "c1"}])
        self.assertIn("Всего: 1 | 🏆 Побед: 1", text)
        self.assertNotIn("вчера", text)

    def test_market_still_reported_without_pings(self):
        text = format_digest([], market=two_days())
        self.assertIn("Нет новых пингов", text)
        self.assertIn("Крипта за сутки", text)
        self.assertIn("▲10.00%", text)

    def test_market_omitted_when_not_passed(self):
        self.assertNotIn("Крипта", format_digest([{"chat": "c1"}]))


if __name__ == "__main__":
    unittest.main()
