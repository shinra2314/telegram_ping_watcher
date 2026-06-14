import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

BASE_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = BASE_DIR / "src"
for path in (str(BASE_DIR), str(SRC_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from pulse_desk.telegram_errors import (
    call_rpc_resilient,
    is_channel_inaccessible,
    is_transient_rpc_error,
    iter_messages_resilient,
    transient_rpc_delay_seconds,
)


class FakeRpcCallFail(Exception):
    def __init__(self):
        super().__init__("Telegram is having internal issues, please try again later. (caused by GetHistoryRequest)")


class FakeClient:
    """iter_messages stub: yields scripted message-id batches, raising the
    scripted exception at the end of each batch except the last."""

    def __init__(self, batches, errors):
        self.batches = list(batches)
        self.errors = list(errors)
        self.calls = []

    async def iter_messages(self, entity, limit=None, max_id=0, **kwargs):
        self.calls.append({"limit": limit, "max_id": max_id, **kwargs})
        batch = self.batches.pop(0)
        for message_id in batch:
            yield SimpleNamespace(id=message_id)
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error


class TransientErrorDetectionTests(unittest.TestCase):
    def test_matches_rpc_call_fail_message(self):
        self.assertTrue(is_transient_rpc_error(FakeRpcCallFail()))

    def test_matches_error_code_marker(self):
        self.assertTrue(is_transient_rpc_error(Exception("RPC_CALL_FAIL (code 500)")))

    def test_ignores_unrelated_errors(self):
        self.assertFalse(is_transient_rpc_error(ValueError("chat not found")))

    def test_delay_grows_and_caps(self):
        self.assertEqual(transient_rpc_delay_seconds(1), 2.0)
        self.assertEqual(transient_rpc_delay_seconds(2), 4.0)
        self.assertEqual(transient_rpc_delay_seconds(10), 30.0)


class ChannelInaccessibleDetectionTests(unittest.TestCase):
    def test_matches_private_channel_message(self):
        exc = Exception(
            "The channel specified is private and you lack permission to access it. "
            "Another reason may be that you were banned from it (caused by GetHistoryRequest)"
        )
        self.assertTrue(is_channel_inaccessible(exc))

    def test_matches_channel_marker(self):
        self.assertTrue(is_channel_inaccessible(Exception("CHANNEL_PRIVATE")))
        self.assertTrue(is_channel_inaccessible(Exception("CHANNEL_INVALID")))

    def test_ignores_transient_and_unrelated_errors(self):
        self.assertFalse(is_channel_inaccessible(FakeRpcCallFail()))
        self.assertFalse(is_channel_inaccessible(ValueError("chat not found")))


class IterMessagesResilientTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, client, **kwargs):
        with patch("pulse_desk.telegram_errors.asyncio.sleep", new=AsyncMock()):
            return [m.id async for m in iter_messages_resilient(client, "entity", **kwargs)]

    async def test_resumes_below_last_seen_message(self):
        client = FakeClient(batches=[[10, 9, 8], [7, 6]], errors=[FakeRpcCallFail(), None])
        ids = await self.collect(client, limit=10)
        self.assertEqual(ids, [10, 9, 8, 7, 6])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[1]["max_id"], 8)
        self.assertEqual(client.calls[1]["limit"], 7)

    async def test_preserves_min_id_and_search_kwargs(self):
        client = FakeClient(batches=[[5], [4]], errors=[FakeRpcCallFail(), None])
        await self.collect(client, limit=5, min_id=2, search="@user")
        for call in client.calls:
            self.assertEqual(call["min_id"], 2)
            self.assertEqual(call["search"], "@user")

    async def test_gives_up_after_retries(self):
        client = FakeClient(
            batches=[[], [], []],
            errors=[FakeRpcCallFail(), FakeRpcCallFail(), FakeRpcCallFail()],
        )
        with self.assertRaises(FakeRpcCallFail):
            await self.collect(client, limit=5, retries=2)

    async def test_non_transient_error_propagates_immediately(self):
        client = FakeClient(batches=[[3]], errors=[ValueError("boom")])
        with self.assertRaises(ValueError):
            await self.collect(client, limit=5)
        self.assertEqual(len(client.calls), 1)

    async def test_stops_at_limit_without_extra_calls(self):
        client = FakeClient(batches=[[3, 2, 1]], errors=[None])
        ids = await self.collect(client, limit=3)
        self.assertEqual(ids, [3, 2, 1])
        self.assertEqual(len(client.calls), 1)


class CallRpcResilientTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_then_succeeds(self):
        attempts = {"count": 0}

        async def flaky():
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise FakeRpcCallFail()
            return "ok"

        with patch("pulse_desk.telegram_errors.asyncio.sleep", new=AsyncMock()):
            result = await call_rpc_resilient(flaky)
        self.assertEqual(result, "ok")
        self.assertEqual(attempts["count"], 3)

    async def test_non_transient_error_raises(self):
        async def broken():
            raise RuntimeError("bad request")

        with self.assertRaises(RuntimeError):
            await call_rpc_resilient(broken)


if __name__ == "__main__":
    unittest.main()
