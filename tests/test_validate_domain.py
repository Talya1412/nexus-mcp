"""Tests for _validate_domain() in nexus_mcp._core (issue #1)."""
import pytest

import nexus_mcp._core as core
from nexus_mcp._core import NexusApiError


class TestValidateDomainValidSlugs:
    """Valid slugs should pass through, optionally normalised."""

    @pytest.mark.parametrize("slug", [
        "skyrimse",
        "forzahorizon6",
        "witcher3",
        "darksouls2",
        "a",
        "123",
        "mod-manager",
        "game-2",
    ])
    def test_valid_slug_passes(self, slug: str) -> None:
        assert core._validate_domain(slug) == slug

    def test_strips_surrounding_whitespace(self) -> None:
        assert core._validate_domain("  skyrimse  ") == "skyrimse"

    def test_lowercases_input(self) -> None:
        assert core._validate_domain("SkyrimSE") == "skyrimse"

    def test_strips_and_lowercases_together(self) -> None:
        assert core._validate_domain("  ForzaHorizon6  ") == "forzahorizon6"


class TestValidateDomainInvalidSlugs:
    """Display names and malformed slugs must raise NexusApiError."""

    @pytest.mark.parametrize("bad", [
        "Skyrim Special Edition",   # display name with spaces
        "The Witcher 3",            # spaces and capital
        "forza/horizon6",           # slash
        "mod_manager",              # underscore (not a valid slug char)
        "game.exe",                 # dot
        "",                         # empty string
        "   ",                      # whitespace only
        "skyrim\tse",               # tab inside
    ])
    def test_invalid_slug_raises(self, bad: str) -> None:
        with pytest.raises(NexusApiError):
            core._validate_domain(bad)

    def test_error_message_mentions_slug_format(self) -> None:
        with pytest.raises(NexusApiError, match="lowercase URL slug"):
            core._validate_domain("Skyrim Special Edition")

    def test_error_message_echoes_bad_value(self) -> None:
        with pytest.raises(NexusApiError, match="forza horizon6"):
            core._validate_domain("forza horizon6")

    def test_empty_string_error_mentions_empty(self) -> None:
        with pytest.raises(NexusApiError, match="empty"):
            core._validate_domain("")
