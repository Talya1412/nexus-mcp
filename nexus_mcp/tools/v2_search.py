"""Tools: v2 GraphQL search + rich lookups (mods, users, games, collections, raw GraphQL)."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field

from .._annotations import (
    _MUTATING_ANNOTATIONS,
    _READ_ONLY_ANNOTATIONS,
)
from .._core import (
    _GAME_ID_QUERY,
    _MOD_SEARCH_FIELDS,
    DOMAIN_DESC,
    _check_domain,
    _gql_call,
    _gql_page,
)
from .._server import mcp

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


_SEARCH_MODS_QUERY = """
query SearchMods($filter: ModsFilter, $sort: [ModsSort!], $offset: Int, $count: Int) {
  mods(filter: $filter, sort: $sort, offset: $offset, count: $count) {
    totalCount
    nodes {
%s
    }
  }
}
""" % _MOD_SEARCH_FIELDS  # noqa: UP031 - GraphQL braces conflict with str.format/f-strings; % is intentional


def _mods_sort(sort: str, direction: str) -> list[dict[str, Any]]:
    key = _SORT_KEY_MAP[sort]
    return [{key: {"direction": direction}}]


@mcp.tool(
    name="nexus_search_mods",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Search Nexus mods"},
)
async def nexus_search_mods(
    term: str | None = Field(default=None, description="Free-text term matched against mod names (wildcard match). Optional."),
    domain_name: str | None = Field(default=None, description=DOMAIN_DESC),
    sort: Literal[
        "endorsements", "downloads", "unique_downloads", "created_at", "updated_at",
        "name", "relevance", "size", "last_comment",
    ] = Field(default="endorsements", description="Sort key. Array order is precedence but only one key is exposed here."),
    direction: Literal["DESC", "ASC"] = Field(default="DESC", description="Sort direction."),
    min_endorsements: int | None = Field(default=None, description="Only mods with at least this many endorsements.", ge=0),
    min_downloads: int | None = Field(default=None, description="Only mods with at least this many downloads.", ge=0),
    exclude_adult: bool = Field(default=False, description="If true, exclude adult-content mods from results."),
    offset: int = Field(default=0, description="Offset-based pagination start.", ge=0),
    count: int = Field(default=20, description="Results per page. Server silently caps page size (~50-80); check '_returned'.", ge=1, le=100),
) -> str:
    """Search Nexus Mods with free text + filters, sorted and paginated.

    Returns:
        JSON {totalCount, _returned, nodes: [{modId, uid, name, summary, author, version, downloads, endorsements, game, uploader, ...}]}; description (full BBCode) NOT included - use nexus_get_mod_v2 for full details.
    """
    flt: dict[str, Any] = {}
    if term:
        flt["name"] = [{"value": term, "op": "WILDCARD"}]
    if domain_name:
        if err := _check_domain(domain_name):
            return err
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
        return json.dumps(result, separators=(",", ":"), ensure_ascii=False)
    return data



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
    """Get rich mod details (v2): raw-BBCode description, tags, requirements, complete file list - none exposed by v1 REST.

    Returns:
        JSON {mod: {..., description (BBCode + literal <br /> tags - render or strip before display), requirements}, files: [{fileId, name, version, category, sizeInBytes, totalDownloads, date, description}]}.
    """
    if err := _check_domain(domain_name):
        return err
    game_data = await _gql_call(_GAME_ID_QUERY, {"domain": domain_name})
    try:
        game = json.loads(game_data)
    except json.JSONDecodeError:
        return game_data
    game_id = (game or {}).get("game", {}).get("id") if isinstance(game, dict) else None
    if not game_id:
        return json.dumps(
            {"error": f"Unknown domain_name '{domain_name}' (game lookup returned no id).", "game": game},
            separators=(",", ":"),
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
    member_id: int | None = Field(default=None, description="Numeric member ID (user_id from nexus_validate_key).", ge=1),
    username: str | None = Field(default=None, description="Exact Nexus username, e.g. 'Talya1412'."),
) -> str:
    """Get a public Nexus Mods user profile by member ID or exact username.

    Provide exactly one of member_id or username.

    Returns:
        JSON {memberId, name, about, avatar, modCount, joined, kudos, contributedModCount, collectionCount, recognizedAuthor, lastActive, posts}.
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
        return json.dumps(user, separators=(",", ":"), ensure_ascii=False)
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
    term: str | None = Field(default=None, description="Free-text general search term (matches name/summary/etc). Optional."),
    domain_name: str | None = Field(default=None, description=DOMAIN_DESC),
    sort: Literal[
        "endorsements", "downloads", "created_at", "updated_at", "rating", "recent_rating", "relevance",
    ] = Field(default="endorsements", description="Sort key."),
    direction: Literal["DESC", "ASC"] = Field(default="DESC", description="Sort direction."),
    offset: int = Field(default=0, description="Offset-based pagination start.", ge=0),
    count: int = Field(default=20, description="Results per page. Server may silently cap page size; check '_returned'.", ge=1, le=100),
) -> str:
    """Search Nexus Mods collections (curated mod packs) with free text.

    Returns:
        JSON {totalCount, _returned, nodes: [{slug, name, summary, endorsements, totalDownloads, overallRating, game, user, ...}]}.
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
        return json.dumps(result, separators=(",", ":"), ensure_ascii=False)
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

    Useful for discovering filters, sorts, and fields before composing a query with nexus_graphql_query.

    Returns:
        JSON {name, kind, description, fields: [{name, type}], inputFields, enumValues} or null if the type does not exist.
    """
    return await _gql_call(_GQL_INTROSPECT_QUERY, {"t": type_name})


@mcp.tool(
    name="nexus_graphql_query",
    annotations={**_MUTATING_ANNOTATIONS, "title": "Run raw v2 GraphQL query"},
)
async def nexus_graphql_query(
    query: str = Field(..., description="GraphQL query document, e.g. 'query { games(count: 5) { nodes { name domainName } } }'."),
    variables: str = Field(default="{}", description="JSON object string with query variables, e.g. '{\"term\": \"sky\"}'."),
) -> str:
    """Run a raw query against the Nexus v2 GraphQL API (power-user escape hatch).

    Read-only: most mutations need OAuth scopes this server lacks and fail cleanly. Introspect types with nexus_graphql_introspect first.

    Returns:
        The raw GraphQL 'data' payload as JSON, or 'Error: ...' with server-side GraphQL error messages.
    """
    try:
        parsed_vars = json.loads(variables)
    except json.JSONDecodeError as exc:
        return f"Error: variables is not valid JSON ({exc})."
    if not isinstance(parsed_vars, dict):
        return "Error: variables must be a JSON object string."
    return await _gql_call(query, parsed_vars)



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
    """Search Nexus Mods users by username (v2 GraphQL); supports partial names unlike nexus_get_user_v2.

    Returns:
        JSON {totalCount, _returned, nodes: [{memberId, name, avatar, modCount, joined, kudos, ...}]}.
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
    term: str | None = Field(default=None, description="Free-text term matched against game names (wildcard match). Optional."),
    sort: Literal["downloads", "mods", "collections", "name", "approved", "relevance"] = Field(
        default="downloads", description="Sort key."
    ),
    direction: Literal["DESC", "ASC"] = Field(default="DESC", description="Sort direction."),
    offset: int = Field(default=0, description="Offset-based pagination start.", ge=0),
    count: int = Field(default=20, description="Results per page.", ge=1, le=100),
) -> str:
    """Search Nexus Mods games by name, sorted and paginated (v2 GraphQL).

    Complements nexus_get_games (full cached catalog).

    Returns:
        JSON {totalCount, _returned, nodes: [{domainName, name, id, modCount, downloadCount, genre, forumUrl, supportsVortex, approvedAt}]}.
    """
    flt: dict[str, Any] | None = None
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


@mcp.tool(
    name="nexus_resolve_domain",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Resolve a game name to its domain slug (v2)"},
)
async def nexus_resolve_domain(
    game_name: str = Field(
        ...,
        description="Game display name or partial name, e.g. 'Skyrim Special Edition', 'fallout 4', 'witcher'.",
    ),
) -> str:
    """Resolve a game display name to its `domain_name` slug (v2 GraphQL).

    Call first when you only know the game's name; most tools need the lowercase domain slug, not the display name.

    Returns:
        JSON {totalCount, _returned, nodes: [{domainName, name, id}]}; best matches first - take `domainName` from the intended node.
    """
    data = await _gql_call(
        _GAMES_SEARCH_QUERY,
        {
            "filter": {"name": [{"value": game_name, "op": "WILDCARD"}]},
            "sort": [{"downloads": {"direction": "DESC"}}],
            "offset": 0,
            "count": 10,
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
    """Get rich game details via v2 GraphQL: mod/download/collection counts, genre, forum URL, Vortex support.

    Returns:
        JSON game object, or {error: ...} for unknown domains.
    """
    if err := _check_domain(domain_name):
        return err
    return await _gql_call(_GAME_DETAIL_QUERY, {"domain": domain_name})
