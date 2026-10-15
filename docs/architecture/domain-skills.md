# Domain analytics skills

**Scope.** The design of `domain_skills/`: what a skill is, the boundaries
it must respect, and how the next 40 are added. This document owns the
**domain-skill contract**.

It does not own domain *policy* — what may be asked, returned or
recommended in a business domain is
[`../DATA_PRIVACY_GOVERNANCE_ENGINE.md`](../DATA_PRIVACY_GOVERNANCE_ENGINE.md),
and a skill cannot change any of it.

**Date:** 2026-10-15. **Batches delivered:** 10 — Sales, MIS / Management Reporting, Marketing, HR, eCommerce, Training / L&D, Finance & Corporate Functions, BFSI, Supply Chain / Procurement & Logistics, Manufacturing & Operations (across 9 of 18 governance domains).

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
code does not have. **Today: Sales, MIS, Marketing, HR, eCommerce, Training / L&D, Finance, BFSI, Supply Chain and Manufacturing — 50 skills across 9 governance domains.**

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

Each applied to live code, then reverted. Clean: `346 passed` (10 batches).
Counts grow with the catalog, which is the point — a guard that covered one
batch would not stay meaningful as domains land.

| Mutation | Result |
|---|---|
| selection stops filtering by domain — `select_skill` honours a slug registered in *any* domain (cross-domain leak) | **3 failed**, 343 passed |
| no longer fails open when the flag is off | **1 failed**, 345 passed |
| contract gains a format slot | **2 failed**, 344 passed |
| `GENERAL` (unknown domain) gets guessed framing | **1 failed**, 345 passed |
| guesses a default across prompt-domains (campaign → Revenue framing) | **2 failed**, 344 passed |
| `group` stops being derived from the slug (returns a constant) | **56 failed**, 281 passed, 9 no longer collected |
| prohibitions block dropped (HR invited to rank individuals) | **7 failed**, 339 passed |
| prohibitions hard-coded to one domain instead of derived (`policy_for(HR)`) | **12 failed**, 334 passed |
| ranking invitation becomes unqualified again | **1 failed**, 345 passed |
| BFSI lending skill loses its redirect (refusal with no alternative) | **1 failed**, 345 passed |
| BFSI skills declared under `FINANCE` (prohibitions silently halve) | **6 failed**, 340 passed |
| supply-chain supplier skill loses its accusation guard | **1 failed**, 345 passed |
| supply-chain logistics skill loses its individual-level guard | **1 failed**, 345 passed |
| supply-chain skills declared under `MANUFACTURING` (its listed neighbour) | **7 failed**, 338 passed (was 4 at batch 9 — see §13) |
| OEE skill loses the average-of-ratios constraint | **1 failed**, 345 passed |
| OEE skill writes out the formula (a second owner for `MetricDefinition.formula`) | **2 failed**, 344 passed |
| manufacturing downtime skill loses its operator guard | **1 failed**, 345 passed |
| manufacturing skills declared under `SUPPLY_CHAIN` (the reverse copy-paste) | **7 failed**, 338 passed |

**Two notes on reading these numbers**, both found while re-measuring the
table at batch 8 rather than carrying the previous figures forward:

- **A mutation has to be named precisely enough to reproduce.** Mutation 1
  must be applied to `composer.select_skill`. Applying the same idea to
  `registry.skills_for_domain` instead — the helper every catalog test also
  calls — yields *26 failed, 251 passed*, because it breaks registration
  assertions that have nothing to do with the cross-domain guard. The
  inflated count would look like stronger evidence while actually testing
  something else. Mutation 8's count likewise depends on **which** domain
  is hard-coded, so the row now names it. `policy_for(HR)` failed 7 at
  batch 8, **10** at batch 9 and **12** at batch 10, because the
  zero-prohibition assertion is derived over every such covered domain and
  each new one catches the mutation again. Either variant proves the
  derivation is load-bearing; only a named one is reproducible, and only a
  re-measured one is current.
- **Mutation 6 changes how many tests exist.** The scope-once test is
  parametrized over the distinct `group` values, so collapsing `group` to
  a constant collapses 10 parameters to 1 and the
  total drops from 346 to 337. The 56 failures are real; the missing 9 were
  never collected, not silently passed.

- **A mutation's meaning can change under you.** Mutation 14 failed 4 tests
  at batch 9 and fails 7 at batch 10 — not because a guard was added, but
  because `MANUFACTURING` stopped being an empty domain. The same edit now
  collides two prompt-domains in one governance domain instead of quietly
  moving one, so the ambiguity refusal fires too. Carrying the old figure
  forward would have published a number that no longer described the
  mutation.

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

## 11. Batch 7 (Finance): where a policy stops and a skill starts

Finance made explicit a line the earlier batches only implied. It prohibits
exactly **one** action, `individual_compensation_decision`. It does **not**
prohibit `unsupported_allegation`.

That matters because AR/AP analysis carries an obvious accusation risk: a
late or disputed balance is easy to report as though it were bad faith. The
governance engine will not stop that for this domain, because this domain's
policy does not list it.

So the rule is now stated rather than assumed:

| Kind of constraint | Where it lives |
|---|---|
| A **policy-backed prohibition** | Derived automatically from `policy_for(domain)`. Never restated in a skill — a test enforces that |
| An **analytical risk the policy does not cover** | The skill's own `focus` text |

The AR/AP skill therefore says a late balance is "a collections and process
matter… never as evidence of bad faith, misconduct or fraud by the
counterparty", and a test asserts that guard is present — because nothing
else would supply it.

Two tests pin the boundary from both sides: one asserts
`unsupported_allegation` does **not** appear in Finance's derived block
(it would be a fabricated prohibition), and the other asserts the AR/AP
focus carries the guard itself.

### Other analytical constraints this batch carries

All read off the subject matter, none from the policy:

- **Period basis.** A part-closed period compared against a closed one
  shows a decline that is purely an artefact of timing.
- **Budget phasing.** An evenly phased budget against lumpy actuals
  produces a variance that reverses next period.
- **Ageing buckets.** The same ledger looks very different on 30/60/90 than
  on 45/90, so the boundaries have to be stated.
- **Consolidation basis.** A group figure built on different translation
  rates is not comparable period to period.

---

## 12. Batch 8 (BFSI): when the skill is named after the prohibition

BFSI is the sharpest collision so far between what a batch asks for and
what its domain policy allows. `policy_for(BFSI)` prohibits four actions:

```
credit_or_loan_decision
fraud_accusation
individualized_financial_advice
unsupported_allegation
```

Three of the five supplied skills are named after one of them. "Loan and
Credit Intelligence" sounds like it should decide whether to lend; "Fraud
and Risk Intelligence" sounds like it should identify fraudsters;
"Collections and Delinquency Intelligence" invites both. The first,
"Banking Customer Intelligence", sits next to
`individualized_financial_advice`.

Nothing about the mechanism changed — all four are policy-backed, so
`composer._prohibitions_block` carries them automatically and no skill
restates one. What this batch had to get right is the **other half**: a
derived block says what must not be done, and if the focus text says
nothing else, the skill is a refusal with no alternative. Each of the three
is therefore redirected to the *portfolio* version of its subject, which is
both the legitimate analytical question and the one the data can actually
answer:

| Skill | Prohibited reading | What the focus text asks for instead |
|---|---|---|
| `bfsi.loan_credit` | whether an applicant should be lent to | approval/decline rates, exposure by grade, vintage performance |
| `bfsi.fraud_risk` | who committed fraud | alert volumes, hit and false-positive rates, case ageing |
| `bfsi.collections` | what kind of borrower this is | bucket distribution, roll rates, cure and recovery |

A separate test pins each redirect, and mutation 10 removes one of them:
the lending skill loses "never assesses whether any applicant or borrower
should be lent to" and becomes "a general-purpose credit skill". That fails
one test, which is the evidence the redirect is load-bearing rather than
decorative prose.

### Analytical constraints this batch carries

Read off the subject matter, not from the policy:

- **A rule firing is a control output, not a finding.** Alerts and
  confirmed cases are reported as separate measures, because collapsing
  them turns a detection rate into an accusation count.
- **Vintages must be compared at equal months on book.** A young vintage
  always looks better than a mature one; comparing them at a single date
  reads as an improvement that is purely seasoning.
- **Claims development.** A recent period's loss ratio is understated until
  claims mature, so reported-vs-incurred has to be stated.
- **Delinquency bucket boundaries.** Roll rates are meaningless without
  them.

### Why mutation 11 matters

The second new mutation declares the BFSI skills under
`BusinessDomain.FINANCE` instead — a realistic copy-paste error, since the
previous batch is Finance and the subject matter overlaps. Finance
prohibits one action; BFSI prohibits four. The mutation would therefore
**silently drop three prohibitions** while every skill still looked
correctly declared. It fails 6 tests.

---

## 13. Batch 9 (Supply Chain): when the policy prohibits nothing

Batch 9 is the exact mirror of batch 8. BFSI's difficulty was a policy
prohibiting four actions while three of its skills were *named* after one
of them. `policy_for(SUPPLY_CHAIN)` prohibits **nothing at all**:

```python
BusinessDomain.SUPPLY_CHAIN: DomainPolicy(
    restricted_categories=frozenset({CONTRACT, BANKING, PROPRIETARY}),
    minimum_group_size=0,
    export_row_limit=500,
    cross_domain_allowed=frozenset({BusinessDomain.MANUFACTURING}),
)   # no prohibited_actions
```

So `composer._prohibitions_block` returns `""` and **no constraint block is
appended** — which means the shared contract's invitation to "rank top and
bottom performers" is never overridden for this domain. Every batch from 3
to 8 had that invitation narrowed by a derived block. This one does not.

MIS is the only other zero-prohibition domain, and it was safe by subject
matter: management reporting is inherently aggregate, so there is nobody
for a ranking to be *about*. Supply chain is not safe that way:

- supplier, carrier and lane measures name **counterparties**, and
- delivery records very often identify an individual **driver or courier**.

`individual_performance_ranking` is prohibited in HR; `unsupported_allegation`
is prohibited in BFSI. Neither is prohibited here. So both risks are carried
by the skills' own focus texts — the `finance.ar_ap` precedent from batch 7,
now the established pattern for an analytical risk the policy does not
cover:

| Skill | Risk the policy does not cover | What the focus text requires |
|---|---|---|
| `supply_chain.logistics` | a "bottom 10 performers" ranking of named drivers | report carrier, lane and route performance, "never an individual's" |
| `supply_chain.supplier_spend` | a missed delivery date reported as bad faith | weak performance is "a performance fact, never evidence of bad faith or misconduct" |

**Neither focus text is the control, and the doc should not imply it is.**
Driver-level performance is `DataCategory.PERFORMANCE`, which
`BASELINE_RESTRICTED_CATEGORIES` restricts in *every* domain, so
`governance/result_policy.py` is what actually withholds those values
(`CLAUDE.md` §5.8 — always read `effective_restricted_categories`, never
`restricted_categories`, which is precisely why this domain's short list
does not loosen anything). The focus text only stops the model being
invited to ask the question.

`TestSupplyChainCarriesItsOwnGuards` pins the chain of reasoning rather
than just the strings: that the domain really appends no block, that the
ranking invitation really does reach it unqualified, and only then that each
guard is present. Its first test fails loudly if `SUPPLY_CHAIN` ever gains
a `ProhibitedAction` — at which point the derived block starts covering some
of this and these guards should be reconsidered, not silently kept.

### Analytical constraints this batch carries

Read off the subject matter, none from the policy. Each is a way a
correct-looking SQL answer is still wrong:

- **Inventory is a stock, not a flow.** A point-in-time snapshot must never
  be summed across periods, and an average has to name the snapshot dates
  it averages. This is the most common grain error in the domain.
- **On-time depends on which promise date.** Measuring against a revised or
  re-confirmed date flatters the number against the original one, so the
  basis has to be stated.
- **Spend needs one currency basis and one document basis.** Purchase
  order, invoice and payment are three different populations; mixing them
  double-counts.
- **Cycle time is censored by work in progress.** Only a completed cycle
  has a measurable duration, so a mean over closed requisitions alone is
  biased low while the slowest are still open — the same seasoning trap as
  BFSI's loan vintages, in a different subject.
- **A chain-level number hides its own cause.** Attribute a movement to a
  stage or say plainly that it cannot be attributed.

### Why mutation 14 is the interesting one

It declares the supply-chain skills under `BusinessDomain.MANUFACTURING` —
the realistic copy-paste error, since manufacturing is this domain's one
listed `cross_domain_allowed` neighbour and the subject matter adjoins it.

The BFSI equivalent (mutation 11) was caught partly because prohibitions
silently halved. **At batch 9 that signal did not exist**: `MANUFACTURING`
prohibits nothing either, so not one prohibition test noticed. What caught
it was the coverage assertion, the registration assertion, and the two new
supply-chain tests, which stopped finding their domain — 4 failures. The
lesson was that a batch landing in a zero-prohibition domain has one fewer
line of defence than previous batches did, which is why this section's
guards are tested directly rather than inferred.

**Batch 10 changed what this mutation means, and the figure moved with
it.** `MANUFACTURING` is no longer an empty domain, so the same edit no
longer quietly relocates a batch — it collides two prompt-domains inside
one governance domain, exactly as Marketing did with Sales at batch 3. The
ambiguity refusal fires as well, and the derived zero-prohibition set loses
a member, so it now fails **7** tests and collects one case fewer. The
mutation is unchanged; the codebase around it is not. This is recorded
rather than silently overwritten because it is the clearest example in this
document of why §7's figures are re-measured each batch instead of carried
forward.

Worth recording for the next batch: the misdeclaration would also have
**quietly changed what the result gate restricts**, since `SUPPLY_CHAIN`
restricts contract, banking and proprietary data while `MANUFACTURING`
restricts only proprietary. No skill test can see that, because a skill
grants nothing and reads nothing from the policy except its prohibitions.

---

## 14. Batch 10 (Manufacturing): where the metric layer stops

Batch 10 lands in `MANUFACTURING` — the domain batch 9 used as its
cautionary mutation. It has **two halves, and only one of them is new.**

### The half that is reused, not rediscovered

`policy_for(MANUFACTURING)` declares one restricted category (proprietary,
plus the unavoidable baseline) and **no `ProhibitedAction`**, making it the
third zero-prohibition domain after MIS and `SUPPLY_CHAIN`. So no
constraint block is appended and the ranking invitation is never narrowed,
and the live risk is the same shape as batch 9's drivers: **downtime and
scrap are recorded against a shift and an operator.**
`individual_performance_ranking` is prohibited in HR, not here.

So the batch-9 pattern applies unchanged — `manufacturing.downtime`
attributes to "equipment, reason and shift pattern, never to a named
operator", and `manufacturing.quality` puts a defect on "a process,
material or machine, never to the person who ran it". This is the second
piece of evidence (after batch 5) that the abstraction has settled: a
recurring problem was handled by applying the existing pattern rather than
changing the model.

The premise test is now **derived** rather than hard-coded.
`ZERO_PROHIBITION_DOMAINS` is computed from the policy, so the next batch
landing in such a domain is covered automatically — with
`test_the_zero_prohibition_set_is_exactly_the_three_known_domains` pinning
the membership, because a derived list that silently empties would make the
parametrized test *vanish* rather than fail. Mutation 18 confirms that
guard is not theoretical: it shrinks the set and the pin catches it.

### The half that is new: a skill must not define a metric

`semantic.metrics.MetricDefinition` owns a governed metric's `definition`,
`formula`, `aggregation` — its docstring names `"derived ratio"` — and
`grain`, under a `MetricStatus` and a version. OEE is exactly such a
metric, and it is the most over-defined number in this domain.

So writing `OEE = availability × performance × quality` into a focus text
would create a **second owner for a governed fact** ([`CLAUDE.md`](../../CLAUDE.md)
§4.8): an ungoverned, unversioned copy, in the one place a reviewer cannot
see it, that would silently contradict an approved definition wherever a
plant treats planned downtime or changeovers differently. The skill
therefore points *at* the definition instead of supplying one — "use the
approved metric definition rather than assuming one: whether planned
downtime, changeovers and no-demand time sit inside the denominator is a
governed choice that changes the number."

This is the same line batch 7 drew between a policy and a skill, now drawn
between the **metric layer** and a skill:

| Owner | Owns |
|---|---|
| `governance/domains.py` | what may be asked, returned, recommended |
| `semantic/metrics.py` | what a metric *is* — definition, formula, aggregation, grain |
| `domain_skills/` | how to frame an analysis, and which basis to state |

`test_no_skill_writes_out_a_metric_formula` enforces it across the whole
catalog, not just this batch, so no future skill can quietly become a
metric definition. Mutation 16 writes the formula in and fails 2 tests.

### The arithmetic error no deterministic control catches

OEE is a ratio, and the dominant failure in this domain is **averaging it**
across lines, shifts or periods instead of recomposing it from component
totals. A sum of ratios is not a ratio of sums.

This is worth stating plainly because it is *not* something the platform
can catch for the model. `agent/sql_validator.py` will happily pass
`AVG(oee)`; it is valid, read-only, single-statement SELECT. The result
gate will return it. Every deterministic control does its job, and the
number is still wrong. That is precisely the category a skill exists to
address — framing an analysis — and precisely why a skill is never
described here as a control.

### Analytical constraints this batch carries

- **Gross versus good units.** Counting reworked or scrapped pieces as
  output overstates every ratio built on top of it.
- **Rank downtime by duration and by frequency separately.** The leading
  reason by one is routinely not the leading reason by the other, and
  overlapping events on parallel equipment must not sum to more downtime
  than the window contains.
- **Name the quality denominator.** Units produced, inspected and shipped
  give three different rates, and only inspected units support a detection
  rate. First-pass yield counts a reworked unit as a failure; final yield
  does not.
- **A line's capacity is its constraint's capacity.** An average
  utilisation across work centres hides the one that governs output, and
  theoretical, demonstrated and scheduled capacity rarely agree.

---

## 15. Adding the next batch

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

## 16. Status

| Item | State |
|---|---|
| Contract, registry, composer | **Live**, off by default (`ENABLE_DOMAIN_SKILLS`) |
| Sales skills (5 of 5 supplied) | **Registered** |
| MIS / Management Reporting skills (5 of 5 supplied) | **Registered** |
| Marketing skills (5 of 5 supplied) | **Registered** |
| HR skills (5 of 5 supplied) | **Registered** |
| eCommerce skills (5 of 5 supplied) | **Registered** |
| Training / L&D skills (5 of 5 supplied) | **Registered** |
| Finance & Corporate Functions skills (5 of 5 supplied) | **Registered** |
| BFSI skills (5 of 5 supplied) | **Registered** |
| Supply Chain / Procurement & Logistics skills (5 of 5 supplied) | **Registered** |
| Manufacturing & Operations skills (5 of 5 supplied) | **Registered** |
| Other 9 domains | **Not registered** — a skill is added when its prompt arrives, never in anticipation |
| Wiring into a live analysis path | **Not done** — see below |

**Deliberately not wired yet.** The composer produces guidance, and nothing
calls it in a request path. The natural home is the AI Data Analyst
(`agent/analyst/`), which is the "Business Analysis → Recommendations"
stage of the supplied flow and is itself off by default — but that wiring
should land with a domain whose skills have been reviewed, not with the
first batch. This is the "built, not wired" state
[`CLAUDE.md`](../../CLAUDE.md) §1 describes and §4.7 protects: do not
"finish the wiring" as a side effect of unrelated work.
