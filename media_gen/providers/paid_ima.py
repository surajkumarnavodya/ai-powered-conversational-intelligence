"""The existing paid IMA Studio path, behind the provider seam.

**An adapter, not a reimplementation.** Both methods call the same
`media_gen.image.generate_image` / `media_gen.video.generate_video` that
`agent.orchestrator.nodes.execute_generation` calls today, with the same
arguments, and translate the returned `MediaResult` into a
`GeneratedMedia`. No request is reshaped, no parameter is added, no
retry or timeout is introduced. The paid behaviour, API contract, UI and
billing path are unchanged, which is the hard requirement this commit is
written against and what
`tests/test_paid_generation_regression.py` pins.

**The advisory fields are dropped on purpose.** `ImageGenerationRequest`
carries `width`/`height`/`seed`/`steps`; the IMA API selects the model and
its parameters server-side and `generate_image` takes a prompt and nothing
else (see `media_gen/client.py`'s "no model picker" note). Passing them
would mean changing `generate_image`'s signature and the live call, which
is exactly what must not happen here. So they are ignored, that is stated
rather than silent, and a caller can see it reflected in
`GeneratedMedia.warnings`.

**Nothing calls this yet** -- routing lands later in the sequence. Added
now so the seam has a second implementation to be shaped against, and so
the free providers are written to an interface the paid path already
satisfies rather than the other way round.
"""

from __future__ import annotations

import logging

from config.settings import Settings
from media_gen.client import MediaGenerationNotConfiguredError, MediaResult, get_ima_client
from media_gen.providers import (
    GeneratedMedia,
    ImageGenerationRequest,
    ProviderHealth,
    ProviderName,
    VideoGenerationRequest,
)

logger = logging.getLogger(__name__)

#: The stable identifier for this backend. Matches `MediaResult.provider`'s
#: existing default so a result's provider label means the same thing
#: whether it came through the seam or through the live path.
PROVIDER_NAME: ProviderName = "ima_studio"

#: Appended to a result whenever the caller asked for render parameters
#: this API cannot accept. A warning rather than an error: the request is
#: still served, just not exactly as specified, and refusing it outright
#: would be worse than producing the image the provider can produce.
_ADVISORY_IGNORED = (
    "Size, seed and step count are chosen by the paid provider's own model "
    "and were not applied to this request."
)


def _advisory_warnings(
    *values: int | None,
) -> tuple[str, ...]:
    """`(_ADVISORY_IGNORED,)` when the caller specified anything this
    provider cannot honour, else empty."""
    return (_ADVISORY_IGNORED,) if any(v is not None for v in values) else ()


def _to_generated_media(result: MediaResult, *, warnings: tuple[str, ...] = ()) -> GeneratedMedia:
    """Translates the paid path's `MediaResult` into a `GeneratedMedia`.

    Translation only -- no field is recomputed. `origin="url"` because this
    API returns a link that
    `agent.orchestrator.nodes.execute_generation` fetches separately
    through `media_gen.download.download_media_bytes`; moving that fetch in
    here would change where the download happens on the paid path.

    `error`/`detail` keep their existing split exactly: `error` is the
    short user-facing sentence, `detail` the full internal payload for logs
    (CLAUDE.md 5.6).
    """
    if not result.ok:
        return GeneratedMedia(
            status="failed",
            provider=result.provider,
            model=result.model,
            error=result.error,
            detail=result.detail,
            warnings=warnings,
        )
    return GeneratedMedia(
        status="completed",
        origin="url",
        url=result.url,
        provider=result.provider,
        model=result.model,
        warnings=warnings,
    )


def _health(settings: Settings) -> ProviderHealth:
    """Config-only health: is the paid path switched on and credentialed?

    Deliberately makes **no** network call. A reachability probe against
    IMA would be an unauthenticated-looking request to a metered third
    party on every health poll, and `checked_remote=False` says plainly
    that this is a configuration read rather than a liveness check.

    Never reports the key itself, only whether one is present.
    """
    if not settings.enable_media_generation:
        return ProviderHealth(
            name=PROVIDER_NAME,
            available=False,
            reason="Paid media generation is switched off (ENABLE_MEDIA_GENERATION).",
        )
    if settings.ima_api_key is None:
        return ProviderHealth(
            name=PROVIDER_NAME,
            available=False,
            reason="No paid-provider API key is configured (IMA_API_KEY).",
        )
    return ProviderHealth(
        name=PROVIDER_NAME,
        available=True,
        detail="Configured. Reachability is not probed; generation is metered.",
        checked_remote=False,
    )


class ImaImageProvider:
    """`ImageProvider` over the existing paid text-to-image call."""

    name: ProviderName = PROVIDER_NAME

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def health(self) -> ProviderHealth:
        return _health(self._settings)

    def generate_image(self, request: ImageGenerationRequest) -> GeneratedMedia:
        """Calls `media_gen.image.generate_image(client, prompt=...)` --
        the identical call `execute_generation` makes today, with the
        prompt and nothing else.

        A missing configuration is returned as a failed result rather than
        raised, matching how `execute_generation` already handles
        `MediaGenerationNotConfiguredError` on this path: generation is not
        a reason a request blows up, it is a reason it reports failure.
        """
        from media_gen.image import generate_image

        warnings = _advisory_warnings(request.width, request.height, request.seed, request.steps)
        try:
            client = get_ima_client(self._settings)
        except MediaGenerationNotConfiguredError as exc:
            logger.error("[generation] paid provider not configured: %s", exc)
            return GeneratedMedia(
                status="failed",
                provider=self.name,
                error=str(exc),
                detail=str(exc),
                warnings=warnings,
            )

        result = generate_image(client, prompt=request.prompt)
        return _to_generated_media(result, warnings=warnings)


class ImaVideoProvider:
    """`VideoProvider` over the existing paid text-to-video call."""

    name: ProviderName = PROVIDER_NAME

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def health(self) -> ProviderHealth:
        return _health(self._settings)

    def generate_video(self, request: VideoGenerationRequest) -> GeneratedMedia:
        """Calls `media_gen.video.generate_video(client, prompt=...,
        duration_seconds=...)`, exactly as `execute_generation` does.

        **`duration_seconds` falls back to
        `Settings.media_gen_video_duration_seconds` when the request does
        not specify one**, because that is precisely what the live caller
        passes today -- reading it from settings here keeps the paid
        default identical rather than quietly sending `None`.

        Image-to-video is refused rather than silently downgraded to
        text-to-video. `image_to_video` is a real IMA task type but
        `generate_video` does not wire it (see its own docstring), and
        dropping the caller's init image would produce a clip of something
        they did not ask for.
        """
        from media_gen.video import generate_video

        warnings = _advisory_warnings(request.width, request.height, request.seed, request.steps)
        if request.init_image_bytes is not None:
            return GeneratedMedia(
                status="failed",
                provider=self.name,
                error="This provider does not support image-to-video.",
                detail=(
                    "ImaVideoProvider received init_image_bytes; media_gen.video.generate_video "
                    "wires text_to_video only, and silently ignoring the init image would "
                    "generate an unrelated clip."
                ),
                warnings=warnings,
            )

        duration = (
            request.duration_seconds
            if request.duration_seconds is not None
            else self._settings.media_gen_video_duration_seconds
        )
        try:
            client = get_ima_client(self._settings)
        except MediaGenerationNotConfiguredError as exc:
            logger.error("[generation] paid provider not configured: %s", exc)
            return GeneratedMedia(
                status="failed",
                provider=self.name,
                error=str(exc),
                detail=str(exc),
                warnings=warnings,
            )

        result = generate_video(client, prompt=request.prompt, duration_seconds=duration)
        return _to_generated_media(result, warnings=warnings)


__all__ = ["PROVIDER_NAME", "ImaImageProvider", "ImaVideoProvider"]
