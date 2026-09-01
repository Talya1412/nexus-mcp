# nexus-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![CI](https://github.com/Talya1412/nexus-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Talya1412/nexus-mcp/actions/workflows/ci.yml)
[![MCP](https://img.shields.io/badge/protocol-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io)

**135 MCP tools** for [Nexus Mods](https://www.nexusmods.com) — wraps the official
**REST API v1** and **GraphQL API v2** as a Model Context Protocol server (Python +
FastMCP, stdio transport). Lets any MCP client (Claude Desktop, opencode, Cursor, ...)
browse games, inspect mods and files, run free-text searches, download mod files with
checksum verification, manage endorsements, comments, collections, and user preferences.

## Highlights

- **Full API coverage** — 69 read tools + 66 mutations across v1 REST and v2 GraphQL.
  Includes things v1 doesn't offer: free-text mod search, batch mod lookups, comment
  threads, collection lifecycle, and quota-free GraphQL reads.
- **Built-in TTL cache** — repeated identical GETs within a session don't consume quota
  (games 1h, mod/file data 5 min, GraphQL POSTs 60 s). Personal state (`/user/*`) is
  never cached.
- **Dual authentication** — personal API key out of the box; optional OAuth2 (PKCE S256)
  with auto-refresh for user-context mutations that API keys cannot perform.
- **Safe downloads** — `nexus_download_mod_file` streams from the CDN to disk with MD5 +
  SHA-256 verification and a configurable size cap.
- **Rate-limit transparency** — every v1 response carries an `_rl` snapshot of Nexus'
  hourly/daily limit headers.

## Install

```bash
# uv (recommended for MCP servers)
uvx --from git+https://github.com/Talya1412/nexus-mcp nexus-mcp

# pipx
pipx install git+https://github.com/Talya1412/nexus-mcp

# pip
pip install git+https://github.com/Talya1412/nexus-mcp
```

Or from a cloned repository:

```bash
pip install -r requirements.txt
python -m nexus_mcp
```

## Works with every MCP harness

Create an API key at <https://www.nexusmods.com/users/myaccount?tab=api%20access>,
then pick your harness below. The server is a single stdio process — no ports,
no daemons, no database.

**Zero-install (recommended):** run straight from GitHub with `uvx` — nothing to
clone, no venv to manage, auto-fetched on first run:

```json
{
  "mcpServers": {
    "nexus": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Talya1412/nexus-mcp", "nexus-mcp"],
      "env": { "NEXUS_API_KEY": "<your-key>" }
    }
  }
}
```

If you installed locally (`pipx install git+https://github.com/Talya1412/nexus-mcp`),
use `"command": "nexus-mcp"` without the `uvx` wrapper instead.

<details>
<summary>Claude Code (one-liner)</summary>

```bash
claude mcp add nexus -e NEXUS_API_KEY=<your-key> -- uvx --from git+https://github.com/Talya1412/nexus-mcp nexus-mcp
```

</details>

<details>
<summary>opencode (<code>opencode.json</code>)</summary>

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "nexus": {
      "type": "local",
      "command": ["uvx", "--from", "git+https://github.com/Talya1412/nexus-mcp", "nexus-mcp"],
      "enabled": true,
      "environment": {
        "NEXUS_API_KEY": "<your-key>"
      }
    }
  }
}
```

</details>

<details>
<summary>Claude Desktop (<code>claude_desktop_config.json</code>)</summary>

```json
{
  "mcpServers": {
    "nexus": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Talya1412/nexus-mcp", "nexus-mcp"],
      "env": {
        "NEXUS_API_KEY": "<your-key>"
      }
    }
  }
}
```

</details>

<details>
<summary>Cursor / Windsurf / Cline (any <code>mcpServers</code> JSON)</summary>

Same shape as the generic JSON above. Paste the `mcpServers` block into:
Cursor — `.cursor/mcp.json` · Windsurf — `~/.codeium/windsurf/mcp_config.json` ·
Cline — extension MCP server settings.

</details>

<details>
<summary>VS Code (<code>.vscode/mcp.json</code>)</summary>

```json
{
  "servers": {
    "nexus": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Talya1412/nexus-mcp", "nexus-mcp"],
      "env": {
        "NEXUS_API_KEY": "<your-key>"
      }
    }
  }
}
```

</details>

<details>
<summary>Gemini CLI (<code>~/.gemini/settings.json</code>)</summary>

```json
{
  "mcpServers": {
    "nexus": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Talya1412/nexus-mcp", "nexus-mcp"],
      "env": {
        "NEXUS_API_KEY": "<your-key>"
      }
    }
  }
}
```

</details>

<details>
<summary>Codex CLI (<code>~/.codex/config.toml</code>)</summary>

```toml
[mcp_servers.nexus]
command = "uvx"
args = ["--from", "git+https://github.com/Talya1412/nexus-mcp", "nexus-mcp"]
env = { "NEXUS_API_KEY" = "<your-key>" }
```

</details>

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `NEXUS_API_KEY` | yes | Personal API key from Nexus Mods |
| `NEXUS_OAUTH_CLIENT_ID` | no | OAuth client ID (see below) |
| `NEXUS_OAUTH_CLIENT_SECRET` | no | Only for non-public OAuth apps |
| `NEXUS_OAUTH_REDIRECT_URI` | no | Defaults to `http://localhost/callback` |
| `NEXUS_OAUTH_TOKEN_FILE` | no | Token store path, defaults to `~/.nexus-mcp/oauth-tokens.json` |

## Authentication

**API key** works for all public reads and most mutations. Note that the key itself is
*not* an authorization scope: some user-context mutations (e.g.
`nexus_update_mod_direct_download`) are rejected with API-key auth even for the mod's
owner — Nexus requires an OAuth user context for those.

**OAuth (optional).** Nexus has no self-service OAuth app registration; email
`support@nexusmods.com` with your app name, description, logo, source link, and callback
URI to obtain a `client_id`. Then run the two-step flow:

1. `nexus_oauth_login` → returns an `authorize_url` (state + PKCE challenge); open it,
   log in, and copy the `code` from the redirect URL.
2. `nexus_oauth_exchange(code)` → exchanges the code for tokens, persists them to
   `NEXUS_OAUTH_TOKEN_FILE`, and validates your identity.

Tokens last ~6 h and auto-refresh (a 4xx refresh response is treated as revocation and
falls back to API-key auth). Companion tools: `nexus_oauth_status`, `nexus_oauth_refresh`,
`nexus_oauth_logout`. The `public` scope is sufficient; Bearer auth is accepted on both
v1 REST and v2 GraphQL.

## Rate limits & quota

- v1 REST: **2000 requests/hour**, **20 000/day** per key. Every v1 response includes an
  `_rl` snapshot (`X-RL-*` headers) so agents can self-throttle.
- v2 GraphQL has its **own rate-limit pool** and does not consume v1 quota — prefer v2
  tools (`nexus_search_mods`, `nexus_get_mod_v2`, `nexus_get_mods_batch`, ...) for
  searches and public data.
- The server-side TTL cache (see Highlights) further reduces quota usage for repeated
  reads within a session.

## Tool catalog

### v1 REST (core)

| Tool | Description |
|---|---|
| `nexus_validate_key` | Check API key + account info (exempt from rate limits) |
| `nexus_get_games` / `nexus_get_game` | Game catalog (substring filter) / one game + categories |
| `nexus_get_mod` / `nexus_get_mod_changelogs` | Mod details (endorsements, downloads, description) / changelog per version |
| `nexus_get_latest_added` / `nexus_get_latest_updated` / `nexus_get_trending` | 10 newest / updated / trending mods for a game |
| `nexus_get_updated_mods` | Mods with activity in the last `1d`/`1w`/`1m` |
| `nexus_get_mod_files` / `nexus_get_file_info` | Mod file list (category filter) / one file's details (MD5, size, version) |
| `nexus_get_download_link` | Short-lived CDN download URL (non-premium needs `key`+`expires` from a `.nxm` link) |
| `nexus_download_mod_file` | Stream a file to disk (MD5+SHA-256 verified, `max_bytes` cap) |
| `nexus_search_by_md5` | Identify a mod/file from an MD5 hash |
| `nexus_get_tracked_mods` / `nexus_track_mod` / `nexus_untrack_mod` | Manage tracked mods |
| `nexus_get_endorsements` / `nexus_endorse_mod` / `nexus_abstain_endorsement` | Manage endorsements |

### v2 GraphQL (does not consume v1 quota)

| Tool | Description |
|---|---|
| `nexus_search_mods` | Free-text mod search (wildcard) + game/endorsement/download filters + sort + pagination |
| `nexus_get_mod_v2` | Full mod details: raw BBCode description, tags, requirements, complete file list |
| `nexus_get_mods_batch` | Resolve many mods in one query: `"domain:modId,domain:modId"` |
| `nexus_get_mod_endorsers` | Users who endorsed a mod (cursor pagination) |
| `nexus_search_games` / `nexus_get_game_v2` | Game search / rich game details (genre, forum, counts, Vortex support) |
| `nexus_resolve_domain` | Resolve a game display name (e.g. 'Skyrim Special Edition') to its `domain_name` slug — call before any tool needing `domain_name` |
| `nexus_get_files_v2` / `nexus_get_files_by_uid` | File lists via v2 (by domain/modId or by uid) |
| `nexus_search_users` / `nexus_get_user_v2` / `nexus_get_user_by_name` | User search (fuzzy) / public profile by id or username / exact username lookup |
| `nexus_search_collections` / `nexus_get_collection` / `nexus_get_collection_revision` | Collection search / details by slug / single revision |
| `nexus_get_categories` / `nexus_get_category_by_id` / `nexus_get_collection_games` | Collection categories (per-game or global) |
| `nexus_get_tags` / `nexus_get_tags_v2` / `nexus_get_tag_by_id` / `nexus_get_tag_categories` / `nexus_get_tag_category_by_id` | Tag taxonomy |
| `nexus_get_comment_thread` / `nexus_get_comment` / `nexus_search_comments` | Comment reads (⚠️ `search_comments` is 500-ing server-side at Nexus; use `get_comment_thread` instead) |
| `nexus_get_badges` | Static catalog of earnable mod badges |
| `nexus_get_news` | Site/game news, interviews, features (filter by category/game) |
| `nexus_graphql_query` / `nexus_graphql_introspect` | Raw GraphQL escape hatch / schema introspection |
| `nexus_get_age_verification_info` / `nexus_get_api_applications` / `nexus_get_current_warnings` | Account state reads |
| `nexus_get_external_video` / `nexus_get_game_artwork` / `nexus_get_legacy_mods` / `nexus_get_file_hash(es)` | Media + legacy lookups |
| `nexus_search_media` | Site-wide media search (⚠️ Nexus endpoint is intermittently flaky — retry) |
| `nexus_get_preferences` / `nexus_update_preferences` | Site preferences (emails, default tabs, download location...) |
| `nexus_get_user_donation_preferences` / `nexus_update_user_donation_preferences` | Donation Points preferences |
| `nexus_get_favourite_games` / `nexus_add_favourite_game` / `nexus_remove_favourite_game` | Favourite games |
| `nexus_get_ignored_users` / `nexus_ignore_user` / `nexus_unignore_user` | Ignored users |
| `nexus_get_blocked_tags` / `nexus_block_tag` / `nexus_unblock_tag` | Blocked tags |
| `nexus_track_user` / `nexus_untrack_user` | Track/untrack users for update notifications |
| `nexus_give_kudos` / `nexus_remove_kudos` | Kudos |
| `nexus_like_comment` / `nexus_remove_comment_like` | Comment likes |
| `nexus_create_comment` / `nexus_edit_comment` / `nexus_discard_comment` / `nexus_restore_comment` | Comment mutations (nested replies via `reply_to_id`) |
| `nexus_create_message` / `nexus_upload_attachment` | Private messages (multipart attachments) |
| `nexus_update_about_me` / `nexus_update_country` | Profile updates |
| `nexus_get_speedtest_urls` | CDN speedtest endpoints |
| `nexus_get_opted_in_mods` / `nexus_get_transactions` / `nexus_get_user_monthly_summary` / `nexus_get_user_monthly_report` / `nexus_get_user_monthly_report_by_id` | Donation Points reporting (⚠️ some data is hidden under API-key auth — OAuth required) |
| `nexus_get_uploads` | Upload activity with scan status |

### Collections, moderation & misc mutations

| Tool | Description |
|---|---|
| `nexus_create_collection` / `nexus_edit_collection` | Create/edit collections (manifest, BBCode description) |
| `nexus_create_or_update_revision` / `nexus_update_revision` / `nexus_publish_revision` / `nexus_retract_revision` / `nexus_discard_revision` | Revision lifecycle |
| `nexus_list_collection` / `nexus_unlist_collection` / `nexus_discard_collection` | Collection visibility lifecycle |
| `nexus_create_changelog` / `nexus_update_changelog` | Revision changelogs |
| `nexus_create_tag` / `nexus_update_tag` / `nexus_discard_tag` | Tag management (moderator) |
| `nexus_add_badge_to_collection` / `nexus_remove_badge_from_collection` / `nexus_reorder_item` | Collection content management |
| `nexus_close_collection_bug_report` | Close bug reports on your collections |
| `nexus_submit_moderation_fix` | Submit fixes for moderated content |
| `nexus_hide_comment` / `nexus_lock_comment` / `nexus_lock_comment_thread` / `nexus_pin_comment` / `nexus_unpin_comment` / `nexus_reorder_pinned_comments` / `nexus_clear_comment_moderation_status` / `nexus_clear_comment_thread_moderation_status` | Comment moderation (moderator/owner) |
| `nexus_block_mods_from_earning_dp` / `nexus_unblock_mods_from_earning_dp` | Donation Points earning control (moderator) |
| `nexus_track_app_metric` | App metrics (e.g. Vortex collection installs) |
| `nexus_update_mod_direct_download` | Toggle direct downloads on your own mods (**OAuth required**) |
| `nexus_start_age_verification_flow` / `nexus_start_age_verification_appeal_flow` | Age verification flows |
| `nexus_request_media_upload_url` / `nexus_get_collection_revision_upload_url` | Presigned upload URLs |
| `nexus_oauth_login` / `nexus_oauth_exchange` / `nexus_oauth_status` / `nexus_oauth_refresh` / `nexus_oauth_logout` | OAuth2 flow (PKCE S256, auto-refresh, apikey fallback) |

## API quirks worth knowing

- `domain_name` is the lowercase URL slug (e.g. `forzahorizon6`, `skyrimse`) — **not**
  the display name.
- Download links are short-lived; don't cache them. Non-premium accounts must pass the
  `key`/`expires` pair extracted from a `.nxm` link generated on the website.
- Preference mutations (`ignore_user`, `block_tag`, ...) apply immediately but list reads
  can lag a few seconds behind (eventual consistency).
- `nexus_discard_comment` is effectively one-way under API-key auth: restoring requires
  OAuth Bearer auth.
- The v1 categories endpoint (`/v1/games/{domain}/categories.json`) was removed by Nexus
  — categories are only available via v2.
- `nexus_search_comments` currently returns HTTP 500 from Nexus itself, regardless of
  parameters; `nexus_get_comment_thread` is the working alternative.
- Scraping the nexusmods.com website directly gets blocked by Cloudflare — use the API
  tools instead.

## Development

```bash
pip install -r requirements.txt
python -m compileall nexus_mcp           # syntax check
python -m nexus_mcp                      # run on stdio
python -c "import asyncio, nexus_mcp; print(len(asyncio.run(nexus_mcp.mcp.list_tools())))"  # tool count
```

CI runs on every push: syntax compile, import check, and tool-count verification.

## License

[MIT](LICENSE)
