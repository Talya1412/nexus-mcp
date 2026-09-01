"""NEXUS_MCP_TOOLS profile filtering (#20)."""

import pytest
from mcp.server.fastmcp import FastMCP

from nexus_mcp._annotations import (
    _DESTRUCTIVE_ANNOTATIONS,
    _DESTRUCTIVE_IDEMPOTENT_ANNOTATIONS,
    _IDEMPOTENT_MUTATION_ANNOTATIONS,
    _MUTATING_ANNOTATIONS,
    _READ_ONLY_ANNOTATIONS,
)
from nexus_mcp._profiles import _PROFILE_VALUES, _apply_profile, _tool_destructive, _tool_read_only


def _make_server() -> FastMCP:
    server = FastMCP("profiles")

    @server.tool(name="tool_read", annotations={**_READ_ONLY_ANNOTATIONS})
    def tool_read(x: int = 1) -> str:
        """Read-only tool."""
        return str(x)

    @server.tool(name="tool_mutate", annotations={**_MUTATING_ANNOTATIONS})
    def tool_mutate() -> str:
        """One-shot write."""
        return "ok"

    @server.tool(name="tool_idem", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS})
    def tool_idem() -> str:
        """Converging setter."""
        return "ok"

    @server.tool(name="tool_destroy", annotations={**_DESTRUCTIVE_ANNOTATIONS})
    def tool_destroy() -> str:
        """Destructive one-shot."""
        return "ok"

    @server.tool(name="tool_destroy_idem", annotations={**_DESTRUCTIVE_IDEMPOTENT_ANNOTATIONS})
    def tool_destroy_idem() -> str:
        """Destructive but repeatable."""
        return "ok"

    return server


def _names(server: FastMCP) -> list[str]:
    return sorted(t.name for t in server._tool_manager.list_tools())


def test_all_profile_keeps_every_tool() -> None:
    server = _make_server()
    assert _apply_profile("all", server=server) == "all"
    assert _names(server) == [
        "tool_destroy",
        "tool_destroy_idem",
        "tool_idem",
        "tool_mutate",
        "tool_read",
    ]


def test_read_profile_keeps_only_read_only_tools() -> None:
    server = _make_server()
    assert _apply_profile("read", server=server) == "read"
    assert _names(server) == ["tool_read"]


def test_rw_profile_removes_destructive_tools() -> None:
    server = _make_server()
    assert _apply_profile("rw", server=server) == "rw"
    assert _names(server) == ["tool_idem", "tool_mutate", "tool_read"]


def test_empty_env_value_is_all() -> None:
    server = _make_server()
    assert _apply_profile("", server=server) == "all"
    assert len(_names(server)) == 5


def test_explicit_value_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXUS_MCP_TOOLS", "read")
    server = _make_server()
    assert _apply_profile("all", server=server) == "all"
    assert len(_names(server)) == 5


def test_env_value_is_used_when_no_explicit_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXUS_MCP_TOOLS", "read")
    server = _make_server()
    assert _apply_profile(server=server) == "read"
    assert _names(server) == ["tool_read"]


def test_unknown_profile_raises() -> None:
    server = _make_server()
    with pytest.raises(ValueError, match="NEXUS_MCP_TOOLS"):
        _apply_profile("bogus", server=server)
    assert len(_names(server)) == 5


def test_annotation_helpers() -> None:
    server = _make_server()
    by_name = {t.name: t for t in server._tool_manager.list_tools()}
    assert _tool_read_only(by_name["tool_read"]) is True
    assert _tool_read_only(by_name["tool_mutate"]) is False
    assert _tool_destructive(by_name["tool_destroy"]) is True
    assert _tool_destructive(by_name["tool_destroy_idem"]) is True
    assert _tool_destructive(by_name["tool_idem"]) is False


def test_real_server_annotation_counts() -> None:
    """Guard the real 140-tool surface: read=74, destructive=12 => rw keeps 128."""
    import nexus_mcp.server  # noqa: F401 - registers all tools with the default profile
    from nexus_mcp._server import mcp

    tools = mcp._tool_manager.list_tools()
    read_only = [t for t in tools if _tool_read_only(t)]
    destructive = [t for t in tools if _tool_destructive(t)]
    assert len(tools) == 140
    assert len(read_only) == 74
    assert len(destructive) == 12
    assert len(tools) - len(destructive) == 128


def test_profile_values_are_valid() -> None:
    assert _PROFILE_VALUES == ("read", "rw", "all")