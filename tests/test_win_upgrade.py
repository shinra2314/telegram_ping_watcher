"""A win that arrives as an *edit* of an already-stored ping must still notify.

Channels routinely edit the original giveaway post to append the winner list.
``process_ping_message`` only notified when the ping was brand new
(``existing is None``), so such a win silently flipped ``is_win`` in the
database and waited for the owner to open the dashboard.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    from pulse_desk.ping_pipeline import upgraded_to_win

    DEPS_OK = True
except Exception as _exc:  # missing deps or unloadable settings (e.g. CI w/o .env)
    DEPS_OK = False
    _IMPORT_ERROR = _exc


class UpgradedToWinTests(unittest.TestCase):
    def setUp(self):
        if not DEPS_OK:
            self.skipTest(f"pulse_desk deps unavailable: {_IMPORT_ERROR!r}")

    def test_edit_turning_a_giveaway_into_a_win_notifies(self):
        existing = {"is_win": 0, "is_giveaway": 1}
        record = {"is_win": True, "is_giveaway": True}
        self.assertTrue(upgraded_to_win(existing, record))

    def test_plain_mention_becoming_a_win_notifies(self):
        self.assertTrue(upgraded_to_win({"is_win": 0, "is_giveaway": 0}, {"is_win": True}))

    def test_already_known_win_does_not_notify_again(self):
        self.assertFalse(upgraded_to_win({"is_win": 1}, {"is_win": True}))

    def test_unrelated_edit_does_not_notify(self):
        self.assertFalse(upgraded_to_win({"is_win": 0, "is_giveaway": 1}, {"is_win": False, "is_giveaway": True}))

    def test_first_detection_is_handled_by_the_normal_path(self):
        self.assertFalse(upgraded_to_win(None, {"is_win": True}))


if __name__ == "__main__":
    unittest.main()
