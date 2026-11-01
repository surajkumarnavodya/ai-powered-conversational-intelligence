# UAT Readiness — consolidated report and gate

**Phase 12 of 12.** Date **2026-10-31** · branch
`uat-readiness/phase-11-operations` · base commit `b3a9fac` ·
auditor: automated session, per `UAT_READINESS_AUDIT_PROMPT.md`.

This is the consolidated verdict for the twelve-phase UAT-readiness
audit recorded in [`docs/uat-readiness/`](.). It is the **one owner** of
the question *"may business users be let into this system?"* — the
per-phase documents own their own method and evidence, and
[`FINDINGS_REGISTER.md`](FINDINGS_REGISTER.md) owns the per-finding
status. Where this file restates a number, the phase document it came
from is named so there is one place to correct.

**What this supersedes, and what it does not.**
[`docs/PRODUCTION_READINESS_RELEASE_GATE.md`](../PRODUCTION_READINESS_RELEASE_GATE.md)
(2026-10-17) returned *"CONDITIONALLY READY"* for UAT. That verdict
predates Phases 1–11 and in particular predates `AUZ-002`, which was not
known on 2026-10-17 and was reproduced at runtime on 2026-10-28. **This
report supersedes that document's UAT verdict.** It does not supersede
its production verdict (NO-GO), which still stands and is reinforced by
§6 below.

---

## 0. Scope and method honesty — read before the verdict

**What Phase 12 is.** A consolidation and a re-measurement. It re-ran
every gate that can be run in this environment, recomputed the finding
tallies from the register rather than copying a prior phase's prose, and
assessed each gate-checklist item against measured evidence.

**What Phase 12 is not.** It is not a new audit of the product. Four
things follow from that and each matters to how this verdict should be
read:

1. **Every number in §4 was measured on this Windows development
   machine, against local Ollama and four local SQL Server
   connections.** If UAT runs anywhere else, §4's latency and capacity
   rows are not transferable (Phase 11 §3 says the same).
2. **Nine checks could not run here at all** and are recorded as
   *Unverified*, never as passing — §6.3 lists them with the command a
   human must run. Phase 0 §6 opened that list; it has shrunk, not
   closed.
3. **No business user has executed any UAT journey.** The 18-journey
   script
   ([`UAT_SCRIPTS_AND_SIGNOFF.md`](../certification/UAT_SCRIPTS_AND_SIGNOFF.md))
   has every result cell blank. Automated tests are not UAT, and this
   report makes no claim about journeys that nobody has run.
4. **One finding is new in this phase** (`SEC-008`, §7). It was found by
   attempting to tick gate item 4 honestly rather than by auditing, and
   it is fixed in this phase's own commit.

**Tally method.** §2 and §3 are computed from
[`FINDINGS_REGISTER.md`](FINDINGS_REGISTER.md)'s table by parsing it, not
by reading the per-phase summaries. This matters: a first pass of that
script used an ID pattern of three letters and silently dropped every
`BP-*` and `UI-*` row, which understated the open S2 count as 11 when it
is 13.

**The same script was then wrong a second time, and this one is worth
recording because the register was blameless.** Eight rows contain a
pipe inside a code span — `` `Mapped[str \| None]` ``,
`` `_MANAGER = _ANALYST \| {…}` ``, `` `db_datareader\|CR…` `` — and all
eight are **correctly escaped** as `\|`. The tally script split on every
`|`, escaped or not, so it read those rows as having extra cells and a
fragment of prose as their Status. The register renders and parses
correctly; the parser did not honour GFM escaping.

Both passes were re-run with a splitter that only breaks on a pipe not
preceded by a backslash. **Every figure in this report is from that
pass**, and it reproduces the hand-resolved numbers exactly — 140
findings, 1 open S1, 13 open S2, 80 fixed. So the second bug changed no
published number, which was luck rather than care: the five rows whose
Status column the naive split corrupted had been resolved by hand, and
the hand reading happened to be right.

---

## 1. Verdict

> # NOT READY
>
> **1 open S1 and 13 open S2, one red blocking CI gate, and two gate
> items that cannot be satisfied at all today.**

The verdict is driven by the rubric in §5, not by the amount of work
done. Twelve phases fixed 80 findings and that is not the question the
gate asks.

**Four things make it NOT READY rather than READY WITH CONDITIONS.** Each
is a gate item that is false, not merely conditional:

1. **`AUZ-002` (S1) is open and is a cross-tenant *destructive* path.** A
   tenant's admin can list, download **and delete** another tenant's
   documents. Reproduced at runtime (200/200/204), not inferred. Gate
   item 1 is false. This is waivable only by a written scoping decision
   to run UAT single-tenant — see §2.1.
2. **The first question a UAT tester asks fails** (`OPS-008`, S2).
   `.env` names `OLLAMA_MODEL=qwen2.5-coder:7b`, which is not installed;
   `POST /ask` with no explicit model returns `status=failed`. Gate item
   8 is false until an operator fixes the environment.
3. **Accuracy is unmeasured and currently unmeasurable** (gate item 7).
   25 of 32 golden accuracy cases carry no `expected_sql`, no accuracy
   threshold has been agreed, and the only full-corpus benchmark is
   self-disclaimed as stale and predates both a model change and a
   prompt-truncation fix. There is no number to compare a threshold to.
4. **`OPS-015` (S2): 12 of 26 unambiguous golden SQL questions never
   reach the database.** On this hardware the LLM router times out and
   the regex fallback *is* the router (7 of 8 live calls). Journey J4 —
   which the UAT script calls its highest-value row — reaches the
   database on 1 of 4 questions. A tester cannot distinguish this from
   the product being wrong.

**What NOT READY does not mean.** It is not a statement that the security
controls are weak. The opposite is better supported: the SQL validator's
adversarial corpus passes 691 cases across five dialects, with the only
three payloads it accepts being ones it is correct to accept; all 213
operations deny an anonymous caller; no
IDOR was found across two users on conversations, messages, shares or
sessions; masking survives renaming in both dialects as of this phase;
and every blocking gate but one is green. The findings that block are
about **tenant partitioning, environment configuration, routing on this
hardware, and the absence of an accuracy measurement** — not about the
boundary holding.

**The shortest honest path to READY WITH CONDITIONS is in §8.** It is
mostly operator and owner actions, not engineering.

---

## 2. Open S1 / S2

**140 findings total.** 1 open S1, 13 open S2. Every open item below is
**Flagged** — deliberately not fixed because the fix crosses a stop line
(an API contract, a migration, a live prompt, a legal question, or a
product decision) — not forgotten.

### 2.0 Count by phase

| Phase | Open S1 | Open S2 | Fixed in phase (all sev) |
|---|---|---|---|
| 0 — Reconnaissance | 0 | 0 | 4 |
| 1 — Architecture | 0 | **2** | 4 |
| 2 — Security | 0 | 0 | 8 |
| 3 — Authentication | 0 | 0 | 4 |
| 4 — Authorization | **1** | **1** | 1 |
| 5 — Hallucination / grounding | 0 | **4** | 6 |
| 6 — Loop control | 0 | 0 | 8 |
| 7 — Context engineering | 0 | 0 | 5 |
| 8 — Best practices | 0 | **1** | 9 |
| 9 — UI / UX | 0 | **1** | 11 |
| 10 — Documentation | 0 | **1** | 10 |
| 11 — Operations | 0 | **3** | 9 |
| 12 — This report | 0 | 0 | 1 (`SEC-008`) |
| **Total** | **1** | **13** | **80** |

Severity totals across all 140: S1 3 (2 fixed), S2 31 (18 fixed), S3 67
(44 fixed, 1 partly), S4 30 (15 fixed), plus 9 rows that are positive
results or unverified checks rather than defects.

Six findings carry a dual phase label, because they were raised in one
phase and closed in a later one — `AUZ-002` and `AUZ-003` (latent in
Phase 1/3, confirmed at runtime in Phase 4), `BP-001` and `BP-002`
(raised Phase 0, fixed Phase 8), `BP-003` (Phase 3/4) and `HAL-003`
(early Phase 5). Each is counted **once**, against the phase that
confirmed or closed it, which is why this table's rows differ slightly
from the per-phase documents' own summaries.

### 2.1 The open S1

#### `AUZ-002` — document, policy and media stores are not tenant-partitioned

**Severity S1. Phase 1 (latent) → Phase 4 (confirmed at runtime).**

`rag/store.py` and `media/store.py` have **no `tenant_id` column** and
not one entry point that accepts a tenant. `api/documents.py`,
`api/media*.py` and `api/attachments.py` contain the string `tenant`
**zero** times each. Measured 2026-10-28: a `tenant_b` admin listed,
downloaded and **deleted** a `default`-tenant document — HTTP
200 / 200 / 204.

This violates CLAUDE.md §5.3 directly, and it is the only finding in the
audit that is both cross-tenant **and** destructive. Three
`xfail(strict=True)` tests in
`tests/security/test_authorization_matrix.py::TestCrossTenantDocumentAccess`
hold the line, so a fix cannot land silently.

**Owner decision needed — one of two, in writing:**

| Option | What it costs | What it leaves |
|---|---|---|
| **A. Migrate before UAT** | A `tenant_id` column on the document and chunk tables, an Alembic revision, a backfill to `DEFAULT_TENANT_ID`, and a **required** (not defaulted) tenant parameter on five `rag.store` entry points — plus the same work for `media/store.py` | Nothing. This closes it |
| **B. Run UAT single-tenant and record it** | Nothing to build. Requires that the UAT environment provision exactly one tenant, that no second tenant be created for the duration, and that this be written into the UAT entry criteria | The defect, unexercised. It must not be signed off as "tested" |

**Recommendation: B for UAT, A before production.** The *required, not
defaulted* detail in option A is load-bearing: CLAUDE.md §5.4 records
that `SEC-006`'s optional parameters mean a new caller silently gets the
weaker path, and this would be the second instance of that shape.

**What must not happen:** running multi-tenant UAT on this code. Two
tenants in the same UAT environment can read and destroy each other's
documents, and the audit log will show the deletion as authorized.

### 2.2 The open S2s

Ordered by whether they block UAT **entry**, which is the distinction an
owner needs and which severity alone does not give.

#### Blocks UAT entry — 6

| ID | Phase | Finding | Owner decision needed |
|---|---|---|---|
| **`OPS-008`** | 5 | `.env` names a default model that is not installed, so a question with no explicit `model` fails in ~1.7 s. The app starts anyway, although `GET /models` already reports `{"installed": false, "is_default": true}` | **Operator, not owner.** `ollama pull qwen2.5-coder:7b` or set `OLLAMA_MODEL=llama3.1:8b`. Then re-check `GET /models`. Note `llama3.1:8b` answers more questions correctly but is slower on this hardware (CLAUDE.md §13) |
| **`OPS-015`** | 11 | The LLM router gets 5 s and routing costs 31–40 s here, so the regex fallback **is** the router (7 of 8 live calls). It sends **12 of 26** unambiguous golden SQL questions somewhere other than the database. J4: 1 of 4 reach the DB. J11: 0 of 2. `web_search` is tested before `sql_database` and matches a bare `price`, so "…list price and standard cost for every bike" went to Tavily three times, each returning `status="succeeded"` after 184 s with no rows and an **empty** `synthesized_answer` | **Owner.** Either reorder/narrow the fallback patterns and re-measure, or raise `router_timeout_seconds` to fit this hardware, or **turn `ENABLE_WEB_SEARCH=false` for UAT** (cheapest, removes the dominant mis-route). Pinned by 8 `xfail(strict=True)` in `tests/test_router_fallback_reaches_the_database.py` |
| **`OPS-003`** | 1 | Three fail-closed startup controls are inert outside `ENVIRONMENT=production`, which defaults to `development`. Measured live 2026-10-28: the app logged `event=db_write_privileges_detected severity=critical` for **all four** connections and started anyway. So the database-role layer CLAUDE.md §5.1 calls the real boundary is **absent on every database**, and `agent/sql_validator.py` is the only thing between a question and a write | **Owner + DBA.** Point every UAT connection at a genuinely read-only role ([`docs/DBA_READONLY_ACCOUNTS.md`](../DBA_READONLY_ACCOUNTS.md) has the `GRANT`s) **and** set `ENVIRONMENT=production` on the UAT host — which then also requires identity configured and `MALWARE_SCAN_PROVIDER=clamav`. Decide whether UAT runs with those on |
| **`OPS-022`** | 11 | Onboarding's published semantic meaning is filed under a per-job `database_id` (`My_Warehouse_<job8>`) while retrieval asks for the `DB_CONNECTIONS` name (`my_warehouse`). **187** collections hold confirmed business truth that never reaches a prompt. J2 and J3 publish successfully; J4 cannot consume it, and every acting consumer (`api/kpi_monitoring.py`, `api/decision_center.py`) answers 404 for a per-job id | **Owner.** Not fixable as a code change alone — it needs both schemes reconciled **and** 187 collections plus their `SemanticCatalogEntry.database_id` rows backfilled. **Interim, required:** tell testers that J2+J3 publish and that **J4 must not be signed off as evidence that the catalog works** |
| **`OPS-012`** | 11 | `question_timeout_seconds` is 120 while `request_timeout_seconds` is 600 — a 5× asymmetry between two routes running the identical pipeline, and `.env` sets neither. Measured: `POST /ask` succeeded at 100.4 s / 126.7 s / 184.1 s while the same class of question via `POST /questions` returned `status="timeout"` at 120.4 s | **Owner.** Raise `QUESTION_TIMEOUT_SECONDS` to at least `REQUEST_TIMEOUT_SECONDS` for UAT, or document that the queue is out of scope. On hardware whose `generate_sql` p50 is 119 s the measured distribution straddles the queue's budget, so the same question succeeds or fails by route |
| **`HAL-010`** | 10 | The display-name repair gate can spend the whole retry budget on a correct query. *"What are the top 5 products by total sales?"* ended after **195.9 s** and 2 retries with "the same query came back unchanged"; all three attempts were rejected by `enforce_display_names` (`key_instead_of_display_name`) although the statement already selects and groups by `EnglishProductName` — the guard objects to `ProductKey` *also* being projected | **Owner.** Either relax the guard to allow a key alongside its display name, or accept that an ordinary top-N question can fail. This is a plain, likely UAT question, so accepting it means testers will meet it |

#### Does not block UAT entry — 7

Each is real and each is mitigable by configuration or by scope for the
duration of UAT.

| ID | Phase | Finding | Owner decision needed |
|---|---|---|---|
| `AUZ-003` | 4 | Neither account nor tenant suspension reaches the `agent.authz` surface. Measured with an untouched token, user **and** tenant suspended: `/auth/me`, `/conversations`, `/navigation` → 401, but `GET /models`, `/sources`, `/schema/tables` and **`POST /ask`** → 200, question answered. Bound is one access-token lifetime (default 15 min, and its `Field(...)` sets **no upper bound**); refresh refuses, so it self-heals | **Accept for UAT with the window documented**, leaving `access_token_expire_minutes` at 15 as a conscious choice; take Option B (the check in `api/authz.py` with a short per-process cache, §4.2 of Phase 4) before production |
| `HAL-004` | 5 | The web-search path has **no grounding gate at all** — `assess_grounding` runs only in `_run_rag_node`. Reproduced: one real Reuters result retrieved, the answer cites `bloomberg.com/news/invented-page` "published on 2026-02-14", `status=succeeded`, no `grounding` key. The Sources panel lists Reuters while the answer body carries the Bloomberg link, so the two halves of the UI disagree | **Turn `ENABLE_WEB_SEARCH=false` for UAT** (which `OPS-015` also argues for), or extend `assess_grounding` to the web node and accept an unmeasured change to web answers |
| `HAL-006` | 5 | The input guard does not run on the no-source route. `route_after_router` can send a question straight to `synthesis` → END; measured, five structurally different inputs (direct injection, off-topic, DELETE, DROP TABLE, unanswerable forecast) all returned 200 / `succeeded` / `rejection_reason: null`. **Not a data-safety bypass** — that route does no retrieval, generation or execution — what is lost is **detection and the audit record**, so UAT step **J12.3** ("trigger a refused action, then find it in the audit") cannot pass there | **Owner.** Wire `check_input` into the orchestrator entry, or rewrite J12.3 to use a route where the guard runs. Pinned by 2 `xfail(strict=True)` |
| `HAL-007` | 5 | Terse questions are answered **wrongly** in a way no grounding check can catch. Live on `llama3.1:8b`: *"Most popular products"* returned the ten most **expensive**; *"Top selling products"* returned **334 rows** from a `ROW_NUMBER() OVER (PARTITION BY ProductKey ORDER BY COUNT(ProductKey))` that ranks nothing; *"Worst selling products"* returned **0 rows** — a false empty. Provenance held and the insight was honest about what was computed; grounding verifies an answer against the *result*, never the result against the *question* | **Owner.** This is model capacity. Either accept it and brief testers that terse questions need specifics, or change the model and re-measure. All five phrasings are a standing dataset (`eval/uat_golden`, `uat_j15_*`) |
| `BP-012` | 8 | **`pymupdf` is AGPL-3.0** (`Dual Licensed — GNU AFFERO GPL 3.0 or Artifex Commercial License`), imported by `attachments/processors/pdf_processor.py` and `rag/ingestion.py`, and `enable_chat_attachments` defaults to **`True`** — so it is not behind an off-by-default flag. `piper-tts` is GPL-3.0-or-later on the voice path. `LICENSE` here is MIT and both ship in the single Docker image. AGPL's network clause obliges offering the whole work's source under AGPL for a network-served application | **Legal, not engineering.** Obtain an Artifex commercial licence, relicense, or replace with `pypdf` (BSD-3-Clause, **already** a declared dependency used elsewhere in `attachments/`). Does not block UAT on a closed internal host; blocks distribution |
| `UI-013` | 9 | The cloud-backed live-caption exception is disclosed **only in source**. `useSpeechRecognition.ts`'s docstring is explicit and correct that Chromium sends microphone audio to the browser vendor's speech service; the authoritative transcript is local `faster-whisper`, so the design is sound. But all nine user-facing `voice.*` strings were measured and **none** mentions the cloud, the vendor, or that audio leaves the machine — on a platform whose stated premise is "no network calls for the LLM" | **Turn `ENABLE_VOICE_MODE=false` for UAT** — which the Phase 11 flag profile already recommends, since no journey exercises it — then add the disclosure in five locales before enabling it. Proposed copy is in the register row |
| `ARC-002` | 1 | Multi-database auto-routing has no data-presence signal and no user override. `embeddings/retriever.py` scores DDL similarity only; the sole emptiness check is `collection.count() == 0` — an unbuilt **index**, not empty **tables**. `AskRequest` has no `database` field | **Configure one database for UAT** (or accept that a question may be answered from the wrong one), then decide whether `AskRequest` gains an explicit `database` field |

---

## 3. Waivers requested

These are the S3 and S4 items this audit proposes be **accepted** for
UAT rather than fixed first. Each row states the residual risk in terms
of what a UAT tester or an attacker could actually do. **No waiver here
has been signed** — the sign-off block is §9.

Two are called out first because they are the only ones with a security
or release-process edge.

### 3.1 `OPS-006` — the `detect-secrets` blocking gate is red

**S3. Phase 2, re-measured Phase 11 and again in this phase.**

| | |
|---|---|
| Baseline | 44 files, 672 reviewed findings |
| Fresh scan, tracked files only | 94 files, 1,469 findings |
| **Un-baselined** | **85 findings across 54 tracked files** |

By category: 60 in `tests/` fixtures, 10 Alembic revision hex, 10 i18n
label strings in `{es,fr}/translation.json`, 5 doc examples. The one
production hit is `identity/security.py:363`'s `_DECOY_PASSWORD`, the
deliberately public constant `AUN-001`'s timing-oracle fix spends
Argon2id work on, already carrying `# nosec B105`.

**Risk if waived:** the gate is red, so it is currently proving nothing
— a genuinely leaked secret would land among 85 known-benign findings
and not be distinguishable. The risk is therefore not "a secret is
leaked" (the full triage found none) but **"the control that would tell
us is off"**. It also means any claim that "all CI gates are green" is
false, which is gate item 2.

**Waiver recommended: no.** This is the cheapest open item in the audit
to close and it is the only red blocking gate. It must be refreshed on
**Linux** — `detect-secrets` writes OS-native path separators, so a
baseline regenerated from this Windows checkout has backslash keys CI's
forward-slash scan can never produce, and hand-normalising them would be
the hand-edit CLAUDE.md §12 forbids for a tool-generated file:

```bash
detect-secrets scan $(git ls-files) --baseline .secrets.baseline
detect-secrets audit .secrets.baseline
python scripts/check_secrets_baseline.py <the pre-scan copy>
```

### 3.2 `SEC-001` — `GET /health` discloses internal topology unauthenticated

**S3. Phase 0 / Phase 2. Companion: `OPS-026` (S4) — it also discloses
the schema-index directory's absolute path.**

`/health` names the configured databases, their table counts, the Ollama
host and the vision model to anyone who asks. It cannot simply be put
behind auth: `AuthGate.tsx` and `Register.tsx` read `local_auth_enabled`,
`google_signin_enabled` and `google_client_id` **before** anyone is
logged in, so gating the whole endpoint breaks sign-in. The proposed
split (public `/health` with auth-mode flags only; operational detail
behind `GET /health/detail`) is in the register.

**Risk if waived:** low on a closed UAT host reachable only by the test
group; **meaningful if the UAT host is reachable beyond it**, because the
response is useful reconnaissance — it names the real databases and
confirms which optional subsystems are on.

**Waiver recommended: yes, conditional on the UAT host not being
internet-reachable.** If it is, take the split first.

### 3.3 Waivers recommended without reservation

Grouped by what the residual actually is. All are S3 or S4, all are
recorded in the register with evidence, and none is a security boundary.

**(a) Known-wrong behaviour a tester may meet — brief them, do not fix now**

| ID | Sev | Residual risk if waived |
|---|---|---|
| `HAL-009` | S3 | A write request (*"Delete all the discontinued products"*) is refused **in effect** — no SQL generated, no write — but the message is the generic "couldn't match that question to any data source". UAT step **J7.1** expects "refused; no write occurred" and a tester cannot tell a safety refusal from a routing miss. Brief them, or rewrite J7.1 |
| `HAL-008` | — | First concrete trigger of the documented aggregate-over-a-restricted-column residual: *"average employee satisfaction score per product"* tried `AVG(CAST(DimEmployee.Gender AS INTEGER))` — an aggregate over a protected characteristic. The run **failed** and no answer was presented, so the control held. The residual is the policy question CLAUDE.md §13 holds open, not a new defect |
| `AUZ-004` | S3 | A `platform_admin`-only account is refused 11 routes a plain `admin` reaches, **including the chat screen**, because `agent.authz.ROLE_PERMISSIONS` does not know that role. Fail-closed, so not a hole — but the highest-privilege role cannot open the product's main screen. Mitigated free: the R1 UAT account already holds `admin` as well |
| `OPS-023` | S3 | The R3 (SME reviewer) account is granted `manager`, which is a strict **superset** of `analyst` on `identity.rbac` (24 vs 20 permissions — it can read any account, revoke any session, read any audit record and open the admin dashboard) and grants **nothing** on `agent.authz` (7 vs 12 — it loses `view_restricted_columns`, `policy_rag_query`, `documents_read_sensitive`). Simultaneously over- and under-granted. **This one is a measured operator action, not really a waiver** — grant `analyst`, remove `manager` |
| `UI-010` | S3 | The streaming progress badge names the node that just **finished**, not the one running — measured as 97 s reading "plan query" (0.05 ms) while `generate_sql` (96.8 s) ran. Testers will read the badge as wrong. Fixing it changes the SSE contract |
| `UI-011` / `UI-015` / `UI-001` | S4 | Masked cells show `[restricted]` with no explanation; an offline reload says "we couldn't check your access"; one empty-result message is hard-coded English. Cosmetic, but each will be reported as a bug by a tester |
| `UI-014` | S3 | Chart currency is hardcoded `USD` with no setting, in a five-locale product. Wrong for any non-USD dataset |
| `UI-008` | S3 | 30 declared-token colour pairings are below WCAG AA and are pinned in a no-growth baseline. Accessibility debt, measured, not growing |

**(b) Accuracy and retrieval behaviour — decide with a benchmark, not a patch**

| ID | Sev | Residual risk if waived |
|---|---|---|
| `CTX-007` | S3 | Schema retrieval is punctuation-sensitive: "top selling products" retrieves 5 tables, with a trailing period 8, with "?" 6. Genuinely two-sided — the trailing-period variant pulls in `FactInternetSales`, arguably the *more* correct table. **Do not "just add a `.strip()`"**; decide with `scripts/run_benchmark`. Pinned by 3 `xfail(strict=True)` |
| `CTX-003` | S3 | Neither routing prompt states any safety invariant, which falsifies `governance_center/safety.py`'s own docstring. Bounded: `router_node`'s RBAC filter is the real control and a rewritten source query still passes the validator and the result gate. Not fixed because editing a live routing prompt changes routing behaviour that nothing measures |
| `CTX-008` / `CTX-009` / `CTX-010` | S4 | Router prompt spends 6,956 chars on one section; golden examples dedup exactly but not near-identically; the serving model is in the metrics rollup but not the response. Efficiency and observability, no behavioural risk |
| `DOC-003` | S4 | `docs/EVALUATION_CURRENT.md`'s figures predate the current default model. **This is the evidentiary half of gate item 7** and is why that item fails; it is listed as a waiver candidate only because the *document* correctly discloses its own staleness |
| `DOC-011` | S3 | CLAUDE.md §13's account of the terse-question gap is stale in both directions (the false-refusal half no longer reproduces; `HAL-007` replaced it with something worse) |

**(c) Platform, process and hygiene**

| ID | Sev | Residual risk if waived |
|---|---|---|
| `OPS-014` | S3 | `/health` does not check the identity database at all, so an identity outage reports healthy. A load balancer would keep sending traffic to a server that cannot authenticate anyone |
| `OPS-020` | S3 | Admission control admits roughly **50×** what this hardware serves (`max_concurrent_ask_requests` 50, per-user queue 100, measured capacity **1**). The first control a burst meets is a 300 s timeout, then the circuit breaker — which then penalises the *next* user. **Actionable form for UAT: stagger the testers**, do not change the default |
| `LOP-008` | S3 | A driver error reaching the retry prompt and `AskResponse.error_history` can contain row values — `redact_secrets` redacts connection secrets, not data. Not cross-privilege, but a path by which a value the *result* gate would have masked appears in an error message. Fixing it is a product decision: the error text is what makes self-correction work |
| `LOP-010` | S4 | `HARNESS_MAX_TOKENS` is 0, so the per-request token budget is not enforced. Cost control only; the per-call and deadline budgets do apply |
| `AUZ-005` | S3 | Any `user` can write a golden example that steers every other user in their tenant, with no review step. Cannot escalate privilege — the validator and result gate still apply — so the exposure is to answer **accuracy**. Accepting is reasonable (it is the thumbs-up feature) but should be a recorded choice |
| `ARC-001` | S3 | Three `async def` upload handlers run heavy synchronous work on the event loop. A large upload stalls the loop; bounded by the upload size caps |
| `ARC-005` / `BP-014` | S4 | 56 package-level import cycles via shared leaf types; 11 modules over 1,500 lines. Maintainability |
| `BP-010` | S3 | 593 lines of dead code at the repo root (`ai_generator_client.py`) that would, if imported, bypass `security/outbound_url.py`, bypass `Settings` for credentials, and call `logging.basicConfig()` at import time. **Zero importers**, pinned unwired by a guard test |
| `BP-013` | S4 | `LICENSE` still reads `Copyright (c) 2026 <Your Name>`. Trivial, and it interacts with `BP-012` |
| `DOC-008` / `DOC-009` / `DOC-010` / `DOC-022` / `DOC-023` / `DOC-024` | S3/S4 | Documentation gaps: two surprising auth behaviours unstated; a probe-method caveat; **1,120 translation keys exist only in English** with nothing comparing locales; `USER_GUIDE.md` pinned at 2026-10-15; `make load-test` has no `tasks.ps1` equivalent on a Windows-primary project; CLAUDE.md is 93,619 chars against its own 60,000 guidance. `DOC-010` is the one with user-visible reach in a five-locale UAT |

**Count: 36 findings proposed as waivers** — **1 not recommended**
(`OPS-006`, §3.1), **2 conditional** (`SEC-001` and `OPS-026`, §3.2),
**33 recommended** (§3.3). `OPS-023` is on the list but is really an
operator action, and `DOC-003` is listed for completeness although it is
the evidentiary half of a failed gate item rather than a waivable
residual.

**Four open items are deliberately *not* here**, so the arithmetic
reconciles: `OPS-001`, `OPS-002`, `OPS-004` and `CTX-011` are
environment-blocked *Unverified* checks, not accepted risks. A check that
was never run cannot be waived — it is listed in §6.3 with the command
that would run it.

---

## 4. Re-measured baseline vs Phase 0

Every row below was run **in this session on 2026-10-31** at `b3a9fac`,
each command exactly as `.github/workflows/ci.yml` runs it. Phase 0's
column is 2026-10-25.

> **Measurement window, and why it is stated.** These figures were taken
> against a working tree whose only modifications were this phase's own
> (one test file and four documents). **While this phase was finishing, a
> concurrent session began an unrelated change in the same working tree**
> — a local-Ollama moderation provider, 17 files including
> `config/settings.py`, `moderation/` and `rag/llm.py` — and `black
> --check .` now reports one file needing reformatting, in
> `config/settings.py`, which belongs to that work and is **deliberately
> not touched here**. Phase 7's process note already recorded this
> hazard: *"if two sessions must share a repository, give each one a
> worktree first and stage explicit paths, never `-A`."* So read this
> table as measured at the stated commit with this phase's changes only.
> Anyone re-running it mid-way through that other change should expect
> `black` red for a reason that is neither this phase's nor a defect.

### 4.1 Gates

| Gate | Command | Phase 0 (10-25) | **Phase 12 (10-31)** | Exit | Δ |
|---|---|---|---|---|---|
| Backend tests | `pytest -q` | 8,297 passed / 0 failed, 11 deselected (521.8 s) | **9,687 passed / 0 failed**, 16 skipped, 11 deselected, **18 xfailed** (709.1 s) | 0 | +1,390 passed |
| Lint | `ruff check .` | **1 finding** (`I001`) | **0 findings** — "All checks passed!" | 0 | **red → green** (`BP-002`) |
| Format | `black --check .` | clean, 1,665 files | clean, **1,693 files** | 0 | +28 files |
| SAST | `bandit -c pyproject.toml -r . -f txt` | 0 findings, 56,950 LOC (12 named packages) | **"No issues identified"**, **119,611 LOC**, 0 `# nosec` skipped, 45 disabled-by-id | 0 | **scope ×2.1** (`BP-005`) |
| Types (production) | `mypy` over 41 packages | *not a gate then* | **"no issues found in 482 source files"** | 0 | new blocking gate (`BP-001`) |
| Types (all, report-only) | `mypy .` | 723 errors / 129 files | **751 errors / 134 files** of 913 checked — **743 in `tests/`, 8 in `eval/`, 0 in production** | 1 | +28, all in tests |
| Python deps | `pip-audit -r requirements-dev.txt` + 6 justified ignores | **could not run** (`ReadTimeout` to pypi.org) | **"No known vulnerabilities found, 7 ignored"** | 0 | now measurable |
| Secret scan | `detect-secrets` vs baseline | *not counted as a gate* | **RED — 85 un-baselined findings across 54 tracked files** | 1 | newly enumerated, still red |
| Frontend tests | `npm run test` | 1,491 / 103 files | **1,551 passed / 107 files**, 0 failed (39.5 s) | 0 | +60 |
| Frontend types | `npx tsc -b --noEmit` | clean | clean | 0 | — |
| Frontend lint | `npm run lint` (oxlint) | warnings only | **32 warnings, 0 errors** | 0 | — |
| Frontend deps | `npm audit --omit=dev` | 0 vulnerabilities | **0 vulnerabilities** | 0 | — |
| Frontend build | `npm run build` | succeeded | **succeeded** (35 precache entries, 3,550 KiB) | 0 | — |
| Docker build | `docker build .` | not run | **not re-run this session** — last measured green after `OPS-021` (2026-10-30) | — | Unverified here |

**9 of 10 blocking gates are green. The tenth is `detect-secrets` (§3.1).**
The blocking set is `ruff`, `black`, `bandit`, `pytest`, `pip-audit`,
`detect-secrets`, production `mypy`, frontend `vitest`, `npm audit`, and
the `docker build` step inside the otherwise-advisory `docker-scan` job.
Four steps are non-blocking by dated decision: `gitleaks`, the
report-only full-tree `mypy`, the Trivy scan, and the load-test smoke
scenario.

**Two things about the test numbers rather than just the numbers.**

The **+1,390** backend tests are attributable to the phases: the growth
is the tests the fixes shipped with. The **xfail count rose from 0 to
18** and that is deliberate, not decay — each is an `xfail(strict=True)`
pinning an open finding so a fix cannot land silently: `OPS-015` 8,
`CTX-007` 3, `AUZ-002` 3, `HAL-006` 2, `OPS-022` 2. Each fails the day
its finding is fixed, which is the point. **What would be decay is a
rising *skip* count**, and that is unchanged at 16.

`bandit`'s figure is not comparable to Phase 0's as a quality signal.
Phase 0 ran it over twelve named packages — 48.6% of the code, never
including `identity/`. It is now scoped by exclusion from
`[tool.bandit]`, so a new package is covered the day it is created.
"Green" now means the whole tree.

### 4.2 Coverage

Measured in Phase 8 (2026-10-29) with `pytest-cov==7.0.0` over the whole
mocked suite. **Not re-run in this phase** — a coverage run roughly
doubles a 709 s suite and nothing in Phases 9–12 changed the
security-package code it measures.

| Package | Coverage |
|---|---|
| `governance/` | **98.7%** |
| `identity/` | **97.0%** |
| `security/` | **96.0%** |
| `api/` | **94.5%** |
| `guardrails/` | **93.3%** |
| **all five** | **95.4%** (12,576 statements, 578 missed) |
| `agent/sql_validator.py` | **94%** |
| `agent/input_guard.py` | **98%** |

Coverage had **never been measured in this project** before Phase 8.
Every security-critical package clears 90%; **13 individual files do
not** — worst `identity/db.py` 69.6%, `api/jobs.py` 69.9%,
`identity/email.py` 71.4%, `api/chat_persistence.py` 76.1%. **No
coverage gate was added to CI on purpose**: picking a threshold is a
product decision, not an auditor's ratchet.

### 4.3 Eval metrics — the weakest column in this table

| Dataset | Cases | Status |
|---|---|---|
| Full Text-to-SQL benchmark (`eval/benchmark/`) | 57 | **STALE.** Last real run 2026-09-16 (`run_20260916T064354Z`): 92.5% execution / 42.3% result-set / 50% final accuracy. `docs/EVALUATION_CURRENT.md` disclaims these in its own opening — they predate commit `9706fa1`, which fixed every Ollama call silently truncating 63% of its prompt, and generation quality is exactly what this measures. **Not re-run**: needs live Ollama + `AdventureWorksDW2025`, ~80 s/question, and `OPS-008` means the configured default model is absent |
| UAT golden set (`eval/uat_golden/`) | **50** — 32 accuracy + 18 behaviour, 11 journeys (J4–J11, J15, J17, J18), 45 `mssql` + 5 `postgresql`. **Counted by loading the YAML this phase**; CLAUDE.md §13 says 57, which is wrong | **Behaviour and structure gate only.** `tests/test_uat_golden_set.py` (18 tests) passes and asserts *integrity* — loads, unique ids, every journey tag is a real journey parsed from the UAT script, both dialects present, every refusal case names its reason. **28 of the 32 accuracy cases are marked `GOLD SQL REQUIRED` and _zero_ carry an `expected_sql` value** — the only two textual matches for that key in the file are comments. So **not one accuracy case is machine-scorable**, and the set asserts nothing about accuracy, which no mocked test can |
| Context-resolution benchmark | 65 hand-labelled | **Passing in CI** (`tests/test_context_resolution_benchmark.py`), no infrastructure needed. The one place in this repo where routing-adjacent accuracy is quantified rather than only pinned |
| Router behaviour vs the golden set | 26 unambiguous | **46% mis-routed** — `OPS-015`, 12 of 26 never reach the database |
| Retrieval recall / precision | — | **UNVERIFIED.** No labelled fixture set exists; `docs/retrieval-evaluation.md` says so in its own opening (`CTX-011`) |

**So there is no current accuracy number for this product.** That is the
single most consequential gap in this report after `AUZ-002`, and it is
what makes gate item 7 unsatisfiable rather than merely unmet.

### 4.4 Latency and capacity

Phase 11 §3, this hardware (4 GB GPU holding 2.2 GB of a 4.9 GB model).
**Not re-measured in this phase** — it needs the live stack and nothing
on the model path changed.

| Run | Users | p50 | max | `succeeded` | Throughput |
|---|---|---|---|---|---|
| 1u | 1 | **79.1 s** | 79.1 s | 1 / 1 | 0.76 q/min |
| 3u | 3 | **193.5 s** | 202.7 s | 2 / 3 | 0.59 q/min |
| 6u | 6 | **318.4 s** | 326.8 s | **1 / 6** | 0.18 q/min |

A cold-model single question took **126.7 s** (12.2 s of it model load).

**This hardware serves one concurrent question.** Adding users subtracts
throughput. At six, five of six failed on the 300 s per-call timeout and
the provider circuit breaker then opened, so **a seventh user arriving
after the burst is refused for the cooldown with no outage having
occurred**.

Stage p50 / p95 over 19 questions, which is the whole explanation:

| Stage | p50 | p95 |
|---|---|---|
| `generate_sql` | **119,016 ms** | 303,800 ms |
| `generate_insight` | 0.14 ms | 80,707 ms |
| `retrieve_schema` | 1,842 ms | 9,407 ms |
| **`execute_sql`** | **8.06 ms** | 140 ms |
| `validate_sql` | 7.53 ms | 58.7 ms |

`generate_sql`'s p50 is about **14,000×** `execute_sql`'s. The database
is not the problem and neither is the connection pool: a question holds a
connection for ~8 ms out of ~80–320 s.

### 4.5 Route surface

| | Phase 0 | Phase 12 |
|---|---|---|
| Operations in the live OpenAPI document | 212 across 187 paths | **213** (Phase 6 count) |
| Operations denying an anonymous caller | 52 of 54 static GETs probed | **all 213**, 401 never 403, with an exact public allowlist of **16** |
| IDOR across two users | not tested | **none found** on conversations, messages, shares, sessions |
| Mass assignment | not tested | **blocked everywhere** by `extra="forbid"` |

---

## 5. Gate checklist

**All must be true for READY.** Status is measured, and a ☐ names what
is false.

### ☐ 1. 0 open S1; 0 open S2 (or explicitly waived in writing by the product owner)

**FALSE. 1 open S1 (`AUZ-002`), 13 open S2.** No waiver has been signed
by anyone. §2 lists each with the decision needed. This is the gate's
primary failure.

### ☐ 2. All CI gates green without blanket suppressions

**FALSE — 9 of 10 blocking gates green; `detect-secrets` is red**
(§3.1, 85 un-baselined findings, no leak found, stale baseline,
refreshable only on Linux).

The "without blanket suppressions" half is **satisfied**, and that is
worth stating because it is the part most easily faked:

- `pip-audit`'s ignore list is **6 individually justified advisory IDs**,
  rebuilt from 14 when ten went stale, not a `continue-on-error`.
- `bandit` reports 0 findings with **0 `# nosec` skipped** and is scoped
  by exclusion, so new packages are covered automatically. The 45
  disabled-by-id suppressions are line-scoped with written
  justifications, and stripping them makes the gate exit 1 again
  (verified in `docs/security/VERIFICATION_RE_REVIEW.md`).
- `ruff`'s single standing finding was fixed with ruff's own import sort,
  **not** a `# noqa`.
- `tests/test_ci_gate_coverage.py` is what keeps all of that honest: it
  reads `ci.yml` itself and fails when the production `mypy` step is
  made report-only, when a tracked package is neither gated nor
  explicitly exempt, when the gate names a package that no longer
  exists, when `bandit`'s exclude list drops a first-party package, when
  any step uses a floating action tag, or when the `docker build` step
  stops blocking.

  **One small correction belongs here rather than in a finding.**
  `ci.yml:329` cites
  `tests/test_mypy_gate_covers_every_production_package.py` as the test
  that holds the typed package list complete. **That file does not
  exist** — the assertions are in `test_ci_gate_coverage.py`'s
  `TestTheProductionTypeGateEnumeratesEveryPackage`. The control is real
  and passing; only the comment's pointer is wrong. Noted
  because a stale pointer in a CI comment is exactly how
  "a gate nobody enumerates is a gate nobody measures" starts, and this
  report cited it before checking.

### ☑ 3. Authz matrix test passing for all roles × routes

**TRUE, with its open finding pinned rather than hidden.**
`tests/security/test_authorization_matrix.py` — 20 test functions
expanding to **259 collected cases** — passes in the full run, including
3 `xfail(strict=True)` holding `AUZ-002`'s cross-tenant document path.
(The distinction matters when reading these numbers: this suite is
heavily parameterised over roles × routes, which is the whole point of
calling it a matrix. Every count in this section is the **collected**
count, measured this phase; where an earlier phase document quotes a
smaller number it was counting functions.) Phase 4 derived the matrix from the **live
app**, not from a list, because this codebase authorizes in three
structurally different places (a dependency reading the JWT, a
dependency re-reading the database, and a call *inside* the handler) —
any technique that sees only one reaches a wrong conclusion in both
directions. Phase 9 cross-checked role-based navigation against 16 real
routes × 7 roles in a real browser: **0 mismatches**.

**What the ☑ does not cover:** `AUZ-003` (suspension does not reach the
token-only surface) and `AUZ-004`/`OPS-023` (roles absent from
`ROLE_PERMISSIONS`) are *pinned as current behaviour* by this suite. The
tests pass because they assert what the system does, and two of those
things are findings.

### ☑ 4. SQL-safety and masking attack suites passing on SQL Server **and** PostgreSQL

**TRUE as of this phase — it was not before, and that is `SEC-008`
(§7).**

| Suite | Dialect coverage | Result |
|---|---|---|
| `test_sql_validator_attack_matrix.py` | **5 dialects** — `tsql`, `postgres`, `mysql`, `oracle`, and sqlglot's generic | **691 cases pass** (collected count, verified this phase). A corpus of **107 payloads** — counted from the file's own eight payload tables this phase; Phase 2 recorded 86 and it has grown since — run against every dialect that can parse them, asserting none is accepted. **Five were accepted or crashed when it was written** and each now names the guard it caused — `SEC-002` parser-recursion exhaustion (a 90-byte input made the gate raise `RecursionError` instead of refusing), `SEC-003` lock escalation, `SEC-004` nested-SQL evaluators (the one real **gate bypass**), `SEC-005` query hints. **Three payloads are accepted and that is correct**, each pinned by `TestPayloadsThatAreCorrectlyAccepted` so the next sweep does not re-report them |
| `test_masking_survives_renaming.py` | was **`postgres` only**; now **`postgres` + `tsql`** | **52 collected, up from 37** (measured both ways). 9 T-SQL rename/wrapper forms, the `CREDENTIAL`-withheld case across 3 roles, an over-blocking counter-case, and the aggregate residual pinned in both dialects |

**Caveat, stated rather than implied:** this is dialect coverage at the
**parser and gate** level, which is where these controls live. Neither
suite has been exercised against a **live PostgreSQL instance** —
`eval/uat_golden/seed_postgres_uat.sql` and the `postgres_uat`
connection recipe exist (`OPS-016`) but the five Postgres golden cases
have never been executed. §6.3 item 5.

### ☑ 5. Prompt-injection (direct + indirect) suite passing

**TRUE**, with one documented gap pinned open.

| File | Cases (collected / functions) | Channels |
|---|---|---|
| `tests/test_adversarial_input.py` | **89** / 41 | direct injection, homoglyphs, control characters, poisoned schema values, poisoned values vs. the validator, the off-topic backstop |
| `tests/test_indirect_channel_injection.py` | **11** / 11 | golden examples, business context, web search, RAG documents, OCR/captions, multi-source synthesis |
| `tests/security/test_prompt_injection_multilingual_and_encoded.py` | **14** / 7 | encoded payloads, non-Latin scripts, the structural backstop |

**114 collected cases across the three**, all passing in the full run.
Phase 2's own table reads 41 / 11 / 7 because it counted test
*functions*; both numbers are given here so neither looks like a
regression against the other.

Phase 2 additionally verified CLAUDE.md §5.5's actual *claim* — that each
untrusted channel is framed **as data** at the prompt-construction site —
for all ten channels it names, including database cell values. The claim
holds.

**The gap: `HAL-006`** (§2.2) — the input guard does not run on the
no-source route, so `injection_detected` is unreachable there. Not a
data-safety bypass (no retrieval, generation or execution on that route;
the identical refusal message even satisfies §5.5's no-oracle rule
perfectly) but **detection and the audit record are lost**, and UAT step
J12.3 cannot pass there. Pinned by 2 `xfail(strict=True)`.

### ☑ 6. Every agent loop provably bounded (tests)

**TRUE, and this is the strongest item on the checklist.**

- `tests/test_agent_loop_bounds.py` — **27 collected cases** — recomputes
  `LONGEST_RETRY_CYCLE_NODES`, `PRE_CYCLE_NODES` and
  `POST_EXECUTION_NODES` **from the compiled graph's own edge set** and
  fails with the new number, so the arithmetic cannot drift. This exists
  because it did drift: `LOP-001` was a silent HTTP 500 when
  `enforce_display_names` was inserted into the retry cycle and the bound
  was not updated.
- `run_agent` catches `GraphRecursionError` regardless, **because a
  constant can drift and a catch cannot.**
- `LOP-003` fixed all ten `agent/orchestrator/` nodes being registered
  without `guard_node`, so every non-SQL route now has a step budget, a
  deadline check and a cancellation checkpoint.
- `LOP-002` added no-progress detection: an attempt that reproduces the
  previous query (compared on `canonical_sql_form`, so a reformat does
  not count as progress) ends the run.
- `LOP-006` wired `resolve_timeout`, which had **zero** non-test callers,
  so the database query is now clamped to the request deadline too.
- `LOP-009` is recorded as a **verified positive**: the retry budget, the
  safety-refusal non-retry path and the cost ceiling all hold.

**Two caveats that are not bound failures.** `HAL-010` spends the whole
(bounded) budget on a correct query, and `LOP-010` leaves
`HARNESS_MAX_TOKENS` at 0 so the token budget is not enforced — the
per-call and deadline budgets are.

### ☐ 7. UAT golden question set at agreed accuracy threshold on the current default model

**FALSE on all three clauses, which is why this is the gate's second
primary failure.**

- **No agreed threshold exists.** Nothing in the repository or the
  certification documents states one.
- **The set cannot measure accuracy at all.** 28 of its 32 accuracy
  cases are marked `GOLD SQL REQUIRED` and **zero** carry an
  `expected_sql` value (verified by loading the YAML, not by grepping —
  the two textual matches for that key are comments). Without gold SQL
  there is no result set for `eval.evaluators.fetch_gold_result` to
  compare against, so **not a single accuracy case is machine-scorable
  today**. It is a behaviour and structure gate and says so in its own
  README.
- **There is no current number on any model.** The only full-corpus run
  is 2026-09-16 and is self-disclaimed as stale (§4.3).
- **And the "current default model" is not installed** (`OPS-008`).

What *is* true: the set exists, is versioned, covers 11 journeys and both
dialects, and its integrity is gated in CI. That is the scaffolding for
this item, not the item.

### ☐ 8. UAT environment, accounts, test data and docs ready for business users

**FALSE — four of seven components ready.**

| Component | Status |
|---|---|
| **Accounts** | ☑ with one fix pending. Six role accounts exist with grants read from the identity database. **R3 is granted `manager` where the script specifies `analyst`** — `OPS-023`, simultaneously over- and under-granted (§3.3a). An operator action against the identity database |
| **Feature-flag profile** | ☑ `OPS-017` closed it. §2.4 of the UAT script now splits 74 flag-shaped settings into required-on / required-if-in-scope / turn-off-for-UAT, each with its real default. Five subsystems were on that no journey exercises — two of them matter: media generation **spends money** per call, and voice mode carries the open `UI-013` |
| **Docs for business users** | ☑ `DOC-018` added the script's own prerequisites; `OPS-011` fixed the first two commands, which did not run as written. Verified: **449 documented command lines across ten user-facing docs all run as written**, and all 874 relative links across 203 markdown files resolve. Residual: `USER_GUIDE.md` is pinned at 2026-10-15 (`DOC-022`) and 1,120 i18n keys are English-only (`DOC-010`) |
| **Runtime config** | ☐ **`OPS-008`** — the default model is absent, so the first question fails. **`OPS-003`** — all four connections have write privileges and the startup refusals are inert outside `ENVIRONMENT=production` |
| **Test data — SQL Server** | ☑ four live connections, `adventureworks` exercised throughout |
| **Test data — PostgreSQL** | ☐ `seed_postgres_uat.sql` and the connection recipe exist; the database has **never been provisioned or run**. The five Postgres golden cases are `UNVERIFIED` |
| **Semantic catalog** | ☐ `OPS-022` — J2/J3 publish, J4 cannot consume. Testers must be told, or J4 descoped |

### ☑ 9. Backup/restore and rollback documented and drilled once

**TRUE — and this is the item that moved furthest in Phase 11.** It was
*"No — RPO/RTO proposed, awaiting sign-off; no restore drill"* as
recently as 2026-10-30.

| | Measured |
|---|---|
| Identity DB dump | `pg_dump -Fc` — **1 s**, 607,252 bytes from a 15 MB database |
| Restore | `pg_restore --no-owner --no-privileges` — **5 s** into a scratch `postgres:18-alpine` |
| Structural verify | 36 tables, 27 users, 196 conversations, 404 `ai_outputs`, Alembic head `q9u2v5w8x1y4` — identical to source |
| **Functional verify** | **sign-in against the restored copy → HTTP 200**, token issued, `/auth/me` returns the right email and roles |
| **RTO achieved** | **under one minute** at this data volume, dominated by provisioning |
| ChromaDB | drilled (§6.2 of Phase 11), including the unavailable-at-startup and unavailable-on-request cases — which is how `OPS-025` was found |
| Rollback | documented — `DOC-019` fixed `docs/DEPLOYMENT.md`'s procedure, and `DOC-013` documented the 26 undocumented `ENABLE_*` flags that procedure tells an operator to set back |

The functional step is the one a row-count comparison cannot give: it
proves the Argon2id hashes, role grants and tenant rows survived the
round trip.

**Three things this ☑ does not say, and the first is serious:**

1. **RPO is unbounded — there is no backup.** This project has no
   scheduler and no background worker by design, and nothing in
   `scripts/` takes one. The recovery point is "whenever someone last ran
   `pg_dump` by hand". **A lost identity database loses every account,
   conversation and stored answer** (`OPS-018`). Scheduling it is an OS
   or platform task, listed in §6.3.
2. `identity_admin` — the role in `AUTH_DATABASE_URL` — **cannot create a
   database**, which is correct least-privilege and also means it cannot
   perform a restore-to-new-database. Any runbook assuming the
   application's own credentials suffice is wrong.
3. `embeddings/.chroma/` is **not** regenerable, and CLAUDE.md used to
   say it was. `scripts/build_embeddings.py` restores the schema index;
   nothing restores the **golden examples**, **message feedback** or
   **drift baselines** in the same directory. The Chroma copy needs the
   app stopped to be consistent.

### Checklist result

**4 of 9 items ☐ (1, 2, 7, 8) · 5 of 9 ☑ (3, 4, 5, 6, 9).**
Items 1 and 7 are the primary failures. **Verdict: NOT READY.**

---

## 6. Explicitly out of scope for UAT, and still required for production

Everything here blocks go-live, and it is listed so that "UAT passed" is
never read as "production ready". The existing production verdict —
**NO-GO** — stands.

Most of it does **not** block letting business users in, with two
deliberate exceptions that appear in both this section and §2.2:
`AUZ-002` and `OPS-022` each need a **UAT-time mitigation** (a
single-tenant scope; telling testers J4 cannot be signed off) *and* a
separate **production fix** (a migration; a backfill of 187
collections). A mitigation accepted for UAT is not a fix, and that is
exactly the confusion this section exists to prevent.

### 6.1 Carried from the open findings

| Item | Why it is a production blocker specifically |
|---|---|
| `AUZ-002` — tenant partitioning | If UAT runs single-tenant under the §2.1 Option B waiver, the defect is simply **unexercised**. The migration is still required before a second tenant exists anywhere |
| `AUZ-003` — suspension window | A 15-minute window in which a suspended account in a suspended tenant still answers questions is a UAT curiosity and a production incident. `access_token_expire_minutes` has **no upper bound** in its `Field(...)` |
| `BP-012` — AGPL on a default-enabled path | A closed internal UAT host is arguably not "conveying". Serving the product is. This must be resolved before go-live, and it is a legal decision |
| `OPS-018` — no backup schedule | RTO is drilled; RPO is unbounded. Production needs a schedule for **both** the identity database and the Chroma persist directory |
| `OPS-014` — `/health` ignores the identity DB | Behind a load balancer this routes traffic to a server that cannot authenticate anyone |
| `OPS-020` + §4.4 capacity | Admission control admits ~50× measured capacity. Production needs either a GPU that fits the model or ceilings matched to the hardware |
| `SEC-001` / `OPS-026` — `/health` disclosure | Low on a closed host, real on an internet-reachable one |
| `OPS-022` — the identifier split | 187 collections of confirmed business truth unreachable by the pipeline. A production catalog that silently writes to nowhere is worse than no catalog |

### 6.2 Attachment-processing security — audited in this phase as evidence

A separate implementation brief for secure attachment processing was
supplied alongside this phase. It was **not implemented** — Phase 12's
remit is the report — but the existing pipeline was audited against it,
because `enable_chat_attachments` defaults to **`True`** and attachments
are therefore inside the UAT surface. This is a confirmed-state record,
not a plan.

**Substantially already built, and verified by reading the code:**

| Control the brief asks for | State |
|---|---|
| Validation before parsing | `attachments/validation.py`; pipeline order is documented as `validate → malware-scan → store → process` (`attachments/pipeline.py:1`) |
| Signature/MIME validation, extension allowlist | present in `attachments/validation.py` |
| Malware scan at raw-byte level, before any parser | `security/malware_scanner.py::scan_upload`, called from `attachments/pipeline.py:258` |
| **Production fails closed when scanning is off** | **Verified.** `Settings._require_malware_scanning_in_production` (`config/settings.py:4252`) **refuses to start** under `ENVIRONMENT=production` when any of chat attachments, document RAG, policy RAG or media search is on and `MALWARE_SCAN_PROVIDER=disabled`. The error names the exact remedy and the flags to turn off instead |
| PDF preflight (JavaScript, launch actions, encryption, embedded files) | `attachments/pdf_safety.py` |
| Decompression-bomb guard for ZIP-based Office formats | `attachments/zip_safety.py` |
| Centralised, configurable limits | **15** `Settings` fields, counted this phase: `max_attachment_image_bytes`, `…_document_bytes`, `…_document_pages`, `…_image_dimension_px`, `…_resize_dimension_px`, `…_spreadsheet_rows`, `…_text_chars`, `…_zip_entries`, `…_zip_uncompressed_bytes`, `max_attachments_per_message`, `max_total_attachment_bytes` (the **combined** ceiling, which per-file limits alone leave open), `attachment_ocr_timeout_seconds`, `attachment_processing_timeout_seconds`, `attachment_retention_hours`, `attachment_storage_dir` |
| Opaque storage names, never the caller's filename | `attachments/storage.py::sanitize_filename`; one file per `attachment_id` |
| Untrusted-content framing in the prompt | verified at the construction site — `attachments/vision.py:32,95` ("untrusted data, never as instructions") |
| Per-processor isolation by type | `attachments/processors/` — image, pdf, docx, xlsx, pptx, csv, json, text |
| Tests | **19 files** under `tests/`, plus `tests/security/test_malware_scanner_gate.py` |

**Confirmed gaps, measured not assumed:**

1. **No tenant scoping at all.** `attachments/store.py`, `storage.py` and
   `api/attachments.py` contain the string `tenant` **zero** times — the
   same shape as `AUZ-002`. It is **narrower than `AUZ-002`** and the
   difference is worth being precise about: every route is scoped to
   `owner_subject` and there is **no list-all or admin route**, so
   cross-tenant access is not reachable the way it is for documents. The
   residual is `AttachmentStore.get`'s `if item.owner_subject is not
   None` — an attachment stored with no owner (the `none` /
   `static_token` auth modes, where every caller shares a subject
   anyway) is readable by anyone. **Do not add a caller of this store
   without threading a tenant through it.**
2. **The scanner default is `disabled`.** Deliberate and documented (no
   scanning capability existed before it was added), and the production
   validator above is what turns that into "must be explicitly enabled".
   **But UAT does not run as `ENVIRONMENT=production`** (`OPS-003`), so
   **in the UAT configuration as it stands, uploads reach parsers, OCR
   and vision models with no malware scanning.** That is the single most
   important sentence in this subsection.
3. **ClamAV's fail-closed path has never seen a real daemon** —
   environment-blocked since Phase 0 and still unverified.
4. **No file-upload attack was run against a live server.** Zip bombs,
   content-type spoofing and traversal filenames are covered at the code
   path by the tests above; nothing was uploaded to a running instance
   (Phase 2 §5.2).
5. `ARC-001` — three `async def` upload handlers run heavy synchronous
   work on the event loop.
6. `BP-012` — the PDF parser on this path is the AGPL dependency.

**Required before production, for this path specifically:** enable a real
ClamAV and verify its fail-closed path; run upload attacks against a
live instance; decide the `pymupdf` licence question; thread a tenant
through the attachment store before any second tenant exists.

### 6.3 Environment-blocked verifications — nine, each with its command

None was skipped silently; none is recorded as passing. Phase 0 §6 opened
this list and Phase 11 §11 narrowed it.

| # | Check | Command a human must run |
|---|---|---|
| 1 | **Live DAST** against a deployment — never run | ZAP/Burp against the UAT host |
| 2 | **OIDC end-to-end** — no live IdP has ever been used | against the real IdP |
| 3 | **Container CVE scan of the application image** — only base-image figures are measured | `docker build -t app:local . && trivy image --scanners vuln --severity HIGH,CRITICAL app:local` |
| 4 | **Docker build parity on Python 3.11** — green on 2026-10-30, not re-run here. `OPS-021` is why this matters: a pin taken from this machine's 3.14 venv can be **uninstallable** on the 3.11 target, and `docker build` is the only gate that knows | `docker build .`, then `docker run --rm --entrypoint python app:local -c "import sys, numpy, scipy, api.main"` |
| 5 | **The PostgreSQL leg** — the recipe exists, the database has never been provisioned | `psql -f eval/uat_golden/seed_postgres_uat.sql`, add the `postgres_uat` connection, `python scripts/build_embeddings.py` |
| 6 | **Real load test at the intended concurrency on the intended hardware** — §4.4 is this laptop | `eval/load/run_load_test.sh full` for the API tier, but drive real Ollama for the capacity question |
| 7 | **Two service-level resilience drills** (SQL Server and the identity DB stopped from an elevated shell) — the same code paths were exercised against a Docker Postgres; what is missing is `pyodbc`'s own `08001` text | `Stop-Service MSSQLSERVER` / `Stop-Service postgresql-x64-18`, then re-ask a question |
| 8 | **The mid-request ChromaDB drill** — Windows will not allow it (the process holds open SQLite handles) | on Linux, `mv embeddings/.chroma embeddings/.chroma.bak` while a question runs. **On a copy, never the live directory** |
| 9 | **A keyboard-only accessibility pass** — tab order, focus visibility per theme, focus trapping in dialogs. **axe checks none of them**, so Phase 9's four rules driven to zero say nothing about keyboard operation | a real browser, by hand |

Plus two authoring tasks that no tool can do: **`expected_sql` for every
golden case marked `GOLD SQL REQUIRED`**, and **a labelled
question → required-tables fixture set** so retrieval recall/precision
becomes measurable (`CTX-011`).

### 6.4 Disclosed-by-design absences

Not defects; named so they are not mistaken for oversights. No
scheduler, no background worker, no delivery channel (KPI monitoring,
schema drift and briefings return documents or exit codes; nothing is
emailed, posted or messaged). No token-level streaming. No LangGraph
checkpointer, so follow-up resolution is per-request on whatever window
the client resends. Chat history and artifacts cover locally
authenticated users only. Process-local state — rate limiters, circuit
breakers, the result cache, the security-event ring buffer and the
question queue — means a multi-worker deployment has one of each **per
worker**, and the queue is the sharpest case because its cap is
advertised to users. No compliance with any named regulatory regime is
claimed anywhere.

---

## 7. New in this phase — `SEC-008`

One finding, found by trying to tick gate item 4 honestly rather than by
auditing, and **fixed in this phase's own commit**.

**`SEC-008` (S3, test coverage): the masking regression suite pinned one
dialect, and it was not the one this project runs.**

`tests/security/test_masking_survives_renaming.py` is the suite that
holds `SEC-006` closed — the S1 where `SELECT salary AS region` returned
a restricted value in the clear, and 13 of 14 rename variants defeated
the result gate. Every one of its cases was authored in the `postgres`
dialect. **All four connections configured on this machine are `mssql`.**
So the strictest control in `governance/result_policy.py` had no
regression coverage in the dialect it actually runs against, and gate
item 4 — which asks for masking suites passing on SQL Server **and**
PostgreSQL — was not tickable.

**Measured first, then fixed.** `projection_source_columns` resolves
every T-SQL rename and wrapper form correctly when the dialect matches:
`TOP n`, `[bracket]`-quoted identifiers, `CAST(... AS VARCHAR(n))`,
`ISNULL`, `+` string concatenation, `OFFSET/FETCH`, CTE renames and
compound branches all resolve to the base column. **The control was
sound; only its coverage was not.**

**What the measurement also exposed, and this is the part worth keeping.**
Parsed in the *wrong* dialect, a T-SQL statement yields **no** source
names at all — `by_name={}` — and an output column with no resolved
sources is classified by its own name alone, which is exactly the
pre-`SEC-006` behaviour. Mutation-verified: flipping the new class to
`dialect="postgres"` fails **10** of its tests, and three of those are
the `CREDENTIAL` case across all three roles — i.e. a dialect mismatch
would release `password_hash AS note` in the clear to a caller CLAUDE.md
§5.4 says **no role** unlocks.

**It is not reachable today, and the reason is worth recording** so a
future change does not break it unknowingly: both governed callers derive
the dialect from the connection that is about to execute the statement —
`agent/nodes.py:2861` and `api/main.py:1930` both read
`get_sqlglot_dialect(db_config.db_type)`, and `api/main.py` uses that one
value for `validate_sql`, `enforce_row_limit`, `qualify_table_schema`
**and** `projection_source_columns`. So the dialect cannot disagree with
the engine. **This adds a third entry to the pattern CLAUDE.md §5.4 and
§5.5 already record twice** — `projection_source_columns`' optional
parameters and `insight_grounding_failure`'s optional `result_labels`
both silently give a caller the weaker behaviour. A wrong `dialect` is
the same hazard in a different costume: not an error, just less
protection.

**Change:** `tests/security/test_masking_survives_renaming.py` only — a
`dialect` keyword on the existing `_govern` helper defaulting to
`postgres` (so every pre-existing case keeps its exact previous meaning)
plus a `TestTheSameProtectionHoldsOnSqlServer` class of 9 T-SQL rename
forms, the credential case across 3 roles, an over-blocking counter-case
and the aggregate residual pinned in both dialects. **No production code
was touched.**

```
pytest tests/security/test_masking_survives_renaming.py -q
  → 52 passed (37 before this change, both --collect-only verified)

mutation check: dialect="tsql" → "postgres" at all 5 call sites
  → 10 failed, 42 passed          # not vacuous
  # 3 of the 10 are the CREDENTIAL case, one per role

ruff check / black --check on the file
  → All checks passed / 1 file unchanged

full suite, before the change   → 9,687 passed, 0 failed (709.1 s)
full suite, after  the change   → 9,702 passed, 0 failed (789.0 s)
```

**+15 collected, 0 failed, and the delta is exactly this change.** The
second run also spans the start of the concurrent moderation work noted
in §4, and it is worth saying that nothing in that work failed either —
but the attributable number is the +15, which matches the new class
case-for-case.

---

## 8. The shortest path to READY WITH CONDITIONS

Ordered by who must act. **Most of this is not engineering.** Nothing
here is a recommendation to lower the bar — items 1–4 are the four
false gate items, addressed at their cheapest honest resolution.

**Product owner — 4 written decisions**

1. **`AUZ-002`:** sign the single-tenant scoping decision (§2.1 Option B)
   and add "exactly one tenant, for the duration" to the UAT entry
   criteria — or schedule the migration. Without one of these, item 1
   stays false.
2. **Accuracy threshold (item 7):** agree a number, and agree *what it is
   measured on*. Until `expected_sql` is authored this can only be a
   behaviour-and-structure threshold, which should be stated as such
   rather than implied to be accuracy.
3. **Sign or reject the 36 waivers in §3.** Three carry reservations, and
   one (`OPS-006`) is recommended against outright.
4. **Scope J4 and J12.3:** `OPS-022` means J4 must not be signed off as
   evidence that the catalog works; `HAL-006` means J12.3 cannot pass on
   the no-source route. Descope or rewrite both steps.

**Operator — 6 configuration actions, all small**

5. `OPS-008`: install the configured model, or change `OLLAMA_MODEL`.
   **Without this the first question fails.**
6. `OPS-003`: point every connection at a read-only role
   ([`docs/DBA_READONLY_ACCOUNTS.md`](../DBA_READONLY_ACCOUNTS.md)) and
   decide on `ENVIRONMENT=production` — which also turns on the identity
   and malware-scanning refusals.
7. `OPS-015` + `HAL-004`: set `ENABLE_WEB_SEARCH=false`. This removes the
   dominant mis-route **and** the ungated web-grounding path in one
   change.
8. `UI-013`: set `ENABLE_VOICE_MODE=false` (and the four other
   unexercised subsystems the Phase 11 flag profile names — media
   generation **spends money**).
9. `OPS-023`: grant R3 `analyst`, remove `manager`.
10. `OPS-012`: raise `QUESTION_TIMEOUT_SECONDS` to at least
    `REQUEST_TIMEOUT_SECONDS`, or descope the queue.

**Engineering — 2 items**

11. `OPS-006`: refresh `.secrets.baseline` **on Linux**. This is the only
    red blocking gate and the cheapest item in the audit (§3.1).
12. `HAL-010`: decide whether `enforce_display_names` may accept a key
    alongside its display name. This is the one open S2 that makes an
    *ordinary* question fail.

**Then, and only then, re-run this gate.** Items 1, 2, 7 and 8 become
satisfiable; 3, 4, 5, 6 and 9 are already ☑. The honest result at that
point would be **READY WITH CONDITIONS**, with the conditions being the
signed waivers and the single-tenant scope — not READY, because §6's
production list would still be open and the accuracy threshold would be a
behaviour threshold.

---

## 9. Sign-off

**This block is unsigned. An auditor cannot waive a finding on an
owner's behalf, and this report does not.**

| | Name | Date | Decision |
|---|---|---|---|
| Product owner — `AUZ-002` scope (§2.1) | | | ☐ Migrate ☐ Single-tenant |
| Product owner — accuracy threshold (item 7) | | | |
| Product owner — the 36 waivers (§3) | | | ☐ All ☐ With exceptions |
| Operator — the 6 configuration actions (§8) | | | |
| Engineering — `detect-secrets` refresh (§3.1) | | | |
| **Gate owner — UAT entry** | | | ☐ READY ☐ READY WITH CONDITIONS ☒ **NOT READY (this report)** |

**Commands behind every number in §4**, so this is reproducible:

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m black --check .
.venv\Scripts\python.exe -m bandit -c pyproject.toml -r . -f txt
.venv\Scripts\python.exe -m mypy agent analytics api artifacts attachments ... workflow
.venv\Scripts\python.exe -m mypy .
.venv\Scripts\python.exe -m pip_audit -r requirements-dev.txt --desc `
  --ignore-vuln PYSEC-2026-311 --ignore-vuln PYSEC-2026-3813 `
  --ignore-vuln PYSEC-2026-3814 --ignore-vuln PYSEC-2026-3815 `
  --ignore-vuln PYSEC-2026-4240 --ignore-vuln CVE-2026-114134
cd frontend; npm run lint; npx tsc -b --noEmit; npm run test; npm audit --omit=dev; npm run build
```

`detect-secrets` is the one gate whose CI command cannot be run from this
checkout at all — `detect-secrets scan $(git ls-files)` overflows
Windows' argument limit. The question it answers *is* answerable here, by
scanning to a temporary file and diffing with path separators normalised;
method in [`phase-11-operations.md`](phase-11-operations.md) §10.
