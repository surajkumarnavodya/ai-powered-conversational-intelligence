# AI Capability Implementation Plan

**Derived from:** [`ai-capability-audit.md`](ai-capability-audit.md), 2026-10-16, baseline `bea4a34`.
**Tracked in:** [`ai-capability-implementation-log.md`](ai-capability-implementation-log.md).
**Decisions:** [`ai-capability-decisions.md`](ai-capability-decisions.md).

Of 149 catalogue rows, **56 are Core and none is Missing**. This plan
therefore covers the **14 Core partials** from audit §4, grouped into
cohesive capabilities rather than treated row by row.

---

## 1. Priorities, and why nothing is P0

**P0 is empty.** P0 means a critical security issue, data-integrity
problem, or a major failure of existing core functionality. The audit
found none: 7,197 backend and 1,147 frontend tests pass, the SQL security
boundary is intact and covered by 49 validator tests plus 25 adversarial
files, and tenant isolation is enforced before authentication.

Declaring a P0 to look thorough would be exactly the kind of manufactured
finding the brief forbids.

| Priority | Meaning here | Items |
|---|---|---|
| P1 | High-impact gap in a Core capability | A (SQL explanation) |
| P2 | Important capability/assurance improvement | B, C, D, F |
| P3 | Optional or low analytics value | G, I, J, K |
| **No action** | Partial **by design** — closing it would weaken the product | H, L, M |

---

## 2. Batch 1 — P1: SQL explanation (row 27)

**Business value.** The platform shows *what* SQL it generated and whether
it validated, but never explains it in words. For the analyst who must
sign off on a number, "this joined orders to customers and excluded
cancelled rows" is the difference between trusting a result and
re-deriving it by hand. It is also the one Core partial that users meet on
every SQL turn.

### Design corrected after inspecting the integration point

My first draft proposed an on-demand **LLM call**. Reading
`agent/sql_validator.py::summarize_sql` changed that: the validator
**already parses the SQL into an AST** and already derives
`statement_type` and `referenced_tables` from it for
`SqlSummaryOut`. The catalogue's own wording for row 27 is *"Explain
joins, filters, aggregations, and query behavior"* — and every one of
those is a fact sitting in that AST.

**A deterministic explainer is strictly better here than a model call:**

| | Deterministic (chosen) | LLM call (rejected) |
|---|---|---|
| Latency / cost | Zero — reuses the existing parse | A second round trip per request |
| Correctness | Cannot be wrong about the SQL | Can hallucinate a join that isn't there |
| Injection surface | None — SQL never reaches a model | SQL is model output; feeding it back is a path |
| Benchmark-pinned prompts | Untouched | Needs a new prompt to avoid §12 |
| Failure mode | Returns `None`, panel shows nothing | Needs explicit fail-open handling |

An explanation of a query that *hallucinates a join* is worse than no
explanation, because it would be read as authoritative next to a real
validator verdict. That decides it.

| Field | Detail |
|---|---|
| **Existing** | `agent/sql_validator.py::summarize_sql` (AST already parsed, CTE-aware); `SqlSummaryOut` in `api/schemas.py`; `SqlIntelligencePanel.tsx` renders the metadata grid |
| **Required change** | A new `describe_sql()` beside `summarize_sql`, reading the **same** AST for joins, filters, aggregates, grouping, ordering, limit and distinct; surfaced as optional fields on `SqlSummaryOut`; rendered as prose in the existing panel |
| **Reuse** | The existing parse, the existing CTE-aware filtering, the existing metadata grid, `Expander` |
| **Dependencies** | **None** — sqlglot is already a core dependency |
| **Complexity** | M (was M; now no provider risk) |
| **Risks** | (1) `summarize_sql` returns a tuple and has callers — **add a new function, do not change its signature**. (2) `SqlSummaryOut` is frozen and widely consumed — new fields must be **optional with defaults** (`CLAUDE.md` §6.3). (3) It must stay purely descriptive: `validate_sql` remains the only thing that gates. (4) A parse failure must yield "no explanation", never a guess |
| **Files likely to change** | `agent/sql_validator.py` (new function only), `api/schemas.py` (additive optional fields), the one place `SqlSummaryOut` is built, `frontend/src/components/sql/SqlIntelligencePanel.tsx`, i18n ×5, backend + frontend tests |
| **Acceptance** | A SQL turn shows a prose description of its joins/filters/aggregations/grain; unparseable SQL shows nothing; no new network call; `summarize_sql`'s existing callers are untouched; the description never contradicts the validator |
| **Tests** | Backend: joins, filters, aggregates, group/order/limit, CTEs, `UNION`, unparseable input, and that `summarize_sql`'s own behaviour is unchanged. Frontend: renders, absent case. Regression: existing `SqlSummaryOut` consumers |

**Open question is now closed by the design**: with no model call, there
is no on-demand-versus-automatic trade-off — the description is free and
can simply always be present.

---

## 3. Batch 2 — P2: assurance gaps (C, D, E)

These three are **evidence** gaps, not code gaps, and they overlap the
parked certification work (prompts 53–60). I propose to run them **there**
rather than duplicate them here.

| Item | Rows | Plan | Blocker |
|---|---|---|---|
| **C** Accessibility | 60 | Add `axe-core` + `vitest-axe` to the existing vitest suite; assert no violations on the chat page, a dialog and the results table | None — can start now |
| **D** Load / capacity | 59 | Run the existing `eval/load/` k6 stack: baseline → load → stress → recovery | **Docker daemon not running** |
| **E** DAST | 49 | Needs a deployed target; `CLAUDE.md` §13 already discloses this | No environment |

**C is the only one actionable today**, and it is a genuine gap: the UI has
aria labels and focus management throughout but nothing asserts they stay.
Complexity S–M; one new dev dependency (`vitest-axe`), which I would flag
before adding.

---

## 4. Batch 3 — P2: AI usage rollup (F, row 45)

**DELIVERED 2026-10-17.** Logged as **Batch 4** in
[`ai-capability-implementation-log.md`](ai-capability-implementation-log.md),
because that log numbers batches by the order they were executed and
row 79's translation work (listed under §5 below) was built first and
took the "Batch 3" slot there. Same work, two numbering schemes — this
section's label is the plan's; the log's label is the execution order.

| Field | Detail |
|---|---|
| **Business value** | Operators can see routing decisions but not what the models actually consumed |
| **Existing** | `agent/llm_client.py:1039–1040` already reads `prompt_eval_count`/`eval_count`; `model_routing` exposes `GET /metrics`; `PlatformAdmin.tsx` renders metrics |
| **Required change** | Aggregate per-model token counts into `observability/metrics.py` and surface on the existing admin metrics panel |
| **Complexity** | M |
| **Risks** | **Must not present a monetary figure** — none exists for local Ollama, and inventing one is forbidden. Per-worker, resets on restart: must be labelled as such, like every other metric here |
| **Acceptance** | Token totals per model visible; no currency anywhere; the disclosure matches existing metrics' wording |

---

## 5. Batch 4 — P3: optional, only on request

| Item | Rows | Note |
|---|---|---|
| **G** Word/Excel export | 63 | Needs a new dependency (`docx`/`xlsx` writer). PDF + CSV + Markdown already cover the common cases |
| **I** Repair transparency | 28 | Surface what the retry loop changed. Small, nice, not load-bearing |
| **J** Screenshot troubleshooting | 145 | Vision + OCR already exist; this is a workflow wrapper |
| **K** Ad-hoc arithmetic | 88 | Low value — the SQL path already computes, with governance |

Also optional and currently **Missing** (all non-Core): 50 (OWASP ZAP), 64/65
(document formatting/conversion), 79 (content translation — *Needs
decision*), 85 (diagram generation), 146 (ZIP packaging).

**Row 79 (translation) is the one genuine product question.** The UI is
translated into five locales but answers come back in the model's
language. Translating governed output is not a formatting change: it must
not alter figures, units or citations, so it needs a design decision
before any code.

---

## 6. Explicitly no action

| Item | Rows | Why |
|---|---|---|
| **H** Prompt hot-swap | 41 | `CLAUDE.md` §5.5 forbids a DB row becoming a system prompt; pinned by `tests/security/test_prompt_registry_cannot_weaken_security.py`. The registry's read-only-to-the-runtime design **is** the correct implementation |
| **L** ETL / data transformation | 32, 33 | The app is a read-only client of the operator's database. Writing would break the layered read-only guarantee |
| **M** Repository inspection | 147 | A codebase-inspection feature inside a data platform is a confused-deputy risk |
| 78 out-of-scope rows | — | Audit §3 |

---

## 7. Sequencing and dependency rule

```
done       Batch 1 (A, P1)          frontend + one backend module, no schema change
done       Batch 2C (a11y, P2)      test-only; needs one dev dependency
done       row 79 (translation)     built, NOT wired — log's "Batch 3"
done       Batch 3 (F, P2)          backend metrics + existing admin panel
on Docker  Batch 2D (load)          run the existing stack — certification 53-60
deferred   Batch 4 (P3)             on request
never      H, L, M, 78 rows         see §6
```

Each batch ends with: run the relevant suites, review `git diff`, update
the coverage CSV and the implementation log, commit separately.

**Nothing in this plan changes an API contract, a database schema, or a
security control.**

---

## 8. Regression surface

| Component | Depended on by |
|---|---|
| `SqlIntelligencePanel.tsx` | `TurnCard`, artifact detail |
| `api/schemas.py` | Frozen response models — any new field must be optional with a default (`CLAUDE.md` §6.3) |
| `agent/llm_client.py` | Every LLM call; `decoding_options` has one owner |
| `observability/metrics.py` | `/metrics/performance`, PlatformAdmin |
| i18n | Five locales; a missing key renders the raw key |
