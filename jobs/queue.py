"""The queue itself: a dispatcher, a bounded worker pool, and the rules
that decide when a QUEUED question becomes a RUNNING one.

**What this module is, and what it deliberately is not.** It is a durable
*in-process* queue: submission returns a job id immediately, work happens
on a bounded pool, and a caller polls. It is not a distributed broker. No
Redis, no Celery, no database table -- consistent with every other control
in this codebase being process-local (CLAUDE.md 13), and stated as a
limitation in `docs/CONCURRENCY.md` rather than left for a reader to
discover. What it does buy is the thing the brief actually asks for: one
authenticated user running up to 100 questions at once, bounded, atomic,
cancellable, and observable.

**Layering: `jobs/` sits below `api/` and never imports it.** The work a
job performs is injected as a `JobRunner`, so this module knows nothing
about FastAPI, `AskResponse`, or `run_orchestrated`. `api/jobs.py` supplies
a runner that calls the *same* `run_orchestrated` entry point and the
*same* `_ask_response_from_state` serializer `POST /ask` uses -- so a
queued question goes through the identical governed path, with the
identical validator, row cap, result gate and provenance (CLAUDE.md 4.5:
reuse the governed path, never build a second one). Injection is what
keeps that true: there is no second pipeline here to drift.

**Resource budgeting is layered, not re-invented.** This queue bounds how
many graph runs are in flight. Each run's *own* consumption is still
bounded by the controls that already existed and are untouched:

  - `agent.rate_limit.get_database_execution_limiter` -- per-database
    concurrency, sized from that database's own pool, so 100 concurrent
    questions cannot exhaust a connection pool however many of them want
    the database at once.
  - `agent.rate_limit.get_llm_call_limiter` -- process-global LLM call
    rate, which is what actually bounds Ollama.
  - `agent.harness` budgets -- per-execution steps, LLM calls, tool
    calls, result bytes and context chars.
  - `db.execution`'s driver-level query timeout and the twice-enforced
    row cap.

So the queue's own cap is the admission gate, not the whole story, and
raising it does not quietly raise any of the above. `jobs.sizing`
cross-checks the queue's cap against the pools underneath it at startup
and warns when they disagree.

**Timeouts are enforced at the node boundary, not with a second thread.**
A naive "run it in another thread and stop waiting" doubles the thread
count per job, which at 100 concurrent questions is the difference between
bounded and not. Instead each job runs on exactly one worker thread, and
its `ExecutionEnvelope` carries a deadline the harness's control boundary
checks before every node (`agent.harness.control.guard_node`). A monitor
thread flips the job's status to TIMEOUT and signals the envelope, so the
caller's next poll is truthful immediately even though the worker can only
notice at its next node boundary. This is the same "stop waiting is not
cancellation" honesty `api/main.py`'s `/ask` already documents -- the
difference is that here the status is correct the moment the deadline
passes, rather than when the work happens to finish.

**Retry is the harness's policy, not a new one.** A failure the harness
classifies as transient (`agent.harness.failures.is_retryable`) is
retried with `agent.harness.retry.RetryPolicy`'s exponential backoff and
jitter, bounded by `QUESTION_MAX_RETRIES`.

The retry happens **in place, on the same worker thread**, so the job
stays RUNNING and keeps its concurrency slot across the backoff. Handing
it back to the queue instead would release the slot -- which sounds
better until the job has to win admission again behind every other
waiter, turning a transient blip into an unbounded wait. The backoffs
this policy produces are short (sub-second to a few seconds, with a 30s
total ceiling) against a question budget measured in minutes, so holding
the slot is the cheaper trade. `decide_retry` also refuses a backoff that
would exceed the execution's remaining deadline, so a job never sleeps
past its own timeout to attempt something that could not finish.

A failure classified as authorization, validation or budget is never
retried, and a guardrail refusal is never retried at all -- a refusal is
a decision, and repeating it is just spending capacity to be told no
again.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from agent.harness.envelope import budgets_from_settings, create_envelope
from agent.harness.failures import FailureClass, is_retryable
from agent.harness.retry import RetryPolicy, decide_retry
from config.settings import Settings
from jobs.models import JobRecord, JobStatus
from jobs.store import (
    AdmissionOutcome,
    AdmissionResult,
    JobStore,
    QueueStats,
    build_idempotency_key,
)
from security.audit_log import reset_correlation_id, set_correlation_id

logger = logging.getLogger(__name__)

#: User-facing text, calm and non-technical, matching
#: `agent.rate_limit`'s own message constants. Module constants rather
#: than f-strings built from the request, so a refusal can never echo
#: the question back and become an oracle (CLAUDE.md 5.5).
QUEUE_DISABLED_MESSAGE = "Asynchronous questions are not enabled on this deployment."
QUEUE_FULL_MESSAGE = (
    "There are too many questions waiting right now -- please try again in a moment."
)
JOB_TIMEOUT_MESSAGE = "This question took longer than the configured limit and was stopped."
JOB_FAILED_MESSAGE = "This question could not be completed."
SHUTDOWN_CANCEL_MESSAGE = "The server is shutting down; this question was not completed."


@dataclass(frozen=True)
class JobOutcome:
    """What a `JobRunner` produced. Returned, never raised.

    A runner signals failure by *returning* an outcome whose status is
    FAILED (or BLOCKED_BY_GUARDRAIL), rather than raising, for two
    reasons. It keeps the failure classification with the code that
    understands the exception -- `api/jobs.py` knows an `AgentError`'s
    `.safe_message`, this module does not -- and it makes the
    "every message shown to a caller is already safe" rule checkable at
    one boundary instead of at every `except`.

    A runner that raises anyway is still handled: `_execute` catches it,
    classifies it as an unknown failure and uses a fixed message, never
    `str(exc)`.

    Attributes:
        status: A terminal `JobStatus`. COMPLETED, FAILED,
            BLOCKED_BY_GUARDRAIL, or TIMEOUT.
        result: The governed, serialized answer payload on COMPLETED.
        error_message: A `.safe_message`-grade string. Never internal
            detail (CLAUDE.md 5.6).
        guardrail_reason: Which guardrail refused, for
            BLOCKED_BY_GUARDRAIL. A fixed constant.
        failure: The harness's classification, which is what decides
            whether a retry is even considered. `None` on success.
        sources_used / model_used / source_confidence: Recorded for
            observability and auditing (brief §3). Operator-configured
            names and numbers, never content.
    """

    status: JobStatus
    result: dict[str, Any] | None = None
    error_message: str | None = None
    guardrail_reason: str | None = None
    failure: FailureClass | None = None
    sources_used: list[str] = field(default_factory=list)
    model_used: str | None = None
    source_confidence: dict[str, float] = field(default_factory=dict)


class JobRunner(Protocol):
    """Performs one job's actual work.

    Implemented by `api/jobs.py`. Receives the job and the envelope the
    queue built for it; the envelope is the only way the runner learns
    about cancellation and the deadline, which is what stops a runner
    from inventing its own budget.
    """

    def __call__(self, job: JobRecord, envelope: Any) -> JobOutcome:  # pragma: no cover
        ...


class QuestionQueue:
    """Admits, schedules, runs, retries and cancels question jobs.

    One instance per process (`get_question_queue`). Threads it owns:

      - **dispatcher** (1): waits on the store's condition, promotes
        startable jobs, hands them to the pool. One thread, so promotion
        order is deterministic and there is no promote/promote race to
        reason about beyond the store's own lock.
      - **monitor** (1): expires queue waits, enforces run deadlines, and
        evicts expired results. Periodic, cheap, and the only thing that
        can turn a stuck job into a TIMEOUT.
      - **workers** (`max_concurrent_questions_global`): the pool jobs
        actually run on, bounded so an overload queues instead of
        spawning threads without limit.
    """

    def __init__(
        self,
        settings: Settings,
        runner: JobRunner,
        *,
        store: JobStore | None = None,
        start_threads: bool = True,
    ) -> None:
        self._settings = settings
        self._runner = runner
        self._store = store or JobStore(
            max_concurrent_per_user=settings.max_concurrent_questions_per_user,
            max_concurrent_global=settings.max_concurrent_questions_global,
            max_queue_depth=settings.question_queue_max_depth,
            max_queue_depth_per_user=settings.question_queue_max_depth_per_user,
            max_retained_per_user=settings.question_max_retained_jobs_per_user,
            result_ttl_seconds=float(settings.question_result_ttl_seconds),
            idempotency_ttl_seconds=float(settings.question_idempotency_ttl_seconds),
        )
        # Shares the store's lock deliberately -- see `JobStore.lock`.
        self._wake = threading.Condition(self._store.lock)
        self._shutting_down = threading.Event()
        #: job_id -> the live envelope, so a cancel can reach a running
        #: execution. Entries are removed when the job finishes, so this
        #: never grows beyond the number of RUNNING jobs.
        self._envelopes: dict[str, Any] = {}
        self._envelope_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=settings.max_concurrent_questions_global,
            thread_name_prefix="question-job",
        )
        self._retry_policy = RetryPolicy(
            max_attempts=settings.question_max_retries + 1,
            jitter=True,
        )
        self._threads: list[threading.Thread] = []
        if start_threads:
            self._start_threads()

    # ---- lifecycle --------------------------------------------------

    def _start_threads(self) -> None:
        for name, target in (
            ("question-dispatcher", self._dispatch_loop),
            ("question-monitor", self._monitor_loop),
        ):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    @property
    def store(self) -> JobStore:
        """The backing store. Exposed for `api/jobs.py`'s owner-scoped
        reads and for tests; never a way around a capacity check, since
        every counter change still goes through the store's own lock."""
        return self._store

    def shutdown(self, grace_seconds: float | None = None) -> None:
        """Stop admitting, signal in-flight work, and wait out the grace.

        Three steps, in this order, each of which matters:

        1. **Stop the loops.** No new job is promoted after this point, so
           the set of in-flight work stops growing and the wait below is
           bounded.
        2. **Cancel every live job.** A QUEUED job becomes CANCELLED
           immediately -- it never started, so there is nothing to wait
           for and nothing to lose. A RUNNING job's envelope is signalled,
           which stops it at its next node boundary rather than mid-query.
        3. **Wait, then stop waiting.** `grace_seconds` bounds how long
           shutdown blocks. Python cannot pre-empt a thread inside a C
           extension (the same limitation `db.execution` documents), so a
           worker deep inside an Ollama call may outlive the grace. It is
           a daemon thread and the process is exiting, so the cost is a
           discarded result, never a corrupted one -- every write a job
           performs is already complete-or-absent.

        Idempotent: calling it twice is a no-op, which matters because
        FastAPI's shutdown hook and a test's explicit teardown can both
        reach it.
        """
        if self._shutting_down.is_set():
            return
        grace = (
            grace_seconds
            if grace_seconds is not None
            else self._settings.question_shutdown_grace_seconds
        )
        self._shutting_down.set()
        with self._wake:
            self._wake.notify_all()

        live = self._store.live_jobs()
        for job in live:
            self._store.finish(
                job.job_id,
                JobStatus.CANCELLED,
                error_message=SHUTDOWN_CANCEL_MESSAGE,
            )
            self._signal_cancel(job.job_id, "server_shutdown")
        if live:
            logger.info(
                "[jobs] graceful shutdown: signalled %d in-flight job(s), waiting up to %.0fs",
                len(live),
                grace,
            )

        deadline = time.monotonic() + grace
        for thread in self._threads:
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)
        # `cancel_futures` drops queued-but-unstarted pool tasks; already
        # running ones are not interrupted, which is what the grace above
        # is for.
        self._pool.shutdown(wait=False, cancel_futures=True)
        logger.info("[jobs] question queue shut down")

    # ---- submission -------------------------------------------------

    def submit(
        self,
        *,
        owner_key: str,
        question: str,
        tenant_id: str | None,
        caller_roles: tuple[str, ...],
        request: dict[str, Any],
        client_idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> AdmissionResult:
        """Admit one question, or say why not.

        Returns the store's `AdmissionResult` unchanged -- ADMITTED,
        DUPLICATE (the original job, so a retried submit is harmless), or
        QUEUE_FULL (backpressure, which `api/jobs.py` turns into a 429).
        Never raises for a capacity condition: a full queue is an ordinary
        operating state, not an error.

        `owner_key` is the subject this server resolved, never anything
        the client sent. The idempotency key is computed here rather than
        accepted from the client so that a client cannot choose a key that
        collides with another user's (CLAUDE.md 5.3) -- a client-supplied
        key is *input* to the hash, never the key itself.
        """
        if self._shutting_down.is_set():
            return AdmissionResult(
                outcome=AdmissionOutcome.QUEUE_FULL,
                detail="the server is shutting down and is not accepting new questions",
            )
        idempotency_key = build_idempotency_key(
            owner_key=owner_key,
            tenant_id=tenant_id,
            question=question,
            client_key=client_idempotency_key,
            request=request,
        )
        result = self._store.admit(
            owner_key=owner_key,
            question=question,
            tenant_id=tenant_id,
            caller_roles=caller_roles,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            request=request,
        )
        with self._wake:
            self._wake.notify_all()
        return result

    def cancel(self, job_id: str, *, owner_key: str) -> JobRecord | None:
        """Cancel this owner's job. `None` if it is not theirs or already done.

        The status flips under the store's lock before the envelope is
        signalled, so a caller polling immediately afterwards sees
        CANCELLED even though the worker has not noticed yet. Reporting
        "running" for a job already decided to be cancelled would be the
        less honest ordering.
        """
        record = self._store.get(job_id, owner_key=owner_key)
        if record is None:
            return None
        cancelled = self._store.cancel(job_id, owner_key=owner_key)
        if cancelled is not None:
            self._signal_cancel(job_id, "cancelled_by_owner")
            with self._wake:
                self._wake.notify_all()
        return cancelled

    def stats(self, *, owner_key: str | None = None) -> QueueStats:
        """Capacity counts, scoped to this caller where it matters."""
        return self._store.stats(owner_key=owner_key)

    # ---- dispatcher -------------------------------------------------

    def _dispatch_loop(self) -> None:
        """Promote startable jobs onto the pool until shutdown.

        Waits on the condition rather than polling, so an idle deployment
        costs nothing, and wakes on every submission and every completion.
        The timeout on the wait is a safety net, not the mechanism: it
        bounds how long a missed notify could stall the queue.
        """
        while not self._shutting_down.is_set():
            with self._wake:
                self._wake.wait(timeout=0.5)
            if self._shutting_down.is_set():
                return
            # Drain as far as capacity allows. `promote_next` returns
            # None the moment nothing can start, so this terminates.
            while not self._shutting_down.is_set():
                job = self._store.promote_next()
                if job is None:
                    break
                try:
                    self._pool.submit(self._execute, job)
                except RuntimeError:
                    # The pool is shutting down. Put the job back so it
                    # is cancelled by shutdown rather than silently
                    # holding a slot forever.
                    self._store.finish(
                        job.job_id,
                        JobStatus.CANCELLED,
                        error_message=SHUTDOWN_CANCEL_MESSAGE,
                    )
                    return

    # ---- monitor ----------------------------------------------------

    def _monitor_loop(self) -> None:
        """Enforce the two deadlines and evict what has expired.

        Deliberately one thread for all jobs rather than a timer per job:
        at 100+ concurrent questions, per-job timers are per-job threads,
        which is the resource exhaustion this subsystem exists to prevent.
        """
        while not self._shutting_down.is_set():
            if self._shutting_down.wait(timeout=1.0):
                return
            try:
                self._store.expire_queue_waits(
                    float(self._settings.question_queue_wait_timeout_seconds)
                )
                self._expire_running_deadlines()
                self._store.evict_expired()
            except Exception:  # noqa: BLE001 - the monitor must never die
                # An accuracy aid fails open; this is a maintenance loop,
                # and a loop that dies on one bad iteration stops
                # enforcing timeouts for every job after it (CLAUDE.md
                # 4.2). Logged with a traceback and retried next tick.
                logger.exception("[jobs] monitor iteration failed; continuing")

    def _expire_running_deadlines(self) -> None:
        """Mark every RUNNING job past its budget as TIMEOUT and signal it."""
        budget = float(self._settings.question_timeout_seconds)
        for job in self._store.live_jobs():
            if job.status is not JobStatus.RUNNING:
                continue
            ran = job.run_seconds
            if ran is None or ran < budget:
                continue
            finished = self._store.finish(
                job.job_id,
                JobStatus.TIMEOUT,
                error_message=JOB_TIMEOUT_MESSAGE,
            )
            if finished is not None:
                logger.warning(
                    "[jobs] job_id=%s exceeded question_timeout_seconds=%.0fs "
                    "(correlation_id=%s) -- cancellation signalled",
                    job.job_id,
                    budget,
                    job.correlation_id,
                )
                self._signal_cancel(job.job_id, "question_timeout_exceeded")
                with self._wake:
                    self._wake.notify_all()

    # ---- execution --------------------------------------------------

    def _execute(self, job: JobRecord) -> None:
        """Run one job to a terminal status. Never raises.

        The whole body is guarded, because a worker that dies with an
        unhandled exception leaves its job RUNNING forever and its
        concurrency slot consumed -- which at this cap means one leaked
        slot per crash until the user can no longer ask anything. The
        `finally` is what guarantees the slot is released and the
        dispatcher is woken, whatever happened above it.

        The correlation ID is bound here, on the **worker** side of the
        thread boundary, for exactly the reason `set_audit_tenant_id`'s
        own comment gives: a `contextvars` value does not cross a raw
        `ThreadPoolExecutor`, so the submitting request's context is not
        this thread's. Binding from `job.correlation_id` rather than
        copying the submitter's context is also the more correct source
        -- a job can start long after `POST /questions` returned, and
        the id recorded on the job is what `GET /questions/{id}` hands
        the caller. Before this, every log line a queued question
        produced read `correlation_id=-`, so the one identifier a user
        can quote matched nothing in the log (`OPS-013`).
        """
        correlation_token = set_correlation_id(job.correlation_id)
        try:
            self._execute_with_correlation(job)
        finally:
            reset_correlation_id(correlation_token)

    def _execute_with_correlation(self, job: JobRecord) -> None:
        """The body of `_execute`, with this job's correlation ID bound."""
        envelope = self._build_envelope(job)
        with self._envelope_lock:
            self._envelopes[job.job_id] = envelope
        try:
            outcome = self._run_with_retry(job, envelope)
            self._store.finish(
                job.job_id,
                outcome.status,
                result=outcome.result,
                error_message=outcome.error_message,
                guardrail_reason=outcome.guardrail_reason,
                sources_used=outcome.sources_used,
                model_used=outcome.model_used,
                source_confidence=outcome.source_confidence,
            )
        except BaseException:
            # Never surfaces internal detail to the caller -- a fixed
            # message, with the real traceback in the log only.
            logger.exception("[jobs] job_id=%s failed unexpectedly in the worker", job.job_id)
            self._store.finish(job.job_id, JobStatus.FAILED, error_message=JOB_FAILED_MESSAGE)
        finally:
            with self._envelope_lock:
                self._envelopes.pop(job.job_id, None)
            with self._wake:
                self._wake.notify_all()

    def _run_with_retry(self, job: JobRecord, envelope: Any) -> JobOutcome:
        """One job's attempts, bounded by the harness's retry policy.

        Retries happen *in place* on this worker thread rather than by
        requeuing: the slot is already held, and handing the job back
        would mean competing for admission again behind every other
        waiter -- turning a transient blip into an unbounded wait. See
        this module's docstring for the full trade-off.

        The backoff is still charged honestly: `decide_retry` refuses a
        backoff that would exceed the envelope's remaining deadline, so a
        job cannot sleep past its own timeout to attempt something that
        could not finish anyway.
        """
        attempt = 0
        elapsed_backoff = 0.0
        while True:
            attempt += 1
            if envelope is not None and getattr(envelope, "is_cancelled", False):
                return JobOutcome(
                    status=JobStatus.CANCELLED,
                    error_message="Cancelled before completion.",
                )
            try:
                outcome = self._runner(job, envelope)
            except BaseException as exc:  # noqa: BLE001 - classified below
                logger.warning(
                    "[jobs] job_id=%s runner raised %s on attempt %d",
                    job.job_id,
                    type(exc).__name__,
                    attempt,
                )
                # UNKNOWN is deliberately not in `agent.harness.failures
                # ._RETRYABLE`, so a runner that raised something nobody
                # classified is surfaced once rather than retried three
                # times into a retry storm -- that module's own reasoning.
                outcome = JobOutcome(
                    status=JobStatus.FAILED,
                    error_message=JOB_FAILED_MESSAGE,
                    failure=FailureClass.UNKNOWN,
                )

            if outcome.status is JobStatus.COMPLETED:
                return outcome
            if outcome.status is JobStatus.BLOCKED_BY_GUARDRAIL:
                # Never retried: a refusal is a decision, and repeating it
                # only spends capacity to be told no again.
                return outcome
            if outcome.failure is None or not is_retryable(outcome.failure):
                return outcome

            decision = decide_retry(
                failure=outcome.failure,
                attempt=attempt,
                policy=self._retry_policy,
                envelope=envelope,
                elapsed_retry_seconds=elapsed_backoff,
            )
            if not decision.should_retry:
                logger.info(
                    "[jobs] job_id=%s not retrying after attempt %d (%s)",
                    job.job_id,
                    attempt,
                    decision.reason,
                )
                return outcome

            logger.info(
                "[jobs] job_id=%s retrying attempt %d after %.2fs backoff (%s)",
                job.job_id,
                attempt + 1,
                decision.backoff_seconds,
                outcome.failure.value,
            )
            elapsed_backoff += decision.backoff_seconds
            # Interruptible sleep: a cancel or a shutdown during a
            # backoff must not have to wait the backoff out.
            if self._shutting_down.wait(timeout=decision.backoff_seconds):
                return JobOutcome(status=JobStatus.CANCELLED, error_message=SHUTDOWN_CANCEL_MESSAGE)

    def _build_envelope(self, job: JobRecord) -> Any:
        """The job's execution envelope, or `None` when the harness is off.

        Budgets come from the same `budgets_from_settings` the `/ask` path
        uses, with one override: `max_duration_seconds` becomes
        `QUESTION_TIMEOUT_SECONDS` rather than `REQUEST_TIMEOUT_SECONDS`.
        A queued question has no caller blocking on it, so the request
        ceiling is the wrong number -- and the brief makes the question
        timeout its own configurable control.
        """
        if not self._settings.enable_agent_harness:
            return None
        budgets = replace(
            budgets_from_settings(self._settings),
            max_duration_seconds=float(self._settings.question_timeout_seconds),
        )
        return create_envelope(
            graph_name="question_job",
            request_id=job.job_id,
            session_id=job.request.get("session_id"),
            conversation_id=job.request.get("conversation_id"),
            correlation_id=job.correlation_id,
            tenant_id=job.tenant_id,
            user_id=job.owner_key,
            caller_roles=tuple(job.caller_roles),
            model=job.request.get("model"),
            budgets=budgets,
        )

    def _signal_cancel(self, job_id: str, reason: str) -> None:
        """Tell a running execution to stop at its next node boundary.

        Best-effort by construction: a job that has not started has no
        envelope yet, and one that just finished has already removed its
        own. Both are no-ops rather than errors.
        """
        with self._envelope_lock:
            envelope = self._envelopes.get(job_id)
        if envelope is None:
            return
        with contextlib.suppress(Exception):
            envelope.cancel(reason)


# ---- process-wide singleton -----------------------------------------

_queue: QuestionQueue | None = None
_queue_lock = threading.Lock()


def get_question_queue(settings: Settings, runner: JobRunner | None = None) -> QuestionQueue | None:
    """The process-wide queue, created on first use.

    First-call-wins, exactly like `agent.rate_limit.get_ask_concurrency_limiter`
    and `agent.llm_client._get_ollama_client`: a `Settings` change takes
    effect on restart, which is how every other process-lifetime singleton
    in this codebase behaves.

    Returns `None` when `ENABLE_QUESTION_QUEUE` is off, so a caller gets a
    clear "not enabled" rather than a silently inert queue. Also returns
    `None` when no `runner` has ever been supplied -- the queue cannot do
    anything useful without one, and constructing a threadless husk would
    hide the wiring mistake instead of surfacing it.
    """
    global _queue
    if not settings.enable_question_queue:
        return None
    with _queue_lock:
        if _queue is None:
            if runner is None:
                return None
            _queue = QuestionQueue(settings, runner)
            logger.info(
                "[jobs] question queue started "
                "(per_user=%d, global=%d, queue_depth=%d, timeout=%ds)",
                settings.max_concurrent_questions_per_user,
                settings.max_concurrent_questions_global,
                settings.question_queue_max_depth,
                settings.question_timeout_seconds,
            )
        return _queue


def reset_question_queue() -> None:
    """Drop the singleton, shutting down the old one. Tests only.

    `tests/conftest.py` calls this between tests for the same reason it
    clears the other process-lifetime caches: without it, a queue built
    from one test's `Settings` is silently reused by the next, and a
    monkeypatched runner is ignored.
    """
    global _queue
    with _queue_lock:
        existing = _queue
        _queue = None
    if existing is not None:
        with contextlib.suppress(Exception):
            existing.shutdown(grace_seconds=0.1)


__all__ = [
    "JOB_FAILED_MESSAGE",
    "JOB_TIMEOUT_MESSAGE",
    "QUEUE_DISABLED_MESSAGE",
    "QUEUE_FULL_MESSAGE",
    "JobOutcome",
    "JobRunner",
    "QuestionQueue",
    "get_question_queue",
    "reset_question_queue",
]
