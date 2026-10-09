"""Unit tests for `briefing/engine.py` and `briefing/reports.py`
(Prompt 51).

Pure: the engine takes already-fetched records and a caller-supplied
reference instant, and returns one briefing. No database, no clock, no
LLM anywhere in this file.

The three behaviours this prompt names explicitly -- unsupported-claim
detection, stale data and empty periods -- each have their own class
below. The claim-level half of unsupported-claim detection lives in
`tests/test_briefing_claims.py`; here it is tested as the engine's own
gate (a bad candidate never reaches `items`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from briefing.engine import build_briefing
from briefing.inputs import (
    DataQualityFindingInput,
    KpiFindingInput,
    RecommendationInput,
    SchemaDriftFindingInput,
)
from briefing.models import (
    BRIEFING_ENGINE_VERSION,
    BriefingPeriod,
    BriefingSection,
    ClaimClass,
)
from briefing.reports import render_markdown
from config.settings import Settings

AS_OF = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
_SETTINGS = Settings()


def _settings(**overrides) -> Settings:
    return Settings(**{**_SETTINGS.__dict__, **overrides})


def _iso(hours_ago: float) -> str:
    return (AS_OF - timedelta(hours=hours_ago)).isoformat()


def _kpi(
    finding_id: str = "f1",
    *,
    metric: str = "revenue",
    risk_score: int = 78,
    hours_ago: float = 2.0,
    direction: str = "down",
    change_percent: float = -36.0,
    **payload_overrides,
) -> KpiFindingInput:
    payload = {
        "metric_concept_key": metric,
        "policy_id": "p1",
        "period": "2026-06",
        "comparison": "previous_period",
        "baseline_label": "previous period 2026-05",
        "current_value": 640.0,
        "baseline_value": 1000.0,
        "change_absolute": -360.0,
        "change_percent": change_percent,
        "threshold_crossed": None,
        "detection_methods": [],
        "direction": direction,
        "has_sufficient_evidence": False,
        "limitations": ["No dimension breakdown was configured."],
        "dimension_columns": [],
        "engine_version": "1.0.0",
    }
    payload.update(payload_overrides)
    return KpiFindingInput(
        finding_id=finding_id,
        kind="kpi_drop" if direction == "down" else "kpi_spike",
        title=f"{metric} moved in 2026-06",
        status="open",
        risk_score=risk_score,
        risk_tier="high" if risk_score >= 70 else "medium" if risk_score >= 40 else "low",
        confidence=0.65,
        version=2,
        last_seen_at=_iso(hours_ago),
        payload=payload,
    )


def _recommendation(record_id: str = "r1", *, hours_ago: float = 3.0) -> RecommendationInput:
    return RecommendationInput(
        record_id=record_id,
        category="revenue",
        kind="action",
        status="generated",
        claim_text="Review EMEA pricing",
        action="Review EMEA pricing",
        confidence=0.7,
        evidence_count=2,
        engine_version="1.0.0",
        evidence_version="abc123",
        generated_at=_iso(hours_ago),
    )


def _data_quality(*, severity: str = "critical", hours_ago: float = 1.0):
    return DataQualityFindingInput(
        dimension="completeness",
        severity=severity,
        table="sales",
        column="amount",
        claim_text="amount is null in 42.00% of rows",
        formula="null_rate = null_count / total_count",
        observed={"null_rate": 42.0},
        engine_version="1.0.0",
        observed_at=_iso(hours_ago),
    )


def _drift(*, risk_score: int = 60, hours_ago: float = 1.0) -> SchemaDriftFindingInput:
    return SchemaDriftFindingInput(
        finding_id="d1",
        kind="schema_drift_column_removed",
        title="Column removed: sales.amount",
        status="open",
        risk_score=risk_score,
        risk_tier="medium",
        confidence=1.0,
        version=1,
        last_seen_at=_iso(hours_ago),
        payload={
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
        },
    )


def _build(**overrides):
    payload = {
        "database_id": "default",
        "period": BriefingPeriod.DAILY,
        "as_of": AS_OF,
        "period_label": "2026-06-30",
        "settings": _SETTINGS,
    }
    payload.update(overrides)
    return build_briefing(**payload)


class TestAssembly:
    def test_every_named_section_is_reachable_from_real_records(self):
        briefing = _build(
            kpi_findings=[
                _kpi(
                    detection_methods=["rolling_zscore"],
                    has_sufficient_evidence=True,
                    confidence=0.65,
                    dimension_columns=["region"],
                    contributors=[
                        {"label": "EMEA", "contribution": -200.0, "contribution_percent": -55.6}
                    ],
                ),
                _kpi("f2", metric="orders", direction="up", change_percent=22.0),
            ],
            recommendations=[_recommendation()],
            data_quality_findings=[_data_quality()],
        )
        sections = {item.section for item in briefing.items}
        assert sections == set(BriefingSection)
        assert briefing.empty_sections == ()

    def test_the_fact_estimate_recommendation_split_is_reported(self):
        briefing = _build(
            kpi_findings=[_kpi()],
            recommendations=[_recommendation()],
            data_quality_findings=[_data_quality()],
        )
        assert briefing.observed_fact_count == 2  # the movement and the DQ finding
        assert briefing.estimate_count >= 2  # risk readings of both
        assert briefing.recommendation_count == 2  # unresolved + opportunity
        assert (
            briefing.observed_fact_count + briefing.estimate_count + briefing.recommendation_count
            == len(briefing.items)
        )

    def test_an_observed_fact_is_never_an_inference_and_vice_versa(self):
        briefing = _build(
            kpi_findings=[_kpi(detection_methods=["iqr"])],
            recommendations=[_recommendation()],
            data_quality_findings=[_data_quality()],
        )
        for item in briefing.items:
            if item.claim_class is ClaimClass.OBSERVED_FACT:
                assert item.truth_level.value == "database_fact"
            else:
                assert item.truth_level.value == "ai_inference"

    def test_every_claim_carries_evidence_with_at_least_one_version(self):
        briefing = _build(
            kpi_findings=[_kpi()],
            schema_drift_findings=[_drift()],
            recommendations=[_recommendation()],
            data_quality_findings=[_data_quality()],
        )
        assert briefing.items
        for item in briefing.items:
            assert item.evidence
            for link in item.evidence:
                assert link.versions
                assert link.reference_id

    def test_the_engine_version_is_recorded(self):
        assert _build().engine_version == BRIEFING_ENGINE_VERSION

    def test_the_caller_s_reference_instant_is_echoed_not_a_clock_read(self):
        assert _build().as_of == AS_OF.isoformat()

    def test_claims_are_ranked_highest_risk_first(self):
        briefing = _build(
            kpi_findings=[_kpi("f1", risk_score=78), _kpi("f2", metric="orders", risk_score=45)]
        )
        scores = [item.risk_score for item in briefing.items]
        assert scores == sorted(scores, reverse=True)

    def test_nothing_is_persisted_the_function_is_a_pure_read(self):
        """Two identical calls produce identical briefings -- there is no
        accumulated state anywhere in this package."""
        first = _build(kpi_findings=[_kpi()])
        second = _build(kpi_findings=[_kpi()])
        assert first == second


class TestEmptyPeriods:
    def test_an_entirely_empty_briefing_is_honest_about_why(self):
        briefing = _build()
        assert briefing.is_empty
        assert briefing.items == ()
        assert set(briefing.empty_sections) == set(BriefingSection)
        assert any("Read `coverage`" in limitation for limitation in briefing.limitations)

    def test_coverage_distinguishes_not_supplied_from_supplied_but_quiet(self):
        """The single most important property of an empty briefing: an
        executive must be able to tell "nothing happened" from "nothing
        was checked"."""
        briefing = _build(
            kpi_findings=[],
            # Supplied, checked, and deliberately below the bar for any
            # claim: a closed recommendation.
            recommendations=[
                _recommendation().model_copy(update={"status": "resolved"}),
            ],
        )
        by_source = {entry.source: entry for entry in briefing.coverage}
        assert by_source["kpi_findings"].supplied is False
        assert "nothing was checked" in by_source["kpi_findings"].note
        assert by_source["recommendations"].supplied is True
        assert by_source["recommendations"].input_count == 1
        assert by_source["recommendations"].items_produced == 0
        assert "none produced a claim" in by_source["recommendations"].note

    def test_an_omitted_source_is_named_in_coverage_and_in_limitations(self):
        """An unauthorized or unavailable source must never look the same
        as a quiet one."""
        briefing = _build(
            omitted_sources=(("recommendations", "omitted: not permitted for this account."),)
        )
        by_source = {entry.source: entry for entry in briefing.coverage}
        assert by_source["recommendations"].supplied is False
        assert "not permitted" in by_source["recommendations"].note
        assert any("recommendations:" in limitation for limitation in briefing.limitations)

    def test_every_source_appears_in_coverage_even_with_no_records_at_all(self):
        assert {entry.source for entry in _build().coverage} == {
            "kpi_findings",
            "schema_drift_findings",
            "recommendations",
            "data_quality_findings",
        }

    def test_a_section_with_no_claims_is_listed_rather_than_hidden(self):
        briefing = _build(data_quality_findings=[_data_quality(severity="info")])
        assert BriefingSection.DATA_QUALITY not in briefing.empty_sections
        assert BriefingSection.KPI_MOVEMENT in briefing.empty_sections

    def test_the_empty_report_tells_the_reader_to_check_coverage(self):
        report = render_markdown(_build())
        assert "No claims in this period" in report
        assert "never checked" in report
        assert "## Coverage" in report


class TestStaleData:
    def test_old_evidence_is_marked_stale_and_explained_not_dropped(self):
        briefing = _build(kpi_findings=[_kpi(hours_ago=100)])
        assert briefing.items, "a stale finding is still reported"
        assert all(item.is_stale for item in briefing.items)
        assert all("freshness budget" in item.stale_reason for item in briefing.items)
        assert briefing.stale_item_count == len(briefing.items)

    def test_a_stale_input_is_listed_once_per_record_not_once_per_claim(self):
        briefing = _build(
            kpi_findings=[
                _kpi(
                    hours_ago=100,
                    detection_methods=["iqr"],
                    has_sufficient_evidence=True,
                    contributors=[
                        {"label": "EMEA", "contribution": -200.0, "contribution_percent": -55.6}
                    ],
                )
            ]
        )
        assert len(briefing.items) > 1
        assert len(briefing.stale_inputs) == 1
        assert briefing.stale_inputs[0].reference_id == "f1"

    def test_a_record_with_no_timestamp_is_disclosed_not_assumed_fresh(self):
        briefing = _build(kpi_findings=[_kpi().model_copy(update={"last_seen_at": None})])
        assert briefing.stale_inputs
        assert "no observation timestamp" in briefing.stale_inputs[0].detail

    def test_the_same_record_is_stale_in_a_daily_briefing_and_fresh_in_a_monthly_one(self):
        record = _kpi(hours_ago=100)
        assert _build(period=BriefingPeriod.DAILY, kpi_findings=[record]).stale_inputs
        assert _build(period=BriefingPeriod.MONTHLY, kpi_findings=[record]).stale_inputs == ()

    def test_a_stale_claim_sorts_below_a_fresh_claim_of_the_same_rank(self):
        briefing = _build(
            kpi_findings=[
                _kpi("fresh", metric="orders", risk_score=78, hours_ago=1),
                _kpi("old", metric="revenue", risk_score=78, hours_ago=200),
            ]
        )
        movements = briefing.items_for(BriefingSection.KPI_MOVEMENT)
        assert [item.is_stale for item in movements] == [False, True]

    def test_staleness_is_disclosed_in_the_report(self):
        report = render_markdown(_build(kpi_findings=[_kpi(hours_ago=100)]))
        assert "Stale evidence" in report
        assert "ranked below fresh records" in report


class TestUnsupportedClaimsAreWithheld:
    def test_a_claim_whose_number_is_not_in_its_evidence_never_reaches_items(self):
        """A caller-supplied data-quality claim citing a threshold it did
        not also supply in `observed` is reported as withheld rather than
        shown."""
        briefing = _build(
            data_quality_findings=[
                _data_quality().model_copy(
                    update={
                        "claim_text": "null rate 42.00% exceeds the 5.00% threshold",
                        "observed": {"null_rate": 42.0},
                    }
                )
            ]
        )
        assert briefing.items == ()
        assert {problem.code for problem in briefing.withheld_claims} == {"ungrounded_number"}
        assert any("withheld" in limitation for limitation in briefing.limitations)

    def test_a_withheld_claim_does_not_suppress_its_supported_siblings(self):
        briefing = _build(
            kpi_findings=[_kpi()],
            data_quality_findings=[
                _data_quality().model_copy(
                    update={
                        "claim_text": "null rate 42.00% exceeds the 5.00% threshold",
                        "observed": {"null_rate": 42.0},
                    }
                )
            ],
        )
        assert briefing.withheld_claims
        assert briefing.items_for(BriefingSection.KPI_MOVEMENT)

    def test_a_withheld_claim_is_still_counted_in_its_source_s_coverage_input(self):
        briefing = _build(
            data_quality_findings=[
                _data_quality().model_copy(
                    update={
                        "claim_text": "null rate 42.00% exceeds the 5.00% threshold",
                        "observed": {"null_rate": 42.0},
                    }
                )
            ]
        )
        by_source = {entry.source: entry for entry in briefing.coverage}
        assert by_source["data_quality_findings"].input_count == 1
        assert by_source["data_quality_findings"].items_produced == 0

    def test_withheld_claims_are_reported_in_the_report(self):
        report = render_markdown(
            _build(
                data_quality_findings=[
                    _data_quality().model_copy(
                        update={
                            "claim_text": "null rate 42.00% exceeds the 5.00% threshold",
                            "observed": {"null_rate": 42.0},
                        }
                    )
                ]
            )
        )
        assert "Withheld claims" in report
        assert "ungrounded_number" in report


class TestBoundsAndSettings:
    def test_a_section_is_capped_after_ranking_and_the_cap_is_disclosed(self):
        briefing = _build(
            settings=_settings(briefing_max_items_per_section=2),
            kpi_findings=[
                _kpi(f"f{index}", metric=f"metric_{index}", risk_score=70 - index)
                for index in range(5)
            ],
        )
        movements = briefing.items_for(BriefingSection.KPI_MOVEMENT)
        assert len(movements) == 2
        assert [item.risk_score for item in movements] == [70, 69]
        assert BriefingSection.KPI_MOVEMENT in briefing.truncated_sections
        assert any("capped" in limitation for limitation in briefing.limitations)

    def test_the_risk_floor_is_read_from_settings(self):
        low = _kpi(risk_score=20)
        with_floor = _build(settings=_settings(briefing_risk_min_tier="medium"), kpi_findings=[low])
        without_floor = _build(settings=_settings(briefing_risk_min_tier="low"), kpi_findings=[low])
        assert with_floor.items_for(BriefingSection.RISKS) == ()
        assert without_floor.items_for(BriefingSection.RISKS)

    @pytest.mark.parametrize("period", list(BriefingPeriod))
    def test_every_cadence_renders(self, period):
        report = render_markdown(_build(period=period, kpi_findings=[_kpi()]))
        assert period.value.capitalize() in report


class TestReportHonesty:
    def test_the_report_labels_each_claim_with_its_class_and_truth_level(self):
        report = render_markdown(_build(kpi_findings=[_kpi(detection_methods=["iqr"])]))
        assert "Claim type: **observed fact** (truth level `database_fact`)" in report
        assert "Claim type: **estimate** (truth level `ai_inference`)" in report

    def test_the_report_states_the_three_way_distinction_up_front(self):
        report = render_markdown(_build(kpi_findings=[_kpi()]))
        assert "Observed facts are arithmetic over governed query results." in report
        assert "Nothing in this briefing is confirmed business truth" in report

    def test_the_report_prints_every_claim_s_evidence_reference_and_versions(self):
        report = render_markdown(_build(kpi_findings=[_kpi()]))
        assert "Evidence: `kpi_finding` `f1`" in report
        assert "versions: briefing_engine_version=" in report

    def test_the_drivers_section_disclaims_causation(self):
        report = render_markdown(
            _build(
                kpi_findings=[
                    _kpi(
                        has_sufficient_evidence=True,
                        contributors=[
                            {
                                "label": "EMEA",
                                "contribution": -200.0,
                                "contribution_percent": -55.6,
                            }
                        ],
                    )
                ]
            )
        )
        assert "not a demonstrated cause" in report
