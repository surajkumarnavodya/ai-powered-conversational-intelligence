"""Cross-cutting regression: an executive briefing never presents an
inference, a suggestion or an attribution as observed fact, and cannot
reach a monitored database (Prompt 51).

Four independent properties, each failing loudly on its own:

1. **Type level.** `briefing.models.BriefingItem` refuses the
   claim-class/truth-level combinations that would misrepresent one kind
   of statement as another, and refuses a claim with no evidence at all.
2. **Pipeline level.** `briefing.claims.find_unsupported_claims` is the
   only gate `briefing.engine` passes candidates through, and the engine
   never emits a candidate that failed it.
3. **Text level.** Nothing any builder or the renderer produces asserts
   a cause -- the same property
   `tests/security/test_causal_never_asserts_causation.py` pins for
   `analytics.causal`, applied to this package's own output.
4. **Structural level.** No module in `briefing/` imports an identity
   model, a database handle, an LLM client or the SQL validator, so a
   briefing has no way to produce a row and the deterministic SQL/data
   controls are unreachable from here. Asserted by parsing the real
   source, not by relying on a mock that a future call site could slip
   past.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

import briefing
from agent.provenance import DataTruthLevel
from briefing.claims import (
    ClaimCandidate,
    data_quality_candidates,
    find_unsupported_claims,
    kpi_candidates,
    recommendation_candidates,
    schema_drift_candidates,
)
from briefing.engine import build_briefing
from briefing.inputs import (
    DataQualityFindingInput,
    KpiFindingInput,
    RecommendationInput,
    SchemaDriftFindingInput,
)
from briefing.models import (
    BriefingItem,
    BriefingPeriod,
    BriefingSection,
    ClaimClass,
    EvidenceLink,
)
from briefing.reports import render_markdown

AS_OF = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
BUDGET = 36 * 3600.0

PACKAGE_DIR = pathlib.Path(briefing.__file__).parent

#: Modules whose presence in `briefing/` would mean a briefing could
#: produce a row, reach a monitored database, or ask a model to write
#: one of its claims.
FORBIDDEN_IMPORT_PREFIXES = (
    "identity",
    "sqlalchemy",
    "agent.graph",
    "agent.nodes",
    "agent.llm_client",
    "agent.sql_validator",
    "db.connection",
    "db.execution",
    "db.schema_introspection",
    "ollama",
    "rag.llm",
)

#: Phrasings that would turn a contribution into a cause. The same list
#: shape `tests/security/test_causal_never_asserts_causation.py` uses.
CAUSAL_PHRASES = (
    "caused by",
    "the cause of",
    "because of",
    "causes the",
    "due to",
    "driven by",
    "responsible for",
)


def _link(**overrides) -> EvidenceLink:
    payload = {
        "source": "kpi_monitoring.findings",
        "reference_kind": "kpi_finding",
        "reference_id": "f1",
        "observed": {"current_value": 100.0},
        "versions": {"kpi_engine_version": "1.0.0"},
    }
    payload.update(overrides)
    return EvidenceLink(**payload)


def _kpi(**payload_overrides) -> KpiFindingInput:
    payload = {
        "metric_concept_key": "revenue",
        "policy_id": "p1",
        "period": "2026-06",
        "comparison": "previous_period",
        "baseline_label": "previous period 2026-05",
        "current_value": 640.0,
        "baseline_value": 1000.0,
        "change_absolute": -360.0,
        "change_percent": -36.0,
        "threshold_crossed": None,
        "detection_methods": ["rolling_zscore"],
        "direction": "down",
        "has_sufficient_evidence": True,
        "limitations": [],
        "dimension_columns": ["region"],
        "engine_version": "1.0.0",
        "confidence": 0.65,
        "contributors": [{"label": "EMEA", "contribution": -200.0, "contribution_percent": -55.6}],
    }
    payload.update(payload_overrides)
    return KpiFindingInput(
        finding_id="f1",
        kind="kpi_drop",
        title="revenue fell 36.0% in 2026-06",
        status="open",
        risk_score=78,
        risk_tier="high",
        confidence=0.65,
        version=2,
        last_seen_at=(AS_OF).isoformat(),
        payload=payload,
    )


def _drift() -> SchemaDriftFindingInput:
    return SchemaDriftFindingInput(
        finding_id="d1",
        kind="schema_drift_column_removed",
        title="Column removed: sales.amount",
        status="open",
        risk_score=60,
        risk_tier="medium",
        confidence=1.0,
        version=1,
        last_seen_at=AS_OF.isoformat(),
        payload={
            "table": "sales",
            "column": "amount",
            "change_kind": "column_removed",
            "impacted": [
                {
                    "node_id": "metric:revenue",
                    "type": "metric",
                    "label": "Net Revenue",
                    "truth_level": "confirmed_business_truth",
                    "concept_key": "revenue",
                }
            ],
        },
    )


def _recommendation() -> RecommendationInput:
    return RecommendationInput(
        record_id="r1",
        category="revenue",
        kind="action",
        status="generated",
        claim_text="Review EMEA pricing",
        action="Review EMEA pricing",
        confidence=0.7,
        evidence_count=2,
        engine_version="1.0.0",
        evidence_version="abc123",
        generated_at=AS_OF.isoformat(),
    )


def _data_quality() -> DataQualityFindingInput:
    return DataQualityFindingInput(
        dimension="completeness",
        severity="critical",
        table="sales",
        column="amount",
        claim_text="amount is null in 42.00% of rows",
        formula="null_rate = null_count / total_count",
        observed={"null_rate": 42.0},
        engine_version="1.0.0",
        observed_at=AS_OF.isoformat(),
    )


def _full_briefing():
    return build_briefing(
        database_id="default",
        period=BriefingPeriod.DAILY,
        as_of=AS_OF,
        period_label="2026-06-30",
        kpi_findings=[_kpi()],
        schema_drift_findings=[_drift()],
        recommendations=[_recommendation()],
        data_quality_findings=[_data_quality()],
    )


def _all_candidates() -> list[ClaimCandidate]:
    return [
        *kpi_candidates([_kpi()], as_of=AS_OF, budget_seconds=BUDGET, risk_min_score=40),
        *schema_drift_candidates([_drift()], as_of=AS_OF, budget_seconds=BUDGET, risk_min_score=40),
        *recommendation_candidates([_recommendation()], as_of=AS_OF, budget_seconds=BUDGET),
        *data_quality_candidates([_data_quality()], as_of=AS_OF, budget_seconds=BUDGET),
    ]


class TestTypeLevelGuarantees:
    @pytest.mark.parametrize(
        ("claim_class", "truth_level"),
        [
            (ClaimClass.ESTIMATE, DataTruthLevel.DATABASE_FACT),
            (ClaimClass.RECOMMENDATION, DataTruthLevel.DATABASE_FACT),
            (ClaimClass.OBSERVED_FACT, DataTruthLevel.AI_INFERENCE),
            (ClaimClass.ESTIMATE, DataTruthLevel.CONFIRMED_BUSINESS_TRUTH),
            (ClaimClass.OBSERVED_FACT, DataTruthLevel.CONFIRMED_BUSINESS_TRUTH),
            (ClaimClass.RECOMMENDATION, DataTruthLevel.CONFIRMED_BUSINESS_TRUTH),
        ],
    )
    def test_pydantic_refuses_a_mismatched_claim_class_and_truth_level(
        self, claim_class, truth_level
    ):
        with pytest.raises(ValidationError):
            BriefingItem(
                section=BriefingSection.RISKS,
                claim_class=claim_class,
                headline="x",
                evidence=(_link(),),
                truth_level=truth_level,
            )

    def test_no_briefing_claim_may_ever_be_confirmed_business_truth(self):
        """A briefing reports what other governed subsystems recorded.
        Confirming anything stays a separate, explicit human action
        elsewhere in this codebase (publishing a catalog entry, accepting
        a finding, resolving a recommendation)."""
        for claim_class in ClaimClass:
            with pytest.raises(ValidationError):
                BriefingItem(
                    section=BriefingSection.RISKS,
                    claim_class=claim_class,
                    headline="x",
                    evidence=(_link(),),
                    truth_level=DataTruthLevel.CONFIRMED_BUSINESS_TRUTH,
                )

    def test_a_claim_with_no_evidence_is_unrepresentable(self):
        with pytest.raises(ValidationError):
            BriefingItem(
                section=BriefingSection.RISKS,
                claim_class=ClaimClass.ESTIMATE,
                headline="x",
                evidence=(),
                truth_level=DataTruthLevel.AI_INFERENCE,
            )

    def test_an_evidence_link_with_no_version_is_unrepresentable(self):
        with pytest.raises(ValidationError):
            _link(versions={})

    def test_a_stale_claim_cannot_hide_why(self):
        with pytest.raises(ValidationError):
            BriefingItem(
                section=BriefingSection.RISKS,
                claim_class=ClaimClass.ESTIMATE,
                headline="x",
                evidence=(_link(),),
                truth_level=DataTruthLevel.AI_INFERENCE,
                is_stale=True,
                stale_reason="",
            )


class TestPipelineLevelGuarantees:
    def test_every_estimate_and_recommendation_in_a_real_briefing_is_an_inference(self):
        briefing = _full_briefing()
        assert briefing.items
        for item in briefing.items:
            if item.claim_class is ClaimClass.OBSERVED_FACT:
                assert item.truth_level is DataTruthLevel.DATABASE_FACT
            else:
                assert item.truth_level is DataTruthLevel.AI_INFERENCE

    def test_an_attribution_is_always_an_estimate_never_an_observed_fact(self):
        """A dimension's contribution is this platform's own inference,
        regardless of how arithmetic the underlying subtraction is."""
        briefing = _full_briefing()
        drivers = briefing.items_for(BriefingSection.DRIVERS)
        assert drivers
        assert all(item.claim_class is ClaimClass.ESTIMATE for item in drivers)

    def test_a_recommendation_is_never_an_observed_fact(self):
        briefing = _full_briefing()
        suggestions = briefing.items_for(BriefingSection.UNRESOLVED_RECOMMENDATIONS)
        assert suggestions
        assert all(item.claim_class is ClaimClass.RECOMMENDATION for item in suggestions)

    def test_every_builder_output_passes_the_gate(self):
        """The builders and the gate must agree: a realistic record is
        never silently withheld because a builder stated a number its own
        evidence lacks."""
        assert find_unsupported_claims(_all_candidates()) == ()

    def test_an_unsupported_candidate_never_reaches_items_but_is_reported(self):
        briefing = build_briefing(
            database_id="default",
            period=BriefingPeriod.DAILY,
            as_of=AS_OF,
            data_quality_findings=[
                _data_quality().model_copy(
                    update={
                        "claim_text": "null rate 42.00% exceeds the 5.00% threshold",
                        "observed": {"null_rate": 42.0},
                    }
                )
            ],
        )
        assert briefing.items == ()
        assert briefing.withheld_claims


class TestTextLevelGuarantees:
    @pytest.mark.parametrize("phrase", CAUSAL_PHRASES)
    def test_no_claim_text_asserts_a_cause(self, phrase):
        briefing = _full_briefing()
        for item in briefing.items:
            haystack = f"{item.headline} {item.detail}".lower()
            assert phrase not in haystack, f"{phrase!r} appears in {item.headline!r}"

    @pytest.mark.parametrize("phrase", CAUSAL_PHRASES)
    def test_the_rendered_report_never_asserts_a_cause(self, phrase):
        assert phrase not in render_markdown(_full_briefing()).lower()

    def test_a_driver_claim_states_its_own_caveat_inline(self):
        """A reader scanning one item must see the caveat without having
        to notice the section note above it."""
        for item in _full_briefing().items_for(BriefingSection.DRIVERS):
            assert "not a demonstrated cause" in item.detail

    def test_the_report_states_the_fact_estimate_recommendation_distinction(self):
        report = render_markdown(_full_briefing())
        assert "Observed facts are arithmetic over governed query results." in report
        assert "Nothing in this briefing is confirmed business truth" in report

    def test_every_rendered_claim_is_labelled_with_its_class_and_truth_level(self):
        report = render_markdown(_full_briefing())
        for label in ("observed fact", "estimate", "recommendation"):
            assert f"Claim type: **{label}**" in report


class TestStructuralGuarantees:
    def _imported_modules(self, path: pathlib.Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)
        return modules

    def test_the_package_has_modules_to_check(self):
        """Guards the two tests below from silently passing if the
        package were ever renamed or moved."""
        assert len(list(PACKAGE_DIR.glob("*.py"))) >= 6

    @pytest.mark.parametrize("path", sorted(PACKAGE_DIR.glob("*.py")), ids=lambda path: path.name)
    def test_no_module_imports_a_database_handle_an_orm_or_an_llm_client(self, path):
        for module in self._imported_modules(path):
            for forbidden in FORBIDDEN_IMPORT_PREFIXES:
                assert not (
                    module == forbidden or module.startswith(f"{forbidden}.")
                ), f"{path.name} imports {module!r}, which this package must never reach"

    @pytest.mark.parametrize("path", sorted(PACKAGE_DIR.glob("*.py")), ids=lambda path: path.name)
    def test_no_module_contains_a_sql_select_literal(self, path):
        """Belt and braces for the import check: a briefing must not be
        able to express a query even as a string."""
        source = path.read_text(encoding="utf-8").lower()
        assert "select " not in source or " from " not in source.split("select ", 1)[1][:200]

    def test_only_one_module_constructs_a_briefing_item(self):
        """`briefing.claims.finalize` is the single promotion point. A
        second construction site would be a second place the evidence
        gate could be skipped."""
        constructors = [
            path.name
            for path in PACKAGE_DIR.glob("*.py")
            if "BriefingItem(" in path.read_text(encoding="utf-8") and path.name != "models.py"
        ]
        assert constructors == ["claims.py"]
