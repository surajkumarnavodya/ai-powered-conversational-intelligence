"""The Data Quality Command Center's detection/scoring engine -- Prompt 41
(`41_DATA_QUALITY_COMMAND_CENTER_CONTRACT.md`).

`assess_data_quality` is a pure function over already-fetched table data
(`TableQualityInput.columns`/`.rows` per table): it never queries a
database and never calls an LLM -- the identical "neither route ever
queries a database" posture `analytics.scenario`/`optimization`/
`analytics.predictive` already establish for their own engines (see each
module's own docstring).

**Reused, not reimplemented** (master-contract rule 2/3):
`analytics.engine.compute_analytics_result` supplies this engine's own
per-column null-count/total-count (COMPLETENESS) and z-score/IQR outlier
findings (VALIDITY) -- this module never recomputes either statistic
itself. `analytics.predictive.detect_feature_drift` supplies
DISTRIBUTION_STABILITY in full (a two-sample Kolmogorov-Smirnov test) --
this module only re-tags its typed result, it does not run a second
statistical test.

**Seven dimensions, each independently gated on a table actually
declaring a relevant check** -- a dimension with zero configured checks
for a table is never assessed (see `DimensionScore.score`'s own
docstring for why that's `None`, not a false `100`):

1. COMPLETENESS -- a column's null rate at/above `Settings
   .data_quality_null_spike_threshold` (or a per-column override in
   `TableQualityInput.null_rate_overrides`) is a null-spike finding.
   Runs automatically whenever `TableQualityInput.enable_column_statistics`
   is `True` (the default) -- no rule needs to be declared.
2. VALIDITY -- `DateColumnRule` (an unparseable or out-of-
   `[min_date, max_date]`-bound value), `CategoricalColumnRule` (a value
   outside the declared allowed set), and the outliers
   `analytics.engine` already found (reused, see above).
3. UNIQUENESS -- `TableQualityInput.unique_key_sets`: a composite key
   value (any row with a null component in the key is skipped, disclosed
   in the finding) appearing more than once.
4. CONSISTENCY -- `ConsistencyRule`: a declared `left OP right`
   comparison, evaluated per row (numeric or ISO-date/datetime values
   only -- a non-comparable or null-sided row is skipped and counted
   separately, never silently treated as a pass).
5. REFERENTIAL_INTEGRITY -- `ReferentialCheck`: a child column value not
   null and not present in the caller-supplied `parent_key_values` is an
   orphan.
6. FRESHNESS -- `FreshnessRule`: the table's own most recent
   `timestamp_column` value compared against the caller-supplied `as_of`
   reference time (never the server's wall clock -- see
   `data_quality.models.FreshnessRule`'s own docstring).
7. DISTRIBUTION_STABILITY -- `TableQualityInput.reference_columns`/
   `.reference_rows`/`.drift_columns`: reuses `analytics.predictive
   .detect_feature_drift` against the real, caller-supplied reference
   dataset.

**Authorization.** A finding whose `column` (or, for REFERENTIAL_INTEGRITY,
`child_column`) `config.sensitive_columns` classifies "restricted" is
suppressed outright for a caller lacking `Permission
.VIEW_RESTRICTED_COLUMNS` -- never partially redacted -- the identical
"suppress outright, never partially redacted" posture
`recommendation.engine`'s own restricted-column gate already establishes.
This never rejects the whole scan (unlike `optimization.validator`/
`analytics.predictive`, which reject a whole problem/model for touching a
restricted column): a data-quality scan is diagnostic, read-only work
over data the caller already has, so only the specific restricted
finding is withheld, disclosed via `DataQualityReport.insufficient_checks`
and audit-logged (`security.audit_log.log_security_event`,
`"data_quality_restricted_column_suppressed"`).
"""

from __future__ import annotations

import statistics as _statistics
from collections.abc import Sequence
from datetime import date, datetime
from typing import TypeVar

from analytics.engine import compute_analytics_result
from analytics.models import AnalyticsFindingKind
from analytics.predictive import detect_feature_drift

from agent.authz import Permission, has_role_permission
from agent.provenance import DataTruthLevel, ProvenancedClaim
from config.sensitive_columns import load_sensitive_columns
from config.settings import Settings, get_settings
from data_quality.models import (
    ConsistencyOperator,
    ConsistencyViolationDetail,
    DataQualityDimension,
    DataQualityFinding,
    DataQualityReport,
    DataQualitySeverity,
    DimensionScore,
    DistributionDriftDetail,
    DuplicateDetail,
    FreshnessDetail,
    InvalidValueDetail,
    NullSpikeDetail,
    OrphanDetail,
    OutlierDetail,
    TableQualityInput,
    TableQualityScore,
)
from security.audit_log import log_security_event

_SOURCE = "data_quality.engine.assess_data_quality"


def _restricted_column_names(settings: Settings) -> frozenset[str]:
    """Every column name (lowercased, table-agnostic) classified
    "restricted" anywhere in `config/sensitive_columns.yaml` -- the
    identical, independently re-derived helper `analytics.scenario`/
    `optimization.validator`/`analytics.predictive`/`recommendation.engine`
    each already establish for their own authorization gate."""
    del settings
    classifications = load_sensitive_columns()
    return frozenset(
        column.lower() for (_table, column), tier in classifications.items() if tier == "restricted"
    )


def _severity_for_rate(rate: float) -> DataQualitySeverity:
    """A fixed, disclosed tiering -- never a per-caller judgment call.
    `rate` is any already-normalized [0, 1] fraction (a null rate, a
    duplicate-row fraction, an orphan-row fraction, a violation-row
    fraction)."""
    if rate >= 0.5:
        return DataQualitySeverity.CRITICAL
    if rate >= 0.1:
        return DataQualitySeverity.WARNING
    return DataQualitySeverity.INFO


def _claim(text: str) -> ProvenancedClaim:
    return ProvenancedClaim(value=text, level=DataTruthLevel.DATABASE_FACT, source=_SOURCE)


def _parse_date(value: object) -> date | datetime | None:
    if isinstance(value, date | datetime):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _as_comparable(value: object) -> float | date | datetime | None:
    """Numeric or ISO-date/datetime only -- `ConsistencyRule`'s own
    disclosed, bounded comparison scope (see that model's own docstring)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    parsed = _parse_date(value)
    if parsed is not None:
        return parsed
    return None


_Comparable = TypeVar("_Comparable", float, datetime)


def _compare_ordered(operator: ConsistencyOperator, left: _Comparable, right: _Comparable) -> bool:
    if operator == ConsistencyOperator.LE:
        return left <= right
    if operator == ConsistencyOperator.LT:
        return left < right
    if operator == ConsistencyOperator.GE:
        return left >= right
    if operator == ConsistencyOperator.GT:
        return left > right
    if operator == ConsistencyOperator.EQ:
        return left == right
    return left != right


def _compare(
    operator: ConsistencyOperator, left: float | date | datetime, right: float | date | datetime
) -> bool | None:
    """A date and a plain number are never comparable -- `None` ("not
    comparable", skipped), never a crash or a silently-wrong pass. Both
    sides are normalized to the same concrete type (`float` or
    `datetime`) before comparing, one branch per type -- not a single
    shared comparison over the declared `float | date | datetime` union,
    which neither mypy nor a reader could verify is type-safe at the
    point of comparison. A bare `date` is promoted to a midnight
    `datetime` for the comparison only (never via `.timestamp()`, which
    would silently depend on the server's own local timezone -- the
    opposite of this engine's "pure function of its inputs" posture).
    """
    if isinstance(left, int | float) and isinstance(right, int | float):
        return _compare_ordered(operator, float(left), float(right))
    if isinstance(left, date | datetime) and isinstance(right, date | datetime):
        left_dt = left if isinstance(left, datetime) else datetime(left.year, left.month, left.day)
        right_dt = (
            right if isinstance(right, datetime) else datetime(right.year, right.month, right.day)
        )
        return _compare_ordered(operator, left_dt, right_dt)
    return None


def _column_index(columns: list[str], name: str) -> int | None:
    try:
        return columns.index(name)
    except ValueError:
        return None


def _is_restricted(column: str, restricted: frozenset[str]) -> bool:
    return column.lower() in restricted


def _completeness_and_outlier_findings(
    table_input: TableQualityInput,
    settings: Settings,
    restricted: frozenset[str],
    authorized: bool,
    insufficient: list[str],
) -> list[DataQualityFinding]:
    if not table_input.enable_column_statistics or not table_input.rows:
        return []
    analytics_result = compute_analytics_result(table_input.columns, table_input.rows, settings)
    findings: list[DataQualityFinding] = []
    for finding in analytics_result.findings:
        if (
            finding.kind == AnalyticsFindingKind.COLUMN_SUMMARY
            and finding.column_summary is not None
        ):
            stat = finding.column_summary
            if stat.count <= 0:
                continue
            if _is_restricted(stat.column, restricted) and not authorized:
                continue
            threshold = table_input.null_rate_overrides.get(
                stat.column, settings.data_quality_null_spike_threshold
            )
            null_rate = stat.null_count / stat.count
            if null_rate < threshold:
                continue
            findings.append(
                DataQualityFinding(
                    dimension=DataQualityDimension.COMPLETENESS,
                    severity=_severity_for_rate(null_rate),
                    table=table_input.table,
                    column=stat.column,
                    claim=_claim(
                        f"{table_input.table}.{stat.column} is {null_rate:.1%} null "
                        f"({stat.null_count} of {stat.count})."
                    ),
                    formula="null_rate = null_count / count; flagged at/above the configured threshold",
                    null_spike=NullSpikeDetail(
                        column=stat.column,
                        null_count=stat.null_count,
                        total_count=stat.count,
                        null_rate=round(null_rate, 4),
                        threshold=threshold,
                    ),
                )
            )
        elif finding.kind == AnalyticsFindingKind.OUTLIER and finding.outlier_detail is not None:
            detail = finding.outlier_detail
            if _is_restricted(detail.column, restricted) and not authorized:
                continue
            findings.append(
                DataQualityFinding(
                    dimension=DataQualityDimension.VALIDITY,
                    severity=DataQualitySeverity.WARNING,
                    table=table_input.table,
                    column=detail.column,
                    claim=_claim(
                        f"{table_input.table}.{detail.column}: {detail.label!r} is a statistical "
                        f"outlier ({detail.method})."
                    ),
                    formula=detail.formula,
                    outlier=OutlierDetail(
                        label=detail.label,
                        value=detail.value,
                        column=detail.column,
                        method=detail.method,
                        formula=detail.formula,
                    ),
                )
            )
    if not table_input.rows:
        insufficient.append(
            f"{table_input.table}: completeness/validity-outlier skipped -- no rows"
        )
    return findings


def _validity_rule_findings(
    table_input: TableQualityInput, restricted: frozenset[str], authorized: bool
) -> list[DataQualityFinding]:
    findings: list[DataQualityFinding] = []

    for date_rule in table_input.date_rules:
        if _is_restricted(date_rule.column, restricted) and not authorized:
            continue
        idx = _column_index(table_input.columns, date_rule.column)
        if idx is None:
            continue
        min_bound = _parse_date(date_rule.min_date) if date_rule.min_date else None
        max_bound = _parse_date(date_rule.max_date) if date_rule.max_date else None
        invalid_samples: list[str] = []
        out_of_range_samples: list[str] = []
        invalid_count = 0
        out_of_range_count = 0
        checked = 0
        for row in table_input.rows:
            value = row[idx]
            if value is None:
                continue
            checked += 1
            parsed = _parse_date(value)
            if parsed is None:
                invalid_count += 1
                if len(invalid_samples) < 10:
                    invalid_samples.append(str(value))
                continue
            comparable = parsed.date() if isinstance(parsed, datetime) else parsed
            if (
                min_bound is not None
                and comparable
                < (min_bound.date() if isinstance(min_bound, datetime) else min_bound)
            ) or (
                max_bound is not None
                and comparable
                > (max_bound.date() if isinstance(max_bound, datetime) else max_bound)
            ):
                out_of_range_count += 1
                if len(out_of_range_samples) < 10:
                    out_of_range_samples.append(str(value))
        if invalid_count:
            findings.append(
                DataQualityFinding(
                    dimension=DataQualityDimension.VALIDITY,
                    severity=_severity_for_rate(invalid_count / checked if checked else 0.0),
                    table=table_input.table,
                    column=date_rule.column,
                    claim=_claim(
                        f"{table_input.table}.{date_rule.column} has {invalid_count} unparseable "
                        f"date value(s) of {checked} checked."
                    ),
                    formula="invalid when datetime.date/datetime.fromisoformat both raise",
                    invalid_value=InvalidValueDetail(
                        column=date_rule.column,
                        reason="invalid_date",
                        sample_values=tuple(invalid_samples),
                        invalid_row_count=invalid_count,
                        checked_row_count=checked,
                    ),
                )
            )
        if out_of_range_count:
            findings.append(
                DataQualityFinding(
                    dimension=DataQualityDimension.VALIDITY,
                    severity=_severity_for_rate(out_of_range_count / checked if checked else 0.0),
                    table=table_input.table,
                    column=date_rule.column,
                    claim=_claim(
                        f"{table_input.table}.{date_rule.column} has {out_of_range_count} date value(s) "
                        f"outside [{date_rule.min_date}, {date_rule.max_date}] of {checked} checked."
                    ),
                    formula="out of range when value < min_date or value > max_date",
                    invalid_value=InvalidValueDetail(
                        column=date_rule.column,
                        reason="out_of_range_date",
                        sample_values=tuple(out_of_range_samples),
                        invalid_row_count=out_of_range_count,
                        checked_row_count=checked,
                    ),
                )
            )

    for category_rule in table_input.categorical_rules:
        if _is_restricted(category_rule.column, restricted) and not authorized:
            continue
        idx = _column_index(table_input.columns, category_rule.column)
        if idx is None:
            continue
        allowed = set(category_rule.allowed_values)
        samples: list[str] = []
        bad_count = 0
        checked = 0
        for row in table_input.rows:
            value = row[idx]
            if value is None:
                continue
            checked += 1
            if str(value) not in allowed:
                bad_count += 1
                if len(samples) < 10:
                    samples.append(str(value))
        if bad_count:
            findings.append(
                DataQualityFinding(
                    dimension=DataQualityDimension.VALIDITY,
                    severity=_severity_for_rate(bad_count / checked if checked else 0.0),
                    table=table_input.table,
                    column=category_rule.column,
                    claim=_claim(
                        f"{table_input.table}.{category_rule.column} has {bad_count} unexpected "
                        f"category value(s) of {checked} checked."
                    ),
                    formula="unexpected when str(value) not in the declared allowed_values set",
                    invalid_value=InvalidValueDetail(
                        column=category_rule.column,
                        reason="unexpected_category",
                        sample_values=tuple(samples),
                        invalid_row_count=bad_count,
                        checked_row_count=checked,
                    ),
                )
            )
    return findings


def _uniqueness_findings(
    table_input: TableQualityInput, settings: Settings, restricted: frozenset[str], authorized: bool
) -> list[DataQualityFinding]:
    findings: list[DataQualityFinding] = []
    for key_columns in table_input.unique_key_sets:
        if any(_is_restricted(c, restricted) for c in key_columns) and not authorized:
            continue
        indices = [_column_index(table_input.columns, c) for c in key_columns]
        if any(i is None for i in indices):
            continue
        counts: dict[tuple, int] = {}
        total = 0
        for row in table_input.rows:
            values = tuple(row[i] for i in indices)  # type: ignore[index]
            if any(v is None for v in values):
                continue
            total += 1
            counts[values] = counts.get(values, 0) + 1
        duplicate_keys = {k: n for k, n in counts.items() if n > 1}
        if not duplicate_keys:
            continue
        duplicate_row_count = sum(duplicate_keys.values())
        samples = [
            " / ".join(str(v) for v in key)
            for key in list(duplicate_keys)[: settings.data_quality_max_reported_samples]
        ]
        findings.append(
            DataQualityFinding(
                dimension=DataQualityDimension.UNIQUENESS,
                severity=_severity_for_rate(duplicate_row_count / total if total else 0.0),
                table=table_input.table,
                column=" / ".join(key_columns),
                claim=_claim(
                    f"{table_input.table} has {len(duplicate_keys)} duplicate key value(s) "
                    f"on ({', '.join(key_columns)}), covering {duplicate_row_count} row(s)."
                ),
                formula="a key is duplicate when its non-null composite value appears in more than one row",
                duplicate=DuplicateDetail(
                    key_columns=key_columns,
                    sample_duplicate_keys=tuple(samples),
                    distinct_duplicate_key_count=len(duplicate_keys),
                    duplicate_row_count=duplicate_row_count,
                    total_row_count=total,
                ),
            )
        )
    return findings


def _consistency_findings(
    table_input: TableQualityInput, restricted: frozenset[str], authorized: bool
) -> list[DataQualityFinding]:
    findings: list[DataQualityFinding] = []
    for rule in table_input.consistency_rules:
        if (
            _is_restricted(rule.left_column, restricted)
            or _is_restricted(rule.right_column, restricted)
        ) and not authorized:
            continue
        left_idx = _column_index(table_input.columns, rule.left_column)
        right_idx = _column_index(table_input.columns, rule.right_column)
        if left_idx is None or right_idx is None:
            continue
        violations = 0
        skipped = 0
        checked = 0
        for row in table_input.rows:
            left = _as_comparable(row[left_idx])
            right = _as_comparable(row[right_idx])
            if left is None or right is None:
                skipped += 1
                continue
            outcome = _compare(rule.operator, left, right)
            if outcome is None:
                skipped += 1
                continue
            checked += 1
            if not outcome:
                violations += 1
        if violations:
            findings.append(
                DataQualityFinding(
                    dimension=DataQualityDimension.CONSISTENCY,
                    severity=_severity_for_rate(violations / checked if checked else 0.0),
                    table=table_input.table,
                    column=f"{rule.left_column}, {rule.right_column}",
                    claim=_claim(
                        f"{table_input.table}: {violations} row(s) violate "
                        f"'{rule.left_column} {rule.operator.value} {rule.right_column}' "
                        f"({rule.name})."
                    ),
                    formula=f"violation when NOT ({rule.left_column} {rule.operator.value} {rule.right_column})",
                    consistency_violation=ConsistencyViolationDetail(
                        rule_name=rule.name,
                        left_column=rule.left_column,
                        operator=rule.operator.value,
                        right_column=rule.right_column,
                        violation_row_count=violations,
                        skipped_row_count=skipped,
                        checked_row_count=checked,
                    ),
                )
            )
    return findings


def _referential_findings(
    table_input: TableQualityInput, settings: Settings, restricted: frozenset[str], authorized: bool
) -> list[DataQualityFinding]:
    findings: list[DataQualityFinding] = []
    for check in table_input.referential_checks:
        if _is_restricted(check.child_column, restricted) and not authorized:
            continue
        idx = _column_index(table_input.columns, check.child_column)
        if idx is None:
            continue
        parent_values = set(check.parent_key_values)
        orphan_values: dict[str, int] = {}
        checked = 0
        for row in table_input.rows:
            value = row[idx]
            if value is None:
                continue
            checked += 1
            text = str(value)
            if text not in parent_values:
                orphan_values[text] = orphan_values.get(text, 0) + 1
        if not orphan_values:
            continue
        orphan_row_count = sum(orphan_values.values())
        samples = tuple(list(orphan_values)[: settings.data_quality_max_reported_samples])
        findings.append(
            DataQualityFinding(
                dimension=DataQualityDimension.REFERENTIAL_INTEGRITY,
                severity=_severity_for_rate(orphan_row_count / checked if checked else 0.0),
                table=table_input.table,
                column=check.child_column,
                claim=_claim(
                    f"{table_input.table}.{check.child_column} has {orphan_row_count} orphan row(s) "
                    f"({len(orphan_values)} distinct value(s)) with no match in {check.parent_table}."
                ),
                formula="orphan when a non-null child value is absent from parent_key_values",
                orphan=OrphanDetail(
                    child_column=check.child_column,
                    parent_table=check.parent_table,
                    sample_orphan_values=samples,
                    distinct_orphan_value_count=len(orphan_values),
                    orphan_row_count=orphan_row_count,
                    checked_row_count=checked,
                ),
            )
        )
    return findings


def _freshness_findings(
    table_input: TableQualityInput, settings: Settings, restricted: frozenset[str], authorized: bool
) -> list[DataQualityFinding]:
    rule = table_input.freshness_rule
    if rule is None:
        return []
    if _is_restricted(rule.timestamp_column, restricted) and not authorized:
        return []
    idx = _column_index(table_input.columns, rule.timestamp_column)
    if idx is None:
        return []
    as_of = _parse_date(rule.as_of)
    if as_of is None:
        return []
    as_of_dt = (
        as_of if isinstance(as_of, datetime) else datetime(as_of.year, as_of.month, as_of.day)
    )
    max_staleness = (
        rule.max_staleness_seconds or settings.data_quality_default_max_staleness_seconds
    )

    latest: datetime | None = None
    for row in table_input.rows:
        parsed = _parse_date(row[idx])
        if parsed is None:
            continue
        parsed_dt = (
            parsed
            if isinstance(parsed, datetime)
            else datetime(parsed.year, parsed.month, parsed.day)
        )
        if latest is None or parsed_dt > latest:
            latest = parsed_dt

    if latest is None:
        return [
            DataQualityFinding(
                dimension=DataQualityDimension.FRESHNESS,
                severity=DataQualitySeverity.WARNING,
                table=table_input.table,
                column=rule.timestamp_column,
                claim=_claim(
                    f"{table_input.table}.{rule.timestamp_column} has no parseable value -- "
                    "freshness could not be assessed."
                ),
                formula="staleness_seconds = (as_of - max(timestamp_column)).total_seconds()",
                freshness=FreshnessDetail(
                    timestamp_column=rule.timestamp_column,
                    latest_value=None,
                    as_of=rule.as_of,
                    staleness_seconds=None,
                    max_staleness_seconds=max_staleness,
                ),
            )
        ]

    staleness_seconds = (as_of_dt - latest).total_seconds()
    if staleness_seconds <= max_staleness:
        return []
    return [
        DataQualityFinding(
            dimension=DataQualityDimension.FRESHNESS,
            severity=_severity_for_rate(min(1.0, staleness_seconds / (2 * max_staleness))),
            table=table_input.table,
            column=rule.timestamp_column,
            claim=_claim(
                f"{table_input.table}.{rule.timestamp_column}'s most recent value is "
                f"{staleness_seconds:,.0f}s old as of {rule.as_of} (threshold {max_staleness}s)."
            ),
            formula="staleness_seconds = (as_of - max(timestamp_column)).total_seconds()",
            freshness=FreshnessDetail(
                timestamp_column=rule.timestamp_column,
                latest_value=str(latest),
                as_of=rule.as_of,
                staleness_seconds=round(staleness_seconds, 1),
                max_staleness_seconds=max_staleness,
            ),
        )
    ]


def _distribution_stability_findings(
    table_input: TableQualityInput, settings: Settings, restricted: frozenset[str], authorized: bool
) -> list[DataQualityFinding]:
    if not table_input.drift_columns or not table_input.reference_rows or not table_input.rows:
        return []
    columns_to_check = tuple(
        c for c in table_input.drift_columns if authorized or not _is_restricted(c, restricted)
    )
    if not columns_to_check:
        return []
    report = detect_feature_drift(
        table_input.reference_columns,
        table_input.reference_rows,
        table_input.columns,
        table_input.rows,
        list(columns_to_check),
        settings,
    )
    findings: list[DataQualityFinding] = []
    for item in report.findings:
        if not item.is_drifted:
            continue
        findings.append(
            DataQualityFinding(
                dimension=DataQualityDimension.DISTRIBUTION_STABILITY,
                severity=DataQualitySeverity.WARNING,
                table=table_input.table,
                column=item.feature,
                claim=_claim(
                    f"{table_input.table}.{item.feature}'s distribution has shifted versus the "
                    f"reference dataset (KS p={item.p_value:.4f} < {report.threshold:g})."
                ),
                formula=item.formula,
                distribution_drift=DistributionDriftDetail(
                    feature=item.feature,
                    reference_count=item.reference_count,
                    new_count=item.new_count,
                    ks_statistic=item.ks_statistic,
                    p_value=item.p_value,
                    threshold=report.threshold,
                ),
            )
        )
    return findings


_DIMENSION_FORMULAS: dict[DataQualityDimension, str] = {
    DataQualityDimension.COMPLETENESS: "100 - 100 * max(null_rate) across flagged columns",
    DataQualityDimension.VALIDITY: "100 - 100 * mean(invalid_row_count / checked_row_count) across findings",
    DataQualityDimension.UNIQUENESS: "100 - 100 * max(duplicate_row_count / total_row_count) across key sets",
    DataQualityDimension.CONSISTENCY: "100 - 100 * mean(violation_row_count / checked_row_count) across rules",
    DataQualityDimension.REFERENTIAL_INTEGRITY: (
        "100 - 100 * max(orphan_row_count / checked_row_count) across checks"
    ),
    DataQualityDimension.FRESHNESS: "100 - 100 * min(1, staleness_seconds / (2 * max_staleness_seconds))",
    DataQualityDimension.DISTRIBUTION_STABILITY: "100 - (15 * count of drifted features), floored at 0",
}


def _dimension_was_checked(table_input: TableQualityInput, dimension: DataQualityDimension) -> bool:
    if dimension == DataQualityDimension.COMPLETENESS:
        return table_input.enable_column_statistics and bool(table_input.rows)
    if dimension == DataQualityDimension.VALIDITY:
        return (
            bool(table_input.date_rules)
            or bool(table_input.categorical_rules)
            or (table_input.enable_column_statistics and bool(table_input.rows))
        )
    if dimension == DataQualityDimension.UNIQUENESS:
        return bool(table_input.unique_key_sets)
    if dimension == DataQualityDimension.CONSISTENCY:
        return bool(table_input.consistency_rules)
    if dimension == DataQualityDimension.REFERENTIAL_INTEGRITY:
        return bool(table_input.referential_checks)
    if dimension == DataQualityDimension.FRESHNESS:
        return table_input.freshness_rule is not None
    return bool(table_input.drift_columns) and bool(table_input.reference_rows)


def _score_dimension(
    dimension: DataQualityDimension, findings: list[DataQualityFinding]
) -> DimensionScore:
    dimension_findings = [f for f in findings if f.dimension == dimension]
    penalty = 0.0
    if dimension_findings:
        if dimension == DataQualityDimension.COMPLETENESS:
            penalty = 100 * max(f.null_spike.null_rate for f in dimension_findings if f.null_spike)
        elif dimension == DataQualityDimension.VALIDITY:
            rates = [
                f.invalid_value.invalid_row_count / f.invalid_value.checked_row_count
                for f in dimension_findings
                if f.invalid_value is not None and f.invalid_value.checked_row_count
            ]
            # Outlier findings carry no rate -- a fixed, disclosed per-finding
            # penalty instead, bounded the same way distribution-stability's
            # own per-feature penalty is.
            outlier_count = sum(1 for f in dimension_findings if f.outlier is not None)
            rate_penalty = 100 * (_statistics.mean(rates)) if rates else 0.0
            penalty = min(100.0, rate_penalty + 5 * outlier_count)
        elif dimension == DataQualityDimension.UNIQUENESS:
            penalty = 100 * max(
                f.duplicate.duplicate_row_count / f.duplicate.total_row_count
                for f in dimension_findings
                if f.duplicate is not None and f.duplicate.total_row_count
            )
        elif dimension == DataQualityDimension.CONSISTENCY:
            rates = [
                f.consistency_violation.violation_row_count
                / f.consistency_violation.checked_row_count
                for f in dimension_findings
                if f.consistency_violation is not None and f.consistency_violation.checked_row_count
            ]
            penalty = 100 * _statistics.mean(rates) if rates else 0.0
        elif dimension == DataQualityDimension.REFERENTIAL_INTEGRITY:
            penalty = 100 * max(
                f.orphan.orphan_row_count / f.orphan.checked_row_count
                for f in dimension_findings
                if f.orphan is not None and f.orphan.checked_row_count
            )
        elif dimension == DataQualityDimension.FRESHNESS:
            ratios = [
                min(1.0, f.freshness.staleness_seconds / (2 * f.freshness.max_staleness_seconds))
                for f in dimension_findings
                if f.freshness is not None and f.freshness.staleness_seconds is not None
            ]
            penalty = 100 * max(ratios) if ratios else 50.0
        else:
            penalty = min(100.0, 15 * len(dimension_findings))

    score = round(max(0.0, 100.0 - penalty), 1)
    return DimensionScore(
        dimension=dimension,
        score=score,
        checks_run=1,
        findings_count=len(dimension_findings),
        formula=_DIMENSION_FORMULAS[dimension],
    )


def assess_table_quality(
    table_input: TableQualityInput,
    caller_roles: tuple[str, ...] = (),
    settings: Settings | None = None,
) -> TableQualityScore:
    """Assesses one table against every check its own `TableQualityInput`
    declares (plus the always-on COMPLETENESS/VALIDITY-outlier reuse of
    `analytics.engine.compute_analytics_result`). See this module's own
    docstring for the full per-dimension design.
    """
    settings = settings or get_settings()
    restricted = _restricted_column_names(settings)
    authorized = has_role_permission(caller_roles, Permission.VIEW_RESTRICTED_COLUMNS)
    insufficient: list[str] = []

    findings: list[DataQualityFinding] = []
    findings.extend(
        _completeness_and_outlier_findings(
            table_input, settings, restricted, authorized, insufficient
        )
    )
    findings.extend(_validity_rule_findings(table_input, restricted, authorized))
    findings.extend(_uniqueness_findings(table_input, settings, restricted, authorized))
    findings.extend(_consistency_findings(table_input, restricted, authorized))
    findings.extend(_referential_findings(table_input, settings, restricted, authorized))
    findings.extend(_freshness_findings(table_input, settings, restricted, authorized))
    findings.extend(_distribution_stability_findings(table_input, settings, restricted, authorized))

    if not authorized:
        suppressed_columns = sorted(
            {
                c
                for c in [*table_input.columns, *table_input.drift_columns]
                if _is_restricted(c, restricted)
            }
        )
        if suppressed_columns:
            insufficient.append(
                f"{table_input.table}: findings for restricted column(s) "
                f"({', '.join(suppressed_columns)}) suppressed -- caller lacks "
                "Permission.VIEW_RESTRICTED_COLUMNS"
            )
            log_security_event(
                "data_quality_restricted_column_suppressed",
                "info",
                "Data-quality findings referencing a restricted column were suppressed for a "
                "caller lacking VIEW_RESTRICTED_COLUMNS.",
                table=table_input.table,
                columns=suppressed_columns,
            )

    dimension_scores: list[DimensionScore] = []
    for dimension in DataQualityDimension:
        if not _dimension_was_checked(table_input, dimension):
            dimension_scores.append(
                DimensionScore(
                    dimension=dimension,
                    score=None,
                    checks_run=0,
                    findings_count=0,
                    formula=_DIMENSION_FORMULAS[dimension],
                )
            )
            continue
        dimension_scores.append(_score_dimension(dimension, findings))

    assessed_scores = [d.score for d in dimension_scores if d.score is not None]
    overall = round(_statistics.mean(assessed_scores), 1) if assessed_scores else None

    return TableQualityScore(
        table=table_input.table,
        row_count=len(table_input.rows),
        dimension_scores=tuple(dimension_scores),
        findings=tuple(findings),
        overall_score=overall,
        insufficient_checks=tuple(insufficient),
    )


def assess_data_quality(
    tables: Sequence[TableQualityInput],
    caller_roles: tuple[str, ...] = (),
    settings: Settings | None = None,
) -> DataQualityReport:
    """The engine's single entry point: assesses every table in `tables`
    independently and rolls the result up into one `DataQualityReport`.

    Args:
        tables: Every table to assess, already fetched by the caller --
            this function never queries a database.
        caller_roles: The roles of whoever is asking -- used only for the
            restricted-column suppression described in this module's own
            docstring.
        settings: Defaults to `config.settings.get_settings()`.
    """
    settings = settings or get_settings()
    table_scores = [assess_table_quality(t, caller_roles, settings) for t in tables]
    all_findings = tuple(f for score in table_scores for f in score.findings)
    all_insufficient = tuple(note for score in table_scores for note in score.insufficient_checks)
    table_overall = [
        score.overall_score for score in table_scores if score.overall_score is not None
    ]
    overall = round(_statistics.mean(table_overall), 1) if table_overall else None

    return DataQualityReport(
        tables=tuple(table_scores),
        overall_score=overall,
        findings=all_findings,
        insufficient_checks=all_insufficient,
    )


__all__ = ["assess_table_quality", "assess_data_quality"]
