"""Startup cross-checks: does the queue's cap fit the resources under it?

The brief requires that 100 concurrent questions not exhaust database
connections, memory, file handles, vector-store capacity, model quotas or
worker resources. Most of that is already bounded by controls that
predate this subsystem, each of which this module *checks against* rather
than duplicates:

  - **Database connections** -- `DB_POOL_SIZE + DB_MAX_OVERFLOW` per
    configured database, plus `agent.rate_limit
    .get_database_execution_limiter`, which turns a pool-exhaustion wait
    into an immediate, clearly-worded rejection.
  - **Model quota** -- `LLM_CALL_RATE_LIMIT_PER_MINUTE`, process-global
    and checked before every real Ollama call including retries.
  - **Worker threads and memory** -- the queue's own pool is bounded by
    `MAX_CONCURRENT_QUESTIONS_GLOBAL`, and each execution's result and
    context size by the harness's `max_result_bytes`/`max_context_chars`.

So the risk is not an unbounded resource; it is a *misconfigured* one --
a cap raised without raising the pool beneath it, which does not crash
but silently converts parallelism into queueing at a layer where nobody
is looking for it. These functions say so at startup.

**Warnings, never refusals.** Every finding here is a tuning signal, not
a safety boundary, and is reported exactly the way
`api.main._warn_on_ask_concurrency_pool_mismatch` already reports its
own: logged at WARNING and startup continues. The mismatches are all
correct-but-slower behaviour, not data loss -- and failing startup over a
throughput preference would make an operator's cautious configuration
unbootable. The one genuinely unsafe combination (a value above the
in-code hard maximum) is rejected much earlier, by `Settings` itself.

Plain functions taking `Settings`, mirroring
`api.main._enforce_database_write_privileges`'s own reasoning, so each is
unit-testable without starting the app.
"""

from __future__ import annotations

import logging

from config.settings import Settings

logger = logging.getLogger(__name__)


def check_database_pool_headroom(settings: Settings) -> str | None:
    """Warn when the queue could run more jobs than a database pool serves.

    Returns the warning text (also logged) or `None` when the sizing is
    consistent -- returned rather than only logged so a test can assert on
    the finding without capturing log output.

    The comparison is against *one* database's pool, because
    `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` apply uniformly to every
    `Settings.databases` entry (there is no per-database override) and a
    question is routed to one database at a time. A deployment whose
    questions spread across several databases has more total capacity
    than this check credits it with, which is the safe direction for a
    warning to err in.
    """
    pool_capacity = settings.db_pool_size + settings.db_max_overflow
    concurrent = settings.max_concurrent_questions_global
    if concurrent <= pool_capacity:
        return None
    message = (
        f"MAX_CONCURRENT_QUESTIONS_GLOBAL={concurrent} exceeds one database's connection "
        f"pool capacity (DB_POOL_SIZE={settings.db_pool_size} + "
        f"DB_MAX_OVERFLOW={settings.db_max_overflow} = {pool_capacity}). Jobs beyond "
        f"{pool_capacity} that need the database will wait for a pooled connection or be "
        f"rejected by the per-database concurrency limiter -- correct, bounded behaviour, "
        f"not an error. Raise DB_POOL_SIZE/DB_MAX_OVERFLOW (within your database server's "
        f"own max_connections, counting every replica of this process) or lower "
        f"MAX_CONCURRENT_QUESTIONS_GLOBAL if this was not intentional."
    )
    logger.warning("[startup] %s", message)
    return message


def check_llm_rate_headroom(settings: Settings) -> str | None:
    """Warn when the per-user cap alone could outrun the LLM call budget.

    A single question costs several LLM calls (roughly 6-8 on a clean run
    through the 18-node graph, more with retries), and
    `LLM_CALL_RATE_LIMIT_PER_MINUTE` is process-global. One user at their
    full cap can therefore exhaust the minute's budget on its own, after
    which every further call -- including other users' -- is refused until
    the window slides. That is the limiter working as designed, but it is
    worth knowing before it presents as "the app got slow".

    The 6 here is a deliberately conservative lower bound on calls per
    question, not a measurement: it is used only to decide whether to
    warn, and a lower estimate means fewer, better-founded warnings.
    """
    calls_per_question = 6
    demand = settings.max_concurrent_questions_per_user * calls_per_question
    budget = settings.llm_call_rate_limit_per_minute
    if demand <= budget:
        return None
    message = (
        f"MAX_CONCURRENT_QUESTIONS_PER_USER={settings.max_concurrent_questions_per_user} "
        f"implies at least ~{demand} model calls for one user's full burst "
        f"(~{calls_per_question} per question), above the process-global "
        f"LLM_CALL_RATE_LIMIT_PER_MINUTE={budget}. Calls past the budget are refused "
        f"until the rate window slides, which affects every caller, not just the one who "
        f"filled it. Raise LLM_CALL_RATE_LIMIT_PER_MINUTE to match your Ollama host's real "
        f"throughput, or lower the per-user cap."
    )
    logger.warning("[startup] %s", message)
    return message


def check_worker_thread_sizing(settings: Settings) -> str | None:
    """Warn when the global cap implies an unreasonable number of threads.

    The queue runs one OS thread per RUNNING job, each with its own stack
    and, while executing SQL, its own pooled database connection. That is
    a deliberate design choice -- the alternative, a watchdog thread per
    job on top of the worker, doubles it (see `jobs/queue.py`) -- but it
    means the global cap *is* a thread count, and a few hundred threads
    in one CPython process is a real cost rather than a free abstraction.

    The 256 threshold is a judgement call about where that cost starts
    being worth a sentence at startup, not a hard limit; the hard limit is
    `config.settings.MAX_SAFE_CONCURRENT_QUESTIONS_GLOBAL`, enforced by
    `Settings`.
    """
    threshold = 256
    concurrent = settings.max_concurrent_questions_global
    if concurrent <= threshold:
        return None
    message = (
        f"MAX_CONCURRENT_QUESTIONS_GLOBAL={concurrent} means up to {concurrent} worker "
        f"threads in this process, each holding a stack and, while executing, a pooled "
        f"database connection. Above roughly {threshold} the thread overhead itself starts "
        f"to dominate; prefer running more replicas of this process over raising this "
        f"further. Note each replica keeps its own queue and its own per-user counters "
        f"(see docs/CONCURRENCY.md)."
    )
    logger.warning("[startup] %s", message)
    return message


def run_startup_checks(settings: Settings) -> list[str]:
    """Run every sizing check. Returns the findings, in order, for tests.

    Called from `api.main`'s lifespan startup alongside the other
    plain-function startup checks. Never raises: a sizing finding is
    advisory, and startup failing over one would be worse than the
    mis-sizing it reports.
    """
    if not settings.enable_question_queue:
        return []
    findings = [
        check_database_pool_headroom(settings),
        check_llm_rate_headroom(settings),
        check_worker_thread_sizing(settings),
    ]
    return [f for f in findings if f is not None]


__all__ = [
    "check_database_pool_headroom",
    "check_llm_rate_headroom",
    "check_worker_thread_sizing",
    "run_startup_checks",
]
