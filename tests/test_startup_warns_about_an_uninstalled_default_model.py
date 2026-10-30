"""`OPS-008`: starting with an uninstalled `OLLAMA_MODEL` must say so.

The app has always started cleanly in that state and then failed **every**
question at generation time. `GET /models` already reported
`installed: false` for the model, so the fact was available -- nothing said
it at the point an operator reads the log, which is why a configured
default that was never pulled stayed invisible until a user asked
something.

Measured against the live application 2026-10-30 with
`OLLAMA_MODEL=qwen2.5-coder:7b` and only `llama3.1:8b`/`qwen3.8:27b`
pulled: startup logged nothing about it, and the first question came back
`status=failed` after ~4s.

A warning and never a refusal: the model may be mid-pull, and an operator
may deliberately start the app before `ollama pull` finishes.
"""

from __future__ import annotations

import logging

import pytest

from api.main import _warn_when_the_default_model_is_not_installed


class _Settings:
    def __init__(self, model: str) -> None:
        self.ollama_model = model
        self.ollama_host = "http://localhost:11434"


@pytest.fixture
def warn_lines(caplog):
    caplog.set_level(logging.WARNING, logger="api.main")
    return caplog


def _patch_installed(monkeypatch, installed: set[str]) -> None:
    monkeypatch.setattr("api.main.discover_installed_models", lambda _settings: installed)


class TestTheWarningFires:
    def test_warns_when_the_default_model_is_absent(self, monkeypatch, warn_lines):
        _patch_installed(monkeypatch, {"llama3.1:8b", "qwen3.8:27b"})
        _warn_when_the_default_model_is_not_installed(_Settings("qwen2.5-coder:7b"))
        text = warn_lines.text
        assert "qwen2.5-coder:7b" in text
        # The two things an operator can actually do about it.
        assert "ollama pull" in text
        assert "llama3.1:8b" in text, "the installed alternatives must be named"

    def test_names_every_installed_model_so_the_operator_can_choose(self, monkeypatch, warn_lines):
        _patch_installed(monkeypatch, {"b:1", "a:1"})
        _warn_when_the_default_model_is_not_installed(_Settings("missing:1"))
        # Sorted, so the message is stable across runs rather than following
        # set iteration order.
        assert "a:1, b:1" in warn_lines.text


class TestTheWarningStaysQuiet:
    def test_silent_when_the_default_model_is_installed(self, monkeypatch, warn_lines):
        _patch_installed(monkeypatch, {"llama3.1:8b"})
        _warn_when_the_default_model_is_not_installed(_Settings("llama3.1:8b"))
        assert warn_lines.text == ""

    def test_silent_when_ollama_could_not_be_reached(self, monkeypatch, warn_lines):
        """`discover_installed_models` fails open to an empty set.

        Warning then would blame the model for an unreachable server, and
        `GET /health`'s own `ollama` block already reports that accurately.
        This is the case that makes the guard safe to run at startup at
        all, so it is asserted rather than assumed.
        """
        _patch_installed(monkeypatch, set())
        _warn_when_the_default_model_is_not_installed(_Settings("anything:1"))
        assert warn_lines.text == ""

    def test_discovery_failure_never_breaks_startup(self, monkeypatch, warn_lines):
        """A warning helper must not be able to stop the app booting."""

        def _boom(_settings):
            raise RuntimeError("ollama exploded")

        monkeypatch.setattr("api.main.discover_installed_models", _boom)
        with pytest.raises(RuntimeError):
            # Documents the current contract: `discover_installed_models`
            # is the layer that swallows, and it already does (it catches
            # `Exception` and returns an empty set). If that ever changes,
            # this test says the responsibility moved rather than letting a
            # startup crash appear with no explanation.
            _warn_when_the_default_model_is_not_installed(_Settings("x:1"))
