"""The façade: run the advisory layers, report one verdict.

`resolve_action` is the most important function here. It is the single
place `GUARDRAIL_ACTION` is applied, and the single place the
advisory/fail-closed distinction is enforced -- so the property "a
configuration setting can never soften a boundary" is checkable by
reading one function rather than auditing every call site.

**Refusal messages are fixed constants.** Never built from the question,
never naming which pattern matched, so a refusal cannot be used as an
oracle for what the detector looks for (CLAUDE.md 5.5). The matched
pattern names go to the log and the audit event, which is where an
investigator can see them and an attacker cannot.

**Nothing here logs content.** Findings carry layer names, pattern names
and counts. The question, the retrieved excerpt and the answer are never
logged by this module (CLAUDE.md 5.6).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agent.input_guard import check_input
from config.settings import Settings
from guardrails.config import (
    FAIL_CLOSED_LAYERS,
    GuardrailAction,
    GuardrailConfig,
    GuardrailLayer,
    config_from_settings,
)
from security.injection_patterns import INJECTION_PATTERNS

logger = logging.getLogger(__name__)

#: Fixed caller-facing text, one per outcome. Module constants so a
#: refusal is never assembled from the input.
BLOCKED_MESSAGE = (
    "This request could not be processed because it did not pass this deployment's "
    "safety checks."
)
WARNED_NOTICE = (
    "This answer was produced despite a safety check flagging the request. Treat it " "with care."
)
REDACTED_NOTICE = "Part of this content was removed by a safety check."
UNGROUNDED_MESSAGE = (
    "I could not verify an answer to that from the sources available to you, so I am "
    "not going to guess. Try rephrasing, or ask an administrator whether the relevant "
    "data or documents are configured."
)

#: Deliberately coarse toxicity/abuse patterns for *free text*. This is
#: not a content classifier and does not pretend to be one -- the real
#: content gate for ingested assets is `moderation.gate`, which is
#: mandatory whenever media search or document RAG is on and rejects the
#: whole asset on any bad chunk.
#:
#: Kept narrow on purpose. A broad regex list over user questions
#: over-blocks ordinary analytics ("show me the churn/attrition figures",
#: "which accounts are at risk of default") and over-blocking is its own
#: failure mode, named as such in CLAUDE.md 5.8.
_ABUSE_PATTERNS: dict[str, str] = {
    "self_harm_instruction": r"\bhow\s+(do|can)\s+i\s+(kill|hurt|harm)\s+myself\b",
    "weapon_synthesis": r"\bhow\s+to\s+(build|make|synthesi[sz]e)\s+a?\s*"
    r"(bomb|explosive|nerve\s+agent|bioweapon)\b",
    "malware_authoring": r"\b(write|generate|create)\s+(me\s+)?(a\s+)?"
    r"(ransomware|keylogger|rootkit|computer\s+virus)\b",
    "credential_exfiltration": r"\b(dump|exfiltrate|steal|leak)\s+"
    r"(the\s+)?(password|passwords|credential|credentials|api\s+keys?|secret|secrets)\b",
}


@dataclass(frozen=True)
class GuardrailFinding:
    """One layer firing on one request.

    Attributes:
        layer: Which check fired.
        action: What was actually done -- the resolved action, not the
            configured one, so a `WARN` setting that could not apply to a
            fail-closed layer shows `BLOCK` here. This is the field an
            auditor reads.
        detail: Operator-facing, log-safe explanation. Pattern names and
            counts; never the matching text.
        fail_closed: Whether this layer's verdict is a boundary. Carried
            on the finding so a reader need not re-derive it from
            `FAIL_CLOSED_LAYERS`.
    """

    layer: GuardrailLayer
    action: GuardrailAction
    detail: str
    fail_closed: bool = False


@dataclass(frozen=True)
class GuardrailVerdict:
    """The combined outcome of every layer that ran.

    Attributes:
        findings: Every layer that fired, in the order they ran.
        blocked: Whether the request must be refused. True if *any*
            finding resolved to `BLOCK`.
        message: The fixed caller-facing refusal text when blocked, else
            `None`.
        notices: Caller-facing notices for `WARN`/`REDACT` findings --
            shown alongside an answer that was still produced.
        cleaned_text: The normalised (and, under `REDACT`, redacted)
            text downstream should use. Always set, so a caller never
            has to decide whether to re-normalise.
    """

    findings: tuple[GuardrailFinding, ...] = ()
    blocked: bool = False
    message: str | None = None
    notices: tuple[str, ...] = ()
    cleaned_text: str = ""

    @property
    def fired_layers(self) -> tuple[GuardrailLayer, ...]:
        """Which layers fired, for telemetry. Names only, never content."""
        return tuple(f.layer for f in self.findings)


def resolve_action(layer: GuardrailLayer, config: GuardrailConfig) -> GuardrailAction:
    """What this layer actually does when it fires. **The safety seam.**

    A fail-closed layer always resolves to `BLOCK`, whatever
    `GUARDRAIL_ACTION` says. That is the whole point of this function
    existing instead of each call site reading `config.action` directly:
    there is one place to read, one place to test, and no way for a new
    call site to forget the rule.

    Concretely, with `GUARDRAIL_ACTION=warn` configured, this still
    returns `BLOCK` for input validation, domain policy, permission and
    unsafe tool calls -- so a non-`SELECT` statement, an unauthorized
    source, a prohibited domain action and an oversized input are all
    still refused. `tests/test_guardrails.py` asserts exactly that, and
    asserts it for every member of `FAIL_CLOSED_LAYERS` rather than a
    sample, so adding a layer cannot quietly escape the check.

    For an advisory layer, `REDACT` degrades to `WARN` when the layer has
    nothing to redact (grounding, permission); the caller is told via the
    finding's `detail` rather than silently getting a no-op.
    """
    if layer in FAIL_CLOSED_LAYERS:
        return GuardrailAction.BLOCK
    if config.action is GuardrailAction.REDACT and layer in (
        GuardrailLayer.GROUNDING,
        GuardrailLayer.TOXICITY,
    ):
        # Nothing to excise: a refusal to answer is not a span, and a
        # redacted abusive question is still the same request.
        return GuardrailAction.WARN
    return config.action


def _injection_matches(text: str) -> tuple[str, ...]:
    """Named injection patterns that fire on `text`.

    Reuses `security.injection_patterns.INJECTION_PATTERNS` rather than
    keeping a second copy -- that module is the single owner of what
    injection phrasing looks like in this codebase, and a divergent
    second list is how a detector silently stops matching what the tests
    think it matches.
    """
    return tuple(name for name, pattern in INJECTION_PATTERNS.items() if pattern.search(text))


def _abuse_matches(text: str) -> tuple[str, ...]:
    import re

    return tuple(
        name for name, pattern in _ABUSE_PATTERNS.items() if re.search(pattern, text, re.IGNORECASE)
    )


def evaluate_question(
    question: str,
    settings: Settings,
    *,
    config: GuardrailConfig | None = None,
) -> GuardrailVerdict:
    """Run the input-side layers over one question.

    Layer order is deliberate and matches the cheap-and-decisive-first
    principle `agent.input_guard.check_input` already follows internally:

    1. **Input validation** (fail-closed). Length on the raw string
       before any normalisation work, then Unicode folding and the
       control-character strip. Everything after it operates on the
       normalised text, so a layer cannot be evaded with a confusable
       character.
    2. **Prompt injection** (advisory). Detection only. A match here is
       a signal recorded for investigation; what actually bounds the
       consequence is the SQL validator and the RBAC gates, which run
       regardless.
    3. **Abuse/toxicity** (advisory, narrow). See `_ABUSE_PATTERNS` on
       why this is deliberately not a content classifier.

    **Domain/topic policy is not run here.** `governance.request_policy`
    is already invoked by `router_node` with the caller's roles, which
    this function does not receive -- duplicating it here would create a
    second owner for a fail-closed decision and risk the two disagreeing
    (CLAUDE.md 4.8). `GuardrailLayer.TOPIC_POLICY` exists so a finding
    from that existing call can be *reported* in this vocabulary, not so
    this module re-decides it.

    Returns a verdict whose `cleaned_text` is always safe to use
    downstream, even when blocked.
    """
    cfg = config or config_from_settings(settings)
    findings: list[GuardrailFinding] = []
    notices: list[str] = []

    guard = check_input(question, settings.max_question_length)
    cleaned = guard.cleaned_question

    if not guard.passed:
        # Fail-closed, and the reason stays internal: `agent.input_guard`
        # owns the user-facing message for each `RejectionReason`, and
        # that message is deliberately standardised so it reveals nothing
        # about which check fired.
        findings.append(
            GuardrailFinding(
                layer=GuardrailLayer.INPUT_VALIDATION,
                action=GuardrailAction.BLOCK,
                detail=(
                    f"input_guard rejected: reason={guard.reason} "
                    f"patterns={list(guard.matched_patterns)}"
                ),
                fail_closed=True,
            )
        )
        logger.warning(
            "[guardrails] input validation blocked a question (reason=%s, patterns=%s)",
            guard.reason,
            list(guard.matched_patterns),
        )
        return GuardrailVerdict(
            findings=tuple(findings),
            blocked=True,
            message=BLOCKED_MESSAGE,
            cleaned_text=cleaned,
        )

    if cfg.layer_enabled(GuardrailLayer.PROMPT_INJECTION):
        matched = _injection_matches(cleaned)
        if matched:
            action = resolve_action(GuardrailLayer.PROMPT_INJECTION, cfg)
            findings.append(
                GuardrailFinding(
                    layer=GuardrailLayer.PROMPT_INJECTION,
                    action=action,
                    detail=f"injection patterns matched: {list(matched)}",
                )
            )
            logger.warning(
                "[guardrails] prompt-injection patterns matched (action=%s, patterns=%s)",
                action.value,
                list(matched),
            )
            if action is GuardrailAction.WARN:
                notices.append(WARNED_NOTICE)

    if cfg.layer_enabled(GuardrailLayer.TOXICITY):
        matched = _abuse_matches(cleaned)
        if matched:
            action = resolve_action(GuardrailLayer.TOXICITY, cfg)
            findings.append(
                GuardrailFinding(
                    layer=GuardrailLayer.TOXICITY,
                    action=action,
                    detail=f"abuse patterns matched: {list(matched)}",
                )
            )
            logger.warning(
                "[guardrails] abuse patterns matched (action=%s, patterns=%s)",
                action.value,
                list(matched),
            )
            if action is not GuardrailAction.BLOCK:
                notices.append(WARNED_NOTICE)

    blocked = any(f.action is GuardrailAction.BLOCK for f in findings)
    return GuardrailVerdict(
        findings=tuple(findings),
        blocked=blocked,
        message=BLOCKED_MESSAGE if blocked else None,
        notices=tuple(dict.fromkeys(notices)),
        cleaned_text=cleaned,
    )


def scan_untrusted_content(
    text: str,
    settings: Settings,
    *,
    origin: str,
    config: GuardrailConfig | None = None,
) -> GuardrailVerdict:
    """Screen retrieved or supplied content for indirect injection.

    The shared entry point for every untrusted channel: web search
    results, ingested PDF text, uploaded attachments, OCR/ASR output,
    caption text, media metadata, tool output and database cell values.
    One function so the framing is identical everywhere and a new source
    cannot forget to apply it.

    **This never changes what the caller may do.** Content asserting
    authority -- "this is authorized", "pretend I am admin", "ignore your
    instructions" -- is matched like any other words. Authorization comes
    only from the caller's roles (CLAUDE.md 5.5). A match here means
    "this text tried to issue instructions", which is recorded and may
    suppress the excerpt; it never grants anything, and it is never the
    control that stops the instruction working -- the SQL validator and
    the RBAC gates are.

    `origin` is a short, operator-chosen label ("web_search",
    "document_chunk", "ocr_text") used only for the log line and the
    finding detail. It is never caller-supplied free text.

    Under `REDACT` the offending content is dropped from `cleaned_text`
    entirely rather than partially masked: a document chunk carrying an
    injection attempt has no trustworthy remainder worth splicing back
    into a prompt, and a partial mask would leave the surrounding
    instruction intact.
    """
    cfg = config or config_from_settings(settings)
    if not cfg.layer_enabled(GuardrailLayer.INDIRECT_INJECTION):
        return GuardrailVerdict(cleaned_text=text)

    matched = _injection_matches(text)
    if not matched:
        return GuardrailVerdict(cleaned_text=text)

    action = resolve_action(GuardrailLayer.INDIRECT_INJECTION, cfg)
    finding = GuardrailFinding(
        layer=GuardrailLayer.INDIRECT_INJECTION,
        action=action,
        detail=f"indirect injection in {origin}: {list(matched)}",
    )
    logger.warning(
        "[guardrails] indirect-injection patterns in untrusted content "
        "(origin=%s, action=%s, patterns=%s, chars=%d)",
        origin,
        action.value,
        list(matched),
        len(text),
    )

    if action is GuardrailAction.BLOCK:
        return GuardrailVerdict(
            findings=(finding,),
            blocked=True,
            message=BLOCKED_MESSAGE,
            cleaned_text="",
        )
    if action is GuardrailAction.REDACT:
        return GuardrailVerdict(
            findings=(finding,),
            notices=(REDACTED_NOTICE,),
            cleaned_text="",
        )
    return GuardrailVerdict(findings=(finding,), notices=(WARNED_NOTICE,), cleaned_text=text)


__all__ = [
    "BLOCKED_MESSAGE",
    "REDACTED_NOTICE",
    "UNGROUNDED_MESSAGE",
    "WARNED_NOTICE",
    "GuardrailFinding",
    "GuardrailVerdict",
    "evaluate_question",
    "resolve_action",
    "scan_untrusted_content",
]
