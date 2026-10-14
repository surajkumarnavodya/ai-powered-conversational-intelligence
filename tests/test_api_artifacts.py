"""HTTP tests for `api/artifacts.py` -- the analysis artifact
workspace (see `docs/conversational-intelligence-ui.md`).

A real in-memory SQLite identity database (real tenants, real local
accounts, real JWT login, real `conversations`/`prompts`/`ai_outputs`
rows written through `identity.repositories.history.append_turn`),
mirroring `tests/test_api_bi_integration.py`'s own fixture precedent and
combining it with `tests/test_api_execute.py`'s mocked SQL execution
(`execute_readonly_sql`/`get_read_only_engine` patched at the
`api.artifacts` module they are looked up from).

Covers every scenario the brief names: create, open, refresh/reload,
navigate back to the source question, tenant isolation, stale result
handling, and deletion/retention.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import identity.db as identity_db_mod
import pytest
from fastapi.testclient import TestClient
from identity.bootstrap import seed_rbac
from identity.models import AnalysisArtifact, Base
from identity.repositories.history import append_turn, create_conversation
from identity.repositories.tenants import create_tenant
from identity.repositories.users import create_user
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

import api.artifacts as artifacts_mod
import api.auth as api_auth_mod
import api.identity_auth as identity_auth_mod
import api.identity_authz as identity_authz_mod
import api.main as api_main
import api.rate_limit as api_rate_limit_mod
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
    # No schema index exists in a test, so the live fingerprint is
    # unknown unless a case says otherwise -- which must draw no
    # conclusion, never a false "changed".
    monkeypatch.setattr(
        artifacts_mod, "get_last_schema_fingerprint", lambda name, settings=None: None
    )

    session = identity_db_mod.get_identity_session(_SETTINGS)
    seed_rbac(session)
    create_tenant(session, tenant_id="tenant-a", name="Tenant A")
    create_tenant(session, tenant_id="tenant-b", name="Tenant B")
    session.commit()
    session.close()

    api_rate_limit_mod._limiters.clear()
    identity_auth_mod._login_limiters.clear()
    identity_auth_mod._register_limiters.clear()
    yield engine


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


def _account(
    client: TestClient, email: str, role: str = "user", tenant_id: str = "tenant-a"
) -> tuple[dict, uuid.UUID]:
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        user = create_user(
            session, email=email, password=PASSWORD, tenant_id=tenant_id, role_name=role
        )
        session.commit()
        user_id = user.id
    finally:
        session.close()
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}, user_id


def _turn_metadata(
    *,
    with_result: bool = True,
    captured_at: datetime | None = None,
    sql: str = "SELECT region, revenue FROM sales",
    columns: list[str] | None = None,
    rows: list[list[object]] | None = None,
) -> dict:
    """The shape `api.chat_persistence` really writes into
    `ai_outputs.metadata` -- this is the artifact's whole source of truth,
    so the fixture reproduces it rather than inventing a shape."""
    metadata: dict = {
        "schema_version": 2,
        "status": "succeeded",
        "sources_used": ["sql"],
        "database": "default",
        "model": "llama3.1:8b",
        "sql": sql,
        "row_count": 3,
        "retry_count": 0,
        # Deliberately a superset of what the SQL below uses: schema
        # retrieval offers the top-k candidates, and a generated query
        # commonly uses one of them. An artifact must report the tables the
        # query references, not the ones retrieval consulted.
        "schema_tables": [
            {"table_name": "sales", "similarity_score": 0.9},
            {"table_name": "orders", "similarity_score": 0.7},
            {"table_name": "customers", "similarity_score": 0.6},
        ],
        "insight": "Australia leads at 2300000.0, ahead of Southwest at 1100000.0.",
        "insight_numbers_checked": 2,
        "result_snapshot": None,
    }
    if with_result:
        metadata["result_snapshot"] = {
            "columns": columns if columns is not None else ["region", "revenue"],
            "rows": (
                rows
                if rows is not None
                else [["Australia", 2300000.0], ["Southwest", 1100000.0], ["Canada", 900000.0]]
            ),
            "row_count": 3,
            "returned_rows": 3,
            "truncated": False,
            "column_types": {"region": "text", "revenue": "numeric"},
            "chart_recommendation": {"chart_type": "bar", "reason": "one category, one measure"},
            "normalized_sql": sql,
            "duration_ms": 42.0,
            "captured_at": (captured_at or datetime.now(UTC)).isoformat(),
        }
    return metadata


def _make_turn(
    user_id: uuid.UUID,
    *,
    tenant_id: str = "tenant-a",
    question: str = "Revenue by region?",
    **metadata_kwargs,
) -> tuple[str, str, str]:
    """Creates a real conversation + persisted turn. Returns
    `(conversation_id, prompt_id, ai_output_id)`."""
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        conversation = create_conversation(
            session, user_id=user_id, feature_type="text_to_sql", tenant_id=tenant_id
        )
        prompt, ai_output = append_turn(
            session,
            conversation=conversation,
            user_id=user_id,
            question=question,
            answer_text="Australia leads.",
            output_type="sql_query",
            output_metadata=_turn_metadata(**metadata_kwargs),
        )
        return str(conversation.id), str(prompt.id), str(ai_output.id)
    finally:
        session.close()


def _make_non_sql_turn(
    user_id: uuid.UUID,
    *,
    tenant_id: str = "tenant-a",
    question: str = "What does the leave policy say?",
    sources_used: list[str] | None = None,
    synthesized_answer: str | None = None,
    answer_text: str = "Employees get 22 days of annual leave.",
) -> tuple[str, str, str]:
    """A turn produced by a source other than SQL.

    The shape `api.chat_persistence` really writes for a documents/policy/
    web/media/attachment answer: no `sql`, no `database`, no `insight`,
    and no `result_snapshot` -- the answer lives in `AiOutput.content`
    and, for a multi-source run, in `synthesized_answer`.
    """
    session = identity_db_mod.get_identity_session(_SETTINGS)
    try:
        conversation = create_conversation(
            session, user_id=user_id, feature_type="text_to_sql", tenant_id=tenant_id
        )
        metadata: dict = {
            "schema_version": 2,
            "status": "succeeded",
            "sources_used": sources_used or ["documents"],
            "database": None,
            "model": "llama3.1:8b",
            "sql": None,
            "insight": None,
            "synthesized_answer": synthesized_answer,
            "result_snapshot": None,
        }
        prompt, ai_output = append_turn(
            session,
            conversation=conversation,
            user_id=user_id,
            question=question,
            answer_text=answer_text,
            output_type="sql_query",
            output_metadata=metadata,
        )
        return str(conversation.id), str(prompt.id), str(ai_output.id)
    finally:
        session.close()


def _save(client: TestClient, headers: dict, conversation_id: str, message_id: str, **extra):
    payload = {"conversation_id": conversation_id, "message_id": message_id}
    payload.update(extra)
    return client.post("/artifacts", json=payload, headers=headers)


class TestCreate:
    def test_saving_a_turn_returns_an_artifact_carrying_its_source_ids(self, client):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, prompt_id, message_id = _make_turn(user_id)

        response = _save(client, headers, conversation_id, message_id)
        assert response.status_code == 201, response.text
        body = response.json()
        # The four things the brief says an artifact must retain.
        assert body["conversation_id"] == conversation_id
        assert body["prompt_id"] == prompt_id
        assert body["message_id"] == message_id
        assert body["created_at"]

    def test_it_carries_the_source_and_lineage_metadata_from_the_turn(self, client):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _prompt_id, message_id = _make_turn(user_id)
        body = _save(client, headers, conversation_id, message_id).json()

        assert body["database"] == "default"
        assert body["model"] == "llama3.1:8b"
        assert body["sources_used"] == ["sql"]
        # The three tables retrieval consulted are not the one the query
        # uses -- see `api.artifacts._referenced_tables`.
        assert body["tables_referenced"] == ["sales"]
        assert body["sql"] == "SELECT region, revenue FROM sales"
        assert body["insight_numbers_checked"] == 2
        assert body["truth_level"] == "ai_inference"

    def test_it_reports_the_tables_the_query_uses_not_the_ones_consulted(self, client):
        # A real saved artifact showed 7 consulted tables for a 3-table
        # query, which is exactly the mislabel
        # `agent.sql_validator.summarize_sql` exists to prevent.
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(
            user_id, sql="SELECT o.id FROM orders o JOIN customers c ON c.id = o.customer_id"
        )
        body = _save(client, headers, conversation_id, message_id).json()
        assert body["tables_referenced"] == ["customers", "orders"]
        assert "sales" not in body["tables_referenced"]

    def test_unparseable_sql_reports_no_tables_rather_than_the_consulted_set(self, client):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id, sql="this is not sql at all ((")
        body = _save(client, headers, conversation_id, message_id).json()
        assert body["tables_referenced"] == []

    def test_the_title_defaults_to_the_source_question(self, client):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id, question="Revenue by region?")
        body = _save(client, headers, conversation_id, message_id).json()
        assert body["title"] == "Revenue by region?"
        assert body["question"] == "Revenue by region?"

    def test_an_explicit_title_is_kept(self, client):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        body = _save(client, headers, conversation_id, message_id, title="Q3 board slide").json()
        assert body["title"] == "Q3 board slide"

    def test_saving_the_same_turn_twice_returns_the_same_artifact(self, client):
        # Rather than accumulating a second, identical row every time the
        # button is pressed.
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        first = _save(client, headers, conversation_id, message_id).json()
        second = _save(client, headers, conversation_id, message_id).json()
        assert first["id"] == second["id"]
        assert len(client.get("/artifacts", headers=headers).json()["items"]) == 1

    def test_the_request_carries_no_result_so_a_client_cannot_invent_one(self, client):
        # The whole result comes from the already-persisted turn. An
        # unknown field is rejected outright (`extra="forbid"`), so there
        # is no way to smuggle rows in.
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        response = _save(client, headers, conversation_id, message_id, rows=[["Fabricated", 999]])
        assert response.status_code == 422

    def test_a_turn_with_no_confirmed_result_can_still_be_saved(self, client):
        # Perfectly ordinary: the question was answered but "Confirm and
        # Run" was never clicked. It is reported as having no result,
        # never as a stale one.
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id, with_result=False)
        body = _save(client, headers, conversation_id, message_id).json()
        assert body["freshness"]["state"] == "no_result_captured"
        assert body["origin_result"] is None
        assert body["row_count"] is None

    def test_an_unparseable_or_unknown_message_id_is_a_404(self, client):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, _m = _make_turn(user_id)
        assert _save(client, headers, conversation_id, "not-a-uuid").status_code == 404
        assert _save(client, headers, conversation_id, str(uuid.uuid4())).status_code == 404

    def test_a_message_from_another_conversation_is_a_404(self, client):
        headers, user_id = _account(client, "a@example.com")
        conversation_a, _p, _m = _make_turn(user_id)
        _conversation_b, _p2, message_b = _make_turn(user_id, question="Something else?")
        assert _save(client, headers, conversation_a, message_b).status_code == 404


class TestNonSqlSources:
    """An artifact saved from a source other than SQL.

    Five of this application's six answer sources (documents, policy,
    web, media search, attachments) never produce SQL, a database or an
    insight -- so everything an artifact reports about them has to come
    from somewhere else. Found by saving real answers from a running
    app, where each of these was wrong.
    """

    def test_a_document_answer_is_carried_into_the_artifact(self, client):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_non_sql_turn(user_id)
        body = _save(client, headers, conversation_id, message_id).json()
        # `insight` is the SQL pipeline's own narrative and stays None --
        # but the answer must still be there, or the artifact reopens empty.
        assert body["answer"] == "Employees get 22 days of annual leave."
        assert body["insight"] is None

    def test_a_multi_source_answer_keeps_every_source_not_just_sql(self, client):
        """The bug this pins: `AiOutput.content` prefers the SQL insight,
        so an attachments+sql artifact carried only the database half and
        silently dropped the half the question was actually about."""
        headers, user_id = _account(client, "a@example.com")
        blank = chr(10) + chr(10)
        synthesized = (
            "**Database**: 1 row(s) returned."
            + blank
            + "**Attached Files**: The region with the highest revenue is East, at 143500."
        )
        conversation_id, _p, message_id = _make_non_sql_turn(
            user_id,
            sources_used=["attachments", "sql"],
            synthesized_answer=synthesized,
            answer_text="The region with the highest revenue is Australia.",
        )
        body = _save(client, headers, conversation_id, message_id).json()
        assert body["answer"] == synthesized
        assert "East" in body["answer"]
        assert "143500" in body["answer"]

    def test_a_sql_only_answer_still_uses_the_turns_own_content(self, client):
        """A single-source SQL run has no synthesized answer at all
        (`synthesis_node` is a pass-through), so the fallback must hold."""
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        body = _save(client, headers, conversation_id, message_id).json()
        assert body["answer"] == "Australia leads."

    def test_no_database_recorded_is_not_reported_as_one_going_missing(self, client):
        """A non-SQL artifact never had a database, so `database_not_available`
        -- which reads as something having broken -- must not appear."""
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_non_sql_turn(user_id)
        body = _save(client, headers, conversation_id, message_id).json()
        reasons = body["freshness"]["reasons"]
        assert "database_not_available" not in reasons
        assert reasons == ["no_result_captured"]
        assert body["freshness"]["state"] == "no_result_captured"

    def test_it_still_cannot_be_refreshed_and_says_why(self, client):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_non_sql_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]
        detail = client.get(f"/artifacts/{artifact_id}", headers=headers).json()
        assert detail["is_refreshable"] is False
        assert detail["refresh_blocked_reason"]
        assert client.post(f"/artifacts/{artifact_id}/refresh", headers=headers).status_code == 409


class TestOpenAndNavigateBack:
    def test_opening_an_artifact_returns_the_result_without_re_executing_anything(
        self, client, monkeypatch
    ):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]

        def _explode(*args, **kwargs):
            raise AssertionError("opening an artifact must never execute SQL")

        monkeypatch.setattr(artifacts_mod, "execute_readonly_sql", _explode)

        body = client.get(f"/artifacts/{artifact_id}", headers=headers).json()
        assert body["origin_result"]["columns"] == ["region", "revenue"]
        assert body["origin_result"]["rows"][0] == ["Australia", 2300000.0]
        assert body["origin_result"]["chart_recommendation"]["chart_type"] == "bar"

    def test_the_prompt_id_is_the_anchor_a_reloaded_conversation_uses(self, client):
        # "Navigate back to the source question" reuses the existing
        # `turn-${entryId}` anchor because a reloaded conversation keys
        # each turn on its prompt row id
        # (`frontend/src/lib/history.ts::serverMessagesToQueryHistory`).
        headers, user_id = _account(client, "a@example.com")
        conversation_id, prompt_id, message_id = _make_turn(user_id)
        artifact = _save(client, headers, conversation_id, message_id).json()

        messages = client.get(f"/conversations/{conversation_id}/messages", headers=headers).json()
        user_rows = [row for row in messages["messages"] if row["role"] == "user"]
        assert artifact["prompt_id"] == prompt_id == user_rows[0]["id"]

    def test_the_source_question_and_insight_are_returned(self, client):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]
        body = client.get(f"/artifacts/{artifact_id}", headers=headers).json()
        assert body["question"] == "Revenue by region?"
        assert "Australia leads" in body["insight"]

    def test_a_list_row_carries_no_result_rows(self, client):
        # 200 artifacts must not ship 200 result sets.
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        _save(client, headers, conversation_id, message_id)
        item = client.get("/artifacts", headers=headers).json()["items"][0]
        assert "origin_result" not in item
        assert item["row_count"] == 3

    def test_the_list_can_be_scoped_to_one_conversation(self, client):
        headers, user_id = _account(client, "a@example.com")
        conversation_a, _p, message_a = _make_turn(user_id, question="First?")
        conversation_b, _p2, message_b = _make_turn(user_id, question="Second?")
        _save(client, headers, conversation_a, message_a)
        _save(client, headers, conversation_b, message_b)

        scoped = client.get(f"/artifacts?conversation_id={conversation_a}", headers=headers).json()
        assert [item["title"] for item in scoped["items"]] == ["First?"]

    def test_a_deleted_source_conversation_leaves_an_honest_artifact(self, client):
        # Soft-deleting the conversation does not cascade, so the artifact
        # survives -- and must say its source is gone rather than present
        # its old rows as current.
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]
        assert client.delete(f"/conversations/{conversation_id}", headers=headers).status_code in (
            200,
            204,
        )

        body = client.get(f"/artifacts/{artifact_id}", headers=headers).json()
        assert body["freshness"]["state"] == "source_missing"
        assert body["freshness"]["reasons"] == ["source_turn_deleted"]
        assert body["origin_result"] is None


class TestRefresh:
    def _refresh(self, client, headers, artifact_id):
        return client.post(f"/artifacts/{artifact_id}/refresh", headers=headers)

    def test_a_refresh_re_runs_the_recorded_sql_and_records_the_result(self, client, monkeypatch):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]

        monkeypatch.setattr(
            artifacts_mod,
            "execute_readonly_sql",
            lambda *a, **k: (["region", "revenue"], [["Australia", 2400000.0]]),
        )
        body = self._refresh(client, headers, artifact_id).json()
        assert body["last_refresh_outcome"] == "success"
        assert body["refreshed_result"]["rows"] == [["Australia", 2400000.0]]
        assert body["last_refresh_error"] is None

    def test_a_refresh_that_finds_different_numbers_says_so(self, client, monkeypatch):
        # Requirement 7, at its sharpest: the artifact is now standing on
        # a fresh result, *and* the one it was created from has moved.
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]

        monkeypatch.setattr(
            artifacts_mod,
            "execute_readonly_sql",
            lambda *a, **k: (["region", "revenue"], [["Australia", 9999.0]]),
        )
        body = self._refresh(client, headers, artifact_id).json()
        assert body["result_changed_since_creation"] is True
        assert "result_changed_on_refresh" in body["freshness"]["reasons"]
        # Both results are reported, with both timestamps -- the original
        # is superseded, not overwritten.
        assert body["origin_result"]["rows"][0] == ["Australia", 2300000.0]
        assert body["refreshed_result"]["captured_at"]

    def test_an_identical_refresh_reports_no_change(self, client, monkeypatch):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]

        monkeypatch.setattr(
            artifacts_mod,
            "execute_readonly_sql",
            lambda *a, **k: (
                ["region", "revenue"],
                [["Australia", 2300000.0], ["Southwest", 1100000.0], ["Canada", 900000.0]],
            ),
        )
        body = self._refresh(client, headers, artifact_id).json()
        assert body["result_changed_since_creation"] is False
        assert "result_changed_on_refresh" not in body["freshness"]["reasons"]

    def test_a_driver_shaped_result_is_stored_and_compared_as_plain_json(self, client, monkeypatch):
        """A `decimal.Decimal`/`datetime` cell must survive a refresh.

        Two real failures this pins, both found by refreshing a live SQL
        Server result and invisible to every other test in this class
        because they all mock clean Python floats:

        1. The JSONB write raised `TypeError: Object of type Decimal is
           not JSON serializable` inside SQLAlchemy's own JSON serializer,
           so the refresh was an HTTP 500 rather than a recorded result.
        2. `fingerprint_result` falls back to `str` for a type it does not
           know, so a `Decimal` hashed as the *quoted* string
           `"2300000.0"` while the origin snapshot -- already JSON, read
           back as a float -- hashed the bare number `2300000.0`. An
           unchanged result therefore reported "the numbers moved" on
           every single refresh, which is precisely the claim requirement
           7 exists to make trustworthy.

        Both are fixed by converting once, through
        `api.result_serialization.rows_to_json`, before anything stores or
        hashes the rows -- so the stored origin and a fresh execution are
        directly comparable.
        """
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]

        monkeypatch.setattr(
            artifacts_mod,
            "execute_readonly_sql",
            lambda *a, **k: (
                ["region", "revenue"],
                [
                    ["Australia", Decimal("2300000.0")],
                    ["Southwest", Decimal("1100000.0")],
                    ["Canada", Decimal("900000.0")],
                ],
            ),
        )
        response = self._refresh(client, headers, artifact_id)
        assert response.status_code == 200
        body = response.json()
        assert body["last_refresh_outcome"] == "success"
        # Stored as plain numbers, identical to what the origin snapshot
        # holds -- not as strings, and not as a failed write.
        assert body["refreshed_result"]["rows"] == [
            ["Australia", 2300000.0],
            ["Southwest", 1100000.0],
            ["Canada", 900000.0],
        ]
        # The same numbers came back, so the artifact must not claim they moved.
        assert body["result_changed_since_creation"] is False
        assert "result_changed_on_refresh" not in body["freshness"]["reasons"]

    def test_a_binary_cell_does_not_fail_a_refresh(self, client, monkeypatch):
        """The other half of `api.result_serialization.json_safe_cell`'s
        own reason for existing: raw `bytes` from a `varbinary` column are
        replaced with a short placeholder rather than crashing the write
        on a UTF-8 decode. A refresh is a results table like any other --
        it has no useful way to render a JPEG either."""
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]

        monkeypatch.setattr(
            artifacts_mod,
            "execute_readonly_sql",
            lambda *a, **k: (
                ["region", "photo"],
                # Real PNG magic bytes, written without escapes so the
                # literal cannot be mangled by an editing tool.
                [["Australia", bytes([137, 80, 78, 71, 13, 10, 26, 10])]],
            ),
        )
        response = self._refresh(client, headers, artifact_id)
        assert response.status_code == 200
        assert response.json()["refreshed_result"]["rows"] == [
            ["Australia", "<binary data, 8 bytes>"]
        ]

    def test_the_recorded_sql_is_revalidated_every_time(self, client, monkeypatch):
        # "SQL is untrusted output, always" -- a stored statement is
        # exactly as untrusted as a freshly generated one. A row whose SQL
        # was tampered with directly in the database must be refused, not
        # executed because it was valid when the artifact was created.
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]

        session = identity_db_mod.get_identity_session(_SETTINGS)
        try:
            row = session.get(AnalysisArtifact, uuid.UUID(artifact_id))
            row.source_sql = "DELETE FROM sales"
            session.commit()
        finally:
            session.close()

        def _explode(*args, **kwargs):
            raise AssertionError("a rejected statement must never be executed")

        monkeypatch.setattr(artifacts_mod, "execute_readonly_sql", _explode)
        body = self._refresh(client, headers, artifact_id).json()
        assert body["last_refresh_outcome"] == "failed"
        assert body["last_refresh_error"].startswith("Rejected:")

    def test_the_route_accepts_no_sql_from_the_caller(self, client):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]
        response = client.post(
            f"/artifacts/{artifact_id}/refresh",
            json={"sql": "SELECT * FROM salaries"},
            headers=headers,
        )
        assert response.status_code == 422

    def test_an_execution_failure_is_recorded_and_returned_as_a_200(self, client, monkeypatch):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]

        def _fail(*args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("connection reset"))

        monkeypatch.setattr(artifacts_mod, "execute_readonly_sql", _fail)
        response = self._refresh(client, headers, artifact_id)
        assert response.status_code == 200
        body = response.json()
        assert body["last_refresh_outcome"] == "failed"
        assert body["last_refresh_error"].startswith("Execution failed:")

    def test_a_failed_refresh_keeps_the_previous_successful_result(self, client, monkeypatch):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]

        monkeypatch.setattr(
            artifacts_mod,
            "execute_readonly_sql",
            lambda *a, **k: (["region", "revenue"], [["Australia", 2400000.0]]),
        )
        self._refresh(client, headers, artifact_id)

        def _fail(*args, **kwargs):
            raise TimeoutError("took too long")

        monkeypatch.setattr(artifacts_mod, "execute_readonly_sql", _fail)
        body = self._refresh(client, headers, artifact_id).json()
        assert body["last_refresh_outcome"] == "failed"
        # The good result is still there -- a transient failure must not
        # destroy what the artifact was legitimately standing on.
        assert body["refreshed_result"]["rows"] == [["Australia", 2400000.0]]
        assert "last_refresh_failed" in body["freshness"]["reasons"]

    def test_a_refresh_never_writes_back_into_the_source_turn(self, client, monkeypatch):
        # Otherwise reopening the saved conversation would show numbers
        # that were never the answer to that question at that time.
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]

        monkeypatch.setattr(
            artifacts_mod,
            "execute_readonly_sql",
            lambda *a, **k: (["region", "revenue"], [["Mars", 1.0]]),
        )
        self._refresh(client, headers, artifact_id)

        messages = client.get(f"/conversations/{conversation_id}/messages", headers=headers).json()
        assistant = [row for row in messages["messages"] if row["role"] == "assistant"][0]
        snapshot = assistant["metadata"]["result_snapshot"]
        assert snapshot["rows"][0] == ["Australia", 2300000.0]
        assert ["Mars", 1.0] not in snapshot["rows"]

    def test_an_artifact_with_no_recorded_sql_cannot_be_refreshed(self, client):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id, sql="")
        saved = _save(client, headers, conversation_id, message_id).json()
        assert saved["is_refreshable"] is False
        assert saved["refresh_blocked_reason"]
        response = self._refresh(client, headers, saved["id"])
        assert response.status_code == 409

    def test_a_database_no_longer_configured_for_the_tenant_blocks_a_refresh(
        self, client, monkeypatch
    ):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]

        monkeypatch.setattr(
            type(_SETTINGS), "databases_for_tenant", lambda self, tenant_id: [], raising=False
        )
        body = client.get(f"/artifacts/{artifact_id}", headers=headers).json()
        assert body["is_refreshable"] is False
        assert "database_not_available" in body["freshness"]["reasons"]
        assert self._refresh(client, headers, artifact_id).status_code == 409


class TestStaleResultHandling:
    def test_an_old_capture_reads_as_stale_on_reopen(self, client):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(
            user_id, captured_at=datetime.now(UTC) - timedelta(days=5)
        )
        body = _save(client, headers, conversation_id, message_id).json()
        assert body["freshness"]["state"] == "stale"
        assert "aged_past_stale_budget" in body["freshness"]["reasons"]
        assert body["freshness"]["result_as_of"]

    def test_a_schema_change_under_an_artifact_makes_it_stale(self, client, monkeypatch):
        headers, user_id = _account(client, "a@example.com")
        monkeypatch.setattr(
            artifacts_mod, "get_last_schema_fingerprint", lambda name, settings=None: "v1"
        )
        conversation_id, _p, message_id = _make_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]

        monkeypatch.setattr(
            artifacts_mod, "get_last_schema_fingerprint", lambda name, settings=None: "v2"
        )
        body = client.get(f"/artifacts/{artifact_id}", headers=headers).json()
        assert body["freshness"]["state"] == "stale"
        assert "schema_changed" in body["freshness"]["reasons"]

    def test_freshness_is_recomputed_on_every_read_not_stored(self, client, monkeypatch):
        # The same row reads differently as the clock moves, with nothing
        # written in between -- which is what makes requirement 6 hold.
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]
        assert (
            client.get(f"/artifacts/{artifact_id}", headers=headers).json()["freshness"]["state"]
            == "current"
        )

        session = identity_db_mod.get_identity_session(_SETTINGS)
        try:
            row = session.get(AnalysisArtifact, uuid.UUID(artifact_id))
            row.origin_captured_at = datetime.now(UTC) - timedelta(days=9)
            session.commit()
        finally:
            session.close()

        assert (
            client.get(f"/artifacts/{artifact_id}", headers=headers).json()["freshness"]["state"]
            == "stale"
        )

    def test_a_refresh_brings_a_stale_artifact_back_to_current(self, client, monkeypatch):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(
            user_id, captured_at=datetime.now(UTC) - timedelta(days=5)
        )
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]
        monkeypatch.setattr(
            artifacts_mod,
            "execute_readonly_sql",
            lambda *a, **k: (["region", "revenue"], [["Australia", 2300000.0]]),
        )
        body = client.post(f"/artifacts/{artifact_id}/refresh", headers=headers).json()
        assert body["freshness"]["state"] == "current"


class TestTenantAndOwnerIsolation:
    def test_another_tenants_artifact_is_a_404_not_a_403(self, client):
        a_headers, a_user = _account(client, "a@example.com", tenant_id="tenant-a")
        b_headers, _b_user = _account(client, "b@example.com", tenant_id="tenant-b")
        conversation_id, _p, message_id = _make_turn(a_user, tenant_id="tenant-a")
        artifact_id = _save(client, a_headers, conversation_id, message_id).json()["id"]

        assert client.get(f"/artifacts/{artifact_id}", headers=b_headers).status_code == 404
        assert (
            client.post(f"/artifacts/{artifact_id}/refresh", headers=b_headers).status_code == 404
        )
        assert client.delete(f"/artifacts/{artifact_id}", headers=b_headers).status_code == 404

    def test_another_user_in_the_same_tenant_cannot_open_it(self, client):
        # Stricter than every other tenant-scoped resource here, because
        # the thing it references -- a private conversation turn -- is
        # stricter.
        a_headers, a_user = _account(client, "a@example.com")
        b_headers, _b_user = _account(client, "colleague@example.com")
        conversation_id, _p, message_id = _make_turn(a_user)
        artifact_id = _save(client, a_headers, conversation_id, message_id).json()["id"]
        assert client.get(f"/artifacts/{artifact_id}", headers=b_headers).status_code == 404

    def test_an_admin_of_the_same_tenant_cannot_open_it_either(self, client):
        a_headers, a_user = _account(client, "a@example.com")
        admin_headers, _admin = _account(client, "admin@example.com", role="admin")
        conversation_id, _p, message_id = _make_turn(a_user)
        artifact_id = _save(client, a_headers, conversation_id, message_id).json()["id"]
        assert client.get(f"/artifacts/{artifact_id}", headers=admin_headers).status_code == 404

    def test_the_list_never_shows_another_users_artifacts(self, client):
        a_headers, a_user = _account(client, "a@example.com")
        b_headers, _b = _account(client, "colleague@example.com")
        conversation_id, _p, message_id = _make_turn(a_user)
        _save(client, a_headers, conversation_id, message_id)
        assert client.get("/artifacts", headers=b_headers).json()["items"] == []
        assert len(client.get("/artifacts", headers=a_headers).json()["items"]) == 1

    def test_a_cross_tenant_turn_cannot_be_saved_as_an_artifact(self, client):
        _a_headers, a_user = _account(client, "a@example.com", tenant_id="tenant-a")
        b_headers, _b_user = _account(client, "b@example.com", tenant_id="tenant-b")
        conversation_id, _p, message_id = _make_turn(a_user, tenant_id="tenant-a")
        assert _save(client, b_headers, conversation_id, message_id).status_code == 404

    def test_an_unauthenticated_caller_gets_nothing(self, client):
        assert client.get("/artifacts").status_code in (401, 403)
        assert client.post("/artifacts", json={}).status_code in (401, 403, 422)


class TestDeletionAndRetention:
    def test_deleting_an_artifact_removes_it_from_the_workspace(self, client):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]

        assert client.delete(f"/artifacts/{artifact_id}", headers=headers).status_code == 204
        assert client.get("/artifacts", headers=headers).json()["items"] == []
        assert client.get(f"/artifacts/{artifact_id}", headers=headers).status_code == 404

    def test_deletion_is_a_soft_delete_and_leaves_the_source_turn_alone(self, client):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]
        client.delete(f"/artifacts/{artifact_id}", headers=headers)

        session = identity_db_mod.get_identity_session(_SETTINGS)
        try:
            row = session.get(AnalysisArtifact, uuid.UUID(artifact_id))
            assert row is not None and row.deleted_at is not None
        finally:
            session.close()

        # Deleting a saved analysis must never delete the question and
        # answer it was made from.
        messages = client.get(f"/conversations/{conversation_id}/messages", headers=headers).json()
        assert len(messages["messages"]) == 2

    def test_deleting_twice_is_a_404_the_second_time(self, client):
        headers, user_id = _account(client, "a@example.com")
        conversation_id, _p, message_id = _make_turn(user_id)
        artifact_id = _save(client, headers, conversation_id, message_id).json()["id"]
        assert client.delete(f"/artifacts/{artifact_id}", headers=headers).status_code == 204
        assert client.delete(f"/artifacts/{artifact_id}", headers=headers).status_code == 404

    def test_a_deleted_artifact_frees_room_under_the_retention_cap(self, client, monkeypatch):
        # `Settings` is frozen and `artifact_max_per_user` is a field, so
        # the cap is lowered by swapping in a whole settings object (the
        # `Settings(**{**base.__dict__, **overrides})` convention
        # `tests/test_connection.py::_settings` establishes) rather than by
        # patching a class attribute, which instance data would win over.
        capped = Settings(**{**_SETTINGS.__dict__, "artifact_max_per_user": 1})
        monkeypatch.setattr(artifacts_mod, "get_settings", lambda: capped)
        headers, user_id = _account(client, "a@example.com")
        conversation_a, _p, message_a = _make_turn(user_id, question="First?")
        conversation_b, _p2, message_b = _make_turn(user_id, question="Second?")

        first = _save(client, headers, conversation_a, message_a).json()["id"]
        refused = _save(client, headers, conversation_b, message_b)
        assert refused.status_code == 409
        # Refused with a clear message rather than silently evicting the
        # artifact the user deliberately kept.
        assert "Delete one" in refused.json()["detail"]

        client.delete(f"/artifacts/{first}", headers=headers)
        assert _save(client, headers, conversation_b, message_b).status_code == 201

    def test_the_list_reports_the_cap_so_the_workspace_can_show_it(self, client):
        headers, _user_id = _account(client, "a@example.com")
        body = client.get("/artifacts", headers=headers).json()
        assert body["limit_per_user"] == _SETTINGS.artifact_max_per_user

    def test_the_schema_declares_the_intended_delete_behaviour(self):
        # Asserted on the declared schema rather than by deleting a row:
        # SQLite does not enforce foreign keys unless `PRAGMA
        # foreign_keys=ON` is set, so a behavioural test here would pass
        # vacuously and prove nothing about PostgreSQL, where it matters.
        #
        # The three rules, and why each differs: a deleted *conversation*
        # leaves nothing to open, so its artifacts go with it; a vanished
        # message row leaves the artifact as an honest record whose source
        # is gone (reported as `source_missing`, not silently dropped);
        # and a tenant must not be deletable out from under live rows.
        rules = {
            fk.column.table.name: fk.ondelete for fk in AnalysisArtifact.__table__.foreign_keys
        }
        assert rules["conversations"] == "CASCADE"
        assert rules["users"] == "CASCADE"
        assert rules["prompts"] == "SET NULL"
        assert rules["ai_outputs"] == "SET NULL"
        assert rules["tenants"] == "RESTRICT"

    def test_the_migration_declares_the_same_delete_behaviour_as_the_model(self):
        # The ORM builds the schema in tests; the migration builds it in
        # production. A disagreement between the two is exactly the bug
        # class `tests/test_identity_schema_invariants.py` was written for
        # after migration `a1f5c9e3b7d2` shipped without a server default.
        source = Path("identity/migrations/versions/p8t1u4v7w0x3_analysis_artifacts.py").read_text(
            encoding="utf-8"
        )
        assert (
            'ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE")'
            in source
        )
        assert 'ForeignKeyConstraint(["prompt_id"], ["prompts.id"], ondelete="SET NULL")' in source
        assert (
            'ForeignKeyConstraint(["ai_output_id"], ["ai_outputs.id"], ondelete="SET NULL")'
            in source
        )
        assert 'ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT")' in source
