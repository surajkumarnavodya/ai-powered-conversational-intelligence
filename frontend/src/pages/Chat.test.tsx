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

  /** One reading column, one pair of edges. The composer and the turns
   * share a single `max-w-chat` container and neither carries a left
   * inset of its own -- the avatar-width `pl-10` that used to step the
   * answer (and then the composer) in from the column is gone, so a
   * question, its answer and the box you type the next one into all line
   * up. A second container here is how they drift apart. */
  it('puts the turns and the composer in the same reading column, with no extra inset on either', () => {
    useChatStore.setState({ queryHistory: [makeEntry('First'), makeEntry('Second')] } as never)
    const { container } = renderChat()

    const composerWrapper = container.querySelector('textarea')?.closest('.sticky')
    expect(composerWrapper).not.toBeNull()
    expect(composerWrapper?.className).not.toMatch(/\bpl-10\b/)

    const column = composerWrapper?.parentElement
    expect(column?.className).toMatch(/\bmax-w-chat\b/)

    // The turns live in that same column, not a container of their own.
    const turn = container.querySelector('[id^="turn-"]')
    expect(turn?.closest('.max-w-chat')).toBe(column)
    expect(turn?.parentElement?.className).not.toMatch(/\bpl-10\b/)
  })

  it('centres the composer while the conversation is empty and drops it to the bottom once a turn exists', () => {
    const { container, rerender } = renderChat()
    expect(container.querySelector('textarea')?.closest('.sticky')?.className).toMatch(/\bmy-auto\b/)

    useChatStore.setState({ queryHistory: [makeEntry('First')] } as never)
    rerenderChat(rerender)

    const wrapper = container.querySelector('textarea')?.closest('.sticky')
    expect(wrapper?.className).toMatch(/\bmt-auto\b/)
    expect(wrapper?.className).not.toMatch(/\bmy-auto\b/)
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

  /** The dotted progress ring on the Jump to Bottom button.
   *
   * Driven entirely by `chatStore.pendingQuestion`, which is the real
   * request lifecycle: set only after a question passes validation, held
   * across routing/retrieval/SQL/execution, and cleared in the store's
   * own `finally` on completion, failure, timeout or abort. These tests
   * drive that state rather than a timer, so a regression that decoupled
   * the ring from the request would fail here. */
  describe('processing ring on the Jump to Bottom button', () => {
    const pending = (stage: string | null = null) => ({
      question: 'How many employees are there?',
      startedAt: Date.now(),
      stage,
    })

    it('is absent while nothing is in flight', () => {
      useChatStore.setState({ queryHistory: [makeEntry('First')], pendingQuestion: null } as never)
      const { container } = renderChat()
      const button = getScrollToBottomButton(container)
      expect(button.className).not.toMatch(/dotted-ring/)
      expect(button).not.toHaveAttribute('aria-busy')
    })

    it('is shown at the bottom while a question is pending, but as an indicator that takes no clicks', () => {
      // The resolution of two opposite reports, so both directions are
      // pinned here at once.
      //
      // It must be SEEN: asking from the bottom is the common case, and
      // hiding the disc there hid the ring for the whole of it ("the
      // arrow icon and round dotted line not appearing when I ask any
      // question").
      //
      // It must not be ACTIONABLE: at the bottom `scrollToBottom` is
      // `el.scrollTo({ top: el.scrollHeight })`, a no-op, and offering an
      // arrow that cannot move the page is the earlier report ("the
      // arrow is not working").
      useChatStore.setState({
        queryHistory: [makeEntry('First')],
        pendingQuestion: pending(),
      } as never)
      const { container } = renderChat()
      const button = getScrollToBottomButton(container)
      const scrollEl = getScrollContainer(container)
      const scrollToSpy = vi.spyOn(scrollEl, 'scrollTo')

      // Seen, with the ring.
      expect(button).toHaveAttribute('aria-hidden', 'false')
      expect(button).toHaveAttribute('aria-busy', 'true')
      expect(button.className).toMatch(/dotted-ring/)

      // But inert: not focusable, reported unavailable, pointer-proof,
      // and wired to nothing -- a click cannot reach a no-op scroll.
      expect(button).toHaveAttribute('aria-disabled', 'true')
      expect(button).toHaveAttribute('tabindex', '-1')
      expect(button.className).toMatch(/pointer-events-none/)
      fireEvent.click(button)
      expect(scrollToSpy).not.toHaveBeenCalled()
    })

    it('becomes a real arrow again once scrolled away, where it can move the page', () => {
      // The other half of the seam above: the same disc, now genuinely
      // actionable, because scrolling down from here does something.
      useChatStore.setState({
        queryHistory: [makeEntry('First'), makeEntry('Second')],
        pendingQuestion: pending(),
      } as never)
      const { container } = renderChat()
      const scrollEl = getScrollContainer(container)
      mockScrollState(scrollEl, { scrollHeight: 2000, scrollTop: 200, clientHeight: 600 })
      act(() => {
        fireEvent.scroll(scrollEl)
      })
      const scrollToSpy = vi.spyOn(scrollEl, 'scrollTo')

      const button = getScrollToBottomButton(container)
      expect(button).not.toHaveAttribute('aria-disabled')
      expect(button).toHaveAttribute('tabindex', '0')
      fireEvent.click(button)
      expect(scrollToSpy).toHaveBeenCalled()
    })

    it('shows the ring once the user scrolls away, where the arrow does move the page', () => {
      useChatStore.setState({
        queryHistory: [makeEntry('First'), makeEntry('Second')],
        pendingQuestion: pending(),
      } as never)
      const { container } = renderChat()
      const scrollEl = getScrollContainer(container)
      mockScrollState(scrollEl, { scrollHeight: 2000, scrollTop: 200, clientHeight: 600 })
      act(() => {
        fireEvent.scroll(scrollEl)
      })

      const button = getScrollToBottomButton(container)
      expect(button).toHaveAttribute('aria-hidden', 'false')
      expect(button).toHaveAttribute('aria-busy', 'true')
      expect(button.className).toMatch(/dotted-ring/)
    })

    it('says "Processing…" until the server reports a stage, then names it', () => {
      useChatStore.setState({
        queryHistory: [makeEntry('First')],
        pendingQuestion: pending(null),
      } as never)
      const { container, rerender } = renderChat()
      expect(getScrollToBottomButton(container)).toHaveAttribute(
        'title',
        'Scroll to latest message — Processing…',
      )

      // A real progress event for a real graph node.
      useChatStore.setState({ pendingQuestion: pending('retrieve_schema') } as never)
      rerenderChat(rerender)
      expect(getScrollToBottomButton(container)).toHaveAttribute(
        'title',
        'Scroll to latest message — Processing — Schema',
      )
    })

    it('stays active across the whole pipeline, not just the first stage', () => {
      useChatStore.setState({
        queryHistory: [makeEntry('First')],
        pendingQuestion: pending('retrieve_schema'),
      } as never)
      const { container, rerender } = renderChat()

      for (const [node, label] of [
        ['generate_sql', 'SQL'],
        ['validate_sql', 'Validation'],
        ['estimate_cost', 'Cost check'],
        ['execute_sql', 'Ready to run'],
      ] as const) {
        useChatStore.setState({ pendingQuestion: pending(node) } as never)
        rerenderChat(rerender)
        const button = getScrollToBottomButton(container)
        expect(button.className).toMatch(/dotted-ring/)
        expect(button).toHaveAttribute('title', `Scroll to latest message — Processing — ${label}`)
      }
    })

    it('keeps spinning on a post-result node rather than dropping to no label', () => {
      // generate_insight runs *after* rows exist; QueryLifecycle
      // deliberately does not show it, so nodeToStage returns null. The
      // request is still in flight, so the ring must stay and fall back
      // to the plain label instead of naming an unmapped node.
      useChatStore.setState({
        queryHistory: [makeEntry('First')],
        pendingQuestion: pending('generate_insight'),
      } as never)
      const { container } = renderChat()
      const button = getScrollToBottomButton(container)
      expect(button.className).toMatch(/dotted-ring/)
      expect(button).toHaveAttribute('title', 'Scroll to latest message — Processing…')
    })

    it('stops when the request completes', () => {
      useChatStore.setState({
        queryHistory: [makeEntry('First')],
        pendingQuestion: pending('execute_sql'),
      } as never)
      const { container, rerender } = renderChat()
      expect(getScrollToBottomButton(container).className).toMatch(/dotted-ring/)

      // What the store does on success: the pending turn becomes history.
      useChatStore.setState({
        queryHistory: [makeEntry('First'), makeEntry('How many employees are there?')],
        pendingQuestion: null,
      } as never)
      rerenderChat(rerender)

      const button = getScrollToBottomButton(container)
      expect(button.className).not.toMatch(/dotted-ring/)
      expect(button).not.toHaveAttribute('aria-busy')
    })

    it('stops on failure, and on a cancelled request, with no stuck spinner', () => {
      for (const terminal of ['failed', 'cancelled'] as const) {
        useChatStore.setState({
          queryHistory: [makeEntry('First')],
          pendingQuestion: pending('generate_sql'),
        } as never)
        const { container, rerender, unmount } = renderChat()
        expect(getScrollToBottomButton(container).className).toMatch(/dotted-ring/)

        // Both paths clear `pendingQuestion` in the store's `finally`;
        // a failure also leaves an error entry behind, a cancel does not.
        useChatStore.setState({
          pendingQuestion: null,
          queryHistory:
            terminal === 'failed' ? [makeEntry('First'), makeEntry('That failed')] : [makeEntry('First')],
        } as never)
        rerenderChat(rerender)

        expect(getScrollToBottomButton(container).className).not.toMatch(/dotted-ring/)
        unmount()
      }
    })

    it('does not leak the ring into a different conversation', () => {
      useChatStore.setState({
        activeConversationId: 'conv-a',
        queryHistory: [makeEntry('First')],
        pendingQuestion: pending('generate_sql'),
      } as never)
      const { container, rerender } = renderChat()
      expect(getScrollToBottomButton(container).className).toMatch(/dotted-ring/)

      // Switching conversation clears the pending turn with it (the store
      // scopes `pendingQuestion` to the active conversation).
      useChatStore.setState({
        activeConversationId: 'conv-b',
        queryHistory: [makeEntry('A different conversation entirely')],
        pendingQuestion: null,
      } as never)
      rerenderChat(rerender)

      const button = getScrollToBottomButton(container)
      expect(button.className).not.toMatch(/dotted-ring/)
      expect(button).not.toHaveAttribute('aria-busy')
    })

    it('still scrolls to the latest message while processing', async () => {
      useChatStore.setState({
        queryHistory: [makeEntry('First'), makeEntry('Second')],
        pendingQuestion: pending('generate_sql'),
      } as never)
      const { container } = renderChat()
      const scrollEl = getScrollContainer(container)
      mockScrollState(scrollEl, { scrollHeight: 2000, scrollTop: 200, clientHeight: 600 })
      act(() => {
        fireEvent.scroll(scrollEl)
      })
      const scrollToSpy = vi.spyOn(scrollEl, 'scrollTo')

      await userEvent.click(screen.getByRole('button', { name: 'Scroll to latest message' }))

      expect(scrollToSpy).toHaveBeenCalledWith({ top: 2000, behavior: 'smooth' })
    })

    it('never activates for an empty or whitespace-only submission', async () => {
      // ChatInput returns before touching the store when the trimmed box
      // is empty, so no request starts and the ring must stay away.
      useChatStore.setState({ queryHistory: [makeEntry('First')], pendingQuestion: null } as never)
      const { container } = renderChat()
      const box = screen.getByLabelText('Ask a question about your data...')

      await userEvent.type(box, '   ')
      await userEvent.keyboard('{Enter}')

      expect(useChatStore.getState().pendingQuestion).toBeNull()
      const button = getScrollToBottomButton(container)
      expect(button.className).not.toMatch(/dotted-ring/)
      expect(button).not.toHaveAttribute('aria-busy')
    })

    it('adds no second announcer for the processing lifecycle', () => {
      useChatStore.setState({
        queryHistory: [makeEntry('First')],
        pendingQuestion: pending('generate_sql'),
      } as never)
      const { container } = renderChat()

      // The guard is about *this* event: the ring must not re-announce
      // what ChatLiveRegion already says once per question.
      //
      // Asserted directly rather than by counting every `aria-live` on the
      // page. That count was always a proxy, and it stopped meaning what
      // it said once `CopyAnswerButton` started owning one region per
      // answer for a different event (empty at rest, so it adds nothing to
      // lifecycle traffic). Counting them would now fail for a reason that
      // has nothing to do with the regression this test exists for.
      const button = getScrollToBottomButton(container)
      expect(button.querySelector('[aria-live]')).toBeNull()
      expect(button.querySelector('[role="status"]')).toBeNull()

      // ChatLiveRegion is still mounted and still the lifecycle announcer.
      expect(container.querySelector('[role="status"][aria-live="polite"]')).toBeInTheDocument()
    })
  })
})

describe('Chat page -- keyboard scrolling', () => {
  /** Arrow keys, PageUp/PageDown, Home and End scroll whichever scrollable
   * element holds focus. A bare `overflow-y-auto` div cannot hold focus,
   * so none of them reached the transcript -- and `<main>` is
   * `overflow-hidden`, so the document could not take them either. The
   * region was mouse-and-touch only.
   *
   * jsdom performs no layout and does not implement native scroll-on-key,
   * so these pin the thing that was actually broken -- the region can take
   * focus and is named -- rather than simulating a scroll jsdom would fake
   * either way. */
  it('makes the transcript focusable, so the keyboard can scroll it', () => {
    useChatStore.setState({ queryHistory: [makeEntry('First')] } as never)
    const { container } = renderChat()
    const scrollEl = getScrollContainer(container)

    expect(scrollEl).toHaveAttribute('tabindex', '0')
    scrollEl.focus()
    expect(scrollEl).toHaveFocus()
  })

  it('names the transcript region, so the new tab stop is not an unlabelled stop', () => {
    useChatStore.setState({ queryHistory: [makeEntry('First')] } as never)
    const { container } = renderChat()
    const scrollEl = getScrollContainer(container)

    expect(scrollEl).toHaveAttribute('role', 'region')
    expect(scrollEl.getAttribute('aria-label')).toBeTruthy()
  })

  it('leaves arrow keys to the browser rather than intercepting them', () => {
    // The fix is focusability, not a key handler. If something here began
    // calling preventDefault on an arrow key, native scrolling would stop
    // again and this is what would catch it.
    useChatStore.setState({ queryHistory: [makeEntry('First')] } as never)
    const { container } = renderChat()
    const scrollEl = getScrollContainer(container)
    scrollEl.focus()

    for (const key of ['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'PageDown', 'Home']) {
      const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })
      scrollEl.dispatchEvent(event)
      expect(event.defaultPrevented, `${key} must stay with the browser`).toBe(false)
    }
  })
})
