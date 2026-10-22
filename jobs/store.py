"""The job store: one lock, every counter, every transition.

**Why the counters live here and not in a limiter.** The brief requires
per-user concurrency enforcement that is atomic and race-safe. The
existing `agent.rate_limit.ConcurrencyLimiter` is both of those for what
it does, but it answers a different question: "may this request start
*now*", with an immediate no. A queue has to answer "may this job start
now, and if not, keep it" -- and the decision to start a job, the
increment of two counters, and the status transition that records it have
to be one indivisible step. Split across a limiter and a store they are
three steps with two windows, and under 100 concurrent submissions those
windows are not theoretical: two workers each see one free slot and both
take it.

So there is exactly one `threading.RLock` in this module, and every
capacity number is read and written under it. `admit` and `promote_next`
are the only two methods that change a counter, and each is a single
critical section. `tests/test_question_queue_concurrency.py` proves this
with real threads -- a sequential test would pass even with the bug.

**Ownership is the isolation boundary.** Every read takes an
`owner_key` and silently returns nothing for a job belonging to someone
else -- a 404 at the API layer, never a distinguishable 403, so a caller
cannot enumerate other users' job ids (CLAUDE.md 5.3). The store never
consults a client-supplied value for this; `api/jobs.py` passes the
subject this server resolved.

**A stored result is not a trusted result.** `result` is the governed
payload captured when the job completed. It is re-governed against the
*reader's current* roles on every read, exactly as
`api.artifacts._result_out` and `api.shares._governed_result_snapshot`
already do (CLAUDE.md 5.4) -- roles change, and a result governed for an
analyst must not come back unredacted for a viewer an hour later. The
store deliberately does not do that re-governing itself: it holds no
opinion about policy. It hands the raw snapshot to `api/jobs.py`, which
runs the same result gate every other read-back surface runs.

**Process-local, like every other control here.** No persistence, no
cross-process coordination (CLAUDE.md 13's standing limitation for rate
limiters, breakers and caches). A multi-worker deployment has one store
per worker, so the per-user cap is per worker; `docs/CONCURRENCY.md`
states that explicitly rather than leaving it implied.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from jobs.models import (
    JobRecord,
    JobStatus,
    assert_transition,
)

logger = logging.getLogger(__name__)


class AdmissionOutcome(StrEnum):
    """Why `JobStore.admit` did what it did.

    Separate from `JobStatus` because these describe the *submission*,
    not the job: `DUPLICATE` and `QUEUE_FULL` do not correspond to any
    job status, and `ADMITTED` says nothing about whether the job is
    already running.
    """

    #: A new job was created and is QUEUED.
    ADMITTED = "admitted"
    #: An identical submission from this same owner is already known;
    #: `AdmissionResult.job` is that original job, untouched.
    DUPLICATE = "duplicate"
    #: The shared queue, or this owner's share of it, is full. Nothing
    #: was created -- this is the backpressure signal (HTTP 429).
    QUEUE_FULL = "queue_full"


@dataclass(frozen=True)
class AdmissionResult:
    """`JobStore.admit`'s verdict, plus the job it concerns."""

    outcome: AdmissionOutcome
    job: JobRecord | None = None
    #: Operator-facing detail for the log line and the 429 body. A fixed
    #: phrase plus counts -- never the question text.
    detail: str = ""


@dataclass(frozen=True)
class QueueStats:
    """A point-in-time capacity picture, for `GET /questions/stats`.

    Every number here is a count. Nothing in this struct carries a
    question, an answer, a user id or a model name -- it is safe to log,
    to expose to an operator, and to scrape (CLAUDE.md 5.8's "telemetry
    counts decisions, never content").
    """

    running_global: int
    queued_global: int
    max_concurrent_global: int
    max_concurrent_per_user: int
    max_queue_depth: int
    retained_jobs: int
    distinct_owners: int
    #: Only ever this caller's own numbers, so one user cannot infer
    #: another's load from the stats endpoint.
    running_for_caller: int
    queued_for_caller: int


def build_idempotency_key(
    *,
    owner_key: str,
    tenant_id: str | None,
    question: str,
    client_key: str | None,
    request: dict[str, Any] | None = None,
) -> str:
    """A stable, owner- and tenant-scoped hash identifying one submission.

    The brief asks for duplicate-submission prevention "through an
    idempotency key or equivalent server-side mechanism". Two things make
    this server-side rather than advisory:

    1. The owner and the tenant are hashed *in*, not merely prefixed, so
       no crafted `client_key` can collide with another user's or another
       tenant's entry. This is the same construction
       `agent.harness.idempotency.build_key` uses, and for the same
       reason stated there.
    2. When the client supplies no key, one is derived from the question
       and the request options. That makes a double-submitted form
       idempotent by default -- the common real cause of a duplicate --
       without the client having to cooperate.

    The question is hashed, never stored in the key, so an idempotency
    table is not a log of everything anyone asked (CLAUDE.md 5.6).
    """
    hasher = hashlib.sha256()
    hasher.update(b"question_job\x00")
    hasher.update(owner_key.encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update((tenant_id or "__no_tenant__").encode("utf-8"))
    hasher.update(b"\x00")
    if client_key is not None:
        # An explicit client key replaces the content hash entirely: the
        # client is asserting "these two submissions are the same
        # operation", which is exactly what an idempotency key means,
        # even if the text differs.
        hasher.update(b"client\x00")
        hasher.update(client_key.encode("utf-8"))
    else:
        hasher.update(b"derived\x00")
        hasher.update(question.strip().casefold().encode("utf-8", errors="replace"))
        hasher.update(b"\x00")
        hasher.update(repr(sorted((request or {}).items())).encode("utf-8", errors="replace"))
    return hasher.hexdigest()


def _snapshot(record: JobRecord) -> JobRecord:
    """A copy a reader may hold without seeing later mutations.

    `dataclasses.replace` alone is a shallow copy, which would share the
    mutable fields -- a caller mid-serialisation could then observe
    `sources_used` growing. Each mutable field is copied explicitly.
    """
    return replace(
        record,
        caller_roles=tuple(record.caller_roles),
        result=dict(record.result) if record.result is not None else None,
        request=dict(record.request),
        sources_used=list(record.sources_used),
        source_confidence=dict(record.source_confidence),
    )


class JobStore:
    """Holds every job, every capacity counter, and the one lock over both.

    Not a singleton by construction -- `jobs.queue` owns the process-wide
    instance, and a test builds its own. That keeps the store itself free
    of the process-lifetime-cache problem `tests/conftest.py` has to
    clear for the other singletons in this codebase.
    """

    def __init__(
        self,
        *,
        max_concurrent_per_user: int,
        max_concurrent_global: int,
        max_queue_depth: int,
        max_queue_depth_per_user: int,
        max_retained_per_user: int,
        result_ttl_seconds: float,
        idempotency_ttl_seconds: float,
    ) -> None:
        self._max_concurrent_per_user = max_concurrent_per_user
        self._max_concurrent_global = max_concurrent_global
        self._max_queue_depth = max_queue_depth
        self._max_queue_depth_per_user = max_queue_depth_per_user
        self._max_retained_per_user = max_retained_per_user
        self._result_ttl_seconds = result_ttl_seconds
        self._idempotency_ttl_seconds = idempotency_ttl_seconds

        #: The single lock. Reentrant because `admit` calls `_evict_*`,
        #: which would otherwise need a lock-free twin of every helper.
        self._lock = threading.RLock()

        self._jobs: dict[str, JobRecord] = {}
        #: owner -> job ids, oldest first. An `OrderedDict` used as an
        #: ordered set, so per-owner eviction is O(1) at the front.
        self._by_owner: dict[str, OrderedDict[str, None]] = {}
        #: (owner, idempotency hash) -> (job id, monotonic expiry).
        self._idempotency: dict[tuple[str, str], tuple[str, float]] = {}

        self._running_global = 0
        self._queued_global = 0
        self._running_by_owner: dict[str, int] = {}
        self._queued_by_owner: dict[str, int] = {}

    # ---- capacity ---------------------------------------------------

    @property
    def lock(self) -> threading.RLock:
        """The store's lock, exposed so `jobs.queue`'s condition variable
        can share it. Sharing one lock between the store and the
        dispatcher's `Condition` is what keeps "a slot freed" and "a
        waiter woken" from being two separately-locked events with a
        window between them."""
        return self._lock

    def has_capacity_for(self, owner_key: str) -> bool:
        """Whether a job for `owner_key` could start right now.

        Read-only and advisory on its own -- a caller must not act on it
        without re-checking under the lock, which `promote_next` does.
        Exposed for the dispatcher's "is there any point looking" check
        and for tests.
        """
        with self._lock:
            return self._has_capacity_locked(owner_key)

    def _has_capacity_locked(self, owner_key: str) -> bool:
        if self._running_global >= self._max_concurrent_global:
            return False
        return self._running_by_owner.get(owner_key, 0) < self._max_concurrent_per_user

    # ---- admission --------------------------------------------------

    def admit(
        self,
        *,
        owner_key: str,
        question: str,
        tenant_id: str | None,
        caller_roles: tuple[str, ...],
        idempotency_key: str,
        correlation_id: str | None,
        request: dict[str, Any],
    ) -> AdmissionResult:
        """Create a QUEUED job, or explain why not. One critical section.

        Order matters and is deliberate:

        1. **Idempotency first.** A duplicate must collapse onto the
           original even when the queue is full -- otherwise a client
           retrying a submission it already made would get a 429 and
           conclude its first attempt was lost.
        2. **Then queue depth**, per-owner before global, so one user
           filling their own share produces a message about their own
           share rather than a misleading "system busy".

        Nothing here starts the job. Promotion to RUNNING is
        `promote_next`'s job and happens in its own critical section, so
        there is exactly one place a concurrency slot is ever taken.
        """
        with self._lock:
            self._expire_idempotency_locked()

            existing = self._idempotency.get((owner_key, idempotency_key))
            if existing is not None:
                job = self._jobs.get(existing[0])
                if job is not None:
                    return AdmissionResult(
                        AdmissionOutcome.DUPLICATE,
                        job=_snapshot(job),
                        detail="an identical submission is already in progress or complete",
                    )
                # The job was evicted but its idempotency entry had not
                # expired yet. Drop the dangling entry and admit afresh
                # rather than returning a DUPLICATE with no job attached.
                self._idempotency.pop((owner_key, idempotency_key), None)

            owner_queued = self._queued_by_owner.get(owner_key, 0)
            if owner_queued >= self._max_queue_depth_per_user:
                return AdmissionResult(
                    AdmissionOutcome.QUEUE_FULL,
                    detail=(
                        f"you already have {owner_queued} question(s) waiting, which is this "
                        f"deployment's per-user queue limit"
                    ),
                )
            if self._queued_global >= self._max_queue_depth:
                return AdmissionResult(
                    AdmissionOutcome.QUEUE_FULL,
                    detail=(f"the shared question queue is full ({self._queued_global} waiting)"),
                )

            job_id = f"q_{uuid.uuid4().hex}"
            record = JobRecord(
                job_id=job_id,
                owner_key=owner_key,
                question=question,
                status=JobStatus.QUEUED,
                tenant_id=tenant_id,
                caller_roles=caller_roles,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
                request=dict(request),
            )
            self._jobs[job_id] = record
            self._by_owner.setdefault(owner_key, OrderedDict())[job_id] = None
            self._queued_global += 1
            self._queued_by_owner[owner_key] = owner_queued + 1
            self._idempotency[(owner_key, idempotency_key)] = (
                job_id,
                time.monotonic() + self._idempotency_ttl_seconds,
            )
            self._evict_for_owner_locked(owner_key)
            return AdmissionResult(AdmissionOutcome.ADMITTED, job=_snapshot(record))

    def promote_next(self) -> JobRecord | None:
        """Move the oldest startable QUEUED job to RUNNING, atomically.

        "Startable" means its owner is under the per-user cap and the
        process is under the global cap. Scanning for the oldest such job
        rather than simply the oldest job is what makes this fair: one
        user at their own 100-job ceiling does not block a second user's
        first question, which a strict FIFO head-of-line queue would.

        Returns the promoted job's snapshot, or `None` when nothing can
        start right now. The counter increment and the status transition
        happen together under the lock, so two dispatcher threads can
        never both promote into the same free slot.
        """
        with self._lock:
            if self._running_global >= self._max_concurrent_global:
                return None
            for job_id, record in self._jobs.items():
                if record.status is not JobStatus.QUEUED:
                    continue
                if not self._has_capacity_locked(record.owner_key):
                    continue
                assert_transition(record.status, JobStatus.RUNNING)
                record.status = JobStatus.RUNNING
                record.started_at = time.monotonic()
                record.attempts += 1
                self._queued_global -= 1
                self._queued_by_owner[record.owner_key] = max(
                    0, self._queued_by_owner.get(record.owner_key, 1) - 1
                )
                self._running_global += 1
                self._running_by_owner[record.owner_key] = (
                    self._running_by_owner.get(record.owner_key, 0) + 1
                )
                logger.debug(
                    "[jobs] promoted job_id=%s to running (running=%d/%d, queued=%d)",
                    job_id,
                    self._running_global,
                    self._max_concurrent_global,
                    self._queued_global,
                )
                return _snapshot(record)
            return None

    # ---- completion -------------------------------------------------

    def finish(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
        guardrail_reason: str | None = None,
        sources_used: list[str] | None = None,
        model_used: str | None = None,
        source_confidence: dict[str, float] | None = None,
    ) -> JobRecord | None:
        """Transition a job to a terminal status and release its capacity.

        Releasing is driven off the status it is *leaving*, which is the
        one rule that keeps the counters honest: a RUNNING job releases a
        concurrency slot, a QUEUED job releases queue depth, and nothing
        else releases anything. An already-terminal job is a no-op (not
        an error): a worker finishing a job that graceful shutdown
        already cancelled is an ordinary race, not a bug.
        """
        if not status.is_terminal:
            raise ValueError(f"finish() requires a terminal status, got {status.value!r}")
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record.status.is_terminal:
                return None
            self._release_locked(record)
            assert_transition(record.status, status)
            record.status = status
            record.finished_at = time.monotonic()
            if result is not None:
                record.result = result
            if error_message is not None:
                record.error_message = error_message
            if guardrail_reason is not None:
                record.guardrail_reason = guardrail_reason
            if sources_used is not None:
                record.sources_used = list(sources_used)
            if model_used is not None:
                record.model_used = model_used
            if source_confidence is not None:
                record.source_confidence = dict(source_confidence)
            return _snapshot(record)

    def _release_locked(self, record: JobRecord) -> None:
        if record.status is JobStatus.RUNNING:
            self._running_global = max(0, self._running_global - 1)
            self._running_by_owner[record.owner_key] = max(
                0, self._running_by_owner.get(record.owner_key, 1) - 1
            )
        elif record.status is JobStatus.QUEUED:
            self._queued_global = max(0, self._queued_global - 1)
            self._queued_by_owner[record.owner_key] = max(
                0, self._queued_by_owner.get(record.owner_key, 1) - 1
            )

    # ---- owner-scoped reads -----------------------------------------

    def get(self, job_id: str, *, owner_key: str) -> JobRecord | None:
        """This owner's job, or `None`.

        `None` covers both "no such job" and "not yours", deliberately
        indistinguishable: the API turns either into the same 404, so a
        caller cannot probe for the existence of another user's job id
        (CLAUDE.md 5.3).
        """
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record.owner_key != owner_key:
                return None
            return _snapshot(record)

    def list_for_owner(
        self,
        owner_key: str,
        *,
        statuses: frozenset[JobStatus] | None = None,
        limit: int = 100,
    ) -> list[JobRecord]:
        """This owner's jobs, newest first. Never anyone else's."""
        with self._lock:
            ids = list(self._by_owner.get(owner_key, OrderedDict()).keys())
            out: list[JobRecord] = []
            for job_id in reversed(ids):
                record = self._jobs.get(job_id)
                if record is None:
                    continue
                if statuses is not None and record.status not in statuses:
                    continue
                out.append(_snapshot(record))
                if len(out) >= limit:
                    break
            return out

    def cancel(self, job_id: str, *, owner_key: str) -> JobRecord | None:
        """Mark this owner's job CANCELLED. `None` if not theirs or already done.

        **Ownership is enforced here, not only by the caller.** Both
        current callers (`jobs.queue.QuestionQueue.cancel` and
        `api.jobs.cancel_question`) already check ownership before calling
        this, and that is correct defence in depth -- but a method that
        takes an `owner_key` and does not check it is a trap for the next
        caller, who would reasonably assume it does. The check is cheap
        and it belongs with the data (CLAUDE.md 5.2: a check at the layer
        above is never the boundary).

        Only sets the status and releases capacity. Signalling the
        in-flight execution to stop is `jobs.queue`'s job, because only
        it holds the envelope -- and the two are deliberately separate:
        the status must flip immediately so the caller's next poll is
        truthful, even though the worker can only notice at its next node
        boundary.
        """
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record.owner_key != owner_key:
                # Indistinguishable from "no such job", exactly as `get`
                # is, so a cancel attempt cannot be used to probe for
                # another user's job id either.
                return None
            return self.finish(
                job_id,
                JobStatus.CANCELLED,
                error_message="Cancelled at your request.",
            )

    def owner_of(self, job_id: str) -> str | None:
        """The owner key for a job id, for `jobs.queue`'s internal use only.

        Never exposed through the API: it exists so the queue can find
        the envelope to signal without having to ask the caller to prove
        ownership a second time (`api/jobs.py` already did).
        """
        with self._lock:
            record = self._jobs.get(job_id)
            return record.owner_key if record is not None else None

    # ---- maintenance ------------------------------------------------

    def expire_queue_waits(self, max_wait_seconds: float) -> list[JobRecord]:
        """Time out every job that has been QUEUED too long.

        A queue draining slower than it fills must tell its callers so.
        Without this a polling client cannot distinguish "still waiting"
        from "will never run", which is the failure mode that makes
        people submit the question again and make it worse.
        """
        timed_out: list[JobRecord] = []
        with self._lock:
            now = time.monotonic()
            stale = [
                job_id
                for job_id, record in self._jobs.items()
                if record.status is JobStatus.QUEUED
                and (now - record.queued_at) >= max_wait_seconds
            ]
            for job_id in stale:
                finished = self.finish(
                    job_id,
                    JobStatus.TIMEOUT,
                    error_message=(
                        "This question waited longer than the configured queue limit and was "
                        "not started. Please try again."
                    ),
                )
                if finished is not None:
                    timed_out.append(finished)
        if timed_out:
            logger.warning(
                "[jobs] %d queued job(s) exceeded the %.0fs queue-wait limit",
                len(timed_out),
                max_wait_seconds,
            )
        return timed_out

    def evict_expired(self) -> int:
        """Drop terminal jobs past their TTL. Returns how many went.

        Live jobs are never evicted by age -- a job still running is not
        stale, however long it has taken, and dropping it would both
        leak its counter and lose the only handle its owner holds.
        """
        with self._lock:
            now = time.monotonic()
            expired = [
                job_id
                for job_id, record in self._jobs.items()
                if record.status.is_terminal
                and record.finished_at is not None
                and (now - record.finished_at) >= self._result_ttl_seconds
            ]
            for job_id in expired:
                self._forget_locked(job_id)
            self._expire_idempotency_locked()
            return len(expired)

    def _expire_idempotency_locked(self) -> None:
        now = time.monotonic()
        for key in [k for k, (_, expiry) in self._idempotency.items() if expiry <= now]:
            self._idempotency.pop(key, None)

    def _evict_for_owner_locked(self, owner_key: str) -> None:
        """Hold one owner to `max_retained_per_user`, oldest terminal first.

        Bounds memory against a burst that arrives faster than the TTL
        can expire it. Only terminal jobs are candidates, for the same
        reason `evict_expired` skips live ones.
        """
        ids = self._by_owner.get(owner_key)
        if ids is None or len(ids) <= self._max_retained_per_user:
            return
        for job_id in list(ids.keys()):
            if len(ids) <= self._max_retained_per_user:
                break
            record = self._jobs.get(job_id)
            if record is None:
                ids.pop(job_id, None)
                continue
            if record.status.is_terminal:
                self._forget_locked(job_id)

    def _forget_locked(self, job_id: str) -> None:
        record = self._jobs.pop(job_id, None)
        if record is None:
            return
        ids = self._by_owner.get(record.owner_key)
        if ids is not None:
            ids.pop(job_id, None)
            if not ids:
                self._by_owner.pop(record.owner_key, None)
        if record.idempotency_key is not None:
            self._idempotency.pop((record.owner_key, record.idempotency_key), None)

    def live_jobs(self) -> list[JobRecord]:
        """Every job still QUEUED or RUNNING, for graceful shutdown."""
        with self._lock:
            return [_snapshot(r) for r in self._jobs.values() if r.status.is_live]

    def stats(self, *, owner_key: str | None = None) -> QueueStats:
        """Counts only -- never content. Caller-scoped where it matters."""
        with self._lock:
            return QueueStats(
                running_global=self._running_global,
                queued_global=self._queued_global,
                max_concurrent_global=self._max_concurrent_global,
                max_concurrent_per_user=self._max_concurrent_per_user,
                max_queue_depth=self._max_queue_depth,
                retained_jobs=len(self._jobs),
                distinct_owners=len(self._by_owner),
                running_for_caller=(
                    self._running_by_owner.get(owner_key, 0) if owner_key is not None else 0
                ),
                queued_for_caller=(
                    self._queued_by_owner.get(owner_key, 0) if owner_key is not None else 0
                ),
            )

    def clear(self) -> None:
        """Drop everything. Tests only -- never called by the running app."""
        with self._lock:
            self._jobs.clear()
            self._by_owner.clear()
            self._idempotency.clear()
            self._running_global = 0
            self._queued_global = 0
            self._running_by_owner.clear()
            self._queued_by_owner.clear()


__all__ = [
    "AdmissionOutcome",
    "AdmissionResult",
    "JobStore",
    "QueueStats",
    "build_idempotency_key",
]
