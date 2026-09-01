"""Required-argument enforcement (#21): schema <-> arg-model invariant + call rejection smoke."""

import asyncio

import pytest
from mcp.server.fastmcp.exceptions import ToolError

import nexus_mcp


def _protocol_tools_by_name():
    return {t.name: t for t in asyncio.run(nexus_mcp.mcp.list_tools())}


def _arg_model_tools():
    return list(nexus_mcp.mcp._tool_manager.list_tools())


def test_required_flags_match_pydantic_arg_model():
    """A parameter is required in inputSchema iff the pydantic arg-model field is required."""
    by_name = _protocol_tools_by_name()
    for tool in _arg_model_tools():
        model = tool.fn_metadata.arg_model
        model_required = {n for n, field in model.model_fields.items() if field.is_required()}
        schema_required = set(by_name[tool.name].inputSchema.get("required", []))
        assert model_required == schema_required, (
            f"{tool.name}: arg-model required {sorted(model_required)} "
            f"!= inputSchema required {sorted(schema_required)}"
        )


def test_optional_only_tools_are_callable_with_empty_args():
    """Tools with no required args must not be blocked by FastMCP (schema required empty)."""
    by_name = _protocol_tools_by_name()
    for tool in _arg_model_tools():
        model = tool.fn_metadata.arg_model
        if model.model_fields and not any(f.is_required() for f in model.model_fields.values()):
            schema = by_name[tool.name].inputSchema
            assert not schema.get("required"), (
                f"{tool.name}: schema lists required args that the arg-model does not"
            )


def test_missing_required_read_arg_is_rejected():
    with pytest.raises(ToolError) as excinfo:
        asyncio.run(nexus_mcp.mcp.call_tool("nexus_get_mod", {}))
    message = str(excinfo.value)
    assert "validation error" in message.lower()
    assert "domain_name" in message
    assert "mod_id" in message


def test_missing_required_v2_mutation_arg_is_rejected():
    with pytest.raises(ToolError) as excinfo:
        asyncio.run(nexus_mcp.mcp.call_tool("nexus_close_collection_bug_report", {}))
    assert "bug_report_id" in str(excinfo.value)


def test_all_optional_args_accepted_and_locally_rejected():
    """Omitting all-optional params is accepted; the tool body then handles it locally."""
    result = asyncio.run(nexus_mcp.mcp.call_tool("nexus_get_user_v2", {}))
    text = result[0][0].text
    assert "provide exactly one of member_id or username" in text


def test_zero_arg_tool_accepts_empty_args():
    result = asyncio.run(nexus_mcp.mcp.call_tool("nexus_oauth_status", {}))
    assert result