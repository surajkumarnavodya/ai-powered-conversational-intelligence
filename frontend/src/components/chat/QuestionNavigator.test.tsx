import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
  return { ...result, containerRef }
}

/** The flyout is only ever mounted while open -- every test that needs to
 * see the question list opens it first via the toggle button, exactly like
 * a click would in the real app. */
function openNavigator() {
  const toggle = screen.getByRole('button', { name: 'Toggle question list' })
  fireEvent.click(toggle)
  return toggle
}

let scrollIntoViewSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  scrollIntoViewSpy = vi.spyOn(HTMLElement.prototype, 'scrollIntoView').mockImplementation(() => {})
})

afterEach(() => {
  scrollIntoViewSpy.mockRestore()
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

  it('collapses again when the toggle is activated a second time', () => {
    renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    const toggle = openNavigator()
    expect(screen.getByRole('navigation', { name: 'Questions' })).toBeInTheDocument()

    fireEvent.click(toggle)
    expect(screen.queryByRole('navigation', { name: 'Questions' })).not.toBeInTheDocument()
  })

  it('expands on hover and collapses when the pointer leaves', () => {
    const { container } = renderNavigator([makeEntry('First question'), makeEntry('Second question')])
    const wrapper = container.querySelector('button')?.parentElement as HTMLElement

    fireEvent.mouseEnter(wrapper)
    expect(screen.getByRole('navigation', { name: 'Questions' })).toBeInTheDocument()

    fireEvent.mouseLeave(wrapper)
    expect(screen.queryByRole('navigation', { name: 'Questions' })).not.toBeInTheDocument()
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

  it('shows a truncated single-line label, never the full question text, as the visible label', () => {
    const full =
      'Can you show me the top five companies by annual revenue in India and compare them with last year?'
    renderNavigator([makeEntry(full), makeEntry('Second question')])
    openNavigator()

    const nav = screen.getByRole('navigation', { name: 'Questions' })
    const firstButton = within(nav).getAllByRole('button')[0]
    expect(firstButton.textContent).not.toBe(full)
    expect(firstButton.textContent?.length).toBeLessThan(full.length)
  })

  it('exposes the full original question through an accessible tooltip (hover and focus)', async () => {
    const full = 'What was total revenue by region for the last fiscal quarter, broken down by product line?'
    renderNavigator([makeEntry(full), makeEntry('Second question')])
    openNavigator()

    const nav = screen.getByRole('navigation', { name: 'Questions' })
    const firstButton = within(nav).getAllByRole('button')[0]

    firstButton.focus()
    await waitFor(() => {
      expect(screen.getByText(full)).toBeInTheDocument()
    })
  })

  it('never mutates the original question text stored in history', () => {
    const full = 'Show revenue by region for last year compared to this year in a single combined view'
    const entries = [makeEntry(full), makeEntry('Second question')]
    renderNavigator(entries)
    openNavigator()

    expect(entries[0].question).toBe(full)
  })

  it('does not display the entire question paragraph in the navigator', () => {
    const full = 'word '.repeat(60).trim()
    renderNavigator([makeEntry(full), makeEntry('Second question')])
    openNavigator()

    const nav = screen.getByRole('navigation', { name: 'Questions' })
    const firstButton = within(nav).getAllByRole('button')[0]
    expect(firstButton.textContent?.length ?? 0).toBeLessThan(full.length / 2)
  })

  it('navigates duplicate questions to their own distinct message by stable id, not by text', () => {
    const a = makeEntry('Show revenue by company')
    const b = makeEntry('Show revenue by company')
    expect(a.entryId).not.toBe(b.entryId)
    renderNavigator([a, b])
    openNavigator()

    const nav = screen.getByRole('navigation', { name: 'Questions' })
    const buttons = within(nav).getAllByRole('button')

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
    const nav = screen.getByRole('navigation', { name: 'Questions' })

    fireEvent.click(within(nav).getAllByRole('button')[0])

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
    const nav = screen.getByRole('navigation', { name: 'Questions' })

    for (const button of within(nav).getAllByRole('button')) {
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
})
