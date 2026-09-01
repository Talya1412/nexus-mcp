#!/usr/bin/env python3
"""Backwards-compatible entry shim - the implementation lives in the nexus_mcp package.

Kept so configs that run `python server.py` directly keep working; the
installable entry point is `nexus-mcp` (nexus_mcp.server:main).
"""
from nexus_mcp.server import main, mcp

if __name__ == "__main__":
    main()
