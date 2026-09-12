from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.bot_connection import bot_reconnect_delay_seconds, watch_bot_connection


class FakeBotClient:
    """Stand-in for the connection surface of a Telethon client.

    Mirrors the real contract: ``is_connected()`` flips to False only once
    Telethon has given up reconnecting on its own, and ``disconnected`` is a
    fresh future per connection that resolves (or raises) on that give-up.
    """

    def __init__(self, connect_failures: int = 0) -> None:
        self._connected = True
        self._future: asyncio.Future = asyncio.get_event_loop().create_future()
        self.connect_calls = 0
        self.connect_failures = connect_failures

    @property
    def disconnected(self):
        return asyncio.shield(self._future)

    def is_connected(self) -> bool:
        return self._connected

    def drop(self, error: BaseException | None = None) -> None:
        self._connected = False
        if not self._future.done():
            if error is not None:
                self._future.set_exception(error)
            else:
                self._future.set_result(None)

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_failures > 0:
            self.connect_failures -= 1
            raise ConnectionError("network down")
        self._connected = True
        self._future = asyncio.get_event_loop().create_future()


class WatchBotConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, client, *, changes, should_stop, delays=None, **kwargs):
        async def fake_sleep(seconds: float) -> None:
            if delays is not None:
                delays.append(seconds)
            await asyncio.sleep(0)

        await asyncio.wait_for(
            watch_bot_connection(
                client,
                poll_seconds=0.01,
                sleep=fake_sleep,
                should_stop=should_stop,
                on_change=lambda connected, attempt: changes.append((connected, attempt)),
                **kwargs,
            ),
            timeout=5,
        )

    async def test_reconnects_after_telethon_gives_up(self):
        client = FakeBotClient()
        changes: list[tuple[bool, int]] = []
        task = asyncio.create_task(
            self._run(client, changes=changes, should_stop=lambda: len(changes) >= 2)
        )
        await asyncio.sleep(0)
        client.drop()
        await task
        self.assertEqual(client.connect_calls, 1)
        self.assertTrue(client.is_connected())
        self.assertEqual([c for c, _ in changes], [False, True])

    async def test_a_raising_disconnected_future_still_triggers_reconnect(self):
        client = FakeBotClient()
        changes: list[tuple[bool, int]] = []
        task = asyncio.create_task(
            self._run(client, changes=changes, should_stop=lambda: len(changes) >= 2)
        )
        await asyncio.sleep(0)
        client.drop(ConnectionError("Connection to Telegram failed 5 time(s)"))
        await task
        self.assertEqual(client.connect_calls, 1)
        self.assertTrue(client.is_connected())

    async def test_retries_with_backoff_until_connect_succeeds(self):
        client = FakeBotClient(connect_failures=2)
        changes: list[tuple[bool, int]] = []
        delays: list[float] = []
        task = asyncio.create_task(
            self._run(
                client,
                changes=changes,
                delays=delays,
                should_stop=lambda: len(changes) >= 2,
                delay_for=lambda attempt: attempt * 10,
            )
        )
        await asyncio.sleep(0)
        client.drop()
        await task
        self.assertEqual(client.connect_calls, 3)
        self.assertEqual(delays, [10, 20])
        self.assertTrue(client.is_connected())

    async def test_does_nothing_while_client_stays_connected(self):
        client = FakeBotClient()
        changes: list[tuple[bool, int]] = []
        ticks = {"n": 0}

        def should_stop() -> bool:
            ticks["n"] += 1
            return ticks["n"] > 3

        await self._run(client, changes=changes, should_stop=should_stop)
        self.assertEqual(client.connect_calls, 0)
        self.assertEqual(changes, [])

    async def test_shutdown_returns_immediately(self):
        client = FakeBotClient()
        client.drop()
        changes: list[tuple[bool, int]] = []
        await self._run(client, changes=changes, should_stop=lambda: True)
        self.assertEqual(client.connect_calls, 0)


class BackoffTests(unittest.TestCase):
    def test_backoff_grows_and_is_capped(self):
        first = bot_reconnect_delay_seconds(1)
        later = bot_reconnect_delay_seconds(4)
        self.assertGreater(later, first)
        self.assertLessEqual(bot_reconnect_delay_seconds(20), 320)

    def test_first_retry_is_quick(self):
        self.assertLessEqual(bot_reconnect_delay_seconds(1), 20)


if __name__ == "__main__":
    unittest.main()


class LivenessCallbackTests(unittest.IsolatedAsyncioTestCase):
    """`on_alive` is the supervisor's only liveness signal.

    The loop never returns, so start_supervised_task can never mark it healthy;
    without this callback the watchdog has nothing to measure and a dead
    supervisor looks identical to a working one.
    """

    async def _run_passes(self, client, passes: int, **kwargs):
        seen = {"n": 0}

        def should_stop() -> bool:
            seen["n"] += 1
            return seen["n"] > passes

        async def fake_sleep(seconds: float) -> None:
            await asyncio.sleep(0)

        await asyncio.wait_for(
            watch_bot_connection(
                client, poll_seconds=0.01, sleep=fake_sleep,
                should_stop=should_stop, **kwargs,
            ),
            timeout=5,
        )

    async def test_fires_once_per_pass(self):
        beats: list[int] = []
        await self._run_passes(FakeBotClient(), 3, on_alive=lambda: beats.append(1))
        self.assertEqual(len(beats), 3)

    async def test_a_throwing_callback_never_kills_the_loop(self):
        def boom() -> None:
            raise RuntimeError("bad heartbeat")

        # Must complete rather than propagate — the supervisor is more important
        # than the metric it reports.
        await self._run_passes(FakeBotClient(), 2, on_alive=boom)

    async def test_absent_callback_is_fine(self):
        await self._run_passes(FakeBotClient(), 2)
