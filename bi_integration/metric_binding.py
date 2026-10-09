"""Governed-metric binding validation -- the literal "reuse governed
metrics and permissions; do not create duplicate metric definitions"
mission requirement, Prompt 48.

**Deliberately ORM-independent and reuses `agent.sql_validator`'s own
already-tested table-extraction, never a second sqlglot implementation**
(mandatory control: "inspect and reuse existing implementation before
adding code"). `validate_governed_metric_binding` is a pure function --
the API layer (`api/bi_integration.py`) is responsible for resolving a
real, already-published `semantic.catalog.CatalogEntrySnapshot` (via
`identity.repositories.semantic_catalog`) before calling this, exactly
the same split `eval_platform.release_gate`/`governance_center.policy`
already establish between "fetch the real row" (the API layer) and
"decide against an already-fetched shape" (this module).
"""

from __future__ import annotations

from semantic.catalog import CatalogEntrySnapshot, CatalogStatus

from agent.sql_validator import DEFAULT_DIALECT, find_unexpected_table_references


def referenced_tables(sql: str, dialect: str | None = DEFAULT_DIALECT) -> frozenset[str]:
    """Every table `sql` references, lower-cased -- reuses
    `agent.sql_validator.find_unexpected_table_references` as a generic
    table extractor by passing an empty `known_tables` set, so *every*
    referenced table comes back as "unexpected" (there was nothing to
    compare against). This is deliberate reuse of that function's own
    already-tested sqlglot/CTE-aware table-walking logic, not a second
    implementation of it -- see that function's own docstring for the
    CTE-reference-exclusion detail this inherits for free.

    Returns an empty set (never raises) if `sql` doesn't parse -- the
    same "nothing to flag" posture `find_unexpected_table_references`
    itself already takes on a parse failure; SQL validity is
    `agent.sql_validator.validate_sql`'s own job, checked separately and
    earlier in every real call path.
    """
    return frozenset(name.lower() for name in find_unexpected_table_references(sql, set(), dialect))


def validate_governed_metric_binding(
    snapshot: CatalogEntrySnapshot | None,
    sql: str,
    dialect: str | None = DEFAULT_DIALECT,
) -> list[str]:
    """Returns every violation found (never just the first) binding
    `snapshot` to `sql` as a connector's governed-metric link -- an
    empty list means the binding is accepted. `snapshot is None` (no
    governed-metric link requested) always returns `[]`: linking a
    connector to a governed metric is optional, never required.

    Violations:
        - `snapshot.status != PUBLISHED`: only a published entry is
          `CONFIRMED_BUSINESS_TRUTH` (see `semantic.catalog
          .status_to_truth_level`) -- a draft/reviewed/superseded entry
          is still `AI_INFERENCE`/stale, and binding a connector to one
          would let an external BI tool present an unconfirmed
          definition as though it were approved.
        - Zero overlap between `snapshot.source_tables` and the real
          tables `sql` references: a **cheap, deliberately bounded
          sanity check**, not a proof that `sql` actually computes the
          metric's own `approved_expression` -- this module does not
          (and, consistent with `agent.nodes.review_metric_conformance
          _node`'s own disclosed scope, realistically cannot without an
          LLM judgment call this feature has no budget for) prove SQL-
          to-formula equivalence. It exists only to catch the clearest
          mismatch case: a connector claiming to expose "Net Revenue"
          whose SQL doesn't even touch any table that metric is defined
          over.
    """
    if snapshot is None:
        return []

    violations: list[str] = []
    if snapshot.status != CatalogStatus.PUBLISHED:
        violations.append(
            f"Governed metric {snapshot.concept_key!r} v{snapshot.version} is "
            f"{snapshot.status.value!r}, not published -- only a published entry "
            "may be linked to a connector."
        )

    if snapshot.source_tables:
        sql_tables = referenced_tables(sql, dialect)
        declared_tables = {t.lower() for t in snapshot.source_tables}
        if sql_tables and not (sql_tables & declared_tables):
            violations.append(
                f"Governed metric {snapshot.concept_key!r} declares source tables "
                f"{sorted(snapshot.source_tables)}, none of which this connector's own "
                f"SQL references ({sorted(sql_tables)}) -- refusing a binding this "
                "inconsistent."
            )

    return violations
