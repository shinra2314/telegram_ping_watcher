from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import unittest

from pulse_desk.bot.cards import home_card, summary_card


class HomeCardTests(unittest.TestCase):
    def _card(self, **over):
        data = dict(
            role="admin", new_pings=12, urgent=3, accounts_online=4,
            accounts_total=4, fresh_checks=2, last_scan="06-26 14:02 · ok",
        )
        data.update(over)
        return home_card(**data)

    def test_shows_brand_and_owner_badge(self):
        out = self._card(role="admin")
        self.assertIn("**PULSE DESK**", out)
        self.assertIn("владелец", out)

    def test_viewer_badge(self):
        self.assertIn("просмотр", self._card(role="viewer"))

    def test_counters_present_and_monospaced(self):
        out = self._card()
        self.assertIn("Новых пингов: `12`", out)
        self.assertIn("Срочных: `3`", out)
        self.assertIn("Аккаунты: `4/4`", out)
        self.assertIn("Чеки: `2`", out)
        self.assertIn("Скан: `06-26 14:02 · ok`", out)

    def test_has_section_prompt(self):
        self.assertIn("Выберите раздел", self._card())

    def test_accounts_signal_bar_and_urgent_flag(self):
        out = self._card(urgent=2, accounts_online=3, accounts_total=5)
        self.assertIn("▰", out)          # signal bar present
        self.assertIn("Аккаунты: `3/5`", out)
        self.assertIn("🔥", out)         # urgent>0 flagged

    def test_no_fire_when_no_urgent(self):
        self.assertNotIn("🔥", self._card(urgent=0))


class SummaryCardTests(unittest.TestCase):
    ANALYTICS = {"total_pings": 1240, "new_pings": 12, "favorites": 8}
    MARKET = {"btc": 98420, "eth": 3510, "ton": 5.23, "sol": 182.4}
    SYSTEM = {
        "version": "1.5.0", "uptime": "12ч 30м", "db_mb": 8.4,
        "accounts_online": 4, "accounts_total": 4, "last_scan": "06-26 14:02 · ok",
    }

    def test_header_and_three_blocks(self):
        out = summary_card(analytics=self.ANALYTICS, market=self.MARKET, system=self.SYSTEM)
        self.assertIn("**СВОДКА**", out)
        self.assertIn("Записей: `1240`", out)
        self.assertIn("BTC", out)
        self.assertIn("Система", out)
        self.assertIn("v1.5.0", out)

    def test_market_values_rendered(self):
        out = summary_card(analytics=self.ANALYTICS, market=self.MARKET, system=self.SYSTEM)
        self.assertIn("98,420", out)
        self.assertIn("5.230", out)

    def test_missing_market_shows_empty_state(self):
        out = summary_card(analytics=self.ANALYTICS, market=None, system=self.SYSTEM)
        self.assertIn("📭", out)
        self.assertNotIn("BTC", out)


from pulse_desk.bot.cards import feed_badge, feed_header, ping_card


class FeedRenderTests(unittest.TestCase):
    def test_badge_by_priority(self):
        self.assertEqual(feed_badge("critical"), "🔥")
        self.assertEqual(feed_badge("high"), "⚡")
        self.assertEqual(feed_badge("normal"), "•")

    def test_header_has_breadcrumb_and_count(self):
        out = feed_header("Чеки", 5)
        self.assertIn("**МОНИТОРИНГ**", out)
        self.assertIn("Чеки", out)
        self.assertIn("5", out)

    def test_empty_feed_header(self):
        out = feed_header("Все", 0)
        self.assertIn("📭", out)


class PingCardTests(unittest.TestCase):
    PING = {
        "id": 842, "detected_at": "2026-06-26T14:02:31", "chat": "@chan",
        "priority_label": "critical", "is_giveaway": 1, "is_win": 0, "is_check": 0,
        "text": "Поздравляем, вы выиграли подарок номер 17 в нашем розыгрыше!",
        "link": "https://t.me/chan/123",
    }

    def test_shows_id_chat_and_full_text(self):
        out = ping_card(self.PING)
        self.assertIn("#842", out)
        self.assertIn("@chan", out)
        self.assertIn("подарок номер 17", out)
        self.assertIn("https://t.me/chan/123", out)

    def test_shows_giveaway_tag(self):
        self.assertIn("🎁", ping_card(self.PING))

    def test_tags_rendered_as_chips(self):
        self.assertIn("「🎁 розыгрыш」", ping_card(self.PING))

    def test_missing_text_falls_back(self):
        out = ping_card(dict(self.PING, text=None, link=None))
        self.assertIn("—", out)


from pulse_desk.bot.cards import giveaway_card, giveaways_header


class GiveawaysHeaderTests(unittest.TestCase):
    STATS = {"claim_prize": 3, "overdue": 2, "waiting_result": 5}

    def test_shows_counts(self):
        out = giveaways_header(self.STATS, need_count=4)
        self.assertIn("**РОЗЫГРЫШИ**", out)
        self.assertIn("К действию: `4`", out)
        self.assertIn("Призы: `3`", out)
        self.assertIn("Просрочено: `2`", out)

    def test_empty_need_shows_calm_state(self):
        out = giveaways_header(self.STATS, need_count=0)
        self.assertIn("📭", out)


class GiveawayCardTests(unittest.TestCase):
    PING = {
        "id": 50, "detected_at": "2026-06-26T10:00:00", "deadline_at": "2026-06-27T18:00:00",
        "chat": "@gw", "priority_label": "high",
        "text": "Розыгрыш 100 TON среди подписчиков!", "link": "https://t.me/gw/9",
    }

    def test_shows_deadline_id_text_link(self):
        out = giveaway_card(self.PING)
        self.assertIn("#50", out)
        self.assertIn("06-27 18:00", out)
        self.assertIn("100 TON", out)
        self.assertIn("https://t.me/gw/9", out)

    def test_missing_deadline_shows_dash(self):
        out = giveaway_card(dict(self.PING, deadline_at=None))
        self.assertIn("Дедлайн: `—`", out)


from pulse_desk.bot.cards import management_card, member_card, members_header


class ManagementCardTests(unittest.TestCase):
    def test_management_header(self):
        self.assertIn("**УПРАВЛЕНИЕ**", management_card())

    def test_members_header_count(self):
        self.assertIn("3", members_header(3))

    def test_members_header_empty(self):
        self.assertIn("📭", members_header(0))


class MemberCardTests(unittest.TestCase):
    MEMBER = {
        "tg_id": 7, "tg_username": "ivan", "name": "Иван",
        "key_label": "friends", "blocked": 0, "last_seen_at": "2026-06-26T13:50:00",
    }

    def test_shows_username_key_and_open_state(self):
        out = member_card(self.MEMBER, access_open=True)
        self.assertIn("@ivan", out)
        self.assertIn("friends", out)
        self.assertIn("активен", out)
        self.assertIn("открыт", out)

    def test_blocked_and_closed(self):
        out = member_card(dict(self.MEMBER, blocked=1), access_open=False)
        self.assertIn("заблокирован", out)
        self.assertIn("закрыт", out)


from pulse_desk.bot.cards import keys_card, restart_confirm_card, scan_card


class ScanCardTests(unittest.TestCase):
    def test_idle_shows_last_scan(self):
        out = scan_card({"running": False, "last_error": None}, "06-26 14:02 · ok")
        self.assertIn("**СКАН**", out)
        self.assertIn("не запущен", out)
        self.assertIn("06-26 14:02 · ok", out)

    def test_idle_shows_error_if_any(self):
        out = scan_card({"running": False, "last_error": "FloodWait"}, "—")
        self.assertIn("FloodWait", out)

    def test_running_shows_progress_and_current(self):
        status = {
            "running": True, "processed_accounts": 1, "total_accounts": 2,
            "current_channel": "@chan", "found": 7,
        }
        out = scan_card(status, "—")
        self.assertIn("Идёт сканирование", out)
        self.assertIn("`1/2`", out)
        self.assertIn("@chan", out)
        self.assertIn("Найдено: `7`", out)
        self.assertIn("▰", out)


class RestartConfirmCardTests(unittest.TestCase):
    def test_asks_for_confirmation(self):
        out = restart_confirm_card()
        self.assertIn("**ПЕРЕЗАПУСК**", out)
        self.assertIn("?", out)


class KeysCardTests(unittest.TestCase):
    def test_empty_state(self):
        out = keys_card([])
        self.assertIn("**КЛЮЧИ ДОСТУПА**", out)
        self.assertIn("📭", out)

    def test_lists_keys_with_member_count_and_expiry(self):
        keys = [
            {"id": 3, "label": "друзья", "member_count": 2, "expires_at": None},
            {"id": 5, "label": "", "member_count": 0, "expires_at": "2026-07-10T00:00:00"},
        ]
        out = keys_card(keys)
        self.assertIn("#3", out)
        self.assertIn("друзья", out)
        self.assertIn("👥 2", out)
        self.assertIn("бессрочно", out)
        self.assertIn("07-10 00:00", out)


if __name__ == "__main__":
    unittest.main()
