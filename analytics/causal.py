"""The Advanced Root Cause Engine -- Prompt 37
(`37_ADVANCED_ROOT_CAUSE_ENGINE_CONTRACT.md`), upgrading Prompt 14's
single-level, contribution-percent-only `analytics.root_cause
.investigate_root_cause` into a hierarchical, evidence-driven
investigation.

**Reused, never reimplemented** (master-contract rule 2/3):
`investigate_root_cause` itself, `analytics.engine.group_and_sum_by_label`/
`group_values_by_label`, and every `analytics.models` type Prompt 14 already
defined (`RootCauseResult`/`ContributorStat`). This module adds a layer on
top of that engine rather than modifying it -- the one live caller of the
Prompt-14 function (`agent.multiagent.agents.root_cause_handler`'s default
path, see that module's own docstring) keeps its exact existing behavior
untouched; this module is a new, additive, opt-in entry point.

What this module adds, each directly answering one line of Prompt 37's
mission:

1. **Sample-size/confidence enforcement** (`StatisticalSignificance`,
   `_two_sample_significance`): every contributor Prompt 14's own
   contribution-percent bar already validated is re-checked against the
   *raw* per-row values underlying each period's total, using a Welch-style
   two-sample z-test (stdlib only -- `statistics.pvariance` for the
   within-group variance, `math.erf` for the normal CDF; no numpy/scipy,
   matching every other `analytics/` module's own "stdlib only" posture).
   A contributor whose underlying sample is smaller than
   `Settings.root_cause_min_sample_size` on either side never gets a
   p-value computed at all (`SignificanceVerdict.INSUFFICIENT_SAMPLE`) --
   the literal "enforce sample-size/confidence thresholds and return
   insufficient evidence when necessary" requirement.
2. **Distinguishing known causes from distractors** (`ContributorEvidence
   .is_distractor`): a contributor that clears Prompt 14's contribution-
   percent bar but fails this module's own significance check is a
   distractor -- numerically large, statistically unsupported -- reported
   in `HierarchicalRootCauseNode.distractors`, never promoted into a claim.
3. **Hierarchical investigation** (`HierarchicalRootCauseNode.children`):
   for a multi-level `dimension_hierarchy` (e.g. `[["Region"],
   ["Category"]]`), only a top-level contributor that is *statistically
   supported* (never a distractor) is drilled into at the next level, over
   the subset of rows matching that contributor's own label -- "only drill
   where there's already evidence" bounds the recursion to a depth of
   `Settings.root_cause_max_hierarchy_depth`, never a combinatorial
   expansion of every contributor at every level.
4. **Interaction testing** (`InteractionEffect`, `_detect_interactions`):
   when a hierarchy names exactly two single-column levels, this module
   runs the standard two-way, unweighted-means fixed-effects interaction
   decomposition (the textbook ANOVA interaction term) over the full
   observed `(label_a, label_b)` grid -- exactly `0` for a perfectly
   additive dataset, strictly non-zero whenever a specific combination
   moves differently than its two main effects alone would predict. A
   genuine interaction is only ever reported
   (`is_statistically_justified=True`) when that cell's own sample clears
   the significance bar *and* its residual clears `Settings
   .root_cause_interaction_min_percent`. A 3+-way interaction is out of
   scope (a disclosed, bounded choice, matching `RankingStat`'s own
   flattened-composite-label precedent for `MULTIDIMENSIONAL` results).
5. **Correlation, never causation** (`AdvancedRootCauseResult
   .causation_confirmed`/`.evidence_type`/`.causation_disclaimer`): every
   result this module produces is structurally incapable of claiming
   causation -- see `analytics.models.CausalEvidenceStrength`'s own
   docstring for why. The acceptance criterion "never present inference,
   simulation or prediction as observed fact" is enforced by construction
   here, not by convention: there is no field this module could set to
   assert a confirmed cause even if a future bug tried to.

**Pure, deterministic, no LLM, no I/O** -- the same posture every other
module under `analytics/` already establishes. A caller with no interest
in the hierarchy/interaction/significance upgrade keeps calling
`analytics.root_cause.investigate_root_cause` directly; nothing about that
function changed.
"""

from __future__ import annotations

import math
import statistics as _statistics

from analytics.engine import group_and_sum_by_label, group_values_by_label
from analytics.models import (
    CAUSAL_ENGINE_VERSION,
    AdvancedRootCauseResult,
    ContributorEvidence,
    ContributorStat,
    HierarchicalRootCauseNode,
    InteractionEffect,
    RootCauseResult,
    SignificanceVerdict,
    StatisticalSignificance,
)
from analytics.root_cause import investigate_root_cause
from config.settings import Settings, get_settings


def _standard_normal_cdf(x: float) -> float:
    """`math.erf`-based standard normal CDF -- exact (to float precision),
    not a lookup-table approximation; stdlib only."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _two_sample_significance(
    current_values: list[float], baseline_values: list[float], settings: Settings
) -> StatisticalSignificance:
    """A Welch-style two-sample z-test over the raw values in each group --
    see this module's own docstring, point 1, for the full reasoning.

    Returns `SignificanceVerdict.INSUFFICIENT_SAMPLE` (and no z-score/
    p-value at all) whenever either side has fewer than `Settings
    .root_cause_min_sample_size` values -- a genuinely small sample never
    gets a misleadingly confident p-value computed from it.
    """
    current_n = len(current_values)
    baseline_n = len(baseline_values)
    min_n = settings.root_cause_min_sample_size
    alpha = settings.root_cause_significance_alpha

    if current_n < min_n or baseline_n < min_n:
        return StatisticalSignificance(
            verdict=SignificanceVerdict.INSUFFICIENT_SAMPLE,
            current_n=current_n,
            baseline_n=baseline_n,
            current_mean=round(sum(current_values) / current_n, 4) if current_n else None,
            baseline_mean=round(sum(baseline_values) / baseline_n, 4) if baseline_n else None,
            alpha=alpha,
            min_sample_size=min_n,
        )

    current_mean = sum(current_values) / current_n
    baseline_mean = sum(baseline_values) / baseline_n
    # Population variance (ddof=0) -- the same convention
    # analytics.anomaly/analytics.engine already use (statistics.pvariance/
    # pstdev), kept consistent here rather than switching to the sample
    # variant for just this one module.
    current_var = _statistics.pvariance(current_values) if current_n > 1 else 0.0
    baseline_var = _statistics.pvariance(baseline_values) if baseline_n > 1 else 0.0
    standard_error = math.sqrt(current_var / current_n + baseline_var / baseline_n)

    if standard_error == 0:
        # Every value identical on both sides (including the degenerate
        # current_n == baseline_n == 1 case) -- there is no within-group
        # variance to compare a difference against. Treated as "not
        # significant" rather than computing a spurious infinite z-score.
        return StatisticalSignificance(
            verdict=SignificanceVerdict.NOT_SIGNIFICANT,
            current_n=current_n,
            baseline_n=baseline_n,
            current_mean=round(current_mean, 4),
            baseline_mean=round(baseline_mean, 4),
            z_score=0.0,
            p_value=1.0,
            alpha=alpha,
            min_sample_size=min_n,
        )

    z_score = (current_mean - baseline_mean) / standard_error
    p_value = 2 * (1 - _standard_normal_cdf(abs(z_score)))
    verdict = (
        SignificanceVerdict.SIGNIFICANT if p_value < alpha else SignificanceVerdict.NOT_SIGNIFICANT
    )
    return StatisticalSignificance(
        verdict=verdict,
        current_n=current_n,
        baseline_n=baseline_n,
        current_mean=round(current_mean, 4),
        baseline_mean=round(baseline_mean, 4),
        z_score=round(z_score, 4),
        p_value=round(p_value, 6),
        alpha=alpha,
        min_sample_size=min_n,
    )


def _evidence_for_contributors(
    contributors: tuple[ContributorStat, ...],
    current_values_by_label: dict[str, list[float]],
    baseline_values_by_label: dict[str, list[float]],
    settings: Settings,
) -> tuple[tuple[ContributorEvidence, ...], tuple[ContributorEvidence, ...]]:
    """Partitions `contributors` (already validated by `investigate_root_cause`'s
    own contribution-percent bar) into statistically supported evidence vs.
    distractors -- see point 2 of this module's own docstring."""
    supported: list[ContributorEvidence] = []
    distractors: list[ContributorEvidence] = []
    for contributor in contributors:
        significance = _two_sample_significance(
            current_values_by_label.get(contributor.label, []),
            baseline_values_by_label.get(contributor.label, []),
            settings,
        )
        is_distractor = significance.verdict != SignificanceVerdict.SIGNIFICANT
        evidence = ContributorEvidence(
            contributor=contributor, significance=significance, is_distractor=is_distractor
        )
        (distractors if is_distractor else supported).append(evidence)
    return tuple(supported), tuple(distractors)


def _rows_matching_label(
    columns: list[str], rows: list[tuple], label_indices: list[int], label: str
) -> list[tuple]:
    """Every row whose composite label (the identical `" / ".join(...)`
    convention `analytics.engine.group_and_sum_by_label` uses) equals
    `label` exactly -- the subset a hierarchical drill-down recurses into."""
    matching: list[tuple] = []
    for row in rows:
        if any(row[i] is None for i in label_indices):
            continue
        if " / ".join(str(row[i]) for i in label_indices) == label:
            matching.append(row)
    return matching


def _investigate_level(
    current_rows: list[tuple],
    baseline_rows: list[tuple],
    columns: list[str],
    dimension_hierarchy: list[list[str]],
    value_column: str,
    settings: Settings,
    depth: int,
    limitations: list[str],
) -> HierarchicalRootCauseNode:
    """One level of the hierarchy -- computes the base (Prompt 14) result,
    re-checks every one of its contributors for statistical significance,
    and, when further levels remain and the depth budget allows it,
    recurses into every statistically supported (non-distractor)
    contributor only."""
    dimension_columns = dimension_hierarchy[0]
    base_result = investigate_root_cause(
        current_rows, baseline_rows, columns, dimension_columns, value_column, settings
    )

    if not base_result.contributors:
        return HierarchicalRootCauseNode(
            depth=depth, dimension_columns=tuple(dimension_columns), base_result=base_result
        )

    value_idx = columns.index(value_column)
    dimension_indices = [columns.index(c) for c in dimension_columns]
    current_values_by_label = group_values_by_label(
        columns, current_rows, value_idx, dimension_indices
    )
    baseline_values_by_label = group_values_by_label(
        columns, baseline_rows, value_idx, dimension_indices
    )
    supported, distractors = _evidence_for_contributors(
        base_result.contributors, current_values_by_label, baseline_values_by_label, settings
    )
    if distractors:
        labels = ", ".join(e.label for e in distractors)
        limitations.append(
            f"depth {depth}: {len(distractors)} contributor(s) had a large contribution_percent "
            f"but were not statistically distinguishable from random variation ({labels}) -- "
            "reported as distractors, not evidence"
        )

    children: list[HierarchicalRootCauseNode] = []
    can_drill_deeper = (
        len(dimension_hierarchy) > 1 and depth + 1 < settings.root_cause_max_hierarchy_depth
    )
    if can_drill_deeper:
        for evidence in supported:
            child_current = _rows_matching_label(
                columns, current_rows, dimension_indices, evidence.label
            )
            child_baseline = _rows_matching_label(
                columns, baseline_rows, dimension_indices, evidence.label
            )
            if not child_current or not child_baseline:
                limitations.append(
                    f"depth {depth}: could not drill into {evidence.label!r} -- no rows for it "
                    "on one side at the next hierarchy level"
                )
                continue
            children.append(
                _investigate_level(
                    child_current,
                    child_baseline,
                    columns,
                    dimension_hierarchy[1:],
                    value_column,
                    settings,
                    depth + 1,
                    limitations,
                )
            )
    elif len(dimension_hierarchy) > 1:
        limitations.append(
            f"hierarchy depth capped at {settings.root_cause_max_hierarchy_depth} "
            f"(root_cause_max_hierarchy_depth) -- deeper levels were not evaluated"
        )

    return HierarchicalRootCauseNode(
        depth=depth,
        dimension_columns=tuple(dimension_columns),
        base_result=base_result,
        contributor_evidence=supported,
        distractors=distractors,
        children=tuple(children),
    )


def _detect_interactions(
    current_rows: list[tuple],
    baseline_rows: list[tuple],
    columns: list[str],
    dimension_a: str,
    dimension_b: str,
    value_column: str,
    settings: Settings,
    main_effect_a: RootCauseResult,
) -> tuple[tuple[InteractionEffect, ...], list[str]]:
    """Pairwise interaction test between exactly two single-column
    dimensions -- see point 4 of this module's own docstring.

    The interaction itself is the standard two-way, unweighted-means
    fixed-effects decomposition (the textbook ANOVA interaction term, not
    a bespoke heuristic): for each observed `(label_a, label_b)` cell,
    `predicted_additive = row_mean(label_a) + col_mean(label_b) -
    grand_mean`, and `interaction = actual_contribution - predicted_additive`.
    This is exactly `0` for a perfectly additive dataset (each cell's own
    change is just its row effect plus its column effect, nothing extra) --
    verified directly in `tests/test_analytics_causal.py`'s own additive-
    model regression test -- and strictly non-zero whenever a specific
    combination moves differently than its two main effects alone would
    predict. **Disclosed, bounded scope**: this unweighted-means formula
    assumes a reasonably complete grid of observed `(label_a, label_b)`
    combinations; a sparse/highly unbalanced grid makes `row_mean`/
    `col_mean` cruder approximations of each dimension's "true" main
    effect, the identical kind of disclosed approximation
    `analytics.anomaly`'s own rolling z-score (no detrending) already
    carries elsewhere in this package.

    `main_effect_a` is already-computed (the top-of-hierarchy
    `investigate_root_cause` call for `dimension_a` alone, reused rather
    than recomputed -- master-contract rule 2/3) -- used here only for the
    informational `main_effect_a_percent` field, not for the interaction
    math itself.
    """
    notes: list[str] = []
    if dimension_a == dimension_b:
        notes.append("interaction testing requires two distinct dimension columns")
        return (), notes
    if dimension_a not in columns or dimension_b not in columns or value_column not in columns:
        notes.append("interaction testing: a configured column was not found in the result set")
        return (), notes

    main_effect_b = investigate_root_cause(
        current_rows, baseline_rows, columns, [dimension_b], value_column, settings
    )
    joint = investigate_root_cause(
        current_rows, baseline_rows, columns, [dimension_a, dimension_b], value_column, settings
    )
    if not joint.contributors or not joint.magnitude:
        notes.append(
            "interaction testing: the joint dimension breakdown had no validated contributor"
        )
        return (), notes

    main_a_by_label = {c.label: c.contribution_percent for c in main_effect_a.contributors}
    main_b_by_label = {c.label: c.contribution_percent for c in main_effect_b.contributors}

    value_idx = columns.index(value_column)
    idx_a = columns.index(dimension_a)
    idx_b = columns.index(dimension_b)

    # The FULL joint grid (every observed cell, not just the ones that
    # cleared investigate_root_cause's own contribution-percent bar) --
    # the row/column means below need every cell to be a correct
    # decomposition, not just the "big enough to report" ones.
    current_joint_totals, _, _ = group_and_sum_by_label(
        columns, current_rows, value_idx, [idx_a, idx_b]
    )
    baseline_joint_totals, _, _ = group_and_sum_by_label(
        columns, baseline_rows, value_idx, [idx_a, idx_b]
    )
    cell_contribution: dict[tuple[str, str], float] = {}
    for label in set(current_joint_totals) | set(baseline_joint_totals):
        parts = label.split(" / ", 1)
        if len(parts) != 2:
            continue  # pragma: no cover -- built from exactly 2 dimension columns above
        cell_contribution[(parts[0], parts[1])] = current_joint_totals.get(
            label, 0.0
        ) - baseline_joint_totals.get(label, 0.0)

    if len(cell_contribution) < 2:
        notes.append("interaction testing needs at least two observed dimension-value combinations")
        return (), notes

    grand_mean = sum(cell_contribution.values()) / len(cell_contribution)
    a_values = {a for a, _ in cell_contribution}
    b_values = {b for _, b in cell_contribution}
    row_mean = {
        a: sum(v for (ra, _), v in cell_contribution.items() if ra == a)
        / sum(1 for (ra, _) in cell_contribution if ra == a)
        for a in a_values
    }
    col_mean = {
        b: sum(v for (_, rb), v in cell_contribution.items() if rb == b)
        / sum(1 for (_, rb) in cell_contribution if rb == b)
        for b in b_values
    }

    current_values_by_joint = group_values_by_label(
        columns, current_rows, value_idx, [idx_a, idx_b]
    )
    baseline_values_by_joint = group_values_by_label(
        columns, baseline_rows, value_idx, [idx_a, idx_b]
    )

    effects: list[InteractionEffect] = []
    for contributor in joint.contributors:
        parts = contributor.label.split(" / ", 1)
        if len(parts) != 2:
            continue  # pragma: no cover -- joint was built from exactly 2 dimension columns
        label_a, label_b = parts
        actual = cell_contribution.get((label_a, label_b), contributor.contribution)
        predicted_additive = row_mean[label_a] + col_mean[label_b] - grand_mean
        residual_percent = round(100 * (actual - predicted_additive) / joint.magnitude, 1)

        significance = _two_sample_significance(
            current_values_by_joint.get(contributor.label, []),
            baseline_values_by_joint.get(contributor.label, []),
            settings,
        )
        is_justified = (
            significance.verdict == SignificanceVerdict.SIGNIFICANT
            and abs(residual_percent) >= settings.root_cause_interaction_min_percent
        )
        if is_justified:
            rationale = (
                f"{label_a!r} x {label_b!r} together move {residual_percent:+.1f} percentage "
                "points differently than their independent (main) effects alone would predict -- "
                "statistically supported; treat as a genuine interaction, not two independent "
                "effects (still correlation, not confirmed causation)"
            )
        elif significance.verdict != SignificanceVerdict.SIGNIFICANT:
            rationale = (
                "sample too small or not statistically distinguishable from random variation -- "
                "no interaction claimed"
            )
        else:
            rationale = (
                "this combination's change is adequately explained by the two dimensions' own "
                "independent (main) effects -- no genuine interaction effect detected"
            )
        effects.append(
            InteractionEffect(
                dimension_a=dimension_a,
                dimension_b=dimension_b,
                label_a=label_a,
                label_b=label_b,
                joint_label=contributor.label,
                joint_contribution_percent=contributor.contribution_percent,
                main_effect_a_percent=main_a_by_label.get(label_a, 0.0),
                main_effect_b_percent=main_b_by_label.get(label_b, 0.0),
                interaction_residual_percent=residual_percent,
                sample_size=significance.current_n + significance.baseline_n,
                is_statistically_justified=is_justified,
                rationale=rationale,
            )
        )
    return tuple(effects), notes


def investigate_root_cause_advanced(
    current_rows: list[tuple],
    baseline_rows: list[tuple],
    columns: list[str],
    dimension_hierarchy: list[list[str]],
    value_column: str,
    settings: Settings | None = None,
) -> AdvancedRootCauseResult:
    """The advanced engine's single entry point.

    Args:
        current_rows: Rows for the period under investigation -- identical
            shape to `analytics.root_cause.investigate_root_cause`'s own
            parameter.
        baseline_rows: Rows for the comparison/baseline period.
        columns: Column names shared by both row sets.
        dimension_hierarchy: An ordered list of hierarchy levels, each a
            list of one or more dimension column names (e.g.
            `[["Region"], ["Category"]]` drills Region -> Category;
            `[["Region", "Channel"]]` is a single, flat, composite-label
            level, exactly like Prompt 14's own `dimension_columns`).
            Must be non-empty, and every level must itself be non-empty.
        value_column: Which column holds the metric under investigation.
        settings: Defaults to `config.settings.get_settings()`.

    Returns:
        An `AdvancedRootCauseResult` -- see that model's own docstring for
        every field's meaning. `has_sufficient_evidence` is `True` only
        when the top level has at least one statistically supported
        (non-distractor) contributor; `causation_confirmed` is always
        `False`.

    Raises:
        ValueError: `dimension_hierarchy` is empty, or one of its levels
            is an empty list -- a caller programming error (not a data
            condition), the same "fail loudly on a malformed call,
            fail soft on bad/insufficient data" split every function in
            this package already draws.
    """
    if not dimension_hierarchy or any(not level for level in dimension_hierarchy):
        raise ValueError(
            "dimension_hierarchy must be a non-empty list of non-empty dimension-column lists"
        )

    settings = settings or get_settings()
    limitations: list[str] = []

    root = _investigate_level(
        current_rows,
        baseline_rows,
        columns,
        dimension_hierarchy,
        value_column,
        settings,
        depth=0,
        limitations=limitations,
    )

    interactions: tuple[InteractionEffect, ...] = ()
    if len(dimension_hierarchy) >= 2:
        if len(dimension_hierarchy[0]) == 1 and len(dimension_hierarchy[1]) == 1:
            interactions, interaction_notes = _detect_interactions(
                current_rows,
                baseline_rows,
                columns,
                dimension_hierarchy[0][0],
                dimension_hierarchy[1][0],
                value_column,
                settings,
                main_effect_a=root.base_result,
            )
            limitations.extend(interaction_notes)
        else:
            limitations.append(
                "interaction testing only runs for exactly two single-column hierarchy levels; "
                "this hierarchy's first two levels were not both single-column, so no interaction "
                "test was attempted"
            )

    return AdvancedRootCauseResult(
        root=root,
        interactions=interactions,
        has_sufficient_evidence=bool(root.contributor_evidence),
        limitations=tuple(limitations),
        engine_version=CAUSAL_ENGINE_VERSION,
    )
