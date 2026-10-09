"""Executive AI briefing (Prompt 51,
`51_EXECUTIVE_AI_BRIEFING_CONTRACT.md`).

A new top-level package, a peer of `analytics/`, `data_quality/`,
`kpi_monitoring/`, `lineage/`, `schema_drift/` and `recommendation/`. It
assembles an evidence-linked daily, weekly or monthly briefing from what
those subsystems have already recorded: KPI movement, significant
anomalies, drivers, risks, opportunities, unresolved recommendations and
data-quality issues.

Module map:

- `models.py` -- typed, frozen vocabulary (`ClaimClass`,
  `EvidenceLink`, `BriefingItem`, `SourceCoverage`, `StaleInput`,
  `UnsupportedClaim`, `ExecutiveBriefing`).
- `inputs.py` -- the storage-free projections of the governed records
  this package reads, so nothing here imports `identity` or
  `sqlalchemy`.
- `freshness.py` -- staleness assessment against a caller-supplied
  reference instant. Never reads a clock.
- `claims.py` -- one builder per source, plus the evidence gate
  (`find_unsupported_claims`/`finalize`) that is the only place a
  `BriefingItem` is ever constructed.
- `engine.py` -- the one orchestration entry point.
- `reports.py` -- the single markdown rendering.

**Heavy reuse, by design.** Nothing here detects, computes or ranks
anything a governed subsystem already did: KPI movement, anomaly flags
and dimension attribution come from `kpi_monitoring` findings already
persisted in Prompt 35's shared review queue; schema-drift impact comes
from `schema_drift.impact`'s own already-resolved impacted-concept list;
unresolved recommendations come from `identity.models
.RecommendationRecord` with their own `engine_version`/
`evidence_version` snapshots; data-quality findings come from
`data_quality.engine`. Ranking reuses the risk score
`semantic.intelligence.risk.score` already assigned, and number
grounding reuses `agent.insight.extract_numbers` verbatim.

**What this package never does.** It never generates SQL, never executes
a query, never reads a monitored database and never calls an LLM --
every record arrives as an argument, already fetched through this
application's own governed, authorized path. That is what keeps the
deterministic SQL/data controls in `agent/sql_validator.py` untouched
and un-bypassable by anything here. It also persists nothing: a briefing
is a read of governed state, not a new governed artifact.

**Three kinds of statement stay separate** (master rule 10):
`ClaimClass.OBSERVED_FACT` is arithmetic over fetched rows
(`DATABASE_FACT`); `ESTIMATE` is this platform's own inference about
those numbers; `RECOMMENDATION` is a suggestion awaiting a decision.
Both inference classes are `AI_INFERENCE`, and `BriefingItem`'s own
model validator makes the mismatched combinations unrepresentable.
`CONFIRMED_BUSINESS_TRUTH` appears nowhere -- confirming anything stays
a separate, explicit human action elsewhere in this codebase.

**Every claim links to deterministic evidence and versions**, enforced
twice: `BriefingItem.evidence` has `min_length=1`, and
`briefing.claims.find_unsupported_claims` withholds (and reports) any
candidate with no evidence, a version-less link, a claim-class/truth-
level mismatch, or a number its own evidence does not contain.
"""
