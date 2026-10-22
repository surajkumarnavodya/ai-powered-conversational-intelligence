"""The source catalog the router prompt is filled with, built once.

The router has to decide whether a question "refers to data that
plausibly exists in the listed tables" (the prompt's rule 4). It cannot
do that from source *names* alone -- `sql_database` says nothing about
whether this deployment holds sales data or leave records. So the prompt
is given a catalog: a line per table, a line per policy document, and the
names of anything the user uploaded this session.

**Built once and cached**, because it is derived from the schema index
and the policy store, both of which are rebuilt by their own commands
rather than changing per request (`scripts/build_embeddings.py`,
`scripts/ingest_schema.py`). Rebuilding it per question would add two
store round-trips to the latency path ahead of any real work, for a
result that is identical until an operator re-indexes.

**Deliberately shallow.** One line per table, no column lists: the brief
asks for this, and it is also what keeps the catalog from dominating the
router prompt. On the reference machine a full column dump for 36 tables
measured ~3,000 tokens, which at ~185 tok/s is ~16s of prompt processing
spent before routing even begins (see `docs/PERFORMANCE.md`).

**Per-process, not per-tenant.** The table list is schema shape, not row
data, and is identical for every caller of a given deployment. Nothing
caller-specific is cached here -- uploaded file names are passed in per
call instead, since those genuinely differ by session.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from config.settings import Settings

logger = logging.getLogger(__name__)

#: Hard cap on catalog lines per section. A deployment with hundreds of
#: tables would otherwise push the real question out of the router's
#: context window, turning a routing aid into a routing hazard.
_MAX_TABLES = 60
_MAX_POLICY_DOCS = 40
_MAX_UPLOADS = 20


def _table_lines(settings: Settings) -> list[str]:
    """One `- name: description` line per indexed table, best-effort.

    Reads the business-context store first (its table chunks carry a
    human description, which is what makes a catalog line useful), and
    falls back to bare table names from the schema index when business
    context has not been ingested. Returns `[]` rather than raising if
    neither is available -- the catalog is an accuracy aid, so it fails
    open to "no catalog" and lets the router work from source definitions
    alone (CLAUDE.md 4.2).
    """
    try:
        from embeddings.schema_indexer import get_chroma_client, get_collection

        client = get_chroma_client(settings)
        lines: list[str] = []
        for database in settings.databases:
            try:
                collection = get_collection(client, settings, database.name)
                metadatas = collection.get(include=["metadatas"]).get("metadatas") or []
            except Exception as exc:  # noqa: BLE001 - one bad index must not kill the catalog
                logger.warning(
                    "[router_catalog] skipped database %r: %s", database.name, type(exc).__name__
                )
                continue
            names = sorted(
                {str(meta["table_name"]) for meta in metadatas if meta and meta.get("table_name")}
            )
            for name in names:
                lines.append(f"  - {name}")
        return lines[:_MAX_TABLES]
    except Exception as exc:  # noqa: BLE001 - see docstring: fails open
        logger.warning("[router_catalog] table catalog unavailable: %s", type(exc).__name__)
        return []


def _policy_lines(settings: Settings) -> list[str]:
    """Policy document titles, when the policy store is configured."""
    if not settings.enable_document_rag:
        return []
    try:
        from rag.store import get_rag_engine, list_documents

        documents = list_documents(get_rag_engine(settings), collection="policies")
    except Exception as exc:  # noqa: BLE001 - fails open, same as tables
        logger.warning("[router_catalog] policy catalog unavailable: %s", type(exc).__name__)
        return []

    lines = []
    for document in documents[:_MAX_POLICY_DOCS]:
        title = getattr(document, "title", None) or getattr(document, "filename", None)
        if not title:
            continue
        category = getattr(document, "category", None)
        lines.append(f"  - {title}" + (f" ({category})" if category else ""))
    return lines


@lru_cache(maxsize=4)
def _cached_sections(settings_fingerprint: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Memoized `(table_lines, policy_lines)`.

    Keyed by a fingerprint of the settings that can change the catalog,
    rather than by `Settings` itself -- a frozen pydantic model is
    hashable, but keying on the whole object would rebuild the catalog
    whenever any unrelated setting differed.
    """
    from config.settings import get_settings

    settings = get_settings()
    return tuple(_table_lines(settings)), tuple(_policy_lines(settings))


def _fingerprint(settings: Settings) -> str:
    databases = ",".join(sorted(d.name for d in settings.databases))
    return f"{databases}|{settings.enable_document_rag}|{settings.chroma_collection_name}"


def reset_catalog_cache() -> None:
    """Drops the memoized catalog.

    Called by `tests/conftest.py` alongside the other process-lifetime
    caches, and available to an operator who re-indexes without
    restarting. Without it a test that configures different databases
    would silently read the previous test's catalog (CLAUDE.md 4.6).
    """
    _cached_sections.cache_clear()


def build_source_catalog(
    settings: Settings,
    *,
    available: list[str],
    uploaded_filenames: list[str] | None = None,
) -> str:
    """Renders the `{source_catalog}` block for the router prompt.

    Args:
        settings: Live settings; decides which sections are present.
        available: Internal source names this caller may actually reach,
            already filtered by `get_available_sources`. A source the
            caller has no permission for is never described to the
            router, so the model cannot pick what it would then be
            refused (CLAUDE.md 5.2 -- the filter still runs afterwards;
            this just stops wasting a pick on it).
        uploaded_filenames: Files attached to *this* session, if any.

    Returns:
        A markdown block. Never raises -- an unavailable store yields a
        missing section, not a failed question.
    """
    tables, policies = _cached_sections(_fingerprint(settings))
    sections: list[str] = []

    if "sql" in available:
        if tables:
            sections.append("- sql_database tables:\n" + "\n".join(tables))
        else:
            sections.append("- sql_database: configured, table list unavailable")

    if "policy" in available:
        if policies:
            sections.append("- policy_kb documents:\n" + "\n".join(policies))
        else:
            sections.append("- policy_kb: configured, document list unavailable")

    if uploaded_filenames:
        names = "\n".join(f"  - {name}" for name in uploaded_filenames[:_MAX_UPLOADS])
        sections.append("- user_documents uploaded this session:\n" + names)

    enabled = [
        label
        for label, source in (
            ("web_search", "web"),
            ("media_search", "media_search"),
            ("image_generation", "generation"),
            ("video_generation", "generation"),
        )
        if source in available
    ]
    if enabled:
        sections.append("- Enabled capabilities: " + ", ".join(sorted(set(enabled))))

    return "\n".join(sections) if sections else "- (no catalog available)"
