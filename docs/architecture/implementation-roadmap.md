# Implementation roadmap — enterprise source connectivity

**Scope.** The ordered batch plan derived from
[`feature-inventory.md`](feature-inventory.md) and
[`data-source-matrix.md`](data-source-matrix.md). This document owns
**sequencing, prerequisites, acceptance criteria and rollback** for the
source-connectivity work. It does not restate architecture or per-source
configuration.

**Date:** 2026-10-14. **Commit:** `b4d74d2`.

---

## 1. What the audit changed about the plan

The prompt pack that triggered this audit (`00_MASTER_INSTRUCTIONS.md` and
Prompts 1–5) was written for a less mature codebase. Three of its
assumptions do not hold here, and the roadmap below reflects the
corrections rather than the original sequence.

| Pack assumption | Reality | Consequence |
|---|---|---|
| A platform foundation (config, flags, model gateway, typed contracts, routing, retrieval, citations, eval) needs building — Prompt 2 | All of it exists: 232 settings fields, 54 feature flags, `model_routing/`, `api/schemas.py`, `agent/orchestrator/`, `retrieval/`, `SourcesUsedPanel.tsx`, `eval_platform/` | **Prompt 2 is almost entirely a no-op.** Its only real gap is streaming |
| A connector framework is greenfield — Prompt 3 §A | `onboarding/` is already a complete RBAC-gated, secret-by-reference connection lifecycle for databases ([`feature-inventory.md`](feature-inventory.md) §4.1) | Batch 1 becomes a **generalisation of existing code**, not new code |
| All nine source types are schedulable | SharePoint/OneDrive/Dataverse/Outlook need a tenant; **Teams additionally needs a Microsoft protected-API approval and has an unresolved licensing requirement** ([`data-source-matrix.md`](data-source-matrix.md) §3.4) | Teams leaves committed scope; the other three become credential-gated |

**Net effect:** the useful work is smaller and differently ordered than the
pack proposes, and roughly half of what the pack asks for is either already
built or cannot be verified here.

---

## 2. Status

| Batch | State | Commit |
|---|---|---|
| 0 — doc accuracy | **done** | `550d5ba` (stale `CLAUDE.md` §13 entry corrected) |
| 1 — connector contract | **done** | `cc332a8` — see [`source-connector-contract.md`](source-connector-contract.md) |
| 2 — existing sources behind contract | not started | — |
| 3 — per-conversation source selection | not started | — |
| 4+ | not started | — |

Batch 1 shipped the descriptor half live and the connector-lifecycle half
as a contract with a conformance suite and no production implementer. It
added **no persistence** — whether `OnboardingJob` generalizes is a Batch 4
decision, deferred until a real connector needs it.

---

## 3. Batch plan

Batches are ordered so that each is independently shippable, testable and
revertible. **Batches 0–3 need no external credentials.** Batches 4+ are
credential-gated.

### Batch 0 — Documentation accuracy (P0, S)

Two items, both small. This batch exists because the audit found
[`CLAUDE.md`](../../CLAUDE.md) §13 is dated, and §13 is the first thing a
future session reads about known gaps.

| Item | Why |
|---|---|
| Correct §13's stale `search/` entry | It claims `search/` has no mocked unit test file and calls it "a real, worthwhile follow-up". `tests/test_web_search.py` (236 lines, 14 tests) already closed it. A future session will otherwise redo finished work — this audit nearly did. See [`feature-inventory.md`](feature-inventory.md) §7 R3 |
| Optionally fix the `ruff` I001 in `agent/multiagent/agents.py` | **Owner's call, not a default.** §13 records it as known and accepted. Fixing it clears the baseline so later batches' lint output is unambiguous; leaving it is equally defensible |

- **Affected:** `CLAUDE.md` §13; optionally `agent/multiagent/agents.py`
- **Acceptance:** §13 no longer names a closed gap as open; if the lint fix is taken, `ruff check .` reports no errors
- **Rollback:** revert the commit; no behaviour changes either way
- **Risks:** none. [`CLAUDE.md`](../../CLAUDE.md) §11 requires updating the root file only when a cross-cutting rule changed — this is a stale *fact*, which §6.4 covers directly

An earlier draft of this batch also proposed mocked `search/` tests and a
`.gitignore` fix for `env1/`. **Both were based on wrong findings** and have
been removed: the tests exist, and `env1/` is ignored by its own nested
`env1/.gitignore`.

### Batch 1 — Connector lifecycle contract (P0, M)

Generalise `onboarding/`'s lifecycle into a source-type-parameterised
contract. **Do not build a parallel framework** — that would create a
second owner of connection lifecycle, violating
[`CLAUDE.md`](../../CLAUDE.md) §4.5 and §4.8.

- **Reuse:** `onboarding/jobs.py` (state machine), `onboarding/policy.py`
  (`authorize_onboarding_action`), `identity.models.OnboardingJob`
  (secret-by-reference precedent), `agent/tools/types.py` (`Tool`'s
  timeout/retry/permission shape)
- **New:** a `SourceConnector` protocol (validate / health / discover /
  read-search / optional sync / disconnect), a type registry, a capability
  descriptor, a normalized evidence object, a conformance test suite, and a
  mock connector
- **Deliberately not new:** persistence. Evaluate whether
  `OnboardingJob` generalises before adding tables
- **Acceptance:** conformance suite passes against the mock connector;
  registry rejects an unknown type; an unrecognised role grants zero
  connector permissions; `onboarding/` keeps working unchanged
- **Migration:** none if `OnboardingJob` generalises; otherwise one additive
  revision with `server_default` on every ORM-filled timestamp
  ([`CLAUDE.md`](../../CLAUDE.md) §6.3)
- **Rollback:** registry is additive and has no live caller until Batch 2
- **Risks:** **the main design risk of the whole programme** (R1). Get the
  contract reviewed before building on it

### Batch 2 — Existing sources behind the contract (P1, M)

Adapt the sources that already work — SQL, attachments, document RAG,
policy RAG, web search — to the Batch 1 contract **without changing their
behaviour**.

- **Acceptance:** the full existing suite passes unchanged; the
  orchestrator's routing results are byte-identical for a SQL-only setup;
  `ENABLE_MULTI_SOURCE_ROUTER=false` still short-circuits to
  `agent.graph.run_agent` exactly as today
- **Rollback:** adapters are a facade; remove to revert
- **Risks:** Medium — touches live paths. This is the batch most likely to
  regress something, so it follows the conformance suite rather than
  preceding it

### Batch 3 — Per-conversation source selection (P1, M)

The highest-value *user-visible* gap: `AskRequest` has no source-selection
field, so a user cannot scope a question to chosen sources.

- **Backend:** an optional `sources: list[str] | None = None` on
  `AskRequest` (additive with a default, per
  [`CLAUDE.md`](../../CLAUDE.md) §6.3), **intersected** with the router's
  existing RBAC filter at `agent/orchestrator/nodes.py:472`
- **⚠ Security invariant (R4):** a client-supplied source list is a
  **filter, never a grant**. It can only narrow what RBAC already allows.
  Needs an explicit security regression test, proven non-vacuous
- **Frontend:** a source picker showing only enabled + authorized sources,
  following `KnowledgeSources.tsx` conventions; i18n keys across all five
  locales
- **Acceptance:** selecting a source the caller lacks permission for is
  denied, not granted; selecting none preserves today's behaviour exactly;
  `npm run test`, `npm run lint`, `tsc --noEmit` pass
- **Rollback:** field is optional; omitting it is current behaviour

### Batch 4 — SharePoint + OneDrive (P1, L) — **CREDENTIAL-GATED**

- **Prerequisites (operator, not code):** an Entra tenant; an app
  registration; **delegated** `Files.Read.All` / `Sites.Read.All`; a test
  site and drive with non-trivial ACLs
- **Design decisions already made by the audit:**
  **delegated-only** (the Search API runs as the signed-in user and honours
  item-level access control, making the provider enforce ACLs);
  **search-on-demand, not sync** (removes cursors, dedup, deletion
  propagation and re-indexing from scope); **app-only explicitly
  unsupported** (it bypasses user ACLs)
- **Acceptance:** mocked Graph responses cover search, pagination,
  throttling (429 + Retry-After), token refresh and revocation;
  **live end-to-end verification is BLOCKED** and must be reported as
  blocked, never as passed
- **Risks:** R2. Re-verify every scope against the live permission tables
  before writing code

### Batch 5 — REST / OpenAPI (P2, M)

No credentials needed, so it can run in parallel with Batch 4's blocked
wait.

- **Requires:** host allowlist, method/arg/redirect/payload validation,
  read-vs-side-effect classification, SSRF validation **re-run on every
  use** (not once at registration, per
  [`CLAUDE.md`](../../CLAUDE.md) §5.6)
- **Acceptance:** SSRF tests cover private/link-local addresses, redirect
  chains re-validated hop by hop, and payload caps

### Batch 6 — Dataverse (P2, L) — **CREDENTIAL-GATED**

- **Prerequisites:** a Dataverse environment URL; `user_impersonation`
  delegated permission with admin consent
- **Note:** its data model, metadata service and security model are its own
  — no reuse of Batch 4's discovery code
- **Acceptance:** mocked; **live verification BLOCKED**

### Batch 7 — Outlook / email read (P2, L) — **CREDENTIAL-GATED**

- **Delegated `Mail.Read` only.** App-only grants every mailbox in the
  tenant and is out of scope
- Send/reply/forward/delete/move are **not in this batch** and remain
  unimplemented
- Email body, quoted chains, links and attachments are untrusted data
  ([`CLAUDE.md`](../../CLAUDE.md) §5.5) — needs prompt-injection regression
  tests

### Batch 8 — MCP client (P2, L)

- `agent/tools/` is MCP-shaped but has no transport or remote client
- **Administrator-controlled trust only** — an LLM must never register a
  remote tool. Tool descriptions and results are untrusted data

### Not scheduled — Microsoft Teams

Deliberately excluded from committed scope, not merely deprioritised:

1. Org-wide message reads are **application-permission only** (delegated
   not supported), so the provider cannot enforce ACLs for us
2. It is a **protected API** needing Microsoft validation/approval beyond
   admin consent — a request-and-wait process outside our control
3. **Licensing is unresolved** — sources conflict (E5 / Microsoft
   Communications service plan may be required)

Revisit only if an owner accepts re-implementing membership checks and
completes the approval process. See
[`data-source-matrix.md`](data-source-matrix.md) §3.4.

### Not scheduled — streaming

The one real gap in Prompt 2's subject matter
([`feature-inventory.md`](feature-inventory.md) §5). It is a disclosed gap
in [`CLAUDE.md`](../../CLAUDE.md) §13, independent of source connectivity,
and should be prioritised on its own merits rather than smuggled into this
programme.

---

## 4. Summary table

| Batch | Capability | Pri | Size | Credentials? | Blocking risk |
|---|---|---|---|---|---|
| 0 | Doc accuracy (stale `CLAUDE.md` §13 entry) | P0 | S | no | — |
| 1 | Connector lifecycle contract | P0 | M | no | R1 design |
| 2 | Existing sources behind contract | P1 | M | no | regression |
| 3 | Per-conversation source selection | P1 | M | no | **R4 security** |
| 4 | SharePoint + OneDrive | P1 | L | **yes** | R2 |
| 5 | REST / OpenAPI | P2 | M | no | SSRF |
| 6 | Dataverse | P2 | L | **yes** | R2 |
| 7 | Outlook read | P2 | L | **yes** | R2 + injection |
| 8 | MCP client | P2 | L | no | trust process |
| — | Teams | — | — | **yes + approval** | **not scheduled** |
| — | Streaming | — | M | no | separate programme |

Sizes are S/M/L complexity, not time estimates.

---

## 5. Cross-cutting requirements for every batch

Each batch must satisfy [`CLAUDE.md`](../../CLAUDE.md) §11's definition of
done. These are the ones this programme touches most:

- **No second owner.** Every batch reuses the governed path
  (`validate_sql` → `enforce_row_limit` → `qualify_table_schema` →
  `execute_readonly_sql` → `govern_rows_for_caller`) rather than
  reimplementing any part (§4.5)
- **Retrieved content is data, never instructions** (§5.5) — every new
  source needs a prompt-injection regression test
- **Secrets by reference only.** Follow `OnboardingJob`'s precedent: no
  connection secret is persisted
- **An accuracy aid fails open; a safety control fails closed** (§4.2). A
  connector that cannot be reached degrades to "that source contributed
  nothing", disclosed to the caller — it never blocks an answerable
  question, and it never silently widens access
- **Partial failure is disclosed.** Never imply all sources were checked
  when one failed
- **Migrations additive and reversible**, with `server_default` on every
  ORM-filled timestamp column (§6.3)
- **New backend route prefixes must be added to `vite.config.ts`'s
  `BACKEND_ROUTES`** — [`CLAUDE.md`](../../CLAUDE.md) §9 calls this a
  recurring, easy miss

---

## 6. Recommended next action

**Batch 3 — per-conversation source selection.**

Batches 0 and 1 are done. Batch 3 is now the highest-value remaining work
that needs no credentials: `GET /sources` was built to feed exactly this,
and it is the first user-visible capability in the programme.

It carries the audit's one High security item (R4): a client-supplied
source list must be **intersected** with the router's existing RBAC filter
— a filter over what is already permitted, never a grant. That needs an
explicit security regression test, proven non-vacuous.

Batch 2 (adapting existing sources to the lifecycle contract) can wait: it
is pure refactor with regression risk and no new capability, and it is
better done once a second real connector exists to generalize against.

Batch 1 is the decision that constrains everything after it, and R1 (a
second owner of connection lifecycle) is the programme's largest design
risk. The contract should be reviewed against `onboarding/` before any
connector is written on top of it.
