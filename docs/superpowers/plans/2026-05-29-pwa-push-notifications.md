# PWA Push Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a new ping is found during a scan, push a Web Push notification to all subscribed browsers — without a Telegram bot, via the browser's native push channel.

**Architecture:** Generate a VAPID key pair on startup (stored in `settings` table). Add a `push_subscriptions` table. Add `/api/push/vapid-public-key`, `/api/push/subscribe`, `/api/push/unsubscribe` endpoints. Extend `save_ping` path in `main.py` to fan-out a push notification. Handle `push` event in the service worker.

**Tech Stack:** `pywebpush` Python library, Web Push API (browser), existing `sw.js` service worker, existing `settings` table for VAPID key storage.

---

### Task 1: Install dependency and VAPID key generation

**Files:**
- Modify: `requirements.txt`
- Modify: `pyproject.toml` (if exists from pyproject plan)
- Modify: `database.py` (new table)
- Create: `src/pulse_desk/push.py`

- [ ] **Step 1: Add `pywebpush` to requirements.txt**

In `requirements.txt`, add:
```
pywebpush==2.0.0
```

Install in your venv:
```bash
pip install pywebpush==2.0.0
```

If using pyproject.toml, also add `"pywebpush==2.0.0"` to `dependencies`.

- [ ] **Step 2: Write failing test**

In `tests/test_core.py`, add:

```python
def test_vapid_key_pair_generates(self):
    from pulse_desk.push import generate_vapid_keys
    keys = generate_vapid_keys()
    self.assertIn("private_key", keys)
    self.assertIn("public_key", keys)
    self.assertTrue(len(keys["public_key"]) > 20)
```

- [ ] **Step 3: Run test to confirm it fails**

```bash
python -m unittest tests.test_core.CoreParsingTests.test_vapid_key_pair_generates
```

Expected: ImportError

- [ ] **Step 4: Create `src/pulse_desk/push.py`**

```python
from __future__ import annotations

import json
import logging
from typing import Any

from py_vapid import Vapid

logger = logging.getLogger(__name__)


def generate_vapid_keys() -> dict[str, str]:
    vapid = Vapid()
    vapid.generate_keys()
    return {
        "private_key": vapid.private_pem().decode(),
        "public_key": vapid.public_key.public_bytes(
            encoding=__import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding", "PublicFormat"]).Encoding.X962,
            format=__import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding", "PublicFormat"]).PublicFormat.UncompressedPoint,
        ).hex(),  # returned as application server key (urlsafe b64 in endpoint)
    }


def vapid_public_key_bytes(private_pem: str) -> bytes:
    from py_vapid import Vapid
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    vapid = Vapid.from_pem(private_pem.encode())
    return vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)


async def send_push(
    subscription_info: dict[str, Any],
    payload: dict[str, Any],
    vapid_private_pem: str,
    vapid_claims: dict[str, str],
) -> bool:
    """Send a single Web Push message. Returns True on success."""
    try:
        from pywebpush import webpush, WebPushException
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=vapid_private_pem,
            vapid_claims=vapid_claims,
        )
        return True
    except Exception as exc:
        logger.warning("Push send failed: %s", exc)
        return False
```

- [ ] **Step 5: Run test — should pass now**

```bash
python -m unittest tests.test_core.CoreParsingTests.test_vapid_key_pair_generates
```

Expected: PASS

- [ ] **Step 6: Add `push_subscriptions` table in `database.py`**

In `init_db()`, after the `settings_history` table (or any convenient location after other tables):

```python
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT NOT NULL UNIQUE,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
```

- [ ] **Step 7: Add DB helper functions for subscriptions**

At the end of `database.py` (before backup helpers):

```python
async def save_push_subscription(endpoint: str, p256dh: str, auth: str) -> None:
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO push_subscriptions (endpoint, p256dh, auth, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth
            """,
            (endpoint, p256dh, auth, _now_iso()),
        )
        await db.commit()


async def delete_push_subscription(endpoint: str) -> None:
    async with _connect() as db:
        await db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        await db.commit()


async def get_push_subscriptions() -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT endpoint, p256dh, auth FROM push_subscriptions")).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 8: Commit**

```bash
git add src/pulse_desk/push.py database.py requirements.txt tests/test_core.py
git commit -m "feat: push notification DB table and VAPID key generation"
```

---

### Task 2: VAPID key lifecycle on startup + API endpoints

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add imports in `main.py`**

```python
from pulse_desk.push import generate_vapid_keys, send_push, vapid_public_key_bytes
from database import (
    # ... existing imports ...
    save_push_subscription,
    delete_push_subscription,
    get_push_subscriptions,
)
```

- [ ] **Step 2: Initialize VAPID keys in lifespan**

In `main.py`, inside the `lifespan` async context manager, after `await init_db()`:

```python
    # VAPID key lifecycle
    vapid_private_pem = await get_setting("vapid_private_pem", "")
    if not vapid_private_pem:
        vapid_keys = generate_vapid_keys()
        await set_setting("vapid_private_pem", vapid_keys["private_key"])
        vapid_private_pem = vapid_keys["private_key"]
        logger.info("Generated new VAPID key pair")
```

Store `vapid_private_pem` in a module-level variable or in `AppState` for use in endpoints. Simplest: add to AppState:

In `src/pulse_desk/runtime.py`, add field to `AppState`:
```python
    vapid_private_pem: str = ""
```

Then in lifespan: `state.vapid_private_pem = vapid_private_pem`

- [ ] **Step 3: Add push endpoints in `main.py`**

```python
import base64


@app.get("/api/push/vapid-public-key")
async def push_vapid_public_key():
    if not state.vapid_private_pem:
        raise HTTPException(status_code=503, detail="Push not initialised")
    pub_bytes = vapid_public_key_bytes(state.vapid_private_pem)
    # Return as URL-safe base64 (applicationServerKey format)
    return {"key": base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()}


@app.post("/api/push/subscribe")
async def push_subscribe(request: Request, token_valid: bool = Depends(require_viewer)):
    body = await request.json()
    endpoint = body.get("endpoint")
    keys = body.get("keys", {})
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        raise HTTPException(status_code=400, detail="endpoint, keys.p256dh and keys.auth required")
    await save_push_subscription(endpoint, keys["p256dh"], keys["auth"])
    return {"ok": True}


@app.delete("/api/push/subscribe")
async def push_unsubscribe(request: Request, token_valid: bool = Depends(require_viewer)):
    body = await request.json()
    endpoint = body.get("endpoint")
    if not endpoint:
        raise HTTPException(status_code=400, detail="endpoint required")
    await delete_push_subscription(endpoint)
    return {"ok": True}
```

- [ ] **Step 4: Trigger push on new ping**

In `main.py`, find the place where a new ping is detected and `enqueue_outbox_event` is called (search for `enqueue_outbox_event` or `save_ping` calls). After saving a ping, add:

```python
    # Fan out push notifications (fire-and-forget)
    if state.vapid_private_pem:
        asyncio.create_task(_fan_push_ping(ping_record))
```

Add the helper (module level in `main.py`):

```python
async def _fan_push_ping(ping: dict) -> None:
    subscriptions = await get_push_subscriptions()
    if not subscriptions:
        return
    payload = {
        "title": f"Ping: {ping.get('chat', '?')}",
        "body": (ping.get("text") or "")[:100],
        "url": ping.get("link") or "/",
        "tag": f"ping-{ping.get('id')}",
    }
    claims = {"sub": f"mailto:{settings.effective_admin_token[:8]}@pulse.local"}
    for sub in subscriptions:
        subscription_info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        await send_push(subscription_info, payload, state.vapid_private_pem, claims)
```

- [ ] **Step 5: Verify imports and startup**

```bash
python -c "import main; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add main.py src/pulse_desk/runtime.py
git commit -m "feat: VAPID key lifecycle, push subscribe/unsubscribe endpoints, fan-out on new ping"
```

---

### Task 3: Service worker push handler + frontend subscribe button

**Files:**
- Modify: `static/sw.js`
- Modify: `static/app.js`
- Modify: `static/index.html`

- [ ] **Step 1: Add push event handler in `static/sw.js`**

At the end of `static/sw.js`, add:

```javascript
self.addEventListener('push', event => {
    const data = event.data ? event.data.json() : {};
    event.waitUntil(
        self.registration.showNotification(data.title || 'Pulse Desk', {
            body: data.body || '',
            icon: '/favicon.svg',
            badge: '/favicon.svg',
            tag: data.tag || 'pulse-ping',
            data: { url: data.url || '/' },
        })
    );
});

self.addEventListener('notificationclick', event => {
    event.notification.close();
    const url = (event.notification.data || {}).url || '/';
    event.waitUntil(clients.openWindow(url));
});
```

- [ ] **Step 2: Add subscribe button in `static/index.html`**

In the Settings or header area:

```html
<button id="btn-push-subscribe" class="btn-secondary" title="Включить уведомления">
  🔔 Уведомления
</button>
```

- [ ] **Step 3: Add push subscription logic in `static/app.js`**

```javascript
async function getPushPublicKey() {
    const res = await api('/api/push/vapid-public-key');
    return res.key;
}

function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64);
    return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}

async function subscribeToPush() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
        alert('Push-уведомления не поддерживаются в этом браузере');
        return;
    }
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') {
        alert('Разрешение на уведомления отклонено');
        return;
    }
    const reg = await navigator.serviceWorker.ready;
    const publicKey = await getPushPublicKey();
    const subscription = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
    });
    await api('/api/push/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(subscription.toJSON()),
    });
    document.getElementById('btn-push-subscribe').textContent = '🔕 Отписаться';
}

async function unsubscribeFromPush() {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
        await api('/api/push/subscribe', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ endpoint: sub.endpoint }),
        });
        await sub.unsubscribe();
    }
    document.getElementById('btn-push-subscribe').textContent = '🔔 Уведомления';
}

document.getElementById('btn-push-subscribe')?.addEventListener('click', async () => {
    const reg = await navigator.serviceWorker.ready;
    const existing = await reg.pushManager.getSubscription();
    if (existing) {
        await unsubscribeFromPush();
    } else {
        await subscribeToPush();
    }
});

// On page load, update button state
(async () => {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    const btn = document.getElementById('btn-push-subscribe');
    if (btn && sub) btn.textContent = '🔕 Отписаться';
})();
```

- [ ] **Step 4: Run all tests**

```bash
python -m unittest tests.test_core -v
```

Expected: all pass

- [ ] **Step 5: Manual test** — start the app, open browser, click "🔔 Уведомления", grant permission, trigger a scan, verify push notification appears.

- [ ] **Step 6: Commit**

```bash
git add static/sw.js static/app.js static/index.html
git commit -m "feat: PWA push notifications — service worker handler and subscribe UI"
```
