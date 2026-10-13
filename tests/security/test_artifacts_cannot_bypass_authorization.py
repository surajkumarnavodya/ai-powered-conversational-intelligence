"""An artifact must never become a durable copy of data its owner is no
longer allowed to see -- the analysis artifact workspace, requirement 8.

The temptation this file exists to refuse: a saved artifact already holds
governed rows, so re-governing them on every read looks redundant. It is
not. Roles change. An analyst who saves an analysis over a restricted
column and later loses `view_restricted_columns` would otherwise keep a
permanent, unprivileged window onto exactly the values the restricted-
column gate exists to withhold -- and it would be a window that no part
of the live SQL pipeline could close, because nothing re-executes.

So the stored snapshot is re-governed against the **current** caller's
roles each time an artifact is opened, the recorded SQL is withheld when
it touches a restricted field, and a refresh is gated on the same
`Permission.EXECUTE_SQL` that `POST /execute` itself requires.

Lives in `tests/security/` rather than in `tests/test_api_artifacts.py`
because it is an adversarial-scenario regression that spans the artifact
routes, `governance/result_policy.py` and `config/sensitive_columns.py` --
the same reason that directory exists at all (see its own `README.md`).
"""

from __future__ import annotations

import ast
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import identity.db as identity_db_mod
import pytest
from fastapi.testclient import TestClient
from identity.bootstrap import seed_rbac
from identity.models import Base
from identity.repositories.history import append_turn, create_conversation
from identity.repositories.tenants import create_tenant
from identity.repositories.users import create_user
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import api.artifacts as artifacts_mod
import api.auth as api_auth_mod
import api.identity_auth as identity_auth_mod
import api.identity_authz as identity_authz_mod
import api.main as api_main
import api.rate_limit as api_rate_limit_mod
import config.sensitive_columns as sensitive_columns_mod
import governance.result_policy as result_policy_mod
from config.settings import Settings
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
    max_result_rows=100,
    chat_history_max_result_rows=50,
    enable_result_governance=True,
)

#: One restricted column, as `config/sensitive_columns.yaml` would
#: classify it. The shipped file is an empty template, so a test that
#: relied on it would pass vacuously -- see CLAUDE.md's governance-engine
#: note on exactly that gap.
RESTRICTED = {("employees", "salary"): "restricted"}


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
    monkeypatch.setattr(artifacts_mod, "get_settings", lambda: _SETTINGS)
    monkeypatch.setattr(artifacts_mod, "get_read_only_engine", lambda config: object())
    monkeypatch.setattr(
        artifacts_mod, "get_last_schema_fingerprint", lambda name, settings=None: None
    )
    # Patched at all three modules the classification is looked up from,
    # since each imports it by name.
    monkeypatch.setattr(sensitive_columns_mod, "load_sensitive_columns", lambda: RESTRICTED)
    monkeypatch.setattr(result_policy_mod, "load_sensitive_columns", lambda: RESTRICTED)
    monkeypatch.setattr(artifacts_mod, "load_sensitive_columns", lambda: RESTRICTED)

    session = identity_db_mod.get_identity_session(_SETTINGS)
    seed_rbac(session)
    create_tenant(session, tenant_id="tenant-a", name="Tenant A")
    session.commit()
    session.close()

    api_rate_limit_mod._limiters.clear()
    identity_auth_mod._login_limiters.clear()
    identity_auth_mod._register_limiters.clear()
    yield engine


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


def _account(client: TestClient, email: str, role: str) -> tuple[dict, uuid.UUID]:
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        user = create_user(
            session, email=email, password=PASSWORD, tenant_id="tenant-a", role_name=role
        )
        session.commit()
        user_id = user.id
    finally:
        session.close()
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}, user_id


def _restricted_turn(user_id: uuid.UUID) -> tuple[str, str]:
    """A persisted turn whose confirmed result includes a restricted
    column -- exactly what an analyst with `view_restricted_columns`
    would legitimately have produced and saved."""
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        conversation = create_conversation(
            session, user_id=user_id, feature_type="text_to_sql", tenant_id="tenant-a"
        )
        _prompt, ai_output = append_turn(
            session,
            conversation=conversation,
            user_id=user_id,
            question="What does each employee earn?",
            answer_text="Three employees.",
            output_type="sql_query",
            output_metadata={
                "schema_version": 2,
                "status": "succeeded",
                "sources_used": ["sql"],
                "database": "default",
                "sql": "SELECT name, salary FROM employees",
                "row_count": 2,
                "result_snapshot": {
                    "columns": ["name", "salary"],
                    "rows": [["Ada", 180000], ["Linus", 165000]],
                    "row_count": 2,
                    "returned_rows": 2,
                    "truncated": False,
                    "normalized_sql": "SELECT name, salary FROM employees",
                    "captured_at": datetime.now(UTC).isoformat(),
                },
            },
        )
        return str(conversation.id), str(ai_output.id)
    finally:
        session.close()


def _reassign_role(user_id: uuid.UUID, *, from_role: str, to_role: str) -> None:
    """Demotes an account, the way an administrator genuinely would."""
    from identity.repositories.users import assign_role, remove_role

    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        assign_role(session, user_id=user_id, role_name=to_role, assigned_by_user_id=None)
        remove_role(session, user_id=user_id, role_name=from_role)
        session.commit()
    finally:
        session.close()


class TestStoredRowsAreRegovernedOnEveryRead:
    def test_an_analyst_sees_the_restricted_values_they_are_entitled_to(self, client):
        headers, user_id = _account(client, "analyst@example.com", "analyst")
        conversation_id, message_id = _restricted_turn(user_id)
        body = client.post(
            "/artifacts",
            json={"conversation_id": conversation_id, "message_id": message_id},
            headers=headers,
        ).json()
        assert body["origin_result"]["rows"] == [["Ada", 180000], ["Linus", 165000]]

    def test_the_same_artifact_masks_those_values_once_the_role_is_revoked(self, client):
        # The whole reason this file exists. The rows were governed for a
        # caller who could see them; the caller changed.
        headers, user_id = _account(client, "analyst@example.com", "analyst")
        conversation_id, message_id = _restricted_turn(user_id)
        artifact_id = client.post(
            "/artifacts",
            json={"conversation_id": conversation_id, "message_id": message_id},
            headers=headers,
        ).json()["id"]

        _reassign_role(user_id, from_role="analyst", to_role="user")
        # A fresh login, since permissions are read per request from the
        # account rather than from the token.
        login = client.post(
            "/auth/login", json={"email": "analyst@example.com", "password": PASSWORD}
        )
        demoted = {"Authorization": f"Bearer {login.json()['access_token']}"}

        body = client.get(f"/artifacts/{artifact_id}", headers=demoted).json()
        values = [cell for row in body["origin_result"]["rows"] for cell in row]
        assert 180000 not in values
        assert 165000 not in values
        assert "[restricted]" in values
        # The non-restricted column is untouched: this masks a field, it
        # does not withhold the analysis.
        assert "Ada" in values

    def test_a_refreshed_result_is_governed_before_it_is_ever_stored(self, client, monkeypatch):
        # Belt and braces: the refresh path governs on the way in *and*
        # the read path governs on the way out, so neither alone is the
        # only thing standing between a restricted value and a caller.
        headers, user_id = _account(client, "plain@example.com", "user")
        conversation_id, message_id = _restricted_turn(user_id)
        artifact_id = client.post(
            "/artifacts",
            json={"conversation_id": conversation_id, "message_id": message_id},
            headers=headers,
        ).json()["id"]

        monkeypatch.setattr(
            artifacts_mod,
            "execute_readonly_sql",
            lambda *a, **k: (["name", "salary"], [["Ada", 180000]]),
        )
        client.post(f"/artifacts/{artifact_id}/refresh", headers=headers)

        session = identity_db_mod.get_identity_session(_SETTINGS)
        try:
            from identity.models import AnalysisArtifact

            row = session.get(AnalysisArtifact, uuid.UUID(artifact_id))
            stored = row.metadata_json["refreshed_snapshot"]["rows"]
        finally:
            session.close()
        assert stored == [["Ada", "[restricted]"]]
        assert 180000 not in [cell for r in stored for cell in r]


class TestSqlIsWithheldWhenItTouchesARestrictedField:
    def test_an_entitled_caller_is_shown_the_query(self, client):
        headers, user_id = _account(client, "analyst@example.com", "analyst")
        conversation_id, message_id = _restricted_turn(user_id)
        body = client.post(
            "/artifacts",
            json={"conversation_id": conversation_id, "message_id": message_id},
            headers=headers,
        ).json()
        assert body["sql"] == "SELECT name, salary FROM employees"
        assert body["sql_withheld_reason"] is None

    def test_a_caller_without_the_permission_is_not(self, client):
        headers, user_id = _account(client, "plain@example.com", "user")
        conversation_id, message_id = _restricted_turn(user_id)
        body = client.post(
            "/artifacts",
            json={"conversation_id": conversation_id, "message_id": message_id},
            headers=headers,
        ).json()
        assert body["sql"] is None
        assert body["sql_withheld_reason"]
        # The reason is a fixed sentence: it must not name the column, the
        # table, or the validator rule that matched.
        assert "salary" not in body["sql_withheld_reason"]
        assert "employees" not in body["sql_withheld_reason"]

    def test_an_ordinary_query_is_never_withheld_from_an_ordinary_caller(self, client):
        # Over-withholding is its own failure mode -- the same lesson the
        # governance engine's bulk-extraction rule learned when it started
        # refusing "How complete is our customer data?".
        headers, user_id = _account(client, "plain@example.com", "user")
        session = identity_db_mod.get_identity_session(_SETTINGS)
        try:
            conversation = create_conversation(
                session, user_id=user_id, feature_type="text_to_sql", tenant_id="tenant-a"
            )
            _prompt, ai_output = append_turn(
                session,
                conversation=conversation,
                user_id=user_id,
                question="Revenue by region?",
                answer_text="Australia leads.",
                output_type="sql_query",
                output_metadata={
                    "schema_version": 2,
                    "sources_used": ["sql"],
                    "database": "default",
                    "sql": "SELECT region, revenue FROM sales",
                    "result_snapshot": {
                        "columns": ["region", "revenue"],
                        "rows": [["Australia", 2300000.0]],
                        "row_count": 1,
                        "captured_at": datetime.now(UTC).isoformat(),
                    },
                },
            )
            ids = (str(conversation.id), str(ai_output.id))
        finally:
            session.close()

        body = client.post(
            "/artifacts",
            json={"conversation_id": ids[0], "message_id": ids[1]},
            headers=headers,
        ).json()
        assert body["sql"] == "SELECT region, revenue FROM sales"
        assert body["sql_withheld_reason"] is None


class TestRefreshIsGatedLikeAnExecution:
    def test_a_viewer_cannot_make_an_artifact_run_a_query(self, client):
        # `viewer` holds `ask` but not `execute_sql`. If a saved artifact
        # could refresh, it would be a way around the one permission that
        # separates reading answers from running queries.
        headers, user_id = _account(client, "viewer@example.com", "viewer")
        conversation_id, message_id = _restricted_turn(user_id)
        artifact_id = client.post(
            "/artifacts",
            json={"conversation_id": conversation_id, "message_id": message_id},
            headers=headers,
        ).json()["id"]

        response = client.post(f"/artifacts/{artifact_id}/refresh", headers=headers)
        assert response.status_code == 403

    def test_a_viewer_can_still_open_their_own_saved_analysis(self, client):
        headers, user_id = _account(client, "viewer@example.com", "viewer")
        conversation_id, message_id = _restricted_turn(user_id)
        artifact_id = client.post(
            "/artifacts",
            json={"conversation_id": conversation_id, "message_id": message_id},
            headers=headers,
        ).json()["id"]
        assert client.get(f"/artifacts/{artifact_id}", headers=headers).status_code == 200

    def test_a_refused_refresh_executes_nothing(self, client, monkeypatch):
        headers, user_id = _account(client, "viewer@example.com", "viewer")
        conversation_id, message_id = _restricted_turn(user_id)
        artifact_id = client.post(
            "/artifacts",
            json={"conversation_id": conversation_id, "message_id": message_id},
            headers=headers,
        ).json()["id"]

        def _explode(*args, **kwargs):
            raise AssertionError("a refused refresh must never reach execution")

        monkeypatch.setattr(artifacts_mod, "execute_readonly_sql", _explode)
        assert client.post(f"/artifacts/{artifact_id}/refresh", headers=headers).status_code == 403


class TestTheModuleHasNoSecondExecutionPath:
    def test_refreshing_goes_through_the_same_four_governed_steps_as_execute(self):
        # Reuse, not a parallel implementation: if a future edit inlines
        # its own execution here, this fails and says why.
        source = Path("api/artifacts.py").read_text(encoding="utf-8")
        for name in (
            "validate_sql(",
            "enforce_row_limit(",
            "qualify_table_schema(",
            "execute_readonly_sql(",
            "govern_rows_for_caller(",
        ):
            assert name in source, f"{name} missing from the refresh path"

    def test_nothing_in_the_artifacts_package_executes_sql_or_calls_a_model(self):
        # The pure package stays pure. Every database and model touch
        # lives in the route layer, where authorization has already run.
        #
        # Parsed as real imports rather than scanned as text, so a module
        # may *document* what it deliberately does not import -- which
        # `artifacts/__init__.py` does at length -- without failing.
        forbidden_prefixes = (
            "sqlalchemy",
            "identity",
            "db.execution",
            "db.connection",
            "db.schema_introspection",
            "agent.graph",
            "agent.nodes",
            "agent.llm_client",
            "ollama",
            "rag.llm",
        )
        for path in sorted(Path("artifacts").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
            for module in imported:
                for prefix in forbidden_prefixes:
                    assert not (
                        module == prefix or module.startswith(prefix + ".")
                    ), f"{path} imports {module}"
