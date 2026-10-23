"""Bounded retry: classification first, backoff second, budget always.

**The retry decision is made from the failure class, never from the
exception type at the call site.** `agent.harness.failures.is_retryable`
owns that judgement. This module only answers "given that this class is
retryable, may *this execution* afford another attempt, and how long
should it wait first".

Four things bound a retry here, and all four must pass:

1. the failure class is retryable at all,
2. attempts so far are under `max_attempts`,
3. the execution's own retry budget is not spent,
4. the next attempt's backoff still fits inside the deadline.

(4) is the one most retry helpers miss. Sleeping 8 seconds before a
retry that cannot finish before the request deadline is strictly worse
than failing immediately: it burns a worker, delays the error the
caller is already waiting for, and the retry was never going to land.

**Jitter is full jitter, not fixed.** `random.uniform(0, backoff)`
rather than `backoff ± 10%`. Equal-width jitter still leaves a
synchronised fleet retrying in the same narrow window after a provider
blip; full jitter spreads it across the whole interval, which is what
actually breaks a thundering herd.

`random` here is for load spreading, never for anything security-
bearing, so the stdlib generator is the right tool.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from agent.harness.envelope import UNLIMITED, ExecutionEnvelope
from agent.harness.failures import FailureClass, is_retryable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetryPolicy:
    """How many times, how long between, and when to stop trying.

    Attributes:
        max_attempts: Total attempts including the first. `1` disables
            retrying without needing a separate flag.
        initial_backoff_seconds: Wait before the second attempt.
        backoff_multiplier: Growth factor per attempt.
        max_backoff_seconds: Ceiling on one wait, so exponential growth
            cannot produce a minutes-long sleep inside a request.
        total_retry_deadline_seconds: Ceiling on all waiting combined.
            Separate from the per-wait cap because many short waits add
            up to the same problem one long wait causes.
        jitter: Whether to spread retries across the interval.
    """

    max_attempts: int = 3
    initial_backoff_seconds: float = 0.5
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 8.0
    total_retry_deadline_seconds: float = 30.0
    jitter: bool = True

    def backoff_for(self, attempt: int) -> float:
        """Seconds to wait before `attempt` (1-based; attempt 1 never waits)."""
        if attempt <= 1:
            return 0.0
        raw = self.initial_backoff_seconds * (self.backoff_multiplier ** (attempt - 2))
        capped = min(raw, self.max_backoff_seconds)
        if not self.jitter:
            return capped
        # nosec B311 - retry jitter spreads load; nothing here is a secret or a token.
        return random.uniform(
            0.0, capped
        )  # noqa: S311 - load spreading, not security  # nosec B311


#: The default for model and tool calls. Conservative on purpose: three
#: attempts and at most 30s of total waiting, inside a request whose own
#: default ceiling is 600s.
DEFAULT_RETRY_POLICY = RetryPolicy()

#: For a database call. Fewer attempts and a tighter total: a database
#: that is timing out is usually under load, and piling retries onto a
#: loaded database is how a slowdown becomes an outage.
DATABASE_RETRY_POLICY = RetryPolicy(
    max_attempts=2,
    initial_backoff_seconds=0.25,
    max_backoff_seconds=2.0,
    total_retry_deadline_seconds=5.0,
)


@dataclass(frozen=True)
class RetryDecision:
    """Whether to retry, how long to wait first, and why."""

    should_retry: bool
    backoff_seconds: float = 0.0
    reason: str = ""


def decide_retry(
    *,
    failure: FailureClass,
    attempt: int,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    envelope: ExecutionEnvelope | None = None,
    elapsed_retry_seconds: float = 0.0,
) -> RetryDecision:
    """Whether attempt `attempt` may be followed by another one.

    Args:
        failure: The classified failure that just happened.
        attempt: Which attempt just failed, 1-based.
        policy: The policy in force for this kind of call.
        envelope: The execution, when one exists. Supplies the retry
            budget and the deadline; `None` means only `policy` applies.
        elapsed_retry_seconds: Time already spent waiting between
            attempts of this operation.

    Returns:
        A `RetryDecision`. `reason` names the first rule that said no,
        which is what makes a retry-storm investigation tractable.
    """
    if not is_retryable(failure):
        return RetryDecision(False, reason=f"non_retryable:{failure.value}")

    if attempt >= policy.max_attempts:
        return RetryDecision(False, reason="max_attempts_reached")

    if elapsed_retry_seconds >= policy.total_retry_deadline_seconds:
        return RetryDecision(False, reason="total_retry_deadline_reached")

    backoff = policy.backoff_for(attempt + 1)

    if envelope is not None:
        if envelope.is_cancelled:
            return RetryDecision(False, reason="cancelled")
        if envelope.retry_budget_exhausted:
            return RetryDecision(False, reason="execution_retry_budget_exhausted")
        remaining = envelope.remaining_seconds()
        if remaining != UNLIMITED and remaining <= backoff:
            # Sleeping past the deadline to attempt something that
            # cannot finish is worse than failing now.
            return RetryDecision(False, reason="backoff_exceeds_deadline")

    return RetryDecision(True, backoff_seconds=backoff, reason="retrying")


def never_retry(failure: FailureClass) -> bool:
    """True for failures a retry must never touch, whatever the policy says.

    A convenience for a call site that wants to assert the hard rule
    locally. The real enforcement is `decide_retry`'s first check;
    this exists so a reader at the call site can see the rule without
    following the call.
    """
    return failure in {
        FailureClass.AUTHORIZATION_DENIED,
        FailureClass.VALIDATION_REJECTED,
        FailureClass.SQL_DETERMINISTIC,
        FailureClass.CANCELLED,
        FailureClass.BUDGET_EXHAUSTED,
        FailureClass.DEADLINE_EXCEEDED,
    }


__all__ = [
    "DATABASE_RETRY_POLICY",
    "DEFAULT_RETRY_POLICY",
    "RetryDecision",
    "RetryPolicy",
    "decide_retry",
    "never_retry",
]
