"""AmpHead: Hermes as the guitar amp.

There is no host application any more. The plugin starts its own audio engine, which owns the
interface, runs the capture, tone stack, cabinet and master, and answers over a pipe. Chat and the
pane are two ways to say the same thing to that engine.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    """Register the tools. Imports stay inside this function so validation can run with no engine
    installed, no audio device and no captures or cabinets on the machine."""
    from .amphead import tools

    # json_tools() wraps each handler so the tool-call answer is one JSON string while the panes
    # and the tests keep reading the sentence the handler itself returns.
    registered = tools.json_tools()
    for name, schema, handler, emoji in registered:
        ctx.register_tool(name=name, toolset="amphead", schema=schema, handler=handler,
                          emoji=emoji)
    logger.debug("amphead: registered %d tools", len(registered))
