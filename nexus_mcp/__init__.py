"""Nexus Mods MCP server package (REST API v1 + GraphQL API v2)."""

from ._core import APP_VERSION as __version__
from ._core import EXPECTED_TOOLS
from .server import main, mcp

__all__ = ["EXPECTED_TOOLS", "__version__", "main", "mcp"]
