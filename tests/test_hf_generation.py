"""The Hugging Face hosted image provider, with HF mocked.

The behaviour that matters here is **error classification**, not the happy
path. Hugging Face routes to third-party providers that bill per request,
so on a free account HTTP 402 is the normal steady state rather than an
exceptional one -- verified against a real, correctly-scoped token on
2026-10-23, where `FLUX.1-schnell` returned 402 in about half a second.
A provider that folded that into "generation failed" would send somebody
to debug a token that is demonstrably fine.

So each status code is pinned to its own message and its own `retryable`
verdict, and the token is pinned as never reaching anything user-visible.
"""

from __future__ import annotations

import io

import pytest

from config.settings import Settings
from media_gen.providers import ImageGenerationRequest
from media_gen.providers.huggingface import (
    QUOTA_EXHAUSTED_MESSAGE,
    RATE_LIMITED_MESSAGE,
    HuggingFaceImageProvider,
)
from security.secrets import SecretStr

TOKEN = "hf_tokenvaluethatmustneverleak"


def _settings(**overrides) -> Settings:
    base = {
        "enable_free_generation": True,
        "enable_hf_generation": True,
        "hf_token": SecretStr(TOKEN),
    }
    base.update(overrides)
    return Settings(**base)


class _FakeImage:
    """Stands in for the PIL image `text_to_image` returns."""

    size = (1024, 1024)

    def save(self, buffer: io.BytesIO, format: str) -> None:  # noqa: A002
        buffer.write(b"\x89PNG\r\n\x1a\n" + b"fake" * 8)


def _http_error(status: int, message: str = "error"):
    """A real `HfHubHTTPError`, not a stand-in.

    `huggingface_hub` 1.31's constructor takes a keyword-only
    `response: httpx.Response`, so a hand-rolled duck type does not work --
    building the genuine article is both simpler and means these tests
    exercise the same attribute access the provider will see in production.
    """
    import httpx
    from huggingface_hub.errors import HfHubHTTPError

    response = httpx.Response(
        status,
        text=message,
        request=httpx.Request("POST", "https://router.huggingface.co/test"),
    )
    return HfHubHTTPError(message, response=response)


def _patch_client(monkeypatch, *, image=None, error=None, captured: dict | None = None):
    class _Client:
        def __init__(self, token=None, timeout=None):
            if captured is not None:
                captured["token"] = token
                captured["timeout"] = timeout

        def text_to_image(self, prompt, model=None, **kwargs):
            if captured is not None:
                captured["prompt"] = prompt
                captured["model"] = model
                captured["kwargs"] = kwargs
            if error is not None:
                raise error
            return image or _FakeImage()

    monkeypatch.setattr("huggingface_hub.InferenceClient", _Client)


class TestSuccess:
    def test_a_generated_image_comes_back_as_png_bytes(self, monkeypatch):
        _patch_client(monkeypatch)

        result = HuggingFaceImageProvider(_settings()).generate_image(
            ImageGenerationRequest(prompt="a red bicycle")
        )

        assert result.ok is True
        assert result.origin == "bytes"
        assert result.media_type == "image/png"
        assert result.data.startswith(b"\x89PNG")

    def test_the_prompt_and_model_reach_the_client(self, monkeypatch):
        captured: dict = {}
        _patch_client(monkeypatch, captured=captured)

        HuggingFaceImageProvider(_settings(hf_image_model="some-org/some-model")).generate_image(
            ImageGenerationRequest(prompt="a kite")
        )

        assert captured["prompt"] == "a kite"
        assert captured["model"] == "some-org/some-model"

    def test_advisory_fields_are_forwarded_only_when_set(self, monkeypatch):
        """The hosted model's own defaults beat anything invented here, so
        an unset field must not become an explicit value."""
        captured: dict = {}
        _patch_client(monkeypatch, captured=captured)

        HuggingFaceImageProvider(_settings()).generate_image(
            ImageGenerationRequest(prompt="p", width=512, steps=4)
        )

        assert captured["kwargs"] == {"width": 512, "num_inference_steps": 4}
        assert "height" not in captured["kwargs"]
        assert "negative_prompt" not in captured["kwargs"]


class TestQuotaAndRateLimits:
    """The conditions a free account actually hits."""

    def test_402_is_reported_as_quota_not_as_a_generic_failure(self, monkeypatch):
        _patch_client(monkeypatch, error=_http_error(402, "Payment Required"))

        result = HuggingFaceImageProvider(_settings()).generate_image(
            ImageGenerationRequest(prompt="p")
        )

        assert result.ok is False
        assert result.error == QUOTA_EXHAUSTED_MESSAGE
        assert "token itself is fine" in result.error

    def test_402_is_marked_retryable_so_routing_can_try_elsewhere(self, monkeypatch):
        _patch_client(monkeypatch, error=_http_error(402))

        result = HuggingFaceImageProvider(_settings()).generate_image(
            ImageGenerationRequest(prompt="p")
        )

        assert "retryable" in result.warnings

    def test_429_is_reported_as_rate_limiting(self, monkeypatch):
        _patch_client(monkeypatch, error=_http_error(429, "Too Many Requests"))

        result = HuggingFaceImageProvider(_settings()).generate_image(
            ImageGenerationRequest(prompt="p")
        )

        assert result.error == RATE_LIMITED_MESSAGE
        assert "retryable" in result.warnings

    def test_a_400_about_providers_is_not_retryable(self, monkeypatch):
        """ "No provider enabled" is a configuration fact; retrying it on
        another backend cannot change it."""
        _patch_client(
            monkeypatch,
            error=_http_error(
                400, "The requested model is not supported by any provider you have enabled."
            ),
        )

        result = HuggingFaceImageProvider(_settings()).generate_image(
            ImageGenerationRequest(prompt="p")
        )

        assert "inference-providers" in result.error
        assert "retryable" not in result.warnings

    @pytest.mark.parametrize("status", [401, 403])
    def test_an_auth_failure_points_at_the_token_and_is_not_retryable(self, monkeypatch, status):
        _patch_client(monkeypatch, error=_http_error(status, "Unauthorized"))

        result = HuggingFaceImageProvider(_settings()).generate_image(
            ImageGenerationRequest(prompt="p")
        )

        assert "HF_TOKEN" in result.error
        assert "retryable" not in result.warnings

    def test_an_unexpected_exception_is_a_failed_result_not_a_raise(self, monkeypatch):
        _patch_client(monkeypatch, error=RuntimeError("socket exploded"))

        result = HuggingFaceImageProvider(_settings()).generate_image(
            ImageGenerationRequest(prompt="p")
        )

        assert result.ok is False
        assert "unreachable" in result.error
        assert "socket exploded" in result.detail


class TestTheTokenNeverLeaks:
    def test_it_is_passed_to_the_client_unwrapped(self, monkeypatch):
        """`.get_secret_value()` -- a bare `str(secret)` would send the
        literal '**********' and every call would fail mysteriously."""
        captured: dict = {}
        _patch_client(monkeypatch, captured=captured)

        HuggingFaceImageProvider(_settings()).generate_image(ImageGenerationRequest(prompt="p"))

        assert captured["token"] == TOKEN

    @pytest.mark.parametrize("status", [402, 429, 401, 400])
    def test_it_never_appears_in_a_failure_message(self, monkeypatch, status):
        _patch_client(monkeypatch, error=_http_error(status, f"failed for token {TOKEN}"))

        result = HuggingFaceImageProvider(_settings()).generate_image(
            ImageGenerationRequest(prompt="p")
        )

        assert TOKEN not in (result.error or "")

    def test_it_never_appears_in_health_output(self):
        health = HuggingFaceImageProvider(_settings()).health()

        assert TOKEN not in f"{health.reason} {health.detail}"


class TestHealth:
    def test_unavailable_without_a_token(self):
        health = HuggingFaceImageProvider(_settings(hf_token=None)).health()

        assert health.available is False
        assert "HF_TOKEN" in health.reason

    def test_unavailable_when_switched_off(self):
        health = HuggingFaceImageProvider(_settings(enable_hf_generation=False)).health()

        assert health.available is False
        assert "ENABLE_HF_GENERATION" in health.reason

    def test_available_with_a_token_and_honest_about_the_allowance(self):
        health = HuggingFaceImageProvider(_settings()).health()

        assert health.available is True
        assert "credit" in health.detail

    def test_health_does_not_spend_a_request_to_probe(self):
        """A liveness check would consume exactly the metered allowance
        this provider exists to conserve."""
        assert HuggingFaceImageProvider(_settings()).health().checked_remote is False

    def test_no_token_means_no_client_is_ever_built(self, monkeypatch):
        def _explode(*_a, **_k):
            raise AssertionError("a client must not be built without a token")

        monkeypatch.setattr("huggingface_hub.InferenceClient", _explode)

        result = HuggingFaceImageProvider(_settings(hf_token=None)).generate_image(
            ImageGenerationRequest(prompt="p")
        )

        assert result.ok is False
