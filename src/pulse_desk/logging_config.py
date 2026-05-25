from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(log_file: Path, log_level: str = "INFO", telethon_log_level: str = "WARNING") -> logging.Logger:
    log_file.parent.mkdir(exist_ok=True)
    handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[handler, logging.StreamHandler()],
        force=True,
    )
    logging.getLogger("telethon").setLevel(getattr(logging, telethon_log_level.upper(), logging.WARNING))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").disabled = True
    return logging.getLogger("pulse_desk")
