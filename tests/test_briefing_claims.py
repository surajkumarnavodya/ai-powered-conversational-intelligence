"""Unit tests for `briefing/claims.py` (Prompt 51).

Two concerns, matching the module's own two halves: the builders turn a
governed record into candidates, and the gate refuses the candidates
that are not supported by their own evidence.

Pure throughout -- no database, no clock, no LLM.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from agent.provenance import DataTruthLevel
from briefing.claims import (
    OPPORTUNITY_CATEGORIES,
    UNRESOLVED_RECOMMENDATION_STATUSES,
    ClaimCandidate,
    data_quality_candidates,
    finalize,
    find_unsupported_claims,
    kpi_candidates,
    recommendation_candidates,
    schema_drift_candidates,
    tier_for,
)
from briefing.inputs import (
    DataQualityFindingInput,
    KpiFindingInput,
    RecommendationInput,
    SchemaDriftFindingInput,
)
from briefing.models import BriefingItem, BriefingSection, ClaimClass, EvidenceLink

AS_OF = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
BUDGET = 36 * 3600.0


def _iso(hours_ago: float) -> str:
    return (AS_OF - timedelta(hours=hours_ago)).isoformat()


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


def _candidate(**overrides) -> ClaimCandidate:
    payload = {
        "section": BriefingSection.KPI_MOVEMENT,
        "claim_class": ClaimClass.OBSERVED_FACT,
        "headline": "Revenue was 100.00 this period",
        "detail": "",
        "truth_level": DataTruthLevel.DATABASE_FACT,
        "evidence": (_link(),),
    }
    payload.update(overrides)
    return ClaimCandidate(**payload)


def _kpi_finding(**payload_overrides) -> KpiFindingInput:
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
        "detection_methods": [],
        "direction": "down",
        "has_sufficient_evidence": False,
        "limitations": ["No dimension breakdown was configured."],
        "dimension_columns": [],
        "engine_version": "1.0.0",
    }
    payload.update(payload_overrides)
    return KpiFindingInput(
        finding_id="f1",
        kind="kpi_drop",
        title="Net Revenue fell 36.0% in 2026-06",
        status="open",
        risk_score=78,
        risk_tier="high",
        confidence=0.65,
        version=2,
        last_seen_at=_iso(2),
        payload=payload,
    )


# --- the gate -------------------------------------------------------------


class TestUnsupportedClaimDetection:
    def test_a_fully_supported_claim_is_not_reported(self):
        assert find_unsupported_claims([_candidate()]) == ()

    def test_a_claim_with_no_evidence_is_refused(self):
        problems = find_unsupported_claims([_candidate(evidence=())])
        assert [p.code for p in problems] == ["no_evidence"]

    def test_an_evidence_link_with_no_version_is_refused(self):
        """A link with no version cannot answer "which version said
        this", which is half of what evidence is for here. The model
        itself also refuses it, so this exercises the gate against a
        candidate built around that refusal."""
        with pytest.raises(ValidationError):
            _link(versions={})

        class _VersionlessLink:
            source = "x"
            reference_kind = "kpi_finding"
            reference_id = "f1"
            observed: dict[str, float] = {}
            versions: dict[str, str] = {}
            recorded_at = None
            truth_level = DataTruthLevel.DATABASE_FACT

        problems = find_unsupported_claims(
            [_candidate(evidence=(_VersionlessLink(),))]  # type: ignore[arg-type]
        )
        assert [p.code for p in problems] == ["missing_version"]

    def test_an_estimate_labelled_as_an_observed_fact_is_refused(self):
        problems = find_unsupported_claims(
            [
                _candidate(
                    claim_class=ClaimClass.ESTIMATE,
                    truth_level=DataTruthLevel.DATABASE_FACT,
                )
            ]
        )
        assert [p.code for p in problems] == ["truth_level_mismatch"]

    def test_an_observed_fact_labelled_as_an_inference_is_refused(self):
        problems = find_unsupported_claims(
            [
                _candidate(
                    claim_class=ClaimClass.OBSERVED_FACT,
                    truth_level=DataTruthLevel.AI_INFERENCE,
                )
            ]
        )
        assert [p.code for p in problems] == ["truth_level_mismatch"]

    def test_a_number_the_evidence_does_not_record_is_refused(self):
        problems = find_unsupported_claims([_candidate(headline="Revenue was 999.00 this period")])
        assert [p.code for p in problems] == ["ungrounded_number"]
        assert "999" in problems[0].detail

    def test_an_ungrounded_number_in_the_detail_is_caught_too(self):
        """The check covers the whole claim text, not only the headline --
        a fabricated statistic buried in the body is exactly as
        misleading."""
        problems = find_unsupported_claims([_candidate(detail="Driven by a 47% shift in the mix.")])
        assert [p.code for p in problems] == ["ungrounded_number"]

    def test_a_signed_evidence_value_may_be_stated_unsigned(self):
        """A movement's `change_percent` is stored signed (-36.0) while
        any readable sentence says "fell 36.0%". Rejecting that would
        reject correct text."""
        candidate = _candidate(
            headline="Revenue fell 36.0% this period",
            evidence=(_link(observed={"change_percent": -36.0}),),
        )
        assert find_unsupported_claims([candidate]) == ()

    def test_a_number_in_a_period_label_is_not_a_fabricated_statistic(self):
        candidate = _candidate(
            headline="Revenue moved in 2026-06",
            period_label="2026-06",
            evidence=(_link(observed={}),),
        )
        assert find_unsupported_claims([candidate]) == ()

    def test_a_number_copied_verbatim_from_the_source_is_allowed_via_grounding_context(self):
        """`grounding_context` is the documented allowance for a label the
        source itself wrote -- the same allowance
        `agent.insight.is_insight_grounded` makes for a number the user
        typed into the question."""
        candidate = _candidate(
            headline="Unresolved: concentration rose above 55%",
            grounding_context=("concentration rose above 55%",),
            evidence=(_link(observed={}),),
        )
        assert find_unsupported_claims([candidate]) == ()

    def test_a_percentage_rendered_from_a_stored_rate_is_grounded(self):
        """`data_quality.engine` records `null_rate = 1.0` and renders
        "is 100.0% null"; `analytics.root_cause` does the same for a
        share. A rate and the percentage rendered from it are the same
        observed fact, so refusing the percentage would withhold a real,
        deterministic observation over a unit convention -- which is
        exactly what happened to most data-quality claims before this
        allowance existed (found by running the briefing end to end
        against real data)."""
        candidate = _candidate(
            headline="demo.name is 100.0% null (1 of 1)",
            evidence=(_link(observed={"null_rate": 1.0, "null_count": 1.0, "total_count": 1.0}),),
        )
        assert find_unsupported_claims([candidate]) == ()

    def test_a_fractional_rate_still_grounds_its_own_plain_form(self):
        candidate = _candidate(
            headline="The null rate is 0.42 of rows",
            evidence=(_link(observed={"null_rate": 0.42}),),
        )
        assert find_unsupported_claims([candidate]) == ()

    def test_a_fabricated_percentage_is_still_refused(self):
        """The allowance is a unit identity, not a loosening: 91% is not
        derivable from a rate of 0.42 in either unit."""
        problems = find_unsupported_claims(
            [
                _candidate(
                    headline="Coverage collapsed by 91.0%",
                    evidence=(_link(observed={"null_rate": 0.42}),),
                )
            ]
        )
        assert [p.code for p in problems] == ["ungrounded_number"]

    def test_a_plain_number_is_not_grounded_by_a_scaled_observation(self):
        """The scaling is confined to percent-typed numbers. A bare 100
        must still match a plain observed value, or the check would
        accept almost any integer."""
        problems = find_unsupported_claims(
            [
                _candidate(
                    headline="There were 100 affected rows",
                    evidence=(_link(observed={"rate": 1.0}),),
                )
            ]
        )
        assert [p.code for p in problems] == ["ungrounded_number"]

    def test_every_problem_is_reported_not_just_the_first_candidate(self):
        problems = find_unsupported_claims(
            [
                _candidate(evidence=()),
                _candidate(headline="Revenue was 999.00 this period"),
                _candidate(),
            ]
        )
        assert sorted(p.code for p in problems) == ["no_evidence", "ungrounded_number"]


class TestFinalize:
    def test_it_produces_a_real_briefing_item(self):
        item = finalize(_candidate())
        assert isinstance(item, BriefingItem)
        assert item.claim_class is ClaimClass.OBSERVED_FACT
        assert item.truth_level is DataTruthLevel.DATABASE_FACT

    def test_it_clamps_a_score_into_the_model_s_own_range(self):
        assert finalize(_candidate(risk_score=5000)).risk_score == 100
        assert finalize(_candidate(risk_score=-5)).risk_score == 0


class TestTierFor:
    @pytest.mark.parametrize(
        ("score", "tier"), [(0, "low"), (39, "low"), (40, "medium"), (69, "medium"), (70, "high")]
    )
    def test_boundaries_match_the_shared_risk_thresholds(self, score, tier):
        assert tier_for(score) == tier


# --- the builders ---------------------------------------------------------


class TestKpiCandidates:
    def test_a_movement_is_an_observed_fact_with_its_versions_linked(self):
        candidates = kpi_candidates(
            [_kpi_finding()], as_of=AS_OF, budget_seconds=BUDGET, risk_min_score=40
        )
        movement = next(c for c in candidates if c.section is BriefingSection.KPI_MOVEMENT)
        assert movement.claim_class is ClaimClass.OBSERVED_FACT
        assert movement.truth_level is DataTruthLevel.DATABASE_FACT
        link = movement.evidence[0]
        assert link.reference_kind == "kpi_finding"
        assert link.versions["kpi_engine_version"] == "1.0.0"
        assert link.versions["finding_version"] == "2"
        assert link.observed["current_value"] == 640.0

    def test_every_candidate_a_real_finding_produces_passes_the_gate(self):
        """The builders and the gate must agree: a realistic record must
        not be silently withheld because a builder stated a number its
        own evidence lacks."""
        candidates = kpi_candidates(
            [
                _kpi_finding(
                    detection_methods=["rolling_zscore"],
                    has_sufficient_evidence=True,
                    confidence=0.65,
                    dimension_columns=["region"],
                    contributors=[
                        {"label": "EMEA", "contribution": -200.0, "contribution_percent": -55.6}
                    ],
                )
            ],
            as_of=AS_OF,
            budget_seconds=BUDGET,
            risk_min_score=40,
        )
        assert find_unsupported_claims(candidates) == ()
        sections = {c.section for c in candidates}
        assert sections == {
            BriefingSection.KPI_MOVEMENT,
            BriefingSection.ANOMALIES,
            BriefingSection.DRIVERS,
            BriefingSection.RISKS,
        }

    def test_an_anomaly_claim_is_an_estimate_not_a_fact(self):
        candidates = kpi_candidates(
            [_kpi_finding(detection_methods=["iqr"])],
            as_of=AS_OF,
            budget_seconds=BUDGET,
            risk_min_score=40,
        )
        anomaly = next(c for c in candidates if c.section is BriefingSection.ANOMALIES)
        assert anomaly.claim_class is ClaimClass.ESTIMATE
        assert anomaly.truth_level is DataTruthLevel.AI_INFERENCE

    def test_no_anomaly_claim_without_a_method_or_a_threshold(self):
        candidates = kpi_candidates(
            [_kpi_finding()], as_of=AS_OF, budget_seconds=BUDGET, risk_min_score=40
        )
        assert not any(c.section is BriefingSection.ANOMALIES for c in candidates)

    def test_no_driver_claim_without_sufficient_evidence(self):
        """`kpi_monitoring.models.ChangeEvidence` guarantees contributors
        are absent without evidence; this pins that the briefing never
        invents one either."""
        candidates = kpi_candidates(
            [_kpi_finding(has_sufficient_evidence=False)],
            as_of=AS_OF,
            budget_seconds=BUDGET,
            risk_min_score=40,
        )
        assert not any(c.section is BriefingSection.DRIVERS for c in candidates)

    def test_a_driver_claim_never_asserts_a_cause(self):
        candidates = kpi_candidates(
            [
                _kpi_finding(
                    has_sufficient_evidence=True,
                    contributors=[
                        {"label": "EMEA", "contribution": -200.0, "contribution_percent": -55.6}
                    ],
                )
            ],
            as_of=AS_OF,
            budget_seconds=BUDGET,
            risk_min_score=40,
        )
        driver = next(c for c in candidates if c.section is BriefingSection.DRIVERS)
        assert "not a demonstrated cause" in driver.detail
        assert driver.claim_class is ClaimClass.ESTIMATE

    def test_a_bigger_driver_outranks_a_smaller_one_and_neither_outranks_the_movement(self):
        candidates = kpi_candidates(
            [
                _kpi_finding(
                    has_sufficient_evidence=True,
                    contributors=[
                        {"label": "EMEA", "contribution": -200.0, "contribution_percent": -55.6},
                        {"label": "APAC", "contribution": -100.0, "contribution_percent": -27.8},
                    ],
                )
            ],
            as_of=AS_OF,
            budget_seconds=BUDGET,
            risk_min_score=40,
        )
        drivers = [c for c in candidates if c.section is BriefingSection.DRIVERS]
        emea = next(c for c in drivers if "EMEA" in c.headline)
        apac = next(c for c in drivers if "APAC" in c.headline)
        movement = next(c for c in candidates if c.section is BriefingSection.KPI_MOVEMENT)
        assert emea.risk_score > apac.risk_score
        assert emea.risk_score < movement.risk_score

    def test_an_upward_movement_is_an_opportunity_and_a_downward_one_a_risk(self):
        down = kpi_candidates(
            [_kpi_finding(direction="down")], as_of=AS_OF, budget_seconds=BUDGET, risk_min_score=40
        )
        up = kpi_candidates(
            [_kpi_finding(direction="up", change_absolute=360.0, change_percent=36.0)],
            as_of=AS_OF,
            budget_seconds=BUDGET,
            risk_min_score=40,
        )
        assert any(c.section is BriefingSection.RISKS for c in down)
        assert any(c.section is BriefingSection.OPPORTUNITIES for c in up)

    def test_the_risk_floor_suppresses_the_risk_reading_but_never_the_movement(self):
        """The floor governs whether a *second*, inferred risk claim is
        made -- the observed movement itself is always reported."""
        low = _kpi_finding()
        low = low.model_copy(update={"risk_score": 10, "risk_tier": "low"})
        candidates = kpi_candidates([low], as_of=AS_OF, budget_seconds=BUDGET, risk_min_score=40)
        assert any(c.section is BriefingSection.KPI_MOVEMENT for c in candidates)
        assert not any(c.section is BriefingSection.RISKS for c in candidates)

    def test_stale_evidence_marks_every_candidate_from_that_finding(self):
        candidates = kpi_candidates(
            [_kpi_finding().model_copy(update={"last_seen_at": _iso(100)})],
            as_of=AS_OF,
            budget_seconds=BUDGET,
            risk_min_score=40,
        )
        assert candidates
        assert all(c.is_stale and c.stale_reason for c in candidates)

    def test_a_non_kpi_kind_is_ignored(self):
        other = _kpi_finding().model_copy(update={"kind": "conflict"})
        assert kpi_candidates([other], as_of=AS_OF, budget_seconds=BUDGET, risk_min_score=40) == []

    def test_a_finding_with_no_current_value_is_skipped_rather_than_guessed(self):
        broken = _kpi_finding().model_copy(update={"payload": {"period": "2026-06"}})
        assert kpi_candidates([broken], as_of=AS_OF, budget_seconds=BUDGET, risk_min_score=40) == []


class TestSchemaDriftCandidates:
    def _drift(self, **overrides) -> SchemaDriftFindingInput:
        payload = {
            "database_id": "default",
            "table": "sales",
            "column": "amount",
            "change_kind": "column_removed",
            "is_inferred": False,
            "impacted": [
                {
                    "node_id": "metric:revenue",
                    "type": "metric",
                    "label": "Net Revenue",
                    "truth_level": "confirmed_business_truth",
                    "concept_key": "revenue",
                }
            ],
        }
        payload.update(overrides.pop("payload", {}))
        base = {
            "finding_id": "d1",
            "kind": "schema_drift_column_removed",
            "title": "Column removed: sales.amount",
            "status": "open",
            "risk_score": 60,
            "risk_tier": "medium",
            "confidence": 1.0,
            "version": 1,
            "last_seen_at": _iso(1),
            "payload": payload,
        }
        base.update(overrides)
        return SchemaDriftFindingInput(**base)

    def test_drift_under_a_published_concept_becomes_a_risk_estimate(self):
        candidates = schema_drift_candidates(
            [self._drift()], as_of=AS_OF, budget_seconds=BUDGET, risk_min_score=40
        )
        assert [c.section for c in candidates] == [BriefingSection.RISKS]
        assert candidates[0].claim_class is ClaimClass.ESTIMATE
        assert candidates[0].evidence[0].observed["impacted_published_concepts"] == 1.0
        assert find_unsupported_claims(candidates) == ()

    def test_low_risk_drift_with_no_impact_is_not_executive_news(self):
        quiet = self._drift(risk_score=5, risk_tier="low", payload={"impacted": []})
        assert (
            schema_drift_candidates([quiet], as_of=AS_OF, budget_seconds=BUDGET, risk_min_score=40)
            == []
        )

    def test_low_risk_drift_still_surfaces_when_a_published_concept_reads_it(self):
        """Impact, not score alone -- a structurally minor change under a
        reported number still matters."""
        impactful = self._drift(risk_score=5, risk_tier="low")
        assert schema_drift_candidates(
            [impactful], as_of=AS_OF, budget_seconds=BUDGET, risk_min_score=40
        )

    def test_a_truncated_impact_walk_is_disclosed_as_a_limitation(self):
        truncated = self._drift(payload={"impact_truncated": True})
        candidate = schema_drift_candidates(
            [truncated], as_of=AS_OF, budget_seconds=BUDGET, risk_min_score=40
        )[0]
        assert any("truncated" in limitation for limitation in candidate.limitations)

    def test_a_non_drift_kind_is_ignored(self):
        other = self._drift(kind="kpi_drop")
        assert (
            schema_drift_candidates([other], as_of=AS_OF, budget_seconds=BUDGET, risk_min_score=40)
            == []
        )


class TestRecommendationCandidates:
    def _record(self, **overrides) -> RecommendationInput:
        base = {
            "record_id": "r1",
            "category": "revenue",
            "kind": "action",
            "status": "generated",
            "claim_text": "Review EMEA pricing",
            "action": "Review EMEA pricing",
            "confidence": 0.7,
            "evidence_count": 2,
            "engine_version": "1.0.0",
            "evidence_version": "abc123",
            "generated_at": _iso(3),
        }
        base.update(overrides)
        return RecommendationInput(**base)

    def test_an_unresolved_record_becomes_a_recommendation_claim(self):
        candidates = recommendation_candidates([self._record()], as_of=AS_OF, budget_seconds=BUDGET)
        unresolved = next(
            c for c in candidates if c.section is BriefingSection.UNRESOLVED_RECOMMENDATIONS
        )
        assert unresolved.claim_class is ClaimClass.RECOMMENDATION
        assert unresolved.truth_level is DataTruthLevel.AI_INFERENCE
        assert unresolved.evidence[0].versions["evidence_version"] == "abc123"
        assert find_unsupported_claims(candidates) == ()

    @pytest.mark.parametrize("status", sorted(UNRESOLVED_RECOMMENDATION_STATUSES))
    def test_every_unresolved_status_is_surfaced(self, status):
        candidates = recommendation_candidates(
            [self._record(status=status)], as_of=AS_OF, budget_seconds=BUDGET
        )
        assert any(c.section is BriefingSection.UNRESOLVED_RECOMMENDATIONS for c in candidates)

    @pytest.mark.parametrize("status", ["resolved", "rejected", "incorrect", "expired"])
    def test_a_closed_judgment_is_never_resurfaced(self, status):
        assert (
            recommendation_candidates(
                [self._record(status=status)], as_of=AS_OF, budget_seconds=BUDGET
            )
            == []
        )

    @pytest.mark.parametrize("category", sorted(OPPORTUNITY_CATEGORIES))
    def test_a_growth_action_is_also_an_opportunity(self, category):
        candidates = recommendation_candidates(
            [self._record(category=category)], as_of=AS_OF, budget_seconds=BUDGET
        )
        assert any(c.section is BriefingSection.OPPORTUNITIES for c in candidates)

    def test_a_remediation_action_is_not_an_opportunity(self):
        candidates = recommendation_candidates(
            [self._record(category="data_quality")], as_of=AS_OF, budget_seconds=BUDGET
        )
        assert not any(c.section is BriefingSection.OPPORTUNITIES for c in candidates)

    def test_a_next_question_is_never_an_opportunity(self):
        candidates = recommendation_candidates(
            [self._record(kind="next_question")], as_of=AS_OF, budget_seconds=BUDGET
        )
        assert not any(c.section is BriefingSection.OPPORTUNITIES for c in candidates)

    def test_a_record_with_no_evidence_is_surfaced_with_that_stated(self):
        """The record is a real governed artifact worth resurfacing; what
        it asserts is not itself grounded, and the briefing says which."""
        candidates = recommendation_candidates(
            [self._record(evidence_count=0)], as_of=AS_OF, budget_seconds=BUDGET
        )
        assert candidates
        assert any("recorded no evidence" in limitation for limitation in candidates[0].limitations)

    def test_a_recommendation_never_ranks_high(self):
        """A suggestion outranking an observed collapse would misorder
        exactly the attention this briefing directs."""
        candidates = recommendation_candidates(
            [self._record(confidence=1.0)], as_of=AS_OF, budget_seconds=BUDGET
        )
        assert all(c.risk_tier != "high" for c in candidates)
        assert all(c.risk_score <= 60 for c in candidates)


class TestDataQualityCandidates:
    def _finding(self, **overrides) -> DataQualityFindingInput:
        base = {
            "dimension": "completeness",
            "severity": "critical",
            "table": "sales",
            "column": "amount",
            "claim_text": "amount is null in 42.00% of 1000 rows",
            "formula": "null_rate = null_count / total_count",
            "observed": {"null_rate": 42.0, "total_count": 1000.0},
            "engine_version": "1.0.0",
            "observed_at": _iso(1),
        }
        base.update(overrides)
        return DataQualityFindingInput(**base)

    def test_a_data_quality_finding_is_an_observed_fact(self):
        """`data_quality.models.DataQualityReport.truth_level` is
        `DATABASE_FACT` for a real reason -- a null rate is a
        measurement, so the briefing keeps it one."""
        candidates = data_quality_candidates([self._finding()], as_of=AS_OF, budget_seconds=BUDGET)
        observed = next(c for c in candidates if c.section is BriefingSection.DATA_QUALITY)
        assert observed.claim_class is ClaimClass.OBSERVED_FACT
        assert observed.truth_level is DataTruthLevel.DATABASE_FACT
        assert find_unsupported_claims(candidates) == ()

    def test_a_critical_finding_also_gets_a_separate_risk_estimate(self):
        candidates = data_quality_candidates([self._finding()], as_of=AS_OF, budget_seconds=BUDGET)
        risk = next(c for c in candidates if c.section is BriefingSection.RISKS)
        assert risk.claim_class is ClaimClass.ESTIMATE
        assert "not established here" in risk.detail

    @pytest.mark.parametrize("severity", ["info", "warning"])
    def test_a_non_critical_finding_is_reported_but_not_called_a_risk(self, severity):
        candidates = data_quality_candidates(
            [self._finding(severity=severity)], as_of=AS_OF, budget_seconds=BUDGET
        )
        assert [c.section for c in candidates] == [BriefingSection.DATA_QUALITY]

    def test_severity_drives_the_rank_deterministically(self):
        scores = {
            severity: data_quality_candidates(
                [self._finding(severity=severity)], as_of=AS_OF, budget_seconds=BUDGET
            )[0].risk_score
            for severity in ("info", "warning", "critical")
        }
        assert scores["info"] < scores["warning"] < scores["critical"]

    def test_a_claim_stating_a_number_the_caller_did_not_supply_is_withheld(self):
        """The one real failure mode of a caller-supplied source: the
        engine's own claim text cites a threshold the caller omitted from
        `observed`, so the claim cannot be verified and is not shown."""
        unverifiable = self._finding(
            claim_text="amount is null in 42.00% of rows, above the 5.00% threshold",
            observed={"null_rate": 42.0},
        )
        candidates = data_quality_candidates([unverifiable], as_of=AS_OF, budget_seconds=BUDGET)
        problems = find_unsupported_claims(candidates)
        assert {p.code for p in problems} == {"ungrounded_number"}
