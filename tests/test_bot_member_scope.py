"""Что видит держатель ключа в 📊 Сводке, 📈 Аналитике и 🛰 Статусе.

18.09 владелец обнаружил свой пульт в чате гостя: экран считал всю базу —
790 упоминаний, 588 важных, прогресс скана, 4154 канала — и открывался по
гранту `stats`. Теперь эти экраны для не-владельца собираются из
`analytics.build_panel_report`, того же отчёта, которым уже считала Mini App:
только аккаунты ключа и ничего про хозяйство владельца.

Проверяется ровно это: чей отчёт запрошен, что в нём нет чужих чисел, и что
владельцу ничего не урезали.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
for path in (str(ROOT), str(SRC_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from pulse_desk.app_ctx import state  # noqa: E402
from pulse_desk.bot.cards import member_analytics_card, member_summary_card  # noqa: E402
from pulse_desk.bot.keyboards import analytics_keyboard  # noqa: E402
from pulse_desk.bot.sections import analytics as analytics_section  # noqa: E402
from pulse_desk.bot.sections import dashboard as dashboard_section  # noqa: E402
from pulse_desk.bot.sections import home as home_section  # noqa: E402
from pulse_desk.bot.views import ANALYTICS_TABS, MEMBER_ANALYTICS_TABS  # noqa: E402

PERMS = {"features": ["stats", "analytics", "status"], "accounts": ["amoralniyyrod"]}

REPORT = {
    "summary": {"total": 12, "wins": 3, "giveaways": 5, "last_24h": 2, "last_7d": 7, "win_rate": 25.0},
    "daily": [{"day": "2026-09-17", "total": 5, "wins": 1, "giveaways": 2},
              {"day": "2026-09-18", "total": 7, "wins": 2, "giveaways": 3}],
    "hours": [0] * 12 + [4] + [0] * 11,
    "chats": [{"chat": "Дроп-чат", "count": 9, "wins": 2, "giveaways": 4}],
    "senders": [{"sender": "manson", "count": 6, "wins": 1}],
    "accounts": [{"name": "amoralniyyrod", "mentions": 12, "wins": 3}],
    "latency": {},
    "window_days": 30,
}


def run(coro):
    return asyncio.run(coro)


class ScopeTests(unittest.TestCase):
    """Гостю считается его ключ, владельцу — прежний глобальный отчёт."""

    def test_guest_report_is_asked_for_the_keys_accounts(self):
        with patch.object(analytics_section, "build_panel_report", new=AsyncMock(return_value=REPORT)) as panel, \
             patch.object(analytics_section, "visible_accounts", return_value=["amoralniyyrod"]):
            text, _ = run(analytics_section.render_report("sum", PERMS, is_admin=False))
        panel.assert_awaited_once_with(["amoralniyyrod"], ["amoralniyyrod"])
        self.assertIn("amoralniyyrod", text)

    def test_guest_stats_never_touch_the_global_counters(self):
        with patch.object(analytics_section, "build_panel_report", new=AsyncMock(return_value=REPORT)), \
             patch.object(analytics_section, "visible_accounts", return_value=["amoralniyyrod"]), \
             patch.object(analytics_section, "build_analytics", new=AsyncMock()) as glob:
            run(analytics_section.render_stats(PERMS, is_admin=False))
        glob.assert_not_awaited()

    def test_owner_still_gets_the_whole_base(self):
        with patch.object(analytics_section, "build_analytics", new=AsyncMock(return_value={"total_pings": 790})), \
             patch.object(analytics_section, "build_detailed_analytics", new=AsyncMock(return_value={})), \
             patch.object(analytics_section, "build_panel_report", new=AsyncMock()) as panel:
            text, kb = run(analytics_section.render_report("sum", {}, is_admin=True))
        panel.assert_not_awaited()
        self.assertIn("790", text)
        self.assertEqual(len([b for row in kb[:-1] for b in row]), len(ANALYTICS_TABS))

    def test_dashboard_gives_a_guest_the_member_screen(self):
        with patch.object(dashboard_section, "build_panel_report", new=AsyncMock(return_value=REPORT)), \
             patch.object(dashboard_section, "visible_accounts", return_value=["amoralniyyrod"]), \
             patch.object(dashboard_section, "render_cached", new=AsyncMock(return_value=None)), \
             patch.object(dashboard_section, "collect_dashboard", new=AsyncMock()) as pult:
            text, kb, _ = run(dashboard_section.render_member(PERMS))
        pult.assert_not_awaited()
        self.assertIn("Упоминаний", text)
        data = [b.data for row in kb for b in row]
        # Пультовые переходы владельца гостю не предлагаются.
        self.assertNotIn(b"menu_scan", data)
        self.assertNotIn(b"menu_status", data)
        self.assertIn(b"menu_summary", data)


class HomeTests(unittest.TestCase):
    """Главный экран — первое, что видит гость, и первый же источник утечки."""

    def test_guest_home_counts_only_their_accounts(self):
        with patch.object(home_section, "build_panel_report", new=AsyncMock(return_value=REPORT)), \
             patch("pulse_desk.bot.sections.giveaways.visible_accounts", return_value=["amoralniyyrod"]), \
             patch.object(home_section, "build_home_counters", new=AsyncMock()) as counters:
            text = run(home_section.render_home("viewer", PERMS))
        counters.assert_not_awaited()
        self.assertIn("amoralniyyrod", text)
        for leak in ("Скан", "Новых пингов"):
            self.assertNotIn(leak, text)

    def test_owner_home_is_untouched(self):
        with patch.object(home_section, "build_home_counters",
                          new=AsyncMock(return_value={"new_pings": 554, "accounts_online": 8})), \
             patch.object(home_section, "giveaway_bucket_total", new=AsyncMock(return_value=120)), \
             patch.object(home_section, "build_panel_report", new=AsyncMock()) as panel:
            text = run(home_section.render_home("admin", {}))
        panel.assert_not_awaited()
        self.assertIn("554", text)
        self.assertIn("120", text)


class MemberCardTests(unittest.TestCase):
    def test_summary_card_counts_only_the_key(self):
        text = member_summary_card(REPORT, ["amoralniyyrod"])
        self.assertIn("@amoralniyyrod", text)
        self.assertIn("12", text)
        # Ни прогресса скана, ни каналов, ни проблем аккаунтов.
        for leak in ("скан", "каналов", "Uptime", "База"):
            self.assertNotIn(leak, text)

    def test_every_member_tab_renders(self):
        for code, _ in MEMBER_ANALYTICS_TABS:
            text = member_analytics_card(code, REPORT, ["amoralniyyrod"])
            self.assertTrue(text.strip(), code)

    def test_unknown_tab_falls_back_to_the_overview(self):
        self.assertEqual(
            member_analytics_card("flow", REPORT, ["amoralniyyrod"]),
            member_analytics_card("sum", REPORT, ["amoralniyyrod"]),
        )

    def test_member_tab_strip_drops_the_owner_only_page(self):
        rows = analytics_keyboard("sum", MEMBER_ANALYTICS_TABS)
        data = [b.data for row in rows for b in row]
        self.assertNotIn(b"an:flow", data)
        self.assertIn(b"an:lat", data)


class StatusTests(unittest.TestCase):
    def setUp(self):
        self._accounts = dict(state.accounts_state)
        state.accounts_state.clear()
        state.accounts_state.update({
            "sess1": {"session_name": "sess1", "username": "amoralniyyrod", "status": "online"},
            "sess2": {"session_name": "sess2", "username": "MuverGT", "status": "offline"},
        })
        self.addCleanup(self._restore)

    def _restore(self):
        state.accounts_state.clear()
        state.accounts_state.update(self._accounts)

    def test_guest_sees_only_their_own_account(self):
        with patch.object(analytics_section, "visible_accounts", return_value=["amoralniyyrod"]):
            text = analytics_section.member_status(PERMS)
        self.assertIn("amoralniyyrod", text)
        self.assertNotIn("MuverGT", text)
        # Хозяйство владельца из статуса ушло.
        for leak in ("Uptime", "База", "Jobs", "Скан"):
            self.assertNotIn(leak, text)

    def test_key_without_a_whitelist_still_sees_the_fleet(self):
        # Пустой список аккаунтов в ключе означает «все» — так же, как в панели.
        with patch.object(analytics_section, "visible_accounts", return_value=["amoralniyyrod", "MuverGT"]):
            text = analytics_section.member_status({"accounts": []})
        self.assertIn("amoralniyyrod", text)
        self.assertIn("MuverGT", text)


if __name__ == "__main__":
    unittest.main()
