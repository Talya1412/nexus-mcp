"""Tool profiles: NEXUS_MCP_TOOLS=read|rw|all prunes the registered tool set.

Applied once at server import (see server.py), before the first tools/list
round-trip, so a restrictive profile is pinned early. An explicit invalid
value fails loudly instead of silently exposing the write surface.
"""

from __future__ import annotations

import os
from typing import Any

from ._server import mcp

_PROFILE_VALUES = ("read", "rw", "all")


def _tool_read_only(tool: Any) -> bool:
    return bool(getattr(getattr(tool, "annotations", None), "readOnlyHint", False))


def _tool_destructive(tool: Any) -> bool:
    return bool(getattr(getattr(tool, "annotations", None), "destructiveHint", False))


def _apply_profile(value: str | None = None, server: Any | None = None) -> str:
    """Prune ``server`` so only the tools allowed by the active profile remain.

    Profiles:
      - all (default when NEXUS_MCP_TOOLS is unset or empty): every tool stays.
      - read: only tools annotated read-only (public reads, quota-free reads).
      - rw: read-only plus non-destructive writes; destructive tools are removed.

    An unknown profile raises ValueError at startup rather than quietly
    widening the write surface. Returns the applied profile name.
    """
    target = server or mcp
    raw = value if value is not None else os.environ.get("NEXUS_MCP_TOOLS", "")
    profile = raw.strip().lower()
    if not profile:
        profile = "all"
    if profile not in _PROFILE_VALUES:
        raise ValueError(
            f"Invalid NEXUS_MCP_TOOLS profile {raw!r} - expected one of: {', '.join(_PROFILE_VALUES)}."
        )
    if profile == "all":
        return profile
    for tool in target._tool_manager.list_tools():
        keep = _tool_read_only(tool) if profile == "read" else not _tool_destructive(tool)
        if not keep:
            target.remove_tool(tool.name)
    return profile