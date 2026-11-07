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
    from .amphead import appdecl
    from .amphead.tools import check_reaper_available, json_tools

    # Name REAPER to the host in its own vocabulary (application declaration). Loaders do not
    # read plugin YAML for this, so the plugin registers it itself; a host without
    # hermes_platform returns False and nothing else changes.
    appdecl.register()

    # json_tools() wraps each handler so the tool-call answer is one JSON string (guide T:115)
    # while the panes and the tests keep reading the sentence the handler itself returns.
    tools = json_tools()
    for name, schema, handler, emoji in tools:
        ctx.register_tool(name=name, toolset="amphead", schema=schema, handler=handler,
                          check_fn=check_reaper_available, emoji=emoji)
    logger.debug("amphead: registered %d tools", len(tools))
