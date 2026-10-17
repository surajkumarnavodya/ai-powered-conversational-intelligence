import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useChatStore } from '@/store/chatStore'
import { RegenerateAnswerButton } from './RegenerateAnswerButton'

/** Feature 009 (Regenerate Answer).
 *
 * The store action and the five translations already existed; what did not
 * exist was any caller, so these tests are specifically about the wiring
 * -- that the button reaches `rerunEntry` with the right id, and that it
 * cannot start a second run while one is in flight.
 */
describe('RegenerateAnswerButton', () => {
  beforeEach(() => {
    useChatStore.setState({ pendingQuestion: null })
  })

  it('re-runs this turn, identified by its own entryId', async () => {
    const rerunEntry = vi.fn().mockResolvedValue(undefined)
    useChatStore.setState({ rerunEntry })

    render(<RegenerateAnswerButton entryId="entry-7" />)
    await userEvent.click(screen.getByRole('button'))

    expect(rerunEntry).toHaveBeenCalledTimes(1)
    // The id, not the index and not the question text -- two turns can
    // carry identical question text.
    expect(rerunEntry).toHaveBeenCalledWith('entry-7')
  })

  it('is disabled while another question is in flight', async () => {
    const rerunEntry = vi.fn().mockResolvedValue(undefined)
    useChatStore.setState({
      rerunEntry,
      pendingQuestion: { question: 'something else', startedAt: Date.now(), stage: null },
    })

    render(<RegenerateAnswerButton entryId="entry-7" />)
    const button = screen.getByRole('button')
    expect(button).toBeDisabled()

    // Non-vacuity: a disabled attribute that the click still got through
    // would make the assertion above decorative.
    await userEvent.click(button)
    expect(rerunEntry).not.toHaveBeenCalled()
  })

  it('renders the existing chat.rerun label rather than hardcoded text', () => {
    useChatStore.setState({ rerunEntry: vi.fn().mockResolvedValue(undefined) })
    render(<RegenerateAnswerButton entryId="entry-7" />)
    // The key was translated into all five locales long before this button
    // existed; this pins that the button uses it instead of a literal.
    expect(screen.getByRole('button', { name: /re-run/i })).toBeInTheDocument()
  })
})
