"""The declared source types -- Batch 1 of
`docs/architecture/implementation-roadmap.md`.

**This module is the single owner of what each source is and when it is
usable.** Before it existed, those facts lived in three parallel structures
in `agent/orchestrator/nodes.py` -- `_SOURCE_DESCRIPTIONS`,
`SOURCE_PERMISSIONS` and the `if` chain inside `get_available_sources` --
which could drift from one another. Adding a source meant editing three
places and nothing failed if you edited two. Those three now derive from
this table (`CLAUDE.md` 4.8, one owner per fact).

The routing descriptions are reproduced here **verbatim** from the ones
`_SOURCE_DESCRIPTIONS` carried. They are prompt text read by the LLM
classifier, so rewording them changes routing behaviour. They were moved,
not edited; `tests/test_sources_descriptors.py` pins that.

Only sources that actually exist are declared. SharePoint, OneDrive,
Dataverse, Outlook, REST/OpenAPI and MCP are **not** here -- each arrives
with its connector, in its own batch. A descriptor with nothing behind it
would make `GET /sources` advertise a source the router cannot reach.
"""

from __future__ import annotations

from agent.authz import Permission
from sources.types import (
    SourceCapabilities,
    SourceDescriptor,
    SourceKind,
    SourceOperation,
)

# Every source implemented today is read-only: no descriptor below declares
# `has_side_effects=True`, and media generation -- the only one that creates
# anything -- writes a cache entry in this app, never anything in a source
# system. A connector that genuinely mutates a source system declares it,
# and the roadmap requires such an operation to be off by default.

_SQL = SourceDescriptor(
    name="sql",
    kind=SourceKind.STRUCTURED_SQL,
    description=(
        "the company's structured databases -- customer data, sales/order "
        "data, financial/general-ledger data, HR/employee records, or any "
        "other data that would live in rows and columns"
    ),
    capabilities=SourceCapabilities(
        operations=frozenset({SourceOperation.SEARCH, SourceOperation.DISCOVER}),
        supports_citations=False,
        # The row cap is a hard ceiling enforced twice (in the SQL text and
        # at the cursor), not a page through a larger result set.
        supports_pagination=False,
        # Live introspection every time; there is no cached schema to refresh.
        supports_incremental_sync=False,
        # SELECT-only allowlist, enforced by `agent/sql_validator.py`.
        has_side_effects=False,
        # The database role is shared across callers, so the source cannot
        # filter per-user. Authorization is this platform's job here: the
        # result gate, the restricted-column gate and tenant `WHERE` folding.
        preserves_source_acl=False,
    ),
    # No source-level permission: asking a data question is the baseline
    # capability. Bounded instead by SQL validation and result governance.
    permission=None,
    # `Settings.databases` always has at least one entry, so `sql` is always
    # configured -- see that field's own docstring.
    is_configured=lambda _settings: True,
)

_DOCUMENTS = SourceDescriptor(
    name="documents",
    kind=SourceKind.DOCUMENT,
    description=(
        "general uploaded PDF documents -- the default destination for any "
        "uploaded document, including HR-adjacent topics like leave, holiday, "
        "benefits, or onboarding, unless that specific document was "
        "separately flagged sensitive at upload time (see 'policy' below). If "
        "the question could plausibly be answered by an uploaded PDF on any "
        "topic, include this source"
    ),
    capabilities=SourceCapabilities(
        operations=frozenset(
            {SourceOperation.SEARCH, SourceOperation.READ, SourceOperation.INGEST}
        ),
        supports_citations=True,
        supports_pagination=False,
        supports_incremental_sync=False,
        has_side_effects=False,
        # `DocumentRecord.restricted_roles` is an independent RBAC gate this
        # platform applies -- the store does not filter by caller itself.
        preserves_source_acl=False,
    ),
    # Reading general documents is part of the baseline viewer role
    # (`Permission.DOCUMENTS_READ`); the orchestrator did not gate this
    # source, and moving the table must not start gating it.
    permission=None,
    is_configured=lambda s: bool(s.enable_document_rag and s.rag_store_connection_string),
)

_POLICY = SourceDescriptor(
    name="policy",
    kind=SourceKind.POLICY,
    description=(
        "a separate, access-restricted collection of internal documents "
        "specifically flagged sensitive at upload time -- compensation, "
        "disciplinary action, or legal/compliance matters only. Do not assume "
        "a question merely containing the word 'policy' belongs here -- most "
        "policy-titled documents (e.g. a leave or holiday policy) live in "
        "'documents' instead"
    ),
    capabilities=SourceCapabilities(
        operations=frozenset(
            {SourceOperation.SEARCH, SourceOperation.READ, SourceOperation.INGEST}
        ),
        supports_citations=True,
        supports_pagination=False,
        supports_incremental_sync=False,
        has_side_effects=False,
        preserves_source_acl=False,
    ),
    permission=Permission.POLICY_RAG_QUERY,
    is_configured=lambda s: bool(s.enable_policy_rag and s.rag_store_connection_string),
)

_WEB = SourceDescriptor(
    name="web",
    kind=SourceKind.WEB,
    description=(
        "live web search, for current or external information not in any of "
        "the company's own systems (e.g. today's news, a public fact, "
        "something outside this company entirely)"
    ),
    capabilities=SourceCapabilities(
        operations=frozenset({SourceOperation.SEARCH}),
        supports_citations=True,
        supports_pagination=False,
        supports_incremental_sync=False,
        has_side_effects=False,
        # Public web: there is no per-caller ACL to preserve.
        preserves_source_acl=False,
    ),
    permission=Permission.WEB_SEARCH,
    is_configured=lambda s: bool(s.enable_web_search and s.web_search_api_key),
)

_GENERATION = SourceDescriptor(
    name="generation",
    kind=SourceKind.MEDIA_GENERATION,
    description=(
        "generating a brand-new image or video artifact that doesn't exist "
        "yet -- only for a question that explicitly asks to "
        "create/generate/draw/render/animate/make new media. Never use this "
        "for retrieving, displaying, or visualizing already-computed data -- "
        "that's 'sql'/'documents'/'policy'/'web'. Also never use this to find "
        "an EXISTING photo/video/recording -- that's 'media_search'"
    ),
    capabilities=SourceCapabilities(
        operations=frozenset({SourceOperation.GENERATE}),
        # A generated artifact is not evidence about anything; it has no
        # source to cite.
        supports_citations=False,
        supports_pagination=False,
        supports_incremental_sync=False,
        # Writes only a local cache entry under an opaque id, never the
        # provider's CDN URL and nothing in any source system.
        has_side_effects=False,
        preserves_source_acl=False,
    ),
    permission=Permission.MEDIA_GENERATE,
    is_configured=lambda s: bool(s.enable_media_generation and s.ima_api_key),
)

_MEDIA_SEARCH = SourceDescriptor(
    name="media_search",
    kind=SourceKind.MEDIA_SEARCH,
    description=(
        "finding an EXISTING photo or video already sitting in the company's "
        "local media library, by describing its visual/audio content -- e.g. "
        "'find the photo of the site inspection', 'show me the clip where the "
        "crane lifts the beam'. Never use this for creating brand-new media "
        "(that's 'generation') or for structured data (that's 'sql')"
    ),
    capabilities=SourceCapabilities(
        operations=frozenset({SourceOperation.SEARCH, SourceOperation.INGEST}),
        supports_citations=True,
        supports_pagination=False,
        supports_incremental_sync=False,
        has_side_effects=False,
        preserves_source_acl=False,
    ),
    permission=Permission.MEDIA_SEARCH,
    is_configured=lambda s: bool(s.enable_media_search and s.media_library_path),
)

_ATTACHMENTS = SourceDescriptor(
    name="attachments",
    kind=SourceKind.ATTACHMENT,
    description=(
        "a file the user attached directly to THIS specific question -- an "
        "image, PDF, Word/Excel/PowerPoint document, or plain text/CSV/JSON "
        "file uploaded just now, not anything already stored in the company's "
        "own systems. Use this whenever the question refers to 'this file', "
        "'the attached image/document', 'what I just uploaded', or similar"
    ),
    capabilities=SourceCapabilities(
        operations=frozenset({SourceOperation.SEARCH, SourceOperation.READ}),
        supports_citations=True,
        supports_pagination=False,
        supports_incremental_sync=False,
        has_side_effects=False,
        # The attachment belongs to the caller who uploaded it in this
        # session, so ownership is the access control.
        preserves_source_acl=True,
    ),
    # The orchestrator forces `attachments` into the route whenever it is
    # available and gates it on the flag, not on a role.
    permission=None,
    is_configured=lambda s: bool(s.enable_chat_attachments),
    per_request=True,
)

#: Declaration order is the order `GET /sources` reports and the order the
#: classifier sees its options in. `sql` stays first: it is the platform's
#: primary source and the one every deployment has.
BUILTIN_DESCRIPTORS: tuple[SourceDescriptor, ...] = (
    _SQL,
    _DOCUMENTS,
    _POLICY,
    _WEB,
    _GENERATION,
    _MEDIA_SEARCH,
    _ATTACHMENTS,
)
