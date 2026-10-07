import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '@/components/ui/toast'
import { newHistoryEntry } from '@/lib/history'
import { askState } from '@/test/analyticsFixtures'
import { useChatStore } from '@/store/chatStore'
import { Chat } from './Chat'

function getScrollContainer(container: HTMLElement): HTMLElement {
  return container.querySelector('.overflow-y-auto') as HTMLElement
}

/** `aria-hidden="true"` makes testing-library compute an empty accessible
 * name for the element (it's excluded from the a11y tree, name and all),
 * so `getByRole('button', { name: ... })` can't find the button while
 * it's hidden -- this looks it up by its (always-present) `aria-label`
 * attribute directly instead, regardless of visibility. */
function getScrollToBottomButton(container: HTMLElement): HTMLElement {
  return container.querySelector('button[aria-label="Scroll to latest message"]') as HTMLElement
}

/** jsdom never actually lays out content, so `scrollHeight`/`scrollTop`/
 * `clientHeight` are all 0 by default -- stands in for real layout, same
 * pattern `useScrollToBottom.test.ts` and `scrolling-text.test.tsx` both
 * already use for their own overflow/scroll measurement. */
function mockScrollState(
  el: HTMLElement,
  { scrollHeight, scrollTop, clientHeight }: { scrollHeight: number; scrollTop: number; clientHeight: number },
) {
  Object.defineProperty(el, 'scrollHeight', { configurable: true, value: scrollHeight })
  Object.defineProperty(el, 'scrollTop', { configurable: true, value: scrollTop })
  Object.defineProperty(el, 'clientHeight', { configurable: true, value: clientHeight })
}

function rerenderChat(rerender: (ui: ReactElement) => void) {
  rerender(
    <QueryClientProvider client={new QueryClient()}>
      <ToastProvider>
        <Chat />
      </ToastProvider>
    </QueryClientProvider>,
  )
}

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
  // useScrollToBottom's own scroll-position measurement is
  // rAF-throttled (see its own docstring) -- a real browser's rAF is
  // asynchronous, so without this, a `fireEvent.scroll` inside a
  // synchronous `act(...)` block would leave the measurement pending
  // past the assertion. Runs the callback synchronously instead, same
  // as useScrollToBottom.test.ts's own stub.
  vi.stubGlobal(
    'requestAnimationFrame',
    vi.fn((cb: FrameRequestCallback) => {
      cb(0)
      return 0
    }),
  )
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
})

afterEach(() => {
  useChatStore.setState(initialState, true)
  vi.unstubAllGlobals()
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
    rerenderChat(rerender)

    expect(screen.queryByRole('button', { name: 'Toggle question list' })).toBeNull()
  })

  it('gives the composer the same left inset TurnCard applies to its answer content', () => {
    useChatStore.setState({ queryHistory: [makeEntry('First'), makeEntry('Second')] } as never)
    const { container } = renderChat()

    const composerWrapper = container.querySelector('textarea')?.closest('.sticky')
    expect(composerWrapper).not.toBeNull()
    expect(composerWrapper?.className).toMatch(/\bpl-10\b/)
  })

  describe('Jump to Bottom button', () => {
    it('is hidden while already at the bottom (the default, and jsdom\'s own default scroll measurements)', () => {
      useChatStore.setState({ queryHistory: [makeEntry('First'), makeEntry('Second')] } as never)
      const { container } = renderChat()
      expect(getScrollToBottomButton(container)).toHaveAttribute('aria-hidden', 'true')
    })

    it('appears once scrolled away from the bottom, and clicking it smoothly returns to the latest content', async () => {
      useChatStore.setState({ queryHistory: [makeEntry('First'), makeEntry('Second')] } as never)
      const { container } = renderChat()
      const scrollEl = getScrollContainer(container)
      mockScrollState(scrollEl, { scrollHeight: 2000, scrollTop: 200, clientHeight: 600 })
      const scrollToSpy = vi.spyOn(scrollEl, 'scrollTo')

      act(() => {
        fireEvent.scroll(scrollEl)
      })

      const button = screen.getByRole('button', { name: 'Scroll to latest message' })
      expect(button).toHaveAttribute('aria-hidden', 'false')

      await userEvent.click(button)
      expect(scrollToSpy).toHaveBeenCalledWith({ top: 2000, behavior: 'smooth' })
    })

    it('hides again once the user scrolls back to the bottom themselves', () => {
      useChatStore.setState({ queryHistory: [makeEntry('First'), makeEntry('Second')] } as never)
      const { container } = renderChat()
      const scrollEl = getScrollContainer(container)

      mockScrollState(scrollEl, { scrollHeight: 2000, scrollTop: 200, clientHeight: 600 })
      act(() => {
        fireEvent.scroll(scrollEl)
      })
      expect(getScrollToBottomButton(container)).toHaveAttribute('aria-hidden', 'false')

      mockScrollState(scrollEl, { scrollHeight: 2000, scrollTop: 2000, clientHeight: 600 })
      act(() => {
        fireEvent.scroll(scrollEl)
      })
      expect(getScrollToBottomButton(container)).toHaveAttribute('aria-hidden', 'true')
    })

    it('does not forcibly scroll back down when a new turn arrives while the user has scrolled up', () => {
      useChatStore.setState({ queryHistory: [makeEntry('First'), makeEntry('Second')] } as never)
      const { container, rerender } = renderChat()
      const scrollEl = getScrollContainer(container)
      mockScrollState(scrollEl, { scrollHeight: 2000, scrollTop: 200, clientHeight: 600 })
      act(() => {
        fireEvent.scroll(scrollEl)
      })
      const scrollToSpy = vi.spyOn(scrollEl, 'scrollTo')

      useChatStore.setState({
        queryHistory: [makeEntry('First'), makeEntry('Second'), makeEntry('Third')],
      } as never)
      rerenderChat(rerender)

      expect(scrollToSpy).not.toHaveBeenCalled()
      expect(screen.getByRole('button', { name: 'Scroll to latest message' })).toHaveAttribute('aria-hidden', 'false')
    })

    it('still auto-follows a new turn when the user was already at the bottom', () => {
      useChatStore.setState({ queryHistory: [makeEntry('First')] } as never)
      const { container, rerender } = renderChat()
      const scrollEl = getScrollContainer(container)
      const scrollToSpy = vi.spyOn(scrollEl, 'scrollTo')

      useChatStore.setState({ queryHistory: [makeEntry('First'), makeEntry('Second')] } as never)
      mockScrollState(scrollEl, { scrollHeight: 900, scrollTop: 0, clientHeight: 600 })
      rerenderChat(rerender)

      expect(scrollToSpy).toHaveBeenCalledWith({ top: 900, behavior: 'smooth' })
    })

    it('jumps to the bottom immediately when switching to a different conversation, even if scrolled up in the previous one', () => {
      useChatStore.setState({
        activeConversationId: 'conv-a',
        queryHistory: [makeEntry('First'), makeEntry('Second')],
      } as never)
      const { container, rerender } = renderChat()
      const scrollEl = getScrollContainer(container)
      mockScrollState(scrollEl, { scrollHeight: 2000, scrollTop: 200, clientHeight: 600 })
      act(() => {
        fireEvent.scroll(scrollEl)
      })
      const scrollToSpy = vi.spyOn(scrollEl, 'scrollTo')

      useChatStore.setState({
        activeConversationId: 'conv-b',
        queryHistory: [makeEntry('A different conversation entirely')],
      } as never)
      rerenderChat(rerender)

      expect(scrollToSpy).toHaveBeenCalledWith({ top: 2000, behavior: 'auto' })
    })
  })
})
