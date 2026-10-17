import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ChatMessage } from './ChatMessage'

/** Feature 010 (Edit and Resubmit Question).
 *
 * The security-relevant behaviour here is the *absence* of the control,
 * not its presence: `ChatMessage` is shared with `SharedConversation`,
 * which a viewer holding no roles at all can open. Those cases are
 * asserted first and deliberately.
 */
describe('ChatMessage', () => {
  it('offers no edit control when the caller passes no handler', () => {
    // This is what `PendingTurn` and `SharedConversation` render. A share
    // link can be opened by someone with no roles; the control must not
    // appear there by default.
    render(<ChatMessage message={{ role: 'user', content: 'Top regions?' }} />)
    expect(screen.queryByRole('button', { name: /edit/i })).not.toBeInTheDocument()
  })

  it('offers no edit control on an assistant bubble even with a handler', () => {
    // Editing what the assistant said would be fabrication, not an edit.
    render(
      <ChatMessage
        message={{ role: 'assistant', content: 'North leads.' }}
        onSubmitEdit={vi.fn()}
      />,
    )
    expect(screen.queryByRole('button', { name: /edit/i })).not.toBeInTheDocument()
  })

  it('submits the edited question to the handler', async () => {
    const onSubmitEdit = vi.fn()
    render(
      <ChatMessage message={{ role: 'user', content: 'Top regions?' }} onSubmitEdit={onSubmitEdit} />,
    )

    await userEvent.click(screen.getByRole('button', { name: /edit/i }))
    const textarea = screen.getByRole('textbox')
    await userEvent.clear(textarea)
    await userEvent.type(textarea, 'Top regions by margin?')
    await userEvent.click(screen.getByRole('button', { name: /ask again/i }))

    expect(onSubmitEdit).toHaveBeenCalledWith('Top regions by margin?')
  })

  it('starts the draft from the current question text', async () => {
    render(
      <ChatMessage message={{ role: 'user', content: 'Top regions?' }} onSubmitEdit={vi.fn()} />,
    )
    await userEvent.click(screen.getByRole('button', { name: /edit/i }))
    expect(screen.getByRole('textbox')).toHaveValue('Top regions?')
  })

  it('cancelling restores the original and submits nothing', async () => {
    const onSubmitEdit = vi.fn()
    render(
      <ChatMessage message={{ role: 'user', content: 'Top regions?' }} onSubmitEdit={onSubmitEdit} />,
    )

    await userEvent.click(screen.getByRole('button', { name: /edit/i }))
    await userEvent.type(screen.getByRole('textbox'), ' and margins')
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }))

    expect(onSubmitEdit).not.toHaveBeenCalled()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    // And re-opening does not resurrect the abandoned draft.
    await userEvent.click(screen.getByRole('button', { name: /edit/i }))
    expect(screen.getByRole('textbox')).toHaveValue('Top regions?')
  })

  it('Escape cancels', async () => {
    const onSubmitEdit = vi.fn()
    render(
      <ChatMessage message={{ role: 'user', content: 'Top regions?' }} onSubmitEdit={onSubmitEdit} />,
    )
    await userEvent.click(screen.getByRole('button', { name: /edit/i }))
    await userEvent.keyboard('{Escape}')

    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(onSubmitEdit).not.toHaveBeenCalled()
  })

  it('Enter submits and Shift+Enter does not', async () => {
    const onSubmitEdit = vi.fn()
    render(
      <ChatMessage message={{ role: 'user', content: 'Top regions?' }} onSubmitEdit={onSubmitEdit} />,
    )
    await userEvent.click(screen.getByRole('button', { name: /edit/i }))

    // Shift+Enter inserts a newline and keeps the editor open -- the same
    // contract the composer uses.
    await userEvent.keyboard('{Shift>}{Enter}{/Shift}')
    expect(onSubmitEdit).not.toHaveBeenCalled()
    expect(screen.getByRole('textbox')).toBeInTheDocument()

    await userEvent.keyboard('{Enter}')
    expect(onSubmitEdit).toHaveBeenCalledTimes(1)
  })

  it('refuses to submit a question edited down to whitespace', async () => {
    const onSubmitEdit = vi.fn()
    render(
      <ChatMessage message={{ role: 'user', content: 'Top regions?' }} onSubmitEdit={onSubmitEdit} />,
    )
    await userEvent.click(screen.getByRole('button', { name: /edit/i }))
    await userEvent.clear(screen.getByRole('textbox'))
    await userEvent.type(screen.getByRole('textbox'), '   ')

    const submit = screen.getByRole('button', { name: /ask again/i })
    expect(submit).toBeDisabled()
    await userEvent.click(submit)
    expect(onSubmitEdit).not.toHaveBeenCalled()
  })

  it('does not submit while another question is in flight', async () => {
    const onSubmitEdit = vi.fn()
    render(
      <ChatMessage
        message={{ role: 'user', content: 'Top regions?' }}
        onSubmitEdit={onSubmitEdit}
        editDisabled
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: /edit/i }))

    expect(screen.getByRole('button', { name: /ask again/i })).toBeDisabled()
    // Non-vacuity: the keyboard path must honour the same gate, or the
    // disabled button would be cosmetic.
    await userEvent.keyboard('{Enter}')
    expect(onSubmitEdit).not.toHaveBeenCalled()
  })

  it('trims the submitted question', async () => {
    const onSubmitEdit = vi.fn()
    render(
      <ChatMessage message={{ role: 'user', content: 'Top regions?' }} onSubmitEdit={onSubmitEdit} />,
    )
    await userEvent.click(screen.getByRole('button', { name: /edit/i }))
    await userEvent.clear(screen.getByRole('textbox'))
    await userEvent.type(screen.getByRole('textbox'), '  Revenue by region  ')
    await userEvent.keyboard('{Enter}')

    expect(onSubmitEdit).toHaveBeenCalledWith('Revenue by region')
  })

  it('fills the row so the bubble can grow to its max-width', () => {
    // A layout regression guard, not a style preference. Every caller
    // wraps this in `<div className="flex justify-end">`, which makes the
    // root a shrink-to-fit flex item. Without `w-full` the `max-w-[75%]`
    // below resolved against the root's *content* width, so a question
    // was capped at 75% of its own natural text width and wrapped early
    // with most of the row empty beside it. Dropping `w-full` brings that
    // back, silently and only at certain text lengths -- which is exactly
    // the kind of bug a test should hold.
    const { container } = render(
      <ChatMessage message={{ role: 'user', content: 'Top news of Delhi for today.' }} />,
    )
    const root = container.firstElementChild
    expect(root).toHaveClass('w-full')

    // The cap lives on the column that holds the bubble and its action
    // row, so both are constrained by the same width.
    const column = root?.querySelector('.max-w-\\[85\\%\\]')
    expect(column).not.toBeNull()
    expect(column).toHaveClass('sm:max-w-[75%]')
    expect(column).toHaveClass('min-w-0')
    expect(column).toHaveClass('flex-col')
  })

  it('keeps the action row hidden until hover or focus', () => {
    const { container } = render(
      <ChatMessage
        message={{ role: 'user', content: 'Top regions?' }}
        onSubmitEdit={vi.fn()}
        actions={<button type="button">Copy</button>}
      />,
    )
    const row = container.querySelector('.group-hover\\:opacity-100')
    expect(row).not.toBeNull()

    // At rest: invisible, and not clickable either. `opacity-0` alone
    // would leave buttons that cannot be seen but can still be hit.
    expect(row).toHaveClass('opacity-0')
    expect(row).toHaveClass('pointer-events-none')
    // Revealed by hovering the turn, and by keyboard focus reaching it,
    // so it is not a mouse-only affordance.
    expect(row).toHaveClass('group-hover:pointer-events-auto')
    expect(row).toHaveClass('focus-within:opacity-100')

    // `opacity-0`, not `hidden`: the row holds its height so the
    // conversation does not jump as the pointer moves down it.
    expect(row).not.toHaveClass('hidden')
  })

  it('renders caller-supplied actions beside the edit button', () => {
    render(
      <ChatMessage
        message={{ role: 'user', content: 'Top regions?' }}
        onSubmitEdit={vi.fn()}
        actions={<button type="button">Copy question</button>}
      />,
    )
    expect(screen.getByRole('button', { name: 'Copy question' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /edit/i })).toBeInTheDocument()
  })

  it('renders an action row for actions alone, with no edit handler', () => {
    // `SharedConversation` passes neither and gets no row at all; a
    // caller may still want copy without offering edit.
    render(
      <ChatMessage
        message={{ role: 'user', content: 'Top regions?' }}
        actions={<button type="button">Copy question</button>}
      />,
    )
    expect(screen.getByRole('button', { name: 'Copy question' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /edit/i })).not.toBeInTheDocument()
  })

  it('hides the action row while editing', async () => {
    render(
      <ChatMessage
        message={{ role: 'user', content: 'Top regions?' }}
        onSubmitEdit={vi.fn()}
        actions={<button type="button">Copy question</button>}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: /edit/i }))

    // Copying or sharing a half-typed draft would be meaningless, and
    // the editor has its own Cancel/Ask-again pair.
    expect(screen.queryByRole('button', { name: 'Copy question' })).not.toBeInTheDocument()
  })

  it('still renders the question text when not editing', () => {
    render(
      <ChatMessage message={{ role: 'user', content: 'Top regions?' }} onSubmitEdit={vi.fn()} />,
    )
    // `getAllByText`, not `getByText`: `ExpandableText` renders the
    // string twice by design -- once visibly and once in the hidden copy
    // it measures to decide whether a "Show more" toggle is needed.
    expect(screen.getAllByText('Top regions?').length).toBeGreaterThan(0)
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })
})
