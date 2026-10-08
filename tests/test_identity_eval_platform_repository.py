"""Tests for `identity/repositories/eval_platform.py` -- Prompt 46
(`46_AI_EVALUATION_BENCHMARKING_CONTRACT.md`). A real in-memory SQLite
engine (`Base.metadata.create_all`), never a mock of the ORM -- the
same convention every other `identity/repositories/` test file in this
codebase already uses.
"""

from __future__ import annotations

import uuid

import pytest
from identity.models import Base
from identity.repositories import eval_platform as repo
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    yield s
    s.close()


class TestCreateAndGetRun:
    def test_create_run_persists_every_field(self, session: Session):
        run = repo.create_run(
            session,
            tenant_id="tenant-a",
            source="component_benchmark",
            dataset_version="fp123",
            summary={"cases": {"semantic/x": True}},
            total_cases=62,
            total_passed=62,
            quality=1.0,
            governed_asset_type="model",
            governed_asset_key="llama3.1:8b",
            governed_asset_version=1,
            notes="first run",
        )
        assert run.id is not None
        assert run.status == "completed"
        assert run.tenant_id == "tenant-a"
        assert run.source == "component_benchmark"
        assert run.dataset_version == "fp123"
        assert run.quality == 1.0
        assert run.governed_asset_key == "llama3.1:8b"

    def test_get_run_by_id_returns_none_for_an_unknown_id(self, session: Session):
        assert repo.get_run_by_id(session, uuid.uuid4()) is None

    def test_get_run_by_id_returns_the_real_row_regardless_of_tenant(self, session: Session):
        """The repository itself never tenant-scopes a get-by-id lookup
        -- that's the API layer's own job (see this module's own
        docstring)."""
        run = repo.create_run(
            session,
            tenant_id="tenant-a",
            source="sql_benchmark",
            dataset_version="v1",
            summary={},
        )
        fetched = repo.get_run_by_id(session, run.id)
        assert fetched is not None
        assert fetched.tenant_id == "tenant-a"


class TestListRuns:
    def test_list_runs_is_tenant_scoped_at_the_query(self, session: Session):
        repo.create_run(
            session, tenant_id="tenant-a", source="sql_benchmark", dataset_version="v1", summary={}
        )
        repo.create_run(
            session, tenant_id="tenant-b", source="sql_benchmark", dataset_version="v1", summary={}
        )
        assert len(repo.list_runs(session, tenant_id="tenant-a")) == 1
        assert len(repo.list_runs(session, tenant_id="tenant-b")) == 1
        assert len(repo.list_runs(session, tenant_id="tenant-c")) == 0

    def test_list_runs_filters_by_source(self, session: Session):
        repo.create_run(
            session,
            tenant_id="tenant-a",
            source="sql_benchmark",
            dataset_version="v1",
            summary={},
        )
        repo.create_run(
            session,
            tenant_id="tenant-a",
            source="security_benchmark",
            dataset_version="v1",
            summary={},
        )
        sql_only = repo.list_runs(session, tenant_id="tenant-a", source="sql_benchmark")
        assert len(sql_only) == 1
        assert sql_only[0].source == "sql_benchmark"

    def test_list_runs_orders_most_recent_first(self, session: Session):
        """SQLite's `CURRENT_TIMESTAMP` default only has second
        resolution, so two inserts within the same real-world second
        would otherwise tie -- `created_at` is set explicitly here
        (never possible through the repository's own public API, which
        always lets the database stamp it) purely to make the ordering
        assertion deterministic in this test."""
        from datetime import UTC, datetime

        first = repo.create_run(
            session, tenant_id="tenant-a", source="sql_benchmark", dataset_version="v1", summary={}
        )
        second = repo.create_run(
            session, tenant_id="tenant-a", source="sql_benchmark", dataset_version="v2", summary={}
        )
        first.created_at = datetime(2020, 1, 1, tzinfo=UTC)
        second.created_at = datetime(2020, 1, 2, tzinfo=UTC)
        session.add_all([first, second])
        session.commit()

        runs = repo.list_runs(session, tenant_id="tenant-a")
        assert runs[0].id == second.id
        assert runs[1].id == first.id

    def test_list_runs_respects_limit(self, session: Session):
        for i in range(5):
            repo.create_run(
                session,
                tenant_id="tenant-a",
                source="sql_benchmark",
                dataset_version=f"v{i}",
                summary={},
            )
        assert len(repo.list_runs(session, tenant_id="tenant-a", limit=3)) == 3


class TestReleaseGateVerdict:
    def test_record_release_gate_verdict_persists_and_is_idempotent(self, session: Session):
        run = repo.create_run(
            session,
            tenant_id="tenant-a",
            source="component_benchmark",
            dataset_version="v1",
            summary={},
        )
        updated = repo.record_release_gate_verdict(
            session, run, verdict="block", reasons={"blocking_reasons": ["x"], "warnings": []}
        )
        assert updated.release_gate_verdict == "block"

        updated_again = repo.record_release_gate_verdict(
            session, run, verdict="pass", reasons={"blocking_reasons": [], "warnings": []}
        )
        assert updated_again.release_gate_verdict == "pass"


class TestCaseReviews:
    def test_create_and_list_case_reviews_for_a_run(self, session: Session):
        run = repo.create_run(
            session,
            tenant_id="tenant-a",
            source="sql_benchmark",
            dataset_version="v1",
            summary={},
        )
        repo.create_case_review(
            session,
            run_id=run.id,
            tenant_id="tenant-a",
            domain="sql",
            case_name="case_1",
            original_verdict=True,
            reviewed_verdict=False,
            comment="disagree",
        )
        reviews = repo.list_case_reviews_for_run(session, run.id, tenant_id="tenant-a")
        assert len(reviews) == 1
        assert reviews[0].reviewed_verdict is False

    def test_list_case_reviews_is_tenant_scoped_even_with_the_right_run_id(self, session: Session):
        run = repo.create_run(
            session,
            tenant_id="tenant-a",
            source="sql_benchmark",
            dataset_version="v1",
            summary={},
        )
        repo.create_case_review(
            session,
            run_id=run.id,
            tenant_id="tenant-a",
            domain="sql",
            case_name="case_1",
            original_verdict=True,
        )
        # A caller passing a different tenant_id (as the API layer would
        # if the actual caller belonged to another tenant) sees nothing,
        # even though run_id is correct.
        assert repo.list_case_reviews_for_run(session, run.id, tenant_id="tenant-b") == []

    def test_creating_a_review_never_mutates_the_parent_run(self, session: Session):
        run = repo.create_run(
            session,
            tenant_id="tenant-a",
            source="sql_benchmark",
            dataset_version="v1",
            summary={"cases": {"x": True}},
            total_passed=10,
        )
        repo.create_case_review(
            session,
            run_id=run.id,
            tenant_id="tenant-a",
            domain="sql",
            case_name="x",
            original_verdict=True,
            reviewed_verdict=False,
        )
        refreshed = repo.get_run_by_id(session, run.id)
        assert refreshed is not None
        assert refreshed.summary == {"cases": {"x": True}}
        assert refreshed.total_passed == 10
