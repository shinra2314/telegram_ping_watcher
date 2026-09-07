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


if __name__ == "__main__":
    unittest.main()
