# UAT Readiness — Findings Register

Opened 2026-10-25. One row per finding, per `UAT_READINESS_AUDIT_PROMPT.md`.

**Status values:** Open / Fixed / Flagged (needs an owner decision) /
Waiver-needed / Unverified.

**A note on what this register is not.** It records only what was
*measured* in this environment on the stated date. Where a check could
not run here, the row says **Unverified** and names what a human must
run — never "passed". See `phase-0-recon.md` §6 for the full list of
environment-blocked checks.

| ID | Phase | Sev | Title | Evidence | Status | Fix commit | Test |
|---|---|---|---|---|---|---|---|
| DOC-001 | 0 | S3 | README states `bandit` is a red blocking gate; it passes | `bandit -r agent api config db security media_gen moderation rag search voice media embeddings -f txt` → **exit 0, 0 Low/0 Med/0 High, 56,950 LOC** (2026-10-25). README:27-29 says "Two blocking CI gates (`ruff`, `bandit`) were re-measured red today" | Fixed | this change | n/a (doc) |
| DOC-002 | 0 | S3 | README states 7,673 backend tests with 5 known failures; both numbers are stale | `pytest -q` → **8,297 passed, 11 deselected, 0 failed** (2026-10-25, 521s). The 5 cross-file-state failures were closed 2026-10-24 (CLAUDE.md §13) | Fixed | this change | n/a (doc) |
| DOC-003 | 0 | S4 | `docs/EVALUATION_CURRENT.md` accuracy figures predate the current default model | Self-disclaimed in the doc; `OLLAMA_MODEL` default is `qwen2.5-coder:7b`, figures were measured against `llama3.1:8b` | Open | — | Phase 5 re-run required |
| SEC-001 | 0/2 | S3 | `GET /health` discloses internal topology to an unauthenticated caller | `curl -s http://127.0.0.1:8000/health` with no credentials returns configured **database names** (`hr`, `adventureworks`, `employee_management_database`, `test_db`), **per-database table counts**, the **Ollama host URL**, the **vision model name** (`qwen3.8:27b`) and the full feature-flag posture | Flagged | — | proposed below |
| AUZ-001 | 0/4 | — | Deny-by-default holds on the static GET surface (positive finding, recorded as evidence not a defect) | Unauthenticated probe of all 54 non-parameterised GET routes: **52 → 401**, 1 → 200 (`/health`, intentionally public), 1 → 404 (`/auth/google/nonce`, Google sign-in disabled). Script and raw results in `phase-0-recon.md` §4 | Verified | n/a | Phase 4 will extend to parameterised routes + IDOR |
| OPS-001 | 0 | S3 | `pip-audit` cannot be run in this environment | `python -m pip_audit` → `requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='pypi.org', port=443)` (2026-10-25). No network egress to PyPI here | Unverified | — | a human must run it with network |
| OPS-002 | 0 | S3 | Container CVE scan, load test, and restore drill remain unrun | No Docker daemon / scanner / load tool exercised in this session; no scratch environment for a restore drill | Unverified | — | human-run; see §6 |
| BP-001 | 0/8 | S4 | `mypy` reports 723 errors in 129 files; 7 are production code | `mypy .` → `Found 723 errors in 129 files (checked 885 source files)` (2026-10-25). Non-test, non-eval errors: **7**, matching the set CLAUDE.md §13 documents (`agent/sql_validator.py:839`×2, `db/schema_introspection.py:376`, `identity/repositories/semantic_intelligence.py:199,200,242`, `identity/repositories/recommendation_governance.py:402`). `mypy` is report-only in CI by dated decision | Open | — | Phase 8 |
| BP-002 | 0/8 | S4 | `ruff` has one blocking finding | `ruff check .` → `agent/multiagent/agents.py:23:1: I001 Import block is un-sorted`. Unchanged since 2026-10-17; auto-fixable (`--fix`) but touching an unrelated module's imports is out of this phase's scope | Open | — | Phase 8 |

---

## SEC-001 — proposed fix (flagged, not applied)

**Why flagged rather than fixed.** Rule 8: changing `/health`'s response
shape is an API-contract and user-visible change. It cannot simply be
put behind auth, because the React app reads it *before* anyone is
logged in — `components/auth/AuthGate.tsx` and `pages/auth/Register.tsx`
read `local_auth_enabled`, `google_signin_enabled` and
`google_client_id` to decide what the login page renders. Gating the
whole endpoint would break sign-in.

**Proposed split:**

- `GET /health` stays public and returns only what an unauthenticated
  client legitimately needs: `status`, and the auth-mode flags the login
  page renders from.
- The operational detail — database names, table counts, Ollama host,
  model names, the remaining feature flags — moves behind the existing
  auth dependency, e.g. `GET /health/detail` requiring a local user.

**Risk if left as-is for UAT:** low in a closed UAT environment on
localhost; meaningful if the UAT host is reachable beyond the test
group, because the endpoint names the real databases and confirms which
optional subsystems are on — useful reconnaissance for a later attempt.

**Decision needed from the product owner:** accept for UAT with a
documented waiver, or take the split before UAT opens.
