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

Before adding a new file here, check whether the scenario already has
coverage under its own module's `tests/test_<module>.py` first — this
directory is for genuinely cross-cutting adversarial scenarios or brand
-new security capabilities, not a place to relocate existing tests.
