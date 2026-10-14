# Governance, identity and admin features — invariants and limitations

Identity, authorization, multi-tenancy, the governed lifecycles
(semantic catalog, recommendations, governance center, evaluation,
workflow), the admin surfaces, model routing, BI integrations, and the
platform-wide security/readiness passes.

Moved verbatim out of the root `CLAUDE.md`. Several of these entries record
**security properties that are structural rather than conventional** — read
the entry before changing the code it describes.

## Contents

- [Authentication, authorization, and the 2026 security hardening passes](#authentication-authorization-and-the-2026-security-hardening-passes)
- [Universal server-side chat history (`identity/repositories/history.py`, `api/chat_history.py`)](#universal-server-side-chat-history-identityrepositorieshistorypy-apichathistorypy)
- [Prompt-injection benchmark and hardening pass (2026-09-24/25)](#prompt-injection-benchmark-and-hardening-pass-2026-09-2425)
- [Google sign-in (2026-09-28)](#google-sign-in-2026-09-28)
- [Secure conversation sharing (2026-09-28)](#secure-conversation-sharing-2026-09-28)
- [Client-database onboarding engine (`onboarding/`, Prompt 08)](#client-database-onboarding-engine-onboarding-prompt-08)
- [Tenant-aware semantic catalog (`semantic/catalog.py`, Prompt 09)](#tenant-aware-semantic-catalog-semanticcatalogpy-prompt-09)
- [Multi-tenant enterprise architecture (Prompt 20)](#multi-tenant-enterprise-architecture-prompt-20)
- [Enterprise security & data governance hardening (Prompt 21)](#enterprise-security--data-governance-hardening-prompt-21)
- [Observability, evaluation & reliability (Prompt 23)](#observability-evaluation--reliability-prompt-23)
- [Full integration & regression (Prompt 24)](#full-integration--regression-prompt-24)
- [Production readiness & release gate (Prompt 25)](#production-readiness--release-gate-prompt-25)
- [Database Onboarding Portal (Prompt 26)](#database-onboarding-portal-prompt-26)
- [SME Semantic Review Dashboard (Prompt 27)](#sme-semantic-review-dashboard-prompt-27)
- [Global Platform Admin Dashboard (Prompt 28)](#global-platform-admin-dashboard-prompt-28)
- [Client/Tenant Admin Dashboard (Prompt 29)](#clienttenant-admin-dashboard-prompt-29)
- [Prompts 33-35: AI analysis layers (2026-10)](#prompts-33-35-ai-analysis-layers-2026-10)
- [AI Governance Center (Prompt 44)](#ai-governance-center-prompt-44)
- [Prompt/agent/policy registry: inventory + typed specs + validation (Prompt 45)](#promptagentpolicy-registry-inventory--typed-specs--validation-prompt-45)
- [AI Evaluation Benchmarking platform (Prompt 46)](#ai-evaluation-benchmarking-platform-prompt-46)
- [Enterprise BI integrations (Prompt 48)](#enterprise-bi-integrations-prompt-48)
- [Governed model routing (Prompt 47)](#governed-model-routing-prompt-47)
- [Enterprise Workflow Automation (Prompt 49)](#enterprise-workflow-automation-prompt-49)
- [Enterprise Data Privacy, Domain Safety & Governance Engine](#enterprise-data-privacy-domain-safety--governance-engine)

---

## Authentication, authorization, and the 2026 security hardening passes
Two independent layers — the "no per-user authorization system" phrasing
that appears in a couple of older notes elsewhere in this file predates
both and is no longer accurate for anything gated by `agent/authz.py`'s
permissions.

**Invariants that must not regress:** `Settings.auth_mode` is the one
dispatch point (`none`/`static_token`/`oidc`/local self-hosted accounts,
plus Google sign-in layered on local accounts) — `ENVIRONMENT=production`
fails closed at startup if identity is unconfigured. RBAC
(`agent/authz.py`) is wired into every data-touching/expensive route
**and** the multi-source router itself — `router_node` filters the LLM's
own source selection through a permission check *before* any subgraph
runs, so a routing decision the LLM makes can request access but never
unilaterally grant it; an unrecognized role grants zero permissions (fail
closed). Local accounts and Google sign-in both feed the *same*
`AuthIdentity.roles`/RBAC bridge — zero changes needed to any existing
route's authorization check.

**Read [`docs/AUTHENTICATION.md`](../AUTHENTICATION.md)** for the four
auth modes, frontend OIDC login, and Google sign-in (including what's
live-verified vs. not), and
[`docs/AUTHORIZATION.md`](../AUTHORIZATION.md) for the full
resource-to-permission mapping. For the broader security posture
(security headers/CSP, DB write-privilege startup enforcement, CI gates,
CVE triage) see [`SECURITY.md`](../../SECURITY.md) and
[`docs/security-changelog.md`](../security-changelog.md); for the
current, most-rigorously-sourced production-readiness verdict (as of this
writing: **NOT READY**, over DAST/live-IdP gaps, not a found
vulnerability), start at
[`docs/security/PRODUCTION_SECURITY_READINESS_REPORT.md`](../security/PRODUCTION_SECURITY_READINESS_REPORT.md)
— it supersedes the repo-root `SECURITY_*.md` files' own bottom line where
the two differ.


## Universal server-side chat history (`identity/repositories/history.py`, `api/chat_history.py`)
For a locally-authenticated user, every conversation and message is
stored permanently in the identity database — the fix for a real,
previously-disclosed gap (chat state used to be entirely in-memory-only,
so the same signed-in user got a blank history on a different
browser/device, and a page reload cleared it).

**Invariants that must not regress:** every route requires
`Depends(require_local_user)` and folds `user_id` directly into every
repository-layer query, so a wrong/forged `conversation_id` resolves to a
404, never confirming another user's conversation even exists. A
persistence failure here can never fail the `/ask` response itself
(fail-open, same posture as every other non-critical-path accuracy aid).
Retention is soft-delete only (`deleted_at`) — logout, session expiry, or
an app restart never delete chat history; only an explicit
`DELETE /conversations/{id}` does. **Opening a saved conversation never
re-executes SQL, re-fetches a web page, or re-runs OCR/vision** — only an
explicit "Confirm and Run" click does. **Known limitation:** only a
locally-authenticated user gets this — an OIDC or unauthenticated
caller's questions are answered normally but nothing is persisted.

**Read [`docs/chat-history-architecture.md`](../chat-history-architecture.md)**
for the schema/API/frontend design (§6.5 covers the full-turn-metadata
fix and what still isn't restored on reload — retrieved-schema DDL, a
chart's own customization),
[`docs/chat-history-authentication-audit.md`](../chat-history-authentication-audit.md)
for the before/after, and
[`docs/chat-history-search.md`](../chat-history-search.md) for the
search design.


## Prompt-injection benchmark and hardening pass (2026-09-24/25)

A 500-case prompt-injection/security benchmark (20 categories) was run to
completion against the real live agent (real Ollama, real SQL Server
databases) — not a static regex-blind-spot probe. Harness:
`eval/security_benchmark/`, CLI entry points
`scripts/run_security_benchmark.py`/`scripts/run_multiturn_persistence_benchmark.py`.

**Headline result as of this writing: all 500 cases completed, 0 critical
findings** — zero writes executed, zero unauthorized sources reached,
zero secrets or system prompts leaked. 66.4% raw pass rate; every
`expected_behavior` mismatch is a content-level miss, never a hard-gate
violation. Five real bugs were found and fixed along the way — including
an unguarded `OPTION (MAXRECURSION 0)` SQL hint that could hang a query
indefinitely (now a flat-denied `unsafe_query_option` violation type), a
secret-redaction gap in LLM-generated response text, and a new
`cross_source_injection_narrative` detection pattern for third-person-
narrated indirect-injection payloads (confirmed live: pass rate on the
two affected categories went 20% → 100%).

**This is a point-in-time result, not a standing guarantee** — re-run the
benchmark after any change to `agent/sql_validator.py`,
`security/injection_patterns.py`, or the orchestrator's prompt
construction before trusting these numbers still hold. **Honest residual
gaps, not yet closed as of this writing:** an admin-role re-run of the
RBAC-bypass categories, and 2 of 10 multi-turn-persistence payloads not
yet separately re-graded against their real history entry.

**Read [`docs/security/PROMPT_INJECTION_BENCHMARK_GAP_REPORT.md`](../security/PROMPT_INJECTION_BENCHMARK_GAP_REPORT.md)**
(the authoritative, continuously-updated source) for live results per
category, the exact fix for each of the five bugs above, and the current
state of the residual gaps.


## Google sign-in (2026-09-28)
A second way to reach this app's own self-hosted accounts — "Continue
with Google" on both sign-in and sign-up. Layered entirely on top of the
pre-existing local-account system; does not touch OIDC mode, the
static-token mode, or `agent/authz.py`'s RBAC. Uses Google Identity
Services' ID-token flow (not OAuth authorization-code), since this is
authentication only — no `client_secret` is ever held server-side.

**Invariants that must not regress:** `security/google_oidc.py
::verify_google_id_token` validates signature against Google's live,
rotating public keys, issuer, exact `aud`, `azp`, and a server-issued
single-use nonce, and reads `hd` (hosted-domain restriction) from the
**signed claim**, never inferred from the email's `@domain` suffix. `sub`
is the only durable identity key ever stored. Identity linking
(`identity/repositories/external_identities.py`) never auto-merges an
account by email alone, and an unverified-email collision is refused
generically (a non-confirming 401) to avoid becoming an
account-enumeration oracle.

**Honest verification status:** the Alembic migration and every
identity-linking function are live-verified against a real PostgreSQL
database. **A real, browser-based, end-to-end Google sign-in has not been
exercised in this environment** — successful token verification is
necessarily mocked in HTTP-level tests.

**Read [`docs/AUTHENTICATION.md`](../AUTHENTICATION.md#google-sign-in-2026-09-28)**
(also summarized in [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md#9-google-sign-in))
for every server-side check and the full "what's verified vs. not"
breakdown.


## Secure conversation sharing (2026-09-28)
Turns a conversation into a controlled, read-only snapshot other people
may view — never a live query channel, an authentication session, or a
way to reach this app's SQL/RAG/web/model/image pipelines from a shared
view.

**Invariants that must not regress:** `identity/share_policy.py` is the
single RBAC/ABAC decision point every route/resolver calls before
acting — deny-by-default on an unrecognized action, role, or missing
tenant; owner/viewer/anonymous-link-viewer are the only three roles, no
"Editor." `build_share_projection` is a hard **allowlist** (not a
blocklist) of which persisted-turn fields a viewer may ever see — a
newly-added internal field defaults to invisible. This app has no real
multi-tenant model; only the new sharing tables carry a scoped
`tenant_id` — a deliberate, narrower decision than a full retrofit.

**A real bug found only by live-testing the running app, not by
`TestClient` alone:** raising `HTTPException` discards headers a route
had already set on its injected `Response` — the *denial* path of the
anonymous-reachable `GET /share-view/{ref}` was serving default
cache/referrer headers instead of `private, no-store`/`no-referrer`.
Fixed and re-verified live. **Not verified:** a real reverse proxy/CDN was
not available to confirm it honors `Cache-Control: private, no-store` for
this path prefix.

**Read [`docs/SHARING_SECURITY.md`](../SHARING_SECURITY.md)** (also
summarized in [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md#10-secure-conversation-sharing))
for the full data model, token lifecycle, and API surface.


## Client-database onboarding engine (`onboarding/`, Prompt 08)
Automates onboarding a client database when the platform operator is not
the subject-matter expert: discovery (Prompt 06, unchanged) → relationship
inference (Prompt 07, unchanged) → profiling → PII detection → semantic
inference/ambiguity detection → SME review → semantic contract → golden
questions → a pre-publish evaluation smoke test. The literal mechanism
behind the acceptance criterion "technical onboarding is automated and
ambiguous business meaning is routed to SME review" is
`onboarding.semantic_inference.infer_semantic_labels`'s `is_ambiguous`
flag: a column's best-scoring semantic-role candidate below an absolute
confidence floor, or not clearly ahead of its runner-up, becomes an
`OnboardingReviewItem` an SME must confirm or reject — never silently
promoted (master-contract rule 10).

**Invariants that must not regress:**
- **No connection secret is ever persisted.** `identity.models
  .OnboardingJob` has no password field at all — a connection secret is
  supplied fresh per API call (`CreateOnboardingJobRequest`/
  `RunDiscoveryRequest`/`PublishJobRequest`'s own `db_password`), used
  immediately to build a throwaway `Engine`
  (`create_engine(build_connection_url(config), pool_pre_ping=True)`,
  disposed in a `finally` block), and discarded. This deliberately
  bypasses `db.connection`'s process-wide cached-engine pool, whose own
  docstring assumes "a handful" of configured connections — an
  assumption onboarding's dynamic, many-distinct-candidate-databases-
  over-time use case would violate.
- **No background worker, therefore no automatic resume.** A job stuck
  mid-`"discovering"`/`"publishing"` because its own API call crashed is
  only recoverable via `POST /onboarding/jobs/{id}/retry`, which requires
  a fresh call (and fresh credentials) to actually resume — the
  deliberate, disclosed cost of never storing a password.
- **PII/relationship data-verification sampling are two *separate*
  opt-in flags** (`Settings.enable_pii_data_verification`/
  `enable_relationship_data_verification`), per this prompt's own
  "minimize sensitive sampling" requirement — an operator can opt into
  relationship value-overlap sampling (never PII-shaped) without also
  sampling columns already name-flagged as likely PII. Neither ever
  returns, logs, or stores a raw sampled value — only an aggregate
  match-rate float ever leaves `onboarding.pii_detection
  .verify_pii_with_data`.
- **Golden questions are template-based, never LLM-generated**
  (`onboarding/golden_questions.py`, master-contract rule 5) — 4 fixed
  shapes (count/aggregate_by_category/top_n/join_count), each carrying a
  deterministically-generated `candidate_sql` alongside its NL text, so
  `onboarding/evaluation.py` can smoke-test it through the *exact* same
  two gates every other SQL in this codebase passes through
  (`agent.sql_validator.validate_sql`/`enforce_row_limit`, then
  `db.execution.execute_readonly_sql`) — explicitly not `eval/`'s own
  execution-accuracy benchmark (no gold answer exists yet for a
  brand-new database).
- **Publishing requires every review item already decided.**
  `onboarding.jobs.publish_job` raises `OnboardingJobError` (and marks
  the job `"failed"`) if any item is still `"pending"` — an undecided
  PII/relationship/semantic claim reaching a published artifact would be
  exactly the "silently promoted inference" rule 10 forbids.
  `onboarding.semantic_contract.build_semantic_contract` only ever
  includes `"confirmed"` items; a `"rejected"` item contributes nothing,
  not even a trace.
- **Tenant isolation mirrors `identity.share_policy` exactly** —
  `OnboardingJob.tenant_id` + `onboarding.policy
  .authorize_onboarding_action` (deny-by-default, cross-tenant denial
  checked before any RBAC branch, mapped to the same 404 a genuinely
  nonexistent job would get — anti-enumeration, not a distinguishable
  403). `ONBOARDING_MANAGE` (create/discover/publish/cancel/retry) is
  `_ADMIN`-only; `ONBOARDING_REVIEW` (deciding items) is `_ANALYST`+.

**Read [`08_ONBOARDING_ENGINE_CONTRACT.md`](../../08_ONBOARDING_ENGINE_CONTRACT.md)**
for the full inspection findings, every new module's design, the 116-test
testing summary, and the security/tenant-isolation/performance review.


## Tenant-aware semantic catalog (`semantic/catalog.py`, Prompt 09)
A governed, persisted, tenant-scoped catalog connecting technical
metadata to business meaning — names, descriptions, grain, keys,
relationships, domains, metrics, dimensions, synonyms, business rules,
examples, evidence, confidence, status, owner, version — with a real
`draft → reviewed → published → superseded` review workflow
(`semantic.catalog.CatalogStatus`). The acceptance criterion ("natural-
language planning can retrieve business concepts rather than relying
only on raw schema names") is satisfied by integrating directly into the
*existing* `retrieval/` pipeline rather than building a second one: a new
`ChunkType.BUSINESS_CONCEPT` + a new `agent.llm_client
._build_plan_business_concept_block` feeding `plan_query_node`'s own LLM
call, not just `generate_sql_node`'s.

**Invariants that must not regress:**
- **Only a `PUBLISHED` entry is ever rendered into a retrieval chunk.**
  `retrieval.chunking.business_concept_chunk_from_catalog_entry`/
  `retrieval.ingestion.sync_catalog_entry_to_vector_store` are only ever
  called from `api/semantic_catalog.py`'s publish route — a `DRAFT`/
  `REVIEWED` entry structurally never reaches the LLM's prompt, which is
  what makes master-contract rule 10 ("never silently convert AI
  inference into confirmed business truth") true by construction here,
  not by a runtime check that could be forgotten. `semantic.catalog
  .status_to_truth_level` maps this onto `agent.provenance
  .DataTruthLevel` directly (draft/reviewed/superseded →
  `AI_INFERENCE`, published → `CONFIRMED_BUSINESS_TRUTH`).
- **One row per version, never mutated in place once published.**
  `identity.models.SemanticCatalogEntry` fuses `OnboardingArtifact`'s
  append-only versioning with `semantic.metrics.MetricDefinition
  .supersedes`'s chain: editing a published entry always creates a new
  `DRAFT` row (a new `version` for the same `concept_key`); publishing it
  flips the *previous* published row to `SUPERSEDED` and sets this new
  row's `supersedes_id` — a concept's full version history stays
  inspectable, never overwritten. `semantic.catalog
  .VALID_STATUS_TRANSITIONS` is the single source of truth for which
  transition is legal, checked by both `identity/repositories
  /semantic_catalog.py` (defense in depth) and `api/semantic_catalog.py`
  (the user-facing 409).
- **A vector-store sync failure on publish never rolls back the already-
  committed status change** — the identity DB remains authoritative; the
  failure is logged and surfaced as a non-fatal condition, the same
  "retrieval is best-effort, never a hard gate" philosophy `retrieval
  .retriever.retrieve_business_context`'s own fail-open contract already
  establishes elsewhere. See `09_SEMANTIC_CATALOG_CONTRACT.md`'s own
  known-limitations section for the disclosed resync gap this leaves.
- **Deliberately a new `ChunkType`, not a reuse of `GLOSSARY`/`METRIC`**
  — a governed catalog entry carries structured governance fields
  (status/owner/confidence/evidence/version) a hand-authored YAML
  glossary/metric chunk never does; conflating the two under one type
  would make them indistinguishable during rerank/labeling. The existing
  `semantic/metrics.py`'s `MetricDefinition`/`MetricRegistry` is reused,
  not duplicated, for `METRIC`-type catalog entries specifically:
  `semantic.catalog.build_metric_registry_from_catalog` converts
  published metric entries into real `MetricDefinition`s
  (`status=APPROVED`) via `YamlMetricRegistry`'s own existing
  constructor — closing `02_TARGET_ARCHITECTURE.md` §5's long-named gap
  "a real approval-workflow/CRUD surface sets `owner`/`APPROVED`."
- **Tenant isolation mirrors `onboarding.policy` exactly** —
  `SemanticCatalogEntry.tenant_id` + `semantic.catalog_policy
  .authorize_catalog_action` (deny-by-default, cross-tenant denial
  checked before any RBAC branch, mapped to the same 404 a genuinely
  nonexistent entry would get — anti-enumeration, not a distinguishable
  403). `CATALOG_MANAGE` (create/edit-draft/publish) is `_ADMIN`-only;
  `CATALOG_REVIEW` (review/request-changes) is `_ANALYST`+.

**Read [`09_SEMANTIC_CATALOG_CONTRACT.md`](../../09_SEMANTIC_CATALOG_CONTRACT.md)**
for the full inspection findings, every new module's design, the 91-test
testing summary, and the security/tenant-isolation/performance review.


## Multi-tenant enterprise architecture (Prompt 20)
Tenant isolation became a first-class platform boundary. Before this,
`tenant_id` already existed on five tables and four deny-by-default ABAC
policy modules genuinely compared it — but `security.tenancy
.resolve_actor_tenant_id` returned **one hardcoded constant for every
user**, so no two real callers could ever be in different tenants and every
one of those comparisons was vacuous in practice. The *shape* of tenant
isolation existed; the *fact* of it did not. `identity.models.Tenant` is now
a real table, `users.tenant_id` a real `RESTRICT` foreign key onto it, and
an actor's tenant is read from their own account row — **no call site of
either resolver function had to change** for those four policy modules to
become load-bearing, which was the entire reason resolution was routed
through one module in the first place.

**Invariants that must not regress:**
- **A tenant id is never read from anything a client sends.** It comes only
  from a `User` row this server loaded, this app's own signature-verified
  JWT `tid` claim (minted by `identity.security.create_access_token` from
  that row), or an IdP-signed claim named by `Settings.oidc_tenant_claim`.
  `security.tenancy.reject_client_tenant_override` additionally **refuses**
  (400) any request carrying `X-Tenant-Id`/`X-Tenant`/`Tenant-Id`/
  `X-Tenant-Override`, at `api/auth.py`'s single authentication chokepoint,
  before anything is authenticated — including in `AUTH_MODE=none`.
  Refusing rather than ignoring is deliberate: ignoring is safe today but
  leaves no signal if a future proxy config starts forwarding one.
- **Unknown means deny, never "default".**
  `security.tenancy.resolve_tenant_context` is the one gate, failing closed
  on a missing/soft-deleted/`"suspended"` tenant and on `None`, so a
  suspension takes effect across every tenant-scoped route at once rather
  than needing a per-route check. Every failure logs the real reason
  server-side but raises one generic `safe_message` — a caller must not be
  able to tell "no such tenant" from "suspended" from "not yours".
- **Status is read live, never cached.** Login *and* refresh both re-check
  it, bounding a suspension's effect to one access-token lifetime rather
  than the much longer refresh-token lifetime.
- **A shared database still needs per-tenant caches and retrieval.** Two
  tenants may legitimately share one configured connection, which is what
  makes these partitions load-bearing rather than redundant: the
  `POST /execute` result cache key is `(tenant_id, database_name, sql)` (it
  sits *after* every authorization gate, so it cannot rely on one), a golden
  example carries `tenant_id` in both its document id and filterable
  metadata (plus a per-document re-check behind the Chroma `where` filter —
  never trust the store to have applied it), and a published
  semantic-catalog chunk now records `tenant_id` in queryable metadata
  rather than only inside an opaque `source_id` string nothing parsed (the
  one real cross-tenant leak this prompt found and closed).
  Operator-authored chunks — live schema introspection and
  `data/knowledge/*.yaml` — deliberately carry no tenant and stay shared:
  that is deployment configuration, not one tenant's private content.
- **`observability.metrics.PerformanceMetrics` partitions per tenant** and
  `GET /metrics/performance` returns only the caller's own slice. The route
  is admin-gated, but this codebase has no platform-admin-versus-tenant-admin
  distinction, so admin-gating alone would have let one tenant's admin read
  another's latency/status/cache numbers. `snapshot()` with no argument keeps
  its merged, process-wide meaning for operator use and is deliberately not
  reachable over HTTP.
- **`DB_<NAME>_TENANT_IDS` binds a connection to specific tenants.** Unset
  (the default, and what every existing `.env` has) means shared by every
  tenant — so the upgrade changes nothing. Once set,
  `Settings.databases_for_tenant` filters it out everywhere:
  `POST /execute` and `GET /schema/tables` resolve names through that view
  (cross-tenant name → the same 404 a nonexistent name gets,
  anti-enumeration), and `embeddings.retriever.select_database` never even
  scores it, so a question can't be routed to — or have schema retrieved
  from — a database the asking tenant may not use.
- **The migration is non-destructive.**
  `a4c7e2b9d6f5_multi_tenant_boundary` seeds the `default` tenant row
  *before* either new column exists (no window in which an existing row
  violates the new FKs), and both columns are `NOT NULL` with
  `server_default='default'` — precisely the value
  `resolve_actor_tenant_id` already returned. `identity.bootstrap
  .ensure_default_tenant` is called from `seed_rbac` (deliberately folded
  in, so none of the dozen callers can forget it) to keep a
  `create_all`-built database consistent with a migrated one.

- **Shared model infrastructure carries no tenant context.** The cached
  `ollama.Client` and the compiled LangGraph graph are process-wide
  singletons; a tenant is a plain value in the per-request `AgentState`
  dict (the identical `selected_database`/`selected_model` pattern), never a
  field on the graph or a mutation of the cached `Settings`. The one piece
  of ambient state this prompt adds — `security.audit_log
  .set_audit_tenant_id`, which stamps `tenant_id=` onto every security event
  without touching ~40 call sites — is a `ContextVar` bound and reset in a
  `try`/`finally` around the graph invocation. Proven under **real
  concurrency** (`tests/security/test_cross_tenant_shared_infrastructure.py`),
  mirroring `tests/test_model_selection_concurrency.py`'s own threading
  regression test for the identical bug class: a sequential test would pass
  even with a global-mutation bug.

**Known limitations, named rather than left to be rediscovered:** the five
pre-existing `tenant_id` columns still have no FK onto `tenants` (adding one
could fail mid-upgrade on a real deployment for a modest integrity gain);
`AUTH_MODE=none`/`static_token` are not meaningful tenant boundaries (every
caller under either is indistinguishable by design); there is no
self-service tenant assignment and no admin route for tenants (CRUD exists
in `identity/repositories/tenants.py`, unexposed); no code path moves a user
between tenants, and `identity.repositories.history._tenant_matches_owner`
fails *closed* if a conversation's tenant ever disagrees with its owner's;
attachments/rate limits/concurrency limits are keyed by caller identity, not
tenant, so a noisy-neighbour tenant can still consume a shared global
budget; **PII classification (`config/sensitive_columns.yaml`) and RBAC
role/permission *definitions* remain global** — both are deliberately scoped
out rather than half-done, since making either per-tenant means moving it
out of shared seed data into a governed, reviewable store; and **no live
two-tenant deployment has been exercised** — every invariant above is
covered against an in-memory SQLite identity database.

**Read [`docs/MULTI_TENANCY.md`](../MULTI_TENANCY.md)** for the full
decision record, the per-resource isolation table, and every disclosed
limitation.


## Enterprise security & data governance hardening (Prompt 21)
A comprehensive, route-by-route deterministic-security audit
(`21_ENTERPRISE_SECURITY_DATA_GOVERNANCE_CONTRACT.md`) — not a new
subsystem. The headline finding is reassuring rather than alarming: RBAC
coverage across every route in `api/` is solid (every mutating/expensive
route already has a real permission/identity dependency; no route is
missing auth entirely, no sibling routes in the same router carry
inconsistent permission levels), CORS wildcard-rejection, the SQL AST
validator, and restricted-column enforcement are all unchanged and
intact, and every langgraph/langchain/chromadb CVE already triaged in
`docs/security/CVE_TRIAGE.md` was re-verified genuinely unreachable
against the *current* codebase (zero new `CachePolicy`/`Checkpointer`/
`ChatOpenAI` usage introduced since that triage) and already covered by
CI's own justified `--ignore-vuln` allowlist.

**Three real, scoped gaps were found and closed:**
- **SSRF rejections weren't audit-logged**
  (`media_gen/download.py::_validate_download_url`) — a real,
  previously-disclosed gap (`docs/security/PRODUCTION_SECURITY_READINESS_REPORT.md`'s
  P1-6, dated 2026-09-18), confirmed still open. The SSRF *defense* itself
  was already correct and tested; a blocked attempt simply left no
  `security.audit_log` trail, unlike every other rejection class in this
  app. All three rejection points (non-HTTPS scheme, no hostname,
  private/reserved resolved address) now call `log_security_event
  ("ssrf_blocked", ...)` — covering both of this function's callers
  (media generation and AI-guided image editing) from the one shared
  implementation. A DNS resolution failure is deliberately **not** logged
  under this event type — no address was ever evaluated against the
  blocklist, so it isn't an SSRF rejection.
- **`POST /feedback/golden-example`/`POST /feedback/message`**
  (`api/main.py`) had no rate limit at all — the only mutating,
  store-writing routes in that file inconsistent with `/execute`/
  `/schema/refresh`'s existing `enforce_api_action_rate_limit` coverage, a
  floodable data-poisoning vector against the golden-example/feedback
  stores. Fixed by adding the identical call under two new action names
  (`"feedback_golden_example"`/`"feedback_message"`), reusing the existing
  `API_ACTION_RATE_LIMIT_PER_MINUTE` control — no new setting needed.
- **`POST /onboarding/jobs`/`.../discover`/`.../publish`**
  (`api/onboarding.py`) each open a live outbound connection to a
  caller-supplied `db_host`/`db_port` with no rate limit at all.
  `ONBOARDING_MANAGE`-gated (admin-only) already, but `POST
  /onboarding/jobs` in particular was a repeatable TCP-connect oracle —
  its success/failure plus classified failure-reason message leak
  host/port reachability — against any host/port an authenticated admin
  names. New `agent.rate_limit.get_onboarding_connection_test_limiter`
  (identical shape to the existing `get_share_invite_limiter`: per-user,
  hourly, a `BoundedLimiterCache`-backed `SlidingWindowRateLimiter`) +
  `Settings.onboarding_connection_rate_limit_per_hour` (default `10`),
  checked via a shared `api.onboarding._enforce_connection_test_rate_limit`
  helper called immediately after each route's own `_authorize(...)` --
  an unauthorized caller is denied on its own terms first, never counted
  against this budget.

**Found and re-verified, not fixed (already correct):** onboarding's own
SQL-evaluation path (`onboarding/evaluation.py`) deliberately does **not**
run generated SQL through `config.sensitive_columns`-restricted-column
enforcement — correctly so, since at onboarding time the only caller is
the admin who already supplied the raw database password directly (no
narrower-permission role exists yet for a database still being onboarded,
and the classification doesn't apply to an unprofiled database), unlike
`validate_sql_node`'s restricted-column gate, which exists specifically to
bound what an *under-privileged* caller's LLM-generated SQL may reach.

**Known, disclosed limitations carried forward, not re-closed this
pass** (already named in `docs/security/PRODUCTION_SECURITY_READINESS_REPORT.md`,
confirmed still accurate as of this session): DAST has never been run
against a live deployment; OIDC has never been verified end-to-end
against a live Identity Provider; malware scanning (`clamav`) has never
been verified against a real daemon; no backup/recovery procedure is
documented. All four are environment-blocked (this sandboxed session has
no live, network-reachable deployment, no real IdP, and no real ClamAV
daemon to test against), not oversights. `mypy .`'s error count grew from
117 (2026-09-19) to 340 across 56 files — confirmed via a fresh run that
100% of the growth is confined to `tests/`/`eval/security_benchmark/`
`AgentState`-vs-`dict` type-looseness (a pre-existing, disclosed category
this project already scoped out as "a real, bounded, separate
mypy-hygiene pass"), **zero new errors in any production security-relevant
module** (`agent/`, `api/`, `db/`, `security/`, `identity/`).

**Read [`docs/security-changelog.md`](../security-changelog.md)'s
2026-10-02 entry** for the dated, audit-trail-formatted record of the two
rate-limit additions, and
[`21_ENTERPRISE_SECURITY_DATA_GOVERNANCE_CONTRACT.md`](../../21_ENTERPRISE_SECURITY_DATA_GOVERNANCE_CONTRACT.md)
for the full prompt text and outcome summary.


## Observability, evaluation & reliability (Prompt 23)
Two additive pieces, reusing far more existing machinery than it added:
per-stage tracing that threads correlation ID, tenant, and
stage-specific metadata through every log line (extending `agent.nodes
._timed_node`, `security/audit_log.py`, and `config.settings
.configure_logging` — all pre-existing), and a new, fully-deterministic
`eval/component_benchmark/` covering the four domains that had zero
evaluation-dataset coverage before this prompt.

**Tracing.** `agent.nodes._timed_node` (already wraps every one of the
graph's nodes, see "Self-correcting retry loop" above) now also emits an
additive `[trace] stage=<name> attempt=<n> duration_ms=<ms> <fields>`
log line alongside its pre-existing `[timing]` one (kept byte-for-byte
unchanged — `scripts/profile_pipeline.py` parses it). `<fields>` —
`status`/`error_category`/`model`/`db_provider`/`query_fingerprint`/
`result_size` — are read from already-existing `AgentState` keys with
**zero changes to any individual node's own return contract**, the
identical "cross-cutting, no node needs to know this exists" principle
`stage_timings` itself already established; each is omitted outright
(never a literal `None`) when not meaningful for that stage.
`query_fingerprint` (`agent.nodes._query_fingerprint`) is a bare SHA-256
prefix of the SQL text — never the text itself or anything derived from
a literal value in it, a stronger guarantee than a masked-literal preview
(`db.query_store._mask_literals`'s own, different use case) would give.
`security/audit_log.py` gained `TenantIdLogFilter` (identical mechanism
to the pre-existing `CorrelationIdLogFilter`, wired in alongside it by
`config.settings.configure_logging`) — closing a real, found gap: a
request's tenant was previously visible only on `security.audit` events,
never on an ordinary `logger.info(...)` call (the `[timing]`/`[trace]`
lines included). Together, this is the literal mechanism behind this
prompt's acceptance criterion: a single `grep correlation_id=<id>` now
surfaces one request's full stage-by-stage trace — including which
stage set an error category, which model/database it used, and whether
its result set was empty — without needing a second monitoring system.

**Evaluation.** `eval/component_benchmark/` (new) covers **semantic**
(governed-metric rendering determinism, `agent.llm_client
._build_mandatory_metrics_block`), **planning** (the analytical-plan
validator, `agent.plan_validator.validate_plan`), **analytics** (the
deterministic analytics engine, `analytics.engine
.compute_analytics_result`), and **recommendations** (the evidence-first
recommendation engine, `recommendation.engine.generate_recommendations`)
— the four domains Prompt 23 named that had no dedicated evaluation
dataset at all before this pass (each already has its own, much more
fine-grained pytest unit-test suite from the prompt that introduced it;
this is a smaller set of named, realistic, regression-tracked scenarios,
a qualitatively different thing from unit coverage, not a re-run of it).
Every case calls a real production function **directly**, never a mock
— all four domains are pure/deterministic with no I/O, which is what
makes this the first `eval/` harness that's also wired into the ordinary
`pytest` suite (`tests/test_eval_component_benchmark.py`) rather than
staying a manual script, unlike `eval/runner.py`/`eval/security_benchmark
/runner.py`, both of which need a live database and/or LLM this
environment doesn't have. `eval/component_benchmark/regression.py` is a
deliberately small, new, **case-level** (binary pass/fail per named
case) regression gate against a committed baseline
(`eval/baselines/component_benchmark_latest.json`) — not a reuse of
`eval/regression.py`'s own metric-drift/tolerance-band logic, which is
shaped for the SQL benchmark's scored accuracy/latency metrics and has
no equivalent concept here. `scripts/run_component_benchmark.py` is the
operator-facing CLI (`--save-baseline`/`--check-regression`), mirroring
`scripts/run_benchmark.py`'s own flag shape.

**Reused, not duplicated — the three domains this prompt also named that
already had real coverage:** "SQL" is the existing, mature
`eval/benchmark/*.yaml` harness (`eval/runner.py`/`eval/regression.py`,
manual, needs a live DB+LLM); "security" is `eval/security_benchmark/`
(the 500-case prompt-injection benchmark); "tenant isolation" is
`tests/security/test_cross_tenant_isolation.py`/
`test_cross_tenant_shared_infrastructure.py` — a real, CI-enforced
pytest regression suite, arguably a *stronger* regression gate than a
parallel YAML dataset would be, since it already runs on every PR. None
of these three got a parallel dataset built alongside them.

**Known limitations, disclosed rather than hidden:** the component
benchmark's "planning"/"analytics"/"recommendations" cases are
hand-authored and necessarily non-exhaustive (28 cases across 4
domains) — they catch a *regression* in already-identified, realistic
scenarios, not every possible input shape (that's what each domain's own
much larger pytest suite is for). `[trace]`'s `model`/`db_provider`
fields reflect whichever model/database was *selected* for this
question, not a live token-count/dollar-cost figure — no per-call
token/cost metadata is captured anywhere in this pass (Ollama's own
`total_ms`/token-count breakdown, captured by `observability
.llm_timing_capture.capture_llm_timings`, remains a separate,
pre-existing, benchmark-only mechanism, not merged into this log-line
trace). No new distributed-tracing backend (OpenTelemetry spans,
Prometheus histograms) was introduced — unchanged, deliberate posture,
see `docs/OBSERVABILITY.md`'s own "Known gaps" section.

**Read [`docs/OBSERVABILITY.md`](../OBSERVABILITY.md)**'s §3/§3a for
the full `[trace]`/tenant-propagation design, and
[`23_OBSERVABILITY_EVALUATION_RELIABILITY_CONTRACT.md`](../../23_OBSERVABILITY_EVALUATION_RELIABILITY_CONTRACT.md)
for the full prompt text and outcome summary.


## Full integration & regression (Prompt 24)
A validation pass, not a new-feature pass — the mandatory review of
Prompt 01's baseline against everything built since found one real,
structural gap: **nothing in this codebase had ever proven the 18 real
LangGraph nodes compose correctly through the actual compiled graph.**
Every existing test either called one node function directly in
isolation (`tests/test_agent_nodes.py`) or mocked `agent.graph.run_agent`
itself at the API boundary (`tests/test_api_ask.py`,
`tests/test_orchestrator.py`) — confirmed by grepping every `run_agent(`
call site in the test suite, not assumed. New
`tests/test_full_pipeline_integration.py` closes this: five tests that
invoke the real, compiled graph end-to-end, mocking only the genuine
network/DB boundary (`agent.nodes.retrieve_relevant_schema`/
`retrieve_golden_examples`/`retrieve_business_context`, the seven
`*_from_llm` functions in `agent/llm_client.py`, and
`agent.nodes.execute_readonly_sql`) — covering the basic Text-to-SQL
journey; the self-correction retry loop *actually looping* through the
real conditional edges (not just `route_after_execution` returning the
right string in isolation, already covered elsewhere); a declining-
revenue result flowing through the real `analytics.engine
.compute_analytics_result` → real `recommendation.engine
.generate_recommendations` → real insight generation together in one
run; `review_metric_conformance_node` actually engaging mid-run when a
governing metric is retrieved; and an injection attempt being rejected
before any generation/execution mock could even be reached. **All five
passed without needing any production-code fix** — real, measured
confirmation that the graph still composes correctly across every
Prompt-9-through-23 feature at once, not an assumption.

**Two further gaps found and closed while comparing against the Prompt
01 baseline, both documentation/test-coverage drift, neither a behavior
regression:**
- `tests/test_sql_agent_integration.py
  ::test_existing_nodes_are_all_still_present` had silently drifted out
  of sync with the real graph — missing `retrieve_business_context`
  (Prompt 9), `classify_analytical_intent` (Prompt 11),
  `review_metric_conformance` (Prompt 10), and `generate_recommendations`
  (Prompt 17) from its own checklist (a subset-containment check, so the
  gap never failed, it just silently under-tested four of eighteen real
  nodes). Now an exhaustive equality check instead, so a future added or
  removed node must touch this test too.
- `docs/ARCHITECTURE.md`'s `StateGraph` diagram and per-node prose
  walkthrough were stale since roughly Prompt 8/9 — still describing
  "the twelve nodes" and completely missing all six nodes added since.
  Updated to the real, current eighteen-node graph, mermaid diagram
  included, plus the two new retry-table rows (`metric_definition_not_used`,
  `database_busy`) Prompts 10/22 added without ever reaching that table.

**Confirmed already adequate, deliberately not duplicated:** the
"new-client onboarding" journey (`tests/test_api_onboarding.py
::TestFullLifecycle::test_create_discover_review_publish` — a real
create → discover → review → publish lifecycle against a real SQLite
target database) and the "unauthorized access" journey
(`tests/test_api_authz.py`'s vertical/horizontal privilege-escalation,
missing/invalid-role, and expired/invalid-credential suites) both
already had real end-to-end coverage before this pass. The frontend
test suite (292 tests, `frontend/`'s own `vitest`) and production build
(`npm run build`) were both run fresh in this session and confirmed
green/clean — zero backend-driven frontend regressions.

**Read
[`24_FULL_INTEGRATION_REGRESSION_CONTRACT.md`](../../24_FULL_INTEGRATION_REGRESSION_CONTRACT.md)**
for the full prompt text and outcome summary.


## Production readiness & release gate (Prompt 25)
Before this pass, production-readiness guidance was scattered across six
overlapping documents accumulated over three prior sessions — and two of
them had gone actively **wrong**, not just stale:
`docs/RISK_REGISTER.md`'s R-001 title still read "No authentication or
per-user authorization by default," and `docs/PRODUCTION_CHECKLIST.md`
still said "neither the UI nor the API has real auth." Both predate the
real OIDC/local-account/RBAC/tenant-isolation system that has existed for
several prompts now — both corrected in place this pass, with a pointer
to the current evidence, not deleted. The two broader reports
(`docs/PRODUCTION_READINESS_REPORT.md`'s 2026-09-01 69/100 score,
`docs/security/PRODUCTION_SECURITY_READINESS_REPORT.md`'s 2026-09-18
verdict) were simply frozen snapshots — the first predates the entire
auth system and 23 further prompts of work; the second predates Prompts
19-24 (Query Store, multi-tenancy, this project's own Prompt 21 security
audit, scale hardening, observability, full-pipeline integration
testing) entirely. Both now carry an explicit "superseded by" pointer at
the top, kept as historical evidence rather than rewritten.

New **`docs/PRODUCTION_READINESS_RELEASE_GATE.md`** is now the single,
current, authoritative source — extending (not replacing) `docs/security
/FINAL_PRODUCTION_GATE.md`'s own well-evidenced 25-row matrix with the
rows that changed since September (Rate Limiting, Monitoring, Backup/
Recovery, Dependencies — each re-verified fresh, not assumed) and a new
set of rows this prompt's own broader scope required and no prior
document covered at all: Tenant Isolation, Database Safety, Onboarding
Engine, Semantic Catalog/Governed Metrics, Analytics Correctness,
Recommendation Evidence, AI Accuracy, the Prompt-Injection Benchmark, and
Scale. **Honest finding, not glossed over:** the only real Text-to-SQL
accuracy numbers that exist (`docs/EVALUATION_CURRENT.md`'s 92.5%/42.3%/
50%) are from a live run dated 2026-09-16 — 13 prompts of real pipeline
changes ago — and could not be re-measured in this pass for the same
reason every other live-infrastructure gap in this codebase stays open:
no live Ollama instance or database exists in this sandboxed
environment. Flagged as a new P1, not silently assumed still accurate.

**Two P1 findings were concretely closeable here and were closed**:
`docs/DEPLOYMENT.md` gained real **Backup & Recovery** (what this app
itself owns — the optional identity Postgres database via standard
`pg_dump`/`alembic downgrade`, the fully-regenerable Chroma vector index,
`.env`/configuration — explicitly *not* the operator's own connected
business database, which remains correctly out of scope since this app
is only ever a read-only client of it) and **Rollback** (digest-pinned/
tag-addressable container images, `alembic downgrade` — every one of
this project's 9 migrations confirmed to have a real `downgrade()` by
direct read — and the fact that every feature added since Prompt 08 is
behind its own `.env` flag, so disabling a flag is almost always a
faster, safer "rollback" than a code/image rollback at all) sections,
neither of which existed before this pass despite being a named,
disclosed gap since the 2026-09-18 report.

**The three P0 findings carried forward unchanged, still genuinely
environment-blocked**: DAST has never been run (no staging environment/
scanner available), OIDC has never been verified against a live Identity
Provider (none available), and malware scanning (ships disabled by
default) has never been verified against a real ClamAV daemon (none
available) — identical constraints every prior session already
disclosed, not newly discovered, not newly closed.

**Verdict: CONDITIONALLY READY** (a real change from the prior
engagement's NOT READY framing) — not because any environment-blocked
item closed, but because this pass freshly re-verified that everything
built across Prompts 9 through 24 still holds up under direct
re-inspection, and closed every gap that was actually fixable without
live infrastructure.

**Read [`docs/PRODUCTION_READINESS_RELEASE_GATE.md`](../PRODUCTION_READINESS_RELEASE_GATE.md)**
for the full gate matrix, P0-P3 findings, release checklist,
prerequisites, and post-release monitoring plan, and
[`25_PRODUCTION_READINESS_RELEASE_GATE_CONTRACT.md`](../../25_PRODUCTION_READINESS_RELEASE_GATE_CONTRACT.md)
for the full prompt text and outcome summary.


## Database Onboarding Portal (Prompt 26)
The first real frontend workflow built on top of Prompt 08's onboarding
engine — `frontend/src/pages/DatabaseOnboarding.tsx`, a new, role-gated
nav tab (`frontend/src/components/layout/AppShell.tsx`), using
`api/onboarding.py`'s 10 real routes end to end with **no mock or
placeholder data anywhere in the page**. A new `frontend/src/lib
/onboardingApi.ts` (thin `request()` wrappers, mirroring `lib/identityApi
.ts`'s own precedent) and matching types in `lib/types.ts` are the only
new API surface; `frontend/src/hooks/queries.ts` gained the React Query
hooks. `vite.config.ts`'s dev-proxy `BACKEND_ROUTES` list was missing
`/onboarding` entirely — a real, necessary fix found during inspection,
not something this page could have worked around.

**Deliberately honest about what the real backend does, not a literal
reading of a generic wizard outline** — `DatabaseOnboarding.tsx`'s own
docstring states every one of these, so a future reader doesn't have to
rediscover them by reading `onboarding/jobs.py` again:
- **"Test connection" and "create job" are the same backend call**
  (`POST /onboarding/jobs` tests the connection and only creates the job
  if it succeeds) — one combined "Test connection & create job" action,
  not two buttons for a distinction the backend doesn't make.
- **"Start semantic analysis" and "generate golden questions" are not
  separate steps** — both already happen inside `POST .../discover`
  (Prompt 08's own `run_discovery_stage`); their results (semantic-label
  and golden-question review items) simply appear once discovery
  finishes.
- **"Run evaluation" is not a pre-publish action** — it happens
  automatically inside `POST .../publish` (`publish_job`'s own
  `evaluate_candidates` call); its result is the `evaluation_report`
  artifact shown once the job reaches `published`.
- **No "create/select tenant" step exists** — a job's tenant is resolved
  server-side from the caller's own account
  (`security.tenancy.resolve_actor_tenant_id`) and is never a
  client-supplied value (`docs/MULTI_TENANCY.md` rule 1); this UI never
  presents a selector for a value the server would ignore.
- **Progress is an honest loading state, not a fabricated multi-stage
  bar.** `POST .../discover`/`POST .../publish` are synchronous, no-
  background-worker calls (`onboarding/jobs.py`'s own module docstring)
  — there is no intermediate signal to poll, so the UI shows a live
  elapsed-time counter (`useElapsedSeconds`, reused, not duplicated)
  while the one request is in flight, never invented intermediate steps.
- **"Cancellation" of an in-flight discover/publish call is client-side
  only**, disclosed as such in the UI copy — there is no server-side
  mechanism to interrupt a running call (same no-background-worker
  design); `POST .../cancel` genuinely works, but only against a job in
  a resting (non-terminal, not-mid-call) state.
- **No dedicated "profiling" display** — the backend never returns raw
  column-profile statistics to any caller; they only ever feed semantic-
  label inference and PII confidence internally. The one real, exposed
  profiling-derived number (`duplicate_key_count`) is shown, honestly
  labeled, in the discovery summary alongside table/view/relationship/
  PII/golden-question counts.
- **"Show schemas/tables/views/relationships"** is served entirely from
  `OnboardingReviewItem`s already returned by `GET .../review-items` --
  `semantic_label` items (one per non-view column, Prompt 08's own
  `infer_semantic_labels` docstring: "One `SemanticLabel` per non-view
  column") double as the schema/column browser, grouped by table in the
  UI's "Schema & columns" tab; no new backend endpoint was needed or
  added. Views are not individually itemized (semantic labeling skips
  them by design) — disclosed in the discovery-summary count, not hidden.

**Permission gating, UX-only by this codebase's own stated principle**
(`AuthGate.tsx`'s own docstring: "no UI-level restriction is a security
boundary anywhere in this codebase") — the nav tab itself is hidden for
an account with neither `admin` nor `analyst` in its roles (the one nav
tab gated this way, see `docs/navigation-and-actions.md`'s updated
table), "New job"/discover/publish/cancel/retry are admin-only inside
the page, and review-item confirm/reject is admin-or-analyst — all
re-enforced server-side regardless (`identity/rbac.py`'s own
`ONBOARDING_MANAGE`/`ONBOARDING_REVIEW` grants, unchanged by this prompt).

**A real regression found and fixed during this pass, not merely
avoided**: an early version of the nav-tab role check
(`useLocalAuthStore((state) => state.user?.roles ?? [])`) returned a
freshly-allocated array on every call when no user was signed in —
breaking Zustand's snapshot-equality check and crashing the entire shell
with React's "Maximum update depth exceeded" guard. Caught by this
app's own pre-existing `AppShell.test.tsx` suite, not discovered by
manual testing — fixed by selecting the already-stable `user` object
itself and deriving `.includes(...)` checks outside the selector.

**Known, disclosed limitations:** the wizard's own UI text is in
English only (the nav label is translated across all 5 locales, matching
`MediaSearch.tsx`'s own precedent of translating the minimum bar rather
than every string on a large new page); the password re-entry fields for
discover/publish/retry are a real, disclosed consequence of this app's
own "never persist a connection secret" design (`identity.models
.OnboardingJob`'s own docstring), not an oversight; a job's "+ New job"
form does not support `DB_CONNECTION_STRING`-style full-URL connections,
only the discrete-field shape `CreateOnboardingJobRequest` already
supports.

**Read [`26_DATABASE_ONBOARDING_PORTAL_CONTRACT.md`](../../26_DATABASE_ONBOARDING_PORTAL_CONTRACT.md)**
for the full prompt text and outcome summary.


## SME Semantic Review Dashboard (Prompt 27)
The first real frontend surface for the tenant-aware semantic catalog
(`semantic/catalog.py`, `api/semantic_catalog.py`, Prompt 09/10) --
`frontend/src/pages/SemanticReview.tsx`, a second role-gated nav tab
(sharing Prompt 26's identical admin/analyst gate, since
`CATALOG_MANAGE`/`CATALOG_REVIEW` sit at the same two RBAC tiers as
`ONBOARDING_MANAGE`/`ONBOARDING_REVIEW`). Before this prompt, the
catalog had **zero frontend** despite existing since Prompt 09 -- every
entity/metric/dimension/domain lived only behind direct API calls.

**Two real backend review systems exist, never bridged into one**
(confirmed by inspection: nothing in `onboarding/` ever creates a
`SemanticCatalogEntry` row). This dashboard is the catalog's own review
surface -- the actual subject of this prompt's own "Revenue metric:
source, proposed expression, evidence, confidence, version, reviewer"
example -- plus a compact, linked-not-duplicated summary of onboarding
jobs still `awaiting_review`, reusing the *existing* `ReviewItemsSection`
component and `DatabaseOnboarding` page (Prompt 26) for the actual PII/
relationship/semantic-label/golden-question review interaction rather
than re-implementing that table a second time (master-contract rule 3).

**The prompt's five actions (Confirm, Reject, Edit, Request
Clarification, Defer) don't map one-to-one onto the catalog's three real
transitions** (`identity.repositories.semantic_catalog
.VALID_STATUS_TRANSITIONS`: draft→reviewed, reviewed→draft,
reviewed→published) -- mapped honestly, disclosed in the page's own
docstring rather than faked:
- **Confirm** is context-sensitive: "Approve" (draft→reviewed, any
  reviewer) or "Publish" (reviewed→published, admin/`CATALOG_MANAGE`
  only), whichever transition the entry's current status allows.
- **Reject has no backend equivalent for a catalog entry at all** --
  there is no "rejected" `CatalogStatus`. The closest real action is
  **Request Changes** (reviewed→draft, with notes), shown only for a
  *reviewed* entry; a draft entry can only be approved, edited, or
  deferred.
- **Edit** is the real `PATCH` route, and only succeeds on a `draft`
  entry (the one row this table ever allows an in-place edit on).
- **Request Clarification** is the real `request-changes` route.
- **Defer** makes no API call at all -- it advances the reviewer to the
  next item in the priority-sorted queue, leaving the entry's real state
  completely unchanged.

**Prioritization is a disclosed, deterministic client-side heuristic**
(`SemanticReview.tsx::priorityScore`) -- a conflict scores highest (two
published claims can't both be right), then low confidence (<70%), then
`concept_type == "metric"` ("high-impact": a metric definition feeds
every KPI question that uses it) -- never a hidden model, matching
`onboarding/semantic_inference.py`'s own transparent ambiguity-flag
precedent.

**Two real, additive backend gaps found and closed, both in
`api/semantic_catalog.py`/`api/semantic_catalog_schemas.py`:**
- `CatalogEntryOut` never surfaced *who* reviewed/published an entry
  (`SemanticCatalogEntry.reviewed_by_user_id`/`published_by_user_id`
  always existed on the ORM row; nothing read them back) -- now exposes
  `reviewed_by_display_name`/`published_by_display_name` too (a
  best-effort lookup, `None` once that account is deleted via the FK's
  own `ondelete="SET NULL"`, never a reason the route fails), the literal
  "reviewer" field the prompt's own metric example names.
- `GET /semantic-catalog/entries` never recomputed conflicts (only
  `create`/`publish` did, by design, per Prompt 10's own docstring) --
  this dashboard needs to prioritize conflicting entries from the list
  view, so a new opt-in `include_conflicts` query param (default `false`,
  zero behavior/latency change for every pre-existing caller) reuses the
  identical, already-bounded `find_conflicting_published_entries` check,
  run only for a `published` entry in the result set (a draft/reviewed
  row can't itself be in conflict with a published claim).

**A real, pre-existing concurrency bug was found and fixed while writing
this prompt's own required "concurrent review" test, not a hypothetical:**
every status-transition function (`mark_reviewed`/`request_changes`/
`publish_entry`/`update_draft_entry`) used to validate against the
caller's own in-memory `entry.status` and then commit an *unconditioned*
`UPDATE` -- two concurrent callers could both pass that check (racing to
publish, or one editing a draft the instant another approves it) and
both "succeed," silently losing the "only one caller's transition should
ever win" guarantee a 409 is supposed to provide. Fixed by
`identity.repositories.semantic_catalog._apply_transition`: one atomic
`UPDATE ... WHERE id = entry.id AND status = expected_current`,
portable across every SQL dialect this app supports -- no advisory lock
or dialect-specific code needed. Proven under **real `threading.Thread`s**
(`tests/test_api_semantic_catalog.py::TestConcurrentPublish`, mirroring
Prompt 20's own `test_cross_tenant_shared_infrastructure.py` precedent for
why a sequential test would pass even with the original bug). **Known,
disclosed, narrower scope than the fix might imply**: this closes the
single-row race (two callers transitioning the *same* entry); it does
**not** close the separate, rarer race of two *different* draft versions
of the *same concept* both reaching `published` concurrently with neither
seeing the other as `previous_published` (no shared row to guard via a
`WHERE` clause) -- a real, narrower, disclosed limitation, not silently
glossed over.

**Audit logging, the prompt's own explicit requirement, was missing for
every ordinary state change before this prompt** (only Prompt 10's
conflict-detection path called `security.audit_log.log_security_event`
at all). Added to `update_catalog_entry`/`review_catalog_entry`/
`request_catalog_entry_changes`/`publish_catalog_entry`
(`api/semantic_catalog.py`) and `decide_review_item_route`
(`api/onboarding.py`) -- six new, `"info"`-severity structured events,
each carrying the entry/item id and the acting user id.

**Known, disclosed limitations:** "Affected questions" (named in the
prompt's own metric example) is not tracked anywhere in this backend --
shown as an honest note in the UI, never fabricated. No deep link exists
from the onboarding-jobs summary card into a specific job on
`/db-onboarding` (that page's job selection is local component state,
not a URL param) -- the link opens the page, the reviewer still has to
pick the job themselves. The dashboard's own "+ New concept" form covers
only the core + metric-specific fields, not every `CreateCatalogEntryRequest`
field (relationships/business rules/examples/evidence are editable only
via the existing `EditEntryCard`'s own narrower field set, or directly
against the API).

**Read [`27_SME_SEMANTIC_REVIEW_DASHBOARD_CONTRACT.md`](../../27_SME_SEMANTIC_REVIEW_DASHBOARD_CONTRACT.md)**
for the full prompt text and outcome summary.

**Addendum (Prompt 28, same session):** while stabilizing the full test
suite for the next prompt's own new tests, this fix's real-threading
test (`TestConcurrentPublish`) turned out to itself be flaky under full-
suite load -- not because the fix was wrong, but because the test
fixture's `StaticPool` SQLite engine shares one single physical
`sqlite3.Connection` object across both "concurrent" threads (required so
multiple `Session`s can see one `:memory:` database at all), and
`sqlite3` connections are not documented as safe for genuinely
concurrent multi-threaded statement execution even with
`check_same_thread=False`. Replaced with a deterministic two-`Session`
test that interleaves explicitly (session 1 publishes and commits;
session 2's own stale, already-loaded object -- still saying
`"reviewed"` -- then attempts the same transition and must be rejected
by the atomic `WHERE status = expected_current` guard) -- this proves
the exact same property without depending on real OS-thread scheduling
against a test-only shared connection. The fix in `identity.repositories
.semantic_catalog._apply_transition` itself is unchanged.


## Global Platform Admin Dashboard (Prompt 28)
The first platform-wide operations surface in this codebase --
`frontend/src/pages/PlatformAdmin.tsx` + `api/platform_admin.py` -- and
the prompt that finally closes a gap named explicitly, as a known
limitation, back in Prompt 20: **this application had no platform-admin-
versus-tenant-admin distinction**. Every existing `admin`-gated route
(`/schema/refresh`, `/metrics/performance`, `ONBOARDING_MANAGE`,
`CATALOG_MANAGE`, ...) is correctly scoped to the actor's own tenant by
design -- a tenant's own admin must never see or act on another tenant's
data. This prompt's entire purpose is the deliberate exception: a new
`identity.rbac.Permission.PLATFORM_ADMIN`, granted only to a new
`platform_admin` role (`_PLATFORM_ADMIN = _ADMIN | {PLATFORM_ADMIN}` --
a genuine superset, so holding it also grants every tenant-admin
capability, but holding plain `admin` never grants this one), enforced
by a new `api.identity_authz.require_platform_admin` dependency
(`require_identity_permission(PLATFORM_ADMIN)`, literally the same
factory every other granular permission already uses -- no new
mechanism) on every route in the new `api/platform_admin.py` router.

**Heavy reuse, almost nothing built from scratch:**
- `identity/repositories/tenants.py`'s full CRUD (list/create/suspend/
  soft-delete) existed since Prompt 20 and was never exposed by any
  route until now -- this dashboard is exactly what it was waiting for.
- `identity.repositories.users.assign_role`/`remove_role` are the exact
  same functions every other role-assignment call site already uses.
- `observability.metrics.get_default_metrics().snapshot(tenant_id=None)`
  already supported the merged, cross-tenant view, "deliberately not
  reachable through the HTTP route" per that module's own docstring --
  `GET /platform-admin/metrics` is that route, finally.
- `GET /health`/`GET /models` are deliberately **not** duplicated under
  `/platform-admin/*` -- both are already public/unscoped, so the
  dashboard's Health & Models tab calls the existing `useHealth`/
  `useAvailableModels` hooks directly, same as every other page.

**Two further real, found-and-closed gaps, both disclosed rather than
silently worked around:**
- **`identity.models.AuditLog` was dead code.** The `audit_logs` table,
  with `Permission.AUDIT_READ_ANY`/`AUDIT_READ_OWN` seeded since this
  project's identity schema was first built, had never been written to
  or read from by anything in this codebase -- confirmed by grep: zero
  `AuditLog(` construction sites anywhere. New `identity/repositories
  /audit.py` (`record_audit_event`/`list_audit_events`) is the first real
  write/read path, wired into this prompt's own new mutating actions
  (tenant status changes, role assignment) only -- a disclosed, narrower
  scope than "every mutation in this application," named as a known
  limitation rather than implied complete. The table also has **no
  `tenant_id` column** (it predates Prompt 20 and was never retrofitted),
  so `list_audit_events` cannot be scoped to one tenant -- not a new leak,
  since its only caller is already `PLATFORM_ADMIN`-gated for
  cross-tenant visibility, but a real structural limit on ever reusing it
  for a future *tenant*-scoped audit view without a migration first.
- **"Security Events" had no queryable backing at all.**
  `security.audit_log.log_security_event`'s ~40 existing call sites only
  ever wrote to a Python logger -- no DB, no aggregation, nothing a
  dashboard could read. A bounded (`_MAX_RECENT_EVENTS = 1000`),
  thread-safe, in-process ring buffer now hooks inside that one function
  itself (`_record_recent_event`), so every one of those ~40 call sites
  started feeding `GET /platform-admin/security-events` with **zero
  changes to any of them** -- the identical "instrument the one shared
  chokepoint" approach `CorrelationIdLogFilter`/`TenantIdLogFilter`
  already use for ordinary log lines in the same module.

**Invariants that must not regress:**
- **Never exposes a credential.** `DatabaseStatusOut` has no
  `db_user`/`db_password`/`db_connection_string` field at all (not a
  redacted one -- the field simply doesn't exist in the response shape),
  and `GET /platform-admin/config-status` only ever reports `Settings`
  fields that are already plain Python `bool` -- a `pydantic.SecretStr`-
  typed field can never satisfy `isinstance(value, bool)`, so this is
  safe by construction, not by a redaction step that could be forgotten
  or bypassed.
- **`PLATFORM_ADMIN` is never implied by `admin` alone.** Proven directly
  (`tests/test_identity_rbac.py
  ::test_platform_admin_permission_is_granted_only_to_platform_admin_role`)
  and end-to-end (`tests/test_api_platform_admin.py
  ::TestPlatformAdminVsTenantAdminAccess` -- a tenant admin gets 403 on
  every route this router defines). The frontend nav tab mirrors this:
  `AppShell.tsx`'s `canSeePlatformAdmin` is a separate boolean from
  `canSeeReviewTabs` (Database Onboarding/SME Semantic Review's own
  admin-or-analyst gate), not folded into it -- a tenant's own admin must
  never see a tab that would 403 for them.
- **Cross-tenant visibility is proven, not assumed.** `tests/test_api_platform_admin.py
  ::TestCrossTenantDataVisibility` creates two real tenants with real
  accounts bound to each (via `identity.repositories.tenants
  .create_tenant`/`identity.repositories.users.create_user`'s own
  `tenant_id` parameter -- there is still no self-service tenant
  selection anywhere in this app to abuse) and confirms a single
  `GET /platform-admin/users`/`/tenants` call genuinely spans both.
- **A platform admin account is deliberately still subject to
  `agent.authz`'s own base-role bridge for ordinary AI/chat use.**
  `platform_admin` is not one of `identity.rbac.BASE_ROLE_NAMES`, so an
  account holding *only* that role (with no `admin`/`user`/`analyst`
  alongside it) would get zero permissions from `agent.authz
  .permissions_for` for the chat/SQL features themselves -- the exact
  same disclosed, intentional tradeoff `identity/rbac.py`'s own docstring
  already states for `auditor`/`manager`/`support`. An operator wanting
  both capabilities from one account assigns both roles.

**Known, disclosed limitations:**
- Audit-log coverage is narrow (this prompt's own new actions only, see
  above) -- a genuinely complete audit trail across every mutating route
  in this application remains a larger, separate undertaking.
- Security events reset on process restart and are single-process (a
  multi-worker deployment has one independent buffer per worker) -- the
  identical, already-disclosed limit `observability.metrics
  .PerformanceMetrics` carries, for the same reason (no shared store like
  Redis is part of this application's current architecture).
- `identity/repositories/users.py::list_users`/`identity/repositories
  /semantic_catalog.py::list_entries_across_tenants`/`identity/repositories
  /onboarding.py::list_jobs_across_tenants` are all simple
  limit-bounded queries with no cursor/offset pagination -- fine for this
  dashboard's realistic scale today, a disclosed gap for a much larger
  user/job/catalog-entry count.
- The frontend's role-assignment control on the Users tab is a free-text
  role-name input, not a dropdown constrained to real seeded role names
  -- `POST /platform-admin/users/{id}/roles` already validates server-side
  (400 on an unknown role), so this is a UX rough edge, not a security
  gap.

**Read [`28_GLOBAL_PLATFORM_ADMIN_DASHBOARD_CONTRACT.md`](../../28_GLOBAL_PLATFORM_ADMIN_DASHBOARD_CONTRACT.md)**
for the full prompt text and outcome summary.


## Client/Tenant Admin Dashboard (Prompt 29)
The tenant-scoped counterpart to Prompt 28's platform-wide dashboard --
`frontend/src/pages/TenantAdmin.tsx` + `api/tenant_admin.py`. **Unlike
Prompt 28, this prompt introduces no new RBAC permission or role at
all** -- a tenant admin is exactly what the existing, tenant-scoped
`admin` role already means. Every route resolves `tenant_id` exactly
once, from `security.tenancy.resolve_actor_tenant_id(user)`, and **no
route signature in `api/tenant_admin.py` accepts a `tenant_id` parameter
at all** -- not a check that could be forgotten on one route, but a
shape no route in the file can violate. This is the structural guarantee
behind "tenant admins cannot access platform-wide or other-tenant data,"
proven directly (`tests/test_api_tenant_admin.py::TestTenantIsolation`,
including a test that a client-supplied `tenant_id` query parameter is
simply ignored).

**Even heavier reuse than Prompt 28:** `identity.repositories.tenants
.get_tenant`, `identity.repositories.users.list_users`/
`list_roles_with_permissions`/`assign_role`/`remove_role`,
`identity.repositories.semantic_catalog.list_entries`,
`identity.repositories.onboarding.list_jobs_for_tenant`/
`list_review_items`/`list_artifacts`, and `Settings.databases_for_tenant`
all already existed and were already tenant-aware -- every route here is
a thin aggregation over them. `api/platform_admin_schemas.py`'s
`TenantOut`/`PlatformUserOut`/`RoleOut`/`DatabaseStatusOut`/
`SecurityEventOut`/`AssignRoleRequest` are imported and reused directly
rather than redefined, since the shape a tenant admin needs is identical
to what the platform dashboard already returns -- only the query scope
differs, and that's enforced in `api/tenant_admin.py`, not in the schema.

**Two existing routes were confirmed, by reading them, to already be
tenant-scoped -- so this dashboard calls them directly instead of
duplicating them:**
- `GET /metrics/performance` already resolves and scopes to the
  caller's own tenant (unchanged since it was built) -- but had **zero
  frontend consumer anywhere in this codebase** before this prompt. New
  `lib/api.ts::getPerformanceMetrics` + `useTenantPerformanceMetrics` is
  the first one, for the "AI usage" tab.
- `GET /recommendations` and `GET /recommendations/metrics`
  (Prompt 17/18) were already tenant-scoped too, and likewise had **zero
  frontend consumer** -- their own schema module's docstring literally
  says "the literal 'expose APIs for later dashboards' requirement."
  New `lib/tenantAdminApi.ts` functions are that dashboard, for the
  "Recommendations" tab (list + quality metrics only -- the fuller
  feedback/resolve/expire governance workflow is a disclosed,
  deliberately deferred follow-up, matching this codebase's own
  "compute + test, defer deepest UI wiring" precedent).

**One real, newly-relevant tenant-isolation gap found and closed:**
`POST /schema/refresh` (`api/main.py`) refreshes *every* configured
database with no tenant filter at all -- correct for its existing,
unrelated platform-operator use (gated on `agent.authz
.Permission.SCHEMA_REFRESH`, a base-role check with no tenant-scoping
concept at all), but wrong to hand a tenant-admin dashboard as-is: a
tenant admin triggering it would re-introspect and re-embed *other*
tenants' databases too. Rather than change that existing route (master-
contract rule 4: preserve working behavior), a new, genuinely
tenant-scoped `POST /tenant-admin/databases/refresh` loops only
`Settings.databases_for_tenant(caller_tenant_id)`, reusing the exact same
`embeddings.schema_indexer.refresh_schema_index` function per database --
proven directly (`tests/test_api_tenant_admin.py
::TestSchemaRefreshIsTenantScoped::test_refresh_only_touches_the_callers_own_databases`).

**Invariants that must not regress:**
- **The `platform_admin` role can never be seen or assigned from this
  dashboard.** `_RESTRICTED_ROLE_NAMES` excludes it from both
  `GET /tenant-admin/roles`'s own list and
  `POST /tenant-admin/users/{id}/roles`'s own validation (403, not a
  silent no-op) -- a cross-tenant role has no legitimate place in a
  tenant-scoped surface. Proven directly (`tests/test_api_tenant_admin.py
  ::TestRoleSpecificAccess`).
- **A cross-tenant role-assignment target is a 404, not a 403 or a
  success.** `_require_same_tenant_user` 404s both a genuinely
  nonexistent account and one that exists in a *different* tenant --
  the identical anti-enumeration posture `api/semantic_catalog.py`/
  `api/onboarding.py` already established.
- **Viewing and acting are two different tiers, both pre-existing.**
  `ADMIN_DASHBOARD_READ` (admin/auditor/manager) gates every `GET` route;
  `USERS_ASSIGN_ROLES`/`ONBOARDING_MANAGE` (admin only) additionally gate
  the two write actions (role assignment, schema refresh). The frontend
  mirrors this: `canSeeTenantAdmin` (admin/auditor/manager) controls the
  nav tab and page visibility; a separate `canManage` (admin only) inside
  `TenantAdmin.tsx` controls whether the Users/Databases tabs render
  their own action controls at all -- UX-only in both cases, the real
  enforcement is entirely server-side.
- **Managing an onboarding job's own lifecycle is not re-implemented
  here.** The Pending Reviews/Golden Questions/Evaluation tabs are
  read-only summaries that link to the existing `/db-onboarding` and
  `/semantic-review` pages for the actual review workflow -- the same
  "link, don't duplicate" precedent `SemanticReview.tsx` already set.

**Known, disclosed limitations:**
- The Recommendations tab is read-only (list + quality metrics) --
  submitting feedback, resolving, or expiring a recommendation from this
  dashboard is not built; an operator uses the underlying
  `/recommendations/*` routes directly for that today.
- `identity.repositories.users.list_users`/
  `identity.repositories.semantic_catalog.list_entries`/
  `identity.repositories.onboarding.list_jobs_for_tenant` (all reused
  from Prompt 20/26/27/28) remain simple limit-bounded queries with no
  cursor/offset pagination -- the identical, already-disclosed limitation
  Prompt 28 named for its own platform-wide equivalents.
- The free-text role-name input on the Users tab is not constrained to a
  dropdown of real role names (same disclosed rough edge Prompt 28's own
  equivalent control has) -- server-side validation (400 on an unknown
  role, 403 on `platform_admin`) is the real guardrail.

**Read [`29_TENANT_ADMIN_DASHBOARD_CONTRACT.md`](../../29_TENANT_ADMIN_DASHBOARD_CONTRACT.md)**
for the full prompt text and outcome summary.


## Prompts 33-35: AI analysis layers (2026-10)

Three optional layers, all off by default. Each has a contract document with
full design and limitations. Read those before changing them.

- **AI Data Analyst** (`agent/analyst/`, `api/analyst.py`, `33_AI_DATA_ANALYST_AGENT_CONTRACT.md`).
  A LangGraph loop (understand, then execute and analyze in a loop, then recommend, then explain)
  that runs each sub-question through `agent.graph.run_agent`. The analyst never generates or
  executes SQL itself. Every step is charged against a budget from `agent/analyst/budget.py`,
  a pure function, so a stop is deterministic and reported. A planner failure falls back to
  the plain single-question path.
- **Multi-agent supervisor** (`agent/multiagent/`, `34_MULTI_AGENT_SUPERVISOR_CONTRACT.md`).
  A plain-Python supervisor with fixed routing, not an LLM mesh. Each specialist has an
  `AgentSpec` (allowed tools, truth-level ceiling, cost). The `ToolGateway` enforces the
  allowlist and refuses identity fields in tool input. Breakers are scoped per tenant. Claims
  must cite evidence the supervisor issued. Conflicts are withheld or superseded by a fixed
  rule, never by agent order.
- **Semantic intelligence** (`semantic/intelligence/`, `35_SEMANTIC_INTELLIGENCE_CONTRACT.md`).
  Deterministic synonym, ambiguity, conflict and relationship detection over the catalog, with
  versioned findings in the `semantic_findings` table (migration `a1f5c9e3b7d2`) and a
  risk-ordered queue. Every finding is `ai_inference`. Only publishing a catalog entry confirms
  it, and a finding never changes one.

Shared invariant for all three: the LLM and any agent may propose, never decide. Every SQL run
goes through the governed pipeline, every tool call through its permission check, and every
result carries its truth level.


## AI Governance Center (Prompt 44)

`governance_center/` (new top-level package, a peer of `analytics/`/
`optimization/`/`data_quality/`/`lineage/`/`schema_drift/` --
**deliberately not the same package as the pre-existing `governance/`**,
which is a different concern: a runtime request/result policy gate
consumed by `agent/multiagent/agents.py`'s own `governance_handler`,
never touched by this prompt) is a governed Draft -> Test -> Evaluate ->
Approve -> Deploy -> Rollback lifecycle, applied uniformly across every
kind of asset the mission names (models, providers, prompts, agents,
tools, retrieval sources, policies) rather than seven separate,
drifting subsystems. `governance_center/models.py`'s `GovernanceStage`
is the mission's own named chain plus two real terminal outcomes this
codebase's own approval-workflow precedent already needs (`REJECTED`,
`RETIRED`); `VALID_STAGE_TRANSITIONS` deliberately excludes `retired`/
`rolled_back` -> `deployed` -- that move is `rollback`'s own, separately
validated action (see below), never an ordinary forward transition.
`stage_to_truth_level` maps `approved`/`deployed` to
`CONFIRMED_BUSINESS_TRUTH` (a human has reviewed and approved this
specific version for production use) and every other stage to
`AI_INFERENCE` -- a governance-approval signal only, making **no claim
whatsoever** about what a deployed model/prompt/tool/retrieval source
actually produces at runtime (that stays each engine's own, already-
established provenance, completely unaffected by this module).

**Owners, versions, approvals, usage/cost/latency, and policy events --
the mission's own named tracking dimensions** -- live on
`identity.models.GovernanceAssetVersion` (one row per **version**, the
identical append-only-past-draft discipline `SemanticCatalogEntry`
already establishes: `owner`, `version`, every `*_by_user_id`/`*_at`
approval/deployment/retirement timestamp pair) and
`identity.models.GovernanceEvent` (an append-only audit trail --
`stage_change`/`rollback`/`usage_recorded`/`note` events).
**Usage/cost/latency is deliberately caller/operator-reported, never
auto-instrumented** -- no per-model/per-prompt/per-tool token-cost
capture exists anywhere in this codebase today (a disclosed, pre-
existing gap -- see `observability/metrics.py`'s own "no per-call
token/cost metadata is captured" note); `POST .../usage` stores
whatever sample an operator reports, and `usage_summary` is a plain,
deterministic sum/mean aggregation over those samples (`DATABASE_FACT`
-- a real measurement of real recorded numbers, never an inference).

**"Deploy" (the mission's own "release") atomically retires whichever
version of the same asset was deployed before it; "rollback" is a
distinct, explicitly-audited action, not a plain transition-table
entry** -- `identity.repositories.governance.rollback` independently
validates every condition (same asset, target in `retired`/
`rolled_back`, current actually `deployed`) and, in one atomic unit,
flips the current version to `rolled_back` while promoting the named
target back to `deployed` -- the identical "mutate the second row
before the first row's own atomic, conditional commit" pattern `deploy`
itself uses, so a lost race on the primary row rolls the second row's
change back too. Every transition (including `deploy`/`rollback`) goes
through the identical atomic `UPDATE ... WHERE stage = expected_current`
guard `identity/repositories/semantic_catalog.py::_apply_transition`'s
own docstring documents finding and fixing for a real concurrency bug
(Prompt 27) -- applied here from the start, proven with two independent
`Session`s (not real threads, for the identical, already-documented
reason a shared in-memory SQLite connection isn't a safe stand-in for
genuine concurrency).

**RBAC**: new `identity.rbac.Permission.GOVERNANCE_MANAGE` (admin tier
-- create/edit a draft, submit for testing/evaluation, record results,
deploy, roll back, retire, record usage: operational release actions,
not domain judgment) and `GOVERNANCE_REVIEW` (analyst tier -- approve/
reject: the identical domain-judgment tier `ONBOARDING_REVIEW`/
`CATALOG_REVIEW`/`RECOMMENDATION_REVIEW` already sit at). **No
`Settings.enable_*` feature flag** -- this is an administrative/
compliance surface gated purely by RBAC, the identical posture
`api/onboarding.py`/`api/semantic_catalog.py`/`api/platform_admin.py`
already establish (none of them has a feature flag either).

**New, standalone REST surface, `api/governance.py`** (15 routes under
`/governance`, all requiring a local account): create/list/get a
version, get the currently-deployed version for `(asset_type,
asset_key)`, get its audit trail/usage summary, edit a draft, and one
route per lifecycle action (`submit-for-testing`/`test-results`/
`submit-for-evaluation`/`evaluation-results`/`approve`/`reject`/
`deploy`/`rollback`/`retire`/`usage`). Cross-tenant access is denied
with the same 404 a genuinely nonexistent version would get -- the
identical anti-enumeration convention `api/semantic_catalog.py`/
`api/onboarding.py` already establish.

**Frontend**: `frontend/src/pages/AIGovernance.tsx` -- a list + detail
+ capability-gated action-button surface mirroring `SemanticReview.tsx`'s
own shape, wired through `security/navigation.py` (a new `ai_governance`
`NavItem`, `identity_any={GOVERNANCE_MANAGE, GOVERNANCE_REVIEW}`),
`App.tsx`, `AppShell.tsx`'s screen-presentation map, and all 5 locale
files' nav label. Button visibility mirrors the lifecycle for UX only;
the server re-checks every request regardless.

**Two real bugs found while writing this prompt's own required
"unauthorized changes and rollback" tests, both fixed before any test
was left to merely document the wrong behavior:**
- `governance_center.policy.authorize_governance_action` conflated "no
  specific resource to check a tenant against yet" (true for `LIST`/
  `GET .../current`, which are scoped by the caller's own tenant filter,
  not by comparing to one resolved resource) with "the resource wasn't
  found" (true for `CREATE_DRAFT` only) -- every list/current-lookup
  call was wrongly denied with a 403. Fixed by giving `LIST` its own,
  distinct action, authorized by permission alone like `CREATE_DRAFT`,
  never comparing against a tenant that doesn't apply.
- `approve`/`retire`'s own request bodies have every field optional,
  but FastAPI still required *some* JSON body content for the bodied-
  model parameter -- a caller omitting the body entirely (every field
  being optional implies this should be allowed) got a 422. Fixed by
  giving each route's own `payload` parameter a module-level singleton
  default instance.

**Invariant that must not regress:** every stage transition -- including
`deploy`/`rollback` -- is race-safe (proven with two independent
`Session`s, not merely an in-memory pre-check); a cross-tenant action
gets the same 404 a genuinely nonexistent version would; `GOVERNANCE_REVIEW`
alone can never deploy/roll back/retire. Locked in by
`tests/security/test_governance_unauthorized_rollback_and_tenant_isolation.py`.

**Known, disclosed limitations:** usage/cost/latency is entirely
operator-reported (no live instrumentation); this center tracks and
governs the *lifecycle/approval record* of an asset version -- it does
not itself rewire which model/prompt/tool/retrieval-source configuration
the live `/ask`/multi-agent pipeline actually uses at runtime (the
identical "stubbed today, wired later" scope boundary this codebase's
own `recommendation.governance` already disclosed for a structurally
similar reason: "nothing here ever gates whether \[something] is shown
... this lifecycle exists purely to measure and record"); no multi-agent
specialist wiring exists for this prompt (an administrative/compliance
surface, the identical "never wired into `agent/multiagent/`" posture
`api/onboarding.py`/`api/semantic_catalog.py`/`api/platform_admin.py`
already establish).

**Read [`44_AI_GOVERNANCE_CENTER_CONTRACT.md`](../../44_AI_GOVERNANCE_CENTER_CONTRACT.md)**
for the full inspection findings, every new module's design, the testing
summary, and the security/tenant-isolation/performance review.


## Prompt/agent/policy registry: inventory + typed specs + validation (Prompt 45)

Prompt 44 above already built the governed **lifecycle** (draft → testing
→ evaluating → approved → deployed, plus rollback/rejected/retired),
owners, versions, tenant isolation, RBAC and the audit trail -- across
asset types that already include `prompt`/`agent`/`policy`. Prompt 45
deliberately added **no second registry**; it added the three things
that genuinely did not exist, as three modules inside the same package:

- **`governance_center/inventory.py`** -- the live inventory (15 prompt
  templates, 13 agent definitions, 6 analytical policies), each resolved
  from the **live** object at call time (`importlib` + the real
  constant, the real `agent.multiagent.agents.SPECS` dict, real
  `Settings` fields), never transcribed. A named artifact that has moved
  raises `InventoryUnavailableError` rather than being skipped, so the
  inventory can never silently shrink into a falsely-clean picture.
  Every entry is `DataTruthLevel.DATABASE_FACT` -- an observation about
  this repository's own source, never a claim the artifact is approved
  (that stays `GovernanceStage`'s job).
- **`governance_center/artifacts.py`** -- gives Prompt 44's deliberately
  free-form `config` a typed, frozen, `extra="forbid"` reading for
  **exactly three** asset types (`TYPED_ASSET_TYPES`): template,
  variables, required tools, compatible models, evaluation suites,
  declared safety invariants. The other four asset types keep their
  original never-parsed behavior byte for byte. A `PolicyRule` is a
  typed `(subject, operator, value)` triple, so there is no field an
  authored expression could occupy -- the same structural guarantee
  `optimization/models.py` and `analytics/scenario.py` already make.
- **`governance_center/safety.py` + `validation.py`** -- six named
  `SafetyInvariant`s with detectors anchored on real phrasings this
  codebase's own prompts use, eight `WEAKENING_PATTERNS` for text that
  actively subverts a control, and 14 `ViolationCode`s. One `ERROR`
  invalidates the whole artifact (no partial trust).

**Invariants that must not regress:**
- **The required-invariant set is monotonic across versions.** A
  successor inherits whatever its predecessor *declared or carried*
  (`api/governance.py::_enforce_artifact_validity`'s
  `predecessor_config` -- the stored draft being edited, or the
  currently-deployed version of the same asset key), so deleting a
  control together with its declaration is refused. Without this, a
  version declaring nothing had nothing to drop and passed -- a real
  hole this prompt's own regression suite found and closed.
- **Validation is enforced at four gates, including `deploy`.** A
  draft's `config` is mutable, so a check that ran only at authoring
  time could be edited away before release.
  `tests/security/test_prompt_registry_cannot_weaken_security.py
  ::test_the_deploy_gate_revalidates_the_stored_config` weakens a row
  directly in the database to prove deploy re-validates rather than
  trusting creation.
- **The registry never hot-swaps a live prompt.** `agent/llm_client.py`
  still uses its own in-code constants; nothing loads a deployed
  artifact as a system prompt. Making a database row the system prompt
  would turn every write path into the registry into prompt injection
  with full pipeline reach. Promoting a reviewed artifact into the
  runtime stays a code change, deliberately.
- **`GET /governance/inventory` never returns live prompt template
  text** -- only names, source locations, fingerprints and declared
  invariants. Shipping the prompt set over HTTP would make every
  prompt's own `NO_PROMPT_DISCLOSURE` rule pointless; asserted against a
  60-character probe of every real template.
- **`safety.py` is not the security boundary** and says so in its own
  docstring, mirroring `security/injection_patterns.py`'s identical
  framing. `agent/sql_validator.py`'s AST allowlist and the RBAC/
  restricted-column gates remain what bound consequences.
- Both new routes (`GET /governance/inventory`,
  `POST /governance/assets/validate`) use `GovernanceAction.LIST` -- the
  action for a route with no specific target. `VIEW` is wrong there and
  correctly denies, since it always tenant-matches a resolved version.

**A real validator bug found and fixed by its own test suite:** running
`security.injection_patterns.INJECTION_PATTERNS` as a *blocking* check
over prompt templates rejected this codebase's own most
security-conscious prompt -- `agent.llm_client._system_prompt` quotes the
attacks it defends against and prohibits a "connection string" in
generated SQL. All three matches were defensive mentions. That pattern
set is tuned for untrusted *user input*; an authored prompt discussing
security is the opposite context. Fixed by stripping quoted spans and
downgrading the finding to a **warning**, leaving `UNSAFE_COMPOSITION`
(imperative-phrased, zero false positives across all 15 live prompts) to
carry the blocking weight. See `validation._scan_for_injection_mentions`.

**Known, disclosed limitations:** a brand-new asset key with no
predecessor and no declared invariants gets only self-consistency
validation (deliberate -- the live inventory itself contains two prompts
carrying no safety framing, so requiring one would reject a real artifact
shape; active subversion is still refused). **Inventory finding, reported
rather than fixed:** `rag.retriever._GRADE_SYSTEM_PROMPT` receives raw
document excerpt text and states no untrusted-data framing, and
`_REWRITE_SYSTEM_PROMPT` states none either -- bounded blast radius (a
YES/NO gating retry, and a search string; neither reaches generated SQL
or the user's answer), left unchanged because this pass does not edit
live prompt wording, and recorded as `set()` in the regression suite's
own expectation table so the gap stays visible. `AgentSpec.kind`'s own
docstring is stale (it lists four kinds; `semantic` is a live
`"retrieval"` agent) -- the real set is taken from the live specs and
pinned by a test. No frontend surface; evaluation suites are validated
for existence, not for having actually been run.

**Read [`45_PROMPT_AGENT_REGISTRY_CONTRACT.md`](../../45_PROMPT_AGENT_REGISTRY_CONTRACT.md)**
for the full inspection findings, both real bugs, the 101-test testing
summary, and the security/tenant-isolation/performance review.


## AI Evaluation Benchmarking platform (Prompt 46)

`eval_platform/` (new top-level package, a peer of `governance_center/`)
is a persisted, versioned, release-gated wrapper **combining** the four
evaluation harnesses this codebase already had -- never a second
evaluation engine. `eval/component_benchmark/` gained five new domains
this prompt's own mission named and nothing covered before:
`cases_groundedness.py`/`cases_calculations.py`/
`cases_anomaly_root_cause.py`/`cases_forecasting.py`/`cases_intent.py`
(62 cases across 9 domains total, up from 28 across 4), each wrapping a
real, already-tested production function directly with zero I/O, the
identical shape `cases_semantic.py`/`cases_analytics.py` already
establish. `eval_platform.models.EvalDomain` is the full 12-domain union
(the 9 real component-benchmark domains plus `sql`/`security`/
`tenant_isolation`) -- the mission's other three named domains
deliberately get **no** parallel zero-I/O dataset here, since each
already has its own real, much larger harness that needs live
infrastructure (`eval/benchmark/`+`eval.runner`, `eval/security_benchmark/`)
or is already a CI-enforced pytest suite
(`tests/security/test_cross_tenant_isolation.py`) -- adding a stub would
misrepresent coverage, not add it.

`eval_platform.release_gate.evaluate_release_gate` is a pure function
that **reuses, never recomputes**: it takes the real
`eval.component_benchmark.regression.ComponentBenchmarkComparison`/
`eval.regression.RegressionReport` dataclasses directly, plus plain
operator-reported counts/booleans for the security benchmark and
tenant-isolation suite (both genuinely need live DB/LLM/pytest
infrastructure this platform does not itself run -- see
`eval_platform.models.EvalRunSource`'s own docstring). Two floors are
non-negotiable regardless of any baseline comparison -- any critical
security finding, or a failed tenant-isolation suite, blocks a release
outright, matching those two domains' own real-world shape elsewhere in
this codebase; every other domain blocks only on a genuine regression. A
domain nothing reported produces a warning, never a block.

New `identity.models.EvaluationRun` (one row per recorded run, always
created already-complete -- no background worker exists in this
codebase) + `EvaluationCaseReview` (an independent, append-only human-
review table satisfying the mission's own "support ... human review"
requirement -- never mutates its parent run's own `summary`/
`total_passed`). `EvaluationRun.dataset_version` is a `sha256` fingerprint
(`eval_platform.dataset_registry.fingerprint_baseline`) of the actual
baseline/dataset a run was graded against -- the literal "version
datasets" requirement, without a second dataset-storage mechanism (the
dataset itself already lives wherever it always has).
`governed_asset_type`/`governed_asset_key`/`governed_asset_version` are a
deliberately **loose** link (plain strings, never a foreign key) to a
`governance_asset_versions` row -- satisfying "track quality, latency and
cost" for "model/prompt/agent versions" without this table depending on
`governance_center`'s own schema at all.

New RBAC (`Permission.EVAL_MANAGE`/`EVAL_REVIEW`, the identical two-tier
operational-vs-domain-judgment split every prior `*_MANAGE`/`*_REVIEW`
pair already establishes) and `api/eval_platform.py` (7 routes under
`/eval-platform`, mirroring `api/governance.py`/`api/semantic_catalog.py`'s
own authorization/anti-enumeration discipline exactly). No
`Settings.enable_*` feature flag -- the identical reasoning
`api/governance.py`'s own docstring already gives for an administrative/
compliance surface gated purely by RBAC.

**Invariants that must not regress:** `POST /eval-platform/runs/report`
rejects `source=component_benchmark` with a 422 -- an operator can never
hand-report a result for the one domain this platform can actually
verify itself, which would let a caller fabricate a clean run. Submitting
a case review never changes its parent run's own persisted `summary`/
`total_passed` -- the identical "feedback must not automatically alter
production numbers from a single event" posture
`recommendation.governance`'s own repository docstring already
establishes. Cross-tenant access to a run, its release gate, or its case
reviews are all 404s, not 403s.

**Known, disclosed limitations:** no background worker exists, so a run
is always created already-complete -- there is no "in-progress" run
state. Usage/cost/latency is entirely caller/operator-reported (no live
per-call token/cost instrumentation exists anywhere in this codebase).
The release gate's REST layer takes primitive, flattened inputs for the
SQL/security/tenant-isolation domains rather than reconstructing the
richer dataclasses the pure engine function itself accepts -- a
disclosed, bounded simplification at the API boundary, not a loss of
genuine reuse at the engine layer (the component-benchmark path *does*
reconstruct and reuse the real `ComponentBenchmarkComparison` dataclass
from a run's own already-stored outcome). No dashboard/frontend surface
exists for this platform in this pass.

**Read [`46_AI_EVALUATION_BENCHMARKING_CONTRACT.md`](../../46_AI_EVALUATION_BENCHMARKING_CONTRACT.md)**
for the full inspection findings, every new module's design, the
150-test testing summary, and the security/tenant-isolation/performance
review.


## Enterprise BI integrations (Prompt 48)

`bi_integration/` (new top-level package, a peer of `eval_platform/`/
`governance_center/`) is governed connectors to Power BI/Looker (push)
and Tableau/Excel (authenticated file/feed pull) -- never a second
query/export engine. A connector's own refresh reuses this app's
governed SQL execution path **verbatim**: `agent.sql_validator
.validate_sql` -> `enforce_row_limit` -> `qualify_table_schema` ->
`db.execution.execute_readonly_sql` -> `governance.result_policy
.govern_rows_for_caller` (masking a restricted column for a caller
lacking `Permission.VIEW_RESTRICTED_COLUMNS`), the exact sequence
`POST /execute` already uses, applied **before** a push-style
connector's own rows ever leave this process and before a pull-style
connector's own in-memory export cache is populated.

**"Reuse governed metrics... do not create duplicate metric
definitions" is structural, not conventional.**
`identity.models.BiConnector.governed_metric_concept_key`/
`governed_metric_version` are a deliberately **loose reference** (plain
strings, never a copy of a metric's own `approved_expression`) --
`bi_integration.metric_binding.validate_governed_metric_binding`
rejects linking a connector to anything but a currently-**published**
semantic-catalog `METRIC` entry, plus a cheap, disclosed sanity check
(reusing `agent.sql_validator.find_unexpected_table_references` as a
generic table extractor, never a second sqlglot implementation): at
least one of the metric's own declared `source_tables` must appear in
the connector's own SQL. This is a bounded consistency check, not a
SQL-to-formula equivalence proof -- the identical disclosed scope
`agent.nodes.review_metric_conformance_node`'s own LLM-judged check
already carries, applied here deterministically instead since this
feature has no LLM-call budget for the richer judgment.

**Two push targets, two pull targets, each honestly scoped to what's
actually implementable without a live tenant:** Power BI's own real,
documented push-dataset REST API
(`POST .../datasets/{id}/tables/{table}/rows`) and a generic Looker
Action-Hub-shaped webhook push are the two **push** connectors --
`bi_integration/push.py`, disclosed, not live-verified (the identical
posture `search/web_search.py`/`media_gen/` already carry for their own
external providers, covered by mocked-`httpx` tests only). Tableau and
Excel have no equivalent "push" API this app could target without a
live tenant, so both are **pull**-style instead: `GET /bi/connectors
/{id}/export?format=csv|xlsx|json` is a plain, authenticated file/feed
a tool's own generic "Web Data Connector"/"Get Data from Web" can read
-- not a native plugin, disclosed as such.

**No connection secret is ever stored** -- Power BI's own push
credential (`Settings.power_bi_push_base_url`/`power_bi_push_api_key`,
a `SecretStr`) is operator-configured once, process-wide, in `.env`
(the identical `web_search_api_key` shape, since Power BI's own push
API is backed by one organization-wide Azure AD app registration, not
a per-tenant-dynamic credential) -- never per-connector. A Looker
connector's own `push_config["webhook_url"]` is a destination URL, not
a credential, and is independently re-validated against SSRF
(`bi_integration.outbound_safety.assert_safe_outbound_url`) on **every
single refresh**, never only once at connector-creation time -- the
identical "an edit is exactly as untrusted as an LLM generation"
re-check-every-time posture `agent/sql_validator.py` already
establishes for SQL text, applied here to a caller-supplied URL.
Deliberately a **second, independent copy** of `media_gen/download.py`'s
own resolve-and-blocklist SSRF logic, not an extraction into a shared
helper -- that module's own test suite patches `media_gen.download
.log_security_event` directly, and moving the call into a shared
module would silently break that patch target for a marginal
de-duplication benefit on ~15 lines of logic a security-critical,
already-audited test suite depends on.

**An operational refresh failure is a 200 with `status: "error"` in
the body, never an HTTP error code** -- the identical
"refresh/status/error handling" shape `POST /execute`'s own
`ExecuteResponse(status="failed", ...)` already establishes. An HTTP
error code is reserved for a request-level problem (authorization,
validation, rate limit, not-found); a SQL error, a push HTTP error, or
an SSRF-blocked webhook are all recorded on the connector's own row
(`last_error`/`last_refresh_status`) instead.

New RBAC (`Permission.BI_INTEGRATION_MANAGE`, a single admin-tier
permission -- unlike `ONBOARDING_*`/`CATALOG_*`/`GOVERNANCE_*`, this
feature has no SME-approval/domain-judgment step the mission asks for)
and `api/bi_integration.py` (6 routes under `/bi`, behind
`Settings.enable_bi_integration`, default **off** -- unlike the
pure-compute analytics engines (`enable_optimization_engine` etc.,
default on), this feature makes real outbound HTTP calls, the same
"an optional feature that reaches outside this process defaults off"
posture `enable_web_search`/`enable_media_generation` already
establish).

**Invariants that must not regress:** a connector's own SQL is
re-validated fresh on every refresh, never trusted because it was
valid at creation time. `govern_rows_for_caller` always runs (when
`Settings.enable_result_governance` is on) before a push-style
connector's rows leave this process. Cross-tenant access to a
connector, its refresh, or its export is a 404, not a 403
(anti-enumeration, matching `api/eval_platform.py`/`api/governance.py`'s
own precedent). Deleting a connector also discards its own cached
export (`bi_integration.export_cache.BiExportCache.discard`), so a
stale export can never be served under a reused id.

**Known, disclosed limitations:** no soft-disable route is exposed yet
(`identity.repositories.bi_integration.set_disabled` is built and
tested but not wired into any route in this pass -- a connector is
only ever created or deleted, matching this series' own "compute +
test, defer the deepest wiring" precedent). The export cache is
process-lifetime, in-memory, not persisted -- a restart loses every
pull-style connector's last-refreshed rows until the next refresh. The
governed-metric table-overlap check is a disclosed heuristic, not a
correctness proof. Looker's own webhook contract and Tableau/Excel's
own "generic file/feed" shape have not been verified against a real
Looker/Tableau/Excel instance -- only Power BI's own documented REST
API shape and this feature's own mocked-`httpx` tests exist as
evidence.

**Read [`48_ENTERPRISE_BI_INTEGRATIONS_CONTRACT.md`](../../48_ENTERPRISE_BI_INTEGRATIONS_CONTRACT.md)**
for the full inspection findings, every new module's design, the
testing summary, and the security/tenant-isolation/performance review.


## Governed model routing (Prompt 47)

`model_routing/` (new top-level package, a peer of `analytics/`/
`optimization/`/`data_quality/`/`lineage/`/`schema_drift/`/
`governance_center/`) picks **which locally-installed Ollama model drafts
each LLM call the SQL pipeline makes**, instead of using one model for
every call in a run. Almost entirely wiring: every ingredient already
existed and is reused by import, not by copy --
`agent.complexity.detect_complexity_signals` (read from
`state["complexity_signals"]`, never re-scanned, so the retry budget and
routing can never disagree about a question's difficulty),
`agent.intent.intent_implies_planning`, `config/ollama_models.yaml`'s own
`resource_level`/`capabilities` tags, `agent.multiagent.policy
.CircuitBreaker`/`get_breaker` and `TurnBudget`, `agent.rate_limit
.SlidingWindowRateLimiter`/`BoundedLimiterCache`, and Prompt 44's
`governance_center` as the place a model's evaluation lives.

Routed at exactly the seven `*_from_llm` call sites in `agent/nodes.py`,
through one helper (`_routed_llm_call`). No prompt, no node contract, no
retry rule and no conditional edge changed.

**Off by default** (`Settings.enable_model_routing`), unlike the pure-
computation engines (`enable_data_quality_engine`/`enable_lineage_platform`/
`enable_schema_drift_detection`): this changes which model answers a real
question. With it off, `state["model_router"]` is `None` and every call
uses `state["selected_model"]` exactly as before.

**Invariants that must not regress:**
- **Routing is never a security decision.** No module in `model_routing/`
  imports `agent.sql_validator`, `agent.authz`, `agent.input_guard`,
  `security.injection_patterns`, `security.tenancy`, `db.execution`,
  `db.connection` or `identity.rbac`, and none imports or calls an LLM
  client -- asserted by parsing the real source in
  `tests/security/test_model_routing_never_weakens_deterministic_controls.py`,
  which also proves the AST allowlist still refuses a `DELETE`/`DROP`/
  system-catalog read with routing *on*, through the real compiled graph.
  `RiskLevel` in `model_routing/models.py` is **not** an authorization
  concept; it answers only "may routing trade accuracy for compute on
  this task".
- **`Settings.ollama_allowed_models` stays the only gate.** Routing
  chooses within it, never beyond it (swept across every task, question
  shape, latency setting and breaker state).
- **A caller's explicit model always wins.** `api/main.py` resolves an
  omitted `AskRequest.model` to `Settings.ollama_model` *before* the graph
  runs, so a new `model_explicitly_selected` flag -- threaded `/ask` ->
  `run_orchestrated` -> `run_agent` -- is what distinguishes "the caller
  asked for this model" from "the caller asked for nothing". When it is
  set, the router is not even constructed.
- **Risk decides the direction a task may move; complexity decides
  whether it moves.** `sql_generation`, `sql_review` and
  `metric_conformance_review` (Prompt 45 treats governed-metric precedence
  as a safety invariant) are HIGH and `query_plan`/`analytical_plan` are
  MEDIUM: upgradeable, never downgraded. `analytical_intent`/`sql_insight`
  are LOW: downgradeable, **never upgraded** -- classifying a complex
  question's intent is still a 13-way label and narrating an
  already-computed result is still narration, so escalating either would
  be the opposite of cost optimization. (The first version of the policy
  thresholded on complexity alone and did exactly that; found by the
  integration test meant to demonstrate per-task routing.)
- **Capability metadata never gates the operator's own default.**
  `config/ollama_models.yaml`'s docstring promises it is "never a gate",
  so `TASK_REQUIRED_CAPABILITIES` filters only models routing would swap
  *to*. An un-catalogued model is honestly `ModelTier.UNKNOWN` and is
  never chosen as either an upgrade or a downgrade target.
- **An availability failure and a quality failure are different things.**
  `OllamaUnavailableError` trips the per-model breaker and walks the
  decision's `fallback_chain` (configured default first); any other
  `AgentError` is re-raised unchanged to the calling node with no second
  model tried, and **never** reaches the breaker -- otherwise one
  adversarial question could disable a healthy model for every caller.
  That is a regression test, not a comment.
- **A spent budget never fails a question.** It removes the *option* to
  escalate; the request proceeds on `Settings.ollama_model`. The
  per-tenant hourly window is consulted only after an optimistic policy
  pass has decided an escalation is actually wanted, so an operator's
  allowance is never burned on a task routing was not going to escalate.
- **Cost units are ordinal, never money.** Every routable model runs
  locally through Ollama, so there is no per-token price to report;
  inventing a dollar figure would present an estimate as observed fact.
  A test asserts no money-shaped field appears in the API response.
- **`RouteDecision.truth_level` is a fixed
  `Literal[DataTruthLevel.AI_INFERENCE]`** -- pydantic itself refuses a
  decision claiming to be an observed fact. `RouteOutcome.latency_ms` is
  a real measurement, recorded separately so decision and result are
  never conflated. An evaluation score is read verbatim from an
  operator's own governed record, never computed here.
- **Evidence is tenant-scoped and fail-open.** `list_versions` is already
  tenant-filtered; the TTL cache is keyed per tenant; a disabled or
  unreachable identity database yields an empty mapping, which (with the
  requirement on) correctly means "no escalation", never an exception on
  the request path.
- **`GET /model-routing/*` has no write route, deliberately** -- routing
  is configured in `.env` and its escalation targets are governed through
  `/governance`. `policy`/`preview` need `Permission.ASK`; `metrics` needs
  `ADMIN_CONFIG` like `GET /metrics/performance`. `preview` runs the real
  `decide_route` (never a parallel implementation) and consumes no budget
  and no breaker probe.

**Known, disclosed limitations:** no per-model quality numbers exist
anywhere in this repository (`docs/EVALUATION_CURRENT.md` is one model,
dated 2026-09-16), so with `model_routing_require_evaluation_evidence` on
(the default) routing correctly does nothing but record
`no_evaluation_evidence` until a model is evaluated and approved through
`/governance` -- the honest outcome, not a bug; only the seven SQL-pipeline
LLM calls are routed (RAG/attachment/vision/media/analyst calls thread no
per-request model); installed-status is still never checked on the request
path, so a configured-but-unpulled model fails with
`OllamaUnavailableError` -- which routing now answers with a fallback
chain, strictly better than the previous hard failure; breakers, budget
windows and the per-route rollup are process-local (one per worker, the
same limitation `agent/rate_limit.py`/`observability/metrics.py` already
disclose); `RequestProfile.latency_sensitive` has no live producer yet (the
policy brake and its tests are real, and `POST /model-routing/preview`
exposes it, but nothing computes "short on wall clock" from
`Settings.request_timeout_seconds`); no frontend surface.

**One pre-existing gap found and fixed in passing:**
`agent.orchestrator.nodes.sql_subgraph_node` never passed `tenant_id` to
`run_agent`, so on the multi-source path every `security.audit_log` event
the SQL subgraph emitted was un-stamped. Fixed, because this feature's
tenant scoping depends on it.

**Read [`47_MODEL_ROUTING_COST_OPTIMIZATION_CONTRACT.md`](../../47_MODEL_ROUTING_COST_OPTIMIZATION_CONTRACT.md)**
for the full inspection findings, every module's design, the 101-test
testing summary, and the security/tenant-isolation/performance review.


## Enterprise Workflow Automation (Prompt 49)

`workflow/` (new top-level package, a peer of `recommendation/`/
`governance_center/`/`bi_integration/`) turns an already-`accepted`/
`partially_useful` `identity.models.RecommendationRecord` (Prompt 18)
into a governed, auditable unit of real-world work -- `workflow.models
.WorkflowStatus`'s exact mission-named chain: proposed ->
approval_required -> approved -> assigned -> in_progress ->
completed/rejected/expired, with `EXPIRED` reachable from any
non-terminal state (the identical pattern `recommendation.governance
.RecommendationStatus.EXPIRED` already establishes) and `REJECTED`
reachable only from `APPROVAL_REQUIRED` -- the one real decision point.
Heavy, deliberate reuse rather than a second governance stack: `workflow
/models.py`/`workflow/policy.py` mirror `recommendation/governance.py`/
`recommendation/governance_policy.py`'s own ORM-independent, pure-policy
split exactly, and `identity/repositories/workflow.py` mirrors
`identity/repositories/recommendation_governance.py`'s own atomic-
conditional-`UPDATE` race/retry-safety discipline line for line, applied
against this table's own columns.

**"Never permit uncontrolled autonomous execution" is structural, not a
runtime check.** No module in `workflow/`, `identity/repositories
/workflow.py`, or `api/workflow.py` imports `agent.graph`/`agent.nodes`
(the LLM/LangGraph pipeline), `agent.multiagent` (the supervisor),
`db.execution`/`db.connection` (SQL execution), `bi_integration.push`/
`bi_integration.outbound_safety` (the one real outbound-push precedent
in this codebase), or any HTTP client -- locked in by `tests/security
/test_workflow_never_autonomously_executes.py`, which parses this
package's own source rather than relying on mocking coverage that could
miss a future added call site. `COMPLETED` records a human's own report
(`outcome_summary`/`outcome_measured_impact`) that the work was done --
it never triggers, verifies, or re-runs anything. The workflow's own
`description` (copied from the source recommendation's `action` field)
is display text a human reads and acts on themselves, never a string
this package parses or executes -- the identical "no field a caller or
an LLM could turn into an executed expression" structural guarantee
`optimization.models`/`analytics.scenario.models` already establish for
a formula, applied here to free-text instructions instead.

**"Require approval for consequential actions" is a real separation
between two permission codes, not one combined grant.** New
`identity.rbac.Permission.WORKFLOW_MANAGE` (admin tier -- create, submit
for approval, assign/reassign an owner and due date, start, complete,
set an external task reference, comment, or force-expire: operational
actions) and `WORKFLOW_REVIEW` (analyst tier -- approve or reject a
workflow once it is `approval_required`: the one domain-judgment
decision point), the identical two-tier split every other `*_MANAGE`/
`*_REVIEW` pair in this codebase already establishes. Honestly
disclosed, not overclaimed: `identity/rbac.py`'s `_ADMIN` set is
`_ANALYST | {...}`, so the seeded `admin` role inherits `WORKFLOW_REVIEW`
from the analyst tier in addition to its own `WORKFLOW_MANAGE` grant --
an admin account can propose, submit, *and* approve the same workflow
today, identically to how an admin can already both manage and review a
recommendation, a catalog entry, or a governance asset version. The
separation this policy enforces is between the two permissions
themselves (load-bearing the moment a future custom role holds only one
of them), not a claim that no seeded role holds both.

**"Support owners, due dates, comments, external task references and
outcome measurement" is five typed capabilities on `identity.models
.Workflow`, each with its own `WorkflowEvent.event_type`
(`owner_assigned`/`due_date_set`/`comment`/`external_reference_set`,
plus `status_change`) so none of them ever touches `status` by
accident.** `external_system`/`external_reference_id`/
`external_reference_url` are plain, caller-supplied metadata -- a label
and a link a human can click to open a ticket in whatever system they
already use; nothing in this codebase calls an external ticketing API
(no such connector exists, and `bi_integration/push.py`'s own real
outbound-push precedent targets a BI dataset/webhook, not a
task-tracking system, so there was nothing safe to reuse here).
`assign` is both the `approved -> assigned` transition on first
assignment *and* a plain reassignment (no status change) from
`assigned`/`in_progress` -- the owner must be an active, same-tenant
user who holds a workflow permission, the same independently-re-checked
pattern `recommendation.governance_policy.assign_owner` already
establishes.

**"Test authorization, retries, duplicate events and audit" --
all four literally satisfied, not just asserted:**
- **Authorization**: `tests/test_workflow_policy.py` (pure, every
  action x permission-combination) plus `tests/test_api_workflow.py`'s
  `TestCrossTenantAccess`/`TestLifecycleAuthorizationAndHappyPath` (a
  cross-tenant workflow is a 404, never a 403 -- the identical
  anti-enumeration convention `api/recommendation_governance.py`/
  `api/governance.py` already establish; an analyst can approve but
  cannot assign/start/complete).
- **Retries**: a client retrying an already-applied transition gets a
  clean 409 and writes **no** second `WorkflowEvent` -- proven both at
  the repository layer (`TestRetrySafety`, including the real two-
  `Session` conditional-`UPDATE` race proof mirroring Prompt 27/31's own
  precedent) and at the HTTP layer.
- **Duplicate events**: at most one *active* (non-terminal) workflow may
  exist per recommendation at a time -- `create_workflow` refuses a
  second one with `DuplicateActiveWorkflowError`/409
  (`TestDuplicateActiveWorkflow`), while a *fresh* workflow is correctly
  allowed once the first one reaches a terminal state.
- **Audit**: every mutation appends an immutable `WorkflowEvent`
  (never only a column overwrite) and is logged via
  `security.audit_log.log_security_event` (`event=workflow_action`,
  ids/action/status-transition only -- never the title/description/
  comment/outcome text, proven directly in `TestAudit`).

New, standalone REST surface, `api/workflow.py` (12 routes under
`/workflows`, no `Settings.enable_*` feature flag -- the identical
"administrative surface gated purely by RBAC" posture
`api/governance.py`/`api/semantic_catalog.py`/`api/recommendation
_governance.py` already establish). `POST /workflows` is the one route
that resolves a *different* resource first (the source
`RecommendationRecord`) since a workflow doesn't exist yet to authorize
against -- a cross-tenant or nonexistent recommendation id gets the same
404 either way.

**Invariant that must not regress:** `identity.models.Workflow.title`/
`.description` are set once at creation (from the source recommendation,
optionally overridden) and never edited afterward -- the identical
"content is immutable once created; only status changes" discipline
`RecommendationRecord`'s own docstring establishes word for word, so
what a reviewer approves can never silently drift after the fact.

**Known, disclosed limitations:** no live ticketing-system connector
exists or is invoked anywhere in this feature -- `external_reference_*`
is plain metadata only, never verified against a real Jira/ServiceNow/
Trello instance. Owner reassignment from `assigned`/`in_progress` is
last-writer-wins (no conditional-status guard applies, since status
itself doesn't change) -- the identical disclosed limitation Prompt 31's
own recommendation-owner-assignment already carries. The per-action rate
limit is per-IP, not per-identity -- the identical disclosed limitation
Prompt 31 already names for its own recommendation-action routes. There
is no scheduled/automated expiry sweep (every `EXPIRED` transition in
this codebase today is a human-initiated administrative override); no
frontend surface exists for this platform in this pass.

**Read [`49_ENTERPRISE_WORKFLOW_AUTOMATION_CONTRACT.md`](../../49_ENTERPRISE_WORKFLOW_AUTOMATION_CONTRACT.md)**
for the full inspection findings, every new module's design, the testing
summary, and the security/tenant-isolation/performance review.


## Enterprise Data Privacy, Domain Safety & Governance Engine

`governance/` (which already existed as three modules: the six-state
decision vocabulary, 11 request rules across 6 domains, and result
masking) is now the centralized engine every request passes through --
what may be asked, what may be returned, what may be recommended. A
domain rule is a data change in one registry rather than a new branch in
a node or a sentence in a prompt.

**The critical gap this closed, found by inspection:**
`config/sensitive_columns.yaml` ships as an empty template (`tables: []`,
deliberately, so a human classifies each column). But both enforcement
layers built on it -- `agent.sql_validator
.find_restricted_column_references` and `governance.result_policy` --
read *only* that file, so on a real database **they protected nothing at
all**. A governance engine that only works after someone hand-lists
several hundred columns is a governance engine that does not work. The
fix is two-layered and order matters: an explicit human classification
always wins (including when it *loosens* -- an operator who reviewed a
column and called it public meant it), and `governance.classification`'s
name-pattern inference fills the gap otherwise, marked `inferred=True`
with the pattern that matched.

**Module map (6 new, 4 extended):** `classification.py`
(`DataClassification`'s 6 ordered levels x `DataCategory`'s 16 families ->
`FieldPolicy`), `domains.py` (18 domains + fallback, `DomainPolicy`,
14 `ProhibitedAction`s, `GOVERNANCE_POLICY_VERSION`),
`domain_detection.py`, `suppression.py` (k-anonymity),
`recommendation_safety.py`, `telemetry.py`; plus `decisions.py`,
`request_policy.py`, `result_policy.py` and `__init__.py` extended.

**Reused, never duplicated:** `agent/sql_validator.py` is **untouched**
-- the engine adds no SQL rewriting and no second SQL security
mechanism. So are `agent/authz.py`, `identity/rbac.py`, `security/*`,
`config/sensitive_columns.*` and `onboarding/pii_detection.py`. No new
permission, no new role, no new table, no migration. The six
pre-existing integration points (`agent/nodes.py` x2,
`agent/orchestrator/nodes.py`, `agent/multiagent/agents.py` x2,
`api/main.py`, `api/bi_integration.py`) stayed the integration points.

**Invariants that must not regress:**
- **No domain policy protects personal data less than the baseline.**
  `BASELINE_RESTRICTED_CATEGORIES` (PII, SPI, PHI, compensation,
  performance, credential, payment, banking) is unioned into every
  domain via `DomainPolicy.effective_restricted_categories` -- **always
  read that, never `restricted_categories` directly**. Without it,
  detecting a domain could *loosen* protection: a question mentioning
  "machine downtime" scores for manufacturing, whose own policy restricts
  only proprietary data. This floor is what makes it safe for question
  wording alone to claim a domain, and it is why the detection score
  threshold could be lowered to one text hit.
- **The `GENERAL` fallback prohibits all 14 actions, not none.** If the
  domain is unknown, no decision about an individual is safe to make or
  recommend. An unrecognized question must not become the loosest path
  through the engine.
- **A credential or a payment instrument is withheld from every caller,
  including an admin.** No permission unlocks `NEVER_DISCLOSED_CATEGORIES`
  -- there is no role for which returning a password is the right answer.
  Enforced by *classification*, so it holds for any column name that
  classifies that way, not only names someone remembered to blocklist.
- **Small-group suppression applies to an authorized caller too.** It
  protects the individual in the group, not the sensitivity of the
  column, so permission does not unlock it. The group label and count
  stay visible and **no row is ever dropped** -- a vanished row would let
  a reader difference a published total against the visible rows and
  recover the hidden value exactly.
- **Protection happens before the model sees the values.** The result
  gate runs in `execute_sql_node` before `state["result_rows"]` is set,
  so the insight step, the recommendation engine, the chart builder and
  the response all receive protected rows. The LLM never sees a
  restricted value merely because the UI would later hide it.
- **The result gate classifies the *returned column names*, not the SQL
  text.** That is why an alias (`salary AS monthly_pay`), a `UNION` or a
  `SELECT *` reaches the same protection -- generating different SQL does
  not get a different answer about what is allowed.
- **Authorization comes only from the caller's roles.** Text claiming
  authority ("this is authorized", "pretend I am admin") and a database
  cell claiming it are both data.
  `tests/security/test_governance_cannot_be_bypassed.py` asserts this
  structurally by parsing the real source: no module in `governance/`
  branches on a `Permission` without `has_role_permission`, none imports
  an LLM client (a refusal that depended on a model choosing to refuse
  would not be a control), and none imports a database handle.
- **Refusal messages are fixed module constants**, never built from the
  question, so a refusal cannot be used as an oracle for what a rule
  matched on.
- **One `ProhibitedAction` list drives both the request gate and the
  recommendation filter**, so "what we refuse to be asked" and "what we
  refuse to propose" cannot drift apart. An unsafe recommendation is
  **withheld and reported, never rewritten** -- editing it would leave
  the evidence attached to a claim it no longer supports.
- **Telemetry counts, never content.** `governance.telemetry` is keyed by
  decision/domain/category only, and `normalize_action` strips the column
  list off an action string before counting it.

**Three real bugs the tests found, each fixed:**
1. **`\b` does not match across an underscore or a case boundary**, so
   `crm_opportunity` and `EmployeePayroll` matched no domain at all. Fixed
   by `domain_detection.normalize_schema_text` (normalize the input, keep
   the patterns readable) rather than by stripping `\b` from every
   pattern.
2. **`monthly_pay` was unclassified** -- the compensation pattern covered
   `base_pay`/`gross_pay` but not the most natural alias a generated
   query would produce for a salary column. Found by a security test
   about aliasing.
3. **The bulk-extraction rule refused "How complete is our customer
   data?"** -- "complete" read as a quantifier and the word gap reached
   across the verb into "customer". It broke a pre-existing Prompt 41
   data-quality test, which is how it was found. Fixed by removing
   `complete` from the quantifier list (it stays in the explicit
   dump/export branch) and excluding clause-boundary words from the gap.
   Over-blocking ordinary analytics is its own failure mode, and
   `tests/test_governance_engine.py` now pins seven legitimate aggregate
   questions containing "all"/"complete".

**Known behaviour change for operators:** a result that previously
returned a personal or compensation value to a caller without
`view_restricted_columns` now returns `[restricted]`, and a credential or
payment column returns `[withheld]` for everyone. That is the intended
correction of the gap above, but it is a visible change on an existing
dashboard reading such columns. Each layer has its own independent
rollback switch (`ENABLE_REQUEST_GOVERNANCE`, `ENABLE_RESULT_GOVERNANCE`,
`ENABLE_RECOMMENDATION_SAFETY`).

**Open, disclosed residual risks** (full list in the audit report's §13):
name-based inference is not content inspection, so a sensitive value in a
neutrally-named column is not detected; there is no cross-session query
auditor, so suppression closes the single-result-set and
total-minus-visible cases but not many-differently-filtered-queries;
conversation-memory classification before persistence is **not**
implemented (partly mitigated because what is persisted is the
already-protected answer); `DomainPolicy.export_row_limit` and
`aggregation_required` are declared but not yet enforced by any
component.

**Read [`docs/DATA_PRIVACY_GOVERNANCE_ENGINE.md`](../DATA_PRIVACY_GOVERNANCE_ENGINE.md)**
for the full audit report -- the existing-architecture inventory, the
gap/risk tables, the 18-domain policy matrix, the classification matrix,
the authorization/SQL/result flows, the 14 attack scenarios tested, the
production-readiness assessment, and the rollback strategy. It states
plainly that technical controls supporting privacy and governance
requirements have been implemented, and that formal regulatory compliance
requires organizational, legal, contractual, administrative and technical
assessment -- no compliance with any named regime is claimed.
