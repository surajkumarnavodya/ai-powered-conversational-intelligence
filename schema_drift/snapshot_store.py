"""Persists the last schema snapshot `schema_drift.diff.compare_schemas`
compared against, per configured database -- the "previous" side of every
later scan's diff.

A small, per-database JSON file alongside the Chroma persist directory
(`.schema_drift_snapshot__<db_name>.json`), mirroring `embeddings
.schema_indexer._manifest_filename`'s own naming convention exactly. Not
a new identity-DB table: this is an operational cache of the last-seen
schema shape, not governed content -- the governed artifact a scan
produces (the findings themselves) is persisted via Prompt 35's own
`identity.models.SemanticFinding` table instead (see `schema_drift
/__init__.py`'s own docstring).

Fails open on a missing/corrupt file (treated as "no previous snapshot,"
the same "a first scan has nothing to diff against" contract
`schema_drift.diff.compare_schemas` already documents) -- never raises.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from config.settings import Settings, get_settings
from db.schema_introspection import ColumnInfo, ForeignKeyInfo, TableSchemaInfo

logger = logging.getLogger(__name__)


def _snapshot_path(database_id: str, settings: Settings) -> Path:
    return settings.chroma_persist_dir / f".schema_drift_snapshot__{database_id}.json"


def _column_to_dict(column: ColumnInfo) -> dict:
    return {
        "name": column.name,
        "type": column.type,
        "nullable": column.nullable,
        "is_primary_key": column.is_primary_key,
        "default": column.default,
        "is_computed": column.is_computed,
        "is_identity": column.is_identity,
        "length": column.length,
        "precision": column.precision,
        "scale": column.scale,
    }


def _column_from_dict(data: dict) -> ColumnInfo:
    return ColumnInfo(
        name=data["name"],
        type=data["type"],
        nullable=data["nullable"],
        is_primary_key=data["is_primary_key"],
        default=data.get("default"),
        is_computed=data.get("is_computed", False),
        is_identity=data.get("is_identity", False),
        length=data.get("length"),
        precision=data.get("precision"),
        scale=data.get("scale"),
    )


def _fk_to_dict(fk: ForeignKeyInfo) -> dict:
    return {
        "constrained_columns": list(fk.constrained_columns),
        "referred_table": fk.referred_table,
        "referred_columns": list(fk.referred_columns),
    }


def _fk_from_dict(data: dict) -> ForeignKeyInfo:
    return ForeignKeyInfo(
        constrained_columns=tuple(data["constrained_columns"]),
        referred_table=data["referred_table"],
        referred_columns=tuple(data["referred_columns"]),
    )


def _table_to_dict(table: TableSchemaInfo) -> dict:
    return {
        "table_name": table.table_name,
        "columns": [_column_to_dict(c) for c in table.columns],
        "foreign_keys": [_fk_to_dict(fk) for fk in table.foreign_keys],
        "ddl": table.ddl,
        "is_view": table.is_view,
        "row_count_estimate": table.row_count_estimate,
        "unique_constraints": [list(columns) for columns in table.unique_constraints],
    }


def _table_from_dict(data: dict) -> TableSchemaInfo:
    return TableSchemaInfo(
        table_name=data["table_name"],
        columns=tuple(_column_from_dict(c) for c in data["columns"]),
        foreign_keys=tuple(_fk_from_dict(fk) for fk in data["foreign_keys"]),
        ddl=data["ddl"],
        is_view=data.get("is_view", False),
        row_count_estimate=data.get("row_count_estimate"),
        unique_constraints=tuple(tuple(columns) for columns in data.get("unique_constraints", [])),
    )


def load_snapshot(
    database_id: str, settings: Settings | None = None
) -> list[TableSchemaInfo] | None:
    """Reads back the last snapshot captured for `database_id`, or `None`
    if none exists yet or the file is unreadable/corrupt -- never raises."""
    settings = settings or get_settings()
    path = _snapshot_path(database_id, settings)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        tables = data.get("tables") if isinstance(data, dict) else None
        if not isinstance(tables, list):
            return None
        return [_table_from_dict(entry) for entry in tables]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Could not read schema-drift snapshot for %r: %s", database_id, exc)
        return None


def save_snapshot(
    database_id: str, tables: list[TableSchemaInfo], settings: Settings | None = None
) -> None:
    """Overwrites the stored snapshot for `database_id` with `tables` --
    always the *current* (just-diffed-against) shape, so the next scan
    diffs from here."""
    settings = settings or get_settings()
    settings.chroma_persist_dir.mkdir(parents=True, exist_ok=True)
    path = _snapshot_path(database_id, settings)
    payload = {"database_id": database_id, "tables": [_table_to_dict(t) for t in tables]}
    path.write_text(json.dumps(payload), encoding="utf-8")


__all__ = ["load_snapshot", "save_snapshot"]
