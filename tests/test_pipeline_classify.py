from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    from pulse_desk.app_ctx import state
    from pulse_desk.ping_pipeline import classify_record

    DEPS_OK = True
except Exception as _exc:  # missing deps or unloadable settings (e.g. CI w/o .env)
    DEPS_OK = False
    _IMPORT_ERROR = _exc


def make_record(text: str, mentions: list[str]) -> dict:
    return {"text": text, "mentions": mentions, "chat_type": "channel"}


class ClassifyRecordTests(unittest.TestCase):
    """Win/giveaway flags require a tracked-username mention.

    Mention-less records enter the pipeline only via the check-capture path
    (require_mentions=False); a channel check saying "Победители: @stranger"
    must not become a "win" on the giveaway board."""

    def setUp(self):
        if not DEPS_OK:
            self.skipTest(f"pulse_desk deps unavailable: {_IMPORT_ERROR!r}")
        self._old_win = state.win_keywords
        self._old_gw = state.giveaway_keywords
        state.win_keywords = ["победител", "выиграл"]
        state.giveaway_keywords = ["розыгрыш", "чек"]

    def tearDown(self):
        if not DEPS_OK:
            return
        state.win_keywords = self._old_win
        state.giveaway_keywords = self._old_gw

    def test_foreign_check_win_is_not_my_win(self):
        # Real sample: ZANOSNOY check post, winner is a stranger.
        record = make_record(
            "📥Чек на сумму 10 USDT\n\nУсловия -\nБыть подписанным на наш канал\n\nПобедители: @makcEPTA",
            mentions=[],
        )
        classify_record(record)
        self.assertFalse(record["is_win"])
        self.assertFalse(record["is_giveaway"])

    def test_win_mentioning_tracked_username_stays_win(self):
        record = make_record(
            "Итоги розыгрыша!\n\nПобедители: @w3v8f0rm, @KUWWL",
            mentions=["@w3v8f0rm"],
        )
        classify_record(record)
        self.assertTrue(record["is_win"])

    def test_giveaway_mentioning_tracked_username_stays_giveaway(self):
        record = make_record(
            "🎁 Розыгрыш призов! Прошлый победитель @w3v8f0rm",
            mentions=["@w3v8f0rm"],
        )
        classify_record(record)
        self.assertTrue(record["is_giveaway"])

    def test_mentionless_giveaway_text_not_flagged(self):
        record = make_record("🎁 Розыгрыш призов для всех!", mentions=[])
        classify_record(record)
        self.assertFalse(record["is_giveaway"])
        self.assertFalse(record["is_win"])


if __name__ == "__main__":
    unittest.main()
