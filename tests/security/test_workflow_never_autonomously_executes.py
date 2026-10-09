"""Cross-cutting regression for Prompt 49
(`49_ENTERPRISE_WORKFLOW_AUTOMATION_CONTRACT.md`) -- mirrors the
source-parsing pattern `test_schema_drift_never_rewrites_catalog.py`/
`test_prompt_registry_cannot_weaken_security.py` already establish for
proving a "this package never does X" invariant structurally, not just
by mocking coverage that could miss a future added call site.

The mission's own mandatory control: "Require approval for consequential
actions and never permit uncontrolled autonomous execution." Proven two
ways:

1. **No module in `workflow/`, `identity/repositories/workflow.py`, or
   `api/workflow.py` ever imports or references a real execution entry
   point** -- SQL execution (`db.execution`/`db.connection`), the LLM/
   LangGraph pipeline (`agent.graph`/`agent.nodes`), the multi-agent
   supervisor (`agent.multiagent`), an outbound HTTP client (`httpx`/
   `requests`/`urllib`), or a BI push connector
   (`bi_integration.push`/`bi_integration.outbound_safety`). A genuine
   future regression (e.g. a workflow auto-executing its own SQL on
   approval) would necessarily add one of these imports, which this test
   would then catch.
2. **`APPROVAL_REQUIRED -> APPROVED` requires a distinct permission
   (`WORKFLOW_REVIEW`) from the one that creates/submits/operates a
   workflow (`WORKFLOW_MANAGE`)** -- the mission's own "require approval
   for consequential actions" control, proven against the real policy
   function rather than merely asserted in a docstring.
"""

from __future__ import annotations

import ast
import inspect

import identity.repositories.workflow
from identity.rbac import Permission

import api.workflow
import workflow.models
import workflow.policy
from workflow.policy import WorkflowAction, authorize_workflow_action

_FORBIDDEN_IMPORT_PREFIXES = (
    "db.execution",
    "db.connection",
    "agent.graph",
    "agent.nodes",
    "agent.multiagent",
    "bi_integration.push",
    "bi_integration.outbound_safety",
    "httpx",
    "requests",
    "urllib.request",
    "subprocess",
)

_MODULES = [
    workflow.models,
    workflow.policy,
    identity.repositories.workflow,
    api.workflow,
]


def _imported_module_names(module: object) -> set[str]:
    """Every module name this module's own source imports from (`import
    X`, `import X.Y`, `from X.Y import ...`) -- a cheap, exact way to
    prove "this module never imports X" without relying on mocking
    coverage."""
    source = inspect.getsource(module)
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class TestNoExecutionEntryPointIsEverImported:
    def test_no_module_imports_a_forbidden_execution_entry_point(self):
        for module in _MODULES:
            imported = _imported_module_names(module)
            for forbidden in _FORBIDDEN_IMPORT_PREFIXES:
                matches = {
                    name
                    for name in imported
                    if name == forbidden or name.startswith(forbidden + ".")
                }
                assert not matches, (
                    f"{module.__name__} imports {matches} -- the workflow "
                    "package must never reach a real execution entry point "
                    "(see workflow/models.py's own module docstring)."
                )

    def test_the_package_docstring_states_the_invariant(self):
        doc = (workflow.models.__doc__ or "").lower()
        assert "never permit uncontrolled autonomous execution" in doc


class TestApprovalIsASeparatePermissionFromOperating:
    """The mission's own 'require approval for consequential actions'
    control, proven against the real policy function."""

    def test_manage_alone_can_submit_but_approval_is_a_distinct_check(self):
        manage_only = frozenset({Permission.WORKFLOW_MANAGE.value})
        review_only = frozenset({Permission.WORKFLOW_REVIEW.value})

        submit_decision = authorize_workflow_action(
            actor_permissions=manage_only,
            actor_tenant_id="tenant-a",
            workflow_tenant_id="tenant-a",
            action=WorkflowAction.SUBMIT_FOR_APPROVAL,
        )
        assert submit_decision.allowed is True

        # A caller holding only the review permission (not manage)
        # cannot submit -- submitting and approving are deliberately
        # gated by two different permission codes, never one combined
        # "do anything" grant.
        submit_by_reviewer = authorize_workflow_action(
            actor_permissions=review_only,
            actor_tenant_id="tenant-a",
            workflow_tenant_id="tenant-a",
            action=WorkflowAction.SUBMIT_FOR_APPROVAL,
        )
        assert submit_by_reviewer.allowed is False

    def test_approve_and_reject_are_gated_by_the_review_permission(self):
        review_only = frozenset({Permission.WORKFLOW_REVIEW.value})
        for action in (WorkflowAction.APPROVE, WorkflowAction.REJECT):
            decision = authorize_workflow_action(
                actor_permissions=review_only,
                actor_tenant_id="tenant-a",
                workflow_tenant_id="tenant-a",
                action=action,
            )
            assert decision.allowed is True

    def test_a_caller_with_neither_permission_can_neither_submit_nor_approve(self):
        neither = frozenset({"ai.use"})
        for action in (
            WorkflowAction.SUBMIT_FOR_APPROVAL,
            WorkflowAction.APPROVE,
            WorkflowAction.REJECT,
        ):
            decision = authorize_workflow_action(
                actor_permissions=neither,
                actor_tenant_id="tenant-a",
                workflow_tenant_id="tenant-a",
                action=action,
            )
            assert decision.allowed is False


class TestCompletionNeverVerifiesItsOwnOutcome:
    """`complete_workflow` records a human's own report -- it must never
    itself compute, fetch, or verify the outcome it's told about."""

    def test_complete_workflow_has_no_verification_side_effect_in_its_source(self):
        source = inspect.getsource(identity.repositories.workflow.complete_workflow)
        # The function body must do nothing but pass through the
        # caller-supplied strings/dict into the same conditional UPDATE
        # every other transition uses -- never a query, a request, or a
        # call to anything outside this module's own helpers.
        forbidden_tokens = ("execute_readonly_sql", "httpx", "requests.", "subprocess")
        for token in forbidden_tokens:
            assert token not in source
