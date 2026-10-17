# Observability

Phase 3 Step 4: AUDIT → COMPARE → GAP ANALYSIS → IMPLEMENT ONLY WHAT WAS MISSING. This
document describes what this application actually emits today (logs, correlation IDs,
per-stage timing, health checks) verified against the running code, not aspirational.
**No commercial/hosted monitoring dependency exists or is proposed** (no Datadog, New
Relic, Prometheus/Grafana stack) — consistent with this project's "local-first,
single-user/local-dev-oriented" identity (`README.md`'s own Limitations section);
everything below is either the Python standard library's `logging` module or a
standalone script that reads its output.

## 1. Correlation ID propagation

**API layer.** `api/main.py::_correlation_id_middleware` binds one correlation ID per
HTTP request — the caller's own `X-Correlation-ID` header if supplied, else a fresh
`uuid4()` — into a `contextvars.ContextVar` (`security/audit_log.py`), and echoes it
back on every response's `X-Correlation-ID` header, success or failure. `contextvars`
(not `threading.local`) is required specifically because Starlette dispatches a sync
endpoint to a worker thread via `anyio.to_thread.run_sync`, which copies the calling
context into that thread; a thread-local would not see the value the middleware set in
the event loop's own async context.

**Into every log line, not just audit events (Phase 3 fix).** Before this pass,
the correlation ID was readable by exactly one thing: `security.audit_log
.log_security_event`, which explicitly reads the contextvar. Every *ordinary*
`logging.getLogger(__name__)` call anywhere else — `agent/nodes.py`'s eleven node
functions, `agent/orchestrator/nodes.py`, `db/`, `rag/`, `media_gen/`, `search/` — used
a plain per-module logger with no correlation ID at all. That made it impossible to
grep one live request's full trace across the API → LangGraph → RAG/SQL/external-call
boundary from ordinary application logs, only from the narrower `security.audit`
stream — a real, named gap, not a hypothetical one.

Fixed with one small, additive piece: `security.audit_log.CorrelationIdLogFilter`, a
`logging.Filter` that stamps `record.correlation_id` (the current contextvar value, or
`"-"` outside a request) onto every record. `config.settings.configure_logging`
attaches it to every handler on the root logger and adds `correlation_id=%(correlation_id)s`
to the format string. Because `logging.Filter` runs before formatting on every record
that reaches a handler, **every existing call site in every module gets this for free**
— zero changes needed to `agent/nodes.py`, `rag/`, `db/`, etc. Verified by
`tests/test_audit_log.py::TestCorrelationIdLogFilter` (including an end-to-end test
against a plain, non-`security.audit` module logger).

**LangGraph → RAG/SQL/external calls.** Once attached at the root logger, this covers
every stage a question passes through in one process, since LangGraph nodes,
`db/execution.py`, `rag/graph.py`, `media_gen/`, and `search/web_search.py` all log via
plain module-level `logging.getLogger(__name__)` calls that flow to the same root
handler — none of them run in a separate process or a detached thread that would lose
the contextvar. The one exception: a background `ThreadPoolExecutor` worker (e.g.
`scripts/build_media_index.py`'s `MEDIA_INGEST_WORKERS`) does **not** inherit the
submitting thread's contextvar by default (unlike `anyio.to_thread.run_sync`, plain
`concurrent.futures.ThreadPoolExecutor.submit` does not copy `contextvars.copy_context()`
automatically) — logs from that pool correctly show `correlation_id=-`, since batch
ingestion isn't a per-request operation in the first place and has no single request to
attribute to.

**What already existed and was correct, not touched:** the middleware itself, the
response header, and `log_security_event`'s own correlation-ID inclusion — Phase 2
built all of that already; this pass only extended the *same* mechanism to ordinary
logs instead of duplicating a second one.

## 2. Structured logging

**Security/audit events** (`security/audit_log.py::log_security_event`) are the one
genuinely structured stream: a stable `event_type`, a `severity` (`info`/`warning`/
`critical`), a human `detail`, and arbitrary `**context` key=value pairs, rendered as
one line: `event=<type> severity=<level> detail='...' correlation_id=<id> key=value ...`.
The full event taxonomy actually emitted today (verified by grep, not aspirational):

| event_type | Where | Meaning |
|---|---|---|
| `input_rejected` | `agent/nodes.py` | `sanitize_input_node` blocked a question (length/injection pattern) |
| `conversation_history_injection_detected` | `agent/input_guard.py` | A prior-turn history entry looked like an injection attempt (logged, not rejected) |
| `possible_rag_poisoning` | `agent/nodes.py` | Retrieved schema/golden-example content looked adversarial |
| `rate_limit_tripped` | `agent/nodes.py` | Per-session LLM-call rate limit hit |
| `sql_safety_violation` | `agent/nodes.py` | `sql_validator` rejected generated/edited SQL |
| `sensitive_column_blocked` | `agent/nodes.py` | A restricted column was referenced without `VIEW_RESTRICTED_COLUMNS` |
| `auth_failed` | `api/auth.py` | Any authentication rejection (missing/invalid/expired credential) |
| `oidc_token_rejected` | `security/oidc.py` | JWT-specific rejection reason (bad signature, issuer, audience, expiry) |
| `authz_denied` | `api/authz.py`, `api/documents.py` | A permission check failed (RBAC or sensitive-document gate) |
| `session_expensive_source_limit_tripped` | `agent/orchestrator/nodes.py` | Per-session cost ceiling hit (generation/web/policy) |
| `orchestrator_source_denied` | `agent/orchestrator/nodes.py` | Router picked a source the caller's role can't use |
| `generation_routed` / `_rejected` / `_rate_limited` / `_provider_failed` / `_download_failed` / `_succeeded` / `_pending_approval` | `agent/orchestrator/nodes.py` | Media generation lifecycle events |
| `content_moderation_rejected` / `_soft_flagged` | `moderation/gate.py` | Ingestion-time moderation outcomes |

This list is exactly what `scripts/monitoring_summary.py --log-file <captured output>`
already parses and tallies (`_AUDIT_EVENT_RE`) — the no-commercial-dependency
"monitoring" story for this app: redirect stdout/stderr to a file (or capture a
container's logs) and run that script.

**Ordinary application logs** (everything else — `[timing] stage=... duration_ms=...`,
`[retrieve_schema] question=...`, `[generate_sql] attempt N: generated SQL: ...`, etc.)
are prose, not key=value pairs, though each carries a consistent `[node_name]` prefix
making them greppable. **Named limitation, not fixed in this pass**: these are not
JSON and a log aggregator (Splunk, ELK, Loki) would need a grok/regex pattern, not
out-of-the-box JSON parsing, to structure them. Migrating every `logger.info`/`.warning`
call site across the codebase to a JSON formatter would be a sweeping, high-blast-radius
change (every one of ~150 call sites, every existing log-scraping test assertion in
`tests/`) for a single-user/local-dev-oriented app that has no log aggregator deployed
today — out of scope for this pass per "no large rewrites unless absolutely necessary."
If this app is ever deployed somewhere a log pipeline expects JSON, wrapping
`logging.Formatter` with a JSON-emitting one in `configure_logging` (the fields are
already there on each `LogRecord` — level, name, message, and now `correlation_id`) is
a small, contained follow-up, not a rewrite.

## 3. Per-stage latency instrumentation (already existed, verified working)

`agent/nodes.py::_timed_node` wraps every one of the graph's nodes (eighteen as of
Prompt 17's `generate_recommendations` — see `CLAUDE.md`'s "Self-correcting retry
loop" section for the current count and full list), timing each call (including every
retry attempt) and both (a) logging `[timing] stage=<name> attempt=<n>
duration_ms=<ms>` at INFO, and (b) appending a `StageTiming` entry to
`AgentState["stage_timings"]`, which accumulates into `stage_timings_ms` in the
benchmark's own result records. This is what `docs/PERFORMANCE_BASELINE.md`'s entire
per-stage breakdown was built from — real, already-wired instrumentation, not something
this pass had to add. Combined with fix #1 above, every `[timing]` log line now also
carries the request's `correlation_id`, so an operator can now reconstruct one live
request's full per-stage latency breakdown directly from logs (`grep correlation_id=<id>
... | grep '\[timing\]'`) — previously only possible by re-running the benchmark
harness, which captures the same data into a JSON file instead of relying on log
scraping.

**Prompt 23 (observability, evaluation & reliability) added a second, additive log
line per stage call: `[trace] stage=<name> attempt=<n> duration_ms=<ms> <fields>`** —
the literal mechanism behind "failures can be attributed to a specific stage." Never
replaces `[timing]` (kept byte-for-byte unchanged, since `scripts/profile_pipeline.py`
parses it) — a tool that already greps for `[timing]` is unaffected. `<fields>` is
built from whatever's actually meaningful for that particular call, each one read from
already-existing, well-known `AgentState` keys with zero changes needed to any
individual node's own return contract (the same "cross-cutting, no node needs to know
this exists" principle `stage_timings` itself established):

- `status` — the state's own `status` field after this node ran (e.g. `succeeded`,
  `retrieving_schema`, `failed`).
- `error_category` — `AgentState["last_error_category"]`, when this attempt set one
  (e.g. `missing_reference`, `database_busy`, `metric_definition_not_used`).
- `model` — `AgentState["selected_model"]`, once resolved (see "Configurable Ollama
  model selection" in `CLAUDE.md`) — absent on every stage before schema retrieval.
- `db_provider` — `AgentState["selected_database"]` (the configured connection *name*,
  never a host/credential), once resolved.
- `query_fingerprint` — a 16-character SHA-256 prefix of `AgentState["sql"]`
  (`agent.nodes._query_fingerprint`), never the SQL text itself or anything derived
  from a literal value in it — lets an operator see "did the SQL change between
  attempt 1 and attempt 2" without it ever being logged in a form that could leak a
  literal.
- `result_size` — `AgentState["row_count"]` (a count, never the rows themselves),
  present only after a successful `execute_sql`.

Every field is entirely omitted (never rendered as a literal `None`) when not
meaningful for that stage/outcome — e.g. no `[trace]` field at all appears before a
database or model has been selected. `correlation_id`/`tenant_id` are **not** repeated
inside `<fields>` — both are already stamped onto *every* log line, this one included,
by the root-logger filters described in §1/§1a below.

### 3a. Tenant propagation into every log line (Prompt 23)

The identical gap §1 already closed for `correlation_id`, closed for `tenant_id`:
before this, a request's tenant was only visible on `security.audit` events
(`log_security_event` reads `security.audit_log._tenant_id` explicitly) — every
*ordinary* `logger.info(...)` call (the `[timing]`/`[trace]` lines above included)
carried a correlation ID but no tenant. `security.audit_log.TenantIdLogFilter`
(attached to the root logger by `config.settings.configure_logging`, alongside
`CorrelationIdLogFilter`) stamps `record.tenant_id` on every record the identical way,
and the text/JSON log formats both render it (`tenant_id=%(tenant_id)s` in the text
format; a `"tenant_id"` key, `null` when unbound, in the JSON format) — zero changes
needed to any individual call site.

`observability/llm_timing_capture.py::capture_llm_timings` separately captures
Ollama's own reported `total_ms`/`load_ms`/`prompt_eval_ms`/`generation_ms` and token
counts by attaching a `logging.Handler` to `agent.llm_client`'s logger for the scope of
one question — this is what distinguishes model-load time from prompt-processing time
from generation time (wall-clock alone can't, per that module's own docstring).

**`eval/runner.py` is its only caller, and it must stay that way — it is not
usable on the request path.** Corrected 2026-10-17: that module's own docstring
claims it "backs the API/observability layer's per-request `LlmCallTiming` list",
and nothing in `api/` imports it, so the claim is stale. It is also unsafe
here. It works by attaching a handler to the `agent.llm_client` logger **and
forcing that logger's level**, both process-wide for the duration of the `with`
block. Under concurrent `/ask` requests every active capture would see every
*other* request's timing lines, so tokens would be **cross-attributed between
tenants**, and the save/restore of the logger level would race. That is correct
for the single-threaded benchmark runner it was written for and a
tenant-isolation defect if reused live — which is why the token rollup in §3c
below records at the call funnel instead of by log scraping.

### 3b. Per-route model-routing metrics (Prompt 47)

When `ENABLE_MODEL_ROUTING` is on, `model_routing/metrics.py` keeps a
second, separate rollup keyed by **route** -- one `(task, model)` pair, the
granularity a routing decision is actually made at -- read back by
`GET /model-routing/metrics` (admin-only, like `GET /metrics/performance`).

It is a sibling of `observability/metrics.py`, not a new dimension on it, on
purpose: `PerformanceMetrics` aggregates per LangGraph *stage* per *tenant*
and knows nothing about which model served a call, so adding `(task, model)`
keys there would change the shape `GET /metrics/performance` has returned
since Prompt 22 for every existing consumer, for data only this feature
produces. It copies that module's disclosed posture rather than inventing a
better one, because the same constraints apply: **single-process,
in-memory, resets on restart, bounded by sample count**. A multi-worker
deployment has one rollup per worker.

Per route it tracks `calls`, `successes`, `availability_failures`,
`quality_failures`, `quality_failure_rate`, `fallback_uses`,
mean/p50/p95/max latency and `cost_units`; process-wide it tracks
`decisions`, `escalations`, `de_escalations`, `decisions_by_reason`,
`budget_denials`, `breaker_denials` and `total_cost_units`.

Two things it deliberately does not claim:

- **`quality_failure_rate` is not an accuracy score.** It is the share of
  judged calls this application's own deterministic parsers/validators
  rejected (a malformed SQL block, an unreadable plan). A perfectly
  parseable answer can still be wrong. Benchmarked accuracy lives in
  `eval/` and, for a governed model version, in that version's own
  operator-recorded `evaluation_results` -- never synthesized from these
  counters. An off-topic refusal is recorded as a *success*, since that is
  the model doing its job.
- **`cost_units` are ordinal compute units, never money.** Every routable
  model runs locally through Ollama, so there is no per-token price to
  report.

Routing also emits one `INFO` line per decision that actually moved off the
configured default (`[model_routing] task=... model=... (baseline=...)
reason=... tier=... complexity=... risk=... evaluation_backed=...`). It
carries no question text, no SQL and no answer -- only task names, model
ids and the decision's own reason, so it is safe at `INFO` in a way the
free-text lines named in section 4 below are not. A decision that changed
nothing is counted in the rollup but not logged, to keep the common case
quiet.

### 3c. Per-model token consumption rollup (2026-10-17)

`agent/llm_client.py` had read Ollama's own `prompt_eval_count` and
`eval_count` on every call for a long time and then thrown them away into a
log line. An operator could see *which* model a request was routed to
(§3b) and how long each LangGraph stage took (§3), but nothing anywhere
said what the models actually **consumed**.
`observability.metrics.PerformanceMetrics.record_llm_call` now accumulates
them, and `llm_usage` on the snapshot reports, per model, `calls`,
`prompt_tokens`, `completion_tokens` and `total_tokens`, sorted by total
descending. It is read back by the two **existing** metrics routes — `GET
/metrics/performance` (the caller's own tenant) and `GET
/platform-admin/metrics` (merged across tenants) — and shown on the two
admin panels that already displayed latency. No new route, no new
permission.

**Where it is captured, and why there.** `_log_ollama_timing` is the single
point all seven of that module's `client.chat` calls already funnel
through, which is why the circuit breaker's success signal is there too.
The rollup goes in the same place for the same reason: **a future eighth
call site is counted without anyone remembering to add it.** A structural
test parses the module's own AST and asserts
`len(client.chat calls) == len(_log_ollama_timing calls)`, which is the one
failure mode no behavioural test of the existing seven would catch. The
model name is read from the *response* rather than threaded in from the
call sites, so not one of the seven changed — and it is the more correct
source, because the name recorded is what the server actually served,
which can differ from what this app asked for (an alias, or a per-request
`AskRequest.model` override).

**Tenant partitioning**, like latency and the status mix, via
`security.audit_log`'s existing `_tenant_id` contextvar
(`get_audit_tenant_id()`), already bound by `run_agent`/`run_orchestrated`
on the **worker side** of `/ask`'s thread-pool boundary — which is
precisely where a contextvar survives. Token counts measure a tenant's own
usage volume, and this codebase has no platform-admin-versus-tenant-admin
split inside the tenant-scoped route, so admin-gating alone would let one
tenant's admin read another's numbers. A test asserts over HTTP that it
does not.

**No monetary figure, deliberately.** Every model runs locally through
Ollama, so there is no per-token price to report; a cost column would be an
invented number presented as an observed fact (`CLAUDE.md` §4.4). Three
tests pin its absence — on the snapshot's keys, on the HTTP response's
keys, and on the rendered panel. This is the same posture §3b's
`cost_units` takes, and the two rollups own different facts (§4.8):
`model_routing` owns "which model was chosen and did the route work", this
owns "what the models consumed".

Stated limits, all of them the disclosed posture every metric store in this
codebase already carries:

- **Cumulative per worker process, and resets on restart.** Not windowed,
  like the existing cache counters and for the same reason. A multi-worker
  deployment has one rollup per worker, so these are *not* deployment-wide
  totals. Both admin panels say so in their own prose.
- **`calls` counts LLM calls, not questions.** One question makes several
  (planning, generation, review, insight) and a retry makes more.
- **Nothing is persisted**, so this is not a billing or chargeback record
  and must not be cited as one.
- **Per model and per tenant only** — no per-question or per-user
  attribution.
- **It depends on the provider reporting counts.** An older Ollama server
  that omits them yields an empty rollup, deliberately indistinguishable
  from "no calls yet": a response carrying no counts records nothing rather
  than a zero, because "nothing measured" must not read as "measured zero".
- Distinct model names are **bounded** and LRU-evicted (`max_models`,
  default 50), the same defence-in-depth posture as the tenant-window
  bound.

Being an observability aid it **fails open** (`CLAUDE.md` §4.2): an
unusable value costs the counter, never the request. Both token counts are
coerced *before* the window is touched, so a non-numeric or negative count
records nothing at all rather than leaving a phantom zero-token call behind
that a reader could not tell apart from a real call that consumed nothing.

## 4. What's never logged

**Secrets.** `security/redaction.py::redact_secrets` scrubs a configured DB password
(exact-value match) and any connection-string-shaped `password=`/`pwd=`/`://user:pass@`
text (regex fallback) out of any driver/Chroma exception text before it reaches a log
or an API response — applied at every site that surfaces raw third-party exception text
(`db/connection.py`'s connection-test failures, `agent/nodes.py::execute_sql_node`,
`embeddings/retriever.py`, and `GET /health`, the one unauthenticated endpoint, where a
leak would be public rather than merely internal). `.env`/API tokens/OIDC secrets are
`pydantic.SecretStr` (`security/secrets.py`) — `str()`/`repr()`/`%r`-formatting all
render `**********`; a call site needs `.get_secret_value()` explicitly to get the
real value, so an accidental `logger.info("token=%s", settings.api_auth_token)` is
already safe by construction, not by discipline alone.

**Query result content.** `observability/redaction.py::summarize_result_for_log` turns
an executed query's result into row/column *counts* only, never cell values — this is
the enforcement mechanism behind `CLAUDE.md`'s coding-standard promise ("Never log ...
full result rows"). `Settings.log_redaction_level` (`"standard"` default / `"strict"`)
additionally controls whether *column names* are included, since a column name itself
can be sensitive in some schemas (`ssn`, `salary`, `date_of_birth`) even with no row
data logged — `"strict"` drops those too, leaving only counts.

**Named limitation, disclosed rather than hidden: free-text question/SQL/insight
content is logged in full at INFO**, unredacted (`agent/nodes.py`'s
`[retrieve_schema] question=%r`, `[generate_sql] ... generated SQL: %s`,
`[generate_insight] generated: %r`, and similar). This is intentional for the stated
debuggability goal (`CLAUDE.md`: "the terminal shows the agent's reasoning steps
live") and none of it is a *credential* — but nothing prevents a user from typing
incidental PII into a free-text question (an email address, a name), and that text
will appear in plaintext logs exactly as typed. No PII-detection/scrubbing exists for
this, and building a reliable one is out of scope for this pass (a regex-based PII
filter would be unreliable enough to give false confidence, and a real NLP-based one is
a separate, much larger project). This is a real, standing tradeoff — teams deploying
this somewhere query text may contain regulated PII should either lower `LOG_LEVEL`
above `INFO` for `agent.nodes`/`agent.orchestrator.nodes` in that environment, or treat
the log destination itself as within the same compliance boundary as the database being
queried.

## 5. Health / readiness

`GET /health` (unauthenticated, by design — needs to be reachable by an orchestrator
with no API key) performs a real, non-cached reachability check of every configured
database (`test_connection` — a live `SELECT`-equivalent round trip) plus its Chroma
schema-index population, and Ollama (`.list()`), returning HTTP 200 only when every
component is reachable, 503 (`status: "degraded"`) otherwise — verified already correct
in Phase 1/2, unchanged this pass. Any leaked exception text in a component's `detail`
field is redacted (see §4) since this is the one endpoint where that leak would be
public.

## 6. Known gaps, named rather than silently left

- **No metrics/tracing backend** (Prometheus counters/histograms, OpenTelemetry spans).
  Deliberate, not an oversight — this app has no commercial monitoring dependency by
  design, and `_timed_node`'s log-based per-stage timing (§3) plus the benchmark
  harness already answer "where does latency go" without one. If this is ever deployed
  at a scale where dashboards/alerting on live traffic (not just periodic benchmark
  runs) are needed, that's the point to introduce OpenTelemetry — a real, larger
  addition, not attempted here.
- **Resolved, no longer a gap (`Settings.log_format="json"`, enterprise scalability/
  security assessment, 2026-09-27):** ordinary logs can render as one JSON object per
  line for a real log-aggregation pipeline, opt-in, with the original prose/key=value
  format kept as the unchanged default — see `config.settings._JsonLogFormatter`.
- **Free-text question/SQL/insight content is unredacted in logs** (§4) — a disclosed,
  accepted tradeoff for debuggability, not a defect.
- **The `security.audit` log stream is not tamper-evident** — already flagged as a
  Phase 2 P1 item (`docs/PHASE2_FINAL_REPORT.md`); unchanged by this pass, since making
  an append-only/signed audit trail is a security-architecture decision (a dedicated
  write-once store, or forwarding to an external SIEM) rather than an observability
  fix, and remains open for a future pass.
- **uvicorn's own access log (method/path/status/duration per HTTP request) does not
  carry this app's `correlation_id`** — it's emitted by uvicorn's own logger before
  this app's middleware runs, a separate log line from this app's own request-scoped
  logs. Not fixed here: correlating the two requires either a custom ASGI access-log
  middleware (replacing uvicorn's default, a real behavior change) or accepting the two
  streams as separately greppable (by timestamp/path), which is what this app does today.
