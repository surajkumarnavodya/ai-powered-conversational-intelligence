# Phase 0 — Reconnaissance & baseline

**Date:** 2026-10-25 · **Branch:** `feat/conversation-memory` ·
**Auditor:** automated session, per `UAT_READINESS_AUDIT_PROMPT.md`

> **Scope honesty.** This file covers **Phase 0 only**. The audit prompt
> itself says a single session attempting every phase "will run out of
> context and start skimming", and that is a correct prediction. Phases
> 1–12 are **not** done and are not claimed here. §7 lists what each
> remaining phase needs, so the next session starts from these files
> rather than from memory.

---

## 1. Baseline measurements

Every row was run in this session on 2026-10-25, with the exact command
shown. Where a command could not run, the row says so — none are
inferred from documentation.

| Gate | Command | Result | Exit |
|---|---|---|---|
| Backend tests | `pytest -q` | **8,297 passed**, 11 deselected, 0 failed (521.80s) | 0 |
| Frontend tests | `npm run test -- --run` | **1,491 passed**, 103 files | 0 |
| Frontend types | `npx tsc --noEmit -p tsconfig.app.json` | clean | 0 |
| Frontend lint | `npm run lint` (oxlint) | warnings only, no errors | 0 |
| Frontend build | `npm run build` | succeeded | 0 |
| Lint (ruff) | `ruff check .` | **1 finding** — `agent/multiagent/agents.py:23:1 I001` | 1 |
| Format (black) | `black --check .` | clean, **1,665 files** | 0 |
| SAST (bandit) | `bandit -r agent api config db security media_gen moderation rag search voice media embeddings -f txt` | **0 Low / 0 Medium / 0 High**, 56,950 LOC | **0** |
| Types (mypy) | `mypy .` | 723 errors / 129 files (**7 in production code**) | 1 (report-only in CI) |
| Frontend deps | `npm audit --omit=dev` | **0 vulnerabilities** | 0 |
| Python deps | `pip_audit` | **could not run** — `ReadTimeout` to `pypi.org`, no egress here | — |
| Docker build | — | **not run** in this session | — |

### What changed against the README's stated baseline

The README's "Project status" table was last re-measured 2026-10-22.
Two of its rows no longer hold:

- **Tests:** README says *"7,673 backend passed with 5 known failures"*.
  Measured today: **8,297 passed, 0 failed**. The five failures were a
  cross-file-state interaction closed on 2026-10-24 (CLAUDE.md §13).
- **CI gates:** README says *"Two blocking CI gates (`ruff`, `bandit`)
  were re-measured red"*. `bandit` now exits **0 with zero findings**.
  Only `ruff`'s single `I001` is still red.

`pip-audit` remains genuinely unverifiable in this environment, so the
README's statement about it is still accurate and is kept.

The `# nosec` annotations in the scanned packages (14 across 10 files)
are line-scoped and each carries a written justification, which is what
Shared Rule 7 permits. They were **not** added by this session.

---

## 2. Route inventory

Taken from the running application's own OpenAPI document, not from
source grep:

```
curl -s http://127.0.0.1:8000/openapi.json
→ 212 operations across 187 paths
```

| Prefix | Ops | Prefix | Ops |
|---|---|---|---|
| `/auth` | 22 | `/semantic-catalog` | 8 |
| `/governance` | 19 | `/eval-platform` | 7 |
| `/conversations` | 14 | `/semantic-intelligence` | 6 |
| `/platform-admin` | 14 | `/bi` | 6 |
| `/workflows` | 14 | `/questions` | 5 |
| `/tenant-admin` | 12 | `/artifacts` | 5 |
| `/onboarding` | 11 | `/documents` | 4 |
| `/attachments` | 9 | `/lineage`, `/schema-drift`, `/model-routing` | 3 each |
| `/recommendations` | 9 | `/analyst` | 2 |

**Method note for the next phase.** Counting `require_permission(`
alone is misleading and nearly produced a false S1 in this session: 20
route files show zero matches, which looks like an unauthenticated
admin surface. They are not. This codebase has **two** authorization
layers (CLAUDE.md §5.2) and the admin and governed-lifecycle routers use
the second one — `Depends(require_local_user)`,
`Depends(require_platform_admin)` from `identity/rbac.py`. Any Phase 4
matrix must enumerate **both**, and should prefer live probing over
grep.

---

## 3. Feature-flag posture (as configured in this environment)

Read from the running `/health` and `.env`. This is the *local* posture,
not a recommendation for the UAT host.

| Flag | Value here |
|---|---|
| `LOCAL_AUTH_ENABLED` | true |
| `ENABLE_MULTI_SOURCE_ROUTER` | true |
| `ENABLE_WEB_SEARCH` | true (Tavily key present) |
| `ENABLE_DOCUMENT_RAG` / `ENABLE_POLICY_RAG` | true |
| `ENABLE_MEDIA_SEARCH`, voice, vision, OCR | true |
| `google_signin_enabled` | false |
| `share_public_links_enabled` | false |
| `ENABLE_GENERAL_KNOWLEDGE_ANSWERS` | false (default) |

**Not verified:** whether "off" genuinely removes a route's attack
surface or only hides UI. That is a Phase 0 item 3 question this session
did not answer, and it should be answered by probing each gated route
with its flag off — not by reading the flag's docstring.

---

## 4. Unauthenticated exposure probe (evidence for Phases 2–4)

Every non-parameterised `GET` in the OpenAPI document was requested with
**no credentials** against the running server:

| Result | Count |
|---|---|
| `401 Unauthorized` | **52** |
| `200 OK` | 1 — `/health` |
| `404 Not Found` | 1 — `/auth/google/nonce` (Google sign-in disabled) |
| skipped (path parameter) | 29 |

So **deny-by-default holds across the whole static GET surface** in this
configuration. That is a genuine positive result and is recorded as
`AUZ-001`.

It is also *not* a substitute for Phase 4. This probe says nothing
about: the 29 parameterised routes, any `POST`/`PUT`/`PATCH`/`DELETE`,
IDOR between two real users, tenant isolation, or whether a route that
correctly returns 401 then applies the *right* role check once a token
is present. Those are the Phase 4 matrix.

The one `200` is finding `SEC-001`: `/health` names the configured
databases, their table counts, the Ollama host and the vision model to
anyone who asks. It is **flagged, not fixed**, because the login page
reads it before authentication — see the register for the proposed
split.

---

## 5. Highest-risk areas to audit first

Ranked by blast radius × how little this session verified:

1. **Authorization on parameterised and mutating routes** (Phase 4) —
   the 212-route surface is almost entirely unexercised by this probe.
   IDOR across users and tenants is the single most likely S1.
2. **SQL-safety attack suite on both dialects** (Phase 2) — the
   validator is well covered by unit tests, but this session ran no
   adversarial SQL of its own.
3. **Masking bypass via aliases/expressions/aggregates** (Phase 2).
4. **Indirect prompt injection** through DB values, documents, web
   results and attachments (Phase 2).
5. **Loop bounds** (Phase 6) — unbounded retry or cost is a UAT-stopper
   and is cheap to test with a stub LLM.
6. **Accuracy on the current default model** (Phase 5) — `DOC-003`:
   the only full-corpus benchmark predates the model change.

---

## 6. Environment-blocked checks (a human must run these)

Not oversights. Nothing in this list could be executed here, and none of
it is recorded as passing.

| Check | Why blocked | Command for a human |
|---|---|---|
| `pip-audit` | no network egress to pypi.org | `pip-audit` on a networked host |
| Container CVE scan | no Docker daemon / scanner exercised here | `trivy image <tag>` |
| Docker build parity | not run this session | `docker build .` on Python 3.11 |
| Load / capacity test | no load tool run; local Ollama is the bottleneck anyway | `k6`/`locust` at UAT concurrency |
| Backup/restore drill | no scratch environment | restore identity DB + Chroma to a scratch host |
| Live DAST | never run against a deployment | ZAP/Burp against the UAT host |
| OIDC end-to-end | no live IdP | against the real IdP |
| Browser UI / axe / Lighthouse | **no browser tooling in this environment** | Phase 9 in a real browser |

---

## 7. What each remaining phase still needs

Phases 1–12 are **not started**. The prompt's own recommended order
stands, and Phases 2–4 are the ones most likely to block UAT.

The one piece of Phase 0 left undone is item 4 — reconciling
`docs/architecture/feature-inventory.md` against reality to find claimed
-but-missing and present-but-undocumented code. It needs a package-by-
package read and did not fit this session.
