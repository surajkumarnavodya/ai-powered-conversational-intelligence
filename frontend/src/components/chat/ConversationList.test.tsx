import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { newHistoryEntry, type ConversationSummary } from '@/lib/history'
import type { AskResponse } from '@/lib/types'
import { ConversationList } from './ConversationList'

function makeFinalState(overrides: Partial<AskResponse> = {}): AskResponse {
  return {
    session_id: 's1',
    conversation_id: 'c1',
    message_id: null,
    status: 'succeeded',
    database: 'default',
    model: 'llama3.1:8b',
    sql: 'SELECT 1',
    result_columns: null,
    result_rows: null,
    row_count: 1,
    retry_count: 0,
    attempt_history: [],
    insight: null,
    cost_notice: null,
    low_confidence_notice: null,
    rejection_reason: null,
    rejection_message: null,
    rate_limit_message: null,
    clarification_message: null,
    failure_explanation: null,
    error_history: [],
    sources_used: ['sql'],
    synthesized_answer: null,
    document_result: null,
    policy_result: null,
    web_result: null,
    generation_result: null,
    media_search_result: null,
    attachment_result: null,
    query_plan: null,
    schema_tables: [],
    followup_classification: null,
    followup_resolved_against: null,
    permission_denied_notice: null,
    source_selection_notice: null,
    analytical_result: null,
    forecast_result: null,
    recommendations: [],
    analytical_intent: null,
    analytical_plan: null,
    governing_metrics: [],
    restricted_field_notice: null,
    ...overrides,
  }
}

function makeConversation(id: string, title: string): ConversationSummary {
  return {
    id,
    title,
    updatedAt: new Date().toISOString(),
    entries: [newHistoryEntry(title, makeFinalState(), 100)],
    messagesLoaded: true,
  }
}

function noop() {}

const baseProps = {
  activeConversationId: null,
  isLoadingConversation: false,
  isLoading: false,
  renamingId: null,
  renameValue: '',
  onRenameValueChange: noop,
  onSelect: noop,
  onStartRename: noop,
  onCommitRename: noop,
  onCancelRename: noop,
  onDelete: noop,
  onShare: noop,
}

describe('ConversationList', () => {
  it('shows a loading spinner when isLoading is true', () => {
    render(<ConversationList {...baseProps} conversations={[]} isLoading={true} />)
    expect(document.querySelector('.animate-spin')).toBeInTheDocument()
  })

  it('shows the empty state when there are no conversations and not loading', () => {
    render(<ConversationList {...baseProps} conversations={[]} />)
    expect(screen.getByText(/no conversations yet/i)).toBeInTheDocument()
  })

  it('renders conversations grouped by recency', () => {
    render(
      <ConversationList
        {...baseProps}
        conversations={[makeConversation('c1', 'Revenue last quarter'), makeConversation('c2', 'Top products')]}
      />,
    )
    expect(screen.getByText('Today')).toBeInTheDocument()
    expect(screen.getByText('Revenue last quarter')).toBeInTheDocument()
    expect(screen.getByText('Top products')).toBeInTheDocument()
  })

  describe('collapsible recency groups', () => {
    function renderWithTodayGroup() {
      return render(
        <ConversationList
          {...baseProps}
          conversations={[
            makeConversation('c1', 'Revenue last quarter'),
            makeConversation('c2', 'Top products'),
          ]}
        />,
      )
    }

    it('renders each category heading as an expanded toggle by default', () => {
      renderWithTodayGroup()
      const heading = screen.getByRole('button', { name: /collapse today/i })
      expect(heading).toHaveAttribute('aria-expanded', 'true')
      // Names the region it controls, and that region really exists.
      const controlled = document.getElementById(heading.getAttribute('aria-controls') as string)
      expect(controlled).not.toBeNull()
      expect(controlled).not.toHaveAttribute('hidden')
    })

    it('collapses the group and hides its conversations when clicked', async () => {
      renderWithTodayGroup()
      await userEvent.click(screen.getByRole('button', { name: /collapse today/i }))

      const heading = screen.getByRole('button', { name: /expand today/i })
      expect(heading).toHaveAttribute('aria-expanded', 'false')
      const controlled = document.getElementById(heading.getAttribute('aria-controls') as string)
      // `hidden` is `display:none`, so the rows leave the a11y tree and
      // the tab order rather than merely looking gone.
      expect(controlled).toHaveAttribute('hidden')
    })

    it('expands again on a second click', async () => {
      renderWithTodayGroup()
      await userEvent.click(screen.getByRole('button', { name: /collapse today/i }))
      await userEvent.click(screen.getByRole('button', { name: /expand today/i }))

      const heading = screen.getByRole('button', { name: /collapse today/i })
      expect(heading).toHaveAttribute('aria-expanded', 'true')
      const controlled = document.getElementById(heading.getAttribute('aria-controls') as string)
      expect(controlled).not.toHaveAttribute('hidden')
    })

    it('swaps the plus/minus affordance with the state', async () => {
      const { container } = renderWithTodayGroup()
      const iconClass = () =>
        (screen.getByRole('button', { name: /today/i }).querySelector('svg') as SVGElement).getAttribute(
          'class',
        ) ?? ''

      expect(iconClass()).toMatch(/minus/)
      await userEvent.click(screen.getByRole('button', { name: /collapse today/i }))
      expect(iconClass()).toMatch(/plus/)
      expect(container).toBeTruthy()
    })

    it('styles the category heading as bold primary text, not muted', () => {
      // The requested black/bold treatment. Asserted as the theme token
      // rather than a literal colour: `--chat-text-primary` is near-black
      // in the light theme and near-white in the dark one, so a literal
      // `#000` here would mean an unreadable heading on dark.
      renderWithTodayGroup()
      const heading = screen.getByRole('button', { name: /collapse today/i })
      expect(heading.className).toMatch(/font-bold/)
      expect(heading.className).toMatch(/text-\[var\(--chat-text-primary\)\]/)
      expect(heading.className).not.toMatch(/muted-foreground/)
    })
  })
})
