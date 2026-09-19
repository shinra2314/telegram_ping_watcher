# add_salary_expenses_column.ps1 - give monthly expenses their own pair of columns.
#
# Running the accounts costs money (proxies, subscriptions, fees) and the book
# had nowhere to put it, so the only way to carry a cost was to quietly shade
# someone's percentage. That is a lie with arithmetic on top: the guy reads his
# rate in the bot, and a rate that does not mean what it says is a rate nobody
# can check. An expense line can be checked.
#
# The deduction happens BEFORE the split, so owner and account carry the cost in
# the same proportion as they carry the prize:
#
#   Итого = E+G+I - K,  K = Расходы * Доля
#         = выигрыш*доля - расходы*доля
#         = (выигрыш - расходы) * доля
#
# which is exactly «вычет до доли», arranged so the per-kind columns keep their
# meaning and the deduction stays a visible line instead of a shaved percent.
#
# After, on «Выплаты по месяцам»:
#   D Крипта · E к выплате · F Скины · G к выплате · H йобо · I к выплате
#   J Расходы (ВРУЧНУЮ) · K Удержано с доли · L Итого (=E+G+I-K)
#   M Дата выплаты · N Статус
# and on «Сводка»:
#   … H йобо · I владельцу · J Расходы всего · K Удержано · L Всего выиграно
#   B17 «Всего к выдаче» = E11+G11+I11-K11   (иначе C19 сверки закричит
#   «НЕ СХОДИТСЯ»: она сравнивает B17 с итогом помесячного листа)
#   B18 «Остаётся организатору» = B14-B17-J11 — расходы платит организатор из
#   своего остатка, поэтому здесь они вычитаются ещё раз, а не «уже учтены».
#
# Column J is left EMPTY on purpose: it is the owner's manual input, like «Дата
# выплаты», and gets that column's fill so the book's own «жёлтое = вписать
# руками» convention still reads. Пустая ячейка = 0 расходов, поэтому все
# прошлые месяцы остаются цифра в цифру теми же.
#
# The insert runs through Excel itself (COM), not by editing the XML: Excel is
# what shifts every formula, merged cell, width and conditional format that
# pointed at the old J/K/L. It also recalculates and saves, so the cached <v>
# values Pulse Desk reads are the new ones.
#
# Порядок с приложением не важен: salary.month_columns определяет разметку по
# заголовку J5, поэтому бот правильно читает и старую книгу, и новую.
#
# Run it with the workbook CLOSED:
#   powershell -File scripts\add_salary_expenses_column.ps1
#   powershell -File scripts\add_salary_expenses_column.ps1 -Path C:\путь\книга.xlsx
param(
    [string]$Path = "",
    [switch]$SkipBackup
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LF = [char]10

$SHEET_MONTHS = "Выплаты по месяцам"
$SHEET_TOTALS = "Сводка"

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
# у владельца рядом может быть открыт любой другой файл.
$lock = Join-Path (Split-Path -Parent $book) ("~`$" + (Split-Path -Leaf $book))
if (Test-Path $lock) { throw "Рядом с книгой лежит lock-файл $(Split-Path -Leaf $lock) - книга открыта или осталась от аварийной сессии." }
if (@(Get-Process -Name EXCEL -ErrorAction SilentlyContinue).Count -gt 0) {
    try {
        $running = [System.Runtime.InteropServices.Marshal]::GetActiveObject("Excel.Application")
        foreach ($open in $running.Workbooks) {
            if ($open.FullName -eq $book) { throw "Книга открыта в Excel - закройте её и повторите." }
        }
    } catch [System.Runtime.InteropServices.COMException] {
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
    if ($ws.Range("J5").Text -like "*асход*") { throw "На листе «$SHEET_MONTHS» столбец J уже про расходы - правка уже применялась." }
    # Вставлять вслепую нельзя: если «Итого» стоит не в J, книга уже другая, и
    # два новых столбца встали бы посреди чужих данных.
    if ($ws.Range("J5").Text -notlike "*того*") { throw "На листе «$SHEET_MONTHS» в J5 ожидалось «Итого к выплате», а там «$($ws.Range('J5').Text)»." }

    $ws.Range("J:K").Insert() | Out-Null
    # Копия с указанием куда, а не Copy+PasteSpecial: та оставляет включённым
    # режим буфера, и следующая вставка столбцов превратилась бы во «вставить
    # скопированные ячейки». Формулы, приехавшие с форматом, ниже переписываются.
    $ws.Range("D5:E149").Copy($ws.Range("J5:K149")) | Out-Null

    $ws.Range("J5").Value2 = "Расходы" + $LF + "за месяц, `$"
    $ws.Range("K5").Value2 = "Удержано" + $LF + "с доли, `$"

    # J - ручной столбец: ни одной формулы, и выглядит как «Дата выплаты»,
    # то есть как ячейка, в которую вписывают руками.
    # Заливку берём у «Даты выплаты» напрямую, а не через буфер обмена:
    # PasteSpecial оставляет режим копирования включённым, а погасить его из
    # PowerShell нечем - типизированный CutCopyMode не принимает ни 0, ни $false
    # (в VBA это Application.CutCopyMode = False). Заодно Pattern ставится
    # ПОСЛЕ Color: присвоение цвета само делает заливку сплошной, и колонка без
    # заливки иначе стала бы белой вместо прозрачной.
    $ws.Range("J6:J149").ClearContents() | Out-Null
    $manualColor = $ws.Range("M6").Interior.Color
    $manualPattern = $ws.Range("M6").Interior.Pattern
    $ws.Range("J6:J149").Interior.Color = $manualColor
    $ws.Range("J6:J149").Interior.Pattern = $manualPattern
    $ws.Range("J6:J149").NumberFormat = $ws.Range("D6").NumberFormat

    $ws.Range("K6:K149").Formula = '=$J6*$C6'
    $ws.Range("L6:L149").Formula = '=$E6+$G6+$I6-$K6'
    $ws.Range("N6:N149").Formula = '=IF(AND($D6=0,$F6=0,$H6=0),"нет выигрышей",IF($M6="","ОЖИДАЕТ ВЫПЛАТЫ","выплачено"))'
    $ws.Range("J151").Formula = '=SUM(J6:J149)'
    $ws.Range("K151").Formula = '=SUM(K6:K149)'
    $ws.Columns.Item("J").ColumnWidth = 15
    $ws.Columns.Item("K").ColumnWidth = 17

    # ---- «Сводка» -------------------------------------------------------------
    $ts = $wb.Worksheets.Item($SHEET_TOTALS)
    if ($ts.Range("J4").Text -like "*асход*") { throw "На листе «$SHEET_TOTALS» столбец J уже про расходы - правка уже применялась." }
    if ($ts.Range("J4").Text -notlike "*сего выиграно*") { throw "На листе «$SHEET_TOTALS» в J4 ожидалось «Всего выиграно», а там «$($ts.Range('J4').Text)»." }

    $ts.Range("J:K").Insert() | Out-Null
    $ts.Range("D4:E11").Copy($ts.Range("J4:K11")) | Out-Null

    $ts.Range("J4").Value2 = "Расходы" + $LF + "всего, `$"
    $ts.Range("K4").Value2 = "Удержано" + $LF + "с доли, `$"

    $expSum = "=SUMIFS('{0}'!`$J`$6:`$J`$149,'{0}'!`$B`$6:`$B`$149,`$A5)" -f $SHEET_MONTHS
    $ts.Range("J5:J10").Formula = $expSum
    $ts.Range("K5:K10").Formula = '=$J5*$B5'
    $ts.Range("J11:K11").Formula = '=SUM(J5:J10)'

    # Сверка C19 сравнивает B17 с итогом помесячного листа, а тот теперь за
    # вычетом удержанного - без -K11 книга сама объявила бы себя сломанной.
    $ts.Range("B17").Formula = '=E11+G11+I11-K11'
    $ts.Range("A18").Value2 = "Остаётся организатору (после расходов), `$"
    $ts.Range("B18").Formula = '=B14-B17-J11'
    $ts.Range("A27").Value2 = "6. «Расходы» на «Выплатах по месяцам» вписываются руками и вычитаются ДО дележа: доля считается от остатка."
    $ts.Columns.Item("J").ColumnWidth = 15
    $ts.Columns.Item("K").ColumnWidth = 17

    $excel.CalculateFullRebuild()
    $wb.Save()
    $saved = $true
    Write-Host "Готово: «$SHEET_MONTHS» J/K, «$SHEET_TOTALS» J/K, книга пересчитана и сохранена."
} finally {
    # Закрывать с сохранением можно только после удачного прохода: упади мы на
    # середине - на диск легла бы книга со вставленными, но не заполненными
    # столбцами.
    if ($wb) { $wb.Close($saved) | Out-Null }
    $excel.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
}
