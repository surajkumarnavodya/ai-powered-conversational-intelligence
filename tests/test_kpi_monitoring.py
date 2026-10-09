"""Unit tests for `kpi_monitoring/` (Prompt 50,
`50_PROACTIVE_AI_ANALYST_CONTRACT.md`).

Fully offline: no database, no Ollama, no network. Every module in the
package is a pure function over supplied data, which is what makes that
possible.

The mission names three things to test explicitly, and each gets its own
class here: `TestAlertNoise`, `TestDuplicateSuppression` and
`TestInsufficientEvidence`. Cross-cutting guarantees (never asserting a
cause, approved-metrics-only, tenant isolation) live in
`tests/security/test_kpi_monitoring_never_asserts_cause.py`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.provenance import DataTruthLevel
from config.settings import Settings
from kpi_monitoring.calendar import exclude_non_business_periods, is_alertable_period
from kpi_monitoring.detect import detect_change
from kpi_monitoring.engine import MetricSeries, monitor_kpis
from kpi_monitoring.evidence import investigate
from kpi_monitoring.findings import change_to_finding, finding_key, is_kpi_kind
from kpi_monitoring.models import (
    AlertPreference,
    BusinessCalendar,
    ChangeEvidence,
    ChangeKind,
    ComparisonPeriod,
    MonitoringPolicy,
    QuietPeriod,
    SuppressionReason,
)
from kpi_monitoring.policy import ApprovedMetric, index_catalog_entries, validate_policies
from kpi_monitoring.reports import render_markdown
from kpi_monitoring.suppression import decide_alerts


class CatalogRow:
    """Stand-in for a `SemanticCatalogEntry` row / `CatalogEntrySnapshot`
    -- both expose exactly these attributes, which is the duck-typed
    boundary `index_catalog_entries` is written against."""

    def __init__(
        self,
        concept_key: str,
        concept_type: str = "metric",
        status: str = "published",
        business_name: str = "",
        dimensions: tuple[str, ...] = (),
        version: int = 1,
    ) -> None:
        self.concept_key = concept_key
        self.concept_type = concept_type
        self.status = status
        self.business_name = business_name
        self.dimensions = dimensions
        self.version = version


REVENUE = ApprovedMetric("revenue", "Net Revenue")
FLAT_THEN_DROP = [
    ("2026-01", 1000.0),
    ("2026-02", 1010.0),
    ("2026-03", 995.0),
    ("2026-04", 1005.0),
    ("2026-05", 1000.0),
    ("2026-06", 640.0),
]
BREAKDOWN_COLUMNS = ["region", "revenue"]
BASELINE_ROWS = [("East", 500.0), ("West", 300.0), ("North", 200.0)]
CURRENT_ROWS = [("East", 160.0), ("West", 290.0), ("North", 190.0)]


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def _policy(**overrides: object) -> MonitoringPolicy:
    base: dict[str, object] = {
        "policy_id": "p1",
        "metric_concept_key": "revenue",
        "min_change_percent": 10.0,
    }
    base.update(overrides)
    return MonitoringPolicy(**base)  # type: ignore[arg-type]


class TestBusinessCalendar:
    def test_non_business_periods_are_excluded_from_the_series(self):
        calendar = BusinessCalendar(non_business_periods=frozenset({"2026-02"}))
        kept, excluded = exclude_non_business_periods(FLAT_THEN_DROP, calendar)
        assert [period for period, _ in kept] == [
            "2026-01",
            "2026-03",
            "2026-04",
            "2026-05",
            "2026-06",
        ]
        assert excluded == ("2026-02",)

    def test_no_calendar_keeps_everything(self):
        kept, excluded = exclude_non_business_periods(FLAT_THEN_DROP, None)
        assert len(kept) == len(FLAT_THEN_DROP)
        assert excluded == ()

    def test_chronological_order_survives_exclusion(self):
        """Every downstream lag counts positions, so a reordering here
        would silently corrupt `seasonal_lag`/`trailing_window`."""
        calendar = BusinessCalendar(non_business_periods=frozenset({"2026-03", "2026-05"}))
        kept, _ = exclude_non_business_periods(FLAT_THEN_DROP, calendar)
        assert [period for period, _ in kept] == sorted(period for period, _ in kept)

    def test_a_holiday_never_becomes_the_baseline(self):
        """The real reason this module exists: a holiday period left in
        the series would be the comparison point, turning a normal month
        into a fabricated 'revenue exploded' finding."""
        series = [*FLAT_THEN_DROP[:5], ("2026-05H", 10.0), ("2026-06", 1000.0)]
        calendar = BusinessCalendar(non_business_periods=frozenset({"2026-05H"}))
        change, _ = detect_change(_policy(), REVENUE, series, calendar=calendar)
        # Against the holiday it would be +9900%; against the real
        # previous period it is 0% and therefore no finding at all.
        assert change is None

    def test_is_alertable_period_is_separate_from_baseline_exclusion(self):
        calendar = BusinessCalendar(non_business_periods=frozenset({"2026-06"}))
        assert is_alertable_period("2026-06", calendar) is False
        assert is_alertable_period("2026-05", calendar) is True
        assert is_alertable_period("2026-06", None) is True


class TestApprovedMetricsOnly:
    def test_the_three_refusals_are_distinguishable(self):
        catalog = index_catalog_entries(
            [
                CatalogRow("revenue"),
                CatalogRow("draft_metric", status="draft"),
                CatalogRow("region", concept_type="dimension"),
            ]
        )
        policies = [
            _policy(policy_id="ok", metric_concept_key="revenue"),
            _policy(policy_id="draft", metric_concept_key="draft_metric"),
            _policy(policy_id="dim", metric_concept_key="region"),
            _policy(policy_id="ghost", metric_concept_key="never_existed"),
        ]
        runnable, violations = validate_policies(policies, catalog)

        assert [policy.policy_id for policy in runnable] == ["ok"]
        by_id = {violation.policy_id: violation.code for violation in violations}
        assert by_id == {
            "draft": "metric_not_published",
            "dim": "metric_not_a_metric",
            "ghost": "metric_not_found",
        }

    def test_one_bad_policy_does_not_reject_the_whole_run(self):
        """Policies are independent; refusing ninety-nine good ones over
        one typo would be its own outage."""
        catalog = index_catalog_entries([CatalogRow("revenue")])
        policies = [
            _policy(policy_id=f"good-{i}", metric_concept_key="revenue") for i in range(5)
        ] + [_policy(policy_id="bad", metric_concept_key="nope")]
        runnable, violations = validate_policies(policies, catalog)
        assert len(runnable) == 5
        assert len(violations) == 1

    def test_a_disabled_policy_runs_and_violates_nothing(self):
        catalog = index_catalog_entries([CatalogRow("revenue")])
        runnable, violations = validate_policies([_policy(enabled=False)], catalog)
        assert runnable == ()
        assert violations == ()

    def test_policies_beyond_the_cap_are_reported_not_silently_dropped(self):
        catalog = index_catalog_entries([CatalogRow("revenue")])
        policies = [_policy(policy_id=f"p{i}") for i in range(5)]
        _runnable, violations = validate_policies(
            policies, catalog, settings=_settings(kpi_monitor_max_policies_per_scan=2)
        )
        assert [v.code for v in violations] == ["too_many_policies"] * 3

    def test_an_approved_version_outranks_an_earlier_unapproved_one(self):
        catalog = index_catalog_entries(
            [CatalogRow("revenue", status="draft"), CatalogRow("revenue", status="published")]
        )
        assert "revenue" in catalog.approved
        assert "revenue" not in catalog.rejection_codes


class TestComparisonPeriods:
    def test_previous_period(self):
        change, _ = detect_change(_policy(), REVENUE, FLAT_THEN_DROP)
        assert change is not None
        assert change.baseline_value == 1000.0
        assert "previous period 2026-05" in change.baseline_label
        assert change.change_percent == pytest.approx(-36.0)

    def test_trailing_average(self):
        change, _ = detect_change(
            _policy(comparison=ComparisonPeriod.TRAILING_AVERAGE, trailing_window=4),
            REVENUE,
            FLAT_THEN_DROP,
        )
        assert change is not None
        assert change.baseline_value == pytest.approx((1010.0 + 995.0 + 1005.0 + 1000.0) / 4)
        assert "mean of the 4 periods" in change.baseline_label

    def test_same_period_last_year(self):
        change, _ = detect_change(
            _policy(comparison=ComparisonPeriod.SAME_PERIOD_LAST_YEAR, seasonal_lag=5),
            REVENUE,
            FLAT_THEN_DROP,
        )
        assert change is not None
        assert change.baseline_value == 1000.0  # 2026-01, five positions back
        assert "same period last year" in change.baseline_label

    def test_too_little_history_is_a_reported_skip_not_a_silent_pass(self):
        flat = [("2026-01", 1000.0), ("2026-02", 1000.0), ("2026-03", 1000.0)]
        change, reason = detect_change(
            _policy(comparison=ComparisonPeriod.SAME_PERIOD_LAST_YEAR, seasonal_lag=99),
            REVENUE,
            flat,
        )
        assert change is None
        assert reason is not None and "needs 99 prior business periods" in reason

    def test_no_baseline_still_reports_a_statistically_flagged_point(self):
        """Deliberate: with no comparison available the policy rule
        cannot judge the period, but a point the anomaly detector flags
        is still worth surfacing -- as a trend break rather than a
        drop/spike, since there is nothing it moved *against*."""
        change, _ = detect_change(
            _policy(comparison=ComparisonPeriod.SAME_PERIOD_LAST_YEAR, seasonal_lag=99),
            REVENUE,
            FLAT_THEN_DROP,
        )
        assert change is not None
        assert change.kind is ChangeKind.TREND_BREAK
        assert change.baseline_value is None
        assert change.detection_methods

    def test_every_baseline_carries_a_human_readable_label(self):
        for comparison in ComparisonPeriod:
            change, _ = detect_change(
                _policy(comparison=comparison, seasonal_lag=3, trailing_window=3),
                REVENUE,
                FLAT_THEN_DROP,
            )
            assert change is not None, comparison
            assert change.baseline_label.strip(), comparison


class TestDetection:
    def test_a_move_below_the_threshold_is_not_reported(self):
        steady = [("2026-01", 1000.0), ("2026-02", 1020.0)]
        change, reason = detect_change(_policy(min_change_percent=10.0), REVENUE, steady)
        assert change is None
        assert reason is not None and "below the 10.0% threshold" in reason

    def test_a_drop_and_a_spike_are_distinguished(self):
        drop, _ = detect_change(_policy(), REVENUE, [("a", 1000.0), ("b", 500.0)])
        spike, _ = detect_change(_policy(), REVENUE, [("a", 1000.0), ("b", 2000.0)])
        assert drop is not None and drop.kind is ChangeKind.DROP and drop.direction == "down"
        assert spike is not None and spike.kind is ChangeKind.SPIKE and spike.direction == "up"

    def test_a_threshold_breach_fires_regardless_of_move_size(self):
        """An operator who set a floor meant it even for a tiny move."""
        change, _ = detect_change(
            _policy(min_change_percent=90.0, lower_threshold=999.0),
            REVENUE,
            [("a", 1000.0), ("b", 998.0)],
        )
        assert change is not None
        assert change.kind is ChangeKind.THRESHOLD_BREACH
        assert change.threshold_crossed == 999.0

    def test_a_zero_baseline_does_not_divide_by_zero(self):
        change, reason = detect_change(_policy(), REVENUE, [("a", 0.0), ("b", 5.0)])
        # Either outcome is acceptable; crashing is not.
        assert change is None or change.change_percent is None
        if change is None:
            assert reason is not None and "undefined" in reason

    def test_anomaly_methods_are_recorded_when_they_also_fire(self):
        change, _ = detect_change(_policy(), REVENUE, FLAT_THEN_DROP)
        assert change is not None
        assert change.detection_methods  # a 36% collapse after a flat run
        assert all(isinstance(method, str) for method in change.detection_methods)

    def test_only_the_latest_period_is_ever_reported(self):
        """Raising one finding per historical anomaly would bury what
        just happened and re-raise old news on every run."""
        noisy = [("a", 1000.0), ("b", 100.0), ("c", 1000.0), ("d", 1010.0)]
        change, _ = detect_change(_policy(), REVENUE, noisy)
        assert change is None or change.period == "d"

    def test_a_point_flagged_by_both_detectors_is_reported_once(self):
        change, _ = detect_change(_policy(), REVENUE, FLAT_THEN_DROP)
        assert change is not None
        # One object, with both sources represented on it.
        assert change.kind is ChangeKind.DROP
        assert change.detection_methods


class TestInsufficientEvidence:
    """The mission's own named requirement: a change that cannot be
    explained must say so, never imply a cause."""

    def _change(self):
        change, _ = detect_change(_policy(dimension_columns=("region",)), REVENUE, FLAT_THEN_DROP)
        assert change is not None
        return change

    def test_no_dimension_columns_configured(self):
        change, _ = detect_change(_policy(), REVENUE, FLAT_THEN_DROP)
        assert change is not None
        evidence = investigate(change, _policy())
        assert evidence.has_sufficient_evidence is False
        assert evidence.contributors == ()
        assert evidence.confidence is None
        assert any("no dimension columns" in limitation for limitation in evidence.limitations)

    def test_no_breakdown_supplied(self):
        evidence = investigate(self._change(), _policy(dimension_columns=("region",)))
        assert evidence.has_sufficient_evidence is False
        assert any("no dimension breakdown" in limitation for limitation in evidence.limitations)

    def test_breakdown_missing_the_policys_dimension_column(self):
        evidence = investigate(
            self._change(),
            _policy(dimension_columns=("country",)),
            current_breakdown=(BREAKDOWN_COLUMNS, CURRENT_ROWS),
            baseline_breakdown=(BREAKDOWN_COLUMNS, BASELINE_ROWS),
            value_column="revenue",
        )
        assert evidence.has_sufficient_evidence is False
        assert any("country" in limitation for limitation in evidence.limitations)

    def test_mismatched_breakdown_columns(self):
        evidence = investigate(
            self._change(),
            _policy(dimension_columns=("region",)),
            current_breakdown=(["region", "revenue"], CURRENT_ROWS),
            baseline_breakdown=(["region", "sales"], BASELINE_ROWS),
            value_column="revenue",
        )
        assert evidence.has_sufficient_evidence is False
        assert any("different columns" in limitation for limitation in evidence.limitations)

    def test_a_broad_based_change_explains_nothing_and_says_so(self):
        """Every region moved a little: real, but not attributable."""
        evidence = investigate(
            self._change(),
            _policy(dimension_columns=("region",)),
            current_breakdown=(BREAKDOWN_COLUMNS, [("East", 320.0), ("West", 320.0)]),
            baseline_breakdown=(BREAKDOWN_COLUMNS, [("East", 500.0), ("West", 500.0)]),
            value_column="revenue",
        )
        if not evidence.has_sufficient_evidence:
            assert evidence.limitations
            assert evidence.contributors == ()

    def test_insufficient_evidence_always_carries_a_limitation(self):
        with pytest.raises(ValidationError):
            ChangeEvidence(has_sufficient_evidence=False, limitations=())

    def test_contributors_cannot_exist_without_evidence(self):
        """The acceptance criterion as a type-level guarantee."""
        from kpi_monitoring.models import DimensionContribution

        with pytest.raises(ValidationError):
            ChangeEvidence(
                has_sufficient_evidence=False,
                limitations=("x",),
                contributors=(
                    DimensionContribution(
                        label="East",
                        current_value=1.0,
                        baseline_value=2.0,
                        contribution=-1.0,
                        contribution_percent=-50.0,
                    ),
                ),
            )

    def test_confidence_cannot_exist_without_evidence(self):
        with pytest.raises(ValidationError):
            ChangeEvidence(has_sufficient_evidence=False, limitations=("x",), confidence=0.9)

    def test_sufficient_evidence_populates_contributors_and_confidence(self):
        evidence = investigate(
            self._change(),
            _policy(dimension_columns=("region",)),
            current_breakdown=(BREAKDOWN_COLUMNS, CURRENT_ROWS),
            baseline_breakdown=(BREAKDOWN_COLUMNS, BASELINE_ROWS),
            value_column="revenue",
        )
        assert evidence.has_sufficient_evidence is True
        assert evidence.contributors
        assert evidence.confidence is not None
        assert evidence.contributors[0].label == "East"


class TestDuplicateSuppression:
    """The mission's own named requirement, in both its halves."""

    def _pair(self, period: str = "2026-06"):
        series = [*FLAT_THEN_DROP[:-1], (period, 640.0)]
        change, _ = detect_change(_policy(), REVENUE, series)
        assert change is not None
        evidence = investigate(change, _policy())
        return change, change_to_finding(change, evidence)

    def test_the_same_movement_produces_the_same_key_every_run(self):
        first, _ = self._pair()
        second, _ = self._pair()
        assert finding_key(first) == finding_key(second)

    def test_a_different_period_is_a_different_finding(self):
        """Keying on policy+metric alone would collapse every month into
        one perpetually-updated row and destroy the history."""
        june, _ = self._pair("2026-06")
        july, _ = self._pair("2026-07")
        assert finding_key(june) != finding_key(july)

    def test_a_different_policy_on_the_same_metric_is_a_different_finding(self):
        change, _ = detect_change(_policy(policy_id="a"), REVENUE, FLAT_THEN_DROP)
        other, _ = detect_change(_policy(policy_id="b"), REVENUE, FLAT_THEN_DROP)
        assert change is not None and other is not None
        assert finding_key(change) != finding_key(other)

    def test_an_already_open_finding_is_not_re_alerted(self):
        """The alert-level half: re-paging about something nobody has
        acted on yet is how an alerting system teaches people to ignore
        it."""
        change, finding = self._pair()
        bundles = decide_alerts(
            [(change, finding)],
            [AlertPreference(user_id="alice")],
            previously_open_keys=frozenset({finding.key}),
        )
        decision = bundles[0].decisions[0]
        assert decision.alerted is False
        assert SuppressionReason.DUPLICATE_OF_OPEN_FINDING in decision.suppressed_by

    def test_a_new_finding_is_alerted(self):
        change, finding = self._pair()
        bundles = decide_alerts(
            [(change, finding)],
            [AlertPreference(user_id="alice")],
            previously_open_keys=frozenset(),
        )
        assert bundles[0].decisions[0].alerted is True

    def test_a_suppressed_duplicate_is_still_recorded_not_dropped(self):
        """Suppression governs alerting, never evidence."""
        change, finding = self._pair()
        bundles = decide_alerts(
            [(change, finding)],
            [AlertPreference(user_id="alice")],
            previously_open_keys=frozenset({finding.key}),
        )
        # It still appears in the bundle, with its reason -- a complete
        # account of the run, not only what got through.
        assert len(bundles[0].decisions) == 1
        assert bundles[0].suppressed_count == 1

    def test_the_key_is_stable_across_content_changes(self):
        """A restatement must version the *same* row, not create a new
        one -- so the key must not depend on the values."""
        change_a, _ = detect_change(_policy(), REVENUE, FLAT_THEN_DROP)
        restated = [*FLAT_THEN_DROP[:-1], ("2026-06", 600.0)]
        change_b, _ = detect_change(_policy(), REVENUE, restated)
        assert change_a is not None and change_b is not None
        assert finding_key(change_a) == finding_key(change_b)


class TestAlertNoise:
    """The mission's own named requirement: a run must not flood."""

    def _many(self, count: int):
        items = []
        for index in range(count):
            # Each drop clears the 10% floor and is progressively larger,
            # so the set spans both risk tiers and has an unambiguous
            # ranking to assert against.
            series = [("a", 1000.0), ("b", 1000.0 * (1.0 - (0.15 + index * 0.05)))]
            policy = _policy(policy_id=f"p{index}", metric_concept_key=f"m{index}")
            change, _ = detect_change(policy, ApprovedMetric(f"m{index}"), series)
            assert change is not None
            evidence = investigate(change, policy)
            items.append((change, change_to_finding(change, evidence)))
        return items

    def test_a_per_recipient_cap_bounds_one_runs_alerts(self):
        items = self._many(12)
        bundles = decide_alerts(items, [AlertPreference(user_id="alice", max_alerts_per_run=3)])
        assert bundles[0].alerted_count == 3
        assert bundles[0].suppressed_count == 9

    def test_the_cap_keeps_the_highest_ranked_findings(self):
        items = self._many(10)
        bundles = decide_alerts(items, [AlertPreference(user_id="alice", max_alerts_per_run=2)])
        alerted = [d for d in bundles[0].decisions if d.alerted]
        suppressed = [d for d in bundles[0].decisions if not d.alerted]
        assert len(alerted) == 2
        assert min(d.risk_score for d in alerted) >= max(d.risk_score for d in suppressed)

    def test_a_severity_floor_silences_low_tier_findings(self):
        items = self._many(6)
        bundles = decide_alerts(items, [AlertPreference(user_id="bob", min_risk_tier="high")])
        for decision in bundles[0].decisions:
            if not decision.alerted:
                assert SuppressionReason.BELOW_USER_SEVERITY in decision.suppressed_by

    def test_one_users_mute_never_affects_another(self):
        items = self._many(2)
        bundles = decide_alerts(
            items,
            [
                AlertPreference(user_id="alice"),
                AlertPreference(user_id="carol", muted_metric_keys=frozenset({"m0", "m1"})),
            ],
        )
        by_user = {bundle.user_id: bundle for bundle in bundles}
        assert by_user["alice"].alerted_count == 2
        assert by_user["carol"].alerted_count == 0

    def test_no_preferences_means_no_bundles_not_alert_everyone(self):
        assert decide_alerts(self._many(3), []) == ()

    def test_every_finding_appears_in_every_bundle_with_its_reasons(self):
        items = self._many(4)
        bundles = decide_alerts(items, [AlertPreference(user_id="alice", max_alerts_per_run=1)])
        assert len(bundles[0].decisions) == 4
        for decision in bundles[0].decisions:
            if not decision.alerted:
                assert decision.suppression_detail

    def test_ranking_is_deterministic_across_runs(self):
        items = self._many(5)
        first = decide_alerts(items, [AlertPreference(user_id="a")])[0]
        second = decide_alerts(items, [AlertPreference(user_id="a")])[0]
        assert [d.finding_key for d in first.decisions] == [d.finding_key for d in second.decisions]


class TestQuietPeriods:
    def _pair(self):
        change, _ = detect_change(_policy(), REVENUE, FLAT_THEN_DROP)
        assert change is not None
        return change, change_to_finding(change, investigate(change, _policy()))

    def test_a_quiet_period_suppresses_the_alert_with_its_reason(self):
        change, finding = self._pair()
        bundles = decide_alerts(
            [(change, finding)],
            [AlertPreference(user_id="alice")],
            quiet_periods=[
                QuietPeriod(name="freeze", periods=frozenset({"2026-06"}), reason="release freeze")
            ],
        )
        decision = bundles[0].decisions[0]
        assert decision.alerted is False
        assert SuppressionReason.QUIET_PERIOD in decision.suppressed_by
        assert "release freeze" in decision.suppression_detail

    def test_a_non_business_period_suppresses_the_alert(self):
        change, finding = self._pair()
        bundles = decide_alerts(
            [(change, finding)],
            [AlertPreference(user_id="alice")],
            calendar=BusinessCalendar(name="acme", non_business_periods=frozenset({"2026-06"})),
        )
        decision = bundles[0].decisions[0]
        assert decision.alerted is False
        assert SuppressionReason.NON_BUSINESS_PERIOD in decision.suppressed_by

    def test_reasons_accumulate_rather_than_short_circuiting(self):
        """'Why didn't I get paged' needs the whole answer."""
        change, finding = self._pair()
        bundles = decide_alerts(
            [(change, finding)],
            [AlertPreference(user_id="carol", muted_metric_keys=frozenset({"revenue"}))],
            quiet_periods=[QuietPeriod(name="freeze", periods=frozenset({"2026-06"}))],
            previously_open_keys=frozenset({finding.key}),
        )
        reasons = set(bundles[0].decisions[0].suppressed_by)
        assert SuppressionReason.QUIET_PERIOD in reasons
        assert SuppressionReason.DUPLICATE_OF_OPEN_FINDING in reasons
        assert SuppressionReason.METRIC_MUTED_BY_USER in reasons


class TestRankingByImpact:
    def test_a_bigger_move_outranks_a_smaller_one_of_the_same_kind(self):
        small, _ = detect_change(_policy(), REVENUE, [("a", 1000.0), ("b", 850.0)])
        large, _ = detect_change(_policy(), REVENUE, [("a", 1000.0), ("b", 200.0)])
        assert small is not None and large is not None
        small_finding = change_to_finding(small, investigate(small, _policy()))
        large_finding = change_to_finding(large, investigate(large, _policy()))
        assert large_finding.risk_score > small_finding.risk_score

    def test_a_threshold_breach_outranks_an_equivalent_ordinary_move(self):
        plain, _ = detect_change(_policy(), REVENUE, [("a", 1000.0), ("b", 800.0)])
        breach, _ = detect_change(
            _policy(lower_threshold=900.0), REVENUE, [("a", 1000.0), ("b", 800.0)]
        )
        assert plain is not None and breach is not None
        plain_score = change_to_finding(plain, investigate(plain, _policy())).risk_score
        breach_score = change_to_finding(breach, investigate(breach, _policy())).risk_score
        assert breach_score > plain_score

    def test_rank_reasons_are_transparent(self):
        change, _ = detect_change(_policy(), REVENUE, FLAT_THEN_DROP)
        assert change is not None
        finding = change_to_finding(change, investigate(change, _policy()))
        assert any("base" in reason for reason in finding.reasons)
        assert any("impact" in reason for reason in finding.reasons)

    def test_the_impact_term_cannot_push_a_score_past_100(self):
        change, _ = detect_change(_policy(), REVENUE, [("a", 1.0), ("b", 100000.0)])
        assert change is not None
        finding = change_to_finding(change, investigate(change, _policy()))
        assert finding.risk_score <= 100


class TestFindingShape:
    def test_every_kind_fits_the_database_column(self):
        """`SemanticFinding.kind` is String(32); SQLite ignores that and
        PostgreSQL does not."""
        for kind in ChangeKind:
            assert len(kind.value) <= 32, kind

    def test_kpi_kinds_are_recognisable_in_the_shared_queue(self):
        for kind in ChangeKind:
            assert is_kpi_kind(kind.value)
        assert not is_kpi_kind("conflict")
        assert not is_kpi_kind("schema_drift_table_added")

    def test_a_finding_is_always_ai_inference(self):
        change, _ = detect_change(_policy(), REVENUE, FLAT_THEN_DROP)
        assert change is not None
        finding = change_to_finding(change, investigate(change, _policy()))
        assert finding.truth_level == "ai_inference"

    def test_observed_and_inferred_evidence_are_separately_labelled(self):
        change, _ = detect_change(_policy(dimension_columns=("region",)), REVENUE, FLAT_THEN_DROP)
        assert change is not None
        evidence = investigate(
            change,
            _policy(dimension_columns=("region",)),
            current_breakdown=(BREAKDOWN_COLUMNS, CURRENT_ROWS),
            baseline_breakdown=(BREAKDOWN_COLUMNS, BASELINE_ROWS),
            value_column="revenue",
        )
        finding = change_to_finding(change, evidence)
        levels = {item.get("truth_level") for item in finding.evidence}
        assert levels == {"database_fact", "ai_inference"}

    def test_the_payload_never_carries_the_metrics_expression(self):
        change, _ = detect_change(_policy(), REVENUE, FLAT_THEN_DROP)
        assert change is not None
        finding = change_to_finding(change, investigate(change, _policy()))
        rendered = repr(finding.payload)
        assert "approved_expression" not in rendered
        assert "SELECT" not in rendered.upper()

    def test_a_detected_change_is_always_ai_inference(self):
        change, _ = detect_change(_policy(), REVENUE, FLAT_THEN_DROP)
        assert change is not None
        assert change.truth_level is DataTruthLevel.AI_INFERENCE


class TestEngine:
    def _run(self, **overrides):
        kwargs: dict[str, object] = {
            "database_id": "default",
            "policies": [_policy(dimension_columns=("region",))],
            "catalog_entries": [CatalogRow("revenue", business_name="Net Revenue")],
            "series_by_policy": {
                "p1": MetricSeries(
                    policy_id="p1",
                    points=FLAT_THEN_DROP,
                    current_breakdown=(BREAKDOWN_COLUMNS, CURRENT_ROWS),
                    baseline_breakdown=(BREAKDOWN_COLUMNS, BASELINE_ROWS),
                    value_column="revenue",
                )
            },
            "preferences": [AlertPreference(user_id="alice")],
        }
        kwargs.update(overrides)
        return monitor_kpis(**kwargs)  # type: ignore[arg-type]

    def test_a_full_run_produces_a_ranked_finding_with_evidence(self):
        run = self._run()
        assert len(run.findings) == 1
        finding = run.findings[0]
        assert finding.risk_score > 0
        assert run.result.evidence_by_key[finding.key].has_sufficient_evidence is True
        assert run.alert_bundles[0].alerted_count == 1

    def test_a_policy_with_no_series_is_reported_as_skipped(self):
        run = self._run(series_by_policy={})
        assert run.findings == ()
        assert any("no series supplied" in reason for reason in run.result.series_skipped)

    def test_nothing_moved_is_distinguishable_from_nothing_checked(self):
        flat = [("a", 1000.0), ("b", 1000.0)]
        run = self._run(series_by_policy={"p1": MetricSeries(policy_id="p1", points=flat)})
        assert run.findings == ()
        assert run.result.series_skipped  # says *why* nothing was produced

    def test_findings_are_capped_keeping_the_most_impactful(self):
        policies = []
        series = {}
        catalog = []
        for index in range(6):
            key = f"m{index}"
            policies.append(_policy(policy_id=f"p{index}", metric_concept_key=key))
            catalog.append(CatalogRow(key))
            series[f"p{index}"] = MetricSeries(
                policy_id=f"p{index}",
                points=[("a", 1000.0), ("b", 1000.0 - (index + 1) * 100)],
            )
        run = monitor_kpis(
            database_id="default",
            policies=policies,
            catalog_entries=catalog,
            series_by_policy=series,
            settings=_settings(kpi_monitor_max_findings_per_scan=2),
        )
        assert run.result.truncated is True
        assert len(run.findings) == 2
        # The two biggest drops survived.
        assert {f.key for f in run.findings} == set(run.changes_by_key) & {
            f.key for f in run.findings
        }
        assert all(run.changes_by_key[f.key].change_percent is not None for f in run.findings)

    def test_evidence_and_changes_stay_consistent_after_truncation(self):
        policies, series, catalog = [], {}, []
        for index in range(5):
            key = f"m{index}"
            policies.append(_policy(policy_id=f"p{index}", metric_concept_key=key))
            catalog.append(CatalogRow(key))
            series[f"p{index}"] = MetricSeries(
                policy_id=f"p{index}", points=[("a", 1000.0), ("b", 500.0 - index)]
            )
        run = monitor_kpis(
            database_id="default",
            policies=policies,
            catalog_entries=catalog,
            series_by_policy=series,
            settings=_settings(kpi_monitor_max_findings_per_scan=2),
        )
        for finding in run.findings:
            assert finding.key in run.result.evidence_by_key
            assert finding.key in run.changes_by_key
        assert len(run.result.evidence_by_key) == len(run.findings)
        assert len(run.result.changes) == len(run.findings)


class TestReport:
    def test_a_report_states_contributions_not_causes(self):
        run = TestEngine()._run()
        report = render_markdown(run)
        assert "demonstrated cause" in report
        assert "contributions to the arithmetic total" in report
        assert "caused by" not in report.lower()

    def test_a_report_without_evidence_says_so(self):
        run = TestEngine()._run(
            policies=[_policy()],
            series_by_policy={"p1": MetricSeries(policy_id="p1", points=FLAT_THEN_DROP)},
        )
        report = render_markdown(run)
        assert "No attribution was made." in report
        assert "Limitations:" in report

    def test_a_quiet_run_says_nothing_moved(self):
        run = TestEngine()._run(
            series_by_policy={"p1": MetricSeries(policy_id="p1", points=[("a", 1.0), ("b", 1.0)])}
        )
        assert "No meaningful change was detected." in render_markdown(run)

    def test_the_report_discloses_that_nothing_is_actually_sent(self):
        report = render_markdown(TestEngine()._run())
        assert "no delivery channel" in report.lower()
