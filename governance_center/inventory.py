"""The live inventory of this platform's own prompt templates, agent
definitions and analytical policies -- Prompt 45
(`45_PROMPT_AGENT_REGISTRY_CONTRACT.md`), whose mission begins
"inventory existing prompt templates and agent definitions first."

## Read from the real modules, never copied

Every entry below resolves its content from the **live** object at call
time: a prompt entry imports the real module and reads the real constant,
and an agent entry reads the real `agent.multiagent.agents.SPECS`
dict. Nothing here is a transcription that could drift. That is the whole
point -- `tests/security/test_prompt_registry_cannot_weaken_security.py`
asserts that each live prompt still carries the safety invariants this
inventory says it carries, so editing `agent/llm_client.py` to drop the
untrusted-data framing fails the suite instead of shipping quietly.

Imports are deferred into each resolver (never at module import time) so
importing this module stays cheap and side-effect-free -- several of the
modules holding these prompts pull in LangGraph/Chroma/Ollama transitively.

## Why the registry does not hot-swap a live prompt

This module surfaces live artifacts *as* specs so a governed candidate
version can be compared against what is actually running. It deliberately
does not go the other way: no code path anywhere loads a deployed
`GovernanceAssetVersion` row and uses it as a system prompt. Doing that
would make a database row the system prompt, turning every write path
into the registry (and every mistake in review) into prompt-injection
with full pipeline reach -- the precise opposite of what a governance
layer is for. The registry governs review, approval and rollback of
candidate artifacts, and detects drift in live ones; promoting a
reviewed artifact into the runtime remains a code change, deliberately.

## Truth level

Every entry is `DataTruthLevel.DATABASE_FACT`. An inventory entry is a
deterministic observation about this repository's own source -- the same
honest classification `lineage/sql_lineage.py` uses for a deterministic
parse of SQL text, and `data_quality/` for a measured null rate. It is
emphatically **not** a claim that the artifact is correct, approved, or
safe; approval is `GovernanceStage`'s job (Prompt 44).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.provenance import DataTruthLevel
from governance_center.artifacts import (
    AgentArtifactSpec,
    AnalyticalPolicyArtifactSpec,
    ArtifactSpec,
    PolicyRule,
    PromptArtifactSpec,
    PromptVariable,
    extract_template_variables,
)
from governance_center.models import GovernedAssetType
from governance_center.safety import SafetyInvariant, invariants_present

logger = logging.getLogger(__name__)

#: The `DB_TYPE` used when fingerprinting the one live prompt that is
#: built by a function rather than stored as a constant
#: (`agent.llm_client._system_prompt`, which interpolates an engine
#: name). Fixing it keeps the fingerprint deterministic; the engine name
#: is the only part that varies, and no safety invariant depends on it.
FINGERPRINT_DB_TYPE = "mssql"


class InventoryEntry(BaseModel):
    """One live, in-code artifact, described as a governed spec.

    Attributes:
        asset_type: Which `GovernedAssetType` this artifact would be
            registered as.
        asset_key: The stable key a governed version of this artifact
            should use, so an inventory entry and its registry versions
            line up.
        display_name: Human label.
        source_module: The module the live object lives in.
        source_symbol: The live object's own name in that module.
        spec: The artifact rendered as its typed spec.
        fingerprint: `sha256` over the artifact's own canonical content,
            truncated to 16 hex characters. Comparable across runs;
            changes whenever the live artifact changes.
        declared_invariants: For a prompt, the invariants its live text
            was verified to carry at the time this inventory was
            written. The regression suite requires these to still be
            present -- see this module's own docstring.
        notes: Any honest caveat about this specific entry.
    """

    model_config = ConfigDict(frozen=True)

    asset_type: GovernedAssetType
    asset_key: str
    display_name: str
    source_module: str
    source_symbol: str
    spec: ArtifactSpec
    fingerprint: str
    declared_invariants: tuple[SafetyInvariant, ...] = ()
    notes: str = ""

    @property
    def truth_level(self) -> DataTruthLevel:
        """See this module's own docstring for why this is a fact, not an
        approval signal."""
        return DataTruthLevel.DATABASE_FACT


def fingerprint_text(text: str) -> str:
    """Returns a short, stable content fingerprint for `text`.

    Reuses the same `sha256`-prefix convention `db.schema_introspection
    .get_schema_fingerprint` and `agent.nodes._query_fingerprint` already
    use, rather than inventing a third hashing style.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _prompt_variables(template: str) -> tuple[PromptVariable, ...]:
    """Declares one required variable per named placeholder in `template`.

    A live prompt has no separate declaration to read -- its placeholders
    *are* its contract -- so the inventory derives them instead of
    asserting a hand-written list that could drift.
    """
    named, _positional = extract_template_variables(template)
    return tuple(PromptVariable(name=name) for name in sorted(named))


#: Each live prompt: a stable asset key -> (display name, module,
#: symbol, resolver, notes). The resolver returns the live text.
#:
#: `declared_invariants` is NOT listed here -- it is derived from the
#: live text by `invariants_present`, so the inventory can never claim a
#: prompt carries something it does not. The regression suite is what
#: pins the *expected* set, so a removal shows up as a failing test
#: there (with a readable diff) rather than as a silently-shrinking
#: inventory field.
_PROMPT_SOURCES: tuple[tuple[str, str, str, str, Callable[[], str], str], ...] = (
    (
        "sql_generation",
        "Text-to-SQL generation",
        "agent.llm_client",
        "_system_prompt",
        lambda: _resolve("agent.llm_client", "_system_prompt")(FINGERPRINT_DB_TYPE),
        "Built by a function (it interpolates the target engine name), so the "
        f"fingerprint is taken with DB_TYPE={FINGERPRINT_DB_TYPE!r}. Only the engine "
        "name varies; no safety invariant depends on it.",
    ),
    (
        "sql_insight",
        "Result insight narration",
        "agent.llm_client",
        "_INSIGHT_SYSTEM_PROMPT",
        lambda: _resolve("agent.llm_client", "_INSIGHT_SYSTEM_PROMPT"),
        "Pinned wording: docs/EVALUATION_CURRENT.md's live-LLM benchmark numbers "
        "were measured against this exact text.",
    ),
    (
        "query_plan",
        "Free-text query planning",
        "agent.llm_client",
        "_PLAN_SYSTEM_PROMPT",
        lambda: _resolve("agent.llm_client", "_PLAN_SYSTEM_PROMPT"),
        "",
    ),
    (
        "sql_review",
        "Plan-conformance SQL review",
        "agent.llm_client",
        "_REVIEW_SYSTEM_PROMPT",
        lambda: _resolve("agent.llm_client", "_REVIEW_SYSTEM_PROMPT"),
        "",
    ),
    (
        "metric_conformance_review",
        "Governed-metric conformance review",
        "agent.llm_client",
        "_METRIC_CONFORMANCE_SYSTEM_PROMPT",
        lambda: _resolve("agent.llm_client", "_METRIC_CONFORMANCE_SYSTEM_PROMPT"),
        "Carries GOVERNED_METRIC_PRECEDENCE: this prompt is how Prompt 10's "
        "semantic governance is actually enforced against generated SQL.",
    ),
    (
        "analytical_intent",
        "Analytical intent classification",
        "agent.llm_client",
        "_INTENT_SYSTEM_PROMPT",
        lambda: _resolve("agent.llm_client", "_INTENT_SYSTEM_PROMPT"),
        "",
    ),
    (
        "analytical_plan",
        "Structured analytical planning",
        "agent.llm_client",
        "_ANALYTICAL_PLAN_SYSTEM_PROMPT",
        lambda: _resolve("agent.llm_client", "_ANALYTICAL_PLAN_SYSTEM_PROMPT"),
        "",
    ),
    (
        "analyst_planner",
        "AI Data Analyst sub-question planner",
        "agent.analyst.planner",
        "_PLANNER_SYSTEM_PROMPT",
        lambda: _resolve("agent.analyst.planner", "_PLANNER_SYSTEM_PROMPT"),
        "",
    ),
    (
        "rag_answer",
        "Document/policy RAG answer",
        "rag.graph",
        "_GENERATE_SYSTEM_PROMPT",
        lambda: _resolve("rag.graph", "_GENERATE_SYSTEM_PROMPT"),
        "",
    ),
    (
        "rag_relevance_grade",
        "Document/policy retrieval relevance grading",
        "rag.retriever",
        "_GRADE_SYSTEM_PROMPT",
        lambda: _resolve("rag.retriever", "_GRADE_SYSTEM_PROMPT"),
        "Inventory finding: carries no stated safety invariant, although it does "
        "receive raw document excerpt text. Blast radius is bounded -- it returns "
        "a single YES/NO that only gates retry/rewrite, never reaching generated "
        "SQL or the user's answer. Reported rather than silently reworded: this "
        "pass does not edit live prompt text.",
    ),
    (
        "rag_query_rewrite",
        "Document/policy query rewrite",
        "rag.retriever",
        "_REWRITE_SYSTEM_PROMPT",
        lambda: _resolve("rag.retriever", "_REWRITE_SYSTEM_PROMPT"),
        "Inventory finding: carries no stated safety invariant. Receives document "
        "filenames only (not excerpt bodies), and returns a search string. Same "
        "report-not-rewrite treatment as rag_relevance_grade.",
    ),
    (
        "attachment_qa",
        "Chat-attachment question answering",
        "attachments.graph",
        "_TEXT_ONLY_SYSTEM_PROMPT",
        lambda: _resolve("attachments.graph", "_TEXT_ONLY_SYSTEM_PROMPT"),
        "",
    ),
    (
        "vision_describe",
        "Attached-image description",
        "attachments.vision",
        "_VISION_SYSTEM_PROMPT",
        lambda: _resolve("attachments.vision", "_VISION_SYSTEM_PROMPT"),
        "",
    ),
    (
        "media_caption",
        "Media-library frame captioning",
        "media.captioning",
        "_CAPTION_SYSTEM_PROMPT",
        lambda: _resolve("media.captioning", "_CAPTION_SYSTEM_PROMPT"),
        "",
    ),
    (
        "media_search_answer",
        "Media-search answer composition",
        "agent.orchestrator.nodes",
        "_MEDIA_SEARCH_ANSWER_SYSTEM_PROMPT",
        lambda: _resolve("agent.orchestrator.nodes", "_MEDIA_SEARCH_ANSWER_SYSTEM_PROMPT"),
        "",
    ),
)


def _resolve(module_name: str, symbol: str) -> Any:
    """Imports `module_name` and returns its `symbol`.

    Deferred (called from a resolver, not at import time) so this
    module's own import stays cheap -- see this module's own docstring.

    Raises:
        InventoryUnavailableError: If the module or symbol is gone, which
            means the inventory is describing something that no longer
            exists and must not be reported as live.
    """
    import importlib

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:  # pragma: no cover - a real packaging break
        raise InventoryUnavailableError(f"Cannot import {module_name}: {exc}") from exc
    try:
        return getattr(module, symbol)
    except AttributeError as exc:
        raise InventoryUnavailableError(
            f"{module_name} no longer defines {symbol} -- the inventory is stale."
        ) from exc


class InventoryUnavailableError(RuntimeError):
    """A live artifact the inventory names no longer exists.

    Raised rather than skipped: an inventory that silently drops an entry
    whose source moved would report a shrinking, falsely-clean picture --
    exactly the drift this module exists to detect.
    """


def inventory_prompt_templates() -> tuple[InventoryEntry, ...]:
    """Returns every live prompt template, as governed prompt specs.

    Raises:
        InventoryUnavailableError: If a named prompt constant no longer
            exists (see that exception's own docstring).
    """
    entries: list[InventoryEntry] = []
    for key, display, module_name, symbol, resolver, notes in _PROMPT_SOURCES:
        text = resolver()
        if not isinstance(text, str) or not text.strip():  # pragma: no cover - guards drift
            raise InventoryUnavailableError(
                f"{module_name}.{symbol} did not resolve to prompt text."
            )
        present = tuple(sorted(invariants_present(text), key=lambda item: item.value))
        entries.append(
            InventoryEntry(
                asset_type=GovernedAssetType.PROMPT,
                asset_key=key,
                display_name=display,
                source_module=module_name,
                source_symbol=symbol,
                spec=PromptArtifactSpec(
                    template=text,
                    variables=_prompt_variables(text),
                    safety_invariants=present,
                ),
                fingerprint=fingerprint_text(text),
                declared_invariants=present,
                notes=notes,
            )
        )
    return tuple(entries)


def inventory_agent_definitions() -> tuple[InventoryEntry, ...]:
    """Returns every live multi-agent specialist, as governed agent specs.

    Reads `agent.multiagent.agents.SPECS` directly, so this is the
    live authority rather than a copy of it -- adding a specialist there
    makes it appear here with no edit to this module.
    """
    specs = _resolve("agent.multiagent.agents", "SPECS")
    entries: list[InventoryEntry] = []
    for name, spec in sorted(specs.items(), key=lambda item: str(item[0].value)):
        allowed = tuple(sorted(spec.allowed_tools))
        canonical = "|".join(
            (
                str(name.value),
                spec.kind,
                ",".join(allowed),
                str(spec.max_truth_level.value),
                str(spec.cost_units),
                str(spec.max_attempts),
            )
        )
        entries.append(
            InventoryEntry(
                asset_type=GovernedAssetType.AGENT,
                asset_key=str(name.value),
                display_name=f"{name.value} specialist",
                source_module="agent.multiagent.agents",
                source_symbol="SPECS",
                spec=AgentArtifactSpec(
                    agent_name=str(name.value),
                    kind=spec.kind,
                    allowed_tools=allowed,
                    max_truth_level=spec.max_truth_level,
                    cost_units=spec.cost_units,
                    max_attempts=spec.max_attempts,
                ),
                fingerprint=fingerprint_text(canonical),
                notes="",
            )
        )
    return tuple(entries)


#: Each live analytical policy: a stable key -> (display name, the
#: `Settings` field names whose values make up the policy, policy kind,
#: what it applies to, a note). Values are read from a live `Settings`
#: at call time, never hardcoded here.
_POLICY_SOURCES: tuple[tuple[str, str, str, tuple[str, ...], tuple[str, ...], str], ...] = (
    (
        "sql_retry_budget",
        "Self-correction retry budget",
        "retry_budget",
        ("max_retries", "complex_query_max_retry_bonus"),
        ("sql_generation", "sql_review", "metric_conformance_review"),
        "The bound on the LangGraph self-correction loop (agent/complexity.py "
        "widens max_retries per question within this budget).",
    ),
    (
        "sql_result_bounds",
        "Read-only execution bounds",
        "execution_bounds",
        ("max_result_rows", "query_timeout_seconds"),
        ("sql_generation",),
        "Enforced deterministically in db/execution.py (row cap at the cursor "
        "and a driver-level timeout), never by a prompt.",
    ),
    (
        "schema_retrieval_scope",
        "Schema retrieval scope",
        "retrieval_scope",
        ("schema_top_k", "golden_examples_min_similarity"),
        ("sql_generation",),
        "How much schema and how many golden examples reach the generation " "prompt.",
    ),
    (
        "recommendation_confidence_floor",
        "Recommendation confidence floor",
        "confidence_floor",
        ("recommendation_min_confidence",),
        ("recommendation",),
        "A candidate below this floor is dropped by recommendation/engine.py "
        "even when its evidence check passed.",
    ),
    (
        "root_cause_evidence_floor",
        "Root-cause evidence floor",
        "evidence_floor",
        ("root_cause_min_contribution_percent", "root_cause_significance_alpha"),
        ("root_cause",),
        "What analytics/root_cause.py and analytics/causal.py require before a "
        "contributor becomes a claim rather than a disclosed distractor.",
    ),
    (
        "predictive_data_sufficiency",
        "Predictive data sufficiency",
        "data_sufficiency",
        ("predictive_min_training_rows",),
        ("predictive",),
        "Below this, analytics/predictive.py returns a typed "
        "insufficient-data result rather than a model.",
    ),
)


def inventory_analytical_policies(settings: Any | None = None) -> tuple[InventoryEntry, ...]:
    """Returns every live analytical policy, as governed policy specs.

    Args:
        settings: A `config.settings.Settings`. Defaults to the cached
            process settings, so a caller in a request already holding
            one can pass it rather than re-reading configuration.

    Raises:
        InventoryUnavailableError: If a named `Settings` field no longer
            exists -- the policy inventory would otherwise silently
            describe a threshold nothing reads.
    """
    if settings is None:
        from config.settings import get_settings

        settings = get_settings()
    entries: list[InventoryEntry] = []
    for key, display, kind, fields, applies_to, note in _POLICY_SOURCES:
        rules: list[PolicyRule] = []
        for field in fields:
            if not hasattr(settings, field):
                raise InventoryUnavailableError(
                    f"Settings no longer defines {field!r} -- the policy inventory is stale."
                )
            value = getattr(settings, field)
            if not isinstance(value, str | int | float | bool):  # pragma: no cover - guards drift
                raise InventoryUnavailableError(
                    f"Settings.{field} is not a primitive policy value "
                    f"({type(value).__name__})."
                )
            rules.append(PolicyRule(subject=field, operator="==", value=value))
        canonical = "|".join(f"{rule.subject}={rule.value}" for rule in rules)
        entries.append(
            InventoryEntry(
                asset_type=GovernedAssetType.POLICY,
                asset_key=key,
                display_name=display,
                source_module="config.settings",
                source_symbol="Settings",
                spec=AnalyticalPolicyArtifactSpec(
                    policy_kind=kind,
                    rules=tuple(rules),
                    applies_to=applies_to,
                ),
                fingerprint=fingerprint_text(canonical),
                notes=note,
            )
        )
    return tuple(entries)


class InventoryReport(BaseModel):
    """The full live inventory.

    Attributes:
        prompts: Live prompt templates.
        agents: Live multi-agent specialists.
        policies: Live analytical policies.
        truth_level: Always `DATABASE_FACT` -- see this module's own
            docstring.
    """

    model_config = ConfigDict(frozen=True)

    prompts: tuple[InventoryEntry, ...] = ()
    agents: tuple[InventoryEntry, ...] = ()
    policies: tuple[InventoryEntry, ...] = ()
    truth_level: DataTruthLevel = Field(default=DataTruthLevel.DATABASE_FACT, frozen=True)

    @property
    def entries(self) -> tuple[InventoryEntry, ...]:
        return self.prompts + self.agents + self.policies


def full_inventory(settings: Any | None = None) -> InventoryReport:
    """Returns the complete live inventory of prompts, agents and policies."""
    return InventoryReport(
        prompts=inventory_prompt_templates(),
        agents=inventory_agent_definitions(),
        policies=inventory_analytical_policies(settings),
    )


def live_prompt_entry(asset_key: str) -> InventoryEntry | None:
    """Returns the live prompt inventory entry for `asset_key`, if any.

    Used by `governance_center/validation.py` to compare a candidate
    prompt version against whatever is actually running under the same
    key -- the check that catches a candidate dropping an invariant its
    live counterpart carries, even when the candidate declares none.
    """
    for entry in inventory_prompt_templates():
        if entry.asset_key == asset_key:
            return entry
    return None


def live_agent_entry(asset_key: str) -> InventoryEntry | None:
    """Returns the live agent inventory entry for `asset_key`, if any.

    The counterpart to `live_prompt_entry` for agent definitions: this is
    what lets validation refuse a candidate that grants itself tools or a
    truth-level ceiling beyond the live specialist's own.
    """
    for entry in inventory_agent_definitions():
        if entry.asset_key == asset_key:
            return entry
    return None


def known_tool_names() -> frozenset[str]:
    """Returns every real tool name in this platform.

    The union of two genuinely separate registries, both read live:
    `agent.multiagent.tools`'s `TOOL_*` names (what a supervisor
    specialist may invoke) and `agent.tools.definitions
    .build_default_registry`'s own names (the MCP-shaped abstraction).
    Deliberately a union rather than one or the other -- a governed
    artifact may legitimately reference a tool from either, and
    validating against only one would reject real names.
    """
    import importlib

    # `agent.multiagent` has no `__init__.py` (an implicit namespace
    # package), so importing the parent does not bind the `tools`
    # submodule -- it has to be imported by its own full name.
    tools_module = importlib.import_module("agent.multiagent.tools")
    names = {
        value
        for key, value in vars(tools_module).items()
        if key.startswith("TOOL_") and isinstance(value, str)
    }
    build_default_registry = _resolve("agent.tools.definitions", "build_default_registry")
    names.update(tool.name for tool in build_default_registry().list_tools())
    return frozenset(names)


def known_model_names(settings: Any | None = None) -> frozenset[str]:
    """Returns every model name this deployment is configured to allow.

    `Settings.ollama_model` plus `Settings.ollama_allowed_models` -- the
    same pair `agent.model_registry.validate_model_selection` already
    treats as authoritative for a per-question model choice, so a
    governed artifact cannot claim compatibility with a model `/ask`
    would itself reject.
    """
    if settings is None:
        from config.settings import get_settings

        settings = get_settings()
    names = {settings.ollama_model, *settings.ollama_allowed_models}
    return frozenset(name for name in names if name)


def known_evaluation_suites() -> frozenset[str]:
    """Returns every evaluation suite that exists in this repository.

    Discovered from the filesystem rather than from an in-code list, so
    adding a suite file makes it valid immediately and no list can go
    stale:

    - `eval/benchmark/*.yaml` -> the SQL benchmark's own suites, by stem
      (`easy`, `hard`, `adversarial`, ...).
    - `eval/component_benchmark/cases_*.py` -> the deterministic
      component-benchmark domains, by the stem after `cases_`.
    - `security_benchmark` -> the 500-case prompt-injection harness
      (`eval/security_benchmark/`), which is a package rather than a
      per-suite file, hence named explicitly.

    **Disclosed approximation**: a `cases_*.py` module that exists but is
    not registered in `eval.component_benchmark.runner._DOMAIN_MODULES`
    is reported here as existing even though `run_all()` would not
    execute it. Globbing is deliberately the more permissive of the two
    readings -- it names the suite a reviewer would actually cite, and
    treating a present-but-unregistered module as nonexistent would
    reject a legitimate reference during the window between adding a
    suite and wiring it up.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    suites: set[str] = {"security_benchmark"}
    for path in sorted((root / "eval" / "benchmark").glob("*.yaml")):
        suites.add(path.stem)
    for path in sorted((root / "eval" / "component_benchmark").glob("cases_*.py")):
        suites.add(path.stem[len("cases_") :])
    return frozenset(suite for suite in suites if suite)


__all__ = [
    "FINGERPRINT_DB_TYPE",
    "InventoryEntry",
    "InventoryReport",
    "InventoryUnavailableError",
    "fingerprint_text",
    "full_inventory",
    "known_evaluation_suites",
    "known_model_names",
    "known_tool_names",
    "inventory_agent_definitions",
    "inventory_analytical_policies",
    "inventory_prompt_templates",
    "live_agent_entry",
    "live_prompt_entry",
]
