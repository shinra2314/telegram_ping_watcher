from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import unittest
from unittest.mock import patch

from pulse_desk.bot import stickers as st


class RegistryTests(unittest.TestCase):
    def test_known_alt(self):
        self.assertEqual(st.sticker_alt("win"), "🏆")

    def test_unknown_alt_fallback(self):
        self.assertEqual(st.sticker_alt("nope"), "✨")

    def test_path_none_for_unknown(self):
        self.assertIsNone(st.sticker_path("nope"))

    def test_path_none_or_webp_for_known(self):
        # 'win' is registered but the .webp may not be generated in CI.
        p = st.sticker_path("win")
        self.assertTrue(p is None or p.endswith("win.webp"))


class _FakeSettings:
    def __init__(self, enabled: bool):
        self.bot_stickers_enabled = enabled


class _SpyClient:
    def __init__(self):
        self.calls = 0

    async def send_file(self, *args, **kwargs):
        self.calls += 1


class SendTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_client_returns_false(self):
        self.assertFalse(await st.send_sticker(None, 123, "win"))

    async def test_empty_peer_returns_false(self):
        self.assertFalse(await st.send_sticker(_SpyClient(), None, "win"))

    async def test_disabled_flag_skips_send(self):
        spy = _SpyClient()
        with patch.object(st, "_settings", _FakeSettings(False)):
            sent = await st.send_sticker(spy, 123, "win")
        self.assertFalse(sent)
        self.assertEqual(spy.calls, 0)

    async def test_unknown_name_skips_send(self):
        spy = _SpyClient()
        with patch.object(st, "_settings", _FakeSettings(True)):
            sent = await st.send_sticker(spy, 123, "does-not-exist")
        self.assertFalse(sent)
        self.assertEqual(spy.calls, 0)


if __name__ == "__main__":
    unittest.main()
