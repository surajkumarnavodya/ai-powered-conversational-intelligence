"""A queued question's own log lines carry its correlation ID.

UAT-readiness Phase 11 (`OPS-013`).

Measured against the live app 2026-10-30: a question submitted to
`POST /questions` produced 23 log lines across `agent.nodes`,
`agent.graph`, `db.connection` and `jobs.queue`, and **every one read
`correlation_id=-`**. The job's real id appeared exactly once in the
whole log, and only because one warning interpolated `job.correlation_id`
into its message text by hand. So the one identifier a user can quote
from `GET /questions/{job_id}` matched nothing a support engineer could
grep for -- during a UAT whose J12 step is "trigger an action, then find
it in the audit".

The cause is the thread boundary `security/audit_log.py`'s own comment
already documents: a `contextvars` value does not cross a raw
`concurrent.futures.ThreadPoolExecutor`. `api/main.py` handles this for
`/ask` with `contextvars.copy_context()` at the submit site (Phase 9's
`UI-004` fix); `jobs/queue.py` did not handle it at all. The fix binds
`job.correlation_id` inside `_execute`, on the worker side -- the same
place and the same reason `agent.graph.run_agent` binds the tenant.

Binding from the job record rather than copying the submitter's context
is also the more correct source: a job can start long after
`POST /questions` returned, and the id on the job is the one the caller
was given.

These tests use **real threads**, because the whole defect is a thread
boundary: a test that called `_execute` directly would pass with the
binding removed, since the calling thread already has the id bound.
"""

from __future__ import annotations

import threading
import time

from config.settings import Settings
from jobs.queue import JobOutcome, QuestionQueue
from jobs.store import JobStatus
from security.audit_log import get_correlation_id, set_correlation_id


def _settings(**overrides) -> Settings:
    base = {
        "max_concurrent_questions_per_user": 3,
        "max_concurrent_questions_global": 10,
        "question_queue_max_depth": 50,
        "question_queue_max_depth_per_user": 50,
        "question_timeout_seconds": 30,
        "question_max_retries": 0,
    }
    base.update(overrides)
    return Settings(**base)


def _submit(queue: QuestionQueue, correlation_id: str | None, owner_key: str = "u1"):
    return queue.submit(
        owner_key=owner_key,
        question="how many products are there?",
        tenant_id="t",
        caller_roles=(),
        request={},
        correlation_id=correlation_id,
    )


def _drain(queue: QuestionQueue, deadline: float = 5.0) -> None:
    started = time.monotonic()
    while time.monotonic() - started < deadline:
        stats = queue.stats()
        if stats.running_global == 0 and stats.queued_global == 0:
            return
        time.sleep(0.02)


class TestTheWorkerThreadSeesTheJobsCorrelationId:
    def test_the_runner_runs_with_the_jobs_correlation_id_bound(self) -> None:
        seen: list[str | None] = []
        thread_ids: list[int] = []

        def runner(job, envelope):
            seen.append(get_correlation_id())
            thread_ids.append(threading.get_ident())
            return JobOutcome(status=JobStatus.COMPLETED, result={})

        queue = QuestionQueue(_settings(), runner)
        try:
            _submit(queue, "corr-abc-123")
            _drain(queue)
        finally:
            queue.shutdown(grace_seconds=2.0)

        assert seen == ["corr-abc-123"]
        # Non-vacuity: if the runner had executed on this thread, the id
        # would have been visible with or without the fix.
        assert thread_ids and thread_ids[0] != threading.get_ident()

    def test_a_job_without_a_correlation_id_leaves_the_context_unset(self) -> None:
        """`None` must bind as `None`, not as the string "None" and not as
        whatever the previous job on this pooled thread had."""
        seen: list[str | None] = []

        def runner(job, envelope):
            seen.append(get_correlation_id())
            return JobOutcome(status=JobStatus.COMPLETED, result={})

        queue = QuestionQueue(_settings(), runner)
        try:
            _submit(queue, None)
            _drain(queue)
        finally:
            queue.shutdown(grace_seconds=2.0)

        assert seen == [None]

    def test_one_jobs_id_never_leaks_into_the_next_on_a_reused_thread(self) -> None:
        """The pool reuses threads, so the `finally` reset is the control.

        A single worker forces both jobs onto the same thread, which is
        the only configuration where a missing reset is observable.
        """
        seen: list[tuple[str, str | None]] = []
        order = threading.Lock()

        def runner(job, envelope):
            with order:
                seen.append((job.question, get_correlation_id()))
            return JobOutcome(status=JobStatus.COMPLETED, result={})

        queue = QuestionQueue(
            _settings(max_concurrent_questions_per_user=1, max_concurrent_questions_global=1),
            runner,
        )
        try:
            queue.submit(
                owner_key="u1",
                question="first",
                tenant_id="t",
                caller_roles=(),
                request={},
                correlation_id="corr-first",
            )
            queue.submit(
                owner_key="u1",
                question="second",
                tenant_id="t",
                caller_roles=(),
                request={},
                correlation_id=None,
            )
            _drain(queue)
        finally:
            queue.shutdown(grace_seconds=2.0)

        assert dict(seen) == {"first": "corr-first", "second": None}

    def test_the_submitting_threads_own_id_is_not_what_the_worker_sees(self) -> None:
        """Proves the id comes from the job record, not from an inherited
        context -- which is the difference between this fix and a
        `copy_context()` at the submit site. A job's recorded id is what
        `GET /questions/{id}` returns, so it is the one that must appear
        in the log."""
        seen: list[str | None] = []

        def runner(job, envelope):
            seen.append(get_correlation_id())
            return JobOutcome(status=JobStatus.COMPLETED, result={})

        queue = QuestionQueue(_settings(), runner)
        token = set_correlation_id("the-submitting-requests-id")
        try:
            _submit(queue, "the-jobs-own-id")
            _drain(queue)
        finally:
            from security.audit_log import reset_correlation_id

            reset_correlation_id(token)
            queue.shutdown(grace_seconds=2.0)

        assert seen == ["the-jobs-own-id"]
