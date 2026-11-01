"""`tests/conftest.py`'s env isolation must actually hold.

CLAUDE.md 7 states that every pytest test is fully mocked -- "no real
database, no Ollama". The Ollama half is enforced by
`conftest._block_real_ollama_calls`, which exists because the rule was
being broken silently. The database half had no equivalent and *was*
being broken silently (finding `BP-003`, 2026-10-28): `conftest` stripped
only env vars whose names are `Settings` field names, while
`config/settings.py` reads the open-ended `DB_CONNECTIONS` / `DB_<NAME>_*`
and `MCP_SERVERS` / `MCP_<NAME>_*` families straight from `os.environ`.

Measured before the fix, with the autouse fixture active: 24 `DB_*`
variables visible inside a test, two of them `DB_<NAME>_PASSWORD`, and
`get_settings().databases` returning this developer's four real SQL
Server connections. A test that swept `GET /health` then opened real
connections to them.

This file is the missing enforcement. It is deliberately small and
assertion-only: if it ever fails, the suite is reading the developer's
`.env` again.
"""

from __future__ import annotations

import os

import pytest

from config.settings import Settings, get_settings


def test_no_settings_field_env_var_is_visible():
    """The original guarantee, restated as a test rather than a docstring."""
    leaked = sorted(
        field_name.upper()
        for field_name in Settings.model_fields
        if field_name.upper() in os.environ
    )
    assert not leaked, f"env vars for declared Settings fields leaked into a test: {leaked}"


def test_no_dynamic_family_env_var_is_visible():
    """`BP-003`: the families that are not field names, and so were never
    stripped."""
    leaked = sorted(name for name in os.environ if name.upper().startswith(("DB_", "MCP_")))
    assert not leaked, (
        "the real .env's dynamic connection vars leaked into a test, so this suite's "
        f"results depend on the developer's machine: {leaked}"
    )


def test_a_test_sees_no_connectable_database():
    """The consequence that actually matters: no test may see a database
    it could actually reach.

    Asserted on connectability rather than on an empty tuple, because
    `Settings` always synthesises one implicit `"default"` connection from
    the scalar `DB_*` fields whether or not they are set. With the
    environment clean that entry has `db_type=""` and neither a host nor a
    connection string -- nothing to connect to -- which is the real
    guarantee. Before the fix this list held `hr`, `adventureworks`,
    `employee_management_database` and `test_db`, each with a live host or
    ODBC connection string.
    """
    reachable = [
        database.name
        for database in get_settings().databases
        if database.db_host or database.db_connection_string
    ]
    assert not reachable, f"a test can reach real configured databases: {reachable}"


def test_a_test_sees_no_configured_mcp_server():
    settings = get_settings()
    assert (
        settings.mcp_servers == ()
    ), f"a test can see real configured MCP servers: {settings.mcp_servers}"


def test_the_isolation_is_restored_after_a_test_sets_its_own(monkeypatch):
    """The per-test fixture must also undo what a test itself sets, not
    only what `.env` left behind -- otherwise one test's `monkeypatch.setenv`
    would leak into the next through the module-level strip's blind spot.
    """
    monkeypatch.setenv("DB_CONNECTIONS", "leaked")
    monkeypatch.setenv("DB_LEAKED_TYPE", "postgresql")
    assert os.environ["DB_CONNECTIONS"] == "leaked"
    # monkeypatch undoes this at teardown; the next test asserting
    # `test_no_dynamic_family_env_var_is_visible` is what proves it, and
    # the autouse fixture re-strips regardless.


def test_isolation_still_holds_after_the_previous_test(monkeypatch):
    """Ordering-dependent on purpose: this is the assertion that the
    previous test's env writes did not survive it."""
    assert "DB_CONNECTIONS" not in os.environ
    assert "DB_LEAKED_TYPE" not in os.environ


def test_a_test_can_still_opt_into_real_env_parsing(monkeypatch):
    """Non-vacuity guard for the fix: stripping must clear the slate, not
    make the dynamic families unusable. Tests that exercise
    `_parse_named_connections` set them explicitly, and that has to keep
    working."""
    monkeypatch.setenv("DB_CONNECTIONS", "probe")
    monkeypatch.setenv("DB_PROBE_TYPE", "postgresql")
    monkeypatch.setenv("DB_PROBE_HOST", "probe.example.invalid")
    monkeypatch.setenv("DB_PROBE_NAME", "probedb")
    monkeypatch.setenv("DB_PROBE_USER", "probe")
    monkeypatch.setenv("DB_PROBE_PASSWORD", "probe")
    get_settings.cache_clear()
    try:
        names = [database.name for database in get_settings().databases]
    finally:
        get_settings.cache_clear()
    assert names == ["probe"]


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """`get_settings` is `functools.cache`d, so a value built by an
    earlier test (or by an earlier assertion in this file) would be
    returned here regardless of the environment."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
