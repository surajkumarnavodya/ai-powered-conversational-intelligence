"""Startup sizing cross-checks for the question queue (`jobs/sizing.py`).

These are warnings, never refusals, so the tests assert on the returned
findings rather than on an exception -- which is also why the functions
return their message instead of only logging it.

The reason this matters: the brief requires that 100 concurrent questions
not exhaust database connections, memory, file handles or model quotas.
Each of those is already bounded by a control that predates this
subsystem, so the real risk is a *misconfigured* bound -- a cap raised
without raising the pool beneath it, which does not crash but silently
converts parallelism into queueing where nobody is looking for it.
"""

from __future__ import annotations

from config.settings import Settings
from jobs.sizing import (
    check_database_pool_headroom,
    check_llm_rate_headroom,
    check_worker_thread_sizing,
    run_startup_checks,
)


def _settings(**overrides) -> Settings:
    return Settings(**overrides)


class TestDatabasePoolHeadroom:
    def test_a_consistent_configuration_reports_nothing(self) -> None:
        settings = _settings(
            max_concurrent_questions_per_user=10,
            max_concurrent_questions_global=20,
            db_pool_size=10,
            db_max_overflow=20,
        )
        assert check_database_pool_headroom(settings) is None

    def test_a_cap_above_the_pool_is_reported(self) -> None:
        settings = _settings(
            max_concurrent_questions_per_user=100,
            max_concurrent_questions_global=200,
            db_pool_size=5,
            db_max_overflow=5,
        )
        finding = check_database_pool_headroom(settings)
        assert finding is not None
        assert "DB_POOL_SIZE" in finding
        assert "MAX_CONCURRENT_QUESTIONS_GLOBAL" in finding

    def test_the_finding_says_this_is_bounded_behaviour_not_an_error(self) -> None:
        """An operator must not read a tuning signal as a fault."""
        settings = _settings(max_concurrent_questions_global=200, db_pool_size=5, db_max_overflow=5)
        finding = check_database_pool_headroom(settings)
        assert finding is not None
        assert "not an error" in finding


class TestLlmRateHeadroom:
    def test_a_generous_rate_limit_reports_nothing(self) -> None:
        settings = _settings(
            max_concurrent_questions_per_user=10,
            max_concurrent_questions_global=20,
            llm_call_rate_limit_per_minute=1000,
        )
        assert check_llm_rate_headroom(settings) is None

    def test_a_per_user_cap_outrunning_the_llm_budget_is_reported(self) -> None:
        settings = _settings(
            max_concurrent_questions_per_user=100,
            max_concurrent_questions_global=200,
            llm_call_rate_limit_per_minute=20,
        )
        finding = check_llm_rate_headroom(settings)
        assert finding is not None
        assert "LLM_CALL_RATE_LIMIT_PER_MINUTE" in finding

    def test_the_finding_explains_that_it_affects_every_caller(self) -> None:
        """The limiter is process-global, so one user filling it slows
        everyone -- which is the non-obvious part worth stating."""
        settings = _settings(
            max_concurrent_questions_per_user=100,
            max_concurrent_questions_global=200,
            llm_call_rate_limit_per_minute=20,
        )
        finding = check_llm_rate_headroom(settings)
        assert finding is not None
        assert "every caller" in finding


class TestWorkerThreadSizing:
    def test_a_modest_global_cap_reports_nothing(self) -> None:
        assert check_worker_thread_sizing(_settings(max_concurrent_questions_global=100)) is None

    def test_a_large_global_cap_is_reported(self) -> None:
        finding = check_worker_thread_sizing(_settings(max_concurrent_questions_global=500))
        assert finding is not None
        assert "worker threads" in finding

    def test_the_finding_points_at_replicas_rather_than_a_bigger_number(self) -> None:
        finding = check_worker_thread_sizing(_settings(max_concurrent_questions_global=500))
        assert finding is not None
        assert "replicas" in finding


class TestRunStartupChecks:
    def test_a_well_sized_deployment_produces_no_findings(self) -> None:
        settings = _settings(
            max_concurrent_questions_per_user=10,
            max_concurrent_questions_global=20,
            db_pool_size=20,
            db_max_overflow=20,
            llm_call_rate_limit_per_minute=600,
        )
        assert run_startup_checks(settings) == []

    def test_a_badly_sized_deployment_produces_every_relevant_finding(self) -> None:
        settings = _settings(
            max_concurrent_questions_per_user=400,
            max_concurrent_questions_global=400,
            db_pool_size=5,
            db_max_overflow=5,
            llm_call_rate_limit_per_minute=20,
        )
        findings = run_startup_checks(settings)
        assert len(findings) == 3

    def test_nothing_is_checked_when_the_queue_is_disabled(self) -> None:
        settings = _settings(
            enable_question_queue=False,
            max_concurrent_questions_global=400,
            db_pool_size=1,
            db_max_overflow=0,
        )
        assert run_startup_checks(settings) == []

    def test_the_checks_never_raise(self) -> None:
        """Startup must not fail over a throughput preference.

        The one genuinely unsafe combination -- a value above the in-code
        hard maximum -- is refused far earlier, by `Settings` itself.
        """
        settings = _settings(
            max_concurrent_questions_per_user=500,
            max_concurrent_questions_global=500,
            db_pool_size=1,
            db_max_overflow=0,
            llm_call_rate_limit_per_minute=1,
        )
        assert isinstance(run_startup_checks(settings), list)

    def test_the_wrapper_in_api_main_delegates_without_raising(self) -> None:
        """`api.main.run_question_queue_startup_checks` is what lifespan
        calls; it must behave identically and never raise."""
        from api.main import run_question_queue_startup_checks

        settings = _settings(
            max_concurrent_questions_per_user=400,
            max_concurrent_questions_global=400,
            db_pool_size=5,
            db_max_overflow=5,
            llm_call_rate_limit_per_minute=20,
        )
        assert run_question_queue_startup_checks(settings) == run_startup_checks(settings)


class TestShutdownHook:
    """`_shutdown_question_queue` must be safe when no queue was built."""

    def test_shutdown_is_a_no_op_when_no_queue_exists(self) -> None:
        """It passes no runner, so it never *creates* a queue to tear down."""
        from api.main import _shutdown_question_queue

        _shutdown_question_queue(_settings())

    def test_shutdown_is_a_no_op_when_the_queue_is_disabled(self) -> None:
        from api.main import _shutdown_question_queue

        _shutdown_question_queue(_settings(enable_question_queue=False))
