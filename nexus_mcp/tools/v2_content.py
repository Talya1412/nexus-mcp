"""Tools: v2 GraphQL content - files, mod batches, endorsers, news, categories, tags, collections, comments, monthly reports."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field

from .._annotations import (
    _READ_ONLY_ANNOTATIONS,
)
from .._core import (
    _GAME_ID_QUERY,
    _MOD_SEARCH_FIELDS,
    DOMAIN_DESC,
    _gql_call,
    _gql_page,
)
from .._server import mcp

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
""" % _MOD_SEARCH_FIELDS  # noqa: UP031 - GraphQL braces conflict with str.format/f-strings; % is intentional


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
    after_cursor: str | None = Field(default=None, description="Cursor from a previous page's pageInfo.endCursor."),
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
    category: Literal["SITE_NEWS", "GAME_NEWS", "MOD_NEWS", "INTERVIEWS", "COMPETITIONS", "FEATURES"] | None = Field(
        default=None, description="Filter by news category. Optional."
    ),
    domain_name: str | None = Field(default=None, description=DOMAIN_DESC),
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
    game_id: int | None = None
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
    domain_name: str | None = Field(default=None, description=DOMAIN_DESC),
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
    revision: int | None = Field(default=None, description="Revision number. Omit for the latest revision.", ge=1),
    domain_name: str | None = Field(default=None, description=DOMAIN_DESC),
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
    term: str | None = Field(default=None, description="Free-text search over comment bodies."),
    thread_id: int | None = Field(default=None, description="Restrict results to a single comment thread.", ge=1),
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

    NOTE: only thread IDs returned by the GraphQL API itself resolve
    (e.g. from nexus_search_mods-related queries or createComment).
    Thread IDs scraped from mod-page posts-tab HTML do NOT resolve
    ("not found").

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
