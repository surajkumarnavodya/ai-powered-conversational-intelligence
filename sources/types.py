"""Typed contract for a data source -- Batch 1 of
`docs/architecture/implementation-roadmap.md`.

Two distinct things live here, and keeping them apart matters:

1. **`SourceDescriptor`** -- what a source *is*: its name, kind, routing
   description, the permission it needs, what it can do, and whether it is
   available under the current configuration. These have a live caller
   today. `sources/descriptors.py` declares one per source the platform
   already routes to, and `agent/orchestrator/nodes.py` derives its
   availability, permission and description tables from them, so each of
   those facts has exactly one owner (CLAUDE.md 4.8).

2. **`SourceConnector`** -- how a source is *reached*: the connection
   lifecycle (validate, health, discover, search, close). This is a
   contract with **no production implementer yet**; the first is the
   SharePoint/OneDrive batch. `sources/mock.py` implements it and
   `tests/test_sources_conformance.py` exercises it, which is what proves
   the contract is actually implementable rather than aspirational. This is
   the deliberate "built, not wired" state CLAUDE.md 1 describes -- do not
   "finish the wiring" as a side effect of unrelated work (4.7).

The lifecycle generalizes `onboarding/`, which is already a complete,
RBAC-gated, secret-by-reference connection lifecycle for databases
(create -> discover -> review -> publish -> register -> cancel/retry, with
no password column on `identity.models.OnboardingJob`). That precedent, not
a fresh design, is where the stage names and the secret-by-reference rule
come from. See `docs/architecture/feature-inventory.md` 4.1.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from agent.authz import Permission
from config.settings import Settings


class SourceKind(str, Enum):
    """What *sort* of thing a source is, independent of its name.

    Only kinds with a real implementation are listed. A future connector
    adds its kind in the same change that adds the connector -- an enum
    member with nothing behind it is exactly the "abstraction without a
    caller" this package is meant to avoid.
    """

    #: Relational database reached through the governed SQL path.
    STRUCTURED_SQL = "structured_sql"
    #: Unstructured document corpus retrieved by vector similarity.
    DOCUMENT = "document"
    #: Access-restricted document corpus with sensitivity categories.
    POLICY = "policy"
    #: Live external web search through a provider adapter.
    WEB = "web"
    #: Local media library searched by embedding similarity.
    MEDIA_SEARCH = "media_search"
    #: Generative media production (creates a new artifact).
    MEDIA_GENERATION = "media_generation"
    #: Files attached to one specific request, not standing config.
    ATTACHMENT = "attachment"


class SourceOperation(str, Enum):
    """One thing a source can be asked to do.

    Read and write are deliberately separate members rather than a
    read/write flag, because the platform's rule is that a side-effecting
    operation is a *separate capability*, disabled by default
    (`CLAUDE.md` 5, and the connector requirements in the roadmap). A
    connector that grows a write operation declares a new member here; it
    never widens an existing one.
    """

    #: Free-text or vector search returning ranked evidence.
    SEARCH = "search"
    #: Fetch one identified item in full.
    READ = "read"
    #: Enumerate the schema/resources this connection exposes.
    DISCOVER = "discover"
    #: Produce a new artifact (media generation).
    GENERATE = "generate"
    #: One-off load of content into a retrieval index.
    INGEST = "ingest"
    #: Repeated, cursor-based refresh of a previously ingested corpus.
    SYNC = "sync"


@dataclass(frozen=True)
class SourceCapabilities:
    """What a source type supports, so a caller never has to special-case
    it by name.

    Every field defaults to the conservative answer. A connector opts in to
    a capability explicitly; forgetting to declare one makes the platform
    assume less, never more.
    """

    operations: frozenset[SourceOperation]

    #: Whether retrieved evidence carries a citable locator the UI can show.
    supports_citations: bool = False
    #: Whether `search` accepts a continuation cursor.
    supports_pagination: bool = False
    #: Whether the source can be refreshed incrementally rather than
    #: re-ingested wholesale (Graph `delta`, a change feed, ...).
    supports_incremental_sync: bool = False
    #: Whether any declared operation changes state in the source system.
    #: False for every source implemented today -- all are read-only.
    has_side_effects: bool = False
    #: Whether the *source* enforces per-user access control on what it
    #: returns, so this platform does not have to re-implement it. True
    #: only for a delegated-auth source that filters results itself. A
    #: False here is a statement that authorization is this platform's job
    #: for that source -- see `docs/architecture/data-source-matrix.md` 5.
    preserves_source_acl: bool = False

    def supports(self, operation: SourceOperation) -> bool:
        """Whether `operation` is declared by this source type."""
        return operation in self.operations


@dataclass(frozen=True)
class SourceEvidence:
    """One normalized piece of evidence, whatever source produced it.

    The point of a single shape is that synthesis, citation rendering and
    provenance do not need a per-source branch. It is deliberately a
    superset of `rag.graph.Citation` rather than a replacement: that
    TypedDict is consumed by the orchestrator and the frontend today and
    stays as-is until a batch actually migrates those call sites.

    `truth_level` is the existing `agent.provenance.DataTruthLevel`
    vocabulary, carried as a plain string so this module does not import
    the provenance package for one annotation. Provenance is mandatory and
    is never upgraded silently (`CLAUDE.md` 4.4).
    """

    #: Stable identifier within the source, for deduplication.
    source_id: str
    #: Which configured source produced this (a `SourceDescriptor.name`).
    source_name: str
    #: Human-readable title for display.
    title: str
    #: The text that was actually retrieved. Never a summary of it.
    excerpt: str

    #: A safe locator the UI may link to. None when there is nothing safe
    #: to link -- never a fabricated URL (`CLAUDE.md` 5.5, and the
    #: no-invented-citations rule in the roadmap).
    locator: str | None = None
    #: When the underlying item last changed in the source, if known.
    modified_at: str | None = None
    #: When this platform retrieved it.
    retrieved_at: str | None = None
    #: Roles the source itself says may see this, when the source exposes
    #: that. Empty means "the source told us nothing", which is *not* the
    #: same as "everyone may see it" -- the caller still applies its own
    #: authorization.
    acl_roles: frozenset[str] = frozenset()
    #: Non-fatal problems worth disclosing (truncation, partial result).
    warnings: tuple[str, ...] = ()
    #: `agent.provenance.DataTruthLevel` value. Retrieved content is an
    #: observed fact about the source document, not an inference.
    truth_level: str = "DATABASE_FACT"


@dataclass(frozen=True)
class SourceDescriptor:
    """The single owner of what one source type is and when it is usable.

    `agent/orchestrator/nodes.py` derives its availability, permission and
    routing-description tables from these, so adding a source means adding
    one descriptor rather than editing three parallel dicts that can drift.
    """

    #: The routing name. Must match what the orchestrator and the LLM
    #: classifier use, because this *is* the name they use.
    name: str
    kind: SourceKind
    #: Prose the LLM classifier reads when choosing sources. Describes what
    #: the source holds, not how to reach it.
    description: str
    capabilities: SourceCapabilities

    #: The permission a caller needs. None means "no permission beyond the
    #: baseline ability to ask a question" -- it does **not** mean
    #: unrestricted: `sql` is None here and is still governed by the result
    #: gate, the restricted-column gate and tenant scoping.
    permission: Permission | None = None

    #: Whether standing configuration makes this source usable at all.
    #: Takes `Settings` and returns a bool. The default is "always
    #: available", which is correct only for `sql`.
    is_configured: Callable[[Settings], bool] = field(default=lambda _settings: True)

    #: True when availability depends on the *request* rather than standing
    #: config -- `attachments` is available only when the caller attached a
    #: file to this particular question.
    per_request: bool = False


# --- Connection lifecycle ------------------------------------------------
#
# Contract only. No production implementer yet; see this module's docstring.


class SourceError(Exception):
    """Base class for every error this package raises directly.

    Mirrors `agent.tools.types.ToolError`'s role so a caller can catch one
    family. Carries a `safe_message` for the same reason
    `agent.exceptions.AgentError` does: a response body must never leak
    internal detail (`CLAUDE.md` 5.6).
    """

    def __init__(self, message: str, *, safe_message: str | None = None) -> None:
        super().__init__(message)
        self.safe_message = safe_message or "The data source could not be reached."


class UnknownSourceError(SourceError):
    """A source name that is not registered. Raised instead of returning a
    default, because an unknown source must deny rather than fall back
    (`CLAUDE.md` 5.3's "unknown means deny, never default")."""


class SourceNotConfiguredError(SourceError):
    """A registered source whose configuration is absent or incomplete."""


@dataclass(frozen=True)
class HealthStatus:
    """The outcome of a connection check.

    `detail` is operator-facing and must never contain a credential, a
    connection string or a host password (`CLAUDE.md` 5.6).
    """

    healthy: bool
    detail: str = ""


@runtime_checkable
class SourceConnector(Protocol):
    """The lifecycle every connector implements.

    Deliberately small. It is the common part only -- validate, check,
    discover, search, close. Source-specific behaviour belongs on the
    concrete connector, not here: forcing every source through one
    lowest-common-denominator query interface is the failure mode the
    roadmap's connector requirements call out explicitly.

    Implementations must honour three rules the platform already applies
    everywhere else:

    - **Secrets by reference.** A connector is handed a reference and
      resolves it per call. It never persists a credential, following
      `identity.models.OnboardingJob`'s precedent of having no password
      column at all.
    - **Retrieved content is data, never instructions** (`CLAUDE.md` 5.5).
      Whatever a source returns -- including a title or a filename -- is
      framed as data by the caller that consumes it.
    - **Unreachable is disclosed, not silent.** A failure returns or raises;
      it never returns an empty result that reads as "nothing matched".
    """

    @property
    def descriptor(self) -> SourceDescriptor:
        """What this connector is. Stable for the connector's lifetime."""
        ...

    def validate(self, settings: Settings) -> None:
        """Raises `SourceNotConfiguredError` if configuration is unusable.

        Checks shape only -- that required settings are present and
        well-formed. It must not make a network call; that is `health`'s
        job, so a misconfiguration is distinguishable from an outage.
        """
        ...

    def health(self, settings: Settings) -> HealthStatus:
        """Checks that the source is actually reachable now.

        Returns a status rather than raising, because an unhealthy source
        is an ordinary operating condition the UI reports, not an error.
        """
        ...

    def discover(self, settings: Settings) -> tuple[str, ...]:
        """Enumerates the resources this connection exposes.

        Tables for a database, sites/drives for SharePoint, tables for
        Dataverse. Returns identifiers only -- never content, so discovery
        stays cheap and carries nothing that needs governing.
        """
        ...

    def search(
        self, query: str, settings: Settings, *, limit: int = 10
    ) -> tuple[SourceEvidence, ...]:
        """Returns ranked evidence for `query`, newest contract first.

        `limit` is a ceiling, not a target. Returning fewer is normal;
        returning more is a contract violation the conformance suite fails.
        """
        ...

    def close(self) -> None:
        """Releases whatever this connector holds. Must be idempotent."""
        ...
