# 44 — AI Governance Center

Prompt 44 of the ongoing Enterprise AI Analytics & Recommendation
Platform initiative: a governed Draft -> Test -> Evaluate -> Approve ->
Deploy -> Rollback lifecycle for models, providers, prompts, agents,
tools, retrieval sources, and policies -- owners, versions, approvals,
usage/cost/latency, and policy events, restricted by role, with release
and rollback APIs plus dashboard views. **INSPECT/PLAN/IMPLEMENT/TEST/
REVIEW/FIX/DOCUMENT** — one new top-level package (`governance_center/`),
two new identity tables, a new standalone REST surface
(`api/governance.py`), a new frontend page, server-driven nav wiring,
new RBAC permissions, and a dedicated cross-cutting regression test
proving the mission's own two named test requirements. No existing
route, node, prompt, or `Settings` field changed behavior for any
pre-existing caller.

## 0. A found, disclosed concurrent-work note

While this prompt was in progress, a separate, concurrent session
committed its own unrelated work (`5b2e6a5`, Prompt 43's "Schema Drift
Impact Analysis," `schema_drift/`/`api/schema_drift.py`) directly on top
of this branch's own history, mid-session. This was discovered by
re-checking `git status` before committing and confirmed by `git log`
-- not a conflict with anything this prompt built (no shared file was
left in an inconsistent state; `api/main.py`/`config/settings.py`/
`frontend/vite.config.ts`/`frontend/src/components/layout/AppShell.tsx`
were all re-read fresh after that commit landed, and this prompt's own
edits to each were applied cleanly on top of it, confirmed by a full
`api.main` import check and a clean `alembic heads` single-head check
immediately after). Disclosed here for the same reason Prompt 42's own
§0 disclosed a different cross-branch finding: an honest record of what
happened during implementation, not a silent workaround.

## 1. Inspection: what already existed vs. the real gap

Exhaustive inspection confirmed **no prior "asset lifecycle/release
governance" concept existed anywhere in this codebase** for models,
providers, prompts, agents, tools, retrieval sources, or policies --
every one of those is governed today only by static `.env`
configuration (`Settings.ollama_allowed_models`, `ENABLE_*` flags,
`agent/multiagent/`'s own hardcoded `AgentSpec`/`Tool` registrations)
with no draft/test/evaluate/approve/deploy/rollback workflow, no
versioning, no owner/approval record, and no audit trail of who
changed what and when. The closest existing building blocks, all found
by inspection and reused rather than reimplemented:

- `semantic.catalog.CatalogStatus`/`VALID_STATUS_TRANSITIONS` +
  `identity/repositories/semantic_catalog.py`'s `_apply_transition` --
  the exact draft -> reviewed -> published -> superseded shape and its
  atomic, conditional-`UPDATE` concurrency guard (found and fixed for a
  real race in Prompt 27), mirrored structurally for this prompt's own,
  differently-named lifecycle and reused verbatim for its own
  `_apply_transition`.
- `recommendation.governance.RecommendationStatus`/
  `VALID_STATUS_TRANSITIONS` -- the identical "pure, ORM-independent
  vocabulary + transition table, enforced at both the repository and
  API layer" split, and the precedent for "this gates measurement/
  recording, not whether something is shown" framing this prompt's own
  "usage/cost/latency, never auto-instrumented" disclosure echoes.
- `onboarding.policy.authorize_onboarding_action`/`semantic
  .catalog_policy.authorize_catalog_action` -- the exact RBAC+ABAC
  decision-function shape (deny-by-default, cross-tenant -> the same
  404 a nonexistent resource gets) mirrored verbatim for
  `governance_center.policy.authorize_governance_action`.
- `identity.rbac.Permission`'s existing `*_MANAGE`/`*_REVIEW` pairs
  (`ONBOARDING_MANAGE`/`REVIEW`, `CATALOG_MANAGE`/`REVIEW`,
  `RECOMMENDATION_MANAGE`/`REVIEW`) -- the exact admin-tier/analyst-tier
  split reused for `GOVERNANCE_MANAGE`/`GOVERNANCE_REVIEW`.
- `agent.model_registry` -- confirmed to be configuration/display-only
  (configured/allowed vs. installed vs. curated display metadata), with
  no lifecycle/approval concept at all; not extended or modified by this
  prompt (see §5's own disclosed scope boundary on why this prompt does
  not rewire live model/tool selection).
- `observability/metrics.py`'s own disclosed "no per-call token/cost
  metadata is captured anywhere in this codebase" note -- the honest
  basis for this prompt's own "usage/cost/latency is operator-reported,
  never auto-instrumented" design decision, rather than silently
  fabricating numbers this application cannot actually measure yet.
- `tests/test_api_navigation.py`/`tests/test_api_semantic_catalog.py`'s
  real-in-memory-identity-database test fixture pattern (real tenants,
  real local accounts, real JWT login) -- reused verbatim for every new
  test file this prompt adds, never a mock of the authorization path.

## 2. What was built

### `governance_center/` (new top-level package)

- `models.py` -- `GovernedAssetType` (model/provider/prompt/agent/tool/
  retrieval_source/policy), `GovernanceStage` (draft/testing/evaluating/
  approved/deployed/rolled_back/rejected/retired), `VALID_STAGE_TRANSITIONS`
  (deliberately excludes `retired`/`rolled_back` -> `deployed` --
  `rollback`'s own separately-validated action, never a plain table
  entry), `ROLLBACK_ELIGIBLE_STAGES`, `stage_to_truth_level` (approved/
  deployed -> `CONFIRMED_BUSINESS_TRUTH`; everything else ->
  `AI_INFERENCE` -- a governance-approval signal only, never a claim
  about runtime output), `GovernanceEventType`,
  `GovernanceAssetVersionSnapshot` (ORM-independent, mirrors
  `CatalogEntrySnapshot`'s own split).
- `policy.py` -- `GovernanceAction`, `authorize_governance_action`
  (deny-by-default RBAC+ABAC, cross-tenant -> `"cross_tenant"`). `LIST`
  is a distinct action from `VIEW` (see §4's first bug).

### `identity/models.py` + migration `c7d2e5f8a1b4`

- `GovernanceAssetVersion` -- one row per version (append-only past
  `draft`, the identical discipline `SemanticCatalogEntry` already
  establishes), unique on `(tenant_id, asset_type, asset_key, version)`,
  every `*_by_user_id`/`*_at` approval/test/evaluation/deployment/
  retirement pair, `config`/`test_results`/`evaluation_results` as
  free-form JSON (human-authored, never parsed/executed by this
  module -- the identical "never let an LLM invent a constraint"
  posture `optimization.models`/`data_quality.models` already
  establish for a different kind of payload).
- `GovernanceEvent` -- append-only audit trail (`stage_change`/
  `rollback`/`usage_recorded`/`note`), mirrors
  `RecommendationFeedbackEvent`'s own shape.
- Migration `c7d2e5f8a1b4` chains onto `a1f5c9e3b7d2` (confirmed the
  real, unchanged head via `alembic heads` both before and after this
  prompt's own work, and after the concurrent Prompt 43 commit landed
  -- see §0).

### `identity/repositories/governance.py`

`create_draft`, `update_draft` (draft-only, mirrors
`SemanticCatalogEntry`'s identical in-place-edit exception),
`submit_for_testing`/`record_test_results`/`submit_for_evaluation`/
`record_evaluation_results`/`approve`/`reject`/`deploy`/`rollback`/
`retire`/`record_usage`, `list_events`, `usage_summary` (a plain,
deterministic sum/mean aggregation -- `DATABASE_FACT`),
`version_to_snapshot`. Every transition goes through `_apply_transition`'s
atomic, conditional `UPDATE ... WHERE stage = expected_current` --
proven race-safe with two independent `Session`s (not real threads, for
the identical, already-documented reason a shared in-memory SQLite
connection isn't a safe stand-in for genuine concurrency).
`deploy`/`rollback` each mutate a *second* row (the previously-deployed/
rollback-target version) on the same ORM object before the primary
row's own atomic commit, so a lost race on the primary row rolls the
second row's change back too.

### `identity/rbac.py`

New `Permission.GOVERNANCE_MANAGE` (admin tier) / `GOVERNANCE_REVIEW`
(analyst tier), seeded via the existing idempotent `identity.bootstrap
.seed_rbac` -- no migration needed for RBAC data itself, the same
"picked up automatically on next startup" contract every prior
permission addition already has.

### `api/governance.py` + `api/governance_schemas.py`

15 routes under `/governance`, all requiring a local account
(`require_local_user`). **No `Settings.enable_*` feature flag** -- an
administrative/compliance surface gated purely by RBAC, the identical
posture `api/onboarding.py`/`api/semantic_catalog.py`/
`api/platform_admin.py` already establish. Every route resolves the
target version by id first, then calls `authorize_governance_action`
and acts only on `allowed=True` -- a cross-tenant version is denied the
same 404 a genuinely nonexistent one would get.

### Frontend

`frontend/src/pages/AIGovernance.tsx` -- list + detail + capability-
gated action buttons, mirroring `SemanticReview.tsx`'s own shape.
`frontend/src/lib/governanceApi.ts` -- thin `request()` wrappers.
Wired through `security/navigation.py` (new `ai_governance` `NavItem`),
`App.tsx`, `AppShell.tsx`'s screen-presentation map (new `ClipboardCheck`
icon), `frontend/vite.config.ts`'s dev-proxy `BACKEND_ROUTES`, and all 5
locale files' nav label.

## 3. Authorization, tenant isolation, and provenance

**RBAC**: `GOVERNANCE_MANAGE` (admin tier -- every operational action:
create/edit draft, submit for testing/evaluation, record results,
deploy, roll back, retire, record usage) and `GOVERNANCE_REVIEW`
(analyst tier -- approve/reject only, the identical domain-judgment
tier `ONBOARDING_REVIEW`/`CATALOG_REVIEW`/`RECOMMENDATION_REVIEW`
already sit at). Proven directly: a `GOVERNANCE_REVIEW`-only caller can
approve but gets a 403 on deploy/rollback/retire; a `GOVERNANCE_MANAGE`
caller can do everything review can plus every operator action.

**Tenant isolation** mirrors `onboarding.policy`/`semantic
.catalog_policy` exactly -- `GovernanceAssetVersion.tenant_id` +
`governance_center.policy.authorize_governance_action`'s own ABAC
check, cross-tenant denial mapped to the same 404 a genuinely
nonexistent version would get. Proven directly against a real identity
database with two real tenants: a cross-tenant GET/deploy/rollback is a
404; a tenant's own `GET /governance/assets` list never includes
another tenant's versions.

**Provenance**: every version's own `truth_level` is a governance-
approval signal (`CONFIRMED_BUSINESS_TRUTH` only once a human approves
it for deployment) -- never a claim about anything the asset itself
produces at runtime, which remains each engine/pipeline's own,
already-established provenance, completely unaffected by this prompt.

## 4. Two real bugs found and fixed before any test was left to merely
document the wrong behavior

1. **`LIST`/`GET .../current` were wrongly denied with a 403.**
   `authorize_governance_action` originally treated every non-
   `CREATE_DRAFT` action's `asset_tenant_id=None` as "resource not
   found" -- correct for `VIEW` on one specific, already-resolved
   version, but wrong for listing/looking-up-the-current-deployed-
   version, neither of which has one specific resource to tenant-match
   against (the query itself scopes the result to the caller's own
   tenant). Fixed by giving `LIST` its own, distinct `GovernanceAction`,
   authorized by permission alone like `CREATE_DRAFT`. Locked in by a
   dedicated unit test in `tests/test_governance_center.py`.
2. **`approve`/`retire` required a request body even though every
   field on each is optional.** FastAPI treats a bodied-model route
   parameter as requiring *some* JSON content unless that parameter
   itself has a default -- a caller correctly omitting the body
   entirely (since nothing on `ApproveRequest`/`RetireRequest` is
   required) got a 422. Fixed by giving each route's own `payload`
   parameter a module-level singleton default instance (not a mutable
   default constructed inline, which `ruff`'s `B008` correctly flags).
   Locked in by `tests/test_api_governance.py
   ::test_approve_and_retire_work_with_no_request_body_at_all`.

## 5. Testing

- `tests/test_governance_center.py` (18 cases) -- pure vocabulary/
  transition-table/truth-level/policy unit coverage, including the
  `LIST`-vs-`VIEW` bug's own regression test.
- `tests/test_identity_governance_repository.py` (31 cases) -- the full
  create/update/testing/evaluation/approve/reject/deploy/supersession/
  rollback/retire/usage-tracking lifecycle, plus the dedicated two-
  `Session` atomic-transition race test.
- `tests/test_api_governance.py` (16 cases) -- the ordinary HTTP
  contract: happy path, request validation, stage-conflict 409s, the
  approve/retire-with-no-body regression.
- `tests/security/test_governance_unauthorized_rollback_and_tenant_isolation.py`
  (12 cases) -- the mission's own two explicitly-named test
  requirements, against the real HTTP API with a real identity
  database: unauthorized changes refused at every tier boundary,
  rollback genuinely restores the prior version with a correct audit
  trail, and cross-tenant authorization holds throughout.
- `tests/test_security_navigation.py`/`tests/test_api_navigation.py` --
  updated for the new `ai_governance` screen (the same exhaustive
  per-persona equality check Prompt 42's own update to these files
  already established).

Full suites re-run clean: **backend governance-specific tests 121/121
passing**; mypy clean on every new/changed production module (two pre-
existing, unrelated nullable-FK-assignment mypy errors elsewhere in
`identity/repositories/` confirmed untouched by this session, matching
a pattern already tolerated there); ruff/black clean; frontend
`tsc --noEmit` clean, `npm run lint` clean on every new file, and the
full frontend suite passes (one pre-existing, unrelated flaky test in
`TurnCard.analytics.test.tsx` -- confirmed to pass reliably in
isolation and to involve no file this prompt touched -- noted honestly,
not silently ignored).

## 6. Known, disclosed limitations

- **Usage/cost/latency is entirely operator-reported.** No per-model/
  per-prompt/per-tool token-cost capture exists anywhere in this
  codebase today; `usage_summary` aggregates only what a caller
  reports, never fabricating a number this application cannot actually
  measure.
- **This center governs the lifecycle/approval *record*, not live
  request-serving selection.** Approving or deploying a version here
  does not itself rewire `Settings.ollama_allowed_models`, `agent
  /multiagent/`'s hardcoded tool registrations, or any `ENABLE_*` flag
  -- the identical "measure and record, never gate what's shown"
  scope boundary `recommendation.governance`'s own docstring already
  discloses for a structurally similar reason. Closing this gap for
  real would mean making live pipeline configuration database-driven,
  a materially larger and riskier undertaking than this prompt's own
  scope.
- **No multi-agent specialist wiring** -- an administrative/compliance
  surface, never reached from `/ask`, the identical posture
  `api/onboarding.py`/`api/semantic_catalog.py`/`api/platform_admin.py`
  already establish.
- **No dedicated dashboard rollup view** (e.g. "N assets awaiting
  review across the tenant") -- the frontend page lists/filters/acts on
  individual versions; a cross-asset summary card (mirroring
  `TenantAdmin.tsx`'s own "Pending Reviews" summary pattern) is a real,
  disclosed follow-up, not built in this pass.
- **Free-text `asset_key`/`config`** -- no validation that a model's
  `asset_key` actually matches a real `Settings.ollama_allowed_models`
  entry, or that a tool's `config` references a real `agent/multiagent/`
  tool name. This is intentional (the catalog can describe a *proposed*
  future asset, not only an already-configured one) but means this
  center cannot yet detect "this approved model was never actually
  added to `.env`" on its own.

## 7. Recommended next prompt

Either (a) a read-only "drift" check between this center's own
`deployed` versions and the live `Settings`/`agent/multiagent/`
configuration (flagging, never auto-correcting, a mismatch -- "this
tool is marked deployed here but isn't registered in the live tool
registry"), which would close §6's last limitation without taking on
the much larger risk of making live pipeline selection database-driven;
or (b) extending `observability/metrics.py` with real per-model/
per-prompt usage capture (closing the "operator-reported, never
auto-instrumented" limitation for real), which several other prompts in
this series have already identified as a disclosed, deferred gap.
