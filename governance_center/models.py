"""The AI Governance Center's typed vocabulary -- Prompt 44
(`44_AI_GOVERNANCE_CENTER_CONTRACT.md`): one governed lifecycle,
applied uniformly across every kind of AI-platform asset this mission
names (models, providers, prompts, agents, tools, retrieval sources,
policies) rather than seven separate, drifting subsystems.

**Deliberately ORM-independent and pure**, mirroring `semantic.catalog`'s
and `recommendation.governance`'s own identical "no identity/SQLAlchemy
import" split -- this vocabulary (and the transition table below) is
unit-testable with plain enum values, with no database in the loop, and
reusable by both the repository layer (`identity/repositories
/governance.py`, defense in depth) and the API layer
(`api/governance.py`, the user-facing 409 before a repository call is
even attempted).

**What "config" means here, and what it deliberately does not.**
`GovernanceAssetVersionSnapshot.config` is a free-form, human-authored
JSON payload describing one version of a governed asset (e.g. a model's
own `{"provider": "ollama", "model_id": "llama3.1:8b"}`, a prompt's own
`{"template": "..."}` , a tool's own `{"allowed_tools": [...]}`) -- this
module never parses, executes, or interprets it. It is stored and
displayed, never evaluated, which is what keeps "never let an LLM
invent a constraint" (master rule 5, already established by
`optimization.models`/`data_quality.models`/`analytics.scenario
.models` for a different kind of payload) true here too: a human author
is always the one who writes a draft's own `config`, and this module
never generates or auto-completes one.

**Truth level is a governance-approval signal, not a claim about what
the asset itself produces at runtime.** `stage_to_truth_level` maps
`APPROVED`/`DEPLOYED` to `CONFIRMED_BUSINESS_TRUTH` (a human has
reviewed and approved this specific version for production use) and
every other stage to `AI_INFERENCE` (not yet confirmed) -- the identical
"only a human's own review promotes a claim" posture `semantic.catalog
.status_to_truth_level` already establishes, applied to "is this asset
version production-ready" rather than to a business-concept definition.
This module makes **no claim whatsoever** about the correctness of
anything a deployed model/prompt/tool/retrieval source generates at
request time -- that remains each engine/pipeline's own, already-
established provenance (`agent.provenance.DataTruthLevel`, enforced
node-by-node elsewhere in this codebase) and is completely unaffected
by this module's own existence.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.provenance import DataTruthLevel

#: Bumped when this module's typed shapes or the transition table change
#: in a way a consumer should be able to detect -- the identical
#: convention every other governed-lifecycle/engine module in this
#: codebase already follows.
GOVERNANCE_CENTER_VERSION = "1.0.0"


class GovernedAssetType(str, Enum):
    """The seven kinds of asset this center governs -- the mission's own
    named list, as a closed set rather than a free-form string."""

    MODEL = "model"
    PROVIDER = "provider"
    PROMPT = "prompt"
    AGENT = "agent"
    TOOL = "tool"
    RETRIEVAL_SOURCE = "retrieval_source"
    POLICY = "policy"


class GovernanceStage(str, Enum):
    """One governed asset version's own lifecycle state -- the mission's
    own named chain (Draft -> Test -> Evaluate -> Approve -> Deploy ->
    Rollback), plus the two terminal outcomes every real approval
    workflow in this codebase already needs (`REJECTED`, mirroring
    `semantic.catalog`'s "request changes"/no-rejected-state honesty
    lesson learned there -- here a real `REJECTED` state does exist,
    since an asset version, unlike a catalog entry, is not expected to
    cycle back into review after a hard rejection; `RETIRED`, mirroring
    `recommendation.governance.RecommendationStatus.RESOLVED`'s own
    "administratively closed, not because it was wrong" distinction).

    Attributes:
        DRAFT: Authored, not yet tested. Mutable in place
            (`identity.repositories.governance.update_draft`) -- the one
            exception, mirroring `semantic.catalog.CatalogStatus.DRAFT`'s
            own identical exception.
        TESTING: Submitted for testing; `test_results` is where a caller
            records the outcome (functional correctness, not yet a
            production-readiness judgment).
        EVALUATING: Testing passed; submitted for evaluation (accuracy/
            quality/benchmark scoring, cost/latency projection, policy
            compliance) -- `evaluation_results` holds that outcome.
        APPROVED: A human reviewer has approved this version for
            deployment -- `CONFIRMED_BUSINESS_TRUTH` (see this module's
            own docstring), but **not yet live**.
        DEPLOYED: Released into production use. At most one version of a
            given `(tenant_id, asset_type, asset_key)` may hold this
            stage at a time -- deploying a new version atomically retires
            whichever version held it before (see
            `identity.repositories.governance.deploy`).
        ROLLED_BACK: Was `DEPLOYED`, then explicitly rolled back due to an
            operational issue -- distinct from `RETIRED` (superseded by a
            newer, intentional deployment) so an audit trail can tell
            "this broke something" from "this was simply replaced."
            Terminal: a rolled-back version is never redeployed in place;
            an operator deploys it fresh again instead (still recorded as
            a `rollback` event either way -- see
            `identity.repositories.governance.rollback`).
        REJECTED: A reviewer rejected this version outright -- terminal.
        RETIRED: Administratively retired (superseded by a newer
            deployment, or manually decommissioned) -- terminal, but
            still eligible as a rollback *target* (see `rollback` above).
    """

    DRAFT = "draft"
    TESTING = "testing"
    EVALUATING = "evaluating"
    APPROVED = "approved"
    DEPLOYED = "deployed"
    ROLLED_BACK = "rolled_back"
    REJECTED = "rejected"
    RETIRED = "retired"


#: Ordinary forward (and "send back for more work") transitions --
#: enforced by both `identity/repositories/governance.py` (defense in
#: depth, atomic) and `api/governance.py` (the user-facing 409 before a
#: repository call is even attempted). Mirrors `semantic.catalog
#: .VALID_STATUS_TRANSITIONS`'s own dict shape exactly.
#:
#: Deliberately does **not** include `RETIRED`/`ROLLED_BACK` ->
#: `DEPLOYED` -- that specific move is `rollback`'s own, separately
#: validated action (see `identity.repositories.governance.rollback`'s
#: own docstring for why it is a distinct operation, not a plain
#: transition-table entry).
VALID_STAGE_TRANSITIONS: dict[GovernanceStage, frozenset[GovernanceStage]] = {
    GovernanceStage.DRAFT: frozenset({GovernanceStage.TESTING, GovernanceStage.REJECTED}),
    GovernanceStage.TESTING: frozenset(
        {GovernanceStage.EVALUATING, GovernanceStage.DRAFT, GovernanceStage.REJECTED}
    ),
    GovernanceStage.EVALUATING: frozenset(
        {GovernanceStage.APPROVED, GovernanceStage.DRAFT, GovernanceStage.REJECTED}
    ),
    GovernanceStage.APPROVED: frozenset(
        {GovernanceStage.DEPLOYED, GovernanceStage.RETIRED, GovernanceStage.REJECTED}
    ),
    GovernanceStage.DEPLOYED: frozenset({GovernanceStage.ROLLED_BACK, GovernanceStage.RETIRED}),
    GovernanceStage.ROLLED_BACK: frozenset(),
    GovernanceStage.REJECTED: frozenset(),
    GovernanceStage.RETIRED: frozenset(),
}

#: Every stage with no ordinary outgoing transition -- a version in one
#: of these states is permanently done *through the ordinary transition
#: table* (it may still be a `rollback` target -- see `VALID_STAGE_TRANSITIONS`'s
#: own docstring).
TERMINAL_STAGES: frozenset[GovernanceStage] = frozenset(
    stage for stage, targets in VALID_STAGE_TRANSITIONS.items() if not targets
)

#: Stages a `rollback` may legally retarget *to* -- a version that was
#: once live and is now resting, never a version that was never deployed
#: at all (a `DRAFT`/`TESTING`/`EVALUATING`/`APPROVED`/`REJECTED` version
#: has no production history to "roll back" to).
ROLLBACK_ELIGIBLE_STAGES: frozenset[GovernanceStage] = frozenset(
    {GovernanceStage.RETIRED, GovernanceStage.ROLLED_BACK}
)


def is_terminal_stage(stage: GovernanceStage) -> bool:
    """True if `stage` has no ordinary outgoing transition at all (it may
    still be a legal `rollback` *target* -- see `ROLLBACK_ELIGIBLE_STAGES`)."""
    return stage in TERMINAL_STAGES


def stage_to_truth_level(stage: GovernanceStage) -> DataTruthLevel:
    """See this module's own docstring for the full "governance-approval
    signal, not a runtime-output claim" framing."""
    if stage in (GovernanceStage.APPROVED, GovernanceStage.DEPLOYED):
        return DataTruthLevel.CONFIRMED_BUSINESS_TRUTH
    return DataTruthLevel.AI_INFERENCE


class GovernanceEventType(str, Enum):
    """The closed set of event kinds `identity.models.GovernanceEvent`
    may record -- every lifecycle action produces exactly one of these,
    never a free-text label."""

    STAGE_CHANGE = "stage_change"
    ROLLBACK = "rollback"
    USAGE_RECORDED = "usage_recorded"
    NOTE = "note"


class GovernanceAssetVersionSnapshot(BaseModel):
    """The ORM-independent read shape of one `identity.models
    .GovernanceAssetVersion` row -- mirrors `semantic.catalog
    .CatalogEntrySnapshot`'s own "no identity/ORM dependency" precedent
    exactly, for the identical reason (unit-testable with hand-built
    values, no SQLAlchemy on this module's own import graph).

    `keys`/`relationships`/`synonyms`-style enrichment fields `semantic
    .catalog` carries have no equivalent here -- a governed asset version
    is a configuration artifact with an approval lifecycle, not a
    business-meaning definition, so this shape stays deliberately
    smaller and specific to that purpose.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    asset_type: GovernedAssetType
    asset_key: str
    display_name: str
    description: str = ""
    owner: str | None = None
    version: int
    stage: GovernanceStage
    config: dict[str, Any] = Field(default_factory=dict)
    test_results: dict[str, Any] | None = None
    evaluation_results: dict[str, Any] | None = None
    approval_notes: str | None = None
    rejection_reason: str | None = None
    supersedes_id: str | None = None

    @property
    def truth_level(self) -> DataTruthLevel:
        return stage_to_truth_level(self.stage)


__all__ = [
    "GOVERNANCE_CENTER_VERSION",
    "GovernedAssetType",
    "GovernanceStage",
    "VALID_STAGE_TRANSITIONS",
    "TERMINAL_STAGES",
    "ROLLBACK_ELIGIBLE_STAGES",
    "is_terminal_stage",
    "stage_to_truth_level",
    "GovernanceEventType",
    "GovernanceAssetVersionSnapshot",
]
