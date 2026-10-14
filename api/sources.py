"""Source capability discovery -- Batch 1 of
`docs/architecture/implementation-roadmap.md`.

`GET /sources`: which data sources exist, which are configured, and which
this caller may route to. Computed server-side from the same descriptor
table `agent/orchestrator/nodes.py` routes with and the same permission
check `router_node` applies, so the frontend never guesses from a role name
and cannot disagree with the router about what a source is.

This mirrors `api/navigation.py` deliberately -- same pattern, same
reasoning, one step down the stack: that endpoint answers "which screens
may this caller open", this one answers "which sources may this caller
ask". Both are UX affordances, **not** boundaries. The real gate stays
where it was: `router_node` filters the LLM's own source selection through
`SOURCE_PERMISSIONS` before any subgraph runs, and every data-touching
route re-checks. Hiding a source here is cosmetic; a caller who asks for a
source anyway is still refused server-side (`CLAUDE.md` 5.2).

Read-only, no side effects, no rate limit of its own beyond the app-wide
one -- it reads an in-process table and makes no LLM, database or network
call.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from agent.authz import permissions_for
from api.auth import verify_api_key
from api.authz import get_auth_identity
from api.sources_schemas import SourceCapabilitiesOut, SourceOut, SourcesOut
from config.settings import get_settings
from security.oidc import AuthIdentity
from sources.registry import all_descriptors
from sources.types import SourceDescriptor

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sources", tags=["sources"])


def _to_out(descriptor: SourceDescriptor, *, configured: bool, authorized: bool) -> SourceOut:
    """Projects one descriptor for the response.

    `operations` is sorted so the response is stable across runs --
    `SourceCapabilities.operations` is a frozenset, whose iteration order
    is not guaranteed, and an unstable response body makes a contract test
    flaky for no reason.
    """
    caps = descriptor.capabilities
    return SourceOut(
        name=descriptor.name,
        kind=descriptor.kind.value,
        description=descriptor.description,
        capabilities=SourceCapabilitiesOut(
            operations=sorted(op.value for op in caps.operations),
            supports_citations=caps.supports_citations,
            supports_pagination=caps.supports_pagination,
            supports_incremental_sync=caps.supports_incremental_sync,
            has_side_effects=caps.has_side_effects,
            preserves_source_acl=caps.preserves_source_acl,
        ),
        configured=configured,
        authorized=authorized,
        required_permission=(
            descriptor.permission.value if descriptor.permission is not None else None
        ),
        per_request=descriptor.per_request,
    )


@router.get("", response_model=SourcesOut)
def list_sources(
    request: Request,
    _auth: None = Depends(verify_api_key),
    identity: AuthIdentity = Depends(get_auth_identity),
) -> SourcesOut:
    """Every registered source, annotated for this caller.

    Reports all sources rather than only the usable ones, so the UI can
    show a source as unavailable *with a reason* instead of omitting it.
    `configured` is evaluated without attachments, since whether this
    caller will attach a file to some future question is unknowable here --
    `per_request` marks the sources for which that distinction matters.
    """
    settings = get_settings()
    permissions = permissions_for(identity.roles)

    out: list[SourceOut] = []
    usable: list[str] = []
    for descriptor in all_descriptors():
        # `per_request` sources are reported on their standing flag alone;
        # their real availability depends on the individual request.
        configured = bool(descriptor.is_configured(settings))
        authorized = descriptor.permission is None or descriptor.permission in permissions
        out.append(_to_out(descriptor, configured=configured, authorized=authorized))
        if configured and authorized and not descriptor.per_request:
            usable.append(descriptor.name)

    return SourcesOut(sources=out, usable=usable, roles=list(identity.roles))
