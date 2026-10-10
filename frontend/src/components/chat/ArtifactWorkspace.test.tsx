import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { newHistoryEntry, type QueryHistoryEntry } from '@/lib/history'
import { askState } from '@/test/analyticsFixtures'
import { useChatStore } from '@/store/chatStore'
import { ArtifactWorkspace, PinTurnButton, summarizeArtifacts } from './ArtifactWorkspace'

function entry(question: string, overrides: Partial<QueryHistoryEntry> = {}): QueryHistoryEntry {
  return { ...newHistoryEntry(question, askState({ sql: 'SELECT 1' }), 1000), ...overrides }
}

beforeEach(() => {
  useChatStore.setState({ pinnedEntryIds: new Set() })
})

describe('summarizeArtifacts', () => {
  it('includes only the pinned turns', () => {
    const a = entry('First?')
    const b = entry('Second?')
    const summaries = summarizeArtifacts([a, b], new Set([b.entryId]))
    expect(summaries.map((item) => item.question)).toEqual(['Second?'])
  })

  it('skips a pinned id that is not in the open conversation', () => {
    // Switching conversations must not leave dead rows behind.
    const a = entry('First?')
    expect(summarizeArtifacts([a], new Set(['some-other-entry']))).toEqual([])
  })

  it('prefers the confirmed row count over the agent-reported one', () => {
    const a = entry('First?', { confirmedColumns: ['c'], confirmedRows: [[1], [2]] })
    expect(summarizeArtifacts([a], new Set([a.entryId]))[0].rowCount).toBe(2)
  })

  it('marks a turn that has a chart', () => {
    const a = entry('First?', { chartOptions: { chartType: 'bar' } as never })
    expect(summarizeArtifacts([a], new Set([a.entryId]))[0].hasChart).toBe(true)
  })

  it('caps a long question label', () => {
    const long = `Why did ${'revenue '.repeat(30)}drop?`
    const a = entry(long)
    expect(summarizeArtifacts([a], new Set([a.entryId]))[0].question.length).toBeLessThanOrEqual(70)
  })

  it('references turns by id rather than copying their results', () => {
    // An artifact is a reference, so the summary carries no rows -- it
    // cannot go stale or disagree with the turn it points at.
    const a = entry('First?', { confirmedColumns: ['c'], confirmedRows: [[1]] })
    const summary = summarizeArtifacts([a], new Set([a.entryId]))[0]
    expect(Object.keys(summary).sort()).toEqual(
      ['entryId', 'hasChart', 'hasResult', 'question', 'rowCount'].sort(),
    )
  })
})

describe('ArtifactWorkspace', () => {
  it('renders nothing when nothing is pinned', () => {
    const { container } = render(<ArtifactWorkspace entries={[entry('First?')]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('lists the pinned analyses with a count', () => {
    const a = entry('Revenue by region?')
    useChatStore.setState({ pinnedEntryIds: new Set([a.entryId]) })
    render(<ArtifactWorkspace entries={[a]} />)
    const section = screen.getByRole('region', { name: 'Pinned analyses' })
    expect(within(section).getByText(/Pinned analyses \(1\)/)).toBeInTheDocument()
    expect(within(section).getByText('Revenue by region?')).toBeInTheDocument()
  })

  it('scrolls to the turn it references, reusing the chat anchor', () => {
    const a = entry('Revenue by region?')
    useChatStore.setState({ pinnedEntryIds: new Set([a.entryId]) })
    const anchor = document.createElement('div')
    anchor.id = `turn-${a.entryId}`
    const scrollIntoView = vi.fn()
    anchor.scrollIntoView = scrollIntoView
    document.body.append(anchor)

    render(<ArtifactWorkspace entries={[a]} />)
    fireEvent.click(screen.getByRole('button', { name: /^Revenue by region/ }))
    expect(scrollIntoView).toHaveBeenCalled()
    anchor.remove()
  })

  it('unpins from the workspace', () => {
    const a = entry('Revenue by region?')
    useChatStore.setState({ pinnedEntryIds: new Set([a.entryId]) })
    render(<ArtifactWorkspace entries={[a]} />)
    fireEvent.click(screen.getByRole('button', { name: /^Unpin/ }))
    expect(useChatStore.getState().pinnedEntryIds.has(a.entryId)).toBe(false)
  })
})

describe('PinTurnButton', () => {
  it('pins and unpins the same turn', () => {
    const a = entry('Revenue by region?')
    render(<PinTurnButton entryId={a.entryId} question={a.question} />)

    fireEvent.click(screen.getByRole('button', { name: /^Pin/ }))
    expect(useChatStore.getState().pinnedEntryIds.has(a.entryId)).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: /^Unpin/ }))
    expect(useChatStore.getState().pinnedEntryIds.has(a.entryId)).toBe(false)
  })

  it('exposes its pressed state to assistive technology', () => {
    const a = entry('Revenue by region?')
    useChatStore.setState({ pinnedEntryIds: new Set([a.entryId]) })
    render(<PinTurnButton entryId={a.entryId} question={a.question} />)
    expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'true')
  })
})
