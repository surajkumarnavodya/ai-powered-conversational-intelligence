"""The declared domain skills.

**This file is the only place a skill is introduced.** Adding one is a few
lines here; there is deliberately no API route, no admin surface and no
settings field that can add or edit a skill at runtime — see
`domain_skills/types.py` on why a skill must stay an in-code constant.

Batches so far: Sales, MIS / Management Reporting, Marketing. Every batch
has arrived with an identical body and an identical "Domain scope" sentence
across its five prompts, differing only in the heading — so each *prompt-
domain* writes its scope once (`_SALES_SCOPE`, `_MIS_SCOPE`,
`_MARKETING_SCOPE`) and each skill carries only its own focus.

**Marketing shares `BusinessDomain.SALES_MARKETING` with Sales** but has a
different analytical scope. A governance domain answers "what may this
question return"; it is not a statement about framing. `DomainSkill.group`
(the slug prefix) is what separates the two, and
`composer.select_skill` refuses to pick a default when a domain holds more
than one group rather than guessing.

The remaining domains are **not** registered yet. A skill is added when its
prompt arrives, not in anticipation of one — an empty domain here would
advertise analysis the platform cannot actually shape.
"""

from __future__ import annotations

from domain_skills.types import DomainSkill
from governance.domains import BusinessDomain

#: The supplied Sales prompts' "Domain scope:" sentence, verbatim. Shared by
#: all five rather than repeated, which is also what keeps them honest: if
#: the scope ever changes it changes for the whole domain at once.
_SALES_SCOPE = (
    "Analyze commercial revenue, customers, regions, products and pricing "
    "using validated business metrics."
)

#: Sales maps to `BusinessDomain.SALES_MARKETING`, which merges Sales and
#: Marketing. That merge predates this package and drives the governance
#: engine's own policy lookup, so it is reused rather than split — a second
#: domain vocabulary would mean two answers to "what may this question
#: return", which is exactly the duplication `CLAUDE.md` §4.8 forbids. The
#: skill slugs keep Sales and Marketing distinguishable where it matters.
_SALES = BusinessDomain.SALES_MARKETING

SALES_SKILLS: tuple[DomainSkill, ...] = (
    DomainSkill(
        slug="sales.revenue",
        title="Revenue Intelligence",
        domain=_SALES,
        scope=_SALES_SCOPE,
        focus=(
            "Revenue performance: totals and growth against prior period or "
            "target, the mix of what drives them, and material movements "
            "worth explaining."
        ),
        dimensions=("period", "region", "product", "customer", "channel"),
    ),
    DomainSkill(
        slug="sales.customer",
        title="Customer Intelligence",
        domain=_SALES,
        scope=_SALES_SCOPE,
        focus=(
            "Customer behaviour in aggregate: acquisition, retention, "
            "concentration and value distribution. Analyse cohorts and "
            "segments, never an identifiable individual."
        ),
        dimensions=("customer segment", "cohort", "tenure", "region", "product"),
    ),
    DomainSkill(
        slug="sales.regional",
        title="Regional Intelligence",
        domain=_SALES,
        scope=_SALES_SCOPE,
        focus=(
            "Geographic performance: how regions, territories and markets "
            "compare, where growth and decline are concentrated, and which "
            "differences are material rather than noise."
        ),
        dimensions=("region", "territory", "country", "period", "product"),
    ),
    DomainSkill(
        slug="sales.product",
        title="Product Intelligence",
        domain=_SALES,
        scope=_SALES_SCOPE,
        focus=(
            "Product and category performance: mix, contribution, growth and "
            "decline, and which products carry or drag the portfolio."
        ),
        dimensions=("product", "category", "period", "region", "channel"),
    ),
    DomainSkill(
        slug="sales.pricing",
        title="Pricing and Discount Intelligence",
        domain=_SALES,
        scope=_SALES_SCOPE,
        focus=(
            "Realised price and discount behaviour: list versus realised "
            "price, discount depth and spread, and the margin effect of "
            "both. Treat an unexplained discount pattern as something to "
            "flag for review, never as proven misconduct."
        ),
        dimensions=("product", "customer segment", "region", "period", "discount band"),
    ),
)

#: The supplied MIS prompts' "Domain scope:" sentence, verbatim. Shared by
#: all five, same as `_SALES_SCOPE` above.
_MIS_SCOPE = (
    "Analyze executive KPIs, targets, variances, trends, anomalies and " "management scorecards."
)

#: `BusinessDomain.EXECUTIVE` also exists and would fit "Executive KPI
#: Intelligence" by name. `MIS` is used instead because that is the domain
#: the batch was supplied under ("MIS / Management Reporting"), and because
#: the choice is governance-neutral: `policy_for(MIS)` and
#: `policy_for(EXECUTIVE)` were compared and are identical -- same effective
#: restricted categories, same (empty) prohibited-action set, same minimum
#: group size. Nothing is under-protected by picking one.
_MIS = BusinessDomain.MIS

MIS_SKILLS: tuple[DomainSkill, ...] = (
    DomainSkill(
        slug="mis.executive_kpi",
        title="Executive KPI Intelligence",
        domain=_MIS,
        scope=_MIS_SCOPE,
        focus=(
            "Headline KPIs for management review: current standing, "
            "direction of travel, and which movements are material enough "
            "to brief rather than merely visible."
        ),
        dimensions=("kpi", "period", "business unit", "region", "owner"),
    ),
    DomainSkill(
        slug="mis.target_vs_actual",
        title="Target vs Actual Intelligence",
        domain=_MIS,
        scope=_MIS_SCOPE,
        focus=(
            "Variance against plan or target: size and direction of the gap, "
            "which line items drive it, and whether a gap is closing or "
            "widening. State the basis of any variance percentage, since a "
            "variance on a near-zero target is arithmetically unstable."
        ),
        dimensions=("kpi", "target", "variance", "period", "business unit"),
    ),
    DomainSkill(
        slug="mis.period_comparison",
        title="Period Comparison Intelligence",
        domain=_MIS,
        scope=_MIS_SCOPE,
        focus=(
            "Like-for-like comparison across periods: period-over-period and "
            "year-over-year movement, and what changed between them. Say so "
            "when a period is partial or the two periods are not comparable "
            "in length, rather than comparing them silently."
        ),
        dimensions=("period", "kpi", "business unit", "region", "product"),
    ),
    DomainSkill(
        slug="mis.anomaly",
        title="MIS Anomaly Intelligence",
        domain=_MIS,
        scope=_MIS_SCOPE,
        focus=(
            "Outliers and breaks in an otherwise stable series: what deviated, "
            "by how much, and against what baseline. An anomaly is something "
            "to investigate, never a proven error or an established cause."
        ),
        dimensions=("kpi", "period", "baseline", "business unit", "deviation"),
    ),
    DomainSkill(
        slug="mis.scorecard",
        title="Management Scorecard Intelligence",
        domain=_MIS,
        scope=_MIS_SCOPE,
        focus=(
            "Balanced view across several KPIs at once: where a scorecard is "
            "green, amber or red, and which measures move together. Report "
            "unit and team performance in aggregate -- this domain restricts "
            "the employee and performance categories, so never rank or "
            "evaluate an identifiable individual."
        ),
        dimensions=("kpi", "business unit", "period", "status", "weight"),
    ),
)

#: The supplied Marketing prompts' "Domain scope:" sentence, verbatim.
#:
#: Note this is a *different* scope from `_SALES_SCOPE` inside the *same*
#: governance domain. Sales and Marketing share `SALES_MARKETING` because
#: that enum answers "what may this question return"; they do not share an
#: analytical scope. `DomainSkill.group` is what keeps them apart — see its
#: docstring for the modelling error batch 3 exposed.
_MARKETING_SCOPE = "Analyze campaigns, channels, offers, segments and lead funnels."

MARKETING_SKILLS: tuple[DomainSkill, ...] = (
    DomainSkill(
        slug="marketing.campaign",
        title="Campaign Intelligence",
        domain=_SALES,
        scope=_MARKETING_SCOPE,
        focus=(
            "Campaign performance: reach, response and return against spend "
            "and against prior campaigns. Attribute an outcome to a campaign "
            "only where the data actually links the two; otherwise say the "
            "two moved together and label it a correlation."
        ),
        dimensions=("campaign", "period", "channel", "segment", "spend"),
    ),
    DomainSkill(
        slug="marketing.channel",
        title="Channel Intelligence",
        domain=_SALES,
        scope=_MARKETING_SCOPE,
        focus=(
            "Channel mix and efficiency: volume, cost and conversion by "
            "channel, and where spend is concentrated versus where it "
            "performs. A channel carrying little volume can look efficient "
            "on a ratio alone — report the base alongside the rate."
        ),
        dimensions=("channel", "campaign", "period", "cost", "conversion"),
    ),
    DomainSkill(
        slug="marketing.offer",
        title="Offer Intelligence",
        domain=_SALES,
        scope=_MARKETING_SCOPE,
        focus=(
            "Offer and promotion response: uptake, incremental volume and "
            "the margin given away to achieve it. Distinguish demand the "
            "offer created from demand it merely discounted, and say when "
            "the data cannot separate the two."
        ),
        dimensions=("offer", "campaign", "segment", "period", "discount"),
    ),
    DomainSkill(
        slug="marketing.segmentation",
        title="Customer Segmentation Intelligence",
        domain=_SALES,
        scope=_MARKETING_SCOPE,
        focus=(
            "How segments differ in size, value and behaviour, and which "
            "differences are material rather than small-sample noise. "
            "Analyse segments and cohorts; never profile or target an "
            "identifiable individual, and never infer a protected "
            "characteristic from behaviour."
        ),
        dimensions=("segment", "cohort", "value band", "channel", "period"),
    ),
    DomainSkill(
        slug="marketing.lead_funnel",
        title="Lead Funnel Intelligence",
        domain=_SALES,
        scope=_MARKETING_SCOPE,
        focus=(
            "Progression and drop-off through the lead funnel: volume at "
            "each stage, conversion between stages, and where leads stall. "
            "State the cohort and the window a conversion rate is measured "
            "over, since a funnel measured on an open cohort understates "
            "later stages."
        ),
        dimensions=("funnel stage", "source", "campaign", "period", "conversion"),
    ),
)

#: Every registered skill, in declaration order. Later batches append their
#: own tuple here.
ALL_SKILLS: tuple[DomainSkill, ...] = SALES_SKILLS + MIS_SKILLS + MARKETING_SKILLS
