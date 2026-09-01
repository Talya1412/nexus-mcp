"""Nexus Mods MCP server package (REST API v1 + GraphQL API v2)."""

from ._core import APP_VERSION as __version__
from .server import main, mcp

__all__ = ["__version__", "main", "mcp"]
