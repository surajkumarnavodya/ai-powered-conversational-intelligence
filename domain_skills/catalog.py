"""The declared domain skills.

**This file is the only place a skill is introduced.** Adding one is a few
lines here; there is deliberately no API route, no admin surface and no
settings field that can add or edit a skill at runtime — see
`domain_skills/types.py` on why a skill must stay an in-code constant.

Batch 1 registers the five Sales skills supplied as
`01_revenue_intelligence.md` … `05_pricing_and_discount_intelligence.md`.
All five shared an identical body and an identical "Domain scope" sentence,
differing only in their heading, so the scope is written once as
`_SALES_SCOPE` and each skill carries only its own focus.

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

#: Every registered skill, in declaration order. Later batches append their
#: own domain tuple here.
ALL_SKILLS: tuple[DomainSkill, ...] = SALES_SKILLS
