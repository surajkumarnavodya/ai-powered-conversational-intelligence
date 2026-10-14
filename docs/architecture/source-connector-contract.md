# Source connector contract — Batch 1

**Scope.** The design of `sources/` and `GET /sources`: what the contract
is, what it deliberately is not, and which invariants it carries. This
document owns the **connector contract's design and rationale**.

Status and priority of each source live in
[`feature-inventory.md`](feature-inventory.md); per-source-type
capabilities and auth in [`data-source-matrix.md`](data-source-matrix.md);
the batch plan in
[`implementation-roadmap.md`](implementation-roadmap.md).

**Date:** 2026-10-14. **Batch:** 1 of the source-connectivity roadmap.

---

## 1. What this batch did

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
it and `tests/test_sources_conformance.py` runs 27 checks against it. A
Protocol alone cannot be wrong; an implementation plus a suite that
rejects a non-conforming connector can be.

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

## 4. How "behaviour is unchanged" is actually proven

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

## 5. Invariants this batch carries

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

## 6. What this batch deliberately did not do

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

---

## 8. Next

**Batch 2** adapts the existing sources to the lifecycle half of the
contract, behaviour-preserving. **Batch 3** adds per-conversation source
selection, which is what `GET /sources` was built to feed — and which
carries the audit's one High security item: a client-supplied source list
must be a filter over RBAC, never a grant.
