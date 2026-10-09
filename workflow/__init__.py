"""Enterprise Workflow Automation -- Prompt 49
(`49_ENTERPRISE_WORKFLOW_AUTOMATION_CONTRACT.md`).

Turns an already-**accepted** `recommendation.models.Recommendation`
(persisted as `identity.models.RecommendationRecord`, Prompt 18) into a
governed, auditable unit of real-world work -- proposed -> approval
required -> approved -> assigned -> in progress -> completed/rejected/
expired (`workflow.models.WorkflowStatus`).

**This package never executes anything.** It is a persisted state
machine and append-only audit trail only -- no SQL runs, no external
HTTP call is made, no other engine (the recommendation engine, the
multi-agent supervisor, a BI connector) is invoked from anywhere in this
package. "Complete" a workflow records a human's own report that the
work was done; it never verifies or re-runs it. See
`workflow/models.py`'s own module docstring for the full "never permit
uncontrolled autonomous execution" rationale, and
`tests/security/test_workflow_never_autonomously_executes.py` for the
regression test that proves it by parsing this package's own source.

Mirrors `recommendation/governance.py` + `recommendation/governance_policy.py`'s
own split exactly: `workflow/models.py` is the pure, ORM-independent
status/transition vocabulary; `workflow/policy.py` is the pure RBAC+ABAC
decision engine. Persistence lives in `identity.models.Workflow`/
`WorkflowEvent` + `identity/repositories/workflow.py`; the REST surface
is `api/workflow.py`.
"""
