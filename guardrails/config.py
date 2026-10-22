"""What is switched on, and what happens when a layer fires.

Two concepts, kept apart because conflating them is how a safety control
gets accidentally disabled:

  - **`GuardrailLayer`** -- *which* checks run. Each has its own `.env`
    flag so an operator can roll one back without touching the others,
    the same independent-rollback pattern `ENABLE_REQUEST_GOVERNANCE`,
    `ENABLE_RESULT_GOVERNANCE` and `ENABLE_RECOMMENDATION_SAFETY`
    already follow.
  - **`GuardrailAction`** -- what happens when an *advisory* layer
    fires. Never consulted for a fail-closed control; see
    `guardrails.engine.resolve_action`, which is the one place that
    distinction is applied.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from config.settings import Settings


class GuardrailAction(StrEnum):
    """What to do when an advisory guardrail layer fires.

    `BLOCK` is the default everywhere. The other two exist because a
    deployment tuning its detection thresholds needs to see what *would*
    have been blocked without refusing real work in the meantime -- and
    the honest way to offer that is an explicit setting, not a commented
    -out check.
    """

    #: Refuse the request with a fixed message. The default.
    BLOCK = "block"
    #: Proceed, but record the finding and attach a caller-visible
    #: notice. For tuning a detector, or for a deployment that would
    #: rather over-answer than over-refuse on an advisory signal.
    WARN = "warn"
    #: Proceed with the offending span removed or masked. Only
    #: meaningful for layers that *can* redact (PII in free text,
    #: secret-shaped strings); for a layer with nothing to redact
    #: (grounding, for instance) this behaves as `WARN`, and
    #: `guardrails.engine` says so rather than silently doing nothing.
    REDACT = "redact"


class GuardrailLayer(StrEnum):
    """One check. Named so a finding, a log line and a metric all agree.

    Only layers with a real implementation are listed. A member with
    nothing behind it would be exactly the "documented capability that
    does not exist" CLAUDE.md 1 warns about.
    """

    #: Length, Unicode normalisation, control characters, gibberish.
    #: `agent.input_guard.check_input`. **Fail-closed.**
    INPUT_VALIDATION = "input_validation"
    #: Prompt-injection and jailbreak phrasing.
    #: `security.injection_patterns`. **Advisory** -- the SQL validator
    #: and the RBAC gates bound the consequences.
    PROMPT_INJECTION = "prompt_injection"
    #: The same patterns applied to retrieved or supplied content rather
    #: than the question: documents, web pages, OCR text, metadata, tool
    #: output. **Advisory**, same reasoning.
    INDIRECT_INJECTION = "indirect_injection"
    #: Domain policy and prohibited actions.
    #: `governance.request_policy`. **Fail-closed.**
    TOPIC_POLICY = "topic_policy"
    #: Abusive or unsafe content. `moderation.gate` where it is wired
    #: (uploaded assets); pattern-based elsewhere. **Advisory** for a
    #: question, **fail-closed** for an ingested asset.
    TOXICITY = "toxicity"
    #: Personal and sensitive data. `governance.classification` and
    #: `governance.result_policy` for result columns (**fail-closed**);
    #: `security.redaction` for free text (**always applied**).
    PII = "pii"
    #: Source, tool and column permission. `agent.authz`,
    #: `identity.rbac`, `SOURCE_PERMISSIONS`. **Fail-closed.**
    PERMISSION = "permission"
    #: Answer-to-evidence alignment and citation presence.
    #: `guardrails.grounding`. **Configurable.**
    GROUNDING = "grounding"
    #: A model-proposed tool call or statement that deterministic
    #: validation rejected. `agent.sql_validator`, `agent.tools`.
    #: **Fail-closed.**
    UNSAFE_TOOL_CALL = "unsafe_tool_call"


#: The layers whose verdict is a boundary, not a signal. `GUARDRAIL_ACTION`
#: is **never** applied to one of these -- they refuse, whatever it is set
#: to (see this package's own docstring and
#: `guardrails.engine.resolve_action`).
#:
#: Membership here is the single source of truth for that distinction. A
#: new layer is advisory unless it is added to this set *and* the control
#: it names genuinely refuses on its own, which is the safe default
#: direction: a layer wrongly treated as advisory is still enforced by the
#: underlying control, while a layer wrongly treated as fail-closed would
#: claim an enforcement this package does not perform.
FAIL_CLOSED_LAYERS: frozenset[GuardrailLayer] = frozenset(
    {
        GuardrailLayer.INPUT_VALIDATION,
        GuardrailLayer.TOPIC_POLICY,
        GuardrailLayer.PERMISSION,
        GuardrailLayer.UNSAFE_TOOL_CALL,
    }
)


@dataclass(frozen=True)
class GuardrailConfig:
    """The resolved guardrail configuration for one request.

    Frozen and built once per request from `Settings`, so nothing
    downstream -- and in particular nothing the model produces -- can
    widen it mid-execution. The same discipline
    `agent.harness.ExecutionBudgets` follows, for the same reason: a
    layer that could raise its own ceiling is not a layer.
    """

    enabled: bool
    action: GuardrailAction
    prompt_injection: bool
    pii_redaction: bool
    toxicity: bool
    hallucination: bool
    grounding_required: bool
    block_unsafe_tool_calls: bool
    min_source_confidence: float
    require_citations: bool
    allow_general_knowledge: bool
    max_retrieval_chunks: int

    def layer_enabled(self, layer: GuardrailLayer) -> bool:
        """Whether `layer` runs under this configuration.

        **A fail-closed layer is always on.** `GUARDRAILS_ENABLED=false`
        turns off this package's advisory screening; it does not and
        cannot disable the SQL validator, the RBAC gates, the domain
        policy or input validation, each of which is enforced by its own
        module regardless of anything here. Returning `True` for those
        layers even when `enabled` is `False` is what keeps this method
        an honest description of reality rather than a claim that the
        boundary went away.
        """
        if layer in FAIL_CLOSED_LAYERS:
            return True
        if not self.enabled:
            return False
        if layer is GuardrailLayer.PROMPT_INJECTION:
            return self.prompt_injection
        if layer is GuardrailLayer.INDIRECT_INJECTION:
            return self.prompt_injection
        if layer is GuardrailLayer.TOXICITY:
            return self.toxicity
        if layer is GuardrailLayer.PII:
            return self.pii_redaction
        if layer is GuardrailLayer.GROUNDING:
            return self.hallucination or self.grounding_required
        return True


def config_from_settings(settings: Settings) -> GuardrailConfig:
    """Build the per-request guardrail configuration from `.env`.

    A plain function over `Settings` rather than a cached singleton:
    these values are read per request, and a cached config would make a
    test's monkeypatched `Settings` silently ineffective -- the exact
    hazard `tests/conftest.py` has to clear for the other
    process-lifetime caches in this codebase.
    """
    return GuardrailConfig(
        enabled=settings.guardrails_enabled,
        action=GuardrailAction(settings.guardrail_action),
        prompt_injection=settings.prompt_injection_protection,
        pii_redaction=settings.pii_redaction_enabled,
        toxicity=settings.toxicity_check_enabled,
        hallucination=settings.hallucination_check_enabled,
        grounding_required=settings.source_grounding_required,
        block_unsafe_tool_calls=settings.block_unsafe_tool_calls,
        min_source_confidence=settings.min_source_confidence,
        require_citations=settings.require_citations,
        allow_general_knowledge=settings.allow_general_knowledge,
        max_retrieval_chunks=settings.max_retrieval_chunks,
    )


__all__ = [
    "FAIL_CLOSED_LAYERS",
    "GuardrailAction",
    "GuardrailConfig",
    "GuardrailLayer",
    "config_from_settings",
]
