from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import unittest

from pulse_desk.bot.emoji import build_entities, enrich


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


if __name__ == "__main__":
    unittest.main()
