"""Typed shapes for one query result's analytics findings.

Every `AnalyticsFinding` this module's own default provider produces
carries `claim.level == DataTruthLevel.DATABASE_FACT` -- nothing here
involves an LLM; a finding is a typed, human-readable rendering of a
statistic `agent.insight.summarize_result` already computed
deterministically. See `analytics/provider.py` for the adapter that
builds these.

**Prompt 13** (`13_ANALYTICAL_RESULT_ENGINE_CONTRACT.md`) extended this
module additively -- every pre-existing field/enum value/construction
call keeps working unchanged (verified by re-running
`tests/test_analytics_models.py`/`test_analytics_provider.py`
unmodified): `ResultShape` (what kind of result these findings are about),
five new `AnalyticsFindingKind` values and their typed sub-stats
(`MedianStat`/`PercentileStat`/`DistinctCountStat`/`GrowthStat`/
`RankingStat`, each carrying its own `formula: str` -- "record formula ...
metadata" per that prompt's own requirement), a richer `OutlierFinding`
(distinct from the legacy single-method `outlier: OutlierStat` field --
see its own docstring for why two outlier shapes coexist), and
`AnalyticsResult.shape`/`engine_version`/`insufficient_data_reasons`.
`analytics/engine.py` is the actual computation that produces these new
shapes; this module stays pure data contracts, same split as before.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.insight import OutlierStat, TrendStat
from agent.provenance import DataTruthLevel, ProvenancedClaim

#: The typed-contract schema version for everything `analytics.engine`
#: produces -- stamped onto every `AnalyticsResult.engine_version` (Prompt
#: 13's own "record ... version metadata" requirement, master-contract
#: rule 11). Lives here, not in `analytics/engine.py`, so the one engine
#: module can import it from its own contracts module rather than the
#: reverse (avoiding a circular import) -- bump this when a computed
#: finding's formula/shape changes in a way a consumer should be able to
#: detect.
ANALYTICS_ENGINE_VERSION = "1.0.0"

#: The typed-contract schema version for everything `analytics.forecasting`
#: produces -- Prompt 16 (`16_FORECASTING_CONTRACT.md`). Kept separate from
#: `ANALYTICS_ENGINE_VERSION` above (own module, own versioning axis),
#: mirroring `VISUALIZATION_ENGINE_VERSION`'s identical precedent in
#: `analytics/visualization.py`.
FORECASTING_ENGINE_VERSION = "1.0.0"


class ResultShape(str, Enum):
    """What shape of query result a set of findings is about -- Prompt 13's
    own classification (`analytics.engine.classify_result_shape`), a
    closed set like `AnalyticsFindingKind` below.

    Attributes:
        SCALAR: A single row (any number of columns) -- one data point,
            nothing to rank/trend against.
        TIME_SERIES: Multiple rows, a single label/value pair whose label
            column is period-shaped (a year, a year-month, or an ISO
            date) for a majority of rows.
        CATEGORICAL_AGGREGATE: Multiple rows, a single label/value pair
            whose label column is not period-shaped (e.g. "revenue by
            product category").
        MULTIDIMENSIONAL: Multiple rows, a value column plus two or more
            non-numeric/dimension columns (e.g. "revenue by category and
            region") -- ranked via a flattened composite label, not true
            N-way pivot/cube analysis (a disclosed, bounded scope).
        RAW_TABLE: No usable label/value pair exists (e.g. every column
            is numeric, or every column is non-numeric) -- only
            per-column stats are computed, nothing row-level.
        EMPTY: Zero rows.
    """

    SCALAR = "scalar"
    TIME_SERIES = "time_series"
    CATEGORICAL_AGGREGATE = "categorical_aggregate"
    MULTIDIMENSIONAL = "multidimensional"
    RAW_TABLE = "raw_table"
    EMPTY = "empty"


class AnalyticsFindingKind(str, Enum):
    """What kind of statistical finding one `AnalyticsFinding` reports.

    A closed set, not a free-form string, mirroring
    `retrieval.models.ChunkType`'s own "deliberately closed" rationale --
    a caller (a future UI trend badge, an eval script) can exhaustively
    switch on this instead of string-matching.

    `MEDIAN`/`PERCENTILE`/`DISTINCT_COUNT`/`GROWTH`/`RANKING` (Prompt 13)
    are additive -- only ever produced by `analytics.engine
    .compute_analytics_result`, never by the pre-existing
    `ResultSummaryAnalyticsProvider`.
    """

    TREND = "trend"
    OUTLIER = "outlier"
    VARIANCE = "variance"
    MEDIAN = "median"
    PERCENTILE = "percentile"
    DISTINCT_COUNT = "distinct_count"
    GROWTH = "growth"
    RANKING = "ranking"
    COLUMN_SUMMARY = "column_summary"
    #: Prompt 14 (14_ANOMALY_ROOT_CAUSE_CONTRACT.md) -- only ever produced
    #: by `analytics.anomaly.detect_anomalies` (via `analytics.engine
    #: .compute_analytics_result`'s TIME_SERIES branch), one finding per
    #: flagged point -- see `AnomalyPoint`'s own docstring.
    ANOMALY = "anomaly"


class ColumnSummaryStat(BaseModel):
    """One column's basic shape -- row count, null count, and (for a
    numeric column) min/max/mean. The remaining per-column statistics
    (median, percentiles, distinct count, variance/stddev) each get their
    own dedicated finding/kind above/below rather than being folded in
    here, matching how `VARIANCE` already stood alone as its own kind
    before this prompt."""

    model_config = ConfigDict(frozen=True)

    column: str
    count: int
    null_count: int
    is_numeric: bool
    minimum: float | None = None
    maximum: float | None = None
    mean: float | None = None


class VarianceStat(BaseModel):
    """One column's variance/stddev/coefficient-of-variation, typed --
    unlike the legacy `VARIANCE` kind's `variance_column: str` field
    (text-only, see `AnalyticsFinding`'s own docstring), this new-engine
    counterpart carries the real numbers, matching every other Prompt-13
    finding's own typed shape."""

    model_config = ConfigDict(frozen=True)

    column: str
    variance: float
    stddev: float
    coefficient_of_variation: float | None = None
    formula: str = "population variance/stddev (statistics.pvariance/pstdev); CoV = stddev / |mean|"


class MedianStat(BaseModel):
    """One column's median -- `agent.insight.ColumnStat` has no median
    field at all; this closes that gap without touching the pinned
    module (see this package's own module docstring)."""

    model_config = ConfigDict(frozen=True)

    column: str
    median: float
    formula: str = "statistics.median(non-null numeric values)"


class PercentileStat(BaseModel):
    """One column's percentile breakdown -- `statistics.quantiles(...,
    n=100, method="inclusive")`, stdlib, no new dependency. `p50` is the
    same value `MedianStat.median` would report for the same column
    (kept as two separate findings/kinds anyway, matching how `median`
    and `mean`/`mode` are conventionally reported as distinct named
    statistics, not because the numbers themselves differ)."""

    model_config = ConfigDict(frozen=True)

    column: str
    p25: float
    p50: float
    p75: float
    p90: float
    p95: float
    p99: float
    formula: str = 'statistics.quantiles(non-null numeric values, n=100, method="inclusive")'


class DistinctCountStat(BaseModel):
    """One column's distinct-value count -- including a *numeric* column,
    a real gap `agent.insight.ColumnStat.distinct_count` has today (only
    ever set for non-numeric columns there)."""

    model_config = ConfigDict(frozen=True)

    column: str
    distinct_count: int


class GrowthPoint(BaseModel):
    """One period's value in a `GrowthStat.points` series.

    `change_percent_from_previous` is `None` for the series' first point
    (nothing to compare against) and also `None` when the *previous*
    point's value was exactly `0` (a percentage change from zero is
    undefined, the identical zero-denominator guard
    `agent.insight._compute_trend` already established) -- never a
    division-by-zero, never a misleading "infinite growth" claim.
    """

    model_config = ConfigDict(frozen=True)

    period: str
    value: float
    change_percent_from_previous: float | None = None


class GrowthStat(BaseModel):
    """A full period-by-period growth series -- `TIME_SERIES` shapes only.

    Unlike `agent.insight.TrendStat` (first-vs-last only), this is every
    period in the series. `missing_periods` is populated only when the
    label column matched one of the three recognized period patterns
    (4-digit year, `YYYY-MM`, ISO date) -- see `analytics.engine`'s own
    docstring for the recognized shapes and why an unrecognized label
    column still gets `points` (row-order-based, mirroring `TrendStat`'s
    own documented convention) just without gap detection.
    """

    model_config = ConfigDict(frozen=True)

    label_column: str
    value_column: str
    points: tuple[GrowthPoint, ...]
    overall_change_percent: float | None = None
    direction: Literal["up", "down", "flat"] | None = None
    missing_periods: tuple[str, ...] = ()
    formula: str = "change_percent = 100 * (current - previous) / abs(previous)"


class RankingEntry(BaseModel):
    """One label's rank within a `RankingStat`."""

    model_config = ConfigDict(frozen=True)

    label: str
    value: float
    rank: int
    share_percent: float | None = None


class RankingStat(BaseModel):
    """A full, ranked breakdown of every distinct label's total --
    `CATEGORICAL_AGGREGATE`/`MULTIDIMENSIONAL` shapes only. `share_percent`
    on each entry is also this result's "distribution" -- a distribution
    is the same ranked breakdown viewed by its share-of-total percentages,
    not a second, separately-computed structure (avoids computing and
    carrying the same underlying totals twice).

    For a `MULTIDIMENSIONAL` result, `label` is a flattened composite of
    every dimension column's value for that row (e.g. `"Bikes / West"`) --
    a disclosed, bounded approach, not true N-way pivot/cube analysis.
    """

    model_config = ConfigDict(frozen=True)

    label_column: str
    value_column: str
    entries: tuple[RankingEntry, ...]
    #: True when the real distinct-label count exceeded
    #: `Settings.analytics_ranking_max_entries` and `entries` was capped
    #: to the top N by value -- so a caller can tell "this is everything"
    #: from "this is the top N of more."
    truncated: bool = False
    formula: str = "share_percent = 100 * value / sum(all values); ranked descending by value"


class OutlierFinding(BaseModel):
    """One label flagged as a statistical outlier by the new engine --
    deliberately a **separate** shape from `agent.insight.OutlierStat`
    (kept as the `outlier` field below, unchanged, for the pre-existing
    `ResultSummaryAnalyticsProvider`): that one only ever reports a
    single z-score-based finding; this one is produced by
    `analytics.engine.compute_analytics_result`, which checks **both**
    z-score and IQR methods and tags which one flagged each label (a
    label can be flagged by one, the other, or both -- each becomes its
    own `OutlierFinding`).
    """

    model_config = ConfigDict(frozen=True)

    label: str
    value: float
    column: str
    method: Literal["zscore", "iqr"]
    #: Set only for `method == "zscore"`.
    deviations_from_mean: float | None = None
    #: Set only for `method == "iqr"` -- how far outside the
    #: `[Q1 - 1.5*IQR, Q3 + 1.5*IQR]` fence this value fell.
    iqr_distance: float | None = None
    formula: str


class AnomalyMethod(str, Enum):
    """Which of `analytics.anomaly.detect_anomalies`'s five independent
    detection methods flagged a point -- a closed set, like every other
    enum in this module. See that module's own docstring for the exact
    algorithm/configuration-flag per method.
    """

    THRESHOLD = "threshold"
    PERCENT_CHANGE = "percent_change"
    ROLLING_ZSCORE = "rolling_zscore"
    IQR = "iqr"
    SEASONAL = "seasonal"


class AnomalySignal(BaseModel):
    """One method's own evidence that a point is anomalous -- a point can
    carry several of these at once (one per method that flagged it),
    never collapsed into a single undifferentiated "is this anomalous"
    boolean."""

    model_config = ConfigDict(frozen=True)

    method: AnomalyMethod
    baseline_value: float
    actual_value: float
    #: A z-score, a percent change, or an absolute delta, depending on
    #: `method` -- always the number that was actually compared against
    #: `threshold_used` to decide whether this signal fires.
    deviation: float
    threshold_used: float
    formula: str


class AnomalyPoint(BaseModel):
    """One chronological point `analytics.anomaly.detect_anomalies`
    flagged -- carries every `AnomalySignal` that fired for it (always
    non-empty: a point only ever becomes an `AnomalyPoint` because at
    least one method flagged it)."""

    model_config = ConfigDict(frozen=True)

    period: str
    value: float
    signals: tuple[AnomalySignal, ...]


class AnomalyDetectionResult(BaseModel):
    """`analytics.anomaly.detect_anomalies`'s own return shape -- the
    direct result of scanning one chronological series, before any of its
    `anomalies` are folded into `AnalyticsFinding`s by `analytics.engine
    .compute_analytics_result`'s `TIME_SERIES` branch (which is the
    *only* live caller of `detect_anomalies` in this codebase today; a
    future caller with its own series, outside that one integration
    point, can use this function/result directly).

    Attributes:
        anomalies: Every flagged point, in series order -- empty (never
            `None`) when nothing was flagged, the same "always iterable"
            convention every other plural field in this module already
            uses.
        methods_evaluated: Which of the five `AnomalyMethod` values
            actually ran at least once (had enough history/configuration
            to evaluate anything) -- distinct from which methods *fired*
            (see each `AnomalyPoint.signals`).
        insufficient_data_reasons: Every method that was skipped
            entirely and why (e.g. `"iqr: fewer than 4 points"`,
            `"seasonal: no prior occurrence at lag 12"`) -- inspectable,
            not a silent omission.
        engine_version: `ANALYTICS_ENGINE_VERSION` at computation time.
    """

    model_config = ConfigDict(frozen=True)

    anomalies: tuple[AnomalyPoint, ...] = ()
    methods_evaluated: tuple[AnomalyMethod, ...] = ()
    insufficient_data_reasons: tuple[str, ...] = ()
    engine_version: str = ANALYTICS_ENGINE_VERSION


class AnalyticsFinding(BaseModel):
    """One statistical finding about a query result, already computed and
    already true of the data -- never an LLM's own claim about it.

    Exactly one of the per-kind fields below is set, matching `kind`.
    Modeled as one class with optional per-kind fields (rather than one
    class per kind) so `AnalyticsResult.findings` can be a single
    homogeneous tuple a caller iterates without an `isinstance` check per
    element -- `kind` alone is enough to know which field to read.
    """

    model_config = ConfigDict(frozen=True)

    kind: AnalyticsFindingKind
    #: A short, deterministic, human-readable rendering of this finding
    #: (e.g. "Sales rose 12.4% from 2011 to 2014."). Always
    #: `DataTruthLevel.DATABASE_FACT` for this module's own default
    #: provider -- see this module's own docstring.
    claim: ProvenancedClaim
    trend: TrendStat | None = None
    #: Legacy single-method outlier shape -- only ever set by the
    #: pre-existing `ResultSummaryAnalyticsProvider`. See `OutlierFinding`
    #: above for the new engine's own, richer shape.
    outlier: OutlierStat | None = None
    #: Set only when `kind == VARIANCE` -- the column this finding is about.
    #: Not a full `ColumnStat` (that would duplicate `stddev`/
    #: `coefficient_of_variation`, which already live in `claim.value`'s
    #: rendering) -- just enough to let a caller group variance findings by
    #: column without re-parsing `claim.value`. Only ever set by the
    #: legacy `ResultSummaryAnalyticsProvider`; the new engine's own
    #: `kind == VARIANCE` findings set `variance` (typed) instead.
    variance_column: str | None = None
    # --- Prompt 13 additions (13_ANALYTICAL_RESULT_ENGINE_CONTRACT.md) ---
    column_summary: ColumnSummaryStat | None = None
    variance: VarianceStat | None = None
    median: MedianStat | None = None
    percentile: PercentileStat | None = None
    distinct_count: DistinctCountStat | None = None
    growth: GrowthStat | None = None
    ranking: RankingStat | None = None
    #: The new engine's own outlier shape -- see `OutlierFinding`'s own
    #: docstring for why this is distinct from the legacy `outlier` field.
    outlier_detail: OutlierFinding | None = None
    #: Set only when `kind == ANOMALY` -- Prompt 14
    #: (14_ANOMALY_ROOT_CAUSE_CONTRACT.md).
    anomaly: AnomalyPoint | None = None


class AnalyticsResult(BaseModel):
    """The full set of analytics findings for one query result.

    Attributes:
        row_count: The result's row count, carried alongside `findings` so
            a caller can distinguish "genuinely nothing to find" (e.g. a
            single-row aggregate) from "findings were computed but empty."
        findings: Every finding this result produced -- empty (never a
            sentinel `None`) when there was nothing to report, so a caller
            can always iterate this without a `None` check, the same
            convention `agent.insight.ResultSummary.outliers` already uses.
        shape: Which `ResultShape` these findings are about -- `None` for
            the pre-existing `ResultSummaryAnalyticsProvider` (it never
            classified a shape), set for every result
            `analytics.engine.compute_analytics_result` produces.
        engine_version: `ANALYTICS_ENGINE_VERSION` at the time these
            findings were computed (Prompt 13's "record ... version
            metadata" requirement) -- defaults to the current version, so
            the pre-existing adapter's own construction call needs no
            change to keep validating.
        insufficient_data_reasons: Every calculation `analytics.engine
            .compute_analytics_result` explicitly skipped and why (e.g.
            `"growth: fewer than 2 periods"`, `"outliers: fewer than 3
            categories"`) -- inspectable, not a silent `None`/omission.
            Always empty for the pre-existing adapter (it never skips
            anything explainable this way).
    """

    model_config = ConfigDict(frozen=True)

    row_count: int
    findings: tuple[AnalyticsFinding, ...] = ()
    shape: ResultShape | None = None
    engine_version: str = ANALYTICS_ENGINE_VERSION
    insufficient_data_reasons: tuple[str, ...] = ()


class ContributorStat(BaseModel):
    """One dimension value's contribution to the change between a
    baseline and a current period -- `analytics.root_cause
    .investigate_root_cause`'s per-contributor shape. Only ever appears
    inside `RootCauseResult.contributors` after clearing that function's
    own validation threshold (`Settings.root_cause_min_contribution_
    percent`) -- see `RootCauseResult.has_sufficient_evidence`'s own
    docstring for why a contributor that doesn't clear it is never
    constructed as one of these at all.
    """

    model_config = ConfigDict(frozen=True)

    label: str
    baseline_value: float
    current_value: float
    contribution: float
    contribution_percent: float
    rank: int


class RootCauseResult(BaseModel):
    """`analytics.root_cause.investigate_root_cause`'s full result --
    Prompt 14 (`14_ANOMALY_ROOT_CAUSE_CONTRACT.md`). Deliberately **not**
    wrapped in `AnalyticsFinding`/`AnalyticsResult`: this isn't a finding
    about one query result, it's a standalone investigation across two
    datasets (a current period and a baseline period) with its own
    shape -- forcing it through the single-dataset finding taxonomy would
    be a worse fit than its own small, clearly-named model.

    Attributes:
        has_sufficient_evidence: The literal acceptance-criterion switch
            -- `True` only when at least one contributor cleared
            `Settings.root_cause_min_contribution_percent`. `False`
            means `contributors` is empty and `confidence` is `None`,
            **by construction** (this function has no code path that
            populates either without this being `True` first) -- "no
            root cause is stated without supporting evidence."
        magnitude: `current_total - baseline_total` -- always populated
            (even when evidence is insufficient), so a caller can still
            show "here's what changed" without a confident "here's why."
        magnitude_percent: The same change, as a percentage of
            `baseline_total` -- `None` when `baseline_total` is `0` (a
            percentage change from zero is undefined, the identical
            guard `agent.insight._compute_trend` already established).
        baseline_total: Sum of the baseline period's value column.
        current_total: Sum of the current period's value column.
        contributors: Ranked, validated contributors only -- empty when
            `has_sufficient_evidence` is `False`.
        confidence: `None` when `has_sufficient_evidence` is `False`;
            otherwise the fraction of the total change the validated
            contributors collectively explain, `0.0`-`1.0`.
        limitations: Disclosed caveats -- e.g. how many contributors were
            excluded for not clearing the validation bar, or why evidence
            was insufficient at all. Always populated when `contributors`
            excludes anything found, never silently dropped.
        formula: How `confidence`/`contribution_percent` are computed.
        engine_version: `ANALYTICS_ENGINE_VERSION` at computation time.
    """

    model_config = ConfigDict(frozen=True)

    has_sufficient_evidence: bool
    magnitude: float | None = None
    magnitude_percent: float | None = None
    baseline_total: float | None = None
    current_total: float | None = None
    contributors: tuple[ContributorStat, ...] = ()
    confidence: float | None = None
    limitations: tuple[str, ...] = ()
    formula: str = (
        "contribution_percent = 100 * (current - baseline) / magnitude; "
        "confidence = min(1.0, sum(|contribution_percent| of validated contributors) / 100)"
    )
    engine_version: str = ANALYTICS_ENGINE_VERSION


class ForecastModelType(str, Enum):
    """Which deterministic baseline model produced (or, for `AUTO`, was
    asked to select) a `ForecastResult` -- Prompt 16
    (`16_FORECASTING_CONTRACT.md`). A closed set, like every other enum in
    this module. `AUTO` is a caller-facing *selection* only -- see
    `ForecastModelMetadata.model`'s own docstring for why it is never the
    value stored there once a forecast is actually produced.
    """

    AUTO = "auto"
    NAIVE = "naive"
    SEASONAL_NAIVE = "seasonal_naive"
    MOVING_AVERAGE = "moving_average"
    LINEAR_TREND = "linear_trend"
    SIMPLE_EXPONENTIAL_SMOOTHING = "simple_exponential_smoothing"


class ForecastPoint(BaseModel):
    """One future period's point forecast, with a prediction interval
    where the model supports one (see `ForecastModelMetadata
    .supports_interval`). `period` is synthesized by extrapolating the
    same recognized period pattern (`analytics.engine
    .classify_period_column`) the historical series itself matched --
    never guessed for an unrecognized label shape (that's a hard
    rejection instead, see `ForecastResult.status`).
    """

    model_config = ConfigDict(frozen=True)

    period: str
    forecast: float
    lower_bound: float | None = None
    upper_bound: float | None = None
    horizon_step: int


class ForecastEvaluation(BaseModel):
    """A deterministic backtest -- the last `Settings
    .forecast_backtest_holdout` historical points are held out, the model
    is fit on everything before them, and the forecast for those points is
    compared against their real, already-known values. `None` (via
    `ForecastResult.evaluation`) when there wasn't enough history for a
    holdout of that size -- disclosed in `ForecastResult.limitations`,
    never silently skipped.
    """

    model_config = ConfigDict(frozen=True)

    holdout_size: int
    mae: float
    rmse: float
    #: `None` when every held-out actual value was `0` (a percent error
    #: from zero is undefined -- the identical zero-denominator guard
    #: `agent.insight._compute_trend`/`analytics.engine._compute_growth_stat`
    #: already establish elsewhere in this codebase).
    mape: float | None = None
    formula: str = (
        "MAE = mean(|actual - forecast|); RMSE = sqrt(mean((actual - forecast)^2)); "
        "MAPE = 100 * mean(|actual - forecast| / |actual|) over non-zero actuals"
    )


class ForecastModelMetadata(BaseModel):
    """Which model actually produced a `ForecastResult`, with enough
    recorded to reproduce it -- Prompt 16's own "record model version,
    training window, parameters" requirement. Deliberately **no wall-clock
    timestamp** -- `analytics.forecasting.generate_forecast` is a pure
    function of its inputs, and keeping this metadata timestamp-free is
    what makes "the same input always produces byte-identical output"
    directly testable (see `tests/test_analytics_forecasting.py`'s own
    reproducibility tests).
    """

    model_config = ConfigDict(frozen=True)

    #: Never `ForecastModelType.AUTO` here -- once a forecast is actually
    #: produced, this always names the one concrete model that produced it
    #: (AUTO itself picked one via backtest comparison, see
    #: `ForecastResult.candidate_evaluations`).
    model: ForecastModelType
    version: str = FORECASTING_ENGINE_VERSION
    parameters: dict[str, float | int | str | None] = Field(default_factory=dict)
    training_window_start: str
    training_window_end: str
    training_point_count: int
    #: `"date"` is deliberately excluded -- `analytics.forecasting
    #: .generate_forecast` rejects a raw-ISO-date-labeled series outright
    #: (see that function's own docstring), since no reliable future-label
    #: cadence can be synthesized from the label shape alone. Only a
    #: produced (never rejected) `ForecastResult` ever constructs this
    #: metadata, so this Literal is exhaustive in practice, not aspirational.
    period_kind: Literal["year", "year_month"]
    supports_interval: bool = True
    confidence_level: float


class ForecastResult(BaseModel):
    """`analytics.forecasting.generate_forecast`'s full result -- Prompt 16
    (`16_FORECASTING_CONTRACT.md`). Deliberately **not** folded into
    `AnalyticsResult`/`AnalyticsFinding` (the same reasoning `RootCauseResult`
    above already gives): a forecast is a standalone extrapolation beyond
    one query result's own historical data, with its own status/rejection/
    evaluation shape, not one more finding *about* that historical data.

    Attributes:
        status: `"ok"` or `"rejected"` -- see `rejection_reasons`.
        rejection_reasons: Every reason a forecast was not produced (e.g.
            "fewer than 4 historical points", "the label column is not
            confidently chronological") -- always non-empty when
            `status == "rejected"`, always empty when `status == "ok"`.
        horizon: How many future periods were requested.
        model: The model actually used -- `None` when rejected.
        points: The forecast itself, one `ForecastPoint` per requested
            horizon step, in chronological order -- empty when rejected.
        evaluation: A backtest of `model` against recent held-out history,
            or `None` when there wasn't enough history for one (see
            `ForecastEvaluation`'s own docstring) -- never computed when
            `status == "rejected"`.
        candidate_evaluations: Every eligible model's own backtest,
            keyed by `ForecastModelType.value`, populated only when
            `ForecastModelType.AUTO` was requested (so a caller can see
            why `model` was picked) -- empty otherwise.
        limitations: Always non-empty for an `"ok"` result -- the literal
            "forecasts are explicitly represented as estimates with
            limitations" acceptance criterion. Disclosed caveats: no
            exogenous variables, assumes the historical pattern continues
            unchanged, prediction-interval validity depends on residuals
            being roughly stable, etc.
        truth_level: Always `DataTruthLevel.AI_INFERENCE` -- see
            `analytics.forecasting`'s own module docstring for why a
            deterministic (non-LLM) statistical extrapolation is still
            classified this way rather than as `DATABASE_FACT`.
        summary: A short, deterministic, human-readable sentence
            describing the forecast (or, for a rejected one, why) -- the
            same "always non-empty, grounded rendering" convention
            `AnalyticsFinding.claim.value` already establishes, kept as a
            plain string here rather than a full `ProvenancedClaim` since
            `truth_level` above already carries this result's one truth
            level for all of it.
        engine_version: `FORECASTING_ENGINE_VERSION` at computation time.
    """

    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "rejected"]
    rejection_reasons: tuple[str, ...] = ()
    horizon: int
    model: ForecastModelMetadata | None = None
    points: tuple[ForecastPoint, ...] = ()
    evaluation: ForecastEvaluation | None = None
    candidate_evaluations: dict[str, ForecastEvaluation] = Field(default_factory=dict)
    limitations: tuple[str, ...] = ()
    truth_level: DataTruthLevel = DataTruthLevel.AI_INFERENCE
    summary: str = ""
    engine_version: str = FORECASTING_ENGINE_VERSION


#: The typed-contract schema version for everything `analytics.causal`
#: produces -- Prompt 37 (`37_ADVANCED_ROOT_CAUSE_ENGINE_CONTRACT.md`). Kept
#: separate from `ANALYTICS_ENGINE_VERSION`/`FORECASTING_ENGINE_VERSION`
#: (own module, own versioning axis), the identical precedent each prior
#: engine module already set for itself.
CAUSAL_ENGINE_VERSION = "1.0.0"


class CausalEvidenceStrength(str, Enum):
    """How strong a causal claim `analytics.causal` is permitted to make.

    Deliberately a **singleton** enum today -- `CORRELATION_ONLY` is the
    only value any code path in this module can ever produce. This is the
    structural half of "distinguish correlation from confirmed causation":
    nothing in this codebase runs a controlled experiment, a randomized
    trial, or a counterfactual/instrumental-variable analysis, so nothing
    here is entitled to assert causation. A future module that *did* have
    one of those (e.g. a proper A/B-test reader) would add a second,
    genuinely distinct member (`CONFIRMED_CAUSATION` or similar) rather
    than this module quietly starting to set one -- see
    `AdvancedRootCauseResult.causation_confirmed`, which is hardcoded
    `Literal[False]` for the same reason.
    """

    CORRELATION_ONLY = "correlation_only"


#: Fixed, deterministic text every `AdvancedRootCauseResult` carries --
#: master-contract rule 10 ("never present inference, simulation or
#: prediction as observed fact") applied specifically to causal language.
#: Not an LLM-authored disclaimer (which could be prompt-injected or
#: inconsistently worded) -- a constant, so it is byte-identical on every
#: result and trivially greppable.
CAUSATION_DISCLAIMER = (
    "This is a statistical correlation/contribution analysis, not a controlled "
    "experiment. It identifies which dimension values moved together with the "
    "observed change and by how much, using only historical data already "
    "retrieved -- it does not rule out confounding variables, reverse "
    "causality, or coincidence. Treat every contributor below as 'associated "
    "with' the change, never as its confirmed cause."
)


class SignificanceVerdict(str, Enum):
    """`analytics.causal`'s own classification of one contributor's
    statistical support -- a closed set, like every other enum in this
    package.

    Attributes:
        SIGNIFICANT: The sample size on both sides cleared
            `Settings.root_cause_min_sample_size` and the two-sample test's
            p-value is below `Settings.root_cause_significance_alpha`.
        NOT_SIGNIFICANT: Enough sample to run the test, but the observed
            difference is not distinguishable from random variation at the
            configured significance level -- a genuine "we looked and found
            no statistical support," not a skip.
        INSUFFICIENT_SAMPLE: Fewer than `Settings.root_cause_min_sample_size`
            underlying rows on at least one side -- the test was not run at
            all (never a misleadingly confident p-value computed from too
            little data).
    """

    SIGNIFICANT = "significant"
    NOT_SIGNIFICANT = "not_significant"
    INSUFFICIENT_SAMPLE = "insufficient_sample"


class StatisticalSignificance(BaseModel):
    """One contributor's two-sample significance test -- a Welch-style
    z-test over the *raw* per-row values underlying each period's total
    (never just the two sums), using only Python's stdlib (`statistics`
    for variance, `math.erf` for the normal CDF -- no numpy/scipy, matching
    `analytics/`'s own established "stdlib only" posture).

    `verdict` is the literal mechanism behind "enforce sample-size/
    confidence thresholds and return insufficient evidence when
    necessary": a contributor whose raw dollar/row difference looks large
    but whose underlying sample is small or highly variable gets
    `INSUFFICIENT_SAMPLE`/`NOT_SIGNIFICANT`, not a false "significant."
    """

    model_config = ConfigDict(frozen=True)

    verdict: SignificanceVerdict
    current_n: int
    baseline_n: int
    current_mean: float | None = None
    baseline_mean: float | None = None
    #: `None` whenever `verdict == INSUFFICIENT_SAMPLE` (no test was run).
    z_score: float | None = None
    #: Two-sided p-value from the standard normal CDF -- `None` whenever
    #: `verdict == INSUFFICIENT_SAMPLE`.
    p_value: float | None = None
    alpha: float
    min_sample_size: int
    formula: str = (
        "z = (current_mean - baseline_mean) / sqrt(current_var/current_n + "
        "baseline_var/baseline_n) [Welch]; p = 2*(1 - Phi(|z|)) via math.erf; "
        "significant when current_n>=min_sample_size and baseline_n>=min_sample_size "
        "and p < alpha"
    )


class ContributorEvidence(BaseModel):
    """One dimension value's contribution, re-checked for statistical
    support -- `analytics.causal`'s own, stricter companion to
    `ContributorStat` (Prompt 14's own, unchanged, contribution-percent-only
    shape). Every `ContributorEvidence` wraps exactly one `ContributorStat`
    that already cleared `investigate_root_cause`'s own validation bar;
    `significance` is the *additional* sample-size/confidence check Prompt
    14 never ran.

    `is_distractor` is the literal "known causes, distractors, no-cause"
    distinction the engine is required to make: a contributor whose
    `contribution_percent` alone looks compelling but whose `significance
    .verdict` is not `SIGNIFICANT` is a distractor -- numerically large,
    statistically unsupported -- and is reported, never silently promoted
    to a claim.
    """

    model_config = ConfigDict(frozen=True)

    contributor: ContributorStat
    significance: StatisticalSignificance
    is_distractor: bool

    @property
    def label(self) -> str:
        return self.contributor.label


class InteractionEffect(BaseModel):
    """A pairwise interaction test between exactly two single-column
    dimensions -- "test relevant interactions where statistically
    justified." Computed only when a hierarchy names exactly two
    single-column levels (see `analytics.causal`'s own module docstring
    for the exact precondition); never attempted for a 3+-way combination
    (a disclosed, bounded scope, matching `RankingStat`'s own flattened-
    composite-label precedent rather than true N-way decomposition).

    `interaction_residual_percent` is the standard two-way, unweighted-
    means fixed-effects interaction term (the textbook ANOVA interaction,
    not a bespoke heuristic) -- `analytics.causal._detect_interactions`'s
    own docstring has the full formula and why it is exactly `0` for a
    perfectly additive dataset. `is_statistically_justified` requires both
    a sample size clearing the configured significance bar *and* a
    residual whose magnitude clears `Settings
    .root_cause_interaction_min_percent`, so a joint cell that is large
    only because its two main effects are each already large on their own
    (no genuine synergy) is correctly reported as not justified.
    """

    model_config = ConfigDict(frozen=True)

    dimension_a: str
    dimension_b: str
    label_a: str
    label_b: str
    joint_label: str
    joint_contribution_percent: float
    #: Informational only -- `0.0` when that single value showed no
    #: detectable main-effect contribution on its own (it never cleared
    #: the base contribution threshold alone in the single-dimension
    #: breakdown). Not an input to `interaction_residual_percent` below
    #: (that uses the full, unfiltered per-cell grid instead).
    main_effect_a_percent: float
    main_effect_b_percent: float
    interaction_residual_percent: float
    sample_size: int
    is_statistically_justified: bool
    rationale: str
    formula: str = (
        "interaction = cell_contribution - (row_mean(label_a) + col_mean(label_b) - grand_mean) "
        "[two-way, unweighted-means fixed-effects decomposition over the full observed grid]; "
        "reported as a percent of the overall magnitude; justified when the two-sample "
        "significance test is SIGNIFICANT and |residual_percent| >= interaction_min_percent"
    )


class HierarchicalRootCauseNode(BaseModel):
    """One level of a hierarchical root-cause investigation.

    `base_result` is `analytics.root_cause.investigate_root_cause`'s own,
    completely unmodified output for this level's dimension breakdown
    (Prompt 14 code, reused verbatim -- master-contract rule 2/3, never
    reimplemented). `contributor_evidence`/`distractors` partition that
    result's own `base_result.contributors` by statistical support (see
    `ContributorEvidence.is_distractor`). `children` holds one node per
    *statistically supported* (non-distractor) contributor that was drilled
    into at the next hierarchy level -- a distractor is never drilled into,
    which is what bounds the recursion to "only where there's already
    evidence" rather than a combinatorial expansion of every contributor at
    every level.
    """

    model_config = ConfigDict(frozen=True)

    depth: int
    dimension_columns: tuple[str, ...]
    base_result: RootCauseResult
    contributor_evidence: tuple[ContributorEvidence, ...] = ()
    distractors: tuple[ContributorEvidence, ...] = ()
    children: tuple[HierarchicalRootCauseNode, ...] = ()


class AdvancedRootCauseResult(BaseModel):
    """`analytics.causal.investigate_root_cause_advanced`'s full result --
    Prompt 37 (`37_ADVANCED_ROOT_CAUSE_ENGINE_CONTRACT.md`). Deliberately
    **not** a replacement for `RootCauseResult` (Prompt 14's own flat,
    single-level shape stays exactly as it was, still the one type the
    live multi-agent `root_cause_engine` tool returns by default) -- this
    is the richer, opt-in result for a caller that asks for the hierarchy/
    interaction/significance upgrade specifically.

    Attributes:
        root: The top-of-hierarchy node (depth 0).
        interactions: Every pairwise interaction test run at the top level
            -- empty unless the hierarchy named exactly two single-column
            levels (see `InteractionEffect`'s own docstring).
        has_sufficient_evidence: `True` only when `root.contributor_evidence`
            is non-empty -- i.e. at least one top-level contributor is both
            large enough (Prompt 14's own bar) *and* statistically
            supported (this prompt's own, additional bar). Stricter than
            `RootCauseResult.has_sufficient_evidence`, which this result
            does not reuse the name of, specifically so the two can never
            be confused for one another.
        causation_confirmed: Hardcoded `False` -- see
            `CausalEvidenceStrength`'s own docstring for why no code path
            in this module can ever set this `True`.
        evidence_type: Hardcoded `CORRELATION_ONLY` -- the same guarantee,
            typed.
        causation_disclaimer: `CAUSATION_DISCLAIMER` verbatim.
        truth_level: Always `DataTruthLevel.AI_INFERENCE` -- the
            *interpretation* (which contributors to trust, how the
            hierarchy/interactions are framed) is an inference over
            `DATABASE_FACT`-equivalent arithmetic, the identical framing
            `ForecastResult.truth_level` already establishes for a
            deterministic-but-still-inferential result.
        limitations: Every disclosed caveat -- distractors excluded,
            hierarchy depth capped, interaction preconditions not met,
            etc. -- never a silent omission.
        engine_version: `CAUSAL_ENGINE_VERSION` at computation time.
    """

    model_config = ConfigDict(frozen=True)

    root: HierarchicalRootCauseNode
    interactions: tuple[InteractionEffect, ...] = ()
    has_sufficient_evidence: bool
    causation_confirmed: Literal[False] = False
    evidence_type: CausalEvidenceStrength = CausalEvidenceStrength.CORRELATION_ONLY
    causation_disclaimer: str = CAUSATION_DISCLAIMER
    truth_level: DataTruthLevel = DataTruthLevel.AI_INFERENCE
    limitations: tuple[str, ...] = ()
    engine_version: str = CAUSAL_ENGINE_VERSION


HierarchicalRootCauseNode.model_rebuild()


#: The typed-contract schema version for everything `analytics.scenario`
#: produces -- Prompt 38 (`38_WHATIF_SCENARIO_ENGINE_CONTRACT.md`). Kept
#: separate from every other engine's own version constant, the identical
#: precedent each prior engine module already set for itself.
SCENARIO_ENGINE_VERSION = "1.0.0"


class ScenarioChangeType(str, Enum):
    """The closed vocabulary for how a `ScenarioParameter.scenario_value`
    relates to the real, observed baseline value it perturbs -- the
    literal "deterministic model" behind "modeled estimates," recorded so
    a result is reproducible (master rule 11).

    Attributes:
        PERCENT_CHANGE: `scenario_value` is a percentage point delta
            applied multiplicatively (e.g. `20` means "+20%": `baseline *
            1.20`; `-15` means "-15%": `baseline * 0.85`).
        ABSOLUTE_DELTA: `scenario_value` is added directly to the
            baseline (same unit as the metric itself).
        ABSOLUTE_VALUE: `scenario_value` *replaces* the baseline value
            outright -- the baseline is read and shown for comparison,
            but the computation ignores it.
        MULTIPLIER: `scenario_value` multiplies the baseline directly
            (e.g. `1.2` means "+20%", `0.5` means "half") -- the same
            effect as `PERCENT_CHANGE` expressed as a ratio instead of a
            percentage; kept as a separate type because a caller's own
            assumption is sometimes naturally phrased one way or the
            other (e.g. "1.5x growth" vs. "+50% growth"), and forcing a
            caller to convert is an avoidable source of a silent
            off-by-a-factor-of-100 mistake.
    """

    PERCENT_CHANGE = "percent_change"
    ABSOLUTE_DELTA = "absolute_delta"
    ABSOLUTE_VALUE = "absolute_value"
    MULTIPLIER = "multiplier"


class ScenarioValueKind(str, Enum):
    """What *kind* of number one value in a scenario result is -- the
    literal "clearly separate observed facts, assumptions and modeled
    estimates" requirement, made explicit and typed rather than left to
    prose or client-side convention.

    Deliberately **not** `agent.provenance.DataTruthLevel` -- that
    vocabulary answers "how sure are we this is true" (fact / inference /
    confirmed-business-truth) and stays exactly as it was (every
    `ScenarioResult`/`ScenarioForecastResult` is still, correctly,
    `DataTruthLevel.AI_INFERENCE` overall -- the interpretation of a
    hypothetical is always an inference). This enum answers a different,
    orthogonal question specific to a what-if scenario's own anatomy:
    *which part* of the number is a fact, which part is the caller's own
    input, and which part is this engine's arithmetic on top of both.

    Attributes:
        OBSERVED: Read straight from the real, already-executed baseline
            data -- a `DataTruthLevel.DATABASE_FACT` by origin.
        ASSUMPTION: The caller's own hypothetical input (a
            `ScenarioParameter.scenario_value`) -- never computed by this
            engine, never verified against reality, just recorded as
            exactly what was asked.
        MODELED_ESTIMATE: This engine's own deterministic transform of
            an `OBSERVED` value through an `ASSUMPTION`, per
            `ScenarioChangeType` -- the "what if" answer itself.
    """

    OBSERVED = "observed"
    ASSUMPTION = "assumption"
    MODELED_ESTIMATE = "modeled_estimate"


class ScenarioParameter(BaseModel):
    """One typed what-if assumption -- the mission's own named
    requirement: "assumptions as typed parameters with units, bounds,
    affected metrics and model versions."

    Attributes:
        name: A short, human-readable label (e.g. "Marketing spend
            growth").
        affected_metric: Which column of the baseline dataset this
            parameter perturbs -- validated against the real baseline's
            own columns before anything runs (see
            `analytics.scenario.validate_scenario_parameters`); never
            silently ignored if it doesn't exist.
        unit: A short, free-form unit label for display (e.g.
            `"percent"`, `"USD"`, `"count"`, `"ratio"`) -- descriptive
            only, the same "informational, not re-derived" posture
            `analytics.visualization.ChartField.format` already
            establishes; the actual arithmetic is driven entirely by
            `change_type`, never inferred from this string.
        change_type: How `scenario_value` relates to the real baseline
            value -- see `ScenarioChangeType`.
        scenario_value: The hypothetical input itself. Must be finite
            (never `NaN`/`inf` -- rejected, see
            `validate_scenario_parameters`).
        target_label: Restricts this parameter's effect to the rows whose
            dimension-column composite label (the identical `" / "`-
            joined convention `analytics.engine.group_and_sum_by_label`
            already establishes) equals this value exactly -- e.g. only
            the `"North"` region's rows. `None` (the default) applies the
            parameter to every row.
        min_value: An explicit, caller-supplied lower bound on
            `scenario_value` -- when set, a value below it is a hard
            rejection, never silently clamped. `None` means "no explicit
            bound" (still subject to the engine's own, separate,
            disclosed "unrealistic magnitude" soft check -- see
            `Settings.scenario_unrealistic_change_percent_threshold`).
        max_value: The corresponding upper bound.
        model_version: `SCENARIO_ENGINE_VERSION` at the time this
            parameter was constructed -- master rule 11's "record ...
            version metadata" requirement, applied per-parameter since a
            parameter is the unit a caller actually authors and might
            persist/replay later.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    affected_metric: str
    unit: str
    change_type: ScenarioChangeType
    scenario_value: float
    target_label: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    model_version: str = SCENARIO_ENGINE_VERSION


class ParameterValidation(BaseModel):
    """`analytics.scenario.validate_scenario_parameters`'s own per-
    parameter verdict -- always produced, even for a parameter that turns
    out invalid, so a caller sees *every* problem at once (mirroring
    `analytics.plan_validator`/`RootCauseResult`'s own "report everything
    found, never just the first" convention).

    Attributes:
        parameter_name: Which `ScenarioParameter.name` this is about.
        is_valid: `False` if `violations` is non-empty -- derived, never
            set independently of it (the identical structural guarantee
            `RootCauseResult.has_sufficient_evidence` already establishes
            for its own `contributors` field).
        is_unrealistic: A *soft* signal (never on its own a reason
            `is_valid` is `False`, unless `Settings
            .scenario_reject_unrealistic` is on) -- the implied percent
            change from baseline exceeds `Settings
            .scenario_unrealistic_change_percent_threshold`. Disclosed in
            `violations` either way (as a warning when soft, as a hard
            reason when the reject flag is on), never silently ignored.
        violations: Every reason `is_valid` is `False` -- a hard
            rejection reason only. An "unrealistic" finding lands here
            **only** when `Settings.scenario_reject_unrealistic` is on;
            otherwise it is disclosed in `warnings` instead, which never
            affects `is_valid` -- keeping the two lists separate is what
            makes "unrealistic, but still runs" and "unrealistic, and
            therefore rejected" two genuinely different, independently
            testable outcomes rather than one list a caller has to guess
            the meaning of entry-by-entry.
        warnings: Soft, disclosed-but-non-blocking notes -- today, only
            ever the "unrealistic magnitude" note when `Settings
            .scenario_reject_unrealistic` is off. Always empty when
            `is_unrealistic` is `False`.
        baseline_value: The real, observed value this parameter's
            `affected_metric` (and `target_label`, if set) resolved to in
            the baseline data -- `None` when it could not be resolved at
            all (missing column, empty baseline, or no row matching
            `target_label`), which is itself always reflected in
            `violations`.
        implied_scenario_value: What `scenario_value` actually becomes
            once `change_type` is applied to `baseline_value` -- `None`
            under the same conditions `baseline_value` is `None`.
    """

    model_config = ConfigDict(frozen=True)

    parameter_name: str
    is_valid: bool
    is_unrealistic: bool = False
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    baseline_value: float | None = None
    implied_scenario_value: float | None = None


class ScenarioMetricComparison(BaseModel):
    """One affected metric's baseline-vs-scenario comparison -- the
    mission's own "support baseline/scenario comparison" requirement,
    typed. `baseline_kind`/`scenario_kind` are fixed `Literal`s (not just
    conventionally-set defaults) -- a `ScenarioMetricComparison` is
    structurally incapable of labeling its own baseline as anything but
    `OBSERVED` or its own scenario value as anything but
    `MODELED_ESTIMATE`, the type-level half of "clearly separate observed
    facts ... and modeled estimates" (the same pattern
    `AdvancedRootCauseResult.causation_confirmed: Literal[False]` already
    established for a different invariant).
    """

    model_config = ConfigDict(frozen=True)

    metric: str
    baseline_value: float
    scenario_value: float
    delta: float
    #: `None` only when `baseline_value == 0` (a percent change from zero
    #: is undefined -- the identical zero-denominator guard
    #: `agent.insight._compute_trend` already establishes elsewhere).
    delta_percent: float | None = None
    baseline_kind: Literal[ScenarioValueKind.OBSERVED] = ScenarioValueKind.OBSERVED
    scenario_kind: Literal[ScenarioValueKind.MODELED_ESTIMATE] = ScenarioValueKind.MODELED_ESTIMATE


class ScenarioResult(BaseModel):
    """`analytics.scenario.run_scenario`'s full result -- Prompt 38
    (`38_WHATIF_SCENARIO_ENGINE_CONTRACT.md`).

    Attributes:
        scenario_id: The caller-supplied (or engine-generated) identifier
            for this scenario run.
        name: A short, human-readable scenario name (e.g. "20% marketing
            spend increase").
        status: `"ok"` once every parameter validated; `"rejected"` if
            any parameter failed hard validation -- **never** a partial
            run with some parameters silently dropped (mirrors
            `agent.analytical_plan`'s own "an invalid plan is never
            partially trusted" precedent).
        parameter_validations: Every parameter's own verdict, always
            populated regardless of `status`.
        rejection_reasons: Every parameter-level violation, flattened,
            when `status == "rejected"` -- empty when `status == "ok"`.
        comparisons: One `ScenarioMetricComparison` per distinct
            `affected_metric` named by a validated parameter -- empty
            when `status == "rejected"`.
        baseline_analytics: The real baseline dataset's own
            `analytics.engine.compute_analytics_result` output (reused,
            never recomputed by this module) -- `None` when rejected.
        scenario_analytics: The identical computation over the
            scenario-adjusted (synthetic, in-memory only) rows -- `None`
            when rejected. Never derived from or written back to any real
            database -- see this module's own docstring for "run
            scenarios without changing source data."
        truth_level: Always `DataTruthLevel.AI_INFERENCE` -- the overall
            interpretation of a hypothetical, exactly like
            `ForecastResult.truth_level`/`AdvancedRootCauseResult
            .truth_level`.
        limitations: Disclosed caveats -- e.g. multiple parameters
            touching the same cell are composed in the order given, not
            jointly solved; a target label absent from the baseline; a
            soft "unrealistic" warning.
        engine_version: `SCENARIO_ENGINE_VERSION` at computation time.
    """

    model_config = ConfigDict(frozen=True)

    scenario_id: str
    name: str
    status: Literal["ok", "rejected"]
    parameter_validations: tuple[ParameterValidation, ...] = ()
    rejection_reasons: tuple[str, ...] = ()
    comparisons: tuple[ScenarioMetricComparison, ...] = ()
    baseline_analytics: AnalyticsResult | None = None
    scenario_analytics: AnalyticsResult | None = None
    truth_level: DataTruthLevel = DataTruthLevel.AI_INFERENCE
    limitations: tuple[str, ...] = ()
    engine_version: str = SCENARIO_ENGINE_VERSION


class ScenarioForecastPoint(BaseModel):
    """One future period's baseline-vs-scenario forecast pair --
    `analytics.scenario.apply_scenario_to_forecast`'s own per-point shape.
    Both values are estimates (the real forecast is itself
    `DataTruthLevel.AI_INFERENCE`); `scenario_forecast` is this engine's
    further, explicitly hypothetical adjustment on top of it -- never
    confused with the baseline forecast itself, which is why this is a
    separate field rather than overwriting `ForecastPoint.forecast`.
    """

    model_config = ConfigDict(frozen=True)

    period: str
    baseline_forecast: float
    scenario_forecast: float


class ScenarioForecastResult(BaseModel):
    """`analytics.scenario.apply_scenario_to_forecast`'s full result --
    applies exactly one `ScenarioParameter` uniformly to every point of an
    already-computed `ForecastResult`. Deliberately **not** folded into
    `ScenarioResult` (the same reasoning `RootCauseResult`/`ForecastResult`
    already give for staying standalone): this is a projection on top of
    another projection, not a baseline/scenario row comparison.

    Attributes:
        status: `"rejected"` when the source `ForecastResult.status !=
            "ok"` (nothing to adjust), or the parameter itself is invalid
            -- never silently applied to a rejected forecast.
        rejection_reasons: Every reason, when rejected.
        points: One `ScenarioForecastPoint` per forecasted period -- empty
            when rejected.
        parameter: The parameter actually applied -- `None` when
            rejected.
        truth_level: Always `DataTruthLevel.AI_INFERENCE`.
        limitations: Always non-empty for an `"ok"` result -- carries
            forward the source forecast's own `limitations` plus this
            function's own disclosed caveat that the adjustment is a
            uniform, simplistic perturbation (it does not refit the
            model).
        engine_version: `SCENARIO_ENGINE_VERSION` at computation time.
    """

    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "rejected"]
    rejection_reasons: tuple[str, ...] = ()
    points: tuple[ScenarioForecastPoint, ...] = ()
    parameter: ScenarioParameter | None = None
    truth_level: DataTruthLevel = DataTruthLevel.AI_INFERENCE
    limitations: tuple[str, ...] = ()
    engine_version: str = SCENARIO_ENGINE_VERSION


#: The typed-contract schema version for everything `analytics.predictive`
#: produces -- Prompt 40 (`40_PREDICTIVE_ANALYTICS_CONTRACT.md`), expanding
#: `analytics.forecasting` into general-purpose governed regression/
#: classification. Kept separate from every other engine's own version
#: constant, the identical precedent each prior engine module already set.
PREDICTIVE_ENGINE_VERSION = "1.0.0"


class PredictiveDomain(str, Enum):
    """The mission's own named problem domains, plus an honest escape
    hatch -- the identical closed-set pattern `OptimizationDomain`
    already establishes for a different engine. `DEMAND` and most
    continuous business metrics map onto `PredictiveTaskType.REGRESSION`;
    `CHURN`/`RISK` typically map onto `PredictiveTaskType.CLASSIFICATION`
    (a risk *score* instead would be `RISK` + `REGRESSION`) -- this
    engine does not hardcode that mapping, a caller states both the
    domain (for disclosure/grouping) and the task type (for the actual
    math) independently.
    """

    DEMAND = "demand"
    CHURN = "churn"
    RISK = "risk"
    OTHER = "other"


class PredictiveTaskType(str, Enum):
    """Which kind of prediction problem this is -- a closed set, not a
    free-form string."""

    REGRESSION = "regression"
    CLASSIFICATION = "classification"


class PredictiveModelType(str, Enum):
    """The closed catalog of models this engine may fit -- deliberately
    small and reviewed, never an arbitrary class name a caller (or an
    LLM) could inject. Both are deterministic: ordinary least squares
    has a unique closed-form solution for a non-degenerate design
    matrix, and `LogisticRegression`'s convex optimization converges to
    the same optimum every time given the same data and a fixed
    `random_state` -- the literal mechanism behind "test reproducibility."
    """

    LINEAR_REGRESSION = "linear_regression"
    LOGISTIC_REGRESSION = "logistic_regression"


class PredictiveModelStatus(str, Enum):
    """The closed set of outcomes `analytics.predictive
    .train_predictive_model` can report.

    Attributes:
        TRAINED: A model was successfully fit and evaluated.
        INSUFFICIENT_DATA: Fewer rows (or, for classification, fewer
            examples of some class) than `Settings
            .predictive_min_training_rows`/`predictive_min_rows_per_class`
            require.
        LEAKAGE_DETECTED: A feature is identical to, or suspiciously
            correlated with, the target -- rejected by default (see
            `Settings.predictive_reject_on_leakage`).
        UNAUTHORIZED: The caller lacks `Permission
            .VIEW_RESTRICTED_COLUMNS` for a feature/target column
            `config.sensitive_columns` classifies restricted --
            deliberately distinct from `INVALID`/`INSUFFICIENT_DATA`
            (a property of *who is asking*, never conflated with a
            property of the data or the request's own structure).
        INVALID: The request itself failed structural validation (an
            unknown column, a non-numeric feature, a mismatched
            model_type/task_type pairing, ...) before any fit was
            attempted.
        ERROR: The underlying solver raised or returned something this
            engine does not recognize -- never silently swallowed.
    """

    TRAINED = "trained"
    INSUFFICIENT_DATA = "insufficient_data"
    LEAKAGE_DETECTED = "leakage_detected"
    UNAUTHORIZED = "unauthorized"
    INVALID = "invalid"
    ERROR = "error"


class LeakageFinding(BaseModel):
    """One feature flagged as a likely target-leakage source -- "no
    partial trust": `analytics.predictive.train_predictive_model` never
    fits a model while any finding is present and `Settings
    .predictive_reject_on_leakage` is on (the default)."""

    model_config = ConfigDict(frozen=True)

    feature: str
    reason: str
    correlation: float | None = None


class RegressionMetrics(BaseModel):
    """Evaluation metrics for a `PredictiveTaskType.REGRESSION` model,
    computed **only** on the held-out test split -- never the training
    rows the model itself was fit on, which would be an overfitting-
    optimistic (and dishonest) estimate of real-world accuracy."""

    model_config = ConfigDict(frozen=True)

    mae: float
    rmse: float
    r2: float
    formula: str = (
        "MAE = mean(|actual - predicted|); RMSE = sqrt(mean((actual - predicted)^2)); "
        "R2 = 1 - SS_res/SS_tot -- all computed on the held-out test split only"
    )


class ClassificationMetrics(BaseModel):
    """Evaluation metrics for a `PredictiveTaskType.CLASSIFICATION`
    model, computed **only** on the held-out test split. `precision`/
    `recall`/`f1` use macro-averaging across classes (never just the
    positive class) so a caller isn't misled by an imbalanced minority
    class's own, possibly-much-worse, score.

    `test_class_counts` is included specifically so a caller can judge
    how much a small test-set class count should discount confidence in
    these numbers -- the identical "disclose, never hide a limitation"
    posture every other engine in this package already follows.
    """

    model_config = ConfigDict(frozen=True)

    accuracy: float
    precision: float
    recall: float
    f1: float
    test_class_counts: dict[str, int]
    formula: str = (
        "accuracy = correct / total; precision/recall/f1 = macro-averaged across classes "
        "(sklearn.metrics, average='macro') -- all computed on the held-out test split only"
    )


class PredictiveModelMetadata(BaseModel):
    """Master-contract rule 11's "record ... version metadata" applied
    to a trained predictive model -- the mission's own "store model/data
    fingerprints, features and metrics" requirement.

    Attributes:
        model_type: See `PredictiveModelType`.
        version: `PREDICTIVE_ENGINE_VERSION` at training time.
        feature_columns: Exactly the columns the model was fit on, in
            the order used to build its own design matrix.
        target_column: What the model predicts.
        training_row_count: Rows in the training split.
        test_row_count: Rows in the held-out test split.
        test_fraction: The configured train/test split fraction.
        random_seed: The deterministic split seed -- see
            `analytics.predictive._deterministic_order`'s own docstring
            for why this makes the split (and therefore the whole
            result) exactly reproducible without depending on Python's
            or numpy's own global random state.
        data_fingerprint: `sha256` of a deterministic serialization of
            the training+test rows actually used (feature values, the
            target, and the row count) -- changes if and only if the
            underlying data changes, the literal "detect drift / confirm
            reproducibility" mechanism the mission names.
        model_fingerprint: `sha256` of `model_type` + `feature_columns` +
            `target_column` + `data_fingerprint` -- identifies this
            exact trained model; two training runs with byte-identical
            inputs always produce the same `model_fingerprint`.
        coefficients: `feature name -> fitted coefficient` -- the model
            is always fully transparent, never a black box; for
            `LOGISTIC_REGRESSION` with more than two classes this is the
            coefficient vector for the first class scikit-learn reports
            (a disclosed, bounded scope -- see this module's own
            docstring in `analytics/predictive.py` for multi-class
            detail).
        intercept: The fitted intercept term.
    """

    model_config = ConfigDict(frozen=True)

    model_type: PredictiveModelType
    version: str = PREDICTIVE_ENGINE_VERSION
    feature_columns: tuple[str, ...]
    target_column: str
    training_row_count: int
    test_row_count: int
    test_fraction: float
    random_seed: int
    data_fingerprint: str
    model_fingerprint: str
    coefficients: dict[str, float]
    intercept: float


class PredictionWithUncertainty(BaseModel):
    """One scored row's prediction -- "expose uncertainty where
    supported," the mission's own requirement, made concrete per task
    type.

    Attributes:
        index: Which `predict_rows` entry (0-based, in the order given)
            this is about -- predictions are always returned in the
            same order they were requested, never resorted.
        value: The predicted value -- a `float` for `REGRESSION`, the
            predicted class label (rendered as `str`, whatever the
            original label type) for `CLASSIFICATION`.
        lower_bound: `REGRESSION` only -- a residual-based prediction
            interval lower bound (same z-based convention
            `analytics.forecasting.ForecastPoint` already establishes),
            `None` for `CLASSIFICATION`.
        upper_bound: The corresponding upper bound.
        class_probabilities: `CLASSIFICATION` only -- `class label ->
            predicted probability`, always summing to `1.0`; `None` for
            `REGRESSION` (a prediction interval is this task type's own
            uncertainty exposure instead).
    """

    model_config = ConfigDict(frozen=True)

    index: int
    value: float | str
    lower_bound: float | None = None
    upper_bound: float | None = None
    class_probabilities: dict[str, float] | None = None


class PredictiveModelResult(BaseModel):
    """`analytics.predictive.train_predictive_model`'s full result --
    Prompt 40 (`40_PREDICTIVE_ANALYTICS_CONTRACT.md`).

    Attributes:
        model_id: Caller-supplied identifier for this training run.
        name: A short, human-readable name.
        domain: See `PredictiveDomain`.
        task_type: See `PredictiveTaskType`.
        status: See `PredictiveModelStatus`.
        metadata: `None` unless `status == TRAINED`.
        regression_metrics: Set only when `status == TRAINED` and
            `task_type == REGRESSION`.
        classification_metrics: Set only when `status == TRAINED` and
            `task_type == CLASSIFICATION`.
        predictions: One `PredictionWithUncertainty` per `predict_rows`
            entry supplied -- empty when none were supplied, or when
            `status != TRAINED`.
        leakage_findings: Every leakage candidate found -- populated
            whenever any exist, regardless of whether
            `Settings.predictive_reject_on_leakage` actually rejected
            training because of them (so a caller who turned that flag
            off still sees what was found).
        rejection_reasons: Every reason training did not produce a model
            -- always non-empty when `status != TRAINED`.
        truth_level: Always `DataTruthLevel.AI_INFERENCE` -- a trained
            model's prediction is an inference over historical data,
            never an observed fact, the identical framing every other
            engine in this package already establishes.
        limitations: Disclosed caveats -- always non-empty for a
            `TRAINED` result (e.g. "numeric features only," "no
            categorical encoding," a small-test-set-size caveat when
            relevant).
        engine_version: `PREDICTIVE_ENGINE_VERSION` at computation time.
    """

    model_config = ConfigDict(frozen=True)

    model_id: str
    name: str
    domain: PredictiveDomain
    task_type: PredictiveTaskType
    status: PredictiveModelStatus
    metadata: PredictiveModelMetadata | None = None
    regression_metrics: RegressionMetrics | None = None
    classification_metrics: ClassificationMetrics | None = None
    predictions: tuple[PredictionWithUncertainty, ...] = ()
    leakage_findings: tuple[LeakageFinding, ...] = ()
    rejection_reasons: tuple[str, ...] = ()
    truth_level: DataTruthLevel = DataTruthLevel.AI_INFERENCE
    limitations: tuple[str, ...] = ()
    engine_version: str = PREDICTIVE_ENGINE_VERSION


class FeatureDriftFinding(BaseModel):
    """One feature's own two-sample distribution-difference test --
    `analytics.predictive.detect_feature_drift`'s per-feature result."""

    model_config = ConfigDict(frozen=True)

    feature: str
    reference_count: int
    new_count: int
    ks_statistic: float
    p_value: float
    is_drifted: bool
    formula: str = (
        "two-sample Kolmogorov-Smirnov test (scipy.stats.ks_2samp); drifted when "
        "p_value < Settings.predictive_drift_p_value_threshold"
    )


class FeatureDriftReport(BaseModel):
    """`analytics.predictive.detect_feature_drift`'s full result --
    compares a reference (e.g. training) dataset against a new one,
    feature by feature. Deliberately **not** folded into
    `PredictiveModelResult`: drift detection is a standalone comparison
    between two datasets (mirroring `RootCauseResult`'s own "standalone,
    not one more finding about a single dataset" reasoning), callable on
    its own against any two same-shaped datasets, not only before/after
    training one specific model.
    """

    model_config = ConfigDict(frozen=True)

    threshold: float
    findings: tuple[FeatureDriftFinding, ...]
    any_drifted: bool
    #: Every feature named but not actually compared, and why (missing
    #: from one side, non-numeric, or too few points for the test) --
    #: disclosed, never a silent omission.
    notes: tuple[str, ...] = ()
    truth_level: DataTruthLevel = DataTruthLevel.AI_INFERENCE
    engine_version: str = PREDICTIVE_ENGINE_VERSION
