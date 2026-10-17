# Production Release Gate v2 — 2026-10-17

**Assessment date:** 2026-10-17
**Baseline commit:** `779d7fc` on `feat/source-connectivity-audit`
**Supersedes the *verdict* of:** [`../PRODUCTION_READINESS_RELEASE_GATE.md`](../PRODUCTION_READINESS_RELEASE_GATE.md) (2026-10-03, **CONDITIONALLY READY**)
**Preserves:** that document and all its evidence, unmodified
(sha256 `54c81ff030ac2ad9…`, verified unchanged at the start and end of this pass)

---

## 1. Verdict

# NO-GO for production release

> ### ⚠️ Revised the same day — read [§11](#11-addendum--same-day-re-verification-2026-10-17-evening) before acting on this section
>
> **F-008 below is now CLOSED and verified fixed** (commit `08f385e`;
> the image builds and runs on Python 3.11.16). The verdict is **still
> NO-GO**, but no longer for the reason stated here — the blocker class
> moved from *"cannot be built"* to *"built but unverified, and CI is
> red on three blocking gates"*. §11 carries the current scorecard,
> three new findings (F-011, F-012, F-013), one regression (F-009:
> 3 → 4) and the first live end-to-end functional verification this
> project has on record. **Everything in §1–§10 is preserved unmodified
> as the morning's assessment**, exactly as this document preserved its
> own predecessor.

**This is a downgrade from the previous CONDITIONALLY READY verdict, and
it is driven by one new, reproducible, measured defect, not by a
re-scoring of old facts.**

> **F-008 — the production container image does not build.**
> `requirements.txt` pins `scipy==1.18.1`, which requires Python ≥ 3.12.
> The Dockerfile's base image is `python:3.11-slim`. `docker build`
> fails at the dependency-install layer.
>
> You cannot deploy the documented artefact. A platform that cannot be
> built cannot be released, regardless of how many tests pass.

The previous verdict was not carried forward, and the fact that most
checks still pass did not promote anything. Four areas that the previous
gate could not test remain untested, and three of them are untestable
**because of F-008**.

### What changed since 2026-10-03

| Change | Effect on this gate |
|---|---|
| ~28 commits (domain skills ×3 batches, web-search overhaul, 4 UI capabilities, 3 UI fixes) | Test counts up; no regression found |
| **Docker daemon started this session** | Unblocked container testing — which then revealed F-008 |
| **Container build attempted for the first time in this environment** | **Found F-008** |
| CI gates replicated locally for the first time | Found F-009, F-010 |
| Component benchmark re-run | 62/62, no regressions |
| Benchmark harness exercised against a named connection | Found F-007 |

---

## 2. Scorecard

| Area | 2026-10-03 | **2026-10-17** | Basis |
|---|---|---|---|
| Functional correctness | PARTIAL | **PASS** | 7,197 backend + 1,147 frontend tests pass |
| Deterministic analytics correctness | not a named row | **PASS** | Component benchmark 62/62, no regression vs committed baseline |
| Security — SAST | PASS | **PASS WITH CONDITIONS** | 0 High, 0 Medium, **3 Low**; bandit exits 1, so the blocking CI step fails (F-009) |
| Security — dependencies | PASS | **PASS WITH CONDITIONS** | 1 advisory outside the justified ignore list, **verified not reachable** (F-010) |
| Security — secrets | PASS | **PASS** | CI-equivalent detect-secrets run: clean |
| Security — container CVEs | NOT VERIFIED | **BLOCKED** | No image to scan — F-008 |
| Tenant isolation | PASS | **PASS** | 25 adversarial files incl. cross-tenant; unchanged |
| SQL safety | PASS | **PASS** | 49 validator tests; boundary untouched this pass |
| AI quality | NOT VERIFIED | **PASS WITH CONDITIONS** | Partial live re-run — §4 |
| Data correctness | PARTIAL | **PASS WITH CONDITIONS** | Deterministic layer verified; independent SQL reconciliation not done |
| Performance / capacity | NOT VERIFIED | **BLOCKED** | Load stack builds the same broken image — F-008 |
| Reliability | PASS | **PASS** | Harness retry/cancellation tests pass |
| Observability | PASS | **PASS** | Metrics endpoints live; per-worker limitation disclosed |
| Disaster recovery | PARTIAL | **NOT TESTED** | Targets now proposed; no drill executed — §6 |
| UAT | NOT VERIFIED | **NOT TESTED** | Scripts prepared; no human participant — §7 |
| Documentation | PASS | **PASS WITH CONDITIONS** | F-001: CLAUDE.md misstates which CI gates block |
| Rollback readiness | PARTIAL | **NOT TESTED** | Documented, never exercised |
| **Deployability** | not a named row | **FAIL** | **F-008** |

**Counts:** PASS 8 · PASS WITH CONDITIONS 5 · FAIL 1 · BLOCKED 2 · NOT TESTED 3.

---

## 3. Findings register

| ID | Sev | Area | Finding | Status |
|---|---|---|---|---|
| **F-008** | **P1 — release blocking** | Deploy | **Production image does not build.** `scipy==1.18.1` (requires Python ≥3.12) vs `python:3.11-slim` base. Blocks deployment, CI `docker-scan`, Trivy, and the k6 load stack (which builds the same Dockerfile) | **OPEN — not fixed, see §8** |
| **F-010** | P2 | Security | CI's pip-audit gate fails: `PYSEC-2026-4240` (langgraph-sdk 0.3.15) is not on the justified ignore list. **Verified NOT reachable** — the app never imports `langgraph_sdk` and uses no checkpointer; the package is transitive via `langgraph==1.0.10rc1` | **OPEN — reported, not fixed** |
| **F-009** | P2 | Security | CI's bandit gate fails (exit 1) on 3 Low findings: `random` for retry jitter, an `assert` in `api/streaming.py`, and `try/except/pass` in `security/audit_log.py`. All three are deliberate and already annotated for `ruff`, but not for `bandit` | **OPEN — reported, not fixed** |
| **F-004** | P1 | AI quality | No valid accuracy evidence existed (baseline 2026-09-01, self-disclaimed) | **PARTIALLY CLOSED** — §4 |
| **F-007** | P2 | Tooling | `scripts/run_benchmark.py` reads the legacy flat `DB_*` config and aborts against a named-connection `.env`. This is why the benchmark had gone un-rerun | **WORKED AROUND** — §4 |
| **F-005** | P2 | Security evidence | Security benchmark last run 2026-09-25, 50 of 500 cases; `eval/security_baselines/` does not exist so `--check-regression` cannot work | **OPEN** |
| **F-002** | P2 | DR | No RPO/RTO defined | **ADDRESSED** — proposal written, pending sign-off |
| **F-003** | P2 | DR | Backup/rollback never drilled | **OPEN** |
| **F-006** | P2 | UAT | No UAT record | **ADDRESSED** — scripts written; execution still required |
| **F-001** | P3 | Docs | `CLAUDE.md` §5.7/§13 say mypy blocks and only 2 scanners are non-blocking; in fact 4 are (`gitleaks`, `mypy`, Trivy, load-test-smoke) | **OPEN** |

**No P0.** No security vulnerability, data-integrity defect or
tenant-isolation failure was found. F-008 is P1 rather than P0 because it
prevents deployment rather than compromising a running system.

---

## 4. AI quality — partial live re-run

The previous gate could not test this. This pass could, after working
around F-007.

**Environment:** `llama3.1:8b` (local Ollama), `AdventureWorksDW2025`
(36 tables indexed) — the same model and database the 2026-09-01
baseline used, which is what makes any comparison meaningful.

**Hardware reality, measured:** ~3.6 minutes per LLM-bound case. Latency
is dominated by prompt evaluation (53–62 s for 4–6 k tokens) because the
4.9 GB model exceeds this machine's 4 GB VRAM and runs largely on CPU.
A full 57-case run is ~3.5 h, which exceeds the 2-hour execution limit
available here — so a **stratified subset** was run instead.

> **Latency figures from this environment must not be used as a
> performance claim.** They measure a VRAM-constrained laptop, not a
> deployment target. Only the *accuracy* comparison is meaningful.

**Run status at the time of writing: IN PROGRESS, not complete.**
A stratified subset (`--difficulty adversarial easy medium`, 31 cases)
was launched and had not finished when this report was written. Its
result is therefore **not** included, and the AI-quality row above rests
on what *was* completed: the 3-case pilot (2 of 2 easy cases that
produced SQL passed; the third failed on retrieval, below) and the
deterministic component benchmark (62/62).

**No aggregate accuracy figure is claimed in this report.** F-004 is
marked PARTIALLY CLOSED for exactly that reason: the harness gap that
prevented any re-run (F-007) is now solved and a run is demonstrably
possible, but a comparable full-corpus number does not yet exist. The
2026-09-01 baseline remains the only full-corpus figure on record and
remains self-disclaimed as stale.

**Qualitative observation from the pilot**, worth recording regardless of
the aggregate: on one case the agent generated SQL against
`FactInternetSales` for a product-attribute question, and the log shows
why — `[build_analytical_plan] candidate plan failed validation …
Table 'DimProduct' is not among the tables retrieved for this question`.
**Schema retrieval, not generation, failed to surface the right table.**
The validator flagged the anomaly and execution failed cleanly on invalid
column names, so nothing incorrect was returned — the controls behaved
correctly — but it points at retrieval quality as the lever, which is a
different fix from prompt tuning.

---

## 5. Performance — BLOCKED

Not run, and not estimated. `eval/load/docker-compose.loadtest.yml`
builds `dockerfile: Dockerfile` from the repo root — the same image that
fails to build (F-008). Docker itself is available and working; the
blocker is the image.

**No capacity claim is made.** The previous gate made none either.

---

## 6. Disaster recovery — NOT TESTED

Targets **proposed** in [`RPO_RTO_PROPOSAL.md`](RPO_RTO_PROPOSAL.md):
RPO 24 h, RTO 8 h. Both **pending business sign-off**; neither is
claimed as met.

Key assessment from that document: RPO 24 h is achievable but **not
currently achieved**, because this application ships no scheduler by
design — the backup must be driven by your infrastructure. RTO 8 h looks
generous for the architecture, with one genuinely unquantified step:
`scripts/build_embeddings.py`, whose duration scales with schema size and
has never been timed. That single measurement is the highest-value DR
test available.

---

## 7. UAT — NOT TESTED

[`UAT_SCRIPTS_AND_SIGNOFF.md`](UAT_SCRIPTS_AND_SIGNOFF.md) provides 15
journeys across 6 roles with prerequisites, steps, expected results and a
sign-off template. **Every result cell is deliberately blank.**

No UAT participant has executed anything. Automated tests are not UAT and
are not counted as such anywhere in this report.

---

## 8. Why F-008, F-009 and F-010 were not fixed

All three have obvious-looking fixes. None was applied, for reasons that
are about safety rather than effort:

- **F-008** — the fix is to pin a Python-3.11-compatible scipy (≤ 1.17.1).
  But scipy is used by five production modules
  (`analytics/causal.py`, `forecasting.py`, `models.py`, `predictive.py`,
  `optimization/engine.py`), the local venv is Python **3.14** so it
  cannot validate a 3.11 resolution, and changing a pinned numerical
  dependency demands a full suite re-run. The approved instructions say
  to stop and ask when a fix could break a major existing feature.
  **Recommended fix and its validation plan are in §9.**
- **F-009 / F-010** — both fixes touch a **blocking CI security gate**
  (adding `# nosec` markers; adding an ignore-list entry). Editing a
  security gate to make it green is precisely the move the instructions
  forbid doing unilaterally, even when — as here — the findings are
  verified benign and the remediation follows documented precedent.

---

## 9. Required before this can become a GO

| # | Action | Owner | Blocks |
|---|---|---|---|
| 1 | **Fix F-008.** Pin scipy ≤ 1.17.1, rebuild, run the full backend suite, confirm `analytics/` and `optimization/` tests pass | Approval needed | Deployability, container CVEs, load testing |
| 2 | Re-run the container build and Trivy scan | After 1 | Container CVE status |
| 3 | Run the k6 load stack and record a capacity envelope | After 1 | Performance status |
| 4 | Decide F-009/F-010 remediation (annotate vs upgrade) | You | Green CI |
| 5 | Execute UAT with real participants | You | UAT status |
| 6 | Sign off RPO/RTO, then run a restore drill against isolated resources | You | DR status |
| 7 | Complete the full 57-case benchmark on hardware that can hold the model in VRAM | You | Full AI baseline |
| 8 | Correct `CLAUDE.md` §5.7/§13 CI-gate claims | Low risk | Documentation accuracy |

---

## 10. Scope and honesty statement

**Executed this pass:** backend and frontend suites, component benchmark,
CI-equivalent bandit / pip-audit / detect-secrets, Docker daemon start,
container build attempt, a partial live Text-to-SQL benchmark.

**Not executed, and why:** container CVE scan and load testing (F-008);
DAST (no deployed target); UAT (no participants); backup/restore drill
(not authorised to run yet); full 57-case benchmark (exceeds the
execution limit on this hardware); OIDC against a live IdP and
multi-worker behaviour (pre-existing, disclosed in `CLAUDE.md` §13).

**No result in this document was simulated, extrapolated or carried
forward from an earlier report.** Where a measurement is absent, the row
says NOT TESTED or BLOCKED rather than inheriting a previous verdict.

---

## 11. Addendum — same-day re-verification (2026-10-17, evening)

**Assessed commit:** `08f385e` (6 commits after this document's `779d7fc`
baseline, and `origin/feat/source-connectivity-audit` is at the same
commit — so the fix is pushed, not local-only).
**Working tree:** clean at assessment start and end.
**Why an addendum and not a v3 document:** the baseline moved by six
commits on the same day, and §9's "required before this can become a GO"
list is the thing being discharged. A new dated gate would duplicate
§1–§10 rather than answer it.

### 11.1 Revised verdict

# Still NO-GO for production · **READY FOR UAT, with conditions**

The two judgements are separate and must not be collapsed:

| Question | Answer | Why |
|---|---|---|
| Can the documented artefact be deployed? | **Yes** (was: no) | F-008 closed and verified — §11.2 |
| Is it production-ready? | **No** | Three blocking CI gates are red (F-009, F-010, F-011); container CVE scan and capacity testing are now *unblocked but still unrun*; DR undrilled; no UAT |
| Is it ready for business users to run UAT against? | **Yes, with conditions** | Core workflows, auth, RBAC and the SQL/result controls were driven live end to end — §11.5. Conditions: F-012 must be disclosed to testers, and the database-routing decision in §11.4 taken first |
| Has UAT been completed? | **No — NOT COMPLETED** | Zero journeys executed by a human. Unchanged from §7 |

### 11.2 F-008 — CLOSED, verified

The fix landed as `08f385e` ("fix(packaging): a scipy pin that cannot
install on the Python we target"): `scipy==1.18.1` to `scipy==1.17.1`,
plus two `.dockerignore` exclusions (`*.log`, `tests/`) and an R-004
update. §8 declined to apply this fix because it "demands a full suite
re-run" and could not be validated on a 3.11 resolution. Both objections
are now discharged by measurement, not by argument:

| Evidence | Command | Result |
|---|---|---|
| The image exists and targets 3.11 | `docker run --rm --entrypoint python text-to-sql-dashboard:cert -c "import sys,scipy,numpy; ..."` | `python 3.11.16`, `scipy 1.17.1`, `numpy 2.4.6` |
| The 3.11 resolution actually installed scipy | same run | scipy imports inside the image — the exact failure mode F-008 described is gone |
| The five scipy-dependent production modules load on 3.11 | `importlib.import_module` for `analytics.causal`, `analytics.forecasting`, `analytics.models`, `analytics.predictive`, `optimization.engine` inside the image | all 5 `OK`, plus `api.main`, `agent.graph`, `agent.sql_validator` |
| No regression from the downgrade | `.venv/Scripts/python.exe -m pytest -q` at `08f385e` | **7274 passed, 11 deselected, 0 failed** in 385.98s |

**What this does and does not prove.** It proves the pinned dependency
set resolves and imports on the targeted interpreter inside the real
production image, and that the numerical-library downgrade broke no test.
It does **not** prove numerical-output parity between scipy 1.17.1 and
1.18.1 — the suite asserts behaviour, and it passes, but no one diffed
forecast or causal figures across the two versions. The
`analytics`/`optimization` suites passing is the evidence on offer.

**One honesty note on the image provenance.** `text-to-sql-dashboard:cert`
was built at `2026-10-17T09:10:29Z` by an earlier pass in this session's
environment, not by this addendum. It was **not taken on trust** — it was
re-verified by executing the interpreter and the imports inside it, above.
A second, independent `docker build` of the committed tree was launched
during this pass. **It has since finished, and its result corroborates
the fix without being a clean build** — recorded precisely because the
distinction matters:

- **Every dependency layer succeeded**, including
  `#18 [base …] RUN … pip install -r requirements.txt` → `DONE 1729.1s`
  on the `python:3.11-slim` base. That layer is *exactly* where F-008
  failed. `COPY . .`, the frontend-dist copy and the final `chown` layer
  all completed too.
- **It then failed at `exporting to image`**:
  `failed to create temp dir: mkdir /var/lib/desktop-containerd/…: read-only
  file system`. That is a **host storage fault, not a dependency or code
  error** — the host is at **98% disk (8.9 GB free)** with Docker holding
  53.9 GB of images and 67.4 GB of build cache.
- **A trap worth recording:** the shell pipeline reported `exit code 0`
  because `docker build … | tail` returns `tail`'s status, not Docker's.
  The failure was only visible by reading the log. Do not take an exit
  code from a piped `docker build` as a build result.

So the pinned set now has two independent confirmations of the thing
F-008 was about — the `:cert` image's installed-and-importable
`scipy 1.17.1` on `python 3.11.16`, and a from-scratch dependency
install completing on the same base — and **no** end-to-end clean
`docker build` of the committed tree in this environment. The §11.2
verdict rests on the former, which does not depend on the latter.

**Consequential unblocking — reassessed:**

| Previously BLOCKED by F-008 | Status now |
|---|---|
| Container CVE scan (Trivy) | **still BLOCKED — by host disk, not by F-008.** Needs free space before an image can be exported to scan |
| k6 load stack / capacity envelope | **still BLOCKED — same cause.** `eval/load/docker-compose.loadtest.yml` builds this Dockerfile, so it hits the same export step |
| CI `docker-scan` job | **unblocked in principle, unverified** (no `gh` CLI on this machine — CI run history cannot be read) |

**This is an environment limitation, not a product defect,** so it is
not filed as an F-number. But it is the live blocker on §11.7 items 2
and 3: those two cannot proceed until the host has disk. Reclaimable
right now: ~50.6 GB of images and ~19.1 GB of build cache, by `docker
system df`'s own reckoning — **not pruned during this pass**, because
the reclaimable set includes images other sessions and the load stack
may still want.

### 11.3 Findings register — delta

| ID | Sev | Change this pass | Evidence |
|---|---|---|---|
| **F-008** | P1 | **OPEN to CLOSED, verified** | §11.2 |
| **F-009** | P2 | **REGRESSED: 3 to 4 Low findings** | A fourth appeared — `agent/sql_validator.py:842` `B112 try_except_continue`, introduced by `933cfff`'s `describe_sql`. Full set: `agent/harness/retry.py:75` (B311), `agent/sql_validator.py:842` (B112), `api/streaming.py:82` (B101), `security/audit_log.py:255` (B110). Still **0 High, 0 Medium**; bandit exits 1, so the blocking gate still fails |
| **F-010** | P2 | unchanged; **non-reachability independently re-verified** | `pip-audit` with CI's exact 14-entry ignore list: "Found 1 known vulnerability" — `PYSEC-2026-4240`, langgraph-sdk 0.3.15, fix 0.4.4. Re-greps this pass: **zero** first-party `langgraph_sdk` imports, **zero** `BaseCheckpointSaver`/`checkpointer=` usage, **zero** `auth.on.threads`/`assistants`/`crons` decorators — and that decorator API *is* the vulnerable surface. Transitive via `langgraph==1.0.10rc1` |
| **F-011** | **P2 — NEW** | **CI's blocking `ruff check .` gate is red** | `ruff check .` gives `Found 1 error.` (exit 1): one `I001` import-order finding in `agent/multiagent/agents.py`. This is *not* a new lint error — `CLAUDE.md` §13 has recorded it as known and accepted, and [`../architecture/implementation-roadmap.md`](../architecture/implementation-roadmap.md) Batch 0 calls fixing it "**owner's call, not a default**". What is new is the *consequence*: `.github/workflows/ci.yml` runs `ruff check .` unscoped and **blocking**, so a finding recorded as "accepted" also makes a blocking gate fail. §2's scorecard never named it. Deliberately **not fixed here** — the roadmap assigns that decision to the owner |
| **F-012** | **P2 — NEW** | **Multi-database auto-routing selected a database that holds no data, while one that could answer was configured** | Reproduced live — §11.4 |
| **F-013** | **P3 — NEW** | `CLAUDE.md` §13 cites `langgraph==0.2.62`; the real pin is `langgraph==1.0.10rc1` | `requirements.txt:2`. Same class as F-001 (doc accuracy), and it matters because §13 uses that version to justify "no checkpointer ships for SQL Server", which is the stated reason follow-up resolution is not checkpointed. The *conclusion* still holds — no checkpointer is used — but the cited premise is three major versions stale. Also note the production pin is a **release candidate** (`rc1`) |
| F-001 | P3 | still OPEN at assessment start, **fixed during this pass** | See §11.7 row 8 |
| F-002, F-003, F-005, F-006, F-007 | P2 | unchanged | No DR drill, no security-benchmark re-run, no UAT execution |

**Still no P0.** Nothing found this pass compromises a running system.

### 11.4 F-012 — auto-routing to an empty database (new)

**Reproduction**, against the live local deployment (4 configured
connections: `hr`, `adventureworks`, `employee_management_database`,
`test_db`):

```
POST /ask  {"question":"How many employees are in each department?","enable_insight":true}
  -> status "succeeded" | database "hr" | retry_count 0 | 183,379 ms
  -> SQL: WITH department_employees AS (SELECT DepartmentId, COUNT(DISTINCT EmployeeId) ...
            FROM EmployeeOrganizationAssignment GROUP BY DepartmentId)
          SELECT TOP 1000 ... ORDER BY num_employees DESC
  -> row_count 0 | result_rows [] | insight null
  -> analytical_result.shape "empty", insufficient_data_reasons ["all calculations: empty result"]
```

Then, directly via `POST /execute`:

| Query | Result |
|---|---|
| `SELECT COUNT(*) FROM Employee` on `hr` | **0** |
| `SELECT COUNT(*) FROM EmployeeOrganizationAssignment` on `hr` | **0** |
| `SELECT COUNT(*) FROM Employee` on `employee_management_database` | **15** |

So the router chose a schema-rich but **data-empty** database over one
holding the answer. `embeddings/retriever.py::select_database` ranks each
connection by its single best-matching *table embedding* — its own
docstring says it skips a database whose index "isn't built yet (or is
empty)", which means an empty **index**, not an empty **table**. `hr`'s
richer HR vocabulary (`EmployeeOrganizationAssignment`, similarity
`0.7513`) outranks the plainer `Employee`/`Department` naming in the
database that actually has rows.

**Severity reasoning.** P2, not P1, and not a security finding:

- The controls behaved correctly throughout. Nothing was fabricated —
  `insight` is `null`, the analytical layer reported `shape: "empty"`,
  and the persisted assistant message reads *"Query succeeded. 0 row(s)
  returned."* Provenance (`CLAUDE.md` §4.4) held.
- It **cannot occur in a single-database deployment** —
  `select_database` short-circuits with no Chroma query at all.
- But in a multi-database deployment there is **no user recourse**:
  `AskRequest` has no `database` field (`extra="forbid"` rejects one, as
  this pass confirmed with a 422), and the UI's `SourcePicker` selects
  *source types*, not database connections. A business user cannot
  redirect the question.

**This is the single most likely cause of a UAT J5.1 failure** and must
be disclosed to testers before they start. **Not fixed here** —
changing routing to consider row counts (or adding an operator-facing
database override) is a design decision with its own cost and privacy
questions, not a defect fix to apply mid-assessment.

### 11.5 Live end-to-end functional verification (new — first on record)

§10 of this document listed functional verification as resting on the
mocked suite. This pass drove the **running application**: `uvicorn
api.main:app` on `127.0.0.1:8000`, serving the freshly built
`frontend/dist`, against the four live SQL Server connections and local
Ollama. **This is manual functional testing, not UAT** — it was executed
by an agent, not by a business user, and it judges that the controls work,
not that the right thing was specified.

| # | What was driven | Result |
|---|---|---|
| L1 | `GET /health` | `status: ok`; 4/4 databases connected; schema index 11/36/3/2 tables; Ollama reachable |
| L2 | Dashboard served by the API process | `GET /` 200, correct `<title>`; `/login` SPA fallback 200; main bundle 200 (1.62 MB) |
| L3 | `GET /openapi.json` | 182 routes |
| L4 | `POST /ask` unauthenticated | **401** `Missing or invalid Authorization header` |
| L5 | `POST /ask` with `X-Tenant-Id: evil` | **400** — `reject_client_tenant_override` fires before authentication, as `CLAUDE.md` §5.3 requires |
| L6 | `POST /auth/register` with an email-derived password | **400** `Password must not contain your email address` — policy enforced |
| L7 | `POST /auth/register` then `POST /auth/login` | **201 / 200**, JWT with `roles:["user"]`, `tid:"default"` |
| L8 | `POST /ask` full 18-node pipeline | **succeeded**, 183 s, 1 attempt, 0 retries — see §11.4 for the empty result |
| L9 | Chat creation + history persistence | Conversation auto-created and titled from the question; `GET /conversations` lists it; `GET /conversations/{id}/messages` returns both turns |
| L10 | `GET /sources` | 7 sources, all `configured: true`. `policy` and `generation` correctly `authorized: false` for a `user` role (`policy_rag_query`, `media_generate`) — **RBAC filters the source list, not just the UI** |
| L11 | `POST /execute` — hand-edited `UPDATE` | **rejected**: *"Only SELECT statements are allowed; got a Update statement instead."* |
| L12 | `POST /execute` — `DROP TABLE` | **rejected**: *"... got a Drop statement instead."* |
| L13 | `POST /execute` — stacked `SELECT 1; DELETE FROM ...` | **rejected**: *"Only a single SQL statement is allowed; found multiple statements."* |
| L14 | `POST /execute` — `SELECT name FROM sys.tables` | **rejected**: system catalog reference refused |
| L15 | `POST /execute` — `SELECT * FROM Department` | **succeeded**, 6 rows; normalized to `SELECT TOP 1000 *` — the row cap is injected into the SQL text |
| L16 | `POST /execute` — `SELECT * FROM EmployeeSalary` | **succeeded**, 21 rows, and `SalaryID`/`SalaryAmount`/`SalaryType` returned as `[restricted]` while `EmployeeID`/`CurrencyCode`/dates stayed visible. Compensation protection is live on real data, against `SELECT *`, for a caller without `VIEW_RESTRICTED_COLUMNS` |
| L17 | 14 admin/governance `GET` routes with a `user` token | **14/14 refused** — 12 x 403, one 404 (`/model-routing/policy`, routing disabled), one 422-then-403 (`/governance/assets/current` validates query params first, then refuses; no data returned either way) |
| L18 | Refusal recorded in the audit log | `event=authz_denied severity=warning ... roles=['user'] required_permission='admin_config' path='/metrics/performance'` |
| L19 | Nonexistent resource id | **404**, matching the anti-enumeration rule |
| L20 | Secret redaction in logs | Connection-test lines log `<DB_CONNECTION_STRING override>` and `mssql://(localdb)\MSSQLLocalDB:None/EmployeeManagementDB` — no password in any line read |

**L11–L17 are the rows that matter.** They are the live counterpart of
the mocked `tests/security/` suite, and they confirm the SQL validator,
the row cap, the restricted-column gate and server-side RBAC all hold
against real traffic rather than only against fixtures.

**Account created.** `acceptance.check@example.com` was registered in the
local identity database to obtain a token. It is a real row in
`identity.users`; delete it when it is no longer wanted.

### 11.6 Gate re-measurements

All run at `08f385e` on this machine (Python 3.14.4, `.venv`):

| Gate | CI blocking? | Command | Result |
|---|---|---|---|
| Backend tests | yes | `pytest -q` | **7274 passed, 11 deselected, 0 failed** (385.98 s) |
| Frontend tests | n/a (not in CI) | `npx vitest run` | **89 files, 1161 tests, all passed** (33.98 s) |
| Frontend typecheck | n/a | `npx tsc --noEmit -p tsconfig.app.json` | **clean** (exit 0) |
| Frontend lint | n/a | `npm run lint` (oxlint) | **exit 0** — warnings only (`jsx-a11y`, `react` fast-refresh / set-state-in-effect) |
| black | yes | `black --check .` | **clean**, 1635 files |
| ruff | **yes** | `ruff check .` | **FAILS** — 1 x `I001` (F-011) |
| bandit | **yes** | CI's exact 12-package invocation | **FAILS** — 4 Low, 0 Medium, 0 High (F-009) |
| pip-audit | **yes** | CI's exact 14-ignore invocation | **FAILS** — 1 advisory (F-010) |
| mypy | no (report-only) | `mypy .` | **631 errors in 115 files**, of which **exactly 7 in production modules** — byte-for-byte the list `CLAUDE.md` §13 documents (`db/schema_introspection.py:376`, `agent/sql_validator.py:839` x2, `identity/repositories/semantic_intelligence.py:199,200,242`, `identity/repositories/recommendation_governance.py:402`). **No drift** |
| detect-secrets | **yes** | CI's `scan $(git ls-files) --baseline` | **NOT COMPLETED** — the 1,371-file list exceeds Windows' argument limit; a wrapper run against a *copy* of the baseline (so the real `.secrets.baseline` was never rewritten) had not finished. §2's "clean" result from this morning stands unrefuted but was not reproduced |

**Three of the four blocking gates named above are red.** None is a
vulnerability; all three are the same class — a verified-benign finding
that no one has been authorised to annotate or ignore. That is the
production blocker now, in place of F-008.

### 11.7 Updated §9 — required before this can become a GO

| # | Action | Status after this pass |
|---|---|---|
| 1 | Fix F-008, rebuild, full suite | **DONE and verified** (`08f385e`; §11.2) |
| 2 | Container build + Trivy scan | **unblocked, not run** |
| 3 | k6 load stack + capacity envelope | **unblocked, not run** |
| 4 | Decide F-009/F-010 remediation | **still yours** — now also F-011 |
| 5 | Execute UAT with real participants | **still yours** — the script is ready and now carries F-012 as a known defect |
| 6 | Sign off RPO/RTO, then drill restore | **still yours** |
| 7 | Full 57-case benchmark on adequate hardware | **still yours** |
| 8 | Correct `CLAUDE.md` §5.7/§13 CI claims | **DONE** this pass — and F-013's stale langgraph pin with it |
| 9 | **NEW** — decide the F-012 routing question | **yours**: operator-selectable database, row-count awareness in routing, or a documented "point one deployment at one database" |

### 11.8 Honesty statement for this addendum

**Executed:** full backend suite, full frontend suite, frontend typecheck
and lint, black, ruff, bandit, pip-audit, mypy, verification of the
production image's interpreter and imports, and 20 live end-to-end probes
against the running application with real databases and a real local
model.

**Not executed:** Trivy/container CVE scan, k6 load test, DAST, the
57-case accuracy benchmark, the 500-case security benchmark, a DR restore
drill, OIDC against a live IdP, multi-worker behaviour, and **UAT**.

**Two launched runs, two different outcomes — stated separately because
"didn't finish" is not one thing:**

- The second `docker build` **finished and failed at image export** on
  host disk exhaustion, after every dependency layer had succeeded. Its
  partial result is reported in §11.2 and is not presented as a clean
  build.
- The CI-equivalent `detect-secrets` run **was stopped at a 30-minute
  execution limit** having never produced output. Nothing is claimed
  from it; §2's morning result stands unrefuted but unreproduced. The
  real `.secrets.baseline` was never written to — the run used a copy.

**Post-assessment merge.** After this addendum was written,
`origin/master` was merged into the branch to pick up a Dependabot bump
of `sharp` 0.35.4 → 0.35.5 (a devDependency; the only content master
held that this branch lacked). The frontend was re-verified against it:
**1,161 tests passed across 89 files**, `tsc` clean, `oxlint` exit 0,
and `npm run build` succeeded. No figure elsewhere in this document is
affected — the backend suite, the gates and the live probes all predate
the merge and touch none of the changed files.

**No figure in this addendum was simulated, extrapolated, or carried
forward.** Every number is from a command run during this pass, and the
one artefact inherited from earlier in the session (the container image)
was re-verified by executing it rather than trusted.
