"""Recommendation safety: a recommendation must respect domain boundaries.

The mission's §29. `recommendation/engine.py` already refuses to produce
a recommendation without evidence, and `recommendation/governance.py`
measures quality after the fact -- neither asks whether the *content* of
a recommendation is something this system is allowed to recommend at all.
A perfectly evidenced recommendation to terminate an employee is still
one this system must never make.

**Checked against the same `ProhibitedAction` list the request gate
uses** (`governance.domains`), so "what we refuse to be asked" and "what
we refuse to propose" cannot drift apart. That shared list is the reason
this module is small: it owns the text patterns that recognize a
prohibited *proposal*, and nothing else.

**Withheld, not rewritten.** An unsafe recommendation is dropped and
reported, never edited into a softer version -- editing would leave the
evidence attached to a claim it no longer supports, and a reader would
have no way to know the engine's actual output had been altered. The
same "withhold and disclose" posture `briefing.claims` takes for an
unsupported claim.

**No LLM call, no I/O.** Deterministic, so a recommendation's safety
verdict is reproducible for audit and cannot be talked out of a refusal.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from governance.domains import BusinessDomain, ProhibitedAction, prohibited_actions_for

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ActionPattern:
    action: ProhibitedAction
    pattern: re.Pattern[str]
    reason: str


_PATTERNS: tuple[_ActionPattern, ...] = (
    _ActionPattern(
        ProhibitedAction.INDIVIDUAL_EMPLOYMENT_ACTION,
        re.compile(
            r"\b(?:terminate|termination|fire|firing|dismiss|dismissal|demote|demotion"
            r"|discipline|disciplinary\s+action|put\s+on\s+a?\s*performance\s+improvement"
            r"|let\s+(?:him|her|them|this\s+employee)\s+go)\b",
            re.IGNORECASE,
        ),
        "proposes an individual employment action",
    ),
    _ActionPattern(
        ProhibitedAction.INDIVIDUAL_COMPENSATION_DECISION,
        re.compile(
            r"\b(?:promote|promotion|raise|increment|increase\s+(?:his|her|their|the\s+employee)"
            r"(?:'s)?\s+(?:salary|pay|compensation)|adjust\s+(?:his|her|their)\s+"
            r"(?:salary|pay|compensation)|award\s+(?:him|her|them)\s+a\s+bonus)\b",
            re.IGNORECASE,
        ),
        "proposes an individual compensation decision",
    ),
    _ActionPattern(
        ProhibitedAction.INDIVIDUAL_PERFORMANCE_RANKING,
        re.compile(
            r"\b(?:worst|lowest|bottom|poorest)\s+(?:performing\s+|performer\s+)?"
            r"(?:employee|performer|staff|worker)s?\b"
            r"|\brank\s+(?:employees?|staff|workers?)\s+by\s+(?:performance|rating|appraisal)\b",
            re.IGNORECASE,
        ),
        "ranks or labels individual employees",
    ),
    _ActionPattern(
        ProhibitedAction.MEDICAL_DIAGNOSIS_OR_PRESCRIPTION,
        re.compile(
            r"\b(?:prescribe|prescription|administer|dosage|\bdose\b|diagnos(?:e|is|ed)"
            r"|start\s+(?:the\s+)?patient\s+on|switch\s+(?:the\s+)?patient\s+to)\b",
            re.IGNORECASE,
        ),
        "proposes a diagnosis, prescription or dosage",
    ),
    _ActionPattern(
        ProhibitedAction.CLINICAL_DECISION,
        re.compile(
            r"\b(?:discharge\s+(?:the\s+)?patient|admit\s+(?:the\s+)?patient"
            r"|treatment\s+plan\s+for\s+(?:this|the)\s+patient|clinical\s+decision)\b",
            re.IGNORECASE,
        ),
        "proposes an individual clinical decision",
    ),
    _ActionPattern(
        ProhibitedAction.CREDIT_OR_LOAN_DECISION,
        re.compile(
            r"\b(?:approve|reject|decline|deny)\s+(?:the\s+|this\s+|his\s+|her\s+|their\s+)?"
            r"(?:loan|credit|application|mortgage|limit)\b"
            r"|\b(?:increase|decrease|reduce)\s+(?:the\s+|his\s+|her\s+|their\s+)?credit\s+limit\b",
            re.IGNORECASE,
        ),
        "proposes an individual credit or loan decision",
    ),
    _ActionPattern(
        ProhibitedAction.FRAUD_ACCUSATION,
        re.compile(
            r"\b(?:is|are)\s+(?:a\s+|an\s+)?(?:fraudster|fraudulent\s+customer|criminal|thief)\b"
            r"|\b(?:report|flag)\s+(?:this|the)\s+(?:customer|client|person)\s+"
            r"(?:as|for)\s+(?:fraud|a\s+fraudster|money\s+laundering)\b"
            r"|\bcommitted\s+fraud\b",
            re.IGNORECASE,
        ),
        "accuses a person of fraud",
    ),
    _ActionPattern(
        ProhibitedAction.INDIVIDUALIZED_FINANCIAL_ADVICE,
        re.compile(
            r"\b(?:this\s+customer\s+should\s+invest|recommend\s+(?:that\s+)?"
            r"(?:he|she|they|this\s+customer)\s+(?:buy|sell|invest|refinance))\b",
            re.IGNORECASE,
        ),
        "gives individualized financial advice",
    ),
    _ActionPattern(
        ProhibitedAction.LEGAL_CONCLUSION_OR_ADVICE,
        re.compile(
            r"\b(?:is\s+(?:legally\s+)?liable|constitutes\s+a\s+breach|is\s+in\s+breach\s+of"
            r"|we\s+should\s+(?:sue|settle|litigate)|legally\s+(?:required|obligated))\b",
            re.IGNORECASE,
        ),
        "states a legal conclusion or gives legal advice",
    ),
    _ActionPattern(
        ProhibitedAction.LIABILITY_DETERMINATION,
        re.compile(
            r"\b(?:at\s+fault|responsible\s+for\s+the\s+breach|caused\s+the\s+violation)\b",
            re.IGNORECASE,
        ),
        "determines liability or fault",
    ),
    _ActionPattern(
        ProhibitedAction.PROTECTED_CHARACTERISTIC_INFERENCE,
        re.compile(
            r"\b(?:because\s+(?:he|she|they)\s+(?:is|are)\s+)?(?:pregnan\w*|disabled|disability"
            r"|religio\w*|ethnic\w*|\bgay\b|sexual\s+orientation|mental\s+health\s+condition)\b"
            r"|\b(?:target|segment|exclude)\s+(?:by|on)\s+(?:race|religion|ethnicity|gender|age)\b",
            re.IGNORECASE,
        ),
        "infers or targets on a protected characteristic",
    ),
    _ActionPattern(
        ProhibitedAction.SENSITIVE_PERSONAL_TARGETING,
        re.compile(
            r"\btarget\s+(?:customers?|users?|people)\s+(?:with|who\s+have)\s+"
            r"(?:a\s+)?(?:medical|health|disability|pregnan\w*|financial\s+distress)\b",
            re.IGNORECASE,
        ),
        "targets individuals on sensitive personal attributes",
    ),
    _ActionPattern(
        ProhibitedAction.CREDENTIAL_DISCLOSURE,
        re.compile(
            # No \b before the keyword: a real identifier is
            # "db_password"/"client_secret", and an underscore is a word
            # character, so a bounded pattern missed exactly the shape a
            # leaked credential takes. Found by a security test.
            r"(?:password|api[\s_-]*key|access[\s_-]*token|private[\s_-]*key|"
            r"client[\s_-]*secret|connection[\s_-]*string)s?\s*(?:is|are|=|:)\s*\S",
            re.IGNORECASE,
        ),
        "discloses a credential",
    ),
    _ActionPattern(
        ProhibitedAction.UNSUPPORTED_ALLEGATION,
        re.compile(
            r"\b(?:is\s+(?:stealing|embezzling|colluding)|has\s+been\s+(?:stealing|embezzling)"
            r"|deliberately\s+(?:falsified|manipulated))\b",
            re.IGNORECASE,
        ),
        "makes an unsupported allegation about a person",
    ),
)


@dataclass(frozen=True)
class RecommendationVerdict:
    """Whether one recommendation may be shown.

    `reason` names the prohibited action for audit. It is never shown to
    the caller as-is: the caller sees the fixed, reviewed
    `WITHHELD_NOTICE` instead, so a refusal cannot reveal which pattern
    matched on what text.
    """

    allowed: bool
    action: ProhibitedAction | None = None
    reason: str = ""

    @property
    def is_refused(self) -> bool:
        return not self.allowed


SAFE_VERDICT = RecommendationVerdict(allowed=True)

WITHHELD_NOTICE = (
    "One or more recommendations were withheld because they would amount to a "
    "decision about an individual that this system does not make. Aggregate, "
    "process and organizational recommendations are unaffected."
)


def check_recommendation(
    text: str,
    domain: BusinessDomain = BusinessDomain.GENERAL,
) -> RecommendationVerdict:
    """Whether one recommendation's text is safe to show in this domain.

    Only the actions the domain's own policy prohibits are checked, so a
    manufacturing recommendation is not measured against clinical rules
    -- except for the two actions below, which are prohibited everywhere.

    Args:
        text: The recommendation's full text (claim, rationale and
            action concatenated by the caller).
        domain: The request's detected domain.

    Returns:
        A `RecommendationVerdict`. Safe by default: a recommendation is
        refused only on a positive pattern match.
    """
    prohibited = prohibited_actions_for(domain) | _UNIVERSALLY_PROHIBITED
    for rule in _PATTERNS:
        if rule.action not in prohibited:
            continue
        if rule.pattern.search(text):
            return RecommendationVerdict(allowed=False, action=rule.action, reason=rule.reason)
    return SAFE_VERDICT


#: Prohibited in every domain regardless of policy. A credential has no
#: domain in which disclosing it is correct, and an unsupported
#: allegation about a person is never a safe recommendation -- so these
#: two are not left to each domain to remember to declare.
_UNIVERSALLY_PROHIBITED: frozenset[ProhibitedAction] = frozenset(
    {ProhibitedAction.CREDENTIAL_DISCLOSURE, ProhibitedAction.UNSUPPORTED_ALLEGATION}
)


def filter_recommendations(
    recommendations: Sequence[object],
    domain: BusinessDomain = BusinessDomain.GENERAL,
) -> tuple[list[object], tuple[str, ...]]:
    """Drops unsafe recommendations from a list, reporting what was dropped.

    Duck-typed on purpose: it reads `claim`/`action`/`rationale` if
    present, so it works for both `recommendation.models.Recommendation`
    (whose `claim` is a `ProvenancedClaim`) and the plain projections the
    API layer builds, without this module importing either.

    Returns:
        The kept recommendations, and one audit string per drop naming
        the prohibited action (never the recommendation's own text).
    """
    kept: list[object] = []
    dropped: list[str] = []
    for item in recommendations:
        verdict = check_recommendation(_text_of(item), domain)
        if verdict.allowed:
            kept.append(item)
            continue
        action = verdict.action.value if verdict.action else "unknown"
        dropped.append(action)
        logger.warning(
            "[governance.recommendation_safety] withheld a recommendation in domain %s: %s",
            domain.value,
            verdict.reason,
        )
    return kept, tuple(dropped)


def _text_of(item: object) -> str:
    """The text of a recommendation, however it is shaped."""
    parts: list[str] = []
    for attribute in ("claim", "claim_text", "rationale", "action", "measurable_impact"):
        value = getattr(item, attribute, None)
        if value is None:
            continue
        # `Recommendation.claim` is a `ProvenancedClaim`; everything else
        # is already a string.
        inner = getattr(value, "value", value)
        if isinstance(inner, str):
            parts.append(inner)
    return " ".join(parts)


__all__ = [
    "SAFE_VERDICT",
    "WITHHELD_NOTICE",
    "RecommendationVerdict",
    "check_recommendation",
    "filter_recommendations",
]
