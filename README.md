# Telegram Ping Watcher / Pulse Desk

Готовая короткая инструкция для друзей лежит в [FRIENDS_GUIDE.md](FRIENDS_GUIDE.md).

Pulse Desk — локальное Python-приложение для мониторинга Telegram-упоминаний, побед и
розыгрышей. Данные лежат в SQLite, работа идёт через Telethon и account-session файлы,
а **весь интерфейс — это Telegram-бот**.

Веб-приложение удалено 12.09.2026: всё, что оно показывало, переехало в бота. По HTTP
осталась одна ручка `http://127.0.0.1:8000/api/health` — её опрашивают сторож и скрипт
автозапуска.

## Что важно для копии приложения

- Все Python-зависимости лежат в одном файле: `requirements.txt`.
- Запуск для обычной локальной копии: `.\run_local.ps1`.
- Старый ручной запуск тоже работает: создать `.venv`, поставить `requirements.txt`, запустить `.\.venv\Scripts\python.exe main.py`.
- Секреты не входят в копию: `.env`, `*.session`, `*.db`, логи и бэкапы нельзя отправлять другим людям.
- Для реального мониторинга нужны `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` из `https://my.telegram.org/apps`.

## Структура

```text
main.py                 точка входа: фоновые джобы, бот и /api/health
database.py             SQLite-схема, миграции и запросы
telegram_ping_watcher.py Telegram parsing/helpers
auth_accounts.py        консольный вход в Telegram-аккаунт
src/pulse_desk/         настройки, runtime, jobs, statuses
src/pulse_desk/bot/     весь интерфейс: разделы, клавиатуры, карточки, рендер картинок
tests/                  unit-тесты
requirements.txt        полный pinned-набор Python-пакетов
run_local.ps1           создание .venv, установка зависимостей и запуск
```

## Быстрый запуск на Windows

Откройте PowerShell в папке проекта и выполните:

```powershell
.\run_local.ps1
```

Скрипт сделает три вещи:

1. Создаст `.venv`, если её ещё нет.
2. Установит все Python-пакеты из `requirements.txt`.
3. Запустит приложение через `.\.venv\Scripts\python.exe main.py`.

После старта напишите боту `/menu` — это и есть интерфейс. Проверить, что приложение
поднялось, можно так:

```text
http://127.0.0.1:8000/api/health
```

Если зависимости уже установлены и нужно просто запустить:

```powershell
.\run_local.ps1 -SkipInstall
```

## Ручной запуск

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
.\.venv\Scripts\python.exe main.py
```

На этой машине надёжный вариант именно `.\.venv\Scripts\python.exe main.py`, потому что обычный `python main.py` зависит от PATH и может указывать на другой Python без нужных библиотек.

## Настройка `.env`

Если `.env` ещё нет:

```powershell
copy .env.example .env
```

Минимум для Telegram:

```env
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_api_hash_here
TELEGRAM_SESSIONS=
USERNAMES=alga_kazakhst2n,w3v8f0rm,Fjfjfjfjds,Timofey02513,MuverGT,xdfusybau,davifd23,fsdfsdfdsg34
TELEGRAM_BOT_TOKEN=your_bot_token
ADMIN_ID=your_telegram_id
HOST=127.0.0.1
PORT=8000
```

Коротко по важным полям:

- `TELEGRAM_SESSIONS` - имена session-файлов без `.session`; если пусто, приложение само ищет `*.session`.
- `USERNAMES` и `EXTRA_USERNAMES` - usernames, которые надо отслеживать в сообщениях.
- `TELEGRAM_BOT_TOKEN` и `ADMIN_ID` - бот и его владелец; без них интерфейса нет.
- Доступ друзьям выдаётся ключом из самого бота (`/newkey`), а не токеном.
- `HOST=127.0.0.1` - безопасный локальный режим.
- `SCAN_ACCOUNT_CONCURRENCY` - сколько аккаунтов сканировать параллельно.
- `SCAN_HISTORY_LIMIT` - сколько найденных сообщений брать на проход `канал x username`; `0` означает без лимита.
- `EDIT_SCAN_RECENT_MESSAGES` - сколько последних постов канала перепроверять в фоновом скане, чтобы ловить правки с победителями; `0` отключает эту проверку.
- `STARTUP_SCAN_DELAY_SECONDS` - пауза перед первым фоновым сканом после запуска.
- `TELEGRAM_CONNECT_TIMEOUT_SECONDS`, `TELEGRAM_RETRY_DELAY_SECONDS`, `TELEGRAM_RECONNECT_BASE_SECONDS`, `TELEGRAM_RECONNECT_MAX_SECONDS`, `TELEGRAM_RECONNECT_JITTER_SECONDS` - настройки сетевого timeout/backoff для Telethon; помогают не засыпать лог при временных `WinError 121` и переподключать аккаунты с паузой.
- `PULSE_DB_PATH`, `PULSE_SESSION_DIR`, `PULSE_LOG_DIR` - необязательные пути для базы, сессий и логов.

Не отправляйте друзьям `.env`, `.session`, `.db`, `app.log` и содержимое `backups/`.

## Вход в Telegram-аккаунт

Вход в аккаунт делается консольным `auth_accounts.py` — код подтверждения нельзя вводить
в Telegram-чат, Telegram его аннулирует. Имя сессии должно совпадать с тем, что вы хотите
видеть в `TELEGRAM_SESSIONS`, например `MuverGT`.

Консольный вариант:

```powershell
.\.venv\Scripts\python.exe auth_accounts.py
```

## Доступ для друзей

Друг получает **ключ доступа в боте**, а не ссылку на веб: владелец пишет боту `/newkey`,
отдаёт ссылку `t.me/<бот>?start=<ключ>`, и в панели ключа настраивает, какие разделы и
какие уведомления другу видны, по каким аккаунтам и с какой задержкой. Ключ можно
отозвать или удалить там же. Ни session-файлы, ни база другу не нужны.

## Проверка

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -c "import main"
```

## Docker

```powershell
docker compose up --build
```

Docker тоже устанавливает Python-пакеты только из `requirements.txt`. `.env`, session-файлы, база, логи и бэкапы не копируются в образ.
