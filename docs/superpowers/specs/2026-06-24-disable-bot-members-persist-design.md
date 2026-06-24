# Persisting disabled bot members

Date: 2026-06-24

## Problem

Admins can disable ("заблокировать") a bot member from the web UI. The block is
written to `bot_members.blocked = 1` and `bot_role` already denies blocked
members (`access_control.resolve_access` returns `Decision(False, "blocked")`).

But the block does not truly persist: any key re-redeem clears it. The flow:

```
blocked friend re-opens invite link
  -> /start <secret>  (or /redeem <secret>)
  -> grant_access(event, key)
  -> upsert_bot_member(...)
  -> ON CONFLICT(tg_id) DO UPDATE SET ... blocked = 0   <-- silently un-blocks
```

`grant_access` is the only caller of `upsert_bot_member`, so the leak is fully
contained to the redeem path.

## Fix

Two narrow changes plus tests. No schema change, no migration.

### 1. `database/bot_access.py` — stop clearing `blocked` on upsert

Remove `blocked = 0` from the `ON CONFLICT(tg_id) DO UPDATE SET` clause in
`upsert_bot_member`. A re-interaction (re-redeem, profile refresh) must never
silently clear an admin-set block. New members still default to `blocked = 0`
via the `INSERT ... VALUES (..., 0)` path, which is unchanged.

### 2. `src/pulse_desk/bot_service.py` — refuse redeem for a blocked member

In `grant_access`, before granting:

- Load the existing member (`get_bot_member(event.sender_id)`).
- If it exists and `member["blocked"]` is truthy, respond with
  `🚫 Доступ отключён владельцем.` and return without calling
  `upsert_bot_member`.

This gives clear UX on a redeem attempt and is defence-in-depth on top of
change 1. Re-enabling a member is done only by the admin **Разблокировать**
button in the web UI (already wired: `POST /api/bot/access/members/{tg_id}/block`
with `{"blocked": false}` -> `set_bot_member_blocked`).

### 3. Tests

- `upsert_bot_member` on an existing blocked member preserves `blocked = 1`.
- A blocked member redeeming a valid key stays blocked and is refused
  (behavioural test around `grant_access` / the redeem path, or a focused test
  on the upsert + block invariant if the bot handler is hard to exercise).

## Unchanged

Web block/unblock button, the `/api/bot/access/members/{tg_id}/block` endpoint,
`set_bot_member_blocked`, and `bot_role` gating are already correct and stay as
they are.

## Result

Disabling a bot member persists across every reconnect and key re-redeem until
the admin explicitly unblocks them in the web UI.
