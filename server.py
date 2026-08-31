#!/usr/bin/env python3
"""Nexus Mods MCP server.

Wraps the official Nexus Mods REST API v1 (https://api.nexusmods.com) as MCP
tools: validate key, browse games, inspect mods/files, get download links,
search by MD5, and manage tracked mods / endorsements.

Authentication: NEXUS_API_KEY (personal API key, https://www.nexusmods.com/users/myaccount?tab=api%20access)
and optionally OAuth2 (NEXUS_OAUTH_CLIENT_ID [+ NEXUS_OAUTH_CLIENT_SECRET], registered by emailing
support@nexusmods.com) which unlocks user-context mutations like updateModDirectDownloadEnabled.
OAuth tokens are persisted to ~/.nexus-mcp/oauth-tokens.json (override with NEXUS_OAUTH_TOKEN_FILE)
and auto-refreshed; Bearer auth takes precedence over apikey when logged in.

Docs: https://app.swaggerhub.com/apis-docs/NexusMods/nexus-mods_public_api_params_in_form_data/1.0
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Literal, Optional

import httpx
from pydantic import Field
from mcp.server.fastmcp import FastMCP

API_BASE = "https://api.nexusmods.com"
GRAPHQL_PATH = "/v2/graphql"
APP_NAME = "nexus-mcp"
APP_VERSION = "1.0.0"

RL_HEADERS = [
    "X-RL-Hourly-Limit",
    "X-RL-Hourly-Remaining",
    "X-RL-Hourly-Reset",
    "X-RL-Daily-Limit",
    "X-RL-Daily-Remaining",
    "X-RL-Daily-Reset",
]

DOMAIN_DESC = "Nexus Mods game domain (lowercase URL slug), e.g. 'forzahorizon6', 'skyrimse'. NOT the display name."

mcp = FastMCP("nexus_mcp")


class NexusApiError(Exception):
    """Raised for config, network, or API-level failures with actionable messages."""


# ---------------------------------------------------------------------------
# Shared client + helpers
# ---------------------------------------------------------------------------

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    """Lazily create the shared async HTTP client. Auth headers are applied per-request."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=API_BASE,
            headers={
                "User-Agent": f"{APP_NAME}/{APP_VERSION} ({sys.platform}; Python httpx)",
                "Application-Name": APP_NAME,
                "Application-Version": APP_VERSION,
                "Accept": "application/json",
            },
            timeout=30.0,
        )
    return _client


# ---------------------------------------------------------------------------
# OAuth2 (optional) - Bearer auth unlocks user-context mutations; apikey fallback
# ---------------------------------------------------------------------------

OAUTH_AUTHORIZE_URL = "https://users.nexusmods.com/oauth/authorize"
OAUTH_TOKEN_URL = "https://users.nexusmods.com/oauth/token"
OAUTH_REFRESH_MARGIN = 60  # refresh this many seconds before expiry

_oauth_pending: Optional[dict[str, str]] = None


def _oauth_client_id() -> str:
    return os.environ.get("NEXUS_OAUTH_CLIENT_ID", "").strip()


def _oauth_client_secret() -> str:
    return os.environ.get("NEXUS_OAUTH_CLIENT_SECRET", "").strip()


def _oauth_redirect_uri() -> str:
    return os.environ.get("NEXUS_OAUTH_REDIRECT_URI", "").strip() or "http://localhost/callback"


def _oauth_token_file() -> Path:
    return Path(os.environ.get("NEXUS_OAUTH_TOKEN_FILE", "")).expanduser() if os.environ.get("NEXUS_OAUTH_TOKEN_FILE") else Path.home() / ".nexus-mcp" / "oauth-tokens.json"


def _load_oauth_tokens() -> Optional[dict[str, Any]]:
    try:
        return json.loads(_oauth_token_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_oauth_tokens(tokens: dict[str, Any]) -> None:
    path = _oauth_token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")


def _clear_oauth_tokens() -> None:
    try:
        _oauth_token_file().unlink()
    except OSError:
        pass


def _pkce_pair() -> tuple[str, str]:
    """PKCE S256 pair (verifier >= 43 chars per RFC 7636; S256 required for public apps)."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


async def _oauth_token_request(form: dict[str, str]) -> dict[str, Any]:
    """POST to the token endpoint (form-encoded); returns the parsed JSON token reply."""
    async with httpx.AsyncClient(timeout=30.0) as hc:
        response = await hc.post(OAUTH_TOKEN_URL, data=form, headers={"Accept": "application/json"})
    try:
        body = response.json()
    except json.JSONDecodeError:
        raise NexusApiError(f"OAuth token endpoint returned HTTP {response.status_code} with a non-JSON body.")
    if response.status_code != 200 or "error" in body:
        detail = body.get("error_description") or body.get("error") or f"HTTP {response.status_code}"
        raise NexusApiError(f"OAuth token request failed: {detail}")
    return body


def _tokens_from_reply(reply: dict[str, Any]) -> dict[str, Any]:
    expires_in = int(reply.get("expires_in") or 21600)
    return {
        "access_token": reply["access_token"],
        "refresh_token": reply.get("refresh_token"),
        "token_type": reply.get("token_type", "Bearer"),
        "scope": reply.get("scope"),
        "created_at": reply.get("created_at") or int(time.time()),
        "expires_at": int(time.time()) + expires_in - OAUTH_REFRESH_MARGIN,
    }


async def _oauth_refresh(tokens: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Refresh the access token. Returns new tokens, or None if revoked/invalid (tokens cleared)."""
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        return None
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": _oauth_client_id(),
    }
    if _oauth_client_secret():
        form["client_secret"] = _oauth_client_secret()
    try:
        reply = await _oauth_token_request(form)
    except NexusApiError:
        # Per the official guide, a 4xx on refresh means the user revoked the app
        _clear_oauth_tokens()
        return None
    new_tokens = _tokens_from_reply(reply)
    _save_oauth_tokens(new_tokens)
    return new_tokens


async def _auth_headers() -> dict[str, str]:
    """Per-request auth headers: OAuth Bearer when valid (auto-refresh), else apikey."""
    tokens = _load_oauth_tokens()
    if tokens and tokens.get("access_token"):
        if tokens.get("expires_at", 0) > time.time():
            return {"Authorization": f"Bearer {tokens['access_token']}"}
        refreshed = await _oauth_refresh(tokens)
        if refreshed:
            return {"Authorization": f"Bearer {refreshed['access_token']}"}
    api_key = os.environ.get("NEXUS_API_KEY", "").strip()
    if not api_key:
        raise NexusApiError(
            "No authentication available: NEXUS_API_KEY is not set and there is no valid OAuth token. "
            "Set NEXUS_API_KEY or run nexus_oauth_login."
        )
    return {"apikey": api_key}


# ---------------------------------------------------------------------------
# TTL cache - repeated identical GETs within a session do not consume quota
# ---------------------------------------------------------------------------

_CACHE: dict[str, tuple[float, Any, dict[str, str]]] = {}


def _ttl_for(path: str) -> int:
    """Client-side cache TTL (seconds) by endpoint class. 0 = never cache."""
    if path == "/v1/games.json":
        return 3600
    if re.fullmatch(r"/v1/games/[^/]+\.json", path):
        return 3600
    if "/user" in path:
        return 0  # personal state (validate/tracked/endorsements) must be fresh
    if "/mods/" in path or "/files/" in path:
        return 300  # public mod/file data - Nexus itself caches these 5 minutes
    return 0


def _cache_key(method: str, path: str, params: Optional[dict[str, Any]], data: Optional[dict[str, Any]]) -> str:
    return json.dumps([method, path, params or {}, data or {}], sort_keys=True)


def _rl_snapshot(response: httpx.Response) -> dict[str, str]:
    """Extract Nexus rate-limit headers (header names may be lowercased)."""
    rl: dict[str, str] = {}
    for name in RL_HEADERS:
        value = response.headers.get(name) or response.headers.get(name.lower())
        if value is not None:
            rl[name.lower()] = value
    return rl


def _status_hint(status: int) -> str:
    hints = {
        400: "Bad request - for download_link.json, 'key'/'expires' must come from a .nxm link.",
        401: "Invalid or missing credentials. Check NEXUS_API_KEY or refresh the OAuth login.",
        403: (
            "Not permitted. For download_link.json: non-premium users MUST pass 'key' and "
            "'expires' query params taken from the .nxm download link (premium users can omit them)."
        ),
        404: (
            "Not found. Verify domain_name (lowercase game slug, e.g. 'forzahorizon6', not the "
            "display name) and the mod_id/file_id."
        ),
        410: "Download link expired. Request a fresh .nxm link from the Nexus website.",
        422: "Unprocessable request - check parameter formats.",
    }
    return hints.get(status, "")


async def _api(
    method: str,
    path: str,
    *,
    params: Optional[dict[str, Any]] = None,
    data: Optional[dict[str, Any]] = None,
    ttl: int = 0,
) -> tuple[Any, dict[str, str]]:
    """Perform an authenticated API request; returns (json payload, rate-limit headers)."""
    key = _cache_key(method, path, params, data)
    ttl = ttl or (_ttl_for(path) if method == "GET" else 0)
    if ttl:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] > time.monotonic():
            return hit[1], hit[2]

    payload, rl = await _request(method, path, params, data)

    if ttl:
        _CACHE[key] = (time.monotonic() + ttl, payload, rl)
    return payload, rl


async def _request(
    method: str,
    path: str,
    params: Optional[dict[str, Any]],
    data: Optional[dict[str, Any]],
) -> tuple[Any, dict[str, str]]:
    client = _get_client()
    try:
        response = await client.request(method, path, params=params, data=data, headers=await _auth_headers())
    except httpx.TimeoutException:
        raise NexusApiError("Request timed out after 30s. Try again.")
    except httpx.HTTPError as exc:
        raise NexusApiError(f"Network error: {type(exc).__name__}: {exc}")

    rl = _rl_snapshot(response)

    if response.status_code == 202:
        raise NexusApiError(
            "API returned 202 (accepted but not processed in time). Treat as a timeout: "
            "the side effect may or may not have been applied - re-check the resource state."
        )
    if response.status_code == 429:
        raise NexusApiError(
            f"Rate limit exceeded (quota or >30 requests/second). Remaining quota: "
            f"{json.dumps(rl) if rl else 'unknown'}. Wait for the reset window before retrying."
        )
    if response.status_code >= 400:
        detail = ""
        try:
            body = response.json()
            if isinstance(body, dict) and body.get("message"):
                detail = f" API says: {body['message']}"
        except json.JSONDecodeError:
            pass
        raise NexusApiError(
            f"API error {response.status_code}{detail}. {_status_hint(response.status_code)}".strip()
        )

    try:
        return response.json(), rl
    except json.JSONDecodeError:
        raise NexusApiError(
            "API returned a non-JSON (HTML) response - possibly a firewall/CDN error page. Retry."
        )


def _dump(payload: Any, rl: dict[str, str]) -> str:
    """Serialize a payload with a compact rate-limit snapshot appended."""
    body: Any = {**payload, "_rl": rl} if isinstance(payload, dict) else {"result": payload, "_rl": rl}
    return json.dumps(body, indent=2, ensure_ascii=False)


async def _call(
    method: str,
    path: str,
    *,
    params: Optional[dict[str, Any]] = None,
    data: Optional[dict[str, Any]] = None,
) -> str:
    try:
        payload, rl = await _api(method, path, params=params, data=data)
        return _dump(payload, rl)
    except NexusApiError as exc:
        return f"Error: {exc}"


GRAPHQL_TTL = 60  # GraphQL POSTs cached briefly - repeated identical queries within a session are cheap


async def _graphql(query: str, variables: Optional[dict[str, Any]] = None) -> tuple[Any, dict[str, str]]:
    """Execute a Nexus v2 GraphQL query (POST JSON); returns (data, rate-limit headers).

    v2 GraphQL has a rate-limit pool separate from v1 REST, so search/detail
    queries here do not consume the v1 quota.
    """
    body = {"query": query, "variables": variables or {}}
    key = _cache_key("POST", GRAPHQL_PATH, None, body)
    hit = _CACHE.get(key)
    if hit is not None and hit[0] > time.monotonic():
        return hit[1], hit[2]

    client = _get_client()
    try:
        response = await client.post(GRAPHQL_PATH, json=body, headers=await _auth_headers())
    except httpx.TimeoutException:
        raise NexusApiError("Request timed out after 30s. Try again.")
    except httpx.HTTPError as exc:
        raise NexusApiError(f"Network error: {type(exc).__name__}: {exc}")

    rl = _rl_snapshot(response)
    if response.status_code >= 400:
        raise NexusApiError(f"GraphQL error {response.status_code}. {_status_hint(response.status_code)}".strip())
    try:
        result = response.json()
    except json.JSONDecodeError:
        raise NexusApiError("GraphQL endpoint returned a non-JSON response - possibly a firewall/CDN error page. Retry.")

    errors = result.get("errors")
    if errors:
        msgs = "; ".join(str(e.get("message", e)) for e in errors if isinstance(e, dict)) or json.dumps(errors)
        raise NexusApiError(f"GraphQL query failed: {msgs}")

    data = result.get("data")
    _CACHE[key] = (time.monotonic() + GRAPHQL_TTL, data, rl)
    return data, rl


async def _gql_call(query: str, variables: Optional[dict[str, Any]] = None) -> str:
    try:
        data, rl = await _graphql(query, variables)
        return _dump(data, rl)
    except NexusApiError as exc:
        return f"Error: {exc}"


# ---------------------------------------------------------------------------
# Tools: account
# ---------------------------------------------------------------------------

@mcp.tool(
    name="nexus_validate_key",
    annotations={
        "title": "Validate Nexus Mods API key",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def nexus_validate_key() -> str:
    """Validate the configured Nexus Mods API key and identify the account.

    Returns user_id, username, is_premium, is_supporter, email, and profile_url.
    This endpoint is exempt from hourly rate limits - safe to use to check the
    key works or to probe remaining quota via the '_rl' rate-limit snapshot.
    """
    return await _call("GET", "/v1/users/validate.json")


# ---------------------------------------------------------------------------
# Tools: games
# ---------------------------------------------------------------------------

@mcp.tool(
    name="nexus_get_games",
    annotations={
        "title": "List Nexus games",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def nexus_get_games(
    include_unapproved: bool = Field(default=False, description="Include games that are not yet fully approved on Nexus."),
    filter: Optional[str] = Field(
        default=None,
        description="Optional case-insensitive substring filter applied client-side to game name/domain_name (reduces output size).",
    ),
) -> str:
    """List all games on Nexus Mods with their domain_name, mod counts, and file counts.

    Use this to find the correct lowercase domain_name slug for other tools.

    Returns:
        JSON array of games: {id, domain_name, name, genre, mods, file_count,
        downloads, approved_date, collections}. With '_rl' rate-limit snapshot.
    """
    query = {"include_unapproved": str(include_unapproved).lower()}
    try:
        payload, rl = await _api("GET", "/v1/games.json", params=query)
    except NexusApiError as exc:
        return f"Error: {exc}"

    if filter:
        needle = filter.lower()
        payload = [
            g
            for g in payload
            if needle in str(g.get("name", "")).lower() or needle in str(g.get("domain_name", "")).lower()
        ]
    return _dump(payload, rl)


@mcp.tool(
    name="nexus_get_game",
    annotations={
        "title": "Get game details",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def nexus_get_game(
    domain_name: str = Field(..., description=DOMAIN_DESC),
) -> str:
    """Get details for one game: stats, file counts, download counts, and its file/mod categories.

    Returns:
        JSON object with game info including a 'categories' list (category_id,
        name, parent_category).
    """
    return await _call("GET", f"/v1/games/{domain_name}.json")


# ---------------------------------------------------------------------------
# Tools: mods
# ---------------------------------------------------------------------------

@mcp.tool(
    name="nexus_get_mod",
    annotations={
        "title": "Get mod details",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def nexus_get_mod(
    domain_name: str = Field(..., description=DOMAIN_DESC),
    mod_id: int = Field(..., description="Numeric mod ID from the mod page URL, e.g. 959 for forzahorizon6/mods/959.", ge=1),
) -> str:
    """Get full details for one mod: name, author, version, description, endorsement and download counts, upload dates.

    Returns:
        JSON mod info object (mod_id, name, summary, description BBCode, version,
        author, endorsement_count, mod_downloads, created/updated timestamps, ...).
        Server-cached 5 minutes.
    """
    return await _call("GET", f"/v1/games/{domain_name}/mods/{mod_id}.json")


@mcp.tool(
    name="nexus_get_mod_changelogs",
    annotations={
        "title": "Get mod changelogs",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def nexus_get_mod_changelogs(
    domain_name: str = Field(..., description=DOMAIN_DESC),
    mod_id: int = Field(..., description="Numeric mod ID.", ge=1),
) -> str:
    """Get the changelog history for one mod.

    Returns:
        JSON dict mapping version -> list of HTML changelog strings,
        e.g. {"1.1": ["<p>Fixed X</p>"], "1.2": ["<p>Added Y</p>"]}.
    """
    return await _call("GET", f"/v1/games/{domain_name}/mods/{mod_id}/changelogs.json")


@mcp.tool(
    name="nexus_get_latest_added",
    annotations={
        "title": "Get latest added mods",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def nexus_get_latest_added(
    domain_name: str = Field(..., description=DOMAIN_DESC),
) -> str:
    """Get the 10 most recently added mods for a game (array of mod info objects)."""
    return await _call("GET", f"/v1/games/{domain_name}/mods/latest_added.json")


@mcp.tool(
    name="nexus_get_latest_updated",
    annotations={
        "title": "Get latest updated mods",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def nexus_get_latest_updated(
    domain_name: str = Field(..., description=DOMAIN_DESC),
) -> str:
    """Get the 10 most recently updated mods for a game (array of mod info objects)."""
    return await _call("GET", f"/v1/games/{domain_name}/mods/latest_updated.json")


@mcp.tool(
    name="nexus_get_trending",
    annotations={
        "title": "Get trending mods",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def nexus_get_trending(
    domain_name: str = Field(..., description=DOMAIN_DESC),
) -> str:
    """Get the 10 currently trending mods for a game (array of mod info objects)."""
    return await _call("GET", f"/v1/games/{domain_name}/mods/trending.json")


@mcp.tool(
    name="nexus_get_updated_mods",
    annotations={
        "title": "Get mods updated in period",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def nexus_get_updated_mods(
    domain_name: str = Field(..., description=DOMAIN_DESC),
    period: Literal["1d", "1w", "1m"] = Field(..., description="Time window: '1d' (last day), '1w' (last week), or '1m' (last month)."),
) -> str:
    """Get all mods for a game whose files/activity changed within a time window.

    Returns:
        JSON array of {mod_id, latest_file_update (epoch), latest_mod_activity (epoch)}.
        Server-cached 5 minutes. Useful to check if your own mod page saw activity.
    """
    return await _call("GET", f"/v1/games/{domain_name}/mods/updated.json", params={"period": period})


# ---------------------------------------------------------------------------
# Tools: mod files
# ---------------------------------------------------------------------------

@mcp.tool(
    name="nexus_get_mod_files",
    annotations={
        "title": "List mod files",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def nexus_get_mod_files(
    domain_name: str = Field(..., description=DOMAIN_DESC),
    mod_id: int = Field(..., description="Numeric mod ID.", ge=1),
    category: Optional[str] = Field(
        default=None,
        description=(
            "Optional comma-separated file-category filter (case-insensitive). "
            "Valid values: main, update, optional, old_version, miscellaneous."
        ),
    ),
) -> str:
    """List all files attached to a mod, with versions, sizes, upload dates, and update chain.

    Returns:
        JSON {files: [...], file_updates: [{old_file_id, new_file_id, ...}]}.
        File category_id mapping: 1=MAIN, 2=UPDATE, 3=OPTIONAL, 4=OLD_VERSION,
        6=DELETED, 7=ARCHIVED.
    """
    query = {"category": category} if category else None
    return await _call("GET", f"/v1/games/{domain_name}/mods/{mod_id}/files.json", params=query)


@mcp.tool(
    name="nexus_get_file_info",
    annotations={
        "title": "Get file details",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def nexus_get_file_info(
    domain_name: str = Field(..., description=DOMAIN_DESC),
    mod_id: int = Field(..., description="Numeric mod ID.", ge=1),
    file_id: int = Field(..., description="Numeric file ID, e.g. 2291.", ge=1),
) -> str:
    """Get details for a single file of a mod: version, size, MD5, virus scan link, changelog."""
    return await _call("GET", f"/v1/games/{domain_name}/mods/{mod_id}/files/{file_id}.json")


@mcp.tool(
    name="nexus_get_download_link",
    annotations={
        "title": "Get mod file download link",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def nexus_get_download_link(
    domain_name: str = Field(..., description=DOMAIN_DESC),
    mod_id: int = Field(..., description="Numeric mod ID.", ge=1),
    file_id: int = Field(..., description="Numeric file ID.", ge=1),
    key: Optional[str] = Field(
        default=None,
        description="'key' from a .nxm download link. REQUIRED for non-premium accounts (403 otherwise).",
    ),
    expires: Optional[int] = Field(
        default=None,
        description="'expires' (unix epoch seconds, absolute timestamp) from a .nxm download link. REQUIRED for non-premium accounts.",
    ),
) -> str:
    """Get a short-lived download URL for a mod file from the Nexus CDN.

    Premium accounts can omit key/expires. Non-premium accounts MUST extract
    'key' and 'expires' from a .nxm download link (nxm://{domain}/mods/{mod}/
    files/{file}?key=...&expires=...) generated on the Nexus website.

    Returns:
        JSON array of {URI, name, short_name} mirrors - the first entry is the
        preferred location. Links are short-lived; do not cache them.
    """
    query: dict[str, Any] = {}
    if key is not None:
        query["key"] = key
    if expires is not None:
        query["expires"] = expires
    return await _call(
        "GET",
        f"/v1/games/{domain_name}/mods/{mod_id}/files/{file_id}/download_link.json",
        params=query or None,
    )


@mcp.tool(
    name="nexus_download_mod_file",
    annotations={
        "title": "Download a mod file to disk",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def nexus_download_mod_file(
    domain_name: str = Field(..., description=DOMAIN_DESC),
    mod_id: int = Field(..., description="Numeric mod ID.", ge=1),
    file_id: int = Field(..., description="Numeric file ID.", ge=1),
    destination: Optional[str] = Field(
        default=None,
        description="Directory to save the file into (created if missing). Defaults to the current working directory.",
    ),
    key: Optional[str] = Field(
        default=None,
        description="'key' from a .nxm download link. REQUIRED for non-premium accounts (403 otherwise).",
    ),
    expires: Optional[int] = Field(
        default=None,
        description="'expires' (unix epoch seconds) from a .nxm download link. REQUIRED for non-premium accounts.",
    ),
    max_bytes: int = Field(
        default=10 * 1024 * 1024 * 1024,
        description="Safety cap on download size in bytes; the transfer aborts past this (default 10 GiB).",
        ge=1,
    ),
) -> str:
    """Resolve a mod file's CDN link and stream it to a local file.

    Downloads the actual file behind nexus_get_download_link, saving it to disk
    with MD5 + SHA-256 checksums (verify MD5 against nexus_get_file_info).
    Premium accounts can omit key/expires; non-premium accounts MUST pass the
    key/expires pair from a .nxm download link generated on the Nexus website.

    Returns:
        JSON {file, bytes, md5, sha256, mirror, _rl} or an error string.
    """
    # Direct-call artifact: unpassed Optional Field params arrive as FieldInfo
    if not isinstance(destination, str):
        destination = None
    if not isinstance(key, str):
        key = None
    if not isinstance(expires, int):
        expires = None
    if not isinstance(max_bytes, int):
        max_bytes = 10 * 1024 * 1024 * 1024

    query: dict[str, Any] = {}
    if key is not None:
        query["key"] = key
    if expires is not None:
        query["expires"] = expires
    try:
        payload, rl = await _api(
            "GET",
            f"/v1/games/{domain_name}/mods/{mod_id}/files/{file_id}/download_link.json",
            params=query or None,
        )
    except NexusApiError as exc:
        return f"Error: {exc}"

    mirrors = payload if isinstance(payload, list) else (payload.get("result") if isinstance(payload, dict) else None)
    if not isinstance(mirrors, list) or not mirrors:
        return "Error: no download mirrors returned for this file."
    first = mirrors[0] if isinstance(mirrors[0], dict) else {}
    uri = first.get("URI")
    if not uri:
        return "Error: download link response had no URI."

    raw_name = first.get("name") if isinstance(first.get("name"), str) else ""
    filename = os.path.basename(urllib.parse.urlparse(raw_name).path)
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip(" .")
    if not filename:
        filename = f"{domain_name}_mod{mod_id}_file{file_id}.bin"
    dest_dir = Path(destination).expanduser() if destination else Path.cwd()
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        out_path = dest_dir / filename
    except OSError as exc:
        return f"Error: cannot use destination '{destination}': {exc}"

    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    total = 0
    exceeded = False
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=True) as hc:
            async with hc.stream("GET", uri) as resp:
                if resp.status_code >= 400:
                    return (
                        f"Error: CDN returned HTTP {resp.status_code} for the file download "
                        "(expired link or premium required?). Re-run to mint a fresh link."
                    )
                with open(out_path, "wb") as fh:
                    async for chunk in resp.aiter_bytes(65536):
                        total += len(chunk)
                        if total > max_bytes:
                            exceeded = True
                            break
                        md5.update(chunk)
                        sha256.update(chunk)
                        fh.write(chunk)
    except httpx.TimeoutException:
        return f"Error: download timed out; partial file left at {out_path}."
    except httpx.HTTPError as exc:
        return f"Error: network error during download: {type(exc).__name__}: {exc}"
    except OSError as exc:
        return f"Error: could not write '{out_path}': {exc}"
    if exceeded:
        try:
            out_path.unlink()
        except OSError:
            pass
        return f"Error: file exceeded max_bytes={max_bytes}; aborted and deleted the partial file."

    return json.dumps(
        {
            "file": str(out_path),
            "bytes": total,
            "md5": md5.hexdigest(),
            "sha256": sha256.hexdigest(),
            "mirror": first.get("short_name") or raw_name,
            "_rl": rl,
        },
        indent=2,
        ensure_ascii=False,
    )


@mcp.tool(
    name="nexus_search_by_md5",
    annotations={
        "title": "Find mod file by MD5",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def nexus_search_by_md5(
    domain_name: str = Field(..., description=DOMAIN_DESC),
    md5_hash: str = Field(..., description="MD5 hash of the file to look up (32 hex characters).", min_length=32, max_length=32),
) -> str:
    """Look up which mod and file on Nexus matches a file's MD5 hash (useful to identify an installed file).

    Returns:
        JSON array of {mod: <mod info>, file_details: <file info>}.
    """
    return await _call("GET", f"/v1/games/{domain_name}/mods/md5_search/{md5_hash}.json")


# ---------------------------------------------------------------------------
# Tools: user - tracked mods
# ---------------------------------------------------------------------------

@mcp.tool(
    name="nexus_get_tracked_mods",
    annotations={
        "title": "List tracked mods",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def nexus_get_tracked_mods() -> str:
    """List the mods the authenticated account is tracking.

    Returns:
        JSON array of {mod_id, domain_name}.
    """
    return await _call("GET", "/v1/user/tracked_mods.json")


@mcp.tool(
    name="nexus_track_mod",
    annotations={
        "title": "Track a mod",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def nexus_track_mod(
    domain_name: str = Field(..., description=DOMAIN_DESC),
    mod_id: int = Field(..., description="Numeric mod ID to track.", ge=1),
) -> str:
    """Start tracking a mod for the authenticated account (get update notifications).

    Returns:
        JSON {message: "..."}; HTTP 200 = already tracking, 201 = newly tracked.
    """
    return await _call(
        "POST",
        "/v1/user/tracked_mods.json",
        params={"domain_name": domain_name},
        data={"mod_id": mod_id},
    )


@mcp.tool(
    name="nexus_untrack_mod",
    annotations={
        "title": "Untrack a mod",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def nexus_untrack_mod(
    domain_name: str = Field(..., description=DOMAIN_DESC),
    mod_id: int = Field(..., description="Numeric mod ID to stop tracking.", ge=1),
) -> str:
    """Stop tracking a mod for the authenticated account.

    Returns:
        JSON {message: "..."}.
    """
    return await _call(
        "DELETE",
        "/v1/user/tracked_mods.json",
        params={"domain_name": domain_name},
        data={"mod_id": mod_id},
    )


# ---------------------------------------------------------------------------
# Tools: user - endorsements
# ---------------------------------------------------------------------------

@mcp.tool(
    name="nexus_get_endorsements",
    annotations={
        "title": "List endorsements",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def nexus_get_endorsements() -> str:
    """List the authenticated account's endorsement history.

    Returns:
        JSON array of {mod_id, domain_name, date (epoch), version,
        status ('Undecided' | 'Abstained' | 'Endorsed')}.
    """
    return await _call("GET", "/v1/user/endorsements.json")


@mcp.tool(
    name="nexus_endorse_mod",
    annotations={
        "title": "Endorse a mod",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def nexus_endorse_mod(
    domain_name: str = Field(..., description=DOMAIN_DESC),
    mod_id: int = Field(..., description="Numeric mod ID to endorse.", ge=1),
    version: str = Field(..., description="Mod version being endorsed, e.g. '1.619.6'."),
) -> str:
    """Endorse a mod on behalf of the authenticated account.

    Constraints: the account must have downloaded the mod, and Nexus enforces a
    15-minute cooldown after the download before endorsing (TOO_SOON_AFTER_DOWNLOAD).

    Returns:
        JSON {message: "Updated to: Endorsed", status: "Endorsed"}.
    """
    return await _call(
        "POST",
        f"/v1/games/{domain_name}/mods/{mod_id}/endorse.json",
        data={"version": version},
    )


@mcp.tool(
    name="nexus_abstain_endorsement",
    annotations={
        "title": "Abstain from endorsing",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def nexus_abstain_endorsement(
    domain_name: str = Field(..., description=DOMAIN_DESC),
    mod_id: int = Field(..., description="Numeric mod ID.", ge=1),
    version: str = Field(..., description="Mod version to abstain from endorsing."),
) -> str:
    """Withdraw or abstain from an endorsement for a mod.

    Returns:
        JSON {message: ..., status: "Abstained"}.
    """
    return await _call(
        "POST",
        f"/v1/games/{domain_name}/mods/{mod_id}/abstain.json",
        data={"version": version},
    )


# ---------------------------------------------------------------------------
# Tools: v2 GraphQL (search + rich data - separate rate-limit pool from v1 REST)
# ---------------------------------------------------------------------------

_READ_ONLY_ANNOTATIONS = {
    "title": "",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

_SORT_KEY_MAP = {
    "relevance": "relevance",
    "name": "name",
    "downloads": "downloads",
    "unique_downloads": "uniqueDownloads",
    "endorsements": "endorsements",
    "created_at": "createdAt",
    "updated_at": "updatedAt",
    "size": "size",
    "last_comment": "lastComment",
}

_MOD_SEARCH_FIELDS = """
      modId uid name summary author version downloads endorsements fileSize
      adultContent status supportsVortex pictureUrl thumbnailUrl
      createdAt updatedAt
      game { domainName name }
      modCategory { name }
      uploader { memberId name }
"""

_SEARCH_MODS_QUERY = """
query SearchMods($filter: ModsFilter, $sort: [ModsSort!], $offset: Int, $count: Int) {
  mods(filter: $filter, sort: $sort, offset: $offset, count: $count) {
    totalCount
    nodes {
%s
    }
  }
}
""" % _MOD_SEARCH_FIELDS


def _mods_sort(sort: str, direction: str) -> list[dict[str, Any]]:
    key = _SORT_KEY_MAP[sort]
    return [{key: {"direction": direction}}]


@mcp.tool(
    name="nexus_search_mods",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Search Nexus mods"},
)
async def nexus_search_mods(
    term: Optional[str] = Field(default=None, description="Free-text term matched against mod names (wildcard match). Optional."),
    domain_name: Optional[str] = Field(default=None, description=DOMAIN_DESC),
    sort: Literal[
        "endorsements", "downloads", "unique_downloads", "created_at", "updated_at",
        "name", "relevance", "size", "last_comment",
    ] = Field(default="endorsements", description="Sort key. Array order is precedence but only one key is exposed here."),
    direction: Literal["DESC", "ASC"] = Field(default="DESC", description="Sort direction."),
    min_endorsements: Optional[int] = Field(default=None, description="Only mods with at least this many endorsements.", ge=0),
    min_downloads: Optional[int] = Field(default=None, description="Only mods with at least this many downloads.", ge=0),
    exclude_adult: bool = Field(default=False, description="If true, exclude adult-content mods from results."),
    offset: int = Field(default=0, description="Offset-based pagination start.", ge=0),
    count: int = Field(default=20, description="Results per page. Server silently caps page size (~50-80); check '_returned'.", ge=1, le=100),
) -> str:
    """Search Nexus Mods with free text + filters, sorted and paginated.

    Backed by the v2 GraphQL API (https://api.nexusmods.com/v2/graphql), which
    does NOT consume the v1 REST rate-limit quota. The v1 REST API has no
    free-text search, so this is the only way to search by name.

    Returns:
        JSON {totalCount, _returned, nodes: [{modId, uid, name, summary, author,
        version, downloads, endorsements, fileSize, game, modCategory, uploader,
        pictureUrl, ...}]}. Paginate with offset += _returned until
        offset >= totalCount. Note: description (full BBCode) is NOT included
        here - use nexus_get_mod_v2 for full mod details.
    """
    flt: dict[str, Any] = {}
    if term:
        flt["name"] = [{"value": term, "op": "WILDCARD"}]
    if domain_name:
        flt["gameDomainName"] = [{"value": domain_name, "op": "EQUALS"}]
    if min_endorsements is not None:
        flt["endorsements"] = [{"value": min_endorsements, "op": "GTE"}]
    if min_downloads is not None:
        flt["downloads"] = [{"value": min_downloads, "op": "GTE"}]
    if exclude_adult:
        flt["adultContent"] = [{"value": False, "op": "EQUALS"}]

    data = await _gql_call(
        _SEARCH_MODS_QUERY,
        {
            "filter": flt or None,
            "sort": _mods_sort(sort, direction),
            "offset": offset,
            "count": count,
        },
    )
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return data
    page = parsed.get("mods") if isinstance(parsed, dict) else None
    if isinstance(page, dict) and "totalCount" in page:
        nodes = page.get("nodes") or []
        result = {**page, "nodes": nodes, "_returned": len(nodes)}
        result["_hint"] = (
            "Paginate: offset += _returned while offset < totalCount. "
            "Page size may be capped silently by the server."
        )
        return json.dumps(result, indent=2, ensure_ascii=False)
    return data


_GAME_ID_QUERY = """
query GameId($domain: String!) {
  game(domainName: $domain) { id domainName name }
}
"""

_MOD_DETAIL_QUERY = """
query ModDetail($modId: ID!, $gameId: ID!) {
  mod(modId: $modId, gameId: $gameId) {
    modId uid name summary description author version downloads endorsements
    fileSize adultContent status supportsVortex directDownloadEnabled
    pictureUrl thumbnailUrl thumbnailLargeUrl
    createdAt updatedAt
    tags { name }
    modCategory { name }
    game { domainName name }
    uploader { memberId name avatar }
    modRequirements { nexusRequirements { nodes { modName url notes } } }
  }
  modFiles(modId: $modId, gameId: $gameId) {
    fileId name version category sizeInBytes totalDownloads date description
  }
}
"""


@mcp.tool(
    name="nexus_get_mod_v2",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get rich mod details (v2)"},
)
async def nexus_get_mod_v2(
    domain_name: str = Field(..., description=DOMAIN_DESC),
    mod_id: int = Field(..., description="Numeric mod ID (same as v1).", ge=1),
) -> str:
    """Get rich mod details via v2 GraphQL: full description (raw BBCode),
    tags, requirements, and complete file list - none of which v1 REST exposes.

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota.

    Returns:
        JSON {mod: {..., description (BBCode + literal <br /> tags - render or
        strip before display), requirements}, files: [{fileId, name, version,
        category, sizeInBytes, totalDownloads, date, description}]}.
    """
    game_data = await _gql_call(_GAME_ID_QUERY, {"domain": domain_name})
    try:
        game = json.loads(game_data)
    except json.JSONDecodeError:
        return game_data
    game_id = (game or {}).get("game", {}).get("id") if isinstance(game, dict) else None
    if not game_id:
        return json.dumps(
            {"error": f"Unknown domain_name '{domain_name}' (game lookup returned no id).", "game": game},
            indent=2,
        )
    return await _gql_call(_MOD_DETAIL_QUERY, {"modId": mod_id, "gameId": game_id})


_USER_BY_ID_QUERY = """
query UserById($id: Int!) {
  user(id: $id) {
    memberId name about avatar modCount joined kudos
    contributedModCount collectionCount recognizedAuthor lastActive posts
  }
}
"""

_USER_BY_NAME_QUERY = """
query UserByName($name: String!) {
  userByName(name: $name) {
    memberId name about avatar modCount joined kudos
    contributedModCount collectionCount recognizedAuthor lastActive posts
  }
}
"""


@mcp.tool(
    name="nexus_get_user_v2",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get public user profile (v2)"},
)
async def nexus_get_user_v2(
    member_id: Optional[int] = Field(default=None, description="Numeric member ID (user_id from nexus_validate_key).", ge=1),
    username: Optional[str] = Field(default=None, description="Exact Nexus username, e.g. 'Talya1412'."),
) -> str:
    """Get a public Nexus Mods user profile by member ID or exact username.

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota. Provide exactly one of member_id or username.

    Returns:
        JSON {memberId, name, about, avatar, modCount, joined, kudos,
        contributedModCount, collectionCount, recognizedAuthor, lastActive, posts}.
    """
    if bool(member_id) == bool(username):
        return "Error: provide exactly one of member_id or username."
    if member_id:
        data = await _gql_call(_USER_BY_ID_QUERY, {"id": member_id})
    else:
        data = await _gql_call(_USER_BY_NAME_QUERY, {"name": username})
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return data
    user = parsed.get("user") or parsed.get("userByName") if isinstance(parsed, dict) else None
    if user:
        return json.dumps(user, indent=2, ensure_ascii=False)
    return data


_COLLECTIONS_SEARCH_QUERY = """
query SearchCollections($filter: CollectionsSearchFilter, $sort: [CollectionsSearchSort!], $offset: Int, $count: Int) {
  collectionsV2(filter: $filter, sort: $sort, offset: $offset, count: $count) {
    totalCount
    nodes {
      slug name summary endorsements totalDownloads uniqueDownloads
      overallRating overallRatingCount recentRating recentRatingCount
      createdAt updatedAt
      game { domainName name }
      user { name memberId }
    }
  }
}
"""


@mcp.tool(
    name="nexus_search_collections",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Search mod collections (v2)"},
)
async def nexus_search_collections(
    term: Optional[str] = Field(default=None, description="Free-text general search term (matches name/summary/etc). Optional."),
    domain_name: Optional[str] = Field(default=None, description=DOMAIN_DESC),
    sort: Literal[
        "endorsements", "downloads", "created_at", "updated_at", "rating", "recent_rating", "relevance",
    ] = Field(default="endorsements", description="Sort key."),
    direction: Literal["DESC", "ASC"] = Field(default="DESC", description="Sort direction."),
    offset: int = Field(default=0, description="Offset-based pagination start.", ge=0),
    count: int = Field(default=20, description="Results per page. Server may silently cap page size; check '_returned'.", ge=1, le=100),
) -> str:
    """Search Nexus Mods collections (curated mod packs) with free text.

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota. v1 REST has no collections search at all.

    Returns:
        JSON {totalCount, _returned, nodes: [{slug, name, summary, endorsements,
        totalDownloads, overallRating, game, user, ...}]}.
    """
    flt: dict[str, Any] = {}
    if term:
        flt["generalSearch"] = [{"value": term, "op": "WILDCARD"}]
    if domain_name:
        flt["gameDomainName"] = [{"value": domain_name, "op": "EQUALS"}]

    collection_sort_map = {
        "endorsements": "endorsements",
        "downloads": "downloads",
        "created_at": "createdAt",
        "updated_at": "updatedAt",
        "rating": "rating",
        "recent_rating": "recentRating",
        "relevance": "relevance",
    }
    data = await _gql_call(
        _COLLECTIONS_SEARCH_QUERY,
        {
            "filter": flt or None,
            "sort": [{collection_sort_map[sort]: {"direction": direction}}],
            "offset": offset,
            "count": count,
        },
    )
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return data
    page = parsed.get("collectionsV2") if isinstance(parsed, dict) else None
    if isinstance(page, dict) and "totalCount" in page:
        nodes = page.get("nodes") or []
        result = {**page, "nodes": nodes, "_returned": len(nodes)}
        return json.dumps(result, indent=2, ensure_ascii=False)
    return data


# ---------------------------------------------------------------------------
# Tools: v2 GraphQL extras (raw queries, games, users, news, comments, ...)
# ---------------------------------------------------------------------------

_GQL_INTROSPECT_QUERY = """
query Introspect($t: String!) {
  __type(name: $t) {
    name kind description
    fields { name description type { name kind ofType { name kind ofType { name kind ofType { name } } } } }
    inputFields { name description type { name kind ofType { name kind ofType { name } } } }
    enumValues { name description }
  }
}
"""


@mcp.tool(
    name="nexus_graphql_introspect",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Introspect v2 GraphQL schema type"},
)
async def nexus_graphql_introspect(
    type_name: str = Field(..., description="GraphQL type name to introspect, e.g. 'Mod', 'ModsFilter', 'Collection'."),
) -> str:
    """Introspect any type in the Nexus v2 GraphQL schema.

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota. Useful for discovering filters, sorts, and fields
    before composing a query with nexus_graphql_query.

    Returns:
        JSON {name, kind, description, fields: [{name, type}], inputFields,
        enumValues} or null if the type does not exist.
    """
    return await _gql_call(_GQL_INTROSPECT_QUERY, {"t": type_name})


@mcp.tool(
    name="nexus_graphql_query",
    annotations={"title": "Run raw v2 GraphQL query"},
)
async def nexus_graphql_query(
    query: str = Field(..., description="GraphQL query document, e.g. 'query { games(count: 5) { nodes { name domainName } } }'."),
    variables: str = Field(default="{}", description="JSON object string with query variables, e.g. '{\"term\": \"sky\"}'."),
) -> str:
    """Run a raw query against the Nexus v2 GraphQL API (power-user escape hatch).

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota. Introspect types with nexus_graphql_introspect first to
    build valid queries. Use for read-only queries; most mutations require
    OAuth scopes this server does not have and will fail cleanly.

    Returns:
        The raw GraphQL 'data' payload as JSON, or 'Error: ...' with the
        server-side GraphQL error messages.
    """
    try:
        parsed_vars = json.loads(variables)
    except json.JSONDecodeError as exc:
        return f"Error: variables is not valid JSON ({exc})."
    if not isinstance(parsed_vars, dict):
        return "Error: variables must be a JSON object string."
    return await _gql_call(query, parsed_vars)


def _gql_page(data: str, root: str) -> str:
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return data
    page = parsed.get(root) if isinstance(parsed, dict) else None
    if isinstance(page, dict) and "totalCount" in page:
        nodes = page.get("nodes") or []
        return json.dumps({**page, "nodes": nodes, "_returned": len(nodes)}, indent=2, ensure_ascii=False)
    return data


_USERS_SEARCH_QUERY = """
query SearchUsers($filter: UsersSearchFilter, $sort: [UsersSearchSort!], $offset: Int, $count: Int) {
  users(filter: $filter, sort: $sort, offset: $offset, count: $count) {
    totalCount nodesCount
    nodes {
      memberId name about avatar modCount joined kudos
      contributedModCount collectionCount recognizedAuthor lastActive posts
    }
  }
}
"""


@mcp.tool(
    name="nexus_search_users",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Search Nexus users (v2)"},
)
async def nexus_search_users(
    term: str = Field(..., description="Username search term."),
    mode: Literal["wildcard", "exact"] = Field(default="wildcard", description="wildcard = substring match; exact = exact username match."),
    sort: Literal["name", "relevance"] = Field(default="relevance", description="Sort key."),
    direction: Literal["DESC", "ASC"] = Field(default="ASC", description="Sort direction."),
    offset: int = Field(default=0, description="Offset-based pagination start.", ge=0),
    count: int = Field(default=20, description="Results per page.", ge=1, le=100),
) -> str:
    """Search Nexus Mods users by username (v2 GraphQL).

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota. Unlike nexus_get_user_v2 this supports partial names.

    Returns:
        JSON {totalCount, _returned, nodes: [{memberId, name, avatar, modCount,
        joined, kudos, ...}]}. Paginate with offset += _returned.
    """
    if mode == "exact":
        flt: dict[str, Any] = {"nameExact": [{"value": term}]}
    else:
        flt = {"nameWildcard": [{"value": term, "op": "WILDCARD"}]}
    data = await _gql_call(
        _USERS_SEARCH_QUERY,
        {
            "filter": flt,
            "sort": [{sort: {"direction": direction}}],
            "offset": offset,
            "count": count,
        },
    )
    return _gql_page(data, "users")


_GAMES_SEARCH_QUERY = """
query SearchGames($filter: GamesSearchFilter, $sort: [GamesSearchSort!], $offset: Int, $count: Int) {
  games(filter: $filter, sort: $sort, offset: $offset, count: $count) {
    totalCount nodesCount
    nodes {
      domainName name id modCount downloadCount collectionCount
      genre forumUrl supportsVortex approvedAt
    }
  }
}
"""


@mcp.tool(
    name="nexus_search_games",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Search Nexus games (v2)"},
)
async def nexus_search_games(
    term: Optional[str] = Field(default=None, description="Free-text term matched against game names (wildcard match). Optional."),
    sort: Literal["downloads", "mods", "collections", "name", "approved", "relevance"] = Field(
        default="downloads", description="Sort key."
    ),
    direction: Literal["DESC", "ASC"] = Field(default="DESC", description="Sort direction."),
    offset: int = Field(default=0, description="Offset-based pagination start.", ge=0),
    count: int = Field(default=20, description="Results per page.", ge=1, le=100),
) -> str:
    """Search Nexus Mods games by name, sorted and paginated (v2 GraphQL).

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota. Complements nexus_get_games (full cached catalog).

    Returns:
        JSON {totalCount, _returned, nodes: [{domainName, name, id, modCount,
        downloadCount, genre, forumUrl, supportsVortex, approvedAt}]}.
    """
    flt: Optional[dict[str, Any]] = None
    if term:
        flt = {"name": [{"value": term, "op": "WILDCARD"}]}
    data = await _gql_call(
        _GAMES_SEARCH_QUERY,
        {
            "filter": flt,
            "sort": [{sort: {"direction": direction}}],
            "offset": offset,
            "count": count,
        },
    )
    return _gql_page(data, "games")


_GAME_DETAIL_QUERY = """
query GameDetail($domain: String!) {
  game(domainName: $domain) {
    domainName name id genre forumUrl modCount downloadCount uniqueDownloadCount
    collectionCount supportsVortex trendingPeriodDays approvedAt
  }
}
"""


@mcp.tool(
    name="nexus_get_game_v2",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get rich game details (v2)"},
)
async def nexus_get_game_v2(
    domain_name: str = Field(..., description=DOMAIN_DESC),
) -> str:
    """Get rich game details via v2 GraphQL: mod/download/collection counts,
    genre, forum URL, Vortex support.

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota.

    Returns:
        JSON game object, or {error: ...} for unknown domains.
    """
    return await _gql_call(_GAME_DETAIL_QUERY, {"domain": domain_name})


_MOD_FILES_QUERY = """
query ModFiles($modId: ID!, $gameId: ID!) {
  modFiles(modId: $modId, gameId: $gameId) {
    fileId name version category sizeInBytes totalDownloads date description
  }
}
"""


@mcp.tool(
    name="nexus_get_files_v2",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get mod file list (v2)"},
)
async def nexus_get_files_v2(
    domain_name: str = Field(..., description=DOMAIN_DESC),
    mod_id: int = Field(..., description="Numeric mod ID.", ge=1),
) -> str:
    """Get the complete file list of a mod via v2 GraphQL.

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota. Same data as nexus_get_mod_files but from v2 and
    therefore quota-free on the v1 pool.

    Returns:
        JSON {totalFiles, _returned, files: [{fileId, name, version, category,
        sizeInBytes, totalDownloads, date, description}]}.
    """
    game_data = await _gql_call(_GAME_ID_QUERY, {"domain": domain_name})
    try:
        game = json.loads(game_data)
    except json.JSONDecodeError:
        return game_data
    game_id = (game or {}).get("game", {}).get("id") if isinstance(game, dict) else None
    if not game_id:
        return json.dumps(
            {"error": f"Unknown domain_name '{domain_name}' (game lookup returned no id).", "game": game},
            indent=2,
        )
    data = await _gql_call(_MOD_FILES_QUERY, {"modId": mod_id, "gameId": game_id})
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return data
    files = parsed.get("modFiles") if isinstance(parsed, dict) else None
    if isinstance(files, list):
        return json.dumps(
            {"totalFiles": len(files), "_returned": len(files), "files": files},
            indent=2,
            ensure_ascii=False,
        )
    return data


_MODS_BATCH_QUERY = """
query ModsByDomain($ids: [CompositeDomainWithIdInput!]!, $offset: Int, $count: Int) {
  legacyModsByDomain(ids: $ids, offset: $offset, count: $count) {
    totalCount
    nodes {
%s
    }
  }
}
""" % _MOD_SEARCH_FIELDS


@mcp.tool(
    name="nexus_get_mods_batch",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get many mods in one query (v2)"},
)
async def nexus_get_mods_batch(
    mods: str = Field(
        ...,
        description='Comma-separated "domain:modId" entries, e.g. "skyrimse:12604,fallout4:27251".',
    ),
    offset: int = Field(default=0, description="Offset into the resolved mod list.", ge=0),
    count: int = Field(default=25, description="Max mods to return.", ge=1, le=100),
) -> str:
    """Fetch many mods across games in a single v2 GraphQL query.

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota. Batch equivalent of nexus_get_mod - ideal for
    resolving many mod IDs at once without burning quota.

    Returns:
        JSON {totalResolved, _returned, mods: [...same shape as
        nexus_search_mods nodes...]}.
    """
    entries: list[dict[str, Any]] = []
    bad: list[str] = []
    for chunk in mods.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        domain, sep, mod_id = chunk.rpartition(":")
        if not sep or not domain.strip() or not mod_id.strip().isdigit():
            bad.append(chunk)
            continue
        entries.append({"gameDomain": domain.strip(), "modId": int(mod_id)})
    if bad:
        return json.dumps(
            {"error": 'Invalid entries (expected "domain:modId"):', "entries": bad},
            indent=2,
        )
    if not entries:
        return "Error: provide at least one \"domain:modId\" entry."
    data = await _gql_call(_MODS_BATCH_QUERY, {"ids": entries, "offset": offset, "count": count})
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return data
    root = parsed.get("legacyModsByDomain") if isinstance(parsed, dict) else None
    if isinstance(root, list):
        return json.dumps(
            {"totalResolved": len(root), "_returned": len(root), "mods": root},
            indent=2,
            ensure_ascii=False,
        )
    if isinstance(root, dict) and "nodes" in root:
        nodes = root.get("nodes") or []
        return json.dumps(
            {"totalResolved": root.get("totalCount", len(nodes)), "_returned": len(nodes), "mods": nodes},
            indent=2,
            ensure_ascii=False,
        )
    return data


_MOD_UID_QUERY = """
query ModUid($modId: ID!, $gameId: ID!) {
  mod(modId: $modId, gameId: $gameId) { uid }
}
"""

_MOD_ENDORSERS_QUERY = """
query ModEndorsers($modUid: ID!, $first: Int) {
  modEndorsers(modUid: $modUid, first: $first) {
    pageInfo { endCursor hasNextPage }
    nodes { memberId name avatar modCount kudos contributedModCount collectionCount }
  }
}
"""


@mcp.tool(
    name="nexus_get_mod_endorsers",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get mod endorsers (v2)"},
)
async def nexus_get_mod_endorsers(
    domain_name: str = Field(..., description=DOMAIN_DESC),
    mod_id: int = Field(..., description="Numeric mod ID.", ge=1),
    first: int = Field(default=20, description="Page size (cursor pagination).", ge=1, le=100),
    after_cursor: Optional[str] = Field(default=None, description="Cursor from a previous page's pageInfo.endCursor."),
) -> str:
    """List users who endorsed a mod via v2 GraphQL.

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota. Resolves the mod's uid first (two-step, both cached).

    Returns:
        JSON {pageInfo: {endCursor, hasNextPage}, nodes: [{memberId, name,
        avatar, modCount, kudos, ...}]}. Paginate with after_cursor.
    """
    game_data = await _gql_call(_GAME_ID_QUERY, {"domain": domain_name})
    try:
        game = json.loads(game_data)
    except json.JSONDecodeError:
        return game_data
    game_id = (game or {}).get("game", {}).get("id") if isinstance(game, dict) else None
    if not game_id:
        return json.dumps(
            {"error": f"Unknown domain_name '{domain_name}' (game lookup returned no id).", "game": game},
            indent=2,
        )
    uid_data = await _gql_call(_MOD_UID_QUERY, {"modId": mod_id, "gameId": game_id})
    try:
        uid_parsed = json.loads(uid_data)
    except json.JSONDecodeError:
        return uid_data
    mod_uid = (uid_parsed or {}).get("mod", {}).get("uid") if isinstance(uid_parsed, dict) else None
    if not mod_uid:
        return json.dumps(
            {"error": f"Mod {mod_id} not found in '{domain_name}' (uid lookup returned nothing).", "mod": uid_parsed},
            indent=2,
        )
    return await _gql_call(_MOD_ENDORSERS_QUERY, {"modUid": mod_uid, "first": first})


_NEWS_QUERY = """
query News($cat: NewsCategoryEnum, $gameId: Int, $offset: Int, $count: Int) {
  news(newsCategory: $cat, gameId: $gameId, offset: $offset, count: $count) {
    totalCount nodesCount
    nodes {
      id title summary author { memberId name } date newsCategory { name }
      sourceName sourceUrl commentsCount image
      games { domainName name }
    }
  }
}
"""


@mcp.tool(
    name="nexus_get_news",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get Nexus news (v2)"},
)
async def nexus_get_news(
    category: Optional[Literal["SITE_NEWS", "GAME_NEWS", "MOD_NEWS", "INTERVIEWS", "COMPETITIONS", "FEATURES"]] = Field(
        default=None, description="Filter by news category. Optional."
    ),
    domain_name: Optional[str] = Field(default=None, description=DOMAIN_DESC),
    offset: int = Field(default=0, description="Offset-based pagination start.", ge=0),
    count: int = Field(default=20, description="Results per page.", ge=1, le=100),
) -> str:
    """Get Nexus Mods news articles (site news, game news, interviews, ...).

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota. Filter by category and/or game.

    Returns:
        JSON {totalCount, _returned, nodes: [{id, title, summary, author, date,
        newsCategory, sourceName, sourceUrl, commentsCount, image, games}]}.
    """
    game_id: Optional[int] = None
    if domain_name:
        game_data = await _gql_call(_GAME_ID_QUERY, {"domain": domain_name})
        try:
            game = json.loads(game_data)
        except json.JSONDecodeError:
            return game_data
        game_id = (game or {}).get("game", {}).get("id") if isinstance(game, dict) else None
        if not game_id:
            return json.dumps(
                {"error": f"Unknown domain_name '{domain_name}' (game lookup returned no id).", "game": game},
                indent=2,
            )
    data = await _gql_call(
        _NEWS_QUERY,
        {"cat": category, "gameId": game_id, "offset": offset, "count": count},
    )
    return _gql_page(data, "news")


_CATEGORIES_QUERY = """
query Categories($gameId: Int) {
  categories(gameId: $gameId) {
    id name parentId description approved createdAt updatedAt
  }
}
"""

_GLOBAL_CATEGORIES_QUERY = """
query GlobalCategories {
  categories(global: true) {
    id name parentId description approved createdAt updatedAt
  }
}
"""


@mcp.tool(
    name="nexus_get_categories",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get mod categories (v2)"},
)
async def nexus_get_categories(
    domain_name: Optional[str] = Field(default=None, description=DOMAIN_DESC),
    is_global: bool = Field(default=False, description="If true, return global categories instead of game-specific ones."),
) -> str:
    """Get mod categories (per-game or global) via v2 GraphQL.

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota. Provide exactly one of domain_name or is_global.
    Note: these are collection-style categories (Total Overhaul, Themed,
    Vanilla Plus, ...) - per-game lists may be sparse for newer games.

    Returns:
        JSON list [{id, name, parentId, description, approved, ...}].
    """
    if is_global:
        if domain_name:
            return "Error: provide only one of domain_name or is_global."
        return await _gql_call(_GLOBAL_CATEGORIES_QUERY, {})
    if not domain_name:
        return "Error: provide domain_name or set is_global=true."
    game_data = await _gql_call(_GAME_ID_QUERY, {"domain": domain_name})
    try:
        game = json.loads(game_data)
    except json.JSONDecodeError:
        return game_data
    game_id = (game or {}).get("game", {}).get("id") if isinstance(game, dict) else None
    if not game_id:
        return json.dumps(
            {"error": f"Unknown domain_name '{domain_name}' (game lookup returned no id).", "game": game},
            indent=2,
        )
    return await _gql_call(_CATEGORIES_QUERY, {"gameId": game_id})


_LEGACY_TAGS_QUERY = """
query LegacyTags($gameId: ID, $onlyAdult: Boolean, $excludeAdult: Boolean) {
  legacyTags(gameId: $gameId, onlyAdult: $onlyAdult, excludeAdult: $excludeAdult) {
    id name parentId global blockable searchable
  }
}
"""


@mcp.tool(
    name="nexus_get_tags",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get mod tags (v2)"},
)
async def nexus_get_tags(
    domain_name: str = Field(..., description=DOMAIN_DESC),
    only_adult: bool = Field(default=False, description="If true, return only adult-content tags."),
    exclude_adult: bool = Field(default=True, description="If true, exclude adult-content tags from results."),
) -> str:
    """Get the mod tag taxonomy of a game via v2 GraphQL.

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota. Useful to build tag filters for nexus_search_mods.

    Returns:
        JSON list [{id, name, parentId, global, blockable, searchable}].
    """
    game_data = await _gql_call(_GAME_ID_QUERY, {"domain": domain_name})
    try:
        game = json.loads(game_data)
    except json.JSONDecodeError:
        return game_data
    game_id = (game or {}).get("game", {}).get("id") if isinstance(game, dict) else None
    if not game_id:
        return json.dumps(
            {"error": f"Unknown domain_name '{domain_name}' (game lookup returned no id).", "game": game},
            indent=2,
        )
    return await _gql_call(
        _LEGACY_TAGS_QUERY,
        {"gameId": game_id, "onlyAdult": only_adult, "excludeAdult": exclude_adult},
    )


_COLLECTION_DETAIL_QUERY = """
query CollectionDetail($slug: String!) {
  collection(slug: $slug) {
    slug name summary description endorsements totalDownloads uniqueDownloads
    overallRating overallRatingCount recentRating recentRatingCount
    createdAt updatedAt
    game { domainName name }
    user { memberId name avatar }
    tags { name }
    category { name }
  }
}
"""


@mcp.tool(
    name="nexus_get_collection",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get collection details (v2)"},
)
async def nexus_get_collection(
    slug: str = Field(..., description="Collection slug from nexus_search_collections, e.g. 'collections-skyrimsse-x'."),
) -> str:
    """Get full details of a mod collection by slug via v2 GraphQL.

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota. v1 REST has no collection detail endpoint.

    Returns:
        JSON {slug, name, summary, description (BBCode), endorsements,
        downloads, ratings, game, author, tags, category}.
    """
    return await _gql_call(_COLLECTION_DETAIL_QUERY, {"slug": slug})


_COLLECTION_REVISION_QUERY = """
query CollectionRevisionDetail($slug: String!, $rev: Int, $adult: Boolean, $domain: String) {
  collectionRevision(slug: $slug, revision: $rev, viewAdultContent: $adult, domainName: $domain) {
    id revisionNumber revisionStatus status adultContent latest
    overallRating overallRatingCount totalDownloads uniqueDownloads
    modCount totalSize createdAt updatedAt
    collection { slug name }
  }
}
"""


@mcp.tool(
    name="nexus_get_collection_revision",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get collection revision (v2)"},
)
async def nexus_get_collection_revision(
    slug: str = Field(..., description="Collection slug from nexus_search_collections."),
    revision: Optional[int] = Field(default=None, description="Revision number. Omit for the latest revision.", ge=1),
    domain_name: Optional[str] = Field(default=None, description=DOMAIN_DESC),
    view_adult_content: bool = Field(default=False, description="Set true to inspect adult collections."),
) -> str:
    """Get a specific collection revision (mod count, sizes, status) via v2 GraphQL.

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota.

    Returns:
        JSON {id, revisionNumber, revisionStatus, status, adultContent, latest,
        overallRating, totalDownloads, uniqueDownloads, modCount, totalSize,
        collection}.
    """
    return await _gql_call(
        _COLLECTION_REVISION_QUERY,
        {"slug": slug, "rev": revision, "adult": view_adult_content, "domain": domain_name},
    )


_COMMENTS_SEARCH_QUERY = """
query SearchComments($filter: CommentsSearchFilter, $sort: [CommentsSearchSort!], $first: Int) {
  searchComments(filter: $filter, sort: $sort, first: $first) {
    totalCount timeTaken
    nodes {
      id body createdAt updatedAt likesCount isPinned
      creator { memberId name avatar }
    }
  }
}
"""


@mcp.tool(
    name="nexus_search_comments",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Search comments (v2)"},
)
async def nexus_search_comments(
    term: Optional[str] = Field(default=None, description="Free-text search over comment bodies."),
    thread_id: Optional[int] = Field(default=None, description="Restrict results to a single comment thread.", ge=1),
    count: int = Field(default=20, description="Results per page (cursor pagination).", ge=1, le=100),
) -> str:
    """Search Nexus Mods comments by text or list a thread's comments.

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota. Provide exactly one of term or thread_id. KNOWN ISSUE:
    Nexus' searchComments endpoint currently returns HTTP 500 for all
    requests server-side; errors are surfaced as-is until Nexus fixes it.
    May also require extra permissions for some threads.

    Returns:
        JSON {totalCount, timeTaken, nodes: [{id, body, createdAt, likesCount,
        isPinned, creator}]}.
    """
    if bool(term) == bool(thread_id):
        return "Error: provide exactly one of term or thread_id."
    if thread_id:
        flt: dict[str, Any] = {"threadId": [{"value": str(thread_id), "op": "EQUALS"}]}
    else:
        flt = {"query": [{"value": term, "op": "WILDCARD"}]}
    return await _gql_call(
        _COMMENTS_SEARCH_QUERY,
        {"filter": flt, "sort": None, "first": count},
    )


# ---------------------------------------------------------------------------
# Tools: v2 GraphQL extras (comments, badges, monthly summary, game categories)
# ---------------------------------------------------------------------------

_COMMENT_THREAD_QUERY = """
query CommentThreadDetail($id: ID!) {
  commentThread(commentThreadId: $id) {
    id
    comments {
      totalCount
      nodes {
        id body createdAt updatedAt likesCount isPinned
        creator { memberId name avatar }
        replies {
          totalCount
          nodes { id body createdAt likesCount creator { memberId name } }
        }
      }
    }
  }
}
"""


@mcp.tool(
    name="nexus_get_comment_thread",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get comment thread (v2)"},
)
async def nexus_get_comment_thread(
    thread_id: int = Field(..., description="Comment thread ID.", ge=1),
) -> str:
    """Get a comment thread with all top-level comments and their replies.

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota. Useful to read replies on threads where
    nexus_search_comments is unavailable (that endpoint is currently
    broken upstream).

    Returns:
        JSON {id, comments: {totalCount, nodes: [{id, body, createdAt,
        likesCount, isPinned, creator, replies: {totalCount, nodes}}]}}.
    """
    return await _gql_call(_COMMENT_THREAD_QUERY, {"id": str(thread_id)})


_COMMENT_QUERY = """
query CommentDetail($id: ID!) {
  comment(commentId: $id) {
    id body createdAt updatedAt likesCount isPinned
    creator { memberId name avatar }
  }
}
"""


@mcp.tool(
    name="nexus_get_comment",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get comment (v2)"},
)
async def nexus_get_comment(
    comment_id: int = Field(..., description="Comment ID.", ge=1),
) -> str:
    """Get a single comment by ID via v2 GraphQL.

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota.

    Returns:
        JSON {id, body, createdAt, updatedAt, likesCount, isPinned, creator}.
    """
    return await _gql_call(_COMMENT_QUERY, {"id": str(comment_id)})


_BADGES_QUERY = "{ badges { id name description } }"


@mcp.tool(
    name="nexus_get_badges",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "List mod badges (v2)"},
)
async def nexus_get_badges() -> str:
    """List all badges a mod can earn (e.g. 'Top pick', 'Easy install').

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota. Static catalog - cache the result in your workflow.

    Returns:
        JSON {badges: [{id, name, description}]}.
    """
    return await _gql_call(_BADGES_QUERY)


_MONTHLY_SUMMARY_QUERY = """
query MonthlySummary($accountId: Int!) {
  userMonthlySummary(accountId: $accountId) {
    userId
    entries { month year }
  }
}
"""


@mcp.tool(
    name="nexus_get_user_monthly_summary",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get user monthly summary (v2)"},
)
async def nexus_get_user_monthly_summary(
    account_id: int = Field(..., description="Nexus Mods account ID.", ge=1),
) -> str:
    """List the months a user has a monthly activity report for.

    Backed by the v2 GraphQL API, which does NOT consume the v1 REST
    rate-limit quota. Follow up with nexus_get_user_monthly_report
    (v1) for a specific month's download/upload numbers.

    Returns:
        JSON {userId, entries: [{month, year}]}.
    """
    return await _gql_call(_MONTHLY_SUMMARY_QUERY, {"accountId": account_id})


# ---------------------------------------------------------------------------
# Tools: v2 GraphQL mutations (user actions)
# ---------------------------------------------------------------------------


@mcp.tool(name="nexus_track_user", annotations={"title": "Track a user (v2)"})
async def nexus_track_user(
    user_id: int = Field(..., description="Nexus Mods member ID to track for updates.", ge=1),
) -> str:
    """Start tracking a user (get notifications about their new mods) via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.

    Returns:
        JSON {trackUser: {success}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { trackUser(trackedUserId: $id) { ... on TrackUserMutationPayload { success } } }",
        {"id": str(user_id)},
    )


@mcp.tool(name="nexus_untrack_user", annotations={"title": "Untrack a user (v2)"})
async def nexus_untrack_user(
    user_id: int = Field(..., description="Nexus Mods member ID to stop tracking.", ge=1),
) -> str:
    """Stop tracking a user via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.

    Returns:
        JSON {untrackUser: {success}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { untrackUser(trackedUserId: $id) { ... on UntrackUserMutationPayload { success } } }",
        {"id": str(user_id)},
    )


@mcp.tool(name="nexus_give_kudos", annotations={"title": "Give kudos to a user (v2)"})
async def nexus_give_kudos(
    user_id: int = Field(..., description="Nexus Mods member ID to give kudos to.", ge=1),
) -> str:
    """Give kudos to a user via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.

    Returns:
        JSON {giveKudos: {success}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { giveKudos(kudosUserId: $id) { ... on GiveKudosMutationPayload { success } } }",
        {"id": str(user_id)},
    )


@mcp.tool(name="nexus_remove_kudos", annotations={"title": "Remove kudos from a user (v2)"})
async def nexus_remove_kudos(
    user_id: int = Field(..., description="Nexus Mods member ID to remove kudos from.", ge=1),
) -> str:
    """Remove previously given kudos from a user via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.

    Returns:
        JSON {removeKudos: {success}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { removeKudos(kudosUserId: $id) { ... on RemoveKudosMutationPayload { success } } }",
        {"id": str(user_id)},
    )


@mcp.tool(name="nexus_add_favourite_game", annotations={"title": "Favourite a game (v2)"})
async def nexus_add_favourite_game(
    game_id: int = Field(..., description="Game ID (from nexus_search_games / nexus_get_game 'id').", ge=1),
) -> str:
    """Add a game to your favourites via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.

    Returns:
        JSON {addFavouriteGame: {success}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { addFavouriteGame(gameId: $id) { ... on AddFavouriteGameMutationPayload { success } } }",
        {"id": str(game_id)},
    )


@mcp.tool(name="nexus_remove_favourite_game", annotations={"title": "Unfavourite a game (v2)"})
async def nexus_remove_favourite_game(
    game_id: int = Field(..., description="Game ID to remove from favourites.", ge=1),
) -> str:
    """Remove a game from your favourites via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.

    Returns:
        JSON {removeFavouriteGame: {success}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { removeFavouriteGame(gameId: $id) { ... on RemoveFavouriteGameMutationPayload { success } } }",
        {"id": str(game_id)},
    )


@mcp.tool(name="nexus_like_comment", annotations={"title": "Like a comment (v2)"})
async def nexus_like_comment(
    comment_id: int = Field(..., description="Comment ID to like.", ge=1),
) -> str:
    """Like a comment via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.

    Returns:
        JSON {likeComment: {comment}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { likeComment(commentId: $id) { ... on LikeCommentMutationPayload { comment { id likesCount } } } }",
        {"id": str(comment_id)},
    )


@mcp.tool(name="nexus_remove_comment_like", annotations={"title": "Unlike a comment (v2)"})
async def nexus_remove_comment_like(
    comment_id: int = Field(..., description="Comment ID to remove your like from.", ge=1),
) -> str:
    """Remove your like from a comment via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.

    Returns:
        JSON {removeCommentLike: {comment}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { removeCommentLike(commentId: $id) { ... on RemoveCommentLikeMutationPayload { comment { id likesCount } } } }",
        {"id": str(comment_id)},
    )


@mcp.tool(name="nexus_create_comment", annotations={"title": "Post a comment (v2)"})
async def nexus_create_comment(
    thread_id: int = Field(..., description="Comment thread ID to reply in.", ge=1),
    body: str = Field(..., description="Comment body text (plain text).", min_length=1),
) -> str:
    """Post a top-level comment in a thread via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.
    Find thread IDs on mod pages (?tab=posts); forum threads and mod
    posts have distinct thread IDs.

    Returns:
        JSON {createComment: {comment: {id, body, ...}}} or an error string.
    """
    return await _gql_call(
        "mutation($t: ID!, $b: String!) { createComment(commentThreadId: $t, body: $b) { ... on CreateCommentMutationPayload { comment { id body createdAt creator { name } } } } }",
        {"t": str(thread_id), "b": body},
    )


# ---------------------------------------------------------------------------
# Tools: v2 GraphQL reads & user preferences (batch 4)
# ---------------------------------------------------------------------------

_MOD_FILES_BY_UID_QUERY = """
query ModFilesByUid($uids: [ID!]!, $offset: Int, $count: Int) {
  modFilesByUid(uids: $uids, offset: $offset, count: $count) {
    nodes {
      fileId name version category sizeInBytes totalDownloads date description
    }
    totalCount
  }
}
"""


@mcp.tool(
    name="nexus_get_files_by_uid",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get mod file list by UID (v2)"},
)
async def nexus_get_files_by_uid(
    uids: str = Field(
        ...,
        description='Comma-separated mod UID entries, e.g. "39715562587071". UID is the big numeric uid from nexus_get_mod / nexus_search_mods, not the per-game mod ID.',
    ),
    offset: int = Field(default=0, description="Offset into the file list.", ge=0),
    count: int = Field(default=50, description="Max files to return.", ge=1, le=100),
) -> str:
    """Get mod file lists by mod UID(s) via v2 GraphQL - no domain/modId pair needed.

    Ideal when you only have the uid (e.g. from a .nxm link or your own mod
    pipeline). Backed by the v2 GraphQL API, which does NOT consume the v1
    REST rate-limit quota.

    Returns:
        JSON {totalFiles, _returned, files: [{fileId, name, version, category,
        sizeInBytes, totalDownloads, date, description}]}.
    """
    ids: list[str] = []
    bad: list[str] = []
    for chunk in uids.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.isdigit():
            ids.append(chunk)
        else:
            bad.append(chunk)
    if bad:
        return json.dumps({"error": "Invalid uids (expected numeric strings):", "entries": bad}, indent=2)
    if not ids:
        return "Error: provide at least one numeric mod UID."
    data = await _gql_call(_MOD_FILES_BY_UID_QUERY, {"uids": ids, "offset": offset, "count": count})
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return data
    root = parsed.get("modFilesByUid") if isinstance(parsed, dict) else None
    if isinstance(root, dict):
        nodes = root.get("nodes") or []
        return json.dumps(
            {"totalFiles": root.get("totalCount", len(nodes)), "_returned": len(nodes), "files": nodes},
            indent=2,
            ensure_ascii=False,
        )
    return data


@mcp.tool(
    name="nexus_get_favourite_games",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "List your favourite games (v2)"},
)
async def nexus_get_favourite_games() -> str:
    """Get the authenticated user's favourite games via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.

    Returns:
        JSON array of games: {id, name, domainName, genre, modCount,
        collectionCount}.
    """
    return await _gql_call("query { favouriteGames { id name domainName genre modCount collectionCount } }")


@mcp.tool(
    name="nexus_get_ignored_users",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "List your ignored users (v2)"},
)
async def nexus_get_ignored_users() -> str:
    """Get the current user's ignored (muted) users via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.

    Returns:
        JSON array of users: {memberId, name, avatar, viewerHasIgnored}.
    """
    return await _gql_call("query { ignoredUsers { memberId name avatar viewerHasIgnored } }")


@mcp.tool(name="nexus_ignore_user", annotations={"title": "Ignore a user (v2)"})
async def nexus_ignore_user(
    user_id: Optional[int] = Field(default=None, description="Nexus Mods member ID to ignore. Provide this or username.", ge=1),
    username: Optional[str] = Field(default=None, description="Exact Nexus username to ignore. Provide this or user_id."),
) -> str:
    """Ignore (mute) a user via v2 GraphQL - hides their content in your feed.

    Personal preference only: no public side effect. Reversible with
    nexus_unignore_user. Provide user_id OR username (at least one).

    Returns:
        JSON {ignoreUser: {success}} or an error string.
    """
    if not isinstance(user_id, int):
        user_id = None
    if not isinstance(username, str):
        username = None
    if user_id is None and username is None:
        return "Error: provide user_id or username."
    return await _gql_call(
        "mutation($u: ID, $n: String) { ignoreUser(userId: $u, userName: $n) { ... on IgnoreUserMutationPayload { success } } }",
        {"u": str(user_id) if user_id is not None else None, "n": username},
    )


@mcp.tool(name="nexus_unignore_user", annotations={"title": "Unignore a user (v2)"})
async def nexus_unignore_user(
    user_id: Optional[int] = Field(default=None, description="Nexus Mods member ID to unignore. Provide this or username.", ge=1),
    username: Optional[str] = Field(default=None, description="Exact Nexus username to unignore. Provide this or user_id."),
) -> str:
    """Stop ignoring (unmute) a user via v2 GraphQL.

    Personal preference only: no public side effect. Provide user_id OR
    username (at least one).

    Returns:
        JSON {unignoreUser: {success}} or an error string.
    """
    if not isinstance(user_id, int):
        user_id = None
    if not isinstance(username, str):
        username = None
    if user_id is None and username is None:
        return "Error: provide user_id or username."
    return await _gql_call(
        "mutation($u: ID, $n: String) { unignoreUser(userId: $u, userName: $n) { ... on UnignoreUserMutationPayload { success } } }",
        {"u": str(user_id) if user_id is not None else None, "n": username},
    )


@mcp.tool(
    name="nexus_get_blocked_tags",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "List your blocked tags (v2)"},
)
async def nexus_get_blocked_tags(
    exclude_adult: bool = Field(default=False, description="Exclude adult-content tags from the result."),
) -> str:
    """Get the current user's blocked tags via v2 GraphQL.

    Blocked tags hide matching mods/collections from your searches. Find tag
    IDs with nexus_get_tags / nexus_search_mods.

    Returns:
        JSON array of tags: {id, name, global, blockable, searchable, parentId}.
    """
    if not isinstance(exclude_adult, bool):
        exclude_adult = False
    return await _gql_call(
        "query($ex: Boolean) { blockedTags(excludeAdult: $ex) { id name global blockable searchable parentId } }",
        {"ex": exclude_adult},
    )


@mcp.tool(name="nexus_block_tag", annotations={"title": "Block a tag (v2)"})
async def nexus_block_tag(
    tag_id: int = Field(..., description="Tag ID to block (from nexus_get_tags / mod tag lists).", ge=1),
) -> str:
    """Block a tag for the current user via v2 GraphQL - hides matching content.

    Personal preference only: no public side effect. Reversible with
    nexus_unblock_tag. Only blockable tags can be blocked.

    Returns:
        JSON {blockTag: {success}} or an error string.
    """
    return await _gql_call(
        "mutation($t: ID!) { blockTag(tagId: $t) { ... on BlockTagMutationPayload { success } } }",
        {"t": str(tag_id)},
    )


@mcp.tool(name="nexus_unblock_tag", annotations={"title": "Unblock a tag (v2)"})
async def nexus_unblock_tag(
    tag_id: int = Field(..., description="Tag ID to unblock.", ge=1),
) -> str:
    """Unblock a previously blocked tag via v2 GraphQL.

    Personal preference only: no public side effect.

    Returns:
        JSON {unblockTag: {success}} or an error string.
    """
    return await _gql_call(
        "mutation($t: ID!) { unblockTag(tagId: $t) { ... on UnblockTagMutationPayload { success } } }",
        {"t": str(tag_id)},
    )


@mcp.tool(
    name="nexus_get_user_by_name",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get a user by exact username (v2)"},
)
async def nexus_get_user_by_name(
    username: str = Field(..., description="Exact Nexus username (case-sensitive), e.g. 'Talya1412'."),
) -> str:
    """Get a user profile by exact username via v2 GraphQL.

    Unlike nexus_search_users (fuzzy), this resolves one exact username and
    fails cleanly when nobody has it. Useful to convert a username to a
    memberId for the user mutation tools.

    Returns:
        JSON user object {memberId, name, avatar, modCount, kudos, joined, ...}
        or {userByName: null} when the username does not exist.
    """
    return await _gql_call(
        "query($n: String!) { userByName(name: $n) { memberId name avatar about country joined lastActive "
        "modCount contributedModCount collectionCount kudos posts endorsementsGiven recognizedAuthor "
        "verifiedCurator banned deleted viewerHasIgnored isTracked } }",
        {"n": username},
    )


@mcp.tool(
    name="nexus_get_user_monthly_report",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get one month's user report (v2)"},
)
async def nexus_get_user_monthly_report(
    account_id: int = Field(..., description="Nexus Mods account ID.", ge=1),
    year: int = Field(..., description="Report year, e.g. 2026.", ge=2007),
    month: int = Field(..., description="Report month (1-12).", ge=1, le=12),
) -> str:
    """Get the download/upload numbers for ONE specific month via v2 GraphQL.

    Companion to nexus_get_user_monthly_summary (which lists available
    months): this fetches the actual per-mod/per-game values for a chosen
    month. Useful for tracking your own mod's download history.

    Returns:
        JSON {userMonthlyReport: {userId, reportType, entries: [{month, year,
        value, status, ratio, modId, gameId, authorId, ...}]}}.
    """
    return await _gql_call(
        "query($a: Int!, $y: Int!, $m: Int!) { userMonthlyReport(accountId: $a, year: $y, month: $m) "
        "{ userId reportType entries { year month value status ratio modId gameId authorId modValue authorValue } } }",
        {"a": account_id, "y": year, "m": month},
    )


@mcp.tool(
    name="nexus_get_speedtest_urls",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get CDN speedtest URLs (v2)"},
)
async def nexus_get_speedtest_urls() -> str:
    """Get CDN speedtest URLs to diagnose download issues via v2 GraphQL.

    Handy when downloads feel slow: test latency/throughput against each
    mirror and compare. Consumes the v2 GraphQL pool.

    Returns:
        JSON array of {title, description, location, tag}.
    """
    return await _gql_call("query { speedtestUrls { title description location tag } }")


# ---------------------------------------------------------------------------
# Tools: OAuth2 login lifecycle
# ---------------------------------------------------------------------------

_OAUTH_REGISTER_NOTE = (
    "OAuth applications on Nexus Mods are registered by EMAILING support@nexusmods.com "
    "(name, short description, logo, source link, callback URI) - there is no self-serve web UI yet. "
    "Then set NEXUS_OAUTH_CLIENT_ID (and optionally NEXUS_OAUTH_CLIENT_SECRET / "
    "NEXUS_OAUTH_REDIRECT_URI) in the MCP server 'environment' config."
)


@mcp.tool(name="nexus_oauth_login", annotations={"title": "Start Nexus OAuth login"})
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


@mcp.tool(name="nexus_oauth_exchange", annotations={"title": "Complete Nexus OAuth login"})
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


@mcp.tool(name="nexus_oauth_refresh", annotations={"title": "Force OAuth token refresh"})
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


@mcp.tool(name="nexus_oauth_logout", annotations={"title": "Log out of OAuth"})
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


@mcp.tool(name="nexus_update_mod_direct_download", annotations={"title": "Toggle mod direct download (v2)"})
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


if __name__ == "__main__":
    mcp.run()
