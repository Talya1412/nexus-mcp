# Contributing to nexus-mcp

Thanks for your interest in contributing! This document covers setup, conventions, and the things CI will check.

## Development setup

Requires Python 3.10+.

```bash
git clone https://github.com/Talya1412/nexus-mcp.git
cd nexus-mcp
python -m pip install -e ".[dev]"
python -m pytest          # full suite, no network needed
python -m ruff check nexus_mcp tests
```

Run the server locally on stdio:

```bash
python -m nexus_mcp
```

To exercise real API calls, set `NEXUS_API_KEY` (personal API key from
https://www.nexusmods.com/users/myaccount?tab=api%20access). Never commit keys or
tokens, and never paste them into issues or PRs.

## Repository layout

```
nexus_mcp/
  _core.py         # HTTP pipeline: auth, retries hints, TTL cache, GraphQL, OAuth
  _server.py       # FastMCP instance
  _annotations.py  # readOnly / mutating / idempotent annotation presets
  tools/           # tool modules grouped by API surface (v1_rest, v2_*, oauth)
tests/
  conftest.py      # env isolation + cache/client reset
  test_request.py  # mocked HTTP pipeline via httpx.MockTransport
.github/workflows/ # ci.yml (lint, test matrix, audit, build, live smoke) + release.yml
```

## Adding a tool

1. Pick the right module in `nexus_mcp/tools/` (v1 REST vs v2 GraphQL vs OAuth).
2. Use the annotation presets from `_annotations.py`: read tools get
   `_READ_ONLY_ANNOTATIONS`, safe mutations get `_IDEMPOTENT_MUTATION_ANNOTATIONS`,
   everything else gets `_MUTATING_ANNOTATIONS`. Add `"destructiveHint": True` only
   when misuse can cause real damage (deletion, moderation, irreversible actions).
3. Every tool needs a Google-style docstring with a `Returns:` section — the
   registry tests enforce that tools are documented.
4. **Docstrings are agent-facing token budget**: every tool description is loaded
   into the context of every agent session. Keep them compact — one-sentence
   description, at most one line of point-of-use constraints, and a one-line
   `Returns:` shape. Target ≤300 chars; hard cap 450. Quota/TTL boilerplate
   belongs in the server instructions (`_server.py`), not per-tool.
5. Tool-facing JSON must be compact: `json.dumps(..., separators=(",", ":"))` —
   no `indent=2` in tool outputs (pretty-printing inflates every response).
6. `domain_name` parameters must reuse the `DOMAIN_DESC` wording (lowercase slug,
   not display name).
7. **Tool count matters**: `tests/test_registry.py` and the release workflow's
   smoke test assert exactly `135` tools. If your change adds or removes a tool,
   update both assertions in the same PR.
8. Add tests. Network calls go through `httpx.MockTransport` in tests — the suite
   must stay offline. The live-smoke CI job is the only place real API calls happen.

## Tests and CI

- `pytest` must pass locally before you push. CI runs the same suite on
  ubuntu + windows across Python 3.10-3.12.
- `ruff check` must be clean (config in `pyproject.toml`).
- `pip-audit` runs on every push; a new dependency vulnerability will fail CI.
- GraphQL `query` strings may use `%s`-style templates on purpose (braces conflict
  with GraphQL); these spots carry `noqa: UP031`.

## Commits and pull requests

- Commit messages: short imperative English ("Add retry hint for 429s", not "added stuff").
- Keep PRs focused; one feature or fix per PR.
- Fill in the PR template. CI must be green before review.
- Bug reports: use the issue templates so we get OS/Python/client/version up front.

## License

By contributing you agree your contributions are licensed under the MIT license
(the project's license).
