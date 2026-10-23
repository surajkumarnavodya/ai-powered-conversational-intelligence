# Remediation Summary — 2026-10-23

Companion to [`PENETRATION_TEST_REPORT.md`](PENETRATION_TEST_REPORT.md),
which carries the evidence, reproduction steps and root-cause analysis.
Branch `feat/source-connectivity-audit`, base commit `e970404`.

**4 findings confirmed · 4 fixed and verified · 0 partially fixed · 0 open
findings · 5 open observations (not findings) · 1 residual edge on F-01.**

---

## Per-finding

### F-01 · High · Fixed, verified

| | |
|---|---|
| **Title** | Result-level governance bypassed by LLM-derived narrative text |
| **Category** | Broken access control / sensitive data exposure (OWASP A01, A02; API3). CWE-200, CWE-213 |
| **Affected** | `api/shares.py::_projected_turn_out`, `api/artifacts.py::_detail_out`; root cause between `agent/nodes.py::execute_sql_node` and `::generate_insight_node` |
| **Exposure** | An **unauthenticated** visitor to an "anyone with the link" share could read a restricted value (salary, name, PHI, banking) in the LLM's prose while the table beside it read `[restricted]` |
| **Root cause** | The result gate governs rows for the *runner*. `agent.insight.summarize_result` then feeds real cell values (`top_label`, `maximum`, `total`) to the LLM, and that sentence is persisted verbatim. Both re-governance surfaces re-governed only the tabular snapshot, passing `insight`, `synthesized_answer` and `content` through untouched |
| **Fix applied** | New `governance.result_policy.narrative_withhold_reason()`, deriving its answer from the **same** `policy_for(domain)` → `_classification_masks` chain the row gate uses, so the two cannot drift. Wired into both surfaces: narrative fields become `None`, an assistant turn's `content` becomes a fixed module constant, and a new optional `narrative_withheld_reason` reports the blocking **column names** (never a value). Withheld, never rewritten. Frontend renders a localized notice; `sharedView.narrativeWithheld` added to all five locales |
| **Tests** | 5 added to `tests/security/test_shared_results_are_regoverned.py`, 2 to `tests/security/test_artifacts_cannot_bypass_authorization.py`, 7 to `tests/test_governance_result_policy.py`, 1 to `frontend/src/pages/SharedConversation.test.tsx` |
| **Result** | PoC failed before the fix (anonymous viewer read `187431.55` on an HTTP 200), passes after. **Mutation-verified**: stubbing the control fails 4 guards across both surfaces |
| **Outstanding** | A turn with an `insight` but **no** persisted `result_snapshot` (an `/ask` never confirmed with "Confirm and Run") has no column evidence, so its prose is served as before. Closing it means persisting the column set or the governance decision at capture time — a schema-touching change, not taken unprompted. **This is the sharpest remaining edge** |

### F-02 · Medium · Fixed, verified

| | |
|---|---|
| **Title** | `GET /conversations/{id}/messages` served a stored result with no re-governance |
| **Category** | Broken access control — stale authorization (OWASP A01). CWE-613, CWE-200 |
| **Affected** | `api/chat_history.py::list_messages_route` |
| **Exposure** | A demoted owner kept a durable, unprivileged window onto restricted values in their own stored conversation. Also triggered with **no role change at all** by classifying a column `restricted` in `config/sensitive_columns.yaml` |
| **Root cause** | `CLAUDE.md` §5.4 names "both read-back surfaces"; there are **four**. `api/jobs.py::_regovern_job_result` is a third and does re-govern. This was the fourth and returned `metadata=row.metadata` raw. Ownership *is* correctly enforced (`user_id` inside the `WHERE`), which made it look sufficient — but ownership answers "whose conversation", not "may this caller see these values now" |
| **Fix applied** | `_governed_metadata()` runs the full `govern_result_for_domain` against `get_user_roles(session, user.id)` — the roles held *now* — and applies `narrative_withhold_reason` on the same decision. `_message_content()` covers `ai_outputs.content`, which carries the same sentence. Row bookkeeping follows the export ceiling, matching `api/shares.py` |
| **Tests** | `tests/security/test_chat_history_reload_is_regoverned.py` — new file, 4 tests |
| **Result** | 2 adversarial tests failed before the fix while the 2 control tests passed (proving the harness, not the fixture, was sound); all 4 pass after. **Mutation-verified** |
| **Outstanding** | `GET /chat/search` snippets derive from the same `content` field and are **not** governed — see "Open observations" below |

### F-03 · Medium · Fixed, verified

| | |
|---|---|
| **Title** | The blocking `bandit` CI gate exits non-zero |
| **Category** | CI/CD security misconfiguration (OWASP A05) |
| **Affected** | `.github/workflows/ci.yml` SAST step; `media_gen/svg_sanitize.py`, `media_gen/providers/huggingface.py`, `agent/harness/retry.py`, `agent/sql_validator.py`, `api/streaming.py`, `security/audit_log.py` |
| **Exposure** | Loss of a control, not a directly exploitable defect. Because the `test` job's steps run sequentially, a red `bandit` also prevented `pytest` and `pip-audit` from running in CI at all |
| **Root cause** | Two parts. (1) `bandit` exits 1 on any finding at or above its default `low` severity level, so the gate was **already red** while `CLAUDE.md` §5.7 recorded it as passing (§13 of the same file states the opposite and is correct). (2) This branch's SVG/Hugging Face commits added a new **Medium** (B314, stdlib XML parse on untrusted input) plus two Lows |
| **Assessment of the new Medium** | **Not exploitable.** `sanitize_svg` refuses `<!DOCTYPE`/`<!ENTITY` as a construct *before* parsing, and bounds size and depth. Verified empirically: billion-laughs rejected in 0.000s, XXE rejected, a 140 KB / 20,000-level payload rejected at depth 32 in 0.012s, and `script`/`onload`/`foreignObject`/`javascript:`/`<image href>` all stripped. The sanitiser is well built |
| **Fix applied** | 7 per-line `# nosec` annotations, each with a written justification (incl. one genuine false positive, B105 on an error *message* constant). **No threshold changed, `--severity-level` untouched** — a new unsafe pattern anywhere still fails. `defusedxml` considered and declined, with the trade-off recorded in-code |
| **Tests** | None added; verification is the gate itself |
| **Result** | CI-scope `bandit` went from **exit 1** (6 Low, 1 Medium) to **exit 0** (0 findings, 16 justified `#nosec` total) |
| **Outstanding** | The gate's *scope* — see "Open observations" |

### F-04 · Low · Fixed, verified

| | |
|---|---|
| **Title** | A `# nosec B608` annotated the wrong line |
| **Affected** | `onboarding/profiling.py:256` (`_sample_freshness_days`) |
| **Exposure** | None directly — a suppression that silently did not suppress |
| **Root cause** | The comment sat on the closing parenthesis, one line below the f-string `bandit` reports. Its three siblings in the same file are placed correctly |
| **Underlying code** | **Safe.** Identifiers reach these f-strings through SQLAlchemy's dialect-aware `identifier_preparer.quote`. Verified against hostile identifiers in all four dialects — every closing delimiter correctly doubled (`a]; DROP TABLE y--` → `[a]]; DROP TABLE y--]`). No SQL injection |
| **Fix applied** | Suppression moved onto the statement, with the quoting rationale written out. The four `candidate_sql` builders in `onboarding/golden_questions.py` annotated too (Medium, and entirely unscanned), noting that nothing there executes — every candidate is re-parsed by `validate_sql` and row-capped before `evaluate_candidates` runs it |
| **Result** | `bandit -r onboarding` → 0 Medium (was 5) |

---

## Open observations (not findings — no fix applied)

| # | Observation | Why not fixed |
|---|---|---|
| O-1 | **`bandit`'s CI scope is 12 of 41 first-party packages.** Unscanned: `attachments/`, `identity/`, `onboarding/`, `governance/`, `guardrails/`, `jobs/`, `rest/`, `mcp_client/`, `bi_integration/`, `analytics/` and 19 more. Measured: **5 Medium + 17 Low** outside its reach. All 5 Mediums were triaged benign this session and are now annotated | Widening changes what fails the build for every contributor, and needs 17 further findings triaged. Writing justifications I had not verified would be worse than leaving the gap visible. Recommendation in the report §12.1 |
| O-2 | **`GET /chat/search` snippets are ungoverned.** `SearchHitOut.snippet` derives from `ai_outputs.content` — the field that carries insight prose — so a snippet could surface a fragment that F-02's fix now withholds | Narrower than F-02 (a snippet, matched by the caller's own terms, in their own conversation). Governing it needs per-hit metadata that `search_history` does not return, i.e. a repository-layer change. Named rather than half-fixed |
| O-3 | **`.secrets.baseline` appears stale** — 44 tracked files reported findings it does not record. **Unverified**: the Windows reproduction of that gate proved unreliable (same files gave 7 findings in-tree, 0 scanned standalone), so no claim is made about CI's actual state. Every finding inspected was a test fixture, UI label, Alembic revision id or redaction docstring — no real credential | Needs one authoritative run on a Linux runner. Report §12.2 |
| O-4 | **Two surfaces disagree on showing SQL that touches a restricted column.** `api/artifacts.py::_sql_for_caller` withholds it; `api/shares.py` serves it to an anonymous link visitor (no `find_restricted_column_references` call anywhere in that file) | Exposure is schema identifiers, not values, and showing the query is arguably the point of sharing -- a product call, not mine to make unilaterally. But only one of the two surfaces can be right. Report §11.4 |
| O-5 | **Documentation drift in `CLAUDE.md`**: §5.7 states `bandit` passes (it does not — F-03); §13 records 6 baseline test failures where this checkout has 5 | That file is the cross-cutting rules document and its own §11 says to change it only when a rule changed. Corrections recorded here and in the report |

---

## Verification evidence

| Check | Before | After |
|---|---|---|
| `pytest` (full) | `5 failed, 7958 passed, 11 deselected` (485s) | `5 failed, 7976 passed, 11 deselected` (726s) — **+18 tests, no regressions** |
| Pre-existing failures attributed? | Yes — all 11 pass in isolation; `CLAUDE.md` §13's documented cross-file-state issue | unchanged |
| `bandit` (CI scope) | **exit 1** | **exit 0** |
| `ruff check .` | 1 pre-existing `I001` | 1 pre-existing `I001` |
| `black --check .` | clean on tracked source | clean (1,643 files) |
| `pip-audit` | `No known vulnerabilities found, 7 ignored` | unchanged |
| `npm audit --omit=dev` | 0 vulnerabilities | 0 |
| `vitest` | 1,372 passed (98 files) | 1,373 passed |
| `tsc --noEmit` | clean | clean |
| `oxlint` | exit 0 | exit 0, no warnings in changed files |
| Mutation-verified guards | — | **6 tests fail when the two new controls are stubbed** |

**Regressions: none.** Same five pre-existing failures before and after;
all new tests assert the legitimate path still works as well as the attack
path failing.

---

## Files changed

26 code files, 1,074 insertions, 25 deletions (plus these two reports). No
migration, no dependency added, no credential rotation required.

**Controls**
- `governance/result_policy.py` — `narrative_withhold_reason()` (+80)
- `api/shares.py` — `_narrative_withheld_reason()`, wiring, fixed refusal constant
- `api/artifacts.py` — `_narrative_for_caller()`, wiring
- `api/chat_history.py` — `_governed_metadata()`, `_message_content()`, wiring (+123)

**Schemas** (all additive, optional, defaulted)
- `identity/share_schemas.py`, `api/artifact_schemas.py`

**Frontend**
- `frontend/src/lib/types.ts`, `frontend/src/pages/SharedConversation.tsx`
- `frontend/src/i18n/locales/{en,es,fr,hi,mr}/translation.json` — one line each

**Gate annotations**
- `media_gen/svg_sanitize.py`, `media_gen/providers/huggingface.py`,
  `agent/harness/retry.py`, `agent/sql_validator.py`, `api/streaming.py`,
  `security/audit_log.py`, `onboarding/profiling.py`,
  `onboarding/golden_questions.py`

**Tests**
- `tests/security/test_chat_history_reload_is_regoverned.py` (new)
- `tests/security/test_shared_results_are_regoverned.py`,
  `tests/security/test_artifacts_cannot_bypass_authorization.py`,
  `tests/test_governance_result_policy.py`,
  `frontend/src/pages/SharedConversation.test.tsx`

**Not changed:** `.secrets.baseline` (byte-identical to `HEAD` — see report
§9.4 for a process error here that was caught and reverted), `CLAUDE.md`,
`identity/migrations/`, `.env*`, any CI workflow file.
