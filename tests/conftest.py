"""Keep the test run away from the live app's files.

The suite used to run against whatever `.env` pointed at. A test that did not
patch `database.DB_PATH` wrote into the real `pulse_desk.db` — a fake
«Bot handler failed: nope» event from `test_bot_safety.py` sits in the owner's
event log from 11.09 — `app_ctx` logged into the running app's `logs/app.log`,
and a run while the app was up competed with it for SQLite's write lock.

Env vars beat `.env` (`load_dotenv(override=False)`, and pydantic-settings ranks
the environment above its env file), so pointing them at a temp dir *before*
anything imports `pulse_desk.config` moves every default path. The guard turns a
future slip — a hardcoded path, a new setting — into a loud failure instead of
a silent write into the owner's data.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE_DB_FILES = {
    (ROOT / name).resolve()
    for name in ("pulse_desk.db", "pulse_desk_archive.db")
}
_TMP = Path(tempfile.mkdtemp(prefix="pulse_tests_"))

os.environ["PULSE_DB_PATH"] = str(_TMP / "pulse_desk.db")
os.environ["PULSE_LOG_DIR"] = str(_TMP / "logs")
# Features that read or write the owner's own files elsewhere on the disk.
os.environ["OBSIDIAN_DEBTS_PATH"] = ""
os.environ["OBSIDIAN_SYNC_ENABLED"] = "false"
os.environ["OBSIDIAN_SYNC_WRITE"] = "false"
os.environ["SALARY_XLSX_PATH"] = ""

for path in (str(ROOT), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)


def _target(database) -> Path | None:
    text = os.fspath(database)
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    if text == ":memory:" or not text:
        return None
    if text.startswith("file:"):
        text = text[5:].split("?", 1)[0]
    try:
        return Path(text).resolve()
    except (OSError, ValueError):
        return None


_real_connect = sqlite3.connect
# Code under test often swallows DB errors (record_app_event must never break
# a handler), so the raise alone could go unseen — every hit also fails the run.
_LIVE_HITS: list[str] = []


def _guarded_connect(database, *args, **kwargs):
    if _target(database) in LIVE_DB_FILES:
        _LIVE_HITS.append(repr(database))
        raise RuntimeError(
            f"test opened the live database {database!r}; patch database.DB_PATH "
            "(see tests/conftest.py)"
        )
    return _real_connect(database, *args, **kwargs)


# aiosqlite resolves `sqlite3.connect` at call time, so this covers it too.
sqlite3.connect = _guarded_connect

import database  # noqa: E402  (after the env above)

database.BACKUP_DIR = _TMP / "backups"


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)
    if _LIVE_HITS:
        sys.stderr.write(
            f"\n{len(_LIVE_HITS)} connection(s) to the live database were refused: "
            f"{sorted(set(_LIVE_HITS))}\n"
        )
        session.exitstatus = 1
