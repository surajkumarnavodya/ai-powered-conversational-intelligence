"""The question job: its statuses, its legal transitions, and its record.

A queued question needs a status vocabulary the synchronous `/ask` path
never had. `/ask` blocks until it has an answer, so "what is happening
right now" is never a question anyone can ask it. `POST /questions`
returns a job id immediately, which makes the status *the* contract: a
caller polls it, the frontend renders it, and an operator alerts on it.

**Why not reuse `agent.harness.lifecycle.ExecutionState`.** That enum
describes one graph execution's position *inside* the harness
(`VALIDATING`, `WAITING_FOR_TOOL`, `RETRYING`, ...) and is deliberately
kept separate from `AgentState["status"]` -- see its own module
docstring. A job has a strictly larger life: it exists before any
execution does (`QUEUED`, where no envelope has been created yet) and it
can end without one ever running (`BLOCKED_BY_GUARDRAIL`, decided before
admission). Mapping those onto an execution-scoped enum would mean
inventing "queued" and "blocked" positions inside a state machine that
has no such positions, so this is a third vocabulary with one explicit
mapping to each neighbour -- `JobStatus.from_execution_state` and
`JobStatus.to_agent_status` are the only places either mapping lives
(CLAUDE.md 4.8: one owner per fact).

**Transitions are a whitelist.** Exactly the discipline
`agent.harness.lifecycle` establishes, and for the same reason: a state
machine that merely warns on a bad edge is a state machine that drifts,
and the bug it catches -- a worker marking a job `COMPLETED` while its
graph run is still in flight -- only shows up under the concurrency this
subsystem exists to support.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class JobStatus(StrEnum):
    """One question job's observable position. Terminal statuses are final.

    These are exactly the seven statuses the public API reports. The
    values are lowercase on the wire (`"queued"`) while the member names
    are the uppercase vocabulary the brief specifies (`QUEUED`); a
    `StrEnum` makes a member compare equal to its own value, so a JSON
    body and a Python comparison agree without a conversion step.
    """

    #: Admitted and retained, but not started: either the user's own
    #: concurrency budget or the global one was full at submission.
    QUEUED = "queued"
    #: A worker is executing the question through `run_orchestrated`.
    RUNNING = "running"
    #: Finished and produced an answer. The result is readable, and
    #: re-governed on every read (see `jobs.store`).
    COMPLETED = "completed"
    #: Ran and broke. Distinct from TIMEOUT and CANCELLED on purpose: an
    #: operator needs to tell a provider outage from a user hanging up.
    FAILED = "failed"
    #: A cancel was requested by the owner, or by graceful shutdown,
    #: before the job reached a result.
    CANCELLED = "cancelled"
    #: Exceeded `QUESTION_TIMEOUT_SECONDS` while running, or
    #: `QUESTION_QUEUE_WAIT_TIMEOUT_SECONDS` while queued.
    TIMEOUT = "timeout"
    #: A guardrail refused the question. Its own status rather than folded
    #: into FAILED, because nothing malfunctioned: the system worked
    #: exactly as configured, and a caller (and an auditor) needs to tell
    #: the two apart without parsing a message string.
    BLOCKED_BY_GUARDRAIL = "blocked_by_guardrail"

    @property
    def is_terminal(self) -> bool:
        """Whether no further transition is legal from here."""
        return self in TERMINAL_JOB_STATUSES

    @property
    def is_live(self) -> bool:
        """Whether this job still occupies capacity (queue depth or a slot)."""
        return self in LIVE_JOB_STATUSES

    def to_agent_status(self) -> str:
        """This status as the `AgentState["status"]` vocabulary.

        `api/schemas.py`'s `AgentStatus` understands four values and is a
        frozen response contract (CLAUDE.md 6.3), so the four distinct
        unsuccessful job outcomes all map to `"failed"` there. The
        distinction is not lost -- it is carried by `JobStatus` itself on
        the job-status response, which is a *new* contract and therefore
        free to be precise.
        """
        if self is JobStatus.COMPLETED:
            return "succeeded"
        if self.is_terminal:
            return "failed"
        return "pending"

    @classmethod
    def from_execution_state(cls, state: Any) -> JobStatus:
        """The job status corresponding to a harness `ExecutionState`.

        Imported lazily inside the function so this module stays
        importable without pulling the harness in -- the store and the
        API schemas need `jobs.models`, and neither needs the harness.

        An execution state with no job-level meaning (`VALIDATING`,
        `WAITING_FOR_TOOL`, `RETRYING`, ...) maps to `RUNNING`: from
        outside, every one of those *is* "still working on it", and
        leaking the harness's internal positions onto a public API would
        freeze a contract nobody intended to publish.
        """
        from agent.harness.lifecycle import ExecutionState

        mapping = {
            ExecutionState.COMPLETED: cls.COMPLETED,
            ExecutionState.FAILED: cls.FAILED,
            ExecutionState.CANCELLED: cls.CANCELLED,
            ExecutionState.TIMED_OUT: cls.TIMEOUT,
        }
        return mapping.get(ExecutionState(state), cls.RUNNING)


#: Statuses no transition may leave.
TERMINAL_JOB_STATUSES: frozenset[JobStatus] = frozenset(
    {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.TIMEOUT,
        JobStatus.BLOCKED_BY_GUARDRAIL,
    }
)

#: Statuses that still consume capacity. `QUEUED` counts against queue
#: depth; `RUNNING` counts against the per-user and global concurrency
#: budgets. Nothing else counts against anything, which is what makes
#: "release on reaching a terminal status" the single release rule.
LIVE_JOB_STATUSES: frozenset[JobStatus] = frozenset({JobStatus.QUEUED, JobStatus.RUNNING})

#: Every legal edge, read as "from -> the set it may enter".
#:
#: Three rules shape this table, mirroring
#: `agent.harness.lifecycle._ALLOWED`:
#:   1. A job may be cancelled or time out from either live status. Both
#:      are external events, not something the worker chooses.
#:   2. Terminal statuses have no outgoing edges at all.
#:   3. `BLOCKED_BY_GUARDRAIL` is reachable from both live statuses, not
#:      only from `QUEUED`: the pre-admission request gate runs before a
#:      job is ever queued, but the output guardrail and the grounding
#:      gate run while it is `RUNNING`, and a refusal there is the same
#:      verdict arriving later.
_ALLOWED_JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset(
        {
            JobStatus.RUNNING,
            JobStatus.CANCELLED,
            JobStatus.TIMEOUT,
            JobStatus.FAILED,
            JobStatus.BLOCKED_BY_GUARDRAIL,
        }
    ),
    JobStatus.RUNNING: frozenset(
        {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.TIMEOUT,
            JobStatus.BLOCKED_BY_GUARDRAIL,
        }
    ),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
    JobStatus.TIMEOUT: frozenset(),
    JobStatus.BLOCKED_BY_GUARDRAIL: frozenset(),
}


class InvalidJobTransition(RuntimeError):
    """An illegal job-status edge was attempted. Always a bug, never user input."""

    def __init__(self, source: JobStatus, target: JobStatus) -> None:
        self.source = source
        self.target = target
        super().__init__(f"Illegal job status transition: {source.value} -> {target.value}.")


def can_transition(source: JobStatus, target: JobStatus) -> bool:
    """Whether `source -> target` is legal. Pure; safe to call as a check."""
    return target in _ALLOWED_JOB_TRANSITIONS[source]


def assert_transition(source: JobStatus, target: JobStatus) -> None:
    """Raises `InvalidJobTransition` unless `source -> target` is legal."""
    if not can_transition(source, target):
        raise InvalidJobTransition(source, target)


@dataclass
class JobRecord:
    """One submitted question and everything known about it so far.

    Mutated only by `jobs.store.JobStore` with the store's lock held --
    never by a worker directly, and never by a reader. Readers get a
    snapshot, so a caller cannot hold a reference and watch it change
    under them mid-serialisation.

    **What is deliberately not here.** No connection string, no password,
    no prompt text, and no raw result rows beyond the governed snapshot
    `result` carries (CLAUDE.md 5.6). `question` *is* retained, because a
    caller polling their own job needs to know which question it was and
    the frontend renders it -- but it is never logged (see `jobs.queue`,
    which logs `job_id` and counts only).

    Attributes:
        job_id: Server-generated opaque id. The only handle a caller
            holds, and never derived from the question, so it leaks
            nothing about the content.
        owner_key: The authenticated subject this job belongs to, from
            `security.oidc.real_caller_subject`. Every read is scoped to
            it -- a mismatch is a 404, not a 403 (CLAUDE.md 5.3's
            anti-enumeration rule).
        tenant_id: Resolved server-side from the caller's identity, never
            from anything the client sent (CLAUDE.md 5.3).
        caller_roles: The roles held *at submission*, used to run the
            graph. Never used to govern a later read of `result`: that is
            re-governed against the reader's *current* roles, because
            roles change (CLAUDE.md 5.4).
        status: Current `JobStatus`.
        idempotency_key: Server-side hash of this submission, used to
            collapse a duplicate submit onto this same job.
        attempts: How many times a worker has started this job. Above 1
            only after a transient-failure retry.
        queued_at / started_at / finished_at: Monotonic-clock instants,
            so a wall-clock change cannot corrupt a duration.
        submitted_at_epoch: Wall-clock submission time, for display only.
        result: The governed `AskResponse`-shaped payload, set once on
            COMPLETED. Re-governed on every read.
        error_message: A `.safe_message`-grade string (CLAUDE.md 5.6) --
            never internal detail, never a stack trace.
        guardrail_reason: Which guardrail refused, when status is
            BLOCKED_BY_GUARDRAIL. A fixed constant, never built from the
            question (CLAUDE.md 5.5: a refusal must not be an oracle).
        correlation_id: Ties every log line and audit record for this job
            together without carrying any of its content.
    """

    job_id: str
    owner_key: str
    question: str
    status: JobStatus = JobStatus.QUEUED
    tenant_id: str | None = None
    caller_roles: tuple[str, ...] = ()
    idempotency_key: str | None = None
    attempts: int = 0

    queued_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    finished_at: float | None = None
    submitted_at_epoch: float = field(default_factory=time.time)

    result: dict[str, Any] | None = None
    error_message: str | None = None
    guardrail_reason: str | None = None
    correlation_id: str | None = None

    #: Request parameters carried through to `run_orchestrated`,
    #: unchanged. A plain dict rather than one field each, so adding a
    #: request option does not change this record's shape.
    request: dict[str, Any] = field(default_factory=dict)

    #: Observability, populated as the run proceeds. `sources_used` and
    #: `model_used` are the two facts the brief requires be recorded for
    #: auditing; both are operator-configured names, not secrets.
    sources_used: list[str] = field(default_factory=list)
    model_used: str | None = None
    #: Per-source confidence, when the deterministic router scored it.
    source_confidence: dict[str, float] = field(default_factory=dict)

    @property
    def queue_wait_seconds(self) -> float:
        """How long this job waited before starting, or has waited so far."""
        end = self.started_at if self.started_at is not None else time.monotonic()
        return max(0.0, end - self.queued_at)

    @property
    def run_seconds(self) -> float | None:
        """How long this job ran, or has been running. None while QUEUED."""
        if self.started_at is None:
            return None
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return max(0.0, end - self.started_at)


__all__ = [
    "LIVE_JOB_STATUSES",
    "TERMINAL_JOB_STATUSES",
    "InvalidJobTransition",
    "JobRecord",
    "JobStatus",
    "assert_transition",
    "can_transition",
]
