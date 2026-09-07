# Aperture Mini App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the bot a Telegram Mini App with custom line-art icons and card/pill buttons — Home, Giveaways, Rates + Converter — while every existing inline keyboard keeps working untouched.

**Architecture:** A second ASGI app on its own port serves the Mini App and nothing else; a supervised `cloudflared` quick tunnel publishes that port over HTTPS and writes the URL into `state.public_url`. Every JSON call authenticates by verifying Telegram's `initData` HMAC, then resolves the caller through the same membership and grant rules the bot already uses. WebApp buttons are built at send time and simply vanish when no tunnel is up.

**Tech Stack:** Python 3.13, FastAPI, uvicorn, Telethon 1.43.2, aiosqlite, pydantic-settings, unittest (`asyncio_mode = "auto"`), vanilla JS + CSS (no build step).

**Run tests:** `.\.venv\Scripts\python.exe -m pytest tests\ -q`

**Spec:** `docs/superpowers/specs/2026-09-07-aperture-mini-app-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `src/pulse_desk/bot_membership.py` | NEW. Telegram user → (role, grants). Extracted from `bot/service.py` so the Mini App and the bot share one rule set. |
| `src/pulse_desk/miniapp_auth.py` | NEW. Pure `initData` parsing + HMAC verification. |
| `src/pulse_desk/tunnel.py` | NEW. `cloudflared` quick-tunnel job; owns `state.public_url`. |
| `src/pulse_desk/miniapp_server.py` | NEW. The second ASGI app + its uvicorn task. |
| `routers/miniapp.py` | NEW. The page route and `/api/app/*` JSON. |
| `static/app/index.html` | NEW. Mini App shell. |
| `static/app/app.css` | NEW. Aperture card/pill styling. |
| `static/app/icons.js` | NEW. `{name: svg path}` line-art icon map. |
| `static/app/app.js` | NEW. Screens, routing, fetch wrapper. |
| `src/pulse_desk/runtime.py` | Modify. Add `public_url`. |
| `src/pulse_desk/config.py` | Modify. Add `MINIAPP_ENABLED`, `MINIAPP_PORT`, `CLOUDFLARED_BIN`. |
| `src/pulse_desk/bot/keyboards.py` | Modify. Add `webapp_row`. |
| `src/pulse_desk/bot/views.py` | Modify. Put the WebApp row on the home screen. |
| `src/pulse_desk/bot/service.py` | Modify. Delegate membership resolution to `bot_membership`. |
| `main.py` | Modify. Start the tunnel job + the Mini App server. |

---

### Task 1: Extract membership resolution into `bot_membership.py`

`resolve_member_access` currently lives as a closure inside `init_bot`
(`src/pulse_desk/bot/service.py:250`), so nothing outside the bot's handlers can
call it. The Mini App must apply the exact same rules. Move it to a module.

**Files:**
- Create: `src/pulse_desk/bot_membership.py`
- Modify: `src/pulse_desk/bot/service.py:214-271`
- Test: `tests/test_bot_membership.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bot_membership.py`:

```python
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk import bot_membership


class AdminChatIdsTests(unittest.TestCase):
    def test_admin_id_alone(self):
        self.assertEqual(bot_membership.admin_chat_ids("77", ""), {77})

    def test_extra_chats_are_merged(self):
        self.assertEqual(bot_membership.admin_chat_ids("77", "88, 99"), {77, 88, 99})

    def test_junk_entries_are_skipped(self):
        self.assertEqual(bot_membership.admin_chat_ids("", "abc,12"), {12})

    def test_empty_everywhere(self):
        self.assertEqual(bot_membership.admin_chat_ids("", ""), set())


class ResolveMemberAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_gets_full_permissions(self):
        role, perms = await bot_membership.resolve_member_access(
            77, admin_ids={77}, get_member=self._never, touch=self._never,
            decide=self._never,
        )
        self.assertEqual(role, "admin")
        self.assertTrue(perms["features"])

    async def test_unknown_user_has_no_role(self):
        async def no_member(_tg):
            return None

        role, _perms = await bot_membership.resolve_member_access(
            5, admin_ids=set(), get_member=no_member, touch=self._noop,
            decide=self._never,
        )
        self.assertIsNone(role)

    async def test_blocked_member_has_no_role(self):
        async def member(_tg):
            return {"role": "viewer", "blocked": 1, "permissions": ""}

        role, _perms = await bot_membership.resolve_member_access(
            5, admin_ids=set(), get_member=member, touch=self._noop,
            decide=self._never,
        )
        self.assertIsNone(role)

    async def test_member_closed_by_schedule_has_no_role(self):
        async def member(_tg):
            return {"role": "viewer", "blocked": 0, "permissions": ""}

        async def closed(_tg, _member):
            return False, "schedule", None

        role, _perms = await bot_membership.resolve_member_access(
            5, admin_ids=set(), get_member=member, touch=self._noop, decide=closed,
        )
        self.assertIsNone(role)

    async def test_open_member_keeps_role_and_grants(self):
        async def member(_tg):
            return {"role": "premium", "blocked": 0,
                    "permissions": '{"features": ["giveaways"], "delay_minutes": 5}'}

        async def open_now(_tg, _member):
            return True, "", None

        role, perms = await bot_membership.resolve_member_access(
            5, admin_ids=set(), get_member=member, touch=self._noop, decide=open_now,
        )
        self.assertEqual(role, "premium")
        self.assertEqual(perms["features"], ["giveaways"])
        self.assertEqual(perms["delay_minutes"], 5)

    async def _noop(self, *args, **kwargs):
        return None

    async def _never(self, *args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("should not be reached")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_bot_membership.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pulse_desk.bot_membership'`

- [ ] **Step 3: Write the module**

Create `src/pulse_desk/bot_membership.py`:

```python
"""Who a Telegram user is to this bot: their role and their grants.

Lifted out of ``bot/service.py`` so that surfaces other than the bot's own
handlers — the Mini App — resolve access through exactly the same rules
instead of a second, drifting copy. Nothing here touches the bot client, so a
FastAPI router can import it freely.

``resolve_member_access`` takes its collaborators as arguments. That keeps the
decision logic testable without a database, and lets the bot pass its cached
schedule decision while the Mini App passes an uncached one.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from .access_control import resolve_access, window_from_row
from .app_ctx import ADMIN_ID, settings, state
from .bot_permissions import full_permissions, parse_permissions


def admin_chat_ids(admin_id: str, extra_chats: str) -> set[int]:
    """Owner chat ids: the configured admin plus any comma-separated extras.

    Junk entries are dropped rather than raising — a typo in the env var must
    not take the bot's whole permission check down.
    """
    ids: set[int] = set()
    for chunk in [admin_id, *(extra_chats or "").split(",")]:
        chunk = (chunk or "").strip()
        if not chunk:
            continue
        try:
            ids.add(int(chunk))
        except ValueError:
            continue
    return ids


def configured_admin_ids() -> set[int]:
    """`admin_chat_ids` fed from settings — the form callers normally want."""
    return admin_chat_ids(str(ADMIN_ID or ""), settings.bot_admin_chats or "")


async def access_decision(sender_id: int, member: dict) -> tuple[bool, str, Optional[datetime]]:
    """Cached schedule decision for a member: (allowed, reason, until_utc).

    The source of truth is computed here, not read from a stored flag, so access
    stays correct even when the scheduler loop is down. The cache only skips
    repeat SQL until the next window boundary.
    """
    from database import list_access_windows

    now = datetime.now(timezone.utc)
    cached = state.access_cache.get(sender_id)
    if cached and now < cached[1]:
        return cached[0], cached[2], cached[1]
    rows = await list_access_windows(sender_id)
    decision = resolve_access(member, [window_from_row(r) for r in rows], now)
    until = decision.until or (now + timedelta(minutes=1))
    state.access_cache[sender_id] = (decision.allowed, until, decision.reason)
    return decision.allowed, decision.reason, until


async def resolve_member_access(
    sender_id: int,
    *,
    admin_ids: Optional[set[int]] = None,
    get_member: Optional[Callable[[int], Awaitable[Optional[dict]]]] = None,
    touch: Optional[Callable[[int], Awaitable[Any]]] = None,
    decide: Optional[Callable[[int, dict], Awaitable[tuple]]] = None,
) -> tuple[Optional[str], dict]:
    """Resolve a Telegram user to ``(role, grants)``.

    ``role`` is 'admin', 'viewer'/'premium', or None for no access. Admins
    bypass both the schedule and the grants; members are gated by their access
    windows and carry the grants copied from the key they joined with.
    """
    if admin_ids is None:
        admin_ids = configured_admin_ids()
    if get_member is None or touch is None:
        from database import get_bot_member, touch_bot_member

        get_member = get_member or get_bot_member
        touch = touch or touch_bot_member
    decide = decide or access_decision

    if sender_id in admin_ids:
        return "admin", full_permissions()
    member = await get_member(sender_id)
    if not member or member.get("blocked"):
        return None, full_permissions()
    await touch(sender_id)
    allowed, _reason, _until = await decide(sender_id, member)
    if not allowed:
        return None, full_permissions()
    return member.get("role") or "viewer", parse_permissions(member.get("permissions"))
```

- [ ] **Step 4: Run the test**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_bot_membership.py -q`
Expected: PASS, 9 tests.

- [ ] **Step 5: Point `service.py` at the module**

In `src/pulse_desk/bot/service.py`, add to the import block near line 21:

```python
from ..bot_membership import (
    access_decision as _shared_access_decision,
    admin_chat_ids as _shared_admin_chat_ids,
    resolve_member_access as _shared_resolve_member_access,
)
```

Then replace the three closures at `src/pulse_desk/bot/service.py:214-271` with
delegating versions, keeping the same names so the ~40 call sites below them
need no edit:

```python
        def _bot_admin_chat_ids() -> set[int]:
            return _shared_admin_chat_ids(str(ADMIN_ID or ""), settings.bot_admin_chats or "")

        async def _access_decision(sender_id: int, member: dict):
            return await _shared_access_decision(sender_id, member)

        def _until_phrase(until: Optional[datetime]) -> str:
            if not until:
                return ""
            return f" до {until.astimezone().strftime('%d.%m %H:%M')}"

        async def resolve_member_access(sender_id: int) -> tuple[Optional[str], dict]:
            return await _shared_resolve_member_access(
                sender_id, admin_ids=_bot_admin_chat_ids()
            )

        async def bot_role(sender_id: int) -> Optional[str]:
            role, _perms = await resolve_member_access(sender_id)
            return role
```

- [ ] **Step 6: Verify nothing regressed**

Run: `.\.venv\Scripts\python.exe -c "import main"`
Expected: no output, exit 0 (every module imports, all routers register).

Run: `.\.venv\Scripts\python.exe -m pytest tests\ -q`
Expected: the whole suite passes.

- [ ] **Step 7: Commit**

```bash
git add src/pulse_desk/bot_membership.py src/pulse_desk/bot/service.py tests/test_bot_membership.py
git commit -m "refactor(bot): extract membership resolution into bot_membership"
```

---

### Task 2: Settings for the Mini App and the tunnel

**Files:**
- Modify: `src/pulse_desk/config.py:120-121`
- Modify: `.env.example`

- [ ] **Step 1: Add the settings fields**

In `src/pulse_desk/config.py`, directly after the `host` / `port` fields:

```python
    # --- Mini App -----------------------------------------------------------
    # The Mini App is served by a SECOND ASGI app on its own port, and only
    # that port is published through the tunnel. Tunnelling the dashboard's
    # port would expose every /api/* route and the SSE stream to the internet.
    miniapp_enabled: bool = Field(default=False, alias="MINIAPP_ENABLED")
    miniapp_port: int = Field(default=8010, alias="MINIAPP_PORT")
    cloudflared_bin: str = Field(default="cloudflared", alias="CLOUDFLARED_BIN")
```

- [ ] **Step 2: Document them in `.env.example`**

Append:

```env
# --- Telegram Mini App (bot panel with custom buttons) ---
# Off by default: turning it on starts a Cloudflare quick tunnel that publishes
# MINIAPP_PORT over HTTPS so Telegram can load the page from a phone. Only the
# Mini App is served on that port — never the dashboard.
MINIAPP_ENABLED=false
MINIAPP_PORT=8010
# Path or name of the cloudflared binary. Leave as-is when it is on PATH.
CLOUDFLARED_BIN=cloudflared
```

- [ ] **Step 3: Verify the settings load**

Run: `.\.venv\Scripts\python.exe -c "from pulse_desk.config import get_settings; s=get_settings(); print(s.miniapp_enabled, s.miniapp_port, s.cloudflared_bin)"`
Expected: `False 8010 cloudflared`

- [ ] **Step 4: Commit**

```bash
git add src/pulse_desk/config.py .env.example
git commit -m "feat(config): MINIAPP_ENABLED, MINIAPP_PORT, CLOUDFLARED_BIN"
```

---

### Task 3: `initData` verification

**Files:**
- Create: `src/pulse_desk/miniapp_auth.py`
- Test: `tests/test_miniapp_auth.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_miniapp_auth.py`:

```python
from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.miniapp_auth import InitDataError, data_check_string, sign, verify

TOKEN = "123456:AAHtesttokenvaluethatisnotreal"


def make_init_data(tg_id: int = 42, auth_date: int | None = None, token: str = TOKEN) -> str:
    """Build a correctly signed initData string, the way Telegram would."""
    payload = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAF_test",
        "user": json.dumps(
            {"id": tg_id, "first_name": "Ostap", "username": "bender"},
            separators=(",", ":"),
        ),
    }
    unsigned = urlencode(payload)
    return urlencode({**payload, "hash": sign(unsigned, token)})


class DataCheckStringTests(unittest.TestCase):
    def test_sorted_and_hash_removed(self):
        pairs = [("b", "2"), ("hash", "x"), ("a", "1")]
        self.assertEqual(data_check_string(pairs), "a=1\nb=2")


class VerifyTests(unittest.TestCase):
    def test_valid_init_data_returns_the_user(self):
        user = verify(make_init_data(), TOKEN)
        self.assertEqual(user.tg_id, 42)
        self.assertEqual(user.username, "bender")
        self.assertEqual(user.first_name, "Ostap")

    def test_tampered_value_is_rejected(self):
        forged = make_init_data().replace("Ostap", "Kisa")
        with self.assertRaises(InitDataError):
            verify(forged, TOKEN)

    def test_another_bot_token_is_rejected(self):
        with self.assertRaises(InitDataError):
            verify(make_init_data(token="999:otherbottoken"), TOKEN)

    def test_missing_hash_is_rejected(self):
        with self.assertRaises(InitDataError):
            verify(urlencode({"auth_date": "1", "user": "{}"}), TOKEN)

    def test_empty_string_is_rejected(self):
        with self.assertRaises(InitDataError):
            verify("", TOKEN)

    def test_stale_auth_date_is_rejected(self):
        old = int(time.time()) - 60 * 60 * 25
        with self.assertRaises(InitDataError):
            verify(make_init_data(auth_date=old), TOKEN)

    def test_fresh_auth_date_at_the_edge_is_accepted(self):
        edge = int(time.time()) - 60 * 60 * 23
        self.assertEqual(verify(make_init_data(auth_date=edge), TOKEN).tg_id, 42)

    def test_unparseable_user_json_is_rejected(self):
        payload = {"auth_date": str(int(time.time())), "user": "not-json"}
        raw = urlencode({**payload, "hash": sign(urlencode(payload), TOKEN)})
        with self.assertRaises(InitDataError):
            verify(raw, TOKEN)

    def test_empty_bot_token_is_rejected(self):
        with self.assertRaises(InitDataError):
            verify(make_init_data(), "")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_miniapp_auth.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pulse_desk.miniapp_auth'`

- [ ] **Step 3: Write the module**

Create `src/pulse_desk/miniapp_auth.py`:

```python
"""Telegram Mini App ``initData`` validation.

Pure: hand in the raw ``initData`` string and the bot token, get the caller's
Telegram identity back. No I/O and no global state, so it unit-tests without a
running bot or a database.

The algorithm is Telegram's own: drop ``hash`` from the query string, join the
remaining ``key=value`` pairs with newlines in key order, and HMAC-SHA256 that
under a secret which is itself ``HMAC_SHA256(b"WebAppData", bot_token)``.
Values are compared after percent-decoding, which is what ``parse_qsl`` gives.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import NamedTuple, Optional
from urllib.parse import parse_qsl

# Telegram recommends rejecting old initData; a day is long enough for a panel
# left open on a phone and short enough that a leaked string goes stale.
MAX_AGE_SECONDS = 24 * 60 * 60


class InitDataError(Exception):
    """initData was missing, malformed, forged or stale."""


class WebAppUser(NamedTuple):
    tg_id: int
    first_name: str
    username: str
    auth_date: int


def data_check_string(pairs: list[tuple[str, str]]) -> str:
    """Telegram's canonical form: ``k=v`` per line, key order, no ``hash``."""
    return "\n".join(f"{k}={v}" for k, v in sorted(pairs) if k != "hash")


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def sign(init_data: str, bot_token: str) -> str:
    """The hash Telegram would produce for `init_data`. Used by verify and tests."""
    pairs = parse_qsl(init_data, keep_blank_values=True)
    return hmac.new(
        _secret_key(bot_token), data_check_string(pairs).encode(), hashlib.sha256
    ).hexdigest()


def verify(
    init_data: str,
    bot_token: str,
    now: Optional[int] = None,
    max_age: int = MAX_AGE_SECONDS,
) -> WebAppUser:
    """Validate `init_data` and return the caller, or raise ``InitDataError``."""
    if not init_data or not bot_token:
        raise InitDataError("empty initData or bot token")
    pairs = parse_qsl(init_data, keep_blank_values=True)
    supplied = dict(pairs).get("hash", "")
    if not supplied:
        raise InitDataError("no hash in initData")
    expected = hmac.new(
        _secret_key(bot_token), data_check_string(pairs).encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise InitDataError("initData signature mismatch")

    fields = dict(pairs)
    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError as exc:
        raise InitDataError("auth_date is not an integer") from exc
    age = (now if now is not None else int(time.time())) - auth_date
    if auth_date <= 0 or age > max_age:
        raise InitDataError("initData is stale")

    try:
        user = json.loads(fields.get("user", ""))
        tg_id = int(user["id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise InitDataError("initData carries no usable user") from exc
    return WebAppUser(
        tg_id=tg_id,
        first_name=str(user.get("first_name") or ""),
        username=str(user.get("username") or ""),
        auth_date=auth_date,
    )
```

- [ ] **Step 4: Run the test**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_miniapp_auth.py -q`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/miniapp_auth.py tests/test_miniapp_auth.py
git commit -m "feat(miniapp): verify Telegram initData signatures"
```

---

### Task 4: The cloudflared quick tunnel

**Files:**
- Create: `src/pulse_desk/tunnel.py`
- Modify: `src/pulse_desk/runtime.py:20` (add `public_url`)
- Test: `tests/test_tunnel.py`

- [ ] **Step 1: Add `public_url` to `AppState`**

In `src/pulse_desk/runtime.py`, directly after `bot_username`:

```python
    # Public HTTPS origin of the Mini App, published by the ``tunnel`` job.
    # None whenever the tunnel is disabled, starting, or down — WebApp buttons
    # are then simply not rendered and the bot falls back to inline keyboards.
    public_url: Optional[str] = None
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_tunnel.py`:

```python
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.tunnel import extract_tunnel_url

# Verbatim shapes from `cloudflared tunnel --url`, which prints the address
# inside a box drawn with +---+ and | characters.
BANNER = (
    "2026-09-07T09:00:02Z INF +----------------------------------------+",
    "2026-09-07T09:00:02Z INF |  Your quick Tunnel has been created!    |",
    "2026-09-07T09:00:02Z INF |  https://busy-fox-quiet-lamp.trycloudflare.com  |",
    "2026-09-07T09:00:02Z INF +----------------------------------------+",
)


class ExtractTunnelUrlTests(unittest.TestCase):
    def test_pulls_the_url_out_of_the_banner(self):
        found = [u for u in (extract_tunnel_url(line) for line in BANNER) if u]
        self.assertEqual(found, ["https://busy-fox-quiet-lamp.trycloudflare.com"])

    def test_ignores_ordinary_log_lines(self):
        self.assertIsNone(extract_tunnel_url("2026-09-07T09:00:01Z INF Requesting new quick Tunnel"))

    def test_ignores_the_api_host(self):
        self.assertIsNone(extract_tunnel_url("INF Connecting to api.trycloudflare.com:443"))

    def test_ignores_a_plain_http_url(self):
        self.assertIsNone(extract_tunnel_url("INF proxying to http://127.0.0.1:8010"))

    def test_handles_digits_in_the_subdomain(self):
        line = "INF |  https://fox-42-lamp-7.trycloudflare.com  |"
        self.assertEqual(extract_tunnel_url(line), "https://fox-42-lamp-7.trycloudflare.com")

    def test_empty_line(self):
        self.assertIsNone(extract_tunnel_url(""))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_tunnel.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pulse_desk.tunnel'`

- [ ] **Step 4: Write the module**

Create `src/pulse_desk/tunnel.py`:

```python
"""Publish the Mini App port over HTTPS with a Cloudflare quick tunnel.

Telegram loads a Mini App from the user's phone, so ``127.0.0.1`` is useless —
the page needs a public HTTPS origin. A quick tunnel gives one with no domain
and no account, at the cost of a fresh hostname on every start. That is fine
here because WebApp buttons are built at send time from ``state.public_url``
and never baked into a callback payload.

Only the Mini App port is published. A quick tunnel forwards a whole origin and
cannot be narrowed to a path, so pointing it at the dashboard's port would put
every ``/api/*`` route on the internet.

The job never raises into the bot: a missing binary or a dead tunnel just
leaves ``state.public_url`` empty, and the WebApp buttons disappear.
"""
from __future__ import annotations

import asyncio
import re
import shutil
from typing import Optional

from .app_ctx import logger, settings, state
from .common import record_app_event

# The banner line looks like:  INF |  https://busy-fox.trycloudflare.com  |
# `api.trycloudflare.com` is the control plane, not a tunnel, so the subdomain
# must not be that one.
_TUNNEL_URL = re.compile(r"https://(?!api\.)([a-z0-9]+(?:-[a-z0-9]+)*)\.trycloudflare\.com")

# A dead tunnel restarts through start_supervised, which does not back off on a
# clean return. Pace it here so a permanently failing binary cannot spin.
RESTART_DELAY_SECONDS = 10.0


def extract_tunnel_url(line: str) -> Optional[str]:
    """The quick-tunnel URL in `line`, or None when there is none."""
    match = _TUNNEL_URL.search(line or "")
    return match.group(0) if match else None


async def _publish(url: str) -> None:
    if url == state.public_url:
        return
    state.public_url = url
    logger.info("Mini App tunnel is up: %s", url)
    await record_app_event("INFO", "tunnel", "Mini App tunnel is up", {"url": url})


async def tunnel_loop() -> None:
    """Run one cloudflared process; return when it dies so the supervisor respawns.

    Started only when ``MINIAPP_ENABLED`` is true (see main.py), so there is no
    disabled-and-spinning case to guard against here.
    """
    binary = shutil.which(settings.cloudflared_bin) or settings.cloudflared_bin
    local = f"http://127.0.0.1:{settings.miniapp_port}"
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "tunnel", "--url", local, "--no-autoupdate",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        logger.warning("cloudflared could not be started (%s) — Mini App stays local", exc)
        await record_app_event(
            "WARNING", "tunnel", "cloudflared could not be started",
            {"binary": binary, "error": str(exc)},
        )
        await asyncio.sleep(RESTART_DELAY_SECONDS * 6)
        return

    logger.info("cloudflared started for %s (pid %s)", local, proc.pid)
    try:
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            url = extract_tunnel_url(raw.decode("utf-8", "replace"))
            if url:
                await _publish(url)
        await proc.wait()
    except asyncio.CancelledError:
        proc.terminate()
        raise
    finally:
        state.public_url = None
        if proc.returncode is None:
            proc.terminate()

    logger.warning("cloudflared exited with %s — Mini App buttons are hidden", proc.returncode)
    await record_app_event(
        "WARNING", "tunnel", "cloudflared exited", {"code": proc.returncode},
    )
    await asyncio.sleep(RESTART_DELAY_SECONDS)
```

- [ ] **Step 5: Run the test**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_tunnel.py -q`
Expected: PASS, 6 tests.

- [ ] **Step 6: Commit**

```bash
git add src/pulse_desk/tunnel.py src/pulse_desk/runtime.py tests/test_tunnel.py
git commit -m "feat(miniapp): supervise a cloudflared quick tunnel"
```

---

### Task 5: WebApp button helper

**Files:**
- Modify: `src/pulse_desk/bot/keyboards.py:26` (imports) and end of file
- Test: `tests/test_bot_keyboards.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bot_keyboards.py` (keep the file's existing imports; add
`webapp_row` to the `from pulse_desk.bot.keyboards import ...` list):

```python
class WebappRowTests(unittest.TestCase):
    def setUp(self):
        from pulse_desk.app_ctx import state

        self.state = state
        self.addCleanup(setattr, state, "public_url", None)

    def test_no_tunnel_means_no_row(self):
        self.state.public_url = None
        self.assertEqual(webapp_row("Панель", "/app"), [])

    def test_empty_url_means_no_row(self):
        self.state.public_url = ""
        self.assertEqual(webapp_row("Панель", "/app"), [])

    def test_row_carries_the_full_url(self):
        self.state.public_url = "https://fox.trycloudflare.com"
        row = webapp_row("Панель", "/app")
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0].url, "https://fox.trycloudflare.com/app")
        self.assertEqual(row[0].text, "Панель")

    def test_trailing_slash_is_not_doubled(self):
        self.state.public_url = "https://fox.trycloudflare.com/"
        self.assertEqual(webapp_row("Панель", "/app")[0].url,
                         "https://fox.trycloudflare.com/app")

    def test_button_is_an_inline_web_view(self):
        from telethon.tl.custom.button import Button as TButton
        from telethon.tl.types import KeyboardButtonWebView

        self.state.public_url = "https://fox.trycloudflare.com"
        button = webapp_row("Панель", "/app")[0]
        self.assertIsInstance(button, KeyboardButtonWebView)
        self.assertTrue(TButton._is_inline(button))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_bot_keyboards.py -k Webapp -q`
Expected: FAIL — `ImportError: cannot import name 'webapp_row'`

- [ ] **Step 3: Implement the helper**

In `src/pulse_desk/bot/keyboards.py`, add to the imports:

```python
from telethon.tl.types import KeyboardButtonWebView

from ..app_ctx import state
```

and add the helper right after `back_home()`:

```python
def webapp_row(label: str, path: str = "/app") -> list[Button]:
    """A one-button row opening the Mini App, or an empty row when it is down.

    The URL is read at send time rather than cached, because a quick tunnel
    hands out a new hostname on every restart. With no tunnel the row is empty
    and the caller's keyboard is simply one row shorter — the inline UI below
    it stays fully usable, which is the whole point of the fallback.

    Telethon 1.43 counts ``KeyboardButtonWebView`` among its inline button
    types, so the raw TL object goes straight into ``buttons=``.
    """
    base = (state.public_url or "").rstrip("/")
    if not base:
        return []
    return [KeyboardButtonWebView(text=label, url=f"{base}{path}")]
```

- [ ] **Step 4: Run the test**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_bot_keyboards.py -q`
Expected: PASS — the new 5 tests plus every pre-existing keyboard test.

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/bot/keyboards.py tests/test_bot_keyboards.py
git commit -m "feat(bot): webapp_row helper for Mini App buttons"
```

---

### Task 6: Mini App JSON router

**Files:**
- Create: `routers/miniapp.py`

- [ ] **Step 1: Write the router**

Create `routers/miniapp.py`:

```python
"""The Mini App: its page and the JSON it reads.

Mounted on a SEPARATE ASGI app (see src/pulse_desk/miniapp_server.py), because
the tunnel that publishes it forwards a whole origin. Nothing else lives on
that port.

Every JSON route authenticates the caller by verifying the ``initData`` that
Telegram handed the page, then resolves role and grants through
``bot_membership`` — the same rules the bot's own handlers use, so a key that
cannot see giveaways in the bot cannot see them here either.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse

import database
from pulse_desk.app_ctx import BOT_TOKEN, settings
from pulse_desk.bot_membership import resolve_member_access
from pulse_desk.bot_permissions import account_mentioned, accounts_allowed, has_feature
from pulse_desk.converter import (
    CRYPTO,
    FIAT,
    convert,
    fmt_amount,
    fmt_money,
    snapshot_time,
)
from pulse_desk.miniapp_auth import InitDataError, verify

router = APIRouter()

APP_DIR = Path(settings.static_dir) / "app"
FEED_PAGE_SIZE = 8


class Caller:
    """The authenticated Mini App user: their Telegram id, role and grants."""

    def __init__(self, tg_id: int, role: str, perms: dict, name: str):
        self.tg_id = tg_id
        self.role = role
        self.perms = perms
        self.name = name

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def may(self, feature: str) -> bool:
        return self.is_admin or has_feature(self.perms, feature)

    def require(self, feature: str) -> None:
        if not self.may(feature):
            raise HTTPException(status_code=403, detail="Этот раздел вам не открыт")


async def current_caller(
    x_telegram_init_data: Optional[str] = Header(default=None),
) -> Caller:
    """Resolve the caller from the initData header, or refuse."""
    try:
        user = verify(x_telegram_init_data or "", BOT_TOKEN)
    except InitDataError as exc:
        raise HTTPException(status_code=401, detail="Откройте панель заново из бота") from exc
    role, perms = await resolve_member_access(user.tg_id)
    if role is None:
        raise HTTPException(status_code=403, detail="Доступ к боту закрыт")
    return Caller(user.tg_id, role, perms, user.first_name or user.username)


def visible_accounts(caller: Caller) -> list[str]:
    """Tracked usernames this caller may see, '@' stripped.

    Mirrors ``giveaway_accounts`` in bot/service.py so the Mini App and the bot
    agree on the account list, including its order.
    """
    from pulse_desk.app_ctx import state

    names = [name.lstrip("@") for name in (state.ping_usernames or [])]
    whitelist = {n.lower() for n in (caller.perms.get("accounts") or [])}
    return [n for n in names if not whitelist or n.lower() in whitelist]


@router.get("/")
@router.get("/app")
async def app_page() -> FileResponse:
    """The Mini App shell. Unauthenticated on purpose: the HTML holds no data."""
    return FileResponse(APP_DIR / "index.html")


@router.get("/api/app/home")
async def home(caller: Caller = Depends(current_caller)) -> dict:
    """Counters for the home cards, plus which cards the caller may open."""
    board = await database.get_giveaway_board(limit=400)
    need = [
        row for row in (board.get("buckets") or {}).get("need_action") or []
        if accounts_allowed(caller.perms, row.get("mentions"))
    ]
    return {
        "name": caller.name,
        "role": caller.role,
        "sections": {
            "giveaways": caller.may("giveaways"),
            "market": caller.may("market"),
        },
        "counters": {
            "giveaways": len(need),
            "wins": len([r for r in need if r.get("is_win")]),
        },
    }


def _feed_row(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "chat": row.get("chat") or "?",
        "detected_at": row.get("detected_at"),
        "date": row.get("date"),
        "is_win": bool(row.get("is_win")),
        "deleted": bool(row.get("deleted_at")),
        "priority": row.get("priority_label"),
        "mentions": row.get("mentions"),
        "link": row.get("link"),
    }


@router.get("/api/app/giveaways")
async def giveaways(
    caller: Caller = Depends(current_caller),
    sort: str = Query("d", pattern="^[dp]$"),
    wins: bool = Query(False),
    account: int = Query(-1),
    page: int = Query(1, ge=1),
) -> dict:
    """The giveaway feed, filtered exactly as the bot's ``gw:f:`` callback does.

    ``sort`` is 'd' for detection time or 'p' for the post's own date; ``account``
    is an index into ``visible_accounts`` (-1 for all), matching GiveawayFilter.
    """
    caller.require("giveaways")
    accounts = visible_accounts(caller)
    picked = accounts[account] if 0 <= account < len(accounts) else None
    narrowed = bool(caller.perms.get("accounts")) or wins or picked is not None
    window = FEED_PAGE_SIZE * page + 1
    board = await database.get_giveaway_board(
        limit=window * (8 if narrowed else 1),
        sort="date" if sort == "p" else "detected_at",
    )
    rows = [
        r for r in (board.get("buckets") or {}).get("need_action") or []
        if accounts_allowed(caller.perms, r.get("mentions"))
    ]
    if wins:
        rows = [r for r in rows if r.get("is_win")]
    if picked is not None:
        rows = [r for r in rows if account_mentioned(r.get("mentions"), picked)]
    start = FEED_PAGE_SIZE * (page - 1)
    window_rows = rows[start:start + FEED_PAGE_SIZE + 1]
    return {
        "items": [_feed_row(r) for r in window_rows[:FEED_PAGE_SIZE]],
        "has_more": len(window_rows) > FEED_PAGE_SIZE,
        "page": page,
        "total": len(rows),
        "accounts": accounts,
        "account": account,
        "sort": sort,
        "wins": wins,
    }


@router.get("/api/app/giveaways/{ping_id}")
async def giveaway_card(ping_id: int, caller: Caller = Depends(current_caller)) -> dict:
    """One giveaway, refused when it names an account the caller was not granted."""
    caller.require("giveaways")
    rows = await database.get_pings(limit=1, search=None)
    row = next((r for r in await database.get_pings(limit=2000) if int(r["id"]) == ping_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if not accounts_allowed(caller.perms, row.get("mentions")):
        raise HTTPException(status_code=403, detail="Эта запись не про ваш аккаунт")
    card = _feed_row(row)
    card["text"] = row.get("text") or ""
    card["sender"] = row.get("sender")
    return card


@router.get("/api/app/market")
async def market(caller: Caller = Depends(current_caller)) -> dict:
    """The newest stored market snapshot, formatted for the rates screen."""
    caller.require("market")
    history = await database.get_market_history(limit=1)
    snapshot = history[0] if history else None
    if not snapshot:
        return {"updated": "", "coins": [], "fiat": []}
    prices = snapshot.get("prices") or snapshot
    coins = []
    for code, (coingecko_id, emoji) in CRYPTO.items():
        quote = (prices.get(coingecko_id) or {}) if isinstance(prices, dict) else {}
        usd = quote.get("usd")
        if usd is None:
            continue
        coins.append({
            "code": code,
            "emoji": emoji,
            "usd": usd,
            "usd_text": fmt_money(float(usd), "USD"),
            "change": quote.get("usd_24h_change"),
        })
    fiat = []
    for code in ("UAH", "EUR", "PLN", "RUB"):
        value = convert(snapshot, 1.0, "USD", code)
        if value is not None:
            fiat.append({"code": code, "flag": FIAT[code][1], "text": fmt_money(value, code)})
    return {"updated": snapshot_time(snapshot), "coins": coins, "fiat": fiat}


@router.get("/api/app/convert")
async def convert_pair(
    caller: Caller = Depends(current_caller),
    amount: float = Query(1.0, gt=0),
    src: str = Query(..., min_length=2, max_length=8),
    dst: str = Query(..., min_length=2, max_length=8),
) -> dict:
    """Convert `amount` from `src` to `dst` off the newest snapshot — no network."""
    caller.require("market")
    history = await database.get_market_history(limit=1)
    snapshot = history[0] if history else None
    src_code, dst_code = src.upper(), dst.upper()
    value = convert(snapshot, amount, src_code, dst_code) if snapshot else None
    if value is None:
        raise HTTPException(status_code=422, detail="Такую пару пока не посчитать")
    return {
        "amount": amount,
        "src": src_code,
        "dst": dst_code,
        "value": value,
        "from_text": fmt_amount(amount, src_code),
        "to_text": fmt_money(value, dst_code),
        "updated": snapshot_time(snapshot),
    }
```

- [ ] **Step 2: Simplify the card lookup**

The draft above fetches the whole ping table to find one row. Replace the body
of `giveaway_card` with a direct read. First check whether `database` exposes a
single-ping getter:

Run: `.\.venv\Scripts\python.exe -c "import database; print(hasattr(database,'get_ping'))"`

If it prints `True`, use it:

```python
    row = await database.get_ping(ping_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
```

If it prints `False`, add one to `database/pings.py` and re-export it from
`database/__init__.py`:

```python
async def get_ping(ping_id: int) -> Optional[dict[str, Any]]:
    """One ping by id, or None. Used by the Mini App card and the bot drilldown."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM pings WHERE id = ?", (ping_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
```

Add `get_ping` to the `from .pings import (...)` list in `database/__init__.py`.
Then delete the two `get_pings` lines from `giveaway_card` and use the version
above.

- [ ] **Step 3: Verify the module imports**

Run: `.\.venv\Scripts\python.exe -c "import routers.miniapp; print(len(routers.miniapp.router.routes))"`
Expected: `7`

- [ ] **Step 4: Commit**

```bash
git add routers/miniapp.py database/pings.py database/__init__.py
git commit -m "feat(miniapp): JSON endpoints for home, giveaways and rates"
```

---

### Task 7: The second ASGI app

**Files:**
- Create: `src/pulse_desk/miniapp_server.py`
- Modify: `main.py:95-110` (lifespan)

- [ ] **Step 1: Write the server module**

Create `src/pulse_desk/miniapp_server.py`:

```python
"""A second ASGI app carrying only the Mini App.

The tunnel forwards a whole origin, so whatever runs on the published port is
on the internet. Keeping the Mini App on its own port means the dashboard, the
SSE stream and every token-authenticated ``/api/*`` route stay bound to
localhost where they were.

The server runs as an asyncio task inside the main process — same event loop,
same database connections, same ``state`` — so nothing needs to be shared
across processes.
"""
from __future__ import annotations

import asyncio

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from .app_ctx import logger, settings


def build_miniapp() -> FastAPI:
    """The Mini App ASGI app: its router plus its own static mount."""
    from routers import miniapp as miniapp_router

    app = FastAPI(title="Pulse Desk Mini App", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.mount(
        "/app-static",
        StaticFiles(directory=str(settings.static_dir / "app")),
        name="app-static",
    )
    app.include_router(miniapp_router.router)
    return app


async def serve_miniapp() -> None:
    """Run the Mini App server until the process shuts down."""
    config = uvicorn.Config(
        build_miniapp(),
        host="127.0.0.1",
        port=settings.miniapp_port,
        access_log=False,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    logger.info("Mini App server listening on 127.0.0.1:%s", settings.miniapp_port)
    try:
        await server.serve()
    except asyncio.CancelledError:
        server.should_exit = True
        raise
```

- [ ] **Step 2: Start both jobs from the lifespan**

In `main.py`, add to the imports near the other `pulse_desk` imports:

```python
from pulse_desk.miniapp_server import serve_miniapp
from pulse_desk.tunnel import tunnel_loop
```

and in the lifespan, right after the
`start_supervised("access-scheduler", ...)` line:

```python
    if settings.miniapp_enabled:
        start_supervised("miniapp-server", serve_miniapp, backoff_base=5.0, backoff_max=120.0)
        start_supervised("tunnel", tunnel_loop, backoff_base=10.0, backoff_max=600.0)
```

The `if` matters: `start_supervised` restarts a job that returns normally, so a
disabled tunnel job would spin.

- [ ] **Step 3: Verify everything imports and registers**

Run: `.\.venv\Scripts\python.exe -c "import main"`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add src/pulse_desk/miniapp_server.py main.py
git commit -m "feat(miniapp): serve the Mini App on its own port"
```

---

### Task 8: The Mini App UI

**Files:**
- Create: `static/app/index.html`, `static/app/app.css`, `static/app/icons.js`, `static/app/app.js`

- [ ] **Step 1: Write the icon map**

Create `static/app/icons.js`:

```javascript
// Line-art icons for the Mini App, drawn on a 24x24 grid.
// Stroke-only and `currentColor`, so one icon serves every state and theme —
// the .webp emoji tiles in assets/bot/emoji are raster, fixed-colour and cannot.
// Silhouettes follow the same shapes as scripts/generate_bot_emoji.py so the
// panel and the bot's card emoji read as one set.
const ICONS = {
  gift: 'M3 11h18v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1zM2 7h20v4H2zM12 7v14M12 7C12 7 9.5 3 7.5 3A2.5 2.5 0 0 0 7.5 8M12 7c0 0 2.5-4 4.5-4a2.5 2.5 0 0 1 0 5',
  trophy: 'M7 4h10v5a5 5 0 0 1-10 0zM7 5H4v2a3 3 0 0 0 3 3M17 5h3v2a3 3 0 0 1-3 3M9 20h6M12 14v6',
  chart: 'M3 21h18M6 17v-5M11 17V7M16 17v-8M21 17v-3',
  coins: 'M3 7c0-1.7 3.1-3 7-3s7 1.3 7 3-3.1 3-7 3-7-1.3-7-3zM3 7v5c0 1.7 3.1 3 7 3M17 7v4M21 13c0 1.7-3.1 3-7 3s-7-1.3-7-3 3.1-3 7-3 7 1.3 7 3zM7 13v4c0 1.7 3.1 3 7 3s7-1.3 7-3v-4',
  swap: 'M4 8h13l-3-3M20 16H7l3 3',
  bell: 'M6 9a6 6 0 1 1 12 0c0 4 2 5 2 5H4s2-1 2-5zM10 19a2 2 0 0 0 4 0',
  menu: 'M4 7h16M4 12h16M4 17h10',
  clock: 'M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18zM12 7v5l3 2',
  back: 'M15 5l-7 7 7 7',
  spark: 'M12 3l2.2 5.9L20 11l-5.8 2.1L12 19l-2.2-5.9L4 11l5.8-2.1z',
};

function icon(name, size) {
  const path = ICONS[name] || ICONS.spark;
  return `<svg class="ic" viewBox="0 0 24 24" width="${size || 22}" height="${size || 22}"
    fill="none" stroke="currentColor" stroke-width="1.6"
    stroke-linecap="round" stroke-linejoin="round"><path d="${path}"/></svg>`;
}
```

- [ ] **Step 2: Check the JS parses**

Run: `node --check static/app/icons.js`
Expected: no output, exit 0.

- [ ] **Step 3: Write the stylesheet**

Create `static/app/app.css`:

```css
/* Aperture Mini App — citron on slate, the palette the bot's emoji tiles use
   (see scripts/generate_bot_emoji.py). Dark-first on purpose: the identity
   should survive a client set to the light theme. */
:root {
  --ground: #0f1113;
  --card: #1e2226;
  --card-hi: #262b30;
  --keyline: rgba(168, 216, 31, 0.28);
  --accent: #cdff4a;
  --amber: #ffbe46;
  --red: #ff6060;
  --text: #e7ecea;
  --dim: #8d9895;
  --radius: 16px;
  --bar-h: 66px;
}

* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }

body {
  margin: 0;
  padding: 14px 14px calc(var(--bar-h) + 20px);
  background: var(--ground);
  color: var(--text);
  font: 15px/1.45 -apple-system, "Segoe UI", Roboto, system-ui, sans-serif;
}

.head { display: flex; align-items: baseline; gap: 8px; margin: 4px 2px 14px; }
.head h1 { font-size: 17px; letter-spacing: .04em; margin: 0; text-transform: uppercase; }
.head .sub { color: var(--dim); font-size: 12px; }

/* Cards: a top light gradient plus an inner keyline is what reads as a raised
   panel without a real shadow, which would smear on an OLED phone. */
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.card {
  grid-column: span 1;
  display: flex; align-items: center; gap: 10px;
  padding: 16px 14px;
  border: 1px solid var(--keyline);
  border-radius: var(--radius);
  background:
    linear-gradient(180deg, rgba(255,255,255,.06), rgba(255,255,255,0) 60%),
    var(--card);
  color: var(--accent);
  font-weight: 600;
  cursor: pointer;
  transition: transform .12s ease, background .12s ease;
}
.card.wide { grid-column: span 2; }
.card:active { transform: scale(.98); background: var(--card-hi); }
.card .label { color: var(--text); }
.card .count { margin-left: auto; color: var(--dim); font-variant-numeric: tabular-nums; }
.ic { flex: none; }

.rows { display: flex; flex-direction: column; gap: 8px; }
.row {
  display: flex; align-items: center; gap: 10px;
  padding: 12px 14px;
  border: 1px solid rgba(255,255,255,.07);
  border-radius: 13px;
  background: var(--card);
  cursor: pointer;
}
.row:active { background: var(--card-hi); }
.row .when { color: var(--dim); font-size: 12px; font-variant-numeric: tabular-nums; }
.row .chat { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row.win { border-color: var(--keyline); }
.row.win .chat { color: var(--accent); }

.chips { display: flex; gap: 8px; flex-wrap: wrap; margin: 12px 0; }
.chip {
  padding: 7px 13px; border-radius: 999px; cursor: pointer;
  border: 1px solid rgba(255,255,255,.09);
  background: var(--card); color: var(--dim); font-size: 13px;
}
.chip.on { border-color: var(--keyline); color: var(--accent); }

.note { color: var(--dim); padding: 26px 6px; text-align: center; }
.err { color: var(--red); padding: 26px 6px; text-align: center; }

/* Bottom bar: fixed pills, the geometry of the reference screenshot. */
.bar {
  position: fixed; left: 0; right: 0; bottom: 0;
  display: flex; gap: 8px; padding: 10px 12px calc(10px + env(safe-area-inset-bottom));
  background: linear-gradient(180deg, rgba(15,17,19,.6), var(--ground) 40%);
  backdrop-filter: blur(12px);
}
.pill {
  flex: 1; display: flex; align-items: center; justify-content: center; gap: 7px;
  height: 46px; border-radius: 14px; cursor: pointer;
  border: 1px solid rgba(255,255,255,.08);
  background: linear-gradient(180deg, #34383c, #24282c);
  color: var(--dim); font-weight: 600; font-size: 14px;
}
.pill.on { color: var(--text); background: linear-gradient(180deg, #4a4f54, #34383c); }
.pill:active { transform: scale(.985); }

.conv { display: flex; gap: 8px; margin: 10px 0 4px; }
.conv input {
  flex: 1; min-width: 0; padding: 12px 14px; border-radius: 13px;
  border: 1px solid rgba(255,255,255,.09);
  background: var(--card); color: var(--text); font-size: 15px;
  font-variant-numeric: tabular-nums;
}
.conv input:focus { outline: none; border-color: var(--keyline); }
.out { padding: 16px 14px; border-radius: var(--radius);
       border: 1px solid var(--keyline); background: var(--card);
       font-size: 18px; font-variant-numeric: tabular-nums; }
.out .to { color: var(--accent); font-weight: 600; }
```

- [ ] **Step 4: Write the shell**

Create `static/app/index.html`:

```html
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Pulse Desk</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <link rel="stylesheet" href="/app-static/app.css">
</head>
<body>
  <div class="head"><h1 id="title">Pulse Desk</h1><span class="sub" id="sub"></span></div>
  <main id="screen"><div class="note">Загрузка…</div></main>
  <nav class="bar" id="bar"></nav>
  <script src="/app-static/icons.js"></script>
  <script src="/app-static/app.js"></script>
</body>
</html>
```

- [ ] **Step 5: Write the app script**

Create `static/app/app.js`:

```javascript
// Mini App screens. Classic script, one global scope, no build step — the same
// convention static/js/app-*.js follows.
const tg = window.Telegram ? window.Telegram.WebApp : null;
const screen = document.getElementById('screen');
const bar = document.getElementById('bar');
const sub = document.getElementById('sub');

let view = 'home';
let feed = { sort: 'd', wins: false, account: -1, page: 1 };
let home = null;

function tap() {
  if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('light');
}

// Every call carries initData; the server verifies it each time, so there is no
// session to expire halfway through a panel left open on a phone.
async function api(path) {
  const res = await fetch(path, {
    headers: { 'X-Telegram-Init-Data': (tg && tg.initData) || '' },
  });
  if (!res.ok) {
    let detail = 'Ошибка ' + res.status;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* keep default */ }
    throw new Error(detail);
  }
  return res.json();
}

function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso.replace(' ', 'T'));
  if (isNaN(d)) return '';
  return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function esc(text) {
  return String(text == null ? '' : text).replace(/[&<>"]/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
  });
}

function renderBar() {
  const tabs = [['home', 'menu', 'Меню'], ['giveaways', 'gift', 'Розыгрыши'], ['market', 'coins', 'Курсы']];
  bar.innerHTML = tabs.map(function (t) {
    return '<div class="pill ' + (view === t[0] ? 'on' : '') + '" data-go="' + t[0] + '">'
      + icon(t[1], 19) + '<span>' + t[2] + '</span></div>';
  }).join('');
}

async function showHome() {
  view = 'home';
  home = await api('/api/app/home');
  sub.textContent = home.name || '';
  const cards = [];
  if (home.sections.giveaways) {
    cards.push(card('wide', 'trophy', 'Победы', home.counters.wins, 'giveaways-wins'));
    cards.push(card('', 'gift', 'Розыгрыши', home.counters.giveaways, 'giveaways'));
  }
  if (home.sections.market) {
    cards.push(card('', 'coins', 'Курсы', '', 'market'));
    cards.push(card('', 'swap', 'Конвертер', '', 'convert'));
  }
  screen.innerHTML = cards.length
    ? '<div class="grid">' + cards.join('') + '</div>'
    : '<div class="note">Ваш ключ пока не открывает ни одного раздела.</div>';
  renderBar();
}

function card(extra, ic, label, count, go) {
  return '<div class="card ' + extra + '" data-go="' + go + '">' + icon(ic, 22)
    + '<span class="label">' + label + '</span>'
    + '<span class="count">' + (count === '' ? '' : count) + '</span></div>';
}

async function showGiveaways() {
  view = 'giveaways';
  const q = '/api/app/giveaways?sort=' + feed.sort + '&wins=' + feed.wins
    + '&account=' + feed.account + '&page=' + feed.page;
  const data = await api(q);
  sub.textContent = data.total + ' в очереди';
  const chips = [
    chip('Все', !feed.wins, 'f-all'),
    chip('Победы', feed.wins, 'f-wins'),
    chip(feed.sort === 'd' ? 'По находке' : 'По дате поста', true, 'f-sort'),
  ].join('');
  const rows = data.items.map(function (it) {
    return '<div class="row ' + (it.is_win ? 'win' : '') + '" data-open="' + it.id + '">'
      + icon(it.is_win ? 'trophy' : 'gift', 18)
      + '<span class="chat">' + esc(it.chat) + '</span>'
      + '<span class="when">' + fmtTime(feed.sort === 'p' ? it.date : it.detected_at) + '</span></div>';
  }).join('');
  const pager = (feed.page > 1 ? chip('← Новее', false, 'p-prev') : '')
    + (data.has_more ? chip('Старее →', false, 'p-next') : '');
  screen.innerHTML = '<div class="chips">' + chips + '</div>'
    + (rows ? '<div class="rows">' + rows + '</div>' : '<div class="note">Пусто.</div>')
    + '<div class="chips">' + pager + '</div>';
  renderBar();
}

function chip(label, on, action) {
  return '<div class="chip ' + (on ? 'on' : '') + '" data-act="' + action + '">' + label + '</div>';
}

async function showCard(id) {
  view = 'card';
  const it = await api('/api/app/giveaways/' + id);
  sub.textContent = it.chat;
  screen.innerHTML = '<div class="out">' + (it.is_win ? icon('trophy', 20) + ' Победа' : icon('gift', 20) + ' Розыгрыш')
    + '</div><div class="rows" style="margin-top:10px">'
    + '<div class="row"><span class="chat">' + esc(it.text.slice(0, 600) || '—') + '</span></div>'
    + (it.link ? '<div class="row" data-link="' + esc(it.link) + '">' + icon('spark', 18)
        + '<span class="chat">Открыть в Telegram</span></div>' : '')
    + '</div><div class="chips">' + chip('← Назад', false, 'back') + '</div>';
  renderBar();
}

async function showMarket() {
  view = 'market';
  const data = await api('/api/app/market');
  sub.textContent = data.updated || '';
  const rows = data.coins.map(function (c) {
    const change = c.change == null ? '' : (c.change >= 0 ? '+' : '') + c.change.toFixed(1) + '%';
    return '<div class="row"><span class="chat">' + c.emoji + ' ' + c.code + '</span>'
      + '<span class="when">' + change + '</span>'
      + '<span style="font-variant-numeric:tabular-nums">' + esc(c.usd_text) + '</span></div>';
  }).join('');
  const fiat = data.fiat.map(function (f) {
    return '<div class="row"><span class="chat">' + f.flag + ' 1 USD</span>'
      + '<span style="font-variant-numeric:tabular-nums">' + esc(f.text) + '</span></div>';
  }).join('');
  screen.innerHTML = (rows ? '<div class="rows">' + rows + '</div>' : '<div class="note">Нет данных.</div>')
    + (fiat ? '<div class="chips"></div><div class="rows">' + fiat + '</div>' : '')
    + '<div class="chips">' + chip('Конвертер', false, 'to-convert') + '</div>';
  renderBar();
}

function showConvert(result) {
  view = 'convert';
  sub.textContent = '';
  screen.innerHTML = '<div class="conv">'
    + '<input id="amt" inputmode="decimal" value="100" aria-label="Сумма">'
    + '<input id="src" value="USD" aria-label="Из">'
    + '<input id="dst" value="UAH" aria-label="В">'
    + '</div><div class="chips">' + chip('Посчитать', true, 'do-convert') + '</div>'
    + '<div class="out" id="out">' + (result || 'Введите сумму и пару.') + '</div>';
  renderBar();
}

async function runConvert() {
  const amount = parseFloat(document.getElementById('amt').value.replace(',', '.'));
  const src = document.getElementById('src').value.trim();
  const dst = document.getElementById('dst').value.trim();
  const out = document.getElementById('out');
  if (!amount || amount <= 0) { out.textContent = 'Сумма должна быть больше нуля.'; return; }
  try {
    const r = await api('/api/app/convert?amount=' + amount + '&src=' + encodeURIComponent(src)
      + '&dst=' + encodeURIComponent(dst));
    out.innerHTML = esc(r.from_text) + ' = <span class="to">' + esc(r.to_text) + '</span>';
  } catch (e) {
    out.textContent = e.message;
  }
}

const ROUTES = {
  home: showHome,
  giveaways: function () { feed.wins = false; feed.page = 1; return showGiveaways(); },
  'giveaways-wins': function () { feed.wins = true; feed.page = 1; return showGiveaways(); },
  market: showMarket,
  convert: function () { showConvert(); },
};

async function go(name) {
  tap();
  try {
    await ROUTES[name]();
  } catch (e) {
    screen.innerHTML = '<div class="err">' + esc(e.message) + '</div>';
  }
}

document.addEventListener('click', function (event) {
  const goTarget = event.target.closest('[data-go]');
  if (goTarget) { go(goTarget.dataset.go); return; }

  const open = event.target.closest('[data-open]');
  if (open) { tap(); showCard(open.dataset.open).catch(function (e) {
    screen.innerHTML = '<div class="err">' + esc(e.message) + '</div>'; }); return; }

  const link = event.target.closest('[data-link]');
  if (link && tg) { tg.openTelegramLink(link.dataset.link); return; }

  const act = event.target.closest('[data-act]');
  if (!act) return;
  tap();
  const action = act.dataset.act;
  if (action === 'f-all') { feed.wins = false; feed.page = 1; showGiveaways(); }
  else if (action === 'f-wins') { feed.wins = true; feed.page = 1; showGiveaways(); }
  else if (action === 'f-sort') { feed.sort = feed.sort === 'd' ? 'p' : 'd'; feed.page = 1; showGiveaways(); }
  else if (action === 'p-prev') { feed.page = Math.max(1, feed.page - 1); showGiveaways(); }
  else if (action === 'p-next') { feed.page += 1; showGiveaways(); }
  else if (action === 'back') { showGiveaways(); }
  else if (action === 'to-convert') { showConvert(); }
  else if (action === 'do-convert') { runConvert(); }
});

if (tg) { tg.ready(); tg.expand(); }
go('home');
```

- [ ] **Step 6: Check both scripts parse**

Run: `node --check static/app/app.js`
Expected: no output, exit 0.

Run: `node --check static/app/icons.js`
Expected: no output, exit 0.

- [ ] **Step 7: Commit**

```bash
git add static/app
git commit -m "feat(miniapp): Aperture card and pill UI"
```

---

### Task 9: Put the WebApp button in the bot

**Files:**
- Modify: `src/pulse_desk/bot/views.py:178-198`
- Test: `tests/test_bot_views.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bot_views.py` (add `main_menu_buttons` to the existing
`from pulse_desk.bot.views import ...` list if it is not already there):

```python
class MainMenuWebappTests(unittest.TestCase):
    def setUp(self):
        from pulse_desk.app_ctx import state

        self.state = state
        self.addCleanup(setattr, state, "public_url", None)

    def test_no_tunnel_leaves_the_menu_unchanged(self):
        self.state.public_url = None
        rows = main_menu_buttons("admin")
        self.assertTrue(all(getattr(b, "url", None) is None for row in rows for b in row))

    def test_tunnel_adds_one_panel_row_on_top(self):
        self.state.public_url = "https://fox.trycloudflare.com"
        rows = main_menu_buttons("admin")
        self.assertEqual(len(rows[0]), 1)
        self.assertEqual(rows[0][0].url, "https://fox.trycloudflare.com/app")

    def test_panel_row_does_not_replace_the_inline_sections(self):
        self.state.public_url = "https://fox.trycloudflare.com"
        with_tunnel = main_menu_buttons("admin")
        self.state.public_url = None
        without = main_menu_buttons("admin")
        self.assertEqual(len(with_tunnel), len(without) + 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_bot_views.py -k Webapp -q`
Expected: FAIL — `IndexError` or an assertion error, because no panel row exists.

- [ ] **Step 3: Add the row**

In `src/pulse_desk/bot/views.py`, import the helper at the top of the file:

```python
from .keyboards import webapp_row
```

If that import is circular (keyboards.py already imports from views.py), import
it lazily inside the function instead — put this as the first line of
`main_menu_buttons`'s body:

```python
    from .keyboards import webapp_row
```

Then change the end of `main_menu_buttons` from:

```python
    rows.append([Button.inline("❓ Помощь", b"menu_help")])
    return rows
```

to:

```python
    rows.append([Button.inline("❓ Помощь", b"menu_help")])
    # The panel sits on top when a tunnel is up; with none, `webapp_row` returns
    # an empty list and the menu is exactly what it has always been.
    panel = webapp_row("🛰 Панель")
    return [panel] + rows if panel else rows
```

- [ ] **Step 4: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_bot_views.py tests\test_bot_keyboards.py -q`
Expected: PASS.

- [ ] **Step 5: Verify the whole suite and the import graph**

Run: `.\.venv\Scripts\python.exe -m pytest tests\ -q`
Expected: all pass.

Run: `.\.venv\Scripts\python.exe -c "import main"`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/pulse_desk/bot/views.py tests/test_bot_views.py
git commit -m "feat(bot): open the Mini App from the home screen"
```

---

### Task 10: Manual verification against the real bot

**Files:** none — this is a run-through.

- [ ] **Step 1: Enable the Mini App**

Set in `.env`:

```env
MINIAPP_ENABLED=true
```

- [ ] **Step 2: Restart the app**

Run: `.\restart_app.ps1`

(Do not start a second process on :8000 — restart through this script, which
detaches via WMI so the process survives.)

- [ ] **Step 3: Confirm the tunnel came up**

Run: `.\.venv\Scripts\python.exe -c "import re,pathlib; print([l for l in pathlib.Path('app.log').read_text(encoding='utf-8',errors='replace').splitlines() if 'tunnel' in l.lower()][-5:])"`
Expected: a line containing `Mini App tunnel is up: https://….trycloudflare.com`

- [ ] **Step 4: Check the panel opens**

In Telegram, send `/start` to the bot. The home screen should now carry a
`🛰 Панель` button above the existing sections. Tapping it opens the Mini App:
dark cards, citron line-art icons, three pills at the bottom.

Verify: Home counters match the bot's `🎁 Розыгрыши` header, the giveaway feed
paginates, and the converter returns a number for `100 USD → UAH`.

- [ ] **Step 5: Check the fallback**

Set `MINIAPP_ENABLED=false`, restart, send `/start` again. The `🛰 Панель`
button must be gone and every inline keyboard must behave exactly as before.

- [ ] **Step 6: Commit any fixes found**

```bash
git add -A
git commit -m "fix(miniapp): corrections from the first live run"
```

---

### Task 11: Documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Document the modules**

In `CLAUDE.md`, under the `src/pulse_desk/` layer breakdown, add after the
`bot_permissions.py` entry:

```
  bot_membership.py — Telegram user -> (role, grants). Lifted out of
                      bot/service.py so the Mini App resolves access through
                      the same rules instead of a second copy. Collaborators
                      are arguments, so the decision logic unit-tests with no
                      database
  miniapp_auth.py   — Telegram Mini App initData validation (pure): drop
                      `hash`, join the rest as sorted `k=v` lines, HMAC-SHA256
                      under HMAC(b"WebAppData", bot_token), reject anything
                      older than 24 h
  miniapp_server.py — The Mini App's own ASGI app + uvicorn task. Separate from
                      the dashboard because the tunnel forwards a whole origin:
                      whatever shares that port is on the internet
  tunnel.py         — cloudflared quick tunnel (`tunnel` job). Owns
                      `state.public_url`. A new hostname per restart is fine —
                      WebApp buttons are built at send time. Missing binary or
                      dead tunnel just empties `public_url`, the buttons vanish
                      and the bot falls back to its inline keyboards
```

- [ ] **Step 2: Document the jobs**

Add two rows to the background-jobs table:

```
| `miniapp-server` | Serves the Mini App on `MINIAPP_PORT`, alone on that port | `MINIAPP_ENABLED`, `MINIAPP_PORT` |
| `tunnel` | Keeps a cloudflared quick tunnel up and `state.public_url` current | `MINIAPP_ENABLED`, `CLOUDFLARED_BIN` |
```

- [ ] **Step 3: Record the keyboard limit and the way around it**

Under **Key conventions**, add:

```
- **Bot keyboards cannot carry custom emoji, ever.** Button text is a plain
  string with no `entities` in both MTProto and the Bot API, so the Aperture
  pack renders in card *text* only. Custom icons and custom button shapes live
  in the Mini App (`static/app/`), which is HTML. Do not try to solve this in
  `keyboards.py` again.
```

- [ ] **Step 4: Note the static layout**

Under the `static/` entry, add:

```
  static/app/ is the Telegram Mini App — its own shell, CSS, icons and script,
  served by miniapp_server.py on a different port. It shares nothing with the
  dashboard's app-*.js global scope.
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: record the Mini App, the tunnel and the keyboard limit"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| Second ASGI app on its own port | 7 |
| `tunnel.py` job + `state.public_url` | 4 |
| `miniapp_auth.py` initData verification | 3 |
| Role + grants reuse (`bot_membership`) | 1, 6 |
| `/api/app/*` endpoints | 6 |
| `static/app/` UI, palette, icons, bottom bar | 8 |
| `webapp_row` + home-screen wiring | 5, 9 |
| Settings + `.env.example` | 2 |
| Tests (auth, tunnel, keyboards, views, membership) | 1, 3, 4, 5, 9 |
| Error handling / fallback | 4 (tunnel), 5 (empty row), 6 (401/403), 10 (verified live) |
| Docs | 11 |

**Placeholders:** none — every code step carries its full body, and Task 6 Step 2
gives both branches of the one conditional (`database.get_ping` exists or not)
rather than deferring the decision.

**Type consistency:** `webapp_row(label, path)` is defined in Task 5 and called
in Task 9 with one argument, matching its `path: str = "/app"` default.
`resolve_member_access` keeps its `(role, perms)` tuple across Tasks 1 and 6.
`extract_tunnel_url` is defined in Task 4 and used only there. The `Caller`
class in Task 6 is the sole `Depends` target for every JSON route. The feed
query parameters (`sort`, `wins`, `account`, `page`) match the `feed` object in
`static/app/app.js`.
