"""Centralized data classification: what a field is, and what that implies.

The single source of truth for "how sensitive is this column", replacing
the scattered, implicit answers the codebase had before. Everything that
needs to know whether a value may be shown, sampled, logged, aggregated
or exported asks this module.

**Why this exists even though `config/sensitive_columns.yaml` already
did.** That file is authoritative and stays so -- a human-reviewed
classification always wins here. But it ships as an empty template
(`tables: []`), by deliberate design, so until an operator classifies
each column by hand *nothing* is classified, and the two enforcement
gates built on it (`agent.sql_validator.find_restricted_column_references`
and `governance.result_policy`) protect nothing at all on a real
database. A governance engine that only works after someone hand-lists
several hundred columns is a governance engine that does not work.

So classification is two-layered, and the order matters:

1. **Explicit**, from `config/sensitive_columns.yaml` -- a reviewed human
   decision, mapped onto this module's taxonomy. Always wins, including
   when it *loosens* (an operator who reviewed a column named
   `salary_band_label` and called it `public` meant it).
2. **Inferred**, from the column (and table) name, using the pattern
   registry below. Never overrides an explicit decision, and is honest
   about what it is: a name is not a guarantee of content, so an inferred
   classification carries `inferred=True` and the reason that matched.

**Relationship to `onboarding/pii_detection.py`.** That module answers a
different question at a different time: it proposes PII findings with
confidence scores and optional data sampling for a *human SME to confirm*
during onboarding. This module makes a binary runtime enforcement
decision on the request path, over a wider set of data families than PII
(compensation, clinical, legal, credentials, payment, proprietary). The
two deliberately stay separate; `tests/test_governance_classification.py`
asserts that every category onboarding can detect is also classified
here, so their coverage cannot silently diverge.

**Nothing here reads data.** Classification is a function of names and
configuration only -- no query, no sampling, no LLM. That is what lets it
run on the request path, inside the SQL gate, and over a result set
without cost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from functools import lru_cache

from config.sensitive_columns import SensitivityTier, load_sensitive_columns


class DataClassification(IntEnum):
    """How sensitive a field is, ordered so comparisons are meaningful.

    An `IntEnum` rather than a `StrEnum` because the useful questions are
    comparative ("is this at or above CONFIDENTIAL?"), and encoding that
    order in the type removes the need for a separate ranking table that
    could drift.

    Attributes:
        PUBLIC: No restriction. The default for an unmatched field --
            never restricted by omission.
        INTERNAL: Ordinary business data, not for external disclosure.
        CONFIDENTIAL: Commercially or organizationally sensitive
            (contract pricing, proprietary process data, audit findings).
        SENSITIVE: Personal data about an identifiable individual (PII).
        HIGHLY_SENSITIVE: Special-category personal data, or data whose
            disclosure directly harms an individual (compensation,
            appraisal, clinical, protected characteristics).
        REGULATED: Data whose handling is externally constrained and
            which this system never discloses as a value (credentials,
            payment instruments, government identifiers).
    """

    PUBLIC = 0
    INTERNAL = 10
    CONFIDENTIAL = 20
    SENSITIVE = 30
    HIGHLY_SENSITIVE = 40
    REGULATED = 50


class DataCategory(StrEnum):
    """What *kind* of data a field holds -- the mission's own named list.

    Orthogonal to `DataClassification`: the category says what it is, the
    classification says how guarded it must be. Two categories can share
    a classification (compensation and clinical are both
    HIGHLY_SENSITIVE) while needing different policy text, which is why
    both are carried.
    """

    NONE = "none"
    PII = "pii"
    SPI = "spi"
    PHI = "phi"
    FINANCIAL = "financial"
    BANKING = "banking"
    PAYMENT = "payment"
    EMPLOYEE = "employee"
    COMPENSATION = "compensation"
    PERFORMANCE = "performance"
    LEGAL = "legal"
    CONTRACT = "contract"
    CREDENTIAL = "credential"
    CUSTOMER = "customer"
    PROPRIETARY = "proprietary"
    AUDIT = "audit"


class MaskingStrategy(StrEnum):
    """How a protected value is rendered when it may not be shown as-is.

    `SUPPRESS` and `MASK` differ in what they admit: a masked cell says
    "there is a value here you may not see", a suppressed one says
    "nothing is reported for this cell". Suppression is used where the
    existence of a value is itself disclosive (small-group statistics).
    """

    NONE = "none"
    MASK = "mask"
    PARTIAL = "partial"
    HASH = "hash"
    SUPPRESS = "suppress"


#: Categories this system never renders as a value, for any role.
#: Unlike every other classification, no permission unlocks these: a
#: credential or a payment instrument has no legitimate analytics use,
#: so there is no role for which showing one is the right answer
#: (mission §2.I, §18). `governance.result_policy` enforces this
#: independently of the caller's permissions.
NEVER_DISCLOSED_CATEGORIES: frozenset[DataCategory] = frozenset(
    {DataCategory.CREDENTIAL, DataCategory.PAYMENT}
)


@dataclass(frozen=True)
class FieldPolicy:
    """What is known about one field, and what follows from it.

    The metadata set the mission's §3 asks the semantic layer to carry,
    as one value object rather than a dozen parallel lookups.
    """

    classification: DataClassification
    category: DataCategory = DataCategory.NONE
    masking: MaskingStrategy = MaskingStrategy.NONE
    #: True when this field may only appear in an aggregate, never as a
    #: per-individual value.
    aggregation_required: bool = False
    #: True when access to this field is itself an auditable event.
    audit_required: bool = False
    #: Why this classification was reached. A pattern name for an
    #: inferred one, `"configured"` for an explicit one. Recorded in
    #: audit events; never shown to the caller.
    reason: str = "default"
    #: False when a human classified this field in
    #: `config/sensitive_columns.yaml`, True when a name pattern did.
    inferred: bool = True

    @property
    def is_never_disclosed(self) -> bool:
        return self.category in NEVER_DISCLOSED_CATEGORIES

    @property
    def requires_permission(self) -> bool:
        """Whether a caller needs `VIEW_RESTRICTED_COLUMNS` to see values.

        SENSITIVE and above. CONFIDENTIAL is deliberately *not* included:
        commercially confidential business data (contract pricing, audit
        findings) is governed per domain by `governance.domains`, not by
        the personal-data permission, and conflating the two would both
        over-block ordinary analytics and under-protect individuals.
        """
        return self.classification >= DataClassification.SENSITIVE


PUBLIC_FIELD = FieldPolicy(classification=DataClassification.PUBLIC, reason="unclassified")


@dataclass(frozen=True)
class _Pattern:
    """One classification rule, matched against a column name."""

    name: str
    pattern: re.Pattern[str]
    classification: DataClassification
    category: DataCategory
    masking: MaskingStrategy = MaskingStrategy.MASK
    aggregation_required: bool = False
    audit_required: bool = False


# Ordered most-specific first: the first match wins, so a narrower rule
# must precede any broader one it would also satisfy. Two concrete cases
# this ordering exists for: `ip_address` before the deliberately unbounded
# `address` rule (an IP is infrastructure data, not a home address), and
# every credential/payment rule before the generic `account`/`number`
# shapes further down.
#
# Patterns avoid `\b` around joined words on purpose. A real-world column
# is routinely `PascalCase` or `camelCase` (`StreetAddress`,
# `AnnualSalary`, `PassportNumber`) with no word boundary between the
# parts, so a bounded pattern would miss the most common naming
# convention in the databases this runs against. The same reasoning
# `onboarding/pii_detection.py` documents for its own patterns.
_PATTERNS: tuple[_Pattern, ...] = (
    # --- REGULATED: never disclosed as a value, for any role -----------
    _Pattern(
        "credential",
        re.compile(
            r"password|passwd|\bpwd\b|secret|api[-_]?key|access[-_]?token|refresh[-_]?token"
            r"|private[-_]?key|client[-_]?secret|connection[-_]?string|conn[-_]?str"
            r"|credential|\bsalt\b|password[-_]?hash|\bhash(?:ed)?[-_]?password"
            r"|auth[-_]?token|bearer[-_]?token|session[-_]?token|\botp\b|two[-_]?factor",
            re.IGNORECASE,
        ),
        DataClassification.REGULATED,
        DataCategory.CREDENTIAL,
        MaskingStrategy.SUPPRESS,
        audit_required=True,
    ),
    _Pattern(
        "payment_instrument",
        re.compile(
            r"card[-_]?number|credit[-_]?card|\bcc[-_]?num|\bcvv\b|\bcvc\b|card[-_]?verification"
            r"|\bpin\b|pin[-_]?code|\biban\b|\bswift\b|\bbic\b|routing[-_]?number"
            r"|bank[-_]?account|account[-_]?number|acct[-_]?no|sort[-_]?code"
            r"|card[-_]?holder|expiry[-_]?date|exp[-_]?month|exp[-_]?year",
            re.IGNORECASE,
        ),
        DataClassification.REGULATED,
        DataCategory.PAYMENT,
        MaskingStrategy.SUPPRESS,
        audit_required=True,
    ),
    _Pattern(
        "government_id",
        re.compile(
            r"\bssn\b|social[-_ ]?security|national[-_]?id|\bnin\b|\baadhaar\b|\bpan[-_]?no"
            r"|passport|tax[-_]?id|\btin\b|driver.?s?[-_]?licen[cs]e|licen[cs]e[-_]?number"
            r"|voter[-_]?id",
            re.IGNORECASE,
        ),
        DataClassification.REGULATED,
        DataCategory.PII,
        MaskingStrategy.SUPPRESS,
        audit_required=True,
    ),
    # --- HIGHLY_SENSITIVE: individual harm on disclosure --------------
    _Pattern(
        "compensation",
        re.compile(
            r"salary|salaries|\bwage|\bpay[-_]?rate|\bpayrate|base[-_]?pay|gross[-_]?pay"
            # A bare "<something>_pay"/"monthly pay" alias was unclassified
            # until a security test asked for `monthly_pay` -- the most
            # natural alias a generated query would produce for a salary
            # column. Anchored on a preceding separator or the start of
            # the name so it does not match "payment_date" or "payable".
            r"|(?:^|[-_\s])pay(?:[-_\s](?:amount|total|out))?$|(?:^|[-_\s])pay[-_\s]amount"
            r"|net[-_]?pay|\bctc\b|compensation|\bbonus|incentive[-_]?amount|commission[-_]?amount"
            r"|\bstipend|payslip|take[-_]?home|annual[-_]?income|hourly[-_]?rate"
            # `annual_income` was here and `yearly_income` was not, so
            # `DimCustomer.YearlyIncome` was returned in the clear to a
            # caller without VIEW_RESTRICTED_COLUMNS while the identical
            # `AnnualIncome` would have masked. Measured against the live
            # app 2026-10-30 (Phase 9, `UI-012`): the same five rows came
            # back with FirstName/LastName/EmailAddress/Phone masked and
            # `YearlyIncome` = 90000.0 beside them.
            #
            # Deliberately `yearly[-_]?income` and not a bare `income`:
            # `net_income`, `income_statement` and `operating_income` are
            # ordinary company financials, and CLAUDE.md 5.8 names
            # over-blocking normal analytics as its own failure mode. A
            # bare-`income` rule is a policy decision, not a defect fix --
            # see docs/uat-readiness/phase-9-ui.md.
            r"|yearly[-_]?income" r"|stock[-_]?option|\besop\b|severance",
            re.IGNORECASE,
        ),
        DataClassification.HIGHLY_SENSITIVE,
        DataCategory.COMPENSATION,
        MaskingStrategy.MASK,
        aggregation_required=True,
        audit_required=True,
    ),
    _Pattern(
        "clinical",
        re.compile(
            r"diagnos|\bicd[-_]?\d*|medical[-_]?(?:history|record|condition)|\bdisease"
            r"|medication|prescription|\bdrug[-_]?name|\bdosage|treatment[-_]?plan"
            r"|lab[-_]?result|test[-_]?result|clinical[-_]?note|blood[-_]?(?:group|type|pressure)"
            r"|allerg|\bsymptom|disability|\bpregnan|mental[-_]?health|\btherapy",
            re.IGNORECASE,
        ),
        DataClassification.HIGHLY_SENSITIVE,
        DataCategory.PHI,
        MaskingStrategy.SUPPRESS,
        aggregation_required=True,
        audit_required=True,
    ),
    _Pattern(
        "protected_characteristic",
        re.compile(
            r"\brace\b|\bethnic|religio|\bcaste\b|political|sexual[-_]?orientation"
            r"|marital[-_]?status|\bgender\b|\bsex\b|\bcitizenship|nationality"
            r"|veteran[-_]?status|union[-_]?member",
            re.IGNORECASE,
        ),
        DataClassification.HIGHLY_SENSITIVE,
        DataCategory.SPI,
        MaskingStrategy.MASK,
        aggregation_required=True,
        audit_required=True,
    ),
    _Pattern(
        "performance",
        re.compile(
            r"appraisal|performance[-_]?(?:rating|score|review|rank)|\brating\b"
            r"|manager[-_]?(?:feedback|comment)|review[-_]?comment|disciplinar"
            r"|\bpip\b|improvement[-_]?plan|termination[-_]?reason|exit[-_]?reason"
            r"|attrition[-_]?risk|flight[-_]?risk|promotion[-_]?recommend",
            re.IGNORECASE,
        ),
        DataClassification.HIGHLY_SENSITIVE,
        DataCategory.PERFORMANCE,
        MaskingStrategy.MASK,
        aggregation_required=True,
        audit_required=True,
    ),
    _Pattern(
        "legal_privileged",
        re.compile(
            r"privileged|attorney|\bcounsel\b|legal[-_]?(?:advice|opinion|note|strategy)"
            r"|litigation|settlement[-_]?(?:amount|term)|investigation[-_]?(?:detail|finding)"
            r"|\ballegation|whistleblow",
            re.IGNORECASE,
        ),
        DataClassification.HIGHLY_SENSITIVE,
        DataCategory.LEGAL,
        MaskingStrategy.SUPPRESS,
        audit_required=True,
    ),
    # --- SENSITIVE: personal data about an identifiable individual ----
    _Pattern(
        "ip_address",
        re.compile(r"\bip[-_ ]?address\b|\bip[-_ ]?addr\b|\bipv4\b|\bipv6\b", re.IGNORECASE),
        DataClassification.CONFIDENTIAL,
        DataCategory.PROPRIETARY,
        MaskingStrategy.PARTIAL,
    ),
    _Pattern(
        "contact",
        re.compile(
            r"e[-_]?mail|\bphone|mobile|\bcell\b|telephone|\bfax\b|contact[-_]?(?:number|no)",
            re.IGNORECASE,
        ),
        DataClassification.SENSITIVE,
        DataCategory.PII,
        MaskingStrategy.PARTIAL,
    ),
    _Pattern(
        "address",
        re.compile(
            r"address|street|\baddr\d?\b|postal[-_]?code|\bzip[-_]?code|\bpincode", re.IGNORECASE
        ),
        DataClassification.SENSITIVE,
        DataCategory.PII,
        MaskingStrategy.MASK,
    ),
    _Pattern(
        "person_name",
        re.compile(
            r"first[-_]?name|last[-_]?name|middle[-_]?name|full[-_]?name|\bsurname\b"
            r"|\bfname\b|\blname\b|display[-_]?name|employee[-_]?name|customer[-_]?name"
            r"|patient[-_]?name|contact[-_]?name",
            re.IGNORECASE,
        ),
        DataClassification.SENSITIVE,
        DataCategory.PII,
        MaskingStrategy.MASK,
    ),
    _Pattern(
        "date_of_birth",
        re.compile(r"\bdob\b|date[-_ ]?of[-_ ]?birth|birth[-_ ]?date|birthday", re.IGNORECASE),
        DataClassification.SENSITIVE,
        DataCategory.PII,
        MaskingStrategy.MASK,
    ),
    # --- CONFIDENTIAL: commercially sensitive, not personal -----------
    _Pattern(
        "contract_terms",
        re.compile(
            r"contract[-_]?(?:value|price|rate|term)|negotiated[-_]?(?:rate|price)"
            r"|lease[-_]?(?:term|amount)|discount[-_]?rate|margin[-_]?pct|cost[-_]?price"
            r"|supplier[-_]?(?:price|rate)|vendor[-_]?(?:price|rate)|rate[-_]?card",
            re.IGNORECASE,
        ),
        DataClassification.CONFIDENTIAL,
        DataCategory.CONTRACT,
    ),
    _Pattern(
        "proprietary_process",
        re.compile(
            r"recipe|formula|\bbom\b|bill[-_]?of[-_]?material|process[-_]?parameter"
            r"|trade[-_]?secret|proprietary|tolerance[-_]?spec|batch[-_]?formula",
            re.IGNORECASE,
        ),
        DataClassification.CONFIDENTIAL,
        DataCategory.PROPRIETARY,
    ),
    _Pattern(
        "audit_finding",
        re.compile(
            r"audit[-_]?(?:finding|observation|note)|control[-_]?weakness|risk[-_]?assessment"
            r"|remediation[-_]?(?:plan|note)|\bnon[-_]?conformance",
            re.IGNORECASE,
        ),
        DataClassification.CONFIDENTIAL,
        DataCategory.AUDIT,
        audit_required=True,
    ),
)

#: `config/sensitive_columns.yaml`'s three tiers, mapped onto this
#: taxonomy. `restricted` lands on HIGHLY_SENSITIVE rather than REGULATED
#: because a human marking a column restricted means "needs permission",
#: not "never disclosable to anyone" -- the latter is reserved for
#: categories that have no authorized viewer at all.
_TIER_TO_CLASSIFICATION: dict[SensitivityTier, DataClassification] = {
    "public": DataClassification.PUBLIC,
    "internal": DataClassification.INTERNAL,
    "restricted": DataClassification.HIGHLY_SENSITIVE,
}


def _infer(column: str) -> FieldPolicy | None:
    """The first pattern matching this column name, or `None`."""
    for rule in _PATTERNS:
        if rule.pattern.search(column):
            return FieldPolicy(
                classification=rule.classification,
                category=rule.category,
                masking=rule.masking,
                aggregation_required=rule.aggregation_required,
                audit_required=rule.audit_required,
                reason=rule.name,
                inferred=True,
            )
    return None


@lru_cache(maxsize=4096)
def classify_field(table: str, column: str) -> FieldPolicy:
    """The policy for one field. Explicit configuration wins over inference.

    Args:
        table: The table name. `""` when only a result column name is
            known (a result set names columns by alias, not by origin).
        column: The column or alias name.

    Returns:
        A `FieldPolicy`. `PUBLIC_FIELD` for anything unmatched -- a field
        is never restricted merely because it was not recognized.

    Cached because this is called once per column per request and the
    answer is a pure function of configuration plus the name. The cache
    is cleared by `reset_classification_cache` when configuration is
    reloaded in a test.
    """
    configured = load_sensitive_columns()
    tier = configured.get((table, column))
    if tier is None and table:
        # A result column may be aliased, so also honor a configuration
        # entry for the same column name under any table.
        for (configured_table, configured_column), value in configured.items():
            if configured_column == column and configured_table != table:
                tier = value
                break
    if tier is not None:
        classification = _TIER_TO_CLASSIFICATION[tier]
        inferred = _infer(column)
        # Keep the inferred *category* (it is descriptive, and a human
        # classifying a tier says nothing about which data family it is)
        # while the human's classification level governs.
        return FieldPolicy(
            classification=classification,
            category=inferred.category if inferred else DataCategory.NONE,
            masking=(
                MaskingStrategy.MASK
                if classification >= DataClassification.SENSITIVE
                else MaskingStrategy.NONE
            ),
            aggregation_required=bool(inferred and inferred.aggregation_required),
            audit_required=classification >= DataClassification.SENSITIVE,
            reason="configured",
            inferred=False,
        )

    return _infer(column) or PUBLIC_FIELD


def reset_classification_cache() -> None:
    """Clears the memoized classifications.

    Needed only when `config/sensitive_columns.yaml` is reloaded within
    one process, which in practice means a test that rewrites it.
    """
    classify_field.cache_clear()


def classify_columns(columns: list[str], table: str = "") -> dict[str, FieldPolicy]:
    """Classifies a result set's column names in one pass."""
    return {column: classify_field(table, column) for column in columns}


def highest_classification(policies: list[FieldPolicy]) -> DataClassification:
    """The most sensitive classification among several fields.

    What a request or result as a whole is judged by -- a result is as
    sensitive as its most sensitive column, not its average.
    """
    return max((policy.classification for policy in policies), default=DataClassification.PUBLIC)


__all__ = [
    "NEVER_DISCLOSED_CATEGORIES",
    "PUBLIC_FIELD",
    "DataCategory",
    "DataClassification",
    "FieldPolicy",
    "MaskingStrategy",
    "classify_columns",
    "classify_field",
    "highest_classification",
    "reset_classification_cache",
]
