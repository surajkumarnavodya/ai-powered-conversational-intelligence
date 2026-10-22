"""Per-task model pinning: which model is this *kind* of work's baseline.

**How this differs from `model_routing.policy`, and why both exist.**
They answer two different questions and are deliberately not merged:

  - *This* module answers "what model should a task of this kind start
    from" -- a static, operator-configured mapping from a kind of work
    (routing, SQL generation, RAG answering, vision, embedding) to a
    model name. It makes one LLM call site's model an `.env` decision
    instead of a hardcoded `Settings.ollama_model` read.
  - `model_routing.policy` answers "given this request's complexity,
    budget, breaker state and recorded evaluation evidence, should we
    escalate or de-escalate *from* that baseline" -- a dynamic,
    per-request decision.

So this supplies the baseline the policy already takes as input. Running
both is not two implementations of one question (CLAUDE.md 6.1.2); it is
one decision each. `ModelRouter` keeps its existing behaviour: a
caller-pinned `AskRequest.model` still wins over everything here, because
a per-task default is still only a default.

**Scope is honest about what is actually wired.** `ModelTask` lists only
tasks with a real call site that threads a model today. `EMBEDDING` and
`VISION` resolve to the settings that already own those choices
(`EMBEDDING_MODEL_NAME`, `MEDIA_VISION_MODEL`) rather than introducing a
second owner for them -- `resolve_task_model` returns what that existing
setting holds. A task nobody threads a model through is absent, not
listed-and-ignored, for the reason `RouteTask`'s own docstring gives:
listing it would imply a routing capability that does not exist.

**A pin can never reach an unapproved model.** Every configured pin is
validated against `Settings.ollama_allowed_models` at startup
(`validate_task_model_pins`, called from `Settings`' own validator), so a
typo or a deliberately-smuggled name fails the app's boot rather than
arriving at `ollama.Client.chat(model=...)` unchecked. That is the same
allowlist `agent.model_registry.validate_model_selection` enforces for a
caller-supplied model, reused rather than re-implemented.

**Nothing here is exposed to a caller.** The pins are provider and
deployment configuration. `api/jobs_schemas.py` and `AskResponse` report
only the resolved model *name* that already appears on the synchronous
path; no response carries the mapping, the strategy, or the fallback
chain (brief §3: never expose internal model configuration).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any

from config.settings import Settings

logger = logging.getLogger(__name__)


class ModelTask(StrEnum):
    """A kind of LLM work that has its own configurable model.

    Each member corresponds to a real call site that threads a model
    today -- see this module's docstring on why an unwired task is
    absent rather than listed.
    """

    #: Source/intent classification in `agent.orchestrator.nodes
    #: .classify_sources`. Wants a small, fast model: the output is a
    #: comma-separated list of at most a few tokens, and the call sits
    #: directly in the user's latency path before any real work starts.
    ROUTER = "router"
    #: SQL generation in `agent.nodes.generate_sql_node`. The one task
    #: where a SQL-specialised model (`sqlcoder`, `duckdb-nsql`) can beat
    #: a larger general model, which is the whole reason this pin exists.
    SQL = "sql"
    #: Answer composition over retrieved chunks (`rag.graph`). Wants
    #: instruction-following and faithfulness to the excerpts, not SQL
    #: ability.
    RAG_ANSWER = "rag_answer"
    #: Image understanding (`attachments/`, `media/`). Resolves to
    #: `MEDIA_VISION_MODEL`, which already owns this choice.
    VISION = "vision"
    #: Text embedding (`embeddings/`, `retrieval/`). Resolves to
    #: `EMBEDDING_MODEL_NAME`, which already owns this choice. Not an
    #: Ollama chat model, so it is never allowlist-checked against
    #: `ollama_allowed_models` -- see `validate_task_model_pins`.
    EMBEDDING = "embedding"


#: `MODEL_ROUTING_STRATEGY`'s accepted values, and what each means when
#: `model_routing.policy` has several eligible candidates.
#:
#: These order *preferences among already-eligible models*. None of them
#: can reach a model outside `ollama_allowed_models`, relax a risk level,
#: or widen a budget -- a strategy chooses between permitted options and
#: is never itself a permission.
MODEL_ROUTING_STRATEGIES: tuple[str, ...] = ("capability", "cost", "latency", "quality")

#: What each strategy optimises, for the startup log line and the error
#: message a bad value produces. Prose rather than behaviour, so this
#: cannot drift from the implementation: the behaviour lives in
#: `strategy_sort_key` below.
STRATEGY_DESCRIPTIONS: dict[str, str] = {
    "capability": (
        "match the task's declared requirement; escalate only when complexity warrants it "
        "(the default, and the pre-existing behaviour)"
    ),
    "cost": "prefer the lowest resource tier that is still eligible",
    "latency": "prefer the fastest observed p95, then the lowest tier",
    "quality": "prefer the highest recorded evaluation score, then the strongest tier",
}


def _pin_for(task: ModelTask, settings: Settings) -> str:
    """The raw configured pin for one task, or `""` when unset.

    Reads the existing owner for the two tasks that already have one
    (`VISION`, `EMBEDDING`) rather than a new parallel field, so neither
    choice gains a second owner (CLAUDE.md 4.8).
    """
    if task is ModelTask.ROUTER:
        return settings.router_model
    if task is ModelTask.SQL:
        return settings.sql_model
    if task is ModelTask.RAG_ANSWER:
        return settings.rag_answer_model
    if task is ModelTask.VISION:
        return settings.media_vision_model
    if task is ModelTask.EMBEDDING:
        return settings.embedding_model_name
    return ""


def resolve_task_model(
    task: ModelTask,
    settings: Settings,
    *,
    caller_pinned: str | None = None,
) -> str:
    """The model this task should use, in strict precedence order.

    1. **`caller_pinned`** -- a validated `AskRequest.model`. A caller's
       explicit choice always wins, exactly as `ModelRouter` already
       guarantees with `RouteReason.CALLER_PINNED_MODEL`. Passing an
       unvalidated string here would bypass the allowlist, so callers
       pass the output of `agent.model_registry.validate_model_selection`,
       never a raw request field.
    2. **The task's own pin** (`ROUTER_MODEL`, `SQL_MODEL`, ...), when an
       operator set one.
    3. **`FALLBACK_MODEL`**, when set. This is the "approved alternate"
       the brief asks for at the configuration layer; the *runtime*
       alternate on a provider failure is `model_routing.policy`'s
       fallback chain, which is a different mechanism for a different
       moment (a model that is down, not a model that is unconfigured).
    4. **`OLLAMA_MODEL`** -- the one model an operator has definitely
       decided to run.

    Never returns an empty string: step 4 always holds, because
    `ollama_model` has a non-empty default and is itself validated.

    `EMBEDDING` deliberately ignores steps 1 and 3: a chat model is not
    an embedding model, and falling back from a missing embedding model
    to `OLLAMA_MODEL` would produce vectors from the wrong model and a
    corrupt index rather than a clean failure.
    """
    if task is ModelTask.EMBEDDING:
        return settings.embedding_model_name

    if caller_pinned:
        return caller_pinned

    pinned = _pin_for(task, settings).strip()
    if pinned:
        return pinned

    fallback = settings.fallback_model.strip()
    if fallback:
        return fallback

    return settings.ollama_model


def validate_task_model_pins(settings: Settings) -> list[str]:
    """Every configured chat-model pin that is outside the allowlist.

    Returns the offending `(SETTING=value)` descriptions rather than
    raising, so `Settings`' own validator can report all of them in one
    message instead of making an operator fix them one restart at a
    time.

    `EMBEDDING` is excluded: `EMBEDDING_MODEL_NAME` is a
    sentence-transformers model, not an Ollama chat model, so checking it
    against `ollama_allowed_models` would reject every correct value.

    **Why this is fail-closed.** An unapproved pin is not a degraded
    experience, it is a model the operator never approved receiving this
    deployment's prompts and data. Booting with it and logging a warning
    would mean the first anyone hears of it is in a provider's logs.
    """
    allowed = set(settings.ollama_allowed_models) | {settings.ollama_model}
    offenders: list[str] = []
    checks = (
        ("ROUTER_MODEL", settings.router_model),
        ("SQL_MODEL", settings.sql_model),
        ("RAG_ANSWER_MODEL", settings.rag_answer_model),
        ("FALLBACK_MODEL", settings.fallback_model),
    )
    for name, value in checks:
        pin = (value or "").strip()
        if pin and pin not in allowed:
            offenders.append(f"{name}={pin!r}")
    return offenders


def strategy_sort_key(
    strategy: str,
    observed_p95_latency_ms: Mapping[str, float] | None = None,
) -> Callable[[Any], tuple[Any, ...]]:
    """A sort key over `RoutableModel`, expressing one strategy's preference.

    Used by `model_routing.policy` to order otherwise-equally-eligible
    candidates. Returns a callable rather than doing the sort, so the
    policy keeps ownership of *which* candidates are eligible -- this
    only ever reorders a list the policy already approved.

    `observed_p95_latency_ms` is the same measured map
    `model_routing.policy._drop_slow` already receives (latency is a
    deployment observation held in `model_routing.metrics`, not a field
    on the model), and is only consulted by the `"latency"` strategy. A
    model with no measurement sorts **last** under that strategy:
    unknown is not fast, and treating absence of evidence as evidence of
    speed is exactly the inference-as-fact failure CLAUDE.md 4.4
    prohibits. Note `_drop_slow` keeps such a model for the opposite and
    equally correct reason -- absence of evidence is not evidence of
    *slowness* either, so it stays eligible but is not preferred.

    `"capability"` returns the policy's own pre-existing ordering
    (evaluation score, then tier, then the recommended flag, then id), so
    the default strategy is byte-for-byte the behaviour that shipped
    before this setting existed. That matters: a new configuration knob
    whose default silently changed routing would be a regression
    disguised as a feature.

    An unrecognized strategy also falls back to `"capability"`. It cannot
    normally happen -- `Settings` validates the value -- but "the
    previous behaviour" is the right answer to a value nobody
    anticipated, rather than an exception raised from inside a sort.
    """
    from model_routing.models import TIER_RANK

    latency = observed_p95_latency_ms or {}

    def _score(candidate: Any) -> float:
        value = getattr(candidate, "evaluation_quality_score", None)
        return value if value is not None else -1.0

    def _p95(candidate: Any) -> float:
        return latency.get(candidate.model_id, float("inf"))

    if strategy == "cost":
        return lambda c: (TIER_RANK[c.tier], -_score(c), not c.recommended, c.model_id)
    if strategy == "latency":
        return lambda c: (_p95(c), TIER_RANK[c.tier], not c.recommended, c.model_id)
    # "quality" and "capability" share this ordering today. They are kept
    # as distinct accepted values because they answer different questions
    # and will diverge: "capability" is also what gates *whether* to
    # escalate at all (the complexity thresholds), while "quality" speaks
    # only to the choice among candidates once escalating.
    return lambda c: (-_score(c), -TIER_RANK[c.tier], not c.recommended, c.model_id)


def describe_configuration(settings: Settings) -> str:
    """A log-safe one-line summary of the task pins, for startup.

    Model names only -- an operator-chosen Ollama model name is not a
    secret (it is already returned on every `AskResponse`), and there is
    no host, key or provider credential anywhere in this mapping.
    """
    resolved = {
        task.value: resolve_task_model(task, settings)
        for task in ModelTask
        if task is not ModelTask.EMBEDDING
    }
    return (
        f"strategy={settings.model_routing_strategy} "
        f"tasks={resolved} embedding={settings.embedding_model_name!r}"
    )


__all__ = [
    "MODEL_ROUTING_STRATEGIES",
    "STRATEGY_DESCRIPTIONS",
    "ModelTask",
    "describe_configuration",
    "resolve_task_model",
    "strategy_sort_key",
    "validate_task_model_pins",
]
