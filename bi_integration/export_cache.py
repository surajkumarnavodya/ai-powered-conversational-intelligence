"""Short-lived, in-memory store for a pull-style (Tableau/Excel)
connector's own last-refreshed **raw rows** -- Prompt 48. Mirrors
`media_gen/cache.py`'s identical "process-lifetime only, not persisted,
bounded, eviction on overflow" shape, applied to a BI connector's own
last-refreshed result instead of generated media bytes.

Caches `(columns, rows)`, never pre-rendered CSV/XLSX/JSON bytes --
`GET /bi/connectors/{id}/export?format=...` renders on demand from this
one cached result (`bi_integration.rendering`'s own pure, cheap
functions), so a connector refreshed once can still be exported in any
of the three formats without re-running SQL or caching three separate
copies of the same data.

Keyed by `connector_id` (never a random opaque id, unlike
`media_gen.cache.MediaCache`) -- a caller fetches "the current export
for connector X," not "whatever this specific earlier call produced,"
so the key must be the connector's own stable identity. LRU (not FIFO)
eviction: a connector refreshed recently is more likely to be exported
again soon than one that hasn't been touched in a while.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

_DEFAULT_MAX_ENTRIES = 200


@dataclass(frozen=True)
class CachedExport:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    generated_at: datetime
    row_count: int


class BiExportCache:
    """Bounded `connector_id -> CachedExport` store. Construct via
    `get_export_cache()` below rather than directly, mirroring every
    other process-lifetime singleton in this codebase (`media_gen
    .cache.get_media_cache`, `agent.rate_limit`'s limiters)."""

    def __init__(self, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        self._max_entries = max_entries
        self._entries: OrderedDict[str, CachedExport] = OrderedDict()

    def put(self, connector_id: str, export: CachedExport) -> None:
        """Stores (or replaces) `connector_id`'s own export, marking it
        most-recently-used. Evicts the least-recently-used entry first
        if this is a *new* key and the cache is already full."""
        if connector_id in self._entries:
            del self._entries[connector_id]
        elif len(self._entries) >= self._max_entries:
            self._entries.popitem(last=False)
        self._entries[connector_id] = export

    def get(self, connector_id: str) -> CachedExport | None:
        """Returns the stored export, or `None` if this connector was
        never refreshed, its entry was evicted, or the process has
        since restarted. Marks the entry most-recently-used on a hit."""
        entry = self._entries.get(connector_id)
        if entry is not None:
            self._entries.move_to_end(connector_id)
        return entry

    def discard(self, connector_id: str) -> None:
        """Removes `connector_id`'s own cached export, if any -- called
        when a connector is deleted, so a stale export can never be
        served for an id a future connector might reuse (ids are
        UUIDs, so collision is already astronomically unlikely, but this
        is a real, cheap guarantee rather than relying on that alone)."""
        self._entries.pop(connector_id, None)


_export_cache: BiExportCache | None = None


def get_export_cache(max_entries: int = _DEFAULT_MAX_ENTRIES) -> BiExportCache:
    """Returns the process-wide export cache, creating it on first use."""
    global _export_cache
    if _export_cache is None:
        _export_cache = BiExportCache(max_entries)
    return _export_cache


def _reset_export_cache_for_tests() -> None:
    """Test-only: clears the process-wide singleton so one test's cached
    export can never leak into another's assertions. Mirrors `tests
    /conftest.py`'s `_clear_process_singleton_caches` fixture's identical
    purpose for this codebase's other process-global caches."""
    global _export_cache
    _export_cache = None
