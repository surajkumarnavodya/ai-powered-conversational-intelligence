import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Select } from '@/components/ui/select'
import { expectNoAccessibilityViolations, findAccessibilityViolations } from '@/test/axe'

/** Regressions for the accessibility defects a **real browser** found.
 *
 * ## Why this file is separate from `accessibility.test.tsx`
 *
 * That file audits the shared primitives and says, correctly, that
 * "whole-page audits are deliberately not attempted — they need a real
 * browser". Phase 9 ran that audit: Chrome driven over the DevTools
 * Protocol, axe-core injected with `color-contrast` **enabled**, across
 * eleven screens x two themes x six accents plus a 360px viewport, against
 * the live application. It found five distinct rule failures that no jsdom
 * test could have reported, because they depend on either composition
 * across a whole page or on layout and paint.
 *
 * The ones that *can* be pinned under jsdom are pinned here. Each names
 * the finding id so the browser evidence is findable:
 *
 * - `UI-006` `label-content-name-mismatch` (serious, WCAG 2.5.3): three
 *   header controls whose `aria-label` did not contain their own visible
 *   text, so a speech-input user saying what they could see did not match
 *   the control. axe reports a **false pass** for this rule under jsdom,
 *   so the tests below check the computed accessible name directly -- see
 *   that block's own note.
 * - `UI-007` `select-name` and `label` (both **critical**): nine native
 *   `<select>` elements and one `<input type="file">` with no accessible
 *   name at all. `components/ui/select.tsx`'s docstring says a native
 *   select is "fully accessible out of the box" -- true of keyboard
 *   operation, not of naming, which is how ten of them shipped unnamed.
 *
 * Two findings deliberately have **no** test here, because asserting them
 * under jsdom would be asserting nothing:
 *
 * - `UI-005` `target-size` (WCAG 2.2 AA 2.5.8) is a measured 16x16 box.
 *   jsdom reports every element as 0x0, so axe cannot evaluate it and a
 *   class-name assertion would only restate the fix. Re-measure in a
 *   browser.
 * - `UI-008` `color-contrast` needs computed colour over a composited
 *   background. `src/test/contrast.ts` covers what the *declared tokens*
 *   can prove; the translucent-tint pairings this audit measured are
 *   recorded in `docs/uat-readiness/phase-9-ui.md` and are an open design
 *   decision, not a fixed defect.
 */

describe('UI-007 — a styled native select must carry an accessible name', () => {
  it('is named when one is given', async () => {
    const { container } = render(
      <Select aria-label="Filter by asset type" value="">
        <option value="">All asset types</option>
        <option value="prompt">prompt</option>
      </Select>,
    )
    await expectNoAccessibilityViolations(container)
    expect(screen.getByRole('combobox', { name: 'Filter by asset type' })).toBeInTheDocument()
  })

  it('is named when an associated label supplies it', async () => {
    const { container } = render(
      <div>
        <label htmlFor="knowledge-sensitivity">Sensitivity</label>
        <Select id="knowledge-sensitivity" value="">
          <option value="">None</option>
        </Select>
      </div>,
    )
    await expectNoAccessibilityViolations(container)
    expect(screen.getByRole('combobox', { name: 'Sensitivity' })).toBeInTheDocument()
  })

  it('is reported unnamed when nothing names it — the shipped defect', async () => {
    // Proves this suite is not vacuous: exactly the markup that was live
    // on five screens, and the rule that caught it in Chrome.
    const { container } = render(
      <Select value="">
        <option value="">All asset types</option>
      </Select>,
    )
    const violations = await findAccessibilityViolations(container)
    expect(violations.map((violation) => violation.id)).toContain('select-name')
  })

  it("a bare label beside the control does not name it", async () => {
    // The shape `KnowledgeSources.tsx` had: a <label> with neither htmlFor
    // nor the control nested inside it. It reads as a label and is not one.
    const { container } = render(
      <div>
        <label>Sensitivity</label>
        <Select value="">
          <option value="">None</option>
        </Select>
      </div>,
    )
    const violations = await findAccessibilityViolations(container)
    expect(violations.map((violation) => violation.id)).toContain('select-name')
  })

  it('a file input with no name is reported', async () => {
    const { container } = render(<input type="file" accept="application/pdf" />)
    const violations = await findAccessibilityViolations(container)
    expect(violations.map((violation) => violation.id)).toContain('label')
  })

  it('a named file input passes', async () => {
    const { container } = render(
      <input type="file" accept="application/pdf" aria-label="Upload a document" />,
    )
    await expectNoAccessibilityViolations(container)
  })
})

describe('UI-006 - a control\'s accessible name must contain its visible text', () => {
  /** **axe cannot check this under jsdom, and reports a false pass.**
   * Verified while writing this file: the exact markup Chrome flagged --
   * `aria-label="Start a new chat"` on a button whose visible text is the
   * product title -- comes back in axe's `passes` list here, because the
   * rule needs rendered text and jsdom has no layout. `src/test/axe.ts`
   * now names the rule as unsupported for that reason.
   *
   * So these assert the property the fix guarantees, using the accessible
   * name `getByRole` computes, rather than asking axe. That is directly
   * checkable and non-vacuous: each assertion fails against the shipped
   * `aria-label`. */
  it('the header home button is reachable by the words a user can see', () => {
    render(
      <button type="button" aria-label="Conversational Intelligence - Start a new chat">
        <span>Conversational Intelligence</span>
      </button>,
    )
    // A speech-input user says what is on screen; the name must match it.
    expect(screen.getByRole('button', { name: /Conversational Intelligence/ })).toBeInTheDocument()
    // And the action is still in the name, so the name still says what it does.
    expect(screen.getByRole('button', { name: /Start a new chat/ })).toBeInTheDocument()
  })

  it('the model picker is reachable by the model name it displays', () => {
    render(
      <button type="button" aria-label="AI Model: Llama 3.1 8B">
        <span>Llama 3.1 8B</span>
      </button>,
    )
    expect(screen.getByRole('button', { name: /Llama 3\.1 8B/ })).toBeInTheDocument()
  })

  it('the avatar button is reachable by the initials it displays', async () => {
    // The avatar button. `aria-hidden` on the initials does **not** satisfy
    // this rule -- axe counts aria-hidden text as visible text here, and
    // hiding them would not help the speech-input user either, who can only
    // say the two characters they see. So the initials go *into* the name,
    // with the purpose kept after them. This is what `UserMenu.tsx` renders.
    const { container } = render(
      <button type="button" aria-label="UA - account menu">
        <span>UA</span>
      </button>,
    )
    await expectNoAccessibilityViolations(container)
    expect(screen.getByRole('button', { name: /UA/ })).toBeInTheDocument()
    // The purpose is still in the name, so the name still says what it does.
    expect(screen.getByRole('button', { name: /account menu/ })).toBeInTheDocument()
  })
})
