"""The media-generation provider seam and the paid adapter over it.

Two things are pinned here. First the **result shape**: `GeneratedMedia`
carries either a URL or bytes, and a completed result that carries neither
is a programming error rather than something a caller has to defend
against. Second the **paid adapter's translation**: it must call the
existing paid functions with exactly today's arguments and reshape nothing.

`tests/test_paid_generation_regression.py` is the companion -- it pins that
the *live* path is unchanged. This file pins that the new seam behaves.

Fully mocked: the paid functions are monkeypatched, so nothing here makes a
network call or spends provider credit.
"""

from __future__ import annotations

import pytest

from config.settings import Settings
from media_gen.client import MediaGenerationNotConfiguredError, MediaResult
from media_gen.providers import (
    FakeImageProvider,
    FakeVideoProvider,
    GeneratedMedia,
    GenerationProviderError,
    ImageGenerationRequest,
    ImageProvider,
    ProviderHealth,
    VideoGenerationRequest,
    VideoProvider,
    paid_ima,
)


def _settings(**overrides) -> Settings:
    from security.secrets import SecretStr

    base = {
        "enable_media_generation": True,
        "ima_api_key": SecretStr("test-key"),
    }
    base.update(overrides)
    return Settings(**base)


class TestGeneratedMediaShape:
    def test_a_url_result_is_ok(self):
        media = GeneratedMedia(
            status="completed", origin="url", url="https://example.invalid/a.png"
        )
        assert media.ok is True

    def test_a_bytes_result_is_ok(self):
        media = GeneratedMedia(status="completed", origin="bytes", data=b"x")
        assert media.ok is True

    def test_a_failed_result_is_never_ok(self):
        assert GeneratedMedia(status="failed", error="nope").ok is False

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"status": "completed", "origin": "url"},
            {"status": "completed", "origin": "bytes"},
            {"status": "completed"},
        ],
    )
    def test_a_completed_result_must_actually_carry_media(self, kwargs):
        """A 'completed' result with nothing in it is the bug this guards:
        every consumer would branch on `ok` and silently render nothing."""
        with pytest.raises(ValueError):
            GeneratedMedia(**kwargs)

    def test_a_failed_result_needs_no_media(self):
        # The counterpart -- the check above must not make failures awkward
        # to construct, which is the common case.
        assert GeneratedMedia(status="failed").ok is False


class TestProviderHealth:
    def test_an_unavailable_provider_must_say_why(self):
        """The brief's requirement: unavailable is reported "with that
        reason", not as a silently missing option."""
        with pytest.raises(ValueError):
            ProviderHealth(name="x", available=False)

    def test_an_available_provider_needs_no_reason(self):
        assert ProviderHealth(name="x", available=True).reason is None

    def test_remote_checking_is_not_claimed_by_default(self):
        """A health panel must not imply a reachability probe that never
        happened."""
        assert ProviderHealth(name="x", available=True).checked_remote is False


class TestTheFakesSatisfyTheProtocols:
    def test_the_fakes_are_usable_where_the_protocol_is_expected(self):
        image: ImageProvider = FakeImageProvider()
        video: VideoProvider = FakeVideoProvider()

        assert image.generate_image(ImageGenerationRequest(prompt="a cat")).ok
        assert video.generate_video(VideoGenerationRequest(prompt="a cat")).ok

    def test_a_fake_records_the_whole_request_not_just_the_prompt(self):
        """So a provider test can assert what would have been sent --
        including the advisory fields -- rather than only that a call
        happened."""
        fake = FakeImageProvider()

        fake.generate_image(ImageGenerationRequest(prompt="p", width=512, seed=7, steps=4))

        assert fake.requests[0].width == 512
        assert fake.requests[0].seed == 7
        assert fake.requests[0].steps == 4

    def test_a_fake_can_raise(self):
        fake = FakeImageProvider(error=GenerationProviderError("boom", retryable=True))

        with pytest.raises(GenerationProviderError) as excinfo:
            fake.generate_image(ImageGenerationRequest(prompt="p"))

        assert excinfo.value.retryable is True


class TestGenerationProviderError:
    def test_it_keeps_internal_detail_and_a_safe_message_apart(self):
        """Same split as `MediaGenerationError` -- `str(exc)` is loggable
        detail, `.safe_message` is what a response body may carry
        (CLAUDE.md 5.6)."""
        exc = GenerationProviderError("raw payload {'code': 4008}")

        assert "4008" in str(exc)
        assert "4008" not in exc.safe_message

    def test_retryable_defaults_to_false(self):
        """A refusal must not be retried onto another provider by default --
        on the paid backend that would cost money and fail again."""
        assert GenerationProviderError("x").retryable is False


class TestPaidImageAdapter:
    def test_it_calls_the_existing_function_with_the_prompt_alone(self, monkeypatch):
        """The call shape `execute_generation` makes today. Anything extra
        would be a change to the paid path."""
        captured: dict[str, object] = {}

        def _fake_generate_image(client, prompt):
            captured["client"] = client
            captured["prompt"] = prompt
            return MediaResult(status="completed", url="https://example.invalid/a.png")

        monkeypatch.setattr("media_gen.image.generate_image", _fake_generate_image)
        monkeypatch.setattr(paid_ima, "get_ima_client", lambda settings: "CLIENT")

        result = paid_ima.ImaImageProvider(_settings()).generate_image(
            ImageGenerationRequest(prompt="a red bicycle")
        )

        assert captured == {"client": "CLIENT", "prompt": "a red bicycle"}
        assert result.ok is True
        assert result.origin == "url"
        assert result.url == "https://example.invalid/a.png"

    def test_it_returns_a_url_not_bytes(self, monkeypatch):
        """The paid API hands back a link that `execute_generation`
        downloads separately; fetching it in the adapter would move where
        that happens today."""
        monkeypatch.setattr(
            "media_gen.image.generate_image",
            lambda client, prompt: MediaResult(status="completed", url="https://x.invalid/a.png"),
        )
        monkeypatch.setattr(paid_ima, "get_ima_client", lambda settings: object())

        result = paid_ima.ImaImageProvider(_settings()).generate_image(
            ImageGenerationRequest(prompt="p")
        )

        assert result.data is None
        assert result.origin == "url"

    def test_advisory_render_parameters_are_reported_not_silently_dropped(self, monkeypatch):
        monkeypatch.setattr(
            "media_gen.image.generate_image",
            lambda client, prompt: MediaResult(status="completed", url="https://x.invalid/a.png"),
        )
        monkeypatch.setattr(paid_ima, "get_ima_client", lambda settings: object())

        result = paid_ima.ImaImageProvider(_settings()).generate_image(
            ImageGenerationRequest(prompt="p", width=1024, steps=4)
        )

        assert result.warnings
        assert "not applied" in result.warnings[0]

    def test_no_warning_when_nothing_was_asked_for(self, monkeypatch):
        """Non-vacuity of the warning above -- it has to distinguish."""
        monkeypatch.setattr(
            "media_gen.image.generate_image",
            lambda client, prompt: MediaResult(status="completed", url="https://x.invalid/a.png"),
        )
        monkeypatch.setattr(paid_ima, "get_ima_client", lambda settings: object())

        result = paid_ima.ImaImageProvider(_settings()).generate_image(
            ImageGenerationRequest(prompt="p")
        )

        assert result.warnings == ()

    def test_a_provider_failure_keeps_the_error_detail_split(self, monkeypatch):
        monkeypatch.setattr(
            "media_gen.image.generate_image",
            lambda client, prompt: MediaResult(
                status="failed", error="Short message.", detail="{'code': 4008}"
            ),
        )
        monkeypatch.setattr(paid_ima, "get_ima_client", lambda settings: object())

        result = paid_ima.ImaImageProvider(_settings()).generate_image(
            ImageGenerationRequest(prompt="p")
        )

        assert result.ok is False
        assert result.error == "Short message."
        assert result.detail == "{'code': 4008}"

    def test_missing_configuration_is_a_failed_result_not_an_exception(self, monkeypatch):
        """Matches how `execute_generation` already handles this: not being
        configured makes generation report failure, it does not blow up the
        request."""

        def _raise(settings):
            raise MediaGenerationNotConfiguredError("not configured")

        monkeypatch.setattr(paid_ima, "get_ima_client", _raise)

        result = paid_ima.ImaImageProvider(_settings()).generate_image(
            ImageGenerationRequest(prompt="p")
        )

        assert result.ok is False
        assert result.status == "failed"


class TestPaidVideoAdapter:
    def test_it_passes_the_configured_duration_when_the_request_omits_one(self, monkeypatch):
        """Exactly what the live caller does -- it reads
        `media_gen_video_duration_seconds` from settings. Sending None
        instead would change the paid default."""
        captured: dict[str, object] = {}

        def _fake(client, prompt, duration_seconds):
            captured["prompt"] = prompt
            captured["duration_seconds"] = duration_seconds
            return MediaResult(status="completed", url="https://x.invalid/a.mp4")

        monkeypatch.setattr("media_gen.video.generate_video", _fake)
        monkeypatch.setattr(paid_ima, "get_ima_client", lambda settings: object())

        paid_ima.ImaVideoProvider(_settings(media_gen_video_duration_seconds=9)).generate_video(
            VideoGenerationRequest(prompt="a kite")
        )

        assert captured["duration_seconds"] == 9

    def test_an_explicit_request_duration_wins(self, monkeypatch):
        captured: dict[str, object] = {}

        def _fake(client, prompt, duration_seconds):
            captured["duration_seconds"] = duration_seconds
            return MediaResult(status="completed", url="https://x.invalid/a.mp4")

        monkeypatch.setattr("media_gen.video.generate_video", _fake)
        monkeypatch.setattr(paid_ima, "get_ima_client", lambda settings: object())

        paid_ima.ImaVideoProvider(_settings(media_gen_video_duration_seconds=9)).generate_video(
            VideoGenerationRequest(prompt="a kite", duration_seconds=5)
        )

        assert captured["duration_seconds"] == 5

    def test_image_to_video_is_refused_rather_than_downgraded(self, monkeypatch):
        """Dropping the init image would generate a clip of something the
        caller never asked for. `generate_video` wires text_to_video only."""
        called: list[str] = []
        monkeypatch.setattr(
            "media_gen.video.generate_video",
            lambda **kwargs: called.append("called"),
        )
        monkeypatch.setattr(paid_ima, "get_ima_client", lambda settings: object())

        result = paid_ima.ImaVideoProvider(_settings()).generate_video(
            VideoGenerationRequest(prompt="p", init_image_bytes=b"\x89PNG")
        )

        assert result.ok is False
        assert "image-to-video" in result.error
        assert called == [], "the paid API must not be called for an unsupported task"


class TestPaidHealth:
    def test_unavailable_when_the_feature_is_off(self):
        health = paid_ima.ImaImageProvider(_settings(enable_media_generation=False)).health()

        assert health.available is False
        assert "ENABLE_MEDIA_GENERATION" in health.reason

    def test_unavailable_without_a_key(self):
        health = paid_ima.ImaImageProvider(_settings(ima_api_key=None)).health()

        assert health.available is False
        assert "IMA_API_KEY" in health.reason

    def test_available_when_configured(self):
        assert paid_ima.ImaImageProvider(_settings()).health().available is True

    def test_health_never_reports_the_key_itself(self):
        """A health payload is operator-facing but still a response body --
        it names whether a key exists, never its value (CLAUDE.md 5.6)."""
        from security.secrets import SecretStr

        health = paid_ima.ImaImageProvider(
            _settings(ima_api_key=SecretStr("super-secret-value"))
        ).health()

        rendered = f"{health.reason} {health.detail}"
        assert "super-secret-value" not in rendered

    def test_health_does_not_claim_a_reachability_probe(self):
        """It is a config read. Claiming otherwise would make an operator
        trust a liveness signal that was never collected."""
        assert paid_ima.ImaImageProvider(_settings()).health().checked_remote is False
