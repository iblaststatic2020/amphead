"""The verdict is the amp's headline word. The machine behind it lives in `lifecycle.py`.

This module stays the public name because every caller already says `verdict.judge(...)`, the
pane paints `verdict`, and the tests pin that vocabulary. It is now a thin alias: one pure
function decides, and it also returns the axes a caller needs to explain the word (engine,
signal, activity, health, next, notice). Adding fields is backward compatible; the headline
words and the `judge` signature are unchanged.
"""

from __future__ import annotations

from typing import Any

from .lifecycle import (  # noqa: F401  (re-exported words are the public vocabulary)
    ARM_DB,
    CLIPPING,
    DEVICE_CLOSED,
    DEVICE_REOPENED,
    DEVICE_UNPLUGGED,
    ENGINE_OFF,
    FLOOR,
    GLITCH,
    LATENCY_LIMIT_MS,
    MUTED,
    NEXT,
    NO_CHAIN,
    NO_INPUT,
    NO_SIGNAL,
    PARKED,
    PARKED_DB,
    READY,
    STANDBY,
    clip_of,
    snapshot,
)


def judge(state: dict, reading: dict | None = None,
          chain: list[dict] | None = None) -> dict[str, Any]:
    """One word a player can read, the reason, and the explicit axes behind them."""
    return snapshot(state, reading, chain)
