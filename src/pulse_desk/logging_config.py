from __future__ import annotations

import logging
import re
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable


class CollapseRepeats(logging.Filter):
    """One line a minute for Telethon's reconnect noise; the rest are counted.

    After a network drop every account's connection logs the same warnings in a
    storm — «Security error while unpacking…» reached 723 lines in one minute
    (23.09), each a synchronous file write on the event loop, burying anything
    else in the log. The first of a kind per ``window`` gets through and says how
    many were swallowed since the last one. A filter per handler: a record
    reaches each handler once, so the counts stay honest.
    """

    PATTERNS = (
        re.compile(r"^Security error while unpacking a received message"),
        re.compile(r"^Server closed the connection"),
        re.compile(r"^Attempt \d+ at connecting failed"),
        re.compile(r"^Graceful disconnection timed out"),
    )

    def __init__(self, window: float = 60.0, clock: Callable[[], float] = time.monotonic):
        super().__init__()
        self.window = window
        self.clock = clock
        self._seen: dict[str, tuple[float, int]] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.name.startswith("telethon"):
            return True
        message = record.getMessage()
        kind = next((pattern.pattern for pattern in self.PATTERNS if pattern.match(message)), None)
        if kind is None:
            return True
        now = self.clock()
        at, held = self._seen.get(kind, (float("-inf"), 0))
        if now - at < self.window:
            self._seen[kind] = (at, held + 1)
            return False
        self._seen[kind] = (now, 0)
        if held:
            record.msg, record.args = f"{message} (and {held} more like it in the last minute)", ()
        return True


def console_level(stream) -> int:
    """Launched hidden, stderr is a file (host_stderr.log, runtime.out.log) that
    used to get a second copy of the whole app.log. It keeps what a person
    opening it looks for: warnings, errors, and a crash before logging is up."""
    is_terminal = stream is not None and hasattr(stream, "isatty") and stream.isatty()
    return logging.NOTSET if is_terminal else logging.WARNING


def configure_logging(log_file: Path, log_level: str = "INFO", telethon_log_level: str = "WARNING") -> logging.Logger:
    log_file.parent.mkdir(exist_ok=True)
    handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    console = logging.StreamHandler()
    console.setLevel(console_level(getattr(console, "stream", None)))
    for target in (handler, console):
        target.addFilter(CollapseRepeats())
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[handler, console],
        force=True,
    )
    logging.getLogger("telethon").setLevel(getattr(logging, telethon_log_level.upper(), logging.WARNING))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").disabled = True
    return logging.getLogger("pulse_desk")

