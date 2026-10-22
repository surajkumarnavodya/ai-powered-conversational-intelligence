"""The provider seam for media generation -- one interface, many backends.

Every generation backend (the existing paid IMA Studio path, and the free
ones added alongside it) implements `ImageProvider` or `VideoProvider`, so
the routing layer only ever calls this interface and never a concrete
provider class directly.

Deliberately modelled on `media_gen/image_edit_provider.py`, which already
established exactly this pattern in this package: a `*Error` carrying both
internal detail and a short `safe_message`, frozen request/result
dataclasses, a `Protocol` seam, and a `Fake*` double in the same module for
tests. A second, differently-shaped provider abstraction in the same
package would be two answers to one question (CLAUDE.md 4.8), so this
follows that one rather than inventing a new convention.

**`GeneratedMedia` carries either a URL or bytes, never both, and that is
not sloppiness.** The paid provider's API hands back a URL which
`agent.orchestrator.nodes.execute_generation` then fetches through
`media_gen.download.download_media_bytes` (SSRF-validated, provider URL
never exposed to a client). A local provider such as ComfyUI has no URL to
give -- it returns the finished bytes. Forcing one shape would mean either
making the paid adapter download (moving where that happens today, i.e. a
behaviour change in the paid path, which is explicitly out of scope) or
inventing a fake URL for local output. So the result expresses both and
`origin` says which to read.

**Nothing calls this yet.** Routing lands with the free providers; this
commit is the seam and the paid adapter only, so the live paid path is
byte-for-byte unchanged (pinned by
`tests/test_paid_generation_regression.py`). That is the deliberate
"built, not wired" state CLAUDE.md 1 describes, not an oversight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

#: Which backend produced a result, for the honest-labelling requirement
#: ("Free -- local model, may be slower") and for the audit trail. A plain
#: string rather than an enum because a provider name is also an env-var
#: value and a UI label, and an enum would need mapping at both ends.
ProviderName = str

#: How to read a `GeneratedMedia` -- see the module docstring.
MediaOrigin = Literal["url", "bytes"]

GenerationStatus = Literal["completed", "failed"]


class GenerationProviderError(RuntimeError):
    """Any provider-side failure: a business error, a network failure, a
    timeout, or unusable output.

    Same two-message convention as `media_gen.client.MediaGenerationError`
    and `media_gen.image_edit_provider.ImageEditProviderError`: `str(exc)`
    is the full internal detail and is safe to log, `.safe_message` is a
    short user-facing sentence that never leaks a raw provider payload
    (CLAUDE.md 5.6).

    `retryable` tells the routing layer whether trying the *next* provider
    is sensible. A quota exhaustion or a down local server is retryable
    elsewhere; a prompt the provider refused is not, and retrying it on a
    paid backend would both cost money and fail again.
    """

    def __init__(
        self,
        message: str,
        *,
        safe_message: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.safe_message = safe_message or (
            "Media generation failed due to a provider error. Please try again in a moment."
        )
        self.retryable = retryable


@dataclass(frozen=True)
class ProviderHealth:
    """Whether one provider can serve a request right now, and why not.

    `reason` is required when `available` is False and is shown to the
    user: the brief's requirement is that an unavailable provider is
    "disabled with that reason", not silently missing from a dropdown. It
    is operator-facing detail (a missing model file, an unreachable
    ComfyUI, no `HF_TOKEN`), so it must never carry a secret -- a provider
    reports *that* a token is absent, never the token.

    `checked_remote` distinguishes "we asked the server" from "we read the
    config", so a health panel cannot imply a reachability check that
    never happened.
    """

    name: ProviderName
    available: bool
    reason: str | None = None
    detail: str | None = None
    checked_remote: bool = False

    def __post_init__(self) -> None:
        if not self.available and not self.reason:
            raise ValueError("an unavailable provider must say why (ProviderHealth.reason)")


@dataclass(frozen=True)
class ImageGenerationRequest:
    """One text-to-image request, in provider-neutral terms.

    `width`/`height`/`seed`/`steps` are **advisory**: a provider that
    cannot honour them ignores them and says so in its own docstring. The
    paid IMA path is exactly that case -- its API selects the model and its
    parameters server-side (`media_gen/client.py`'s "no model picker"
    note), so the adapter passes the prompt alone. Silently dropping them
    is wrong; the adapter documents it and
    `tests/test_paid_generation_regression.py` pins that the paid call
    shape is unchanged.
    """

    prompt: str
    width: int | None = None
    height: int | None = None
    seed: int | None = None
    steps: int | None = None
    negative_prompt: str | None = None


@dataclass(frozen=True)
class VideoGenerationRequest:
    """One text-to-video or image-to-video request.

    `init_image_bytes` set means image-to-video. Real, already-validated
    bytes by the time this is constructed -- never a filename, a blob URL
    or a client-supplied path, matching `ImageEditRequest`'s own rule for
    the same reason.

    `duration_seconds` is advisory in the same way the image request's size
    fields are: every current text-to-video model caps in the single-digit
    seconds (see `media_gen/video.py`'s measured note on the paid model's
    4-15s range), so a provider clamps or rejects rather than promising a
    length it cannot produce.
    """

    prompt: str
    duration_seconds: int | None = None
    width: int | None = None
    height: int | None = None
    seed: int | None = None
    steps: int | None = None
    init_image_bytes: bytes | None = None
    init_image_content_type: str | None = None


@dataclass(frozen=True)
class GeneratedMedia:
    """What a provider produced -- a URL to fetch, or the bytes themselves.

    Read `origin` to know which. `url` is set for a provider whose API
    returns one (the paid path); `data` for a provider that returns the
    asset directly (a local ComfyUI render). Exactly one is populated on a
    `completed` result; both are None on a `failed` one.

    `provider` and `model` exist so the UI can label a result honestly --
    which backend made this, not just that something did.
    """

    status: GenerationStatus
    origin: MediaOrigin | None = None
    url: str | None = None
    data: bytes | None = None
    media_type: str | None = None
    provider: ProviderName = "unknown"
    model: str | None = None
    #: Short, user-facing failure text (`GenerationProviderError
    #: .safe_message`). Safe to render.
    error: str | None = None
    #: Full internal failure detail, for logs only -- never a response body
    #: (CLAUDE.md 5.6). Mirrors `MediaResult.detail`'s role exactly.
    detail: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.status != "completed":
            return
        if self.origin == "url" and not self.url:
            raise ValueError("a completed url-origin result must carry a url")
        if self.origin == "bytes" and not self.data:
            raise ValueError("a completed bytes-origin result must carry data")
        if self.origin is None:
            raise ValueError("a completed result must say whether it carries a url or bytes")

    @property
    def ok(self) -> bool:
        """True only for a completed result that actually carries media.

        Mirrors `MediaResult.ok`, which every existing caller already
        branches on, so a reader does not have to learn a second
        success-test in the same package.
        """
        if self.status != "completed":
            return False
        return bool(self.url) if self.origin == "url" else bool(self.data)


class ImageProvider(Protocol):
    """The seam every image backend implements.

    `name` is the stable identifier used in config, in the health endpoint
    and as the UI's provider label, so it is part of the interface rather
    than an implementation detail.
    """

    name: ProviderName

    def health(self) -> ProviderHealth: ...

    def generate_image(self, request: ImageGenerationRequest) -> GeneratedMedia: ...


class VideoProvider(Protocol):
    """The seam every video backend implements. Separate from
    `ImageProvider` rather than one interface with a `kind` argument: a
    deployment can genuinely have images and no video (this is the common
    case -- a 4 GB GPU runs neither local video model), and a single
    interface would force every image-only provider to implement a video
    method that always fails."""

    name: ProviderName

    def health(self) -> ProviderHealth: ...

    def generate_video(self, request: VideoGenerationRequest) -> GeneratedMedia: ...


class FakeImageProvider:
    """Test double -- never makes a network call.

    Records every request it receives (`self.requests`) so a test can
    assert what *would* have been sent, including the advisory size/seed
    fields, rather than only that a call happened. Same contract as
    `media_gen.image_edit_provider.FakeImageEditProvider`, which exists for
    the same reason.
    """

    def __init__(
        self,
        *,
        name: ProviderName = "fake_image",
        result: GeneratedMedia | None = None,
        error: GenerationProviderError | None = None,
        health: ProviderHealth | None = None,
    ) -> None:
        self.name = name
        self.requests: list[ImageGenerationRequest] = []
        self._result = result
        self._error = error
        self._health = health

    def health(self) -> ProviderHealth:
        return self._health or ProviderHealth(name=self.name, available=True)

    def generate_image(self, request: ImageGenerationRequest) -> GeneratedMedia:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        if self._result is not None:
            return self._result
        return GeneratedMedia(
            status="completed",
            origin="bytes",
            data=b"fake-image-bytes",
            media_type="image/png",
            provider=self.name,
            model="fake-image-model",
        )


class FakeVideoProvider:
    """Test double for the video seam -- see `FakeImageProvider`."""

    def __init__(
        self,
        *,
        name: ProviderName = "fake_video",
        result: GeneratedMedia | None = None,
        error: GenerationProviderError | None = None,
        health: ProviderHealth | None = None,
    ) -> None:
        self.name = name
        self.requests: list[VideoGenerationRequest] = []
        self._result = result
        self._error = error
        self._health = health

    def health(self) -> ProviderHealth:
        return self._health or ProviderHealth(name=self.name, available=True)

    def generate_video(self, request: VideoGenerationRequest) -> GeneratedMedia:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        if self._result is not None:
            return self._result
        return GeneratedMedia(
            status="completed",
            origin="bytes",
            data=b"fake-video-bytes",
            media_type="video/mp4",
            provider=self.name,
            model="fake-video-model",
        )


__all__ = [
    "FakeImageProvider",
    "FakeVideoProvider",
    "GeneratedMedia",
    "GenerationProviderError",
    "GenerationStatus",
    "ImageGenerationRequest",
    "ImageProvider",
    "MediaOrigin",
    "ProviderHealth",
    "ProviderName",
    "VideoGenerationRequest",
    "VideoProvider",
]
