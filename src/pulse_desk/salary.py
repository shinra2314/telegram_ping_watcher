"""Зарплаты парней из книги «Учет розыгрышей» (.xlsx).

Одна строка «Журнала» — один выигрыш. Владелец забирает свою долю, остаток по
ставке аккаунта — зарплата парня за месяц; лист «Выплаты по месяцам» сводит это
помесячно, а столбец «Дата выплаты» (ручной) говорит, забрал человек деньги или
ещё нет. Бот только читает книгу — единственный источник правды остаётся Excel.

Расходы месяца (прокси, подписки, комиссии) вычитаются **до** дележа: ручной
столбец «Расходы» и считаемое из него «Удержано» = расходы × доля, а «Итого» —
уже за вычетом. Арифметически это ``(выигрыш − расходы) × доля``, просто
разложенное так, чтобы столбцы по типам наград остались читаемыми, а вычет был
виден отдельной строкой, а не спрятан в проценте.

Парсер — на stdlib (``zipfile`` + ``ElementTree``): книга это zip с XML внутри, а
``openpyxl`` тянуть в зависимости ради четырёх листов незачем. Читаются
**закешированные** значения формул (``<v>``), которые Excel пересчитывает при
каждом сохранении, — считать SUMIFS заново мы не пытаемся.

Всё здесь чистое, кроме ``read_book`` (одно чтение файла), поэтому агрегаты и
разбор XML тестируются без книги на диске.
"""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

SHEET_SETTINGS = "Настройки"
SHEET_MONTHS = "Выплаты по месяцам"
SHEET_JOURNAL = "Журнал"
SHEET_SUMMARY = "Сводка"

# Лист «Настройки» жёстко задаёт свои диапазоны в формулах всей книги, так что и
# мы читаем ровно их — сдвиг строк сломал бы книгу задолго до бота.
ACCOUNT_ROWS = range(8, 14)      # A8:B13 — аккаунт и доля владельца
KIND_ROWS = range(16, 19)        # A16:A18 — типы наград
MONTH_HEADER_ROW = 5             # «Выплаты по месяцам», строка заголовков
MONTH_FIRST_ROW = 6              # «Выплаты по месяцам», данные ниже шапки
MONTH_MAX_ROW = 400
JOURNAL_ROWS = range(4, 504)     # «Журнал» A4:H503 — тот же диапазон, что в SUMIFS

# Excel считает дни от 1899-12-30 (эпоха плюс баг 1900 года, который на наших
# датах уже учтён этим сдвигом).
EXCEL_EPOCH = date(1899, 12, 30)

STATUS_NONE = "none"
STATUS_PENDING = "pending"
STATUS_PAID = "paid"

MONTH_NAMES = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]


# --------------------------------------------------------------------------
# разбор xlsx
# --------------------------------------------------------------------------
def serial_to_date(value: Any) -> Optional[date]:
    """Excel-serial → дата. Пустое, текст и мусор дают None, а не исключение."""
    try:
        days = int(float(value))
    except (TypeError, ValueError):
        return None
    if days <= 0 or days > 400000:
        return None
    return EXCEL_EPOCH + timedelta(days=days)


def shared_strings(xml: bytes) -> list[str]:
    """Таблица общих строк книги; индекс — это то, что лежит в ячейке t="s"."""
    if not xml:
        return []
    root = ET.fromstring(xml)
    return ["".join(node.text or "" for node in item.iter(NS + "t")) for item in root]


def sheet_cells(xml: bytes, strings: Optional[list[str]] = None) -> dict[str, Any]:
    """``{"B7": значение}`` для одного листа.

    Числа возвращаются как float, всё остальное — как str. Разбирать надо три
    формы: t="s" (индекс в общих строках), t="str" (строковый результат формулы —
    именно так хранится столбец «Статус») и ячейку без t (число).
    """
    strings = strings or []
    root = ET.fromstring(xml)
    data = root.find(NS + "sheetData")
    cells: dict[str, Any] = {}
    if data is None:
        return cells
    for row in data:
        for cell in row:
            ref = cell.get("r")
            if not ref:
                continue
            kind = cell.get("t")
            if kind == "inlineStr":
                node = cell.find(NS + "is")
                text = "".join(t.text or "" for t in node.iter(NS + "t")) if node is not None else ""
                if text:
                    cells[ref] = text
                continue
            node = cell.find(NS + "v")
            raw = node.text if node is not None else None
            if raw is None or raw == "":
                continue
            if kind == "s":
                try:
                    cells[ref] = strings[int(raw)]
                except (ValueError, IndexError):
                    cells[ref] = ""
            elif kind in ("str", "e"):
                cells[ref] = raw
            elif kind == "b":
                cells[ref] = raw == "1"
            else:
                try:
                    cells[ref] = float(raw)
                except ValueError:
                    cells[ref] = raw
    return cells


def sheet_targets(workbook_xml: bytes, rels_xml: bytes) -> dict[str, str]:
    """``{имя листа: путь внутри архива}``.

    По имени, а не по номеру файла: порядок ``sheetN.xml`` не обязан совпадать с
    порядком вкладок, и переименование листа в Excel его не меняет.
    """
    rels = {rel.get("Id"): (rel.get("Target") or "") for rel in ET.fromstring(rels_xml)}
    out: dict[str, str] = {}
    sheets = ET.fromstring(workbook_xml).find(NS + "sheets")
    for sheet in sheets if sheets is not None else []:
        target = rels.get(sheet.get(NS_REL + "id"), "")
        if not target:
            continue
        target = target.lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        out[sheet.get("name") or ""] = target
    return out


# --------------------------------------------------------------------------
# модель
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Account:
    name: str
    share: float          # доля владельца аккаунта, 0..1


@dataclass(frozen=True)
class MonthRow:
    month: str            # "2026-09"
    account: str
    share: float
    crypto: float         # выиграно криптой за месяц, $
    skins: float          # выиграно скинами за месяц, $
    yobo: float           # выиграно в йобо за месяц, $
    pay_money: float      # к выплате деньгами
    pay_skins: float      # к выплате скинами
    pay_yobo: float       # к выплате за йобо
    total: float          # итого к выплате — уже за вычетом удержанного
    paid_at: Optional[date]
    status: str           # STATUS_NONE / STATUS_PENDING / STATUS_PAID
    expenses: float = 0.0  # расходы месяца по этому аккаунту, $ (ручной столбец)
    withheld: float = 0.0  # сколько расходов легло на долю аккаунта, $

    @property
    def won(self) -> float:
        """Сколько выиграно за месяц всеми типами — до доли владельца."""
        return self.crypto + self.skins + self.yobo

    @property
    def net(self) -> float:
        """Сколько осталось делить после расходов месяца."""
        return self.won - self.expenses

    @property
    def gross_pay(self) -> float:
        """Доля аккаунта до удержания расходов — то, из чего вычли ``withheld``."""
        return self.pay_money + self.pay_skins + self.pay_yobo


@dataclass(frozen=True)
class Entry:
    day: date
    account: str
    kind: str
    title: str
    value: float          # стоимость приза, $
    owner_share: float    # доля владельца по этой строке, $


@dataclass
class SalaryBook:
    accounts: list[Account] = field(default_factory=list)
    months: list[MonthRow] = field(default_factory=list)
    journal: list[Entry] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return [account.name for account in self.accounts]


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def parse_workbook(data: bytes) -> SalaryBook:
    """Разобрать книгу целиком. Бросает ``ValueError`` на не-книге."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        names = set(archive.namelist())
        if "xl/workbook.xml" not in names:
            raise ValueError("не похоже на .xlsx")
        raw_strings = archive.read("xl/sharedStrings.xml") if "xl/sharedStrings.xml" in names else b""
        strings = shared_strings(raw_strings)
        targets = sheet_targets(archive.read("xl/workbook.xml"), archive.read("xl/_rels/workbook.xml.rels"))

        def load(title: str) -> dict[str, Any]:
            path = targets.get(title)
            if not path or path not in names:
                return {}
            return sheet_cells(archive.read(path), strings)

        settings = load(SHEET_SETTINGS)
        months = load(SHEET_MONTHS)
        journal = load(SHEET_JOURNAL)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"битый файл книги: {exc}") from exc

    accounts = [
        Account(name=_text(settings.get(f"A{row}")), share=_num(settings.get(f"B{row}")))
        for row in ACCOUNT_ROWS
        if _text(settings.get(f"A{row}"))
    ]
    kinds = [_text(settings.get(f"A{row}")) for row in KIND_ROWS if _text(settings.get(f"A{row}"))]

    book = SalaryBook(accounts=accounts, kinds=kinds)
    book.months = parse_month_rows(months)
    book.journal = parse_journal_rows(journal)
    book.issues = collect_issues(book)
    return book


def month_columns(cells: dict[str, Any]) -> dict[str, str]:
    """Буквы столбцов «Выплат по месяцам» — по шапке, а не по вере в порядок.

    Пара «Расходы»/«Удержано» встаёт перед «Итого»
    (``scripts/add_salary_expenses_column.ps1``), сдвигая итог, дату и статус на
    два столбца вправо. Правку применяет Excel на машине владельца, а не мы, так
    что момента «книга уже новая, процесс ещё старый» не избежать — и в этот
    момент жёсткая разметка прочитала бы «Итого» как расходы и показала бы всем
    нули. Поэтому разметка берётся из заголовка: пустая шапка (так строят
    ``cells`` тесты и старые книги) читается как старый лист.
    """
    header = _text(cells.get(f"J{MONTH_HEADER_ROW}")).casefold()
    if "расход" in header:
        return {"expenses": "J", "withheld": "K", "total": "L", "paid": "M"}
    return {"expenses": "", "withheld": "", "total": "J", "paid": "K"}


def parse_month_rows(cells: dict[str, Any]) -> list[MonthRow]:
    """Строки листа «Выплаты по месяцам» — по одной на пару (месяц, аккаунт).

    Столбцы: D крипта, E к выплате, F скины, G к выплате, **H йобо, I к выплате**,
    дальше — по ``month_columns``: либо сразу J итого / K дата (книга до расходов),
    либо **J расходы, K удержано**, L итого, M дата. Пара под йобо появилась 18.09
    (до этого тип не считался нигде, и его деньги не доходили ни до кого), пара
    под расходы — 19.09.
    """
    columns = month_columns(cells)
    expenses_col = columns["expenses"]
    withheld_col = columns["withheld"]
    total_col = columns["total"]
    paid_col = columns["paid"]
    rows: list[MonthRow] = []
    for row in range(MONTH_FIRST_ROW, MONTH_MAX_ROW):
        day = serial_to_date(cells.get(f"A{row}"))
        account = _text(cells.get(f"B{row}"))
        if day is None and not account:
            # Ниже данных идёт пустая строка, а за ней итоги — на пустой и
            # останавливаемся, чтобы не принять строку «ИТОГО» за седьмого парня.
            break
        if day is None or not account:
            continue
        crypto = _num(cells.get(f"D{row}"))
        skins = _num(cells.get(f"F{row}"))
        yobo = _num(cells.get(f"H{row}"))
        paid_at = serial_to_date(cells.get(f"{paid_col}{row}"))
        if crypto == 0 and skins == 0 and yobo == 0:
            status = STATUS_NONE
        elif paid_at is not None:
            status = STATUS_PAID
        else:
            status = STATUS_PENDING
        rows.append(MonthRow(
            month=month_key(day),
            account=account,
            share=_num(cells.get(f"C{row}")),
            crypto=crypto,
            skins=skins,
            yobo=yobo,
            pay_money=_num(cells.get(f"E{row}")),
            pay_skins=_num(cells.get(f"G{row}")),
            pay_yobo=_num(cells.get(f"I{row}")),
            total=_num(cells.get(f"{total_col}{row}")),
            paid_at=paid_at,
            status=status,
            expenses=_num(cells.get(f"{expenses_col}{row}")) if expenses_col else 0.0,
            withheld=_num(cells.get(f"{withheld_col}{row}")) if withheld_col else 0.0,
        ))
    return rows


def parse_journal_rows(cells: dict[str, Any]) -> list[Entry]:
    """Строки «Журнала» — по одной на выигрыш.

    Строка без аккаунта не пропускается, а сохраняется с пустым именем: Excel
    сам помечает такую «ОШИБКА: аккаунт не из списка», и её деньги должны
    попасть владельцу в предупреждения, а не потеряться. Ни одна выборка по
    аккаунту с пустым именем не совпадёт, так что в зарплаты она не просочится.
    """
    entries: list[Entry] = []
    for row in JOURNAL_ROWS:
        day = serial_to_date(cells.get(f"A{row}"))
        account = _text(cells.get(f"B{row}"))
        value = cells.get(f"E{row}")
        if day is None or value is None:
            continue
        entries.append(Entry(
            day=day,
            account=account,
            kind=_text(cells.get(f"C{row}")),
            title=_text(cells.get(f"D{row}")),
            value=_num(value),
            owner_share=_num(cells.get(f"F{row}")),
        ))
    return entries


def collect_issues(book: SalaryBook) -> list[str]:
    """Что в книге не сходится — показывается только владельцу.

    Главное здесь — деньги, которые никому не попали в зарплату: так видно и
    строку с опечаткой в аккаунте, и новый тип награды, который забыли внести в
    формулу помесячного листа (ровно это случилось с «йобо»).
    """
    known = {account.name.casefold() for account in book.accounts}
    kinds = {kind.casefold() for kind in book.kinds}
    issues: list[str] = []

    bad_account = sum(e.value for e in book.journal if e.account.casefold() not in known)
    if bad_account > 0.005:
        issues.append(f"Аккаунт не из списка: `{bad_account:.2f}$` мимо зарплат")
    bad_kind = sum(e.value for e in book.journal if e.kind.casefold() not in kinds)
    if bad_kind > 0.005:
        issues.append(f"Тип награды не из списка: `{bad_kind:.2f}$`")

    gap = uncounted_total(book)
    if gap > 0.005:
        issues.append(f"Мимо помесячных выплат: `{gap:.2f}$` — проверьте столбец D")

    # Расходы месяца больше выигрыша — доля уходит в минус. Книгу не подкручиваем
    # (это были бы выдуманные цифры), но владелец должен увидеть это раньше, чем
    # человек откроет карточку с отрицательной зарплатой.
    overspent = [row for row in book.months if row.total < -0.005]
    if overspent:
        worst = min(overspent, key=lambda row: row.total)
        tail = f" и ещё {len(overspent) - 1}" if len(overspent) > 1 else ""
        issues.append(
            f"Расходы съели долю: `{worst.account}` за {month_label(worst.month)} — "
            f"`{worst.total:.2f}$`{tail}"
        )
    return issues


def uncounted_total(book: SalaryBook) -> float:
    """Сколько зарплаты «Журнал» обещает, а помесячный лист не насчитал.

    Помесячные формулы складывают только перечисленные в них типы наград, так что
    новый тип (как «йобо») тихо выпадает из выплат. Сравнение с «Журналом» ловит
    это без знания о том, какие именно типы попали в формулу.

    Удержанные расходы прибавляются обратно: они не «не досчитаны», а вычтены
    сознательно, и без этого каждый месяц с расходами кричал бы, что теряет
    деньги.
    """
    shares = {account.name.casefold(): account.share for account in book.accounts}
    expected: dict[tuple[str, str], float] = {}
    for entry in book.journal:
        share = shares.get(entry.account.casefold())
        if share is None:
            continue
        key = (month_key(entry.day), entry.account.casefold())
        expected[key] = expected.get(key, 0.0) + entry.value * share
    counted: dict[tuple[str, str], float] = {}
    for row in book.months:
        key = (row.month, row.account.casefold())
        counted[key] = counted.get(key, 0.0) + row.total + row.withheld
    return sum(max(0.0, value - counted.get(key, 0.0)) for key, value in expected.items())


# --------------------------------------------------------------------------
# месяцы
# --------------------------------------------------------------------------
def month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def month_label(key: str) -> str:
    """``"2026-09"`` → ``"сентябрь 2026"``. Мусор возвращается как есть."""
    try:
        year, month = key.split("-")
        return f"{MONTH_NAMES[int(month) - 1]} {int(year)}"
    except (ValueError, IndexError):
        return key


def shift_month(key: str, delta: int) -> str:
    try:
        year, month = (int(part) for part in key.split("-"))
    except ValueError:
        return key
    index = year * 12 + (month - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def parse_month_arg(text: Any) -> Optional[str]:
    """``"09.2026"`` / ``"9.2026"`` / ``"2026-09"`` → ключ месяца; иначе None."""
    raw = _text(text).replace("/", ".").replace("-", ".")
    parts = [part for part in raw.split(".") if part]
    if len(parts) != 2:
        return None
    try:
        first, second = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    year, month = (first, second) if first > 12 else (second, first)
    if not (1 <= month <= 12) or not (2000 <= year <= 2100):
        return None
    return f"{year:04d}-{month:02d}"


def month_keys(book: SalaryBook) -> list[str]:
    """Месяцы книги по возрастанию — по ним ходят стрелки в карточке."""
    return sorted({row.month for row in book.months})


def current_month(book: SalaryBook, today: Optional[date] = None) -> str:
    """Текущий месяц, подрезанный к диапазону книги."""
    keys = month_keys(book)
    key = month_key(today or date.today())
    if not keys:
        return key
    return min(max(key, keys[0]), keys[-1])


# --------------------------------------------------------------------------
# выборки
# --------------------------------------------------------------------------
def match_account(label: Any, accounts: list[str]) -> Optional[str]:
    """Метка ключа → имя аккаунта. Регистр и лишние пробелы не важны."""
    name = _text(label).casefold()
    if not name:
        return None
    for account in accounts:
        if account.casefold() == name:
            return account
    return None


def salary_account_for(member: Optional[dict], book: Optional[SalaryBook]) -> Optional[str]:
    """Аккаунт, зарплату которого видит этот участник бота.

    Метки нет, совпадения нет или книга не загружена — раздела зарплат для
    человека не существует вовсе.
    """
    if not member or not book:
        return None
    return match_account(member.get("key_label"), book.names)


def salary_for(book: SalaryBook, account: str, month: str) -> Optional[MonthRow]:
    for row in book.months:
        if row.month == month and row.account.casefold() == account.casefold():
            return row
    return None


def top_for_month(book: SalaryBook, month: str) -> list[MonthRow]:
    """Все аккаунты за месяц: сначала большие суммы, при равенстве — по имени."""
    rows = [row for row in book.months if row.month == month]
    return sorted(rows, key=lambda row: (-row.total, row.account.casefold()))


def rank_of(rows: list[MonthRow], account: str) -> int:
    """Место аккаунта в уже отсортированном топе; 0 — если его там нет."""
    for index, row in enumerate(rows, start=1):
        if row.account.casefold() == account.casefold():
            return index
    return 0


def entries_for(book: SalaryBook, account: str, month: str) -> list[Entry]:
    return [
        entry for entry in book.journal
        if entry.account.casefold() == account.casefold() and month_key(entry.day) == month
    ]


def account_analytics(book: SalaryBook, account: str, month: str) -> dict:
    """Всё, что карточка показывает одному человеку за один месяц."""
    row = salary_for(book, account, month)
    rows = top_for_month(book, month)
    entries = sorted(entries_for(book, account, month), key=lambda entry: -entry.value)
    prev = salary_for(book, account, shift_month(month, -1))
    mine = [r for r in book.months if r.account.casefold() == account.casefold()]

    by_kind: dict[str, float] = {}
    for entry in entries:
        by_kind[entry.kind] = by_kind.get(entry.kind, 0.0) + entry.value

    total = row.total if row else 0.0
    prev_total = prev.total if prev else 0.0
    won = sum(entry.value for entry in entries)
    return {
        "account": account,
        "month": month,
        "row": row,
        "rank": rank_of(rows, account),
        "of": len(rows),
        "wins": len(entries),
        "won": won,
        "best": entries[0] if entries else None,
        "avg": (won / len(entries)) if entries else 0.0,
        "by_kind": by_kind,
        "expenses": row.expenses if row else 0.0,
        "withheld": row.withheld if row else 0.0,
        "gross_pay": row.gross_pay if row else 0.0,
        "prev_total": prev_total,
        "delta": total - prev_total,
        "all_time": sum(r.total for r in mine),
        "all_time_withheld": sum(r.withheld for r in mine),
        "all_time_paid": sum(r.total for r in mine if r.status == STATUS_PAID),
        "all_time_pending": sum(r.total for r in mine if r.status == STATUS_PENDING),
        "wins_all_time": sum(1 for e in book.journal if e.account.casefold() == account.casefold()),
    }


def owner_overview(book: SalaryBook, month: str) -> dict:
    """Сводка месяца для владельца: кому сколько и что ещё не выплачено.

    ``profit`` — то, что остаётся организатору **после** расходов месяца: доля
    вычитается из уже уменьшенного банка, а сами расходы организатор платит из
    своего остатка, поэтому они вычитаются здесь ещё раз, а не «уже учтены».
    """
    rows = top_for_month(book, month)
    entries = [entry for entry in book.journal if month_key(entry.day) == month]
    payout = sum(row.total for row in rows)
    won = sum(entry.value for entry in entries)
    expenses = sum(row.expenses for row in rows)
    return {
        "month": month,
        "rows": rows,
        "payout": payout,
        "paid": sum(row.total for row in rows if row.status == STATUS_PAID),
        "pending": sum(row.total for row in rows if row.status == STATUS_PENDING),
        "pending_names": [row.account for row in rows if row.status == STATUS_PENDING],
        "won": won,
        "expenses": expenses,
        "withheld": sum(row.withheld for row in rows),
        "profit": won - payout - expenses,
        "wins": len(entries),
        "issues": list(book.issues),
    }


def paid_pairs(book: SalaryBook) -> list[str]:
    """``"<месяц>|<аккаунт>"`` по каждой выплаченной строке.

    Ключи стабильны между перечитываниями книги, поэтому по разнице с прошлым
    снимком видно, какая зарплата только что стала выплаченной.
    """
    return sorted(
        f"{row.month}|{row.account.casefold()}"
        for row in book.months
        if row.status == STATUS_PAID
    )


# --------------------------------------------------------------------------
# чтение файла
# --------------------------------------------------------------------------
def file_signature(path: Path) -> Optional[tuple[float, int]]:
    """(mtime, размер) — по ним и понятно, что книгу пересохранили."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime, stat.st_size)


def read_book(path: Path) -> SalaryBook:
    """Прочитать и разобрать книгу. Синхронно — вызывать через ``asyncio.to_thread``.

    Excel держит открытую книгу в режиме share-read, так что чтение проходит и
    при открытом файле; исключения пробрасываются наверх, где очередной тик
    синхронизации просто пропускается.
    """
    return parse_workbook(path.read_bytes())
