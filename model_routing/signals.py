"""Turns signals this pipeline already computes into a `RequestProfile`.

**Adds no new classifier and makes no LLM call.** Every input is
something `agent/graph.py` or an earlier node already produced for its
own reasons:

- `agent.complexity.detect_complexity_signals` -- the regex heuristic
  `run_agent` already runs once per question to widen the retry budget
  (`state["complexity_signals"]`). Routing reads that same list rather
  than re-scanning the question, so a question's complexity means the
  same thing to both features and cannot drift between them.
- `agent.intent` -- `classify_analytical_intent_node`'s own
  classification (`state["analytical_intent"]`), including its
  `ambiguity_flags`. `intent_implies_planning` is reused verbatim rather
  than re-deriving which intents are "hard," for the same
  no-second-source-of-truth reason.

The resulting `complexity_score` is an ordinal count of independent
hardness signals, not a probability or a calibrated difficulty estimate
-- naming it a "score" is already generous, and nothing downstream
treats it as more than a threshold input.
"""

from __future__ import annotations

from agent.complexity import detect_complexity_signals
from agent.intent import intent_implies_planning
from model_routing.models import RequestProfile


def assess_request(
    question: str,
    complexity_signals: list[str] | tuple[str, ...] | None = None,
    analytical_intent: dict | None = None,
    *,
    latency_sensitive: bool = False,
) -> RequestProfile:
    """Builds this request's routing profile.

    Args:
        question: The user's question. Only used when
            `complexity_signals` is not supplied -- inside the live graph
            it always is (`state["complexity_signals"]`, computed once by
            `run_agent`), so the regex scan does not run twice per
            request. The fallback exists for a direct caller (the
            `POST /model-routing/preview` route, a test) that has a
            question but no precomputed signals.
        complexity_signals: `agent.complexity.detect_complexity_signals`'s
            output for this question, if already computed.
        analytical_intent: `state["analytical_intent"]` --
            `agent.intent.AnalyticalIntentClassification.model_dump()`'s
            shape, or `None` when intent classification is off, failed,
            or has not run yet (every call before
            `classify_analytical_intent_node`, which includes
            `sql_generation` on a question whose intent was never
            classified). `None` simply contributes nothing; it is never
            treated as "simple".
        latency_sensitive: See `RequestProfile.latency_sensitive`.

    Returns:
        A frozen `RequestProfile`. The score is
        `len(complexity_signals) + 1 if the intent implies multi-step
        planning + 1 if the intent carried any ambiguity flag` -- so a
        plain lookup scores 0 and a genuinely layered question scores 3+.
    """
    signals = tuple(
        complexity_signals
        if complexity_signals is not None
        else detect_complexity_signals(question)
    )
    implies_planning = bool(analytical_intent) and intent_implies_planning(analytical_intent or {})
    has_ambiguity = bool((analytical_intent or {}).get("ambiguity_flags"))

    score = len(signals) + (1 if implies_planning else 0) + (1 if has_ambiguity else 0)
    intent_value = (analytical_intent or {}).get("intent")

    return RequestProfile(
        complexity_signals=signals,
        complexity_score=score,
        analytical_intent=str(intent_value) if intent_value else None,
        implies_planning=implies_planning,
        has_ambiguity=has_ambiguity,
        latency_sensitive=latency_sensitive,
    )


__all__ = ["assess_request"]
