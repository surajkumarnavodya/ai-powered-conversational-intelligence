"""Opt-in live integration tests.

Everything here needs real credentials or real infrastructure, so it is
**deselected by default** (`addopts = "-m 'not live'"` in `pyproject.toml`)
and the ordinary suite stays fully mocked per `CLAUDE.md` 7.

Run a subset explicitly once its credentials exist:

    pytest -m live tests/live/test_live_web_search.py

Each module skips itself with a named reason when its configuration is
absent, so an unconfigured run reports *which* credential is missing
rather than failing opaquely. See `docs/LIVE_INTEGRATION_TESTING.md` for
the per-provider setup.
"""
