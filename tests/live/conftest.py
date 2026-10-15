"""Credential gating for the opt-in live tests.

Two jobs:

1. **Skip with a named reason.** An unconfigured run should say *which*
   credential is missing, not fail opaquely or -- worse -- pass because
   the code under test silently degraded.
2. **Undo the ordinary suite's env stripping.** `tests/conftest.py`
   deliberately strips every env var a `Settings` field could read, so a
   mocked test never inherits the developer's real `.env`. That is
   exactly right for the mocked suite and exactly wrong here, where
   reading the real `.env` is the point. `live_settings` below loads
   settings from the real environment instead.

**These tests are allowed to touch the network. Nothing else in `tests/`
is.** They never write: every assertion below is a read, a search, or a
connection check. A live test that mutated a tenant's data would be a test
nobody could safely run.
"""

from __future__ import annotations

import os

import pytest

#: Loaded once per session from the real environment, bypassing
#: `tests/conftest.py`'s stripping.
_REAL_ENV = dict(os.environ)


def require_env(*names: str) -> dict[str, str]:
    """Returns the named variables, or skips the test naming the missing ones.

    Reads the snapshot taken at import, before the ordinary suite's
    autouse fixture clears anything.
    """
    missing = [n for n in names if not _REAL_ENV.get(n)]
    if missing:
        pytest.skip(f"live test needs {', '.join(missing)} -- see docs/LIVE_INTEGRATION_TESTING.md")
    return {n: _REAL_ENV[n] for n in names}


@pytest.fixture(scope="session")
def live_settings():
    """`Settings` built from the real environment.

    Constructed directly rather than via `get_settings()` because that is
    `functools.cache`-backed and `tests/conftest.py` clears it between
    tests, which would hand back a stripped-environment instance.
    """
    from config.settings import Settings

    restore = dict(os.environ)
    os.environ.update(_REAL_ENV)
    try:
        return Settings()
    finally:
        os.environ.clear()
        os.environ.update(restore)


@pytest.fixture(autouse=True)
def _announce_live(request):
    """Makes it obvious in the log which external system a test touched,
    so a surprising bill or rate-limit has an audit trail."""
    import logging

    logging.getLogger(__name__).info("[live] running %s", request.node.nodeid)
    yield
