# Open Security Decisions

Items from `PENETRATION_TEST_REPORT.md` that need an **owner's decision**
or **infrastructure access**, not a code change I can make unilaterally.
Each states the evidence, the options, and what I recommend.

Companion to [`REMEDIATION_SUMMARY.md`](REMEDIATION_SUMMARY.md), which
covers what was fixed.

---

## O-4 — Two surfaces disagree about echoing SQL that touches a restricted column

### The evidence

| Surface | Behaviour | Code |
|---|---|---|
| Artifacts | **Withholds** the recorded SQL and reports `sql_withheld_reason` when `find_restricted_column_references` hits and the caller lacks `VIEW_RESTRICTED_COLUMNS` | `api/artifacts.py::_sql_for_caller` |
| Shares | **Serves** `sql` to an anonymous link visitor, no such check | `api/shares.py`; `sql` is on `_PROJECTED_METADATA_FIELDS` |

Confirmed live during the first pass: an anonymous share view returned
`"sql":"SELECT employee_name, annual_salary FROM hr_employees"` in the
same response whose table correctly read `[restricted]`.

Only one of these can be right.

### What is actually exposed

Normally **schema identifiers** — table and column names — not values.
That is a real but modest exposure: it tells a reader your HR table is
called `hr_employees` and has an `annual_salary` column.

**The exception that matters:** a literal in the `WHERE` clause puts a
*value* in the SQL text. `WHERE salary > 180000` discloses a threshold;
`WHERE employee_name = 'Ada Lovelace'` discloses an individual. If the
pipeline emits those, the SQL echo is a value channel, not just a schema
one — and the governance gate never looks at it.

**Not measured:** how often this pipeline actually emits literal-bearing
`WHERE` clauses. That needs a sample of real generated SQL, which this
environment has no corpus of. It is the single input I would want before
choosing, because it decides whether option C is acceptable.

### The options

**A — Withhold on shares, matching artifacts.** Safest, consistent,
smallest diff: route both through the existing `_sql_for_caller`. Cost:
an anonymous viewer loses the query, which is arguably part of the point
of sharing an analysis ("here's how I got this").

**B — Allow, but strip `WHERE`-clause literals bound to restricted
columns.** Keeps the explanatory value, removes the value channel. Parse
the stored SQL with sqlglot, walk the `WHERE` predicate, and for any
comparison whose column side classifies restricted, replace the literal
with a placeholder (`WHERE salary > ?`). Costs: a new transform to
maintain and test; sqlglot must parse every stored statement (it already
must, for validation); and a parse failure has to fail closed (withhold).

**C — Allow fully, as a documented product decision.** Cheapest. Only
defensible if literal-bearing `WHERE` clauses are rare *and* the schema
names are not themselves considered sensitive.

### Recommendation

**B**, with **A** as the fallback if the literal-stripping transform
looks like more surface than it is worth. Whichever is chosen, route
**both** surfaces through one shared helper so they cannot drift again —
that drift is the actual defect here, more than either behaviour alone.

---

## O-1 — The `bandit` gate scans 12 of 41 first-party packages

### The evidence

The CI step names an explicit list:

```
bandit -r agent api config db security media_gen moderation rag search voice media embeddings
```

Unscanned and security-sensitive: `attachments/` (upload and document
parsing), `identity/` (authentication, RBAC, tokens), `onboarding/`,
`governance/`, `guardrails/`, `jobs/`, `rest/`, `mcp_client/`,
`bi_integration/`, `analytics/`, and 19 more.

Measured across all 41: **5 Medium + 17 Low** outside the gate's reach.
All five Mediums were triaged and annotated during the first pass (they
were the `onboarding/` B608 sites, verified safe — identifiers reach
those f-strings through SQLAlchemy's dialect-aware
`identifier_preparer.quote`). So widening the scope today would surface
only the 17 Lows.

### Why it is not done here

Widening changes what fails the build for **every contributor**. The 17
Lows — mostly `assert` usage in `identity/`, `mcp_client/` and
`recommendation/` — must be individually triaged and annotated *before*
the scope change lands. Writing 17 justifications I have not verified
would be worse than leaving the gap visible.

**Do not** make it pass by adding `--severity-level medium`. That would
hide exactly the class F-03 was.

### Suggested replacement scope

All first-party packages except `tests/`, `scripts/`, `eval/`, `docs/`,
`data/`, `prompts/`, `media_library/`.

---

## O-3 — Is `.secrets.baseline` stale? (needs a Linux runner)

### What is known

A full scan on this machine reported **44 tracked files with findings the
baseline does not record** — almost all `tests/*` and
`identity/migrations/versions/*` (Alembic revision hex), consistent with
drift since the baseline was generated on 2026-10-01 against the same
tool version (1.5.0, 27 plugins).

**The local reproduction is not trustworthy**, for two measured reasons:
the committed baseline uses forward-slash paths while a Windows scan
emits backslashes (the workflow's own comment flags this), and the same
`HEAD` files produced 7 findings scanned in-tree versus 0 scanned as
copies in a scratch directory. So **no claim is made about CI's real
state.**

Every finding inspected was a test fixture, a UI label, an Alembic
revision id or a redaction docstring — **no real credential**.

### Runbook (hand to whoever has the Linux runner)

```bash
# 1. From a clean checkout, on Linux.
git status --porcelain            # must be empty

# 2. Keep an untouched copy to compare against.
cp .secrets.baseline /tmp/baseline_before.json

# 3. Scan EVERY tracked file. Never a subset -- see the warning below.
detect-secrets scan $(git ls-files) --baseline .secrets.baseline

# 4. What changed?
git diff --stat .secrets.baseline
python scripts/check_secrets_baseline.py /tmp/baseline_before.json

# 5. Classify each new finding interactively.
detect-secrets audit .secrets.baseline
```

> **Warning, from a mistake already made and reverted.**
> `detect-secrets scan --baseline FILE` **rewrites FILE in place**.
> Scoping it to a subset of files deletes every entry for the files not
> scanned — this removed 4,786 lines during the first pass before being
> caught by `git diff --stat` and restored. Run it over **all** tracked
> files, and do any experimental verification against a **copy** in a
> scratch directory.

### Interpreting the audit

Classify each hit as: **test fixture** (e.g. `PASSWORD = "correcthorse
battery staple 1"`), **UI label** (a locale file's "password" key),
**Alembic revision id** (hex that is not a secret), **redaction
docstring** (`security/redaction.py` describing what it redacts), or
**real credential**.

If any hit is a real credential: rotate it at the provider first, then
purge it from git history (`git filter-repo`), then move it to a secret
manager. Do not simply baseline it.

### Keeping it honest

Add a `pre-commit` hook so the baseline cannot drift again:

```yaml
- repo: https://github.com/Yelp/detect-secrets
  rev: v1.5.0
  hooks:
    - id: detect-secrets
      args: ['--baseline', '.secrets.baseline']
```

CI already runs the blocking check (`scripts/check_secrets_baseline.py`);
the hook just moves the feedback earlier.

---

## O-5 — `CLAUDE.md` drift

Two claims in the project's own rules file are contradicted by the tools
on this checkout:

1. **§5.7 says `bandit` passes** ("4 Low / 0 Medium / 0 High … the Low
   findings are below its default threshold"). `bandit` exits **1** on any
   finding at or above its default severity level, which is `low` — so
   that premise is wrong and the gate was red. §13 of the same file says
   the opposite and is the accurate one. *(The gate itself was fixed in
   the first pass; the doc claim was not.)*
2. **§13 records 6 baseline test failures**, of which 3 are `…QueryStore`.
   This checkout produces **5**: 3 `…QueryStore` plus 2
   `TestGenerateRecommendationsNode`.

Both are load-bearing for a human decision — §5.7's claim would tell a
reader the SAST gate is protecting them when it was not. Left to the
owner because `CLAUDE.md` is the cross-cutting rules document and its own
§11 says to change it only when a rule changed.
