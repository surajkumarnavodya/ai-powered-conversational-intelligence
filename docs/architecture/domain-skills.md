# Domain analytics skills

**Scope.** The design of `domain_skills/`: what a skill is, the boundaries
it must respect, and how the next 85 are added. This document owns the
**domain-skill contract**.

It does not own domain *policy* — what may be asked, returned or
recommended in a business domain is
[`../DATA_PRIVACY_GOVERNANCE_ENGINE.md`](../DATA_PRIVACY_GOVERNANCE_ENGINE.md),
and a skill cannot change any of it.

**Date:** 2026-10-15. **Batches delivered:** 6 — Sales, MIS / Management Reporting, Marketing, HR, eCommerce, Training / L&D (across 5 of 18 governance domains).

---

## 1. What a skill is, and what it is not

A skill is a narrow analytical focus inside a business domain — "revenue
intelligence" inside Sales, "attrition analysis" inside HR. It shapes how
the platform *frames* an analysis.

**It is not a security control, and it is not a source of truth.** Three
boundaries, each enforced by structure rather than by convention:

| Boundary | Why | Enforced by |
|---|---|---|
| Skills are **in-code constants**, never database rows | [`CLAUDE.md`](../../CLAUDE.md) §5.5: a row that becomes a system prompt turns every write into prompt injection with full pipeline reach | No API route, no admin surface, no settings field can add one. An AST check asserts no module in the package imports `identity` or `sqlalchemy` |
| A skill **grants nothing** | Authorization is `agent/authz.py` + `identity/rbac.py`; what may be returned is `governance/`. None of them reads a skill | A skill naming a table or claiming a permission is simply ignored by every gate |
| A skill **fails open** | It is an accuracy aid, never a reason a question cannot be answered ([`CLAUDE.md`](../../CLAUDE.md) §4.2) | Feature off, domain uncovered, or domain unknown → nothing is appended and behaviour is byte-identical |

---

## 2. Why the supplied prompts are stored as data, not as text

The five Sales prompts supplied in batch 1 —
`01_revenue_intelligence.md` … `05_pricing_and_discount_intelligence.md` —
are **byte-identical except for their heading**. Same opening, same
"Domain scope" sentence, same Before/Analyze/Safety/Return blocks.

**Batch 2 (MIS) confirmed the pattern**: its five prompts are likewise
identical except for the heading, with their own shared scope sentence. The
structure is therefore a property of the series, not a coincidence of the
first batch — which is what justifies storing the framing once.

Storing them verbatim would mean five copies of the same thirty lines, and
ninety copies across the full series. So:

- the shared framing lives **once** in `domain_skills/contract.py`
- the domain scope lives **once per domain** in `catalog.py`
- a skill carries only what actually varies: `title`, `focus`, `dimensions`

Adding a skill is therefore a handful of lines. A test asserts the five
Sales skills still share one scope string, so a future edit that forks them
fails loudly.

---

## 3. What was deliberately *not* passed to the model

Several clauses in the supplied prompts describe controls this platform
already enforces deterministically. Restating them as model instructions
would imply the model is the control, and
[`CLAUDE.md`](../../CLAUDE.md) §4.3 is explicit that the LLM may propose but
never decides.

| Supplied clause | Actually enforced by |
|---|---|
| "Read-only SQL only" | `agent/sql_validator.py` — an AST allowlist — plus a read-only `DB_USER`, startup-enforced |
| "Protect sensitive data and enforce authorization" | `agent/authz.py`, `identity/rbac.py`, `governance/result_policy.py` |
| "Validate joins, grain, NULLs, duplicates, division by zero" | the validation and plan-conformance review steps in `agent/nodes.py`, then the result gate |
| "Never expose hidden chain-of-thought" | the response schema: only a composed answer is ever returned |

A test asserts the contract does **not** contain the phrase "read-only
SQL", so nobody can quietly re-add it and imply the model is the gate.

What remains in the contract is the part a model genuinely does: how to
frame an analysis, and what to refuse to claim. The fact/calculation/
correlation/hypothesis labelling is kept because it shapes output; the
injection-resistance clauses are kept as a cheap second line, never the
first.

### The response sections were narrowed

The supplied contract asks for `SQL` and `Validation` sections. Those are
omitted: the API response already carries the real executed statement and
the real validation outcome as structured fields. Having the model restate
them would create a second, unverified copy of a fact the API already owns
([`CLAUDE.md`](../../CLAUDE.md) §4.8).

---

## 4. Domains reuse the governance enum

A skill's domain is a `governance.domains.BusinessDomain`, not a new
vocabulary. That enum already drives the privacy policy, the
restricted-category union and the prohibited-action list, so a skill
cannot sit in a domain the governance engine has no policy for.

The supplied 18-domain list maps onto it cleanly, with one merge:
**Sales and Marketing share `SALES_MARKETING`**.

A second choice arose in batch 2: `BusinessDomain.EXECUTIVE` also exists
and fits "Executive KPI Intelligence" by name. `MIS` was used because that
is the domain the batch was supplied under, and because the choice is
**governance-neutral** — `policy_for(MIS)` and `policy_for(EXECUTIVE)` were
compared and are identical: same effective restricted categories, same
(empty) prohibited-action set, same minimum group size. Nothing is
under-protected either way. That merge predates this
package. It is reused rather than split, because two domain vocabularies
would mean two answers to "what may this question return". Skill slugs keep
the two distinguishable where it matters (`sales.*` vs a future
`marketing.*`).

`registry.covered_domains()` answers "which of the eighteen are actually
covered" from the catalog itself, so no document can claim coverage the
code does not have. **Today: Sales, MIS, Marketing, HR, eCommerce and Training / L&D — 30 skills across 5 governance domains.**

A test asserts `covered_domains()` equals exactly the delivered batches, so
a new domain fails it until this document is updated in the same change —
the coverage claim cannot drift behind the code.

---

## 5. Guidance is additive, never a prompt edit

The composed block is a **separate system message placed after the existing
one**. It never modifies `agent/llm_client.py`'s prompts, for two reasons:

1. `_INSIGHT_SYSTEM_PROMPT` and the generation prompt are benchmark-pinned
   ([`CLAUDE.md`](../../CLAUDE.md) §12) — `docs/EVALUATION.md`'s accuracy
   numbers were measured against their exact wording.
2. Additive means the default path is byte-identical.

There is also a substantive reason the insight path is the **wrong** home:
`_INSIGHT_SYSTEM_PROMPT` forbids precisely what a skill asks for — it
mandates one-to-two sentences, bans computing any new statistic, and bans
speculating about *why*. A skill wants trends, rankings, comparisons and
labelled hypotheses. The two cannot share a prompt, and the pinned one is
not the one that moves.

`tests/test_domain_skills.py::TestThePinnedPromptsAreUntouched` asserts no
skill text appears in the insight prompt, and carries a sentinel on its
pinned wording so a future relaxation fails loudly rather than silently
invalidating the accuracy figures.

---

## 6. Nothing untrusted reaches the prompt

Every line the composer emits traces to a frozen, in-code constant. No
caller text, no retrieved document, no database value, no model output.

- The contract is built by **concatenation, never `str.format`**. A format
  slot is a hole, and a hole in a system prompt is somewhere untrusted text
  could one day be passed. A test asserts the contract contains no `{`
  or `}`, and that no skill field does either.
- `compose_guidance(skill)` takes exactly one parameter, and
  `guidance_for` takes exactly three — the signature itself is the control,
  because there is nowhere to pass a question or a row.
- An explicit skill slug is honoured **only** when it is registered *and*
  belongs to the routed domain, so the skill parameter cannot pull in
  another domain's framing.

---

## 7. Mutation evidence

Each applied to live code, then reverted. Clean: `218 passed` (6 batches).
Counts grow with the catalog, which is the point — a guard that covered one
batch would not stay meaningful as domains land.

| Mutation | Result |
|---|---|
| selection stops filtering by domain (cross-domain leak) | **3 failed**, 215 passed |
| no longer fails open when the flag is off | **1 failed**, 217 passed |
| contract gains a format slot | **2 failed**, 216 passed |
| `GENERAL` (unknown domain) gets guessed framing | **1 failed**, 217 passed |
| guesses a default across prompt-domains (campaign → Revenue framing) | **2 failed**, 216 passed |
| `group` stops being derived from the slug | **36 failed**, 177 passed |
| prohibitions block dropped (HR invited to rank individuals) | **5 failed**, 213 passed |
| prohibitions hard-coded to one domain instead of derived | **5 failed**, 213 passed |
| ranking invitation becomes unqualified again | **1 failed**, 217 passed |

Three of those only became real after a vacuous test was fixed. The full
pattern is in §8; the first two are recorded here because they were found
in batch 1:

- **The cross-domain test was vacuous.** It passed a slug for an
  *unregistered* skill, so the safe and the broken implementations both
  fell through to the same default. It proved nothing. The intruder skill
  is now genuinely registered via `monkeypatch`, because only Sales has
  skills today and a second domain has to be injected to exercise the
  guard at all.
- **The `GENERAL` guard was redundant.** `GENERAL` has no declared skills,
  so deleting the guard changed nothing. It is defensive for the day
  someone declares one; the test now injects a GENERAL skill so that
  defence is real rather than theoretical.

---

## 8. What batch 3 corrected: prompt-domain is not governance-domain

Marketing shares `BusinessDomain.SALES_MARKETING` with Sales. That exposed
a modelling error in batches 1–2, which had quietly treated the two as the
same thing.

**They are not.** A governance domain answers *"what may this question
return"* — it drives the restricted-category union and the
prohibited-action list. A prompt-domain answers *"how should this analysis
be framed"*. Sales and Marketing share the first and differ completely on
the second: "commercial revenue, customers, regions, products and pricing"
versus "campaigns, channels, offers, segments and lead funnels".

Two things broke, and neither would have shown until a second prompt-domain
landed in an occupied governance domain:

| Broke | Fix |
|---|---|
| The one-scope-per-domain invariant — two legitimate scopes in one domain | `DomainSkill.group` (the slug prefix, derived not stored). The scope test now asserts one scope per **group** |
| `select_skill` returned `candidates[0]` as the domain default, so a campaign question would have been framed as **Revenue Intelligence** | With more than one group and nothing to disambiguate, selection returns **None** |

### Why refusing to guess is the right answer

Wrong framing is worse than no framing. Returning None means the analysis
proceeds with the platform's existing neutral behaviour, which is the
correct fail-open posture for an accuracy aid
([`CLAUDE.md`](../../CLAUDE.md) §4.2). An explicit skill slug still resolves
the ambiguity, and an unambiguous domain still gets its default — the
refusal is scoped to real ambiguity, not a blanket disabling.

Resolving it *automatically* needs a classifier that can tell a sales
question from a marketing one. That belongs with the wiring batch, not
here, and it is listed as outstanding rather than quietly assumed.

### Three tests went vacuous as the data grew

Worth recording, because the pattern will recur:

1. **Cross-domain (batch 1)** — passed a slug for an *unregistered* skill,
   so safe and broken implementations both fell through to the same
   default. Fixed by injecting a registered intruder.
2. **`GENERAL` guard (batch 1)** — `GENERAL` had no skills, so deleting the
   guard changed nothing. Fixed the same way.
3. **Disabled-flag (batch 3)** — asserted `guidance_for(SALES,
   enabled=False) is None`, but once SALES became ambiguous it returned
   None with the flag *on* too. Deleting the flag check changed nothing and
   the mutation survived. Fixed by asserting on an unambiguous domain *and*
   asserting the positive case.

The common shape: **a test that asserts "returns None" proves nothing
unless the same call returns something when the condition under test is
reversed.** Each was found by mutation testing, not by review.

---

## 9. What batch 4 added: prohibitions derived, not restated

HR is the first domain whose policy actively **contradicts** the shared
contract. `policy_for(HR)` prohibits five actions, including
`individual_performance_ranking` — while the contract says "rank top and
bottom performers". A skill called *Performance Intelligence* sitting in
that domain is exactly where that collision shows.

Two changes, both structural rather than editorial:

1. **The ranking invitation is qualified.** The bullet now says to rank
   "when a ranking is meaningful AND not prohibited for this domain — the
   domain constraints at the end of these instructions override this line."
2. **A constraint block is appended, derived from `policy_for(domain)`.**
   It lists that domain's prohibited actions and comes last, so it visibly
   overrides everything above it.

### Why derived rather than written per skill

The prohibitions are already data with one owner, in
`governance/domains.py`. Hand-writing them into each focus text would mean
twenty chances to forget one — and a second copy free to drift from the
policy the governance engine actually enforces. Deriving them means every
future domain gets this correct on the day its skills land, with no author
having to remember.

A test asserts no skill's focus text restates a prohibition verbatim, so
the single-owner property cannot quietly erode.

**This is defence in depth, not the control.**
`governance/request_policy.py` and `governance/recommendation_safety.py`
are what actually refuse a prohibited output. This only stops the model
being invited to produce one. A domain with no prohibitions (MIS has none)
appends nothing at all, rather than an empty heading.

### A fourth vacuous test, and a lesson about mutations

Mutation 9 — unqualifying the ranking bullet — **survived twice**:

- First because the test asked "is the phrase present anywhere in the
  contract?". It was, in the next fragment of the same sentence. Tightened
  to assert on the ranking **line** itself.
- Then because the *mutation* was unrealistic: it replaced one of three
  concatenated source fragments, leaving the rest of the sentence dangling,
  so the phrase genuinely did survive. Fixed by replacing the whole bullet.

The second case is the more interesting one. **A surviving mutation does
not always mean the test is weak — sometimes the mutation is wrong.**
Distinguishing the two is the work; assuming either without looking
produces a false conclusion in both directions.

### Supplied order is preserved

The HR prompts arrived out of sequence (01, 03, 02, 04, 05) and are
registered 01→05. That matters beyond tidiness: declaration order decides
the domain default, so the default is Workforce Intelligence (01), the
broadest of the five, rather than whichever file happened to be listed
first.

---

## 10. Batch 5 (eCommerce): the process held with no correction

Batches 3 and 4 each forced a change to the model — `group`, then derived
prohibitions. **Batch 5 needed neither.** eCommerce landed as a pure data
addition: one scope string, five focus declarations, one tuple appended,
plus the coverage assertions those batches deliberately made fail.

That is the first evidence the abstraction has settled. It also exercised
both earlier corrections without special handling:

- Its one prohibition (`sensitive_personal_targeting`) was derived and
  appended automatically — no author had to know about it.
- It occupies its own governance domain, so the ambiguity rule did not
  apply and it has a normal default.

The prompts again arrived shuffled (03, 04, 02, 01, 05) and were registered
01→05, so the default is Conversion Funnel Intelligence rather than
whichever file was listed first.

One thing worth watching: the catalog is now the only file that grows per
batch, and `test_a_prompt_domain_does_not_imply_a_governance_domain`
hard-codes the group and domain counts. That assertion is cheap to update
and failing it is the intended signal, but if it ever becomes the *only*
thing a batch changes, it has stopped earning its place.

### Batch 6 (Training / L&D) confirmed it

A second consecutive data-only batch. Training is the sharpest test the
derived-prohibitions design has had so far: it prohibits **a strict subset
of HR's** — `individual_employment_action` and
`individual_performance_ranking`, two of HR's five. A shared or cached list
would leak the other three, so a test asserts the subset relationship holds
*and* that none of HR's extra prohibitions appears in Training's guidance.

Its focus texts also carry the batch's real analytical risk, read off the
subject rather than the policy: **training impact is the skill most likely
to be misread**, because people *choose* training, so trained and untrained
groups differ before any training happens. That skill says to report the
comparison as a correlation and to claim impact only where the data shows a
genuine before-and-after on a comparable group.

---

## 11. Adding the next batch

1. Add the **prompt-domain's** scope string once in `catalog.py`. If the
   batch shares a governance domain with an existing one (as Marketing
   does with Sales), that is fine — the scope belongs to the group, not
   the domain.
2. Add one `DomainSkill` per supplied prompt — `slug`, `title`, `domain`,
   `focus`, `dimensions`. The slug prefix *is* the group, so pick it
   deliberately.
3. Append the new tuple to `ALL_SKILLS`.
4. Update the coverage test, which asserts `covered_domains()` equals
   exactly the delivered batches.
5. If the batch lands in an already-occupied governance domain, check
   whether any test asserts a default for it — that default is now
   deliberately gone.
6. **Register in the supplied numeric order**, even if the prompts arrive
   shuffled. Declaration order decides the domain default.
7. **Read `policy_for(domain)` before writing the focus texts.** If the
   domain prohibits something the shared contract invites, the constraint
   block handles it automatically — but a focus text that contradicts the
   policy is still the author's mistake to avoid.

No other file changes. Validation runs at import, so a malformed skill
fails the process rather than producing a degraded prompt on some later
request.

---

## 12. Status

| Item | State |
|---|---|
| Contract, registry, composer | **Live**, off by default (`ENABLE_DOMAIN_SKILLS`) |
| Sales skills (5 of 5 supplied) | **Registered** |
| MIS / Management Reporting skills (5 of 5 supplied) | **Registered** |
| Marketing skills (5 of 5 supplied) | **Registered** |
| HR skills (5 of 5 supplied) | **Registered** |
| eCommerce skills (5 of 5 supplied) | **Registered** |
| Training / L&D skills (5 of 5 supplied) | **Registered** |
| Other 13 domains | **Not registered** — a skill is added when its prompt arrives, never in anticipation |
| Wiring into a live analysis path | **Not done** — see below |

**Deliberately not wired yet.** The composer produces guidance, and nothing
calls it in a request path. The natural home is the AI Data Analyst
(`agent/analyst/`), which is the "Business Analysis → Recommendations"
stage of the supplied flow and is itself off by default — but that wiring
should land with a domain whose skills have been reviewed, not with the
first batch. This is the "built, not wired" state
[`CLAUDE.md`](../../CLAUDE.md) §1 describes and §4.7 protects: do not
"finish the wiring" as a side effect of unrelated work.
