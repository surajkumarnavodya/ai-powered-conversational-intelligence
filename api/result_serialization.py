"""Turning raw database result rows into plain, JSON-encodable values.

Extracted from `api/main.py`, where this logic grew out of two real
crashes found by live use (both documented on the functions below), once a
second caller appeared: `api/artifacts.py`'s refresh, which writes a
re-executed result into a JSONB column and fingerprints it. That path hit
the *same* `Decimal` failure from the other side -- `json.dumps` raising
inside SQLAlchemy's own JSON serializer rather than inside
`jsonable_encoder` -- which is the signal that this belonged in one place
rather than being reimplemented per caller.

It lives under `api/` rather than in `db/` deliberately: it is a
*serialization* concern of this application's HTTP/storage boundary and
depends on `fastapi.encoders`, which `db/` has no business importing.
"""

from __future__ import annotations

from typing import Any

from fastapi.encoders import jsonable_encoder


def json_safe_cell(value: Any) -> Any:
    """Replaces a raw binary value with a short, readable placeholder.

    A real crash, found via live use: `fastapi.encoders.jsonable_encoder`'s
    default handling for `bytes` is `lambda o: o.decode()` -- a bare UTF-8
    decode, which raises `UnicodeDecodeError` (crashing the *entire*
    response, not just that one cell) the moment a SELECT touches a
    genuinely binary column, e.g. AdventureWorks' own
    `Production.ProductPhoto.LargePhoto` (a `varbinary(max)` storing real
    JPEG bytes). This app's SQL validator has no reason to reject such a
    column (it's an ordinary, safe, read-only SELECT), so this must be
    handled at serialization time, not prevented upstream. A results table
    has no useful way to render raw binary anyway, so this is a short
    placeholder -- never a base64 dump, which could turn one cell into
    several MB of response body for a large image/blob column.
    """
    if isinstance(value, bytes | bytearray | memoryview):
        return f"<binary data, {len(value)} bytes>"
    return value


def rows_to_json(rows: list[Any] | None) -> list[list[Any]] | None:
    """Converts DB result rows to plain JSON-encodable lists.

    `db.execution.execute_readonly_sql` returns `sqlalchemy.engine.row.Row`
    objects, not plain tuples -- `Row` behaves like a tuple (iterable,
    indexable) but isn't recognized as one by `fastapi.encoders
    .jsonable_encoder`'s isinstance checks, so passing it directly raises
    (its dict()/vars() object-fallback path doesn't apply to Row either).
    Converting to `list(row)` first sidesteps that entirely; jsonable_encoder
    still does the real work of encoding each row's actual values (Decimal,
    datetime, UUID, ...) -- `json_safe_cell` runs first so a binary value
    never reaches jsonable_encoder's own crash-prone default handling.

    **Anything that persists or hashes a result must go through this
    first**, not only an HTTP response. A SQL Server `decimal` column
    arrives as `decimal.Decimal`, which neither SQLAlchemy's JSON
    serializer (a hard `TypeError`, so the write fails outright) nor a
    `default=str` hash (a *quoted* string where the stored value is a bare
    number, so two identical results hash differently) handles the way a
    caller would expect. Converting once, here, is what keeps a stored
    result and a freshly executed one directly comparable.
    """
    if rows is None:
        return None
    return jsonable_encoder([[json_safe_cell(value) for value in row] for row in rows])
