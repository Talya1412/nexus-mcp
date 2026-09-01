"""Nexus Mods MCP server - tool registration + stdio entry point."""

from . import tools  # noqa: F401 - registers all tools on import
from ._core import APP_VERSION as __version__
from ._server import mcp

__all__ = ["__version__", "mcp", "main"]


def main() -> None:
    """Entry point for the packaged `nexus-mcp` command (stdio transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
