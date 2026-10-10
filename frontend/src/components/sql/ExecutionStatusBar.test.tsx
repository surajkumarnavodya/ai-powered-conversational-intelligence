import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { newHistoryEntry, type QueryHistoryEntry } from '@/lib/history'
import type { AskResponse } from '@/lib/types'
import { askState } from '@/test/analyticsFixtures'
import { ExecutionStatusBar, deriveStatusItems } from './ExecutionStatusBar'

/** i18next is configured in the test setup, so `t` returns the real
 * English strings; this stand-in keeps the unit tests independent of that
 * and asserts on the key, which is what matters here. */
const tKey = (key: string, options?: Record<string, unknown>) =>
  options && 'count' in options ? `${key}:${options.count}` : key

function entry(
  state: Partial<AskResponse> = {},
  overrides: Partial<QueryHistoryEntry> = {},
): QueryHistoryEntry {
  const base = newHistoryEntry('How many rows?', askState({ sql: 'SELECT 1', ...state }), 1200)
  return { ...base, ...overrides }
}

function labels(items: ReturnType<typeof deriveStatusItems>) {
  return items.map((item) => item.label)
}

describe('deriveStatusItems -- result-trust flags only', () => {
  it('reports nothing for an ordinary, un-run query', () => {
    // Validation state and "not run" belong to SqlIntelligencePanel's
    // collapsed title; this bar is only for flags about whether a
    // *result* can be relied on.
    expect(deriveStatusItems(entry(), tKey)).toEqual([])
  })

  it('reports nothing for a clean, fresh, untruncated result', () => {
    const items = deriveStatusItems(
      entry({}, { confirmedColumns: ['c'], confirmedRows: [[1], [2]] }),
      tKey,
    )
    expect(items).toEqual([])
  })

  it('leaves the execution outcome to the lifecycle rail', () => {
    // `QueryLifecycle`'s `executed` stage owns whether the confirmed run
    // succeeded, and is equally always visible -- see
    // QueryLifecycle.test.tsx's own "reports a confirmed run that failed"
    // case. Reporting it here too would state the same fact twice.
    const items = deriveStatusItems(entry({}, { confirmedError: 'Invalid object name' }), tKey)
    expect(items).toEqual([])
  })

  it('flags a truncated result', () => {
    const items = deriveStatusItems(
      entry({}, { confirmedColumns: ['c'], confirmedRows: [[1]], confirmedTruncated: true }),
      tKey,
    )
    expect(labels(items)).toContain('status.truncated')
  })

  it('distinguishes a cached result from a fresh one', () => {
    const cached = deriveStatusItems(
      entry({}, { confirmedColumns: ['c'], confirmedRows: [[1]], confirmedCacheStatus: 'hit' }),
      tKey,
    )
    expect(labels(cached)).toContain('status.cacheHit')

    const fresh = deriveStatusItems(
      entry({}, { confirmedColumns: ['c'], confirmedRows: [[1]], confirmedCacheStatus: 'miss' }),
      tKey,
    )
    expect(labels(fresh)).toContain('status.cacheMiss')
  })

  it('surfaces the server governance notice', () => {
    const items = deriveStatusItems(
      entry({ restricted_field_notice: 'Some values were masked.' }),
      tKey,
    )
    const governed = items.find((item) => item.key === 'governed')
    expect(governed?.label).toBe('status.governed')
    expect(governed?.detail).toBe('Some values were masked.')
  })

  it('does not repeat what the SQL panel already states', () => {
    // Both surfaces are visible at once, so stating the same fact twice
    // would be noise. Validation state, row count, duration, attempts and
    // the cost flag all live in SqlIntelligencePanel -- its own test file
    // asserts them there. This pins that they have not crept back.
    const items = deriveStatusItems(
      entry(
        {
          cost_notice: 'This query scans a large table.',
          attempt_history: [
            { attempt: 1, sql: 'SELECT x', outcome: 'invalid_column', error: 'b', will_retry: true },
            { attempt: 2, sql: 'SELECT 1', outcome: 'succeeded', error: null, will_retry: false },
          ],
        },
        {
          confirmedColumns: ['c'],
          confirmedRows: [[1], [2], [3]],
          confirmedDurationMs: 2500,
        },
      ),
      tKey,
    )
    const shown = labels(items)
    expect(shown).not.toContain('status.executionFailed')
    expect(shown).not.toContain('status.validated')
    expect(shown).not.toContain('status.blocked')
    expect(shown).not.toContain('status.costFlagged')
    expect(shown.some((label) => label.startsWith('status.rows'))).toBe(false)
    expect(shown.some((label) => label.startsWith('status.attempts'))).toBe(false)
    expect(shown).not.toContain('2.50s')
  })
})

describe('ExecutionStatusBar', () => {
  it('renders nothing when there is no trust flag to report', () => {
    const { container } = render(<ExecutionStatusBar entry={entry()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders each flag as a list item under a labelled list', () => {
    render(
      <ExecutionStatusBar
        entry={entry({ restricted_field_notice: 'Some values were masked.' }, {
          confirmedColumns: ['c'],
          confirmedRows: [[1]],
          confirmedTruncated: true,
        })}
      />,
    )
    const list = screen.getByRole('list', { name: 'Query status' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(2)
    expect(within(list).getByText('Truncated')).toBeInTheDocument()
    expect(within(list).getByText('Governed')).toBeInTheDocument()
  })

  it('keeps the icons out of the accessibility tree', () => {
    // The label beside each icon carries the meaning.
    const { container } = render(
      <ExecutionStatusBar
        entry={entry({}, { confirmedColumns: ['c'], confirmedRows: [[1]], confirmedTruncated: true })}
      />,
    )
    expect(container.querySelectorAll('svg[aria-hidden="true"]').length).toBeGreaterThan(0)
  })

  it('never shows a credential or connection detail', () => {
    const { container } = render(
      <ExecutionStatusBar
        entry={entry({ restricted_field_notice: 'Some values were masked.' }, {
          confirmedColumns: ['c'],
          confirmedRows: [[1]],
          confirmedTruncated: true,
        })}
      />,
    )
    expect(container.textContent ?? '').not.toMatch(/password|Server=|connection string/i)
  })
})
