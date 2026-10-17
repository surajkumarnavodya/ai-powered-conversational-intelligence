# Phase Zero — Repository Assessment & Execution Plan (Certification 53–60)

**Date:** 2026-10-16
**Baseline commit:** `bea4a34` on `feat/source-connectivity-audit` (26 ahead of `origin/`, working tree clean at assessment start)
**Scope:** Phase Zero only — assessment, baseline, capability matrix, plan.
**No application code was changed.** No commits, no pushes, no destructive commands.

---

## 1. Executive summary

The platform already carries **most of the certification machinery prompts
53–60 ask for**, built across prompts 01–52: a 57-case Text-to-SQL
benchmark with a recorded baseline, a 500-case prompt-injection security
benchmark, a deterministic component benchmark, a containerised k6 load
stack, 25 adversarial security test files, and a six-job CI pipeline. The
backend suite is **7,197 passing**; the frontend is **1,147 passing**.

So the certification problem here is **not** "build the tooling". It is
**three evidence problems**:

1. **The AI-accuracy evidence is void, by its own admission.** The recorded
   baseline is from 2026-09-01 and `docs/EVALUATION_CURRENT.md` carries a
   banner saying its figures "are **not** representative of current
   behaviour" because they predate a prompt-truncation fix. A *second*
   truncation fix (`rag/llm.py`, commit `0097cf6`) landed on 2026-10-16.
   There is currently **no valid accuracy number for this platform.**
2. **Recovery has never been drilled.** Backup, restore and rollback are
   documented, and `docs/PRODUCTION_READINESS_RELEASE_GATE.md` says so
   plainly: "documented and code-level-verified … **not drilled**". No
   RPO/RTO target exists anywhere.
3. **No UAT has ever happened.** There is no UAT record in the repository.

Everything else is largely a matter of **re-running existing harnesses and
recording the result**, which is mostly possible on this machine: Ollama is
live with `llama3.1:8b`, and the benchmark's exact target database
(`AdventureWorksDW2025`) is connected and indexed with 36 tables.

**The one hard blocker is Docker**: the CLI is installed (29.8.0) but the
daemon is not running, which blocks the k6 load stack (prompt 55) and the
Trivy container scan.

### Relationship to the existing release gate — read this before approving

`docs/PRODUCTION_READINESS_RELEASE_GATE.md` (2026-10-03, Prompt 25)
declares itself "the authoritative production-readiness source for this
project" and returned **CONDITIONALLY READY**. Prompts 53–60 cover much of
the same ground 13 days and 26+ commits later.

**I propose to supersede its verdict, not its evidence**, exactly as it did
to its own predecessors — adding a new dated document rather than editing
it. Confirming that is part of what I am asking approval for.

---

## 2. Architecture inventory

Condensed; the authoritative map is
[`docs/architecture/context-map.md`](../architecture/context-map.md).

| Layer | Actual stack | Certification relevance |
|---|---|---|
| UI | React 19 + Vite + TS + Tailwind + Zustand + TanStack Query, i18n ×5 | 53, 59 |
| API | FastAPI, ~70 route modules, `api/main.py` (`/ask`, `/execute`, `/health`, `/metrics/*`) | 53, 54, 55 |
| Orchestration | LangGraph: 18-node SQL graph + multi-source graph above it | 53, 56 |
| SQL safety | `agent/sql_validator.py` — sqlglot AST allowlist | 54, 57 |
| Governance | `governance/` request/result/recommendation policy, 19 domains | 54, 57 |
| Identity | PostgreSQL + SQLAlchemy 2.0 + Alembic (**the only real migrations**) | 54, 58 |
| Retrieval | ChromaDB (schema + business context), SQL Server `VECTOR` (documents) | 56, 58 |
| LLM | Ollama, local, `llama3.1:8b` | 55, 56 |
| Observability | `observability/metrics.py`, `GET /metrics/performance`, `/metrics/harness` | 53, 55 |
| Deployment | `Dockerfile` (digest-pinned), `docker-compose.yml`, `Makefile`, `tasks.ps1` | 55, 58 |

**Live environment confirmed on this machine:** 4 MSSQL databases reachable
(`hr`/HrAutomationDb, `adventureworks`/**AdventureWorksDW2025**,
`employee_management_database`/EmployeeManagementDB, `test_db`/TestDB), and
Ollama reachable at `localhost:11434`. Database names only are recorded
here; no host, user or credential is reproduced anywhere in this document.

### Existing certification infrastructure (do not rebuild)

| Asset | What it is |
|---|---|
| `eval/benchmark/*.yaml` | 57 Text-to-SQL cases: easy/medium/hard/real_world/adversarial/follow_up/cost_estimation |
| `scripts/run_benchmark.py` | Runs them live; `--save-baseline`, `--check-regression` |
| `eval/baselines/latest.json` | Recorded baseline `run_20260901T151533Z` |
| `eval/security_benchmark/` | 500-case prompt-injection corpus + runner, detectors, multiturn |
| `scripts/run_security_benchmark.py` | Live runner, role-parameterised (`viewer` default) |
| `eval/component_benchmark/` | 9 deterministic case modules incl. `cases_calculations.py` |
| `eval/load/` | k6 scripts, `docker-compose.loadtest.yml`, mock Ollama, user seeder |
| `tests/security/` | 25 adversarial regression files |
| `scripts/integration_test.py` | Manual live end-to-end against real databases, read-only |
| `scripts/harness_stress_test.py`, `profile_pipeline.py` | Resilience and profiling |
| `.github/workflows/ci.yml` | 6 jobs: secret-scan, test, frontend, docker-scan, load-test-smoke, benchmark-regression |

---

## 3. Baseline (measured this session)

All at `bea4a34`. Commands and verbatim results:

| Check | Command | Result |
|---|---|---|
| Backend tests | `pytest -q` | **7197 passed, 11 deselected**, 425.94s |
| Frontend tests | `npm run test` | **1147 passed, 88 files** |
| Frontend types | `npx tsc --noEmit -p tsconfig.app.json` | Clean |
| Frontend lint | `npm run lint` | Exit 0; warnings only, all pre-existing |
| Frontend build | `npm run build` | Succeeds |
| Backend format | `black --check .` | Clean, 1632 files |
| Backend lint | `ruff check .` | **1 finding** — `agent/multiagent/agents.py:23 I001` |
| Backend types | `mypy .` | Pre-existing errors; CI records 281 across 51 files, all in test files |

**Pre-existing failures, not caused by this work** (both named in
[`CLAUDE.md`](../../CLAUDE.md) §13): the single `ruff` I001 finding, and the
`mypy` backlog. The 11 deselected tests are `tests/live/`, opt-in by design.

### Tooling availability — this decides what can be certified

| Tool | State | Consequence |
|---|---|---|
| Ollama + `llama3.1:8b` | **Running** | 56 and the security benchmark are executable |
| `AdventureWorksDW2025` | **Connected, 36 tables indexed** | 56/57 executable against the baseline's own database |
| `bandit` 1.9.4, `pip-audit` 2.10.1, `detect-secrets` | **Installed** | 54 SAST + dependency scan executable |
| Docker CLI 29.8.0 | **Installed, daemon NOT running** | **Blocks** k6 load stack (55) and Trivy scan |
| `k6` native | Not installed | Only reachable via Docker |
| Real users | None | **Blocks** 59 |

---

## 4. Capability matrix

Classification per the brief: (1) implemented & verified, (2) implemented
but insufficiently tested, (3) partially implemented, (4) implemented but
defective, (5) missing, (6) N/A, (7) unable to verify.

| Req | Area | Class | Existing implementation | Evidence today | Gap | Risk of change |
|---|---|---|---|---|---|---|
| 53-A | Auth / RBAC / tenant isolation | **1** | `agent/authz.py`, `identity/rbac.py`, `security/tenancy.py` | `tests/security/test_cross_tenant_isolation.py` + 24 more, all green | Live E2E not re-run | High — do not touch |
| 53-B | DB onboarding → semantic review → SQL → analytics journey | **2** | `api/onboarding.py`, `agent/graph.py` | `tests/test_journey_onboarding_to_recommendation.py` (mocked) | Never run live end-to-end this session | Medium |
| 53-C | Health / readiness / startup validation | **1** | `/health`, `/live`, startup warm-up + write-privilege check | Observed live: 4 DBs + Ollama OK | — | Low |
| 53-D | Observability | **1** | `observability/metrics.py`, `/metrics/*`, `PlatformAdmin.tsx` | Per-worker, resets on restart (disclosed) | No live load profile | Low |
| 54-A | SQL injection / AST allowlist | **1** | `agent/sql_validator.py` | `test_sql_validator.py` (31) + `_hardening.py` (18) | — | **Very high — never weaken** |
| 54-B | Prompt-injection defence | **2** | `security/injection_patterns.py`, data-framing, `sanitize_conversation_history` | 500-case corpus exists; **last run 2026-09-25, only 50 cases** | 90% of corpus unrun; 21 days stale | Low (re-run only) |
| 54-C | Secrets management | **1** | `SecretStr`, `security/redaction.py`, `detect-secrets` in CI | Green | Not re-scanned this session | Low |
| 54-D | Dependency vulnerabilities | **2** | `pip-audit` in CI with justified ignores | Not run this session | Re-run needed | Low |
| 54-E | Container image CVEs | **7** | Trivy in CI (`continue-on-error`) | **Unable to verify** — Docker daemon down | Blocked | — |
| 55-A | Admission control / concurrency | **1** | bounded pool + per-caller cap → 429 | `tests/test_harness_runtime.py` (39) | No measured load | Low |
| 55-B | Measured capacity envelope | **5** | k6 stack exists, never run here | **No measurement** | **Blocked by Docker** | — |
| 55-C | Timeouts / row limits | **1** | Double row cap, driver timeout | `db/execution.py` tests green | — | **High — never weaken** |
| 56-A | SQL / join / metric accuracy | **4 (evidence)** | 57-case benchmark | Baseline 2026-09-01, **self-disclaimed invalid** | **No valid current number** | Low (re-run only) |
| 56-B | Deterministic component accuracy | **2** | `eval/component_benchmark/` + baseline | Boolean baseline recorded, not re-run | Re-run needed | Low |
| 56-C | Groundedness / refusal | **2** | `cases_groundedness.py`, adversarial cases | Within the stale run | Re-run needed | Low |
| 57-A | Calculation correctness | **2** | `cases_calculations.py` (deterministic, no LLM) | Baseline recorded | Re-run + independent reconciliation | Low |
| 57-B | LLM cannot override computed values | **1** | `agent/provenance.py`, `tests/security/test_*_never_presents_*_as_fact.py` (7 files) | Green | — | **High** |
| 58-A | Backup / restore procedure | **3** | `docs/DEPLOYMENT.md` sections | Documented, **explicitly never drilled** | Drill needed | Medium |
| 58-B | Rollback (image / migration / flag) | **3** | Digest-pinned images, `alembic downgrade`, `.env` flags | Documented, every `downgrade()` read but not executed | Drill needed | Medium |
| 58-C | RPO / RTO targets | **5** | — | **Absent** (only a TODO at `SCALE_OUT_PROMPT.md:328`) | Must be defined by you | — |
| 59-A | UAT with representative users | **5** | — | **No UAT record exists** | **Needs real humans** | — |
| 60-A | Consolidated release gate | **3** | Prompt 25 gate, 2026-10-03, CONDITIONALLY READY | 13 days + 26 commits stale | Supersede with new dated gate | Low |

---

## 5. Findings already identified (Phase Zero, before any testing)

| ID | Sev | Area | Finding | Evidence |
|---|---|---|---|---|
| **F-001** | P3 | Docs/CI | `CLAUDE.md` §5.7 lists `mypy` as a **blocking** CI gate and §13 says only `gitleaks` and Trivy are non-blocking. In fact **four** steps are `continue-on-error`: gitleaks, **mypy**, Trivy, **load-test-smoke**. | `.github/workflows/ci.yml` lines 68, 222, 318, 353 |
| **F-002** | P2 | DR | **No RPO/RTO defined anywhere.** Prompt 58 requires documented targets. | Only occurrence is the TODO `docs/SCALE_OUT_PROMPT.md:328` "Define RPO/RTO" |
| **F-003** | P2 | DR | Backup/recovery and rollback are documented but **never exercised**. | `PRODUCTION_READINESS_RELEASE_GATE.md:78` — "documented and code-level-verified …, not drilled" |
| **F-004** | **P1** | AI quality | **No valid accuracy evidence exists.** The only baseline predates two prompt-truncation fixes; its own doc says figures "are **not** representative of current behaviour". Recorded `result_set_accuracy` was **0.296**. | `eval/baselines/latest.json` (2026-09-01); `docs/EVALUATION_CURRENT.md` banner |
| **F-005** | P2 | Security evidence | Security benchmark last run **2026-09-25** (21 days) and covered only **50 of 500** cases. `eval/security_baselines/` **does not exist**, so the runner's advertised `--check-regression` cannot work. | `eval/security_results/secrun_20260925T172655Z_full.json`; directory absent |
| **F-006** | P2 | UAT | **No UAT record exists.** | No matching document in `docs/` |

None of these is a demonstrated vulnerability. F-004 is P1 because shipping
an AI analytics platform with **no valid accuracy measurement** is a
release-blocking evidence gap, not because a defect is known.

---

## 6. Proposed execution plan

Ordered by dependency and value. Nothing here runs until you approve.

### Stage 1 — Zero-risk, read-only, fully automatable (~45 min)

| # | Task | Prompt | Output |
|---|---|---|---|
| 1.1 | `pip-audit`, `bandit`, `detect-secrets` scans | 54 | Current vulnerability status |
| 1.2 | Re-run component benchmark vs baseline | 56, 57 | Deterministic regression check |
| 1.3 | Fix **F-001** (CLAUDE.md gate list) — 2-line doc correction | — | Accurate gate documentation |
| 1.4 | Live `scripts/integration_test.py` (read-only, real DBs) | 53 | Live journey evidence |

### Stage 2 — Live model runs, read-only, long (~3–5 h unattended)

| # | Task | Prompt | Note |
|---|---|---|---|
| 2.1 | Full 57-case Text-to-SQL benchmark vs `latest.json` | 56 | **Closes F-004.** Real LLM round trips against AdventureWorksDW2025 |
| 2.2 | Security benchmark, **all 500 cases**, `--roles viewer` | 54 | **Closes F-005.** Then `--save-baseline` to create the missing baseline |
| 2.3 | Independent SQL reconciliation for calculations | 57 | Hand-computed expected values vs engine output |

These are read-only against your databases and use the **local** Ollama —
no external provider, no cost, no writes. 2.1 and 2.2 are slow (each case
is a real round trip) so I would run them in the background.

### Stage 3 — Needs Docker running (you start Docker Desktop)

| # | Task | Prompt |
|---|---|---|
| 3.1 | k6 load stack: baseline → load → stress → recovery | 55 |
| 3.2 | Trivy container scan | 54 |
| 3.3 | Migration up/down drill on the throwaway Postgres | 58 |

### Stage 4 — Needs your decisions or participation

| # | Task | Prompt | What I need from you |
|---|---|---|---|
| 4.1 | Define RPO/RTO targets | 58 | **Your business decision** — I can propose defaults |
| 4.2 | Backup/restore drill | 58 | Confirmation to drill against *isolated* resources only |
| 4.3 | UAT with 6 representative roles | 59 | **Real humans.** I can prepare scripted journeys and a results template; I cannot perform it |
| 4.4 | Final release gate | 60 | Consolidation after 1–3; **conditional on 4.1–4.3** |

### Explicitly out of scope unless you direct otherwise

Production databases, production deployments, destructive fault injection,
external/paid APIs, and any weakening of a security control.

---

## 7. What cannot be certified here, and why

Recorded now so the final gate cannot quietly imply otherwise.

| Requirement | Status | Reason |
|---|---|---|
| Measured capacity envelope (55) | **NOT TESTED** until Docker runs | Daemon down |
| Container CVE scan (54-E) | **NOT TESTED** until Docker runs | Daemon down |
| Real-user UAT (59) | **NOT TESTED** | Requires human participants; cannot be simulated |
| Backup/restore RTO (58) | **NOT TESTED** | Requires a drill against isolated resources |
| DAST against a live deployment | **NOT TESTED** | No deployed environment exists |
| OIDC against a real IdP | **NOT TESTED** | Pre-existing, disclosed in `CLAUDE.md` §13 |
| Multi-worker behaviour | **NOT TESTED** | Single-process dev environment; per-worker state is disclosed |

---

## 8. Decisions I need from you

1. **Approve the plan**, or amend the staging.
2. **Supersede or amend?** May the new gate supersede
   `docs/PRODUCTION_READINESS_RELEASE_GATE.md`'s *verdict* (keeping its
   evidence, adding a pointer), as it did to its own predecessors?
3. **Docker** — start Docker Desktop to unblock Stage 3, or accept
   performance and container CVEs as NOT TESTED?
4. **RPO/RTO** — give me targets, or shall I propose defaults for your sign-off?
5. **UAT** — can you provide participants, or should I deliver scripted
   journeys plus a results template and mark 59 NOT TESTED?
6. **Long runs** — Stage 2 is several hours of local LLM work. Run it?

**No application changes have been made. Awaiting approval.**
