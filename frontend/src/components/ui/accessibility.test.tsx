import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { ChatMessage } from '@/components/chat/ChatMessage'
import { expectNoAccessibilityViolations, findAccessibilityViolations } from '@/test/axe'

/** Runtime accessibility checks on the shared surfaces (capability-audit
 * row 60).
 *
 * These complement, rather than duplicate, two things the repository
 * already had: the `jsx-a11y` lint rules, which read one file's JSX
 * statically, and the 53 test files that query by role and label, which
 * fail implicitly when an element has no accessible name. axe evaluates
 * the *composed, rendered* tree, which is where ARIA relationships and
 * accessible names actually resolve.
 *
 * Scoped to the shared primitives and the chat bubble on purpose: these
 * are what every screen is built from, so a regression here reaches
 * everywhere. Whole-page audits are deliberately not attempted — they
 * need a real browser, which is also the only place colour contrast can
 * be checked at all (see `src/test/axe.ts`).
 */
describe('accessibility — shared UI primitives', () => {
  it('a button has no violations', async () => {
    const { container } = render(<Button>Run query</Button>)
    await expectNoAccessibilityViolations(container)
  })

  it('an icon-only button is still named', async () => {
    // The failure mode this guards: an icon button with no text and no
    // aria-label is invisible to a screen reader while looking fine.
    const { container } = render(
      <Button size="icon" aria-label="Close dialog">
        <span aria-hidden="true">x</span>
      </Button>,
    )
    await expectNoAccessibilityViolations(container)
  })

  it('catches an icon-only button that lost its name', async () => {
    // Non-vacuity: the suite must actually fail on a real defect, or
    // every passing assertion above is decorative.
    const { container } = render(
      <Button size="icon">
        <span aria-hidden="true">x</span>
      </Button>,
    )
    const violations = await findAccessibilityViolations(container)
    expect(violations.map((v) => v.id)).toContain('button-name')
  })

  it('a card has no violations', async () => {
    const { container } = render(
      <Card>
        <p>Result summary</p>
      </Card>,
    )
    await expectNoAccessibilityViolations(container)
  })
})

describe('accessibility — dialog', () => {
  it('an open dialog is correctly labelled and described', async () => {
    // Radix wires aria-modal/aria-labelledby/aria-describedby; this
    // pins that the app's own wrapper does not break that wiring.
    render(
      <Dialog open>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Give feedback</DialogTitle>
            <DialogDescription>Tell us what was helpful.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button>Submit</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>,
    )
    const dialog = screen.getByRole('dialog')
    await expectNoAccessibilityViolations(dialog)
    expect(dialog).toHaveAccessibleName('Give feedback')
  })
})

describe('accessibility — chat message', () => {
  it('a question bubble has no violations', async () => {
    const { container } = render(
      <ChatMessage message={{ role: 'user', content: 'Top regions by revenue?' }} />,
    )
    await expectNoAccessibilityViolations(container)
  })

  it('the hover action row is reachable and named', async () => {
    // The row is visually hidden until hover, but pointer-events and
    // opacity never affect assistive technology -- so its controls must
    // be named even at rest. This is the a11y half of that design.
    const { container } = render(
      <ChatMessage
        message={{ role: 'user', content: 'Top regions?' }}
        onSubmitEdit={vi.fn()}
      />,
    )
    await expectNoAccessibilityViolations(container)
    expect(screen.getByRole('button', { name: /edit/i })).toBeInTheDocument()
  })

  it('the question editor is labelled', async () => {
    const { container } = render(
      <ChatMessage
        message={{ role: 'user', content: 'Top regions?' }}
        onSubmitEdit={vi.fn()}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: /edit/i }))
    expect(screen.getByRole('textbox')).toHaveAccessibleName()
    await expectNoAccessibilityViolations(container)
  })
})
