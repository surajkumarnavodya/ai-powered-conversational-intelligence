"""Circuit breakers for the harness, built on the one this repo already has.

`agent/multiagent/policy.py::CircuitBreaker` is a correct closed /
open / half-open implementation with a cooldown and a single-probe
recovery, already under test in `tests/test_multiagent_supervisor.py`.
It was written for the specialist supervisor, but nothing about it is
supervisor-specific.

**So this module imports it rather than writing a second one.** A
second breaker would be a second place for the half-open probe logic
to be subtly wrong, and `docs/HARNESS_MANAGEMENT_BASELINE.md` §4.2
named reuse here explicitly. What this module adds is the part that
was missing: a **provider-scoped** registry, and the rule about which
failures are allowed to trip a breaker at all.

### Why the primary LLM path needed one

The baseline's gap H5: `agent/llm_client.py` calls Ollama directly. With
Ollama down, every node that calls it waits its full
`ollama_request_timeout_seconds` (300 by default), on every request,
with no fast-fail. A handful of concurrent requests then occupy the
whole bounded `/ask` pool doing nothing but waiting. The breaker turns
that into an immediate, cheap failure after a few real ones.

### Only health signals trip it

`FailureClass.MODEL_MALFORMED_OUTPUT` is a failure, but it says nothing
about whether the provider is healthy -- counting it would let one
awkward question disable a model for every caller.
`agent.harness.failures.is_provider_health_signal` draws that line, and
`record_outcome` is the only way failures reach a breaker here, so the
line cannot be crossed by a call site forgetting it.
"""

from __future__ import annotations

import logging
import threading
import time

from agent.harness.failures import FailureClass, is_provider_health_signal
from agent.multiagent.policy import CircuitBreaker

logger = logging.getLogger(__name__)

_breakers: dict[str, CircuitBreaker] = {}
_lock = threading.Lock()


class ProviderUnavailable(RuntimeError):
    """A breaker is open: the call was refused without being attempted.

    Carries `provider` so a caller can report *which* dependency is
    unhealthy, and `safe_message` so the refusal can be shown without
    leaking a host, a model name or an internal error.
    """

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.safe_message = (
            "The language model service is temporarily unavailable. Please try again shortly."
        )
        super().__init__(f"Circuit open for provider {provider!r}.")


def get_provider_breaker(
    provider: str,
    *,
    failure_threshold: int = 5,
    cooldown_seconds: float = 30.0,
) -> CircuitBreaker:
    """The process-wide breaker for one provider, created on first use.

    Process-local, like every other limiter in this codebase: a
    multi-worker deployment has one independent breaker per worker
    (CLAUDE.md §13). Keyed by provider rather than by model, because a
    provider outage takes every model on it down together and five
    separate per-model breakers would each need their own five failures
    to notice the same event.

    The defaults are looser than the supervisor's (5 failures / 30s
    versus 3 / 60s): this sits on the primary user-facing path, where a
    breaker that opens too eagerly turns a transient blip into a visible
    outage, and recovers quickly because a local Ollama usually comes
    back fast.
    """
    with _lock:
        breaker = _breakers.get(provider)
        if breaker is None:
            breaker = CircuitBreaker(
                name=provider,
                failure_threshold=failure_threshold,
                cooldown_seconds=cooldown_seconds,
            )
            _breakers[provider] = breaker
        return breaker


def check_provider(provider: str) -> None:
    """Fast-fails when `provider`'s circuit is open.

    Consumes the half-open probe when the cooldown has elapsed, so the
    next real call is the probe. Call immediately before attempting the
    provider, never while merely planning to.

    Raises:
        ProviderUnavailable: the circuit refused this call.
    """
    breaker = get_provider_breaker(provider)
    if not breaker.allow(time.monotonic()):
        logger.warning("[harness] circuit open for provider=%s -- call refused", provider)
        raise ProviderUnavailable(provider)


def record_outcome(provider: str, failure: FailureClass | None) -> None:
    """Reports one call's result to the provider's breaker.

    `None` means success and closes the breaker. A failure advances it
    **only** when it is a provider-health signal; anything else is
    recorded nowhere, because it is not evidence about the provider.
    """
    breaker = get_provider_breaker(provider)
    if failure is None:
        breaker.record_success()
        return
    if is_provider_health_signal(failure):
        breaker.record_failure(time.monotonic())
        if breaker.state != "closed":
            logger.warning(
                "[harness] provider=%s breaker is now %s after %s",
                provider,
                breaker.state,
                failure.value,
            )


def breaker_states() -> dict[str, str]:
    """Every known provider's breaker state. For telemetry; no secrets."""
    with _lock:
        return {name: breaker.state for name, breaker in _breakers.items()}


def reset_breakers() -> None:
    """Clears every breaker. For tests and for an operator-triggered reset.

    Process-lifetime state like this is exactly what `tests/conftest.py`
    already clears between tests for the three other singletons; without
    a reset, one test opening a circuit would fail the next.
    """
    with _lock:
        _breakers.clear()


__all__ = [
    "CircuitBreaker",
    "ProviderUnavailable",
    "breaker_states",
    "check_provider",
    "get_provider_breaker",
    "record_outcome",
    "reset_breakers",
]
