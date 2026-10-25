"""Shared state for the LangGraph Text-to-SQL agent.

Every node function takes an `AgentState` and returns a partial dict of
updates; LangGraph merges those updates into the running state between node
executions. `error_history` uses an `operator.add` reducer so that each
node's contribution is *appended* to the running list rather than
overwriting it -- this is what lets `generate_sql` see the full trail of
prior failures on a retry.
"""

from __future__ import annotations

import operator

# typing_extensions.TypedDict (not stdlib typing.TypedDict) is required here:
# this TypedDict is used as a LangGraph node's state, and LangGraph's
# StateGraph.get_graph() triggers pydantic v2 to build a schema for it --
# which raises PydanticUserError on Python < 3.12, where stdlib TypedDict
# lacks __orig_bases__ (added in CPython 3.12). Production runs Python 3.11.
#
# `NotRequired` comes from stdlib `typing` (3.11+) rather than from
# `typing_extensions` alongside `TypedDict`: the reason above is specific
# to `TypedDict`, and ruff's UP035 flags the `typing_extensions` import of
# anything stdlib already provides. The split is deliberate, not an
# oversight -- do not "tidy" `TypedDict` back to stdlib with it.
from typing import Annotated, Literal, NotRequired

from typing_extensions import TypedDict

from agent.insight import ResultSummary
from db.query_cost import CostEstimate

AgentStatus = Literal[
    "pending",
    "sanitizing_input",
    "classifying_followup",
    "retrieving_schema",
    "generating",
    "reviewing",
    "validating",
    "estimating_cost",
    "executing",
    "succeeded",
    "failed",
    "needs_clarification",
    "rejected",
    "rate_limited",
]

# Why a question never reached generation at all -- either the input gate
# (`agent.input_guard.check_input`) rejected it before any LLM call, or the
# model itself refused via `agent.llm_client.OFF_TOPIC_SENTINEL` (the
# defense-in-depth backstop for anything the gate's cheaper pre-filter
# missed). Both land on the same "rejected" status/reason space so the UI
# has one place to look, regardless of which layer caught it.
RejectionReason = Literal["too_long", "empty", "injection_detected", "off_topic", "policy_refused"]


class TableSchema(TypedDict):
    """One retrieved table's DDL plus how relevant it was to the question."""

    table_name: str
    ddl: str
    similarity_score: float


class GoldenExample(TypedDict):
    """One retrieved human-approved (question, SQL) pair from the golden
    dataset (see `embeddings.golden_examples`) -- same shape/spirit as
    `TableSchema` above (a retrieved, scored search result), not the raw
    storage record."""

    question: str
    sql: str
    similarity_score: float


class ConversationExchange(TypedDict):
    """One prior turn's resolved shape, for follow-up reference resolution.

    Deliberately holds *structure*, not result data: the question text, the
    SQL that was generated for it, and which tables that SQL ended up using
    -- never result rows. This is what `agent.followup.classify_followup`
    and `generate_sql_node`'s follow-up prompt block are allowed to see; it
    keeps the reference-resolution prompt bounded regardless of how large
    the actual result set was (see CLAUDE.md's constraint on this).

    Built by the caller from the same session query history the UI's own
    history panel reads -- one source of truth for "what was asked and
    what happened," not a parallel state. On the web path that caller is
    `frontend/src/lib/history.ts::buildConversationHistory`; for a script
    driving the agent it is `eval/history.py`. (The `ui/session_history.py`
    this docstring used to name was the Streamlit app's, removed when the
    React dashboard reached parity -- see CLAUDE.md §1.)
    """

    question: str
    sql: str | None
    tables: list[str]
    status: str  # "succeeded" | "failed" | "needs_clarification"
    # The assistant's own reply for this turn, when the caller recorded one.
    #
    # `NotRequired` rather than a plain field: this TypedDict is `total=True`
    # and is constructed positionally-by-keyword in a dozen places (the API
    # layer, `eval/history.py`, every test fixture), so a required key would
    # be a breaking change to all of them (CLAUDE.md 6.3 -- additive with a
    # default, never a break).
    #
    # Still structure, not result data: a short natural-language answer, the
    # same thing the transcript already shows. Result *rows* remain excluded
    # by design (see this class's docstring) -- what this adds is the text a
    # refinement acts on ("make it more professional" has nothing to revise
    # without it) and the entities an answer named that the question did not.
    #
    # `agent.orchestrator.nodes._recent_turns_text` has read
    # `exchange.get("answer", "")` since the router was written; until this
    # field existed that read always returned "", so the router's own
    # "Conversation so far" block showed questions with no answers.
    answer: NotRequired[str | None]


StageOutcome = Literal["ok", "error", "no_op"]
"""What one node call did, as a fact about that call alone.

- ``"ok"``: returned normally having produced at least one non-null value.
- ``"error"``: recorded a failure or refusal -- it set
  ``last_error_category``, or flipped ``status`` to a terminal value.
- ``"no_op"``: returned normally having produced nothing. This is what a
  pass-through node looks like when its feature flag is off (e.g.
  ``plan_query_node`` with ``ENABLE_QUERY_PLANNING=false`` returns only
  ``status``), and also what an enabled node that genuinely found nothing
  looks like (``retrieve_golden_examples_node`` with no match above the
  similarity floor returns ``{"golden_examples": None, ...}``). The two
  are deliberately not distinguished: neither contributed anything to this
  question, which is the only distinction a reader of a timeline needs,
  and telling them apart would require every node to report its own
  skip reason -- exactly the per-node coupling `_timed_node` exists to
  avoid.

A node that *raises* produces no ``StageTiming`` at all, since
`agent.nodes._timed_node` appends the record only after the node returns.
So the presence of a record already means "this call completed"; this
field says what it completed with.
"""


class StageTiming(TypedDict):
    """One node call's wall-clock duration and outcome.

    One entry per node *call* (not per attempt) -- a question that retries
    twice produces two `generate_sql`/`validate_sql`/`execute_sql` entries,
    which is the point: it lets a profiling script sum "total time spent in
    stage X" across the whole run, including retries, rather than only
    seeing the last attempt's cost.

    `outcome`/`error_category` are read from the node's *own returned dict*,
    never from the merged state -- `last_error_category` persists in state
    across retries, so a merged read would attribute a previous attempt's
    failure to whichever node happened to run next. See `StageOutcome`.
    """

    stage: str  # "retrieve_schema" | "generate_sql" | "validate_sql" | "execute_sql"
    attempt: int
    duration_ms: float
    outcome: StageOutcome
    # The node's own `last_error_category` when it set one, else the
    # terminal `status` name it flipped to ("rejected", "rate_limited",
    # ...). None whenever `outcome` is not "error". Always one of this
    # application's own fixed category names -- never a driver message,
    # never anything derived from the question or the data.
    error_category: str | None


class AttemptRecord(TypedDict):
    """One full generate -> validate -> execute cycle's outcome.

    Exactly one entry is appended per attempt, at the point that attempt's
    fate becomes known (a validate/execute failure, or a final execute
    success) -- not per node call -- so the list reads as a plain timeline:
    "Attempt 1: missing_reference (retrying). Attempt 2: succeeded."

    Attributes:
        attempt: 1-indexed attempt number.
        sql: The SQL text this attempt validated/executed (None if the
            attempt never got that far, e.g. the LLM call itself failed).
        outcome: One of "succeeded", "safety_violation", "empty",
            "parse_error", "nested_aggregate", "restricted_column",
            "high_cost", "syntax_error", "missing_reference",
            "aggregate_nesting", "plan_not_satisfied", "timeout",
            "unknown_error", "schema_retrieval_error", "llm_error",
            "off_topic", "rate_limited", "database_busy" (Prompt 22 --
            the per-database execution concurrency limiter rejected this
            attempt before any query was sent; see
            `agent.rate_limit.get_database_execution_limiter`).
        error: The raw error message (validator or database), None on success.
        will_retry: Whether the graph will attempt another generation cycle.
    """

    attempt: int
    sql: str | None
    outcome: str
    error: str | None
    will_retry: bool


class ContextResolutionRecord(TypedDict):
    """One question's context resolution, flattened for transport.

    A plain `TypedDict` of primitives rather than the
    `agent.context_resolution.ResolvedContext` dataclass itself, for two
    reasons. It lands in `AgentState`, whose other structured values are
    all `TypedDict`s for the same LangGraph/pydantic-schema reason
    `ConversationExchange` is one; and it is surfaced through
    `api.schemas.AskResponse`, which cannot carry a dataclass holding a
    `MemorySnapshot` of further dataclasses.

    Attributes:
        relation: `agent.context_resolution.ContextRelation`'s value --
            "new_topic" | "followup" | "refinement" | "correction" |
            "topic_return" | "ambiguous" | "missing_context".
        resolved_question: The standalone form actually used downstream.
        rewritten: Whether `resolved_question` differs from what was typed.
        antecedent_question: The prior turn's question text this resolved
            against, or None. Distinct from
            `AgentState["followup_resolved_against"]`, which carries that
            turn's whole exchange; this is the one field a log line and a
            badge need.
        confidence: Evidence for the resolution, in [0, 1]. **Never a
            probability that the answer is correct** -- the same caveat
            `AskResponse.source_confidence` carries (CLAUDE.md 13).
        source: "deterministic" | "llm" | "disabled" -- which tier decided.
        carried_constraints: Constraints in force, as written.
        replaced_constraints: Constraints a correction displaced. Reported
            rather than dropped silently, so a narrowed answer is explicable.
        candidate_topics: On an ambiguous reference, the competing topics
            the clarifying question named.
        signals: Deterministic signal names that fired, for inspection.
    """

    relation: str
    resolved_question: str
    rewritten: bool
    antecedent_question: str | None
    confidence: float
    source: str
    carried_constraints: list[str]
    replaced_constraints: list[str]
    candidate_topics: list[str]
    signals: list[str]


class AgentState(TypedDict, total=False):
    """Full state threaded through the LangGraph graph.

    All fields are optional (`total=False`) because each node only sets the
    fields it's responsible for; the graph accumulates state as it runs.
    """

    # Input
    question: str

    # Input, set once by the caller (see agent.graph.run_agent /
    # agent.orchestrator.graph.run_orchestrated) from the authenticated
    # caller's `security.oidc.AuthIdentity.roles` (api/auth.py) -- empty
    # tuple for an unauthenticated/"none"-auth-mode caller or a caller
    # script that doesn't pass one (e.g. eval/runner.py), which resolves to
    # the same "no elevated permissions" outcome `agent.authz`'s default-
    # deny role map already gives an empty role set. Read by
    # `validate_sql_node` (restricted-column gate) and, on the
    # orchestrator's `OrchestratorState` superset, by
    # `agent.orchestrator.nodes`'s per-source authorization check (2026
    # Phase 2 security review) -- never mutated by a node, just read.
    caller_roles: tuple[str, ...]

    # Input, set once by the caller alongside caller_roles -- the
    # authenticated caller's `security.oidc.AuthIdentity.subject`. `None`
    # for the "none"/"static_token" auth modes (no real per-user identity
    # exists to key anything on -- see api/auth.py) or for a caller that
    # never passed one. 2026 Phase 2 security review: used by
    # `agent.orchestrator.nodes.router_node` to scope the session-level
    # expensive-source cost ceiling to a real, authenticated identity when
    # one exists, instead of only the client-supplied, unauthenticated
    # `OrchestratorState["session_id"]` (trivially resettable by minting a
    # fresh one -- see that field's own docstring).
    caller_subject: str | None

    # Set by sanitize_input_node -- None/"passed" means the question is
    # clean and may proceed. On rejection, status becomes "rejected" and
    # rejection_message holds the standardized, non-technical text shown to
    # the user (see agent.input_guard._MESSAGES) -- never the raw reason or
    # which pattern matched, per CLAUDE.md's "don't confirm what was
    # detected" rule. `question` itself is overwritten with the normalized
    # (NFKC + confusables-folded + control-stripped) text on a pass, so
    # every downstream node operates on sanitized text.
    rejection_reason: RejectionReason | None
    rejection_message: str | None

    # Set by generate_sql_node when the process-wide LLM-call rate limiter
    # (agent.rate_limit) denies a generation attempt -- including a retry
    # attempt, not just the first one. Distinct from "rejected": this is a
    # temporary, systemic load condition, not a judgment about the question
    # itself, so it gets its own status/message rather than reusing
    # rejection_reason/rejection_message.
    rate_limit_message: str | None

    # Input, set once by the caller (see agent.graph.run_agent) from the
    # UI's session query history, capped to the last few exchanges -- never
    # mutated by a node during a single run, just read by
    # classify_followup_node / retrieve_schema_node / generate_sql_node.
    conversation_history: list[ConversationExchange]

    # Set by classify_followup_node
    followup_classification: Literal["standalone", "followup", "ambiguous"] | None
    # The specific prior exchange a "followup" classification was resolved
    # against (the most recent one in conversation_history) -- surfaced in
    # the UI as "Following up on: ..." so a wrong interpretation is visible
    # before the user runs anything.
    followup_resolved_against: ConversationExchange | None
    # Set only when status becomes "needs_clarification" -- a plain-language
    # explanation of why the agent couldn't tell what was being asked,
    # mirroring failure_explanation's role for the "failed" status.
    clarification_message: str | None

    # --- Conversational context resolution (agent/context_resolution.py) ---
    # Set by classify_followup_node alongside the three legacy fields above,
    # which are derived from this one (`ContextRelation.legacy_classification`)
    # rather than computed separately -- one owner per fact (CLAUDE.md 4.8).
    #
    # The question in standalone form: pronouns substituted for the entity
    # they refer to, the referenced topic quoted, and any constraint still in
    # force appended. `retrieve_schema_node` embeds it, `generate_sql_node`
    # puts it in the prompt, and the orchestrator routes and searches on it.
    # Equal to `question` whenever nothing was carried, so every consumer can
    # read it unconditionally -- see `agent.nodes.retrieval_question`.
    #
    # `question` itself is never replaced with it. The transcript shows what
    # the person typed, and a rewrite is an internal processing form.
    resolved_question: str | None
    # The full resolution, as a flat record for the API and the UI -- see
    # `ContextResolutionRecord`.
    context_resolution: ContextResolutionRecord | None

    # Set by retrieve_schema on its first pass through (see that node's
    # docstring) via embeddings.retriever.select_database -- which
    # configured database (Settings.databases[i].name) this question was
    # auto-routed to. Read, never re-selected, on the one retry path that
    # re-enters retrieve_schema (execute_sql's "missing_reference" retry):
    # a retry must keep targeting the same database, not silently jump to
    # a different one mid-attempt. validate_sql/estimate_query_cost/
    # execute_sql all resolve their dialect/engine from this via
    # db.connection.get_connection(settings, state["selected_database"]).
    selected_database: str | None

    # Input, set once by the caller (`agent.graph.run_agent`) via
    # `agent.model_registry.validate_model_selection` -- the Ollama model
    # name to use for every LLM call this run makes (generation, planning,
    # review, insight). Always a validated, allowed model name by the time
    # it lands here (an invalid caller-supplied model never reaches the
    # graph at all -- see `api/main.py`'s `/ask` handler, which validates
    # before ever calling `run_agent`), never a raw, unchecked string.
    # Deliberately request-scoped, exactly like `selected_database` above --
    # never a process-global -- so one caller's model choice can never leak
    # into a concurrent request for a different one. `agent.nodes` reads
    # this (never re-selects it) and passes it straight through to
    # `agent.llm_client`'s `model=` parameter on every call; `None` there
    # means "use `Settings.ollama_model`," the pre-existing default
    # behavior, so a caller that never sets this field (every caller before
    # this feature existed, and any current caller that omits
    # `AskRequest.model`) sees zero change. Deliberately never changes
    # per-attempt: a retry keeps using the same model the first attempt did,
    # for the same reason a retry keeps targeting the same
    # `selected_database` -- switching models mid-question would make the
    # error-feedback retry loop's "learn from the last mistake" premise
    # incoherent. Every LLM call for one question -- generation, the
    # up-front plan, the plan-conformance review, and the post-execution
    # insight -- intentionally uses this same one model, never a mix; see
    # `agent.llm_client.generate_insight_from_llm`'s own docstring for why.
    selected_model: str | None

    # Input, set once by the caller (`agent.graph.run_agent` /
    # `agent.orchestrator.graph.run_orchestrated`) via
    # `security.tenancy.resolve_tenant_id_for_identity` -- which tenant the
    # authenticated caller belongs to. `None` for an anonymous/no-identity
    # caller (mirrors `caller_subject` above). Always
    # `security.tenancy.DEFAULT_TENANT_ID` today, since this codebase is
    # explicitly single-tenant by design (see `security/tenancy.py`'s own
    # module docstring for the full decision record) -- introduced here as
    # plumbing only, ahead of any real enforcement, so a future prompt that
    # adds genuine multi-tenant checks doesn't need another `AgentState`
    # migration to get a tenant id to check against. Deliberately
    # request-scoped, exactly like `selected_database`/`selected_model`
    # above -- never a process-global -- so one caller's tenant can never
    # leak into a concurrent request for a different one. Read, never
    # re-resolved, by any node that comes to depend on it later; today,
    # nothing reads it -- the only real enforcement of tenant scoping in
    # this codebase remains `identity.share_policy.authorize_share_action`,
    # which resolves its own tenant id independently via
    # `security.tenancy.resolve_actor_tenant_id` and does not read this
    # field.
    tenant_id: str | None

    # Set by retrieve_schema
    schema_tables: list[TableSchema]
    schema_context_text: str

    # Set by `enforce_display_names_node`. Key columns it rewrote into a
    # joined display name, e.g. ["ProductKey"] -- recorded so a reader can
    # see that the SQL shown is not byte-for-byte what the model wrote,
    # and why. Empty when nothing needed repairing, which is the common
    # case for a query that already selects names.
    display_names_repaired: list[str]

    # An honest disclosure when a result is still labelled by a key
    # because the related dimension has no readable name column at all.
    # Surfaced to the caller rather than silently returning the key --
    # a fabricated label would be worse (CLAUDE.md 4.4). None when every
    # label is readable.
    display_name_notice: str | None

    # Set by retrieve_golden_examples_node, which runs immediately after
    # retrieve_schema (and before plan_query) -- the best-matching
    # human-approved (question, SQL) pairs for this database's golden
    # dataset (see embeddings.golden_examples), or None if the feature is
    # disabled (Settings.enable_golden_examples), the store is empty/
    # unreachable (fails open, same as plan_query_node), or nothing cleared
    # Settings.golden_examples_min_similarity. Reused unchanged across every
    # generate_sql retry for this question, exactly like query_plan below
    # (only recomputed if retrieve_schema itself reruns) -- injected into
    # generate_sql's prompt via agent.llm_client._build_golden_examples_block.
    golden_examples: list[GoldenExample] | None

    # Set by retrieve_business_context_node, which runs between
    # retrieve_golden_examples and plan_query (see agent/graph.py) -- the
    # ranked business-context chunks (retrieval.models.ScoredChunk.to_context_dict()
    # shape) retrieval.retriever.retrieve_business_context found for this
    # question: table/column/relationship descriptions, glossary terms,
    # metric definitions, curated SQL examples, and documentation snippets.
    # Always a plain list of dicts, never the pydantic Chunk/ScoredChunk
    # objects themselves -- same "TypedDict state holds plain dicts, not
    # model instances" convention TableSchema/AttemptRecord already follow.
    # Empty list (not None) when retrieval found nothing, was disabled, or
    # failed -- see retrieval_warnings below for *why* it's empty. Reused
    # unchanged across every generate_sql retry for this question (only
    # recomputed if retrieve_schema itself reruns), exactly like
    # golden_examples/query_plan above -- injected into generate_sql's
    # prompt via agent.llm_client._build_business_context_block.
    retrieved_context: list[dict]
    # The exact text retrieval.retriever.retrieve_business_context embedded
    # as its query -- normally just `question` itself, kept separately on
    # state (rather than re-read from `question`) so a future caller can
    # tell "this is literally what was searched for" apart from the
    # original question text without assuming the two always match.
    retrieval_query: str | None
    # chunk_id of every chunk that made it into retrieved_context, in the
    # same order -- for traceability (e.g. an admin UI or eval script
    # tying a generation back to exactly which knowledge-base entries
    # informed it) independent of parsing retrieved_context itself.
    retrieval_sources: list[str]
    # Non-empty whenever business-context retrieval degraded for any reason
    # (disabled, empty/missing collection, embedding failure, vector-store
    # failure, or nothing cleared the similarity threshold) -- see
    # retrieval.retriever.retrieve_business_context's docstring for the
    # full fallback contract. Never a reason status becomes "failed":
    # retrieval is an accuracy aid, exactly like golden_examples/query_plan.
    retrieval_warnings: list[str]
    # Observability-only counters from the retrieval call (candidate/final
    # chunk counts, per-type breakdown, duration_ms) -- never read by any
    # node's own logic, purely for logging/debugging a retrieval decision,
    # the same role agent.state.DatabaseSelection.scores_by_db plays for
    # multi-database routing.
    retrieval_metadata: dict
    # Set by retrieve_business_context_node alongside retrieved_context --
    # Prompt 10 (10_GOVERNED_METRICS_CONTRACT.md): the subset of
    # retrieved_context that is specifically a PUBLISHED, METRIC-typed
    # governed catalog entry (retrieval.retriever.extract_governing_
    # metrics), i.e. a CONFIRMED_BUSINESS_TRUTH definition that must take
    # precedence over anything generate_sql would otherwise invent for a
    # named KPI. Empty list (not None) when no governing metric matched
    # this question -- the common case. Reused unchanged across every
    # generate_sql retry, exactly like retrieved_context itself. Injected
    # into generate_sql's prompt via agent.llm_client
    # ._build_mandatory_metrics_block and checked against the generated
    # SQL by review_metric_conformance_node below.
    governing_metrics: list[dict]
    # Set by retrieve_business_context_node alongside governing_metrics --
    # the same filter applied to PUBLISHED, DIMENSION-typed governed
    # catalog entries instead (retrieval.retriever
    # .extract_governing_dimensions, added by Prompt 37 and until now
    # without a live caller). Read only by generate_recommendations_node,
    # which prefers an approved dimension's own business name over a raw
    # schema column name when suggesting a follow-up breakdown
    # (recommendation.followups). Empty list (not None) when no governed
    # dimension matched -- the common case. Never influences sql/
    # result_rows: unlike governing_metrics, a governed dimension places
    # no mandatory constraint on generation.
    governing_dimensions: list[dict]

    # Set by classify_analytical_intent_node (Prompt 11,
    # 11_ANALYTICAL_INTENT_CONTRACT.md), which runs between
    # retrieve_business_context and plan_query, on every question
    # (unlike query_plan below, which is gated by agent.complexity's
    # signals) -- a structured judgment of what *kind* of analytical
    # question this is (agent.intent.AnalyticalIntentType), with
    # confidence, metric/dimension/filter candidates, a time requirement,
    # a comparison description, an expected result shape, and ambiguity
    # flags. Always a plain dict (agent.intent
    # .AnalyticalIntentClassification.model_dump()'s output, matching this
    # TypedDict's established "plain dicts, not model instances"
    # convention for retrieved_context/query_plan-shaped fields), never
    # the pydantic model itself. None when Settings
    # .enable_intent_classification is False, Ollama is unreachable, or
    # the response was unparseable -- a fail-open contract identical to
    # query_plan's own, and the thing that makes "existing fallback
    # behavior is preserved" true by construction: plan_query_node's gate
    # degrades to exactly agent.complexity's signals alone whenever this
    # is None. Always AI_INFERENCE (see agent.provenance.DataTruthLevel)
    # -- never promoted, and never itself a security/authorization
    # control (see classify_analytical_intent_node's own docstring).
    # metric_candidates here is a *different*, unconfirmed thing from
    # governing_metrics above -- raw phrases pulled from the question's
    # own text, never conflated with that CONFIRMED_BUSINESS_TRUTH set.
    analytical_intent: dict | None

    # Set by build_analytical_plan_node (Prompt 12,
    # 12_ANALYTICAL_PLANNING_CONTRACT.md), which runs between
    # classify_analytical_intent and plan_query -- a structured,
    # DETERMINISTICALLY RE-VERIFIED breakdown of what the eventual SQL
    # must compute (metric(s), dimensions, filters, time range, grain,
    # comparison, ranking, sort, limit, required database capabilities --
    # see agent.analytical_plan.AnalyticalPlan), or None. Always a plain
    # dict (AnalyticalPlan.model_dump()'s output, matching this
    # TypedDict's established "plain dicts, not model instances"
    # convention), never the pydantic model itself. None whenever
    # Settings.enable_analytical_planning is False, plan_query_node's own
    # gate (complexity_signals or intent_triggers_planning) doesn't fire,
    # Ollama is unreachable, the response is unparseable, OR --
    # critically, unlike every other advisory field on this state --
    # agent.plan_validator.validate_plan found any violation at all (an
    # unknown table/column, no FK-declared relationship path between the
    # plan's own tables, a restricted column the caller may not view, an
    # infeasible time range, or a database-capability mismatch): an
    # invalid plan is discarded entirely, never partially trusted, and
    # this falls back to plan_query_node's unmodified pre-Prompt-12
    # free-text planning (see that node's own docstring for the exact
    # three-tier fallback: validated structured plan -> free-text
    # query_plan -> direct generation). Reused unchanged across every
    # generate_sql retry for this question (only recomputed if
    # retrieve_schema itself reruns), exactly like query_plan below --
    # injected into generate_sql's prompt via agent.llm_client
    # ._build_analytical_plan_block, which supersedes query_plan's own
    # generic block when this is set (query_plan is still populated
    # alongside it, via agent.analytical_plan.render_plan_as_steps, so
    # review_sql_node's existing plan-conformance check keeps working
    # unmodified against a plan now known to be deterministically valid).
    analytical_plan: dict | None
    # Observability only -- the violations agent.plan_validator
    # .validate_plan found when a candidate plan was rejected (see
    # agent.plan_validator.PlanViolation), or None when no plan was built
    # at all, a plan passed validation outright, or Ollama/parsing failed
    # before validation ever ran. Never read by any node's own logic,
    # purely for logging/debugging a planning decision -- the same role
    # retrieval_metadata plays for business-context retrieval above.
    analytical_plan_violations: list[dict] | None

    # Set by plan_query_node, which runs between retrieve_schema and
    # generate_sql -- an ordered list of concrete steps the model judged
    # necessary to answer the question (grouping, metrics, filters, whether
    # a top-N-per-group ranking or a period-over-period comparison is
    # needed), or None if planning was skipped. Skipped whenever
    # Settings.enable_query_planning is False, or the question matched no
    # agent.complexity signal (see plan_query_node's docstring) -- the
    # overwhelmingly common case, so this is None for most questions.
    # Reused unchanged across every generate_sql retry for this question
    # (only recomputed if retrieve_schema itself reruns, on a
    # missing_reference retry) -- injected into generate_sql's prompt
    # (agent.llm_client._build_plan_block) and checked against the
    # generated SQL by review_sql_node below.
    query_plan: list[str] | None

    # Set by review_sql_node, only when query_plan is not None (nothing to
    # check against otherwise) -- whether the LLM judged the just-generated
    # SQL to implement every plan step. None means review didn't run.
    plan_review_passed: bool | None
    # The reviewer's critique when plan_review_passed is False -- fed back
    # into the next generate_sql attempt the same way any other retryable
    # error is (error_history/last_error_category). None when review passed
    # or didn't run.
    plan_review_feedback: str | None

    # Set by review_metric_conformance_node (Prompt 10,
    # 10_GOVERNED_METRICS_CONTRACT.md), only when governing_metrics is
    # non-empty and Settings.enable_metric_conformance_review is True
    # (nothing to check against / disabled otherwise) -- whether the LLM
    # judged the just-generated SQL to actually use every governing
    # metric's approved expression, mirroring plan_review_passed exactly.
    # None means the check didn't run.
    metric_conformance_passed: bool | None
    # The reviewer's critique when metric_conformance_passed is False --
    # fed back into the next generate_sql attempt the same way
    # plan_review_feedback is. None when it passed or didn't run.
    metric_conformance_feedback: str | None

    # Set by generate_sql
    sql: str | None

    # Set by validate_sql -- table names the generated SQL referenced that
    # were never part of the retrieved schema context for this attempt (see
    # agent.sql_validator.find_unexpected_table_references). Detection/
    # logging signal only, not a new gate -- see that function's docstring.
    # Empty list is the overwhelmingly common case.
    schema_anomaly_tables: list[str]

    # Set by validate_sql
    validation_error: str | None

    # Set by estimate_query_cost_node -- the non-executing EXPLAIN/SHOWPLAN
    # estimate for the validated SQL (see db.query_cost), or None if
    # estimation was disabled, unsupported for this DB_TYPE, or failed open
    # (timed out / errored -- see that module's docstring on why this must
    # never block a legitimate query). Logged at debug level regardless of
    # severity, so "what does normal look like" can be tuned from real data
    # rather than guessed.
    cost_estimate: CostEstimate | None
    # Set only when cost_estimate.severity == "moderate" -- a UI-facing
    # "this may take a moment" notice shown before/during execution. A
    # "high" severity estimate never reaches execute_sql at all (see
    # route_after_cost_estimate): it's routed back to generate_sql as a
    # retryable error instead, the same as any other correctable mistake.
    cost_notice: str | None

    # Set by execute_sql
    execution_error: str | None
    result_columns: list[str] | None
    result_rows: list[tuple] | None
    row_count: int | None

    # Set by execute_sql, only on a *successful* multi-table-join execution
    # that returned zero rows -- detection-only, never a new gate (same
    # philosophy as schema_anomaly_tables above): a legitimate zero-row
    # answer to a multi-table question is common, but this shape is also
    # the observable symptom of a join that matched unrelated surrogate-key
    # columns (see agent.sql_validator.references_multiple_tables and
    # agent.llm_client._system_prompt's join-correctness rules). None means
    # either the result had rows, only one table was involved, or execution
    # didn't succeed at all.
    low_confidence_notice: str | None

    # Set once by `sanitize_input_node` from the request-level governance
    # verdict (`governance.request_policy`), read by the result gate in
    # `execute_sql_node` and by the recommendation-safety filter. A plain
    # value in per-request state, never a global -- the identical pattern
    # `selected_database`/`selected_model` already use. Absent means the
    # strict `governance.domains.BusinessDomain.GENERAL` fallback applies.
    governance_domain: str

    # A fixed, reviewed notice when the domain-safety filter withheld a
    # recommendation (`governance.recommendation_safety.WITHHELD_NOTICE`).
    # `None` when nothing was withheld. Never names which recommendation
    # or which rule matched -- that detail goes to the audit log only.
    governance_notice: str | None

    # Set by compute_analytics_node (Prompt 13,
    # 13_ANALYTICAL_RESULT_ENGINE_CONTRACT.md), which runs between
    # execute_sql and generate_insight, only after a successful execution
    # -- a deterministic, full statistical breakdown of the result
    # (analytics.engine.compute_analytics_result), covering whichever
    # result shape it classified (scalar/time_series/categorical_
    # aggregate/multidimensional/raw_table/empty): per-column count/null_
    # count/distinct_count/min/max/mean/median/variance/stddev/
    # percentiles, a full period-by-period growth series (time series
    # only), a complete ranking with each entry's share-of-total percent
    # (categorical/multidimensional only), and both z-score- and IQR-
    # flagged outliers -- a strict superset of what `insight_summary`
    # above covers, computed independently (never derived from it). Always
    # a plain dict (analytics.models.AnalyticsResult.model_dump()'s
    # output, matching this TypedDict's established "plain dicts, not
    # model instances" convention), never the pydantic model itself. None
    # when Settings.enable_analytics_engine is False or the computation
    # raised unexpectedly (fails open, logged, never a reason a question
    # fails -- see compute_analytics_node's own docstring). Every finding
    # in it is DataTruthLevel.DATABASE_FACT (agent.provenance) -- pure
    # arithmetic over the already-executed result, no LLM involved.
    # Deliberately NOT read by generate_insight_node or
    # agent.llm_client._build_insight_prompt in this prompt -- the live
    # insight narrative prompt is benchmark-pinned (see CLAUDE.md) and
    # stays untouched; this field is computed, tested, and surfaced
    # (AskResponse.analytical_result) ahead of that wiring decision, the
    # same deferral precedent `insight_summary`'s own trend/outliers/
    # stddev fields already established before this prompt existed.
    analytical_result: dict | None

    # Input, set once by `run_agent()` from the caller (`AskRequest
    # .forecast_horizon`, already range-validated against
    # `Settings.forecast_max_horizon` by `api/main.py`'s `/ask` handler
    # before this function is ever called -- the identical "validate
    # before any admission-control slot is acquired" pattern
    # `AskRequest.model` already uses). `None` (the default -- every
    # caller before this field existed) means "use
    # `Settings.forecast_default_horizon`", resolved by `analytics
    # .forecasting.generate_forecast` itself, not here.
    forecast_horizon: int | None

    # Set by generate_forecast_node (Prompt 16,
    # 16_FORECASTING_CONTRACT.md), which runs between compute_analytics and
    # generate_insight, only when classify_analytical_intent_node already
    # classified the question as AnalyticalIntentType.FORECAST --
    # analytics.forecasting.generate_forecast's full typed
    # ForecastResult.model_dump() output (status, the model actually used
    # and its training-window/parameters metadata, the forecasted points
    # with prediction intervals where supported, a backtest evaluation,
    # and always-non-empty limitations), or `None` when forecasting wasn't
    # even attempted (feature disabled, no FORECAST intent, or no
    # time-series growth data available for this result -- see that
    # node's own docstring for the full skip/fail-open conditions). A
    # *rejected* ForecastResult (data insufficient / untrusted temporal
    # semantics) is still stored here, not collapsed into None -- so a
    # caller can see why forecasting didn't happen. Always a plain dict,
    # matching this TypedDict's established convention. Every value in it
    # is DataTruthLevel.AI_INFERENCE (agent.provenance) -- a statistical
    # extrapolation beyond the already-executed result, never a confirmed
    # fact; see analytics.forecasting's own module docstring for why this
    # is classified that way rather than as DATABASE_FACT.
    forecast_result: dict | None

    # Set by generate_recommendations_node (Prompt 17,
    # 17_RECOMMENDATION_ENGINE_CONTRACT.md), which runs between
    # generate_forecast and generate_insight -- recommendation.engine
    # .generate_recommendations's output, one dict per
    # recommendation.models.Recommendation.model_dump(), covering whichever
    # of its nine categories found evidence-backed, sufficiently-confident,
    # authorized candidates for this result (ANOMALY/DATA_QUALITY/REVENUE/
    # CUSTOMER/PRODUCT from state["analytical_result"], DATABASE_PERFORMANCE
    # from state["cost_estimate"], PERFORMANCE from the live
    # observability.metrics snapshot, SECURITY from any restricted-column
    # reference in state["sql"]). Always `[]` (never `None`) when the engine
    # ran and found nothing, matching this TypedDict's established
    # "always iterable" convention for plural fields; `[]` also when
    # Settings.enable_recommendation_engine is off or the computation raised
    # unexpectedly (fails open, logged, never a reason a question fails --
    # see that node's own docstring). OPERATIONS (which needs a
    # analytics.root_cause.RootCauseResult -- a second, comparison dataset
    # this single-query pipeline doesn't produce, see that module's own
    # disclosed gap) never fires from this live node; it remains reachable
    # only by calling recommendation.engine.generate_recommendations
    # directly with one in hand. Every value in it is
    # DataTruthLevel.AI_INFERENCE (agent.provenance), grounded in
    # DATABASE_FACT/CONFIRMED_BUSINESS_TRUTH evidence -- never a bare,
    # ungrounded suggestion (master rules 9-10).
    recommendations: list[dict]

    # Input, set once by the caller -- whether generate_insight_node should
    # even attempt an insight. True (the default) is normally low-risk and
    # high-value; the UI exposes this as a toggle so it can be turned off
    # per-question without touching anything else in the pipeline.
    enable_insight: bool

    # Set by generate_insight_node, only after execute_sql_node succeeds.
    # None means "no insight" -- disabled via enable_insight, the result was
    # a redundant single-value shape (see agent.insight.should_skip_insight),
    # the LLM call itself failed, or the generated text failed the
    # groundedness check (see agent.insight.is_insight_grounded) and was
    # dropped rather than shown. Never influences sql/result_rows/row_count
    # above -- purely a narrative layer rendered alongside them.
    insight: str | None
    # The exact ResultSummary the insight (if any) was generated from --
    # kept on state so scripts/run_eval.py's grounding checks reuse the
    # same summary the node itself graded against, rather than recomputing
    # it and risking the two definitions of "grounded" drifting apart.
    insight_summary: ResultSummary | None
    # How many numbers in `insight` were verified against the result by
    # `agent.insight.is_insight_grounded`. None whenever there is no
    # insight at all; 0 for an insight that states no numbers (a real,
    # ordinary case -- "sales are concentrated in the top category").
    #
    # Redundant with the drop behaviour above only in the sense that an
    # insight which reaches here always passed the check. It exists so the
    # UI can show what was actually verified as a server-reported fact
    # instead of asserting it from this node's own control flow, which a
    # future change could alter without the claim in the UI noticing.
    insight_numbers_checked: int | None

    # Retry bookkeeping, shared across validate_sql / execute_sql
    error_history: Annotated[list[str], operator.add]
    retry_count: int

    # Set once by the caller (agent.graph.run_agent), via
    # agent.complexity.compute_max_retries -- this question's effective
    # retry budget, which may exceed Settings.max_retries if the question
    # text matched one or more complexity signals (top-N-per-group phrasing,
    # growth/period comparisons, several metrics at once). Every retry-vs-
    # give-up check (validate_sql/estimate_query_cost/execute_sql) reads
    # this instead of Settings.max_retries directly, falling back to it if
    # unset (e.g. a test constructing AgentState by hand).
    max_retries: int
    # The signals agent.complexity.detect_complexity_signals matched for
    # this question (empty list if none) -- carried on state purely for
    # observability (logged once by run_agent), never read by any node's
    # own logic.
    complexity_signals: list[str]

    # Full per-attempt timeline (see AttemptRecord) -- one entry per
    # concluded attempt, for the UI's "Retry timeline" expander and for
    # inspecting/testing the retry loop's behavior directly.
    attempt_history: Annotated[list[AttemptRecord], operator.add]

    # Category of the most recent failure -- a agent.sql_validator.ViolationType
    # value ("empty" | "parse_error" | "nested_aggregate" | "restricted_column"
    # | "high_cost" | "safety_violation") or an
    # agent.error_classification.ExecutionErrorCategory value ("syntax" |
    # "missing_reference" | "aggregate_nesting" | "timeout" | "unknown"), or
    # None. Overwritten each attempt (not accumulated) -- generate_sql_node
    # reads it to give the next LLM call a more targeted retry hint
    # (agent.llm_client._ERROR_CATEGORY_HINTS) than a generic "fix it."
    last_error_category: str | None

    # Set once, at the point status becomes "failed" -- a plain-language,
    # UI-ready summary of why the agent gave up (distinct from the raw
    # driver/validator error text in error_history).
    failure_explanation: str | None

    # Overall progress, useful for the UI to show a status indicator
    status: AgentStatus

    # --- Prompt 47 (47_MODEL_ROUTING_COST_OPTIMIZATION_CONTRACT.md):
    # governed model routing. All three are request-scoped, exactly like
    # `selected_model`/`selected_database`/`tenant_id` above and for the
    # same reason -- a routing budget and a caller's own pinned model must
    # never leak into a concurrent request.

    # True when the caller explicitly named a model (`AskRequest.model`),
    # as opposed to letting the server pick. Needed as its own field
    # because `api/main.py` resolves an omitted `AskRequest.model` to
    # `Settings.ollama_model` *before* the graph runs (see
    # `agent.model_registry.validate_model_selection`), so
    # `selected_model` alone can no longer tell "the caller asked for this
    # model" from "the caller asked for nothing". Routing never overrides
    # an explicit choice, so this flag is what makes that guarantee
    # expressible.
    model_explicitly_selected: bool

    # The request's `model_routing.router.ModelRouter`, or None when
    # routing is off (`Settings.enable_model_routing`) or this run was
    # started by a caller that did not build one (`eval/runner.py`,
    # `scripts/integration_test.py`). Typed `object | None` rather than the
    # real class so `agent/state.py` keeps importing nothing from
    # `model_routing/` -- the same import-direction discipline that keeps
    # this module free of `db`/`api` dependencies. A live object in state
    # is safe here because this graph runs without a LangGraph
    # checkpointer: nothing in this codebase ever serializes a whole
    # `AgentState` (verified by inspection, Prompt 47), only named fields.
    model_router: object | None

    # One entry per routed LLM call -- `model_routing.models
    # .RouteDecision.model_dump()`'s shape (task, model, tier, reason,
    # baseline, risk, complexity score, fallback chain, cost units, and
    # `truth_level: "ai_inference"`). Deliberately a plain list, not an
    # `operator.add` reducer channel like `error_history`/`stage_timings`:
    # no node returns this key. `agent.graph.run_agent` fills it once,
    # after the graph has run, from the router's own ordered record of
    # every decision it made -- which keeps seven call sites in
    # `agent/nodes.py` from each having to thread an extra key through
    # every one of their return paths. Surfaced on
    # `AskResponse.route_decisions`; always an AI_INFERENCE judgment about
    # which model was likely to do better, never a claim about the
    # answer's own quality.
    route_decisions: list[dict]

    # Prompt 02 (harness): this execution's `agent.harness
    # .ExecutionEnvelope` -- the server-owned record of its identity,
    # budgets, deadline and cancellation flag. Read by the control
    # boundary (`agent.harness.guard_node`) before every node; never
    # written by a node, and never derived from anything a model
    # produced. Typed `object | None` for the same import-direction
    # discipline `model_router` above follows, and `None` whenever a
    # caller runs the graph directly -- which leaves the run completely
    # unguarded, exactly as it was before the harness existed. A live
    # object in state is safe for the same reason `model_router` is:
    # this graph runs without a LangGraph checkpointer, so no whole
    # `AgentState` is ever serialized.
    harness_envelope: object | None

    # Set only when the harness stopped the run itself (budget, deadline
    # or cancellation) -- `agent.harness.FailureClass`'s value. Distinct
    # from `status`, which collapses every unsuccessful outcome into
    # "failed": this says *which* stop it was, which is what telemetry
    # and an operator need and what the four-value response contract
    # cannot express.
    harness_termination_reason: str | None

    # Per-node-call wall-clock timings (see StageTiming) -- one entry per
    # node call, accumulated across retries. Powers performance profiling
    # (scripts/profile_pipeline.py) and, potentially, a live "which stage is
    # running now" progress indicator in the UI.
    stage_timings: Annotated[list[StageTiming], operator.add]
