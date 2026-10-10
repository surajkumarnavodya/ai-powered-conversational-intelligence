import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import {
  buildConversationHistory,
  MAX_FOLLOWUP_EXCHANGES,
  newHistoryEntry,
  type QueryHistoryEntry,
} from '@/lib/history'
import type { AskResponse } from '@/lib/types'
import { useChatStore } from '@/store/chatStore'
import { askState } from '@/test/analyticsFixtures'
import { ConversationContextPanel } from './ConversationContextPanel'

function entry(
  question: string,
  overrides: Partial<QueryHistoryEntry> = {},
  state: Partial<AskResponse> = {},
): QueryHistoryEntry {
  const base = newHistoryEntry(question, askState({ status: 'succeeded', ...state }), 1000)
  return { ...base, agentStatus: 'succeeded', ...overrides }
}

function expand() {
  // Native <details> -- clicking the summary is the real interaction.
  const summary = document.querySelector('summary')
  if (summary) summary.click()
}

function renderPanel(entries: QueryHistoryEntry[]) {
  const result = render(<ConversationContextPanel entries={entries} />)
  expand()
  return result
}

/** Rows the panel currently presents as context, by kind. */
function rowsOfKind(kind: string): HTMLElement[] {
  return Array.from(document.querySelectorAll(`[data-context-kind="${kind}"]`)) as HTMLElement[]
}

describe('ConversationContextPanel', () => {
  beforeEach(() => {
    useChatStore.setState({
      queryHistory: [],
      excludedContextEntryIds: new Set(),
      conversations: {},
      nlQuestionCache: new Map(),
    })
  })

  it('renders nothing until there is something to say', () => {
    const { container } = render(<ConversationContextPanel entries={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('still explains itself when every turn failed', () => {
    // There is no context, but "nothing is carried, because nothing
    // succeeded" is a real answer -- an absent panel reads as hidden.
    renderPanel([entry('Broke?', { agentStatus: 'failed' })])
    expect(screen.getByText(/did not succeed/)).toBeInTheDocument()
  })

  it('collapses to an active-item count', () => {
    renderPanel([entry('Revenue by company?', { tables: ['sales'] })])
    // Question + database + tables.
    expect(screen.getByText(/Context • 3 active items/)).toBeInTheDocument()
  })

  it('groups items under the three provenance classes', () => {
    renderPanel([
      entry(
        'Revenue FY2026?',
        { tables: ['CompanyMaster', 'Sales'] },
        {
          database: 'SalesDB',
          analytical_plan: {
            metrics: [],
            dimensions: [],
            filters: [{ table: 'Sales', column: 'fiscal_year', operator: '=', value: 'FY2026' }],
            time_range: null,
            grain: null,
            comparison: null,
            ranking: null,
            sort: [],
            limit: null,
            required_operations: [],
            truth_level: 'ai_inference',
          },
        },
      ),
    ])
    expect(screen.getByRole('heading', { name: 'From you' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Confirmed' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Inferred' })).toBeInTheDocument()
  })

  it('shows the database, tables, previous question and filter the UX calls for', () => {
    renderPanel([
      entry('Revenue by company?', { tables: ['CompanyMaster', 'Sales'] }, { database: 'SalesDB' }),
      entry(
        'And for FY2026?',
        { tables: ['Sales'] },
        {
          database: 'SalesDB',
          followup_classification: 'followup',
          followup_resolved_against: {
            question: 'Revenue by company?',
            sql: 'SELECT 1',
            tables: ['Sales'],
            status: 'succeeded',
          },
          analytical_plan: {
            metrics: [],
            dimensions: [],
            filters: [{ table: 'Sales', column: 'fiscal_year', operator: '=', value: 'FY2026' }],
            time_range: null,
            grain: null,
            comparison: null,
            ranking: null,
            sort: [],
            limit: null,
            required_operations: [],
            truth_level: 'ai_inference',
          },
        },
      ),
    ])
    expect(within(rowsOfKind('database')[0]).getByText('SalesDB')).toBeInTheDocument()
    expect(within(rowsOfKind('tables')[0]).getByText('CompanyMaster, Sales')).toBeInTheDocument()
    expect(
      within(rowsOfKind('referencedAnswer')[0]).getByText('Revenue by company?'),
    ).toBeInTheDocument()
    expect(within(rowsOfKind('filter')[0]).getByText('fiscal_year = FY2026')).toBeInTheDocument()
  })

  it('says in words which item the server actually reads', () => {
    // Not by list position or colour: only the most recent carried turn is
    // functionally read downstream, and that is a real difference in effect.
    renderPanel([entry('Older?'), entry('Newest?')])
    const questions = rowsOfKind('question')
    expect(questions[0].dataset.contextLifetime).toBe('carried')
    expect(within(questions[0]).getByText('sent')).toBeInTheDocument()
    expect(questions[1].dataset.contextLifetime).toBe('influencing')
    expect(within(questions[1]).getByText('sent, and read')).toBeInTheDocument()
  })

  it('labels a re-derived item as re-derived rather than carried', () => {
    renderPanel([entry('Revenue?', {}, { database: 'SalesDB' })])
    const row = rowsOfKind('database')[0]
    expect(row.dataset.contextLifetime).toBe('rederived')
    expect(within(row).getByText('re-derived each question')).toBeInTheDocument()
  })

  it('shows each item its truth level, never implied', () => {
    renderPanel([
      entry(
        'Revenue?',
        { tables: ['sales'] },
        {
          governing_metrics: [
            {
              business_name: 'Net Revenue',
              approved_expression: 'SUM(amount)',
              aggregation: 'sum',
              text: 'Net Revenue',
            },
          ],
        },
      ),
    ])
    expect(within(rowsOfKind('definition')[0]).getByText('Approved definition')).toBeInTheDocument()
    expect(within(rowsOfKind('database')[0]).getByText('Computed from data')).toBeInTheDocument()
  })

  describe('removing one item', () => {
    it('offers a remove control on a carried turn only', () => {
      renderPanel([entry('Revenue?', { tables: ['sales'] }, { database: 'SalesDB' })])
      expect(
        within(rowsOfKind('question')[0]).getByRole('button', { name: /Remove from context/ }),
      ).toBeInTheDocument()
      // Server-derived scope has no remove control -- a button that could
      // not do what it claims.
      for (const kind of ['database', 'tables']) {
        expect(within(rowsOfKind(kind)[0]).queryByRole('button')).toBeNull()
      }
    })

    it('narrows what the next question will carry', async () => {
      const entries = [entry('Keep?'), entry('Drop?')]
      useChatStore.setState({ queryHistory: entries })
      renderPanel(entries)

      const dropRow = rowsOfKind('question')[1]
      await userEvent.click(within(dropRow).getByRole('button', { name: /Remove from context/ }))

      const excluded = useChatStore.getState().excludedContextEntryIds
      expect(excluded.has(entries[1].entryId)).toBe(true)
      expect(
        buildConversationHistory(entries, MAX_FOLLOWUP_EXCHANGES, excluded).map(
          (exchange) => exchange.question,
        ),
      ).toEqual(['Keep?'])
    })

    it('keeps the removed turn listed and restorable', async () => {
      const entries = [entry('Keep?'), entry('Drop?')]
      useChatStore.setState({ queryHistory: entries, excludedContextEntryIds: new Set([entries[1].entryId]) })
      renderPanel(entries)

      expect(screen.getByRole('heading', { name: /Removed by you \(1\)/ })).toBeInTheDocument()
      await userEvent.click(screen.getByRole('button', { name: /Restore to context: Drop\?/ }))
      expect(useChatStore.getState().excludedContextEntryIds.size).toBe(0)
    })

    it('removes only the duplicate that was selected', async () => {
      // Two identical questions are two different turns; text-based
      // removal would take out both.
      const entries = [entry('Revenue?'), entry('Revenue?')]
      useChatStore.setState({ queryHistory: entries })
      renderPanel(entries)

      const rows = rowsOfKind('question')
      expect(rows).toHaveLength(2)
      await userEvent.click(within(rows[0]).getByRole('button', { name: /Remove from context/ }))

      const excluded = useChatStore.getState().excludedContextEntryIds
      expect([...excluded]).toEqual([entries[0].entryId])
      expect(
        buildConversationHistory(entries, MAX_FOLLOWUP_EXCHANGES, excluded),
      ).toHaveLength(1)
    })

    it('does not touch the transcript', async () => {
      const entries = [entry('Keep?'), entry('Drop?')]
      useChatStore.setState({ queryHistory: entries })
      renderPanel(entries)
      await userEvent.click(
        within(rowsOfKind('question')[1]).getByRole('button', { name: /Remove from context/ }),
      )
      expect(useChatStore.getState().queryHistory).toHaveLength(2)
    })
  })

  describe('resetting context', () => {
    it('clears every carried turn without discarding the conversation', async () => {
      const entries = [entry('One?'), entry('Two?')]
      useChatStore.setState({ queryHistory: entries })
      renderPanel(entries)

      await userEvent.click(screen.getByRole('button', { name: /Clear context/ }))

      const state = useChatStore.getState()
      expect(
        buildConversationHistory(entries, MAX_FOLLOWUP_EXCHANGES, state.excludedContextEntryIds),
      ).toEqual([])
      // The transcript survives -- this is the difference from "start fresh".
      expect(state.queryHistory).toHaveLength(2)
    })

    it('drops the question cache, so a cleared ask is not served from a with-context answer', async () => {
      const entries = [entry('One?')]
      useChatStore.setState({
        queryHistory: entries,
        nlQuestionCache: new Map([['k', askState()]]),
      })
      renderPanel(entries)
      await userEvent.click(screen.getByRole('button', { name: /Clear context/ }))
      expect(useChatStore.getState().nlQuestionCache.size).toBe(0)
    })

    it('starting fresh empties the conversation and its removals together', async () => {
      const entries = [entry('One?')]
      useChatStore.setState({
        queryHistory: entries,
        excludedContextEntryIds: new Set([entries[0].entryId]),
      })
      renderPanel(entries)

      const before = useChatStore.getState().activeConversationId
      await userEvent.click(screen.getByRole('button', { name: /Start fresh analysis/ }))

      const state = useChatStore.getState()
      expect(state.queryHistory).toEqual([])
      expect(state.activeConversationId).not.toBe(before)
      // A new conversation must not inherit another's removals -- they
      // would target turns it does not even contain.
      expect(state.excludedContextEntryIds.size).toBe(0)
    })

    it('offers no clear action when nothing is carried', () => {
      renderPanel([entry('Broke?', { agentStatus: 'failed' })])
      expect(screen.getByRole('button', { name: /Clear context/ })).toBeDisabled()
    })

    it('says which action keeps the conversation', () => {
      renderPanel([entry('One?')])
      expect(screen.getByText(/Clearing context keeps this conversation on screen/)).toBeInTheDocument()
    })
  })

  describe('what it never shows', () => {
    it('shows no SQL -- each turn already shows its own', () => {
      const { container } = renderPanel([
        entry('Revenue?', { sql: 'SELECT SUM(x) FROM sales', tables: ['sales'] }),
      ])
      expect(container.textContent ?? '').not.toContain('SELECT')
    })

    it('shows no retrieved DDL', () => {
      const { container } = renderPanel([
        entry(
          'Revenue?',
          { tables: ['sales'] },
          {
            schema_tables: [
              { table_name: 'sales', ddl: 'CREATE TABLE sales (id INT)', similarity_score: 0.9 },
            ],
          },
        ),
      ])
      expect(container.textContent ?? '').not.toContain('CREATE TABLE')
    })

    it('discloses the window limits instead of silently truncating', () => {
      const entries = Array.from({ length: MAX_FOLLOWUP_EXCHANGES + 2 }, (_, index) =>
        entry(`Q${index}?`),
      )
      renderPanel(entries)
      expect(screen.getByText(/outside the window/)).toBeInTheDocument()
    })
  })
})
