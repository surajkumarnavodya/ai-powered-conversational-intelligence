# AI Capability Audit — the 149-row activity catalogue

**Date:** 2026-10-16
**Baseline commit:** `bea4a34` on `feat/source-connectivity-audit` (working tree clean)
**Input:** `Claude_Prompts_AI_Conversational_Intelligence.zip` — master prompt,
149-row `ACTIVITY_CAPABILITY_CATALOGUE.md`, roadmap, execution guide, 19 workstream prompts.
**Phase 1 only. No application code was changed to produce this document.**

Companions: [`ai-capability-coverage.csv`](ai-capability-coverage.csv) (all 149
rows, machine-generated), [`ai-capability-implementation-plan.md`](ai-capability-implementation-plan.md),
[`ai-capability-decisions.md`](ai-capability-decisions.md).

---

## 1. Executive summary

**Of the 149 catalogue rows, 57 are Core capabilities for this product. Of
those 57: 44 are Complete, 13 are Partial, and none is Missing.**

No Core capability is absent. Every genuine gap is a partial — an existing
implementation that stops short — which makes this a refinement exercise,
not a build-out.

> **Recounted from the CSV on 2026-10-17**, after four batches landed and
> one row was reclassified. The figures below are what
> [`ai-capability-coverage.csv`](ai-capability-coverage.csv) actually
> contains now, not the original audit's snapshot. What moved: rows 27
> (Batch 1), 60 (Batch 2) and 45 (Batch 4) went Partial → Complete, and
> **row 79 (content translation) went from "Needs decision" to Core /
> Partial** when `agent/translation.py` was built — which is why Core is
> 57 rather than the 56 this summary first reported, and why the "Needs
> decision" row below is gone rather than zeroed. Batch 4's own log entry
> notes that row 45 is Optional, so closing it did not move the Core
> tallies. See
> [`ai-capability-implementation-log.md`](ai-capability-implementation-log.md).

| Product relevance | Rows | |
|---|---:|---|
| **Core** | **57** | Maps to a real capability of this platform |
| Optional | 14 | Could fit, low analytics value |
| **Out of scope** | **78** | Not features of this product — see §3 |

| Existing status (Core + Optional only) | Rows |
|---|---:|
| Complete | 45 |
| Partial | 21 |
| Missing | 5 |
| N/A (out of scope) | 78 |

### The finding that governs everything else

**This catalogue is an inventory of the user's own assistant usage, not a
product specification.** Fifty-two percent of its rows describe activities
performed *with* an AI assistant — many of them the activities that built
this very application.

Concretely:

- **Rows 10–25 (Software Engineering)** are code generation, debugging,
  refactoring, git/PR planning and "coding-agent prompt engineering". These
  describe how this repository was *written*. The product contains no
  coding-assistant capability and should not grow one.
- **Rows 103–111 (Career & Learning)** are interview questions, STAR
  answers, ATS resume scoring and job-description matching.
- **Rows 120–124 (Education)**, **125–129 (Hardware advice)**,
  **136–140 (Everyday help** — land-record terminology, admission fees).
- **Row 99 is "Gap analysis"** — this document is an instance of it.

The supplied README authorises exactly this reading: *"The catalogue
describes request/activity types, not necessarily 1:1 application
features… A category being present in the catalogue does not authorize
adding a feature that the app, provider, architecture, or security model
cannot safely support."*

Treating all 149 rows as a backlog would turn a governed enterprise
analytics platform into a general-purpose chatbot, and would route around
every control in `governance/`. §3 records each exclusion with its reason.

### One claim the package told me to check, which turned out true

The package README cautions: *"Video generation is a candidate category
and must not be represented as supported unless a real implementation
exists."*

**It exists.** `media_gen/video.py`, routed from `generation_node` via
`_VIDEO_INTENT_KEYWORDS`, with `Settings.media_gen_video_duration_seconds`
verified live against the provider's model list. It is **off by default**
and uses a **paid external provider** (IMA Studio) at materially higher
cost per call than an image. Row 86 is therefore Complete-but-gated, and
the audit says so in both directions rather than defaulting to "absent".

---

## 2. Architecture inventory (traced, not assumed)

Authoritative map: [`architecture/context-map.md`](architecture/context-map.md).

| Layer | Implementation | Evidence |
|---|---|---|
| UI | React 19 + Vite + TS + Tailwind + Zustand + TanStack Query, i18n ×5 | `frontend/src/` — 10 component families, 88 test files |
| Chat state | One Zustand store | `frontend/src/store/chatStore.ts` (863 lines) |
| API | FastAPI, ~70 route modules | `api/main.py` (`/ask`, `/execute`, `/health`, `/metrics/*`) + routers |
| Orchestration | LangGraph: 18-node SQL graph; multi-source graph above it with **parallel fan-out** | `agent/graph.py`; `agent/orchestrator/graph.py`, `nodes.py::route_after_router` returns a list |
| SQL safety | sqlglot AST allowlist, re-validated on every display/execution | `agent/sql_validator.py`; `tests/test_sql_validator*.py` (49) |
| Execution | `validate_sql` → `enforce_row_limit` → `qualify_table_schema` → `execute_readonly_sql` → `govern_rows_for_caller` | `db/execution.py`, `api/main.py` |
| Governance | Request / result / recommendation policy, 19 domains, 95 domain skills | `governance/`, `domain_skills/` |
| Identity | PostgreSQL + SQLAlchemy 2.0 + Alembic (the only real migrations) | `identity/` |
| Retrieval | ChromaDB (schema + business context); SQL Server `VECTOR` (documents) | `embeddings/`, `retrieval/`, `rag/store.py` |
| LLM | Ollama, local, `llama3.1:8b`; vision `qwen3.8:27b` | `agent/llm_client.py`, live `/health` |
| Files | 8 processors + malware scan before any parser | `attachments/processors/`, `security/malware_scanner.py` |
| Media | Image/video/audio generation (off, paid); media search (on) | `media_gen/`, `media/` |
| Web | Tavily (off by default) | `search/web_search.py` |
| Observability | Per-worker metrics, SSE progress | `observability/metrics.py`, `api/streaming.py` |

**Live environment confirmed:** 4 MSSQL databases reachable
(incl. `AdventureWorksDW2025`, 36 tables indexed), Ollama reachable.

### Baseline (measured at `bea4a34`)

| Check | Result |
|---|---|
| `pytest -q` | **7197 passed**, 11 deselected, 425.94s |
| `npm run test` | **1147 passed**, 88 files |
| `tsc --noEmit` / `npm run build` / `black --check` | Clean |
| `ruff check .` | 1 finding — pre-existing (`agent/multiagent/agents.py` I001) |
| `mypy .` | Pre-existing backlog (CI records 281, all in test files) |

Both lint findings are disclosed in [`CLAUDE.md`](../CLAUDE.md) §13 and are
**not** attributable to this work.

---

## 3. Why 78 rows are out of scope

Grouped, with the reason stated once per group. Full per-row detail is in
the CSV.

| Rows | Group | Reason |
|---|---|---|
| 10–25 | Software Engineering | Describes how this app was *built*, not what it does. No coding-assistant exists; adding one puts a second product inside an analytics platform. |
| 46–47 | AI strategy | Use-case discovery and competitor benchmarking are product-strategy activities. |
| 58 | Bug triage | A team process. |
| 66–67 | Requirements / checklists | Authoring activities performed with an assistant. |
| 69–78 | Writing & Communication | Rephrasing, proofreading, bios, LinkedIn posts, emails, resumes, SEO copy. **A free-text writing assistant would route around every control in `governance/`** and is also a data-exfiltration surface. |
| 82–84 | Design & marketing graphics | Design activities, not platform capabilities. |
| 89–91 | Tax / dev-cost / pricing | Business activities performed with an assistant. |
| 94, 96, 99 | Pros-cons, roadmap, gap analysis | Assistant activities — row 99 is this document. |
| 103–111 | Career & Learning | Personal career assistance. **Several would process personal data the governance engine exists to restrict**, with no business purpose. |
| 112–119 | Project & Program Management | Delivery planning, PMO, RAID, estimation. `workflow/` covers approvals only, a different thing. |
| 120–124 | Education | A general tutor is a different product. |
| 125–129 | Hardware & Product Advice | Consumer advisory. |
| 130–135 | Websites & Digital Products | About building products, not this product's features. |
| 136–140 | Everyday & Administrative | Consumer assistant activities. |

**None of these is excluded for being hard.** Each is excluded because it
belongs to a different product, or because implementing it would weaken a
control this platform exists to enforce.

---

## 4. The Core partials — where the real work is

Grouped into cohesive capabilities, as the brief requires, rather than
treated as a row per feature. **Written as "the 14 Core partials"**; three
groups have since closed (struck through below) and one row joined the set
after being reclassified, so the live count is the 13 Core partial rows
§1 now reports: 2, 7, 8, 28, 32, 41, 49, 59, 63, 79, 88, 145 and 147.

| # | Capability | Rows | What exists | What stops short |
|---|---|---|---|---|
| ~~**A**~~ | ~~**SQL explanation**~~ | 27 | **CLOSED 2026-10-17 (Batch 1).** `describe_sql` reads the AST `validate_sql` already parsed; six structural rows added to the existing metadata grid. Deterministic — no LLM call, no new dependency. See the implementation log | — |
| **B** | **Research depth & verification** | 2, 7, 8 | Web search with synthesis, inline citations, hedging when sources disagree | No verification pass, no multi-step research plan, no iterative retrieval |
| ~~**C**~~ | ~~**Accessibility assurance**~~ | 60 | **CLOSED 2026-10-17 (Batch 2).** `jsx-a11y` enabled in the existing oxlint config (no new dependency) + `axe-core` runtime checks in the vitest suite. Contrast remains browser-only — see the log | — |
| **D** | **Load / capacity evidence** | 59 | Complete k6 + compose stack in `eval/load/`; CI smoke scenario | Never executed — Docker daemon not running |
| **E** | **DAST** | 49 | SAST (bandit) blocking in CI | DAST has never run; needs a deployed target |
| ~~**F**~~ | ~~**AI usage rollup**~~ | 45 | **CLOSED 2026-10-17 (Batch 4).** `PerformanceMetrics.record_llm_call` sums Ollama's own per-call counts per model, fed from the one `_log_ollama_timing` funnel every `client.chat` already passes through; served by the two metrics routes that already existed, each with its own scope. **Tokens only — no monetary figure**, which stays N/A for local Ollama. See the implementation log | — |
| **G** | **Office document generation** | 63 | PDF, CSV and Markdown export | No Word or Excel generation |
| **H** | **Prompt registry activation** | 41 | Versioned registry with approve/deploy/rollback | Deliberately never hot-swaps a live prompt (`CLAUDE.md` §5.5) — **not a defect** |
| **I** | **SQL repair transparency** | 28 | Bounded retry + `review_sql_node` repairs automatically | No user-facing account of what was repaired |
| **J** | **Screenshot troubleshooting** | 145 | Image attachments, OCR, vision Q&A | No troubleshooting-specific workflow |
| **K** | **Ad-hoc arithmetic** | 88 | Deterministic maths inside `analytics/` and SQL | No calculator outside a query |
| **L** | **ETL / data cleaning** | 32, 33 | Schema refresh, onboarding, `data_quality/` scans | No transformation — **by charter**: read-only client |
| **M** | **Repository/file inspection** | 147 | `attachments/` ingests and analyses uploads | No codebase inspection (nor should there be) |
| **N** | **Multi-dimension research report** | 8 | See B | — |
| **O** | **Content translation** | 79 | Added 2026-10-17 — `agent/translation.py`: protect / translate / restore / **verify**, discarding any translation whose protected spans (figures, dates, citations, `[restricted]`) do not come back as the same multiset. Not in the original 14: row 79 was "Needs decision" at audit time | **Built, not wired** — no route, node or setting calls it. Wiring it is a separate decision, not an oversight |

**H, L and M are partial by deliberate design**, not by omission. They are
recorded as partials for honesty, and the plan proposes no work on them.

---

## 5. Security observations

No new vulnerability was found in this audit. Three observations shape the
plan:

1. **The governed answer path is the product's integrity boundary.** Rows
   69–78 (free-text writing) would bypass it entirely. This is the single
   strongest reason for the out-of-scope calls, not a matter of taste.
2. **Catalogue rows 103–111 would introduce PII processing** (resumes,
   career profiles) into a platform whose `governance/` engine exists to
   restrict exactly that, with no offsetting business purpose.
3. **Three capabilities are gated behind paid external providers** — image
   (80), video (86) and image editing (81) via IMA Studio, all off by
   default. Enabling any of them is a cost and data-egress decision, not a
   code change, and is listed in the decisions log.

---

## 6. How far this audit's claims reach

- **Code exists** — established for every row, by citing files.
- **Behaviour is tested** — established where a row cites a test.
- **Verified end to end against live infrastructure** — established only
  for what was exercised today: the API `/health` path, 4 live databases,
  Ollama reachability, and the frontend build served by the running API.

A row marked Complete means *implemented and, where cited, covered by
tests* — not that it was driven through a browser against production data.
