"""REST/OpenAPI connector -- Batch 5 of
`docs/architecture/implementation-roadmap.md`.

Implements the `sources.types.SourceConnector` lifecycle Batch 1 defined,
so it is exercised by `tests/test_sources_conformance.py` alongside the
mock rather than having a bespoke test shape of its own.

**It is not a routable source yet.** There is no entry for it in
`sources/descriptors.py`, so `GET /sources` does not advertise it and the
router cannot reach it. That is deliberate: a descriptor whose source the
orchestrator has no node for would offer the picker something that cannot
answer. Wiring it into routing is a separate step, and wants a deployment
that actually has endpoints configured to test against. See
`sources/types.py`'s docstring on the "built, not wired" state the
platform's rules protect.

Security posture, all of it inherited rather than reinvented:

- Every URL is validated by `security.outbound_url.assert_safe_url` on
  **every** use -- HTTPS-only, host allowlist checked before DNS, and
  every resolved address checked against the private/reserved blocklist.
  Never validated once at configuration time (`CLAUDE.md` 5.6).
- Redirects are followed manually with each hop re-validated
  (`safe_get_json`).
- Responses are size-capped while streaming.
- Reads only. A `side_effect=True` endpoint is refused outright here;
  there is no opt-in flag for it yet, because the first such endpoint
  should arrive with its own approval gate rather than inherit a generic
  one.
- Everything a response contains is **data, never instructions**
  (`CLAUDE.md` 5.5), including an endpoint's own `description`. Nothing
  read here is ever placed in a prompt as a directive.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from config.settings import Settings
from rest.types import RestEndpoint, RestMethod
from security.outbound_url import (
    OutboundFetchError,
    OutboundUrlBlockedError,
    assert_safe_url,
    safe_get_json,
)
from sources.types import (
    HealthStatus,
    SourceCapabilities,
    SourceDescriptor,
    SourceEvidence,
    SourceKind,
    SourceNotConfiguredError,
    SourceOperation,
)

logger = logging.getLogger(__name__)

#: Audit/log label for every outbound call this connector makes.
_CONTEXT = "rest_connector"

#: Not in `sources.descriptors.BUILTIN_DESCRIPTORS` -- see the module
#: docstring on why this is not routable yet.
REST_DESCRIPTOR = SourceDescriptor(
    name="rest",
    kind=SourceKind.DOCUMENT,
    description=(
        "an operator-configured external REST API, queried read-only over " "an allowlisted host"
    ),
    capabilities=SourceCapabilities(
        operations=frozenset({SourceOperation.SEARCH, SourceOperation.DISCOVER}),
        supports_citations=True,
        # Each endpoint is one call; there is no cursor contract to honour
        # across arbitrary third-party APIs.
        supports_pagination=False,
        supports_incremental_sync=False,
        # Reads only -- a side-effecting endpoint is refused (see `search`).
        has_side_effects=False,
        # A shared API credential cannot filter per-caller, so authorization
        # for what comes back is this platform's job, not the source's.
        preserves_source_acl=False,
    ),
    permission=None,
)


def _navigate(body: Any, pointer: str | None) -> Any:
    """Walks `pointer` ("data.items") into `body`.

    Returns `body` unchanged for a None/empty pointer. A path that does not
    exist yields None rather than raising: a third-party API changing its
    shape is an ordinary operating condition, and it is reported as an
    empty result with a warning rather than as a crash.
    """
    if not pointer:
        return body
    current = body
    for part in pointer.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _excerpt(value: Any, limit: int = 2000) -> str:
    """Renders a response fragment as bounded text for evidence.

    Truncates rather than summarizing -- the excerpt has to be what the
    source actually returned, not this platform's paraphrase of it
    (`CLAUDE.md` 4.4: never present an inference as observed fact).
    """
    text = value if isinstance(value, str) else repr(value)
    return text[:limit]


class RestConnector:
    """Calls a fixed set of operator-declared REST endpoints.

    Args:
        endpoints: The declared endpoints. Order is the order `discover`
            reports and `search` tries them in.
    """

    def __init__(self, endpoints: tuple[RestEndpoint, ...] = ()) -> None:
        self._endpoints = endpoints
        self._closed = False
        self.close_count = 0

    @property
    def descriptor(self) -> SourceDescriptor:
        return REST_DESCRIPTOR

    def _allowed_hosts(self, settings: Settings) -> tuple[str, ...]:
        return tuple(settings.rest_allowed_hosts)

    def validate(self, settings: Settings) -> None:
        """Shape check only -- no network call, per the contract.

        Refuses when the feature is off, when no host is allowlisted, when
        no endpoint is declared, or when any declared endpoint names a
        host that is not allowlisted. The last one matters: an endpoint
        left behind after its host was removed from the allowlist must
        make configuration *invalid*, not silently unreachable, so an
        operator finds out at startup instead of from a confused user.
        """
        if not settings.enable_rest_connector:
            raise SourceNotConfiguredError(
                "ENABLE_REST_CONNECTOR is false",
                safe_message="That data source is not configured.",
            )
        if not settings.rest_allowed_hosts:
            raise SourceNotConfiguredError(
                "REST_ALLOWED_HOSTS is empty -- an allowlist with no entries permits nothing",
                safe_message="That data source is not configured.",
            )
        if not self._endpoints:
            raise SourceNotConfiguredError(
                "No REST endpoints are declared",
                safe_message="That data source is not configured.",
            )

        allowed = {host.lower() for host in settings.rest_allowed_hosts}
        for endpoint in self._endpoints:
            host = (urlparse(endpoint.url).hostname or "").lower()
            if host not in allowed:
                raise SourceNotConfiguredError(
                    f"Endpoint {endpoint.name!r} names host {host!r}, which is not "
                    "in REST_ALLOWED_HOSTS",
                    safe_message="That data source is not configured.",
                )

    def health(self, settings: Settings) -> HealthStatus:
        """Checks the declared endpoints are *callable*, without calling them.

        Runs the full URL validation (including DNS resolution and the
        blocklist check) but issues no request, so a health check cannot
        itself become a way to make this server hit an external service
        repeatedly. Returns a status rather than raising, because an
        unreachable source is an ordinary condition the UI reports.

        `detail` never contains a credential, a header or a full URL --
        only the endpoint name and a short reason (`CLAUDE.md` 5.6).
        """
        try:
            self.validate(settings)
        except SourceNotConfiguredError as exc:
            return HealthStatus(healthy=False, detail=str(exc.safe_message))

        hosts = self._allowed_hosts(settings)
        for endpoint in self._endpoints:
            try:
                assert_safe_url(endpoint.url, allowed_hosts=hosts, context=_CONTEXT)
            except OutboundUrlBlockedError:
                return HealthStatus(
                    healthy=False,
                    detail=f"Endpoint {endpoint.name!r} is not callable from this server.",
                )
        return HealthStatus(healthy=True, detail="All declared endpoints are callable.")

    def discover(self, settings: Settings) -> tuple[str, ...]:
        """Endpoint names only -- never a URL, which could carry a token in
        a query string, and never any response content."""
        self.validate(settings)
        return tuple(endpoint.name for endpoint in self._endpoints)

    def search(
        self, query: str, settings: Settings, *, limit: int = 10
    ) -> tuple[SourceEvidence, ...]:
        """Calls each read-only endpoint and returns the bodies as evidence.

        `query` is **not** interpolated into any URL. Letting a question's
        text reach a URL would hand an end user partial control of an
        outbound request -- path traversal, a swapped host via a crafted
        value, a credential leaked into a query string. Endpoints are
        fixed; filtering happens on what comes back. A connector that
        genuinely needs server-side search gets an endpoint declared for
        it, with the parameter validated at declaration time.

        Honours `limit` as a ceiling. An empty query matches nothing rather
        than everything, matching the mock connector's contract.
        """
        self.validate(settings)
        if not query.strip():
            return ()

        hosts = self._allowed_hosts(settings)
        needle = query.lower()
        out: list[SourceEvidence] = []

        for endpoint in self._endpoints:
            if len(out) >= limit:
                break
            if endpoint.side_effect:
                # Refused rather than skipped silently: a side-effecting
                # endpoint reaching a read path is a configuration error
                # worth surfacing, and this connector has no approval gate.
                logger.warning(
                    "[rest] endpoint %r declares a side effect and was not called",
                    endpoint.name,
                )
                continue
            if endpoint.method is not RestMethod.GET:
                continue

            try:
                body = safe_get_json(
                    endpoint.url,
                    allowed_hosts=hosts,
                    context=_CONTEXT,
                    timeout=settings.rest_timeout_seconds,
                    max_bytes=settings.rest_max_response_bytes,
                    max_redirects=settings.rest_max_redirects,
                )
            except (OutboundUrlBlockedError, OutboundFetchError) as exc:
                # Disclosed, never silently empty: an unreachable endpoint
                # becomes a warning on the evidence rather than a missing
                # row a reader would mistake for "nothing matched".
                logger.warning("[rest] endpoint %r failed: %s", endpoint.name, exc)
                out.append(
                    SourceEvidence(
                        source_id=endpoint.name,
                        source_name=REST_DESCRIPTOR.name,
                        title=endpoint.name,
                        excerpt="",
                        warnings=(str(exc.safe_message),),
                    )
                )
                continue

            fragment = _navigate(body, endpoint.json_pointer)
            if fragment is None:
                out.append(
                    SourceEvidence(
                        source_id=endpoint.name,
                        source_name=REST_DESCRIPTOR.name,
                        title=endpoint.name,
                        excerpt="",
                        warnings=("The response did not contain the expected field.",),
                    )
                )
                continue

            text = _excerpt(fragment)
            if needle not in text.lower():
                continue
            out.append(
                SourceEvidence(
                    source_id=endpoint.name,
                    source_name=REST_DESCRIPTOR.name,
                    title=endpoint.name,
                    excerpt=text,
                    # The endpoint URL is safe to cite: it is operator
                    # configuration, not a value from the response.
                    locator=endpoint.url,
                )
            )

        return tuple(out[:limit])

    def close(self) -> None:
        """Idempotent, as the contract requires. Nothing is held open --
        `requests` manages its own connections per call -- so this only
        records that the lifecycle completed."""
        self._closed = True
        self.close_count += 1

    @property
    def closed(self) -> bool:
        return self._closed
