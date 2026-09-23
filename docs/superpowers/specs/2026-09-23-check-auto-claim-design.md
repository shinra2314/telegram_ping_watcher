# Auto-claim of crypto checks (xRocket, CryptoBot) — design

Date: 2026-09-23. Asked by the owner: «скрипт на авто забирание таких чеков, но
важно чтобы чеки отправленные 1 из наших аккаунтов автоматически не забирались»,
with a screenshot of an xRocket check («Чек на 5 USDT», button «Получить 5 USDT»)
and a link to `t.me/ludka2k33/357065` — a personal xRocket check «для @MCshinra»
(session `w3v8f0rm`) sent by an outsider in the group «промодрочь 2015».

## Decisions (owner's answers, 23.09)

| Question | Answer |
|---|---|
| General check: how many accounts try | not answered → **every account that received the message** (recommended option) |
| Notify or press | **the script presses itself**; a human is asked only where a human is required |
| Start mode after deploy | **claim at once** (`mode = claim`) |
| Bots | **xRocket + CryptoBot** (`@send` / `@CryptoBot`) |

Not built, on purpose: **solving captchas.** A captcha is the check author's
"humans only" switch; the script does not defeat it. Instead the captcha (text,
picture, its buttons) is mirrored into the owner's bot chat; the owner taps the
answer and the script presses that same button in the wallet bot, from the
account that hit it. Everything around that tap is automatic.

## Where it runs

Inside Pulse Desk, not as a separate script. All accounts are already connected
there; a second process on the same `.session` files splits Telegram's updates
between two connections and fights over the session file lock.

The hook sits in each account's live `NewMessage` / `MessageEdited` handler
(`telegram_accounts.start_client`) **before** the shared `remember_message`
dedupe — that dedupe processes a message once for all accounts, while here every
account decides for itself — and before `process_ping_message`, so a claim never
waits on classification or SQLite.

## Units

* `check_claims.py` — pure: link extraction, "is this a check", amount, addressee
  («для @X»), password written in the post, own-sender rule, reply
  classification, config normalisation. No Telethon I/O.
* `check_claimer.py` — I/O: `on_message` (sync, schedules a task), `claim`,
  reply polling, auto-subscribe, password entry, relay cards, bot warm-up,
  admin-chat harvesting from the dialog list.
* `database/check_claims.py` — the journal table `check_claims` (schema 25).
* `bot/sections/checks.py` — ⚙️ → 🧾 Чеки (`ck:*`, owner only): mode, which
  accounts claim, recent attempts, totals; relay buttons.

## Detection

A check is a link `t.me/<bot>?start=<code>` (URL button, hidden text link or
plain text) where `<bot>` is `xrocket`, `send` or `CryptoBot`, **and** the
message looks like a check: the word «чек/мультичек/cheque/check» in the text or a
button label like «Получить/Забрать/Receive/Claim». Referral links in ordinary
chatter do not count. Codes: CryptoBot only `CQ…` (invoices are `IV…`); xRocket
anything except `inv…`. Amount from the button label or text («0.1 USDT»).

Personal check («для @user» / «for @user»): only the account whose live
`username` matches tries; an addressee that is none of ours → skipped.

Only live messages no older than 30 min (`MAX_AGE_SECONDS`) — the morning
catch-up replays checks that are long gone.

## Own checks are never claimed

Skipped for every account when any of:

1. the sender is one of our accounts (`state.connected_user_ids`) or the owner
   (`ADMIN_ID`);
2. `message.out` for the receiving account (our account posted it — also as a
   channel or as an anonymous admin); the code is remembered for the others;
3. the sender is a channel/chat itself and one of our accounts is its creator or
   admin (harvested from the dialog list every sweep, persisted in the
   `check_claim_admin_chats` key so a restart does not open a window);
4. the code was already seen as ours (rule 2, or a code the wallet bot sent one
   of our accounts in its private chat — a check created there).

The wallet bot itself also refuses a creator's own check; that reply is
classified `own`.

## Claim

`messages.startBot(bot, peer=bot, start_param=code)` — exactly what the
«Получить» button does. Then the bot's private chat is polled (Telethon's
Conversation API races the dispatcher here, see memory) for up to 12 s and the
reply is classified:

| Outcome | What happens |
|---|---|
| `claimed` | journal + owner card «🧾 Чек забран» |
| `gone`, `not_for_you`, `premium`, `own` | journal only |
| `subscribe` | join the linked channels (≤ 3, public or invite), press the bot's «check» button (or restart the check), classify again |
| `password` | password written in the post → sent; otherwise → relay |
| `captcha`, `unknown` | relay card (first account per check; the card offers the next account afterwards) |
| `error` (flood, network) | journal |

Every attempt is one journal row per (account, bot, code); a relay updates it.

## Relay card

Bot reply text (+ picture, re-sent by the bot), its callback buttons mirrored as
`ck:b:<token>:<row>:<col>`, «✍️ Ответить текстом» (`ck:t:<token>`, a pending
prompt whose text the account sends to the wallet bot), «🔁 Повторить»
(`ck:r:<token>`, startBot again), «▶️ Следующий аккаунт» (`ck:n:<token>`) when
other accounts stopped on the same check. Relays live 30 min
(`bot-janitor`).

## Settings

Key `check_claim`: `{"mode": "claim" | "watch" | "off", "disabled": [session…]}`;
absent → `claim`, all accounts. `watch` announces «👀 Поймал бы» once per check
and presses nothing. Applied to `state.check_claim_cfg` at startup and on save.

## Tests

Pure: detection (button / hidden link / plain text; referral ignored; invoice
ignored), amount, addressee, password, own rules, classification (RU/EN),
config. I/O with fakes: claim path calls startBot only for eligible accounts,
personal check → addressee only, own → nobody, watch → nobody, dedupe, stale
message, subscribe path joins then presses, relay click presses the mirrored
button. Route test covers `ck`.
