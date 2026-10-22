"""The router's structured output contract, and its deterministic fallback.

`agent.orchestrator.nodes.classify_sources` used to ask the model for a
comma-separated list of source names. That carried exactly one fact --
which sources -- so everything else the orchestrator wanted to know had to
be re-derived downstream or guessed at: whether the question was ambiguous
enough to be worth asking about, what to search each source *for* (the raw
question is rarely the best query for a vector store), how long an answer
should be, and what to try when the first source comes back empty.

This module owns that richer contract. It is deliberately a separate
module from `nodes.py`:

* **Two vocabularies meet here.** The routing prompt speaks the
  product-level names an operator would recognise (`sql_database`,
  `policy_kb`, `user_documents`, ...). The graph speaks the internal
  source names its subgraph nodes are registered under (`sql`, `policy`,
  `documents`, ...). Translating in one place means a future source is
  added by editing one map rather than hunting for string literals.
* **The fallback must not import the model.** `keyword_fallback` is pure
  regex over the question text, so it still works when Ollama is down,
  which is the case it exists for.

Nothing here decides anything a caller must obey. `router_node` still
applies the RBAC filter, the session cost ceiling and the
attachment-forcing rule *after* this contract is parsed -- the model may
request a source, it never grants one (CLAUDE.md 4.3, 5.2).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

#: The vocabulary the router prompt uses, mapped to the internal source
#: name its subgraph node is registered under in
#: `agent.orchestrator.graph.build_orchestrator_graph`.
#:
#: `general_knowledge` and `conversation` map to None on purpose: they are
#: real routing outcomes ("answer without retrieving anything") but they
#: are not sources, and there is no subgraph to run for them. Giving them
#: a fake source name would make every consumer special-case it anyway,
#: one layer further down.
ROUTED_SOURCE_NAMES: dict[str, str | None] = {
    "sql_database": "sql",
    "policy_kb": "policy",
    "user_documents": "documents",
    "web_search": "web",
    "media_search": "media_search",
    "image_generation": "generation",
    "video_generation": "generation",
    "general_knowledge": None,
    "conversation": None,
}

#: Routing outcomes that answer the question without running any source.
NO_RETRIEVAL_SOURCES = frozenset({"general_knowledge", "conversation"})

AnswerStyle = Literal[
    "quick_fact", "briefing", "explanation", "comparison", "data_table", "creative"
]

#: Accepted `answer_style` values, as a set, for validation and for tests
#: that need to enumerate them without importing `typing.get_args`.
ANSWER_STYLES: frozenset[str] = frozenset(
    ("quick_fact", "briefing", "explanation", "comparison", "data_table", "creative")
)


class RouterDecision(BaseModel):
    """One parsed, validated routing decision.

    Mirrors the JSON the router prompt asks for. Unknown source names are
    dropped rather than rejected: a model that invents `"crm_database"`
    has made one bad pick, and discarding the whole decision over it would
    throw away the good picks alongside it. A decision left with no
    `primary` at all *is* rejected, by the validator below -- that is the
    case the keyword fallback exists for.
    """

    primary: str
    secondary: list[str] = Field(default_factory=list)
    fallback: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_clarification: bool = False
    clarifying_question: str | None = None
    source_queries: dict[str, str] = Field(default_factory=dict)
    answer_style: str = "explanation"
    reason: str = ""

    @field_validator("primary")
    @classmethod
    def _primary_must_be_known(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ROUTED_SOURCE_NAMES:
            raise ValueError(f"unknown primary source {value!r}")
        return normalized

    @field_validator("secondary")
    @classmethod
    def _drop_unknown_secondary(cls, value: list[str]) -> list[str]:
        return [s.strip().lower() for s in value if s.strip().lower() in ROUTED_SOURCE_NAMES]

    @field_validator("fallback")
    @classmethod
    def _drop_unknown_fallback(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        return normalized if normalized in ROUTED_SOURCE_NAMES else None

    @field_validator("answer_style")
    @classmethod
    def _known_answer_style(cls, value: str) -> str:
        normalized = value.strip().lower()
        # An unrecognised style falls back to "explanation" rather than
        # failing the whole decision: the style only shapes prose length,
        # so getting it wrong is cosmetic, while discarding the route over
        # it would cost a correct source choice.
        return normalized if normalized in ANSWER_STYLES else "explanation"

    @field_validator("clarifying_question")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    def ordered_sources(self) -> list[str]:
        """Primary first, then secondary, de-duplicated, prompt vocabulary.

        Order matters downstream: `synthesis_node` leads with the primary
        source's answer, so the list it receives has to preserve which one
        that was.
        """
        return list(dict.fromkeys([self.primary, *self.secondary]))

    def internal_sources(self) -> list[str]:
        """`ordered_sources` translated to the graph's own source names,
        with the no-retrieval outcomes removed."""
        mapped = [ROUTED_SOURCE_NAMES[name] for name in self.ordered_sources()]
        return list(dict.fromkeys([name for name in mapped if name is not None]))

    def is_no_retrieval(self) -> bool:
        """True when the primary route answers without running any source."""
        return self.primary in NO_RETRIEVAL_SOURCES


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_router_decision(raw: str) -> RouterDecision | None:
    """Parses the model's reply into a `RouterDecision`, or None.

    Tolerates the two things a local model reliably does to JSON it was
    told to emit bare: wrapping it in a ``` fence, and prefixing it with a
    sentence of commentary. Both are recovered by taking the outermost
    brace-delimited span rather than by trusting the whole reply to parse.

    Returns None on anything it cannot turn into a valid decision, which
    is the caller's signal to retry once and then fall back -- never to
    guess.
    """
    if not raw or not raw.strip():
        return None

    candidate = raw.strip()
    match = _JSON_BLOCK_RE.search(candidate)
    if match:
        candidate = match.group(0)

    try:
        payload = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None

    try:
        return RouterDecision.model_validate(payload)
    except ValidationError as exc:
        # The question text is never logged here -- only the shape of the
        # failure (CLAUDE.md 5.6).
        logger.warning("[router] decision failed schema validation: %s", exc.error_count())
        return None


# --- Deterministic fallback ------------------------------------------
#
# Ordered: the first pattern that matches wins, so the more specific
# intents (create an image, find a video) are tested before the broader
# ones (anything mentioning "policy"). Each is a plain, readable regex --
# this runs when the model is unavailable, so it must not need one.

_FALLBACK_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "image_generation",
        re.compile(
            r"\b(create|generate|draw|design|make)\b[^.?!]{0,40}"
            r"\b(image|logo|poster|picture|illustration|banner|artwork)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "video_generation",
        re.compile(
            r"\b(create|generate|make|produce)\b[^.?!]{0,40}\b(video|animation|clip)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "media_search",
        re.compile(
            r"\b(show me|find)\b[^.?!]{0,30}" r"\b(pictures?|images?|photos?|videos?|footage)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "policy_kb",
        re.compile(
            r"\b(polic(y|ies)|rules?|eligib\w*|entitle\w*|allowed|permitted|"
            r"how do i (apply|claim|request)|procedure|guidelines?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "web_search",
        re.compile(
            r"\b(news|today|latest|current|headlines?|price|prices|weather|"
            r"score|scores|stock|share price)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "sql_database",
        re.compile(
            r"\b(how many|total|count|sum of|list all|top \d+|average|"
            r"by (month|region|department|year|quarter|territory)|trend|"
            r"breakdown)\b",
            re.IGNORECASE,
        ),
    ),
)

_CONVERSATION_RE = re.compile(
    r"^\s*(hi|hello|hey|thanks|thank you|good (morning|afternoon|evening)|"
    r"bye|goodbye|who are you|what can you do)\b[\s!.?]*$",
    re.IGNORECASE,
)


def keyword_fallback(question: str, table_keywords: frozenset[str] = frozenset()) -> RouterDecision:
    """A routing decision from the question text alone, no model involved.

    Used when the router's model call fails or returns unparseable JSON
    twice. `table_keywords` lets the `sql_database` rule require that the
    question actually names something in the catalog, which is the brief's
    own condition -- "how many" alone is not a database question if
    nothing in the schema matches it.

    Confidence is reported as 0.3 throughout: these are keyword matches,
    not a calibrated estimate, and over-reporting them would let a
    downstream consumer treat a guess as a judgement.
    """
    text = question or ""

    if _CONVERSATION_RE.match(text):
        return RouterDecision(
            primary="conversation",
            confidence=0.3,
            answer_style="quick_fact",
            reason="keyword fallback: greeting or small talk",
        )

    for source, pattern in _FALLBACK_RULES:
        if not pattern.search(text):
            continue
        if source == "sql_database" and table_keywords:
            lowered = text.lower()
            if not any(keyword in lowered for keyword in table_keywords):
                # Matches an aggregate shape but names nothing in the
                # catalog -- the brief's rule 4. Keep looking.
                continue
        return RouterDecision(
            primary=source,
            fallback="web_search" if source in ("media_search",) else None,
            confidence=0.3,
            answer_style=_FALLBACK_STYLES.get(source, "explanation"),
            source_queries={source: text.strip()},
            reason=f"keyword fallback matched {source}",
        )

    return RouterDecision(
        primary="general_knowledge",
        fallback="web_search",
        confidence=0.3,
        answer_style="explanation",
        reason="keyword fallback: no rule matched",
    )


_FALLBACK_STYLES: dict[str, str] = {
    "sql_database": "data_table",
    "web_search": "briefing",
    "image_generation": "creative",
    "video_generation": "creative",
    "media_search": "quick_fact",
    "policy_kb": "explanation",
}
