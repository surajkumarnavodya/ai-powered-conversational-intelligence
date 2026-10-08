"""Continuous schema-drift detection and lineage-based impact analysis --
Prompt 43 (`43_SCHEMA_DRIFT_IMPACT_ANALYSIS_CONTRACT.md`).

Compares two schema snapshots of one configured database (the last one
this module captured vs. a fresh live introspection) and reports every
added/removed/renamed column, type/nullability change, key/relationship
change, and table/view change as a typed, deterministic
`schema_drift.diff.SchemaDriftFact` -- every one of these is a
`DATABASE_FACT` (a measurement of two real introspected snapshots), except
the column-rename heuristic, which is always disclosed as `AI_INFERENCE`
(two independent facts paired by a name-similarity guess, never asserted
as the database's own claim).

**Deliberately reuses Prompt 35's (`semantic/intelligence/`) governed
review-queue storage rather than building a second one.**
`identity.models.SemanticFinding` + `identity.repositories
.semantic_intelligence` (`upsert_findings`/`list_findings`/`get_finding`/
`decide_finding`/`rollback_finding`) are already a generic, kind-agnostic,
tenant-scoped, versioned (append-only `history`) review-queue with an
accept/dismiss lifecycle and a "rollback to an earlier version" action --
exactly the "approval-aware remediation" shape this prompt's own mission
calls for, and `semantic.intelligence.detect.Finding` is already a
domain-agnostic dataclass (kind/key/title/detail/subjects/evidence/
confidence/truth_level/risk/payload). `schema_drift.findings` wraps this
module's own structural facts into that exact same `Finding` shape (new
`kind` values, prefixed `schema_drift_...` so they're never confused with
a catalog-detector finding sharing the same table), and `api/schema_drift
.py`'s own `/scan`/`/findings` routes call the *same* repository functions
Prompt 35's API already does. **A schema-drift finding's decision/rollback
goes through the existing, already-built, already-tested
`POST /semantic-intelligence/findings/{id}/decision` and `.../rollback`
routes** -- `identity.repositories.semantic_intelligence.decide_finding`/
`rollback_finding` operate on any `SemanticFinding` row by id, regardless
of which detector produced it, so duplicating a second pair of routes
here would be the exact kind of redundant reimplementation master rule 2/3
forbids. `schema_drift/`'s own API surface is deliberately narrower:
only `/scan` (genuinely new behavior) and `/findings` (a kind-scoped,
read-only convenience view over the same shared table).

**"Metadata rollback" is the existing `POST /semantic-intelligence
/entries/{entry_id}/rollback` route** (`identity.repositories
.semantic_intelligence.rollback_catalog_entry`, also Prompt 35) -- creates
a brand-new `DRAFT` from an older published version's own content; the
published row is never touched, and the draft still needs the catalog's
normal review-and-publish workflow before it goes live. This prompt's own
`schema_drift.impact` module surfaces exactly which catalog entry
id/concept_key/version a drift finding threatens, so an operator knows
what to roll back -- it never calls that route itself, and never
auto-publishes or auto-edits a catalog entry on its own. **Nothing in this
package writes to a catalog entry, a golden question, a dashboard
specification, or a persisted query.** Every "impact" this package reports
is read-only lineage-trace output (`lineage.graph.trace_lineage`,
Prompt 42) -- a report, never a silent rewrite.

**Snapshot capture (`schema_drift.snapshot_store`) is new** -- nothing
else in this codebase persists a full column-level historical schema
snapshot (`embeddings/schema_indexer.py`'s own manifest only stores a
per-table *fingerprint*, not the column/type/nullability/key detail a
real diff needs). Stored as a small per-database JSON file alongside the
Chroma persist directory, mirroring that manifest's own file-naming
convention -- not a new identity-DB table, since this is purely an
operational cache (the last snapshot this module compared against), not
governed content.
"""
