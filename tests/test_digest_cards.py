"""Digest card layout + caption: the pure halves of the picture digest."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pulse_desk.bot_notify import file_field, file_from_field  # noqa: E402
from pulse_desk.digest import format_digest_caption  # noqa: E402
from pulse_desk.digest_cards import _damp_weights, group_pings, squarify  # noqa: E402


class SquarifyTests(unittest.TestCase):
    def test_tiles_fill_the_rect(self):
        rect = (0.0, 0.0, 600.0, 400.0)
        tiles = squarify([6, 6, 4, 3, 2, 2, 1], rect)
        self.assertEqual(len(tiles), 7)
        area = sum(w * h for _x, _y, w, h in tiles)
        self.assertAlmostEqual(area, 600 * 400, places=3)

    def test_areas_are_proportional(self):
        tiles = squarify([4, 2, 1, 1], (0.0, 0.0, 200.0, 200.0))
        areas = [w * h for _x, _y, w, h in tiles]
        self.assertAlmostEqual(areas[0] / areas[1], 2.0, places=6)
        self.assertAlmostEqual(areas[1] / areas[2], 2.0, places=6)

    def test_tiles_stay_inside_the_rect(self):
        for x, y, w, h in squarify([9, 5, 5, 3, 1], (10.0, 20.0, 300.0, 500.0)):
            self.assertGreaterEqual(x, 10 - 1e-6)
            self.assertGreaterEqual(y, 20 - 1e-6)
            self.assertLessEqual(x + w, 310 + 1e-6)
            self.assertLessEqual(y + h, 520 + 1e-6)

    def test_aspect_ratios_stay_readable(self):
        # The whole point of squarifying: no 40:1 slivers for a sane spread.
        for _x, _y, w, h in squarify([10, 8, 6, 5, 4, 3, 2], (0.0, 0.0, 800.0, 600.0)):
            self.assertLess(max(w / h, h / w), 6.0)

    def test_zero_and_empty_inputs(self):
        self.assertEqual(squarify([], (0.0, 0.0, 10.0, 10.0)), [])
        self.assertEqual(squarify([1, 2], (0.0, 0.0, 0.0, 50.0)),
                         [(0.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0)])
        # A zero value keeps its slot so the caller can zip labels back on.
        tiles = squarify([5, 0, 5], (0.0, 0.0, 100.0, 100.0))
        self.assertEqual(tiles[1], (0.0, 0.0, 0.0, 0.0))


class GroupPingsTests(unittest.TestCase):
    def test_groups_and_counts(self):
        pings = [
            {"chat": "@a", "is_win": 1},
            {"chat": "@a", "is_giveaway": 1},
            {"chat": "@b", "is_giveaway": 1},
            {"chat": "@b", "is_giveaway": 1},
            {"chat": "@c"},
        ]
        groups = {key: (total, chats) for key, _label, total, chats in group_pings(pings)}
        self.assertEqual(groups["wins"], (1, [("@a", 1)]))
        self.assertEqual(groups["giveaways"], (3, [("@b", 2), ("@a", 1)]))
        self.assertEqual(groups["mentions"], (1, [("@c", 1)]))

    def test_a_win_never_counts_twice(self):
        # A win is usually flagged as a giveaway too — it belongs to one tile.
        groups = dict((key, total) for key, _l, total, _c in
                      group_pings([{"chat": "@a", "is_win": 1, "is_giveaway": 1}]))
        self.assertEqual(groups, {"wins": 1})

    def test_empty_groups_are_dropped(self):
        self.assertEqual(group_pings([]), [])

    def test_missing_chat_falls_back(self):
        groups = group_pings([{"is_win": 1}])
        self.assertEqual(groups[0][3], [("?", 1)])


class DampWeightsTests(unittest.TestCase):
    def test_range_is_compressed_but_ordered(self):
        tiles = _damp_weights([{"weight": 1.6e12}, {"weight": 3e11}, {"weight": 5e9}])
        weights = [t["weight"] for t in tiles]
        self.assertEqual(weights, sorted(weights, reverse=True))
        self.assertLess(weights[0] / weights[-1], 20)

    def test_smallest_tile_keeps_a_floor(self):
        tiles = _damp_weights([{"weight": 1e12}, {"weight": 1.0}])
        self.assertAlmostEqual(tiles[1]["weight"], tiles[0]["weight"] / 15.0)

    def test_empty(self):
        self.assertEqual(_damp_weights([]), [])


class CaptionTests(unittest.TestCase):
    def test_wins_are_linked(self):
        pings = [{"chat": "@a", "is_win": 1, "link": "https://t.me/a/1"},
                 {"chat": "@b"}]
        caption = format_digest_caption(pings)
        self.assertIn("[@a](https://t.me/a/1)", caption)
        self.assertIn("Всего: 2", caption)

    def test_no_wins_stays_one_block(self):
        caption = format_digest_caption([{"chat": "@b"}])
        self.assertNotIn("Победы", caption)

    def test_fits_a_telegram_caption(self):
        pings = [{"chat": f"@channel_with_a_long_name_{i}", "is_win": 1,
                  "link": f"https://t.me/c/1234567890/{i}"} for i in range(40)]
        self.assertLessEqual(len(format_digest_caption(pings)), 1024)

    def test_delta_against_yesterday(self):
        caption = format_digest_caption([{"chat": "@a"}], prev_pings=[{"chat": "@a"}, {"chat": "@b"}])
        self.assertIn("вчера 2", caption)


class FileFieldTests(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(file_from_field(file_field(["a.png", "b.png"])), ["a.png", "b.png"])
        self.assertEqual(file_from_field(file_field("a.png")), "a.png")
        self.assertIsNone(file_from_field(file_field(None)))
        self.assertIsNone(file_from_field(""))


if __name__ == "__main__":
    unittest.main()
