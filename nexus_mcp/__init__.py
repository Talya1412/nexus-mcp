"""Nexus Mods MCP server package (REST API v1 + GraphQL API v2)."""

from .server import main, mcp
from ._core import APP_VERSION as __version__

__all__ = ["__version__", "main", "mcp"]
