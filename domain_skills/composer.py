"""Builds the guidance block a skill contributes to an analysis prompt.

**Everything this module emits comes from frozen, in-code constants.** No
caller-supplied text, no retrieved document, no database value and no model
output reaches the returned string. That is the property that makes a skill
safe to put in a system message at all, and
`tests/test_domain_skills.py::TestNothingUntrustedReachesThePrompt` pins it.

The composed block is **additive**. It is a separate system message placed
after the existing one, never a modification of it. Two reasons:

- `agent/llm_client.py::_INSIGHT_SYSTEM_PROMPT` and the generation prompt
  are benchmark-pinned (`CLAUDE.md` §12): the accuracy figures in
  `docs/EVALUATION.md` were measured against their exact wording, so
  editing them would invalidate the only real accuracy numbers this project
  has.
- Additive means the default path is byte-identical. With the feature off,
  or with no skill matched, nothing is appended and every existing caller
  behaves exactly as before.
"""

from __future__ import annotations

import logging

from domain_skills.contract import ANALYTICS_CONTRACT, RESPONSE_SECTIONS
from domain_skills.registry import skills_for_domain
from domain_skills.types import DomainSkill
from governance.domains import BusinessDomain

logger = logging.getLogger(__name__)


def compose_guidance(skill: DomainSkill) -> str:
    """The full guidance block for one skill.

    Assembled from `ANALYTICS_CONTRACT` (shared, written once) plus the
    skill's own scope, focus and dimensions. Deliberately built by
    concatenation rather than `str.format`: a format slot is a hole, and a
    hole in a system prompt is somewhere untrusted text could one day be
    passed.
    """
    parts = [
        ANALYTICS_CONTRACT,
        "",
        f"Domain: {skill.domain.value}",
        f"Analytical scope: {skill.scope}",
        f"Focus for this question: {skill.focus}",
    ]
    if skill.dimensions:
        parts.append("Dimensions worth considering: " + ", ".join(skill.dimensions) + ".")
    parts.extend(
        [
            "",
            "Structure the analysis under these headings, omitting any you "
            "genuinely have nothing to say under: " + ", ".join(RESPONSE_SECTIONS) + ".",
        ]
    )
    return "\n".join(parts)


def select_skill(domain: BusinessDomain, preferred_slug: str | None = None) -> DomainSkill | None:
    """Picks the skill to frame a question in `domain`.

    Args:
        preferred_slug: An explicit choice, if the caller made one. It is
            honoured **only** when it is registered *and* belongs to
            `domain` — a slug naming another domain's skill is ignored
            rather than followed, so a caller cannot use the skill
            parameter to pull in framing for a domain their question was
            not routed to.

    Returns:
        The chosen skill, or None when the domain has none declared — which
        is the common case today, and must degrade to the platform's
        existing behaviour rather than to a generic skill.
    """
    candidates = skills_for_domain(domain)
    if not candidates:
        return None

    if preferred_slug:
        for candidate in candidates:
            if candidate.slug == preferred_slug:
                return candidate
        # Registered elsewhere, or not registered at all. Either way this is
        # not a failure: the question still gets its domain's default
        # framing, and the mismatch is logged for an operator rather than
        # surfaced to the caller.
        logger.info(
            "[domain_skills] requested skill %r is not available for domain %r; "
            "using the domain default",
            preferred_slug,
            domain.value,
        )

    return candidates[0]


def guidance_for(
    domain: BusinessDomain,
    *,
    enabled: bool,
    preferred_slug: str | None = None,
) -> tuple[str, DomainSkill] | None:
    """The guidance block and the skill it came from, or None.

    Returns None — meaning "change nothing" — when the feature is off, when
    the domain has no declared skill, or when the domain is `GENERAL`.
    `GENERAL` is excluded deliberately: `governance/domains.py` treats it as
    "domain unknown", and the right response to an unknown domain is the
    platform's existing neutral framing, not a guess.

    A skill is an accuracy aid, so every one of those paths fails open
    (`CLAUDE.md` §4.2).
    """
    if not enabled:
        return None
    if domain is BusinessDomain.GENERAL:
        return None

    skill = select_skill(domain, preferred_slug)
    if skill is None:
        return None
    return compose_guidance(skill), skill
