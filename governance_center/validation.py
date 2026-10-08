"""Deterministic validation of a governed artifact version -- Prompt 45
(`45_PROMPT_AGENT_REGISTRY_CONTRACT.md`)'s "validate unsafe composition,
missing variables and unapproved tools".

Pure and I/O-free: no database, no LLM, no network. Everything it needs
arrives as a `ValidationContext`, which `api/governance.py` builds once
per request from already-loaded state (`Settings`, the live inventory,
the real tool registries). That keeps this module unit-testable with
hand-built values -- the same split `governance_center/policy.py`,
`onboarding/policy.py` and `agent/plan_validator.py` already use.

## What it refuses, and what it deliberately does not

It refuses a *candidate artifact version* at review time. It is **not** a
runtime guard, and it is not the security boundary for anything the
platform actually executes -- see `governance_center/safety.py`'s own
docstring for that distinction, which applies in full here. A prompt
artifact in this registry never becomes a live system prompt
(`governance_center/inventory.py` explains why), so this validator's job
is to stop a *governance* mistake: approving a version that drops a
control, names a tool that does not exist, targets a model this
deployment cannot run, or claims an evaluation suite that was never run.

## No partial trust

One `ERROR`-severity violation invalidates the whole artifact
(`ArtifactValidationReport.is_valid` is `False`), exactly as
`analytics.scenario.validate_scenario_parameters` and
`optimization.validator.validate_problem` already treat their own
inputs: there is no "keep the valid parts" path. Every violation found
is reported, not just the first, so a reviewer fixes one round of real
problems rather than discovering them one at a time.

`WARNING` violations are reported and never block -- they cover genuine
quality regressions (a dropped `NO_FABRICATION`, an undeclared
placeholder) that a reviewer should see but that are not a security or
semantic-governance bypass.
"""

from __future__ import annotations

import logging
import re
from enum import Enum

from pydantic import BaseModel, ConfigDict

from agent.provenance import DataTruthLevel
from governance_center.artifacts import (
    TYPED_ASSET_TYPES,
    AgentArtifactSpec,
    AnalyticalPolicyArtifactSpec,
    ArtifactSpec,
    ArtifactSpecError,
    PromptArtifactSpec,
    extract_template_variables,
    parse_artifact_spec,
)
from governance_center.models import GovernedAssetType
from governance_center.safety import (
    SECURITY_INVARIANTS,
    SafetyInvariant,
    detect_weakening,
    invariants_present,
)
from security.injection_patterns import INJECTION_PATTERNS

logger = logging.getLogger(__name__)


class ValidationSeverity(str, Enum):
    """Whether a violation blocks the artifact or is merely reported.

    Only `ERROR` makes `ArtifactValidationReport.is_valid` false.
    """

    ERROR = "error"
    WARNING = "warning"


class ViolationCode(str, Enum):
    """The closed set of problems this validator reports.

    A closed enum (rather than free-text messages alone) so the API, the
    tests and a future UI can branch on a specific problem without
    string-matching a human sentence.
    """

    #: The asset type has no typed specification at all, so nothing here
    #: can be checked. Reported as an ERROR rather than silently passing
    #: -- a caller asking for validation of an untyped type should be
    #: told it cannot be validated, not handed a clean report.
    UNTYPED_ASSET_TYPE = "untyped_asset_type"
    #: `config` could not be read as its asset type's spec.
    SPEC_PARSE_ERROR = "spec_parse_error"
    #: A prompt template using `{}` or `{0}` -- unrenderable from a
    #: named-variable declaration.
    POSITIONAL_PLACEHOLDER = "positional_placeholder"
    #: A `{placeholder}` in the template with no matching declared
    #: variable: the "missing variables" case this mission names.
    MISSING_VARIABLE_DECLARATION = "missing_variable_declaration"
    #: A variable declared `required=True` that the template never uses.
    UNUSED_REQUIRED_VARIABLE = "unused_required_variable"
    #: A tool name that is not a real tool in this platform.
    UNAPPROVED_TOOL = "unapproved_tool"
    #: An agent granting itself a tool the live specialist of the same
    #: name does not hold.
    TOOL_PRIVILEGE_ESCALATION = "tool_privilege_escalation"
    #: An agent claiming a stronger truth-level ceiling than the live
    #: specialist of the same name.
    TRUTH_LEVEL_ESCALATION = "truth_level_escalation"
    #: A model name this deployment is not configured to allow.
    UNKNOWN_MODEL = "unknown_model"
    #: An evaluation suite that does not exist.
    UNKNOWN_EVALUATION_SUITE = "unknown_evaluation_suite"
    #: The template no longer carries a safety invariant it declares, or
    #: one its own live counterpart carries.
    DROPPED_SAFETY_INVARIANT = "dropped_safety_invariant"
    #: The template contains an instruction that actively subverts a
    #: control (`governance_center.safety.WEAKENING_PATTERNS`).
    UNSAFE_COMPOSITION = "unsafe_composition"
    #: The template mentions a known prompt-injection phrasing
    #: (`security.injection_patterns`). **A warning, never an error** --
    #: see `_scan_for_injection_mentions` for why a blocking check here
    #: was wrong.
    INJECTION_PHRASE_IN_TEMPLATE = "injection_phrase_in_template"
    #: A policy rule naming no subject, or an empty rule set.
    EMPTY_POLICY = "empty_policy"


#: Which codes block. Everything else is reported as a WARNING.
#:
#: `MISSING_VARIABLE_DECLARATION` blocks because an undeclared
#: placeholder is a render-time `KeyError` waiting to happen, which for a
#: prompt means an un-rendered `{placeholder}` reaching a model verbatim.
#: `UNUSED_REQUIRED_VARIABLE` does not block: the declaration is dead,
#: which is untidy but harmless.
_ERROR_CODES: frozenset[ViolationCode] = frozenset(
    {
        ViolationCode.UNTYPED_ASSET_TYPE,
        ViolationCode.SPEC_PARSE_ERROR,
        ViolationCode.POSITIONAL_PLACEHOLDER,
        ViolationCode.MISSING_VARIABLE_DECLARATION,
        ViolationCode.UNAPPROVED_TOOL,
        ViolationCode.TOOL_PRIVILEGE_ESCALATION,
        ViolationCode.TRUTH_LEVEL_ESCALATION,
        ViolationCode.UNKNOWN_MODEL,
        ViolationCode.UNSAFE_COMPOSITION,
        ViolationCode.EMPTY_POLICY,
    }
)


class ArtifactViolation(BaseModel):
    """One problem found in a candidate artifact version.

    Attributes:
        code: Which problem.
        severity: Whether it blocks.
        subject: The specific name the problem is about (a variable, a
            tool, a model, an invariant, a pattern name) -- empty when
            the problem is about the artifact as a whole.
        message: A short, reviewer-readable sentence. Never echoes the
            artifact's own `config` payload back (a template can contain
            anything a caller pasted, including something that should not
            land in a log line).
    """

    model_config = ConfigDict(frozen=True)

    code: ViolationCode
    severity: ValidationSeverity
    subject: str = ""
    message: str


class ArtifactValidationReport(BaseModel):
    """The result of validating one candidate artifact version.

    Attributes:
        asset_type: What was validated.
        asset_key: Which asset.
        violations: Every problem found, errors first then warnings, each
            group ordered by code then subject so a report is stable
            across runs (tests compare them, and an unstable order would
            make a reviewer's diff noisy).
        compared_against_live: Whether a live in-code counterpart was
            found and compared. `False` means this artifact names no
            live equivalent, so only its self-consistency was checked --
            surfaced explicitly rather than letting a clean report imply
            a comparison that never happened.
        truth_level: Always `DATABASE_FACT`: a deterministic finding
            about the submitted payload's own structure, not a prediction
            about how the artifact would behave. Approval remains a
            human judgment (`GovernanceStage`).
    """

    model_config = ConfigDict(frozen=True)

    asset_type: GovernedAssetType
    asset_key: str
    violations: tuple[ArtifactViolation, ...] = ()
    compared_against_live: bool = False

    @property
    def truth_level(self) -> DataTruthLevel:
        return DataTruthLevel.DATABASE_FACT

    @property
    def errors(self) -> tuple[ArtifactViolation, ...]:
        return tuple(v for v in self.violations if v.severity is ValidationSeverity.ERROR)

    @property
    def warnings(self) -> tuple[ArtifactViolation, ...]:
        return tuple(v for v in self.violations if v.severity is ValidationSeverity.WARNING)

    @property
    def is_valid(self) -> bool:
        """True only when nothing blocking was found -- see this module's
        own "no partial trust" note."""
        return not self.errors


class ValidationContext(BaseModel):
    """Everything the validator needs, supplied by its caller.

    Built once per request by `api/governance.py` from already-loaded
    state. Every collection defaults to empty, and an empty collection
    *disables* its own check rather than failing every name against it --
    a caller that cannot determine the real model list must not thereby
    reject every model (that would turn a configuration gap into a
    governance outage). The one exception is the safety checks, which
    need no context at all and therefore always run.

    Attributes:
        known_tools: Every real tool name in this platform.
        known_models: Every model name this deployment allows.
        known_evaluation_suites: Every evaluation suite that exists.
        live_prompt_invariants: `asset_key` -> the invariants the live
            in-code prompt of that key carries. A candidate under a key
            present here must not drop any of them, even if it declares
            none itself -- this is what makes "a prompt change cannot
            weaken security" hold for an artifact whose author simply
            omitted the declaration.
        live_agent_tools: `asset_key` -> the live specialist's own
            allowed-tool set.
        live_agent_truth_levels: `asset_key` -> the live specialist's own
            truth-level ceiling.
    """

    model_config = ConfigDict(frozen=True)

    known_tools: frozenset[str] = frozenset()
    known_models: frozenset[str] = frozenset()
    known_evaluation_suites: frozenset[str] = frozenset()
    live_prompt_invariants: dict[str, frozenset[SafetyInvariant]] = {}
    live_agent_tools: dict[str, frozenset[str]] = {}
    live_agent_truth_levels: dict[str, DataTruthLevel] = {}


#: Ordering of `DataTruthLevel` from weakest to strongest claim, so a
#: candidate agent's ceiling can be compared against the live one. Kept
#: here rather than on `agent.provenance` because "stronger than" is this
#: validator's own question: the vocabulary itself is deliberately a
#: plain set of labels, and adding an ordering to it would imply every
#: consumer agrees on one.
_TRUTH_RANK: dict[DataTruthLevel, int] = {
    DataTruthLevel.AI_INFERENCE: 0,
    DataTruthLevel.DATABASE_FACT: 1,
    DataTruthLevel.CONFIRMED_BUSINESS_TRUTH: 2,
}


def _severity(code: ViolationCode) -> ValidationSeverity:
    return ValidationSeverity.ERROR if code in _ERROR_CODES else ValidationSeverity.WARNING


def _violation(code: ViolationCode, message: str, subject: str = "") -> ArtifactViolation:
    return ArtifactViolation(code=code, severity=_severity(code), subject=subject, message=message)


def _validate_prompt(
    asset_key: str, spec: PromptArtifactSpec, context: ValidationContext
) -> tuple[list[ArtifactViolation], bool]:
    """Validates a prompt spec. Returns `(violations, compared_against_live)`."""
    violations: list[ArtifactViolation] = []

    # --- Variables -------------------------------------------------
    try:
        named, positional = extract_template_variables(spec.template)
    except ArtifactSpecError as exc:
        # An unparseable template cannot be variable-checked at all, and
        # must not be reported as clean.
        return [_violation(ViolationCode.SPEC_PARSE_ERROR, str(exc))], False
    for placeholder in positional:
        violations.append(
            _violation(
                ViolationCode.POSITIONAL_PLACEHOLDER,
                f"Template uses the positional placeholder {placeholder}, which cannot be "
                "filled from a named variable declaration.",
                subject=placeholder,
            )
        )
    declared = {variable.name for variable in spec.variables}
    for name in sorted(named - declared):
        violations.append(
            _violation(
                ViolationCode.MISSING_VARIABLE_DECLARATION,
                f"Template references {{{name}}} but declares no variable named {name!r}.",
                subject=name,
            )
        )
    for variable in sorted(spec.variables, key=lambda item: item.name):
        if variable.required and variable.name not in named:
            violations.append(
                _violation(
                    ViolationCode.UNUSED_REQUIRED_VARIABLE,
                    f"Variable {variable.name!r} is declared required but the template "
                    "never uses it.",
                    subject=variable.name,
                )
            )

    # --- Safety: dropped invariants --------------------------------
    # Union of what this version declares and what the live prompt of the
    # same key carries. Taking the union is the whole point: an author who
    # declares nothing cannot thereby escape the live prompt's controls.
    live_required = context.live_prompt_invariants.get(asset_key, frozenset())
    compared = asset_key in context.live_prompt_invariants
    required = frozenset(spec.safety_invariants) | live_required
    present = invariants_present(spec.template)
    for invariant in sorted(required - present, key=lambda item: item.value):
        from_live = invariant in live_required and invariant not in set(spec.safety_invariants)
        origin = (
            "carried by the live prompt of the same key"
            if from_live
            else "declared by this version"
        )
        blocking = invariant in SECURITY_INVARIANTS
        violations.append(
            ArtifactViolation(
                code=ViolationCode.DROPPED_SAFETY_INVARIANT,
                # A dropped *security* invariant blocks; a dropped quality
                # one is reported. Set explicitly here rather than through
                # `_severity`, because this one code spans both classes.
                severity=(ValidationSeverity.ERROR if blocking else ValidationSeverity.WARNING),
                subject=invariant.value,
                message=(
                    f"Template no longer carries the {invariant.value!r} safety invariant "
                    f"({origin})."
                ),
            )
        )

    # --- Safety: active subversion ---------------------------------
    for pattern_name in detect_weakening(spec.template):
        violations.append(
            _violation(
                ViolationCode.UNSAFE_COMPOSITION,
                f"Template contains an instruction matching {pattern_name!r}, which would "
                "weaken a control this platform enforces.",
                subject=pattern_name,
            )
        )
    violations.extend(_scan_for_injection_mentions(spec.template))

    violations.extend(_validate_tools(spec.required_tools, context))
    violations.extend(_validate_models(spec.compatible_models, context))
    violations.extend(_validate_suites(spec.evaluation_suites, context))
    return violations, compared


#: Spans in quotes (single, double, or backtick) are stripped before the
#: injection-mention scan -- see `_scan_for_injection_mentions`.
_QUOTED_SPAN_RE = re.compile(r"'[^'\n]{0,200}'|\"[^\"\n]{0,200}\"|`[^`\n]{0,200}`")


def _scan_for_injection_mentions(template: str) -> list[ArtifactViolation]:
    """Flags a template that mentions a canonical prompt-injection phrasing.

    **Always a WARNING, never an error, and that is a deliberate
    correction of a bug this check shipped with in draft.** Running
    `security.injection_patterns.INJECTION_PATTERNS` as a blocking check
    over prompt *templates* rejected this codebase's own most
    security-conscious prompt: `agent.llm_client._system_prompt` quotes
    the attacks it defends against ("including things like 'ignore
    previous instructions', 'you are now in developer mode'") and
    prohibits writing a "connection string" into SQL. All three matches
    were defensive mentions. That pattern set is tuned for *untrusted
    user input*, where a match means someone is attempting the attack;
    an authored prompt that discusses security is the opposite context,
    and no fixed pattern list can reliably tell "quote the attack to
    forbid it" from "issue the attack".

    So two things changed from the draft: quoted spans are stripped
    first (removing the dominant legitimate-mention case outright), and
    what remains is a reviewer signal rather than a refusal.
    `ViolationCode.UNSAFE_COMPOSITION` carries the blocking weight
    instead -- its patterns are phrased around imperative instructions
    and matched none of the fifteen live prompts, while correctly
    catching a genuinely weakened template.

    Reuses the exact same pattern dict `agent/input_guard.py` applies to
    typed questions -- never a second, drifting copy.

    Args:
        template: The candidate prompt template.

    Returns:
        One warning per matching pattern, in sorted pattern-name order.
    """
    scanned = _QUOTED_SPAN_RE.sub(" ", template)
    return [
        _violation(
            ViolationCode.INJECTION_PHRASE_IN_TEMPLATE,
            f"Template mentions a known prompt-injection phrasing ({pattern_name!r}) "
            "outside of quotes. Confirm this is defensive wording (naming an attack in "
            "order to forbid it) and not an instruction.",
            subject=pattern_name,
        )
        for pattern_name, pattern in sorted(INJECTION_PATTERNS.items())
        if pattern.search(scanned)
    ]


def _validate_agent(
    asset_key: str, spec: AgentArtifactSpec, context: ValidationContext
) -> tuple[list[ArtifactViolation], bool]:
    """Validates an agent spec. Returns `(violations, compared_against_live)`."""
    violations: list[ArtifactViolation] = list(_validate_tools(spec.allowed_tools, context))
    violations.extend(_validate_models(spec.compatible_models, context))
    violations.extend(_validate_suites(spec.evaluation_suites, context))

    compared = False
    live_tools = context.live_agent_tools.get(asset_key)
    if live_tools is not None:
        compared = True
        for tool in sorted(frozenset(spec.allowed_tools) - live_tools):
            violations.append(
                _violation(
                    ViolationCode.TOOL_PRIVILEGE_ESCALATION,
                    f"Grants tool {tool!r}, which the live {asset_key!r} specialist does "
                    "not hold.",
                    subject=tool,
                )
            )
    live_ceiling = context.live_agent_truth_levels.get(asset_key)
    if live_ceiling is not None:
        compared = True
        candidate_rank = _TRUTH_RANK.get(spec.max_truth_level, 0)
        live_rank = _TRUTH_RANK.get(live_ceiling, 0)
        if candidate_rank > live_rank:
            violations.append(
                _violation(
                    ViolationCode.TRUTH_LEVEL_ESCALATION,
                    f"Claims truth-level ceiling {spec.max_truth_level.value!r}, stronger "
                    f"than the live {asset_key!r} specialist's {live_ceiling.value!r}.",
                    subject=spec.max_truth_level.value,
                )
            )
    return violations, compared


def _validate_policy(
    spec: AnalyticalPolicyArtifactSpec, context: ValidationContext
) -> tuple[list[ArtifactViolation], bool]:
    """Validates an analytical-policy spec."""
    violations: list[ArtifactViolation] = []
    if not spec.rules:
        violations.append(
            _violation(
                ViolationCode.EMPTY_POLICY,
                "A policy version must declare at least one rule.",
            )
        )
    violations.extend(_validate_suites(spec.evaluation_suites, context))
    return violations, False


def _validate_tools(tools: tuple[str, ...], context: ValidationContext) -> list[ArtifactViolation]:
    """Reports every tool name that is not a real tool in this platform."""
    if not context.known_tools:
        return []
    return [
        _violation(
            ViolationCode.UNAPPROVED_TOOL,
            f"Names tool {tool!r}, which is not a tool this platform provides.",
            subject=tool,
        )
        for tool in sorted(frozenset(tools) - context.known_tools)
    ]


def _validate_models(
    models: tuple[str, ...], context: ValidationContext
) -> list[ArtifactViolation]:
    """Reports every model this deployment is not configured to allow."""
    if not context.known_models:
        return []
    return [
        _violation(
            ViolationCode.UNKNOWN_MODEL,
            f"Names model {model!r}, which this deployment is not configured to allow.",
            subject=model,
        )
        for model in sorted(frozenset(models) - context.known_models)
    ]


def _validate_suites(
    suites: tuple[str, ...], context: ValidationContext
) -> list[ArtifactViolation]:
    """Reports every evaluation suite that does not exist."""
    if not context.known_evaluation_suites:
        return []
    return [
        _violation(
            ViolationCode.UNKNOWN_EVALUATION_SUITE,
            f"Claims evaluation suite {suite!r}, which does not exist.",
            subject=suite,
        )
        for suite in sorted(frozenset(suites) - context.known_evaluation_suites)
    ]


def _sorted_violations(violations: list[ArtifactViolation]) -> tuple[ArtifactViolation, ...]:
    """Errors first, then warnings, each group by code then subject."""
    return tuple(
        sorted(
            violations,
            key=lambda item: (
                0 if item.severity is ValidationSeverity.ERROR else 1,
                item.code.value,
                item.subject,
            ),
        )
    )


def validate_artifact_spec(
    asset_type: GovernedAssetType,
    asset_key: str,
    spec: ArtifactSpec,
    context: ValidationContext,
) -> ArtifactValidationReport:
    """Validates an already-parsed spec.

    Args:
        asset_type: The version's asset type.
        asset_key: The version's asset key, used to find the live
            counterpart to compare against.
        spec: The parsed spec.
        context: Everything needed for the checks -- see
            `ValidationContext`.

    Returns:
        The report. `is_valid` is `False` if any `ERROR` was found.
    """
    if isinstance(spec, PromptArtifactSpec):
        violations, compared = _validate_prompt(asset_key, spec, context)
    elif isinstance(spec, AgentArtifactSpec):
        violations, compared = _validate_agent(asset_key, spec, context)
    else:
        violations, compared = _validate_policy(spec, context)
    return ArtifactValidationReport(
        asset_type=asset_type,
        asset_key=asset_key,
        violations=_sorted_violations(violations),
        compared_against_live=compared,
    )


def validate_artifact_config(
    asset_type: GovernedAssetType,
    asset_key: str,
    config: dict,
    context: ValidationContext,
) -> ArtifactValidationReport:
    """Parses `config` and validates it, in one call.

    The entry point `api/governance.py` uses: a version's stored `config`
    is raw JSON, so parsing and validating are always wanted together.

    Args:
        asset_type: The version's asset type. An asset type with no typed
            spec yields a single `UNTYPED_ASSET_TYPE` error -- never a
            clean report, see `ViolationCode.UNTYPED_ASSET_TYPE`.
        asset_key: The version's asset key.
        config: The raw `config` payload.
        context: Validation context.

    Returns:
        The report. A parse failure yields exactly one `SPEC_PARSE_ERROR`
        violation (the spec could not be read, so no other check could
        run) rather than a partial list that might read as "only one
        problem".
    """
    if asset_type not in TYPED_ASSET_TYPES:
        return ArtifactValidationReport(
            asset_type=asset_type,
            asset_key=asset_key,
            violations=(
                _violation(
                    ViolationCode.UNTYPED_ASSET_TYPE,
                    f"Asset type {asset_type.value!r} has no typed specification, so its "
                    "configuration cannot be validated.",
                ),
            ),
        )
    try:
        spec = parse_artifact_spec(asset_type, config)
    except ArtifactSpecError as exc:
        return ArtifactValidationReport(
            asset_type=asset_type,
            asset_key=asset_key,
            violations=(_violation(ViolationCode.SPEC_PARSE_ERROR, str(exc)),),
        )
    return validate_artifact_spec(asset_type, asset_key, spec, context)


def requires_validation(asset_type: GovernedAssetType) -> bool:
    """True when `asset_type` has a typed spec and must therefore be
    validated before a lifecycle action proceeds.

    `api/governance.py` uses this to leave the other four asset types'
    behavior exactly as Prompt 44 shipped it.
    """
    return asset_type in TYPED_ASSET_TYPES


__all__ = [
    "ArtifactViolation",
    "ArtifactValidationReport",
    "ValidationContext",
    "ValidationSeverity",
    "ViolationCode",
    "requires_validation",
    "validate_artifact_config",
    "validate_artifact_spec",
]
