"""Typed contract for a domain analytics skill.

A *skill* is a narrow analytical focus within a business domain — "revenue
intelligence" inside Sales, "attrition analysis" inside HR. It shapes how
the platform *analyses* a result; it never decides what a caller may see.

**Three boundaries that are not negotiable, stated here because a skill is
exactly the kind of object someone would later be tempted to make
editable:**

1. **Skills are in-code constants, never database rows.**
   `CLAUDE.md` §5.5 settles this: "making a database row the system prompt
   would turn every write into prompt injection with full pipeline reach."
   A skill's text reaches an LLM, so the same rule binds it. There is
   deliberately no API route, no admin surface and no settings field that
   can introduce or edit one.

2. **A skill is an accuracy aid, so it fails open.** If no skill matches, or
   the feature is off, analysis proceeds exactly as it did before. A skill
   is never a reason a question cannot be answered (`CLAUDE.md` §4.2).

3. **A skill grants nothing.** Authorization stays with `agent/authz.py` and
   `identity/rbac.py`; what may be returned stays with `governance/`. A
   skill that named a table, loosened a restriction or claimed a permission
   would be ignored by every one of those gates, because none of them reads
   a skill.

The supplied domain prompts are near-identical by design — the five Sales
prompts differ only in their title. So the shared contract lives **once**
in `domain_skills/contract.py`, and a skill carries only what actually
varies. Ninety skills therefore cost ninety short declarations, not ninety
copies of the same thirty lines.
"""

from __future__ import annotations

from dataclasses import dataclass

from governance.domains import BusinessDomain


class DomainSkillError(Exception):
    """Base class for every error this package raises.

    Carries `safe_message` for the same reason `agent.exceptions.AgentError`
    does: a response body must never leak internal detail (`CLAUDE.md` §5.6).
    """

    def __init__(self, message: str, *, safe_message: str | None = None) -> None:
        super().__init__(message)
        self.safe_message = safe_message or "That analysis skill is not available."


class UnknownSkillError(DomainSkillError):
    """A skill slug that is not registered.

    Raised rather than returning `None` so an unknown slug denies instead of
    degrading to a default (`CLAUDE.md` §5.3's "unknown means deny").
    """


@dataclass(frozen=True)
class DomainSkill:
    """One analytical focus inside one business domain.

    Attributes:
        slug: Stable identifier, e.g. `"sales.revenue"`. Used in logs and
            as the `preferred_slug` a caller of `composer.guidance_for`
            may pass. **There is no `GET /domain-skills` route** -- this
            docstring claimed one until batch 16 checked; the package has
            no live caller at all (`CLAUDE.md` §1, "built, not wired").
            Lower-case, dot-separated; uniqueness is enforced at import
            by the registry.
        title: Human-readable name, from the supplied prompt's own heading.
        domain: The `governance.domains.BusinessDomain` this belongs to.
            Reused rather than introduced: that enum already drives the
            privacy policy, the restricted-category union and the prohibited
            -action list, so a skill cannot sit in a domain the governance
            engine does not know about.
        scope: The domain's analytical scope — the supplied prompt's "Domain
            scope:" sentence. Shared by every skill in a domain, so it is
            written once per domain in `catalog.py`.
        focus: What *this* skill attends to, and the only thing that varies
            between the five Sales prompts. One or two sentences.
        dimensions: The specific cuts this skill should consider (e.g.
            `("customer", "region", "product")`). Guidance for analysis, not
            a column list — these are business concepts, and the semantic
            layer is what maps them to physical fields, if it can.
    """

    slug: str
    title: str
    domain: BusinessDomain
    scope: str
    focus: str
    dimensions: tuple[str, ...] = ()

    @property
    def group(self) -> str:
        """The *supplied prompt-domain*, e.g. `"sales"` — the slug prefix.

        Distinct from `domain`, and batch 3 is why. A governance domain can
        hold several prompt-domains: Sales and Marketing arrived as separate
        batches with genuinely different analytical scopes ("commercial
        revenue, customers, regions…" versus "campaigns, channels, offers…"),
        yet both map to `BusinessDomain.SALES_MARKETING`, because that enum
        exists to answer "what may this question return", not "how should
        this analysis be framed".

        Conflating the two was a real modelling error in batches 1–2. It did
        not show until a second prompt-domain landed in an occupied
        governance domain, at which point two things broke: the
        one-scope-per-domain invariant, and the default-skill choice, which
        would have framed a campaign question as Revenue Intelligence.

        Derived from the slug rather than stored, so the two can never
        disagree.
        """
        return self.slug.split(".", 1)[0]

    def __post_init__(self) -> None:
        # Validated at import rather than at use: a malformed skill is a
        # programming error that should fail the process, not produce a
        # subtly degraded prompt on some later request.
        if not self.slug or self.slug != self.slug.strip().lower():
            raise ValueError(f"skill slug must be lower-case and stripped: {self.slug!r}")
        if "." not in self.slug:
            raise ValueError(f"skill slug must be '<domain>.<name>': {self.slug!r}")
        for field_name in ("title", "scope", "focus"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"skill {self.slug!r} has an empty {field_name}")
