"""Loads `config/display_names.yaml` -- the human override for which
column is an entity's readable name.

Mirrors `config/sensitive_columns.py`'s pattern exactly: a hand-authored,
deliberately-incomplete-until-reviewed YAML file, read fresh on every call
(no caching), because a hand-edit should take effect on the very next
question rather than after a restart.

**Inference is the default, not the fallback of last resort.**
`db/display_names.py` derives a display column from live introspection on
its own and is right for the common `DimProduct.EnglishProductName`
shape. This file exists for the cases inference cannot get right:

  - a dimension whose readable column does not look like a name
    (`DimAccount.AccountDescription`, a `Territory` table whose label is
    `Region`),
  - a person entity whose name parts are spelled unusually,
  - any table where an operator wants a specific column shown and does
    not want that choice to depend on a pattern list.

An unlisted table is never an error -- it simply falls through to
inference, and if inference also finds nothing the caller reports an
honest "no readable name" rather than inventing one.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parent / "display_names.yaml"


class DisplayNameOverride(BaseModel):
    """One table's declared display name, as written in the YAML file."""

    model_config = ConfigDict(frozen=True)

    #: Column(s) that render the name, in order. Two or more are
    #: concatenated with `separator` and NULL-guarded -- see
    #: `db.display_names.DisplayName.select_expression`.
    columns: tuple[str, ...] = Field(min_length=1)
    #: The column a fact row joins to. `None` falls back to the table's
    #: own single-column primary key.
    key_column: str | None = None
    #: Inserted between parts of a composite name.
    separator: str = " "


def load_display_name_overrides(
    path: Path | None = None,
) -> dict[str, DisplayNameOverride]:
    """Returns lowercased table name -> its declared display name.

    Keys are lowercased so a caller can match them against live
    introspection's spelling without worrying about case, which differs
    by engine. Both the bare table name (`DimProduct`) and a
    schema-qualified one (`dbo.DimProduct`) are accepted as written.

    A missing file, an empty file, or a malformed entry is **never an
    error**: this is an optional refinement layer, and failing a question
    because an operator mistyped a YAML key would make an accuracy aid
    into an outage (CLAUDE.md 4.2). A malformed entry is logged and
    skipped so it is visible without being fatal.
    """
    target = path or _DEFAULT_PATH
    if not target.exists():
        return {}

    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("[display_names] could not read %s: %s -- using inference only", target, exc)
        return {}

    entries = raw.get("tables") if isinstance(raw, dict) else None
    if not isinstance(entries, dict):
        return {}

    overrides: dict[str, DisplayNameOverride] = {}
    for table_name, spec in entries.items():
        if not isinstance(spec, dict):
            logger.warning("[display_names] entry for %r is not a mapping -- skipped", table_name)
            continue
        columns = spec.get("columns")
        if isinstance(columns, str):
            columns = [columns]
        if not isinstance(columns, list) or not columns:
            logger.warning(
                "[display_names] entry for %r has no 'columns' list -- skipped", table_name
            )
            continue
        try:
            overrides[str(table_name).lower()] = DisplayNameOverride(
                columns=tuple(str(c) for c in columns),
                key_column=(str(spec["key_column"]) if spec.get("key_column") else None),
                separator=str(spec.get("separator", " ")),
            )
        except Exception as exc:  # noqa: BLE001 - one bad entry must not break the rest
            logger.warning(
                "[display_names] entry for %r is invalid: %s -- skipped", table_name, exc
            )

    return overrides


__all__ = ["DisplayNameOverride", "load_display_name_overrides"]
