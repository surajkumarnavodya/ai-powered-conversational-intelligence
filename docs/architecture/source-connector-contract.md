# Source connector contract — Batches 1, 3 and 5

**Scope.** The design of `sources/` and `GET /sources`: what the contract
is, what it deliberately is not, and which invariants it carries. This
document owns the **connector contract's design and rationale**.

Status and priority of each source live in
[`feature-inventory.md`](feature-inventory.md); per-source-type
capabilities and auth in [`data-source-matrix.md`](data-source-matrix.md);
the batch plan in
[`implementation-roadmap.md`](implementation-roadmap.md).

**Date:** 2026-10-15. **Batches:** 1 (contract + registry), 3
(per-conversation source selection) and 5 (REST/OpenAPI connector) of the
source-connectivity roadmap.

---

## 1. What Batch 1 did

Two halves, in deliberately different states.

### 1.1 Descriptors — live, with a real caller

`sources/descriptors.py` declares one `SourceDescriptor` per source the
platform already routes to: name, kind, routing description, required
permission, and an `is_configured` predicate.

**This replaced three parallel structures with one owner.** Before, each
source's facts lived in three places inside
`agent/orchestrator/nodes.py`:

| Fact | Used to live in | Now |
|---|---|---|
| routing description (prompt text) | `_SOURCE_DESCRIPTIONS` literal | derived from descriptors |
| required permission | `SOURCE_PERMISSIONS` literal | derived from descriptors |
| "is it configured" | the `if`-chain in `get_available_sources` | derived from descriptors |

All three now derive from the descriptor table. Adding a source is one
descriptor instead of three edits where doing two of three failed
silently — which is [`CLAUDE.md`](../../CLAUDE.md) §4.8, one owner per
fact.

**Behaviour is unchanged**, and that is tested rather than asserted — see
§4.

### 1.2 Connector lifecycle — contract only, no production implementer

`sources/types.py::SourceConnector` is a Protocol: `validate`, `health`,
`discover`, `search`, `close`. Nothing in `api/` or `agent/` implements it
yet. The first implementer is the SharePoint/OneDrive batch.

This is the **"built, not wired"** state [`CLAUDE.md`](../../CLAUDE.md) §1
describes and §4.7 protects — do not "finish the wiring" as a side effect
of unrelated work, and do not delete it as dead code.

What keeps it from being merely aspirational: `sources/mock.py` implements
it and `tests/test_sources_conformance.py` runs against it. A Protocol
alone cannot be wrong; an implementation plus a suite that rejects a
non-conforming connector can be. **`rest/` (Batch 5) is now the first
real implementer** — see §9.

---

## 2. Why the lifecycle generalizes `onboarding/` rather than replacing it

The audit's largest design risk (R1 in
[`feature-inventory.md`](feature-inventory.md) §7) was building a second
owner of connection lifecycle. `onboarding/` already is one, for
databases:

| Stage | `onboarding/` already has |
|---|---|
| create / list / get | `POST`/`GET /onboarding/jobs` |
| validate + discover | `POST /jobs/{id}/discover` |
| human review | `GET /jobs/{id}/review-items`, `POST .../decide` |
| publish / enable | `POST /jobs/{id}/publish` |
| register as routable | `POST /jobs/{id}/register-for-chat` |
| cancel / retry | `POST /jobs/{id}/cancel`, `/retry` |
| RBAC + ABAC per action | `onboarding/policy.py::authorize_onboarding_action` |
| **secret by reference** | `identity.models.OnboardingJob` has **no password column** |

So the Protocol's stage names and its secret-by-reference rule are taken
from that precedent. **No new persistence was added in this batch.**
Whether `OnboardingJob` generalizes to non-database sources is a Batch 4
decision, deferred until a real connector needs it — adding tables now
would be schema invented ahead of a caller.

---

## 3. `GET /sources` — an affordance, not a boundary

`api/sources.py` mirrors `api/navigation.py` deliberately: same pattern,
one step down the stack. That endpoint answers "which screens may this
caller open"; this one answers "which sources may this caller ask".

```
GET /sources  ->  { sources: [...], usable: [...], roles: [...] }
```

Each source reports `configured` and `authorized` **separately**. They are
different failures with different remedies — an operator fixes
configuration, an administrator fixes permission — and a single
"unavailable" flag would leave the UI unable to say which. That is the
exact bug `agent/orchestrator/nodes.py` records in its own comments for
the routing path: a denied source dropped silently, leaving the caller
with an unrelated generic failure and no idea their role was the reason.

**The endpoint changes no access decision.** The gate stays where it was:
`router_node` filters the LLM's own source selection through
`SOURCE_PERMISSIONS` before any subgraph runs, and every data-touching
route re-checks. Hiding a source here is cosmetic
([`CLAUDE.md`](../../CLAUDE.md) §5.2: a UI-level restriction is never a
boundary). `tests/test_api_sources.py::TestItIsNotABoundary` pins this, so
a later change cannot quietly make the UI's view the thing that decides.

It reads an in-process table — no LLM, database or network call, and no
secret in the response body (tested against settings that deliberately
contain a real-shaped connection string and API key).

---

## 4. How Batch 1's "behaviour is unchanged" is actually proven

`tests/test_sources_descriptors.py` does not import the new table to build
its expectations — that would make every assertion vacuous. Instead it:

1. **Transcribes the pre-refactor values by hand** — the description
   prefixes, and `SOURCE_PERMISSIONS` exactly as the literal dict held it,
   including which three sources carried *no* permission.
2. **Reimplements the deleted `if`-chain verbatim** as an oracle, kept
   deliberately literal so rewriting it cannot "fix" a behaviour the
   original had.
3. **Sweeps two dimensions**: all 64 combinations of the six feature flags,
   and all 16 combinations of the four config values each clause also
   required, each × attachments on/off.

The second sweep exists because of a real miss. An earlier version swept
only flags, always with their config present — so it never produced a
"flag on, config absent" case, which is precisely the `and <config>` half
of each clause. A mutation dropping `and s.web_search_api_key` **survived
it**, caught by only one unrelated test. With the config sweep added, the
same mutation fails 17 tests.

### Mutation evidence

Both run against the live code, then reverted:

| Mutation | Result |
|---|---|
| `is_configured=lambda s: bool(s.enable_web_search)` (drop the API-key requirement) | **17 failed**, 162 passed |
| `permission=None` on the `policy` descriptor | **2 failed**, 177 passed |

Clean: `179 passed`.

`tests/test_sources_conformance.py::TestTheSuiteIsNotVacuous` does the
same job for the connector suite: it builds connectors that ignore
`limit`, leak a secret in `health.detail`, and return empty instead of
raising, and asserts each is detectably broken.

---

## 5. Invariants Batch 1 carries

- **A descriptor with `permission=None` is not unrestricted.** `sql`
  carries no source-level permission and is still bounded by SQL
  validation, the result gate, the restricted-column gate and tenant
  scoping. Source-level permission is one layer, never the only one.
- **Unknown means deny.** `get_descriptor` raises `UnknownSourceError`
  rather than returning `None`, so a caller cannot fall back to a default
  ([`CLAUDE.md`](../../CLAUDE.md) §5.3). Its `safe_message` is a fixed
  string and never echoes the requested name, so a refusal cannot be used
  as an oracle (§5.5).
- **Duplicate descriptor names fail at import**, not at first use — a
  shadowed descriptor could be the stricter of the two, so the
  consequence is a permission check that quietly stops applying.
- **Conservative capability defaults.** Every `SourceCapabilities` field
  defaults to the cautious answer; a connector opts in explicitly.
  Forgetting one makes the platform assume less, never more.
- **No implemented source declares `has_side_effects=True`**, and a test
  asserts it. A connector that genuinely mutates a source system has to
  make that declaration deliberately, and the roadmap requires such an
  operation to be off by default.
- **Read and write are separate `SourceOperation` members**, not a
  read/write flag, so a connector growing a write operation declares a new
  one rather than widening an existing one.
- **The mock is never registered.** `sources/mock.py` is imported only by
  tests; a mock in `GET /sources` would advertise a source the router
  cannot reach.
- **`preserves_source_acl` is an honest statement, not an aspiration.** It
  is `False` for every source whose access control this platform enforces
  itself, and that distinction is what makes delegated auth the design for
  the Microsoft 365 batches — see
  [`data-source-matrix.md`](data-source-matrix.md) §5.

---

## 6. What Batch 1 deliberately did not do

- **No persistence.** No tables, no migration. Deferred to the batch that
  needs it (§2).
- **No new connector.** No SharePoint, Dataverse, Outlook, REST or MCP —
  each is its own batch, and the M365 ones are credential-blocked.
- **No change to existing sources' runtime behaviour.** The SQL, RAG, web,
  media and attachment paths are untouched; only where their *declarations*
  live changed.
- **No frontend.** The source picker is Batch 3. `/sources` was added to
  `vite.config.ts`'s `BACKEND_ROUTES` now, because
  [`CLAUDE.md`](../../CLAUDE.md) §9 records that omission as a recurring
  dev-only miss. Note `'/search'` was already there and `^/search` does
  **not** match `/sources`, so the entry was genuinely needed.
- **No sync, cursors or deletion propagation.** The matrix's
  search-on-demand-first decision removes these from the first connector
  batch entirely.

---

## 7. Files

### Batch 1

| File | Role |
|---|---|
| `sources/types.py` | the contract: descriptor, capabilities, evidence, lifecycle Protocol, errors |
| `sources/descriptors.py` | the 7 declared sources — single owner of each one's facts |
| `sources/registry.py` | lookup + the configured/authorized split |
| `sources/mock.py` | reference connector, tests only |
| `sources/__init__.py` | public surface |
| `api/sources.py` | `GET /sources` |
| `api/sources_schemas.py` | its response models |
| `agent/orchestrator/nodes.py` | three tables now derived (the only live-path change) |
| `api/main.py` | router registration |
| `frontend/vite.config.ts` | `/sources` dev-proxy route |
| `tests/test_sources_descriptors.py` | the equivalence proof (§4) |
| `tests/test_sources_registry.py` | registry behaviour and descriptor invariants |
| `tests/test_sources_conformance.py` | the conformance suite |
| `tests/test_api_sources.py` | endpoint, incl. no-secret-leak and not-a-boundary |

### Batch 3

| File | Role |
|---|---|
| `agent/orchestrator/nodes.py` | `_apply_source_selection` — the filter (§8.1) |
| `agent/orchestrator/state.py` | `selected_sources`, `source_selection_notice` |
| `agent/orchestrator/graph.py` | `selected_sources` param; short-circuit |
| `api/schemas.py` | `AskRequest.sources` + name validator; `AskResponse.source_selection_notice` |
| `api/main.py` | threads the selection and the notice |
| `frontend/src/components/chat/SourcePicker.tsx` | the picker |
| `frontend/src/components/chat/ChatInput.tsx` | mounts it |
| `frontend/src/components/chat/TurnCard.tsx` | renders the notice |
| `frontend/src/store/settingsStore.ts` | `selectedSources` (persisted) |
| `frontend/src/store/chatStore.ts` | sends it on `/ask` |
| `frontend/src/lib/{api,types,history}.ts`, `hooks/queries.ts` | client, types, `useSources` |
| `frontend/src/i18n/locales/*/translation.json` | 11 `sources.*` keys × 5 locales |
| `tests/test_source_selection.py` | the R4 security regression + non-vacuity |
| `tests/test_api_source_selection.py` | schema validation + router wiring |
| `frontend/src/components/chat/SourcePicker.test.tsx` | picker behaviour and states |

### Batch 5

| File | Role |
|---|---|
| `security/outbound_url.py` | the SSRF controls: allowlist, blocklist, redirect-safe bounded JSON GET |
| `rest/types.py` | `RestEndpoint`, `RestMethod` — the operator-declared call |
| `rest/connector.py` | `RestConnector` + its (unregistered) descriptor |
| `rest/__init__.py` | public surface |
| `config/settings.py` | 5 `rest_*` settings + the comma-separated splitter |
| `.env.example` | the operator-facing block |
| `tests/test_outbound_url.py` | 61 SSRF tests + the three-way blocklist pin |
| `tests/test_rest_connector.py` | configuration validity, read-only posture, no query interpolation |
| `tests/test_sources_conformance.py` | `Harness`/`TestUniversalContract`; `RestConnector` enrolled |

---

## 8. Batch 3 — per-conversation source selection

`AskRequest.sources` lets a caller restrict one question to named
source(s). `GET /sources` feeds the picker that produces it.

### 8.1 The security property, and where it lives

**R4:** a client-supplied source list is a *filter*, never a grant.
`agent/orchestrator/nodes.py::_apply_source_selection` returns a subset of
what `get_available_sources` already allows, and the pre-existing role
check downstream in `router_node` still runs on whatever survives. So the
selection narrows a route; it cannot widen one.

The filter is applied **before** the branch that short-circuits or calls
the classifier, which has a useful side effect: narrowing to one source
skips the LLM classification call entirely, so a caller who has already
decided does not pay for the model deciding again.

### 8.2 Mutation evidence

The filter was replaced with a union — `sorted(set(available) | keep)`,
the exact R4 mistake — applied to live code and then reverted:

| Mutation | Result |
|---|---|
| intersection → union | **19 failed**, 27 passed |

Clean: `46 passed`. The failures include every assertion in
`TestSelectionCannotGrant`, the subset invariant, and the end-to-end
"a viewer naming `policy` still gets denied" case.

`TestTheGuardIsNotVacuous` additionally builds the broken union
implementation inline and asserts it *does* grant, so the guard cannot
pass by both sides being wrong together.

### 8.3 Decisions worth recording

| Decision | Why |
|---|---|
| An unrecognized name → `422`; configured/authorized → **not** checked at the schema layer | A 422 on availability or permission would be an oracle for another tenant's configuration and for the caller's own roles. Names are already public via `GET /sources`, so rejecting an unknown one leaks nothing |
| An empty array is rejected | `[]` is ambiguous between "no restriction" and "no sources at all"; the latter makes the question unanswerable. Callers omit the field |
| An unhonorable selection falls back **with disclosure**, not a failure | A stale client or an operator switching a source off must not become an outage for an answerable question. Mirrors how a role-denied source already falls back |
| `source_selection_notice` is a **separate field** from `permission_denied_notice` | "Not configured" and "not permitted" have different causes and different remedies — an operator fixes one, an administrator the other. One field for both would leave the caller unable to tell which. Both can be set on the same turn |
| `attachments` is exempt from the filter | Attaching a file is itself an explicit per-request choice, and the attachment feature's own rule is that an attachment is never silently discarded |
| A selection breaks the `ENABLE_MULTI_SOURCE_ROUTER=false` short-circuit | Nothing else would apply the filter or report an unhonorable selection. `sources=None` leaves that path byte-identical, so the default deployment is unaffected |
| The store normalizes `[]` → `null` | So "I unchecked the last box" and "I never opened the picker" mean the same thing, and the request omits the field rather than sending a value the server rejects |

### 8.4 The picker is an affordance

`SourcePicker.tsx` shows a source the caller cannot use as **disabled
with a reason** (`not set up` vs `no access`) rather than omitting it, so
"why can't I pick this" has a visible answer. Hiding it would be no more
secure — the server re-decides every request — and less honest.

`per_request` sources are excluded from the standing choices: their
availability depends on whether a file is attached to that individual
question, so offering them as a persistent preference would mislead.

---

## 9. Batch 5 — REST/OpenAPI connector

`rest/` calls a fixed set of operator-declared, allowlisted HTTPS
endpoints read-only. `security/outbound_url.py` holds the SSRF controls.

### 9.1 The duplication decision, stated plainly

Two copies of the resolve-and-blocklist check already existed:
`media_gen/download.py` (which also has the redirect-revalidation loop)
and `bi_integration/outbound_safety.py`, whose own docstring explains why
it did not extract a shared helper — `tests/test_media_gen.py` patches
`media_gen.download.log_security_event` directly, and moving that call out
of that namespace would break an audited suite.

That reasoning still holds, so **Batch 5 did not touch either.**
`security/outbound_url.py` is the canonical home for *new* callers. There
are therefore **three** copies of the blocklist, which for a security
control is only acceptable if divergence cannot go unnoticed. What makes
it acceptable is a test, not a convention:
`tests/test_outbound_url.py::TestBlocklistsAreIdentical` asserts all three
match exactly, plus a second check that the list still covers the ranges
that matter — so editing all three together into something weaker fails
too.

**Consolidating the two older copies is its own task** and has to deal
with those patch targets. It is not done.

### 9.2 What this batch adds beyond the older two

| Control | Why it is new here |
|---|---|
| **Host allowlist, checked before DNS** | A blocklist alone is deny-known-bad. Fine for a provider CDN URL the app itself built; not sufficient for an operator-configured endpoint, where the safe set is small, enumerable, and should be stated positively. Checking it before resolution also means this server makes no network request at all — not even a lookup — on behalf of a rejected URL |
| **Exact host matching** | A suffix check accepts `api.example.com.evil.net`; a substring check accepts `evil-api.example.com`. Both are tested |
| **Every resolved address checked** | Not `addrinfo[0]`. A host with one public and one private A record must be refused, and taking the first would let record ordering decide |
| **Bounded JSON GET** | Cap enforced against a declared `Content-Length` before any body is read *and* against bytes received, since a response can omit or lie about it |

### 9.3 Mutation evidence

Each applied to live code, then reverted. Clean: `61 passed`.

| Mutation | Result |
|---|---|
| drop per-hop redirect re-validation | **3 failed**, 58 passed |
| drop the host allowlist check | **9 failed**, 52 passed |
| check only the first resolved address | **1 failed**, 60 passed |

### 9.4 A real finding: the suite was making DNS calls

After enrolling `RestConnector` in the conformance suite, a probe run with
`socket.getaddrinfo` stubbed to raise caught **four tests making real DNS
lookups** for `api.example.com` — through `health()`, which resolves. They
passed (the name NXDOMANs, so health reported unhealthy), which is exactly
why it went unnoticed: a green test that quietly depends on the network is
slow, fails offline, and breaks the repo's fully-mocked rule
([`CLAUDE.md`](../../CLAUDE.md) §7).

Fixed with a `Harness.prepare` hook that patches DNS and transport before
every universal check. Re-probed: **137 passed with `getaddrinfo` globally
blocked** across all three new test files.

### 9.5 A correction to Batch 1's claim

Batch 1's conformance-suite docstring said any connector could be added to
`_CONNECTORS` and "inherit every check". That was overstated — those
checks are mock-shaped (they drive a `(corpus, configured=, healthy=)`
constructor and assert against corpus contents). A real connector takes
its own constructor arguments and reads its own `Settings`.

The suite now has two tiers: the existing corpus-aware checks over
`_CONNECTORS`, and `TestUniversalContract` over `_HARNESSES` — the parts
of the contract that do not depend on what a connector reads. `RestConnector`
is enrolled in the second. §4's claim is corrected accordingly.

### 9.6 Deliberately not done

- **Not routable.** No `sources/descriptors.py` entry, so `GET /sources`
  does not advertise it and the router cannot reach it — there is no
  orchestrator node for it, and advertising a source nothing can answer
  is worse than omitting it.
- **No write verbs.** `RestMethod` has `GET` and `HEAD` only. The write
  verbs are absent rather than present-and-blocked: adding `POST` should
  be the change that requires review, and an enum member nothing can
  execute is an empty abstraction.
- **No query interpolation.** `search`'s `query` never reaches a URL.
  Letting a question's text into an outbound request hands an end user
  partial control of it — path traversal, a swapped host, a credential
  leaked into a query string. Endpoints are fixed; filtering happens on
  what comes back.
- **No OpenAPI spec parsing.** Endpoints are declared directly. Ingesting
  a spec means trusting a remote document to define what this server may
  call, which needs its own trust story.
- **No endpoint-creation API route.** Endpoints are configuration only.

---

## 10. Next





Batches 1, 3 and 5 are done. The remaining credential-free work is
**Batch 8** (a real MCP client, administrator-controlled trust only), and
two items worth their own decision: wiring `rest/` into the orchestrator so
it becomes routable, and consolidating the three SSRF blocklist copies
(§9.1).

**Batch 2** — adapting the existing sources to the lifecycle half of the
contract — is now better motivated than before: `rest/` is a second real
implementer to generalize against, which was the stated reason for
deferring it.

**Batch 4/6/7** (SharePoint/OneDrive, Dataverse, Outlook) stay blocked on
an Entra tenant and admin consent. Teams is not scheduled at all — see
[`data-source-matrix.md`](data-source-matrix.md) §3.4.
