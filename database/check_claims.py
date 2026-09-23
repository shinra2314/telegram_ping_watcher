"""Journal of wallet-bot check attempts (see ``pulse_desk.check_claimer``).

One row per (account session, bot, code). The claim path never reads this
table before pressing — that decision is in-memory, so a busy SQLite cannot slow
a claim — it only writes the outcome afterwards, and a relay step (the owner
answering a captcha) updates the same row.
"""
from __future__ import annotations

from typing import Any, Optional

import aiosqlite

from ._core import _connect, _now_iso

_FIELDS = ("bot", "code", "session", "account", "chat_id", "chat", "message_id", "link", "amount", "outcome", "reply")


async def record_check_claim(row: dict[str, Any]) -> int:
    """Insert or refresh the row for (session, bot, code); returns its id."""
    values = {name: row.get(name) for name in _FIELDS}
    for name in ("session", "account", "chat", "link", "amount", "reply"):
        values[name] = str(values[name] or "")
    values["reply"] = values["reply"][:1000]
    now = _now_iso()
    async with _connect() as db:
        await db.execute(
            f"""
            INSERT INTO check_claims (created_at, updated_at, {', '.join(_FIELDS)})
            VALUES (?, ?, {', '.join('?' * len(_FIELDS))})
            ON CONFLICT(session, bot, code) DO UPDATE SET
                updated_at = excluded.updated_at,
                outcome = excluded.outcome,
                reply = excluded.reply,
                amount = CASE WHEN excluded.amount != '' THEN excluded.amount ELSE check_claims.amount END
            """,
            (now, now, *(values[name] for name in _FIELDS)),
        )
        cursor = await db.execute(
            "SELECT id FROM check_claims WHERE session = ? AND bot = ? AND code = ?",
            (values["session"], values["bot"], values["code"]),
        )
        found = await cursor.fetchone()
        await db.commit()
    return int(found[0]) if found else 0


async def update_check_claim(claim_id: int, outcome: str, reply: Optional[str] = None) -> None:
    async with _connect() as db:
        if reply is None:
            await db.execute("UPDATE check_claims SET outcome = ?, updated_at = ? WHERE id = ?",
                             (outcome, _now_iso(), int(claim_id)))
        else:
            await db.execute("UPDATE check_claims SET outcome = ?, reply = ?, updated_at = ? WHERE id = ?",
                             (outcome, str(reply)[:1000], _now_iso(), int(claim_id)))
        await db.commit()


async def get_recent_check_claims(limit: int = 8) -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM check_claims ORDER BY COALESCE(updated_at, created_at) DESC, id DESC LIMIT ?",
            (int(limit),),
        )).fetchall()
    return [dict(row) for row in rows]


async def get_check_claim_stats(since: Optional[str] = None) -> dict[str, Any]:
    """``{"outcomes": {outcome: count}, "claimed": [amount…]}``, optionally since an ISO time."""
    where, args = ("WHERE created_at >= ?", (since,)) if since else ("", ())
    async with _connect() as db:
        outcomes = {
            str(outcome): int(count)
            for outcome, count in await (await db.execute(
                f"SELECT outcome, COUNT(*) FROM check_claims {where} GROUP BY outcome", args
            )).fetchall()
        }
        claimed_where = f"{where} {'AND' if where else 'WHERE'} outcome = 'claimed'"
        amounts = [str(row[0] or "") for row in await (await db.execute(
            f"SELECT amount FROM check_claims {claimed_where}", args
        )).fetchall()]
    return {"outcomes": outcomes, "claimed": amounts}
