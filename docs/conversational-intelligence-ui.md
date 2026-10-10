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
| 3 | Query Validation & Execution Status | **Added** | `cost_notice`, `RetryTimeline`, a duration badge, a truncation flag — each in a different place | New `sql/ExecutionStatusBar.tsx`: one scannable badge row. The duplicate duration badge was removed from the results area |
| 4 | Interactive Analytics Workspace | **Already complete** | `analytics/AnalyticsSummary.tsx` + 12 panels + `ChartSection` | Nothing |
| 5 | Data Source & Lineage Panel | **Linked** | `SchemaContextPanel`, `SourcesUsedPanel`, a separate `/data-lineage` page | A link from the SQL intelligence panel to the lineage explorer |
| 6 | Grounded Insight / Evidence Panel | **Already complete** | `analytics/EvidencePanel.tsx`, `TruthLevelBadge` | Scoped to its name: the follow-up chips moved out (see #10) |
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
| `ExecutionStatusBar.test.tsx` | 13 (rescoped) |
| `FollowUpSuggestions.test.tsx` | 14 |
| `SqlIntelligencePanel.test.tsx` | 25 |
| `ConversationContextPanel.test.tsx` | 9 |
| `ArtifactWorkspace.test.tsx` | 12 |
| `tests/test_api_ask.py` (backend) | 7 |
| **New total** | **94** |

Frontend: **666 passed** (68 files). Backend: **5487 passed**. Lint exit
0; `tsc` clean across every file this work touched.

**One caveat, honestly stated:** `npm run build` (`tsc -b && vite build`)
currently fails on `src/components/chat/QuestionNavigator.test.tsx`,
which has an unused variable from another session's in-progress edit in
this same working tree. That file is untouched by this work and was left
alone rather than fixed mid-edit. `npx vite build` succeeds (2749 modules
transformed), and `tsc` reports no error in any file this pass changed.

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
