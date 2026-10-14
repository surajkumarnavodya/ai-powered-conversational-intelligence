"""Registry of source types -- Batch 1 of
`docs/architecture/implementation-roadmap.md`.

The registry answers three questions, and nothing else:

- what source types exist (`all_descriptors`)
- which are usable under this configuration (`available_descriptors`)
- which of those this caller may actually use (`authorized_descriptors`)

Splitting the last two matters. "Configured" and "permitted" are different
failures with different remedies -- an operator fixes the first, an
administrator the second -- and collapsing them would reproduce the exact
bug `agent/orchestrator/nodes.py` records in its own comments: a source
dropped silently, leaving the caller with an unrelated generic failure and
no idea their role was the reason.

This module holds no state beyond the built-in descriptor table, so it
needs none of the cache-clearing `tests/conftest.py` does for the
process-lifetime singletons.
"""

from __future__ import annotations

from collections import Counter

from agent.authz import Permission
from config.settings import Settings
from sources.descriptors import BUILTIN_DESCRIPTORS
from sources.types import SourceDescriptor, UnknownSourceError

#: Name -> descriptor, built once from the declared table. Keyed by the
#: routing name, which is the same name the orchestrator and the LLM
#: classifier use -- there is no second naming scheme to translate.
_BY_NAME: dict[str, SourceDescriptor] = {d.name: d for d in BUILTIN_DESCRIPTORS}

# A duplicate name would make one descriptor silently shadow another, and
# the shadowed one's permission could be the stricter of the two. Caught at
# import rather than left to a test, because the consequence is a
# permission check that quietly stops applying.
if len(_BY_NAME) != len(BUILTIN_DESCRIPTORS):
    _counts = Counter(d.name for d in BUILTIN_DESCRIPTORS)
    _dupes = sorted(name for name, count in _counts.items() if count > 1)
    raise RuntimeError(f"Duplicate source descriptor name(s): {', '.join(_dupes)}")


def all_names() -> tuple[str, ...]:
    """Every registered source name, in declaration order."""
    return tuple(d.name for d in BUILTIN_DESCRIPTORS)


def all_descriptors() -> tuple[SourceDescriptor, ...]:
    """Every registered descriptor, in declaration order."""
    return BUILTIN_DESCRIPTORS


def get_descriptor(name: str) -> SourceDescriptor:
    """The descriptor for `name`.

    Raises:
        UnknownSourceError: if `name` is not registered. Deliberately not a
            `None` return: an unregistered source must deny rather than
            degrade to a default (`CLAUDE.md` 5.3).
    """
    try:
        return _BY_NAME[name]
    except KeyError:
        raise UnknownSourceError(
            f"Unknown source {name!r}; registered: {', '.join(all_names())}",
            safe_message="That data source is not available.",
        ) from None


def is_registered(name: str) -> bool:
    """Whether `name` is a registered source. Never raises."""
    return name in _BY_NAME


def available_descriptors(
    settings: Settings, *, has_attachments: bool = False
) -> tuple[SourceDescriptor, ...]:
    """Every source that standing configuration makes usable.

    A feature flag alone is not enough: each descriptor's `is_configured`
    also requires whatever its module needs to run (a store connection
    string, an API key, a library path), because routing to a flagged-on
    but unconfigured source just fails. This is the rule
    `agent.orchestrator.nodes.get_available_sources` already applied; the
    descriptors are now where it lives.

    Args:
        has_attachments: Whether the caller attached file(s) to *this*
            request. Gates the `per_request` descriptors, whose
            availability does not come from standing config at all.
    """
    return tuple(
        d
        for d in BUILTIN_DESCRIPTORS
        if (has_attachments if d.per_request else True) and d.is_configured(settings)
    )


def authorized_descriptors(
    settings: Settings,
    permissions: frozenset[Permission],
    *,
    has_attachments: bool = False,
) -> tuple[SourceDescriptor, ...]:
    """The available sources this caller's permissions actually allow.

    A descriptor with `permission=None` needs nothing beyond the baseline
    ability to ask a question -- which is not the same as unrestricted.
    `sql` carries no source-level permission and is still bounded by SQL
    validation, the result gate, the restricted-column gate and tenant
    scoping. Source-level permission is one layer, never the only one.
    """
    return tuple(
        d
        for d in available_descriptors(settings, has_attachments=has_attachments)
        if d.permission is None or d.permission in permissions
    )
