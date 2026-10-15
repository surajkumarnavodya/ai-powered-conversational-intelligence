# Domain analytics skills

**Scope.** The design of `domain_skills/`: what a skill is, the boundaries
it must respect, and how the next 85 are added. This document owns the
**domain-skill contract**.

It does not own domain *policy* — what may be asked, returned or
recommended in a business domain is
[`../DATA_PRIVACY_GOVERNANCE_ENGINE.md`](../DATA_PRIVACY_GOVERNANCE_ENGINE.md),
and a skill cannot change any of it.

**Date:** 2026-10-15. **Batch:** 1 of 18 domains (Sales).

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
**Sales and Marketing share `SALES_MARKETING`**. That merge predates this
package. It is reused rather than split, because two domain vocabularies
would mean two answers to "what may this question return". Skill slugs keep
the two distinguishable where it matters (`sales.*` vs a future
`marketing.*`).

`registry.covered_domains()` answers "which of the eighteen are actually
covered" from the catalog itself, so no document can claim coverage the
code does not have. **Today: Sales only.**

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

Each applied to live code, then reverted. Clean: `59 passed`.

| Mutation | Result |
|---|---|
| selection stops filtering by domain (cross-domain leak) | **1 failed**, 58 passed |
| no longer fails open when the flag is off | **1 failed**, 58 passed |
| contract gains a format slot | **2 failed**, 57 passed |
| `GENERAL` (unknown domain) gets guessed framing | **1 failed**, 58 passed |

Two of those only became real after the tests were fixed, and the reason is
worth recording:

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

## 8. Adding the next batch

1. Add the domain's scope string once in `catalog.py`.
2. Add one `DomainSkill` per supplied prompt — `slug`, `title`, `domain`,
   `focus`, `dimensions`.
3. Append the new tuple to `ALL_SKILLS`.
4. Add the domain to the coverage test's expectation.

No other file changes. Validation runs at import, so a malformed skill
fails the process rather than producing a degraded prompt on some later
request.

---

## 9. Status

| Item | State |
|---|---|
| Contract, registry, composer | **Live**, off by default (`ENABLE_DOMAIN_SKILLS`) |
| Sales skills (5 of 5 supplied) | **Registered** |
| Other 17 domains | **Not registered** — a skill is added when its prompt arrives, never in anticipation |
| Wiring into a live analysis path | **Not done** — see below |

**Deliberately not wired yet.** The composer produces guidance, and nothing
calls it in a request path. The natural home is the AI Data Analyst
(`agent/analyst/`), which is the "Business Analysis → Recommendations"
stage of the supplied flow and is itself off by default — but that wiring
should land with a domain whose skills have been reviewed, not with the
first batch. This is the "built, not wired" state
[`CLAUDE.md`](../../CLAUDE.md) §1 describes and §4.7 protects: do not
"finish the wiring" as a side effect of unrelated work.
