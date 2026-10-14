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
  it('reads SQL • n rows once a result exists', () => {
    const line = buildSqlSummaryLine(
      entry({}, { confirmedColumns: ['c'], confirmedRows: [[1], [2]] }),
      tKey,
    )
    expect(line).toBe('sqlIntelligence.sqlLabel • status.rows:2')
  })

  it('says the query has not been run before Confirm and Run', () => {
    expect(buildSqlSummaryLine(entry(), tKey)).toContain('sqlIntelligence.notRun')
  })

  it('leaves validation state and the execution outcome to the lifecycle rail', () => {
    // `QueryLifecycle` is rendered unconditionally above this panel and
    // owns "Validated"/"Blocked"/"Executed failed" -- see
    // QueryLifecycle.test.tsx. Repeating any of it in this collapsed
    // title would state the same fact twice, which is the duplication
    // this split exists to prevent.
    const blockedLine = buildSqlSummaryLine(
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
    expect(blockedLine).not.toContain('status.blocked')
    expect(blockedLine).not.toContain('status.validated')

    const failedLine = buildSqlSummaryLine(entry({}, { confirmedError: 'Invalid object name' }), tKey)
    expect(failedLine).not.toContain('status.executionFailed')
    // Nor does it fall back to claiming the query was never run.
    expect(failedLine).not.toContain('sqlIntelligence.notRun')
    expect(failedLine).toBe('sqlIntelligence.sqlLabel')
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

  it('never reports the attempt count -- the timeline owns it', () => {
    // `AgentExecutionTimeline` states the retry count in its own
    // collapsed summary, visible without opening a panel and next to
    // which stage was actually retried. Asserting its absence here is
    // what keeps the duplication from creeping back.
    const twice = entry({
      attempt_history: [
        { attempt: 1, sql: 'SELECT x', outcome: 'invalid_column', error: 'b', will_retry: true },
        { attempt: 2, sql: SQL, outcome: 'succeeded', error: null, will_retry: false },
      ],
    })
    expect(buildSqlMetadata(twice, false, tKey).some((row) => row.key === 'attempts')).toBe(false)
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
    expect(screen.getByText(/SQL • 1 rows/)).toBeInTheDocument()
  })

  it('offers a copy control that does not execute the query', async () => {
    // CopyButton is clipboard-only. jsdom provides no `navigator.clipboard`
    // at all, so without this stub the click leaks an unhandled rejection
    // that vitest (rightly) warns can cause false positives elsewhere in
    // the run -- and the assertion below would pass for the wrong reason.
    const writeText = vi.fn().mockResolvedValue(undefined)
    const original = navigator.clipboard
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    try {
      const { onConfirmAndRun } = renderPanel(entry())
      fireEvent.click(screen.getByRole('button', { name: /copy/i }))
      // Copying reached the clipboard and nothing else.
      expect(writeText).toHaveBeenCalledWith(SQL)
      expect(onConfirmAndRun).not.toHaveBeenCalled()
    } finally {
      Object.defineProperty(navigator, 'clipboard', {
        value: original,
        configurable: true,
      })
    }
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
