"""Pure, zero-I/O file rendering for `bi_integration/`'s pull-style
(Tableau/Excel) connectors -- Prompt 48. Reuses `openpyxl` (already a
pinned dependency, used today for *reading* XLSX attachments --
`attachments/processors/xlsx_processor.py`) for writing, so this is the
first XLSX-*writing* call site in this codebase, not a new dependency.

Every renderer takes already-governed rows (the caller, `api/
bi_integration.py`, is responsible for having already applied
`governance.result_policy.govern_rows_for_caller` before these ever see
a row) and returns raw bytes -- no file-system access, no network call.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from openpyxl import Workbook

#: Excel's own hard row-count ceiling (XLSX, the modern format) -- a
#: defensive cap independent of `Settings.bi_export_max_rows`, which
#: already bounds this far below in practice; this only guards against a
#: future caller raising that setting past what the format itself can
#: hold.
_XLSX_MAX_ROWS = 1_048_576


def json_safe_value(value: Any) -> Any:
    """Coerces one cell value into something `json.dumps` can serialize
    without raising -- a `Decimal`/`datetime`/`date` is common in a real
    SQL result (see `db.column_formatting`'s own identical concern for
    the frontend-facing path) but isn't natively JSON-serializable.

    Public (not `_json_safe`) because `bi_integration.push`'s own
    Power BI/Looker payload builders reuse it too -- a shared, intra-
    package helper, not an external private-import situation."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def render_csv(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def render_json(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> bytes:
    """A plain `[{"col": value, ...}, ...]` array -- the shape every
    "from the web"/generic REST connector (Power BI's "Web" source,
    Tableau's Web Data Connector, Excel's "Get Data from Web") expects
    without any format-specific negotiation."""
    payload = [
        {column: json_safe_value(value) for column, value in zip(columns, row, strict=True)}
        for row in rows
    ]
    return json.dumps(payload, default=str).encode("utf-8")


def render_xlsx(columns: Sequence[str], rows: Sequence[Sequence[Any]], sheet_title: str) -> bytes:
    """Raises `ValueError` if `rows` exceeds XLSX's own hard row ceiling
    -- a connector's own `Settings.bi_export_max_rows` cap should always
    keep this unreachable in practice; this is a second, independent
    guard against the format's own real limit, not a reason to silently
    truncate further here."""
    if len(rows) > _XLSX_MAX_ROWS:
        raise ValueError(
            f"{len(rows)} rows exceeds XLSX's own {_XLSX_MAX_ROWS}-row ceiling -- "
            "lower Settings.bi_export_max_rows or use the csv/json export format instead."
        )
    workbook = Workbook()
    sheet = workbook.active
    # openpyxl rejects a handful of characters/an empty title in sheet
    # names -- truncated to its own 31-character limit, a connector's
    # own display name is never guaranteed to fit.
    sheet.title = (sheet_title or "Export")[:31]
    sheet.append(list(columns))
    for row in rows:
        sheet.append([json_safe_value(value) for value in row])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
