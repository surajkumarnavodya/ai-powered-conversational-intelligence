import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { createRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { newHistoryEntry } from '@/lib/history'
import { askState } from '@/test/analyticsFixtures'
import { QuestionNavigator } from './QuestionNavigator'

function makeEntry(question: string) {
  return newHistoryEntry(question, askState(), 1200)
}

/** Mounts each entry's target element (`turn-${entryId}`) inside the same
 * scroll container the navigator is given, mirroring how Chat.tsx actually
 * nests TurnCard under the scroll region -- close enough to the real DOM
 * shape for `scrollIntoView`/IntersectionObserver wiring to mean something,
 * without rendering the real (heavy) TurnCard. */
function renderNavigator(entries: ReturnType<typeof makeEntry>[]) {
  const containerRef = createRef<HTMLDivElement>()
  const result = render(
    <div>
      <div ref={containerRef} data-testid="scroll-container">
        {entries.map((entry) => (
          <div key={entry.entryId} id={`turn-${entry.entryId}`}>
            {entry.question}
          </div>
        ))}
      </div>
      <QuestionNavigator entries={entries} scrollContainerRef={containerRef} />
    </div>,
  )
  // The hover target is the navigator's own wrapper -- the element that
  // contains both the toggle and (once open) the panel, so travelling
  // between them never leaves it. Resolved from the toggle rather than by
  // a brittle container query.
  const wrapper = result.container.querySelector('button')?.parentElement as HTMLElement
  return { ...result, containerRef, wrapper }
}

/** The grace period `QuestionNavigator` waits before collapsing. Mirrors
 * the component's own `CLOSE_DELAY_MS`; kept here as a literal so a change
 * to the component's timing shows up as a failing test rather than a test
 * that silently retunes itself. */
const CLOSE_DELAY_MS = 180

/** Drives React's synthetic pointer-enter/leave the way a real mouse does.
 *
 * `fireEvent.mouseEnter`/`mouseLeave` are deliberately NOT used: they
 * dispatch a non-bubbling native event that React's enter/leave plugin
 * never sees, so a test written with them passes whether the handler
 * exists or not -- which is exactly how the previous "does not open on
 * hover" test stayed green after the behaviour changed. `pointerType` is
 * set explicitly because the component keys its touch guard on it. */
function hoverIn(element: HTMLElement) {
  fireEvent.pointerEnter(element, { pointerType: 'mouse' })
}

function hoverOut(element: HTMLElement) {
  fireEvent.pointerLeave(element, { pointerType: 'mouse', relatedTarget: document.body })
}

/** The flyout is only ever mounted while open -- every test that needs to
 * see the question list opens it first via the toggle button, exactly like
 * a click would in the real app. */
function openNavigator() {
  const toggle = screen.getByRole('button', { name: 'Toggle question list' })
  fireEvent.click(toggle)
  return toggle
}

/** A controllable stand-in for the real `IntersectionObserver`, so a test
 * can say "this turn just crossed the scrollspy band" without a layout
 * engine. Deliberately the same shape as the one in
 * `hooks/useActiveElementId.test.ts` -- that one proves the hook's own
 * contract in isolation; this one is what lets the *navigator's* own
 * active-item behavior (highlight, aria-current, keeping the active item
 * visible inside the panel) be asserted through the real component tree. */
class FakeIntersectionObserver {
  static instances: FakeIntersectionObserver[] = []
  readonly callback: IntersectionObserverCallback
  disconnected = false

  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback
    FakeIntersectionObserver.instances.push(this)
  }

  observe() {}
  unobserve() {}
  disconnect() {
    this.disconnected = true
  }
  takeRecords(): IntersectionObserverEntry[] {
    return []
  }
}

/** Makes `turn-${entryId}` the *only* turn crossing the scrollspy band,
 * via the most recently constructed observer (the live one -- earlier
 * instances have been disconnected by the hook's own effect cleanup).
 *
 * Every other turn is reported as having left the band in the same
 * callback, which is what a real browser does when scrolling moves one
 * element in and another out. Reporting only the entering element would
 * leave the previous one in the hook's intersecting set, and the hook
 * deliberately resolves ties by "last in DOM order wins" -- so scrolling
 * *upward* would appear not to move the active item at all. */
function markVisible(entryId: string) {
  const observer = FakeIntersectionObserver.instances.at(-1)
  if (!observer) throw new Error('no IntersectionObserver was constructed')
  const target = document.getElementById(`turn-${entryId}`)
  if (!target) throw new Error(`no element with id turn-${entryId}`)
  const records = Array.from(document.querySelectorAll('[id^="turn-"]')).map((element) => ({
    target: element,
    isIntersecting: element === target,
  })) as unknown as IntersectionObserverEntry[]
  act(() => {
    observer.callback(records, observer as unknown as IntersectionObserver)
  })
}

let originalIntersectionObserver: typeof IntersectionObserver
let scrollIntoViewSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  scrollIntoViewSpy = vi.spyOn(HTMLElement.prototype, 'scrollIntoView').mockImplementation(() => {})
  FakeIntersectionObserver.instances = []
  originalIntersectionObserver = window.IntersectionObserver
  window.IntersectionObserver = FakeIntersectionObserver as unknown as typeof IntersectionObserver
})

afterEach(() => {
  scrollIntoViewSpy.mockRestore()
  window.IntersectionObserver = originalIntersectionObserver
})

describe('QuestionNavigator', () => {
  it('renders nothing for a single question (case 1)', () => {
    const { container } = renderNavigator([makeEntry('Only question')])
    expect(container.querySelector('button')).toBeNull()
  })

  it('renders nothing for zero questions', () => {
    const { container } = renderNavigator([])
    expect(container.querySelector('button')).toBeNull()
  })

  it('shows only the compact toggle control by default, with no question list', () => {
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])

    expect(screen.getByRole('button', { name: 'Toggle question list' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Questions' })).not.toBeInTheDocument()
  })

  it('expands to show every question once the toggle is activated', () => {
    const entries = [makeEntry('First question'), makeEntry('Second question'), makeEntry('Third question')]
    renderNavigator(entries)
    openNavigator()

    const nav = screen.getByRole('navigation', { name: 'Questions' })
    expect(within(nav).getAllByRole('listitem')).toHaveLength(3)
  })

  it('shows ten rows before scrolling, and still drops none', () => {
    // jsdom computes no layout, so the assertion is on the contract that
    // produces the behaviour: a bounded height plus vertical overflow on
    // the one scroll region, with every entry still in the DOM. A test
    // that only counted rendered rows would pass just as happily if the
    // list were *truncated* to ten, which is the opposite of what was
    // asked for.
    const entries = Array.from({ length: 24 }, (_, i) => makeEntry(`Question ${i + 1}`))
    renderNavigator(entries)
    openNavigator()

    const list = screen.getByTestId('question-navigator-list')

    expect(list.className).toContain('overflow-y-auto')
    // Ten rows: 10 x (1rem line box + 0.375rem x 2 padding) = 17.5rem,
    // plus the list's own 0.5rem `py-1` under `box-border` = 18rem.
    expect(list.className).toContain('max-h-[min(60vh,18rem)]')
    expect(within(list).getAllByRole('listitem')).toHaveLength(24)
  })

  it('renders all ten rows, and the tenth, without scrolling to reach them', () => {
    // The point of the taller panel: ten questions fit at once. Nothing
    // here may be clipped away or virtualized out of the DOM.
    const entries = Array.from({ length: 10 }, (_, i) => makeEntry(`Question ${i + 1}`))
    renderNavigator(entries)
    openNavigator()

    const list = screen.getByTestId('question-navigator-list')
    expect(within(list).getAllByRole('listitem')).toHaveLength(10)
    expect(within(list).getByText('Question 10')).toBeInTheDocument()
  })

  it('adapts its height to a short viewport rather than overflowing off-screen', () => {
    // Ten rows need 288px, which a landscape phone or a short laptop
    // window does not have -- the `min()` is what makes the list take 60%
    // of the viewport and scroll instead.
    renderNavigator(Array.from({ length: 15 }, (_, i) => makeEntry(`Q${i + 1}`)))
    openNavigator()

    const list = screen.getByTestId('question-navigator-list')
    expect(list.className).toMatch(/max-h-\[min\(60vh,/)
    expect(list.className).toContain('overflow-y-auto')
  })

  it('keeps every question reachable by scrolling rather than hiding the overflow', () => {
    const entries = Array.from({ length: 20 }, (_, i) => makeEntry(`Question ${i + 1}`))
    renderNavigator(entries)
    openNavigator()

    const list = screen.getByTestId('question-navigator-list')

    expect(list.className).not.toContain('overflow-hidden')
    expect(within(list).getByText('Question 20')).toBeInTheDocument()
  })

  it('still bounds the panel when there are fewer than five questions', () => {
    // The cap is a maximum, not a fixed height -- a short list must not
    // leave empty space below it.
    renderNavigator([makeEntry('One'), makeEntry('Two')])
    openNavigator()

    const list = screen.getByTestId('question-navigator-list')

    expect(list.className).toContain('max-h-')
    // A standalone `h-[...]`, not the `h-[` inside `max-h-[`.
    expect(list.className).not.toMatch(/(^|\s)h-\[/)
  })

  it('collapses again when the toggle is activated a second time', () => {
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    const toggle = openNavigator()
    expect(screen.getByRole('navigation', { name: 'Questions' })).toBeInTheDocument()

    fireEvent.click(toggle)
    expect(screen.queryByRole('navigation', { name: 'Questions' })).not.toBeInTheDocument()
  })

  it('opens on hover, with no click needed', () => {
    const { wrapper } = renderNavigator([makeEntry('First question'), makeEntry('Second question')])

    hoverIn(wrapper)

    expect(screen.getByRole('navigation', { name: 'Questions' })).toBeInTheDocument()
  })

  it('closes once the pointer has been away for the grace period', () => {
    vi.useFakeTimers()
    try {
      const { wrapper } = renderNavigator([makeEntry('First question'), makeEntry('Second')])
      hoverIn(wrapper)

      hoverOut(wrapper)
      // Still open immediately after leaving -- the close is deferred.
      expect(screen.getByRole('navigation', { name: 'Questions' })).toBeInTheDocument()

      act(() => void vi.advanceTimersByTime(CLOSE_DELAY_MS))

      expect(screen.queryByRole('navigation', { name: 'Questions' })).not.toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not flicker shut when the pointer re-enters during the grace period', () => {
    // The regression that forced hover-to-open to be reverted once before:
    // a pointer travelling from the icon to the list crossed a gap that
    // belonged to neither, fired leave, and closed the panel before it
    // could be reached. The grace period is what absorbs that -- any
    // re-entry before it elapses cancels the pending close outright.
    vi.useFakeTimers()
    try {
      const { wrapper } = renderNavigator([makeEntry('First question'), makeEntry('Second')])
      hoverIn(wrapper)
      const panelBefore = screen.getByTestId('question-navigator-panel')
      hoverOut(wrapper)

      act(() => void vi.advanceTimersByTime(CLOSE_DELAY_MS - 20))
      hoverIn(wrapper)
      // Well past the original deadline: the cancelled timer must not fire.
      act(() => void vi.advanceTimersByTime(CLOSE_DELAY_MS * 4))

      // Asserting on node *identity*, not merely "a panel is present":
      // closing and reopening would also leave a panel on screen, but as a
      // different element -- which is precisely the flicker being ruled
      // out. The panel must never have unmounted.
      expect(screen.getByTestId('question-navigator-panel')).toBe(panelBefore)
    } finally {
      vi.useRealTimers()
    }
  })

  it('ignores hover from a touch pointer, so a tap does not open-then-toggle shut', () => {
    // A touch tap synthesizes mouseenter *before* its click. Honouring it
    // would open the panel on enter and let the click immediately toggle
    // it shut, so a tap would appear to do nothing at all.
    const { wrapper } = renderNavigator([makeEntry('First question'), makeEntry('Second')])

    fireEvent.pointerEnter(wrapper, { pointerType: 'touch' })

    expect(screen.queryByRole('navigation', { name: 'Questions' })).not.toBeInTheDocument()
  })

  it('opens and closes on tap for a touch user', () => {
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    const toggle = screen.getByRole('button', { name: 'Toggle question list' })

    fireEvent.click(toggle)
    expect(screen.getByRole('navigation', { name: 'Questions' })).toBeInTheDocument()

    fireEvent.click(toggle)
    expect(screen.queryByRole('navigation', { name: 'Questions' })).not.toBeInTheDocument()
  })

  it('dismisses on a tap outside, the touch counterpart to the pointer leaving', () => {
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    openNavigator()

    fireEvent.pointerDown(document.body)

    expect(screen.queryByRole('navigation', { name: 'Questions' })).not.toBeInTheDocument()
  })

  it('stays open for a tap inside the panel, so selecting a question does not dismiss it', () => {
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    openNavigator()

    const firstItem = within(screen.getByRole('list')).getAllByRole('button')[0]
    fireEvent.pointerDown(firstItem)

    expect(screen.getByRole('navigation', { name: 'Questions' })).toBeInTheDocument()
  })

  it('stays open when focus moves away -- blur never auto-hides it', () => {
    // Deliberately unchanged by the hover work: closing on blur would shut
    // the panel the moment a keyboard user tabbed into the list.
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    const toggle = openNavigator()

    fireEvent.blur(toggle)

    expect(screen.getByRole('navigation', { name: 'Questions' })).toBeInTheDocument()
  })

  it('has no close button -- dismissal is automatic', () => {
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    openNavigator()

    expect(screen.queryByRole('button', { name: /close/i })).not.toBeInTheDocument()
    const panel = screen.getByTestId('question-navigator-panel')
    // The only buttons inside the panel are the question rows themselves.
    expect(within(panel).getAllByRole('button')).toHaveLength(2)
  })

  it('keeps the panel header to just the title now that nothing sits opposite it', () => {
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    openNavigator()

    const panel = screen.getByTestId('question-navigator-panel')
    const header = panel.firstElementChild as HTMLElement

    expect(header.textContent).toBe('Questions')
    expect(header.querySelector('button')).toBeNull()
  })

  it('returns focus to the toggle after Escape, rather than stranding it', () => {
    const { wrapper } = renderNavigator([makeEntry('First question'), makeEntry('Second')])
    const toggle = openNavigator()

    fireEvent.keyDown(wrapper, { key: 'Escape' })

    expect(document.activeElement).toBe(toggle)
  })

  it('anchors the panel to the right edge and reserves the icon its own lane', () => {
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    openNavigator()

    const panel = screen.getByTestId('question-navigator-panel')

    // `right-0` -- flush to the wrapper's right edge (and so to the
    // screen's right margin), not `right-full` beside the icon. This is
    // both the requested full-width look and what removes the gap a
    // pointer used to fall through.
    expect(panel.className).toMatch(/(^|\s)right-0(\s|$)/)
    expect(panel.className).not.toMatch(/right-full/)
    // The icon now overlays the panel, so the panel keeps a 36px lane
    // clear for it -- otherwise a question row would run underneath it.
    expect(panel.className).toMatch(/(^|\s)pr-9(\s|$)/)
  })

  it('keeps the icon above the panel it now overlays', () => {
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    const toggle = openNavigator()
    const panel = screen.getByTestId('question-navigator-panel')

    // Without this the panel would paint over its own trigger: the panel
    // is absolutely positioned and comes later in the DOM.
    expect(toggle.className).toMatch(/z-30/)
    expect(panel.className).not.toMatch(/z-[34]\d/)
  })

  it('clamps the panel to the viewport on a phone and widens with the breakpoints', () => {
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    openNavigator()

    const panel = screen.getByTestId('question-navigator-panel')

    // Phone: never wider than the viewport minus the 1rem gutter each side.
    expect(panel.className).toContain('w-[calc(100vw-2rem)]')
    expect(panel.className).toContain('max-w-[calc(100vw-2rem)]')
    // ...stepping up to fixed widths once there is room for them.
    expect(panel.className).toContain('sm:w-80')
    expect(panel.className).toContain('lg:w-96')
    expect(panel.className).toContain('xl:w-[26rem]')
  })

  it('is available on small viewports, not desktop-only', () => {
    // It used to be `xl:flex`/`hidden`, because the old panel opened to
    // the left of the icon and would land on the answer text. The panel
    // now opens flush right and clamps to the viewport, so phones and
    // tablets -- where scrolling a long conversation by hand is worst --
    // get it too.
    const { wrapper } = renderNavigator([makeEntry('First question'), makeEntry('Second')])

    expect(wrapper.className).not.toMatch(/(^|\s)hidden(\s|$)/)
    expect(wrapper.className).not.toMatch(/xl:flex/)
    expect(wrapper.className).toMatch(/(^|\s)flex(\s|$)/)
  })

  it('animates in on mount, where a plain transition could not', () => {
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    openNavigator()

    // The panel is mounted only while open, so there is no previous frame
    // to transition from -- a keyframe animation is the one thing that
    // runs on mount. index.css caps its duration under reduced motion.
    expect(screen.getByTestId('question-navigator-panel').className).toContain('animate-flyout-in')
  })

  it('keeps the decorative rail from swallowing clicks over the conversation', () => {
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    const toggle = screen.getByRole('button', { name: 'Toggle question list' })
    const wrapper = toggle.parentElement as HTMLElement

    // The icon itself stays visually small (h-9, 36px) inside a much
    // taller rail that marks where the control is...
    expect(toggle.className).toMatch(/h-9/)
    expect(wrapper.className).toMatch(/h-44/)
    // ...but that rail is no longer a hit target now that nothing depends
    // on the pointer staying inside it, so the column must not intercept
    // clicks meant for the conversation underneath. Only the real controls
    // opt back in.
    expect(wrapper.className).toMatch(/pointer-events-none/)
    expect(toggle.className).toMatch(/pointer-events-auto/)
  })

  it('marks the toggle control as expanded/collapsed via aria-expanded', () => {
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    const toggle = screen.getByRole('button', { name: 'Toggle question list' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')

    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
  })

  it('closes on Escape', () => {
    const { container } = renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    openNavigator()
    expect(screen.getByRole('navigation', { name: 'Questions' })).toBeInTheDocument()

    const wrapper = container.querySelector('button')?.parentElement as HTMLElement
    fireEvent.keyDown(wrapper, { key: 'Escape' })
    expect(screen.queryByRole('navigation', { name: 'Questions' })).not.toBeInTheDocument()
  })

  it('renders the full question text as the item label -- never pre-shortened -- so it can be revealed on hover', () => {
    // ScrollingText (the hover-to-reveal-the-rest treatment) needs the
    // complete text actually present in the DOM; what keeps the resting
    // label compact is CSS clipping (see the next test), not a
    // programmatically shortened string the way `shortenQuestion` used to
    // produce for this same label.
    const full =
      'Can you show me the top five companies by annual revenue in India and compare them with last year?'
    renderNavigator([makeEntry(full), makeEntry('Second question')])
    openNavigator()

    const firstButton = within(screen.getByRole('list')).getAllByRole('button')[0]
    expect(firstButton.textContent).toContain(full)
  })

  it('clips each item label at rest via overflow-hidden, rather than relying on a shortened string', () => {
    renderNavigator([makeEntry('word '.repeat(60).trim()), makeEntry('Second question')])
    openNavigator()

    const firstButton = within(screen.getByRole('list')).getAllByRole('button')[0]
    // Document order: [0] the index-number span, [1] ScrollingText's own
    // clipping container, [2] its inner (unclipped) text span.
    const scrollingTextContainer = firstButton.querySelectorAll('span')[1] as HTMLElement
    expect(scrollingTextContainer.className).toMatch(/overflow-hidden/)
  })

  it('exposes the full original question through an accessible tooltip (hover and focus)', async () => {
    const full = 'What was total revenue by region for the last fiscal quarter, broken down by product line?'
    renderNavigator([makeEntry(full), makeEntry('Second question')])
    openNavigator()

    const firstButton = within(screen.getByRole('list')).getAllByRole('button')[0]

    firstButton.focus()
    await waitFor(() => {
      // Scoped to the tooltip's own role, not a bare text match -- the
      // item's own visible label now also contains this exact full text
      // (see the ScrollingText tests above), so a plain `getByText` would
      // ambiguously match both.
      expect(screen.getByRole('tooltip')).toHaveTextContent(full)
    })
  })

  it('never mutates the original question text stored in history', () => {
    const full = 'Show revenue by region for last year compared to this year in a single combined view'
    const entries = [makeEntry(full), makeEntry('Second question')]
    renderNavigator(entries)
    openNavigator()

    expect(entries[0].question).toBe(full)
  })

  it('scrolls a long item label left on hover to reveal the rest, via the shared ScrollingText treatment', () => {
    const full = 'word '.repeat(60).trim()
    renderNavigator([makeEntry(full), makeEntry('Second question')])
    openNavigator()

    const firstButton = within(screen.getByRole('list')).getAllByRole('button')[0]
    const scrollingTextContainer = firstButton.querySelectorAll('span')[1] as HTMLElement
    const textSpan = scrollingTextContainer.querySelector('span') as HTMLElement

    Object.defineProperty(textSpan, 'scrollWidth', { configurable: true, value: 600 })
    Object.defineProperty(scrollingTextContainer, 'clientWidth', { configurable: true, value: 200 })
    fireEvent(window, new Event('resize'))

    // Only re-confirms the wiring (the real entry text reaches ScrollingText
    // and responds to overflow) in this real component tree -- the full
    // forward-and-reset mouse/focus behavior is already exhaustively
    // covered in isolation by scrolling-text.test.tsx. (Firing a raw
    // mouseleave directly on a node nested this deeply in jsdom has no
    // real relatedTarget/position context, so it can also -- spuriously,
    // only in this test environment -- read as leaving the ancestor hover
    // rail that keeps the whole flyout mounted; re-testing the reset here
    // would be testing that jsdom quirk, not this component.)
    fireEvent.mouseEnter(scrollingTextContainer)
    expect(textSpan.style.transform).toBe('translateX(-400px)')
  })

  it('navigates duplicate questions to their own distinct message by stable id, not by text', () => {
    const a = makeEntry('Show revenue by company')
    const b = makeEntry('Show revenue by company')
    expect(a.entryId).not.toBe(b.entryId)
    renderNavigator([a, b])
    openNavigator()

    const buttons = within(screen.getByRole('list')).getAllByRole('button')

    fireEvent.click(buttons[0])
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(1)
    const firstTargetCalledOn = (scrollIntoViewSpy.mock.instances[0] as HTMLElement).id

    fireEvent.click(buttons[1])
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(2)
    const secondTargetCalledOn = (scrollIntoViewSpy.mock.instances[1] as HTMLElement).id

    expect(firstTargetCalledOn).toBe(`turn-${a.entryId}`)
    expect(secondTargetCalledOn).toBe(`turn-${b.entryId}`)
    expect(firstTargetCalledOn).not.toBe(secondTargetCalledOn)
  })

  it('scrolls the main chat with block: start, smooth behavior', () => {
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    openNavigator()
    fireEvent.click(within(screen.getByRole('list')).getAllByRole('button')[0])

    expect(scrollIntoViewSpy).toHaveBeenCalledWith(
      expect.objectContaining({ block: 'start', behavior: 'smooth' }),
    )
  })

  it('skips an entry with empty/invalid question text entirely', () => {
    const entries = [makeEntry('Real question one'), makeEntry('   '), makeEntry('Real question two')]
    renderNavigator(entries)
    openNavigator()

    const nav = screen.getByRole('navigation', { name: 'Questions' })
    expect(within(nav).getAllByRole('listitem')).toHaveLength(2)
  })

  it('updates automatically when a new question is added', () => {
    const entries = [makeEntry('First'), makeEntry('Second')]
    const { rerender, containerRef } = renderNavigator(entries)
    openNavigator()
    const nav = () => screen.getByRole('navigation', { name: 'Questions' })
    expect(within(nav()).getAllByRole('listitem')).toHaveLength(2)

    const withThird = [...entries, makeEntry('Third')]
    rerender(
      <div>
        <div ref={containerRef} data-testid="scroll-container">
          {withThird.map((entry) => (
            <div key={entry.entryId} id={`turn-${entry.entryId}`}>
              {entry.question}
            </div>
          ))}
        </div>
        <QuestionNavigator entries={withThird} scrollContainerRef={containerRef} />
      </div>,
    )

    expect(within(nav()).getAllByRole('listitem')).toHaveLength(3)
  })

  it('has keyboard-reachable, native buttons for each item (Enter/Space activate natively)', () => {
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    openNavigator()
    for (const button of within(screen.getByRole('list')).getAllByRole('button')) {
      expect(button.tagName).toBe('BUTTON')
    }
  })

  it('exposes the navigator region with the recommended accessible name "Questions"', () => {
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    openNavigator()
    expect(screen.getByRole('navigation', { name: 'Questions' })).toBeInTheDocument()
  })

  it('renders a large number of questions without failing (100+)', () => {
    const entries = Array.from({ length: 120 }, (_, i) => makeEntry(`Question number ${i + 1}`))
    renderNavigator(entries)
    openNavigator()

    const nav = screen.getByRole('navigation', { name: 'Questions' })
    expect(within(nav).getAllByRole('listitem')).toHaveLength(120)
  })

  it('bounds the scrollable list region to a max height rather than growing unbounded', () => {
    const entries = Array.from({ length: 50 }, (_, i) => makeEntry(`Question ${i + 1}`))
    renderNavigator(entries)
    openNavigator()

    const list = screen.getByRole('list')
    expect(list.className).toMatch(/max-h-/)
    expect(list.className).toMatch(/overflow-y-auto/)
  })

  // --- Active-question tracking, driven through the real component tree ---

  it('marks the question currently visible in the chat as the active item', () => {
    const entries = [makeEntry('First'), makeEntry('Second'), makeEntry('Third')]
    renderNavigator(entries)
    openNavigator()

    markVisible(entries[1].entryId)

    const buttons = within(screen.getByRole('list')).getAllByRole('button')
    expect(buttons[1]).toHaveAttribute('aria-current', 'true')
    expect(buttons[0]).not.toHaveAttribute('aria-current')
    expect(buttons[2]).not.toHaveAttribute('aria-current')
  })

  it('signals the active item without relying on colour alone (weight + leading indicator)', () => {
    const entries = [makeEntry('First'), makeEntry('Second')]
    renderNavigator(entries)
    openNavigator()
    markVisible(entries[1].entryId)

    const active = within(screen.getByRole('list')).getAllByRole('button')[1]
    // A screen reader gets aria-current; a sighted reader gets a heavier
    // font and a solid left border, not just a different hue.
    expect(active).toHaveAttribute('aria-current', 'true')
    expect(active.className).toMatch(/font-semibold/)
    expect(active.className).toMatch(/border-\[var\(--accent\)\]/)
  })

  it('tracks the active item across duplicate question text by id, not by text', () => {
    const a = makeEntry('Show sales by region')
    const b = makeEntry('Show sales by region')
    renderNavigator([a, b])
    openNavigator()

    markVisible(b.entryId)
    const buttons = within(screen.getByRole('list')).getAllByRole('button')
    expect(buttons[1]).toHaveAttribute('aria-current', 'true')
    expect(buttons[0]).not.toHaveAttribute('aria-current')

    markVisible(a.entryId)
    expect(buttons[0]).toHaveAttribute('aria-current', 'true')
  })

  it('reveals the active item inside the panel when the panel is opened (large conversation)', () => {
    // The regression this covers: the "keep the active item visible" effect
    // used to depend on the active id *changing*. A reader who scrolls down
    // to question 40 and only then opens the panel never changes it, so the
    // list opened scrolled to the top with the active item far out of view.
    const entries = Array.from({ length: 50 }, (_, i) => makeEntry('Question ' + (i + 1)))
    renderNavigator(entries)

    // Active state settles *before* the panel is ever opened.
    markVisible(entries[39].entryId)
    scrollIntoViewSpy.mockClear()

    openNavigator()

    const active = within(screen.getByRole('list')).getAllByRole('button')[39]
    expect(active).toHaveAttribute('aria-current', 'true')
    // `block: 'nearest'` scrolls the navigator's own list region only --
    // never the conversation (that is what clicking an item does).
    expect(scrollIntoViewSpy).toHaveBeenCalledWith({ block: 'nearest' })
    expect(scrollIntoViewSpy.mock.instances).toContain(active)
  })

  it('tears the scrollspy observer down when the navigator unmounts', () => {
    const { unmount } = renderNavigator([makeEntry('First'), makeEntry('Second')])
    expect(FakeIntersectionObserver.instances).toHaveLength(1)
    unmount()
    expect(FakeIntersectionObserver.instances[0].disconnected).toBe(true)
  })

  it('does not leave a second live observer behind when a question is appended', () => {
    const entries = [makeEntry('First'), makeEntry('Second')]
    const { rerender, containerRef } = renderNavigator(entries)
    const withThird = [...entries, makeEntry('Third')]
    rerender(
      <div>
        <div ref={containerRef} data-testid="scroll-container">
          {withThird.map((entry) => (
            <div key={entry.entryId} id={'turn-' + entry.entryId}>
              {entry.question}
            </div>
          ))}
        </div>
        <QuestionNavigator entries={withThird} scrollContainerRef={containerRef} />
      </div>,
    )

    const live = FakeIntersectionObserver.instances.filter((o) => !o.disconnected)
    expect(live).toHaveLength(1)
  })

  it('does not re-create the observer on a re-render that leaves the questions unchanged', () => {
    const entries = [makeEntry('First'), makeEntry('Second')]
    const { rerender, containerRef } = renderNavigator(entries)
    expect(FakeIntersectionObserver.instances).toHaveLength(1)

    // Same entries, a fresh array instance -- exactly what Chat.tsx hands
    // down on any unrelated store update (an SQL edit, a chart tweak).
    rerender(
      <div>
        <div ref={containerRef} data-testid="scroll-container">
          {entries.map((entry) => (
            <div key={entry.entryId} id={'turn-' + entry.entryId}>
              {entry.question}
            </div>
          ))}
        </div>
        <QuestionNavigator entries={[...entries]} scrollContainerRef={containerRef} />
      </div>,
    )

    expect(FakeIntersectionObserver.instances).toHaveLength(1)
  })

  // --- Accessibility of the individual items ---

  it('gives each item an explicit "Navigate to question" accessible name', () => {
    const question = 'Show the average order value for each customer'
    renderNavigator([makeEntry(question), makeEntry('Second question')])
    openNavigator()

    expect(
      within(screen.getByRole('list')).getByRole('button', {
        name: 'Navigate to question: ' + question,
      }),
    ).toBeInTheDocument()
  })

  it('names duplicate items identically but keeps them as two separate, independently targeted items', () => {
    const a = makeEntry('Show sales by region')
    const b = makeEntry('Show sales by region')
    renderNavigator([a, b])
    openNavigator()

    const named = within(screen.getByRole('list')).getAllByRole('button', {
      name: 'Navigate to question: Show sales by region',
    })
    expect(named).toHaveLength(2)

    fireEvent.click(named[1])
    expect(scrollIntoViewSpy.mock.instances[0]).toBe(document.getElementById('turn-' + b.entryId))
  })

  it('hides the decorative position number from assistive technology', () => {
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    openNavigator()

    const firstButton = within(screen.getByRole('list')).getAllByRole('button')[0]
    const indexSpan = firstButton.querySelector('span')
    expect(indexSpan?.textContent).toBe('1')
    expect(indexSpan).toHaveAttribute('aria-hidden', 'true')
  })

  it('collapses a multi-line question into one line for the accessible name', () => {
    renderNavigator([makeEntry('Show revenue\n\n  by   region'), makeEntry('Second question')])
    openNavigator()

    expect(
      within(screen.getByRole('list')).getByRole('button', {
        name: 'Navigate to question: Show revenue by region',
      }),
    ).toBeInTheDocument()
  })

  it('keeps emoji and special characters intact in both the label and the accessible name', () => {
    const question = 'Top 5 "products" by revenue > $1M? 📈 <alpha & beta>'
    renderNavigator([makeEntry(question), makeEntry('Second question')])
    openNavigator()

    const button = within(screen.getByRole('list')).getByRole('button', {
      name: 'Navigate to question: ' + question,
    })
    expect(button.textContent).toContain(question)
  })

  // --- Truncation of the resting label ---

  it('ellipsis-truncates a long label at rest rather than clipping it mid-character', () => {
    renderNavigator([makeEntry('word '.repeat(60).trim()), makeEntry('Second question')])
    openNavigator()

    const firstButton = within(screen.getByRole('list')).getAllByRole('button')[0]
    const textSpan = firstButton.querySelectorAll('span')[2] as HTMLElement
    expect(textSpan.style.textOverflow).toBe('ellipsis')
  })

  // --- Historical (reloaded) conversations ---

  it('navigates a reloaded conversation using the server message id it was restored with', () => {
    // A restored turn's entryId is the persisted user-message row id, not a
    // fresh uuid -- the navigator must work off whatever stable id the
    // entry already carries, with no separate "live vs historical" path.
    const restored = [
      { ...makeEntry('Historical first'), entryId: 'srv-msg-101' },
      { ...makeEntry('Historical second'), entryId: 'srv-msg-102' },
    ]
    renderNavigator(restored)
    openNavigator()

    const buttons = within(screen.getByRole('list')).getAllByRole('button')
    expect(buttons).toHaveLength(2)

    fireEvent.click(buttons[1])
    expect(scrollIntoViewSpy.mock.instances[0]).toBe(document.getElementById('turn-srv-msg-102'))
  })

  // --- Graceful degradation ---

  it('does nothing (and does not throw) when the target message is not currently rendered', () => {
    const entries = [makeEntry('First'), makeEntry('Second')]
    const containerRef = createRef<HTMLDivElement>()
    // The navigator is given entries whose turn elements were never
    // mounted -- a virtualised/unmounted message, per the edge-case list.
    render(
      <div>
        <div ref={containerRef} data-testid="scroll-container" />
        <QuestionNavigator entries={entries} scrollContainerRef={containerRef} />
      </div>,
    )
    openNavigator()

    const button = within(screen.getByRole('list')).getAllByRole('button')[0]
    expect(() => fireEvent.click(button)).not.toThrow()
    expect(scrollIntoViewSpy).not.toHaveBeenCalled()
  })

  it('honours prefers-reduced-motion by jumping instead of smooth-scrolling', () => {
    const matchMediaSpy = vi
      .spyOn(window, 'matchMedia')
      .mockImplementation((query: string) => ({ matches: true, media: query }) as MediaQueryList)
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    openNavigator()
    fireEvent.click(within(screen.getByRole('list')).getAllByRole('button')[0])

    expect(scrollIntoViewSpy).toHaveBeenCalledWith(
      expect.objectContaining({ block: 'start', behavior: 'auto' }),
    )
    matchMediaSpy.mockRestore()
  })

  it('stays open after an item is activated, so several questions can be visited in a row', () => {
    const entries = [makeEntry('First'), makeEntry('Second'), makeEntry('Third')]
    renderNavigator(entries)
    openNavigator()

    const list = () => within(screen.getByRole('list')).getAllByRole('button')
    fireEvent.click(list()[0])
    expect(screen.getByRole('navigation', { name: 'Questions' })).toBeInTheDocument()
    fireEvent.click(list()[2])
    expect(scrollIntoViewSpy.mock.instances).toContain(
      document.getElementById('turn-' + entries[2].entryId),
    )
  })

  it('re-activating the already-active item still scrolls to it', () => {
    const entries = [makeEntry('First'), makeEntry('Second')]
    renderNavigator(entries)
    openNavigator()
    markVisible(entries[0].entryId)
    scrollIntoViewSpy.mockClear()

    fireEvent.click(within(screen.getByRole('list')).getAllByRole('button')[0])
    expect(scrollIntoViewSpy).toHaveBeenCalledWith(
      expect.objectContaining({ block: 'start', behavior: 'smooth' }),
    )
  })
})
