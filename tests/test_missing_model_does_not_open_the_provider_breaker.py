"""A model that is not installed must not disable the models that are.

## The defect these tests were written against (`OPS-009`)

`agent/harness/breakers.py` keys its circuit breaker by **provider**, not
by model, and its docstring gives the right reason: "a provider outage
takes every model on it down together and five separate per-model
breakers would each need their own five failures to notice the same
event."

That reasoning holds for an outage. It does not hold for a 404. When
Ollama answers `model "x" not found`, the provider is demonstrably
healthy -- it was reached, it parsed the request, and it answered. Only
*that model* is missing, which is an operator configuration error.

`agent/llm_client.py` could not tell the two apart: all seven call sites
caught every exception from `client.chat` and reported
`FailureClass.MODEL_UNAVAILABLE`, which
`agent.harness.failures.is_provider_health_signal` counts, so five
missing-model 404s opened the provider breaker. After that,
`check_provider` refused calls for **every** model on Ollama, including
installed ones -- so the model picker's own working fallback was blocked
by the broken default.

Measured against the live application 2026-10-30, with
`.env`'s `OLLAMA_MODEL=qwen2.5-coder:7b` not installed:

  * a question on the default model failed in ~4s with the accurate
    "the model is not installed on that server. Pull it with
    `ollama pull ...`" explanation;
  * after a few of those, a question explicitly pinned to the
    **installed** `llama3.1:8b` failed in **1.4s** with the generic
    "The AI model service is temporarily unavailable. Please try again
    in a moment." -- the breaker's refusal, not a real call;
  * once the 30s cooldown elapsed, the identical question succeeded in
    108.8s with 5 correct rows.

The breaker is also what the user's own screenshot showed: a question
answered in 5.7s with "The AI model service is temporarily unavailable",
when Ollama was up the whole time.

## Why the timeout and connection cases must keep tripping it

That is the gap the breaker was added for: with Ollama down, every node
waits its full `ollama_request_timeout_seconds` (300 by default), so a
handful of concurrent requests occupy the whole bounded `/ask` pool doing
nothing. A 404 costs nothing to retry -- it is immediate -- so there is no
timeout to protect against, which is precisely why it is the one case
that should not count.
"""

from __future__ import annotations

import httpx
import ollama
import pytest

from agent.harness.breakers import (
    ProviderUnavailable,
    check_provider,
    get_provider_breaker,
    record_outcome,
)
from agent.harness.failures import FailureClass
from agent.llm_client import is_provider_health_failure


@pytest.fixture(autouse=True)
def _fresh_breaker():
    """A closed breaker per test -- it is a process-wide singleton."""
    breaker = get_provider_breaker("ollama")
    breaker.record_success()
    yield
    breaker.record_success()


def _not_found(model: str = "qwen2.5-coder:7b") -> ollama.ResponseError:
    """The error Ollama raises for a model it does not have."""
    error = ollama.ResponseError(f'model "{model}" not found, try pulling it first')
    error.status_code = 404
    return error


class TestWhichFailuresAreProviderHealthSignals:
    def test_a_missing_model_is_not_a_provider_health_signal(self):
        """The 404 case: the provider answered, so it is healthy."""
        assert is_provider_health_failure(_not_found()) is False

    def test_a_404_without_a_status_code_is_still_recognised(self):
        """`_ollama_error` matches on the message too, so this must agree.

        Some `ollama` versions raise without populating `status_code`;
        if the two disagreed, the user-facing message would say "not
        installed" while the breaker counted an outage.
        """
        error = ollama.ResponseError('model "x" not found, try pulling it first')
        assert is_provider_health_failure(error) is False

    def test_an_unreachable_server_is_a_provider_health_signal(self):
        assert is_provider_health_failure(httpx.ConnectError("refused")) is True
        assert is_provider_health_failure(ConnectionError("refused")) is True

    def test_a_timeout_is_a_provider_health_signal(self):
        assert is_provider_health_failure(httpx.ReadTimeout("slow")) is True
        assert is_provider_health_failure(TimeoutError("slow")) is True

    def test_a_server_error_is_a_provider_health_signal(self):
        error = ollama.ResponseError("internal server error")
        error.status_code = 500
        assert is_provider_health_failure(error) is True


class TestTheBreakerStaysClosedForAMissingModel:
    def test_many_missing_model_failures_never_open_the_breaker(self):
        """The regression itself, at the breaker.

        The default threshold is 5; twenty is well past it.
        """
        for _ in range(20):
            if is_provider_health_failure(_not_found()):
                record_outcome("ollama", FailureClass.MODEL_UNAVAILABLE)
        # Must not raise: an installed model is still callable.
        check_provider("ollama")

    def test_an_installed_model_is_still_callable_after_a_broken_default(self):
        """The user-visible shape of it: the picker's fallback must work.

        An operator whose `OLLAMA_MODEL` names an uninstalled model must
        still be able to pick an installed one, which is the documented
        workaround for `OPS-008`.
        """
        for _ in range(10):
            if is_provider_health_failure(_not_found("qwen2.5-coder:7b")):
                record_outcome("ollama", FailureClass.MODEL_UNAVAILABLE)
        try:
            check_provider("ollama")
        except ProviderUnavailable:  # pragma: no cover - the defect
            pytest.fail(
                "a missing model opened the provider breaker, so the installed "
                "model the picker offers as a fallback is refused too"
            )

    def test_a_real_outage_still_opens_the_breaker(self):
        """The guard must not be vacuous in the other direction.

        If this ever stops failing fast, the gap the breaker exists to
        close (every node waiting its full timeout during an outage) is
        back.
        """
        for _ in range(5):
            if is_provider_health_failure(httpx.ConnectError("refused")):
                record_outcome("ollama", FailureClass.MODEL_UNAVAILABLE)
        with pytest.raises(ProviderUnavailable):
            check_provider("ollama")

    def test_a_timeout_outage_still_opens_the_breaker(self):
        for _ in range(5):
            if is_provider_health_failure(httpx.ReadTimeout("slow")):
                record_outcome("ollama", FailureClass.MODEL_UNAVAILABLE)
        with pytest.raises(ProviderUnavailable):
            check_provider("ollama")
