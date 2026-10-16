# UAT Scripts and Sign-off Template (Prompt 59)

**Prepared:** 2026-10-17 · **Baseline commit:** `779d7fc`
**Status: NOT TESTED.** No UAT participant has executed any journey below.

> ### This document contains no results
>
> Every "Actual result", "Status" and "Tester sign-off" cell is
> deliberately blank. They are filled in **by a human tester**, not by
> an agent. Nothing here may be reported as UAT evidence until a named
> person has run the journey and signed the row.
>
> **Automated tests are not UAT.** The platform has 7,197 backend and
> 1,147 frontend automated tests passing, and they prove something
> different: that the code behaves as its authors specified. UAT asks
> whether the *right* thing was specified, judged by someone who does
> the job. One cannot substitute for the other.

---

## 1. How to run this

1. Create a copy of this file per tester, named
   `UAT_<role>_<YYYY-MM-DD>.md`.
2. Work top to bottom. Do not skip a prerequisite.
3. Record what actually happened, including anything surprising, even
   when the step "passed".
4. Mark each step **PASS**, **FAIL**, **BLOCKED** or **N/A**.
5. For a FAIL, capture evidence (screenshot, correlation id from the
   error, approximate time) so it can be traced in the logs.
6. Sign the role summary at the end.

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
| R1 Platform Admin | Operates the platform across tenants | J1, J2, J12, J13 |
| R2 Tenant Admin | Owns one tenant's configuration | J1, J2, J3, J12 |
| R3 SME / Semantic Reviewer | Owns business meaning of the data | J3, J4 |
| R4 Business Analyst | Daily analytical user | J5–J9, J11 |
| R5 Business User | Occasional question-asker | J5, J6, J10, J11 |
| R6 Read-only Viewer | Can see, must not change | J1, J14 |

---

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
| PASS / FAIL / BLOCKED / N/A counts |  |
| P0 or P1 defects outstanding |  |
| **Verdict** (Accept / Accept with conditions / Reject) |  |
| Conditions, if any |  |
| Signature |  |

---

## 6. Overall UAT outcome

| Field | Entry |
|---|---|
| Roles completed (of 6) |  |
| Journeys passed (of 15) |  |
| Outstanding P0 |  |
| Outstanding P1 |  |
| **UAT verdict** |  |
| Approver |  |
| Date |  |

**Until this table is completed and signed by real participants,
prompt 59 remains NOT TESTED and the release gate must record it as
such.**
