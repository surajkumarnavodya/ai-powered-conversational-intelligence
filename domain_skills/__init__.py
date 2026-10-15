"""Domain analytics skills -- Batch 1 (Sales).

A *skill* is a narrow analytical focus inside a business domain. It shapes
how the platform frames an analysis; it never decides what a caller may
see.

- `types.py` -- the `DomainSkill` contract and the three boundaries it must
  respect
- `contract.py` -- the shared analytics framing, written once for all skills
- `catalog.py` -- the declared skills; the only place one is introduced
- `registry.py` -- lookup, plus "which domains are actually covered"
- `composer.py` -- builds the additive guidance block

Off by default (`Settings.enable_domain_skills`). Skills are in-code
constants, never database rows, for the reason `CLAUDE.md` 5.5 gives: a
row that becomes a system prompt turns every write into prompt injection
with full pipeline reach.

Coverage is honest and queryable: `registry.covered_domains()` answers
"which of the eighteen domains have skills" from the catalog itself, so no
document can claim coverage the code does not have.
"""

from __future__ import annotations

from domain_skills.composer import compose_guidance, guidance_for, select_skill
from domain_skills.contract import ANALYTICS_CONTRACT, RESPONSE_SECTIONS
from domain_skills.registry import (
    all_skills,
    all_slugs,
    covered_domains,
    get_skill,
    is_registered,
    skills_for_domain,
)
from domain_skills.types import DomainSkill, DomainSkillError, UnknownSkillError

__all__ = [
    "ANALYTICS_CONTRACT",
    "RESPONSE_SECTIONS",
    "DomainSkill",
    "DomainSkillError",
    "UnknownSkillError",
    "all_skills",
    "all_slugs",
    "compose_guidance",
    "covered_domains",
    "get_skill",
    "guidance_for",
    "is_registered",
    "select_skill",
    "skills_for_domain",
]
