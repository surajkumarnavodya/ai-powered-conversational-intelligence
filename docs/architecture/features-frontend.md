# Frontend features — invariants and limitations

The React dashboard: the UI redesign, role-based navigation, the chat
workspace's panels, charting, and the analysis artifact workspace.

Moved verbatim out of the root `CLAUDE.md`. The recurring rule across all of
it: **no UI-level restriction is a security boundary** — every one is UX
only, and the server re-checks every request.

## Contents

- [Frontend UI redesign (2026 UI pass)](#frontend-ui-redesign-2026-ui-pass)
- [Responsive layout and the conversation reading column](#responsive-layout-and-the-conversation-reading-column)
- [Analytics & Insights panels in the answered turn (Prompt 30)](#analytics--insights-panels-in-the-answered-turn-prompt-30)
- [Recommendation & action dashboard (Prompt 31)](#recommendation--action-dashboard-prompt-31)
- [Role-based navigation, server-driven access & the onboarding→SME bridge (Prompt 32)](#role-based-navigation-server-driven-access--the-onboardingsme-bridge-prompt-32)
- [Conversational Intelligence UI (chat workspace, 2026-10)](#conversational-intelligence-ui-chat-workspace-2026-10)
- [Follow-up recommendation chips ("Ask next")](#follow-up-recommendation-chips-ask-next)
- [Agent execution timeline (stage-level status)](#agent-execution-timeline-stage-level-status)
- [SQL structural description in the query-metadata grid (2026-10-17)](#sql-structural-description-in-the-query-metadata-grid-2026-10-17)
- [Accessibility: static lint plus runtime axe checks (2026-10-17)](#accessibility-static-lint-plus-runtime-axe-checks-2026-10-17)

---

## Frontend UI redesign (2026 UI pass)
A ground-up-in-appearance, additive-in-substance redesign of `frontend/`
into a left-sidebar/main-workspace layout. **Invariant that must not
regress:** no backend logic, API contract, auth flow, SQL validation, or
chat-persistence behavior changed as part of this pass or its two
follow-up bug-fix passes (duplicated Settings/Sign-out controls; a
dark-mode-only modal-transparency bug from an inherited "glass" CSS
token) — every change is presentation/interaction layer only.

**Read, per topic:**
- [`docs/frontend-ui-audit.md`](../frontend-ui-audit.md) — the Phase 1 audit this pass started from.
- [`docs/ui-design-system.md`](../ui-design-system.md) — the CSS token set and shared `components/ui/` primitives added.
- [`docs/chat-history-ui.md`](../chat-history-ui.md) — the sidebar/settings split (`Sidebar.tsx`, `MobileNav.tsx`, `SettingsDialog.tsx`) and the extracted, independently-tested history-search components.
- [`docs/image-editing-architecture.md`](../image-editing-architecture.md) — the local image editor (Konva/react-konva). At the time of this UI pass, AI-guided editing was a deliberate, permanent stub; it was later implemented for real — see "AI-guided (generative) image editing" further below, which supersedes that original limitation.
- [`docs/ui-production-audit.md`](../ui-production-audit.md) — the duplicated-controls fix (`UserMenu.tsx`, sidebar collapse).
- [`docs/settings-modal-visual-bug.md`](../settings-modal-visual-bug.md) — the dark-mode transparency fix (`--modal-backdrop`/`--modal-surface` tokens).
- [`docs/navigation-and-actions.md`](../navigation-and-actions.md) — the resulting one-action-one-owner table for every control in the shell.


## Responsive layout and the conversation reading column
A later, presentation-only pass over the same shell: the conversation now
has **one** reading measure — `--chat-column` (48rem), surfaced as the
`max-w-chat` utility — shared by the turns, the composer and the shared
read-only view, with the avatar-width `pl-10` insets removed so a
question, its answer and the composer line up on one pair of edges. The
shell adapts at `sm`/`lg`/`xl`: the composer's controls drop below the
textarea on a phone, the sidebar appears at `lg`, and
`QuestionNavigator`'s flyout widens by breakpoint (its right-edge rail is
present at every size, clamping the panel to the viewport on a phone).
That flyout expands on hover and collapses when the pointer leaves, after
a short grace period that keeps it from flickering shut mid-travel; touch
users tap the rail icon to open it and tap outside to dismiss.

**Invariant:** the same one as the redesign above — no backend logic, API
contract, auth flow, SQL validation or chat-persistence behavior changed;
`/ask` → "Confirm and Run" → governed execution is untouched.

**Authoritative:** [`docs/ui-responsive-layout.md`](../ui-responsive-layout.md)
— the measure, what shares it, the per-breakpoint table, how it was
measured across ten viewport widths, and what that verification does
*not* cover (no real devices, no touch or on-screen-keyboard behaviour).


## Analytics & Insights panels in the answered turn (Prompt 30)
No new page: the analysis renders under each answered question's insight
in `TurnCard.tsx` (`frontend/src/components/analytics/AnalyticsSummary.tsx`),
gated like the insight itself (after "Confirm and Run", and only while the
SQL box still holds the SQL that produced the answer). It is lazy-loaded
like `ChartSection`, so Chart.js stays out of the main bundle -- do not
statically import any analytics panel from `TurnCard.tsx`.

It renders only from already-computed backend state and never recomputes a
statistic. `AskResponse` gained four additive pass-through fields
(`analytical_intent`, `analytical_plan`, `governing_metrics`,
`restricted_field_notice`); `ExecuteResponse.cache_status` mirrors the
`X-Cache` header into the JSON body. The restricted notice is a presentation
translation of an existing failure category; the restricted-column gate and
its retry behavior are unchanged.

Every database-originated label goes through `lib/analyticsCharts.ts`'s
`sanitizeLabel` before it is shown or fed into a drill-down question (master
rule 8). Every claim shows its truth level (`TruthLevelBadge`). Charts reuse
`ResultChart`/`PreparedChart`; no second chart engine.

Known gaps (see the contract for detail): reloaded past turns show no
analysis (analytics fields are not in persisted message metadata);
`cache_status` is session-only; `visualization_spec` is still not consumed;
root-cause attribution is not wired into the live graph; query scope is
read-only.

**Read [`30_ANALYTICS_INSIGHTS_DASHBOARD_CONTRACT.md`](../../30_ANALYTICS_INSIGHTS_DASHBOARD_CONTRACT.md)**
for the full prompt text and outcome summary.


## Recommendation & action dashboard (Prompt 31)
`frontend/src/pages/Recommendations.tsx` (route `/recommended-actions`, not `/recommendations`, which is the API list path; nav tab shown by the server's navigation policy) is the reviewer-facing surface over the governed recommendation APIs from Prompts 18/31. Read `31_RECOMMENDATION_ACTION_DASHBOARD_CONTRACT.md` before changing it.

**Invariants that must not regress:**
- Every recommendation is shown as an **AI estimate**, with its evidence and each claim's truth level. Nothing is presented as a confirmed fact (master rule 10).
- Status transitions are **race-safe**: `identity/repositories/recommendation_governance.py::_apply_conditional_status_update` is a conditional `UPDATE ... WHERE status = expected`. Do not replace it with a read-check-then-write. `tests/test_recommendation_actions_repository.py`'s race test fails if the guard is removed.
- **Notes and owner changes never change `status`.** They are `RecommendationFeedbackEvent` rows with `event_type` `note` / `owner_assigned`, in the same append-only trail.
- An owner must be an active, same-tenant user who holds a recommendation-review permission. A refused owner gets the same 404 whether the user is missing, cross-tenant, inactive, or not a reviewer.
- Every governed mutation is logged (`event=recommendation_action`) without claim, evidence, or note text, and is rate-limited after authorization.
- Button visibility on the frontend mirrors the lifecycle for UX only. The server re-checks every request.

**Known gaps:** local accounts only (`require_local_user`); the list is capped at 200 with no pagination; owner changes are last-writer-wins; the rate limit is per IP.


## Role-based navigation, server-driven access & the onboarding→SME bridge (Prompt 32)
`32_ROLE_BASED_NAVIGATION_CONTRACT.md` is the full record. The short version a future session needs:

- **Screens and actions come from the server.** `security/navigation.py` (pure policy) decides which screens and capabilities a caller has, from the same permission checks the real routes make. `GET /navigation` returns them (`api/navigation.py`). The frontend draws the header tabs from `items`, guards each route with `components/auth/RequireNavItem.tsx`, and reads named actions from `capabilities` through `useCapabilities()`. **Never add a role-name check in the frontend.** Add a `CapabilityRule`, and a test that pins it.
- **Hiding a tab is not access control.** A URL typed directly shows the forbidden page, and `POST /navigation/access-denied` audit-logs the refusal (`event=ui_route_denied`), but only for a real screen path. Every API route still enforces its own permission on the server.
- **Platform operators need both `platform_admin` and `admin`.** `platform_admin` is not a base role (`agent.authz.ROLE_PERMISSIONS`), so on its own it has no AI workspace. The navigation test pins this on purpose.
- **Recommendations live at `/recommended-actions`.** `/recommendations` is the governance API's JSON list route. A reload of the page there would have hit JSON. The Vite dev proxy also skips HTML requests now, so a reload of any page path that is also an API prefix reaches the app.
- **Onboarding publish now creates DRAFT catalog entities** (`onboarding/catalog_bridge.py`), one per table with SME-confirmed items, under a slug of the job's label (`My Warehouse` → `My_Warehouse_<id8>`). The slug is a vector-store collection name, which rejects spaces. The bridge never publishes. A retried publish creates nothing new.
- **A job missing its connection details** answers 422 on discover and publish, not a generic 500 (`api/onboarding.py::_engine_for_job`).

**Known gaps:** a "Confirm and run" button is shown to any user who can ask a question, including a viewer who lacks `execute_sql`; the server refuses it, but the button is not gated on the capability yet. The "viewer" role has no screen of its own: "read-only viewer" maps to `auditor`. Browser-level end-to-end tests are not part of the project (no Playwright); the journey test is at the HTTP API layer.

---


## Conversational Intelligence UI (chat workspace, 2026-10)

Ten UI capabilities were requested for the chat workspace. The audit
found **four already complete** (`QuestionNavigator`, the analytics
workspace, the evidence panel, and the conversation list/search half of
context management) and three more existing in parts. This pass added
what was missing and consolidated what was scattered -- nothing that
already worked was rebuilt.

**The one genuinely missing piece of data:** `agent.nodes._timed_node`
has always recorded a per-node `StageTiming` into
`AgentState["stage_timings"]`, and `observability/metrics.py` aggregates
it -- but it was never exposed, so the UI could show retry *outcomes*
(`RetryTimeline`) and never where the time went. `AskResponse
.stage_timings` now carries it (new `StageTimingOut`, additive and
optional, defaulting to `[]`), feeding
`chat/AgentExecutionTimeline.tsx`. No new instrumentation: the data was
being discarded.

**New components:** `chat/AgentExecutionTimeline.tsx` (per-stage
durations, aggregated across retried calls, in pipeline order -- later
given per-stage status and made the single stage-level surface, see
"Agent execution timeline" below),
`sql/ExecutionStatusBar.tsx` (one scannable badge row for validation /
retries / cost / rows / truncation / duration / cache / governance),
`sql/SqlIntelligencePanel.tsx` (one expander replacing four siblings),
`chat/FollowUpSuggestions.tsx`, `chat/ConversationContextPanel.tsx`,
`chat/ArtifactWorkspace.tsx` + `PinTurnButton`.

**Invariants that must not regress:**
- **The status bar re-judges nothing.** Every badge reads a field the
  server already returned -- validation from `attempt_history`, cost from
  `cost_notice`, governance from `restricted_field_notice`. A
  client-side opinion about whether SQL is safe or a value may be shown
  would be a duplicated, drift-prone security decision; there is none.
- **Exactly one renderer per affordance.** `next_question`
  recommendations render only in `FollowUpSuggestions` (they were inside
  `EvidencePanel`, lazy-loaded behind the analytics expander and only
  reachable after Confirm and Run); `AnalyticsSummary.test.tsx` asserts
  their *absence* there so the two-renderer state cannot come back.
  Likewise `QueryPlanContent`/`SchemaContextContent` were extracted so
  `SqlIntelligencePanel` composes the existing panels rather than
  re-rendering their content.
- **An artifact is a reference, never a copy.** `pinnedEntryIds` is a
  `Set<string>` of `entryId`s on the existing `chatStore` (mirroring
  `goldenFeedbackGiven`), so pinning cannot duplicate rows or create a
  snapshot that disagrees with the turn it points at. Jumping reuses the
  `turn-${entryId}` anchor `QuestionNavigator` already relies on -- one
  scroll mechanism in the chat column, not two.
- **The context panel calls the request builder.**
  `ConversationContextPanel` renders `buildConversationHistory(entries)`
  -- the exact function that builds the `conversation_history` sent with
  every `/ask` -- so what it shows cannot drift from what is sent. A test
  asserts the rendered list equals that function's output.
- **`stage_timings` can never fail an answer.**
  `api.main._stage_timings_out` skips a malformed entry rather than
  raising, because that is an `operator.add` reducer channel a future
  node could append to. Pinned by a test.
- **Nothing new is exposed.** Node names and durations, plan steps,
  retrieved DDL, the chosen database/model, governed metric names -- all
  already in `AskResponse`. Tests assert the absence of prompt,
  reasoning and credential-shaped strings in the new panels.
- **Database-derived text stays untrusted.** Follow-up labels go through
  `sanitizeLabel` on display *and* on the way back into `onAsk`; a
  clicked chip re-enters the normal `/ask` path, so the input guard, the
  governance request policy and the SQL validator all apply to it.

**A pre-existing flake fixed in passing:**
`TurnCard.analytics.test.tsx` races the lazily-loaded analysis section
because CodeMirror throws while measuring in jsdom
(`textRange(...).getClientRects is not a function`). It failed
intermittently before this pass too -- verified by stashing the changes
and reproducing -- and a heavier `TurnCard` made it trip more often. The
editor is now stubbed in that one file, which tests the analysis section,
not the editor. **A second flake remains open and is not mine:**
`src/pages/TenantAdmin.test.tsx` fails intermittently under full-suite
parallel load while passing 3/3 standalone; that file and its page were
untouched.

**Known limitations:** pinned artifacts and the context panel are
session-scoped like `queryHistory` itself (persisting artifacts would
need a server-side artifact store); the lineage link opens the explorer
without pre-seeding a database, because that page resolves its own
selection.

**SQL Intelligence Panel refinement (same pass).** The panel now owns
*everything* about a turn's SQL behind one progressive disclosure:
collapsed it reads `SQL • Validated • 42 rows`; expanded it holds the SQL
itself, Confirm and Run, a metadata grid (statement type, tables
referenced, database, model, attempts, rows, duration), the plan,
governed metrics, retrieved schema and the lineage link. It replaced five
sibling expanders.

**Already present, so deliberately not rebuilt:** CodeMirror with
`@codemirror/lang-sql` gives `SqlEditor` syntax highlighting; `ui/CopyButton`
inside it already provides Copy SQL (`navigator.clipboard.writeText`
only); and `EditorView.lineWrapping` already keeps long SQL usable. No
highlighter, clipboard helper or second code-presentation mechanism was
added.

**New: `AskResponse.sql_summary`** (optional, `null` when absent) from a
new pure `agent.sql_validator.summarize_sql`, carrying `statement_type`
and `referenced_tables`. Those are the two facts the UI had no accurate
source for: the statement type is read from the parsed AST (a leading
comment or a `WITH` prefix cannot mislabel it), and the **referenced**
tables are not `schema_tables` -- those are the top-k candidates
*retrieval offered*, commonly a superset of what the query uses, so
labelling them "tables referenced" would be wrong. It reuses the same
parse and the same CTE-aware filtering
`find_unexpected_table_references` already performs, and gates nothing.

**Invariants that must not regress:**
- **The panel has no execution path.** Its only action calls the caller's
  `onConfirmAndRun` -> `POST /execute`, which re-validates and
  re-executes the *current* text server-side every time. An edit in the
  box is therefore exactly as untrusted as the original generation, and
  the panel cannot bypass validation. A test asserts clicking Copy does
  not invoke the run handler.
- **The SQL shown is the server's.** `entry.editableSql` is seeded from
  `state.sql`; nothing generates, rewrites or reformats SQL client-side.
- **A failed validation reads `SQL • Blocked • ...`, never "Validated".**
- **The cost notice is withheld once the SQL is edited**, because it
  describes the server's own query, not the edit.
- **Open until the turn has a result, collapsed after.** Burying the
  primary action behind a collapse would make the main path harder to
  reach; afterwards the summary line is the useful state.
- **No fact is stated twice.** The panel owns validation state, rows,
  duration, attempts and cost; `ExecutionStatusBar` was rescoped to the
  result-trust flags that must *not* hide behind a collapse (execution
  failure, truncation, cache freshness, governance). Both surfaces are
  visible at once, and `ExecutionStatusBar.test.tsx` asserts the moved
  facts are absent there so the duplication cannot creep back.

**A real bug mypy caught that the tests did not:**
`_sql_summary_out` passed the whole `DatabaseConnectionConfig` to
`get_sqlglot_dialect`, which takes `db_type: str`. At runtime that
returns `None` -- sqlglot's generic dialect -- and **T-SQL does not parse
under the generic dialect**, so every SQL Server query (all four
configured databases here) would have returned `sql_summary: null`. The
existing tests passed because an ordinary ANSI `SELECT` parses either
way. Fixed, and pinned by a mutation-tested regression
(`SELECT TOP 10 name FROM [dbo].[Employee]`). Worth remembering when
resolving a dialect anywhere: pass `config.db_type`, not the config.

**A caveat recorded here earlier has since resolved:** at the time of the
SQL-panel commit, `npm run build` (`tsc -b && vite build`) failed on
`src/components/chat/QuestionNavigator.test.tsx`, which carried an unused
variable from a parallel session's in-progress edit in the same working
tree (left untouched rather than fixed mid-edit; `npx vite build` was
used instead). That edit has since been completed and the full
`npm run build` now passes.

**Query lifecycle rail (same pass).** `sql/QueryLifecycle.tsx` is one
compact row saying how far a question got and where it stopped:
`✓ Understanding  ✓ Schema  ✓ SQL  ✓ Validation  ✓ Cost check  ● Ready to
run  ○ Execution`. Three run-related surfaces already existed and none
answered that question -- `RetryTimeline` answered "what happened on each
attempt" (since replaced, see "Agent execution timeline" below),
`AgentExecutionTimeline` "which stage was slow", `ExecutionStatusBar`
"can the result be trusted".

**Invariants that must not regress:**
- **A stage reads as reached only because `stage_timings` holds a real
  call to one of its nodes.** The rail is withheld entirely when
  `stage_timings` is empty (every turn reconstructed from saved chat
  history), rather than guessing a plausible rail. Nothing infers a
  success from the absence of an error.
- **`KNOWN_NODES` must equal the graph's real node set.** `STAGE_NODES`
  plus `POST_RESULT_NODES` enumerate Python node names, so a node added
  to `agent/graph.py` and classified in neither reads as a stage that
  never started -- silently, forever.
  `tests/test_sql_agent_integration.py::test_existing_nodes_are_all_still_present`
  pins that set server-side and its docstring points at the rail;
  `QueryLifecycle.test.tsx` asserts the two lists together cover it
  (mutation-tested: a renamed node fails 6 cases).
- **Stage grouping follows real execution order.** `retrieve_schema` runs
  *before* the golden-example/business-context/intent/plan nodes, so
  "Understanding" is only `sanitize_input`+`classify_followup` and the
  post-schema comprehension nodes group with `SQL`. The four
  post-execution analysis nodes are listed in `POST_RESULT_NODES` and
  shown as no stage -- their output is already rendered elsewhere.
- **"Ready to run" is the agent's own verification; "Execution" is the
  user's.** The graph executes candidates internally to self-correct, and
  that result is never rendered, so `ready` is the
  requires-confirmation state and `executed` reflects only Confirm and
  Run. Calling the internal run "Executed" would claim the user had seen
  a result they had not.
- **`blocked` (a deterministic gate refused) and `failed` (a stage could
  not finish) stay distinct**, read from the outcome name the server
  already recorded. `validate_sql_node` emits the single fixed
  `safety_violation` for its fail-closed class and the category only for
  retryable ones, so **no copy of `SAFETY_VIOLATION_TYPES` lives on the
  client**. An unmapped outcome falls back to the last stage that ran.
- **Stop reasons are fixed strings chosen by outcome class** -- never the
  server's raw error text, never the validator rule that fired
  (`system_catalog_access` and friends stay out of this always-visible
  rail; a test asserts it).
- **Renders for blocked/failed turns too**, gated only on it being a
  SQL-path turn -- deliberately not on `BLOCKED_STATUSES` like
  `showSqlPanel`. A refused run is when "where did this stop" matters
  most, and the rail carries no editor and no run control.
- **Stage labels are nouns, not past participles.** One label renders
  under all six statuses, so `Validation: blocked` is coherent where
  `Validated: blocked` has the label contradicting its status. Each
  status is carried as `sr-only` text, never by colour or icon alone.
- **No fact is stated twice.** Adding a fourth status surface meant
  re-cutting the split: the execution-failure badge left
  `ExecutionStatusBar` and validation state left `SqlIntelligencePanel`'s
  summary line (now just `SQL • 42 rows`). Both of those tests now assert
  the *absence* of the moved fact, and the behaviour is asserted in
  `QueryLifecycle.test.tsx` instead.

**There is no streaming, so this is final-state rendering.** `POST /ask`
is a single request returning once the graph has finished; there is no
SSE/WebSocket endpoint anywhere in `api/`, so no "currently on stage N"
signal exists mid-run. `running` is used only where genuinely
observable: the user's own Confirm and Run. `deriveLifecycle` takes
exactly one required argument -- the completed response -- which is the
structural form of that statement. The in-flight `/ask` phase keeps
`PendingTurn`'s "Thinking Ns…" badge; a rail with a guessed active stage
was considered and rejected. **Zero backend changes were needed** --
`stage_timings` and `attempt_history` already carried everything.

**Evidence status on every claim (same pass).** Three of the five labels
the brief named already existed in `TruthLevelBadge` (`Computed from
data`/`Approved definition`/`AI estimate`) and ~12 analytics panels
already rendered one. Two did not exist at all (`Source-backed`, `Not
available`), and two prominent claims carried **no label whatsoever**:
the insight narrative (a sparkle box) and a document/policy/web answer.
`EvidencePanel` was also, despite its name, a *recommendations* panel.

**Invariants that must not regress:**
- **`EvidenceStatus` (`analytics/EvidenceBadge.tsx`) is orthogonal to
  `TruthLevel`, not a replacement** -- the same split `briefing/models.py`'s
  `ClaimClass` already makes against `agent.provenance.DataTruthLevel`.
  Two of its five states cannot be a `TruthLevel`: a cited external
  source is not something this platform computed, and "no evidence" is
  not a confidence level. `TruthLevel` itself is reused unchanged.
- **`EvidenceBadge` is the single owner of all five labels and tones.**
  `TruthLevelBadge` is now a thin adapter over it; two hand-rolled
  `AI estimate` badges in `recommendations/RecommendationList.tsx` and
  `RecommendationDetail.tsx` were replaced with the shared one. Four
  copies of one label became one.
- **`data-truth-level` is preserved** -- `AnalyticsSummary.test.tsx` and
  `DecisionCenter.test.tsx` assert on it. Stamped only when the status
  came from a real server `TruthLevel`; a `source_backed`/`unavailable`
  badge has none and carries no such attribute. `EvidenceList` therefore
  takes the `TruthLevel`, not a pre-derived status -- deriving at the
  call site silently dropped the attribute, which is how that signature
  was arrived at.
- **The insight is labelled `AI estimate`, deliberately.** Every number
  in it passed `agent.insight.is_insight_grounded` server-side (an
  insight with an unsupported number is *dropped* and never reaches the
  client, so the brief's "never present an unsupported number as a
  verified fact" held structurally already) -- but the wording and any
  causal framing are the model's. Calling the whole sentence "Computed
  from data" would extend the numbers' verification to an interpretation
  nobody verified. The expandable evidence states what was actually
  checked. Contrast the brief's own example: "Revenue increased 18%" *is*
  `Computed from data`, because that comes from `analytics.engine`'s
  deterministic `GrowthStat`, not the narrative.
- **`Source-backed` requires a real citation.** An answer with
  `citations == []` is `Not available` -- reachable via `rag/graph.py`'s
  insufficient-information fallback *and* via a withheld
  `sensitivity_category`/`restricted_roles` match. **Claiming a source
  for an answer whose supporting material was withheld would turn an
  access control into a false endorsement**, so this is a security
  property, mutation-verified.
- **The claim text and its status badge are visible in every
  expand/collapse state**, so the status can never be the thing hidden.
  The open default is the caller's, because it differs by context: the
  insight starts collapsed (dense answer area), recommendation evidence
  starts **open** because it was always visible before -- caught by a
  failing pre-existing test, not by review.
- **`agent/insight.py` is untouched** -- no change to `summarize_result`,
  `_INSIGHT_SYSTEM_PROMPT` (benchmark-pinned) or the drop behaviour.

**One additive backend field, `AskResponse.insight_numbers_checked`.**
The UI could have asserted "numbers verified" from the server's
drop-if-ungrounded behaviour alone, but that makes the claim an
assumption about server control flow that a future change would falsify
silently. `null`/`0`/`n` are three different statements and the UI says
three different things: nothing about verification / "states no figures
of its own" / "n figures were checked against the 42 returned rows". The
count is `len(extract_numbers(text))` taken *after* grounding passed, so
it is exactly the count verified -- confirmed against the real functions
(2, 1, 0 for three realistic insights).

**Analysis artifact workspace (same pass).** The one part of this series
that needed persistence. A session-only pinned worklist already existed
(`pinnedEntryIds` on `chatStore`); it could not carry a source id, a
timestamp, lineage, a result reference, tenant ownership, or anything to
refresh, so a real `analysis_artifacts` table now sits behind the same
control. New `artifacts/` (pure: `models.py`, `staleness.py`,
`policy.py`), `identity/repositories/artifacts.py`, `api/artifacts.py`
(5 routes), migration `p8t1u4v7w0x3`.

**Invariants that must not regress:**
- **An artifact stores no result rows.** It references the `Prompt` row
  (source question id), the `AiOutput` row (answer/result id), and keeps
  only `origin_row_count`/`origin_captured_at`/`origin_fingerprint` about
  the snapshot `api.chat_persistence.persist_execute_result` already
  wrote into `ai_outputs.metadata.result_snapshot`. The only rows it owns
  are a refresh's, because those exist nowhere else.
- **A refresh never writes back into the referenced turn.** Mutating that
  turn's snapshot would make a saved conversation show numbers that were
  never the answer to that question at that time -- the mirror image of
  chat history's "opening a saved conversation never re-executes SQL".
  Both results are reported, with both timestamps.
- **Owner-scoped, not just tenant-scoped.** Unlike every other
  tenant-scoped resource here, an artifact points at rows
  `identity/repositories/history.py` scopes to one `user_id`; a
  tenant-wide artifact would be a readable back door into a colleague's
  private question. `artifacts/policy.py` matches tenant **and** owner,
  and either mismatch is the same 404 a nonexistent artifact gets. An
  admin of the same tenant gets that 404 too.
- **A stored snapshot is re-governed on every read.**
  `govern_rows_for_caller` runs against the **current** caller's roles
  each time an artifact is opened, and the recorded SQL is withheld (a
  fixed sentence, never the column name) when
  `find_restricted_column_references` says it touches a restricted field.
  Roles change: without this, an analyst who saved an analysis over a
  restricted column and later lost `view_restricted_columns` would keep a
  permanent unprivileged window onto it, one no part of the live pipeline
  could close because nothing re-executes. Mutation-verified.
- **Refresh is gated on `Permission.EXECUTE_SQL`**, the permission
  `POST /execute` itself requires -- so a `viewer` who may ask but not
  run cannot make a saved artifact run for them. **No new permission code
  was invented**; the base-role vocabulary already drew the distinction.
  Refresh reuses `POST /execute`'s own governed sequence verbatim and
  accepts no SQL from the caller; the stored statement is re-validated
  every time.
- **Freshness is computed on every read and never stored.** There is no
  column to go wrong, pinned by an exact-column-set test.
  `artifacts.staleness.assess_freshness` is pure and takes a
  caller-supplied `as_of`. Five states (`current`/`aging`/`stale`/
  `source_missing`/`no_result_captured`) and accumulating reasons; a
  failed refresh is never the standing result; an unknown schema
  fingerprint draws no conclusion; `result_changed_on_refresh` may appear
  on a `current` artifact because it describes the superseded original.
- **Change detection needs both halves**: the fingerprint covers the
  capped stored rows, the row count is the query's uncapped total. A
  change past the cap within an unchanged total is disclosed, not claimed
  as covered.
- **Anything that stores or hashes a result goes through
  `api.result_serialization.rows_to_json` first.** A SQL Server `decimal`
  cell otherwise fails the JSONB write outright, and `default=str` would
  hash it as a quoted string against the origin's bare number -- so every
  refresh would have reported "the numbers moved". Found by a parallel
  session extracting that module from `api/main.py`; this refresh path is
  its second caller.
- **Navigating back costs no new mechanism**: a restored turn's `entryId`
  *is* its `Prompt` row id, so `resolveArtifactAnchor` reuses the
  existing `turn-${entryId}` anchor.

**Known limitations:** local accounts only (`require_local_user`) -- the
same limitation chat history itself has; without one the control falls
back to the pre-existing session pin and the panel says so. Soft delete
only, capped by `ARTIFACT_MAX_PER_USER` (a further save is refused, never
a silent eviction). Schema-change detection reads the schema index's own
manifest, so a change nothing has discovered yet is invisible. The
refreshed result renders as a table in the panel; the original capture
does not (it already renders in the turn the artifact points at).


## Follow-up recommendation chips ("Ask next")

`RecommendationKind.NEXT_QUESTION` and the chip component
(`chat/FollowUpSuggestions.tsx`) both already existed. **What did not was
a producer**: the only two anywhere in this codebase were
`analytics.forecasting.recommendations_for_forecast` and
`analytics.scenario.recommendations_for_scenario`, and both are disclosed
above as not wired into any live path, while `recommendation.engine`
itself produced zero. The chips were real UI over a permanently empty
list -- in the live `/ask` pipeline they never rendered at all.
`recommendation/followups.py` is that missing producer.

It produces **candidates, not `Recommendation`s**:
`recommendation.engine._finalize_candidate` stays the single construction
point, so the evidence floor, the confidence floor
(`RECOMMENDATION_MIN_CONFIDENCE`) and the restricted-column authorization
gate all apply to a chip exactly as to any other rule. The engine runs it
last, so every real finding stays ahead of every chip.

**Everything a chip is grounded in is a fact the request already
computed** -- no new query, no vector lookup, no LLM call. Result shape
and findings from `analytics.engine` (a `RANKING` finding's own
`DATABASE_FACT` claim is reused verbatim as the evidence for "Top 5", a
`GROWTH` finding's for a period comparison); the introspected DDL of the
tables retrieval already selected; and `governing_metrics`/
`governing_dimensions`, which are `CONFIRMED_BUSINESS_TRUTH` published
catalog entries. `extract_governing_dimensions` (Prompt 37, until now
with no live caller) finally has one. The question and the conversation
are used **only to suppress** a repeat -- user input is never evidence.

**Invariants that must not regress:**
- **"Never suggest what the data cannot answer" is structural.** A chip
  naming a column exists only because that column was found in real
  introspected DDL; "Top N" only because a real ranking with more than N
  entries was computed (three rows are already their own top 5). An
  empty result, a zero row count, or a missing `AnalyticsResult` all
  yield **nothing** rather than a guessed shape.
- **Restricted fields are filtered twice, and the first filter ignores
  role.** A column classified at or above
  `DataClassification.CONFIDENTIAL` is never named, for any caller: a
  chip *proactively steers* someone toward a field, a stronger act than
  reporting a statistic they asked for, and withholding costs nothing
  (they can still type the question). The engine's existing
  `_authorized` check is the second gate. Note **which** classifier:
  `config/sensitive_columns.yaml` ships empty by design, so a filter
  reading only that file protects nothing on a real database (the gap
  `docs/DATA_PRIVACY_GOVERNANCE_ENGINE.md` names) --
  `governance.classification` is the layer that actually infers from the
  column name, so it is the one consulted.
- **Deduplication is by column, not only by text.** The governed and
  schema breakdown rules can propose the *same column* under two names
  ("By Sales Region" from the catalog, "By Region" from the column) --
  two choices to read, one in substance. The governed rule runs first,
  so the approved name wins. Dedup is on "every column already
  claimed", not "any", so a breakdown sharing only a measure column is
  still offered.
- **Short label, complete question.** `action` carries the chip text
  (`MAX_LABEL_CHARS` = 24), `claim.value` the full question a click
  submits; the whole question is the button's accessible name, so a
  screen-reader user is never asked to act on "By Region" without
  knowing what it asks. A name too long to scan is **dropped, not
  truncated** -- "By Regional Sales Terr…" tells the reader nothing.
- **No SQL, anywhere.** A chip carries natural language; the frontend
  builds no query. A click re-enters the ordinary `POST /ask` path, so
  `agent.input_guard`, the governance request policy and the SQL
  validator all apply as they would to a typed question. Asking is also
  not running: "Confirm and Run" is still required, so a click can
  never execute anything.
- **A new `RecommendationCategory.EXPLORATION`**, not one of Prompt 17's
  nine reused (a follow-up is not a finding about performance or revenue,
  and reusing one would make a chip indistinguishable from a real
  finding) and not `None` (which already means "pre-taxonomy").

**Reuse rather than a second parser:** `db.schema_introspection
.extract_ddl_column_types` is new, sharing `extract_ddl_column_names`'
own line-skipping rules, and `agent.plan_validator._column_type_family`
was refactored onto it -- one DDL type lookup in this codebase, not two.

**Four real bugs its own tests and the type checker caught:** the cap was
checked *after* appending, so `MAX_FOLLOW_UP_SUGGESTIONS=0` still leaked
one chip; the same column was offered twice under two names; an
exhaustive-equality test pinned the category enum to nine (working as
designed); and `Record<RecommendationCategory, string>` in
`lib/recommendationDisplay.ts` stopped compiling until the new category
had a label.

**Known limitations:** chips need the analytics engine on
(`ENABLE_ANALYTICS_ENGINE`) -- result shape is a required grounding
input. A dimension candidate is any non-key text-family column, since
DDL carries no cardinality, so a high-cardinality text column can be
offered. Questions are templated, not LLM-phrased -- deliberate (no new
LLM call, and determinism), at the cost of some naturalness. A reloaded
past turn shows no chips, since `recommendations` are not in the
persisted message metadata (the same disclosed limitation the analytics
panels carry).

**Read [`docs/conversational-intelligence-ui.md`](../conversational-intelligence-ui.md)**
for the feature-by-feature audit table, the API change, the
accessibility/performance/security review and the full test counts.


## Agent execution timeline (stage-level status)

`chat/AgentExecutionTimeline.tsx` is the one stage-level record of a run:
collapsed it reads `Agent completed • 2.75s in 7 stages • 1 retry`;
expanded it lists each stage that did something, with `✓` / `↻` / `✗`, its
duration, its call count, and -- for the stage that stopped the run -- a
user-safe sentence. It began as a duration-only bar chart with mechanical
labels ("Generate sql"); this pass gave it status, retries, failure
reasons and readable operational labels.

**Per-stage status is recorded by the server, not re-derived by the
client.** `agent.state.StageTiming` gained `outcome`
(`ok`/`error`/`no_op`) and `error_category`, set by
`agent.nodes._stage_outcome` from **the dict that node itself returned**.
Reading the merged state instead would blame the wrong node:
`last_error_category` persists across a retry, so `generate_sql`'s second
attempt would inherit `validate_sql`'s first-attempt violation. No new
instrumentation -- `_timed_node` already computed both values for its
`[trace]` line and discarded them. A node that *raises* appends no timing
at all, so a record's presence already means "this call completed".

**Invariants that must not regress:**
- **Only stages that were applicable are listed.** A stage whose every
  call is `no_op` contributed nothing -- an optional feature switched off
  (`plan_query` with `ENABLE_QUERY_PLANNING=false` returns only `status`),
  or an enabled one that genuinely found nothing
  (`retrieve_golden_examples` with no match above the similarity floor).
  On a plain lookup question 10 of 17 node calls are `no_op`, so listing
  them would bury the seven that did the work. Their duration still counts
  toward the total, and the number omitted is stated under the list rather
  than left to be noticed.
- **No internals, ever.** `stage_timings` carries only fixed node names,
  durations, an outcome enum and an error *category*; the panel renders a
  fixed sentence chosen by category, never the category name and never the
  server's own error text. Reusing `lifecycle.reason.*` keeps one set of
  failure wording in the app. The category vocabulary is deliberately
  *not* `AttemptRecord.outcome` (`syntax` here is `syntax_error` there),
  so the two maps stay separate rather than silently mismatching.
- **There is no active/streaming state, because there is no stream.**
  `POST /ask` is one request; there is no SSE or WebSocket route in `api/`,
  so no "currently on stage N" signal exists mid-flight. The panel renders
  final state, the in-flight phase keeps `PendingTurn`'s live counter, and
  nothing polls -- the one response carries the whole timeline. Inventing
  a plausible in-progress rail would be guessing.
- **One renderer per fact.** This panel owns retries and per-stage
  duration. `RetryTimeline` was **deleted**: it listed the same retries but
  rendered each attempt's raw `str(exc)` and raw outcome codes.
  `SqlIntelligencePanel`'s bare attempt count was removed too (its test now
  asserts the absence, so the duplication cannot creep back).
  `QueryLifecycle` keeps its coarse, always-visible safety-gate rail and
  its reason sentence -- a different question at a different granularity.

**A real bug this found and fixed.** The graph registers
`add_node("estimate_cost", ...)` but the node was decorated
`@_timed_node("estimate_query_cost")` -- the only one of eighteen where the
two identifier spaces disagreed. `docs/ARCHITECTURE.md` had disclosed that
as harmless, correctly so when written; it stopped being harmless once
`QueryLifecycle` began deciding which stage a run reached by matching
`stage_timings[].stage` against node keys, which silently meant its
always-visible "Cost check" chip **could never read as reached on any
run**, however long cost estimation actually took. Its own tests passed
because their fixtures used the node name, not the label the server
actually records -- the test encoded the same assumption as the code.
Renamed to the single canonical node key, and
`tests/test_agent_nodes.py::TestStageTimingLabels` now pins every
`_timed_node` label to a real graph node name, comparing the two spaces
against each other rather than against a hand-written list.

**Known limitations:** the 18 stage labels are English-only, as the
mechanical labels they replaced were (the panel's own chrome is
translated), with a derived fallback so a newly added node still gets a
readable label rather than a raw identifier; and a turn reconstructed from
saved chat history shows no timeline, since `stage_timings` is not
persisted -- the same disclosed gap the lifecycle rail already has.

---

## SQL structural description in the query-metadata grid (2026-10-17)

`SqlIntelligencePanel.tsx` showed *what* SQL was generated and whether it
validated; nothing said how the tables were joined, what was filtered, what
was aggregated, or at what grain. Six rows now join the **existing**
metadata grid — Joins, Filtered on, Aggregates, Grouped by, Ordered by, and
Duplicate rows (rendered only when `DISTINCT` is applied) — alongside the
Statement type and Tables referenced rows that were already there. No new
component, and each row is omitted when the statement has nothing to say
for it, so a simple query does not render six empty rows.

**The server returns structured facts; the frontend owns the prose.** The
fields on `SqlSummaryOut` are lists of identifiers and a boolean, and the
labels come from i18n keys added to all five locales (`sqlPanel.joins`,
`filterColumns`, `aggregates`, `groupBy`, `orderBy`, `distinct`,
`distinctRemoved`). English prose from the server would have made the
feature untranslatable in a five-locale app — the same reason the stage
labels in the execution timeline are the exception that gets called out
rather than the rule.

What the panel deliberately cannot show, because the server does not send
it: no row limit (the `TOP n` in the SQL is this platform's governance cap,
not the query's intent), no business interpretation of the grain, and no
filter *values* — column names only, since a predicate can carry data and
this grid is rendered and may be logged. Full rationale:
[`../API.md`](../API.md) under `POST /ask`.

**It gates nothing.** The description is read from the same AST
`validate_sql` already parsed, so it cannot invent a join, but it is also
not a verdict: `agent/sql_validator.py` remains the only thing that decides
whether SQL may run, and a backend test asserts `validate_sql` still
rejects `DELETE` while `describe_sql` happily describes it.

---

## Accessibility: static lint plus runtime axe checks (2026-10-17)

The UI had aria labels, focus management and responsive layout throughout,
and 53 of 88 test files already queried by role or label — but **nothing
asserted any of it stayed true**. Two layers now do, and they catch
genuinely different things.

**Static.** oxlint 1.82 already shipped a `jsx-a11y` plugin that was simply
not enabled — `frontend/.oxlintrc.json` listed only `react`, `typescript`
and `oxc`. Enabling it cost zero dependencies and surfaced 28 findings.

**Runtime.** `frontend/src/test/axe.ts` wraps `axe-core` — Deque's own
engine, a dev dependency only and unreachable from the app entry, so Vite
cannot bundle it. It was chosen over the `vitest-axe` matcher deliberately:
that package is `0.1.0`, last modified January 2025, and is a thin wrapper
around this same engine, so for a repository that digest-pins base images
and justifies every CVE ignore individually, depending on the maintained
first-party engine and writing the fifteen-line matcher ourselves is the
cheaper long-term position.
`frontend/src/components/ui/accessibility.test.tsx` runs it against shared
primitives, an open dialog and a chat message.

**The runtime layer is what proved three static rules were false
positives**, which is why they are off *with evidence* rather than for
noise. `jsx-a11y/label-has-associated-control`,
`jsx-a11y/heading-has-content` and `jsx-a11y/prefer-tag-over-role` cannot
see text nested two levels deep, nor know that `children` exist:
`ShareModal`'s access-mode radios (a `<label>` wrapping an input plus
nested spans) and `card.tsx`'s `CardTitle` (`<h3 {...props} />`, taking
content via children) both render with **no** violation — while a genuinely
empty `<h3 />` *is* still caught, so the engine is not merely permissive.

**Non-vacuity is proven, not assumed.** One test renders an icon-only
button with no accessible name and asserts axe reports `button-name`, so if
axe ever stopped running, that test fails instead of silently passing.

**Known limitations, stated because they bound the claim:**

- **Colour contrast is not checked and cannot be under jsdom**, which has
  no layout or paint. The `color-contrast` rule is explicitly disabled
  rather than left to report a misleading pass, and the helper says so in
  its own docstring. Contrast still needs a real browser, and
  `docs/certification/` records it as an open gap — this helper must not be
  cited as evidence that contrast was verified.
- **Thirteen `jsx-a11y` findings remain as warnings.** They are real,
  pre-existing issues now visible rather than silently absent, and were
  deliberately left unfixed: they pre-date the pass that enabled the
  plugin, and absorbing them quietly would hide what enabling it found.
  `npm run lint` still exits 0 (they are warnings, not errors).
- Coverage is the shared primitives, a dialog and a chat message — not
  every page. Axe evaluates what a test renders, so a component no test
  renders is unchecked.
