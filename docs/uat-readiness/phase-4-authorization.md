# Phase 4 — Authorization audit

**Date:** 2026-10-28 · **Branch:** `uat-readiness/phase-3-4-authn-authz` ·
**Auditor:** automated session, per `UAT_READINESS_AUDIT_PROMPT.md`

> **Scope honesty.** Phase 4 only; Phase 3 is in
> `phase-3-authentication.md`. Every number below was measured in this
> environment on the date shown. Where a check could not run here it says
> **UNVERIFIED** and names what a human must run — never "passed".

**Headline:** authorization is in good shape on the surface this audit
could exercise — **198 of 198 non-public operations refuse an anonymous
caller with 401**, and no IDOR was found across two users. One **S1 is
confirmed at runtime**
(`AUZ-002`, cross-tenant document read *and delete*) and one **S2**
(`AUZ-003`, suspension not reaching the token-only surface). Both need
decisions this audit may not take alone.

---

## 1. Method, and why the obvious method is wrong

Phase 0 warned that counting `require_permission(` is misleading. It is
worse than that: **this codebase authorizes in three structurally
different places**, and any single technique misses two of them.

| Layer | Where the check lives | How to see it |
|---|---|---|
| `agent.authz` | `Depends(require_permission(Permission.X))` — a dependency; reads roles from the JWT, touches no database | dependency graph |
| `identity.rbac` | `Depends(require_identity_permission(...))` / `require_platform_admin` — a dependency; re-derives permissions from the database | dependency graph |
| per-route policy | `_authorize(user, session, Action.X, resource)` **inside the handler** — e.g. `api.governance`, `api.workflow`, `api.semantic_catalog`, `api.onboarding` | runtime only |

A dependency-graph audit reported **117 of 149** `require_local_user`
routes as having "no permission gate", which reads like a 117-route
authorization hole. It is not: the governed-lifecycle routers authorize
in the handler. Conversely, a minimal-body probe reports those same
routes as `422`, because FastAPI validates the body before the handler
runs — so a minimal probe cannot see an in-handler check either.

So the matrix below was built in three passes:

1. **Structural** — walk the live `api.main.app` for every route and its
   real dependency closure (including the `Permission` each
   `require_permission` closure captured). 214 operations.
2. **Broad runtime sweep** — 213 operations × 9 principals (anonymous
   plus the 8 roles `identity.rbac.SEED_ROLES` seeds), minimal bodies.
   1,917 requests. Catches dependency-level gates and 401-vs-403.
3. **Targeted runtime probes with complete valid bodies and real
   resource ids**, on the governed-lifecycle surfaces. This is the only
   pass that tests an in-handler check.

> **A trap that voided one measurement and is worth naming.** `api/main.py`
> serves the built frontend from a catch-all `GET /{full_path:path}`. A
> request to a path that *does not exist* therefore returns **200** with
> `<!doctype html>`. An early probe in this audit read `GET /config` and
> `GET /schema` as authorized-and-allowed; neither is a route. On this
> app, a 200 is never on its own evidence that a route authorized you —
> check the path against the route walk first.

### Walking the routes

FastAPI stores an included router as an opaque wrapper carrying
`original_router` rather than splicing its routes into `app.routes`, so
`[r for r in app.routes if isinstance(r, APIRoute)]` finds **12 of 214**.
That silently-vacuous result is why
`tests/security/test_authorization_matrix.py` carries
`test_the_route_walk_actually_found_the_api` as an explicit non-vacuity
guard.

---

## 2. Route inventory by gate

214 operations across 187 paths (matches Phase 0's OpenAPI count of 212
plus the two catch-all/static routes it skipped).

| Gate composition | Ops | What it means |
|---|---|---|
| `verify_api_key` + `get_identity_db` + `require_local_user` | 149 | needs a real local account |
| `verify_api_key` only | 50 | of which **47** also carry a `require_permission` permission |
| `get_identity_db` only | 11 | the public auth surface + 2 share-view routes |
| none | 4 | `/health`, `/live`, `/auth/google/nonce`, the SPA catch-all |

Permission vocabulary actually bound to a route:

| Permission | Ops | | Permission | Ops |
|---|---|---|---|---|
| `agent:ask` | 28 | | `agent:documents_read` | 2 |
| `identity:platform.admin` | 14 | | `agent:golden_example_write` | 2 |
| `identity:admin.dashboard.read` | 9 | | `agent:media_generate` | 2 |
| `identity:shares.manage_own` | 5 | | `agent:media_search` | 2 |
| `agent:execute_sql` | 3 | | `agent:run_optimization` | 2 |
| `agent:admin_config` | 3 | | `agent:voice_use` | 2 |
| `identity:users.assign_roles` | 2 | | 5 others | 1 each |

**Observation, not a defect:** 20 of the 39 `identity.rbac.Permission`
values are seeded but bound to **no** route —
`GOVERNANCE_MANAGE`/`REVIEW`, `WORKFLOW_MANAGE`/`REVIEW`,
`CATALOG_MANAGE`/`REVIEW`, `EVAL_MANAGE`/`REVIEW`,
`RECOMMENDATION_MANAGE`/`REVIEW`, `BI_INTEGRATION_MANAGE`,
`USERS_READ`/`CREATE`/`UPDATE`/`DEACTIVATE`, `SESSIONS_*`, `AUDIT_*`,
`PROMPTS_*`, `OUTPUTS_*`, `CONVERSATIONS_*`. They are not dead: the
in-handler `_authorize` functions read them via
`get_user_permissions`, which is a *different* mechanism from
`require_identity_permission` and invisible to a dependency scan. Worth
knowing before anyone concludes a permission is unused and removes it.

---

## 3. The matrix

### 3.1 Anonymous — `AUZ-001` extended

Every one of the 213 operations (all methods, including the 29
parameterised paths Phase 0's GET-only probe skipped), with no
credential:

| Result | Count |
|---|---|
| non-public operations, **all** answered `401 Unauthorized` | **198 / 198** |
| intentionally public (`200`×3, `422`×7, `404`×3, `401`×2) | 15 |
| **non-denied on a route that should deny** | **0** |

(The 16th allowlist entry is the SPA catch-all `GET /{full_path:path}`,
excluded from the sweep because every filled path matches it. The two
public routes answering `401` are `POST /auth/refresh` and
`POST /auth/logout` with no cookie presented — correct.)

Phase 0 established this for 54 non-parameterised GETs. It now holds
across the whole surface, and uniformly — every single refusal is a
`401`, with no `404` and no `403` among them. Three further properties were checked, because
"denied" alone is not enough:

- **401, not 403, for a missing credential** — no route 403s an
  anonymous caller, so a client always knows to sign in. Verified for all
  198.
- **The public allowlist is exact in both directions.** 16 operations are
  intentionally public, each with its reason recorded in the test. A new
  unauthenticated route fails the test; so does an allowlisted one that
  has since been locked down.
- **A valid token is never answered with 401.** Swept for `viewer`,
  `user`, `auditor` and `support` across all 213 — zero spurious 401s, so
  an under-privileged caller cannot send the React app into a re-login
  loop.

### 3.2 Role differentiation

Status distribution per principal over 213 operations (minimal bodies, so
`422` means "dependency-level authorization passed"):

| principal | 200 | 201 | 401 | 403 | 404 | 422 |
|---|---|---|---|---|---|---|
| anonymous | 3 | 0 | **200** | 0 | 3 | 7 |
| viewer | 20 | 1 | 1 | 51 | 71 | 68 |
| user | 20 | 1 | 1 | 46 | 71 | 73 |
| analyst | 28 | 1 | 1 | 34 | 72 | 76 |
| admin | 43 | 1 | 1 | 14 | 74 | 78 |
| auditor | 23 | 1 | 1 | 74 | 62 | 51 |
| manager | 31 | 1 | 1 | 66 | 62 | 51 |
| support | 13 | 1 | 1 | 84 | 62 | 51 |
| platform_admin | 43 | 1 | 1 | 48 | 64 | 55 |

The gradient is real and monotone where it should be (viewer 51×403 →
admin 14×403). The only **mutating** routes a `viewer` reached were all
self-service: own logout, own logout-all, own avatar delete, own Google
unlink, own conversation create. Correct.

**`AUZ-004` (S3): `platform_admin` is less privileged than `admin` on the
AI surface.** `platform_admin` shows 48×403 against `admin`'s 14, and is
refused on 11 routes `admin` reaches (`/documents`, `/artifacts`,
`/questions`, `/schema/tables`, `/schema/refresh`, `/models`,
`/metrics/*`, …). Cause: `identity.rbac._PLATFORM_ADMIN` is
`_ADMIN | {PLATFORM_ADMIN}`, a strict superset — but those 11 routes are
gated by `agent.authz`, which reads **base role names from the JWT**, and
`platform_admin` is not in `agent.authz.ROLE_PERMISSIONS`, so it grants
nothing. `identity/rbac.py::BASE_ROLE_NAMES` documents this consequence
for `auditor`/`manager`/`support` and calls it deliberate and fail-closed
— but `platform_admin`'s own seed description says it operates "in
addition to every tenant-admin capability", which is not what happens.

Fail-closed, so not a security hole. It *will* surface in UAT as "the
platform admin account cannot open the dashboard" unless that account is
also granted `admin`. **Flagged, not fixed:** adding `platform_admin` to
`ROLE_PERMISSIONS` would *grant* permissions, which is a change to the
authorization model (Shared Rule 8). Decide between granting it and
documenting that a platform admin needs both roles.

### 3.3 Function-level authorization on the governed lifecycles

Complete valid bodies, real resource ids created by an `admin`, attempted
by `viewer` and by a plain `user`:

| Action | viewer | user | admin |
|---|---|---|---|
| create a governed asset draft | 403 | 403 | reaches handler |
| `PATCH` someone's asset draft | 403 | 403 | — |
| submit for testing / approve / deploy / rollback / retire | 403 | 403 | — |
| create a semantic catalog entry | 403 | 403 | 200 |
| `GET` / `PATCH` / publish a catalog entry | 403 | 403 | — |
| KPI monitoring scan | 403 | 403 | past the gate |
| schema-drift scan | 403 | 403 | past the gate |
| create an onboarding job | 403 | 403 | past the gate (400, config) |
| refresh the schema index | 403 | 403 | past the gate |
| compute an eval release gate | 404 | 404 | — |
| create a BI connector | 404 (feature off) | 404 | 404 |

Every privileged transition is refused, and the refusal comes from the
handler's own `_authorize`, not from body validation. The `admin` column
is the non-vacuity check: an admin gets *past* authorization and then
hits a configuration error (no database is configured in the harness),
which is a different failure and proves the gate opened rather than
refusing everyone.

Cross-tenant anti-enumeration is implemented here too:
`api.governance._authorize` raises 404 (not 403) when
`decision.reason == "cross_tenant"`, matching `identity.share_policy`.

### 3.4 IDOR / BOLA — clean

Alice (admin) creates; Bob (a valid plain `user` in the same tenant)
attacks. Every attempt **404**, with the body byte-identical to a
nonexistent id:

| Attempt | Result |
|---|---|
| read Alice's conversation | 404 |
| read its messages | 404 |
| rename it (`PATCH`) | 404 |
| append a message to it | 404 |
| delete it | 404 |
| read its share | 404 |
| share it (`anyone_with_link`) | 404 |
| regenerate its share link | 404 |
| revoke its share | 404 |
| invite himself to it | 404 |
| revoke Alice's session | 404 |

Three properties beyond "denied", each its own test:

- **Harmless, not merely refused.** After every attempt, Alice's
  conversation still returns 200 with its original title. A 404 returned
  *after* a successful delete would pass a naive table while losing the
  data.
- **Not listed.** Alice's conversation does not appear in Bob's own
  `GET /conversations`.
- **Indistinguishable from nonexistent.** `GET` on Alice's id and on a
  random UUID return the same status *and the same body*, so the API is
  not a resource-id oracle.

Alice's session still worked after Bob's revoke attempt.

### 3.5 Mass assignment — clean

Every request model carries `extra="forbid"` (128 occurrences across
`api/` and `identity/`), so an injected privileged field is a **422**
before anything runs. Tested one field at a time, to be sure the 422 was
the extra-field rejection and not an unrelated validation error:

`role`, `roles`, `tenant_id`, `status`, `is_email_verified`, `is_admin`,
`id`, `user_id`, `password_hash`, `failed_login_count` on
`POST /auth/register` → **422 `extra_forbidden`** each. `roles`, `email`,
`status`, `tenant_id`, `is_email_verified` on `PATCH /auth/me` → **422**
each. A clean payload still succeeds (non-vacuity).

The database row was checked afterwards in each case: `tenant_id` stayed
`default`, `status` stayed as the server set it.

---

## 4. Findings

### 4.1 `AUZ-002` (S1, confirmed) — documents and media are not tenant-partitioned

Phase 1 flagged this as latent from code structure. **It is live.**

Static evidence — no tenant exists to filter on:

```
rag.store.list_documents(engine, collection=None)            tenant param: False
rag.store.similarity_search(engine, collection, q, top_k)    tenant param: False
rag.store.get_document_bytes(engine, document_id)            tenant param: False
rag.store.delete_document(engine, document_id)               tenant param: False
rag.store.insert_document(engine, filename, collection, ...) tenant param: False

DocumentRecord fields: id, filename, collection, sensitivity_category,
  upload_date, status, chunk_count, error_message, has_pdf_bytes,
  uploaded_by, restricted_roles        <- no tenant_id

"tenant" in api/documents.py: 0 occurrences
```

Runtime reproduction — a `tenant_b` admin with a JWT carrying
`tid="tenant_b"`, against a document belonging to `default`:

```
GET    /documents                  -> 200   lists "tenant-A-confidential.pdf"
GET    /documents/doc-a-1/download -> 200   b'TENANT A CONFIDENTIAL BYTES'
DELETE /documents/doc-a-1          -> 204   delete_document called with ['doc-a-1']
```

So it is a cross-tenant **read of document content** and a cross-tenant
**destructive write**. Both are S1 by the rubric.

The same absence holds for the media surface, by the same measurement:
`media/store.py`, `api/media.py`, `api/media_search.py`,
`api/media_library.py` and `api/attachments.py` contain the string
"tenant" **0 times each**. `enable_media_search` defaults to **true**.
(`api/artifacts.py` has 26 and `api/jobs.py` 5, so this is not a
project-wide gap — it is specific to these stores.)

**Honest limits of the proof.** No SQL Server `VECTOR` store exists in
this environment, so `rag.store`'s functions were mocked exactly as
`tests/test_api_documents.py` mocks them. What the mock stands in for is
only "the store returned a row". Because no store function *accepts* a
tenant, there is no configuration in which filtering could occur — the
absence of the control is a property of the signatures, not of the mock.
A human should still re-run this against a real store (§6).

**Flagged, not fixed.** The fix is a `tenant_id` column on the document
and chunk tables plus a migration, a tenant parameter threaded through
five store entry points, and a backfill decision for existing rows —
squarely inside Shared Rule 8's "DB schema/migration" stop condition.

**Proposed fix, for the owner:**

1. Add `tenant_id NOT NULL` to the document and chunk tables (new Alembic
   revision; existing rows backfill to `DEFAULT_TENANT_ID`, which is what
   they implicitly are today).
2. Add `tenant_id` to `DocumentRecord` and a required `tenant_id`
   parameter to `list_documents`, `similarity_search`,
   `get_document_bytes`, `delete_document`, `insert_document` — required,
   not defaulted, so a caller that forgets it fails to import rather
   than silently getting the old behaviour. (CLAUDE.md §5.4 records
   exactly this lesson from `SEC-006`: optional parameters mean a new
   caller silently gets the weaker path.)
3. Resolve the tenant in `api/documents.py` from
   `resolve_tenant_id_for_identity(identity)` — never from the request —
   and return the anti-enumeration **404** for a cross-tenant id.
4. Do the same for `media/store.py` and its three routers.
5. Add `docs/MULTI_TENANCY.md` rows for both stores; they are absent from
   its 17-row partition table *and* from its 7 known limitations, which
   is how this stayed invisible.

Pinned by `tests/security/test_authorization_matrix.py::TestCrossTenantDocumentAccess`
as three `xfail(strict=True)` tests. Strict means that when the fix lands
they become XPASS *failures*, so the marker and the register must be
updated together.

### 4.2 `AUZ-003` (S2, confirmed) — suspension does not reach the token-only surface

`api.authz.require_permission` reads roles from the signature-verified
JWT and touches no database. `security.tenancy.resolve_tenant_id_for_identity`
likewise reads the `tid` claim and never checks the tenant's **status**.
`resolve_tenant_context` — the function that *does* read status live — is
called from exactly two places outside tests:
`api/identity_auth.py::_resolve_login_tenant` (login and refresh) and
`api/identity_authz.py::require_local_user`.

Measured with the access token untouched:

| Route | user suspended | tenant suspended |
|---|---|---|
| `GET /auth/me` (identity.rbac) | 401 | 401 |
| `GET /conversations` (identity.rbac) | 401 | 401 |
| `GET /navigation` | 401 | 401 |
| `GET /models` (agent.authz) | **200** | **200** |
| `GET /sources` (agent.authz) | **200** | **200** |
| `GET /schema/tables` (agent.authz) | **200** | **200** |
| `POST /ask` (agent.authz) | **200** | **200** |

`POST /ask` is the point. A suspended user in a suspended tenant asked a
question and got an answer. `GET /schema/tables` additionally discloses
table names.

CLAUDE.md §5.3 says a missing, soft-deleted or suspended tenant "fails
closed". It does on the identity-RBAC surface and does not here.
`identity/security.py`'s own docstring says a tenant suspension "must not
wait for a token to expire, which is why `resolve_tenant_context` reads
status live" — true of the function, not of this surface.

**Bound:** one access-token lifetime, `access_token_expire_minutes`
(default **15**). `POST /auth/refresh` correctly refuses, so it
self-heals. Note the setting is `Field(default=15, gt=0)` with **no upper
bound**, so an operator who sets 1440 for convenience extends this to a
day.

**Flagged, not fixed.** Every available fix is an architectural decision:

- *Option A — check in `api/auth.py::verify_api_key`.* One chokepoint,
  covers everything. Costs an identity-DB round trip on **every**
  request including `auth_mode="none"`, and `api/auth.py` imports no
  database today. Highest correctness, highest latency and layering cost.
- *Option B — check in `api/authz.py::require_permission`.* Scoped to the
  47 permission-gated routes, still a DB read per request on the
  latency-critical `/ask` path.
- *Option C — short-lived access tokens plus a revocation list.* The
  standard answer; needs shared state, and CLAUDE.md §13 records that
  every such store here is per-worker.
- *Option D — accept it and document the 15-minute window.* Legitimate
  for UAT on a closed network, and it is the status quo; it just has to
  be *written down*, which it currently is not.

Recommendation for UAT: **Option D now** (document it, and do not raise
`access_token_expire_minutes` above 15), **Option B after**, with the
lookup cached per-process for a few seconds so `/ask` pays it once per
burst.

### 4.3 `AUZ-005` (S3, by design) — golden examples influence other users in the tenant

The prompt asks whether a normal user's thumbs-up can inject a golden
example that later steers other users' SQL. **It can, within their own
tenant.**

`Permission.GOLDEN_EXAMPLE_WRITE` is granted at the `user` tier
(`agent.authz._USER`), `POST /feedback/golden-example` writes to the
shared store, and `embeddings.golden_examples.retrieve_golden_examples`
returns it as a few-shot example to everyone in that tenant. There is no
review step between write and retrieval.

Mitigations that are genuinely present:

- **Tenant-partitioned, twice.** `tenant_id` is part of the deterministic
  example id, the Chroma query carries `where={"tenant_id": ...}`, *and*
  a post-filter drops any document whose metadata tenant differs. So the
  blast radius is one tenant, not the deployment.
- **Rate-limited** per caller (`enforce_api_action_rate_limit`), added
  specifically to stop store flooding.
- **No privilege escalation.** A poisoned example only biases what the
  model proposes. The proposal still passes `agent/sql_validator.py` and
  the result-governance gate before anything reaches a caller, so a
  poisoned example cannot produce DML, reach a system catalog, or unmask
  a restricted column.

So the reachable harm is to **accuracy**, not confidentiality: a user
could teach the model that "revenue" means the wrong column, for
everyone in their tenant. Whether that is acceptable is a product
decision about the thumbs-up feature, not a defect, so it is recorded
rather than changed. A `viewer` cannot write; a `user` can. Both are
pinned by
`tests/security/test_authorization_matrix.py::TestTheGoldenExampleWriteBoundary`
so narrowing it later is a conscious change.

### 4.4 `BP-003` (S3, fixed) — tests could reach the developer's real databases

Found while running this phase's own sweep; the detail is in
`phase-3-authentication.md` §4. Summary: `tests/conftest.py` stripped
only env vars named after `Settings` fields, so the open-ended
`DB_CONNECTIONS`/`DB_<NAME>_*` and `MCP_SERVERS`/`MCP_<NAME>_*` families
survived and `get_settings().databases` returned four real SQL Server
connections inside a test. Fixed and pinned by
`tests/test_settings_env_isolation.py`.

It belongs in this phase's report because it is why the first run of the
authorization sweep contacted a production-shaped database — a test
suite that can reach real infrastructure is an authorization problem in
its own right.

### 4.4b Type-checking note on the changed files

`mypy` over the six production files this audit changed reports **0
errors in those files**. The 9 errors it reports transitively are in four
other modules: the four SQLAlchemy `SQLCoreOperations`-vs-`None`
assignments CLAUDE.md 13 already records in
`identity/repositories/semantic_intelligence.py` and
`recommendation_governance.py`, plus **three in
`agent/sql_validator.py` at lines 1068/1549/1554, which postdate the
`:839` pair 13 records** — they arrived with Phase 2's
`projection_source_columns` work.

One of those three reads alarmingly: `"frozenset[str]" has no attribute
"add"`. It was checked and is a **false positive**, not a latent
`AttributeError`: `sources` is bound as a `frozenset` inside the
`Union`/`Except`/`Intersect` branch, which `return`s at line 1533, and
rebound as a `set` at line 1548 in the `exp.Select` path. The two never
coexist; mypy simply applies the first binding's type to the second.
Renaming one of them would silence all three, but editing the security
boundary for type hygiene is out of this phase's scope — recorded for
the Phase 8 pass instead.

### 4.5 Audit trail

Present and reasonable, checked by reading every emission site:

- `api.authz.require_permission` logs `authz_denied` with subject, roles,
  the missing permission and the path.
- `api.identity_authz.require_identity_permission` logs
  `identity_authz_denied` likewise.
- `api.auth.verify_api_key` logs `auth_failed` with the *reason* and
  client IP, never the attempted credential.
- Privileged lifecycle actions log their own events
  (`governance_asset_approved`, `account_locked`,
  `refresh_token_reuse_detected` at `critical`, `logout_all_sessions`,
  `password_changed`, …) with the actor's user id.
- `security.audit_log.set_audit_tenant_id` stamps the request's tenant
  onto later events in the same context, and `signin_events` keeps a
  per-attempt row with a specific `failure_reason_code` server-side even
  though the response is generic.

**Not tamper-evident, and not claimed to be.** Records go to the standard
logger and to ordinary database tables with no hash chain or append-only
enforcement. For UAT that is adequate; it should be stated in the
operational documentation rather than implied.

**UNVERIFIED:** that the audit trail *survives* in the UAT deployment.
Log destination and retention are deployment configuration, and nothing
here pins them.

### 4.6 Data-level authorization inside the agent — partially verified

| Control | Status |
|---|---|
| Per-tenant database visibility (`settings.databases_for_tenant`) | verified by code read; `/execute` and `/schema/tables` resolve the tenant server-side and 404 a cross-tenant database name |
| Restricted-column gate before the model sees values | covered by Phase 2 (`SEC-006`) |
| Golden examples filtered by tenant before retrieval | verified — §4.3 |
| Policy-chunk `sensitivity_category` gate | code read only; needs a live RAG store |
| Document `restricted_roles` second gate | code read only; needs a live RAG store |
| **Per-user** schema entitlement *within* a tenant | **does not exist** — see below |

The last row is worth stating plainly. Entitlement is per **tenant**, not
per **user**: any caller with `Permission.ASK` in a tenant can retrieve
any table of any database that tenant may query into the generation
prompt. There is no per-user table or column allowlist. That is a
coherent design, and the restricted-column gate is what bounds
*sensitive* values — but it is not what the prompt's "a user only gets
schema/tables/columns/databases they're entitled to" implies, so it is
recorded here rather than ticked off.

---

## 5. Fixes made in this phase

| Finding | Change | Test |
|---|---|---|
| `BP-003` | `tests/conftest.py` strips the dynamic `DB_*`/`MCP_*` env families | `tests/test_settings_env_isolation.py` (7); revert → **3 fail** |

Everything else this phase produced is **evidence and regression
coverage**, not behaviour change — `tests/security/test_authorization_matrix.py`,
57 tests:

| Class | Tests | Pins |
|---|---|---|
| `TestEveryRouteDeniesAnAnonymousCaller` | 213 + 2 | deny-by-default; exact public allowlist; 401-not-403 |
| `TestAnAuthenticatedCallerIsNeverMistakenForAnonymous` | 4 | no spurious 401 for 4 roles × 213 routes |
| `TestNoCrossUserAccess` | 14 | IDOR, including harmlessness and anti-enumeration |
| `TestPrivilegedRoutesDenyUnderPrivilegedRoles` | 13 | in-handler authz with valid bodies, + an admin non-vacuity check |
| `TestTheGoldenExampleWriteBoundary` | 2 | `AUZ-005`'s measured boundary |
| `TestSuspensionDoesNotReachTheTokenOnlySurface` | 5 | `AUZ-003` as current behaviour |
| `TestCrossTenantDocumentAccess` | 1 + 3 `xfail(strict)` | `AUZ-002` |
| `test_the_route_walk_actually_found_the_api` | 1 | the whole file is not vacuous |

---

## 6. Commands a human must run

```powershell
.venv\Scripts\python.exe -m pytest tests/security/test_authorization_matrix.py -q
.venv\Scripts\python.exe -m pytest tests/test_settings_env_isolation.py -q
.venv\Scripts\python.exe -m pytest tests/security/ tests/test_api_authz.py tests/test_identity_rbac.py -q
```

`test_authorization_matrix.py` issues roughly 2,400 real HTTP requests
and rebuilds the identity database per test, and completes in **51s**
(240 passed, 16 skipped, 3 xfailed).

It took **24 minutes** on its first run, before `BP-003` was fixed — the
leaked real `DB_CONNECTIONS` meant every route touching a database
opened a live SQL Server connection. A 28× speed-up was the incidental
reward for closing that finding, and the slowness was the clue worth
following.

---

## 7. Environment-blocked (not verified, not passed)

| Check | Why blocked | What a human must run |
|---|---|---|
| **`AUZ-002` against a real store** | no SQL Server `VECTOR`/Azure SQL store here; `rag.store` was mocked | Ingest one document as tenant A, then run the three probes in §4.1 as a tenant B admin against the real store. Expected today: the same 200/200/204 |
| **Cross-tenant media / attachments** | `enable_media_search` needs Tesseract and a media library; not exercised | Same two-tenant probe against `/media/library/{id}`, `/media/{id}`, `/attachments/{id}` |
| **Policy-chunk sensitivity gate and `restricted_roles`** | needs a live RAG store with a classified document | Ingest a policy chunk with a `sensitivity_category`, confirm it is never summarized for any role |
| **Per-tenant database routing end to end** | needs two tenants each with a real, distinct database | Confirm `/execute` and `/schema/tables` 404 a database belonging to the other tenant |
| **OIDC role mapping** | no live IdP | Confirm the configured roles claim maps to `agent.authz` role names, and that an unmapped role grants nothing |
| **Audit-log durability** | deployment configuration | Confirm the UAT host persists `security.audit_log` output and the `signin_events` table with a retention policy |
| **Authorization under real concurrency** | sequential `TestClient` only | Re-run the IDOR table from parallel clients; a sequential test passes even against a race |

---

## 8. Session summary

- **Open S1:** 1 — `AUZ-002`, cross-tenant document read *and* delete,
  confirmed at runtime. Needs a migration, so flagged for an owner.
- **Open S2:** 1 from this phase — `AUZ-003`, suspension not reaching the
  `agent.authz` surface (including `POST /ask`), bounded by the
  15-minute access-token lifetime.
- **Fixed this session:** `BP-003` (S3, test isolation), plus Phase 3's
  `AUN-001`/`002`/`003`/`004`.
- **Flagged for decision:** `AUZ-002`, `AUZ-003`, `AUZ-004`
  (`platform_admin` powerless on the AI surface), `AUZ-005` (golden-example
  influence).
- **Next phase:** Phase 5 (hallucination and grounding), which needs a
  real Ollama and a real database and so cannot run as a mocked session.
