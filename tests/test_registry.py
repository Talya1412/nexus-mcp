"""Tool registry invariants: count, uniqueness, descriptions, schemas, annotations."""
import asyncio

import nexus_mcp

EXPECTED_TOOLS = 140


def _tools():
    return asyncio.run(nexus_mcp.mcp.list_tools())


def test_tool_count():
    assert len(_tools()) == EXPECTED_TOOLS


def test_no_duplicate_names():
    names = [t.name for t in _tools()]
    assert len(names) == len(set(names))


def test_all_described():
    for tool in _tools():
        assert tool.description and tool.description.strip(), f"{tool.name} has no description"


def test_input_schemas_are_objects():
    for tool in _tools():
        schema = tool.inputSchema
        assert schema.get("type") == "object", f"{tool.name}: inputSchema.type != object"
        assert isinstance(schema.get("properties", {}), dict)


def test_annotations_structure():
    annotated = 0
    for tool in _tools():
        if tool.annotations is None:
            continue
        annotated += 1
        ann = tool.annotations
        assert isinstance(ann.readOnlyHint, bool), tool.name
        assert isinstance(ann.destructiveHint, bool), tool.name
        if ann.destructiveHint:
            assert not ann.readOnlyHint, f"{tool.name}: destructive but marked read-only"
    assert annotated >= 61, f"expected >=61 annotated tools, got {annotated}"


def test_known_mutation_is_not_readonly():
    by_name = {t.name: t for t in _tools()}
    assert "nexus_endorse_mod" in by_name
    ann = by_name["nexus_endorse_mod"].annotations
    assert ann is not None
    assert ann.readOnlyHint is False


def test_server_info_version_matches_package():
    from nexus_mcp._core import APP_VERSION

    mcp_version = nexus_mcp.mcp._mcp_server.version
    assert mcp_version == nexus_mcp.__version__
    assert mcp_version == APP_VERSION


def test_server_website_url():
    assert nexus_mcp.mcp._mcp_server.website_url == "https://github.com/Talya1412/nexus-mcp"
