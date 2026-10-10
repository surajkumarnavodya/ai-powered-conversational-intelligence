import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { askState } from '@/test/analyticsFixtures'
import type { AskResponse, Citation, SourceAnswer } from '@/lib/types'
import { SourceLineagePanel } from './SourceLineagePanel'

function citation(overrides: Partial<Citation> = {}): Citation {
  return {
    filename: 'handbook.pdf',
    chunk_index: 0,
    page_number: 4,
    document_id: 'doc-internal-7f3a9c',
    has_pdf_bytes: true,
    ...overrides,
  }
}

function sourceAnswer(overrides: Partial<SourceAnswer> = {}): SourceAnswer {
  return { answer: 'Because the policy says so.', citations: [citation()], status: 'ok', ...overrides }
}

function renderPanel(overrides: Partial<AskResponse> = {}) {
  return render(<SourceLineagePanel state={askState(overrides)} />)
}

/** Opens the outer collapsed panel, exactly as a click would. */
function expand() {
  const summary = screen.getByText(/^Sources \(/)
  fireEvent.click(summary)
  return summary
}

const sqlState: Partial<AskResponse> = {
  database: 'CompanyMaster',
  sql: 'SELECT region, revenue FROM dbo.Orders',
  sql_summary: { statement_type: 'SELECT', referenced_tables: ['dbo.Orders'] },
  result_columns: ['region', 'revenue'],
  row_count: 12,
  insight: 'Revenue rose in the North.',
}

describe('SourceLineagePanel -- collapsed state', () => {
  it('is one line showing the source count, per the requested UX', () => {
    renderPanel(sqlState)
    expect(screen.getByText('Sources (1)')).toBeInTheDocument()
  })

  it('counts every contributing source', () => {
    renderPanel({
      ...sqlState,
      sources_used: ['sql', 'documents', 'web'],
      document_result: sourceAnswer(),
      web_result: sourceAnswer({ citations: [citation({ filename: 'https://example.com', page_number: null })] }),
    })
    expect(screen.getByText('Sources (3)')).toBeInTheDocument()
  })

  it('renders nothing at all when no source can honestly be attributed', () => {
    const { container } = renderPanel({
      status: 'rejected',
      database: null,
      sql: null,
      row_count: null,
      rejection_reason: 'off_topic',
    })
    expect(container).toBeEmptyDOMElement()
  })

  it('uses a native details/summary, so it is keyboard-operable with no JS state', () => {
    const { container } = renderPanel(sqlState)
    const details = container.querySelector('details')
    expect(details).not.toHaveAttribute('open')
    expect(container.querySelector('details > summary')).not.toBeNull()
  })
})

describe('SourceLineagePanel -- expanded state', () => {
  it('shows the sources as one bullet-separated line', () => {
    renderPanel({
      ...sqlState,
      governing_metrics: [
        { business_name: 'Net Revenue', approved_expression: null, aggregation: null, text: 'x' },
      ],
    })
    expand()
    // "Database • CompanyMaster • Business glossary" -- the real engine name
    // is not available from any API schema, so the source kind is used.
    expect(screen.getByText('Database • CompanyMaster • Business glossary')).toBeInTheDocument()
  })

  it('shows the Question to Insight chain as an ordered list', () => {
    renderPanel(sqlState)
    expand()
    const chain = screen.getByRole('list', { name: /how this answer was produced/i })
    expect(within(chain).getAllByRole('listitem')).toHaveLength(6)
  })

  it('marks every chain step reached for a complete SQL answer', () => {
    const { container } = renderPanel(sqlState)
    expand()
    for (const key of ['question', 'source', 'tables', 'query', 'result', 'insight']) {
      expect(container.querySelector(`[data-lineage-step="${key}"]`)).toHaveAttribute(
        'data-lineage-state',
        'present',
      )
    }
  })

  it('does not convey a skipped step by styling alone', () => {
    const { container } = renderPanel({
      sources_used: ['web'],
      database: null,
      sql: null,
      row_count: null,
      web_result: sourceAnswer(),
    })
    expand()
    const query = container.querySelector('[data-lineage-step="query"]')
    expect(query).toHaveAttribute('data-lineage-state', 'not_applicable')
    // A screen reader is told why, not left with a line-through it cannot see.
    expect(screen.getAllByText(/not applicable to this answer/i).length).toBeGreaterThan(0)
  })
})

describe('SourceLineagePanel -- per-source inspection', () => {
  it('makes each source individually inspectable', () => {
    renderPanel(sqlState)
    expand()

    const database = screen.getByRole('button', { name: /Database/ })
    expect(database).toHaveAttribute('aria-expanded', 'false')

    fireEvent.click(database)

    expect(screen.getByRole('button', { name: /Database/ })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Tables used')).toBeInTheDocument()
    expect(screen.getByText('dbo.Orders')).toBeInTheDocument()
    expect(screen.getByText('region, revenue')).toBeInTheDocument()
  })

  it('points aria-controls at the panel it actually reveals', () => {
    renderPanel(sqlState)
    expand()
    const database = screen.getByRole('button', { name: /Database/ })
    const controlled = database.getAttribute('aria-controls')
    fireEvent.click(database)
    expect(document.getElementById(controlled ?? '')).toBeInTheDocument()
  })

  it('collapses an open source when activated again', () => {
    renderPanel(sqlState)
    expand()
    const database = screen.getByRole('button', { name: /Database/ })
    fireEvent.click(database)
    fireEvent.click(screen.getByRole('button', { name: /Database/ }))
    expect(screen.queryByText('Tables used')).not.toBeInTheDocument()
  })

  it('is not an inert control for a source with nothing further to show', () => {
    // A policy answer whose matches were all restricted has no citations,
    // so there is genuinely nothing to expand -- the row must not pretend
    // otherwise.
    renderPanel({
      sources_used: ['policy'],
      database: null,
      sql: null,
      row_count: null,
      policy_result: sourceAnswer({ answer: 'That is restricted.', citations: [] }),
    })
    expand()
    const policy = screen.getByRole('button', { name: /Policy/ })
    expect(policy).toBeDisabled()
    expect(policy).not.toHaveAttribute('aria-expanded')
  })

  it('labels each source with how its content was retrieved', () => {
    renderPanel({
      ...sqlState,
      sources_used: ['sql', 'documents'],
      document_result: sourceAnswer(),
    })
    expand()
    expect(screen.getByText('read-only SQL query')).toBeInTheDocument()
    expect(screen.getByText('retrieved and graded')).toBeInTheDocument()
  })
})

describe('SourceLineagePanel -- result and insight provenance', () => {
  it('badges a query result as computed from data', () => {
    renderPanel(sqlState)
    expand()
    const database = screen.getByRole('button', { name: /Database/ })
    expect(within(database).getByText('Computed from data')).toBeInTheDocument()
  })

  it('badges an approved metric definition distinctly from an inference', () => {
    renderPanel({
      ...sqlState,
      governing_metrics: [
        { business_name: 'Net Revenue', approved_expression: 'SUM(x)', aggregation: 'sum', text: 'x' },
      ],
    })
    expand()
    const glossary = screen.getByRole('button', { name: /Business glossary/ })
    expect(within(glossary).getByText('Approved definition')).toBeInTheDocument()
  })

  it('badges a web answer as an AI estimate', () => {
    renderPanel({
      sources_used: ['web'],
      database: null,
      sql: null,
      row_count: null,
      web_result: sourceAnswer({ citations: [citation({ filename: 'https://example.com', page_number: null })] }),
    })
    expand()
    const web = screen.getByRole('button', { name: /Web/ })
    expect(within(web).getByText('AI estimate')).toBeInTheDocument()
  })

  it('reports the real version and timing metadata it was given', () => {
    renderPanel({ ...sqlState, model: 'llama3.1:8b', answer_duration_ms: 1234.7 })
    expand()
    expect(screen.getByText('llama3.1:8b')).toBeInTheDocument()
    expect(screen.getByText('1235 ms')).toBeInTheDocument()
  })
})

describe('SourceLineagePanel -- restricted sources and tenant isolation', () => {
  it('discloses that values were masked for this caller', () => {
    renderPanel({ ...sqlState, restricted_field_notice: 'Some values were masked for your role.' })
    expand()
    expect(screen.getByText(/withheld for your role/i)).toBeInTheDocument()
  })

  it('discloses a source dropped because the caller lacks permission', () => {
    renderPanel({
      ...sqlState,
      permission_denied_notice: 'Media generation is not available for your role.',
    })
    expand()
    expect(screen.getByText(/withheld for your role/i)).toBeInTheDocument()
  })

  it('does not repeat the server\'s own restriction sentence, which ExecutionStatusBar owns', () => {
    // Showing the same sentence twice in one turn is what this avoids --
    // the marker says a restriction applies, the owner says what it was.
    const notice = 'Some values were masked for your role.'
    const { container } = renderPanel({ ...sqlState, restricted_field_notice: notice })
    expand()
    expect(container.textContent).not.toContain(notice)
  })

  it('shows one marker even when both restriction notices are present', () => {
    renderPanel({
      ...sqlState,
      restricted_field_notice: 'Values masked.',
      permission_denied_notice: 'Source dropped.',
    })
    expand()
    expect(screen.getAllByText(/withheld for your role/i)).toHaveLength(1)
  })

  it('never shows a source the server did not report for this caller', () => {
    renderPanel({ ...sqlState, sources_used: ['sql'] })
    expand()
    expect(screen.queryByRole('button', { name: /Documents/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Policy/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Web/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Media library/ })).not.toBeInTheDocument()
  })

  it('names only the database this caller\'s answer was routed to', () => {
    const { container } = renderPanel({ ...sqlState, database: 'TenantAMaster' })
    expand()
    // Named twice by design -- once in the bullet summary line, once on its
    // own inspectable row.
    expect(screen.getAllByText(/TenantAMaster/).length).toBeGreaterThan(0)
    expect(container.textContent).not.toContain('TenantB')
  })
})

describe('SourceLineagePanel -- no leakage into the DOM', () => {
  const FORBIDDEN = [
    /password\s*=/i,
    /\buid\s*=/i,
    /driver=\{/i,
    /mssql\+pyodbc/i,
    /[A-Za-z]:\\Users\\/i,
    /\/home\/[a-z]/i,
  ]

  it('renders no credential- or path-shaped text, even when planted in the response', () => {
    const { container } = renderPanel({
      ...sqlState,
      sql: "SELECT * FROM t WHERE c = 'Server=10.0.0.1;Uid=sa;Password=hunter2'",
      schema_tables: [
        {
          table_name: 'Orders',
          ddl: "CREATE TABLE Orders (p VARCHAR DEFAULT 'C:\\Users\\Suraj\\secret.txt')",
          similarity_score: 0.9,
        },
      ],
      error_history: ["pyodbc.ProgrammingError: login failed for user 'sa'"],
      query_plan: ['Step 1: reason about the join'],
    })
    expand()
    // Expand every inspectable source, so nothing is hidden behind a click.
    for (const button of screen.getAllByRole('button')) {
      if (!(button as HTMLButtonElement).disabled) fireEvent.click(button)
    }

    const text = container.textContent ?? ''
    for (const pattern of FORBIDDEN) {
      expect(text, String(pattern)).not.toMatch(pattern)
    }
    expect(text).not.toContain('login failed')
    expect(text).not.toContain('Step 1')
    expect(text).not.toContain('CREATE TABLE')
  })

  it('identifies a citation by filename and page, never by its internal document id', () => {
    const { container } = renderPanel({
      sources_used: ['documents'],
      database: null,
      sql: null,
      row_count: null,
      document_result: sourceAnswer(),
    })
    expand()
    fireEvent.click(screen.getByRole('button', { name: /Documents/ }))

    expect(screen.getByText('handbook.pdf (p. 4)')).toBeInTheDocument()
    expect(container.textContent).not.toContain('doc-internal-7f3a9c')
  })

  it('never renders the SQL body, which has its own re-validating editor', () => {
    const { container } = renderPanel({
      ...sqlState,
      sql: 'SELECT secret FROM vault',
      sql_summary: { statement_type: 'SELECT', referenced_tables: ['vault'] },
    })
    expand()
    fireEvent.click(screen.getByRole('button', { name: /Database/ }))
    expect(container.textContent).not.toContain('SELECT secret FROM vault')
    // The statement *shape* is reported, which is not the statement.
    expect(screen.getAllByText('SELECT').length).toBeGreaterThan(0)
  })
})
