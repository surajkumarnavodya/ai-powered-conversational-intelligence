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

from governance.domains import BusinessDomain


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
        """Four prompt-domains, three governance domains: Marketing shares
        `SALES_MARKETING` with Sales. Inventing a governance domain per
        batch would mean two answers to 'what may this question return'."""
        assert len({s.group for s in ds.all_skills()}) == 5
        assert len(ds.covered_domains()) == 4

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
        assert ds.covered_domains() == (SALES, MIS, HR, ECOMMERCE)

    def test_the_other_sixteen_domains_have_no_skills(self):
        """An uncovered domain must stay genuinely empty rather than
        quietly inheriting another domain's framing."""
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

    def test_a_domain_with_no_prohibitions_appends_nothing(self):
        """MIS prohibits nothing, so there is no empty block to read past."""
        from governance.domains import policy_for

        assert policy_for(MIS).prohibited_actions == frozenset()
        result = guidance_for(MIS, enabled=True)
        assert result is not None
        text, _ = result
        assert "Domain constraints" not in text

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
        would be a second owner, free to drift from the policy."""
        for skill in ds.all_skills():
            lowered = skill.focus.lower()
            assert "individual compensation decision" not in lowered
            assert "unsupported allegation" not in lowered


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
