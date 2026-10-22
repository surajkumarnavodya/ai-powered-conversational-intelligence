"""What is allowed to become somebody's profile picture.

The split this file pins is the one
`identity/profile_picture.py` exists to hold: **the deterministic gates
refuse, the model-driven suitability check degrades.** Size, type, magic
bytes and malware are safety controls and every one of them rejects; a
vision verdict is a quality judgement, so an absent one accepts the
picture and discloses that it was not checked (CLAUDE.md 4.2).

Fully mocked, like every other test here -- no Ollama and no malware
daemon. The vision call is replaced at `_check_with_vision`, because what
each test is actually about is what the pipeline *does* with a verdict,
not how the HTTP call is shaped.
"""

from __future__ import annotations

import io
import random

import pytest
from identity import profile_picture as pp
from identity.profile_picture import (
    REASON_EMPTY,
    REASON_MALWARE,
    REASON_NOT_AN_IMAGE,
    REASON_UNSUPPORTED_TYPE,
    REASON_VISION_GENERIC,
    STORED_MEDIA_TYPE,
    prepare_profile_picture,
)
from PIL import Image

from config.settings import Settings


def _settings(**overrides) -> Settings:
    base = {
        # Blank by default so the vision branch is reached deliberately,
        # by a monkeypatch, rather than by whatever a developer's .env says.
        "media_vision_model": "",
        "malware_scan_provider": "disabled",
    }
    base.update(overrides)
    return Settings(**base)


def _image(fmt: str = "PNG", size: tuple[int, int] = (800, 600)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (90, 120, 200)).save(buf, format=fmt)
    return buf.getvalue()


def _large_image(min_bytes: int) -> bytes:
    """A PNG of at least `min_bytes`, for the size-cap tests.

    Random noise, not a flat colour: a solid 900x900 PNG compresses to a
    few hundred bytes, so a flat image can never exceed a KB-scale cap and
    a test built on one would pass while exercising nothing.
    """
    rng = random.Random(1234)
    side = 256
    while True:
        pixels = bytes(rng.getrandbits(8) for _ in range(side * side * 3))
        buf = io.BytesIO()
        Image.frombytes("RGB", (side, side), pixels).save(buf, format="PNG")
        data = buf.getvalue()
        if len(data) >= min_bytes:
            return data
        side *= 2


def _accept(monkeypatch) -> None:
    monkeypatch.setattr(pp, "_check_with_vision", lambda *_a, **_k: (True, ""))


def _reject(monkeypatch, reason: str) -> None:
    monkeypatch.setattr(pp, "_check_with_vision", lambda *_a, **_k: (False, reason))


def _no_verdict(monkeypatch) -> None:
    monkeypatch.setattr(pp, "_check_with_vision", lambda *_a, **_k: None)


class TestTheStoredImageIsAlwaysANormalizedSquare:
    @pytest.mark.parametrize(
        ("fmt", "filename", "media_type", "size"),
        [
            ("PNG", "me.png", "image/png", (800, 600)),
            ("JPEG", "me.jpg", "image/jpeg", (1200, 400)),
            ("JPEG", "me.jpeg", "image/jpg", (400, 1200)),
            ("WEBP", "me.webp", "image/webp", (500, 500)),
        ],
    )
    def test_every_accepted_format_becomes_one_square_webp(
        self, monkeypatch, fmt, filename, media_type, size
    ):
        """Whatever came in -- portrait, landscape or already square --
        exactly one shape and one format comes out, so `avatar_media_type`
        has one possible value and the circular avatar never letterboxes."""
        _accept(monkeypatch)
        settings = _settings()

        result = prepare_profile_picture(filename, media_type, _image(fmt, size), settings)

        assert result.accepted, result.reason
        assert result.media_type == STORED_MEDIA_TYPE
        with Image.open(io.BytesIO(result.image_bytes)) as stored:
            assert stored.size == (settings.profile_picture_size_px,) * 2
            assert stored.format == "WEBP"

    def test_the_uploaded_bytes_are_never_what_gets_stored(self, monkeypatch):
        """The original is re-encoded and discarded -- so EXIF, trailing
        data and any non-pixel payload in the upload do not survive into
        the database."""
        _accept(monkeypatch)
        original = _image("PNG", (800, 600))

        result = prepare_profile_picture("me.png", "image/png", original, _settings())

        assert result.image_bytes != original

    def test_the_square_edge_follows_the_setting(self, monkeypatch):
        _accept(monkeypatch)

        result = prepare_profile_picture(
            "me.png", "image/png", _image(), _settings(profile_picture_size_px=64)
        )

        with Image.open(io.BytesIO(result.image_bytes)) as stored:
            assert stored.size == (64, 64)


class TestTheDeterministicGatesRefuse:
    def test_an_empty_file(self):
        assert prepare_profile_picture("me.png", "image/png", b"", _settings()).reason == (
            REASON_EMPTY
        )

    def test_a_file_over_the_size_cap(self):
        """Checked on the real byte length, before any decode."""
        image = _image("PNG", (900, 900))
        result = prepare_profile_picture(
            "me.png",
            "image/png",
            image,
            _settings(profile_picture_max_bytes=len(image) - 1),
        )

        assert result.accepted is False
        assert "larger than" in result.reason

    def test_a_file_exactly_at_the_cap_is_allowed(self, monkeypatch):
        """The boundary belongs inside the limit -- "up to 5 MB" has to
        mean 5 MB is fine, or the stated limit is a lie by one byte."""
        _accept(monkeypatch)
        image = _image("PNG", (900, 900))

        result = prepare_profile_picture(
            "me.png", "image/png", image, _settings(profile_picture_max_bytes=len(image))
        )

        assert result.accepted is True

    @pytest.mark.parametrize(
        "filename", ["me.gif", "me.bmp", "me.svg", "me.pdf", "me", "me.PNG.exe"]
    )
    def test_an_extension_outside_the_allowlist(self, filename):
        result = prepare_profile_picture(filename, "image/png", _image(), _settings())

        assert result.reason == REASON_UNSUPPORTED_TYPE

    def test_a_declared_type_that_contradicts_the_extension(self):
        """Both are caller-controlled, so disagreement is not resolved in
        the caller's favour."""
        result = prepare_profile_picture("me.png", "image/webp", _image("PNG"), _settings())

        assert result.reason == REASON_UNSUPPORTED_TYPE

    def test_a_declared_type_with_parameters_is_still_read(self):
        """`multipart/form-data` clients send `image/png; charset=...`
        often enough that treating it as a mismatch would reject valid
        uploads."""
        result = prepare_profile_picture(
            "me.png", "image/png; charset=binary", _image("PNG"), _settings()
        )

        assert result.accepted or result.reason != REASON_UNSUPPORTED_TYPE

    def test_a_file_that_only_claims_to_be_an_image(self):
        """The magic bytes are the real check -- this is a renamed
        executable, and it must stop before Pillow is handed it."""
        result = prepare_profile_picture(
            "me.png", "image/png", b"MZ\x90\x00" + b"\x00" * 2048, _settings()
        )

        assert result.reason == REASON_NOT_AN_IMAGE

    def test_a_webp_signature_is_checked_as_a_riff_pair(self):
        """ "RIFF" alone is not WebP -- a .wav renamed to .webp starts with
        RIFF too. Reusing `attachments.validation._signature_matches` is
        what gets this right rather than a naive 4-byte compare."""
        riff_but_not_webp = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 64

        result = prepare_profile_picture("me.webp", "image/webp", riff_but_not_webp, _settings())

        assert result.reason == REASON_NOT_AN_IMAGE

    def test_an_image_the_malware_scanner_blocks(self, monkeypatch):
        """Fail-closed, and it runs on the raw upload before the decode."""
        _accept(monkeypatch)

        class _Blocked:
            blocked = True
            status = "infected"

        monkeypatch.setattr(pp, "scan_upload", lambda *_a, **_k: _Blocked())

        result = prepare_profile_picture("me.png", "image/png", _image(), _settings())

        assert result.reason == REASON_MALWARE
        assert result.image_bytes is None

    def test_the_scanner_runs_before_the_image_is_decoded(self, monkeypatch):
        """Order is a security property here, not a detail: an infected
        file must never reach Pillow."""
        calls: list[str] = []

        class _Clean:
            blocked = False
            status = "clean"

        def _scan(*_a, **_k):
            calls.append("scan")
            return _Clean()

        def _resize(*_a, **_k):
            calls.append("decode")
            raise AssertionError("unreachable in this test")

        monkeypatch.setattr(pp, "scan_upload", _scan)
        monkeypatch.setattr(pp, "resize_image", _resize)

        with pytest.raises(AssertionError):
            prepare_profile_picture("me.png", "image/png", _image(), _settings())

        assert calls == ["scan", "decode"]

    def test_a_corrupt_image_that_passes_the_signature_check(self, monkeypatch):
        """A real PNG header followed by rubbish: the signature matches, so
        only the decode can catch it, and it must not raise out."""
        _accept(monkeypatch)
        truncated = _image("PNG")[:40] + b"\x00" * 200

        result = prepare_profile_picture("me.png", "image/png", truncated, _settings())

        assert result.accepted is False
        assert result.reason


class TestTheVisionCheckGates:
    def test_a_rejection_is_reported_with_the_models_reason(self, monkeypatch):
        _reject(monkeypatch, "face is too blurry")

        result = prepare_profile_picture("me.png", "image/png", _image(), _settings())

        assert result.accepted is False
        assert result.reason == "face is too blurry"
        assert result.image_bytes is None

    def test_a_rejection_without_a_reason_still_says_something(self, monkeypatch):
        _reject(monkeypatch, "")

        result = prepare_profile_picture("me.png", "image/png", _image(), _settings())

        assert result.reason == REASON_VISION_GENERIC

    def test_an_approval_is_marked_as_actually_checked(self, monkeypatch):
        _accept(monkeypatch)

        result = prepare_profile_picture("me.png", "image/png", _image(), _settings())

        assert result.accepted is True
        assert result.vision_checked is True
        assert result.vision_skipped_reason is None


class TestNoVerdictMeansNotCheckedNeverApproved:
    """The fail-open half of CLAUDE.md 4.2, and the disclosure that makes
    it honest."""

    def test_the_picture_is_stored_when_no_model_is_configured(self):
        result = prepare_profile_picture(
            "me.png", "image/png", _image(), _settings(media_vision_model="")
        )

        assert result.accepted is True
        assert result.vision_checked is False
        assert "no vision model" in result.vision_skipped_reason

    @staticmethod
    def _recording_client(monkeypatch) -> list[int]:
        """Patches the vision client factory and records each construction.

        A *recorder*, not a raiser, and that distinction is the whole
        reason this helper exists. `_check_with_vision` catches `Exception`
        broadly so a quality aid can never break an upload -- and
        `AssertionError` is an `Exception`, so a patch that raises is
        swallowed and the function returns None either way. A test built
        on raising therefore passes whether the switch is honoured or not
        (it did, until the mutation check caught it). Counting calls is
        observable from outside that catch.
        """
        import agent.llm_client as llm_client_mod

        built: list[int] = []

        def _record(*_a, **_k):
            built.append(1)
            raise RuntimeError("no real Ollama in a mocked suite")

        monkeypatch.setattr(llm_client_mod, "get_ollama_client", _record)
        return built

    def test_the_switch_turns_the_check_off_without_rejecting(self, monkeypatch):
        """`PROFILE_PICTURE_VISION_CHECK_ENABLED=false` is a rollback switch
        for this one layer (CLAUDE.md 5.8). It must skip the model, not
        reject the upload -- disabling a check cannot be stricter than
        leaving it on. Pinned because the setting shipped unread once.
        """
        built = self._recording_client(monkeypatch)

        verdict = pp._check_with_vision(
            b"irrelevant",
            _settings(media_vision_model="llava", profile_picture_vision_check_enabled=False),
        )

        assert verdict is None
        assert built == [], "the vision model was reached despite the switch being off"

    def test_the_same_patch_does_see_a_call_when_the_switch_is_on(self, monkeypatch):
        """Non-vacuity of the assertion above: with the switch on, the very
        same recorder registers a construction, so `built == []` is a real
        observation rather than an unreachable one."""
        built = self._recording_client(monkeypatch)

        pp._check_with_vision(
            b"irrelevant",
            _settings(media_vision_model="llava", profile_picture_vision_check_enabled=True),
        )

        assert built == [1]

    def test_the_switch_is_reported_as_the_reason(self):
        """The disclosure names *which* skip happened, so an operator who
        turned it off does not read "did not complete in time"."""
        result = prepare_profile_picture(
            "me.png",
            "image/png",
            _image(),
            _settings(media_vision_model="llava", profile_picture_vision_check_enabled=False),
        )

        assert result.accepted is True
        assert result.vision_checked is False
        assert "switched off" in result.vision_skipped_reason

    def test_the_picture_is_stored_when_the_check_does_not_complete(self, monkeypatch):
        _no_verdict(monkeypatch)

        result = prepare_profile_picture(
            "me.png", "image/png", _image(), _settings(media_vision_model="llava")
        )

        assert result.accepted is True
        assert result.vision_checked is False
        assert "did not complete" in result.vision_skipped_reason

    def test_no_verdict_never_reports_itself_as_a_completed_check(self, monkeypatch):
        """The disclosure is the whole point: a caller must be able to tell
        "approved" from "not looked at", or the UI will imply a review that
        never happened."""
        _no_verdict(monkeypatch)

        result = prepare_profile_picture(
            "me.png", "image/png", _image(), _settings(media_vision_model="llava")
        )

        assert result.vision_checked is False

    def test_a_missing_verdict_cannot_rescue_a_deterministic_rejection(self, monkeypatch):
        """Fail-open applies only to the suitability judgement. A file the
        gates refused is still refused when vision is unavailable."""
        _no_verdict(monkeypatch)

        result = prepare_profile_picture("me.gif", "image/gif", _image(), _settings())

        assert result.accepted is False


class TestTheModelsReplyIsParsedNotTrusted:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ('{"suitable": true, "reason": ""}', (True, "")),
            ('{"suitable": false, "reason": "no face"}', (False, "no face")),
            ('```json\n{"suitable": true, "reason": ""}\n```', (True, "")),
            ('Sure! {"suitable": false, "reason": "too dark"}', (False, "too dark")),
        ],
    )
    def test_a_usable_reply_is_read(self, raw, expected):
        assert pp._parse_vision_verdict(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "yes, looks good",
            "{}",
            '{"reason": "no face"}',
            '{"suitable": "yes"}',
            '{"suitable": 1}',
            '{"suitable": null}',
            "{not json at all}",
        ],
    )
    def test_an_unusable_reply_is_no_verdict_rather_than_approval(self, raw):
        """`None` means "not checked". Reading an unparseable reply as
        approval would make the model's *failure* a way past the check --
        the model is never the boundary (CLAUDE.md 4.3)."""
        assert pp._parse_vision_verdict(raw) is None

    def test_a_model_reason_is_bounded_and_stripped_of_markup(self):
        """It is rendered to a person, so it is untrusted content: no
        markup characters, no newlines, and a hard length cap."""
        raw = '{"suitable": false, "reason": "' + "<script>x</script> " * 40 + '"}'

        verdict = pp._parse_vision_verdict(raw)

        assert verdict is not None
        _suitable, reason = verdict
        assert len(reason) <= pp._MAX_MODEL_REASON_CHARS + 3
        for char in "<>{}[]`\\|":
            assert char not in reason

    def test_a_non_string_reason_does_not_break_the_verdict(self):
        assert pp._parse_vision_verdict('{"suitable": false, "reason": 7}') == (False, "")


class TestTheSizeLimitReadsAsALimit:
    @pytest.mark.parametrize(
        ("limit", "expected"),
        [
            (5 * 1024 * 1024, "5 MB"),
            (10 * 1024 * 1024, "10 MB"),
            (2621440, "2.5 MB"),
            (1024 * 1024, "1 MB"),
            (512 * 1024, "512 KB"),
            (1024, "1 KB"),
        ],
    )
    def test_it_is_stated_in_a_unit_a_person_can_act_on(self, limit, expected):
        """Integer MB division alone rendered a sub-megabyte cap as "0 MB",
        which is not a limit anybody can do anything about."""
        assert pp._format_limit(limit) == expected

    def test_the_refusal_names_the_configured_limit(self):
        cap = 512 * 1024
        result = prepare_profile_picture(
            "me.png", "image/png", _large_image(cap + 1), _settings(profile_picture_max_bytes=cap)
        )

        assert result.accepted is False
        assert "512 KB" in result.reason
