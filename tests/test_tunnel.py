from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.tunnel import (
    cloudflared_argv,
    extract_ngrok_url,
    extract_tailscale_url,
    extract_tunnel_url,
    ngrok_argv,
    tailscale_argv,
)

# Verbatim shapes from `cloudflared tunnel --url`, which prints the address
# inside a box drawn with +---+ and | characters.
BANNER = (
    "2026-09-07T09:00:02Z INF +----------------------------------------+",
    "2026-09-07T09:00:02Z INF |  Your quick Tunnel has been created!    |",
    "2026-09-07T09:00:02Z INF |  https://busy-fox-quiet-lamp.trycloudflare.com  |",
    "2026-09-07T09:00:02Z INF +----------------------------------------+",
)


class ExtractTunnelUrlTests(unittest.TestCase):
    def test_pulls_the_url_out_of_the_banner(self):
        found = [u for u in (extract_tunnel_url(line) for line in BANNER) if u]
        self.assertEqual(found, ["https://busy-fox-quiet-lamp.trycloudflare.com"])

    def test_ignores_ordinary_log_lines(self):
        self.assertIsNone(
            extract_tunnel_url("2026-09-07T09:00:01Z INF Requesting new quick Tunnel")
        )

    def test_ignores_the_api_host(self):
        self.assertIsNone(extract_tunnel_url("INF Connecting to api.trycloudflare.com:443"))

    def test_ignores_a_plain_http_url(self):
        self.assertIsNone(extract_tunnel_url("INF proxying to http://127.0.0.1:8010"))

    def test_handles_digits_in_the_subdomain(self):
        line = "INF |  https://fox-42-lamp-7.trycloudflare.com  |"
        self.assertEqual(extract_tunnel_url(line), "https://fox-42-lamp-7.trycloudflare.com")

    def test_empty_line(self):
        self.assertIsNone(extract_tunnel_url(""))

    def test_none_line(self):
        self.assertIsNone(extract_tunnel_url(None))



# Verbatim shape of `ngrok http --log stdout --log-format logfmt`. The same line
# carries `addr=`, which is why only the `url=` key may match.
NGROK_STARTED = (
    't=2026-09-07T15:20:01+0000 lvl=info msg="starting web service" obj=web addr=127.0.0.1:4040',
    't=2026-09-07T15:20:02+0000 lvl=info msg="started tunnel" obj=tunnels name=command_line '
    'addr=http://localhost:8010 url=https://pulse-desk.ngrok-free.app',
)


class ExtractNgrokUrlTests(unittest.TestCase):
    def test_pulls_the_url_from_the_started_line(self):
        found = [u for u in (extract_ngrok_url(line) for line in NGROK_STARTED) if u]
        self.assertEqual(found, ["https://pulse-desk.ngrok-free.app"])

    def test_ignores_the_local_addr_key(self):
        self.assertIsNone(extract_ngrok_url(
            't=1 lvl=info msg="starting web service" addr=127.0.0.1:4040'))

    def test_ignores_a_plain_http_url(self):
        self.assertIsNone(extract_ngrok_url('t=1 msg="x" url=http://localhost:8010'))

    def test_trailing_slash_is_dropped(self):
        self.assertEqual(
            extract_ngrok_url('t=1 url=https://pulse-desk.ngrok-free.app/'),
            "https://pulse-desk.ngrok-free.app",
        )

    def test_reserved_domain_form(self):
        self.assertEqual(
            extract_ngrok_url('t=1 url=https://pulse.ngrok.app'),
            "https://pulse.ngrok.app",
        )

    def test_empty_line(self):
        self.assertIsNone(extract_ngrok_url(""))

    def test_none_line(self):
        self.assertIsNone(extract_ngrok_url(None))

    def test_cloudflared_extractor_does_not_match_ngrok(self):
        self.assertIsNone(extract_tunnel_url(NGROK_STARTED[1]))


class ArgvTests(unittest.TestCase):
    def test_cloudflared_points_at_the_miniapp_port(self):
        self.assertEqual(
            cloudflared_argv("cloudflared", 8010),
            ["cloudflared", "tunnel", "--url", "http://127.0.0.1:8010", "--no-autoupdate"],
        )

    def test_ngrok_without_a_reserved_domain(self):
        self.assertEqual(
            ngrok_argv("ngrok", 8010),
            ["ngrok", "http", "8010", "--log", "stdout", "--log-format", "logfmt"],
        )

    def test_ngrok_pins_a_reserved_domain(self):
        argv = ngrok_argv("ngrok", 8010, "pulse-desk.ngrok-free.app")
        self.assertEqual(argv[-2:], ["--domain", "pulse-desk.ngrok-free.app"])

    def test_ngrok_empty_domain_adds_no_flag(self):
        self.assertNotIn("--domain", ngrok_argv("ngrok", 8010, ""))


# Verbatim shape of `tailscale funnel <port>` in the foreground. The proxied
# target on the third line is plain http and must never be taken for the
# public address.
FUNNEL = (
    "Available on the internet:",
    "",
    "https://desktop-pulse.tail1a2b3c.ts.net/",
    "|-- proxy http://127.0.0.1:8010",
    "",
    "Press Ctrl+C to exit.",
)


class ExtractTailscaleUrlTests(unittest.TestCase):
    def test_pulls_the_funnel_address(self):
        found = [u for u in (extract_tailscale_url(line) for line in FUNNEL) if u]
        self.assertEqual(found, ["https://desktop-pulse.tail1a2b3c.ts.net"])

    def test_ignores_the_proxied_local_target(self):
        self.assertIsNone(extract_tailscale_url("|-- proxy http://127.0.0.1:8010"))

    def test_trailing_slash_is_dropped(self):
        self.assertEqual(
            extract_tailscale_url("https://pc.tail42.ts.net/"),
            "https://pc.tail42.ts.net",
        )

    def test_deeper_tailnet_label(self):
        self.assertEqual(
            extract_tailscale_url("https://pc.user-github.ts.net/"),
            "https://pc.user-github.ts.net",
        )

    def test_empty_line(self):
        self.assertIsNone(extract_tailscale_url(""))

    def test_none_line(self):
        self.assertIsNone(extract_tailscale_url(None))

    def test_other_extractors_do_not_match_a_funnel_line(self):
        self.assertIsNone(extract_tunnel_url(FUNNEL[2]))
        self.assertIsNone(extract_ngrok_url(FUNNEL[2]))

    def test_funnel_extractor_does_not_match_the_other_providers(self):
        self.assertIsNone(extract_tailscale_url(BANNER[2]))
        self.assertIsNone(extract_tailscale_url(NGROK_STARTED[1]))


class TailscaleArgvTests(unittest.TestCase):
    def test_foreground_funnel_on_the_miniapp_port(self):
        self.assertEqual(tailscale_argv("tailscale", 8010), ["tailscale", "funnel", "8010"])

    def test_port_is_stringified(self):
        self.assertTrue(all(isinstance(a, str) for a in tailscale_argv("tailscale", 8010)))


class OrphanAgentTests(unittest.TestCase):
    """An agent left by a killed app holds the :443 listener; ours must not be touched."""

    FUNNEL_CMD = '"C:/Program Files/Tailscale\\tailscale.exe" funnel 8010'

    def test_recognises_the_funnel_for_our_port_only(self):
        from pulse_desk.tunnel import is_agent_for_port

        self.assertTrue(is_agent_for_port(self.FUNNEL_CMD, "tailscale", 8010))
        self.assertFalse(is_agent_for_port('"tailscale.exe" funnel 18010', "tailscale", 8010))
        self.assertFalse(is_agent_for_port('"tailscale.exe" status', "tailscale", 8010))
        self.assertTrue(is_agent_for_port("cloudflared tunnel --url http://127.0.0.1:8010", "cloudflared", 8010))
        self.assertTrue(is_agent_for_port("ngrok http 8010 --log stdout", "ngrok", 8010))

    def test_only_agents_with_a_dead_parent_are_orphans(self):
        from pulse_desk.tunnel import orphaned_agents

        processes = [
            {"pid": 1, "command_line": self.FUNNEL_CMD, "parent_alive": False},
            {"pid": 2, "command_line": self.FUNNEL_CMD, "parent_alive": True},
            {"pid": 3, "command_line": '"tailscale.exe" funnel 9000', "parent_alive": False},
        ]
        self.assertEqual(orphaned_agents(processes, "tailscale", 8010), [1])
        self.assertEqual(orphaned_agents([], "tailscale", 8010), [])


class OutageBookkeepingTests(unittest.TestCase):
    def test_retry_delay_backs_off_and_caps(self):
        from pulse_desk import tunnel

        delays = [tunnel.retry_delay(n) for n in range(1, 8)]
        self.assertEqual(delays[:4], [10.0, 20.0, 40.0, 80.0])
        self.assertEqual(max(delays), tunnel.MAX_RETRY_DELAY_SECONDS)

    def test_failure_reason_is_the_last_non_empty_line(self):
        from pulse_desk.tunnel import failure_reason

        self.assertEqual(failure_reason(["a", "unexpected state: NoState", " "], 1),
                         "unexpected state: NoState")
        self.assertEqual(failure_reason([], 3), "exit code 3")

    def test_same_cause_is_recorded_once_per_streak(self):
        from pulse_desk.tunnel import TunnelHealth, note_failure, note_up

        health = TunnelHealth()
        self.assertTrue(note_failure(health, "unexpected state: NoState"))
        self.assertFalse(note_failure(health, "unexpected state: NoState"))
        self.assertTrue(note_failure(health, "listener already exists for port 443"))
        self.assertFalse(note_up(health))
        self.assertTrue(note_failure(health, "listener already exists for port 443"))

    def test_orphan_scan_only_on_fresh_start_or_busy_port(self):
        from pulse_desk.tunnel import TunnelHealth, note_failure, orphan_scan_due

        health = TunnelHealth()
        self.assertTrue(orphan_scan_due(health))
        note_failure(health, "unexpected state: NoState")
        self.assertFalse(orphan_scan_due(health))
        note_failure(health, "sending serve config: listener already exists for port 443")
        self.assertTrue(orphan_scan_due(health))

    def test_down_alert_once_after_the_threshold(self):
        from datetime import datetime, timedelta

        from pulse_desk import tunnel

        now = datetime(2026, 9, 13, 10, 0)
        health = tunnel.TunnelHealth(down_since=now)
        self.assertFalse(tunnel.down_alert_due(health, now + timedelta(minutes=14)))
        self.assertTrue(tunnel.down_alert_due(health, now + timedelta(minutes=15)))
        health.alerted = True
        self.assertFalse(tunnel.down_alert_due(health, now + timedelta(hours=3)))
        self.assertTrue(tunnel.note_up(health))
        self.assertIsNone(health.down_since)


class TunnelLoopTests(unittest.IsolatedAsyncioTestCase):
    """The loop against a real child process that fails the way tailscale does at boot."""

    def setUp(self):
        from pulse_desk import tunnel

        self.tunnel = tunnel
        self.events: list[tuple] = []
        self.told: list[str] = []
        self.scans = 0
        saved = {name: getattr(tunnel, name) for name in (
            "_health", "provider_spec", "record_app_event", "_tell_owner",
            "kill_orphaned_agents", "_bind_to_app_lifetime", "RESTART_DELAY_SECONDS",
            "MAX_RETRY_DELAY_SECONDS")}
        saved_url = tunnel.state.public_url

        def restore():
            for name, value in saved.items():
                setattr(tunnel, name, value)
            tunnel.state.public_url = saved_url

        self.addCleanup(restore)

        async def record(level, source, message, context=None):
            self.events.append((level, message, context))

        async def tell(text):
            self.told.append(text)

        async def scan(provider, port):
            self.scans += 1
            return []

        tunnel._health = tunnel.TunnelHealth()
        tunnel.record_app_event = record
        tunnel._tell_owner = tell
        tunnel.kill_orphaned_agents = scan
        tunnel._bind_to_app_lifetime = lambda pid: None
        tunnel.RESTART_DELAY_SECONDS = 0.0
        tunnel.MAX_RETRY_DELAY_SECONDS = 0.0
        tunnel.state.public_url = None

    def _agent(self, script: str):
        argv = [sys.executable, "-c", script]
        self.tunnel.provider_spec = lambda: ("tailscale", argv, self.tunnel.extract_tailscale_url)

    async def test_boot_retries_are_quiet_then_alert_then_recover(self):
        from datetime import datetime, timedelta

        self._agent("print('unexpected state: NoState'); raise SystemExit(1)")
        for _ in range(3):
            await self.tunnel.tunnel_loop()
        exits = [e for e in self.events if e[1] == "tailscale exited"]
        self.assertEqual(len(exits), 1)
        self.assertEqual(self.scans, 1)
        self.assertEqual(self.told, [])

        self.tunnel._health.down_since = datetime.now() - timedelta(minutes=16)
        await self.tunnel.tunnel_loop()
        await self.tunnel.tunnel_loop()
        self.assertEqual(len(self.told), 1)
        self.assertIn("NoState", self.told[0])

        self._agent("print('https://desk.tail1.ts.net'); raise SystemExit(0)")
        await self.tunnel.tunnel_loop()
        self.assertEqual(len(self.told), 2)
        self.assertIn("снова доступна", self.told[1])


if __name__ == "__main__":
    unittest.main()
