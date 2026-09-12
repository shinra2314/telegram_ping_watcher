"""Админские разделы: редактор настроек, правила, бэкапы, сервисы, аккаунты.

Редактор тонких настроек берёт границы из схемы, но последнее слово остаётся за
``watch_settings.sanitize_runtime_settings`` — тест на это отдельный: значение,
прошедшее форму, но не прошедшее санитайзер, не должно оказаться в базе.

Индексная адресация проверяется там, где список может измениться между
отрисовкой и кликом (сервисы, бэкапы, аккаунты): устаревший индекс обязан
приводить к «откройте заново», а не к действию над чужой строкой.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
for path in (str(ROOT), str(SRC_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from pulse_desk.bot.sections import accounts, backups, diagnostics, services  # noqa: E402
from pulse_desk.bot.sections import settings as settings_section  # noqa: E402
from pulse_desk.bot.settings_schema import RUNTIME_FIELDS  # noqa: E402


class RuntimeEditorTests(unittest.TestCase):
    VALUES = {"scan_interval_seconds": 900, "market_alert_change_pct": 5.0,
              "giveaway_review_mode": "manual", "giveaway_action_account": "muver"}

    def test_field_list_shows_the_current_value(self):
        text, rows = settings_section.fields_menu(self.VALUES, 1)
        labels = [b.text for row in rows for b in row]
        self.assertTrue(any("900 сек" in label for label in labels))

    def test_field_list_pages_instead_of_one_wall(self):
        _text, first = settings_section.fields_menu(self.VALUES, 1)
        buttons = [b for row in first for b in row if b.data.startswith(b"st_f_")]
        self.assertLessEqual(len(buttons), settings_section.FIELD_PAGE)
        self.assertTrue(len(RUNTIME_FIELDS) > settings_section.FIELD_PAGE)

    def test_field_card_offers_presets_and_manual_entry(self):
        _text, rows = settings_section.field_menu(0, self.VALUES)
        datas = [b.data for row in rows for b in row]
        self.assertTrue(any(d.startswith(b"st_v_0_") for d in datas))
        self.assertIn(b"st_i_0", datas)

    def test_preset_callbacks_are_numeric_on_both_sides(self):
        # Ключи настроек длинные и с подчёркиваниями — адресуем индексами.
        _text, rows = settings_section.field_menu(3, self.VALUES)
        for data in [b.data for row in rows for b in row if b.data.startswith(b"st_v_")]:
            tail = data.decode().removeprefix("st_v_").split("_")
            self.assertEqual(len(tail), 2, data)
            self.assertTrue(all(part.isdigit() for part in tail), data)

    def test_choice_field_lists_its_choices(self):
        index = next(i for i, f in enumerate(RUNTIME_FIELDS) if f.kind == "choice")
        _text, rows = settings_section.field_menu(index, self.VALUES)
        labels = [b.text for row in rows for b in row]
        for choice in RUNTIME_FIELDS[index].choices:
            self.assertIn(choice, labels)


class RuleParsingTests(unittest.TestCase):
    def test_silencing_a_chat(self):
        rule, error = settings_section.parse_rule("- чат @spamchan")
        self.assertEqual(error, "")
        self.assertFalse(rule["notify"])
        self.assertEqual(rule["chats"], ["spamchan"])

    def test_raising_a_keyword(self):
        rule, _error = settings_section.parse_rule("+ слово розыгрыш")
        self.assertTrue(rule["notify"])
        self.assertEqual(rule["keywords"], ["розыгрыш"])

    def test_account_rule_strips_the_at_sign(self):
        rule, _error = settings_section.parse_rule("аккаунт @muver")
        self.assertEqual(rule["usernames"], ["muver"])

    def test_missing_sign_means_notify(self):
        rule, _error = settings_section.parse_rule("чат @x")
        self.assertTrue(rule["notify"])

    def test_unknown_kind_explains_the_format(self):
        rule, error = settings_section.parse_rule("- канал @x")
        self.assertIsNone(rule)
        self.assertIn("Виды:", error)

    def test_one_word_is_refused(self):
        self.assertIsNone(settings_section.parse_rule("чат")[0])

    def test_empty_input_is_refused(self):
        self.assertIsNone(settings_section.parse_rule("   ")[0])

    def test_a_rule_only_touches_its_own_field(self):
        rule, _error = settings_section.parse_rule("- слово казино")
        self.assertEqual(rule["chats"], [])
        self.assertEqual(rule["usernames"], [])


class BackupTests(unittest.TestCase):
    ITEMS = [{"name": "a.db", "path": "/tmp/a.db", "size": 5 * 1024 * 1024, "created_at": ""}]

    def test_size_is_printed_in_megabytes(self):
        self.assertIn("5.0 МБ", backups.card(self.ITEMS))

    def test_upload_limit_is_stated_to_the_owner(self):
        self.assertIn(str(backups.MAX_UPLOAD_MB), backups.card(self.ITEMS))

    def test_every_listed_backup_has_a_download_button(self):
        datas = [b.data for row in backups.keyboard(self.ITEMS) for b in row]
        self.assertIn(b"bk:get:0", datas)
        self.assertIn(b"bk:new", datas)

    def test_empty_list_says_so(self):
        self.assertIn("Копий пока нет", backups.card([]))


class ServiceTests(unittest.TestCase):
    RUNNING = [{"name": "discord-bot", "running": True, "pid": 42, "uptime_seconds": 60,
                "restarts": 1, "panel_url": "http://127.0.0.1:8080"}]
    STOPPED = [{"name": "discord-bot", "running": False, "restarts": 0}]

    def test_running_service_can_be_stopped_restarted_and_read(self):
        datas = [b.data for row in services.keyboard(self.RUNNING) for b in row]
        self.assertIn(b"sv:stop:0", datas)
        self.assertIn(b"sv:re:0", datas)
        self.assertIn(b"sv:log:0", datas)

    def test_stopped_service_offers_start(self):
        datas = [b.data for row in services.keyboard(self.STOPPED) for b in row]
        self.assertIn(b"sv:start:0", datas)
        self.assertNotIn(b"sv:stop:0", datas)

    def test_panel_url_is_printed_because_telegram_cannot_embed_it(self):
        self.assertIn("127.0.0.1:8080", services.card(self.RUNNING))

    def test_empty_manifest_is_explained(self):
        self.assertIn("Манифест пуст", services.card([]))


class AccountTests(unittest.TestCase):
    ACCOUNTS = [
        {"session_name": "a", "status": "online", "healthy": True, "pings_total": 5, "wins": 1},
        {"session_name": "b", "status": "unauthorized", "healthy": False,
         "last_error": "auth key", "pings_total": 0, "wins": 0},
    ]

    def test_only_online_accounts_can_be_disconnected(self):
        datas = [b.data for row in accounts.keyboard(self.ACCOUNTS) for b in row]
        self.assertEqual([d for d in datas if d.startswith(b"ac:off:")], [b"ac:off:0"])

    def test_card_shows_the_error_of_a_broken_account(self):
        self.assertIn("auth key", accounts.card(self.ACCOUNTS))

    def test_card_points_at_the_console_login(self):
        # Вход в аккаунт в бота не переносится — код из Telegram-чата Telegram
        # аннулирует, и это ввод учётных данных.
        self.assertIn("auth_accounts.py", accounts.card(self.ACCOUNTS))


class DiagnosticsTests(unittest.TestCase):
    REPORT = {
        "schema_version": 22,
        "db": {"size_mb": 136.3, "backup_count": 10},
        "runtime": {"missing_background_tasks": ["auto-scan"]},
        "scan": {"background_tasks": ["digest", "auto-scan"]},
        "recent_problem_events": [{"created_at": "2026-09-12T10:00:00", "message": "boom"}],
        "recommendations": ["Проверьте логи."],
    }
    CHECKS = [{"key": "database", "label": "SQLite база доступна", "ok": True},
              {"key": "sessions", "label": "Найдены Telegram-сессии", "ok": False}]

    def test_missing_jobs_are_named(self):
        self.assertIn("auto-scan", diagnostics.report_card(self.REPORT, self.CHECKS))

    def test_failed_check_is_visible(self):
        text = diagnostics.report_card(self.REPORT, self.CHECKS)
        self.assertIn("Найдены Telegram-сессии", text)

    def test_problems_and_recommendations_are_shown(self):
        text = diagnostics.report_card(self.REPORT, self.CHECKS)
        self.assertIn("boom", text)
        self.assertIn("Проверьте логи.", text)


if __name__ == "__main__":
    unittest.main()
