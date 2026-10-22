"""Deterministic source scoring: what the question itself says, before any model.

**The gap this closes.** `router_node` asks the LLM which sources a
question needs (`classify_sources`), then filters the answer through a
deterministic permission check. That ordering is already correct -- the
model proposes, a server-side check decides (CLAUDE.md 4.3) -- but the
*proposal* had no deterministic component at all except one narrow
attachment pre-check. Three consequences, all observed in this repo's own
history:

  1. Which sources a question reached depended on how well the configured
     local model followed a prompt, and that varies by model
     (`router_node`'s own comments record `llama3.1:8b` and
     `qwen2.5-coder:7b` disagreeing on the same question).
  2. Every routed question cost a classification call, including ones
     whose destination is unambiguous ("extract the text from this
     image").
  3. There was no confidence number anywhere, so "the router is sure" and
     "the router guessed" were indistinguishable to the caller, the UI and
     the logs.

This module supplies the missing deterministic signal and a confidence
score per source. It does **not** replace `classify_sources`: a question
with no strong signal still goes to the model, because keyword matching is
genuinely worse than a language model at open-ended phrasing. What it
replaces is *guessing when the question was not ambiguous*.

**Confidence is evidence, not a probability.** Each score is the bounded
sum of the signals that actually matched, normalised to 0..1. It is not
calibrated against any ground truth and this module never claims it is --
presenting it as a likelihood would be exactly the "inference as observed
fact" failure CLAUDE.md 4.4 prohibits. `SourceSignal.matched` names the
signals behind every score, so a number can always be traced to the words
that produced it.

**Scoring never grants access.** The output is a ranking over the sources
`router_node` already established as available *and* the caller is
authorized for -- the permission filter runs afterwards and is unchanged.
A high confidence for `policy` on a caller without `POLICY_RAG_QUERY`
changes nothing: the source is still dropped. Scoring can narrow a route
or skip a model call; it can never widen one.

**Source metadata has one owner.** The per-source signal families below
are declared here, but the source's *name*, its availability rule and its
permission all still come from `sources/descriptors.py` (CLAUDE.md 4.8).
This module takes the available names as a parameter and never recomputes
them.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: A matched signal's weight. Three bands, because a finer scale would
#: imply a precision this scoring does not have:
#:
#:   STRONG  -- phrasing that names the destination almost unambiguously
#:              ("extract the text from this image", "search the web").
#:   MEDIUM  -- vocabulary strongly associated with one source but usable
#:              elsewhere ("revenue", "policy", "document").
#:   WEAK    -- a general hint that only matters in the absence of
#:              anything stronger ("show me", "list").
_STRONG = 0.6
_MEDIUM = 0.3
_WEAK = 0.1

#: Confidence at or above this is treated as "the question named its own
#: destination", which is what licenses skipping the classification call.
#: Deliberately high: a wrong skip routes a question to the wrong place
#: with no model to catch it, while a wrong *non*-skip only costs one
#: LLM call. Configurable through `SOURCE_CONFIDENCE_THRESHOLD`; this is
#: the fallback when no setting is supplied.
DEFAULT_CONFIDENCE_THRESHOLD = 0.7

#: How close a runner-up may come to the leader before the read counts as
#: *ambiguous* rather than confident, as a fraction of the leader's score.
#:
#: A leader over the threshold is not enough on its own. "Summarize this
#: file and compare it with our database records" scores `attachments`
#: conclusively while also carrying real `sql` evidence -- it is a
#: genuinely two-source question, and acting on the leader alone would
#: silently drop the other half of what was asked. A language model is
#: better than keyword matching at exactly that case, so a close
#: runner-up defers to `classify_sources` instead.
#:
#: Relative rather than absolute on purpose: 0.3 against a leader of 0.9
#: is a weak aside, while the same 0.3 against a leader of 0.5 is a real
#: competitor, and one fixed floor cannot tell those apart.
_AMBIGUITY_RATIO = 0.5


def _compile(*patterns: str) -> tuple[re.Pattern[str], ...]:
    """Case-insensitive compiled patterns, built once at import time."""
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


#: Per-source signal families, as (weight, patterns, label) triples.
#:
#: Word boundaries are used throughout rather than bare substrings: a
#: question about "a sumptuous dinner" must not match "sum", and
#: "database" must not be found inside an unrelated longer word. That
#: mistake is the usual reason keyword routers get a bad reputation.
_SIGNALS: dict[str, tuple[tuple[float, tuple[re.Pattern[str], ...], str], ...]] = {
    "sql": (
        (
            _STRONG,
            _compile(
                r"\b(top|bottom)\s+\d+\b",
                r"\bgroup(ed)?\s+by\b",
                r"\b(sum|count|average|avg|median|total)\s+(of|per|by)\b",
                r"\b(year[- ]over[- ]year|month[- ]over[- ]month|yoy|mom)\b",
                r"\b(by|per)\s+(region|month|quarter|year|department|category|product|customer)\b",
            ),
            "aggregate_or_grouping_phrasing",
        ),
        (
            _MEDIUM,
            _compile(
                r"\b(revenue|sales|profit|margin|invoice|order|orders|customer|customers)\b",
                r"\b(employee|employees|headcount|salary|salaries|payroll)\b",
                r"\b(table|tables|database|column|row|rows|record|records)\b",
                r"\b(how many|how much|trend|forecast|growth|decline)\b",
                r"\b(quarter|fiscal|ytd|mtd)\b",
            ),
            "structured_data_vocabulary",
        ),
        (
            _WEAK,
            _compile(
                r"\b(show|list|report|compare|rank|breakdown)\b",
                r"\b(highest|lowest|best|worst|average)\b",
            ),
            "reporting_verb",
        ),
    ),
    "documents": (
        (
            # `(?:\w+\s+){0,2}` allows a qualifier between the article and
            # the noun -- "the *employee* handbook", "the *expense claim*
            # guide". Without it the pattern only fired on the bare noun,
            # which is the less common way anyone actually refers to a
            # specific document. Bounded at two words so it stays a
            # noun-phrase match rather than spanning a whole clause.
            _STRONG,
            _compile(
                r"\bin\s+the\s+(?:\w+\s+){0,2}"
                r"(document|documents|pdf|pdfs|handbook|manual|guide)\b",
                r"\baccording\s+to\s+the\s+(?:\w+\s+){0,2}"
                r"(document|pdf|handbook|manual|guide|policy)\b",
                r"\bwhat\s+does\s+(the|our)\s+(?:\w+\s+){0,2}"
                r"(document|pdf|handbook|manual|guide)\s+say\b",
            ),
            "explicit_document_reference",
        ),
        (
            _MEDIUM,
            _compile(
                r"\b(document|documents|pdf|handbook|manual|guide|procedure|procedures)\b",
                r"\b(leave|holiday|benefits|onboarding|expense|reimbursement)\s+"
                r"(policy|policies|rules|process|entitlement)\b",
                r"\b(eligibility|entitled|allowance|guideline|guidelines)\b",
            ),
            "document_vocabulary",
        ),
        (
            _WEAK,
            _compile(r"\b(explain|describe|what\s+is\s+the\s+process)\b"),
            "explanatory_phrasing",
        ),
    ),
    "policy": (
        (
            _STRONG,
            _compile(
                r"\bin\s+the\s+(policy|policies)\b",
                r"\bcompany\s+policy\s+on\b",
                r"\b(compliance|regulatory)\s+(requirement|requirements|policy)\b",
            ),
            "explicit_policy_reference",
        ),
        (
            _MEDIUM,
            _compile(
                r"\b(policy|policies|compliance|regulation|regulations|statute)\b",
                r"\b(confidential|restricted|classified)\b",
            ),
            "policy_vocabulary",
        ),
    ),
    "web": (
        (
            _STRONG,
            _compile(
                r"\b(search|look)\s+(the\s+)?(web|online|internet)\b",
                r"\b(latest|current|today'?s|recent)\s+(news|price|prices|rate|rates)\b",
                r"\bwhat'?s\s+(happening|new)\s+(in|with)\b",
            ),
            "explicit_web_request",
        ),
        (
            _MEDIUM,
            _compile(
                r"\b(news|headline|headlines|industry\s+trend|market\s+(rate|price))\b",
                r"\b(competitor|competitors|benchmark)\b",
                r"\b(today|this\s+week|right\s+now|currently)\b",
            ),
            "external_currency_vocabulary",
        ),
    ),
    "attachments": (
        (
            _STRONG,
            _compile(
                r"\b(extract|read|transcribe|ocr)\b.*\b(image|photo|picture|screenshot|file|"
                r"attachment|document|pdf|scan)\b",
                r"\b(this|the|my|attached|uploaded)\s+(image|photo|picture|screenshot|file|"
                r"attachment|pdf|spreadsheet|document)\b",
                r"\bwhat('?s| is| does)\s+(in|on|this)\s+(this\s+)?"
                r"(image|photo|picture|screenshot|file|attachment|pdf)\b",
                r"\b(summari[sz]e|describe)\s+(this|the|my|attached|uploaded)\b",
            ),
            "explicit_attachment_reference",
        ),
        (
            _MEDIUM,
            _compile(
                r"\b(attached|attachment|uploaded|upload)\b",
                r"\b(image|photo|picture|screenshot|scan)\b",
            ),
            "attachment_vocabulary",
        ),
    ),
    "media_search": (
        (
            _STRONG,
            _compile(
                r"\b(find|show|search for)\s+(me\s+)?(an?\s+)?"
                r"(image|photo|picture|video|clip|footage)\b",
                r"\bin\s+(the\s+)?(media|image|video)\s+librar(y|ies)\b",
                r"\bdo\s+we\s+have\s+(an?\s+)?(image|photo|picture|video|clip)\b",
            ),
            "explicit_media_lookup",
        ),
        (
            _MEDIUM,
            _compile(r"\b(thumbnail|footage|clip|screenshot\s+librar)\b"),
            "media_vocabulary",
        ),
    ),
    "generation": (
        (
            _STRONG,
            _compile(
                r"\b(generate|create|make|draw|render|produce)\s+"
                r"(me\s+)?(an?\s+|a\s+new\s+)?(image|picture|photo|illustration|logo|"
                r"diagram|video|animation)\b",
                r"\b(image|picture|video)\s+of\b",
                r"\btext[- ]to[- ](image|video)\b",
            ),
            "explicit_generation_request",
        ),
        (
            # A second family so an unmistakable "generate an image of X"
            # clears the threshold on its own. With only the strong family
            # above, the ceiling was 0.6 -- below any sensible threshold --
            # which meant the one phrasing this source exists for could
            # never route deterministically.
            #
            # Generation is a paid action, so being *more* decisive here
            # needs the rest of the chain to hold: the caller still needs
            # `MEDIA_GENERATE`, the session expensive-source ceiling still
            # applies, and `REQUIRE_GENERATION_APPROVAL` still gates the
            # actual call. Deterministic routing changes which node runs,
            # never whether the spend is permitted.
            _MEDIUM,
            _compile(
                r"\b(generate|create|render|draw|illustrate)\b",
                r"\b(image|picture|illustration|logo|diagram|animation)\b",
            ),
            "generation_vocabulary",
        ),
    ),
}


@dataclass(frozen=True)
class SourceSignal:
    """One source's deterministic score for one question.

    Attributes:
        name: The source name, as `sources/descriptors.py` declares it.
        confidence: 0..1, the normalised sum of matched signal weights.
            Evidence, not a calibrated probability -- see the module
            docstring.
        matched: The labels of the signal families that fired, in
            declaration order. This is what makes a score explainable:
            a number with no matched labels behind it is a bug, not a
            weak match.
    """

    name: str
    confidence: float
    matched: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_evidence(self) -> bool:
        """Whether anything at all matched. Distinct from a low score:
        0.1 from a weak hint is evidence; 0.0 is none."""
        return bool(self.matched)


@dataclass(frozen=True)
class RoutingSignals:
    """The full deterministic read on one question.

    Attributes:
        scores: Every scored source, highest confidence first.
        confident: The sources at or above the configured threshold, in
            the same order. Empty when nothing was unambiguous, which is
            the signal to fall back to the model.
        threshold: The threshold actually applied, echoed so a log line
            or a test need not re-read settings to interpret the result.
    """

    scores: tuple[SourceSignal, ...]
    confident: tuple[str, ...]
    threshold: float

    def as_confidence_map(self) -> dict[str, float]:
        """`{source: confidence}`, for the response and the job record.

        Rounded to two places: the underlying number carries no more
        meaningful precision than that, and rendering 0.6000000000000001
        in a UI would imply otherwise.
        """
        return {signal.name: round(signal.confidence, 2) for signal in self.scores}

    def confidence_for(self, name: str) -> float:
        """One source's score, or 0.0 if it was not scored."""
        for signal in self.scores:
            if signal.name == name:
                return signal.confidence
        return 0.0


def _score_one(source: str, text: str) -> SourceSignal:
    """Score a single source against the question text.

    The sum is capped at 1.0 rather than normalised by the number of
    families, because a question that trips three strong signals for one
    source is not *less* confident than one that trips a single family --
    which is what dividing by the family count would say.
    """
    families = _SIGNALS.get(source, ())
    total = 0.0
    matched: list[str] = []
    for weight, patterns, label in families:
        if any(pattern.search(text) for pattern in patterns):
            total += weight
            matched.append(label)
    return SourceSignal(name=source, confidence=min(1.0, total), matched=tuple(matched))


def score_sources(
    question: str,
    available: list[str],
    *,
    has_attachments: bool = False,
    conversation_history: Sequence[object] | None = None,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> RoutingSignals:
    """Deterministically score every available source for this question.

    Args:
        question: The user's question, untouched. Treated purely as text
            to match against -- never as instructions, so a question
            saying "route this to the policy source" is matched like any
            other words and carries no authority (CLAUDE.md 5.5).
        available: Sources this question may use, already narrowed by
            `router_node` for configuration *and* the caller's own
            selection. Nothing outside this list can be scored, so
            scoring can never reach an unconfigured source.
        has_attachments: Whether the caller attached file(s) to this
            question. A real per-request fact, so it is weighted
            directly rather than inferred from the wording: an
            attachment question often does not mention the attachment.
        conversation_history: Prior turns, used only as a weak tiebreak
            (see below). Typed as a `Sequence` of opaque objects because
            this module needs nothing from them but the presence of SQL --
            and because `list` is invariant, so a real
            `list[ConversationExchange]` would not be assignable to
            `list[object]`.
        threshold: `SOURCE_CONFIDENCE_THRESHOLD`.

    Returns:
        A `RoutingSignals`. `confident` is empty whenever the question
        was ambiguous, which is the normal case and the signal to let
        the model decide.
    """
    text = question.strip()
    scored: list[SourceSignal] = []

    for source in available:
        signal = _score_one(source, text)

        if source == "attachments":
            if not has_attachments:
                # Scoring a source the request cannot reach would produce
                # a confident route to nothing.
                signal = SourceSignal(name=source, confidence=0.0)
            else:
                # The file is genuinely attached to *this* question, which
                # is itself an explicit per-request choice by the user --
                # stronger evidence than any phrasing. Note this still
                # only ranks the source; `router_node` separately forces
                # attachments into the route whenever one is attached, so
                # a low score here can never discard an attached file.
                signal = SourceSignal(
                    name=source,
                    confidence=min(1.0, signal.confidence + _STRONG),
                    matched=(*signal.matched, "file_attached_to_this_request"),
                )
        elif source == "sql" and has_attachments and not signal.has_evidence:
            # "Read this image" with a database configured is the exact
            # case `_looks_like_attachment_only_question` was written for.
            # Leaving SQL at zero here, rather than letting a weak
            # reporting verb carry it, is what stops a confident
            # attachment route from dragging the whole SQL pipeline along.
            signal = SourceSignal(name=source, confidence=0.0)

        scored.append(signal)

    # A follow-up on a turn that produced SQL is weak evidence that this
    # turn is about the same data. Weak on purpose: it must never be
    # enough to make SQL confident on its own, or every follow-up would
    # skip classification and go to the database regardless of what it
    # actually asked.
    if conversation_history and any(
        getattr(turn, "sql", None) or (isinstance(turn, dict) and turn.get("sql"))
        for turn in conversation_history
    ):
        scored = [
            (
                SourceSignal(
                    name=s.name,
                    confidence=min(1.0, s.confidence + _WEAK),
                    matched=(*s.matched, "follow_up_to_a_sql_turn"),
                )
                if s.name == "sql" and s.has_evidence
                else s
            )
            for s in scored
        ]

    scored.sort(key=lambda s: (-s.confidence, s.name))

    over_threshold = [s for s in scored if s.confidence >= threshold and s.has_evidence]
    confident: tuple[str, ...] = ()
    if over_threshold:
        leader = over_threshold[0].confidence
        runner_up = max(
            (s.confidence for s in scored if s not in over_threshold),
            default=0.0,
        )
        # A close runner-up means two sources genuinely apply, which is
        # the classifier's job rather than ours -- see `_AMBIGUITY_RATIO`.
        if runner_up < leader * _AMBIGUITY_RATIO:
            confident = tuple(s.name for s in over_threshold)

    return RoutingSignals(scores=tuple(scored), confident=confident, threshold=threshold)


def describe(signals: RoutingSignals) -> str:
    """A one-line, log-safe summary of a scoring result.

    Contains source names, scores and signal labels -- never the question
    itself, so routing decisions can be logged at INFO without logging
    what everyone asked (CLAUDE.md 5.6).
    """
    parts = [
        f"{s.name}={s.confidence:.2f}[{'+'.join(s.matched) or 'none'}]"
        for s in signals.scores
        if s.has_evidence
    ]
    return (
        f"threshold={signals.threshold:.2f} confident={list(signals.confident)} "
        f"scores={' '.join(parts) if parts else 'none'}"
    )


__all__ = [
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "RoutingSignals",
    "SourceSignal",
    "describe",
    "score_sources",
]
