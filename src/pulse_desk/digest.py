from __future__ import annotations

from datetime import datetime
from typing import Any


def format_digest(pings: list[dict[str, Any]], *, period_label: str = "за последние 24 ч") -> str:
    if not pings:
        return f"📭 Нет новых пингов {period_label}."

    wins = [p for p in pings if p.get("is_win")]
    mentions = [p for p in pings if not p.get("is_win")]

    lines: list[str] = [f"📊 *Pulse Desk Digest* — {period_label}"]
    lines.append(f"Всего: {len(pings)} | 🏆 Побед: {len(wins)} | 📌 Упоминаний: {len(mentions)}")
    lines.append("")

    if wins:
        lines.append("🏆 *Победы:*")
        for p in wins[:10]:
            chat = p.get("chat") or "?"
            link = p.get("link") or ""
            text = (p.get("text") or "")[:80].replace("\n", " ")
            lines.append(f"• [{chat}]({link}) — {text}")
        lines.append("")

    if mentions:
        lines.append("📌 *Упоминания:*")
        for p in mentions[:10]:
            chat = p.get("chat") or "?"
            link = p.get("link") or ""
            lines.append(f"• [{chat}]({link})")

    lines.append(f"\n_Сгенерировано {datetime.now().strftime('%d.%m.%Y %H:%M')}_")
    return "\n".join(lines)
