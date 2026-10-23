"""Free SVG generation: the diagram classifier and the LLM-backed provider.

The sanitiser has its own adversarial file (`tests/test_svg_sanitize.py`).
What is pinned here is the layer above it: which requests are diagram-shaped,
and that the provider refuses unsafe or unusable model output as a *failed
result* rather than by raising or by rendering it anyway.

Fully mocked -- `rag.llm.call_ollama` is patched, so no model call is made.
"""

from __future__ import annotations

import base64

import pytest

from config.settings import Settings
from media_gen.diagram_intent import classify_generation_shape
from media_gen.providers import ImageGenerationRequest
from media_gen.providers.svg_llm import SvgLlmImageProvider, svg_data_url

OK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 10">'
    '<rect x="1" y="1" width="8" height="8" fill="#245"/>'
    '<text x="5" y="6" font-size="3" text-anchor="middle">Q3</text></svg>'
)


def _settings(**overrides) -> Settings:
    base = {"enable_free_generation": True, "enable_svg_generation": True}
    base.update(overrides)
    return Settings(**base)


def _provider(monkeypatch, reply: str, **overrides) -> SvgLlmImageProvider:
    monkeypatch.setattr("rag.llm.call_ollama", lambda *a, **k: reply)
    return SvgLlmImageProvider(_settings(**overrides))


class TestTheDiagramClassifier:
    @pytest.mark.parametrize(
        "prompt",
        [
            "a flowchart of our onboarding process",
            "org chart for the engineering team",
            "an icon for the settings page",
            "a timeline of the project milestones",
            "wireframe of the dashboard",
            "infographic showing quarterly revenue",
        ],
    )
    def test_structure_requests_go_to_vector(self, prompt):
        shape, reason = classify_generation_shape(prompt)

        assert shape == "vector"
        assert reason

    @pytest.mark.parametrize(
        "prompt",
        [
            "a photo of a mountain at sunset",
            "portrait of a woman in a red coat",
            "create Ganpati ji image god",
            "a realistic render of a sports car",
            "watercolour painting of a forest",
            "a cat sitting on a windowsill",
        ],
    )
    def test_subject_requests_go_to_raster(self, prompt):
        """Including the reported case -- a depiction of a deity is a
        picture, not a diagram, so SVG mode must not claim it."""
        shape, _reason = classify_generation_shape(prompt)

        assert shape == "raster"

    def test_an_ambiguous_request_prefers_raster(self):
        """ "icon of a dog" names both. Raster wins: a photo answered with a
        flat drawing looks broken, which is the worse failure."""
        shape, reason = classify_generation_shape("an icon of a dog")

        assert shape == "raster"
        assert "both" in reason

    def test_an_unmatched_request_defaults_to_raster(self):
        shape, reason = classify_generation_shape("something nice please")

        assert shape == "raster"
        assert "defaulted" in reason

    def test_every_decision_carries_a_reason(self):
        """The UI shows which path was chosen and why, with a one-click
        switch -- a silent choice the user cannot inspect would be worse
        than no classifier."""
        for prompt in ("a flowchart", "a photo of a dog", "zzz"):
            _shape, reason = classify_generation_shape(prompt)
            assert reason.strip()

    def test_it_makes_no_model_call(self, monkeypatch):
        """Deterministic by design: it runs in front of real work and must
        be reproducible across models."""

        def _explode(*_a, **_k):
            raise AssertionError("the classifier must not call a model")

        monkeypatch.setattr("rag.llm.call_ollama", _explode)

        assert classify_generation_shape("a flowchart of X")[0] == "vector"


class TestTheSvgProvider:
    def test_a_clean_drawing_is_returned_as_bytes(self, monkeypatch):
        result = _provider(monkeypatch, OK_SVG).generate_image(
            ImageGenerationRequest(prompt="a flowchart")
        )

        assert result.ok is True
        assert result.origin == "bytes"
        assert result.media_type == "image/svg+xml"
        assert b"<svg" in result.data
        assert b"Q3" in result.data

    def test_a_fenced_reply_is_recovered(self, monkeypatch):
        result = _provider(monkeypatch, f"Here you go:\n```svg\n{OK_SVG}\n```").generate_image(
            ImageGenerationRequest(prompt="a chart")
        )

        assert result.ok is True

    def test_a_reply_with_no_svg_fails_cleanly(self, monkeypatch):
        result = _provider(monkeypatch, "I can't draw that, sorry.").generate_image(
            ImageGenerationRequest(prompt="a chart")
        )

        assert result.ok is False
        assert result.status == "failed"
        assert "rephrasing" in result.error

    def test_unsafe_model_output_never_reaches_the_caller(self, monkeypatch):
        """The provider sanitises; it does not pass markup through and
        hope the renderer copes."""
        hostile = (
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<script>fetch("https://evil.invalid")</script>'
            '<rect width="1" height="1"/></svg>'
        )

        result = _provider(monkeypatch, hostile).generate_image(
            ImageGenerationRequest(prompt="a chart")
        )

        assert result.ok is True, "the drawing itself is still usable once cleaned"
        assert b"script" not in result.data.lower()
        assert b"evil.invalid" not in result.data

    def test_a_model_failure_is_a_failed_result_not_an_exception(self, monkeypatch):
        def _raise(*_a, **_k):
            raise RuntimeError("ollama down")

        monkeypatch.setattr("rag.llm.call_ollama", _raise)

        result = SvgLlmImageProvider(_settings()).generate_image(
            ImageGenerationRequest(prompt="a chart")
        )

        assert result.ok is False
        assert "unavailable" in result.error
        assert "ollama down" in result.detail

    def test_the_switch_disables_it_without_calling_the_model(self, monkeypatch):
        called: list[str] = []
        monkeypatch.setattr("rag.llm.call_ollama", lambda *a, **k: called.append("x") or OK_SVG)

        result = SvgLlmImageProvider(_settings(enable_svg_generation=False)).generate_image(
            ImageGenerationRequest(prompt="a chart")
        )

        assert result.ok is False
        assert called == []

    def test_the_error_detail_never_carries_the_rejected_markup(self, monkeypatch):
        """Rejected markup is attacker-influenced text; the detail records
        the shape of the failure, not its content (CLAUDE.md 5.6)."""
        hostile = '<svg xmlns="http://www.w3.org/2000/svg"><script>SECRET_PAYLOAD</script>'

        result = _provider(monkeypatch, hostile).generate_image(
            ImageGenerationRequest(prompt="a chart")
        )

        assert "SECRET_PAYLOAD" not in (result.detail or "")
        assert "SECRET_PAYLOAD" not in (result.error or "")


class TestProviderHealth:
    def test_unavailable_when_switched_off(self):
        health = SvgLlmImageProvider(_settings(enable_svg_generation=False)).health()

        assert health.available is False
        assert "ENABLE_SVG_GENERATION" in health.reason

    def test_available_and_honest_about_what_it_is_good_for(self):
        health = SvgLlmImageProvider(_settings()).health()

        assert health.available is True
        assert "diagrams" in health.detail
        assert health.checked_remote is False


class TestTheDataUrl:
    def test_it_is_base64_and_decodes_back(self):
        """Base64 rather than percent-encoding, so a quote or angle bracket
        in a label cannot terminate the attribute it sits in."""
        url = svg_data_url(OK_SVG)

        assert url.startswith("data:image/svg+xml;base64,")
        decoded = base64.b64decode(url.split(",", 1)[1]).decode("utf-8")
        assert decoded == OK_SVG

    def test_a_label_containing_a_quote_survives_intact(self):
        tricky = '<svg xmlns="http://www.w3.org/2000/svg"><text>a"b</text></svg>'

        decoded = base64.b64decode(svg_data_url(tricky).split(",", 1)[1]).decode("utf-8")

        assert decoded == tricky
