from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.bot.cards import (
    salary_analytics_card,
    salary_card,
    salary_off_card,
    salary_owner_card,
    salary_paid_notice,
    salary_status_label,
    salary_top_card,
)
from pulse_desk.bot.keyboards import (
    salary_accounts_keyboard,
    salary_keyboard,
    salary_member_keyboard,
    salary_top_keyboard,
)
from pulse_desk.bot.views import main_menu_buttons
from pulse_desk.salary import (
    STATUS_NONE,
    STATUS_PAID,
    STATUS_PENDING,
    Account,
    Entry,
    MonthRow,
    SalaryBook,
    account_analytics,
    owner_overview,
)

MONTHS = ["2026-07", "2026-08", "2026-09"]


def row(account: str, total: float, *, month: str = "2026-09", crypto: float = 0.0,
        skins: float = 0.0, paid: date | None = None, share: float = 0.35) -> MonthRow:
    status = STATUS_NONE if total == 0 else (STATUS_PAID if paid else STATUS_PENDING)
    return MonthRow(
        month=month, account=account, share=share, crypto=crypto, skins=skins,
        pay_money=crypto * share, pay_skins=skins * share, total=total,
        paid_at=paid, status=status,
    )


def book() -> SalaryBook:
    return SalaryBook(
        accounts=[Account("Тимон", 0.35), Account("Илья", 0.35), Account("Вова", 0.4)],
        months=[
            row("Илья", 12.369, crypto=12.71, skins=22.63),
            row("Вова", 3.7, crypto=9.25, paid=date(2026, 9, 4), share=0.4),
            row("Тимон", 0.0),
            row("Илья", 3.85, month="2026-08", crypto=11.0),
        ],
        journal=[
            Entry(date(2026, 9, 4), "Илья", "скины", "нож", 10.92, 3.82),
            Entry(date(2026, 9, 6), "Илья", "крипта", "крипту", 2.0, 0.7),
        ],
        kinds=["Крипта", "Скины", "йобо"],
        issues=["Аккаунт не из списка: `2.38$` мимо зарплат"],
    )


def labels(keyboard) -> list[str]:
    return [button.text for line in keyboard for button in line]


def payloads(keyboard) -> list[bytes]:
    return [getattr(button, "data", b"") for line in keyboard for button in line]


class StatusLabelTests(unittest.TestCase):
    def test_three_states(self):
        self.assertEqual(salary_status_label(row("Илья", 0.0)), "нет выигрышей")
        self.assertEqual(salary_status_label(row("Илья", 5.0)), "ожидает выплаты")
        self.assertEqual(
            salary_status_label(row("Вова", 3.7, paid=date(2026, 9, 4))),
            "выплачено 04.09.2026",
        )

    def test_missing_row(self):
        self.assertEqual(salary_status_label(None), "нет выигрышей")


class PersonalCardTests(unittest.TestCase):
    def test_shows_amount_status_and_rank(self):
        text = salary_card(account_analytics(book(), "Илья", "2026-09"))
        self.assertIn("12.37$", text)
        self.assertIn("ожидает выплаты", text)
        self.assertIn("1 из 3", text)
        # Крипта и скины показаны и «как выиграно», и «как к выплате».
        self.assertIn("12.71$", text)
        self.assertIn("4.45$", text)

    def test_paid_row_names_the_date(self):
        text = salary_card(account_analytics(book(), "Вова", "2026-09"))
        self.assertIn("выплачено 04.09.2026", text)

    def test_month_without_a_row(self):
        text = salary_card(account_analytics(book(), "Тимон", "2026-07"))
        self.assertIn("нет строки", text)

    def test_zero_month_is_not_an_error(self):
        text = salary_card(account_analytics(book(), "Тимон", "2026-09"))
        self.assertIn("0.00$", text)
        self.assertIn("нет выигрышей", text)


class AnalyticsCardTests(unittest.TestCase):
    def test_breakdown_and_all_time(self):
        text = salary_analytics_card(account_analytics(book(), "Илья", "2026-09"))
        self.assertIn("нож", text)
        self.assertIn("скины", text)
        self.assertIn("крипта", text)
        self.assertIn("16.22$", text)   # начислено за всё время: 12.369 + 3.85

    def test_empty_month_has_no_best_line(self):
        text = salary_analytics_card(account_analytics(book(), "Тимон", "2026-09"))
        self.assertNotIn("Лучший", text)


class TopCardTests(unittest.TestCase):
    def test_open_top_lists_everyone_with_sums(self):
        rows = sorted(
            [r for r in book().months if r.month == "2026-09"],
            key=lambda r: (-r.total, r.account.casefold()),
        )
        text = salary_top_card(rows, "2026-09", mine="Вова")
        for name in ("Илья", "Вова", "Тимон"):
            self.assertIn(name, text)
        self.assertIn("12.37$", text)
        self.assertIn("3.70$", text)
        # Своя строка помечена, чужие — нет.
        self.assertEqual(text.count("「ты」"), 1)
        own_line = next(line for line in text.splitlines() if "Вова" in line)
        self.assertIn("「ты」", own_line)

    def test_empty_month(self):
        self.assertIn("нет строк", salary_top_card([], "2026-07"))


class OwnerCardTests(unittest.TestCase):
    def test_totals_and_warnings(self):
        text = salary_owner_card(owner_overview(book(), "2026-09"))
        self.assertIn("16.07$", text)   # к выплате: 12.369 + 3.7
        self.assertIn("3.70$", text)    # уже выплачено
        self.assertIn("12.37$", text)   # ждут
        self.assertIn("2.38$", text)    # предупреждение из issues

    def test_off_card(self):
        self.assertIn("недоступна", salary_off_card())


class PaidNoticeTests(unittest.TestCase):
    def test_names_month_sum_and_date(self):
        text = salary_paid_notice(row("Вова", 3.7, paid=date(2026, 9, 4)))
        self.assertIn("сентябрь 2026", text)
        self.assertIn("3.70$", text)
        self.assertIn("04.09.2026", text)


class KeyboardTests(unittest.TestCase):
    def test_month_arrows_respect_book_bounds(self):
        first = payloads(salary_keyboard("2026-07", MONTHS))
        self.assertNotIn(b"sal:m:2026-06", first)
        self.assertIn(b"sal:m:2026-08", first)
        last = payloads(salary_keyboard("2026-09", MONTHS))
        self.assertIn(b"sal:m:2026-08", last)
        self.assertNotIn(b"sal:m:2026-10", last)

    def test_owner_gets_account_picker_instead_of_analytics(self):
        member = labels(salary_keyboard("2026-09", MONTHS))
        owner = labels(salary_keyboard("2026-09", MONTHS, is_owner=True))
        self.assertIn("📈 Аналитика", member)
        self.assertNotIn("📈 Аналитика", owner)
        self.assertIn("👥 По аккаунтам", owner)

    def test_callbacks_fit_telegram_limit(self):
        keyboards = [
            salary_keyboard("2026-09", MONTHS),
            salary_keyboard("2026-09", MONTHS, is_owner=True),
            salary_top_keyboard("2026-09", MONTHS),
            salary_accounts_keyboard("2026-09", ["Тимон", "Илья", "Вова"]),
            salary_member_keyboard("2026-09", 2),
            salary_member_keyboard("2026-09", 2, analytics=True),
        ]
        for keyboard in keyboards:
            for data in payloads(keyboard):
                self.assertLessEqual(len(data), 64, data)

    def test_accounts_keyboard_addresses_by_index(self):
        data = payloads(salary_accounts_keyboard("2026-09", ["Тимон", "Илья", "Вова"]))
        self.assertIn(b"sal:who:2026-09:2", data)

    def test_accounts_keyboard_pages(self):
        names = [f"Игрок{i}" for i in range(11)]
        first = payloads(salary_accounts_keyboard("2026-09", names, page=0))
        self.assertIn(b"sal:list:2026-09:1", first)
        second = payloads(salary_accounts_keyboard("2026-09", names, page=1))
        self.assertIn(b"sal:list:2026-09:0", second)
        self.assertIn(b"sal:who:2026-09:10", second)

    def test_member_analytics_keyboard_flips_first_button(self):
        self.assertIn("📈 Аналитика", labels(salary_member_keyboard("2026-09", 1)))
        self.assertIn("💵 Зарплата", labels(salary_member_keyboard("2026-09", 1, analytics=True)))


class MenuButtonTests(unittest.TestCase):
    def test_button_hidden_without_a_matching_label(self):
        self.assertNotIn("💵 Зарплата", labels(main_menu_buttons("viewer")))
        self.assertNotIn("💵 Зарплата", labels(main_menu_buttons("admin")))

    def test_button_shown_when_caller_resolved_an_account(self):
        self.assertIn("💵 Зарплата", labels(main_menu_buttons("viewer", salary=True)))
        self.assertIn("💵 Зарплата", labels(main_menu_buttons("admin", salary=True)))


if __name__ == "__main__":
    unittest.main()
