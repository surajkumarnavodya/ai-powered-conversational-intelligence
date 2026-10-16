"""Request-level governance: decides whether a question may be answered at all.

Runs on the already-normalized question, before schema retrieval, SQL
generation, or any source is called. It is deliberately deterministic, not
an LLM judgment, so a refusal does not depend on the model choosing to refuse.

Authorization comes only from the caller's roles (`agent.authz`). Text in
the question that claims authority ("this is authorized", "pretend I am an
admin") is never consulted, so it cannot unlock anything. Rules are checked
in order and the first match decides. Rules marked with a `permission` only
block callers that lack it.

Scope: these rules cover high-risk request shapes across the business
domains. They are not a complete classifier. Result-level protection
(`governance.result_policy`) and SQL-level restricted-column checks
(`agent.sql_validator`) still apply to anything these rules let through.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from agent.authz import Permission, has_role_permission
from governance.decisions import (
    GovernanceDecision,
    GovernanceVerdict,
)
from governance.domain_detection import detect_domain, strictest
from governance.domains import GOVERNANCE_POLICY_VERSION, BusinessDomain, policy_for
from governance.telemetry import get_governance_metrics


@dataclass(frozen=True)
class _Rule:
    domain: str
    category: str
    pattern: re.Pattern[str]
    decision: GovernanceDecision
    message: str
    #: If set, a caller holding this permission is not blocked by this rule.
    permission: Permission | None = None


_VERB = r"(?:show|give|reveal|list|tell|print|display|dump|export|share|send|what\s*(?:is|are|'s))"

#: The two halves of `site_worker_privacy`, named because the rule matches
#: them in either order -- "operative names" and "the names of the
#: operatives" are the same question. Deliberately construction-specific
#: nouns: every rule is matched against every question regardless of the
#: detected domain, so a broader noun ("worker", "staff") would change
#: what other domains refuse.
_SITE_PERSON = (
    r"(?:operatives?|labou?rers?|tradespe(?:rson|ople)|site\s+workers?|"
    r"crew\s+members?|subcontractor(?:'s)?\s+(?:workers?|staff|personnel))"
)
_WORKER_DETAIL = (
    r"(?:names?|e-?mails?|phone\s+numbers?|addresses|contact\s+details?|"
    r"day\s+rates?|hours\s+worked|induction\s+records?)"
)

_PROMPT_BYPASS_MESSAGE = (
    "Access rules can't be changed from a question. What you can see depends on "
    "your account's permissions."
)
_SECRETS_MESSAGE = (
    "I can't show credentials, secrets or security configuration, even when they "
    "appear in the data."
)
_PAYMENT_MESSAGE = (
    "I can't expose full account numbers, card details, PINs or other payment "
    "credentials. I can provide aggregate banking analytics instead."
)
_PRESCRIPTION_MESSAGE = (
    "I can provide authorized healthcare analytics, but I can't provide individual "
    "prescriptions, dosages or treatment decisions. Please consult a qualified "
    "healthcare professional."
)
_LEGAL_MESSAGE = (
    "Privileged legal content and legal advice aren't available here. Please consult "
    "qualified counsel."
)
_FRAUD_LABEL_MESSAGE = (
    "Fraud analytics can flag a transaction that matches configured risk indicators. "
    "I can't determine that a person committed fraud."
)
_CREDIT_DECISION_MESSAGE = (
    "I can't make or recommend an individual loan or credit decision. I can provide "
    "aggregate portfolio analytics."
)
_EMPLOYMENT_ACTION_MESSAGE = (
    "I can't recommend individual employment actions such as termination, demotion or "
    "discipline. I can show aggregate workforce trends."
)
_RANKING_MESSAGE = (
    "I can't rank or label individual employees using confidential performance "
    "information. I can provide aggregate performance trends by department, role or "
    "other authorized dimensions."
)
_APPRAISAL_MESSAGE = (
    "Individual appraisal and feedback information is restricted. I can provide "
    "aggregate performance analytics by department or role."
)
_COMPENSATION_MESSAGE = (
    "Salary information is restricted. I can provide authorized aggregated "
    "compensation analytics by department, grade or role."
)

_BULK_EXTRACTION_MESSAGE = (
    "I can't return bulk personal records. I can answer an aggregate question, or a "
    "narrow, filtered lookup within your permissions."
)
_PROFILING_MESSAGE = (
    "I can't infer or report personal characteristics such as health, religion, "
    "ethnicity, political views or sexual orientation. I can provide authorized "
    "aggregate analytics that don't rely on them."
)
_VENDOR_BANKING_MESSAGE = (
    "Supplier and vendor banking details are restricted. I can provide authorized "
    "spend, performance and contract analytics."
)
_CONTRACT_TERMS_MESSAGE = (
    "Negotiated contract and supplier pricing terms are commercially confidential. I "
    "can provide authorized aggregate spend and performance analytics."
)
_PROPRIETARY_PROCESS_MESSAGE = (
    "Production formulas, recipes and proprietary process parameters are restricted. I "
    "can provide authorized operational and quality analytics."
)
_CUSTOMER_PII_MESSAGE = (
    "Individual customer contact details aren't available here. I can provide "
    "authorized aggregate customer, campaign and service analytics."
)
_PATIENT_RECORD_MESSAGE = (
    "Individual patient records, diagnoses and clinical details are restricted. I can "
    "provide authorized aggregate and operational healthcare analytics."
)
_AUDIT_FINDING_MESSAGE = (
    "Audit findings, investigation details and control weaknesses are restricted. I "
    "can provide authorized aggregate risk and compliance analytics."
)
_TENANT_PRIVACY_MESSAGE = (
    "Individual tenant and property-owner details are restricted. I can provide "
    "authorized occupancy, utilization and maintenance analytics."
)
_SITE_WORKER_PRIVACY_MESSAGE = (
    "Individual site-worker details are restricted, including for people employed "
    "by a subcontractor. I can provide authorized labour, progress and safety "
    "analytics by trade, package and site."
)
_INFRASTRUCTURE_MESSAGE = (
    "Security-sensitive infrastructure and access-control details aren't available "
    "here. I can provide authorized operational and consumption analytics."
)
_LEARNER_DETAIL_MESSAGE = (
    "Individual learning scores and assessor comments are restricted. I can provide "
    "authorized aggregate completion, compliance and skill-gap analytics."
)
_PROJECT_CONFIDENTIAL_MESSAGE = (
    "Confidential project commercials aren't available here without authorization. I "
    "can provide authorized delivery, milestone and utilization analytics."
)
_CROSS_DOMAIN_MESSAGE = (
    "Combining these subject areas needs explicit authorization, because joining them "
    "can identify individuals even when each side is aggregated. I can answer either "
    "side on its own."
)

#: Everything beyond the original six domains, plus the bulk-extraction
#: and profiling families the mission's §31 and §2.F require. Appended to
#: `_RULES` rather than merged into it so the original, separately-tested
#: rule set stays recognizable and its ordering is untouched.
_EXTRA_RULES: tuple[_Rule, ...] = (
    _Rule(
        domain=BusinessDomain.GENERAL,
        category="bulk_personal_extraction",
        pattern=re.compile(
            # "complete" is deliberately absent from this quantifier list
            # and appears only in the explicit dump/export branches below.
            # "How complete is our customer data?" is a legitimate
            # data-quality question, and it was refused until a full-suite
            # run caught it (it broke a pre-existing Prompt 41 test). The
            # gap between the quantifier and the noun now also excludes
            # clause-boundary words, so a quantifier can no longer reach
            # across a verb into an unrelated noun.
            r"\b(?:all|every|each|entire|whole)\s+"
            r"(?:the\s+)?"
            r"(?:(?!(?:is|are|was|were|do|does|did|has|have|had|can|could|should|"
            r"would|will|many|much|of)\b)\w+\s+){0,2}?"
            r"(?:employees?|employee\s+records?|staff\s+records?|patients?|patient\s+records?|"
            r"customers?|customer\s+(?:records?|emails?|details?)|users?|members?|"
            r"people|persons?|contacts?|salaries|transactions?|accounts?|rows?|records?)\b"
            rf"|\b{_VERB}\b[^.?!]{{0,30}}\b(?:dump|download|export)\s+"
            r"(?:the\s+)?(?:whole|entire|complete|full)?\s*(?:database|table|dataset|data)\b"
            r"|\b(?:dump|download|export|extract)\s+(?:the\s+)?"
            r"(?:whole|entire|complete|full)\s+(?:database|dataset|table)\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REFUSE,
        message=_BULK_EXTRACTION_MESSAGE,
    ),
    _Rule(
        domain=BusinessDomain.GENERAL,
        category="protected_characteristic_profiling",
        pattern=re.compile(
            r"\b(?:who|which\s+(?:employees?|customers?|people|patients?))\b[^.?!]{0,60}"
            r"\b(?:is|are|has|have)\s+(?:pregnant|disabled|gay|lesbian|muslim|hindu|christian|"
            r"jewish|diabetic|hiv|depressed)\b"
            r"|\b(?:infer|predict|guess|determine|identify)\b[^.?!]{0,40}"
            r"\b(?:religion|ethnicity|race|caste|sexual\s+orientation|political\s+"
            r"(?:affiliation|views?)|disability|pregnancy|health\s+condition|"
            r"mental\s+health)\b"
            r"|\b(?:breakdown|segment|split|group|target|filter)\b[^.?!]{0,30}"
            r"\bby\s+(?:religion|caste|ethnicity|race|sexual\s+orientation|"
            r"political\s+affiliation|disability|pregnancy)\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REFUSE,
        message=_PROFILING_MESSAGE,
    ),
    _Rule(
        domain=BusinessDomain.HEALTHCARE,
        category="individual_patient_record",
        pattern=re.compile(
            r"\b(?:medical\s+history|clinical\s+notes?|lab\s+results?|test\s+results?|"
            r"diagnoses|diagnosis|medications?|prescriptions?)\b[^.?!]{0,50}"
            r"\b(?:(?:this|that|a|the|his|her|their|specific)\s+patient|"
            r"patient\s+(?:id\s*)?#?\d+|him|her|them)\b"
            r"|\b(?:this|that|a|specific)\s+patient'?s?\s+"
            r"(?:record|history|diagnosis|medication|treatment|notes?|results?)\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REQUIRE_AUTHORIZATION,
        message=_PATIENT_RECORD_MESSAGE,
        permission=Permission.VIEW_RESTRICTED_COLUMNS,
    ),
    _Rule(
        domain=BusinessDomain.SUPPLY_CHAIN,
        category="vendor_banking",
        pattern=re.compile(
            rf"\b{_VERB}\b[^.?!]{{0,40}}\b(?:supplier|vendor|contractor)s?\b[^.?!]{{0,20}}"
            r"\b(?:bank(?:ing)?\s+(?:details?|account)|account\s+number|iban)\b"
            r"|\b(?:supplier|vendor)s?'?\s+bank(?:ing)?\s+(?:details?|accounts?)\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REFUSE,
        message=_VENDOR_BANKING_MESSAGE,
    ),
    _Rule(
        domain=BusinessDomain.SUPPLY_CHAIN,
        category="confidential_contract_terms",
        pattern=re.compile(
            r"\b(?:negotiated|contract(?:ed)?|confidential)\s+"
            r"(?:rates?|pricing|prices?|terms?|discounts?)\b"
            r"|\bcompare\s+(?:supplier|vendor)s?'?\s+(?:pricing|prices?|rates?|quotes?)\b"
            r"|\brate\s+cards?\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REQUIRE_AUTHORIZATION,
        message=_CONTRACT_TERMS_MESSAGE,
        permission=Permission.VIEW_RESTRICTED_COLUMNS,
    ),
    _Rule(
        domain=BusinessDomain.MANUFACTURING,
        category="proprietary_process",
        pattern=re.compile(
            rf"\b{_VERB}\b[^.?!]{{0,40}}\b(?:recipes?|formulas?|formulations?|"
            r"bill\s+of\s+materials?|process\s+parameters?|trade\s+secrets?|"
            r"batch\s+formula)\b"
            r"|\b(?:production|manufacturing)\s+(?:recipes?|formulas?|secrets?)\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REQUIRE_AUTHORIZATION,
        message=_PROPRIETARY_PROCESS_MESSAGE,
        permission=Permission.VIEW_RESTRICTED_COLUMNS,
    ),
    _Rule(
        domain=BusinessDomain.SALES_MARKETING,
        category="customer_contact_extraction",
        pattern=re.compile(
            rf"\b{_VERB}\b[^.?!]{{0,40}}\b(?:customers?|clients?|leads?|users?|subscribers?|"
            r"contacts?)\b[^.?!]{0,25}"
            r"\b(?:e-?mails?|e-?mail\s+addresses|phone\s+numbers?|mobile\s+numbers?|"
            r"addresses|contact\s+details?)\b"
            r"|\b(?:customers?|clients?|leads?)'?\s+"
            r"(?:e-?mails?|phone\s+numbers?|addresses|contact\s+details?)\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REQUIRE_AUTHORIZATION,
        message=_CUSTOMER_PII_MESSAGE,
        permission=Permission.VIEW_RESTRICTED_COLUMNS,
    ),
    _Rule(
        domain=BusinessDomain.RISK_AUDIT,
        category="audit_finding",
        pattern=re.compile(
            rf"\b{_VERB}\b[^.?!]{{0,40}}\b(?:audit\s+findings?|investigation\s+(?:details?|"
            r"findings?|reports?)|control\s+weakness(?:es)?|whistleblow(?:er|ing)\s+"
            r"(?:reports?|complaints?)|internal\s+investigations?)\b"
            r"|\bwho\s+(?:is|are)\s+under\s+investigation\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REQUIRE_AUTHORIZATION,
        message=_AUDIT_FINDING_MESSAGE,
        permission=Permission.VIEW_RESTRICTED_COLUMNS,
    ),
    _Rule(
        domain=BusinessDomain.REAL_ESTATE,
        category="tenant_privacy",
        pattern=re.compile(
            rf"\b{_VERB}\b[^.?!]{{0,40}}\b(?:tenants?|lessees?|occupants?|"
            r"property\s+owners?|landlords?)\b[^.?!]{0,25}"
            r"\b(?:names?|e-?mails?|phone\s+numbers?|addresses|contact\s+details?|"
            r"lease\s+terms?)\b"
            r"|\b(?:access\s+control|door\s+access|badge|keycard)\s+"
            r"(?:logs?|records?|codes?)\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REQUIRE_AUTHORIZATION,
        message=_TENANT_PRIVACY_MESSAGE,
        permission=Permission.VIEW_RESTRICTED_COLUMNS,
    ),
    # The Construction counterpart of `tenant_privacy`, added with that
    # domain and for a measured reason: "list the names and phone numbers
    # of every operative on site" was *allowed* here before it existed,
    # while the identical question about tenants was not. The individual
    # -decision asks were already covered -- the HR-labelled rules match
    # on pattern, not on detected domain, so "who should we fire on the
    # jobsite" and "rank the workers by performance" already refuse --
    # so this adds the one shape that was genuinely missing rather than
    # a Construction-labelled copy of refusals that already fire.
    #
    # The person nouns are deliberately construction-specific. Every rule
    # is global, so a broader noun ("worker", "staff") would change what
    # other domains refuse, which this batch has no business doing.
    _Rule(
        domain=BusinessDomain.CONSTRUCTION,
        category="site_worker_privacy",
        pattern=re.compile(
            rf"\b{_VERB}\b[^.?!]{{0,40}}\b{_SITE_PERSON}\b[^.?!]{{0,25}}\b{_WORKER_DETAIL}\b"
            rf"|\b{_VERB}\b[^.?!]{{0,40}}\b{_WORKER_DETAIL}\b[^.?!]{{0,40}}\b{_SITE_PERSON}\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REQUIRE_AUTHORIZATION,
        message=_SITE_WORKER_PRIVACY_MESSAGE,
        permission=Permission.VIEW_RESTRICTED_COLUMNS,
    ),
    _Rule(
        domain=BusinessDomain.ENERGY,
        category="critical_infrastructure",
        pattern=re.compile(
            rf"\b{_VERB}\b[^.?!]{{0,40}}\b(?:scada|plc\s+(?:config|settings?)|"
            r"substation\s+(?:access|security)|grid\s+(?:topology|vulnerabilit)|"
            r"facility\s+access\s+(?:codes?|credentials?)|critical\s+infrastructure)\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REFUSE,
        message=_INFRASTRUCTURE_MESSAGE,
    ),
    _Rule(
        domain=BusinessDomain.TRAINING,
        category="individual_learning_detail",
        pattern=re.compile(
            r"\b(?:assessor|trainer|instructor)\s+(?:comments?|feedback|notes?)\b"
            r"|\b(?:test|exam|assessment|course)\s+scores?\b[^.?!]{0,40}"
            r"\b(?:(?:this|that|a|the|his|her|their|specific)\s+(?:employee|learner|"
            r"trainee|participant)|employee\s+(?:id\s*)?#?\d+|him|her|them)\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REQUIRE_AUTHORIZATION,
        message=_LEARNER_DETAIL_MESSAGE,
        permission=Permission.VIEW_RESTRICTED_COLUMNS,
    ),
    _Rule(
        domain=BusinessDomain.PROJECT,
        category="confidential_project_commercials",
        pattern=re.compile(
            r"\b(?:project|client|engagement)\s+(?:margins?|profitability|billing\s+rates?|"
            r"contract\s+values?)\b"
            r"|\bhow\s+much\s+(?:do|does)\s+we\s+(?:bill|charge)\s+"
            r"(?:this|that|the)\s+client\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REQUIRE_AUTHORIZATION,
        message=_PROJECT_CONFIDENTIAL_MESSAGE,
        permission=Permission.VIEW_RESTRICTED_COLUMNS,
    ),
)

_RULES: tuple[_Rule, ...] = (
    _Rule(
        domain="governance",
        category="prompt_bypass",
        pattern=re.compile(
            r"\b(?:ignore|disable|bypass|turn\s+off|override|skip)\s+"
            r"(?:all\s+|the\s+|your\s+|any\s+|these\s+)?"
            r"(?:privacy|safety|governance|security|policy|policies|rules|restrictions|"
            r"guardrails|masking|redaction)\b"
            r"|\bpretend\s+(?:that\s+)?(?:i\s+am|i'm|you\s+are)\s+(?:an?\s+)?"
            r"(?:admin|administrator|superuser|root)\b"
            r"|\bshow\s+me\s+(?:everything|all\s+(?:the\s+)?data)\s+"
            r"(?:without|including\s+(?:the\s+)?(?:restricted|sensitive|private))",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REFUSE,
        message=_PROMPT_BYPASS_MESSAGE,
    ),
    _Rule(
        domain="it_operations",
        category="secrets",
        pattern=re.compile(
            rf"\b{_VERB}\b[^.?!]{{0,40}}\b(?:passwords?|api[\s_-]?keys?|access[\s_-]?tokens?|"
            r"private[\s_-]?keys?|secret[\s_-]?keys?|connection[\s_-]?strings?|"
            r"db[\s_-]?credentials?|credentials?|otp|one[\s-]time\s+(?:codes?|passwords?))\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REFUSE,
        message=_SECRETS_MESSAGE,
    ),
    _Rule(
        domain="bfsi",
        category="payment_credentials",
        pattern=re.compile(
            rf"\b{_VERB}\b[^.?!]{{0,40}}\b(?:card\s+numbers?|full\s+account\s+numbers?|"
            r"account\s+numbers?|cvv|cvc|pins?|card\s+details|bank\s+details|"
            r"bank\s+credentials|iban|routing\s+numbers?)\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REFUSE,
        message=_PAYMENT_MESSAGE,
    ),
    _Rule(
        domain="healthcare",
        category="individual_prescription",
        pattern=re.compile(
            r"\bprescri(?:be|bing)\s+(?:a\s+|an\s+|the\s+|some\s+)?"
            r"(?:medicine|medication|drug|dose|dosage|treatment|antibiotic|painkiller|opioid)s?\b"
            r"|\b(?:dos(?:e|age)|medication|medicine|drug)\s+(?:for|of)\s+"
            r"(?:this|that|the|a|my)\s+patient\b"
            r"|\bwhich\s+(?:medicine|medication|drug|treatment)s?\s+should\s+"
            r"(?:this|the|a|my|he|she|they)\b"
            r"|\bdiagnos(?:e|is)\s+(?:this|the|a|my)\s+patient\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REFUSE,
        message=_PRESCRIPTION_MESSAGE,
    ),
    _Rule(
        domain="legal",
        category="privileged_content",
        pattern=re.compile(
            r"\b(?:attorney[\s-]client|privileged\s+(?:legal\s+)?(?:advice|communications?|"
            r"documents?|information)|litigation\s+strategy|legal\s+(?:advice|opinions?)|"
            r"settlement\s+strategy|is\s+(?:this|the|our)\s+company\s+liable)\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REFUSE,
        message=_LEGAL_MESSAGE,
    ),
    _Rule(
        domain="bfsi",
        category="fraud_label",
        pattern=re.compile(
            r"\b(?:is|are|was|were)\s+(?:this|that|the|a|these)\s+"
            r"(?:customer|user|account|person|client|applicant|individual)s?\s+"
            r"(?:a\s+|an\s+)?(?:fraud(?:ster|sters|ulent)?|criminal|thief|"
            r"money\s+launderer|launderer)\b"
            r"|\b(?:label|call|mark|brand|tag)\s+(?:this|that|the|a)\s+"
            r"(?:customer|user|account|person|client)\s+(?:as\s+)?(?:a\s+|an\s+)?"
            r"(?:fraud\w*|criminal|thief)\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REFUSE,
        message=_FRAUD_LABEL_MESSAGE,
    ),
    _Rule(
        domain="bfsi",
        category="individual_credit_decision",
        pattern=re.compile(
            r"\b(?:approve|reject|deny|decline|grant|block)\s+"
            r"(?:this|that|the|a|his|her|their)\s+(?:loan|credit|application|mortgage|limit)s?\b"
            r"|\bshould\s+(?:we|i)\s+(?:approve|reject|deny|decline|grant)\s+"
            r"(?:this|that|the|a)\s+(?:loan|credit|applicant|customer|application|mortgage)\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REFUSE,
        message=_CREDIT_DECISION_MESSAGE,
    ),
    _Rule(
        domain="hr",
        category="individual_employment_action",
        pattern=re.compile(
            r"\b(?:fire|terminate|dismiss|sack|let\s+go|demote|discipline|promote)\s+"
            r"(?:this|that|him|her|them)\b"
            r"|\b(?:fire|terminate|dismiss|sack|demote|discipline|promote)\s+"
            r"(?:an?\s+|the\s+)?(?:employee|staff|worker|manager)s?\b"
            r"|\bshould\s+(?:we|i)\s+(?:fire|terminate|dismiss|demote|discipline)\b"
            r"|\bwho\s+should\s+(?:we|i)\s+(?:fire|terminate|dismiss|let\s+go)\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REFUSE,
        message=_EMPLOYMENT_ACTION_MESSAGE,
    ),
    _Rule(
        domain="hr",
        category="individual_ranking",
        pattern=re.compile(
            r"\b(?:worst|best|bottom|poorest|lowest|least)\s+"
            r"(?:performing\s+|performer\s+)?(?:employee|staff|worker|performer)s?\b"
            r"|\bwho\s+is\s+the\s+(?:worst|best|bottom|least|lowest)\b"
            r"|\brank\s+(?:the\s+)?(?:employees?|staff|workers?|people)\s+"
            r"(?:by|on|based\s+on)\s+(?:performance|rating|appraisal|salary|pay|score)\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REFUSE,
        message=_RANKING_MESSAGE,
    ),
    _Rule(
        domain="hr",
        category="individual_appraisal",
        pattern=re.compile(
            r"\b(?:appraisal|performance\s+rating|manager\s+feedback|performance\s+review|"
            r"appraisal\s+comments?|disciplinary|promotion\s+recommendation)s?\b.{0,60}"
            r"\b(?:(?:this|that|a|the|one|his|her|their|specific)\s+(?:employee|staff|person|worker)"
            r"|employee\s+(?:id\s*)?#?\d+|him|her|them)\b"
            r"|\b(?:this|that|a|specific)\s+(?:employee|staff|worker)'?s?\s+"
            r"(?:performance|rating|appraisal|feedback|review|disciplinary)\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REQUIRE_AUTHORIZATION,
        message=_APPRAISAL_MESSAGE,
        permission=Permission.VIEW_RESTRICTED_COLUMNS,
    ),
    _Rule(
        domain="hr",
        category="individual_compensation",
        pattern=re.compile(
            r"\b(?:my|his|her|their)\s+(?:salary|salaries|pay|compensation|payslip)\b"
            r"|\b(?:salary|salaries|compensation|pay|payslip|earnings?)\b.{0,60}"
            r"\b(?:(?:this|that|a|the|one|his|her|their|specific|my)\s+(?:employee|staff|person|worker|manager)"
            r"|employee\s+(?:id\s*)?#?\d+|him|her|them|me)\b"
            r"|\bhow\s+much\s+(?:does|did|is|was)\s+(?:this|that|he|she|they|my)\b.{0,30}"
            r"\b(?:earn|make|paid|get)\b",
            re.IGNORECASE,
        ),
        decision=GovernanceDecision.REQUIRE_AUTHORIZATION,
        message=_COMPENSATION_MESSAGE,
        permission=Permission.VIEW_RESTRICTED_COLUMNS,
    ),
)


#: The original six-domain rules plus everything `_EXTRA_RULES` adds.
#: Order is preserved: the first match decides, so the narrower original
#: rules still win over the broader families appended after them.
_ALL_RULES: tuple[_Rule, ...] = _RULES + _EXTRA_RULES


def _verdict(rule: _Rule, domains_touched: tuple[str, ...] = ()) -> GovernanceVerdict:
    return GovernanceVerdict(
        decision=rule.decision,
        domain=str(rule.domain),
        category=rule.category,
        message=rule.message,
        policy_version=GOVERNANCE_POLICY_VERSION,
        domains_touched=domains_touched,
    )


def evaluate_request(
    question: str,
    roles: Sequence[str],
    schema_names: Sequence[str] = (),
) -> GovernanceVerdict:
    """Returns the governance verdict for one already-normalized question.

    `roles` is the caller's role tuple (`AgentState["caller_roles"]`). An
    unrecognized role grants nothing (see `agent.authz`), so a
    permission-gated rule still blocks such a caller.

    `schema_names` is optional table/column evidence for domain
    detection. At the request gate it is empty -- schema retrieval has
    not run yet -- and detection falls back to question wording, which is
    weaker and therefore more likely to land on the strict `GENERAL`
    policy. That is the intended direction of error.

    Three stages, in order:

    1. **Rule match.** The first matching rule decides. A rule carrying a
       `permission` does not block a caller who holds it.
    2. **Cross-domain check** (§30). A request touching two or more
       domains that no policy allows to be combined requires explicit
       authorization -- joining HR with finance can identify an
       individual even when each side is aggregated.
    3. **Allow**, labelled with the detected domain so downstream result
       and recommendation checks know which policy applies.

    Every outcome is counted in `governance.telemetry` and carries
    `GOVERNANCE_POLICY_VERSION`.
    """
    metrics = get_governance_metrics()
    detection = detect_domain(question, schema_names)
    touched = tuple(domain.value for domain in detection.candidates if detection.is_multi_domain)

    for rule in _ALL_RULES:
        if not rule.pattern.search(question):
            continue
        if rule.permission is not None and has_role_permission(roles, rule.permission):
            continue
        verdict = _verdict(rule, touched)
        metrics.record_request_decision(verdict.decision.value, verdict.domain, verdict.category)
        return verdict

    if detection.is_multi_domain:
        scored = [domain for domain, score in detection.scores if score >= _CROSS_DOMAIN_MIN_SCORE]
        primary = strictest(scored)
        unauthorized = [
            domain
            for domain in scored
            if domain is not primary and domain not in policy_for(primary).cross_domain_allowed
        ]
        if unauthorized and not has_role_permission(roles, Permission.VIEW_RESTRICTED_COLUMNS):
            verdict = GovernanceVerdict(
                decision=GovernanceDecision.REQUIRE_AUTHORIZATION,
                domain=primary.value,
                category="unauthorized_cross_domain",
                message=_CROSS_DOMAIN_MESSAGE,
                policy_version=GOVERNANCE_POLICY_VERSION,
                domains_touched=touched,
            )
            metrics.record_request_decision(
                verdict.decision.value, verdict.domain, verdict.category
            )
            return verdict

    allowed = GovernanceVerdict(
        decision=GovernanceDecision.ALLOW,
        domain=detection.domain.value,
        policy_version=GOVERNANCE_POLICY_VERSION,
        domains_touched=touched,
    )
    metrics.record_request_decision(allowed.decision.value, allowed.domain)
    return allowed


#: A domain must score at least this much to count toward the
#: cross-domain check. Matches `governance.domain_detection`'s own floor:
#: a single wording hit is too weak to demand authorization on.
_CROSS_DOMAIN_MIN_SCORE = 3
