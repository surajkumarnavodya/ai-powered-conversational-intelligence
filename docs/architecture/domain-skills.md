# Domain analytics skills

**Scope.** The design of `domain_skills/`: what a skill is, the boundaries
it must respect, and how the next batch is added. This document owns the
**domain-skill contract**.

It does not own domain *policy* — what may be asked, returned or
recommended in a business domain is
[`../DATA_PRIVACY_GOVERNANCE_ENGINE.md`](../DATA_PRIVACY_GOVERNANCE_ENGINE.md),
and a skill cannot change any of it.

**Date:** 2026-10-16. **Batches delivered:** 17 — Sales, MIS / Management Reporting, Marketing, HR, eCommerce, Training / L&D, Finance & Corporate Functions, BFSI, Supply Chain / Procurement & Logistics, Manufacturing & Operations, Customer Service & Support, Project & Portfolio Management, IT Operations & Service Management, Healthcare & Life Sciences, Energy, Utilities & Sustainability, Risk, Audit & Controls, Legal, Secretarial & Governance (across 16 of 18 governance domains).

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

The supplied contract asks for five headings `RESPONSE_SECTIONS` does not
carry. All five are omitted for the same reason — a fact has one owner
([`CLAUDE.md`](../../CLAUDE.md) §4.8):

| Supplied heading | Who already owns it |
|---|---|
| `SQL` | the API response's own field, carrying the statement that actually executed |
| `Validation` | the API response's own field, carrying the real validation outcome |
| `Domain` | routing, not the model — and the composer already emits it as a line of the guidance block |
| `Metrics` | `semantic/`'s `MetricDefinition`. A skill must not restate a metric (§14); inviting the model to list them would be a third copy, free to contradict the approved one |
| `Relevant Entities` | the retrieved schema and business context, which the pipeline already carries as structured data |

Only the first two were written down here until batch 16, whose supplied
prompts made the other three visible.
`test_response_sections_omit_domain_metrics_and_entities` now pins them
alongside `test_response_sections_omit_sql_and_validation`.

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
code does not have. **Today: Sales, MIS, Marketing, HR, eCommerce, Training / L&D, Finance, BFSI, Supply Chain, Manufacturing, Customer Service, Project, IT Operations, Healthcare, Energy, Risk & Audit and Legal — 85 skills across 16 governance domains.**

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

Each applied to the live catalog or to live code, then reverted. Clean:
`572 passed` (16 batches). **Batch 18's seven mutations are listed in §22
instead**, measured against its own `662 passed` baseline — appending them
here would mix two baselines in one column. Counts grow with the catalog, which is the point —
a guard that covered one batch would not stay meaningful as domains land.
Every figure below was re-measured at batch 16; none is carried forward, and
the harness asserts that each mutation actually applied, so a replacement
that silently missed is reported as NOT APPLIED rather than as a catch.

| Mutation | Result |
|---|---|
| selection stops filtering by domain — `select_skill` honours a slug registered in *any* domain (cross-domain leak) | **3 failed**, 569 passed |
| no longer fails open when the flag is off | **1 failed**, 571 passed |
| contract gains a format slot | **2 failed**, 570 passed |
| `GENERAL` (unknown domain) gets guessed framing | **1 failed**, 571 passed |
| guesses a default across prompt-domains (campaign → Revenue framing) | **2 failed**, 570 passed |
| `group` stops being derived from the slug (returns a constant) | **86 failed**, 471 passed, 15 no longer collected |
| prohibitions block dropped (HR invited to rank individuals) | **13 failed**, 559 passed |
| prohibitions hard-coded to one domain instead of derived (`policy_for(HR)`) | **24 failed**, 548 passed |
| ranking invitation becomes unqualified again | **1 failed**, 571 passed |
| BFSI lending skill loses its redirect (refusal with no alternative) | **1 failed**, 571 passed |
| BFSI skills declared under `FINANCE` (prohibitions silently halve) | **6 failed**, 566 passed |
| supply-chain supplier skill loses its accusation guard | **1 failed**, 571 passed |
| supply-chain logistics skill loses its individual-level guard | **1 failed**, 571 passed |
| supply-chain skills declared under `MANUFACTURING` (its listed neighbour) | **11 failed**, 560 passed, 1 no longer collected |
| OEE skill loses the average-of-ratios constraint | **1 failed**, 571 passed |
| OEE skill writes out the formula (a second owner for `MetricDefinition.formula`) | **2 failed**, 570 passed |
| manufacturing downtime skill loses its operator guard | **1 failed**, 571 passed |
| manufacturing skills declared under `SUPPLY_CHAIN` (the reverse copy-paste) | **11 failed**, 560 passed, 1 no longer collected |
| complaint skill loses its provenance guard (an allegation becomes a finding) | **1 failed**, 571 passed |
| contact-centre skill loses its agent-level guard | **2 failed**, 570 passed |
| customer-service skills declared under `SALES_MARKETING` (its listed neighbour) | **5 failed**, 566 passed, 1 no longer collected |
| portfolio skill reports percent-complete as measured delivery (a self-assessment becomes a `DATABASE_FACT`) | **1 failed**, 571 passed |
| risk skill reports a risk score as an outcome rather than a judgement | **1 failed**, 571 passed |
| resource-capacity skill loses its individual-utilisation guard | **1 failed**, 571 passed |
| project skills declared under `HR` (a misdeclaration that *adds* five prohibitions — see §16) | **8 failed**, 563 passed, 1 no longer collected |
| incident skill loses its configuration-provenance clause (a tool reading becomes an observation) | **1 failed**, 571 passed |
| change skill loses its linkage rule (a sequence becomes a cause) | **1 failed**, 571 passed |
| application skill loses the average-of-percentiles constraint | **1 failed**, 571 passed |
| incident skill loses its individual-assignee guard | **1 failed**, 571 passed |
| a focus text *restates* the derived prohibition (a second owner for `policy_for(IT_OPERATIONS)`) | **2 failed**, 570 passed |
| IT-ops skills declared under `PROJECT` (a misdeclaration that *drops* the one absolute prohibition — see §17) | **9 failed**, 563 passed |
| cost skill loses the tagging-coverage trap (untagged spend read as no spend) | **1 failed**, 571 passed |
| the supplied scope sentence is trimmed of its "never diagnose" clause (the catalog stops matching what was supplied) | **1 failed**, 571 passed |
| journey skill accounts for an individual patient's outcome (a clinical decision in place of a cohort description) | **1 failed**, 571 passed |
| journey skill loses the export-ceiling clause (a truncated result read as a population) | **2 failed**, 570 passed |
| quality skill loses the suppression clause (a withheld measure read as a zero) | **2 failed**, 570 passed |
| quality skill loses risk adjustment and its denominator (an unadjusted comparison ranks case mix) | **1 failed**, 571 passed |
| financial skill lets charges, payments and cost stand in for one another | **1 failed**, 571 passed |
| healthcare skills declared under `PROJECT` (drops all three prohibitions *and* the PHI restriction — see §18 on the `MIS` variant) | **9 failed**, 563 passed |
| consumption skill loses the missing-interval trap (an unread meter read as no consumption) | **1 failed**, 571 passed |
| emissions skill presents a derived figure as a measured one (the factor set and its vintage go unnamed) | **1 failed**, 571 passed |
| emissions skill treats the three scopes as additive components of one total (and loses the location/market distinction) | **1 failed**, 571 passed |
| efficiency skill reports a counterfactual saving as a metered reduction | **1 failed**, 571 passed |
| efficiency skill loses the cross-domain denominator clause (a ratio across two grains presented as a conversion) | **1 failed**, 571 passed |
| sustainability skill loses the restated-baseline clause (a change of boundary read as a reduction) | **1 failed**, 571 passed |
| sustainability skill counts a purchased certificate as energy not used | **1 failed**, 571 passed |
| energy skills declared under `MANUFACTURING` (a misdeclaration the derived block cannot see — see §19) | **10 failed**, 561 passed, 1 no longer collected |
| risk/audit skills declared under `LEGAL` (a misdeclaration that *adds* a prohibition — see §20) | **5 failed**, 567 passed |
| internal-audit skill loses its detection-dependence guard (an unaudited unit reads as a clean one) | **1 failed**, 571 passed |
| control skill loses the sample clause (a judgemental sample projected to a population) | **1 failed**, 571 passed |
| operational-risk skill loses the discovery-lag clause (an incomplete latest period read as improvement) | **1 failed**, 571 passed |
| continuity skill reports a passed exercise as demonstrated recoverability | **1 failed**, 571 passed |
| enterprise-risk skill treats an ordinal rating as arithmetic (a 12 becomes twice a 6) | **1 failed**, 571 passed |
| enterprise-risk skill loses its individual-owner guard | **1 failed**, 571 passed |
| `audit_finding` raised from `CONFIDENTIAL` to `SENSITIVE` in `governance/classification.py`, so the domain's signature restriction stops being inert — see §20 | **1 failed**, 571 passed |
| `Metrics` added back to `RESPONSE_SECTIONS` (the model invited to restate a `MetricDefinition` — see §3) | **1 failed**, 571 passed |

**Notes on reading these numbers**, each found while re-measuring the table
rather than carrying the previous figures forward. Counted in the heading
they would go stale, exactly as a count in a test name does:

- **A mutation has to be named precisely enough to reproduce.** Mutation 1
  must be applied to `composer.select_skill`. Applying the same idea to
  `registry.skills_for_domain` instead — the helper every catalog test also
  calls — yields *26 failed, 251 passed*, because it breaks registration
  assertions that have nothing to do with the cross-domain guard. The
  inflated count would look like stronger evidence while actually testing
  something else. Mutation 8's count likewise depends on **which** domain
  is hard-coded, so the row now names it. `policy_for(HR)` failed 7 at
  batch 8, **10** at batch 9, **12** at batch 10, **14** at batch 11,
  **16** at batch 12, **19** at batch 13, **20** at batch 14, **23** at
  batch 15 and **24** at batch 16, because the derived assertions grow with
  the catalog and each new covered domain catches the mutation again. Batch 13 is the first time
  they grew from a domain that *has* a prohibition rather than one that has
  none; batch 14 added only one because `HR` already prohibits
  `protected_characteristic_inference`, so hard-coding to it still renders
  one of healthcare's three. Batch 15 added **three**, which is more than
  any prohibition-bearing domain has contributed: ENERGY prohibits nothing,
  so three separate assertions check that *no* block is appended for it and
  all three fail when one is. Either variant proves the derivation is
  load-bearing; only a named one is reproducible, and only a re-measured
  one is current.
- **Mutation 6 changes how many tests exist.** The scope-once test is
  parametrized over the distinct `group` values, so collapsing `group` to a
  constant collapses 16 parameters to 1 and the total drops from 571 to
  556. The failures are real; the missing 15 were never collected, not
  silently passed.
- **Mutation 6's count also depends on *which* constant**, which batch 16
  measured rather than assumed. Returning `"sales"` fails **86**; returning
  a string that is no skill's prefix (`"constant"`) fails **93** (measured
  at the 571 baseline, before mutation 56's own test landed), because
  the five Sales skills stop matching their own slug prefix and the `sales`
  group stops resolving. Both prove the derivation is load-bearing; the
  table publishes the `"sales"` variant and names it, for the same reason
  mutation 8 names `policy_for(HR)`. The previously published figure of 88
  was measured with a third, unrecorded constant — which is the lesson
  itself.

- **A misdeclaration also shrinks a parametrization, which batch 12 made
  visible.** `ZERO_PROHIBITION_DOMAINS` is derived from
  `registry.covered_domains()`, so declaring a zero-prohibition batch under
  another domain removes it from that list and
  `test_a_domain_with_no_prohibitions_appends_nothing` loses a parameter.
  Mutations 14, 18, 21, 25 and 47 each drop one test that way. Mutation 48
  does **not**, and for a reason worth recording: `RISK_AUDIT` declares
  prohibitions, so it was never in that derived list, and `LEGAL` declares
  some too — the length is unchanged in both directions. The figures up to
  batch 11 omitted it (7 failed + 372 passed is 379, not 380, and the
  shortfall went unexplained), so the column now states it. It matters for
  the same reason as mutation 6: a test that is never collected cannot fail,
  and an unexplained one-test gap is exactly how a vacuous guard hides.
  **Mutation 31 is the exception that shows the mechanism**: it moves a
  batch into `PROJECT`, which stays covered and zero-prohibition, so the
  derived list keeps its length and nothing is dropped.

- **Pulling that thread found a fifth vacuous test — four, in fact.** Each
  zero-prohibition batch's `test_the_domain_genuinely_appends_no_constraint_block`
  looped over `skills_for_domain(...)` and asserted inside the loop. Move
  the batch to another domain and the loop has nothing to iterate, so the
  test passed while the misdeclaration it claims to catch was live. Batch
  12 added `assert len(skills) == 5` to all four. The evidence moved with
  it: mutations 14 and 18 went from 7 failures to **9**, 21 from 4 to
  **5**, and 25 from 7 to **8**. Batch 15's Energy class carries the
  assertion from the start, which is why mutation 47 fails its premise test
  instead of passing vacuously through it. This is the only vacuous test in this
  package that mutation testing found rather than review — and it was found
  only because §16's claim that "the premise test fails" was checked
  against the failure list instead of the total.

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

A fourth followed in §9 and a fifth at batch 12 (§7), the latter a
variant of the same shape: **a test that asserts inside a loop proves
nothing unless something asserts the loop is not empty.**

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

## 15. Batch 11 (Customer Service): the record versus what it claims

Customer Service is the **fourth** zero-prohibition domain, and the one
where that matters most. `policy_for(CUSTOMER_SERVICE)` declares no
`ProhibitedAction`, so the ranking invitation reaches it unnarrowed — and
one of the five supplied skills, Contact Center Intelligence, is *about*
per-agent productivity measures. Batch 9 met this risk with delivery
drivers and batch 10 with machine operators; here it is the subject of the
prompt rather than a side effect of it.

### What the deterministic controls actually cover here

Unlike `SUPPLY_CHAIN` and `MANUFACTURING`, this domain does engage the
result gate: `minimum_group_size=5`, and PII, SPI and customer data
restricted. It would be easy — and wrong — to conclude the problem is
therefore handled.

`governance/suppression.py` suppresses a **sensitive measure for a group
below the threshold**, keeping the group label and the count visible. So
twenty agents who each handled four hundred contacts are not small groups:
nothing suppresses a ranking of them, and an authorised caller sees the
measure. The deterministic controls bound *disclosure*. Whether a
per-person ranking is a sound answer to the question at all is analysis
framing, and that is the only part a skill addresses.

`test_small_group_suppression_does_not_cover_the_ranking_risk` states that
boundary as an assertion rather than a comment, so neither side is credited
with the other's work. The guard itself follows batch 8's rule that a
refusal without an alternative is not a skill — it says what to report
instead (queue, interval, channel and team) and *why* handle time is the
wrong basis for judging a person: it tracks the mix of contacts someone
received, so ranking people on it penalises whoever took the hard work.

### The new problem: a record is not its contents

Every batch so far analysed **measurements** — quantities the business
produced. A ticket or a complaint is different in kind: it is a **third
party's claim**, stored as data.

`agent.provenance.DataTruthLevel` makes the distinction precise, and
[`CLAUDE.md`](../../CLAUDE.md) §4.4 makes it mandatory. The
`DATABASE_FACT` is *that a complaint was logged, in a category, on a date*.
That the product was defective, the team at fault, or the named agent rude
is **not** a fact in the result — it is what the complaint alleges.
Collapsing the two presents an allegation as an observed fact.

Batch 8 is the precedent: "a rule firing is a control output, not a
finding." But there the signal was machine-generated. Here a **person**
made it, and frequently about another person — so a complaint restated as
a finding is simultaneously an unsupported allegation and a claim about an
identifiable individual, in a domain whose policy prohibits neither. That
is why this guard is tested on its own rather than folded in with the
measurement traps:

```
"A complaint record is evidence that a complaint was made, not that its
 content is correct ... never restate what a complaint alleges as an
 established finding against a product, a team or a named employee.
 Report an upheld or rejected outcome only where the data records one."
```

The last clause matters as much as the first: where the data *does* record
an outcome, reporting it is correct and required. The guard is against
inventing a verdict, not against reporting one.

Mutation 19 removes it and replaces it with the plausible-sounding "report
which products and teams the complaints show to be at fault" — which is
exactly the sentence a well-meaning author would write.

### Analytical constraints this batch carries

- **Count a ticket once.** Reopens, merges and splits make volume and
  resolution rate disagree unless the counting rule is stated, and
  resolution time taken from closed tickets alone leaves out the hardest
  cases, which are still open.
- **An SLA clock is a definition, not a measurement.** Calendar versus
  business hours, and whether time waiting on the customer is excluded,
  decide whether the *same* tickets pass or breach. Attainment is measured
  against the target in force when the ticket was raised, and in-flight
  tickets already past target are breaches rather than omissions.
- **Service and churn is reverse-causality bait.** They move together, and
  this data cannot establish the direction: unhappy customers contact
  support, which is not the same as support contact causing them to leave.

---

## 16. Batch 12 (Project): a measure the measured party reported

Project & Portfolio Management is the **fifth** zero-prohibition domain.
`policy_for(PROJECT)` declares no `ProhibitedAction` and
`minimum_group_size=0`, so neither the derived block nor small-group
suppression applies, while contract, employee and PII data are restricted.
Resource allocation is per-person by construction, so the guard batch 9
introduced applies for the fifth time — reused, not restated. By now that
half of the work is routine, which is the point of having a pattern.
Mutation 24 removes it anyway: *routine* and *measured* are different
claims, and only the second one is evidence.

### The distinguishing problem: who produced the number

The series has analysed three different kinds of number, and they are not
interchangeable:

| Kind | Example | What the `DATABASE_FACT` is |
|---|---|---|
| A measurement the business produced | units shipped, downtime minutes | the quantity itself |
| A record of a third party's claim (batch 11) | a complaint | that it was logged |
| **A self-assessment by the measured party** (batch 12) | percent-complete, risk score | that a figure was reported |

Percent-complete is the most consequential number in this domain and the
least observed: **the task owner enters it**, and it is the input to earned
value. A risk score is a forward-looking judgement entered by whoever owns
the risk. Neither is an observation, so neither is a fact about the world —
the fact is that somebody reported it
([`CLAUDE.md`](../../CLAUDE.md) §4.4).

This is a step beyond batch 11. There the claim came from outside and the
risk was restating it as a finding. Here the claim comes from the party
whose performance the number describes, which makes "90% complete, so the
project is nearly done" a conclusion drawn from the self-report of the
person accountable for it. The skills therefore ask for *reported progress*
rather than *measured delivery*, and for what a risk assessment says and
when it was last reviewed rather than what will happen.

Mutations 22 and 23 remove exactly those two clauses and replace them with
the sentences a well-meaning author would write — "report percent-complete
as delivery to date", and "report which risks will materialise and which
projects they will delay."

### Structural traps, not arithmetic ones

Batch 10's traps were arithmetic (a ratio averaged instead of recomposed).
This batch's are **structural** — they come from the shape of the data
rather than the shape of the formula, which is new:

- **Float belongs to a path, not a task.** Schedule data is a dependency
  graph. Slack cannot be summed, and a late task off the critical path need
  not delay anything at all. A "count of late tasks" ranking is therefore
  close to meaningless without the path.
- **Allocation is a many-to-many.** Summing allocations across projects
  counts the same person once per project, so a portfolio can appear to
  have capacity it does not have, or people booked past 100% who are not.
  It has to be reconciled to a person's own total capacity.
- **A portfolio's status is not the average of its projects'.** Status
  rolls up by a stated rule, and the count of projects behind belongs next
  to their value, since one large project outweighs many small ones.
- **Re-baselining resets variance to zero** without the finish date
  moving, so a variance needs the baseline it was measured against.

Earned value follows batch 10's rule unchanged: the skill points at the
approved definition rather than composing SPI or CPI, and the catalog-wide
`test_no_skill_writes_out_a_metric_formula` already covers the structural
half for these skills too. Reusing that assertion rather than adding a
second one is deliberate — it is the same fact with one owner.

### Mutation 25: a misdeclaration that makes the domain *stricter*

Every previous "declared under the wrong domain" mutation lost something:
BFSI under `FINANCE` silently halved the prohibitions. Mutation 25 declares
the project skills under `HR`, which **adds** five prohibitions this domain
does not have.

That is worth a row of its own because the failure is inverted. A
misdeclaration that tightens the policy looks harmless — stricter, even —
but it is still wrong: it would attach HR's employment-decision
prohibitions to portfolio reporting, and, more subtly, it would make the
guards in these focus texts appear redundant, because the derived block
would now narrow the ranking invitation that these skills exist to handle
themselves. A future reader could then delete a guard as duplicated. The
zero-prohibition pin and the premise test both fail, which is the point —
though the premise test only fails because this batch fixed it. It had
asserted inside a loop over the domain's own skills, so the misdeclaration
emptied the loop and the test passed. See §7.

---

## 17. Batch 13 (IT Operations): where the control is absolute

IT Operations & Service Management inverts batches 9–12.
`policy_for(IT_OPERATIONS)` prohibits exactly **one** action,
`credential_disclosure`, which makes it the narrowest non-empty derived
block in the catalog — and the only one whose prohibition is backed by a
control that **no role unlocks**.
`governance.classification.NEVER_DISCLOSED_CATEGORIES` holds `CREDENTIAL`
and `PAYMENT` back from every caller including an admin
([`CLAUDE.md`](../../CLAUDE.md) §5.4), and
`governance/result_policy.py` enforces it independently of permissions.

So the prohibition is **derived and never restated**, exactly as BFSI's
four are (§12). Mutation 30 writes "never disclose a credential or a
password held in resource configuration" into a focus text — the
sentence a careful author would add — and it has to fail twice over: it
is a second owner for a policy fact, and it implies the prompt is the
control when the result gate withholds the category whatever any prompt
says.

### What changes because the block is non-empty

The previous four batches were zero-prohibition domains, where the shared
contract's "rank top and bottom performers" invitation reached the model
unnarrowed and the focus texts were the only thing standing in front of
it. Here `_prohibitions_block` appends "Report in aggregate only", so the
invitation **is** overridden. `minimum_group_size=0` still applies and an
ITSM record still names the assignee of a ticket, so the incident skill
keeps the individual guard batch 9 introduced — but it is honestly
**reinforcement here, not the only line**, and
`test_the_ranking_invitation_is_narrowed_here_unlike_batches_9_to_12`
states that difference as an assertion rather than leaving it to a comment.

That test earned its keep immediately: it asserts the zero-prohibition
domains do *not* get the aggregate-only sentence, so it independently
catches mutations 14 and 18, both of which collide two prompt-domains in
one governance domain and leave the receiving domain with no guidance at
all. Both rows moved from 9 failures to 10 for that reason.

### The distinguishing problem: who configured the instrument

The provenance series now has four steps, and the fourth is this domain's:

| Kind | Example | What the `DATABASE_FACT` is |
|---|---|---|
| A measurement the business produced | units shipped | the quantity |
| A third party's claim (§15) | a complaint | that it was logged |
| A self-assessment by the measured party (§16) | percent-complete | that a figure was reported |
| **An instrument reading, configured by the measured team** (§17) | incident count, availability | what the tooling recorded under its configuration |

An incident count is partly a property of the alerting threshold and the
deduplication rule, so a fall in volume can be a configuration change
rather than a quieter estate. Availability is partly a property of the
exclusion windows: planned-maintenance treatment alone decides whether the
same outage breaches. Neither number is wrong, but neither is an
observation of the world independent of the team whose performance it
describes — which is what makes it the step after batch 12 rather than a
repeat of it. Mutations 26 and 32 remove exactly those two clauses.

### Traps read off the subject matter

- **A percentile is not a mean.** It cannot be averaged across windows or
  instances, only recomputed from the underlying observations. This is
  batch 10's average-of-ratios error in a different costume, and like that
  one it survives a correct, validated SQL statement (mutation 28).
- **Unmonitored reads as healthy.** A component outside monitoring
  contributes nothing to an error rate, so coverage has to be stated or
  the gap looks like good news.
- **A change followed by an incident is a sequence.** Change-failure rate
  needs its linkage rule named, because a time-window linkage
  *manufactures* the relationship it then measures (mutation 27). This is
  the CORRELATION/cause line, in the place in the estate most likely to
  cross it.
- **Untagged spend is not absent spend.** It lands wherever the default
  sends it and quietly flatters every allocated figure.
- **A dependency chain is as available as its weakest required link**, so
  a service's health is not the average of its components'.

### The blind spot this batch states rather than closes

The result gate classifies the **returned column names**
([`CLAUDE.md`](../../CLAUDE.md) §5.4), so it withholds a column *named*
like a secret. A secret pasted into a free-text `message` column or a
configuration-value row is not caught by that name inference, and
`security/injection_patterns.py` is detection rather than a boundary
(§5.5). The cost skill therefore asks for resource counts, classes and
cost rather than configuration or payload bodies.

**That is framing, not a control, and the test says so in its docstring.**
It does not close the gap; it stops the model being invited into it. The
honest statement of the residual risk belongs here rather than in a
reassuring sentence somewhere else: a column-name classifier cannot see
inside a text blob, and nothing in this batch changes that.

### Mutation 31: the mirror of mutation 25

Batch 12's misdeclaration made a domain *stricter* by adding prohibitions
it did not have. Mutation 31 declares the IT-ops skills under `PROJECT`,
its listed neighbour, and does the opposite: it **drops** the one
prohibition this domain has, and with it the entire derived block — the
absolute one, the one no role unlocks. Nine tests fail, and two of them
are worth naming:

- `TestProjectCarriesItsOwnGuards::test_the_domain_genuinely_appends_no_constraint_block`
  fails because batch 12 added `assert len(skills) == 5` to it. Without
  that fix it would have passed with ten skills in the domain. The
  de-vacuuming is load-bearing for a batch that did not exist when it was
  written, which is the best evidence it was worth doing.
- `test_a_domain_with_no_prohibitions_appends_nothing[project]` fails
  because `PROJECT` would then hold two prompt-domains, so selection
  refuses to guess and returns no guidance at all.

---

## 18. Batch 14 (Healthcare): when the supplied scope carries a prohibition

Healthcare & Life Sciences is the most tightly governed domain covered so
far. `policy_for(HEALTHCARE)` declares three `ProhibitedAction`s —
clinical decision, medical diagnosis or prescription, protected
characteristic inference — restricts PHI on top of the personal
baseline, and sets `aggregation_required=True`, `minimum_group_size=5` and
an `export_row_limit` of **50**: half the next tightest among covered
domains, and the lowest in the registry alongside `LEGAL` and
`RISK_AUDIT`.

### The supplied scope sentence carries its own prohibition

Thirteen batches of supplied prompts have had a purely descriptive "Domain
scope:" sentence. This one ends **"; never diagnose"**, which overlaps
`MEDICAL_DIAGNOSIS_OR_PRESCRIPTION` — a prohibition the composer already
derives from the policy.

It is stored **verbatim anyway**. The alternative was considered and
rejected, and it is recorded here so a later reader does not "fix" it:

| Option | Why not |
|---|---|
| Trim the clause, so the scope line does not overlap the derived block | `DomainSkill.scope` is documented as the supplied sentence verbatim. Editing it would make the catalog misrepresent what was supplied, which is a provenance failure — the thing this series keeps finding to matter most |
| Keep it, and drop the derived prohibition for this domain | Never. The policy is the owner, and a skill cannot change governance |
| **Keep it verbatim, pin the policy** | Chosen. The derived block stays the owner and is appended **last**, so it overrides; the scope line is quoted input |

The rule being respected is narrower than "do not mention the territory".
A second **owner** is what drifts — a clause that asserts a policy fact in
the project's own voice and can then disagree with the policy. A quoted
supplied sentence is not that, and the derived block still has the last
word.

One real risk remains: if the policy ever dropped that prohibition, the
quoted sentence would go on asserting it. That drift is in the safe
direction, but it would be **silent**, so
`test_the_three_prohibitions_are_pinned_so_the_scope_overlap_cannot_go_stale`
asserts the prohibition is present. That assertion is the entire
justification for storing the sentence unedited; mutation 33 trims the
clause and mutation 39 removes the policy, and each has to fail.

### The rule's boundary, found by writing the assertion too broadly

Extending the catalog-wide `test_no_skill_hand_writes_its_prohibitions`
for this batch, the first version asserted `"protected characteristic" not
in focus`. It failed — on `marketing.segmentation` (batch 3) and
`hr.recruitment` (batch 4), which say "never infer a protected
characteristic from a name, a source or an outcome".

Those are not restatements. They are the **redirect** pattern §12
established: name the mechanism, pair it with what to analyse instead.
Outlawing them would have deleted exactly the guards this package asks
for. The assertions are therefore on the **rendered action phrase** —
what `_prohibitions_block` actually puts in the prompt — which is what
the two original literals were all along. The broad version looked
stricter and was wrong, which is the same lesson §7 records about
inflated mutation counts: stricter is not the same as correct.

### Two deterministic controls with analytical consequences

The composer puts none of `aggregation_required`, `minimum_group_size` or
`export_row_limit` in the prompt — they are enforced, not restated
(§3). But two of them change what an *analysis* may conclude, and that
is framing, so it belongs to the skills:

- **Suppression.** `governance.suppression` replaces a sensitive measure
  for a cohort below five with `SUPPRESSED_MASK`
  (`"[suppressed: group too small]"`), keeping the label and count and
  never dropping the row. The mask is self-describing, so the risk is not
  misreading one cell: it is **folding it into a total, or reading it as an
  absence of events**. A ward with no reported infections and a ward whose
  rate was withheld are different facts.
- **The export ceiling.** `governance.result_policy` truncates a result
  carrying restricted data to 50 rows — for an authorized caller too —
  and reports `export_limited:N`. The control discloses the truncation;
  whether a share, ranking or concentration claim gets computed over the
  remaining rows anyway is framing. **A truncated result is a sample, not
  a population.**

These are different controls with opposite row behaviour — suppression
never drops a row, the export ceiling is the one control that does
([`CLAUDE.md`](../../CLAUDE.md) §5.4) — so the two clauses live on
different skills, and `test_suppression_and_the_export_ceiling_are_
different_controls` asserts they have not been merged.

### The clinical line is §12's problem in a new domain

Two supplied skills — Patient Journey and Quality & Compliance — are
*about* clinical outcomes. Reporting readmission, infection or
complication rates for a cohort is healthcare operations analytics;
accounting for why one patient's outcome occurred, or what should be done
about it, is a clinical decision. So the focus texts **redirect** rather
than only refuse: "describe how a cohort moved and what the record shows".
Mutation 34 replaces that with "explain why each patient's outcome
occurred and what should be done about it".

### Traps read off the subject matter

- **Three clocks, one wait.** Referral, decision-to-treat and appointment
  give three different waiting times for the same pathway.
- **Occupancy is a census**, so it cannot be summed across days — batch
  9's inventory trap in a new domain, and an average must name the time of
  day it was taken.
- **A readmission rate is a definition**: its window, its index-admission
  rule, and whether planned follow-up admissions are counted, which
  inflates it.
- **The journey is only observed where the patient stayed.** Care received
  elsewhere is invisible, so a pathway that looks complete may be someone
  who went elsewhere. Reading that as an outcome is survivorship bias.
- **Headcount is not capacity.** A shift covered by a different grade is
  not the same shift.
- **Charges, payments and cost are three different numbers**, routinely
  substituted for one another; and cost per case is not comparable across
  units without case-mix adjustment.
- **A rate per device-day is not a rate per patient**, and an unadjusted
  outcome comparison ranks case mix rather than care.
- **Incident volume measures reporting culture as much as safety**, so a
  rise may be better reporting. This is batch 13's instrument lesson and
  batch 11's record-versus-claim lesson arriving together.

### Mutation 39, and why its first figure was not published

Declared under `MIS`, the misdeclaration failed **16** tests — the
largest count in the table — and that number is not published, because most of
it is not evidence about healthcare. `MIS` is the fixture domain several
selection tests use as their canonical unambiguous single-group domain, so
colliding a batch into it breaks tests that have nothing to do with this
policy. The same mutation into `PROJECT` or `MANUFACTURING` fails **9**,
and 9 is the published figure.

This is §7's first lesson applied to a new case: an inflated count looks
like stronger evidence while actually testing something else. It was
caught the same way — by reading the failure list instead of the total.

---

## 19. Batch 15 (Energy): when the policy says nothing at all

Energy, Utilities & Sustainability is the sixth covered domain whose
policy declares no `ProhibitedAction`. It is also the first where the
policy says nothing *distinguishing* at all: `policy_for(ENERGY)` is
**field-identical to `policy_for(MANUFACTURING)`** in everything either
the composer or the result gate reads.

| Field | `ENERGY` | `MANUFACTURING` |
|---|---|---|
| `restricted_categories` | `{PROPRIETARY}` | `{PROPRIETARY}` |
| `prohibited_actions` | none | none |
| `minimum_group_size` | 0 | 0 |
| `export_row_limit` | 500 | 500 |
| `aggregation_required` | False | False |
| `audit_required` | False | False |
| `cross_domain_allowed` | **empty** | `{SUPPLY_CHAIN}` |

`test_the_policy_is_field_identical_to_manufacturings` asserts the first
six rows, and
`test_the_governance_derived_half_of_the_prompt_is_therefore_identical`
asserts the consequence: strip each skill's own four lines out of its
composed guidance and what remains is **byte-identical** between the two
domains. Governance cannot tell an energy question from a production one,
so everything that makes an energy analysis an energy analysis is
framing. That is the plainest statement of this package's purpose the
series has produced — and of its limits, since framing is an accuracy
aid that fails open (§1).

### The one field that differs runs the wrong way

MANUFACTURING pre-authorizes a join with SUPPLY_CHAIN; ENERGY
pre-authorizes nothing. Meanwhile this domain's signature measure is an
**intensity** — consumption or emissions per unit of output, per square
metre, per unit of revenue — and every one of those denominators lives
in another domain.

`governance/request_policy.py` owns what happens to such a request: a
multi-domain question whose other domains fall outside the primary's
`cross_domain_allowed` returns `REQUIRE_AUTHORIZATION` unless the caller
holds `VIEW_RESTRICTED_COLUMNS`. **This package must not restate that**
(§3), and no energy focus text mentions authorization or permission —
asserted, not assumed. What belongs to the skill is the analytical half:
a ratio across two grains is a new metric rather than a conversion, so
the denominator's source and grain have to be named.

This is the first batch in which `cross_domain_allowed` mattered at all,
which is why §23 now says to read it.

### The provenance series reaches a number nobody measured

The catalog has been accumulating a series, each batch one step further
from an observation:

| Batch | What the number is | Example |
|---|---|---|
| 11 (Customer Service) | a record of someone else's claim | a logged complaint |
| 12 (Project) | a self-assessment by the party being measured | percent-complete |
| 13 (IT Operations) | an instrument reading, configured by the measured team | an alert count |
| **15 (Energy)** | **not measured at all** | **an emissions figure** |

An emissions figure is activity data scaled by a published emission
factor that a reporter selected, so a fall can be a factor revision
rather than a reduction. Scope 2 has two methods — location-based and
market-based — which give different answers for the same electricity and
are neither interchangeable nor averageable. Most of Scope 3 is a
supplier's own claim or an estimate derived from spend, which is batch
11's problem again, except that here it is the normal case rather than
the exception.

Nothing deterministic in this repo catches any of it, because the
arithmetic is valid either way: the SQL is correct, the join is correct,
the total is correct, and the number still is not an observation
([`CLAUDE.md`](../../CLAUDE.md) §4.4). The same error appears one skill
along as a *saving* — avoided consumption is a difference against an
adjusted baseline, so it is a counterfactual and never a metered
quantity.

### What this domain deliberately does not carry

Batches 9 to 12 each had to guard an individual, because their subjects
were drivers, operators, agents and allocated people. This domain's
subjects are meters, sites, accounts and scopes. A meter is not a person,
so there is no individual to re-identify and no guard to write, and
`test_nothing_here_carries_an_individual_level_guard` asserts that the
absence is reasoned rather than overlooked.

That is a statement about framing, not about protection. The personal
baseline is unioned into every domain regardless, so a personal column
returned by an energy question is still gated — which is why that test
reads `effective_restricted_categories` and not the declared set
([`CLAUDE.md`](../../CLAUDE.md) §5.8). What is genuinely absent is
suppression: `minimum_group_size=0`.

### Traps read off the subject matter

- **Consumption is a difference between two readings**, so a reset, a
  rollover or a meter replacement breaks the series rather than reducing
  usage — and a missing interval is unknown consumption, not zero. Batch
  9's "a missing row is not a zero" in a new shape.
- **An estimated read is a placeholder** that a later true-up reverses.
- **Demand is not energy.** A peak is an instant and cannot be summed.
- **A billing period is not a calendar month**, so two bills are
  comparable only as per-day rates.
- **A blended unit cost** mixes commodity, delivery, demand and standing
  charges with taxes and levies, so a rise in it may be a levy change
  rather than a price change.
- **Net metering can drive billed consumption to zero or below**, which
  hides the gross volume and leaves a unit rate undefined — the
  division-by-zero case the supplied Safety block names, arriving through
  the subject matter rather than through the SQL.
- **A restated baseline** turns a change of boundary into an apparent
  reduction. Acquisitions and divestitures recalculate it.
- **Intensity and absolute move independently**: intensity falls while
  the absolute figure rises whenever the denominator grows faster.
- **Coverage is part of the figure.** A site outside the reporting
  boundary reads as a clean one.
- **A purchased certificate is a contractual instrument**, never evidence
  that less energy was used on site.
- **The sum of project savings is not a site reduction** — two measures
  on the same end use claim the same unit twice.
- **An average of efficiency ratios is not the portfolio ratio** — batch
  10's OEE lesson, reused rather than rediscovered.

### Mutation 47: a misdeclaration governance cannot see

Declaring the batch under `MANUFACTURING` fails **10** tests, and the
interesting part is *which*. Not one of them is a governance assertion.
The derived block is unchanged, because both policies prohibit nothing,
so the whole of §9's machinery is blind to this mutation. What catches
it is the prompt-domain machinery §8 describes: the misdeclaration
collides two groups inside one governance domain, `select_skill` refuses
to guess, and the registration, coverage, zero-prohibition and
premise assertions fail.

That is the mirror image of the three misdeclarations before it —
mutation 25 made a domain *stricter*, 31 *dropped* an absolute control,
39 dropped three prohibitions and a restricted category — and it is the
one that most needed the batch-3 correction to exist, because governance
contributes nothing to catching it.

---

## 20. Batch 16 (Risk & Audit): when the subject matter is the prohibition

Risk, Audit & Controls is the fifteenth covered governance domain.
`policy_for(RISK_AUDIT)` declares two `ProhibitedAction`s, and unlike
every batch before it both bear on all five skills rather than on one or
two of them.

| Prohibition | Why it is new here |
|---|---|
| `liability_determination` | **Derived into a covered domain for the first time.** Only `LEGAL` and `RISK_AUDIT` declare it, and `LEGAL` has no skills |
| `unsupported_allegation` | Already carried by HR, BFSI and LEGAL — but incidental in each of them. Here it names the domain's ordinary output |

Batch 8 was the first batch whose *skills were named after* a prohibition
(§12). This is the first whose whole professional vocabulary is one. A
finding is the audit function's recorded opinion against a stated
criterion. A control rating is a conclusion drawn from a sample. A risk
score is a judgement someone entered. A loss attribution is a
classification someone chose. None of those is an established fact about
what happened or about who is answerable for it, and the gap between the
two is exactly what the two prohibitions name.

The composer derives both, so no focus text restates either — the
temptation is at its highest in this domain precisely because the subject
matter uses the same words, and
`test_no_focus_text_restates_a_derived_prohibition` pins the absence.
What the focus texts carry instead is the redirect: report what was
recorded, by whom, and against which criterion.

`fraud_accusation` is **not** derived here. It belongs to BFSI alone, and
a risk or audit question is exactly where someone would be tempted to
hand-write it into a skill. `test_the_batch_does_not_derive_a_prohibition_it_does_not_have`
makes that a failure rather than a style note, as §11 did for FINANCE.

### The claim this batch wrote and then had to retract

The domain restricts `DataCategory.AUDIT`, which **no other domain
restricts**, and which names its own subject matter:
`governance.classification`'s `audit_finding` rule matches
`audit_finding`, `control_weakness`, `risk_assessment`,
`remediation_plan` and `non_conformance`. Every covered domain so far
restricted a side-channel — PII in manufacturing, PHI in healthcare —
so "this one restricts the thing it is for" looked like the batch's
headline, with the registry's tightest `export_row_limit` (50, shared
only with HEALTHCARE and LEGAL) as its consequence: a findings result
would routinely be truncated, so any share or ranking over it would
describe the truncation.

That was written into the catalog and then measured. It is wrong.

The `audit_finding` rule classifies at `DataClassification.CONFIDENTIAL`
(20). Every row-level control in the result gate keys on `SENSITIVE`
(30) or above:

| Control | Threshold | Engages for an ordinary findings result? |
|---|---|---|
| `result_policy._classification_masks` | `classification >= SENSITIVE` **and** the category is restricted by the domain | **No** |
| `result_policy._carries_restricted_data` → the `export_row_limit` of 50 | same | **No** |
| `suppression._is_sensitive_measure` (`minimum_group_size` 5) | measure column `>= SENSITIVE` | **No** |
| the derived prohibitions block | `policy_for(domain).prohibited_actions` | **Yes** — both, on every skill |
| the personal baseline (PII, SPI, …) | unchanged from every other domain | Yes, when personal data is present |

Measured, not reasoned:
`govern_result_for_domain(["business_unit", "audit_finding"], <60 rows>, roles, RISK_AUDIT)`
returns `actions=()` and all 60 rows for `viewer`, `analyst` and `admin`
alike. Replace `business_unit` with `employee_name` and the same call
returns `export_limited:10` and 50 rows. The ceiling is real; what makes
it fire is personal data, not audit data.

**This is deliberate upstream and is not something this package should
"fix".** `BASELINE_RESTRICTED_CATEGORIES` excludes the commercially
confidential categories on purpose ("forcing them everywhere would block
ordinary analytics in domains that have no such data"), and
`_carries_restricted_data` says in as many words that the ceiling exists
to stop bulk extraction of personal records rather than to cap ordinary
analytics. Over-blocking is its own failure mode
([`CLAUDE.md`](../../CLAUDE.md) §5.8). So the finding is *pinned* by
`test_that_restriction_is_inert_in_the_result_gate` rather than acted on,
and mutation 55 proves the pin is not vacuous: raise `audit_finding` to
`SENSITIVE` and the assertion fails.

The retraction sharpens the batch's thesis rather than weakening it.
§19's domain was one where governance said nothing distinguishing at all.
This is one where governance says something distinguishing and the
loudest part of it — a restricted category naming the domain's own data —
changes no rows. What governance contributes here that is *live* and
*specific* reaches the model as prompt text. Either way the framing
carries the weight, which is what this package is for.

### The distinguishing problem: a number that exists only where someone looked

The provenance series continues, and this is its fifth distinct shape:

| Batch | The number is… |
|---|---|
| 11 (Customer Service) | a record of someone else's claim |
| 12 (Project) | a self-assessment by the party being measured |
| 13 (IT Operations) | an instrument reading the measured team configured |
| 15 (Energy) | not measured at all — activity data scaled by a chosen factor |
| **16 (Risk & Audit)** | **counted only where somebody looked** |

A finding is raised where an audit went. An exception is found where a
control was tested. A loss is recorded above the threshold someone set.
The measure therefore moves with the amount of looking rather than with
the amount of exposure, and a zero is ambiguous between *clean*,
*untested* and *undetected*. Nothing deterministic in this repo can tell
those apart, because the counts are arithmetically correct in every case
([`CLAUDE.md`](../../CLAUDE.md) §4.4).

Each skill states the version of it that its own subject carries:

- **Internal audit** — a unit with no findings may be an unaudited unit;
  report finding counts against coverage of the plan.
- **Control effectiveness** — *untested is not effective*; untested
  controls are their own state and never a pass-rate denominator.
- **Operational risk** — loss data is truncated at a reporting
  threshold, so a threshold change breaks the series rather than changing
  the risk; and a rise in recorded incidents can be better reporting.
- **Business continuity** — an unmapped dependency reads as no
  dependency, so a concentration figure needs the mapping's coverage
  beside it.

That last one is batch 9's "a missing row is not a zero" reused rather
than rediscovered, as is the unaudited unit.

### Traps read off the subject matter

- **An ordinal rating is not a quantity.** Batch 12 established that a
  risk score is a judgement rather than an outcome; what is new here is
  the arithmetic. Likelihood times impact on an ordinal scale produces a
  ranking code, so a 12 is not twice a 6 and such scores must never be
  averaged or summed — count and rank them by band, naming the scale and
  its version. Residual rating is inherent rating reduced by an assumed
  control effectiveness, which makes it a judgement about a judgement: a
  fall in it can be a re-rating of the controls with no change in
  exposure.
- **A conclusion carries its sample.** A control test covers a sample
  over a period, so the sample size and whether it was statistical or
  judgemental decide what may be said: one exception in twenty-five is
  not a four per cent population rate, and a judgemental sample cannot be
  projected to the population at all. Design and operating effectiveness
  are different conclusions and must not collapse into one pass rate; an
  automated control tested once as a configuration and a manual one
  tested across occurrences are counts of different things.
- **Ageing runs on a revisable date.** An extended due date makes an
  overdue finding current with nothing remediated, so both the original
  and the current date belong in the answer. Closure is management's
  assertion unless audit validated it, so validated and self-reported
  closures are reported separately — a closure count measures closure
  activity, not risk removed.
- **The newest period of a loss series always looks best.** Occurrence,
  discovery and accounting dates give three different series, and recent
  periods are systematically understated because events are still being
  discovered and open losses still developing. Reported as improvement
  that is a false `DATABASE_FACT`. The distribution is also dominated by
  a few severe events, so a mean is uninformative: frequency and severity
  are reported separately.
- **A target is not an achievement and a rehearsal is not an event.**
  RTO and RPO are agreed in a plan; an exercise is planned, scoped and
  usually announced; a tabletop is not a failover; and a recovery time
  measured in an exercise normally starts at declaration, excluding
  detection and decision time.

### An individual-level guard, where batch 15 reasoned its absence

§19's domain needed none — a meter is not a person. This one has a risk
owner, an auditee, a control performer and a loss event's operator, and
each is a plausible thing to rank. The domain declares **no**
individual-performance prohibition, so nothing derived covers it: four of
the five skills carry the guard themselves, and
`test_the_individual_level_guard_is_present_where_there_is_an_individual`
asserts each one. The reason is the same in every case and is stated in
the text rather than left implicit — whoever maintains the register best
records the most risks, so ranking owners ranks diligence.

`cross_domain_allowed` is empty in both directions and no other domain
lists `RISK_AUDIT` either, while the domain's entire subject is other
domains' processes: a control over payroll is HR, over payments is
FINANCE, over change is IT_OPERATIONS. `governance/request_policy.py`
owns whether such a join is authorized and this package must not restate
it (§3), so no focus text mentions authorization or permission —
asserted, not assumed, exactly as §19 did when `cross_domain_allowed`
first mattered.

### Mutations 48 and 55

**Mutation 48** declares the batch under `LEGAL` — the obvious
copy-paste, since LEGAL declares both of this domain's prohibitions and
is the only other domain that declares `liability_determination`. It
fails **5** tests. The derived prohibitions block is *not* among
them in the way earlier misdeclarations were: LEGAL declares both of
RISK_AUDIT's prohibitions plus `legal_conclusion_or_advice`, so the
block grows rather than disappearing. What catches it is coverage, the
first-derivation assertion and the policy-field assertions — a fourth
distinct shape after mutation 25 (a misdeclaration that made a domain
*stricter*), 31 (one that *dropped* an absolute control) and 47 (one
governance could not see at all).

**Mutation 55** is the first mutation in this series applied to
`governance/` rather than to `domain_skills/`. Raising `audit_finding`
from `CONFIDENTIAL` to `SENSITIVE` makes the domain's signature
restriction live and fails **1** test. It exists because the inertness finding
above is a claim about *other people's code*, and a claim about code this
package does not own is exactly the kind that rots quietly. If it ever
fails, this section is what should be rewritten.

---

## 21. Batch 17 (Legal): when the prohibited output is the question

Legal, Secretarial & Governance is the sixteenth covered governance
domain. `policy_for(LEGAL)` declares three `ProhibitedAction`s, and they
are a **strict superset of batch 16's**:

| Prohibition | Also declared by |
|---|---|
| `liability_determination` | `RISK_AUDIT` (§20) |
| `unsupported_allegation` | `HR`, `BFSI`, `RISK_AUDIT` |
| `legal_conclusion_or_advice` | **nobody else** |

§10 recorded the first strict *subset* (Training inside HR). This is the
first strict superset, and it lands one batch after the set it contains,
which is why §20 and this section read as a pair.

### The milestone this closes

`legal_conclusion_or_advice` was the last of the fourteen
`ProhibitedAction`s that **no covered domain declared**. Batch 16 took
`liability_determination`; this one takes the last. Every prohibition in
the enum is now derived into at least one registered domain, so §9's
derivation is exercised across the whole enum rather than a subset of it.

`test_every_prohibited_action_is_now_derived_by_a_covered_domain` asserts
it twice over: that the union of covered domains' prohibition sets equals
`ALL_PROHIBITED_ACTIONS`, and that each one actually appears in a composed
prompt — not merely in a policy. If a new `ProhibitedAction` is added to
the enum this fails, and the right fix is a batch for whichever domain
declares it, not deleting the assertion.

### The difficulty is the overlap, not the count

In §20 the subject matter was a recorded assertion. Here the prohibited
output **is the natural question**:

- *Are we compliant?* → a conclusion in law
- *Will we win this case?* → a conclusion in law
- *Is this clause enforceable?* → a conclusion in law
- *What is our exposure?* → a conclusion in law

A skill that only refused would answer nothing, in the one domain where
almost every question lands there. That is §12's lesson at its sharpest —
a refusal without an alternative is not a skill
([`CLAUDE.md`](../../CLAUDE.md) §4.2) — so every focus text pairs the
refusal with the narrower question the data can actually answer:

| Skill | Will not say | Answers instead |
|---|---|---|
| Contract | whether a clause is enforceable | what the record holds, in which field, as of when |
| Matter | how a matter will end, or the merits | counts, types, stages and spend |
| Compliance | whether the organisation is compliant | how much of the register is attested, by whom, as of when |
| Board & secretarial | how well a director served | activity by body, committee and entity |
| Governance scorecard | whether governance is adequate or meets a code | the components beside the total |

`test_every_skill_redirects_rather_than_only_refusing` asserts both halves
for all five, so a future edit cannot quietly drop the redirect and leave
a refusal behind.

### Two measured findings, both of which contradicted the first draft

§23's checklist step — measure a governance claim before writing it down —
was added by batch 16 after it published a claim the code did not support.
It earned its place twice here.

**One: the domain's two declared categories behave oppositely.** The
first draft assumed both would be inert, as batch 16 found `AUDIT` to be.
They are not:

| Declared category | Classifier rule | Level | Effect at the row level |
|---|---|---|---|
| `LEGAL` | `legal_privileged` | **`HIGHLY_SENSITIVE`** (40) | **Live** — masked without `VIEW_RESTRICTED_COLUMNS`, *and* trips the `export_row_limit` of 50 for every caller including an admin |
| `CONTRACT` | `contract_terms` | `CONFIDENTIAL` (20) | **Inert** — below the `SENSITIVE` threshold every row-level control keys on |

Measured: a 60-row `(matter_id, litigation_status)` result returns
`masked_classified_columns:litigation_status` plus `export_limited:10` at
50 rows for a viewer, and `export_limited:10` at 50 rows for an admin. A
60-row `(contract_id, contract_value)` result returns untouched, both
roles.

So this is the first covered domain where a declared restriction is
genuinely load-bearing at the row level — and the consequence is
asymmetric *within* one domain. The matter skill therefore carries the
truncation clause and the contract skill deliberately does not:
`test_only_the_matter_skill_states_the_truncation_consequence` asserts the
absence as well as the presence, because a guard for a control that does
not fire there would be noise, and §4.8's one-owner rule cuts both ways.

**Two: a declared `minimum_group_size=0` is not a real zero.** This
package had been wrong about it in three places.

`governance.domains.minimum_group_size_for` is:

```python
return policy.minimum_group_size if policy.minimum_group_size else configured_default
```

A declared `0` is falsy, so it is treated as *unset* and the operator's
`GOVERNANCE_MIN_GROUP_SIZE` applies — and that setting defaults to **5**.
The function's own docstring says the opposite: "A domain declaring `0`
means 'no individuals to protect here' and is honored as a real zero, not
treated as unset." **The docstring and the implementation contradict each
other, in `governance/`.** That is reported rather than fixed from this
package: changing it would alter suppression behaviour for the six domains
that declare `0`, which needs its own change and its own tests
([`CLAUDE.md`](../../CLAUDE.md) §4.7).

What this package owed was to stop repeating the wrong reading. Three
comments in `catalog.py` — on PROJECT, IT Operations and ENERGY — said a
declared zero meant no small-group suppression. All three are corrected in
place, each saying what was measured rather than quietly dropping the
claim ([`CLAUDE.md`](../../CLAUDE.md) §6.4).
`test_a_declared_zero_group_size_is_not_a_real_zero` pins the behaviour,
and is written so that **if `governance/` is later fixed to honour the
zero, the test fails and those comments become true again** — which is the
right way round.

The narrower claim that does stand is why the scorecard skill carries its
own guard: suppression only reaches a measure classified `SENSITIVE` or
above, and a board-composition count is an ordinary integer. Measured,
`(director_category, director_count)` with a single-director category is
returned intact at k=5. A board is about ten people, so a one-person cell
describes an identifiable person and nothing deterministic will hide it.

### Traps read off the subject matter

- **A contract value is a ceiling.** A maximum or not-to-exceed figure
  authorises; it does not commit. Summed as revenue or cost it is the
  domain's most common arithmetic error, and a total across contracts of
  different terms is not comparable without the term or an annualised
  value.
- **An expiry date is not the date that governs.** An auto-renewing
  contract does not expire, so the notice deadline is the real deadline —
  and effective, signature and commencement are three different dates.
- **Clause coverage reports what was extracted.** An untagged clause reads
  as an absent one: batch 9's "a missing row is not a zero" again, reused
  rather than rediscovered, as is the un-normalised counterparty name that
  splits one party across rows and understates concentration.
- **A reserve is an estimate that looks like money.** It is set under a
  policy, so a movement can be a re-estimation rather than an event, and
  it is neither a prediction of loss nor an admission. Outcome codes are
  not a win rate: a settlement is not a defeat and a dismissal may be
  procedural.
- **Matter data lags twice.** Ageing runs from when the legal team learned
  of a matter, not when the events happened, so it measures the process;
  and outside-counsel invoices arrive late, so recent spend understates.
  Same shape as §20's discovery lag, two different causes in one skill.
- **A completion rate is not compliance.** It counts attestations
  recorded, which is evidence a process ran. The denominator is whoever
  was scoped in, so anyone outside the scope reads as attested; and an
  approved exception is a permitted deviation, never to be added to
  failures.
- **Attendance is presence, not participation**, and a proxy is a
  different fact. Quorum is defined per body, so a meeting can be quorate
  with few present; filing timeliness across jurisdictions compares
  different statutory clocks; and the entity register holds dormant and
  struck-off entities that inflate any count.
- **A scorecard total moves with its weighting.** It is a composite of
  indicators someone selected, so the components belong beside the total,
  with the framework and version named. Independence is a definition that
  differs by code; board size and committee count describe form, not
  function; a benchmark needs its peer set named.

### The guard that is this batch's own work

Privilege. Nothing derived covers it: the three prohibitions are about
*conclusions*, while privilege is about *content*. The result gate
protects a column classified `legal`, but not a free-text summary the
model might compose from one, so the matter skill states it directly —
report counts, types, stages and spend, never the substance of a matter or
the strategy in it. `test_the_privilege_guard_is_this_batchs_own_work`
asserts both the clause and that no `ProhibitedAction` mentions privilege.

### Mutations

Full figures in §7. The two worth naming:

- **Declaring the batch under `RISK_AUDIT`** is the copy-paste this
  domain invites, because that domain's prohibitions are a subset of this
  one's. It fails **10** tests, and the list was read rather than
  predicted — a first draft of this paragraph claimed the derived block
  was not involved, which the failure list disproved. Two of the three
  prohibitions survive the move, so the block does not vanish; it loses
  exactly `legal_conclusion_or_advice`, and that is enough to fail both
  the three-prohibition assertion and the milestone one. The rest are
  coverage, the two registration assertions, the prompt-domain count,
  the redirect assertion, §20's "does not derive a prohibition it does
  not have" (it now does), and §20's own non-vacuity guard
  `len(skills) == 5`, which fires because the two batches collide in one
  governance domain. That makes it a fifth distinct misdeclaration shape
  after §16's, §17's, §18's and §19's: the first where the moved batch
  lands on a *subset* of its own prohibitions, so the block is degraded
  rather than emptied or grown.
- **Making `minimum_group_size_for` honour a declared zero** is the
  second mutation in this series applied to `governance/` rather than to
  `domain_skills/`. It fails the pin above, which is the point: the
  contradiction is recorded as a *measured current behaviour*, not as an
  opinion about which side is right.

---

## 22. Batch 18 (Real Estate & Facilities): when the asset is the identifier

The first covered domain whose subject is a **building** rather than a
person, a process or a financial record — and the domain where that turns
out to decide which controls fire and which do not.

`policy_for(REAL_ESTATE)` declares no `ProhibitedAction`, so this is the
seventh zero-prohibition domain and the shared contract's "rank top and
bottom performers" invitation reaches it unqualified. Unlike batch 15's
ENERGY, the policy is **not** field-identical to any other domain's: the
personal baseline plus `CONTRACT`, a declared `minimum_group_size` of 5,
and an `export_row_limit` of 500. Nothing else in the registry matches that
combination, so none of batch 15's "framing is the only differentiator"
argument transfers.

### Four findings, measured before the focus texts were written

§23's checklist steps 9 and 10 exist because two batches running published
a claim that was true of the policy and false of the pipeline. All four
below were measured with `classify_field` and `govern_result_for_domain`,
and three of them are the opposite of the tidy story.

**1. A property's address is classified as personal data.**
`classify_field("properties", "property_address")` returns
`DataCategory.PII` at `SENSITIVE` with `MaskingStrategy.MASK`. That is not
a defect: `governance/classification.py`'s `address` rule is documented as
deliberately unbounded — it has to be, to catch `StreetAddress` and
`postal_code` alike — and it cannot tell a building's location from
somebody's home. Failing in that direction is correct.

What is new is the consequence. In every other covered domain an address is
incidental to the question; here it **is** the asset's own identifier.
Measured: 60 rows of `(property_address, occupancy_rate)` come back with
every address `[restricted]` for a caller who may not see it — so every row
now carries the same label and the rows are indistinguishable — and in full
for an admin. `property_id`, `property_name`, `city` and
`gross_floor_area` are unrestricted, so the guard the portfolio skill
carries is actionable rather than a dead end: label and group by
identifier or name, never read two masked rows as one property, and never
report a masked value as a missing one.

**2. That same column is the only thing that opens the export ceiling.**
`result_policy._carries_restricted_data` engages the ceiling only when a
column classifies at `SENSITIVE` *and* falls in a category the domain
restricts — deliberately, because over-blocking ordinary aggregates is its
own failure mode. Measured:

| Result | Viewer | Admin |
|---|---|---|
| 600 × `(property_address, occupancy_rate)` | masked, `export_limited:100`, 500 rows | `export_limited:100`, 500 rows |
| 600 × `(property_id, occupancy_rate)` | untouched, 600 rows | untouched, 600 rows |
| 600 × `(work_order_id, repair_cost)` | untouched, 600 rows | untouched, 600 rows |

So the declared 500 is real but narrow: selecting the address is what
truncates a portfolio listing, and nothing else a property analysis
naturally selects does. It applies to an admin too, because holding the
permission means a value may be seen, not extracted in bulk. Only the
portfolio skill carries the truncation clause — a guard for a control that
does not fire is noise, and [`CLAUDE.md`](../../CLAUDE.md) §4.8's
one-owner rule cuts both ways. Batch 17 made the same split between its
matter and contract skills.

**3. `CONTRACT`, the domain's one non-baseline category, is inert.**
`classify_field("leases", "lease_amount")` is `CONTRACT` at
`CONFIDENTIAL`, one level below the threshold every row-level control keys
on, so 600 rows of `(lease_id, lease_amount)` are returned untouched for a
viewer and an admin alike. That is the third time in the series — batch 16
found `AUDIT` inert and batch 17 found `CONTRACT` inert beside a live
`LEGAL` — but the **first time it is the domain's entire declared addition
to the baseline**. Every control that actually fires in this domain comes
from the personal baseline [`CLAUDE.md`](../../CLAUDE.md) §5.8 unions into
every policy, not from anything this policy declares.

**4. Small-grain occupancy is not suppressed.** `minimum_group_size=5` is
declared truthy here, so unlike batch 17's LEGAL it is a real 5 whatever
`GOVERNANCE_MIN_GROUP_SIZE` says — the two cases are pinned as a pair so
they cannot drift apart. It still does not reach this subject:
`suppression._is_sensitive_measure` keys on the **measure's**
classification, and an occupant count is an ordinary integer. Measured:
`(floor, occupant_count)` with a one-occupant floor is returned intact at
k=5 for a viewer and an admin. Suppression is not broken — add
`employee_name` to the same result and it fires — but occupancy is derived
from badge, booking and sensor records about identifiable people, so a
floor, a neighbourhood or a small team is not an aggregate and nothing
deterministic will hide it. That makes it the sharpest guard in the batch,
and the occupancy skill carries it: report at a grain where no cell can
stand for a single person, give the count beside every percentage, and
never describe when a named person was present.

### Where this batch's own work is

Two columns a real-estate schema certainly holds are not classified at all.
`classify_field` returns `DataCategory.NONE` at `PUBLIC` for both
`tenant_name` and `technician_name`, because the `person_name` rule matches
an enumerated list of prefixes — `employee_`, `customer_`, `patient_`,
`contact_`, `first_`, `last_` and so on — and neither is among them. The
rule is not broken: `employee_name` and `customer_name` classify as PII, so
this is a gap in the list, which the test asserts in both directions.

A residential tenant and a maintenance engineer are both identifiable
people, so the lease and maintenance skills carry a counterparty guard and
an assignee guard that **no deterministic control backs**. Measured:
`(tenant_name, annual_rent)` is returned in full to a viewer.

This is reported, not fixed from here. An operator's explicit entry in
`config/sensitive_columns.yaml` is the supported route and always wins, and
editing `governance/classification.py` is a governance change rather than a
skill one ([`CLAUDE.md`](../../CLAUDE.md) §4.7) — this package has twice
now recorded a contradiction in `governance/` rather than resolving it from
the outside (batch 17 did the same for `minimum_group_size_for`). The
measured behaviour is pinned, so the day the gap closes the test fails and
the two guards become redundant, which is the right way round.

### The denominator question, with one owner

`cross_domain_allowed` is empty in both directions, and it matters here the
way it did for batch 15: cost or occupancy per head takes its denominator
from HR, and cost per unit of output takes one from FINANCE or
MANUFACTURING. `governance/request_policy.py` owns whether such a join may
happen and this package must not restate it (§3) — only the analytical half
belongs here, and it belongs to **one** skill rather than four. It sits in
`real_estate.financial`, where ratios across grains actually appear, and
the other four are asserted not to repeat it. Occupancy's own denominators
— area, desks, seats — are in-domain, which is why that skill names those
instead.

### Traps read off the subject matter

| Skill | The trap that is its own |
|---|---|
| Portfolio | **Area is the denominator everything divides by and it has several definitions** — gross, net lettable, usable, rentable with a common-area factor — so a per-square-metre figure is undefined until the basis is named. A count depends on what someone decided counts as one property; owned and leased are different exposures; the portfolio is a stock and not a flow |
| Occupancy | **Two unrelated measures share the word**: leased share and physical utilisation. A building can be fully let and half used. Utilisation is inferred from an instrument counting a proxy — a badge records an entry, a booking an intention, a network association a device — so an uninstrumented floor reads as empty rather than unknown |
| Lease | **Rent is not one number**: headline against effective, after incentives. The date that governs the expiry profile is rarely the end date, because a break, a renewal option or a holdover each change when space returns. A weighted average unexpired term is four numbers depending on the weighting and the basis |
| Facilities maintenance | **A work-order count measures how much was raised, not how much broke** — a site with a responsive helpdesk looks worse than one where nothing is reported. Planned and reactive work are different populations. The age of open orders measures only the jobs nobody closed |
| Property financial | **A valuation is an opinion at a date produced by a method**, and an income-based one moves with its yield assumption as much as with the income, so it is never a price that could be realised. A gross service charge is not the owner's cost, so cost per occupied area moves with occupancy rather than efficiency |

**Three traps are reused rather than rediscovered**, and are named as such
so the series does not look like five new insights where it has three:

- Batch 16's "counted only where somebody looked" reappears as a reactive
  work order. The shape is identical — a number that exists only where
  somebody reported — in a subject where the reporter is a building
  occupant rather than an auditor.
- Batch 11's "a record is not its contents" reappears as a closure: a work
  order is closed when someone marks it closed, so a repeat order on the
  same asset is the better evidence that a fix held.
- Batch 10's average-of-ratios error reappears as a portfolio yield, which
  must be recomposed from the income and value totals. Batch 15 reused the
  same error for intensity, so this is its third appearance; the catalog
  records it rather than presenting it as new.

Batch 13's "never average percentiles" also carries over directly into
maintenance response times, and batch 10's rule that **a skill must not
define a metric** is tested hardest here: net operating income, yield and
weighted average unexpired term are all named metrics with approved
definitions in `semantic/`, so the financial skill defers to the approved
definition and names it rather than composing one. A test asserts no focus
text in this domain contains `=`, `/` or "divided by".

### Mutations

Measured against this batch's own baseline of **662 passed** for
`tests/test_domain_skills.py`. The table in §7 carries its figures as of
batch 16 against a 572-test suite and was not re-measured here, so these
are listed separately rather than appended to numbers they would not match.

| Mutation | Result |
|---|---|
| batch declared under `PROJECT` (the copy-paste this domain invites) | **10 failed**, 651 passed |
| occupancy skill loses its individual-level guard | **1 failed**, 661 passed |
| portfolio skill loses the masked-address guard | **1 failed**, 661 passed |
| portfolio skill loses the export-ceiling clause | **1 failed**, 661 passed |
| the cross-domain denominator clause is duplicated into the occupancy skill (a second owner) | **1 failed**, 661 passed |
| `tenant_name`/`technician_name` added to the `person_name` rule in `governance/classification.py` (the reported gap closes) | **1 failed**, 661 passed |
| the export ceiling stops checking the category in `governance/result_policy.py` (fires on every result) | **4 failed**, 658 passed |

Two are worth naming.

**Declaring the batch under `PROJECT`** is the copy-paste this domain
invites: PROJECT also prohibits nothing, also restricts `CONTRACT`, and
also carries a 500-row ceiling. It fails **10**, and the list was read
rather than predicted. It is the **sixth distinct misdeclaration shape**
after §16's, §17's, §18's, §19's and §21's, and the first where the target
domain becomes *ambiguous*: PROJECT would then hold two prompt-domains, so
`composer.select_skill` refuses to guess a default and `guidance_for`
returns `None` — which is why
`test_a_domain_with_no_prohibitions_appends_nothing[project]` and
`TestProjectCarriesItsOwnGuards`'s premise test fail alongside this
batch's. Earlier shapes emptied, grew or degraded the derived block; this
one removes the composed block altogether for the domain that absorbed the
batch. It also fails §17's "the ranking invitation is narrowed here"
assertion, for the same reason.

**Removing the category check from `_carries_restricted_data`** is the
third mutation in this series applied to `governance/` rather than to
`domain_skills/`. It fails four: this batch's two measured assertions, and
batch 16's and batch 17's inertness findings. That cross-batch spread is
the point — three batches have now measured the same threshold from
different sides, and a single change to the resolver fails all of them,
which is what makes the "declared is not reached" claim a pinned property
rather than three separate observations.

### The series is complete

Ninety skills across eighteen prompt-domains and seventeen governance
domains, five skills each except `SALES_MARKETING`, which holds two
prompt-domains and so holds ten. Ninety is the figure
[`domain_skills/types.py`](../../domain_skills/types.py) has used as its
example since batch 1, and it is now literal rather than illustrative.

`EXECUTIVE` is the only `BusinessDomain` member without skills, and it is
deliberate rather than pending: its one supplied prompt, "Executive KPI
Intelligence", is registered as `mis.executive_kpi` because that is the
domain the batch was supplied under, and the choice is governance-neutral.
That neutrality is now measured rather than claimed in a comment — the test
compares `effective_restricted_categories`, `prohibited_actions` and
`minimum_group_size` for both policies — so "seventeen of eighteen" cannot
be read as a gap somebody forgot.

What is still absent is the wiring, unchanged by this batch and deliberate
for the reason §24 gives.

---

## 23. Adding the next batch

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
8. **Read `cross_domain_allowed` too.** Batch 15 is where it first
   mattered: a domain whose signature measure needs a denominator from
   another domain is one whose skills should name that denominator's
   source and grain. The request gate decides whether the join is
   authorized, and a skill must not restate that (§3) — only the
   analytical half belongs here.
9. **Measure a governance claim before writing it down.** Batch 16 drafted
   a headline about the domain's export ceiling, ran
   `govern_result_for_domain` against a representative result, and found
   the claim false: the restricted category it rested on classifies one
   level below the threshold every row-level control uses (§20). A policy
   field being *declared* is not the same as it being *reached*. If the
   claim survives measurement, pin it with a test; if it does not, say so
   rather than quietly dropping it. Batch 17 needed the step twice (§21),
   once for the same threshold question in the other direction and once
   for `minimum_group_size`, where a declared `0` turned out not to be a
   real zero and three earlier comments in this package had said it was.
10. **A declared policy field is not the behaviour.** Read the resolver,
   not just the registry: `minimum_group_size_for` falls back to the
   operator default when a domain declares `0`, and
   `result_policy._carries_restricted_data` and
   `_classification_masks` both key on the *classification level* of the
   column rather than on the domain's declared category list. Two
   batches running have found a claim that was true of the policy and
   false of the pipeline.

No other file changes. Validation runs at import, so a malformed skill
fails the process rather than producing a degraded prompt on some later
request.

---

## 24. Status

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
| Customer Service & Support skills (5 of 5 supplied) | **Registered** |
| Project & Portfolio Management skills (5 of 5 supplied) | **Registered** |
| IT Operations & Service Management skills (5 of 5 supplied) | **Registered** |
| Healthcare & Life Sciences skills (5 of 5 supplied) | **Registered** |
| Energy, Utilities & Sustainability skills (5 of 5 supplied) | **Registered** |
| Risk, Audit & Controls skills (5 of 5 supplied) | **Registered** |
| Legal, Secretarial & Governance skills (5 of 5 supplied) | **Registered** |
| Real Estate & Facilities skills (5 of 5 supplied) | **Registered** |
| `EXECUTIVE` | **No skills, deliberately** — its one supplied prompt is registered as `mis.executive_kpi`; the two policies are measured identical in §22 |
| Wiring into a live analysis path | **Not done** — see below |

**Deliberately not wired yet.** The composer produces guidance, and nothing
calls it in a request path. The natural home is the AI Data Analyst
(`agent/analyst/`), which is the "Business Analysis → Recommendations"
stage of the supplied flow and is itself off by default — but that wiring
should land with a domain whose skills have been reviewed, not with the
first batch. This is the "built, not wired" state
[`CLAUDE.md`](../../CLAUDE.md) §1 describes and §4.7 protects: do not
"finish the wiring" as a side effect of unrelated work.
