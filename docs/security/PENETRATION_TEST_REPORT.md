# Penetration Test & Remediation Report

**Date:** 2026-10-23
**Branch / base commit:** `feat/source-connectivity-audit` @ `e970404`
**Scope:** this repository only — the FastAPI backend, the React frontend,
their dependencies and the CI security gates. Local, fully-mocked
environment; no live database, no Ollama, no deployed instance.
**Authorization:** the project's own source tree, assessed at the owner's
request. Nothing outside it was touched; no third-party host was probed.

This report extends the existing `docs/security/` set rather than
replacing it. Where a prior document already owns a topic
(`THREAT_MODEL.md`, `CVE_TRIAGE.md`, `DAST_REPORT.md`) this one links to
it instead of restating it.

---

## 1. Executive summary

Four findings were confirmed and all four were fixed and verified. One is
a genuine data-exposure vulnerability; three concern the integrity of the
blocking CI security gates.

| ID | Severity | Title | Status |
|---|---|---|---|
| F-01 | **High** | Result-level governance bypassed by LLM-derived narrative text on every stored-result read-back surface | **Fixed, verified** |
| F-02 | **Medium** | `GET /conversations/{id}/messages` served a stored result with no re-governance at all | **Fixed, verified** |
| F-03 | **Medium** | The blocking `bandit` CI gate exits non-zero and has been red, while documented as passing | **Fixed, verified** |
| F-04 | **Low** | A `# nosec B608` sat one line below the statement it was meant to annotate, so it never applied | **Fixed, verified** |

The headline finding (F-01) is this: the platform masks a restricted
column in the result *table* and then serves the LLM's own sentence about
that same value in plain text. An anonymous visitor to a shared
conversation link could read a salary in the prose while the table beside
it read `[restricted]`. F-02 is the same class of defect reached through a
different door, with the additional property that the rows themselves were
never re-governed.

The security architecture around these findings is otherwise strong, and
the findings are better described as a *missed surface* than a design
flaw: the codebase already had the correct control, the correct test file,
and a docstring naming this exact failure mode. What it did not have was
the control applied to derived text, or to the fourth read-back surface.

**Substantial areas were tested and found sound** — see §10. Most notably:
all 214 HTTP routes carry an authorization dependency except four that are
legitimately public; the share-authorization policy is fail-closed against
a bare share UUID; the SSRF validator withstood twelve bypass payloads
including userinfo confusion; SQL identifier quoting is correct across all
four dialects; and the new SVG sanitiser blocks billion-laughs, XXE and
deep nesting.

**This is not a statement that the application is secure.** No DAST ran,
no live IdP, database or model was exercised, and the review was
time-boxed. See §11.

---

## 2. Application architecture and assessment scope

A LangGraph text-to-SQL agent behind a FastAPI app that also serves the
built React dashboard. A question becomes SQL against a user-configured
database (PostgreSQL / MySQL / SQL Server / Oracle), is validated by an
AST allowlist, executed read-only and row-capped, then rendered. Many
optional layers sit on top (multi-source orchestration, analytics,
governed metadata, multi-tenant identity, admin dashboards), most off by
default.

Trust boundaries exercised in this assessment:

| Boundary | Mechanism | Assessed |
|---|---|---|
| Unauthenticated → API | `api/auth.py::verify_api_key`, per-mode | Yes (§3, §10.1) |
| Caller → permission | `agent/authz.py`, `identity/rbac.py` | Yes (§10.1) |
| Caller → another tenant | `security/tenancy.py` | Partially (§11) |
| Caller → another user's resource | per-route ownership scoping | Yes (§10.2) |
| **Anonymous → a shared conversation** | `identity/share_policy.py` | Yes (§10.2) |
| **Stored result → a later reader** | `governance/result_policy.py` | **Yes — F-01, F-02** |
| LLM output → database | `agent/sql_validator.py` | Yes (§10.3) |
| Untrusted markup → browser | `media_gen/svg_sanitize.py`, `Markdown` | Yes (§10.4) |
| App → outbound host | `security/outbound_url.py` | Yes (§10.5) |

Out of scope: the live database role, the deployed container, the Ollama
host, and any network perimeter — none exist in this environment.

---

## 3. Testing environment, tools and limitations

| Item | Value |
|---|---|
| OS | Windows 11 Pro 26300 |
| Python | 3.14.4 (`.venv`; the project targets 3.11 — see `CLAUDE.md` §2) |
| Node | frontend workspace, `npx` from `frontend/` |
| Live DB / Ollama / IdP | **none** — every backend test is mocked |

**Tools actually executed** (all of them, in this session, on this
checkout):

| Tool | Version / scope | Result |
|---|---|---|
| `pytest` | full suite, 2 runs (baseline + post-fix) | §4, §9 |
| `bandit` | CI scope, then all 41 first-party packages | F-03, §9 |
| `pip-audit` | `requirements-dev.txt`, CI's exact ignore list | clean |
| `ruff` | whole repo | 1 pre-existing finding |
| `black --check` | whole repo (1,643 files) | clean on tracked source |
| `detect-secrets` | 1.5.0, against the committed baseline | §9.4 |
| `npm audit --omit=dev` | frontend production deps | 0 vulnerabilities |
| `vitest` | 98 files / 1,372 tests | pass |
| `tsc --noEmit` | `tsconfig.app.json` | clean |
| `oxlint` | `frontend/src` | exit 0 (pre-existing warnings only) |
| FastAPI route introspection | custom, all 214 routes | §10.1 |
| Manual exploitation | targeted PoCs per finding | §5 |

**Tools not run, and why:**

- **Semgrep / CodeQL** — not installed and not available offline in this
  environment. `bandit` plus targeted manual review of the security
  boundaries was used instead. This is a real coverage gap: neither
  taint-tracking SAST tool has run against this code.
- **OWASP ZAP / any DAST** — unchanged from
  [`DAST_REPORT.md`](DAST_REPORT.md): the app's `lifespan` warms real
  database engines, the Ollama client and the compiled graph at startup,
  none of which exist here. That document's reasoning still stands and
  was not re-litigated.
- **Trivy** — no container was built.
- **`gitleaks`** — a Go binary, not available; `detect-secrets` (the
  blocking gate) was run instead.

---

## 4. Baseline, established before any change

Recorded first so every later claim is attributable.

```
$ .venv/Scripts/python.exe -m pytest -q
5 failed, 7958 passed, 11 deselected, 41 warnings in 485.07s (0:08:05)
```

The five failures are **pre-existing and order-dependent**, confirmed by
re-running them in isolation:

```
$ pytest -q tests/test_agent_nodes.py::TestGenerateRecommendationsNode \
          tests/test_agent_nodes.py::TestGenerateRecommendationsNodeQueryStore
11 passed, 2 warnings in 5.35s
```

They are the cross-file-state failures `CLAUDE.md` §13 already documents.
One correction to that entry: it records **6** such failures, of which 3
are `…QueryStore`; this checkout produces **5**, of which 3 are
`…QueryStore` and 2 are `TestGenerateRecommendationsNode`. Not
investigated further — out of scope, and unchanged by this work.

Other gates at baseline:

| Gate | Baseline | Note |
|---|---|---|
| `ruff check .` | 1 finding (`I001`, `agent/multiagent/agents.py`) | matches §13 exactly |
| `black --check .` | clean on tracked source | 2 files flagged, both inside the untracked, prunable `.claude/worktrees/real-estate-skills` worktree — not project source |
| `bandit` (CI scope) | **exit 1** — 6 Low, 1 Medium | **F-03** |
| `pip-audit` | `No known vulnerabilities found, 7 ignored` | matches §5.7 |
| `npm audit --omit=dev` | 0 vulnerabilities | |
| `vitest` | 1,372 passed | |

---

## 5. Findings

### F-01 — Result-level governance bypassed by LLM-derived narrative text

- **Severity: High.** Confidentiality of restricted personal data, reachable by an unauthenticated caller.
- **Category:** Broken access control / sensitive data exposure. OWASP A01 & A02; API Security A03 (excessive data exposure).
- **CWE:** CWE-200, CWE-213 (intentional exposure of sensitive information through a different channel).
- **Components:** `api/shares.py::_projected_turn_out`, `api/artifacts.py::_detail_out`, with the root cause in the division of labour between `agent/nodes.py::execute_sql_node` and `agent/nodes.py::generate_insight_node`.

**Root cause.** `governance/result_policy.py` is documented as protecting
values "before the insight step … sees them", and it does — *for the
caller running the query*. `execute_sql_node` governs with
`state["caller_roles"]`, so an analyst holding
`VIEW_RESTRICTED_COLUMNS` correctly receives unmasked rows. Those rows
then reach `generate_insight_node`, which calls
`agent.insight.summarize_result` — and that function puts **real cell
values** into the summary handed to the LLM: `top_label` is a real label
(a person's name), and `minimum`/`maximum`/`total`/`top_value` are real
measures (a salary). The resulting sentence is persisted verbatim by
`api.chat_persistence._metadata_for_ask`, and
`_answer_text_for_history` additionally copies it into
`ai_outputs.content`.

Both re-governance surfaces then re-governed only the **tabular**
snapshot. `api/shares.py` masked `result_snapshot["rows"]` and passed
`insight`, `synthesized_answer` and `content` through untouched;
`api/artifacts.py:444` did the same for `insight` and `answer`.

This is precisely the failure the existing regression suite was written
to catch. `tests/security/test_shared_results_are_regoverned.py`'s own
docstring says each test asserts the real value "appears nowhere in the
response body, because **a mask applied to one field while the raw value
survives in another is the failure this is guarding against**." The guard
had a hole only because its fixture never populated the narrative fields.

**Preconditions.** Result governance on (default). A caller who holds
`VIEW_RESTRICTED_COLUMNS` (the `analyst` tier and above) runs a query
touching a restricted category, and the turn is then exposed to a
lower-privileged reader — most sharply via an "anyone with the link"
share, whose viewer holds **no roles at all**.

**Impact.** A durable, unprivileged copy of restricted data: PII,
compensation, performance, PHI or banking values, in prose, to an
anonymous reader. The table beside it reads `[restricted]`, which makes
the leak *less* likely to be noticed, not more.

**Reproduction** (synthetic data, through the real HTTP surface):

1. Register an owner; persist a turn whose stored `result_snapshot` holds
   `["employee_name","annual_salary"] / [["Ada Lovelace","187431.55"]]`
   **and** whose `insight` reads `"Ada Lovelace has the highest annual
   salary at 187431.55."` — i.e. what an authorized run really produces.
2. `POST /conversations/{id}/share` with `access_mode=anyone_with_link`.
3. `GET /share-view/{token}` with no credentials.

Observed, before the fix:

```
[result_governance] domain=hr applied: masked_classified_columns:annual_salary,employee_name
[shares] result-level governance re-applied to a shared snapshot (domain=hr, actions=1)
HTTP/1.1 200 OK
FAILED …::test_an_insight_derived_from_a_salary_is_not_served_to_an_anonymous_visitor
```

Governance ran, masked both columns in the table, and `187431.55` still
reached the anonymous viewer.

**Remediation.** §6.1.

---

### F-02 — `GET /conversations/{id}/messages` served a stored result with no re-governance

- **Severity: Medium.** Requires the caller to be the data's own owner, which bounds it; but the control it skipped is explicitly documented as mandatory.
- **Category:** Broken access control (stale authorization). OWASP A01.
- **CWE:** CWE-613 (insufficient session/permission expiration), CWE-200.
- **Component:** `api/chat_history.py::list_messages_route`.

**Root cause.** `CLAUDE.md` §5.4 states the invariant plainly: "A
**stored** result is re-governed on every read against the *current*
caller's roles — never trusted because it was governed when captured.
Roles change." It then names "**both** read-back surfaces" —
`api.artifacts._result_out` and `api.shares._governed_result_snapshot`.

There are in fact **four**. `api/jobs.py::_regovern_job_result` is a third
and does re-govern correctly. `api/chat_history.py` is the fourth and
re-governed nothing: it returned `metadata=row.metadata`, the raw
persisted `metadata_json`, including `result_snapshot` with real rows and
the `insight` prose.

Ownership was never the gap and is correctly enforced —
`identity/repositories/history.py` scopes every query by `user_id` inside
the `WHERE` clause. The gap is *time*. Ownership answers "whose
conversation is this"; result governance answers "may this caller see
these values **now**". This surface was almost certainly missed because
its caller is always the owner, which makes ownership look sufficient.

**Preconditions.** An analyst runs a query over a restricted column and
confirms the run (persisting the snapshot), then either (a) loses the
role, or (b) an operator classifies that column `restricted` in
`config/sensitive_columns.yaml` — (b) needs no role change at all.

**Impact.** A demoted user keeps a permanent, unprivileged window onto
exactly the values the restricted-column gate exists to withhold — the
scenario `tests/security/test_artifacts_cannot_bypass_authorization.py`
refuses for the *artifact saved from that very turn*, while the
conversation it was saved from served them. Small-group suppression and
the per-domain export ceiling were also not applied on reload.

Note what is **not** affected: `CREDENTIAL` and `PAYMENT` are withheld
from every caller at capture time, so the stored snapshot already holds
`[withheld]` for those. The exposure is the role-narrowing and
reclassification cases.

**Reproduction.** Classify `employees.salary` as `restricted`; create an
`analyst`; persist a turn with that column; assert the figure is visible
(it legitimately is); demote the account to `user`; re-login; `GET
/conversations/{id}/messages`.

Observed, before the fix:

```
FAILED …::test_a_demoted_owner_no_longer_reads_the_stored_figures
FAILED …::test_the_narrative_is_withheld_from_a_demoted_owner_too
2 failed, 2 passed
```

The two control tests in the same file passed, which establishes the
harness was sound and the failures were the defect, not the fixture.

**Remediation.** §6.2.

---

### F-03 — The blocking `bandit` CI gate exits non-zero

- **Severity: Medium.** Not itself exploitable; it is the loss of a control that exists to catch exploitable things.
- **Category:** CI/CD security misconfiguration. OWASP A05.
- **Component:** `.github/workflows/ci.yml`, SAST step.

**Root cause, in two parts.**

*Part one — the gate was already red, and documented as green.*
`CLAUDE.md` §5.7 records, as of 2026-10-22, that "`bandit` now **passes**
(4 Low / 0 Medium / 0 High …  — the Low findings are below its default
threshold)". That premise is wrong: `bandit` exits **1** on any finding at
or above its default severity level, which is `low`. Measured directly on
this checkout with 5 Low / 0 Medium / 0 High still present, the exit code
was `1`. §13 of the same file states the opposite and is the accurate one
("…already annotated for `ruff` but not for `bandit` — which is why that
blocking gate fails"). So this gate has been failing for some time; the
`test` job's steps run sequentially, so a red `bandit` also stops
`pytest` and `pip-audit` from running at all in CI.

*Part two — this branch added a new Medium.* Three findings in
`media_gen/` are new since §13's measurement, from this branch's recent
SVG and Hugging Face commits:

| Location | Test | Severity | Assessment |
|---|---|---|---|
| `media_gen/svg_sanitize.py:260` | B314 `ElementTree.fromstring` on untrusted XML | **Medium** | True positive on the API; **the attack class is independently blocked** — verified, below |
| `media_gen/svg_sanitize.py:45` | B405 the import | Low | same |
| `media_gen/providers/huggingface.py:49` | B105 hardcoded password | Low | **false positive** — a user-facing error *message* whose constant name ends `_TOKEN` |

The B314 deserved real scrutiny, since untrusted model-authored SVG is
parsed with the stdlib parser. It was tested rather than assumed:

```
1. billion-laughs: REJECTED (SVG carries a DOCTYPE or ENTITY declaration) in 0.000s
2. XXE:            REJECTED (SVG carries a DOCTYPE or ENTITY declaration)
3. deep-nesting:   REJECTED (SVG nests deeper than 32 levels) in 0.012s
                   (payload 140,046 bytes / 20,000 levels, under the 262,144-byte cap)
4. active content: script, onload, foreignObject, javascript: href and <image href>
                   all stripped; only the allowlisted <rect> survives
```

`sanitize_svg` refuses `<!DOCTYPE` / `<!ENTITY` as a construct *before*
parsing, and XML permits no whitespace inside `<!DOCTYPE`, so there is no
valid spelling that slips past. Entity expansion, quadratic blowup and
XXE all require a DTD. `MAX_SVG_BYTES` and `MAX_DEPTH` bound the rest.
**No exploitable XML vulnerability was found here** — the sanitiser is
well built. The finding is real as a gate failure, not as a hole.

**Remediation.** §6.3.

---

### F-04 — A `# nosec B608` annotated the wrong line

- **Severity: Low.** A suppression that silently did not suppress.
- **Component:** `onboarding/profiling.py:256`.

In `_sample_freshness_days` the `# nosec B608` comment sat on the closing
parenthesis, one line below the f-string `bandit` actually reports, so it
never applied. Its three sibling call sites in the same file place the
comment correctly. Found while triaging F-03's scope (§7).

The underlying code is **safe**: every identifier reaches these f-strings
through `_quote`/`_qualified`, i.e. SQLAlchemy's dialect-aware
`identifier_preparer.quote`. Verified against hostile identifiers in all
four supported dialects:

```
pg      'a" FROM x; DROP TABLE y --'  -> '"a"" FROM x; DROP TABLE y --"'
mysql   'a`; DROP x--'                -> '`a``; DROP x--`'
mssql   'a]; DROP TABLE y--'          -> '[a]]; DROP TABLE y--]'
oracle  'a" FROM x; DROP TABLE y --'  -> '"a"" FROM x; DROP TABLE y --"'
```

Every closing delimiter is correctly doubled. No SQL injection.

**Remediation.** §6.4.

---

## 6. Changes implemented

All fixes reuse the existing governed path and add no dependency. 26 code
files changed (1,074 insertions, 25 deletions), plus the two report
documents.

### 6.1 F-01 — one new control, applied at both surfaces

**`governance/result_policy.py`** — added `narrative_withhold_reason(columns, caller_roles, domain)`.

It answers one question: may free text *derived from* this result be shown
to this caller? It derives that from the **same**
`policy_for(domain)` → `_classification_masks` chain
`govern_result_for_domain` already uses, plus layer 1's own configured
`restricted` list — so "what gets masked in the table" and "what makes the
prose undisclosable" cannot drift apart. Returns a reason naming the
blocking **columns** (never a value, so it is safe to return to the
anonymous caller it just withheld from), or `None`.

Two deliberate design choices:

- **Withheld, never rewritten.** Editing the sentence to remove a figure
  would leave the surrounding claim attached to evidence it no longer
  supports — the same reasoning `CLAUDE.md` §5.8 already applies to unsafe
  recommendations. It also invites the "did it get them all?" question
  that a refusal does not.
- **Empty column list returns `None`.** The control needs a column set to
  judge. A turn with no stored result carries no such evidence, and
  document / policy / web answers are bounded by their own gates (a policy
  chunk's `sensitivity_category`, `DocumentRecord.restricted_roles`).
  Over-blocking is its own failure mode, so this does not guess.

**`api/shares.py`** — `_narrative_withheld_reason()` plus wiring in
`_projected_turn_out`: when set, `insight` and `synthesized_answer` become
`None` and an assistant turn's `content` becomes
`_NARRATIVE_WITHHELD_TEXT`, a fixed module constant (never built from the
turn, so it cannot be used as an oracle — §5.5). The reason is surfaced as
a new optional `ProjectedTurnOut.narrative_withheld_reason`.

**`api/artifacts.py`** — `_narrative_for_caller()` mirroring the existing
`_sql_for_caller`, which already returns a `(value, withheld_reason)` pair
for exactly this situation. `answer` and `insight` are withheld together,
judged across both the origin and refreshed snapshots.

**Frontend** — `narrative_withheld_reason` added to `ProjectedTurn` and
`PersistedTurnMetadata`; `SharedConversation.tsx` renders a localized
notice instead of the backend placeholder, with
`sharedView.narrativeWithheld` added to **all five** locales (en, es, fr,
hi, mr) per §6.2 of `CLAUDE.md`. The result table still renders, masked —
this withholds the prose, not the analysis.

### 6.2 F-02 — the fourth surface

**`api/chat_history.py`** — added `_governed_metadata()` and
`_message_content()`, and wired both into `list_messages_route`. The
former runs the full `govern_result_for_domain` over the stored snapshot
against `get_user_roles(session, user.id)` — the roles the caller holds
*now* — and applies `narrative_withhold_reason` to the prose on the same
decision. Row bookkeeping (`returned_rows`/`truncated`) follows when the
export ceiling removes rows, matching `api/shares.py`'s handling. The
latter covers `ai_outputs.content`, which carries the same sentence.

No new switch: `ENABLE_RESULT_GOVERNANCE=false` rolls all of this back
with the rest of result governance, and a test pins that.

### 6.3 F-03 — gate restored without weakening it

Seven per-line `# nosec` annotations, each with a written justification,
taking the CI-scope `bandit` run from **exit 1** to **exit 0**:

| Location | Test | Justification |
|---|---|---|
| `media_gen/svg_sanitize.py` (parse site + import) | B314, B405 | every attack in the advisory class is refused before the parse; verified by the four tests in §5 |
| `agent/harness/retry.py:75` | B311 | retry jitter spreads load; no secret involved |
| `agent/sql_validator.py:885` | B112 | `describe_sql` is a presentation aid and fails open by design; it is not on the validation path |
| `api/streaming.py:82` | B101 | a wiring invariant — `json.dumps` cannot emit a raw newline; SSE framing does not depend on it holding under `-O` |
| `media_gen/providers/huggingface.py:49` | B105 | false positive: a user-facing message, not a credential |
| `security/audit_log.py:255` | B110 | deliberate — audit logging must never break the control it observes |

**No threshold was changed and `--severity-level` was not touched.** These
are scoped suppressions of specific, individually-verified findings; a new
unsafe pattern anywhere still fails the gate. `defusedxml` was considered
for the B314 and not taken: the attack class is already blocked
structurally, and `CLAUDE.md` §6.3 prefers avoiding a dependency. That
trade-off is recorded in the code comment so a future reader can revisit
it.

### 6.4 F-04

`onboarding/profiling.py` — the suppression moved onto the statement it
describes, with the `identifier_preparer.quote` reasoning written out.
The four `candidate_sql` builders in `onboarding/golden_questions.py` were
annotated too (they were Medium and entirely unscanned — §7), noting that
nothing there executes: every candidate is re-parsed by
`agent.sql_validator.validate_sql` and row-capped before
`onboarding.evaluation.evaluate_candidates` runs it, so the SELECT-only
allowlist is what bounds execution.

---

## 7. An additional gate-coverage observation (open)

`bandit`'s CI scope is an explicit 12-package list:

```
bandit -r agent api config db security media_gen moderation rag search voice media embeddings
```

This repository has **41** first-party packages. The unscanned 29 include
`attachments/` (upload and document parsing), `identity/`
(authentication, RBAC, tokens), `onboarding/`, `governance/`,
`guardrails/`, `jobs/`, `rest/`, `mcp_client/`, `bi_integration/` and
`analytics/`.

Measured by widening the scan to all 41 packages: **5 Medium and 17 Low**
findings live outside the gate's reach. All five Mediums were triaged this
session and are benign — they are the `onboarding/` B608 sites, verified
safe at §5 F-04 — and they are now annotated, so widening the scope today
would surface only the 17 Lows.

**Left open deliberately.** Widening the scope changes what fails the
build for every future contributor, and it would require triaging and
annotating 17 further findings (mostly `assert` usage in `identity/`,
`mcp_client/` and `recommendation/`) that I have not individually
verified. Writing justifications I had not checked would be worse than
leaving the gap visible. §12 carries the recommendation and the measured
numbers.

---

## 8. Root-cause analysis

Three of the four findings share one shape: **a control that was correct
in itself, applied to fewer places than its own documentation claimed.**

- F-01: the control existed and was applied to rows, not to text derived
  from those rows. The gap sits exactly on the seam between a
  *deterministic* gate (classification by column name) and a
  *probabilistic* producer (an LLM sentence). The codebase's own framing —
  "protected before the model sees the values" — is true for an
  unauthorized caller and quietly stops being the whole story once a
  surface re-governs the same data for a *different* caller.
- F-02: the invariant was written down and enumerated as "both read-back
  surfaces" when there were four. Ownership enforcement on the missed
  surface made it look complete.
- F-03/§7: a gate whose pass/fail semantics were misunderstood in the
  project's own notes, and whose scope was a hand-maintained list that did
  not grow with the repository.

The common lesson is about **enumeration**, not about any individual
control: each defect is a surface or a package that was not on a list,
rather than a control that was wrong. The fix for F-01/F-02 therefore
routes every surface through one shared function in
`governance/result_policy.py` instead of adding a per-surface check, so a
fifth surface inherits the decision rather than having to remember it.

---

## 9. Validation

### 9.1 Backend suite — no regressions

| Run | Result |
|---|---|
| Baseline (pre-change) | `5 failed, 7958 passed, 11 deselected in 485.07s` |
| After F-01 fixes | `5 failed, 7972 passed, 11 deselected in 534.14s` |

Identical five pre-existing failures; **+14 passing tests**, zero
regressions. A final full run was executed after the F-02 fix — see §9.5.

### 9.2 Each fix was proven non-vacuous

`CLAUDE.md` §7 requires that a load-bearing guard fail when broken. Every
new control was mutation-tested by neutering it and re-running:

```
# narrative_withhold_reason stubbed to return None
FAILED tests/security/test_artifacts_cannot_bypass_authorization.py::…::test_the_narrative_is_withheld_once_the_role_is_revoked
FAILED tests/security/test_shared_results_are_regoverned.py::…::test_an_insight_derived_from_a_salary_is_not_served_to_an_anonymous_visitor
FAILED tests/security/test_shared_results_are_regoverned.py::…::test_the_stored_content_and_synthesized_answer_are_withheld_together
FAILED tests/security/test_shared_results_are_regoverned.py::…::test_the_reason_names_columns_and_never_a_value
4 failed, 20 passed

# _governed_metadata stubbed to return the raw metadata
FAILED tests/security/test_chat_history_reload_is_regoverned.py::…::test_a_demoted_owner_no_longer_reads_the_stored_figures
FAILED tests/security/test_chat_history_reload_is_regoverned.py::…::test_the_narrative_is_withheld_from_a_demoted_owner_too
2 failed, 2 passed
```

Both controls restored afterwards; all tests green.

### 9.3 Tests added

| File | Added | Covers |
|---|---|---|
| `tests/security/test_shared_results_are_regoverned.py` | 5 | the anonymous-share leak, `content`/`synthesized_answer` withheld together, the reason leaking no value, ordinary analytics **not** over-blocked, the rollback switch |
| `tests/security/test_artifacts_cannot_bypass_authorization.py` | 2 | narrative withheld after demotion; an entitled analyst still reads it |
| `tests/security/test_chat_history_reload_is_regoverned.py` | 4 (new file) | the F-02 scenario end to end, plus the entitled case and the rollback switch |
| `tests/test_governance_result_policy.py` | 7 | `narrative_withhold_reason` unit behaviour, `GENERAL` strictness, operator-classified columns, no-evidence case |
| `frontend/src/pages/SharedConversation.test.tsx` | 1 | the localized notice renders; neither the reason nor the English placeholder reaches the page |

Every one of these asserts the legitimate path still works as well as the
attack path failing — over-blocking is a failure mode in its own right,
and `test_an_ordinary_analytics_insight_is_still_shown` exists to pin it.

### 9.4 Gates, after the changes

| Gate | Before | After |
|---|---|---|
| `bandit` (CI scope) | **exit 1** (6 Low, 1 Medium) | **exit 0** (0 findings, 16 justified `#nosec`) |
| `ruff check .` | 1 pre-existing `I001` | 1 pre-existing `I001` (unchanged) |
| `black --check .` | clean on tracked source | clean on tracked source (1,643 files) |
| `pip-audit` | `No known vulnerabilities found, 7 ignored` | unchanged |
| `npm audit --omit=dev` | 0 | 0 |
| `tsc --noEmit` | clean | clean |
| `oxlint` | exit 0 | exit 0, no warnings in changed files |
| `vitest` | 1,372 passed | 1,372 + 1 passed |

**A process error I made and corrected, recorded because it matters.**
While verifying `detect-secrets`, I ran it with `--baseline
.secrets.baseline` scoped to only the changed files. That command
**rewrites the baseline in place**, and scoping it to a subset deleted the
other 4,786 lines — exactly the hazard `.github/workflows/ci.yml`'s own
comment and `CLAUDE.md` §12 warn about. Caught by reading
`git diff --stat` (`4797 deletions`), restored with `git checkout --
.secrets.baseline`, and verified clean. Subsequent verification used a
**copy** in a scratch directory. The committed baseline is byte-identical
to `HEAD`.

With that method, the changed files were checked against the committed
baseline. The only new occurrence attributable to this work is the test
constant `PASSWORD = "correcthorse battery staple 1"` in the new security
test file — byte-identical to the constant already present in the
committed sibling `tests/security/test_artifacts_cannot_bypass_authorization.py:53`
and in `test_shared_results_are_regoverned.py`, neither of which is in the
baseline either. So this introduces no new *class* of finding. The
`Basic Auth Credentials` hit in `tests/test_governance_result_policy.py` is
pre-existing content (`"postgresql://app:hunter2@db.internal/prod"`, a
fixture asserting that redaction works), merely line-shifted by my import
addition; and the five locale edits are one added line each, at line ~308,
well clear of the findings those files already carry at lines 15–45.

**This local verification is indicative, not authoritative** — §11.3
records why the Windows reproduction of this gate could not be trusted,
and §12.2 the single Linux run that would settle it.

### 9.5 Final full-suite run

```
$ .venv/Scripts/python.exe -m pytest -q
5 failed, 7976 passed, 11 deselected, 41 warnings in 725.85s (0:12:05)
```

The same five pre-existing, order-dependent failures as the baseline, and
**+18 passing tests**. No regressions across three full runs.

---

## 10. Areas tested and found sound

Recorded because a report of four findings is only meaningful alongside
what did not yield one.

### 10.1 Authorization coverage — all 214 routes

Rather than sampling, the FastAPI app was introspected directly and every
route's resolved dependency tree walked for an authentication or
authorization dependency. (FastAPI 0.141 wraps `include_router` results in
`_IncludedRouter`, so the naive `app.routes` walk finds only 12 of them —
the audit recurses through `effective_candidates()`/
`effective_route_contexts()`, which carry the merged router-level
dependencies.)

```
=== TOTAL 214 routes; UNGUARDED 4 ===
  GET      /auth/google/nonce      api.identity_auth.google_nonce
  GET      /health                 api.main.health
  GET      /live                   api.main.live
  GET      /{full_path:path}       api.main.serve_frontend
```

All four are legitimately public. Of the 98 routes taking a path
parameter, every one carries at least `verify_api_key`; the 15 routes
without it are the `/auth/*` pre-auth flows and the two
`/share-view/{ref}` endpoints, which are unauthenticated **by design**.
**No missing-function-level-authorization finding.**

### 10.2 Share authorization — fail-closed

`GET /share-view/{ref}` accepts either a secret link token *or* a raw
share UUID. The obvious attack is to present a known UUID without the
secret. `identity/share_policy.py` denies it: for an
`anyone_with_link` share, `link is None` returns
`_deny("link_required")`. Every denial reason collapses to one generic 404
(only a 429 is distinguishable), and the attachment sibling re-derives
authorization from scratch and additionally checks membership in
`ShareProjection.approved_attachment_ids`. No IDOR, no enumeration
oracle.

### 10.3 SQL injection

- `agent/sql_validator.py` is an allowlist on the *parsed statement type*,
  not a regex blocklist. The `onboarding/` candidate-SQL path routes
  through it before execution (§6.4).
- Identifier interpolation in `onboarding/profiling.py` uses SQLAlchemy's
  dialect-aware preparer, verified correct against hostile names in four
  dialects (§5 F-04).
- No `os.system`, `eval`, `exec`, `pickle.loads` or `yaml.load` in the
  request path. `mcp_client/transport.py` uses `subprocess` with
  `shell=False`, documented.

### 10.4 XSS and untrusted markup

- **No `dangerouslySetInnerHTML` anywhere in production frontend code** —
  the only `innerHTML` occurrences are in test files.
- `components/ui/markdown.tsx` deliberately omits `rehype-raw`, so HTML in
  model or web-retrieved text renders as inert text. react-markdown v10's
  default `urlTransform` sanitizes dangerous URL protocols, so a
  `[x](javascript:…)` link is neutralized.
- `SourceAnswerCard.tsx` carries `rel="noopener noreferrer"` and gates on
  `^https?://` before linking a citation.
- The SVG sanitiser is an allowlist and strips all active content (§5 F-03).

### 10.5 SSRF

`security/outbound_url.py::assert_safe_url` was exercised against twelve
payloads. All eleven hostile ones were refused, each with a logged
`ssrf_blocked` event:

```
rejected http://169.254.169.254/latest/meta-data/   non_https_scheme
rejected https://169.254.169.254/                   host_not_allowlisted
rejected https://api.tavily.com@127.0.0.1/          host_not_allowlisted (userinfo confusion)
rejected file:///C:/Windows/win.ini                 non_https_scheme
rejected gopher://api.tavily.com/                   non_https_scheme
rejected https://[::1]/ , https://10.0.0.5/ ,
         https://localhost/ , https://evil.com/     host_not_allowlisted
ALLOWED  https://API.TAVILY.COM/x                   (case-insensitive match, intended)
ALLOWED  https://api.tavily.com/search              (the legitimate case)
```

The allowlist is checked **before** DNS, so a non-allowlisted host costs
no lookup on an attacker's behalf. The resolve-then-connect TOCTOU window
is disclosed in the function's own docstring and bounded by the
allowlist.

### 10.6 Dependencies and secrets

`pip-audit` reports no unignored vulnerabilities; the six ignored IDs
carry individual, re-verified unreachability justifications in
`.github/workflows/ci.yml` and [`CVE_TRIAGE.md`](CVE_TRIAGE.md).
`npm audit --omit=dev` is clean. No hardcoded credential was found in
application code; secrets are `SecretStr` and `.env` is correctly
ignored. `env/` and `env1/` at the repository root are stale local
virtualenvs, both ignored via the `.gitignore` that `venv` writes into
them — not leaks.

---

## 11. Remaining risks, accepted risks and unverified findings

**Open, not fixed:**

1. **`bandit`'s gate scope** — 29 of 41 first-party packages unscanned; 17
   Low findings outside its reach. Measured, triaged as far as the
   Mediums, recommendation in §12. §7 explains why the widening itself was
   not done unilaterally.
2. **`GET /chat/search` snippets are not governed.** `SearchHitOut.snippet`
   derives from `ai_outputs.content`, the same field that carries the
   insight prose — so a search result could surface a fragment of a
   narrative that `GET /conversations/{id}/messages` now withholds.
   Narrower than F-02 (a snippet, matched by the caller's own query
   terms, for their own conversation) and it needs per-hit metadata that
   `search_history` does not currently return, so governing it is a
   change to the repository layer rather than a one-line addition. Named
   here rather than half-fixed.
3. **`detect-secrets`' committed baseline appears stale — unverified.**
   A full scan of all tracked files on this machine reported **44 files
   with findings that `.secrets.baseline` does not record at all**, almost
   all of them `tests/*` files and `identity/migrations/versions/*`
   (Alembic revision hex), consistent with drift since the baseline was
   generated on 2026-10-01 against the same tool version (1.5.0, 27
   plugins).

   **I could not reproduce CI's step faithfully, so I am not claiming this
   gate is red.** Two reasons, both measured: (a) the committed baseline
   uses forward-slash paths and a Windows scan emits backslashes, which
   the workflow's own comment flags as making the comparison meaningless
   without normalization — after normalizing, 44 files still appeared
   new; but (b) the same `HEAD` locale files produced **7 findings each**
   when scanned inside the repository and **zero** when scanned as copies
   in a scratch directory, which means my local invocation is not
   equivalent to CI's and its output cannot be trusted either way.

   Every finding I did inspect is a test fixture, a UI label, an Alembic
   revision id, or a docstring describing redaction — **no real
   credential**. This needs a single authoritative run on a Linux runner
   to settle; see §12.2.
4. **Two surfaces disagree about whether to show SQL that touches a
   restricted column.** `api/artifacts.py::_sql_for_caller` withholds the
   recorded SQL (returning `sql_withheld_reason`) when
   `find_restricted_column_references` finds a hit and the caller lacks
   `VIEW_RESTRICTED_COLUMNS`. `api/shares.py` applies no such check — `sql`
   is on `_PROJECTED_METADATA_FIELDS` and is served to an anonymous link
   visitor as-is. Confirmed by inspection: `shares.py` has no reference to
   `_sql_for_caller` or `find_restricted_column_references`.

   Deliberately **not** changed here. The exposure is schema identifiers
   (table and column names) rather than values, and showing the query is
   arguably the point of sharing a conversation — so withholding it could
   break the feature's legitimate purpose, and that is a product call, not
   a security one I should make unilaterally. But the two surfaces reach
   opposite conclusions on the same question, and only one of them can be
   right. Worth resolving deliberately. Note a secondary consideration: a
   query carrying a literal in its `WHERE` clause (`WHERE salary >
   180000`) puts a value in the SQL text; I did not assess how often the
   pipeline produces those.

5. **Documentation drift in `CLAUDE.md`** (§5.7's "bandit now passes", and
   §13's 6-vs-5 baseline failure count). Not edited: that file is a
   cross-cutting rules document and §11 of its own definition-of-done
   says to change it only when a rule changed. The corrections are
   recorded here and in `REMEDIATION_SUMMARY.md`.

**Residual risk on the fixed findings:**

- F-01's control is **evidence-dependent**. It judges by the stored
  snapshot's column names. A turn with an `insight` but **no** persisted
  `result_snapshot` — an `/ask` that was never confirmed with "Confirm and
  Run" — has no column evidence, and `narrative_withhold_reason` returns
  `None` for it. Such a turn's prose is served as before. Fixing that
  properly means persisting the governance decision (or the column set) at
  capture time, which is a schema-touching change I did not take on
  unprompted. **This is the sharpest remaining edge of F-01** and should be
  the next piece of work.
- The classification driving all of this is name-pattern inference, because
  `config/sensitive_columns.yaml` ships empty. A restricted column whose
  name matches no pattern is protected by neither the old control nor the
  new one. Unchanged by this work, and already `CLAUDE.md` §5.4's stated
  position.
- F-02's fix governs the reload surface; the `/chat/search` sibling remains
  open (above).

**Unverified, environment-blocked** (unchanged from the existing
documents, re-confirmed rather than assumed): no DAST has ever run; OIDC
has never been exercised against a live IdP; ClamAV's fail-closed path has
never seen a real daemon; tenant isolation was reviewed in code and is
covered by `tests/security/test_cross_tenant_isolation.py`, but was not
probed against a live multi-tenant database; and no taint-tracking SAST
(Semgrep/CodeQL) has been run against this code.

**Not assessed at all:** the production database role's actual privileges,
container runtime configuration, TLS termination, and the Ollama host.

---

## 12. Required manual and infrastructure-level remediation

Nothing here was done automatically; each needs an owner decision.

1. **Widen `bandit`'s CI scope** to all first-party packages and triage
   the 17 remaining Low findings. Suggested replacement for the SAST
   step's package list, which would then need those 17 annotated first:
   the 41 directories excluding `tests/`, `eval/`, `scripts/`, `docs/`,
   `data/`, `prompts/`, `media_library/`. Do **not** add
   `--severity-level medium` to make it pass — that would hide the class
   of finding F-03 was.
2. **Regenerate `.secrets.baseline`** on a Linux runner (forward-slash
   paths) and re-audit, which clears risk 3 in §11. `detect-secrets scan
   $(git ls-files) --baseline .secrets.baseline` followed by
   `detect-secrets audit .secrets.baseline`. Never scope that command to a
   subset of files — it rewrites the baseline in place (§9.4).
3. **Decide on F-01's residual edge** (persisting the column set or the
   governance decision at capture time, so an unconfirmed `/ask` turn's
   prose can be judged).
4. **Govern `/chat/search` snippets**, or document the exposure.
5. **Credential rotation: not required by these findings.** No credential
   was exposed, logged or committed by this work, and no secret value
   appears in this report. The pre-existing production checklist in
   [`../../SECURITY_PRODUCTION_CHECKLIST.md`](../../SECURITY_PRODUCTION_CHECKLIST.md)
   is unchanged.
6. **Before deploying these changes**, confirm the response-shape addition
   is acceptable to any non-frontend API consumer: three response models
   gained an optional `narrative_withheld_reason` (defaulting to `None`),
   and `ProjectedTurn.content` / `ArtifactDetailOut.answer` /
   `insight` can now be a placeholder or `None` where they previously
   always carried text. This is additive and backward-compatible per
   `CLAUDE.md` §6.3, but it is a visible behaviour change for a
   less-privileged reader, which is the point.

---

## 13. Production deployment checklist (delta only)

The standing checklist is
[`../../SECURITY_PRODUCTION_CHECKLIST.md`](../../SECURITY_PRODUCTION_CHECKLIST.md).
This change adds:

- [ ] `ENABLE_RESULT_GOVERNANCE=true` (the default) — the new narrative
      gate is governed by this switch and rolls back with it.
- [ ] Frontend rebuilt (`npm run build`), so the five new locale strings
      and the share-view notice ship. Without a rebuild a share viewer
      sees the backend's English placeholder rather than the localized
      notice — degraded copy, not a security regression.
- [ ] `bandit` confirmed green in CI after these annotations land, and the
      §12.1 scope decision scheduled rather than forgotten.
- [ ] No migration, no credential rotation, no infrastructure change is
      required by any fix in this report.

---

## 14. Final security posture

**Better than at the start of this assessment, and not proven secure.**

One genuine data-exposure vulnerability (F-01) and one stale-authorization
defect (F-02) were found, reproduced through the real HTTP surface, fixed
at the root cause, and pinned by mutation-verified regression tests. Two
CI-integrity defects were fixed, restoring a blocking gate that had been
red while documented as green — which also means `pytest` and `pip-audit`
had not been running in CI past that step.

The underlying architecture held up well under targeted attack: 214/214
routes authorized, share authorization fail-closed against a bare UUID,
SSRF refusing eleven of eleven hostile URLs, correct identifier quoting in
four dialects, no raw-HTML sink in the frontend, and a new SVG sanitiser
that blocks billion-laughs, XXE and deep nesting. The defects found were
consistently *missed surfaces* rather than broken controls — which is a
more tractable class of problem, and is why the fixes route every surface
through one shared function.

**What would most change this verdict:** running a taint-tracking SAST and
a real DAST pass, closing the residual F-01 edge for unconfirmed turns,
and widening the `bandit` scope. Until then, a clean run of these gates
means the checks that exist passed — not that no vulnerabilities remain.

Per-finding status, fix and test: [`REMEDIATION_SUMMARY.md`](REMEDIATION_SUMMARY.md).
