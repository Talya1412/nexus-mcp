#!/usr/bin/env python3
"""Nexus Mods MCP server.

Wraps the official Nexus Mods REST API v1 (https://api.nexusmods.com) as MCP
tools: validate key, browse games, inspect mods/files, get download links,
search by MD5, and manage tracked mods / endorsements.

Authentication: reads NEXUS_API_KEY from the environment (personal API key
created at https://www.nexusmods.com/users/myaccount?tab=api%20access).

Docs: https://app.swaggerhub.com/apis-docs/NexusMods/nexus-mods_public_api_params_in_form_data/1.0
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
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
    """Lazily create the shared async HTTP client with auth headers."""
    global _client
    if _client is None:
        api_key = os.environ.get("NEXUS_API_KEY", "").strip()
        if not api_key:
            raise NexusApiError(
                "NEXUS_API_KEY environment variable is not set. "
                "Create a personal API key at https://www.nexusmods.com/users/myaccount?tab=api%20access "
                "and set it in the MCP server 'environment' config."
            )
        _client = httpx.AsyncClient(
            base_url=API_BASE,
            headers={
                "apikey": api_key,
                "User-Agent": f"{APP_NAME}/{APP_VERSION} ({sys.platform}; Python httpx)",
                "Application-Name": APP_NAME,
                "Application-Version": APP_VERSION,
                "Accept": "application/json",
            },
            timeout=30.0,
        )
    return _client


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
        401: "Invalid or missing API key. Check NEXUS_API_KEY.",
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
        response = await client.request(method, path, params=params, data=data)
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
        response = await client.post(GRAPHQL_PATH, json=body)
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


if __name__ == "__main__":
    mcp.run()
