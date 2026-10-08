"""The AI Governance Center -- Prompt 44
(`44_AI_GOVERNANCE_CENTER_CONTRACT.md`): a governed Draft -> Test ->
Evaluate -> Approve -> Deploy -> Rollback lifecycle for models,
providers, prompts, agents, tools, retrieval sources, and policies.

Deliberately a **new, separate top-level package from `governance/`**
(which already exists in this codebase and is a different concern: a
runtime request/result policy gate -- `governance/request_policy.py`/
`result_policy.py`, consumed by `agent/multiagent/agents.py`'s own
`governance_handler`). This package is about the *lifecycle of the
governed assets themselves* (which model/prompt/tool/retrieval source is
approved for production, by whom, since when), never about gating one
in-flight request's own rows -- the two are complementary, not
overlapping, and nothing here imports from or is imported by that
package.

See `governance_center/models.py`'s own module docstring for the full
design.
"""

from __future__ import annotations
