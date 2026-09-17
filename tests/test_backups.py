"""Database backups: consistent WAL snapshots, zip packing, startup skip, rotation."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import database  # noqa: E402
from database import backups  # noqa: E402


class BackupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pulse_backup_")
        self.dir = Path(self._tmp.name)
        self.db = self.dir / "pulse_desk.db"
        self._old_path = database.DB_PATH
        database.DB_PATH = self.db
        self.backup_dir = self.dir / "backups"

    def tearDown(self):
        database.DB_PATH = self._old_path
        self._tmp.cleanup()

    def _live_wal_db(self, rows: int) -> sqlite3.Connection:
        """A DB whose newest rows exist only in the -wal file (writer still open)."""
        con = sqlite3.connect(str(self.db))
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
        con.execute("CREATE TABLE t (v INTEGER)")
        con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
        con.commit()
        return con

    def _rows_in_zip(self, path: Path) -> int:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), 1)
            archive.extractall(self.dir / "restore")
        restored = self.dir / "restore" / names[0]
        # `with sqlite3.connect()` only commits; closing() actually releases the
        # file, which Windows needs before the temp dir can be removed.
        with closing(sqlite3.connect(str(restored))) as con:
            return con.execute("SELECT COUNT(*) FROM t").fetchone()[0]

    def test_snapshot_includes_rows_still_in_the_wal(self):
        con = self._live_wal_db(250)
        try:
            self.assertTrue(Path(f"{self.db}-wal").stat().st_size > 0)
            created = backups.create_db_backup()
        finally:
            con.close()
        self.assertTrue(created["name"].endswith(".db.zip"))
        self.assertEqual(self._rows_in_zip(Path(created["path"])), 250)
        # No uncompressed leftovers next to the zip.
        self.assertEqual([p.name for p in self.backup_dir.glob("*.db")], [])
        self.assertEqual(list(self.backup_dir.glob("*.part")), [])

    def test_startup_backup_skips_when_a_recent_copy_exists(self):
        self._live_wal_db(1).close()
        with mock.patch.dict("os.environ", {"BACKUP_MIN_INTERVAL_HOURS": "6"}):
            backups.backup_db_if_present()
            backups.backup_db_if_present()
        self.assertEqual(len(list(self.backup_dir.glob("*.db.zip"))), 1)

    def test_rotation_removes_old_plain_copies_and_lists_both_kinds(self):
        self._live_wal_db(1).close()
        self.backup_dir.mkdir()
        now = datetime.now()
        # A burst of old-style copies from 40 days ago, one per minute.
        for minute in range(5):
            stamp = (now - timedelta(days=40, minutes=minute)).strftime("%Y%m%d_%H%M%S")
            (self.backup_dir / f"pulse_desk_{stamp}.db").write_bytes(b"x" * 10)
        backups.create_db_backup()
        names = [row["name"] for row in backups.list_db_backups()]
        # The fresh zip first, then the two newest old copies (the "3 most
        # recent" tier, whatever their age); the other three are rotated out.
        self.assertTrue(names[0].endswith(".db.zip"))
        self.assertEqual(len(names), 3)
        self.assertTrue(all(name.endswith(".db") for name in names[1:]))

    def test_retention_zero_never_deletes(self):
        self.backup_dir.mkdir()
        for day in range(1, 60):
            stamp = (datetime.now() - timedelta(days=day)).strftime("%Y%m%d_%H%M%S")
            (self.backup_dir / f"pulse_desk_{stamp}.db").write_bytes(b"x")
        self.assertEqual(backups.prune_db_backups(0), [])
        self.assertEqual(len(list(self.backup_dir.iterdir())), 59)

    def test_missing_database_is_a_no_op(self):
        self.assertIsNone(backups.create_db_backup())
        backups.backup_db_if_present()
        self.assertFalse(self.backup_dir.exists())


if __name__ == "__main__":
    unittest.main()
