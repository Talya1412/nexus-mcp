"""Tools: Nexus Mods REST API v1 - key validation, games, mods, files, downloads, MD5 search, tracking, endorsements."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import Field

from .._core import (
    DOMAIN_DESC,
    NexusApiError,
    _api,
    _call,
    _check_domain,
    _dump,
)
from .._server import mcp

_LOG = logging.getLogger(__name__)


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
    filter: str | None = Field(
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
    if err := _check_domain(domain_name):
        return err
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
    if err := _check_domain(domain_name):
        return err
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
    if err := _check_domain(domain_name):
        return err
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
    if err := _check_domain(domain_name):
        return err
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
    if err := _check_domain(domain_name):
        return err
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
    if err := _check_domain(domain_name):
        return err
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
    if err := _check_domain(domain_name):
        return err
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
    category: str | None = Field(
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
    if err := _check_domain(domain_name):
        return err
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
    if err := _check_domain(domain_name):
        return err
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
    key: str | None = Field(
        default=None,
        description="'key' from a .nxm download link. REQUIRED for non-premium accounts (403 otherwise).",
    ),
    expires: int | None = Field(
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
    if err := _check_domain(domain_name):
        return err
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
    destination: str | None = Field(
        default=None,
        description="Directory to save the file into (created if missing). Defaults to the current working directory.",
    ),
    key: str | None = Field(
        default=None,
        description="'key' from a .nxm download link. REQUIRED for non-premium accounts (403 otherwise).",
    ),
    expires: int | None = Field(
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
        JSON {file, bytes, md5, sha256, overwrote, mirror, _rl} or an error string.
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

    if err := _check_domain(domain_name):
        return err
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
    dest_dir = Path(destination).expanduser() if destination else Path.cwd()  # noqa: ASYNC240 - one-shot local mkdir; anyio.path is not worth it
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        out_path = dest_dir / filename
    except OSError as exc:
        return f"Error: cannot use destination '{destination}': {exc}"

    existed = out_path.exists()
    if existed:
        _LOG.warning("Overwriting existing file: %s", out_path)

    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    total = 0
    exceeded = False
    try:
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=True) as hc,
            hc.stream("GET", uri) as resp,
        ):
            if resp.status_code >= 400:
                return (
                    f"Error: CDN returned HTTP {resp.status_code} for the file download "
                    "(expired link or premium required?). Re-run to mint a fresh link."
                )
            with open(out_path, "wb") as fh:  # noqa: ASYNC230 - small buffered writes; aiofiles is not worth a dep
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
        with contextlib.suppress(OSError):
            out_path.unlink()
        return f"Error: file exceeded max_bytes={max_bytes}; aborted and deleted the partial file."

    return json.dumps(
        {
            "file": str(out_path),
            "bytes": total,
            "md5": md5.hexdigest(),
            "sha256": sha256.hexdigest(),
            "overwrote": existed,
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
    if err := _check_domain(domain_name):
        return err
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
    if err := _check_domain(domain_name):
        return err
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
    if err := _check_domain(domain_name):
        return err
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
    if err := _check_domain(domain_name):
        return err
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
    if err := _check_domain(domain_name):
        return err
    return await _call(
        "POST",
        f"/v1/games/{domain_name}/mods/{mod_id}/abstain.json",
        data={"version": version},
    )
