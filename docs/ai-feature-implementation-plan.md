# AI Feature Implementation Plan — the 28 prompts that need work

**Derived from:** [`ai-feature-audit.md`](ai-feature-audit.md), audited
2026-10-16 against `0398bf6`.
**Tracked in:** [`ai-feature-implementation-status.md`](ai-feature-implementation-status.md).

72 of the 100 supplied prompts need no application code change. This plan
covers the other 28 — 15 partially complete and 13 missing — ordered by
actual gap and risk rather than by prompt number.

---

## 1. How this plan was ordered

The supplied `02_EXECUTION_STRATEGY.md` proposes running 001–015, then
016–030, and so on. That order is **not** used, for a reason the audit
makes concrete: it would start with eleven features that are already
finished and reach the genuinely missing ones late. The order below is:

1. **Finish what is already written.** One feature (009) has a working
   store action and translated labels in five locales with no caller. That
   is the cheapest real improvement in the backlog.
2. **Then frontend-only gaps** that add no persistence, no API surface and
   no model call.
3. **Then gaps needing one small migration.**
4. **Then gaps that add a model call per turn**, which cost latency on a
   local model.
5. **Then gaps that are whole features** (new model + API + UI).
6. **Never** the three bounded by [`CLAUDE.md`](../CLAUDE.md) §5.5 in the
   form their names imply.

### Priority definitions

- **P0** — critical security, data integrity, or core failure. **Nothing in
  this backlog is P0.** No supplied prompt describes a missing or weakened
  security control (audit §5.6).
- **P1** — high-impact gap, small and safe to close.
- **P2** — important enhancement.
- **P3** — optional, or needs a product decision first.

---

## 2. Batch 1 — P1: finish the answer-action row (009, 069)

Both act on the same object (`answerMarkdown` in
`components/chat/TurnCard.tsx`), both are frontend-only, and both sit in
the same button row. Grouping them is one reviewable change; splitting them
would touch the same file twice.

### 009 — Regenerate Answer

| | |
|---|---|
| **Prompt** | `009_regenerate-answer.md` |
| **Status** | PARTIALLY COMPLETE |
| **Existing** | `chatStore.rerunEntry(entryId)` — finds the entry, calls `askQuestion(entry.question)`. `chat.rerun` translated in en/es/fr/hi/mr |
| **Exact gap** | **No component calls `rerunEntry`, and nothing references `chat.rerun`.** Dead store action plus five orphan translations |
| **Proposal** | Render a `RegenerateAnswerButton` in `TurnCard`'s existing action row, beside Copy/Download/Save. Disable while a question is pending, so a second click cannot start a concurrent `/ask` |
| **Reuse** | `rerunEntry`, `chat.rerun`, `ui/button.tsx`, the existing `pendingQuestion` guard |
| **Files** | `components/chat/RegenerateAnswerButton.tsx` (new), `components/chat/TurnCard.tsx` |
| **Dependencies** | None |
| **Security** | None. Re-asking is an ordinary `/ask`: the same validation, authorization, governance and row limits apply. No new endpoint, no new parameter |
| **Performance** | One deliberate, user-initiated `/ask`. The disabled-while-pending guard is what prevents an unbounded click-storm |
| **Database** | None |
| **Acceptance** | Button renders on a completed turn; click re-asks the same question; disabled while pending; labelled from i18n in all five locales |
| **Tests** | Component test: renders, calls `rerunEntry` with the right `entryId`, disabled while pending |
| **Regression risk** | Low — additive to a button row |
| **Complexity** | XS |

### 069 — Markdown Export

| | |
|---|---|
| **Prompt** | `069_markdown-export.md` |
| **Status** | PARTIALLY COMPLETE |
| **Existing** | `CopyAnswerButton` → `copyMarkdownToClipboard(answer)`. `DownloadAnswerButton` → `downloadMarkdownAsPdf(...)` — a **PDF**, not markdown |
| **Exact gap** | No way to obtain the answer **as a `.md` file**. Clipboard-only export loses the question, and PDF is not re-editable |
| **Proposal** | Extend `DownloadAnswerButton` into a two-option control (Markdown / PDF) reusing the filename slug it already builds. A `Blob` + object URL — no new dependency |
| **Reuse** | The existing slug logic, `ui/dropdown-menu.tsx`, `common.download*` keys |
| **Files** | `components/chat/DownloadAnswerButton.tsx`, `lib/markdownFile.ts` (new), i18n ×5 |
| **Dependencies** | None |
| **Security** | Client-side only; the same string already on screen and already copyable. No new network call. Object URL revoked after use |
| **Performance** | Negligible |
| **Database** | None |
| **Acceptance** | A `.md` file downloads containing the question and the answer; the existing PDF path is unchanged |
| **Tests** | Unit test for the markdown file builder; component test that both options are offered |
| **Regression risk** | Low — the PDF path must keep working, which the component test pins |
| **Complexity** | S |

---

## 3. Batch 2 — P2: frontend-only gaps

### 010 — Edit and Resubmit Question

| | |
|---|---|
| **Status** | MISSING |
| **Existing** | `setEditableSql` edits the **SQL**. `rerunEntry` re-asks **verbatim**. `askQuestion` accepts any string |
| **Exact gap** | The question text cannot be edited and resubmitted |
| **Proposal** | An edit control on the user bubble that swaps it for a textarea; submitting calls `askQuestion(editedText)`, appending a new turn rather than mutating the old one — the transcript stays append-only, which is what keeps `conversation_history` honest |
| **Reuse** | `askQuestion`, `ChatMessage.tsx`, `ui/button.tsx` |
| **Files** | `components/chat/ChatMessage.tsx`, `components/chat/TurnCard.tsx`, i18n ×5 |
| **Security** | The edited text is an ordinary question; `sanitize_conversation_history` already treats resent history as untrusted |
| **Performance** | One user-initiated `/ask` |
| **Acceptance** | Edit, submit, new turn appended; cancel restores; original turn unchanged |
| **Tests** | Component: enter edit mode, cancel, submit |
| **Risk / complexity** | Low / M. **Note:** `ChatMessage.tsx` is shared by `TurnCard`, `PendingTurn` and `SharedConversation` — the control must be opt-in per call site so a shared read-only view never offers it |

### 015 — Keyboard Shortcuts · 030 — Guided Tour · 064 — File Preview · 029 — Schema Question Suggestions

| ID | Gap | Proposal | Complexity |
|---|---|---|---|
| 015 | Component-local handlers only; no global layer, no discoverable list | A single `useGlobalShortcuts` hook mounted in `AppShell`, plus a `?`-triggered help dialog reusing `ui/dialog.tsx`. Must not capture keys while a textarea or dialog has focus | M |
| 030 | No in-app tour | A dismissible, `localStorage`-gated tour over existing anchors; must not block the first question | M |
| 064 | Images preview; PDF/CSV/XLSX do not | Extend `SentAttachmentsPreview` with a type-aware preview, reusing processor output already produced server-side rather than parsing in the browser | M |
| 029 | Suggestions exist only *after* an answer | Derive 3–5 starter questions from the indexed schema for the empty state. **Must reuse `recommendation/followups.py`'s classification check** so a restricted column is never named in a suggestion | M |

---

## 4. Batch 3 — P2: one small migration each

### 004 — Conversation Pinning

Add `pinned_at` (nullable timestamp) to the conversation model; a new
Alembic revision with `server_default` where applicable; `PATCH
/conversations/{id}` already exists and can carry the field; sort pinned
first in `ConversationList`. **Must not** reuse `chatStore.pinnedEntryIds`,
which pins a turn into the artifact workspace (audit §7). Complexity M.

### 026 — Saved Questions (user-level)

Tenant-level golden questions already exist. A per-user saved question is a
small table plus a save action on the input. Risk: duplicating the golden
question concept — the plan is to keep them separate and say so, since one
feeds retrieval and the other is a personal bookmark. Complexity M.

---

## 5. Batch 4 — P3: features that add a model call per turn

033 (SQL Explanation), 052 (Alternative SQL Suggestions), 060 (Answer
Verification Mode), 018 (clickable clarification options).

All four share one constraint and one design:

- **Constraint.** The generation and insight prompts are benchmark-pinned
  ([`CLAUDE.md`](../CLAUDE.md) §12). None of these may edit either. Each
  needs a *separate* call, which on a local Ollama model is the slowest
  step in the request.
- **Design.** Each should be **on demand** (a user clicks "explain") rather
  than per turn, and each should fail open — an explanation that fails to
  generate must never block the answer.

058 (Research Plan Preview) is the cheapest of this group and needs **no**
model call: the router's `route_decision` is already computed and simply
is not surfaced before the subgraphs run.

100 (AI usage rollup) also needs no model call — token counts are already
read in `agent/llm_client.py`. It needs an aggregation surface only, and
must not present a monetary figure, which does not exist for local Ollama.

---

## 6. Batch 5 — P3: whole features

005 (Folders), 008 (Branching), 020 (Summarization), 025 (Templates), 042
(Date-Range Controls), 044 (Drill-Down), 045 (Compare Results), 067 (Saved
Dashboards), 082 (retention beyond attachments).

Each needs a data model, API routes, UI and tests. 008 is the largest:
branching changes the shape of a conversation, which `chatStore`,
`identity/repositories/history.py`, the share view and
`conversation_history` construction all assume is linear.

044 and 045 should share one "derive a narrower question" path (audit §7),
and 067 should compose over artifacts rather than introduce a second store.

---

## 7. Not recommended as named: 022, 024, 095

022 (Custom AI Instructions), 024 (Workspace-Specific Instructions) and 095
(Custom Agent Instructions) all mean "let a user or admin author text that
becomes part of a system prompt".

**That is the one thing this codebase forbids by design.**
[`CLAUDE.md`](../CLAUDE.md) §5.5: making a database row the system prompt
would turn every write into prompt injection with full pipeline reach. It
is pinned by `tests/security/test_prompt_registry_cannot_weaken_security.py`,
and `governance_center/inventory.py` documents the decision at the point of
declaration.

Implementing these as named would require weakening that control, which
§10 of the governing request forbids without explicit authorization. **This
work is therefore stopped and raised rather than attempted.**

Two safe alternatives exist, and either can be built on request:

- **Selection, not authoring.** Let a user *choose* among in-code
  constants, exactly as `domain_skills/` already does — 95 reviewed skills
  across 18 governance domains, each a frozen constant. This gives most of
  the product value of 022/023/024 with no injection surface, and it is
  what prompt 023's wiring decision is really about.
- **Data position, not instruction position.** Carry user preferences as
  clearly framed *data* ("the user prefers brief answers") in the same
  untrusted-content envelope retrieved documents already use, never as an
  instruction the model is told to obey.

---

## 8. Review-gated: 023 (Domain-Specific Assistants)

`domain_skills/` is complete and tested but has no live caller, by
deliberate decision recorded in
[`domain-skills.md`](architecture/domain-skills.md) §24. Wiring it is a
real product change — it alters how every routed analysis is framed — and
[`CLAUDE.md`](../CLAUDE.md) §4.7 is explicit that a session must not
"finish the wiring" as a side effect of other work. **Not done here.**

---

## 9. Safe implementation order

```
Batch 1  009, 069          frontend only, no new state        ← implemented
Batch 2  010               frontend only, shared component    ← implemented
         015, 030, 064, 029
Batch 3  004, 026          one migration each
Batch 4  058, 100          no model call
         018, 033, 052, 060   one model call, on demand
Batch 5  042, 044, 045, 067, 082
         005, 020, 025
         008                   largest; changes conversation shape
—        022, 024, 095      blocked by design; see §7
—        023                review-gated; see §8
```

Each batch ends with: run the relevant tests, review the diff against
[`CLAUDE.md`](../CLAUDE.md) §4 and §5, update the status document, and
commit separately.

---

## 10. Regression surface shared by every batch

The components these features touch are the busiest in the app. Before
modifying any of them, check the dependents:

| Component | Depended on by |
|---|---|
| `ChatMessage.tsx` | `TurnCard`, `PendingTurn`, `SharedConversation` — a shared conversation has **no roles at all**, so an action must never leak into it |
| `TurnCard.tsx` | The chat page and the artifact detail view |
| `chatStore.ts` | Every chat surface; `freshConversationState` must reset any new field |
| `ResultsTable.tsx` | Chat, shared view, artifact detail |
| i18n | Five locales; a missing key renders the raw key |
