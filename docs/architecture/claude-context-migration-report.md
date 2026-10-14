# CLAUDE.md context migration report

**Date:** 2026-10-14
**Trigger:** Claude Code reported `CLAUDE.md is over the 150.0k-char limit
(378.8k chars) · /memory to free up context`.
**Outcome:** the root instruction file went from **393,990** to **36,976**
characters — a **90.6% reduction** (357,014 characters removed) — with every
requirement preserved either in the rewritten root file or in a reference
document it names.

---

## 1. What was measured

The warning's figure and the file on disk did not match, so both were
measured rather than assumed.

| Measurement | Value |
|---|---|
| `CLAUDE.md` on disk, bytes | 401,913 |
| `CLAUDE.md` with line endings normalized to `\n` | 393,990 |
| Same file at commit `0adc7d4` (git blob, LF) | 380,336 |
| Claude Code's reported figure | ~378,800 |
| Claude Code's hard limit | 150,000 |

The checkout is CRLF, which adds one character per line (6,327 lines), and
two further commits had landed since the warning was shown. **The warning's
378.8k corresponds to this file at roughly commit `0adc7d4`; by the time of
this work it had grown to 393,990.** All reductions below are quoted against
the measured 393,990.

### What actually contributes to instruction context

| Candidate | Found | Loaded automatically? |
|---|---|---|
| Root `CLAUDE.md` | yes, 393,990 chars | **Yes — the sole contributor** |
| Nested `CLAUDE.md` anywhere in the repo | none (searched, excluding `.venv`/`node_modules`) | n/a |
| `.claude/rules/` | does not exist | n/a |
| `.claude/` contents | `settings.json` (562 B), `scheduled_tasks.lock` (124 B) | No — not instruction files |
| `AGENTS.md`, `.cursorrules` | do not exist | n/a |
| `.github/copilot-instructions.md` | 362 B | No — a different tool |
| 51 `NN_*_CONTRACT.md` at the repo root, ~70 `docs/**.md` | yes | **No** — read on demand only |

So the 378.8k was one file, and the fix is entirely about that file. No
scoped-rules mechanism was introduced: `.claude/rules/` does not exist here,
and moving content into another automatically loaded location would not have
reduced anything.

---

## 2. What the audit found

The root file was **a changelog, not an instruction set.** 95 sections; 83 of
them at `###` level, each one a per-feature or per-prompt writeup of 1-22k
characters. Two umbrella sections held 85% of the file:

| Section | Chars | Share |
|---|---|---|
| `## Key design decisions` | 181,645 | 46% |
| `## Prompts 33-35: AI analysis layers` (everything after it) | 153,230 | 39% |
| `## Folder conventions` | 36,199 | 9% |
| Everything else (identity, stack, how to run, standards, gaps, never-touch) | ~23,000 | 6% |

**The decisive finding: nearly every one of those sections already ended with
"Read [`NN_X_CONTRACT.md`]".** The repository holds 51 numbered contract
documents plus ~70 documents under `docs/`, and those are already
authoritative, richer, and unloaded. The root file was paying full context
cost for a summary layer over documents that already existed.

What was *not* duplicated, and therefore could not simply be deleted: each
section's **"Invariants that must not regress"** block. Spot-checking four
contracts (`44`, `47`, `49`, `50`) showed they carry the design narrative and
the properties in prose, but not that distilled "do not break this" list. So
those blocks were **moved verbatim**, not summarized.

---

## 3. What was done

### Files created

| File | Chars | Contents |
|---|---|---|
| `docs/architecture/context-map.md` | 17,762 | The index: topic → document, what to read before every task, conflict resolution, the 51-contract index, which documents are stale snapshots |
| `docs/architecture/overview.md` | 66,194 | 33 cross-cutting design-decision sections, verbatim |
| `docs/architecture/repository-map.md` | 36,669 | The full per-package folder conventions, verbatim |
| `docs/architecture/features-analytics.md` | 110,589 | 20 analytics/intelligence feature sections, verbatim |
| `docs/architecture/features-governance-and-admin.md` | 129,459 | 24 identity/governance/admin feature sections, verbatim |
| `docs/architecture/features-frontend.md` | 41,372 | 7 frontend feature sections, verbatim |
| `docs/architecture/claude-context-migration-report.md` | this file | The record |
| `scripts/check_instruction_size.py` | ~4,600 | Reports automatically-loaded instruction sizes; exit 1 over the 60k soft target, 2 over Claude Code's 150k limit |

### Files updated

| File | Change |
|---|---|
| `CLAUDE.md` | Rewritten: 393,990 → 36,976 chars (587 insertions, 6,257 deletions) |

### Files moved, removed or renamed

**None.** No document was deleted, renamed or relocated. No application
source file was touched. The 85 moved sections were *copied* out of
`CLAUDE.md` and then dropped from it; the original remains in git history at
`df3452b` and every earlier commit.

### Where each piece of content went

| Original section group | Destination |
|---|---|
| Project identity, tech stack, how to run, commands, Windows notes, coding standards, known gaps, never-touch | **Kept in the root**, condensed |
| `## Folder conventions` (36k) | `repository-map.md` |
| Retry loop, planning, golden examples, nested aggregates, schema scoping, business-context retrieval, relationship intelligence, multi-DB routing, tools, multi-source, RAG, web search, media gen/search, moderation, malware, voice, SQL-untrusted, read-only layering, Pydantic config, exception handling, singletons, caching, observability, charting, insight depth, attachments, scale-out, model selection, scalability assessment, image editing, result cache, Python 3.14 gotchas | `overview.md` |
| Prompts 10-19, 22, 37-43, 50-52 (analytics engines) | `features-analytics.md` |
| Auth/authz, chat history, Google sign-in, sharing, prompts 08-09, 20-21, 23-29, 33-35, 44-49, the data-privacy governance engine, the injection benchmark | `features-governance-and-admin.md` |
| UI redesign, prompts 30-32, the conversational-intelligence UI series, follow-up chips, execution timeline | `features-frontend.md` |

---

## 4. The rewritten root file

36,976 characters, 13 sections, structured as operational instructions rather
than history:

1. Project overview — including an explicit **implementation-status table**
   (live / built-not-wired / off-by-default / disclosed gap), because
   conflating those four is the most common mistake made in this repo
2. Verified technology stack
3. Repository navigation — one line per package, pointing at the map
4. Core architecture rules — 8 numbered non-negotiables
5. Mandatory security and privacy rules — 8 subsections (§5.1-§5.8)
6. Coding and implementation standards
7. Testing and validation
8. Documentation references
9. How to run, commands, Windows notes
10. Standard task workflow (INSPECT → … → REPORT)
11. Definition of done — an 11-item checklist
12. Never touch
13. Known gaps and follow-ups

Content that was **added** rather than carried over, because the original had
it implied across 50 sections but never stated once: the implementation-status
table, the consolidated core-architecture rules, the consolidated security
rules, the task workflow, and the definition of done.

---

## 5. Preservation checks

### 5.1 Line-level content preservation — zero unexplained loss

Every non-empty, non-heading line of the original was compared against the
union of the five new reference documents:

| Measure | Count |
|---|---|
| Distinct content lines in the original | 5,698 |
| Distinct content lines in the new reference docs | 5,541 |
| Original lines not present in the new docs | 276 |
| …of those, belonging to a section the root **keeps** | 276 |
| **…unexplained (real loss)** | **0** |

The 276 are the project-identity, tech-stack, how-to-run, coding-standards,
known-gaps and never-touch lines, all of which the rewritten root carries in
condensed form. Verified by mapping each one back to a kept section's line
range, not by inspection.

### 5.2 Security rules — preserved and consolidated, none weakened

Every security requirement in the original was carried into root §5, which is
now the single authoritative statement of each:

| Area | Where it now lives |
|---|---|
| AST allowlist, system-catalog block, nested aggregates, unsafe hints | §5.1 |
| Re-validate on every display/execution, including after a user edit and for stored SQL | §5.1 |
| Layered read-only (validator + genuinely read-only `DB_USER`, startup-enforced) | §5.1 |
| Row cap enforced twice; driver-level timeout | §5.1 |
| Internal retry executions never shown to the user | §5.1 |
| Base-role + granular RBAC; unrecognized role grants zero | §5.2 |
| RBAC inside the multi-source router itself | §5.2 |
| UI restrictions are never boundaries | §5.2 |
| `ENVIRONMENT=production` fails closed without identity | §5.2 |
| Tenant id never from client input; `X-Tenant-*` refused | §5.3 |
| Unknown tenant denies; one generic message; status read live | §5.3 |
| Cross-tenant → the same 404 as nonexistent (anti-enumeration) | §5.3 |
| Tenant scoping in the `WHERE` clause, not post-hoc | §5.3 |
| Name-pattern classification; explicit human classification wins | §5.4 |
| `[restricted]` masking; **credential/payment withheld from everyone** | §5.4 |
| Governance runs before the model sees values | §5.4 |
| Gate classifies returned column names, not SQL text | §5.4 |
| Small-group suppression applies to authorized callers; no row dropped | §5.4 |
| Stored results re-governed on every read | §5.4 |
| All retrieved/supplied content is data, never instructions | §5.5 |
| Claimed authority in text or data is not authorization | §5.5 |
| Fixed refusal messages (no oracle) | §5.5 |
| Injection patterns are detection, not the boundary | §5.5 |
| `sensitivity_category` hard block + `restricted_roles` RBAC | §5.5 |
| Live prompts are never loaded from the database | §5.5 |
| `SecretStr` + `.get_secret_value()` | §5.6 |
| Never log connection strings, passwords or full rows | §5.6 |
| `safe_message`, global handlers, `redact_secrets` | §5.6 |
| No connection secret persisted | §5.6 |
| SSRF validation re-run on every use; media served under an opaque id | §5.6 |
| Upload validation, malware scan, zip-bomb guard, PDF preflight | §5.7 |
| Mandatory moderation gate; whole-asset reject | §5.7 |
| Admission control; identity-keyed rate limits; trusted-proxy opt-in | §5.7 |
| Blocking CI gates | §5.7 |

**Nothing was softened.** Two rules were made *more* prominent than in the
original, where they appeared only inside a feature narrative: that
credential and payment data is withheld from every caller including an admin,
and that small-group suppression applies to authorized callers too.

### 5.3 Domain privacy and safety — preserved, with a mandatory pointer

Root §5.8 states the rules and **requires consulting
[`../DATA_PRIVACY_GOVERNANCE_ENGINE.md`](../DATA_PRIVACY_GOVERNANCE_ENGINE.md)
before changing anything that affects what may be asked, returned or
recommended.** The authoritative document already existed and was not
duplicated.

The policies were extracted from the code, not invented:
`governance/domains.py` (18 domains + `GENERAL`, 14 `ProhibitedAction`
values, `BASELINE_RESTRICTED_CATEGORIES`) and
`governance/classification.py` (`NEVER_DISCLOSED_CATEGORIES`). Preserved
verbatim in substance:

- The baseline floor (PII, SPI, PHI, compensation, performance, credential,
  payment, banking) is unioned into **every** domain; always read
  `effective_restricted_categories`, never `restricted_categories`.
- `GENERAL` prohibits **all 14** actions, not none.
- The prohibited outputs named in the task — medical diagnosis/prescription,
  individual employment actions, individual compensation decisions,
  individual performance rankings — are present, alongside the other ten.
- Aggregate, organizational and process recommendations are explicitly
  unaffected, so the rule cannot be read as blocking ordinary analytics.
- One action list drives both the request gate and the recommendation filter.
- An unsafe recommendation is withheld and reported, never rewritten.
- Telemetry counts decisions, never content.
- The three independent rollback flags.

### 5.4 Features and requirements

Every feature section survives verbatim in a `features-*.md` or
`overview.md`, including:

- The **four implementation states**, now stated explicitly in root §1 rather
  than left implicit.
- Every "built, not wired" disclosure (root-cause engine, forecasting
  recommendations, visualization specs, tool registry, `RecommendationProvider`).
- Every "off by default" flag and the reason.
- Every disclosed gap — no scheduler, no delivery channel, no streaming,
  process-local state, environment-blocked verifications — consolidated into
  root §13 *and* retained in full in the feature docs.
- Requirements for not-yet-implemented work (the `search/` test gap, per-source
  decomposition, the LangGraph checkpointer, multi-source eval coverage).

### 5.5 Architecture decisions and constraints

Preserved in `overview.md` verbatim, with the eight cross-cutting ones
promoted into root §4 so they are always in context: the bounded state
machine, fail-open vs fail-closed, propose-never-decide, mandatory
provenance, reuse-the-governed-path, request-scoped state, additive-not-rewrite,
and one-owner-per-fact.

---

## 6. Validation performed

| Check | Result |
|---|---|
| Root `CLAUDE.md` under 150,000 chars | **36,976** — pass (4.1× margin) |
| Root within the 30,000-60,000 target | pass |
| Nested `CLAUDE.md` files | none exist |
| Line-level content preservation | 0 unexplained losses (§5.1) |
| Relative links in all 7 new/edited docs | 232 checked, **0 broken** |
| Link re-anchoring after the move | 93 root-relative links rewritten for `docs/architecture/` |
| Application source changed | **none** — only `CLAUDE.md`, new docs, and one new script |
| Secrets in new documentation | none — `detect-secrets scan` over the new files reports 0 findings; a regex sweep for credential-shaped assignments found none |
| `scripts/check_instruction_size.py` | runs, exit 0 |
| Parallel session's uncommitted work | untouched and verified present |
| Backend test suite | unaffected by design (documentation only); last full run 5,666 passed |

### Warning verification — what can and cannot be concluded

The root file is now **36,976 characters, measured**, against a 150,000
limit, and it is the only automatically loaded instruction file in this
repository (§1). On that evidence the warning should not recur.

**This cannot be verified conclusively from repository inspection alone** —
the warning is emitted by Claude Code based on what it loads at session
start, which may include sources outside this repository (for example a
user-level `~/.claude/CLAUDE.md`). If the warning persists, run
`python scripts/check_instruction_size.py` to confirm this repository's
contribution, then look for a user- or project-level instruction file outside
the repo. No such file was inspected here, because doing so is outside the
repository under test.

---

## 6a. One mistake made during validation, and its fix

Running `detect-secrets scan --baseline .secrets.baseline <paths>` to check
the new documentation **rewrote the baseline in place**, dropping 4,799 of its
4,924 lines — because passing explicit paths makes the tool re-emit a baseline
covering only those paths. `.secrets.baseline` is on the root file's own
never-touch list for exactly this reason.

Caught immediately by `git diff .secrets.baseline`, restored with
`git checkout -- .secrets.baseline` (4,924 lines, back to the committed
state, verified clean in `git status`), and the check re-run as a plain
`detect-secrets scan` with no `--baseline` flag, which reports findings
without writing anything. Result: **0 findings** in the new files.

Recorded here rather than quietly fixed, because the lesson generalizes: a
scan command that takes a baseline path may update it.

## 7. Remaining limitations and manual follow-ups

1. **Three reference documents exceed the 60k soft target**
   (`features-governance-and-admin.md` 129k, `features-analytics.md` 110k,
   `overview.md` 66k). This is deliberate: they are read on demand, never
   loaded automatically, and splitting them further would scatter related
   invariants. Each opens with a contents index so a reader can jump
   directly. If any becomes unwieldy, split by subsystem rather than by size.
2. **The moved sections are verbatim**, so they retain their original
   narrative voice and dated framing ("same pass", "this prompt"). That
   wording is accurate and traceable to the commit that added it; it was left
   alone rather than rewritten, which would have risked altering meaning for
   a purely cosmetic gain.
3. **`docs/EVALUATION_CURRENT.md`'s accuracy numbers remain dated**
   (2026-09-16, many pipeline changes ago). Already a P1 in the current
   release gate; flagged again in root §13 and in the context map's
   stale-snapshot table. Re-measuring needs live Ollama and databases.
4. **No archive copy of the original was added to the repository.** Git
   history is the archive (`git show df3452b:CLAUDE.md`). Committing a
   394k archive file would have re-added the bulk this work removed, and a
   backup named `CLAUDE.md` in a loadable location is explicitly the thing to
   avoid. A working copy was also kept in the session scratchpad, outside the
   repository.
5. **The soft-target gate is not wired into CI.** `check_instruction_size.py`
   returns the right exit codes for a pre-commit hook or CI step, but adding
   it to the workflow was out of scope for a documentation change. One-line
   follow-up if wanted.

---

## 8. Reproducing the measurements

```powershell
python scripts\check_instruction_size.py          # current sizes, with budgets
git show df3452b:CLAUDE.md | wc -c                # the original, 395,586 (LF)
```
