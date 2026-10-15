"""The declared domain skills.

**This file is the only place a skill is introduced.** Adding one is a few
lines here; there is deliberately no API route, no admin surface and no
settings field that can add or edit a skill at runtime — see
`domain_skills/types.py` on why a skill must stay an in-code constant.

Batches so far: Sales, MIS / Management Reporting, Marketing, HR,
eCommerce, Training / L&D, Finance & Corporate Functions, BFSI, Supply
Chain / Procurement & Logistics, Manufacturing & Operations, Customer
Service & Support, Project & Portfolio Management, IT Operations &
Service Management. Every batch has arrived with an identical
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
#: `ProhibitedAction` and `minimum_group_size=0`, so neither the derived
#: constraint block nor small-group suppression applies -- while it does
#: restrict contract, employee and PII data. Resource allocation is
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
#: `minimum_group_size=0` still applies and ITSM records name the assignee
#: of a ticket, so the individual-ranking guard arrives for the sixth time
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
)
