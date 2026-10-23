"""Is this request a diagram, or a picture?

Decides whether a generation request should go to SVG mode (vector, drawn
by the LLM this app already runs, free and instant) or to a raster provider
(diffusion, slow and GPU- or quota-bound). The approved routing rule is:
**an explicit user choice always wins; this classifier only decides when
they did not say.**

**Deterministic, not a model call.** Same reasoning as
`agent/orchestrator/source_signals.py`, which scores source routing without
asking the LLM: this runs in front of real work, it must be reproducible
across models, and a wrong answer here is cheap to correct because the UI
offers a one-click "try the other mode". Spending an LLM call -- tens of
seconds on this hardware -- to decide which generator to use would often
cost more than the SVG generation itself.

**Biased toward raster when genuinely unsure**, because the failure modes
are not symmetrical. A photo request answered with a flat vector drawing
looks broken. A diagram request answered by a diffusion model looks
plausible but renders unreadable text, which is the one thing diffusion
models reliably cannot do -- so a mistake in that direction is at least
recognisable as the wrong tool rather than as a bad result.
"""

from __future__ import annotations

import re
from typing import Literal

GenerationShape = Literal["vector", "raster"]

#: Words that describe *structure*: something with boxes, labels and
#: relationships, where legible text is the point. SVG's home ground, and
#: precisely what diffusion models render as nonsense glyphs.
_VECTOR_SIGNALS = re.compile(
    r"\b("
    r"diagram|flow\s?chart|flowchart|org\s?chart|chart|graph|plot|"
    r"wireframe|mockup|schematic|blueprint|topology|architecture\s+diagram|"
    r"sequence\s+diagram|er\s+diagram|mind\s?map|"
    r"icon|logo|badge|glyph|pictogram|favicon|"
    r"timeline|roadmap|funnel|gantt|"
    r"infographic|legend|annotation|callout"
    r")\b",
    re.IGNORECASE,
)

#: Words that describe a *scene or subject*: something photographic or
#: painterly, with no text to get wrong. Raster's home ground, and
#: hopeless in hand-authored SVG.
_RASTER_SIGNALS = re.compile(
    r"\b("
    r"photo|photograph|photorealistic|realistic|cinematic|render|render ing|"
    r"portrait|selfie|headshot|face|person|people|man|woman|child|god|deity|"
    r"landscape|scenery|sunset|sunrise|forest|mountain|ocean|beach|city|street|"
    r"painting|watercolour|watercolor|oil\s+painting|sketch\s+of|artwork|"
    r"illustration\s+of\s+a|concept\s+art|poster|wallpaper|texture|"
    r"animal|cat|dog|bird|flower|food|car|building"
    r")\b",
    re.IGNORECASE,
)


def classify_generation_shape(prompt: str) -> tuple[GenerationShape, str]:
    """`(shape, reason)` for one generation prompt.

    The reason is shown in the UI next to the chosen mode, because the
    approved design is that the user can see which path was picked and
    switch with one click -- a silent choice they cannot inspect or
    override would be worse than no classifier.

    Args:
        prompt: The user's request text. Untrusted input, read only for
            keyword matching; nothing derived from it is executed.

    Returns:
        `("vector", reason)` for diagram-shaped work, `("raster", reason)`
        otherwise.
    """
    text = prompt or ""
    vector_hit = _VECTOR_SIGNALS.search(text)
    raster_hit = _RASTER_SIGNALS.search(text)

    # Both matched: a "diagram of a forest" or an "icon of a dog". Raster
    # wins, for the asymmetry in the module docstring -- and because the
    # subject word is usually the thing the person cares about seeing.
    if vector_hit and raster_hit:
        return "raster", (
            f"mentions both {vector_hit.group(0)!r} and {raster_hit.group(0)!r}; "
            "treated as a picture"
        )
    if vector_hit:
        return "vector", f"mentions {vector_hit.group(0)!r}, which is diagram-shaped"
    if raster_hit:
        return "raster", f"mentions {raster_hit.group(0)!r}, which is a subject to depict"
    return "raster", "no diagram keywords matched; defaulted to a picture"


__all__ = ["GenerationShape", "classify_generation_shape"]
