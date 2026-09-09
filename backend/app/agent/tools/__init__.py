"""Importing this package registers every tool.

The orchestrator imports `registry` from here rather than from `base`, so that
importing the registry can never yield an empty one because a tool module was
not imported yet.
"""

from app.agent.tools import orders, policies, products  # noqa: F401
from app.agent.tools.base import ToolContext, ToolResult, ToolSpec, registry

__all__ = ["ToolContext", "ToolResult", "ToolSpec", "registry"]
