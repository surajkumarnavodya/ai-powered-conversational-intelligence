"""Typed artifact specifications for the three governed asset kinds this
registry can reason about in detail -- Prompt 45
(`45_PROMPT_AGENT_REGISTRY_CONTRACT.md`).

## Why this exists at all

Prompt 44 (`governance_center/models.py`) deliberately made
`GovernanceAssetVersionSnapshot.config` a free-form JSON payload that is
"stored and displayed, never evaluated." That was the right call for a
lifecycle layer that must work for all seven asset types without
pretending to understand any of them. But it means nothing could check
whether a prompt's declared variables actually appear in its template,
whether an agent's tool list names real tools, or whether a version
targets a model this deployment can even run.

This module adds that typed reading for exactly three asset types --
`PROMPT`, `AGENT`, and `POLICY` (`TYPED_ASSET_TYPES`) -- and changes
nothing about the other four. A `MODEL`/`PROVIDER`/`TOOL`/
`RETRIEVAL_SOURCE` version's `config` keeps Prompt 44's original
free-form, never-parsed behavior exactly, so no existing asset's
behavior changes.

## What is still never evaluated

Parsing is not execution. This module reads `config` into typed,
validated, **frozen** values and nothing more: no template is rendered,
no rule is executed, no tool is invoked, no model is called. A
`PolicyRule` is a `(subject, operator, value)` triple rather than a
formula string, for the identical structural reason
`optimization/models.py` and `analytics/scenario.py` use numeric
`(variable, coefficient)` pairs -- there is no field here an LLM-authored
expression could occupy, so "never let a model invent a constraint"
holds by shape, not by a filter that could be bypassed.

## Relationship to the live code

A spec here describes a *candidate* artifact under review. It is **not**
loaded by the runtime: `agent/llm_client.py` still uses its own in-code
prompt constants, and `agent/multiagent/agents.py` still uses its own
`AGENT_SPECS`. See `governance_center/inventory.py` for how the live
in-code artifacts are surfaced *as* specs for comparison, and that
module's own docstring for why the registry deliberately does not
hot-swap a prompt from a database row.
"""

from __future__ import annotations

from enum import Enum
from string import Formatter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.provenance import DataTruthLevel
from governance_center.models import GovernedAssetType
from governance_center.safety import SafetyInvariant

#: The asset types this module gives a typed reading. Every other
#: `GovernedAssetType` keeps Prompt 44's free-form `config` behavior
#: untouched -- see this module's own docstring.
TYPED_ASSET_TYPES: frozenset[GovernedAssetType] = frozenset(
    {
        GovernedAssetType.PROMPT,
        GovernedAssetType.AGENT,
        GovernedAssetType.POLICY,
    }
)


class ArtifactSpecError(ValueError):
    """A `config` payload that cannot be read as its asset type's spec.

    Deliberately a plain `ValueError` subclass rather than an
    `agent.exceptions.AgentError`: this is a caller-input problem
    surfaced as an HTTP 422 by `api/governance.py`, not a pipeline
    failure with a safe-message/internal-message split.
    """


class PromptVariable(BaseModel):
    """One placeholder a prompt template expects to be filled at render
    time.

    Attributes:
        name: The placeholder's name, matching a `{name}` field in the
            template.
        required: Whether a render without this variable is invalid. A
            declared-but-optional variable may legitimately be absent
            from the template (a conditionally-appended block), which is
            why `governance_center/validation.py` only reports an
            unused *required* variable as an error.
        description: Free text for a human reviewer. Never parsed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    required: bool = True
    description: str = ""


class PolicyRule(BaseModel):
    """One analytical-policy rule, as a typed triple rather than an
    expression string.

    See this module's own docstring for why this shape has no field an
    LLM-authored formula could occupy.

    Attributes:
        subject: What the rule constrains (e.g. a setting name, a metric
            name, a category). Never code.
        operator: The comparison, from a closed set.
        value: The threshold/target. A primitive, never an expression.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str = Field(min_length=1, max_length=200)
    operator: str = Field(pattern=r"^(?:==|!=|<=|>=|<|>|in|not_in)$")
    value: str | int | float | bool | list[str]


class PromptArtifactSpec(BaseModel):
    """A governed prompt template version.

    Covers every tracking dimension this mission names for a prompt:
    its text, the variables it expects, the tools whose output it
    assumes, the models it is compatible with, the evaluation suites
    that exercise it, and the safety invariants it must preserve.
    `owner`/`version`/deployment status are not duplicated here -- they
    already live on `identity.models.GovernanceAssetVersion` (Prompt 44)
    and would drift if restated.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    template: str = Field(min_length=1)
    variables: tuple[PromptVariable, ...] = ()
    required_tools: tuple[str, ...] = ()
    compatible_models: tuple[str, ...] = ()
    evaluation_suites: tuple[str, ...] = ()
    #: Which `SafetyInvariant`s this template is required to carry.
    #: `governance_center/validation.py` refuses a version whose
    #: template no longer carries one listed here -- the mechanism
    #: behind "a prompt change cannot weaken security."
    safety_invariants: tuple[SafetyInvariant, ...] = ()


class AgentArtifactSpec(BaseModel):
    """A governed agent-definition version.

    Mirrors `agent.multiagent.contracts.AgentSpec`'s own fields by name
    (`allowed_tools`/`max_truth_level`/`cost_units`/`max_attempts`) so
    `governance_center/validation.py` can compare a candidate against
    the live in-code spec field by field without a translation layer
    that could silently misalign.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_name: str = Field(min_length=1, max_length=120)
    #: The closed set of kinds the live `agent.multiagent.agents.SPECS`
    #: actually uses. Note `AgentSpec.kind`'s own docstring lists only
    #: four ("llm"/"data"/"engine"/"control") and has gone stale --
    #: `semantic` is a real, live `"retrieval"` agent. Taken from the
    #: live specs rather than that docstring, and pinned by
    #: `tests/test_governance_inventory.py` so the next added kind fails
    #: here loudly instead of being silently rejected at review time.
    kind: str = Field(pattern=r"^(?:llm|data|engine|control|retrieval)$")
    allowed_tools: tuple[str, ...] = ()
    max_truth_level: DataTruthLevel = DataTruthLevel.AI_INFERENCE
    cost_units: int = Field(default=1, ge=0, le=1000)
    max_attempts: int = Field(default=1, ge=1, le=10)
    compatible_models: tuple[str, ...] = ()
    evaluation_suites: tuple[str, ...] = ()


class AnalyticalPolicyArtifactSpec(BaseModel):
    """A governed analytical-policy version.

    Attributes:
        policy_kind: Which policy family this is (e.g.
            `"retry_budget"`, `"cost_ceiling"`, `"truth_level_ceiling"`).
            Free text by design -- this registry governs a policy's
            review lifecycle, and inventing a closed set here would
            either exclude a real policy or go stale.
        rules: The typed rules -- see `PolicyRule`.
        applies_to: Which asset keys/agents/engines this policy governs.
        evaluation_suites: Suites that exercise this policy.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_kind: str = Field(min_length=1, max_length=120)
    rules: tuple[PolicyRule, ...] = ()
    applies_to: tuple[str, ...] = ()
    evaluation_suites: tuple[str, ...] = ()


#: Any one of the three typed specs.
ArtifactSpec = PromptArtifactSpec | AgentArtifactSpec | AnalyticalPolicyArtifactSpec


_SPEC_MODELS: dict[GovernedAssetType, type[BaseModel]] = {
    GovernedAssetType.PROMPT: PromptArtifactSpec,
    GovernedAssetType.AGENT: AgentArtifactSpec,
    GovernedAssetType.POLICY: AnalyticalPolicyArtifactSpec,
}


class TemplateVariableKind(str, Enum):
    """How a `{...}` field in a template was written.

    `POSITIONAL` exists to be rejected rather than silently tolerated: a
    template using `{}` or `{0}` cannot be rendered from a named-variable
    declaration at all, so it is a malformed prompt artifact, not a
    stylistic choice.
    """

    NAMED = "named"
    POSITIONAL = "positional"


def extract_template_variables(template: str) -> tuple[frozenset[str], tuple[str, ...]]:
    """Finds every `{placeholder}` a template actually contains.

    Uses `string.Formatter().parse` -- the same parser `str.format` itself
    uses -- rather than a hand-rolled regex, so doubled escape braces
    (`{{"json": "literal"}}`, which several of this codebase's own live
    prompts contain) are correctly *not* reported as variables, and an
    attribute/index access (`{cfg.name}`, `{rows[0]}`) resolves to its
    root name.

    Args:
        template: The raw prompt template text.

    Returns:
        A `(named, positional)` pair: the set of named placeholder root
        names, and the positional placeholders found (each rendered as
        it appeared, for an error message). A well-formed named template
        yields an empty `positional`.

    Raises:
        ArtifactSpecError: If the template is not parseable as a format
            string at all (e.g. a single unmatched brace). Deliberately
            an error rather than "no variables found": an unparseable
            template would silently pass every variable check otherwise.
    """
    named: set[str] = set()
    positional: list[str] = []
    try:
        fields = list(Formatter().parse(template))
    except ValueError as exc:
        raise ArtifactSpecError(f"Template is not a valid format string: {exc}") from exc
    for _literal, field_name, _format_spec, _conversion in fields:
        if field_name is None:
            continue
        if field_name == "":
            positional.append("{}")
            continue
        # "cfg.name" / "rows[0]" -> "cfg" / "rows"; "0" -> positional.
        root = field_name.split(".")[0].split("[")[0]
        if root.isdigit():
            positional.append("{" + field_name + "}")
            continue
        if root:
            named.add(root)
    return frozenset(named), tuple(positional)


def parse_artifact_spec(asset_type: GovernedAssetType, config: dict[str, Any]) -> ArtifactSpec:
    """Reads one asset version's `config` as its asset type's typed spec.

    Args:
        asset_type: The version's own asset type. Must be in
            `TYPED_ASSET_TYPES`.
        config: The raw `config` payload, exactly as stored on
            `identity.models.GovernanceAssetVersion`.

    Returns:
        The parsed, frozen spec.

    Raises:
        ArtifactSpecError: If `asset_type` has no typed spec, if `config`
            is not a mapping, or if it fails the spec's own validation
            (including an unknown key -- every spec sets
            `extra="forbid"`, so a typo'd field name is a refusal rather
            than a silently-ignored value a reviewer would never notice).
    """
    model = _SPEC_MODELS.get(asset_type)
    if model is None:
        raise ArtifactSpecError(
            f"Asset type {asset_type.value!r} has no typed specification "
            f"(typed types: {', '.join(sorted(t.value for t in TYPED_ASSET_TYPES))})."
        )
    if not isinstance(config, dict):
        raise ArtifactSpecError(
            f"config for a {asset_type.value!r} version must be a JSON object, "
            f"got {type(config).__name__}."
        )
    try:
        parsed = model.model_validate(config)
    except ValidationError as exc:
        raise ArtifactSpecError(_format_validation_error(asset_type, exc)) from exc
    # Narrowing for mypy: `_SPEC_MODELS` only ever holds the three spec
    # classes, which is exactly what `ArtifactSpec` unions.
    assert isinstance(parsed, PromptArtifactSpec | AgentArtifactSpec | AnalyticalPolicyArtifactSpec)
    return parsed


def _format_validation_error(asset_type: GovernedAssetType, exc: ValidationError) -> str:
    """Renders a pydantic error as one short, reviewer-readable sentence.

    Keeps the field path and message but drops pydantic's URLs and input
    echo -- the input is a caller's own payload, and echoing it back into
    an error string is how a secret pasted into the wrong field ends up
    in a log.
    """
    parts = []
    for error in exc.errors():
        location = ".".join(str(piece) for piece in error.get("loc", ())) or "(root)"
        parts.append(f"{location}: {error.get('msg', 'invalid')}")
    return f"Invalid {asset_type.value} specification -- " + "; ".join(parts)


__all__ = [
    "TYPED_ASSET_TYPES",
    "ArtifactSpecError",
    "PromptVariable",
    "PolicyRule",
    "PromptArtifactSpec",
    "AgentArtifactSpec",
    "AnalyticalPolicyArtifactSpec",
    "ArtifactSpec",
    "TemplateVariableKind",
    "extract_template_variables",
    "parse_artifact_spec",
]
