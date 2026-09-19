# add_salary_yobo_column.ps1 - give «йобо» its own pair of columns in the salary book.
#
# Before: the two money sheets knew two reward types — Крипта (Настройки!$A$16)
# in column D and Скины ($A$17) in column F. «йобо» ($A$18) existed in the
# journal and in Настройки, but no sheet summed it, so those wins reached
# nobody's salary. fix_salary_yobo_formulas.py solved that by folding йобо into
# the Крипта column; the owner asked for a column of its own instead (18.09).
#
# After, on «Выплаты по месяцам»:
#   D Крипта · E к выплате · F Скины · G к выплате · H йобо · I к выплате
#   J Итого (=E+G+I) · K Дата выплаты · L Статус
# and on «Сводка»:
#   D Крипта · E владельцу · F Скины · G владельцу · H йобо · I владельцу
#   J Всего выиграно
#
# The insert runs through Excel itself (COM), not by editing the XML: Excel is
# what shifts every formula, merged cell, width and conditional format that
# pointed at the old H/I/J. It also recalculates and saves, so the cached <v>
# values Pulse Desk reads are the new ones — no manual open-and-save afterwards.
#
# Run it with the workbook CLOSED:
#   powershell -File scripts\add_salary_yobo_column.ps1
#   powershell -File scripts\add_salary_yobo_column.ps1 -Path C:\путь\книга.xlsx
param(
    [string]$Path = "",
    [switch]$SkipBackup
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LF = [char]10

# Названия листов и типов наград живут в книге, а не в скрипте: меняется книга —
# меняется одна строка здесь.
$SHEET_MONTHS = "Выплаты по месяцам"
$SHEET_TOTALS = "Сводка"
$CRYPTO = "Настройки!`$A`$16"
$YOBO = "Настройки!`$A`$18"

function Resolve-BookPath([string]$given) {
    if ($given) { return $given }
    if ($env:SALARY_XLSX_PATH) { return $env:SALARY_XLSX_PATH }
    $envFile = Join-Path $root ".env"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile -Encoding UTF8) {
            if ($line -like "SALARY_XLSX_PATH=*") { return $line.Substring("SALARY_XLSX_PATH=".Length).Trim() }
        }
    }
    throw "Не задан путь к книге: -Path или SALARY_XLSX_PATH"
}

$book = Resolve-BookPath $Path
if (-not (Test-Path $book)) { throw "Файл не найден: $book" }
$book = (Resolve-Path $book).Path

# Открытая книга - это вторая копия в памяти Excel, которая затрёт нашу правку
# при своём сохранении. Проверяем именно ЭТУ книгу, а не «запущен ли Excel»:
# у владельца рядом может быть открыт любой другой файл, и запрещать из-за него
# нечего. Лок-файл ловит и открытую книгу, и остаток аварийной сессии; список
# открытых книг живого Excel - случай, когда лок по какой-то причине не создан.
$lock = Join-Path (Split-Path -Parent $book) ("~`$" + (Split-Path -Leaf $book))
if (Test-Path $lock) { throw "Рядом с книгой лежит lock-файл $(Split-Path -Leaf $lock) - книга открыта или осталась от аварийной сессии." }
if (@(Get-Process -Name EXCEL -ErrorAction SilentlyContinue).Count -gt 0) {
    try {
        $running = [System.Runtime.InteropServices.Marshal]::GetActiveObject("Excel.Application")
        foreach ($open in $running.Workbooks) {
            if ($open.FullName -eq $book) { throw "Книга открыта в Excel - закройте её и повторите." }
        }
    } catch [System.Runtime.InteropServices.COMException] {
        # Живой Excel есть, но он не отдаёт себя через ROT (другой сеанс, запуск
        # из-под иных прав). Молча продолжать нельзя - именно этот случай и
        # затирает правку.
        throw "Excel запущен, но его список книг недоступен - закройте Excel и повторите."
    }
}

if (-not $SkipBackup) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmm"
    $backup = Join-Path (Split-Path -Parent $book) ("{0}.backup-{1}.xlsx" -f [System.IO.Path]::GetFileNameWithoutExtension($book), $stamp)
    Copy-Item -LiteralPath $book -Destination $backup
    Write-Host "Резервная копия: $(Split-Path -Leaf $backup)"
}

$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false
$excel.DisplayAlerts = $false
$wb = $null
$saved = $false
try {
    $wb = $excel.Workbooks.Open($book)

    # ---- «Выплаты по месяцам» -------------------------------------------------
    $ws = $wb.Worksheets.Item($SHEET_MONTHS)
    if ($ws.Range("H5").Text -like "*йобо*") { throw "На листе «$SHEET_MONTHS» столбец H уже про йобо - правка уже применялась." }

    # Вставка двух столбцов перед «Итого»: Excel сам сдвигает ссылки на H/I/J
    # во всём файле, включая итоговые SUMPRODUCT внизу листа и ссылку «Сводки».
    $ws.Range("H:I").Insert() | Out-Null
    # Копия с указанием куда, а не Copy+PasteSpecial: та оставляет включённым
    # режим буфера, и следующая вставка столбцов на другом листе превратилась бы
    # во «вставить скопированные ячейки». Формулы, приехавшие вместе с форматом,
    # ниже переписываются целиком.
    $ws.Range("D5:E149").Copy($ws.Range("H5:I149")) | Out-Null

    $ws.Range("H5").Value2 = "йобо" + $LF + "за месяц, `$"
    $ws.Range("I5").Value2 = "К выплате" + $LF + "йобо, `$"
    # Столбец D мог остаться «Крипта+йобо» от прошлой правки - возвращаем его
    # к одной только крипте, иначе йобо посчитается дважды.
    $ws.Range("D5").Value2 = "Крипта" + $LF + "за месяц, `$"

    $monthSum = '=SUMIFS(Журнал!$E$4:$E$503,Журнал!$B$4:$B$503,$B6,Журнал!$C$4:$C$503,{0},Журнал!$A$4:$A$503,">="&$A6,Журнал!$A$4:$A$503,"<"&DATE(YEAR($A6),MONTH($A6)+1,1))'
    $ws.Range("D6:D149").Formula = ($monthSum -f $CRYPTO)
    $ws.Range("H6:H149").Formula = ($monthSum -f $YOBO)
    $ws.Range("I6:I149").Formula = '=$H6*$C6'
    $ws.Range("J6:J149").Formula = '=$E6+$G6+$I6'
    $ws.Range("L6:L149").Formula = '=IF(AND($D6=0,$F6=0,$H6=0),"нет выигрышей",IF($K6="","ОЖИДАЕТ ВЫПЛАТЫ","выплачено"))'
    $ws.Range("H151:I151").Formula = '=SUM(H6:H149)'
    $ws.Columns.Item("H").ColumnWidth = 15
    $ws.Columns.Item("I").ColumnWidth = 17

    # ---- «Сводка» -------------------------------------------------------------
    $ts = $wb.Worksheets.Item($SHEET_TOTALS)
    if ($ts.Range("H4").Text -like "*йобо*") { throw "На листе «$SHEET_TOTALS» столбец H уже про йобо - правка уже применялась." }

    $ts.Range("H:I").Insert() | Out-Null
    $ts.Range("D4:E11").Copy($ts.Range("H4:I11")) | Out-Null

    $ts.Range("H4").Value2 = "йобо" + $LF + "всего, `$"
    $ts.Range("I4").Value2 = "Владельцу" + $LF + "йобо, `$"
    $ts.Range("D4").Value2 = "Крипта" + $LF + "всего, `$"

    $totalSum = '=SUMIFS(Журнал!$E$4:$E$503,Журнал!$B$4:$B$503,$A5,Журнал!$C$4:$C$503,{0})'
    $ts.Range("D5:D10").Formula = ($totalSum -f $CRYPTO)
    $ts.Range("H5:H10").Formula = ($totalSum -f $YOBO)
    $ts.Range("I5:I10").Formula = '=$H5*$B5'
    $ts.Range("H11:I11").Formula = '=SUM(H5:H10)'
    # «Всего к выдаче владельцам» складывал только деньги и скины.
    $ts.Range("B17").Formula = '=E11+G11+I11'
    $ts.Columns.Item("H").ColumnWidth = 15
    $ts.Columns.Item("I").ColumnWidth = 17

    $excel.CalculateFullRebuild()
    $wb.Save()
    $saved = $true
    Write-Host "Готово: «$SHEET_MONTHS» H/I, «$SHEET_TOTALS» H/I, книга пересчитана и сохранена."
} finally {
    # Закрывать с сохранением можно только после удачного прохода: упади мы на
    # середине - на диск легла бы книга со вставленными, но не заполненными
    # столбцами.
    if ($wb) { $wb.Close($saved) | Out-Null }
    $excel.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
}
