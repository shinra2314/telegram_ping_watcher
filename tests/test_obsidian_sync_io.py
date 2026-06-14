"""Integration tests for the Obsidian sync orchestration (file IO + DB).

Exercises ``sync_once`` / ``toggle_item`` / ``mark_link_done`` against a temp
note file and a temp SQLite DB — the paths the pure-function unit tests in
``test_obsidian_debts`` do not cover.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    import database
except ModuleNotFoundError as exc:  # pragma: no cover
    if exc.name != "aiosqlite":
        raise
    database = None

from pulse_desk import obsidian_debts as od  # noqa: E402
from pulse_desk.runtime import AppState  # noqa: E402

NOTE_TEXT = """\
---
tags: [розыгрыш]
---

# @Alpha 100 мне
- [x] https://t.me/aaa/1 prize A
- [ ] https://t.me/bbb/2 prize B
"""

PING_A = "https://t.me/aaa/1"
PING_B = "https://t.me/bbb/2"


def _record(link: str, message_id: int) -> dict:
    return {
        "date": "2026-06-01T10:00:00",
        "chat": "Win Channel",
        "chat_id": 100,
        "sender": "Channel",
        "sender_id": 200,
        "message_id": message_id,
        "mentions": ["@Alpha"],
        "link": link,
        "text": "Победитель @Alpha",
        "chat_type": "channel",
        "detected_at": "2026-06-01T10:01:00",
        "is_giveaway": False,
        "is_win": True,
    }


class ObsidianSyncIoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if database is None:
            self.skipTest("aiosqlite is not installed in this Python environment")
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "pulse_test.db"
        await database.init_db()

        self.note = Path(self.tmp.name) / "Долги.md"
        self.note.write_text(NOTE_TEXT, encoding="utf-8")

        self.state = AppState()
        self.settings = SimpleNamespace(
            obsidian_debts_path=str(self.note),
            obsidian_sync_enabled=True,
            obsidian_sync_write=False,
            obsidian_sync_poll_seconds=30,
        )

        self.id_a = await database.save_ping(_record(PING_A, 1))
        await database.update_ping_meta(self.id_a, giveaway_status="pending", action_status="claim_prize")
        self.id_b = await database.save_ping(_record(PING_B, 2))
        await database.update_ping_meta(self.id_b, giveaway_status="claimed", action_status="claimed")

    async def asyncTearDown(self):
        database.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    async def test_sync_reconciles_note_into_pings(self):
        meta = await od.sync_once(self.state, self.settings, force=True)
        self.assertTrue(meta["enabled"])
        # Note [x] for aaa/1 → ping A promoted to claimed.
        ping_a = await database.get_ping_by_id(self.id_a)
        self.assertEqual(ping_a["giveaway_status"], "claimed")
        # Note [ ] for bbb/2 → claimed ping B reverted to pending (note wins).
        ping_b = await database.get_ping_by_id(self.id_b)
        self.assertEqual(ping_b["giveaway_status"], "pending")
        # Snapshot is cached on state for the panel.
        snap = self.state.obsidian_debts
        self.assertEqual(snap["overall"]["total"], 2)
        self.assertEqual(snap["overall"]["done"], 1)

    async def test_load_snapshot_does_not_mutate_pings(self):
        # Viewing the panel must never change ping statuses.
        snap = await od.load_snapshot(self.state, self.settings)
        self.assertEqual(snap["overall"]["total"], 2)
        ping_a = await database.get_ping_by_id(self.id_a)
        ping_b = await database.get_ping_by_id(self.id_b)
        self.assertEqual(ping_a["giveaway_status"], "pending")
        self.assertEqual(ping_b["giveaway_status"], "claimed")

    async def test_read_only_never_writes_file(self):
        before = self.note.read_text(encoding="utf-8")
        await od.sync_once(self.state, self.settings, force=True)
        self.assertEqual(self.note.read_text(encoding="utf-8"), before)

    async def test_toggle_item_writes_note_and_updates_ping(self):
        result = await od.toggle_item(self.state, self.settings, "t.me/bbb/2", True)
        self.assertTrue(result["ok"])
        text = self.note.read_text(encoding="utf-8")
        self.assertIn("- [x] https://t.me/bbb/2", text)
        self.assertIn("✅", text)
        ping_b = await database.get_ping_by_id(self.id_b)
        self.assertEqual(ping_b["giveaway_status"], "claimed")

    async def test_prefs_roundtrip_and_apply(self):
        await od.save_prefs({"enabled": True, "write": True, "path": str(self.note)})
        loaded = await od.load_prefs()
        self.assertTrue(loaded["enabled"])
        target = SimpleNamespace(obsidian_sync_enabled=False, obsidian_sync_write=False, obsidian_debts_path="")
        od.apply_prefs(target, loaded)
        self.assertTrue(target.obsidian_sync_enabled)
        self.assertTrue(target.obsidian_sync_write)
        self.assertEqual(target.obsidian_debts_path, str(self.note))

    async def test_mark_link_done_requires_write_flag(self):
        # write disabled → no-op
        await od.mark_link_done(self.state, self.settings, PING_B, True)
        self.assertIn("- [ ] https://t.me/bbb/2", self.note.read_text(encoding="utf-8"))
        # write enabled → stamps the checkbox
        self.settings.obsidian_sync_write = True
        await od.mark_link_done(self.state, self.settings, PING_B, True)
        self.assertIn("- [x] https://t.me/bbb/2", self.note.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
