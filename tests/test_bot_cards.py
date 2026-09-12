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
            accounts_total=4, last_scan="06-26 14:02 · ok",
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
        self.assertIn("К действию: `3`", out)
        self.assertIn("Аккаунты: `4/4`", out)
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
        "priority_label": "critical", "is_giveaway": 1, "is_win": 0,
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


from pulse_desk.bot.cards import giveaway_accounts_card, giveaway_card, giveaways_header
from pulse_desk.bot.views import GiveawayFilter


class GiveawaysHeaderTests(unittest.TestCase):
    STATS = {"claim_prize": 3, "done": 2, "waiting_result": 5}

    def test_shows_counts(self):
        out = giveaways_header(self.STATS, need_count=4)
        self.assertIn("**РОЗЫГРЫШИ**", out)
        self.assertIn("К действию: `4`", out)
        self.assertIn("Призы: `3`", out)
        self.assertIn("Ждут: `5`", out)
        self.assertIn("Закрыто: `2`", out)

    def test_empty_need_shows_calm_state(self):
        out = giveaways_header(self.STATS, need_count=0)
        self.assertIn("📭", out)

    def test_default_filter_line(self):
        out = giveaways_header(self.STATS, need_count=4)
        self.assertIn("Обнаружено", out)
        self.assertIn("👤 Все", out)

    def test_filter_line_reflects_active_state(self):
        out = giveaways_header(
            self.STATS, need_count=2, state=GiveawayFilter(sort="p", wins=True), account="@muver",
        )
        self.assertIn("Дата поста", out)
        self.assertIn("только победы", out)
        self.assertIn("@muver", out)

    def test_empty_filtered_view_says_so(self):
        out = giveaways_header(self.STATS, need_count=0, state=GiveawayFilter(wins=True))
        self.assertIn("фильтр", out)


class GiveawayAccountsCardTests(unittest.TestCase):
    def test_lists_every_account_with_counts(self):
        out = giveaway_accounts_card({"muver": {"wins": 2, "giveaways": 5}}, ["muver", "other"])
        self.assertIn("@muver", out)
        self.assertIn("🏆 `2`", out)
        self.assertIn("🎁 `5`", out)
        self.assertIn("@other", out)   # tracked but silent — still listed, at zero
        self.assertIn("🏆 `0`", out)

    def test_no_accounts_shows_empty_state(self):
        self.assertIn("📭", giveaway_accounts_card({}, []))


class GiveawayCardTests(unittest.TestCase):
    PING = {
        "id": 50, "detected_at": "2026-06-26T10:00:00",
        "chat": "@gw", "priority_label": "high",
        "text": "Розыгрыш 100 TON среди подписчиков!", "link": "https://t.me/gw/9",
    }

    def test_shows_id_detected_at_text_link(self):
        out = giveaway_card(self.PING)
        self.assertIn("#50", out)
        self.assertIn("06-26 10:00", out)
        self.assertIn("100 TON", out)
        self.assertIn("https://t.me/gw/9", out)

    def test_shows_source_chat(self):
        self.assertIn("@gw", giveaway_card(self.PING))

    def test_shows_both_message_and_detection_dates(self):
        out = giveaway_card(dict(self.PING, date="2026-06-24T21:30:00"))
        self.assertIn("06-24 21:30", out)  # when the post was published
        self.assertIn("06-26 10:00", out)  # when the scan noticed it

    def test_missing_message_date_falls_back_to_dash(self):
        self.assertIn("—", giveaway_card(self.PING))


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

    def test_revoked_and_expired_keys_are_marked(self):
        out = keys_card([
            {"id": 3, "label": "старый", "member_count": 0, "expires_at": None, "revoked": 1},
            {"id": 4, "label": "просроченный", "member_count": 0, "expires_at": "2020-01-01T00:00:00"},
        ])
        self.assertIn("отозван", out)
        self.assertIn("истёк", out)


from pulse_desk.bot.cards import (
    key_delete_card, key_expiry_card, key_members_card, key_panel_card, key_state_badge,
)
from pulse_desk.bot_permissions import full_permissions


class KeyPanelCardTests(unittest.TestCase):
    KEY = {"id": 3, "label": "друзья", "role": "viewer", "revoked": 0,
           "expires_at": None, "member_count": 2}

    def test_state_badge_covers_active_revoked_expired(self):
        self.assertIn("активен", key_state_badge(self.KEY))
        self.assertIn("отозван", key_state_badge(dict(self.KEY, revoked=1)))
        self.assertIn("истёк", key_state_badge(dict(self.KEY, expires_at="2020-01-01T00:00:00")))

    def test_panel_shows_label_role_and_grants(self):
        out = key_panel_card(self.KEY, full_permissions(), ["muver"])
        self.assertIn("друзья", out)
        self.assertIn("просмотр", out)
        self.assertIn("👥 Вошли: `2`", out)
        self.assertIn("бессрочно", out)

    def test_panel_explains_a_revoked_key(self):
        out = key_panel_card(dict(self.KEY, revoked=1), full_permissions(), [])
        self.assertIn("отозвана", out)

    def test_panel_explains_an_expired_key(self):
        out = key_panel_card(dict(self.KEY, expires_at="2020-01-01T00:00:00"), full_permissions(), [])
        self.assertIn("Срок истёк", out)

    def test_expiry_card_states_current_value(self):
        self.assertIn("бессрочно", key_expiry_card(self.KEY))

    def test_members_card_lists_holders(self):
        out = key_members_card(self.KEY, [
            {"tg_id": 7, "tg_username": "ivan", "name": "Иван", "joined_at": "2026-07-01T10:00:00"},
        ])
        self.assertIn("Иван", out)
        self.assertIn("@ivan", out)

    def test_members_card_empty_state(self):
        self.assertIn("📭", key_members_card(self.KEY, []))

    def test_delete_card_warns_about_joined_members(self):
        out = key_delete_card(self.KEY)
        self.assertIn("навсегда", out)
        self.assertIn("`2`", out)


from pulse_desk.bot.cards import analytics_card, spark
from pulse_desk.bot.views import ANALYTICS_TABS


ANALYTICS = {
    "total_pings": 200, "new_pings": 5, "wins": 20, "giveaways": 50, "important": 8,
    "resolved": 100, "last_24h": 7, "last_7d": 40, "avg_priority": 41.5, "noise": 3,
    "total_channels": 12, "accounts_online": 2,
    "hourly": {"09": 4, "21": 12}, "daily": [{"day": "2026-07-29", "count": 7}],
}
DETAILED = {
    "channels_by_account": [{"display": "MuverGT", "status": "online", "channels": 12}],
    "chats": [{"chat": "Hot News", "count": 30, "wins": 4, "giveaways": 12, "avg_priority": 62.0}],
    "sources": [{"chat": "Hot News", "score": 7.5, "total_pings": 30, "wins": 4, "noise": 1}],
    "senders": [{"sender": "Hot News", "count": 30, "wins": 6}],
    "top_mentions": [{"username": "MuverGT", "count": 44}],
    "daily_quality": [{"day": "2026-07-29", "total": 7, "wins": 2, "giveaways": 3, "resolved": 1}],
    "priorities": [{"priority_label": "high", "count": 12}],
    "status_flow": [{"status": "new", "action_status": "claim_prize", "count": 9}],
}


class AnalyticsCardTests(unittest.TestCase):
    def _card(self, tab, **over):
        detailed = {**DETAILED, **over.pop("detailed", {})}
        analytics = {**ANALYTICS, **over.pop("analytics", {})}
        return analytics_card(tab, analytics=analytics, detailed=detailed)

    def test_every_tab_renders_with_its_breadcrumb(self):
        for code, label in ANALYTICS_TABS:
            out = self._card(code)
            self.assertIn("**АНАЛИТИКА**", out)
            self.assertIn(f"Домой › Аналитика › {label}", out)

    def test_unknown_tab_falls_back_to_overview(self):
        self.assertEqual(self._card("bogus"), self._card("sum"))

    def test_overview_shows_rates_and_coverage(self):
        out = self._card("sum")
        self.assertIn("Записей: `200`", out)
        self.assertIn("20 · 10%", out)      # wins share
        self.assertIn("50 · 25%", out)      # giveaway share
        self.assertIn("MuverGT", out)

    def test_sources_tab_ranks_chats_and_scores(self):
        out = self._card("src")
        self.assertIn("Hot News", out)
        self.assertIn("30 упом · 4 побед", out)
        self.assertIn("▰", out)

    def test_time_tab_marks_peak_hour(self):
        self.assertIn("21:00 · 12 записей", self._card("time"))

    def test_flow_tab_lists_status_transitions(self):
        self.assertIn("`new` → `claim_prize`", self._card("flow"))

    def test_empty_data_renders_placeholders_not_crash(self):
        for code, _ in ANALYTICS_TABS:
            out = analytics_card(code, analytics={}, detailed={})
            self.assertIn("АНАЛИТИКА", out)

    def test_spark_scales_to_peak(self):
        self.assertEqual(spark([0, 10]), "▁█")
        self.assertEqual(spark([0, 0]), "▁▁")
        self.assertEqual(spark([]), "")



if __name__ == "__main__":
    unittest.main()
