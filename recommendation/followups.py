"""Grounded, one-click follow-up question suggestions -- the "Ask next"
chips rendered under an answered turn
(`frontend/src/components/chat/FollowUpSuggestions.tsx`).

**Why this module exists.** `RecommendationKind.NEXT_QUESTION` and the
chip component that renders it both already existed, but the only two
producers of that kind anywhere in this codebase were
`analytics.forecasting.recommendations_for_forecast` and
`analytics.scenario.recommendations_for_scenario` -- and both are
disclosed (in `CLAUDE.md` and their own contracts) as *not wired into any
live recommendation or frontend path*. `recommendation.engine` itself
produced zero. The chips were therefore real UI over an empty list: in
the live `/ask` pipeline they never rendered at all. This module is the
missing producer, and it is deliberately a producer of *candidates*, not
of `Recommendation`s -- `recommendation.engine._finalize_candidate`
remains the single construction point for those, so the evidence floor,
the confidence floor and the restricted-column authorization check all
apply here exactly as they do to every other rule.

**Everything a suggestion is grounded in is a fact this request already
computed** -- there is no new query, no new vector lookup and no LLM
call:

- **Result shape** -- `analytics.engine.compute_analytics_result`'s own
  `ResultShape` and findings. A `RANKING` finding is what grounds a
  "Top 5"; a `GROWTH` finding is what grounds a period comparison. The
  evidence attached to a suggestion is that finding's own
  `DATABASE_FACT` claim, reused verbatim rather than restated.
- **Available schema** -- the DDL of the tables retrieval already
  selected for this question
  (`AgentState["schema_tables"]`, synthesized by
  `db.schema_introspection` from live introspection). A suggestion that
  would need a temporal column, or a breakdown dimension, is only ever
  proposed when such a column genuinely exists there.
- **Approved metrics and dimensions** -- `AgentState["governing_metrics"]`
  / `["governing_dimensions"]`, which are `CONFIRMED_BUSINESS_TRUTH`
  published semantic-catalog entries (Prompt 9/10/37). A governed
  dimension is preferred over an arbitrary schema column, and names the
  chip using the business name a human approved rather than the raw
  column name.
- **The current question and the conversation so far** -- used to
  *suppress*, never to ground: a suggestion that restates what was just
  asked, or what was asked earlier in this conversation, is dropped.
  Question text is user input, so it is never evidence.

**"Never suggest what the data cannot answer" is structural here, not a
disclaimer.** A suggestion naming a column exists only because that
column was found in real introspected DDL; a "Top N" exists only because
a real ranking with more than N entries was computed. An empty result
(`ResultShape.EMPTY`, or zero rows) produces no suggestions at all --
there is nothing to drill into. When the analytics engine produced
nothing for this result, this module returns nothing rather than guessing
a shape.

**Restricted fields are filtered twice, and the first filter is
role-independent.** A column whose
`governance.classification.classify_field` classification is at or above
`DataClassification.CONFIDENTIAL` is never named in a suggestion, for any
caller -- a chip is this platform *proactively steering* someone toward a
field, which is a stronger act than reporting a statistic they already
asked for, and withholding it costs nothing (they can still type the
question). On top of that, every suggestion carries the column names it
references out to `recommendation.engine`, whose existing
`_authorized` gate re-checks them against
`Permission.VIEW_RESTRICTED_COLUMNS`. Note which classifier this uses:
`config/sensitive_columns.yaml` ships as an empty template by design, so
a filter reading only that file protects nothing on a real database (a
gap `docs/DATA_PRIVACY_GOVERNANCE_ENGINE.md` names explicitly) --
`governance.classification` is the layer that actually infers from the
column name, so it is the one consulted here.

**No SQL is produced, here or on the frontend.** A suggestion is a
natural-language question. Clicking a chip re-enters the ordinary
`POST /ask` path, so `agent.input_guard`, the governance request policy,
the SQL validator and the restricted-column gate all apply to it exactly
as they would to a typed question -- and the answer still requires an
explicit "Confirm and Run" before anything executes for the user.

Pure and deterministic: no I/O, no LLM, no clock, no randomness. The same
context always yields the same suggestions in the same order.
"""

from __future__ import annotations

import logging
import re

from analytics.models import AnalyticsFindingKind, AnalyticsResult, ResultShape
from pydantic import BaseModel, ConfigDict, Field

from agent.provenance import DataTruthLevel, ProvenancedClaim
from config.settings import Settings, get_settings
from db.column_formatting import format_column_label, is_probable_surrogate_key
from db.relationship_inference import type_family
from db.schema_introspection import extract_ddl_column_names, extract_ddl_column_types
from governance.classification import DataClassification, classify_field

logger = logging.getLogger(__name__)

#: A chip is a chip, not a sentence. Longer than this and it stops being
#: scannable at a glance, which is the entire point of the affordance, so
#: a rule that cannot phrase itself this briefly is dropped rather than
#: shown truncated (the prompt's own examples -- "Top 5", "Show trend",
#: "Break down by region" -- are all well inside it).
MAX_LABEL_CHARS = 24

#: At or above this classification a column is never named in a
#: suggestion, regardless of the caller's permissions -- see this
#: module's own docstring for why a proactive nudge is held to a stricter
#: line than a statistic the caller explicitly asked for.
SUGGESTION_CLASSIFICATION_CEILING = DataClassification.CONFIDENTIAL

#: Non-word runs, for the dedup/normalization key only -- never applied
#: to displayed text (`sanitizeLabel` on the frontend owns that).
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")

#: Type families a breakdown dimension may have. A numeric column is a
#: measure, not a dimension; a datetime column gets its own time-based
#: rule instead of being offered as a category.
_DIMENSION_TYPE_FAMILIES = frozenset({"text"})

#: Column-name tokens that mark a column as plumbing rather than a
#: business dimension, on top of `is_probable_surrogate_key`'s own
#: PK/FK-suffix check (which covers `*Id`/`*Key`).
_NON_DIMENSION_TOKENS = frozenset(
    {"guid", "uuid", "hash", "rowversion", "timestamp", "etag", "sk", "pk"}
)


class FollowUpSchemaTable(BaseModel):
    """One retrieved table's name and synthesized DDL -- the shape of
    `agent.state.TableSchema` minus the similarity score this module has
    no use for, so a caller outside the graph (a test, a future report
    job) need not fabricate one."""

    model_config = ConfigDict(frozen=True)

    table_name: str
    ddl: str


class FollowUpConcept(BaseModel):
    """One governed (`CONFIRMED_BUSINESS_TRUTH`, published) semantic-catalog
    concept, as `retrieval.retriever.extract_governing_metrics` /
    `extract_governing_dimensions` already return it.

    Attributes:
        business_name: The human-approved name -- what a chip is labelled
            with when this concept is used, in preference to a raw
            column name.
        column: The underlying schema column, when the catalog entry
            recorded one (`None` otherwise -- a metric entry typically
            records an expression instead). A dimension with no column
            cannot be verified against real schema, so it is not used.
    """

    model_config = ConfigDict(frozen=True)

    business_name: str
    column: str | None = None


class FollowUpContext(BaseModel):
    """Everything a follow-up suggestion may be grounded in, beyond the
    `AnalyticsResult` itself -- every field optional, so a caller with
    only part of it (a test, a non-graph caller) gets fewer suggestions
    rather than an error.

    Attributes:
        question: The question just answered. Used only to suppress a
            suggestion that restates it -- user input is never evidence.
        result_columns: The answered query's own result column names. A
            dimension already present here is already answered, so it is
            not suggested again.
        schema_tables: The tables retrieval selected for this question,
            with their introspected DDL -- the sole authority on which
            columns actually exist.
        governing_metrics: Published, approved metric definitions matched
            to this question.
        governing_dimensions: Published, approved dimension definitions
            matched to this question.
        prior_questions: Earlier questions in this conversation, oldest
            or newest first (order is irrelevant -- they are only ever
            matched against). Used to avoid re-suggesting ground already
            covered.
    """

    model_config = ConfigDict(frozen=True)

    question: str = ""
    result_columns: tuple[str, ...] = ()
    schema_tables: tuple[FollowUpSchemaTable, ...] = ()
    governing_metrics: tuple[FollowUpConcept, ...] = ()
    governing_dimensions: tuple[FollowUpConcept, ...] = ()
    prior_questions: tuple[str, ...] = ()


class FollowUpSuggestion(BaseModel):
    """One grounded follow-up, before `recommendation.engine` turns it
    into a `Recommendation`.

    Deliberately a separate public type rather than
    `recommendation.engine._Candidate` (private) or `Recommendation`
    (which only `_finalize_candidate` may construct): this module owns
    *which* follow-ups are grounded, and the engine owns whether a
    grounded candidate clears evidence, confidence and authorization.

    Attributes:
        label: The chip text -- at most `MAX_LABEL_CHARS` characters.
        question: The natural-language question a click submits. Never
            SQL.
        evidence: `DATABASE_FACT`/`CONFIRMED_BUSINESS_TRUTH` claims this
            suggestion is traceable to -- never empty (a suggestion with
            nothing behind it is not constructed at all).
        confidence: `0.0`-`1.0`, compared by the engine against
            `Settings.recommendation_min_confidence`.
        rule_name: Fully-qualified producing rule, for traceability.
        columns: Column names this suggestion references, handed to the
            engine's own restricted-column authorization gate.
        limitations: Disclosed caveats (e.g. that a column's business
            meaning was inferred from its name rather than governed).
    """

    model_config = ConfigDict(frozen=True)

    label: str = Field(max_length=MAX_LABEL_CHARS)
    question: str
    evidence: tuple[ProvenancedClaim, ...] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    rule_name: str
    columns: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


def _normalize(text: str) -> str:
    """A comparison key for "is this the same ask?" -- lowercased, with
    every non-alphanumeric run collapsed to a single space, so
    "Top 5 products" and "top-5 products?" compare equal. Used only for
    deduplication, never for display."""
    return _NON_WORD_RE.sub(" ", text.lower()).strip()


def _is_suggestible_column(table: str, column: str) -> bool:
    """False for any column this module refuses to name in a suggestion:
    one classified at or above `SUGGESTION_CLASSIFICATION_CEILING` -- see
    this module's docstring for why that line is drawn
    role-independently."""
    policy = classify_field(table, column)
    return policy.classification < SUGGESTION_CLASSIFICATION_CEILING


def _is_dimension_shaped(column: str, raw_type: str | None) -> bool:
    """True for a column that could meaningfully group a measure: a
    text-family type, not a surrogate key, not obvious plumbing.

    Reuses `db.column_formatting.is_probable_surrogate_key`'s
    last-token check (with no schema key set -- the caller has DDL, not
    `TableSchemaInfo`), which is deliberately token-based rather than
    `str.endswith`, so `Valid` is not mistaken for an id column.
    """
    if raw_type is None or type_family(raw_type) not in _DIMENSION_TYPE_FAMILIES:
        return False
    if is_probable_surrogate_key(column, set()):
        return False
    lowered = column.lower()
    return not any(token in lowered for token in _NON_DIMENSION_TOKENS)


def _schema_fact(table: str, column: str, raw_type: str) -> ProvenancedClaim:
    """A `DATABASE_FACT` claim that one column really exists, with its
    declared type.

    This is a fact, not an inference: `schema_tables`' DDL is synthesized
    by `db.schema_introspection` from a live `Inspector` pass over the
    real database, so "table X declares column Y of type Z" is read, not
    guessed.
    """
    return ProvenancedClaim(
        value=f"Table {table} declares column {column} of type {raw_type}.",
        level=DataTruthLevel.DATABASE_FACT,
        source="db.schema_introspection.introspect_schema",
    )


def _governed_concept_fact(concept: FollowUpConcept, kind: str) -> ProvenancedClaim:
    """A `CONFIRMED_BUSINESS_TRUTH` claim that a named concept is an
    approved, published catalog definition.

    `CONFIRMED_BUSINESS_TRUTH` is correct rather than generous here:
    `retrieval.retriever.extract_governing_metrics`/
    `extract_governing_dimensions` only ever return chunks rendered from
    a **published** `semantic.catalog` entry (a draft never reaches the
    vector store at all), which `semantic.catalog.status_to_truth_level`
    maps to exactly this level.
    """
    return ProvenancedClaim(
        value=f"'{concept.business_name}' is an approved {kind} definition.",
        level=DataTruthLevel.CONFIRMED_BUSINESS_TRUTH,
        source="semantic.catalog (published)",
    )


def _measure_phrase(
    context: FollowUpContext, analytics_result: AnalyticsResult
) -> tuple[str, ProvenancedClaim | None]:
    """What the follow-up should say it is measuring, and the fact that
    backs that name.

    Prefers a governed metric's approved business name (so an approved
    definition drives the wording, per Prompt 10's precedence rule), then
    the value column of a real ranking/growth finding, then a real
    numeric column the engine summarized (which is what names the measure
    for a `SCALAR` or `RAW_TABLE` result, where no ranking or growth
    finding exists), and only then a neutral fallback phrase with no
    column claim attached.
    """
    for metric in context.governing_metrics:
        name = metric.business_name.strip()
        if name:
            return name, _governed_concept_fact(metric, "metric")

    for finding in analytics_result.findings:
        stat = finding.ranking or finding.growth
        if stat is not None and stat.value_column:
            return format_column_label(stat.value_column), finding.claim

    for finding in analytics_result.findings:
        summary = finding.column_summary
        if summary is not None and summary.is_numeric and summary.column:
            return format_column_label(summary.column), finding.claim

    return "the results", None


def _available_dimension_columns(
    context: FollowUpContext,
) -> list[tuple[str, str, str]]:
    """Every `(table, column, raw_type)` in the retrieved schema that
    could group a measure and is safe to name -- excluding anything
    already in the answered query's own result columns (that breakdown
    has already been given) and anything classified too sensitive to
    suggest.

    Deterministic order: schema-table order, then DDL column order.
    """
    already_shown = {column.lower() for column in context.result_columns}
    available: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for table in context.schema_tables:
        types = extract_ddl_column_types(table.ddl)
        for column in extract_ddl_column_names(table.ddl):
            lowered = column.lower()
            if lowered in already_shown or lowered in seen:
                continue
            raw_type = types.get(column)
            if not _is_dimension_shaped(column, raw_type):
                continue
            if not _is_suggestible_column(table.table_name, column):
                logger.debug(
                    "[followups] not suggesting %s.%s: classified at or above %s",
                    table.table_name,
                    column,
                    SUGGESTION_CLASSIFICATION_CEILING.name,
                )
                continue
            # nosec B101 - narrowing only; `_is_dimension_shaped` already rejected
            # None on the line above (BP-004).
            assert raw_type is not None  # nosec B101
            seen.add(lowered)
            available.append((table.table_name, column, raw_type))
    return available


def _temporal_columns(context: FollowUpContext) -> list[tuple[str, str, str]]:
    """Every `(table, column, raw_type)` in the retrieved schema whose
    declared type is a datetime family and which is safe to name -- what
    grounds a "show the trend" suggestion in a real time axis rather than
    an assumption that one exists."""
    temporal: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for table in context.schema_tables:
        types = extract_ddl_column_types(table.ddl)
        for column in extract_ddl_column_names(table.ddl):
            lowered = column.lower()
            if lowered in seen:
                continue
            raw_type = types.get(column)
            if raw_type is None or type_family(raw_type) != "datetime":
                continue
            if not _is_suggestible_column(table.table_name, column):
                continue
            seen.add(lowered)
            temporal.append((table.table_name, column, raw_type))
    return temporal


# ---------------------------------------------------------------------------
# Rules. One function per suggestion family, each returning its own
# candidates in deterministic order; `build_follow_up_suggestions` runs
# them in a fixed sequence and does all deduplication/capping centrally.
# ---------------------------------------------------------------------------

_RULE_PREFIX = "recommendation.followups"


def _governed_dimension_suggestions(
    context: FollowUpContext, measure: str, measure_fact: ProvenancedClaim | None
) -> list[FollowUpSuggestion]:
    """A breakdown by a **governed** dimension -- the highest-confidence
    breakdown this module offers, because both halves are confirmed: the
    dimension is a published catalog definition, and its column is
    verified to exist in the live introspected schema.

    Deliberately does *not* apply `_is_dimension_shaped`, unlike
    `_schema_dimension_suggestions` below: that check exists to guess
    whether an arbitrary column could group a measure, and a published
    catalog entry has already answered that question with a human
    review. Requiring a text type here would reject a perfectly good
    approved dimension on an integer column ("Fiscal Year") or a date one
    ("Order Month"). The sensitivity ceiling still applies -- approval is
    about business meaning, not about disclosure.
    """
    suggestions: list[FollowUpSuggestion] = []
    already_shown = {column.lower() for column in context.result_columns}
    real_columns = {
        column.lower(): (table.table_name, raw_type)
        for table in context.schema_tables
        for column, raw_type in extract_ddl_column_types(table.ddl).items()
    }

    for dimension in context.governing_dimensions:
        column = (dimension.column or "").strip()
        name = dimension.business_name.strip()
        if not column or not name:
            continue
        located = real_columns.get(column.lower())
        if located is None or column.lower() in already_shown:
            continue
        table_name, raw_type = located
        if not _is_suggestible_column(table_name, column):
            continue
        label = f"By {name}"
        if len(label) > MAX_LABEL_CHARS:
            continue
        evidence = [
            _governed_concept_fact(dimension, "dimension"),
            _schema_fact(table_name, column, raw_type),
        ]
        if measure_fact is not None:
            evidence.append(measure_fact)
        suggestions.append(
            FollowUpSuggestion(
                label=label,
                question=f"Break down {measure} by {name}",
                evidence=tuple(evidence),
                confidence=0.85,
                rule_name=f"{_RULE_PREFIX}.GovernedDimensionBreakdownRule",
                columns=(column,),
            )
        )
    return suggestions


def _schema_dimension_suggestions(
    context: FollowUpContext,
    measure: str,
    measure_fact: ProvenancedClaim | None,
    limit: int,
) -> list[FollowUpSuggestion]:
    """A breakdown by a real, dimension-shaped schema column that no
    governed dimension covers.

    Lower confidence than the governed rule above, and it says why: the
    column provably exists, but that it is a *business* dimension is
    inferred from its name and type, not approved by anyone.
    """
    suggestions: list[FollowUpSuggestion] = []
    for table_name, column, raw_type in _available_dimension_columns(context)[:limit]:
        name = format_column_label(column)
        label = f"By {name}"
        if len(label) > MAX_LABEL_CHARS:
            continue
        evidence = [_schema_fact(table_name, column, raw_type)]
        if measure_fact is not None:
            evidence.append(measure_fact)
        suggestions.append(
            FollowUpSuggestion(
                label=label,
                question=f"Break down {measure} by {name}",
                evidence=tuple(evidence),
                confidence=0.6,
                rule_name=f"{_RULE_PREFIX}.SchemaDimensionBreakdownRule",
                columns=(column,),
                limitations=(
                    f"That {column} is a business dimension is inferred from its name and "
                    "type, not from an approved semantic-catalog definition.",
                ),
            )
        )
    return suggestions


def _trend_suggestions(
    context: FollowUpContext,
    analytics_result: AnalyticsResult,
    measure: str,
    measure_fact: ProvenancedClaim | None,
) -> list[FollowUpSuggestion]:
    """ "Show trend" -- only when the result is *not* already a time
    series (otherwise the trend is what they are looking at) and a real
    datetime column exists to trend over."""
    if analytics_result.shape == ResultShape.TIME_SERIES:
        return []
    temporal = _temporal_columns(context)
    if not temporal:
        return []
    table_name, column, raw_type = temporal[0]
    evidence = [_schema_fact(table_name, column, raw_type)]
    if measure_fact is not None:
        evidence.append(measure_fact)
    return [
        FollowUpSuggestion(
            label="Show trend",
            question=f"Show {measure} over time",
            evidence=tuple(evidence),
            confidence=0.7,
            rule_name=f"{_RULE_PREFIX}.TrendOverTimeRule",
            columns=(column,),
        )
    ]


def _period_comparison_suggestions(
    analytics_result: AnalyticsResult, measure: str
) -> list[FollowUpSuggestion]:
    """A period-over-period comparison -- grounded in a real `GROWTH`
    finding, which only exists when the analytics engine actually
    recognised this result's labels as periods. That is what keeps this
    from being offered for a result whose "date" is really free text."""
    for finding in analytics_result.findings:
        if finding.kind != AnalyticsFindingKind.GROWTH or finding.growth is None:
            continue
        if len(finding.growth.points) < 2:
            continue
        return [
            FollowUpSuggestion(
                label="Compare periods",
                question=f"Compare {measure} with the previous period",
                evidence=(finding.claim,),
                confidence=0.75,
                rule_name=f"{_RULE_PREFIX}.PeriodComparisonRule",
            )
        ]
    return []


def _top_n_suggestions(analytics_result: AnalyticsResult, top_n: int) -> list[FollowUpSuggestion]:
    """ "Top N" -- only when a real `RANKING` finding has more than N
    entries, so narrowing to N genuinely drops something. A ranking of
    three rows is already its own top 5."""
    for finding in analytics_result.findings:
        if finding.kind != AnalyticsFindingKind.RANKING or finding.ranking is None:
            continue
        ranking = finding.ranking
        if len(ranking.entries) <= top_n:
            continue
        label_name = format_column_label(ranking.label_column)
        value_name = format_column_label(ranking.value_column)
        label = f"Top {top_n}"
        return [
            FollowUpSuggestion(
                label=label,
                question=f"Top {top_n} {label_name} by {value_name}",
                evidence=(finding.claim,),
                confidence=0.8,
                rule_name=f"{_RULE_PREFIX}.TopRankingRule",
                columns=(ranking.label_column, ranking.value_column),
            )
        ]
    return []


def build_follow_up_suggestions(
    context: FollowUpContext,
    analytics_result: AnalyticsResult | None,
    settings: Settings | None = None,
) -> tuple[FollowUpSuggestion, ...]:
    """Builds the grounded follow-up suggestions for one answered turn.

    Args:
        context: See `FollowUpContext` -- the question, result columns,
            retrieved schema, governed metrics/dimensions and prior
            questions. Every field optional; less context yields fewer
            suggestions, never an error.
        analytics_result: `analytics.engine.compute_analytics_result`'s
            output for this result. `None` (the analytics engine disabled
            or failed) yields no suggestions: result shape is a required
            grounding input, and guessing it would be exactly the
            unsupported suggestion this module exists to avoid.
        settings: Defaults to `config.settings.get_settings()`.

    Returns:
        At most `Settings.max_follow_up_suggestions` suggestions, in
        deterministic rule order, each with non-empty evidence and a
        label no longer than `MAX_LABEL_CHARS`. Empty for an empty
        result, a disabled feature flag, or a turn where nothing could be
        grounded.
    """
    settings = settings or get_settings()
    if not settings.enable_follow_up_suggestions:
        return ()
    if analytics_result is None:
        logger.debug("[followups] no analytics result -- result shape cannot be grounded")
        return ()
    if analytics_result.shape == ResultShape.EMPTY or analytics_result.row_count <= 0:
        logger.debug("[followups] empty result -- nothing to drill into")
        return ()

    measure, measure_fact = _measure_phrase(context, analytics_result)
    top_n = settings.follow_up_top_n

    # Fixed order: the most specific, highest-confidence families first,
    # so that when the cap bites it drops the weakest suggestion rather
    # than whichever rule happened to run last.
    ordered: list[FollowUpSuggestion] = []
    ordered.extend(_top_n_suggestions(analytics_result, top_n))
    ordered.extend(_period_comparison_suggestions(analytics_result, measure))
    ordered.extend(_governed_dimension_suggestions(context, measure, measure_fact))
    ordered.extend(_trend_suggestions(context, analytics_result, measure, measure_fact))
    ordered.extend(
        _schema_dimension_suggestions(
            context, measure, measure_fact, settings.max_follow_up_suggestions
        )
    )

    # Suppression: anything already asked (this question or earlier in the
    # conversation), and anything a higher-priority rule already covered.
    # Question text is only ever used here -- never as evidence.
    asked = {_normalize(context.question)} | {_normalize(q) for q in context.prior_questions}
    asked.discard("")
    seen_questions: set[str] = set()
    seen_labels: set[str] = set()
    #: Columns a kept suggestion already offers to slice by. Text-level
    #: deduplication alone is not enough: the governed and schema
    #: breakdown rules can propose the *same column* under two different
    #: names ("By Sales Region" from the catalog, "By Region" from the
    #: column name), which reads as two choices while being one. The
    #: governed rule runs first, so the approved name is the one kept.
    claimed_columns: set[str] = set()

    kept: list[FollowUpSuggestion] = []
    for suggestion in ordered:
        # Checked before appending, not after: a post-append check lets
        # the first suggestion through even when an operator has set the
        # cap to 0 to switch the chips off by configuration.
        if len(kept) >= settings.max_follow_up_suggestions:
            break
        question_key = _normalize(suggestion.question)
        label_key = _normalize(suggestion.label)
        if not question_key or question_key in asked or question_key in seen_questions:
            continue
        if label_key in seen_labels:
            continue
        columns = {column.lower() for column in suggestion.columns}
        # `all`, not `any`: a suggestion that shares a measure column with
        # an earlier one but introduces a genuinely new dimension is not a
        # repeat.
        if columns and columns <= claimed_columns:
            continue
        seen_questions.add(question_key)
        seen_labels.add(label_key)
        claimed_columns |= columns
        kept.append(suggestion)

    return tuple(kept)
