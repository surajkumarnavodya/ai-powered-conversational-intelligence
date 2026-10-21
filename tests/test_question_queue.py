"""The question queue's own unit and concurrency tests.

Split by what each group proves:

  - **Configuration** -- every invalid `MAX_CONCURRENT_QUESTIONS_PER_USER`
    is rejected at startup, and the cross-field rules hold.
  - **Statuses and transitions** -- the whitelist is real, and the
    vocabulary is exactly the seven the API publishes.
  - **Admission, queueing and idempotency** -- a job beyond the limit is
    queued rather than dropped, and a duplicate collapses.
  - **Concurrency** -- with *real threads*, because a sequential test
    passes even with the race it is meant to catch (CLAUDE.md 7).
  - **Ownership** -- a cross-owner read returns nothing, which the API
    turns into the same 404 a nonexistent job gets.
  - **Cancellation, timeouts and shutdown.**

Everything here is fully mocked: the injected `JobRunner` is a local
function, so no Ollama, no database and no graph execution is involved.
"""

from __future__ import annotations

import threading
import time

import pytest
from pydantic import ValidationError

from config.settings import (
    MAX_SAFE_CONCURRENT_QUESTIONS_PER_USER,
    ConfigurationError,
    Settings,
)
from jobs.models import (
    LIVE_JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
    InvalidJobTransition,
    JobStatus,
    assert_transition,
    can_transition,
)
from jobs.queue import JobOutcome, QuestionQueue
from jobs.store import AdmissionOutcome, JobStore, build_idempotency_key


def _settings(**overrides) -> Settings:
    """A `Settings` with queue sizing suited to a fast test.

    Small numbers deliberately: a test that has to start 100 real jobs to
    prove a cap takes 100 jobs' worth of wall clock, while a cap of 3
    proves the same property. `TestRealisticConcurrencyAtTheConfiguredLimit`
    is the one place that uses the real default.
    """
    base = {
        "max_concurrent_questions_per_user": 3,
        "max_concurrent_questions_global": 10,
        "question_queue_max_depth": 100,
        "question_queue_max_depth_per_user": 100,
        "question_timeout_seconds": 30,
        "question_max_retries": 0,
    }
    base.update(overrides)
    return Settings(**base)


def _completing_runner(delay: float = 0.0):
    """A runner that records what it ran and completes successfully."""
    seen: list[str] = []
    lock = threading.Lock()

    def runner(job, envelope):
        with lock:
            seen.append(job.job_id)
        if delay:
            time.sleep(delay)
        return JobOutcome(
            status=JobStatus.COMPLETED,
            result={"answer": "ok", "status": "succeeded"},
            sources_used=["sql"],
            model_used="llama3.1:8b",
        )

    runner.seen = seen  # type: ignore[attr-defined]
    return runner


def _drain(queue: QuestionQueue, timeout: float = 30.0) -> None:
    """Block until nothing is live, or fail the test loudly.

    A bare `sleep` would make these tests flaky on a slow machine and
    slow on a fast one; polling the queue's own counters is both exact
    and fast.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stats = queue.stats()
        if stats.running_global == 0 and stats.queued_global == 0:
            return
        time.sleep(0.01)
    pytest.fail(f"queue did not drain within {timeout}s: {queue.stats()}")


class TestConcurrencyConfigurationValidation:
    """`MAX_CONCURRENT_QUESTIONS_PER_USER` is validated at startup.

    The brief requires non-numeric, zero, negative and "unsafe" values to
    be rejected, with a safe hard maximum applied in code. Each of those
    is a separate test, because "it raises something" is not the same
    claim as "it raises for this reason".
    """

    def test_the_documented_default_is_one_hundred(self) -> None:
        assert _settings().max_concurrent_questions_per_user == 3
        assert Settings().max_concurrent_questions_per_user == 100

    def test_a_valid_value_is_accepted(self) -> None:
        settings = _settings(
            max_concurrent_questions_per_user=100, max_concurrent_questions_global=200
        )
        assert settings.max_concurrent_questions_per_user == 100

    @pytest.mark.parametrize("bad", ["abc", "", "ten", "1.5.2"])
    def test_a_non_numeric_value_is_rejected(self, bad: str) -> None:
        with pytest.raises((ConfigurationError, ValidationError)):
            Settings(max_concurrent_questions_per_user=bad)

    def test_zero_is_rejected(self) -> None:
        with pytest.raises((ConfigurationError, ValidationError)):
            Settings(max_concurrent_questions_per_user=0)

    @pytest.mark.parametrize("bad", [-1, -100])
    def test_a_negative_value_is_rejected(self, bad: int) -> None:
        with pytest.raises((ConfigurationError, ValidationError)):
            Settings(max_concurrent_questions_per_user=bad)

    def test_the_in_code_hard_maximum_is_enforced(self) -> None:
        """A value above the safe maximum is refused, not silently clamped.

        Clamping would be worse than refusing: an operator who asked for
        10000 and got 500 has a deployment that does not do what their
        config says, and nothing tells them so.
        """
        assert MAX_SAFE_CONCURRENT_QUESTIONS_PER_USER == 500
        Settings(
            max_concurrent_questions_per_user=MAX_SAFE_CONCURRENT_QUESTIONS_PER_USER,
            max_concurrent_questions_global=MAX_SAFE_CONCURRENT_QUESTIONS_PER_USER,
        )
        with pytest.raises((ConfigurationError, ValidationError)):
            Settings(max_concurrent_questions_per_user=MAX_SAFE_CONCURRENT_QUESTIONS_PER_USER + 1)

    def test_a_global_cap_below_the_per_user_cap_is_rejected(self) -> None:
        """Otherwise the per-user cap is a number no user can reach."""
        with pytest.raises(ConfigurationError, match="MAX_CONCURRENT_QUESTIONS_GLOBAL"):
            Settings(
                max_concurrent_questions_per_user=100,
                max_concurrent_questions_global=50,
            )

    def test_a_per_user_queue_share_above_the_shared_depth_is_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="QUESTION_QUEUE_MAX_DEPTH_PER_USER"):
            Settings(question_queue_max_depth=10, question_queue_max_depth_per_user=50)


class TestJobStatusVocabulary:
    """The seven statuses, and the transition whitelist over them."""

    def test_exactly_the_seven_statuses_the_brief_names_exist(self) -> None:
        """An exhaustive equality check, not a subset check.

        Deliberately exhaustive so adding or renaming a status is a
        failing test rather than a silent API change -- the same
        discipline `tests/test_sql_agent_integration.py` applies to the
        graph's node list.
        """
        assert {s.value for s in JobStatus} == {
            "queued",
            "running",
            "completed",
            "failed",
            "cancelled",
            "timeout",
            "blocked_by_guardrail",
        }

    def test_the_terminal_and_live_sets_partition_every_status(self) -> None:
        assert set(JobStatus) == TERMINAL_JOB_STATUSES | LIVE_JOB_STATUSES
        assert not (TERMINAL_JOB_STATUSES & LIVE_JOB_STATUSES)

    def test_a_terminal_status_has_no_outgoing_edge(self) -> None:
        for terminal in TERMINAL_JOB_STATUSES:
            for target in JobStatus:
                assert not can_transition(terminal, target), f"{terminal} -> {target}"

    def test_both_live_statuses_may_be_cancelled_or_time_out(self) -> None:
        for live in LIVE_JOB_STATUSES:
            assert can_transition(live, JobStatus.CANCELLED)
            assert can_transition(live, JobStatus.TIMEOUT)

    def test_a_guardrail_refusal_is_reachable_while_running_too(self) -> None:
        """An output guardrail refuses after the run has started."""
        assert can_transition(JobStatus.RUNNING, JobStatus.BLOCKED_BY_GUARDRAIL)
        assert can_transition(JobStatus.QUEUED, JobStatus.BLOCKED_BY_GUARDRAIL)

    def test_the_whitelist_is_not_vacuous(self) -> None:
        """Proving the guard actually refuses something.

        A whitelist that happens to allow every edge would pass every
        other test in this class (CLAUDE.md 7: prove a load-bearing guard
        is not vacuous).
        """
        with pytest.raises(InvalidJobTransition):
            assert_transition(JobStatus.COMPLETED, JobStatus.RUNNING)
        with pytest.raises(InvalidJobTransition):
            assert_transition(JobStatus.RUNNING, JobStatus.QUEUED)

    def test_only_completed_maps_to_succeeded(self) -> None:
        assert JobStatus.COMPLETED.to_agent_status() == "succeeded"
        for status in TERMINAL_JOB_STATUSES - {JobStatus.COMPLETED}:
            assert status.to_agent_status() == "failed"
        assert JobStatus.QUEUED.to_agent_status() == "pending"
        assert JobStatus.RUNNING.to_agent_status() == "pending"

    def test_harness_execution_states_map_without_leaking_internals(self) -> None:
        from agent.harness.lifecycle import ExecutionState

        assert JobStatus.from_execution_state(ExecutionState.COMPLETED) is JobStatus.COMPLETED
        assert JobStatus.from_execution_state(ExecutionState.TIMED_OUT) is JobStatus.TIMEOUT
        assert JobStatus.from_execution_state(ExecutionState.CANCELLED) is JobStatus.CANCELLED
        # Every internal position reads as "still working on it".
        for internal in (
            ExecutionState.VALIDATING,
            ExecutionState.WAITING_FOR_TOOL,
            ExecutionState.RETRYING,
            ExecutionState.RECOVERING,
        ):
            assert JobStatus.from_execution_state(internal) is JobStatus.RUNNING


class TestAdmissionAndQueueing:
    """Beyond the limit a question queues; it is never silently dropped."""

    def test_a_question_beyond_the_limit_is_queued_not_rejected(self) -> None:
        store = JobStore(
            max_concurrent_per_user=1,
            max_concurrent_global=1,
            max_queue_depth=10,
            max_queue_depth_per_user=10,
            max_retained_per_user=100,
            result_ttl_seconds=60,
            idempotency_ttl_seconds=60,
        )
        for i in range(5):
            result = store.admit(
                owner_key="u1",
                question=f"q{i}",
                tenant_id="t",
                caller_roles=("analyst",),
                idempotency_key=f"k{i}",
                correlation_id=None,
                request={},
            )
            assert result.outcome is AdmissionOutcome.ADMITTED
            assert result.job is not None
            assert result.job.status is JobStatus.QUEUED

        # Only one may run; the rest stay queued rather than vanishing.
        assert store.promote_next() is not None
        assert store.promote_next() is None
        assert store.stats().running_global == 1
        assert store.stats().queued_global == 4

    def test_a_full_per_user_queue_is_backpressure_not_an_unbounded_buffer(self) -> None:
        store = JobStore(
            max_concurrent_per_user=1,
            max_concurrent_global=1,
            max_queue_depth=100,
            max_queue_depth_per_user=2,
            max_retained_per_user=100,
            result_ttl_seconds=60,
            idempotency_ttl_seconds=60,
        )
        for i in range(2):
            assert (
                store.admit(
                    owner_key="u1",
                    question=f"q{i}",
                    tenant_id=None,
                    caller_roles=(),
                    idempotency_key=f"k{i}",
                    correlation_id=None,
                    request={},
                ).outcome
                is AdmissionOutcome.ADMITTED
            )
        refused = store.admit(
            owner_key="u1",
            question="one too many",
            tenant_id=None,
            caller_roles=(),
            idempotency_key="k-extra",
            correlation_id=None,
            request={},
        )
        assert refused.outcome is AdmissionOutcome.QUEUE_FULL
        assert refused.job is None
        # The refusal names the per-user limit, not a misleading
        # "system busy".
        assert "per-user" in refused.detail

    def test_one_user_filling_their_share_does_not_starve_another(self) -> None:
        """Promotion scans for the oldest *startable* job, not the oldest.

        A strict FIFO head-of-line queue would let one user at their own
        cap block every later user's first question.
        """
        store = JobStore(
            max_concurrent_per_user=1,
            max_concurrent_global=5,
            max_queue_depth=100,
            max_queue_depth_per_user=100,
            max_retained_per_user=100,
            result_ttl_seconds=60,
            idempotency_ttl_seconds=60,
        )
        for i in range(3):
            store.admit(
                owner_key="greedy",
                question=f"g{i}",
                tenant_id=None,
                caller_roles=(),
                idempotency_key=f"g{i}",
                correlation_id=None,
                request={},
            )
        store.admit(
            owner_key="polite",
            question="my only question",
            tenant_id=None,
            caller_roles=(),
            idempotency_key="p0",
            correlation_id=None,
            request={},
        )
        first = store.promote_next()
        second = store.promote_next()
        assert first is not None and first.owner_key == "greedy"
        assert second is not None and second.owner_key == "polite"


class TestIdempotency:
    """A duplicate submission is absorbed, never run twice."""

    def test_an_identical_resubmission_returns_the_original_job(self) -> None:
        queue = QuestionQueue(_settings(), _completing_runner(), start_threads=False)
        first = queue.submit(
            owner_key="u1", question="same", tenant_id="t", caller_roles=(), request={}
        )
        second = queue.submit(
            owner_key="u1", question="same", tenant_id="t", caller_roles=(), request={}
        )
        assert first.outcome is AdmissionOutcome.ADMITTED
        assert second.outcome is AdmissionOutcome.DUPLICATE
        assert second.job is not None and first.job is not None
        assert second.job.job_id == first.job.job_id

    def test_a_different_question_is_not_a_duplicate(self) -> None:
        queue = QuestionQueue(_settings(), _completing_runner(), start_threads=False)
        a = queue.submit(
            owner_key="u1", question="first", tenant_id="t", caller_roles=(), request={}
        )
        b = queue.submit(
            owner_key="u1", question="second", tenant_id="t", caller_roles=(), request={}
        )
        assert b.outcome is AdmissionOutcome.ADMITTED
        assert a.job is not None and b.job is not None
        assert a.job.job_id != b.job.job_id

    def test_an_explicit_client_key_overrides_the_content_hash(self) -> None:
        """Two different questions under one key are one operation."""
        queue = QuestionQueue(_settings(), _completing_runner(), start_threads=False)
        a = queue.submit(
            owner_key="u1",
            question="first",
            tenant_id="t",
            caller_roles=(),
            request={},
            client_idempotency_key="form-submit-7",
        )
        b = queue.submit(
            owner_key="u1",
            question="totally different",
            tenant_id="t",
            caller_roles=(),
            request={},
            client_idempotency_key="form-submit-7",
        )
        assert b.outcome is AdmissionOutcome.DUPLICATE
        assert a.job is not None and b.job is not None
        assert b.job.job_id == a.job.job_id

    def test_the_same_question_from_two_users_is_never_one_job(self) -> None:
        """The owner is hashed in, so a key cannot cross a user boundary."""
        queue = QuestionQueue(_settings(), _completing_runner(), start_threads=False)
        a = queue.submit(
            owner_key="u1",
            question="shared text",
            tenant_id="t",
            caller_roles=(),
            request={},
            client_idempotency_key="k",
        )
        b = queue.submit(
            owner_key="u2",
            question="shared text",
            tenant_id="t",
            caller_roles=(),
            request={},
            client_idempotency_key="k",
        )
        assert b.outcome is AdmissionOutcome.ADMITTED
        assert a.job is not None and b.job is not None
        assert a.job.job_id != b.job.job_id

    def test_the_same_question_from_two_tenants_is_never_one_job(self) -> None:
        """The tenant is hashed in -- a shared entry would be the exact
        cross-tenant leak the platform exists to prevent."""
        assert build_idempotency_key(
            owner_key="u1", tenant_id="t1", question="q", client_key="k"
        ) != build_idempotency_key(owner_key="u1", tenant_id="t2", question="q", client_key="k")

    def test_the_key_does_not_contain_the_question(self) -> None:
        """A debug dump of the idempotency table is not a log of every
        question anyone asked (CLAUDE.md 5.6)."""
        key = build_idempotency_key(
            owner_key="u1",
            tenant_id="t",
            question="what is the average salary by department",
            client_key=None,
        )
        assert "salary" not in key
        assert len(key) == 64 and all(c in "0123456789abcdef" for c in key)


class TestPerUserConcurrencyUnderRealThreads:
    """The cap holds under genuine parallel submission.

    These use real threads deliberately. A sequential test of an
    admission counter passes even when the check-then-increment is
    racy, which is the one bug that actually matters here (CLAUDE.md 7).
    """

    def test_the_per_user_cap_is_never_exceeded(self) -> None:
        settings = _settings(
            max_concurrent_questions_per_user=4, max_concurrent_questions_global=20
        )
        peak = 0
        live = 0
        lock = threading.Lock()

        def runner(job, envelope):
            nonlocal peak, live
            with lock:
                live += 1
                peak = max(peak, live)
            time.sleep(0.02)
            with lock:
                live -= 1
            return JobOutcome(status=JobStatus.COMPLETED, result={})

        queue = QuestionQueue(settings, runner)
        try:
            threads = [
                threading.Thread(
                    target=queue.submit,
                    kwargs={
                        "owner_key": "u1",
                        "question": f"q{i}",
                        "tenant_id": "t",
                        "caller_roles": (),
                        "request": {"n": i},
                    },
                )
                for i in range(40)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            _drain(queue)
            assert peak <= 4, f"per-user cap exceeded: peak {peak}"
            assert peak == 4, f"cap was never actually reached (peak {peak}) -- test is vacuous"
        finally:
            queue.shutdown(grace_seconds=2.0)

    def test_the_global_cap_is_never_exceeded_across_users(self) -> None:
        settings = _settings(max_concurrent_questions_per_user=5, max_concurrent_questions_global=6)
        peak = 0
        live = 0
        lock = threading.Lock()

        def runner(job, envelope):
            nonlocal peak, live
            with lock:
                live += 1
                peak = max(peak, live)
            time.sleep(0.02)
            with lock:
                live -= 1
            return JobOutcome(status=JobStatus.COMPLETED, result={})

        queue = QuestionQueue(settings, runner)
        try:
            threads = [
                threading.Thread(
                    target=queue.submit,
                    kwargs={
                        "owner_key": f"u{i % 5}",
                        "question": f"q{i}",
                        "tenant_id": "t",
                        "caller_roles": (),
                        "request": {"n": i},
                    },
                )
                for i in range(40)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            _drain(queue)
            assert peak <= 6, f"global cap exceeded: peak {peak}"
        finally:
            queue.shutdown(grace_seconds=2.0)

    def test_every_submitted_question_eventually_runs(self) -> None:
        """Queued means deferred, not discarded."""
        settings = _settings(max_concurrent_questions_per_user=2, max_concurrent_questions_global=4)
        runner = _completing_runner(delay=0.005)
        queue = QuestionQueue(settings, runner)
        try:
            for i in range(30):
                result = queue.submit(
                    owner_key="u1",
                    question=f"q{i}",
                    tenant_id="t",
                    caller_roles=(),
                    request={"n": i},
                )
                assert result.outcome is AdmissionOutcome.ADMITTED
            _drain(queue)
            assert len(runner.seen) == 30  # type: ignore[attr-defined]
            jobs = queue.store.list_for_owner("u1", limit=100)
            assert all(j.status is JobStatus.COMPLETED for j in jobs)
        finally:
            queue.shutdown(grace_seconds=2.0)


class TestRealisticConcurrencyAtTheConfiguredLimit:
    """The brief's actual requirement: 100 concurrent questions, one user.

    Uses the real default cap (100) rather than a small test value, and
    submits from many threads at once, so this is the configured limit
    being exercised rather than a scaled-down analogue.
    """

    def test_one_hundred_concurrent_questions_from_one_user(self) -> None:
        settings = Settings(
            max_concurrent_questions_per_user=100,
            max_concurrent_questions_global=120,
            question_queue_max_depth=400,
            question_queue_max_depth_per_user=400,
        )
        peak = 0
        live = 0
        completed = 0
        lock = threading.Lock()

        def runner(job, envelope):
            nonlocal peak, live, completed
            with lock:
                live += 1
                peak = max(peak, live)
            # Long enough that all 100 genuinely overlap rather than
            # finishing one at a time as they are promoted.
            time.sleep(0.15)
            with lock:
                live -= 1
                completed += 1
            return JobOutcome(status=JobStatus.COMPLETED, result={"answer": "ok"})

        queue = QuestionQueue(settings, runner)
        try:
            threads = [
                threading.Thread(
                    target=queue.submit,
                    kwargs={
                        "owner_key": "power-user",
                        "question": f"question {i}",
                        "tenant_id": "t1",
                        "caller_roles": ("analyst",),
                        "request": {"n": i},
                    },
                )
                for i in range(100)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            _drain(queue, timeout=60.0)

            assert completed == 100, f"only {completed} of 100 completed"
            assert peak <= 100, f"per-user cap exceeded: peak {peak}"
            # The point of the test: they really did run concurrently.
            assert peak >= 50, (
                f"peak concurrency was only {peak} -- the questions were "
                f"serialised, so this test would not have caught a cap bug"
            )
            stats = queue.stats(owner_key="power-user")
            assert stats.running_global == 0
            assert stats.queued_global == 0
        finally:
            queue.shutdown(grace_seconds=5.0)


class TestOwnershipIsolation:
    """A job belongs to exactly one subject, and nobody else can see it."""

    def _queue_with_job(self) -> tuple[QuestionQueue, str]:
        queue = QuestionQueue(_settings(), _completing_runner(), start_threads=False)
        result = queue.submit(
            owner_key="owner", question="mine", tenant_id="t", caller_roles=(), request={}
        )
        assert result.job is not None
        return queue, result.job.job_id

    def test_another_owner_cannot_read_the_job(self) -> None:
        queue, job_id = self._queue_with_job()
        assert queue.store.get(job_id, owner_key="owner") is not None
        assert queue.store.get(job_id, owner_key="someone-else") is None

    def test_a_nonexistent_job_and_someone_elses_are_indistinguishable(self) -> None:
        """Both are `None`, which the API turns into the same 404 -- so a
        caller cannot probe for the existence of another user's job id."""
        queue, job_id = self._queue_with_job()
        assert queue.store.get(job_id, owner_key="intruder") is None
        assert queue.store.get("q_does_not_exist", owner_key="intruder") is None

    def test_listing_never_includes_another_owners_jobs(self) -> None:
        queue = QuestionQueue(_settings(), _completing_runner(), start_threads=False)
        queue.submit(owner_key="a", question="a1", tenant_id="t", caller_roles=(), request={})
        queue.submit(owner_key="b", question="b1", tenant_id="t", caller_roles=(), request={})
        a_jobs = queue.store.list_for_owner("a", limit=50)
        assert [j.question for j in a_jobs] == ["a1"]
        assert all(j.owner_key == "a" for j in a_jobs)

    def test_another_owner_cannot_cancel_the_job(self) -> None:
        queue, job_id = self._queue_with_job()
        assert queue.cancel(job_id, owner_key="intruder") is None
        still_there = queue.store.get(job_id, owner_key="owner")
        assert still_there is not None and still_there.status is JobStatus.QUEUED

    def test_the_store_enforces_ownership_on_cancel_itself(self) -> None:
        """Not only the queue and the API above it.

        Both callers check ownership before calling `JobStore.cancel`,
        which is correct defence in depth -- but this proves the store
        refuses on its own, so a future caller that forgets cannot
        cancel another user's job.
        """
        queue = QuestionQueue(_settings(), _completing_runner(), start_threads=False)
        result = queue.submit(
            owner_key="owner", question="mine", tenant_id="t", caller_roles=(), request={}
        )
        assert result.job is not None
        job_id = result.job.job_id

        # Driven directly against the store, bypassing the queue's and the
        # API's own checks entirely.
        assert queue.store.cancel(job_id, owner_key="intruder") is None
        still_there = queue.store.get(job_id, owner_key="owner")
        assert still_there is not None and still_there.status is JobStatus.QUEUED

        # And the real owner still can, so the guard is not vacuous.
        assert queue.store.cancel(job_id, owner_key="owner") is not None

    def test_a_reader_snapshot_does_not_mutate_under_them(self) -> None:
        queue, job_id = self._queue_with_job()
        queue.store.promote_next()
        snapshot = queue.store.get(job_id, owner_key="owner")
        assert snapshot is not None
        assert snapshot.status is JobStatus.RUNNING
        queue.store.finish(
            job_id, JobStatus.COMPLETED, result={"answer": "x"}, sources_used=["sql"]
        )
        assert snapshot.status is JobStatus.RUNNING
        assert snapshot.sources_used == []


class TestCancellation:
    """Cancelling flips the status immediately and releases capacity."""

    def test_cancelling_a_queued_job_releases_queue_depth(self) -> None:
        queue = QuestionQueue(_settings(), _completing_runner(), start_threads=False)
        result = queue.submit(
            owner_key="u1", question="q", tenant_id="t", caller_roles=(), request={}
        )
        assert result.job is not None
        assert queue.stats(owner_key="u1").queued_for_caller == 1
        cancelled = queue.cancel(result.job.job_id, owner_key="u1")
        assert cancelled is not None and cancelled.status is JobStatus.CANCELLED
        assert queue.stats(owner_key="u1").queued_for_caller == 0

    def test_cancelling_an_already_finished_job_is_refused(self) -> None:
        queue = QuestionQueue(_settings(), _completing_runner(), start_threads=False)
        result = queue.submit(
            owner_key="u1", question="q", tenant_id="t", caller_roles=(), request={}
        )
        assert result.job is not None
        queue.store.promote_next()
        queue.store.finish(result.job.job_id, JobStatus.COMPLETED, result={})
        assert queue.cancel(result.job.job_id, owner_key="u1") is None

    def test_a_running_job_is_signalled_through_its_envelope(self) -> None:
        """The worker learns about the cancel from the envelope, which is
        what bounds the wasted work to roughly one node."""
        settings = _settings(max_concurrent_questions_per_user=1)
        started = threading.Event()
        saw_cancel = threading.Event()

        def runner(job, envelope):
            started.set()
            for _ in range(200):
                if envelope is not None and envelope.is_cancelled:
                    saw_cancel.set()
                    return JobOutcome(status=JobStatus.CANCELLED)
                time.sleep(0.01)
            return JobOutcome(status=JobStatus.COMPLETED, result={})

        queue = QuestionQueue(settings, runner)
        try:
            result = queue.submit(
                owner_key="u1", question="q", tenant_id="t", caller_roles=(), request={}
            )
            assert result.job is not None
            assert started.wait(timeout=10.0), "job never started"
            queue.cancel(result.job.job_id, owner_key="u1")
            assert saw_cancel.wait(timeout=10.0), "envelope cancellation never observed"
        finally:
            queue.shutdown(grace_seconds=2.0)


class TestTimeouts:
    """Both deadlines are enforced, and each reports TIMEOUT, not FAILED."""

    def test_a_job_queued_too_long_times_out(self) -> None:
        store = JobStore(
            max_concurrent_per_user=1,
            max_concurrent_global=1,
            max_queue_depth=10,
            max_queue_depth_per_user=10,
            max_retained_per_user=10,
            result_ttl_seconds=60,
            idempotency_ttl_seconds=60,
        )
        store.admit(
            owner_key="u1",
            question="q",
            tenant_id=None,
            caller_roles=(),
            idempotency_key="k",
            correlation_id=None,
            request={},
        )
        # A zero-second limit makes every queued job immediately overdue,
        # which tests the rule without a sleep.
        timed_out = store.expire_queue_waits(0.0)
        assert len(timed_out) == 1
        assert timed_out[0].status is JobStatus.TIMEOUT
        assert store.stats().queued_global == 0

    def test_a_running_job_past_its_budget_times_out(self) -> None:
        settings = _settings(question_timeout_seconds=1)
        release = threading.Event()

        def runner(job, envelope):
            release.wait(timeout=30.0)
            return JobOutcome(status=JobStatus.COMPLETED, result={})

        queue = QuestionQueue(settings, runner)
        try:
            result = queue.submit(
                owner_key="u1", question="q", tenant_id="t", caller_roles=(), request={}
            )
            assert result.job is not None
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                job = queue.store.get(result.job.job_id, owner_key="u1")
                assert job is not None
                if job.status is JobStatus.TIMEOUT:
                    break
                time.sleep(0.05)
            else:
                pytest.fail("job never timed out")
            job = queue.store.get(result.job.job_id, owner_key="u1")
            assert job is not None and job.status is JobStatus.TIMEOUT
            # TIMEOUT is its own status, distinguishable from FAILED.
            assert job.status is not JobStatus.FAILED
        finally:
            release.set()
            queue.shutdown(grace_seconds=2.0)


class TestGracefulShutdown:
    """In-flight work is signalled, queued work is cancelled, nothing hangs."""

    def test_shutdown_cancels_queued_jobs_and_signals_running_ones(self) -> None:
        settings = _settings(max_concurrent_questions_per_user=1)
        started = threading.Event()

        def runner(job, envelope):
            started.set()
            for _ in range(300):
                if envelope is not None and envelope.is_cancelled:
                    return JobOutcome(status=JobStatus.CANCELLED)
                time.sleep(0.01)
            return JobOutcome(status=JobStatus.COMPLETED, result={})

        queue = QuestionQueue(settings, runner)
        running = queue.submit(
            owner_key="u1", question="running", tenant_id="t", caller_roles=(), request={}
        )
        assert started.wait(timeout=10.0)
        queued = queue.submit(
            owner_key="u1", question="queued", tenant_id="t", caller_roles=(), request={}
        )
        assert running.job is not None and queued.job is not None

        queue.shutdown(grace_seconds=3.0)

        for job_id in (running.job.job_id, queued.job.job_id):
            job = queue.store.get(job_id, owner_key="u1")
            assert job is not None
            assert job.status is JobStatus.CANCELLED

    def test_shutdown_is_idempotent(self) -> None:
        queue = QuestionQueue(_settings(), _completing_runner())
        queue.shutdown(grace_seconds=1.0)
        queue.shutdown(grace_seconds=1.0)

    def test_submission_after_shutdown_is_refused(self) -> None:
        queue = QuestionQueue(_settings(), _completing_runner())
        queue.shutdown(grace_seconds=1.0)
        result = queue.submit(
            owner_key="u1", question="q", tenant_id="t", caller_roles=(), request={}
        )
        assert result.outcome is AdmissionOutcome.QUEUE_FULL


class TestWorkerRobustness:
    """A worker that explodes must not leak its concurrency slot."""

    def test_a_runner_that_raises_still_releases_the_slot(self) -> None:
        settings = _settings(max_concurrent_questions_per_user=1, question_max_retries=0)

        def runner(job, envelope):
            raise RuntimeError("boom")

        queue = QuestionQueue(settings, runner)
        try:
            result = queue.submit(
                owner_key="u1", question="q", tenant_id="t", caller_roles=(), request={}
            )
            assert result.job is not None
            _drain(queue)
            job = queue.store.get(result.job.job_id, owner_key="u1")
            assert job is not None and job.status is JobStatus.FAILED
            # The slot came back -- a later question can still run.
            assert queue.stats(owner_key="u1").running_for_caller == 0
        finally:
            queue.shutdown(grace_seconds=2.0)

    def test_an_internal_error_never_reaches_the_caller_verbatim(self) -> None:
        """The caller gets a fixed, safe message; the detail goes to the log."""
        settings = _settings(question_max_retries=0)

        def runner(job, envelope):
            raise RuntimeError("connection string postgresql://user:hunter2@host/db")

        queue = QuestionQueue(settings, runner)
        try:
            result = queue.submit(
                owner_key="u1", question="q", tenant_id="t", caller_roles=(), request={}
            )
            assert result.job is not None
            _drain(queue)
            job = queue.store.get(result.job.job_id, owner_key="u1")
            assert job is not None
            assert job.error_message is not None
            assert "hunter2" not in job.error_message
            assert "postgresql://" not in job.error_message
        finally:
            queue.shutdown(grace_seconds=2.0)

    def test_a_transient_failure_is_retried_with_backoff(self) -> None:
        settings = _settings(question_max_retries=2)
        attempts = []
        lock = threading.Lock()

        def runner(job, envelope):
            from agent.harness.failures import FailureClass

            with lock:
                attempts.append(time.monotonic())
                n = len(attempts)
            if n < 3:
                return JobOutcome(
                    status=JobStatus.FAILED,
                    error_message="transient",
                    failure=FailureClass.MODEL_UNAVAILABLE,
                )
            return JobOutcome(status=JobStatus.COMPLETED, result={"answer": "ok"})

        queue = QuestionQueue(settings, runner)
        try:
            result = queue.submit(
                owner_key="u1", question="q", tenant_id="t", caller_roles=(), request={}
            )
            assert result.job is not None
            _drain(queue)
            job = queue.store.get(result.job.job_id, owner_key="u1")
            assert job is not None and job.status is JobStatus.COMPLETED
            assert len(attempts) == 3
        finally:
            queue.shutdown(grace_seconds=2.0)

    def test_a_non_retryable_failure_is_not_retried(self) -> None:
        settings = _settings(question_max_retries=3)
        calls = []
        lock = threading.Lock()

        def runner(job, envelope):
            from agent.harness.failures import FailureClass

            with lock:
                calls.append(1)
            return JobOutcome(
                status=JobStatus.FAILED,
                error_message="not allowed",
                failure=FailureClass.AUTHORIZATION_DENIED,
            )

        queue = QuestionQueue(settings, runner)
        try:
            queue.submit(owner_key="u1", question="q", tenant_id="t", caller_roles=(), request={})
            _drain(queue)
            assert len(calls) == 1, "an authorization denial must never be retried"
        finally:
            queue.shutdown(grace_seconds=2.0)

    def test_a_guardrail_refusal_is_never_retried(self) -> None:
        """A refusal is a decision; repeating it only spends capacity."""
        settings = _settings(question_max_retries=3)
        calls = []
        lock = threading.Lock()

        def runner(job, envelope):
            with lock:
                calls.append(1)
            return JobOutcome(
                status=JobStatus.BLOCKED_BY_GUARDRAIL, guardrail_reason="input_guardrail"
            )

        queue = QuestionQueue(settings, runner)
        try:
            result = queue.submit(
                owner_key="u1", question="q", tenant_id="t", caller_roles=(), request={}
            )
            assert result.job is not None
            _drain(queue)
            assert len(calls) == 1
            job = queue.store.get(result.job.job_id, owner_key="u1")
            assert job is not None and job.status is JobStatus.BLOCKED_BY_GUARDRAIL
        finally:
            queue.shutdown(grace_seconds=2.0)


class TestRetentionBounds:
    """Memory is bounded by both a TTL and a hard per-user count."""

    def test_an_expired_terminal_job_is_evicted(self) -> None:
        store = JobStore(
            max_concurrent_per_user=1,
            max_concurrent_global=1,
            max_queue_depth=10,
            max_queue_depth_per_user=10,
            max_retained_per_user=100,
            result_ttl_seconds=0.0,
            idempotency_ttl_seconds=0.0,
        )
        result = store.admit(
            owner_key="u1",
            question="q",
            tenant_id=None,
            caller_roles=(),
            idempotency_key="k",
            correlation_id=None,
            request={},
        )
        assert result.job is not None
        store.promote_next()
        store.finish(result.job.job_id, JobStatus.COMPLETED, result={})
        assert store.evict_expired() == 1
        assert store.get(result.job.job_id, owner_key="u1") is None

    def test_a_live_job_is_never_evicted_by_age(self) -> None:
        """A running job is not stale however long it has taken, and
        dropping it would leak its counter and lose its owner's handle."""
        store = JobStore(
            max_concurrent_per_user=1,
            max_concurrent_global=1,
            max_queue_depth=10,
            max_queue_depth_per_user=10,
            max_retained_per_user=100,
            result_ttl_seconds=0.0,
            idempotency_ttl_seconds=0.0,
        )
        result = store.admit(
            owner_key="u1",
            question="q",
            tenant_id=None,
            caller_roles=(),
            idempotency_key="k",
            correlation_id=None,
            request={},
        )
        assert result.job is not None
        store.promote_next()
        assert store.evict_expired() == 0
        assert store.get(result.job.job_id, owner_key="u1") is not None

    def test_the_per_user_retention_cap_bounds_a_burst(self) -> None:
        store = JobStore(
            max_concurrent_per_user=1,
            max_concurrent_global=1,
            max_queue_depth=1000,
            max_queue_depth_per_user=1000,
            max_retained_per_user=5,
            result_ttl_seconds=3600,
            idempotency_ttl_seconds=3600,
        )
        for i in range(20):
            result = store.admit(
                owner_key="u1",
                question=f"q{i}",
                tenant_id=None,
                caller_roles=(),
                idempotency_key=f"k{i}",
                correlation_id=None,
                request={},
            )
            assert result.job is not None
            store.promote_next()
            store.finish(result.job.job_id, JobStatus.COMPLETED, result={})
        assert len(store.list_for_owner("u1", limit=100)) <= 5
