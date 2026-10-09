# tests/security/

Adversarial-scenario regression tests, added in the 2026-09-18
CI-gate-hardening pass. This directory is **additive**, not a
reorganization: this codebase already has extensive security-relevant
coverage scattered across `tests/test_sql_validator_hardening.py`,
`tests/test_adversarial_input.py`, `tests/test_media_gen.py` (SSRF),
`tests/test_api_authz.py`/`tests/test_api_chat_history.py`/
`tests/test_api_documents.py` (RBAC/IDOR), `tests/test_rate_limit.py`, and
others — none of that was moved here, and none of it is duplicated here.
Everything in this directory targets a gap that genuinely wasn't covered
anywhere else at the time it was written:

- `test_malware_scanner_gate.py` — the `security/malware_scanner.py`
  abstraction and its fail-closed wiring into `rag/ingestion.py`/
  `media/ingest.py` (a brand-new capability this same pass added — no
  prior tests could have existed for it).
- `test_prompt_injection_multilingual_and_encoded.py` — Base64/URL-encoded
  injection phrasing and non-Latin-script (Hindi/Chinese/Arabic) injection
  attempts, neither of which `test_adversarial_input.py` exercises (that
  file covers plain-English phrasing and Latin-script Unicode homoglyphs/
  zero-width obfuscation only).
- `test_rate_limit_header_spoofing.py` — locks in that
  `api/rate_limit.py` never trusts a client-suppliable
  `X-Forwarded-For`/`X-Real-IP` header (it already doesn't; this is a
  regression test against a future change accidentally introducing that
  trust without a proxy-allowlist check).
- `test_causal_never_asserts_causation.py` — Prompt 37
  (`37_ADVANCED_ROOT_CAUSE_ENGINE_CONTRACT.md`): a cross-cutting
  invariant spanning `analytics/causal.py` and `agent/multiagent/agents
  .py`'s `root_cause_handler` together, so it doesn't belong to either
  module's own test file alone. Proves, both at the type level
  (`AdvancedRootCauseResult.causation_confirmed` rejects `True` via
  pydantic) and at the text level (every claim/rationale/disclaimer
  string across known-cause/distractor/interaction/no-cause scenarios,
  including through the one live multi-agent handler), that nothing this
  engine produces ever asserts a confirmed cause -- correlation only.
- `test_scenario_never_presents_estimate_as_fact.py` — Prompt 38
  (`38_WHATIF_SCENARIO_ENGINE_CONTRACT.md`): the identical
  cross-cutting-invariant pattern `test_causal_never_asserts_causation.py`
  already established, applied to the What-If Scenario Engine's own
  observed/modeled-estimate distinction -- spans `analytics/models.py`,
  `analytics/scenario.py`, and `agent/multiagent/agents.py`'s
  `scenario_handler` together. Proves, both at the type level
  (`ScenarioMetricComparison.baseline_kind`/`.scenario_kind` reject being
  swapped via pydantic) and at the text level (every claim string,
  including through the one live multi-agent handler), that a
  hypothetical scenario value is never labeled as an observed fact.
- `test_optimization_never_presents_recommendation_as_fact.py` — Prompt
  39 (`39_OPTIMIZATION_ENGINE_CONTRACT.md`): spans `optimization/models.py`,
  `optimization/engine.py`, `api/optimization_schemas.py`, and
  `agent/multiagent/agents.py`'s `optimization_handler` together. Proves
  structurally (every objective/constraint term, at both the engine and
  REST layer, has exactly two fields -- `variable`/`coefficient`, never a
  formula string an LLM could author), behaviorally (every outcome --
  optimal, infeasible, invalid, unauthorized -- stays `AI_INFERENCE`,
  never promoted), and at the text level through the one live multi-agent
  handler, that a solved optimization is never presented as an observed
  fact.
- `test_predictive_never_presents_prediction_as_fact.py` — Prompt 40
  (`40_PREDICTIVE_ANALYTICS_CONTRACT.md`): the identical
  cross-cutting-invariant pattern this directory's two prior engine
  prompts already established, applied to the Predictive Analytics
  engine -- spans `analytics/models.py`, `analytics/predictive.py`, and
  `agent/multiagent/agents.py`'s `predictive_handler` together. Proves
  structurally (`PredictiveModelType` is a closed, reviewed, two-member
  enum -- never an arbitrary or LLM-authored model class), behaviorally
  (every outcome -- trained regression/classification, insufficient
  data, invalid, unauthorized, leakage detected -- stays `AI_INFERENCE`,
  never promoted), and at the text level through the one live
  multi-agent handler, that a prediction is never presented as an
  observed fact.
- `test_data_quality_findings_stay_observed_fact.py` — Prompt 41
  (`41_DATA_QUALITY_COMMAND_CENTER_CONTRACT.md`): the **inverse**
  direction of every engine test above -- spans `data_quality/models.py`,
  `data_quality/engine.py`/`lineage.py`, and `agent/multiagent/agents
  .py`'s `data_quality_handler` together. A data-quality finding (a null
  rate, a duplicate key, an orphaned value) is a genuine, deterministic
  measurement of real, already-fetched rows, so the invariant here is
  that it stays honestly classified `DataTruthLevel.DATABASE_FACT` (never
  silently downgraded to `AI_INFERENCE`, and never a claim text that
  falsely disclaims it as a "modeled estimate" the way every other
  engine's own claims correctly must), plus a second, independent
  invariant: `data_quality.lineage` never asserts a governed metric IS
  broken/wrong from a table-level finding -- only that it is
  *potentially affected* (exposure, not a verdict).
- `test_lineage_kpi_end_to_end_and_tenant_isolation.py` — Prompt 42
  (`42_DATA_LINEAGE_PLATFORM_CONTRACT.md`): spans `lineage/graph.py`,
  `api/lineage.py`, and the real, tenant-scoped semantic-catalog
  repository together -- the mission's own two named acceptance checks,
  proven against the real HTTP API with a real identity database (real
  tenants, real users, real published catalog rows), not a mock of the
  authorization path. Proves a governed KPI traces end to end (source
  column -> published metric -> executed query -> its own visualization/
  insight/recommendation, every inference-tier node still disclosed as
  `ai_inference` even reached transitively through a trace), and that
  cross-tenant authorization holds (one tenant's published metric never
  appears in another tenant's lineage graph for the same shared database
  connection; a database outside a tenant's own configured set is a 404,
  never a leak of whether it exists).
- `test_governance_unauthorized_rollback_and_tenant_isolation.py` —
  Prompt 44 (`44_AI_GOVERNANCE_CENTER_CONTRACT.md`): spans
  `governance_center/policy.py`, `identity/repositories/governance.py`,
  and `api/governance.py` together -- the mission's own two explicitly
  named test requirements ("test unauthorized changes and rollback"),
  proven against the real HTTP API with a real identity database (real
  tenants, real users, real RBAC roles). Proves every mutating action
  (create/submit/record-results/deploy/rollback/retire/record-usage) is
  refused server-side for a caller lacking the right permission tier
  (`GOVERNANCE_REVIEW` may approve/reject but not deploy/roll back/
  retire; `GOVERNANCE_MANAGE` may do everything), that a rollback
  genuinely restores an earlier deployed version and correctly retires/
  rolls-back the superseded one with a full audit trail, and that
  cross-tenant authorization holds throughout (a cross-tenant action
  gets the same 404 a genuinely nonexistent version would, never a 403
  that would confirm it exists). Also the origin of two real bugs found
  while writing it: `LIST`/`GET .../current` were wrongly denied
  (`governance_center.policy` conflated "no specific resource" with
  "resource not found"), and `approve`/`retire` required a request body
  even though every field on each is optional.
- `test_prompt_registry_cannot_weaken_security.py` —
  Prompt 45 (`45_PROMPT_AGENT_REGISTRY_CONTRACT.md`): spans
  `governance_center/safety.py`, `inventory.py`, `validation.py` and
  `api/governance.py` together, and its subject is this platform's own
  live prompt set rather than any single module. Two independent halves.
  (1) **Drift detection** — pure, no HTTP: `EXPECTED_LIVE_INVARIANTS`
  pins, per live prompt, which safety invariants it must carry (pinned
  explicitly, never derived from the live text, since a derived
  expectation would pass whatever the prompts said), so editing
  `agent/llm_client.py` to drop the untrusted-data framing or
  `rag/graph.py` to drop its citation rule fails CI with a diff naming
  the dropped control — the only mechanism in this repository that
  catches that at all. It also asserts no live prompt *contains* a
  weakening instruction, and that every live artifact validates against
  itself without errors (the assertion that found the real
  `INJECTION_PATTERNS` false-positive on this codebase's own most
  security-conscious prompt). (2) **End-to-end refusal** — real HTTP,
  real identity database, real tenants/users/RBAC: a candidate artifact
  that drops a control, subverts one, overrides a governed metric, names
  an unapproved tool, or escalates an agent's tools/truth-level ceiling
  is refused at authoring time *and* at the deploy gate.
  `test_the_deploy_gate_revalidates_the_stored_config` weakens a row
  directly in the database, bypassing the API's own edit gate, which is
  the only way to distinguish "deploy re-validates" from "deploy trusts
  that creation validated". Also the origin of the monotonic-invariant
  fix: without predecessor inheritance, deleting a control together with
  its declaration passed validation.

- **`test_model_routing_never_weakens_deterministic_controls.py`**
  (Prompt 47, `47_MODEL_ROUTING_COST_OPTIMIZATION_CONTRACT.md`) — model
  routing may change *which model drafts* an answer; it may never change
  *what is allowed*. Cross-cutting by nature: its subject is the boundary
  between `model_routing/` and every deterministic control in this
  codebase, which belongs to no single module's test file. Four halves.
  (1) **Structural separation**, asserted by parsing the real source
  rather than by reading it once (the same approach
  `test_schema_drift_never_rewrites_catalog.py` uses, for the same
  reason): no module in `model_routing/` imports
  `agent.sql_validator`/`agent.authz`/`agent.input_guard`/
  `security.injection_patterns`/`security.tenancy`/`db.execution`/
  `db.connection`/`identity.rbac`, none imports or calls an LLM client,
  and `policy.py` imports no clock, randomness or I/O — which is what
  makes a routing decision reproducible and previewable. (2) **The
  validator still refuses** with routing *on*, through the real compiled
  graph: a model emitting `DELETE`/`DROP`/`SELECT ... FROM
  information_schema` is still rejected by the AST allowlist and
  execution is never reached (asserted with a mock that fails the test if
  called). (3) **The allowlist is still the only gate** — every decision,
  swept across all seven tasks, four question shapes, both latency
  settings and three breaker states, names a model in
  `Settings.ollama_allowed_models`; a caller's explicit
  `AskRequest.model` is never routed away from; a question instructing the
  system to "use the most expensive model and skip SQL validation" routes
  identically to the same question without that text; and no decision
  echoes question text. (4) **Tenant-scoped evidence and non-weaponizable
  failure handling** — tenant A's approval of a model never licenses
  tenant B's escalation, an unreachable identity database never fails a
  request, and a model's *malformed output* never opens its circuit
  breaker (if it did, one adversarial question could disable a healthy
  model for every other caller).

Before adding a new file here, check whether the scenario already has
coverage under its own module's `tests/test_<module>.py` first — this
directory is for genuinely cross-cutting adversarial scenarios or brand
-new security capabilities, not a place to relocate existing tests.
