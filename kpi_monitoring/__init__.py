"""Proactive KPI monitoring (Prompt 50,
`50_PROACTIVE_AI_ANALYST_CONTRACT.md`).

A new top-level package, a peer of `analytics/`, `data_quality/`,
`lineage/`, `schema_drift/`, `optimization/` and `model_routing/`. It
watches **governed** business metrics for meaningful movement,
investigates what moved, suppresses repeats and noise, and ranks what is
left by impact.

Module map:

- `models.py` -- typed, frozen vocabulary (policies, calendars, quiet
  periods, preferences, detected changes, evidence, alert bundles).
- `calendar.py` -- business-calendar filtering of a period series.
- `policy.py` -- validates a policy against the tenant's own *published*
  catalog metrics, and against restricted columns.
- `detect.py` -- comparison-period baselines plus
  `analytics.anomaly.detect_anomalies`, producing `DetectedChange`s.
- `evidence.py` -- `analytics.root_cause.investigate_root_cause`, mapped
  onto `ChangeEvidence` (baseline, affected dimensions, confidence,
  limitations).
- `findings.py` -- bridges a change + its evidence into Prompt 35's
  `semantic.intelligence.detect.Finding`, with the stable key that makes
  duplicate suppression work.
- `suppression.py` -- quiet periods, business calendar and per-user
  preferences, applied at alert time.
- `engine.py` -- the one orchestration entry point.
- `reports.py` -- the single markdown rendering shared by the API and
  the CLI.

**Heavy reuse, by design.** Detection is
`analytics.anomaly.detect_anomalies` (Prompt 14) verbatim; attribution
is `analytics.root_cause.investigate_root_cause` (Prompt 14) verbatim;
persistence, versioning, duplicate suppression and the review-queue
lifecycle are Prompt 35's `identity.models.SemanticFinding` +
`identity.repositories.semantic_intelligence` unmodified; ranking is
`semantic.intelligence.risk.score`, extended additively with KPI kinds
and an impact term. The same reuse `schema_drift/` already made of the
same storage -- this package is the third consumer of that review queue,
not a fourth place to put findings.

**What this package never does.** It never generates SQL, never executes
a query, never reads a database directly, and never calls an LLM. Every
series and every dimension breakdown is supplied by the caller, already
fetched through this application's own governed path -- the same
"neither route ever queries a database" posture
`analytics/scenario.py`, `optimization/` and `data_quality/` already
establish. That is what keeps the deterministic SQL/data controls in
`agent/sql_validator.py` untouched and un-bypassable by anything here.

**Three kinds of claim stay separate** (master rule 10): an observed
number is `DATABASE_FACT`; the judgment that it is *meaningful* is
`AI_INFERENCE`; an attribution to a dimension is a further inference
that exists only when real supporting evidence was found, and is absent
with a stated limitation otherwise.

**No delivery channel exists.** Nothing here sends an email, webhook or
chat message. A run produces a typed `AlertBundle` describing what
*would* be sent per recipient after suppression -- see that model's own
docstring.
"""
