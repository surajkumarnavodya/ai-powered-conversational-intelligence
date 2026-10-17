# UAT Scripts and Sign-off Template (Prompt 59)

**Prepared:** 2026-10-17 · **Baseline commit:** `779d7fc`
**Revised:** 2026-10-17 at `08f385e` — added §2.5 (objective, business
scenario, preconditions and test data per journey), §2.6 (what is
already verified, and what is already known broken), journeys **J16–J18**
(documentation accuracy; model selection; boundaries and limits), and
per-row **Defect ref / Severity / Tester / Date** columns on the new
journeys. **18 journeys across 6 roles.**
**Status: NOT TESTED.** No UAT participant has executed any journey below.

> ### This document contains no results
>
> Every "Actual result", "Status" and "Tester sign-off" cell is
> deliberately blank. They are filled in **by a human tester**, not by
> an agent. Nothing here may be reported as UAT evidence until a named
> person has run the journey and signed the row.
>
> **Automated tests are not UAT.** The platform has **7,274 backend and
> 1,161 frontend** automated tests passing (re-measured 2026-10-17 at
> `08f385e`; 0 failures), and they prove something
> different: that the code behaves as its authors specified. UAT asks
> whether the *right* thing was specified, judged by someone who does
> the job. One cannot substitute for the other.
>
> The same is true of the live technical probing recorded in §2.6. It
> was performed by an agent against the running application, it is
> genuine evidence that the controls hold, and it is **still not UAT** —
> no acceptance judgement was made by anyone who does the business's
> work.

---

## 1. How to run this

1. Create a copy of this file per tester, named
   `UAT_<role>_<YYYY-MM-DD>.md`.
2. Work top to bottom. Do not skip a prerequisite.
3. Record what actually happened, including anything surprising, even
   when the step "passed".
4. Mark each step with one of **exactly these five**:

   | Status | Use it when |
   |---|---|
   | **PASS** | You performed the step and the expected result occurred |
   | **FAIL** | You performed the step and it did not |
   | **BLOCKED** | You could not perform it — missing data, missing permission, an earlier failure, or absent infrastructure. Say which |
   | **NOT TESTED** | You did not attempt it. This is the **default** for every row until someone does. A blank cell means NOT TESTED |
   | **N/A** | The step does not apply to this deployment (e.g. a disabled feature). Say why |

5. For every FAIL, fill in **Defect ref** and **Severity** (P0–P3, §4)
   and capture evidence: a screenshot, the `correlation_id` from the
   error, and the approximate time, so it can be traced in the server
   log.
6. Fill in **Tester** and **Date** on every row you touch — the
   role-level sign-off in §5 is a summary, not a substitute. A row with
   a status but no tester and date is not evidence.
7. Sign the role summary at the end.

> **What an agent may and may not do in this document.** An automated
> assistant may prepare journeys, correct a step that no longer matches
> the product, and record what *automated or manual technical* checks
> already established (§2.6). It may **never** write into an Actual
> result, Status, Tester or sign-off cell. Those are a human
> acceptance judgement, and a machine-filled cell would make this
> document worthless for the only purpose it has.

### Environment (fill in before starting)

| Field | Value |
|---|---|
| Environment URL | |
| Commit / build | |
| Database(s) in scope | |
| Date | |
| Tester name and role | |

### Data rule

**Use only approved synthetic or non-sensitive data.** Do not paste real
customer, employee, financial or personal records into any prompt. If a
journey appears to require real sensitive data, mark it **BLOCKED** and
say so rather than substituting production data.

---

## 2. Roles

| Role | Who should test it | Journeys |
|---|---|---|
| R1 Platform Admin | Operates the platform across tenants | J1, J2, J12, J13, J17, J18 |
| R2 Tenant Admin | Owns one tenant's configuration | J1, J2, J3, J12, J16 |
| R3 SME / Semantic Reviewer | Owns business meaning of the data | J3, J4, J16 |
| R4 Business Analyst | Daily analytical user | J5–J9, J11, J17, J18 |
| R5 Business User | Occasional question-asker | J5, J6, J10, J11, J16 |
| R6 Read-only Viewer | Can see, must not change | J1, J14, J18 |

---

## 2.5 Objective, business scenario, preconditions and test data

One row per journey. **Read your journeys' rows before you start** — the
preconditions are the usual reason a journey turns out BLOCKED halfway
through, and finding that out at step 1 costs minutes instead of an hour.

| Journey | Business scenario — the question UAT is really asking | Objective (what a PASS proves) | Preconditions | Test data to use | Scenario type |
|---|---|---|---|---|---|
| **J1** Authentication & access control | "Can the wrong person get in, or see what they shouldn't?" | Identity is enforced by the server, and a role's limits are a boundary rather than a hidden menu item | `LOCAL_AUTH_ENABLED=true`; one account per role R1–R6, created in advance by an admin | Your own account; one deliberately wrong password; one R6 viewer account | Positive + **negative** |
| **J2** Database onboarding | "Can we connect our own database without handing credentials to the product?" | The onboarding lifecycle completes and the connection secret is never persisted | R1 or R2 role; an **approved non-production** database and a read-only login for it | A test/staging database only. **Never** a production connection string | Positive + negative (2.5) |
| **J3** Semantic review & metric approval | "Can the business own what a term means, rather than the model guessing?" | A definition can be edited, versioned, published and rolled back, and the published version is what answers use | R3 role; at least one pending semantic entry; J2 completed | One business term you can define authoritatively; one metric with a known formula | Positive + end-to-end |
| **J4** Governed metric correctness | "Is the number the number?" | A governed metric returns the figure the business means — **the single highest-value row in this document** | J3 completed and the metric published | A metric whose true current value you can derive independently (spreadsheet, existing report). Bring that figure with you | **End-to-end, judgement** |
| **J5** Ask a question (Text-to-SQL) | "Can someone who cannot write SQL get a trustworthy answer?" | A plain-English question yields a correct, inspectable answer — or an honest refusal | A database registered and indexed; Ollama running with the configured model | Three questions you already know the answer to; one the data genuinely cannot answer | Positive + **negative** (5.5) |
| **J6** Multi-turn context & chat history | "Does it hold a conversation, and is my work still there tomorrow?" | Follow-ups resolve against prior turns; conversations persist and reload | J5 completed; a locally-authenticated account (**see the limitation in §2.6**) | A question plus two follow-ups ("and by region?", "just last quarter") and one deliberately ambiguous one | Positive + negative + end-to-end |
| **J7** SQL safety | "Can this thing change or destroy our data?" | Write attempts are refused by the server, after a hand-edit, every time | **Authorisation to attempt negative testing.** A non-production database | A natural-language delete request; a hand-edited `UPDATE`; a query over a large table | **Negative + boundary** |
| **J8** Document question answering (RAG) | "Can it answer from our documents and show us where it got it?" | Answers are grounded in an uploaded document with citations that point at real passages | `ENABLE_DOCUMENT_RAG=true` and a configured `RAG_STORE_CONNECTION_STRING` (SQL Server 2025+/Azure SQL) | One **approved, non-sensitive** PDF whose contents you know; one question it does not cover | Positive + **negative** (8.4) |
| **J9** Analysis: anomalies, trends, recommendations | "Does the analysis help, and does it admit what it is guessing?" | Findings carry their evidence, and an estimate is never dressed as an observation | A table with a usable time dimension and enough history | A time series you have previously analysed yourself | Positive + end-to-end |
| **J10** Citations & rendering | "Can I trust and read what it shows me?" | Sources are attributed and long answers render correctly | `ENABLE_WEB_SEARCH=true` and/or J8 completed | A question spanning a document and the web; one wide table (15+ columns) | Positive + boundary |
| **J11** Charts, exports & sharing | "Can I take this into a meeting, and does sharing leak anything?" | A result exports faithfully, and a share link discloses nothing a roleless viewer should not see | A completed J5 result; sharing enabled if in scope (`share_public_links_enabled`) | A numeric result worth charting; one result containing a restricted column | Positive + **negative** (11.5) |
| **J12** Admin, audit & observability | "Can we see what happened and prove it?" | Actions and refusals are both recorded, and admin views render real figures | R1 or R2 role; J1.4's refusal already performed | Your own session's activity | Positive + end-to-end |
| **J13** Error handling & provider failure | "What happens on a bad day?" | Failures are clear, recoverable and leak nothing internal | **An operator who can stop and start the Ollama service.** Coordinate a window | A question asked while the model service is down | **Negative** |
| **J14** Responsive layout & accessibility | "Can everyone on the team actually use it?" | The app is usable at phone width, by keyboard, and in every shipped language | A phone or narrow browser window; keyboard only | All five locales (en/es/fr/hi/mr) | Positive + boundary |
| **J15** Regression check | "Did this release break what I rely on?" | Previously working behaviour is unchanged | A question and a saved conversation from **before** this build | Your own prior work | **Regression** |
| **J16** News, README & documentation accuracy | "Does the documentation describe the product I am holding?" | A new reader is not misled — claims match behaviour, status is stated honestly | None | [`README.md`](../../README.md), [`USER_GUIDE.md`](../../USER_GUIDE.md) | **Documentation accuracy** |
| **J17** Model selection & AI integrations | "Can we choose the model, and is the choice actually honoured and bounded?" | Per-request model selection works and an unapproved model is refused, not silently substituted | Two models pulled in Ollama; both listed in `OLLAMA_ALLOWED_MODELS` | One allowed model; one deliberately not on the allowlist | Positive + **negative** |
| **J18** Boundaries & limits | "What happens at the edges?" | Caps, limits and oversized inputs are handled and disclosed, never silently truncated | A large table; a file above the upload size limit | An empty question; a very long question; an oversized file; a query returning more rows than the cap | **Boundary + negative** |

## 2.6 What is already verified — and what is already known broken

**Read this before you start.** It exists so you do not spend UAT time
re-finding things that are already measured, and so you are not
ambushed by a defect that is already on record.

### Already verified by automated tests (not UAT, and not a substitute)

**7,274 backend tests and 1,161 frontend tests pass, 0 failures**
(measured 2026-10-17 at commit `08f385e`). They prove the code does what
its authors specified. They say nothing about whether the right thing
was specified — which is the whole of your job here.

### Already verified by live technical probing (manual, by an agent, not UAT)

These were driven against the running application on 2026-10-17 with
real databases and a real local model. They are recorded in
[`2026-10-17_release_gate_v2.md`](2026-10-17_release_gate_v2.md) §11.5.
**You should still perform the corresponding journey step** — a business
tester judging the experience is a different test from a probe
confirming a status code — but if one of these *fails* for you, that is
a regression worth escalating immediately:

| Already shown to work | Relevant journey step |
|---|---|
| Sign-in refused without a token; password policy rejects an email-derived password | J1.1, J1.2 |
| 14 of 14 admin routes refused server-side for a plain `user` role | **J1.4** |
| Refusals are written to the audit log with role and required permission | J12.3 |
| A hand-edited `UPDATE`, a `DROP`, a stacked `SELECT 1; DELETE ...` and a `sys.tables` reference are all refused | **J7.1, J7.2** |
| The row cap is injected into the SQL text (`SELECT TOP 1000`) | J7.3 |
| Compensation columns return as `[restricted]` against a bare `SELECT *` | J11.5 |
| A conversation is created, titled, persisted and reloadable | J6.4 |
| A question with no answer in the data returns zero rows and **no invented figure** | **J5.5** |
| Connection strings and passwords do not appear in logs | J13.4 |

### Already known broken or absent — do not raise these as new defects

| Ref | What you will hit | Affects | Raise only if |
|---|---|---|---|
| **F-012** | With **several databases configured**, the question is auto-routed by schema similarity alone and can land on a database that holds no rows — you get a correct-looking query, zero results, and **no way to redirect it** (there is no database picker; `AskRequest` has no `database` field). Reproduced 2026-10-17: "How many employees are in each department?" routed to an empty `hr` database while a configured database with 15 employees was available | **J5.1, J5.3, J15** | …it returns a *wrong* number rather than *no* number. That would be a different, far more serious defect |
| §13 | **Chat history is only persisted for locally-authenticated accounts.** An OIDC or unauthenticated caller is answered normally but nothing is saved | **J6.4** | …it fails for a *local* account |
| §13 | A reloaded turn restores the answer, its SQL and a bounded result snapshot — **not** charts, retrieved DDL or per-source citations | **J6.4, J15.2** | …the answer or SQL is missing too |
| §13 | **No scheduler and no delivery channel.** Nothing is emailed, posted or messaged; KPI monitoring and briefings produce documents only | J9, J12 | …you were told otherwise |
| §13 | **No token-level streaming.** `POST /ask/stream` shows per-node progress, not words appearing | J5.4 | — |
| Perf | **This platform's answer latency is unmeasured on representative hardware.** On the development machine a single question took **183 seconds**, because the model exceeds its VRAM and runs largely on CPU. Judge J5.4 against *your* environment and record the figure; do not treat the dev number as the product's | **J5.4** | — |
| F-004 | **Answer accuracy has no current valid measurement.** The only full-corpus benchmark is self-disclaimed as stale. J4 and J5.3 are therefore the *first* real accuracy evidence this project will have — which is why your judgement there matters more than anything else in this document | **J4, J5.3** | — |

## 3. Journeys

Each step: **prerequisite → action → expected → actual → status → evidence**.

### J1 — Authentication and access control

| # | Action | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|
| 1.1 | Sign in with valid credentials | Reaches the chat workspace | | | |
| 1.2 | Sign in with a wrong password | Refused; message does **not** reveal whether the account exists | | | |
| 1.3 | Note which navigation items are visible | Only items this role is entitled to | | | |
| 1.4 | As R6, attempt an action reserved for an admin | Refused by the **server**, not merely hidden | | | |
| 1.5 | Sign out, then press Back | Does not return to an authenticated screen | | | |

> 1.4 is the one that matters. A hidden button is UX; the check must be
> server-side. If the action succeeds, stop and raise it immediately.

### J2 — Database onboarding

| # | Action | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|
| 2.1 | Start a new onboarding job against an approved test database | Job is created | | | |
| 2.2 | Run discovery | Tables and relationships are listed | | | |
| 2.3 | Review the discovered items | Each can be accepted or rejected | | | |
| 2.4 | Publish | Database becomes available for questions | | | |
| 2.5 | Confirm credentials were not persisted anywhere visible | No password shown or stored in the job record | | | |

### J3 — Semantic review and metric approval

| # | Action | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|
| 3.1 | Open the semantic review queue | Pending entries listed | | | |
| 3.2 | Edit a business definition | Change is saved as a new version | | | |
| 3.3 | Approve a metric | Status becomes published | | | |
| 3.4 | Ask a question using that metric | Answer uses the approved definition | | | |
| 3.5 | Roll a metric back to its previous version | Previous definition is restored | | | |

### J4 — Governed metric correctness *(SME judgement — the key UAT step)*

| # | Action | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|
| 4.1 | Ask for a metric you already know the true value of | Returned figure matches your independently known value | | | |
| 4.2 | If it differs, record **both** numbers and how you derived yours | Discrepancy is explainable | | | |

> This is the single most valuable row in the document. An automated
> test can check the engine computes what it was told to; only you can
> say whether that is the number the business means.

### J5 — Ask a question (Text-to-SQL)

| # | Action | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|
| 5.1 | Ask a simple question in plain English | Relevant answer with a result table | | | |
| 5.2 | Open the SQL panel | Generated SQL is visible and readable | | | |
| 5.3 | Judge whether the SQL answers the question asked | Joins/filters match your intent | | | |
| 5.4 | Note time from send to answer | Acceptable for your working rhythm | | | |
| 5.5 | Ask something the data cannot answer | Says so plainly; does **not** invent a figure | | | |

### J6 — Multi-turn context

| # | Action | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|
| 6.1 | Ask a question, then a follow-up ("and by region?") | Resolved against the previous turn | | | |
| 6.2 | Ask a deliberately ambiguous follow-up | Asks for clarification rather than guessing | | | |
| 6.3 | Remove a turn from the context panel, ask again | Answer reflects the narrowed context | | | |
| 6.4 | Reload the page and continue the conversation | History restored; follow-ups still work | | | |

### J7 — SQL safety *(authorised negative testing only)*

| # | Action | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|
| 7.1 | Ask in plain English for data to be deleted or updated | Refused; no write occurs | | | |
| 7.2 | Hand-edit the SQL into an `UPDATE` and confirm-and-run | Refused by validation **after** the edit | | | |
| 7.3 | Ask a question returning very many rows | Result is capped and the cap is disclosed | | | |

> 7.2 tests that editing does not bypass validation. If any write
> succeeds, **stop all UAT and escalate** — that is a release blocker.

### J8 — Document question answering (RAG)

| # | Action | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|
| 8.1 | Upload an approved non-sensitive document | Accepted and processed | | | |
| 8.2 | Ask a question answered by that document | Answer grounded in it | | | |
| 8.3 | Check the cited sources | Citations point at real passages | | | |
| 8.4 | Ask something the document does **not** cover | Says it is not covered; does not invent | | | |

### J9 — Analysis: anomalies, trends, recommendations

| # | Action | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|
| 9.1 | Ask a question over a time series | Trend described with figures | | | |
| 9.2 | Review any anomaly flagged | Plausible, with its own evidence | | | |
| 9.3 | Review a recommendation | States what it rests on | | | |
| 9.4 | Check that estimates are labelled as estimates | Forecasts never presented as observed fact | | | |

### J10 — Citations and rendering

| # | Action | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|
| 10.1 | Ask a question that uses web or document sources | Sources shown and clickable | | | |
| 10.2 | Read a long answer | Headings, lists and tables render correctly | | | |
| 10.3 | Check a table with many columns | Scrolls/wraps without breaking the page | | | |

### J11 — Charts, exports and sharing

| # | Action | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|
| 11.1 | Visualise a result | Chart matches the table | | | |
| 11.2 | Switch chart type | Updates without re-running the query | | | |
| 11.3 | Export CSV | Opens correctly; values match the screen | | | |
| 11.4 | Download the answer as Markdown and as PDF | Both contain the question and the answer | | | |
| 11.5 | Share a conversation, open the link in a private window | Shows only what it should; restricted values stay masked | | | |

> 11.5 matters: a share link may be opened by someone with **no roles**.
> If a restricted value is visible there, that is a release blocker.

### J12 — Admin, audit and observability

| # | Action | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|
| 12.1 | Open the admin metrics view | Figures render and look plausible | | | |
| 12.2 | Find the audit entries for your own session | Your actions are recorded | | | |
| 12.3 | Trigger a refused action, then look for it in the audit | Refusal recorded | | | |

### J13 — Error handling and provider failure *(coordinate with an operator)*

| # | Action | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|
| 13.1 | Ask a question while the model service is stopped | Clear error; app stays usable; no stack trace shown | | | |
| 13.2 | Restore the service and retry | Succeeds without a reload | | | |
| 13.3 | Press Stop mid-answer | Generation stops promptly | | | |
| 13.4 | Confirm no error exposed a connection string, key or internal path | Nothing sensitive in any message | | | |

### J14 — Responsive layout and accessibility

| # | Action | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|
| 14.1 | Use the app at phone width | No horizontal scrolling; controls reachable | | | |
| 14.2 | Navigate using **only** the keyboard | Every control reachable; focus always visible | | | |
| 14.3 | Open and close a dialog by keyboard | Focus trapped inside, returns to the trigger | | | |
| 14.4 | If you use a screen reader, read one answer | Content and controls announced sensibly | | | |
| 14.5 | Switch language | Interface translates; no raw keys like `chat.rerun` | | | |

### J15 — Regression check of existing behaviour

| # | Action | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|
| 15.1 | Repeat a question you asked before this release | Same quality of answer as previously | | | |
| 15.2 | Open an older saved conversation | Loads with its result and chart | | | |
| 15.3 | Confirm a workflow you rely on daily still works | Unchanged | | | |

### J16 — News, README and documentation accuracy

Added 2026-10-17. **This journey has no in-product screen.** There is no
"News & Views" feature in this platform — the News content lives in
[`README.md`](../../README.md)'s "News and Updates" section, and the
only in-product reading surfaces are the shipped guides. The journey
tests whether the documentation describes the product you are actually
holding, which is the acceptance question behind "is the README
accurate".

| # | Action | Expected | Actual | Status | Defect ref / Sev | Tester | Date | Evidence |
|---|---|---|---|---|---|---|---|---|
| 16.1 | Read `README.md`'s "News and Updates" entries | At most three entries, newest first; each describes something you can find in the product or the repository | | | | | | |
| 16.2 | Pick one concrete claim from the newest entry and try it | The behaviour matches the claim, or the entry says plainly that it is absent/unwired | | | | | | |
| 16.3 | Read `README.md`'s "Project status" table | Its status matches what you have experienced; nothing reads as a production-readiness claim | | | | | | |
| 16.4 | Read `README.md`'s "Known limitations" | Every limitation you hit during J1–J15 is listed there, or raise the gap | | | | | | |
| 16.5 | Follow `README.md`'s Getting Started on a clean machine, if one is available | The documented steps are sufficient and in the right order | | | | | | |
| 16.6 | Read [`USER_GUIDE.md`](../../USER_GUIDE.md) for one feature you used | It describes what you saw, with no screen or control that does not exist | | | | | | |

> 16.4 is the one that matters. A limitation you discovered that the
> documentation does not admit is a **documentation defect**, and it is
> the kind that costs trust fastest. Raise it with the same severity you
> would a functional bug.

### J17 — Model selection and AI integrations

Added 2026-10-17.

| # | Action | Expected | Actual | Status | Defect ref / Sev | Tester | Date | Evidence |
|---|---|---|---|---|---|---|---|---|
| 17.1 | Ask a question without choosing a model | Answered using the configured default | | | | | | |
| 17.2 | Ask the same question, selecting a different allowed model | Answered; the response reports the model actually used | | | | | | |
| 17.3 | Compare 17.1 and 17.2 | Any difference in quality is visible to you, and neither answer claims a figure it did not retrieve | | | | | | |
| 17.4 | Request a model that is **not** on the allowlist | **Refused.** The request must not silently fall back to another model | | | | | | |
| 17.5 | Check whether the interface reports the model and token usage | Reported where the product claims to report it; **no monetary cost figure** — the model runs locally and a price would be invented | | | | | | |

> 17.4 is a control, not a preference. Silent substitution would mean a
> recorded answer could not be attributed to the model that produced it.

### J18 — Boundaries and limits

Added 2026-10-17. **Authorised negative/boundary testing.** Nothing here
should break the application; the point is to confirm each edge is
handled *and disclosed* rather than silently absorbed.

| # | Action | Expected | Actual | Status | Defect ref / Sev | Tester | Date | Evidence |
|---|---|---|---|---|---|---|---|---|
| 18.1 | Submit an empty question | Refused or ignored cleanly; no error screen | | | | | | |
| 18.2 | Submit a very long question (several thousand characters) | Either handled or refused with a clear reason — never truncated silently | | | | | | |
| 18.3 | Ask something returning far more rows than the cap | Result is capped **and the cap is disclosed on screen** | | | | | | |
| 18.4 | Export that capped result | The export matches what was shown, and the truncation is still disclosed | | | | | | |
| 18.5 | Upload a file above the configured size limit | Refused with a clear message before any processing | | | | | | |
| 18.6 | Upload a file of an unsupported type | Refused by type, not by failing later in a parser | | | | | | |
| 18.7 | Ask a question about a group so small it identifies individuals | Small-group suppression applies **even though you are authorised**; the group label and count stay visible and **no row disappears** | | | | | | |
| 18.8 | Send several questions in quick succession | Excess requests are refused promptly and clearly, not queued indefinitely or dropped silently | | | | | | |
| 18.9 | Leave the app idle past the session lifetime, then act | Re-authentication is required; no action succeeds on an expired session | | | | | | |

> 18.3/18.4 together are the real test: a cap that is applied but not
> disclosed is worse than no cap, because the reader believes they are
> looking at the whole answer.
>
> 18.7 protects the individual in the group, not the column — so it
> applies to an authorised caller too. A row vanishing instead of a
> value being suppressed would let a reader difference a published total
> against the visible rows and recover the hidden value; report it if
> you see rows disappear.

---

## 4. Defect log

| ID | Journey/step | Severity (P0–P3) | Description | Evidence | Raised with | Status |
|---|---|---|---|---|---|---|
| | | | | | | |

**Severity.** P0: data loss, a write reaching the database, cross-tenant
exposure, or a restricted value disclosed. P1: a core journey cannot be
completed. P2: workaround exists. P3: cosmetic.

**Any P0 stops UAT** and is escalated before further testing.

---

## 5. Role sign-off

Signed only after every journey for the role is attempted.

| Field | Entry |
|---|---|
| Role |  |
| Tester (name) |  |
| Date |  |
| Journeys attempted |  |
| PASS / FAIL / BLOCKED / NOT TESTED / N/A counts |  |
| P0 or P1 defects outstanding |  |
| **Verdict** (Accept / Accept with conditions / Reject) |  |
| Conditions, if any |  |
| Signature |  |

---

## 6. Overall UAT outcome

| Field | Entry |
|---|---|
| Roles completed (of 6) |  |
| Journeys passed (of 18) |  |
| Outstanding P0 |  |
| Outstanding P1 |  |
| **UAT verdict** |  |
| Approver |  |
| Date |  |

**Until this table is completed and signed by real participants,
prompt 59 remains NOT TESTED and the release gate must record it as
such.**
