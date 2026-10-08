"""Cross-cutting regression for Prompt 43
(`43_SCHEMA_DRIFT_IMPACT_ANALYSIS_CONTRACT.md`) -- mirrors the pattern
`test_causal_never_asserts_causation.py`/`test_scenario_never_presents_
estimate_as_fact.py`/`test_optimization_never_presents_recommendation_as_
fact.py`/`test_predictive_never_presents_prediction_as_fact.py` already
establish for every other inference-producing engine in this series.

Two invariants, proven both at the data level and structurally:

1. Every `schema_drift.diff.SchemaDriftFact`/`semantic.intelligence
   .detect.Finding` this package produces carries the correct truth
   level -- `DATABASE_FACT` for a deterministic structural diff,
   `AI_INFERENCE` *only* for the column-rename heuristic. Nothing here
   ever reaches `CONFIRMED_BUSINESS_TRUTH` on its own.
2. `schema_drift/` and `api/schema_drift.py` never call a catalog-
   mutating function (create/publish/edit/rollback a `SemanticCatalogEntry`)
   -- "do not silently rewrite production definitions" is true by
   construction (no such call site exists), not by a runtime check that
   could be forgotten. A drift finding only ever *reports* which catalog
   entry is threatened (`schema_drift.impact`'s own `payload["impacted"]`);
   acting on that report is always a separate, human-initiated call to
   the existing, already-governed `POST /semantic-intelligence/entries
   /{entry_id}/rollback` route.
"""

from __future__ import annotations

import ast
import inspect

import schema_drift.diff
import schema_drift.engine
import schema_drift.findings
import schema_drift.impact
import schema_drift.reports
import schema_drift.snapshot_store
from agent.provenance import DataTruthLevel
from schema_drift.diff import DriftChangeKind, SchemaDriftFact
from schema_drift.findings import fact_to_finding

_CATALOG_MUTATING_NAMES = frozenset(
    {
        "create_entry",
        "update_draft_entry",
        "publish_entry",
        "review_catalog_entry",
        "request_catalog_entry_changes",
        "rollback_catalog_entry",
    }
)

_SCHEMA_DRIFT_MODULES = [
    schema_drift.diff,
    schema_drift.findings,
    schema_drift.impact,
    schema_drift.engine,
    schema_drift.reports,
    schema_drift.snapshot_store,
]


def _referenced_names(module: object) -> set[str]:
    """Every bare `Name` identifier referenced anywhere in a module's own
    source (not its imports' own internals) -- a cheap, exact way to
    prove "this module never calls function X" without relying on
    mocking/monkeypatch coverage, which could miss an added call site a
    test author forgot to exercise."""
    source = inspect.getsource(module)
    tree = ast.parse(source)
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


class TestTruthLevelsAreCorrectByConstruction:
    def test_every_deterministic_change_kind_is_database_fact(self):
        for kind in DriftChangeKind:
            if kind is DriftChangeKind.COLUMN_RENAMED:
                continue
            fact = SchemaDriftFact(
                kind=kind,
                table="A",
                column="x",
                previous_value="old",
                current_value="new",
                detail="x",
            )
            finding = fact_to_finding("default", fact)
            assert finding.truth_level == DataTruthLevel.DATABASE_FACT.value, kind

    def test_the_rename_heuristic_is_always_ai_inference(self):
        fact = SchemaDriftFact(
            kind=DriftChangeKind.COLUMN_RENAMED,
            table="A",
            column="new_name",
            previous_value="old_name",
            current_value="new_name",
            detail="renamed?",
            is_inferred=True,
            confidence=0.9,
        )
        finding = fact_to_finding("default", fact)
        assert finding.truth_level == DataTruthLevel.AI_INFERENCE.value

    def test_no_finding_this_package_produces_is_ever_confirmed_business_truth(self):
        for kind in DriftChangeKind:
            fact = SchemaDriftFact(
                kind=kind, table="A", column="x", previous_value="a", current_value="b", detail="x"
            )
            finding = fact_to_finding("default", fact)
            assert finding.truth_level != DataTruthLevel.CONFIRMED_BUSINESS_TRUTH.value


class TestNoCatalogMutationCallSiteExists:
    def test_no_schema_drift_module_references_a_catalog_mutating_function(self):
        for module in _SCHEMA_DRIFT_MODULES:
            names = _referenced_names(module)
            overlap = names & _CATALOG_MUTATING_NAMES
            assert not overlap, f"{module.__name__} references {overlap}"

    def test_the_api_route_module_never_mutates_a_catalog_entry(self):
        import api.schema_drift as api_module

        names = _referenced_names(api_module)
        overlap = names & _CATALOG_MUTATING_NAMES
        assert not overlap, f"api.schema_drift references {overlap}"

    def test_the_api_route_module_imports_only_read_only_catalog_functions(self):
        import api.schema_drift as api_module

        source = inspect.getsource(api_module)
        assert "from identity.repositories.semantic_catalog import" in source
        import_line = next(
            line
            for line in source.splitlines()
            if "from identity.repositories.semantic_catalog import" in line
        )
        imported = {name.strip() for name in import_line.split("import", 1)[1].split(",")}
        assert imported <= {"entry_to_snapshot", "list_entries"}
