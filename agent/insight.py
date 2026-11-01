"""Grounds and shapes the "plain-English insight" narrative feature.

Everything here is pure/deterministic (no LLM, no I/O) -- the only thing
that touches Ollama is `agent.llm_client.generate_insight_from_llm`, which
consumes a `ResultSummary` built here. Keeping the summarization and
grounding logic separate from the LLM call means both `generate_insight_node`
(runtime) and `scripts/run_eval.py` (offline eval) can share the exact same
definition of "what counts as grounded" -- one definition, not two that
could quietly drift apart.
"""

from __future__ import annotations

import contextlib
import re
import statistics as _statistics
from decimal import Decimal
from typing import Literal

from analytics.period_labels import classify_period_column
from pydantic import BaseModel, ConfigDict

from security.sanitization import normalize_text

# Types a driver might hand back for a numeric column. Deliberately excludes
# bool -- Python's bool is a subclass of int, and a True/False column is not
# a quantity to compute min/max/sum over.
_NUMERIC_TYPES = (int, float, Decimal)

# Generous cap on the rendered length of a "top label" pulled from actual
# result row data (see summarize_result) before it's ever concatenated into
# the insight prompt -- mirrors db/value_sampling.py's _MAX_VALUE_LENGTH for
# the same reason: this is genuinely attacker-writable data, not metadata.
_MAX_LABEL_LENGTH = 200


def _is_numeric(value: object) -> bool:
    return isinstance(value, _NUMERIC_TYPES) and not isinstance(value, bool)


class ColumnStat(BaseModel):
    """Aggregate shape of one column -- never the column's raw values.

    Attributes:
        name: Column name.
        is_numeric: Whether every value in this column was numeric.
        minimum / maximum / total: Only set when `is_numeric` is True.
        distinct_count: Only set when `is_numeric` is False -- how many
            distinct values the column took across the result.
        stddev: Population standard deviation (`statistics.pstdev`, not the
            sample variant -- a query result is the *entire* population
            being asked about, not a sample of some larger population).
            Only set when `is_numeric` is True and there are >= 2 rows
            (stddev of a single value is degenerate, left `None` rather
            than reported as a misleading `0.0`).
        coefficient_of_variation: `stddev / |mean|`, a scale-independent
            "how spread out is this, relative to its own size" measure --
            lets a caller compare variability across columns/questions
            with very different magnitudes (a stddev of 500 means very
            different things for a column averaging 600 vs. averaging
            600,000). `None` when `stddev` is `None` or the mean is 0
            (division would be meaningless, not just zero).
    """

    model_config = ConfigDict(frozen=True)

    name: str
    is_numeric: bool
    minimum: float | None = None
    maximum: float | None = None
    total: float | None = None
    distinct_count: int | None = None
    stddev: float | None = None
    coefficient_of_variation: float | None = None


class TrendStat(BaseModel):
    """A deterministic period-over-period comparison, computed the same
    "Python does the arithmetic, the LLM only narrates it" way as
    `ResultSummary.top_label`/`top_share_percent` above -- this is the
    part of "AI Data Analyst" that answers a trend/growth-shaped question
    (e.g. "how did sales change over time") without ever asking the LLM to
    compute a percentage change itself.

    Deliberately based on **row order as returned by the SQL**, not a
    parsed/re-sorted chronology -- a query answering a trend question
    already orders by the natural period column (`ORDER BY year`, `ORDER
    BY month`, ...) as part of answering the question correctly in the
    first place; re-deriving that ordering here would risk silently
    disagreeing with what the SQL itself expresses.

    **Only ever computed for a label column that is actually
    chronological**, as judged by `analytics.period_labels
    .classify_period_column` -- the same classification
    `analytics.engine` uses to decide a result is a time series. Trusting
    the SQL's ordering is reasonable; assuming the ordering *means*
    something chronological is not. Without that gate this reported a
    "period-over-period" change between two region names, which is not a
    finding about anything (UAT-readiness Phase 5, `HAL-001`): a
    `Region, TotalSales` result ordered East, West, North produced
    `first_period="East"`, `last_period="North"`,
    `change_percent=-58.3`, `direction="down"`. That number then entered
    `ResultSummary.allowed_percents`, so the grounding gate *licensed*
    an insight stating a 58.3% decline that had never happened -- and
    `analytics.provider._trend_finding` renders the same value as a
    `DataTruthLevel.DATABASE_FACT` sentence, which CLAUDE.md 4.4 forbids
    for anything that is not an observed fact.
    """

    model_config = ConfigDict(frozen=True)

    label_column: str
    value_column: str
    first_period: str
    first_value: float
    last_period: str
    last_value: float
    change_percent: float
    direction: Literal["up", "down", "flat"]


class OutlierStat(BaseModel):
    """One label whose value is a statistical outlier (more than
    `OUTLIER_STDDEV_THRESHOLD` population-standard-deviations from the
    mean) among the per-label totals for the result's value column --
    a deterministic anomaly-detection signal, not an LLM judgment call."""

    model_config = ConfigDict(frozen=True)

    label: str
    value: float
    column: str
    deviations_from_mean: float


#: How many population standard deviations from the mean counts as an
#: outlier. 2.0 is a common, conservative default (roughly the top/bottom
#: ~5% of a normal distribution) -- not tuned against this project's own
#: eval set, since no outlier-detection eval cases exist yet (a disclosed,
#: separate gap from this feature itself).
OUTLIER_STDDEV_THRESHOLD = 2.0


class ResultSummary(BaseModel):
    """A SMALL, aggregate-only summary of a query result -- what the LLM sees.

    Deliberately excludes raw rows: row count, column names, and per-column
    aggregate stats are all that's included, so prompt size is independent
    of how many rows the query actually returned (CLAUDE.md's "keep the
    result summary passed to the LLM small" constraint) -- a 3-row result
    and a 900-row result produce a summary of roughly the same size.

    `top_label` / `top_value` / `top_share_percent` capture the one
    cross-column relationship worth naming explicitly: for the first
    numeric column paired with the first non-numeric ("label") column,
    which label had the largest total and what share of the column's grand
    total that represents (e.g. "Bikes", 2_300_000.0, 60.4). This is the
    single most common shape of question this app answers ("X by category"),
    and computing the share here -- in Python, exactly -- means the LLM
    never has to do arithmetic to produce a claim like "roughly 60% of the
    total," which it would otherwise be prone to getting wrong.
    """

    model_config = ConfigDict(frozen=True)

    row_count: int
    columns: tuple[str, ...]
    column_stats: tuple[ColumnStat, ...]
    top_label_column: str | None = None
    top_label: str | None = None
    top_value_column: str | None = None
    top_value: float | None = None
    top_share_percent: float | None = None
    #: Set only when the result has a natural label/value pair (the same
    #: one `top_label`/`top_value` are derived from) and at least two
    #: distinct labels to compare -- see `TrendStat`'s own docstring.
    #: `None` is a real, common outcome (e.g. a single-row aggregate has
    #: nothing to trend), not a computation failure.
    trend: TrendStat | None = None
    #: Every per-label total more than `OUTLIER_STDDEV_THRESHOLD` standard
    #: deviations from the mean, for the same label/value pair -- empty
    #: (not `None`) when there's nothing to flag, so a caller can always
    #: iterate it without a `None` check.
    outliers: tuple[OutlierStat, ...] = ()

    def allowed_values(self) -> set[float]:
        """Every plain (non-percent) number the insight is allowed to state.

        Used both by this module's own runtime grounding gate
        (`is_insight_grounded`) and by `scripts/run_eval.py`'s eval-set
        grounding checks.
        """
        numbers = {float(self.row_count)}
        for stat in self.column_stats:
            for value in (
                stat.minimum,
                stat.maximum,
                stat.total,
                stat.distinct_count,
                stat.stddev,
            ):
                if value is not None:
                    numbers.add(round(float(value), 2))
        if self.top_value is not None:
            numbers.add(round(float(self.top_value), 2))
        if self.top_label is not None:
            # The "label" can itself be numeric (e.g. a year in a
            # numeric-vs-numeric result -- see summarize_result's fallback),
            # in which case a claim like "2013 was the highest" should be
            # groundable too.
            with contextlib.suppress(ValueError):
                numbers.add(round(float(self.top_label), 2))
        if self.trend is not None:
            numbers.add(round(self.trend.first_value, 2))
            numbers.add(round(self.trend.last_value, 2))
        for outlier in self.outliers:
            numbers.add(round(outlier.value, 2))
        return numbers

    def allowed_percents(self) -> set[float]:
        """Every percentage the insight is allowed to state."""
        percents = set()
        if self.top_share_percent is not None:
            percents.add(round(self.top_share_percent, 1))
        if self.trend is not None:
            percents.add(round(self.trend.change_percent, 1))
        return percents


def should_skip_insight(columns: list[str], rows: list[tuple]) -> bool:
    """True when an insight would add no value, or there's nothing to summarize.

    Covers two cases: an empty result (nothing to narrate -- no aggregates
    are even computable) and a single-row, single-column result (e.g. "how
    many customers are there?"), where the one returned value already *is*
    the full answer and restating it in a sentence would just be redundant.
    """
    if not rows:
        return True
    return len(rows) == 1 and len(columns) == 1


def summarize_result(columns: list[str], rows: list[tuple]) -> ResultSummary:
    """Reduces a full result set to the small `ResultSummary` sent to the LLM.

    Args:
        columns: Column names, in result order.
        rows: Result rows. Only ever read here -- never forwarded whole to
            the LLM (see `ResultSummary`'s docstring).
    """
    row_count = len(rows)
    numeric_col_indices = [
        idx for idx in range(len(columns)) if rows and all(_is_numeric(row[idx]) for row in rows)
    ]
    label_col_indices = [idx for idx in range(len(columns)) if idx not in numeric_col_indices]

    column_stats: list[ColumnStat] = []
    for idx, name in enumerate(columns):
        values = [row[idx] for row in rows]
        if idx in numeric_col_indices:
            nums = [float(v) for v in values]
            # pstdev (population, not sample) needs >= 1 value and is a
            # real statistics.StatisticsError below 1 -- guarded by the
            # `rows` truthiness already required to reach `numeric_col_indices`
            # at all, but a single-row result makes "spread" degenerate
            # (nothing to spread), so it's left `None` rather than a
            # trivially-true `0.0` that looks like a real "no variance"
            # finding.
            stddev = _statistics.pstdev(nums) if len(nums) >= 2 else None
            mean = sum(nums) / len(nums)
            coefficient_of_variation = (
                round(stddev / abs(mean), 4) if stddev is not None and mean != 0 else None
            )
            column_stats.append(
                ColumnStat(
                    name=name,
                    is_numeric=True,
                    minimum=min(nums),
                    maximum=max(nums),
                    total=sum(nums),
                    stddev=round(stddev, 4) if stddev is not None else None,
                    coefficient_of_variation=coefficient_of_variation,
                )
            )
        else:
            column_stats.append(
                ColumnStat(name=name, is_numeric=False, distinct_count=len(set(values)))
            )

    top_label_column = top_label = top_value_column = None
    top_value = top_share_percent = None
    # The *last* numeric column, not the first: a GROUP BY dimension
    # conventionally comes before its aggregate in a SELECT list (e.g.
    # "SELECT CalendarYear, SUM(SalesAmount)"), so when a dimension is
    # itself numeric (a year), picking the first numeric column as the
    # "value" would misattribute the metric to the wrong column.
    value_idx = numeric_col_indices[-1] if numeric_col_indices else None
    label_idx = None
    if value_idx is not None:
        if label_col_indices:
            label_idx = label_col_indices[0]
        else:
            # No non-numeric column at all (e.g. "sales by year" -- both
            # columns are numeric). Fall back to the first other column as
            # the "label" regardless of its type, so a claim like "2013 was
            # the highest year" is still groundable -- without this, a
            # numeric-vs-numeric result could never support a top/share
            # claim at all, even though the relationship is just as real.
            label_idx = next((i for i in range(len(columns)) if i != value_idx), None)
    trend: TrendStat | None = None
    outliers: tuple[OutlierStat, ...] = ()
    if value_idx is not None and label_idx is not None:
        totals_by_label: dict[str, float] = {}
        for row in rows:
            # Row *data*, not schema metadata -- genuinely attacker-writable
            # the same way a sampled schema value is (see db/value_sampling.py),
            # and this is the one point in the insight feature where it flows
            # into a prompt (`agent.llm_client._build_insight_prompt`'s
            # top_label rendering) unless normalized here first.
            label = normalize_text(str(row[label_idx]))[:_MAX_LABEL_LENGTH]
            totals_by_label[label] = totals_by_label.get(label, 0.0) + float(row[value_idx])
        top_label, top_value = max(totals_by_label.items(), key=lambda item: item[1])
        grand_total = sum(totals_by_label.values())
        top_label_column = columns[label_idx]
        top_value_column = columns[value_idx]
        if grand_total:
            top_share_percent = round(100 * top_value / grand_total, 1)

        trend = _compute_trend(totals_by_label, top_label_column, top_value_column)
        outliers = _compute_outliers(totals_by_label, top_value_column)

    return ResultSummary(
        row_count=row_count,
        columns=tuple(columns),
        column_stats=tuple(column_stats),
        top_label_column=top_label_column,
        top_label=top_label,
        top_value_column=top_value_column,
        top_value=top_value,
        top_share_percent=top_share_percent,
        trend=trend,
        outliers=outliers,
    )


def _compute_trend(
    totals_by_label: dict[str, float], label_column: str, value_column: str
) -> TrendStat | None:
    """First-vs-last period comparison, in the label's original row/insertion
    order (a Python `dict` preserves insertion order) -- see `TrendStat`'s
    own docstring for why this deliberately trusts the SQL's own ordering
    rather than re-deriving a chronology.

    Returns `None` when the label column is not chronological (see
    `TrendStat`'s own docstring -- a first-versus-last comparison of two
    *categories* is not a trend and must not be reported as one), when
    there are fewer than two distinct labels (nothing to compare), or
    when the first period's value is exactly zero (a percentage change
    from zero is undefined, not a real "infinite growth" finding worth
    stating).
    """
    if len(totals_by_label) < 2:
        return None
    labels = list(totals_by_label)
    if classify_period_column(labels) is None:
        return None
    first_period, last_period = labels[0], labels[-1]
    first_value, last_value = totals_by_label[first_period], totals_by_label[last_period]
    if first_value == 0:
        return None
    change_percent = round(100 * (last_value - first_value) / abs(first_value), 1)
    direction: Literal["up", "down", "flat"] = (
        "up" if change_percent > 0 else "down" if change_percent < 0 else "flat"
    )
    return TrendStat(
        label_column=label_column,
        value_column=value_column,
        first_period=first_period,
        first_value=first_value,
        last_period=last_period,
        last_value=last_value,
        change_percent=change_percent,
        direction=direction,
    )


def _compute_outliers(
    totals_by_label: dict[str, float], value_column: str
) -> tuple[OutlierStat, ...]:
    """Flags any per-label total more than `OUTLIER_STDDEV_THRESHOLD`
    population-standard-deviations from the mean.

    Requires at least 3 distinct labels -- with only two, one value is
    always exactly as far above the mean as the other is below, which
    isn't a meaningful "this one stands out" signal, just an artifact of
    there being two numbers.
    """
    if len(totals_by_label) < 3:
        return ()
    values = list(totals_by_label.values())
    mean = sum(values) / len(values)
    stddev = _statistics.pstdev(values)
    if stddev == 0:
        return ()
    outliers = []
    for label, value in totals_by_label.items():
        deviations = (value - mean) / stddev
        if abs(deviations) > OUTLIER_STDDEV_THRESHOLD:
            outliers.append(
                OutlierStat(
                    label=label,
                    value=value,
                    column=value_column,
                    deviations_from_mean=round(deviations, 2),
                )
            )
    return tuple(outliers)


# Matches a plain number, optionally $-prefixed and comma-grouped, with an
# optional trailing %/K/M/B suffix. Used only for grounding-checking the
# LLM's *output* text -- generation is separately instructed not to
# abbreviate, so this is a defensive parser, not the primary contract.
#
# The comma-grouped branch requires at least one ",\d{3}" group (`+`, not
# `*`): with `*` it can match on a plain, comma-less number too (e.g.
# "2300000.0"), consuming only the first 1-3 digits as a truncated false
# match ("230") and leaving the rest ("0000.0") to be picked up as a second,
# spurious number -- silently corrupting every large ungrouped number.
_NUMBER_RE = re.compile(
    r"\$?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?P<suffix>%|[kKmMbB](?![a-zA-Z]))?"
)
_SUFFIX_MULTIPLIERS = {"k": 1e3, "m": 1e6, "b": 1e9}


def extract_numbers(text: str) -> list[tuple[float, bool]]:
    """Extracts every number-looking token from `text` as (value, is_percent).

    K/M/B suffixes are expanded to their full value (e.g. "2.3M" -> 2_300_000.0);
    a trailing "%" is flagged separately since percentages and plain
    quantities are never interchangeable when checking groundedness.
    """
    results = []
    for match in _NUMBER_RE.finditer(text):
        raw = match.group("num")
        value = float(raw.replace(",", ""))
        suffix = (match.group("suffix") or "").lower()
        is_percent = suffix == "%"
        if suffix in _SUFFIX_MULTIPLIERS:
            value *= _SUFFIX_MULTIPLIERS[suffix]
        results.append((value, is_percent))
    return results


def _isclose(a: float, b: float, *, abs_tol: float) -> bool:
    return abs(a - b) <= max(abs_tol, abs(b) * 0.01)


#: Words that assert "this one is the extreme of the set". Only
#: unambiguous superlatives, deliberately: a *comparative* ("East grew
#: faster than West") makes a claim this module has no ground truth to
#: contradict, while a superlative is checkable against `top_label`,
#: which `summarize_result` computed in Python from the real rows.
#:
#: `"top"` is included because "the top region" is the commonest phrasing
#: of all here. `"more"`/`"better"` are deliberately absent -- they are
#: comparatives, and treating them as superlatives would reject ordinary
#: correct prose.
_SUPERLATIVE_MARKERS = frozenset(
    """highest lowest largest smallest biggest greatest most least best worst top
    leading strongest weakest maximum minimum""".split()
)

#: Words asserting a direction of change, mapped to the
#: `TrendStat.direction` value each one means.
_DIRECTION_WORDS: dict[str, str] = {
    "increased": "up",
    "increase": "up",
    "increasing": "up",
    "rose": "up",
    "rising": "up",
    "grew": "up",
    "growing": "up",
    "growth": "up",
    "climbed": "up",
    "improved": "up",
    "gained": "up",
    "higher": "up",
    "decreased": "down",
    "decrease": "down",
    "decreasing": "down",
    "declined": "down",
    "decline": "down",
    "declining": "down",
    "fell": "down",
    "falling": "down",
    "dropped": "down",
    "shrank": "down",
    "worsened": "down",
    "lower": "down",
}

#: Shortest label this will look for in prose. A one-character label
#: ("A", "M") matched on a word boundary collides with ordinary English
#: ("a") and with a column initial, so a mention of it is not evidence of
#: anything. Two-character labels ("US", "UK") are kept, minus the
#: function words below.
_MIN_MATCHABLE_LABEL_LENGTH = 2

#: Short labels that are also ordinary English words. Finding one of
#: these in prose says nothing about whether the label was mentioned.
_UNMATCHABLE_LABELS = frozenset(
    """a an as at be by do go he if in is it me my no of on or so to up we the and for
    not but you all one two""".split()
)

_WORD_TOKEN_RE = re.compile(r"[A-Za-z]+")


def _words(text: str) -> set[str]:
    """Lowercase word tokens in `text`, for marker lookups."""
    return {match.group(0).lower() for match in _WORD_TOKEN_RE.finditer(text)}


def _mentions_label(text: str, label: str) -> bool:
    """Whether `label` appears in `text` as a whole word, case-insensitively.

    Whole-word rather than plain substring, so a label that is a prefix
    of a longer word ("East" inside "Eastern") is not counted as a
    mention of the label itself.
    """
    pattern = r"(?<!\w)" + re.escape(label) + r"(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _matchable_labels(result_labels: list[str] | None) -> list[str]:
    """The subset of `result_labels` worth looking for in prose."""
    if not result_labels:
        return []
    seen: dict[str, str] = {}
    for raw in result_labels:
        label = str(raw).strip()
        if len(label) < _MIN_MATCHABLE_LABEL_LENGTH:
            continue
        if label.casefold() in _UNMATCHABLE_LABELS:
            continue
        seen.setdefault(label.casefold(), label)
    return list(seen.values())


def _superlative_contradiction(
    insight_text: str, summary: ResultSummary, result_labels: list[str] | None
) -> str | None:
    """The insight calls some label the extreme, and it is not the one.

    Fires only when all three of these hold, which is what keeps it from
    rejecting correct prose:

      1. `summary.top_label` exists -- there is a computed answer to
         "which one is the extreme" to compare against.
      2. The text contains a superlative marker.
      3. The text mentions at least one of the result's own labels, and
         `top_label` is **not** among the ones it mentions.

    An insight that states a superlative without naming any label ("the
    highest total was 600000") mentions none and passes -- the numeric
    check already covers that sentence. An insight that names the real
    top label passes whatever else it also names.

    Returns the log-safe reason, or `None` when there is no
    contradiction.
    """
    if summary.top_label is None:
        return None
    labels = _matchable_labels(result_labels)
    if not labels:
        return None
    if not (_words(insight_text) & _SUPERLATIVE_MARKERS):
        return None
    mentioned = [label for label in labels if _mentions_label(insight_text, label)]
    if not mentioned:
        return None
    top = str(summary.top_label).casefold()
    if any(label.casefold() == top for label in mentioned):
        return None
    return (
        f"states a superlative about {len(mentioned)} label(s) that do not include "
        f"the computed top {summary.top_label_column or 'label'}"
    )


def _direction_contradiction(insight_text: str, summary: ResultSummary) -> str | None:
    """The insight asserts the opposite of the computed change.

    Deliberately narrow in three ways, because over-blocking a correct
    narrative is its own failure mode (CLAUDE.md 5.8):

      1. Only when `summary.trend` exists at all -- which, since
         `HAL-001`, means the label column is a real chronology.
      2. Only when that chronology has **exactly two** periods. Over
         three or more, "revenue fell in 2022 before recovering" is
         correct prose naming a direction opposite to the overall one,
         and no word-level check can tell that from a reversal. With two
         periods there is no middle to narrate, so any direction stated
         is a claim about that one change.
      3. Only for an `up`/`down` trend, never `flat`: with a flat overall
         change both directions read as "opposite", so the check would
         have an opinion about every sentence.

    A text naming both directions passes -- the correct one is present,
    so the sentence is a narrative rather than a reversal.

    Returns the log-safe reason, or `None`.
    """
    trend = summary.trend
    if trend is None or trend.direction == "flat":
        return None
    label_stat = next(
        (stat for stat in summary.column_stats if stat.name == trend.label_column), None
    )
    if label_stat is None or label_stat.distinct_count != 2:
        return None
    stated = {_DIRECTION_WORDS[word] for word in _words(insight_text) if word in _DIRECTION_WORDS}
    if not stated or trend.direction in stated:
        return None
    return (
        f"asserts the direction {'/'.join(sorted(stated))} where the "
        f"computed direction is {trend.direction}"
    )


def insight_grounding_failure(
    insight_text: str,
    summary: ResultSummary,
    question: str = "",
    sql: str = "",
    *,
    result_labels: list[str] | None = None,
) -> str | None:
    """Why `insight_text` may not be shown, or `None` if it may.

    Three independent checks, each of which only ever fires on a
    **contradiction of something this module computed in Python from the
    real rows** -- never on a claim that is merely unverifiable. That
    distinction is the whole design: an insight nobody can check is
    labelled rather than suppressed (`guardrails.grounding` makes the
    same argument for retrieval answers), while an insight that disagrees
    with the result it was built from is simply wrong and is dropped.

    1. **Numbers.** Every number in the text must match, within a small
       rounding tolerance, a number derivable from `summary` (row count,
       a column's min/max/sum/distinct count, the top value, or its share
       as a percentage) or one that literally appears in the question or
       SQL -- a filter value like a year the user themselves typed is not
       a fabricated claim, even though it isn't part of the result data.
    2. **Superlatives** -- see `_superlative_contradiction`.
    3. **Direction of change** -- see `_direction_contradiction`.

    **What is deliberately still not checked**, because no local
    deterministic check can decide it and guessing would reject correct
    answers: a comparative between two labels ("East grew faster than
    West"), an entity name that is not in the result at all ("South was
    strongest" -- the result may be a TOP-N, so this module cannot know
    "South" is not a real region), a causal claim, and a date that the
    result does not contain. These are recorded as a residual in
    `docs/uat-readiness/phase-5-hallucination.md` rather than papered
    over here.

    Args:
        insight_text: The model's narrative. Untrusted output.
        summary: The result summary the narrative was built from.
        question: The user's question, for literal filter values.
        sql: The executed SQL, likewise.
        result_labels: The label-column values from the executed result,
            which check 2 needs and the other two do not. **Optional, and
            a caller that omits it silently gets the weaker two-check
            behaviour** -- the same trap CLAUDE.md 5.4 records for
            `projection_source_columns`, named here for the same reason.
            `agent.nodes.generate_insight_node` passes them; the offline
            eval callers deliberately do not, because the eval set
            measures the numeric property.

    Returns:
        A short, log-safe reason naming which check failed, or `None`.
        Never contains the insight text or a result value, so it is safe
        to log (CLAUDE.md 5.6).
    """
    allowed_values = summary.allowed_values()
    allowed_percents = summary.allowed_percents()
    for value, _is_percent in extract_numbers(f"{question} {sql}"):
        allowed_values.add(round(value, 2))

    for value, is_percent in extract_numbers(insight_text):
        pool = allowed_percents if is_percent else allowed_values
        if not any(
            _isclose(value, allowed, abs_tol=1.0 if is_percent else 0.5) for allowed in pool
        ):
            return "states a number the result does not support"

    return _superlative_contradiction(insight_text, summary, result_labels) or (
        _direction_contradiction(insight_text, summary)
    )


def is_insight_grounded(
    insight_text: str,
    summary: ResultSummary,
    question: str = "",
    sql: str = "",
    *,
    result_labels: list[str] | None = None,
) -> bool:
    """True if `insight_text` contradicts nothing in the result data.

    A thin boolean wrapper over `insight_grounding_failure`, which is the
    function to call when the reason matters (the runtime node logs it).
    This shape is kept because `scripts/run_eval.py` and
    `eval/component_benchmark/cases_groundedness.py` are pass/fail
    harnesses that want exactly a boolean.

    See `insight_grounding_failure` for what is and is not checked, and
    for why `result_labels` being optional matters.
    """
    return (
        insight_grounding_failure(insight_text, summary, question, sql, result_labels=result_labels)
        is None
    )
