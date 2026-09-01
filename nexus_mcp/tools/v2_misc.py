"""Tools: v2 GraphQL misc - files by UID, favourites, ignored users, tag blocking, user lookups, speedtest."""

from __future__ import annotations

import json

from pydantic import Field

from .._annotations import (
    _IDEMPOTENT_MUTATION_ANNOTATIONS,
    _READ_ONLY_ANNOTATIONS,
)
from .._core import (
    _gql_call,
)
from .._server import mcp

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
        description='Comma-separated numeric mod UIDs (big uid, e.g. "39715562587071"; not the per-game mod ID).',
    ),
    offset: int = Field(default=0, description="Offset into the file list.", ge=0),
    count: int = Field(default=50, description="Max files to return.", ge=1, le=100),
) -> str:
    """Get mod file lists by mod UID(s) - no domain/modId pair needed [v2 - no v1 quota].

    Ideal when you only have the uid (e.g. from a .nxm link or your own mod
    pipeline).
    NOTE: the response does NOT include domainName/modId - resolve the
    owning game/mod separately (e.g. nexus_search_by_md5) before calling
    the download tools.

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
    """List the authenticated user's favourite games [v2 - no v1 quota].

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
    """List the current user's ignored (muted) users [v2 - no v1 quota].

    Returns:
        JSON array of users: {memberId, name, avatar, viewerHasIgnored}.
    """
    return await _gql_call("query { ignoredUsers { memberId name avatar viewerHasIgnored } }")


@mcp.tool(name="nexus_ignore_user", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Ignore a user (v2)"})
async def nexus_ignore_user(
    user_id: int | None = Field(default=None, description="Nexus Mods member ID to ignore. Provide this or username.", ge=1),
    username: str | None = Field(default=None, description="Exact Nexus username to ignore. Provide this or user_id."),
) -> str:
    """Ignore (mute) a user - hides their content in your feed [v2 - no v1 quota].

    Personal preference only: no public side effect. Reversible with
    nexus_unignore_user. Provide user_id OR username (at least one).
    NOTE: applies immediately but list reads (nexus_get_ignored_users)
    can lag several seconds - wait before re-reading to confirm.

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


@mcp.tool(name="nexus_unignore_user", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Unignore a user (v2)"})
async def nexus_unignore_user(
    user_id: int | None = Field(default=None, description="Nexus Mods member ID to unignore. Provide this or username.", ge=1),
    username: str | None = Field(default=None, description="Exact Nexus username to unignore. Provide this or user_id."),
) -> str:
    """Unignore (unmute) a user - restores their content to your feed [v2 - no v1 quota].

    Personal preference only: no public side effect. Provide user_id OR
    username (at least one).
    NOTE: applies immediately but list reads (nexus_get_ignored_users)
    can lag several seconds - wait before re-reading to confirm.

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
    """List the current user's blocked tags [v2 - no v1 quota].

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


@mcp.tool(name="nexus_block_tag", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Block a tag (v2)"})
async def nexus_block_tag(
    tag_id: int = Field(..., description="Tag ID to block (from nexus_get_tags / mod tag lists).", ge=1),
) -> str:
    """Block a tag - hides matching content from your searches [v2 - no v1 quota].

    Personal preference only: no public side effect. Reversible with
    nexus_unblock_tag. Only blockable tags can be blocked.
    NOTE: preference mutations apply immediately but list reads
    (nexus_get_blocked_tags) can lag several seconds - wait before
    re-reading to confirm.

    Returns:
        JSON {blockTag: {success}} or an error string.
    """
    return await _gql_call(
        "mutation($t: ID!) { blockTag(tagId: $t) { ... on BlockTagMutationPayload { success } } }",
        {"t": str(tag_id)},
    )


@mcp.tool(name="nexus_unblock_tag", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Unblock a tag (v2)"})
async def nexus_unblock_tag(
    tag_id: int = Field(..., description="Tag ID to unblock.", ge=1),
) -> str:
    """Unblock a previously blocked tag [v2 - no v1 quota].

    Personal preference only: no public side effect. List reads
    (nexus_get_blocked_tags) can lag several seconds after this
    returns success - wait before re-reading to confirm.

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
    """Get a user profile by exact username [v2 - no v1 quota].

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
    """Get one month of download/upload numbers for an account [v2 - no v1 quota].

    Companion to nexus_get_user_monthly_summary (which lists available
    months): this fetches the actual per-mod/per-game values for a chosen
    month. Useful for tracking your own mod's download history.
    NOTE: Nexus hides this report for privacy-restricted accounts
    ("UserMonthlyReport was hidden due to permissions") - that is an
    API-side restriction, not a tool failure.

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
    """Get CDN speedtest URLs for diagnosing slow downloads [v2 - no v1 quota].

    Handy when downloads feel slow: test latency/throughput against each
    mirror and compare.

    Returns:
        JSON array of {title, description, location, tag}.
    """
    return await _gql_call("query { speedtestUrls { title description location tag } }")


# ---------------------------------------------------------------------------
# Tools: OAuth2 login lifecycle
# ---------------------------------------------------------------------------
