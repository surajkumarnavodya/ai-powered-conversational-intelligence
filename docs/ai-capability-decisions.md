# AI Capability Decision Log

Decisions taken while auditing the 149-row activity catalogue against this
repository. Each records what was decided, why, and what would reverse it.

**Date:** 2026-10-16 · **Baseline:** `bea4a34` · **Audit:** [`ai-capability-audit.md`](ai-capability-audit.md)

---

## D-001 — The catalogue is usage history, not a specification

**Decision.** Classify all 149 rows before planning any work, and treat
only the Core rows as candidate features.

**Why.** Fifty-two percent of the rows describe activities performed *with*
an assistant rather than capabilities of this product — including rows
10–25, which describe how this repository was written, and row 99 ("gap
analysis"), of which this audit is an instance. The supplied README
authorises this explicitly: *"A category being present in the catalogue
does not authorize adding a feature that the app, provider, architecture,
or security model cannot safely support."*

**Consequence.** 78 rows out of scope, 14 optional, 1 needs decision, 56
Core.

**Reversal.** If you want any excluded row built, say which — several are
feasible; the objection is product fit and safety, not difficulty.

---

## D-002 — No free-text writing assistant (rows 69–78)

**Decision.** Do not add rephrasing, proofreading, email/resume/bio/SEO
writing.

**Why.** Two reasons, and the first is not about taste. Every answer this
platform produces travels through the governed path — request policy,
result governance, provenance, restricted-column masking. **A free-text
writing surface routes around all of it**: it accepts arbitrary text,
returns arbitrary text, and has no result to govern. It is also a
data-exfiltration surface — paste a governed result in, get it back
unrestricted. Second, it is a different product.

**Reversal.** Would require a governance design for ungrounded generation,
which does not exist today.

---

## D-003 — No career, education, consumer-advisory or PM features (rows 103–140, minus Core)

**Decision.** Out of scope.

**Why.** Interview prep, ATS resume scoring, tutoring, hardware advice,
land-record terminology and PMO reporting belong to a personal assistant.
Rows 103–111 specifically would introduce **PII processing** (resumes,
career profiles) into a platform whose `governance/` engine exists to
restrict personal data, with no offsetting business purpose.

**Reversal.** None recommended.

---

## D-004 — Video generation is Complete, not absent

**Decision.** Record row 86 as Complete-but-gated.

**Why.** The package README warned not to claim video support without
evidence. I checked in both directions and the evidence exists:
`media_gen/video.py`, `generation_node`'s `_VIDEO_INTENT_KEYWORDS` routing,
and `Settings.media_gen_video_duration_seconds` (whose docstring records a
live verification against the provider's `text_to_video` model list).

**Caveat recorded with it.** Off by default (`ENABLE_MEDIA_GENERATION=false`),
**paid external provider**, cost per call materially higher than an image,
human-approval-gated, served under an opaque id rather than the provider
CDN URL. Enabling it is a cost and data-egress decision for you, not a
code change.

---

## D-005 — Three partials stay partial, by design

**Decision.** No work on H (prompt hot-swap, row 41), L (ETL/transformation,
rows 32–33), M (repository inspection, row 147).

**Why.**

- **H** — making a database row the live system prompt would turn every
  write into prompt injection with full pipeline reach (`CLAUDE.md` §5.5),
  pinned by `tests/security/test_prompt_registry_cannot_weaken_security.py`.
  The registry's approve/deploy/rollback *without* runtime hot-swap is the
  correct implementation, not an incomplete one.
- **L** — this app is a read-only client of the operator's database.
  Transformation would breach the layered read-only guarantee (validator
  *plus* a read-only `DB_USER`).
- **M** — a codebase-inspection feature inside a data platform is a
  confused-deputy risk.

They are listed as partials in the matrix for honesty; the plan proposes
nothing for them.

---

## D-006 — P0 is empty, deliberately

**Decision.** Record no P0 finding.

**Why.** P0 means a critical security issue, data-integrity problem, or
major failure of existing core functionality. None was found: 7,197
backend and 1,147 frontend tests pass at `bea4a34`, the SQL boundary is
covered by 49 validator tests and 25 adversarial files, and tenant
isolation is enforced before authentication. Manufacturing a P0 to look
rigorous is the failure mode the brief names.

**Not a claim of safety.** It is a claim about *this* audit's scope —
static tracing plus the existing suites. It is not a penetration test. The
parked certification work (prompts 53–60) is where measured security
evidence belongs.

---

## D-007 — Assurance gaps belong to the certification track

**Decision.** Run load/capacity (row 59) and DAST (row 49) under the
parked prompts 53–60, not here.

**Why.** [`certification/2026-10-16_phase0_assessment.md`](certification/2026-10-16_phase0_assessment.md)
already planned both, with the same blockers (Docker daemon down; no
deployed target). Running them twice would give two owners for one
measurement.

**Exception.** Accessibility (row 60) is actionable today with the
existing vitest suite and is kept in this plan as Batch 2C.

---

## D-009 — SQL explanation is deterministic, not generated

**Decision.** Implement row 27 by reading the already-parsed sqlglot AST,
not by asking a model.

**Why.** My own first draft of the plan proposed an on-demand LLM call.
Inspecting `agent/sql_validator.py::summarize_sql` showed the validator
already parses the statement and already derives `statement_type` and
`referenced_tables` from that AST. The catalogue's wording for row 27 --
*"Explain joins, filters, aggregations, and query behavior"* -- names
facts that are **in the AST**.

The deciding argument is not cost or latency, though it wins on both: an
explanation that hallucinates a join is worse than no explanation,
because it sits beside a real validator verdict and reads as
authoritative. A deterministic reading cannot be wrong about the SQL, and
it keeps model output from being fed back into a model at all.

**Consequence.** No new dependency, no new network call, no new prompt,
benchmark-pinned prompts untouched, and the on-demand-versus-automatic
question disappears.

**Reversal.** If you later want prose richer than the AST supports
(intent, business meaning), that is a different feature and would need
the model-call design, with its injection and latency trade-offs stated.

---

## D-008 — Open questions for you

| # | Question | Default if you don't answer |
|---|---|---|
| 1 | SQL explanation (Batch 1): on-demand, or automatic every SQL turn? | **On-demand** — automatic roughly doubles per-turn latency on a local model |
| 2 | May I add `vitest-axe` as a dev dependency for Batch 2C? | Hold — I will not add a dependency unasked |
| 3 | Row 79, content translation: translate governed answers? | **Hold** — must not alter figures, units or citations; needs design |
| 4 | Row 63, Word/Excel export: wanted? | **Hold** — needs a new dependency; PDF/CSV/Markdown already cover common cases |
| 5 | Enable any paid media provider (rows 80/81/86)? | **No** — remains off by default |
