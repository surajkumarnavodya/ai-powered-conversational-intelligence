"""Hosted free-tier image generation via Hugging Face Inference Providers.

The only route to a *raster* image that does not need a local GPU, which
on modest hardware (a 4 GB card cannot hold FLUX, let alone a video model)
makes it the only raster option at all.

**Read this before expecting it to work.** Hugging Face no longer serves
inference itself: `InferenceClient` routes to third-party providers
(nscale, fal-ai, together, replicate and others), each of which must be
enabled on the account and each of which bills. A free account receives a
small monthly *included credit*; once that is gone -- or if no
credit-bearing provider is enabled -- every request returns **HTTP 402
Payment Required**, immediately, before any generation happens.

Measured against a real, correctly-scoped token on 2026-10-23:
`inference.serverless.write` granted, `whoami` fine, and both
`black-forest-labs/FLUX.1-schnell` and
`stabilityai/stable-diffusion-xl-base-1.0` returned 402 in about half a
second. So a 402 here is the *expected* state of a free account with no
credit, not a bug and not a misconfiguration -- which is precisely why it
is reported as a distinct, actionable condition rather than folded into a
generic failure.

`huggingface_hub` is reused rather than hand-rolling HTTP: it is already
installed (transitively, via `sentence-transformers`/`transformers`), it
owns the provider-routing logic that would otherwise have to be
reimplemented here, and it raises a typed `HfHubHTTPError` carrying the
status code this module branches on.
"""

from __future__ import annotations

import io
import logging
from typing import Any

from config.settings import Settings
from media_gen.providers import (
    GeneratedMedia,
    ImageGenerationRequest,
    ProviderHealth,
    ProviderName,
)

logger = logging.getLogger(__name__)

PROVIDER_NAME: ProviderName = "huggingface"

# nosec B105 - a false positive: this is the user-facing *message* shown when no
# token is set, matched only because the constant's name ends in `_TOKEN`. The
# real token is a `SecretStr` read from settings, never a literal.
_REASON_NO_TOKEN = "No Hugging Face token is configured (HF_TOKEN)."  # nosec B105
_REASON_DISABLED = "The Hugging Face provider is switched off (ENABLE_HF_GENERATION)."

#: Shown verbatim to the user on a 402. Deliberately specific: "generation
#: failed" would send somebody to debug their token, which is the one thing
#: that is demonstrably fine when this fires.
QUOTA_EXHAUSTED_MESSAGE = (
    "The free Hugging Face image allowance is used up. Hugging Face routes image "
    "generation to third-party providers that bill per request, so this needs "
    "inference credit on the account -- check Settings > Inference Providers and "
    "Settings > Billing at huggingface.co. The token itself is fine."
)
RATE_LIMITED_MESSAGE = (
    "Hugging Face is rate-limiting this token right now. Please try again shortly."
)
_NO_PROVIDER_MESSAGE = (
    "No Hugging Face inference provider is enabled for this model on your account. "
    "Enable one at huggingface.co/settings/inference-providers."
)


def _classify_http_error(status: int | None, detail: str) -> tuple[str, bool]:
    """`(user-facing message, retryable)` for one HF HTTP failure.

    `retryable` governs whether the router should try the *next* provider.
    A 402 is retryable elsewhere (another backend may be free) but not
    worth retrying here; a 400 "no provider enabled" is a configuration
    fact that will not change on retry.
    """
    if status == 402:
        return QUOTA_EXHAUSTED_MESSAGE, True
    if status == 429:
        return RATE_LIMITED_MESSAGE, True
    if status == 400 and "not supported by any provider" in detail.lower():
        return _NO_PROVIDER_MESSAGE, False
    if status in (401, 403):
        return (
            "Hugging Face rejected the configured token. Check that HF_TOKEN is valid "
            "and carries the 'Make calls to Inference Providers' permission.",
            False,
        )
    return (
        "The hosted image provider could not complete this request. Please try again.",
        True,
    )


class HuggingFaceImageProvider:
    """`ImageProvider` over Hugging Face Inference Providers."""

    name: ProviderName = PROVIDER_NAME

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def health(self) -> ProviderHealth:
        """Config-only. Deliberately does not spend a request to probe.

        A liveness check here would consume exactly the metered allowance
        this provider exists to conserve -- and on a free account it would
        report 402 forever while telling the operator nothing their billing
        page does not. `checked_remote=False` says so plainly.
        """
        if not self._settings.enable_hf_generation:
            return ProviderHealth(name=self.name, available=False, reason=_REASON_DISABLED)
        if self._settings.hf_token is None:
            return ProviderHealth(name=self.name, available=False, reason=_REASON_NO_TOKEN)
        return ProviderHealth(
            name=self.name,
            available=True,
            detail=(
                f"Hosted, model {self._settings.hf_image_model}. Routed by Hugging Face to a "
                "third-party provider that bills per request; a free account's included "
                "credit is small, and exhaustion surfaces as a quota error on first use. "
                "Not probed, to avoid spending the allowance."
            ),
            checked_remote=False,
        )

    def generate_image(self, request: ImageGenerationRequest) -> GeneratedMedia:
        """Generates one image, or reports why not.

        Never raises: every failure becomes a failed `GeneratedMedia`, the
        same contract every other provider in this package follows.
        """
        from huggingface_hub import InferenceClient
        from huggingface_hub.errors import HfHubHTTPError

        if not self._settings.enable_hf_generation:
            return GeneratedMedia(
                status="failed", provider=self.name, error=_REASON_DISABLED, detail=_REASON_DISABLED
            )
        if self._settings.hf_token is None:
            return GeneratedMedia(
                status="failed", provider=self.name, error=_REASON_NO_TOKEN, detail=_REASON_NO_TOKEN
            )

        model = self._settings.hf_image_model
        # `.get_secret_value()` -- a bare `str(secret)` would send the
        # literal "**********" (CLAUDE.md 5.6).
        client = InferenceClient(
            token=self._settings.hf_token.get_secret_value(),
            timeout=self._settings.hf_request_timeout_seconds,
        )

        # Advisory fields are forwarded only when the caller set them; the
        # hosted model's own defaults beat anything invented here. Built as
        # an explicit `dict[str, Any]` rather than splatting a comprehension
        # inline: the values are genuinely heterogeneous (int, str), and
        # mypy cannot check a `**dict[str, int | str]` splat against
        # `text_to_image`'s individually-typed parameters. `Any` is the
        # honest annotation for a kwargs bag, not a silenced error.
        #
        # Filtering here rather than relying on huggingface_hub dropping
        # `None` itself -- it does, but that is an internal detail of its
        # provider helpers, not a documented contract.
        extra: dict[str, Any] = {
            key: value
            for key, value in (
                ("width", request.width),
                ("height", request.height),
                ("num_inference_steps", request.steps),
                ("negative_prompt", request.negative_prompt),
            )
            if value is not None
        }

        try:
            image = client.text_to_image(request.prompt, model=model, **extra)
        except HfHubHTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            message, retryable = _classify_http_error(status, str(exc))
            # Logged by status, never with the token or the full payload.
            logger.warning("[hf] image generation failed with HTTP %s", status)
            return GeneratedMedia(
                status="failed",
                provider=self.name,
                model=model,
                error=message,
                detail=f"HTTP {status}: {str(exc)[:400]}",
                warnings=("retryable",) if retryable else (),
            )
        except Exception as exc:  # noqa: BLE001 - a provider reports failure, never raises out
            logger.warning("[hf] image generation raised %s", type(exc).__name__)
            return GeneratedMedia(
                status="failed",
                provider=self.name,
                model=model,
                error="The hosted image provider is unreachable. Please try again.",
                detail=f"{type(exc).__name__}: {exc}",
            )

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data = buffer.getvalue()
        return GeneratedMedia(
            status="completed",
            origin="bytes",
            data=data,
            media_type="image/png",
            provider=self.name,
            model=model,
        )


__all__ = [
    "PROVIDER_NAME",
    "QUOTA_EXHAUSTED_MESSAGE",
    "RATE_LIMITED_MESSAGE",
    "HuggingFaceImageProvider",
]
