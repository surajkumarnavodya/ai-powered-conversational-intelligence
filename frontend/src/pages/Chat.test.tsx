import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ToastProvider } from '@/components/ui/toast'
import { newHistoryEntry } from '@/lib/history'
import { askState } from '@/test/analyticsFixtures'
import { useChatStore } from '@/store/chatStore'
import { Chat } from './Chat'

/** Keeps each turn's own render path minimal (no SQL panel, no results
 * table, no chart) -- this file exists to prove Chat.tsx wires the
 * QuestionNavigator and the composer-alignment fix correctly, not to
 * re-exercise TurnCard's own rendering, which has its own test file. */
function makeEntry(question: string) {
  return newHistoryEntry(question, askState({ status: 'rejected', rejection_message: 'Please rephrase.' }), 500)
}

function renderChat() {
  const queryClient = new QueryClient()
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <Chat />
      </ToastProvider>
    </QueryClientProvider>,
  )
}

const initialState = useChatStore.getState()

beforeEach(() => {
  useChatStore.setState(initialState, true)
})

afterEach(() => {
  useChatStore.setState(initialState, true)
})

describe('Chat page', () => {
  it('does not render the Question Navigator toggle for zero or one question', () => {
    useChatStore.setState({ queryHistory: [] } as never)
    renderChat()
    expect(screen.queryByRole('button', { name: 'Toggle question list' })).toBeNull()
  })

  it('renders the collapsed Question Navigator toggle once there are two or more questions, with the list hidden until expanded', () => {
    useChatStore.setState({ queryHistory: [makeEntry('First'), makeEntry('Second')] } as never)
    renderChat()
    expect(screen.getByRole('button', { name: 'Toggle question list' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Questions' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Toggle question list' }))
    expect(screen.getByRole('navigation', { name: 'Questions' })).toBeInTheDocument()
  })

  it('removes the Question Navigator toggle again after switching to a conversation with only one question', () => {
    useChatStore.setState({ queryHistory: [makeEntry('First'), makeEntry('Second')] } as never)
    const { rerender } = renderChat()
    expect(screen.getByRole('button', { name: 'Toggle question list' })).toBeInTheDocument()

    useChatStore.setState({ queryHistory: [makeEntry('Only one now')] } as never)
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <ToastProvider>
          <Chat />
        </ToastProvider>
      </QueryClientProvider>,
    )

    expect(screen.queryByRole('button', { name: 'Toggle question list' })).toBeNull()
  })

  it('gives the composer the same left inset TurnCard applies to its answer content', () => {
    useChatStore.setState({ queryHistory: [makeEntry('First'), makeEntry('Second')] } as never)
    const { container } = renderChat()

    const composerWrapper = container.querySelector('textarea')?.closest('.sticky')
    expect(composerWrapper).not.toBeNull()
    expect(composerWrapper?.className).toMatch(/\bpl-10\b/)
  })
})
