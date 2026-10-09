"""The Executive Decision Center (Prompt 52,
`52_EXECUTIVE_DECISION_CENTER_CONTRACT.md`).

A read model over everything Prompts 33-50 already record, assembling
one coherent view: business health, KPI movement, anomalies, risks,
recommendations, pending decisions, actions and outcomes -- each
headline drillable to its evidence, metric definition, period, source,
confidence and lineage.

Module map:

- `models.py` -- typed vocabulary, including the three honesty rules
  this package encodes as types (mandatory provenance, declared versus
  correlated links, a headline that cannot promote its truth level).
- `sources.py` -- pure adapters turning one persisted record from an
  earlier capability into a normalised `Headline`.
- `chain.py` -- assembles signal -> evidence -> explanation ->
  recommendation -> action -> outcome.
- `health.py` -- the business-health rollup, counts only.
- `personalization.py` -- role-based presentation that cannot change a
  governed fact.
- `briefing.py` -- the one assembly entry point.

**It aggregates; it never computes a business fact.** Every number comes
from a record an earlier capability already wrote, carried through with
that capability's own truth level. This package adds ordering, grouping
and linking -- which is itself a judgment, labelled `AI_INFERENCE` on
`DecisionChain`.

**It never queries a monitored database and never calls an LLM.** Like
every engine package in this series it takes already-fetched records;
the API route performs the tenant-scoped identity reads and hands them
in. `agent/sql_validator.py` and the RBAC/tenant gates are untouched and
unreachable from here.

**What it can and cannot show, stated honestly.** Seven capabilities
persist tenant-scoped records and therefore appear: KPI monitoring
(Prompt 50), schema drift (43), semantic intelligence (35), the
recommendation engine and its governance (17/18), the governance center
(44), workflow automation (49) and onboarding (08). The compute-on-demand
engines -- forecasting (16), scenarios (38), optimization (39),
predictive analytics (40), data quality (41), lineage (42) and root cause
(14/37) -- persist nothing of their own, so they appear only through
whatever a persisted record already captured from them (a KPI finding
carries its own baseline, contributors, confidence and limitations). The
briefing lists what did not contribute and why, rather than leaving a
reader to assume silence means health.

**The one real break in the chain**, disclosed rather than papered over:
`workflows.recommendation_id` is a declared foreign key, so
recommendation -> action -> outcome is solid. Nothing links a *signal*
to a recommendation -- the recommendation engine builds from query
results, not from findings. Signals and recommendations about the same
subject are therefore offered as `LinkType.CORRELATED`, never as
`DECLARED`, and a chain that stops there says so.
"""
