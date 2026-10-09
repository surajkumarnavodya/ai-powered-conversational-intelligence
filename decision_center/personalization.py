"""Role-based presentation that cannot change a governed fact.

The mission asks for personalization "without changing governed facts".
That is a strong requirement and it is met structurally, not by
convention: `personalize` takes assembled sections and may only

- **reorder** sections, and
- **cap** how many headlines each shows (reporting the true total
  alongside, so a capped section says so).

It cannot filter by content, cannot drop a section, cannot touch a
headline's title, severity, evidence, provenance or truth level, and
cannot reach a number at all -- the function never constructs a
`Headline` or a `HealthIndicator`, it only re-sequences the ones it was
given. `tests/security/test_decision_center_personalization_never_changes_facts.py`
asserts that two different profiles over identical input produce
byte-identical headline content.

**Every section is always present for every role**, including empty
ones. Hiding a section from a role would mean an executive could be
shown a briefing that silently omits an open risk, and would make
"nothing to report" indistinguishable from "not shown to you". Ordering
expresses what a role looks at first; it never decides what they are
allowed to know.

Which profile a viewer gets is decided from their real, server-resolved
permissions (`security/navigation.py`'s own capability rules), never
from anything the client sends.
"""

from __future__ import annotations

from collections.abc import Sequence

from decision_center.models import BriefingSection, PersonalizationProfile, SectionId

#: Executive: outcomes and health first -- did what we decided work.
_EXECUTIVE_ORDER = (
    SectionId.BUSINESS_HEALTH,
    SectionId.KPI_MOVEMENT,
    SectionId.OUTCOMES,
    SectionId.RECOMMENDATIONS,
    SectionId.RISKS,
    SectionId.ANOMALIES,
    SectionId.PENDING_DECISIONS,
    SectionId.ACTIONS,
)

#: Analyst: the raw signal first -- what moved and why.
_ANALYST_ORDER = (
    SectionId.BUSINESS_HEALTH,
    SectionId.ANOMALIES,
    SectionId.KPI_MOVEMENT,
    SectionId.RISKS,
    SectionId.RECOMMENDATIONS,
    SectionId.PENDING_DECISIONS,
    SectionId.ACTIONS,
    SectionId.OUTCOMES,
)

#: Operator: the queue first -- what needs doing and deciding.
_OPERATOR_ORDER = (
    SectionId.BUSINESS_HEALTH,
    SectionId.PENDING_DECISIONS,
    SectionId.ACTIONS,
    SectionId.RISKS,
    SectionId.KPI_MOVEMENT,
    SectionId.ANOMALIES,
    SectionId.RECOMMENDATIONS,
    SectionId.OUTCOMES,
)

PROFILES: dict[str, PersonalizationProfile] = {
    "executive": PersonalizationProfile(
        profile_id="executive", section_order=_EXECUTIVE_ORDER, headlines_per_section=5
    ),
    "analyst": PersonalizationProfile(
        profile_id="analyst", section_order=_ANALYST_ORDER, headlines_per_section=10
    ),
    "operator": PersonalizationProfile(
        profile_id="operator", section_order=_OPERATOR_ORDER, headlines_per_section=10
    ),
}

#: The profile for a viewer whose capabilities match none of the above.
#: Executive ordering is the safe default: it leads with health and
#: movement, which every role needs, rather than a queue only some roles
#: can act on.
DEFAULT_PROFILE_ID = "executive"


def profile_for_capabilities(capabilities: Sequence[str]) -> PersonalizationProfile:
    """Picks a profile from the viewer's own server-resolved capabilities.

    Args:
        capabilities: `security.navigation.resolve_capabilities`' output
            for this viewer -- derived from their real permissions, never
            from a client-supplied hint.

    Returns:
        The matching profile, or the executive default.

    Ordering of the checks is deliberate: someone who can both review
    governance and ask questions is an operator first, because the queue
    is the thing only they can clear.
    """
    held = set(capabilities)
    if held & {"manage_governance", "review_governance", "manage_onboarding", "platform_admin"}:
        return PROFILES["operator"]
    if held & {"review_catalog", "review_recommendations", "execute_sql"}:
        return PROFILES["analyst"]
    return PROFILES[DEFAULT_PROFILE_ID]


def personalize(
    sections: Sequence[BriefingSection], profile: PersonalizationProfile
) -> tuple[BriefingSection, ...]:
    """Reorders and caps. Nothing else.

    Each returned section keeps its own `total_available`, so capping is
    visible rather than silent. A section the profile's order does not
    mention still appears, after the ones it does -- a profile cannot
    remove a section even by omission.
    """
    by_id = {section.section: section for section in sections}
    ordered_ids = [section_id for section_id in profile.section_order if section_id in by_id]
    ordered_ids += [
        section.section for section in sections if section.section not in set(ordered_ids)
    ]

    result: list[BriefingSection] = []
    for section_id in ordered_ids:
        section = by_id[section_id]
        result.append(
            BriefingSection(
                section=section.section,
                title=section.title,
                # Slicing an already-ranked tuple: the headlines that
                # survive are untouched objects, not rebuilt ones.
                headlines=section.headlines[: profile.headlines_per_section],
                total_available=section.total_available,
                empty_reason=section.empty_reason,
            )
        )
    return tuple(result)


__all__ = [
    "DEFAULT_PROFILE_ID",
    "PROFILES",
    "personalize",
    "profile_for_capabilities",
]
