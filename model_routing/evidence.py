"""Where "when evaluation supports it" comes from.

The mission allows a stronger model for complex reasoning *when
evaluation supports it*. This application already has the governed place
where a model's evaluation lives: Prompt 44's AI Governance Center. A
`model`-typed `GovernanceAssetVersion` carries an operator-recorded
`evaluation_results` payload and moves through
draft -> testing -> evaluating -> approved -> deployed with a real human
approval step and an append-only audit trail.

So this module adds no second evaluation store. It reads that one --
`identity.repositories.governance.list_versions` filtered to
`asset_type="model"` -- and answers one question per model id: is there
an `approved` or `deployed` version of it, for this tenant, that
actually recorded evaluation results? That boolean is what
`model_routing.policy` gates escalation on.

Three honesty properties worth stating, because each is a place this
could quietly overclaim:

1. **The quality score is read, never computed.** `_quality_score` pulls
   a numeric value out of the operator's own `evaluation_results` under a
   small set of recognized keys. If none is present, the score is `None`
   and routing falls back to tier ordering. Nothing here measures,
   benchmarks or estimates a model's accuracy.
2. **A missing record is "no evidence", not "bad".** It blocks
   escalation *to* that model and nothing else; the model remains
   perfectly usable as the configured default, and a caller may still
   pin it explicitly.
3. **Evidence is tenant-scoped.** `list_versions` is already filtered by
   `tenant_id`, so one tenant's approval of a model never licenses
   another tenant's routing to escalate to it. The cache below is keyed
   by tenant for the same reason.

The identity database is optional in this application
(`Settings.local_auth_enabled`), and routing must work without it:
`load_model_evaluation_evidence` returns an empty mapping on any failure
-- a disabled identity DB, an unreachable one, a missing table -- which
with the evidence requirement on correctly resolves to "no escalation",
never to an exception on the request path.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping
from typing import Any

from config.settings import Settings

logger = logging.getLogger(__name__)

#: Stages whose evaluation record counts. `approved` and `deployed` are
#: exactly the two stages `governance_center.models.stage_to_truth_level`
#: maps to `CONFIRMED_BUSINESS_TRUTH` -- a human reviewed and approved
#: this specific version. A `testing`/`evaluating` version has results
#: but no approval yet, and routing is not the place to decide an
#: unreviewed model is good enough.
_ELIGIBLE_STAGES = ("approved", "deployed")

#: Keys in an operator-recorded `evaluation_results` payload this module
#: recognizes as "the headline score", in priority order. Anything else
#: is left alone -- an unrecognized shape yields no score rather than a
#: guess at which number mattered.
_SCORE_KEYS = ("quality_score", "accuracy", "execution_accuracy", "final_accuracy", "score")

#: `model_id -> (evaluation_backed, quality_score, governance_version_id)`
EvidenceMap = Mapping[str, tuple[bool, float | None, str | None]]


def _quality_score(evaluation_results: Any) -> float | None:
    """The headline numeric score in an `evaluation_results` payload, if
    one is recognizable. Never raises on a payload of any shape -- this
    is operator-supplied JSON, treated as untrusted structure (not
    untrusted *instructions*: nothing here is ever put in a prompt)."""
    if not isinstance(evaluation_results, dict):
        return None
    for key in _SCORE_KEYS:
        value = evaluation_results.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            return float(value)
    return None


def build_model_evaluation_evidence(
    session: Any, tenant_id: str
) -> dict[str, tuple[bool, float | None, str | None]]:
    """Reads this tenant's approved/deployed governed model versions.

    Args:
        session: An open identity-database `Session`. Typed loosely on
            purpose: this package does not import SQLAlchemy or
            `identity.models` (the same boundary
            `governance_center/models.py` keeps), so a caller supplies
            the session and this function only passes it through to the
            repository.
        tenant_id: The server-resolved tenant whose approvals apply.

    Returns:
        `model_id -> (True, score_or_None, version_id)` for every model
        with an eligible version. A model with several eligible versions
        keeps the highest recorded score (deterministic regardless of row
        order), preferring a `deployed` version over a merely `approved`
        one when neither records a score.
    """
    from identity.repositories.governance import list_versions

    evidence: dict[str, tuple[bool, float | None, str | None]] = {}
    for stage in _ELIGIBLE_STAGES:
        for version in list_versions(session, tenant_id, asset_type="model", stage=stage):
            if version.evaluation_results is None:
                continue
            score = _quality_score(version.evaluation_results)
            existing = evidence.get(version.asset_key)
            if existing is not None:
                existing_score = existing[1]
                if existing_score is not None and (score is None or score <= existing_score):
                    continue
                if existing_score is None and score is None:
                    continue
            evidence[version.asset_key] = (True, score, str(version.id))
    return evidence


class _EvidenceCache:
    """A small per-tenant TTL cache over `build_model_evaluation_evidence`.

    Routing runs on the request path, and a governed approval changes on
    a human timescale, so re-reading the identity database for every LLM
    call would be pure waste. Modeled directly on
    `db.query_store.get_cached_query_store_findings`'s own
    lock-protected, TTL-refreshed cache, for the same reason it exists
    there: keep an operational lookup "usually free" without introducing
    a background worker.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, dict[str, tuple[bool, float | None, str | None]]]] = (
            {}
        )

    def get(
        self, tenant_id: str, ttl_seconds: float, loader: Any
    ) -> dict[str, tuple[bool, float | None, str | None]]:
        now = time.monotonic()
        with self._lock:
            cached = self._entries.get(tenant_id)
            if cached is not None and now - cached[0] < ttl_seconds:
                return cached[1]
        loaded = loader()
        with self._lock:
            self._entries[tenant_id] = (now, loaded)
        return loaded

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_cache = _EvidenceCache()


def load_model_evaluation_evidence(
    tenant_id: str | None, settings: Settings
) -> dict[str, tuple[bool, float | None, str | None]]:
    """Cached, fail-open evidence lookup for the request path.

    Returns an empty mapping -- never raises, never logs a secret -- when
    there is no tenant to scope to, local accounts are disabled, or the
    identity database is unreachable. With
    `Settings.model_routing_require_evaluation_evidence` on, an empty
    mapping means routing keeps the configured default, which is the
    correct, safe reading of "no evaluation evidence is available".
    """
    if tenant_id is None or not settings.local_auth_enabled:
        return {}

    def loader() -> dict[str, tuple[bool, float | None, str | None]]:
        from identity.db import get_identity_session

        session = get_identity_session(settings)
        try:
            return build_model_evaluation_evidence(session, tenant_id)
        finally:
            session.close()

    try:
        return _cache.get(tenant_id, settings.model_routing_evidence_ttl_seconds, loader)
    except Exception as exc:  # noqa: BLE001 - routing must never fail on a metadata lookup
        logger.warning(
            "[model_routing] could not load governed model evaluation evidence: %s",
            type(exc).__name__,
        )
        return {}


def reset_evidence_cache() -> None:
    """Drops the cached evidence. Tests use this for isolation; an
    operator does not need it, since an approval takes effect within
    `Settings.model_routing_evidence_ttl_seconds`."""
    _cache.clear()


__all__ = [
    "EvidenceMap",
    "build_model_evaluation_evidence",
    "load_model_evaluation_evidence",
    "reset_evidence_cache",
]
