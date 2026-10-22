"""Layered guardrails: one configurable façade over controls that already exist.

**This package adds a seam, not a second security model.** Every control
it coordinates was already here and is still the thing actually enforcing:

| Layer | Who enforces it | Posture |
|---|---|---|
| Input validation / normalisation | `agent.input_guard.check_input` | fail-closed |
| Prompt-injection detection | `security.injection_patterns` | **detection only** |
| Topic / domain policy | `governance.request_policy` | fail-closed |
| Source & tool permission | `agent.orchestrator.nodes.SOURCE_PERMISSIONS`, `agent.authz`, `identity.rbac` | fail-closed |
| SQL safety | `agent.sql_validator` (AST allowlist) | fail-closed |
| Restricted columns / PII in results | `governance.result_policy`, `governance.classification` | fail-closed |
| Secret redaction | `security.redaction` | always applied |
| Uploaded-content moderation | `moderation.gate` | fail-closed once enabled |
| Grounding / citations | `guardrails.grounding` | configurable |

What was missing was a single place an operator can see and tune the
*advisory* layers, and one vocabulary for reporting a refusal. That is
all this package is.

**The rule that makes `GUARDRAIL_ACTION` safe.** `GUARDRAIL_ACTION` can
be `block`, `warn` or `redact`, and it governs **only the advisory
layers** -- injection detection, toxicity screening, PII redaction of
free text, and grounding. It has **no effect whatsoever** on a
fail-closed control. Setting `GUARDRAIL_ACTION=warn` cannot:

  - let a non-`SELECT` statement reach the database,
  - let a caller read a source, tool or column their roles do not grant,
  - let a `CREDENTIAL` or `PAYMENT` column be returned to anyone,
  - resolve or override a tenant,
  - let a sensitivity-categorised policy chunk be summarised,
  - or disable small-group suppression.

`guardrails.engine.resolve_action` is the single function that applies
this, and `tests/test_guardrails.py` proves it by driving every
fail-closed control with `GUARDRAIL_ACTION=warn` set and asserting each
still refuses. That test is the reason the rule is a property of the
code rather than a promise in a docstring.

This mirrors `security/injection_patterns.py`'s own standing position in
this codebase: detection is a *signal*; the validator and the RBAC gates
are the boundary. A configurable knob over a signal is useful. A
configurable knob over a boundary would be a vulnerability with an
`.env` setting.

**Everything retrieved or supplied is data, never instructions.** Web
results, ingested PDFs, uploaded attachments, OCR/ASR text, media
metadata, database cell values and model output are all treated as
untrusted (CLAUDE.md 5.5). `guardrails.engine.scan_untrusted_content` is
the shared entry point for screening any of them, and it never changes
what a caller is authorized to do: text claiming authority ("this is
authorized", "pretend I am admin") is matched like any other words and
carries none.
"""

from __future__ import annotations

from guardrails.config import (
    GuardrailAction,
    GuardrailConfig,
    GuardrailLayer,
    config_from_settings,
)
from guardrails.engine import (
    GuardrailFinding,
    GuardrailVerdict,
    evaluate_question,
    resolve_action,
    scan_untrusted_content,
)
from guardrails.grounding import (
    GroundingVerdict,
    assess_grounding,
)

__all__ = [
    "GroundingVerdict",
    "GuardrailAction",
    "GuardrailConfig",
    "GuardrailFinding",
    "GuardrailLayer",
    "GuardrailVerdict",
    "assess_grounding",
    "config_from_settings",
    "evaluate_question",
    "resolve_action",
    "scan_untrusted_content",
]
