"""The declared domain skills.

**This file is the only place a skill is introduced.** Adding one is a few
lines here; there is deliberately no API route, no admin surface and no
settings field that can add or edit a skill at runtime — see
`domain_skills/types.py` on why a skill must stay an in-code constant.

Batches so far: Sales, MIS / Management Reporting, Marketing, HR,
eCommerce, Training / L&D, Finance & Corporate Functions, BFSI, Supply
Chain / Procurement & Logistics, Manufacturing & Operations, Customer
Service & Support, Project & Portfolio Management, IT Operations &
Service Management, Healthcare & Life Sciences, Energy, Utilities &
Sustainability, Risk, Audit & Controls, Legal, Secretarial & Governance,
Real Estate & Facilities.
Every batch has arrived with an identical
body and an identical "Domain scope" sentence across its five prompts,
differing only in the heading — so each *prompt-domain* writes its scope
once (`_SALES_SCOPE`, `_MIS_SCOPE`, `_MARKETING_SCOPE`) and each skill
carries only its own focus.

**Marketing shares `BusinessDomain.SALES_MARKETING` with Sales** but has a
different analytical scope. A governance domain answers "what may this
question return"; it is not a statement about framing. `DomainSkill.group`
(the slug prefix) is what separates the two, and
`composer.select_skill` refuses to pick a default when a domain holds more
than one group rather than guessing.

With Real Estate & Facilities registered, the only `BusinessDomain` member
left without skills is `EXECUTIVE`, and that one is deliberate rather than
pending: its single supplied prompt, "Executive KPI Intelligence", is filed
under `MIS` for the reason the `_MIS` comment below gives. So the series is
complete at ninety skills across seventeen governance domains, which is the
count `types.py` has described since batch 1. A skill is still added only
when its prompt arrives, never in anticipation of one — an empty domain here
would advertise analysis the platform cannot actually shape.
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

#: The supplied HR prompts' "Domain scope:" sentence, verbatim. Note it is
#: the first batch whose own scope names a control ("with privacy
#: protection") rather than only a subject area.
_HR_SCOPE = (
    "Analyze workforce, attrition, recruitment, performance and workforce "
    "risks with privacy protection."
)

#: HR is the most constrained domain delivered so far: `policy_for(HR)`
#: restricts nine categories and prohibits five actions, including
#: `individual_performance_ranking` -- which the shared contract's "rank top
#: and bottom performers" line would otherwise invite. That constraint is
#: appended automatically by `composer._prohibitions_block`, derived from
#: the policy rather than restated here, so these focus texts do not repeat
#: it and cannot drift from it.
_HR = BusinessDomain.HR

#: Declared in the supplied numeric order (01 workforce, 02 attrition,
#: 03 recruitment, 04 performance, 05 risk scorecard), which is also the
#: order `select_skill` uses for a domain default -- so the default here is
#: Workforce Intelligence, the broadest of the five.
HR_SKILLS: tuple[DomainSkill, ...] = (
    DomainSkill(
        slug="hr.workforce",
        title="Workforce Intelligence",
        domain=_HR,
        scope=_HR_SCOPE,
        focus=(
            "Headcount and workforce composition in aggregate: size, shape, "
            "movement in and out, and how these differ by unit, location and "
            "band. Report groups, never named individuals."
        ),
        dimensions=("business unit", "location", "band", "period", "headcount"),
    ),
    DomainSkill(
        slug="hr.attrition",
        title="Attrition Intelligence",
        domain=_HR,
        scope=_HR_SCOPE,
        focus=(
            "Leaver rates and patterns across groups and periods: where "
            "attrition is concentrated and whether it is rising or falling. "
            "State the denominator and the window, since an attrition rate "
            "on a small or fast-growing group is volatile. A pattern is "
            "something to investigate, never a proven cause and never "
            "grounds for a conclusion about any individual."
        ),
        dimensions=("business unit", "tenure band", "period", "location", "reason"),
    ),
    DomainSkill(
        slug="hr.recruitment",
        title="Recruitment Intelligence",
        domain=_HR,
        scope=_HR_SCOPE,
        focus=(
            "Hiring pipeline and outcomes in aggregate: open roles, time to "
            "fill, offer and acceptance rates, and where the funnel stalls. "
            "Analyse the process, never a candidate -- and never infer a "
            "protected characteristic from a name, a source or an outcome."
        ),
        dimensions=("role", "business unit", "pipeline stage", "source", "period"),
    ),
    DomainSkill(
        slug="hr.performance",
        title="Performance Intelligence",
        domain=_HR,
        scope=_HR_SCOPE,
        focus=(
            "Performance **distribution** across teams and units: how ratings "
            "or scores are spread, how that spread shifts over time, and "
            "where calibration looks inconsistent between groups. This "
            "domain prohibits ranking individuals, so report distributions "
            "and group comparisons; if a question can only be answered by "
            "ranking people, say so instead."
        ),
        dimensions=("business unit", "rating band", "period", "location", "band"),
    ),
    DomainSkill(
        slug="hr.risk_scorecard",
        title="Workforce Risk Scorecard",
        domain=_HR,
        scope=_HR_SCOPE,
        focus=(
            "Workforce risk indicators side by side: attrition pressure, "
            "vacancy exposure, concentration of critical skills and span of "
            "control. A red indicator flags a group to look at, never a "
            "person to act on, and never an allegation."
        ),
        dimensions=("business unit", "risk indicator", "period", "location", "severity"),
    ),
)

#: The supplied eCommerce prompts' "Domain scope:" sentence, verbatim.
_ECOMMERCE_SCOPE = (
    "Analyze digital funnels, carts, cohorts, categories, promotions and " "channels."
)

#: `policy_for(ECOMMERCE)` prohibits `sensitive_personal_targeting` and
#: restricts the customer, payment and banking categories. That constraint
#: is appended automatically by `composer._prohibitions_block`, so the
#: focus texts below stay analytical rather than restating it -- but it is
#: why the cohort and promotion skills are framed around groups and
#: behaviour rather than around reaching particular people.
_ECOMMERCE = BusinessDomain.ECOMMERCE

#: Declared in the supplied numeric order (01 funnel, 02 cart, 03 cohort,
#: 04 category, 05 promotion/channel), so the domain default is Conversion
#: Funnel Intelligence (01).
ECOMMERCE_SKILLS: tuple[DomainSkill, ...] = (
    DomainSkill(
        slug="ecommerce.conversion_funnel",
        title="Conversion Funnel Intelligence",
        domain=_ECOMMERCE,
        scope=_ECOMMERCE_SCOPE,
        focus=(
            "Progression and drop-off through the digital funnel: sessions, "
            "product views, add-to-cart, checkout and order. Give each "
            "step's base alongside its rate, and state the window a "
            "conversion is measured over -- a session-based rate and an "
            "order-based rate answer different questions and are not "
            "comparable."
        ),
        dimensions=("funnel step", "device", "channel", "period", "category"),
    ),
    DomainSkill(
        slug="ecommerce.cart_abandonment",
        title="Cart Abandonment Intelligence",
        domain=_ECOMMERCE,
        scope=_ECOMMERCE_SCOPE,
        focus=(
            "Where and how carts are abandoned: rate by step, device, "
            "channel and basket value, and what distinguishes an abandoned "
            "cart from a completed one. Abandonment is a correlation with "
            "whatever else is present, not a demonstrated cause -- a cart "
            "left open is not evidence of intent."
        ),
        dimensions=("checkout step", "device", "basket value band", "channel", "period"),
    ),
    DomainSkill(
        slug="ecommerce.customer_cohort",
        title="Customer Cohort Intelligence",
        domain=_ECOMMERCE,
        scope=_ECOMMERCE_SCOPE,
        focus=(
            "How cohorts behave over time: repeat rate, order frequency and "
            "value by acquisition period or first category. Report cohorts "
            "as groups; a cohort too small to be meaningful should be "
            "reported as such rather than analysed as if it were."
        ),
        dimensions=("acquisition cohort", "period", "channel", "category", "repeat rate"),
    ),
    DomainSkill(
        slug="ecommerce.category",
        title="Category Intelligence",
        domain=_ECOMMERCE,
        scope=_ECOMMERCE_SCOPE,
        focus=(
            "Category and catalogue performance: demand, conversion, "
            "returns and contribution by category, and how the mix shifts. "
            "A category's rate is only interpretable against its base, so "
            "report both -- a niche category can top a conversion ranking "
            "on a handful of orders."
        ),
        dimensions=("category", "subcategory", "period", "channel", "return rate"),
    ),
    DomainSkill(
        slug="ecommerce.promotion_channel",
        title="Promotion and Channel Intelligence",
        domain=_ECOMMERCE,
        scope=_ECOMMERCE_SCOPE,
        focus=(
            "Promotion response and channel contribution: uplift, discount "
            "cost and channel mix. Separate demand a promotion created from "
            "demand it merely discounted, and say when the data cannot do "
            "so. Attribute to a channel only where the data actually links "
            "the two; otherwise report co-movement and label it."
        ),
        dimensions=("promotion", "channel", "category", "period", "discount"),
    ),
)

#: The supplied Training / L&D prompts' "Domain scope:" sentence, verbatim.
_TRAINING_SCOPE = "Analyze training completion, skills, impact, engagement and " "certifications."

#: `policy_for(TRAINING)` prohibits `individual_employment_action` and
#: `individual_performance_ranking`, and restricts the employee,
#: performance and credential categories. Both prohibitions are appended
#: automatically by `composer._prohibitions_block`, so the focus texts
#: below stay analytical -- but it is why every one of them is framed
#: around cohorts, teams and courses rather than around learners.
_TRAINING = BusinessDomain.TRAINING

#: Declared in the supplied numeric order (01 completion, 02 skill gap,
#: 03 impact, 04 engagement, 05 certification), so the domain default is
#: Training Completion Intelligence (01).
TRAINING_SKILLS: tuple[DomainSkill, ...] = (
    DomainSkill(
        slug="training.completion",
        title="Training Completion Intelligence",
        domain=_TRAINING,
        scope=_TRAINING_SCOPE,
        focus=(
            "Completion and progress in aggregate: rates by course, team "
            "and period, and where mandatory training is behind. Give the "
            "enrolled base alongside the rate, and state whether a course "
            "assigned mid-period is counted -- a completion rate over an "
            "open enrolment window understates itself."
        ),
        dimensions=("course", "business unit", "period", "mandatory flag", "cohort"),
    ),
    DomainSkill(
        slug="training.skill_gap",
        title="Skill Gap Intelligence",
        domain=_TRAINING,
        scope=_TRAINING_SCOPE,
        focus=(
            "Gaps between required and held skills at team and role level: "
            "which capabilities are thin, where coverage is concentrated in "
            "few people, and how that has moved. A gap describes a team's "
            "coverage, never an individual's shortfall."
        ),
        dimensions=("skill", "role", "business unit", "proficiency level", "period"),
    ),
    DomainSkill(
        slug="training.impact",
        title="Training Impact Intelligence",
        domain=_TRAINING,
        scope=_TRAINING_SCOPE,
        focus=(
            "Whether trained groups differ from untrained ones on measured "
            "outcomes. This is the skill most likely to be misread: people "
            "choose training, so trained and untrained groups differ before "
            "any training happens. Report the comparison and label it a "
            "correlation; claim impact only where the data shows a genuine "
            "before-and-after on a comparable group, and say so when it "
            "does not."
        ),
        dimensions=("course", "cohort", "outcome metric", "period", "business unit"),
    ),
    DomainSkill(
        slug="training.engagement",
        title="Training Engagement Intelligence",
        domain=_TRAINING,
        scope=_TRAINING_SCOPE,
        focus=(
            "How learners interact with content in aggregate: starts, "
            "drop-off point, time spent and repeat attempts by course and "
            "team. Low engagement with a course is a signal about the "
            "course, not a judgement about the people who took it."
        ),
        dimensions=("course", "module", "business unit", "period", "drop-off point"),
    ),
    DomainSkill(
        slug="training.certification",
        title="Certification Intelligence",
        domain=_TRAINING,
        scope=_TRAINING_SCOPE,
        focus=(
            "Certification coverage and currency across teams: how many "
            "hold a certification, how many lapse and when, and where "
            "expiry clusters. Report counts and coverage by group; an "
            "expiring certification is a scheduling exposure for the team, "
            "not a finding about the holder."
        ),
        dimensions=("certification", "business unit", "expiry window", "role", "period"),
    ),
)

#: The supplied Finance prompts' "Domain scope:" sentence, verbatim.
_FINANCE_SCOPE = "Analyze financial performance, budgets, costs, AR/AP, risk and " "corporate KPIs."

#: `policy_for(FINANCE)` prohibits exactly one action,
#: `individual_compensation_decision`, and restricts the financial,
#: payment and banking categories. Notably it does **not** prohibit
#: `unsupported_allegation` -- so the accusation risk that AR/AP analysis
#: carries (a slow payer reported as though it were misconduct) is handled
#: in that skill's own focus text rather than by the derived constraint
#: block. That is the line between the two: a policy-backed prohibition is
#: derived and never restated, while an analytical risk the policy does not
#: cover belongs to the skill.
_FINANCE = BusinessDomain.FINANCE

#: Declared in the supplied numeric order (01 performance, 02 budget/cost,
#: 03 AR/AP, 04 risk/cost, 05 corporate KPI), so the domain default is
#: Financial Performance Intelligence (01).
FINANCE_SKILLS: tuple[DomainSkill, ...] = (
    DomainSkill(
        slug="finance.performance",
        title="Financial Performance Intelligence",
        domain=_FINANCE,
        scope=_FINANCE_SCOPE,
        focus=(
            "Revenue, cost and margin performance against prior period and "
            "plan: what moved, by how much, and which lines drive it. State "
            "the basis -- which period close, and whether figures are "
            "actual, accrued or forecast -- because a part-closed period "
            "compared against a closed one shows a decline that is an "
            "artefact of timing."
        ),
        dimensions=("period", "entity", "cost centre", "account", "currency"),
    ),
    DomainSkill(
        slug="finance.budget_cost",
        title="Budget and Cost Intelligence",
        domain=_FINANCE,
        scope=_FINANCE_SCOPE,
        focus=(
            "Spend against budget and the shape of cost: variance by cost "
            "centre and category, run-rate against remaining budget, and "
            "where cost is concentrated. Say how the budget is phased -- an "
            "evenly phased budget compared against lumpy actuals produces a "
            "variance that reverses next period -- and state the basis of a "
            "variance percentage, since one on a near-zero budget is "
            "arithmetically unstable."
        ),
        dimensions=("cost centre", "category", "period", "budget line", "entity"),
    ),
    DomainSkill(
        slug="finance.ar_ap",
        title="AR AP Intelligence",
        domain=_FINANCE,
        scope=_FINANCE_SCOPE,
        focus=(
            "Receivables and payables position: ageing profile, days "
            "outstanding, and where balance is concentrated by counterparty "
            "or period. Give the ageing bucket boundaries, since the same "
            "ledger looks very different on 30/60/90 than on 45/90. "
            "A late or disputed balance is a collections and process "
            "matter: report it as an exposure to follow up, never as "
            "evidence of bad faith, misconduct or fraud by the counterparty."
        ),
        dimensions=("counterparty", "ageing bucket", "period", "entity", "currency"),
    ),
    DomainSkill(
        slug="finance.risk_cost",
        title="Enterprise Risk and Cost Intelligence",
        domain=_FINANCE,
        scope=_FINANCE_SCOPE,
        focus=(
            "Financial exposure and its concentration: where risk or cost "
            "clusters by counterparty, currency, entity or contract, and "
            "how that has moved. A risk indicator describes exposure, not a "
            "loss that has happened and not one that will -- report it as "
            "something to size and monitor."
        ),
        dimensions=("risk category", "entity", "counterparty", "period", "exposure"),
    ),
    DomainSkill(
        slug="finance.corporate_kpi",
        title="Corporate KPI Intelligence",
        domain=_FINANCE,
        scope=_FINANCE_SCOPE,
        focus=(
            "Corporate financial KPIs side by side: standing, trend and "
            "movement against target across entities and functions. State "
            "the consolidation basis and the currency treatment, because a "
            "group figure built on different translation rates is not "
            "comparable period to period."
        ),
        dimensions=("kpi", "entity", "period", "currency", "function"),
    ),
)

#: The supplied BFSI prompts' "Domain scope:" sentence, verbatim. It is the
#: only scope so far that names its own control regime ("with strict
#: financial-data controls").
_BFSI_SCOPE = (
    "Analyze banking, lending, insurance, fraud/risk and collections with "
    "strict financial-data controls."
)

#: The sharpest collision between supplied skills and domain policy so far.
#: `policy_for(BFSI)` prohibits `credit_or_loan_decision`,
#: `fraud_accusation`, `individualized_financial_advice` and
#: `unsupported_allegation` -- and three of the five supplied skills are
#: *named after* one of those actions. "Loan and Credit Intelligence" sounds
#: like it should decide whether to lend; "Fraud and Risk Intelligence"
#: sounds like it should identify fraudsters.
#:
#: The prohibitions themselves are derived by
#: `composer._prohibitions_block` and are never restated below. What the
#: focus texts do instead is redirect each skill to the *portfolio* version
#: of its subject, which is both the legitimate analytical question and the
#: one the data can actually answer.
_BFSI = BusinessDomain.BFSI

#: Declared in the supplied numeric order (01 banking customer, 02 loan and
#: credit, 03 insurance, 04 fraud and risk, 05 collections), so the domain
#: default is Banking Customer Intelligence (01).
BFSI_SKILLS: tuple[DomainSkill, ...] = (
    DomainSkill(
        slug="bfsi.banking_customer",
        title="Banking Customer Intelligence",
        domain=_BFSI,
        scope=_BFSI_SCOPE,
        focus=(
            "Portfolio view of customers and product holding: segment mix, "
            "balances, product penetration and attrition across the book. "
            "Describe how segments behave; never produce guidance addressed "
            "to a customer about what they should do with their money."
        ),
        dimensions=("segment", "product", "tenure band", "channel", "period"),
    ),
    DomainSkill(
        slug="bfsi.loan_credit",
        title="Loan and Credit Intelligence",
        domain=_BFSI,
        scope=_BFSI_SCOPE,
        focus=(
            "Credit portfolio quality in aggregate: approval and decline "
            "rates, exposure by grade or product, and vintage performance "
            "as books season. Vintages must be compared at the same months "
            "on book -- a young vintage always looks better than a mature "
            "one. This describes how a portfolio is performing; it never "
            "assesses whether any applicant or borrower should be lent to."
        ),
        dimensions=("product", "risk grade", "vintage", "channel", "period"),
    ),
    DomainSkill(
        slug="bfsi.insurance",
        title="Insurance Intelligence",
        domain=_BFSI,
        scope=_BFSI_SCOPE,
        focus=(
            "Policy and claims portfolio: loss ratio, claims frequency and "
            "severity, persistency and lapse. State whether claims are "
            "reported or incurred and whether development is complete -- a "
            "recent period's loss ratio is understated until claims mature. "
            "This describes the book, not the merits of any single claim."
        ),
        dimensions=("product line", "cohort", "claim status", "period", "channel"),
    ),
    DomainSkill(
        slug="bfsi.fraud_risk",
        title="Fraud and Risk Intelligence",
        domain=_BFSI,
        scope=_BFSI_SCOPE,
        focus=(
            "How detection and risk controls are performing: alert volumes, "
            "hit and false-positive rates, case ageing and where exposure "
            "concentrates. A rule firing is a control output, not a finding: "
            "report alerts and confirmed-case counts as separate measures, "
            "and never describe anyone as having committed fraud. An "
            "unusual pattern is a reason to investigate, nothing more."
        ),
        dimensions=("alert type", "channel", "case status", "period", "exposure"),
    ),
    DomainSkill(
        slug="bfsi.collections",
        title="Collections and Delinquency Intelligence",
        domain=_BFSI,
        scope=_BFSI_SCOPE,
        focus=(
            "Delinquency and recovery across the book: bucket distribution, "
            "roll rates between buckets, cure and recovery rates, and where "
            "arrears concentrate. Give the bucket definitions, since roll "
            "rates are meaningless without them. Delinquency is a portfolio "
            "state to be worked, never a judgement about a borrower's "
            "character or intent."
        ),
        dimensions=("delinquency bucket", "product", "vintage", "period", "channel"),
    ),
)

#: The supplied Supply Chain prompts' "Domain scope:" sentence, verbatim.
_SUPPLY_CHAIN_SCOPE = (
    "Analyze supply, inventory, procurement, suppliers, shipments, logistics and delivery."
)

#: The mirror image of batch 8. `policy_for(SUPPLY_CHAIN)` prohibits
#: **nothing** -- it declares restricted categories (contract, banking and
#: proprietary, plus the unavoidable baseline) but not one
#: `ProhibitedAction`. So `composer._prohibitions_block` returns "" and no
#: constraint block is appended at all, which means the shared contract's
#: invitation to "rank top and bottom performers" stands unqualified here.
#:
#: MIS is the only other zero-prohibition domain, and it got away with it
#: because management reporting is inherently aggregate. Supply chain is
#: not: supplier and carrier measures name counterparties, and delivery
#: records frequently identify an individual driver or courier. The two
#: risks that creates are therefore carried by the skills' own focus texts
#: -- the `finance.ar_ap` precedent -- because there is no policy-backed
#: prohibition for the composer to derive.
#:
#: Neither focus text is the control. Driver-level performance is
#: `DataCategory.PERFORMANCE`, which `BASELINE_RESTRICTED_CATEGORIES`
#: restricts in every domain, so `governance/result_policy.py` is what
#: actually withholds it. The focus text only stops the model being
#: invited to ask for it.
_SUPPLY_CHAIN = BusinessDomain.SUPPLY_CHAIN

#: Declared in the supplied numeric order (01 chain performance, 02
#: inventory, 03 logistics and delivery, 04 supplier and spend, 05
#: procurement cycle), so the domain default is Supply Chain Performance
#: Intelligence (01) -- the broadest of the five.
SUPPLY_CHAIN_SKILLS: tuple[DomainSkill, ...] = (
    DomainSkill(
        slug="supply_chain.performance",
        title="Supply Chain Performance Intelligence",
        domain=_SUPPLY_CHAIN,
        scope=_SUPPLY_CHAIN_SCOPE,
        focus=(
            "End-to-end service and cost across the chain: fill rate, "
            "on-time-in-full, total lead time and cost to serve, and which "
            "stage the delay actually sits in. A chain-level number hides "
            "its own cause, so attribute a movement to a stage or say "
            "plainly that it cannot be attributed."
        ),
        dimensions=("stage", "site", "product category", "supplier", "period"),
    ),
    DomainSkill(
        slug="supply_chain.inventory",
        title="Inventory Intelligence",
        domain=_SUPPLY_CHAIN,
        scope=_SUPPLY_CHAIN_SCOPE,
        focus=(
            "Stock health: cover and days of supply, turns, stock-outs, "
            "excess and ageing. Inventory is a point-in-time snapshot, not "
            "a flow -- it must never be summed across periods, and an "
            "average has to name the snapshot dates it averages. Give the "
            "cost basis behind any turns figure."
        ),
        dimensions=("sku", "location", "product category", "ageing band", "period"),
    ),
    DomainSkill(
        slug="supply_chain.logistics",
        title="Logistics and Delivery Intelligence",
        domain=_SUPPLY_CHAIN,
        scope=_SUPPLY_CHAIN_SCOPE,
        focus=(
            "Shipment and delivery performance: on-time rate, transit time, "
            "exceptions, and carrier and lane performance. Name which "
            "promise date on-time is measured against, since a revised date "
            "flatters the number against the original one. Delivery records "
            "frequently identify a driver or courier: report carrier, lane "
            "and route performance, never an individual's."
        ),
        dimensions=("carrier", "lane", "service level", "destination region", "period"),
    ),
    DomainSkill(
        slug="supply_chain.supplier_spend",
        title="Supplier and Spend Intelligence",
        domain=_SUPPLY_CHAIN,
        scope=_SUPPLY_CHAIN_SCOPE,
        focus=(
            "Spend and supplier performance in aggregate: spend by category "
            "and by supplier, concentration and tail spend, and delivery and "
            "quality performance per supplier. State one currency basis and "
            "one document basis -- purchase order, invoice or payment -- "
            "because mixing them double-counts. Weak measured performance "
            "is a performance fact, never evidence of bad faith or "
            "misconduct by a supplier."
        ),
        dimensions=("supplier", "spend category", "contract status", "region", "period"),
    ),
    DomainSkill(
        slug="supply_chain.procurement_cycle",
        title="Procurement Cycle Intelligence",
        domain=_SUPPLY_CHAIN,
        scope=_SUPPLY_CHAIN_SCOPE,
        focus=(
            "The requisition-to-payment cycle: stage durations, approval "
            "lead time, where requests queue, and off-contract buying. Only "
            "a completed cycle has a measurable duration, so a mean over "
            "closed requisitions alone is biased low while the slowest are "
            "still open -- give the inclusion rule and how many remain in "
            "flight."
        ),
        dimensions=("stage", "buyer group", "spend category", "approval tier", "period"),
    ),
)

#: The supplied Manufacturing prompts' "Domain scope:" sentence, verbatim.
_MANUFACTURING_SCOPE = "Analyze production, OEE, downtime, quality and capacity."

#: The third zero-prohibition domain, after MIS and SUPPLY_CHAIN -- so the
#: batch-9 pattern applies again and is reused here rather than
#: rediscovered: `policy_for(MANUFACTURING)` declares one restricted
#: category (proprietary, plus the unavoidable baseline) and not one
#: `ProhibitedAction`, so no constraint block is appended and the shared
#: contract's ranking invitation is never narrowed for this domain.
#:
#: Downtime and defects are routinely recorded against a shift and an
#: operator, which makes `individual_performance_ranking` -- prohibited in
#: HR, not here -- the live risk, exactly as driver data was at batch 9.
#: The downtime and quality focus texts therefore attribute to equipment,
#: reason, process and material, never to the person who ran the machine.
#: `governance/result_policy.py` is still the control; the focus text only
#: stops the model being invited to ask.
#:
#: What *is* new at batch 10 is the other boundary this batch runs into:
#: `semantic.metrics.MetricDefinition` owns a metric's `definition`,
#: `formula`, `aggregation` (its docstring names "derived ratio") and
#: `grain`, under a governance `status`. So a skill must never define OEE.
#: Writing the formula into a focus text would create a second owner for a
#: governed fact (`CLAUDE.md` §4.8) -- an ungoverned, unversioned copy that
#: would contradict an approved definition wherever a plant counts
#: planned downtime differently. The skill's job is to insist the approved
#: definition and its denominator are named, and that a ratio is recomposed
#: from component totals instead of averaged.
_MANUFACTURING = BusinessDomain.MANUFACTURING

#: Declared in the supplied numeric order (01 production, 02 OEE, 03
#: downtime, 04 quality, 05 capacity) even though the prompts arrived
#: 01, 03, 04, 05, 02 -- declaration order decides the domain default, so
#: it is Production Intelligence (01) rather than whichever file was listed
#: first.
MANUFACTURING_SKILLS: tuple[DomainSkill, ...] = (
    DomainSkill(
        slug="manufacturing.production",
        title="Production Intelligence",
        domain=_MANUFACTURING,
        scope=_MANUFACTURING_SCOPE,
        focus=(
            "Output and schedule attainment: volume against plan, product "
            "mix and yield across lines and plants. State whether a volume "
            "is gross or good units -- counting reworked or scrapped pieces "
            "as output overstates every ratio built on top of it. Give the "
            "plan version a shortfall is measured against."
        ),
        dimensions=("plant", "line", "product", "shift pattern", "period"),
    ),
    DomainSkill(
        slug="manufacturing.oee",
        title="OEE Intelligence",
        domain=_MANUFACTURING,
        scope=_MANUFACTURING_SCOPE,
        focus=(
            "Overall equipment effectiveness and its availability, "
            "performance and quality components. Use the approved metric "
            "definition rather than assuming one: whether planned downtime, "
            "changeovers and no-demand time sit inside the denominator is a "
            "governed choice that changes the number. It is a ratio, so it "
            "has to be recomposed from component totals -- averaging it "
            "across lines, shifts or periods is arithmetically wrong. "
            "Report the components next to it, because one figure cannot "
            "say which of them moved."
        ),
        dimensions=("equipment", "line", "oee component", "shift pattern", "period"),
    ),
    DomainSkill(
        slug="manufacturing.downtime",
        title="Downtime Intelligence",
        domain=_MANUFACTURING,
        scope=_MANUFACTURING_SCOPE,
        focus=(
            "Downtime and where it concentrates: event count, duration, "
            "mean time between failures and to repair, and which reasons "
            "dominate. Rank by duration and by frequency separately, since "
            "the leading reason by one is routinely not the leading reason "
            "by the other. Overlapping events on parallel equipment must "
            "not be summed into more downtime than the window contains. "
            "Attribute downtime to equipment, reason and shift pattern, "
            "never to a named operator."
        ),
        dimensions=("equipment", "downtime reason", "line", "shift pattern", "period"),
    ),
    DomainSkill(
        slug="manufacturing.quality",
        title="Quality Intelligence",
        domain=_MANUFACTURING,
        scope=_MANUFACTURING_SCOPE,
        focus=(
            "Defects, scrap and rework: defect rate, first-pass yield, "
            "scrap cost and which defect modes dominate. Give the "
            "denominator -- units produced, inspected and shipped yield "
            "three different rates, and only inspected units support a "
            "detection rate. First-pass yield counts a reworked unit as a "
            "failure while final yield does not, so name which is reported. "
            "A defect belongs to a process, material or machine, never to "
            "the person who ran it."
        ),
        dimensions=("defect mode", "product", "process step", "line", "period"),
    ),
    DomainSkill(
        slug="manufacturing.capacity",
        title="Capacity Intelligence",
        domain=_MANUFACTURING,
        scope=_MANUFACTURING_SCOPE,
        focus=(
            "Capacity, utilisation and the constraint: demonstrated against "
            "nameplate capacity, utilisation by work centre, and where the "
            "bottleneck sits. A line's capacity is its constraint's "
            "capacity, so an average utilisation across work centres hides "
            "the one work centre that governs output. Say whether a "
            "capacity figure is theoretical, demonstrated or scheduled, "
            "because the three rarely agree."
        ),
        dimensions=("work centre", "plant", "product family", "constraint", "period"),
    ),
)

#: The supplied Customer Service prompts' "Domain scope:" sentence, verbatim.
_CUSTOMER_SERVICE_SCOPE = (
    "Analyze tickets, SLAs, complaints, contact centers and service/retention."
)

#: The fourth zero-prohibition domain, and the one where that matters most.
#: `policy_for(CUSTOMER_SERVICE)` declares no `ProhibitedAction`, so no
#: constraint block is appended and the ranking invitation is never
#: narrowed -- while an entire supplied skill, Contact Center Intelligence,
#: is *about* per-agent productivity measures. This is the sharpest form of
#: the risk batch 9 first met with delivery drivers.
#:
#: Unlike SUPPLY_CHAIN and MANUFACTURING, this domain does engage the
#: result gate: `minimum_group_size=5` plus PII, SPI and customer
#: restrictions. Be precise about what that covers.
#: `governance.suppression` suppresses a *sensitive measure* for a group
#: below five and keeps the label and count visible. It therefore does
#: nothing about ranking twenty agents who each handled four hundred
#: contacts: that is not a small group, and an authorised caller sees the
#: measure. The deterministic controls bound disclosure; whether a
#: per-person ranking is a sound answer at all is analysis framing, which
#: is what the focus texts below address.
#:
#: The genuinely new problem here is provenance. A ticket or complaint
#: record is a *third party's claim*, not a measurement: the
#: `DATABASE_FACT` is that a complaint was logged in a category, never
#: that its content is correct (`agent.provenance.DataTruthLevel`,
#: `CLAUDE.md` §4.4). Batch 8's "a rule firing is a control output, not a
#: finding" is the precedent, but there the signal was machine-generated.
#: Here a person made it, and often about another person -- so a complaint
#: restated as fact is simultaneously an unsupported allegation and a
#: claim about an identifiable individual, in a domain whose policy
#: prohibits neither.
_CUSTOMER_SERVICE = BusinessDomain.CUSTOMER_SERVICE

#: Declared in the supplied numeric order (01 support, 02 SLA, 03
#: complaint, 04 contact centre, 05 service and retention), so the domain
#: default is Customer Support Intelligence (01).
CUSTOMER_SERVICE_SKILLS: tuple[DomainSkill, ...] = (
    DomainSkill(
        slug="customer_service.support",
        title="Customer Support Intelligence",
        domain=_CUSTOMER_SERVICE,
        scope=_CUSTOMER_SERVICE_SCOPE,
        focus=(
            "Demand and resolution across queues: ticket volume, backlog, "
            "first-contact resolution, reopen rate and satisfaction by "
            "queue and channel. Count a ticket once -- reopens, merges and "
            "splits make volume and resolution rate disagree unless the "
            "counting rule is stated. Resolution time taken from closed "
            "tickets alone leaves out the hardest cases, which are still "
            "open."
        ),
        dimensions=("queue", "channel", "priority", "ticket category", "period"),
    ),
    DomainSkill(
        slug="customer_service.sla",
        title="SLA Intelligence",
        domain=_CUSTOMER_SERVICE,
        scope=_CUSTOMER_SERVICE_SCOPE,
        focus=(
            "Attainment against commitment: met and breached volumes, "
            "attainment rate, and where breaches concentrate. Say whether "
            "the clock runs on calendar or business hours and whether time "
            "waiting on the customer is excluded, because the same tickets "
            "pass or breach on that choice alone. Measure against the "
            "target in force when the ticket was raised, and count "
            "in-flight tickets already past target as breaches rather than "
            "omitting them."
        ),
        dimensions=("sla target", "priority", "queue", "breach reason", "period"),
    ),
    DomainSkill(
        slug="customer_service.complaint",
        title="Complaint Intelligence",
        domain=_CUSTOMER_SERVICE,
        scope=_CUSTOMER_SERVICE_SCOPE,
        focus=(
            "Complaint volume, category mix, concentration and time to "
            "resolve. A complaint record is evidence that a complaint was "
            "made, not that its content is correct: report categories and "
            "counts as logged, and never restate what a complaint alleges "
            "as an established finding against a product, a team or a "
            "named employee. Report an upheld or rejected outcome only "
            "where the data records one."
        ),
        dimensions=(
            "complaint category",
            "product",
            "channel",
            "resolution outcome",
            "period",
        ),
    ),
    DomainSkill(
        slug="customer_service.contact_center",
        title="Contact Center Intelligence",
        domain=_CUSTOMER_SERVICE,
        scope=_CUSTOMER_SERVICE_SCOPE,
        focus=(
            "Contact centre operations: offered and handled volume, service "
            "level, abandonment, average handle time, occupancy and "
            "forecast accuracy by interval. Report queue, interval, channel "
            "and team rather than individual agents -- handle time tracks "
            "the mix of contacts someone received, so ranking people on it "
            "penalises whoever took the hard work. Name the denominator "
            "behind occupancy and utilisation."
        ),
        dimensions=("queue", "interval", "channel", "team", "period"),
    ),
    DomainSkill(
        slug="customer_service.service_retention",
        title="Service and Retention Intelligence",
        domain=_CUSTOMER_SERVICE,
        scope=_CUSTOMER_SERVICE_SCOPE,
        focus=(
            "How service experience relates to retention: churn and renewal "
            "by service history, repeat-contact rate, and the revenue at "
            "risk in affected accounts. Service quality and churn moving "
            "together is a correlation whose direction this data cannot "
            "establish -- unhappy customers contact support, which is not "
            "the same as support contact causing them to leave. State the "
            "population and the window a churn rate is measured over."
        ),
        dimensions=(
            "segment",
            "service experience band",
            "tenure band",
            "product",
            "period",
        ),
    ),
)

#: The supplied Project prompts' "Domain scope:" sentence, verbatim.
_PROJECT_SCOPE = (
    "Analyze project portfolios, schedules, finances, resources, risks and dependencies."
)

#: The fifth zero-prohibition domain. `policy_for(PROJECT)` declares no
#: `ProhibitedAction`, so no derived constraint block is appended -- while
#: it does restrict contract, employee and PII data.
#:
#: This comment used to add "and `minimum_group_size=0`, so small-group
#: suppression does not apply either". Batch 17 measured that and it is
#: wrong: `minimum_group_size_for` treats a declared `0` as *unset* and
#: falls back to `GOVERNANCE_MIN_GROUP_SIZE`, which defaults to 5. What
#: limits suppression here is the classification threshold, not the
#: declared zero -- it only reaches a measure at `SENSITIVE` or above. Resource allocation is
#: per-person by construction, so the individual-ranking guard batch 9
#: introduced applies again, now for the fifth time, and is reused rather
#: than restated.
#:
#: What distinguishes this batch is the *kind* of number it analyses.
#: Batch 11 dealt with a record of someone else's claim; here the central
#: measures are **self-assessments by the party being measured** --
#: percent-complete and estimate-to-complete are entered by whoever owns
#: the task, and a risk score is a forward-looking judgement entered by
#: whoever owns the risk. Neither is an observation, so neither is a
#: `DATABASE_FACT` about the world: the fact is that a figure was reported
#: (`agent.provenance.DataTruthLevel`, `CLAUDE.md` §4.4).
#:
#: Earned value follows batch 10's rule unchanged: the skill points at the
#: approved definition instead of composing SPI or CPI itself, because
#: `semantic.metrics.MetricDefinition` owns a metric's formula.
_PROJECT = BusinessDomain.PROJECT

#: Declared in the supplied numeric order (01 portfolio, 02 schedule, 03
#: project financials, 04 resource capacity, 05 risk and dependency), so
#: the domain default is Portfolio Intelligence (01) -- the broadest.
PROJECT_SKILLS: tuple[DomainSkill, ...] = (
    DomainSkill(
        slug="project.portfolio",
        title="Portfolio Intelligence",
        domain=_PROJECT,
        scope=_PROJECT_SCOPE,
        focus=(
            "Portfolio shape and delivery: project count and value by "
            "stage, benefit against plan, and where spend and slippage "
            "concentrate. A portfolio's status is not the average of its "
            "projects' -- roll status up by a stated rule, and report how "
            "many projects are behind alongside their value, since one "
            "large project outweighs many small ones. Percent-complete is "
            "a self-assessment by whoever owns the task, so report it as "
            "reported progress rather than as measured delivery."
        ),
        dimensions=("portfolio", "stage", "business unit", "investment type", "period"),
    ),
    DomainSkill(
        slug="project.schedule",
        title="Schedule Intelligence",
        domain=_PROJECT,
        scope=_PROJECT_SCOPE,
        focus=(
            "Schedule position: milestone attainment, slippage and float "
            "across the plan. Name the baseline a variance is measured "
            "against, because re-baselining resets variance to zero "
            "without the finish date moving. Float belongs to a path "
            "rather than a task, so slack must not be summed, and a late "
            "task off the critical path need not delay anything at all."
        ),
        dimensions=("project", "milestone", "baseline version", "phase", "period"),
    ),
    DomainSkill(
        slug="project.financial",
        title="Project Financial Intelligence",
        domain=_PROJECT,
        scope=_PROJECT_SCOPE,
        focus=(
            "Project cost and benefit: spend against budget, committed "
            "against actual cost, forecast to complete, and capitalised "
            "against operating spend. Use the approved earned-value "
            "definitions rather than composing them here. Spend to date "
            "without commitments understates exposure, because a raised "
            "purchase order is money already spoken for."
        ),
        dimensions=("project", "cost type", "funding source", "capitalisation class", "period"),
    ),
    DomainSkill(
        slug="project.resource_capacity",
        title="Resource Capacity Intelligence",
        domain=_PROJECT,
        scope=_PROJECT_SCOPE,
        focus=(
            "Demand and supply of effort: allocation against capacity, "
            "bench and over-allocation by role and skill, and forecast "
            "demand. Allocation across projects is a many-to-many, so "
            "summing it counts the same person once per project -- "
            "reconcile to a person's own total capacity before concluding "
            "anything about it. Report role, skill and team, and never an "
            "individual's utilisation as a measure of how productive they "
            "are."
        ),
        dimensions=("role", "skill", "team", "project", "period"),
    ),
    DomainSkill(
        slug="project.risk_dependency",
        title="Risk and Dependency Intelligence",
        domain=_PROJECT,
        scope=_PROJECT_SCOPE,
        focus=(
            "The risk register and the dependency network: open risks by "
            "severity and age, issues already realised, and which "
            "dependencies block the most work. A risk score is a "
            "forward-looking judgement someone entered, not an observed "
            "outcome, so report what was assessed and when it was last "
            "reviewed rather than what will happen. Keep a risk and an "
            "issue that has already materialised separate."
        ),
        dimensions=("risk severity", "risk status", "dependency type", "project", "period"),
    ),
)

#: The supplied IT Operations prompts' "Domain scope:" sentence, verbatim.
_IT_OPS_SCOPE = "Analyze IT services, incidents, changes, applications, cloud and IT cost."

#: The first covered domain whose prohibition is backed by an *absolute*
#: control. `policy_for(IT_OPERATIONS)` prohibits exactly one action --
#: `credential_disclosure` -- and restricts `DataCategory.CREDENTIAL`,
#: which `governance.classification.NEVER_DISCLOSED_CATEGORIES` withholds
#: from **every** caller including an admin: no role unlocks it
#: (`CLAUDE.md` §5.4). So the derived block carries it and no focus text
#: restates it, exactly as BFSI's four are derived (batch 8).
#:
#: That makes this batch the inverse of batches 9-12. There the policy
#: prohibited nothing and the focus texts had to carry the risk alone;
#: here the one prohibition is enforced unconditionally, so the skills'
#: job is narrower: not to invite a question whose only answer is a secret.
#: ITSM records name the assignee of a ticket, so the individual-ranking
#: guard arrives for the sixth time
#: -- but unlike batches 9-12 the derived block *does* append "Report in
#: aggregate only" here, so that guard is reinforcement rather than the
#: only line.
#:
#: The analytical problem that is genuinely new is the provenance of a
#: machine-generated measure. Batch 11 analysed a third party's claim and
#: batch 12 a self-assessment by the measured party; here the number is an
#: **instrument reading whose instrument was configured by the team being
#: measured**. An incident count is partly a property of the alerting
#: threshold and the deduplication rule, and availability is partly a
#: property of the exclusion windows, so the `DATABASE_FACT` is what the
#: tooling recorded under its configuration
#: (`agent.provenance.DataTruthLevel`).
_IT_OPS = BusinessDomain.IT_OPERATIONS

#: Declared in the supplied numeric order (01 IT service, 02 incident, 03
#: change management, 04 application performance, 05 IT cost and cloud),
#: so the domain default is IT Service Intelligence (01) -- the broadest.
IT_OPS_SKILLS: tuple[DomainSkill, ...] = (
    DomainSkill(
        slug="it_ops.service",
        title="IT Service Intelligence",
        domain=_IT_OPS,
        scope=_IT_OPS_SCOPE,
        focus=(
            "Service portfolio health: request and incident volume per "
            "service, fulfilment time, and availability against "
            "commitment by service tier. Availability is a definition "
            "before it is a measurement -- name the exclusion windows and "
            "the measurement source, because planned-maintenance "
            "treatment alone decides whether the same outage breaches. A "
            "service is only as available as the weakest link it "
            "genuinely depends on, so a dependency chain's health is not "
            "the average of its components'."
        ),
        dimensions=("service", "service tier", "business unit", "request category", "period"),
    ),
    DomainSkill(
        slug="it_ops.incident",
        title="Incident Intelligence",
        domain=_IT_OPS,
        scope=_IT_OPS_SCOPE,
        focus=(
            "Incident volume, severity mix, restoration time and where "
            "incidents concentrate. An incident count is partly a "
            "property of the alerting threshold and the deduplication "
            "rule that produced it, so a fall in volume may be a "
            "configuration change rather than a quieter estate -- say "
            "which rules were in force. Separate time to restore service "
            "from time to close the ticket, because the second is an "
            "administrative event. Report by service, severity and "
            "assignment group, never ranking the individual assignee, "
            "whose queue mix decides their numbers."
        ),
        dimensions=("service", "severity", "assignment group", "incident category", "period"),
    ),
    DomainSkill(
        slug="it_ops.change",
        title="Change Management Intelligence",
        domain=_IT_OPS,
        scope=_IT_OPS_SCOPE,
        focus=(
            "Change throughput and outcome: volume by type and risk, "
            "success and rollback rates, emergency against planned mix, "
            "and lead time to deploy. A change-failure rate needs its "
            "linkage rule stated -- whether an incident was tied to a "
            "change by a reviewer or by a time window, because a time "
            "window manufactures the relationship it then measures. A "
            "change followed by an incident is a sequence; calling the "
            "change its cause needs the linkage the data records."
        ),
        dimensions=("change type", "risk category", "service", "approval path", "period"),
    ),
    DomainSkill(
        slug="it_ops.application_performance",
        title="Application Performance Intelligence",
        domain=_IT_OPS,
        scope=_IT_OPS_SCOPE,
        focus=(
            "Application responsiveness and reliability: latency, error "
            "rate, throughput and saturation by application and "
            "environment. Name the percentile and report the "
            "distribution, because a mean response time hides the tail "
            "users actually experience -- and a percentile cannot be "
            "averaged across windows or instances, only recomputed from "
            "the underlying observations. State the monitoring coverage "
            "behind any figure, since an unmonitored component "
            "contributes nothing to an error rate and so reads as "
            "healthy."
        ),
        dimensions=("application", "environment", "transaction type", "percentile", "period"),
    ),
    DomainSkill(
        slug="it_ops.cost_cloud",
        title="IT Cost and Cloud Intelligence",
        domain=_IT_OPS,
        scope=_IT_OPS_SCOPE,
        focus=(
            "IT and cloud spend: cost by account, service and resource "
            "class, committed against on-demand, idle and unattached "
            "resources, and unit cost over time. Give the tagging "
            "coverage behind any allocation, because untagged spend is "
            "not absent spend -- it lands wherever the default sends it "
            "and quietly flatters every allocated figure. Say whether "
            "cost is amortised or as-invoiced, since commitments move "
            "spend between periods. Report resource counts, classes and "
            "cost, not the contents of resource configuration or log "
            "payloads."
        ),
        dimensions=("cloud account", "service", "resource class", "allocation tag", "period"),
    ),
)

#: The supplied Healthcare prompts' "Domain scope:" sentence, verbatim --
#: **including its trailing "never diagnose"**, which is the first time a
#: supplied scope sentence has carried a prohibition of its own.
#:
#: Kept verbatim deliberately, and the alternative was considered: trimming
#: the clause would stop the scope line overlapping
#: `ProhibitedAction.MEDICAL_DIAGNOSIS_OR_PRESCRIPTION`, which the composer
#: already derives. It is kept because `DomainSkill.scope` is documented as
#: the supplied sentence verbatim, so editing it would make the catalog
#: misrepresent what was supplied -- and because the rule in question
#: (`CLAUDE.md` §4.8, and this package's "derived, never restated") is
#: about a second *owner* that can drift. The derived block remains the
#: owner and is appended last, so it overrides; this line is quoted input.
#: The residual risk -- the policy dropping that prohibition while the
#: quoted sentence still asserts it -- is made *detected* rather than
#: silent by `test_the_three_prohibitions_are_pinned_so_the_scope_overlap_
#: cannot_go_stale`.
_HEALTHCARE_SCOPE = (
    "Analyze healthcare operations, journeys, resources, finance, quality "
    "and compliance; never diagnose."
)

#: The most tightly governed domain yet covered. `policy_for(HEALTHCARE)`
#: declares three `ProhibitedAction`s (clinical decision, medical diagnosis
#: or prescription, protected characteristic inference), restricts PHI on
#: top of the personal baseline, sets `aggregation_required=True`,
#: `minimum_group_size=5` and an `export_row_limit` of **50** -- half the
#: next tightest among covered domains and the lowest in the registry
#: alongside LEGAL and RISK_AUDIT.
#:
#: Two of those controls have analytical consequences the skills must
#: frame, because the composer deliberately does not put any of them in
#: the prompt (`domain_skills/contract.py` on what is enforced instead of
#: restated):
#:
#: - `governance.suppression.SUPPRESSED_MASK` replaces a sensitive measure
#:   for a cohort below five with "[suppressed: group too small]". It is
#:   self-describing, so the risk is not misreading one cell but folding it
#:   into a total or reading it as an absence of events.
#: - `governance.result_policy`'s export ceiling truncates a result
#:   carrying restricted data to 50 rows, for an authorized caller too, and
#:   reports `export_limited:N`. A share, ranking or concentration claim
#:   computed over those 50 rows describes the truncation, not the cohort.
#:
#: The clinical/operational line is this batch's batch-8 problem: two
#: supplied skills (patient journey, quality and compliance) are *about*
#: clinical outcomes. Reporting readmission, infection or complication
#: rates for a cohort is healthcare operations analytics; explaining why
#: one patient's outcome occurred, or what should be done about it, is a
#: clinical decision. So the focus texts redirect to the cohort question
#: rather than only refusing (`CLAUDE.md` §4.2 -- a refusal without an
#: alternative is not a skill).
_HEALTHCARE = BusinessDomain.HEALTHCARE

#: Declared in the supplied numeric order (01 operations, 02 patient
#: journey, 03 resource, 04 financial, 05 quality and compliance), so the
#: domain default is Healthcare Operations Intelligence (01).
HEALTHCARE_SKILLS: tuple[DomainSkill, ...] = (
    DomainSkill(
        slug="healthcare.operations",
        title="Healthcare Operations Intelligence",
        domain=_HEALTHCARE,
        scope=_HEALTHCARE_SCOPE,
        focus=(
            "Throughput and flow: attendances, admissions, discharges, "
            "waiting times, cancellations and occupancy by site and "
            "service line. Name which clock a wait is measured from -- "
            "referral, decision to treat, or appointment -- because the "
            "same pathway reports three different waits. Occupancy is a "
            "point-in-time census and must never be summed across days, "
            "so an average has to name the time of day it was taken. Say "
            "whether utilisation is against funded or physically "
            "available capacity."
        ),
        dimensions=("site", "service line", "care setting", "admission type", "period"),
    ),
    DomainSkill(
        slug="healthcare.patient_journey",
        title="Patient Journey Intelligence",
        domain=_HEALTHCARE,
        scope=_HEALTHCARE_SCOPE,
        focus=(
            "How cohorts move through a pathway: stage-to-stage "
            "intervals, drop-off, follow-up attendance and readmission. "
            "A readmission rate needs its window and its index-admission "
            "rule stated, because counting planned follow-up admissions "
            "inflates it. This data observes only the care this provider "
            "recorded, so a pathway that looks complete may be someone "
            "who went elsewhere -- say so rather than reading it as an "
            "outcome. Describe how a cohort moved and what the record "
            "shows; never account for why an individual patient's "
            "outcome occurred. A result truncated to the domain's export "
            "ceiling is a sample, not a population, so no share or "
            "ranking may be computed over it."
        ),
        dimensions=("pathway", "cohort", "pathway stage", "care setting", "period"),
    ),
    DomainSkill(
        slug="healthcare.resource",
        title="Healthcare Resource Intelligence",
        domain=_HEALTHCARE,
        scope=_HEALTHCARE_SCOPE,
        focus=(
            "Staffing and capacity: establishment against worked hours, "
            "skill mix, bank and agency usage, theatre and bed capacity, "
            "and equipment availability. Headcount is not capacity -- "
            "report skill mix and worked hours, because a shift covered "
            "by a different grade is not the same shift. Report by unit, "
            "staff group and grade, never an individual clinician's "
            "throughput: case mix decides it, so whoever takes the harder "
            "work reads as slower."
        ),
        dimensions=("unit", "staff group", "grade", "shift pattern", "period"),
    ),
    DomainSkill(
        slug="healthcare.financial",
        title="Healthcare Financial Intelligence",
        domain=_HEALTHCARE,
        scope=_HEALTHCARE_SCOPE,
        focus=(
            "Payer mix, claim and denial outcomes, cost per case and the "
            "revenue cycle. Charges, payments and cost are three "
            "different numbers and must never stand in for one another. "
            "Cost per case is not comparable across units without "
            "case-mix adjustment, so name the adjustment or say there is "
            "none. Give the stage a denial was measured at, since a "
            "front-end rejection, an adjudicated denial and an overturned "
            "appeal each count differently."
        ),
        dimensions=("payer", "service line", "case-mix group", "denial reason", "period"),
    ),
    DomainSkill(
        slug="healthcare.quality_compliance",
        title="Healthcare Quality and Compliance Intelligence",
        domain=_HEALTHCARE,
        scope=_HEALTHCARE_SCOPE,
        focus=(
            "Quality indicators, safety incidents, infection rates, audit "
            "findings and compliance attainment by unit and cohort. Give "
            "the denominator and the risk adjustment: a rate per "
            "device-day is not a rate per patient, and an unadjusted "
            "outcome comparison ranks case mix rather than care. Incident "
            "volume measures reporting culture as much as safety, so a "
            "rise may be better reporting -- report what was recorded and "
            "when. A withheld measure for a small cohort is not a zero: "
            "never read it as an absence of events, and never fold it "
            "into a total."
        ),
        dimensions=("indicator", "unit", "cohort", "incident category", "period"),
    ),
)

#: The supplied Energy prompts' "Domain scope:" sentence, verbatim.
_ENERGY_SCOPE = "Analyze energy, utilities, sustainability, emissions and efficiency."

#: The sixth covered domain whose policy declares no `ProhibitedAction`.
#: More than that: `policy_for(ENERGY)` is **field-identical to
#: `policy_for(MANUFACTURING)`** -- the same single declared restriction
#: (`DataCategory.PROPRIETARY`), the same `minimum_group_size=0`, the same
#: `export_row_limit=500`, no prohibitions, no aggregation requirement and
#: no audit requirement. Governance therefore says nothing that
#: distinguishes an energy question from a production one, which makes
#: this batch the clearest case in the series of the thing this package
#: exists for: everything that makes an energy analysis an energy analysis
#: is framing, and framing is the only layer that can carry it.
#:
#: The one field where the two policies differ cuts against this domain.
#: MANUFACTURING pre-authorizes a join with SUPPLY_CHAIN and vice versa;
#: ENERGY pre-authorizes nothing (`cross_domain_allowed` is empty), while
#: the domain's signature measure -- intensity per unit of output, per
#: square metre, per unit of revenue -- takes its denominator from
#: another domain entirely. `governance/request_policy.py` is what decides
#: whether that join needs authorization, and this package must not
#: restate it; what belongs here is the analytical half, which the
#: efficiency skill carries: name the denominator, its source and its
#: grain, because a ratio across two grains is a new metric rather than a
#: conversion.
#:
#: `minimum_group_size=0` is deliberate in the registry -- this domain
#: holds no individual-level data to protect -- and nothing here needs an
#: individual-level guard of the kind batches 9 to 12 carry. A meter is
#: not a person.
#:
#: This comment used to conclude "so nothing here is suppressed". Batch 17
#: measured that and it is wrong: a declared `0` is treated as unset by
#: `minimum_group_size_for`, so the operator's default (5) applies. The
#: claim that stands is about the subject matter, not about the threshold.
_ENERGY = BusinessDomain.ENERGY

#: Declared in the supplied numeric order (01 consumption, 02 utility
#: performance, 03 sustainability, 04 carbon and emissions, 05
#: efficiency), so the domain default is Energy Consumption Intelligence
#: (01).
#:
#: The distinguishing problem of this batch is **provenance**, continuing
#: a series the catalog has been accumulating: batch 11 handled a record
#: of someone else's claim, batch 12 a self-assessment by the party being
#: measured, batch 13 an instrument reading whose value depends on how the
#: measured team configured the instrument. Emissions go one step further
#: -- the quantity is not measured at all. It is activity data scaled by a
#: published emission factor that a reporter selected, and most of Scope 3
#: is a supplier's own claim or an estimate derived from spend. A fall can
#: be a factor revision. Presenting any of it as an observed quantity is
#: what `CLAUDE.md` 4.4 forbids, and no deterministic control in this repo
#: can tell the difference, because the arithmetic is valid either way.
ENERGY_SKILLS: tuple[DomainSkill, ...] = (
    DomainSkill(
        slug="energy.consumption",
        title="Energy Consumption Intelligence",
        domain=_ENERGY,
        scope=_ENERGY_SCOPE,
        focus=(
            "Consumption across sites, meters and commodities: totals, "
            "load profile, peaks and movement against prior period or "
            "target. Consumption is a difference between two meter "
            "readings, so a reset, a rollover or a meter replacement "
            "breaks the series rather than reducing usage, and a missing "
            "interval is unknown consumption, not zero. Say whether a "
            "read is actual or estimated, because an estimated read is a "
            "placeholder a later true-up reverses. Demand and energy are "
            "different quantities: a peak is an instant and must never be "
            "summed, while energy accumulates. Normalise before comparing "
            "periods -- degree days and occupied days account for much of "
            "any year-on-year movement -- or say that it was not "
            "normalised."
        ),
        dimensions=("site", "meter", "commodity", "tariff", "period"),
    ),
    DomainSkill(
        slug="energy.utility_performance",
        title="Utility Performance Intelligence",
        domain=_ENERGY,
        scope=_ENERGY_SCOPE,
        focus=(
            "Utility accounts, tariffs, billed cost and supply "
            "reliability by provider and site. A billing period is not a "
            "calendar month, so compare a per-day rate rather than two "
            "bills covering different numbers of days. A blended unit "
            "cost mixes commodity, delivery, demand and standing charges "
            "with taxes and levies, so a rise in it may be a demand "
            "charge or a levy change rather than a price change: "
            "separate the components before attributing one. Net "
            "metering can drive billed consumption to zero or below, "
            "which hides the gross volume and leaves a unit rate "
            "undefined, so report gross and net separately. A "
            "reliability measure needs its exclusion rules stated, "
            "because excluded major-event days are where most of the "
            "lost supply sits."
        ),
        dimensions=("utility provider", "account", "commodity", "tariff component", "period"),
    ),
    DomainSkill(
        slug="energy.sustainability",
        title="Sustainability Intelligence",
        domain=_ENERGY,
        scope=_ENERGY_SCOPE,
        focus=(
            "Progress against sustainability targets: absolute and "
            "intensity measures, coverage of the reporting boundary, and "
            "movement against a baseline. State the baseline year and "
            "whether it has been restated, because an acquisition or a "
            "divestiture recalculates it and a reduction against a "
            "restated baseline can be a change of boundary rather than "
            "of behaviour. Report absolute and intensity together: "
            "intensity falls while the absolute figure rises whenever the "
            "denominator grows faster. Give the share of sites and of "
            "activity the figure actually covers, since a site outside "
            "the boundary reads as a clean one. A purchased certificate "
            "is a contractual instrument and never evidence that less "
            "energy was used on site."
        ),
        dimensions=("reporting boundary", "site", "indicator", "baseline year", "period"),
    ),
    DomainSkill(
        slug="energy.emissions",
        title="Carbon and Emission Intelligence",
        domain=_ENERGY,
        scope=_ENERGY_SCOPE,
        focus=(
            "Emissions by scope, source and site, and their movement "
            "against baseline or target. An emissions figure is almost "
            "never measured: it is activity data scaled by a published "
            "emission factor someone selected, so name the factor set and "
            "its vintage, because a fall can be a factor revision rather "
            "than a reduction, and a carbon-equivalent total is "
            "comparable only to another computed on the same "
            "global-warming-potential set. Scope 1, 2 and 3 are "
            "different measurement regimes: say which are included, and "
            "never present their sum as one observed quantity. Scope 2 "
            "has two methods, location-based and market-based, which "
            "give different answers for the same electricity and are "
            "neither interchangeable nor averageable. Most of Scope 3 is "
            "a supplier's own claim or an estimate derived from spend, so "
            "report it as reported, with its method. Offsets and removals "
            "change a net figure only: report gross and net separately "
            "and never net alone."
        ),
        dimensions=("scope", "emission source", "site", "greenhouse gas", "period"),
    ),
    DomainSkill(
        slug="energy.efficiency",
        title="Energy Efficiency Intelligence",
        domain=_ENERGY,
        scope=_ENERGY_SCOPE,
        focus=(
            "Efficiency measures and the savings attributed to them: "
            "avoided consumption, intensity per unit of output or area, "
            "and payback. A saving is a counterfactual rather than an "
            "observation -- it is the difference against an adjusted "
            "baseline -- so state the adjustments made for weather, "
            "occupancy and output, and report calculated avoided "
            "consumption rather than metered reduction. The sum of "
            "project savings is not a site reduction: two measures acting "
            "on the same end use claim the same unit twice, so reconcile "
            "against the meter before adding them up. An intensity figure "
            "takes its denominator from another domain, so name the "
            "denominator, its source and its grain -- a ratio across two "
            "grains is a new metric, not a conversion. An average of "
            "efficiency ratios across sites is not the portfolio ratio, "
            "which has to be recomposed from the underlying totals."
        ),
        dimensions=("efficiency measure", "site", "end use", "baseline period", "period"),
    ),
)

#: The supplied Risk & Audit prompts' "Domain scope:" sentence, verbatim.
#:
#: The first supplied scope sentence that simply **enumerates its own five
#: skills** -- enterprise risk, internal audit, controls, operational risk
#: and business continuity are the titles of prompts 01 to 05. The scope
#: line therefore narrows nothing for any individual skill, and every
#: distinction between them has to be carried by `focus`.
_RISK_AUDIT_SCOPE = (
    "Analyze enterprise risk, internal audit, controls, operational risk "
    "and business continuity."
)

#: The domain where the subject matter *is* the prohibition.
#:
#: `policy_for(RISK_AUDIT)` declares two `ProhibitedAction`s, and both bear
#: on all five skills rather than on one of them:
#:
#: - `liability_determination` -- **the first prohibition in this catalog
#:   that no covered domain has derived before.** Only LEGAL and RISK_AUDIT
#:   declare it, and LEGAL has no skills.
#: - `unsupported_allegation` -- already carried by HR, BFSI and LEGAL, but
#:   incidental in each of them. Here the domain's own professional output
#:   is an assertion: a finding is the audit function's recorded opinion
#:   against a stated criterion, a control rating is a conclusion drawn
#:   from a sample, a risk score is a judgement, a loss attribution is a
#:   classification someone chose.
#:
#: Batch 8 was the first batch whose *skills were named after* a
#: prohibition; this is the first whose whole vocabulary is. The derived
#: block (`composer._prohibitions_block`) is what reaches the model, so no
#: focus text restates either prohibition -- what the focus texts carry is
#: the redirect: report what was recorded, by whom, and against which
#: criterion.
#:
#: Two further facts about this policy are structural rather than
#: incidental, and both are asserted in
#: `tests/test_domain_skills.py::TestRiskAuditIsWhereTheSubjectMatterIsThe
#: Prohibition` rather than only claimed here:
#:
#: - `DataCategory.AUDIT` is restricted by **this domain and no other**,
#:   and it is this domain's ordinary subject matter --
#:   `governance.classification`'s `audit_finding` pattern matches
#:   `audit_finding`, `control_weakness`, `risk_assessment`,
#:   `remediation_plan` and `non_conformance`. Every covered domain so far
#:   restricted a side-channel (PII in manufacturing, PHI in healthcare);
#:   this one restricts the thing it is for.
#: - **That restriction is nonetheless inert in the result gate**, which
#:   was measured rather than assumed. The `audit_finding` rule classifies
#:   at `DataClassification.CONFIDENTIAL`, and all three row-level
#:   controls -- `result_policy._classification_masks`,
#:   `result_policy._carries_restricted_data` (the export ceiling) and
#:   `suppression._is_sensitive_measure` -- require `SENSITIVE` or above.
#:   So an ordinary findings result is not masked for any role, is not
#:   truncated by the domain's `export_row_limit` of 50 (the tightest in
#:   the registry, alongside HEALTHCARE and LEGAL), and its counts are not
#:   suppressed for a group below `minimum_group_size=5`. Those controls
#:   engage here only when the result *also* carries personal data, the
#:   same as in any other domain.
#:
#: That is deliberate upstream, not a defect to fix from this package:
#: `BASELINE_RESTRICTED_CATEGORIES` excludes the commercially confidential
#: categories on purpose, and `_carries_restricted_data` says in as many
#: words that the ceiling exists to stop bulk extraction of personal
#: records rather than to cap ordinary analytics. The consequence for this
#: batch is the point: the distinguishing thing governance has to say
#: about a risk question reaches the model as **prompt text** -- the two
#: derived prohibitions -- and nothing it says operates on the rows. Batch
#: 15 was a domain where governance said nothing distinguishing at all;
#: this is one where it says something and the loudest part of it does
#: nothing at the row level. Either way the framing carries the weight,
#: which is what this package is for.
#:
#: `cross_domain_allowed` is empty in both directions, and no other domain
#: lists RISK_AUDIT either -- while the entire subject matter of the domain
#: is other domains' processes: a control over payroll is HR, over payments
#: is FINANCE, over change is IT_OPERATIONS. `governance/request_policy.py`
#: owns whether such a join is authorized and this package must not restate
#: it (see `domain_skills/contract.py`), so no focus text below mentions
#: authorization or permission.
_RISK_AUDIT = BusinessDomain.RISK_AUDIT

#: Declared in the supplied numeric order (01 enterprise risk, 02 internal
#: audit, 03 control effectiveness, 04 operational risk, 05 business
#: continuity), so the domain default is Enterprise Risk Intelligence (01).
#:
#: The distinguishing analytical problem is the next step in the provenance
#: series the catalog has been accumulating. Batch 11 handled a record of
#: someone else's claim, batch 12 a self-assessment by the measured party,
#: batch 13 an instrument reading the measured team configured, batch 15 a
#: figure nobody measured at all. Here the figure exists **only where
#: somebody looked**: a finding is raised where an audit went, an exception
#: is found where a control was tested, a loss is recorded above the
#: threshold someone set. The measure therefore moves with the amount of
#: looking rather than with the amount of exposure, and a zero is ambiguous
#: between clean, untested and undetected. No deterministic control in this
#: repo can tell those apart -- the counts are arithmetically correct in
#: every case (`CLAUDE.md` §4.4).
RISK_AUDIT_SKILLS: tuple[DomainSkill, ...] = (
    DomainSkill(
        slug="risk_audit.enterprise_risk",
        title="Enterprise Risk Intelligence",
        domain=_RISK_AUDIT,
        scope=_RISK_AUDIT_SCOPE,
        focus=(
            "The risk register in aggregate: inherent and residual "
            "ratings, movement against appetite, and concentration by "
            "category, business unit and owning function. A rating is a "
            "judgement recorded on an ordinal scale, not a measurement, "
            "so likelihood times impact produces a ranking code rather "
            "than a quantity -- a 12 is not twice a 6, and such scores "
            "must never be averaged or summed; count and rank them by "
            "band instead, naming the scale and its version. Residual "
            "rating is inherent rating reduced by an assumed control "
            "effectiveness, so it is a judgement about a judgement, and a "
            "fall in it can be a re-rating of the controls with no change "
            "in exposure. The register holds the risks someone "
            "registered: a short register may be an unmaintained one, not "
            "a safe business. A risk is an exposure that has not "
            "occurred, so never report one as an event. Report by owning "
            "function and never rank named owners by their open risks -- "
            "whoever maintains the register best records the most."
        ),
        dimensions=(
            "risk category",
            "business unit",
            "owning function",
            "rating band",
            "period",
        ),
    ),
    DomainSkill(
        slug="risk_audit.internal_audit",
        title="Internal Audit Intelligence",
        domain=_RISK_AUDIT,
        scope=_RISK_AUDIT_SCOPE,
        focus=(
            "Audit plan coverage and execution, findings by rating and "
            "theme, and remediation ageing. A finding is raised only "
            "where an audit looked, so a unit with no findings may be an "
            "unaudited unit rather than a clean one: report finding "
            "counts against coverage of the plan, and never read a zero "
            "as an absence of issues. A finding is the audit function's "
            "recorded opinion against a stated criterion, not an "
            "established breach -- report what was raised, its rating and "
            "the criterion it was raised against, and never conclude who "
            "is at fault or what the finding proves. Ageing runs from a "
            "due date the owner can revise, so an extended date makes an "
            "overdue item current with nothing remediated: give the "
            "original and the current date and say which the ageing uses. "
            "Closure is management's assertion unless audit validated it, "
            "so report validated and self-reported closures separately -- "
            "a closure count measures closure activity, not risk removed. "
            "Finding counts from audits of different scope and rating "
            "scale are not comparable."
        ),
        dimensions=(
            "audit engagement",
            "business unit",
            "finding rating",
            "finding theme",
            "period",
        ),
    ),
    DomainSkill(
        slug="risk_audit.control_effectiveness",
        title="Control Effectiveness Intelligence",
        domain=_RISK_AUDIT,
        scope=_RISK_AUDIT_SCOPE,
        focus=(
            "Control testing outcomes: design against operating "
            "effectiveness, exception rates, and coverage of the control "
            "population. Design and operating effectiveness are different "
            "conclusions -- a control can be well designed and not "
            "performed -- so never collapse them into one pass rate. A "
            "conclusion is drawn from a sample over a period, so give the "
            "sample size and whether the sample was statistical or "
            "judgemental: one exception in twenty-five is not a four per "
            "cent population rate, and a judgemental sample cannot be "
            "projected to the population at all. Untested is not "
            "effective: report untested controls as their own state and "
            "never let them fall into a pass-rate denominator. An "
            "automated control is usually tested once as a configuration "
            "while a manual one is tested across a sample of occurrences, "
            "so their pass rates are counts of different things. An "
            "exception is a deviation from the test criterion and nothing "
            "more -- it is not proof of loss, of intent, or of who is at "
            "fault; report it by control and process rather than by the "
            "person who performed it."
        ),
        dimensions=(
            "control",
            "control type",
            "process",
            "test cycle",
            "period",
        ),
    ),
    DomainSkill(
        slug="risk_audit.operational_risk",
        title="Operational Risk Intelligence",
        domain=_RISK_AUDIT,
        scope=_RISK_AUDIT_SCOPE,
        focus=(
            "Loss events, incidents, near misses and key risk indicators "
            "by event type, process and business line. Loss data is "
            "truncated at a reporting threshold -- only events above it "
            "are captured -- so every total and average is conditional on "
            "that threshold and a change to it breaks the series rather "
            "than changing the risk. Occurrence date, discovery date and "
            "accounting date give three different series: name which one "
            "is used, and treat the most recent periods as understated, "
            "because events are still being discovered and open losses "
            "are still developing, so a fall at the end of the series is "
            "usually incompleteness rather than improvement. The "
            "distribution is dominated by a few severe events, which "
            "makes a mean uninformative: report frequency and severity "
            "separately and name the largest events as individual cases. "
            "A rise in recorded incidents can be better reporting rather "
            "than more failure, and a near miss or a boundary event is "
            "classified by judgement. A key risk indicator is a proxy "
            "someone selected against a threshold someone set, so a "
            "breach is a signal to investigate, never a measured "
            "exposure or an established cause. Report by event type, "
            "process and business line, never attributing an event to a "
            "named person."
        ),
        dimensions=(
            "event type",
            "business line",
            "process",
            "severity band",
            "period",
        ),
    ),
    DomainSkill(
        slug="risk_audit.business_continuity",
        title="Business Continuity Intelligence",
        domain=_RISK_AUDIT,
        scope=_RISK_AUDIT_SCOPE,
        focus=(
            "Continuity readiness: criticality tiering, recovery "
            "objectives, exercise outcomes and dependency concentration. "
            "A recovery time objective and a recovery point objective are "
            "targets agreed in a plan, never achieved times, so comparing "
            "one with an exercise result compares a target with a "
            "rehearsal. An exercise is planned, scoped and usually "
            "announced, and a tabletop is not a failover -- give the "
            "exercise type, and never present a passed exercise as "
            "demonstrated recoverability under an unplanned event. A "
            "recovery time measured in an exercise normally starts at "
            "declaration and so excludes detection and decision time: "
            "state where the clock starts. Criticality tiers and the "
            "dependency map are declared by the business, and the map is "
            "only as complete as what was declared, so an unmapped "
            "dependency reads as no dependency: report the coverage of "
            "the mapping alongside any concentration figure, because "
            "concentration on one site, provider or system is visible "
            "only inside the boundary that was mapped."
        ),
        dimensions=(
            "business process",
            "criticality tier",
            "dependency",
            "exercise type",
            "period",
        ),
    ),
)

#: The supplied Legal prompts' "Domain scope:" sentence, verbatim.
#:
#: The second scope sentence running that simply **enumerates its own five
#: skills** -- contracts, legal matters, compliance, board actions and
#: governance are the subjects of prompts 01 to 05, exactly as batch 16's
#: was. The scope line narrows nothing for any one skill, so every
#: distinction between them is carried by `focus`.
_LEGAL_SCOPE = "Analyze contracts, legal matters, compliance, board actions and governance."

#: The domain where the prohibited output is the *question*.
#:
#: `policy_for(LEGAL)` declares three `ProhibitedAction`s, and they are a
#: **strict superset of batch 16's** -- the same `liability_determination`
#: and `unsupported_allegation`, plus `legal_conclusion_or_advice`. Batch 6
#: was a strict *subset* of another domain's (Training inside HR); this is
#: the first strict superset, and it lands one batch after the set it
#: contains.
#:
#: `legal_conclusion_or_advice` is declared by **LEGAL alone**, so this
#: batch derives it for the first time -- and it was the last of the
#: fourteen `ProhibitedAction`s never derived into any covered domain.
#: Registering this batch therefore means every one of them is now carried
#: by at least one covered domain, which
#: `test_every_prohibited_action_is_now_derived_by_a_covered_domain`
#: asserts rather than this comment merely claiming.
#:
#: What makes the domain hard is not the count but how far the prohibition
#: overlaps the questions people actually ask. In batch 16 the subject
#: matter was a recorded assertion; here the prohibited output *is* the
#: natural question. "Are we compliant?", "will we win?", "is this clause
#: enforceable?", "what is our exposure?" each ask for a conclusion in law,
#: and a skill that only refused would answer nothing (`CLAUDE.md` §4.2,
#: and the batch-8 lesson that a refusal without an alternative is not a
#: skill). So every focus text below redirects to the same answerable
#: question: what does the record hold, in which field, and as of when.
#:
#: **The first covered domain whose two declared categories behave
#: oppositely at the row level**, which was measured rather than assumed
#: (§23's checklist step, added after batch 16 published a claim the code
#: did not support):
#:
#: - `DataCategory.LEGAL` comes from `governance.classification`'s
#:   `legal_privileged` rule at **`HIGHLY_SENSITIVE`**, which is *above*
#:   the `SENSITIVE` threshold every row-level control keys on. So it is
#:   genuinely live: a column like `litigation_status`, `settlement_amount`
#:   or `legal_opinion` is masked for a caller without
#:   `VIEW_RESTRICTED_COLUMNS`, **and** it trips the domain's
#:   `export_row_limit` of 50 for every caller including an admin.
#: - `DataCategory.CONTRACT` comes from the `contract_terms` rule at
#:   `CONFIDENTIAL`, below that threshold, so it changes no rows at all --
#:   the same inertness batch 16 found for `AUDIT`.
#:
#: Measured: a 60-row result of `(matter_id, litigation_status)` comes back
#: masked and `export_limited:10` at 50 rows for a viewer, and
#: `export_limited:10` at 50 rows for an admin; a 60-row result of
#: `(contract_id, contract_value)` comes back untouched for both. So the
#: matter skill has a truncation consequence to state and the contract
#: skill does not, and that asymmetry is the opposite of a tidy story --
#: it is why it is written down here instead of inferred from the policy.
#:
#: **On suppression, where this package had been wrong.** LEGAL declares
#: `minimum_group_size=0`, and three earlier batch comments read that as
#: "no small-group suppression here". It is not:
#: `governance.domains.minimum_group_size_for` is
#: `policy.minimum_group_size if policy.minimum_group_size else
#: configured_default`, so a declared `0` is falsy and the operator's
#: `GOVERNANCE_MIN_GROUP_SIZE` applies -- and that setting defaults to
#: **5**. The function's own docstring says the opposite ("honored as a
#: real zero, not treated as unset"), so the contradiction is in
#: `governance/`, not here; `test_a_declared_zero_group_size_is_not_a_real_zero`
#: pins the behaviour so this package stops repeating the wrong reading,
#: and the three stale comments are corrected in place.
#:
#: What *is* true is narrower and still the reason the scorecard skill
#: carries its own guard: suppression only touches a measure classified
#: `SENSITIVE` or above, and a board-composition count is an ordinary
#: integer. Measured: `(director_category, director_count)` with a
#: single-director category is returned intact at k=5. A board is about ten
#: people, so a one-person cell identifies someone and nothing
#: deterministic will hide it.
#:
#: `cross_domain_allowed` is empty in both directions. No focus text
#: mentions authorization or permission: `governance/request_policy.py`
#: owns that and this package must not restate it (see
#: `domain_skills/contract.py`).
_LEGAL = BusinessDomain.LEGAL

#: Declared in the supplied numeric order (01 contract, 02 legal matter,
#: 03 compliance, 04 board and secretarial, 05 corporate governance
#: scorecard), so the domain default is Contract Intelligence (01). The
#: prompts arrived shuffled (01, 03, 02, 04, 05); numeric order wins.
LEGAL_SKILLS: tuple[DomainSkill, ...] = (
    DomainSkill(
        slug="legal.contract",
        title="Contract Intelligence",
        domain=_LEGAL,
        scope=_LEGAL_SCOPE,
        focus=(
            "The contract portfolio in aggregate: counts and value by "
            "type and counterparty, the renewal and expiry pipeline, "
            "clause and obligation coverage, and cycle time to execute. "
            "Contract value is usually a ceiling rather than an amount "
            "earned or spent -- a maximum or not-to-exceed figure "
            "authorises, it does not commit -- so never present it as "
            "revenue or cost, and name the consumed figure separately if "
            "the data holds one. A total across contracts of different "
            "terms is not comparable: give the term or an annualised "
            "value. An expiry date alone misstates the pipeline, because "
            "an auto-renewing contract does not expire; the notice "
            "deadline is the date that governs, and effective, signature "
            "and commencement dates are three different dates. Clause "
            "coverage reports what was extracted and tagged, so an "
            "untagged clause reads as an absent one -- give the "
            "extraction coverage beside any coverage figure. Counterparty "
            "names are rarely normalised, and entity variants split one "
            "counterparty across rows, which understates concentration. "
            "Report what the record holds, in which field and as of when; "
            "never say whether a clause is enforceable, whether an "
            "obligation has been breached, or what a term means."
        ),
        dimensions=(
            "contract type",
            "counterparty",
            "business unit",
            "lifecycle stage",
            "period",
        ),
    ),
    DomainSkill(
        slug="legal.matter",
        title="Legal Matter Intelligence",
        domain=_LEGAL,
        scope=_LEGAL_SCOPE,
        focus=(
            "The matter inventory: volume and stage by matter type and "
            "jurisdiction, ageing, external spend, and recorded "
            "outcomes. A recorded exposure or reserve is an accounting "
            "estimate made under a policy -- never a prediction of loss "
            "and never an admission -- so a movement in it can be a "
            "re-estimation rather than an event; report it as the "
            "estimate it is, with its basis if the data names one. "
            "Ageing runs from the date a matter was opened, which is when "
            "the legal team learned of it rather than when the underlying "
            "events happened, so it measures the process and not the "
            "dispute. Outcome codes are not a win rate: a settlement is "
            "not a defeat, a dismissal may be procedural, and any such "
            "rate needs a definition the record may not carry. External "
            "spend is incurred, billed and accrued at different times and "
            "outside-counsel invoices arrive late, so the most recent "
            "periods understate. Matter columns are among the most "
            "protected in the platform, and a result carrying them is "
            "capped at the domain's export ceiling for every caller -- so "
            "treat what came back as a capped extract and compute no "
            "count, share or ranking over it without saying that the cap "
            "applied. Report counts, types, stages and spend, never the "
            "substance of a matter or the strategy in it; report by "
            "matter type, jurisdiction and business unit rather than "
            "ranking the people named in matters; and never weigh the "
            "merits or forecast how a matter will end."
        ),
        dimensions=(
            "matter type",
            "jurisdiction",
            "matter stage",
            "business unit",
            "period",
        ),
    ),
    DomainSkill(
        slug="legal.compliance",
        title="Compliance Intelligence",
        domain=_LEGAL,
        scope=_LEGAL_SCOPE,
        focus=(
            "Attainment against the obligations the register records: "
            "attestation and training completion, policy exceptions, "
            "recorded incidents and filing timeliness, by obligation and "
            "business unit. Never state whether the organisation is "
            "compliant -- what the data supports is narrower and still "
            "useful: how much of the register is attested, by whom, and "
            "as of when. A completion rate counts attestations recorded, "
            "which is evidence that a process ran and not of the "
            "behaviour it asks for, so a figure at one hundred per cent "
            "says the process completed. The denominator is whichever "
            "population someone scoped as in-scope, so anyone outside "
            "that scope reads as attested; give the scope and its "
            "coverage. An approved policy exception is a permitted "
            "deviation rather than a failure, so never add the two "
            "together. A register is a living document, so an obligation "
            "added mid-period looks like a new gap -- state the "
            "register's as-of date. Recorded incidents depend on the "
            "channel that captured them, so a rise can be better "
            "reporting rather than worse conduct."
        ),
        dimensions=(
            "obligation",
            "regulatory framework",
            "business unit",
            "attestation cycle",
            "period",
        ),
    ),
    DomainSkill(
        slug="legal.board_secretarial",
        title="Board and Secretarial Intelligence",
        domain=_LEGAL,
        scope=_LEGAL_SCOPE,
        focus=(
            "Board and committee activity and the statutory record: "
            "meetings held, attendance and quorum, resolutions, action "
            "items, filings and the entity register. Attendance is "
            "recorded presence and not participation, and presence by a "
            "proxy or an alternate is a different fact to be reported "
            "separately rather than summed in. Quorum is defined per body "
            "by the constitution, so a quorum figure needs the rule "
            "stated -- a meeting can be quorate with few people present. "
            "Resolutions passed measures activity rather than "
            "effectiveness, and near-unanimous approval is the norm, so "
            "an approval rate carries almost no signal. Filing timeliness "
            "runs against a statutory deadline that differs by "
            "jurisdiction and entity type, so one days-late figure across "
            "entities compares different clocks. An action item is closed "
            "when its owner says so, which records a report of "
            "completion rather than a verification of it. The entity "
            "register holds dormant and struck-off entities that inflate "
            "any count, so state which statuses are included. Report by "
            "body, committee and entity; where a director's own "
            "attendance is itself a disclosed statutory figure, report it "
            "as that record and never as a judgement of how well they "
            "served."
        ),
        dimensions=(
            "governing body",
            "committee",
            "legal entity",
            "resolution type",
            "period",
        ),
    ),
    DomainSkill(
        slug="legal.governance_scorecard",
        title="Corporate Governance Scorecard",
        domain=_LEGAL,
        scope=_LEGAL_SCOPE,
        focus=(
            "Governance indicators and their movement against a stated "
            "framework: board composition and independence, tenure, "
            "committee structure, policy coverage and disclosure "
            "timeliness. A scorecard total is a composite of indicators "
            "someone selected and weighted, so the total moves with the "
            "weighting -- name the framework and its version, report the "
            "components beside the total, and never present the composite "
            "as a measure of how well the organisation is governed or of "
            "whether it meets a code. Independence is a classification "
            "against a definition that differs between codes and "
            "jurisdictions, so say which definition the data uses. "
            "Structural indicators such as board size or committee count "
            "describe form and not function. The denominator here is "
            "about ten people, so one appointment moves a percentage by a "
            "large step and a single-member category describes an "
            "identifiable person: report counts next to every percentage, "
            "and do not break a small body down to a cell that stands for "
            "one individual. A benchmark compares against a peer set "
            "someone chose, so name the peer set or say the comparison "
            "has none."
        ),
        dimensions=(
            "governance framework",
            "indicator",
            "governing body",
            "legal entity",
            "period",
        ),
    ),
)

#: The supplied Real Estate prompts' "Domain scope:" sentence, verbatim.
#:
#: The third scope sentence running that simply **enumerates its own five
#: skills** -- properties, occupancy, leases, maintenance and property
#: financials are the subjects of prompts 01 to 05, exactly as batches 16
#: and 17 were. The scope line narrows nothing for any one skill, so every
#: distinction between them is carried by `focus`.
_REAL_ESTATE_SCOPE = "Analyze properties, occupancy, leases, maintenance and property financials."

#: The first covered domain whose subject is a **physical asset**, and the
#: domain where that turns out to decide which controls fire.
#:
#: `policy_for(REAL_ESTATE)` declares no `ProhibitedAction` at all, so
#: `composer._prohibitions_block` appends nothing and the shared contract's
#: ranking invitation reaches this domain unqualified -- the seventh
#: zero-prohibition domain, after MIS, eCommerce, Supply Chain,
#: Manufacturing, Customer Service, Project and Energy. Unlike batch 15's
#: ENERGY, the policy is **not** field-identical to any other domain's:
#: `restricted_categories` is the personal baseline plus `CONTRACT`,
#: `minimum_group_size` is a declared `5` and `export_row_limit` is 500.
#:
#: Four findings, all measured before these focus texts were written (§23's
#: checklist steps 9 and 10), and three of them the opposite of the tidy
#: story:
#:
#: - **A property's address is classified as personal data.**
#:   `classify_field("properties", "property_address")` returns
#:   `DataCategory.PII` at `SENSITIVE` with `MaskingStrategy.MASK`, because
#:   `governance.classification`'s `address` rule is deliberately unbounded
#:   and cannot tell a building's location from somebody's home. In every
#:   other covered domain an address is incidental to the question; here it
#:   **is** the asset's own identifier. Measured: 60 rows of
#:   `(property_address, occupancy_rate)` come back with every address
#:   `[restricted]` for a caller who may not see it -- indistinguishable
#:   rows -- and in full for an admin. The portfolio skill therefore carries
#:   the labelling guard itself.
#: - **That same column is the only thing that opens the export ceiling.**
#:   `result_policy._carries_restricted_data` engages the ceiling only when
#:   a column classifies at `SENSITIVE` *and* falls in a category the domain
#:   restricts. Measured: 600 rows of `(property_address, occupancy_rate)`
#:   are cut to 500 with `export_limited:100` **for an admin too**, while
#:   600 rows of `(property_id, occupancy_rate)` and 600 of
#:   `(work_order_id, repair_cost)` are returned untouched. So the declared
#:   500 is real but narrow: selecting the address is what truncates a
#:   portfolio listing, and nothing else a property analysis naturally
#:   selects does.
#: - **`CONTRACT`, the domain's one non-baseline category, is inert.**
#:   `classify_field("leases", "lease_amount")` is `CONTRACT` at
#:   `CONFIDENTIAL`, one level below the threshold every row-level control
#:   keys on, so 600 rows of `(lease_id, lease_amount)` are untouched for a
#:   viewer and an admin alike. That is the third time in the series -- the
#:   same inertness batch 16 found for `AUDIT` and batch 17 for `CONTRACT`
#:   itself -- but the first time it is the domain's *entire* declared
#:   addition to the baseline. Every live control here comes from the
#:   personal baseline `CLAUDE.md` §5.8 unions in, not from anything this
#:   policy declares.
#: - **Small-grain occupancy is not suppressed.** `minimum_group_size=5` is
#:   declared truthy, so unlike batch 17's LEGAL it is a real 5 whatever
#:   `GOVERNANCE_MIN_GROUP_SIZE` says. It still does not reach this
#:   subject: `suppression._is_sensitive_measure` keys on the *measure's*
#:   classification, and an occupant count is an ordinary integer.
#:   Measured: `(floor, occupant_count)` with a one-occupant floor is
#:   returned intact at k=5 for a viewer and an admin. Occupancy is derived
#:   from badge, booking and sensor records about identifiable people, so
#:   this is the batch's sharpest guard and the occupancy skill carries it.
#:
#: **Where this batch's own work is.** Two columns a real-estate schema
#: certainly holds are not classified at all:
#: `classify_field` returns `DataCategory.NONE` at `PUBLIC` for both
#: `tenant_name` and `technician_name`, because the `person_name` rule
#: matches an enumerated list of prefixes (`employee_`, `customer_`,
#: `patient_`, `contact_`, `first_`, `last_`, ...) and neither is in it.
#: A residential tenant and a maintenance engineer are both identifiable
#: people, so the lease and maintenance skills carry a counterparty guard
#: and an assignee guard that no deterministic control backs. This is
#: reported, not fixed from here: an operator's explicit entry in
#: `config/sensitive_columns.yaml` is the supported route, and changing
#: `governance/classification.py` is a governance change, not a skill one
#: (`CLAUDE.md` §4.7). `test_two_person_columns_this_domain_holds_are_not_classified`
#: pins the measured behaviour so the guard stops being needed the moment
#: it is no longer true.
#:
#: `cross_domain_allowed` is empty in both directions, and it matters here
#: the way it did for batch 15: occupancy per head takes its denominator
#: from HR, and cost per unit of output takes one from FINANCE. Only the
#: analytical half belongs to a skill -- name the denominator's source and
#: grain -- because `governance/request_policy.py` owns whether the join
#: may happen and this package must not restate it (see
#: `domain_skills/contract.py`).
_REAL_ESTATE = BusinessDomain.REAL_ESTATE

#: Declared in the supplied numeric order (01 portfolio, 02 occupancy, 03
#: lease, 04 facilities maintenance, 05 property financial), so the domain
#: default is Property Portfolio Intelligence (01). The prompts arrived
#: shuffled (01, 04, 05, 02, 03); numeric order wins.
REAL_ESTATE_SKILLS: tuple[DomainSkill, ...] = (
    DomainSkill(
        slug="real_estate.portfolio",
        title="Property Portfolio Intelligence",
        domain=_REAL_ESTATE,
        scope=_REAL_ESTATE_SCOPE,
        focus=(
            "The property portfolio as a stock at a date: how many assets "
            "of which class and tenure, where, how much area, and what "
            "changed. A count depends on what someone decided counts as "
            "one property -- a site, a building, a floor or a unit -- so "
            "give the grain before any total, because one asset can be "
            "several rows and several assets one row. Area is the "
            "denominator almost everything else here divides by and it "
            "has several definitions -- gross, net lettable, usable, "
            "rentable with a common-area factor -- so a per-square-metre "
            "figure is undefined until the basis is named, and two "
            "properties measured on different bases do not compare. "
            "Owned and leased assets are different exposures, one "
            "carrying capital and the other rent, so a total that mixes "
            "tenure answers no question; split it. The portfolio is a "
            "stock and not a flow, so a growth figure needs its as-of "
            "dates and whether the comparison is like-for-like or "
            "includes what was acquired and disposed in between. One "
            "column here behaves unlike anywhere else in the platform: a "
            "property is identified by its address, and an address is "
            "treated as personal data, so it comes back masked for a "
            "caller who may not see it and every masked row then reads "
            "alike -- label and group properties by identifier or name "
            "rather than by address, never read two masked rows as one "
            "property, and never report a masked value as a missing one. "
            "A large listing that selects the address is also capped at "
            "the domain's export ceiling for every caller, so treat such "
            "a result as a capped extract and say the cap applied before "
            "computing any count or share over it."
        ),
        dimensions=(
            "property",
            "asset class",
            "tenure",
            "region",
            "period",
        ),
    ),
    DomainSkill(
        slug="real_estate.occupancy",
        title="Occupancy Intelligence",
        domain=_REAL_ESTATE,
        scope=_REAL_ESTATE_SCOPE,
        focus=(
            "How much space is let and how much is actually used -- two "
            "unrelated measures that share the word occupancy. A building "
            "can be fully let and half used, so never blend a leased "
            "share with a utilisation rate or let one stand for the "
            "other: say which is being reported and against which "
            "denominator, since area, desks and seats give different "
            "answers and let, vacant, under fit-out, held for disposal "
            "and sublet need not sum to the whole. Utilisation is not "
            "observed but inferred from an instrument counting a proxy: a "
            "badge records an entry rather than a presence, a booking "
            "records an intention and not an attendance, and a network "
            "association counts devices. Name the sensing method and its "
            "coverage, because an uninstrumented floor reads as empty "
            "rather than as unknown, and a missing day is unknown and not "
            "zero -- closures, holidays and a part week deflate an "
            "average unless it is normalised to working days. A peak is "
            "an instant and an average hides it, so capacity keyed to the "
            "average under-provides; report both and never sum a peak. "
            "These records describe identifiable people, and at a fine "
            "grain a cell stands for one of them: a floor, a "
            "neighbourhood or a team with few occupants is not an "
            "aggregate, and nothing deterministic hides it here because "
            "suppression covers a sensitive measure and an occupant count "
            "is an ordinary integer. So report at a grain where no cell "
            "can stand for a single person, give the count beside every "
            "percentage, and never describe when a named person was "
            "present or characterise one person's attendance pattern."
        ),
        dimensions=(
            "property",
            "floor or neighbourhood",
            "space type",
            "business unit",
            "period",
        ),
    ),
    DomainSkill(
        slug="real_estate.lease",
        title="Lease Intelligence",
        domain=_REAL_ESTATE,
        scope=_REAL_ESTATE_SCOPE,
        focus=(
            "The lease portfolio as an obligation and an expiry profile: "
            "passing rent, escalations, incentives, options and what "
            "falls due when. Rent is not one number -- a headline rent "
            "and the effective rent after rent-free periods, fit-out "
            "contributions and capital incentives are different figures, "
            "so a comparison across leases with different incentive "
            "packages is not like-for-like until the basis is given or "
            "declared unknown. The date that governs the profile is "
            "rarely the end date: a break clause, a renewal option and a "
            "holdover each change when space actually returns, and an "
            "option belongs to whichever side holds it, so an expiry "
            "chart built on end dates overstates what is known. A "
            "weighted average unexpired term is four different numbers "
            "depending on whether it is weighted by rent or by area and "
            "run to break or to expiry, so name both choices. An "
            "escalation is contractual rather than forecast: a fixed "
            "uplift is known, an index-linked one is not until the index "
            "prints, and a market review is an opinion until it is "
            "agreed, so never present a modelled future rent as a "
            "recorded one. Rent per unit area inherits whichever area "
            "basis the portfolio carries, and a total across leases of "
            "different lengths or currencies is not a total until it is "
            "annualised with its exchange basis and date stated. A "
            "counterparty here may be a person rather than a company and "
            "a tenant name is not protected by anything deterministic, so "
            "report by tenant segment, sector or covenant grade rather "
            "than naming counterparties, and never characterise one "
            "tenant's payment behaviour or assess a named tenant's "
            "standing."
        ),
        dimensions=(
            "lease",
            "tenant segment",
            "property",
            "lease event",
            "period",
        ),
    ),
    DomainSkill(
        slug="real_estate.facilities_maintenance",
        title="Facilities Maintenance Intelligence",
        domain=_REAL_ESTATE,
        scope=_REAL_ESTATE_SCOPE,
        focus=(
            "Maintenance demand, responsiveness and what it costs, by "
            "asset class, site and trade. A work-order count measures how "
            "much was raised and not how much broke: a reactive order "
            "exists because somebody reported a fault, so a site with a "
            "responsive helpdesk looks worse than one where nothing is "
            "reported -- state the reporting channel and its coverage "
            "before comparing sites. Planned and reactive work are "
            "different populations and must not be summed into one "
            "volume: more planned work is a schedule being executed, "
            "more reactive work is a condition, and the ratio between "
            "them is the signal. A closure is a reported state, so a "
            "repeat or re-opened order on the same asset is the better "
            "evidence of whether a fix held. Response and resolution "
            "clocks are not the same clock as an attainment figure, "
            "because a target commonly pauses for travel, parts on order, "
            "access to occupied space and out-of-hours windows -- name "
            "which clock a figure ran on, and never average percentiles "
            "across sites. The age of open orders measures only the jobs "
            "nobody closed, so a site that clears routine work quickly "
            "can show an older backlog than one that clears nothing; "
            "report the backlog's size beside its age. A recorded cost "
            "may be labour only, a standard rate rather than an incurred "
            "amount, or missing parts entirely, and contractor invoices "
            "arrive late, so the most recent periods understate. A "
            "condition or criticality score is a judgement someone "
            "recorded on a scale that differs between surveyors, so it is "
            "not arithmetic and should not be averaged across surveys "
            "without saying so. The work is done by identifiable people "
            "whose names nothing here protects, so attribute to trade, "
            "vendor, site and asset class rather than to a named engineer, "
            "and never rank the people who carried out the work."
        ),
        dimensions=(
            "asset class",
            "site",
            "trade",
            "work order type",
            "period",
        ),
    ),
    DomainSkill(
        slug="real_estate.financial",
        title="Property Financial Intelligence",
        domain=_REAL_ESTATE,
        scope=_REAL_ESTATE_SCOPE,
        focus=(
            "What the portfolio earns and costs, and what it is carried "
            "at: income, recoveries, operating cost, capital spend and "
            "valuation, by property and line. Net operating income is a "
            "defined figure whose definition varies in exactly the places "
            "that matter -- which recoveries are in, which costs are "
            "non-recoverable, whether management fees or capital items "
            "count -- so use the approved definition and name it rather "
            "than composing one. Capital and operating spend are "
            "different bases, and a replacement charged to capital leaves "
            "the operating result looking better, so say which basis a "
            "cost figure is on. A gross service charge is not the owner's "
            "cost: recovery depends on what is recoverable and on how "
            "much is vacant, so cost per occupied unit area moves with "
            "occupancy rather than with efficiency. A valuation is an "
            "opinion at a date produced by a method, and an income-based "
            "one moves with its yield assumption as much as with the "
            "income, so name the basis and the as-of date, say whether it "
            "is a formal valuation, a desktop update or a book figure, "
            "and never present it as a price that could be realised. A "
            "yield or capitalisation rate divides two figures struck at "
            "different moments, and a portfolio yield is not the average "
            "of property yields -- recompose it from the income and value "
            "totals. Billing differs by market, in advance or in arrears "
            "and quarterly or monthly, so an income series needs its "
            "basis, and recoveries trued up after a year end make recent "
            "periods provisional, where a restatement is not a movement. "
            "Any per-unit-area figure inherits the portfolio's area basis "
            "and any multi-currency total needs its exchange basis and "
            "date. A single-let property's income is one counterparty's "
            "figure rather than an aggregate, so where that counterparty "
            "could be a person, report it at a grain that does not stand "
            "for one. Where a denominator comes from another domain -- a "
            "headcount or a unit of output -- name its source and grain, "
            "because a ratio across two grains is a new metric and not a "
            "conversion."
        ),
        dimensions=(
            "property",
            "income or cost line",
            "measurement basis",
            "region",
            "period",
        ),
    ),
)

#: Every registered skill, in declaration order. Later batches append their
#: own tuple here.
ALL_SKILLS: tuple[DomainSkill, ...] = (
    SALES_SKILLS
    + MIS_SKILLS
    + MARKETING_SKILLS
    + HR_SKILLS
    + ECOMMERCE_SKILLS
    + TRAINING_SKILLS
    + FINANCE_SKILLS
    + BFSI_SKILLS
    + SUPPLY_CHAIN_SKILLS
    + MANUFACTURING_SKILLS
    + CUSTOMER_SERVICE_SKILLS
    + PROJECT_SKILLS
    + IT_OPS_SKILLS
    + HEALTHCARE_SKILLS
    + ENERGY_SKILLS
    + RISK_AUDIT_SKILLS
    + LEGAL_SKILLS
    + REAL_ESTATE_SKILLS
)
