"""AmpHead: Hermes as the amp head for a REAPER guitar rig.

The plugin never processes audio and never drives REAPER's UI. It starts the host, keeps its
own tab, builds and verifies the chain through a file-queue bridge, and leaves the track in
standby until the player flips Live.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    """Register the tools. Imports stay inside this function so validation can run with the host
    closed and with no bridge, no audio device and no rig file present."""
    from .amphead.tools import TOOLS, check_reaper_available

    for name, schema, handler, emoji in TOOLS:
        ctx.register_tool(name=name, toolset="amphead", schema=schema, handler=handler,
                          check_fn=check_reaper_available, emoji=emoji)
    logger.debug("amphead: registered %d tools", len(TOOLS))
