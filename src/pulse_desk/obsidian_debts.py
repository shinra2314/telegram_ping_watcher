"""Two-way sync between the app's "Долги" board and an Obsidian note.

The note (``Долги.md``) is a hand-maintained giveaway-debt ledger:

    # @username <split rule>
    - [x] https://t.me/chan/123 prize name получатель ✅ 2026-05-14
    - [ ] https://t.me/chan/456 another prize

Sections are ``# @username`` headers; checklist items carry a ``t.me`` link
(the join key to a ``pings`` row), an optional ``✅ date`` and a free-text
title.  A YAML frontmatter, a ``BUTTON[...]`` line and a ``dataviewjs`` code
fence sit *above* the first ``# @`` header, so the section parser never
touches them.

This module is split into:
  * pure helpers (parse / normalise / line-edit / reconcile) — unit tested,
    no IO, no DB;
  * filesystem IO (atomic write + dated backup);
  * async orchestration (``sync_once`` / ``toggle_item`` / ``mark_link_done``)
    that pulls pings from the DB and applies the "note wins" rules.

See docs: the note is the source of truth on conflict (per user decision).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

HEADER_RE = re.compile(r"^(#{1,6})\s*@(\S+)\s*(.*)$")
ITEM_RE = re.compile(r"^(\s*)-\s\[([ xX])\]\s?(.*)$")
TME_RE = re.compile(r"(?:https?://)?t\.me/[^\s)\]\}>]+", re.IGNORECASE)
DONE_DATE_RE = re.compile(r"✅\s*\d{4}-\d{2}-\d{2}")
DONE_DATE_CAPTURE_RE = re.compile(r"✅\s*(\d{4}-\d{2}-\d{2})")
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")

# Best-effort recipient keywords used in the ledger.
RECIPIENT_WORDS = (
    "мне", "пумбе", "пумбычу", "пумба", "тимону", "тимон", "снаке", "снейк",
    "вове", "вова", "муверу", "мувер",
)

_NEGATIVE_STATUSES = {"scam", "missed", "missed_reply", "missed_unsubscribe", "closed"}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Item:
    checked: bool
    link: Optional[str]
    link_norm: Optional[str]
    title: str
    recipient: str
    done_date: Optional[str]
    indent: str
    raw: str
    line_index: int
    username: str


@dataclass
class Section:
    username: str
    split_rule: str
    header_index: int
    items: list[Item] = field(default_factory=list)


@dataclass
class ParsedNote:
    sections: list[Section]
    items: list[Item]
    lines: list[str]
    newline: str


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _detect_newline(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text:
        return "\r"
    return "\n"


def _parse_mentions(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if not value:
        return []
    try:
        data = json.loads(value)
        if isinstance(data, list):
            return [str(v) for v in data]
    except (ValueError, TypeError):
        pass
    return []


def _extract_recipient(content: str) -> str:
    found: list[str] = []
    for m in WIKILINK_RE.finditer(content):
        found.append((m.group(2) or m.group(1)).strip())
    lower = content.lower()
    for word in RECIPIENT_WORDS:
        if re.search(rf"(?<![\wа-яё]){re.escape(word)}(?![\wа-яё])", lower):
            found.append(word)
    # De-duplicate, preserve order.
    seen: set[str] = set()
    out: list[str] = []
    for value in found:
        key = value.lower()
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return ", ".join(out)


# ---------------------------------------------------------------------------
# Pure: link normalisation
# ---------------------------------------------------------------------------

def normalize_tme_link(value: Optional[str]) -> Optional[str]:
    """Canonical ``t.me/<path>`` form for matching, or None when absent."""
    if not value:
        return None
    match = TME_RE.search(value)
    if not match:
        return None
    raw = match.group(0).strip()
    raw = re.sub(r"^https?://", "", raw, flags=re.IGNORECASE)
    raw = raw.rstrip(".,;:!?'\")")
    raw = raw.rstrip("/")
    raw = raw.lower()
    if not raw.startswith("t.me/"):
        return None
    return raw


# ---------------------------------------------------------------------------
# Pure: parsing
# ---------------------------------------------------------------------------

def parse_note(text: str) -> ParsedNote:
    newline = _detect_newline(text)
    lines = text.split(newline)
    sections: list[Section] = []
    items: list[Item] = []
    current: Optional[Section] = None
    in_fence = False

    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        header = HEADER_RE.match(line)
        if header:
            current = Section(
                username=header.group(2).strip(),
                split_rule=header.group(3).strip(),
                header_index=index,
            )
            sections.append(current)
            continue

        item_match = ITEM_RE.match(line)
        if not item_match or current is None:
            continue

        indent, check_char, content = item_match.group(1), item_match.group(2), item_match.group(3)
        if not content.strip():
            # Blank checkbox placeholder line — not a real debt.
            continue

        link_norm = normalize_tme_link(content)
        link_raw: Optional[str] = None
        if link_norm:
            link_match = TME_RE.search(content)
            if link_match:
                link_raw = link_match.group(0).rstrip(".,;:!?'\")")

        done_match = DONE_DATE_CAPTURE_RE.search(content)
        done_date = done_match.group(1) if done_match else None

        title = content
        if link_raw:
            title = title.replace(link_raw, " ")
        title = DONE_DATE_RE.sub(" ", title)
        title = re.sub(r"\s+", " ", title).strip()

        item = Item(
            checked=check_char.lower() == "x",
            link=link_raw,
            link_norm=link_norm,
            title=title,
            recipient=_extract_recipient(content),
            done_date=done_date,
            indent=indent,
            raw=line,
            line_index=index,
            username=current.username,
        )
        current.items.append(item)
        items.append(item)

    return ParsedNote(sections=sections, items=items, lines=lines, newline=newline)


# ---------------------------------------------------------------------------
# Pure: single-line edits
# ---------------------------------------------------------------------------

def set_checkbox(line: str, checked: bool, date: Optional[str] = None) -> str:
    """Toggle a checklist line's box, managing the trailing ``✅ date`` stamp."""
    match = ITEM_RE.match(line)
    if not match:
        return line
    indent, _check, content = match.group(1), match.group(2), match.group(3)
    content = DONE_DATE_RE.sub("", content).rstrip()
    box = "x" if checked else " "
    out = f"{indent}- [{box}] {content}".rstrip()
    if checked and date:
        out = f"{out} ✅ {date}"
    return out


def render_append_line(link: str, title: str = "", indent: str = "") -> str:
    base = f"{indent}- [ ] {link}".rstrip()
    if title and title.strip():
        base = f"{base} {title.strip()}"
    return base


# ---------------------------------------------------------------------------
# Pure: reconciliation (note is the source of truth)
# ---------------------------------------------------------------------------

def _is_claimed(ping: dict) -> bool:
    return (ping.get("giveaway_status") or "").lower() == "claimed" or (ping.get("action_status") or "").lower() == "claimed"


def _is_negative(ping: dict) -> bool:
    gs = (ping.get("giveaway_status") or "").lower()
    as_ = (ping.get("action_status") or "").lower()
    return gs in _NEGATIVE_STATUSES or as_ in _NEGATIVE_STATUSES


def reconcile_status(parsed: ParsedNote, pings: Sequence[dict]) -> list[dict]:
    """Status updates to apply to ``pings`` so they match the note.

    Only the binary "done = claimed" bit is synced, and only for links that
    exist in the note.  Explicit negative outcomes (scam/missed) are never
    overridden.
    """
    note_by_link: dict[str, Item] = {}
    for item in parsed.items:
        if item.link_norm and item.link_norm not in note_by_link:
            note_by_link[item.link_norm] = item

    updates: list[dict] = []
    for ping in pings:
        link_norm = normalize_tme_link(ping.get("link"))
        if not link_norm or link_norm not in note_by_link:
            continue
        item = note_by_link[link_norm]
        if item.checked:
            if not _is_claimed(ping) and not _is_negative(ping):
                updates.append({
                    "ping_id": ping["id"],
                    "giveaway_status": "claimed",
                    "action_status": "claimed",
                    "reason": "note_checked",
                })
        else:
            if _is_claimed(ping):
                updates.append({
                    "ping_id": ping["id"],
                    "giveaway_status": "pending",
                    "action_status": "claim_prize",
                    "reason": "note_unchecked",
                })
    return updates


def compute_appends(parsed: ParsedNote, debts: Sequence[dict]) -> list[dict]:
    """New detected wins to append under an existing ``# @username`` section.

    Skips links already present in the note and usernames without a section
    (we never invent headers — the split rule is a human decision).
    """
    note_links = {item.link_norm for item in parsed.items if item.link_norm}
    sections = {section.username.lower(): section.username for section in parsed.sections}

    appends: list[dict] = []
    seen: set[str] = set()
    for debt in debts:
        link_norm = normalize_tme_link(debt.get("link"))
        if not link_norm or link_norm in note_links or link_norm in seen:
            continue
        target: Optional[str] = None
        for mention in debt.get("mentions") or []:
            key = str(mention).lstrip("@").lower()
            if key in sections:
                target = sections[key]
                break
        if not target:
            continue
        seen.add(link_norm)
        appends.append({"username": target, "link": debt.get("link"), "title": debt.get("title") or ""})
    return appends


def build_snapshot(parsed: ParsedNote, pings: Sequence[dict]) -> dict:
    """Display payload for the panel: per-username progress + matched pings."""
    by_link: dict[str, dict] = {}
    for ping in pings:
        link_norm = normalize_tme_link(ping.get("link"))
        if link_norm and link_norm not in by_link:
            by_link[link_norm] = ping

    groups: list[dict] = []
    total = done = linked = matched = 0
    for section in parsed.sections:
        g_items: list[dict] = []
        g_done = 0
        for item in section.items:
            total += 1
            if item.checked:
                done += 1
                g_done += 1
            ping = by_link.get(item.link_norm) if item.link_norm else None
            if item.link_norm:
                linked += 1
            if ping:
                matched += 1
            g_items.append({
                "checked": item.checked,
                "link": item.link,
                "link_norm": item.link_norm,
                "title": item.title,
                "recipient": item.recipient,
                "done_date": item.done_date,
                "matched_ping_id": ping["id"] if ping else None,
                "ping_giveaway_status": (ping.get("giveaway_status") if ping else None),
            })
        g_total = len(section.items)
        groups.append({
            "username": section.username,
            "split_rule": section.split_rule,
            "done": g_done,
            "total": g_total,
            "pct": round(g_done / g_total * 100) if g_total else 0,
            "items": g_items,
        })

    return {
        "generated_at": _now_iso(),
        "overall": {"done": done, "total": total, "pct": round(done / total * 100) if total else 0},
        "groups": groups,
        "stats": {
            "sections": len(parsed.sections),
            "items": total,
            "linked": linked,
            "matched": matched,
            "unlinked": total - linked,
        },
    }


# ---------------------------------------------------------------------------
# Pure: whole-note text edits
# ---------------------------------------------------------------------------

def apply_appends_text(text: str, parsed: ParsedNote, appends: Sequence[dict]) -> str:
    """Insert new ``- [ ]`` lines at the end of each target section."""
    if not appends:
        return text
    lines = list(parsed.lines)
    insert_at: dict[str, int] = {}
    for section in parsed.sections:
        if section.items:
            insert_at[section.username.lower()] = max(item.line_index for item in section.items) + 1
        else:
            insert_at[section.username.lower()] = section.header_index + 1

    buckets: dict[int, list[str]] = {}
    for append in appends:
        key = append["username"].lower()
        if key not in insert_at:
            continue
        buckets.setdefault(insert_at[key], []).append(render_append_line(append["link"], append.get("title", "")))

    if not buckets:
        return text
    for index in sorted(buckets.keys(), reverse=True):
        lines[index:index] = buckets[index]
    return parsed.newline.join(lines)


# ---------------------------------------------------------------------------
# Filesystem IO
# ---------------------------------------------------------------------------

def read_note(path: Path) -> str:
    with open(path, "r", encoding="utf-8-sig") as handle:
        return handle.read()


def _backup(path: Path, *, retention: int = 20) -> None:
    backup_dir = path.parent / ".pulse_desk_backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{path.stem}.{stamp}{path.suffix}"
    try:
        shutil.copy2(path, target)
    except OSError:
        return
    backups = sorted(backup_dir.glob(f"{path.stem}.*{path.suffix}"))
    for stale in backups[:-retention]:
        try:
            stale.unlink()
        except OSError:
            pass


def write_note(path: Path, text: str, *, make_backup: bool = True) -> None:
    if make_backup and path.exists():
        _backup(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Async orchestration
# ---------------------------------------------------------------------------

SETTINGS_KEY = "obsidian"


def apply_prefs(settings, prefs: Optional[dict]) -> None:
    """Override the runtime settings singleton with stored UI prefs."""
    if not prefs:
        return
    if "enabled" in prefs:
        settings.obsidian_sync_enabled = bool(prefs["enabled"])
    if "write" in prefs:
        settings.obsidian_sync_write = bool(prefs["write"])
    if "path" in prefs and prefs["path"] is not None:
        settings.obsidian_debts_path = str(prefs["path"])


async def load_prefs() -> dict:
    from database import get_setting

    raw = await get_setting(SETTINGS_KEY, "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


async def save_prefs(prefs: dict) -> None:
    from database import set_setting

    await set_setting(SETTINGS_KEY, json.dumps(prefs, ensure_ascii=False))


def current_config(settings) -> dict:
    return {
        "enabled": bool(getattr(settings, "obsidian_sync_enabled", False)),
        "write": bool(getattr(settings, "obsidian_sync_write", False)),
        "path": getattr(settings, "obsidian_debts_path", "") or "",
        "poll_seconds": int(getattr(settings, "obsidian_sync_poll_seconds", 30) or 30),
    }


def _note_path(settings) -> Optional[Path]:
    raw = (getattr(settings, "obsidian_debts_path", "") or "").strip()
    if not raw:
        return None
    return Path(raw)


def _short_title(text: Optional[str]) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    return cleaned[:80]


def _debts_for_append(pings: Sequence[dict], since: Optional[str]) -> list[dict]:
    # Never bulk-append history: only wins detected after the previous sync.
    if not since:
        return []
    result: list[dict] = []
    for ping in pings:
        if not ping.get("is_win"):
            continue
        if _is_claimed(ping) or _is_negative(ping):
            continue
        detected = ping.get("detected_at") or ""
        if detected <= since:
            continue
        result.append({
            "link": ping.get("link"),
            "title": _short_title(ping.get("text")),
            "mentions": _parse_mentions(ping.get("mentions")),
        })
    return result


async def load_snapshot(state, settings) -> dict:
    """Read + parse the note and cache a display snapshot. No DB/file writes.

    Used by the read endpoint so merely *viewing* the panel never mutates ping
    statuses — reconciliation only happens on the background loop, an explicit
    ``POST /sync`` or a checkbox toggle.
    """
    from database import get_giveaway_pings_with_links

    path = _note_path(settings)
    if path is None:
        payload = {"enabled": False, "reason": "no_path", "groups": [], "overall": {"done": 0, "total": 0, "pct": 0}}
        state.obsidian_debts = payload
        return payload
    if not path.exists():
        payload = {"enabled": True, "reason": "missing_file", "path": str(path), "groups": [], "overall": {"done": 0, "total": 0, "pct": 0}}
        state.obsidian_debts = payload
        return payload
    async with state.obsidian_lock:
        parsed = parse_note(read_note(path))
        pings = await get_giveaway_pings_with_links()
        snapshot = build_snapshot(parsed, pings)
        snapshot["enabled"] = True
        snapshot["sync"] = state.obsidian_sync_meta or {
            "write_enabled": bool(getattr(settings, "obsidian_sync_write", False)),
            "last_sync_at": None,
        }
        state.obsidian_debts = snapshot
    return snapshot


async def sync_once(state, settings, *, force: bool = False) -> dict:
    """One reconcile pass: note→app status, app→note appends, refresh snapshot."""
    from database import get_giveaway_pings_with_links, set_setting, update_ping_meta

    from .live import publish_live_event

    path = _note_path(settings)
    if path is None:
        meta = {"enabled": False, "reason": "no_path"}
        state.obsidian_debts = {**meta, "groups": [], "overall": {"done": 0, "total": 0, "pct": 0}}
        return meta
    if not path.exists():
        meta = {"enabled": True, "reason": "missing_file", "path": str(path)}
        state.obsidian_debts = {**meta, "groups": [], "overall": {"done": 0, "total": 0, "pct": 0}}
        return meta

    prev_since = (state.obsidian_sync_meta or {}).get("last_sync_at")
    applied = 0
    appended = 0
    async with state.obsidian_lock:
        text = read_note(path)
        parsed = parse_note(text)
        pings = await get_giveaway_pings_with_links()

        for update in reconcile_status(parsed, pings):
            await update_ping_meta(
                update["ping_id"],
                giveaway_status=update["giveaway_status"],
                action_status=update["action_status"],
            )
            applied += 1

        if getattr(settings, "obsidian_sync_write", False):
            appends = compute_appends(parsed, _debts_for_append(pings, prev_since))
            if appends:
                text = apply_appends_text(text, parsed, appends)
                write_note(path, text, make_backup=True)
                appended = len(appends)
                parsed = parse_note(text)

        file_hash = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()
        meta = {
            "enabled": True,
            "path": str(path),
            "mtime": path.stat().st_mtime,
            "file_hash": file_hash,
            "last_sync_at": _now_iso(),
            "write_enabled": bool(getattr(settings, "obsidian_sync_write", False)),
            "applied_status_updates": applied,
            "appended": appended,
        }
        snapshot = build_snapshot(parsed, pings)
        snapshot["enabled"] = True
        snapshot["sync"] = meta
        state.obsidian_debts = snapshot
        state.obsidian_sync_meta = meta

    try:
        await set_setting("obsidian_sync", json.dumps(meta, ensure_ascii=False))
    except Exception:
        pass
    if applied or appended or force:
        await publish_live_event("obsidian-sync", {"applied": applied, "appended": appended})
    return meta


async def toggle_item(state, settings, link_norm: str, checked: bool) -> dict:
    """Toggle a note checkbox from the UI, then reconcile (note wins)."""
    path = _note_path(settings)
    if path is None or not path.exists():
        return {"ok": False, "reason": "no_file"}
    async with state.obsidian_lock:
        text = read_note(path)
        parsed = parse_note(text)
        target = next((item for item in parsed.items if item.link_norm == link_norm), None)
        if target is None:
            return {"ok": False, "reason": "not_found"}
        lines = list(parsed.lines)
        lines[target.line_index] = set_checkbox(lines[target.line_index], checked, _today() if checked else None)
        write_note(path, parsed.newline.join(lines), make_backup=True)
    meta = await sync_once(state, settings, force=True)
    return {"ok": True, "sync": meta}


async def mark_link_done(state, settings, link: Optional[str], checked: bool) -> None:
    """App→note hook: stamp/clear a checkbox for the ping's link (best-effort)."""
    if not getattr(settings, "obsidian_sync_write", False):
        return
    link_norm = normalize_tme_link(link)
    if not link_norm:
        return
    path = _note_path(settings)
    if path is None or not path.exists():
        return
    async with state.obsidian_lock:
        text = read_note(path)
        parsed = parse_note(text)
        target = next((item for item in parsed.items if item.link_norm == link_norm), None)
        if target is None or target.checked == checked:
            return
        lines = list(parsed.lines)
        lines[target.line_index] = set_checkbox(lines[target.line_index], checked, _today() if checked else None)
        write_note(path, parsed.newline.join(lines), make_backup=True)
