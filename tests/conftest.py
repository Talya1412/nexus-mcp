"""Shared fixtures: isolated env vars, clean module-level state between tests."""
import pytest

import nexus_mcp._core as core


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_API_KEY", "test-key-123")
    monkeypatch.delenv("NEXUS_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("NEXUS_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("NEXUS_OAUTH_REDIRECT_URI", raising=False)
    monkeypatch.setenv("NEXUS_OAUTH_TOKEN_FILE", str(tmp_path / "tokens.json"))
    core._CACHE.clear()
    core._client = None
    core._oauth_pending = None
    yield
    core._CACHE.clear()
    core._client = None
    core._oauth_pending = None
