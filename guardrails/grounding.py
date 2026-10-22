"""Grounding: is this answer actually supported by the evidence retrieved?

**What this can and cannot do, stated plainly.** This is not a
hallucination detector. Nothing in a local, deterministic check can know
whether a fluent sentence is true. What it *can* check is a set of
necessary conditions, each of which is a real failure when absent:

  - Evidence exists at all (retrieval returned something).
  - That evidence cleared the configured confidence floor.
  - The answer carries at least one citation, when citations are
    required.
  - Every citation points at a source actually retrieved for this
    question -- a fabricated reference is the one hallucination a
    deterministic check genuinely *can* catch.
  - The answer overlaps the evidence lexically enough that it is
    plausibly derived from it rather than from the model's own
    parametric memory.

Failing any of these means the answer is **unverifiable**, which is a
different and weaker claim than "false". `GroundingVerdict.reason` says
which condition failed, and the caller-facing text says "I could not
verify" rather than "that is wrong" (CLAUDE.md 4.4: never present
inference as observed fact -- including inference about an answer's
truth).

**The overlap check is deliberately weak.** Lexical overlap is a crude
proxy and a high-overlap answer can still be wrong. It is set to catch
the blatant case -- an answer with no words in common with any retrieved
chunk, which is what a model answering from parametric memory produces --
and the threshold is low so that ordinary paraphrase is never penalised.
Over-blocking correct answers is its own failure mode, exactly as
CLAUDE.md 5.8 says of over-blocking ordinary analytics.

**Provenance, not a score.** A verdict maps onto
`agent.provenance.DataTruthLevel` rather than inventing a parallel
vocabulary: a grounded retrieval answer is evidence-backed, an
ungrounded one is `AI_INFERENCE`, and nothing here ever produces
`CONFIRMED_BUSINESS_TRUTH` -- only an explicit human action does
(CLAUDE.md 4.4).

**SQL answers are not assessed here.** A SQL result's grounding is
established by something far stronger than text overlap: the rows came
from the database, through a validated `SELECT`, and
`agent.provenance.DataTruthLevel.DATABASE_FACT` already says so. Running
a lexical check over a numeric result would be noise at best and would
refuse correct answers at worst. `assess_grounding` is for
retrieval-backed prose.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from guardrails.config import GuardrailConfig

logger = logging.getLogger(__name__)

#: Fraction of the answer's own distinctive words that must also appear
#: in the retrieved evidence. Low on purpose -- see the module docstring
#: on why over-blocking is the worse error here. At 0.15 an answer that
#: paraphrases freely still passes, while one sharing almost no
#: vocabulary with its own evidence does not.
MIN_LEXICAL_OVERLAP = 0.15

#: Words ignored when computing overlap: they appear in every answer and
#: every document, so counting them would inflate every score toward
#: passing and make the check vacuous.
_STOPWORDS = frozenset(
    """a an and are as at be been but by for from had has have how i if in into is it its
    may not of on or that the their then there these this to was were what when where
    which who will with would you your can could do does should about also more most such
    than them they we us our""".split()
)

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{2,}")

#: Below this many distinctive words, an answer is too short for lexical
#: overlap to mean anything and is never penalised.
#:
#: "Yes.", "It is." and "Approximately 25." each carry one or two
#: distinctive tokens, so an empty-token rule does not cover them -- and
#: scoring them 0.0 would refuse every legitimate short answer, including
#: the direct answer to a yes/no question. A short answer makes too few
#: claims for this check to have an opinion about, which is different
#: from making a well-supported one: the verdict still reports the reason,
#: so a reader is never told a one-word answer was *verified*.
_MIN_ASSESSABLE_TOKENS = 3


class GroundingReason(StrEnum):
    """Why an answer was or was not considered grounded.

    A named reason rather than a boolean, because the remedies differ: no
    evidence means "configure or ingest the data", a missing citation
    means "the model did not cite", and a fabricated citation means "the
    model invented a source" -- which is the one that deserves an alert.
    """

    #: Evidence present, cited, and lexically consistent.
    GROUNDED = "grounded"
    #: Grounding was not required, so nothing was asserted either way.
    NOT_REQUIRED = "not_required"
    #: Retrieval returned nothing to ground against.
    NO_EVIDENCE = "no_evidence"
    #: Evidence existed but every source scored below the floor.
    BELOW_CONFIDENCE_FLOOR = "below_confidence_floor"
    #: Citations are required and the answer carries none.
    MISSING_CITATIONS = "missing_citations"
    #: A citation names a source that was not retrieved for this
    #: question. The clearest deterministically-detectable fabrication.
    FABRICATED_CITATION = "fabricated_citation"
    #: The answer shares almost no vocabulary with its own evidence.
    LOW_EVIDENCE_OVERLAP = "low_evidence_overlap"
    #: The source itself reported it had nothing useful.
    SOURCE_REPORTED_INSUFFICIENT = "source_reported_insufficient"


@dataclass(frozen=True)
class GroundingVerdict:
    """Whether an answer may be presented, and how it must be labelled.

    Attributes:
        grounded: Whether every necessary condition held.
        reason: Which condition decided it.
        must_refuse: Whether the answer has to be replaced with an
            "I cannot verify this" response. True only when grounding was
            *required* and failed -- a failed check under
            `SOURCE_GROUNDING_REQUIRED=false` is recorded and labelled,
            not suppressed.
        truth_level: The `agent.provenance.DataTruthLevel` value this
            answer must carry. Never upgraded by this module.
        citations_checked / citations_valid: Counts, for the audit
            record. A gap between them is a fabrication signal.
        evidence_overlap: The measured lexical overlap, 0..1, or `None`
            when it was not computed (no evidence, or already refused).
            Reported for tuning; never shown to a caller as a confidence
            score, because it is not one.
        detail: Log-safe explanation. Counts and reasons, never the
            answer or the evidence text.
    """

    grounded: bool
    reason: GroundingReason
    must_refuse: bool = False
    truth_level: str = "ai_inference"
    citations_checked: int = 0
    citations_valid: int = 0
    evidence_overlap: float | None = None
    detail: str = ""


def _tokens(text: str) -> set[str]:
    """Distinctive lowercase word stems in `text`, stopwords removed."""
    return {word.lower() for word in _WORD_RE.findall(text) if word.lower() not in _STOPWORDS}


def lexical_overlap(answer: str, evidence: str) -> float:
    """Fraction of the answer's distinctive words that appear in `evidence`.

    Returns 1.0 for an answer with fewer than `_MIN_ASSESSABLE_TOKENS`
    distinctive words (a bare "Yes.", "Approximately 25.", or a refusal).
    That is the safe direction: such an answer makes too few claims for
    overlap to mean anything, and scoring it 0.0 would refuse every
    legitimate short answer -- including the direct answer to a yes/no
    question.
    """
    answer_tokens = _tokens(answer)
    if len(answer_tokens) < _MIN_ASSESSABLE_TOKENS:
        return 1.0
    evidence_tokens = _tokens(evidence)
    if not evidence_tokens:
        return 0.0
    return len(answer_tokens & evidence_tokens) / len(answer_tokens)


def _citation_identifiers(citations: list[Any]) -> list[str]:
    """Source identifiers from a citation list, tolerating both shapes.

    `rag.graph.Citation` is a `TypedDict` in the RAG path while some
    callers hold plain objects; accepting either keeps this usable from
    both without forcing a conversion at every call site.
    """
    identifiers: list[str] = []
    for citation in citations:
        # `filename` first: that is `rag.graph.Citation`'s own field and
        # the shape this codebase actually produces. The rest are
        # tolerated so a future source with a different citation shape
        # does not silently report zero citations and trip the
        # missing-citations refusal.
        if isinstance(citation, dict):
            value = (
                citation.get("filename")
                or citation.get("source")
                or citation.get("document_name")
                or citation.get("title")
                or citation.get("id")
            )
        else:
            value = (
                getattr(citation, "filename", None)
                or getattr(citation, "source", None)
                or getattr(citation, "document_name", None)
                or getattr(citation, "title", None)
                or getattr(citation, "id", None)
            )
        if value:
            identifiers.append(str(value))
    return identifiers


def assess_grounding(
    answer: str,
    *,
    evidence_texts: list[str],
    citations: list[Any],
    retrieved_sources: list[str],
    config: GuardrailConfig,
    source_confidence: float | None = None,
    source_status: str | None = None,
) -> GroundingVerdict:
    """Decide whether `answer` is supported by the evidence behind it.

    Args:
        answer: The composed prose answer. Treated as untrusted model
            output, never as instructions.
        evidence_texts: The retrieved chunk texts the answer was built
            from. Truncated to `config.max_retrieval_chunks` before
            comparison, so the check measures the evidence the model was
            actually given rather than everything retrieval could find.
        citations: Whatever the source produced, in either supported
            shape (see `_citation_identifiers`).
        retrieved_sources: Identifiers of the sources genuinely retrieved
            for this question. A citation outside this list is a
            fabrication -- this is the list that makes that detectable.
        config: The resolved guardrail configuration.
        source_confidence: The retrieval confidence for this source, when
            known. Compared against `MIN_SOURCE_CONFIDENCE`.
        source_status: The source's own status, e.g.
            `"insufficient_information"`. A source reporting it found
            nothing is authoritative about that and short-circuits every
            check below -- no amount of lexical overlap makes an answer
            grounded in evidence the source says it does not have.

    Returns:
        A `GroundingVerdict`. `must_refuse` is only ever True when
        `SOURCE_GROUNDING_REQUIRED` is on: with it off, a failed check is
        reported and the answer labelled `AI_INFERENCE`, which is the
        honest middle ground between suppressing it and presenting it as
        verified.
    """
    required = config.grounding_required
    grounding_checked = config.hallucination or required

    if not grounding_checked:
        return GroundingVerdict(
            grounded=False,
            reason=GroundingReason.NOT_REQUIRED,
            truth_level="ai_inference",
            detail="grounding checks are disabled (HALLUCINATION_CHECK_ENABLED=false)",
        )

    def _fail(reason: GroundingReason, detail: str, **extra: Any) -> GroundingVerdict:
        return GroundingVerdict(
            grounded=False,
            reason=reason,
            must_refuse=required,
            truth_level="ai_inference",
            detail=detail,
            **extra,
        )

    if source_status == "insufficient_information":
        return _fail(
            GroundingReason.SOURCE_REPORTED_INSUFFICIENT,
            "the source reported it found no relevant information",
        )

    chunks = [text for text in evidence_texts if text][: config.max_retrieval_chunks]
    if not chunks:
        # `ALLOW_GENERAL_KNOWLEDGE` is the one switch that lets an answer
        # stand with no evidence at all. Off by default: on a platform
        # whose purpose is answering from *this* deployment's data, an
        # unsourced answer that looks like a sourced one is the failure
        # that discredits every other answer.
        if config.allow_general_knowledge:
            return GroundingVerdict(
                grounded=False,
                reason=GroundingReason.NO_EVIDENCE,
                must_refuse=False,
                truth_level="ai_inference",
                detail="no retrieved evidence; ALLOW_GENERAL_KNOWLEDGE permits this answer",
            )
        return _fail(GroundingReason.NO_EVIDENCE, "retrieval returned no evidence")

    if source_confidence is not None and source_confidence < config.min_source_confidence:
        return _fail(
            GroundingReason.BELOW_CONFIDENCE_FLOOR,
            f"source confidence {source_confidence:.2f} is below the configured "
            f"floor {config.min_source_confidence:.2f}",
        )

    identifiers = _citation_identifiers(citations)
    if config.require_citations and not identifiers:
        return _fail(
            GroundingReason.MISSING_CITATIONS,
            "REQUIRE_CITATIONS is on and the answer carries no citation",
            citations_checked=0,
            citations_valid=0,
        )

    if identifiers:
        # Compared case-insensitively on the trimmed value: a citation
        # differing from the retrieved name only by case or padding is a
        # formatting artefact, and calling that a fabrication would raise
        # a serious alert for a whitespace difference.
        retrieved = {s.strip().casefold() for s in retrieved_sources if s}
        valid = [i for i in identifiers if i.strip().casefold() in retrieved]
        if retrieved and len(valid) != len(identifiers):
            fabricated = len(identifiers) - len(valid)
            logger.warning(
                "[grounding] %d of %d citation(s) name a source that was not retrieved "
                "for this question -- treating the answer as unverifiable",
                fabricated,
                len(identifiers),
            )
            return _fail(
                GroundingReason.FABRICATED_CITATION,
                f"{fabricated} of {len(identifiers)} citation(s) name an unretrieved source",
                citations_checked=len(identifiers),
                citations_valid=len(valid),
            )

    overlap = lexical_overlap(answer, "\n".join(chunks))
    if overlap < MIN_LEXICAL_OVERLAP:
        return _fail(
            GroundingReason.LOW_EVIDENCE_OVERLAP,
            f"answer/evidence lexical overlap {overlap:.2f} is below " f"{MIN_LEXICAL_OVERLAP:.2f}",
            citations_checked=len(identifiers),
            citations_valid=len(identifiers),
            evidence_overlap=round(overlap, 3),
        )

    return GroundingVerdict(
        grounded=True,
        reason=GroundingReason.GROUNDED,
        must_refuse=False,
        # Still not DATABASE_FACT: a retrieval-backed prose answer is a
        # model's rendering of real evidence, which is a genuinely
        # different claim from a value read out of a table. Only the SQL
        # path produces DATABASE_FACT, and only a human action produces
        # CONFIRMED_BUSINESS_TRUTH (CLAUDE.md 4.4).
        truth_level="ai_inference",
        citations_checked=len(identifiers),
        citations_valid=len(identifiers),
        evidence_overlap=round(overlap, 3),
        detail=(
            f"grounded in {len(chunks)} chunk(s) with {len(identifiers)} citation(s), "
            f"overlap {overlap:.2f}"
        ),
    )


__all__ = [
    "MIN_LEXICAL_OVERLAP",
    "GroundingReason",
    "GroundingVerdict",
    "assess_grounding",
    "lexical_overlap",
]
