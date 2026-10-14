"""Idempotency: stopping a retry from doing the same work twice.

A retry is only safe when repeating the operation is harmless. In this
codebase SQL execution is `SELECT`-only, which bounds the blast radius
a great deal -- a duplicated query returns the same rows rather than
charging a card twice. It is still not free: a duplicated expensive
query costs real database time on a system that is, by hypothesis,
already struggling enough to have produced a retryable failure.

**The key is scoped to the execution, and includes the tenant.** Two
tenants running byte-identical SQL must never share a cache entry --
that would be the cross-tenant leak the whole platform is built to
prevent, arriving through the back door of a performance optimisation.
`build_key` makes `tenant_id` part of the hash so a collision across
tenants is not merely unlikely but impossible by construction.

**The key is a hash, and only a hash.** The SQL text and the arguments
are hashed, never stored. A debug log of an idempotency store must not
become a log of every query the platform ran (CLAUDE.md §5.6).

**Scope: within one execution.** This is not a distributed lock and
makes no cross-process claim. A multi-worker deployment has one store
per worker, like every other process-local control here (CLAUDE.md
§13). It stops the duplicate-work case that actually happens -- a
retry loop inside a single request -- and claims nothing more.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def build_key(
    *,
    operation: str,
    tenant_id: str | None,
    payload: Any,
) -> str:
    """A stable, tenant-scoped key for one logical operation.

    `tenant_id` is hashed in rather than merely prefixed so that no
    amount of crafted `operation` or `payload` text can produce the key
    of another tenant's entry.
    """
    hasher = hashlib.sha256()
    hasher.update(operation.encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update((tenant_id or "__no_tenant__").encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update(repr(payload).encode("utf-8", errors="replace"))
    return hasher.hexdigest()


@dataclass
class IdempotencyStore:
    """Remembers what an execution already did, so a retry need not redo it.

    Bounded by `max_entries` with oldest-first eviction. An unbounded
    cache on a long execution is a memory leak, and the entries that
    matter for a retry are the recent ones.
    """

    max_entries: int = 64
    _entries: dict[str, Any] = field(default_factory=dict, repr=False)
    _in_flight: set[str] = field(default_factory=set, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    hits: int = 0
    misses: int = 0

    def get(self, key: str) -> tuple[bool, Any]:
        """`(found, value)`. A tuple rather than `None`, because `None` is
        a legitimate recorded result and must not read as a miss."""
        with self._lock:
            if key in self._entries:
                self.hits += 1
                return True, self._entries[key]
            self.misses += 1
            return False, None

    def record(self, key: str, value: Any) -> None:
        with self._lock:
            if key not in self._entries and len(self._entries) >= self.max_entries:
                self._entries.pop(next(iter(self._entries)))
            self._entries[key] = value
            self._in_flight.discard(key)

    def begin(self, key: str) -> bool:
        """Marks `key` as started. False if it was already in flight.

        The guard against a *concurrent* duplicate rather than a
        sequential one -- two threads of the same execution reaching the
        same operation at once would both miss the cache, and both run.
        """
        with self._lock:
            if key in self._in_flight or key in self._entries:
                return False
            self._in_flight.add(key)
            return True

    def abandon(self, key: str) -> None:
        """Releases an in-flight marker after a failure, so a retry may proceed."""
        with self._lock:
            self._in_flight.discard(key)

    def stats(self) -> dict[str, int]:
        """Counts only -- never a key, never a value."""
        with self._lock:
            return {
                "entries": len(self._entries),
                "in_flight": len(self._in_flight),
                "hits": self.hits,
                "misses": self.misses,
            }


__all__ = ["IdempotencyStore", "build_key"]
