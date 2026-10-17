# AI Capability Implementation Log

Running record of implementation batches against
[`ai-capability-implementation-plan.md`](ai-capability-implementation-plan.md).
Each entry records what was built, what was measured, and what was not.

---

## Batch 1 — Deterministic SQL explainer (catalogue row 27) · 2026-10-17

**Status: COMPLETE.** Audit status for row 27 moves Partial → Complete.
**Baseline commit:** `9bd780b`.

### What the gap was

The platform showed *what* SQL it generated and whether it validated, but
never described it. `SqlIntelligencePanel` rendered the statement type
and referenced tables; nothing said how the tables were joined, what was
filtered, what was aggregated, or at what grain.

### What was built, and why it is not an LLM call

`agent/sql_validator.py` gained `describe_sql()`, which reads the AST
`validate_sql` **already parsed**. No second parser, no model call, no
new dependency.

The deciding argument was not cost or latency, though it wins on both:
**an explanation that hallucinates a join is worse than no explanation**,
because it renders beside a real validator verdict and reads as
authoritative. A parse cannot be wrong about the statement it parsed.
Recorded as decision D-009.

### Three traps the implementation had to handle

Each was found by probing the real AST before writing the function, and
each is now pinned by a test.

1. **Aggregates double-counted.** `SUM(x)` in both `SELECT` and `HAVING`
   is one aggregate to a reader and two nodes to the parser. De-duplicated.
2. **Window `ORDER BY` misreported as the result's ordering.** Searching
   the tree finds the `ORDER BY` inside `OVER (...)` first on a statement
   that has no ordering of its own. Fixed by reading the statement's own
   `order` argument rather than searching.
3. **`UNION` reported as `DISTINCT`.** sqlglot sets `distinct` on a set
   operation to encode `UNION` rather than `UNION ALL` — a different fact
   from `SELECT DISTINCT`. Would have mislabelled every plain UNION.
   Caught by a probe after the first implementation passed its other tests.

### Three things it deliberately does not report

- **No row limit**, although the AST has one.
  `agent.nodes.validate_sql_node` writes `enforce_row_limit`'s output back
  to `state["sql"]` — verified by reading the node — so the `TOP n` a
  caller sees is **this platform's governance cap, not the query's
  intent**. Reporting it as query behaviour would present a control as
  authorship. The cap already has an owner in the result gate.
- **No business meaning.** `group_by` is the grain the statement
  declares, not "sales by region". Naming what a column means belongs to
  `semantic/`.
- **No filter values.** Column names only: a predicate can carry data
  (`WHERE email = '...'`), and this string is rendered in a UI and may be
  logged.

### Files changed

| File | Why |
|---|---|
| `agent/sql_validator.py` | `+SqlJoinFact`, `+SqlShape`, `+describe_sql()`. **Added beside `summarize_sql`, not widening it** — that function has two live callers (`api.main`, `api.artifacts`) whose tuple unpacking a changed signature would break |
| `api/schemas.py` | `+SqlJoinOut`; six optional fields on `SqlSummaryOut`, each with a default, so the frozen model stays backward compatible (`CLAUDE.md` §6.3) |
| `api/main.py` | `_sql_summary_out` populates them, inside its own `try` so an undescribable shape costs the description and never the statement type |
| `frontend/src/lib/types.ts` | `+SqlJoin`; optional fields on `SqlSummary` |
| `frontend/src/components/sql/SqlIntelligencePanel.tsx` | Six rows added to the **existing** metadata grid — no new component |
| `frontend/src/i18n/locales/*/translation.json` ×5 | 7 keys each |
| `tests/test_sql_validator.py` | `+TestDescribeSql`, 15 tests |
| `frontend/.../SqlIntelligencePanel.test.tsx` | 4 tests |

Server returns **structured facts**; the frontend renders them through
i18n. Emitting English prose server-side would have made the feature
untranslatable in a five-locale app.

### Tests executed — actual results

| Check | Result |
|---|---|
| `pytest tests/test_sql_validator.py` | **73 passed** (15 new) |
| `pytest` validator + hardening + mssql + api_ask + api_artifacts + agent_nodes + restricted_column + `tests/security/` | **868 passed** |
| `pytest` (full suite) | **7215 passed, 11 deselected** (was 7197 -- exactly the 18 new tests, no regression) |
| `npm run test` | **1150 passed, 88 files** (was 1147) |
| `npx tsc --noEmit` | Clean |
| `ruff` / `black` on changed files | Clean (2 findings of my own, both fixed — see below) |
| `npm run lint` | 2 warnings on `SqlIntelligencePanel.tsx`, **pre-existing** (same file and lines in the session baseline) |
| `npm run build` | Succeeds |
| End-to-end through `_sql_summary_out` | Correct shape; unparseable SQL and absent SQL both yield `None`, not a partial |

**Two self-inflicted lint findings, both fixed rather than suppressed:**
an unsorted import block in `api/main.py` (from my insertion), and
`B017 pytest.raises(Exception)` in my own test — replaced with an
assertion on `validate_sql(...).is_valid`, which is what the test
actually meant.

### Security and governance verification

- **`validate_sql` untouched.** `describe_sql` is a separate function; a
  test asserts the validator still rejects `DELETE` while `describe_sql`
  happily describes it — proving the helper is not, and cannot be
  mistaken for, a gate.
- **`summarize_sql` untouched**, pinned by a test, so both existing
  callers are unaffected.
- **No execution path touched.** Row limits, timeouts, restricted-column
  masking, authorization and the governed execution sequence are
  unchanged; `tests/security/` (25 files) passes.
- **No new dependency**, no new endpoint, no new permission.
- **Bounded output**: 12 items per list, 80 chars each, so one
  pathological statement cannot grow the response.

### Remaining limitations

- Describes the **final, row-limited** SQL — the statement that ran, not
  the model's first draft.
- Structural facts only, by design. "Why these tables" is the semantic
  layer's and is not attempted.
- Joins onto an unnamed derived table are skipped rather than labelled
  with a guess.
- Not verified in a browser; verified via the component's own tests and
  the real API construction path.

### Not done in this batch, deliberately

Decisions 2 (accessibility), 3 (translation), 4 (Word/Excel — deferred by
you) and 5 (certification) were kept out, per the instruction not to
combine unrelated work.

---

## Batch 2 — Accessibility assurance (catalogue row 60) · 2026-10-17

**Status: COMPLETE.** Audit status for row 60 moves Partial → Complete.
**Baseline commit:** `933cfff`.

### What the gap was

The UI had aria labels, focus management and responsive layout
throughout, and 53 of 88 test files already query by role or label — but
**nothing asserted any of it stayed true.** No automated accessibility
check existed at all.

### Existing tooling first, as instructed — and it paid

Before proposing any dependency I checked what the installed stack could
already do. **oxlint 1.82 ships a `jsx-a11y` plugin that was simply not
enabled** — the config listed only `react`, `typescript`, `oxc`.

Enabling it cost **zero new dependencies** and immediately surfaced 28
findings. So half the gap closed for free.

### Why `axe-core`, not `vitest-axe`

You approved `vitest-axe` *if justified*. It is not the better choice,
by your own criteria:

| | `vitest-axe` | `axe-core` (chosen) |
|---|---|---|
| Version | **0.1.0**, pre-1.0 | 4.14.0 |
| Last modified | **2025-01-22** (~21 months) | **2026-10-15** (2 days) |
| What it is | A thin matcher around axe-core | Deque's actual engine |

`vitest-axe` wraps this same engine. For a repository that digest-pins
container images and justifies every CVE ignore individually, depending
on the maintained first-party engine and writing the ~15-line matcher
ourselves is the cheaper long-term position. Dev dependency only;
`dependencies` is untouched.

### The static findings were mostly false positives — and the runtime engine proved it

Of the 28 jsx-a11y findings, the eight that looked like real defects
included `ShareModal`'s access-mode radios and `ui/card.tsx`'s
`CardTitle`. Rather than mass-fix, I rendered both patterns and ran axe
against them:

- **ShareModal's `<label>` wrapping an input plus nested text spans:**
  **no runtime violation.** The rule cannot see text nested two levels
  deep; the control is correctly labelled.
- **`CardTitle` (`<h3 {...props} />` taking content via `children`):**
  **no runtime violation.** A static rule cannot know children exist.
- **A genuinely empty `<h3 />`:** *is* caught (`empty-heading`) — so the
  engine is not merely permissive.

Three rules are therefore disabled in `.oxlintrc.json` with that
evidence behind them: `label-has-associated-control` and
`heading-has-content` (false positives on this codebase's legitimate
patterns, both covered properly by axe at runtime) and
`prefer-tag-over-role` (a style preference — `role="status"` is valid
ARIA, not a defect).

**13 findings remain as warnings** and are real, pre-existing issues now
*visible* rather than silently absent: `no-redundant-roles` (5),
`control-has-associated-label` (3), `no-static-element-interactions` (2),
`media-has-caption` (2), `no-autofocus` (1). They are deliberately not
fixed in this batch — they pre-date it, and absorbing them silently
would hide what enabling the plugin found. `npm run lint` still exits 0.

### Files changed

| File | Why |
|---|---|
| `frontend/.oxlintrc.json` | `jsx-a11y` plugin enabled; three rules off with the evidence above |
| `frontend/package.json`, `package-lock.json` | `axe-core@4.14.0`, **devDependencies only** |
| `frontend/src/test/axe.ts` (new) | The matcher, plus an explicit statement of what jsdom cannot check |
| `frontend/src/components/ui/accessibility.test.tsx` (new) | 8 tests across Button, Card, Dialog, ChatMessage |

### Tests executed — actual results

| Check | Result |
|---|---|
| `npx vitest run accessibility.test.tsx` | **8 passed** |
| `npm run test` | **1158 passed, 89 files** (was 1150 / 88) |
| `npx tsc --noEmit` | Clean |
| `npm run lint` | **Exit 0**; 13 a11y warnings now visible |
| `npm run build` | Succeeds |

**Non-vacuity is proven, not assumed:** one test renders an icon-only
button with no accessible name and asserts axe reports `button-name`. If
axe ever stopped running, that test fails rather than silently passing.

### Security

No production dependency added. `axe-core` is unreachable from the app
entry point — only `accessibility.test.tsx` and `src/test/axe.ts` import
it, so Vite cannot bundle it. (`frontend/dist` is read-blocked under
`CLAUDE.md` §12, so this is established by source reachability, which is
what determines bundling, rather than by grepping the artefact.)

### Remaining limitations

- **Colour contrast is not checked and cannot be**, because jsdom has no
  layout or paint. The rule is explicitly disabled rather than left to
  report a misleading pass. **This helper must not be cited as evidence
  that contrast was verified** — that needs a real browser.
- Scoped to shared primitives and the chat bubble, not whole pages.
- No formal WCAG conformance audit; this is regression protection.
- The 13 remaining warnings are a tracked backlog, not fixed here.

---

## Batch 3 — Governed-answer translation (catalogue row 79) · 2026-10-17

**Status: CORE COMPLETE, NOT WIRED.** Row 79 moves
*Needs decision / Missing* → **Core / Partial**.
**Baseline commit:** `cf0781a`.

> **No user can reach this yet.** `agent/translation.py` is built,
> tested and proven against the real model, but there is no API field
> and no UI control. It is the "built, not wired" state `CLAUDE.md` §1
> recognises, and it is recorded as Partial rather than Complete for
> exactly that reason.

### The invariant problem, and why the model is not trusted with it

Your condition was to preserve numbers, signs, units, currency, dates,
identifiers, citations, source references and SQL — and to **defer if
those invariants could not be demonstrated.**

A model asked to translate free text will eventually localise a decimal
separator, round a figure or drop a citation. No prompt makes that a
guarantee. So the model is never shown the parts that must not change:

1. **Protect** — every invariant span becomes an opaque placeholder.
2. **Translate** — only the prose between placeholders reaches the model.
3. **Restore** — spans are put back verbatim.
4. **Verify** — the multiset of spans recovered from the output must
   equal the input's. Any drift ⇒ **discard the translation, return the
   original.**

The guarantee is not "the model behaved". It is "a translation that
failed to preserve the invariants never reaches the caller".
`translate_answer` has exactly two outcomes, by construction.

### Proven live, not just against fakes

Against `llama3.1:8b` (local, no new provider, no cost), French, 32.0 s:

> *Les revenus ont atteint **$1,234.56** sur **2026-10-17**, en
> augmentation de **12.5%** par rapport à l'année précédente. La colonne
> propriétaire est **[restricted]** pour votre rôle. Voir
> **[the catalogue](https://example.test/cat)** pour la définition du
> métrique.*

All five protected spans intact; prose genuinely translated; and
**`[restricted]` was not translated** — a translated mask could read as
content rather than as a withheld value.

### Two defects the tests caught, and one they did not

Caught by tests:

- The number pattern `[\d,]*` swallowed a **sentence comma** — "In 2026,
  revenue" captured `2026,`, so the same figure in two sentences
  produced different spans and verification rejected a *faithful*
  translation. Now `(?:,\d{3})*`.
- `\s?%?` captured a trailing space unconditionally, making spans
  whitespace-sensitive. Now `(?:\s?%)?`.

**Not caught by tests, found only by the live call:** `rag.llm.call_ollama`
takes `max_tokens` as a **required positional**, which my call omitted.
Every unit test passed because the fakes had the wrong arity too. A
mocked suite cannot catch a signature mismatch with the real collaborator
— the live run is what found it, and the fakes now match the real
signature.

### Governance position

- Operates **after** the governed path, on text governance has finished
  with. It never alters a validation verdict, authorization decision,
  restricted-column mask or access-control outcome.
- `[restricted]` is a protected span, so a mask can never be translated
  away.
- No new provider, no egress, no cost — the same local Ollama.
- **Not in conflict with `agent/input_guard.py`**, which refuses a
  *user's* request to translate arbitrary text. That would route around
  the governed answer path; this is a presentation transform of our own
  governed output. Worth stating because the two look similar.

### Tests — actual results

| Check | Result |
|---|---|
| `pytest tests/test_translation.py` | **34 passed** |
| Live translation, `llama3.1:8b`, fr | Translated, all invariants intact, 32.0 s |
| `ruff` / `black` | Clean |

Coverage includes: numbers, signs, percentages, currency in three
symbols, dates, inline and fenced SQL, markdown citations, the
governance mask, a figure legitimately **moved** by word order
(accepted), and five distinct ways a figure can drift (all rejected),
plus model error, empty response, unsupported locale, and all four
non-English locales.

### Why it is not wired, and what wiring needs

**32 seconds per translation on this hardware.** That settles the design
question: translation must be **on demand**, never automatic per answer
— the same conclusion the SQL explainer reached for the same reason.

Wiring needs an optional response field or a small endpoint, an
on-demand control in the answer actions, and i18n ×5. The original must
stay available for comparison, which the two-outcome contract already
guarantees server-side.

### Remaining limitations

- Not reachable by any user.
- Identifier protection is via code spans and the number/date/currency
  rules; a bare unquoted identifier in prose (`DimProduct`) is **not**
  protected and could be translated. Wrapping identifiers in backticks
  upstream would close this; recorded rather than hidden.
- Quality of the translated prose is unmeasured — only invariant
  preservation is guaranteed.
- 4,096-token output cap; a longer answer truncates, which verification
  turns into a discarded translation rather than a wrong one.
