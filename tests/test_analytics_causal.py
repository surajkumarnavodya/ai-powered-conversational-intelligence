"""Unit tests for `analytics/causal.py` -- Prompt 37
(`37_ADVANCED_ROOT_CAUSE_ENGINE_CONTRACT.md`)'s hierarchical, evidence-
driven root-cause upgrade over Prompt 14's `analytics.root_cause
.investigate_root_cause`.

Covers, matching the prompt's own explicit testing requirement ("known
causes, distractors, no-cause and insufficient-data cases"):

- a known cause (a single, statistically significant, sufficiently
  sampled contributor),
- a distractor (a contribution large enough to clear Prompt 14's own
  threshold, but with too small a sample to be statistically supported),
- no cause (a broadly distributed change, reusing the identical fixture
  `tests/test_analytics_root_cause.py`'s own insufficient-evidence test
  uses),
- insufficient data (empty rows),
- hierarchical drill-down (only a statistically supported contributor is
  drilled into, over the real subset of its own rows),
- hierarchy depth capping,
- interaction detection: a genuinely additive dataset (zero interaction,
  verified exactly) vs. a single-combination-only dataset (a real,
  statistically justified interaction),
- the structural correlation-vs-causation guarantees
  (`causation_confirmed`/`evidence_type`/`causation_disclaimer`), and
- that `investigate_root_cause` (Prompt 14) is reused verbatim, not
  reimplemented.

All example numbers in this file were independently verified by hand and
cross-checked by directly exercising the module before being written down
here (see the session's own working notes) -- this is not "whatever the
code happened to produce."
"""

from __future__ import annotations

from analytics.causal import investigate_root_cause_advanced
from analytics.models import CausalEvidenceStrength, SignificanceVerdict
from analytics.root_cause import investigate_root_cause

from config.settings import Settings

_SETTINGS = Settings(
    root_cause_min_contribution_percent=10.0,
    root_cause_min_sample_size=3,
    root_cause_significance_alpha=0.05,
    root_cause_max_hierarchy_depth=3,
    root_cause_interaction_min_percent=5.0,
)


class TestKnownCause:
    """A single, large, statistically significant, sufficiently sampled
    contributor -- the clear "known cause" case."""

    _COLUMNS = ["Region", "Revenue"]
    _BASELINE = [
        ("North", 100.0),
        ("North", 101.0),
        ("North", 99.0),
        ("North", 102.0),
        ("North", 98.0),
        ("North", 100.0),
        ("East", 100.0),
        ("East", 101.0),
        ("East", 99.0),
        ("South", 100.0),
        ("South", 101.0),
        ("South", 99.0),
    ]
    _CURRENT = [
        ("North", 400.0),
        ("North", 401.0),
        ("North", 399.0),
        ("North", 402.0),
        ("North", 398.0),
        ("North", 400.0),
        ("East", 100.0),
        ("East", 101.0),
        ("East", 99.0),
        ("South", 100.0),
        ("South", 101.0),
        ("South", 99.0),
    ]

    def test_the_dominant_contributor_is_reported_with_a_significant_verdict(self):
        result = investigate_root_cause_advanced(
            self._CURRENT, self._BASELINE, self._COLUMNS, [["Region"]], "Revenue", _SETTINGS
        )
        assert result.has_sufficient_evidence is True
        assert len(result.root.contributor_evidence) == 1
        evidence = result.root.contributor_evidence[0]
        assert evidence.contributor.label == "North"
        assert evidence.is_distractor is False
        assert evidence.significance.verdict == SignificanceVerdict.SIGNIFICANT
        assert evidence.significance.current_n == 6
        assert evidence.significance.baseline_n == 6
        assert evidence.significance.p_value is not None and evidence.significance.p_value < 0.05
        assert result.root.distractors == ()

    def test_reuses_prompt_14s_engine_verbatim_rather_than_reimplementing_it(self):
        """The literal "reused, never reimplemented" proof: the base-level
        `RootCauseResult` inside the hierarchy is byte-for-byte what a
        direct call to `investigate_root_cause` produces for the same
        inputs."""
        result = investigate_root_cause_advanced(
            self._CURRENT, self._BASELINE, self._COLUMNS, [["Region"]], "Revenue", _SETTINGS
        )
        direct = investigate_root_cause(
            self._CURRENT, self._BASELINE, self._COLUMNS, ["Region"], "Revenue", _SETTINGS
        )
        assert result.root.base_result == direct


class TestDistractor:
    """A contribution that clears Prompt 14's own percentage-of-magnitude
    bar, but whose underlying sample is too small to be statistically
    supported -- reported, never claimed."""

    _COLUMNS = ["Region", "Revenue"]
    # "Solid" barely moves (filler, keeps the magnitude denominator
    # meaningful); "Flaky" has exactly one row on each side and swings
    # hard -- numerically large, statistically unsupported.
    _BASELINE = [
        ("Solid", 100.0),
        ("Solid", 98.0),
        ("Solid", 102.0),
        ("Solid", 99.0),
        ("Solid", 101.0),
        ("Flaky", 10.0),
    ]
    _CURRENT = [
        ("Solid", 100.0),
        ("Solid", 98.0),
        ("Solid", 102.0),
        ("Solid", 99.0),
        ("Solid", 101.0),
        ("Flaky", 250.0),
    ]

    def test_large_raw_contribution_with_too_small_a_sample_is_a_distractor_not_evidence(self):
        result = investigate_root_cause_advanced(
            self._CURRENT, self._BASELINE, self._COLUMNS, [["Region"]], "Revenue", _SETTINGS
        )
        # Clears the base contribution-percent bar (it IS a ContributorStat)...
        assert any(c.label == "Flaky" for c in result.root.base_result.contributors)
        # ...but is not statistically supported, so the advanced engine
        # withholds it from contributor_evidence and never claims it.
        assert result.root.contributor_evidence == ()
        assert len(result.root.distractors) == 1
        distractor = result.root.distractors[0]
        assert distractor.contributor.label == "Flaky"
        assert distractor.is_distractor is True
        assert distractor.significance.verdict == SignificanceVerdict.INSUFFICIENT_SAMPLE
        # The literal "enforce sample-size/confidence thresholds and
        # return insufficient evidence when necessary" requirement.
        assert result.has_sufficient_evidence is False


class TestNoCause:
    """A broadly distributed change -- no single dimension value explains
    enough of it to even become a base-level contributor. Reuses the exact
    fixture `tests/test_analytics_root_cause.py`'s own insufficient-
    evidence test already established."""

    def test_broadly_distributed_change_has_no_contributor_at_all(self):
        columns = ["Region", "Revenue"]
        baseline = [(f"R{i}", 100.0) for i in range(12)]
        current = [(f"R{i}", 108.0) for i in range(12)]
        result = investigate_root_cause_advanced(
            current, baseline, columns, [["Region"]], "Revenue", _SETTINGS
        )
        assert result.has_sufficient_evidence is False
        assert result.root.contributor_evidence == ()
        assert result.root.distractors == ()
        assert result.root.base_result.contributors == ()


class TestInsufficientData:
    def test_empty_current_rows_is_insufficient_evidence_not_an_exception(self):
        result = investigate_root_cause_advanced(
            [], [("West", 100.0)], ["Region", "Revenue"], [["Region"]], "Revenue", _SETTINGS
        )
        assert result.has_sufficient_evidence is False
        assert "nothing to compare" in " ".join(result.root.base_result.limitations)

    def test_empty_baseline_rows_is_insufficient_evidence_not_an_exception(self):
        result = investigate_root_cause_advanced(
            [("West", 100.0)], [], ["Region", "Revenue"], [["Region"]], "Revenue", _SETTINGS
        )
        assert result.has_sufficient_evidence is False

    def test_malformed_hierarchy_raises_value_error_rather_than_a_misleading_result(self):
        import pytest

        with pytest.raises(ValueError):
            investigate_root_cause_advanced(
                [("West", 100.0)], [("West", 90.0)], ["Region", "Revenue"], [], "Revenue", _SETTINGS
            )
        with pytest.raises(ValueError):
            investigate_root_cause_advanced(
                [("West", 100.0)],
                [("West", 90.0)],
                ["Region", "Revenue"],
                [[]],
                "Revenue",
                _SETTINGS,
            )


class TestHierarchicalDrillDown:
    """Region -> Category: only the statistically supported top-level
    contributor (North) is drilled into, over its own rows only, and the
    real driver (Electronics) is isolated at the next level."""

    _COLUMNS = ["Region", "Category", "Revenue"]
    _BASELINE = [
        ("North", "Electronics", 100.0),
        ("North", "Electronics", 98.0),
        ("North", "Electronics", 102.0),
        ("North", "Clothing", 100.0),
        ("North", "Clothing", 101.0),
        ("North", "Clothing", 99.0),
        ("South", "Electronics", 100.0),
        ("South", "Electronics", 98.0),
        ("South", "Electronics", 102.0),
        ("South", "Clothing", 100.0),
        ("South", "Clothing", 101.0),
        ("South", "Clothing", 99.0),
    ]
    _CURRENT = [
        ("North", "Electronics", 500.0),
        ("North", "Electronics", 498.0),
        ("North", "Electronics", 502.0),
        ("North", "Clothing", 100.0),
        ("North", "Clothing", 101.0),
        ("North", "Clothing", 99.0),
        ("South", "Electronics", 100.0),
        ("South", "Electronics", 98.0),
        ("South", "Electronics", 102.0),
        ("South", "Clothing", 100.0),
        ("South", "Clothing", 101.0),
        ("South", "Clothing", 99.0),
    ]

    def test_only_the_significant_top_level_contributor_is_drilled_into(self):
        result = investigate_root_cause_advanced(
            self._CURRENT,
            self._BASELINE,
            self._COLUMNS,
            [["Region"], ["Category"]],
            "Revenue",
            _SETTINGS,
        )
        assert result.has_sufficient_evidence is True
        # South never even clears the base contribution-percent bar (its
        # own baseline/current totals are unchanged) -- it is not a
        # contributor at all, let alone drilled into.
        assert {c.contributor.label for c in result.root.contributor_evidence} == {"North"}
        assert len(result.root.children) == 1

        child = result.root.children[0]
        assert child.depth == 1
        assert child.dimension_columns == ("Category",)
        assert {c.contributor.label for c in child.contributor_evidence} == {"Electronics"}
        # Clothing never moved within North either -- not even a candidate.
        assert child.distractors == ()

    def test_hierarchy_depth_is_capped_and_disclosed(self):
        columns = ["Region", "Category", "SubCategory", "Revenue"]
        baseline = [
            ("North", "Electronics", "Phones", 100.0),
            ("North", "Electronics", "Phones", 98.0),
            ("North", "Electronics", "Phones", 102.0),
            ("South", "Electronics", "Phones", 100.0),
            ("South", "Electronics", "Phones", 98.0),
            ("South", "Electronics", "Phones", 102.0),
        ]
        current = [
            ("North", "Electronics", "Phones", 500.0),
            ("North", "Electronics", "Phones", 498.0),
            ("North", "Electronics", "Phones", 502.0),
            ("South", "Electronics", "Phones", 100.0),
            ("South", "Electronics", "Phones", 98.0),
            ("South", "Electronics", "Phones", 102.0),
        ]
        capped_settings = Settings(
            root_cause_min_sample_size=3,
            root_cause_min_contribution_percent=10.0,
            root_cause_max_hierarchy_depth=2,
        )
        result = investigate_root_cause_advanced(
            current,
            baseline,
            columns,
            [["Region"], ["Category"], ["SubCategory"]],
            "Revenue",
            capped_settings,
        )
        # depth 0 (Region) -> depth 1 (Category) is allowed; depth 1 -> 2
        # (SubCategory) is not, and the cap is disclosed, not silent.
        assert result.root.depth == 0
        assert [c.depth for c in result.root.children] == [1]
        assert result.root.children[0].children == ()
        assert any("depth capped at 2" in note for note in result.limitations)


class TestInteractionDetection:
    """Exactly two single-column hierarchy levels -- the pairwise
    interaction test."""

    _COLUMNS = ["Region", "Channel", "Revenue"]

    @staticmethod
    def _cell(region: str, channel: str, shift: float) -> tuple[list[tuple], list[tuple]]:
        pattern = [99.0, 100.0, 101.0]
        baseline_rows = [(region, channel, v) for v in pattern]
        current_rows = [(region, channel, v + shift) for v in pattern]
        return baseline_rows, current_rows

    def test_a_perfectly_additive_dataset_has_exactly_zero_interaction(self):
        """Each cell's own change is exactly its region effect plus its
        channel effect, nothing extra -- the textbook "no interaction"
        case, and the engine must report it as exactly 0, not merely
        "small"."""
        b1, c1 = self._cell("North", "Online", 35.0)  # region=20, channel=15
        b2, c2 = self._cell("North", "Store", 20.0)  # region=20, channel=0
        b3, c3 = self._cell("South", "Online", 15.0)  # region=0, channel=15
        b4, c4 = self._cell("South", "Store", 0.0)  # region=0, channel=0
        baseline = b1 + b2 + b3 + b4
        current = c1 + c2 + c3 + c4

        result = investigate_root_cause_advanced(
            current, baseline, self._COLUMNS, [["Region"], ["Channel"]], "Revenue", _SETTINGS
        )
        assert result.interactions  # at least one joint cell cleared the base bar
        for interaction in result.interactions:
            assert interaction.interaction_residual_percent == 0.0
            assert interaction.is_statistically_justified is False
        assert result.limitations == ()

    def test_a_single_combination_driving_everything_is_a_genuine_statistically_justified_interaction(
        self,
    ):
        """Only North x Online moves; every other combination (including
        North alone and Online alone, taken over the *other* dimension's
        values) is flat -- a real, synergy-only effect the two single-
        dimension main effects cannot explain on their own."""
        b1, c1 = self._cell("North", "Online", 400.0)
        b2, c2 = self._cell("North", "Store", 0.0)
        b3, c3 = self._cell("South", "Online", 0.0)
        b4, c4 = self._cell("South", "Store", 0.0)
        baseline = b1 + b2 + b3 + b4
        current = c1 + c2 + c3 + c4

        result = investigate_root_cause_advanced(
            current, baseline, self._COLUMNS, [["Region"], ["Channel"]], "Revenue", _SETTINGS
        )
        assert len(result.interactions) == 1
        interaction = result.interactions[0]
        assert interaction.label_a == "North"
        assert interaction.label_b == "Online"
        assert interaction.interaction_residual_percent == 25.0
        assert interaction.is_statistically_justified is True
        assert interaction.sample_size == 6
        assert "interaction" in interaction.rationale.lower()
        assert "causation" in interaction.rationale.lower()

    def test_interaction_testing_only_runs_for_exactly_two_single_column_levels(self):
        result = investigate_root_cause_advanced(
            [("North", "Online", "Phone", 100.0)],
            [("North", "Online", "Phone", 90.0)],
            ["Region", "Channel", "Product", "Revenue"],
            [["Region"], ["Channel", "Product"]],
            "Revenue",
            _SETTINGS,
        )
        assert result.interactions == ()
        assert any("single-column" in note for note in result.limitations)

    def test_an_insufficient_sample_interaction_is_not_justified_even_with_a_large_residual(self):
        """Mirrors the distractor case, for interactions: one row per cell
        on each side is below root_cause_min_sample_size, so even a huge
        residual is never claimed as a justified interaction."""
        tiny_settings = Settings(
            root_cause_min_sample_size=3, root_cause_min_contribution_percent=10.0
        )
        baseline = [
            ("North", "Online", 10.0),
            ("North", "Store", 10.0),
            ("South", "Online", 10.0),
            ("South", "Store", 10.0),
        ]
        current = [
            ("North", "Online", 500.0),
            ("North", "Store", 10.0),
            ("South", "Online", 10.0),
            ("South", "Store", 10.0),
        ]
        result = investigate_root_cause_advanced(
            current, baseline, self._COLUMNS, [["Region"], ["Channel"]], "Revenue", tiny_settings
        )
        assert len(result.interactions) == 1
        assert result.interactions[0].is_statistically_justified is False


class TestCorrelationNeverCausation:
    """The structural guarantee: nothing this engine produces can ever
    assert a confirmed cause."""

    def test_causation_confirmed_is_always_false_and_the_disclaimer_is_always_present(self):
        columns = ["Region", "Revenue"]
        baseline = [("West", 100.0)]
        current = [("West", 200.0)]
        result = investigate_root_cause_advanced(
            current, baseline, columns, [["Region"]], "Revenue", _SETTINGS
        )
        assert result.causation_confirmed is False
        assert result.evidence_type == CausalEvidenceStrength.CORRELATION_ONLY
        assert "not" in result.causation_disclaimer.lower()
        assert "correlation" in result.causation_disclaimer.lower()

    def test_a_known_cause_result_still_only_ever_claims_correlation(self):
        result = investigate_root_cause_advanced(
            TestKnownCause._CURRENT,
            TestKnownCause._BASELINE,
            TestKnownCause._COLUMNS,
            [["Region"]],
            "Revenue",
            _SETTINGS,
        )
        assert result.has_sufficient_evidence is True
        assert result.causation_confirmed is False
        assert result.truth_level.value == "ai_inference"
