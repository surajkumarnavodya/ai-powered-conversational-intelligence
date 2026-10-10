# Conversational Intelligence UI — audit and implementation

Ten UI capabilities were requested for the chat workspace. The audit
found that **four already existed in full** and three more existed in
parts; this pass added what was genuinely missing and consolidated what
was scattered. Nothing was rebuilt that already worked.

## Feature-by-feature status

| # | Capability | Status | What existed | What changed |
|---|---|---|---|---|
| 1 | Question Navigator | **Already complete** | `chat/QuestionNavigator.tsx` + tests, wired in `Chat.tsx` | Nothing |
| 2 | AI SQL Intelligence Panel | **Consolidated, then completed** | `QueryPlanPanel`, `SchemaContextPanel`, `GoverningMetricsPanel`, a "Query information" expander, a separate SQL editor expander — five siblings. CodeMirror SQL highlighting and Copy SQL already existed inside `SqlEditor` | `sql/SqlIntelligencePanel.tsx` now owns all of it behind one progressive disclosure — see the section below |
| 3 | Query Validation & Execution Status | **Added, then completed** | `cost_notice`, `RetryTimeline`, a duration badge, a truncation flag — each in a different place | First `sql/ExecutionStatusBar.tsx` (one scannable badge row). Then `sql/QueryLifecycle.tsx`, the stage rail none of them provided — see the section below |
| 4 | Interactive Analytics Workspace | **Already complete** | `analytics/AnalyticsSummary.tsx` + 12 panels + `ChartSection` | Nothing |
| 5 | Data Source & Lineage Panel | **Linked** | `SchemaContextPanel`, `SourcesUsedPanel`, a separate `/data-lineage` page | A link from the SQL intelligence panel to the lineage explorer |
| 6 | Grounded Insight / Evidence Panel | **Extended** | `analytics/EvidencePanel.tsx` (a recommendations panel, despite the name), `TruthLevelBadge` with 3 of the 5 labels | `EvidenceStatus` + `EvidenceBadge` (all 5 labels, one owner), `ClaimEvidence`, the insight and source answers labelled at last — see the section below |
| 7 | Analysis Artifact Workspace | **Added** | — | New `chat/ArtifactWorkspace.tsx` + `PinTurnButton`, and `pinnedEntryIds`/`togglePinned` on the existing store |
| 8 | Conversation Context Manager | **Added** | `ConversationList`, `ConversationSearch` (history navigation) — but nothing showed the follow-up window actually sent | New `chat/ConversationContextPanel.tsx`, calling the same `buildConversationHistory` the request payload uses |
| 9 | Agent Execution Timeline | **Added (needed backend)** | `RetryTimeline` (attempt outcomes only); `stage_timings` was recorded server-side but never exposed | `StageTimingOut` + `AskResponse.stage_timings`; new `chat/AgentExecutionTimeline.tsx` |
| 10 | Intelligent Follow-up Recommendations | **Surfaced** | `next_question` recommendations rendered inside `EvidencePanel` — lazy-loaded, nested in the analytics expander, only after Confirm and Run | New `chat/FollowUpSuggestions.tsx` renders them directly under the answer. **One renderer, not two**: removed from `EvidencePanel` |

## The SQL Intelligence Panel in detail

One expander holds everything about a turn's SQL. Collapsed it reads as a
status line; expanded it holds the SQL and its metadata.

```
Collapsed:  🧠 SQL • Validated • 42 rows
Expanded:   the SQL (CodeMirror, highlighted, with Copy)
            Confirm and Run + any cost notice
            Query metadata: statement type, tables referenced,
                            database, model, attempts, rows, duration
            Query plan · Governed metrics · Retrieved schema
            → Open the lineage explorer
```

**Open by default until the turn has a result, collapsed afterwards.**
Burying the primary action behind a collapse would make the main path
harder to reach; once a result exists the summary line is the useful
state and the SQL becomes reference material.

### Already present, so not rebuilt

- **Syntax highlighting** — `SqlEditor` already uses CodeMirror with
  `@codemirror/lang-sql`. The prompt's "only if the project already has a
  suitable dependency" condition was already met, so no highlighter was
  added and there is no second code-presentation mechanism.
- **Copy SQL** — `ui/CopyButton` already sits inside `SqlEditor`, copying
  via `navigator.clipboard.writeText` only.
- **Long SQL** — `SqlEditor` already passes `EditorView.lineWrapping`, so
  a long query wraps inside the box instead of scrolling horizontally.

### Two facts the server had no accurate source for

`AskResponse.sql_summary` (new, optional, `null` when absent):

| Field | Why server-side |
|---|---|
| `statement_type` | Read from the parsed AST (`statement.key`), so a leading comment or a `WITH` prefix cannot mislabel it the way a text match could |
| `referenced_tables` | The tables the query **actually uses**. `schema_tables` holds the top-k candidates *retrieval offered* — commonly a superset — so labelling those "tables referenced" would be wrong |

Both come from one new pure function,
`agent.sql_validator.summarize_sql`, which reuses the same parse and the
same CTE-aware filtering `find_unexpected_table_references` already
performs (a CTE's own name is a reference, not a base table). It
validates and gates nothing — `validate_sql` remains the only thing that
decides whether SQL may run.

### Ownership split with the status bar

Both surfaces are visible at once, so no fact is stated twice:

| Surface | Owns |
|---|---|
| SQL panel (collapsed title + metadata) | validation state, row count, duration, attempts, statement type, referenced tables, database, model, cost notice |
| `ExecutionStatusBar` (always visible) | the result-trust flags that must not hide behind a collapse: execution failure, truncation, cache freshness, governance having altered values |

`ExecutionStatusBar.test.tsx` asserts the moved facts are **absent**
there, so the duplication cannot creep back.

### Safety

- The SQL rendered is `entry.editableSql`, seeded from the server's
  `state.sql`. Nothing in the panel generates, rewrites or formats SQL.
- The panel contains **no execution path**. Its only action calls the
  caller's `onConfirmAndRun`, which goes to `POST /execute` — and that
  route re-validates and re-executes the *current* text server-side every
  time, so an edit in the box is exactly as untrusted as the original
  generation. The panel cannot bypass validation.
- Copying is clipboard-only; a test asserts clicking copy does not invoke
  the run handler.
- A failed validation renders as `SQL • Blocked • …`, never as
  "Validated".
- The cost notice is withheld once the user edits the SQL, because it
  describes the server's own query, not the edit.

### A real bug mypy caught that the tests did not

`_sql_summary_out` first passed the whole `DatabaseConnectionConfig` to
`get_sqlglot_dialect`, which expects `db_type: str`. At runtime that
returns `None` — sqlglot's generic dialect — and **T-SQL does not parse
under the generic dialect**. Every SQL Server query (all four configured
databases here) would have returned `sql_summary: null`. The existing
tests passed because an ordinary ANSI `SELECT` parses either way;
`mypy` flagged the argument type. Fixed, and pinned by
`test_sql_summary_parses_in_the_routed_database_s_own_dialect`
(`SELECT TOP 10 name FROM [dbo].[Employee]`), which was mutation-tested:
reintroducing the bug fails it.

## The query lifecycle rail in detail

`sql/QueryLifecycle.tsx`. One compact row saying how far a question got
through the safe execution lifecycle and where it stopped:

```
✓ Understanding  ✓ Schema  ✓ SQL  ✓ Validation  ✓ Cost check  ● Ready to run  ○ Execution
```

### Why this, when three run-related surfaces already existed

| Surface | Answers |
|---|---|
| `RetryTimeline` | what happened on each attempt (outcome, SQL, error) |
| `AgentExecutionTimeline` | which stage was slow |
| `ExecutionStatusBar` | whether the *result* can be trusted |
| **`QueryLifecycle`** (new) | **how far it got, and where it stopped** |

None of the first three answered the last question. A reader wanting "it
reached validation and was refused there" had to infer it from an attempt
outcome code in a collapsed expander.

### Every stage is evidence-backed

A stage reads as reached only because `AskResponse.stage_timings`
contains a real call to one of its nodes. `agent.nodes._timed_node` wraps
every node in the graph and appends one entry per call, so the set of
stages present is exactly how far the pipeline actually got — and the
rail is **withheld entirely** (`deriveLifecycle` returns `[]`) when
`stage_timings` is empty, which is the case for a turn reconstructed from
saved chat history. Nothing infers a success from the absence of an
error, and nothing invents a plausible rail where there is no evidence.

### Stage grouping follows real execution order

`retrieve_schema` runs *before* the golden-example, business-context,
intent, analytical-plan and plan nodes. So "Understanding" covers only
the two pre-schema gates (`sanitize_input`, `classify_followup`), and the
comprehension nodes that run after schema retrieval group with `SQL`,
which they immediately feed. Grouping them all under a single leading
"Understanding" chip would have put the rail out of order with the
pipeline it describes.

The four post-execution analysis nodes (`compute_analytics`,
`generate_forecast`, `generate_recommendations`, `generate_insight`) are
listed in `POST_RESULT_NODES` and deliberately shown as no stage: they
run after a result exists and their output is already rendered by
`AnalyticsSummary`, the insight block and the follow-up chips. Listing
rather than omitting them keeps the exclusion a decision, and
`KNOWN_NODES` must equal the graph's real node set — pinned by a test on
both sides (see Tests below).

### "Ready to run" is the agent's verification; "Execution" is the user's

The graph executes candidate SQL internally to detect and self-correct
runtime errors, but that result is never rendered (CLAUDE.md, "SQL is
untrusted output, always"). So:

- `ready` = the agent has a query it got to run, awaiting your
  confirmation. This is the **requires-confirmation** state.
- `executed` = only the user's own Confirm and Run.

Labelling the agent's internal run as "Executed" would claim the user had
seen a result they had not.

### `blocked` and `failed` are different claims

| Status | Meaning | Examples |
|---|---|---|
| `blocked` | a deterministic gate refused | SQL safety validator, restricted column, cost ceiling, input guard, rate/concurrency limit |
| `failed` | a stage tried and could not finish | schema retrieval, LLM unreachable, syntax error, timeout |

The split is read from the outcome name the server already recorded.
`validate_sql_node` records the single fixed name `safety_violation` for
its fail-closed class and the specific category only for retryable ones,
so **no copy of `SAFETY_VIOLATION_TYPES` lives on the client** to drift
out of sync.

An outcome absent from `OUTCOME_STAGES` is attributed to the last stage
that ran, as `failed`. That fallback is correct rather than lazy: the
unmapped names are the validator's retryable categories, and a run that
ended there never reached `estimate_cost`, so "the last stage that ran"
is `validation` by construction.

### Streaming

**There is none, so this is final-state rendering.** `POST /ask` is a
single request that returns once the graph has finished; there is no SSE
or WebSocket endpoint anywhere in `api/`, and therefore no observable
"currently on stage N" signal mid-run. `running` is used only where it is
genuinely observable: the user's own Confirm and Run, which the client
itself initiated. `deriveLifecycle` takes exactly one required argument —
the completed response — which is the structural form of that statement,
and a test pins it.

The in-flight `/ask` phase keeps `PendingTurn`'s existing "Thinking Ns…"
badge. A rail with a guessed active stage was considered and rejected: it
would be the one thing this feature must not do.

### Labels are nouns, not past participles

One label is rendered under all six statuses, so `Validation: blocked` is
coherent where `Validated: blocked` would have the stage name
contradicting its own status. Caught by reading the blocked rendering of
an early version, and now pinned by a test asserting the word "Validated"
appears nowhere on a rail whose query was refused.

### Ownership, re-split

Adding a fourth status surface meant re-cutting the split so no fact is
stated twice. Both the rail and the bar are always visible; the panel's
collapsed title is visible whether or not it is expanded.

| Surface | Owns |
|---|---|
| `QueryLifecycle` | stage progression, per-stage status (running/completed/failed/blocked/requires-confirmation/not-started), per-stage retry count, a concise stop reason |
| `ExecutionStatusBar` | result-trust flags only: truncation, cache freshness, governance having altered values |
| `SqlIntelligencePanel` (collapsed title) | the artifact: `SQL • 42 rows`, or that it has not run |
| `RetryTimeline` / `AgentExecutionTimeline` | per-attempt detail and per-stage duration, both behind their own expanders |

Two assertions were therefore **moved, not deleted**: the execution-failure
badge left `ExecutionStatusBar` (its test now asserts its absence) and
validation state left the SQL panel's summary line (same). Each
corresponding behaviour is now asserted in `QueryLifecycle.test.tsx`.

### Safety

- **No new data and no new decision.** Every status is derived from
  `stage_timings`, `attempt_history`, `rejection_reason`, `agentStatus`
  and the client's own confirm-in-flight flag. The rail never
  re-validates SQL, re-judges cost, or re-decides authorization.
- **Stop reasons are fixed strings chosen by outcome class** — never the
  server's raw error text, and never the specific validator rule that
  fired. A `safety_violation` reads "The SQL safety validator refused
  this query. Only read-only queries are allowed." The violation type
  (`system_catalog_access`, …) stays out of this always-visible rail; a
  test asserts it.
- **Renders for blocked and failed turns too**, gated only on this being
  a SQL-path turn — deliberately *not* on `BLOCKED_STATUSES` the way
  `showSqlPanel` is. A refused run is exactly when "where did this stop"
  matters most, and the rail carries no editor and no run control, so
  there is nothing unsafe to offer there.

### Accessibility

Rendered as an `<ol>` labelled "Query lifecycle", one `<li>` per stage.
Each stage's status is carried **as text** (`sr-only`), not by colour or
icon alone, so the rail survives a screen reader and a monochrome or
colour-blind reading. The stage being waited on carries
`aria-current="step"` (exactly one, asserted). Icons are
`aria-hidden="true"`.

## The evidence panel in detail

### What the audit found

Three of the five evidence labels already existed, in `TruthLevelBadge`,
and ~12 analytics panels already rendered one. Two did not exist at all,
and two prominent claims carried **no evidence label whatsoever**:

| Label | Before |
|---|---|
| `Computed from data` | existed (`database_fact`) |
| `Approved definition` | existed (`confirmed_business_truth`) |
| `AI estimate` | existed (`ai_inference`) |
| `Source-backed` | **did not exist** — a document/policy/web answer carried no label |
| `Not available` | **did not exist** — missing evidence was simply an absent badge |

| Claim | Had a status? |
|---|---|
| The insight narrative | **No** — rendered as a sparkle box with no label |
| A document / policy / web answer | **No** — citations were listed, unlabelled |
| Analytics findings, forecast, governed metrics, recommendations | Yes, panel-level |

`EvidencePanel` was also, despite its name, a *recommendations* panel: it
rendered `recommendations.filter(kind === 'action')` and nothing else.

### `EvidenceStatus`, orthogonal to `TruthLevel`

`TruthLevel` is reused unchanged. The new `EvidenceStatus`
(`analytics/EvidenceBadge.tsx`) sits beside it, exactly the split
`briefing/models.py`'s `ClaimClass` already makes against
`agent.provenance.DataTruthLevel` on the server: `TruthLevel` answers
"how sure are we", `EvidenceStatus` answers "what is behind this claim as
the reader sees it". Two of its five states cannot be expressed as a
`TruthLevel` at all — a cited external source is not something this
platform computed, and "no evidence" is not a confidence level.

`EvidenceBadge` is the **single owner** of all five labels and tones.
`TruthLevelBadge` became a thin adapter over it rather than keeping a
second copy of three of them, so "Computed from data" is defined once.
Two hand-rolled `<Badge tone="warning" data-truth-level="ai_inference">AI
estimate</Badge>` copies in `recommendations/RecommendationList.tsx` and
`RecommendationDetail.tsx` were replaced with the shared badge — that was
a third and fourth copy of the same label.

`data-truth-level` is preserved, because `AnalyticsSummary.test.tsx` and
`DecisionCenter.test.tsx` assert on it (the latter's comment calls it
"`TruthLevelBadge`'s own contract"). It is stamped only when the status
came from a real server `TruthLevel`; a `source_backed`/`unavailable`
badge has none and correctly carries no such attribute.

### The insight is labelled `AI estimate`, deliberately

This is the judgment call in this work. An insight that reaches the UI
has already passed `agent.insight.is_insight_grounded` — one that
mentions an unsupported number is **dropped server-side** and never
arrives — so requirement 4 ("never present an unsupported number as a
verified fact") held structurally before this change. But the *wording*,
and any causal or qualitative framing the model added, are the model's.

Labelling the whole sentence `Computed from data` would extend the
numbers' verification to an interpretation nobody verified. So the badge
is the conservative `AI estimate`, and the expandable evidence states
what was actually checked:

> 2 figures in this summary were checked against the 42 returned rows.

That costs the reader nothing and overstates nothing. Note the contrast
with the brief's own example: "Revenue increased 18%" *is* `Computed from
data` — because that claim comes from `analytics.engine`'s deterministic
`GrowthStat`, not from the narrative, and the trend panel already
labelled it correctly.

### One additive backend field

`AskResponse.insight_numbers_checked: int | null`.

The UI could have asserted "numbers verified" purely from the server's
drop-if-ungrounded behaviour, but that makes the claim an assumption
about server control flow: a future change to that behaviour would make
the UI lie silently. So the count is reported explicitly, and
`lib/evidence.ts::insightEvidenceSummary` says nothing about verification
when it is `null` (an older turn, or one reloaded from saved history).

`null`, `0` and `n` are three different statements and the UI says three
different things:

| Value | Meaning | Shown as |
|---|---|---|
| `null` | no count reported | nothing about verification |
| `0` | the summary states no figures | "states no figures of its own" |
| `n` | `n` figures matched the result | "n figures ... were checked against the 42 returned rows" |

The count is exactly `len(extract_numbers(insight_text))` taken *after*
the grounding check passed — every number in a stored insight was matched
against the result summary, so the count in the text is the count
verified. Verified against the real functions: `"Australia had the highest
sales at 2300000.0, ahead of Southwest at 1100000.0."` → 2;
`"Bikes account for 50.0% of total revenue."` → 1;
`"Sales are concentrated in a single leading territory."` → 0. No change
was made to `agent/insight.py`, the insight prompt text, or the drop
behaviour — `_INSIGHT_SYSTEM_PROMPT` stays benchmark-pinned per CLAUDE.md.

### `Source-backed` requires a source

`lib/evidence.ts::sourceAnswerEvidenceStatus` returns `source_backed`
only when there are real citations. An answer with none is
`Not available`, and that is a reachable case rather than a defensive
branch:

- `rag/graph.py`'s insufficient-information fallback answers citing
  nothing.
- A policy chunk carrying a `sensitivity_category`, or a document whose
  `restricted_roles` exclude this caller, is never summarized into an
  answer and yields `citations == []`.

The second is the restricted-field case, and it is why this must not
default to `source_backed`: **claiming a source for an answer whose
supporting material was withheld would turn an access control into a
false endorsement.** Pinned by a test, and mutation-verified (making
`sourceAnswerEvidenceStatus` return `source_backed` unconditionally fails
three cases).

### Expand/collapse, with the default set per context

`ClaimEvidence` is one renderer for "claim + status + expandable
evidence", shared by the insight, source answers and each
recommendation. The claim text and its status badge stay visible in every
state, so the status can never be the thing that is hidden.

The open default is the caller's, because the right default differs:

| Surface | Default | Why |
|---|---|---|
| Insight | collapsed | dense answer area; readability (requirement 8) |
| Recommendations | **open** | this evidence was always visible before; collapsing it would hide working behaviour behind a second disclosure |

That second row was found by a failing pre-existing test
(`AnalyticsSummary.test.tsx` asserted the evidence text was present), not
by review — the first version collapsed everything. `defaultOpen` was the
fix.

Details use `useState` + an explicit button with
`aria-expanded`/`aria-controls`, not the shared `Expander` (`<details>`):
the summary row here is a claim with a badge beside it, not a title, and
interactive badge markup inside a `<summary>` is worse for assistive
technology.

### Multi-source evidence

A single estimate frequently rests on several pieces of evidence at
different levels — a computed figure plus an approved metric definition.
`EvidenceList` keeps each item's own badge rather than folding them into
the claim's single status, because "this estimate rests on an approved
definition" and "this estimate rests on another estimate" are different
things to a reader. (The server already refuses to attach `ai_inference`
evidence at all — see `recommendation/models.py` — so in practice the mix
is computed and approved.)

`EvidenceList` takes the server's `TruthLevel`, not a pre-derived
`EvidenceStatus`. Deriving at the call site silently dropped
`data-truth-level` and broke `AnalyticsSummary.test.tsx`; that failure is
how the signature was arrived at.

### Security

- Nothing new is exposed. Every rendered string is either a claim the
  server already returned, a fixed explanation owned by `EvidenceBadge`,
  or a citation filename already shown. No prompt, no intermediate
  generation, no chain of thought — asserted across all five statuses.
- Database-derived evidence text goes through `sanitizeLabel`.
- The restricted-material case above is a security property, not a
  cosmetic one.

## API / schema changes

Two additions, both additive and optional:

- `api/schemas.py`: new `StageTimingOut` (`stage`, `attempt`,
  `duration_ms`), mirroring `agent.state.StageTiming`.
- `api/schemas.py`: `AskResponse.stage_timings: list[StageTimingOut]`,
  defaulting to `[]`. Every existing field is unchanged and an existing
  client that ignores it is unaffected.
- `api/main.py`: `_stage_timings_out(state)` populates it from the final
  graph state. The timings were **already recorded** by
  `agent.nodes._timed_node` for every node, so this adds no
  instrumentation and no measurable cost — it exposes data the server was
  already carrying and discarding.

Defensive by design: a malformed entry in that `operator.add` reducer
channel is skipped rather than raising, so a profiling field can never
turn a successful answer into a 500 (pinned by a test).

- `api/schemas.py`: new `SqlSummaryOut` (`statement_type`,
  `referenced_tables`) and `AskResponse.sql_summary`, defaulting to
  `None`. Populated by `api.main._sql_summary_out` from
  `agent.sql_validator.summarize_sql`, parsed in the dialect of the
  database the question was actually routed to.

No database change, no migration, no new endpoint, no new permission.

## Reused rather than rebuilt

`Expander`, `Badge`, `Button`, `buildConversationHistory`,
`MAX_FOLLOWUP_EXCHANGES`, `sanitizeLabel`, `collapseQuestionWhitespace`,
`isSqlResult`, `chatStore` (extended, not replaced), the
`turn-${entryId}` scroll anchor `QuestionNavigator` already uses,
`QueryPlanContent`/`SchemaContextContent` (extracted from the existing
panels), and `AttemptRecord`/`Recommendation`/`GoverningMetric` types.

No new state library, no new chart engine, no new dependency.

## Security

- **No client-side security decision was added.** The status bar reads
  server-returned fields and re-judges nothing: validation state comes
  from `attempt_history`, cost from `cost_notice`, governance from
  `restricted_field_notice`. The client never decides whether SQL is
  safe or whether a value may be shown.
- **No role, tenant or source id is trusted from the client.** Pin state
  is a set of local `entryId`s with no authorization meaning. The lineage
  link is a plain route whose own screen re-checks access server-side.
- **Nothing new is exposed.** The timeline carries node names and
  durations; the SQL intelligence panel carries plan steps, retrieved
  DDL, the chosen database/model and governed metric names — all already
  in `AskResponse`. No prompt text, no model reasoning, no intermediate
  generation, no credential. Tests assert the absence of
  prompt/reasoning/credential-shaped strings.
- **Database-derived text stays untrusted.** Follow-up labels go through
  `sanitizeLabel` on display *and* on the way back into `onAsk`; a
  clicked chip re-enters the normal `/ask` path, so the input guard, the
  governance request policy and the SQL validator all apply.

## Accessibility

- Every new list is a real `<ul>`/`<ol>` with an `aria-label`; every
  pinned artifact and status item is a `<li>`.
- Progress bars and icons are `aria-hidden`; the number or label beside
  them carries the same information.
- The pin control exposes `aria-pressed` and a question-specific
  `aria-label`.
- Expanders are the existing native `<details>`/`<summary>`, so they stay
  keyboard-operable with no JS state.
- Follow-up chips are real buttons and are disabled while a question is
  in flight.

## Performance

- Aggregation and selection are memoized (`useMemo`) in all four new
  components.
- The artifact workspace holds **ids, not copies** — pinning cannot
  duplicate rows or create a snapshot that goes stale.
- Nothing new was added to the main bundle's critical path: the analytics
  and chart sections remain lazy-loaded, and the new components are small
  and synchronous.
- Three sibling expanders became one, reducing the DOM a long
  conversation accumulates per turn.

## Tests

| Suite | Cases |
|---|---|
| `AgentExecutionTimeline.test.tsx` | 14 |
| `QueryLifecycle.test.tsx` | 43 |
| `ExecutionStatusBar.test.tsx` | 13 (rescoped twice) |
| `FollowUpSuggestions.test.tsx` | 14 |
| `SqlIntelligencePanel.test.tsx` | 23 (rescoped) |
| `ConversationContextPanel.test.tsx` | 9 |
| `ArtifactWorkspace.test.tsx` | 12 |
| `EvidenceBadge.test.tsx` | 12 |
| `ClaimEvidence.test.tsx` | 14 |
| `lib/evidence.test.ts` | 13 |
| `tests/test_api_ask.py` (backend) | 9 |
| `tests/test_agent_nodes.py` (backend) | 2 new |
| **New total** | **177** |

The evidence work covers each scenario its brief names: verified claims,
unsupported claims (the server-side drop, plus `status` being a required
prop so an unlabelled claim is unrepresentable), estimates, missing
evidence, restricted fields, and multi-source evidence.

Two guards are mutation-verified rather than assumed:

- Making `sourceAnswerEvidenceStatus` return `source_backed`
  unconditionally fails 3 cases.
- Dropping `insight_numbers_checked` from one of the insight node's
  non-success returns fails
  `test_every_non_success_path_clears_the_verified_count`. **The first
  version of that test did not catch it** — it covered four of five exit
  paths and the mutation landed on the LLM-failure path. The test now
  enumerates all six routes out of the node and asserts the key is
  *present*, not merely falsy, since a missing key is what would let a
  stale count survive the LangGraph state merge.

`QueryLifecycle.test.tsx` covers each scenario the lifecycle work was
asked to cover: success, validation failure, execution failure, timeout,
retry, confirmation-required, and the non-streaming fallback — plus a
refusal before any SQL exists (rejected / rate-limited /
needs-clarification), the blocked-vs-failed distinction, and the
unmapped-outcome fallback.

Two guards there are deliberately cross-language, because the rail
enumerates Python node names:

- `KNOWN_NODES` must equal the graph's real node set. **Mutation-tested**:
  renaming `validate_sql` to `validate_sqll` in `STAGE_NODES` fails 6
  cases, so the guard is not vacuous.
- `tests/test_sql_agent_integration.py::test_existing_nodes_are_all_still_present`
  already pins that node set exhaustively server-side; its docstring now
  points here, so a node added to the graph has to be classified in the
  rail too rather than silently reading as a stage that never started.

Full suites after this work: **frontend 806 passed (72 files)**;
**backend 5487 passed**. `npm run lint` exit 0, `tsc --noEmit` clean,
`npm run build` succeeds. The frontend total moved during this work
(724 → 806) because a parallel session was adding tests to the same
working tree; the contribution from this change is the 43 cases in
`QueryLifecycle.test.tsx` plus the rescoping of two existing files.

A pre-existing flake was also fixed: `TurnCard.analytics.test.tsx` waits
on a genuine `lazy()` dynamic import, which can exceed
testing-library's 1s default when the whole suite runs in parallel (it
passed 5/5 in isolation but tripped under load). Those two `findByRole`
calls now carry a real 10s budget. Separately, the Copy-SQL test in
`SqlIntelligencePanel.test.tsx` leaked an unhandled rejection — jsdom
provides no `navigator.clipboard` — which vitest warns can cause false
positives elsewhere; it now stubs the clipboard and asserts
`writeText` received the SQL, so it also tests more than it did before.

Frontend: **910 passed** (77 files). Backend: **5492 passed**.
`npm run lint` exit 0, `tsc --noEmit` clean, `npm run build` succeeds.
(The frontend total includes concurrent additions by a parallel session
in the same working tree, so it is a point-in-time figure.)

**A caveat recorded here earlier has since resolved.** At the time of the
SQL-panel work, `npm run build` (`tsc -b && vite build`) failed on
`src/components/chat/QuestionNavigator.test.tsx`, which carried an unused
variable from a parallel session's in-progress edit in the same working
tree; that file was left alone rather than fixed mid-edit, and
`npx vite build` was used instead. That edit has since been completed, so
the full `npm run build` now passes. Kept as a note rather than deleted,
because the earlier commit was made under that condition.

## Notes and deferred work

- **A pre-existing flake was fixed**: `TurnCard.analytics.test.tsx` races
  the lazily-loaded analysis section because CodeMirror throws while
  measuring in jsdom. It failed intermittently *before* this pass too
  (verified by stashing the changes), and a heavier `TurnCard` made it
  trip more often. The CodeMirror editor is now stubbed in that one file
  — it tests the analysis section, not the editor.
- **A second pre-existing flake remains open**:
  `src/pages/TenantAdmin.test.tsx` intermittently fails under full-suite
  parallel load (passes 3/3 standalone). That file and its page are
  untouched by this pass; it is recorded here rather than silently
  tolerated.
- **Two TurnCard test files gained a `MemoryRouter` wrapper** because the
  component now renders a router `<Link>`. No assertion was weakened.
- **Lint warnings**: four new `react(only-export-components)` warnings,
  from exporting a pure helper beside its component so the helper can be
  unit-tested directly. The same rule already had pre-existing instances
  (`ui/toast.tsx`); it is a fast-refresh DX hint, not an error, and lint
  exits 0.
- **Not persisted**: pinned artifacts and the context panel are
  session-scoped, like `queryHistory` itself. Persisting artifacts would
  need a server-side artifact store — a larger decision than this
  surface warrants.
- **Not deep-linked**: the lineage link opens the explorer without
  pre-seeding a database, because that page resolves its own selection.
