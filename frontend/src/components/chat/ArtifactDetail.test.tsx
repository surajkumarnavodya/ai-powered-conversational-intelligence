import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ArtifactDetailOut, ArtifactResultOut } from '@/lib/types'
import { ArtifactDetail } from './ArtifactDetail'
import { ArtifactFreshnessBadge, ArtifactFreshnessReasons } from './ArtifactFreshnessBadge'

function result(overrides: Partial<ArtifactResultOut> = {}): ArtifactResultOut {
  return {
    columns: ['region', 'revenue'],
    rows: [['Australia', 2300000]],
    row_count: 3,
    returned_rows: 1,
    truncated: false,
    column_types: null,
    chart_recommendation: null,
    captured_at: '2026-10-10T09:00:00Z',
    ...overrides,
  }
}

function detail(overrides: Partial<ArtifactDetailOut> = {}): ArtifactDetailOut {
  return {
    id: 'art-1',
    artifact_type: 'analysis',
    title: 'Revenue by region?',
    created_at: '2026-10-10T09:00:00Z',
    updated_at: '2026-10-10T09:00:00Z',
    conversation_id: 'conv-1',
    prompt_id: 'prompt-1',
    message_id: 'msg-1',
    database: 'default',
    model: 'llama3.1:8b',
    sources_used: ['sql'],
    row_count: 3,
    freshness: {
      state: 'current',
      reasons: [],
      result_age_seconds: 60,
      result_as_of: '2026-10-10T09:00:00Z',
    },
    is_refreshable: true,
    refresh_blocked_reason: null,
    last_refresh_at: null,
    last_refresh_outcome: 'never_refreshed',
    last_refresh_error: null,
    question: 'Revenue by region?',
    insight: 'Australia leads at 2300000.',
    insight_numbers_checked: 1,
    tables_referenced: ['sales'],
    governed_metrics: [],
    truth_level: 'ai_inference',
    sql: 'SELECT region, revenue FROM sales',
    sql_withheld_reason: null,
    origin_result: result(),
    refreshed_result: null,
    result_changed_since_creation: false,
    ...overrides,
  }
}

function renderDetail(artifact: ArtifactDetailOut) {
  // ResultsTable (rendered only for a refreshed result) reads schema
  // metadata through react-query -- the same convention TurnCard.test.tsx
  // already follows for any path that can reach it.
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <ArtifactDetail artifact={artifact} />
    </QueryClientProvider>,
  )
}

describe('ArtifactDetail -- what an artifact retains', () => {
  it('keeps the source question beside the work product', () => {
    // The whole point: an answer becomes reusable without losing the
    // question that produced it.
    renderDetail(detail())
    expect(screen.getByText('Source question')).toBeInTheDocument()
    expect(screen.getByText('Revenue by region?')).toBeInTheDocument()
  })

  it('shows the lineage the turn recorded', () => {
    renderDetail(detail({ governed_metrics: ['revenue'] }))
    expect(screen.getByText('default')).toBeInTheDocument()
    expect(screen.getByText('llama3.1:8b')).toBeInTheDocument()
    expect(screen.getByText('sales')).toBeInTheDocument()
    expect(screen.getByText('revenue')).toBeInTheDocument()
  })

  it('labels the narrative with its truth level, like every other surface', () => {
    const { container } = renderDetail(detail())
    expect(container.querySelector('[data-truth-level="ai_inference"]')).not.toBeNull()
  })

  it('shows the SQL when the server returned it', () => {
    renderDetail(detail())
    expect(screen.getByText('SELECT region, revenue FROM sales')).toBeInTheDocument()
  })

  it('shows the server reason, not the query, when the SQL was withheld', () => {
    renderDetail(
      detail({
        sql: null,
        sql_withheld_reason:
          'The query is not shown because it references a field your role may not view.',
      }),
    )
    expect(screen.getByText(/not shown because it references a field/)).toBeInTheDocument()
    expect(screen.queryByText(/SELECT/)).not.toBeInTheDocument()
  })

  it('never reconstructs a withheld query from anything else it was given', () => {
    // There is no client-side decision about who may read a query, and no
    // other field carries it.
    const { container } = renderDetail(
      detail({ sql: null, sql_withheld_reason: 'Withheld.', tables_referenced: ['employees'] }),
    )
    expect(container.textContent).not.toMatch(/SELECT|FROM employees/)
  })

  it('does not re-render the original result as a table', () => {
    // Those rows already render in the turn this artifact points at, one
    // click away -- a second renderer would be a second thing to keep in
    // agreement with the first.
    renderDetail(detail())
    expect(screen.queryByText('Australia')).not.toBeInTheDocument()
    expect(screen.getByText(/As saved:/)).toBeInTheDocument()
  })

  it('does render a refreshed result, because it exists nowhere else', () => {
    renderDetail(
      detail({
        last_refresh_outcome: 'success',
        refreshed_result: result({ rows: [['Australia', 2400000]] }),
      }),
    )
    expect(screen.getAllByText(/Latest run:/).length).toBeGreaterThan(0)
    expect(screen.getByText('Australia')).toBeInTheDocument()
  })

  it('reports the full row count, not the stored subset, and says when they differ', () => {
    renderDetail(detail({ origin_result: result({ row_count: 500, returned_rows: 50, truncated: true }) }))
    expect(screen.getByText(/500 rows/)).toBeInTheDocument()
    expect(screen.getByText(/first 50 rows stored/)).toBeInTheDocument()
  })

  it('states plainly when re-running returns different numbers', () => {
    // Requirement 7, as one sentence a reader cannot miss.
    const { container } = renderDetail(detail({ result_changed_since_creation: true }))
    expect(container.querySelector('[data-artifact-result-changed="true"]')).not.toBeNull()
    expect(screen.getByRole('status')).toHaveTextContent(/different numbers/)
  })

  it('says nothing about changed numbers when nothing changed', () => {
    const { container } = renderDetail(detail({ result_changed_since_creation: false }))
    expect(container.querySelector('[data-artifact-result-changed]')).toBeNull()
  })

  it('reports a failed re-run without hiding the artifact', () => {
    renderDetail(
      detail({
        last_refresh_outcome: 'failed',
        last_refresh_error: 'Execution failed: connection reset',
      }),
    )
    expect(screen.getByText(/did not complete/)).toBeInTheDocument()
    expect(screen.getByText('Source question')).toBeInTheDocument()
  })

  it('sanitizes database-derived text', () => {
    const noisy = `sales${String.fromCharCode(10)}orders`
    renderDetail(detail({ tables_referenced: [noisy] }))
    expect(screen.getByText('sales orders')).toBeInTheDocument()
  })

  it('does not truncate the source question to a short label', () => {
    // `sanitizeLabel`'s short default would lose exactly what the artifact
    // exists to keep.
    const question = `Why did ${'revenue '.repeat(12)}fall last quarter?`
    renderDetail(detail({ question }))
    expect(screen.getByText(/fall last quarter\?/)).toBeInTheDocument()
  })
})

describe('ArtifactFreshnessBadge', () => {
  function freshness(state: string, reasons: string[] = []) {
    return {
      state: state as never,
      reasons,
      result_age_seconds: 1,
      result_as_of: '2026-10-10T09:00:00Z',
    }
  }

  it('renders every state as words with an icon, never colour alone', () => {
    for (const state of ['current', 'aging', 'stale', 'source_missing', 'no_result_captured']) {
      const { container, unmount } = render(
        <ArtifactFreshnessBadge freshness={freshness(state)} />,
      )
      expect(container.textContent?.trim().length).toBeGreaterThan(0)
      expect(container.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
      expect(container.querySelector(`[data-artifact-freshness="${state}"]`)).not.toBeNull()
      unmount()
    }
  })

  it('gives each state a distinct label', () => {
    const labels = new Set<string>()
    for (const state of ['current', 'aging', 'stale', 'source_missing', 'no_result_captured']) {
      const { container, unmount } = render(
        <ArtifactFreshnessBadge freshness={freshness(state)} />,
      )
      labels.add(container.textContent ?? '')
      unmount()
    }
    expect(labels.size).toBe(5)
  })

  it('lists every reason the server reported, because they accumulate', () => {
    render(
      <ArtifactFreshnessReasons
        freshness={freshness('stale', ['schema_changed', 'last_refresh_failed'])}
      />,
    )
    expect(screen.getByText(/database structure has changed/)).toBeInTheDocument()
    expect(screen.getByText(/last attempt to re-run this did not complete/)).toBeInTheDocument()
  })

  it('skips a reason code it has no sentence for rather than showing the raw token', () => {
    render(<ArtifactFreshnessReasons freshness={freshness('stale', ['some_future_reason'])} />)
    expect(screen.queryByText(/some_future_reason/)).not.toBeInTheDocument()
  })

  it('renders nothing when there is nothing to explain', () => {
    const { container } = render(<ArtifactFreshnessReasons freshness={freshness('current')} />)
    expect(container).toBeEmptyDOMElement()
  })
})
