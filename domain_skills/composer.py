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
from governance.domains import BusinessDomain, policy_for

logger = logging.getLogger(__name__)


def _prohibitions_block(domain: BusinessDomain) -> str:
    """The domain's prohibited actions, rendered as a constraint.

    **Derived from `governance.domains.policy_for`, never hand-written per
    skill.** Those prohibitions are already data with one owner, and HR is
    why that matters: it forbids `individual_performance_ranking`, while
    the shared contract invites "rank top and bottom performers". Writing
    the constraint by hand in each skill would mean fifteen chances to
    forget it, and a copy that could drift from the policy the governance
    engine actually enforces.

    Deriving it also means every future domain gets this for free, correct
    on the day its skills land.

    This is **defence in depth, not the control.**
    `governance/request_policy.py` and `governance/recommendation_safety.py`
    are what actually refuse a prohibited output; this only stops the model
    being invited to produce one in the first place. A domain with no
    prohibitions (MIS has none) yields an empty string and nothing is
    appended.
    """
    actions = sorted(action.value for action in policy_for(domain).prohibited_actions)
    if not actions:
        return ""
    phrased = "; ".join(action.replace("_", " ") for action in actions)
    return (
        "Domain constraints, which override every instruction above. This "
        "domain prohibits: " + phrased + ". Report in aggregate only. If a "
        "question can be answered only by doing one of these, say that "
        "plainly instead of doing it."
    )


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

    # Last, so it visibly overrides everything above -- including the
    # contract's own invitation to rank performers.
    prohibitions = _prohibitions_block(skill.domain)
    if prohibitions:
        parts.extend(["", prohibitions])

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
        # Registered elsewhere, or not registered at all. Not a failure --
        # fall through to the domain default below, logged for an operator
        # rather than surfaced to the caller.
        logger.info(
            "[domain_skills] requested skill %r is not available for domain %r; "
            "falling back to the domain default",
            preferred_slug,
            domain.value,
        )

    # A governance domain can hold more than one prompt-domain:
    # `SALES_MARKETING` holds both `sales` and `marketing`, with genuinely
    # different analytical scopes. With no slug to disambiguate there is no
    # honest default -- picking the first would frame a campaign question as
    # Revenue Intelligence, which is worse than not framing it at all.
    #
    # So: refuse to guess. Returning None means the analysis proceeds with
    # the platform's existing neutral framing, which is the correct
    # fail-open behaviour for an accuracy aid (`CLAUDE.md` 4.2). Resolving
    # this properly needs a classifier that can tell a sales question from a
    # marketing one, and that belongs with the wiring batch, not here.
    groups = {candidate.group for candidate in candidates}
    if len(groups) > 1:
        logger.info(
            "[domain_skills] domain %r holds %d prompt-domains (%s) and no skill "
            "was specified; applying no skill framing rather than guessing",
            domain.value,
            len(groups),
            ", ".join(sorted(groups)),
        )
        return None

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
