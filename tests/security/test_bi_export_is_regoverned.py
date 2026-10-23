"""A BI connector's cached export must apply the protections no role waives.

`GET /bi/connectors/{id}/export` is the **fifth** surface in this codebase
that replays an already-governed result, after `api.artifacts._result_out`,
`api.shares._governed_result_snapshot`, `api.jobs._regovern_job_result` and
`api.chat_history._governed_metadata`. It was missed by the enumeration in
`docs/security/PENETRATION_TEST_REPORT.md`, which recorded four -- which is
why this file pins the invariant per surface rather than asserting it in
prose.

**What is *not* the exposure here, established by test rather than
assumed:** `BI_INTEGRATION_MANAGE` is admin-tier (`identity/rbac.py`), so
every BI action already requires an admin, and an admin holds
`VIEW_RESTRICTED_COLUMNS` anyway. A less-privileged colleague cannot export
at all, and an account demoted out of admin loses BI access entirely rather
than gaining an unmasked download. Ordinary restricted-column masking is
therefore moot on this path, and
`test_a_caller_without_bi_permission_cannot_export_at_all` pins that access
control so widening `BI_INTEGRATION_MANAGE` to a lower tier later cannot
silently open a hole this file would otherwise look like it covered.

**What *is* the exposure:** the path governed with `govern_rows_for_caller`
-- the configured-column layer only -- instead of
`govern_result_for_domain`. Two controls live in the latter that are
explicitly *not* role-waivable (CLAUDE.md 5.4), and both were absent from
the one endpoint whose entire purpose is bulk extraction:

1. **Never-disclosed categories.** A `CREDENTIAL`/`PAYMENT` column is
   withheld from every caller "including an admin -- no role unlocks
   them". On this path it exported in the clear.
2. **The per-domain export ceiling** (and small-group suppression), which
   apply to an authorized caller too: "permission to see a value is not
   permission to extract every one of them at once."

Harness mirrors `tests/test_api_bi_integration.py` (TestClient + in-memory
SQLite identity DB, mocked execution).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import identity.db as identity_db_mod
import pytest
from fastapi.testclient import TestClient
from identity.bootstrap import seed_rbac
from identity.models import Base
from identity.repositories.tenants import create_tenant
from identity.repositories.users import create_user
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import api.auth as api_auth_mod
import api.bi_integration as bi_integration_mod
import api.identity_auth as identity_auth_mod
import api.identity_authz as identity_authz_mod
import api.main as api_main
import api.rate_limit as api_rate_limit_mod
from bi_integration.export_cache import _reset_export_cache_for_tests
from config.settings import Settings
from governance.domains import BusinessDomain, policy_for
from security.secrets import SecretStr

PASSWORD = "correcthorse battery staple 1"

_SETTINGS = Settings(
    local_auth_enabled=True,
    auth_database_url="postgresql://placeholder/unused",
    jwt_secret_key="s" * 40,
    jwt_issuer="text-to-sql-agent",
    jwt_audience="text-to-sql-web",
    allow_public_registration=True,
    cookie_secure=False,
    password_min_length=8,
    login_rate_limit_per_minute=1000,
    register_rate_limit_per_hour=1000,
    api_action_rate_limit_per_minute=1000,
    enable_bi_integration=True,
    db_type="postgresql",
    db_host="db.example.com",
    db_port=5432,
    db_name="mydb",
    db_user="reader",
    db_password=SecretStr("secret"),
    db_connection_string=None,
    db_schema=None,
    db_odbc_driver="x",
    chroma_persist_dir=Path("/tmp/chroma"),
    chroma_collection_name="schema_ddl",
    embedding_model_name="all-MiniLM-L6-v2",
    query_timeout_seconds=15,
    bi_export_max_rows=1000,
    enable_result_governance=True,
)

#: A distinctive stand-in for a credential value, so "absent from the
#: export body" is a real assertion rather than a coincidence.
_SECRET_VALUE = "ZZTOPSECRETHASHZZ"
_HR_LIMIT = policy_for(BusinessDomain.HR).export_row_limit or 100


@pytest.fixture(autouse=True)
def _identity_test_db(monkeypatch) -> Iterator[object]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    monkeypatch.setattr(identity_db_mod, "get_identity_engine", lambda settings=None: engine)
    monkeypatch.setattr(api_auth_mod, "get_settings", lambda: _SETTINGS)
    monkeypatch.setattr(identity_auth_mod, "get_settings", lambda: _SETTINGS)
    monkeypatch.setattr(identity_authz_mod, "get_settings", lambda: _SETTINGS)
    monkeypatch.setattr(bi_integration_mod, "get_settings", lambda: _SETTINGS)
    monkeypatch.setattr(bi_integration_mod, "get_read_only_engine", lambda config: object())

    session = identity_db_mod.get_identity_session(_SETTINGS)
    seed_rbac(session)
    create_tenant(session, tenant_id="tenant-a", name="Tenant A")
    session.commit()
    session.close()

    # The export cache is a process-lifetime singleton; a leaked entry would
    # make one test's capture visible to the next.
    _reset_export_cache_for_tests()
    api_rate_limit_mod._limiters.clear()
    identity_auth_mod._login_limiters.clear()
    identity_auth_mod._register_limiters.clear()
    yield engine
    _reset_export_cache_for_tests()


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


def _account(client: TestClient, email: str, role: str) -> dict:
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        create_user(session, email=email, password=PASSWORD, tenant_id="tenant-a", role_name=role)
        session.commit()
    finally:
        session.close()
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _pull_connector(client: TestClient, headers: dict, sql: str) -> dict:
    response = client.post(
        "/bi/connectors",
        json={
            "target_type": "excel",
            "display_name": "Feed",
            "sql_text": sql,
            "push_config": {},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _mock_rows(monkeypatch, columns, rows):
    monkeypatch.setattr(
        bi_integration_mod,
        "execute_readonly_sql",
        lambda sql, timeout, max_rows, engine=None: (list(columns), [list(r) for r in rows]),
    )


def _refresh(client: TestClient, headers: dict, connector: dict) -> None:
    response = client.post(f"/bi/connectors/{connector['id']}/refresh", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["last_refresh_status"] == "success", response.text


def _export(client: TestClient, headers: dict, connector: dict, fmt: str = "csv"):
    return client.get(f"/bi/connectors/{connector['id']}/export?format={fmt}", headers=headers)


class TestBiExportAppliesTheProtectionsNoRoleWaives:
    def test_an_ordinary_export_is_unaffected(self, client, monkeypatch):
        """Over-blocking is its own failure mode: a connector over
        non-sensitive columns must still export its rows in full."""
        headers = _account(client, "admin@tenant-a.example.com", "admin")
        connector = _pull_connector(client, headers, "SELECT region, units FROM sales")
        _mock_rows(monkeypatch, ("region", "units"), (("East", 100), ("West", 250)))
        _refresh(client, headers, connector)

        response = _export(client, headers, connector)

        assert response.status_code == 200, response.text
        assert "East" in response.text
        assert "250" in response.text

    def test_a_credential_column_is_withheld_even_from_an_admin(self, client, monkeypatch):
        """CLAUDE.md 5.4: CREDENTIAL/PAYMENT are withheld from every caller
        "including an admin -- no role unlocks them". The BI path used a
        gate that does not implement never-disclosed categories at all, so
        the value exported in the clear."""
        headers = _account(client, "admin@tenant-a.example.com", "admin")
        connector = _pull_connector(
            client, headers, "SELECT user_name, password_hash FROM accounts"
        )
        _mock_rows(monkeypatch, ("user_name", "password_hash"), (("ada", _SECRET_VALUE),))
        _refresh(client, headers, connector)

        response = _export(client, headers, connector)

        assert response.status_code == 200, response.text
        assert (
            _SECRET_VALUE not in response.text
        ), "a never-disclosed credential value was exported in the clear"
        assert "[withheld]" in response.text
        # The non-sensitive column is untouched: this withholds a field, it
        # does not break the export.
        assert "ada" in response.text

    def test_the_per_domain_export_ceiling_applies_to_an_admin(self, client, monkeypatch):
        """ "Permission to see a value is not permission to extract every one
        of them at once" -- and this is the bulk-export endpoint."""
        headers = _account(client, "admin@tenant-a.example.com", "admin")
        connector = _pull_connector(
            client, headers, "SELECT employee_name, annual_salary FROM hr_employees"
        )
        over = _HR_LIMIT + 50
        _mock_rows(
            monkeypatch,
            ("employee_name", "annual_salary"),
            tuple((f"Emp{i}", 50_000 + i) for i in range(over)),
        )
        _refresh(client, headers, connector)

        response = _export(client, headers, connector)

        assert response.status_code == 200, response.text
        # Header row, then at most the ceiling's worth of data rows.
        data_rows = [line for line in response.text.strip().splitlines()[1:] if line]
        assert (
            len(data_rows) == _HR_LIMIT
        ), f"exported {len(data_rows)} rows, ceiling is {_HR_LIMIT}"

    def test_a_caller_without_bi_permission_cannot_export_at_all(self, client, monkeypatch):
        """Pins the access control this file's threat model depends on.
        `BI_INTEGRATION_MANAGE` is admin-tier today, which is why ordinary
        restricted-column masking is moot here -- if that is ever widened to
        a lower tier, this test fails and the masking question becomes live
        again rather than silently reopening."""
        admin = _account(client, "admin@tenant-a.example.com", "admin")
        connector = _pull_connector(client, admin, "SELECT name, salary FROM employees")
        _mock_rows(monkeypatch, ("name", "salary"), (("Ada", 187431),))
        _refresh(client, admin, connector)

        colleague = _account(client, "analyst@tenant-a.example.com", "analyst")
        response = _export(client, colleague, connector)

        assert response.status_code == 403, response.text

    def test_every_export_format_is_governed_not_just_csv(self, client, monkeypatch):
        """CSV, XLSX and JSON all serve the same cache; governing one and not
        the others would only move the leak."""
        headers = _account(client, "admin@tenant-a.example.com", "admin")
        connector = _pull_connector(
            client, headers, "SELECT user_name, password_hash FROM accounts"
        )
        _mock_rows(monkeypatch, ("user_name", "password_hash"), (("ada", _SECRET_VALUE),))
        _refresh(client, headers, connector)

        assert _SECRET_VALUE not in _export(client, headers, connector, "csv").text
        assert _SECRET_VALUE not in _export(client, headers, connector, "json").text
        # XLSX is a zip container; the value must not survive in its bytes.
        xlsx = _export(client, headers, connector, "xlsx")
        assert xlsx.status_code == 200
        assert _SECRET_VALUE.encode() not in xlsx.content

    def test_governance_can_be_rolled_back_by_its_own_switch(self, client, monkeypatch):
        """One switch for result governance, not a second one to find."""
        monkeypatch.setattr(
            bi_integration_mod,
            "get_settings",
            lambda: _SETTINGS.model_copy(update={"enable_result_governance": False}),
        )
        headers = _account(client, "admin@tenant-a.example.com", "admin")
        connector = _pull_connector(
            client, headers, "SELECT user_name, password_hash FROM accounts"
        )
        _mock_rows(monkeypatch, ("user_name", "password_hash"), (("ada", _SECRET_VALUE),))
        _refresh(client, headers, connector)

        assert _SECRET_VALUE in _export(client, headers, connector).text
