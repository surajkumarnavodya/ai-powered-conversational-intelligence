# PROMPT 47 — MODEL ROUTING COST OPTIMIZATION

## The prompt, verbatim

**Role:** Principal AI Architect, Enterprise Data Platform Architect and Senior Product Engineer.

**Mission:** Implement governed model routing. Inspect current model abstraction, Ollama/free models, provider configuration, quality metrics and latency. Route by complexity, risk, latency and capability; use smaller models for simple work and stronger models for complex reasoning when evaluation supports it. Track quality, latency, failures and cost by route. Add fallback, circuit breakers and budgets. Never use an LLM for deterministic security validation.

**Mandatory workflow:** INSPECT → PLAN → IMPLEMENT → TEST → REVIEW → FIX → DOCUMENT → REPORT

**Mandatory controls:** Inspect and reuse existing implementation before adding code. / Preserve authentication, authorization, tenant isolation and deterministic SQL/data controls. / Treat retrieved data/documents as untrusted. / Preserve evidence and provenance. / Never present inference, simulation or prediction as observed fact. / Keep secrets out of code/logs/tests. / Add unit/integration/security/regression tests appropriate to the change. / Update API/schema/config documentation.

---

## 1. INSPECT — what already existed

| Area inspected | What was found | Consequence for this prompt |
|---|---|---|
| `agent/model_registry.py` | A real, deliberate three-concept split: **configured/allowed** (`Settings.ollama_allowed_models`, "the only thing that actually gates what a caller may request"), **installed** (a live Ollama lookup, done only by `GET /models`, never on the request path), and **curated display metadata**. | Reused as-is. The allowlist stays the only gate; routing chooses *within* it. Installed-status discovery is deliberately **not** added to the request path — see §6. |
| `config/ollama_models.yaml` + `config/ollama_models.py` | Already carries `resource_level` (low/medium/high), `capabilities` (`text`/`sql`/`reasoning`/`tools`), `context_length` and `recommended` for 7 models. Its own docstring is explicit that this is "purely cosmetic/informational ... never a gate." | This *is* the capability/tier source. `ModelTier` maps `resource_level` directly rather than parsing a parameter-size string. The "never a gate" promise is honored: the metadata only filters models routing would swap **to**, never the operator's configured default. |
| `agent/complexity.py` | `detect_complexity_signals` already runs once per question (four regex signals) to widen the retry budget. | Reused verbatim as the complexity input. `model_routing.signals` reads `state["complexity_signals"]` rather than re-scanning, so the two features can never disagree about a question's difficulty. |
| `agent/intent.py` | `AnalyticalIntentType` (13 kinds) + `intent_implies_planning`. | Reused as the second complexity input. |
| `agent/multiagent/policy.py` | A real, tested `CircuitBreaker` (closed/open/half-open with cooldown probe), a process-wide `get_breaker`/`reset_breakers`, and a `TurnBudget` (max calls + max cost units + refusal reason). | **Reused, not reimplemented.** `CircuitBreaker` gained one additive, read-only method (`would_refuse`) so routing can see which models are broken without consuming a half-open probe; `allow`'s behavior is unchanged. `TurnBudget` is the per-request routing budget. |
| `agent/rate_limit.py` | `SlidingWindowRateLimiter` + `BoundedLimiterCache`, with an established per-caller-per-hour factory shape (`get_share_invite_limiter`). | Reused for the per-tenant hourly escalation cap. |
| `observability/metrics.py` | `PerformanceMetrics`: per-LangGraph-stage, per-tenant, bounded, in-process, resets on restart. Knows nothing about which model served a call. | A sibling rollup (`model_routing/metrics.py`) rather than a new dimension there — see §4 for why. |
| `governance_center/` + `identity/repositories/governance.py` (Prompt 44) | A full governed lifecycle for `model`-typed assets: draft → testing → evaluating → approved → deployed, with an operator-recorded `evaluation_results` payload, tenant scoping, RBAC and an append-only audit trail. | **This is the answer to "when evaluation supports it."** No second evaluation store was built; `model_routing.evidence` reads the approved/deployed versions this center already governs. |
| `agent/nodes.py` | Seven `*_from_llm` call sites, every one already passing `model=state.get("selected_model")` — a single, uniform seam. | One helper (`_routed_llm_call`) at those seven sites. No prompt, no node contract and no retry rule changed. |
| `eval/` + `eval_platform/` (Prompt 46) | Real benchmark harnesses, but **no per-model quality numbers exist anywhere in this repository** — `docs/EVALUATION_CURRENT.md`'s figures are for one model, dated 2026-09-16. | Stated plainly rather than papered over: routing cannot invent per-model accuracy. Evidence comes only from what an operator recorded and approved. See §6. |
| `agent/sql_validator.py`, `agent/authz.py`, `security/tenancy.py` | The deterministic controls. | **Untouched.** No module in `model_routing/` imports any of them, asserted by an AST test, not by a promise. |

**The decisive inspection finding:** every ingredient the mission names already existed in this codebase — complexity signals, intent, capability/tier metadata, a circuit breaker, a budget, a sliding-window limiter, a metrics-rollup pattern, and a governed place where a model's evaluation lives. What did not exist was anything that *joined* them: the pipeline used exactly one model for every call in a run. This prompt is almost entirely wiring, which is why `model_routing/` is small and why `CircuitBreaker`/`TurnBudget`/`SlidingWindowRateLimiter` appear in it by import rather than by copy.

## 2. PLAN

A new top-level package, `model_routing/`, a peer of `analytics/`/`optimization/`/`data_quality/`/`lineage/`/`schema_drift/`/`governance_center/`:

- `models.py` — frozen typed vocabulary + the static per-task risk/capability tables.
- `catalog.py` — allowlist + YAML metadata + evidence → candidate set.
- `evidence.py` — governed evaluation evidence, tenant-scoped, TTL-cached, fail-open.
- `signals.py` — `RequestProfile` from already-computed signals.
- `policy.py` — the **pure** decision function.
- `budget.py` — per-request compute units + per-tenant hourly escalation cap.
- `metrics.py` — per-`(task, model)` quality/latency/failure/cost rollup.
- `router.py` — the stateful façade: policy + breaker + budget, then the call with fallback and outcome recording.

Plus: one helper and seven one-line changes in `agent/nodes.py`; three request-scoped `AgentState` fields; a `model_explicitly_selected` flag threaded `/ask` → `run_orchestrated` → `run_agent`; `AskResponse.route_decisions`; and a three-route read-only REST surface.

## 3. IMPLEMENT — the four levers, and where each enters

| Lever | Mechanism |
|---|---|
| **Complexity** | `RequestProfile.complexity_score` = number of `agent.complexity` signals + 1 if the intent implies multi-step planning + 1 if the intent carried an ambiguity flag. Thresholded by `MODEL_ROUTING_UPGRADE_COMPLEXITY_THRESHOLD` (2) and `..._DOWNGRADE_...` (0). A cross-field `model_validator` refuses a configuration where one score would qualify as both simple and complex. |
| **Risk** | `TASK_RISK`, static and reviewed, decides which *direction* a task may move at all; complexity decides whether it does. `sql_generation`, `sql_review` and `metric_conformance_review` are HIGH and `query_plan`/`analytical_plan` are MEDIUM: upgradeable, **never** downgraded. `analytical_intent`/`sql_insight` are LOW: downgradeable, **never** upgraded. |
| **Latency** | Two independent brakes: `RequestProfile.latency_sensitive` blocks escalation for a request short on wall clock, and a candidate whose **measured** p95 in this deployment exceeds `MODEL_ROUTING_MAX_P95_LATENCY_MS` is dropped from the escalation set. A model with no measurement yet is never treated as slow. |
| **Capability** | `TASK_REQUIRED_CAPABILITIES` against the YAML tags — applied only to a model routing would swap *to*. `sqlcoder:7b` (tagged `[sql]`) can therefore serve generation but is never chosen for a reasoning task; an un-catalogued default still serves every task. |

**The governance gate.** With `MODEL_ROUTING_REQUIRE_EVALUATION_EVIDENCE` on (the default), a model is only chosen over the configured default when an `approved`/`deployed` `model`-typed governed version exists for it, for this tenant, with recorded `evaluation_results`. This applies to **downgrades as well as upgrades**: swapping in a cheaper model is still a change to how answers are produced, and "governed routing" means a human approved the model routing may move to, in either direction. A recorded numeric score (read verbatim under a small set of recognized keys — never computed here) outranks tier when choosing among eligible escalations, which is the whole point of gating on evidence rather than on size.

**Fallback and breakers.** Two failure kinds, deliberately treated differently:

- `AVAILABILITY` (`OllamaUnavailableError`) — a health signal. Trips the per-model breaker and walks the decision's `fallback_chain` (the configured default first, since it is the one model an operator has definitely decided to run). Only after every candidate fails is the error surfaced, and it is the **last** attempt's error, not a stale first one.
- `QUALITY` (any other `AgentError` — malformed output, an unparseable plan, an off-topic refusal) — re-raised unchanged to the calling node, with no second model tried. Counted in the rollup, and **never** fed to the breaker: a model that is up and merely wrong is not a fault to open a circuit on, and doing so would let one adversarial question disable a healthy model for every other caller. That is a regression test, not a comment.

A fallback chain is deliberately **not** filtered by the task's own risk
level, so a HIGH-risk task whose stronger candidates are all unreachable
can end up on a smaller model. A fallback only ever engages when the
alternative is failing the request outright, the SQL is still gated by the
deterministic allowlist whichever model drafted it, and the chain is
ordered strongest-first so a weaker model is only ever reached last. The
decision records the whole chain, so what happened is inspectable rather
than inferred.

**Budgets with teeth that never break a question.** A spent per-request compute budget or per-tenant hourly escalation cap removes the *option* to escalate; the request proceeds on the configured default — the behavior it would have had with the feature off. Refusals are recorded (`RouteReason.BUDGET_EXHAUSTED`, and `budget_denials` in the rollup), so a deployment constantly at its ceiling is visible rather than silent. The per-tenant window is consulted only *after* an optimistic policy pass has decided an escalation is actually wanted, so an operator's hourly allowance is never burned on a task routing was never going to escalate.

**Cost is never money.** Every routable model runs locally through Ollama. `TIER_COST_UNITS` is ordinal (1/2/5), stands in for "how much more of this machine a stronger model consumes," and is never rendered as a currency anywhere — a test asserts no money-shaped field appears in the API response. Inventing a dollar figure would be presenting an estimate as an observed fact.

## 4. Design decisions worth recording

- **A sibling metrics rollup, not a new dimension on `observability.metrics`.** `PerformanceMetrics` is per-stage/per-tenant and has been the shape of `GET /metrics/performance` since Prompt 22. Adding `(task, model)` keys there would change that response for every existing consumer, for data only this feature produces. `model_routing/metrics.py` copies its disclosed posture (single-process, in-memory, resets on restart, bounded) rather than inventing a better one, because the same constraints apply.
- **"Quality" is defined narrowly and honestly.** `quality_failure_rate` is the share of judged calls this application's own deterministic parsers/validators rejected. It is a real observed signal about a route; it is **not** an accuracy score, and the API field's own description says so. A parseable answer can still be wrong.
- **`route_decisions` is a plain list, filled after the graph runs.** It is not an `operator.add` reducer channel like `error_history`/`stage_timings`, because no node returns it: the router keeps an ordered record and `run_agent` reads it back once. That kept seven call sites in `agent/nodes.py` from each having to thread an extra key through every one of their return paths.
- **A caller's explicit model always wins.** `api/main.py` resolves an omitted `AskRequest.model` to `Settings.ollama_model` *before* the graph runs, so `selected_model` alone could no longer distinguish "the caller asked for this" from "the caller asked for nothing." Rather than guess, a `model_explicitly_selected` flag is threaded explicitly; when it is set, the router is not even constructed.
- **Off by default.** Unlike the pure-computation engines (`enable_data_quality_engine`, `enable_lineage_platform`, `enable_schema_drift_detection`), this changes which model answers a real question — a behavioral change to output quality that an operator opts into.

## 5. Files changed

**New (11):**

| File | Purpose |
|---|---|
| `model_routing/__init__.py` | Package map + the "what this is not" boundary. |
| `model_routing/models.py` | Typed vocabulary, `TASK_RISK`, `TASK_REQUIRED_CAPABILITIES`, `TIER_COST_UNITS`. |
| `model_routing/catalog.py` | Candidate set from the allowlist + YAML + evidence. |
| `model_routing/evidence.py` | Governed evaluation evidence (tenant-scoped, TTL-cached, fail-open). |
| `model_routing/signals.py` | `RequestProfile` from existing signals. |
| `model_routing/policy.py` | The pure `decide_route`. |
| `model_routing/budget.py` | Per-request + per-tenant budgets. |
| `model_routing/metrics.py` | Per-route rollup. |
| `model_routing/router.py` | `ModelRouter`: breaker, budget, fallback, outcome recording. |
| `api/model_routing.py` | 3 read-only routes. |
| `api/model_routing_schemas.py` | Their request/response models. |

**New tests (3 files, 95 cases):** `tests/test_model_routing.py` (59), `tests/test_api_model_routing.py` (20), `tests/security/test_model_routing_never_weakens_deterministic_controls.py` (16). Plus 6 cases added to two existing files (`tests/test_full_pipeline_integration.py`, `tests/test_api_authz.py`) -- 101 in total.

**Modified (15):** `agent/nodes.py` (one helper + 7 one-line call-site changes), `agent/graph.py` (router construction + post-run decision record + one parameter), `agent/state.py` (3 request-scoped fields), `agent/multiagent/policy.py` (one additive read-only `CircuitBreaker.would_refuse`), `agent/orchestrator/graph.py` + `agent/orchestrator/nodes.py` (thread the flag; see §7), `api/main.py` (router registration, one parameter, one response field), `api/schemas.py` (`AskResponse.route_decisions`), `config/settings.py` (12 fields + 1 cross-field validator), `tests/conftest.py` (reset the new process-wide singletons), `tests/test_api_authz.py` (2 cases), `tests/test_full_pipeline_integration.py` (4 cases), `tests/test_orchestrator.py` + `tests/test_api_ask.py` (7 stand-in signatures grown for the new parameter).

**Docs:** this contract, `CLAUDE.md`, `docs/API.md`, `docs/CONFIGURATION.md`, `.env.example`, `tests/security/README.md`.

## 6. API / DB / config changes

**API.** Three new read-only routes under `/model-routing` (`GET /policy`, `POST /preview`, `GET /metrics`), all 404 when `ENABLE_MODEL_ROUTING=false`. One additive, defaulted field on `AskResponse` (`route_decisions`). No existing response field changed shape or meaning.

**No write route exists, deliberately.** Routing is configured in `.env` and the models it may escalate to are governed through `/governance` (Prompt 44's approval lifecycle). A write route here would be a second, ungoverned way to change which model answers a question.

**DB.** None. No migration, no new table, no new permission. Evidence is read from `governance_asset_versions`, which already exists.

**Config.** 12 new settings, all defaulted so an untouched `.env` is unaffected: `ENABLE_MODEL_ROUTING` (default **false**), `MODEL_ROUTING_REQUIRE_EVALUATION_EVIDENCE` (true), the two complexity thresholds, `MODEL_ROUTING_MAX_P95_LATENCY_MS`, `MODEL_ROUTING_MAX_FALLBACKS`, the two per-request budget caps, `MODEL_ROUTING_ESCALATIONS_PER_HOUR`, the two breaker settings, and `MODEL_ROUTING_EVIDENCE_TTL_SECONDS`.

## 7. Tests and results

| Suite | Count | Covers |
|---|---|---|
| `tests/test_model_routing.py` | 59 | Catalog (real shipped YAML, not a fixture's invented one), signals, the policy across all four levers, the evidence gate in both directions, fallback-chain construction, budgets, the metrics rollup, router fallback/breaker behavior, and the type-level guarantees. |
| `tests/test_api_model_routing.py` | 20 | Feature flag on all three routes, policy/preview/metrics contracts, that the preview equals the real policy's decision, that previewing consumes no budget or breaker probe, and that no response carries a question, a credential or a currency. |
| `tests/security/test_model_routing_never_weakens_deterministic_controls.py` | 16 | §8. |
| `tests/test_full_pipeline_integration.py` | +4 | The feature's headline claim, through the **real compiled graph**: one run really does hand `sql_generation` the stronger model while `sql_insight`/`analytical_intent` keep the default (and take the smaller one on a simple question); decisions are recorded in the graph's own node order; and with the flag off every call gets the one configured model and `route_decisions` is empty. Added to Prompt 24's own file, whose purpose is proving the nodes compose. |
| `tests/test_api_authz.py` | +2 | `/model-routing/metrics` is admin-only; a viewer may preview (it holds `Permission.ASK`). |
| `tests/test_orchestrator.py`, `tests/test_api_ask.py` | 7 updated | Hand-written `run_agent`/`run_orchestrated` stand-ins grown for the new parameter -- see the results note below. |

**Full backend suite: 4773 passed, 0 failed.**

A first full run surfaced 7 failures, and every one of them was a
hand-written `run_agent`/`run_orchestrated` **stand-in** in an existing
test whose signature had to grow the new `model_explicitly_selected`
parameter (6 in `tests/test_orchestrator.py`, 1 in
`tests/test_api_ask.py`) -- the identical maintenance step Prompt 16
already recorded for its own `forecast_horizon` addition, and the reason
a stand-in that mirrors a real signature by hand is worth keeping rather
than loosening to `**kwargs`: it fails loudly when the real contract
changes. Each was updated, and `tests/test_api_ask.py`'s now *records*
the flag rather than merely tolerating it, so "an explicit model choice
reaches the graph as explicit" is pinned at the HTTP boundary too.

`ruff check` and `black --check` clean on every file touched. `mypy` reports zero errors in `model_routing/` and the two new `api/` modules; the one error it emits when checking them comes from `db/schema_introspection.py:342`, pulled in as a dependency, pre-existing and untouched here. One further pre-existing `ruff` finding was confirmed present in `HEAD` and left alone: `tests/conftest.py:77` F821 (`Undefined name 'Path'`), from Prompt 46's own fixture.

### Found by this prompt's own review pass, and fixed

- **Low-risk tasks were being escalated on a hard question** -- the opposite of cost optimization. The first version thresholded upgrade eligibility on complexity alone, so a genuinely layered question sent `analytical_intent` (a 13-way label) and `sql_insight` (narrating an already-computed result) to the largest available model along with the SQL generation that actually needed it. Found while writing the integration test that was supposed to *demonstrate* per-task routing, which is exactly where a cost-optimization feature quietly inverting its own purpose would otherwise have gone unnoticed. Risk now decides the *direction* a task may move and complexity decides whether it moves: LOW-risk tasks are never upgraded, MEDIUM/HIGH-risk tasks never downgraded.

- **An off-topic refusal was being counted as a quality failure.** The
  router's first version treated every `AgentError` other than
  `OllamaUnavailableError` as `RouteFailureKind.QUALITY`. But
  `OffTopicQuestionError` means the model judged a question unanswerable
  as SQL -- which is it doing its job, and exactly the outcome
  `agent.input_guard`'s pre-filter and `generate_sql_node`'s backstop both
  exist to reach. Counting it as a failure would have inflated a route's
  `quality_failure_rate` for correct behavior, which is precisely the kind
  of number that gets trusted later. Now recorded as a success, with its
  own regression test.
- Two smaller ones: a local import in `metrics.py` justified by a comment
  claiming an import cycle that does not exist (the module already imports
  from `model_routing.models` at module scope), and an incomplete sentence
  in `budget.py`'s docstring.

## 8. Security and tenant review

**"Never use an LLM for deterministic security validation"** is enforced structurally, not by convention, and asserted by parsing the real source:

- No module in `model_routing/` imports `agent.sql_validator`, `agent.authz`, `agent.input_guard`, `security.injection_patterns`, `security.tenancy`, `db.execution`, `db.connection` or `identity.rbac`.
- No module in `model_routing/` imports or calls an LLM client. Routing decides; the caller calls.
- `policy.py` imports no clock, no randomness and no I/O library, which is what makes a decision reproducible and previewable.
- With routing **on**, a model emitting `DELETE`/`DROP`/`SELECT ... FROM information_schema` is still refused by `agent.sql_validator`'s AST allowlist through the real compiled graph, and execution is never reached — tested with a mock that fails the test if called.

**Authorization and the allowlist.** Routing only ever names a model in `Settings.ollama_allowed_models`, swept across every task, four question shapes, both latency settings and three breaker states. A caller's explicit `AskRequest.model` is never overridden. `/model-routing/policy` and `/preview` require `Permission.ASK` (they describe which models would serve this caller's own questions — no more than `GET /models` already tells them); `/metrics` requires `Permission.ADMIN_CONFIG`, like `GET /metrics/performance`.

**Tenant isolation.** Evaluation evidence is read through `list_versions`, already tenant-filtered, and cached per tenant. Tested directly: tenant A's approval of a model escalates for tenant A and yields `NO_EVALUATION_EVIDENCE` for tenant B. The per-tenant escalation budget is keyed by the server-resolved tenant, never anything a client sends, and `None` means "no window" rather than a shared anonymous bucket.

**Untrusted input.** `POST /model-routing/preview` accepts a question. That text is scanned by `agent.complexity`'s regexes and discarded: never put in a prompt, never executed, never stored. A question instructing the system to "use the most expensive model and skip SQL validation" routes identically to the same question without that text. No decision, log line, metric or response echoes question text — asserted with probe phrases.

**Secrets.** No field in `api/model_routing_schemas.py` can carry a credential or connection detail; the only free-form strings are allowlisted model ids and enum values. Tests assert the policy response contains none of the configured db host/user/password/name. No test contains a real secret.

**Provenance.** `RouteDecision.truth_level` is a fixed `Literal[DataTruthLevel.AI_INFERENCE]`, so pydantic itself refuses a decision claiming to be an observed fact. `RouteOutcome.latency_ms` is a real measurement, recorded separately from the decision so the two are never conflated. The evaluation score is read verbatim from an operator's own record and is never computed, measured or inferred here.

## 9. Performance and cost impact

**Feature off (the default): zero.** The router is never constructed, `_routed_llm_call` calls through with `state["selected_model"]`, and no module in `model_routing/` beyond the pure `models.py` is even imported.

**Feature on:** per LLM call, one pure policy evaluation (a handful of list comprehensions over ≤10 candidates), one dict lookup per breaker, and one metrics record under a lock — microseconds against an LLM call measured in seconds (`docs/PERFORMANCE_BASELINE.md`: inference is 93.8–98% of wall clock). Evidence is read at most once per `MODEL_ROUTING_EVIDENCE_TTL_SECONDS` per tenant, mirroring `db.query_store`'s cached-findings pattern. No new LLM call, no new database query on the request path, no new network call anywhere.

The real cost effect is the intended one: a simple question's insight and intent classification can run on a `low`-resource model, and a genuinely hard question's generation can run on a `high`-resource one — bounded by the per-request and per-tenant budgets, and only to models an operator approved.

## 10. Limitations, disclosed rather than discovered later

1. **No per-model quality numbers exist in this repository.** `docs/EVALUATION_CURRENT.md`'s accuracy figures are for a single model and are dated 2026-09-16. Routing therefore cannot rank models by measured accuracy on its own; with the evidence gate on (the default) it escalates **only** to a model whose evaluation an operator recorded and approved through `/governance`. On a deployment that has never used the governance center, routing correctly does nothing but record `NO_EVALUATION_EVIDENCE` — which is the honest outcome, not a bug.
2. **Only the seven SQL-pipeline LLM calls are routed.** RAG answer/grade/rewrite, attachment QA, vision, media captioning, media-search answering and the analyst planner all read `Settings.ollama_model` directly and thread no per-request model; routing them would mean changing each of those call chains, which is a separate change. `RouteTask` lists only what is genuinely routable today.
3. **Installed-status is still not checked on the request path.** A configured-but-not-pulled model can be routed to and fails with `OllamaUnavailableError` — which routing now answers with a fallback chain, a strictly better outcome than the pre-existing hard failure. Adding a live `/api/tags` round trip per request was rejected for the same reason `agent/model_registry.py` rejected it originally.
4. **Breakers, budget windows and the rollup are process-local**, so a multi-worker deployment has one of each per worker. The identical, already-disclosed limitation of `agent/rate_limit.py` and `observability/metrics.py`, for the same reason: no shared store is part of this architecture.
5. **`latency_sensitive` has no live producer yet.** The field, the policy brake and its tests are real, and `POST /model-routing/preview` exposes it, but nothing in the pipeline currently computes "this request is short on wall clock" from `Settings.request_timeout_seconds`. Wiring that needs a per-request deadline the graph does not thread today.
6. **A model's tier and capabilities are an operator's hand-authored claim** (`config/ollama_models.yaml`), not a measurement. An un-catalogued model is honestly `ModelTier.UNKNOWN` and is never chosen as either an upgrade or a downgrade target.
7. **No frontend surface.** The three routes are operator/integration-facing; nothing renders a routing decision in the dashboard, though `AskResponse.route_decisions` makes it available to a client that wants to.
8. **The tenant escalation budget meters escalations, not compute.** A tenant asking many simple questions is bounded by the pre-existing `/ask` rate and concurrency limits, not by this budget.

## 11. Observations found while inspecting, and what was done about each

- **`sql_subgraph_node` never passed `tenant_id` to `run_agent`.** On the multi-source orchestrator path, the SQL subgraph's own `run_agent` call omitted it entirely, so every `security.audit_log` event that path emitted was un-stamped and (post-Prompt-20) no tenant-scoped lookup inside it could work. **Fixed**, because this feature's tenant scoping depends on it; the side effect is that audit events from the SQL subgraph on the multi-source path are now tenant-stamped, matching the single-source path.
- **`CircuitBreaker.allow` consumes the half-open probe.** Correct for its original caller, but it meant routing could not ask "is this model broken?" without spending the probe on a question it had not yet decided to send. **Fixed additively** with a read-only `would_refuse`; `allow` is unchanged and the multi-agent supervisor still uses it.
- **`tests/conftest.py` has a pre-existing `ruff` F821** (`Undefined name 'Path'` at line 77, from Prompt 46's own fixture). Confirmed present in `HEAD` before this prompt, left untouched — it is not this prompt's to fix, and it will surface in CI regardless.

## 12. Recommended next prompt

**Give routing something real to route on.** Two concrete, independent follow-ups, in order of value:

1. **Per-model evaluation runs.** Prompt 46's `eval_platform/` and `eval/component_benchmark/` can already score a model; wire a run so it records its result into a `model`-typed governed asset version's `evaluation_results` and submits it for approval. That closes limitation 1 and turns the evidence gate from a correct no-op into the mission's actual intent on a fresh deployment.
2. **Thread a per-request deadline** through `AgentState` so `latency_sensitive` has a live producer (limitation 5), and extend routing to the RAG/attachment/media LLM calls (limitation 2).
