"""A reference `SourceConnector` for development and conformance testing.

This exists for two reasons, both called for by the connector framework
requirements in `docs/architecture/implementation-roadmap.md`:

1. It is the worked example a real connector is written against. Every
   behaviour the conformance suite checks is demonstrated here once, so a
   new connector author reads one file rather than inferring the contract
   from a Protocol.
2. It makes the contract *falsifiable*. A Protocol alone cannot be wrong;
   an implementation plus a suite that runs against it can be, which is
   what keeps `sources/types.py` honest.

It is **never** registered as a routable source and must never become one:
`sources/descriptors.py` declares only real sources, and a mock in
`GET /sources` would advertise a source the router cannot reach. Nothing in
`api/` or `agent/` imports this module -- only tests do.

`FailingMockConnector` is here for the same reason: the suite has to prove
the contract's failure behaviour, not only its success path.
"""

from __future__ import annotations

from config.settings import Settings
from sources.types import (
    HealthStatus,
    SourceCapabilities,
    SourceDescriptor,
    SourceEvidence,
    SourceKind,
    SourceNotConfiguredError,
    SourceOperation,
)

#: Not in `BUILTIN_DESCRIPTORS` -- deliberately. See the module docstring.
MOCK_DESCRIPTOR = SourceDescriptor(
    name="mock",
    kind=SourceKind.DOCUMENT,
    description="an in-memory corpus used only by tests and local development",
    capabilities=SourceCapabilities(
        operations=frozenset(
            {
                SourceOperation.SEARCH,
                SourceOperation.READ,
                SourceOperation.DISCOVER,
            }
        ),
        supports_citations=True,
        supports_pagination=False,
        supports_incremental_sync=False,
        has_side_effects=False,
        preserves_source_acl=False,
    ),
    permission=None,
)


class MockConnector:
    """An in-memory connector that satisfies the whole lifecycle.

    Args:
        corpus: Title -> body. Searched by case-insensitive substring,
            which is deliberately crude: the suite tests the *contract*
            (ordering, the `limit` ceiling, evidence shape), not retrieval
            quality.
        configured: When False, `validate` raises, so a test can exercise
            the misconfiguration path without touching real settings.
        healthy: What `health` reports. Independent of `configured`,
            because a correctly-configured source can still be down -- the
            contract requires those two be distinguishable.
    """

    def __init__(
        self,
        corpus: dict[str, str] | None = None,
        *,
        configured: bool = True,
        healthy: bool = True,
    ) -> None:
        self._corpus = dict(corpus or {"readme": "the mock corpus body"})
        self._configured = configured
        self._healthy = healthy
        self._closed = False
        #: Counts `close` calls so a test can prove idempotency was
        #: exercised rather than merely not crashing.
        self.close_count = 0

    @property
    def descriptor(self) -> SourceDescriptor:
        return MOCK_DESCRIPTOR

    def validate(self, settings: Settings) -> None:
        """Shape check only -- no network call, per the contract."""
        if not self._configured:
            raise SourceNotConfiguredError(
                "MockConnector was constructed with configured=False",
                safe_message="That data source is not configured.",
            )

    def health(self, settings: Settings) -> HealthStatus:
        if not self._healthy:
            # No credential, host or connection string in the detail.
            return HealthStatus(healthy=False, detail="The mock source is unavailable.")
        return HealthStatus(healthy=True, detail="Reachable.")

    def discover(self, settings: Settings) -> tuple[str, ...]:
        """Identifiers only, never content."""
        return tuple(sorted(self._corpus))

    def search(
        self, query: str, settings: Settings, *, limit: int = 10
    ) -> tuple[SourceEvidence, ...]:
        """Case-insensitive substring match over the corpus.

        Honours `limit` as a ceiling. An empty query matches nothing rather
        than everything -- returning the whole corpus for no query would
        hand the synthesis step a pile of irrelevant context.
        """
        self.validate(settings)
        if not query.strip():
            return ()
        needle = query.lower()
        hits = [
            SourceEvidence(
                source_id=title,
                source_name=MOCK_DESCRIPTOR.name,
                title=title,
                excerpt=body,
                locator=None,
                retrieved_at=None,
            )
            for title, body in sorted(self._corpus.items())
            if needle in title.lower() or needle in body.lower()
        ]
        return tuple(hits[:limit])

    def close(self) -> None:
        """Idempotent, as the contract requires."""
        self._closed = True
        self.close_count += 1

    @property
    def closed(self) -> bool:
        return self._closed


class FailingMockConnector(MockConnector):
    """A connector whose `search` raises, for the disclosure path.

    The platform's rule is that an unreachable source is disclosed, never
    silently empty (`CLAUDE.md` 4.2: an accuracy aid fails open, but it
    does so *visibly*). A test needs something that actually raises to
    prove a caller reports the failure instead of rendering "no results".
    """

    def search(
        self, query: str, settings: Settings, *, limit: int = 10
    ) -> tuple[SourceEvidence, ...]:
        raise SourceNotConfiguredError(
            "FailingMockConnector always raises",
            safe_message="That data source could not be reached.",
        )
