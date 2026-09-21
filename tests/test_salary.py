from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.salary import (
    STATUS_NONE,
    STATUS_PAID,
    STATUS_PENDING,
    Account,
    Entry,
    MonthRow,
    SalaryBook,
    account_analytics,
    collect_issues,
    current_month,
    match_account,
    month_columns,
    month_key,
    month_keys,
    month_label,
    owner_overview,
    paid_pairs,
    parse_journal_rows,
    parse_month_arg,
    parse_month_rows,
    rank_of,
    salary_account_for,
    salary_for,
    serial_to_date,
    shared_strings,
    sheet_cells,
    sheet_targets,
    shift_month,
    top_for_month,
    uncounted_total,
)

MAIN_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
REL_NS = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def sheet_xml(rows: str) -> bytes:
    return f'<worksheet {MAIN_NS}><sheetData>{rows}</sheetData></worksheet>'.encode()


def month_row(row: int, serial: int, account: str, share: float, crypto: float,
              skins: float, total: float, paid: str = "", yobo: float = 0.0) -> str:
    """Одна строка листа «Выплаты по месяцам» в том же виде, что пишет Excel.

    Столбцы H/I — йобо и его выплата, поэтому итог живёт в J, а дата выплаты в K.
    """
    paid_cell = f'<c r="K{row}"><v>{paid}</v></c>' if paid else ""
    return (
        f'<row r="{row}">'
        f'<c r="A{row}"><v>{serial}</v></c>'
        f'<c r="B{row}" t="s"><v>{account}</v></c>'
        f'<c r="C{row}"><v>{share}</v></c>'
        f'<c r="D{row}"><v>{crypto}</v></c>'
        f'<c r="E{row}"><v>{crypto * share}</v></c>'
        f'<c r="F{row}"><v>{skins}</v></c>'
        f'<c r="G{row}"><v>{skins * share}</v></c>'
        f'<c r="H{row}"><v>{yobo}</v></c>'
        f'<c r="I{row}"><v>{yobo * share}</v></c>'
        f'<c r="J{row}"><v>{total}</v></c>'
        f"{paid_cell}"
        f'</row>'
    )


def expenses_header(text: str = "Расходы\nза месяц, $") -> str:
    """Шапка J5 — по ней парсер и узнаёт книгу с расходами."""
    return f'<row r="5"><c r="J5" t="inlineStr"><is><t>{text}</t></is></c></row>'


def month_row_exp(row: int, serial: int, account: str, share: float, crypto: float,
                  expenses: float, paid: str = "") -> str:
    """Строка книги с расходами: J расходы, K удержано, L итого, M дата."""
    withheld = expenses * share
    total = crypto * share - withheld
    paid_cell = f'<c r="M{row}"><v>{paid}</v></c>' if paid else ""
    return (
        f'<row r="{row}">'
        f'<c r="A{row}"><v>{serial}</v></c>'
        f'<c r="B{row}" t="s"><v>{account}</v></c>'
        f'<c r="C{row}"><v>{share}</v></c>'
        f'<c r="D{row}"><v>{crypto}</v></c>'
        f'<c r="E{row}"><v>{crypto * share}</v></c>'
        f'<c r="F{row}"><v>0</v></c>'
        f'<c r="G{row}"><v>0</v></c>'
        f'<c r="H{row}"><v>0</v></c>'
        f'<c r="I{row}"><v>0</v></c>'
        f'<c r="J{row}"><v>{expenses}</v></c>'
        f'<c r="K{row}"><v>{withheld}</v></c>'
        f'<c r="L{row}"><v>{total}</v></c>'
        f"{paid_cell}"
        f'</row>'
    )


def book(months: list[MonthRow] = (), journal: list[Entry] = ()) -> SalaryBook:
    return SalaryBook(
        accounts=[Account("Тимон", 0.35), Account("Илья", 0.35), Account("Вова", 0.4)],
        months=list(months),
        journal=list(journal),
        kinds=["Крипта", "Скины", "йобо"],
    )


def row(month: str, account: str, total: float, *, crypto: float = 0.0, skins: float = 0.0,
        yobo: float = 0.0, paid: date | None = None, share: float = 0.35,
        expenses: float = 0.0) -> MonthRow:
    """Строка месяца. ``total`` — итог **после** удержания, как в книге."""
    if crypto == 0 and skins == 0 and yobo == 0:
        crypto = (total + expenses * share) / share if total else 0.0
    if total == 0 and expenses == 0:
        status = STATUS_NONE
    else:
        status = STATUS_PAID if paid else STATUS_PENDING
    return MonthRow(
        month=month, account=account, share=share, crypto=crypto, skins=skins, yobo=yobo,
        pay_money=crypto * share, pay_skins=skins * share, pay_yobo=yobo * share,
        total=total, paid_at=paid, status=status,
        expenses=expenses, withheld=expenses * share,
    )


class SerialDateTests(unittest.TestCase):
    def test_known_serial(self):
        # 46204 — первый месяц учёта в книге, 01.07.2026.
        self.assertEqual(serial_to_date(46204), date(2026, 7, 1))

    def test_junk_is_none(self):
        for value in (None, "", "ок", 0, -5, 999999999):
            self.assertIsNone(serial_to_date(value))

    def test_float_serial_truncates_time(self):
        self.assertEqual(serial_to_date(46204.75), date(2026, 7, 1))


class SheetParsingTests(unittest.TestCase):
    def test_shared_string_cell(self):
        cells = sheet_cells(sheet_xml('<row r="1"><c r="A1" t="s"><v>1</v></c></row>'), ["нет", "Илья"])
        self.assertEqual(cells["A1"], "Илья")

    def test_formula_string_cell(self):
        # Столбец «Статус» — строковый результат формулы: значение лежит прямо
        # в <v>, а не в таблице общих строк.
        cells = sheet_cells(sheet_xml('<row r="1"><c r="J1" t="str"><v>выплачено</v></c></row>'), [])
        self.assertEqual(cells["J1"], "выплачено")

    def test_number_and_empty_cells(self):
        xml = sheet_xml('<row r="1"><c r="A1"><v>12.5</v></c><c r="B1"><v></v></c><c r="C1"/></row>')
        cells = sheet_cells(xml, [])
        self.assertEqual(cells["A1"], 12.5)
        self.assertNotIn("B1", cells)
        self.assertNotIn("C1", cells)

    def test_inline_string_cell(self):
        xml = sheet_xml('<row r="1"><c r="A1" t="inlineStr"><is><t>Вова</t></is></c></row>')
        self.assertEqual(sheet_cells(xml, [])["A1"], "Вова")

    def test_shared_strings_join_runs(self):
        xml = (
            f'<sst {MAIN_NS}><si><t>Крипта</t></si>'
            '<si><r><t>Скины</t></r><r><t> за месяц</t></r></si></sst>'
        ).encode()
        self.assertEqual(shared_strings(xml), ["Крипта", "Скины за месяц"])

    def test_sheet_targets_by_name(self):
        workbook = (
            f'<workbook {MAIN_NS} {REL_NS}><sheets>'
            '<sheet name="Журнал" sheetId="1" r:id="rId3"/>'
            '<sheet name="Сводка" sheetId="2" r:id="rId1"/>'
            '</sheets></workbook>'
        ).encode()
        rels = (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId3" Target="worksheets/sheet9.xml"/>'
            '<Relationship Id="rId1" Target="/xl/worksheets/sheet2.xml"/>'
            '</Relationships>'
        ).encode()
        targets = sheet_targets(workbook, rels)
        # Имя листа, а не порядок файлов: sheet9.xml — это первая вкладка.
        self.assertEqual(targets["Журнал"], "xl/worksheets/sheet9.xml")
        self.assertEqual(targets["Сводка"], "xl/worksheets/sheet2.xml")


class MonthRowParsingTests(unittest.TestCase):
    def rows(self, xml_rows: str) -> list[MonthRow]:
        return parse_month_rows(sheet_cells(sheet_xml(xml_rows), ["Тимон", "Илья"]))

    def test_status_from_payout_date(self):
        rows = self.rows(
            month_row(6, 46204, "0", 0.35, 10.0, 0.0, 3.5, paid="46244")
            + month_row(7, 46204, "1", 0.35, 4.0, 0.0, 1.4)
        )
        self.assertEqual([r.status for r in rows], [STATUS_PAID, STATUS_PENDING])
        self.assertEqual(rows[0].paid_at, date(2026, 8, 10))
        self.assertIsNone(rows[1].paid_at)

    def test_empty_month_is_none_status(self):
        rows = self.rows(month_row(6, 46204, "0", 0.35, 0.0, 0.0, 0.0))
        self.assertEqual(rows[0].status, STATUS_NONE)

    def test_stops_before_totals_row(self):
        # Под данными идёт пустая строка, потом «ИТОГО» без аккаунта — она не
        # должна стать седьмым парнем.
        rows = self.rows(
            month_row(6, 46204, "0", 0.35, 10.0, 0.0, 3.5)
            + '<row r="8"><c r="H8"><v>36.25</v></c></row>'
        )
        self.assertEqual(len(rows), 1)

    def test_month_key_from_serial(self):
        rows = self.rows(month_row(6, 46235, "0", 0.35, 1.0, 0.0, 0.35))
        self.assertEqual(rows[0].month, "2026-08")

    def test_yobo_is_its_own_column(self):
        # H/I — йобо и его выплата, J — итог, K — дата: месяц, где выиграли
        # только в йобо, обязан считаться месяцем с деньгами, а не пустым.
        rows = self.rows(month_row(6, 46204, "0", 0.35, 0.0, 0.0, 0.175, yobo=0.5))
        self.assertEqual(rows[0].yobo, 0.5)
        self.assertAlmostEqual(rows[0].pay_yobo, 0.175)
        self.assertEqual(rows[0].total, 0.175)
        self.assertEqual(rows[0].status, STATUS_PENDING)
        self.assertAlmostEqual(rows[0].won, 0.5)


class JournalParsingTests(unittest.TestCase):
    def test_row_without_account_is_kept_for_the_warning(self):
        # Excel сам помечает такую строку «ОШИБКА: аккаунт не из списка»; её
        # деньги должны быть видны владельцу, а не исчезнуть.
        xml = sheet_xml(
            '<row r="4"><c r="A4"><v>46235</v></c><c r="C4" t="s"><v>0</v></c>'
            '<c r="E4"><v>1.88</v></c></row>'
        )
        entries = parse_journal_rows(sheet_cells(xml, ["крипта"]))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].account, "")
        self.assertEqual(entries[0].value, 1.88)

    def test_row_without_value_is_skipped(self):
        xml = sheet_xml('<row r="4"><c r="A4"><v>46235</v></c><c r="B4" t="s"><v>0</v></c></row>')
        self.assertEqual(parse_journal_rows(sheet_cells(xml, ["Илья"])), [])


class MonthArithmeticTests(unittest.TestCase):
    def test_month_key_and_label(self):
        self.assertEqual(month_key(date(2026, 9, 4)), "2026-09")
        self.assertEqual(month_label("2026-09"), "сентябрь 2026")
        self.assertEqual(month_label("мусор"), "мусор")

    def test_shift_across_year(self):
        self.assertEqual(shift_month("2026-12", 1), "2027-01")
        self.assertEqual(shift_month("2026-01", -1), "2025-12")

    def test_current_month_clamped_to_book(self):
        data = book([row("2026-07", "Илья", 1.0), row("2026-08", "Илья", 1.0)])
        self.assertEqual(current_month(data, date(2026, 8, 20)), "2026-08")
        # Месяц позже последнего в книге подрезается, а не даёт пустой экран.
        self.assertEqual(current_month(data, date(2027, 3, 1)), "2026-08")
        self.assertEqual(current_month(data, date(2026, 1, 1)), "2026-07")

    def test_month_keys_sorted_and_unique(self):
        data = book([row("2026-08", "Илья", 1.0), row("2026-07", "Вова", 2.0), row("2026-08", "Вова", 1.0)])
        self.assertEqual(month_keys(data), ["2026-07", "2026-08"])

    def test_parse_month_arg(self):
        self.assertEqual(parse_month_arg("09.2026"), "2026-09")
        self.assertEqual(parse_month_arg("9.2026"), "2026-09")
        self.assertEqual(parse_month_arg("2026-09"), "2026-09")
        for junk in ("", "сентябрь", "13.2026", "09.1800", "1.2.3"):
            self.assertIsNone(parse_month_arg(junk))


class LookupTests(unittest.TestCase):
    def test_match_account_ignores_case_and_spaces(self):
        names = ["Тимон", "Илья", "Вова"]
        self.assertEqual(match_account("  илья ", names), "Илья")
        self.assertEqual(match_account("ВОВА", names), "Вова")
        self.assertIsNone(match_account("Петя", names))
        self.assertIsNone(match_account("", names))
        self.assertIsNone(match_account(None, names))

    def test_salary_account_for_member(self):
        data = book()
        self.assertEqual(salary_account_for({"key_label": "вова"}, data), "Вова")
        # Ключ без метки и удалённый ключ (label = NULL) — раздела нет.
        self.assertIsNone(salary_account_for({"key_label": ""}, data))
        self.assertIsNone(salary_account_for({"key_label": None}, data))
        self.assertIsNone(salary_account_for({}, data))
        self.assertIsNone(salary_account_for(None, data))
        # Без книги раздел тоже не существует.
        self.assertIsNone(salary_account_for({"key_label": "Вова"}, None))

    def test_salary_for_is_case_insensitive(self):
        data = book([row("2026-09", "Илья", 5.0)])
        self.assertIsNotNone(salary_for(data, "илья", "2026-09"))
        self.assertIsNone(salary_for(data, "Илья", "2026-08"))


class TopTests(unittest.TestCase):
    def test_sorted_by_total_then_name(self):
        data = book([
            row("2026-09", "Вова", 3.7),
            row("2026-09", "Илья", 12.37),
            row("2026-09", "Тимон", 3.7),
        ])
        top = top_for_month(data, "2026-09")
        self.assertEqual([r.account for r in top], ["Илья", "Вова", "Тимон"])

    def test_rank_of(self):
        data = book([row("2026-09", "Вова", 3.7), row("2026-09", "Илья", 12.37)])
        top = top_for_month(data, "2026-09")
        self.assertEqual(rank_of(top, "илья"), 1)
        self.assertEqual(rank_of(top, "Вова"), 2)
        self.assertEqual(rank_of(top, "Петя"), 0)

    def test_other_months_excluded(self):
        data = book([row("2026-08", "Илья", 3.85), row("2026-09", "Илья", 12.37)])
        self.assertEqual(len(top_for_month(data, "2026-09")), 1)


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.data = book(
            months=[
                row("2026-08", "Илья", 3.85),
                row("2026-09", "Илья", 12.369, crypto=12.71, skins=22.63, paid=None),
                row("2026-09", "Вова", 3.7, paid=date(2026, 9, 4)),
            ],
            journal=[
                Entry(date(2026, 9, 4), "ИЛЬЯ", "скины", "скин", 10.92, 3.82),
                Entry(date(2026, 9, 6), "Илья", "крипта", "крипту", 2.0, 0.7),
                Entry(date(2026, 8, 2), "Илья", "крипта", "крипту", 11.0, 3.85),
            ],
        )

    def test_month_numbers(self):
        data = account_analytics(self.data, "Илья", "2026-09")
        self.assertEqual(data["rank"], 1)
        self.assertEqual(data["of"], 2)
        self.assertEqual(data["wins"], 2)
        self.assertAlmostEqual(data["won"], 12.92)
        self.assertAlmostEqual(data["avg"], 6.46)
        self.assertEqual(data["best"].value, 10.92)
        self.assertEqual(data["by_kind"], {"скины": 10.92, "крипта": 2.0})

    def test_delta_against_previous_month(self):
        data = account_analytics(self.data, "Илья", "2026-09")
        self.assertAlmostEqual(data["prev_total"], 3.85)
        self.assertAlmostEqual(data["delta"], 12.369 - 3.85)

    def test_all_time_split_by_status(self):
        data = account_analytics(self.data, "Илья", "2026-09")
        self.assertAlmostEqual(data["all_time"], 3.85 + 12.369)
        self.assertAlmostEqual(data["all_time_pending"], 3.85 + 12.369)
        self.assertEqual(data["all_time_paid"], 0)
        self.assertEqual(data["wins_all_time"], 3)

    def test_history_is_month_by_month_newest_first(self):
        data = account_analytics(self.data, "Илья", "2026-09")
        self.assertEqual([h["month"] for h in data["history"]], ["2026-09", "2026-08"])
        self.assertAlmostEqual(sum(h["total"] for h in data["history"]), data["all_time"])
        # Only this person's rows: Вова's paid September is not in Илья's history.
        self.assertTrue(all(h["paid_at"] is None for h in data["history"]))

    def test_empty_month_is_safe(self):
        data = account_analytics(self.data, "Тимон", "2026-09")
        self.assertIsNone(data["row"])
        self.assertIsNone(data["best"])
        self.assertEqual(data["wins"], 0)
        self.assertEqual(data["avg"], 0.0)

    def test_owner_overview(self):
        data = owner_overview(self.data, "2026-09")
        self.assertAlmostEqual(data["payout"], 12.369 + 3.7)
        self.assertAlmostEqual(data["paid"], 3.7)
        self.assertAlmostEqual(data["pending"], 12.369)
        self.assertEqual(data["pending_names"], ["Илья"])
        self.assertAlmostEqual(data["won"], 12.92)
        self.assertEqual(data["wins"], 2)


class PaidPairTests(unittest.TestCase):
    def test_only_paid_rows_and_stable_keys(self):
        data = book([
            row("2026-09", "Вова", 3.7, paid=date(2026, 9, 4)),
            row("2026-09", "Илья", 12.37),
            row("2026-08", "ИЛЬЯ", 3.85, paid=date(2026, 8, 20)),
        ])
        self.assertEqual(paid_pairs(data), ["2026-08|илья", "2026-09|вова"])


class IssueTests(unittest.TestCase):
    def test_row_without_account_is_reported(self):
        data = book(journal=[Entry(date(2026, 8, 2), "", "крипта", "", 1.88, 0.0)])
        self.assertTrue(any("1.88" in issue for issue in collect_issues(data)))

    def test_unknown_kind_is_reported(self):
        data = book(journal=[Entry(date(2026, 8, 2), "Илья", "нфт", "", 5.0, 1.75)])
        self.assertTrue(any("5.00" in issue for issue in collect_issues(data)))

    def test_kind_missing_from_monthly_formula_is_caught(self):
        # «йобо» есть в списке типов, но формула помесячного листа его не
        # складывает — зарплата по «Журналу» больше, чем насчитала книга.
        data = book(
            months=[row("2026-09", "Илья", 0.0)],
            journal=[Entry(date(2026, 9, 3), "Илья", "йобо", "приз", 10.0, 3.5)],
        )
        self.assertAlmostEqual(uncounted_total(data), 3.5)
        self.assertTrue(any("3.50" in issue for issue in collect_issues(data)))

    def test_matching_book_has_no_issues(self):
        data = book(
            months=[row("2026-09", "Илья", 3.5, crypto=10.0)],
            journal=[Entry(date(2026, 9, 3), "Илья", "Крипта", "приз", 10.0, 3.5)],
        )
        self.assertEqual(collect_issues(data), [])


class MonthLayoutTests(unittest.TestCase):
    """Разметка берётся из шапки, потому что книгу пересохраняет владелец.

    Между его сохранением и перезапуском приложения живёт процесс со старым
    кодом; жёсткие буквы столбцов прочитали бы в этот момент «Итого» как расходы.
    """

    def test_book_without_the_column_reads_the_old_way(self):
        self.assertEqual(month_columns({}), {"expenses": "", "withheld": "", "total": "J", "paid": "K"})

    def test_header_switches_the_layout(self):
        cells = {"J5": "Расходы\nза месяц, $"}
        self.assertEqual(month_columns(cells),
                         {"expenses": "J", "withheld": "K", "total": "L", "paid": "M"})

    def test_old_header_is_not_mistaken_for_the_new_one(self):
        self.assertEqual(month_columns({"J5": "Итого\nк выплате, $"})["total"], "J")


class ExpenseParsingTests(unittest.TestCase):
    def rows(self, xml_rows: str) -> list[MonthRow]:
        return parse_month_rows(sheet_cells(sheet_xml(xml_rows), ["Тимон", "Вова"]))

    def test_columns_shift_by_two(self):
        rows = self.rows(expenses_header() + month_row_exp(6, 46327, "1", 0.4, 25.0, 5.0, paid="46344"))
        self.assertAlmostEqual(rows[0].expenses, 5.0)
        self.assertAlmostEqual(rows[0].withheld, 2.0)
        self.assertAlmostEqual(rows[0].total, 8.0)
        self.assertEqual(rows[0].paid_at, date(2026, 11, 18))
        self.assertEqual(rows[0].status, STATUS_PAID)

    def test_deduction_happens_before_the_share(self):
        # Ровно то, ради чего столбец и появился: доля берётся с остатка, а не
        # спрятана в проценте. 40% от (25 − 5) == 25*0.4 − 5*0.4.
        rows = self.rows(expenses_header() + month_row_exp(6, 46327, "1", 0.4, 25.0, 5.0))
        got = rows[0]
        self.assertAlmostEqual(got.total, got.net * got.share)
        self.assertAlmostEqual(got.total, got.gross_pay - got.withheld)
        self.assertAlmostEqual(got.net, 20.0)

    def test_old_book_has_no_expenses(self):
        rows = parse_month_rows(sheet_cells(
            sheet_xml(month_row(6, 46204, "0", 0.35, 10.0, 0.0, 3.5)), ["Тимон"]))
        self.assertEqual(rows[0].expenses, 0.0)
        self.assertEqual(rows[0].withheld, 0.0)
        self.assertAlmostEqual(rows[0].total, 3.5)

    def test_empty_expense_cell_is_zero(self):
        # Столбец ручной: у владельца он пуст во всех месяцах, где расходов не
        # было, и такой месяц обязан считаться ровно как раньше.
        rows = self.rows(expenses_header() + month_row_exp(6, 46204, "0", 0.35, 10.0, 0.0))
        self.assertEqual(rows[0].expenses, 0.0)
        self.assertAlmostEqual(rows[0].total, 3.5)


class ExpenseAggregateTests(unittest.TestCase):
    def data(self) -> SalaryBook:
        return book(
            months=[row("2026-11", "Вова", 8.0, crypto=25.0, share=0.4, expenses=5.0)],
            journal=[Entry(date(2026, 11, 3), "Вова", "Крипта", "приз", 25.0, 10.0)],
        )

    def test_withheld_is_not_lost_money(self):
        # Без возврата удержанного сверка решила бы, что книга потеряла 2$, и
        # владелец получал бы предупреждение каждый месяц с расходами.
        self.assertAlmostEqual(uncounted_total(self.data()), 0.0)
        self.assertEqual(collect_issues(self.data()), [])

    def test_owner_overview_counts_expenses_once_more(self):
        data = owner_overview(self.data(), "2026-11")
        self.assertAlmostEqual(data["expenses"], 5.0)
        self.assertAlmostEqual(data["withheld"], 2.0)
        self.assertAlmostEqual(data["payout"], 8.0)
        # 25 выиграно − 8 отдано − 5 потрачено.
        self.assertAlmostEqual(data["profit"], 12.0)

    def test_account_analytics_carries_the_deduction(self):
        data = account_analytics(self.data(), "Вова", "2026-11")
        self.assertAlmostEqual(data["expenses"], 5.0)
        self.assertAlmostEqual(data["withheld"], 2.0)
        self.assertAlmostEqual(data["gross_pay"], 10.0)
        self.assertAlmostEqual(data["all_time_withheld"], 2.0)

    def test_expenses_bigger_than_the_prize_are_reported(self):
        # Книгу не подкручиваем — минус остаётся минусом, но владелец узнаёт об
        # этом раньше, чем человек откроет карточку с отрицательной зарплатой.
        data = book(months=[row("2026-11", "Вова", -1.2, crypto=2.0, share=0.4, expenses=5.0)])
        self.assertTrue(any("Расходы съели долю" in issue for issue in collect_issues(data)))


if __name__ == "__main__":
    unittest.main()
