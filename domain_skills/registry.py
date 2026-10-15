"""Lookup over the declared skills.

Holds no state beyond the catalog built at import, so it needs none of the
cache-clearing `tests/conftest.py` does for the process-lifetime singletons.
"""

from __future__ import annotations

from collections import Counter

from domain_skills.catalog import ALL_SKILLS
from domain_skills.types import DomainSkill, UnknownSkillError
from governance.domains import BusinessDomain

_BY_SLUG: dict[str, DomainSkill] = {s.slug: s for s in ALL_SKILLS}

# A duplicate slug would make one skill silently shadow another, and the
# shadowed one could be the narrower of the two. Caught at import rather
# than left to a test, because the consequence is a question being framed
# by guidance nobody chose.
if len(_BY_SLUG) != len(ALL_SKILLS):
    _dupes = sorted(n for n, c in Counter(s.slug for s in ALL_SKILLS).items() if c > 1)
    raise RuntimeError(f"Duplicate domain-skill slug(s): {', '.join(_dupes)}")


def all_skills() -> tuple[DomainSkill, ...]:
    """Every registered skill, in declaration order."""
    return ALL_SKILLS


def all_slugs() -> tuple[str, ...]:
    """Every registered slug, in declaration order."""
    return tuple(s.slug for s in ALL_SKILLS)


def get_skill(slug: str) -> DomainSkill:
    """The skill registered as `slug`.

    Raises:
        UnknownSkillError: `slug` is not registered. Deliberately not a
            `None` return — an unknown skill must deny rather than degrade
            to some default framing.
    """
    try:
        return _BY_SLUG[slug]
    except KeyError:
        raise UnknownSkillError(
            f"Unknown domain skill {slug!r}; registered: {', '.join(all_slugs())}",
            safe_message="That analysis skill is not available.",
        ) from None


def is_registered(slug: str) -> bool:
    """Whether `slug` is registered. Never raises."""
    return slug in _BY_SLUG


def skills_for_domain(domain: BusinessDomain) -> tuple[DomainSkill, ...]:
    """Every skill declared for `domain`, in declaration order.

    An unregistered domain returns an empty tuple rather than raising: most
    of the eighteen domains have no skills yet, and "this domain has no
    specialised framing" is an ordinary state that must degrade to the
    platform's existing behaviour, not an error.
    """
    return tuple(s for s in ALL_SKILLS if s.domain is domain)


def covered_domains() -> tuple[BusinessDomain, ...]:
    """Domains that have at least one declared skill.

    Useful for an honest status surface: it answers "which of the eighteen
    are actually covered" from the catalog itself, so no document has to
    claim coverage separately and risk drifting from it.
    """
    seen: list[BusinessDomain] = []
    for skill in ALL_SKILLS:
        if skill.domain not in seen:
            seen.append(skill.domain)
    return tuple(seen)
