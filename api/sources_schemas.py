"""Response models for `api/sources.py` -- Batch 1 of
`docs/architecture/implementation-roadmap.md`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SourceCapabilitiesOut(BaseModel):
    """What a source type supports, so the UI never special-cases by name."""

    model_config = ConfigDict(frozen=True)

    operations: list[str]
    supports_citations: bool
    supports_pagination: bool
    supports_incremental_sync: bool
    has_side_effects: bool
    preserves_source_acl: bool


class SourceOut(BaseModel):
    """One source type as this caller sees it.

    `configured` and `authorized` are reported separately on purpose. They
    are different problems with different owners -- an operator fixes
    configuration, an administrator fixes permission -- and a single
    "unavailable" flag would leave the UI unable to say which, which is the
    bug `agent/orchestrator/nodes.py` already records for the routing path.

    No secret, connection string, API key or host appears here. The only
    configuration fact exposed is the boolean `configured`.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    kind: str
    #: Prose describing what the source holds. Also what the LLM classifier
    #: reads, so the UI and the router cannot disagree about a source.
    description: str
    capabilities: SourceCapabilitiesOut
    #: Standing configuration makes this source usable at all.
    configured: bool
    #: This caller's roles permit it. A source can be configured but not
    #: authorized, and vice versa.
    authorized: bool
    #: The permission required, for an admin UI to explain a denial.
    #: None when the source needs no permission beyond asking a question.
    required_permission: str | None
    #: True when availability depends on the request rather than standing
    #: config -- `attachments` is available only when a file is attached to
    #: that particular question, so `configured` here means "the feature is
    #: on", not "usable right now".
    per_request: bool


class SourcesOut(BaseModel):
    """Every source type the platform knows about, annotated for this caller.

    Deliberately reports *all* registered sources rather than only the
    usable ones, so the UI can show a disabled source with a reason instead
    of silently omitting it. That is the same decision `GET /navigation`
    makes for screens.
    """

    model_config = ConfigDict(frozen=True)

    sources: list[SourceOut]
    #: Names this caller can actually route to right now, assuming no
    #: attachment on the request. A convenience projection of `sources`.
    usable: list[str]
    roles: list[str]
