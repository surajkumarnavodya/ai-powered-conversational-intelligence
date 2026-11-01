# API

A REST API (`api/`, FastAPI) over the LangGraph agent. This same process
also serves the React dashboard (`frontend/`), the primary human-facing
surface (SQL review, retry timeline, schema browser, charts) — see
`api/main.py`'s `StaticFiles` mount. The API remains fully usable on its
own for programmatic/scripted access and as the
interface `docs/DEPLOYMENT.md`'s reverse-proxy/container guidance sits in
front of.

## Why this is safe: no second implementation

`POST /ask` calls `agent.orchestrator.graph.run_orchestrated` directly —
which itself is a pure pass-through to `agent.graph.run_agent` unless
`ENABLE_MULTI_SOURCE_ROUTER` is set. Every safety layer described in
`SECURITY.md` (input guard, SQL validator's SELECT-only allowlist, row
cap, query timeout, the process-wide LLM-call rate limiter,
sensitive-column blocking) governs this endpoint identically, because it's
the same graph execution, not a parallel code path that could drift out of
sync or be weaker. `GET /schema/tables` similarly reuses
`db.schema_introspection.introspect_schema` — the same metadata-only
introspection the dashboard's schema browser and
`scripts/build_embeddings.py` use.

## Running it

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Or via Docker Compose — see `docs/DEPLOYMENT.md` (`docker compose up api`).
It reads the same `.env` the React dashboard's backend calls into (same
`config/settings.py`, same database/Ollama/Chroma configuration) — there
is nothing API-specific to configure beyond the optional `API_AUTH_TOKEN`
described below.

## Endpoints

> ### This document describes 82 of the API's 212 operations
>
> **Measured 2026-10-30 (UAT-readiness Phase 10, `DOC-020`).** The
> application exposes **187 paths / 212 operations** in
> `/openapi.json`, plus `GET /live` and the SPA catch-all
> `GET /{full_path:path}`, which carry `include_in_schema=False` — 213
> addressable API operations in all. The sections below describe 82 of
> them in prose. That has always been true; what was missing was saying
> so, so a reader could not tell "undocumented" from "does not exist".
>
> §[Full endpoint index](#full-endpoint-index) at the end of this file
> lists **every** operation and marks which ones have a section here.
> For an operation with no section, the authoritative contract is its
> router module's own docstring and `api/schemas.py`'s response model —
> and the live schema is always `GET /openapi.json` (Swagger UI at
> `/docs`, ReDoc at `/redoc`), which is generated from the code and so
> cannot drift from it the way this page can.
>
> Reproduce the diff:
>
> ```powershell
> .\.venv\Scripts\python.exe -c "import json; from api.main import app; print(json.dumps(app.openapi()))" > openapi.json
> ```

### `GET /health`

Real, non-cached reachability check of every external dependency: every
configured database (`db.connection.test_connection`) plus its schema
index, and Ollama (a cheap `list()` call — no generation). Returns HTTP
`200` with `{"status": "ok", ...}` when everything is reachable, `503` with
`{"status": "degraded", ...}` otherwise, so container/orchestrator
health-check tooling that checks the status code works correctly. Never
requires auth (a health check consumed by infrastructure tooling, not a
data-exposing endpoint).

Also reports live chat-image-vision diagnostics
(`vision_enabled`/`vision_model`/`vision_model_available`/`ocr_enabled`) —
`vision_model_available` is a real, live lookup against Ollama's own
pulled-model list (reusing the same `list()` call above, not a second
round-trip), so a configured-but-never-pulled model name is caught here
rather than only discovered the first time a user attaches an image.

```json
{
  "status": "ok",
  "databases": [
    {
      "name": "default",
      "connection": {"ok": true, "detail": "Connection successful."},
      "schema_index": {"ok": true, "detail": "31 table(s) indexed."}
    }
  ],
  "ollama": {"ok": true, "detail": "Reachable at http://localhost:11434."},
  "voice_enabled": true,
  "media_search_enabled": false,
  "local_auth_enabled": false,
  "google_signin_enabled": false,
  "google_client_id": null,
  "vision_enabled": true,
  "vision_provider": "ollama",
  "vision_model": "llava",
  "vision_model_available": true,
  "ocr_enabled": true
}
```

`google_signin_enabled`/`google_client_id` reflect whether "Continue with
Google" is available on this deployment (`GOOGLE_OAUTH_CLIENT_ID` set *and*
`LOCAL_AUTH_ENABLED=true`) — `google_client_id` is the one deliberately
public value served this way rather than baked into the frontend build, so
it can be rotated with a process restart alone. See
`docs/AUTHENTICATION.md`'s "Google sign-in" section.

### `GET /live`

Pure liveness — `{"status": "alive"}`, always, with no dependency check at
all (never touches the database, Chroma, or Ollama). Added alongside
`/health` above (2026-09-27) so a container orchestrator's liveness probe
and readiness probe can point at different things: a slow/degraded
dependency should never cause a healthy process to be killed and
restarted (liveness → `/live`), while a process that genuinely can't reach
its database shouldn't receive new traffic (readiness → `/health`). Never
requires auth, same reasoning as `/health`.

### `POST /ask`

Runs one question through the full agent graph — schema retrieval,
(for a question that reads as non-trivial — top-N-per-group, year-over-
year growth, several metrics at once) an up-front query plan, SQL
generation, plan-conformance review, validation, cost estimation,
execution, self-correction — and returns the outcome. Requires auth if
`API_AUTH_TOKEN` is set (see below). The planning/review steps are a
zero-cost pass-through for an ordinary question and add one or two extra
LLM round-trips (so extra latency) for a question they do trigger — see
[`docs/ARCHITECTURE.md`](ARCHITECTURE.md#2-retry--self-correction-semantics).

Request:

```json
{
  "question": "What were total internet sales in 2012?",
  "conversation_history": [
    {"question": "prior question", "sql": "SELECT ...", "tables": ["FactInternetSales"], "status": "succeeded"}
  ],
  "enable_insight": true,
  "model": "qwen2.5:7b",
  "sources": ["sql", "policy"]
}
```

`sources` is optional (from a prior `GET /sources` response's
`sources[].name`) — omit it to let the router choose from everything
configured and authorized, which is exactly what every caller from before
this field existed does.

**It is a filter, never a grant.** The selection is intersected with what
standing configuration makes available *and* what the caller's role(s)
permit, so naming a source here can only narrow the route — it can never
reach a source the caller could not otherwise use. The pre-existing role
check in `router_node` still runs on whatever survives the filter. See
`agent/orchestrator/nodes.py::_apply_source_selection`.

Behaviour worth knowing:

- An **unrecognized source name** returns `422` naming the valid options.
  This leaks nothing: every registered name is already public via
  `GET /sources`. Whether a source is *configured*, or whether this caller
  may *use* it, is deliberately **not** answered at this layer — that
  would turn a 422 into an oracle for configuration and permissions.
- An **empty array is rejected**. It would be ambiguous between "no
  restriction" and "no sources at all"; callers omit the field instead.
- Names are lower-cased and de-duplicated, preserving first-seen order.
- A selection that **cannot be honored does not fail the request.** If a
  named source is not configured, the question is answered from the
  unrestricted route and `source_selection_notice` says so. If the caller
  lacks permission for it, the existing `permission_denied_notice` says
  so. Failing instead would turn a stale client — or an operator switching
  a source off — into an outage for questions that are still answerable.
- `attachments` is **exempt**: a file attached to this question stays in
  the route whatever the selection says, because attaching it is itself an
  explicit per-request choice and an attachment is never silently
  discarded.
- Supplying `sources` makes the orchestrator graph run even with
  `ENABLE_MULTI_SOURCE_ROUTER=false`, for the same reason a non-empty
  `attachment_ids` does. Omitting it leaves that path completely
  unchanged.

`model` is optional (from a prior `GET /models` response's `models[].id`) —
omit it to use the server-configured default (`Settings.ollama_model`);
every existing caller from before this field existed keeps working
unchanged. If provided, it's validated against `Settings.ollama_allowed_models`
*before* any LLM/DB work starts — an unrecognized or disallowed value
returns `400 Bad Request` naming the model, never a silent fallback or a
raw string passed through to Ollama unchecked. See
[`docs/CONFIGURATION.md`](CONFIGURATION.md#model-selection) for the full
online-catalog/configured/installed/selectable distinction and how to add
or remove a model.

`conversation_history` is optional and, unlike the UI (which reconstructs
it client-side from `frontend/src/store/chatStore.ts`'s `queryHistory`),
must be resent by the caller each request — the API has no server-side
session of its own. `enable_insight` defaults to `true`.

`forecast_horizon` (optional, positive integer) is how many future
periods to forecast, and is **only meaningful when the question is
classified as FORECAST-intent** (`agent.intent.AnalyticalIntentType`) —
ignored otherwise. Omit it to use `Settings.forecast_default_horizon`.
A value above `Settings.forecast_max_horizon` returns `400`, never a
silent clamp.

Response (mirrors what the React dashboard renders — see `agent.state.AgentState`):

```json
{
  "status": "succeeded",
  "model": "qwen2.5:7b",
  "sql": "SELECT SUM(SalesAmount) FROM FactInternetSales WHERE ...",
  "result_columns": ["TotalSales"],
  "result_rows": [[1234567.89]],
  "row_count": 1,
  "retry_count": 0,
  "max_retries": 3,
  "query_plan": null,
  "attempt_history": [{"attempt": 1, "sql": "...", "outcome": "succeeded", "error": null, "will_retry": false}],
  "insight": "Total internet sales in 2012 were $1,234,567.89.",
  "cost_notice": null,
  "rejection_reason": null,
  "rejection_message": null,
  "rate_limit_message": null,
  "clarification_message": null,
  "failure_explanation": null,
  "error_history": []
}
```

`status` is one of `AgentState`'s values (`succeeded`, `failed`,
`rejected`, `needs_clarification`, `rate_limited`, ...) — check it before
trusting `sql`/`result_rows`, exactly as the UI does. `query_plan` is
non-null only for a question `agent/complexity.py` judged non-trivial
(see `docs/ARCHITECTURE.md`) — the ordered plan steps the SQL above was
generated and checked against; `max_retries` is this question's actual
retry budget, which can exceed the configured `MAX_RETRIES` for that same
class of question. `model` echoes back whichever model actually answered
this question (the request's own `model`, once validated, or the server
default).

**`sql_summary`** (optional, `null` when no SQL was generated or it did not
parse) describes the statement **as the parser read it** — it is purely
descriptive and gates nothing, because `agent/sql_validator.py::validate_sql`
remains the only thing that decides whether SQL may run:

```json
"sql_summary": {
  "statement_type": "SELECT",
  "referenced_tables": ["FactInternetSales", "DimDate"],
  "joins": [{"kind": "INNER", "table": "DimDate"}],
  "filter_columns": ["CalendarYear"],
  "aggregates": ["SUM(SalesAmount)"],
  "group_by": ["CalendarYear"],
  "order_by": ["CalendarYear"],
  "is_distinct": false
}
```

#### The other 26 `AskResponse` fields

**The example above shows 16 of `AskResponse`'s 53 fields**, and this
document named only 27 of them anywhere until 2026-10-30 (`DOC-021`).
The gap mattered for one field in particular, so read this before
writing a client.

**`synthesized_answer` is where the prose answer lives, and `insight`
is not.** `insight` is the one-sentence summary of a *SQL* result.
`synthesized_answer` carries the combined answer text whenever two or
more sources contributed, **and it carries the whole answer on the
router's no-retrieval route** — a general-knowledge reply, or the fixed
"I couldn't match that to a data source" message. A client that reads
only `insight` therefore renders a web, document, policy or
no-match answer as blank, which looks exactly like a server fault and
is not one. `answer_truth_level` is
`agent.provenance.DataTruthLevel`'s value for that text and is
`ai_inference` when it came from the model's own knowledge rather than
from a source; never present it as observed fact.

The rest, grouped. Every one is optional with a default, so an existing
client that ignores all of them is unaffected (`CLAUDE.md` §6.3).

| Field | What it is |
|---|---|
| `synthesized_answer`, `answer_truth_level` | above |
| `document_result`, `policy_result`, `web_result`, `media_search_result`, `attachment_result` | one per non-SQL source that ran, each a `SourceAnswerOut`-shaped block with its own citations |
| `schema_tables` | the tables retrieved into the generation prompt, as the dashboard's schema panel shows them |
| `stage_timings` | per-node wall-clock timings in execution order, retried attempts included |
| `answer_duration_ms` | server-side wall time for the whole run; persisted with the turn, so a reopened conversation shows the same figure |
| `conversation_id`, `message_id`, `session_id` | the persisted turn's identifiers. `conversation_id`/`message_id` are set only for a locally-authenticated caller whose turn was stored; `session_id` is the correlation token described under the request above |
| `followup_classification`, `followup_resolved_against`, `context_resolution` | how this question was read against the conversation before it — see [`docs/CONVERSATIONAL_CONTEXT.md`](CONVERSATIONAL_CONTEXT.md). `context_resolution` is `null` when `ENABLE_CONTEXT_RESOLUTION` is off |
| `analytical_intent`, `analytical_plan` | Prompts 11 and 12. **Read-only context for display** — neither is a way to change what ran, and `analytical_intent` is always `ai_inference` |
| `analytical_result`, `forecast_result` | the deterministic statistical breakdown, and the forecast when the question was FORECAST-intent. A forecast is never a `DATABASE_FACT` |
| `governing_metrics` | published `CONFIRMED_BUSINESS_TRUTH` metric definitions this answer was computed against (Prompt 10); empty when none matched |
| `insight_numbers_checked` | how many numbers in `insight` the grounding check verified against the real rows. `0` with a non-null `insight` means the sentence contained no checkable number — not that it failed |
| `display_names_repaired`, `display_name_notice` | key columns the display-name guard rewrote into a joined readable name, and an honest disclosure when it could not — see [`docs/DISPLAY_NAMES.md`](DISPLAY_NAMES.md) |
| `restricted_field_notice` | set when the generated SQL repeatedly referenced a column this caller's role may not view, so the answer is incomplete and says so |
| `low_confidence_notice` | set when a multi-table join returned 0 rows. **Detection only** — it does not mean the answer is wrong, and its absence does not mean it is right |

`statement_type` and `referenced_tables` come from
`summarize_sql`; the six structural fields come from `describe_sql`, which
reads the **same AST `validate_sql` already parsed** — no second parser and
deliberately no LLM call, since an explanation that hallucinated a join
would render beside a real validator verdict and read as authoritative.
Every one of the six is optional with a default, so a caller written before
they existed is unaffected and a turn whose SQL does not parse simply
carries the empty defaults.

`kind` on a join is the parser's own side (`INNER`, `LEFT`, `RIGHT`,
`FULL`, `CROSS`), never inferred from the SQL text. `referenced_tables` is
the tables the statement actually uses, which is normally fewer than
retrieval offered.

Three things it deliberately does **not** report, each a correctness
boundary rather than an omission:

- **No row limit**, although the AST has one. `validate_sql_node` writes
  `enforce_row_limit`'s output back into the SQL, so the `TOP n`/`LIMIT n`
  a caller sees is *this platform's governance cap*, not the query's own
  intent — reporting it as query behaviour would present a control as
  authorship.
- **No business meaning.** `group_by` is the declared grain, not "sales by
  region"; naming the business concept is `semantic/`'s to do.
- **No filter values** — `filter_columns` is column names only. A
  predicate can carry data, and this response is rendered in a UI and may
  be logged.

Each list is bounded at 12 entries of 80 characters, so one pathological
statement cannot grow the response.

Rate limiting: a per-client-IP question-submission limiter
(`Settings.question_rate_limit_per_minute`, mirroring the UI's
per-session limiter) returns `429` with a `Retry-After` header when
tripped. The stricter, process-wide LLM-*call* limiter
(`Settings.llm_call_rate_limit_per_minute`) applies automatically inside
the agent graph itself, same as it does for the UI.

`attachment_ids` (optional) references file(s) already uploaded via
`POST /attachments/upload` below — see "Attachments" for the full upload
flow. Attaching a file is never a reason SQL gets generated: an
attachment-only question ("extract text from this image," "summarize this
PDF") is deterministically routed away from schema retrieval/SQL
generation entirely (`agent/orchestrator/nodes.py::_looks_like_attachment_only_question`),
regardless of how many databases are configured. A question that
genuinely names a database keyword alongside an attachment ("use this
file to query the database") still reaches the normal SQL path, with the
attachment folded in as grounding context, never as privileged
instructions — the existing read-only SQL validator and schema
authorization are unchanged either way.

### Attachments

`POST /attachments/upload` (`api/attachments.py`) — multipart upload.
Validates (extension + magic-byte signature, size limits), malware-scans
(if `MALWARE_SCAN_PROVIDER` is configured), stores under a generated id,
and eagerly processes each file independently — one bad file in a batch
never fails the others. Requires `Permission.ASK` (the same permission
asking a question needs).

```json
// Response
{
  "attachments": [
    {"attachment_id": "att_...", "filename": "invoice.png", "media_type": "image/png", "size_bytes": 48213, "processing_status": "succeeded", "processing_error": null}
  ],
  "errors": []
}
```

`attachment_id` is what gets passed as `POST /ask`'s `attachment_ids`.
`DELETE /attachments/{id}` removes it (ownership-scoped — a wrong or
another caller's id resolves to a `404`, never confirming it exists).

`GET /attachments/capabilities` — live, per-deployment capability report
(no request body). Reflects actual configuration, not a static claim:

```json
{
  "enabled": true,
  "vision_input": true,
  "vision_model": "llava",
  "ocr": true,
  "image_resize": true,
  "image_blur": true,
  "image_text_removal": true,
  "image_text_removal_method": "opencv_telea_inpaint",
  "image_ai_editing": false,
  "image_ai_editing_provider": null,
  "native_pdf_input": false,
  "max_image_bytes": 10485760,
  "max_document_bytes": 26214400,
  "max_attachments_per_message": 5,
  "max_total_attachment_bytes": 52428800,
  "max_resize_dimension_px": 4096,
  "max_text_removal_regions": 20,
  "max_ai_edit_prompt_length": 500,
  "supported_image_extensions": [".gif", ".jpeg", ".jpg", ".png", ".webp"],
  "supported_document_extensions": [".csv", ".docx", ".json", ".md", ".pdf", ".pptx", ".txt", ".xlsx"],
  "resize_presets": [{"name": "medium_800", "width": 800, "height": 800}]
}
```

`ocr`/`image_text_removal` reflect whether the `pytesseract` *package* is
importable, not whether the system Tesseract *binary* is installed — a
missing binary degrades one OCR/remove-text request to an honest warning
at call time, it doesn't flip this flag.

**Explicit image actions** (image attachments only — each returns `404`
for a document attachment or an id the caller doesn't own):

| Route | What it does |
|---|---|
| `POST /attachments/{id}/extract-text` | Real Tesseract OCR — returns `raw_text`/`cleaned_text` (both exact recognized text, never a model paraphrase) plus per-word bounding boxes/confidence. Distinct from asking a natural-language vision question via `/ask`. |
| `POST /attachments/{id}/resize` | Deterministic Pillow resize — `{"width": 800, "height": null, "fit": "contain", "output_format": null}`. No model call. Always returns a brand-new attachment (the original is never mutated). |
| `GET /attachments/{id}/detect-text-regions` | OCR-proposed text-line regions, for the "Remove text" workflow's confirm/adjust step. Returns `[]` (not an error) if OCR is unavailable or finds nothing — the caller falls back to manual region selection. |
| `POST /attachments/{id}/remove-text` | Real pixel editing via classical OpenCV inpainting (Telea's algorithm) — `{"regions": [{"left": 10, "top": 10, "width": 80, "height": 20}]}`, either OCR-proposed (caller-confirmed) or manually drawn. **Not** generative AI and never a solid-color rectangle; the response's `warnings` field says so explicitly. Always returns a brand-new attachment. |
| `POST /attachments/{id}/blur-region` | Deterministic, local Pillow Gaussian blur over a painted mask — `{"mask_data_url": "data:image/png;base64,...", "radius": 18}`. No model call; works even when AI-guided editing (`image_ai_editing`) is off. Always returns a brand-new attachment. |
| `POST /attachments/{id}/ai-edit` | **Real, generative** AI-guided editing (IMA Studio `image_to_image`) — `{"operation": "remove_object", "prompt": "Remove the selected object.", "mask_data_url": "data:image/png;base64,..."}` (`operation` is one of `remove_object`/`replace_background`/`replace_sky`/`region_edit`/`enhance`; `mask_data_url` is optional). Returns `404`-shaped `{"status": "failed", "error_code": "not_configured", ...}` when `image_ai_editing` is `false` — never a raw exception. IMA has no native mask channel, so a provided mask is conveyed as a translucent overlay + text instruction, disclosed via the response's `warnings`, never presented as pixel-exact. Shares this app's existing media-generation rate limit and a per-caller idempotency key (`idempotency_key`, optional) so a duplicate click never pays for a second generation. See [`docs/image-editing-architecture.md`](image-editing-architecture.md) for the full design. |

The middle three (`remove-text`/`blur-region`/`ai-edit`'s success case)
share the `ImageEditResultResponse`-shaped fields (`attachment_id`,
`source_attachment_id`, `operation`, `image_data_url`, `media_type`,
`width`, `height`, `size_bytes`, `warnings`) — `ai-edit`'s own response
additionally carries `status`, `mask_provided`, `provider`, `model`,
`error_code`, `error_message` since an edit can fail in ways local
resize/remove-text/blur cannot (no credentials, content-policy rejection,
provider timeout).

### `GET /models`

The Ollama Text-to-SQL model registry — every model
`Settings.ollama_allowed_models` configures, enriched with a live
"is it actually installed on the connected Ollama instance right now"
check. Same `Permission.ASK` gate as everything else asking-adjacent.
Never calls the online Ollama Library — see
[`docs/CONFIGURATION.md`](CONFIGURATION.md#model-selection).

```json
{
  "provider": "ollama",
  "default_model": "llama3.1:8b",
  "selection_enabled": true,
  "models": [
    {
      "id": "llama3.1:8b",
      "display_name": "Llama 3.1 8B",
      "is_default": true,
      "enabled": true,
      "installed": true,
      "available": true,
      "recommended": true,
      "parameter_size": "8B",
      "context_length": 128000,
      "resource_level": "medium",
      "capabilities": ["text", "sql", "reasoning", "tools"],
      "description": "This application's own default model. ..."
    },
    {
      "id": "qwen2.5:7b",
      "display_name": "Qwen 2.5 7B",
      "is_default": false,
      "enabled": true,
      "installed": false,
      "available": false,
      "recommended": true,
      "parameter_size": "7B",
      "context_length": 32000,
      "resource_level": "medium",
      "capabilities": ["text", "sql", "reasoning", "tools"],
      "description": "..."
    }
  ]
}
```

A model that's configured but not yet `ollama pull`ed on the server's own
machine still appears here (`installed: false`) rather than being hidden —
the frontend disables selecting it and shows "Not installed" instead. Never
exposes `OLLAMA_HOST`, secrets, or any other environment/connection detail.
Cache this response client-side (the React dashboard does, 30 seconds,
matching `GET /health`) — this endpoint is never called from `/ask` itself.

### Governed model routing: `/model-routing/*`

Three read-only routes over the model router (Prompt 47,
[`47_MODEL_ROUTING_COST_OPTIMIZATION_CONTRACT.md`](../47_MODEL_ROUTING_COST_OPTIMIZATION_CONTRACT.md)),
which picks *which* locally-installed Ollama model drafts each LLM call the
SQL pipeline makes — by complexity, risk, latency and capability — instead
of using one model for every call in a run.

All three answer **404** unless `ENABLE_MODEL_ROUTING=true` (off by
default). **None of them calls an LLM**, and there is deliberately no write
route: routing is configured in `.env`, and the models it may escalate to
are governed through `/governance` below. A write route here would be a
second, ungoverned way to change which model answers a question.

| Route | Permission | What it does |
|---|---|---|
| `GET /model-routing/policy` | `ASK` | The resolved configuration and candidate set for the calling tenant. |
| `POST /model-routing/preview` | `ASK` | What routing *would* decide for a question, per task. Consumes no budget and no circuit-breaker probe. |
| `GET /model-routing/metrics` | `ADMIN_CONFIG` | The live per-route quality/latency/failure/cost rollup. |

`GET /model-routing/policy` returns the candidate models (id, `tier`,
`tier_known`, `capabilities`, `context_length`, `recommended`, `is_default`,
`evaluation_backed`, `evaluation_quality_score`, `governance_version_id`),
the resolved thresholds, `tier_cost_units`, and one entry per task with its
`risk_level` and `required_capabilities`. Evidence is scoped to the caller's
own tenant, so one tenant never learns which models another approved.

`POST /model-routing/preview` takes `{"question": "...", "latency_sensitive":
false}` and returns the matched `complexity_signals`, the
`complexity_score`, and one decision per task:

```json
{
  "enabled": true,
  "complexity_signals": ["top_n_per_group", "growth_comparison", "multi_metric"],
  "complexity_score": 3,
  "decisions": [
    {
      "task": "sql_generation",
      "model_id": "qwen2.5:14b",
      "tier": "large",
      "reason": "upgraded_for_complexity",
      "baseline_model_id": "llama3.1:8b",
      "risk_level": "high",
      "complexity_score": 3,
      "required_capabilities": ["sql"],
      "considered_model_ids": ["qwen2.5:14b"],
      "fallback_chain": ["llama3.1:8b", "llama3.2:3b"],
      "cost_units": 5,
      "evaluation_backed": true,
      "policy_version": "1.0.0",
      "truth_level": "ai_inference"
    }
  ]
}
```

It runs the same pure decision function the live pipeline uses, so a preview
and a real run cannot disagree. The question text is scanned for complexity
signals and discarded — never put in a prompt, never stored, and never
echoed back (only the matched signal *names* are).

`reason` is a closed set: `routing_disabled`, `caller_pinned_model`,
`default_no_change`, `upgraded_for_complexity`,
`downgraded_for_simplicity`, `no_eligible_candidate`,
`no_evaluation_evidence`, `upgrade_blocked_by_latency`, `budget_exhausted`,
`circuit_open`.

**`cost_units` are ordinal compute units, never money.** Every routable
model runs locally through Ollama, so there is no per-token price to report;
small/medium/large are 1/2/5, used only to bound how often one request may
escalate.

**`truth_level` is always `ai_inference`.** A routing decision is this
platform's judgment about which model was likely to do better, never an
observed fact about the answer's quality.

`GET /model-routing/metrics` returns per-`(task, model)` counters —
`calls`, `successes`, `availability_failures`, `quality_failures`,
`quality_failure_rate`, `fallback_uses`, mean/p50/p95/max latency, and
`cost_units` — plus `decisions`, `escalations`, `de_escalations`,
`decisions_by_reason`, `budget_denials`, `breaker_denials` and
`total_cost_units`. `quality_failure_rate` is the share of judged calls this
application's own deterministic parsers/validators rejected — a real
observed signal about a route, explicitly **not** an accuracy score (a
parseable answer can still be wrong). Single-process, in-memory and reset on
restart, the same disclosed limitation `GET /metrics/performance` carries.
No question, SQL or answer content appears in any of these responses.

`POST /ask` gained one additive field for this: `route_decisions`, the same
decision shape as above, one entry per routed LLM call the question made.
It is empty when routing is off (the default), when the caller pinned
`AskRequest.model`, or when the run never reached an LLM call — in all of
those cases the single model named by `model` served every call.

### Proactive KPI monitoring: `/kpi-monitoring/*`

Two routes over the KPI monitor (Prompt 50,
[`50_PROACTIVE_AI_ANALYST_CONTRACT.md`](../50_PROACTIVE_AI_ANALYST_CONTRACT.md)),
which watches governed catalog metrics for meaningful movement.

Both answer **404** unless `ENABLE_KPI_MONITORING=true` (on by default).
**Neither queries a monitored database and neither calls an LLM** -- the
period series and dimension breakdowns arrive in the request body,
already fetched through `POST /execute`.

| Route | Requires | What it does |
|---|---|---|
| `POST /kpi-monitoring/scan` | local account + catalog review | Runs one monitoring pass; persists findings into the shared review queue unless `persist=false`. |
| `GET /kpi-monitoring/findings` | local account + catalog review | This tenant's KPI findings, most impactful first. |

**Deciding a finding is not re-implemented here.** Accept/dismiss/rollback
are `/semantic-intelligence/findings/...`'s existing routes over the same
rows -- a KPI finding is stored in the same governed queue the catalog
detectors (Prompt 35) and schema drift (Prompt 43) already use.

`POST /kpi-monitoring/scan` takes `database_id`, `policies`, `series`, and
optionally `calendar`, `quiet_periods`, `preferences` and `persist`:

```json
{
  "database_id": "default",
  "policies": [
    {"policy_id": "p-revenue", "metric_concept_key": "revenue",
     "comparison": "previous_period", "min_change_percent": 10.0,
     "lower_threshold": null, "dimension_columns": ["region"]}
  ],
  "series": [
    {"policy_id": "p-revenue",
     "points": [["2026-05", 1000.0], ["2026-06", 640.0]],
     "value_column": "revenue",
     "current_breakdown": {"columns": ["region", "revenue"],
                           "rows": [["East", 160.0]]},
     "baseline_breakdown": {"columns": ["region", "revenue"],
                            "rows": [["East", 500.0]]}}
  ],
  "quiet_periods": [{"name": "freeze", "periods": ["2026-07"]}],
  "preferences": [{"user_id": "ops", "min_risk_tier": "medium"}]
}
```

Each returned change carries its baseline (with a human-readable
`baseline_label`), the change, which detection methods fired, its rank and
`rank_reasons`, and an `evidence` object holding the affected dimensions,
`confidence` and `limitations`.

**`contributors` and `confidence` exist only when
`has_sufficient_evidence` is true**, and a contributor is a contribution
to an arithmetic total, **never a demonstrated cause**. When evidence is
insufficient the contributor list is empty, confidence is null, and
`limitations` says why.

**`truth_level` is always `ai_inference`** on a change and its evidence:
the numbers cited are observed arithmetic over the supplied rows, but the
judgment that a movement is *meaningful* is this platform's inference.

`comparison` is one of `previous_period`, `same_period_last_year`,
`trailing_average`. `kind` is one of `kpi_drop`, `kpi_spike`,
`kpi_threshold_breach`, `kpi_trend_break`.

`alert_bundles` describes, per recipient, which findings would be alerted
and why the rest were not -- `quiet_period`, `non_business_period`,
`below_user_severity`, `metric_muted_by_user`,
`duplicate_of_open_finding`. Reasons accumulate rather than
short-circuiting. **This platform has no delivery channel; nothing is
actually sent.** Suppression governs alerting only -- every finding is
still persisted and still appears in the review queue.

`created`/`updated`/`unchanged` report the storage-level half of duplicate
suppression: an identical re-run records `unchanged` and writes nothing.

### Executive AI briefing: `POST /briefing/generate`

One route over the executive briefing (Prompt 51,
[`51_EXECUTIVE_AI_BRIEFING_CONTRACT.md`](../51_EXECUTIVE_AI_BRIEFING_CONTRACT.md)),
which assembles an evidence-linked daily, weekly or monthly summary from
records the KPI monitor, schema drift, the recommendation engine and the
data-quality engine already produced.

Answers **404** unless `ENABLE_EXECUTIVE_BRIEFING=true` (on by default).
**It never queries a monitored database, never calls an LLM, and persists
nothing** -- the only database it reads is the identity database, for two
already-tenant-filtered repository reads.

| Route | Requires | What it does |
|---|---|---|
| `POST /briefing/generate` | local account + catalog review and/or recommendation view | Assembles one briefing for the caller's own tenant and returns the typed claims plus a markdown rendering. |

**Authorization is per section, and adds no new permission.** KPI and
schema-drift findings answer to the same catalog-review gate
`/kpi-monitoring/*` and `/schema-drift/*` apply to the same rows;
recommendation records answer to the same view gate `/recommendations`
applies. A caller holding one but not the other gets the other source
**named as omitted** in `coverage` and `limitations`, never silently
absent. A caller holding neither gets **403**. An unknown or cross-tenant
`database_id` is the same **404** a nonexistent one gets.

Request -- `data_quality_findings` is the one input that arrives in the
body, because nothing in this codebase persists a data-quality scan;
aggregates only, never a sampled row value:

```json
{
  "database_id": "default",
  "period": "daily",
  "period_label": "2026-06-30",
  "as_of": "2026-07-01T09:00:00+00:00",
  "data_quality_findings": [
    {"dimension": "completeness", "severity": "critical",
     "table": "sales", "column": "amount",
     "claim_text": "amount is null in 42.00% of rows",
     "formula": "null_rate = null_count / total_count",
     "observed": {"null_rate": 42.0},
     "engine_version": "1.0.0",
     "observed_at": "2026-07-01T08:00:00+00:00"}
  ]
}
```

`as_of` is the reference instant every staleness check is made against;
omitting it uses the server's current time. Supplying it is what makes a
briefing for a past period judge freshness against that period.

Every returned claim carries `claim_class` (`observed_fact` / `estimate` /
`recommendation`), its `truth_level`, and one or more evidence links --
each naming the record it came from, the deterministic numbers the claim
is allowed to state, and the engine/content versions that produced them:

```json
{
  "section": "kpi_movement",
  "claim_class": "observed_fact",
  "truth_level": "database_fact",
  "headline": "revenue fell 36.0% in 2026-06",
  "risk_score": 78, "risk_tier": "high",
  "is_stale": false,
  "evidence": [
    {"source": "kpi_monitoring.findings", "reference_kind": "kpi_finding",
     "reference_id": "0f1e...", 
     "observed": {"current_value": 640.0, "baseline_value": 1000.0,
                  "change_percent": -36.0},
     "versions": {"kpi_engine_version": "1.0.0", "finding_version": "2",
                  "briefing_engine_version": "1.0.0"},
     "recorded_at": "2026-06-30T22:00:00+00:00",
     "truth_level": "database_fact"}
  ]
}
```

Three response fields make a quiet briefing honest rather than merely
short:

- `coverage[]` distinguishes **not supplied** ("nothing was checked") from
  **supplied but quiet** ("none produced a claim") from **omitted** (with
  the caller's own reason), per source.
- `stale_inputs[]` lists every evidence record older than this cadence's
  freshness budget, or recording no timestamp at all. A stale claim is
  still shown, marked, and ranked below fresh claims of equal risk.
- `withheld_claims[]` lists every claim that was proposed but not shown,
  because its own evidence did not support it -- `no_evidence`,
  `missing_version`, `truth_level_mismatch` or `ungrounded_number`.

`empty_sections[]`, `truncated_sections[]`, the three claim-class counts,
`stale_item_count` and `report_markdown` round out the response. Nothing
is delivered anywhere: this platform has no email, webhook or chat
channel, so the route returns a document.

### Executive Decision Center: `GET /decision-center/briefing`

One read model over everything Prompts 33-50 record (Prompt 52,
[`52_EXECUTIVE_DECISION_CENTER_CONTRACT.md`](../52_EXECUTIVE_DECISION_CENTER_CONTRACT.md)):
business health, KPI movement, anomalies, risks, recommendations, pending
decisions, actions and outcomes.

404 unless `ENABLE_DECISION_CENTER=true` (on by default). Requires a
local account and `ADMIN_DASHBOARD_READ` (admin, auditor, manager).
**Never queries a monitored database, generates SQL, or calls an LLM** --
it reads only governed records this tenant already holds, through
repository functions that are themselves tenant-filtered.

Optional `database_id` narrows the scope; one this tenant may not use
answers 404, the same anti-enumeration convention every tenant-scoped
route here uses.

**There is deliberately no `profile` parameter.** Personalization is
resolved from the viewer's own server-side capabilities -- a viewer
cannot ask to be shown someone else's view. It reorders sections and caps
how many headlines each shows; it never changes a value, a truth level,
or which sections exist. Every section is present for every role.

The response carries `health`, `sections`, `chains`, `metric_definitions`,
`contributing_sources` and `unavailable_sources`.

Every headline carries its full drill-down inline, so opening one costs
no second request:

```json
{
  "headline_id": "finding:...",
  "kind": "kpi_movement",
  "title": "Net Revenue fell 36.0% in 2026-06",
  "severity": 73, "severity_tier": "high", "status": "open",
  "truth_level": "ai_inference",
  "provenance": {
    "source": "kpi_monitoring", "period": "2026-06",
    "metric_concept_key": "revenue", "confidence": 0.94,
    "limitations": ["one value excluded as noise"],
    "lineage_available": true, "metric_definition_available": true
  },
  "evidence": [
    {"label": "observed", "value": "...", "truth_level": "database_fact"},
    {"label": "attribution", "value": "...", "truth_level": "ai_inference"}
  ]
}
```

A provenance field the producing capability never recorded reads
`"not recorded"` rather than being omitted -- an omission reads as
"nothing to see" instead of "nobody wrote this down".

**Truth levels are carried, never assigned.** A health indicator is
always `database_fact` (a count); a recommendation or a KPI signal stays
`ai_inference`; a workflow's status is `database_fact`. An
`ai_inference` headline can never report a confidence of 1.0.

**No "healthy/amber/red" verdict is returned.** Health indicators carry
`direction` (which way is better) and nothing more: a threshold turning a
count into a colour is a business judgment this platform has not been
given.

`chains` is the acceptance chain -- signal, evidence, explanation,
recommendation, action, outcome. Each link is `present`, or absent **with
an `absent_reason`**. `link_type` distinguishes `declared` (a real
foreign key, e.g. `workflows.recommendation_id`) from `correlated` (two
records that merely share a subject, which is *not* evidence that one
followed from the other). **Nothing in this platform links a signal to a
recommendation**, so that stage is always at best `correlated`, and a
chain stopping there says so.

`unavailable_sources` lists capabilities that did not contribute and why,
including the compute-on-demand engines that persist no records of their
own.

### `GET /schema/tables`

Live table/column listing (metadata-only, no data queries) via the same
introspection the UI's sidebar schema browser uses. Requires auth if
`API_AUTH_TOKEN` is set.

```json
{"tables": [{"table_name": "DimCustomer", "columns": [{"name": "CustomerKey", "type": "INT", "nullable": false, "is_primary_key": true}, ...]}]}
```

### `POST /generate/confirm`

The human-approval confirmation step for media generation (`api/generation.py`)
— only meaningful when `ENABLE_MULTI_SOURCE_ROUTER` and
`ENABLE_MEDIA_GENERATION` are both on. A prior `/ask` response's
`generation_result.status == "pending_approval"` means the router picked
the "generation" source but nothing has been generated or charged yet
(`Settings.require_generation_approval`, default `true` — see
`SECURITY.md`'s "Media generation" section). This endpoint is what
actually calls the provider:

```json
// Request
{"question": "generate an image of monthly spend by category"}

// Response
{"answer": "Image generated successfully.", "status": "succeeded", "media_id": "a1b2c3...", "media_type": "image", "model": "seedream-4.5"}
```

`question` is typically the exact text from the proposal (possibly
hand-edited first, same as `/execute`'s SQL text can be) — the media kind
(image vs. video) is always re-inferred from it server-side, never
accepted from the caller. Fetch the actual bytes via `GET /media/{media_id}`.
Rate-limited per client IP (`API_ACTION_RATE_LIMIT_PER_MINUTE`) in addition
to the existing process-wide `MEDIA_GEN_RATE_LIMIT`. Requires auth if
`API_AUTH_TOKEN` is set.

### `POST /voice/transcribe`

Transcribes a recorded question to text (`api/voice.py`), locally via
`faster-whisper` — never routed through `POST /ask` itself, so the caller
is responsible for submitting the returned text through that endpoint
next (`agent.input_guard.check_input` applies there, identically to a
typed question). 404 when `ENABLE_VOICE_MODE` is off (on by default).
Multipart upload (`audio`, any format `faster-whisper`/`ffmpeg` can
decode), capped at `Settings.voice_max_upload_mb` (default 10MB) and
`Settings.voice_max_duration_seconds` (default 30s, checked before the
comparatively expensive transcription call). Rate-limited per client IP
(`API_ACTION_RATE_LIMIT_PER_MINUTE`).

```json
{"text": "what were total sales last quarter", "stt_duration_ms": 812.4}
```

### `POST /voice/synthesize`

Synthesizes text to speech (`api/voice.py`), locally via Piper — returns
raw `audio/wav` bytes, not JSON. 404 when `ENABLE_VOICE_MODE` is off.
`text` is capped at `Settings.max_question_length` (the same bound
`AskRequest.question` uses). Returns `503` if the configured Piper voice
model hasn't been downloaded yet (`python scripts/download_voice_model.py`
is a one-time setup step, separate from the app itself). Rate-limited per
client IP.

### `POST /search/media`

Direct media-library search (`api/media_search.py`), independent of the
conversational `/ask` flow — returns raw ranked hits with a templated
summary, not an LLM-composed answer (ask through `/ask` instead for that,
which routes through `agent.orchestrator.nodes.media_search_node`). 404
when `ENABLE_MEDIA_SEARCH` is off (off by default). Rate-limited per
client IP, sharing `API_ACTION_RATE_LIMIT_PER_MINUTE` with other
lightweight actions rather than a dedicated limiter.

```json
// Request
{"query": "the photo of the site inspection", "media_type": null}

// Response
{
  "answer": "Found 2 result(s).",
  "status": "succeeded",
  "hits": [
    {"media_id": "a1b2...", "media_type": "image", "caption": "...", "timestamp_start": null, "timestamp_end": null}
  ]
}
```

`media_type` is optional (`"image"`, `"video"`, or omitted/`null` for
both). A video hit's `timestamp_start`/`timestamp_end` mark the matched
scene-detected segment; fetch its representative keyframe (or an image
hit's original file) via `GET /media/library/{media_id}` below — a full
video clip is never streamed.

### `GET /media/library/{media_id}`

Streams one media-library asset's bytes (`api/media_library.py`) — the
original file for an image hit, or the representative keyframe thumbnail
for a video-segment hit. Distinct from `GET /media/{media_id}`
(`api/media.py`), which serves freshly *generated* media from an
in-memory, 100-entry cache — this route instead resolves `media_id`
against the persistent Chroma-backed media library and re-validates the
resolved path stays inside the configured library root before opening it.
404 when media search is disabled, `media_id` is unknown, or the
underlying file has moved/been deleted.

### AI analysis: analyst, supervisor and semantic intelligence (optional, off by default)

All routes below require an authenticated local account and return 404 while
their feature flag is off. Full design and limitations are in the Prompt 33/34/35
contract documents.

- **`POST /analyst/investigate`** (flag `ENABLE_DATA_ANALYST_AGENT`). Requires `ASK` and `EXECUTE_SQL`.
  Body: `{"question": str, "conversation_history": [...], "model": str | null}`.
  Returns `status`, `stop_reason`, `subquestions`, `evidence` (each with a `truth_level`),
  `recommendations` (always `ai_inference`), `open_items`, `trace`, `usage` and `report_markdown`.
  A tenant, roles or budget field in the body is a 422.
- **`POST /analyst/supervise`** (flag `ENABLE_MULTI_AGENT_SUPERVISOR`). Same request and identity rules.
  Returns `status`, `claims` (each with `agent`, `task_id`, `truth_level`, `grounded_in`),
  `conflicts` (agents and keys only, never values), `violation_count`, `open_items`, `trace`, `usage`
  and `report_markdown`.
- **`POST /semantic-intelligence/run?database_id=`** (flag `ENABLE_SEMANTIC_INTELLIGENCE`). Requires catalog review.
  Analyses one database's catalog and persists up to `SEMANTIC_INTELLIGENCE_MAX_FINDINGS` findings. Returns the
  counts (created, updated, unchanged, truncated) and the top findings.
- **`GET /semantic-intelligence/findings?database_id=&status=`**. The review queue, riskiest first.
- **`POST /semantic-intelligence/findings/{id}/decision`**. Body: `{"decision": "accept" | "dismiss", "note": str | null}`.
  Changes review state only. A second decision is a 409.
- **`POST /semantic-intelligence/findings/{id}/rollback`** (catalog manage). Body: `{"to_version": int}`.
- **`GET /semantic-intelligence/impact/{concept_key}?database_id=&proposed_source_tables=`**. What depends on a
  concept, and what a source-table change would affect.
- **`POST /semantic-intelligence/entries/{entry_id}/rollback`** (catalog manage). Body: `{"target_version": int}`.
  Creates a new draft from an older version. The published row is not touched.

Cross-tenant resources and databases a tenant may not use return 404, not 403.

### AI asset governance: lifecycle, inventory and validation

`api/governance.py` (`/governance/*`, all routes require a local account
and one of two RBAC permissions: `governance.manage`, admin tier, for
every mutating/release action, or `governance.review`, analyst tier, for
approve/reject and read).

**Lifecycle** (Prompt 44): `POST /governance/assets` creates a `draft`
version of one governed asset -- `model`, `provider`, `prompt`, `agent`,
`tool`, `retrieval_source` or `policy`. From there,
`POST /governance/assets/{id}/` + `submit-for-testing`, `test-results`,
`submit-for-evaluation`, `evaluation-results`, `approve`, `reject`,
`deploy`, `rollback`, `retire`, `usage` move it through
`draft -> testing -> evaluating -> approved -> deployed`, with
`rolled_back`/`rejected`/`retired` as terminal states. Reads:
`GET /governance/assets` (list, filterable by `asset_type`),
`GET /governance/assets/current`, `GET /governance/assets/{id}`,
`.../events` (the append-only audit trail), `.../usage-summary`.
`PATCH /governance/assets/{id}` edits a draft -- the one stage a version
is mutable in. Deploying atomically retires whichever version of the
same asset key was deployed before. Every version is tenant-scoped; a
cross-tenant id returns `404`, never `403`.

**Inventory** (Prompt 45): `GET /governance/inventory` returns this
platform's live, in-code artifacts -- prompt templates, multi-agent
specialist definitions and analytical policies -- each with its source
module/symbol, a content `fingerprint`, its declared safety invariants,
and its variables/tools/truth-level ceiling/policy rules as applicable.
Also returns `known_tools`, `known_models` and
`known_evaluation_suites`: the exact name sets validation checks a
candidate against, so a reviewer sees the valid values up front.
`truth_level` is always `"database_fact"` -- a deterministic observation
about the repository's own source, never a claim an artifact is
approved. **A prompt's template text is deliberately not returned**;
`fingerprint` is what a caller compares instead.

**Validation** (Prompt 45): `POST /governance/assets/validate` validates
a candidate `(asset_type, asset_key, config)` triple without persisting
anything, and returns `200` with the report either way -- a finding is
this route's successful result. The same validation is *enforced* at
four lifecycle gates (create-draft, edit-draft, submit-for-testing and
**deploy**), where a blocking violation is a `422` whose `detail` is the
full report. It applies to `prompt`, `agent` and `policy` only; the
other four asset types keep their original free-form `config` behavior.

Violation codes: `untyped_asset_type`, `spec_parse_error`,
`positional_placeholder`, `missing_variable_declaration`,
`unused_required_variable`, `unapproved_tool`,
`tool_privilege_escalation`, `truth_level_escalation`, `unknown_model`,
`unknown_evaluation_suite`, `dropped_safety_invariant`,
`unsafe_composition`, `injection_phrase_in_template`, `empty_policy`.
Each carries `severity` (`error` blocks, `warning` is reported) and a
`subject` naming the specific variable/tool/model/invariant involved.
One `error` makes the whole artifact invalid -- there is no partial
acceptance.

`config` shape per typed asset type (every field optional except the
first, unknown fields rejected):

- `prompt`: `template` (required), `variables`
  (`[{name, required, description}]`), `required_tools`,
  `compatible_models`, `evaluation_suites`, `safety_invariants` (from
  `untrusted_data_framing`, `no_write_statements`,
  `no_prompt_disclosure`, `no_fabrication`, `citation_required`,
  `governed_metric_precedence`).
- `agent`: `agent_name` (required), `kind` (required: `llm`/`data`/
  `engine`/`control`/`retrieval`), `allowed_tools`, `max_truth_level`,
  `cost_units`, `max_attempts`, `compatible_models`,
  `evaluation_suites`.
- `policy`: `policy_kind` (required), `rules`
  (`[{subject, operator, value}]` -- a typed triple, never an
  expression string), `applies_to`, `evaluation_suites`.

A successor version inherits whatever safety invariants its predecessor
declared **or** carried, so a change can add controls but never remove
them. See `CLAUDE.md`'s "Prompt/agent/policy registry" section and
`45_PROMPT_AGENT_REGISTRY_CONTRACT.md` for the full design, including
why a deployed prompt artifact is never loaded as a live system prompt.

### `GET /sources`

Which data sources exist, which are configured, and which this caller may
route to. Computed from the same descriptor table `agent/orchestrator/`
routes with and the same permission check `router_node` applies, so the
frontend never guesses from a role name and cannot disagree with the
router about what a source is.

```json
{
  "sources": [
    {
      "name": "policy",
      "kind": "policy",
      "description": "a separate, access-restricted collection ...",
      "capabilities": {
        "operations": ["ingest", "read", "search"],
        "supports_citations": true,
        "supports_pagination": false,
        "supports_incremental_sync": false,
        "has_side_effects": false,
        "preserves_source_acl": false
      },
      "configured": true,
      "authorized": false,
      "required_permission": "policy_rag_query",
      "per_request": false
    }
  ],
  "usable": ["sql"],
  "roles": ["viewer"]
}
```

`configured` and `authorized` are reported **separately** on purpose: they
are different failures with different remedies (an operator fixes one, an
administrator the other), and collapsing them would leave the UI unable to
say which. All registered sources are returned, not only the usable ones,
so a source can be shown as unavailable *with a reason* rather than
silently omitted.

`per_request: true` means availability depends on the individual request
rather than standing configuration (`attachments` is usable only when a
file is attached to that question), so such a source never appears in
`usable`.

**This endpoint is an affordance, not a boundary.** It changes no access
decision — `router_node` still filters the LLM's own source selection
through the permission table before any subgraph runs, and every
data-touching route re-checks. No secret, connection string or API key
appears in the response; the only configuration fact exposed is the
boolean `configured`. Design and invariants:
[`docs/architecture/source-connector-contract.md`](architecture/source-connector-contract.md).

### `POST /ask/stream`

The SSE counterpart of `POST /ask`. Same request body, same final
`AskResponse` -- it **calls** that handler rather than reimplementing it,
so model validation, rate limiting, admission control, the execution
envelope, governance and persistence all apply identically
(`api/streaming.py`).

What it adds is per-node progress while the run is still going, so a slow
multi-source question stops looking hung.

```
event: open
data: {"status":"running"}

event: progress
data: {"stage":"retrieve_schema","attempt":1,"duration_ms":12.5,"outcome":"ok","error_category":null}

event: progress
data: {"stage":"generate_sql","attempt":1,"duration_ms":903.2,"outcome":"ok","error_category":null}

event: result
data: { ...the same AskResponse POST /ask returns... }

event: done
data: {"dropped":0}
```

| Event | Meaning |
|---|---|
| `open` | accepted and started |
| `progress` | one node finished. **Metadata only** -- stage name, attempt, duration, coarse outcome, error *category*. Never SQL, never a row, never an error message: an event is emitted before the result gate has run, so its payload is deliberately narrow (`agent/progress.py`) |
| `error` | the run failed; `{status, detail}` carries the HTTP status this would have been |
| `result` | the complete `AskResponse` |
| `done` | terminal. `dropped` is how many progress events were discarded because the buffer filled, so a client can say its view is incomplete rather than silently miss steps |

`result` and `error` are mutually exclusive and `done` is always last.

**Errors after the stream opens arrive in-band, not as an HTTP status.**
Once SSE headers are sent the status is already `200`, so a rate-limit or
governance refusal comes back as an `error` event. A client must read the
stream, not just the status. Request-model validation still returns a real
`422`, because that happens before the stream opens.

**Progress is per-node, not per-token.** The model's own tokens are not
streamed: `agent/llm_client.py` makes one blocking call per step and the
generation/insight prompts are benchmark-pinned, so that is a separate
change. What streams is the pipeline's progress, which is what makes a
slow run legible.

### Asynchronous questions: `/questions/*`

`POST /ask` answers one question and blocks. These routes let **one
authenticated user** have many questions in flight at once: submission
returns a job id immediately and the question runs through the *same*
`run_orchestrated` entry point and the *same* `_ask_response_from_state`
serializer `/ask` uses — so the SQL validator, the twice-enforced row
cap, the query timeout, the restricted-column gate, result governance and
provenance all apply identically, because it is literally the same code.

Behaviour, limits and the security model:
[`docs/CONCURRENCY.md`](CONCURRENCY.md).

| Route | Purpose |
|---|---|
| `POST /questions` | Submit. `202` with a job id. |
| `GET /questions/{job_id}` | One job, with its result once it has one. |
| `GET /questions` | This caller's jobs, newest first. `?status=`, `?limit=`. |
| `POST /questions/{job_id}/cancel` | Cancel. |
| `GET /questions/stats` | Capacity counts. |

All five require authentication; submission additionally requires
`Permission.ASK`, the same permission `/ask` requires.

**Requires a real user account.** `none`/`static_token` auth modes
provide no distinct per-caller subject, so ownership cannot be enforced
and these routes return **403**. Putting every caller in one bucket would
let each read the others' results.

```bash
curl -X POST http://localhost:8000/questions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"question": "What were total sales last quarter?"}'
```

```json
{
  "job_id": "q_8f2c1d...",
  "status": "queued",
  "duplicate": false,
  "queued_for_caller": 1,
  "running_for_caller": 0,
  "max_concurrent_per_user": 100
}
```

Then poll `GET /questions/{job_id}`:

```json
{
  "job_id": "q_8f2c1d...",
  "status": "completed",
  "question": "What were total sales last quarter?",
  "agent_status": "succeeded",
  "attempts": 1,
  "queue_wait_seconds": 0.4,
  "run_seconds": 7.2,
  "result": { "...": "an AskResponse-shaped payload" },
  "sources_used": ["sql"],
  "model_used": "llama3.1:8b",
  "source_confidence": { "sql": 0.9, "documents": 0.3 },
  "error_message": null,
  "guardrail_reason": null
}
```

**Statuses** — `queued`, `running`, `completed`, `failed`, `cancelled`,
`timeout`, `blocked_by_guardrail`. The four unsuccessful ones are
deliberately distinct: `blocked_by_guardrail` means nothing
malfunctioned, and reporting it as a failure would make a correct refusal
indistinguishable from an outage. `agent_status` maps all of them onto
`AskResponse`'s frozen four-value vocabulary for a client that already
renders it.

**Over the limit queues; a full queue is a 429.** Exceeding
`MAX_CONCURRENT_QUESTIONS_PER_USER` is not an error — the question waits
and starts automatically. Only `QUESTION_QUEUE_MAX_DEPTH` (or its
per-user share) produces a `429`, which is genuine backpressure.

**Duplicate submissions are absorbed.** An identical resubmission
returns the original `job_id` with `"duplicate": true`. Pass
`idempotency_key` to make two *differently worded* submissions one
operation; it is hashed with the owner and tenant server-side, never
stored as given, so it cannot collide with another user's.

**A guardrail refusal is a job, not a 4xx.** The caller asked for a job
id and a status, so they get a terminal job with status
`blocked_by_guardrail` — one code path on the client, and an auditable
record.

**Ownership.** A job is readable, listable and cancellable only by its
owner. Another user's job id returns the **same 404 a nonexistent one
does**, never a distinguishable 403, so job ids cannot be enumerated.
Cancelling an already-finished job is a `409`.

**Results are re-governed on every read**, against the *reader's current*
roles — never served as captured. Roles change, and a result captured for
a caller holding `VIEW_RESTRICTED_COLUMNS` must not come back unredacted
once that role is gone. The same control `api/artifacts.py` and
`api/shares.py` apply on their own read-back surfaces.

**Nothing internal is exposed.** `model_used` is the model name
`AskResponse.model` already returns and `sources_used` the source names
the "Sources used" panel already shows. No provider name, key, routing
rule, raw prompt or system instruction appears in any of these
responses.

`GET /questions/stats` returns counts only. The global numbers are
included for any authenticated caller — they are what a client needs to
explain a queued question honestly ("the system is busy" versus "you are
at your own limit") — while `running_for_caller`/`queued_for_caller` are
this caller's own, so one user cannot read another's load.

### Other routes

`POST /execute` (SQL "Confirm and Run" equivalent), `POST /schema/refresh`,
`GET`/`POST`/`DELETE /documents`, `GET /documents/{id}/download`, and
`GET /media/{media_id}` (serving freshly *generated* media — see
`GET /media/library/{media_id}` above for the persistent-library
equivalent) also exist (`api/main.py`, `api/documents.py`, `api/media.py`)
— not yet given their own subsection here; see each module's own
docstrings for the authoritative contract in the meantime.

**`POST /execute` is the "Confirm and Run" path, and it re-validates.**
It runs the governed sequence in full — `validate_sql` →
`enforce_row_limit` → `qualify_table_schema` → `execute_readonly_sql` →
`govern_rows_for_caller` — against **the SQL in the request body**, so a
hand-edited statement is exactly as untrusted as a generated one and a
client cannot submit edited SQL that skips validation, the row cap, the
timeout or the result gate. `ExecuteResponse` carries six fields this
page did not previously name (`DOC-021`): `normalized_sql` (the
validated, row-limited statement that actually ran, `null` when
rejected or failed — **this, not the submitted text, is what executed**),
`column_types` and `column_semantics` (per-column inferred type and
semantic role, in result order, so a client's chart engine binds a real
measure to the y-axis rather than whichever numeric column came first —
see [`docs/CHART_AXIS_BINDING.md`](CHART_AXIS_BINDING.md)),
`chart_recommendation` and `visualization_spec` (a suggested chart type
with its reason, and the fuller deterministic specification), and
`cache_status` (the same signal as the `X-Cache` response header,
mirrored into the body for a client that cannot read headers). All six
are optional with defaults.

**Feedback** (`api/main.py`, both require `Permission.GOLDEN_EXAMPLE_WRITE`):
`POST /feedback/golden-example` records a human-approved (question, SQL)
pair for future few-shot retrieval — the same call the dashboard's
thumbs-up widget makes once a "Confirm and Run" result is confirmed
correct. `POST /feedback/message` is the general-purpose counterpart: a
like/dislike (plus an optional comment) on *any* assistant answer — SQL,
document/policy RAG, web, or media — stored separately
(`feedback/store.py`) and purely additive to the golden-example store
above, never a replacement for it. See `CLAUDE.md`'s "Golden-dataset
feedback loop" section.

`GET /metrics/performance` (`Permission.ADMIN_CONFIG`, same gate as
`POST /schema/refresh`) returns a live rollup of per-LangGraph-stage
timing across recent `/ask` requests, **scoped to the caller's own
tenant**, plus `llm_usage` — per-model token consumption. See
[`docs/OBSERVABILITY.md`](OBSERVABILITY.md) §3 for the full design and
`observability/metrics.py`'s own docstring for the
per-worker/resets-on-restart limits it deliberately does not claim to
solve.

Each `llm_usage` entry is one model: `model`, `calls`, `prompt_tokens`,
`completion_tokens`, `total_tokens`, sorted by total descending. The
counts are Ollama's own `prompt_eval_count`/`eval_count` as reported per
response, accumulated since process start.

```json
"llm_usage": [
  {"model": "llama3.1:8b", "calls": 42, "prompt_tokens": 51234,
   "completion_tokens": 3120, "total_tokens": 54354}
]
```

**There is no monetary field, deliberately.** The LLM runs locally via
Ollama, so this deployment has no per-token price to report, and a cost
column would be an invented number presented as an observed fact
(`CLAUDE.md` §4.4). `model_routing`'s `total_cost_units` (see
`/model-routing/*` above) is an abstract tier weight, not money, and is
a different fact with a different owner. Note also that `calls` counts
**LLM calls, not questions** — one question makes several (planning,
generation, review, insight) and a retry makes more — and that nothing
here is persisted, so it is not a billing or chargeback record.

`GET /platform-admin/metrics` (`Permission.PLATFORM_ADMIN`) returns the
same shape **merged across every tenant**; the tenant-scoped route above
never reveals another tenant's models, which is covered by a test.

**Local-account auth** (`api/identity_auth.py`, only reachable when
`LOCAL_AUTH_ENABLED=true` — 404s otherwise, not 503, see that router's own
docstring): `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`,
`POST /auth/logout`(`-all`), `GET`/`PATCH /auth/me`, `POST
/auth/change-password`, `POST /auth/forgot-password`, `POST
/auth/reset-password`, `POST /auth/verify-email`, `POST
/auth/resend-verification`, `GET /auth/sessions`, `DELETE
/auth/sessions/{id}`. See `docs/AUTHENTICATION.md` and
`docs/authentication-and-password-policy.md`.

**Google sign-in** (same router, additionally requires `GOOGLE_OAUTH_CLIENT_ID`
set — 404s otherwise): `GET /auth/google/nonce` (issues a single-use
sign-in nonce), `POST /auth/google` (verifies a Google ID token, signs in
or signs up), `GET`/`POST`/`DELETE /auth/google/link` (view/link/unlink a
Google identity on the caller's *own* authenticated account). See
`docs/AUTHENTICATION.md`'s "Google sign-in" section for the full token
-verification contract and identity-linking rules.

**Server-side chat history** (`api/chat_history.py`, same
`LOCAL_AUTH_ENABLED` gate): `GET`/`POST /conversations`, `GET`/`PATCH`/
`DELETE /conversations/{id}`, `GET`/`POST /conversations/{id}/messages`,
`GET /chat/search?q=`. Every route requires a local account and is
ownership-scoped to the caller — see `docs/chat-history-architecture.md`
and `docs/chat-history-search.md`.

**Secure conversation sharing** (`api/shares.py`, requires
`LOCAL_AUTH_ENABLED=true` and `ENABLE_CONVERSATION_SHARING=true`, the
latter on by default): owner-only management —
`POST`/`GET`/`PATCH /conversations/{id}/share`, `POST
/conversations/{id}/share/revoke`, `POST
/conversations/{id}/share/link/regenerate`, `POST
/conversations/{id}/share/members/invite`, `DELETE
/conversations/{id}/share/members/{memberId}` — plus a viewer surface
reachable **without authentication** for an "anyone with the link" share
(`SHARE_PUBLIC_LINKS_ENABLED`, off by default): `GET /share-view/{ref}`,
`GET /share-view/{ref}/attachments/{attachmentId}`, and `POST
/share-invitations/{token}/accept` (redeems an invitation for the caller's
own authenticated account). A shared conversation is always a read-only,
server-filtered snapshot — never a live query channel, and never a way to
reach `/ask`/`/execute` or any other AI/SQL route. See
`docs/SHARING_SECURITY.md` for the full RBAC/ABAC model, token lifecycle,
and cache/referrer protections.

## Auth: from a lightweight hook to real per-user identity, depending on configuration

`Settings.auth_mode` resolves to one of four values, in priority order —
see `api/auth.py::verify_api_key` and `docs/AUTHENTICATION.md` for the
full picture:

1. **`local`** (`LOCAL_AUTH_ENABLED=true`) — this app's own accounts
   (`identity/`): real login/registration, per-user sessions (rotating
   refresh tokens), and RBAC roles that feed into the same permission
   checks every route below already enforces. **This is real per-user
   identity and per-user data isolation** — chat history
   (`api/chat_history.py`) is strictly scoped to the authenticated
   caller's own `user_id`, never trusting a client-supplied value (see
   `docs/chat-history-architecture.md`). "Continue with Google"
   (`GOOGLE_OAUTH_CLIENT_ID` set) is a second way to *reach* this same
   mode — it produces the identical session a password login does, never
   a separate `auth_mode` value of its own.
2. **`oidc`** (`OIDC_ISSUER` set) — validates a JWT against any
   standard-compliant external identity provider. See
   `docs/AUTHENTICATION.md`.
3. **`static_token`** (`API_AUTH_TOKEN` set, `local`/`oidc` both unset) —
   the original lightweight hook: one shared bearer secret, checked with a
   constant-time comparison, granting a fixed admin-equivalent identity
   with **no per-user identity, no session, no chat-history persistence**
   (there's no stable per-caller `user_id` to attach a conversation to).
4. **`none`** (nothing configured, the default) — no auth at all,
   suitable for local/trusted-network use only.

`GET /health` never requires auth regardless of mode (health checks
typically need to be reachable by an orchestrator with no credential).

**For anything beyond local/trusted-network use, either configure `local`
or `oidc` auth, or put the API behind a real authenticating reverse proxy**
(e.g. `oauth2-proxy`, your platform's managed auth) — see
`docs/DEPLOYMENT.md`. Modes 3 and 4 above are still appropriate only for a
single-operator/trusted-network deployment, per `SECURITY.md`'s posture;
mode 1 or 2 is what a genuinely multi-user deployment should configure.

## Correlation IDs

Every request is bound to a correlation ID (from an incoming
`X-Correlation-ID` header, or a generated UUID) for the duration of the
request, echoed back in the response's `X-Correlation-ID` header. Every
`security.audit_log.log_security_event` call made during that request
(input rejections, validator safety violations, rate-limit trips,
sensitive-column blocks) includes it automatically — so a caller can hand
you a correlation ID and you can grep the audit log for exactly what
happened on that request, without this app needing per-user identity to do
it. See `security/audit_log.py`.

## What this is not

- Not a multi-tenant API in the "isolated customer workspaces" sense —
  every authenticated user shares the same configured business
  database(s) and schema/business-context retrieval index. Per-user
  identity, session management, RBAC, and per-user chat-history isolation
  *do* exist when `local`/`oidc` auth is configured (see above) — this
  project outgrew the "no per-user identity at all" characterization this
  section used to have as more of `identity/`/`agent/authz.py` was built;
  what's still true is that it has no concept of separate tenants/
  organizations sharing one deployment.
- Not a stable, versioned public API contract — it's young and scoped to
  this project's own needs; expect it to evolve alongside the agent.
- Not a replacement for reading `SECURITY.md` before pointing either
  interface (UI or API) at a real/sensitive database.

---

<a id="full-endpoint-index"></a>

## Full endpoint index

Generated from `app.openapi()` on **2026-10-30** — 187 paths, 212
operations. "Described in a section above?" is a literal check for the
`METHOD /path` pair appearing in this document's prose, so a `no` means
only that this page has no section for it, never that the operation is
unsupported or ungoverned: every one of them goes through the same
authorization, validation and result-governance layers, and
[`docs/uat-readiness/phase-4-authorization.md`](uat-readiness/phase-4-authorization.md)
derives its role × route matrix from the live application rather than
from this list.

Two operations are deliberately absent from `/openapi.json` and so from
this table: `GET /live` (the liveness probe, described under
[`GET /health`](#get-health) above) and the SPA catch-all
`GET /{full_path:path}` that serves `frontend/dist`. **That catch-all is
why a 200 from this app is never on its own evidence that a route
exists** — a nonexistent path returns the dashboard's `<!doctype html>`.

| Operation | Described in a section above? |
|---|---|
| `POST /analyst/investigate` | yes |
| `POST /analyst/supervise` | yes |
| `GET /artifacts` | no |
| `POST /artifacts` | no |
| `GET /artifacts/{artifact_id}` | no |
| `DELETE /artifacts/{artifact_id}` | no |
| `POST /artifacts/{artifact_id}/refresh` | no |
| `POST /ask` | yes |
| `POST /ask/stream` | yes |
| `GET /attachments/capabilities` | yes |
| `POST /attachments/upload` | yes |
| `DELETE /attachments/{attachment_id}` | yes |
| `POST /attachments/{attachment_id}/ai-edit` | yes |
| `POST /attachments/{attachment_id}/blur-region` | yes |
| `GET /attachments/{attachment_id}/detect-text-regions` | yes |
| `POST /attachments/{attachment_id}/extract-text` | yes |
| `POST /attachments/{attachment_id}/remove-text` | yes |
| `POST /attachments/{attachment_id}/resize` | yes |
| `POST /auth/change-password` | yes |
| `POST /auth/forgot-password` | yes |
| `POST /auth/google` | yes |
| `GET /auth/google/link` | no |
| `POST /auth/google/link` | no |
| `DELETE /auth/google/link` | yes |
| `GET /auth/google/nonce` | yes |
| `POST /auth/login` | yes |
| `POST /auth/logout` | yes |
| `POST /auth/logout-all` | no |
| `GET /auth/me` | no |
| `PATCH /auth/me` | yes |
| `GET /auth/me/avatar` | no |
| `POST /auth/me/avatar` | no |
| `DELETE /auth/me/avatar` | no |
| `POST /auth/refresh` | yes |
| `POST /auth/register` | yes |
| `POST /auth/resend-verification` | yes |
| `POST /auth/reset-password` | yes |
| `GET /auth/sessions` | yes |
| `DELETE /auth/sessions/{session_id}` | yes |
| `POST /auth/verify-email` | yes |
| `GET /bi/connectors` | no |
| `POST /bi/connectors` | no |
| `GET /bi/connectors/{connector_id}` | no |
| `DELETE /bi/connectors/{connector_id}` | no |
| `GET /bi/connectors/{connector_id}/export` | no |
| `POST /bi/connectors/{connector_id}/refresh` | no |
| `POST /briefing/generate` | yes |
| `GET /chat/search` | yes |
| `GET /conversations` | no |
| `POST /conversations` | yes |
| `GET /conversations/{conversation_id}` | no |
| `PATCH /conversations/{conversation_id}` | no |
| `DELETE /conversations/{conversation_id}` | yes |
| `GET /conversations/{conversation_id}/messages` | no |
| `POST /conversations/{conversation_id}/messages` | yes |
| `GET /conversations/{conversation_id}/share` | no |
| `POST /conversations/{conversation_id}/share` | no |
| `PATCH /conversations/{conversation_id}/share` | yes |
| `POST /conversations/{conversation_id}/share/link/regenerate` | yes |
| `POST /conversations/{conversation_id}/share/members/invite` | yes |
| `DELETE /conversations/{conversation_id}/share/members/{member_id}` | yes |
| `POST /conversations/{conversation_id}/share/revoke` | yes |
| `POST /data-quality/lineage` | no |
| `POST /data-quality/scan` | no |
| `GET /decision-center/briefing` | yes |
| `GET /documents` | no |
| `POST /documents` | no |
| `DELETE /documents/{document_id}` | no |
| `GET /documents/{document_id}/download` | yes |
| `GET /eval-platform/runs` | no |
| `POST /eval-platform/runs/component-benchmark` | no |
| `POST /eval-platform/runs/report` | no |
| `GET /eval-platform/runs/{run_id}` | no |
| `GET /eval-platform/runs/{run_id}/case-reviews` | no |
| `POST /eval-platform/runs/{run_id}/case-reviews` | no |
| `POST /eval-platform/runs/{run_id}/release-gate` | no |
| `POST /execute` | yes |
| `POST /feedback/golden-example` | yes |
| `POST /feedback/message` | yes |
| `POST /generate/confirm` | yes |
| `GET /governance/assets` | yes |
| `POST /governance/assets` | yes |
| `GET /governance/assets/current` | yes |
| `POST /governance/assets/validate` | yes |
| `GET /governance/assets/{version_id}` | yes |
| `PATCH /governance/assets/{version_id}` | yes |
| `POST /governance/assets/{version_id}/approve` | no |
| `POST /governance/assets/{version_id}/deploy` | no |
| `POST /governance/assets/{version_id}/evaluation-results` | no |
| `GET /governance/assets/{version_id}/events` | no |
| `POST /governance/assets/{version_id}/reject` | no |
| `POST /governance/assets/{version_id}/retire` | no |
| `POST /governance/assets/{version_id}/rollback` | no |
| `POST /governance/assets/{version_id}/submit-for-evaluation` | no |
| `POST /governance/assets/{version_id}/submit-for-testing` | no |
| `POST /governance/assets/{version_id}/test-results` | no |
| `POST /governance/assets/{version_id}/usage` | no |
| `GET /governance/assets/{version_id}/usage-summary` | no |
| `GET /governance/inventory` | yes |
| `GET /health` | yes |
| `GET /kpi-monitoring/findings` | yes |
| `POST /kpi-monitoring/scan` | yes |
| `POST /lineage/graph` | no |
| `POST /lineage/stale-check` | no |
| `POST /lineage/trace` | no |
| `GET /media/library/{media_id}` | yes |
| `GET /media/{media_id}` | yes |
| `GET /metrics/harness` | no |
| `GET /metrics/performance` | yes |
| `GET /model-routing/metrics` | yes |
| `GET /model-routing/policy` | yes |
| `POST /model-routing/preview` | yes |
| `GET /models` | yes |
| `GET /navigation` | no |
| `POST /navigation/access-denied` | no |
| `GET /onboarding/jobs` | no |
| `POST /onboarding/jobs` | no |
| `GET /onboarding/jobs/{job_id}` | no |
| `GET /onboarding/jobs/{job_id}/artifacts` | no |
| `POST /onboarding/jobs/{job_id}/cancel` | no |
| `POST /onboarding/jobs/{job_id}/discover` | no |
| `POST /onboarding/jobs/{job_id}/publish` | no |
| `POST /onboarding/jobs/{job_id}/register-for-chat` | no |
| `POST /onboarding/jobs/{job_id}/retry` | no |
| `GET /onboarding/jobs/{job_id}/review-items` | no |
| `POST /onboarding/jobs/{job_id}/review-items/{item_id}/decide` | no |
| `POST /optimization/solve` | no |
| `POST /optimization/validate` | no |
| `GET /platform-admin/audit-logs` | no |
| `GET /platform-admin/config-status` | no |
| `GET /platform-admin/databases` | no |
| `GET /platform-admin/jobs` | no |
| `GET /platform-admin/metrics` | yes |
| `GET /platform-admin/roles` | no |
| `GET /platform-admin/security-events` | no |
| `GET /platform-admin/semantic-review-queue` | no |
| `GET /platform-admin/tenants` | no |
| `POST /platform-admin/tenants` | no |
| `POST /platform-admin/tenants/{tenant_id}/status` | no |
| `GET /platform-admin/users` | no |
| `POST /platform-admin/users/{user_id}/roles` | no |
| `DELETE /platform-admin/users/{user_id}/roles/{role_name}` | no |
| `POST /predictive/drift` | no |
| `POST /predictive/train` | no |
| `GET /questions` | yes |
| `POST /questions` | yes |
| `GET /questions/stats` | yes |
| `GET /questions/{job_id}` | yes |
| `POST /questions/{job_id}/cancel` | yes |
| `GET /recommendations` | no |
| `GET /recommendations/metrics` | no |
| `GET /recommendations/{record_id}` | no |
| `GET /recommendations/{record_id}/events` | no |
| `POST /recommendations/{record_id}/expire` | no |
| `POST /recommendations/{record_id}/feedback` | no |
| `POST /recommendations/{record_id}/notes` | no |
| `POST /recommendations/{record_id}/owner` | no |
| `POST /recommendations/{record_id}/resolve` | no |
| `POST /scenarios/run` | no |
| `POST /scenarios/validate` | no |
| `POST /schema/refresh` | yes |
| `GET /schema/tables` | yes |
| `GET /schema-drift/findings` | no |
| `GET /schema-drift/findings/{finding_id}` | no |
| `POST /schema-drift/scan` | no |
| `POST /search/media` | yes |
| `GET /semantic-catalog/entries` | no |
| `POST /semantic-catalog/entries` | no |
| `GET /semantic-catalog/entries/concept/{concept_key}/versions` | no |
| `GET /semantic-catalog/entries/{entry_id}` | no |
| `PATCH /semantic-catalog/entries/{entry_id}` | no |
| `POST /semantic-catalog/entries/{entry_id}/publish` | no |
| `POST /semantic-catalog/entries/{entry_id}/request-changes` | no |
| `POST /semantic-catalog/entries/{entry_id}/review` | no |
| `POST /semantic-intelligence/entries/{entry_id}/rollback` | yes |
| `GET /semantic-intelligence/findings` | yes |
| `POST /semantic-intelligence/findings/{finding_id}/decision` | yes |
| `POST /semantic-intelligence/findings/{finding_id}/rollback` | yes |
| `GET /semantic-intelligence/impact/{concept_key}` | yes |
| `POST /semantic-intelligence/run` | yes |
| `POST /share-invitations/{opaque_token}/accept` | yes |
| `GET /share-view/{ref}` | yes |
| `GET /share-view/{ref}/attachments/{attachment_id}` | yes |
| `GET /sources` | yes |
| `GET /tenant-admin/audit` | no |
| `GET /tenant-admin/databases` | no |
| `POST /tenant-admin/databases/refresh` | no |
| `GET /tenant-admin/evaluation` | no |
| `GET /tenant-admin/golden-questions` | no |
| `GET /tenant-admin/pending-reviews` | no |
| `GET /tenant-admin/profile` | no |
| `GET /tenant-admin/roles` | no |
| `GET /tenant-admin/semantic-catalog-status` | no |
| `GET /tenant-admin/users` | no |
| `POST /tenant-admin/users/{user_id}/roles` | no |
| `DELETE /tenant-admin/users/{user_id}/roles/{role_name}` | no |
| `POST /voice/synthesize` | yes |
| `POST /voice/transcribe` | yes |
| `GET /workflows` | no |
| `POST /workflows` | no |
| `GET /workflows/{workflow_id}` | no |
| `POST /workflows/{workflow_id}/approve` | no |
| `POST /workflows/{workflow_id}/assign` | no |
| `POST /workflows/{workflow_id}/comments` | no |
| `POST /workflows/{workflow_id}/complete` | no |
| `POST /workflows/{workflow_id}/due-date` | no |
| `GET /workflows/{workflow_id}/events` | no |
| `POST /workflows/{workflow_id}/expire` | no |
| `POST /workflows/{workflow_id}/external-reference` | no |
| `POST /workflows/{workflow_id}/reject` | no |
| `POST /workflows/{workflow_id}/start` | no |
| `POST /workflows/{workflow_id}/submit-for-approval` | no |
