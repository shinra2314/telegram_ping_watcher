from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    import database
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from pulse_desk.app_ctx import get_current_role
    from routers.pings import router as pings_router

    DEPS_OK = True
except Exception as _exc:  # missing aiosqlite/httpx or unloadable settings (e.g. CI w/o .env)
    DEPS_OK = False
    _IMPORT_ERROR = _exc


class CheckAccessTests(unittest.TestCase):
    """Checks are owner-only and show only fresh (≤ CHECK_FRESH_MINUTES) rows."""

    def setUp(self):
        if not DEPS_OK:
            self.skipTest(f"web deps unavailable: {_IMPORT_ERROR!r}")
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "pulse_test.db"
        asyncio.run(self._seed())
        self.app = FastAPI()
        self.app.include_router(pings_router)

    def tearDown(self):
        if not DEPS_OK:
            return
        self.app.dependency_overrides.clear()
        database.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    async def _seed(self):
        await database.init_db()
        now = datetime.now()
        fresh = (now - timedelta(minutes=5)).replace(microsecond=0).isoformat()
        stale = (now - timedelta(hours=3)).replace(microsecond=0).isoformat()
        await database.save_ping({
            "chat": "Crypto", "chat_id": 1, "message_id": 1,
            "link": "https://t.me/c/1", "text": "свежий чек на 5 TON",
            "chat_type": "channel", "detected_at": fresh, "is_check": True,
        })
        await database.save_ping({
            "chat": "Crypto", "chat_id": 2, "message_id": 2,
            "link": "https://t.me/c/2", "text": "старый чек на 5 TON",
            "chat_type": "channel", "detected_at": stale, "is_check": True,
        })

    def _client(self, role: str) -> TestClient:
        self.app.dependency_overrides[get_current_role] = lambda: role
        return TestClient(self.app)

    def test_viewer_cannot_read_checks(self):
        resp = self._client("viewer").get("/api/pings", params={"chat_type": "check"})
        self.assertEqual(resp.status_code, 403)

    def test_admin_sees_only_fresh_checks(self):
        resp = self._client("admin").get(
            "/api/pings", params={"chat_type": "check", "limit": 100}
        )
        self.assertEqual(resp.status_code, 200)
        message_ids = {row["message_id"] for row in resp.json()}
        self.assertEqual(message_ids, {1})  # stale check (id 2) excluded

    def test_viewer_can_still_read_non_checks(self):
        resp = self._client("viewer").get("/api/pings", params={"chat_type": "all"})
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
