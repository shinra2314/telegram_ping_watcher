"""Backward-compat shim. The bot UI now lives in the `pulse_desk.bot` package.

`main.py` and any external caller still do `from pulse_desk.bot_service import
init_bot`; this re-export keeps that import path stable.
"""
from __future__ import annotations

from .bot.service import init_bot

__all__ = ["init_bot"]
