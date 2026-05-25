# Telegram Ping Watcher / Pulse Desk

Готовая короткая инструкция для друзей лежит в [FRIENDS_GUIDE.md](FRIENDS_GUIDE.md).

Pulse Desk - локальное Python/FastAPI-приложение для мониторинга Telegram-упоминаний, найденных побед, розыгрышей и рабочих задач по ним. Приложение хранит данные в SQLite, показывает веб-интерфейс на `http://127.0.0.1:8000`, работает с Telegram account-session файлами через Telethon и не требует frontend-сборки.

## Что важно для копии приложения

- Все Python-зависимости лежат в одном файле: `requirements.txt`.
- Запуск для обычной локальной копии: `.\run_local.ps1`.
- Старый ручной запуск тоже работает: создать `.venv`, поставить `requirements.txt`, запустить `.\.venv\Scripts\python.exe main.py`.
- Секреты не входят в копию: `.env`, `*.session`, `*.db`, логи и бэкапы нельзя отправлять другим людям.
- Для реального мониторинга нужны `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` из `https://my.telegram.org/apps`.

## Структура

```text
main.py                 FastAPI-приложение и API
database.py             SQLite-схема, миграции и запросы
telegram_ping_watcher.py Telegram parsing/helpers
auth_accounts.py        консольный вход в Telegram-аккаунт
src/pulse_desk/         настройки, runtime, security, jobs, statuses
static/                 веб-интерфейс без сборщика
tests/                  unit-тесты
requirements.txt        полный pinned-набор Python-пакетов
run_local.ps1           создание .venv, установка зависимостей и запуск
share_with_friends.ps1  запуск локального сервера и Cloudflare Quick Tunnel
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

После старта откройте:

```text
http://127.0.0.1:8000
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
ADMIN_TOKEN=change-this-owner-token
VIEWER_TOKEN=change-this-friend-token
HOST=127.0.0.1
PORT=8000
```

Коротко по важным полям:

- `TELEGRAM_SESSIONS` - имена session-файлов без `.session`; если пусто, приложение само ищет `*.session`.
- `USERNAMES` и `EXTRA_USERNAMES` - usernames, которые надо отслеживать в сообщениях.
- `ADMIN_TOKEN` - полный доступ владельца.
- `VIEWER_TOKEN` - доступ только на просмотр для друзей.
- `HOST=127.0.0.1` - безопасный локальный режим.
- `SCAN_ACCOUNT_CONCURRENCY` - сколько аккаунтов сканировать параллельно.
- `SCAN_HISTORY_LIMIT` - сколько найденных сообщений брать на проход `канал x username`; `0` означает без лимита.
- `EDIT_SCAN_RECENT_MESSAGES` - сколько последних постов канала перепроверять в фоновом скане, чтобы ловить правки с победителями; `0` отключает эту проверку.
- `STARTUP_SCAN_DELAY_SECONDS` - пауза перед первым фоновым сканом после запуска.
- `PULSE_DB_PATH`, `PULSE_SESSION_DIR`, `PULSE_LOG_DIR` - необязательные пути для базы, сессий и логов.

Не отправляйте друзьям `.env`, `.session`, `.db`, `app.log` и содержимое `backups/`.

## Вход в Telegram-аккаунт

Через веб-интерфейс используйте вкладку аккаунтов. Имя сессии должно совпадать с тем, что вы хотите видеть в `TELEGRAM_SESSIONS`, например `MuverGT`.

Консольный вариант:

```powershell
.\.venv\Scripts\python.exe auth_accounts.py
```

## Доступ для друзей

Бесплатный безопасный вариант - Cloudflare Quick Tunnel. Друзья открывают HTTPS-ссылку в браузере и входят с `VIEWER_TOKEN`; им не нужны ваши session-файлы или база.

```powershell
.\share_with_friends.ps1
```

Скрипт теперь тоже использует локальный `.venv` и при необходимости ставит зависимости из `requirements.txt`. Для него отдельно нужен `cloudflared.exe` в PATH.

## Проверка

```powershell
.\.venv\Scripts\python.exe -m py_compile main.py database.py telegram_ping_watcher.py auth_accounts.py src\pulse_desk\api_models.py src\pulse_desk\config.py src\pulse_desk\dashboard.py src\pulse_desk\deadlines.py src\pulse_desk\giveaways.py src\pulse_desk\jobs.py src\pulse_desk\logging_config.py src\pulse_desk\runtime.py src\pulse_desk\scan.py src\pulse_desk\security.py src\pulse_desk\statuses.py
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Для frontend JS, если установлен Node.js:

```powershell
node --check static/app.js
node --check static/js/core.js
node --check static/js/giveaways.js
node --check static/js/diagnostics.js
node --check static/js/pwa.js
```

## Docker

```powershell
docker compose up --build
```

Docker тоже устанавливает Python-пакеты только из `requirements.txt`. `.env`, session-файлы, база, логи и бэкапы не копируются в образ.
