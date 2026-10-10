# Conversational Intelligence UI — audit and implementation

Ten UI capabilities were requested for the chat workspace. The audit
found that **four already existed in full** and three more existed in
parts; this pass added what was genuinely missing and consolidated what
was scattered. Nothing was rebuilt that already worked.

## Feature-by-feature status

| # | Capability | Status | What existed | What changed |
|---|---|---|---|---|
| 1 | Question Navigator | **Already complete** | `chat/QuestionNavigator.tsx` + tests, wired in `Chat.tsx` | Nothing |
| 2 | AI SQL Intelligence Panel | **Consolidated** | `QueryPlanPanel`, `SchemaContextPanel`, `GoverningMetricsPanel`, a "Query information" expander — four sibling expanders | New `sql/SqlIntelligencePanel.tsx` groups them into one; the two panels' bodies were extracted (`QueryPlanContent`, `SchemaContextContent`) so nothing is re-rendered or duplicated |
| 3 | Query Validation & Execution Status | **Added** | `cost_notice`, `RetryTimeline`, a duration badge, a truncation flag — each in a different place | New `sql/ExecutionStatusBar.tsx`: one scannable badge row. The duplicate duration badge was removed from the results area |
| 4 | Interactive Analytics Workspace | **Already complete** | `analytics/AnalyticsSummary.tsx` + 12 panels + `ChartSection` | Nothing |
| 5 | Data Source & Lineage Panel | **Linked** | `SchemaContextPanel`, `SourcesUsedPanel`, a separate `/data-lineage` page | A link from the SQL intelligence panel to the lineage explorer |
| 6 | Grounded Insight / Evidence Panel | **Already complete** | `analytics/EvidencePanel.tsx`, `TruthLevelBadge` | Scoped to its name: the follow-up chips moved out (see #10) |
| 7 | Analysis Artifact Workspace | **Added** | — | New `chat/ArtifactWorkspace.tsx` + `PinTurnButton`, and `pinnedEntryIds`/`togglePinned` on the existing store |
| 8 | Conversation Context Manager | **Added** | `ConversationList`, `ConversationSearch` (history navigation) — but nothing showed the follow-up window actually sent | New `chat/ConversationContextPanel.tsx`, calling the same `buildConversationHistory` the request payload uses |
| 9 | Agent Execution Timeline | **Added (needed backend)** | `RetryTimeline` (attempt outcomes only); `stage_timings` was recorded server-side but never exposed | `StageTimingOut` + `AskResponse.stage_timings`; new `chat/AgentExecutionTimeline.tsx` |
| 10 | Intelligent Follow-up Recommendations | **Surfaced** | `next_question` recommendations rendered inside `EvidencePanel` — lazy-loaded, nested in the analytics expander, only after Confirm and Run | New `chat/FollowUpSuggestions.tsx` renders them directly under the answer. **One renderer, not two**: removed from `EvidencePanel` |

## API / schema changes

One addition, additive and optional:

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
| `ExecutionStatusBar.test.tsx` | 16 |
| `FollowUpSuggestions.test.tsx` | 14 |
| `SqlIntelligencePanel.test.tsx` | 8 |
| `ConversationContextPanel.test.tsx` | 9 |
| `ArtifactWorkspace.test.tsx` | 12 |
| `tests/test_api_ask.py` (backend) | 3 |
| **New total** | **76** |

Frontend: **654 passed**. Backend: **5483 passed**. Lint exit 0,
typecheck exit 0, production build clean.

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
