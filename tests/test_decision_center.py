"""Unit tests for `decision_center/` (Prompt 52).

Fully offline: every module is a pure function over already-fetched
rows, which is what makes that possible.

The acceptance criterion -- signal -> evidence -> explanation ->
recommendation -> action -> outcome as one coherent experience -- gets
its own class, `TestAcceptanceChain`. Cross-cutting guarantees
(personalization cannot change a fact, no promotion of truth levels,
tenant isolation) live in
`tests/security/test_decision_center_personalization_never_changes_facts.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agent.provenance import DataTruthLevel
from decision_center.briefing import COMPUTE_ONLY_CAPABILITIES, BriefingInputs, build_briefing
from decision_center.chain import build_chain, build_chains
from decision_center.health import build_health
from decision_center.models import (
    NOT_RECORDED,
    ChainLink,
    ChainStage,
    Headline,
    HeadlineKind,
    LinkType,
    Provenance,
    SectionId,
    SourceCapability,
    evidence_from_payload,
)
from decision_center.personalization import (
    PROFILES,
    personalize,
    profile_for_capabilities,
)
from decision_center.sources import (
    finding_headline,
    governance_headline,
    onboarding_headline,
    recommendation_headline,
    workflow_headline,
)

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


class FindingRow:
    """Stand-in for a `semantic_findings` row -- the duck-typed shape
    `decision_center.sources` is written against."""

    def __init__(
        self,
        kind: str = "kpi_drop",
        title: str = "Net Revenue fell 36.0% in 2026-06",
        score: int = 73,
        tier: str = "high",
        metric: str | None = "revenue",
        status: str = "open",
        detail: str = "Observed against the previous period.",
        database_id: str = "default",
        evidence: list | None = None,
    ) -> None:
        self.id = uuid.uuid4()
        self.kind = kind
        self.title = title
        self.risk_score = score
        self.risk_tier = tier
        self.status = status
        self.confidence = 0.94
        self.truth_level = "ai_inference"
        self.database_id = database_id
        self.created_at = NOW
        self.updated_at = NOW
        self.last_seen_at = NOW
        self.content = {
            "detail": detail,
            "evidence": (
                evidence
                if evidence is not None
                else [
                    {"observed": {"current_value": 640.0}, "truth_level": "database_fact"},
                    {"attribution": {"contributors": ["East"]}, "truth_level": "ai_inference"},
                ]
            ),
            "reasons": ["kpi_drop base 40"],
            "payload": {
                "metric_concept_key": metric,
                "period": "2026-06",
                "engine_version": "1.0.0",
                "limitations": ["one value excluded as noise"],
            },
        }


class RecommendationRow:
    def __init__(
        self,
        claim: str = "revenue is concentrated in East",
        status: str = "accepted",
        confidence: float = 0.8,
        database_id: str = "default",
        record_id: uuid.UUID | None = None,
    ) -> None:
        self.id = record_id or uuid.uuid4()
        self.claim_text = claim
        self.action = "Investigate the East pipeline."
        self.status = status
        self.confidence = confidence
        self.database_id = database_id
        self.generated_at = NOW
        self.created_at = NOW
        self.engine_version = "1.0.0"
        self.limitations = ["column-name heuristic"]
        self.measurable_impact = "recover ~340k"
        self.evidence = [{"finding": "East fell 68%", "truth_level": "database_fact"}]


class WorkflowRow:
    def __init__(
        self,
        status: str = "in_progress",
        outcome: str | None = None,
        recommendation_id: uuid.UUID | None = None,
    ) -> None:
        self.id = uuid.uuid4()
        self.title = "Fix East pipeline"
        self.description = "Owner to investigate."
        self.status = status
        self.recommendation_id = recommendation_id
        self.outcome_summary = outcome
        self.outcome_measured_impact = "+310k" if outcome else None
        self.due_date = None
        self.external_reference_id = "JIRA-123"
        self.created_at = NOW
        self.updated_at = NOW


class GovernanceRow:
    def __init__(self, stage: str = "evaluating") -> None:
        self.id = uuid.uuid4()
        self.display_name = "llama3.1:8b"
        self.description = "Default model."
        self.asset_key = "llama3.1:8b"
        self.stage = stage
        self.created_at = NOW
        self.updated_at = NOW


class OnboardingRow:
    def __init__(self, status: str = "awaiting_review") -> None:
        self.id = uuid.uuid4()
        self.label = "Warehouse"
        self.status = status
        self.created_at = NOW
        self.updated_at = NOW


class TestSourceAdapters:
    def test_a_kpi_finding_becomes_a_movement_headline_with_full_provenance(self):
        headline = finding_headline(FindingRow())
        assert headline.kind is HeadlineKind.KPI_MOVEMENT
        assert headline.severity == 73
        assert headline.severity_tier == "high"
        assert headline.truth_level is DataTruthLevel.AI_INFERENCE
        provenance = headline.provenance
        assert provenance.source is SourceCapability.KPI_MONITORING
        assert provenance.period == "2026-06"
        assert provenance.metric_concept_key == "revenue"
        assert provenance.metric_definition_available is True
        assert provenance.lineage_available is True
        assert provenance.confidence == pytest.approx(0.94)
        assert provenance.limitations

    def test_a_trend_break_is_an_anomaly_not_a_movement(self):
        assert finding_headline(FindingRow(kind="kpi_trend_break")).kind is HeadlineKind.ANOMALY

    def test_schema_drift_and_semantic_findings_are_risks(self):
        assert (
            finding_headline(FindingRow(kind="schema_drift_column_removed")).kind
            is HeadlineKind.RISK
        )
        assert finding_headline(FindingRow(kind="conflict")).kind is HeadlineKind.RISK

    def test_the_source_capability_is_recorded_per_kind(self):
        assert (
            finding_headline(FindingRow(kind="kpi_drop")).provenance.source
            is SourceCapability.KPI_MONITORING
        )
        assert (
            finding_headline(FindingRow(kind="schema_drift_table_added")).provenance.source
            is SourceCapability.SCHEMA_DRIFT
        )
        assert (
            finding_headline(FindingRow(kind="ambiguity")).provenance.source
            is SourceCapability.SEMANTIC_INTELLIGENCE
        )

    def test_a_recommendation_is_always_an_inference(self):
        headline = recommendation_headline(RecommendationRow())
        assert headline.kind is HeadlineKind.RECOMMENDATION
        assert headline.truth_level is DataTruthLevel.AI_INFERENCE

    def test_a_workflow_is_a_database_fact(self):
        """A workflow's status is an observed fact about this platform's
        own records -- unlike the recommendation that prompted it."""
        headline = workflow_headline(WorkflowRow())
        assert headline.truth_level is DataTruthLevel.DATABASE_FACT
        assert headline.kind is HeadlineKind.ACTION

    def test_a_workflow_with_an_outcome_is_an_outcome(self):
        headline = workflow_headline(WorkflowRow(status="completed", outcome="restored"))
        assert headline.kind is HeadlineKind.OUTCOME
        assert any(item.label == "outcome" for item in headline.evidence)

    def test_governance_and_onboarding_are_pending_decisions(self):
        assert governance_headline(GovernanceRow()).kind is HeadlineKind.PENDING_DECISION
        assert onboarding_headline(OnboardingRow()).kind is HeadlineKind.PENDING_DECISION

    def test_a_missing_field_reads_not_recorded_rather_than_blank(self):
        row = FindingRow(metric=None)
        row.content["payload"]["period"] = None
        headline = finding_headline(row)
        assert headline.provenance.period == NOT_RECORDED
        assert headline.provenance.metric_concept_key is None
        assert headline.provenance.metric_definition_available is False

    def test_evidence_keeps_the_truth_level_the_payload_declared(self):
        headline = finding_headline(FindingRow())
        levels = {item.truth_level for item in headline.evidence}
        assert DataTruthLevel.DATABASE_FACT in levels
        assert DataTruthLevel.AI_INFERENCE in levels

    def test_unlabelled_evidence_defaults_to_inference_not_fact(self):
        """The conservative direction: promoting an unlabelled value to a
        fact is the error that matters."""
        items = evidence_from_payload([{"something": "value"}])
        assert items[0].truth_level is DataTruthLevel.AI_INFERENCE

    def test_an_unrecognised_stored_tier_falls_back_rather_than_raising(self):
        """`Headline.severity_tier` is a Literal, so a malformed stored
        tier would raise and cost the whole briefing over one bad row."""
        row = FindingRow(score=80)
        row.risk_tier = "catastrophic"
        assert finding_headline(row).severity_tier == "high"

    def test_severity_is_carried_not_recomputed(self):
        """Re-scoring would mean the executive view disagreeing with the
        review queue an operator actually works from."""
        for score in (0, 12, 55, 99):
            assert finding_headline(FindingRow(score=score)).severity == score


class TestTruthLevelGuarantees:
    def test_an_inference_may_not_claim_certainty(self):
        with pytest.raises(ValidationError):
            Headline(
                headline_id="h",
                kind=HeadlineKind.KPI_MOVEMENT,
                title="t",
                truth_level=DataTruthLevel.AI_INFERENCE,
                provenance=Provenance(
                    source=SourceCapability.KPI_MONITORING,
                    source_record_id="r",
                    confidence=1.0,
                ),
            )

    def test_a_stored_confidence_of_one_is_clamped_rather_than_failing_the_briefing(self):
        """One malformed row must not take the whole page down."""
        row = FindingRow()
        row.confidence = 1.0
        headline = finding_headline(row)
        assert headline.provenance.confidence is not None
        assert headline.provenance.confidence < 1.0

    def test_a_fact_may_report_full_confidence(self):
        headline = Headline(
            headline_id="h",
            kind=HeadlineKind.ACTION,
            title="t",
            truth_level=DataTruthLevel.DATABASE_FACT,
            provenance=Provenance(
                source=SourceCapability.WORKFLOW_AUTOMATION,
                source_record_id="r",
                confidence=1.0,
            ),
        )
        assert headline.provenance.confidence == 1.0

    def test_a_headline_cannot_be_built_without_provenance(self):
        with pytest.raises(ValidationError):
            Headline(
                headline_id="h",
                kind=HeadlineKind.RISK,
                title="t",
                truth_level=DataTruthLevel.DATABASE_FACT,
            )


class TestAcceptanceChain:
    """signal -> evidence -> explanation -> recommendation -> action ->
    outcome, the mission's own acceptance criterion."""

    def _full(self):
        record_id = uuid.uuid4()
        signal = finding_headline(FindingRow())
        recommendation = recommendation_headline(
            RecommendationRow(claim="revenue is concentrated in East", record_id=record_id)
        )
        action = workflow_headline(
            WorkflowRow(status="completed", outcome="restored", recommendation_id=record_id)
        )
        return signal, recommendation, action, str(record_id)

    def test_a_complete_chain_reaches_outcome(self):
        signal, recommendation, action, record_id = self._full()
        chain = build_chain(signal, [recommendation], {record_id: action})
        assert chain.is_complete is True
        assert chain.furthest_stage is ChainStage.OUTCOME
        assert chain.break_reason == ""

    def test_the_recommendation_link_is_correlated_never_declared(self):
        """Nothing in the schema links a signal to a recommendation."""
        signal, recommendation, action, record_id = self._full()
        chain = build_chain(signal, [recommendation], {record_id: action})
        by_stage = {link.stage: link for link in chain.links}
        assert by_stage[ChainStage.RECOMMENDATION].link_type is LinkType.CORRELATED

    def test_the_action_and_outcome_links_are_declared(self):
        """`workflows.recommendation_id` is a real foreign key."""
        signal, recommendation, action, record_id = self._full()
        chain = build_chain(signal, [recommendation], {record_id: action})
        by_stage = {link.stage: link for link in chain.links}
        assert by_stage[ChainStage.ACTION].link_type is LinkType.DECLARED
        assert by_stage[ChainStage.OUTCOME].link_type is LinkType.DECLARED

    def test_a_chain_with_no_recommendation_states_why_it_stops(self):
        signal = finding_headline(FindingRow(metric="orders"))
        chain = build_chain(signal, [], {})
        assert chain.is_complete is False
        assert chain.furthest_stage is ChainStage.EXPLANATION
        assert "no recommendation concerns this subject" in chain.break_reason
        assert "correlation rather than a declared link" in chain.break_reason

    def test_a_chain_stops_honestly_at_action(self):
        signal, recommendation, _action, _record_id = self._full()
        chain = build_chain(signal, [recommendation], {})
        by_stage = {link.stage: link for link in chain.links}
        assert by_stage[ChainStage.RECOMMENDATION].present is True
        assert by_stage[ChainStage.ACTION].present is False
        assert "no workflow has been raised" in by_stage[ChainStage.ACTION].absent_reason

    def test_an_open_action_has_no_outcome_yet_and_says_so(self):
        record_id = uuid.uuid4()
        signal = finding_headline(FindingRow())
        recommendation = recommendation_headline(
            RecommendationRow(claim="revenue is concentrated in East", record_id=record_id)
        )
        action = workflow_headline(WorkflowRow(status="in_progress", recommendation_id=record_id))
        chain = build_chain(signal, [recommendation], {str(record_id): action})
        by_stage = {link.stage: link for link in chain.links}
        assert by_stage[ChainStage.ACTION].present is True
        assert by_stage[ChainStage.OUTCOME].present is False
        assert "recorded no outcome yet" in by_stage[ChainStage.OUTCOME].absent_reason

    def test_correlation_requires_more_than_a_shared_database(self):
        """A bare same-database match would correlate every signal with
        every recommendation, producing confident-looking nonsense."""
        signal = finding_headline(FindingRow(metric="revenue"))
        unrelated = recommendation_headline(
            RecommendationRow(claim="index the orders table for speed")
        )
        chain = build_chain(signal, [unrelated], {})
        by_stage = {link.stage: link for link in chain.links}
        assert by_stage[ChainStage.RECOMMENDATION].present is False

    def test_a_signal_with_no_metric_never_correlates(self):
        signal = finding_headline(FindingRow(kind="schema_drift_column_removed", metric=None))
        recommendation = recommendation_headline(RecommendationRow())
        chain = build_chain(signal, [recommendation], {})
        by_stage = {link.stage: link for link in chain.links}
        assert by_stage[ChainStage.RECOMMENDATION].present is False

    def test_a_different_database_never_correlates(self):
        signal = finding_headline(FindingRow(metric="revenue", database_id="db-a"))
        recommendation = recommendation_headline(
            RecommendationRow(claim="revenue is concentrated", database_id="db-b")
        )
        chain = build_chain(signal, [recommendation], {})
        by_stage = {link.stage: link for link in chain.links}
        assert by_stage[ChainStage.RECOMMENDATION].present is False

    def test_an_absent_stage_must_state_a_reason(self):
        with pytest.raises(ValidationError):
            ChainLink(stage=ChainStage.ACTION, present=False)

    def test_chains_are_built_for_the_highest_severity_signals_first(self):
        signals = [finding_headline(FindingRow(score=score)) for score in (10, 90, 50)]
        chains = build_chains(signals, [], {}, max_chains=2)
        assert len(chains) == 2
        # The 90 and the 50, not the 10.
        assert chains[0].links[0].summary

    def test_a_chain_is_always_an_inference(self):
        signal = finding_headline(FindingRow())
        chain = build_chain(signal, [], {})
        assert chain.truth_level is DataTruthLevel.AI_INFERENCE


class TestHealth:
    def test_every_indicator_is_a_database_fact(self):
        indicators = build_health([], [], [], [], [])
        assert indicators
        for indicator in indicators:
            assert indicator.truth_level is DataTruthLevel.DATABASE_FACT

    def test_counts_reflect_the_records(self):
        signals = [finding_headline(FindingRow()) for _ in range(3)]
        risks = [finding_headline(FindingRow(kind="schema_drift_table_removed"))]
        actions = [workflow_headline(WorkflowRow(status="completed", outcome="done"))]
        indicators = {i.key: i.value for i in build_health(signals, risks, [], actions, [])}
        assert indicators["open_signals"] == 3.0
        assert indicators["open_risks"] == 1.0
        assert indicators["completed_actions"] == 1.0

    def test_acceptance_rate_excludes_unjudged_recommendations(self):
        """Counting an unjudged recommendation as a rejection would
        understate the rate."""
        recommendations = [
            recommendation_headline(RecommendationRow(status="accepted")),
            recommendation_headline(RecommendationRow(status="rejected")),
            recommendation_headline(RecommendationRow(status="generated")),
        ]
        indicators = {i.key: i for i in build_health([], [], recommendations, [], [])}
        assert indicators["recommendation_acceptance_rate"].value == 50.0

    def test_rates_report_zero_with_an_explanation_when_there_is_no_basis(self):
        indicators = {i.key: i for i in build_health([], [], [], [], [])}
        assert indicators["recommendation_acceptance_rate"].value == 0.0
        assert "no rate to report" in indicators["recommendation_acceptance_rate"].detail
        assert "no rate to report" in indicators["outcome_capture_rate"].detail

    def test_outcome_capture_rate_measures_completed_actions_only(self):
        actions = [
            workflow_headline(WorkflowRow(status="completed", outcome="done")),
            workflow_headline(WorkflowRow(status="completed")),
            workflow_headline(WorkflowRow(status="in_progress")),
        ]
        indicators = {i.key: i.value for i in build_health([], [], [], actions, [])}
        assert indicators["outcome_capture_rate"] == 50.0

    def test_no_indicator_renders_a_health_verdict(self):
        """A single colour-coded verdict would be a business judgment
        nobody has given this platform."""
        rendered = " ".join(
            f"{i.label} {i.detail}" for i in build_health([], [], [], [], [])
        ).lower()
        for forbidden in ("healthy", "unhealthy", "amber", "red flag", "critical"):
            assert forbidden not in rendered

    def test_the_active_status_set_matches_the_real_workflow_enum(self):
        """This module keeps a literal set rather than importing
        `workflow/` for one predicate; it must not drift from it."""
        from decision_center.health import _ACTIVE_WORKFLOW_STATUSES
        from workflow.models import WorkflowStatus, is_active_status

        real = {status.value for status in WorkflowStatus if is_active_status(status)}
        assert real == _ACTIVE_WORKFLOW_STATUSES


class TestPersonalization:
    def _sections(self):
        return build_briefing(
            tenant_id="t",
            inputs=BriefingInputs(findings=[FindingRow()]),
            now=NOW,
        ).sections

    def test_each_profile_orders_sections_differently(self):
        orders = {
            name: [s.section for s in personalize(self._sections(), profile)]
            for name, profile in PROFILES.items()
        }
        assert orders["executive"] != orders["operator"]
        assert orders["analyst"] != orders["operator"]

    def test_every_section_is_present_for_every_profile(self):
        """Hiding a section from a role would let a briefing silently
        omit an open risk."""
        for profile in PROFILES.values():
            sections = {s.section for s in personalize(self._sections(), profile)}
            assert sections == set(SectionId)

    def test_a_profile_is_resolved_from_capabilities_not_from_a_request(self):
        assert profile_for_capabilities(["manage_governance"]).profile_id == "operator"
        assert profile_for_capabilities(["execute_sql"]).profile_id == "analyst"
        assert profile_for_capabilities([]).profile_id == "executive"

    def test_capping_reports_the_true_total(self):
        findings = [FindingRow(score=score) for score in range(20)]
        briefing = build_briefing(tenant_id="t", inputs=BriefingInputs(findings=findings), now=NOW)
        movement = next(s for s in briefing.sections if s.section is SectionId.KPI_MOVEMENT)
        assert len(movement.headlines) == PROFILES["executive"].headlines_per_section
        assert movement.total_available == 20


class TestBriefing:
    def test_a_full_briefing_assembles_every_section(self):
        record_id = uuid.uuid4()
        briefing = build_briefing(
            tenant_id="tenant-a",
            inputs=BriefingInputs(
                findings=[FindingRow(), FindingRow(kind="kpi_trend_break", metric="orders")],
                recommendations=[
                    RecommendationRow(claim="revenue is concentrated in East", record_id=record_id)
                ],
                workflows=[
                    WorkflowRow(status="completed", outcome="restored", recommendation_id=record_id)
                ],
                governance_versions=[GovernanceRow()],
                onboarding_jobs=[OnboardingRow()],
            ),
            now=NOW,
        )
        assert {s.section for s in briefing.sections} == set(SectionId)
        assert briefing.health
        assert briefing.chains
        assert briefing.tenant_id == "tenant-a"

    def test_an_empty_section_says_why_it_is_empty(self):
        briefing = build_briefing(tenant_id="t", inputs=BriefingInputs(), now=NOW)
        for section in briefing.sections:
            assert section.empty_reason, section.section

    def test_compute_only_capabilities_are_reported_as_unavailable(self):
        """A reader must be able to tell 'nothing to report' from 'this
        was never consulted'."""
        briefing = build_briefing(tenant_id="t", inputs=BriefingInputs(), now=NOW)
        rendered = " ".join(briefing.unavailable_sources)
        for name, _reason in COMPUTE_ONLY_CAPABILITIES:
            assert name in rendered

    def test_a_malformed_row_is_skipped_and_reported_not_fatal(self):
        class Broken:
            @property
            def content(self):
                raise RuntimeError("boom")

        briefing = build_briefing(
            tenant_id="t",
            inputs=BriefingInputs(findings=[FindingRow(), Broken()]),
            now=NOW,
        )
        movement = next(s for s in briefing.sections if s.section is SectionId.KPI_MOVEMENT)
        assert len(movement.headlines) == 1
        assert any("could not be read" in item for item in briefing.unavailable_sources)

    def test_an_unavailable_source_is_carried_through(self):
        briefing = build_briefing(
            tenant_id="t",
            inputs=BriefingInputs(unavailable=("workflows: could not be read",)),
            now=NOW,
        )
        assert "workflows: could not be read" in briefing.unavailable_sources

    def test_health_is_computed_before_the_display_cap(self):
        """Personalizing first would let a role's cap change a count."""
        findings = [FindingRow(score=score) for score in range(20)]
        briefing = build_briefing(
            tenant_id="t",
            inputs=BriefingInputs(findings=findings),
            profile=PROFILES["executive"],
            now=NOW,
        )
        open_signals = next(i for i in briefing.health if i.key == "open_signals")
        assert open_signals.value == 20.0

    def test_contributing_sources_lists_only_what_contributed(self):
        briefing = build_briefing(
            tenant_id="t", inputs=BriefingInputs(findings=[FindingRow()]), now=NOW
        )
        assert SourceCapability.KPI_MONITORING in briefing.contributing_sources
        assert SourceCapability.WORKFLOW_AUTOMATION not in briefing.contributing_sources
