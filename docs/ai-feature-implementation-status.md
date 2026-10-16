# AI Feature Implementation Status — the 100-prompt backlog

Tracks every one of the 100 supplied feature prompts from assessment
through to verification. Companion to
[`ai-feature-audit.md`](ai-feature-audit.md) (what exists) and
[`ai-feature-implementation-plan.md`](ai-feature-implementation-plan.md)
(what the gaps imply).

**Updated incrementally.** A row is edited in place as work moves; nothing
useful is overwritten.

## Status vocabulary

| Status | Means |
|---|---|
| `ASSESSED` | Audited against the repository, with cited evidence. Nothing further is required of it |
| `PLANNED` | A gap with a written plan, not yet started |
| `IN PROGRESS` | Being implemented now |
| `IMPLEMENTED` | Code written, not yet proven |
| `TESTED` | Its own tests written and passing |
| `VERIFIED` | Tested **and** the surrounding suites pass with no regression, **and** the derived acceptance criteria are met |
| `BLOCKED` | Cannot proceed — reason recorded |
| `DEFERRED` | Deliberately not done in this pass — reason recorded |

`VERIFIED` is reserved. Writing code does not earn it, and no row below
claims verification against live infrastructure, which this work did not
have (audit §10).

---

## 1. Summary

| Status | Count |
|---|---|
| `ASSESSED` (already complete — no code change needed) | 72 |
| `VERIFIED` (gap closed in this pass) | 4 |
| `DEFERRED` (gap, planned, not built in this pass) | 21 |
| `BLOCKED` (cannot be built as named) | 3 |
| **Total** | **100** |

The 3 blocked and 21 deferred together are the 13 missing plus 15
partially complete from the audit, minus the 4 closed here, plus 023
which is review-gated rather than missing.

---

## 2. Work completed in this pass

### 009 — Regenerate Answer · `VERIFIED`

| Field | Value |
|---|---|
| **Prompt** | `009_regenerate-answer.md` |
| **Initial audit status** | PARTIALLY COMPLETE — store action and five translations existed with **no caller and no test** |
| **Planned** | Render the control; gate it on `pendingQuestion` |
| **Actual** | New `RegenerateAnswerButton` mounted in `TurnCard`'s existing action row. Calls the existing `chatStore.rerunEntry(entryId)`; uses the existing `chat.rerun` label; disabled while any question is in flight |
| **Files** | `frontend/src/components/chat/RegenerateAnswerButton.tsx` (new), `RegenerateAnswerButton.test.tsx` (new), `frontend/src/components/chat/TurnCard.tsx` |
| **Reused, not rebuilt** | `rerunEntry` (unchanged), `chat.rerun` ×5 locales (unchanged), `ui/button.tsx`, the `pendingQuestion` busy flag that `ChatInput` and `FollowUpSuggestions` already use |
| **Tests** | 3 new. Right `entryId` reaches `rerunEntry`; disabled while pending **and the click genuinely does not fire**; label comes from i18n rather than a literal |
| **Results** | `vitest`: **1104 passed, 85 files** (was 1101/84). `tsc --noEmit`: clean. `oxlint`: no finding on any new file |
| **Regression** | None. Additive to a button row; the full frontend suite passes |
| **Security review** | No new surface. A re-run is an ordinary `/ask` with unchanged question text — same validation, authorization, governance and row limits. No new endpoint, parameter or permission |
| **Performance** | One user-initiated request. The busy gate is what prevents concurrent `/ask` calls racing for the same `pendingQuestion` slot |
| **Limitations** | Re-running **appends** a turn rather than replacing one. Deliberate: the transcript is append-only everywhere else, `conversation_history` is built from it, and silently overwriting would change what the next question resolves against without the person seeing it. Not offered on a failed or needs-clarification turn — that row only renders when an answer exists |

### 069 — Markdown Export · `VERIFIED`

| Field | Value |
|---|---|
| **Prompt** | `069_markdown-export.md` |
| **Initial audit status** | PARTIALLY COMPLETE — markdown to **clipboard** yes, **PDF** file yes, markdown **file** no |
| **Planned** | Add a `.md` download beside the PDF, reusing the filename slug |
| **Actual** | `DownloadAnswerButton` became a two-option menu (Markdown / PDF). New `lib/markdownFile.ts` owns the document builder, the filename slug and the object-URL download. The PDF path is unchanged |
| **Files** | `frontend/src/lib/markdownFile.ts` (new), `markdownFile.test.ts` (new), `frontend/src/components/chat/DownloadAnswerButton.tsx`, 5 × `translation.json` |
| **Reused, not rebuilt** | `downloadMarkdownAsPdf` (untouched), `ui/dropdown-menu.tsx`, the existing slug logic — now **moved** into `markdownFile.ts` so the two exports share one owner rather than a copy (`CLAUDE.md` §4.8) |
| **Tests** | 10 new. Document shape, trimming, the empty-question case, exactly one trailing newline; slug behaviour including the 60-char cap and the `"answer"` fallback; object URL revoked on success **and** when the click throws |
| **Results** | Same suite run as above — **1104 passed**. `tsc`: clean. `npm run build`: succeeds |
| **Regression** | The PDF path still works and is still the same function; the component test covers both options being offered |
| **Security review** | Client-side only. The string written is the same `answerMarkdown` already rendered and already copyable, so nothing is disclosed that the Copy button did not already disclose. No network call. `charset=utf-8` declared so symbols and accented names survive. Object URL revoked in a `finally` |
| **Performance** | Negligible; synchronous, no import cost. The PDF path keeps its dynamic imports |
| **Limitations** | Exports **one answer**. Exporting a whole conversation is not offered and was not requested by prompt 069 |

### 010 — Edit and Resubmit Question · `VERIFIED`

| Field | Value |
|---|---|
| **Prompt** | `010_edit-and-resubmit-question.md` |
| **Initial audit status** | MISSING — `setEditableSql` edits the **SQL**; `rerunEntry` re-asks **verbatim**; the question itself was not editable |
| **Planned** | Edit affordance on the user bubble, resubmitting through `askQuestion` as a new turn; opt-in per call site |
| **Actual** | `ChatMessage` gained two optional props, `onSubmitEdit` and `editDisabled`. With a handler it renders an edit control that swaps the bubble for a textarea with Ask-again / Cancel. Only `TurnCard` passes the handler |
| **Files** | `frontend/src/components/chat/ChatMessage.tsx`, `ChatMessage.test.tsx` (new), `frontend/src/components/chat/TurnCard.tsx`, 5 × `translation.json` |
| **Reused, not rebuilt** | `askQuestion` (unchanged), `ExpandableText`, the `pendingQuestion` busy flag, `common.cancel` |
| **Tests** | 11 new, the first two being the security-relevant ones — no control without a handler, and none on an assistant bubble. Then: draft seeded from current text, submit, cancel restores and does not resurrect an abandoned draft, Escape cancels, Enter submits while Shift+Enter does not, whitespace-only refused, busy state blocks **the keyboard path too**, text trimmed |
| **Results** | `vitest`: **1115 passed, 86 files** (was 1104/85). `tsc --noEmit`: clean. `oxlint`: no finding in a changed file. `npm run build`: succeeds |
| **Regression** | None. The two props are optional, so `PendingTurn` and `SharedConversation` render exactly what they rendered before — asserted directly rather than assumed |
| **Security review** | The reason the props are opt-in. `ChatMessage` is shared with `SharedConversation`, which **a viewer holding no roles at all can open**; a control that appeared there by default would invite an action that caller must not have. The edited text is an ordinary question — `agent.input_guard.sanitize_conversation_history` already treats resent history as untrusted, and every permission check reads the caller's own roles |
| **Performance** | One user-initiated request. Local component state only; no store field added, so nothing new to reset in `freshConversationState` |
| **Limitations** | Resubmitting **appends** a turn; it does not rewrite history or re-root the conversation. Editing a question whose turn is mid-flight is not offered (`PendingTurn` passes no handler). Branching from an edit (prompt 008) remains a separate, deferred feature |

### 015 — Keyboard Shortcuts · `VERIFIED`

| Field | Value |
|---|---|
| **Prompt** | `015_keyboard-shortcuts.md` |
| **Initial audit status** | PARTIALLY COMPLETE — component-local handlers only (`ChatInput`, `QuestionNavigator`, `ConversationSearch`, `ConversationListItem`, `MediaSearch`); no global layer and nothing discoverable |
| **Planned** | One global key layer mounted in `AppShell`, plus a `?` help dialog; must not capture keys while a text field or dialog has focus |
| **Actual** | `lib/shortcuts.ts` declares the bindings as data; `hooks/useGlobalShortcuts.ts` is the single `document` listener; `ShortcutHelpDialog` renders the list from the same array. Five bindings: `Ctrl/⌘+Shift+O` new chat, `Ctrl/⌘+B` sidebar, `Ctrl/⌘+,` settings, `/` focus composer, `?` help |
| **Files** | `frontend/src/lib/shortcuts.ts` + `.test.ts` (new), `frontend/src/hooks/useGlobalShortcuts.ts` + `.test.tsx` (new), `frontend/src/components/layout/ShortcutHelpDialog.tsx` (new), `AppShell.tsx`, `ChatInput.tsx`, 5 × `translation.json` |
| **Reused, not rebuilt** | `startNewChat` + the existing `handleHome`, `toggleSidebarCollapsed`, the existing `setIsSettingsOpen`, `ui/dialog.tsx`. No existing component-level handler was changed |
| **Tests** | 27 new. No duplicate binding; every shortcut has a label key so none can go undocumented; either platform modifier accepted; Shift checked only alongside a modifier (because `?` *is* Shift+/ on a US layout and unshifted elsewhere); Alt always rejected; ignored in input/textarea/select/contenteditable and while a modal is open; **an unclaimed key is not `preventDefault`ed**; listener detaches on unmount; the latest handler runs after a re-render without double-binding |
| **Results** | `vitest`: **1142 passed, 88 files**. `tsc --noEmit`: clean. `oxlint`: clean on every changed file. `npm run build`: succeeds |
| **Regression** | None. The hook returns early for any keystroke in a text field, so the composer's Enter/Shift+Enter and the question navigator's arrows are untouched — asserted, not assumed |
| **Security review** | No new data path, no network call, no permission implication. `preventDefault` is called **only** on a declared shortcut that has a handler, so no browser or assistive-technology binding is swallowed |
| **Performance** | One listener for the whole app, attached once. Handlers are held in a ref updated in an effect, so a re-render does not detach and re-attach |
| **Limitations** | Bindings are fixed, not user-remappable. `/` finds the composer by data attribute and is a no-op on a route without one. The platform label (⌘ vs Ctrl) is a user-agent guess — a wrong guess is cosmetic only, since the matcher accepts either modifier |

---

## 3. Already complete — no code change (72)

Assessed with cited evidence in [`ai-feature-audit.md`](ai-feature-audit.md)
§5. Each was inspected and left alone. Per the master prompt, none was
rewritten and no test was added to one merely to raise a count.

`ASSESSED`: 001, 002, 003, 006, 007, 011, 012, 013, 014, 016, 017, 019,
021, 027, 028, 031, 032, 034, 035, 036, 037, 038, 039, 040, 041, 043, 046,
047, 048, 049, 050, 051, 053, 054, 055, 056, 057, 059, 061, 062, 063, 065,
066, 068, 070, 071, 072, 073, 074, 075, 076, 077, 078, 079, 080, 081, 083,
084, 085, 086, 087, 088, 089, 090, 091, 092, 093, 094, 096, 097, 098, 099.

---

## 4. Blocked — cannot be built as named (3)

### 022 · 024 · 095 — authored instructions in a system prompt

| Field | Value |
|---|---|
| **Status** | `BLOCKED` |
| **Blocker** | All three mean "let user- or admin-authored text become part of a system prompt". [`CLAUDE.md`](../CLAUDE.md) §5.5 forbids exactly this — a database row that becomes a system prompt turns every write into prompt injection with full pipeline reach — and `tests/security/test_prompt_registry_cannot_weaken_security.py` pins it. `governance_center/inventory.py` records the decision at the point of declaration |
| **Why not overridden** | Building them as named requires weakening a security control. The governing request's §10 forbids disabling security protections without explicit authorization, and §8 forbids weakening SQL/authorization controls |
| **Independent work done** | The audit records what *does* exist: `governance_center/` is already a versioned prompt registry with approve → deploy → rollback, which is 095's governance half minus the hot-swap |
| **Safe alternatives, available on request** | (a) **Selection, not authoring** — let a user choose among in-code constants, as `domain_skills/` already does with 95 reviewed skills across 18 governance domains. (b) **Data position, not instruction position** — carry preferences as clearly framed untrusted *data*, in the same envelope retrieved documents already use |

---

## 5. Deferred — real gaps, planned, not built in this pass (23)

Each is a genuine gap with a written plan. None is abandoned; none is
claimed as done. Ordered as the plan's §9 orders them.

| ID | Feature | Audit status | Why deferred | Priority |
|---|---|---|---|---|
| 029 | Question Suggestions from Schema | MISSING | Must reuse `recommendation/followups.py`'s classification check so a restricted column is never named | P2 |
| 030 | New-User Guided Tour | MISSING | Product decision needed on what the tour covers | P2 |
| 064 | File Preview | PARTIALLY COMPLETE | Should reuse server-side processor output rather than parse in the browser | P2 |
| 004 | Conversation Pinning | MISSING | Needs an Alembic revision. Must **not** reuse `pinnedEntryIds`, which pins a turn (027), not a conversation | P2 |
| 026 | Saved Questions | PARTIALLY COMPLETE | Needs a table; must stay distinct from tenant-level golden questions, which feed retrieval | P2 |
| 058 | Research Plan Preview | PARTIALLY COMPLETE | Cheapest of its group — `route_decision` is already computed, just not surfaced | P3 |
| 100 | AI Usage and Cost Dashboard | PARTIALLY COMPLETE | Token counts already captured; needs an aggregation surface. **Must not show a monetary figure** — none exists for local Ollama | P3 |
| 018 | One-Click Clarification | PARTIALLY COMPLETE | Clickable options need a model call or a deterministic option generator | P3 |
| 033 | SQL Explanation | MISSING | Adds an LLM call; must be on demand and must not edit the benchmark-pinned prompts | P3 |
| 052 | Alternative SQL Suggestions | MISSING | Same constraint as 033, plus N× generation cost | P3 |
| 060 | Answer Verification Mode | PARTIALLY COMPLETE | Same constraint as 033 | P3 |
| 042 | Date-Range Controls | MISSING | Needs a design decision on whether it filters results or rewrites the question | P3 |
| 044 | Drill-Down Analytics | PARTIALLY COMPLETE | Should share a "derive a narrower question" path with 045 | P3 |
| 045 | Compare Results | PARTIALLY COMPLETE | See 044 | P3 |
| 067 | Saved Dashboards | PARTIALLY COMPLETE | Should compose over artifacts, not introduce a second store | P3 |
| 082 | Data Retention Controls | PARTIALLY COMPLETE | Retention beyond attachments is a policy decision, not just code. Already a disclosed limitation | P3 |
| 005 | Conversation Folders | MISSING | Whole feature: model + API + UI | P3 |
| 020 | Conversation Summarization | MISSING | Whole feature; adds a model call | P3 |
| 025 | Conversation Templates | MISSING | Whole feature | P3 |
| 008 | Chat Branching | MISSING | Largest in the backlog — changes a conversation's shape, which `chatStore`, `identity/repositories/history.py`, the share view and `conversation_history` all assume is linear | P3 |
| 023 | Domain-Specific Assistants | PARTIALLY COMPLETE (built, not wired) | **Review-gated, not unbuilt.** 95 skills across 18 domains are implemented and tested. Wiring alters how every routed analysis is framed, and [`CLAUDE.md`](../CLAUDE.md) §4.7 forbids finishing wiring as a side effect of other work | Review |

---

## 6. Validation runs

| Check | Command | Result |
|---|---|---|
| Frontend unit/component | `npm run test` | **1142 passed, 88 files** (baseline before this work: 1101 / 84) |
| Frontend types | `npx tsc --noEmit -p tsconfig.app.json` | Clean |
| Frontend lint | `npm run lint` | Exit 0; warnings only, all pre-existing, none in a changed file |
| Frontend build | `npm run build` | Succeeds (chunk-size warning pre-existing) |
| Backend | `pytest` | **7134 passed, 11 deselected** — unchanged; no backend file was modified in this pass |

Not run, and not claimed: the execution-accuracy benchmark, the security
benchmark, `scripts/integration_test.py`, and any browser or live-database
check. All require infrastructure this environment does not have.

---

## 7. Pre-existing findings, not introduced here

Named so a later pass does not attribute them to this work. Both are
already recorded in [`CLAUDE.md`](../CLAUDE.md) §13.

- `ruff`: one import-order finding in `agent/multiagent/agents.py`.
- `mypy`: one arg-type error in `db/schema_introspection.py`.
- `oxlint`: `react(only-export-components)` warnings across ~10 existing
  components, and one `set-state-in-effect` warning in
  `image/ResizeImageDialog.tsx`.
