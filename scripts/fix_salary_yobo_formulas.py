"""One-off: make the salary workbook count the «йобо» reward type.

The workbook already knows three reward types (`Настройки!A16:A18` — Крипта,
Скины, йобо), and the journal validates against all three. But the two sheets
that turn the journal into money — «Выплаты по месяцам» and «Сводка» — sum only
`$A$16` and `$A$17`. A win logged as «йобо» therefore lands in nobody's salary:
the row looks fine ("ок" in column H), the money simply never reaches column D.

This script adds the missing `+SUMIFS(… Настройки!$A$18 …)` term to every
affected formula in column D, renames those two headers, and asks Excel to
recalculate the whole book on open (the cached `<v>` values in the file are the
old ones, and Pulse Desk reads exactly those cached values).

Run it with the workbook CLOSED — Excel keeps its own copy in memory and would
overwrite this edit on its next save. Usage:

    python scripts/fix_salary_yobo_formulas.py            # uses SALARY_XLSX_PATH
    python scripts/fix_salary_yobo_formulas.py <path.xlsx>
    python scripts/fix_salary_yobo_formulas.py --dry-run
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.salary import sheet_targets  # noqa: E402

CRYPTO_REF = "Настройки!$A$16"
YOBO_REF = "Настройки!$A$18"
# Заголовки столбца D на обоих листах: что суммируем, то и подписано.
HEADERS = {
    "Выплаты по месяцам": ("D5", "Крипта\nза месяц, $", "Крипта+йобо\nза месяц, $"),
    "Сводка": ("D4", "Крипта\nвсего, $", "Крипта+йобо\nвсего, $"),
}
CELL_RE = re.compile(r"<c\s+r=\"D\d+\"[^>]*?(?:/>|>.*?</c>)", re.DOTALL)
FORMULA_RE = re.compile(r"(<f[^>]*>)(.*?)(</f>)", re.DOTALL)


def patch_formulas(xml: str) -> tuple[str, int]:
    """Добавить слагаемое «йобо» в каждую формулу столбца D.

    Правка идёт по сырому тексту, а не через ElementTree: формула хранится уже
    экранированной (`&amp;`, `&quot;`), и копия с заменённой ссылкой на тип
    сохраняет экранирование сама собой, а остальной XML остаётся байт в байт
    таким, каким его записал Excel.
    """
    patched = [0]

    def fix_formula(inner: re.Match[str]) -> str:
        body = inner.group(2)
        if CRYPTO_REF not in body or YOBO_REF in body:
            return inner.group(0)
        patched[0] += 1
        return inner.group(1) + body + "+" + body.replace(CRYPTO_REF, YOBO_REF) + inner.group(3)

    def fix_cell(match: re.Match[str]) -> str:
        return FORMULA_RE.sub(fix_formula, match.group(0))

    return CELL_RE.sub(fix_cell, xml), patched[0]


def add_shared_string(xml: str, value: str) -> tuple[str, int]:
    """Дописать строку в sharedStrings и вернуть её индекс.

    Именно дописать: существующая запись «Крипта…» может стоять и в других
    ячейках, и правка на месте переименовала бы их заодно.
    """
    index = xml.count("<si>")
    escaped = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # xml:space="preserve" — в заголовке есть перевод строки, иначе Excel его срежет.
    entry = f'<si><t xml:space="preserve">{escaped}</t></si>'
    xml = xml.replace("</sst>", entry + "</sst>", 1)
    xml = re.sub(r'(<sst[^>]*?)\scount="\d+"', lambda m: m.group(1) + f' count="{index + 1}"', xml, count=1)
    xml = re.sub(r'(<sst[^>]*?)\suniqueCount="\d+"', lambda m: m.group(1) + f' uniqueCount="{index + 1}"', xml, count=1)
    return xml, index


def retitle(xml: str, ref: str, index: int) -> tuple[str, bool]:
    """Перевести ячейку заголовка на новую общую строку."""
    pattern = re.compile(rf"<c\s+r=\"{ref}\"[^>]*?(?:/>|>.*?</c>)", re.DOTALL)
    match = pattern.search(xml)
    if not match:
        return xml, False
    cell = match.group(0)
    if ' t="s"' not in cell:
        return xml, False
    replaced = re.sub(r"<v>\d+</v>", f"<v>{index}</v>", cell, count=1)
    return xml[:match.start()] + replaced + xml[match.end():], replaced != cell


def force_full_recalc(xml: str) -> str:
    """`fullCalcOnLoad` — иначе Excel покажет старый кеш вместо новых сумм."""
    if "fullCalcOnLoad" in xml:
        return xml
    if "<calcPr" in xml:
        return re.sub(r"<calcPr([^>]*?)/>", r'<calcPr\1 fullCalcOnLoad="1"/>', xml, count=1)
    return xml.replace("</workbook>", '<calcPr fullCalcOnLoad="1"/></workbook>', 1)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    raw = args[0] if args else os.environ.get("SALARY_XLSX_PATH", "")
    if not raw:
        env = Path(__file__).resolve().parents[1] / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("SALARY_XLSX_PATH="):
                    raw = line.split("=", 1)[1].strip()
    if not raw:
        print("Не задан путь к книге: аргументом или SALARY_XLSX_PATH")
        return 2
    path = Path(raw)
    if not path.exists():
        print(f"Файл не найден: {path}")
        return 2
    lock = path.with_name("~$" + path.name)
    if lock.exists():
        print(f"Книга открыта в Excel ({lock.name}) — закройте её, правка будет затёрта.")
        return 2

    with zipfile.ZipFile(path) as book:
        parts = {name: book.read(name) for name in book.namelist()}
        order = list(book.namelist())
    targets = sheet_targets(parts["xl/workbook.xml"], parts["xl/_rels/workbook.xml.rels"])

    shared = parts["xl/sharedStrings.xml"].decode("utf-8")
    total = 0
    for title, (ref, _old, new_title) in HEADERS.items():
        part = targets.get(title)
        if not part:
            print(f"Лист «{title}» не найден — пропускаю")
            continue
        xml, patched = patch_formulas(parts[part].decode("utf-8"))
        total += patched
        shared, index = add_shared_string(shared, new_title)
        xml, renamed = retitle(xml, ref, index)
        parts[part] = xml.encode("utf-8")
        print(f"«{title}»: формул поправлено {patched}, заголовок {ref} — {'да' if renamed else 'НЕТ'}")
    parts["xl/sharedStrings.xml"] = shared.encode("utf-8")
    parts["xl/workbook.xml"] = force_full_recalc(parts["xl/workbook.xml"].decode("utf-8")).encode("utf-8")
    # Цепочка пересчёта описывает старый набор формул; Excel строит её заново.
    parts.pop("xl/calcChain.xml", None)
    order = [name for name in order if name != "xl/calcChain.xml"]

    if not total:
        print("Нечего править — формулы уже считают «йобо».")
        return 0
    if dry_run:
        print(f"--dry-run: файл не тронут (поправилось бы {total} формул)")
        return 0

    backup = path.with_name(f"{path.stem}.backup-{datetime.now():%Y%m%d-%H%M}{path.suffix}")
    shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for name in order:
            out.writestr(name, parts[name])
    tmp.replace(path)
    print(f"Готово. Резервная копия: {backup.name}")
    print("Откройте книгу в Excel и сохраните — она пересчитается и обновит кеш значений.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
