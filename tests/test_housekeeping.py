"""Pure housekeeping rules: VACUUM trigger, backup rotation, disk and downtime checks."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk import housekeeping as hk  # noqa: E402

NOW = datetime(2026, 9, 13, 12, 0, 0)
PAGE = 4096


class VacuumDueTests(unittest.TestCase):
    def test_the_real_case_triggers(self):
        # 13.09.2026: 136 MB file, 29 257 free pages of 33 277.
        self.assertTrue(hk.vacuum_due(page_count=33277, freelist_count=29257, page_size=PAGE,
                                      last_vacuum_at=NOW, now=NOW))

    def test_small_absolute_waste_does_not_trigger(self):
        # 50 % free, but only 2 MB: not worth rewriting the file.
        self.assertFalse(hk.vacuum_due(page_count=1000, freelist_count=500, page_size=PAGE,
                                       last_vacuum_at=NOW, now=NOW))

    def test_low_ratio_does_not_trigger(self):
        # 20 MB free of a 1 GB file.
        self.assertFalse(hk.vacuum_due(page_count=262144, freelist_count=5120, page_size=PAGE,
                                       last_vacuum_at=NOW, now=NOW))

    def test_interval_fallback_uses_the_stored_time(self):
        kwargs = dict(page_count=10000, freelist_count=100, page_size=PAGE, now=NOW, interval_hours=168)
        self.assertTrue(hk.vacuum_due(last_vacuum_at=NOW - timedelta(days=8), **kwargs))
        self.assertFalse(hk.vacuum_due(last_vacuum_at=NOW - timedelta(days=1), **kwargs))
        self.assertTrue(hk.vacuum_due(last_vacuum_at=None, **kwargs))

    def test_nothing_free_never_triggers_on_the_interval(self):
        self.assertFalse(hk.vacuum_due(page_count=10000, freelist_count=0, page_size=PAGE,
                                       last_vacuum_at=None, now=NOW))

    def test_empty_database(self):
        self.assertFalse(hk.vacuum_due(page_count=0, freelist_count=0, page_size=PAGE,
                                       last_vacuum_at=None, now=NOW))


class BackupNameTests(unittest.TestCase):
    def test_zip_and_plain_names_parse(self):
        expected = datetime(2026, 9, 12, 22, 50, 43)
        self.assertEqual(hk.backup_created_at("pulse_desk_20260912_225043.db"), expected)
        self.assertEqual(hk.backup_created_at("pulse_desk_20260912_225043.db.zip"), expected)

    def test_foreign_name_is_none(self):
        self.assertIsNone(hk.backup_created_at("pulse_desk_archive.db"))
        self.assertIsNone(hk.backup_created_at("pulse_desk_20261399_999999.db"))


class BackupNeededTests(unittest.TestCase):
    def test_first_backup(self):
        self.assertTrue(hk.backup_needed(None, NOW))

    def test_restart_burst_is_skipped(self):
        self.assertFalse(hk.backup_needed(NOW - timedelta(minutes=5), NOW, 6))
        self.assertTrue(hk.backup_needed(NOW - timedelta(hours=7), NOW, 6))


def _item(name: str, when: datetime, size: int = 10) -> hk.BackupItem:
    return hk.BackupItem(name, when, size)


class RotationTests(unittest.TestCase):
    def test_restart_burst_keeps_older_days(self):
        # Ten copies within one day used to push every older copy out.
        items = [_item(f"burst{i}", NOW - timedelta(minutes=10 * i)) for i in range(10)]
        items += [_item(f"day{d}", NOW - timedelta(days=d)) for d in range(1, 7)]
        doomed = set(hk.backups_to_delete(items, NOW))
        self.assertTrue({f"day{d}" for d in range(1, 7)}.isdisjoint(doomed))
        self.assertEqual(doomed, {f"burst{i}" for i in range(3, 10)})

    def test_weekly_tier_keeps_one_per_week(self):
        items = [_item(f"d{d}", NOW - timedelta(days=d)) for d in range(0, 60)]
        doomed = set(hk.backups_to_delete(items, NOW))
        kept = {item.name for item in items} - doomed
        # 7 days + up to 4 older weeks.
        self.assertLessEqual(len(kept), 7 + 4)
        self.assertIn("d0", kept)
        self.assertIn("d59", doomed)

    def test_size_cap_drops_oldest_but_never_the_newest(self):
        items = [_item("new", NOW, 500), _item("old", NOW - timedelta(days=1), 500)]
        self.assertEqual(hk.backups_to_delete(items, NOW, max_total_bytes=600), ["old"])
        self.assertEqual(hk.backups_to_delete([_item("huge", NOW, 5000)], NOW, max_total_bytes=100), [])

    def test_empty(self):
        self.assertEqual(hk.backups_to_delete([], NOW), [])


class DiskAndGapTests(unittest.TestCase):
    def test_disk_low(self):
        self.assertTrue(hk.disk_low(1 * hk.MB, 2048))
        self.assertFalse(hk.disk_low(5000 * hk.MB, 2048))
        self.assertFalse(hk.disk_low(0, 0))

    def test_nightly_shutdown_is_reported_restart_is_not(self):
        self.assertTrue(hk.gap_worth_reporting(NOW - timedelta(hours=9), NOW))
        self.assertFalse(hk.gap_worth_reporting(NOW - timedelta(minutes=6), NOW))
        self.assertFalse(hk.gap_worth_reporting(None, NOW))
        self.assertFalse(hk.gap_worth_reporting(NOW + timedelta(minutes=1), NOW))

    def test_format_duration(self):
        self.assertEqual(hk.format_duration(timedelta(hours=9, minutes=16)), "9 ч 16 мин")
        self.assertEqual(hk.format_duration(timedelta(hours=2)), "2 ч")
        self.assertEqual(hk.format_duration(timedelta(minutes=45)), "45 мин")


if __name__ == "__main__":
    unittest.main()
