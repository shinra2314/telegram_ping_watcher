"""Tests for the launcher: service-manifest parsing + process supervision."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = BASE_DIR / "src"
for path in (str(BASE_DIR), str(SRC_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from pulse_desk.process_supervisor import (  # noqa: E402
    ManagedService,
    ServiceSupervisor,
    _backoff,
)
from pulse_desk.service_registry import load_services  # noqa: E402


def _write_manifest(tmp: Path, entries: list[dict]) -> Path:
    path = tmp / "services.json"
    path.write_text(json.dumps({"services": entries}), encoding="utf-8")
    return path


class RegistryTests(unittest.TestCase):
    def test_parses_and_expands_paths(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            os.environ["PULSE_TEST_BOTDIR"] = str(tmp / "bot")
            manifest = _write_manifest(tmp, [
                {
                    "name": "discord-bot",
                    "label": "Discord Bot",
                    "cmd": ["node", "src/index.js"],
                    "cwd": "${PULSE_TEST_BOTDIR}",
                    "health": {"host": "127.0.0.1", "port": 8080},
                    "panel_url": "http://127.0.0.1:8080",
                    "autostart": True,
                },
            ])
            os.environ["PULSE_SERVICES_FILE"] = str(manifest)
            try:
                services = load_services()
            finally:
                del os.environ["PULSE_SERVICES_FILE"]
                del os.environ["PULSE_TEST_BOTDIR"]
            self.assertEqual(len(services), 1)
            svc = services[0]
            self.assertEqual(svc.name, "discord-bot")
            self.assertTrue(svc.cwd.endswith("bot"))
            self.assertNotIn("$", svc.cwd)
            self.assertEqual(svc.health_port, 8080)
            self.assertTrue(svc.autostart)
            self.assertTrue(svc.has_health_probe())

    def test_skips_invalid_and_dedupes(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            manifest = _write_manifest(tmp, [
                {"name": "", "cmd": ["x"]},          # invalid: no name
                {"name": "a", "cmd": []},            # invalid: empty cmd
                {"name": "ok", "cmd": ["echo", "1"]},
                {"name": "ok", "cmd": ["echo", "2"]},  # duplicate -> dropped
            ])
            os.environ["PULSE_SERVICES_FILE"] = str(manifest)
            try:
                services = load_services()
            finally:
                del os.environ["PULSE_SERVICES_FILE"]
            self.assertEqual([s.name for s in services], ["ok"])

    def test_missing_manifest_returns_empty(self):
        os.environ["PULSE_SERVICES_FILE"] = str(Path(tempfile.gettempdir()) / "does-not-exist-xyz.json")
        try:
            self.assertEqual(load_services(), [])
        finally:
            del os.environ["PULSE_SERVICES_FILE"]


class BackoffTests(unittest.TestCase):
    def test_backoff_grows_and_caps(self):
        svc = ManagedService(name="x", label="x", cmd=["x"], backoff_base=3.0, backoff_max=120.0)
        self.assertEqual(_backoff(svc, 1), 3.0)
        self.assertEqual(_backoff(svc, 2), 6.0)
        self.assertEqual(_backoff(svc, 3), 12.0)
        self.assertEqual(_backoff(svc, 99), 120.0)  # capped


class SupervisorLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_running_then_stop(self):
        sup = ServiceSupervisor()
        sup.register(ManagedService(
            name="sleeper",
            label="Sleeper",
            cmd=[sys.executable, "-c", "import time; time.sleep(30)"],
            autorestart=False,
        ))
        status = await sup.start("sleeper")
        self.assertTrue(status["running"])
        self.assertIsNotNone(status["pid"])
        self.assertEqual(status["status"], "running")
        logs = sup.logs("sleeper")
        self.assertTrue(any("started pid=" in line for line in logs))

        status = await sup.stop("sleeper")
        self.assertFalse(status["running"])
        self.assertEqual(status["status"], "stopped")

    async def test_quick_exit_marked_not_running_and_captures_output(self):
        sup = ServiceSupervisor()
        sup.register(ManagedService(
            name="oneshot",
            label="One Shot",
            cmd=[sys.executable, "-c", "print('hello-from-child')"],
            autorestart=False,
        ))
        await sup.start("oneshot")
        # Watch loop polls every ~1s; give it room to observe the exit.
        import asyncio
        for _ in range(40):
            if not sup.status("oneshot")["running"]:
                break
            await asyncio.sleep(0.1)
        self.assertFalse(sup.status("oneshot")["running"])
        self.assertTrue(any("hello-from-child" in line for line in sup.logs("oneshot")))
        await sup.shutdown()

    async def test_unknown_service_raises(self):
        sup = ServiceSupervisor()
        with self.assertRaises(KeyError):
            await sup.start("nope")


if __name__ == "__main__":
    unittest.main()
