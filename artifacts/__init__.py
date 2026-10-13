"""Analysis artifacts -- durable, reopenable work products made from a
conversation turn (see `docs/conversational-intelligence-ui.md`).

**This package stores no result data of its own.** An artifact is a
*reference* to a turn that this application already persists: the
`identity.models.Prompt` row that asked the question, the
`identity.models.AiOutput` row that answered it, and that answer's own
already-bounded `metadata.result_snapshot` (written by
`api.chat_persistence.persist_execute_result` when the user clicked
"Confirm and Run"). The only result rows an artifact ever owns are the
ones a *refresh* produced, which exist nowhere else.

Nothing in this package executes SQL, calls an LLM, opens a database
connection, or reads the clock. `artifacts.staleness` is a pure function
taking a caller-supplied `as_of` -- the same boundary
`data_quality.models.FreshnessRule.as_of` and `briefing/` already draw --
and `artifacts.policy` is a pure deny-by-default decision function, the
same split `bi_integration.policy`/`eval_platform.policy` establish. The
real I/O lives in `identity/repositories/artifacts.py` (persistence) and
`api/artifacts.py` (the governed refresh, which reuses `POST /execute`'s
own validate -> row-limit -> qualify -> execute -> govern sequence
verbatim).
"""
