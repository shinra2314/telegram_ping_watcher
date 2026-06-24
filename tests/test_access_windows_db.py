from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    import database
except ModuleNotFoundError as exc:  # keeps pure tests runnable without project deps
    if exc.name != "aiosqlite":
        raise
    database = None


@unittest.skipIf(database is None, "aiosqlite is not installed")
class AccessWindowsDbTests(unittest.TestCase):
    def _run(self, coro):
        with tempfile.TemporaryDirectory() as tmp:
            original = database.DB_PATH
            database.DB_PATH = Path(tmp) / "test.db"
            try:
                return asyncio.run(coro())
            finally:
                database.DB_PATH = original

    def test_migration_adds_member_columns(self):
        async def scenario():
            await database.init_db()
            await database.upsert_bot_member(tg_id=1, tg_username="u", name="U", key_id=None, role="viewer")
            member = await database.get_bot_member(1)
            self.assertEqual(member.get("access_default_policy"), "allow")
            self.assertIn("timezone", member)
        self._run(scenario)

    def test_create_and_list_round_trip(self):
        async def scenario():
            await database.init_db()
            await database.upsert_bot_member(tg_id=2, tg_username="u", name="U", key_id=None, role="viewer")
            row = await database.create_access_window(
                tg_id=2, enabled=False, repeat_rule={"type": "daily", "from": "23:00", "to": "08:00"},
                timezone="Europe/Kyiv", priority=200, label="night", created_by=99,
            )
            self.assertIsInstance(row["id"], int)
            self.assertEqual(row["enabled"], 0)
            windows = await database.list_access_windows(2)
            self.assertEqual(len(windows), 1)
            self.assertEqual(windows[0]["label"], "night")
            self.assertEqual(windows[0]["timezone"], "Europe/Kyiv")
        self._run(scenario)

    def test_list_all_groups_by_user(self):
        async def scenario():
            await database.init_db()
            for tg in (3, 4):
                await database.upsert_bot_member(tg_id=tg, tg_username="u", name="U", key_id=None, role="viewer")
                await database.create_access_window(tg_id=tg, enabled=True, repeat_rule={"type": "none"})
            grouped = await database.list_all_access_windows()
            self.assertEqual(set(grouped.keys()), {3, 4})
            self.assertEqual(len(grouped[3]), 1)
        self._run(scenario)

    def test_deactivate_hides_from_active_list(self):
        async def scenario():
            await database.init_db()
            await database.upsert_bot_member(tg_id=5, tg_username="u", name="U", key_id=None, role="viewer")
            row = await database.create_access_window(tg_id=5, enabled=True, repeat_rule={"type": "none"})
            await database.deactivate_access_window(int(row["id"]))
            self.assertEqual(await database.list_access_windows(5), [])
            self.assertEqual(len(await database.list_access_windows(5, active_only=False)), 1)
        self._run(scenario)

    def test_set_default_policy(self):
        async def scenario():
            await database.init_db()
            await database.upsert_bot_member(tg_id=6, tg_username="u", name="U", key_id=None, role="viewer")
            await database.set_member_default_policy(6, "deny")
            member = await database.get_bot_member(6)
            self.assertEqual(member["access_default_policy"], "deny")
        self._run(scenario)

    def test_audit_insert_and_fetch(self):
        async def scenario():
            await database.init_db()
            await database.upsert_bot_member(tg_id=7, tg_username="u", name="U", key_id=None, role="viewer")
            await database.record_access_audit(7, None, "manual_off", "admin:99", {"a": 1}, {"a": 2})
            log = await database.get_access_audit(7)
            self.assertEqual(len(log), 1)
            self.assertEqual(log[0]["action"], "manual_off")
            self.assertEqual(log[0]["actor"], "admin:99")
        self._run(scenario)

    def test_full_resolve_path_denies_during_blackout(self):
        async def scenario():
            from datetime import datetime, timezone
            from pulse_desk.access_control import resolve_access, window_from_row

            await database.init_db()
            await database.upsert_bot_member(tg_id=20, tg_username="u", name="U", key_id=None, role="viewer")
            # All-day blackout so the result is independent of wall-clock at run time.
            await database.create_access_window(
                tg_id=20, enabled=False,
                repeat_rule={"type": "daily", "from": "00:00", "to": "23:59"}, timezone="UTC",
            )
            member = await database.get_bot_member(20)
            rows = await database.list_access_windows(20)
            decision = resolve_access(member, [window_from_row(r) for r in rows], datetime.now(timezone.utc))
            self.assertFalse(decision.allowed)
            self.assertTrue(decision.reason.startswith("window#"))
        self._run(scenario)

    def test_undo_round_trip_reverses_manual_off(self):
        async def scenario():
            from datetime import datetime, timezone
            from pulse_desk.access_control import find_undoable, plan_undo, resolve_access, window_from_row

            await database.init_db()
            await database.upsert_bot_member(tg_id=21, tg_username="u", name="U", key_id=None, role="viewer")
            row = await database.create_disable_until_window(21, None)
            await database.record_access_audit(21, int(row["id"]), "manual_off", "admin:1", None, {"until": None})
            # member now closed
            member = await database.get_bot_member(21)
            wins = await database.list_access_windows(21)
            self.assertFalse(resolve_access(member, [window_from_row(w) for w in wins], datetime.now(timezone.utc)).allowed)
            # undo
            target = find_undoable(await database.get_access_audit(21))
            self.assertEqual(target["action"], "manual_off")
            plan = plan_undo(target)
            self.assertEqual(plan["op"], "deactivate")
            await database.deactivate_access_window(int(plan["schedule_id"]))
            # member open again
            wins = await database.list_access_windows(21)
            self.assertTrue(resolve_access(member, [window_from_row(w) for w in wins], datetime.now(timezone.utc)).allowed)
        self._run(scenario)

    def test_cascade_delete_with_member(self):
        async def scenario():
            await database.init_db()
            await database.upsert_bot_member(tg_id=8, tg_username="u", name="U", key_id=None, role="viewer")
            await database.create_access_window(tg_id=8, enabled=True, repeat_rule={"type": "none"})
            # deleting the member should cascade-remove their windows (FK ON)
            async with database._core._connect() as db:
                await db.execute("DELETE FROM bot_members WHERE tg_id = 8")
                await db.commit()
            self.assertEqual(await database.list_access_windows(8, active_only=False), [])
        self._run(scenario)


if __name__ == "__main__":
    unittest.main()
