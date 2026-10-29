# Phase 8 — Engineering best practices audit

**Date:** 2026-10-29
**Branch:** `uat-readiness/phase-8-best-practices`
**Scope:** Python type coverage and exception/resource/async correctness,
test quality and coverage, CI gates, dependencies and licences, code
health, repo hygiene.

**Twelve findings: 0 S1, 1 S2, 7 S3, 4 S4.** Eight fixed, three flagged
for an owner decision, one is a positive result recorded as evidence.

**Final gate state:** `pytest` **9,571 passed / 0 failed**;
`ruff check .` **0**; `black --header` clean over 1,683 files;
`mypy` over the 41 production packages **0 errors in 482 source files**;
`bandit -c pyproject.toml -r .` **0 issues over 119,384 lines**;
frontend `lint` 0 errors, `tsc` 0, `vitest` **1,508 passed**,
`npm audit --omit=dev` **0 vulnerabilities**.

---

## 0. This phase inherited an unfinished session, and that matters

A previous session ran most of this phase and was closed before
committing. It left **21 modified files and one new test file, entirely
uncommitted**, no `phase-8-best-practices.md`, and no register rows. Its
four findings were numbered `BP-004`..`BP-007`, which collided with
`BP-001`..`BP-004` already in the register from Phases 0, 3 and 6.

Its analysis was good and its four findings are real — they are
`BP-005`..`BP-008` below, renumbered. **But the work had not been run**,
and verifying it found three defects:

1. **It broke the application.** To clear four mypy errors it widened
   `identity/models.py`'s column annotations to `Mapped[uuid.UUID | None]`
   and `Mapped[str | None]`. That is the one change the file's own module
   docstring explicitly forbids:

   > **A new "Python 3.14 gotcha" […]: a nullable column below is never
   > annotated `Mapped[X | None]`/`Mapped[Optional[X]]`.** SQLAlchemy
   > 2.0.36's declarative annotation resolution […] crashes with
   > `TypeError: descriptor '__getitem__' requires a 'typing.Union'
   > object but received a 'tuple'` […] Every nullable column here is
   > instead annotated with its bare (non-Optional) Python type […] so
   > this is a real, correct, if slightly typing-lossy (mypy sees the
   > attribute as non-Optional) workaround, not a hidden bug.

   The docstring states that *these very mypy errors are the accepted
   cost of the workaround*. Measured: `python -c "import api.main"`
   raised that exact `TypeError`, so `api/main.py` — the whole
   application — would not import. Reverted, and the four errors closed
   instead with line-scoped `# type: ignore[assignment]` citing the
   docstring.

2. **It left a failing test.** Changing
   `assert_no_remote_tool_is_registered` to raise
   `RemoteToolRegistrationError` instead of `AssertionError` broke
   `tests/test_mcp_client.py::test_a_qualified_remote_name_would_be_detected`,
   which still expected the old type.

3. **It left `ruff` and `black` failing** on three files.

Its central claim — "mypy over all 41 production packages reports 0
errors in 482 source files" — was therefore only reachable *via the
change that broke the application*. With a working app the true figure
was 4 errors in 2 files; it is 0 now, by a different route.

**None of this is a criticism of the analysis, and all of it is the
reason rule 10 exists.** It is recorded as `BP-015` because the failure
mode generalises: a type error and a runtime constraint can point in
opposite directions, and when a module docstring says "mypy is wrong
here, on purpose", that is evidence and not a to-do.

---

## 1. Python

### 1.1 Type coverage — `BP-001` (closed)

`BP-001` was raised in Phase 0 as "`mypy` reports 723 errors in 129
files; 7 are production code" and deferred here.

Re-measured 2026-10-29:

| scope | result |
|---|---|
| 41 production packages | **0 errors in 482 source files** |
| `mypy .` (everything) | 746 errors in 131 files, of 903 checked |

So **every production error is closed** and the remainder is the
`tests/` hygiene backlog CLAUDE.md §13 already names. Ten production
fixes were needed, two of them in the security boundary:

* `agent/sql_validator.py::_render_shape_items` took `nodes: object` and
  carried a `# type: ignore[union-attr]` that no longer covered the line
  being reported — exactly the drift CLAUDE.md §13 flags. The signature
  is now `Iterable[exp.Expression] | None` and the ignore is **deleted**
  rather than re-aimed.
* `db/schema_introspection.py` and `agent/orchestrator/graph.py` drop
  stale ignores the same way. The schema-introspection change is a
  no-op at runtime: the surrounding comprehension already carries
  `and all(col is not None for col in ix["column_names"])`, so the added
  inner filter cannot change which indexes are returned — verified
  before accepting it.
* The four SQLAlchemy `SQLCoreOperations[...]`-vs-`None` errors in
  `identity/repositories/` are closed with line-scoped
  `# type: ignore[assignment]`, **not** by widening the model
  annotations (see §0).

CI now has a **second, blocking** mypy step, deliberately last in the
job. The existing report-only `mypy .` stays exactly as it is, for the
reason its own comment gives — this job's steps run sequentially and a
blocking type gate placed above `bandit`/`pytest`/`pip-audit` once
stopped all three from running. That argument is about *position*, not
about whether a type error may land, and it had been doing duty for
both.

### 1.2 Exception handling — clean (`BP-016`)

* **Bare `except:` in production: zero.**
* **`except Exception` / `except BaseException`: 135 occurrences**, of
  which 114 carry a `# noqa: BLE001` with a written justification. Of
  the 21 without, four are docstring prose rather than handlers; every
  one of the rest was read, and all of them either log with
  `exc_info=True` or re-raise. **No swallowed errors found.**

### 1.3 Async correctness — clean (`BP-016`)

Two AST scans over every production module:

* blocking calls (`time.sleep`, `requests.*`, `subprocess.*`, `socket.*`,
  `urllib.request`, `os.system`, `input`) lexically inside an
  `async def`, not descending into nested sync `def`s: **0 occurrences**;
* synchronous work (`run_agent`, `run_orchestrated`,
  `execute_readonly_sql`, `session.commit/execute/query`, `get_engine`)
  inside an `async def`: **0 occurrences**.

This is consistent with the deliberate design Phase 6 recorded: `POST
/ask` is a sync `def` so Starlette runs it in a threadpool, while
`/ask/stream` is async and does its blocking work on
`_get_ask_executor`'s pool.

### 1.4 `print()` debugging — one violation, and it is dead code

CLAUDE.md §6.2 allows `print()` only in the standalone CLI scripts meant
to be read as terminal output. Production packages: **zero**. The one
exception is `ai_generator_client.py` (9 calls), which is `BP-010` —
dead code at the repository root, not under `scripts/`.

### 1.5 Dead code — `BP-010` (**flagged**)

`ai_generator_client.py`: **593 lines, zero importers, zero tests**, the
only top-level Python module in a repo that otherwise keeps every entry
point under `scripts/`. It implements image generation across
pixazo/cloudflare/ima_studio — the job `media_gen/providers/` already
does.

Harmless today only because nothing imports it. Wiring it up would
reintroduce four controls at once:

1. it requests provider URLs with `requests` directly and never calls
   `security/outbound_url.py`, so **no SSRF validation** (CLAUDE.md §5.6);
2. it reads credentials from `os.environ` rather than `Settings`, so its
   API keys are plain `str`, not `SecretStr`;
3. line 57 calls **`logging.basicConfig()` at module import time**, so
   importing it anywhere reconfigures the root logger for the whole
   process and overrides the application's own `LOG_LEVEL` handling;
4. it is a second owner of a capability `media_gen/` already owns, which
   CLAUDE.md §6.1.2 calls a defect regardless of test results.

**Not deleted**, because removing 593 lines of provider integration is a
product decision, not a local one — it may be a staging ground for a
provider `media_gen/` does not cover. Guarded instead by
`tests/test_ai_generator_client_is_unwired.py`, whose `test_nothing_imports_it`
fails the moment somebody imports it, and whose third test fails if one
of the four bypasses is fixed in place so the register entry is
corrected rather than quietly becoming wrong.

**Decision needed:** delete it, or adopt it into `media_gen/providers/`
behind the existing controls.

### 1.6 Resource cleanup and Pydantic validation

Connections and file handles: `db/connection.py` disposes engines through
its own lifecycle, every `open()` in production is a context manager, and
Phase 6 separately verified the harness releases its admission slots via
`on_done`. Pydantic v2 validation is on every request model, and Phase 6
confirmed `extra="forbid"` blocks mass assignment everywhere. No new
findings.

---

## 2. Tests

### 2.1 Coverage of security-critical packages — `BP-011`

**Coverage had never been measured in this project.** No coverage
tooling was declared anywhere — not `requirements-dev.txt`, not
`pyproject.toml` — and CI has no coverage step. Measured here by
installing `pytest-cov` ad hoc, which is precisely the un-repeatable
state `BP-009` is about, so it is now pinned in
`requirements-dev.txt`.

Measured over the whole mocked suite, 2026-10-29:

| package | statements | missed | coverage |
|---|---|---|---|
| `governance/` | 699 | 9 | **98.7%** |
| `identity/` | 2,962 | 88 | **97.0%** |
| `security/` | 598 | 24 | **96.0%** |
| `api/` | 8,093 | 442 | **94.5%** |
| `guardrails/` | 224 | 15 | **93.3%** |
| **all five** | **12,576** | **578** | **95.4%** |
| `agent/sql_validator.py` | 386 | 23 | **94%** |
| `agent/input_guard.py` | 64 | 1 | **98%** |

**Every security-critical package clears the brief's 90% bar.** Thirteen
individual files do not, and they are the honest detail:

| coverage | file | missed |
|---|---|---|
| 69.6% | `identity/db.py` | 7/23 |
| 69.9% | `api/jobs.py` | 56/186 |
| 71.4% | `identity/email.py` | 2/7 |
| 76.1% | `api/chat_persistence.py` | 37/155 |
| 76.7% | `identity/repositories/audit.py` | 7/30 |
| 77.3% | `identity/repositories/tenants.py` | 10/44 |
| 79.7% | `api/streaming.py` | 14/69 |
| 83.3% | `identity/share_audit.py` | 3/18 |
| 84.2% | `api/shares.py` | 58/366 |
| 84.5% | `security/oidc.py` | 15/97 |
| 88.5% | `guardrails/config.py` | 6/52 |
| 88.7% | `api/onboarding.py` | 23/204 |
| 89.9% | `api/main.py` | 63/626 |

Two of these are worth naming. `security/oidc.py` at 84.5% is the lowest
security-critical file, and CLAUDE.md §13 already records that **OIDC has
never been verified against a live IdP** — so the untested lines and the
untested integration are the same gap seen twice. `api/shares.py` at
84.2% (58 lines) matters because a share is the sharpest re-governance
case in the codebase (§5.4: an "anyone with the link" viewer holds no
roles at all).

**No coverage gate was added to CI.** Picking a threshold is a product
decision, and a number an auditor chose to match today's measurement is
a ratchet nobody agreed to.

### 2.2 Assertion quality — clean (`BP-016`)

Scanned all **6,713** test functions for any assertion (`assert`,
`pytest.raises`/`warns`, or an `assert_*` helper call). **57 (0.85%)**
have none. Every one was read by name and a sample read in full: they
are tests of code that *raises on violation*, so returning is the
assertion — `test_allows_calls_under_the_limit` even carries
`# must not raise` inline, and `test_own_conversation_is_allowed` calls
`require_owned_conversation` for the same reason. Not a finding.

### 2.3 Tests hitting real infrastructure — clean (`BP-016`)

`pyproject.toml` sets `addopts = "-m 'not live'"`, so the one opt-in file
(`tests/live/test_live_connectors.py`) is deselected by default. No
unmarked real outbound call exists in `tests/`. Phases 5 and 7 separately
closed the two cases where mocking *had* hidden real behaviour — the real
Ollama call (`_block_real_ollama_calls`) and the real database connections
leaking through env (`_DYNAMIC_ENV_PREFIXES`) — both now guarded and
mutation-verified, and both are the reason this item was checked rather
than assumed.

---

## 3. CI

### 3.1 `BP-005` — the SAST gate was scoped to 12 of ~40 packages (S3, fixed)

`bandit` named twelve packages on the command line, so it never looked at
`identity/`, `governance/`, `governance_center/`, `guardrails/`, `jobs/`,
`attachments/`, `mcp_client/`, `onboarding/`, `semantic/`, `analytics/`,
`scripts/` or roughly twenty-five others. Measured both scopes on the
same checkout:

| scope | lines | findings |
|---|---|---|
| old 12-package list | 58,041 | 0 |
| whole tree | **119,384** | **24** (22 Low, 2 Medium) |

So "the gate is green" had been a statement about **48.6% of the code**.
Six of the 24 were in `identity/security.py` — the authentication module
— and four of those are the `assert`-guarded JWT key checks in `BP-006`.
The two Medium were B608 in `scripts/integration_test.py` and B603 in
`mcp_client/transport.py`.

Fixed with `[tool.bandit]` in `pyproject.toml` rather than a longer list,
so a new package is in scope the day it is created. CI and a local run
now use the identical command, which they did not before. Each remaining
finding is a line-scoped `# nosec` naming the check and why it is benign.

**The suppressions are not vacuous:** the same command against
`git archive HEAD` still reports the 24.

### 3.2 `BP-006` — three controls relied on `assert` (S3, fixed)

`python -O` and `PYTHONOPTIMIZE=1` remove every `assert` from the
bytecode, so under optimised bytecode these three boundaries would not
exist:

* **`identity/security.py`** — four asserts guarded the JWT signing and
  verification keys. With the assert gone the next line calls
  `.get_secret_value()` on `None`, turning a clear configuration failure
  into an `AttributeError` surfacing as an opaque HTTP 500 **on every
  login**.
* **`mcp_client/trust.py`** — `assert_no_remote_tool_is_registered` keeps
  a remote MCP server's tools out of the local `ToolRegistry`. A trust
  boundary, documented as one, implemented as an `assert`.
* **`mcp_client/protocol.py`** — the embedded-newline check on an
  outgoing JSON-RPC line. The transport is newline-delimited, so an
  embedded newline is message smuggling.

Narrowing-only asserts elsewhere are deliberately left as asserts with a
`# nosec B101` naming the real guarantee. The distinction that matters is
whether removing the statement removes a control.

Tested in a subprocess, the only honest way, since `-O` is interpreter
start-up state. `test_asserts_really_are_stripped_under_dash_o` proves
the premise first, so the rest cannot pass vacuously.

### 3.3 `BP-008` — the frontend job ran none of its tests (S3, fixed)

The `frontend` job did lint, `tsc`, build and a bundle secret-scan.
`npm run test` was simply **absent**, while `frontend/` ships **1,508
vitest tests across 104 files** — all passing, verified here in 85s. A
frontend regression could only be caught by somebody running it locally.
`npm audit --omit=dev` is added for the same reason (`pip-audit` had no
frontend counterpart at all) and reports **0 vulnerabilities** with no
ignore list.

### 3.4 `BP-007` — seven actions floated on tags (S3, fixed)

Six `actions/*` steps and `gitleaks/gitleaks-action@v2` were pinned to
mutable tags. All are now commit SHAs with the tag in a trailing comment.
The `trivy-action` comment already in the file explains why: tags were
force-pushed to malicious commits across **76 of 77** of them. `gitleaks`
is report-only, but a report-only step still executes on the runner with
the job's token, so "cannot fail the build" is not "cannot do harm".

### 3.5 Gate inventory after this phase

| gate | blocking? | verified |
|---|---|---|
| `ruff check .` | yes | 0 findings |
| `black --check .` | yes | clean, 1,683 files |
| `bandit -c pyproject.toml -r .` | yes | 0 over 119,384 lines |
| `pytest` | yes | 9,571 passed |
| `pip-audit` | yes | per Phase 2 |
| `detect-secrets` | yes | **still red** — stale baseline, CLAUDE.md §13 |
| `mypy` (production, 41 packages) | **yes, new** | 0 in 482 files |
| frontend `lint` / `tsc` / `build` | yes | 0 / 0 / ok |
| frontend `vitest` | **yes, new** | 1,508 passed |
| `npm audit --omit=dev` | **yes, new** | 0 vulnerabilities |
| `mypy .` (everything) | no, report-only | 746 in 131 files |
| `gitleaks`, Trivy, load-test smoke | no, report-only | dated decisions, CLAUDE.md §13 |

So the blocking set goes from six to **nine**. `permissions:` was already
least-privilege per job and `persist-credentials: false` already set on
every checkout — no finding.

`tests/test_ci_gate_coverage.py` (14 tests) is what stops this falling
behind: the type gate's package list is checked against every tracked
top-level package, the bandit config is checked for exclusion-based
scoping, the frontend steps are checked to exist, and no step may use a
floating tag.

---

## 4. Dependencies

### 4.1 `BP-009` — six packages imported directly but never declared (S3, fixed)

`requirements.txt` already carried the scar of this bug class:

```
pyyaml==6.0.3   # config/table_descriptions.py -- was only a transitive dep before
```

Six more had the same shape. Each is imported by production code and was
installed **only because some other package depended on it**:

| package | imported by | present via |
|---|---|---|
| `requests` | `security/outbound_url.py` + 3 `media_gen/` modules | CacheControl, detect-secrets, kubernetes, langsmith, pip_audit, requests-oauthlib, requests-toolbelt |
| `httpx` | `agent/llm_client.py`, `rag/llm.py`, `search/web_search.py`, `security/oidc.py` + 7 more | chromadb, huggingface_hub, langchain-core, langgraph-sdk, ollama |
| `av` | `voice/stt.py` | faster-whisper |
| `huggingface_hub` | `media_gen/providers/huggingface.py` | faster-whisper, sentence-transformers, tokenizers, transformers |
| `numpy` | `attachments/inpaint.py` | chromadb, pandas, scikit-learn + 8 more |
| `starlette` | `security/client_ip.py` | fastapi |

Two are worth naming individually. **`requests` is what
`security/outbound_url.py` — the SSRF validator — makes its requests
with.** **`starlette` is imported by `security/client_ip.py`**, which
decides whether `X-Forwarded-For` is trusted and is therefore on the
rate-limiting path (§5.7). Relying on FastAPI to keep re-exporting a
compatible Starlette is a reasonable bet and still an undeclared one.

All six pinned to the versions this checkout already resolves, so nothing
installed changes; `pip install --dry-run -r requirements.txt` reports no
conflict. `tests/test_declared_dependencies.py` derives the requirement
from the source tree with `ast` + `importlib.metadata.packages_distributions()`,
which is why **it found four of the six that inspection had missed**.

### 4.2 `BP-012` — AGPL and GPL dependencies in an MIT project (**S2, flagged**)

The brief asks for licence compatibility with MIT. Audited all 46
declared production dependencies. Forty-four are MIT/BSD/Apache/PSF and
fine. Two are not:

| package | licence | imported by | gated? |
|---|---|---|---|
| **`pymupdf==1.28.2`** | **"Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License"** | `attachments/processors/pdf_processor.py`, `rag/ingestion.py` | **No — `enable_chat_attachments` defaults to `True`** |
| `piper-tts==1.8.0` | `GPL-3.0-or-later` | `voice/tts.py` | `ENABLE_VOICE_MODE` |

`LICENSE` in this repository is the MIT licence.

**Why this is S2 and not S4.** AGPL-3.0 is the strongest copyleft and
carries a network clause: providing network access to software that uses
AGPL code obliges you to offer the complete corresponding source of the
whole work under AGPL. This platform **is** a network-served web
application, `pymupdf` is on a **default-enabled** path (PDF chat
attachments), and both it and `piper-tts` ship inside the single Docker
image. The alternative to AGPL-ing the project is an Artifex commercial
licence, or replacing the PDF parser across two modules — and `pypdf`
(BSD-3-Clause) is *already* a declared dependency used elsewhere in
`attachments/`, so a replacement path plausibly exists.

It does not block UAT *execution*, and an owner may reasonably re-grade
it to S3-with-waiver if UAT is internal-only and never distributed. It is
S2 here because UAT sign-off implies intent to ship, and this is far
cheaper to resolve before two modules are built on it than after.

**This is a legal question, not an engineering one, and is explicitly not
mine to decide.** Flagged with no code change.

**Decision needed:** obtain an Artifex commercial licence; or replace
`pymupdf` with `pypdf`/`pdfminer.six` and drop `piper-tts`; or relicense
the project; or document that it is never distributed outside the
organisation.

`psycopg2-binary` ("LGPL with exceptions") was checked and is **fine** —
psycopg's own exception permits use from differently-licensed code.
`torch` reports no top-level `License` but its `License-Expression` is
`Apache-2.0 AND … AND BSD-3-Clause AND BSL-1.0 AND MIT`: benign.

### 4.3 Pins resolvable on 3.11

`requires-python` is `>=3.11` and CI runs 3.11, while this machine's venv
is 3.14 (CLAUDE.md §2). **UNVERIFIED here**: nothing in this environment
can resolve the set against 3.11, so the "scipy incident" class of
breakage cannot be re-tested locally. `pip install --dry-run -r
requirements.txt` resolves cleanly on **3.14**, and CI's own install step
is the real check. A human on 3.11, or CI, must confirm.

No unused declared dependencies were found: every one of the 46 maps to
at least one production import.

---

## 5. Code health — `BP-014` (S4, backlog)

Measured across 483 production modules and 2,708 functions.

**11 modules over 1,500 lines:**

| lines | module | comment lines | code |
|---|---|---|---|
| 4,509 | `config/settings.py` | 1,463 (32.4%) | 2,865 |
| 3,533 | `agent/nodes.py` | 385 (10.9%) | 2,827 |
| 2,816 | `domain_skills/catalog.py` | — | — |
| 2,786 | `agent/orchestrator/nodes.py` | — | — |
| 2,497 | `api/main.py` | 356 (14.3%) | 1,929 |
| 2,404 | `agent/llm_client.py` | — | — |
| 2,321 | `identity/models.py` | — | — |
| 1,850 | `api/schemas.py` | — | — |
| 1,721 | `agent/sql_validator.py` | 312 (18.1%) | 1,222 |
| 1,615 | `agent/context_resolution.py` | — | — |
| 1,593 | `analytics/models.py` | — | — |

**106 functions over 100 lines**, largest: `router_node` (440),
`run_agent` (373), `train_predictive_model` (359),
`run_supervised_analysis` (357), `execute` (328), `execute_sql_node`
(310), `ask` (303).

**Reported, not acted on, and the reason is worth stating.** A third of
`config/settings.py` is explanatory comment, by deliberate convention —
this codebase buys reviewability with prose, and the line counts
partly measure that rather than complexity. The large functions are the
graph nodes and route handlers at the centre of the request path, where
a refactor is a behavioural risk with no test-visible benefit; Phase 6
and Phase 7 both found real defects in `router_node` and `run_agent`
*without* needing them shorter. Splitting them is a maintainability
judgement for the owners, and it is not something to do as a side effect
of an audit (CLAUDE.md §6.1.3).

No duplicated logic was found beyond `BP-010`. Magic numbers: spot-checked
the thresholds Phases 6 and 7 touched and all are `Settings` fields or
named module constants with a comment giving the reason for the value.

---

## 6. Repo hygiene — clean, with one cosmetic finding

* **`.env` is not tracked**; only `.env.example`.
* **No committed build output**: no `frontend/dist/`, `node_modules/`,
  `.venv/`, `env/`, `*.pyc`, `embeddings/.chroma/` or `media_library/`.
* **No large binaries.** Largest tracked file is 756 KB
  (`docs/security/sbom/frontend-sbom.cyclonedx.json`); the next six are
  eval result JSONs at 460–710 KB, which are this project's accuracy
  record and belong in the repo.
* **No developer-machine paths.** A repo-wide search for `C:\Users`,
  `D:\OllamaModels`, `/home/<user>/` and `C:/Users` in tracked code found
  **two** hits, both `/home/app/.cache` in
  `eval/load/docker-compose.loadtest.yml` — container paths, correct as
  written.
* **`BP-013` (S4):** `LICENSE` reads `Copyright (c) 2026 <Your Name>` —
  the placeholder was never filled in. Not fixed: the copyright holder is
  not something an audit can invent. One-line fix for an owner, and it
  interacts with `BP-012`.

---

## 7. Findings summary

| ID | Sev | Title | Status |
|---|---|---|---|
| `BP-012` | **S2** | `pymupdf` is AGPL-3.0 on a default-enabled path in an MIT project | **Flagged** |
| `BP-001` | S3 | `mypy` had no blocking gate; 10 production errors | **Fixed** |
| `BP-005` | S3 | SAST gate scoped to 12 of ~40 packages; 24 findings in the blind spot | **Fixed** |
| `BP-006` | S3 | Three controls relied on `assert`, which `python -O` deletes | **Fixed** |
| `BP-007` | S3 | Seven GitHub Actions floated on mutable tags | **Fixed** |
| `BP-008` | S3 | 1,508 frontend tests never ran in CI; no `npm audit` | **Fixed** |
| `BP-009` | S3 | Six directly-imported packages undeclared, incl. the SSRF validator's | **Fixed** |
| `BP-010` | S3 | 593 lines of dead code bypassing four controls | **Flagged** |
| `BP-011` | S3 | No coverage tooling declared; coverage never measured | **Fixed** |
| `BP-013` | S4 | `LICENSE` still says `<Your Name>` | **Flagged** |
| `BP-014` | S4 | 11 modules > 1,500 lines; 106 functions > 100 lines | Backlog |
| `BP-015` | S4 | The inherited session's mypy fix broke `import api.main` | **Fixed** |
| `BP-016` | — | Exception handling, async correctness, assertion quality and test isolation all verified clean | Verified |

---

## 8. What a human must run

1. **`pip install -r requirements.txt` on Python 3.11** — the pin set
   cannot be resolved against 3.11 from this 3.14 venv (§4.3). CI's
   install step is the real check.
2. **`detect-secrets scan $(git ls-files) --baseline .secrets.baseline`
   on Linux**, then `detect-secrets audit .secrets.baseline`. Still red
   from Phase 2, still not refreshable from a Windows checkout
   (CLAUDE.md §13). Untouched by this phase.
3. **A licence decision on `BP-012`**, which may need legal input rather
   than engineering.
4. **The `BP-010` decision** — delete `ai_generator_client.py` or adopt
   it behind `Settings` and `outbound_url`.
5. **Verify the pinned action SHAs** against the tags they claim, if your
   threat model requires it. They were taken from the published tags on
   2026-10-29 and are recorded with the tag in a trailing comment.

---

## 9. Five-line summary

* **Open S1: 0. Open S2: 1** — `BP-012`, the AGPL dependency, which is a
  legal decision and not an engineering one.
* **Fixed this session: 8** — `BP-001`, `BP-005`, `BP-006`, `BP-007`,
  `BP-008`, `BP-009`, `BP-011`, `BP-015`.
* **Flagged for an owner: 3** — `BP-010` (dead code bypassing four
  controls), `BP-012` (AGPL/GPL), `BP-013` (`LICENSE` placeholder).
* **Blocking CI gates went from six to nine**, and the SAST gate went
  from covering 48.6% of the code to all of it.
* **Next phase: 9 — UI/UX audit**, which needs a real browser for axe,
  Lighthouse and colour contrast.
