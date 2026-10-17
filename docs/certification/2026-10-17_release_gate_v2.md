# Production Release Gate v2 — 2026-10-17

**Assessment date:** 2026-10-17
**Baseline commit:** `779d7fc` on `feat/source-connectivity-audit`
**Supersedes the *verdict* of:** [`../PRODUCTION_READINESS_RELEASE_GATE.md`](../PRODUCTION_READINESS_RELEASE_GATE.md) (2026-10-03, **CONDITIONALLY READY**)
**Preserves:** that document and all its evidence, unmodified
(sha256 `54c81ff030ac2ad9…`, verified unchanged at the start and end of this pass)

---

## 1. Verdict

# NO-GO for production release

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
