# Security Policy

## Reporting a vulnerability

Please report security issues privately via
[GitHub's private vulnerability reporting](https://github.com/Talya1412/nexus-mcp/security/advisories/new)
rather than a public issue.

This tool handles Nexus Mods API keys and OAuth tokens — anything that could leak,
persist, or transmit credentials unintentionally is in scope.

## Scope notes

- OAuth tokens are stored in `~/.nexus-mcp/oauth-tokens.json` (override with
  `NEXUS_OAUTH_TOKEN_FILE`) with owner-only permissions on POSIX systems.
- The server talks only to `api.nexusmods.com` and `users.nexusmods.com`.
