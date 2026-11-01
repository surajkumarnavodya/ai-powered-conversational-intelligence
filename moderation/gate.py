"""Orchestrates the pre-ingestion moderation gate: runs every chunk of an
asset through the configured provider (`moderation/provider.py`) plus the
text blocklist (`config/moderation_blocklist.py`), and decides pass/reject
for the **whole asset** -- never a partial ingestion.

Called by both `media/ingest.py` and `rag/ingestion.py` before either one
ever calls its own store's upsert/insert functions -- see each module's own
integration for exactly where. This module never touches Chroma, SQL
Server, or the moderation metadata store itself (`moderation/store.py`
handles persistence); it's pure decision logic plus the audit-log call.
"""

from __future__ import annotations

import logging
from typing import Literal

from config.moderation_blocklist import find_matches, load_blocklist
from config.settings import Settings, get_settings
from moderation.exceptions import ModerationProviderError
from moderation.provider import analyze_chunk
from moderation.taxonomy import Category
from moderation.types import (
    CategoryResult,
    ChunkModerationResult,
    ModerationChunk,
    ModerationDecision,
)
from security.audit_log import log_security_event

logger = logging.getLogger(__name__)


def _moderate_one_chunk(
    chunk: ModerationChunk, settings: Settings, blocklist: dict
) -> ChunkModerationResult:
    provider_results = analyze_chunk(chunk, settings)
    # Two details that look cosmetic and are not:
    #
    # `source=result.source` preserves what the provider said about itself
    # instead of relabelling everything `"provider"`. Azure always reports
    # `"provider"`, so nothing changes for it, but `ollama_local` reports
    # `"not_checked"` for an image chunk when no vision model is
    # configured, and overwriting that would turn an honest "not examined"
    # into an implied clean result -- the one thing `moderation/taxonomy.py`
    # is most careful never to do.
    #
    # `severity is None` is excluded from the comparison rather than
    # coerced to 0. `(result.severity or 0) >= threshold` is correct for
    # every integer severity, but at `MODERATION_SEVERITY_THRESHOLD=0`
    # (which `Field(ge=0)` permits) it made a `None` -- i.e. "no severity
    # concept here" -- trigger a hard reject of the whole asset.
    thresholded = [
        CategoryResult(
            category=result.category,
            triggered=(
                result.severity is not None
                and result.severity >= settings.moderation_severity_threshold
            ),
            severity=result.severity,
            source=result.source,
        )
        for result in provider_results
    ]

    blocklist_results: list[CategoryResult] = []
    if chunk.text:
        for category in find_matches(chunk.text, blocklist):
            blocklist_results.append(
                CategoryResult(category=category, triggered=True, severity=None, source="blocklist")
            )

    # Synthetic/manipulated-media placeholder -- see moderation/taxonomy.py's
    # module docstring. Only recorded for visual content; deliberately
    # always `triggered=False` regardless of anything else, since no
    # detector is wired in -- this is an honest "not checked" entry in the
    # audit trail, never a silent omission and never a false "passed".
    placeholder_results: list[CategoryResult] = []
    if chunk.content_type == "image":
        placeholder_results.append(
            CategoryResult(
                category="synthetic_media", triggered=False, severity=None, source="not_checked"
            )
        )

    return ChunkModerationResult(
        chunk_index=chunk.chunk_index,
        categories=tuple(thresholded + blocklist_results + placeholder_results),
    )


def moderate_chunks(
    asset_hash: str,
    chunks: list[ModerationChunk],
    settings: Settings | None = None,
) -> ModerationDecision:
    """Runs every chunk through moderation and returns one asset-level decision.

    Per this feature's decision rule: a hard-reject category triggered on
    *any* chunk rejects the *entire* asset -- every remaining chunk is still
    checked (so the audit trail is complete), but nothing partial is ever
    treated as ingestable.

    Args:
        asset_hash: The asset's content hash -- logged, never the content
            itself (`security.audit_log.log_security_event`).
        chunks: Every chunk to check (see `media/ingest.py`/
            `rag/ingestion.py` for how each pipeline builds this list).
        settings: Optional `Settings` override (mainly for tests).

    Raises:
        ModerationNotConfiguredError: provider not configured -- propagated
            from `moderation.provider.analyze_chunk`, not caught here. A
            safety gate must fail closed on a configuration problem, not
            silently pass content through unchecked.
        ModerationProviderError: either the provider ran and its answer
            could not be read (propagated from `analyze_chunk`), or every
            chunk of this asset came back `"not_checked"` -- see the
            comment on that check below for why that is a refusal and not
            a pass.
        httpx.HTTPError: a genuine provider outage -- also not caught here,
            for the same fail-closed reason.
    """
    settings = settings or get_settings()
    blocklist = load_blocklist(settings.moderation_blocklist_path)

    chunk_results = [_moderate_one_chunk(chunk, settings, blocklist) for chunk in chunks]

    # An asset whose every chunk came back `"not_checked"` was not examined
    # at all, and calling that "passed" would make this mandatory gate a
    # no-op -- precisely the outcome `Settings.moderation_provider`'s
    # docstring says there is deliberately no toggle for.
    #
    # It is reachable: `MODERATION_PROVIDER=ollama_local` reports
    # `"not_checked"` for an image chunk when `MODERATION_LOCAL_VISION_MODEL`
    # is blank, and `media/ingest.py` submits a plain image as image chunks
    # and *nothing else* (a video submits keyframes plus caption/transcript/
    # OCR text, but a silent video with no readable text submits keyframes
    # alone). So with that provider and no vision model, every image in the
    # media library would have been stored having been examined for
    # nothing.
    #
    # Scoped to "every chunk", not "any chunk", on purpose: a PDF whose
    # pages were rated and whose cover logo was not has still been
    # examined, and refusing it would make the local provider unable to
    # ingest ordinary documents -- see `moderation/provider.py
    # ::_ollama_local`'s docstring and `docs/RISK_REGISTER.md`'s R-021.
    if chunks and all(
        category.source == "not_checked"
        for chunk_result in chunk_results
        for category in chunk_result.categories
    ):
        raise ModerationProviderError(
            f"Moderation examined nothing in this asset: all {len(chunks)} chunk(s) came "
            "back not_checked, so there is no basis on which to pass it. With "
            "MODERATION_PROVIDER=ollama_local this means the asset is image-only and "
            "MODERATION_LOCAL_VISION_MODEL is not set -- set it to a vision-capable "
            "Ollama model, or use MODERATION_PROVIDER=azure_content_safety.",
            safe_message=(
                "This file could not be checked for content safety, so it was not "
                "ingested. Image moderation is not configured on this deployment."
            ),
        )

    hard_reject_categories: list[Category] = []
    soft_flag_categories: list[Category] = []
    triggering_chunk_index: int | None = None
    for result in chunk_results:
        if result.hard_rejected_categories and triggering_chunk_index is None:
            triggering_chunk_index = result.chunk_index
        hard_reject_categories.extend(result.hard_rejected_categories)
        soft_flag_categories.extend(result.soft_flagged_categories)

    status: Literal["passed", "rejected"] = "rejected" if hard_reject_categories else "passed"
    decision = ModerationDecision(
        status=status,
        triggering_categories=tuple(dict.fromkeys(hard_reject_categories)),
        soft_flagged_categories=tuple(dict.fromkeys(soft_flag_categories)),
        triggering_chunk_index=triggering_chunk_index,
        chunk_results=tuple(chunk_results),
    )

    if status == "rejected":
        log_security_event(
            "content_moderation_rejected",
            "warning",
            "An ingested asset was rejected by the pre-ingestion moderation gate.",
            asset_hash=asset_hash,
            categories=list(decision.triggering_categories),
            chunk_index=triggering_chunk_index,
            chunk_count=len(chunks),
        )
    elif decision.soft_flagged_categories:
        log_security_event(
            "content_moderation_soft_flagged",
            "info",
            "An ingested asset was soft-flagged (not blocked) by the moderation gate.",
            asset_hash=asset_hash,
            categories=list(decision.soft_flagged_categories),
            chunk_count=len(chunks),
        )

    logger.info(
        "[moderation] asset_hash=%s status=%s chunks=%d categories=%s",
        asset_hash[:12],
        status,
        len(chunks),
        list(decision.triggering_categories) or "none",
    )
    return decision


def unchecked_categories(decision: ModerationDecision) -> tuple[Category, ...]:
    """Every category some chunk of this asset was *not* examined for.

    A `"not_checked"` result is the honest marker a provider uses when it
    cannot examine a chunk -- `synthetic_media` on every image since this
    gate was built, and (with `MODERATION_PROVIDER=ollama_local` and no
    `MODERATION_LOCAL_VISION_MODEL`) the four harm categories on an image
    chunk. It was only ever visible in the in-memory
    `ChunkModerationResult`, so the stored audit row recorded a `"passed"`
    asset without recording what that pass did and did not cover. This is
    what puts it in `decision_summary` below.
    """
    seen: dict[Category, None] = {}
    for chunk_result in decision.chunk_results:
        for category in chunk_result.categories:
            if category.source == "not_checked":
                seen[category.category] = None
    return tuple(seen)


def decision_summary(decision: ModerationDecision) -> dict:
    """JSON-serializable summary of a `ModerationDecision`, for
    `moderation.store.record_asset`'s `moderation_checks` column --
    category names/counts only, never chunk content (an image path or
    extracted text never appears here)."""
    return {
        "status": decision.status,
        "triggering_categories": list(decision.triggering_categories),
        "soft_flagged_categories": list(decision.soft_flagged_categories),
        "unchecked_categories": list(unchecked_categories(decision)),
        "triggering_chunk_index": decision.triggering_chunk_index,
        "chunks_checked": len(decision.chunk_results),
    }
