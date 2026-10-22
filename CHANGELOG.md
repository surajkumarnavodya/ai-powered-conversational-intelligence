# Changelog

Newest first. The README's "News and Updates" section shows only the three most
recent entries. This file keeps the full record. Each entry names the contract
document that holds the design, the API, the configuration and the limitations.

## 2026-10-22: readable answers, measured latency, a retrieval fix, and status moved to UAT (pre-production)

Seven commits (`60f6e23`..`628565d`). Four result-quality fixes, one
measurement pass, and a status move.

**Project status is now `UAT (pre-production)`**, set at `628565d`. This
is the phase the 2026-10-17 release gate pointed at — its verdict
(**NO-GO for production, READY FOR UAT with conditions**) is unchanged,
and entering UAT is not passing it. Nothing has been signed off: the
15-journey, 6-role script in
[`docs/certification/UAT_SCRIPTS_AND_SIGNOFF.md`](docs/certification/UAT_SCRIPTS_AND_SIGNOFF.md)
still has every result cell blank.

### Result quality

- **Business display names** (`5a77042`, `60f6e23`). A question about
  products returns `EnglishProductName`, not `ProductKey`. The generated
  SQL is repaired by AST surgery to join the dimension table holding the
  name — never by renaming a column, which would have produced a label
  that does not match the value under it. A *date* key is explicitly
  excluded: "sales by order date" was being silently rewritten into
  "sales by month name", found only by running against the real
  AdventureWorks schema. See [`docs/DISPLAY_NAMES.md`](docs/DISPLAY_NAMES.md).
- **Chart axis binding** (`13b8896`). The y-axis binds to a numeric
  *measure* chosen by a priority ladder (revenue > profit > quantity >
  count > average > percentage, with an `ORDER BY` boost) rather than
  the first numeric column, which in a star schema is usually a key. The
  backend classification was only a hint until the frontend stopped
  re-deriving axes independently. See
  [`docs/CHART_AXIS_BINDING.md`](docs/CHART_AXIS_BINDING.md).
- **Schema retrieval no longer strands a measure question** (`1b68ff9`).
  "Top selling products." was answering *"I can only answer questions
  about this database"*. Retrieval was trimming to the single
  highest-scoring table — `DimProductSubcategory`, a dimension with no
  sales measure — so the model received a schema that genuinely could
  not answer the question and correctly emitted its off-topic sentinel.
  A dimension's DDL is short and entirely on-topic, so it outscores
  every fact table on exactly the questions that need one; the same
  question *without the trailing period* retrieved five tables including
  the fact table. The dominant-gap trim is now withheld when the
  question asks for a measure the lone winner has no column to supply. A
  270-phrasing sweep leaves **zero** measure questions retrieving no
  measure table, with no lookup or aggregate question widened. The
  backstop's refusal message also stopped claiming "off-topic" for what
  is really a coverage miss.
- **Full-width question editor** (`f3947b1`). Clicking Edit gave a field
  a few characters wide; the cap was on the turn column, not the bubble.
  Editing now takes the whole column with no percentage at all, so it is
  width-independent across phone, tablet and desktop.
- **Web-search answer shaping** (`628565d`). The answer is sized to the
  question (a quick fact gets 1-3 sentences; a briefing is grouped by
  theme), quotes are capped at 15 words, and the model is explicitly
  forbidden from stating or estimating a publication date — results
  carry only a retrieval time, so a freshness rule without that guard
  invites a fabricated date.

### Latency, measured rather than assumed

`docs/PERFORMANCE.md` (new) records the diagnosis. A baseline question
took **123.77s**, of which `execute_sql` was **0.05s — 0.04%**.
Essentially all of it is model inference: `qwen2.5-coder:7b` needs
5.84 GB and only 2.37 GB fits the 4 GB GPU, so it runs **41% on GPU** at
~185 tok/s prompt and ~9.6 tok/s generation. Latency is therefore
`prompt_tokens/185 + generated/9.6`, making *call count* and *prompt
size* the only levers. Skipping the analytical-intent call for a
question with no complexity signal — whose result provably cannot change
any downstream decision — took it to **80.08s**, 3 model calls to 2
(`df8ffdb`, `6f93ef1`).

**The 3-5s target is not met for a SQL answer**: ~80s today, ~30s in the
best configuration this hardware allows. What does meet it is progress —
the first `/ask/stream` event lands at **0.025s** and every
pre-generation stage completes by 2.57s.

### Verification

**7,673** backend passed with **5 known failures** — a pre-existing cross-file-state interaction (`tests/security` running before `test_agent_nodes.py`), unrelated to any 2026-10-22 change and recorded in [`CLAUDE.md`](CLAUDE.md) §13; 11 `tests/live/` deselected · **1,334** frontend tests across 95 files · `tsc` clean ·
`npm run lint` exit 0 · `black` clean over **1,661** files. `ruff` exits
non-zero on one pre-existing import-order finding and `bandit` on 4 Low
(0 Medium, 0 High), both deliberate and both pre-existing. **`pip-audit`
could not be re-measured** — it requires PyPI and this environment has
no egress.

### Known and unfixed

Two phrasings ("Worst selling products", "Most popular products") are
still refused by `qwen2.5-coder:7b` even with the fact table and
`SalesAmount` verifiably in the prompt. `llama3.1:8b` answers both
(316.1s and 110.7s), so this is model capacity rather than the pipeline;
`OLLAMA_MODEL` is the lever, at a latency cost. Recorded in
[`CLAUDE.md`](CLAUDE.md) §13.

## 2026-10-17 (later): release-blocker verified closed, project status moved to advanced beta

A certification/status pass, not a feature change. **No application code
was modified** — the only code change in scope landed separately as
`08f385e` (the `scipy` pin). Everything here is measurement and
documentation.

### Verified

- **F-008 — the production image does not build — is CLOSED.** The
  morning's release gate returned NO-GO on it and declined to apply the
  obvious fix, because validating a numerical-dependency change needs a
  full suite re-run and a 3.11 resolution this machine cannot provide.
  Both objections are now discharged by measurement: the production
  image runs **`python 3.11.16`** with **`scipy 1.17.1`**; all five
  scipy-dependent production modules (`analytics/causal.py`,
  `forecasting.py`, `models.py`, `predictive.py`,
  `optimization/engine.py`) import inside it, along with `api.main`,
  `agent.graph` and `agent.sql_validator`; and the backend suite re-ran
  at **7,274 passed, 0 failed**. The image was not taken on trust — it
  was re-verified by executing the interpreter and the imports inside
  it.
- **The running application was driven end to end for the first time on
  record** — 20 probes against live SQL Server databases and local
  Ollama. The controls hold against real traffic, not only fixtures: a
  hand-edited `UPDATE`, a `DROP`, a stacked `SELECT 1; DELETE ...` and a
  `sys.tables` reference were all refused; the row cap appears in the
  SQL text as `SELECT TOP 1000`; compensation columns came back
  `[restricted]` against a bare `SELECT *`; 14 of 14 admin routes
  refused a `user`-role caller server-side, with the refusal written to
  the audit log; `X-Tenant-Id` was rejected with a 400 before
  authentication; and no password or connection string appeared in any
  log line read.
- **No drift in the lint/type baseline**: 631 mypy errors in 115 files
  with the same 7 in production modules, black clean over 1,635 files,
  one ruff `I001`, 1,161 frontend tests passing, `tsc` clean.

### Found

- **F-012 / R-020 — multi-database auto-routing can select a database
  that holds no data.** `select_database` ranks connections by
  schema-text similarity alone; it has no notion of whether a table has
  rows. Reproduced: a question routed to an empty `hr` database while a
  configured database holding 15 employees was available. Nothing was
  fabricated — `insight` was `null` and the answer said *"0 row(s)
  returned"*, so provenance held — but there is **no user recourse**,
  because `AskRequest` has no `database` field and the source picker
  selects source *types*. Cannot occur with one configured database.
  **Not fixed:** the remedy is a design decision, recorded with three
  options in [`docs/RISK_REGISTER.md`](docs/RISK_REGISTER.md) R-020.
- **F-011 — one accepted lint finding makes a *blocking* CI gate fail.**
  The `I001` in `agent/multiagent/agents.py` has long been recorded as
  known and accepted; what nobody had recorded is that CI runs `ruff
  check .` unscoped and blocking. **Not fixed** — the roadmap assigns
  that call to the owner; the point of filing it is that the trade-off
  is no longer cosmetic.
- **F-009 regressed, 3 → 4 bandit findings** —
  `agent/sql_validator.py:842` (`B112`) joined via `933cfff`. Still 0
  High, 0 Medium.
- **F-013 — `CLAUDE.md` §13 cited `langgraph==0.2.62`; the real pin is
  `langgraph==1.0.10rc1`** (a release candidate, in a production
  requirements file). The conclusion that entry drew still holds — zero
  checkpointer usage, re-verified — but the premise was three major
  versions stale. Corrected.

### Changed

- Project status is now **advanced beta — pre-production hardening**
  ([`README.md`](README.md)), with a table separating "builds",
  "tests pass", "workflows run live", "CI green", "capacity known",
  "accuracy known", "recovery drilled" and "UAT completed" — because
  those had been collapsing into one impression.
- [`docs/certification/2026-10-17_release_gate_v2.md`](docs/certification/2026-10-17_release_gate_v2.md)
  gains a §11 addendum rather than a v3 document. Verdict: **still
  NO-GO for production, READY FOR UAT with conditions.** §1–§10 are
  preserved unmodified as the morning's assessment.
- [`docs/certification/UAT_SCRIPTS_AND_SIGNOFF.md`](docs/certification/UAT_SCRIPTS_AND_SIGNOFF.md)
  grows from 15 to **18 journeys** (documentation accuracy; model
  selection; boundaries and limits), plus per-journey objective,
  business scenario, preconditions and test data; a five-value status
  vocabulary including **NOT TESTED** as the default; per-row defect
  reference, severity, tester and date; and a section recording what is
  already verified and what is already known broken, so UAT time is not
  spent rediscovering F-012. **Every result cell remains blank.**

### Not done, and why

**UAT is NOT COMPLETED and is not claimed otherwise.** No business user
has executed or signed off a single journey, and automated tests are not
UAT. Also not run: the Trivy container scan and the k6 load stack (both
*unblocked* by the F-008 fix but not executed), DAST, the 57-case
accuracy benchmark, the 500-case security benchmark, a DR restore drill,
OIDC against a live IdP, and multi-worker behaviour. A second
independent `docker build` and a CI-equivalent `detect-secrets` run were
started and did not finish; neither is claimed as evidence.

## 2026-10-17: SQL shape explanation, per-model token counts, accessibility checks

Four batches from the capability-catalogue plan
([`docs/ai-capability-implementation-plan.md`](docs/ai-capability-implementation-plan.md)),
each recorded with its full evidence in
[`docs/ai-capability-implementation-log.md`](docs/ai-capability-implementation-log.md).
`POST /ask` and `/ask/stream` keep their shape: every new response field is
optional with a default, no route or permission was added, and no governed
path (validation, row limits, masking, authorization) changed.

### Added

- **The generated-SQL panel now says what the SQL *does*, not just what it
  is.** `agent.sql_validator.describe_sql` reads the AST `validate_sql` has
  already parsed and reports the joins (the parser's own `INNER`/`LEFT`/
  `RIGHT`/`FULL`/`CROSS` side, never inferred from text), the columns
  filtered on, the aggregates, the `GROUP BY` grain, the statement-level
  `ORDER BY`, and whether `DISTINCT` is applied. No second parser, no model
  call, no new dependency — an explanation that hallucinated a join would
  sit beside a real validator verdict and read as authoritative, whereas a
  parse cannot be wrong about the statement it parsed. Three deliberate
  honesty boundaries: **no row limit** is reported (the `TOP`/`LIMIT` a
  caller sees is this platform's governance cap written back by
  `validate_sql_node`, not the query's own intent, and reporting it as
  query behaviour would present a control as authorship); **no business
  meaning** (`group_by` is the declared grain, not "sales by region", which
  is `semantic/`'s to say); and **no filter values** — column names only,
  because a predicate can carry data and this string is rendered and may be
  logged. Output is bounded at 12 items of 80 characters. `SqlSummaryOut`
  gains `joins`, `filter_columns`, `aggregates`, `group_by`, `order_by` and
  `is_distinct`, all optional with defaults; the server returns structured
  facts and the frontend renders them through i18n in all five locales, as
  six new rows in the *existing* query-metadata grid, each omitted when the
  statement has nothing to say for it. `describe_sql` gates nothing — a test
  asserts `validate_sql` still rejects `DELETE` while `describe_sql`
  happily describes it, proving the helper cannot be mistaken for a gate.
- **Per-model token consumption.** `agent/llm_client.py` had read Ollama's
  own `prompt_eval_count`/`eval_count` on every call for a long time and
  thrown them into a log line; `observability.metrics.record_llm_call` now
  accumulates them per model, and `llm_usage` on the metrics snapshot
  reports calls, prompt tokens, completion tokens and total tokens per
  model. Surfaced on the two **existing** metrics routes — `GET
  /metrics/performance` (`Permission.ADMIN_CONFIG`, the caller's own tenant
  only) and `GET /platform-admin/metrics` (`Permission.PLATFORM_ADMIN`,
  merged across tenants) — and on the two admin panels that already showed
  latency. The capture point is `_log_ollama_timing`, the single funnel all
  seven `client.chat` calls already pass through, so a future eighth call
  site is counted without anyone remembering to add it; a structural test
  parses the module's own AST and asserts the two counts stay equal. The
  model name is read from the *response*, so no call site changed and the
  name recorded is what the server actually served rather than what was
  asked for. Tenant attribution reuses `security.audit_log`'s existing
  contextvar.
  **Deliberately no monetary figure, anywhere.** The LLM runs locally, so
  this deployment has no per-token price; a cost column would be an
  invented number presented as an observed fact (`CLAUDE.md` §4.4). Three
  tests pin its absence — on the snapshot's keys, on the HTTP response's
  keys, and on the rendered panel. `model_routing`'s `total_cost_units`
  remains an abstract tier weight, not money, and keeps owning "which model
  was chosen and did the route work" while this rollup owns "what the
  models consumed" (§4.8).
- **Runtime accessibility checks in the component suite.** oxlint 1.82
  already shipped a `jsx-a11y` plugin that was simply not enabled — turning
  it on cost zero dependencies and surfaced 28 findings. For the runtime
  half, `frontend/src/test/axe.ts` wraps `axe-core` (Deque's own engine,
  dev dependency only, unreachable from the app entry so Vite cannot bundle
  it) and `frontend/src/components/ui/accessibility.test.tsx` runs it
  against rendered primitives, a dialog and a chat message. The static
  rules read one file's JSX and cannot see composition; the rendered tree
  can, which is how three `jsx-a11y` rules were turned off **with
  evidence** rather than for noise: `ShareModal`'s wrapping-label radios
  and `card.tsx`'s children-taking `CardTitle` both render with no
  violation, while a genuinely empty `<h3 />` is still caught. Non-vacuity
  is proven — one test renders an icon-only button with no accessible name
  and asserts axe reports `button-name`, so if axe stopped running that
  test fails rather than silently passing.

### Changed

- `observability/llm_timing_capture.py`'s documented role is corrected
  rather than extended. It parses the same Ollama token counts, but it
  works by attaching a handler to the `agent.llm_client` logger and forcing
  that logger's level **process-wide** for the scope of a `with` block.
  Under concurrent `/ask` requests every active capture would see every
  other request's timing lines, so tokens would be **cross-attributed
  between tenants**, and the level save/restore would race. It is correct
  for the single-threaded benchmark runner it was written for
  (`eval/runner.py`, still its only caller) and must not be reused on the
  request path — which is why the token rollup above records at the funnel
  instead. See [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md) §3a.

### Built, not wired (no live caller — see `CLAUDE.md` §1)

- **`agent/translation.py`** translates the *prose* of an answer that has
  already been through the governed path, so a Hindi-speaking analyst reads
  Hindi prose around the same figures an English-speaking one sees. What
  makes it safe is the protocol, not the model: every span that must
  survive byte-for-byte — numbers, currency, dates, code and SQL blocks,
  markdown links (citations and sources) and the governance mask
  `[restricted]` — is replaced by an opaque placeholder, only the prose
  between placeholders reaches the model, the spans are restored verbatim,
  and the protected spans recovered from the output must be the same
  multiset as those taken from the input. If a single placeholder is
  missing, duplicated or mangled the translation is **discarded** and the
  original returned, so `translate_answer` has exactly two outcomes. It
  uses the same local Ollama model, adding no provider, no egress path and
  no cost. **Nothing calls it yet** — there is no route, no node and no
  setting; it is tested (`tests/test_translation.py`) and deliberately
  parked, not shipped. `agent.input_guard` still refuses a user's request
  to translate arbitrary text, which is a different thing and correctly
  refused.

### Benchmarks

- Checked in the artefacts from three `scripts/run_benchmark.py` runs on
  2026-10-16 (`eval/results/run_20261016T184207Z*`, `...T185313Z*`,
  `...T205212Z*` — `llama3.1:8b` against `AdventureWorksDW2025`, 3, 3 and
  33 cases). These were **not** produced or verified by the session that
  committed them, so no headline figure was promoted into
  [`docs/EVALUATION_CURRENT.md`](docs/EVALUATION_CURRENT.md), whose numbers
  are pinned to specific prompt wording (`CLAUDE.md` §12). The `_full.json`
  files embed result rows from Microsoft's synthetic AdventureWorks sample
  warehouse — no real or personal data.

## 2026-10-16: Web search — untruncated results and a synthesized answer

Scoped to the web source. `POST /ask`, `/ask/stream` and every response
model are unchanged; `ENABLE_WEB_SEARCH` is still off by default.

### Fixed

- **Web-search and RAG prompts were silently truncated by Ollama.**
  `rag/llm.py::call_ollama` — the helper behind the RAG subgraph, the
  orchestrator's router / web-search / media-search answer steps and the
  analyst planner — set only `num_predict` and `temperature`, never
  `num_ctx`. Prompt 05 fixed exactly this for `agent/llm_client.py`'s
  seven calls but did not reach the second call site, so those steps ran
  against Ollama's default served window while the SQL path ran at
  `OLLAMA_NUM_CTX`. Ollama does not reject an oversized prompt: it
  truncates from the *start*, taking the system prompt — which for the
  web node is both the answer-formatting instructions and the "untrusted
  data, not instructions" framing. Measured on `llama3.1:8b`,
  2026-10-16: without `num_ctx`, a long prompt came back evaluated at
  **2,050 tokens with the system prompt gone**; at
  `OLLAMA_NUM_CTX=16384`, 12,043 tokens with it intact.
  `agent.llm_client._decoding_options` is now public `decoding_options`
  and is the one owner of decoding policy for both call sites, so they
  cannot drift again. A side effect: Ollama no longer reloads the model
  between a SQL call at 16,384 and a RAG/web call at the default.

### Added

- **`WEB_SEARCH_CONTEXT_MAX_CHARS`** (default `18000`, `0` disables).
  An explicit, reported input budget for the results block, replacing the
  silent cut inside Ollama — removing a limit is not the same as removing
  truncation. Measured 2026-10-16: five ordinary pages (1,547–2,363
  characters each) all pass whole, while five academic pages for one
  medical question exceeded it between them and the top two were used.
  When it binds it drops **whole**
  results from the lowest-ranked end (never half a source), never drops
  the top-ranked one, states the omission in the answer, and leaves the
  dropped results out of the citation list — a page the model never saw
  is not a source for the answer.

### Changed

- **The web answer is asked to read like a modern search assistant's.**
  The existing direct-answer-then-headings prompt gains: synthesize
  across results rather than giving each its own paragraph, never state
  the same fact twice, keep figures/dates/names/versions rather than
  rounding them, use a table only where several items share the same few
  attributes, drop filler, and state plainly when results disagree, when
  a claim rests on one result, or when part of the question is uncovered.
  Inline citations (a markdown link under the source's own title) stay
  the only in-answer attribution: `SourceAnswerCard.tsx` already owns the
  source list, and this repo keeps one owner per fact. The prompt is not
  benchmark-pinned; the SQL generation and insight prompts are untouched.
- `WEB_SEARCH_ANSWER_MAX_TOKENS` in `.env.example` corrected to `2400`,
  the actual `Settings` default.

Documentation: [`docs/MULTI_SOURCE_GUIDE.md`](docs/MULTI_SOURCE_GUIDE.md)
§5, [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md),
[`docs/INFERENCE_ENGINEERING_BASELINE.md`](docs/INFERENCE_ENGINEERING_BASELINE.md)
§2.

## 2026-10-15: Source connectivity, streaming, and an evidence-based audit

Everything added here is off by default except streaming and the source
picker. `POST /ask` is unchanged: `/ask/stream` calls that handler rather
than reimplementing it, and omitting `AskRequest.sources` takes the exact
path it always did.

### Added

- **Source-type contract and registry** (`sources/`). Single owner of each
  source's routing description, required permission and "is it configured"
  rule. `agent/orchestrator/nodes.py` now derives `_SOURCE_DESCRIPTIONS`,
  `SOURCE_PERMISSIONS` and `get_available_sources` from it instead of three
  parallel literals that could drift. Also a `SourceConnector` lifecycle
  Protocol plus a mock and a conformance suite.
  Design: [`docs/architecture/source-connector-contract.md`](docs/architecture/source-connector-contract.md).
- **Per-conversation source selection** (`AskRequest.sources`, plus a picker
  in the chat composer and `GET /sources`). Restricts one question to named
  sources. Additive and optional; omitting it is the previous behaviour.
- **REST/OpenAPI connector** (`rest/`, flag `ENABLE_REST_CONNECTOR`).
  Operator-declared HTTPS endpoints, read-only, over an allowlist. Not
  routable yet: no `sources/descriptors.py` entry and no orchestrator node,
  so `GET /sources` does not advertise it.
- **SSRF-safe outbound helper** (`security/outbound_url.py`). HTTPS-only, a
  host allowlist checked *before* DNS, every resolved address checked against
  the private/reserved blocklist, redirects re-validated hop by hop, and a
  byte-capped JSON GET.
- **MCP client** (`mcp_client/`, flag `ENABLE_MCP_CLIENT`). A client *of*
  remote MCP servers over stdio — the opposite direction to `agent/tools/`,
  with no shared code. Targets MCP revision 2025-06-18, whose wire shapes were
  verified against the specification rather than written from memory.
- **Streaming** (`POST /ask/stream`, `api/streaming.py`, `agent/progress.py`).
  Server-sent events: per-node progress while a run is going, then the same
  `AskResponse` `POST /ask` returns. The chat composer's pending badge shows
  the running stage. Closes the "no streaming" gap `CLAUDE.md` §13 disclosed.
- **Opt-in live integration tests** (`tests/live/`, marked `live`,
  deselected by default via `addopts = "-m 'not live'"`). Covers what exists
  and is credential-blocked: web search, the RAG vector store, the REST
  connector against real DNS, the MCP handshake, and Ollama. Each skips by
  name when its credential is absent.
  Runbook: [`docs/LIVE_INTEGRATION_TESTING.md`](docs/LIVE_INTEGRATION_TESTING.md).
- **Capability inventory** ([`docs/architecture/feature-inventory.md`](docs/architecture/feature-inventory.md)),
  a source matrix and an implementation roadmap, all produced by an
  evidence-based audit rather than from a plan document.
- Settings: 8 new variables (`ENABLE_REST_CONNECTOR`, `REST_ALLOWED_HOSTS`,
  `REST_TIMEOUT_SECONDS`, `REST_MAX_RESPONSE_BYTES`, `REST_MAX_REDIRECTS`,
  `ENABLE_MCP_CLIENT`, `MCP_REQUEST_TIMEOUT_SECONDS`, `MCP_MAX_RESPONSE_BYTES`)
  plus the open-ended `MCP_SERVERS` + `MCP_<NAME>_*` block, all documented in
  `docs/CONFIGURATION.md` and `.env.example`.
- The header product name is now a home link that starts a new chat, running
  the identical two calls the sidebar's New chat button runs.

### Changed

- `agent/nodes.py::_timed_node` emits a progress event. With no sink bound —
  every caller except an SSE stream — it is a contextvar read and an early
  return. No node body changed.
- `AskResponse` gains `source_selection_notice`, additive with a default and
  deliberately separate from `permission_denied_notice`: "not configured" and
  "not permitted" have different causes and different remedies.
- `tests/test_sources_conformance.py` split into corpus-aware checks and a
  universal tier, correcting an overstated claim that any connector could be
  added to `_CONNECTORS` and inherit every check.

### Fixed

- Four tests were making **real DNS lookups** for `api.example.com` through
  `RestConnector.health`. They passed — the name NXDOMAINs — which is why it
  went unnoticed, but a green test that quietly depends on the network breaks
  the fully-mocked rule. Fixed with a transport-patching hook; re-verified at
  137 passed with `getaddrinfo` globally blocked.
- A `mypy` error introduced with source selection: `_apply_source_selection`
  inferred `notice` as `str` from its first assignment, making the later
  `notice = None` invalid. That batch's type check had not covered
  `agent/orchestrator/`.
- `CLAUDE.md` §13 listed mocked tests for `search/` as an open follow-up;
  `tests/test_web_search.py` had already closed it. §13 now warns that its own
  items are dated.

### Security

- **Source selection is a filter, never a grant.** `AskRequest.sources` is
  intersected with what configuration makes available *and* what the caller's
  roles permit, and the pre-existing role check still runs on whatever
  survives. Replacing the intersection with a union fails 19 tests.
- **MCP trust is administrator-controlled only.** A remote tool is never
  auto-registered into `agent.tools.registry.ToolRegistry` (pinned by an AST
  check), an empty per-server allowlist approves nothing, allowlist matching
  is exact, and a server's own metadata cannot widen its access.
- **`InitializeResult.instructions` is read and discarded.** The MCP spec
  describes it as telling a client how to use the server; feeding it to a
  model would be prompt injection with full pipeline reach.
- **The MCP subprocess gets no shell and no inherited environment.** This is
  the first production code here to spawn a subprocess. `env` is a complete
  replacement, so a third-party binary never receives `DB_PASSWORD`,
  `JWT_SECRET_KEY` or anything else from `.env`.
- **Progress events carry metadata only.** An event reaches the client before
  the result gate runs inside `execute_sql_node`, so it carries stage,
  attempt, duration, outcome and an error *category* — never SQL, a row, or an
  error message.
- Three copies of the SSRF blocklist now exist (`security/outbound_url.py`,
  `media_gen/download.py`, `bi_integration/outbound_safety.py`). Acceptable
  only because divergence cannot go unnoticed:
  `tests/test_outbound_url.py::TestBlocklistsAreIdentical` asserts all three
  match. Consolidating the two older copies is its own task, not done.
- Known, not fixed: the `ToolRegistry.execute` `caller_roles` override noted
  in the 2026-10-06 entry still stands. `mcp_client/` does not use that path.

### Migration and deployment

- **No database migration.** No schema change in this release.
- **No breaking change.** Every new setting defaults off, `AskRequest.sources`
  is optional, and `AskResponse.source_selection_notice` is additive with a
  default.
- To roll back any individual feature, unset its flag; no data is written that
  would need undoing.

### Verification

- Backend suite: **6,453 passed, 11 deselected** (the opt-in live tests), in
  755.93 s. Frontend: **1,079 passed** across 81 files. `tsc --noEmit` clean;
  `black --check` clean; `ruff` reports only the pre-existing `I001` in
  `agent/multiagent/agents.py`; `mypy` clean on every new module.
- Security controls were mutation-tested rather than asserted: the source
  filter (19 failures when turned into a union), three SSRF controls (3/9/1
  failures), and five MCP trust and transport controls (7/2/2/1/1 failures).
  Each mutation was applied to live code and reverted.
- **Not verified**: the MCP client has never met a real server, the REST
  connector has no live endpoint here, and no Microsoft 365 connector exists
  to test. See [`docs/LIVE_INTEGRATION_TESTING.md`](docs/LIVE_INTEGRATION_TESTING.md).

## 2026-10-06: AI analysis layers (Prompts 33-35)

All three layers are optional and off by default. None of them changes `/ask`.

### Added

- **AI Data Analyst** (`POST /analyst/investigate`, flag `ENABLE_DATA_ANALYST_AGENT`).
  A bounded, multi-step LangGraph loop that splits a business question, runs each
  part through the governed SQL pipeline, follows anomalies with fixed-template
  follow-ups, and returns an evidence-linked report. Budgets for steps,
  sub-questions, LLM calls, follow-ups and time are hard server-side stops.
  Contract: [`33_AI_DATA_ANALYST_AGENT_CONTRACT.md`](33_AI_DATA_ANALYST_AGENT_CONTRACT.md).
- **Multi-agent supervisor** (`POST /analyst/supervise`, flag `ENABLE_MULTI_AGENT_SUPERVISOR`).
  A deterministic supervisor over eight typed specialists. Each specialist has
  its own tool allowlist and truth-level ceiling. The tool gateway refuses
  identity fields in tool input. Circuit breakers are scoped per tenant. Claims
  must cite evidence the supervisor issued. Conflicts are resolved by a fixed rule.
  A benchmark (`eval/multiagent_benchmark.py`) confirms the supervisor makes the
  same SQL and planner calls as the analyst.
  Contract: [`34_MULTI_AGENT_SUPERVISOR_CONTRACT.md`](34_MULTI_AGENT_SUPERVISOR_CONTRACT.md).
- **Semantic intelligence** (`/semantic-intelligence/*`, flag `ENABLE_SEMANTIC_INTELLIGENCE`).
  Deterministic detection of synonym candidates, term clusters, ambiguous terms,
  conflicting metric definitions, candidate business rules, metric relationships
  and change impact over the governed catalog. Findings are versioned, carry an
  owner, and are ordered for review by risk rather than volume. Finding rollback
  and catalog-version rollback both create new versions and never rewrite history.
  Contract: [`35_SEMANTIC_INTELLIGENCE_CONTRACT.md`](35_SEMANTIC_INTELLIGENCE_CONTRACT.md).
- Database migration `a1f5c9e3b7d2` (additive): the `semantic_findings` table.
- Settings: 14 new variables, all documented in `docs/CONFIGURATION.md` and `.env.example`.

### Changed

- `rag/llm.py`: `call_ollama` accepts an optional `model` override. The default
  is unchanged for every existing caller.

### Security

- Every new route requires an authenticated local account with the relevant
  permission. Cross-tenant resources return 404, not 403.
- The analyst and the supervisor execute SQL only through the existing governed
  pipeline, under the caller's own roles.
- Findings and claims are always labelled `ai_inference` unless they come from a
  published catalog entry or an observed database value. Nothing inferred is
  presented as fact.
- Known, not fixed: `ToolRegistry.execute` lets tool input override
  `caller_roles`. The new supervisor gateway blocks this for everything it runs,
  but the shared registry still allows it. See the Prompt 34 contract's risk section.

### Verification

- Backend suite: 3,993 tests passing on the final code, including `tests/security/`.
- The three layers were verified with mocked LLM and SQL boundaries and with
  synthetic catalog data. They have not been run against a live model or a real
  tenant's catalog. See each contract's limitations section.
