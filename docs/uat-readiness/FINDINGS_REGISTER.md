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
| AUZ-002 | 1 | **S1** (latent) | Document RAG, policy RAG and the media library are not tenant-partitioned at the store or the route layer | `rag/store.py:84-120` — `DocumentRecord`/`ChunkMatch` have no `tenant_id`; no store function takes one (`list_documents` `:390`, `similarity_search` `:502`, `get_document_bytes` `:476`, `delete_document` `:494`). `api/documents.py` contains "tenant" **0** times; all four routes guard on base role only (`:55`, `:104`, `:172`, `:194`). `media/store.py`, `api/media.py`, `api/media_search.py`, `api/media_library.py` → **0** each. Absent from `docs/MULTI_TENANCY.md`'s 17-row partition table **and** its 7 known limitations. `enable_media_search` defaults **True** | Flagged | — | Phase 4 runtime proof: two hand-assigned tenants, then cross-tenant `GET /documents`, `/download`, `DELETE` |
| OPS-003 | 1 | **S2** | Three fail-closed startup controls are inert outside `ENVIRONMENT=production`, which defaults to `development` | `config/settings.py:3213` (`environment` default) and `.env.example:619` ship `development`. Gated controls: writable-DB-role refusal (`api/main.py:269`), no-authentication refusal (`config/settings.py:4212`), malware-scanning refusal (`config/settings.py:4247`). Outside production the first warns and the other two do not fire, so a UAT host built from `.env.example` can start with a writable DB role, no authentication at all, and no malware scanning | Flagged | — | `phase-1-architecture.md` §3.4, §5 |
| ARC-002 | 1 | **S2** | Multi-database auto-routing has no data-presence signal and no user override | `embeddings/retriever.py:599-694` scores only `1.0 - <Chroma DDL distance>`; the sole emptiness check is `collection.count() == 0` (`:672`), i.e. an unbuilt **index**, not empty **tables**. `TableSchemaInfo.row_count_estimate` exists (`db/schema_introspection.py:125`) but is opt-in and consumed only by `onboarding/jobs.py:107` and `schema_drift/snapshot_store.py:88`. `AskRequest` has no `database` field (AST-extracted field list in §3.5) | Flagged | — | `phase-1-architecture.md` §3.5, §5 |
| ARC-003 | 1 | S2 | `functools.cache` did not serialise Chroma client construction, so concurrent cold-cache callers each built a `PersistentClient` on one directory — the documented HNSW-corruption condition whose symptom is silent misrouting to the wrong database | Reproduced with real threads before the fix: **8 threads → 8 clients constructed, 8 distinct instances returned**. Reachable from `embeddings/retriever.py:665,735` on `api.main._get_ask_executor`'s pool; `api.main.lifespan` pre-warms the engines, Ollama and both graphs but not this client | **Fixed** | `defb07a` | `tests/test_chroma_client_singleton_concurrency.py` (5, incl. non-vacuity) |
| ARC-004 | 1 | S3 | Client-supplied `X-Correlation-ID` was bound, stamped onto every log record and echoed back unvalidated — log-field forgery, ANSI escape injection, unbounded log bloat | Live `TestClient` probe before the fix, all three accepted and echoed verbatim: `x<TAB>level=CRITICAL<TAB>event=admin_login_success` produced `correlation_id=x<TAB>level=CRITICAL<TAB>event=admin_login_success \| tenant_id=- \| …`; an ANSI escape written verbatim; a 4 KiB id stamped on every record. Routed through neither `normalize_text` nor `truncate_for_log`, against the latter's own docstring | **Fixed** | `54e434a` | `tests/test_correlation_id_sanitization.py` (23) |
| ARC-001 | 1 | S3 | Three `async def` upload handlers run heavy synchronous work on the event loop; `run_in_threadpool` is used nowhere in `api/` | `api/attachments.py:70`→`:121` `validate_and_store_upload` (scan, PDF parse, OCR, resize); `api/documents.py:91`→`:142` `ingest_pdf` (extract→chunk→moderate→embed→store); `api/voice.py:50`→`:77,:88` `transcribe` + `correct_transcript` (an LLM call). All callees are plain `def`. `Dockerfile:106` runs uvicorn with no `--workers`, so one ingest stalls the whole deployment, including `/ask/stream` SSE and `/questions/{id}` polling | Flagged | — | `phase-1-architecture.md` §3.7, §5 |
| OPS-004 | 1 | S3 | Dependency pins are unverified against the Python 3.11 deploy target | The Dockerfile's own comment states the pins "were chosen for 3.14 wheel availability on that dev machine and have not been independently verified against 3.11 here." No Docker daemon in this environment | Unverified | — | `docker build .` then an import smoke test — `phase-1-architecture.md` §3.10 |
| ARC-005 | 1 | S4 | 56 package-level import cycles, all via shared leaf types living inside higher packages | AST import graph over 884 tracked files. Runtime module cycles: **1** (`mcp_client/__init__.py` ↔ `mcp_client/client.py`, the ordinary package-init pattern). Representative edges: `sources`→`agent.authz.Permission`, `observability`→`agent.state.StageTiming`, `config`→`moderation.taxonomy.Category`. No inversion of control, no runtime cycle | Open | — | `phase-1-architecture.md` §3.1 |
| DOC-004 | 1 | S4 | CLAUDE.md §5.4 says "**Both** read-back surfaces" re-govern stored results and names two; there are three | `api.jobs._regovern_job_result` is a third, alongside `api.artifacts._result_out` and `api.shares._governed_result_snapshot` | Fixed | this change | n/a (doc) |
| DOC-005 | 1 | S3 | The numbered contracts were declared authoritative with no caveat that their cross-cutting counts are a dated snapshot — and the stale figures have already propagated | `02_TARGET_ARCHITECTURE.md` §1 says "12 nodes" (measured: **19**) and "67 real routes" (measured: **212**); §4.5 says tenancy is "plumbing only, no enforcement" when it **is** enforced. This audit's own prompt repeats "12-node" twice. `docs/architecture/context-map.md` §3 had no dated-record note | Fixed | this change | n/a (doc) |

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
