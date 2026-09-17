"""The «🛰 Панель» button: present only while the tunnel is up."""
from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import unittest

from pulse_desk.bot.keyboards import webapp_row
from pulse_desk.bot.views import main_menu_buttons

URL = "https://desk.tail1234.ts.net"


class WebappRowTests(unittest.TestCase):
    def setUp(self):
        from pulse_desk.app_ctx import state

        self.state = state
        self.addCleanup(setattr, state, "public_url", None)

    def test_no_tunnel_means_no_row(self):
        self.state.public_url = None
        self.assertEqual(webapp_row("Панель", "/app"), [])

    def test_empty_url_means_no_row(self):
        self.state.public_url = ""
        self.assertEqual(webapp_row("Панель", "/app"), [])

    def test_row_carries_the_full_url(self):
        self.state.public_url = URL
        row = webapp_row("Панель", "/app")
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0].url, f"{URL}/app")
        self.assertEqual(row[0].text, "Панель")

    def test_trailing_slash_is_not_doubled(self):
        self.state.public_url = URL + "/"
        self.assertEqual(webapp_row("Панель", "/app")[0].url, f"{URL}/app")

    def test_default_path_is_the_app_root(self):
        self.state.public_url = URL
        self.assertEqual(webapp_row("Панель")[0].url, f"{URL}/app")

    def test_button_is_an_inline_web_view(self):
        from telethon.tl.custom.button import Button as TButton
        from telethon.tl.types import KeyboardButtonWebView

        self.state.public_url = URL
        button = webapp_row("Панель", "/app")[0]
        self.assertIsInstance(button, KeyboardButtonWebView)
        self.assertTrue(TButton._is_inline(button))


class MainMenuWebappTests(unittest.TestCase):
    def setUp(self):
        from pulse_desk.app_ctx import state

        self.state = state
        self.addCleanup(setattr, state, "public_url", None)

    def test_no_tunnel_leaves_the_menu_unchanged(self):
        self.state.public_url = None
        rows = main_menu_buttons("admin")
        self.assertTrue(all(getattr(b, "url", None) is None for row in rows for b in row))

    def test_tunnel_adds_one_panel_row_on_top(self):
        self.state.public_url = URL
        rows = main_menu_buttons("admin")
        self.assertEqual(len(rows[0]), 1)
        self.assertEqual(rows[0][0].url, f"{URL}/app")
        self.assertEqual(rows[0][0].text, "🛰 Панель")

    def test_panel_row_does_not_replace_the_inline_sections(self):
        self.state.public_url = URL
        with_tunnel = main_menu_buttons("admin")
        self.state.public_url = None
        without = main_menu_buttons("admin")
        self.assertEqual(len(with_tunnel), len(without) + 1)

    def test_guest_also_gets_the_panel(self):
        self.state.public_url = URL
        rows = main_menu_buttons("viewer")
        self.assertEqual(rows[0][0].url, f"{URL}/app")


if __name__ == "__main__":
    unittest.main()
