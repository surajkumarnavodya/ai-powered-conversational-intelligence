"""Tests for `domain_skills/` -- Batch 1 (Sales).

The classes that matter are the last four. A skill's text reaches an LLM
system message, so the properties worth pinning are: nothing untrusted can
get into it, it cannot grant anything, it fails open, and it does not
disturb the benchmark-pinned prompts.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import domain_skills as ds
import pytest
from domain_skills.composer import compose_guidance, guidance_for, select_skill
from domain_skills.contract import ANALYTICS_CONTRACT, RESPONSE_SECTIONS
from domain_skills.types import DomainSkill, UnknownSkillError

from governance.domains import BusinessDomain, ProhibitedAction, policy_for


def _an_uncovered_domain() -> BusinessDomain:
    """A governance domain with no declared skills, or skip.

    Resolved at call time rather than hard-coded. HR was used for this
    until batch 4 registered it, breaking four tests at once; picking the
    next uncovered domain by hand would just defer the same breakage to the
    batch after next. Once all eighteen are covered there is no such domain
    and the tests skip with that as the stated reason, which is accurate
    rather than a silently weakened assertion.
    """
    covered = set(ds.covered_domains())
    for domain in BusinessDomain:
        if domain is not BusinessDomain.GENERAL and domain not in covered:
            return domain
    pytest.skip("every governance domain now has skills; nothing is uncovered")


SALES = BusinessDomain.SALES_MARKETING
MIS = BusinessDomain.MIS
HR = BusinessDomain.HR
ECOMMERCE = BusinessDomain.ECOMMERCE
TRAINING = BusinessDomain.TRAINING
FINANCE = BusinessDomain.FINANCE
BFSI = BusinessDomain.BFSI
SUPPLY_CHAIN = BusinessDomain.SUPPLY_CHAIN
MANUFACTURING = BusinessDomain.MANUFACTURING
CUSTOMER_SERVICE = BusinessDomain.CUSTOMER_SERVICE
PROJECT = BusinessDomain.PROJECT
IT_OPERATIONS = BusinessDomain.IT_OPERATIONS
HEALTHCARE = BusinessDomain.HEALTHCARE
ENERGY = BusinessDomain.ENERGY
RISK_AUDIT = BusinessDomain.RISK_AUDIT
LEGAL = BusinessDomain.LEGAL
REAL_ESTATE = BusinessDomain.REAL_ESTATE

#: Covered domains whose policy declares no `ProhibitedAction` at all, so
#: `composer._prohibitions_block` appends nothing and the shared contract's
#: ranking invitation is never narrowed for them. Derived, not hard-coded,
#: so a future batch landing in such a domain is covered automatically;
#: `test_the_zero_prohibition_set_is_exactly_the_known_domains` stops the
#: derivation silently emptying.
ZERO_PROHIBITION_DOMAINS = tuple(
    d for d in ds.covered_domains() if not policy_for(d).prohibited_actions
)


class TestCatalog:
    def test_the_supplied_sales_skills_are_registered(self):
        """By group, not by domain: `skills_for_domain(SALES_MARKETING)`
        now returns Marketing's five as well."""
        assert tuple(s.slug for s in ds.all_skills() if s.group == "sales") == (
            "sales.revenue",
            "sales.customer",
            "sales.regional",
            "sales.product",
            "sales.pricing",
        )

    def test_the_supplied_real_estate_skills_are_registered_in_numeric_order(self):
        assert tuple(s.slug for s in ds.skills_for_domain(REAL_ESTATE)) == (
            "real_estate.portfolio",
            "real_estate.occupancy",
            "real_estate.lease",
            "real_estate.facilities_maintenance",
            "real_estate.financial",
        )

    def test_the_supplied_legal_skills_are_registered_in_numeric_order(self):
        """The prompts arrived shuffled (01, 03, 02, 04, 05); numeric order
        wins, so the domain default is Contract Intelligence."""
        assert tuple(s.slug for s in ds.skills_for_domain(LEGAL)) == (
            "legal.contract",  # 01
            "legal.matter",  # 02
            "legal.compliance",  # 03
            "legal.board_secretarial",  # 04
            "legal.governance_scorecard",  # 05
        )
        default = select_skill(LEGAL)
        assert default is not None
        assert default.slug == "legal.contract"

    def test_the_supplied_risk_audit_skills_are_registered_in_numeric_order(self):
        """Supplied 01..05 in order; registered in that order, so the
        domain default is Enterprise Risk Intelligence."""
        assert tuple(s.slug for s in ds.skills_for_domain(RISK_AUDIT)) == (
            "risk_audit.enterprise_risk",  # 01
            "risk_audit.internal_audit",  # 02
            "risk_audit.control_effectiveness",  # 03
            "risk_audit.operational_risk",  # 04
            "risk_audit.business_continuity",  # 05
        )
        default = select_skill(RISK_AUDIT)
        assert default is not None
        assert default.slug == "risk_audit.enterprise_risk"

    def test_the_supplied_energy_skills_are_registered_in_numeric_order(self):
        """Supplied 01..05 in order; registered in that order, so the
        domain default is Energy Consumption Intelligence."""
        assert tuple(s.slug for s in ds.skills_for_domain(ENERGY)) == (
            "energy.consumption",  # 01
            "energy.utility_performance",  # 02
            "energy.sustainability",  # 03
            "energy.emissions",  # 04
            "energy.efficiency",  # 05
        )
        default = select_skill(ENERGY)
        assert default is not None
        assert default.slug == "energy.consumption"

    def test_the_supplied_healthcare_skills_are_registered_in_numeric_order(self):
        """Supplied 01..05 in order; registered in that order, so the
        domain default is Healthcare Operations Intelligence."""
        assert tuple(s.slug for s in ds.skills_for_domain(HEALTHCARE)) == (
            "healthcare.operations",  # 01
            "healthcare.patient_journey",  # 02
            "healthcare.resource",  # 03
            "healthcare.financial",  # 04
            "healthcare.quality_compliance",  # 05
        )
        default = select_skill(HEALTHCARE)
        assert default is not None
        assert default.slug == "healthcare.operations"

    def test_the_supplied_it_ops_skills_are_registered_in_numeric_order(self):
        """Supplied 01..05 in order; registered in that order, so the
        domain default is IT Service Intelligence, the broadest."""
        assert tuple(s.slug for s in ds.skills_for_domain(IT_OPERATIONS)) == (
            "it_ops.service",  # 01
            "it_ops.incident",  # 02
            "it_ops.change",  # 03
            "it_ops.application_performance",  # 04
            "it_ops.cost_cloud",  # 05
        )
        default = select_skill(IT_OPERATIONS)
        assert default is not None
        assert default.slug == "it_ops.service"

    def test_the_supplied_project_skills_are_registered_in_numeric_order(self):
        """Supplied 01..05 in order; registered in that order, so the
        domain default is Portfolio Intelligence, the broadest."""
        assert tuple(s.slug for s in ds.skills_for_domain(PROJECT)) == (
            "project.portfolio",  # 01
            "project.schedule",  # 02
            "project.financial",  # 03
            "project.resource_capacity",  # 04
            "project.risk_dependency",  # 05
        )
        default = select_skill(PROJECT)
        assert default is not None
        assert default.slug == "project.portfolio"

    def test_the_supplied_customer_service_skills_are_registered_in_numeric_order(self):
        """Supplied 01..05 in order; registered in that order, so the
        domain default is Customer Support Intelligence."""
        assert tuple(s.slug for s in ds.skills_for_domain(CUSTOMER_SERVICE)) == (
            "customer_service.support",  # 01
            "customer_service.sla",  # 02
            "customer_service.complaint",  # 03
            "customer_service.contact_center",  # 04
            "customer_service.service_retention",  # 05
        )
        default = select_skill(CUSTOMER_SERVICE)
        assert default is not None
        assert default.slug == "customer_service.support"

    def test_the_supplied_manufacturing_skills_are_registered_in_numeric_order(self):
        """The prompts arrived 01, 03, 04, 05, 02 and are registered 01..05.

        Declaration order decides the domain default, so it is Production
        Intelligence (01) rather than whichever file happened to be listed
        first -- the same reason the HR and eCommerce batches were
        re-sequenced.
        """
        assert tuple(s.slug for s in ds.skills_for_domain(MANUFACTURING)) == (
            "manufacturing.production",  # 01
            "manufacturing.oee",  # 02
            "manufacturing.downtime",  # 03
            "manufacturing.quality",  # 04
            "manufacturing.capacity",  # 05
        )
        default = select_skill(MANUFACTURING)
        assert default is not None
        assert default.slug == "manufacturing.production"

    def test_the_supplied_supply_chain_skills_are_registered_in_numeric_order(self):
        """Supplied 01..05 in order; registered in that order, so the
        domain default is Supply Chain Performance Intelligence, the
        broadest of the five."""
        assert tuple(s.slug for s in ds.skills_for_domain(SUPPLY_CHAIN)) == (
            "supply_chain.performance",  # 01
            "supply_chain.inventory",  # 02
            "supply_chain.logistics",  # 03
            "supply_chain.supplier_spend",  # 04
            "supply_chain.procurement_cycle",  # 05
        )
        default = select_skill(SUPPLY_CHAIN)
        assert default is not None
        assert default.slug == "supply_chain.performance"

    def test_the_supplied_bfsi_skills_are_registered_in_numeric_order(self):
        """Supplied 01..05 in order; registered in that order, so the domain
        default is Banking Customer Intelligence."""
        assert tuple(s.slug for s in ds.skills_for_domain(BFSI)) == (
            "bfsi.banking_customer",  # 01
            "bfsi.loan_credit",  # 02
            "bfsi.insurance",  # 03
            "bfsi.fraud_risk",  # 04
            "bfsi.collections",  # 05
        )
        default = select_skill(BFSI)
        assert default is not None
        assert default.slug == "bfsi.banking_customer"

    def test_the_supplied_finance_skills_are_registered_in_numeric_order(self):
        """Supplied as 01, 02, 03, 05, 04; registered 01..05, so the domain
        default is Financial Performance Intelligence."""
        assert tuple(s.slug for s in ds.skills_for_domain(FINANCE)) == (
            "finance.performance",  # 01
            "finance.budget_cost",  # 02
            "finance.ar_ap",  # 03
            "finance.risk_cost",  # 04
            "finance.corporate_kpi",  # 05
        )
        default = select_skill(FINANCE)
        assert default is not None
        assert default.slug == "finance.performance"

    def test_the_supplied_training_skills_are_registered_in_numeric_order(self):
        """Supplied as 02, 03, 04, 05, 01; registered 01..05, so the domain
        default is Training Completion Intelligence."""
        assert tuple(s.slug for s in ds.skills_for_domain(TRAINING)) == (
            "training.completion",  # 01
            "training.skill_gap",  # 02
            "training.impact",  # 03
            "training.engagement",  # 04
            "training.certification",  # 05
        )
        default = select_skill(TRAINING)
        assert default is not None
        assert default.slug == "training.completion"

    def test_the_supplied_ecommerce_skills_are_registered_in_numeric_order(self):
        """Supplied as 03, 04, 02, 01, 05; registered 01..05, so the domain
        default is Conversion Funnel Intelligence rather than whichever
        file happened to be listed first."""
        assert tuple(s.slug for s in ds.skills_for_domain(ECOMMERCE)) == (
            "ecommerce.conversion_funnel",  # 01
            "ecommerce.cart_abandonment",  # 02
            "ecommerce.customer_cohort",  # 03
            "ecommerce.category",  # 04
            "ecommerce.promotion_channel",  # 05
        )
        default = select_skill(ECOMMERCE)
        assert default is not None
        assert default.slug == "ecommerce.conversion_funnel"

    def test_the_supplied_hr_skills_are_registered_in_numeric_order(self):
        """Declaration order is the supplied 01..05 order, which also
        decides the domain default -- so the default is Workforce
        Intelligence (01), the broadest of the five, rather than whichever
        prompt happened to arrive first."""
        assert tuple(s.slug for s in ds.skills_for_domain(HR)) == (
            "hr.workforce",  # 01
            "hr.attrition",  # 02
            "hr.recruitment",  # 03
            "hr.performance",  # 04
            "hr.risk_scorecard",  # 05
        )
        default = select_skill(HR)
        assert default is not None and default.slug == "hr.workforce"

    def test_the_supplied_marketing_skills_are_registered(self):
        """Marketing shares a governance domain with Sales, so it is looked
        up by group rather than by domain."""
        assert tuple(s.slug for s in ds.all_skills() if s.group == "marketing") == (
            "marketing.campaign",
            "marketing.channel",
            "marketing.offer",
            "marketing.segmentation",
            "marketing.lead_funnel",
        )

    def test_a_prompt_domain_does_not_imply_a_governance_domain(self):
        """Eighteen prompt-domains, seventeen governance domains:
        Marketing shares `SALES_MARKETING` with Sales. Inventing a
        governance domain per batch would mean two answers to 'what may
        this question return'.

        The gap of one has been constant since batch 3 and is the whole
        point of `DomainSkill.group`. Batch 18 is where the counts stop
        moving: ninety skills across eighteen prompt-domains, which is
        the figure `domain_skills/types.py` has used as its example since
        batch 1.
        """
        assert len({s.group for s in ds.all_skills()}) == 18
        assert len(ds.covered_domains()) == 17
        assert len(ds.all_skills()) == 90

    def test_the_supplied_mis_skills_are_registered(self):
        assert tuple(s.slug for s in ds.skills_for_domain(MIS)) == (
            "mis.executive_kpi",
            "mis.target_vs_actual",
            "mis.period_comparison",
            "mis.anomaly",
            "mis.scorecard",
        )

    def test_every_skill_belongs_to_a_real_governance_domain(self):
        """Reusing `BusinessDomain` rather than inventing a second domain
        vocabulary is what stops a skill sitting in a domain the governance
        engine has no policy for."""
        for skill in ds.all_skills():
            assert isinstance(skill.domain, BusinessDomain)

    def test_slugs_are_unique(self):
        assert len(set(ds.all_slugs())) == len(ds.all_slugs())

    @pytest.mark.parametrize("group", sorted({s.group for s in ds.all_skills()}), ids=lambda g: g)
    def test_each_prompt_domain_stores_its_scope_once(self, group):
        """One scope per PROMPT-domain, not per governance domain.

        This asserted per-governance-domain until batch 3. Marketing shares
        `SALES_MARKETING` with Sales but has a genuinely different
        analytical scope, so the old assertion was wrong about the model,
        not about the data. Keeping it would have forced either a fake
        shared scope or a deleted test; the invariant it actually protects
        -- a scope is written once and not forked per skill -- survives
        intact at the right grain.
        """
        scopes = {s.scope for s in ds.all_skills() if s.group == group}
        assert len(scopes) == 1

    def test_coverage_is_exactly_the_delivered_batches(self):
        """Coverage is read from the catalog, so no document can claim a
        domain the code does not actually have.

        **This assertion is meant to be updated by each batch.** Failing
        here when a domain lands is the point: it forces the coverage claim
        in `docs/architecture/domain-skills.md` to be updated in the same
        change, instead of drifting behind the code.
        """
        assert ds.covered_domains() == (
            SALES,
            MIS,
            HR,
            ECOMMERCE,
            TRAINING,
            FINANCE,
            BFSI,
            SUPPLY_CHAIN,
            MANUFACTURING,
            CUSTOMER_SERVICE,
            PROJECT,
            IT_OPERATIONS,
            HEALTHCARE,
            ENERGY,
            RISK_AUDIT,
            LEGAL,
            REAL_ESTATE,
        )

    def test_the_only_domain_left_uncovered_is_the_deliberate_one(self):
        """Batch 18 is the last of the supplied batches, so this states
        what remains rather than leaving it to be inferred.

        `EXECUTIVE` has no skills and is not pending: its one supplied
        prompt, "Executive KPI Intelligence", is registered as
        `mis.executive_kpi`, because that is the domain the batch was
        supplied under and because the choice is governance-neutral --
        the two policies are compared below rather than claimed equal.
        Asserted here so "seventeen of eighteen" cannot be read as a gap
        somebody forgot.
        """
        covered = set(ds.covered_domains())
        uncovered = {
            d for d in BusinessDomain if d is not BusinessDomain.GENERAL and d not in covered
        }
        assert uncovered == {BusinessDomain.EXECUTIVE}

        # Governance-neutral, measured rather than asserted in a comment:
        # whichever of the two the prompt had been filed under, the
        # composed block and every row-level control would be the same.
        mis_policy, executive_policy = policy_for(MIS), policy_for(BusinessDomain.EXECUTIVE)
        assert (
            mis_policy.effective_restricted_categories
            == executive_policy.effective_restricted_categories
        )
        assert mis_policy.prohibited_actions == executive_policy.prohibited_actions
        assert mis_policy.minimum_group_size == executive_policy.minimum_group_size

        # And the prompt really is registered, not merely accounted for.
        assert ds.get_skill("mis.executive_kpi").title == "Executive KPI Intelligence"

    def test_every_uncovered_domain_has_no_skills(self):
        """An uncovered domain must stay genuinely empty rather than
        quietly inheriting another domain's framing.

        Named without a count on purpose: this was
        `test_the_other_sixteen_domains_have_no_skills`, correct at batch 2
        and stale from batch 3 onward, because the body derives the
        uncovered set from the catalog while the name hard-coded it. The
        assertion was never wrong -- only its name was.
        """
        covered = set(ds.covered_domains())
        for domain in BusinessDomain:
            if domain not in covered:
                assert ds.skills_for_domain(domain) == (), domain.value

    @pytest.mark.parametrize("skill", ds.all_skills(), ids=lambda s: s.slug)
    def test_each_skill_is_frozen(self, skill):
        with pytest.raises(FrozenInstanceError):
            skill.slug = "mutated"  # type: ignore[misc]

    @pytest.mark.parametrize("skill", ds.all_skills(), ids=lambda s: s.slug)
    def test_group_always_matches_the_slug_prefix(self, skill):
        """`group` is derived, not stored, so the two cannot disagree. This
        pins that derivation."""
        assert skill.group == skill.slug.split(".", 1)[0]
        assert skill.group

    @pytest.mark.parametrize("skill", ds.all_skills(), ids=lambda s: s.slug)
    def test_each_skill_has_a_distinct_focus(self, skill):
        others = [s.focus for s in ds.all_skills() if s.slug != skill.slug]
        assert skill.focus not in others


class TestValidation:
    @pytest.mark.parametrize(
        "bad",
        [
            {"slug": ""},
            {"slug": "Sales.Revenue"},  # not lower-case
            {"slug": " sales.revenue "},  # not stripped
            {"slug": "norepository"},  # no domain prefix
            {"title": "  "},
            {"scope": ""},
            {"focus": "   "},
        ],
    )
    def test_a_malformed_skill_fails_at_construction(self, bad):
        """Validated at import, not at use: a malformed skill is a
        programming error that should fail the process, not quietly produce
        a degraded prompt on some later request."""
        good = dict(
            slug="sales.test",
            title="Test",
            domain=SALES,
            scope="scope",
            focus="focus",
        )
        good.update(bad)
        with pytest.raises(ValueError):
            DomainSkill(**good)  # type: ignore[arg-type]


class TestLookup:
    def test_every_registered_slug_resolves(self):
        for slug in ds.all_slugs():
            assert ds.get_skill(slug).slug == slug

    def test_an_unknown_slug_raises_rather_than_defaulting(self):
        assert not ds.is_registered("nosuchdomain.nosuchskill"), "setup"
        with pytest.raises(UnknownSkillError):
            ds.get_skill("nosuchdomain.nosuchskill")

    def test_the_refusal_does_not_echo_the_requested_slug(self):
        """A refusal must not become an oracle for what exists."""
        with pytest.raises(UnknownSkillError) as exc:
            ds.get_skill("../../etc/passwd")
        assert "etc/passwd" not in exc.value.safe_message

    def test_is_registered_never_raises(self):
        assert ds.is_registered("sales.revenue") is True
        assert ds.is_registered("nope") is False

    def test_a_domain_with_no_skills_returns_empty_not_an_error(self):
        assert ds.skills_for_domain(_an_uncovered_domain()) == ()


class TestFailsOpen:
    """A skill is an accuracy aid, so every miss degrades to the platform's
    existing behaviour rather than refusing (`CLAUDE.md` 4.2)."""

    def test_disabled_changes_nothing(self):
        """Asserted on MIS, not `SALES_MARKETING`.

        This used SALES and went VACUOUS when batch 3 made that domain
        ambiguous: SALES returns None whether the flag is on or off, so
        deleting the `if not enabled` guard changed nothing and the
        mutation survived. A test that asserts "returns None" proves
        nothing unless the same call returns something when the condition
        under test is reversed -- so the positive case is asserted here
        too.
        """
        assert guidance_for(MIS, enabled=True) is not None, "setup: must be non-None when on"
        assert guidance_for(MIS, enabled=False) is None

    def test_an_uncovered_domain_changes_nothing(self):
        assert guidance_for(_an_uncovered_domain(), enabled=True) is None

    def test_general_changes_nothing(self, monkeypatch):
        """`GENERAL` means "domain unknown". The right response to an
        unknown domain is the existing neutral framing, not a guess.

        A GENERAL skill is injected so the guard is actually exercised.
        Without that this test passes for the wrong reason -- GENERAL has
        no declared skills, so `select_skill` would return None anyway and
        deleting the guard would change nothing. The guard is defensive for
        the day someone declares one; this makes that defence real now.
        """
        import domain_skills.registry as reg

        general = DomainSkill(
            slug="general.catchall",
            title="Catch-all",
            domain=BusinessDomain.GENERAL,
            scope="s",
            focus="f",
        )
        monkeypatch.setitem(reg._BY_SLUG, general.slug, general)
        monkeypatch.setattr(reg, "ALL_SKILLS", (*ds.all_skills(), general))
        assert reg.skills_for_domain(BusinessDomain.GENERAL) == (general,), "setup failed"

        assert guidance_for(BusinessDomain.GENERAL, enabled=True) is None

    def test_mis_produces_its_own_guidance(self):
        """Each covered domain must get its own scope text, not another
        domain's."""
        result = guidance_for(MIS, enabled=True)
        assert result is not None
        text, skill = result
        assert skill.domain is MIS
        assert "executive KPIs" in text
        assert "commercial revenue" not in text, "Sales scope leaked into MIS"

    def test_a_covered_unambiguous_domain_produces_guidance(self):
        """Uses MIS: `SALES_MARKETING` now holds two prompt-domains and
        deliberately produces nothing without a slug -- see
        `TestItRefusesToGuessAcrossPromptDomains`."""
        result = guidance_for(MIS, enabled=True)
        assert result is not None
        text, skill = result
        assert text
        assert skill.domain is MIS


class TestItRefusesToGuessAcrossPromptDomains:
    """`SALES_MARKETING` holds two prompt-domains with different scopes.

    With nothing to disambiguate, framing a campaign question as Revenue
    Intelligence would be actively wrong -- worse than not framing it. So
    selection returns None and the analysis keeps the platform's existing
    neutral framing.
    """

    def test_an_ambiguous_domain_with_no_slug_returns_nothing(self):
        from domain_skills.registry import groups_for_domain

        assert len(groups_for_domain(SALES)) > 1, "setup: domain must be ambiguous"
        assert select_skill(SALES) is None
        assert guidance_for(SALES, enabled=True) is None

    def test_an_unambiguous_domain_still_gets_its_default(self):
        """The refusal must be scoped to real ambiguity, not a blanket
        disabling of defaults."""
        from domain_skills.registry import groups_for_domain

        assert groups_for_domain(MIS) == ("mis",)
        chosen = select_skill(MIS)
        assert chosen is not None and chosen.group == "mis"

    @pytest.mark.parametrize(
        "slug,expected_group",
        [("sales.revenue", "sales"), ("marketing.campaign", "marketing")],
    )
    def test_an_explicit_slug_resolves_the_ambiguity(self, slug, expected_group):
        chosen = select_skill(SALES, preferred_slug=slug)
        assert chosen is not None
        assert chosen.slug == slug
        assert chosen.group == expected_group

    def test_marketing_guidance_carries_the_marketing_scope(self):
        """The two prompt-domains must not bleed into each other."""
        result = guidance_for(SALES, enabled=True, preferred_slug="marketing.offer")
        assert result is not None
        text, skill = result
        assert skill.group == "marketing"
        assert "campaigns, channels, offers" in text
        assert "commercial revenue" not in text, "Sales scope leaked into Marketing"

    def test_sales_guidance_still_carries_the_sales_scope(self):
        result = guidance_for(SALES, enabled=True, preferred_slug="sales.revenue")
        assert result is not None
        text, skill = result
        assert skill.group == "sales"
        assert "commercial revenue" in text
        assert "lead funnels" not in text, "Marketing scope leaked into Sales"


class TestSkillSelectionCannotCrossDomains:
    """The skill parameter must not become a way to pull in another
    domain's framing."""

    def test_a_real_skill_from_another_domain_is_ignored(self):
        """Exercised against the real catalog, no injection needed.

        Runs against MIS rather than `SALES_MARKETING`: the assertion is
        that an intruder slug falls back to *this* domain's default, and
        `SALES_MARKETING` deliberately has no default now that it holds two
        prompt-domains.
        """
        assert ds.is_registered("sales.revenue")
        assert ds.get_skill("sales.revenue").domain is SALES

        chosen = select_skill(MIS, preferred_slug="sales.revenue")
        assert chosen is not None
        assert chosen.domain is MIS, "another domain's skill leaked through"
        assert chosen.group == "mis"

    def test_a_slug_from_another_domain_is_ignored(self, monkeypatch):
        """The other-domain skill must be genuinely REGISTERED for this to
        test anything.

        An earlier version built an unregistered `DomainSkill` and passed
        its slug. That test passed against a deliberately broken
        `select_skill` that looked the slug up globally -- because the
        lookup missed too, so both the safe and the broken version fell
        through to the same default. It proved nothing. Only Sales has
        skills today, so the second domain has to be injected.
        """
        import domain_skills.registry as reg

        intruder = DomainSkill(
            slug="legal.contracts",
            title="Contracts",
            domain=BusinessDomain.LEGAL,
            scope="s",
            focus="f",
        )
        monkeypatch.setitem(reg._BY_SLUG, intruder.slug, intruder)
        monkeypatch.setattr(reg, "ALL_SKILLS", (*ds.all_skills(), intruder))

        assert reg.is_registered("legal.contracts") is True, "setup failed"
        assert reg.get_skill("legal.contracts").domain is BusinessDomain.LEGAL

        chosen = select_skill(MIS, preferred_slug="legal.contracts")
        assert chosen is not None
        assert chosen.domain is MIS, "another domain's skill leaked through"
        assert chosen.slug != "legal.contracts"

    def test_an_unknown_slug_falls_back_to_the_domain_default(self):
        """On an unambiguous domain. On an ambiguous one it returns None --
        see `TestItRefusesToGuessAcrossPromptDomains`."""
        chosen = select_skill(MIS, preferred_slug="does.not.exist")
        assert chosen is not None
        assert chosen.slug == "mis.executive_kpi"

    def test_an_unknown_slug_on_an_ambiguous_domain_yields_nothing(self):
        assert select_skill(SALES, preferred_slug="does.not.exist") is None

    def test_a_valid_same_domain_slug_is_honoured(self):
        chosen = select_skill(SALES, preferred_slug="sales.pricing")
        assert chosen is not None
        assert chosen.slug == "sales.pricing"

    def test_selection_never_invents_a_skill_for_an_uncovered_domain(self):
        assert select_skill(_an_uncovered_domain(), preferred_slug="sales.revenue") is None


class TestDomainProhibitionsAreDerivedNotRestated:
    """HR forbids `individual_performance_ranking` while the shared
    contract invites "rank top and bottom performers". The constraint is
    derived from `governance.domains.policy_for`, so it cannot drift from
    the policy the governance engine actually enforces."""

    def test_hr_guidance_carries_every_prohibition_from_its_policy(self):
        from governance.domains import policy_for

        result = guidance_for(HR, enabled=True)
        assert result is not None
        text, _ = result
        for action in policy_for(HR).prohibited_actions:
            phrase = action.value.replace("_", " ")
            assert phrase in text, f"{action.value} missing from HR guidance"

    def test_the_ranking_invitation_is_qualified_not_absolute(self):
        """Otherwise the contract would invite exactly the thing HR
        prohibits.

        Asserted on the ranking LINE, not on the contract as a whole. The
        loose version -- "is the phrase present anywhere?" -- passed a
        mutation that unqualified the bullet, because the phrase still
        appeared in the trailing fragment of the same sentence. Checking
        the line the invitation actually lives on is what makes this
        meaningful.
        """
        ranking_lines = [
            line
            for line in ANALYTICS_CONTRACT.splitlines()
            if line.lstrip().startswith("- Rank top and bottom performers")
        ]
        assert len(ranking_lines) == 1, "expected exactly one ranking bullet"
        bullet = ranking_lines[0]
        assert "not prohibited for this domain" in bullet, (
            "the ranking invitation is unqualified -- it would invite the "
            "individual ranking HR prohibits"
        )
        assert "override this line" in bullet

    def test_the_constraint_block_comes_last_so_it_overrides(self):
        result = guidance_for(HR, enabled=True)
        assert result is not None
        text, _ = result
        assert text.rstrip().endswith("instead of doing it.")
        assert text.index("Domain constraints") > text.index("Rank top and bottom")

    def test_the_zero_prohibition_set_is_exactly_the_known_domains(self):
        """Pins the derivation the test below parametrizes over.

        That list is derived from the policy so a future zero-prohibition
        batch is covered automatically. Derived alone, though, it could
        silently shrink to nothing and the parametrized test would vanish
        rather than fail -- so the membership is asserted here.

        Named without a count, deliberately: the previous name said
        "three" and batch 11 made it four. A count in a test name goes
        stale exactly as `test_the_other_sixteen_domains_have_no_skills`
        did, and for the same reason -- the body derives what the name
        hard-codes.
        """
        assert ZERO_PROHIBITION_DOMAINS == (
            MIS,
            SUPPLY_CHAIN,
            MANUFACTURING,
            CUSTOMER_SERVICE,
            PROJECT,
            ENERGY,
            REAL_ESTATE,
        )
        # Batch 13 deliberately does *not* join this list: IT_OPERATIONS
        # prohibits `credential_disclosure`, so it gets a derived block.
        # Asserted here rather than only in `TestITOperationsDerivesItsOne
        # Prohibition`, because this is the tuple that would silently grow.
        assert IT_OPERATIONS not in ZERO_PROHIBITION_DOMAINS

    @pytest.mark.parametrize("domain", ZERO_PROHIBITION_DOMAINS, ids=lambda d: d.value)
    def test_a_domain_with_no_prohibitions_appends_nothing(self, domain):
        """None of these declares a `ProhibitedAction`, so there is no
        empty block to read past.

        Parametrized at batch 9 and derived at batch 10, where
        MANUFACTURING became the third. The consequence is not cosmetic:
        with no block appended, the contract's "rank top and bottom
        performers" invitation is never overridden for any of them. MIS is
        safe by subject matter -- management reporting is inherently
        aggregate -- and so is ENERGY, whose subjects are meters, sites and
        scopes rather than people (`TestEnergyIsGovernedExactlyLike
        Manufacturing` asserts that absence is reasoned rather than
        overlooked). The others carry their own guards, in
        `TestSupplyChainCarriesItsOwnGuards`,
        `TestManufacturingCarriesItsOwnGuards`,
        `TestCustomerServiceCarriesItsOwnGuards`,
        `TestProjectCarriesItsOwnGuards` and
        `TestRealEstateIsWhereTheAssetIsTheIdentifier`. REAL_ESTATE is the
        one that most needs its own, because occupancy is derived from
        records about identifiable people and `minimum_group_size=5` does
        not reach an occupant count.
        """
        from governance.domains import policy_for

        assert policy_for(domain).prohibited_actions == frozenset()
        result = guidance_for(domain, enabled=True)
        assert result is not None
        text, _ = result
        assert "Domain constraints" not in text

    def test_bfsi_carries_all_four_of_its_prohibitions(self):
        """BFSI is the first domain whose own skills are *named after* the
        actions it prohibits -- "Loan and Credit Intelligence" against
        `credit_or_loan_decision`, "Fraud and Risk Intelligence" against
        `fraud_accusation`. The block must carry all four regardless of
        which skill is selected, so this checks every BFSI skill rather
        than only the default.
        """
        from governance.domains import policy_for

        actions = {a.value for a in policy_for(BFSI).prohibited_actions}
        assert actions == {
            "credit_or_loan_decision",
            "fraud_accusation",
            "individualized_financial_advice",
            "unsupported_allegation",
        }

        for skill in ds.skills_for_domain(BFSI):
            result = guidance_for(BFSI, enabled=True, preferred_slug=skill.slug)
            assert result is not None
            text, chosen = result
            assert chosen.slug == skill.slug
            for action in actions:
                assert action.replace("_", " ") in text, f"{action} missing for {skill.slug}"

    def test_the_bfsi_prohibitions_are_not_a_restatement_of_another_domains(self):
        """Derived per domain: BFSI must not inherit HR's employment
        actions or Finance's compensation one, both of which a shared or
        cached list would leak."""
        from governance.domains import policy_for

        bfsi_actions = {a.value for a in policy_for(BFSI).prohibited_actions}
        other = {a.value for a in policy_for(HR).prohibited_actions} | {
            a.value for a in policy_for(FINANCE).prohibited_actions
        }
        result = guidance_for(BFSI, enabled=True)
        assert result is not None
        text, _ = result
        for leaked in other - bfsi_actions:
            assert leaked.replace("_", " ") not in text, f"{leaked} leaked into BFSI"

    def test_the_three_bfsi_skills_named_after_a_prohibition_redirect_to_the_portfolio(
        self,
    ):
        """The derived block says what must not be done; the focus text has
        to say what to do instead, or the skill is only a refusal. Each of
        the three invites a prohibited action by name, so each is pinned to
        the aggregate question it legitimately answers.
        """
        lending = ds.get_skill("bfsi.loan_credit").focus.lower()
        assert "portfolio" in lending
        assert "never assesses whether any applicant or borrower" in lending

        fraud = ds.get_skill("bfsi.fraud_risk").focus.lower()
        assert "never describe anyone as having committed fraud" in fraud
        assert "reason to investigate" in fraud

        collections = ds.get_skill("bfsi.collections").focus.lower()
        assert "portfolio state" in collections
        assert "never a judgement about a borrower" in collections

    def test_finance_does_not_derive_a_prohibition_it_does_not_have(self):
        """Finance prohibits only `individual_compensation_decision`. It
        does NOT prohibit `unsupported_allegation`, so that must not appear
        in its derived block -- the AR/AP accusation risk is handled in
        that skill's own focus text instead.

        This is the line between the two mechanisms: a policy-backed
        prohibition is derived and never restated, while an analytical risk
        the policy does not cover belongs to the skill.
        """
        from governance.domains import policy_for

        actions = {a.value for a in policy_for(FINANCE).prohibited_actions}
        assert actions == {"individual_compensation_decision"}

        result = guidance_for(FINANCE, enabled=True)
        assert result is not None
        text, _ = result
        assert "individual compensation decision" in text
        assert "unsupported allegation" not in text

    def test_the_ar_ap_skill_carries_the_accusation_guard_itself(self):
        """Since the policy does not cover it, the skill must -- a late
        balance reported as evidence of bad faith is the realistic failure
        here."""
        skill = ds.get_skill("finance.ar_ap")
        lowered = skill.focus.lower()
        assert "collections" in lowered
        assert "never as evidence of bad faith" in lowered

    def test_training_carries_a_subset_of_hrs_prohibitions(self):
        """Training prohibits two of HR's five. A domain with a strict
        subset is the sharpest test of per-domain derivation: a shared or
        cached list would leak the other three."""
        from governance.domains import policy_for

        training_actions = {a.value for a in policy_for(TRAINING).prohibited_actions}
        hr_actions = {a.value for a in policy_for(HR).prohibited_actions}
        assert training_actions < hr_actions, "expected a strict subset"

        result = guidance_for(TRAINING, enabled=True)
        assert result is not None
        text, _ = result
        for action in training_actions:
            assert action.replace("_", " ") in text
        for leaked in hr_actions - training_actions:
            assert leaked.replace("_", " ") not in text, f"{leaked} leaked from HR"

    def test_ecommerce_carries_its_single_prohibition(self):
        """One prohibition, not HR's five and not Sales' two -- proving the
        block is derived per domain rather than a shared list."""
        from governance.domains import policy_for

        actions = policy_for(ECOMMERCE).prohibited_actions
        assert len(actions) == 1
        result = guidance_for(ECOMMERCE, enabled=True)
        assert result is not None
        text, _ = result
        assert "sensitive personal targeting" in text
        assert "individual performance ranking" not in text

    def test_sales_carries_its_own_two_prohibitions(self):
        """Derived per domain, not a single hard-coded list."""
        from governance.domains import policy_for

        result = guidance_for(SALES, enabled=True, preferred_slug="sales.revenue")
        assert result is not None
        text, _ = result
        sales_actions = policy_for(SALES).prohibited_actions
        assert len(sales_actions) == 2
        for action in sales_actions:
            assert action.value.replace("_", " ") in text
        # HR-only prohibitions must not appear.
        assert "individual performance ranking" not in text

    def test_no_skill_hand_writes_its_prohibitions(self):
        """Structural: if a focus text restated a prohibition verbatim it
        would be a second owner, free to drift from the policy.

        The assertions are on the **rendered action phrase** -- what
        `composer._prohibitions_block` puts in the prompt -- not on the
        subject matter. Batch 14 first wrote the broader
        `"protected characteristic" not in lowered` and it failed on
        `marketing.segmentation` (batch 3) and `hr.recruitment` (batch 4),
        both of which say "never infer a protected characteristic from a
        name, a source or an outcome". That is the *redirect* pattern
        batch 8 established, not a restatement: it names the mechanism and
        pairs it with what to analyse instead, and it does not reproduce
        the policy's own phrasing. Outlawing it would have deleted the
        guards this package asks for. The narrower rule is the real one.
        """
        for skill in ds.all_skills():
            lowered = skill.focus.lower()
            assert "individual compensation decision" not in lowered
            assert "unsupported allegation" not in lowered
            assert "credential disclosure" not in lowered
            assert "clinical decision" not in lowered
            assert "medical diagnosis or prescription" not in lowered
            assert "protected characteristic inference" not in lowered


class TestSupplyChainCarriesItsOwnGuards:
    """Batch 9 is the mirror image of batch 8.

    BFSI's problem was a policy that prohibits four actions while three of
    its skills are named after one of them. SUPPLY_CHAIN's problem is the
    opposite: the policy prohibits **nothing**, so
    `composer._prohibitions_block` appends no constraint block, and the
    shared contract's invitation to "rank top and bottom performers" is
    never overridden for this domain.

    MIS is the only other zero-prohibition domain and it is safe by
    subject matter -- management reporting is inherently aggregate. Supply
    chain is not: supplier and carrier measures name counterparties, and
    delivery records frequently identify a driver. So the two risks are
    carried by the skills' own focus texts, following `finance.ar_ap`.

    These tests exist because a derived block cannot cover this. They are
    the only thing standing between the contract's ranking invitation and
    a driver-level performance ranking in a domain whose policy forbids
    neither.
    """

    def test_the_domain_genuinely_appends_no_constraint_block(self):
        """The premise of this whole class. If SUPPLY_CHAIN ever gains a
        `ProhibitedAction`, the derived block starts covering some of this
        and these guards should be revisited rather than silently kept."""
        from governance.domains import policy_for

        assert policy_for(SUPPLY_CHAIN).prohibited_actions == frozenset()

        skills = ds.skills_for_domain(SUPPLY_CHAIN)
        # Without this the loop empties when the batch is declared under
        # another domain, and the test passes vacuously -- so mutation
        # 14 would survive the very assertion written to catch it.
        # Found at batch 12; see docs/architecture/domain-skills.md section 7.
        assert len(skills) == 5
        for skill in skills:
            result = guidance_for(SUPPLY_CHAIN, enabled=True, preferred_slug=skill.slug)
            assert result is not None
            text, chosen = result
            assert chosen.slug == skill.slug
            assert "Domain constraints" not in text, skill.slug

    def test_the_ranking_invitation_therefore_reaches_this_domain_unqualified(self):
        """Proves the risk is real rather than theoretical: the ranking
        bullet is present in supply-chain guidance and nothing after it
        narrows it. This is what makes the two guards below load-bearing.
        """
        result = guidance_for(SUPPLY_CHAIN, enabled=True)
        assert result is not None
        text, _ = result
        assert "Rank top and bottom performers" in text
        assert "Domain constraints" not in text

    def test_the_logistics_skill_guards_individual_level_performance_itself(self):
        """`individual_performance_ranking` is prohibited in HR, not here --
        yet delivery data is exactly where a named driver or courier shows
        up. The realistic failure is a "bottom 10 performers" ranking of
        people in a domain whose policy does not forbid it.
        """
        focus = ds.get_skill("supply_chain.logistics").focus.lower()
        assert "identify a driver or courier" in focus
        assert "never an individual's" in focus

    def test_the_supplier_skill_carries_the_accusation_guard_itself(self):
        """`unsupported_allegation` is prohibited in BFSI, not here. A
        supplier missing its dates is a measured fact; reported as bad
        faith it becomes an allegation about a counterparty. Same
        mechanism as `finance.ar_ap`, different domain.
        """
        focus = ds.get_skill("supply_chain.supplier_spend").focus.lower()
        assert "never evidence of bad faith or misconduct" in focus

    def test_the_guards_are_not_a_restatement_of_a_prohibition(self):
        """They must read as analytical guidance, not as a hand-written
        copy of a `ProhibitedAction` -- which would be a second owner for
        a fact `governance/domains.py` owns, free to drift from it.
        """
        for skill in ds.skills_for_domain(SUPPLY_CHAIN):
            lowered = skill.focus.lower()
            assert "individual performance ranking" not in lowered
            assert "unsupported allegation" not in lowered
            assert "prohibit" not in lowered

    def test_each_skill_states_the_measurement_trap_its_subject_carries(self):
        """Read off the subject matter, not from the policy. Each is a way
        a correct-looking SQL answer is still wrong: a snapshot summed as
        a flow, on-time measured against a re-promised date, spend mixed
        across document bases, a cycle time averaged over closed cases
        only.
        """
        inventory = ds.get_skill("supply_chain.inventory").focus.lower()
        assert "point-in-time snapshot, not" in inventory
        assert "never be summed across periods" in inventory

        logistics = ds.get_skill("supply_chain.logistics").focus.lower()
        assert "which promise date" in logistics

        spend = ds.get_skill("supply_chain.supplier_spend").focus.lower()
        assert "one currency basis" in spend
        assert "double-counts" in spend

        cycle = ds.get_skill("supply_chain.procurement_cycle").focus.lower()
        assert "biased low" in cycle
        assert "remain in flight" in cycle

        performance = ds.get_skill("supply_chain.performance").focus.lower()
        assert "cannot be attributed" in performance


class TestManufacturingCarriesItsOwnGuards:
    """Batch 10: the third zero-prohibition domain, and a second boundary.

    The guard half is batch 9's pattern reused rather than rediscovered.
    `policy_for(MANUFACTURING)` declares no `ProhibitedAction`, so no
    constraint block is appended and the ranking invitation is never
    narrowed -- while downtime and defects are routinely recorded against a
    shift and an operator. `individual_performance_ranking` is prohibited
    in HR, not here, so the focus texts carry it.

    What is new is where this batch stops. `semantic.metrics.MetricDefinition`
    owns a metric's `definition`, `formula`, `aggregation` and `grain` under
    a governance `status`. A focus text that wrote out the OEE formula would
    be a second owner of a governed fact (`CLAUDE.md` §4.8) -- ungoverned,
    unversioned, and wrong wherever a plant's approved definition treats
    planned downtime differently.
    """

    def test_the_domain_genuinely_appends_no_constraint_block(self):
        """The premise. If MANUFACTURING ever gains a `ProhibitedAction`
        the derived block starts covering some of this, and these guards
        should be revisited rather than silently kept."""
        assert policy_for(MANUFACTURING).prohibited_actions == frozenset()

        skills = ds.skills_for_domain(MANUFACTURING)
        # Without this the loop empties when the batch is declared under
        # another domain, and the test passes vacuously -- so mutation
        # 18 would survive the very assertion written to catch it.
        # Found at batch 12; see docs/architecture/domain-skills.md section 7.
        assert len(skills) == 5
        for skill in skills:
            result = guidance_for(MANUFACTURING, enabled=True, preferred_slug=skill.slug)
            assert result is not None
            text, chosen = result
            assert chosen.slug == skill.slug
            assert "Domain constraints" not in text, skill.slug
            assert "Rank top and bottom performers" in text, skill.slug

    def test_downtime_and_defects_are_attributed_to_equipment_not_to_a_person(self):
        """The live risk in this domain: a downtime or scrap record names a
        shift and an operator, and the unqualified ranking invitation would
        happily rank them."""
        downtime = ds.get_skill("manufacturing.downtime").focus.lower()
        assert "never to a named operator" in downtime

        quality = ds.get_skill("manufacturing.quality").focus.lower()
        assert "never to the person who ran it" in quality

    def test_the_oee_skill_defers_to_the_approved_definition(self):
        """It must point at the governed definition, not supply one."""
        focus = ds.get_skill("manufacturing.oee").focus.lower()
        assert "use the approved metric definition" in focus
        assert "governed choice" in focus

    def test_no_skill_writes_out_a_metric_formula(self):
        """Structural, across the whole catalog: a formula in a focus text
        would be a second owner for `MetricDefinition.formula`, free to
        contradict the approved one. The skill names the basis; the
        semantic layer owns the arithmetic.
        """
        for skill in ds.all_skills():
            lowered = skill.focus.lower()
            assert "availability x performance" not in lowered
            assert "availability * performance" not in lowered
            assert "×" not in lowered, f"{skill.slug} contains a multiplication sign"
            assert " = " not in lowered, f"{skill.slug} looks like it states a formula"
            assert "multiplied by" not in lowered

    def test_the_oee_skill_blocks_the_average_of_ratios_error(self):
        """The single most common arithmetic error in this domain, and one
        no deterministic control catches: averaging a ratio across lines or
        periods instead of recomposing it from component totals. A correct
        and validated SQL statement can still produce it.
        """
        focus = ds.get_skill("manufacturing.oee").focus.lower()
        assert "recomposed from component totals" in focus
        assert "arithmetically wrong" in focus

    def test_each_skill_states_the_measurement_trap_its_subject_carries(self):
        """Read off the subject matter, not from the policy."""
        production = ds.get_skill("manufacturing.production").focus.lower()
        assert "gross or good units" in production

        downtime = ds.get_skill("manufacturing.downtime").focus.lower()
        assert "by duration and by frequency separately" in downtime
        assert "more downtime than the window contains" in downtime

        quality = ds.get_skill("manufacturing.quality").focus.lower()
        assert "give the denominator" in quality
        assert "first-pass yield counts a reworked unit as a" in quality

        capacity = ds.get_skill("manufacturing.capacity").focus.lower()
        assert "theoretical, demonstrated or scheduled" in capacity
        assert "hides" in capacity


class TestCustomerServiceCarriesItsOwnGuards:
    """Batch 11: the fourth zero-prohibition domain, and the sharpest one.

    An entire supplied skill -- Contact Center Intelligence -- is *about*
    per-agent productivity measures, in a domain whose policy prohibits
    `individual_performance_ranking` not at all. Batch 9 met this with
    delivery drivers and batch 10 with machine operators; here it is the
    subject of the prompt rather than a side effect of it.

    This domain does engage the result gate, unlike the previous two:
    `minimum_group_size=5` and PII/SPI/customer restrictions. The tests
    below are deliberately scoped to what that does *not* cover, so the
    guards are not credited with work the deterministic controls do, nor
    the controls with work they do not.

    The new problem is provenance. A ticket or complaint is a third
    party's claim, so the `DATABASE_FACT` is that it was logged -- never
    that its content is true (`CLAUDE.md` §4.4).
    """

    def test_the_domain_genuinely_appends_no_constraint_block(self):
        """The premise. If CUSTOMER_SERVICE ever gains a `ProhibitedAction`
        the derived block starts covering some of this, and these guards
        should be revisited rather than silently kept."""
        assert policy_for(CUSTOMER_SERVICE).prohibited_actions == frozenset()

        skills = ds.skills_for_domain(CUSTOMER_SERVICE)
        # Without this the loop empties when the batch is declared under
        # another domain, and the test passes vacuously -- so mutation
        # 21 would survive the very assertion written to catch it.
        # Found at batch 12; see docs/architecture/domain-skills.md section 7.
        assert len(skills) == 5
        for skill in skills:
            result = guidance_for(CUSTOMER_SERVICE, enabled=True, preferred_slug=skill.slug)
            assert result is not None
            text, chosen = result
            assert chosen.slug == skill.slug
            assert "Domain constraints" not in text, skill.slug
            assert "Rank top and bottom performers" in text, skill.slug

    def test_small_group_suppression_does_not_cover_the_ranking_risk(self):
        """Why the focus guard is still needed in a domain that *does*
        engage the result gate.

        `governance.suppression` suppresses a sensitive measure for a group
        below `minimum_group_size` and keeps the label and count. Twenty
        agents who each handled hundreds of contacts are not small groups,
        so nothing suppresses a ranking of them, and an authorised caller
        sees the measure. The controls bound disclosure; whether a
        per-person ranking is a sound answer is framing.
        """
        policy = policy_for(CUSTOMER_SERVICE)
        assert policy.minimum_group_size == 5, "premise: this domain sets a threshold"
        assert policy.prohibited_actions == frozenset(), "premise: no action is prohibited"

        focus = ds.get_skill("customer_service.contact_center").focus.lower()
        assert "rather than individual agents" in focus

    def test_the_contact_center_skill_gives_a_reason_not_just_a_refusal(self):
        """Batch 8's lesson: a guard with no alternative is a refusal. This
        one has to say what to report instead, and why handle time is the
        wrong basis for judging a person.
        """
        focus = ds.get_skill("customer_service.contact_center").focus.lower()
        assert "queue, interval, channel" in focus
        assert "penalises whoever took the hard work" in focus

    def test_a_complaint_is_reported_as_logged_not_as_established(self):
        """The provenance guard. A complaint record is evidence a complaint
        was made; its content is an allegation, frequently about a named
        employee. Restating it as a finding would present a third party's
        claim as a `DATABASE_FACT`.
        """
        focus = ds.get_skill("customer_service.complaint").focus.lower()
        assert "evidence that a complaint was made, not that its content is correct" in focus
        assert "never restate what a complaint alleges as an established finding" in focus
        assert "only where the data records one" in focus

    def test_the_guards_are_not_a_restatement_of_a_prohibition(self):
        """They must read as analytical guidance rather than a hand-written
        copy of a `ProhibitedAction`, which would be a second owner for a
        fact `governance/domains.py` owns.
        """
        for skill in ds.skills_for_domain(CUSTOMER_SERVICE):
            lowered = skill.focus.lower()
            assert "individual performance ranking" not in lowered
            assert "unsupported allegation" not in lowered
            assert "prohibit" not in lowered

    def test_each_skill_states_the_measurement_trap_its_subject_carries(self):
        """Read off the subject matter, not from the policy."""
        support = ds.get_skill("customer_service.support").focus.lower()
        assert "count a ticket once" in support
        assert "reopens, merges and splits" in support

        sla = ds.get_skill("customer_service.sla").focus.lower()
        assert "calendar or business hours" in sla
        assert "target in force when the ticket was raised" in sla

        retention = ds.get_skill("customer_service.service_retention").focus.lower()
        assert "correlation whose direction this data cannot establish" in retention
        assert "which is not the same as support contact causing them to leave" in retention


class TestProjectCarriesItsOwnGuards:
    """Batch 12: the fifth zero-prohibition domain, and self-reported data.

    `policy_for(PROJECT)` declares no `ProhibitedAction` and
    `minimum_group_size=0`, so neither the derived block nor small-group
    suppression applies. Resource allocation is per-person by
    construction, so the guard batch 9 introduced applies for the fifth
    time -- reused, not restated.

    What distinguishes this batch is the *kind* of number. Batch 11 dealt
    with a record of someone else's claim; here the central measures are
    self-assessments by the party being measured. Percent-complete and
    estimate-to-complete are entered by whoever owns the task, and a risk
    score is a forward-looking judgement entered by whoever owns the risk.
    Neither is an observation, so neither is a `DATABASE_FACT` about the
    world -- the fact is that a figure was reported (`CLAUDE.md` §4.4).
    """

    def test_the_domain_genuinely_appends_no_constraint_block(self):
        """The premise. If PROJECT ever gains a `ProhibitedAction` the
        derived block starts covering some of this, and these guards
        should be revisited rather than silently kept."""
        assert policy_for(PROJECT).prohibited_actions == frozenset()
        assert policy_for(PROJECT).minimum_group_size == 0

        skills = ds.skills_for_domain(PROJECT)
        # Without this the loop empties when the batch is declared under
        # another domain, and the test passes vacuously -- mutation 25 is
        # exactly that, and survived this assertion until batch 12 checked
        # its failure list rather than its total.
        assert len(skills) == 5
        for skill in skills:
            result = guidance_for(PROJECT, enabled=True, preferred_slug=skill.slug)
            assert result is not None
            text, chosen = result
            assert chosen.slug == skill.slug
            assert "Domain constraints" not in text, skill.slug
            assert "Rank top and bottom performers" in text, skill.slug

    def test_reported_progress_is_not_presented_as_measured_delivery(self):
        """Percent-complete is the most consequential number in the domain
        and the least observed: the task owner enters it. Reporting it as
        delivery presents a self-assessment as a `DATABASE_FACT`.
        """
        focus = ds.get_skill("project.portfolio").focus.lower()
        assert "self-assessment by whoever owns the task" in focus
        assert "reported progress rather than as measured delivery" in focus

    def test_a_risk_score_is_reported_as_a_judgement_not_an_outcome(self):
        """A risk register is forward-looking. A "high" risk is somebody's
        assessment, not an observation about what will happen, and an
        issue that already materialised is a different thing entirely.
        """
        focus = ds.get_skill("project.risk_dependency").focus.lower()
        assert "forward-looking judgement someone entered, not an observed" in focus
        assert "when it was last reviewed rather than what will happen" in focus
        assert "issue that has already materialised separate" in focus

    def test_the_resource_skill_guards_individual_utilisation(self):
        """Allocation data is per-person, and `minimum_group_size=0` means
        nothing suppresses a one-person group here."""
        focus = ds.get_skill("project.resource_capacity").focus.lower()
        assert "report role, skill and team" in focus
        assert "never an individual's utilisation as a measure of how productive" in focus

    def test_the_financial_skill_defers_to_the_approved_earned_value_definition(self):
        """Batch 10's rule, reused: `semantic.metrics.MetricDefinition`
        owns a metric's formula, so the skill points at it rather than
        composing SPI or CPI here. The catalog-wide formula assertion in
        `TestManufacturingCarriesItsOwnGuards` already covers the
        structural half for every skill, including these.
        """
        focus = ds.get_skill("project.financial").focus.lower()
        assert "use the approved earned-value definitions" in focus
        assert "rather than composing them here" in focus

    def test_the_guards_are_not_a_restatement_of_a_prohibition(self):
        for skill in ds.skills_for_domain(PROJECT):
            lowered = skill.focus.lower()
            assert "individual performance ranking" not in lowered
            assert "unsupported allegation" not in lowered
            assert "prohibit" not in lowered

    def test_each_skill_states_the_measurement_trap_its_subject_carries(self):
        """Read off the subject matter. The schedule and allocation traps
        are structural rather than arithmetic, which is new: float belongs
        to a path in a dependency graph, and allocation is a many-to-many.
        """
        portfolio = ds.get_skill("project.portfolio").focus.lower()
        assert "not the average of its projects'" in portfolio
        assert "one large project outweighs many small ones" in portfolio

        schedule = ds.get_skill("project.schedule").focus.lower()
        assert "re-baselining resets variance to zero" in schedule
        assert "float belongs to a path" in schedule
        assert "slack must not be summed" in schedule

        financial = ds.get_skill("project.financial").focus.lower()
        assert "without commitments understates exposure" in financial

        resource = ds.get_skill("project.resource_capacity").focus.lower()
        assert "many-to-many" in resource
        assert "counts the same person once per project" in resource


class TestITOperationsDerivesItsOneProhibition:
    """Batch 13 is the inverse of batches 9-12.

    Those domains prohibited nothing, so the focus texts had to carry the
    risk alone. `policy_for(IT_OPERATIONS)` prohibits exactly one action --
    `credential_disclosure` -- and it is the one backed by an *absolute*
    control: `governance.classification.NEVER_DISCLOSED_CATEGORIES` holds
    `CREDENTIAL` back from every caller including an admin, with no role
    that unlocks it (`CLAUDE.md` 5.4). So the block is derived and no focus
    text restates it, exactly as BFSI's four are.

    What the skills carry instead is the provenance of a machine-generated
    measure. Batch 11 analysed a third party's claim and batch 12 a
    self-assessment by the measured party; here the number is an instrument
    reading whose instrument was configured by the team being measured.
    """

    def test_the_single_prohibition_is_derived_into_every_skills_block(self):
        """The premise. One prohibition, derived -- not five, and not
        none."""
        actions = {a.value for a in policy_for(IT_OPERATIONS).prohibited_actions}
        assert actions == {"credential_disclosure"}

        skills = ds.skills_for_domain(IT_OPERATIONS)
        # Without this the loop empties when the batch is declared under
        # another domain and the test passes vacuously -- the batch-12
        # lesson, applied on arrival rather than after a mutation.
        assert len(skills) == 5
        for skill in skills:
            result = guidance_for(IT_OPERATIONS, enabled=True, preferred_slug=skill.slug)
            assert result is not None
            text, chosen = result
            assert chosen.slug == skill.slug
            assert "This domain prohibits: credential disclosure." in text, skill.slug

    def test_it_is_the_narrowest_non_empty_block_in_the_catalog(self):
        """Proves the block is derived per domain rather than being one
        constant: HR renders five prohibitions, BFSI four and this domain
        exactly one, from the same code path.
        """
        counts = {
            domain: len(policy_for(domain).prohibited_actions)
            for domain in (HR, BFSI, IT_OPERATIONS)
        }
        assert counts[IT_OPERATIONS] == 1
        assert counts[IT_OPERATIONS] < counts[BFSI] < counts[HR]

        result = guidance_for(IT_OPERATIONS, enabled=True)
        assert result is not None
        text, _ = result
        # One action phrased, so no separator -- a hand-written block would
        # not track that.
        assert "; " not in text.split("This domain prohibits: ")[1].split(".")[0]

    def test_the_ranking_invitation_is_narrowed_here_unlike_batches_9_to_12(self):
        """The structural difference from the previous four batches, stated
        as an assertion rather than a comment.

        `_prohibitions_block` appends "Report in aggregate only" whenever a
        domain prohibits anything at all. IT_OPERATIONS does, so unlike
        SUPPLY_CHAIN, MANUFACTURING, CUSTOMER_SERVICE and PROJECT the
        contract's "rank top and bottom performers" invitation *is*
        overridden here. That is why the per-skill individual guard below
        is reinforcement rather than the only line.
        """
        result = guidance_for(IT_OPERATIONS, enabled=True)
        assert result is not None
        text, _ = result
        assert "Report in aggregate only." in text
        assert text.index("Domain constraints") > text.index("Rank top and bottom")

        for zero in ZERO_PROHIBITION_DOMAINS:
            other = guidance_for(zero, enabled=True)
            assert other is not None
            assert "Report in aggregate only." not in other[0], zero.value

    def test_no_focus_text_restates_the_credential_prohibition(self):
        """Derived, never restated -- the batch-4 rule. A second owner
        could drift from the policy the governance engine enforces, and
        here it would also imply the skill is the control, which it is
        not: `governance/result_policy.py` withholds the category whatever
        any prompt says.
        """
        for skill in ds.skills_for_domain(IT_OPERATIONS):
            lowered = skill.focus.lower()
            assert "credential" not in lowered, skill.slug
            assert "password" not in lowered, skill.slug
            assert "secret" not in lowered, skill.slug

    def test_a_machine_generated_count_carries_the_configuration_that_produced_it(self):
        """The new provenance step. An incident count is partly a property
        of the alerting threshold and the deduplication rule, so a fall in
        volume can be a configuration change rather than a quieter estate.
        """
        focus = ds.get_skill("it_ops.incident").focus.lower()
        assert "alerting threshold and the deduplication" in focus
        assert "configuration change rather than a quieter estate" in focus
        assert "say which rules were in force" in focus

    def test_availability_is_reported_as_a_definition_before_a_measurement(self):
        """Planned-maintenance treatment alone decides whether the same
        outage breaches, so the exclusion windows have to be named."""
        focus = ds.get_skill("it_ops.service").focus.lower()
        assert "availability is a definition" in focus
        assert "name the exclusion windows" in focus

    def test_the_change_skill_refuses_to_turn_a_sequence_into_a_cause(self):
        """Change-and-incident data is the most tempting place in this
        domain to cross the CORRELATION/cause line, and a time-window
        linkage manufactures the relationship it then measures.
        """
        focus = ds.get_skill("it_ops.change").focus.lower()
        assert "linkage rule stated" in focus
        assert "manufactures the relationship it then measures" in focus
        assert "is a sequence" in focus

    def test_the_application_skill_blocks_the_average_of_percentiles_error(self):
        """Batch 10's average-of-ratios error has a sibling here: a
        percentile is not a mean and cannot be averaged across windows or
        instances. A correct, validated SQL statement still produces it.
        """
        focus = ds.get_skill("it_ops.application_performance").focus.lower()
        assert "percentile cannot be" in focus
        assert "averaged across windows or instances" in focus
        assert "recomputed from" in focus

    def test_the_incident_skill_still_guards_the_individual_assignee(self):
        """Reinforcement, not the only line (see the narrowing test above).
        Assignee-level restoration time is the most common bad ask in this
        domain, and queue mix decides it.
        """
        focus = ds.get_skill("it_ops.incident").focus.lower()
        assert "never ranking the individual assignee" in focus
        assert "queue mix decides their numbers" in focus

    def test_the_cost_skill_does_not_invite_a_configuration_or_payload_body(self):
        """Framing, explicitly **not** a control.

        `governance.result_policy` classifies the *returned column names*,
        so it withholds a column named like a secret. A secret pasted into
        a free-text `message` or configuration-value column is not caught
        by that name inference, and `security/injection_patterns.py` is
        detection rather than a boundary (`CLAUDE.md` 5.5). Asking for
        counts, classes and cost rather than payload bodies does not close
        that gap -- it just stops the model being invited into it.
        """
        focus = ds.get_skill("it_ops.cost_cloud").focus.lower()
        assert "report resource counts, classes and cost" in focus
        assert "not the contents of resource configuration or log" in focus

    def test_each_skill_states_the_measurement_trap_its_subject_carries(self):
        """Read off the subject matter, not from the policy."""
        service = ds.get_skill("it_ops.service").focus.lower()
        assert "weakest link it" in service
        assert "not the average of its components'" in service

        change = ds.get_skill("it_ops.change").focus.lower()
        assert "emergency against planned mix" in change

        application = ds.get_skill("it_ops.application_performance").focus.lower()
        assert "unmonitored component" in application
        assert "reads as healthy" in application

        cost = ds.get_skill("it_ops.cost_cloud").focus.lower()
        assert "untagged spend is" in cost
        assert "amortised or as-invoiced" in cost


class TestHealthcareIsTheMostTightlyGovernedDomainCovered:
    """Batch 14. Three prohibitions, PHI, `aggregation_required`,
    `minimum_group_size=5` and an `export_row_limit` of 50 -- half the next
    tightest among covered domains.

    Two things here are new to the series.

    First, **the supplied scope sentence itself carries a prohibition**:
    it ends "; never diagnose", overlapping
    `ProhibitedAction.MEDICAL_DIAGNOSIS_OR_PRESCRIPTION`, which the
    composer already derives. It is stored verbatim anyway, for the
    reasons in `catalog._HEALTHCARE_SCOPE`, and the overlap is pinned here
    rather than left to be discovered.

    Second, **two deterministic controls have analytical consequences the
    skills must frame**, because the composer puts none of them in the
    prompt: small-group suppression replaces a measure with
    `SUPPRESSED_MASK`, and the export ceiling truncates a restricted
    result to 50 rows for an authorized caller too.
    """

    def test_the_three_prohibitions_are_derived_into_every_skills_block(self):
        """The premise, and the widest derived block in the catalog after
        HR's five."""
        actions = {a.value for a in policy_for(HEALTHCARE).prohibited_actions}
        assert actions == {
            "clinical_decision",
            "medical_diagnosis_or_prescription",
            "protected_characteristic_inference",
        }

        skills = ds.skills_for_domain(HEALTHCARE)
        assert len(skills) == 5
        for skill in skills:
            result = guidance_for(HEALTHCARE, enabled=True, preferred_slug=skill.slug)
            assert result is not None
            text, chosen = result
            assert chosen.slug == skill.slug
            for action in actions:
                assert action.replace("_", " ") in text, (skill.slug, action)
            assert "Report in aggregate only." in text, skill.slug

    def test_the_three_prohibitions_are_pinned_so_the_scope_overlap_cannot_go_stale(self):
        """The one real risk of keeping "never diagnose" in the stored
        scope: if the policy ever dropped
        `MEDICAL_DIAGNOSIS_OR_PRESCRIPTION`, the quoted sentence would go
        on asserting it while the derived block no longer did.

        That drift is in the safe direction, but it would be *silent*.
        This assertion makes it detected instead, which is the whole
        justification for storing the sentence verbatim.
        """
        from governance.domains import ProhibitedAction

        assert (
            ProhibitedAction.MEDICAL_DIAGNOSIS_OR_PRESCRIPTION
            in policy_for(HEALTHCARE).prohibited_actions
        )
        assert "never diagnose" in ds.get_skill("healthcare.operations").scope.lower()

    def test_the_scope_sentence_is_shared_verbatim_and_the_block_still_overrides(self):
        """The scope line and the derived block both touch diagnosis, and
        that is accepted -- but only because the derived block is the owner
        and comes last. If the ordering inverted, the quoted sentence would
        be the last word on a policy fact it does not own.
        """
        scopes = {s.scope for s in ds.skills_for_domain(HEALTHCARE)}
        assert len(scopes) == 1, "one scope per prompt-domain, written once"

        result = guidance_for(HEALTHCARE, enabled=True)
        assert result is not None
        text, _ = result
        assert text.index("Analytical scope:") < text.index("Domain constraints")
        assert text.rstrip().endswith("instead of doing it.")

    def test_no_focus_text_restates_a_derived_prohibition(self):
        """Batch 4's rule. The focus texts redirect to the cohort question
        instead of naming the prohibited action, which is also batch 8's
        rule: a refusal without an alternative is not a skill.
        """
        for skill in ds.skills_for_domain(HEALTHCARE):
            lowered = skill.focus.lower()
            assert "clinical decision" not in lowered, skill.slug
            assert "diagnos" not in lowered, skill.slug
            assert "prescrib" not in lowered, skill.slug
            assert "protected characteristic" not in lowered, skill.slug

    def test_the_two_clinical_skills_redirect_to_the_cohort_question(self):
        """Patient Journey and Quality & Compliance are *about* clinical
        outcomes, which is batch 8's problem in a new domain. Reporting a
        readmission or infection rate for a cohort is operations
        analytics; accounting for one patient's outcome is not.
        """
        journey = ds.get_skill("healthcare.patient_journey").focus.lower()
        assert "describe how a cohort moved and what the record shows" in journey
        assert "never account for why an individual patient" in journey

        quality = ds.get_skill("healthcare.quality_compliance").focus.lower()
        assert "by unit and cohort" in quality
        assert "report what was recorded and when" in quality

    def test_the_journey_skill_states_the_export_ceiling_consequence(self):
        """`governance.result_policy` truncates a restricted result to this
        domain's `export_row_limit` -- 50 -- for an authorized caller too,
        and reports `export_limited:N`. The control discloses the
        truncation; whether a share is computed over the remainder anyway
        is framing, and that is what this clause addresses.
        """
        assert policy_for(HEALTHCARE).export_row_limit == 50
        focus = ds.get_skill("healthcare.patient_journey").focus.lower()
        assert "truncated to the domain's export" in focus
        assert "is a sample, not a population" in focus

    def test_the_quality_skill_states_the_suppression_consequence(self):
        """`governance.suppression` substitutes `SUPPRESSED_MASK` for a
        sensitive measure in a cohort below five and keeps the label and
        count, never dropping the row. The mask is self-describing, so the
        analytical risk is not misreading one cell -- it is folding it
        into a total or reading it as an absence of events.
        """
        from governance.suppression import SUPPRESSED_MASK

        assert policy_for(HEALTHCARE).minimum_group_size == 5
        assert "suppressed" in SUPPRESSED_MASK

        focus = ds.get_skill("healthcare.quality_compliance").focus.lower()
        assert "withheld measure for a small cohort is not a zero" in focus
        assert "never fold it into a total" in focus

    def test_suppression_and_the_export_ceiling_are_different_controls(self):
        """Stated as an assertion because conflating them is easy and the
        consequences differ: suppression never drops a row, while the
        export ceiling is the one control that does (`CLAUDE.md` 5.4).
        The two clauses therefore live on different skills.
        """
        policy = policy_for(HEALTHCARE)
        assert policy.minimum_group_size == 5
        assert policy.export_row_limit == 50

        journey = ds.get_skill("healthcare.patient_journey").focus.lower()
        quality = ds.get_skill("healthcare.quality_compliance").focus.lower()
        assert "sample, not a population" in journey
        assert "sample, not a population" not in quality
        assert "not a zero" in quality
        assert "not a zero" not in journey

    def test_the_resource_skill_guards_the_individual_clinician(self):
        """`minimum_group_size=5` suppresses a measure for a cohort below
        five; it does nothing about ranking thirty clinicians who each saw
        two hundred patients. Batch 11 established that boundary; this is
        the sixth domain where the framing has to carry it.
        """
        focus = ds.get_skill("healthcare.resource").focus.lower()
        assert "never an individual clinician" in focus
        assert "case mix decides it" in focus
        assert "harder work reads as slower" in focus

    def test_each_skill_states_the_measurement_trap_its_subject_carries(self):
        """Read off the subject matter, not from the policy."""
        operations = ds.get_skill("healthcare.operations").focus.lower()
        assert "which clock a wait is measured from" in operations
        assert "point-in-time census and must never be summed" in operations
        assert "funded or physically" in operations

        journey = ds.get_skill("healthcare.patient_journey").focus.lower()
        assert "window and its index-admission" in journey
        assert "planned follow-up admissions" in journey
        assert "who went elsewhere" in journey

        resource = ds.get_skill("healthcare.resource").focus.lower()
        assert "headcount is not capacity" in resource
        assert "covered by a different grade is not the same shift" in resource

        financial = ds.get_skill("healthcare.financial").focus.lower()
        assert "three different numbers" in financial
        assert "without case-mix adjustment" in financial
        assert "stage a denial was measured at" in financial

        quality = ds.get_skill("healthcare.quality_compliance").focus.lower()
        assert "per device-day is not a rate per patient" in quality
        assert "ranks case mix rather than care" in quality
        assert "reporting culture as much as safety" in quality


class TestEnergyIsGovernedExactlyLikeManufacturing:
    """Batch 15: the sixth zero-prohibition domain, and the clearest case
    in the series for why this package exists.

    `policy_for(ENERGY)` is **field-identical to
    `policy_for(MANUFACTURING)`** in everything either the composer or the
    result gate reads: one declared restriction (`PROPRIETARY`), no
    prohibited actions, `minimum_group_size=0`, `export_row_limit=500`, no
    aggregation requirement, no audit requirement. Governance says nothing
    that distinguishes an energy question from a production one, so
    everything that makes an energy analysis an energy analysis is
    framing.

    The one field that differs cuts against this domain: MANUFACTURING
    pre-authorizes a join with SUPPLY_CHAIN, while ENERGY pre-authorizes
    nothing -- and the domain's signature measure, intensity, takes its
    denominator from another domain.

    What is new in the subject matter is **provenance**, one step beyond
    batches 11 to 13. An emissions figure is not a record of someone's
    claim, nor a self-assessment, nor an instrument reading: it is
    activity data scaled by a published factor someone selected, and most
    of Scope 3 is a supplier's claim or an estimate from spend. A fall can
    be a factor revision (`CLAUDE.md` 4.4).
    """

    def test_the_domain_genuinely_appends_no_constraint_block(self):
        """The premise. If ENERGY ever gains a `ProhibitedAction` the
        derived block starts covering some of this and these guards should
        be revisited rather than silently kept."""
        assert policy_for(ENERGY).prohibited_actions == frozenset()
        assert policy_for(ENERGY).minimum_group_size == 0

        skills = ds.skills_for_domain(ENERGY)
        # Non-vacuity: without this the loop empties when the batch is
        # declared under another domain and the test passes while
        # asserting nothing (section 7's mutation-25 lesson).
        assert len(skills) == 5
        for skill in skills:
            result = guidance_for(ENERGY, enabled=True, preferred_slug=skill.slug)
            assert result is not None
            text, chosen = result
            assert chosen.slug == skill.slug
            assert "Domain constraints" not in text, skill.slug
            assert "Rank top and bottom performers" in text, skill.slug

    def test_the_policy_is_field_identical_to_manufacturings(self):
        """The batch's thesis, asserted rather than asserted-in-prose.

        If this fails because ENERGY's policy was tightened, that is a
        governance improvement: update this test and section 19 of the
        contract document. Do not loosen the policy to keep it passing.
        """
        energy = policy_for(ENERGY)
        manufacturing = policy_for(MANUFACTURING)

        assert energy.restricted_categories == manufacturing.restricted_categories
        assert energy.prohibited_actions == manufacturing.prohibited_actions
        assert energy.minimum_group_size == manufacturing.minimum_group_size
        assert energy.export_row_limit == manufacturing.export_row_limit
        assert energy.aggregation_required == manufacturing.aggregation_required
        assert energy.audit_required == manufacturing.audit_required
        assert energy.required_permission == manufacturing.required_permission

        # The one substantive difference, and the one that matters here.
        assert manufacturing.cross_domain_allowed != frozenset()
        assert energy.cross_domain_allowed == frozenset()

    def test_the_governance_derived_half_of_the_prompt_is_therefore_identical(self):
        """Not a restatement of the test above: that compares the policy,
        this compares what reaches the model. Two domains with the same
        policy get the same derived text, so the skill text is the only
        thing carrying the difference -- which is the whole claim.
        """
        energy = guidance_for(ENERGY, enabled=True)
        manufacturing = guidance_for(MANUFACTURING, enabled=True)
        assert energy is not None and manufacturing is not None
        energy_text, energy_skill = energy
        manufacturing_text, manufacturing_skill = manufacturing

        assert "Domain constraints" not in energy_text
        assert "Domain constraints" not in manufacturing_text
        assert energy_skill.focus != manufacturing_skill.focus
        assert energy_skill.scope != manufacturing_skill.scope

        def shared_part(text: str, skill) -> list[str]:
            """Every line the skill itself did not contribute."""
            own = {
                f"Domain: {skill.domain.value}",
                f"Analytical scope: {skill.scope}",
                f"Focus for this question: {skill.focus}",
                "Dimensions worth considering: " + ", ".join(skill.dimensions) + ".",
            }
            return [line for line in text.split("\n") if line not in own]

        # Byte-identical: the contract, the blank lines and the headings
        # sentence, with no derived block on either side. The moment
        # ENERGY gains a prohibition this fails, which is correct -- the
        # claim above would no longer hold.
        assert shared_part(energy_text, energy_skill) == shared_part(
            manufacturing_text, manufacturing_skill
        )
        assert ANALYTICS_CONTRACT in "\n".join(shared_part(energy_text, energy_skill))

    def test_the_intensity_denominator_crosses_a_boundary_this_policy_does_not_open(self):
        """The first batch where `cross_domain_allowed` matters.

        Intensity -- consumption or emissions per unit of output, per
        square metre, per unit of revenue -- is this domain's signature
        measure, and its denominator lives in MANUFACTURING, REAL_ESTATE
        or FINANCE. ENERGY pre-authorizes none of them, in either
        direction.

        `governance/request_policy.py` owns what happens to such a
        request, and this package must not restate it (section 3). What
        belongs to the skill is the analytical half: a ratio across two
        grains is a new metric, so the denominator's source and grain have
        to be named.
        """
        from governance.domains import is_cross_domain_allowed

        assert is_cross_domain_allowed(ENERGY, MANUFACTURING) is False
        assert is_cross_domain_allowed(MANUFACTURING, ENERGY) is False
        assert is_cross_domain_allowed(ENERGY, FINANCE) is False

        focus = ds.get_skill("energy.efficiency").focus.lower()
        assert "takes its denominator from another domain" in focus
        assert "name the denominator, its source and its grain" in focus
        assert "a new metric, not a conversion" in focus

        # The analytical half only: no skill may claim to authorize, or to
        # refuse, a cross-domain join. That is the request gate's call.
        for skill in ds.skills_for_domain(ENERGY):
            lowered = skill.focus.lower()
            assert "authoriz" not in lowered, skill.slug
            assert "permission" not in lowered, skill.slug

    def test_an_emissions_figure_is_framed_as_derived_not_observed(self):
        """The provenance step this batch adds. The number is not
        measured: it is activity data scaled by a factor someone selected,
        so its movement can be a factor revision. No deterministic control
        catches this -- the arithmetic is valid either way.
        """
        focus = ds.get_skill("energy.emissions").focus.lower()
        assert "almost never measured" in focus
        assert "activity data scaled by a published emission factor" in focus
        assert "name the factor set and its vintage" in focus
        assert "a factor revision rather than a reduction" in focus
        assert "global-warming-potential set" in focus

    def test_the_three_scopes_are_not_one_measurement_regime(self):
        """Scope 2 has two methods that disagree about the same
        electricity, and most of Scope 3 is a third party's claim or an
        estimate from spend -- batch 11's record-versus-claim lesson
        arriving in a domain where it is the normal case rather than the
        exception.
        """
        focus = ds.get_skill("energy.emissions").focus.lower()
        assert "never present their sum as one observed quantity" in focus
        assert "location-based and market-based" in focus
        assert "neither interchangeable nor averageable" in focus
        assert "supplier's own claim or an estimate derived from spend" in focus
        assert "report gross and net separately" in focus

    def test_a_saving_is_framed_as_a_counterfactual(self):
        """Avoided consumption is the difference against an adjusted
        baseline, so it is a calculation and never a metered quantity.
        Reporting it as metered reduction is the same provenance error as
        the emissions one, one skill along.
        """
        focus = ds.get_skill("energy.efficiency").focus.lower()
        assert "a counterfactual rather than an observation" in focus
        assert "adjustments made for weather, occupancy and output" in focus
        assert "rather than metered reduction" in focus
        assert "claim the same unit twice" in focus

    def test_a_market_instrument_is_not_a_physical_reduction(self):
        """A purchased certificate changes a reported figure, not the
        meter. Stated on the sustainability skill because that is where a
        target-progress question lands.
        """
        focus = ds.get_skill("energy.sustainability").focus.lower()
        assert "a purchased certificate is a contractual instrument" in focus
        assert "never evidence that less energy was used on site" in focus

    def test_nothing_here_carries_an_individual_level_guard(self):
        """The absence is reasoned, not an omission.

        Batches 9 to 12 each had to guard an individual, because their
        subjects were drivers, operators, agents and allocated people.
        This domain's subjects are meters, sites, accounts and scopes: a
        meter is not a person, so there is no individual to re-identify
        and no guard to write.

        That is a statement about **framing**, not about protection. The
        personal baseline is unioned into every domain regardless
        (`CLAUDE.md` 5.8), so a personal column returned by an energy
        question is still gated -- which is why this reads
        `effective_restricted_categories` rather than the declared set.
        What is genuinely absent is suppression: `minimum_group_size=0`.
        """
        from governance.domains import DataCategory

        policy = policy_for(ENERGY)
        assert policy.restricted_categories == frozenset({DataCategory.PROPRIETARY})
        assert DataCategory.PII in policy.effective_restricted_categories
        assert DataCategory.SPI in policy.effective_restricted_categories
        assert policy.minimum_group_size == 0

        for skill in ds.skills_for_domain(ENERGY):
            lowered = skill.focus.lower()
            assert "individual" not in lowered, skill.slug
            assert "employee" not in lowered, skill.slug

    def test_the_guards_are_not_a_restatement_of_a_prohibition(self):
        for skill in ds.skills_for_domain(ENERGY):
            lowered = skill.focus.lower()
            assert "individual performance ranking" not in lowered
            assert "unsupported allegation" not in lowered
            assert "prohibit" not in lowered

    def test_each_skill_states_the_measurement_trap_its_subject_carries(self):
        """Read off the subject matter. Two are reused rather than
        rediscovered: the average-of-ratios error batch 10 found in OEE
        reappears in portfolio intensity, and batch 9's "a missing row is
        not a zero" reappears as a missing meter interval.
        """
        consumption = ds.get_skill("energy.consumption").focus.lower()
        assert "a difference between two meter readings" in consumption
        assert "unknown consumption, not zero" in consumption
        assert "actual or estimated" in consumption
        assert "a peak is an instant and must never be summed" in consumption
        assert "degree days" in consumption

        utility = ds.get_skill("energy.utility_performance").focus.lower()
        assert "a billing period is not a calendar month" in utility
        assert "blended unit cost mixes" in utility
        assert "leaves a unit rate undefined" in utility
        assert "excluded major-event days" in utility

        sustainability = ds.get_skill("energy.sustainability").focus.lower()
        assert "whether it has been restated" in sustainability
        assert "intensity falls while the absolute figure rises" in sustainability
        assert "outside the boundary reads as a clean one" in sustainability

        efficiency = ds.get_skill("energy.efficiency").focus.lower()
        assert "is not the portfolio ratio" in efficiency
        assert "recomposed from the underlying totals" in efficiency


class TestRiskAuditIsWhereTheSubjectMatterIsTheProhibition:
    """Batch 16: the first domain whose own vocabulary is the prohibited
    vocabulary.

    `policy_for(RISK_AUDIT)` declares two `ProhibitedAction`s and both
    bear on all five skills rather than on one. One of them,
    `liability_determination`, has **never been derived into a covered
    domain before** -- only LEGAL and RISK_AUDIT declare it, and LEGAL has
    no skills. The other, `unsupported_allegation`, is carried elsewhere
    but is incidental there; here the domain's professional output *is* an
    assertion: a finding is a recorded opinion against a criterion, a
    control rating is a conclusion from a sample, a risk score is a
    judgement, a loss attribution is a classification someone chose.

    Two further properties are measured here rather than claimed in
    prose, because the second one is counter-intuitive and the first time
    this series has written a governance claim that the code did not
    support:

    - `DataCategory.AUDIT` is restricted by this domain and no other, and
      it names the domain's ordinary subject matter.
    - That restriction is nonetheless **inert in the result gate**. The
      `audit_finding` rule classifies at `CONFIDENTIAL`, while every
      row-level control keys on `SENSITIVE` or above, so a findings
      result is not masked, not truncated by the tightest
      `export_row_limit` in the registry, and not suppressed for a small
      group. Those controls engage only when personal data is also
      present -- as in any domain.

    So the distinguishing thing governance has to say here reaches the
    model as prompt text, and nothing it says touches the rows.
    """

    def test_both_prohibitions_are_derived_into_every_skills_block(self):
        policy = policy_for(RISK_AUDIT)
        assert policy.prohibited_actions == frozenset(
            {ProhibitedAction.LIABILITY_DETERMINATION, ProhibitedAction.UNSUPPORTED_ALLEGATION}
        )

        skills = ds.skills_for_domain(RISK_AUDIT)
        # Non-vacuity: without this the loop empties if the batch is
        # declared under another domain and the test passes while
        # asserting nothing (§7's mutation-25 lesson).
        assert len(skills) == 5
        for skill in skills:
            result = guidance_for(RISK_AUDIT, enabled=True, preferred_slug=skill.slug)
            assert result is not None
            text, chosen = result
            assert chosen.slug == skill.slug
            assert "liability determination" in text, skill.slug
            assert "unsupported allegation" in text, skill.slug
            # Last, so it overrides the contract's ranking invitation.
            assert text.rstrip().endswith("say that plainly instead of doing it."), skill.slug

    def test_risk_audit_derived_liability_determination_before_legal_did(self):
        """Pins the claim, and this is the test going stale on purpose.

        It was written at batch 16 as "...reaches a covered domain for the
        first time", asserting `covered_deriving == [RISK_AUDIT]` with the
        docstring note that another domain gaining skills should fail it.
        Batch 17 registered LEGAL one batch later and it did fail, which
        is the guard working rather than a regression. The claim worth
        keeping is narrower and still true: RISK_AUDIT derived it first,
        and `covered_domains()` is in declaration order, so the ordering
        is readable from the catalog itself.
        """
        from governance.domains import DOMAIN_POLICIES

        declaring = {
            domain
            for domain, policy in DOMAIN_POLICIES.items()
            if ProhibitedAction.LIABILITY_DETERMINATION in policy.prohibited_actions
            and domain is not BusinessDomain.GENERAL
        }
        assert declaring == {LEGAL, RISK_AUDIT}

        covered_deriving = [
            d
            for d in ds.covered_domains()
            if ProhibitedAction.LIABILITY_DETERMINATION in policy_for(d).prohibited_actions
        ]
        # Both are covered now; RISK_AUDIT is still the earlier batch.
        assert covered_deriving == [RISK_AUDIT, LEGAL]

    def test_the_domain_restricts_its_own_subject_matter_and_nothing_else_does(self):
        """Every covered domain so far restricted a side-channel -- PII in
        manufacturing, PHI in healthcare. This one restricts the category
        that names what it is for."""
        from governance.classification import DataCategory, classify_field
        from governance.domains import DOMAIN_POLICIES

        restricting = {
            domain
            for domain, policy in DOMAIN_POLICIES.items()
            if DataCategory.AUDIT in policy.restricted_categories
        }
        assert restricting == {RISK_AUDIT}

        # And the classifier genuinely puts this domain's own columns in
        # that category, so the restriction is about real data.
        for column in (
            "audit_finding",
            "control_weakness",
            "risk_assessment",
            "remediation_plan",
            "non_conformance",
        ):
            assert classify_field("findings", column).category is DataCategory.AUDIT, column

    def test_that_restriction_is_inert_in_the_result_gate(self):
        """Measured, not assumed -- and the measurement corrected a claim
        this batch was about to publish.

        `audit_finding` classifies at `CONFIDENTIAL`; masking, the export
        ceiling and small-group suppression all require `SENSITIVE` or
        above. So the domain's signature restriction changes no rows.

        This is deliberate upstream (`BASELINE_RESTRICTED_CATEGORIES`
        excludes the commercially confidential categories on purpose, and
        `_carries_restricted_data` exists to stop bulk extraction of
        personal records rather than to cap ordinary analytics), so it is
        pinned here rather than 'fixed' from this package. If governance
        ever raises the `audit_finding` level this fails, and the contract
        document's §20 should be rewritten rather than the assertion
        loosened.
        """
        from governance.classification import DataClassification, classify_field
        from governance.result_policy import govern_result_for_domain

        assert (
            classify_field("findings", "audit_finding").classification
            is DataClassification.CONFIDENTIAL
        )
        assert DataClassification.CONFIDENTIAL < DataClassification.SENSITIVE

        rows = [(f"unit-{i}", f"finding-{i}") for i in range(60)]
        for roles in (["viewer"], ["analyst"], ["admin"]):
            governed = govern_result_for_domain(
                ["business_unit", "audit_finding"], rows, roles, RISK_AUDIT
            )
            assert governed.actions == (), roles
            assert len(governed.rows) == 60, roles

        # Non-vacuity: the same ceiling does fire once personal data is
        # present, so the assertion above is about the category and not
        # about a gate that never runs.
        personal = [(f"person-{i}", f"finding-{i}") for i in range(60)]
        governed = govern_result_for_domain(
            ["employee_name", "audit_finding"], personal, ["admin"], RISK_AUDIT
        )
        assert "export_limited:10" in governed.actions
        assert len(governed.rows) == 50

    def test_the_export_ceiling_is_the_tightest_in_the_registry(self):
        """True of the declared number, whatever the test above says about
        when it engages. Asserted so a later loosening is visible."""
        from governance.domains import DOMAIN_POLICIES

        assert policy_for(RISK_AUDIT).export_row_limit == 50
        assert policy_for(RISK_AUDIT).audit_required is True
        limits = [
            p.export_row_limit for p in DOMAIN_POLICIES.values() if p.export_row_limit is not None
        ]
        assert min(limits) == 50
        tightest = {
            d.value
            for d, p in DOMAIN_POLICIES.items()
            if p.export_row_limit == 50 and d is not BusinessDomain.GENERAL
        }
        assert tightest == {"healthcare", "legal", "risk_audit"}

    def test_no_focus_text_restates_a_derived_prohibition(self):
        """The composer owns both prohibitions. A focus text repeating one
        would be a second owner that can drift (`CLAUDE.md` §4.8), and in
        this domain the temptation is at its strongest because the subject
        matter is the same words."""
        for skill in ds.skills_for_domain(RISK_AUDIT):
            lowered = skill.focus.lower()
            assert "unsupported allegation" not in lowered, skill.slug
            assert "allegation" not in lowered, skill.slug
            assert "liabilit" not in lowered, skill.slug
            assert "prohibit" not in lowered, skill.slug

    def test_the_batch_does_not_derive_a_prohibition_it_does_not_have(self):
        """`fraud_accusation` belongs to BFSI, not here -- and a risk or
        audit question is exactly where someone would be tempted to
        hand-write it. Batch 7 made the same check for FINANCE."""
        policy = policy_for(RISK_AUDIT)
        assert ProhibitedAction.FRAUD_ACCUSATION not in policy.prohibited_actions
        assert ProhibitedAction.FRAUD_ACCUSATION in policy_for(BFSI).prohibited_actions

        result = guidance_for(RISK_AUDIT, enabled=True)
        assert result is not None
        text, _ = result
        assert "fraud accusation" not in text
        for skill in ds.skills_for_domain(RISK_AUDIT):
            assert "fraud" not in skill.focus.lower(), skill.slug

    def test_no_focus_text_claims_anything_about_a_cross_domain_join(self):
        """The domain's whole subject is other domains' processes -- a
        control over payroll is HR, over payments is FINANCE, over change
        is IT_OPERATIONS -- and `cross_domain_allowed` is empty in both
        directions. `governance/request_policy.py` owns what happens to
        such a request and this package must not restate it (§3)."""
        from governance.domains import DOMAIN_POLICIES, is_cross_domain_allowed

        assert policy_for(RISK_AUDIT).cross_domain_allowed == frozenset()
        assert [
            d.value for d, p in DOMAIN_POLICIES.items() if RISK_AUDIT in p.cross_domain_allowed
        ] == []
        assert is_cross_domain_allowed(RISK_AUDIT, HR) is False
        assert is_cross_domain_allowed(RISK_AUDIT, IT_OPERATIONS) is False

        for skill in ds.skills_for_domain(RISK_AUDIT):
            lowered = skill.focus.lower()
            assert "authoriz" not in lowered, skill.slug
            assert "permission" not in lowered, skill.slug

    def test_a_count_here_measures_how_much_looking_was_done(self):
        """The batch's distinguishing problem, and the next step in the
        provenance series.

        Batch 11 had a record of someone else's claim, 12 a
        self-assessment, 13 an instrument reading, 15 a figure nobody
        measured. Here the figure exists only where somebody looked: a
        finding where an audit went, an exception where a control was
        tested, a loss above a threshold someone set. Zero is ambiguous
        between clean, untested and undetected, and no deterministic
        control tells them apart -- the arithmetic is correct either way.
        """
        audit = ds.get_skill("risk_audit.internal_audit").focus.lower()
        assert "raised only where an audit looked" in audit
        assert "may be an unaudited unit rather than a clean one" in audit
        assert "never read a zero as an absence of issues" in audit

        controls = ds.get_skill("risk_audit.control_effectiveness").focus.lower()
        assert "untested is not effective" in controls
        assert "never let them fall into a pass-rate denominator" in controls

        operational = ds.get_skill("risk_audit.operational_risk").focus.lower()
        assert "truncated at a reporting threshold" in operational
        assert "better reporting rather than more failure" in operational

        continuity = ds.get_skill("risk_audit.business_continuity").focus.lower()
        assert "an unmapped dependency reads as no dependency" in continuity

    def test_a_rating_is_framed_as_a_judgement_and_not_as_arithmetic(self):
        """Batch 12 established that a risk score is a judgement rather
        than an outcome. What is new here is the ordinal arithmetic:
        likelihood times impact on an ordinal scale is a ranking code, so
        averaging or summing it is not a calculation of anything."""
        focus = ds.get_skill("risk_audit.enterprise_risk").focus.lower()
        assert "a ranking code rather than a quantity" in focus
        assert "a 12 is not twice a 6" in focus
        assert "never be averaged or summed" in focus
        assert "a judgement about a judgement" in focus
        assert "re-rating of the controls with no change in exposure" in focus
        assert "an exposure that has not occurred" in focus

    def test_a_test_conclusion_carries_its_sample(self):
        """An exception rate from a sample is an inference about a
        population, and from a judgemental sample it is not even that."""
        focus = ds.get_skill("risk_audit.control_effectiveness").focus.lower()
        assert "design and operating effectiveness are different conclusions" in focus
        assert "statistical or judgemental" in focus
        assert "not a four per cent population rate" in focus
        assert "cannot be projected to the population at all" in focus

    def test_the_most_recent_period_is_framed_as_incomplete(self):
        """Discovery lag makes the newest period of a loss series look
        best. Reported as improvement it is a false `DATABASE_FACT`
        (`CLAUDE.md` §4.4), and nothing deterministic catches it."""
        focus = ds.get_skill("risk_audit.operational_risk").focus.lower()
        assert "occurrence date, discovery date and accounting date" in focus
        assert "treat the most recent periods as understated" in focus
        assert "incompleteness rather than improvement" in focus
        assert "report frequency and severity separately" in focus
        assert "a signal to investigate, never a measured" in focus

    def test_a_passed_exercise_is_not_demonstrated_recoverability(self):
        """A target is not an achievement and a rehearsal is not an
        event -- the same shape as batch 12's reported-versus-measured,
        on a different subject."""
        focus = ds.get_skill("risk_audit.business_continuity").focus.lower()
        assert "targets agreed in a plan, never achieved times" in focus
        assert "a tabletop is not a failover" in focus
        assert "never present a passed exercise as demonstrated recoverability" in focus
        assert "state where the clock starts" in focus

    def test_the_individual_level_guard_is_present_where_there_is_an_individual(self):
        """The mirror of batch 15, which reasoned its *absence*.

        A meter is not a person, so ENERGY needed no such guard. A risk
        owner, an auditee, a control performer and a loss event's operator
        all are, and each is a plausible thing to rank -- so four of the
        five skills carry the guard explicitly.

        The domain declares no individual-performance prohibition, so
        nothing derived covers this. It is the skill's own work.
        """
        assert (
            ProhibitedAction.INDIVIDUAL_PERFORMANCE_RANKING
            not in policy_for(RISK_AUDIT).prohibited_actions
        )

        assert (
            "never rank named owners by their open risks"
            in ds.get_skill("risk_audit.enterprise_risk").focus.lower()
        )
        assert (
            "never conclude who is at fault"
            in ds.get_skill("risk_audit.internal_audit").focus.lower()
        )
        assert (
            "rather than by the person who performed it"
            in ds.get_skill("risk_audit.control_effectiveness").focus.lower()
        )
        assert (
            "never attributing an event to a named person"
            in ds.get_skill("risk_audit.operational_risk").focus.lower()
        )

    def test_each_skill_states_the_measurement_trap_its_subject_carries(self):
        """Read off the subject matter. One is reused rather than
        rediscovered: batch 9's "a missing row is not a zero" returns as
        an unmapped continuity dependency and as an unaudited unit."""
        enterprise = ds.get_skill("risk_audit.enterprise_risk").focus.lower()
        assert "a short register may be an unmaintained one" in enterprise

        audit = ds.get_skill("risk_audit.internal_audit").focus.lower()
        assert "a due date the owner can revise" in audit
        assert "closure is management's assertion unless audit validated it" in audit
        assert "not risk removed" in audit
        assert "different scope and rating scale are not comparable" in audit

        controls = ds.get_skill("risk_audit.control_effectiveness").focus.lower()
        assert "tested once as a configuration" in controls
        assert "counts of different things" in controls

        operational = ds.get_skill("risk_audit.operational_risk").focus.lower()
        assert "a change to it breaks the series" in operational
        assert "which makes a mean uninformative" in operational

        continuity = ds.get_skill("risk_audit.business_continuity").focus.lower()
        assert "excludes detection and decision time" in continuity
        assert "coverage of the mapping alongside any concentration figure" in continuity


class TestLegalIsWhereTheProhibitedOutputIsTheQuestion:
    """Batch 17: the domain whose prohibitions overlap what people ask.

    `policy_for(LEGAL)` declares three `ProhibitedAction`s and they are a
    **strict superset of batch 16's** -- the first superset in the series,
    one batch after the set it contains. `legal_conclusion_or_advice` is
    declared by LEGAL alone and was the last of the fourteen never derived
    into a covered domain, so registering this batch closes that set.

    The difficulty is not the count. In batch 16 the subject matter was a
    recorded assertion; here the prohibited output *is* the natural
    question -- "are we compliant?", "will we win?", "is this clause
    enforceable?" -- so a skill that only refused would answer nothing
    (`CLAUDE.md` §4.2, and §12's lesson that a refusal without an
    alternative is not a skill). Every focus text redirects to the same
    answerable question instead: what does the record hold, in which
    field, as of when.

    Two measured findings, both of which contradicted a first draft:

    - The domain's two declared categories behave **oppositely** at the
      row level. `LEGAL` classifies at `HIGHLY_SENSITIVE` and is genuinely
      live (masked, and it trips the export ceiling for every caller);
      `CONTRACT` classifies at `CONFIDENTIAL` and changes no rows, the
      same inertness batch 16 found for `AUDIT`.
    - A declared `minimum_group_size=0` is **not** a real zero. Three
      earlier batch comments said it was.
    """

    def test_all_three_prohibitions_are_derived_into_every_skills_block(self):
        policy = policy_for(LEGAL)
        assert policy.prohibited_actions == frozenset(
            {
                ProhibitedAction.LEGAL_CONCLUSION_OR_ADVICE,
                ProhibitedAction.LIABILITY_DETERMINATION,
                ProhibitedAction.UNSUPPORTED_ALLEGATION,
            }
        )

        skills = ds.skills_for_domain(LEGAL)
        # Non-vacuity: without this the loop empties when the batch is
        # declared under another domain and the test passes while
        # asserting nothing (§7's mutation-25 lesson).
        assert len(skills) == 5
        for skill in skills:
            result = guidance_for(LEGAL, enabled=True, preferred_slug=skill.slug)
            assert result is not None
            text, chosen = result
            assert chosen.slug == skill.slug
            assert "legal conclusion or advice" in text, skill.slug
            assert "liability determination" in text, skill.slug
            assert "unsupported allegation" in text, skill.slug
            assert text.rstrip().endswith("say that plainly instead of doing it."), skill.slug

    def test_the_prohibitions_are_a_strict_superset_of_batch_16s(self):
        """Batch 6 was a strict subset of another domain's (§10). This is
        the first strict superset, and it arrives one batch after the set
        it contains, which is why the two classes read as a pair."""
        legal = policy_for(LEGAL).prohibited_actions
        risk_audit = policy_for(RISK_AUDIT).prohibited_actions
        assert risk_audit < legal
        assert legal - risk_audit == frozenset({ProhibitedAction.LEGAL_CONCLUSION_OR_ADVICE})

    def test_legal_conclusion_or_advice_is_this_domains_alone(self):
        from governance.domains import DOMAIN_POLICIES

        declaring = {
            domain
            for domain, policy in DOMAIN_POLICIES.items()
            if ProhibitedAction.LEGAL_CONCLUSION_OR_ADVICE in policy.prohibited_actions
            and domain is not BusinessDomain.GENERAL
        }
        assert declaring == {LEGAL}

    def test_every_prohibited_action_is_now_derived_by_a_covered_domain(self):
        """The milestone this batch completes, asserted rather than
        claimed in prose.

        `legal_conclusion_or_advice` was the last of the fourteen that no
        covered domain declared -- batch 16 took `liability_determination`
        and this one takes the last. Every `ProhibitedAction` is now
        reachable through `composer._prohibitions_block` for at least one
        registered skill, so the derivation §9 describes is exercised
        across the whole enum rather than a subset of it.

        If this fails because a new `ProhibitedAction` was added to the
        enum, that is correct and the right fix is a batch for whichever
        domain declares it -- not deleting the assertion.
        """
        from governance.domains import ALL_PROHIBITED_ACTIONS

        covered = ds.covered_domains()
        derived: set[ProhibitedAction] = set()
        for domain in covered:
            derived |= policy_for(domain).prohibited_actions
        assert derived == set(ALL_PROHIBITED_ACTIONS)

        # And each really does reach a composed prompt, not just a policy.
        for action in ALL_PROHIBITED_ACTIONS:
            phrase = action.value.replace("_", " ")
            hit = False
            for domain in covered:
                if action not in policy_for(domain).prohibited_actions:
                    continue
                result = guidance_for(domain, enabled=True)
                if result is None:
                    # An ambiguous domain refuses to pick a default
                    # (§8); name a skill explicitly instead.
                    skill = ds.skills_for_domain(domain)[0]
                    result = guidance_for(domain, enabled=True, preferred_slug=skill.slug)
                assert result is not None
                if phrase in result[0]:
                    hit = True
                    break
            assert hit, action.value

    def test_a_declared_zero_group_size_is_not_a_real_zero(self):
        """Measured, and it corrected three comments in this package.

        `governance.domains.minimum_group_size_for` is
        `policy.minimum_group_size if policy.minimum_group_size else
        configured_default`, so a declared `0` is falsy and the
        operator's default applies -- and `GOVERNANCE_MIN_GROUP_SIZE`
        defaults to 5. The function's own docstring says the opposite
        ("honored as a real zero, not treated as unset"), so the
        contradiction lives in `governance/` and is reported rather than
        fixed from here (`CLAUDE.md` §4.7).

        Pinned because three batch comments in `catalog.py` read the
        declared zero as "no suppression here" and were wrong. If
        `governance/` is later fixed to honour the zero, this test fails
        and those comments become true again -- which is the right way
        round.
        """
        from config.settings import Settings
        from governance.domains import minimum_group_size_for

        assert policy_for(LEGAL).minimum_group_size == 0
        assert minimum_group_size_for(LEGAL, 0) == 0
        assert minimum_group_size_for(LEGAL, 5) == 5
        # The shipped default is not 0, so this is the live behaviour.
        assert Settings.model_fields["governance_min_group_size"].default == 5
        # A domain that declares its own threshold is unaffected.
        assert minimum_group_size_for(RISK_AUDIT, 0) == 5

    def test_a_small_board_category_is_not_suppressed_so_the_guard_is_framing(self):
        """What limits suppression here is the classification threshold,
        not the declared zero.

        A board-composition count is an ordinary integer, so
        `suppression._is_sensitive_measure` is False and the row is
        returned intact even with k=5 configured. A board is about ten
        people, so a one-person category describes an identifiable
        person and nothing deterministic will hide it -- which is why
        the scorecard skill carries the guard itself.
        """
        from governance.result_policy import govern_result_for_domain

        rows = [("independent", 4), ("executive", 3), ("chair", 1)]
        for roles in (["viewer"], ["admin"]):
            governed = govern_result_for_domain(
                ["director_category", "director_count"], rows, roles, LEGAL, 5
            )
            assert governed.actions == (), roles
            assert list(governed.rows) == rows, roles

        focus = ds.get_skill("legal.governance_scorecard").focus.lower()
        assert "about ten people" in focus
        assert "a single-member category describes an identifiable person" in focus
        assert "counts next to every percentage" in focus

    def test_the_two_declared_categories_behave_oppositely_at_the_row_level(self):
        """The finding that contradicted this batch's first draft, which
        had assumed both would be inert as batch 16's `AUDIT` was.

        If governance changes either classification level this fails, and
        §21 of the contract document should be rewritten rather than the
        assertion loosened.
        """
        from governance.classification import DataCategory, DataClassification, classify_field
        from governance.result_policy import govern_result_for_domain

        legal_policy = classify_field("matters", "litigation_status")
        assert legal_policy.category is DataCategory.LEGAL
        assert legal_policy.classification is DataClassification.HIGHLY_SENSITIVE
        assert legal_policy.classification >= DataClassification.SENSITIVE

        contract_policy = classify_field("contracts", "contract_value")
        assert contract_policy.category is DataCategory.CONTRACT
        assert contract_policy.classification is DataClassification.CONFIDENTIAL
        assert contract_policy.classification < DataClassification.SENSITIVE

        assert {DataCategory.LEGAL, DataCategory.CONTRACT} <= policy_for(
            LEGAL
        ).restricted_categories

        matters = [(f"m-{i}", "open") for i in range(60)]
        viewer = govern_result_for_domain(
            ["matter_id", "litigation_status"], matters, ["viewer"], LEGAL
        )
        assert "masked_classified_columns:litigation_status" in viewer.actions
        assert "export_limited:10" in viewer.actions
        assert len(viewer.rows) == 50
        # The ceiling applies to an authorized caller too -- holding the
        # permission means a value may be seen, not extracted in bulk.
        admin = govern_result_for_domain(
            ["matter_id", "litigation_status"], matters, ["admin"], LEGAL
        )
        assert "export_limited:10" in admin.actions
        assert len(admin.rows) == 50

        contracts = [(f"c-{i}", 1000) for i in range(60)]
        for roles in (["viewer"], ["admin"]):
            governed = govern_result_for_domain(
                ["contract_id", "contract_value"], contracts, roles, LEGAL
            )
            assert governed.actions == (), roles
            assert len(governed.rows) == 60, roles

    def test_only_the_matter_skill_states_the_truncation_consequence(self):
        """Because only matter columns actually trip the ceiling. The
        contract skill deliberately does not carry the clause -- a
        guard for a control that does not fire there would be noise,
        and §4.8's one-owner rule cuts both ways.
        """
        matter = ds.get_skill("legal.matter").focus.lower()
        assert "capped at the domain's export ceiling for every caller" in matter
        assert "treat what came back as a capped extract" in matter

        contract = ds.get_skill("legal.contract").focus.lower()
        assert "export ceiling" not in contract
        assert "capped" not in contract

    def test_no_focus_text_restates_a_derived_prohibition(self):
        """Hardest here of any batch: the derived block's own words are
        this domain's vocabulary. A restatement would be a second owner
        free to drift (`CLAUDE.md` §4.8)."""
        for skill in ds.skills_for_domain(LEGAL):
            lowered = skill.focus.lower()
            assert "legal conclusion" not in lowered, skill.slug
            assert "advice" not in lowered, skill.slug
            assert "allegation" not in lowered, skill.slug
            assert "liabilit" not in lowered, skill.slug
            assert "prohibit" not in lowered, skill.slug

    def test_every_skill_redirects_rather_than_only_refusing(self):
        """§12's lesson, and the batch it matters most for.

        Each skill names something it will not say *and* the narrower
        question it answers instead. A refusal with no alternative would
        make the skill useless in exactly the domain where the
        prohibited output is what was asked for.
        """
        refusals = {
            "legal.contract": "never say whether a clause is enforceable",
            "legal.matter": "never weigh the merits",
            "legal.compliance": "never state whether the organisation is compliant",
            "legal.board_secretarial": "never as a judgement of how well they served",
            "legal.governance_scorecard": "never present the composite",
        }
        redirects = {
            "legal.contract": "report what the record holds, in which field and as of when",
            "legal.matter": "report counts, types, stages and spend",
            "legal.compliance": "how much of the register is attested, by whom, and as of when",
            "legal.board_secretarial": "report by body, committee and entity",
            "legal.governance_scorecard": "report the components beside the total",
        }
        skills = ds.skills_for_domain(LEGAL)
        assert len(skills) == 5
        for skill in skills:
            lowered = skill.focus.lower()
            assert refusals[skill.slug] in lowered, f"{skill.slug}: refusal"
            assert redirects[skill.slug] in lowered, f"{skill.slug}: redirect"

    def test_no_focus_text_claims_anything_about_a_cross_domain_join(self):
        from governance.domains import DOMAIN_POLICIES, is_cross_domain_allowed

        assert policy_for(LEGAL).cross_domain_allowed == frozenset()
        assert [
            d.value for d, p in DOMAIN_POLICIES.items() if LEGAL in p.cross_domain_allowed
        ] == []
        assert is_cross_domain_allowed(LEGAL, FINANCE) is False
        assert is_cross_domain_allowed(LEGAL, RISK_AUDIT) is False

        for skill in ds.skills_for_domain(LEGAL):
            lowered = skill.focus.lower()
            assert "authoriz" not in lowered, skill.slug
            assert "permission" not in lowered, skill.slug

    def test_a_contract_value_is_framed_as_a_ceiling(self):
        """The domain's most common arithmetic error: a not-to-exceed
        figure summed as though it were committed spend."""
        focus = ds.get_skill("legal.contract").focus.lower()
        assert "a ceiling rather than an amount earned or spent" in focus
        assert "authorises, it does not commit" in focus
        assert "never present it as revenue or cost" in focus
        assert "give the term or an annualised value" in focus

    def test_an_expiry_date_is_not_the_date_that_governs(self):
        focus = ds.get_skill("legal.contract").focus.lower()
        assert "an auto-renewing contract does not expire" in focus
        assert "the notice deadline is the date that governs" in focus
        assert "three different dates" in focus

    def test_a_reserve_is_framed_as_an_estimate_not_an_outcome(self):
        """Batch 12's judgement-not-outcome shape, on a number that also
        looks like money -- which is what makes it easy to report as
        fact."""
        focus = ds.get_skill("legal.matter").focus.lower()
        assert "an accounting estimate made under a policy" in focus
        assert "never a prediction of loss" in focus
        assert "can be a re-estimation rather than an event" in focus
        assert "outcome codes are not a win rate" in focus

    def test_matter_ageing_and_spend_carry_their_lag(self):
        """The discovery-lag shape batch 16 found in loss data, arriving
        twice in one skill for different reasons: when a matter was
        opened, and when counsel invoiced."""
        focus = ds.get_skill("legal.matter").focus.lower()
        assert "when the legal team learned of it" in focus
        assert "measures the process and not the dispute" in focus
        assert "outside-counsel invoices arrive late" in focus
        assert "the most recent periods understate" in focus

    def test_a_completion_rate_is_not_compliance(self):
        """The clearest case of the batch's problem: the question asked
        is "are we compliant" and the data answers something narrower."""
        focus = ds.get_skill("legal.compliance").focus.lower()
        assert "evidence that a process ran" in focus
        assert "says the process completed" in focus
        assert "reads as attested" in focus
        assert "a permitted deviation rather than a failure" in focus
        assert "the register's as-of date" in focus

    def test_attendance_and_quorum_carry_their_definitions(self):
        focus = ds.get_skill("legal.board_secretarial").focus.lower()
        assert "recorded presence and not participation" in focus
        assert "proxy or an alternate" in focus
        assert "quorate with few people present" in focus
        assert "compares different clocks" in focus
        assert "dormant and struck-off entities" in focus

    def test_a_composite_score_names_its_framework_and_weighting(self):
        focus = ds.get_skill("legal.governance_scorecard").focus.lower()
        assert "the total moves with the weighting" in focus
        assert "name the framework and its version" in focus
        assert "differs between codes and jurisdictions" in focus
        assert "describe form and not function" in focus
        assert "name the peer set" in focus

    def test_the_privilege_guard_is_this_batchs_own_work(self):
        """Nothing derived covers it. The prohibitions are about
        conclusions; privilege is about *content*, and the result gate
        protects a column classified `legal` but not a free-text summary
        the model might write from one.
        """
        focus = ds.get_skill("legal.matter").focus.lower()
        assert "never the substance of a matter or the strategy in it" in focus
        for action in policy_for(LEGAL).prohibited_actions:
            assert "privileg" not in action.value


class TestRealEstateIsWhereTheAssetIsTheIdentifier:
    """Batch 18: the first covered domain whose subject is a building.

    `policy_for(REAL_ESTATE)` declares no `ProhibitedAction`, so this is
    the seventh zero-prohibition domain and the contract's ranking
    invitation reaches it unqualified. Unlike batch 15's ENERGY the policy
    is not field-identical to any other: the personal baseline plus
    `CONTRACT`, a declared `minimum_group_size=5`, `export_row_limit=500`.

    Four measured findings, three of them the opposite of the tidy story:

    - **A property's address is classified as personal data** -- PII at
      SENSITIVE -- because the `address` rule is deliberately unbounded
      and cannot tell a building from a home. In every other covered
      domain an address is incidental; here it is the asset's identifier.
    - **That column is the only thing that opens the export ceiling.**
      `_carries_restricted_data` needs SENSITIVE *and* a restricted
      category, so a 600-row portfolio listing is capped only when it
      selects the address -- and then for an admin too.
    - **`CONTRACT`, the domain's one non-baseline category, is inert**
      at `CONFIDENTIAL`. Third time in the series, first time it is the
      domain's entire declared addition to the baseline.
    - **Small-grain occupancy is not suppressed.** The declared `5` is a
      real 5 (unlike batch 17's declared `0`), but suppression keys on
      the measure's classification and an occupant count is an ordinary
      integer.

    And two columns a real-estate schema certainly holds -- `tenant_name`
    and `technician_name` -- are not classified at all, because the
    `person_name` rule enumerates prefixes and neither is among them. The
    lease and maintenance skills carry guards nothing deterministic backs,
    which is reported here rather than fixed from this package.
    """

    def test_the_domain_genuinely_appends_no_constraint_block(self):
        """The premise. If REAL_ESTATE ever gains a `ProhibitedAction` the
        derived block starts covering some of this and these guards should
        be revisited rather than silently kept."""
        assert policy_for(REAL_ESTATE).prohibited_actions == frozenset()

        skills = ds.skills_for_domain(REAL_ESTATE)
        # Non-vacuity: without this the loop empties when the batch is
        # declared under another domain and the test passes while
        # asserting nothing (§16's mutation-25 lesson).
        assert len(skills) == 5
        for skill in skills:
            result = guidance_for(REAL_ESTATE, enabled=True, preferred_slug=skill.slug)
            assert result is not None
            text, chosen = result
            assert chosen.slug == skill.slug
            assert "Domain constraints" not in text, skill.slug

    def test_the_policy_is_not_field_identical_to_any_other_domains(self):
        """Batch 15's claim does not transfer. ENERGY matched
        MANUFACTURING field for field, which is what made framing the
        only differentiator there; this domain's policy is its own, so
        the derived half of the prompt is not shared with anybody.
        """
        from dataclasses import fields

        from governance.domains import DOMAIN_POLICIES

        def signature(policy):
            return tuple(
                (f.name, getattr(policy, f.name))
                for f in fields(policy)
                if f.name not in ("domain", "description")
            )

        mine = signature(policy_for(REAL_ESTATE))
        twins = [
            d.value
            for d, p in DOMAIN_POLICIES.items()
            if d is not REAL_ESTATE and signature(p) == mine
        ]
        assert twins == []

        # The two fields that make it unique among the zero-prohibition
        # domains: a declared threshold and the `CONTRACT` restriction.
        for other in (SUPPLY_CHAIN, MANUFACTURING, PROJECT, ENERGY):
            assert policy_for(other).minimum_group_size == 0, other.value
        assert policy_for(REAL_ESTATE).minimum_group_size == 5

    def test_a_property_address_is_classified_as_personal_data(self):
        """The finding that shaped the portfolio skill.

        `governance.classification`'s `address` rule is documented as
        deliberately unbounded -- it has to be, to catch `StreetAddress`
        and `postal_code` alike -- and it therefore cannot tell a
        building's location from somebody's home. That is the safe
        direction to fail, and it has an analytical consequence: the
        column a property analysis most naturally labels rows with comes
        back `[restricted]`, so the rows become indistinguishable.
        """
        from governance.classification import DataCategory, DataClassification, classify_field
        from governance.result_policy import govern_result_for_domain

        for column in ("property_address", "site_address", "street", "postal_code"):
            policy = classify_field("properties", column)
            assert policy.category is DataCategory.PII, column
            assert policy.classification is DataClassification.SENSITIVE, column
            assert policy.classification >= DataClassification.SENSITIVE, column

        # The asset's own non-address identifiers are not restricted, so
        # the guard the skill gives is actionable rather than a dead end.
        for column in ("property_id", "property_name", "city", "gross_floor_area"):
            assert classify_field("properties", column).category is DataCategory.NONE, column

        rows = [(f"{i} High Street", 0.9) for i in range(60)]
        viewer = govern_result_for_domain(
            ["property_address", "occupancy_rate"], rows, ["viewer"], REAL_ESTATE, 5
        )
        assert "masked_classified_columns:property_address" in viewer.actions
        assert len(viewer.rows) == 60
        # Indistinguishable: every row's label is now the same string.
        assert len({tuple(row)[0] for row in viewer.rows}) == 1

        admin = govern_result_for_domain(
            ["property_address", "occupancy_rate"], rows, ["admin"], REAL_ESTATE, 5
        )
        assert admin.actions == ()

        focus = ds.get_skill("real_estate.portfolio").focus.lower()
        assert "an address is treated as personal data" in focus
        assert "every masked row then reads alike" in focus
        assert "label and group properties by identifier or name" in focus
        assert "never read two masked rows as one property" in focus

    def test_the_address_is_the_only_column_that_opens_the_export_ceiling(self):
        """Measured, and it narrows the declared 500 considerably.

        `result_policy._carries_restricted_data` engages the ceiling only
        when a column classifies at SENSITIVE *and* falls in a category
        the domain restricts. A property listing keyed by id, and a
        600-row work-order extract, are therefore returned whole -- the
        ceiling fires on the address and on nothing else a property
        analysis naturally selects.
        """
        from governance.result_policy import govern_result_for_domain

        assert policy_for(REAL_ESTATE).export_row_limit == 500

        addressed = [(f"{i} High Street", 0.9) for i in range(600)]
        for roles in (["viewer"], ["admin"]):
            governed = govern_result_for_domain(
                ["property_address", "occupancy_rate"], addressed, roles, REAL_ESTATE, 5
            )
            # Including an admin: holding the permission means a value
            # may be seen, not extracted in bulk.
            assert "export_limited:100" in governed.actions, roles
            assert len(governed.rows) == 500, roles

        for columns, rows in (
            (["property_id", "occupancy_rate"], [(f"p-{i}", 0.9) for i in range(600)]),
            (["work_order_id", "repair_cost"], [(f"w-{i}", 120) for i in range(600)]),
        ):
            for roles in (["viewer"], ["admin"]):
                governed = govern_result_for_domain(columns, rows, roles, REAL_ESTATE, 5)
                assert governed.actions == (), (columns, roles)
                assert len(governed.rows) == 600, (columns, roles)

    def test_only_the_portfolio_skill_states_the_truncation_consequence(self):
        """Because only a listing that selects the address trips it. A
        guard for a control that does not fire is noise, and §4.8's
        one-owner rule cuts both ways (batch 17 made the same split
        between its matter and contract skills).
        """
        portfolio = ds.get_skill("real_estate.portfolio").focus.lower()
        assert "capped at the domain's export ceiling for every caller" in portfolio
        assert "treat such a result as a capped extract" in portfolio

        for slug in (
            "real_estate.occupancy",
            "real_estate.lease",
            "real_estate.facilities_maintenance",
            "real_estate.financial",
        ):
            focus = ds.get_skill(slug).focus.lower()
            assert "export ceiling" not in focus, slug
            assert "capped" not in focus, slug

    def test_the_one_non_baseline_category_this_domain_declares_is_inert(self):
        """Third time in the series, and the first time it is the whole
        of the domain's declared addition.

        Batch 16 found `AUDIT` inert and batch 17 found `CONTRACT` inert
        beside a live `LEGAL`. Here `CONTRACT` is the only category this
        policy adds to the personal baseline, so every control that
        actually fires in this domain comes from the baseline
        `CLAUDE.md` §5.8 unions in -- not from anything the policy says.
        """
        from governance.classification import DataCategory, DataClassification, classify_field
        from governance.result_policy import govern_result_for_domain

        policy = policy_for(REAL_ESTATE)
        assert policy.restricted_categories == frozenset(
            {DataCategory.PII, DataCategory.SPI, DataCategory.CONTRACT}
        )
        # Declared minus the baseline is exactly the inert one.
        assert policy.restricted_categories - frozenset(
            {DataCategory.PII, DataCategory.SPI}
        ) == frozenset({DataCategory.CONTRACT})

        for column in ("lease_term", "lease_amount", "contract_value", "vendor_rate"):
            field_policy = classify_field("leases", column)
            assert field_policy.category is DataCategory.CONTRACT, column
            assert field_policy.classification is DataClassification.CONFIDENTIAL, column
            assert field_policy.classification < DataClassification.SENSITIVE, column

        leases = [(f"l-{i}", 1000) for i in range(600)]
        for roles in (["viewer"], ["admin"]):
            governed = govern_result_for_domain(
                ["lease_id", "lease_amount"], leases, roles, REAL_ESTATE, 5
            )
            assert governed.actions == (), roles
            assert len(governed.rows) == 600, roles

    def test_a_declared_five_is_a_real_five_unlike_batch_17s_zero(self):
        """The other side of batch 17's finding.

        `minimum_group_size_for` is `policy.minimum_group_size if
        policy.minimum_group_size else configured_default`, so a declared
        `0` is falsy and the operator's value applies. A declared `5` is
        truthy, so this domain's threshold holds even if an operator
        configures none -- which is why the next test is about the
        classification threshold rather than about k.
        """
        from governance.domains import minimum_group_size_for

        assert policy_for(REAL_ESTATE).minimum_group_size == 5
        assert minimum_group_size_for(REAL_ESTATE, 0) == 5
        assert minimum_group_size_for(REAL_ESTATE, 5) == 5
        # The contrast, pinned so the pair cannot drift apart.
        assert policy_for(LEGAL).minimum_group_size == 0
        assert minimum_group_size_for(LEGAL, 0) == 0

    def test_small_grain_occupancy_is_not_suppressed_so_the_guard_is_framing(self):
        """The batch's sharpest guard, and the reason it is framing.

        `suppression._is_sensitive_measure` keys on the *measure's*
        classification, and an occupant count is an ordinary integer, so
        a one-occupant floor is returned intact at k=5 for every caller.
        Occupancy is derived from badge, booking and sensor records about
        identifiable people, so a fine-grained cell can stand for one
        person and nothing deterministic will hide it.
        """
        from governance.classification import DataCategory, classify_field
        from governance.result_policy import govern_result_for_domain

        for column in ("occupancy_rate", "desk_utilisation", "headcount", "badge_swipes"):
            assert classify_field("occupancy", column).category is DataCategory.NONE, column

        rows = [("L1", 42), ("L2", 3), ("L3", 1)]
        for roles in (["viewer"], ["admin"]):
            governed = govern_result_for_domain(
                ["floor", "occupant_count"], rows, roles, REAL_ESTATE, 5
            )
            assert governed.actions == (), roles
            assert list(governed.rows) == rows, roles

        # Suppression is not broken -- it fires when the measure itself
        # classifies, which is what makes the contrast meaningful.
        with_name = govern_result_for_domain(
            ["floor", "employee_name", "occupant_count"],
            [("L1", "Ann Smith", 42), ("L3", "Bo Lee", 1)],
            ["admin"],
            REAL_ESTATE,
            5,
        )
        assert "suppressed_small_groups:1" in with_name.actions

        focus = ds.get_skill("real_estate.occupancy").focus.lower()
        assert "an occupant count is an ordinary integer" in focus
        assert "no cell can stand for a single person" in focus
        assert "give the count beside every percentage" in focus
        assert "never describe when a named person was present" in focus

    def test_two_person_columns_this_domain_holds_are_not_classified(self):
        """Where this batch's own work is, measured rather than assumed.

        `person_name` matches an enumerated list of prefixes --
        `employee_`, `customer_`, `patient_`, `contact_`, `first_`,
        `last_` and so on -- and neither `tenant_name` nor
        `technician_name` is among them. A residential tenant and a
        maintenance engineer are both identifiable people, so the lease
        and maintenance skills carry guards nothing deterministic backs.

        Reported, not fixed from here: an operator's explicit entry in
        `config/sensitive_columns.yaml` is the supported route, and
        editing `governance/classification.py` is a governance change
        rather than a skill one (`CLAUDE.md` §4.7). If that changes, this
        test fails and the two guards become redundant -- which is the
        right way round.
        """
        from governance.classification import DataCategory, classify_field
        from governance.result_policy import govern_result_for_domain

        for table, column in (("leases", "tenant_name"), ("work_orders", "technician_name")):
            assert classify_field(table, column).category is DataCategory.NONE, column

        # Non-vacuity: the rule does fire for the prefixes it enumerates,
        # so this is a gap in the list and not a broken classifier.
        assert classify_field("occupancy", "employee_name").category is DataCategory.PII
        assert classify_field("leases", "customer_name").category is DataCategory.PII

        governed = govern_result_for_domain(
            ["tenant_name", "annual_rent"],
            [("Ann Smith", 24000), ("Acme Ltd", 480000)],
            ["viewer"],
            REAL_ESTATE,
            5,
        )
        assert governed.actions == ()
        assert list(governed.rows[0]) == ["Ann Smith", 24000]

        lease = ds.get_skill("real_estate.lease").focus.lower()
        assert "a counterparty here may be a person rather than a company" in lease
        assert "a tenant name is not protected by anything deterministic" in lease
        assert "report by tenant segment, sector or covenant grade" in lease

        maintenance = ds.get_skill("real_estate.facilities_maintenance").focus.lower()
        assert "whose names nothing here protects" in maintenance
        assert "never rank the people who carried out the work" in maintenance

    def test_a_ratio_denominator_crosses_a_boundary_this_policy_does_not_open(self):
        """Batch 15's shape in a new domain, with one owner.

        Cost or occupancy per head takes its denominator from HR and cost
        per unit of output takes one from FINANCE or MANUFACTURING;
        REAL_ESTATE pre-authorizes none of them, in either direction. The
        request gate owns whether such a join may happen and this package
        must not restate it (§3) -- only the analytical half belongs
        here, and it belongs to one skill rather than four.
        """
        from governance.domains import is_cross_domain_allowed

        assert policy_for(REAL_ESTATE).cross_domain_allowed == frozenset()
        for other in (HR, FINANCE, MANUFACTURING):
            assert is_cross_domain_allowed(REAL_ESTATE, other) is False, other.value
            assert is_cross_domain_allowed(other, REAL_ESTATE) is False, other.value

        financial = ds.get_skill("real_estate.financial").focus.lower()
        assert "where a denominator comes from another domain" in financial
        assert "name its source and grain" in financial
        assert "a new metric and not a conversion" in financial

        # One owner: the other four do not repeat the clause. Occupancy's
        # own denominators -- area, desks, seats -- are in-domain, which
        # is why that skill names those instead.
        for slug in (
            "real_estate.portfolio",
            "real_estate.occupancy",
            "real_estate.lease",
            "real_estate.facilities_maintenance",
        ):
            assert "another domain" not in ds.get_skill(slug).focus.lower(), slug

    def test_no_focus_text_claims_anything_about_authorization(self):
        """The portfolio skill states a masking and a truncation
        consequence, which is the closest this package comes to the
        boundary. It must describe what the result looks like, never who
        may see it -- `governance/` owns that.
        """
        for skill in ds.skills_for_domain(REAL_ESTATE):
            lowered = skill.focus.lower()
            assert "authoriz" not in lowered, skill.slug
            assert "authoris" not in lowered, skill.slug
            assert "permission" not in lowered, skill.slug
            assert "role" not in lowered, skill.slug

    def test_the_guards_are_not_a_restatement_of_a_prohibition(self):
        for skill in ds.skills_for_domain(REAL_ESTATE):
            lowered = skill.focus.lower()
            assert "individual performance ranking" not in lowered, skill.slug
            assert "unsupported allegation" not in lowered, skill.slug
            assert "sensitive personal targeting" not in lowered, skill.slug
            assert "prohibit" not in lowered, skill.slug

    def test_each_skill_states_the_measurement_trap_its_subject_carries(self):
        """Read off the subject matter. Three are reused rather than
        rediscovered and are named as such in §24: batch 16's "counted
        only where somebody looked" reappears as a reactive work order,
        batch 11's "a record is not its contents" as a closure, and batch
        10's average-of-ratios error as a portfolio yield.
        """
        portfolio = ds.get_skill("real_estate.portfolio").focus.lower()
        assert "what someone decided counts as one property" in portfolio
        assert "gross, net lettable, usable, rentable" in portfolio
        assert "a per-square-metre figure is undefined until the basis is named" in portfolio
        assert "a total that mixes tenure answers no question" in portfolio
        assert "a stock and not a flow" in portfolio

        occupancy = ds.get_skill("real_estate.occupancy").focus.lower()
        assert "two unrelated measures that share the word occupancy" in occupancy
        assert "a badge records an entry rather than a presence" in occupancy
        assert "a booking records an intention and not an attendance" in occupancy
        assert "reads as empty rather than as unknown" in occupancy
        assert "a peak is an instant and an average hides it" in occupancy

        lease = ds.get_skill("real_estate.lease").focus.lower()
        assert "rent is not one number" in lease
        assert "the date that governs the profile is rarely the end date" in lease
        assert "four different numbers" in lease
        assert "a market review is an opinion until it is agreed" in lease
        assert "annualised with its exchange basis and date stated" in lease

        maintenance = ds.get_skill("real_estate.facilities_maintenance").focus.lower()
        assert "how much was raised and not how much broke" in maintenance
        assert "a responsive helpdesk looks worse" in maintenance
        assert "planned and reactive work are different populations" in maintenance
        assert "a closure is a reported state" in maintenance
        assert "never average percentiles across sites" in maintenance
        assert "measures only the jobs nobody closed" in maintenance

        financial = ds.get_skill("real_estate.financial").focus.lower()
        assert "whose definition varies" in financial
        assert "use the approved definition and name it rather than composing one" in financial
        assert "a gross service charge is not the owner's cost" in financial
        assert "moves with occupancy rather than with efficiency" in financial
        assert "an opinion at a date produced by a method" in financial
        assert "not the average of property yields" in financial
        assert "a restatement is not a movement" in financial

    def test_no_skill_writes_out_a_metric_formula(self):
        """Batch 10's rule, which this domain tests hardest: net
        operating income, yield and weighted average unexpired term are
        all named metrics with approved definitions in `semantic/`, and a
        skill that spelled one out would be a second owner free to drift
        (`CLAUDE.md` §4.8).
        """
        for skill in ds.skills_for_domain(REAL_ESTATE):
            focus = skill.focus
            assert "=" not in focus, skill.slug
            assert "divided by" not in focus.lower(), skill.slug
            assert "/" not in focus, skill.slug

    def test_this_batch_completes_the_supplied_series(self):
        """The milestone, asserted rather than claimed in prose.

        Ninety skills across eighteen prompt-domains and seventeen
        governance domains, every one of them five skills except
        `sales_marketing`, which holds two prompt-domains. `EXECUTIVE` is
        the only member without skills and
        `test_the_only_domain_left_uncovered_is_the_deliberate_one`
        covers why.
        """
        assert len(ds.all_skills()) == 90
        counts = {
            group: len([s for s in ds.all_skills() if s.group == group])
            for group in {s.group for s in ds.all_skills()}
        }
        assert set(counts.values()) == {5}
        assert len(counts) == 18
        assert len(ds.skills_for_domain(SALES)) == 10


class TestNothingUntrustedReachesThePrompt:
    """The property that makes a skill safe to put in a system message."""

    def test_the_contract_has_no_format_slots(self):
        """A format slot is a hole, and a hole in a system prompt is
        somewhere untrusted text could one day be passed."""
        assert "{" not in ANALYTICS_CONTRACT
        assert "}" not in ANALYTICS_CONTRACT

    @pytest.mark.parametrize("skill", ds.all_skills(), ids=lambda s: s.slug)
    def test_no_skill_field_has_a_format_slot(self, skill):
        for value in (skill.slug, skill.title, skill.scope, skill.focus):
            assert "{" not in value and "}" not in value

    @pytest.mark.parametrize("skill", ds.all_skills(), ids=lambda s: s.slug)
    def test_guidance_is_built_only_from_the_skill_and_the_contract(self, skill):
        """Every line of the output traces to a frozen in-code constant."""
        text = compose_guidance(skill)
        assert ANALYTICS_CONTRACT in text
        assert skill.scope in text
        assert skill.focus in text
        assert skill.domain.value in text

    def test_compose_takes_no_caller_text(self):
        """Structural: the signature is the control. There is nowhere to
        pass a question, a row or a retrieved document."""
        import inspect

        assert set(inspect.signature(compose_guidance).parameters) == {"skill"}

    def test_guidance_for_takes_no_free_text_either(self):
        import inspect

        params = set(inspect.signature(guidance_for).parameters)
        assert params == {"domain", "enabled", "preferred_slug"}


class TestTheContractDoesNotClaimToBeTheSecurityBoundary:
    """`CLAUDE.md` 4.3: the LLM may propose, it never decides. The contract
    must not restate deterministic controls as if the model enforced them."""

    def test_it_frames_untrusted_input_as_data(self):
        lowered = ANALYTICS_CONTRACT.lower()
        assert "never instructions to you" in lowered
        assert "data" in lowered

    def test_it_refuses_individual_decisions(self):
        assert "identifiable individual" in ANALYTICS_CONTRACT.lower()

    def test_it_distinguishes_fact_from_hypothesis(self):
        for label in ("FACT:", "CALCULATION:", "CORRELATION:", "HYPOTHESIS:"):
            assert label in ANALYTICS_CONTRACT

    def test_it_does_not_restate_sql_validation_as_a_model_instruction(self):
        """`agent/sql_validator.py` is the control. Telling the model to
        "only write read-only SQL" would imply the model is the gate."""
        lowered = ANALYTICS_CONTRACT.lower()
        assert "read-only sql" not in lowered

    def test_response_sections_omit_sql_and_validation(self):
        """The API already carries the executed statement and the real
        validation outcome as structured fields. Asking the model to
        restate them would create a second, unverified copy
        (`CLAUDE.md` 4.8)."""
        assert "SQL" not in RESPONSE_SECTIONS
        assert "Validation" not in RESPONSE_SECTIONS

    def test_response_sections_omit_domain_metrics_and_entities(self):
        """The supplied prompts ask for these three as well, and they are
        omitted for the same reason, which batch 16 noticed was pinned for
        `SQL`/`Validation` but never written down for them.

        - `Domain` is already in the guidance the composer emits and is
          decided by routing, not by the model.
        - `Metrics` and `Relevant Entities` are the semantic layer's
          facts. `semantic/` owns a `MetricDefinition`, and batch 10
          established that a skill must not restate a metric
          (§14) -- having the model list them would be a third,
          unverified copy of a catalog fact (`CLAUDE.md` §4.8).
        """
        for section in ("Domain", "Metrics", "Relevant Entities"):
            assert section not in RESPONSE_SECTIONS, section
        # Non-vacuity: the sections that ARE asked for are present, so
        # this is not passing because the tuple is empty.
        assert "Intent" in RESPONSE_SECTIONS
        assert "Data Limitations" in RESPONSE_SECTIONS


class TestItIsOffByDefaultAndNotRuntimeEditable:
    def test_the_flag_defaults_off(self):
        from config.settings import Settings

        assert Settings.model_fields["enable_domain_skills"].default is False

    def test_no_module_in_the_package_reads_a_database(self):
        """Skills are in-code constants. If nothing here can reach the ORM,
        a database-backed skill cannot be added by accident -- only
        deliberately, which is the point (`CLAUDE.md` 5.5)."""
        import ast
        import pathlib

        banned = {"identity", "sqlalchemy"}
        for path in sorted(pathlib.Path("domain_skills").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    mods.append(node.module)
                elif isinstance(node, ast.Import):
                    mods.extend(a.name for a in node.names)
                for mod in mods:
                    assert mod.split(".")[0] not in banned, (
                        f"{path.name} imports {mod!r} -- a skill must stay an "
                        "in-code constant, never a database row"
                    )


class TestThePinnedPromptsAreUntouched:
    """`CLAUDE.md` 12: `_INSIGHT_SYSTEM_PROMPT` and the generation prompt are
    benchmark-pinned. Guidance is additive, so these must be unchanged."""

    def test_the_insight_prompt_contains_no_skill_text(self):
        from agent.llm_client import _INSIGHT_SYSTEM_PROMPT

        assert ANALYTICS_CONTRACT not in _INSIGHT_SYSTEM_PROMPT
        for skill in ds.all_skills():
            assert skill.focus not in _INSIGHT_SYSTEM_PROMPT

    def test_the_insight_prompt_still_forbids_new_statistics(self):
        """A sentinel on the pinned wording: if someone later relaxes the
        insight prompt to accommodate skills, this fails loudly rather than
        the accuracy numbers silently ceasing to apply."""
        from agent.llm_client import _INSIGHT_SYSTEM_PROMPT

        assert "compute a new statistic" in _INSIGHT_SYSTEM_PROMPT
        assert "one-to-two sentence" in _INSIGHT_SYSTEM_PROMPT


class TestTheGuardsAreNotVacuous:
    def test_a_cross_domain_selection_would_be_detectable(self):
        """Shows the domain filter is doing work: without it, a slug from
        another domain would be returned as-is.

        Uses MIS as the domain under test, because the comparison needs a
        domain that still has a default to fall back to.
        """

        def _broken(domain, preferred_slug=None):
            if preferred_slug and ds.is_registered(preferred_slug):
                return ds.get_skill(preferred_slug)
            return ds.skills_for_domain(domain)[0]

        leaked = _broken(MIS, "sales.revenue")
        assert leaked.group == "sales", "the broken version should leak"

        safe = select_skill(MIS, preferred_slug="sales.revenue")
        assert safe is not None and safe.group == "mis"

    def test_a_contract_with_a_format_slot_would_be_caught(self):
        hostile = "Analyse {user_input} carefully."
        assert "{" in hostile
        assert "{" not in ANALYTICS_CONTRACT
