from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk import bot_membership


class AdminChatIdsTests(unittest.TestCase):
    def test_admin_id_alone(self):
        self.assertEqual(bot_membership.admin_chat_ids("77", ""), {77})

    def test_extra_chats_are_merged(self):
        self.assertEqual(bot_membership.admin_chat_ids("77", "88, 99"), {77, 88, 99})

    def test_junk_entries_are_skipped(self):
        self.assertEqual(bot_membership.admin_chat_ids("", "abc,12"), {12})

    def test_empty_everywhere(self):
        self.assertEqual(bot_membership.admin_chat_ids("", ""), set())


class ResolveMemberAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_gets_full_permissions(self):
        role, perms = await bot_membership.resolve_member_access(
            77, admin_ids={77}, get_member=self._never, touch=self._never,
            decide=self._never,
        )
        self.assertEqual(role, "admin")
        self.assertTrue(perms["features"])

    async def test_unknown_user_has_no_role(self):
        async def no_member(_tg):
            return None

        role, _perms = await bot_membership.resolve_member_access(
            5, admin_ids=set(), get_member=no_member, touch=self._noop,
            decide=self._never,
        )
        self.assertIsNone(role)

    async def test_blocked_member_has_no_role(self):
        async def member(_tg):
            return {"role": "viewer", "blocked": 1, "permissions": ""}

        role, _perms = await bot_membership.resolve_member_access(
            5, admin_ids=set(), get_member=member, touch=self._noop,
            decide=self._never,
        )
        self.assertIsNone(role)

    async def test_member_closed_by_schedule_has_no_role(self):
        async def member(_tg):
            return {"role": "viewer", "blocked": 0, "permissions": ""}

        async def closed(_tg, _member):
            return False, "schedule", None

        role, _perms = await bot_membership.resolve_member_access(
            5, admin_ids=set(), get_member=member, touch=self._noop, decide=closed,
        )
        self.assertIsNone(role)

    async def test_open_member_keeps_role_and_grants(self):
        async def member(_tg):
            return {"role": "premium", "blocked": 0,
                    "permissions": '{"features": ["giveaways"], "delay_minutes": 5}'}

        async def open_now(_tg, _member):
            return True, "", None

        role, perms = await bot_membership.resolve_member_access(
            5, admin_ids=set(), get_member=member, touch=self._noop, decide=open_now,
        )
        self.assertEqual(role, "premium")
        self.assertEqual(perms["features"], ["giveaways"])
        self.assertEqual(perms["delay_minutes"], 5)

    async def test_member_without_a_role_defaults_to_viewer(self):
        async def member(_tg):
            return {"role": "", "blocked": 0, "permissions": ""}

        async def open_now(_tg, _member):
            return True, "", None

        role, _perms = await bot_membership.resolve_member_access(
            5, admin_ids=set(), get_member=member, touch=self._noop, decide=open_now,
        )
        self.assertEqual(role, "viewer")

    async def _noop(self, *args, **kwargs):
        return None

    async def _never(self, *args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("should not be reached")


if __name__ == "__main__":
    unittest.main()
