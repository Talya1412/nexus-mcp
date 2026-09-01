"""OAuth + auth-header resolution tests: apikey fallback, Bearer, refresh, revocation."""
import asyncio
import json
import os
import time

import pytest

import nexus_mcp._core as core


def run(coro):
    return asyncio.run(coro)


def _write_tokens(**overrides):
    tokens = {
        "access_token": "old-access",
        "refresh_token": "rt-1",
        "token_type": "Bearer",
        "created_at": int(time.time()) - 7200,
        "expires_at": int(time.time()) - 60,  # expired
    }
    tokens.update(overrides)
    core._save_oauth_tokens(tokens)
    return tokens


class TestAuthHeaders:
    def test_apikey_fallback_without_tokens(self):
        assert run(core._auth_headers()) == {"apikey": "test-key-123"}

    def test_bearer_when_token_valid(self):
        _write_tokens(expires_at=int(time.time()) + 3600)
        headers = run(core._auth_headers())
        assert headers == {"Authorization": "Bearer old-access"}

    def test_refresh_on_expiry(self, monkeypatch):
        _write_tokens()

        async def fake_token_request(form):
            assert form["grant_type"] == "refresh_token"
            assert form["refresh_token"] == "rt-1"
            return {"access_token": "new-access", "refresh_token": "rt-2", "expires_in": 3600}

        monkeypatch.setattr(core, "_oauth_token_request", fake_token_request)
        headers = run(core._auth_headers())
        assert headers == {"Authorization": "Bearer new-access"}
        saved = core._load_oauth_tokens()
        assert saved["access_token"] == "new-access"
        assert saved["expires_at"] > time.time()

    def test_revoked_app_clears_tokens_and_falls_back(self, monkeypatch):
        _write_tokens()

        async def fake_token_request(form):
            raise core.NexusApiError("OAuth token request failed: invalid_grant", status=400)

        monkeypatch.setattr(core, "_oauth_token_request", fake_token_request)
        headers = run(core._auth_headers())
        assert headers == {"apikey": "test-key-123"}
        assert core._load_oauth_tokens() is None

    def test_transient_refresh_failure_keeps_tokens(self, monkeypatch):
        """A 5xx (or status-less) refresh failure must NOT log the user out."""
        _write_tokens()

        async def fake_token_request(form):
            raise core.NexusApiError("OAuth token request failed: HTTP 503", status=503)

        monkeypatch.setattr(core, "_oauth_token_request", fake_token_request)
        with pytest.raises(core.NexusApiError, match="503"):
            run(core._auth_headers())
        saved = core._load_oauth_tokens()
        assert saved is not None
        assert saved["refresh_token"] == "rt-1"

    def test_refresh_race_single_flight(self, monkeypatch):
        """While one refresh is in flight, a second caller gets the fresh tokens."""
        _write_tokens()
        calls = []

        async def fake_token_request(form):
            calls.append(form["refresh_token"])
            await asyncio.sleep(0.01)
            return {"access_token": f"new-{len(calls)}", "refresh_token": "rt-2", "expires_in": 3600}

        monkeypatch.setattr(core, "_oauth_token_request", fake_token_request)
        tokens = core._load_oauth_tokens()

        async def two_refreshes():
            return await asyncio.gather(core._oauth_refresh(tokens), core._oauth_refresh(tokens))

        results = run(two_refreshes())
        assert len(calls) == 1  # second waiter picked up the saved fresh tokens instead
        assert all(r is not None and r["access_token"].startswith("new-") for r in results)

    def test_no_credentials_raises(self, monkeypatch, tmp_path):
        monkeypatch.delenv("NEXUS_API_KEY", raising=False)
        with pytest.raises(core.NexusApiError, match="No authentication available"):
            run(core._auth_headers())

    def test_bearer_takes_precedence_over_apikey(self):
        _write_tokens(expires_at=int(time.time()) + 3600)
        headers = run(core._auth_headers())
        assert "apikey" not in headers


class TestTokenStore:
    def test_save_load_roundtrip(self):
        tokens = {"access_token": "a", "expires_at": 1}
        core._save_oauth_tokens(tokens)
        assert core._load_oauth_tokens() == tokens

    def test_missing_file_returns_none(self):
        assert core._load_oauth_tokens() is None

    def test_corrupt_file_returns_none(self):
        core._oauth_token_file().write_text("not json{", encoding="utf-8")
        assert core._load_oauth_tokens() is None

    def test_clear_removes_file(self):
        core._save_oauth_tokens({"access_token": "a"})
        core._clear_oauth_tokens()
        assert not core._oauth_token_file().exists()

    def test_clear_missing_file_is_noop(self):
        core._clear_oauth_tokens()  # must not raise

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permissions only")
    def test_saved_token_file_owner_only(self):
        core._save_oauth_tokens({"access_token": "a"})
        mode = core._oauth_token_file().stat().st_mode & 0o777
        assert mode == 0o600


class TestOauthHelpers:
    def test_client_id_from_env(self, monkeypatch):
        monkeypatch.setenv("NEXUS_OAUTH_CLIENT_ID", " my-app ")
        assert core._oauth_client_id() == "my-app"

    def test_redirect_uri_default(self, monkeypatch):
        monkeypatch.delenv("NEXUS_OAUTH_REDIRECT_URI", raising=False)
        assert core._oauth_redirect_uri() == "http://localhost/callback"

    def test_token_file_from_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NEXUS_OAUTH_TOKEN_FILE", str(tmp_path / "custom.json"))
        core._save_oauth_tokens({"access_token": "x"})
        assert json.loads((tmp_path / "custom.json").read_text())["access_token"] == "x"
