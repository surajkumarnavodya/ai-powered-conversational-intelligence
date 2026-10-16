"""The domain policy registry: one versioned policy per business domain.

Centralization is the point. Before this module, domain rules lived as a
flat tuple of regexes in `governance/request_policy.py` covering six
domains, with the domain itself only a label on each rule. The mission's
§33 asks instead for a declared policy per domain -- restricted
categories, prohibited actions, aggregation requirements, minimum group
size, role requirements, export rules -- so that adding a domain or
tightening one is a data change in one place, not new branches scattered
through nodes and prompts.

**What a `DomainPolicy` is and is not.** It is a declaration, evaluated
by `governance.request_policy`, `governance.result_policy`,
`governance.suppression` and `governance.recommendation_safety`. It is
not an executable rule language: every field is a typed value, never a
string an LLM (or a caller) could author. The same structural guarantee
`optimization/models.py` and `analytics/scenario.py` already make for
their own inputs.

**Versioning.** `GOVERNANCE_POLICY_VERSION` is recorded on every
decision, so an audit trail says which policy set produced a refusal.
Bump it whenever a change here could alter an outcome for unchanged
input.

**`PROHIBITED_*` actions are the mission's own "never" list** (§29),
expressed once and consumed by both the request gate (a question asking
for one) and the recommendation validator (a recommendation proposing
one). Keeping one list is what stops the two from drifting into
disagreement about what is forbidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from agent.authz import Permission
from governance.classification import DataCategory

#: Bumped when a policy change here could alter a decision for unchanged
#: input. Recorded on every `GovernanceVerdict` for audit.
GOVERNANCE_POLICY_VERSION = "1.0.0"


class BusinessDomain(StrEnum):
    """The nineteen domains this engine covers, plus a general fallback.

    `GENERAL` is not a twentieth domain: it is the policy applied when
    domain detection is not confident, and it is deliberately the
    *strictest* common denominator for personal data rather than the
    loosest, so an unrecognized question cannot route around a domain
    policy by being unrecognizable.

    Eighteen of the nineteen are the mission's own list. `CONSTRUCTION`
    is the one later addition, and it is recorded as such rather than
    folded in silently: it was requested after the series closed, and
    the policy below was *derived* here from the nearest neighbours
    (`PROJECT` for capital work, `REAL_ESTATE` for the built asset,
    `HR` for the people on site) rather than supplied by a prompt. A
    reader deciding whether to trust the declared prohibitions should
    know which ones were specified and which were reasoned.
    """

    HR = "hr"
    HEALTHCARE = "healthcare"
    BFSI = "bfsi"
    FINANCE = "finance"
    LEGAL = "legal"
    SALES_MARKETING = "sales_marketing"
    ECOMMERCE = "ecommerce"
    TRAINING = "training"
    MIS = "mis"
    SUPPLY_CHAIN = "supply_chain"
    MANUFACTURING = "manufacturing"
    CUSTOMER_SERVICE = "customer_service"
    PROJECT = "project"
    IT_OPERATIONS = "it_operations"
    REAL_ESTATE = "real_estate"
    CONSTRUCTION = "construction"
    ENERGY = "energy"
    RISK_AUDIT = "risk_audit"
    EXECUTIVE = "executive"
    GENERAL = "general"


class ProhibitedAction(StrEnum):
    """Decisions this system never makes or recommends about a person.

    Each is a *decision about an individual* with real-world
    consequence -- the category the mission forbids analytics from
    silently becoming (§29). Aggregate, organizational and process
    recommendations are unaffected.
    """

    INDIVIDUAL_EMPLOYMENT_ACTION = "individual_employment_action"
    INDIVIDUAL_COMPENSATION_DECISION = "individual_compensation_decision"
    INDIVIDUAL_PERFORMANCE_RANKING = "individual_performance_ranking"
    MEDICAL_DIAGNOSIS_OR_PRESCRIPTION = "medical_diagnosis_or_prescription"
    CLINICAL_DECISION = "clinical_decision"
    CREDIT_OR_LOAN_DECISION = "credit_or_loan_decision"
    FRAUD_ACCUSATION = "fraud_accusation"
    INDIVIDUALIZED_FINANCIAL_ADVICE = "individualized_financial_advice"
    LEGAL_CONCLUSION_OR_ADVICE = "legal_conclusion_or_advice"
    LIABILITY_DETERMINATION = "liability_determination"
    PROTECTED_CHARACTERISTIC_INFERENCE = "protected_characteristic_inference"
    SENSITIVE_PERSONAL_TARGETING = "sensitive_personal_targeting"
    CREDENTIAL_DISCLOSURE = "credential_disclosure"
    UNSUPPORTED_ALLEGATION = "unsupported_allegation"


#: Every `ProhibitedAction`, as one value. Used by the `GENERAL` fallback
#: policy: when the domain is unknown, no individual decision is safe to
#: make, so the fallback prohibits all of them rather than none.
ALL_PROHIBITED_ACTIONS: frozenset[ProhibitedAction] = frozenset(ProhibitedAction)

#: The floor every domain inherits, whatever its own policy declares.
#:
#: Without this, detecting a domain could *loosen* protection: a question
#: mentioning "machine downtime" scores for manufacturing, whose own
#: policy restricts only proprietary data, and personal data would then
#: be less protected than it is for an undetected question. A domain
#: policy may add restrictions; it can never drop one of these.
#:
#: Confined to categories where exposure harms an individual or leaks a
#: credential. Commercially confidential categories (contract,
#: proprietary, audit) are deliberately not in the floor -- they are
#: genuinely domain-specific, and forcing them everywhere would block
#: ordinary analytics in domains that have no such data.
BASELINE_RESTRICTED_CATEGORIES: frozenset[DataCategory] = frozenset(
    {
        DataCategory.PII,
        DataCategory.SPI,
        DataCategory.PHI,
        DataCategory.COMPENSATION,
        DataCategory.PERFORMANCE,
        DataCategory.CREDENTIAL,
        DataCategory.PAYMENT,
        DataCategory.BANKING,
    }
)


@dataclass(frozen=True)
class DomainPolicy:
    """One domain's declared governance policy.

    Attributes:
        domain: Which domain this governs.
        description: What the domain covers, for operators reading the
            registry.
        restricted_categories: Data categories that may not be returned
            as individual values to a caller without
            `required_permission`. Aggregates remain available.
        prohibited_actions: Decisions never made or recommended in this
            domain, regardless of role. No permission unlocks these.
        aggregation_required: True when individual-level rows of this
            domain's sensitive categories are withheld by default and
            only aggregates are offered.
        minimum_group_size: The k-anonymity threshold applied to this
            domain's grouped results (§22). `0` disables suppression for
            domains with no individual-level data to protect.
        required_permission: What a caller must hold to see this
            domain's restricted categories as values.
        export_row_limit: The largest number of rows this domain's
            sensitive data may be returned in at once, independent of
            the global row cap. `None` means only the global cap applies.
        cross_domain_allowed: Domains this one may be joined with
            without an explicit authorization step (§30). Joining
            outside this set requires `required_permission`.
        audit_required: True when every decision in this domain is an
            auditable event, including an allow.
    """

    domain: BusinessDomain
    description: str
    restricted_categories: frozenset[DataCategory] = frozenset()
    prohibited_actions: frozenset[ProhibitedAction] = frozenset()
    aggregation_required: bool = False
    minimum_group_size: int = 0
    required_permission: Permission = Permission.VIEW_RESTRICTED_COLUMNS
    export_row_limit: int | None = None
    cross_domain_allowed: frozenset[BusinessDomain] = field(default_factory=frozenset)
    audit_required: bool = False

    @property
    def effective_restricted_categories(self) -> frozenset[DataCategory]:
        """What this domain actually restricts: its own set plus the floor.

        **Always read this, never `restricted_categories` directly**, when
        deciding whether a value may be shown. The declared field says
        what the domain adds; this says what is enforced. Keeping the two
        separate is what makes the registry readable (each domain lists
        only what is special about it) while making the floor
        unavoidable (`BASELINE_RESTRICTED_CATEGORIES`).
        """
        return self.restricted_categories | BASELINE_RESTRICTED_CATEGORIES


_PERSONAL = frozenset({DataCategory.PII, DataCategory.SPI})
_EMPLOYEE_SENSITIVE = frozenset(
    {
        DataCategory.PII,
        DataCategory.SPI,
        DataCategory.COMPENSATION,
        DataCategory.PERFORMANCE,
        DataCategory.EMPLOYEE,
        DataCategory.PHI,
    }
)
_FINANCIAL_SENSITIVE = frozenset(
    {DataCategory.BANKING, DataCategory.PAYMENT, DataCategory.FINANCIAL}
)
_SECRETS = frozenset({DataCategory.CREDENTIAL})

#: Every domain's policy. The registry is exhaustive over
#: `BusinessDomain` -- `tests/test_governance_domains.py` asserts that, so
#: a new domain cannot be added to the enum without a policy.
DOMAIN_POLICIES: dict[BusinessDomain, DomainPolicy] = {
    BusinessDomain.HR: DomainPolicy(
        domain=BusinessDomain.HR,
        description="Workforce, employment, compensation, performance and absence data.",
        restricted_categories=_EMPLOYEE_SENSITIVE,
        prohibited_actions=frozenset(
            {
                ProhibitedAction.INDIVIDUAL_EMPLOYMENT_ACTION,
                ProhibitedAction.INDIVIDUAL_COMPENSATION_DECISION,
                ProhibitedAction.INDIVIDUAL_PERFORMANCE_RANKING,
                ProhibitedAction.PROTECTED_CHARACTERISTIC_INFERENCE,
                ProhibitedAction.UNSUPPORTED_ALLEGATION,
            }
        ),
        aggregation_required=True,
        minimum_group_size=5,
        export_row_limit=100,
        cross_domain_allowed=frozenset({BusinessDomain.TRAINING}),
        audit_required=True,
    ),
    BusinessDomain.HEALTHCARE: DomainPolicy(
        domain=BusinessDomain.HEALTHCARE,
        description="Patient, clinical and healthcare operational data.",
        restricted_categories=frozenset({DataCategory.PHI}) | _PERSONAL,
        prohibited_actions=frozenset(
            {
                ProhibitedAction.MEDICAL_DIAGNOSIS_OR_PRESCRIPTION,
                ProhibitedAction.CLINICAL_DECISION,
                ProhibitedAction.PROTECTED_CHARACTERISTIC_INFERENCE,
            }
        ),
        aggregation_required=True,
        minimum_group_size=5,
        export_row_limit=50,
        audit_required=True,
    ),
    BusinessDomain.BFSI: DomainPolicy(
        domain=BusinessDomain.BFSI,
        description="Banking, financial services and insurance customer data.",
        restricted_categories=_FINANCIAL_SENSITIVE | _PERSONAL,
        prohibited_actions=frozenset(
            {
                ProhibitedAction.CREDIT_OR_LOAN_DECISION,
                ProhibitedAction.FRAUD_ACCUSATION,
                ProhibitedAction.INDIVIDUALIZED_FINANCIAL_ADVICE,
                ProhibitedAction.UNSUPPORTED_ALLEGATION,
            }
        ),
        aggregation_required=True,
        minimum_group_size=5,
        export_row_limit=100,
        audit_required=True,
    ),
    BusinessDomain.FINANCE: DomainPolicy(
        domain=BusinessDomain.FINANCE,
        description="Corporate finance, payroll, tax and treasury data.",
        restricted_categories=_FINANCIAL_SENSITIVE
        | frozenset({DataCategory.COMPENSATION, DataCategory.PII}),
        prohibited_actions=frozenset({ProhibitedAction.INDIVIDUAL_COMPENSATION_DECISION}),
        aggregation_required=True,
        minimum_group_size=5,
        export_row_limit=100,
        cross_domain_allowed=frozenset({BusinessDomain.MIS, BusinessDomain.EXECUTIVE}),
        audit_required=True,
    ),
    BusinessDomain.LEGAL: DomainPolicy(
        domain=BusinessDomain.LEGAL,
        description="Legal matters, contracts, litigation and secretarial records.",
        restricted_categories=frozenset({DataCategory.LEGAL, DataCategory.CONTRACT}) | _PERSONAL,
        prohibited_actions=frozenset(
            {
                ProhibitedAction.LEGAL_CONCLUSION_OR_ADVICE,
                ProhibitedAction.LIABILITY_DETERMINATION,
                ProhibitedAction.UNSUPPORTED_ALLEGATION,
            }
        ),
        minimum_group_size=0,
        export_row_limit=50,
        audit_required=True,
    ),
    BusinessDomain.SALES_MARKETING: DomainPolicy(
        domain=BusinessDomain.SALES_MARKETING,
        description="Pipeline, campaign, segmentation and revenue analytics.",
        restricted_categories=_PERSONAL | frozenset({DataCategory.CUSTOMER}),
        prohibited_actions=frozenset(
            {
                ProhibitedAction.PROTECTED_CHARACTERISTIC_INFERENCE,
                ProhibitedAction.SENSITIVE_PERSONAL_TARGETING,
            }
        ),
        minimum_group_size=5,
        export_row_limit=500,
        cross_domain_allowed=frozenset({BusinessDomain.ECOMMERCE, BusinessDomain.CUSTOMER_SERVICE}),
    ),
    BusinessDomain.ECOMMERCE: DomainPolicy(
        domain=BusinessDomain.ECOMMERCE,
        description="Orders, carts, conversion and product performance.",
        restricted_categories=_PERSONAL | frozenset({DataCategory.PAYMENT, DataCategory.CUSTOMER}),
        prohibited_actions=frozenset({ProhibitedAction.SENSITIVE_PERSONAL_TARGETING}),
        minimum_group_size=5,
        export_row_limit=500,
        cross_domain_allowed=frozenset({BusinessDomain.SALES_MARKETING}),
    ),
    BusinessDomain.TRAINING: DomainPolicy(
        domain=BusinessDomain.TRAINING,
        description="Learning, certification and skills data.",
        restricted_categories=frozenset(
            {DataCategory.PERFORMANCE, DataCategory.EMPLOYEE, DataCategory.PII}
        ),
        prohibited_actions=frozenset(
            {
                ProhibitedAction.INDIVIDUAL_EMPLOYMENT_ACTION,
                ProhibitedAction.INDIVIDUAL_PERFORMANCE_RANKING,
            }
        ),
        aggregation_required=True,
        minimum_group_size=5,
        cross_domain_allowed=frozenset({BusinessDomain.HR}),
    ),
    BusinessDomain.MIS: DomainPolicy(
        domain=BusinessDomain.MIS,
        description="Management information and cross-functional KPI reporting.",
        restricted_categories=_EMPLOYEE_SENSITIVE | _FINANCIAL_SENSITIVE,
        aggregation_required=True,
        minimum_group_size=5,
        export_row_limit=200,
        cross_domain_allowed=frozenset({BusinessDomain.FINANCE, BusinessDomain.EXECUTIVE}),
        audit_required=True,
    ),
    BusinessDomain.SUPPLY_CHAIN: DomainPolicy(
        domain=BusinessDomain.SUPPLY_CHAIN,
        description="Procurement, sourcing, supplier and logistics data.",
        restricted_categories=frozenset(
            {DataCategory.CONTRACT, DataCategory.BANKING, DataCategory.PROPRIETARY}
        ),
        minimum_group_size=0,
        export_row_limit=500,
        cross_domain_allowed=frozenset({BusinessDomain.MANUFACTURING}),
    ),
    BusinessDomain.MANUFACTURING: DomainPolicy(
        domain=BusinessDomain.MANUFACTURING,
        description="Production, quality and plant operations data.",
        restricted_categories=frozenset({DataCategory.PROPRIETARY}),
        minimum_group_size=0,
        export_row_limit=500,
        cross_domain_allowed=frozenset({BusinessDomain.SUPPLY_CHAIN}),
    ),
    BusinessDomain.CUSTOMER_SERVICE: DomainPolicy(
        domain=BusinessDomain.CUSTOMER_SERVICE,
        description="Tickets, SLA, complaints and service quality.",
        restricted_categories=_PERSONAL | frozenset({DataCategory.CUSTOMER}),
        minimum_group_size=5,
        export_row_limit=500,
        cross_domain_allowed=frozenset({BusinessDomain.SALES_MARKETING}),
    ),
    BusinessDomain.PROJECT: DomainPolicy(
        domain=BusinessDomain.PROJECT,
        description="Projects, portfolios, allocations and project financials.",
        restricted_categories=frozenset(
            {DataCategory.CONTRACT, DataCategory.EMPLOYEE, DataCategory.PII}
        ),
        minimum_group_size=0,
        export_row_limit=500,
    ),
    BusinessDomain.IT_OPERATIONS: DomainPolicy(
        domain=BusinessDomain.IT_OPERATIONS,
        description="Infrastructure, incidents, security logs and service management.",
        restricted_categories=_SECRETS | frozenset({DataCategory.PROPRIETARY}),
        prohibited_actions=frozenset({ProhibitedAction.CREDENTIAL_DISCLOSURE}),
        minimum_group_size=0,
        export_row_limit=500,
        audit_required=True,
    ),
    BusinessDomain.REAL_ESTATE: DomainPolicy(
        domain=BusinessDomain.REAL_ESTATE,
        description="Property, lease, tenant and facilities data.",
        restricted_categories=_PERSONAL | frozenset({DataCategory.CONTRACT}),
        minimum_group_size=5,
        export_row_limit=500,
    ),
    # Derived, not supplied (see `BusinessDomain`'s docstring). Each
    # choice below is reasoned from what a construction estate actually
    # holds, and the reasoning is recorded because nobody can check it
    # against a source prompt:
    #
    # - `CONTRACT`: the domain is contractual throughout -- tenders,
    #   subcontract packages, rates, variations, retention. Same call
    #   `PROJECT` and `REAL_ESTATE` already make.
    # - `EMPLOYEE` and `_PERSONAL`: a site records the people on it, by
    #   name, including people employed by somebody else. `_PERSONAL` is
    #   already in the baseline floor and is declared for readability,
    #   as `REAL_ESTATE` declares it.
    # - `LIABILITY_DETERMINATION` and `UNSUPPORTED_ALLEGATION`: the
    #   question this domain invites is "whose fault was the delay or the
    #   defect", and that is adjudicated under a contract, never derived
    #   from a progress table. `RISK_AUDIT` prohibits the same pair for
    #   the same reason.
    # - The two individual actions: site data is per-person by
    #   construction -- operative hours, crew output, an injury with a
    #   name on it -- so the `HR` guard applies to people the
    #   organisation may not even employ. This is the first policy to
    #   carry both pairs at once.
    #
    # `minimum_group_size=5` because a trade crew is small: a "gang of
    # three" is three identifiable people. The ceiling matches its
    # neighbours rather than inventing a number.
    BusinessDomain.CONSTRUCTION: DomainPolicy(
        domain=BusinessDomain.CONSTRUCTION,
        description=(
            "Construction and capital works: sites, packages, progress, cost, "
            "subcontracts, quality and site safety."
        ),
        restricted_categories=_PERSONAL | frozenset({DataCategory.CONTRACT, DataCategory.EMPLOYEE}),
        prohibited_actions=frozenset(
            {
                ProhibitedAction.INDIVIDUAL_EMPLOYMENT_ACTION,
                ProhibitedAction.INDIVIDUAL_PERFORMANCE_RANKING,
                ProhibitedAction.LIABILITY_DETERMINATION,
                ProhibitedAction.UNSUPPORTED_ALLEGATION,
            }
        ),
        minimum_group_size=5,
        export_row_limit=500,
    ),
    BusinessDomain.ENERGY: DomainPolicy(
        domain=BusinessDomain.ENERGY,
        description="Energy consumption, emissions and sustainability metrics.",
        restricted_categories=frozenset({DataCategory.PROPRIETARY}),
        minimum_group_size=0,
        export_row_limit=500,
    ),
    BusinessDomain.RISK_AUDIT: DomainPolicy(
        domain=BusinessDomain.RISK_AUDIT,
        description="Enterprise risk, internal audit and investigations.",
        restricted_categories=frozenset({DataCategory.AUDIT, DataCategory.LEGAL}) | _PERSONAL,
        prohibited_actions=frozenset(
            {ProhibitedAction.UNSUPPORTED_ALLEGATION, ProhibitedAction.LIABILITY_DETERMINATION}
        ),
        minimum_group_size=5,
        export_row_limit=50,
        audit_required=True,
    ),
    BusinessDomain.EXECUTIVE: DomainPolicy(
        domain=BusinessDomain.EXECUTIVE,
        description="Executive reporting and board-level analytics.",
        restricted_categories=_EMPLOYEE_SENSITIVE | _FINANCIAL_SENSITIVE,
        aggregation_required=True,
        minimum_group_size=5,
        export_row_limit=200,
        cross_domain_allowed=frozenset({BusinessDomain.MIS, BusinessDomain.FINANCE}),
        audit_required=True,
    ),
    # The fallback. Strict on personal data on purpose: an unrecognized
    # question must not become the loosest path through the engine.
    BusinessDomain.GENERAL: DomainPolicy(
        domain=BusinessDomain.GENERAL,
        description="Applied when domain detection is not confident.",
        # Every prohibited action, not none: if the domain is unknown, no
        # decision about an individual is safe to make or recommend.
        prohibited_actions=ALL_PROHIBITED_ACTIONS,
        restricted_categories=_PERSONAL
        | _SECRETS
        | frozenset(
            {
                DataCategory.COMPENSATION,
                DataCategory.PERFORMANCE,
                DataCategory.PHI,
                DataCategory.PAYMENT,
                DataCategory.BANKING,
                DataCategory.LEGAL,
            }
        ),
        minimum_group_size=5,
        export_row_limit=500,
    ),
}


def policy_for(domain: BusinessDomain) -> DomainPolicy:
    """The policy for one domain, falling back to `GENERAL`.

    The fallback can only be reached by a caller passing a domain the
    registry does not cover, which the registry-exhaustiveness test
    makes impossible for a declared member -- it exists so a future
    dynamic domain string cannot produce a `KeyError` on the request
    path.
    """
    return DOMAIN_POLICIES.get(domain, DOMAIN_POLICIES[BusinessDomain.GENERAL])


def minimum_group_size_for(domain: BusinessDomain, configured_default: int) -> int:
    """The k-anonymity threshold for a domain.

    A domain's own threshold wins when it declares one; otherwise the
    operator's configured default applies. A domain declaring `0` means
    "no individuals to protect here" and is honored as a real zero, not
    treated as unset -- manufacturing throughput has no small-group risk,
    and suppressing it would destroy legitimate analytics.
    """
    policy = policy_for(domain)
    return policy.minimum_group_size if policy.minimum_group_size else configured_default


def prohibited_actions_for(domain: BusinessDomain) -> frozenset[ProhibitedAction]:
    return policy_for(domain).prohibited_actions


def is_cross_domain_allowed(source: BusinessDomain, target: BusinessDomain) -> bool:
    """Whether two domains may be combined without explicit authorization.

    Reflexive (a domain with itself) and otherwise only where the source
    policy names the target. Deliberately **not** symmetric by
    construction: HR may be combined with training, but a training
    question must not silently pull compensation data. Where symmetry is
    intended, both policies declare it.
    """
    if source is target:
        return True
    return target in policy_for(source).cross_domain_allowed


__all__ = [
    "DOMAIN_POLICIES",
    "GOVERNANCE_POLICY_VERSION",
    "BusinessDomain",
    "DomainPolicy",
    "ProhibitedAction",
    "is_cross_domain_allowed",
    "minimum_group_size_for",
    "policy_for",
    "prohibited_actions_for",
]
