"""Governed model routing and cost optimization (Prompt 47,
`47_MODEL_ROUTING_COST_OPTIMIZATION_CONTRACT.md`).

A new top-level package, a peer of `analytics/`, `optimization/`,
`data_quality/`, `lineage/`, `schema_drift/` and `governance_center/`.
It decides **which locally-installed Ollama model drafts each LLM call
the SQL pipeline makes**, from signals this pipeline already computes,
and records what that choice actually cost in latency, failures and
compute units.

Module map:

- `models.py` -- typed, frozen vocabulary (tasks, tiers, risk, decisions,
  outcomes) plus the static per-task risk/capability tables.
- `catalog.py` -- turns `Settings.ollama_allowed_models` plus
  `config/ollama_models.yaml` into the candidate set.
- `evidence.py` -- reads approved/deployed `model`-typed governed asset
  versions (`governance_center`, Prompt 44) so an escalation target must
  have real, recorded evaluation evidence behind it.
- `signals.py` -- builds a `RequestProfile` from
  `agent.complexity.detect_complexity_signals` and `agent.intent`'s own
  classification; adds no new classifier and makes no LLM call.
- `policy.py` -- the pure, deterministic decision function.
- `budget.py` -- per-request compute-unit budget and a per-tenant hourly
  escalation cap.
- `metrics.py` -- per-route quality/latency/failure/cost rollup.
- `router.py` -- the stateful façade `agent/nodes.py` calls: policy +
  circuit breaker + budget, then outcome recording and fallback.

**What this package is not.** It never validates SQL, never checks a
permission, never resolves a tenant, and never decides whether a result
may be shown. Those are deterministic controls that live in
`agent/sql_validator.py`, `agent/authz.py` and `security/tenancy.py`,
and nothing here imports them. Routing changes *who drafts*, never *what
is allowed* -- which is the structural form of this prompt's own "never
use an LLM for deterministic security validation" requirement.

**Off by default.** `Settings.enable_model_routing` is `False`, so an
untouched deployment keeps exactly the pre-Prompt-47 behavior: one model
for every call in a run (`Settings.ollama_model`, or the caller's own
validated `AskRequest.model`). A caller's explicit model choice always
wins over routing, whatever the flag says.
"""
