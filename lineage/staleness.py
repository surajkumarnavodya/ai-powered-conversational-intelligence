"""Stale-lineage detection -- a pure comparison, never a timestamp/wall-
clock check of its own.

Mirrors `data_quality.models.FreshnessRule`'s own disclosed discipline
("`as_of` is the caller's own reference now, never the server's wall
clock") applied to a different kind of freshness: a previously-built
`lineage.models.LineageGraph` records a `schema_fingerprint`
(`db.schema_introspection.get_schema_fingerprint`'s own deterministic
hash, reused verbatim) and a `catalog_versions` map at the moment it was
built. `detect_staleness` never re-fetches either side itself -- the
caller (`api/lineage.py`'s `/stale-check` route, the one place in this
feature that re-introspects/re-fetches live) supplies both the graph's
own recorded state and the current live state, and this function is a
pure, deterministic comparison between them.
"""

from __future__ import annotations

from collections.abc import Mapping

from lineage.models import LineageGraph, LineageStalenessFinding, LineageStalenessReport


def detect_staleness(
    graph: LineageGraph,
    current_schema_fingerprint: str,
    current_catalog_versions: Mapping[str, int],
) -> LineageStalenessReport:
    """Compares `graph`'s own recorded `schema_fingerprint`/
    `catalog_versions` against the caller-supplied current live values.

    Three independent kinds of staleness are reported, each a separate
    finding (never collapsed into one undifferentiated boolean):
      - the live schema's own fingerprint no longer matches what this
        graph was built from (a table/column was added, removed, or
        changed shape since);
      - a catalog concept this graph linked has since moved to a
        *different* version (a metric definition was edited/republished);
      - a catalog concept this graph linked no longer exists at all in
        the current live set (removed/renamed).
    """
    findings: list[LineageStalenessFinding] = []

    if (
        graph.schema_fingerprint is not None
        and graph.schema_fingerprint != current_schema_fingerprint
    ):
        findings.append(
            LineageStalenessFinding(
                kind="schema_changed",
                detail=(
                    f"The live schema's fingerprint ({current_schema_fingerprint[:12]}...) no "
                    f"longer matches this graph's own recorded fingerprint "
                    f"({graph.schema_fingerprint[:12]}...) -- the database's schema has "
                    "changed since this lineage graph was built."
                ),
            )
        )

    for concept_key, recorded_version in graph.catalog_versions.items():
        current_version = current_catalog_versions.get(concept_key)
        if current_version is None:
            findings.append(
                LineageStalenessFinding(
                    kind="catalog_entry_removed",
                    detail=(
                        f"Governed concept '{concept_key}' (v{recorded_version} at build time) "
                        "no longer has a published entry."
                    ),
                    node_id=concept_key,
                )
            )
        elif current_version != recorded_version:
            findings.append(
                LineageStalenessFinding(
                    kind="catalog_entry_version_changed",
                    detail=(
                        f"Governed concept '{concept_key}' has moved from v{recorded_version} "
                        f"(recorded in this graph) to v{current_version} (currently published)."
                    ),
                    node_id=concept_key,
                )
            )

    return LineageStalenessReport(is_stale=bool(findings), findings=tuple(findings))


__all__ = ["detect_staleness"]
