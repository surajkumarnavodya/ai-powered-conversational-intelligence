"""Makes model-authored SVG safe to render, or refuses it.

SVG is markup, not a picture. A browser asked to render it as a *document*
will run `<script>`, fire `onload=`, follow `<use href="http://...">`, and
fetch whatever `<image href>` points at. So an SVG this app generated from
an LLM's output -- which is to say, from text derived from a user's prompt
-- is untrusted content in the most literal sense, and
`media_gen/download.py` already records this project's position on it:
serving `image/svg+xml` under this app's own origin is a
stored-content-confusion/XSS vector.

**Two independent controls, and both are required.**

1. *This sanitiser* removes every executable and network-reaching
   construct, and fails closed on anything it cannot parse.
2. *The render context* must be a non-executing one. Sanitised SVG is
   returned as markup for the frontend to render through
   `<img src="data:image/svg+xml;base64,...">`, because an SVG loaded via
   `<img>` is rendered in a restricted mode where scripts never run and
   external references are not fetched -- a browser-enforced guarantee
   that does not depend on this file being perfect. It is deliberately
   **not** served from `GET /media/{id}` with an `image/svg+xml`
   content type, which would be exactly the vector
   `media_gen/download.py`'s allowlist comment warns about.

A note on that allowlist, found while writing this: it is prefix-based
(`_ALLOWED_CONTENT_TYPE_PREFIXES = ("image/", ...)`), so `image/svg+xml`
would in fact *pass* it despite the comment naming SVG as the risk. That
gap is in the downloaded-provider-media path, not this one, and is left
for its own change rather than widened here -- but it is the reason this
module does not rely on that allowlist for anything.

**Allowlist, never blocklist.** Elements and attributes not named here are
dropped. A blocklist of known-bad tags is the wrong shape for markup: SVG
has a large surface, browsers accept malformed input generously, and
`<foreignObject>` alone can embed arbitrary HTML. The same reasoning
`agent/sql_validator.py` applies to SQL -- an allowlist of what is
permitted, not a list of what is forbidden.
"""

from __future__ import annotations

import logging
import re
from xml.etree import ElementTree  # nosec B405 - justified at the parse site

logger = logging.getLogger(__name__)

_SVG_NS = "http://www.w3.org/2000/svg"

#: Drawing primitives, shapes, text and grouping. Nothing that scripts,
#: embeds, loads, or animates via external reference.
#:
#: `foreignObject` is absent on purpose -- it embeds arbitrary HTML inside
#: SVG and would reopen everything below. `image` is absent because its
#: whole purpose is to reference external bytes. `a` is absent because a
#: generated illustration has no business carrying links.
ALLOWED_ELEMENTS: frozenset[str] = frozenset(
    {
        "svg",
        "g",
        "defs",
        "title",
        "desc",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "text",
        "tspan",
        "marker",
        "linearGradient",
        "radialGradient",
        "stop",
        "clipPath",
        "pattern",
        "symbol",
        "use",
    }
)

#: Geometry, styling and layout only. No `on*` handler can appear here
#: because none is listed, and the check below is an allowlist -- so a
#: novel handler name invented by a future browser is excluded by default
#: rather than needing to be added to a blocklist.
ALLOWED_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "viewBox",
        "width",
        "height",
        "x",
        "y",
        "x1",
        "y1",
        "x2",
        "y2",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "d",
        "points",
        "fill",
        "fill-opacity",
        "fill-rule",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-dasharray",
        "stroke-opacity",
        "opacity",
        "transform",
        "font-family",
        "font-size",
        "font-weight",
        "font-style",
        "text-anchor",
        "dominant-baseline",
        "letter-spacing",
        "offset",
        "stop-color",
        "stop-opacity",
        "gradientUnits",
        "gradientTransform",
        "patternUnits",
        "clip-path",
        "clip-rule",
        "id",
        "class",
        "xmlns",
        "preserveAspectRatio",
        "markerWidth",
        "markerHeight",
        "href",
        "refX",
        "refY",
        "orient",
    }
)

#: `style` is allowed but filtered: it is the one attribute whose *value*
#: can still reach the network or execute, via `url(...)`, `@import`, or
#: the legacy `expression(...)`.
_STYLE_ATTRIBUTE = "style"
_DANGEROUS_STYLE = re.compile(r"url\s*\(|expression\s*\(|@import|javascript:", re.IGNORECASE)

#: Only a same-document fragment reference is allowed on `use`/`clip-path`
#: etc. Anything with a scheme or a path reaches outside the document.
_SAFE_LOCAL_REFERENCE = re.compile(r"^#[A-Za-z_][\w.:-]*$")
_REFERENCE_ATTRIBUTES = frozenset(
    {"clip-path", "fill", "stroke", "href", "{http://www.w3.org/1999/xlink}href"}
)

#: A generated illustration has no legitimate reason to be enormous, and an
#: unbounded one is a denial-of-service shaped problem for whatever renders
#: it. Generous enough for a detailed diagram.
MAX_SVG_BYTES = 256 * 1024

#: Likewise for nesting -- deep recursion is a parser problem before it is
#: a rendering one.
MAX_DEPTH = 32


class SvgRejected(ValueError):
    """The SVG could not be made safe and must not be rendered.

    Raised rather than returning a partially-cleaned document: a caller
    that got back "something" would render it, and the whole point here is
    that unparseable or structurally wrong markup is refused, not
    best-efforted.
    """


def _local_name(tag: str) -> str:
    """`{namespace}tag` -> `tag`. ElementTree reports namespaced names this
    way, and the allowlist is written in plain local names."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _clean_attributes(element: ElementTree.Element) -> None:
    """Drops every attribute not on the allowlist, in place."""
    for name in list(element.attrib):
        local = _local_name(name)

        if local == _STYLE_ATTRIBUTE:
            if _DANGEROUS_STYLE.search(element.attrib[name] or ""):
                del element.attrib[name]
            continue

        if local not in ALLOWED_ATTRIBUTES:
            # Catches `onload`, `onclick`, `xlink:href`, `externalResourcesRequired`
            # and anything a future spec adds, because it is an allowlist.
            del element.attrib[name]
            continue

        if local in _REFERENCE_ATTRIBUTES:
            value = (element.attrib[name] or "").strip()
            # A reference that is not a bare same-document fragment can
            # fetch bytes from elsewhere. `fill`/`stroke` are in this set
            # because `fill="url(http://...)"` is a real pattern.
            if (
                value.startswith("url(")
                or "://" in value
                or value.startswith("//")
                or name in ("href", "{http://www.w3.org/1999/xlink}href")
                and not (_SAFE_LOCAL_REFERENCE.match(value))
            ):
                del element.attrib[name]


def _strip(element: ElementTree.Element, depth: int = 0) -> None:
    """Recursively removes disallowed children and attributes, in place."""
    if depth > MAX_DEPTH:
        raise SvgRejected(f"SVG nests deeper than {MAX_DEPTH} levels")

    _clean_attributes(element)

    for child in list(element):
        if _local_name(child.tag) not in ALLOWED_ELEMENTS:
            # Removed whole, not unwrapped: a `<script>`'s *text* is the
            # payload, so keeping its children would keep the payload.
            element.remove(child)
            continue
        _strip(child, depth + 1)


def sanitize_svg(markup: str) -> str:
    """Returns safe SVG markup, or raises `SvgRejected`.

    Args:
        markup: Raw SVG text, as produced by a model. Untrusted.

    Returns:
        Serialized SVG containing only allowlisted elements and
        attributes. Always re-serialized from the parsed tree rather than
        string-edited, so anything the parser did not understand cannot
        survive into the output.

    Raises:
        SvgRejected: empty, oversized, unparseable, or not an `<svg>` root.
    """
    if not markup or not markup.strip():
        raise SvgRejected("empty SVG")
    encoded = markup.encode("utf-8", errors="replace")
    if len(encoded) > MAX_SVG_BYTES:
        raise SvgRejected(f"SVG is larger than {MAX_SVG_BYTES} bytes")

    # A doctype is how XXE and entity-expansion attacks arrive. ElementTree
    # does not expand external entities, but refusing the construct outright
    # is cheaper than reasoning about which parser is in use.
    if "<!DOCTYPE" in markup.upper() or "<!ENTITY" in markup.upper():
        raise SvgRejected("SVG carries a DOCTYPE or ENTITY declaration")

    # bandit B314 (`xml.etree.ElementTree.fromstring` on untrusted input).
    # Kept, rather than taking a `defusedxml` dependency, because every
    # attack in that advisory class is already refused *before* this line
    # and each refusal is pinned by a test in
    # `tests/test_media_gen_svg_sanitize.py`:
    #   * entity expansion (billion laughs) and XXE both require a DTD,
    #     and the `<!DOCTYPE`/`<!ENTITY` guard above rejects the construct
    #     outright -- there is no valid XML spelling of either that gets
    #     past it, since XML permits no whitespace inside `<!DOCTYPE`.
    #   * quadratic blowup likewise needs an `<!ENTITY` declaration.
    #   * entity *references* without a declaration are a `ParseError`
    #     ("undefined entity"), handled below as a clean refusal.
    #   * unbounded input and deep nesting are capped by `MAX_SVG_BYTES`
    #     and `MAX_DEPTH` (the latter enforced in `_strip`, which is what
    #     keeps a deeply-nested document from reaching recursive
    #     serialization).
    # So this is a scoped, per-line suppression of a verified-benign
    # finding, not a threshold change: a new unsafe parse anywhere else
    # still fails the gate.
    try:
        root = ElementTree.fromstring(encoded)  # nosec B314
    except ElementTree.ParseError as exc:
        raise SvgRejected(f"SVG did not parse: {exc}") from exc

    if _local_name(root.tag) != "svg":
        raise SvgRejected(f"root element is <{_local_name(root.tag)}>, not <svg>")

    _strip(root)

    # The namespace is required for an `<img>` to render the result at
    # all. When the input was namespaced, ElementTree emits it from the
    # parsed tag and setting it by hand as well produces a document with
    # two `xmlns` attributes; when the input was not, nothing emits it and
    # it has to be added. So: register the default prefix, serialize, and
    # only patch it in if it is genuinely missing.
    root.attrib.pop("xmlns", None)
    ElementTree.register_namespace("", _SVG_NS)
    cleaned = ElementTree.tostring(root, encoding="unicode")
    if "xmlns=" not in cleaned:
        cleaned = cleaned.replace("<svg", f'<svg xmlns="{_SVG_NS}"', 1)
    logger.debug("[svg] sanitized %d bytes -> %d", len(markup), len(cleaned))
    return cleaned


def extract_svg(text: str) -> str:
    """Pulls the `<svg>...</svg>` span out of a model reply.

    A local model reliably wraps markup in a ``` fence or prefaces it with
    a sentence, the same two habits
    `agent.orchestrator.router_contract.parse_router_decision` tolerates
    for JSON. Takes the outermost span rather than trusting the whole reply.

    Raises:
        SvgRejected: no `<svg>` element in the reply at all.
    """
    match = re.search(r"<svg\b.*?</svg\s*>", text or "", re.DOTALL | re.IGNORECASE)
    if match is None:
        raise SvgRejected("the model's reply contained no <svg> element")
    return match.group(0)


__all__ = [
    "ALLOWED_ATTRIBUTES",
    "ALLOWED_ELEMENTS",
    "MAX_DEPTH",
    "MAX_SVG_BYTES",
    "SvgRejected",
    "extract_svg",
    "sanitize_svg",
]
