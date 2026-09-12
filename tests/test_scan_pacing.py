"""Sweep pacing helpers: skip idle channels, don't stack sleep on scan time."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.scan import (
    EDIT_SWEEP_IDLE_INTERVAL_SECONDS,
    MIN_SCAN_GAP_SECONDS,
    channel_has_new_messages,
    edit_sweep_due,
    next_scan_delay,
)


class ChannelHasNewMessagesTests(unittest.TestCase):
    """The dialog list already carries every channel's newest message id, so an
    idle channel can be skipped without spending a history request on it."""

    def test_idle_channel_is_skipped(self):
        self.assertFalse(channel_has_new_messages(latest_message_id=100, sweep_start_id=100))
        self.assertFalse(channel_has_new_messages(latest_message_id=95, sweep_start_id=100))

    def test_new_message_is_scanned(self):
        self.assertTrue(channel_has_new_messages(latest_message_id=101, sweep_start_id=100))

    def test_unknown_ids_never_skip(self):
        self.assertTrue(channel_has_new_messages(latest_message_id=0, sweep_start_id=100))
        self.assertTrue(channel_has_new_messages(latest_message_id=None, sweep_start_id=100))
        self.assertTrue(channel_has_new_messages(latest_message_id=100, sweep_start_id=0))
        self.assertTrue(channel_has_new_messages(latest_message_id="x", sweep_start_id=100))


class EditSweepDueTests(unittest.TestCase):
    """Edits don't move a channel's top message id, so idle channels still get a
    recent-window pass — just on their own slower cadence."""

    def test_first_pass_is_due(self):
        self.assertTrue(edit_sweep_due(None, now=1000.0))

    def test_recent_pass_is_not_due(self):
        self.assertFalse(edit_sweep_due(1000.0, now=1000.0 + EDIT_SWEEP_IDLE_INTERVAL_SECONDS - 1))

    def test_stale_pass_is_due(self):
        self.assertTrue(edit_sweep_due(1000.0, now=1000.0 + EDIT_SWEEP_IDLE_INTERVAL_SECONDS))

    def test_custom_interval(self):
        self.assertTrue(edit_sweep_due(10.0, now=70.0, interval=60))
        self.assertFalse(edit_sweep_due(10.0, now=69.0, interval=60))


class NextScanDelayTests(unittest.TestCase):
    """Sleeping the full interval *after* a 20-minute sweep doubled the gap
    between sweeps; sleep only what is left of the cycle instead."""

    def test_sleeps_remainder_of_interval(self):
        self.assertEqual(next_scan_delay(900, 300), 600)

    def test_slow_sweep_restarts_almost_immediately(self):
        self.assertEqual(next_scan_delay(900, 1400), MIN_SCAN_GAP_SECONDS)

    def test_never_shorter_than_minimum(self):
        self.assertEqual(next_scan_delay(900, 900), MIN_SCAN_GAP_SECONDS)

    def test_fast_sweep_keeps_full_interval(self):
        self.assertEqual(next_scan_delay(900, 0), 900)

    def test_garbage_input_falls_back_to_minimum(self):
        self.assertEqual(next_scan_delay(None, "x"), MIN_SCAN_GAP_SECONDS)


if __name__ == "__main__":
    unittest.main()
