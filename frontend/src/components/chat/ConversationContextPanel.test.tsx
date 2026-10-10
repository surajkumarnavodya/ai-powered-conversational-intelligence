import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import {
  buildConversationHistory,
  MAX_FOLLOWUP_EXCHANGES,
  newHistoryEntry,
  type QueryHistoryEntry,
} from '@/lib/history'
import { askState } from '@/test/analyticsFixtures'
import { ConversationContextPanel } from './ConversationContextPanel'

function entry(
  question: string,
  status: QueryHistoryEntry['agentStatus'] = 'succeeded',
  tables: string[] = [],
): QueryHistoryEntry {
  const base = newHistoryEntry(question, askState({ status }), 1000)
  return { ...base, agentStatus: status, tables }
}

describe('ConversationContextPanel', () => {
  it('renders nothing until there is a prior turn to carry', () => {
    const { container } = render(<ConversationContextPanel entries={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when no turn succeeded', () => {
    // A conversation of failures carries no context, so there is nothing
    // to explain.
    const { container } = render(
      <ConversationContextPanel entries={[entry('bad one', 'failed')]} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('states how many turns are carried against the cap', () => {
    render(<ConversationContextPanel entries={[entry('Revenue by region?')]} />)
    expect(
      screen.getByText(`Context carried: 1 of ${MAX_FOLLOWUP_EXCHANGES} turns`),
    ).toBeInTheDocument()
  })

  it('lists exactly the turns the server will receive', () => {
    // The panel calls `buildConversationHistory`, the same function that
    // builds the request payload -- so the list cannot drift from what is
    // actually sent.
    const entries = [
      entry('First?'),
      entry('Second?'),
      entry('Third?'),
      entry('Fourth?'),
    ]
    render(<ConversationContextPanel entries={entries} />)
    const list = screen.getByRole('list', { name: 'Turns carried as context' })
    const shown = within(list)
      .getAllByRole('listitem')
      .map((item) => item.textContent)
    const sent = buildConversationHistory(entries).map((exchange) => exchange.question)
    expect(shown).toEqual(sent)
  })

  it('carries only successful turns', () => {
    const entries = [entry('Worked?'), entry('Broke?', 'failed')]
    render(<ConversationContextPanel entries={entries} />)
    const list = screen.getByRole('list', { name: 'Turns carried as context' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(1)
    expect(within(list).getByText('Worked?')).toBeInTheDocument()
  })

  it('discloses how many earlier turns are not carried', () => {
    const entries = [
      entry('One?'),
      entry('Two?'),
      entry('Three?'),
      entry('Four?'),
      entry('Five?'),
    ]
    render(<ConversationContextPanel entries={entries} />)
    expect(screen.getByText(/earlier turn\(s\) are not carried/)).toBeInTheDocument()
  })

  it('names the tables in context', () => {
    render(
      <ConversationContextPanel
        entries={[entry('Revenue?', 'succeeded', ['sales', 'product'])]}
      />,
    )
    expect(screen.getByText(/product, sales/)).toBeInTheDocument()
  })

  it('does not show SQL -- each turn already shows its own', () => {
    const base = entry('Revenue?', 'succeeded', ['sales'])
    const { container } = render(
      <ConversationContextPanel entries={[{ ...base, sql: 'SELECT SUM(x) FROM sales' }]} />,
    )
    expect(container.textContent ?? '').not.toContain('SELECT')
  })

  it('collapses and caps a long question rather than wrapping the panel', () => {
    const long = `Why did ${'revenue '.repeat(30)}drop?`
    render(<ConversationContextPanel entries={[entry(long)]} />)
    const list = screen.getByRole('list', { name: 'Turns carried as context' })
    const text = within(list).getAllByRole('listitem')[0].textContent ?? ''
    expect(text.length).toBeLessThanOrEqual(92)
    expect(text).toContain('…')
  })
})
