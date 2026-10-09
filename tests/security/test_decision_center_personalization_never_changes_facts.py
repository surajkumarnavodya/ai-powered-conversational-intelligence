"""The Executive Decision Center presents governed facts. It may reorder
them per role; it may never change them (Prompt 52,
`52_EXECUTIVE_DECISION_CENTER_CONTRACT.md`).

Five cross-cutting guarantees, none belonging to a single module:

1. **Personalization cannot change a governed fact.** Two roles over
   identical input see byte-identical headline content, identical health
   numbers and identical chains -- only the section order and the
   display cap differ.
2. **It structurally cannot produce data.** No module in
   `decision_center/` imports the SQL validator, the execution path, a
   database connection, SQLAlchemy or an LLM client -- asserted by
   parsing the real source, the same approach
   `test_schema_drift_never_rewrites_catalog.py` and
   `test_kpi_monitoring_never_asserts_cause.py` use.
3. **It never promotes a truth level.** A record's own level is carried
   through unchanged; an inference can never be rendered as certain.
4. **A correlated link is never presented as a declared one** -- the
   difference between "these concern the same metric" and "this followed
   from that".
5. **Tenant isolation**, over a real identity database.

Fully offline apart from an in-memory SQLite identity database.
"""

from __future__ import annotations

import ast
import pathlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import identity.db as identity_db_mod
import pytest
from identity.bootstrap import seed_rbac
from identity.models import Base
from identity.repositories.semantic_intelligence import list_findings, upsert_findings
from identity.repositories.tenants import create_tenant
from semantic.intelligence.detect import Finding
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from tests.test_decision_center import (
    NOW,
    FindingRow,
    GovernanceRow,
    OnboardingRow,
    RecommendationRow,
    WorkflowRow,
)

from agent.provenance import DataTruthLevel
from config.settings import Settings
from decision_center.briefing import BriefingInputs, build_briefing
from decision_center.chain import build_chain
from decision_center.models import ChainStage, LinkType, SectionId
from decision_center.personalization import PROFILES, personalize
from decision_center.sources import finding_headline, recommendation_headline, workflow_headline

_PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "decision_center"

#: Modules whose purpose is producing or gating real data. The Decision
#: Center presents records it was handed; it must have no path of its own
#: to a row.
_FORBIDDEN_IMPORTS = (
    "agent.sql_validator",
    "agent.graph",
    "agent.nodes",
    "agent.llm_client",
    "db.execution",
    "db.connection",
    "db.schema_introspection",
    "sqlalchemy",
    "identity.models",
    "identity.db",
)

_FORBIDDEN_LLM_NAMES = ("ollama", "call_ollama", "get_ollama_client")


def _package_modules() -> list[pathlib.Path]:
    modules = sorted(path for path in _PACKAGE.glob("*.py"))
    assert len(modules) >= 6, f"expected the full decision_center package, found {modules}"
    return modules


def _rich_inputs() -> BriefingInputs:
    record_id = uuid.uuid4()
    return BriefingInputs(
        findings=[
            FindingRow(score=90, metric="revenue"),
            FindingRow(kind="kpi_trend_break", score=40, metric="orders"),
            FindingRow(kind="schema_drift_column_removed", score=60, metric=None),
        ],
        recommendations=[
            RecommendationRow(claim="revenue is concentrated in East", record_id=record_id)
        ],
        workflows=[
            WorkflowRow(status="completed", outcome="restored", recommendation_id=record_id)
        ],
        governance_versions=[GovernanceRow()],
        onboarding_jobs=[OnboardingRow()],
    )


class TestStructurallyCannotProduceData:
    def test_no_module_imports_the_sql_or_database_layer(self):
        offenders: list[str] = []
        for path in _package_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    for forbidden in _FORBIDDEN_IMPORTS:
                        if name == forbidden or name.startswith(forbidden + "."):
                            offenders.append(f"{path.name}: {name}")
        assert offenders == [], (
            "decision_center/ presents records it was handed; it must have no path of its "
            f"own to a row: {offenders}"
        )

    def test_no_module_calls_a_model(self):
        offenders: list[str] = []
        for path in _package_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import | ast.ImportFrom):
                    module = (
                        (node.module or "")
                        if isinstance(node, ast.ImportFrom)
                        else node.names[0].name
                    )
                    if module.split(".")[0] in _FORBIDDEN_LLM_NAMES:
                        offenders.append(f"{path.name}: {module}")
        assert offenders == [], offenders

    def test_no_module_contains_sql_text(self):
        offenders: list[str] = []
        for path in _package_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    upper = node.value.upper()
                    if "SELECT " in upper and " FROM " in upper:
                        offenders.append(f"{path.name}: {node.value[:60]!r}")
        assert offenders == [], offenders


class TestPersonalizationNeverChangesFacts:
    def _by_id(self, briefing):
        return {
            headline.headline_id: headline
            for section in briefing.sections
            for headline in section.headlines
        }

    def test_two_profiles_see_identical_headline_content(self):
        inputs = _rich_inputs()
        briefings = {
            name: build_briefing(tenant_id="t", inputs=inputs, profile=profile, now=NOW)
            for name, profile in PROFILES.items()
        }
        executive = self._by_id(briefings["executive"])
        operator = self._by_id(briefings["operator"])

        shared = set(executive) & set(operator)
        assert shared, "the profiles must share headlines for this test to mean anything"
        for headline_id in shared:
            assert executive[headline_id] == operator[headline_id], headline_id

    def test_health_numbers_are_identical_across_profiles(self):
        inputs = _rich_inputs()
        values = {
            name: build_briefing(tenant_id="t", inputs=inputs, profile=profile, now=NOW).health
            for name, profile in PROFILES.items()
        }
        assert values["executive"] == values["operator"] == values["analyst"]

    def test_chains_are_identical_across_profiles(self):
        inputs = _rich_inputs()
        chains = {
            name: build_briefing(tenant_id="t", inputs=inputs, profile=profile, now=NOW).chains
            for name, profile in PROFILES.items()
        }
        assert chains["executive"] == chains["operator"] == chains["analyst"]

    def test_only_the_order_and_the_cap_differ(self):
        inputs = _rich_inputs()
        executive = build_briefing(
            tenant_id="t", inputs=inputs, profile=PROFILES["executive"], now=NOW
        )
        operator = build_briefing(
            tenant_id="t", inputs=inputs, profile=PROFILES["operator"], now=NOW
        )
        assert [s.section for s in executive.sections] != [s.section for s in operator.sections]
        # ...but the same sections, with the same true totals.
        assert {s.section: s.total_available for s in executive.sections} == {
            s.section: s.total_available for s in operator.sections
        }

    def test_no_profile_can_hide_a_section(self):
        """A role shown a briefing that silently omits an open risk is
        the failure this guarantee exists to prevent."""
        inputs = _rich_inputs()
        for profile in PROFILES.values():
            briefing = build_briefing(tenant_id="t", inputs=inputs, profile=profile, now=NOW)
            assert {s.section for s in briefing.sections} == set(SectionId)

    def test_personalize_returns_the_same_headline_objects(self):
        """It re-sequences; it does not rebuild. A rebuilt headline is a
        place a value could change."""
        briefing = build_briefing(tenant_id="t", inputs=_rich_inputs(), now=NOW)
        original = {
            headline.headline_id: headline
            for section in briefing.sections
            for headline in section.headlines
        }
        reordered = personalize(briefing.sections, PROFILES["analyst"])
        for section in reordered:
            for headline in section.headlines:
                assert headline == original[headline.headline_id]


class TestNeverPromotesATruthLevel:
    def test_every_headline_keeps_its_records_own_level(self):
        assert finding_headline(FindingRow()).truth_level is DataTruthLevel.AI_INFERENCE
        assert (
            recommendation_headline(RecommendationRow()).truth_level is DataTruthLevel.AI_INFERENCE
        )
        assert workflow_headline(WorkflowRow()).truth_level is DataTruthLevel.DATABASE_FACT

    def test_no_inference_is_ever_rendered_as_certain(self):
        briefing = build_briefing(tenant_id="t", inputs=_rich_inputs(), now=NOW)
        for section in briefing.sections:
            for headline in section.headlines:
                if headline.truth_level is DataTruthLevel.AI_INFERENCE:
                    confidence = headline.provenance.confidence
                    assert confidence is None or confidence < 1.0

    def test_a_chain_is_an_inference_even_when_every_link_is_declared(self):
        record_id = uuid.uuid4()
        signal = finding_headline(FindingRow())
        recommendation = recommendation_headline(
            RecommendationRow(claim="revenue is concentrated in East", record_id=record_id)
        )
        action = workflow_headline(
            WorkflowRow(status="completed", outcome="restored", recommendation_id=record_id)
        )
        chain = build_chain(signal, [recommendation], {str(record_id): action})
        assert chain.is_complete is True
        assert chain.truth_level is DataTruthLevel.AI_INFERENCE


class TestCorrelatedIsNeverPresentedAsDeclared:
    def test_the_signal_to_recommendation_link_is_always_correlated(self):
        """Nothing in the schema links a signal to a recommendation. If a
        future change adds one, this test should be updated deliberately
        -- not silently satisfied by relabelling a guess."""
        record_id = uuid.uuid4()
        signal = finding_headline(FindingRow())
        recommendation = recommendation_headline(
            RecommendationRow(claim="revenue is concentrated in East", record_id=record_id)
        )
        chain = build_chain(signal, [recommendation], {})
        link = next(item for item in chain.links if item.stage is ChainStage.RECOMMENDATION)
        assert link.link_type is LinkType.CORRELATED
        assert link.link_type is not LinkType.DECLARED

    def test_a_break_at_the_recommendation_stage_explains_the_missing_link(self):
        signal = finding_headline(FindingRow(metric="nothing_matches_this"))
        chain = build_chain(signal, [], {})
        assert "correlation rather than a declared link" in chain.break_reason

    def test_the_two_link_types_are_never_merged(self):
        record_id = uuid.uuid4()
        signal = finding_headline(FindingRow())
        recommendation = recommendation_headline(
            RecommendationRow(claim="revenue is concentrated in East", record_id=record_id)
        )
        action = workflow_headline(
            WorkflowRow(status="completed", outcome="restored", recommendation_id=record_id)
        )
        chain = build_chain(signal, [recommendation], {str(record_id): action})
        by_stage = {link.stage: link.link_type for link in chain.links}
        assert by_stage[ChainStage.RECOMMENDATION] is LinkType.CORRELATED
        assert by_stage[ChainStage.ACTION] is LinkType.DECLARED


class TestNoSecretsOrDefinitionsLeak:
    def test_a_briefing_carries_no_credential_shaped_text(self):
        briefing = build_briefing(tenant_id="t", inputs=_rich_inputs(), now=NOW)
        rendered = str(briefing.model_dump()).lower()
        for forbidden in ("password", "secret", "token", "connection string", "pwd="):
            assert forbidden not in rendered

    def test_the_metric_definition_shape_cannot_carry_an_expression(self):
        """A metric's authoritative SQL is a governed definition, not
        briefing content -- omitting the field makes that structural."""
        from api.decision_center_schemas import MetricDefinitionOut

        assert "approved_expression" not in MetricDefinitionOut.model_fields


_SETTINGS = Settings(
    local_auth_enabled=True,
    auth_database_url="postgresql://placeholder/unused",
    jwt_secret_key="s" * 40,
)


@pytest.fixture
def identity_session(monkeypatch) -> Iterator[object]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(identity_db_mod, "get_identity_engine", lambda settings=None: engine)
    session = identity_db_mod.get_identity_session(_SETTINGS)
    seed_rbac(session)
    create_tenant(session, tenant_id="tenant-a", name="A")
    create_tenant(session, tenant_id="tenant-b", name="B")
    session.commit()
    yield session
    session.close()


class TestTenantIsolation:
    def test_one_tenants_signals_never_reach_anothers_briefing(self, identity_session):
        finding = Finding(
            kind="kpi_drop",
            key="k" * 64,
            title="Revenue fell",
            detail="d",
            subjects=(),
            evidence=(),
            confidence=0.9,
        )
        upsert_findings(
            identity_session,
            tenant_id="tenant-a",
            database_id="default",
            findings=[finding],
            now=datetime.now(UTC),
        )

        rows_a = list_findings(identity_session, tenant_id="tenant-a")
        rows_b = list_findings(identity_session, tenant_id="tenant-b")

        briefing_a = build_briefing(
            tenant_id="tenant-a", inputs=BriefingInputs(findings=rows_a), now=NOW
        )
        briefing_b = build_briefing(
            tenant_id="tenant-b", inputs=BriefingInputs(findings=rows_b), now=NOW
        )

        movement_a = next(s for s in briefing_a.sections if s.section is SectionId.KPI_MOVEMENT)
        movement_b = next(s for s in briefing_b.sections if s.section is SectionId.KPI_MOVEMENT)
        assert movement_a.total_available == 1
        assert movement_b.total_available == 0

    def test_the_briefing_records_the_tenant_it_was_built_for(self):
        briefing = build_briefing(tenant_id="tenant-a", inputs=_rich_inputs(), now=NOW)
        assert briefing.tenant_id == "tenant-a"
