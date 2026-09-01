# Changelog

All notable changes to `nexus-mods-mcp` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - to be released as 1.4.0

### Added
- `NEXUS_MCP_TOOLS` tool profiles: `all` (default, 140 tools), `read` (74 read-only tools), `rw` (all but the 12 destructive tools). Invalid values fail loudly at startup. (#20)
- `nexus_get_mod_posts` reads a mod's posts/comment tab with pagination and reply-thread structure; `nexus_find_unreplied_comments` scans up to `max_mods` mods by author/ID and reports threads the mod author hasn't replied to. Both use the web UI (posts tab) where the v2 API exposes no comment tree. (#5)
- Server `serverInfo` now advertises the package version (`APP_VERSION`) and the repository URL. (#12)
- `CHANGELOG.md`. (#12)
- Registry invariants for `serverInfo` version + `website_url`. (#12)
- Download helper now verifies the file's SHA-256 from Nexus' virus-scan endpoint in addition to MD5, with atomic temp-file replacement. (#19)
- OAuth pre-checks for `nexus_restore_comment` and `nexus_get_transactions` return a clear "run nexus_oauth_login first" error instead of an opaque API failure. (#16-#H5)
- Tool surface trimmed and docstrings tightened so agent token budgets stay small. (#15)

### Changed
- `nexus_graphql_query` documents that mutations are sent verbatim; user-scoped mutations may fail cleanly when the server's OAuth scopes don't cover them. (#10)
- Error and status hints are path-aware (e.g. `download_link` on HTTP 400/403), and the 429 suffix no longer prints a false "retry after" value. (#11, #15)
- Collection create/update now only sends non-empty fields and validates `mods_json` / `collection_data_json` with actionable soft errors. (#16)
- Response cache, rate-limit handling and optional-parameter handling verified against live API responses. (#16)
- Registry/profile counting now reflects the exact 140-tool surface (74 read-only, 12 destructive). (#12, #20)

### Fixed
- `nexus_get_mod_files` ignored a non-string `category` sentinel instead of applying no filter. (#16)
- `nexus_unlist_collection` sent an object payload instead of the collection id. (#18)
- FastMCP optional parameters are parsed as `None` rather than `FieldInfo` sentinels. (#16)
- `_core` globals no longer leak into the public namespaces of the oauth tool module. (#9)

## [1.2.0] - 2026-09-01

### Added
- Domain-name slug validation before every API call, with actionable hints when a display name is passed.
- Logging and reporting when a downloaded file overwrites an existing one.

### Changed
- Honor `Retry-After` on HTTP 429 responses.
- README points usage questions and ideas to the Discussions tab.

### Fixed
- None.

## [1.1.1] - 2026-09-01

### Added
- Contribution framework: contributor guide, issue/PR templates and a security policy.
- PyPI version badge in the README.

### Changed
- Restructured the single-file server into the `nexus_mcp` package with a shared FastMCP instance.
- Renamed the PyPI distribution to `nexus-mods-mcp`; installs point at PyPI (`uvx --from nexus-mods-mcp nexus-mcp`).
- Hardened auth and cache handling for production and reworked CI with a real test suite.
- Release uploads made idempotent with `twine --skip-existing`.

### Fixed
- None.