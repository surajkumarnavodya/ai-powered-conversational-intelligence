"""Builds the candidate set routing may choose from.

One function, `build_routable_models`, joining three things that already
exist -- no new source of truth, no second allowlist:

1. **`Settings.ollama_allowed_models`** (`agent/model_registry.py`'s own
   module docstring calls this "the only thing that actually gates what a
   caller may request"). It remains exactly that here: a model absent
   from it is never a candidate, so routing can never reach a model an
   operator did not configure. `Settings.ollama_model` is always unioned
   in by `Settings`' own `_fill_default_ollama_allowed_models`, so the
   configured default is always present.
2. **`config/ollama_models.yaml`** via `config.ollama_models
   .load_ollama_model_catalog`/`describe_model` -- capabilities,
   `resource_level` (this package's `ModelTier`), context length and the
   `recommended` flag. Reused verbatim; this module does not re-derive a
   tier from a parameter-size string.
3. **Evaluation evidence** (`model_routing.evidence`), passed in rather
   than fetched here, so this function stays pure and testable without a
   database.

Installed-vs-configured stays where `agent/model_registry.py` put it: a
live Ollama round trip happens only on `GET /models`, never on the
request path. A configured-but-not-pulled model therefore *can* be
routed to, and fails exactly as it already would -- an
`OllamaUnavailableError`, which `model_routing.router` treats as an
availability failure and answers with the fallback chain. That is a
strictly better outcome than the pre-routing behavior (a hard failure
with no fallback at all).
"""

from __future__ import annotations

from collections.abc import Mapping

from config.ollama_models import OllamaModelInfo, describe_model, load_ollama_model_catalog
from config.settings import Settings
from model_routing.models import (
    TIER_BY_RESOURCE_LEVEL,
    ModelTier,
    RoutableModel,
)


def _tier_for(info: OllamaModelInfo, catalogued: bool) -> tuple[ModelTier, bool]:
    """Resolves one model's tier and whether that tier is actually known.

    A model with no catalog entry gets `describe_model`'s generic
    fallback (`capabilities=("text", "sql")`, `resource_level="unknown"`),
    which is deliberately **not** treated as evidence of a tier: see
    `ModelTier.UNKNOWN`'s own docstring. The same applies to a real entry
    that simply omits `resource_level`.
    """
    if not catalogued:
        return ModelTier.UNKNOWN, False
    tier = TIER_BY_RESOURCE_LEVEL.get(info.resource_level.strip().lower())
    if tier is None:
        return ModelTier.UNKNOWN, False
    return tier, True


def build_routable_models(
    settings: Settings,
    evidence: Mapping[str, tuple[bool, float | None, str | None]] | None = None,
) -> tuple[RoutableModel, ...]:
    """Returns every model routing may consider, in a deterministic order.

    Args:
        settings: Application settings. Only `ollama_allowed_models` and
            `ollama_model` are read -- never a secret, never a live
            connection.
        evidence: `model_id -> (evaluation_backed, quality_score,
            governance_version_id)`, normally from
            `model_routing.evidence.build_model_evaluation_evidence`.
            `None` (the default) means no evidence was available, which
            is honestly recorded as `evaluation_backed=False` on every
            candidate rather than optimistically assumed -- with
            `Settings.model_routing_require_evaluation_evidence` on, that
            correctly blocks every escalation.

    Returns:
        One `RoutableModel` per allowed model, sorted by model id so the
        candidate order (and therefore every tie-break downstream) is
        reproducible across processes.
    """
    catalog = load_ollama_model_catalog()
    evidence_map = evidence or {}

    models: list[RoutableModel] = []
    for model_id in sorted(set(settings.ollama_allowed_models)):
        info = describe_model(model_id, catalog)
        tier, tier_known = _tier_for(info, model_id in catalog)
        backed, score, version_id = evidence_map.get(model_id, (False, None, None))
        models.append(
            RoutableModel(
                model_id=model_id,
                tier=tier,
                tier_known=tier_known,
                capabilities=tuple(info.capabilities),
                context_length=info.context_length,
                recommended=info.recommended,
                is_default=(model_id == settings.ollama_model),
                evaluation_backed=backed,
                evaluation_quality_score=score,
                governance_version_id=version_id,
            )
        )
    return tuple(models)


def find_model(candidates: tuple[RoutableModel, ...], model_id: str) -> RoutableModel | None:
    """The candidate with this id, or `None`.

    Used by `model_routing.policy`/`router` to describe the configured
    default (and a caller-pinned model) with the same shape as any other
    candidate, rather than special-casing it everywhere.
    """
    for candidate in candidates:
        if candidate.model_id == model_id:
            return candidate
    return None


__all__ = ["build_routable_models", "find_model"]
