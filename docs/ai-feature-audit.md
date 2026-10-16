# AI Feature Audit — the 100-prompt Conversational Intelligence backlog

**Audited:** 2026-10-16, against `feat/source-connectivity-audit` at `0398bf6`.
**Input:** `Improvements.zip` — 104 files (100 feature prompts, a master
prompt, an index, an execution strategy and a README).

This document assesses all 100 supplied feature prompts against the code
that already exists. It is an *assessment*, not a design: the work each gap
implies is in
[`ai-feature-implementation-plan.md`](ai-feature-implementation-plan.md),
and what was actually built is tracked in
[`ai-feature-implementation-status.md`](ai-feature-implementation-status.md).

Per [`CLAUDE.md`](../CLAUDE.md) §4.8 this file does not restate facts that
already have an owner. Where a capability is documented in depth elsewhere,
the row cites that document rather than summarising it.

---

## 1. Executive summary

**The repository already implements the large majority of this backlog.**
Of 100 prompts, 72 are already complete, 15 are partially complete, and 13
are genuinely missing. No prompt was found to describe a security control
that is absent or weakened, and none is blocked by an unavailable
dependency.

That ratio is not a surprise once the repository is inspected: this is a
platform built over ~55 numbered prompts, carrying 52 feature contracts at
the repository root, ~70 documents under `docs/`, and a backend suite of
**7,134 passing tests**. The supplied backlog describes the feature set of
a mature conversational-analytics product, and most of it is here.

### The finding that shapes everything below

**The 100 feature prompts are a single template repeated 100 times.** After
substituting the feature name, all 100 files reduce to **one** distinct
3,436-character skeleton, with the name appearing exactly 4 times
(heading, Objective, Step 1 bullet, Step 3 bullet). Verified
mechanically rather than by reading a sample.

The consequence is important and is stated here rather than worked around:

- **No prompt carries any feature-specific requirement, acceptance
  criterion, data model, UI description or success measure.** The master
  prompt's instruction to "confirm its acceptance criteria" cannot be
  satisfied from the prompts, because there are none to confirm.
- `01_FEATURE_PROMPT_INDEX.md` adds only a name and a path.
  `02_EXECUTION_STRATEGY.md` adds only an ordering.
- Every requirement in this audit is therefore **derived** from (a) the
  feature's name, and (b) the conventions the existing codebase already
  follows for comparable features. Where that derivation is ambiguous, the
  row says so instead of inventing a criterion and then marking it met.

A second, smaller discrepancy: the README and the index describe eight
category folders (`01_UI_UX` … `08_Enterprise_Developer`), but the ZIP
delivers all 100 prompts **flat** in a single `Improvements/` directory.
The category mapping used below is recovered from the index's paths.

### What this audit did not do

- It did not re-run the security benchmark, the execution-accuracy
  benchmark, or any live-infrastructure test. Security rows cite existing
  controls and their existing adversarial tests; they are **not** a fresh
  penetration test, and no row should be read as one.
- It did not assess UI behaviour in a browser. Frontend rows are based on
  component source, its tests, and the i18n keys that back it.

---

## 2. Repository architecture

Owned by [`docs/architecture/context-map.md`](architecture/context-map.md)
and [`CLAUDE.md`](../CLAUDE.md) §2–§3; summarised here only as far as the
audit depends on it.

| Layer | Technology | Entry points |
|---|---|---|
| UI | React 19 + Vite + TypeScript + Tailwind + Zustand + TanStack Query + i18next (en/es/fr/hi/mr) | `frontend/src/pages/`, `frontend/src/components/` (10 component families) |
| Chat state | Zustand, one store | `frontend/src/store/chatStore.ts` (863 lines) |
| API | FastAPI, ~70 route modules | `api/main.py` (`/ask`, `/execute`, `/models`, `/schema/*`, `/metrics/*`), plus `api/*.py` routers |
| Orchestration | LangGraph — an 18-node SQL graph, and a multi-source graph above it | `agent/graph.py`, `agent/orchestrator/graph.py` |
| SQL safety | sqlglot AST allowlist | `agent/sql_validator.py` |
| Retrieval | ChromaDB (schema) + a second collection (business context) + SQL Server `VECTOR` (documents) | `embeddings/`, `retrieval/`, `rag/store.py` |
| Identity | PostgreSQL + SQLAlchemy 2.0 + Alembic — the only real migrations | `identity/` |
| Governance | Request / result / recommendation policy engine, 19 domains | `governance/` |
| Streaming | SSE | `api/streaming.py`, `agent/progress.py` |

Implementation status is **not uniform**, and the audit respects the four
states [`CLAUDE.md`](../CLAUDE.md) §1 defines: *live*, *built but not
wired*, *off by default*, and *disclosed gap*. Several prompts below map to
capabilities that are built and tested but have no live caller; those are
recorded as partially complete with the reason, never as complete.

---

## 3. Existing core capabilities that must be preserved

These carry the platform's value and its safety. Nothing in the
implementation plan modifies any of them.

1. **The 18-node SQL graph** and its single bounded retry edge
   (`agent/graph.py`). Adding or renaming a node requires updating an
   exhaustive equality test and the frontend stage map.
2. **`agent/sql_validator.py`** — the security boundary. An AST allowlist,
   re-run on every display and every execution including after a hand-edit.
3. **The governed execution sequence** — `validate_sql` →
   `enforce_row_limit` → `qualify_table_schema` → `execute_readonly_sql` →
   `govern_rows_for_caller`, shared by every caller.
4. **Result governance before the model sees values** — the restricted-column
   gate runs inside `execute_sql_node`, so insight, charting and
   recommendations all receive protected rows.
5. **Tenant isolation** — never read from anything a client sends;
   `X-Tenant-Id` and friends are refused before authentication.
6. **Provenance** — `DataTruthLevel` travels with every claim; nothing
   inferred is presented as observed.
7. **The benchmark-pinned prompts** — `agent.llm_client._INSIGHT_SYSTEM_PROMPT`
   and the generation prompt, whose exact wording the only real accuracy
   figures were measured against ([`CLAUDE.md`](../CLAUDE.md) §12).
8. **Chat history and conversation state** — `chatStore.ts` plus
   `identity/repositories/history.py`, documented in
   [`chat-history-architecture.md`](chat-history-architecture.md).

---

## 4. Shared components and services available for reuse

Recorded because the single largest risk in this backlog is building a
second implementation of something already present under a different name.

| Need | Already exists |
|---|---|
| Copy to clipboard | `components/ui/copy-button.tsx`, `lib/clipboard.tsx` |
| Markdown rendering | `components/ui/markdown.tsx` (react-markdown + remark-gfm; `rehype-raw` deliberately absent) |
| PDF of an answer | `lib/pdf.tsx` → `downloadMarkdownAsPdf` |
| CSV of a result | `lib/csv.ts` |
| Expand/collapse long text | `components/ui/expandable-text.tsx` |
| Dialog / drawer / popover / tabs / toast | `components/ui/` |
| Result table (sort, filter, paginate) | `components/sql/ResultsTable.tsx` |
| Charting | `lib/chartEngine.ts` + `components/sql/ChartSection.tsx` |
| Conversation CRUD | `chatStore.ts` + `lib/identityApi.ts` + `api/chat_history.py` |
| Turn-level actions | `components/chat/TurnCard.tsx` |
| Per-request progress | `lib/askStream.ts` + `agent/progress.py` |
| Permission checks | `agent/authz.py`, `identity/rbac.py` |
| Safe error surfaces | `agent.exceptions.AgentError.safe_message` |

---

## 5. Assessment of all 100 prompts

Status values are the five the master prompt defines. **Evidence** cites
files that exist in this repository at the audited commit.

### 5.1 Category 01 — UI/UX (001–015)

| ID | Feature | Status | Evidence | Required change |
|---|---|---|---|---|
| 001 | Question Navigator | **ALREADY COMPLETE** | `components/chat/QuestionNavigator.tsx` + `.test.tsx`; `questionNavigator.*` i18n (5 locales); mounted in `pages/Chat.tsx` | None |
| 002 | Chat Search | **ALREADY COMPLETE** | `ConversationSearch.tsx`, `ConversationSearchResults.tsx`, `hooks/useChatSearch.ts`, `GET /chat/search`, `identity/repositories/history.py`; [`chat-history-search.md`](chat-history-search.md) | None |
| 003 | Collapsible Sidebar | **ALREADY COMPLETE** | `layout/SidebarToggle.tsx` + `.test.tsx`, `layout/AppShell.tsx`, `sidebar.collapseSidebar`/`expandSidebar` | None |
| 004 | Conversation Pinning | **MISSING** | No match for pinning a *conversation* anywhere. `chatStore.pinnedEntryIds` pins a **turn** into the artifact workspace — a different feature (027) | Persistence column + API + sidebar UI |
| 005 | Conversation Folders | **MISSING** | No model, route, column or component | Schema + API + UI |
| 006 | Rename Conversations | **ALREADY COMPLETE** | `chatStore.renameConversation`, `PATCH /conversations/{id}`, `ConversationListItem.tsx`, `history.rename` | None |
| 007 | Recent Conversations | **ALREADY COMPLETE** | `ConversationList.tsx` groups by `today`/`yesterday`/`previous7Days`/`older`; `hydrateHistoryFromServer` | None |
| 008 | Chat Branching | **MISSING** | No `parent_message_id`, no fork action, no branch UI | Schema + state + UI; large |
| 009 | Regenerate Answer | **PARTIALLY COMPLETE** | `chatStore.rerunEntry` **exists and works**, and `chat.rerun` is translated in **all five locales** — but **no component calls either**, and no test covers it. Dead code plus an orphan key | Render the control |
| 010 | Edit and Resubmit Question | **MISSING** | `setEditableSql` edits the **SQL**, not the question; `rerunEntry` re-asks **verbatim**. No question-edit affordance in `TurnCard.tsx` or `ChatMessage.tsx` | Edit affordance + resubmit |
| 011 | Stop Generation | **ALREADY COMPLETE** | `chatStore.cancelPendingQuestion` → real `AbortController.abort()`; button in `ChatInput.tsx:382` with `common.stop` | None |
| 012 | Copy Individual Sections | **ALREADY COMPLETE** | `ui/copy-button.tsx` reused by `TurnCard`, `SqlEditor`, `SqlIntelligencePanel`, `OcrResultDialog`, `ShareModal`; `CopyAnswerButton` copies markdown | None |
| 013 | Answer Feedback | **ALREADY COMPLETE** | `ResponseFeedbackWidget.tsx`, `chatStore.giveMessageFeedback`, `feedback/store.py`, 13 `feedback.*` keys | None |
| 014 | Response Display Controls | **ALREADY COMPLETE** | `ui/expandable-text.tsx`, `ui/scrolling-text.tsx`, `results.expand`/`showTechnical`, `ResultsTable` density controls | None |
| 015 | Keyboard Shortcuts | **PARTIALLY COMPLETE** | Component-local handlers only (`ChatInput`, `QuestionNavigator`, `ConversationSearch`, `ConversationListItem`, `MediaSearch`). No global registry, no discoverable shortcut help | Global layer + help surface |

### 5.2 Category 02 — Conversational Intelligence (016–030)

| ID | Feature | Status | Evidence | Required change |
|---|---|---|---|---|
| 016 | Context-Aware Follow-Up | **ALREADY COMPLETE** | `agent/followup.py::classify_followup_node`, `agent/nodes.py` (40 refs), `MAX_FOLLOWUP_EXCHANGES` | None |
| 017 | Suggested Follow-Up Questions | **ALREADY COMPLETE** | `components/chat/FollowUpSuggestions.tsx` + test, `recommendation/followups.py` (classification-checked before suggesting) | None |
| 018 | One-Click Clarification | **PARTIALLY COMPLETE** | `status="needs_clarification"` + `clarification_message` flow end to end (`agent/nodes.py:673`, `api/schemas.py`, `AgentExecutionTimeline.tsx:138`), but the message is **rendered as text** — no clickable disambiguation options | Clickable options |
| 019 | Conversation Context Indicator | **ALREADY COMPLETE** | `ConversationContextPanel.tsx` + test, `excludedContextEntryIds`, 18 `context.*` keys | None |
| 020 | Conversation Summarization | **MISSING** | No summarisation of a conversation anywhere | New capability |
| 021 | Persistent User Preferences | **ALREADY COMPLETE** | `store/settingsStore.ts`, `PATCH /auth/me`, `components/settings/` (theme, accent, font, language, model) | None |
| 022 | Custom AI Instructions | **MISSING** | No user-authored instruction reaches any prompt | New capability — **constrained**, see §9 |
| 023 | Domain-Specific Assistants | **PARTIALLY COMPLETE** | `domain_skills/` is fully built and tested — **95 skills across 18 governance domains**, `composer.guidance_for`, 662 tests, [`domain-skills.md`](architecture/domain-skills.md). But it is *built, not wired*: nothing calls it in a request path, by deliberate decision | Wiring, gated on review |
| 024 | Workspace-Specific Instructions | **MISSING** | No workspace-scoped instruction model | New capability — same constraint as 022 |
| 025 | Conversation Templates | **MISSING** | No template model, route or UI | New capability |
| 026 | Saved Questions | **PARTIALLY COMPLETE** | Golden questions exist at the **tenant/onboarding** level (`onboarding/golden_questions.py`, `GET /golden-questions`, `TenantAdmin.tsx`) and golden *examples* feed retrieval (`embeddings/golden_examples.py`). No per-user "save this question" | User-level save |
| 027 | Favorite Results | **ALREADY COMPLETE** | `chatStore.pinnedEntryIds` + `togglePinned` + `ArtifactWorkspace.tsx`; pins reference a turn rather than copying rows | None |
| 028 | Continue Previous Analysis | **ALREADY COMPLETE** | `chatStore.loadConversation` with lazy `messagesLoaded` hydration; `ConversationListItem` | None |
| 029 | Question Suggestions from Schema | **MISSING** | `recommendation/followups.py` suggests **after** an answer (017). Nothing suggests opening questions from the indexed schema; the empty chat shows only `chat.placeholderEmpty` | New capability |
| 030 | New-User Guided Tour | **MISSING** | Only prose in `USER_GUIDE.md`; no in-app tour component | New capability |

### 5.3 Category 03 — SQL and Data Analytics (031–045)

| ID | Feature | Status | Evidence | Required change |
|---|---|---|---|---|
| 031 | SQL Preview Panel | **ALREADY COMPLETE** | `sql/SqlEditor.tsx`, `sql/SqlIntelligencePanel.tsx` + test, gated by `showSqlEditor` in `TurnCard.tsx:108` | None |
| 032 | Show Generated SQL | **ALREADY COMPLETE** | `AskResponse.sql`, `TurnCard`, re-validated before display | None |
| 033 | SQL Explanation | **MISSING** | No natural-language explanation of the generated SQL | New capability — cost/latency implications, see plan |
| 034 | Query Execution Status | **ALREADY COMPLETE** | `sql/ExecutionStatusBar.tsx`, `sql/QueryLifecycle.tsx`, `chat/AgentExecutionTimeline.tsx`, all tested; 15 `status.*` + 7 `lifecycle.*` keys | None |
| 035 | Interactive Result Tables | **ALREADY COMPLETE** | `sql/ResultsTable.tsx` — sortable columns, global filter, per-column filters, pagination | None |
| 036 | Download Results | **ALREADY COMPLETE** | `lib/csv.ts` + `csv.test.ts`, `common.exportCsv` in `ResultsTable` | None |
| 037 | Chart Recommendations | **ALREADY COMPLETE** | `agent/result_charting.py`, `lib/chartEngine.ts` (+28-case test), [`15_VISUALIZATION_ENGINE_CONTRACT.md`](../15_VISUALIZATION_ENGINE_CONTRACT.md) | None |
| 038 | Switch Chart Type | **ALREADY COMPLETE** | `sql/ChartPicker.tsx`, `setChartOptions`, plus NL chart-type follow-up (`tryApplyChartTypeFollowup`) with no re-query | None |
| 039 | Table-to-Chart | **ALREADY COMPLETE** | `sql/ChartSection.tsx` + test, `results.showChart` | None |
| 040 | Chart-to-Table | **ALREADY COMPLETE** | `sql/ChartDataTable.tsx` + test | None |
| 041 | Business Metric Definitions | **ALREADY COMPLETE** | `semantic/metrics.py`, `semantic/catalog.py`, `analytics/GoverningMetricsPanel.tsx`, [`10_GOVERNED_METRICS_CONTRACT.md`](../10_GOVERNED_METRICS_CONTRACT.md) | None |
| 042 | Date-Range Controls | **MISSING** | No date-range picker or period selector | New capability |
| 043 | Dynamic Filters | **ALREADY COMPLETE** | `ResultsTable.filterableColumns` — computed from the unfiltered rows so controls stay stable, plus a global filter | None |
| 044 | Drill-Down Analytics | **PARTIALLY COMPLETE** | `analytics/RankingPanel.tsx`, `lib/analyticsCharts.ts`, root-cause engine. Panels are read-only; no click-through to a narrower query | Interactive drill-through |
| 045 | Compare Results | **PARTIALLY COMPLETE** | `analytics/ComparisonPanel.tsx` and prior-period comparison inside the analytics engine. No side-by-side comparison of two *chosen turns* | Turn-vs-turn comparison |

### 5.4 Category 04 — AI Quality and Research (046–060)

| ID | Feature | Status | Evidence | Required change |
|---|---|---|---|---|
| 046 | Evidence-Linked Answers | **ALREADY COMPLETE** | `analytics/ClaimEvidence.tsx`, `EvidenceBadge.tsx`, `lib/evidence.ts`, `briefing/claims.py`, 81 test files touch it | None |
| 047 | Source Provenance Panel | **ALREADY COMPLETE** | `chat/SourceLineagePanel.tsx`, `chat/SourcesUsedPanel.tsx`, `agent/provenance.py`, `decision_center/sources.py` | None |
| 048 | Answer Confidence Indicators | **ALREADY COMPLETE** | `analytics/TruthLevelBadge.tsx`, `DataTruthLevel`, confidence throughout `recommendation/engine.py` and `analytics/models.py` | None |
| 049 | SQL Validation Summary | **ALREADY COMPLETE** | `sql/SqlIntelligencePanel.tsx` (12 refs), `agent/sql_validator.py`, `status.validated*` | None |
| 050 | Automatic SQL Repair | **ALREADY COMPLETE** | The bounded retry edge back to `generate_sql` on a validation or execution failure (`agent/graph.py`), plus `review_sql_node` and `review_metric_conformance_node` (`agent/nodes.py:1427`, `:1531`). Retries are surfaced by `AgentExecutionTimeline` | None |
| 051 | Query Plan Preview | **ALREADY COMPLETE** | `chat/QueryPlanPanel.tsx`, the planning node, [`12_ANALYTICAL_PLANNING_CONTRACT.md`](../12_ANALYTICAL_PLANNING_CONTRACT.md) | None |
| 052 | Alternative SQL Suggestions | **MISSING** | Only eval-harness references. One statement is generated per turn | New capability |
| 053 | Result Interpretation | **ALREADY COMPLETE** | The insight node + `agent/insight.py`, [`13_ANALYTICAL_RESULT_ENGINE_CONTRACT.md`](../13_ANALYTICAL_RESULT_ENGINE_CONTRACT.md) | None |
| 054 | Anomaly Detection | **ALREADY COMPLETE** | `analytics/anomaly.py` (54 refs) + `AnomalyPanel.tsx`, [`14_ANOMALY_ROOT_CAUSE_CONTRACT.md`](../14_ANOMALY_ROOT_CAUSE_CONTRACT.md) | None |
| 055 | Trend Detection | **ALREADY COMPLETE** | `agent/insight.py`, `analytics/provider.py`, `TrendPanel.tsx` | None |
| 056 | Automated Insight Cards | **ALREADY COMPLETE** | `analytics/KpiStrip.tsx`, `AnalyticsSummary.tsx` + test, `sql/KpiCard.tsx` | None |
| 057 | Research Mode | **ALREADY COMPLETE** | `agent/orchestrator/` — router + 7 source subgraphs + synthesis; off by default; [`MULTI_SOURCE_GUIDE.md`](MULTI_SOURCE_GUIDE.md) | None |
| 058 | Research Plan Preview | **PARTIALLY COMPLETE** | The **SQL** analytical plan is previewed (`QueryPlanPanel`). The **multi-source** routing decision is not previewed before the subgraphs run | Expose the route decision |
| 059 | Source Selection | **ALREADY COMPLETE** | `chat/SourcePicker.tsx` + test, `tests/test_source_selection.py` (20) and `test_api_source_selection.py` (13); `router_node` filters the model's own selection through RBAC | None |
| 060 | Answer Verification Mode | **PARTIALLY COMPLETE** | Internal review exists (`review_sql_node`, `review_metric_conformance_node`, governance gates). No user-invokable "verify this answer" mode | New user-facing mode |

### 5.5 Category 05 — Files and Workspaces (061–070)

| ID | Feature | Status | Evidence | Required change |
|---|---|---|---|---|
| 061 | Drag-and-Drop Upload | **ALREADY COMPLETE** | `chat/ChatInput.tsx` (13 drag refs), `image/ImageUploader.tsx` | None |
| 062 | CSV and Excel Analysis | **ALREADY COMPLETE** | `attachments/processors/csv_processor.py` and `xlsx_processor.py` in the registry; `tests/test_attachments_processors.py` | None |
| 063 | PDF Question Answering | **ALREADY COMPLETE** | `rag/ingestion.py`, `attachments/processors/pdf_processor.py`, `tests/test_attachments_pdf_safety.py`, [`FILE_UPLOAD_SECURITY.md`](FILE_UPLOAD_SECURITY.md) | None |
| 064 | File Preview | **PARTIALLY COMPLETE** | `chat/SentAttachmentsPreview.tsx` (sent-attachment chips) and `image/ImageViewer.tsx`. No preview of a PDF/CSV/XLSX before or after upload | Non-image preview |
| 065 | Attachment Management | **ALREADY COMPLETE** | `hooks/useChatAttachments.ts`, `chat/ChatAttachmentChip.tsx`, `api/attachments.py`, `tests/test_attachment_api.py` (100 refs) | None |
| 066 | Reusable Analysis Workspaces | **ALREADY COMPLETE** | `chat/ArtifactWorkspace.tsx` + test, `agent/multiagent/supervisor.py` | None |
| 067 | Saved Dashboards | **PARTIALLY COMPLETE** | Artifacts persist and refresh (`api/artifacts.py`, 201 refs); admin dashboards exist. No user-composed multi-result dashboard | Dashboard composition |
| 068 | Shareable Reports | **ALREADY COMPLETE** | `api/shares.py`, `chat/ShareModal.tsx`, a dedicated migration, and `tests/security/test_shared_results_are_regoverned.py` — a share re-governs on every read | None |
| 069 | Markdown Export | **PARTIALLY COMPLETE** | `CopyAnswerButton` copies **markdown** to the clipboard; `DownloadAnswerButton` downloads a **PDF** via `downloadMarkdownAsPdf`. There is no markdown **file** download | Add `.md` download |
| 070 | Reusable Analysis Artifacts | **ALREADY COMPLETE** | `api/artifacts.py`, `identity/repositories/artifacts.py`, `ArtifactDetail.tsx`, `tests/test_artifact_staleness.py` | None |

### 5.6 Category 06 — Security, Privacy and Governance (071–082)

The repository's strongest area. Every row here is already implemented and
carries its own adversarial tests. **No weakness was found, so no row is
marked NEEDS HARDENING** — see §10 on how far that statement reaches.

| ID | Feature | Status | Evidence |
|---|---|---|---|
| 071 | Read-Only Query Enforcement | **ALREADY COMPLETE** | Layered, not code-only: the AST allowlist, a read-only `DB_USER`, and `db.connection.check_write_privileges` enforced at startup (`tests/test_startup_write_privilege_enforcement.py`) |
| 072 | SQL Allowlist and AST Validation | **ALREADY COMPLETE** | `agent/sql_validator.py` (48 refs) — allowlist of the parsed statement type, never a regex blocklist; rejects system catalogs, nested aggregates, unsafe hints; `tests/test_sql_validator.py` (31) + `_hardening.py` (18) |
| 073 | Database Least-Privilege Access | **ALREADY COMPLETE** | `db/connection.py`, `tests/test_write_privilege_check.py` |
| 074 | Role-Based Access Control | **ALREADY COMPLETE** | `agent/authz.py` (base roles) + `identity/rbac.py` (88 refs, granular); an unrecognised role grants zero; `router_node` filters source selection through it |
| 075 | Row-Level Security | **ALREADY COMPLETE** | Tenant scoping folded into the query's own `WHERE`; the row cap enforced **twice** (SQL text and `fetchmany`); small-group suppression in `governance/suppression.py` |
| 076 | Column-Level Security | **ALREADY COMPLETE** | `governance/result_policy.py` classifies **returned column names**, so an alias or `SELECT *` reaches the same gate; `tests/test_restricted_column_bypass.py` |
| 077 | Sensitive Data Masking | **ALREADY COMPLETE** | `governance/classification.py` + `MaskingStrategy`; `CREDENTIAL`/`PAYMENT` withheld from every caller including admin |
| 078 | Audit Trail | **ALREADY COMPLETE** | `identity/repositories/audit.py`, `security/audit_log.py`, `GET /audit-logs`, `GET /security-events` |
| 079 | Prompt-Injection Defenses | **ALREADY COMPLETE** | `security/injection_patterns.py` as a detection signal; retrieved content framed as data in every consuming prompt; `agent.input_guard.sanitize_conversation_history`; `eval/benchmark/adversarial.yaml` |
| 080 | Secure File Uploads | **ALREADY COMPLETE** | `security/malware_scanner.py` (58 refs) scanning raw bytes before any parser, decompression-bomb guard, PDF preflight; `tests/security/test_malware_scanner_gate.py` (63) |
| 081 | Secret Management | **ALREADY COMPLETE** | `pydantic.SecretStr` throughout, `security/redaction.py`, `detect-secrets` as a blocking CI gate; `tests/test_secrets.py`, `tests/test_redaction.py` |
| 082 | Data Retention Controls | **PARTIALLY COMPLETE** | `Settings.attachment_retention_hours` (default 24) with opportunistic purge in `attachments/storage.py`. **No retention policy for conversations, artifacts or audit rows** — and [`chat-history-architecture.md`](chat-history-architecture.md) already discloses this |

### 5.7 Category 07 — Backend Performance and Reliability (083–092)

| ID | Feature | Status | Evidence |
|---|---|---|---|
| 083 | Streaming Status Updates | **ALREADY COMPLETE** | `POST /ask/stream` (`api/streaming.py`) emits per-node progress then the same `AskResponse`; it *calls* `api.main.ask` rather than reimplementing it. Token-level streaming remains a disclosed gap |
| 084 | Response Caching | **ALREADY COMPLETE** | `db/result_cache.py`, `tests/test_result_cache.py` (25), `chatStore.nlQuestionCache`, `status.cacheHit`/`cacheMiss` |
| 085 | Schema Retrieval Optimization | **ALREADY COMPLETE** | `embeddings/schema_indexer.py` (42), `tests/test_schema_retriever.py` (43), [`vector-retrieval-design.md`](vector-retrieval-design.md) |
| 086 | Hybrid Schema Search | **ALREADY COMPLETE** | `retrieval/reranker.py` + `retrieval/retriever.py`; [`retrieval-evaluation.md`](retrieval-evaluation.md) |
| 087 | Parallel Independent Tasks | **ALREADY COMPLETE** | `route_after_router` returns a **list** of destinations, so LangGraph runs each selected source as a parallel branch in one superstep before `synthesis` (`agent/orchestrator/nodes.py:732`) |
| 088 | Request Cancellation | **ALREADY COMPLETE** | Frontend `AbortController`; `agent/harness/lifecycle.py` + `envelope.py`; `tests/test_harness_runtime.py` (39) |
| 089 | Query Timeouts and Row Limits | **ALREADY COMPLETE** | `db/execution.py`, driver-level timeout or forced connection abort, row cap enforced twice |
| 090 | Retry and Backoff Policies | **ALREADY COMPLETE** | `agent/harness/retry.py`, the single bounded graph retry budget, `tests/security/test_harness_resilience.py` |
| 091 | Observability Dashboard | **ALREADY COMPLETE** | `GET /metrics/performance`, `GET /metrics/harness`, `pages/PlatformAdmin.tsx` + `usePlatformMetrics`; [`OBSERVABILITY.md`](OBSERVABILITY.md). Per-worker and reset-on-restart, already disclosed |
| 092 | Graceful Error Recovery | **ALREADY COMPLETE** | `agent/exceptions.py` with `safe_message`, two global handlers in `api/main.py`, per-subsystem exception modules |

### 5.8 Category 08 — Enterprise and Developer (093–100)

| ID | Feature | Status | Evidence | Required change |
|---|---|---|---|---|
| 093 | Model Selection | **ALREADY COMPLETE** | `settings/ModelSelector.tsx` + test, `GET /models`, `AskRequest.model` validated against `Settings.ollama_allowed_models`, `agent/model_registry.py` | None |
| 094 | Automatic Model Routing | **ALREADY COMPLETE** | `model_routing/policy.py` + `router.py`, `api/model_routing.py`, and `tests/security/test_model_routing_never_weakens_deterministic_controls.py`; off by default | None |
| 095 | Custom Agent Instructions | **PARTIALLY COMPLETE** | `governance_center/` is a versioned prompt/agent registry with approve → deploy → rollback. It **deliberately does not hot-swap a live prompt** — making a database row the system prompt would turn every write into prompt injection ([`CLAUDE.md`](../CLAUDE.md) §5.5), pinned by `tests/security/test_prompt_registry_cannot_weaken_security.py` | None recommended — see §9 |
| 096 | Tool Permission Controls | **ALREADY COMPLETE** | `agent/tools/registry.py` + `definitions.py` + `types.py`, `mcp_client/trust.py`, [`TOOLS.md`](TOOLS.md) | None |
| 097 | MCP Integration Management | **ALREADY COMPLETE** | `mcp_client/` (client, transport, protocol, trust, types), `tests/test_mcp_client.py` (52); off by default, administrator-controlled trust only | None |
| 098 | Golden-Question Evaluation Suite | **ALREADY COMPLETE** | `api/eval_platform.py` (49), `eval/`, `embeddings/golden_examples.py`, `tests/test_golden_examples.py` (48), [`46_AI_EVALUATION_BENCHMARKING_CONTRACT.md`](../46_AI_EVALUATION_BENCHMARKING_CONTRACT.md) | None |
| 099 | Prompt and Configuration Versioning | **ALREADY COMPLETE** | `governance_center/artifacts.py` + `inventory.py` (versioned assets with lifecycle routes), `GOVERNANCE_POLICY_VERSION` recorded on every decision | None |
| 100 | AI Usage and Cost Dashboard | **PARTIALLY COMPLETE** | Token counts **are** captured (`agent/llm_client.py:1039–1040` reads `prompt_eval_count`/`eval_count`); `GET /metrics` exposes a routing rollup; `db/query_cost.py` estimates **database** query cost. There is no AI-usage rollup surfaced in the UI. **Monetary cost is not applicable** to a local Ollama deployment and must not be fabricated | Usage rollup only |

---

## 6. Summary counts

| Status | Count | IDs |
|---|---|---|
| **ALREADY COMPLETE** | **72** | 001–003, 006, 007, 011–014, 016, 017, 019, 021, 027, 028, 031, 032, 034–041, 043, 046–051, 053–057, 059, 061–063, 065, 066, 068, 070–081, 083–094, 096–099 |
| **PARTIALLY COMPLETE** | **15** | 009, 015, 018, 023, 026, 044, 045, 058, 060, 064, 067, 069, 082, 095, 100 |
| **MISSING** | **13** | 004, 005, 008, 010, 020, 022, 024, 025, 029, 030, 033, 042, 052 |
| **NEEDS HARDENING** | **0** | — |
| **BLOCKED** | **0** | — |
| **Total** | **100** | |

---

## 7. Duplicate and overlapping capabilities

Several prompts name one capability that the repository already provides
under another name, or that another prompt also covers. Building each
separately would be the main way this backlog could damage the codebase.

| Prompts | Shared underlying capability |
|---|---|
| 004 ↔ 027 | "Pinning" means two different things. 027 (pin a **turn** into the artifact workspace) is built; 004 (pin a **conversation** in the sidebar) is not. They must not share an implementation |
| 009 ↔ 010 | Both are "ask this again". 009 re-asks verbatim; 010 re-asks an edited question. One store path (`askQuestion`) serves both |
| 012 ↔ 069 | Both are "get this answer out of the app". `CopyAnswerButton` and `DownloadAnswerButton` already share the same `answerMarkdown` string |
| 017 ↔ 018 ↔ 029 | Three kinds of suggestion: after an answer (built), on ambiguity (partly built), and from the schema with no answer yet (missing). One presentation component could serve all three |
| 022 ↔ 024 ↔ 095 | Three framings of "user-authored instructions reaching a prompt". All three hit the same constraint in §9 |
| 035 ↔ 043 | Interactive tables and dynamic filters are the same component, `ResultsTable.tsx` |
| 037 ↔ 038 ↔ 039 ↔ 040 | One charting stack: `chartEngine.ts` + `ChartSection` + `ResultChart` + `ChartDataTable` |
| 044 ↔ 045 | Both want a *second* query derived from the first; a shared "derive a narrower question" path would serve both |
| 066 ↔ 067 ↔ 070 | Artifacts already provide persistence and refresh; a "saved dashboard" is most cheaply a composition over artifacts, not a new store |
| 071 ↔ 072 ↔ 073 ↔ 089 | One layered SQL safety story, already implemented end to end |
| 075 ↔ 076 ↔ 077 | One governance engine (`governance/`), three views of it |
| 093 ↔ 094 | `model_registry` and `model_routing` already compose |

---

## 8. Security and reliability observations

- **No prompt in 071–082 describes a control that is missing.** Each maps
  to an implemented, tested control, several with dedicated adversarial
  suites under `tests/security/`.
- **The one genuine gap in that category is 082**, and it is already a
  disclosed limitation rather than a new discovery: retention exists for
  attachment bytes only.
- **Two implementation areas in this backlog carry real risk** and are
  treated accordingly in the plan:
  - Anything that lets user text reach a system prompt (022, 024, 095) —
    see §9.
  - Anything that adds an LLM call per turn (033, 052, 060) — latency and
    load, on a local model that is already the slowest step.
- **Two pre-existing findings are unrelated to this backlog** and were not
  introduced here: one `ruff` import-order finding in
  `agent/multiagent/agents.py`, and one `mypy` arg-type error in
  `db/schema_introspection.py`. Both are named in
  [`CLAUDE.md`](../CLAUDE.md) §13.

---

## 9. Architectural constraints that bound this backlog

These are not preferences; each is an invariant with a test behind it.

1. **A database row must never become a system prompt.**
   [`CLAUDE.md`](../CLAUDE.md) §5.5, pinned by
   `tests/security/test_prompt_registry_cannot_weaken_security.py`. This
   directly bounds 022, 024 and 095: user- or admin-authored instruction
   text cannot be injected into the generation or insight prompts. A safe
   version of those features must either (a) select among in-code
   constants, as `domain_skills/` does, or (b) place user text in a clearly
   framed *data* position, never an instruction position.
2. **The generation and insight prompts are benchmark-pinned.**
   [`CLAUDE.md`](../CLAUDE.md) §12 — the only real accuracy figures this
   project has were measured against their exact wording. 033, 052 and 060
   must add a *separate* call rather than edit either prompt.
3. **The graph is a bounded state machine.** Adding a node means updating
   an exhaustive equality test and `QueryLifecycle.tsx`'s stage map
   ([`CLAUDE.md`](../CLAUDE.md) §4.1).
4. **An accuracy aid fails open; a safety control fails closed**
   ([`CLAUDE.md`](../CLAUDE.md) §4.2). Every feature in this backlog except
   those in category 06 is an accuracy aid.
5. **One owner per fact** ([`CLAUDE.md`](../CLAUDE.md) §4.8). Before adding
   a badge or panel, check which component already owns that fact — §7
   above lists the collisions this backlog invites.
6. **Identity migrations are the only real migrations.** 004, 005, 008,
   020, 025 and 026 all imply new persistence, hence a new Alembic revision
   carrying `server_default` for any ORM-filled timestamp
   ([`CLAUDE.md`](../CLAUDE.md) §6.3).
7. **Chat history covers locally-authenticated users only.** An OIDC or
   unauthenticated caller is answered but nothing is persisted. Every
   persistence-backed feature above inherits that limit.

---

## 10. How far these assessments reach

Stated explicitly, because "assessed" is not "verified end to end".

- **Code exists** — established for every row, by citing files.
- **Behaviour is tested** — established where a row cites a test file.
- **Verified end to end in a running system** — **not** established by this
  audit for any row. No browser session, no live database, no Ollama call
  was part of it.

A row marked ALREADY COMPLETE therefore means: *the capability is
implemented and, where cited, covered by tests* — not that it was
exercised against live infrastructure today.

---

## 11. Recommended priorities

Derived from gap size, risk and dependency rather than from prompt order.
The plan document expands each.

| Priority | Features | Rationale |
|---|---|---|
| **P1** | 009, 069 | Smallest possible change, immediate user value, no new persistence, no new API surface. 009 is finishing code that is already written |
| **P2** | 010, 015, 029, 030, 064 | Frontend-only, no schema change, no new model call |
| **P2** | 004, 026 | Small persistence additions, one migration each |
| **P3** | 018, 033, 042, 044, 045, 052, 058, 060, 067, 100 | Larger, or add a model call per turn, or need a design decision first |
| **P3** | 005, 008, 020, 025 | New persistence models and substantial UI; each is a feature in its own right |
| **Not recommended** | 022, 024, 095 | Blocked by §9.1 in the form the names imply. A safe variant is described in the plan |
| **Review-gated** | 023 | Built and tested; wiring is a deliberate, separate decision |

---

## 12. Features requiring no code change

The 72 rows marked ALREADY COMPLETE. They were inspected, not modified. Per
the master prompt's instruction, none was rewritten, and no test was added
to them merely to raise a count.
