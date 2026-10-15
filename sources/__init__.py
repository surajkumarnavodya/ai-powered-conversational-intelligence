"""Source-type contract and registry -- Batch 1 of
`docs/architecture/implementation-roadmap.md`.

Answers "what sources exist, which are configured, and which may this
caller use" from one place, so the orchestrator, the API and (later) the
UI all read the same table instead of three that can drift.

- `sources/types.py` -- the typed contract (`SourceDescriptor`,
  `SourceCapabilities`, `SourceEvidence`, the `SourceConnector` lifecycle)
- `sources/descriptors.py` -- the declared source types; the single owner
  of each source's name, routing description, permission and availability
- `sources/registry.py` -- lookup, plus the configured/authorized split
- `sources/mock.py` -- reference connector, for tests only

The connection *lifecycle* half of the contract has no production
implementer yet; the first is the SharePoint/OneDrive batch. See
`sources/types.py`'s docstring for why that is deliberate rather than
unfinished.
"""

from __future__ import annotations

from sources.registry import (
    all_descriptors,
    all_names,
    authorized_descriptors,
    available_descriptors,
    get_descriptor,
    is_registered,
)
from sources.types import (
    HealthStatus,
    SourceCapabilities,
    SourceConnector,
    SourceDescriptor,
    SourceError,
    SourceEvidence,
    SourceKind,
    SourceNotConfiguredError,
    SourceOperation,
    UnknownSourceError,
)

__all__ = [
    "HealthStatus",
    "SourceCapabilities",
    "SourceConnector",
    "SourceDescriptor",
    "SourceError",
    "SourceEvidence",
    "SourceKind",
    "SourceNotConfiguredError",
    "SourceOperation",
    "UnknownSourceError",
    "all_descriptors",
    "all_names",
    "authorized_descriptors",
    "available_descriptors",
    "get_descriptor",
    "is_registered",
]
