"""Tools: OAuth2 login/exchange/status/refresh/logout."""

from __future__ import annotations

from typing import Any, Optional

import json
import secrets
import time
import urllib.parse

from pydantic import Field

from .._core import (
    NexusApiError,
    OAUTH_AUTHORIZE_URL,
    _clear_oauth_tokens,
    _gql_call,
    _load_oauth_tokens,
    _oauth_client_id,
    _oauth_client_secret,
    _oauth_pending,
    _oauth_redirect_uri,
    _oauth_refresh,
    _oauth_token_file,
    _oauth_token_request,
    _pkce_pair,
    _save_oauth_tokens,
    _tokens_from_reply,
)

from .._annotations import (
    _IDEMPOTENT_MUTATION_ANNOTATIONS,
    _MUTATING_ANNOTATIONS,
    _READ_ONLY_ANNOTATIONS,
)

from .._server import mcp
from .v1_rest import nexus_validate_key

_OAUTH_REGISTER_NOTE = (
    "OAuth applications on Nexus Mods are registered by EMAILING support@nexusmods.com "
    "(name, short description, logo, source link, callback URI) - there is no self-serve web UI yet. "
    "Then set NEXUS_OAUTH_CLIENT_ID (and optionally NEXUS_OAUTH_CLIENT_SECRET / "
    "NEXUS_OAUTH_REDIRECT_URI) in the MCP server 'environment' config."
)


@mcp.tool(name="nexus_oauth_login", annotations={**_MUTATING_ANNOTATIONS, "title": "Start Nexus OAuth login"})
async def nexus_oauth_login(
    scope: str = Field(default="public", description="Space-separated OAuth scope. 'public' (or '') suffices for API access; 'public openid' adds identity."),
    redirect_uri: Optional[str] = Field(default=None, description="Override the callback URI. Must match the one registered with Nexus; override with env NEXUS_OAUTH_REDIRECT_URI."),
) -> str:
    """Start the OAuth2 authorization-code flow: returns the URL to open in a browser.

    Uses PKCE S256 + random state (per the official Nexus OAuth guide). After
    approving, copy the 'code' query parameter from the final redirect URL and
    pass it to nexus_oauth_exchange. Bearer tokens then take precedence over
    NEXUS_API_KEY and unlock user-context mutations.

    Returns:
        JSON {authorize_url, redirect_uri, state, instructions}.
    """
    global _oauth_pending
    client_id = _oauth_client_id()
    if not client_id:
        return f"Error: NEXUS_OAUTH_CLIENT_ID is not set. {_OAUTH_REGISTER_NOTE}"
    if not isinstance(redirect_uri, str):
        redirect_uri = None
    ru = redirect_uri or _oauth_redirect_uri()
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    _oauth_pending = {"verifier": verifier, "state": state, "redirect_uri": ru, "scope": scope}
    url = OAUTH_AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "scope": scope,
        "redirect_uri": ru,
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    })
    return json.dumps({
        "authorize_url": url,
        "redirect_uri": ru,
        "state": state,
        "instructions": (
            "1. Open authorize_url in a browser and approve the application. "
            "2. From the final redirect URL copy the 'code' query parameter "
            "(e.g. http://localhost/callback?code=XXXX -> XXXX). "
            "3. Call nexus_oauth_exchange with that code."
        ),
    }, indent=2)


@mcp.tool(name="nexus_oauth_exchange", annotations={**_MUTATING_ANNOTATIONS, "title": "Complete Nexus OAuth login"})
async def nexus_oauth_exchange(
    code: str = Field(..., description="The 'code' query parameter from the OAuth redirect after nexus_oauth_login.", min_length=1),
    state: Optional[str] = Field(default=None, description="Optional 'state' value from the redirect URL; verified against the pending login when provided."),
) -> str:
    """Complete the OAuth2 flow: exchange the authorization code for tokens.

    Exchanges via PKCE code_verifier (+ client_secret when configured), saves
    tokens to the token file, and validates the identity with the resulting
    Bearer token. Tokens auto-refresh; any 4xx refresh response is treated as
    revocation per the official guide.

    Returns:
        JSON account summary from nexus_validate_key, or an error string.
    """
    global _oauth_pending
    pending = _oauth_pending
    if not pending:
        return "Error: no pending login. Run nexus_oauth_login first."
    if not isinstance(state, str):
        state = None
    if state and state != pending["state"]:
        return "Error: state mismatch - restart the login with nexus_oauth_login."
    client_id = _oauth_client_id()
    if not client_id:
        return f"Error: NEXUS_OAUTH_CLIENT_ID is not set. {_OAUTH_REGISTER_NOTE}"
    form = {
        "grant_type": "authorization_code",
        "redirect_uri": pending["redirect_uri"],
        "scope": pending["scope"],
        "client_id": client_id,
        "code": code.strip(),
        "code_verifier": pending["verifier"],
    }
    if _oauth_client_secret():
        form["client_secret"] = _oauth_client_secret()
    try:
        reply = await _oauth_token_request(form)
    except NexusApiError as exc:
        return f"Error: {exc}"
    _save_oauth_tokens(_tokens_from_reply(reply))
    _oauth_pending = None
    return await nexus_validate_key()


@mcp.tool(name="nexus_oauth_status", annotations={**_READ_ONLY_ANNOTATIONS, "title": "OAuth status"})
async def nexus_oauth_status() -> str:
    """Report the OAuth login state: configured env vars, token expiry, scope.

    Never exposes the access token itself - only a prefix and expiry metadata.

    Returns:
        JSON {configured, logged_in, expires_at, scope, has_refresh_token, token_file}.
    """
    tokens = _load_oauth_tokens()
    info: dict[str, Any] = {
        "client_id_configured": bool(_oauth_client_id()),
        "client_secret_configured": bool(_oauth_client_secret()),
        "redirect_uri": _oauth_redirect_uri(),
        "pending_login": _oauth_pending is not None,
        "logged_in": False,
        "token_file": str(_oauth_token_file()),
    }
    if tokens:
        info.update({
            "logged_in": bool(tokens.get("access_token")),
            "token_prefix": str(tokens.get("access_token", ""))[:12] + "...",
            "expires_at": tokens.get("expires_at"),
            "expires_in_seconds": max(0, int(tokens.get("expires_at", 0)) - int(time.time())) if tokens.get("expires_at") else None,
            "scope": tokens.get("scope"),
            "has_refresh_token": bool(tokens.get("refresh_token")),
        })
    return json.dumps(info, indent=2)


@mcp.tool(name="nexus_oauth_refresh", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Force OAuth token refresh"})
async def nexus_oauth_refresh() -> str:
    """Force a refresh of the OAuth access token via the refresh grant.

    Use when a request unexpectedly returns 401. A 4xx refresh failure means
    the user revoked the application (see https://users.nexusmods.com/oauth/authorized_applications)
    and the stored tokens are cleared - log in again with nexus_oauth_login.

    Returns:
        JSON {refreshed, expires_at, scope} or an error string.
    """
    tokens = _load_oauth_tokens()
    if not tokens:
        return "Error: not logged in. Run nexus_oauth_login."
    refreshed = await _oauth_refresh(tokens)
    if not refreshed:
        return "Error: refresh failed (token revoked or invalid). Log in again with nexus_oauth_login."
    return json.dumps({
        "refreshed": True,
        "expires_at": refreshed["expires_at"],
        "scope": refreshed.get("scope"),
    }, indent=2)


@mcp.tool(name="nexus_oauth_logout", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Log out of OAuth"})
async def nexus_oauth_logout() -> str:
    """Delete stored OAuth tokens and fall back to NEXUS_API_KEY authentication.

    Removes the local token file. To fully revoke access, also remove the
    application at https://users.nexusmods.com/oauth/authorized_applications.

    Returns:
        JSON {logged_out: true}.
    """
    global _oauth_pending
    _clear_oauth_tokens()
    _oauth_pending = None
    return json.dumps({"logged_out": True})


@mcp.tool(name="nexus_update_mod_direct_download", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Toggle mod direct download (v2)"})
async def nexus_update_mod_direct_download(
    mod_uid: str = Field(..., description="Mod UID (numeric string, e.g. from nexus_get_mod 'uid')."),
    enabled: bool = Field(..., description="True to enable direct downloads for this mod, false to disable."),
) -> str:
    """Enable or disable direct (no-ads) downloads on your own mod via v2 GraphQL.

    REQUIRES OAuth login (nexus_oauth_login + nexus_oauth_exchange): this
    user-context mutation is rejected under apikey-only auth even for the
    mod owner. Useful for mod authors automating release pipelines.

    Returns:
        JSON payload from Nexus or an error string.
    """
    return await _gql_call(
        "mutation($u: ID!, $e: Boolean!) { updateModDirectDownloadEnabled(modUid: $u, directDownloadEnabled: $e) { ... on UpdateModDirectDownloadEnabledMutationPayload { success } } }",
        {"u": mod_uid, "e": enabled},
    )
