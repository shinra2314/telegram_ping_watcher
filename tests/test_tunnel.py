from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.tunnel import extract_tunnel_url

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


if __name__ == "__main__":
    unittest.main()
