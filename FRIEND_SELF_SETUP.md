# Pulse Desk clean setup for a friend

This copy intentionally does not include the owner's `.env`, `*.session`,
database, logs, backups or virtual environment. Create your own Telegram
sessions on your own machine.

## 1. Open PowerShell in this folder

```powershell
cd "PATH_TO_THIS_FOLDER"
```

## 2. Create local config

```powershell
copy .env.example .env
```

Edit `.env` and set:

```env
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_SESSIONS=alga_kazakhst2n,w3v8f0rm,Fjfjfjfjds,MuverGT,Timofey02513,xdfusybau,davifd23,fsdfsdfdsg34
USERNAMES=alga_kazakhst2n,w3v8f0rm,Fjfjfjfjds,Timofey02513,MuverGT,xdfusybau,davifd23,fsdfsdfdsg34
ADMIN_TOKEN=make-a-long-private-owner-token
VIEWER_TOKEN=make-a-long-readonly-token
HOST=127.0.0.1
PORT=8000
```

Get `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` from:

```text
https://my.telegram.org/apps
```

`TELEGRAM_SESSIONS` are your login/session file names. `USERNAMES` are the
mentions to watch. The defaults use the same readable names as the tracked
accounts, but they are still your own local sessions after you log in. Do not
paste someone else's `.session` files here.

## 3. Install and run

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe auth_accounts.py
.\.venv\Scripts\python.exe main.py
```

Open:

```text
http://127.0.0.1:8000
```

## 4. If Telegram codes do not arrive

Run:

```powershell
.\.venv\Scripts\python.exe auth_accounts.py
```

Choose QR login when asked. Telegram on your phone:

```text
Settings -> Devices -> Link Desktop Device
```

Scan the QR shown in the terminal or open the generated local SVG from `data/`.
Use `4` to skip the current account or `5` to finish auth immediately.

## Never copy from the owner

Do not ask for or copy:

- `.env`
- `*.session`
- `*.db`
- `logs/`
- `backups/`
- `ADMIN_TOKEN`
