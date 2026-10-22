"""What the app tells an operator when a model call fails.

This is a diagnostics test file, and the thing it pins is that **the
advice matches the cause**. The reported failure it exists for:

    Could not reach Ollama at http://localhost:11434 with model
    'qwen3.8:27b': timed out. Is `ollama serve` running and has the
    model been pulled (`ollama pull qwen3.8:27b`)?

Both suggestions were already true -- `ollama serve` was running and the
17 GB model was pulled. It had simply taken longer than the 300s per-call
limit on a 4 GB GPU, which the message never mentioned. One `except`
tuple catches a read timeout and a refused connection alike, and all
eight copies of the message asserted the second.

`str(exc)` from these reaches the user: `agent.nodes.generate_sql_node`
puts it in `failure_explanation` (redacted, but not reduced to
`.safe_message`), so an inaccurate one is read by a person and acted on.
"""

from __future__ import annotations

import httpx
import ollama
import pytest

from agent.exceptions import OllamaUnavailableError
from agent.llm_client import ollama_unavailable_error
from config.settings import Settings

HOST = "http://localhost:11434"
MODEL = "qwen3.8:27b"


def _settings(**overrides) -> Settings:
    base = {"ollama_host": HOST, "ollama_request_timeout_seconds": 300}
    base.update(overrides)
    return Settings(**base)


def _message(exc: Exception, **overrides) -> str:
    return str(ollama_unavailable_error(_settings(**overrides), MODEL, exc))


class TestATimeoutIsNotAnUnreachableServer:
    """The reported bug, in both of its exception shapes."""

    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ReadTimeout("timed out"),
            httpx.WriteTimeout("timed out"),
            httpx.PoolTimeout("timed out"),
            TimeoutError("timed out"),
        ],
    )
    def test_it_says_the_server_was_reached(self, exc: Exception) -> None:
        message = _message(exc)

        assert "was reached and the model is installed" in message
        assert "did not answer within the per-call limit of 300s" in message

    @pytest.mark.parametrize("exc", [httpx.ReadTimeout("timed out"), TimeoutError("timed out")])
    def test_it_never_tells_them_to_pull_or_restart(self, exc: Exception) -> None:
        """The two things that were already true. Suggesting either sends
        the reader to verify working infrastructure instead of changing
        the one thing that would help."""
        message = _message(exc)

        assert "ollama pull" not in message
        assert "ollama serve" not in message

    @pytest.mark.parametrize("exc", [httpx.ReadTimeout("timed out"), TimeoutError("timed out")])
    def test_it_names_the_two_levers_that_work(self, exc: Exception) -> None:
        message = _message(exc)

        assert "OLLAMA_REQUEST_TIMEOUT_SECONDS" in message
        assert "smaller model" in message

    def test_it_reports_the_configured_limit_not_a_fixed_number(self) -> None:
        assert "limit of 30s" in _message(httpx.ReadTimeout("x"), ollama_request_timeout_seconds=30)


class TestAConnectTimeoutIsStillUnreachable:
    """Ordering is the whole test.

    `httpx.ConnectTimeout` IS an `httpx.TimeoutException`, so a naive
    `isinstance(exc, httpx.TimeoutException)` check placed first would
    call a refused server "too slow" and send the reader to buy a smaller
    model for a server that was never listening.
    """

    def test_a_connect_timeout_blames_the_server_not_the_model_size(self) -> None:
        message = _message(httpx.ConnectTimeout("timed out"))

        assert "could not be reached" in message
        assert "ollama serve" in message
        assert "smaller model" not in message

    @pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), ConnectionError("refused")])
    def test_a_refused_connection_does_too(self, exc: Exception) -> None:
        message = _message(exc)

        assert "could not be reached" in message
        assert f"listening on {HOST}" in message


class TestAMissingModelIsTheOneCaseThatShouldSayPull:
    def test_a_404_says_pull_it(self) -> None:
        message = _message(ollama.ResponseError('model "x" not found', 404))

        assert "not installed on that server" in message
        assert f"ollama pull {MODEL}" in message

    def test_a_not_found_body_without_a_404_is_caught_too(self) -> None:
        """Ollama has not always set a status code on this error."""
        assert "not installed" in _message(
            ollama.ResponseError('model "x" not found, try pulling it first')
        )

    def test_another_response_error_asserts_no_cause(self) -> None:
        """A 500 is not a missing model, and guessing is what this
        function exists to stop."""
        message = _message(ollama.ResponseError("internal boom", 500))

        assert "internal boom" in message
        assert "ollama pull" not in message
        assert "ollama serve" not in message
        assert "smaller model" not in message


class TestEveryMessageIdentifiesTheCall:
    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ReadTimeout("x"),
            httpx.ConnectError("x"),
            ollama.ResponseError("model not found", 404),
            ollama.ResponseError("boom", 500),
            OSError("x"),
        ],
    )
    def test_the_model_and_host_are_always_named(self, exc: Exception) -> None:
        """Whichever branch ran. The model matters most: with a
        per-request `AskRequest.model`, it is not `OLLAMA_MODEL`, and
        `rag/llm.py` used to report the default regardless of what it
        called."""
        message = _message(exc)

        assert MODEL in message
        assert HOST in message

    def test_it_returns_the_error_rather_than_raising_it(self) -> None:
        """So the call site keeps its own `raise ... from exc` and the
        cause chain survives."""
        result = ollama_unavailable_error(_settings(), MODEL, httpx.ReadTimeout("x"))

        assert isinstance(result, OllamaUnavailableError)

    def test_the_safe_message_is_untouched(self) -> None:
        """Response bodies use `.safe_message` (CLAUDE.md 5.6); only the
        operator-facing detail changed here."""
        result = ollama_unavailable_error(_settings(), MODEL, httpx.ReadTimeout("x"))

        assert MODEL not in result.safe_message
        assert HOST not in result.safe_message
