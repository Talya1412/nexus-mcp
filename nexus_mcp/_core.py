#!/usr/bin/env python3
"""Nexus Mods MCP server.

Wraps the official Nexus Mods REST API v1 (https://api.nexusmods.com) and
GraphQL API v2 (https://api.nexusmods.com/v2/graphql) as 135 MCP tools:
validate key, browse games, inspect mods/files, free-text search, get download
links, download files (MD5+SHA256 verified), search by MD5, comment threads,
collections lifecycle, endorsements, and user preference/mutation tools.

Authentication: NEXUS_API_KEY (personal API key, https://www.nexusmods.com/users/myaccount?tab=api%20access)
and optionally OAuth2 (NEXUS_OAUTH_CLIENT_ID [+ NEXUS_OAUTH_CLIENT_SECRET], registered by emailing
support@nexusmods.com) which unlocks user-context mutations like updateModDirectDownloadEnabled.
OAuth tokens are persisted to ~/.nexus-mcp/oauth-tokens.json (override with NEXUS_OAUTH_TOKEN_FILE)
and auto-refreshed; Bearer auth takes precedence over apikey when logged in.

Docs: https://app.swaggerhub.com/apis-docs/NexusMods/nexus-mods_public_api_params_in_form_data/1.0
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic.fields import FieldInfo

API_BASE = "https://api.nexusmods.com"
GRAPHQL_PATH = "/v2/graphql"
APP_NAME = "nexus-mcp"
APP_VERSION = "1.1.1"
__version__ = APP_VERSION

RL_HEADERS = [
    "X-RL-Hourly-Limit",
    "X-RL-Hourly-Remaining",
    "X-RL-Hourly-Reset",
    "X-RL-Daily-Limit",
    "X-RL-Daily-Remaining",
    "X-RL-Daily-Reset",
]

DOMAIN_DESC = "Nexus Mods game domain (lowercase URL slug), e.g. 'forzahorizon6', 'skyrimse'. NOT the display name."

_DOMAIN_RE = re.compile(r"[a-z0-9-]+")


def _validate_domain(domain: str) -> str:
    """Validate and normalise a Nexus Mods game domain slug.

    Strips surrounding whitespace and lower-cases the value, then rejects
    anything that still contains spaces, slashes, or other characters that
    cannot appear in a real slug.  Raises :exc:`NexusApiError` with an
    actionable message so callers see a clear fix instead of a generic 404.

    Returns the normalised slug so callers can write::

        domain_name = _validate_domain(domain_name)
    """
    domain = domain.strip().lower()
    if not domain:
        raise NexusApiError(
            "domain_name must not be empty. "
            "Use the lowercase URL slug (e.g. 'forzahorizon6', 'skyrimse')."
        )
    if not _DOMAIN_RE.fullmatch(domain):
        raise NexusApiError(
            f"Invalid domain_name: {domain!r}. "
            "Use the lowercase URL slug (e.g. 'forzahorizon6', 'skyrimse'), "
            "not the display name. Slugs contain only letters, digits, and hyphens."
        )
    return domain


class NexusApiError(Exception):
    """Raised for config, network, or API-level failures with actionable messages.

    ``status`` carries the HTTP status code when the error came from an HTTP
    response (None for network/config errors) so callers can distinguish
    permanent auth failures from transient ones.
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Shared client + helpers
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None


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

_oauth_pending: dict[str, str] | None = None


def _oauth_client_id() -> str:
    return os.environ.get("NEXUS_OAUTH_CLIENT_ID", "").strip()


def _oauth_client_secret() -> str:
    return os.environ.get("NEXUS_OAUTH_CLIENT_SECRET", "").strip()


def _oauth_redirect_uri() -> str:
    return os.environ.get("NEXUS_OAUTH_REDIRECT_URI", "").strip() or "http://localhost/callback"


def _oauth_token_file() -> Path:
    return Path(os.environ.get("NEXUS_OAUTH_TOKEN_FILE", "")).expanduser() if os.environ.get("NEXUS_OAUTH_TOKEN_FILE") else Path.home() / ".nexus-mcp" / "oauth-tokens.json"


def _load_oauth_tokens() -> dict[str, Any] | None:
    try:
        return json.loads(_oauth_token_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_oauth_tokens(tokens: dict[str, Any]) -> None:
    """Persist tokens atomically with owner-only permissions (0600)."""
    path = _oauth_token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
    with contextlib.suppress(OSError):  # Windows: chmod is best-effort
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _clear_oauth_tokens() -> None:
    with contextlib.suppress(OSError):
        _oauth_token_file().unlink()


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
        raise NexusApiError(
            f"OAuth token endpoint returned HTTP {response.status_code} with a non-JSON body.",
            status=response.status_code,
        ) from None
    if response.status_code != 200 or "error" in body:
        detail = body.get("error_description") or body.get("error") or f"HTTP {response.status_code}"
        raise NexusApiError(f"OAuth token request failed: {detail}", status=response.status_code)
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


_REFRESH_LOCK = asyncio.Lock()


async def _oauth_refresh(tokens: dict[str, Any]) -> dict[str, Any] | None:
    """Refresh the access token (single-flight). Returns new tokens, or None if revoked.

    Only 4xx replies count as revocation (tokens cleared) - transient failures
    (5xx, non-JSON, network) keep the tokens and surface the error, so a flaky
    connection can never log the user out.
    """
    async with _REFRESH_LOCK:
        fresh = _load_oauth_tokens()  # another request may have refreshed while we waited
        if fresh is not None and fresh is not tokens and fresh.get("expires_at", 0) > time.time():
            return fresh
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
        except NexusApiError as exc:
            if exc.status is not None and 400 <= exc.status < 500:
                # Per the official guide, a 4xx on refresh means the user revoked the app
                _clear_oauth_tokens()
                return None
            raise  # transient failure - keep tokens
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
_CACHE_MAX_ENTRIES = 512


def _clear_cache() -> None:
    """Drop all cached responses (called when the auth identity changes)."""
    _CACHE.clear()


def _prune_cache() -> None:
    """Keep the cache bounded: drop expired entries, then oldest-first if still over cap."""
    if len(_CACHE) < _CACHE_MAX_ENTRIES:
        return
    now = time.monotonic()
    for key in [k for k, v in _CACHE.items() if v[0] <= now]:
        _CACHE.pop(key, None)
    if len(_CACHE) >= _CACHE_MAX_ENTRIES:
        for key in sorted(_CACHE, key=lambda k: _CACHE[k][0])[: len(_CACHE) - _CACHE_MAX_ENTRIES + 1]:
            _CACHE.pop(key, None)


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


def _cache_key(method: str, path: str, params: dict[str, Any] | None, data: dict[str, Any] | None) -> str:
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
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
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
        _prune_cache()
        _CACHE[key] = (time.monotonic() + ttl, payload, rl)
    return payload, rl


async def _request(
    method: str,
    path: str,
    params: dict[str, Any] | None,
    data: dict[str, Any] | None,
) -> tuple[Any, dict[str, str]]:
    client = _get_client()
    try:
        response = await client.request(method, path, params=params, data=data, headers=await _auth_headers())
    except httpx.TimeoutException as exc:
        raise NexusApiError("Request timed out after 30s. Try again.") from exc
    except httpx.HTTPError as exc:
        raise NexusApiError(f"Network error: {type(exc).__name__}: {exc}") from exc

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
        ) from None


def _dump(payload: Any, rl: dict[str, str]) -> str:
    """Serialize a payload with a compact rate-limit snapshot appended."""
    body: Any = {**payload, "_rl": rl} if isinstance(payload, dict) else {"result": payload, "_rl": rl}
    return json.dumps(body, indent=2, ensure_ascii=False)


async def _call(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> str:
    try:
        payload, rl = await _api(method, path, params=params, data=data)
        return _dump(payload, rl)
    except NexusApiError as exc:
        return f"Error: {exc}"


GRAPHQL_TTL = 60  # GraphQL POSTs cached briefly - repeated identical queries within a session are cheap


async def _graphql(query: str, variables: dict[str, Any] | None = None) -> tuple[Any, dict[str, str]]:
    """Execute a Nexus v2 GraphQL query (POST JSON); returns (data, rate-limit headers).

    v2 GraphQL has a rate-limit pool separate from v1 REST, so search/detail
    queries here do not consume the v1 quota.

    Mutations are never cached - a repeated toggle must actually hit the server.
    """
    body = {"query": query, "variables": variables or {}}
    is_mutation = query.lstrip().startswith("mutation")
    key = None if is_mutation else _cache_key("POST", GRAPHQL_PATH, None, body)
    hit = _CACHE.get(key) if key else None
    if hit is not None and hit[0] > time.monotonic():
        return hit[1], hit[2]

    client = _get_client()
    try:
        response = await client.post(GRAPHQL_PATH, json=body, headers=await _auth_headers())
    except httpx.TimeoutException as exc:
        raise NexusApiError("Request timed out after 30s. Try again.") from exc
    except httpx.HTTPError as exc:
        raise NexusApiError(f"Network error: {type(exc).__name__}: {exc}") from exc

    rl = _rl_snapshot(response)
    if response.status_code >= 400:
        raise NexusApiError(f"GraphQL error {response.status_code}. {_status_hint(response.status_code)}".strip())
    try:
        result = response.json()
    except json.JSONDecodeError:
        raise NexusApiError("GraphQL endpoint returned a non-JSON response - possibly a firewall/CDN error page. Retry.") from None

    errors = result.get("errors")
    if errors:
        msgs = "; ".join(str(e.get("message", e)) for e in errors if isinstance(e, dict)) or json.dumps(errors)
        raise NexusApiError(f"GraphQL query failed: {msgs}")

    data = result.get("data")
    if key:
        _prune_cache()
        _CACHE[key] = (time.monotonic() + GRAPHQL_TTL, data, rl)
    return data, rl


async def _gql_call(query: str, variables: dict[str, Any] | None = None) -> str:
    try:
        data, rl = await _graphql(query, variables)
        return _dump(data, rl)
    except NexusApiError as exc:
        return f"Error: {exc}"


# ---------------------------------------------------------------------------
# Tools: account
# ---------------------------------------------------------------------------

def _opt(value: Any, default: Any = None) -> Any:
    """Unwrap a FastMCP Optional param that arrives as a FieldInfo when unset."""
    if isinstance(value, FieldInfo):
        return default
    return value


def _qlit(value: Any) -> str:
    """Render a Python value as a GraphQL inline literal.

    json.dumps covers strings/numbers/bools/lists; quoted enum names are
    accepted for enum inputs by graphql-core servers.
    """
    return json.dumps(value)


def _inline_args(**params: Any) -> str:
    """Build a GraphQL argument string from optional keyword params."""
    parts: list[str] = []
    for name, value in params.items():
        value = _opt(value)
        if value is None:
            continue
        parts.append(f"{name}: {_qlit(value)}")
    return ", ".join(parts)


def _split_ids(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]

_MOD_SEARCH_FIELDS = """
      modId uid name summary author version downloads endorsements fileSize
      adultContent status supportsVortex pictureUrl thumbnailUrl
      createdAt updatedAt
      game { domainName name }
      modCategory { name }
      uploader { memberId name }
"""


_GAME_ID_QUERY = """
query GameId($domain: String!) {
  game(domainName: $domain) { id domainName name }
}
"""


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



