# Telegram sessions and another IP

Telegram/Telethon session files (`*.session`) are device keys. Do not run the
same copied session file from two IP addresses at the same time. If it happens,
Telegram can invalidate the key and Telethon raises:

```text
The authorization key (session file) was used under two different IP addresses simultaneously
```

## If the error already happened

1. Stop Pulse Desk on every machine/IP where this copied session may be running.
2. Move the broken session file out of the active folder, or delete it if you do
   not need a backup. Move the matching `*.session-journal` file too if it exists.
3. Sign in again and let Telethon create a fresh session.

Example from the project root:

```powershell
.\.venv\Scripts\python.exe auth_accounts.py
```

## How to run from another IP

Use separate session files on the second IP. Do not copy active `*.session`
files from the first IP.

On the second machine/IP, set unique values in `.env`:

```env
PULSE_SESSION_DIR=sessions-ip2
TELEGRAM_SESSIONS=alga_kazakhst2n_ip2,w3v8f0rm_ip2,Fjfjfjfjds_ip2,MuverGT_ip2
```

Then authenticate these new session names on that machine:

```powershell
.\.venv\Scripts\python.exe auth_accounts.py
```

If login codes do not arrive, use the new interactive fallback in
`auth_accounts.py`:

```text
1 - QR/deep-link login
2 - Telegram app code
3 - SMS code
```

Choose `1` first when Telegram says the code was sent to an already logged-in
app but nothing appears. Choose `3` when you specifically want to ask Telegram
for SMS. Do not submit an empty code; Pulse Desk now ignores empty input so it
does not burn Telegram resend attempts.

Keep `USERNAMES` the same if you want to track the same Telegram usernames.
`TELEGRAM_SESSIONS` are account login files; `USERNAMES` are mentions to watch.

If giveaway actions must use one specific account, set `GIVEAWAY_ACTION_ACCOUNT`
to the real Telegram username or to the new session name used on that IP.

## If you want to move, not run in parallel

You may move a session to a new IP only when the old copy is fully stopped.
For a stable setup, separate sessions per IP are still safer.
