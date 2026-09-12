"""Regression guards for the bot's hot-path optimisations.

Each of these replaced a slower implementation, so the test that matters is
"same answer, cheaper" — not just that the new code runs.
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
for path in (str(ROOT), str(SRC_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from pulse_desk.analytics import ANALYTICS_TTL_SECONDS, cache_fresh  # noqa: E402
from pulse_desk.bot.emoji import build_entities, compile_emoji_map, with_vs16_variants  # noqa: E402
from pulse_desk.bot_membership import TOUCH_INTERVAL_SECONDS, _touch_due, reset_touch_throttle  # noqa: E402
from pulse_desk.common import tail_lines  # noqa: E402

try:
    import database
except ModuleNotFoundError as exc:
    if exc.name != "aiosqlite":
        raise
    database = None


def _reference_build(clean_text: str, emoji_map: dict[str, int]):
    """The pre-index implementation: sort every key, scan them all per position."""
    keys = sorted(emoji_map, key=len, reverse=True)
    out = []
    offset = i = 0
    while i < len(clean_text):
        match = next((k for k in keys if clean_text.startswith(k, i)), None)
        if match:
            length = len(match.encode("utf-16-le")) // 2
            out.append((offset, length, emoji_map[match]))
            offset += length
            i += len(match)
        else:
            offset += len(clean_text[i].encode("utf-16-le")) // 2
            i += 1
    return out


class EmojiIndexTests(unittest.TestCase):
    """The bucketed index must be a pure speed-up — identical entities."""

    def setUp(self):
        glyphs = "🏆🎁💸⚙️⚠️🛰🆕🔥📊📨⭐🔄👑👁🎯✅⏭🚫📣💾⏱🔎🔑🔐🔗🗑📤💱🎰📈🟢🔴⬅️✖️➕"
        self.emap = with_vs16_variants({g: 5_000_000_000_000_000_000 + i for i, g in enumerate(glyphs)})
        self.index = compile_emoji_map(self.emap)

    def _assert_same(self, text: str):
        new = [(e.offset, e.length, e.document_id) for e in build_entities(text, self.emap, self.index)]
        self.assertEqual(new, _reference_build(text, self.emap), f"mismatch for {text!r}")

    def test_matches_reference_on_card_like_text(self):
        self._assert_same("🏆 **Победа!**\n⚙️ Розыгрыш · 🎁 приз 500 TON\n⚠️ дедлайн завтра")

    def test_matches_reference_on_plain_text(self):
        self._assert_same("Обычный текст без эмодзи вообще")

    def test_matches_reference_on_adjacent_emoji(self):
        self._assert_same("🏆🎁💸⚙️⚠️")

    def test_matches_reference_on_empty(self):
        self._assert_same("")

    def test_vs16_form_still_wins_over_the_bare_codepoint(self):
        # ⚙️ is U+2699 U+FE0F. Matching only U+2699 emits an entity one UTF-16
        # unit short, which Telegram renders on the wrong character.
        entities = build_entities("⚙️", self.emap, self.index)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].length, 2)

    def test_index_buckets_by_first_character_longest_first(self):
        for keys in self.index.values():
            self.assertEqual(keys, sorted(keys, key=len, reverse=True))

    def test_works_without_a_prebuilt_index(self):
        text = "🏆 приз"
        self.assertEqual(
            [(e.offset, e.length) for e in build_entities(text, self.emap)],
            [(e.offset, e.length) for e in build_entities(text, self.emap, self.index)],
        )


class AnalyticsCacheTests(unittest.TestCase):
    def test_fresh_within_ttl(self):
        self.assertTrue(cache_fresh(100.0, 60.0, 159.0))

    def test_stale_past_ttl(self):
        self.assertFalse(cache_fresh(100.0, 60.0, 161.0))

    def test_never_cached_is_stale(self):
        self.assertFalse(cache_fresh(None, 60.0, 0.0))

    def test_ttl_is_short_enough_to_feel_live(self):
        self.assertLessEqual(ANALYTICS_TTL_SECONDS, 120)


class TouchThrottleTests(unittest.TestCase):
    def setUp(self):
        reset_touch_throttle()
        self.addCleanup(reset_touch_throttle)

    def test_first_interaction_writes(self):
        self.assertTrue(_touch_due(1, now=0.0))

    def test_second_interaction_within_the_window_does_not(self):
        _touch_due(1, now=0.0)
        self.assertFalse(_touch_due(1, now=TOUCH_INTERVAL_SECONDS - 1))

    def test_write_resumes_after_the_window(self):
        _touch_due(1, now=0.0)
        self.assertTrue(_touch_due(1, now=TOUCH_INTERVAL_SECONDS + 1))

    def test_members_are_throttled_independently(self):
        _touch_due(1, now=0.0)
        self.assertTrue(_touch_due(2, now=0.0))


class TailLinesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "app.log"

    def _write(self, lines):
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_returns_the_last_n_lines(self):
        self._write([f"line {i}" for i in range(100)])
        self.assertEqual(tail_lines(self.path, 3), ["line 97", "line 98", "line 99"])

    def test_matches_a_full_read_across_block_boundaries(self):
        # Small blocks force the backwards seek to stitch several reads.
        lines = [f"{i:05d} " + "x" * 60 for i in range(500)]
        self._write(lines)
        self.assertEqual(tail_lines(self.path, 20, block_bytes=64), lines[-20:])

    def test_fewer_lines_than_requested(self):
        self._write(["only one"])
        self.assertEqual(tail_lines(self.path, 20), ["only one"])

    def test_missing_file_is_empty(self):
        self.assertEqual(tail_lines(Path(self.tmp.name) / "nope.log", 5), [])

    def test_empty_file_is_empty(self):
        self.path.write_text("", encoding="utf-8")
        self.assertEqual(tail_lines(self.path, 5), [])

    def test_does_not_read_the_whole_file(self):
        self._write([f"{i:07d}" for i in range(200_000)])
        self.assertGreater(self.path.stat().st_size, 1_000_000)
        started = time.perf_counter()
        got = tail_lines(self.path, 5)
        self.assertLess(time.perf_counter() - started, 0.05)
        self.assertEqual(got[-1], "0199999")


@unittest.skipIf(database is None, "aiosqlite is not installed")
class MaintenanceCapTests(unittest.IsolatedAsyncioTestCase):
    """A chatty settings key must not be able to bloat the database again."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "maint.db"
        await database.init_db()

    async def asyncTearDown(self):
        database.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    async def test_history_is_capped_per_key(self):
        for i in range(50):
            await database.set_setting("chatty", {"tick": i})
        stats = await database.cleanup_unbounded_tables(history_per_key=10, checkpoint_days=0)
        self.assertGreater(stats["settings_history"], 0)
        self.assertLessEqual(len(await database.get_settings_history(key="chatty", limit=999)), 10)

    async def test_the_cap_is_per_key_not_global(self):
        for i in range(20):
            await database.set_setting("a", {"tick": i})
            await database.set_setting("b", {"tick": i})
        await database.cleanup_unbounded_tables(history_per_key=5, checkpoint_days=0)
        self.assertEqual(len(await database.get_settings_history(key="a", limit=999)), 5)
        self.assertEqual(len(await database.get_settings_history(key="b", limit=999)), 5)

    async def test_audit_false_writes_no_history_at_all(self):
        # This is what kept `obsidian_sync` — rewritten every 30 s — from filling
        # the table with 141k rows of a changing timestamp.
        for i in range(20):
            await database.set_setting("runtime_meta", {"last_sync_at": i}, audit=False)
        self.assertEqual(await database.get_settings_history(key="runtime_meta", limit=999), [])
        self.assertEqual(await database.get_setting("runtime_meta"), {"last_sync_at": 19})

    async def test_unchanged_value_writes_no_history(self):
        await database.set_setting("same", {"v": 1})
        before = len(await database.get_settings_history(key="same", limit=999))
        await database.set_setting("same", {"v": 1})
        self.assertEqual(len(await database.get_settings_history(key="same", limit=999)), before)


if __name__ == "__main__":
    unittest.main()
