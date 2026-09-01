## What does this PR change?

<!-- One or two sentences: what and why. Link related issues with "Fixes #N". -->

## Checklist

- [ ] `python -m pytest` passes locally (no network required)
- [ ] `python -m ruff check nexus_mcp tests` is clean
- [ ] Tool count assertions updated if tools were added/removed
      (`tests/test_registry.py` + `.github/workflows/release.yml` smoke test)
- [ ] New/changed tools have docstrings with a `Returns:` section and correct annotations
- [ ] No secrets, API keys, or tokens committed

## How was this tested?

<!-- Commands you ran and their outcome, e.g. pytest output or manual client runs. -->
