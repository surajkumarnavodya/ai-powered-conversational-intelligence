"""The paid generation path must behave exactly as it did before the
provider seam existed.

This is the guard for the one hard constraint on the free-generation work:
**do not change the paid feature's behaviour, API contract, UI or billing
path.** Adding `media_gen/providers/` is additive, and these tests assert
that in the way that actually matters -- by exercising the live caller
(`agent.orchestrator.nodes.execute_generation`) and checking it still makes
the same calls, in the same order, with the same arguments, and still
applies the same safety and rate-limit gates first.

What this file deliberately does **not** do is re-test IMA client
mechanics; `tests/test_media_gen.py` owns those. The subject here is the
boundary between the live path and the new seam.

Fully mocked -- no network call, no provider credit spent.

**Patch the package root, not the submodule.** `execute_generation` does a
function-level `from media_gen import generate_image, generate_video,
get_ima_client, download_media_bytes`, which resolves the names re-exported
by `media_gen/__init__.py`. Monkeypatching `media_gen.image.generate_image`
rebinds something nothing reads, and the call then goes out to the real IMA
endpoint -- which is what happened while writing this file (a live HTTP 401
against `/open/v1/product/list` appeared in the test log). Every patch here
targets `media_gen.<name>` for that reason.
"""

from __future__ import annotations

import pytest

import agent.orchestrator.nodes as orchestrator_nodes
from config.settings import Settings
from media_gen.client import MediaResult
from security.secrets import SecretStr


def _settings(**overrides) -> Settings:
    base = {
        "enable_media_generation": True,
        "ima_api_key": SecretStr("test-key"),
        "media_gen_rate_limit": 100,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The media-generation limiter is a process-lifetime singleton, so a
    test that trips it would leak into the next one."""
    from agent import rate_limit

    yield
    if hasattr(rate_limit, "_media_generation_limiter"):
        rate_limit._media_generation_limiter = None


class TestTheLivePathStillCallsThePaidFunctionsDirectly:
    """The seam exists but nothing routes through it yet. If a later commit
    wires routing in, it must do so deliberately -- not by these assertions
    quietly starting to fail."""

    def test_image_generation_calls_generate_image_with_the_prompt_alone(self, monkeypatch):
        captured: dict[str, object] = {}

        def _fake_generate_image(client, prompt):
            captured["prompt"] = prompt
            return MediaResult(status="completed", url="https://example.invalid/a.png")

        monkeypatch.setattr("media_gen.generate_image", _fake_generate_image)
        monkeypatch.setattr("media_gen.get_ima_client", lambda settings: "CLIENT")
        monkeypatch.setattr(
            "media_gen.download_media_bytes",
            lambda *a, **k: (b"\x89PNG", "image/png"),
        )

        orchestrator_nodes.execute_generation(
            "generate an image of a red bicycle", "image", _settings()
        )

        assert captured["prompt"] == "generate an image of a red bicycle"

    def test_video_generation_still_passes_the_configured_duration(self, monkeypatch):
        captured: dict[str, object] = {}

        def _fake_generate_video(client, prompt, duration_seconds):
            captured["duration_seconds"] = duration_seconds
            return MediaResult(status="completed", url="https://example.invalid/a.mp4")

        monkeypatch.setattr("media_gen.generate_video", _fake_generate_video)
        monkeypatch.setattr("media_gen.get_ima_client", lambda settings: "CLIENT")
        monkeypatch.setattr(
            "media_gen.download_media_bytes",
            lambda *a, **k: (b"\x00\x00", "video/mp4"),
        )

        orchestrator_nodes.execute_generation(
            "generate a video of a kite", "video", _settings(media_gen_video_duration_seconds=7)
        )

        assert captured["duration_seconds"] == 7


class TestTheSafetyAndRateGatesStillRunFirst:
    """Order is the property: a rejected prompt must never reach the paid
    API, because reaching it costs money."""

    def test_a_refused_prompt_never_calls_the_provider(self, monkeypatch):
        from media_gen.content_policy import DISALLOWED_PROMPT_SUBSTRINGS

        called: list[str] = []
        monkeypatch.setattr("media_gen.generate_image", lambda **k: called.append("called"))
        monkeypatch.setattr("media_gen.get_ima_client", lambda settings: "CLIENT")

        result = orchestrator_nodes.execute_generation(
            f"generate an image of {DISALLOWED_PROMPT_SUBSTRINGS[0]}", "image", _settings()
        )

        assert called == []
        assert result["status"] == "failed"

    def test_the_rate_limiter_still_blocks_before_the_provider(self, monkeypatch):
        called: list[str] = []
        monkeypatch.setattr("media_gen.generate_image", lambda **k: called.append("called"))
        monkeypatch.setattr("media_gen.get_ima_client", lambda settings: "CLIENT")

        settings = _settings(media_gen_rate_limit=1, media_gen_rate_window_seconds=600.0)
        monkeypatch.setattr(
            "media_gen.generate_image",
            lambda client, prompt: MediaResult(status="completed", url="https://x.invalid/a.png"),
        )
        monkeypatch.setattr(
            "media_gen.download_media_bytes",
            lambda *a, **k: (b"\x89PNG", "image/png"),
        )
        orchestrator_nodes.execute_generation("generate an image of a cat", "image", settings)

        monkeypatch.setattr("media_gen.generate_image", lambda **k: called.append("called"))
        second = orchestrator_nodes.execute_generation(
            "generate an image of a dog", "image", settings
        )

        assert called == [], "the second call must be refused before reaching the provider"
        assert second["status"] == "failed"


class TestThePackageSurfaceIsUnchanged:
    """`media_gen/__init__.py` is imported by name in
    `execute_generation`'s own function-level import. An adapter that
    shadowed or removed any of these would break the live path."""

    @pytest.mark.parametrize(
        "name",
        [
            "IMAClient",
            "MediaCache",
            "MediaGenerationError",
            "MediaGenerationNotConfiguredError",
            "MediaResult",
            "download_media_bytes",
            "get_ima_client",
            "get_media_cache",
            "generate_audio",
            "generate_image",
            "generate_video",
        ],
    )
    def test_every_previously_exported_name_is_still_exported(self, name):
        import media_gen

        assert name in media_gen.__all__
        assert hasattr(media_gen, name)

    def test_the_new_seam_is_not_exported_from_the_package_root(self):
        """Kept out of `media_gen/__init__.py` on purpose: that module's
        import is on the live paid path, and adding provider imports to it
        would pull new code into it for no benefit while routing is still
        unwired."""
        import media_gen

        assert "ImageProvider" not in media_gen.__all__
        assert "GeneratedMedia" not in media_gen.__all__


class TestMediaResultSemanticsAreUnchanged:
    """`MediaResult` is the paid path's return shape and several call sites
    branch on `.ok`. The seam must not have altered it."""

    def test_ok_still_requires_a_url(self):
        assert MediaResult(status="completed", url=None).ok is False
        assert MediaResult(status="completed", url="https://x.invalid/a.png").ok is True

    def test_provider_still_defaults_to_ima_studio(self):
        assert MediaResult(status="completed", url="https://x.invalid/a.png").provider == (
            "ima_studio"
        )
