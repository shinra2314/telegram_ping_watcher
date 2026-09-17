"""Recovering an undecodable post from t.me: one fetch per post edit, not per sweep."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk import public_preview as pp  # noqa: E402


class RecoverPublicTextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.saved = dict(pp._recovered)
        pp._recovered.clear()
        self.addCleanup(lambda: (pp._recovered.clear(), pp._recovered.update(self.saved)))
        self.calls = 0
        self.pages: list = []

    async def fetch(self, username, message_id):
        self.calls += 1
        page = self.pages.pop(0) if self.pages else "winners: @alice"
        if isinstance(page, Exception):
            raise page
        return page

    async def test_repeat_reads_hit_the_cache_and_report_no_change(self):
        first = await pp.recover_public_text("@Chan", 5, None, fetch=self.fetch, now=0.0)
        again = await pp.recover_public_text("chan", 5, None, fetch=self.fetch, now=60.0)
        self.assertEqual(first, ("winners: @alice", True))
        self.assertEqual(again, ("winners: @alice", False))
        self.assertEqual(self.calls, 1)

    async def test_a_new_edit_refetches(self):
        self.pages = ["draw soon", "winners: @alice"]
        await pp.recover_public_text("chan", 5, "2026-09-13 10:00", fetch=self.fetch, now=0.0)
        text, changed = await pp.recover_public_text("chan", 5, "2026-09-13 10:05", fetch=self.fetch, now=10.0)
        self.assertEqual((text, changed), ("winners: @alice", True))
        self.assertEqual(self.calls, 2)

    async def test_expired_entry_refetches_but_same_text_is_not_a_change(self):
        await pp.recover_public_text("chan", 5, fetch=self.fetch, now=0.0)
        text, changed = await pp.recover_public_text(
            "chan", 5, fetch=self.fetch, now=pp.RECOVERY_CACHE_SECONDS + 1)
        self.assertEqual(self.calls, 2)
        self.assertFalse(changed)

    async def test_failure_is_cached_briefly_and_never_raises(self):
        self.pages = [RuntimeError("t.me down"), "winners: @alice"]
        self.assertEqual(await pp.recover_public_text("chan", 5, fetch=self.fetch, now=0.0), ("", False))
        await pp.recover_public_text("chan", 5, fetch=self.fetch, now=60.0)
        self.assertEqual(self.calls, 1)
        text, changed = await pp.recover_public_text(
            "chan", 5, fetch=self.fetch, now=pp.RECOVERY_FAILURE_SECONDS + 1)
        self.assertEqual((text, changed), ("winners: @alice", True))

    async def test_failed_reread_keeps_the_good_copy(self):
        self.pages = ["winners: @alice", ""]
        await pp.recover_public_text("chan", 5, "e1", fetch=self.fetch, now=0.0)
        text, changed = await pp.recover_public_text("chan", 5, "e2", fetch=self.fetch, now=1.0)
        self.assertEqual((text, changed), ("winners: @alice", False))

    async def test_cache_is_bounded(self):
        for i in range(pp.RECOVERY_CACHE_MAX + 20):
            await pp.recover_public_text("chan", i, fetch=self.fetch, now=float(i))
        self.assertLessEqual(len(pp._recovered), pp.RECOVERY_CACHE_MAX)


if __name__ == "__main__":
    unittest.main()
