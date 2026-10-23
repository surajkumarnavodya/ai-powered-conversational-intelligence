# Verification Re-Review — Current Security Status

**Date:** 2026-10-23 · **Branch:** `sec/verification-re-review` (from
`sec/governance-open-items` @ `bdfa3ba`) · **Scope:** this repository,
staging/offline only.

Every status below was established by **running** something against the
current source. No row is marked resolved on the strength of a prior
report, a summary, or a test I did not also mutation-check. Where a check
could not be run here, it says so rather than inferring.

Supersedes the status columns of
[`PENETRATION_TEST_REPORT.md`](PENETRATION_TEST_REPORT.md) and
[`REMEDIATION_SUMMARY.md`](REMEDIATION_SUMMARY.md); their narrative and
evidence remain valid.

---

## Headline

**One previously "fixed, verified" finding was not fully fixed.** F-01 and
F-02 were re-verified as closed on the shares and chat-history surfaces,
but the **artifacts** surface was still re-governing with
`govern_rows_for_caller` — the configured-column layer only — at
`api/artifacts.py:238` and `:815`. That is the same weaker-gate root cause
F-05 found on the BI export.

Proved on the real HTTP surface, not inferred:

```
$ pytest tests/security/test_artifacts_cannot_bypass_authorization.py::\
TestArtifactsApplyTheProtectionsNoRoleWaives
E  AssertionError: a never-disclosed credential value was served from a stored artifact
E  assert 'ZZTOPSECRETHASHZZ' not in ['ada', 'ZZTOPSECRETHASHZZ']
1 failed
```

The existing artifacts tests passed throughout, because they hand-classify
`employees.salary` — which is exactly the one layer the weaker gate *does*
honour. Tracked below as **F-07**, fixed in this pass.

---

## (a) Status table

| ID | Title | Prior status | Verified status now | Evidence | Still-required work |
|---|---|---|---|---|---|
| **F-01** | Governance bypassed by LLM-derived narrative | Fixed, verified | **Fixed-and-verified** | Control at `governance/result_policy.py:339`; callers `api/shares.py:370`, `api/artifacts.py:366`, `api/chat_history.py:259`. Mutation (`return None`) → `5 failed, 21 passed` | None |
| **F-02** | Stored result replayed with no re-governance (chat history) | Fixed, verified | **Fixed-and-verified** | `api/chat_history.py:237` `_governed_metadata`. Mutation (return raw) → `6 failed, 8 passed` | None |
| **F-03** | Blocking `bandit` gate red while believed green | Fixed, verified | **Fixed-and-verified** | `bandit -r <CI scope>` → **exit 0**, 0 findings, 16 suppressed. Gate self-test: suppressions stripped → **exit 1, 16 findings** — the gate genuinely blocks | None |
| **F-04** | `# nosec` on the wrong line | Fixed, verified | **Fixed-and-verified** | Empirical placement audit: 16 findings, **16 suppressed within the statement's `line_range`, 0 unsuppressed** | None |
| **F-05** | BI export: no re-governance; weaker gate | Fixed (prior pass) | **Fixed-and-verified** | `api/bi_integration.py` `_govern_for_caller` at capture + export. Mutation → `3 failed, 3 passed` | None |
| **F-06** | Derived prose written to application logs | Fixed (prior pass) | **Fixed-and-verified** | `agent/nodes.py:2921,2934` now log `len(insight_text)` and the verified-number count; `grep insight_text agent/nodes.py` shows no logger call carrying the text | None |
| **F-07** | **Artifacts re-governed with the configured-column layer only** | *not previously identified* | **NEW → Fixed-and-verified** | Was `govern_rows_for_caller` at `api/artifacts.py:238,815`; credential served in the clear (above). Now `govern_result_for_domain`. Mutation (both sites) → `4 failed, 10 passed` | None |
| **F-01-res** | Unconfirmed `/ask` turn: prose ungoverned | Open (prior report) | **Fixed-and-verified, with a documented default** | `insight_source_columns` at `api/chat_persistence.py:226`; allowlisted at `identity/repositories/shares.py`. Mutations → capture `3 failed`, allowlist `1 failed` | `GOVERN_NARRATIVE_WITHOUT_EVIDENCE` defaults **false**, so the legacy backlog is served. Decide whether to flip it |
| **O-2** | `/chat/search` snippets ungoverned | Open | **Fixed-and-verified** | `api/chat_history.py` `_governed_snippet`; `identity/repositories/history.py:628` carries `output_metadata`. Mutation → `2 failed, 12 passed` | None |
| **O-4** | Shares vs artifacts disagree on echoing SQL | Open | **Still open** | `grep -c "find_restricted_column_references\|_sql_for_caller" api/shares.py` → **0**. Artifacts withholds; shares does not | Product decision — options in [`OPEN_DECISIONS.md`](OPEN_DECISIONS.md) |
| **O-1** | `bandit` scope = 12 of 41 packages | Open | **Still open** (now 12 of **42**) | CI list is 12 packages; `ls -d */` minus non-source → **42** first-party packages | Triage the 17 unscanned Lows, then widen. Never via `--severity-level` |
| **O-3** | `.secrets.baseline` stale | Open, unverified | **Still open, still unverified here** | Not re-attempted: the Windows reproduction is known-unreliable (report §11.3) | One authoritative Linux run — runbook in `OPEN_DECISIONS.md` |
| **O-5** | `CLAUDE.md` doc drift | Open | **Still open, and worse** | §5.7 still claims bandit passes on a false premise; §13 says 631 mypy errors / 115 files — actual is **700 / 124**; §13 says 6 baseline test failures — actual is **5** | Three one-line doc corrections |
| **—** | Empty `config/sensitive_columns.yaml` | Open | **Still open** | File contains `tables: []` | Classification rests on name-pattern inference; prompt 13B's tag-based + strict-flag design is unimplemented |

---

## (b) What to fix next — ranked by exposure × effort

1. **Decide `GOVERN_NARRATIVE_WITHOUT_EVIDENCE`** *(high exposure, ~0
   effort)*. Every pre-existing turn with narrative and no stored snapshot
   currently serves its prose to anonymous share viewers. One setting.
   The reason it is not already `true` is that flipping it changes what
   existing shares show — your call, not mine.
2. **O-4, the SQL echo** *(medium exposure, low effort)*. Two surfaces
   give opposite answers to the same question; that drift is the defect.
   A `WHERE salary > 180000` literal turns the echo into a value channel.
3. **The empty `sensitive_columns.yaml`** *(high exposure, medium
   effort)*. Everything above rests on classification. Today that is
   name-pattern inference, so a restricted column named `comp_amt_v2` is
   protected by nothing. The strongest remaining systemic gap.
4. **O-1, widen the bandit scope** *(medium exposure, medium effort)*.
   `identity/`, `attachments/`, `jobs/`, `rest/`, `mcp_client/` are
   unscanned. 17 Lows to triage first.
5. **O-3, settle the secrets baseline** *(unknown exposure, low effort —
   needs a Linux runner)*.
6. **O-5, the doc corrections** *(no exposure, trivial)*. Worth doing
   because §5.7's claim would tell a reader the SAST gate protects them.
7. **CI step ordering** *(latent)*. ruff → black → bandit → **tests** →
   pip-audit run sequentially in one job, so a red lint gate still skips
   tests and the dependency scan. All three pass today, so this is latent,
   not active. Splitting into independent jobs removes the class.

---

## (c) Not verified in this environment, and why

- **DAST / live HTTP attack surface.** No staging deployment. Unchanged
  from `DAST_REPORT.md`; its reasoning still holds.
- **OIDC against a real IdP.** No IdP configured.
- **`detect-secrets` / O-3.** Deliberately *not* re-run. The committed
  baseline uses forward-slash paths and a Windows scan emits backslashes;
  the same `HEAD` files produced 7 findings in-tree and 0 scanned
  standalone. A result I know to be unreliable is worse than none.
- **Taint-tracking SAST (Semgrep / CodeQL).** Not installed, no network.
  Neither has ever run against this code — a real coverage gap, not a
  passed check.
- **Trivy container scan.** No image built here.
- **`gitleaks`.** Go binary, unavailable.
- **Live DB / Ollama behaviour.** Every backend test is mocked. The
  governance fixes are verified against the real HTTP surface with a real
  in-memory identity database, but not against a live warehouse.
- **Multi-worker deployment.** Rate limiters, caches and the job queue are
  per-process; a multi-worker deployment is untested here.

### A note on my own tooling

Two verification scripts in this pass produced **wrong results before they
produced right ones**, which is worth recording because it is the same
failure mode the audit is about:

1. A text-mode strip/restore silently rewrote CRLF line endings in five
   files. Caught by `git status`, restored with `git checkout`.
2. The nosec-strip regex consumed the leading `#` of standalone comment
   lines, leaving bare prose. The files stopped parsing, **bandit silently
   reported nothing**, and the run "proved" seven suppressions were dead.
   Fixed by blanking whole comment lines and asserting every stripped file
   still parses before any result is believed.

Both wrong results were self-consistent and plausible. Only re-running
with a different method exposed them.

---

## Commands run

```
pytest -q                                     5 failed, 8000 passed, 11 deselected
  (baseline on this branch before F-07:       5 failed, 7999 passed)
  the 5 are the known cross-file-state failures in tests/test_agent_nodes.py;
  they pass in isolation (CLAUDE.md §13)

bandit -r <CI scope>                          exit 0 | 0 findings, 16 suppressed
bandit, suppressions stripped                 exit 1 | 16 findings  (gate is LIVE)
nosec placement audit                         16/16 suppressed in-range, 0 missed
ruff check .                                  1 error (pre-existing I001, agent/multiagent/agents.py)
black --check .                               clean on tracked source (2 hits, both in untracked .claude/worktrees)
pip-audit -r requirements-dev.txt <CI ignores> No known vulnerabilities found, 7 ignored
mypy .                                        700 errors / 124 files (7 in production code, unchanged)
npm audit --omit=dev                          found 0 vulnerabilities
vitest run                                    98 files, 1392 passed
route authorization audit                     214 routes, 4 unguarded (all intentionally public)

mutation checks (control stubbed → guard tests must fail):
  F-01 narrative gate            5 failed, 21 passed    GUARDED
  F-02 chat-history gate         6 failed,  8 passed    GUARDED
  F-05 BI export gate            3 failed,  3 passed    GUARDED
  F-07 artifacts full gate       4 failed, 10 passed    GUARDED
  O-2  search-snippet gate       2 failed, 12 passed    GUARDED
  F-01-res capture evidence      3 failed, 31 passed    GUARDED
  F-01-res share allowlist       1 failed, 12 passed    GUARDED
  no-evidence strict gate        1 failed, 13 passed    GUARDED
```

---

## (3) Channel and surface enumeration — current state

**Stored-result read-back surfaces (6).** Every one re-governs against the
caller's roles fetched now:

| Surface | Gate | Evidence |
|---|---|---|
| Shares | `govern_result_for_domain` + narrative | `api/shares.py:260,370` |
| Artifacts | `govern_result_for_domain` + narrative | `api/artifacts.py:238,366` *(fixed this pass)* |
| Chat history reload | `govern_result_for_domain` + narrative | `api/chat_history.py:259,263` |
| Jobs | `govern_result_for_domain` | `api/jobs.py:420` |
| BI export | `govern_result_for_domain` | `api/bi_integration.py:179` |
| `/chat/search` snippets | narrative gate via `_governed_snippet` | `api/chat_history.py` |

**Derived-text channels.** Insight, synthesized answer, persisted
assistant `content`, and search snippets are all gated. Exports (CSV /
XLSX / JSON) are gated at the BI surface. **Charts carry no values** —
`ChartRecommendation` is `chart_type`/`reason`/`x_column`/`y_column`
(`agent/result_charting.py:120`), column names only. **Echoed SQL remains
ungoverned on shares** — that is O-4.

**Nothing new bypasses the gate.** The route audit is unchanged at 214
routes / 4 unguarded, and `api/platform_admin.py:465` (audit-event
metadata) and `api/artifacts.py:575` (lineage identifiers) were checked
and carry no result values.
