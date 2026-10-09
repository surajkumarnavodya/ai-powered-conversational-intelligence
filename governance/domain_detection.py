"""Which business domain a request belongs to.

Deterministic and keyword-based, deliberately not an LLM call. Three
reasons: the domain decides which policy applies, so a model declining
or hallucinating a domain would change what is enforced; it runs on
every request, before any LLM is warmed; and a refusal must be
reproducible for audit.

**Two signals, schema first.** A referenced table or column name is much
stronger evidence than question wording -- `employee_compensation` is HR
whatever the sentence around it says -- so schema evidence outranks text
evidence. Text alone decides only when no schema is known yet, which is
the case at the request gate (domain detection runs before schema
retrieval).

**Ambiguity is reported, not resolved.** When two domains tie, both are
returned and the caller decides what that means:
`governance.request_policy` applies the stricter of the two, and a
cross-domain join check (§30) treats a genuine tie as exactly the
multi-domain case it is meant to catch. Collapsing a tie to one winner
here would hide the thing the policy needs to see.

**Low confidence falls back to `GENERAL`**, whose policy is the strictest
common denominator for personal data (see `governance.domains`) -- an
unrecognized question must not take the loosest path.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from governance.domains import BusinessDomain

#: Weight given to a table/column-name hit versus a question-text hit.
#: Schema evidence is near-certain; wording is suggestive.
_SCHEMA_WEIGHT = 3
_TEXT_WEIGHT = 1

#: Minimum score before a domain is claimed at all.
#:
#: One question-text hit is enough, which is safe *only* because
#: `governance.domains.BASELINE_RESTRICTED_CATEGORIES` guarantees no
#: domain policy protects personal data less than the `GENERAL` fallback
#: does. Claiming a domain can therefore only ever add restrictions and
#: domain-specific prohibited actions -- never remove one. Before that
#: floor existed, a single weak wording hit could have routed a question
#: onto a laxer policy, so this threshold was deliberately set above
#: text evidence; with the floor in place, the stricter behaviour is to
#: let wording claim a domain.
_MIN_SCORE = _TEXT_WEIGHT


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


#: Splits a camelCase/PascalCase boundary so `EmployeePayroll` becomes
#: two words.
_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def normalize_schema_text(names: Sequence[str]) -> str:
    """Schema names as space-separated words, so `` anchors work.

    A real column or table name is `employee_payroll` or
    `EmployeePayroll`, and in both forms a ``-anchored pattern fails on
    the second word: an underscore is a word character, and a case
    transition is not a word boundary. Found by this module's own tests --
    `crm_opportunity` matched no domain at all before this existed.

    Normalizing the input rather than dropping `` from every pattern
    keeps the patterns readable and keeps them from matching inside an
    unrelated longer word.
    """
    joined = " ".join(names)
    spaced = _CASE_BOUNDARY.sub(" ", joined)
    return spaced.replace("_", " ").replace("-", " ")


# Patterns are matched against both question text and schema names, so
# they avoid `\b` around joined words for the same PascalCase reason
# `governance/classification.py` documents.
_DOMAIN_PATTERNS: tuple[tuple[BusinessDomain, re.Pattern[str]], ...] = (
    (
        BusinessDomain.HR,
        _p(
            r"employee|headcount|attrition|payroll|appraisal|recruit|onboarding[-_]?employee"
            r"|workforce|\bhrms?\b|\bhr\b|absenteeism|\bleave\b|designation|\bgrade\b"
            r"|manager[-_]?feedback|resignation|\bhiring\b|job[-_]?level|\bstaff\b"
        ),
    ),
    (
        BusinessDomain.HEALTHCARE,
        _p(
            r"patient|diagnos|clinic|hospital|\bward\b|\bbed[-_\s]?occupanc|physician|\bnurse"
            r"|medication|prescription|treatment|\bicd\b|lab[-_]?result|appointment[-_]?slot"
            r"|admission|discharge|\bemr\b|\behr\b"
        ),
    ),
    (
        BusinessDomain.BFSI,
        _p(
            r"\bloan|\bcredit[-_]?(?:score|limit|card|decision)|\bmortgage|underwrit"
            r"|insurance|\bpolicy[-_]?holder|premium|\bclaim[-_]?amount|\bkyc\b|\baml\b"
            r"|fraud|\bbranch[-_]?code|\bdeposit|\bnpa\b|borrower"
        ),
    ),
    (
        BusinessDomain.FINANCE,
        _p(
            r"general[-_]?ledger|\bgl[-_]?account|trial[-_]?balance|accounts[-_]?(?:payable|receivable)"
            r"|\bap\b|\bar\b|invoice|\btax\b|\bgst\b|\bvat\b|treasury|budget|cost[-_]?cent"
            r"|profit[-_]?and[-_]?loss|\bp&l\b|balance[-_]?sheet|cash[-_]?flow"
        ),
    ),
    (
        BusinessDomain.LEGAL,
        _p(
            r"\blegal\b|litigation|attorney|\bcounsel\b|privileged|contract[-_]?clause"
            r"|\bmatter[-_]?id|board[-_]?resolution|secretarial|compliance[-_]?filing"
            r"|\bnda\b|liability|settlement"
        ),
    ),
    (
        BusinessDomain.SALES_MARKETING,
        _p(
            r"\blead\b|\bopportunit|pipeline|\bquota\b|campaign|\bctr\b|impression"
            r"|segmentation|\bchurn[-_]?rate|funnel|\bcrm\b|\bmql\b|\bsql[-_]?lead"
            r"|conversion[-_]?rate|marketing"
        ),
    ),
    (
        BusinessDomain.ECOMMERCE,
        _p(
            r"\bcart\b|checkout|\border[-_]?(?:line|item)|\bsku\b|\baov\b|abandon"
            r"|catalog|\bbasket\b|storefront|\bwishlist|product[-_]?listing|\bcoupon"
        ),
    ),
    (
        BusinessDomain.TRAINING,
        _p(
            r"training|\bcourse\b|\bl&d\b|learning|certification|\bskill[-_]?gap"
            r"|curriculum|\bmodule[-_]?completion|\blms\b|enrol"
        ),
    ),
    (
        BusinessDomain.MIS,
        _p(r"\bmis\b|management[-_]?report|consolidated[-_]?report|scorecard|\bkpi\b"),
    ),
    (
        BusinessDomain.SUPPLY_CHAIN,
        _p(
            r"supplier|vendor|procure|purchase[-_]?order|\bpo[-_]?number|sourcing"
            r"|logistics|\bfreight|shipment|warehouse|inventory|stock[-_]?level|\brfq\b"
        ),
    ),
    (
        BusinessDomain.MANUFACTURING,
        _p(
            r"production|\bplant\b|\bshop[-_]?floor|\boee\b|yield|\bscrap\b|\bbatch\b"
            r"|work[-_]?order|machine|\bdowntime|quality[-_]?control|\bdefect"
        ),
    ),
    (
        BusinessDomain.CUSTOMER_SERVICE,
        _p(
            r"\bticket|\bsla\b|complaint|\bcsat\b|\bnps\b|resolution[-_]?time|helpdesk"
            r"|support[-_]?case|escalation|first[-_]?response"
        ),
    ),
    (
        BusinessDomain.PROJECT,
        _p(
            r"\bproject\b|portfolio|\bmilestone|\bsprint\b|\bbacklog|allocation"
            r"|\btimesheet|\bwbs\b|deliverable|utilization[-_]?rate"
        ),
    ),
    (
        BusinessDomain.IT_OPERATIONS,
        _p(
            r"\bincident|\bitsm\b|\bserver\b|\bhost(?:name)?\b|\bvm\b|deployment|\bpatch"
            r"|vulnerabilit|\bcve\b|\bfirewall|\bapi[-_]?key|access[-_]?token|credential"
            r"|security[-_]?log|infrastructure|\bcmdb\b"
        ),
    ),
    (
        BusinessDomain.REAL_ESTATE,
        _p(
            r"\blease|\btenant|\blandlord|\bproperty\b|facilit|\bpremise|occupancy[-_]?rate"
            r"|\brent\b|building|floor[-_]?plan|\bsqft\b"
        ),
    ),
    (
        BusinessDomain.ENERGY,
        _p(
            r"\benergy\b|\bemission|sustainab|\bkwh\b|consumption|carbon|renewable"
            r"|\bmeter[-_]?reading|utilit|\bgrid\b|\bsolar\b"
        ),
    ),
    (
        BusinessDomain.RISK_AUDIT,
        _p(
            r"\baudit\b|internal[-_]?control|risk[-_]?register|\bfinding\b|investigation"
            r"|control[-_]?weakness|remediation|whistleblow|\bsox\b"
        ),
    ),
    (
        BusinessDomain.EXECUTIVE,
        _p(
            r"\bboard\b|\bexecutive\b|\bc[-_]?suite|\bceo\b|\bcfo\b|shareholder|\bquarterly[-_]?review"
        ),
    ),
)


@dataclass(frozen=True)
class DomainDetection:
    """What domain detection concluded, and how sure it is.

    Attributes:
        domain: The winning domain, or `GENERAL` below the confidence
            floor.
        candidates: Every domain that scored, highest first. More than
            one entry means the request touches several domains, which
            is what the cross-domain check reads.
        scores: Each candidate's score, for audit and for tests that
            need to see why a domain won.
        confident: True when the winner cleared the score floor.
    """

    domain: BusinessDomain
    candidates: tuple[BusinessDomain, ...] = ()
    scores: tuple[tuple[BusinessDomain, int], ...] = ()
    confident: bool = False

    @property
    def is_multi_domain(self) -> bool:
        """True when at least two domains scored at or above the floor."""
        return len([score for _, score in self.scores if score >= _MIN_SCORE]) >= 2


def detect_domain(
    question: str,
    schema_names: Sequence[str] = (),
) -> DomainDetection:
    """Detects the business domain of one request.

    Args:
        question: The already-normalized question text.
        schema_names: Table and/or column names the request is known to
            touch, when known. At the request gate this is empty (schema
            retrieval has not run yet); at the SQL gate it is the real
            referenced names, which is where detection is strongest.

    Returns:
        A `DomainDetection`. Never raises, and never returns a domain
        with no registry policy.
    """
    schema_text = normalize_schema_text(schema_names)
    scores: dict[BusinessDomain, int] = {}
    for domain, pattern in _DOMAIN_PATTERNS:
        score = 0
        if schema_text and pattern.search(schema_text):
            score += _SCHEMA_WEIGHT
        if pattern.search(question):
            score += _TEXT_WEIGHT
        if score:
            scores[domain] = score

    if not scores:
        return DomainDetection(domain=BusinessDomain.GENERAL)

    # Sort by score then by domain name, so a tie resolves identically on
    # every run -- an audit trail that reorders between runs is not an
    # audit trail.
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0].value))
    winner, top_score = ranked[0]
    confident = top_score >= _MIN_SCORE
    return DomainDetection(
        domain=winner if confident else BusinessDomain.GENERAL,
        candidates=tuple(domain for domain, _ in ranked),
        scores=tuple(ranked),
        confident=confident,
    )


def strictest(domains: Sequence[BusinessDomain]) -> BusinessDomain:
    """The domain whose policy is most restrictive among several.

    Used when a request spans domains: the protection applied is the
    strongest any touched domain requires, never the weakest. Ranked by
    how much the policy restricts (restricted categories, then
    prohibited actions, then whether aggregation is required), with a
    stable name tie-break.
    """
    from governance.domains import policy_for

    if not domains:
        return BusinessDomain.GENERAL

    def rank(domain: BusinessDomain) -> tuple[int, int, int, str]:
        policy = policy_for(domain)
        return (
            -len(policy.restricted_categories),
            -len(policy.prohibited_actions),
            -int(policy.aggregation_required),
            domain.value,
        )

    return sorted(domains, key=rank)[0]


__all__ = ["DomainDetection", "detect_domain", "strictest"]
