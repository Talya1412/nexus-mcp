"""FastMCP instance (leaf module: no tool imports, so any import order works)."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "nexus_mcp",
    instructions=(
        "Nexus Mods API tools (v1 REST + v2 GraphQL).\n"
        "- Most tools take `domain_name`: the lowercase game slug (e.g. 'skyrimspecialedition'), "
        "NOT the display name. Resolve unknown game names with nexus_resolve_domain.\n"
        "- Quota strategy: v1 REST allows 2000 req/hour and 20000/day; every v1 response carries an "
        "`_rl` snapshot of the remaining quota. v2 GraphQL tools run on a separate pool and do NOT "
        "consume v1 quota - prefer v2 tools (nexus_search_mods, nexus_get_mod_v2, nexus_get_mods_batch) "
        "for searches and public data. Repeated identical reads are served from a built-in TTL cache "
        "and cost nothing.\n"
        "- Auth: NEXUS_API_KEY covers all public reads and most mutations. Some user-context mutations "
        "require OAuth (nexus_oauth_login then nexus_oauth_exchange) - e.g. nexus_update_mod_direct_download, "
        "nexus_restore_comment, nexus_get_transactions. Tool descriptions flag this where it applies.\n"
        "- Pagination: list tools return {totalCount, _returned, nodes}; paginate with offset += _returned "
        "while offset < totalCount. Page size may be silently capped by the server - always check _returned.\n"
        "- Responses are compact JSON; parse with a JSON parser (do not regex text). Tool failures come "
        "back as 'Error: ...' strings, not exceptions - read the message and adjust inputs.\n"
        "- Tools carry readOnly/destructive/idempotent annotations; treat destructive tools as "
        "user-confirm-worthy."
    ),
)
