"""Offline tests for the domain_name slug validation guarding API-calling tools."""
import asyncio

import httpx
from pydantic.fields import FieldInfo

import nexus_mcp._core as core
from nexus_mcp._core import _check_domain
from nexus_mcp.tools.v1_rest import nexus_get_game, nexus_track_mod
from nexus_mcp.tools.v2_content import nexus_get_categories, nexus_get_collection_revision
from nexus_mcp.tools.v2_search import nexus_get_game_v2, nexus_search_mods


def run(coro):
    return asyncio.run(coro)


def install(handler):
    core._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=core.API_BASE
    )


VALID_SLUGS = ["skyrimse", "forzahorizon6", "skyrimspecialedition", "a1-b2-c3"]
INVALID_VALUES = [
    "Skyrim Special Edition",  # display name instead of slug
    "SkyrimSE",  # uppercase
    "skyrim se",  # space
    "../user",  # path traversal
    "skyrim/mods",  # path segment
    "",  # empty string
    "sk_yrim",  # underscore
    "skyrim!",  # punctuation
    42,  # wrong type
    FieldInfo(),  # direct-call artifact: unpassed Field default arrives as FieldInfo
]


class TestCheckDomain:
    def test_valid_slugs_pass(self):
        for slug in VALID_SLUGS:
            assert _check_domain(slug) is None, slug

    def test_none_means_omitted(self):
        # optional-domain tools forward None as-is; it must not be rejected
        assert _check_domain(None) is None

    def test_invalid_values_rejected(self):
        for bad in INVALID_VALUES:
            err = _check_domain(bad)
            assert err is not None and err.startswith("Error: invalid domain_name"), repr(bad)
            assert "nexus_resolve_domain" in err


class TestToolGuards:
    """Guards return before any HTTP request - no client needed for invalid slugs."""

    def test_rest_read_guard(self):
        out = run(nexus_get_game("Skyrim Special Edition"))
        assert out.startswith("Error: invalid domain_name")

    def test_rest_mutation_guard(self):
        out = run(nexus_track_mod("Bad/Slug", 42))
        assert out.startswith("Error: invalid domain_name")

    def test_v2_required_guard(self):
        out = run(nexus_get_game_v2("Bad Slug"))
        assert out.startswith("Error: invalid domain_name")

    def test_v2_optional_guard_when_provided(self):
        out = run(nexus_search_mods(domain_name="Bad Slug"))
        assert out.startswith("Error: invalid domain_name")

    def test_v2_optional_guard_skipped_when_omitted(self):
        def handler(request):
            return httpx.Response(
                200, json={"data": {"mods": {"totalCount": 0, "nodes": []}}, "errors": None}
            )

        install(handler)
        # every param explicit: direct calls receive FieldInfo defaults otherwise
        out = run(
            nexus_search_mods(
                term=None,
                domain_name=None,
                sort="endorsements",
                direction="DESC",
                min_endorsements=None,
                min_downloads=None,
                exclude_adult=False,
                offset=0,
                count=20,
            )
        )
        assert "invalid domain_name" not in out

    def test_categories_guard_with_explicit_flag(self):
        # is_global must be passed explicitly: direct call would deliver FieldInfo (truthy)
        out = run(nexus_get_categories(domain_name="Bad Slug", is_global=False))
        assert out.startswith("Error: invalid domain_name")

    def test_collection_revision_guard(self):
        out = run(nexus_get_collection_revision(domain_name="Bad Slug"))
        assert out.startswith("Error: invalid domain_name")
