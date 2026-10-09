"""Enterprise data privacy, domain safety and governance.

The centralized engine every request passes through. One place decides
what may be asked, what may be returned, and what may be recommended --
so a domain rule is a data change here rather than a new branch in a
node or a sentence in a prompt.

Module map:

- `decisions.py` -- the shared decision vocabulary. Six states: allow,
  allow-with-redaction, allow-aggregated, allow-with-warning,
  require-authorization, refuse.
- `classification.py` -- what a field is (`DataClassification` x
  `DataCategory`) and what follows from it. Explicit human classification
  from `config/sensitive_columns.yaml` always wins; name-pattern
  inference fills the gap that file leaves empty on a fresh database.
- `domains.py` -- the versioned policy registry: one `DomainPolicy` per
  business domain, plus the `ProhibitedAction` list shared by the request
  gate and the recommendation filter.
- `domain_detection.py` -- which domain a request belongs to.
  Deterministic; schema evidence outranks question wording.
- `request_policy.py` -- whether a question may be answered at all.
- `result_policy.py` -- protects values before the LLM, the
  recommendation engine or the UI sees them.
- `suppression.py` -- k-anonymity suppression for small groups.
- `recommendation_safety.py` -- whether a recommendation may be shown.
- `telemetry.py` -- counts of what the engine did. Counts only.

**Defense in depth, and where each layer lives.** Governance is not a
prompt instruction, and no single layer is load-bearing alone:

| Layer | Enforced by |
|---|---|
| Intent / request | `request_policy` (before schema retrieval) |
| Domain | `domain_detection` + `domains` |
| Schema / column | `classification`, `db/value_sampling.py` |
| SQL | `agent/sql_validator.py` (AST allowlist, restricted columns, wildcard, system catalogs) |
| Result | `result_policy` + `suppression` |
| Recommendation | `recommendation_safety` |
| Logging | `security/audit_log.py` (events carry decisions, never values) |

A request that generates different SQL does not get a different answer
about whether it is allowed: the result gate classifies the *returned
column names*, so an alias or a `UNION` reaches the same protection.

**Authorization comes only from the caller's roles.** `agent.authz
.has_role_permission` is the single decision point, and its only input is
the role tuple the application resolved. Text in a question claiming
authority, and content in a database cell claiming it, are both data --
`tests/security/test_governance_cannot_be_bypassed.py` asserts this
structurally, including that no module here calls an LLM or opens a
database connection.

**Two protections no permission unlocks.** A credential or a payment
instrument is withheld from every caller including an admin -- there is
no role for which returning a password is the right answer. And
small-group suppression applies to an authorized caller too, because it
protects the individual in the group rather than the sensitivity of the
column.

**Rollback.** `Settings.enable_request_governance`,
`enable_result_governance` and `enable_recommendation_safety` each
restore the prior behaviour of their own layer independently.

**Honest scope.** This is a set of technical controls. It does not by
itself constitute compliance with HIPAA, GDPR, the DPDP Act, PCI DSS,
SOX or any other regime -- formal compliance requires organizational,
legal, contractual and administrative assessment alongside these
controls. Specific disclosed limits: name-based inference is not
content inspection, so a sensitive value in a neutrally-named column is
not detected; suppression closes the single-result-set and
total-minus-visible cases but there is no cross-session query auditor;
and `config/sensitive_columns.yaml` remains the authoritative source for
anything a human has actually reviewed.
"""
