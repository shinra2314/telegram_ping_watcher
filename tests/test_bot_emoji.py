from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import unittest

from pulse_desk.bot.emoji import build_entities, enrich, with_vs16_variants


MAP = {"🏆": 111, "🎁": 222, "⚙️": 333}


class BuildEntitiesTests(unittest.TestCase):
    def test_empty_map_no_entities(self):
        self.assertEqual(build_entities("🏆 win", {}), [])

    def test_single_emoji_offset_and_length(self):
        ents = build_entities("🏆", MAP)
        self.assertEqual(len(ents), 1)
        self.assertEqual(ents[0].offset, 0)
        self.assertEqual(ents[0].length, 2)        # 🏆 is 2 UTF-16 units
        self.assertEqual(ents[0].document_id, 111)

    def test_offset_after_ascii_prefix(self):
        ents = build_entities("ok 🎁", MAP)
        self.assertEqual(len(ents), 1)
        self.assertEqual(ents[0].offset, 3)        # "ok " = 3 UTF-16 units
        self.assertEqual(ents[0].document_id, 222)

    def test_multi_codepoint_emoji_matched_whole(self):
        ents = build_entities("⚙️", MAP)
        self.assertEqual(len(ents), 1)
        self.assertEqual(ents[0].offset, 0)
        self.assertEqual(ents[0].length, 2)        # U+2699 U+FE0F = 2 units
        self.assertEqual(ents[0].document_id, 333)

    def test_unmapped_emoji_skipped(self):
        self.assertEqual(build_entities("🔥", MAP), [])

    def test_two_emoji_consecutive_offsets(self):
        ents = build_entities("🏆🎁", MAP)
        self.assertEqual([e.offset for e in ents], [0, 2])
        self.assertEqual([e.document_id for e in ents], [111, 222])


class EnrichTests(unittest.TestCase):
    def test_no_map_returns_text_and_none(self):
        self.assertEqual(enrich("🏆 **win**", {}), ("🏆 **win**", None))

    def test_no_mapped_emoji_returns_none(self):
        self.assertEqual(enrich("**plain**", MAP), ("**plain**", None))

    def test_markdown_stripped_and_entities_merged(self):
        clean, ents = enrich("**hi** 🏆", MAP)
        self.assertEqual(clean, "hi 🏆")          # ** removed by markdown parse
        # one bold entity + one custom-emoji entity, sorted by offset
        self.assertEqual(len(ents), 2)
        self.assertEqual(ents[0].offset, 0)        # bold at start
        self.assertEqual(ents[1].offset, 3)        # 🏆 after "hi "
        self.assertEqual(ents[1].document_id, 111)


class Vs16VariantTests(unittest.TestCase):
    """A pack emoticon that lost (or kept) its VS16 must still match the text."""

    def test_bare_emoticon_gains_vs16_form(self):
        emap = with_vs16_variants({"⚠": 7})
        self.assertEqual(emap["⚠"], 7)
        self.assertEqual(emap["⚠️"], 7)

    def test_vs16_emoticon_gains_bare_form(self):
        emap = with_vs16_variants({"⚙️": 9})
        self.assertEqual(emap["⚙"], 9)
        self.assertEqual(emap["⚙️"], 9)

    def test_existing_key_not_overwritten(self):
        emap = with_vs16_variants({"⚠": 1, "⚠️": 2})
        self.assertEqual(emap["⚠"], 1)
        self.assertEqual(emap["⚠️"], 2)

    def test_bare_pack_key_matches_vs16_text_with_full_length(self):
        # Regression: without the variant the entity would be 1 unit long and
        # Telegram would paint it over the wrong character.
        ents = build_entities("⚠️ сбой", with_vs16_variants({"⚠": 7}))
        self.assertEqual(len(ents), 1)
        self.assertEqual(ents[0].offset, 0)
        self.assertEqual(ents[0].length, 2)
        self.assertEqual(ents[0].document_id, 7)

    def test_plain_emoji_unaffected(self):
        self.assertEqual(with_vs16_variants({"🏆": 1}), {"🏆": 1, "🏆️": 1})


class GlyphSetTests(unittest.TestCase):
    """The generated set must cover the emoji the bot actually prints."""

    def _glyphs(self):
        import importlib.util
        path = (Path(__file__).resolve().parents[1] / "scripts"
                / "generate_bot_emoji.py")
        spec = importlib.util.spec_from_file_location("gen_emoji", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_no_duplicate_emoji_or_stems(self):
        mod = self._glyphs()
        emojis = [e for e, _s, _p, _c in mod.GLYPHS]
        stems = [s for _e, s, _p, _c in mod.GLYPHS]
        self.assertEqual(len(emojis), len(set(emojis)))
        self.assertEqual(len(stems), len(set(stems)))

    def test_emoji_map_shape_matches_uploader_contract(self):
        mod = self._glyphs()
        self.assertEqual(len(mod.EMOJI_MAP), len(mod.GLYPHS))
        for stem, value in mod.EMOJI_MAP.items():
            emoji, painter = value          # upload_emoji_pack.py unpacks a pair
            self.assertTrue(emoji)
            self.assertTrue(callable(painter))
            self.assertIsInstance(stem, str)


if __name__ == "__main__":
    unittest.main()
