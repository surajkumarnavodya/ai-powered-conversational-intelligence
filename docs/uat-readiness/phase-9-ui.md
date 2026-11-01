# Phase 9 — UI / UX audit (React frontend)

**Branch:** `uat-readiness/phase-9-ui`
**Measured:** 2026-10-29 (first pass) and 2026-10-30 (re-measurement after fixes)
**Method:** real Chrome 1440×1000 and 360×780, driven over the DevTools
Protocol, against the live application (`uvicorn api.main:app`) with four
real SQL Server databases, Ollama, and seven throwaway role accounts.

> **This report does not mark any UAT journey as passed.** Only business
> users executing the script can do that. What is recorded here is whether
> each journey is *walkable* for each role, and what a user sees when it is
> not.

---

## 1. Scope and what was actually exercised

| Audit item | How it was checked | Outcome |
|---|---|---|
| 15 journeys × 7 roles in a browser | role × screen grid, client-side navigation, 12 screens | §3 — grid, 2 defects fixed |
| Async view states (loading/empty/error/partial/success) | live runs incl. a forced model failure | §4 — good; 1 defect fixed, 1 flagged |
| Streaming progress on slow answers | SSE frame capture + badge sampling at 600 ms | §5 — **`UI-004` fixed and verified**, `UI-010` flagged |
| SQL review & Confirm-and-Run | full J1 walk, SQL shown → edited → executed | §6 — works |
| Masked values marked `[restricted]` | `POST /execute` as admin vs user on real rows | §7 — works; **`UI-012` fixed** |
| Charts: axis, number format, dates | code + live chart build | §8 — good; `UI-014` flagged |
| Role-based nav vs API (cross-check Phase 4) | nav verdict vs 16 real routes × 7 roles | §3.3 — **0 mismatches** |
| Accessibility WCAG 2.2 AA, every theme × accent | axe-core in real Chrome, contrast **enabled**, 38 runs | §9 — 4 rules fixed to zero, contrast flagged |
| Responsiveness 360 px → wide | 360×780 viewport, overflow measured | §10 — no horizontal scroll |
| PWA / offline after logout | network emulation offline post-sign-out | §11 — no stale data; 1 minor |
| Security in UI (markdown, storage, attachments) | injection tests + storage dump | §12 — clean; **test gap closed** |
| i18n completeness, RTL | locale key parity, hardcoded-string sweep | §13 |
| Voice / attachments / image editor failure paths | code + live mic-denied path | §14 — **`UI-013` flagged (S2)** |
| Bundle size and performance | `vite build`, measured answer latency | §15 |

### 1.1 Method limitations — read these before trusting a cell

1. **Radix dropdowns do not open from a programmatic `element.click()`.**
   They listen on `pointerdown`. The first pass therefore recorded the
   composer model picker, the account menu and the screens menu as
   "no items". Re-driven with real CDP `Input.dispatchMouseEvent` the
   account menu opens correctly (`["Settings","Sign out"]`) and sign-out
   works. **The screens menu was still not opened** by either technique
   (the trigger was not located by label); it is covered by the role grid
   and by `App.routes.test.tsx` instead, not by a browser screenshot.
2. **`.env` names `OLLAMA_MODEL=qwen2.5-coder:7b`, which is not installed
   on this machine** (`OPS-008`, open from Phase 5). Every question on the
   default model fails in ~1.7 s. Journeys that need an answer were run
   with `llama3.1:8b` pinned. This is a real UAT blocker, not an artefact
   of the harness — see §4.2.
3. **`hr` is schema-rich and data-empty** — `SELECT TOP 5 * FROM Employee`
   returns 0 rows. Masking had to be measured against `adventureworks`
   instead. A UAT journey that reads HR data cannot be walked on this
   seed; that belongs to Phase 11.
4. **The first pass's role grid was contaminated and has been discarded.**
   It navigated with a full page load per screen — 12 screens × 7 roles =
   84 loads, each spending one `POST /auth/refresh` against
   `LOGIN_RATE_LIMIT_PER_MINUTE=10` keyed by client IP. It rate-limited
   itself, and because of `UI-002` that silently signed the driver out
   mid-run: `R1-platform_admin.png` and `R1-tenant_admin.png` are
   **byte-identical to `01-signin.png`** (same MD5, 22,689 bytes)
   because they *are* the sign-in page. The grid in §3 is the 2026-10-30 re-run, which navigates
   client-side and spends one refresh per role.
5. **One un-run item.** No Lighthouse run: the Lighthouse CLI is not
   installed here and installing a toolchain was out of scope. Bundle
   sizes and real answer latency are measured instead (§15); a human must
   run Lighthouse for the composite score. Recorded **UNVERIFIED**, not
   passed.

---

## 2. Findings summary

| ID | Sev | Title | Status |
|---|---|---|---|
| `UI-002` | S2 | A rate-limited `/auth/refresh` signed the user out of a valid session | **Fixed** |
| `UI-003` | S3 | An unmatched in-app URL rendered a blank white page for every role | **Fixed** |
| `UI-004` | S2 | `POST /ask/stream` emitted **zero** progress events to a browser | **Fixed** |
| `UI-005` | S3 | 16×16 tap targets on both auth screens (WCAG 2.5.8) | **Fixed** |
| `UI-006` | S3 | Three header controls' accessible name excluded their visible text (WCAG 2.5.3) | **Fixed** |
| `UI-007` | S2 | Nine `<select>` and one file input had no accessible name at all | **Fixed** |
| `UI-008` | S3 | 72 sub-AA colour-contrast pairings; the jsdom guard reasoned its way past the failing class | **Guard fixed, palette flagged** |
| `UI-009` | S3 | The primary-navigation landmark was **0 px wide** at ≤414 px | **Fixed** |
| `UI-010` | S3 | The progress badge names the node that *finished*, not the one running | **Flagged** |
| `UI-011` | S4 | Masked cells are marked but never explained | **Flagged** |
| `UI-012` | S2 | `YearlyIncome` returned in the clear while `AnnualIncome` masks | **Fixed** |
| `UI-013` | S2 | The cloud-backed live-caption exception is disclosed in a docstring, never to the user | **Flagged** |
| `UI-014` | S3 | Chart currency is hardcoded `USD` with no setting, in a five-locale app | **Flagged** |
| `UI-015` | S4 | Offline is reported as "we couldn't check your access" | **Flagged** |
| `UI-016` | S4 | `Markdown` — the component that renders untrusted text — had no test | **Fixed** |

Fixed: 9. Flagged for an owner decision: 6. **No S1.**

Two rows in the register are not in the table above. `UI-001` (S4, a
hardcoded empty-result string in `AnalyticsSummary.tsx`) was raised by
Phase 5 and is **still open** — see §13 for why it travels with the
`DOC-010` i18n pass rather than being done in isolation here. `UI-017`
is a positive finding recording what was verified clean: the nav-vs-API
cross-check, browser storage, sign-out on both sides, and 360 px
layout.

---

## 3. Journey × role grid

Seven accounts, created additively and idempotently
(`scratchpad/seed_uat_roles.py`), one per UAT role:

| | Account | Roles as the server resolves them |
|---|---|---|
| R1 | `uat9.r1@example.com` | `user`, `admin`, `platform_admin` |
| R1b | `uat9.r1b@example.com` | `platform_admin` only |
| R2 | `uat9.r2@example.com` | `user`, `admin` |
| R3 | `uat9.r3@example.com` | `user`, `manager` |
| R4 | `uat9.r4@example.com` | `user`, `analyst` |
| R5 | `uat9.r5@example.com` | `user` |
| R6 | `uat9.r6@example.com` | `viewer` |

### 3.1 What each role can open (measured 2026-10-30, client-side navigation)

`ok` = the screen rendered its own content. `refused` = the server's
`RequireNavItem` gate showed "You don't have access to this screen".
`404` = the new not-found page.

| Screen | R1 | R1b | R2 | R3 | R4 | R5 | R6 |
|---|---|---|---|---|---|---|---|
| Chat (`/`) | ok | **refused** | ok | ok | ok | ok | ok |
| Knowledge Sources | ok | **refused** | ok | ok | ok | ok | ok |
| Media Search | ok | **refused** | ok | ok | ok | ok | ok |
| Data Lineage | ok | **refused** | ok | ok | ok | ok | ok |
| Decision Center | ok | ok | ok | ok | refused | refused | refused |
| DB Onboarding | ok | ok | ok | ok | ok | refused | refused |
| Recommended Actions | ok | ok | ok | ok | ok | refused | refused |
| Semantic Review | ok | ok | ok | ok | ok | refused | refused |
| AI Governance | ok | ok | ok | ok | ok | refused | refused |
| Tenant Admin | ok | ok | ok | ok | refused | refused | refused |
| Platform Admin | ok | ok | refused | refused | refused | refused | refused |
| Unmatched URL | 404 | 404 | 404 | 404 | 404 | 404 | 404 |

`GET /navigation` item counts agree exactly: R1 11, R1b 7, R2 10, R3 10,
R4 8, R5 4, R6 4.

### 3.2 The R1b row is `AUZ-004` made visible

**A `platform_admin`-only account cannot open the chat screen** — the core
product — nor Knowledge Sources, Media Search or Data Lineage. Phase 4
flagged the cause (`AUZ-004`): `platform_admin` is absent from
`agent.authz.ROLE_PERMISSIONS`, so it grants nothing on that surface. It
is fail-closed and therefore not a security defect, but for a UAT
administrator it reads as a broken account: they are the highest-privilege
role and are refused the product's main screen. **No change made here** —
the fix is Phase 4's to own, and it is a role-mapping decision. Phase 9
adds only the user-visible evidence.

### 3.3 Nav gate vs API — the Phase 4 cross-check passes

For each role, every screen the nav gate refused was checked against the
real API routes behind it (16 routes, resolved from `/openapi.json`, not
guessed):

| Route | R1 | R1b | R2 | R3 | R4 | R5 | R6 |
|---|---|---|---|---|---|---|---|
| `/decision-center/briefing` | 200 | 200 | 200 | 200 | 403 | 403 | 403 |
| `/onboarding/jobs` | 200 | 200 | 200 | 200 | 200 | 403 | 403 |
| `/recommendations` | 200 | 200 | 200 | 200 | 200 | 403 | 403 |
| `/semantic-catalog/entries` | 200 | 200 | 200 | 200 | 200 | 403 | 403 |
| `/governance/inventory` | 200 | 200 | 200 | 200 | 200 | 403 | 403 |
| `/tenant-admin/users` | 200 | 200 | 200 | 200 | 403 | 403 | 403 |
| `/tenant-admin/roles` | 200 | 200 | 200 | 200 | 403 | 403 | 403 |
| `/tenant-admin/audit` | 200 | 200 | 200 | 200 | 403 | 403 | 403 |
| `/platform-admin/users` | 200 | 200 | 403 | 403 | 403 | 403 | 403 |
| `/platform-admin/tenants` | 200 | 200 | 403 | 403 | 403 | 403 | 403 |
| `/platform-admin/security-events` | 200 | 200 | 403 | 403 | 403 | 403 | 403 |
| `/platform-admin/audit-logs` | 200 | 200 | 403 | 403 | 403 | 403 | 403 |
| `/documents` | 200 | 403 | 200 | 200 | 200 | 200 | 200 |

**Mismatches (screen hidden, route open): zero, for all seven roles.**
Direct URL access to a hidden page is refused by the API, not only by the
nav.

Two cells are **uninformative and must not be read as passes**:
`/semantic-intelligence/findings` and `/governance/assets/current` return
**422 for every role**, including anonymous — a missing required query
parameter is rejected before any in-handler authorization runs. Phase 4
recorded this exact trap; it is reproduced here rather than papered over.

### 3.4 A trap that cost one void measurement

An earlier probe recorded `/admin/users` and `/platform/tenants` as
**200 for a `viewer`**, which looked like an S1. They are not routes.
`api/main.py` serves the dashboard from a catch-all
`GET /{full_path:path}`, so both returned 200 with `<!doctype html>`.
CLAUDE.md §5.3 already records this; the probe now classifies any
HTML-bodied 200 as `SPA-CATCHALL` and resolves every path against
`/openapi.json` first. **On this app a 200 is never on its own evidence
that a route authorized you.**

---

## 4. Async view states

### 4.1 What works

`RequireNavItem` has explicit loading ("Checking your access…"), error
("We couldn't check your access to this screen." + **Try again**) and
forbidden states, and the forbidden page moves focus to its heading and
links to the first screen the caller *can* open. The chat turn has a
pending state, a per-stage lifecycle strip, a done state with measured
duration, and an honest failure state. Empty conversation renders "No
conversations yet. Start by asking a question." No blank screens were
observed on any of the 84 role×screen cells after `UI-003`.

### 4.2 `OPS-008` is what a UAT user hits first, and the message is good

With the shipped `.env`, the very first question fails. What the user sees
(measured, `05-uninstalled-default-model-failure.png`):

```
Answered in 1.7s

Agent could not produce a working query: The LLM call itself failed on
attempt 1: Ollama call to model 'qwen2.5-coder:7b' at
http://localhost:11434 failed: the model is not installed on that server.
Pull it with `ollama pull qwen2.5-coder:7b`, or pick a model that is
already installed (`ollama list`).

Understanding : completed     Schema : completed      SQL : failed
Validation : not started      Cost check : not started …
```

This is honest, specific and actionable, and the lifecycle strip correctly
shows where it stopped. Two observations for UAT rather than defects:
the next step given (`ollama pull`) is an operator action shown to a
business user, and `GET /models` already reports
`{"id":"qwen2.5-coder:7b","installed":false,"is_default":true}` — the
server knows the default is unusable at startup and still serves it as the
default. Both belong to `OPS-008`, which is already open.

### 4.3 `UI-002` — a rate limit used to end a valid session (**S2, fixed**)

`POST /auth/refresh` shares `login_rate_limit_per_minute` (default **10
per minute, keyed by client IP**) and one full page load spends one
refresh. Eleven direct URL entries in ~40 s returned 429, and both refresh
call sites treated *any* rejection as "not signed in" — so a live session
was discarded and the sign-in form appeared, with no message, while the
`HttpOnly` refresh cookie was still valid. The same shape hit
`handleUnauthorized`, where it is worse: an access token expires every
`access_token_expire_minutes` (default 15), so an ordinary session
refreshes repeatedly and a rate-limited moment would sign the user out
mid-question.

Fixed by `localAuthStore.isSessionEndedError` — a **narrow allowlist**
(401, 404) rather than a blocklist of transient statuses, so a status it
has never heard of cannot end a session. `initialize` now reports a new
`"unavailable"` status and `AuthGate` renders a retry panel for it;
`handleUnauthorized` leaves the session exactly as it was, which is what
makes a rate-limit window recover by itself.
Tests: `localAuthStore.sessionSurvival.test.ts` (18).

---

## 5. Streaming progress

### 5.1 `UI-004` — the documented per-node progress had never reached a browser (**S2, fixed and verified**)

`api/streaming.py` binds `agent.progress`'s sink with a `ContextVar` on
its own worker thread, and its docstring said that was "the thread the
graph actually runs on". That was true when written and false now:
`api.main.ask` hands `run_orchestrated` to a *second*, bounded
`ThreadPoolExecutor` for admission control, and `ThreadPoolExecutor.submit`
gives its worker a **fresh, empty** context. So `emit_progress` read `None`
for the sink on every real request and returned early.

Measured 2026-10-29 against the live app: a 58-second question produced
`open`, three keepalive comments, `result` and `done` — and **zero**
`progress` events, with `done` reporting `{"dropped": 0}`, so they were
never emitted rather than dropped under backpressure. In the browser the
consequence was 123 seconds of "The assistant is thinking about your
question." with no stage ever named.

Fixed by submitting inside `contextvars.copy_context()` — a fresh copy per
submit, so a `ContextVar` the graph sets cannot leak back to the caller or
sideways into another request.

**Verified end to end, 2026-10-30.** The server now emits 20 progress
events for one question:

```
+ 0.3s classify_followup      + 1.6s retrieve_schema       + 1.8s retrieve_golden_examples
+ 2.1s retrieve_business_context  + 2.1s classify_analytical_intent
+ 2.1s build_analytical_plan   + 2.1s plan_query            +13.6s generate_sql
+13.6s review_sql             +13.8s enforce_display_names  +13.8s validate_sql
+13.8s estimate_cost          +13.8s execute_sql            +13.8s compute_analytics
+13.8s generate_recommendations +18.2s generate_insight
event counts: {'open': 1, 'progress': 20, 'result': 1, 'done': 1}
```

and the browser badge renders them
(`04-streaming-progress-stage-in-badge.png`):

```
Thinking 3s…· retrieve schema
Thinking 4s…· plan query
…
Thinking 37s…· plan query
DISTINCT STAGES NAMED: ["classify followup","retrieve schema","plan query"]
```

Tests: `tests/test_progress_crosses_the_ask_executor.py` (6), which keep
the real `ask` path and stub only `run_orchestrated` so the sink has to
cross the executor. The pre-existing
`test_api_streaming.py::test_progress_events_are_forwarded` could not
catch this: it replaces `api.main.ask` wholesale and emits from the stub,
which runs on the *streaming* worker thread where the sink is bound. **The
thread boundary was the whole bug, and mocking `ask` removes it.**

### 5.2 `UI-010` — the badge names the node that just finished (**S3, flagged**)

`agent/nodes.py` calls `emit_progress` *after* the node returns, with its
measured `duration_ms`. Every event is therefore a **completion** event,
and the badge shows the last completed node — not the running one.

Measured on the same run: the badge read **"plan query" from +4 s to
+101 s** — 97 seconds — while the node actually running was
`generate_sql` (`duration_ms: 96807.11`). `plan_query` itself took
0.05 ms. Keepalive comments arrived every ~15.7 s throughout, so the
connection was alive and the UI had something it could have said.

This is the failure the badge exists to prevent: the longest wait in the
run is labelled with the name of a node that took a twentieth of a
millisecond. Two docstrings state the opposite and are now corrected in
this branch: `TimingBadge.tsx` ("names the graph node currently running")
and `chatStore.ts:58` ("The graph node currently running").

**Not fixed — two options, both needing a decision:**

- **(a) Emit a `started` event per node.** Honest and simple to read, but
  it changes the SSE event stream's shape, which is a contract
  (`api/streaming.py` + every client), and doubles the event count.
- **(b) Let the client name the next stage.** `QueryLifecycle.tsx`
  already holds the node order, so the client could show "the stage after
  the last completed one". This is a **guess**, and `TimingBadge`'s own
  comment says it renders a stage only when the server reported one
  precisely so nothing guessed is shown.

Recommendation: (a), scoped to a new event name so old clients ignore it.

---

## 6. SQL review and Confirm-and-Run

Walked fully as R5 (`user`) with `llama3.1:8b`:

- SQL is shown **before** execution, in an editor, with the cost notice
  beside it ("Cost check").
- Editing is permitted and the edited text is what is posted; `POST
  /execute` re-validates it server-side on every call (Phase 2's
  territory, unchanged here).
- Confirm-and-Run returned 5 rows with readable headers
  (`Product`, `Total Sales`) derived from display names, a "Visualize"
  affordance, CSV export and pagination.
- Wide/long data: the results table paginates and scrolls horizontally;
  no overflow of the page itself was observed.

One note carried from §3.3: `GET /navigation` reports
`capabilities: {ask: true}` for a `viewer` — **no `execute_sql`** — and
`execute_sql` is consumed **nowhere in the frontend** (only `useCapabilities`
consumers are the five admin pages). A viewer is therefore shown the
Confirm-and-Run button and gets the server's 403 on click, which
`chatStore.confirmAndRun` catches and renders via `withConfirmedError`. It
degrades legibly rather than crashing, so this is not raised as its own
finding, but "users only see what they may use" is not strictly true for
this one control.

---

## 7. Masked values

### 7.1 `[restricted]` is applied and clearly marked

`POST /execute` on `adventureworks.DimCustomer`, same SQL, two roles:

| | R1 (`admin`, holds `VIEW_RESTRICTED_COLUMNS`) | R5 (`user`) |
|---|---|---|
| `FirstName` | `Jon` | `[restricted]` |
| `LastName` | `Yang` | `[restricted]` |
| `EmailAddress` | `jon24@adventure-works.com` | `[restricted]` |
| `Phone` | `1 (11) 500 555-0162` | `[restricted]` |
| `YearlyIncome` | `90000.0` | `[restricted]` *(after `UI-012`)* |
| `EnglishEducation` | `Bachelors` | `Bachelors` |

### 7.2 `UI-012` — `YearlyIncome` was returned in the clear (**S2, fixed**)

Before the fix the same request returned **20** masked cells and
`YearlyIncome = 90000.0` beside them. `governance/classification.py`'s
compensation pattern carried `annual[-_]?income` and **not**
`yearly[-_]?income`, so the identical column under the other common
spelling escaped classification entirely — and `config/sensitive_columns.yaml`
ships as an empty template, so name-pattern inference is all there is.

Fixed by adding `yearly[-_]?income` to that pattern. **Deliberately not a
bare `income`**: `net_income`, `operating_income` and `income_statement`
are ordinary company financials and CLAUDE.md §5.8 names over-blocking
normal analytics as its own failure mode. A bare-`income` rule is a policy
decision, not a defect fix, and is flagged rather than taken.

Verified live: 20 → **25** masked cells, R1 unaffected. Tests:
`tests/security/test_governance_cannot_be_bypassed.py` —
`test_yearly_income_masks_the_same_as_annual_income` (both spellings, both
casings) and `test_company_income_lines_are_not_over_blocked` (the other
direction, so a later widening fails and starts the conversation).
Mutation-verified: neutralising the pattern fails 1 of 54.

### 7.3 `UI-011` — marked but unexplained (**S4, flagged**)

A reader sees `[restricted]` in four columns and is told nothing about
why. `ResultsTable.tsx` gives masked cells no tooltip, legend or distinct
styling, and `AskResponse.restricted_field_notice` is **not** the place to
look — that field is set only when a question was *refused* because its
SQL kept referencing a restricted column, so `None` on a successfully
masked result is correct behaviour, not a bug.

Proposed: a one-line note under the table whenever any cell equals the
mask — "Some values are hidden because your role may not view them." —
behind a new i18n key in all five locales. Not taken because it is new
user-visible copy.

---

## 8. Charts

Verified good, and the design is coherent rather than accidental:

- **Axis binding** is driven by `column_semantics` (measure vs key), so a
  surrogate key that happens to be numeric is not plotted as a measure —
  `chartEngine.ts:685` names exactly that case.
- **Chart numbers are formatted** for reading: `formatNumber` applies
  `Intl.NumberFormat` with `style: 'currency'`, `style: 'percent'`
  (`maximumFractionDigits: 1`), or plain with `maximumFractionDigits: 2`.
- **Result-table numbers are deliberately *not* rounded.**
  `columnFormatting.ts` inserts locale thousands separators into the
  integer part through `BigInt` and leaves the fractional characters
  byte-for-byte untouched, because a SQL Server `decimal(38,10)` arrives
  as a string precisely so it is not rounded through a float64. This is
  why the J1 result shows `3,105,726.6592`. Exactness in the table,
  readability in the chart, is the right split and is documented in both
  modules.

### 8.1 `UI-014` — chart currency is hardcoded USD (**S3, flagged**)

`chartEngine.ts:1045`:

```ts
return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(value)
```

`USD` is the only currency literal in the repository, and there is **no
currency setting** in `config/settings.py` or `.env.example`. The app
ships five locales including `hi` and `mr`, the database is
user-configured, and `ChartCustomizePanel` lets a user *choose* "Currency"
for any numeric column — which then renders `$` over data that may be in
any currency. This mislabels magnitude-bearing data, which is why it is S3
rather than cosmetic.

Not fixed because the right scope is a product decision — per deployment,
per database, per tenant, or inferred from the column's SQL type — and
each implies a different `Settings` shape.

---

## 9. Accessibility (WCAG 2.2 AA) in a real browser

axe-core injected into real Chrome with **`color-contrast` enabled**,
across 11 screens × light/dark at the default accent, plus the chat screen
× 2 themes × all 6 accents, plus 360 px × 2 themes, plus the sign-in
screen. 38 runs per pass, three passes.

| Rule | Impact | 2026-10-29 before | 2026-10-29 partial | 2026-10-30 after |
|---|---|---|---|---|
| `label-content-name-mismatch` | serious | **88** | 36 | **0** |
| `select-name` | critical | **6** | 0 | **0** |
| `label` | critical | **2** | 0 | **0** |
| `target-size` | serious | **4** | 2 | **0** |
| `color-contrast` | serious | 72 | 72 | **72** |

> The middle column is the first session's own re-run, taken at 20:13
> **before** its last two fixes landed at 20:14 and 20:16. Those two were
> never re-measured; closing them is what moved 36 → 0 and 2 → 0.
> Counts are `violation.count` (the real node count), not
> `len(violation.nodes)`, which the driver truncates to three — an earlier
> reading of these files undercounted contrast as 48 for that reason.

### 9.1 Fixed

- **`UI-007` (critical).** Nine native `<select>` elements and one
  `<input type="file">` had no accessible name at all — a screen reader
  announced "combobox" and "file, button". `components/ui/select.tsx`'s
  docstring says a native select is "fully accessible out of the box",
  which is true of keyboard operation and not of naming; that is how ten
  shipped unnamed. Each now carries an `aria-label`, or an associated
  `<label htmlFor>` where one was already visible beside it.
- **`UI-006` (serious, WCAG 2.5.3 Label in Name).** The header home
  button, the composer model picker and the account-menu trigger had
  accessible names that excluded their own visible text, so a
  speech-input user saying the words on screen matched nothing. Each name
  now *contains* the visible text with the purpose after it
  (`"Conversational Intelligence - Start a new chat"`,
  `"AI Model: Llama 3.1 8B"`, `"UA - account menu"`). Note `aria-hidden`
  on the avatar initials does **not** satisfy this rule — axe counts
  aria-hidden text as visible text here, and hiding them would not help
  the speech-input user either.
- **`UI-005` (serious, WCAG 2.2 AA 2.5.8 Target Size).** The show/hide
  password toggle on Sign In and Register was a bare 16×16 icon. axe:
  "16px by 16px, should be at least 24px by 24px". Now a real 24×24 box
  with a focus ring, visually where it was.
- **`UI-009` (serious).** The primary-navigation landmark carried both
  `flex-1` and `shrink-0`, which combine to "basis 0, may grow, must not
  shrink below basis" — i.e. zero. At ≤414 px the landmark measured
  **0 px wide** with its 36 px button still rendering inside it,
  overlapping the account block; axe reported a 6×32 px tappable sliver.
  The screens menu is the only way to reach another screen on a phone —
  the drawer beside it holds chat history, not navigation — so this was
  losing navigation, not a cosmetic overlap.

### 9.2 `UI-008` — contrast, and why this one is sharper than "a design decision"

72 nodes, in exactly two shapes:

**(a) `--accent` set as 12 px text.** Every accent fails in at least one
theme: dark indigo `#4f46e5` on `#13112d` = **2.91**; light amber
`#d97706` on `#f1f5f9` = **2.91**; dark violet `#7c3aed` on `#1a1830` =
**3.03**. 20 of 36 accent×theme×surface pairings are sub-AA.

**(b) Status tints in light theme** — `bg-[var(--token)]/15
text-[var(--token)]`, the badge shape used by Decision Center evidence
chips, onboarding step states, recommendation evidence labels and
semantic-review flags. `#15803d` on its own tint over a card = **4.09**;
`#b45309` over the page = **3.92**. 10 of 18 pairings sub-AA.

**The important part is why nothing caught it.** `src/index.contrast.test.ts`
is a declared-token AA guard that **passes**, and it passes because it
asserted the pairing that works and wrote a comment explaining why the
failing one need not be checked:

> *"Overwhelmingly used as `text-[var(--token)]`, including on top of its
> own `/10`-`/15` tint. **The tint is too faint to shift the result**, so
> the opaque surfaces below are the real test."*

That is false, and false in the direction that hides a failure: the tint
moves `--success` in light mode from 5.33 on the card to 4.09 on its own
tint. And `src/test/axe.ts` recorded contrast as something only a browser
could check — which licensed the whole class being skipped.

**Both claims are now disproved from inside the repo.** `src/test/contrast.ts`
already composites, so these pairings are arithmetic, not paint. Computing
them reproduces Chrome's own numbers to the second decimal:

| Pairing | Chrome | Computed from tokens |
|---|---|---|
| `#15803d` on `#dcece2` (success tint over card) | 4.09 | **4.09** |
| `#15803d` on `#d6e8df` (over page) | 3.93 | **3.93** |
| `#b45309` on `#eee1d8` (warning tint over page) | 3.92 | **3.92** |
| `#4f46e5` on dark `--card` | 3.01 | **3.02** |

**What was fixed (the guard, not the palette):**

- The false comment is corrected and names the measurement.
- `src/test/contrast.ts` gains `tintedSurface` and `contrastAgainst`, and
  its "what this does not prove" list no longer claims the translucent
  case is out of reach — the sentence that did is called out, because it
  cost this finding.
- `src/index.contrast.test.ts` gains a **no-growth baseline**
  (`src/index.contrast.baseline.json`, 30 pairings) in the same shape as
  `i18n/localeParity.test.ts`: the computed set of sub-AA pairings must
  equal the file exactly, so a newly failing pairing fails **and** a
  pairing that has since been fixed also fails. It cannot become a
  permanent allowlist. One test pins the agreement with Chrome's ratios,
  so the arithmetic cannot drift from what a browser paints.
  Mutation-verified in both directions: dropping a baselined entry → fail;
  adding a pairing that passes AA → fail.
- `src/test/axe.ts` now disables `target-size` and
  `label-content-name-mismatch` under jsdom as well, with the measurement
  behind each. `label-content-name-mismatch` is the dangerous one: it did
  not stay silent under jsdom, it reported a **false pass** on the exact
  markup Chrome flagged, because it needs rendered text.

**What was not fixed (flagged).** Every repair is a new colour value — a
visible design change and the owner's call. Proposal:

1. Hold `--accent` to AA as text, or stop using it for 12 px text and use
   `--foreground` with the accent as a non-text cue. The existing test
   already decoupled `--focus-ring` from `--accent` for the same reason;
   this is the same move for text.
2. Darken the light-theme `--success`/`--warning` *text* tokens (a
   `--success-text` pair, as `--danger-strong` already does for fills), or
   drop the `/15` tint behind them.

### 9.3 One contrast failure only a browser can see

`#9ca4ae` on `#ffffff` = **2.51**, on `QueryLifecycle`'s "not started"
stage labels. The token is fine; an `opacity-60` wrapper halves it, and
opacity is not a colour token, so no declared-token test can reach it.
Arguably exempt under WCAG 1.4.3 as an inactive component — recorded here
with that caveat rather than counted as a confirmed failure.

### 9.4 Keyboard, focus and reduced motion — what was and was not checked

Being precise here, because three of these are easy to over-claim:

**Verified.** "Skip to main content" is the first text on **every one of
the 84 role x screen cells** (the grid captures `body.innerText`, and all
84 begin with it). The header home control is a real `<button>`, not a
`<div onClick>` — so Tab and Enter/Space reach it by construction, and
`AppShell.tsx`'s comment says that is deliberate. The forbidden page and
the new not-found page both move focus to their heading, asserted in
jsdom (`App.routes.test.tsx`, `RequireNavItem`). `prefers-reduced-motion`
has a real global rule in `index.css:568` capping
`animation-duration`/`animation-iteration-count` for every element, plus
`motion-reduce:transition-none` on the individually-animated controls and
a `matchMedia('(prefers-reduced-motion: reduce)')` branch in
`QuestionNavigator.tsx` that jumps instead of smooth-scrolling — with its
own test.

**UNVERIFIED, and a human must do it.** No keyboard-only pass was driven
in a browser: Tab order across a whole screen, focus visibility against
each theme and accent, and focus trapping in the dialogs and dropdowns
were **not** measured. axe does not check any of those, so the zero
violations in §9 say nothing about them. This is the largest remaining
accessibility gap in this phase and it is not closed by anything above.
The radix-dropdown limitation in §1.1 is part of why: the three menu
controls could not be opened programmatically, so their focus behaviour
was never exercised either.

---

## 10. Responsiveness

At **360×780**, chat screen, both themes:

```
overflow = { scrollW: 360, clientW: 360, horizontalScroll: false }
```

No horizontal page scroll in either theme, and zero axe violations at
360 px in light theme after `UI-009` (one contrast node in dark, part of
§9.2). The long product title truncates rather than pushing the nav and
avatar off the header — `AppShell` now gives that block `flex-1` so it is
what yields width first, since the screens menu beside it is a fixed 36 px
icon button with nothing to give. Collapsed sidebar rail and the mobile
drawer were exercised by the grid run.

---

## 11. PWA and offline after logout

Sign-out driven through the real control with CDP input events, then the
network emulated offline:

- **Sign-out clears both sides.** Sign-in form shown, `localStorage` `{}`,
  no JS-visible cookie, and **`POST /auth/refresh` → 401 "No refresh token
  was presented."** The `HttpOnly` cookie was genuinely cleared
  server-side, not just forgotten by the client.
- **No stale authenticated data offline.** With the service worker
  installed (`precache 35 entries, 3550.02 KiB`), reloading offline after
  sign-out showed **no trace of the previous session's question**
  (`stillShowsTheQuestion: false`, body text 178 chars).

### 11.1 `UI-015` — offline reads as an access problem (**S4, flagged**)

Offline, the shell renders and the user is told *"We couldn't check your
access to this screen. / Try again"* — `RequireNavItem`'s error state,
which is a plausible but misleading explanation when the real cause is no
network. Proposed: branch on `navigator.onLine` / a `TypeError` from
`fetch` and say so. New user-visible copy in five locales, so flagged.

---

## 12. Security in the UI

| Check | Result |
|---|---|
| `dangerouslySetInnerHTML` | **zero occurrences** in `frontend/src` |
| `.innerHTML =` / `outerHTML` | zero in application code |
| `rehype-raw` | not enabled; react-markdown's HTML-stripping default is used |
| `dompurify` | an `overrides` pin for a transitive dependency, not an unused direct import |
| Token in `localStorage` | **none** — dump is `{}` signed in; access token lives in memory, refresh token is `HttpOnly` and invisible to JS (`document.cookie` empty) |
| PII in `localStorage` | none; the only persisted key is `tsql-settings` (appearance/model preferences) |
| Token in a URL | none observed |

### 12.1 `UI-016` — the component that renders untrusted text had no test (**S4, fixed**)

`components/ui/markdown.tsx` renders web-search answers, document and
policy RAG answers, synthesized multi-source answers and insight text —
all untrusted data by CLAUDE.md §5.5 — and its docstring says the
HTML-stripping default is "the correct one here, not an oversight". There
was **no `markdown.test.tsx` anywhere in the repository**, and no test in
`frontend/src` exercising a `javascript:` URL. The safety rested entirely
on a dependency's defaults.

Added `markdown.test.tsx` (7 tests) exercising the attacks: a `<script>`
tag, an `<img onerror>`, an `<iframe>`, a `javascript:` href, a `data:`
href, plus an ordinary `https` link so the guard cannot pass by blocking
everything. Measured behaviour: raw HTML renders as inert text, and
react-markdown's default `urlTransform` **empties** a disallowed scheme's
href rather than removing the anchor — so the element has no `link` role
at all and nothing is left to activate. Mutation-verified: removing the
custom anchor renderer fails 1 of 7.

Attachment preview, upload rejection and scan-unavailable paths were
reviewed in code but not driven in the browser (no malware-scanner daemon
here, per Phase 0's environment-blocked list) — **UNVERIFIED**.

---

## 13. i18n

- **Five locales offered**: `en`, `es`, `fr`, `hi`, `mr`.
- **No RTL language is offered**, so RTL is **not applicable**. No `dir`
  attribute handling exists, which is correct for this locale set and
  would become a gap the moment Arabic, Hebrew, Farsi or Urdu is added.
- **New strings in this phase are in all five locales**: the
  `routeGate.*` block (7 keys), `auth.sessionCheckUnavailable`,
  `auth.sessionCheckRetry` and `nav.primaryLandmark`. `localeParity.test.ts`
  (the no-growth guard) passes unchanged, so none of them added to the
  translation debt.
- One unreferenced key added during the first pass (`nav.filterLabel`) was
  removed from all five locales.
- `DOC-010` (1,120 keys English-only across four locales) is unchanged and
  remains Phase 10's. `UI-001` from Phase 5 (a hardcoded empty-result
  string in `AnalyticsSummary.tsx`) is also unchanged — it is a one-line
  change that moves a test, and it belongs with the `DOC-010` pass rather
  than being done in isolation here.
- The `aria-label`s added for `UI-007` on `AIGovernance`, `DataLineage`,
  `MediaSearch` and `PlatformAdmin` are **English literals**, matching
  those pages, whose visible text ("All asset types", "Roll back to…",
  "Security events") is already untranslated. Translating the labels while
  the surrounding page stays English would be inconsistent; both move
  together in Phase 10.

---

## 14. Voice, attachments and the image editor

- **Mic denied** is handled: `useAudioRecorder` distinguishes it, and
  `useVoiceConversation` sets the i18n **key** `voice.micDenied` (not the
  raw string) for the caller to translate. The hardcoded English fallback
  inside `useAudioRecorder` is never displayed — its only consumer
  discards it, with a comment saying so.
- **Transcription failure** has its own string and tells the user they can
  type instead.

### 14.1 `UI-013` — the cloud-backed live-caption exception is never shown to the user (**S2, flagged**)

`hooks/useSpeechRecognition.ts` uses the browser's Web Speech API for
live, word-by-word interim captions, and its docstring is explicit and
correct:

> *"This is a deliberate, disclosed exception to this feature's 'fully
> local, no cloud' design: in Chromium-based browsers, `SpeechRecognition`
> sends microphone audio to the browser vendor's own speech service."*

The authoritative final transcript does come from local `faster-whisper`,
so the design is sound. **But the disclosure exists only in source.** All
nine user-facing `voice.*` strings are:

```
settingsLabel, startConversation, listening, transcribing, speaking,
stopConversation, unsupportedCaption, transcribeFailed, micDenied
```

None mentions the cloud, the browser vendor, or that audio leaves the
machine. A user who taps "Start voice conversation" on a platform whose
whole premise is "the LLM runs locally, no network calls" has microphone
audio sent to a third party with no on-screen notice. For a UAT with real
business users this is a consent problem, not a copy nit — hence S2.

Not fixed because it is new user-visible copy in five locales and its
placement is a product decision. Proposed: a line under the voice toggle
in Settings and a one-time notice on first use, e.g. *"Live captions use
your browser's speech service, which sends audio to your browser vendor.
The final transcript is produced on this machine."*

---

## 15. Bundle size and performance

`npm run build`, 2026-10-30:

| Asset | Raw | gzip |
|---|---|---|
| `index-*.js` (entry) | **1,658.03 kB** | **487.42 kB** |
| `jspdf.es.min-*.js` | 399.71 kB | 129.92 kB |
| `ImageEditor-*.js` | 350.83 kB | 107.12 kB |
| `html2canvas-pro.esm-*.js` | 248.97 kB | 63.22 kB |
| `server.browser-*.js` | 201.65 kB | 61.56 kB |
| `KpiCard-*.js` | 198.61 kB | 68.62 kB |
| `index-*.css` | 74.79 kB | 13.26 kB |
| PWA precache | **35 entries, 3,550.02 KiB** | — |

Vite warns that chunks exceed 500 kB. The entry chunk at 487 kB gzip is
heavy for a first load; the heavy optional features (PDF export, image
editor, canvas capture) are already split out, so the remaining win is in
the entry chunk itself. Self-hosted fonts are a large share of the
precache: seven families × many subsets, including `merriweather-latin`
97.54 kB, `inter-latin-ext` 85.06 kB and `merriweather-latin-ext`
74.96 kB.

One build warning worth a follow-up:
`INEFFECTIVE_DYNAMIC_IMPORT — src/store/questionQueueStore.ts is
dynamically imported by src/store/chatStore.ts but also statically
imported by src/components/sql/QuestionQueuePanel.tsx`, so the lazy import
in `chatStore` buys nothing.

**Measured answer latency** (live, `llama3.1:8b`, `adventureworks`):

| Run | Total | `generate_sql` | Note |
|---|---|---|---|
| SSE probe, warm | 18.4 s | 11.6 s | 20 progress events |
| Browser J1, cold | 162.6 s | — | model load dominates |
| Browser badge probe | 101 s | 96.8 s | the 97 s silent window of `UI-010` |
| Default model | 1.7 s | — | fails: not installed (`OPS-008`) |

**Lighthouse: UNVERIFIED.** The CLI is not installed here. A human must
run `npx lighthouse http://localhost:8000/ --preset=desktop` and
`--preset=mobile` against a built deployment for the composite scores.

---

## 16. Gates re-run after this phase

All nine blocking CI gates, measured 2026-10-30 on this branch:

| Gate | Result |
|---|---|
| `ruff check .` | **exit 0** — All checks passed |
| `black --check .` | **exit 0** — 1,684 files unchanged |
| `bandit -c pyproject.toml -r .` | **exit 0** |
| `mypy` (41 production packages) | **exit 0** — no issues in 482 source files |
| `pytest` (targeted) | **1,914 passed / 0 failed**, 16 skipped, 5 xfailed, 192s — all of `tests/security/`, plus streaming, governance, agent-node and CI-guard suites |
| `pytest` (full suite) | **9,579 passed / 0 failed**, 16 skipped, 11 deselected, 8 xfailed, 3,919s |
| `pip-audit` | not re-run — no dependency changed in this phase |
| `detect-secrets` | **red before this phase and unchanged by it** (see below) |
| frontend `vitest` | **1,551 passed / 0 failed**, 107 files (was 1,540 / 106) |
| frontend `npm audit --omit=dev` | **exit 0** — 0 vulnerabilities |

Also green and not a gate: `npm run lint` (oxlint) exit 0 (warnings only,
all pre-existing), `npx tsc --noEmit -p tsconfig.app.json` exit 0,
`npm run build` succeeds, and `python -c "import api.main"` succeeds.

**On the suite's wall-clock time.** CLAUDE.md §13 records 9,485 passed in
**514s** (2026-10-28); this run took **3,919s** for 9,579. That is not
being reported as a regression, because the same machine ran 1,914 of
those tests in 192s during this session — 0.10s per test against the full
run's 0.41s. The full run overlapped real Chrome instances, two `uvicorn`
servers, a `mypy` pass and a parallel targeted `pytest`, so the gap is
contention in this environment on this day rather than a property of the
suite. **A human re-measuring it on a quiet machine is the only way to
settle that**, and the 514s figure should not be treated as refuted by
this number.

The counts themselves reconcile: 9,485 → 9,579 is +94 tests (this phase
added 37 — 6 streaming, 2 masking, 7 markdown, 5 contrast, 7 routing, 18
session-survival, less overlap — and the inherited session added the
rest), and 5 → 8 xfailed is Phase 7's three `CTX-007` marks, which
postdate CLAUDE.md's 2026-10-28 measurement.

Two of these needed fixing *because of inherited work*, which is worth
recording: `black --check .` was **red** on arrival —
`tests/test_progress_crosses_the_ask_executor.py`, written by the
previous session, had never been formatted — and `governance/classification.py`
needed formatting after this phase's own change. Both are now clean.

**`detect-secrets`.** Still red, for exactly the reason CLAUDE.md §13
already records (a stale baseline, no leaked secret, refreshable only from
Linux). Confirmed **not made worse here**: `detect-secrets scan` over all
ten new and changed files reports one finding, at
`FINDINGS_REGISTER.md:62` — a pre-existing Phase 2 row whose *prose about
the gate* trips the keyword and hex detectors. None of this phase's own
additions produces a finding, including the base64 `data:` URL in
`markdown.test.tsx`.

`prettier` is **not** a gate in this repository — it appears in neither
`.github/workflows/ci.yml` nor `frontend/package.json`, and files written
by earlier sessions do not satisfy it. `oxlint` is the enforced frontend
linter.

---

## 17. What a human must still run

1. **Lighthouse**, desktop and mobile presets, against a built deployment
   (§15).
2. **Install the configured model** — `ollama pull qwen2.5-coder:7b` — or
   change `OLLAMA_MODEL` to something installed. Until then the first
   question any UAT user asks fails (`OPS-008`).
3. **Seed data for the HR journeys.** `hr` has 11 tables and 0 rows, so
   any journey reading HR data cannot be walked (Phase 11).
4. **Decide the six flagged items**: `UI-008` (palette), `UI-010`
   (progress protocol), `UI-011` (mask explanation), `UI-013` (voice
   disclosure), `UI-014` (currency), `UI-015` (offline message).
5. **`AUZ-004`** — decide whether `platform_admin` should imply the base
   `user` permissions, so a platform admin can open the chat screen
   (§3.2).
6. **A keyboard-only pass in a browser** (§9.4) — Tab order per screen,
   visible focus against each theme and accent, and focus trapping in
   dialogs and dropdowns. axe checks none of these, so §9's zeros do not
   cover them. This is the largest accessibility gap left open.
7. **Drive the screens menu in a browser** (§1.1 item 1) if a screenshot
   of mobile navigation is wanted for the UAT pack.
8. **Attachment upload rejection and scan-unavailable paths** in a
   deployment with a real malware scanner (§12).

---

## 17b. Post-audit end-to-end run (2026-10-30)

After the audit's own fixes were committed, the application was driven end
to end against the live stack, prompted by a user's screenshot: a question
answered in 5.7s with *"Agent could not produce a working query: The AI
model service is temporarily unavailable. Please try again in a moment."*

That one screen turned out to be showing **three** separate things, and
separating them is the useful part.

**1. `OPS-008` — the configured model was not installed.** `.env` named
`OLLAMA_MODEL=qwen2.5-coder:7b`; Ollama had only `llama3.1:8b` and
`qwen3.8:27b`. Every question on the default therefore failed. Both
`.env.example` and `config/settings.py` ship `llama3.1:8b`, so the local
value had drifted from the documented default. Corrected locally (a
one-line `.env` change, backed up first); the repository already had it
right.

**2. `OPS-009` (S2, fixed) — a missing model disabled the installed
ones.** This is why the message was the *generic* breaker refusal rather
than the accurate "not installed, pull it" text the same stack produces
on a cold breaker. `agent/harness/breakers.py` keys its breaker by
**provider**, and every `client.chat` call site reported
`MODEL_UNAVAILABLE` for any exception — including Ollama's `404 model not
found`, which is the one case where the provider is demonstrably healthy.
Five of those opened the breaker, and it then refused *every* model on
Ollama, including the installed one the model picker offers as the
documented workaround for `OPS-008`.

Measured before the fix: a question pinned to the installed
`llama3.1:8b` returned in **1.4s** with the generic refusal; the
identical question **succeeded in 108.8s with 5 correct rows** once the
30s cooldown elapsed. After the fix: six consecutive failures on the
uninstalled model, each with the *accurate* message, then the installed
model succeeded with **5 rows in 10.4s**.

The defect's shape is worth keeping. `ollama_unavailable_error` and
`record_outcome` each classified the same 404, independently, and
**disagreed**: one told the user to pick another model, the other then
refused the model they picked. Neither was wrong alone, and no test asked
whether they agreed. The fix is a shared predicate rather than a new
rule.

**3. `OPS-010` (S3, fixed) — startup said nothing.** The app booted clean
with an unusable default, so the misconfiguration was invisible until a
user asked something. `GET /models` already reported
`installed: false`; nothing stated it where an operator reads it. There
is now a `[startup]` warning naming the model, the host, `ollama pull`
and the installed alternatives — silent when Ollama is simply
unreachable, which `GET /health` already reports.

### What the same run confirmed working

- **A non-SQL question routes correctly.** "Top news stories for today
  around the world" routes to `web`, calls Tavily (HTTP 200) and
  synthesises an answer. The screenshot's SQL-framed error was not a
  routing defect — with the LLM path down, the router's own classifier
  call failed too.
- **Write SQL is refused** on all four shapes tried: `DELETE`, `DROP`,
  a stacked `SELECT …; DELETE`, and `SELECT * FROM sys.tables`.
- **Masking holds** across admin / user / viewer (§7).
- The dashboard is served and the built bundle carries the Phase 9 fixes.

### One observation, not a finding

**The LLM router timed out and fell back to keywords on every invocation
measured.** `router_timeout_seconds` defaults to 5.0, and the log shows
`[router] routing exceeded 5.0s on attempt 1 -- using keyword fallback`
for 2 of 2 routed questions on this hardware. It chose correctly both
times, and the deterministic fallback is the designed behaviour, so
nothing is broken. It is recorded because it means
`SOURCE_CONFIDENCE_THRESHOLD` and the LLM classifier have little
practical effect on this machine — routing here is effectively the
hand-authored keyword path, which CLAUDE.md §13 already notes has no
labelled-corpus measurement behind it. A deployment on faster hardware
would route differently, and neither configuration has been measured for
accuracy.

---

## 18. Screenshots

In [`phase-9-screenshots/`](phase-9-screenshots/):

| File | Shows |
|---|---|
| `01-unmatched-route-now-explained.png` | `UI-003` fixed — the not-found page, inside the shell |
| `02-viewer-refused-platform-admin.png` | A `viewer` refused Platform Admin by the server's gate |
| `03-admin-sees-platform-admin.png` | The same screen for `platform_admin` |
| `04-streaming-progress-stage-in-badge.png` | `UI-004` fixed — a stage named in the badge |
| `05-uninstalled-default-model-failure.png` | `OPS-008` — the honest failure and lifecycle strip |
| `06-offline-after-signout.png` | Offline after sign-out, no stale data (`UI-015`'s message) |
| `07-after-sign-out.png` | Sign-out complete, storage empty |
| `08-chat-at-360px.png` | 360 px, no horizontal scroll, `UI-009` fixed |
| `09-contrast-status-tints-light.png` | `UI-008` shape (b) — status tints |
| `10-contrast-accent-text-dark.png` | `UI-008` shape (a) — accent as text |
