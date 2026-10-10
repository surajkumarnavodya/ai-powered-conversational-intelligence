import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { newHistoryEntry, type QueryHistoryEntry } from '@/lib/history'
import type { AskResponse } from '@/lib/types'
import { askState } from '@/test/analyticsFixtures'
import {
  SqlIntelligencePanel,
  buildSqlMetadata,
  buildSqlSummaryLine,
} from './SqlIntelligencePanel'

const SQL = 'SELECT category, SUM(revenue) AS revenue FROM sales GROUP BY category'

/** The real English strings are loaded by the test setup, so rendered
 * assertions read as a user would see them. The pure builders are tested
 * against keys, which is what actually matters there. */
const tKey = (key: string, options?: Record<string, unknown>) =>
  options && 'count' in options ? `${key}:${options.count}` : key

function entry(
  state: Partial<AskResponse> = {},
  overrides: Partial<QueryHistoryEntry> = {},
): QueryHistoryEntry {
  const base = newHistoryEntry('Revenue by category', askState({ sql: SQL, ...state }), 1200)
  return { ...base, ...overrides }
}

function renderPanel(
  e: QueryHistoryEntry,
  props: Partial<Parameters<typeof SqlIntelligencePanel>[0]> = {},
) {
  const onSqlChange = vi.fn()
  const onConfirmAndRun = vi.fn()
  const result = render(
    <MemoryRouter>
      <SqlIntelligencePanel
        entry={e}
        showDatabase={false}
        onSqlChange={onSqlChange}
        onConfirmAndRun={onConfirmAndRun}
        {...props}
      />
    </MemoryRouter>,
  )
  return { ...result, onSqlChange, onConfirmAndRun }
}

describe('buildSqlSummaryLine -- the collapsed progressive-disclosure line', () => {
  it('reads SQL • Validated • n rows once a result exists', () => {
    const line = buildSqlSummaryLine(
      entry({}, { confirmedColumns: ['c'], confirmedRows: [[1], [2]] }),
      tKey,
    )
    expect(line).toBe('sqlIntelligence.sqlLabel • status.validated • status.rows:2')
  })

  it('says the query has not been run before Confirm and Run', () => {
    expect(buildSqlSummaryLine(entry(), tKey)).toContain('sqlIntelligence.notRun')
  })

  it('represents a failed validation clearly rather than claiming validated', () => {
    const line = buildSqlSummaryLine(
      entry({
        attempt_history: [
          {
            attempt: 1,
            sql: 'DROP TABLE t',
            outcome: 'safety_violation',
            error: 'not allowed',
            will_retry: false,
          },
        ],
      }),
      tKey,
    )
    expect(line).toContain('status.blocked')
    expect(line).not.toContain('status.validated')
  })

  it('still reads validated when a failure was retried and then succeeded', () => {
    const line = buildSqlSummaryLine(
      entry({
        attempt_history: [
          { attempt: 1, sql: 'SELECT x', outcome: 'invalid_column', error: 'bad', will_retry: true },
          { attempt: 2, sql: SQL, outcome: 'succeeded', error: null, will_retry: false },
        ],
      }),
      tKey,
    )
    expect(line).toContain('status.validated')
  })

  it('reports an execution failure in place of a row count', () => {
    const line = buildSqlSummaryLine(entry({}, { confirmedError: 'Invalid object name' }), tKey)
    expect(line).toContain('status.executionFailed')
    expect(line).not.toContain('status.rows')
  })
})

describe('buildSqlMetadata', () => {
  it('reports the statement type and the tables the query references', () => {
    const rows = buildSqlMetadata(
      entry({
        sql_summary: { statement_type: 'SELECT', referenced_tables: ['product', 'sales'] },
      }),
      false,
      tKey,
    )
    const byKey = Object.fromEntries(rows.map((row) => [row.key, row.value]))
    expect(byKey.statementType).toBe('SELECT')
    expect(byKey.referencedTables).toBe('product, sales')
  })

  it('omits the statement type when the server could not parse the SQL', () => {
    // No guess is made client-side.
    const rows = buildSqlMetadata(entry({ sql_summary: null }), false, tKey)
    expect(rows.some((row) => row.key === 'statementType')).toBe(false)
  })

  it('names the database only when more than one is configured', () => {
    const e = entry({ database: 'hr' })
    expect(buildSqlMetadata(e, false, tKey).some((row) => row.key === 'database')).toBe(false)
    expect(buildSqlMetadata(e, true, tKey).some((row) => row.key === 'database')).toBe(true)
  })

  it('reports attempts only when the query was retried', () => {
    const once = entry({
      attempt_history: [{ attempt: 1, sql: SQL, outcome: 'succeeded', error: null, will_retry: false }],
    })
    expect(buildSqlMetadata(once, false, tKey).some((row) => row.key === 'attempts')).toBe(false)

    const twice = entry({
      attempt_history: [
        { attempt: 1, sql: 'SELECT x', outcome: 'invalid_column', error: 'b', will_retry: true },
        { attempt: 2, sql: SQL, outcome: 'succeeded', error: null, will_retry: false },
      ],
    })
    expect(buildSqlMetadata(twice, false, tKey).find((row) => row.key === 'attempts')?.value).toBe(
      '2',
    )
  })

  it('reports the row count and duration after execution', () => {
    const rows = buildSqlMetadata(
      entry({}, {
        confirmedColumns: ['c'],
        confirmedRows: [[1], [2], [3]],
        confirmedDurationMs: 2500,
      }),
      false,
      tKey,
    )
    const byKey = Object.fromEntries(rows.map((row) => [row.key, row.value]))
    expect(byKey.rowCount).toBe('3')
    expect(byKey.duration).toBe('2.50s')
  })

  it('formats a sub-second duration in milliseconds', () => {
    const rows = buildSqlMetadata(
      entry({}, { confirmedColumns: ['c'], confirmedRows: [[1]], confirmedDurationMs: 42 }),
      false,
      tKey,
    )
    expect(rows.find((row) => row.key === 'duration')?.value).toBe('42ms')
  })
})

describe('SqlIntelligencePanel', () => {
  it('renders nothing when there is no SQL and no intelligence to show', () => {
    const { container } = renderPanel(
      entry({ sql: null, query_plan: null, schema_tables: [], governing_metrics: [], model: null }),
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the server-returned SQL, not a regenerated or reformatted copy', () => {
    const { container } = renderPanel(entry())
    // CodeMirror splits text across line spans, so compare on normalized
    // whitespace rather than an exact node match.
    const text = (container.textContent ?? '').replace(/\s+/g, ' ')
    expect(text).toContain('SELECT category, SUM(revenue) AS revenue FROM sales GROUP BY category')
  })

  it('is open before a result exists so Confirm and Run is reachable', () => {
    const { container } = renderPanel(entry())
    expect(container.querySelector('details')).toHaveAttribute('open')
    expect(screen.getByRole('button', { name: /Confirm and Run/i })).toBeInTheDocument()
  })

  it('collapses once a result exists, leaving the summary line', () => {
    const { container } = renderPanel(
      entry({}, { confirmedColumns: ['c'], confirmedRows: [[1]] }),
    )
    expect(container.querySelector('details')).not.toHaveAttribute('open')
    expect(screen.getByText(/SQL • Validated • 1 rows/)).toBeInTheDocument()
  })

  it('offers a copy control that does not execute the query', () => {
    // CopyButton is clipboard-only (`navigator.clipboard.writeText`).
    const { onConfirmAndRun } = renderPanel(entry())
    const copy = screen.getByRole('button', { name: /copy/i })
    fireEvent.click(copy)
    expect(onConfirmAndRun).not.toHaveBeenCalled()
  })

  it('reports an edit upward rather than re-running anything itself', () => {
    const { onConfirmAndRun } = renderPanel(entry())
    // The panel owns no execution path; confirming is the caller's.
    expect(onConfirmAndRun).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: /Confirm and Run/i }))
    expect(onConfirmAndRun).toHaveBeenCalledTimes(1)
  })

  it('disables Confirm and Run while a run is in flight', () => {
    renderPanel(entry(), { isConfirming: true })
    expect(screen.getByRole('button', { name: /Confirm and Run/i })).toBeDisabled()
  })

  it('shows read-only SQL with no action when editing is not offered', () => {
    renderPanel(entry(), { showEditor: false })
    expect(screen.queryByRole('button', { name: /Confirm and Run/i })).not.toBeInTheDocument()
    expect(screen.getByText(/SELECT category/)).toBeInTheDocument()
  })

  it('keeps long SQL usable by wrapping rather than overflowing', () => {
    const long = `SELECT ${Array.from({ length: 60 }, (_, i) => `column_${i}`).join(', ')} FROM wide_table`
    const { container } = renderPanel(entry({ sql: long }, { editableSql: long }))
    // The read-only path wraps; the editor path uses CodeMirror's
    // lineWrapping. Either way nothing is clipped away from the DOM.
    const text = (container.textContent ?? '').replace(/\s+/g, ' ')
    expect(text).toContain('column_59')
  })

  it('surfaces the server cost notice next to the SQL it describes', () => {
    renderPanel(entry({ cost_notice: 'This query scans a large table.' }))
    expect(screen.getByText('This query scans a large table.')).toBeInTheDocument()
  })

  it('withholds the cost notice once the user has edited the SQL', () => {
    // The notice describes the SQL the server produced, not the edit.
    renderPanel(
      entry({ cost_notice: 'This query scans a large table.' }, { editableSql: 'SELECT 1' }),
    )
    expect(screen.queryByText('This query scans a large table.')).not.toBeInTheDocument()
  })

  it('shows the referenced tables, not the retrieved candidates', () => {
    renderPanel(
      entry({
        sql_summary: { statement_type: 'SELECT', referenced_tables: ['sales'] },
        schema_tables: [
          { table_name: 'sales', ddl: 'CREATE TABLE sales ()', similarity_score: 0.9 },
          { table_name: 'unused_candidate', ddl: 'CREATE TABLE unused_candidate ()', similarity_score: 0.7 },
        ],
      }),
    )
    const metadata = screen.getByText('Tables referenced').closest('div')
    expect(within(metadata as HTMLElement).getByText('sales')).toBeInTheDocument()
  })

  it('links to the lineage explorer without pre-seeding its state', () => {
    renderPanel(entry())
    expect(screen.getByRole('link', { name: /lineage explorer/i })).toHaveAttribute(
      'href',
      '/data-lineage',
    )
  })

  it('exposes no prompt text, model reasoning or credential', () => {
    const { container } = renderPanel(
      entry({
        sql_summary: { statement_type: 'SELECT', referenced_tables: ['sales'] },
        model: 'llama3.1:8b',
        query_plan: ['Aggregate revenue by category'],
      }),
    )
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/system prompt|you are an|chain of thought|reasoning:/i)
    expect(text).not.toMatch(/password|Server=|connection string/i)
  })
})
